"""Content engine — picture ads + 9:16 videos from catalog photos (Marketing Phase 3).

100% free/open-source: Pillow composes branded ad cards from the product photos
already in the public `catalog` bucket; FFmpeg (zoompan + xfade) turns the cards
into a 15s vertical video; edge-tts (free Microsoft neural voices) adds an optional
EN voiceover. Captions reuse the `marketing` agent's EN/AR campaign copy.

Output → public `marketing` bucket + a `social_posts` draft row per asset. A human
approves in the Marketing Studio before anything is posted (owner decision).
Missing photos are skipped and reported — never a blank card.
"""
from __future__ import annotations

import io
import logging
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone

from app.config import settings
from app.database import get_client

log = logging.getLogger(__name__)

_BUCKET = "marketing"
PURPLE = (109, 40, 217)        # #6d28d9 — the platform accent
INK = (26, 20, 48)             # #1a1430
PAPER = (250, 249, 252)        # #faf9fc
AD_SIZE = (1080, 1350)         # 4:5 feed post
VIDEO_SIZE = (1080, 1920)      # 9:16 reel/story
SECONDS_PER_CARD = 5

_FONT_DIRS = [
    "C:/Windows/Fonts",                          # local dev (Windows)
    "/usr/share/fonts/truetype/dejavu",          # Railway image (fonts-dejavu-core)
    "/usr/share/fonts/truetype/noto",
]
_FONT_FILES = ["segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf", "NotoSans-Bold.ttf"]
_FONT_FILES_REG = ["segoeui.ttf", "arial.ttf", "DejaVuSans.ttf", "NotoSans-Regular.ttf"]


def _font(size: int, bold: bool = True):
    from PIL import ImageFont
    for d in _FONT_DIRS:
        for f in (_FONT_FILES if bold else _FONT_FILES_REG):
            p = os.path.join(d, f)
            if os.path.exists(p):
                try:
                    return ImageFont.truetype(p, size)
                except Exception:  # noqa: BLE001
                    continue
    return ImageFont.load_default()


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _fetch_photo(url: str):
    """Product photo → RGBA PIL image (None on any failure — caller skips the item)."""
    import requests
    from PIL import Image
    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        return Image.open(io.BytesIO(r.content)).convert("RGBA")
    except Exception as e:  # noqa: BLE001
        log.warning("photo fetch failed (%s): %s", url[:80], e)
        return None


def _paste_center(canvas, photo, box: tuple[int, int, int, int]) -> None:
    """Fit the photo inside `box` keeping aspect, centered, on white matte."""
    from PIL import Image
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    im = photo.copy()
    im.thumbnail((w, h), Image.LANCZOS)
    px = x0 + (w - im.width) // 2
    py = y0 + (h - im.height) // 2
    canvas.paste(im, (px, py), im)


def _badge(draw, xy: tuple[int, int], text: str, fill=PURPLE, size: int = 44) -> None:
    f = _font(size)
    pad = int(size * 0.45)
    bb = draw.textbbox(xy, text, font=f)
    box = (bb[0] - pad, bb[1] - pad // 2, bb[2] + pad, bb[3] + pad // 2)
    draw.rounded_rectangle(box, radius=(box[3] - box[1]) // 2, fill=fill)
    draw.text(xy, text, font=f, fill=(255, 255, 255))


def make_ad_card(item: dict, template: str = "hero_card",
                 size: tuple[int, int] = AD_SIZE) -> bytes | None:
    """One branded ad card (PNG). Templates: hero_card | price_drop | new_arrival.
    `item` needs: item_code, spec/display_name, price_bhd, product_image_url,
    optional promo_price_bhd + discount_pct for price_drop."""
    from PIL import Image, ImageDraw
    photo = _fetch_photo(item.get("product_image_url") or "")
    if photo is None:
        return None
    W, H = size
    canvas = Image.new("RGBA", size, PAPER + (255,))
    d = ImageDraw.Draw(canvas)

    # header band — brand
    d.rectangle((0, 0, W, 8), fill=PURPLE)
    d.text((60, 48), "YQ BAHRAIN", font=_font(56), fill=INK)
    d.text((60, 120), "VFAN mobile accessories · wholesale", font=_font(34, bold=False),
           fill=(107, 100, 128))
    if template == "new_arrival":
        _badge(d, (W - 320, 60), "NEW IN", size=48)
    elif template == "price_drop":
        pct = int(item.get("discount_pct") or 30)
        _badge(d, (W - 340, 60), f"-{pct}% OFF", fill=(220, 38, 38), size=48)
    elif template == "hero_card":
        _badge(d, (W - 360, 60), "TOP PICK", size=48)

    # product photo — the star, ~55% of the canvas on a white card
    card = (60, 200, W - 60, int(H * 0.68))
    d.rounded_rectangle(card, radius=36, fill=(255, 255, 255),
                        outline=(232, 228, 240), width=2)
    _paste_center(canvas, photo, (card[0] + 30, card[1] + 30, card[2] - 30, card[3] - 30))

    # name + spec
    y = int(H * 0.70)
    code = str(item.get("item_code") or "")
    spec = str(item.get("display_name") or item.get("spec") or "").replace("\n", " ")[:80]
    d.text((60, y), code, font=_font(64), fill=INK)
    d.text((60, y + 84), spec, font=_font(38, bold=False), fill=(107, 100, 128))

    # price block
    price = item.get("price_bhd")
    promo = item.get("promo_price_bhd")
    py = y + 160
    if template == "price_drop" and promo and price:
        d.text((60, py + 26), f"BHD {float(price):.3f}", font=_font(44, bold=False),
               fill=(150, 145, 165))
        w = d.textlength(f"BHD {float(price):.3f}", font=_font(44, bold=False))
        d.line((60, py + 52, 60 + w, py + 52), fill=(220, 38, 38), width=4)
        d.text((60 + w + 40, py), f"BHD {float(promo):.3f}", font=_font(84), fill=PURPLE)
    elif price is not None:
        d.text((60, py), f"BHD {float(price):.3f}", font=_font(84), fill=PURPLE)

    # CTA footer
    wa = os.getenv("WA_HUMAN_NUMBER", "")
    cta = f"Order on WhatsApp {('+' + wa) if wa else ''} · Trade prices".strip()
    d.rounded_rectangle((60, H - 130, W - 60, H - 50), radius=20, fill=INK)
    f = _font(38)
    tw = d.textlength(cta, font=f)
    d.text(((W - tw) / 2, H - 118), cta, font=f, fill=(255, 255, 255))

    out = io.BytesIO()
    canvas.convert("RGB").save(out, "PNG", optimize=True)
    return out.getvalue()


# ── Video (FFmpeg ken-burns over ad cards) ────────────────────────────────────

def _tts(text: str, path: str) -> bool:
    """Free Microsoft neural voiceover via edge-tts. Best-effort — silent video is fine."""
    try:
        import asyncio
        import edge_tts

        async def _run():
            await edge_tts.Communicate(text, voice="en-US-AriaNeural", rate="+5%").save(path)
        asyncio.run(_run())
        return os.path.exists(path) and os.path.getsize(path) > 1000
    except Exception as e:  # noqa: BLE001
        log.info("tts skipped: %s", str(e)[:120])
        return False


def make_video(cards: list[bytes], script: str | None = None) -> bytes | None:
    """9:16 MP4 from 2-4 ad cards: slow zoom (ken-burns) per card + crossfades +
    optional voiceover. Rendered with the ffmpeg binary — no Python video deps."""
    if not ffmpeg_available() or not cards:
        return None
    from PIL import Image
    fps, dur = 30, SECONDS_PER_CARD
    with tempfile.TemporaryDirectory() as td:
        # letterbox each 4:5 card onto the 9:16 canvas
        paths = []
        for i, png in enumerate(cards):
            im = Image.open(io.BytesIO(png)).convert("RGB")
            canvas = Image.new("RGB", VIDEO_SIZE, PAPER)
            im.thumbnail((VIDEO_SIZE[0], VIDEO_SIZE[1]), Image.LANCZOS)
            canvas.paste(im, ((VIDEO_SIZE[0] - im.width) // 2, (VIDEO_SIZE[1] - im.height) // 2))
            p = os.path.join(td, f"card{i}.png")
            canvas.save(p, "PNG")
            paths.append(p)

        # per-card zoompan stream, then chain xfades
        n = len(paths)
        inputs: list[str] = []
        for p in paths:
            inputs += ["-loop", "1", "-t", str(dur), "-i", p]
        filters, labels = [], []
        for i in range(n):
            filters.append(
                f"[{i}:v]scale=8000:-1,zoompan=z='min(zoom+0.0008,1.12)':x='iw/2-(iw/zoom/2)'"
                f":y='ih/2-(ih/zoom/2)':d={dur * fps}:s={VIDEO_SIZE[0]}x{VIDEO_SIZE[1]}:fps={fps}"
                f",format=yuv420p[v{i}]")
            labels.append(f"[v{i}]")
        chain = labels[0]
        for i in range(1, n):
            out = f"[x{i}]" if i < n - 1 else "[vout]"
            off = i * dur - 0.5 * i  # each fade eats 0.5s
            filters.append(f"{chain}{labels[i]}xfade=transition=fade:duration=0.5:offset={off:.2f}{out}")
            chain = out
        if n == 1:
            filters.append(f"{labels[0]}null[vout]")

        audio = os.path.join(td, "voice.mp3")
        has_audio = bool(script) and _tts(script or "", audio)
        outp = os.path.join(td, "out.mp4")
        cmd = ["ffmpeg", "-y", *inputs]
        if has_audio:
            cmd += ["-i", audio]
        cmd += ["-filter_complex", ";".join(filters), "-map", "[vout]"]
        if has_audio:
            cmd += ["-map", f"{n}:a", "-c:a", "aac", "-shortest"]
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-movflags", "+faststart", outp]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=300)
            if r.returncode != 0:
                log.warning("ffmpeg failed: %s", r.stderr.decode(errors="ignore")[-400:])
                return None
            with open(outp, "rb") as f:
                return f.read()
        except Exception as e:  # noqa: BLE001
            log.warning("video render failed: %s", e)
            return None


# ── Storage + the content_engine agent ────────────────────────────────────────

def _upload(data: bytes, name: str, content_type: str) -> str | None:
    cli = get_client()
    try:
        cli.storage.create_bucket(_BUCKET, options={"public": True})
    except Exception:  # noqa: BLE001
        pass
    try:
        cli.storage.from_(_BUCKET).upload(name, data, {
            "content-type": content_type, "upsert": "true"})
        return f"{settings.supabase_url.rstrip('/')}/storage/v1/object/public/{_BUCKET}/{name}"
    except Exception as e:  # noqa: BLE001
        log.warning("marketing upload failed: %s", e)
        return None


def _hashtags() -> str:
    return "#Bahrain #MobileAccessories #VFAN #YQBahrain #WholesaleBahrain #TradePrice"


def content_engine(items_per_run: int = 4) -> dict:
    """Agent content_engine — render fresh picture ads + one video from catalog photos,
    caption them with the marketing agent's campaign copy, queue as social_posts drafts."""
    from app.agents import marketing
    from app.db_read import exec_sql

    # items WITH photos, newest first, rotating past what was already rendered
    items = (get_client().table("catalog_items")
             .select("item_code,display_name,spec,category,product_image_url,updated_at")
             .eq("is_active", True).neq("product_image_url", "")
             .not_.is_("product_image_url", "null")
             .order("updated_at", desc=True).limit(200).execute().data or [])
    done = {(r.get("item_code"), r.get("template")) for r in
            (get_client().table("social_posts").select("item_code,template")
             .gte("created_at", (datetime.now(timezone.utc)).strftime("%Y-%m-01"))
             .execute().data or [])}
    prices = {r["item_code"]: r.get("standard_rate") for r in
              (exec_sql("SELECT item_code, standard_rate FROM v_catalog WHERE is_active") or [])}

    camp = marketing()
    campaigns = {c.get("campaign"): c for c in camp.get("campaigns", [])}
    hero_cap = campaigns.get("Hero product push", {})
    link = camp.get("catalog_link") or ""

    made, skipped, cards_for_video = [], 0, []
    ts = datetime.now(timezone.utc).strftime("%Y%m%d")
    for it in items:
        if len(made) >= items_per_run:
            break
        code = it["item_code"]
        template = "new_arrival" if (code, "new_arrival") not in done else "hero_card"
        if (code, template) in done:
            skipped += 1
            continue
        item = {**it, "price_bhd": prices.get(code)}
        png = make_ad_card(item, template)
        if png is None:
            skipped += 1
            continue
        url = _upload(png, f"ads/{ts}-{code}-{template}.png", "image/png")
        if not url:
            continue
        cap_en = (f"{code} — {str(it.get('display_name') or it.get('spec') or '').strip()}. "
                  f"{hero_cap.get('message_en') or 'In stock now at YQ Bahrain.'}\n"
                  f"{('Catalog: ' + link) if link else ''}\n{_hashtags()}")
        cap_ar = (f"{code} — {hero_cap.get('message_ar') or 'متوفر الآن لدى YQ البحرين.'}\n"
                  f"{('الكتالوج: ' + link) if link else ''}")
        get_client().table("social_posts").insert({
            "campaign": "hero", "item_code": code, "kind": "image", "template": template,
            "caption_en": cap_en, "caption_ar": cap_ar, "media_url": url,
            "platforms": ["instagram", "facebook"], "meta": {"price_bhd": prices.get(code)},
        }).execute()
        made.append({"item_code": code, "template": template, "kind": "image", "url": url})
        cards_for_video.append(png)

    video_made = None
    if len(cards_for_video) >= 2 and ffmpeg_available():
        names = ", ".join(m["item_code"] for m in made[:3])
        script = (f"New at YQ Bahrain: {names} — genuine VFAN accessories at trade prices. "
                  f"Message us on WhatsApp to order today.")
        mp4 = make_video(cards_for_video[:3], script)
        if mp4:
            vurl = _upload(mp4, f"videos/{ts}-reel.mp4", "video/mp4")
            if vurl:
                get_client().table("social_posts").insert({
                    "campaign": "hero", "item_code": made[0]["item_code"], "kind": "video",
                    "template": "reel", "caption_en":
                        f"This week at YQ Bahrain 📱 {_hashtags()}\n" + (f"Catalog: {link}" if link else ""),
                    "caption_ar": "جديد هذا الأسبوع لدى YQ البحرين",
                    "media_url": vurl, "platforms": ["instagram", "facebook", "tiktok"],
                }).execute()
                video_made = vurl

    total_active = (get_client().table("catalog_items").select("item_code", count="exact")
                    .eq("is_active", True).execute().count or 0)
    missing = max(0, total_active - len(items))
    return {
        "count": len(made) + (1 if video_made else 0),
        "summary": (f"Rendered {len(made)} picture ads" +
                    (" + 1 video reel" if video_made else
                     ("" if ffmpeg_available() else " (video skipped — ffmpeg not installed)")) +
                    f" → drafts in Marketing Studio for approval. "
                    f"{missing} active items still have no photo." ),
        "ads": made,
        "video_url": video_made,
        "items_missing_photos": missing,
    }
