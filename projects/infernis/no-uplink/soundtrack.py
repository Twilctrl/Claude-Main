#!/usr/bin/env python3
"""Synthesize the No Uplink score: a low 90 BPM pulse in D minor, hits on the
cuts, radio static, radar pings, lock-on beeps and one big impact when the
thermal view cuts to colour. numpy only.

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
EIGHTH = 60 / 90 / 2

dry = np.zeros((2, N))
wet = np.zeros((2, N))
rng = np.random.default_rng(11)


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

def score():
    c = CUES
    # 1 · signal: a faint drone, link chatter, then the cuts and silence
    place(pad([26, 33], 3.4, a=1.5, r=0.4, cutoff=260, gain=0.14), 0.0)
    place(static(3.0, 21) * ar(int(3.0 * SR), 0.3, 0.05), 0.1, 0.018)
    for i in range(14):
        place(tick(i, 2600, 3600, 0.4), 0.35 + i * 0.06, 0.05, pan=-0.6 + i * 0.09)
    for i, t in enumerate(c["cuts"]):
        place(tick(100 + i, 900, 1400, 0.7), t, 0.12, pan=(i % 5 - 2) * 0.2)
    place(boom(1.6, 48, 30, 0.5), 3.05, 0.45)
    t = T(2.8)
    place(np.sin(2 * np.pi * midi(86) * t) * ar(len(t), 1.0, 1.2) * 0.012, 3.3, send=0.6)
    place(pad([38, 45, 50], 2.6, a=1.8, r=0.3, cutoff=700, gain=0.1, seed=2), 3.65, send=0.4)
    place(beep(1200, 0.12) * 0.6, 5.85, 0.2, send=0.5)

    # 2 · boot: the hit, then the pulse starts
    place(hit(), 6.25, 0.55, send=0.4)
    for i in range(7):
        place(tick(200 + i, 3000, 3800, 0.35), 6.4 + i * 0.2, 0.08, pan=-0.4)
    for lt in (0.357, 0.952, 1.438, 2.07, 2.67, 3.15):
        if lt < 3.6:
            place(ping(1450), 6.25 + lt, 0.06, pan=0.4, send=0.7)

    prog = [("Dm", 6.25), ("Bb", 10.0), ("Dm", 13.75), ("C", 17.5), ("Dm", 21.25), ("Bb", 25.0), ("Gm", 28.1), ("Dm", 31.25), ("A", 34.4)]
    root = {"Dm": 38, "Bb": 34, "C": 36, "Gm": 31, "A": 33}
    chords = {"Dm": [50, 53, 57], "Bb": [46, 50, 53], "C": [48, 52, 55], "Gm": [43, 50, 55], "A": [45, 49, 52]}
    for i, (name, t0) in enumerate(prog):
        t1 = prog[i + 1][1] if i + 1 < len(prog) else 37.5
        place(pad(chords[name], t1 - t0 + 0.8, a=0.6, r=0.8, cutoff=850, gain=0.11, seed=i + 5), t0, send=0.45)
    # the ostinato: root, root, octave, root ... brighter as it goes
    t = 6.25 + EIGHTH * 2
    k = 0
    while t < 43.6:
        name = next(n for n, t0 in reversed(prog) if t >= t0) if t < 37.5 else "Dm"
        n = root[name] + (12 if k % 4 == 2 else 0)
        cut = 380 + 900 * min(1, (t - 6.25) / 30)
        if 19.95 < t < 20.6:
            cut = 220
        gain = 0.26 if not (43.0 < t) else 0.18
        if not (44.0 < t):
            place(pulse_note(n, EIGHTH * 0.95, cut), t, gain, pan=-0.15 if k % 2 else 0.15)
        t += EIGHTH
        k += 1
    # soft low drum on the beat from Ember to Flare
    for b in np.arange(17.5, 37.4, EIGHTH * 2):
        place(boom(0.6, 90, 45, 0.12), b, 0.22)

    # 3 · CerberOS
    place(hit(), 10.0, 0.5, send=0.4)
    for i in range(4):
        place(tick(300 + i, 1600, 2000, 0.8), 11.0 + i * 0.2, 0.12, send=0.4)
    for i in range(24):
        place(tick(320 + i, 3200, 4200, 0.3), 11.9 + i * 0.035, 0.05, pan=-0.3)
    place(whoosh(0.8, 500, 3500, peak=0.5), 14.25, 0.12, pan=0.5)

    # 4 · Ember
    place(hit(), 17.5, 0.5, send=0.4)
    place(whoosh(2.5, 150, 900, peak=0.5, bw=1.0), 17.8, 0.1)
    place(static(0.65, 33, 600, 4000) * ar(int(0.65 * SR), 0.01, 0.1), 19.95, 0.08)
    place(sine(np.linspace(220, 140, int(0.6 * SR)), 0.6) * decay(int(0.6 * SR), 0.3) * 0.5, 19.95, 0.12)
    place(braam((26, 38, 45), 2.8, 1.0), 20.6, 0.32, send=0.4)
    place(ping(1700), 21.2, 0.05, send=0.6)

    # 5 · Feather
    place(hit(), 25.0, 0.45, send=0.4)
    place(boom(0.5, 120, 60, 0.06), 27.45, 0.45)
    place(tick(400, 900, 1100, 1.0), 27.45, 0.35)
    place(ping(980, 1.5), 27.5, 0.08, send=0.7)
    for i, t in enumerate(c["laptop"]):
        place(key(500 + i), t, 0.08, pan=0.4)

    # 6 · Flare
    place(hit(), 31.25, 0.45, send=0.4)
    t = T(6.0)
    tens = lp(saw(midi(74), 6.0, 10, 7) * (0.6 + 0.4 * np.sin(2 * np.pi * 5.5 * t)), 2500) * ar(len(t), 1.5, 0.6)
    place(tens, 31.3, 0.035, pan=0.3, send=0.6)
    for i in range(3):
        for j in range(3):
            place(beep(2100, 0.05), 32.15 + i * 0.35 + j * 0.06, 0.06, pan=-0.2 + 0.2 * i)
    for k2 in range(5):
        place(beep(660, 0.18) * 0.8, 35.35 + k2 * 0.625, 0.08)
        place(beep(880, 0.12) * 0.8, 35.35 + k2 * 0.625 + 0.2, 0.06)

    # 7 · doctrine: a hit on every card, then the wall
    for i in range(8):
        place(hit(big=(i == 3)), 37.5 + i * 0.54, 0.38 if i != 3 else 0.5, send=0.3)
    place(whoosh(0.9, 400, 4000, peak=0.6), 41.7, 0.18)
    place(braam((26, 38, 45, 50), 3.0, 1.0), 42.3, 0.35, send=0.5)
    place(riser(1.4), 42.3, 0.25)

    # 8 · statement: almost nothing
    place(pad([26, 33], 3.5, a=0.2, r=1.2, cutoff=240, gain=0.16), 43.75)
    place(boom(2.5, 44, 26, 1.0), 44.05, 0.4, send=0.4)
    place(pad([62, 65, 69], 2.0, a=0.8, r=0.9, cutoff=1800, gain=0.05, seed=9), 45.3, send=0.7)

    # 9 · thermal lock, then the candle
    t = T(3.5)
    hum = (np.sin(2 * np.pi * 120 * t) * 0.6 + np.sin(2 * np.pi * 7800 * t) * 0.05) * ar(len(t), 0.2, 0.05)
    place(hum, 47.85, 0.035)
    place(crackle(0.75, 50, 5, 1500), 48.65, 0.14)
    place(whoosh(0.9, 180, 1500, peak=0.15, bw=1.0), 49.28, 0.3, send=0.3)
    bt, gap = 49.7, 0.26
    while bt < 50.6:
        place(beep(2300, 0.05), bt, 0.07)
        bt += gap
        gap = max(0.06, gap * 0.8)
    place(beep(2300, 0.38), 50.62, 0.08)
    place(hit(big=True), 51.0, 0.7, send=0.6)
    place(braam((26, 38, 45, 50), 4.0, 1.0), 51.0, 0.3, send=0.5)
    dm9 = [38, 45, 50, 53, 57, 60, 64]
    place(pad(dm9, 7.0, a=1.6, r=2.6, cutoff=1800, gain=0.24, seed=80), 51.0, send=0.6)
    place(whoosh(2.4, 200, 3500, peak=0.85, bw=0.8), 51.4, 0.22, send=0.4)
    place(sine(np.linspace(1100, 2300, int(0.55 * SR)), 0.55) * ar(int(0.55 * SR), 0.05, 0.1), 53.5, 0.05, send=0.7)
    place(whoosh(0.7, 250, 1500, peak=0.15, bw=1.0), 53.98, 0.18, send=0.4)
    place(bell(midi(81), 3.0, 1.4), 54.0, 0.07, send=0.8)


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
