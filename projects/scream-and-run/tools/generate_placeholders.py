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
#   0 idle, 1-4 walk cycle, 5 "telegraph" (arms up, before a teleport).
FRAME_W, FRAME_H, FRAMES = 40, 56, 6


def draw_tsubaki(d, oy, frame):
    # long hair behind the body
    rect(d, 11, oy + 6, 22, oy + 40, HAIR)
    rect(d, 10, oy + 14, 12, oy + 42, HAIR)
    # legs (walk cycle offsets)
    stride = {0: (0, 0), 1: (-2, 2), 2: (0, 0), 3: (2, -2), 4: (0, 0), 5: (-1, 1)}[frame]
    lift = {1: 1, 3: 1}.get(frame, 0)
    rect(d, 16 + stride[0], oy + 40, 18 + stride[0], oy + 50 - lift, SKIN_SHADE)
    rect(d, 21 + stride[1], oy + 40, 23 + stride[1], oy + 50, SKIN)
    rect(d, 15 + stride[0], oy + 50 - lift, 19 + stride[0], oy + 53 - lift, SHOE)
    rect(d, 20 + stride[1], oy + 50, 25 + stride[1], oy + 53, SHOE)
    # pleated skirt
    rect(d, 13, oy + 32, 28, oy + 40, NAVY)
    for x in range(14, 28, 3):
        rect(d, x, oy + 33, x, oy + 40, NAVY_DARK)
    # sailor top + collar + ribbon
    rect(d, 14, oy + 20, 27, oy + 32, WHITE)
    rect(d, 13, oy + 20, 17, oy + 25, NAVY)
    rect(d, 21, oy + 23, 24, oy + 26, RIBBON)
    rect(d, 22, oy + 26, 23, oy + 29, RIBBON)
    # head
    rect(d, 16, oy + 7, 27, oy + 19, SKIN)
    rect(d, 15, oy + 4, 28, oy + 9, HAIR)       # bangs
    rect(d, 14, oy + 5, 17, oy + 20, HAIR)      # side hair
    rect(d, 20, oy + 4, 25, oy + 5, HAIR_HI)    # shine
    rect(d, 13, oy + 5, 16, oy + 7, RIBBON)     # hair ribbon
    # eyes (bigger and wider in the telegraph frame)
    if frame == 5:
        rect(d, 21, oy + 11, 22, oy + 13, EYE)
        rect(d, 25, oy + 11, 26, oy + 13, EYE)
        rect(d, 21, oy + 16, 26, oy + 16, BLOOD)  # smile
    else:
        rect(d, 21, oy + 12, 22, oy + 12, EYE)
        rect(d, 25, oy + 12, 26, oy + 12, EYE)
    # arm + knife
    if frame == 5:
        rect(d, 26, oy + 12, 28, oy + 22, WHITE)
        rect(d, 27, oy + 9, 28, oy + 11, SKIN)
        rect(d, 27, oy + 2, 28, oy + 8, BLADE)
        rect(d, 27, oy + 8, 28, oy + 9, HANDLE)
    else:
        swing = {1: 1, 3: -1}.get(frame, 0)
        rect(d, 25, oy + 21, 27, oy + 29 + swing, WHITE)
        rect(d, 26, oy + 29 + swing, 28, oy + 31 + swing, SKIN)
        rect(d, 28, oy + 30 + swing, 29, oy + 31 + swing, HANDLE)
        rect(d, 30, oy + 30 + swing, 35, oy + 31 + swing, BLADE)
        rect(d, 33, oy + 32 + swing, 33, oy + 33 + swing, BLOOD)


def tsubaki():
    img = Image.new("RGBA", (FRAME_W, FRAME_H * FRAMES), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for f in range(FRAMES):
        draw_tsubaki(d, f * FRAME_H, f)
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
    img = Image.new("RGBA", (256, 256), (0, 0, 0, 255))
    d = ImageDraw.Draw(img)
    d.ellipse([20, 0, 236, 280], fill=HAIR)               # hair mass
    d.ellipse([58, 40, 198, 230], fill=SKIN)              # face
    d.polygon([(58, 40), (198, 40), (198, 95), (170, 80), (128, 100), (86, 80), (58, 95)], fill=HAIR)  # bangs
    for ex in (98, 158):                                  # eyes
        d.ellipse([ex - 22, 110, ex + 22, 150], fill=(250, 250, 250, 255))
        d.ellipse([ex - 14, 114, ex + 14, 146], fill=EYE)
        d.ellipse([ex - 3, 126, ex + 3, 134], fill=(0, 0, 0, 255))
    d.arc([78, 150, 178, 215], 10, 170, fill=BLOOD, width=6)   # smile
    rect(d, 80, 180, 82, 230, BLOOD)
    rect(d, 170, 175, 173, 240, BLOOD)
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
