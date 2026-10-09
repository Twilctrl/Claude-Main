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

from PIL import Image, ImageDraw

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ScreamAndRun")
TEX = os.path.join(ROOT, "Assets", "Textures")

HAIR = (14, 10, 18, 255)
HAIR_HI = (40, 30, 52, 255)
SKIN = (238, 224, 214, 255)
SKIN_SHADE = (205, 186, 178, 255)
EYE = (220, 10, 30, 255)
WHITE = (236, 236, 244, 255)
NAVY = (30, 36, 72, 255)
NAVY_DARK = (20, 22, 48, 255)
RIBBON = (190, 16, 36, 255)
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


# ---------------------------------------------------------------- Tsubaki
# 6 frames, 40x56 each. Drawn facing right, then mirrored: Terraria expects NPC
# sprites to face LEFT and flips them itself when they walk right.
#   0 idle, 1-4 walk cycle, 5 "telegraph" (knife raised, wide eyes, smile).
FRAME_W, FRAME_H, FRAMES = 40, 56, 6

OUTLINE = (8, 5, 10, 255)
HAIR_MID = (26, 18, 34, 255)
SKIN_BLUSH = (232, 176, 180, 255)
EYE_DARK = (110, 0, 16, 255)
SCLERA = (250, 246, 246, 255)
BLOUSE_SHADE = (196, 198, 214, 255)
NAVY_HI = (52, 62, 112, 255)
SOCK = (16, 14, 22, 255)
SOCK_HI = (40, 36, 52, 255)
LOAFER = (64, 34, 26, 255)
LOAFER_HI = (100, 58, 40, 255)
BLADE_HI = (246, 248, 255, 255)
BLOOD_HI = (170, 10, 24, 255)


def px(d, x, y, c):
    d.point((x, y), fill=c)


def draw_tsubaki(d, oy, frame):
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

    # --- legs: black thigh-highs, a sliver of skin, loafers
    def leg(lx, lifted, front):
        top = y + 40
        bottom = y + 50 - lifted
        rect(d, lx, top, lx + 2, top + 1, SKIN if front else SKIN_SHADE)       # skin above the socks
        rect(d, lx, top + 2, lx + 2, bottom, SOCK)
        px(d, lx + (2 if front else 0), top + 3, SOCK_HI)
        rect(d, lx - 1, bottom + 1, lx + 3, bottom + 3, LOAFER)
        rect(d, lx + 2, bottom + 1, lx + 3, bottom + 1, LOAFER_HI)
    leg(16 + back_dx, lift, False)
    leg(21 + front_dx, 0, True)

    # --- pleated skirt with a white trim stripe
    rect(d, 13, y + 32, 28, y + 40, NAVY)
    for x in range(14, 28, 3):
        rect(d, x, y + 33, x, y + 40, NAVY_DARK)
        px(d, x + 1, y + 33, NAVY_HI)
    rect(d, 13, y + 38, 28, y + 38, WHITE)

    # --- sailor blouse
    rect(d, 14, y + 20, 27, y + 32, WHITE)
    rect(d, 14, y + 29, 27, y + 32, BLOUSE_SHADE)          # shading under the chest
    rect(d, 12, y + 20, 18, y + 25, NAVY)                 # collar (back flap)
    rect(d, 12, y + 24, 18, y + 24, WHITE)                # collar stripe
    rect(d, 19, y + 21, 25, y + 22, NAVY)                 # front collar
    rect(d, 20, y + 23, 24, y + 25, RIBBON)               # neckerchief knot
    rect(d, 21, y + 26, 23, y + 29, RIBBON)
    px(d, 22, y + 30, RIBBON)
    px(d, 21, y + 23, BLOOD_HI)
    for bx, by in ((17, 27), (25, 30), (15, 31)):        # blood flecks
        px(d, bx, by, BLOOD)

    # --- head
    rect(d, 16, y + 7, 27, y + 19, SKIN)
    rect(d, 16, y + 17, 18, y + 19, SKIN_SHADE)            # jaw shadow
    rect(d, 15, y + 3, 28, y + 8, HAIR)                    # hime-cut bangs, straight across
    for bx in (17, 20, 23, 26):
        px(d, bx, y + 9, HAIR)                             # bang points
    rect(d, 20, y + 4, 25, y + 4, HAIR_MID)                # shine
    rect(d, 14, y + 4, 17, y + 21, HAIR)                   # side lock, to the chin
    rect(d, 15, y + 8, 15, y + 19, HAIR_MID)
    # big red bow at the back of the head
    rect(d, 9, y + 3, 12, y + 6, RIBBON)
    rect(d, 9, y + 9, 12, y + 12, RIBBON)
    rect(d, 12, y + 6, 14, y + 9, EYE_DARK)
    rect(d, 10, y + 12, 11, y + 16, RIBBON)                # tails

    # eyes: lash line, white, red iris, dark pupil
    for ex in (20, 24):
        if tele:
            rect(d, ex, y + 10, ex + 2, y + 13, SCLERA)
            rect(d, ex + 1, y + 11, ex + 1, y + 12, EYE)
            px(d, ex + 1, y + 11, OUTLINE)
        else:
            rect(d, ex, y + 11, ex + 2, y + 11, OUTLINE)    # lashes
            rect(d, ex, y + 12, ex + 2, y + 13, SCLERA)
            rect(d, ex + 1, y + 12, ex + 2, y + 13, EYE)
            px(d, ex + 2, y + 13, EYE_DARK)
    # blush + mouth
    px(d, 19, y + 15, SKIN_BLUSH)
    px(d, 26, y + 15, SKIN_BLUSH)
    if tele:
        rect(d, 21, y + 16, 25, y + 16, BLOOD)             # too-wide smile
        px(d, 20, y + 15, BLOOD)
        px(d, 26, y + 15, BLOOD)
    else:
        rect(d, 22, y + 16, 23, y + 16, (170, 110, 120, 255))

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


def tsubaki():
    img = Image.new("RGBA", (FRAME_W, FRAME_H * FRAMES), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for f in range(FRAMES):
        draw_tsubaki(d, f * FRAME_H, f)
    add_outline(img, OUTLINE)
    save(img.transpose(Image.FLIP_LEFT_RIGHT), "NPCs", "Tsubaki.png")


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


def jumpscare():
    rng = random.Random(77)
    img = Image.new("RGBA", (256, 256), (0, 0, 0, 255))
    d = ImageDraw.Draw(img)
    d.ellipse([16, -6, 240, 290], fill=HAIR)                       # hair mass
    for _ in range(40):                                           # loose strands
        x = rng.randint(20, 236)
        d.line([(x, 20), (x + rng.randint(-12, 12), 256)], fill=(30, 22, 40, 255), width=1)
    d.ellipse([58, 40, 198, 232], fill=SKIN)                      # face
    d.ellipse([58, 150, 198, 232], fill=SKIN_SHADE)
    d.ellipse([62, 140, 194, 226], fill=SKIN)
    bangs = [(54, 30), (202, 30), (202, 92)]
    for k in range(8):                                            # bangs hanging in points
        x = 202 - (k + 1) * 18.5
        bangs += [(x + 9, 104 if k % 2 else 98), (x, 86)]
    bangs += [(54, 92)]
    d.polygon(bangs, fill=HAIR)
    d.polygon([(40, 60), (70, 60), (66, 240), (40, 256)], fill=HAIR)              # side locks
    d.polygon([(186, 60), (216, 60), (216, 256), (190, 240)], fill=HAIR)
    d.polygon([(196, 18), (236, 4), (232, 44)], fill=RIBBON)                      # bow
    d.polygon([(196, 18), (214, 50), (176, 42)], fill=RIBBON)
    d.ellipse([190, 14, 204, 28], fill=EYE_DARK)
    for ex in (100, 156):                                         # eyes, wide open
        d.ellipse([ex - 25, 106, ex + 25, 152], fill=SCLERA)
        d.ellipse([ex - 15, 110, ex + 15, 148], fill=EYE)
        d.ellipse([ex - 10, 115, ex + 10, 143], fill=EYE_DARK)
        d.ellipse([ex - 3, 125, ex + 3, 133], fill=(0, 0, 0, 255))
        d.ellipse([ex + 4, 117, ex + 9, 122], fill=(255, 255, 255, 255))    # glint
        d.arc([ex - 27, 102, ex + 27, 156], 190, 350, fill=OUTLINE, width=4)  # lash line
        for k in range(5):
            lx = ex - 22 + k * 11
            d.line([(lx, 106), (lx - 3, 98)], fill=OUTLINE, width=2)
    d.ellipse([70, 158, 92, 168], fill=SKIN_BLUSH)                # blush
    d.ellipse([164, 158, 186, 168], fill=SKIN_BLUSH)
    d.chord([84, 176, 172, 204], 0, 180, fill=(50, 0, 6, 255))     # thin, too-wide smile
    for tx in range(98, 160, 7):                                  # small even teeth
        d.rectangle([tx, 190, tx + 4, 194], fill=(236, 230, 220, 255))
    d.arc([84, 176, 172, 204], 0, 180, fill=BLOOD, width=2)
    d.line([(84, 190), (76, 184)], fill=BLOOD, width=2)           # corners pulled up
    d.line([(172, 190), (180, 184)], fill=BLOOD, width=2)
    rect(d, 100, 135, 102, 175, BLOOD)                            # tears of blood
    rect(d, 157, 140, 159, 185, BLOOD)
    rect(d, 120, 205, 123, 245, BLOOD)                            # drip from the smile
    save(img, "UI", "Jumpscare.png")


def icon():
    img = Image.new("RGBA", (80, 80), (20, 0, 4, 255))
    d = ImageDraw.Draw(img)
    d.ellipse([12, 6, 68, 90], fill=HAIR)
    d.ellipse([24, 18, 56, 64], fill=SKIN)
    d.rectangle([24, 18, 56, 30], fill=HAIR)
    d.rectangle([31, 38, 36, 41], fill=EYE)
    d.rectangle([44, 38, 49, 41], fill=EYE)
    d.arc([30, 44, 50, 58], 20, 160, fill=BLOOD, width=2)
    rect(d, 10, 8, 14, 12, RIBBON)
    img.save(os.path.join(ROOT, "icon.png"))
    print("wrote icon.png")


if __name__ == "__main__":
    tsubaki()
    love_letter()
    locker_item()
    locker_tile()
    torn_ribbon()
    vignette()
    blood_splat()
    jumpscare()
    icon()
