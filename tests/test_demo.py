"""Checks of the demo, run inside every image: `python -m unittest discover -s tests -t .` (from /srv).

- demo_app.py: each mode runs and reports its own time accounting; the shares match the design
  (tokenize the largest CPU leaf, wrappers handle_batch/process_batch without own accounting, stats rare).
- loadgen.py: open loop against a local stdlib server: slot count, CSV and raw files.
- server.py (only where FastAPI is installed): /batch, /healthz, /report through a real uvicorn process.
"""
import contextlib
import csv
import importlib.util
import io
import json
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent.parent
APP = ROOT / "app" / "demo_app.py"


def run_demo(mode, seconds):
    with tempfile.TemporaryDirectory() as td:
        rep = pathlib.Path(td) / "r.json"
        p = subprocess.run([sys.executable, str(APP), mode, str(seconds)], capture_output=True, text=True,
                           env=dict(os.environ, DEMO_REPORT=str(rep)), timeout=60)
        assert p.returncode == 0, p.stderr
        return json.loads(rep.read_text())


class DemoApp(unittest.TestCase):
    def test_plain_shares(self):
        r = run_demo("plain", 2.0)
        s = r["self_pct"]
        self.assertEqual(max((k for k in s if k != "load_batch"), key=s.get), "tokenize")
        self.assertNotIn("handle_batch", s)
        self.assertNotIn("process_batch", s)
        self.assertLess(s["stats"], 2.0)
        self.assertGreater(s["load_batch"], 20.0)      # about 25 of 77 ms per batch is waiting
        self.assertGreater(r["wall_s"], 1.9)

    def test_threads_mode(self):
        r = run_demo("threads", 1.5)
        self.assertIn("crunch", r["self_s"])
        self.assertIn("tokenize", r["self_s"])

    def test_recurse_mode(self):
        r = run_demo("recurse", 1.0)
        self.assertEqual(r["self_s"], {})

    def test_unknown_mode(self):
        p = subprocess.run([sys.executable, str(APP), "bogus", "1"], capture_output=True, text=True, timeout=30)
        self.assertNotEqual(p.returncode, 0)


class _Ok(BaseHTTPRequestHandler):
    def do_GET(self):
        time.sleep(0.01)
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *a):
        pass


class Loadgen(unittest.TestCase):
    def test_open_loop(self):
        sys.path.insert(0, str(ROOT / "loadgen"))
        import loadgen
        srv = ThreadingHTTPServer(("127.0.0.1", 0), _Ok)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as td:
                raw, agg = f"{td}/raw.csv", f"{td}/agg.csv"
                buf = io.StringIO()
                lo = time.monotonic_ns()
                with contextlib.redirect_stdout(buf):
                    s = loadgen.main(["--url", f"http://127.0.0.1:{srv.server_port}/", "--rps", "20",
                                      "--duration", "2", "--interval", "1", "--raw", raw, "--csv", agg])
                hi = time.monotonic_ns()
                self.assertEqual(s["slots"], 40)
                self.assertEqual(s["ok"], 40)
                rows = list(csv.reader(open(raw)))
                self.assertEqual(len(rows), 41)
                self.assertEqual(rows[0], loadgen.RAW_HEADER)
                self.assertEqual(len(list(csv.reader(open(agg)))), 3)
                # clock anchor line: CLOCK_MONOTONIC in ns, read between our own two readings
                line = next(x for x in buf.getvalue().splitlines() if x.startswith("loadgen: clock "))
                t0 = int(line.split("t0_mono_ns=")[1].split()[0])
                self.assertTrue(lo <= t0 <= hi, (lo, t0, hi))
        finally:
            srv.shutdown()

    def test_rejects_bad_rate(self):
        sys.path.insert(0, str(ROOT / "loadgen"))
        import loadgen
        with self.assertRaises(SystemExit):
            loadgen.main(["--rps", "0"])


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@unittest.skipIf(importlib.util.find_spec("fastapi") is None, "no FastAPI on this stack (script modes only)")
class Server(unittest.TestCase):
    def test_endpoints(self):
        port = _free_port()
        p = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.server:app", "--port", str(port),
                              "--no-access-log"], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            base = f"http://127.0.0.1:{port}"
            for _ in range(100):
                try:
                    h = json.load(urllib.request.urlopen(base + "/healthz", timeout=1))
                    break
                except OSError:
                    time.sleep(0.1)
            else:
                self.fail("server did not start")
            self.assertEqual(h["app"], "c1-demo")
            self.assertEqual(set(h["reps"]), {"validate", "tokenize", "render", "stats"})
            t0 = time.perf_counter()
            b = json.load(urllib.request.urlopen(base + "/batch", timeout=5))
            self.assertGreater(b["n"], 0)
            self.assertGreater(time.perf_counter() - t0, 0.05)   # 25 ms wait + about 52 ms CPU
            r = json.load(urllib.request.urlopen(base + "/report", timeout=5))
            self.assertIn("tokenize", r["self_s"])
        finally:
            p.terminate()
            p.wait(10)


if __name__ == "__main__":
    unittest.main()
