"""Simulated Picoware PSRAM: an 8 MB bytearray behind the same methods as picoware.system.psram.PSRAM.

Mirrors the real behaviour the app has to cope with (see device/picolm_lib/hal.py):
bulk reads and writes need 4-byte-aligned addresses, and the interface can be
shut down behind the app's back (simulate_deinit) or lose its contents on
re-init (simulate_wipe).
"""

import micropython

SIZE = 8 * 1024 * 1024
HEAP_START_ADDR = 320 * 320 + 1024

_mem = None
_ready = False


@micropython.viper
def _copy(dst: ptr32, doff: int, src: ptr32, soff: int, n: int):
    """Word copy without allocating, so the self-test's allocation count sees only the engine."""
    d = doff >> 2
    s = soff >> 2
    i = 0
    n4 = n >> 2
    while i < n4:
        dst[d + i] = src[s + i]
        i += 1


class PSRAM:
    def __init__(self):
        global _mem, _ready
        if _mem is None:
            _mem = bytearray(SIZE)
        _ready = True

    def size(self):
        return SIZE

    def is_ready(self):
        return _ready

    def get_next_free(self):
        return HEAP_START_ADDR

    def _check(self, addr, buf):
        if not _ready:
            raise RuntimeError("PSRAM not initialized. Call init() first.")
        if addr & 3:
            raise ValueError("Address must be 4-byte aligned for bulk32")
        n = len(buf) & ~3
        if addr + n > SIZE:
            raise ValueError("Read would exceed PSRAM size")
        return n

    def read32_bulk(self, addr, buf):
        n = self._check(addr, buf)
        _copy(buf, 0, _mem, addr, n)
        return n

    def write32_bulk(self, addr, buf):
        n = self._check(addr, buf)
        _copy(_mem, addr, buf, 0, n)
        return n


def simulate_deinit():
    global _ready
    _ready = False


def simulate_wipe():
    """Lose the top 1 MB, as a chip reset on re-init might."""
    if _mem is not None:
        _mem[SIZE - 1024 * 1024:] = bytes(1024 * 1024)
