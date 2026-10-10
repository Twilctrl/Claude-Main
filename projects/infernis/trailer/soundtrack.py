#!/usr/bin/env python3
"""Synthesize the trailer soundtrack: a dark 100 BPM bed in D minor plus sound
design (keys, whooshes, heartbeats, the match strike) placed on the picture's
cue times. numpy only.

    node cues.cjs          # writes build/cues.json from the built trailer
    python3 soundtrack.py  # writes build/soundtrack.wav and build/soundtrack.mp3
"""
import json
import subprocess
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build"
SR = 48000
CUES = json.loads((BUILD / "cues.json").read_text())
DUR = CUES["duration"]
N = int(SR * (DUR + 3))
BEAT = 0.6  # 100 BPM

dry = np.zeros((2, N))
wet = np.zeros((2, N))
rng = np.random.default_rng(7)


# ---------------------------------------------------------------- utilities

def T(d):
    return np.arange(int(d * SR)) / SR


def midi(n):
    return 440.0 * 2 ** ((n - 69) / 12)


def place(sig, t, gain=1.0, pan=0.0, send=0.0):
    if sig.ndim == 1:
        a = (pan + 1) * np.pi / 4
        sig = np.vstack([sig * np.cos(a), sig * np.sin(a)]) * np.sqrt(2)
    i = int(round(t * SR))
    if i >= N or i + sig.shape[1] <= 0:
        return
    j = min(N, i + sig.shape[1])
    part = sig[:, : j - i] * gain
    dry[:, i:j] += part
    if send:
        wet[:, i:j] += part * send


def fft_filter(sig, gain_fn):
    n = sig.shape[-1]
    m = 1 << int(np.ceil(np.log2(n + 1)))
    f = np.fft.rfftfreq(m, 1 / SR)
    return np.fft.irfft(np.fft.rfft(sig, m) * gain_fn(f), m)[..., :n]


def lp(sig, fc, order=2):
    return fft_filter(sig, lambda f: 1 / np.sqrt(1 + (f / fc) ** (2 * order)))


def hp(sig, fc, order=2):
    return fft_filter(sig, lambda f: 1 / np.sqrt(1 + (fc / np.maximum(f, 1e-3)) ** (2 * order)))


def sweep(sig, f0, f1, bw=0.6, block=2048, curve=1.0):
    """Band-pass whose centre glides from f0 to f1 (overlap-add, Hann)."""
    n = len(sig)
    hop = block // 2
    win = 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(block) / block)
    pad = np.concatenate([sig, np.zeros(block)])
    out = np.zeros(n + block)
    f = np.maximum(np.fft.rfftfreq(block, 1 / SR), 1)
    for s in range(0, n, hop):
        p = (s / max(1, n)) ** curve
        fc = f0 * (f1 / f0) ** p
        g = np.exp(-0.5 * (np.log2(f / fc) / bw) ** 2)
        out[s:s + block] += np.fft.irfft(np.fft.rfft(pad[s:s + block] * win) * g, block)
    return out[:n]


def noise(d, seed=None):
    r = np.random.default_rng(seed) if seed is not None else rng
    return r.standard_normal(int(d * SR))


def decay(n, tau):
    return np.exp(-np.arange(n) / SR / tau)


def ar(n, a, r):
    """Linear attack, linear release, flat in between."""
    e = np.ones(n)
    na, nr = int(a * SR), int(r * SR)
    if na:
        e[:na] = np.linspace(0, 1, na) ** 1.5
    if nr:
        e[-nr:] *= np.linspace(1, 0, nr) ** 1.5
    return e


def sine(f, d, phase=0.0):
    f = np.broadcast_to(np.asarray(f, float), (int(d * SR),))
    return np.sin(2 * np.pi * np.cumsum(f) / SR + phase)


def saw(f, d, nh=9, seed=0):
    t = T(d)
    r = np.random.default_rng(seed)
    out = np.zeros_like(t)
    for k in range(1, nh + 1):
        if k * f > SR * 0.45:
            break
        out += np.sin(2 * np.pi * k * f * t + r.uniform(0, 6.28)) / k
    return out


# ---------------------------------------------------------------- instruments

def pad(notes, d, a=0.6, r=1.0, cutoff=1200, gain=0.1, seed=0):
    out = np.zeros((2, int(d * SR)))
    for ch, sign in ((0, -1), (1, 1)):
        for i, n in enumerate(notes):
            for dc in (-7, 7):
                out[ch] += saw(midi(n) * 2 ** (sign * dc / 1200), d, seed=seed * 1000 + i * 13 + ch * 101 + dc + 50)
    out = lp(out, cutoff) * ar(out.shape[1], a, r)
    return out * gain / len(notes)


def kick():
    d = 0.5
    t = T(d)
    f = 44 + 96 * np.exp(-t / 0.04)
    body = sine(f, d) * decay(len(t), 0.17)
    click = hp(noise(d, 1), 2500) * decay(len(t), 0.0025) * 0.35
    return body + click


def hat(seed=0):
    d = 0.06
    return hp(noise(d, seed), 7500, 3) * decay(int(d * SR), 0.011)


def clap():
    d = 0.25
    n = sweep(noise(d, 5), 1400, 1100, bw=0.8)
    e = decay(len(n), 0.06)
    for k in (0.0, 0.012, 0.024):
        e[int(k * SR):int(k * SR) + 60] += 0.6
    return n * e


def bass(n, d):
    t = T(d)
    f = midi(n)
    s = np.sin(2 * np.pi * f * t) + 0.35 * np.sin(4 * np.pi * f * t) + 0.12 * np.sin(6 * np.pi * f * t)
    return lp(s * ar(len(t), 0.004, 0.06) * (0.55 + 0.45 * decay(len(t), 0.12)), 420)


def pluck(n, d=0.35):
    t = T(d)
    f = midi(n)
    s = np.sin(2 * np.pi * f * t) + 0.3 * np.sin(4 * np.pi * f * t + 1)
    return s * decay(len(t), 0.09) * ar(len(t), 0.002, 0.02)


def bell(f, d=2.0, tau=1.0):
    t = T(d)
    s = np.zeros_like(t)
    for k, a, dk in ((1, 1, 1), (2.0, 0.45, 0.6), (3.01, 0.22, 0.4), (4.17, 0.12, 0.25)):
        s += a * np.sin(2 * np.pi * f * k * t) * decay(len(t), tau * dk)
    return s * ar(len(t), 0.002, 0.05)


def boom(d=2.2, f0=58, f1=30, tau=0.9):
    t = T(d)
    f = f1 + (f0 - f1) * np.exp(-t / 0.25)
    return sine(f, d) * decay(len(t), tau) + lp(noise(d, 9), 160) * decay(len(t), tau * 0.5) * 0.6


def whoosh(d, f0, f1, peak=0.6, bw=0.7, seed=None):
    n = sweep(noise(d, seed), f0, f1, bw=bw)
    t = np.linspace(0, 1, len(n))
    e = np.where(t < peak, (t / peak) ** 2, ((1 - t) / (1 - peak)) ** 1.6)
    return n * e


def tick(seed, lo=1800, hi=3400, body=0.6):
    r = np.random.default_rng(seed)
    d = 0.035
    t = T(d)
    s = np.sin(2 * np.pi * r.uniform(lo, hi) * t) * decay(len(t), 0.0045) * body
    s += hp(r.standard_normal(len(t)), 3000) * decay(len(t), 0.0028) * 0.5
    return s


def key(seed):
    """A heavier, mechanical key for the laptop."""
    r = np.random.default_rng(seed)
    d = 0.06
    t = T(d)
    s = sweep(r.standard_normal(len(t)), r.uniform(1500, 2400), 900, bw=0.5) * decay(len(t), 0.01)
    s += np.sin(2 * np.pi * r.uniform(170, 230) * t) * decay(len(t), 0.012) * 0.6
    return s


def thump(f0=72, f1=42, tau=0.13):
    d = 0.4
    t = T(d)
    f = f1 + (f0 - f1) * np.exp(-t / 0.03)
    return sine(f, d) * decay(len(t), tau) + lp(noise(d, 3), 200) * decay(len(t), 0.05) * 0.5


def impact(big=False):
    d = 2.5 if big else 1.6
    s = boom(d, 64, 30, 0.7 if big else 0.45)
    s[: int(0.3 * SR)] += hp(noise(0.3, 11), 900) * decay(int(0.3 * SR), 0.05) * (0.7 if big else 0.45)
    return s


def glitch(d=0.28, seed=4):
    r = np.random.default_rng(seed)
    out = np.zeros(int(d * SR))
    i = 0
    while i < len(out):
        n = int(r.uniform(0.008, 0.025) * SR)
        f = r.choice([180, 360, 720, 1440, 2880]) * r.uniform(0.9, 1.1)
        t = np.arange(n) / SR
        out[i:i + n] = np.sign(np.sin(2 * np.pi * f * t))[: len(out) - i] * r.uniform(0.2, 1)
        i += n
    return lp(np.round(out * 4) / 4, 5000)


def crackle(d, density, seed=0, lo=2000):
    r = np.random.default_rng(seed)
    out = np.zeros(int(d * SR))
    for _ in range(int(d * density)):
        i = r.integers(0, len(out) - 200)
        n = int(r.uniform(0.0008, 0.003) * SR)
        out[i:i + n] += r.standard_normal(n) * r.uniform(0.2, 1) * np.linspace(1, 0, n)
    return hp(out, lo)


def scribble(d=0.6, seed=2):
    r = np.random.default_rng(seed)
    t = T(d)
    n = sweep(noise(d, seed), 2600, 3400, bw=0.5)
    wob = 0.5 + 0.5 * np.sin(2 * np.pi * (17 + 6 * np.sin(2 * np.pi * 1.3 * t)) * t + r.uniform(0, 6))
    return n * wob * ar(len(t), 0.03, 0.1)


def printer(d=1.8):
    t = T(d)
    burst = (np.sin(2 * np.pi * 38 * t) > 0.2).astype(float)
    lines = (np.sin(2 * np.pi * 12 / d * t) > -0.6).astype(float)
    s = sweep(noise(d, 21), 2200, 2600, bw=0.6) * burst * lines * 0.9
    s += np.sin(2 * np.pi * 118 * t) * 0.25 * lines
    return s * ar(len(t), 0.02, 0.05)


def shimmer(d, notes, gain=1.0):
    t = T(d)
    s = sum(np.sin(2 * np.pi * midi(n) * t + i) * (0.5 + 0.5 * np.sin(2 * np.pi * (0.7 + 0.3 * i) * t + i)) for i, n in enumerate(notes))
    return s * ar(len(t), d * 0.4, d * 0.5) * gain / len(notes)


def riser(d):
    t = T(d)
    n = sweep(noise(d, 31), 250, 9000, bw=0.9, curve=1.6)
    f = midi(38) * 2 ** (2 * (t / d) ** 1.4)
    tone = lp(np.sin(2 * np.pi * np.cumsum(f) / SR) + 0.5 * np.sin(4 * np.pi * np.cumsum(f) / SR), 2500)
    e = (t / d) ** 2.2
    return (n * 0.8 + tone * 0.35) * e


# ---------------------------------------------------------------- the score

CHORD = {"Dm": [50, 53, 57, 62], "Bb": [46, 50, 53, 58], "F": [53, 57, 60, 65], "C": [48, 55, 60, 64]}
ROOT = {"Dm": 38, "Bb": 34, "F": 41, "C": 36}


def chord_at(prog, t):
    cur = prog[0][0]
    for name, t0 in prog:
        if t >= t0:
            cur = name
    return cur


def bed():
    # 0 - 6.9: cold open, a faint high shimmer and a sub drone
    place(shimmer(6.8, [74, 81, 86], 0.05), 0.2, send=0.6)
    place(pad([38, 45], 7.2, a=2.0, r=1.5, cutoff=300, gain=0.10), 0.0)
    place(pad(CHORD["Dm"], 4.2, a=1.6, r=0.8, cutoff=700, gain=0.08, seed=3), 2.9, send=0.3)

    # 6.9 - 21: groove, Dm Bb F C Dm Bb, one chord per bar
    prog1 = [("Dm", 6.9), ("Bb", 9.3), ("F", 11.7), ("C", 14.1), ("Dm", 16.5), ("Bb", 18.9)]
    for i, (name, t0) in enumerate(prog1):
        end = prog1[i + 1][1] if i + 1 < len(prog1) else 21.0
        cut = 1500 if t0 < 14 else 1100
        place(pad(CHORD[name], end - t0 + 0.6, a=0.25, r=0.6, cutoff=cut, gain=0.1, seed=i), t0, send=0.35)
    for k in range(1, 12):  # four on the floor through CerberOS
        place(kick(), 6.9 + k * BEAT, 0.55)
    for k in range(int((13.9 - 7.2) / 0.3)):
        t = 7.2 + k * 0.3
        place(bass(ROOT[chord_at(prog1, t)], 0.28), t, 0.32)
        if k % 2:
            place(hat(k), t, 0.06, pan=0.3)
    for k in range(6):  # half time under the keyboard
        place(kick(), 14.6 + k * 2 * BEAT, 0.4)
    for k in range(int((20.4 - 15.0) / 0.3)):
        place(hat(100 + k), 15.0 + k * 0.3, 0.05, pan=-0.2 + 0.4 * (k % 2))
    arp = [62, 65, 69, 74, 69, 65]
    for k in range(int((19.7 - 17.4) / 0.15)):  # arpeggio while the laptop types
        t = 17.4 + k * 0.15
        name = chord_at(prog1, t)
        notes = [n + 12 for n in CHORD[name]]
        place(pluck(notes[k % len(notes)]), t, 0.07, pan=0.4 * np.sin(k), send=0.5)

    # 21 - 27: the horror beat, a drone with a creeping dissonance
    place(pad([38, 45, 50], 6.4, a=0.8, r=1.2, cutoff=500, gain=0.14, seed=40), 21.0, send=0.4)
    t = T(4.5)
    creep = np.sin(2 * np.pi * midi(75) * t) * (t / 4.5) ** 2 * ar(len(t), 0.5, 0.6) * 0.035
    place(creep, 21.6, pan=0.2, send=0.7)

    # 27 - 36.6: plugins, groove back with claps
    prog2 = [("Dm", 27.0), ("Bb", 29.4), ("F", 31.8), ("C", 34.2)]
    for i, (name, t0) in enumerate(prog2):
        place(pad(CHORD[name], 3.0, a=0.15, r=0.6, cutoff=1700, gain=0.1, seed=50 + i), t0, send=0.35)
    for k in range(16):
        t = 27.0 + k * BEAT
        place(kick(), t, 0.55)
        if k % 2:
            place(clap(), t, 0.16, send=0.4)
        place(hat(200 + k), t + 0.3, 0.07, pan=0.25)
    for k in range(int((36.5 - 27.0) / 0.3)):
        t = 27.0 + k * 0.3
        place(bass(ROOT[chord_at(prog2, t)], 0.28), t, 0.34)

    # 36.6 - 40.6: the code shot, muffled
    for i, (name, t0, d) in enumerate((("Bb", 36.6, 2.0), ("C", 38.6, 2.2))):
        place(pad(CHORD[name], d + 0.5, a=0.3, r=0.5, cutoff=700, gain=0.1, seed=60 + i), t0, send=0.4)
    for k in range(6):
        place(lp(kick(), 300), 36.6 + k * BEAT, 0.45)

    # 40.6 - 44.35: riser, then silence
    r = riser(3.75)
    place(r, 40.6, 0.7, send=0.25)
    place(pad(CHORD["Dm"] + [69], 3.75, a=2.5, r=0.02, cutoff=2400, gain=0.2, seed=70), 40.6, send=0.3)
    for k, tt in enumerate(np.cumsum([0.3, 0.26, 0.22, 0.19, 0.16, 0.14, 0.12, 0.1, 0.09, 0.08, 0.07, 0.06, 0.05, 0.05, 0.05])):
        if 42.2 + tt < 44.33:
            place(clap(), 42.2 + tt, 0.05 + 0.012 * k)

    # 44.4 - end: the candle
    place(lp(noise(1.9, 50), 300) * ar(int(1.9 * SR), 0.6, 0.3), 44.4, 0.02)
    dm9 = [38, 45, 50, 53, 57, 60, 64]
    place(pad(dm9, 9.2, a=1.6, r=2.6, cutoff=1800, gain=0.26, seed=80), 46.2, send=0.55)
    place(pad([62, 69, 76], 5.0, a=1.5, r=2.0, cutoff=3500, gain=0.09, seed=90), 50.6, send=0.8)
    place(shimmer(3.6, [86, 93, 98], 0.04), 51.5, send=0.9)


def sfx():
    c = CUES
    # 1 · pill
    place(sine(np.linspace(880, 1320, int(0.12 * SR)), 0.12) * decay(int(0.12 * SR), 0.04), 0.25, 0.18, send=0.5)
    place(whoosh(0.7, 300, 2600, peak=0.85), 0.62, 0.12, send=0.3)
    place(shimmer(0.8, [93, 98], 0.5), 1.5, 0.12, send=0.6)
    place(whoosh(0.6, 3000, 400, peak=0.3), 2.42, 0.1, send=0.3)
    # 2 · prompt
    place(whoosh(0.45, 600, 2500, peak=0.4), 2.95, 0.07)
    for i in range(5):
        place(tick(500 + i, 2400, 3600, 0.4), 3.55 + i * 0.07, 0.12, pan=-0.4 + 0.2 * i)
    for i, t in enumerate(c["typing"]):
        place(tick(i), t, 0.11, pan=(i % 7 - 3) * 0.05)
    place(tick(900, 1200, 1400, 1.0), 6.0, 0.35)
    place(whoosh(0.4, 4000, 500, peak=0.85), 6.3, 0.14)
    place(thump(90, 50, 0.08), 6.75, 0.35)
    place(impact(), 6.9, 0.5, send=0.5)
    place(whoosh(0.7, 500, 5000, peak=0.25), 6.9, 0.14, send=0.3)
    # 3 · CerberOS
    for i in range(8):
        place(tick(600 + i, 900, 1300, 0.8), 7.02 + i * 0.045, 0.12, pan=-0.3 + 0.08 * i)
    for i, n in enumerate((74, 77, 81)):
        place(bell(midi(n), 2.0, 0.8), 8.1 + i * 0.12, 0.07, pan=-0.5 + 0.5 * i, send=0.6)
    place(thump(110, 60, 0.1), 9.0, 0.3)
    place(bell(midi(62), 2.5, 1.0), 9.0, 0.06, send=0.6)
    place(whoosh(0.9, 400, 3000, peak=0.5), 10.25, 0.16, pan=0.5, send=0.3)
    place(whoosh(0.9, 500, 3500, peak=0.5), 10.5, 0.14, pan=0.3, send=0.3)
    for k in range(11):
        place(tick(700 + k, 3000, 3600, 0.3), 11.6 + k * 0.19, 0.06, pan=0.3)
    place(whoosh(0.55, 700, 7000, peak=0.6), 13.75, 0.3)
    # 4 · Feather
    for t in (14.5, 15.85, 16.5):
        place(whoosh(0.6, 2000, 5000, peak=0.5, bw=0.4), t, 0.05, pan=-0.3)
    place(tick(950, 1000, 1200, 1.0), 17.05, 0.3)
    place(bell(midi(86), 1.0, 0.3), 17.05, 0.05, send=0.5)
    for i, t in enumerate(c["laptop"]):
        place(key(1000 + i), t, 0.1, pan=0.35)
    place(whoosh(0.9, 300, 4000, peak=0.9), 20.35, 0.28)
    place(boom(1.8, 50, 30, 0.5), 21.2, 0.35, send=0.3)
    # 5 · Scream and Run
    place(glitch(), 22.0, 0.12, send=0.3)
    for i, t in enumerate(c["heartbeat"]):
        place(thump(70 if i % 2 == 0 else 62, 42, 0.14), t, 0.75 if i % 2 == 0 else 0.5)
    for t, big in ((22.3, False), (23.3, False), (24.25, True), (25.25, False)):
        place(impact(big), t, 0.34 if not big else 0.45, send=0.6)
    place(tick(1100, 1600, 2000, 0.8), 25.6, 0.15, send=0.4)
    place(whoosh(0.9, 300, 6000, peak=0.5), 26.55, 0.35, send=0.3)
    # 6 · plugins
    place(whoosh(0.35, 800, 4000, peak=0.7), 27.85, 0.1, pan=-0.4)
    for t in (28.25, 29.18):
        place(sine(np.linspace(1400, 700, int(0.06 * SR)), 0.06) * decay(int(0.06 * SR), 0.015), t, 0.25)
        place(tick(1200, 3000, 3500, 0.6), t, 0.2)
    place(tick(1201, 1200, 1400, 1.0), 28.3, 0.2)
    for t in (29.75, 30.1, 30.45):
        place(tick(int(t * 100), 2000, 2600, 0.7), t, 0.15)
    place(thump(160, 90, 0.05), 30.9, 0.25)
    place(whoosh(0.35, 2500, 6000, peak=0.4, bw=0.5), 30.75, 0.07)
    for i in range(12):
        place(tick(1300 + i, 3200, 3800, 0.3), 32.1 + i * 0.025, 0.06)
    place(whoosh(0.55, 1200, 4500, peak=0.9, bw=0.4), 32.45, 0.08)
    place(bell(midi(88), 0.8, 0.25), 33.3, 0.06, send=0.4)
    t = T(0.3)
    buzz = lp(np.sign(np.sin(2 * np.pi * 110 * t)), 1500) * decay(len(t), 0.12)
    place(buzz, 33.65, 0.1)
    place(printer(1.8), 34.7, 0.09, pan=0.1)
    place(whoosh(0.6, 3000, 500, peak=0.6), 36.5, 0.15)
    # 7 · code
    r = np.random.default_rng(3)
    for i in range(40):
        place(tick(1400 + i, 3500, 5200, 0.4), 36.85 + r.uniform(0, 0.95), 0.04, pan=r.uniform(-0.6, 0.6))
    place(scribble(), 38.25, 0.14, pan=0.2)
    place(whoosh(1.8, 200, 1200, peak=0.7, bw=0.9), 38.7, 0.1)
    # 8 · statement
    for i in range(60):
        place(tick(1500 + i, 3500, 5500, 0.4), 40.95 + r.uniform(0, 2.0), 0.035, pan=r.uniform(-0.5, 0.5))
    # 9 · the candle
    place(crackle(0.65, 60, 5, 1500), 45.55, 0.14)
    place(whoosh(1.0, 180, 1600, peak=0.12, bw=1.0), 46.15, 0.5, send=0.4)
    place(boom(2.8, 60, 30, 1.1), 46.2, 0.6, send=0.5)
    place(crackle(5.5, 5, 6, 1200), 46.5, 0.08, pan=-0.2)
    place(whoosh(2.4, 200, 3500, peak=0.85, bw=0.8), 48.1, 0.28, send=0.4)
    place(sine(np.linspace(1100, 2300, int(0.55 * SR)), 0.55) * ar(int(0.55 * SR), 0.05, 0.1), 50.4, 0.05, send=0.7)
    place(whoosh(0.7, 250, 1500, peak=0.15, bw=1.0), 50.92, 0.22, send=0.4)
    place(bell(midi(81), 3.0, 1.4), 50.95, 0.08, send=0.8)


def reverb(sig, length=3.0, tau=0.6):
    n = int(length * SR)
    t = np.arange(n) / SR
    ir = np.vstack([lp(noise(length, 70 + ch), 5000) * np.exp(-t / tau) for ch in range(2)])
    ir[:, : int(0.012 * SR)] = 0
    ir /= np.sqrt((ir ** 2).sum(axis=1, keepdims=True))
    m = 1 << int(np.ceil(np.log2(sig.shape[1] + n)))
    out = np.fft.irfft(np.fft.rfft(sig, m) * np.fft.rfft(ir, m), m)[:, : sig.shape[1]]
    return out


def main():
    bed()
    sfx()
    mix = dry + reverb(wet) * 0.5
    mix = mix[:, : int(DUR * SR)]
    fade = int(0.7 * SR)
    mix[:, -fade:] *= np.linspace(1, 0, fade) ** 2
    mix /= np.abs(mix).max()
    mix = np.tanh(mix * 1.4) / np.tanh(1.4) * 0.93
    pcm = (mix.T * 32767).astype(np.int16)
    BUILD.mkdir(exist_ok=True)
    with wave.open(str(BUILD / "soundtrack.wav"), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(BUILD / "soundtrack.wav"), "-b:a", "160k", str(BUILD / "soundtrack.mp3")], check=True)
    print(f"wrote build/soundtrack.wav and .mp3 ({DUR:.1f}s)")


if __name__ == "__main__":
    main()
