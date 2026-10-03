"""Draw the splitroll icon: a cinnamon roll cut down the middle.

Outputs icon.png (1024), icon.ico (16–256) and ui/logo.png (sidebar mark).
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

HERE = Path(__file__).resolve().parent
S = 1024
BG = (22, 22, 20, 255)
DOUGH = (214, 150, 74, 255)
DOUGH_EDGE = (150, 92, 40, 255)
CINNAMON = (104, 56, 24, 255)
GLAZE = (246, 232, 206, 255)


def roll_layer(size, r):
    """Supersampled roll: dough disc, cinnamon spiral with a round brush, soft glaze."""
    k = 4
    big = size * k
    R = r * k
    c = big / 2
    im = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse([c - R, c - R, c + R, c + R], fill=DOUGH_EDGE)
    d.ellipse([c - R * 0.94, c - R * 0.94, c + R * 0.94, c + R * 0.94], fill=DOUGH)
    turns = 2.75
    a = (R * 0.80) / (turns * 2 * math.pi)
    w = R * 0.085
    n = 2600
    for i in range(n):
        t = 0.6 + i / (n - 1) * (turns * 2 * math.pi - 0.6)
        rr = a * t
        x = c + rr * math.cos(t - math.pi / 2)
        y = c + rr * math.sin(t - math.pi / 2)
        ww = w * (0.55 + 0.45 * min(1, t / 6))
        d.ellipse([x - ww / 2, y - ww / 2, x + ww / 2, y + ww / 2], fill=CINNAMON)
    # glaze: a soft translucent pool over the upper half
    g = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    gd = ImageDraw.Draw(g)
    gd.ellipse([c - R * 0.62, c - R * 0.78, c + R * 0.30, c - R * 0.10], fill=GLAZE[:3] + (70,))
    g = g.filter(ImageFilter.GaussianBlur(R * 0.06))
    im.alpha_composite(g)
    return im.resize((size, size), Image.Resampling.LANCZOS)


def make(size=S, background=True):
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    if background:
        bgm = Image.new("L", (size, size), 0)
        ImageDraw.Draw(bgm).rounded_rectangle([0, 0, size - 1, size - 1], radius=int(size * 0.22), fill=255)
        out.paste(Image.new("RGBA", (size, size), BG), (0, 0), bgm)
    r = size * (0.34 if background else 0.44)
    roll = roll_layer(size, r)
    gap = int(size * 0.035)
    half = size // 2
    left = roll.crop((0, 0, half, size))
    right = roll.crop((half, 0, size, size))
    shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    shadow.alpha_composite(left, (-gap, 0))
    shadow.alpha_composite(right, (half + gap, 0))
    sh = shadow.getchannel("A").filter(ImageFilter.GaussianBlur(size * 0.02))
    out.alpha_composite(Image.merge("RGBA", [Image.new("L", (size, size), 0)] * 3 + [sh.point(lambda v: v * 0.5)]),
                        (0, int(size * 0.015)))
    out.alpha_composite(left, (-gap, 0))
    out.alpha_composite(right, (half + gap, 0))
    return out


if __name__ == "__main__":
    big = make(S)
    big.save(HERE / "icon.png")
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    big.save(HERE / "icon.ico", sizes=[(s, s) for s in sizes])
    mark = make(256, background=False).resize((96, 96), Image.Resampling.LANCZOS)
    ui = HERE.parent / "src" / "splitroll" / "ui"
    ui.mkdir(parents=True, exist_ok=True)
    mark.save(ui / "logo.png")
    big.resize((64, 64), Image.Resampling.LANCZOS).save(ui / "icon.png")
    print("ok")
