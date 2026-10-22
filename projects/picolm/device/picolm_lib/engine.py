"""Integer-only PicoLM inference for MicroPython on a 32-bit MCU (RP2040 / RP2350).

Implements docs/format.md. tools/intref.py (NumPy) is the reference: the
golden cases in <model>.golden.json must come out token for token.

Weights stream through one staging buffer from a store (PSRAM, SD file or
RAM; see hal.py). Every buffer is allocated in __init__, and step() and
sample() allocate nothing, so generation never triggers garbage collection.

The @micropython.viper kernels rely on 32-bit native ints: ptr32 loads are
signed, and no product can overflow because every bound is fixed by the
format (largest is z * (cand - h) < 2**30). check_platform() refuses a
64-bit MicroPython, where ptr32 loads are zero-extended.
"""

import array
import struct

import micropython
from micropython import const

from . import hal
from .tokenizer import Tokenizer

MAGIC = b"PLM1"
HEADER_FMT = "<4sHHBBBBHHHHBBBBIIIIHHIIIII"
TABLE_FMT = "<BBBBHHII"
_META = const(8)  # bytes per meta slot; const() so viper code can use it
KIND_EMB, KIND_WZH, KIND_WO, KIND_W1, KIND_W2 = 1, 2, 3, 4, 5
Q_INT8 = 1
EXP_FRAC = 5
NORM_ONE = 1 << 26
TEMP_MIN, TEMP_MAX = 16, 1024


# -- kernels -------------------------------------------------------------------------

@micropython.viper
def _mv8(w: ptr8, rows: int, stride: int, wofs: int, mofs: int, cols: int,
         x: ptr32, sum_x: int, out: ptr32, row0: int):
    """out[row0 + r] = requant(sum_c q[r][c] * x[c] + bacc[r]) for int8 rows (stored q + 128)."""
    w32 = ptr32(w)
    corr = sum_x << 7
    r = 0
    while r < rows:
        rec = r * stride
        p = rec + wofs
        acc = 0
        c = 0
        while c < cols:
            acc += w[p] * x[c] + w[p + 1] * x[c + 1] + w[p + 2] * x[c + 2] + w[p + 3] * x[c + 3]
            p += 4
            c += 4
        m = rec + mofs
        acc = acc - corr + w32[m >> 2]
        s1 = w[m + 6]
        if s1:
            acc = (acc + (1 << (s1 - 1))) >> s1
        acc = acc * (w[m + 4] | (w[m + 5] << 8))
        sh = w[m + 7]
        out[row0 + r] = (acc + (1 << (sh - 1))) >> sh
        r += 1


@micropython.viper
def _mv4(w: ptr8, rows: int, stride: int, wofs: int, mofs: int, cols: int,
         x: ptr32, sum_x: int, out: ptr32, row0: int):
    """As _mv8 for int4 rows: two weights per byte (q + 8), even column in the low nibble."""
    w32 = ptr32(w)
    corr = sum_x << 3
    r = 0
    while r < rows:
        rec = r * stride
        p = rec + wofs
        acc = 0
        c = 0
        while c < cols:
            b = w[p]
            acc += (b & 15) * x[c] + (b >> 4) * x[c + 1]
            b = w[p + 1]
            acc += (b & 15) * x[c + 2] + (b >> 4) * x[c + 3]
            p += 2
            c += 4
        m = rec + mofs
        acc = acc - corr + w32[m >> 2]
        s1 = w[m + 6]
        if s1:
            acc = (acc + (1 << (s1 - 1))) >> s1
        acc = acc * (w[m + 4] | (w[m + 5] << 8))
        sh = w[m + 7]
        out[row0 + r] = (acc + (1 << (sh - 1))) >> sh
        r += 1


@micropython.viper
def _embed(w: ptr8, base: int, d: int, x: ptr32):
    """x = requant(q_emb[tok]) using the record at w[base:], meta slot 1 (embedding)."""
    m = base + _META
    M = w[m + 4] | (w[m + 5] << 8)
    s1 = w[m + 6]
    sh = w[m + 7]
    p = base + 2 * _META
    c = 0
    while c < d:
        v = w[p + c] - 128
        if s1:
            v = (v + (1 << (s1 - 1))) >> s1
        x[c] = (v * M + (1 << (sh - 1))) >> sh
        c += 1


@micropython.viper
def _norm_a(x: ptr32, d: int, xs: ptr32) -> int:
    """RMSNorm part 1: xs = x >> k (|xs| < 2**11); returns isqrt(sum xs**2), at least 1."""
    m = 0
    c = 0
    while c < d:
        v = x[c]
        if v < 0:
            v = 0 - v
        if v > m:
            m = v
        c += 1
    k = 0
    while (m >> k) >= 2048:
        k += 1
    ss = 0
    c = 0
    while c < d:
        v = x[c] >> k
        xs[c] = v
        ss += v * v
        c += 1
    bit = 1 << 15
    bit = bit * bit  # 2**30, built at run time: the literal would not be a small int
    while bit > ss:
        bit >>= 2
    res = 0
    while bit != 0:
        if ss >= res + bit:
            ss -= res + bit
            res = (res >> 1) + bit
        else:
            res >>= 1
        bit >>= 2
    if res < 1:
        res = 1
    return res


@micropython.viper
def _norm_b(xs: ptr32, d: int, inv: int, g: ptr16, g0: int, shift: int, out: ptr32) -> int:
    """RMSNorm part 2: out = clamp(rshift(rshift(xs * inv, 11) * gain, shift), +-127); returns sum(out)."""
    rnd = (1 << shift) >> 1
    s = 0
    c = 0
    while c < d:
        n = (xs[c] * inv + 1024) >> 11
        gv = g[g0 + c]
        if gv > 32767:
            gv -= 65536
        u = (n * gv + rnd) >> shift
        if u > 127:
            u = 127
        elif u < -127:
            u = -127
        out[c] = u
        s += u
        c += 1
    return s


@micropython.viper
def _gate(pre: ptr32, d: int, sig: ptr16, tnh: ptr16, h: ptr32, h0: int, out: ptr32) -> int:
    """z = sigmoid LUT, cand = tanh LUT, h += rshift(z * (cand - h), 14); out = int8(h); returns sum(out)."""
    s = 0
    c = 0
    while c < d:
        zi = pre[c]
        if zi < -256:
            zi = -256
        elif zi > 255:
            zi = 255
        ci = pre[d + c]
        if ci < -256:
            ci = -256
        elif ci > 255:
            ci = 255
        z = sig[zi + 256]
        cand = tnh[ci + 256]
        if cand > 32767:
            cand -= 65536
        hv = h[h0 + c]
        hv += (z * (cand - hv) + 8192) >> 14
        h[h0 + c] = hv
        q = (hv + 128) >> 8
        if q > 127:
            q = 127
        elif q < -127:
            q = -127
        out[c] = q
        s += q
        c += 1
    return s


@micropython.viper
def _relu(v: ptr32, n: int, out: ptr32) -> int:
    s = 0
    c = 0
    while c < n:
        a = v[c]
        if a < 0:
            a = 0
        elif a > 127:
            a = 127
        out[c] = a
        s += a
        c += 1
    return s


@micropython.viper
def _add(x: ptr32, y: ptr32, n: int):
    c = 0
    while c < n:
        x[c] = x[c] + y[c]
        c += 1


@micropython.viper
def _zero(x: ptr32, n: int):
    c = 0
    while c < n:
        x[c] = 0
        c += 1


@micropython.viper
def _argmax(lg: ptr32, n: int) -> int:
    best = 0
    i = 1
    while i < n:
        if lg[i] > lg[best]:
            best = i
        i += 1
    return best


@micropython.viper
def _topk(lg: ptr32, n: int, k: int, inv_t: int, ex: ptr16, ex_n: int, shift: int,
          chosen: ptr8, w: ptr32) -> int:
    """Mark the k highest logits (lowest id wins ties), weight them with the exp LUT; returns the total."""
    fill = 0
    if k >= n:
        fill = 1
    i = 0
    while i < n:
        chosen[i] = fill
        i += 1
    top = lg[0]
    if fill:
        i = 1
        while i < n:
            if lg[i] > top:
                top = lg[i]
            i += 1
        k = 0
    j = 0
    while j < k:
        best = -1
        i = 0
        while i < n:
            if chosen[i] == 0:
                if best < 0:
                    best = i
                elif lg[i] > lg[best]:
                    best = i
            i += 1
        chosen[best] = 1
        if j == 0:
            top = lg[best]
        j += 1
    total = 0
    i = 0
    while i < n:
        wi = 0
        if chosen[i]:
            dl = top - lg[i]
            if dl > 65535:
                dl = 65535
            kk = (dl * inv_t) >> shift
            if kk < ex_n:
                wi = ex[kk]
        w[i] = wi
        total += wi
        i += 1
    return total


@micropython.viper
def _pick(w: ptr32, n: int, r: int) -> int:
    i = 0
    while i < n:
        if r < w[i]:
            return i
        r -= w[i]
        i += 1
    return n - 1


@micropython.viper
def _ld32(p: ptr32) -> int:
    return p[0]


def check_platform():
    if _ld32(array.array("i", [-1])) != -1:
        raise RuntimeError("PicoLM needs a 32-bit MicroPython (RP2040/RP2350); "
                           "on a PC use device/sim with CPython or a 32-bit unix build")


def _zeros(typecode, n):
    return array.array(typecode, [0] * n)


# -- engine --------------------------------------------------------------------------

class Engine:
    """Load a .plm model and run it one token at a time.

    mode: "auto" (PSRAM if present, else RAM if it fits, else SD), "psram", "ram" or "sd".
    """

    def __init__(self, path=None, mode="auto", staging=8192, log=None):
        check_platform()
        self.path = path or hal.default_model_path()
        self.log = log
        self.profile = False
        self.io_us = 0
        with open(self.path, "rb") as f:
            self.header = f.read(64)
            (magic, version, _, arch, _, _, L, d, ffn, V, n_merges, unk_id, eot_id, logit_frac, _,
             tok_off, lut_off, norm_off, table_off, n_tensors, _, _, _, file_len, _, _) = \
                struct.unpack(HEADER_FMT, self.header)
            if magic != MAGIC or version != 1 or arch != 1:
                raise ValueError("not a PicoLM v1 model: " + self.path)
            self.L, self.d, self.ffn, self.V = L, d, ffn, V
            self.eot = eot_id
            self.file_len = file_len

            f.seek(tok_off)
            raw = f.read(lut_off - tok_off)
            tokens = []
            p = 0
            for _ in range(V):
                n = raw[p]
                tokens.append(bytes(raw[p + 1:p + 1 + n]).decode())
                p += 1 + n
            p += (4 - p % 4) % 4
            merges = [struct.unpack_from("<HH", raw, p + 4 * i) for i in range(n_merges)]
            self.tok = Tokenizer(tokens, n_merges, merges, unk_id)
            raw = None

            f.seek(lut_off)
            self.sig = _zeros("h", 512)
            self.tanh = _zeros("h", 512)
            self.exp = _zeros("H", 512)
            f.readinto(self.sig)
            f.readinto(self.tanh)
            f.readinto(self.exp)
            self.exp_shift = 8 + logit_frac - EXP_FRAC

            n_norms = 2 * L + 1
            self.gains = _zeros("h", n_norms * d)
            self.norm_shift = []
            gv = memoryview(self.gains)
            norm_stride = (4 + 2 * d + 3) & ~3
            for i in range(n_norms):
                f.seek(norm_off + i * norm_stride)
                self.norm_shift.append(struct.unpack("<i", f.read(4))[0])
                f.readinto(gv[i * d:(i + 1) * d])

            f.seek(table_off)
            tab = f.read(16 * n_tensors)
            table = [struct.unpack_from(TABLE_FMT, tab, 16 * i) for i in range(n_tensors)]

        self.staging = bytearray(staging)
        self.mode = self._open_store(mode)
        self.store_read = None if self.store.resident is not None else self.store.read
        self._plan(table)

        self.x = _zeros("i", d)
        self.xs = _zeros("i", d)
        self.xin = _zeros("i", max(d, ffn))
        self.out = _zeros("i", max(2 * d, ffn, V))
        self.h = _zeros("i", L * d)
        self.logits = _zeros("i", V)
        self.wts = _zeros("i", V)
        self.chosen = bytearray(V)
        self._temp = -1
        self._inv_t = 0
        self.seed(1)

    # -- storage ------------------------------------------------------------------

    def _open_store(self, mode):
        if mode == "auto":
            if hal.psram_available():
                try:
                    return self._open_store("psram")
                except (MemoryError, OSError, RuntimeError) as e:
                    if self.log:
                        self.log("PSRAM unusable (%s): streaming from SD instead" % e)
            free = hal.mem_free()
            mode = "ram" if free < 0 or self.file_len < free // 2 else "sd"
        if mode == "psram":
            self.store = hal.PsramStore(self.file_len)
            self._copy_to_store()
        elif mode == "ram":
            self.store = hal.RamStore(self.path, self.file_len)
        elif mode == "sd":
            self.store = hal.FileStore(self.path)
        else:
            raise ValueError("unknown mode " + mode)
        return self.store.kind

    def _copy_to_store(self):
        t0 = hal.ticks_ms()
        mv = memoryview(self.staging)
        off = 0
        with open(self.path, "rb") as f:
            while off < self.file_len:
                n = f.readinto(mv)
                if not n:
                    break
                self.store.write(off, mv[:(n + 3) & ~3])
                off += n
        if self.log:
            self.log("copied %d bytes to %s in %d ms" % (off, self.store.kind,
                                                         hal.ticks_diff(hal.ticks_ms(), t0)))

    def ensure_loaded(self):
        """Before generating: make sure the PSRAM copy is still there; reload it if not.

        Returns True if a reload was needed.
        """
        if self.store.kind != "PSRAM":
            return False
        head = bytearray(64)
        try:
            if not self.store.ready():
                self.store.reinit()
            self.store.read(0, head)
            if bytes(head) == self.header:
                return False
        except RuntimeError:
            self.store.reinit()
        self._copy_to_store()
        return True

    def _plan(self, table):
        """Per tensor: (kernel, stride, weights offset, cols, [(src, rows, row0, view), ...])."""
        res = self.store.resident
        smv = memoryview(self.staging)
        views = {}
        self.wzh, self.wo, self.w1, self.w2 = [], [], [], []
        for kind, layer, quant, n_meta, rows, cols, offset, stride in table:
            per = rows if res is not None else len(self.staging) // stride
            plan = []
            r = 0
            while r < rows:
                n = min(per, rows - r)
                src = offset + r * stride
                nb = n * stride
                if res is not None:
                    mv = memoryview(res)[src:src + nb]
                else:
                    mv = views.get(nb)
                    if mv is None:
                        mv = views[nb] = smv[:nb]
                plan.append((src, n, r, mv))
                r += n
            t = (_mv8 if quant == Q_INT8 else _mv4, stride, n_meta * _META, cols, plan)
            if kind == KIND_EMB:
                self.emb = t
                self.emb_off = offset
            else:
                (self.wzh, self.wo, self.w1, self.w2)[kind - KIND_WZH].append(t)
        stride = self.emb[1]
        self.erow = res if res is not None else bytearray(stride)
        self.erow_mv = memoryview(self.erow)[:stride]

    # -- inference ----------------------------------------------------------------

    def reset(self):
        _zero(self.h, self.L * self.d)

    def _mv(self, t, s, out):
        kern, stride, wofs, cols, plan = t
        read = self.store_read
        xin = self.xin
        prof = self.profile
        for src, n, r0, mv in plan:
            if read is not None:
                if prof:
                    t0 = hal.ticks_us()
                    read(src, mv)
                    self.io_us += hal.ticks_diff(hal.ticks_us(), t0)
                else:
                    read(src, mv)
            kern(mv, n, stride, wofs, 0, cols, xin, s, out, r0)

    def _norm(self, i):
        r = _norm_a(self.x, self.d, self.xs)
        return _norm_b(self.xs, self.d, NORM_ONE // r, self.gains, i * self.d, self.norm_shift[i], self.xin)

    def step(self, tok, want_logits=True):
        """Feed one token. With want_logits, self.logits holds the next-token logits."""
        d, x, out = self.d, self.x, self.out
        stride = self.emb[1]
        src = self.emb_off + tok * stride
        if self.store_read is None:
            _embed(self.erow, src, d, x)
        else:
            self.store_read(src, self.erow_mv)
            _embed(self.erow, 0, d, x)
        for l in range(self.L):
            s = self._norm(2 * l)
            self._mv(self.wzh[l], s, out)
            s = _gate(out, d, self.sig, self.tanh, self.h, l * d, self.xin)
            self._mv(self.wo[l], s, out)
            _add(x, out, d)
            s = self._norm(2 * l + 1)
            self._mv(self.w1[l], s, out)
            s = _relu(out, self.ffn, self.xin)
            self._mv(self.w2[l], s, out)
            _add(x, out, d)
        if want_logits:
            s = self._norm(2 * self.L)
            self._mv(self.emb, s, self.logits)

    def prime(self, text):
        """Reset, then feed EOT + the prompt. Returns the number of tokens fed."""
        ids = [self.eot] + self.tok.encode(text)
        self.reset()
        for t in ids[:-1]:
            self.step(t, False)
        self.step(ids[-1], True)
        return len(ids)

    # -- sampling -----------------------------------------------------------------

    def seed(self, s):
        """xorshift32 state, kept as two 16-bit halves so every value stays a small int."""
        s &= 0xFFFFFFFF
        if s == 0:
            s = 0x9E3779B9
        self._hi = s >> 16
        self._lo = s & 0xFFFF

    def _rand30(self):
        hi, lo = self._hi, self._lo
        hi ^= ((hi << 13) | (lo >> 3)) & 0xFFFF   # x ^= x << 13
        lo ^= (lo << 13) & 0xFFFF
        lo ^= hi >> 1                             # x ^= x >> 17
        hi ^= ((hi << 5) | (lo >> 11)) & 0xFFFF   # x ^= x << 5
        lo ^= (lo << 5) & 0xFFFF
        self._hi, self._lo = hi, lo
        return (hi << 14) | (lo >> 2)

    def sample(self, temp_q8, top_k):
        """Next token from self.logits: temp_q8 is Q8 (0 = greedy), top_k 0 = all."""
        V = self.V
        if temp_q8 <= 0:
            return _argmax(self.logits, V)
        if temp_q8 != self._temp:
            t = min(max(temp_q8, TEMP_MIN), TEMP_MAX)
            self._inv_t = (65536 + t // 2) // t
            self._temp = temp_q8
        k = top_k if 0 < top_k < V else V
        total = _topk(self.logits, V, k, self._inv_t, self.exp, 512, self.exp_shift, self.chosen, self.wts)
        return _pick(self.wts, V, self._rand30() % total)

    def close(self):
        self.store.close()
