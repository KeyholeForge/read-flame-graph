"""C1 demo as a small web service: the same work as `demo_app.py`, one batch per HTTP request.

GET /batch    sync `def` endpoint (FastAPI runs it in a worker thread from the AnyIO pool): one call of
              `demo_app.handle_batch()`, i.e. load_batch (25 ms wait) + process_batch (about 52 ms CPU).
GET /healthz  effective variant: Python, library versions, calibration (REPS), pid, server command line
              (so every run can store what was actually running).
GET /report   the app's own time accounting so far (perf_counter per function, `demo_app.ACC`).

Calibration (`demo_app.calibrate()`) runs once at import, before the first request.
"""
import os
import platform
from importlib import metadata

from fastapi import FastAPI

from app import demo_app as d

d.calibrate()
app = FastAPI()


def _version(dist):
    try:
        return metadata.version(dist)
    except metadata.PackageNotFoundError:
        return None


def _cmdline(pid):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return f.read().replace(b"\0", b" ").decode().strip()
    except OSError:
        return ""


VARIANT = {
    "app": "c1-demo",
    "python": platform.python_version(),
    "versions": {k: _version(k) for k in ("fastapi", "starlette", "anyio", "uvicorn", "pydantic", "py-spy")},
    "reps": dict(d.REPS),
    "target_ms": dict(d.TARGET_MS),
}


@app.get("/batch")
def batch():
    return {"n": d.handle_batch()}


@app.get("/healthz")
def healthz():
    return dict(VARIANT, pid=os.getpid(), cmdline=_cmdline(os.getpid()))


@app.get("/report")
def report():
    acc = dict(d.ACC)
    total = sum(acc.values()) or 1.0
    return {"self_s": {k: round(v, 3) for k, v in acc.items()},
            "self_pct": {k: round(100 * v / total, 1) for k, v in acc.items()}}
