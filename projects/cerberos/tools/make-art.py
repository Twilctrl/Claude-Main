"""Generate the CerberOS wallpaper and icons.

    python3 tools/make-art.py stage-cerberos/06-desktop/files
"""
import random, sys
OUT = sys.argv[1]
random.seed(13)

DEFS = '''<defs>
  <radialGradient id="pit" cx="50%" cy="40%" r="75%">
    <stop offset="0" stop-color="#2e0909"/><stop offset="0.45" stop-color="#120505"/><stop offset="1" stop-color="#030202"/>
  </radialGradient>
  <radialGradient id="vignette" cx="50%" cy="45%" r="70%">
    <stop offset="0.55" stop-color="#000" stop-opacity="0"/><stop offset="1" stop-color="#000" stop-opacity="0.85"/>
  </radialGradient>
  <linearGradient id="hide" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#1e1414"/><stop offset="0.6" stop-color="#0e0909"/><stop offset="1" stop-color="#060404"/>
  </linearGradient>
  <linearGradient id="ironV" x1="0" y1="0" x2="1" y2="0">
    <stop offset="0" stop-color="#040303"/><stop offset="0.32" stop-color="#2a2421"/><stop offset="0.48" stop-color="#4c423c"/>
    <stop offset="0.66" stop-color="#1d1816"/><stop offset="1" stop-color="#020101"/>
  </linearGradient>
  <linearGradient id="ironH" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#040303"/><stop offset="0.35" stop-color="#2a2421"/><stop offset="0.5" stop-color="#4c423c"/>
    <stop offset="0.7" stop-color="#1d1816"/><stop offset="1" stop-color="#020101"/>
  </linearGradient>
  <linearGradient id="blood" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#3d0505"/><stop offset="0.6" stop-color="#7a0c10"/><stop offset="1" stop-color="#a3121b"/>
  </linearGradient>
  <linearGradient id="boneG" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#e6dcc8"/><stop offset="0.7" stop-color="#b3a68f"/><stop offset="1" stop-color="#6e5f4c"/>
  </linearGradient>
  <linearGradient id="textG" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#d8cdb8"/><stop offset="0.55" stop-color="#a39782"/><stop offset="1" stop-color="#5a1010"/>
  </linearGradient>
  <filter id="grime" x="0" y="0" width="100%" height="100%">
    <feTurbulence type="fractalNoise" baseFrequency="0.85" numOctaves="2" seed="3"/>
    <feColorMatrix values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 -1.4 0.95"/>
  </filter>
  <filter id="stains" x="0" y="0" width="100%" height="100%">
    <feTurbulence type="fractalNoise" baseFrequency="0.0045" numOctaves="4" seed="21"/>
    <feColorMatrix values="0 0 0 0 0.24  0 0 0 0 0.02  0 0 0 0 0.02  -2.2 0 0 0 1.25"/>
  </filter>
  <filter id="rust" x="0" y="0" width="100%" height="100%">
    <feTurbulence type="fractalNoise" baseFrequency="0.02 0.004" numOctaves="4" seed="9" result="n"/>
    <feColorMatrix in="n" values="0 0 0 0 0.30  0 0 0 0 0.11  0 0 0 0 0.05  2.6 0 0 0 -1.35" result="r"/>
    <feComposite in="r" in2="SourceAlpha" operator="in" result="rr"/>
    <feMerge><feMergeNode in="SourceGraphic"/><feMergeNode in="rr"/></feMerge>
  </filter>
  <filter id="ragged" x="-10%" y="-10%" width="120%" height="120%">
    <feTurbulence type="fractalNoise" baseFrequency="0.06" numOctaves="2" seed="5" result="t"/>
    <feDisplacementMap in="SourceGraphic" in2="t" scale="7" xChannelSelector="R" yChannelSelector="G"/>
  </filter>
  <filter id="eyeglow" x="-150%" y="-150%" width="400%" height="400%">
    <feGaussianBlur stdDeviation="7" result="b"/>
    <feMerge><feMergeNode in="b"/><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
  </filter>
  <filter id="shadow" x="-10%" y="-30%" width="120%" height="160%">
    <feGaussianBlur stdDeviation="10"/>
  </filter>
</defs>'''

def P(cx, cy, s, pts):
    return " ".join(f"{cx + x * s:.1f},{cy + y * s:.1f}" for x, y in pts)

def drip(x, y, length, w, cls='fill="url(#blood)"'):
    """A run of blood with a bulb at the end."""
    r = w * 0.9
    return (f'<path d="M{x - w / 2:.1f},{y:.1f} C{x - w / 2:.1f},{y + length * 0.5:.1f} {x - w * 0.35:.1f},{y + length * 0.8:.1f} '
            f'{x - r:.1f},{y + length:.1f} A{r:.1f},{r:.1f} 0 1,0 {x + r:.1f},{y + length:.1f} '
            f'C{x + w * 0.35:.1f},{y + length * 0.8:.1f} {x + w / 2:.1f},{y + length * 0.5:.1f} {x + w / 2:.1f},{y:.1f} Z" {cls}/>'
            f'<ellipse cx="{x - r * 0.35:.1f}" cy="{y + length - r * 0.2:.1f}" rx="{r * 0.25:.1f}" ry="{r * 0.4:.1f}" fill="#d4202c" opacity="0.5"/>')

def hound(cx, cy, s, eye_scale=1.0):
    skull = [(-0.62, -0.62), (-0.98, -1.5), (-0.36, -0.86), (0.36, -0.86), (0.98, -1.5), (0.62, -0.62),
             (1.02, -0.18), (0.8, 0.28), (0.46, 0.55), (0.34, 1.0), (0.14, 1.18), (-0.14, 1.18),
             (-0.34, 1.0), (-0.46, 0.55), (-0.8, 0.28), (-1.02, -0.18)]
    mouth = [(-0.36, 0.98), (0.36, 0.98), (0.3, 1.52), (0, 1.62), (-0.3, 1.52)]
    jaw = [(-0.33, 1.5), (0.33, 1.5), (0.2, 1.8), (-0.2, 1.8)]
    fangs_up = [(-0.27, 1.02, 0.3), (0.27, 1.02, 0.3), (-0.12, 1.08, 0.14), (0.12, 1.08, 0.14)]
    fangs_dn = [(-0.21, 1.52, 0.2), (0.21, 1.52, 0.2)]
    eyes = [[(-0.66, -0.2), (-0.18, 0.04), (-0.55, 0.12)], [(0.66, -0.2), (0.18, 0.04), (0.55, 0.12)]]
    out = ['<g filter="url(#ragged)">',
           f'<polygon points="{P(cx, cy, s, skull)}" fill="url(#hide)" stroke="#2a0606" stroke-width="{s * 0.03:.1f}"/>',
           f'<polygon points="{P(cx, cy, s, mouth)}" fill="#1c0202"/>',
           f'<polygon points="{P(cx, cy, s, [(-0.26, 1.05), (0.26, 1.05), (0.18, 1.45), (-0.18, 1.45)])}" fill="#4a0505"/>',
           f'<polygon points="{P(cx, cy, s, jaw)}" fill="url(#hide)" stroke="#2a0606" stroke-width="{s * 0.03:.1f}"/>']
    for x, y, h in fangs_up:
        out.append(f'<polygon points="{P(cx, cy, s, [(x - 0.06, y), (x + 0.06, y), (x, y + h)])}" fill="url(#boneG)"/>')
    for x, y, h in fangs_dn:
        out.append(f'<polygon points="{P(cx, cy, s, [(x - 0.055, y), (x + 0.055, y), (x, y - h)])}" fill="url(#boneG)"/>')
    # nose, angry brows
    out.append(f'<polygon points="{P(cx, cy, s, [(-0.12, 0.92), (0.12, 0.92), (0, 1.04)])}" fill="#000"/>')
    for sign in (-1, 1):
        out.append(f'<line x1="{cx + sign * 0.78 * s:.1f}" y1="{cy - 0.42 * s:.1f}" x2="{cx + sign * 0.14 * s:.1f}" '
                   f'y2="{cy - 0.1 * s:.1f}" stroke="#000" stroke-width="{s * 0.09:.1f}" stroke-linecap="round"/>')
    out.append('</g>')
    for e in eyes:
        out.append(f'<polygon points="{P(cx, cy, s, e)}" fill="#c4161f" filter="url(#eyeglow)"/>')
        x0 = sum(p[0] for p in e) / 3
        out.append(f'<circle cx="{cx + x0 * s:.1f}" cy="{cy + 0.02 * s:.1f}" r="{s * 0.035 * eye_scale:.1f}" fill="#ff6a4a"/>')
    # blood from the jaws
    for dx, ln in ((-0.3, 0.9), (0.05, 1.4), (0.28, 0.6), (-0.1, 0.45)):
        out.append(drip(cx + dx * s, cy + 1.72 * s, ln * s, s * 0.07))
    return "\n".join(out)

def chain(x, y0, y1, scale=1.0):
    out, y, i = [], y0, 0
    while y < y1:
        if i % 2 == 0:
            out.append(f'<ellipse cx="{x}" cy="{y + 26 * scale:.1f}" rx="{15 * scale:.1f}" ry="{27 * scale:.1f}" fill="none" stroke="url(#ironV)" stroke-width="{8 * scale:.1f}"/>')
        else:
            out.append(f'<rect x="{x - 4 * scale:.1f}" y="{y:.1f}" width="{8 * scale:.1f}" height="{54 * scale:.1f}" rx="{4 * scale:.1f}" fill="url(#ironV)"/>')
        y += 40 * scale
        i += 1
    # hook / shackle at the end
    out.append(f'<path d="M{x},{y + 10} q0,40 30,40 q30,0 30,-34" fill="none" stroke="url(#ironV)" stroke-width="{11 * scale:.1f}" stroke-linecap="round"/>')
    out.append(drip(x + 60, y + 10, 70, 7))
    return '<g filter="url(#rust)">' + "".join(out) + '</g>'

def wallpaper():
    W, H = 1920, 1080
    s = []
    s.append(f'<rect width="{W}" height="{H}" fill="url(#pit)"/>')
    s.append(f'<rect width="{W}" height="{H}" filter="url(#stains)" opacity="0.7"/>')
    # a dim furnace glow low behind the hounds
    s.append(f'<ellipse cx="{W / 2}" cy="560" rx="560" ry="300" fill="#5a0a0a" opacity="0.35" filter="url(#shadow)"/>')
    s.append(hound(W / 2 - 340, 470, 112))
    s.append(hound(W / 2 + 340, 470, 112))
    s.append(hound(W / 2, 400, 150, 1.2))
    # the cage
    bars = '<g filter="url(#rust)">'
    xs = list(range(15, W, 124))  # leaves a gap framing the middle hound
    for x in xs:
        bars += f'<rect x="{x}" y="0" width="30" height="{H}" fill="url(#ironV)"/>'
    for y in (140, 930):
        bars += f'<rect x="0" y="{y}" width="{W}" height="36" fill="url(#ironH)"/>'
        for x in xs:
            bars += f'<circle cx="{x + 15}" cy="{y + 18}" r="9" fill="#3a322d" stroke="#0a0808" stroke-width="3"/><circle cx="{x + 12}" cy="{y + 15}" r="3" fill="#6a5d54"/>'
    bars += '</g>'
    s.append(bars)
    s.append(chain(214, 176, 700))
    s.append(chain(1706, 176, 560, 0.9))
    # blood running off the top crossbar and down the bars
    for x in xs:
        if random.random() < 0.55:
            s.append(drip(x + random.uniform(6, 24), 176, random.uniform(40, 420), random.uniform(6, 11)))
    for _ in range(18):
        x, y, r = random.uniform(0, W), random.uniform(820, H), random.uniform(2, 9)
        s.append(f'<ellipse cx="{x:.0f}" cy="{y:.0f}" rx="{r:.1f}" ry="{r * random.uniform(0.6, 1):.1f}" fill="#5a0808" opacity="0.8"/>')
    # name
    s.append(f'<text x="{W / 2}" y="842" text-anchor="middle" font-family="DejaVu Serif, serif" font-weight="700" font-size="112" letter-spacing="22" fill="#000" opacity="0.9" filter="url(#shadow)">CERBEROS</text>')
    s.append(f'<g filter="url(#ragged)"><text x="{W / 2}" y="836" text-anchor="middle" font-family="DejaVu Serif, serif" font-weight="700" font-size="112" letter-spacing="22" fill="url(#textG)">CERBEROS</text></g>')
    for x, ln in ((560, 70), (668, 30), (812, 110), (905, 45), (1060, 85), (1190, 40), (1312, 120), (1392, 55)):
        s.append(drip(x, 832, ln, 7))
    # tally marks
    tx, ty = 120, 990
    for g in range(6):
        for k in range(4):
            s.append(f'<line x1="{tx + g * 62 + k * 10}" y1="{ty}" x2="{tx + g * 62 + k * 10 + 2}" y2="{ty + 36}" stroke="#8a7f70" stroke-width="3" opacity="0.6"/>')
        s.append(f'<line x1="{tx + g * 62 - 6}" y1="{ty + 30}" x2="{tx + g * 62 + 38}" y2="{ty + 6}" stroke="#8a7f70" stroke-width="3" opacity="0.6"/>')
    # small print
    s.append(f'<text x="{W - 60}" y="1040" text-anchor="end" font-family="DejaVu Sans Mono, monospace" font-size="17" letter-spacing="3" fill="#6f8a8f" opacity="0.55">NPU · CPU · REMOTE</text>')
    s.append(f'<text x="{W / 2}" y="900" text-anchor="middle" font-family="DejaVu Sans Mono, monospace" font-size="19" letter-spacing="9" fill="#8a7f70" opacity="0.75">LOCAL AI · NO CLOUD</text>')
    s.append(f'<rect width="{W}" height="{H}" fill="url(#vignette)"/>')
    s.append(f'<rect width="{W}" height="{H}" filter="url(#grime)" opacity="0.45"/>')
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">\n{DEFS}\n' + "\n".join(s) + "\n</svg>\n"

def icon():
    s = ['<rect x="4" y="4" width="120" height="120" rx="18" fill="url(#pit)" stroke="#3a0a0a" stroke-width="4"/>',
         hound(36, 58, 16), hound(92, 58, 16), hound(64, 50, 22)]
    for x in (22, 48, 80, 106):
        s.append(f'<rect x="{x - 3}" y="8" width="6" height="112" fill="url(#ironV)"/>')
    s.append('<rect x="8" y="14" width="112" height="6" fill="url(#ironH)"/>')
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128">\n{DEFS}\n' + "\n".join(s) + "\n</svg>\n"

def model_icon():
    s = ['<rect x="4" y="4" width="120" height="120" rx="18" fill="#0a0707" stroke="#3a0a0a" stroke-width="4"/>',
         # a blood drop held in a cage, with a thin circuit trace: the little bit of machine
         '<path d="M64,18 C64,18 30,62 30,82 A34,34 0 0,0 98,82 C98,62 64,18 64,18 Z" fill="url(#blood)"/>',
         '<ellipse cx="52" cy="78" rx="6" ry="11" fill="#d4202c" opacity="0.5"/>',
         '<path d="M64,104 V90 H78 V76" fill="none" stroke="#6f8a8f" stroke-width="3" opacity="0.8"/><circle cx="78" cy="74" r="4" fill="#6f8a8f"/>']
    for x in (36, 64, 92):
        s.append(f'<rect x="{x - 3}" y="10" width="6" height="108" fill="url(#ironV)" opacity="0.9"/>')
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128">\n{DEFS}\n' + "\n".join(s) + "\n</svg>\n"

open(f"{OUT}/usr/share/backgrounds/cerberos/cerberos.svg", "w").write(wallpaper())
open(f"{OUT}/usr/share/icons/hicolor/scalable/apps/cerberos.svg", "w").write(icon())
open(f"{OUT}/usr/share/icons/hicolor/scalable/apps/cerberos-model.svg", "w").write(model_icon())
