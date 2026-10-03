"""Generate synthetic half-frame scans (two pictures side by side with a dark gap)."""
import sys
from pathlib import Path

import numpy as np
import tifffile
from PIL import Image, ImageCms


def pair(w, h, gap_at, gap_w, seed, bits=8):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    img = np.zeros((h, w, 3), np.float32)
    img[..., 0] = 0.35 + 0.4 * np.sin(xx / (w / 7) + seed)
    img[..., 1] = 0.45 + 0.3 * np.cos(yy / (h / 5) + seed * 2)
    img[..., 2] = 0.5 + 0.25 * np.sin((xx + yy) / (w / 9))
    img += rng.normal(0, 0.03, img.shape)
    cx = int(gap_at * w)
    img[:, cx - gap_w // 2: cx + (gap_w + 1) // 2] = 0.02 + rng.normal(0, 0.004, (h, gap_w, 3))
    img = np.clip(img, 0, 1)
    if bits == 16:
        return (img * 65535).astype(np.uint16)
    return (img * 255).astype(np.uint8)


def main(out):
    out = Path(out)
    (out / "roll16").mkdir(parents=True, exist_ok=True)
    (out / "rolljpg" / "jpg").mkdir(parents=True, exist_ok=True)
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    for i in range(6):
        a = pair(1810, 1200, 0.5 + (i - 3) * 0.008, 36, i, 16)
        tifffile.imwrite(out / "roll16" / f"0000188100{i + 1:02d}.tif", a, photometric="rgb",
                         iccprofile=icc, resolution=(2400, 2400), resolutionunit="INCH")
    for i in range(8):
        a = pair(1565, 1037, 0.51, 22, 10 + i)
        Image.fromarray(a).save(out / "rolljpg" / "jpg" / f"2609022609{i + 1:04d}.jpg", quality=92, dpi=(300, 300))
    # a frame with no gap (night shot) and a portrait scan
    a = (np.random.default_rng(99).random((1037, 1565, 3)) * 30).astype(np.uint8)
    Image.fromarray(a).save(out / "rolljpg" / "jpg" / "26090226090009.jpg", quality=92)
    print("samples in", out)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/home/claude/samples")
