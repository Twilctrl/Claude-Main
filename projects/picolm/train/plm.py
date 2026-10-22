"""The .plm model file: writer and reader (NumPy only, no torch).

Layout (little-endian, every section and row 4-byte aligned; docs/format.md
has the full description):

    header      64 bytes, HEADER_FMT
    tokenizer   per token: u8 length + bytes; pad; merges as (u16 a, u16 b); pad
    luts        sigmoid i16[512], tanh i16[512], exp u16[512]
    norms       per norm (layer0.n1, layer0.n2, layer1.n1, ..., final): i32 shift, i16 gain[d]
    table       per tensor 16 bytes: u8 kind, u8 layer, u8 quant, u8 n_meta,
                u16 rows, u16 cols, u32 offset, u32 stride
    weights     per tensor, per row: n_meta x (i32 bacc, u16 M, u8 s1, u8 sh), then packed weights

Weights are stored offset-binary so the device never sign-extends: int8 as
q + 128, int4 as q + 8 with the even column in the low nibble. The embedding
(kind EMB) is also the output head; its rows carry two meta slots,
[head, embedding].
"""

import struct
import zlib

import numpy as np

MAGIC = b"PLM1"
VERSION = 1
HEADER_FMT = "<4sHHBBBBHHHHBBBBIIIIHHIIIII"
HEADER_SIZE = struct.calcsize(HEADER_FMT)
assert HEADER_SIZE == 64
CRC_OFFSET = 56

ARCH_MINGRU = 1
Q_INT8, Q_INT4 = 1, 2
KIND_EMB, KIND_WZH, KIND_WO, KIND_W1, KIND_W2 = 1, 2, 3, 4, 5
KIND_NAMES = {KIND_EMB: "emb", KIND_WZH: "wzh", KIND_WO: "wo", KIND_W1: "w1", KIND_W2: "w2"}
META_FMT = "<iHBB"
META_SIZE = 8
TABLE_FMT = "<BBBBHHII"
TABLE_SIZE = 16
HEADER_FIELDS = ("arch", "quant_body", "quant_emb", "n_layers", "d_model", "d_ffn", "vocab",
                 "n_merges", "unk_id", "eot_id", "logit_frac")


def _pad4(buf):
    buf.extend(b"\0" * (-len(buf) % 4))


def row_bytes(cols, quant):
    return cols if quant == Q_INT8 else cols // 2


def pack_rows(q, quant):
    """Signed integer matrix (rows, cols) -> offset-binary packed uint8 (rows, row_bytes)."""
    if quant == Q_INT8:
        return (q.astype(np.int16) + 128).astype(np.uint8)
    u = (q.astype(np.int16) + 8).astype(np.uint8)
    return u[:, 0::2] | (u[:, 1::2] << 4)


def unpack_rows(packed, cols, quant):
    if quant == Q_INT8:
        return (packed.astype(np.int16) - 128).astype(np.int8)
    q = np.empty((packed.shape[0], cols), np.int8)
    q[:, 0::2] = (packed & 0x0F).astype(np.int16) - 8
    q[:, 1::2] = (packed >> 4).astype(np.int16) - 8
    return q


def write(path, P):
    """Serialise a params dict (see export.py) to path; returns the file bytes."""
    h = P["header"]
    buf = bytearray(HEADER_SIZE)

    tok_off = len(buf)
    for t in P["tokens"]:
        buf.append(len(t))
        buf.extend(t)
    _pad4(buf)
    for a, b in P["merges"]:
        buf.extend(struct.pack("<HH", a, b))
    _pad4(buf)

    lut_off = len(buf)
    buf.extend(np.asarray(P["luts"]["sig"], "<i2").tobytes())
    buf.extend(np.asarray(P["luts"]["tanh"], "<i2").tobytes())
    buf.extend(np.asarray(P["luts"]["exp"], "<u2").tobytes())

    norm_off = len(buf)
    for n in P["norms"]:
        buf.extend(struct.pack("<i", n["shift"]))
        buf.extend(np.asarray(n["gain"], "<i2").tobytes())
        _pad4(buf)

    table_off = len(buf)
    buf.extend(b"\0" * (TABLE_SIZE * len(P["tensors"])))
    weights_off = len(buf)
    for i, T in enumerate(P["tensors"]):
        rows, cols, quant = T["rows"], T["cols"], T["quant"]
        rb = row_bytes(cols, quant)
        n_meta = len(T["meta"])
        stride = n_meta * META_SIZE + rb
        assert rb % 4 == 0 and stride % 4 == 0, "rows must be 4-byte aligned"
        offset = len(buf)
        packed = pack_rows(T["q"], quant)
        for r in range(rows):
            for bacc, M, s1, sh in T["meta"]:
                buf.extend(struct.pack(META_FMT, int(bacc[r]), int(M[r]), int(s1[r]), int(sh[r])))
            buf.extend(packed[r].tobytes())
        struct.pack_into(TABLE_FMT, buf, table_off + i * TABLE_SIZE, T["kind"], T["layer"], quant, n_meta,
                         rows, cols, offset, stride)
    weights_len = len(buf) - weights_off

    struct.pack_into(HEADER_FMT, buf, 0, MAGIC, VERSION, HEADER_SIZE, h["arch"], h["quant_body"], h["quant_emb"],
                     h["n_layers"], h["d_model"], h["d_ffn"], h["vocab"], h["n_merges"], h["unk_id"],
                     h["eot_id"], h["logit_frac"], 0, tok_off, lut_off, norm_off, table_off,
                     len(P["tensors"]), 0, weights_off, weights_len, len(buf), 0, 0)
    struct.pack_into("<I", buf, CRC_OFFSET, zlib.crc32(buf))
    data = bytes(buf)
    with open(path, "wb") as f:
        f.write(data)
    return data


def read(path_or_bytes):
    """Parse a .plm file back into the params dict that write() takes."""
    data = path_or_bytes if isinstance(path_or_bytes, (bytes, bytearray)) else open(path_or_bytes, "rb").read()
    f = struct.unpack_from(HEADER_FMT, data, 0)
    (magic, version, header_size, arch, quant_body, quant_emb, n_layers, d_model, d_ffn, vocab, n_merges,
     unk_id, eot_id, logit_frac, _, tok_off, lut_off, norm_off, table_off, n_tensors, _, weights_off,
     weights_len, file_len, crc, _) = f
    if magic != MAGIC:
        raise ValueError("not a .plm file")
    if version != VERSION or header_size != HEADER_SIZE:
        raise ValueError(f"unsupported .plm version {version}")
    if file_len != len(data):
        raise ValueError("truncated .plm file")
    check = bytearray(data)
    struct.pack_into("<I", check, CRC_OFFSET, 0)
    if zlib.crc32(check) != crc:
        raise ValueError("CRC mismatch")
    header = dict(zip(HEADER_FIELDS, (arch, quant_body, quant_emb, n_layers, d_model, d_ffn, vocab, n_merges,
                                      unk_id, eot_id, logit_frac)))

    p = tok_off
    tokens = []
    for _ in range(vocab):
        n = data[p]
        tokens.append(bytes(data[p + 1:p + 1 + n]))
        p += 1 + n
    p += -p % 4
    merges = [struct.unpack_from("<HH", data, p + 4 * i) for i in range(n_merges)]

    lut = np.frombuffer(data, "<i2", 1024, lut_off)
    luts = {"sig": lut[:512].copy(), "tanh": lut[512:].copy(),
            "exp": np.frombuffer(data, "<u2", 512, lut_off + 2048).copy()}

    norms = []
    p = norm_off
    for _ in range(2 * n_layers + 1):
        shift = struct.unpack_from("<i", data, p)[0]
        gain = np.frombuffer(data, "<i2", d_model, p + 4).copy()
        norms.append({"shift": shift, "gain": gain})
        p += 4 + 2 * d_model
        p += -p % 4

    tensors = []
    for i in range(n_tensors):
        kind, layer, quant, n_meta, rows, cols, offset, stride = struct.unpack_from(TABLE_FMT, data,
                                                                                    table_off + i * TABLE_SIZE)
        rec = np.frombuffer(data, np.uint8, rows * stride, offset).reshape(rows, stride)
        meta = []
        for k in range(n_meta):
            m = rec[:, k * META_SIZE:(k + 1) * META_SIZE]
            bacc = np.ascontiguousarray(m[:, 0:4]).view("<i4")[:, 0].astype(np.int64)
            M = np.ascontiguousarray(m[:, 4:6]).view("<u2")[:, 0].astype(np.int64)
            meta.append((bacc, M, m[:, 6].astype(np.int64), m[:, 7].astype(np.int64)))
        q = unpack_rows(rec[:, n_meta * META_SIZE:], cols, quant)
        tensors.append({"kind": kind, "layer": layer, "quant": quant, "rows": rows, "cols": cols, "q": q,
                        "meta": meta, "offset": offset, "stride": stride})
    return {"header": header, "tokens": tokens, "merges": [tuple(m) for m in merges], "luts": luts,
            "norms": norms, "tensors": tensors, "file_len": file_len}


def params_equal(a, b):
    """Exact equality of two params dicts (ignores file-position fields). Returns a list of differences."""
    diffs = []
    if a["header"] != b["header"]:
        diffs.append("header")
    if list(a["tokens"]) != list(b["tokens"]):
        diffs.append("tokens")
    if [tuple(m) for m in a["merges"]] != [tuple(m) for m in b["merges"]]:
        diffs.append("merges")
    for k in ("sig", "tanh", "exp"):
        if not np.array_equal(np.asarray(a["luts"][k]), np.asarray(b["luts"][k])):
            diffs.append(f"lut {k}")
    for i, (x, y) in enumerate(zip(a["norms"], b["norms"])):
        if x["shift"] != y["shift"] or not np.array_equal(x["gain"], y["gain"]):
            diffs.append(f"norm {i}")
    if len(a["tensors"]) != len(b["tensors"]):
        diffs.append("tensor count")
    for x, y in zip(a["tensors"], b["tensors"]):
        name = f"{KIND_NAMES[x['kind']]}[{x['layer']}]"
        for k in ("kind", "layer", "quant", "rows", "cols"):
            if x[k] != y[k]:
                diffs.append(f"{name}.{k}")
        if not np.array_equal(x["q"], y["q"]):
            diffs.append(f"{name}.q")
        for s, (ma, mb) in enumerate(zip(x["meta"], y["meta"])):
            if not all(np.array_equal(np.asarray(u, np.int64), np.asarray(v, np.int64)) for u, v in zip(ma, mb)):
                diffs.append(f"{name}.meta{s}")
    return diffs
