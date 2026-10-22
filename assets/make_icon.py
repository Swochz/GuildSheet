"""Draws assets/icon.png (400x400): a guild banner over a spreadsheet, with a sync badge.

    pip install pillow
    python assets/make_icon.py
"""

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

S = 1600  # draw large, then scale down for smooth edges
OUT = Path(__file__).resolve().parent / "icon.png"

BG_TOP = (22, 30, 48)
BG_BOTTOM = (10, 12, 20)
GOLD = (232, 182, 72)
GOLD_DARK = (150, 104, 30)
SHEET = (236, 232, 220)
GRID = (178, 170, 150)
HEADER = (58, 92, 70)
CELL_GREEN = (98, 168, 110)
BANNER = (150, 32, 38)
BANNER_DARK = (100, 18, 24)


def rounded(draw, box, r, **kw):
    draw.rounded_rectangle(box, radius=r, **kw)


def main():
    img = Image.new("RGB", (S, S))
    px = img.load()
    for y in range(S):  # vertical gradient
        t = y / (S - 1)
        c = tuple(round(BG_TOP[i] * (1 - t) + BG_BOTTOM[i] * t) for i in range(3))
        for x in range(S):
            px[x, y] = c
    d = ImageDraw.Draw(img)

    # gold frame
    rounded(d, (40, 40, S - 40, S - 40), 170, outline=GOLD_DARK, width=26)
    rounded(d, (72, 72, S - 72, S - 72), 145, outline=GOLD, width=10)

    # sheet with a soft shadow
    sx0, sy0, sx1, sy1 = 300, 420, 1300, 1300
    shadow = Image.new("L", (S, S), 0)
    ImageDraw.Draw(shadow).rounded_rectangle((sx0 + 30, sy0 + 40, sx1 + 30, sy1 + 40), radius=50, fill=170)
    shadow = shadow.filter(ImageFilter.GaussianBlur(40))
    img.paste((0, 0, 0), (0, 0), shadow)
    d = ImageDraw.Draw(img)
    rounded(d, (sx0, sy0, sx1, sy1), 50, fill=SHEET)

    # header row and grid
    cols, rows = 4, 5
    hh = 150
    d.rounded_rectangle((sx0, sy0, sx1, sy0 + hh), radius=50, fill=HEADER)
    d.rectangle((sx0, sy0 + hh - 50, sx1, sy0 + hh), fill=HEADER)
    cw = (sx1 - sx0) / cols
    rh = (sy1 - sy0 - hh) / rows
    for i in range(1, cols):
        x = sx0 + i * cw
        d.line((x, sy0 + hh, x, sy1 - 10), fill=GRID, width=8)
    for j in range(1, rows):
        y = sy0 + hh + j * rh
        d.line((sx0 + 10, y, sx1 - 10, y), fill=GRID, width=8)
    for i in range(cols):  # header labels as bars
        x = sx0 + i * cw
        d.rounded_rectangle((x + 45, sy0 + 58, x + cw - 45, sy0 + 92), radius=17, fill=(210, 226, 210))
    # filled cells: a few "data" bars and checkmarks
    for j in range(rows):
        y = sy0 + hh + j * rh
        w = [0.75, 0.55, 0.85, 0.6, 0.7][j]
        d.rounded_rectangle((sx0 + 40, y + rh * 0.36, sx0 + 40 + (cw - 80) * w, y + rh * 0.64), radius=16, fill=(120, 112, 98))
        if j in (1, 2, 4):  # row 0 is under the banner
            cx, cy = sx0 + 2.5 * cw, y + rh / 2
            d.line((cx - 40, cy, cx - 10, cy + 30, cx + 45, cy - 35), fill=CELL_GREEN, width=22, joint="curve")

    # guild banner hanging over the top of the sheet
    bx0, bx1, by0, by1 = 560, 1040, 150, 700
    mid = (bx0 + bx1) / 2
    d.rectangle((bx0 - 70, by0 - 30, bx1 + 70, by0 + 14), fill=GOLD_DARK)  # pole
    d.ellipse((bx0 - 110, by0 - 48, bx0 - 30, by0 + 32), fill=GOLD)
    d.ellipse((bx1 + 30, by0 - 48, bx1 + 110, by0 + 32), fill=GOLD)
    d.polygon([(bx0, by0), (bx1, by0), (bx1, by1), (mid, by1 - 110), (bx0, by1)], fill=BANNER)
    d.polygon([(bx1 - 60, by0), (bx1, by0), (bx1, by1), (bx1 - 60, by1 - 50)], fill=BANNER_DARK)
    d.line([(bx0 + 30, by0 + 20), (bx0 + 30, by1 - 30), (mid, by1 - 145), (bx1 - 30, by1 - 30), (bx1 - 30, by0 + 20)],
           fill=GOLD, width=14, joint="curve")
    # emblem: a gold star on the banner
    cx, cy, r1, r2 = mid, by0 + 240, 120, 50
    pts = []
    for k in range(10):
        a = -math.pi / 2 + k * math.pi / 5
        r = r1 if k % 2 == 0 else r2
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    d.polygon(pts, fill=GOLD)

    # sync badge, bottom right
    bcx, bcy, br = 1215, 1215, 205
    d.ellipse((bcx - br - 18, bcy - br - 18, bcx + br + 18, bcy + br + 18), fill=BG_BOTTOM)
    d.ellipse((bcx - br, bcy - br, bcx + br, bcy + br), fill=GOLD)
    ar = 118
    box = (bcx - ar, bcy - ar, bcx + ar, bcy + ar)
    d.arc(box, 200, 330, fill=BG_BOTTOM, width=38)
    d.arc(box, 20, 150, fill=BG_BOTTOM, width=38)

    def arrow_head(angle_deg, clockwise=True):
        a = math.radians(angle_deg)
        tip = (bcx + ar * math.cos(a), bcy + ar * math.sin(a))
        tang = a + (math.pi / 2 if clockwise else -math.pi / 2)
        size = 70
        p1 = (tip[0] + size * math.cos(tang), tip[1] + size * math.sin(tang))
        p2 = (tip[0] - size * 0.75 * math.cos(a), tip[1] - size * 0.75 * math.sin(a))
        p3 = (tip[0] + size * 0.75 * math.cos(a), tip[1] + size * 0.75 * math.sin(a))
        d.polygon([p1, p2, p3], fill=BG_BOTTOM)

    arrow_head(330)
    arrow_head(150)

    # round the outer corners
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, S - 1, S - 1), radius=210, fill=255)
    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    out.resize((400, 400), Image.LANCZOS).save(OUT)
    print(OUT)


if __name__ == "__main__":
    main()
