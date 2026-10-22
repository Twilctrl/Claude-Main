"""Write golden generation cases for a .plm: the exact token ids the integer reference produces.

Run from projects/picolm:
    python tools/golden.py --plm models/picolm-s-int8.plm

Writes <plm minus .plm>.golden.json. The device self-test (picolm_lib/selftest.py)
and the PC simulation replay each case and must produce the same ids.
"""

import argparse
import json
import os
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.intref import IntModel, generate, tokenizer_from_params  # noqa: E402
from train import plm  # noqa: E402

CASES = [
    {"prompt": "Once upon a time", "temp_q8": 0, "top_k": 0, "seed": 1, "n": 120},
    {"prompt": "Once upon a time", "temp_q8": 205, "top_k": 20, "seed": 1234, "n": 200},
    {"prompt": "The little dog", "temp_q8": 256, "top_k": 0, "seed": 7, "n": 100},
]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plm", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    raw = open(args.plm, "rb").read()
    P = plm.read(raw)
    tok = tokenizer_from_params(P)
    cases = []
    for c in CASES:
        ids = generate(IntModel(P), tok, c["prompt"], c["n"], c["temp_q8"], c["top_k"], c["seed"])
        cases.append(dict(c, ids=ids, crc32=f"{zlib.crc32(bytes(ids)):08x}", text=tok.decode(ids)))
        print(f"{c['prompt']!r} temp_q8 {c['temp_q8']} top_k {c['top_k']} seed {c['seed']}: "
              f"{len(ids)} tokens, crc32 {cases[-1]['crc32']}")
    out = args.out or os.path.splitext(args.plm)[0] + ".golden.json"
    with open(out, "w") as f:
        json.dump({"model_crc32": f"{zlib.crc32(raw):08x}", "file_bytes": len(raw), "cases": cases}, f, indent=1)
    print("wrote", out)


if __name__ == "__main__":
    main()
