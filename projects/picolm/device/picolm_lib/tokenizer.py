"""Device-side tokenizer: decode table plus the chunk-and-merge encoder.

Mirrors train/tokenizer.py exactly (same chunk rules, same merge order), so a
prompt encodes to the same ids on the PC and on the PicoCalc. Encoding runs
once per prompt and may allocate; decoding is a table lookup.
"""

EOT = "\x03"
USER = "\x01"
CHAR = "\x02"
SPECIALS = (EOT, USER, CHAR)


def _cls(ch):
    if ch.isalpha() or ch.isdigit() or ch == "'":
        return 0
    if ch == " ":
        return 1
    if ch == "\n":
        return 2
    return 3


def chunks(text):
    out = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in SPECIALS:
            out.append(ch)
            i += 1
            continue
        start = i
        if ch == " " and i + 1 < n and text[i + 1] not in SPECIALS and _cls(text[i + 1]) in (0, 3):
            i += 1
        c = _cls(text[i])
        i += 1
        while i < n and text[i] not in SPECIALS and _cls(text[i]) == c:
            i += 1
        out.append(text[start:i])
    return out


class Tokenizer:
    def __init__(self, tokens, n_merges, merges, unk_id):
        self.tokens = tokens                       # id -> str
        n_base = len(tokens) - n_merges
        self.char_id = {tokens[i]: i for i in range(n_base)}
        self.merges = merges                       # rank -> (a, b)
        self.rank = {m: r for r, m in enumerate(merges)}
        self.n_base = n_base
        self.unk_id = unk_id

    def _encode_chunk(self, chunk, out):
        if chunk in SPECIALS:
            out.append(self.char_id[chunk])
            return
        ids = [self.char_id.get(c, self.unk_id) for c in chunk]
        while len(ids) > 1 and self.merges:
            best_rank, best_i = -1, -1
            for i in range(len(ids) - 1):
                r = self.rank.get((ids[i], ids[i + 1]), -1)
                if r >= 0 and (best_rank < 0 or r < best_rank):
                    best_rank, best_i = r, i
            if best_rank < 0:
                break
            a, b = self.merges[best_rank]
            merged = []
            i = 0
            while i < len(ids):
                if i + 1 < len(ids) and ids[i] == a and ids[i + 1] == b:
                    merged.append(self.n_base + best_rank)
                    i += 2
                else:
                    merged.append(ids[i])
                    i += 1
            ids = merged
        out.extend(ids)

    def encode(self, text):
        out = []
        for ch in chunks(text):
            self._encode_chunk(ch, out)
        return out

    def decode(self, ids):
        return "".join(self.tokens[i] for i in ids)
