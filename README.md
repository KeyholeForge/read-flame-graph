# read-flame-graph

Demo code for the KeyholeForge video **"How to Read a Flame Graph"**. A small Python program with a known shape, so you can record a flame graph with [py-spy](https://github.com/benfred/py-spy) and check what you see against what the code does.

## What the program does

`app/demo_app.py` repeats one batch of work, using only the standard library:

```
main -> run_forever -> handle_batch
    -> load_batch      waits 25 ms (time.sleep): a stand-in for a database or network call
    -> process_batch   a wrapper that computes almost nothing itself
        -> validate    leaf
        -> parse       wrapper -> tokenize (the hottest leaf)
        -> render      leaf
        -> stats       a rare, tiny leaf
```

Modes: `plain` (one thread), `threads` (adds a thread that only waits and one that computes) and `recurse` (`fib` and `walk`). When it ends, the program prints how long it spent in each function, so you can compare that with the profile.

`app/server.py` runs the same work behind HTTP (FastAPI, `GET /batch`). `loadgen/loadgen.py` sends requests at a fixed rate.

## Run it

You need Docker. py-spy reads another process's memory, so the container needs `SYS_PTRACE`.

```sh
docker build -t read-flame-graph .
docker run -d --name rfg --cap-add SYS_PTRACE read-flame-graph sleep infinity

# start the program as the service user, then record 10 s with py-spy
docker exec -d -u app rfg python -m app.demo_app plain 60
docker exec -u dev rfg bash -c 'mkdir -p ~/out && sudo py-spy record -p "$(pgrep -u app -f demo_app)" -d 10 -o ~/out/profile.svg'
docker cp rfg:/home/dev/out/profile.svg .
```

Open `profile.svg` in a browser. Try it again with `--idle`, `--threads` or `--function`, or use `py-spy top` instead of `record`.

The web service:

```sh
docker run -d --name rfg-web --cap-add SYS_PTRACE read-flame-graph
docker exec rfg-web python loadgen/loadgen.py --url http://localhost:8000/batch --rps 8 --duration 30 &
docker exec -u dev rfg-web bash -c 'sudo py-spy record -p "$(pgrep -u app -f uvicorn)" -d 10 -o /tmp/server.svg'
```

To use Python 3.15 and its built-in sampling profiler (Tachyon, `python -m profiling.sampling`):

```sh
docker build -t read-flame-graph:315 --build-arg PY=3.15.2 --build-arg REQS=requirements-none.txt .
```

## Versions in the video

Python 3.14.10, py-spy 0.4.4, FastAPI 0.165.0 (all pins in `requirements-314.txt`). The Tachyon part uses Python 3.15.2. Your numbers will differ from the video, because the program calibrates its work to your CPU when it starts. The shape of the graph should stay the same.

## Tests

```sh
docker run --rm read-flame-graph python -m unittest discover -s tests -t .
```

## License

MIT, see `LICENSE`.
