#!/usr/bin/env python3
"""Generate the original 'Demo Cursor' pack shipped in assets/bundled.

Everything here is drawn from scratch with Pillow (no third-party artwork), so the
pack can be published under the project's MIT license. Re-run to regenerate:
    .venv/bin/python tools/make_demo_cursor.py
"""
import math
import struct
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parents[1] / "assets" / "bundled" / "Demo Cursor"
S, K = 32, 4                       # cursor size and supersampling factor
DARK, LIGHT = (27, 21, 51, 255), (246, 243, 255, 255)
ACCENT, RED = (240, 182, 74, 255), (224, 79, 76, 255)


def canvas():
    return Image.new("RGBA", (S * K, S * K), (0, 0, 0, 0))


def P(pts):
    return [(x * K, y * K) for x, y in pts]


def finish(im):
    return im.resize((S, S), Image.LANCZOS)


def outlined_polygon(d, pts, fill=LIGHT):
    d.polygon(P(pts), fill=fill)
    d.line(P(pts + [pts[0]]), fill=DARK, width=2 * K, joint="curve")


def arrow_img(spark=None, badge=None):
    im = canvas(); d = ImageDraw.Draw(im)
    outlined_polygon(d, [(2, 1), (2, 24), (8.5, 18), (12.5, 28), (17.5, 26), (13.5, 16), (22, 16)])
    if spark is not None:              # small pulsing star near the tip
        r = spark * K
        cx, cy = 26 * K, 6 * K
        d.polygon([(cx, cy - r), (cx + r * .35, cy - r * .35), (cx + r, cy), (cx + r * .35, cy + r * .35),
                   (cx, cy + r), (cx - r * .35, cy + r * .35), (cx - r, cy), (cx - r * .35, cy - r * .35)], fill=ACCENT)
    if badge:
        badge(d)
    return im


def ring(d, cx, cy, r, angle, width=3):
    box = [(cx - r) * K, (cy - r) * K, (cx + r) * K, (cy + r) * K]
    d.arc(box, 0, 360, fill=DARK, width=width * K + 2 * K)
    d.arc(box, 0, 360, fill=LIGHT, width=width * K)
    d.arc(box, angle, angle + 110, fill=ACCENT, width=width * K)


def double_arrow():
    im = canvas(); d = ImageDraw.Draw(im)
    outlined_polygon(d, [(16, 2), (22, 9), (18.5, 9), (18.5, 23), (22, 23), (16, 30), (10, 23), (13.5, 23),
                         (13.5, 9), (10, 9)])
    return im


def rotate(im, deg):
    return im.rotate(deg, resample=Image.BICUBIC, expand=False)


def hand():
    im = canvas(); d = ImageDraw.Draw(im)
    d.rounded_rectangle(P([(9, 13), (25, 30)]), radius=5 * K, fill=LIGHT, outline=DARK, width=2 * K)
    d.rounded_rectangle(P([(12, 3), (18, 18)]), radius=3 * K, fill=LIGHT, outline=DARK, width=2 * K)
    d.rounded_rectangle(P([(12.8, 6), (17.2, 17)]), radius=2 * K, fill=LIGHT)   # hide the seam
    for x in (18.5, 22):                                                          # knuckle lines
        d.line(P([(x, 14), (x, 19)]), fill=DARK, width=K)
    return im


def cursors():
    """role file name -> (frames, hotspot)"""
    font = ImageFont.load_default(size=13 * K)
    out = {}
    out["Normal.ani"] = ([finish(arrow_img(spark=2.2 + 1.4 * math.sin(i / 8 * 2 * math.pi))) for i in range(8)], (2, 1))

    def q_badge(d):
        d.ellipse(P([(18, 18), (30, 30)]), fill=ACCENT, outline=DARK, width=K)
        d.text((24 * K, 24 * K), "?", fill=DARK, font=font, anchor="mm")
    out["Help.cur"] = ([finish(arrow_img(badge=q_badge))], (2, 1))

    frames = []
    for i in range(8):
        im = arrow_img(); ring(ImageDraw.Draw(im), 24, 24, 6, i * 45, width=3); frames.append(finish(im))
    out["Working.ani"] = (frames, (2, 1))

    frames = []
    for i in range(8):
        im = canvas(); ring(ImageDraw.Draw(im), 16, 16, 11, i * 45, width=4); frames.append(finish(im))
    out["Busy.ani"] = (frames, (16, 16))

    im = canvas(); d = ImageDraw.Draw(im)
    for a, b in (((16, 2), (16, 12)), ((16, 20), (16, 30)), ((2, 16), (12, 16)), ((20, 16), (30, 16))):
        d.line(P([a, b]), fill=DARK, width=4 * K); d.line(P([a, b]), fill=LIGHT, width=2 * K)
    d.ellipse(P([(14.5, 14.5), (17.5, 17.5)]), fill=ACCENT)
    out["Precision.cur"] = ([finish(im)], (16, 16))

    im = canvas(); d = ImageDraw.Draw(im)
    for a, b in (((16, 3), (16, 29)), ((11, 3), (21, 3)), ((11, 29), (21, 29))):
        d.line(P([a, b]), fill=DARK, width=4 * K); d.line(P([a, b]), fill=LIGHT, width=2 * K)
    out["Text.cur"] = ([finish(im)], (16, 16))

    im = canvas(); d = ImageDraw.Draw(im)
    outlined_polygon(d, [(3, 29), (5, 22), (23, 4), (28, 9), (10, 27)], fill=ACCENT)
    d.polygon(P([(3, 29), (5, 22), (10, 27)]), fill=LIGHT)
    d.line(P([(3, 29), (5, 22), (10, 27), (3, 29)]), fill=DARK, width=2 * K)
    out["Handwriting.cur"] = ([finish(im)], (3, 29))

    im = canvas(); d = ImageDraw.Draw(im)
    d.ellipse(P([(4, 4), (28, 28)]), outline=RED, width=4 * K)
    d.line(P([(9, 9), (23, 23)]), fill=RED, width=4 * K)
    out["Unavailable.cur"] = ([finish(im)], (16, 16))

    v = double_arrow()
    out["Vertical.cur"] = ([finish(v)], (16, 16))
    out["Horizontal.cur"] = ([finish(rotate(v, 90))], (16, 16))
    out["Diagonal1.cur"] = ([finish(rotate(v, 45))], (16, 16))
    out["Diagonal2.cur"] = ([finish(rotate(v, -45))], (16, 16))
    mv = rotate(v, 90); mv.alpha_composite(v)
    out["Move.cur"] = ([finish(mv)], (16, 16))
    out["Alternate.cur"] = ([finish(arrow_img().transpose(Image.FLIP_LEFT_RIGHT))], (29, 1))
    out["Link.cur"] = ([finish(hand())], (15, 4))
    return out


def cur_bytes(img, hot):
    w, h = img.size
    xor = img.transpose(Image.FLIP_TOP_BOTTOM).tobytes("raw", "BGRA")
    and_mask = bytes(((w + 31) // 32) * 4 * h)
    dib = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0, len(xor) + len(and_mask), 0, 0, 0, 0)
    image = dib + xor + and_mask
    return struct.pack("<HHH", 0, 2, 1) + struct.pack("<BBBBHHII", w % 256, h % 256, 0, 0, hot[0], hot[1], len(image), 22) + image


def ani_bytes(curs, jiffies=5):
    def chunk(cid, body):
        return cid + struct.pack("<I", len(body)) + body + (b"\x00" if len(body) % 2 else b"")
    anih = struct.pack("<9I", 36, len(curs), len(curs), 0, 0, 0, 0, jiffies, 1)
    fram = b"fram" + b"".join(chunk(b"icon", c) for c in curs)
    body = b"ACON" + chunk(b"anih", anih) + chunk(b"LIST", fram)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, (frames, hot) in cursors().items():
        curs = [cur_bytes(f, hot) for f in frames]
        data = ani_bytes(curs) if name.endswith(".ani") else curs[0]
        (OUT / name).write_bytes(data)
    (OUT / "README.txt").write_text(
        "Demo Cursor - original placeholder art drawn by tools/make_demo_cursor.py.\n"
        "Part of Simple Animated Cursor Manager, MIT licensed. Replace it with packs from real artists!\n")
    print(f"wrote {len(list(OUT.iterdir()))} files to {OUT}")


if __name__ == "__main__":
    main()
