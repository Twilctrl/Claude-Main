"""Size, RAM and speed budget for a PicoLM model on the PicoCalc (RP2040 + 8 MB PSRAM).

Run from projects/picolm:
    python tools/budget.py --plm runs/s-int4/model.plm
    python tools/budget.py --preset m --quant int4

Sizes are exact. Speeds are estimates until measured on the device; every
assumption is a flag, and docs/budget.md explains where each default comes from.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from train import plm  # noqa: E402

PRESETS = {"xs": (64, 2, 128), "s": (128, 2, 256), "m": (192, 3, 384)}


def shapes_from_config(vocab, d, L, f, quant):
    q = plm.Q_INT8 if quant == "int8" else plm.Q_INT4
    out = [(plm.KIND_EMB, vocab, d, plm.Q_INT8, 2)]
    for _ in range(L):
        out += [(plm.KIND_WZH, 2 * d, d, q, 1), (plm.KIND_WO, d, d, q, 1), (plm.KIND_W1, f, d, q, 1),
                (plm.KIND_W2, d, f, q, 1)]
    return out


def record_bytes(rows, cols, quant, n_meta):
    return rows * (n_meta * plm.META_SIZE + plm.row_bytes(cols, quant))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plm", default=None)
    ap.add_argument("--preset", default="s", choices=sorted(PRESETS))
    ap.add_argument("--quant", default="int4", choices=["int8", "int4", "ternary"])
    ap.add_argument("--vocab", type=int, default=85)
    ap.add_argument("--mhz", type=float, default=200, help="Picoware's RP2040 default clock")
    ap.add_argument("--asm-cycles", type=float, default=6.5, help="cycles per MAC, asm_thumb kernel (est.)")
    ap.add_argument("--viper-cycles", type=float, default=30, help="cycles per MAC, viper kernel (est.)")
    ap.add_argument("--psram-mbs", type=float, default=12, help="read32_bulk throughput, MB/s (est. 8-20)")
    ap.add_argument("--sd-mbs", type=float, default=1.5, help="SD read throughput, MB/s (est. 1-2.5)")
    ap.add_argument("--overhead-ms", type=float, default=1.5, help="per-token glue: norms, LUTs, sampling (est.)")
    ap.add_argument("--swap-ms", type=float, default=28, help="one full-screen Draw.swap() (est.)")
    ap.add_argument("--refresh-hz", type=float, default=8, help="screen refreshes per second while generating")
    ap.add_argument("--staging-kb", type=float, default=8)
    ap.add_argument("--code-kb", type=float, default=30, help="engine + UI bytecode/native code (est.)")
    ap.add_argument("--free-heap-kb", type=float, default=90, help="free heap when the app starts (unmeasured)")
    args = ap.parse_args()

    if args.plm:
        P = plm.read(args.plm)
        h = P["header"]
        V, d, L, f = h["vocab"], h["d_model"], h["n_layers"], h["d_ffn"]
        shapes = [(t["kind"], t["rows"], t["cols"], t["quant"], len(t["meta"])) for t in P["tensors"]]
        file_bytes = P["file_len"]
        label = f"{args.plm} ({'int8' if h['quant_body'] == plm.Q_INT8 else 'int4'} body)"
        n_merges = h["n_merges"]
    else:
        d, L, f = PRESETS[args.preset]
        V = args.vocab
        shapes = shapes_from_config(V, d, L, f, args.quant)
        file_bytes = None
        label = f"preset {args.preset}, {args.quant} body"
        n_merges = 0

    body = [s for s in shapes if s[0] != plm.KIND_EMB]
    emb = [s for s in shapes if s[0] == plm.KIND_EMB][0]
    params = sum(r * c for _, r, c, _, _ in shapes)
    body_rec = sum(record_bytes(r, c, q, m) for _, r, c, q, m in body)
    emb_rec = record_bytes(*emb[1:])
    emb_row = emb_rec // emb[1]
    macs_body = sum(r * c for _, r, c, _, _ in body)
    macs_head = emb[1] * emb[2]
    if file_bytes is None:
        file_bytes = (64 + 2 * V + 4 * n_merges + 3072 + (2 * L + 1) * (4 + 2 * d) + 16 * len(shapes)
                      + body_rec + emb_rec)

    print(f"== {label}")
    print(f"   vocab {V}, d {d}, layers {L}, ffn {f}: {params:,} weights")
    print(f"   .plm file {file_bytes:,} B (SD card; copied once into PSRAM at start)")
    print(f"   weight records: body {body_rec:,} B, embedding/head {emb_rec:,} B")

    sram = {
        "weight staging buffer": args.staging_kb * 1024,
        "activations (x, u, pre, h, ffn, logits)": 4 * d + d + 8 * d + 4 * L * d + d + 5 * f + 8 * V,
        "LUTs (sigmoid, tanh, exp)": 3072,
        "norm gains": (2 * L + 1) * (4 + 2 * d),
        "tokenizer tables": 2 * V + 4 * n_merges,
        "code (estimate)": args.code_kb * 1024,
        "UI text buffer": 2048,
    }
    total = sum(sram.values())
    print("== main RAM (SRAM) with weights streamed from PSRAM")
    for k, v in sram.items():
        print(f"   {k:40s} {v / 1024:6.1f} KB")
    print(f"   {'total':40s} {total / 1024:6.1f} KB  (assumed free heap {args.free_heap_kb:.0f} KB)")
    resident = total - args.staging_kb * 1024 + body_rec + emb_rec
    print(f"   all weights resident in SRAM instead: {resident / 1024:.1f} KB"
          + ("  (fits)" if resident < args.free_heap_kb * 1024 else "  (does not fit)"))

    print(f"== per token: {macs_body + macs_head:,} MACs; streams {body_rec + emb_rec + emb_row:,} B "
          f"(prompt tokens skip the head: {macs_body:,} MACs, {body_rec + emb_row:,} B)")
    hz = args.refresh_hz
    print(f"   {'weights in':8s} {'kernel':6s} {'ms/token':>9s} {'tok/s model':>12s} {'tok/s w/ screen':>16s}")
    for store, mbs in (("PSRAM", args.psram_mbs), ("SD", args.sd_mbs), ("SRAM", None)):
        io = 0 if mbs is None else (body_rec + emb_rec + emb_row) / (mbs * 1e6)
        for kernel, cyc in (("viper", args.viper_cycles), ("asm", args.asm_cycles)):
            t = (macs_body + macs_head) * cyc / (args.mhz * 1e6) + io + args.overhead_ms / 1e3
            screen = (1 - hz * args.swap_ms / 1e3) / t
            print(f"   {store:8s} {kernel:6s} {1e3 * t:9.1f} {1 / t:12.1f} {screen:16.1f}")
    print("   (estimates: kernel cycles, PSRAM/SD throughput, overhead and swap time are unmeasured)")


if __name__ == "__main__":
    main()
