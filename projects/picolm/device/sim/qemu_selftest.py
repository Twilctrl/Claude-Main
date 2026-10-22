"""Run the device self-test on an emulated ARM MCU (QEMU) from a ROMFS image of the SD build.

Linux / WSL only. Needs a MicroPython checkout with:
  - mpy-cross built                (make -C mpy-cross)
  - the QEMU port built as below, which makes the native code generator emit
    ARMv6-M code like the RP2040's and limits the heap to ~100 KB:
        make -C ports/qemu BOARD=MPS2_AN385 BUILD=build-armv6m \\
             MICROPY_HEAP_SIZE=102400 CFLAGS_EXTRA=-DMICROPY_EMIT_THUMB_ARMV7M=0
  - qemu-system-arm and pyserial installed

From projects/picolm:
    python device/sim/qemu_selftest.py --micropython ~/micropython [--model models/picolm-s-int8.plm]

QEMU checks correctness, memory and allocations on real Thumb code; its speed
says nothing about the PicoCalc.
"""

import argparse
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SCRIPT = """
import gc, sys
gc.collect()
print("heap free before import:", gc.mem_free())
sys.path.append("/rom")
from picolm_lib import selftest
gc.collect()
print("heap free after importing the engine:", gc.mem_free())
r = selftest.run("/rom/picolm_lib/model.plm", "sd", bench_tokens=16)
print("RESULT", r)
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--micropython", required=True, help="MicroPython checkout")
    ap.add_argument("--model", default=os.path.join(ROOT, "models", "picolm-s-int8.plm"))
    args = ap.parse_args()
    mp = os.path.abspath(os.path.expanduser(args.micropython))
    firmware = os.path.join(mp, "ports", "qemu", "build-armv6m", "firmware.elf")

    with tempfile.TemporaryDirectory() as tmp:
        sd = os.path.join(tmp, "sd")
        subprocess.run([sys.executable, os.path.join(ROOT, "tools", "build_sd.py"), "--model", args.model,
                        "--out", sd, "--mpy-cross", os.path.join(mp, "mpy-cross", "build", "mpy-cross")],
                       check=True, stdout=subprocess.DEVNULL)
        sys.path.insert(0, os.path.join(mp, "tools", "mpremote"))
        from mpremote.romfs import make_romfs

        img = os.path.join(tmp, "romfs.img")
        with open(img, "wb") as f:
            f.write(make_romfs(os.path.join(sd, "picoware", "apps"), mpy_cross=False))
        script = os.path.join(tmp, "selftest.py")
        with open(script, "w") as f:
            f.write(SCRIPT)
        qemu = ("qemu-system-arm -machine mps2-an385 -nographic -monitor null -semihosting -serial pty "
                f"-kernel {firmware} -device loader,file={img},addr=0x21000000,force-raw=on")
        out = subprocess.run([sys.executable, os.path.join(mp, "tools", "pyboard.py"), "--device",
                              "execpty:" + qemu, script], capture_output=True, text=True, timeout=1800)
    print(out.stdout + out.stderr)
    ok = "'golden': '3/3'" in out.stdout and "'alloc_per_token': 0" in out.stdout
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
