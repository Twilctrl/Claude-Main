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


# ---------------------------------------------------------------- Onryo
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


def jumpscare():
    """The face you see when she catches you. Deliberately gory."""
    rng = random.Random(77)
    img = Image.new("RGBA", (256, 256), (0, 0, 0, 255))
    d = ImageDraw.Draw(img)
    DEAD_SKIN = (204, 200, 190, 255)
    DEAD_SHADE = (150, 148, 140, 255)
    BRUISE = (96, 70, 96, 255)
    VEIN = (110, 90, 120, 255)
    GORE = (90, 0, 6, 255)
    GORE_DARK = (40, 0, 4, 255)
    GUM = (150, 40, 50, 255)
    TOOTH = (226, 214, 180, 255)

    # hair: a wet, matted mass with clumped strands
    d.ellipse([12, -10, 244, 296], fill=HAIR)
    for _ in range(46):
        x = rng.randint(16, 240)
        d.line([(x, 10), (x + rng.randint(-14, 14), 256)], fill=(30, 22, 40, 255), width=rng.choice((1, 1, 2)))

    # face: grey dead skin in a diamond: narrow forehead, wide cheekbones, pointed chin
    face = [(92, 40), (164, 40), (194, 96), (206, 140), (184, 196), (152, 230), (128, 246),
            (104, 230), (72, 196), (50, 140), (62, 96)]
    d.polygon(face, fill=DEAD_SHADE)
    # lighter centre so the cheekbones and jaw edges read as shadow
    d.polygon([(96, 44), (160, 44), (188, 98), (198, 140), (178, 192), (148, 224), (128, 238),
               (108, 224), (78, 192), (58, 140), (68, 98)], fill=DEAD_SKIN)
    d.polygon([(184, 150), (198, 142), (180, 196), (156, 224)], fill=DEAD_SHADE)     # hollow under the cheekbones
    d.polygon([(72, 150), (58, 142), (76, 196), (100, 224)], fill=DEAD_SHADE)
    for ex in (100, 156):                                         # sunken, bruised under-eyes
        d.ellipse([ex - 30, 112, ex + 30, 170], fill=BRUISE)
        d.ellipse([ex - 32, 98, ex + 32, 160], fill=DEAD_SKIN)
    for _ in range(14):                                           # veins
        x, y = rng.randint(70, 186), rng.randint(60, 220)
        pts = [(x, y)]
        for _ in range(4):
            x += rng.randint(-6, 6)
            y += rng.randint(2, 7)
            pts.append((x, y))
        d.line(pts, fill=VEIN, width=1)

    # left eye: an empty socket, bleeding
    d.ellipse([74, 108, 126, 154], fill=GORE)
    d.ellipse([80, 114, 120, 148], fill=GORE_DARK)
    d.ellipse([88, 120, 112, 142], fill=(0, 0, 0, 255))
    # right eye: huge, bloodshot, pinprick pupil
    d.ellipse([130, 104, 184, 156], fill=SCLERA)
    for _ in range(16):
        a = rng.uniform(0, math.tau)
        r0, r1 = 12, 26
        d.line([(157 + math.cos(a) * r0, 130 + math.sin(a) * r0),
                (157 + math.cos(a) * r1 + rng.randint(-2, 2), 130 + math.sin(a) * r1 + rng.randint(-2, 2))],
               fill=BLOOD_HI, width=1)
    d.ellipse([145, 118, 169, 142], fill=EYE)
    d.ellipse([155, 128, 159, 132], fill=(0, 0, 0, 255))
    d.ellipse([162, 120, 166, 124], fill=(255, 255, 255, 255))
    d.arc([128, 100, 186, 160], 190, 350, fill=OUTLINE, width=4)

    # Glasgow smile: mouth slit ear to ear, teeth showing through the torn cheeks
    smile = [(68, 166), (86, 186), (128, 198), (170, 186), (188, 166),
             (184, 176), (170, 202), (128, 214), (86, 202), (72, 176)]
    d.polygon(smile, fill=GORE_DARK)
    d.polygon([(70, 176), (84, 190), (128, 202), (172, 190), (186, 176),
               (172, 198), (128, 208), (84, 198)], fill=GUM)
    for tx in range(78, 178, 6):                                  # upper teeth, uneven
        top = 182 + abs(tx - 128) // 12
        d.rectangle([tx, top, tx + 4, top + rng.randint(5, 8)], fill=TOOTH)
    for tx in range(88, 168, 7):                                  # lower teeth
        d.rectangle([tx, 200, tx + 4, 205], fill=TOOTH)
    d.line(smile + [smile[0]], fill=BLOOD, width=2)

    # her hime-cut bangs, wet and stuck together, plus the torn bow
    bangs = [(52, 24), (204, 24), (204, 96)]
    for k in range(8):
        x = 204 - (k + 1) * 19
        bangs += [(x + 9, 106 if k % 2 else 100), (x, 88)]
    bangs += [(52, 96)]
    d.polygon(bangs, fill=HAIR)
    for k in range(8):                                            # blood dripping off the tips
        x = 204 - (k + 1) * 19 + 9
        tip = 106 if k % 2 else 100
        rect(d, x - 1, tip, x + 1, tip + rng.randint(6, 26), BLOOD)
    d.polygon([(196, 16), (238, 2), (230, 30), (222, 24), (226, 44)], fill=RIBBON)     # torn bow
    d.polygon([(196, 16), (210, 52), (186, 40), (180, 46)], fill=RIBBON)
    d.ellipse([189, 9, 203, 23], fill=EYE_DARK)
    rect(d, 226, 44, 227, 70, BLOOD)

    # gashes: cheek and jaw, dark centre with a wet edge
    for (x0, y0, x1, y1) in ((150, 160, 192, 150), (72, 150, 90, 190), (110, 230, 144, 222)):
        d.line([(x0, y0), (x1, y1)], fill=BLOOD_HI, width=5)
        d.line([(x0, y0), (x1, y1)], fill=GORE_DARK, width=2)

    # blood pouring from the hairline, the socket and the mouth
    for x, top, length in ((92, 150, 90), (100, 150, 70), (108, 152, 104), (157, 156, 60), (128, 210, 46), (110, 206, 50)):
        rect(d, x, top, x + 3, min(255, top + length), BLOOD)
        d.ellipse([x - 2, min(252, top + length) - 3, x + 5, min(255, top + length) + 3], fill=BLOOD)

    # spray across the whole image
    for _ in range(140):
        x, y, r = rng.randint(0, 255), rng.randint(0, 255), rng.choice((1, 1, 1, 2, 3))
        d.ellipse([x - r, y - r, x + r, y + r], fill=rng.choice((BLOOD, BLOOD_HI, GORE)))
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
    onryo()
    love_letter()
    locker_item()
    locker_tile()
    torn_ribbon()
    vignette()
    blood_splat()
    jumpscare()
    icon()
