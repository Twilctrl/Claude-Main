"""Self-test and benchmark: replay the golden cases, then measure speed and memory.

On the PicoCalc, from Picoware's Python REPL (Library > Utilities > Python REPL) or Thonny:

    import sys; sys.path.append("/sd/picoware/apps")
    from picolm_lib import selftest; selftest.run()

On a PC: python device/sim/run_selftest.py (see docs/FLASHING.md).

Prints whether the device reproduces the PC's integer reference token for
token, the load time, ms per token split into weight reads and compute, heap
use, bytes allocated per generated token (should be 0), and the raw PSRAM and
SD read speeds that docs/budget.md had to estimate.
"""

import gc
import json

from . import hal
from .engine import Engine


def golden_path(model_path):
    return model_path[:-4] + ".golden.json" if model_path.endswith(".plm") else model_path + ".golden.json"


def _replay(eng, case):
    """Generate as tools/intref.py does; returns (index of first mismatch or -1, tokens, ms)."""
    eng.seed(case["seed"])
    eng.prime(case["prompt"])
    ids = case["ids"]
    t0 = hal.ticks_ms()
    for i in range(len(ids)):
        nxt = eng.sample(case["temp_q8"], case["top_k"])
        if nxt != ids[i]:
            return i, i, hal.ticks_diff(hal.ticks_ms(), t0)
        eng.step(nxt)
    if len(ids) < case["n"] and eng.sample(case["temp_q8"], case["top_k"]) != eng.eot:
        return len(ids), len(ids), hal.ticks_diff(hal.ticks_ms(), t0)
    return -1, len(ids), hal.ticks_diff(hal.ticks_ms(), t0)


def _throughput(read, buf, reps):
    t0 = hal.ticks_us()
    for _ in range(reps):
        read(buf)
    dt = hal.ticks_diff(hal.ticks_us(), t0)
    return len(buf) * reps / max(dt, 1)  # bytes per microsecond = MB/s


def run(model_path=None, mode="auto", log=print, bench_tokens=32, max_cases=None):
    model_path = model_path or hal.default_model_path()
    gc.collect()
    free0 = hal.mem_free()
    log("PicoLM self-test: " + model_path)
    log("free heap before load: %d B" % free0)
    t0 = hal.ticks_ms()
    eng = Engine(model_path, mode, log=log)
    load_ms = hal.ticks_diff(hal.ticks_ms(), t0)
    gc.collect()
    free1 = hal.mem_free()
    log("loaded in %d ms, weights in %s, engine uses %d B, %d B heap left" %
        (load_ms, eng.mode, free0 - free1, free1))
    result = {"mode": eng.mode, "load_ms": load_ms, "heap_used": free0 - free1, "heap_left": free1}

    try:
        with open(golden_path(model_path)) as f:
            cases = json.load(f)["cases"]
    except OSError:
        cases = []
        log("no golden file next to the model: skipping the exactness check")
    passed = 0
    for i, case in enumerate(cases[:max_cases] if max_cases else cases):
        bad, n, ms = _replay(eng, case)
        rate = 1000 * n / ms if ms else 0
        if bad < 0:
            passed += 1
            log("golden case %d: MATCH, %d tokens (%.1f tok/s incl. sampling)" % (i, n, rate))
        else:
            log("golden case %d: MISMATCH at token %d" % (i, bad))
    result["golden"] = "%d/%d" % (passed, len(cases[:max_cases] if max_cases else cases))

    eng.seed(1)
    eng.prime("Once upon a time")
    tok = eng.sample(205, 20)
    eng.profile = True
    eng.io_us = 0
    t_step = 0
    t0 = hal.ticks_us()
    for _ in range(bench_tokens):
        t1 = hal.ticks_us()
        eng.step(tok)
        t_step += hal.ticks_diff(hal.ticks_us(), t1)
        tok = eng.sample(205, 20)
    total = hal.ticks_diff(hal.ticks_us(), t0)
    eng.profile = False
    ms = total / bench_tokens / 1000
    io = eng.io_us / bench_tokens / 1000
    step = t_step / bench_tokens / 1000
    log("per token: %.1f ms (weight reads %.1f, compute %.1f, sampling %.1f) = %.1f tok/s" %
        (ms, io, step - io, ms - step, 1000 / ms))
    result.update({"ms_per_token": ms, "io_ms": io, "compute_ms": step - io, "sample_ms": ms - step})

    if hasattr(gc, "mem_alloc"):
        gc.collect()
        gc.disable()
        a0 = gc.mem_alloc()
        for _ in range(16):
            eng.step(tok)
            tok = eng.sample(205, 20)
        a1 = gc.mem_alloc()
        gc.enable()
        log("heap allocated per token: %d B" % ((a1 - a0) // 16))
        result["alloc_per_token"] = (a1 - a0) // 16

    mv = memoryview(eng.staging)
    if eng.mode == "PSRAM":
        mbs = _throughput(lambda b: eng.store.read(0, b), mv, 32)
        log("PSRAM read32_bulk: %.1f MB/s" % mbs)
        result["psram_mbs"] = mbs
    with open(model_path, "rb") as f:
        def sd_read(b):
            if not f.readinto(b):
                f.seek(0)
        mbs = _throughput(sd_read, mv, 16)
    log("model file read: %.2f MB/s" % mbs)
    result["file_mbs"] = mbs
    gc.collect()
    log("free heap at end: %d B" % hal.mem_free())
    eng.close()
    return result
