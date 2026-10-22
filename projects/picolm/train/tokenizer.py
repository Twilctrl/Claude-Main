"""PicoLM tokenizer: a character vocabulary with optional BPE merges.

Every token is a short byte string, so the device only needs a token -> bytes
table to decode and the (char table, merge list) pair to encode a prompt.

Token ids:
    0, 1, 2      special control tokens (EOT, USER, CHAR), see SPECIALS
    3 .. 3+C-1   single characters, sorted by code point
    3+C ..       merged tokens, in merge-rank order (merge i -> id 3+C+i)

Encoding is "chunk, then merge": text is split into chunks with a rule simple
enough to re-implement on the device without regex (see chunks()), and BPE
merges are applied inside each chunk in rank order. With zero merges this is
plain character-level tokenisation.
"""

import json
from collections import Counter

EOT = "\x03"   # end of a story / conversation; also fed before a prompt
USER = "\x01"  # the player / user speaking (dialogue data)
CHAR = "\x02"  # the character speaking (dialogue data)
SPECIALS = (EOT, USER, CHAR)


def _cls(ch):
    """Character class used by chunks(): 0 word, 1 space, 2 newline, 3 other."""
    if ch.isalpha() or ch.isdigit() or ch == "'":
        return 0
    if ch == " ":
        return 1
    if ch == "\n":
        return 2
    return 3


def chunks(text):
    """Split text into merge chunks.

    Rules (mirrored by device/tokenizer.py):
    - a special token is always its own chunk;
    - a single space joins the run that follows it (" cat", " !");
    - otherwise a chunk is a maximal run of one class (word chars, spaces,
      newlines, other punctuation).
    """
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
    def __init__(self, chars, merges=(), unk=" "):
        self.chars = list(chars)
        self.merges = [tuple(m) for m in merges]
        self.tokens = list(SPECIALS) + self.chars
        for a, b in self.merges:
            self.tokens.append(self.tokens[a] + self.tokens[b])
        self.char_id = {c: i for i, c in enumerate(self.tokens[: len(SPECIALS) + len(self.chars)])}
        self.rank = {m: i for i, m in enumerate(self.merges)}
        self.unk_id = self.char_id.get(unk, self.char_id.get("?", len(SPECIALS)))
        self.eot_id = 0
        self._cache = {}

    @property
    def vocab_size(self):
        return len(self.tokens)

    # -- training ---------------------------------------------------------
    @classmethod
    def train(cls, text, max_vocab=96, n_merges=0, min_count=1, sample_chars=8_000_000):
        """Pick the character vocabulary from the corpus, then learn merges.

        max_vocab counts specials + chars + merges. Characters beyond the cap
        (the rarest ones) are dropped from the corpus by encode().
        """
        counts = Counter(text)
        for s in SPECIALS:
            counts.pop(s, None)
        n_char_slots = max_vocab - len(SPECIALS) - n_merges
        if n_char_slots <= 0:
            raise ValueError("max_vocab too small for the requested merges")
        keep = [c for c, k in counts.most_common() if k >= min_count][:n_char_slots]
        tok = cls(sorted(keep))
        if n_merges:
            tok = cls(tok.chars, tok._learn_merges(text[:sample_chars], n_merges))
        return tok

    def _learn_merges(self, text, n_merges):
        words = Counter(chunks(text))
        seqs = {}
        for w in words:
            ids = [self.char_id[c] for c in w if c in self.char_id]
            if len(ids) > 1:
                seqs[w] = ids
        merges = []
        next_id = len(self.tokens)
        for _ in range(n_merges):
            pairs = Counter()
            for w, ids in seqs.items():
                f = words[w]
                for a, b in zip(ids, ids[1:]):
                    pairs[(a, b)] += f
            if not pairs:
                break
            best = max(pairs.items(), key=lambda kv: (kv[1], -kv[0][0], -kv[0][1]))[0]
            merges.append(best)
            for w, ids in seqs.items():
                seqs[w] = _merge_pair(ids, best, next_id)
            next_id += 1
        return merges

    # -- encode / decode --------------------------------------------------
    def _encode_chunk(self, chunk):
        if chunk in SPECIALS:
            return [self.char_id[chunk]]
        cached = self._cache.get(chunk)
        if cached is not None:
            return cached
        ids = [self.char_id.get(c, self.unk_id) for c in chunk]
        base = len(SPECIALS) + len(self.chars)
        while len(ids) > 1 and self.merges:
            best_rank, best_i = None, -1
            for i in range(len(ids) - 1):
                r = self.rank.get((ids[i], ids[i + 1]))
                if r is not None and (best_rank is None or r < best_rank):
                    best_rank, best_i = r, i
            if best_rank is None:
                break
            ids = _merge_pair(ids, self.merges[best_rank], base + best_rank)
        if len(self._cache) < 200_000:
            self._cache[chunk] = ids
        return ids

    def encode(self, text, drop_unknown=False):
        if drop_unknown:
            text = "".join(c for c in text if c in self.char_id)
        out = []
        for ch in chunks(text):
            out.extend(self._encode_chunk(ch))
        return out

    def decode(self, ids):
        return "".join(self.tokens[i] for i in ids)

    # -- persistence ------------------------------------------------------
    def to_dict(self):
        return {"version": 1, "chars": "".join(self.chars), "merges": [list(m) for m in self.merges],
                "unk": self.tokens[self.unk_id]}

    @classmethod
    def from_dict(cls, d):
        return cls(d["chars"], d.get("merges", []), d.get("unk", " "))

    def save(self, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=1)

    @classmethod
    def load(cls, path):
        with open(path, encoding="utf-8") as f:
            return cls.from_dict(json.load(f))


def _merge_pair(ids, pair, new_id):
    out = []
    i = 0
    a, b = pair
    while i < len(ids):
        if i + 1 < len(ids) and ids[i] == a and ids[i + 1] == b:
            out.append(new_id)
            i += 2
        else:
            out.append(ids[i])
            i += 1
    return out
