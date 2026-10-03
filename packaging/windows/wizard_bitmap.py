"""164×314 sidebar bitmap for the installer's welcome/finish pages."""
import sys
from PIL import Image

icon, out = sys.argv[1], sys.argv[2]
bmp = Image.new("RGB", (164, 314), (22, 22, 20))
ic = Image.open(icon).convert("RGBA").resize((112, 112), Image.Resampling.LANCZOS)
bmp.paste(ic, (26, 70), ic)
bmp.save(out, "BMP")
