#!/usr/bin/env python3
"""Synthesize The Gate score: a dark 84 BPM pulse in C minor, an opening bell
when the market opens, a hit on every cut, deny buzzes at the fence, a dot-matrix
printer under the audit, and a bloom when the candle lights. numpy only.

    node cues.cjs          # writes build/cues.json from the built edit
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
EIGHTH = 60 / 84 / 2

dry = np.zeros((2, N))
wet = np.zeros((2, N))
rng = np.random.default_rng(17)


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
    if i >= N:
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
    n = len(sig)
    hop = block // 2
    win = 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(block) / block)
    pad = np.concatenate([sig, np.zeros(block)])
    out = np.zeros(n + block)
    f = np.maximum(np.fft.rfftfreq(block, 1 / SR), 1)
    for s in range(0, n, hop):
        fc = f0 * (f1 / f0) ** ((s / max(1, n)) ** curve)
        g = np.exp(-0.5 * (np.log2(f / fc) / bw) ** 2)
        out[s:s + block] += np.fft.irfft(np.fft.rfft(pad[s:s + block] * win) * g, block)
    return out[:n]


def noise(d, seed=None):
    r = np.random.default_rng(seed) if seed is not None else rng
    return r.standard_normal(int(d * SR))


def decay(n, tau):
    return np.exp(-np.arange(n) / SR / tau)


def ar(n, a, r):
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


def saw(f, d, nh=12, seed=0):
    t = T(d)
    r = np.random.default_rng(seed)
    out = np.zeros_like(t)
    for k in range(1, nh + 1):
        if k * f > SR * 0.45:
            break
        out += np.sin(2 * np.pi * k * f * t + r.uniform(0, 6.28)) / k
    return out


# ---------------------------------------------------------------- instruments

def pad(notes, d, a=0.8, r=1.2, cutoff=900, gain=0.1, seed=0):
    out = np.zeros((2, int(d * SR)))
    for ch, sign in ((0, -1), (1, 1)):
        for i, n in enumerate(notes):
            for dc in (-6, 6):
                out[ch] += saw(midi(n) * 2 ** (sign * dc / 1200), d, nh=8, seed=seed * 1000 + i * 13 + ch * 101 + dc + 50)
    return lp(out, cutoff) * ar(out.shape[1], a, r) * gain / len(notes)


def pulse_note(n, d, cutoff):
    """One note of the low ostinato: a plucky filtered saw."""
    t = T(d)
    s = saw(midi(n), d, nh=14, seed=n) + 0.5 * np.sin(2 * np.pi * midi(n - 12) * t)
    env = ar(len(t), 0.003, 0.03) * (0.35 + 0.65 * decay(len(t), 0.09))
    return lp(s * env, cutoff)


def boom(d=2.2, f0=58, f1=28, tau=0.9):
    t = T(d)
    f = f1 + (f0 - f1) * np.exp(-t / 0.22)
    return sine(f, d) * decay(len(t), tau) + lp(noise(d, 9), 150) * decay(len(t), tau * 0.5) * 0.6


def metal(f=210, d=2.5, seed=3):
    r = np.random.default_rng(seed)
    t = T(d)
    s = np.zeros_like(t)
    for k, ratio in enumerate((1, 1.47, 2.09, 2.56, 3.17, 4.23)):
        s += np.sin(2 * np.pi * f * ratio * t + r.uniform(0, 6)) * decay(len(t), 0.9 / (1 + k * 0.6)) / (1 + k * 0.5)
    return s * ar(len(t), 0.001, 0.05)


def hit(big=False):
    d = 3.0
    s = boom(d, 62, 28, 1.0 if big else 0.6)
    s += metal(190 if big else 240, d, 5) * (0.35 if big else 0.22)
    n = int(0.25 * SR)
    s[:n] += hp(noise(0.25, 13), 1200) * decay(n, 0.04) * (0.6 if big else 0.35)
    return s


def braam(notes=(26, 38, 45, 50), d=3.0, gain=1.0):
    t = T(d)
    s = sum(saw(midi(n), d, nh=16, seed=n) for n in notes)
    cut = 200 + 2200 * np.exp(-t / 0.35)
    # time-varying lowpass: overlap-add over Hann blocks
    block, hop = 2048, 1024
    win = 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(block) / block)
    padded = np.concatenate([s, np.zeros(block)])
    out = np.zeros(len(s) + block)
    f = np.fft.rfftfreq(block, 1 / SR)
    for i in range(0, len(s), hop):
        g = 1 / np.sqrt(1 + (f / cut[i]) ** 4)
        out[i:i + block] += np.fft.irfft(np.fft.rfft(padded[i:i + block] * win) * g, block)
    return out[: len(s)] * ar(len(t), 0.02, 1.2) * gain / len(notes)


def tick(seed, lo=1800, hi=3400, body=0.6):
    r = np.random.default_rng(seed)
    d = 0.035
    t = T(d)
    s = np.sin(2 * np.pi * r.uniform(lo, hi) * t) * decay(len(t), 0.0045) * body
    s += hp(r.standard_normal(len(t)), 3000) * decay(len(t), 0.0028) * 0.5
    return s


def key(seed):
    r = np.random.default_rng(seed)
    d = 0.06
    t = T(d)
    s = sweep(r.standard_normal(len(t)), r.uniform(1500, 2400), 900, bw=0.5) * decay(len(t), 0.01)
    s += np.sin(2 * np.pi * r.uniform(170, 230) * t) * decay(len(t), 0.012) * 0.6
    return s


def beep(f=1900, d=0.08):
    t = T(d)
    return np.sin(2 * np.pi * f * t) * ar(len(t), 0.003, 0.01)


def ping(f=1400, d=1.2):
    t = T(d)
    return (np.sin(2 * np.pi * f * t) + 0.3 * np.sin(2 * np.pi * f * 2.01 * t)) * decay(len(t), 0.18) * ar(len(t), 0.002, 0.05)


def static(d, seed=21, lo=900, hi=3500):
    n = sweep(noise(d, seed), lo, hi, bw=1.2)
    crack = (np.random.default_rng(seed).random(len(n)) > 0.9985) * np.random.default_rng(seed + 1).uniform(1, 4, len(n))
    return n + hp(crack, 2000)


def whoosh(d, f0, f1, peak=0.6, bw=0.7, seed=None):
    n = sweep(noise(d, seed), f0, f1, bw=bw)
    t = np.linspace(0, 1, len(n))
    return n * np.where(t < peak, (t / peak) ** 2, ((1 - t) / (1 - peak)) ** 1.6)


def riser(d):
    t = T(d)
    n = sweep(noise(d, 31), 200, 7000, bw=0.9, curve=1.6)
    f = midi(38) * 2 ** (1.5 * (t / d) ** 1.5)
    tone = lp(np.sin(2 * np.pi * np.cumsum(f) / SR) + 0.5 * np.sin(4 * np.pi * np.cumsum(f) / SR), 2000)
    return (n * 0.7 + tone * 0.4) * (t / d) ** 2.4


def crackle(d, density, seed=0, lo=2000):
    r = np.random.default_rng(seed)
    out = np.zeros(int(d * SR))
    for _ in range(int(d * density)):
        i = r.integers(0, len(out) - 200)
        n = int(r.uniform(0.0008, 0.003) * SR)
        out[i:i + n] += r.standard_normal(n) * r.uniform(0.2, 1) * np.linspace(1, 0, n)
    return hp(out, lo)


def bell(f, d=2.0, tau=1.0):
    t = T(d)
    s = np.zeros_like(t)
    for k, a, dk in ((1, 1, 1), (2.0, 0.45, 0.6), (3.01, 0.22, 0.4), (4.17, 0.12, 0.25)):
        s += a * np.sin(2 * np.pi * f * k * t) * decay(len(t), tau * dk)
    return s * ar(len(t), 0.002, 0.05)


# ---------------------------------------------------------------- the score

def opening_bell(t0):
    """Rapid strikes on a bright bell, like a trading-floor opening bell."""
    for k in range(6):
        place(metal(1180, 1.6, 40 + k) * 0.55 + bell(1180, 1.6, 0.6) * 0.45, t0 + k * 0.17, 0.11 * (1 - k * 0.1), send=0.5)


def printer(d):
    t = T(d)
    burst = (np.sin(2 * np.pi * 34 * t) > 0.25).astype(float)
    lines = (np.sin(2 * np.pi * 3.2 * t) > -0.5).astype(float)
    s = sweep(noise(d, 61), 2100, 2500, bw=0.6) * burst * lines * 0.9
    s += np.sin(2 * np.pi * 112 * t) * 0.22 * lines
    return s * ar(len(t), 0.05, 0.1)


def buzz(d=0.32, f=98):
    t = T(d)
    return lp(np.sign(np.sin(2 * np.pi * f * t)), 1400) * decay(len(t), 0.13)


def score():
    c = CUES
    # 1 · pre-market: drone, a ticking clock, a soft tick per candle, two low notes
    place(pad([24, 31], 6.2, a=1.6, r=0.3, cutoff=240, gain=0.15), 0.0)
    for k in range(12):
        place(tick(700 + k, 1700, 1900, 0.9), 0.5 * k, 0.07, pan=0.25)
    for i, t in enumerate(c["chart"]):
        place(tick(i, 2600, 3600, 0.35), t, 0.045, pan=-0.6 + 1.2 * i / len(c["chart"]))
    place(bell(midi(36), 3.0, 1.2) * 0.6 + bell(midi(43), 3.0, 1.2) * 0.4, 2.2, 0.09, send=0.6)
    place(bell(midi(32), 3.0, 1.4), 3.7, 0.1, send=0.6)
    place(whoosh(0.5, 3000, 300, peak=0.7), 5.5, 0.14)

    # 2 · the open: bell, hit, and the pulse starts
    place(hit(), 6.0, 0.55, send=0.4)
    opening_bell(6.02)
    prog = [("Cm", 6.0), ("Ab", 9.75), ("Fm", 13.5), ("G", 17.25), ("Cm", 21.0), ("Ab", 24.0), ("Eb", 27.0), ("Fm", 30.0), ("G", 32.0), ("Cm", 35.0)]
    root = {"Cm": 36, "Ab": 32, "Fm": 29, "G": 31, "Eb": 39, "Bb": 34}
    chords = {"Cm": [48, 51, 55], "Ab": [44, 48, 51], "Fm": [53, 56, 60], "G": [55, 59, 62], "Eb": [51, 55, 58], "Bb": [46, 50, 53]}
    for i, (name, t0) in enumerate(prog):
        t1 = prog[i + 1][1] if i + 1 < len(prog) else 38.0
        place(pad(chords[name], t1 - t0 + 0.8, a=0.7, r=0.8, cutoff=800, gain=0.11, seed=i + 3), t0, send=0.45)
    t = 6.0 + EIGHTH * 2
    k = 0
    while t < 44.3:
        name = next(n for n, t0 in reversed(prog) if t >= t0) if t < 38.0 else "Cm"
        n = root[name] + (12 if k % 4 == 3 else 0)
        cut = 360 + 800 * min(1, (t - 6.0) / 30)
        place(pulse_note(n, EIGHTH * 0.95, cut), t, 0.27, pan=-0.15 if k % 2 else 0.15)
        t += EIGHTH
        k += 1
    for b in np.arange(13.5, 38.0, EIGHTH * 2):
        place(boom(0.55, 86, 44, 0.12), b, 0.2)
    for i, t in enumerate(c["reroute"]):
        place(tick(100 + i, 1500, 2100, 0.7), t, 0.08, pan=(i % 7 - 3) * 0.12)
    place(bell(midi(72), 2.0, 0.8), 11.3, 0.05, send=0.7)

    # 3 · CerberOS Enterprise
    place(hit(), 13.5, 0.5, send=0.4)
    place(whoosh(0.9, 400, 3500, peak=0.5), 13.85, 0.14, pan=0.5)
    r = np.random.default_rng(5)
    for i in range(40):
        place(tick(300 + i, 3200, 4600, 0.3), 15.1 + r.uniform(0, 1.6), 0.035, pan=r.uniform(0, 0.7))
    for i in range(60):
        place(tick(400 + i, 3600, 5200, 0.25), 15.4 + i * 0.09, 0.018, pan=0.5)

    # 4 · perimeter
    place(hit(), 21.0, 0.48, send=0.4)
    place(whoosh(0.6, 1500, 4500, peak=0.9, bw=0.4), 21.9, 0.07)
    for t0 in (22.7, 23.25):
        place(whoosh(0.3, 800, 3000, peak=0.8, bw=0.5), t0, 0.05)
        place(ping(1560, 0.9), t0 + 0.3, 0.05, send=0.5)
    for t0 in (23.85, 24.45):
        place(whoosh(0.3, 800, 3000, peak=0.8, bw=0.5), t0, 0.05)
        place(buzz(), t0 + 0.24, 0.12)
        place(boom(0.4, 110, 55, 0.07), t0 + 0.24, 0.25)

    # 5 · audit
    place(hit(), 27.0, 0.45, send=0.4)
    place(printer(4.7), 27.1, 0.06, pan=-0.1)
    for i in range(36):
        place(tick(500 + i, 2800, 3400, 0.3), 27.6 + i * 0.12, 0.025, pan=0.5)

    # 6 · the key
    place(hit(), 32.0, 0.45, send=0.4)
    place(boom(0.5, 130, 60, 0.06), 34.6, 0.45)
    place(tick(600, 900, 1100, 1.0), 34.6, 0.35)
    place(ping(990, 1.6), 34.65, 0.08, send=0.7)
    place(boom(0.35, 160, 80, 0.05), 34.95, 0.3)

    # 7 · numbers: a hit on every card, then the wall
    for i in range(8):
        place(hit(big=(i == 3)), 38.0 + i * 0.56, 0.36 if i != 3 else 0.48, send=0.3)
    place(whoosh(0.9, 400, 4000, peak=0.6), 42.4, 0.18)
    place(braam((24, 36, 43, 48), 3.0, 1.0), 43.0, 0.33, send=0.5)
    place(riser(1.4), 43.1, 0.24)

    # 8 · statement
    place(pad([24, 31], 3.4, a=0.2, r=1.1, cutoff=220, gain=0.16), 44.5)
    place(boom(2.5, 42, 25, 1.0), 44.75, 0.4, send=0.4)
    place(pad([60, 63, 66], 2.0, a=0.8, r=0.9, cutoff=1600, gain=0.05, seed=9), 45.95, send=0.7)

    # 9 · the candle: a low room tone under the chart so it isn't dead silence
    place(pad([24, 31], 4.0, a=0.6, r=0.6, cutoff=200, gain=0.12, seed=12), 48.0)
    for j, t in enumerate(c["reveal"]):
        place(tick(800 + j, 1900, 2500, 0.6), t, 0.07, pan=-0.5 + j / 17)
    place(whoosh(1.5, 300, 1800, peak=0.8, bw=0.9), 49.55, 0.16)
    place(shimmer_tone(0.6), 50.9, 0.05)
    place(crackle(0.7, 50, 5, 1500), 51.3, 0.14)
    place(whoosh(0.9, 180, 1500, peak=0.15, bw=1.0), 51.78, 0.32, send=0.3)
    place(hit(big=True), 51.8, 0.55, send=0.6)
    cm9 = [36, 43, 48, 51, 55, 58, 62]
    place(pad(cm9, 6.2, a=1.6, r=2.4, cutoff=1800, gain=0.24, seed=80), 51.8, send=0.6)
    place(whoosh(2.4, 200, 3500, peak=0.85, bw=0.8), 52.7, 0.22, send=0.4)
    place(sine(np.linspace(1100, 2300, int(0.55 * SR)), 0.55) * ar(int(0.55 * SR), 0.05, 0.1), 55.0, 0.05, send=0.7)
    place(whoosh(0.7, 250, 1500, peak=0.15, bw=1.0), 55.48, 0.18, send=0.4)
    place(bell(midi(79), 3.0, 1.4), 55.5, 0.07, send=0.8)


def shimmer_tone(d):
    t = T(d)
    return sum(np.sin(2 * np.pi * midi(n) * t + i) for i, n in enumerate((84, 91, 96))) * ar(len(t), 0.2, 0.3) / 3


def reverb(sig, length=3.2, tau=0.7):
    n = int(length * SR)
    t = np.arange(n) / SR
    ir = np.vstack([lp(noise(length, 70 + ch), 4500) * np.exp(-t / tau) for ch in range(2)])
    ir[:, : int(0.015 * SR)] = 0
    ir /= np.sqrt((ir ** 2).sum(axis=1, keepdims=True))
    m = 1 << int(np.ceil(np.log2(sig.shape[1] + n)))
    return np.fft.irfft(np.fft.rfft(sig, m) * np.fft.rfft(ir, m), m)[:, : sig.shape[1]]


def main():
    score()
    mix = dry + reverb(wet) * 0.5
    mix = mix[:, : int(DUR * SR)]
    fade = int(0.8 * SR)
    mix[:, -fade:] *= np.linspace(1, 0, fade) ** 2
    mix /= np.abs(mix).max()
    mix = np.tanh(mix * 1.5) / np.tanh(1.5) * 0.93
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
