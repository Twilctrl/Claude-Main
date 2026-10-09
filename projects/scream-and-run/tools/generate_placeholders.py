#!/usr/bin/env python3
"""Generate the placeholder art for Scream and Run.

Every texture the mod loads is drawn here with Pillow, so the mod builds and
runs without any hand-made art. To use real art, overwrite the PNG in
ScreamAndRun/Assets/Textures/ with the same name and frame layout (see
ScreamAndRun/Assets/README.md). Re-running this script overwrites ALL
textures, so don't run it after you've dropped in real art.

Usage:  python3 tools/generate_placeholders.py
"""
import math
import os
import random

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ScreamAndRun")
TEX = os.path.join(ROOT, "Assets", "Textures")

HAIR = (18, 14, 18, 255)           # black
HAIR_HI = (70, 64, 72, 255)
SKIN = (238, 224, 214, 255)
SKIN_SHADE = (205, 186, 178, 255)
EYE = (225, 18, 40, 255)           # red
WHITE = (236, 236, 244, 255)
NAVY = (24, 22, 26, 255)           # black uniform
NAVY_DARK = (10, 8, 12, 255)
RIBBON = (205, 14, 38, 255)        # red
SHOE = (24, 18, 18, 255)
BLADE = (200, 206, 218, 255)
HANDLE = (70, 40, 30, 255)
BLOOD = (120, 0, 10, 255)


def save(img, *parts):
    path = os.path.join(TEX, *parts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path)
    print("wrote", os.path.relpath(path, ROOT))


def rect(d, x0, y0, x1, y1, c):
    d.rectangle([x0, y0, x1, y1], fill=c)


# ---------------------------------------------------------------- Onryo
# 6 frames, 40x56 each. Drawn facing right, then mirrored: Terraria expects NPC
# sprites to face LEFT and flips them itself when they walk right.
#   0 idle, 1-4 walk cycle, 5 "telegraph" (knife raised, wide eyes, smile).
FRAME_W, FRAME_H, FRAMES = 40, 56, 6

OUTLINE = (8, 5, 8, 255)
HAIR_MID = (44, 40, 46, 255)
SKIN_BLUSH = (255, 150, 180, 255)
EYE_DARK = (110, 0, 16, 255)
SCLERA = (250, 246, 246, 255)
BLOUSE_SHADE = (196, 198, 214, 255)
NAVY_HI = (64, 60, 68, 255)
SOCK = (16, 14, 22, 255)
SOCK_HI = (40, 36, 52, 255)
LOAFER = (26, 22, 24, 255)
LOAFER_HI = (76, 70, 76, 255)
BLADE_HI = (246, 248, 255, 255)
BLOOD_HI = (170, 10, 24, 255)


def px(d, x, y, c):
    d.point((x, y), fill=c)


def draw_onryo(d, oy, frame):
    tele = frame == 5
    # Walk cycle: (back leg x-offset, front leg x-offset, back leg lift)
    back_dx, front_dx, lift = {0: (0, 0, 0), 1: (-2, 2, 1), 2: (0, 0, 0), 3: (2, -2, 1), 4: (0, 0, 0), 5: (-1, 1, 0)}[frame]
    bob = 1 if frame in (2, 4) else 0   # slight body bob on passing frames
    y = oy + bob

    # --- long back hair (behind everything)
    rect(d, 10, y + 6, 21, y + 38, HAIR)
    rect(d, 9, y + 14, 11, y + 41, HAIR)
    for sx in (11, 14, 17, 20):          # strand highlights
        rect(d, sx, y + 12, sx, y + 34 - (sx % 3) * 3, HAIR_MID)
    for tx in range(9, 22, 2):           # ragged tips
        px(d, tx, y + 39 + (tx % 3), HAIR)

    # --- far arm: hangs at her side behind the torso (shaded, it's further away),
    # swinging opposite to the knife arm while she walks
    back_swing = {1: -1, 3: 1}.get(frame, 0)
    rect(d, 11, y + 21, 12, y + 28 + back_swing, BLOUSE_SHADE)          # sleeve
    rect(d, 13, y + 22, 13, y + 28, (120, 120, 138, 255))               # shadow fold between arm and body
    rect(d, 11, y + 27 + back_swing, 12, y + 28 + back_swing, NAVY)     # cuff
    rect(d, 11, y + 29 + back_swing, 12, y + 30 + back_swing, SKIN_SHADE)   # hand

    # --- legs: a sliver of skin under the hem, black thigh-highs tapering thigh > knee > ankle,
    # shaped loafers pointing forward. The far leg is a shade darker for depth.
    def leg(lx, lifted, front):
        sock = SOCK if front else (8, 6, 10, 255)
        skin = SKIN if front else SKIN_SHADE
        top = y + 41
        bottom = y + 50 - lifted
        rect(d, lx, top, lx + 2, top, skin)                         # zettai ryouiki
        rect(d, lx, top + 1, lx + 2, top + 5 - lifted, sock)       # thigh, 3 wide
        rect(d, lx + 1, top + 6 - lifted, lx + 2, bottom, sock)    # calf and ankle, 2 wide
        px(d, lx, top + 6 - lifted, sock)                          # knee
        px(d, lx + 2, top + 2, SOCK_HI)                            # shine on the thigh
        rect(d, lx, bottom + 1, lx + 3, bottom + 1, LOAFER)        # loafer: upper
        rect(d, lx, bottom + 2, lx + 4, bottom + 2, LOAFER)        #          toe pointing forward
        rect(d, lx, bottom + 3, lx + 4, bottom + 3, OUTLINE)       #          sole
        px(d, lx + 3, bottom + 1, LOAFER_HI)
    leg(16 + back_dx, lift, False)
    leg(21 + front_dx, 0, True)

    # --- pleated A-line skirt: narrow at the waist, flaring to a zigzag pleated hem
    skirt_rows = {32: (16, 25), 33: (16, 25), 34: (15, 26), 35: (15, 26), 36: (14, 27),
                  37: (14, 27), 38: (13, 28), 39: (13, 28), 40: (12, 29)}
    for row, (x0, x1) in skirt_rows.items():
        rect(d, x0, y + row, x1, y + row, NAVY)
    for x in range(12, 30, 2):                                  # pleat points along the hem
        px(d, x, y + 41, NAVY)
    for k in (-3, -1, 1, 3):                                    # pleats fanning out from the waist
        x_top = 20.5 + k * 1.5
        x_bottom = 20.5 + k * 2.6
        d.line([(x_top, y + 33), (x_bottom, y + 40)], fill=NAVY_DARK)
        px(d, int(x_top) + 1, y + 33, NAVY_HI)
    rect(d, 13, y + 39, 28, y + 39, WHITE)                      # trim stripe near the hem

    # --- sailor blouse
    # shoulders and chest full width, then tapering in to a narrow waist
    blouse_rows = {**{r: (14, 27) for r in range(20, 27)}, 27: (15, 26), 28: (15, 26),
                   29: (16, 25), 30: (16, 25), 31: (16, 25), 32: (16, 25)}
    for row, (x0, x1) in blouse_rows.items():
        rect(d, x0, y + row, x1, y + row, BLOUSE_SHADE if row >= 29 else WHITE)   # shading under the chest
    rect(d, 12, y + 20, 18, y + 25, NAVY)                 # collar (back flap)
    rect(d, 12, y + 24, 18, y + 24, WHITE)                # collar stripe
    rect(d, 19, y + 21, 25, y + 22, NAVY)                 # front collar
    rect(d, 20, y + 23, 24, y + 25, RIBBON)               # neckerchief knot
    rect(d, 21, y + 26, 23, y + 29, RIBBON)
    px(d, 22, y + 30, RIBBON)
    px(d, 21, y + 23, BLOOD_HI)
    for bx, by in ((17, 27), (24, 30), (17, 31)):        # blood flecks
        px(d, bx, by, BLOOD)

    # --- head: anime taper. Full cranium, then the jaw narrows evenly on both sides to a small
    # pointed chin centred under the eyes, like the jumpscare portrait.
    face_rows = {7: (16, 27), 8: (16, 27), 9: (16, 27), 10: (16, 27), 11: (16, 27), 12: (16, 27),
                 13: (16, 27), 14: (16, 27), 15: (16, 27), 16: (17, 26), 17: (18, 25), 18: (19, 24),
                 19: (20, 23), 20: (21, 22)}
    for row, (x0, x1) in face_rows.items():
        rect(d, x0, y + row, x1, y + row, SKIN)
    rect(d, 20, y + 21, 23, y + 21, SKIN_SHADE)             # short neck under the chin
    rect(d, 15, y + 3, 28, y + 8, HAIR)                    # hime-cut bangs, straight across
    for bx in (17, 20, 23, 26):
        px(d, bx, y + 9, HAIR)                             # bang points
    rect(d, 20, y + 4, 25, y + 4, HAIR_MID)                # shine
    rect(d, 14, y + 4, 16, y + 21, HAIR)                   # side lock, straight down to the collar
    rect(d, 15, y + 8, 15, y + 19, HAIR_MID)
    # big red bow at the back of the head
    rect(d, 9, y + 3, 12, y + 6, RIBBON)
    rect(d, 9, y + 9, 12, y + 12, RIBBON)
    rect(d, 12, y + 6, 14, y + 9, EYE_DARK)
    rect(d, 10, y + 12, 11, y + 16, RIBBON)                # tails

    # eyes: anime style, both 2px wide and mirrored around the centre of the face.
    # Lash line on top (extending one pixel outward), red iris dark at the top, white highlight.
    for ex, outward in ((18, -1), (24, 1)):
        if tele:
            # heart eyes
            rect(d, ex, y + 11, ex + 1, y + 12, EYE)
            px(d, ex if outward < 0 else ex + 1, y + 13, EYE)
        else:
            lash_x0 = ex - 1 if outward < 0 else ex
            rect(d, lash_x0, y + 10, lash_x0 + 2, y + 10, OUTLINE)
            rect(d, ex, y + 11, ex + 1, y + 11, EYE_DARK)
            rect(d, ex, y + 12, ex + 1, y + 13, EYE)
            px(d, ex, y + 11, SCLERA)                      # highlight
    # blush + a heart-print bandaid on the cheek
    px(d, 18, y + 15, SKIN_BLUSH)
    px(d, 25, y + 15, SKIN_BLUSH)
    rect(d, 25, y + 14, 27, y + 14, (246, 214, 180, 255))
    px(d, 26, y + 14, RIBBON)
    if tele:
        rect(d, 19, y + 17, 24, y + 17, BLOOD)             # too-wide grin, corners up
        px(d, 18, y + 16, BLOOD)
        px(d, 25, y + 16, BLOOD)
        px(d, 21, y + 18, SCLERA)                          # fang
    else:
        rect(d, 21, y + 17, 22, y + 17, (150, 40, 56, 255))   # small smile with a fang, centred
        px(d, 21, y + 18, SCLERA)

    # --- arm + knife
    if tele:
        rect(d, 25, y + 12, 27, y + 22, WHITE)             # arm raised
        rect(d, 25, y + 12, 27, y + 13, NAVY)              # cuff
        rect(d, 26, y + 9, 27, y + 11, SKIN)
        rect(d, 26, y + 7, 27, y + 8, HANDLE)
        rect(d, 26, oy + 0, 27, y + 6, BLADE)
        px(d, 27, oy + 1, BLADE_HI)
        px(d, 26, y + 6, BLOOD)
    else:
        swing = {1: 1, 3: -1}.get(frame, 0)
        rect(d, 25, y + 21, 27, y + 28 + swing, WHITE)
        rect(d, 25, y + 27 + swing, 27, y + 28 + swing, NAVY)   # cuff
        rect(d, 26, y + 29 + swing, 28, y + 31 + swing, SKIN)
        rect(d, 28, y + 30 + swing, 30, y + 31 + swing, HANDLE)
        rect(d, 31, y + 30 + swing, 36, y + 31 + swing, BLADE)
        rect(d, 32, y + 30 + swing, 35, y + 30 + swing, BLADE_HI)
        px(d, 36, y + 30 + swing, BLADE_HI)
        rect(d, 33, y + 32 + swing, 33, y + 34 + swing, BLOOD)   # drip
        px(d, 35, y + 32 + swing, BLOOD_HI)


def add_outline(img, color):
    """1px dark outline around everything opaque, so she reads against dark backgrounds."""
    src = img.copy()
    sp, dp = src.load(), img.load()
    w, h = img.size
    for yy in range(h):
        for xx in range(w):
            if sp[xx, yy][3] != 0:
                continue
            for nx, ny in ((xx - 1, yy), (xx + 1, yy), (xx, yy - 1), (xx, yy + 1)):
                # stay inside the same frame so outlines don't bleed between frames
                if 0 <= nx < w and 0 <= ny < h and ny // FRAME_H == yy // FRAME_H and sp[nx, ny][3] != 0:
                    dp[xx, yy] = color
                    break


def onryo():
    img = Image.new("RGBA", (FRAME_W, FRAME_H * FRAMES), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for f in range(FRAMES):
        draw_onryo(d, f * FRAME_H, f)
    add_outline(img, OUTLINE)
    save(img.transpose(Image.FLIP_LEFT_RIGHT), "NPCs", "Onryo.png")


# ---------------------------------------------------------------- items
def love_letter():
    img = Image.new("RGBA", (28, 20), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    rect(d, 0, 2, 27, 19, (110, 60, 80, 255))
    rect(d, 1, 3, 26, 18, (250, 205, 220, 255))
    d.line([(1, 3), (13, 12), (26, 3)], fill=(200, 140, 165, 255))
    # heart seal
    rect(d, 11, 10, 12, 11, RIBBON)
    rect(d, 14, 10, 15, 11, RIBBON)
    rect(d, 11, 12, 15, 12, RIBBON)
    rect(d, 12, 13, 14, 13, RIBBON)
    rect(d, 13, 14, 13, 14, RIBBON)
    save(img, "Items", "LoveLetter.png")


def locker_image():
    """A 32x48 locker, used for both the tile and the item."""
    img = Image.new("RGBA", (32, 48), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    rect(d, 1, 0, 30, 47, (40, 46, 56, 255))
    rect(d, 2, 1, 29, 46, (92, 104, 122, 255))
    rect(d, 15, 1, 16, 46, (40, 46, 56, 255))           # split doors
    for door_x in (4, 19):
        for y in range(5, 14, 3):                        # vents
            rect(d, door_x, y, door_x + 8, y, (30, 34, 42, 255))
    rect(d, 12, 22, 13, 27, (200, 200, 210, 255))        # handles
    rect(d, 18, 22, 19, 27, (200, 200, 210, 255))
    rect(d, 5, 38, 7, 41, (110, 10, 20, 255))            # a little stain
    return img


def locker_item():
    img = locker_image().resize((20, 30), Image.NEAREST)
    save(img, "Items", "HidingLockerItem.png")


def locker_tile():
    # 2x3 tile sheet: 16px tiles with 2px padding -> 36x54
    src = locker_image()
    sheet = Image.new("RGBA", (36, 54), (0, 0, 0, 0))
    for tx in range(2):
        for ty in range(3):
            cell = src.crop((tx * 16, ty * 16, tx * 16 + 16, ty * 16 + 16))
            sheet.paste(cell, (tx * 18, ty * 18))
    save(sheet, "Tiles", "HidingLocker.png")


def bow(d, x, y):
    rect(d, x, y + 1, x + 2, y + 4, RIBBON)
    rect(d, x + 5, y + 1, x + 7, y + 4, RIBBON)
    rect(d, x + 3, y + 2, x + 4, y + 3, (140, 8, 24, 255))


def torn_ribbon():
    img = Image.new("RGBA", (26, 18), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    rect(d, 1, 3, 10, 12, RIBBON)
    rect(d, 15, 3, 24, 12, RIBBON)
    rect(d, 11, 5, 14, 10, (140, 8, 24, 255))
    rect(d, 9, 11, 11, 17, RIBBON)
    rect(d, 15, 11, 17, 15, RIBBON)       # torn tail
    rect(d, 20, 4, 21, 6, (0, 0, 0, 0))   # tear
    save(img, "Items", "TornRibbon.png")
    # Head equip sheet: 20 frames of 40x56 (vanilla player head layout).
    head = Image.new("RGBA", (40, 56 * 20), (0, 0, 0, 0))
    hd = ImageDraw.Draw(head)
    for f in range(20):
        bob = -2 if f in (7, 8, 9, 14, 15, 16) else 0
        bow(hd, 10, f * 56 + 6 + bob)
    save(head, "Items", "TornRibbon_Head.png")


# ---------------------------------------------------------------- UI
def vignette():
    n = 512
    img = Image.new("RGBA", (n, n))
    px = img.load()
    for y in range(n):
        for x in range(n):
            r = math.hypot((x + 0.5) / n * 2 - 1, (y + 0.5) / n * 2 - 1)
            t = min(max((r - 0.3) / 0.7, 0.0), 1.0)
            a = int(255 * (t * t * (3 - 2 * t)))
            px[x, y] = (255, 255, 255, a)
    save(img, "UI", "Vignette.png")


def blood_splat():
    rng = random.Random(1313)
    img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for _ in range(14):
        cx, cy, r = rng.randint(40, 216), rng.randint(40, 180), rng.randint(14, 40)
        shade = rng.randint(70, 140)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(shade, 0, 8, 255))
        for _ in range(rng.randint(1, 3)):        # drips
            dx = cx + rng.randint(-r // 2, r // 2)
            length = rng.randint(20, 75)
            w = rng.randint(2, 5)
            rect(d, dx, cy, dx + w, min(cy + length, 250), (shade, 0, 8, 255))
            d.ellipse([dx - 1, cy + length - 3, dx + w + 1, cy + length + 3], fill=(shade, 0, 8, 255))
    for _ in range(90):                           # spatter
        x, y, r = rng.randint(0, 255), rng.randint(0, 255), rng.randint(1, 4)
        d.ellipse([x - r, y - r, x + r, y + r], fill=(rng.randint(80, 150), 0, 10, 255))
    save(img, "UI", "BloodSplat.png")


FONT_BOLD = "/usr/share/fonts/opentype/inter/Inter-ExtraBold.otf"
FONT_JP = "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf"


def _font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def _shape(canvas, draw_mask, fill, outline=None, line=0):
    """Paint a shape drawn into a mask, with a clean outline of `line` px (anime line-art style)."""
    mask = Image.new("L", canvas.size, 0)
    draw_mask(ImageDraw.Draw(mask))
    if outline and line:
        grown = mask.filter(ImageFilter.MaxFilter(line * 2 + 1))
        canvas.paste(Image.new("RGBA", canvas.size, outline), (0, 0), grown)
    canvas.paste(Image.new("RGBA", canvas.size, fill), (0, 0), mask)


def _heart(d, cx, cy, r, fill):
    d.ellipse([cx - r, cy - r * 0.9, cx, cy + r * 0.1], fill=fill)
    d.ellipse([cx, cy - r * 0.9, cx + r, cy + r * 0.1], fill=fill)
    d.polygon([(cx - r * 0.98, cy - r * 0.25), (cx + r * 0.98, cy - r * 0.25), (cx, cy + r * 1.1)], fill=fill)


def _splatter(d, rng, cx, cy, spread, count, colors):
    for _ in range(count):
        a = rng.uniform(0, math.tau)
        dist = abs(rng.gauss(0, spread))
        x, y = cx + math.cos(a) * dist, cy + math.sin(a) * dist
        r = max(2, rng.gauss(spread * 0.06, spread * 0.04))
        c = rng.choice(colors)
        d.ellipse([x - r, y - r, x + r, y + r], fill=c)
        if rng.random() < 0.25:                                   # a drip running down
            length = rng.uniform(r * 2, r * 7)
            d.rectangle([x - r * 0.35, y, x + r * 0.35, y + length], fill=c)
            d.ellipse([x - r * 0.55, y + length - r * 0.5, x + r * 0.55, y + length + r * 0.6], fill=c)


def jumpscare():
    """The face that fills the screen when she catches you: guro-kawaii, like a deathcore album cover.

    Cute pastel anime girl, manic grin, heart pupils, plus cut lines, stitches and blood.
    Drawn at 2x and scaled down for smooth lines. 16:9 so widescreen doesn't crop it.
    """
    W, H = 1920, 1080
    rng = random.Random(1313)
    img = Image.new("RGBA", (W, H))
    d = ImageDraw.Draw(img)
    LINE = (20, 10, 14, 255)
    SKIN_K = (255, 236, 228, 255)
    SKIN_K_SHADE = (246, 206, 206, 255)
    BLUSH_K = (255, 140, 175, 255)
    HAIR_K = (22, 18, 22, 255)
    HAIR_K_HI = (96, 90, 98, 255)
    COLLAR = (26, 24, 28, 255)
    BLOOD_K = (196, 10, 40, 255)
    BLOOD_K_DARK = (120, 0, 24, 255)
    CUT = (215, 16, 44, 255)

    # background: lavender to pink, with bubbles
    for y in range(H):
        t = y / H
        c = tuple(int(a + (b - a) * t) for a, b in zip((255, 238, 242), (248, 196, 212))) + (255,)
        d.line([(0, y), (W, y)], fill=c)
    bubbles = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bubbles)
    for _ in range(26):
        r = rng.randint(30, 150)
        x, y = rng.randint(0, W), rng.randint(0, H)
        bd.ellipse([x - r, y - r, x + r, y + r], fill=(255, 255, 255, 46), outline=(255, 255, 255, 140), width=4)
        bd.ellipse([x - r * 0.55, y - r * 0.6, x - r * 0.25, y - r * 0.3], fill=(255, 255, 255, 150))
    img.alpha_composite(bubbles)

    # big blood splatter behind her
    _splatter(d, rng, 420, 820, 260, 160, [BLOOD_K, BLOOD_K_DARK, (220, 30, 60, 255)])
    _splatter(d, rng, 1500, 420, 160, 55, [BLOOD_K, BLOOD_K_DARK])

    cx = 960
    # back hair
    _shape(img, lambda m: m.polygon([(cx - 330, 260), (cx - 420, 700), (cx - 470, 1080), (cx + 470, 1080),
                                      (cx + 420, 700), (cx + 330, 260), (cx, 90)], fill=255), HAIR_K, LINE, 6)
    for k in range(14):                                           # strand lines
        x = cx - 400 + k * 62
        d.line([(x, 420), (x + rng.randint(-30, 30), 1080)], fill=(52, 46, 54, 255), width=4)

    # neck, choker with the dashed cut line, sailor collar
    _shape(img, lambda m: m.rectangle([cx - 70, 800, cx + 70, 960], fill=255), SKIN_K_SHADE, LINE, 6)
    # white sailor blouse, black collar lapels with white stripes
    _shape(img, lambda m: m.polygon([(cx - 400, 1080), (cx - 310, 930), (cx - 70, 900), (cx + 70, 900),
                                      (cx + 310, 930), (cx + 400, 1080)], fill=255), (250, 248, 250, 255), LINE, 6)
    for side in (-1, 1):
        lapel = [(cx + side * 330, 960), (cx + side * 300, 920), (cx + side * 70, 896), (cx, 1000),
                 (cx + side * 30, 1040), (cx + side * 120, 950), (cx + side * 320, 990)]
        _shape(img, lambda m, lapel=lapel: m.polygon(lapel, fill=255), COLLAR, LINE, 5)
        d.line([(cx + side * 300, 948), (cx + side * 110, 930), (cx + side * 24, 1010)], fill=(255, 255, 255, 255), width=7)
    _shape(img, lambda m: m.polygon([(cx - 60, 990), (cx + 60, 990), (cx, 1080)], fill=255), RIBBON, LINE, 5)

    # face: anime construction, wide cranium tapering to a small pointed chin
    jaw = [(cx - 262, 500), (cx - 252, 600), (cx - 222, 690), (cx - 168, 772), (cx - 96, 832),
           (cx - 30, 862), (cx, 868), (cx + 30, 862), (cx + 96, 832), (cx + 168, 772),
           (cx + 222, 690), (cx + 252, 600), (cx + 262, 500)]
    def face(m):
        m.ellipse([cx - 268, 190, cx + 268, 720], fill=255)
        m.polygon(jaw, fill=255)
    _shape(img, face, SKIN_K, LINE, 6)
    # shadow cast by the bangs across the forehead
    shade = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shade)
    sd.polygon([(cx - 250, 420), (cx + 250, 420), (cx + 245, 492), (cx + 120, 470), (cx, 488), (cx - 120, 470), (cx - 245, 492)],
               fill=SKIN_K_SHADE[:3] + (255,))
    face_mask = Image.new("L", (W, H), 0)
    face(ImageDraw.Draw(face_mask))
    img.paste(shade, (0, 0), ImageChops.multiply(shade.getchannel("A"), face_mask))
    # neck details below the chin: choker and the dashed "cut here" line
    d.rectangle([cx - 66, 884, cx + 66, 908], fill=(16, 10, 12, 255))
    _heart(d, cx, 920, 17, CUT)
    for x in range(cx - 60, cx + 60, 22):
        d.line([(x, 952), (x + 12, 952)], fill=CUT, width=6)

    # blush with hatching
    for bx in (cx - 165, cx + 165):
        blush = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(blush).ellipse([bx - 78, 660, bx + 78, 712], fill=BLUSH_K[:3] + (140,))
        img.alpha_composite(blush)
        for k in range(5):
            x = bx - 46 + k * 20
            d.line([(x, 704), (x + 13, 670)], fill=(226, 92, 120, 255), width=4)

    # eyes: tall anime eyes, thick winged upper lash, tall red iris, glossy highlights
    for side, ex in ((-1, cx - 128), (1, cx + 128)):
        ey = 590
        white = [ex - 92, ey - 92, ex + 92, ey + 88]
        eye_mask = Image.new("L", (W, H), 0)
        ImageDraw.Draw(eye_mask).ellipse(white, fill=255)
        eye = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ed = ImageDraw.Draw(eye)
        ed.ellipse(white, fill=(255, 255, 255, 255))
        ed.ellipse([ex - 92, ey - 92, ex + 92, ey - 30], fill=(226, 218, 226, 255))   # lid shadow
        for k in range(32):                                        # iris: dark top to bright bottom
            t = k / 31
            col = tuple(int(a + (b - a) * t) for a, b in zip((70, 0, 10), (255, 50, 64))) + (255,)
            ed.ellipse([ex - 64 + k * 0.6, ey - 96 + k * 2.4, ex + 64 - k * 0.6, ey + 80], fill=col)
        ed.ellipse([ex - 64, ey - 96, ex + 64, ey + 80], outline=(60, 0, 8, 255), width=5)
        _heart(ed, ex, ey + 2, 30, (40, 0, 6, 255))                # yandere heart pupil
        ed.ellipse([ex - 50, ey - 70, ex - 8, ey - 24], fill=(255, 255, 255, 255))     # main highlight
        ed.ellipse([ex + 22, ey + 34, ex + 40, ey + 52], fill=(255, 255, 255, 255))
        ed.arc([ex - 50, ey + 20, ex + 50, ey + 74], 20, 160, fill=(255, 140, 150, 255), width=6)  # glow
        img.paste(eye, (0, 0), ImageChops.multiply(eye.getchannel("A"), eye_mask))
        # thick upper lash line: a filled crescent, flicked at the outer corner
        lash = [(ex - 100, ey - 40), (ex - 70, ey - 86), (ex - 20, ey - 104), (ex + 30, ey - 104),
                (ex + 78, ey - 88), (ex + 104, ey - 60)]
        outer = lash[-1] if side > 0 else lash[0]
        d.line(lash, fill=LINE, width=20, joint="curve")
        d.polygon([outer, (outer[0] + side * 46, outer[1] - 30), (outer[0] + side * 8, outer[1] + 22)], fill=LINE)
        d.line([(ex - 86, ey + 74), (ex - 20, ey + 92), (ex + 40, ey + 88)] if side < 0 else
               [(ex - 40, ey + 88), (ex + 20, ey + 92), (ex + 86, ey + 74)], fill=LINE, width=5)   # lower lash
        d.line([(ex - side * 30, ey + 108), (ex + side * 60, ey + 100)], fill=(214, 160, 160, 255), width=3)  # eyebag

    # tiny nose, then a smaller open grin with a fang and blood drool
    d.line([(cx + 6, 700), (cx + 14, 716)], fill=(214, 150, 150, 255), width=5)
    def mouth(m):
        m.chord([cx - 82, 724, cx + 82, 830], 0, 180, fill=255)
    _shape(img, mouth, (112, 10, 26, 255), LINE, 5)
    d.ellipse([cx - 46, 784, cx + 46, 826], fill=(240, 96, 110, 255))         # tongue
    d.rectangle([cx - 76, 776, cx + 76, 785], fill=(255, 255, 255, 255))      # teeth
    d.polygon([(cx - 52, 785), (cx - 34, 785), (cx - 43, 812)], fill=(255, 255, 255, 255))   # fang
    d.line([(cx - 52, 785), (cx - 43, 812), (cx - 34, 785)], fill=LINE, width=3)
    d.rectangle([cx + 64, 800, cx + 72, 862], fill=BLOOD_K)                   # drool of blood
    d.ellipse([cx + 58, 850, cx + 78, 874], fill=BLOOD_K)

    # guro details: stitched scar, bandaid, blood smear
    d.line([(cx + 128, 744), (cx + 232, 700)], fill=(196, 60, 76, 255), width=7)
    for k in range(5):
        x = cx + 138 + k * 21
        y = 740 - k * 9
        d.line([(x - 9, y - 12), (x + 9, y + 12)], fill=LINE, width=5)
        d.line([(x - 9, y + 12), (x + 9, y - 12)], fill=LINE, width=5)
    bandaid = Image.new("RGBA", (180, 66), (0, 0, 0, 0))
    bdd = ImageDraw.Draw(bandaid)
    bdd.rounded_rectangle([3, 3, 177, 63], radius=28, fill=(250, 224, 196, 255), outline=LINE, width=5)
    bdd.rectangle([62, 8, 118, 58], fill=(255, 238, 226, 255))
    for hx, hy in ((28, 22), (38, 44), (142, 22), (152, 44)):
        _heart(bdd, hx, hy, 9, CUT)
    bandaid = bandaid.rotate(24, expand=True, resample=Image.BICUBIC)
    img.alpha_composite(bandaid, (cx - 286, 690))
    _splatter(d, rng, cx - 200, 760, 64, 24, [BLOOD_K, BLOOD_K_DARK])

    # bangs: hime cut with an angel-ring highlight, side locks, the big bow
    def bangs(m):
        pts = [(cx - 300, 300), (cx - 220, 140), (cx, 100), (cx + 220, 140), (cx + 300, 300), (cx + 300, 420)]
        for k in range(10):
            x = cx + 300 - (k + 1) * 60
            pts += [(x + 30, 476 if k % 2 else 460), (x, 420)]
        pts += [(cx - 300, 470)]
        m.polygon(pts, fill=255)
        m.polygon([(cx - 300, 300), (cx - 278, 420), (cx - 262, 620), (cx - 250, 900), (cx - 350, 940)], fill=255)   # side locks
        m.polygon([(cx + 300, 300), (cx + 278, 420), (cx + 262, 620), (cx + 250, 900), (cx + 350, 940)], fill=255)
    _shape(img, bangs, HAIR_K, LINE, 7)
    d.arc([cx - 230, 170, cx + 230, 330], 200, 340, fill=HAIR_K_HI, width=14)
    for ex in (cx - 128, cx + 128):                                # thin brows, drawn over the bangs (anime style)
        d.arc([ex - 76, 452, ex + 76, 510], 205, 335, fill=(70, 56, 60, 255), width=6)
    for k in range(9):
        x = cx - 240 + k * 60
        d.line([(x, 200), (x + 10, 420)], fill=(58, 52, 60, 255), width=4)
    def bow(m):
        m.polygon([(cx + 230, 150), (cx + 400, 60), (cx + 410, 250)], fill=255)
        m.polygon([(cx + 230, 150), (cx + 140, 20), (cx + 90, 200)], fill=255)
        m.ellipse([cx + 200, 120, cx + 262, 182], fill=255)
    _shape(img, bow, RIBBON, LINE, 6)
    d.line([(cx + 270, 140), (cx + 380, 100)], fill=(255, 120, 130, 255), width=8)

    # a little blood on the hair and face
    _splatter(d, rng, cx + 120, 300, 120, 40, [BLOOD_K, BLOOD_K_DARK])
    _splatter(d, rng, cx - 300, 980, 160, 50, [BLOOD_K, BLOOD_K_DARK])

    # drippy deathcore logo, top left
    logo = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(logo)
    font = _font(FONT_BOLD, 190)
    ld.text((70, 30), "ONRYO", font=font, fill=BLOOD_K, stroke_width=10, stroke_fill=LINE)
    bbox = ld.textbbox((70, 30), "ONRYO", font=font, stroke_width=10)
    for x in range(bbox[0] + 20, bbox[2] - 20, 18):                      # drips off the letters
        if rng.random() < 0.55:
            length = rng.randint(30, 150)
            w = rng.randint(6, 12)
            ld.rectangle([x, bbox[3] - 30, x + w, bbox[3] - 30 + length], fill=BLOOD_K)
            ld.ellipse([x - 3, bbox[3] - 36 + length, x + w + 3, bbox[3] - 22 + length], fill=BLOOD_K)
    for x in range(bbox[0] + 10, bbox[2] - 10, 34):                      # spikes up top
        if rng.random() < 0.5:
            ld.polygon([(x, bbox[1] + 20), (x + 16, bbox[1] + 20), (x + 8, bbox[1] - rng.randint(20, 60))], fill=LINE)
    img.alpha_composite(logo)

    # vertical tagline: "we'll be together forever"
    jp = _font(FONT_JP, 92)
    for k, ch in enumerate("ずっと一緒だよ"):
        d.text((1720, 170 + k * 112), ch, font=jp, fill=(20, 10, 14, 255), stroke_width=4, stroke_fill=(255, 245, 248, 255))
    _heart(d, 1766, 170 + 7 * 112 + 30, 34, CUT)

    save(img.resize((960, 540), Image.LANCZOS), "UI", "Jumpscare.png")


def icon():
    """Mod icon: a close crop of the jumpscare face, so the two always match. Run after jumpscare()."""
    face = Image.open(os.path.join(TEX, "UI", "Jumpscare.png")).convert("RGBA")
    crop = face.crop((290, 70, 670, 450))          # bow, bangs, heart eyes, grin
    crop.resize((80, 80), Image.LANCZOS).save(os.path.join(ROOT, "icon.png"))
    print("wrote icon.png")



if __name__ == "__main__":
    onryo()
    love_letter()
    locker_item()
    locker_tile()
    torn_ribbon()
    vignette()
    blood_splat()
    jumpscare()
    icon()
