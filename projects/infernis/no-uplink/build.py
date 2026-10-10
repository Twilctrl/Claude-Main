#!/usr/bin/env python3
"""Inline fonts, the wordmark and the soundtrack into one HTML file.

    python3 build.py                     # -> infernis-no-uplink.html
    python3 build.py --fragment out.html # also write a copy without <html>/<head>/<body>

The soundtrack is read from build/soundtrack.mp3 (made by soundtrack.py) when it
exists; without it the edit still plays, silently.
"""
import argparse
import base64
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent  # projects/infernis

FONTS = {"Geist": ROOT / "fonts" / "Geist.woff2", "GeistMono": ROOT / "fonts" / "GeistMono.woff2"}
WORDMARK = ROOT / "brand" / "infernis-wordmark.svg"
AUDIO = HERE / "build" / "soundtrack.mp3"
MIME = {".mp3": "audio/mpeg", ".woff2": "font/woff2"}


def data_uri(path):
    return f"data:{MIME[path.suffix]};base64,{base64.b64encode(path.read_bytes()).decode()}"


def build():
    src = (HERE / "no-uplink.src.html").read_text()
    faces = "".join(
        f"@font-face{{font-family:{name};src:url({data_uri(p)}) format('woff2');font-weight:100 900;font-display:block}}"
        for name, p in FONTS.items()
    )
    assets = {"audio": data_uri(AUDIO) if AUDIO.exists() else None}
    head = f"<style>{faces}</style>\n<script>const ASSETS = {json.dumps(assets)};</script>"
    return src.replace("<!--@ASSETS@-->", head).replace("<!--@WORDMARK@-->", WORDMARK.read_text().strip())


def fragment(html):
    """The Artifact publisher wraps pages in its own document, so drop ours."""
    html = re.sub(r"<!doctype html>\s*", "", html, flags=re.I)
    html = re.sub(r"</?html[^>]*>|</?head>|</?body>", "", html)
    return re.sub(r'<meta (charset|name="viewport")[^>]*>\s*', "", html)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fragment", type=Path)
    args = ap.parse_args()
    html = build()
    out = HERE / "infernis-no-uplink.html"
    out.write_text(html)
    print(f"wrote {out.name} ({len(html) / 1e6:.2f} MB)")
    if args.fragment:
        args.fragment.write_text(fragment(html))
        print(f"wrote {args.fragment}")


if __name__ == "__main__":
    main()
