#!/usr/bin/env python3
"""Build the Infernis logo files.

The wordmark is Geist Medium, outlined, with both i's turned into candles: the
stem is the plain dotless i and the tittle is a red flame. The mark is that
same candle on its own, so the trailer can zoom out from it into the wordmark.

    pip install fonttools brotli
    python3 make_brand.py

Writes the SVGs next to this script.
"""
from pathlib import Path

from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

HERE = Path(__file__).resolve().parent
FONT = HERE.parent / "fonts" / "Geist.woff2"

WORD = "infernis"
WEIGHT = 500
TRACKING = -40  # font units (1000/em)

INK = "#0b0a0a"    # background
PAPER = "#f4f0ec"  # letters on dark
RED = "#e11d2e"    # the flame

# Flame proportions, relative to the i's stem width.
FLAME_W = 1.12
FLAME_H = 2.50 * FLAME_W
FLAME_GAP = 0.50   # space between stem top and flame base
FLAME_LEAN = 0.24  # tip offset to the right, as a share of flame width


def flame_path(cx, bottom, w, h, lean=FLAME_LEAN):
    """Candle flame: round base of width w sitting on `bottom`, tip h above it,
    leaning right with a slight flick."""
    r = w / 2
    cy = bottom - r
    top = bottom - h
    tx = cx + lean * w
    return (
        f"M{tx:.1f},{top:.1f}"
        f"C{tx - 0.06 * w:.1f},{top + 0.36 * h:.1f} {cx + r:.1f},{cy - 0.22 * h:.1f} {cx + r:.1f},{cy:.1f}"
        f"A{r:.1f},{r:.1f} 0 0 1 {cx - r:.1f},{cy:.1f}"
        f"C{cx - r:.1f},{cy - 0.25 * h:.1f} {tx - 0.35 * w:.1f},{top + 0.40 * h:.1f} {tx:.1f},{top:.1f}Z"
    )


def build():
    font = instantiateVariableFont(TTFont(FONT), {"wght": WEIGHT})
    glyphs = font.getGlyphSet()
    cmap = font.getBestCmap()
    xh = font["OS/2"].sxHeight

    letters, candles = [], []
    x = 0
    for ch in WORD:
        name = "dotlessi" if ch == "i" else cmap[ord(ch)]
        g = glyphs[name]
        pen = SVGPathPen(glyphs)
        g.draw(TransformPen(pen, (1, 0, 0, -1, x, 0)))  # flip y: baseline at 0
        if ch == "i":
            bp = BoundsPen(glyphs)
            g.draw(bp)
            x0, _, x1, _ = bp.bounds
            sw = x1 - x0
            fw, fh = sw * FLAME_W, sw * FLAME_H
            base = -xh - sw * FLAME_GAP
            candles.append({
                "stem": (x + x0, -xh, sw, xh),
                "flame": flame_path(x + (x0 + x1) / 2, base, fw, fh),
                "top": base - fh,
            })
        else:
            letters.append(pen.getCommands())
        x += g.width + TRACKING
    width = x - TRACKING
    return letters, candles, width, xh


def rect(x, y, w, h, fill, extra=""):
    return f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" fill="{fill}"{extra}/>'


def wordmark_svg(letters, candles, width, ink, flame, pad=60):
    top = min(c["top"] for c in candles) - pad
    vb = f"{-pad} {top:.0f} {width + 2 * pad:.0f} {-top + pad:.0f}"
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb}" role="img" aria-label="infernis">']
    out.append(f'<g fill="{ink}">' + "".join(f'<path d="{d}"/>' for d in letters) + "</g>")
    for i, c in enumerate(candles, 1):
        out.append(rect(*c["stem"], ink, f' id="i{i}-stem"'))
        out.append(f'<path id="i{i}-flame" d="{c["flame"]}" fill="{flame}"/>')
    out.append("</svg>")
    return "".join(out)


def candle_svg(candle, ink, flame, stem_h=None, pad=40):
    """The mark: one candle. stem_h shortens the stem for small sizes."""
    sx, sy, sw, sh = candle["stem"]
    if stem_h:
        sh = stem_h
    top = candle["top"]
    flame_d = candle["flame"]
    w = sw * FLAME_W * 1.2
    cx = sx + sw / 2
    x0 = cx - w / 2 - pad
    y0 = top - pad
    h = (sy + sh) - top + 2 * pad
    vb = f"{x0:.0f} {y0:.0f} {w + 2 * pad:.0f} {h:.0f}"
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb}" role="img" aria-label="infernis">'
            + rect(sx, sy, sw, sh, ink)
            + f'<path d="{flame_d}" fill="{flame}"/></svg>')


def icon_svg(candle, size=1024):
    """App icon: a short candle on a dark rounded square."""
    sx, sy, sw, _ = candle["stem"]
    sh = sw * 2.9
    top = candle["top"]
    total_h = (sy + sh) - top
    scale = size * 0.70 / total_h
    cx = sx + sw / 2
    tx = size / 2 - cx * scale
    ty = size / 2 - (top + total_h / 2) * scale
    r = size * 0.225
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" role="img" aria-label="infernis">'
            f'<rect width="{size}" height="{size}" rx="{r:.0f}" fill="{INK}"/>'
            f'<g transform="translate({tx:.1f} {ty:.1f}) scale({scale:.4f})">'
            + rect(sx, sy, sw, sh, PAPER)
            + f'<path d="{candle["flame"]}" fill="{RED}"/></g></svg>')


def main():
    letters, candles, width, _ = build()
    first = candles[0]
    files = {
        "infernis-wordmark.svg": wordmark_svg(letters, candles, width, PAPER, RED),
        "infernis-wordmark-light-bg.svg": wordmark_svg(letters, candles, width, INK, RED),
        "infernis-wordmark-mono.svg": wordmark_svg(letters, candles, width, "currentColor", "currentColor"),
        "infernis-mark.svg": candle_svg(first, PAPER, RED),
        "infernis-mark-light-bg.svg": candle_svg(first, INK, RED),
        "infernis-mark-small.svg": candle_svg(first, PAPER, RED, stem_h=first["stem"][2] * 2.9),
        "infernis-icon.svg": icon_svg(first),
    }
    for name, svg in files.items():
        (HERE / name).write_text(svg + "\n")
        print("wrote", name)


if __name__ == "__main__":
    main()
