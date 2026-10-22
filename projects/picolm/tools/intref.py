"""NumPy integer-only reference of the PicoLM forward pass and sampler.

This is the executable spec for device/engine.py. It reads nothing but the
.plm file and uses nothing but integer arithmetic (int64 arrays, but with
check=True every intermediate is asserted to stay inside the int32 ranges the
device relies on; the largest magnitudes seen are kept in .peak).
"""

import math

import numpy as np

from train import fixedpoint as fx
from train import plm
from train.tokenizer import SPECIALS, Tokenizer

LIMITS = {
    "acc": 1 << 31,                       # q @ in + bacc
    "acc_shifted": (1 << fx.ACC_BITS) + 1,
    "product": 1 << 30,                   # rshift(acc, s1) * M
    "norm_sumsq": 1 << 31,
    "norm_scaled": (1 << fx.NORM_INV_BITS) + 1,
    "norm_gain": 1 << 31,
    "gate_update": 1 << 31,               # z * (cand - h)
    "residual": 1 << 30,                  # stays a MicroPython small int
    "logit": 1 << 30,
}


class IntModel:
    def __init__(self, P, check=True):
        h = P["header"]
        self.header = h
        self.L, self.d, self.V = h["n_layers"], h["d_model"], h["vocab"]
        self.sig = np.asarray(P["luts"]["sig"], np.int64)
        self.tanh = np.asarray(P["luts"]["tanh"], np.int64)
        self.exp = [int(v) for v in P["luts"]["exp"]]
        self.T = {(t["kind"], t["layer"]): t for t in P["tensors"]}
        self.q = {k: t["q"].astype(np.int64) for k, t in self.T.items()}
        self.norms = [(int(n["shift"]), np.asarray(n["gain"], np.int64)) for n in P["norms"]]
        self.check = check
        self.peak = {}
        self.reset()

    def reset(self):
        self.h = np.zeros((self.L, self.d), np.int64)

    def _note(self, name, v):
        m = int(np.max(np.abs(v))) if np.size(v) else 0
        if m > self.peak.get(name, -1):
            self.peak[name] = m
        if self.check and m >= LIMITS[name]:
            raise OverflowError(f"{name} reached {m} (limit {LIMITS[name]})")

    def matvec(self, key, inp, slot=0):
        bacc, M, s1, sh = self.T[key]["meta"][slot]
        acc = self.q[key] @ inp + bacc
        self._note("acc", acc)
        a = fx.rshift(acc, s1)
        self._note("acc_shifted", a)
        p = a * M
        self._note("product", p)
        return fx.rshift(p, sh)

    def norm(self, x, i):
        shift, gain = self.norms[i]
        k = max(0, int(np.max(np.abs(x))).bit_length() - fx.NORM_X_BITS)
        xs = x >> k
        ss = int(np.sum(xs * xs))
        self._note("norm_sumsq", ss)
        r = max(1, math.isqrt(ss))
        t = xs * ((1 << fx.NORM_INV_BITS) // r)
        self._note("norm_scaled", t)
        g = fx.rshift(t, fx.NORM_N_SHIFT) * gain
        self._note("norm_gain", g)
        return np.clip(fx.rshift(g, shift), -fx.IN_MAX, fx.IN_MAX)

    def embed(self, tok):
        _, M, s1, sh = self.T[(plm.KIND_EMB, 0)]["meta"][1]
        return fx.rshift(fx.rshift(self.q[(plm.KIND_EMB, 0)][tok], s1[tok]) * M[tok], sh[tok])

    def step(self, tok, want_logits=True):
        """Feed one token; returns int logits (units of 2**-logit_frac nat) or None."""
        d, lo = self.d, fx.LUT_LO
        x = self.embed(tok)
        for l in range(self.L):
            u = self.norm(x, 2 * l)
            pre = self.matvec((plm.KIND_WZH, l), u)
            z = self.sig[np.clip(pre[:d], lo, fx.LUT_HI) - lo]
            cand = self.tanh[np.clip(pre[d:], lo, fx.LUT_HI) - lo]
            upd = z * (cand - self.h[l])
            self._note("gate_update", upd)
            self.h[l] = self.h[l] + fx.rshift(upd, fx.Z_FRAC)
            hq = np.clip(fx.rshift(self.h[l], fx.HQ_SHIFT), -fx.IN_MAX, fx.IN_MAX)
            x = x + self.matvec((plm.KIND_WO, l), hq)
            v = self.norm(x, 2 * l + 1)
            a = np.clip(self.matvec((plm.KIND_W1, l), v), 0, fx.IN_MAX)
            x = x + self.matvec((plm.KIND_W2, l), a)
            self._note("residual", x)
        if not want_logits:
            return None
        logits = self.matvec((plm.KIND_EMB, 0), self.norm(x, 2 * self.L), slot=0)
        self._note("logit", logits)
        return logits


class Sampler:
    """Integer sampling: temperature (Q8, 0 = greedy), top-k, exp LUT, xorshift32 PRNG."""

    def __init__(self, seed=1):
        self.state = (seed & 0xFFFFFFFF) or 0x9E3779B9

    def rand30(self):
        x = self.state
        x ^= (x << 13) & 0xFFFFFFFF
        x ^= x >> 17
        x ^= (x << 5) & 0xFFFFFFFF
        self.state = x
        return x >> 2

    def pick(self, logits, temp_q8, top_k, exp_lut):
        logits = [int(v) for v in logits]
        n = len(logits)
        if temp_q8 <= 0:
            best = 0
            for i in range(1, n):
                if logits[i] > logits[best]:
                    best = i
            return best
        t = min(max(temp_q8, fx.TEMP_MIN), fx.TEMP_MAX)
        order = sorted(range(n), key=lambda i: (-logits[i], i))
        if 0 < top_k < n:
            order = order[:top_k]
        top = logits[order[0]]
        inv_t = ((1 << 16) + t // 2) // t
        shift = 8 + fx.LOGIT_FRAC - fx.EXP_FRAC
        chosen = sorted(order)
        weights = []
        for i in chosen:
            k = (min(top - logits[i], 0xFFFF) * inv_t) >> shift
            weights.append(exp_lut[k] if k < len(exp_lut) else 0)
        r = self.rand30() % sum(weights)
        for i, w in zip(chosen, weights):
            if r < w:
                return i
            r -= w
        return chosen[-1]


def tokenizer_from_params(P):
    n_chars = P["header"]["vocab"] - len(SPECIALS) - P["header"]["n_merges"]
    chars = [t.decode("ascii") for t in P["tokens"][len(SPECIALS):len(SPECIALS) + n_chars]]
    return Tokenizer(chars, P["merges"], unk=P["tokens"][P["header"]["unk_id"]].decode("ascii"))


def stream(model, tok, prompt, n_tokens=200, temp_q8=fx.TEMP_ONE, top_k=0, seed=1, stop_at_eot=True):
    """Prime with EOT + prompt, then sample; yields generated token ids one at a time."""
    eot = model.header["eot_id"]
    ids = [eot] + tok.encode(prompt)
    model.reset()
    for t in ids[:-1]:
        model.step(t, want_logits=False)
    logits = model.step(ids[-1])
    sampler = Sampler(seed)
    for _ in range(n_tokens):
        nxt = sampler.pick(logits, temp_q8, top_k, model.exp)
        if nxt == eot and stop_at_eot:
            return
        yield nxt
        logits = model.step(nxt)


def generate(model, tok, prompt, n_tokens=200, temp_q8=fx.TEMP_ONE, top_k=0, seed=1, stop_at_eot=True):
    return list(stream(model, tok, prompt, n_tokens, temp_q8, top_k, seed, stop_at_eot))
