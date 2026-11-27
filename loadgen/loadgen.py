#!/usr/bin/env python3
"""Open-loop load generator for the C1 demo (stdlib only).

Requests are scheduled at a fixed rate (slot k at t0 + k / rps), independent of responses; each slot runs in its
own thread, at most --max-inflight at once (a slot that finds the limit reached is counted as `dropped`).

Output:
  --raw  CSV, one row per slot: slot, sched_s, start_s, end_s, status, error   (seconds from t0, monotonic clock)
  --csv  CSV every --interval s: t_s, sent, ok, err, dropped, p50_ms, p95_ms, max_ms (requests finished in it)
stdout: one summary line `LOADGEN {...}` (JSON) at the end.
"""
import argparse
import csv
import json
import threading
import time
import urllib.request

# Columns of --raw.
RAW_HEADER = ["slot", "sched_s", "start_s", "end_s", "status", "error"]


def pct(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100.0 * (len(xs) - 1))))]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000/batch")
    ap.add_argument("--rps", type=float, default=8.0)
    ap.add_argument("--duration", type=float, default=30.0)
    ap.add_argument("--max-inflight", type=int, default=64)
    ap.add_argument("--timeout", type=float, default=10.0)
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--raw")
    ap.add_argument("--csv")
    a = ap.parse_args(argv)
    if a.rps <= 0 or a.duration <= 0:
        raise SystemExit("--rps and --duration must be > 0")

    rows, lock = [], threading.Lock()
    inflight = threading.BoundedSemaphore(a.max_inflight)
    t0_ns = time.monotonic_ns()
    t0 = t0_ns / 1e9
    # Anchor of the slot grid in CLOCK_MONOTONIC of the (VM) kernel: shared by all containers on one Docker Desktop VM,
    # so a profiler window read with time.monotonic_ns() in another container maps onto sched_s.
    print(f"loadgen: clock t0_mono_ns={t0_ns} rps={a.rps:g} slots={int(a.duration * a.rps)}", flush=True)
    n_slots = int(a.duration * a.rps)

    def fire(slot, sched):
        start = time.monotonic() - t0
        status, err = 0, ""
        try:
            with urllib.request.urlopen(a.url, timeout=a.timeout) as r:
                r.read()
                status = r.status
        except Exception as e:  # noqa: BLE001 (any failure is one error row)
            err = type(e).__name__
        finally:
            inflight.release()
        end = time.monotonic() - t0
        with lock:
            rows.append((slot, sched, start, end, status, err))

    threads = []
    for k in range(n_slots):
        sched = k / a.rps
        delay = t0 + sched - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        if not inflight.acquire(blocking=False):
            with lock:
                rows.append((k, sched, None, None, 0, "dropped"))
            continue
        th = threading.Thread(target=fire, args=(k, sched), daemon=True)
        th.start()
        threads.append(th)
    for th in threads:
        th.join(a.timeout + 1)

    rows.sort()
    if a.raw:
        with open(a.raw, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(RAW_HEADER)
            for r in rows:
                w.writerow([r[0], f"{r[1]:.4f}", "" if r[2] is None else f"{r[2]:.4f}",
                            "" if r[3] is None else f"{r[3]:.4f}", r[4], r[5]])
    ok = [r for r in rows if r[4] == 200]
    lat = [1000 * (r[3] - r[1]) for r in ok]   # from scheduled time (open loop)
    if a.csv:
        with open(a.csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["t_s", "sent", "ok", "err", "dropped", "p50_ms", "p95_ms", "max_ms"])
            nb = int(a.duration / a.interval + 0.999)
            for b in range(nb):
                lo, hi = b * a.interval, (b + 1) * a.interval
                inb = [r for r in rows if r[3] is not None and lo <= r[3] < hi]
                okb = [1000 * (r[3] - r[1]) for r in inb if r[4] == 200]
                sent = sum(1 for r in rows if lo <= r[1] < hi and r[5] != "dropped")
                drop = sum(1 for r in rows if lo <= r[1] < hi and r[5] == "dropped")
                fmt = lambda v: "" if v is None else f"{v:.1f}"
                w.writerow([f"{hi:g}", sent, len(okb), len(inb) - len(okb), drop,
                            fmt(pct(okb, 50)), fmt(pct(okb, 95)), fmt(max(okb) if okb else None)])
    p50, p95 = pct(lat, 50), pct(lat, 95)
    summary = {"slots": n_slots, "ok": len(ok), "err": sum(1 for r in rows if r[4] != 200 and r[5] != "dropped"),
               "dropped": sum(1 for r in rows if r[5] == "dropped"),
               "p50_ms": None if p50 is None else round(p50, 1), "p95_ms": None if p95 is None else round(p95, 1)}
    print("LOADGEN " + json.dumps(summary), flush=True)
    return summary


if __name__ == "__main__":
    main()
