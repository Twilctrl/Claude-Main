"""Train PicoLM: a float phase, then quantisation-aware training (QAT).

Run from projects/picolm:
    python -m train.train --data data/tinystories --preset s --quant int8 --out runs/s-int8
    # QAT a different bit-width from the same float model, without retraining it:
    python -m train.train --data data/tinystories --init runs/s-int8/float.pt --steps 0 \
        --quant int4 --out runs/s-int4

Writes float.pt (end of the float phase) and final.pt (after QAT) into --out.
Uses a GPU when torch sees one (ROCm builds report as "cuda"), else the CPU.
"""

import argparse
import json
import math
import os
import time

import numpy as np
import torch
import torch.nn.functional as F

from train.model import PRESETS, Config, PicoLM
from train.tokenizer import Tokenizer


def pick_device(name):
    if name != "auto":
        return torch.device(name)
    if torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")


def lr_at(step, total, base, warmup):
    if total <= 0:
        return base
    if step < warmup:
        return base * (step + 1) / warmup
    p = (step - warmup) / max(1, total - warmup)
    return base * (0.1 + 0.9 * 0.5 * (1 + math.cos(math.pi * min(1.0, p))))


class Batches:
    def __init__(self, path, seq, batch, seed):
        self.data = np.memmap(path, dtype=np.uint8, mode="r")
        self.seq, self.batch = seq, batch
        self.rng = np.random.default_rng(seed)

    def next(self, device):
        ix = self.rng.integers(0, len(self.data) - self.seq - 1, self.batch)
        x = np.stack([self.data[i:i + self.seq + 1] for i in ix]).astype(np.int64)
        return torch.from_numpy(x).to(device)


@torch.no_grad()
def evaluate(model, path, seq, max_tokens, device, qat=None, batch=32):
    """Mean cross-entropy (nats/token) over non-overlapping windows of the validation set."""
    data = np.memmap(path, dtype=np.uint8, mode="r")
    n = min(len(data) - 1, max_tokens) // seq
    starts = np.arange(n) * seq
    was_training = model.training
    model.eval()
    total, count = 0.0, 0
    for i in range(0, n, batch):
        x = np.stack([data[s:s + seq + 1] for s in starts[i:i + batch]]).astype(np.int64)
        x = torch.from_numpy(x).to(device)
        logits, _ = model(x[:, :-1], qat=qat)
        total += F.cross_entropy(logits.reshape(-1, logits.shape[-1]), x[:, 1:].reshape(-1),
                                 reduction="sum").item()
        count += x[:, 1:].numel()
    model.train(was_training)
    return total / max(1, count)


def save(path, model, tok, args, step, metrics):
    torch.save({"config": model.cfg.to_dict(), "model": model.state_dict(), "tokenizer": tok.to_dict(),
                "qat": model.qat, "act_frozen": all(a.frozen for a in model.act_quants()),
                "step": step, "args": vars(args), "metrics": metrics}, path)


def load_model(path, map_location="cpu"):
    ck = torch.load(path, map_location=map_location, weights_only=False)
    model = PicoLM(Config(**ck["config"]))
    model.load_state_dict(ck["model"])
    model.qat = ck["qat"]
    for a in model.act_quants():
        a.frozen = ck.get("act_frozen", False)
    return model, Tokenizer.from_dict(ck["tokenizer"]), ck


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default="data/tinystories")
    ap.add_argument("--out", default="runs/s-int8")
    ap.add_argument("--preset", default="s", choices=sorted(PRESETS))
    ap.add_argument("--quant", default="int8", choices=["int8", "int4", "ternary"])
    ap.add_argument("--init", default=None, help="start from this checkpoint (e.g. a float.pt)")
    ap.add_argument("--steps", type=int, default=6000, help="float training steps")
    ap.add_argument("--qat-steps", type=int, default=2000)
    ap.add_argument("--seq", type=int, default=256)
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--lr", type=float, default=3e-3)
    ap.add_argument("--qat-lr", type=float, default=1e-3)
    ap.add_argument("--warmup", type=int, default=200)
    ap.add_argument("--wd", type=float, default=0.05)
    ap.add_argument("--freeze-frac", type=float, default=0.25,
                    help="freeze activation scales for this final fraction of QAT")
    ap.add_argument("--calib-batches", type=int, default=8)
    ap.add_argument("--eval-every", type=int, default=500)
    ap.add_argument("--eval-tokens", type=int, default=131072)
    ap.add_argument("--log-every", type=int, default=50)
    ap.add_argument("--device", default="auto")
    ap.add_argument("--threads", type=int, default=0, help="CPU threads (0 = torch default)")
    ap.add_argument("--seed", type=int, default=1337)
    args = ap.parse_args()

    torch.manual_seed(args.seed)
    if args.threads:
        torch.set_num_threads(args.threads)
    device = pick_device(args.device)
    hip = getattr(torch.version, "hip", None)
    print(f"device: {device}" + (f" (ROCm/HIP {hip})" if hip and device.type == "cuda" else ""))

    tok = Tokenizer.load(os.path.join(args.data, "tokenizer.json"))
    if args.init:
        model, init_tok, _ = load_model(args.init)
        if init_tok.tokens != tok.tokens:
            raise SystemExit("--init checkpoint was trained with a different tokenizer")
        model.cfg.quant = args.quant
    else:
        model = PicoLM(Config(vocab=tok.vocab_size, quant=args.quant, **PRESETS[args.preset]))
    model.to(device).train()
    print(f"params: {model.n_params():,}  config: {model.cfg.to_dict()}")

    os.makedirs(args.out, exist_ok=True)
    train_bin = os.path.join(args.data, "train.bin")
    val_bin = os.path.join(args.data, "val.bin")
    batches = Batches(train_bin, args.seq, args.batch, args.seed)
    opt = torch.optim.AdamW(model.param_groups(args.wd), lr=args.lr, betas=(0.9, 0.95))
    metrics = {}
    total = args.steps + args.qat_steps
    t0 = time.time()
    tokens = 0

    for step in range(total):
        in_qat = step >= args.steps
        if in_qat and not model.qat:
            if args.steps:
                save(os.path.join(args.out, "float.pt"), model, tok, args, step, metrics)
            model.qat = True
            with torch.no_grad():  # calibrate activation scales before training through them
                for _ in range(args.calib_batches):
                    model(batches.next(device)[:, :-1])
            print("QAT on: activation scales", [round(a.scale, 5) for a in model.act_quants()])
        if in_qat and step - args.steps >= (1 - args.freeze_frac) * args.qat_steps:
            for a in model.act_quants():
                a.frozen = True

        lr = (lr_at(step - args.steps, args.qat_steps, args.qat_lr, args.warmup // 4) if in_qat
              else lr_at(step, args.steps, args.lr, args.warmup))
        for g in opt.param_groups:
            g["lr"] = lr
        x = batches.next(device)
        logits, _ = model(x[:, :-1])
        loss = F.cross_entropy(logits.reshape(-1, logits.shape[-1]), x[:, 1:].reshape(-1))
        opt.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        opt.step()
        tokens += x[:, 1:].numel()

        if step % args.log_every == 0:
            dt = time.time() - t0
            print(f"step {step:5d} {'qat' if in_qat else 'flt'} loss {loss.item():.4f} lr {lr:.2e} "
                  f"{tokens / dt:,.0f} tok/s {dt / 60:.1f} min", flush=True)
        last = step == total - 1
        if (step and step % args.eval_every == 0) or last:
            ce = evaluate(model, val_bin, args.seq, args.eval_tokens, device)
            metrics[f"val_bpt@{step}"] = ce / math.log(2)
            print(f"  eval step {step}: val {ce / math.log(2):.4f} bits/token "
                  f"({'qat ' + model.cfg.quant if model.qat else 'float'})", flush=True)

    if not model.qat:  # float-only run
        save(os.path.join(args.out, "float.pt"), model, tok, args, total, metrics)
    else:
        metrics["val_bpt_float_weights"] = evaluate(model, val_bin, args.seq, args.eval_tokens, device,
                                                    qat=False) / math.log(2)
        save(os.path.join(args.out, "final.pt"), model, tok, args, total, metrics)
    with open(os.path.join(args.out, "metrics.json"), "w") as f:
        json.dump(metrics, f, indent=1)
    print(json.dumps(metrics, indent=1))


if __name__ == "__main__":
    main()
