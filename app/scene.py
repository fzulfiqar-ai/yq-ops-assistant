"""AI lifestyle scenes — place the REAL product into a premium AI-generated studio scene
(the 'million-dollar' look). Agnes text-to-image makes an empty premium backdrop; the real
product (background knocked out) is composited on with a contact shadow + reflection, so the
actual product is never distorted. Backdrops are cached in the bucket and reused (cost/speed).

Returns a CLEAN composite (no text — safe for Agnes image-to-video and moderation) and a
BRANDED still (brand + price chip + CTA overlay) for the picture ad.
"""
from __future__ import annotations

import io
import logging
import os
import random
from datetime import datetime, timezone

from app.database import get_client

log = logging.getLogger(__name__)

_BUCKET = "marketing"
# Neutral, moderation-safe premium backdrops (empty — the product is composited in).
SCENES = {
    "marble": ("Empty premium product-photography backdrop, polished white marble surface with "
               "soft reflections, dark charcoal studio wall, elegant softbox key light from upper "
               "left, gentle falloff, minimal, no product, no text, no people, photorealistic, 4k"),
    "desk": ("Empty modern minimalist wooden desk surface, soft morning window light, blurred "
             "green plant bokeh in the background, warm premium lifestyle backdrop, top-down room "
             "light, no product, no text, no people, photorealistic, 4k"),
    "gradient": ("Empty luxury studio backdrop, smooth deep violet to black gradient, subtle "
                 "spotlight pool on a glossy reflective floor, high-end minimal, no product, no "
                 "text, no people, photorealistic, 4k"),
    "concrete": ("Empty premium microcement podium on polished concrete, soft diffused studio "
                 "light, muted neutral tones, architectural minimal product backdrop, no product, "
                 "no text, no people, photorealistic, 4k"),
}


def _cover(img, size):
    """Resize+crop an image to fully cover `size` (like CSS object-fit: cover)."""
    from PIL import Image
    W, H = size
    r = max(W / img.width, H / img.height)
    im = img.resize((int(img.width * r) + 1, int(img.height * r) + 1), Image.LANCZOS)
    x = (im.width - W) // 2
    y = (im.height - H) // 2
    return im.crop((x, y, x + W, y + H)).convert("RGBA")


def _public_url(path: str) -> str:
    from app.config import settings
    return f"{settings.supabase_url.rstrip('/')}/storage/v1/object/public/{_BUCKET}/{path}"


def get_backdrop(style: str):
    """Return a premium backdrop PIL image for `style`, generating + caching it in the bucket
    on first use (reused thereafter). None if generation fails."""
    import requests
    from PIL import Image
    cli = get_client()
    path = f"backdrops/{style}.png"
    url = _public_url(path)
    try:  # reuse cached backdrop if present
        r = requests.get(url, timeout=20)
        if r.status_code == 200 and len(r.content) > 5000:
            return Image.open(io.BytesIO(r.content)).convert("RGBA")
    except Exception:  # noqa: BLE001
        pass
    from app import agnes
    gen = agnes.generate_image(SCENES.get(style, SCENES["marble"]))
    if not gen:
        return None
    try:
        data = requests.get(gen, timeout=90).content
        try:
            cli.storage.create_bucket(_BUCKET, options={"public": True})
        except Exception:  # noqa: BLE001
            pass
        cli.storage.from_(_BUCKET).upload(path, data, {"content-type": "image/png", "upsert": "true"})
        return Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception as e:  # noqa: BLE001
        log.warning("backdrop cache failed: %s", str(e)[:120])
        try:
            return Image.open(io.BytesIO(data)).convert("RGBA")
        except Exception:  # noqa: BLE001
            return None


def compose(product, backdrop, size: tuple[int, int]):
    """Composite a knocked-out product onto a backdrop with a contact shadow + reflection."""
    from PIL import Image, ImageDraw, ImageFilter
    W, H = size
    canvas = _cover(backdrop, size)
    # scale product to sit on the lower 'surface' band
    p = product.copy()
    p.thumbnail((int(W * 0.62), int(H * 0.46)), Image.LANCZOS)
    px = (W - p.width) // 2
    base_y = int(H * 0.72)          # where the product 'stands'
    py = base_y - p.height

    # contact shadow — soft dark ellipse under the base
    shadow = Image.new("RGBA", size, (0, 0, 0, 0))
    sw = int(p.width * 0.8)
    ImageDraw.Draw(shadow).ellipse(
        (px + (p.width - sw) // 2, base_y - 18, px + (p.width + sw) // 2, base_y + 34),
        fill=(0, 0, 0, 120))
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(22)))

    # reflection — flipped product, fading out downward, low opacity
    refl = p.transpose(Image.FLIP_TOP_BOTTOM)
    fade = Image.new("L", refl.size, 0)
    for yy in range(refl.height):
        fade.putpixel((0, yy), 0)  # placeholder to init
    grad = Image.new("L", (1, refl.height))
    for yy in range(refl.height):
        grad.putpixel((0, yy), max(0, int(70 * (1 - yy / refl.height))))
    ralpha = grad.resize(refl.size)
    r2 = refl.copy()
    r2.putalpha(Image.composite(ralpha, Image.new("L", refl.size, 0), refl.split()[-1]))
    canvas.alpha_composite(r2, (px, base_y))

    canvas.alpha_composite(p, (px, py))
    return canvas


def _overlay_branding(canvas, item: dict, template: str):
    """Light-on-dark branding for the scene STILL: brand top, badge, price chip, CTA bar."""
    from PIL import Image, ImageDraw
    from app.video_gen import PURPLE, _badge, _font, _v_gradient
    W, H = canvas.size
    d = ImageDraw.Draw(canvas)
    # subtle top + bottom scrims so text is always legible on any scene
    top = Image.new("RGBA", (W, 240), (0, 0, 0, 0))
    for yy in range(240):
        ImageDraw.Draw(top).line((0, yy, W, yy), fill=(10, 6, 24, int(150 * (1 - yy / 240))))
    canvas.alpha_composite(top, (0, 0))
    bot = Image.new("RGBA", (W, 300), (0, 0, 0, 0))
    for yy in range(300):
        ImageDraw.Draw(bot).line((0, yy, W, yy), fill=(10, 6, 24, int(180 * (yy / 300))))
    canvas.alpha_composite(bot, (0, H - 300))
    d = ImageDraw.Draw(canvas)

    d.text((60, 54), "YQ BAHRAIN", font=_font(56), fill=(255, 255, 255))
    d.text((62, 122), "PREMIUM VFAN ACCESSORIES", font=_font(28, bold=False), fill=(210, 200, 240))
    lbl, col = {"new_arrival": ("NEW IN", PURPLE), "price_drop": (
        f"-{int(item.get('discount_pct') or 30)}% OFF", (220, 38, 38))}.get(
        template, ("TOP PICK", (255, 255, 255)))
    _badge(d, (W - 60 - int(len(lbl) * 26), 66), lbl, fill=col,
           size=44) if col != (255, 255, 255) else _badge(d, (W - 250, 66), lbl, fill=PURPLE, size=44)

    code = str(item.get("item_code") or "")
    spec = str(item.get("display_name") or item.get("spec") or "").replace("\n", " ")[:64]
    d.text((60, H - 260), code, font=_font(60), fill=(255, 255, 255))
    d.text((62, H - 190), spec, font=_font(32, bold=False), fill=(214, 206, 232))

    price = item.get("price_bhd")
    if price is not None:
        label = f"BHD {float(price):.3f}"
        ch = 100
        cw = int(d.textlength(label, font=_font(64)) + 76)
        chip = _v_gradient((cw, ch), (124, 58, 237), (76, 29, 149))
        mask = Image.new("L", (cw, ch), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, cw - 1, ch - 1), radius=ch // 2, fill=255)
        canvas.paste(chip, (60, H - 130), mask)
        ImageDraw.Draw(canvas).text((60 + 38, H - 130 + 18), label, font=_font(64),
                                    fill=(255, 255, 255))
    wa = os.getenv("WA_HUMAN_NUMBER", "")
    cta = f"Order on WhatsApp{(' +' + wa) if wa else ''}"
    f = _font(34)
    tw = d.textlength(cta, font=f)
    ImageDraw.Draw(canvas).text((W - 60 - tw, H - 100), cta, font=f, fill=(255, 255, 255))
    return canvas


def build_scene_ad(item: dict, template: str = "hero_card",
                   size: tuple[int, int] = (1080, 1350), style: str | None = None):
    """Return (branded_still_png, clean_composite_png) or (None, None) on failure.
    clean_composite has NO text (safe for Agnes image-to-video)."""
    from PIL import Image
    from app.video_gen import _fetch_photo, _knockout_bg
    photo = _fetch_photo(item.get("product_image_url") or "")
    if photo is None:
        return None, None
    product = _knockout_bg(photo)
    # require a real cut-out (studio-white shot); otherwise scenes look wrong
    if product.mode != "RGBA" or product.split()[-1].getextrema()[0] != 0:
        return None, None
    style = style or random.choice(list(SCENES))
    backdrop = get_backdrop(style)
    if backdrop is None:
        return None, None
    clean = compose(product, backdrop, size)
    still = _overlay_branding(clean.copy(), item, template)

    def _png(im):
        b = io.BytesIO()
        im.convert("RGB").save(b, "PNG", optimize=True)
        return b.getvalue()
    return _png(still), _png(clean)
