"""Build the marketplace PWA icons from the master logo.

    python -m scripts.make_market_icons

Writes into web/public/:
  yq-icon-192.png, yq-icon-512.png          purpose "any"   (transparent corners, the rounded mark)
  yq-icon-512-maskable.png                  purpose "maskable" (full-bleed plum, mark inside the 80% safe zone)
  apple-touch-icon.png (180)                opaque plum
  shortcut-search.png / shortcut-cart.png / shortcut-orders.png (96)  plum tiles with a white glyph

The plum is the logo's own (#6D4091 — src/market/market.css --m-plum). Pillow only.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "YQ LOGO" / "derived" / "yq-logo-transparent.png"
OUT = ROOT / "web" / "public"
PLUM = (0x6D, 0x40, 0x91, 255)


def load_mark() -> Image.Image:
    im = Image.open(SRC).convert("RGBA")
    bbox = im.getbbox()
    return im.crop(bbox) if bbox else im


def fit(mark: Image.Image, size: int, scale: float) -> Image.Image:
    w = int(size * scale)
    m = mark.copy()
    m.thumbnail((w, w), Image.LANCZOS)
    return m


def any_icon(mark: Image.Image, size: int) -> Image.Image:
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    m = fit(mark, size, 1.0)
    canvas.alpha_composite(m, ((size - m.width) // 2, (size - m.height) // 2))
    return canvas


def maskable_icon(mark: Image.Image, size: int) -> Image.Image:
    canvas = Image.new("RGBA", (size, size), PLUM)
    # the mark is itself a plum square with white glyphs — keep only the white glyphs on the plum field
    m = fit(mark, size, 0.62)
    canvas.alpha_composite(m, ((size - m.width) // 2, (size - m.height) // 2))
    return canvas


def opaque_icon(mark: Image.Image, size: int) -> Image.Image:
    canvas = Image.new("RGBA", (size, size), PLUM)
    m = fit(mark, size, 0.86)
    canvas.alpha_composite(m, ((size - m.width) // 2, (size - m.height) // 2))
    return canvas


def glyph_tile(kind: str, size: int = 96) -> Image.Image:
    """Simple white line glyphs on plum (no font dependency): search, cart, orders."""
    im = Image.new("RGBA", (size, size), PLUM)
    d = ImageDraw.Draw(im)
    s = size
    lw = max(4, s // 16)
    white = (255, 255, 255, 255)
    if kind == "search":
        r = s * 0.22
        cx, cy = s * 0.44, s * 0.44
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=white, width=lw)
        d.line([cx + r * 0.7, cy + r * 0.7, s * 0.78, s * 0.78], fill=white, width=lw)
    elif kind == "cart":
        d.rounded_rectangle([s * 0.24, s * 0.36, s * 0.76, s * 0.78], radius=s * 0.06, outline=white, width=lw)
        d.arc([s * 0.36, s * 0.2, s * 0.64, s * 0.52], 180, 360, fill=white, width=lw)
    else:  # orders: a box with a lid line
        d.rounded_rectangle([s * 0.24, s * 0.3, s * 0.76, s * 0.78], radius=s * 0.06, outline=white, width=lw)
        d.line([s * 0.24, s * 0.46, s * 0.76, s * 0.46], fill=white, width=lw)
        d.line([s * 0.5, s * 0.3, s * 0.5, s * 0.46], fill=white, width=lw)
    return im


def save(im: Image.Image, name: str) -> None:
    path = OUT / name
    im.save(path, format="PNG", optimize=True)
    print(f"  {name:28s} {path.stat().st_size / 1024:6.1f} KB")


def main() -> int:
    if not SRC.exists():
        print(f"missing {SRC}")
        return 1
    mark = load_mark()
    print("writing icons to web/public/")
    save(any_icon(mark, 192), "yq-icon-192.png")
    save(any_icon(mark, 512), "yq-icon-512.png")
    save(maskable_icon(mark, 512), "yq-icon-512-maskable.png")
    save(opaque_icon(mark, 180), "apple-touch-icon.png")
    for k in ("search", "cart", "orders"):
        save(glyph_tile(k), f"shortcut-{k}.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
