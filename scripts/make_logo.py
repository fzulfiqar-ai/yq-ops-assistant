"""Generate brand logo assets from YQ LOGO/LOGO.jpeg.

Outputs (into YQ LOGO/derived/):
  - yq-logo-transparent.png   full-res icon with the white corners removed
  - yq-icon-{512,180,32,16}.png  favicon / apple-touch / PWA sizes
  - yq-favicon.svg            crisp vector recreation (rounded square + 'yq?')

The white *corners* around the rounded square are flood-filled to transparent
from each corner; the white 'yq?' text is enclosed by purple, so it is never
reached and stays intact.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "YQ LOGO" / "LOGO.jpeg"
OUT = ROOT / "YQ LOGO" / "derived"
OUT.mkdir(parents=True, exist_ok=True)

img = Image.open(SRC).convert("RGB")
w, h = img.size
print(f"source size: {w}x{h}")


def px(x, y):
    return img.getpixel((x, y))


tl = px(int(w * 0.12), int(h * 0.12))   # square top-left (lighter purple)
br = px(int(w * 0.88), int(h * 0.88))   # square bottom-right (darker purple)
print("corner sample:", px(2, 2))
print("square TL/BR :", tl, br)

# ── transparent PNG: flood-fill the white corners to a marker, then to alpha ──
flood = img.copy()
MARK = (255, 0, 255)
for c in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]:
    ImageDraw.floodfill(flood, c, MARK, thresh=70)

rgba = img.convert("RGBA")
fp, rp = flood.load(), rgba.load()
for y in range(h):
    for x in range(w):
        if fp[x, y] == MARK:
            rp[x, y] = (0, 0, 0, 0)

rgba.save(OUT / "yq-logo-transparent.png")
for s in (512, 180, 32, 16):
    rgba.resize((s, s), Image.LANCZOS).save(OUT / f"yq-icon-{s}.png")

# ── crisp SVG recreation (rounded square gradient + 'yq?') ──
def hexc(c):
    return "#%02x%02x%02x" % c[:3]

svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="512" height="512" role="img" aria-label="YQ">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="{hexc(tl)}"/>
      <stop offset="1" stop-color="{hexc(br)}"/>
    </linearGradient>
  </defs>
  <rect x="16" y="16" width="480" height="480" rx="116" fill="url(#g)"/>
  <text x="248" y="312" text-anchor="middle"
        font-family="'Segoe UI','Helvetica Neue',Arial,sans-serif" font-weight="800"
        font-size="290" fill="#ffffff" letter-spacing="-10">yq?</text>
</svg>
"""
(OUT / "yq-favicon.svg").write_text(svg, encoding="utf-8")

print("\nwrote:")
for p in sorted(OUT.iterdir()):
    print("  ", p.relative_to(ROOT))
