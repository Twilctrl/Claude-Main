"""Generate text from a .plm with the integer-only reference (the device's exact algorithm).

Run from projects/picolm:
    python tools/sample.py --plm models/picolm-s-int8.plm --prompt "Once upon a time" --temp 0.8 --top-k 20
    python tools/sample.py --plm models/picolm-s-int8.plm --temp 0          # greedy

Same prompt, settings and seed give the same text as the device engine.
"""

import argparse
import os
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.intref import IntModel, stream, tokenizer_from_params  # noqa: E402
from train import fixedpoint as fx  # noqa: E402
from train import plm  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plm", required=True)
    ap.add_argument("--prompt", default="Once upon a time")
    ap.add_argument("--n", type=int, default=400, help="max tokens to generate")
    ap.add_argument("--temp", type=float, default=0.8, help="0 = greedy")
    ap.add_argument("--top-k", type=int, default=20, help="0 = no top-k")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--no-stop", action="store_true", help="keep going past end-of-story")
    args = ap.parse_args()

    P = plm.read(args.plm)
    tok = tokenizer_from_params(P)
    temp_q8 = round(args.temp * fx.TEMP_ONE)
    out = []
    sys.stdout.write(args.prompt)
    for t in stream(IntModel(P, check=False), tok, args.prompt, args.n, temp_q8, args.top_k, args.seed,
                    stop_at_eot=not args.no_stop):
        out.append(t)
        sys.stdout.write("\n[EOT]\n" if t == P["header"]["eot_id"] else tok.decode([t]))
        sys.stdout.flush()
    print(f"\n-- {len(out)} tokens, temp_q8 {temp_q8}, top_k {args.top_k}, seed {args.seed}, "
          f"crc32 {zlib.crc32(bytes(out)):08x}")


if __name__ == "__main__":
    main()
