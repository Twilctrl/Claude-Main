"""Verify a .plm export against the PyTorch checkpoint it came from.

Run from projects/picolm:
    python tools/verify.py --plm runs/s-int4/model.plm --data data/tinystories

1. Integrity: CRC/layout, and a bit-exact round trip (re-exporting the
   checkpoint in memory gives exactly what the file contains).
2. Integer-only NumPy forward pass (tools/intref.py, reads only the file) vs
   the PyTorch model on held-out text: max/mean logit error against the QAT
   model and against the same weights in float, log-prob error, argmax
   agreement, bits per character for each.
3. int32 headroom: the largest magnitude each integer operation reached.
4. A seeded sample: the golden output the device engine must reproduce.
Exits non-zero if the round trip differs or any integer limit is exceeded.
"""

import argparse
import json
import math
import os
import sys
import time
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
import torch  # noqa: E402
import torch.nn.functional as F  # noqa: E402

from tools.intref import LIMITS, IntModel, generate, tokenizer_from_params  # noqa: E402
from train import fixedpoint as fx  # noqa: E402
from train import plm  # noqa: E402
from train.export import export_params  # noqa: E402
from train.train import load_model  # noqa: E402


def log_softmax(a):
    a = a - a.max(-1, keepdims=True)
    return a - np.log(np.exp(a).sum(-1, keepdims=True))


def bits_per_token(logits, targets):
    return float(-log_softmax(logits)[np.arange(len(targets)), targets].mean() / math.log(2))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plm", required=True)
    ap.add_argument("--ckpt", default=None, help="defaults to the checkpoint recorded in <plm>.json")
    ap.add_argument("--data", default="data/tinystories")
    ap.add_argument("--n-seq", type=int, default=8)
    ap.add_argument("--seq-len", type=int, default=256)
    ap.add_argument("--prompt", default="Once upon a time")
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument("--temp", type=float, default=0.8)
    ap.add_argument("--top-k", type=int, default=20)
    ap.add_argument("--sample-tokens", type=int, default=300)
    args = ap.parse_args()
    ok = True

    raw = open(args.plm, "rb").read()
    P = plm.read(raw)
    h = P["header"]
    with open(args.plm + ".json") as f:
        info = json.load(f)
    print(f"== {args.plm}: {len(raw):,} bytes, CRC OK")
    print(f"   vocab {h['vocab']}, d {h['d_model']}, layers {h['n_layers']}, ffn {h['d_ffn']}, "
          f"body {'int8' if h['quant_body'] == plm.Q_INT8 else 'int4'} ({info['quant']}), emb int8")

    model, tok, _ = load_model(args.ckpt or info["ckpt"])
    model.eval()
    diffs = plm.params_equal(export_params(model, tok, info["scales"]["s_x"]), P)
    print("== round trip: " + ("bit-exact" if not diffs else f"MISMATCH {diffs}"))
    ok &= not diffs

    data = np.memmap(os.path.join(args.data, "val.bin"), dtype=np.uint8, mode="r")
    starts = np.linspace(0, len(data) - args.seq_len - 1, args.n_seq).astype(np.int64)
    im = IntModel(P, check=True)
    scale = 2.0 ** -h["logit_frac"]
    res = {k: [] for k in ("int", "qat", "flt", "tgt")}
    t0 = time.time()
    for s in starts:
        toks = np.asarray(data[s:s + args.seq_len + 1], np.int64)
        x = torch.from_numpy(toks[None, :-1])
        with torch.no_grad():
            res["qat"].append(model(x, qat=True)[0][0].double().numpy())
            res["flt"].append(model(x, qat=False)[0][0].double().numpy())
        im.reset()
        try:
            res["int"].append(np.stack([im.step(int(t)) for t in toks[:-1]]).astype(np.float64) * scale)
        except OverflowError as e:
            print(f"== integer overflow: {e}")
            return 1
        res["tgt"].append(toks[1:])
    dt = time.time() - t0
    L = {k: np.concatenate(v) for k, v in res.items()}
    n_tok = len(L["tgt"])
    print(f"== logits on {args.n_seq} x {args.seq_len} held-out tokens "
          f"(numpy int engine {1e3 * dt / n_tok:.2f} ms/token on this PC)")
    for ref in ("qat", "flt"):
        e = np.abs(L["int"] - L[ref])
        lp = np.abs(log_softmax(L["int"]) - log_softmax(L[ref]))
        agree = float((L["int"].argmax(-1) == L[ref].argmax(-1)).mean())
        name = "QAT model (fake-quant)" if ref == "qat" else "same weights, float"
        print(f"   int vs {name:24s} max |dlogit| {e.max():.4f}  mean {e.mean():.4f}  "
              f"max |dlogp| {lp.max():.4f}  argmax agree {100 * agree:.2f}%")
    unit = "bits/char" if h["n_merges"] == 0 else "bits/token"
    for k, name in (("flt", "float"), ("qat", "QAT"), ("int", "integer")):
        print(f"   {unit} {name:8s} {bits_per_token(L[k], L['tgt']):.4f}")

    print("== int32 headroom (largest magnitude seen / limit)")
    for k, lim in LIMITS.items():
        v = im.peak.get(k, 0)
        print(f"   {k:13s} {v:>12,}  2^{math.log2(max(v, 1)):5.1f} / 2^{math.log2(lim):.0f}")

    temp_q8 = round(args.temp * fx.TEMP_ONE)
    ids = generate(IntModel(P, check=True), tokenizer_from_params(P), args.prompt, args.sample_tokens,
                   temp_q8, args.top_k, args.seed)
    text = tokenizer_from_params(P).decode(ids)
    print(f"== golden sample: prompt {args.prompt!r}, temp_q8 {temp_q8}, top_k {args.top_k}, "
          f"seed {args.seed}, {len(ids)} tokens, crc32 {zlib.crc32(bytes(ids)):08x}")
    print(args.prompt + text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
