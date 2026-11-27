#!/usr/bin/env python3
"""Deliberate workload for `py-spy record` (video "How to Read a Flame Graph").

Stack (from the top):
    main -> run_forever -> handle_batch
        -> load_batch      waiting for I/O (time.sleep), off the CPU
        -> process_batch   wide wrapper frame, computes almost nothing itself
            -> validate     leaf
            -> parse        wrapper -> tokenize (leaf, the hottest)
            -> render       leaf
            -> stats        rare leaf (~0.6% of the time)

Usage: python demo_app.py <mode> [seconds]
    plain    main thread only
    threads  plain + a waiting thread (uploader) + a computing thread (indexer)
    recurse  recursion: fib (two call lines) and walk (one line)

When it ends, it prints its own time accounting (perf_counter) as JSON
and writes it to the file named in DEMO_REPORT, if that variable is set.
"""
import collections
import json
import os
import sys
import threading
import time

WORDS = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu".split()
ACC = collections.Counter()   # own accounting: seconds spent in each function
REPS = {}                     # calibration: iterations per unit of work

# Target time of one unit of work (ms): I/O 25 ms, CPU 52 ms per item.
TARGET_MS = {"validate": 8.0, "tokenize": 30.0, "parse": 4.0,
             "render": 10.0, "stats": 0.5, "sleep": 25.0}


def validate(item, n):
    t0 = time.perf_counter()
    s = 0
    for i in range(n):
        s += len(item) ^ (i & 255)
    ACC["validate"] += time.perf_counter() - t0
    return s


def tokenize(item, n):
    t0 = time.perf_counter()
    out = []
    for _ in range(n):
        for w in item:
            out.append((w, sum(map(ord, w)) % 97))
    ACC["tokenize"] += time.perf_counter() - t0
    return out


def parse(item):
    """Wrapper: computes almost nothing itself, tokenize does the work."""
    t0 = time.perf_counter()
    before = ACC["tokenize"]
    out = tokenize(item, REPS["tokenize"])
    total = sum(v for _, v in out)
    # own time = time in parse minus time in tokenize
    ACC["parse"] += (time.perf_counter() - t0) - (ACC["tokenize"] - before)
    return total


def render(item, n):
    t0 = time.perf_counter()
    s = ""
    for i in range(n):
        s = f"{i % 10}:{len(item)}" + s[-16:]
    ACC["render"] += time.perf_counter() - t0
    return len(s)


def stats(item, n):
    """Rare leaf: ~0.6% of an item's time."""
    t0 = time.perf_counter()
    s = 0
    for i in range(n):
        s += (len(item) * i) % 13
    ACC["stats"] += time.perf_counter() - t0
    return s


def process_batch(items):
    acc = 0
    for item in items:
        acc += validate(item, REPS["validate"])
        acc += parse(item)
        acc += render(item, REPS["render"])
        acc += stats(item, REPS["stats"])
    return acc


def load_batch():
    t0 = time.perf_counter()
    time.sleep(TARGET_MS["sleep"] / 1000.0)   # pretend to wait for a database / network
    ACC["load_batch"] += time.perf_counter() - t0
    return WORDS


def handle_batch():
    """One batch = one wait for I/O and one item to process."""
    return process_batch([load_batch()])


def run_forever(seconds):
    end = time.perf_counter() + seconds
    n = 0
    while time.perf_counter() < end:
        n += handle_batch()
    return n


def crunch(item, n):
    """Work of the second thread (indexer): the same amount of CPU as a whole main-thread item."""
    t0 = time.perf_counter()
    s = 0
    for i in range(n):
        s += len(item) ^ (i & 255)
    ACC["crunch"] += time.perf_counter() - t0
    return s


_ready = threading.Event()


def uploader():
    """A thread that only waits: it does not show up in the default profile at all."""
    while True:
        _ready.wait(0.05)


def indexer(seconds):
    end = time.perf_counter() + seconds
    n = REPS["validate"] + REPS["tokenize"] // 2 + REPS["render"] + REPS["stats"]
    while time.perf_counter() < end:
        crunch(WORDS, n)


# --- recursion -------------------------------------------------------------

def fib(n):
    """Two recursive calls on two different lines."""
    if n < 2:
        return n
    a = fib(n - 1)   # line A
    b = fib(n - 2)   # line B
    return a + b


def walk(node, acc=0):
    """One recursive call, always from the same line."""
    if isinstance(node, list):
        for child in node:
            acc += walk(child)   # the same line
        return acc
    return acc + len(node)


def _calibrate(fn, name, target_ms, *args):
    n = 2000
    fn(*args, n)                       # warm-up, measure on the second lap
    t0 = time.perf_counter()
    fn(*args, n)
    dt = time.perf_counter() - t0
    REPS[name] = max(1, int(round(n * target_ms / 1000.0 / dt)))


def calibrate():
    _calibrate(validate, "validate", TARGET_MS["validate"], WORDS)
    _calibrate(tokenize, "tokenize", TARGET_MS["tokenize"], WORDS)
    _calibrate(render, "render", TARGET_MS["render"], WORDS)
    _calibrate(stats, "stats", TARGET_MS["stats"], WORDS)
    ACC.clear()


def report(wall):
    out = {
        "wall_s": round(wall, 3),
        "cpu_s": round(time.process_time(), 3),
        "reps": REPS,
        "self_s": {k: round(v, 3) for k, v in ACC.items()},
    }
    total = sum(ACC.values())
    out["self_pct"] = {k: round(100 * v / total, 1) for k, v in ACC.items()}
    line = "DEMO_REPORT " + json.dumps(out, sort_keys=True)
    print(line)
    path = os.environ.get("DEMO_REPORT")
    if path:
        with open(path, "w") as f:
            f.write(json.dumps(out, sort_keys=True, indent=1) + "\n")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "plain"
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 6.0
    t0 = time.perf_counter()
    if mode == "plain":
        calibrate()
        run_forever(seconds)
    elif mode == "threads":
        calibrate()
        threading.Thread(target=uploader, name="uploader", daemon=True).start()
        threading.Thread(target=indexer, args=(seconds,), name="indexer", daemon=True).start()
        run_forever(seconds)
    elif mode == "recurse":
        while time.perf_counter() - t0 < seconds:
            fib(24)
            walk(WORDS + [WORDS[:4], [WORDS[2:6]], WORDS[1:5], [[WORDS[3:7]]]])
    else:
        raise SystemExit(f"unknown mode: {mode}")
    report(time.perf_counter() - t0)


if __name__ == "__main__":
    main()
