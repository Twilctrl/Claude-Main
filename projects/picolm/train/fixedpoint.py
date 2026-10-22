"""Fixed-point conventions shared by QAT, the exporter and every integer engine.

The device only ever sees integers. The numbers below define them; QAT
simulates them, export.py produces them, tools/intref.py and device/engine.py
execute them. Change one and all four must follow (bump the format version).

  matvec input       int8, [-127, 127] (ReLU outputs [0, 127])
  matvec output      out = rshift(rshift(acc + bacc, s1) * M, sh), (M, s1, sh) per row
  gate pre-act       sigmoid LUT index, frac 5   ([-8, 8) in 512 steps)
  candidate pre-act  tanh LUT index, frac 6      ([-4, 4) in 512 steps)
  z (gate)           Q14, 0 .. 16384
  h, tanh(.)         Q15, -32767 .. 32767
  h as matvec input  clamp(rshift(h, 8), -127, 127), i.e. frac 7
  logits             frac 6 (units of 1/64 nat)
  residual stream    int32, scale calibrated at export (folded into M)

rshift(v, s) = (v + ((1 << s) >> 1)) >> s is "round half up", and is exact for
s == 0. All >> are arithmetic shifts.
"""

import math

LUT_SIZE = 512
LUT_LO, LUT_HI = -256, 255          # LUT index range before the +256 offset
Z_IN_FRAC = 5                       # sigmoid LUT input step 1/32
CAND_IN_FRAC = 6                    # tanh LUT input step 1/64
Z_FRAC = 14
H_FRAC = 15
H_MAX = 32767
HQ_SHIFT = 8                        # Q15 state -> frac-7 int8 matvec input
HQ_FRAC = H_FRAC - HQ_SHIFT
LOGIT_FRAC = 6
EXP_FRAC = 5                        # exp LUT input step 1/32 nat
EXP_SIZE = 512

NORM_X_BITS = 11                    # RMSNorm works on x >> k with |x >> k| < 2**11
NORM_INV_BITS = 26                  # inv = (1 << 26) // isqrt(sum((x >> k)**2))
NORM_N_SHIFT = 11                   # n = rshift(xs * inv, 11) is Q15 of xs / r

ACC_BITS = 18                       # |rshift(acc, s1)| < 2**18 ...
M_BITS = 12                         # ... and M < 2**12, so the product stays < 2**30
MAX_SHIFT = 30
IN_MAX = 127

TEMP_ONE = 256                      # sampling temperature is Q8; 0 means greedy
TEMP_MIN, TEMP_MAX = 16, 1024


def rshift(v, s):
    """Rounding arithmetic right shift; works on Python ints and NumPy int arrays."""
    return (v + ((1 << s) >> 1)) >> s


def sigmoid_lut():
    return [round(16384 / (1 + math.exp(-(i + LUT_LO) / (1 << Z_IN_FRAC)))) for i in range(LUT_SIZE)]


def tanh_lut():
    return [max(-H_MAX, min(H_MAX, round(math.tanh((i + LUT_LO) / (1 << CAND_IN_FRAC)) * 32768)))
            for i in range(LUT_SIZE)]


def exp_lut():
    return [round(65535 * math.exp(-k / (1 << EXP_FRAC))) for k in range(EXP_SIZE)]


def requant_params(m, bound):
    """Return (M, s1, sh) so that rshift(rshift(acc, s1) * M, sh) ~= acc * m for |acc| <= bound.

    s1 brings the accumulator under 2**ACC_BITS, M is normalised to
    [2**11, 2**12), so every intermediate fits comfortably in int32.
    """
    s1 = max(0, int(bound).bit_length() - ACC_BITS)
    if m <= 0:
        return 0, s1, 1
    # out ~= (acc / 2**s1) * M / 2**sh, so M = m * 2**(s1 + sh).
    sh = (M_BITS - 1) - math.floor(math.log2(m)) - s1
    M = round(m * 2.0 ** (s1 + sh))
    if M >= 1 << M_BITS:
        M = (M + 1) >> 1
        sh -= 1
    if sh > MAX_SHIFT:
        sh = MAX_SHIFT
        M = round(m * 2.0 ** (s1 + sh))
    if sh < 1:
        raise ValueError(f"multiplier {m:g} too large for the fixed-point format")
    return M, s1, sh
