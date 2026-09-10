"""Decode Windows .cur/.ani files and ordinary images into cursor frames,
and encode frames into Xcursor files (pure Python + Pillow, no xcursorgen)."""
from __future__ import annotations

import io
import struct
from dataclasses import dataclass, field
from typing import List

from PIL import Image, ImageChops, ImageFile, ImageSequence

ImageFile.LOAD_TRUNCATED_IMAGES = True

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
XCURSOR_IMAGE_TYPE = 0xFFFD0002


@dataclass
class Frame:
    image: Image.Image      # RGBA
    xhot: int
    yhot: int
    delay_ms: int = 0       # 0 for static cursors


@dataclass
class Cursor:
    frames: List[Frame] = field(default_factory=list)

    @property
    def animated(self) -> bool:
        return len(self.frames) > 1

    @property
    def size(self):
        return self.frames[0].image.size


# --------------------------------------------------------------------------- CUR / ICO

def _decode_dib(blob: bytes) -> Image.Image:
    """Decode a BITMAPINFOHEADER icon image (XOR bitmap + AND mask) to RGBA."""
    hsz, bw, bh, planes, bpp, comp = struct.unpack_from("<IiiHHI", blob, 0)
    width = bw
    height = abs(bh) // 2          # the DIB height covers the XOR bitmap plus the AND mask
    if height <= 0 or width <= 0:
        raise ValueError("empty cursor bitmap")
    ncolors = struct.unpack_from("<I", blob, 32)[0] if hsz >= 36 else 0
    pos = hsz
    palette = b""
    if bpp <= 8:
        ncolors = ncolors or (1 << bpp)
        palette = blob[pos:pos + 4 * ncolors]
        pos += 4 * ncolors
    elif comp == 3:  # BI_BITFIELDS masks
        pos += 12
    xor_stride = ((width * bpp + 31) // 32) * 4
    and_stride = ((width + 31) // 32) * 4
    xor = blob[pos:pos + xor_stride * height]
    pos += xor_stride * height
    and_data = blob[pos:pos + and_stride * height]

    if bpp == 32:
        img = Image.frombytes("RGBA", (width, height), xor, "raw", "BGRA", xor_stride, -1)
    elif bpp == 24:
        img = Image.frombytes("RGB", (width, height), xor, "raw", "BGR", xor_stride, -1).convert("RGBA")
    elif bpp == 16:
        img = Image.frombytes("RGB", (width, height), xor, "raw", "BGR;15", xor_stride, -1).convert("RGBA")
    elif bpp in (1, 4, 8):
        raw_mode = "P" if bpp == 8 else f"P;{bpp}"
        img = Image.frombytes("P", (width, height), xor, "raw", raw_mode, xor_stride, -1)
        pal = []
        for i in range(0, len(palette), 4):
            b, g, r = palette[i], palette[i + 1], palette[i + 2]
            pal += [r, g, b]
        pal += [0, 0, 0] * (256 - len(pal) // 3)
        img.putpalette(pal)
        img = img.convert("RGBA")
    else:
        raise ValueError(f"unsupported bpp {bpp}")

    has_alpha = bpp == 32 and img.getchannel("A").getbbox() is not None
    if not has_alpha and len(and_data) >= and_stride * height:
        # AND mask: bit 1 = transparent. Raw mode "1;I" inverts so set bits -> 0.
        mask = Image.frombytes("1", (width, height), and_data, "raw", "1;I", and_stride, -1)
        img.putalpha(mask.convert("L"))
    return img


def decode_cur(data: bytes) -> List[Frame]:
    """Decode a .cur (or .ico) file. Returns one Frame per embedded image
    (largest first)."""
    reserved, typ, count = struct.unpack_from("<HHH", data, 0)
    if reserved != 0 or typ not in (1, 2) or count == 0:
        raise ValueError("not a CUR/ICO file")
    frames = []
    for i in range(count):
        w, h, cc, res, xh, yh, size, off = struct.unpack_from("<BBBBHHII", data, 6 + 16 * i)
        blob = data[off:off + size]
        if blob[:8] == PNG_MAGIC:
            img = Image.open(io.BytesIO(blob)).convert("RGBA")
        else:
            img = _decode_dib(blob)
        if typ == 1:  # ICO stores planes/bpp there, no hotspot
            xh = yh = 0
        frames.append(Frame(img, min(xh, img.width - 1), min(yh, img.height - 1)))
    frames.sort(key=lambda f: f.image.width * f.image.height, reverse=True)
    return frames


# --------------------------------------------------------------------------- ANI

def _riff_chunks(data: bytes, start: int, end: int):
    pos = start
    while pos + 8 <= end:
        cid = data[pos:pos + 4]
        size = struct.unpack_from("<I", data, pos + 4)[0]
        body_start = pos + 8
        body_end = min(body_start + size, end)
        yield cid, body_start, body_end
        pos = body_end + (size & 1)


def decode_ani(data: bytes) -> Cursor:
    if data[:4] != b"RIFF" or data[8:12] != b"ACON":
        raise ValueError("not an ANI file")
    info = {}
    icons: List[bytes] = []
    rate = seq = None

    def walk(start, end):
        nonlocal rate, seq
        for cid, bs, be in _riff_chunks(data, start, end):
            if cid == b"LIST":
                walk(bs + 4, be)
            elif cid == b"anih":
                (_, nframes, nsteps, w, h, bpp, planes, jif, flags) = struct.unpack_from("<9I", data, bs)
                info.update(nframes=nframes, nsteps=nsteps, jif=jif, flags=flags, w=w, h=h, bpp=bpp)
            elif cid == b"icon":
                icons.append(data[bs:be])
            elif cid == b"rate":
                rate = list(struct.unpack_from(f"<{(be - bs) // 4}I", data, bs))
            elif cid == b"seq ":
                seq = list(struct.unpack_from(f"<{(be - bs) // 4}I", data, bs))

    walk(12, len(data))
    if not icons:
        raise ValueError("ANI has no frames")
    decoded = []
    for blob in icons:
        if info.get("flags", 1) & 1:
            decoded.append(decode_cur(blob)[0])
        else:  # raw DIB frames (rare)
            img = _decode_dib(blob)
            decoded.append(Frame(img, 0, 0))
    nsteps = info.get("nsteps") or len(decoded)
    default_jif = info.get("jif") or 6
    frames = []
    for step in range(nsteps):
        idx = seq[step] if seq and step < len(seq) else step
        idx = min(idx, len(decoded) - 1)
        jif = rate[step] if rate and step < len(rate) else default_jif
        f = decoded[idx]
        frames.append(Frame(f.image, f.xhot, f.yhot, max(10, round(jif * 1000 / 60))))
    return Cursor(frames)


# --------------------------------------------------------------------------- generic images

MAX_IMAGE_PX = 512


def decode_image(data: bytes, hotspot=(0, 0)) -> Cursor:
    im = Image.open(io.BytesIO(data))
    if max(im.size) > MAX_IMAGE_PX:
        raise ValueError(f"{im.size[0]}x{im.size[1]} is too large for a cursor (max {MAX_IMAGE_PX} px); "
                         "this looks like a preview picture")
    frames = []
    n = getattr(im, "n_frames", 1)
    for fr in ImageSequence.Iterator(im):
        rgba = fr.convert("RGBA")
        delay = int(fr.info.get("duration", 100)) if n > 1 else 0
        frames.append(Frame(rgba, hotspot[0], hotspot[1], max(10, delay) if n > 1 else 0))
    return Cursor(frames)


def load_cursor(path: str, hotspot=(0, 0)) -> Cursor:
    """Decode any supported cursor/image file. Raises ValueError for corrupt input."""
    with open(path, "rb") as fh:
        data = fh.read()
    try:
        if data[:4] == b"RIFF" and data[8:12] == b"ACON":
            return decode_ani(data)
        if data[:4] in (b"\x00\x00\x02\x00", b"\x00\x00\x01\x00"):
            return Cursor([decode_cur(data)[0]])
        return decode_image(data, hotspot)
    except (struct.error, IndexError) as exc:
        raise ValueError(f"corrupt cursor file ({exc})") from exc


# --------------------------------------------------------------------------- Xcursor writer

def _premultiplied_bgra(img: Image.Image) -> bytes:
    r, g, b, a = img.convert("RGBA").split()
    r = ImageChops.multiply(r, a)
    g = ImageChops.multiply(g, a)
    b = ImageChops.multiply(b, a)
    return Image.merge("RGBA", (b, g, r, a)).tobytes()


def scale_frame(frame: Frame, target_px: int) -> Frame:
    """Scale a frame so its longest side is target_px, keeping the hotspot aligned."""
    w, h = frame.image.size
    if max(w, h) == target_px:
        return frame
    f = target_px / max(w, h)
    nw, nh = max(1, round(w * f)), max(1, round(h * f))
    if f < 1:
        resample = Image.LANCZOS
    elif abs(f - round(f)) < 1e-6:
        resample = Image.NEAREST      # crisp integer upscale for pixel-art cursors
    else:
        resample = Image.BICUBIC
    img = frame.image.resize((nw, nh), resample)
    return Frame(img, min(nw - 1, round(frame.xhot * f)), min(nh - 1, round(frame.yhot * f)), frame.delay_ms)


def write_xcursor(path: str, cursor: Cursor, base_px: int, nominal_sizes=(24, 48)):
    """Write an Xcursor file containing the animation at several nominal sizes.

    base_px is the pixel size drawn when the desktop asks for nominal size 24
    (Plasma's default). Other nominal sizes scale proportionally.
    """
    images = []  # (nominal, Frame)
    for nominal in sorted(nominal_sizes):
        px = max(1, round(base_px * nominal / 24))
        for fr in cursor.frames:
            images.append((nominal, scale_frame(fr, px)))

    ntoc = len(images)
    header = struct.pack("<4sIII", b"Xcur", 16, 0x10000, ntoc)
    toc = bytearray()
    body = bytearray()
    offset = 16 + 12 * ntoc
    for nominal, fr in images:
        w, h = fr.image.size
        pixels = _premultiplied_bgra(fr.image)
        chunk = struct.pack("<IIIIIIIII", 36, XCURSOR_IMAGE_TYPE, nominal, 1, w, h, fr.xhot, fr.yhot, fr.delay_ms) + pixels
        toc += struct.pack("<III", XCURSOR_IMAGE_TYPE, nominal, offset)
        body += chunk
        offset += len(chunk)
    with open(path, "wb") as fh:
        fh.write(header)
        fh.write(toc)
        fh.write(body)
