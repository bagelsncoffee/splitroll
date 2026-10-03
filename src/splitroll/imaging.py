"""Image I/O that preserves bit depth, colour profile and resolution.

Pixels travel as numpy arrays (H, W) or (H, W, 3) in uint8 or uint16.
Only previews/thumbnails are ever reduced to 8-bit.
"""
import io
import math
import os
import struct
import zlib
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

try:
    import tifffile
except Exception:  # pragma: no cover
    tifffile = None

Image.MAX_IMAGE_PIXELS = None      # film scans can be huge; files are the user's own

IMAGE_EXTS = {".jpg", ".jpeg", ".tif", ".tiff", ".png", ".webp", ".bmp"}
try:                                # optional HEIC support if pillow-heif is present
    import pillow_heif              # noqa
    pillow_heif.register_heif_opener()
    IMAGE_EXTS |= {".heic", ".heif"}
except Exception:
    pass

DISPLAY_MAX = 2400
THUMB_MAX = 480


class Picture:
    """Decoded image + the metadata we carry through to export."""
    __slots__ = ("arr", "icc", "dpi")

    def __init__(self, arr, icc=None, dpi=None):
        self.arr = arr
        self.icc = icc
        self.dpi = dpi

    @property
    def bits(self):
        return 16 if self.arr.dtype == np.uint16 else 8

    @property
    def size(self):
        return self.arr.shape[1], self.arr.shape[0]


# ── decoding ──────────────────────────────────────────────────────────────
def _normalise(arr):
    """Coerce any decoded array to (H,W) or (H,W,3) uint8/uint16."""
    if arr.ndim == 3 and arr.shape[0] in (3, 4) and arr.shape[2] not in (3, 4):
        arr = np.moveaxis(arr, 0, -1)                       # planar → contig
    if arr.ndim == 3 and arr.shape[2] == 4:
        arr = arr[:, :, :3]                                  # drop alpha / extra sample
    if arr.ndim == 3 and arr.shape[2] == 2:
        arr = arr[:, :, 0]
    if arr.ndim == 3 and arr.shape[2] == 1:
        arr = arr[:, :, 0]
    if arr.dtype == np.uint8 or arr.dtype == np.uint16:
        pass
    elif arr.dtype == bool:
        arr = arr.astype(np.uint8) * 255
    elif np.issubdtype(arr.dtype, np.floating):
        a = np.nan_to_num(arr.astype(np.float64))
        hi = 1.0 if a.max() <= 1.0 else float(a.max())
        arr = np.clip(a / hi * 65535 + 0.5, 0, 65535).astype(np.uint16)
    elif np.issubdtype(arr.dtype, np.integer):
        if arr.dtype.itemsize > 2:
            arr = (arr.astype(np.uint64) >> (8 * arr.dtype.itemsize - 16)).astype(np.uint16)
        else:
            arr = arr.astype(np.uint16)
    return np.ascontiguousarray(arr)


def _tiff_read(path):
    with tifffile.TiffFile(path) as tf:
        page = tf.pages[0]
        photometric = int(page.photometric)
        if photometric not in (0, 1, 2):            # palette, CMYK, YCbCr-raw, etc → Pillow
            raise ValueError("unsupported photometric")
        arr = page.asarray()
        if photometric == 0:                         # MINISWHITE
            arr = (np.iinfo(arr.dtype).max - arr) if np.issubdtype(arr.dtype, np.integer) else 1 - arr
        icc = None
        t = page.tags.get(34675)
        if t is not None:
            icc = bytes(t.value)
        dpi = None
        try:
            xr, yr, unit = page.tags.get(282), page.tags.get(283), page.tags.get(296)
            if xr and yr:
                fx = xr.value[0] / xr.value[1]
                fy = yr.value[0] / yr.value[1]
                u = int(unit.value) if unit else 2
                if u == 3:                           # centimetre
                    fx, fy = fx * 2.54, fy * 2.54
                if fx > 0 and fy > 0:
                    dpi = (round(fx, 3), round(fy, 3))
        except Exception:
            dpi = None
        orient = page.tags.get(274)
        arr = _normalise(arr)
        if orient is not None:
            arr = _apply_orientation(arr, int(orient.value))
        return Picture(arr, icc, dpi)


def _apply_orientation(arr, o):
    if o == 2: return arr[:, ::-1].copy()
    if o == 3: return np.rot90(arr, 2).copy()
    if o == 4: return arr[::-1].copy()
    if o == 5: return np.rot90(arr[:, ::-1], 1).copy()
    if o == 6: return np.rot90(arr, -1).copy()
    if o == 7: return np.rot90(arr[:, ::-1], -1).copy()
    if o == 8: return np.rot90(arr, 1).copy()
    return arr


def _pil_read(path):
    im = Image.open(path)
    icc = im.info.get("icc_profile")
    dpi = im.info.get("dpi")
    try:
        im = ImageOps.exif_transpose(im)
    except Exception:
        pass
    mode = im.mode
    if mode in ("I;16", "I;16B", "I;16L", "I;16N"):
        arr = np.array(im, dtype=np.uint16)
    elif mode == "I":
        arr = np.clip(np.array(im), 0, 65535).astype(np.uint16)
    elif mode == "L":
        arr = np.array(im)
    elif mode == "LA":
        arr = np.array(im.convert("L"))
    else:
        if mode != "RGB":
            im = im.convert("RGB")
            if mode == "CMYK":
                icc = None                            # CMYK profile no longer applies
        arr = np.array(im)
    if dpi:
        try:
            dpi = (float(dpi[0]), float(dpi[1]))
        except Exception:
            dpi = None
    return Picture(_normalise(arr), icc, dpi)


def _png16_read(path):
    """Pillow reduces 16-bit RGB PNGs to 8-bit, so decode those with imagecodecs."""
    import imagecodecs
    with open(path, "rb") as fh:
        head = fh.read(33)
        if head[:8] != b"\x89PNG\r\n\x1a\n" or head[24] != 16:
            return None
        data = head + fh.read()
    arr = imagecodecs.png_decode(data)
    with Image.open(path) as im:
        icc, dpi = im.info.get("icc_profile"), im.info.get("dpi")
    return Picture(_normalise(arr), icc, (float(dpi[0]), float(dpi[1])) if dpi else None)


def load(path):
    """Full-resolution decode, keeping 16-bit data where present."""
    ext = Path(path).suffix.lower()
    if ext in (".tif", ".tiff") and tifffile is not None:
        try:
            return _tiff_read(path)
        except Exception:
            pass
    if ext == ".png":
        try:
            pic = _png16_read(path)
            if pic is not None:
                return pic
        except Exception:
            pass
    return _pil_read(path)


def probe(path):
    """Cheap (w, h, bits) without decoding pixels where possible."""
    ext = Path(path).suffix.lower()
    if ext in (".tif", ".tiff") and tifffile is not None:
        try:
            with tifffile.TiffFile(path) as tf:
                p = tf.pages[0]
                h, w = p.imagelength, p.imagewidth
                bits = 16 if p.bitspersample > 8 else 8
                o = p.tags.get(274)
                if o is not None and int(o.value) in (5, 6, 7, 8):
                    w, h = h, w
                return w, h, bits
        except Exception:
            pass
    with Image.open(path) as im:
        w, h = im.size
        bits = 16 if im.mode.startswith("I") else 8
        try:
            o = im.getexif().get(274, 1)
            if o in (5, 6, 7, 8):
                w, h = h, w
        except Exception:
            pass
        return w, h, bits


# ── transforms ────────────────────────────────────────────────────────────
def rotate(arr, deg):
    """Clockwise rotation in 90° steps."""
    k = (int(deg) // 90) % 4
    return np.ascontiguousarray(np.rot90(arr, -k)) if k else arr


def to8(arr):
    if arr.dtype == np.uint8:
        return arr
    return ((arr.astype(np.uint32) + 128) // 257).astype(np.uint8)


def to_pil(arr):
    a = to8(arr)
    return Image.fromarray(a, "L" if a.ndim == 2 else "RGB")


def pil_rotate(im, deg):
    k = (int(deg) // 90) % 4
    return {0: im, 1: im.transpose(Image.Transpose.ROTATE_270),
            2: im.transpose(Image.Transpose.ROTATE_180),
            3: im.transpose(Image.Transpose.ROTATE_90)}[k]


def split_bounds(width, pct, border):
    """Pixel columns [0, le) and [rs, width) for a cut at pct% with a centred border."""
    pct = min(95.0, max(5.0, float(pct)))
    border = max(0, int(border))
    cx = int(math.floor(pct / 100.0 * width + 0.5))   # same rounding as the UI
    le = max(1, cx - border // 2)
    rs = min(width - 1, cx + (border + 1) // 2)
    if rs <= le:
        rs = le
    return le, rs


# ── encoders ──────────────────────────────────────────────────────────────
def _png_chunk(tag, data):
    c = struct.pack(">I", len(data)) + tag + data
    return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def _write_png16(path, arr, icc=None, dpi=None):
    h, w = arr.shape[:2]
    color = 0 if arr.ndim == 2 else 2
    raw = arr.astype(">u2")
    rows = raw.reshape(h, -1).view(np.uint8)
    data = np.zeros((h, rows.shape[1] + 1), np.uint8)      # filter byte 0 per row
    data[:, 1:] = rows
    out = [b"\x89PNG\r\n\x1a\n",
           _png_chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 16, color, 0, 0, 0))]
    if icc:
        out.append(_png_chunk(b"iCCP", b"ICC Profile\x00\x00" + zlib.compress(icc)))
    if dpi:
        ppm = [max(1, round(d / 0.0254)) for d in dpi]
        out.append(_png_chunk(b"pHYs", struct.pack(">IIB", ppm[0], ppm[1], 1)))
    out.append(_png_chunk(b"IDAT", zlib.compress(data.tobytes(), 6)))
    out.append(_png_chunk(b"IEND", b""))
    with open(path, "wb") as f:
        for c in out:
            f.write(c)


def save(pic_or_arr, path, fmt, icc=None, dpi=None):
    """fmt: 'tiff' | 'png' | 'jpeg'. TIFF/PNG are lossless and keep 16-bit."""
    arr = pic_or_arr.arr if isinstance(pic_or_arr, Picture) else pic_or_arr
    if isinstance(pic_or_arr, Picture):
        icc = icc or pic_or_arr.icc
        dpi = dpi or pic_or_arr.dpi
    tmp = str(path) + ".part"
    if fmt == "tiff":
        kw = {}
        if dpi:
            kw["resolution"] = (float(dpi[0]), float(dpi[1]))
            kw["resolutionunit"] = "INCH"
        if icc:
            kw["iccprofile"] = icc
        tifffile.imwrite(tmp, arr, photometric="minisblack" if arr.ndim == 2 else "rgb",
                         compression="zlib", metadata=None, **kw)
    elif fmt == "png" and arr.dtype == np.uint16:
        _write_png16(tmp, arr, icc, dpi)
    else:
        im = Image.fromarray(to8(arr), "L" if arr.ndim == 2 else "RGB")
        kw = {}
        if icc:
            kw["icc_profile"] = icc
        if dpi:
            kw["dpi"] = (round(dpi[0]), round(dpi[1]))
        if fmt == "png":
            im.save(tmp, "PNG", compress_level=6, **kw)
        else:
            im.save(tmp, "JPEG", quality=100, subsampling=0, optimize=True, **kw)
    os.replace(tmp, path)


# ── previews ──────────────────────────────────────────────────────────────
def make_previews(pic, disp_path, thumb_path):
    im = to_pil(pic.arr)
    if im.mode == "L":
        im = im.convert("RGB")
    d = im.copy()
    d.thumbnail((DISPLAY_MAX, DISPLAY_MAX), Image.Resampling.LANCZOS)
    d.save(disp_path, "JPEG", quality=90)
    t = d.copy()
    t.thumbnail((THUMB_MAX, THUMB_MAX), Image.Resampling.LANCZOS)
    t.save(thumb_path, "JPEG", quality=85)
    return d


def jpeg_bytes(im, q=88):
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=q)
    buf.seek(0)
    return buf


# ── automatic cut detection ───────────────────────────────────────────────
def detect_cut(im, src_width):
    """Find the gap between the two half-frames.

    `im` is a PIL preview already rotated the way the user sees it.
    Returns (cut_pct, gap_px_in_source) or None if no confident gap was found.
    """
    g = im.convert("L")
    if g.width > 1200:
        g = g.resize((1200, max(1, round(g.height * 1200 / g.width))), Image.Resampling.BILINEAR)
    a = np.asarray(g, dtype=np.float32) / 255.0
    h, w = a.shape
    if w < 40 or h < 20:
        return None
    band = a[int(h * 0.1):int(h * 0.9)]
    mean = band.mean(axis=0)
    std = band.std(axis=0)
    lo, hi = int(w * 0.30), int(w * 0.70)
    ref = float(np.median(mean))

    def runs(mask):
        out, start = [], None
        for x in range(lo, hi):
            if mask[x] and start is None:
                start = x
            elif not mask[x] and start is not None:
                out.append((start, x)); start = None
        if start is not None:
            out.append((start, hi))
        return out

    best = None
    dark = (mean < max(0.06, min(0.22, ref * 0.45))) & (std < 0.07)
    bright = (mean > max(0.90, ref + 0.35)) & (std < 0.05)
    for mask in (dark, bright):
        for s, e in runs(mask):
            width = e - s
            if width < 2 or width > w * 0.12 or s == lo or e == hi:
                continue
            # the gap must be clearly different from the image just beside it
            l_ctx = mean[max(0, s - 12):s]
            r_ctx = mean[e:e + 12]
            if l_ctx.size == 0 or r_ctx.size == 0:
                continue
            inside = mean[s:e].mean()
            contrast = min(abs(l_ctx.mean() - inside), abs(r_ctx.mean() - inside))
            if contrast < 0.05:
                continue
            centre = (s + e) / 2.0
            score = contrast - abs(centre / w - 0.5)
            if best is None or score > best[0]:
                best = (score, s, e)
        if best:
            break
    if not best:
        return None
    _, s, e = best
    cut = (s + e) / 2.0 / w * 100.0
    gap = int(round((e - s) * src_width / w))
    return round(cut, 3), gap
