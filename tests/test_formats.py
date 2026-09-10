"""Smoke tests using synthetic cursors (no real cursor packs are shipped).

Run with:  .venv/bin/python -m pytest tests/   (or just: python3 tests/test_formats.py)
"""
import io
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image  # noqa: E402

from cursor_manager import sets  # noqa: E402
from cursor_manager.formats import decode_ani, load_cursor, write_xcursor  # noqa: E402


def make_cur(size=32, hot=(3, 5), color=(255, 0, 200, 255)) -> bytes:
    """Build a 32-bpp .cur file with a filled square in the top-left quarter."""
    w = h = size
    rows = []
    for y in range(h - 1, -1, -1):          # DIB rows are stored bottom-up
        row = bytearray()
        for x in range(w):
            r, g, b, a = color if (x < w // 2 and y < h // 2) else (0, 0, 0, 0)
            row += bytes((b, g, r, a))
        rows.append(bytes(row))
    xor = b"".join(rows)
    and_mask = b"\x00" * (((w + 31) // 32) * 4) * h
    dib = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0, len(xor) + len(and_mask), 0, 0, 0, 0)
    image = dib + xor + and_mask
    header = struct.pack("<HHH", 0, 2, 1) + struct.pack("<BBBBHHII", w, h, 0, 0, hot[0], hot[1], len(image), 22)
    return header + image


def make_ani(frames, jiffies=6) -> bytes:
    """Wrap several .cur blobs in a RIFF ACON container."""
    def chunk(cid, body):
        return cid + struct.pack("<I", len(body)) + body + (b"\x00" if len(body) % 2 else b"")
    anih = struct.pack("<9I", 36, len(frames), len(frames), 0, 0, 0, 0, jiffies, 1)
    fram = b"fram" + b"".join(chunk(b"icon", f) for f in frames)
    body = b"ACON" + chunk(b"anih", anih) + chunk(b"LIST", fram)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def test_cur_decodes_with_hotspot_and_alpha():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "arrow.cur"
        p.write_bytes(make_cur(hot=(3, 5)))
        cur = load_cursor(str(p))
        assert not cur.animated and cur.size == (32, 32)
        fr = cur.frames[0]
        assert (fr.xhot, fr.yhot) == (3, 5)
        assert fr.image.getpixel((2, 2))[3] == 255 and fr.image.getpixel((30, 30))[3] == 0


def test_ani_decodes_frames_and_timing():
    ani = make_ani([make_cur(color=(255, 0, 0, 255)), make_cur(color=(0, 255, 0, 255)), make_cur()], jiffies=6)
    cur = decode_ani(ani)
    assert cur.animated and len(cur.frames) == 3
    assert cur.frames[0].delay_ms == 100
    assert cur.frames[1].image.getpixel((2, 2))[:3] == (0, 255, 0)


def test_gif_decodes_as_animation():
    frames = [Image.new("RGBA", (24, 24), (i * 40, 0, 0, 255)) for i in range(3)]
    buf = io.BytesIO()
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:], duration=80, loop=0)
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "pulse.gif"
        p.write_bytes(buf.getvalue())
        cur = load_cursor(str(p), hotspot=(4, 4))
        assert cur.animated and len(cur.frames) == 3 and cur.frames[0].xhot == 4


def test_xcursor_file_is_well_formed():
    cur = decode_ani(make_ani([make_cur(), make_cur()]))
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "default"
        write_xcursor(str(out), cur, base_px=32, nominal_sizes=(24, 48))
        data = out.read_bytes()
        magic, hsize, version, ntoc = struct.unpack_from("<4sIII", data, 0)
        assert magic == b"Xcur" and hsize == 16 and version == 0x10000
        assert ntoc == 2 * len(cur.frames)
        _, nominal, offset = struct.unpack_from("<III", data, 16)
        w, h = struct.unpack_from("<II", data, offset + 16)
        assert nominal == 24 and (w, h) == (32, 32)
        _, nominal48, offset48 = struct.unpack_from("<III", data, 16 + 12 * len(cur.frames))
        assert nominal48 == 48 and struct.unpack_from("<II", data, offset48 + 16) == (64, 64)


def test_role_matching_by_name_and_number():
    files = [Path(f"/x/{n}.ani") for n in ("Normal", "Help", "Busy", "Text", "Link", "Vertical")]
    roles = sets._match_roles(Path("/x"), files)
    assert roles["default"].name == "Normal.ani" and roles["ns-resize"].name == "Vertical.ani"
    numbered = [Path(f"/y/pack_{i:02d}.ani") for i in range(1, 16)]
    roles = sets._match_roles(Path("/y"), numbered)
    assert roles["default"].name == "pack_01.ani" and roles["pointer"].name == "pack_15.ani"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
