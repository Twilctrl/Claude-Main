# PicoLM model file (`.plm`, version 1) and integer forward pass

This is the contract between the PC side (`train/export.py`, `tools/intref.py`)
and the device engine (`device/engine.py`). The NumPy reference in
`tools/intref.py` is the executable version of this document; when they
disagree, the code wins and this page has a bug.

## File layout

Little-endian. Every section, every tensor and every row starts on a 4-byte
boundary, because the device reads weights with `PSRAM.read32_bulk()`, which
needs 4-byte-aligned addresses and lengths.

### Header (64 bytes)

| Offset | Type | Field |
|---|---|---|
| 0 | `char[4]` | magic `PLM1` |
| 4 | u16 | version (1) |
| 6 | u16 | header size (64) |
| 8 | u8 | arch (1 = minGRU-style recurrent) |
| 9 | u8 | body weight quant: 1 = int8, 2 = int4 (ternary models use int4) |
| 10 | u8 | embedding/head quant (1 = int8) |
| 11 | u8 | n_layers |
| 12 | u16 | d_model |
| 14 | u16 | d_ffn |
| 16 | u16 | vocab |
| 18 | u16 | n_merges |
| 20 | u8 | unk_id (token used for characters outside the vocab) |
| 21 | u8 | eot_id (end-of-text, also fed before a prompt) |
| 22 | u8 | logit_frac (6: logits are in units of 1/64 nat) |
| 23 | u8 | reserved |
| 24 | u32 | tokenizer section offset |
| 28 | u32 | LUT section offset |
| 32 | u32 | norm section offset |
| 36 | u32 | tensor table offset |
| 40 | u16 | number of tensors |
| 42 | u16 | reserved |
| 44 | u32 | weights section offset |
| 48 | u32 | weights section length |
| 52 | u32 | file length |
| 56 | u32 | CRC-32 (zlib) of the whole file with this field set to 0 |
| 60 | u32 | reserved |

### Tokenizer

For each of `vocab` tokens: `u8 length` then that many ASCII bytes. Pad to 4.
Then `n_merges` pairs `(u16 a, u16 b)` in rank order; merge `i` creates token
`vocab - n_merges + i`. Pad to 4.

Ids 0, 1, 2 are the control tokens EOT (`\x03`), USER (`\x01`) and CHAR
(`\x02`). Then single characters sorted by code point, then merged tokens.
Encoding a prompt: split it into chunks (`train/tokenizer.py:chunks`), map
characters to ids (unknown characters become `unk_id`), then inside each chunk
repeatedly merge the adjacent pair with the lowest rank. With `n_merges = 0`
this is a plain character lookup.

### LUTs (3,072 bytes)

| Table | Type | Entry `i` |
|---|---|---|
| sigmoid | i16[512] | `round(16384 * sigmoid((i - 256) / 32))` (Q14) |
| tanh | i16[512] | `clamp(round(32768 * tanh((i - 256) / 64)), ±32767)` (Q15) |
| exp | u16[512] | `round(65535 * exp(-i / 32))` |

### Norms

`2 * n_layers + 1` entries in execution order (layer 0 norm 1, layer 0 norm 2,
layer 1 norm 1, ..., final norm). Each is `i32 shift`, then `i16 gain[d_model]`,
then padding to 4.

### Tensor table

16 bytes per tensor: `u8 kind, u8 layer, u8 quant, u8 n_meta, u16 rows,
u16 cols, u32 offset, u32 stride`. Kinds: 1 EMB (embedding and tied output
head), 2 WZH (gate rows then candidate rows, `2d × d`), 3 WO (`d × d`), 4 W1
(`ffn × d`), 5 W2 (`d × ffn`). File order: EMB, then for each layer WZH, WO,
W1, W2.

### Weight records

Each tensor is `rows` records of `stride` bytes. A record is `n_meta` meta
slots of 8 bytes (`i32 bacc, u16 M, u8 s1, u8 sh`) followed by the row's
weights:

- int8: one byte per weight, stored as `q + 128` (`q` in −127..127);
- int4: two weights per byte, stored as `q + 8` (`q` in −8..7), the even
  column in the low nibble.

Offset-binary storage means the kernel never sign-extends:
`Σ q·x = Σ u·x − zero_point · Σ x`, with `Σ x` computed once per input vector.
EMB rows have two meta slots, `[head, embedding]`; every other tensor has one.

## Integer forward pass

Notation: `rshift(v, s) = (v + ((1 << s) >> 1)) >> s` (round half up, exact
for `s = 0`, arithmetic shift). `clamp(v, lo, hi)` as usual. All values below
fit in int32; `tools/verify.py` reports the largest magnitude each step
reaches.

**Matvec** with tensor `W`, meta slot `m`, int8 input `in`:

```
acc[r] = Σ_c q[r][c] * in[c] + bacc[r]
out[r] = rshift(rshift(acc[r], s1[r]) * M[r], sh[r])
```

The exporter guarantees `|rshift(acc, s1)| ≤ 2^18` and `M < 2^12`, so the
product stays below 2^30.

**RMSNorm** of the int32 residual `x` with norm entry `(shift, gain)`:

```
k   = max(0, bit_length(max_c |x[c]|) - 11)
xs  = x >> k                       (floor shift, |xs| < 2^11)
r   = max(1, isqrt(Σ xs[c]^2))     (floor square root)
inv = (1 << 26) // r
u[c] = clamp(rshift(rshift(xs[c] * inv, 11) * gain[c], shift), -127, 127)
```

**One token** (state `h[l]` is int32[d] per layer, zero after a reset):

```
x = rshift(rshift(q_emb[tok], s1) * M, sh)            EMB meta slot 1 (embedding), bacc = 0
for l in layers:
    u    = rmsnorm(x, norm 2l)
    pre  = matvec(WZH[l], u)
    z    = sigmoid_lut[clamp(pre[0:d],  -256, 255) + 256]      Q14
    cand = tanh_lut   [clamp(pre[d:2d], -256, 255) + 256]      Q15
    h[l] = h[l] + rshift(z * (cand - h[l]), 14)
    x    = x + matvec(WO[l], clamp(rshift(h[l], 8), -127, 127))
    v    = rmsnorm(x, norm 2l+1)
    x    = x + matvec(W2[l], clamp(matvec(W1[l], v), 0, 127))
logits = matvec(EMB, rmsnorm(x, final norm)) with meta slot 0 (head)
```

Prompt tokens other than the last skip the final norm and head.

## Sampling

Temperature `t` is Q8 (256 = 1.0); `t = 0` means greedy (highest logit, lowest
id on ties). Otherwise `t` is clamped to 16..1024 and:

```
order  = token ids sorted by (logit descending, id ascending), cut to top_k if 0 < top_k < vocab
top    = logit of order[0]
inv_t  = (65536 + t // 2) // t
for each id in order, visited in ascending id order:
    k = (min(top - logit[id], 65535) * inv_t) >> 9      (8 for Q8, +1 for frac 6 -> frac 5)
    w[id] = exp_lut[k] if k < 512 else 0
r = rand30() % Σ w
pick the first id (ascending) where r < running sum of w
```

`rand30()` is xorshift32 (`x ^= x << 13; x ^= x >> 17; x ^= x << 5`, all
mod 2^32, a zero seed becomes `0x9E3779B9`) returning `x >> 2`, which stays
a MicroPython small int.

Generation primes the state with EOT followed by the prompt, then samples
until EOT or the token limit. The same prompt, settings and seed must give the
same tokens on the PC (`tools/sample.py`) and on the device.
