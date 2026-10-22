"""PicoLM: a gated linear recurrent (minGRU-style) character model with QAT.

Per token, for each of n_layers blocks:

    u  = RMSNorm(x) * g1                      -> int8
    z  = sigmoid(Wz u + bz)                   (LUT)
    h~ = tanh(Wh u + bh)                      (LUT)
    h  = h + z * (h~ - h)                     recurrent state, bounded in (-1, 1)
    x  = x + Wo h + bo
    v  = RMSNorm(x) * g2                      -> int8
    x  = x + W2 relu(W1 v + b1) + b2
logits = E (RMSNorm(x) * gf) + b_out          (head tied to the embedding E)

The gates only see the input, so training runs over a whole sequence at once;
the state carries across tokens at inference, giving constant memory per token.

With qat=True every quantity the device will round is rounded here too (STE):
per-row int8/int4/ternary weights, int8 activations with static scales, the
LUT input grids and the h -> int8 conversion. See train/fixedpoint.py.
"""

import math
from dataclasses import asdict, dataclass

import torch
import torch.nn as nn
import torch.nn.functional as F

from train import fixedpoint as fx

PRESETS = {
    "xs": dict(d_model=64, n_layers=2, d_ffn=128),
    "s": dict(d_model=128, n_layers=2, d_ffn=256),
    "m": dict(d_model=192, n_layers=3, d_ffn=384),
}
QMAX = {"int8": 127, "int4": 7, "ternary": 1}


@dataclass
class Config:
    vocab: int = 96
    d_model: int = 128
    n_layers: int = 2
    d_ffn: int = 256
    quant: str = "int4"  # body weights: int8 | int4 | ternary; embedding/head are always int8

    def to_dict(self):
        return asdict(self)


def quantize_rows(w, mode):
    """Per-output-row symmetric quantisation. Returns integer-valued q and scale s (rows, 1)."""
    if mode == "ternary":
        s = w.abs().mean(1, keepdim=True).clamp_min(1e-8)
        return (w / s).round().clamp(-1, 1), s
    qmax = QMAX[mode]
    s = w.abs().amax(1, keepdim=True).clamp_min(1e-8) / qmax
    return (w / s).round().clamp(-qmax, qmax), s


def fq_weight(w, mode):
    q, s = quantize_rows(w.detach(), mode)
    return w + (q * s - w).detach()


def fq_grid(x, frac, lo, hi):
    s = 2.0 ** -frac
    return x + ((x / s).round().clamp(lo, hi) * s - x).detach()


def rmsnorm(x, eps=1e-6):
    return x * torch.rsqrt(x.pow(2).mean(-1, keepdim=True) + eps)


class ActQuant(nn.Module):
    """Static-scale int8 fake quant. Scale = running abs-max / 127, tracked while training."""

    def __init__(self, unsigned=False, momentum=0.95):
        super().__init__()
        self.unsigned = unsigned
        self.momentum = momentum
        self.frozen = False
        self.register_buffer("amax", torch.zeros(()))

    @property
    def scale(self):
        return max(float(self.amax), 1e-8) / fx.IN_MAX

    def forward(self, x, enabled):
        if not enabled:
            return x
        if self.training and not self.frozen:
            m = x.detach().abs().amax()
            if self.amax == 0:
                self.amax.copy_(m)
            else:
                self.amax.mul_(self.momentum).add_(m * (1 - self.momentum))
        s = self.amax.clamp_min(1e-8) / fx.IN_MAX
        q = (x / s).round().clamp(0 if self.unsigned else -fx.IN_MAX, fx.IN_MAX)
        return x + (q * s - x).detach()


def scan(z, cand, h):
    """h_t = h_{t-1} + z_t * (cand_t - h_{t-1}), sequentially over time (T is small)."""
    keep = 1 - z
    push = z * cand
    out = []
    for t in range(z.shape[1]):
        h = keep[:, t] * h + push[:, t]
        out.append(h)
    return torch.stack(out, 1), h


class Block(nn.Module):
    def __init__(self, cfg):
        super().__init__()
        d, f, L = cfg.d_model, cfg.d_ffn, cfg.n_layers
        self.g1 = nn.Parameter(torch.ones(d))
        self.g2 = nn.Parameter(torch.ones(d))
        self.wzh = nn.Parameter(torch.randn(2 * d, d) / math.sqrt(d))
        # Gate biases spread over time scales: z from ~0.02 (slow) to 0.5 (fast).
        self.bzh = nn.Parameter(torch.cat([torch.linspace(-4, 0, d), torch.zeros(d)]))
        self.wo = nn.Parameter(torch.randn(d, d) / math.sqrt(d * 2 * L))
        self.bo = nn.Parameter(torch.zeros(d))
        self.w1 = nn.Parameter(torch.randn(f, d) / math.sqrt(d))
        self.b1 = nn.Parameter(torch.zeros(f))
        self.w2 = nn.Parameter(torch.randn(d, f) / math.sqrt(f * 2 * L))
        self.b2 = nn.Parameter(torch.zeros(d))
        self.aq_u = ActQuant()
        self.aq_v = ActQuant()
        self.aq_a = ActQuant(unsigned=True)

    def forward(self, x, h0, qat, mode, trace=None):
        d = self.g1.numel()
        wq = (lambda w: fq_weight(w, mode)) if qat else (lambda w: w)
        u = self.aq_u(rmsnorm(x) * self.g1, qat)
        pre = F.linear(u, wq(self.wzh), self.bzh)
        zp, cp = pre[..., :d], pre[..., d:]
        if qat:
            zp = fq_grid(zp, fx.Z_IN_FRAC, fx.LUT_LO, fx.LUT_HI)
            cp = fq_grid(cp, fx.CAND_IN_FRAC, fx.LUT_LO, fx.LUT_HI)
        hs, h = scan(torch.sigmoid(zp), torch.tanh(cp), h0)
        if qat:
            hs = fq_grid(hs, fx.HQ_FRAC, -fx.IN_MAX, fx.IN_MAX)
        x = x + F.linear(hs, wq(self.wo), self.bo)
        if trace is not None:
            trace.append(x.detach().abs().amax())
        v = self.aq_v(rmsnorm(x) * self.g2, qat)
        a = self.aq_a(F.relu(F.linear(v, wq(self.w1), self.b1)), qat)
        x = x + F.linear(a, wq(self.w2), self.b2)
        if trace is not None:
            trace.append(x.detach().abs().amax())
        return x, h


class PicoLM(nn.Module):
    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.qat = False
        self.emb = nn.Parameter(torch.randn(cfg.vocab, cfg.d_model) / math.sqrt(cfg.d_model))
        self.blocks = nn.ModuleList(Block(cfg) for _ in range(cfg.n_layers))
        self.gf = nn.Parameter(torch.ones(cfg.d_model))
        self.b_out = nn.Parameter(torch.zeros(cfg.vocab))
        self.aq_y = ActQuant()

    def act_quants(self):
        return [m for m in self.modules() if isinstance(m, ActQuant)]

    def forward(self, idx, state=None, qat=None, trace=None):
        """idx (B, T) token ids -> logits (B, T, vocab), final recurrent state per layer."""
        qat = self.qat if qat is None else qat
        E = fq_weight(self.emb, "int8") if qat else self.emb
        x = F.embedding(idx, E)
        if trace is not None:
            trace.append(x.detach().abs().amax())
        new_state = []
        for i, blk in enumerate(self.blocks):
            h0 = state[i] if state is not None else x.new_zeros(idx.shape[0], self.cfg.d_model)
            x, h = blk(x, h0, qat, self.cfg.quant, trace)
            new_state.append(h)
        y = self.aq_y(rmsnorm(x) * self.gf, qat)
        return F.linear(y, E, self.b_out), new_state

    def param_groups(self, weight_decay):
        decay = [p for n, p in self.named_parameters() if p.dim() == 2]
        rest = [p for n, p in self.named_parameters() if p.dim() < 2]
        return [{"params": decay, "weight_decay": weight_decay}, {"params": rest, "weight_decay": 0.0}]

    def n_params(self):
        return sum(p.numel() for p in self.parameters())
