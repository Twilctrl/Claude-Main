"""Assemble the files to copy onto the PicoCalc's SD card.

Run from projects/picolm:
    python tools/build_sd.py                       # int8 model, engine compiled to .mpy if mpy-cross is found
    python tools/build_sd.py --model runs/s-int4/model.plm --no-mpy

Produces (under --out, default build/sd):
    picoware/apps/PicoLM.py                  the app (device/main.py)
    picoware/apps/PicoLM-Test.py             the on-device self-test (device/selftest_app.py)
    picoware/apps/picolm_lib/*.mpy or *.py   engine, tokenizer, hal, selftest, ui
    picoware/apps/picolm_lib/model.plm       the model
    picoware/apps/picolm_lib/model.golden.json

Copy the "picoware" folder onto the root of the SD card, merging with the one
already there. .mpy files are compiled with `mpy-cross -march=armv6m` (RP2040);
they load faster and avoid compiling on the device, which needs extra RAM.
`pip install mpy-cross` provides the compiler; its version has to produce the
.mpy format your Picoware firmware accepts (see docs/FLASHING.md).
"""

import argparse
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB_MODULES = ("__init__", "engine", "hal", "selftest", "tokenizer", "ui")


def find_mpy_cross(explicit):
    if explicit:
        return [explicit]
    exe = shutil.which("mpy-cross")
    if exe:
        return [exe]
    try:
        import mpy_cross  # noqa: F401  (pip install mpy-cross)
        return [sys.executable, "-m", "mpy_cross"]
    except ImportError:
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default=os.path.join(ROOT, "models", "picolm-s-int8.plm"))
    ap.add_argument("--out", default=os.path.join(ROOT, "build", "sd"))
    ap.add_argument("--no-mpy", action="store_true", help="copy .py sources instead of compiling")
    ap.add_argument("--mpy-cross", default=None, help="path to mpy-cross")
    args = ap.parse_args()

    apps = os.path.join(args.out, "picoware", "apps")
    lib = os.path.join(apps, "picolm_lib")
    if os.path.isdir(lib):
        shutil.rmtree(lib)
    os.makedirs(lib)
    shutil.copyfile(os.path.join(ROOT, "device", "main.py"), os.path.join(apps, "PicoLM.py"))
    shutil.copyfile(os.path.join(ROOT, "device", "selftest_app.py"), os.path.join(apps, "PicoLM-Test.py"))

    mpy = None if args.no_mpy else find_mpy_cross(args.mpy_cross)
    if mpy is None and not args.no_mpy:
        print("mpy-cross not found: copying .py sources (pip install mpy-cross to compile)")
    src_dir = os.path.join(ROOT, "device", "picolm_lib")
    for name in LIB_MODULES:
        src = os.path.join(src_dir, name + ".py")
        if mpy is None:
            shutil.copyfile(src, os.path.join(lib, name + ".py"))
        else:
            subprocess.run(mpy + ["-march=armv6m", "-o", os.path.join(lib, name + ".mpy"), src], check=True)

    shutil.copyfile(args.model, os.path.join(lib, "model.plm"))
    golden = os.path.splitext(args.model)[0] + ".golden.json"
    if os.path.exists(golden):
        shutil.copyfile(golden, os.path.join(lib, "model.golden.json"))
    else:
        print("no golden file for this model (python tools/golden.py --plm ...): the self-test will skip it")

    total = 0
    for dirpath, _, files in os.walk(os.path.join(args.out, "picoware")):
        for fn in sorted(files):
            p = os.path.join(dirpath, fn)
            total += os.path.getsize(p)
            print(f"  {os.path.relpath(p, args.out):45s} {os.path.getsize(p):>8,} B")
    print(f"{total:,} B in {args.out}: copy its 'picoware' folder onto the SD card root")


if __name__ == "__main__":
    main()
