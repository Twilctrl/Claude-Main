"""Export a QAT checkpoint to a .plm file: integer weights, LUTs and requant constants.

Run from projects/picolm:
    python -m train.export --ckpt runs/s-int4/final.pt --data data/tinystories --out runs/s-int4/model.plm

Also writes <out>.json with the float-side scales so tools/verify.py can
re-export in memory and check the file bit for bit. The file is read back and
compared before this script reports success.
"""

import argparse
import json
import math
import os

import numpy as np
import torch

from train import fixedpoint as fx
from train import plm
from train.model import quantize_rows
from train.train import load_model

QUANT_CODE = {"int8": plm.Q_INT8, "int4": plm.Q_INT4, "ternary": plm.Q_INT4}
RESIDUAL_BITS = 16  # calibrated max |x| maps to 2**16 in the int32 residual stream


def calib_tokens(val_bin, seq=256, n=16):
    data = np.memmap(val_bin, dtype=np.uint8, mode="r")
    starts = np.linspace(0, len(data) - seq - 1, n).astype(np.int64)
    return torch.from_numpy(np.stack([data[s:s + seq] for s in starts]).astype(np.int64))


@torch.no_grad()
def residual_scale(model, tokens):
    trace = []
    model.eval()
    model(tokens, qat=True, trace=trace)
    return float(max(trace)) / 2 ** RESIDUAL_BITS


def _quant(w, mode):
    q, s = quantize_rows(w.detach().float().cpu(), mode)
    return q.numpy().astype(np.int8), s.numpy()[:, 0].astype(np.float64)


def matvec_meta(q, s_row, s_in, s_out, bias=None, bound=None):
    """Per-row (bacc, M, s1, sh) for out = s_row * s_in * (q @ in) + bias, in units of s_out."""
    rows = q.shape[0]
    bacc, M, s1, sh = (np.zeros(rows, np.int64) for _ in range(4))
    absq = np.abs(q.astype(np.int64)).sum(1)
    for r in range(rows):
        acc_scale = s_row[r] * s_in
        b = 0 if bias is None else int(round(float(bias[r]) / acc_scale))
        if abs(b) >= 1 << 28:
            raise ValueError("bias too large for the accumulator")
        row_bound = (fx.IN_MAX * int(absq[r]) if bound is None else bound) + abs(b)
        bacc[r] = b
        M[r], s1[r], sh[r] = fx.requant_params(acc_scale / s_out, row_bound)
    return bacc, M, s1, sh


def norm_entry(g, s_out):
    """Integer RMSNorm gain: u = rshift(n * gain, shift), where n is Q15 of x / ||x||."""
    g = g.detach().double().cpu().numpy()
    G = g * math.sqrt(len(g)) / s_out
    shift = min(fx.MAX_SHIFT, 15 + math.floor(math.log2(32767 / max(np.abs(G).max(), 1e-12))))
    if shift < 1:
        raise ValueError("norm gain too large for the fixed-point format")
    gain = np.clip(np.round(G * 2.0 ** (shift - 15)), -32767, 32767).astype(np.int16)
    return {"shift": int(shift), "gain": gain}


def export_params(model, tok, s_x):
    cfg = model.cfg
    qmode, qcode = cfg.quant, QUANT_CODE[cfg.quant]
    s_y = model.aq_y.scale
    tensors = []

    qe, se = _quant(model.emb, "int8")
    head = matvec_meta(qe, se, s_y, 2.0 ** -fx.LOGIT_FRAC, model.b_out.detach().cpu().numpy())
    emb = matvec_meta(qe, se, 1.0, s_x, bound=fx.IN_MAX)
    tensors.append({"kind": plm.KIND_EMB, "layer": 0, "quant": plm.Q_INT8, "rows": cfg.vocab,
                    "cols": cfg.d_model, "q": qe, "meta": [head, emb]})

    norms = []
    d = cfg.d_model
    for l, blk in enumerate(model.blocks):
        s_u, s_v, s_a = blk.aq_u.scale, blk.aq_v.scale, blk.aq_a.scale
        s_h = 2.0 ** -fx.HQ_FRAC
        norms.append(norm_entry(blk.g1, s_u))
        norms.append(norm_entry(blk.g2, s_v))

        q, s = _quant(blk.wzh, qmode)
        b = blk.bzh.detach().cpu().numpy()
        mz = matvec_meta(q[:d], s[:d], s_u, 2.0 ** -fx.Z_IN_FRAC, b[:d])
        mc = matvec_meta(q[d:], s[d:], s_u, 2.0 ** -fx.CAND_IN_FRAC, b[d:])
        meta = tuple(np.concatenate([x, y]) for x, y in zip(mz, mc))
        tensors.append({"kind": plm.KIND_WZH, "layer": l, "quant": qcode, "rows": 2 * d, "cols": d,
                        "q": q, "meta": [meta]})
        for kind, w, bias, s_in, s_out in (
            (plm.KIND_WO, blk.wo, blk.bo, s_h, s_x),
            (plm.KIND_W1, blk.w1, blk.b1, s_v, s_a),
            (plm.KIND_W2, blk.w2, blk.b2, s_a, s_x),
        ):
            q, s = _quant(w, qmode)
            meta = matvec_meta(q, s, s_in, s_out, bias.detach().cpu().numpy())
            tensors.append({"kind": kind, "layer": l, "quant": qcode, "rows": q.shape[0], "cols": q.shape[1],
                            "q": q, "meta": [meta]})
    norms.append(norm_entry(model.gf, s_y))

    header = {"arch": plm.ARCH_MINGRU, "quant_body": qcode, "quant_emb": plm.Q_INT8,
              "n_layers": cfg.n_layers, "d_model": d, "d_ffn": cfg.d_ffn, "vocab": cfg.vocab,
              "n_merges": len(tok.merges), "unk_id": tok.unk_id, "eot_id": tok.eot_id,
              "logit_frac": fx.LOGIT_FRAC}
    luts = {"sig": np.array(fx.sigmoid_lut(), np.int16), "tanh": np.array(fx.tanh_lut(), np.int16),
            "exp": np.array(fx.exp_lut(), np.uint16)}
    return {"header": header, "tokens": [t.encode("ascii") for t in tok.tokens], "merges": list(tok.merges),
            "luts": luts, "norms": norms, "tensors": tensors}


def scales_info(model, s_x):
    return {"s_x": s_x, "s_y": model.aq_y.scale,
            "layers": [{"s_u": b.aq_u.scale, "s_v": b.aq_v.scale, "s_a": b.aq_a.scale} for b in model.blocks]}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--data", default="data/tinystories", help="dataset dir (val.bin is used for calibration)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    model, tok, ck = load_model(args.ckpt)
    if not ck["qat"] or any(float(a.amax) == 0 for a in model.act_quants()):
        raise SystemExit("export needs a QAT checkpoint (final.pt), not a float one")
    model.eval()
    s_x = residual_scale(model, calib_tokens(os.path.join(args.data, "val.bin")))
    P = export_params(model, tok, s_x)
    data = plm.write(args.out, P)
    diffs = plm.params_equal(P, plm.read(data))
    if diffs:
        raise SystemExit(f"round-trip mismatch: {diffs}")
    info = {"ckpt": os.path.abspath(args.ckpt), "quant": model.cfg.quant, "config": model.cfg.to_dict(),
            "file_bytes": len(data), "scales": scales_info(model, s_x), "metrics": ck.get("metrics", {})}
    with open(args.out + ".json", "w") as f:
        json.dump(info, f, indent=1)
    weights = sum(T["rows"] * (len(T["meta"]) * plm.META_SIZE + plm.row_bytes(T["cols"], T["quant"]))
                  for T in P["tensors"])
    print(f"wrote {args.out}: {len(data):,} bytes ({weights:,} in weight records), "
          f"quant {model.cfg.quant}, round-trip OK")


if __name__ == "__main__":
    main()
