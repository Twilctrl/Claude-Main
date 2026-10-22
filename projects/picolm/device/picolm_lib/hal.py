"""Thin adapter between the engine and the platform (Picoware, or the PC simulator).

Everything the engine needs from outside MicroPython goes through here:
weight storage (PSRAM, SD file or RAM), timers and memory info. The
Picoware APIs used were checked against the Picoware source (commit 566d2e5):

  picoware.system.psram.PSRAM: size(), is_ready(), get_next_free(),
      read32_bulk(addr, buf), write32_bulk(addr, buf)   (4-byte aligned, 64 B bursts)
  builtin open() on /sd/... once the app loader has mounted the SD VFS:
      readinto(), seek()

Caveats found in that source, handled below:
  - every PSRAM() call resets Picoware's PSRAM allocator, and a PSRAM object's
    finaliser shuts the PSRAM interface down. So the model lives in a fixed
    region at the top of PSRAM (not in the allocator), stale PSRAM objects
    are collected before ours is made, and the copy is re-checked (and
    reloaded if needed) before each generation.
  - re-initialising PSRAM sends the chip a reset command; whether the data
    survives that is only known on real hardware, hence the re-check.
"""

import gc
import time

try:
    from picoware.system.psram import PSRAM as _PSRAM
except ImportError:  # not on a PicoCalc (or Picoware without PSRAM support)
    _PSRAM = None

if hasattr(time, "ticks_us"):
    ticks_us = time.ticks_us
    ticks_ms = time.ticks_ms
    ticks_diff = time.ticks_diff
else:  # CPython simulator
    def ticks_us():
        return int(time.perf_counter() * 1000000)

    def ticks_ms():
        return int(time.perf_counter() * 1000)

    def ticks_diff(a, b):
        return a - b


def mem_free():
    """Free MicroPython heap in bytes, or -1 when unknown (CPython)."""
    f = getattr(gc, "mem_free", None)
    return f() if f else -1


def default_model_path():
    """model.plm next to this package (e.g. /sd/picoware/apps/picolm_lib/model.plm)."""
    here = __file__.replace("\\", "/")
    return here[:here.rfind("/") + 1] + "model.plm"


def psram_available():
    return _PSRAM is not None


class PsramStore:
    """The model file copied into a fixed region at the top of the 8 MB PSRAM."""

    kind = "PSRAM"
    resident = None

    def __init__(self, nbytes):
        gc.collect()  # run finalisers of stale PSRAM objects now, not mid-generation
        self.p = _PSRAM()
        self.nbytes = (nbytes + 63) & ~63
        self.base = (self.p.size() - self.nbytes) & ~63
        if self.p.get_next_free() > self.base:
            raise MemoryError("PSRAM region in use")

    def read(self, off, mv):
        self.p.read32_bulk(self.base + off, mv)

    def write(self, off, mv):
        self.p.write32_bulk(self.base + off, mv)

    def ready(self):
        return self.p.is_ready()

    def reinit(self):
        gc.collect()
        self.p = _PSRAM()

    def close(self):
        self.p = None


class FileStore:
    """Stream weights straight from the model file (SD card): slow but needs no PSRAM."""

    kind = "SD"
    resident = None

    def __init__(self, path):
        self.f = open(path, "rb")

    def read(self, off, mv):
        self.f.seek(off)
        self.f.readinto(mv)

    def ready(self):
        return True

    def close(self):
        self.f.close()


class RamStore:
    """Whole model in the MicroPython heap (small models, or the PC simulator)."""

    kind = "RAM"

    def __init__(self, path, nbytes):
        self.resident = bytearray(nbytes)
        with open(path, "rb") as f:
            f.readinto(self.resident)

    def read(self, off, mv):
        mv[:] = self.resident[off:off + len(mv)]

    def ready(self):
        return True

    def close(self):
        self.resident = None
