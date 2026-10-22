"""Corpus preparation: fetch and normalise text, train the tokenizer, write token arrays.

Run from projects/picolm:
    python -m train.data --source tinystories-valid --out data/tinystories
    python -m train.data --source tinystories-train --max-mb 200 --out data/ts200
    python -m train.data --source path/to/my_text --out data/mine --tokenizer data/tinystories/tokenizer.json

--source is a built-in name (see SOURCES) or a local file/directory (searched
recursively). Local formats:

  *.txt  prose. Documents are separated by a line '<|endoftext|>'; a file
         without separators is one document.
  *.dlg  dialogue. Conversations are separated by blank lines. 'U:' lines are
         the user, 'C:' lines the character, anything else is narration:
             U: hello?
             C: oh! um... hi.
             *she looks at her shoes*

Every document or conversation starts with the EOT token, which is also what
the device feeds before a prompt. Output: tokenizer.json, train.bin, val.bin
(uint8 token ids) and meta.json in --out.
"""

import argparse
import json
import os
import re
import unicodedata
import urllib.request

import numpy as np

from train.tokenizer import CHAR, EOT, USER, Tokenizer

SOURCES = {
    "tinystories-valid": "https://huggingface.co/datasets/roneneldan/TinyStories/resolve/main/TinyStories-valid.txt",
    "tinystories-train": "https://huggingface.co/datasets/roneneldan/TinyStories/resolve/main/TinyStoriesV2-GPT4-train.txt",
}
DOC_SEP = "<|endoftext|>"

_TRANSLATE = str.maketrans({
    "“": '"', "”": '"', "„": '"', "‘": "'", "’": "'", "´": "'",
    "–": "-", "—": "-", "…": "...", " ": " ", " ": " ", " ": " ",
    "​": "", "­": "", "\t": " ", "\r": "",
})
# Control characters other than newline never appear in normalised text; the
# special tokens are inserted from document structure only.
_DROP_CONTROL = {c: None for c in range(32) if c != 10}


def normalize(text):
    """Map text onto printable ASCII + newline (curly quotes, dashes, accents...)."""
    text = unicodedata.normalize("NFKD", text.translate(_TRANSLATE))
    text = text.encode("ascii", "ignore").decode("ascii")
    return text.translate(_DROP_CONTROL)


def prose_docs(text):
    parts = text.split(DOC_SEP) if DOC_SEP in text else [text]
    return [p.strip() + "\n" for p in parts if p.strip()]


def dialogue_docs(text):
    docs = []
    for conv in re.split(r"\n\s*\n", text):
        lines = []
        for line in conv.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            if line[:2].upper() == "U:":
                lines.append(USER + line[2:].strip() + "\n")
            elif line[:2].upper() == "C:":
                lines.append(CHAR + line[2:].strip() + "\n")
            else:
                lines.append(line + "\n")
        if lines:
            docs.append("".join(lines))
    return docs


def download(url, dest, max_bytes=None):
    """Stream url to dest, optionally stopping after max_bytes at a document boundary."""
    if os.path.exists(dest):
        return dest
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    got = 0
    with urllib.request.urlopen(url) as r, open(tmp, "wb") as f:
        while True:
            buf = r.read(1 << 20)
            if not buf:
                break
            f.write(buf)
            got += len(buf)
            print(f"\rdownloading {os.path.basename(dest)}: {got / 1e6:.0f} MB", end="", flush=True)
            if max_bytes and got >= max_bytes:
                break
    print()
    if max_bytes and got >= max_bytes:
        with open(tmp, "rb") as f:
            data = f.read()
        cut = data.rfind(DOC_SEP.encode())
        if cut > 0:
            with open(tmp, "wb") as f:
                f.write(data[:cut])
    os.replace(tmp, dest)
    return dest


def load_docs(source, raw_dir, max_mb=None):
    if source in SOURCES:
        suffix = f"-{max_mb}mb" if max_mb else ""
        path = download(SOURCES[source], os.path.join(raw_dir, source + suffix + ".txt"),
                        int(max_mb * 1e6) if max_mb else None)
        files = [path]
    elif os.path.isdir(source):
        files = sorted(os.path.join(dp, fn) for dp, _, fns in os.walk(source) for fn in fns
                       if fn.endswith((".txt", ".dlg")))
    elif os.path.isfile(source):
        files = [source]
    else:
        raise SystemExit(f"unknown source {source!r}: use one of {sorted(SOURCES)} or a path")
    docs = []
    for path in files:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = normalize(f.read())
        docs.extend(dialogue_docs(text) if path.endswith(".dlg") else prose_docs(text))
    if not docs:
        raise SystemExit("no documents found")
    return docs


def encode_stream(tok, docs):
    text = "".join(EOT + d for d in docs)
    if not tok.merges:
        # Character-level fast path: drop out-of-vocab chars, then a table lookup.
        lut = np.full(256, 255, np.uint8)
        for ch, i in tok.char_id.items():
            lut[ord(ch)] = i
        ids = lut[np.frombuffer(text.encode("ascii"), np.uint8)]
        return ids[ids != 255], len(text)
    return np.array(tok.encode(text, drop_unknown=True), np.uint8), len(text)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default="tinystories-valid")
    ap.add_argument("--out", default="data/tinystories")
    ap.add_argument("--raw-dir", default="data/raw", help="download cache")
    ap.add_argument("--max-mb", type=float, default=None, help="only fetch this many MB of a large source")
    ap.add_argument("--tokenizer", default=None, help="reuse an existing tokenizer.json instead of training one")
    ap.add_argument("--vocab", type=int, default=96, help="max vocab size incl. specials and merges")
    ap.add_argument("--merges", type=int, default=0, help="BPE merges to learn (0 = character-level)")
    ap.add_argument("--val-frac", type=float, default=0.02)
    args = ap.parse_args()

    docs = load_docs(args.source, args.raw_dir, args.max_mb)
    n_val = max(1, int(len(docs) * args.val_frac))
    train_docs, val_docs = docs[:-n_val], docs[-n_val:]
    if args.tokenizer:
        tok = Tokenizer.load(args.tokenizer)
    else:
        tok = Tokenizer.train("".join(train_docs), max_vocab=args.vocab, n_merges=args.merges)
    if tok.vocab_size > 255:
        raise SystemExit("vocab must stay below 256 so token arrays fit in uint8")

    os.makedirs(args.out, exist_ok=True)
    tok.save(os.path.join(args.out, "tokenizer.json"))
    meta = {"source": args.source, "vocab": tok.vocab_size, "merges": len(tok.merges),
            "docs_train": len(train_docs), "docs_val": len(val_docs)}
    for name, part in (("train", train_docs), ("val", val_docs)):
        ids, n_chars = encode_stream(tok, part)
        ids.tofile(os.path.join(args.out, name + ".bin"))
        meta[f"tokens_{name}"] = int(len(ids))
        meta[f"chars_per_token_{name}"] = round(n_chars / max(1, len(ids)), 4)
    with open(os.path.join(args.out, "meta.json"), "w") as f:
        json.dump(meta, f, indent=1)
    print(json.dumps(meta, indent=1))
    print("vocab:", repr("".join(tok.tokens[3:3 + len(tok.chars)])))


if __name__ == "__main__":
    main()
