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


import re as _re
_EMOJI = _re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF"
    "←-⇿⬀-⯿ -⁯️⃣]+")


def _no_emoji(s: str) -> str:
    """Strip emoji/symbols that the DejaVu/Noto fonts render as tofu boxes when BURNED into
    an image. Emojis stay in the post captions (rendered natively by Instagram/TikTok)."""
    return _EMOJI.sub("", s or "").replace("  ", " ").strip()


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


def _knockout_bg(photo):
    """Make a studio-white product photo's background transparent so it floats on the
    gradient (the 'million-dollar' look). Flood-fills from the corners, so interior
    white parts of the product (e.g. a white cable) are preserved. No-op if the photo
    isn't on a near-white background (already transparent PNG / dark bg)."""
    try:
        import numpy as np
        from PIL import Image, ImageDraw
        im = photo.convert("RGB")
        corners = [im.getpixel(p) for p in
                   [(0, 0), (im.width - 1, 0), (0, im.height - 1), (im.width - 1, im.height - 1)]]
        if not all(min(c) > 232 for c in corners):
            return photo  # not a white-studio shot → leave untouched
        seed = (255, 0, 255)
        work = im.copy()
        for pt in [(1, 1), (im.width - 2, 1), (1, im.height - 2), (im.width - 2, im.height - 2)]:
            ImageDraw.floodfill(work, pt, seed, thresh=36)
        arr, orig = np.array(work), np.array(im)
        bg = np.all(arr == seed, axis=-1)
        alpha = np.where(bg, 0, 255).astype("uint8")
        return Image.fromarray(np.dstack([orig, alpha]), "RGBA")
    except Exception:  # noqa: BLE001
        return photo


def _v_gradient(size: tuple[int, int], top: tuple, bottom: tuple):
    """Vertical linear gradient background (premium studio feel)."""
    from PIL import Image
    W, H = size
    grad = Image.new("RGB", (1, H))
    for y in range(H):
        t = y / max(H - 1, 1)
        grad.putpixel((0, y), tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    return grad.resize((W, H)).convert("RGBA")


def _wrap(draw, text: str, font, max_w: int, max_lines: int = 2) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = w
            if len(lines) == max_lines:
                break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    if lines and draw.textlength(lines[-1], font=font) > max_w:
        while lines[-1] and draw.textlength(lines[-1] + "…", font=font) > max_w:
            lines[-1] = lines[-1][:-1]
        lines[-1] += "…"
    return lines


def make_ad_card(item: dict, template: str = "hero_card",
                 size: tuple[int, int] = AD_SIZE) -> bytes | None:
    """One premium ad card (PNG): studio gradient, soft product glow + grounded shadow,
    refined type and a gradient price chip. Templates: hero_card | price_drop | new_arrival."""
    from PIL import Image, ImageDraw, ImageFilter
    photo = _fetch_photo(item.get("product_image_url") or "")
    if photo is None:
        return None
    photo = _knockout_bg(photo)   # float the product on the gradient (no white box)
    W, H = size
    canvas = _v_gradient(size, (255, 255, 255), (238, 231, 250))
    d = ImageDraw.Draw(canvas)

    # top accent hairline
    d.rectangle((0, 0, W, 10), fill=PURPLE)

    # brand lockup
    d.text((60, 54), "YQ BAHRAIN", font=_font(58), fill=INK)
    d.text((62, 128), "PREMIUM VFAN ACCESSORIES", font=_font(30, bold=False), fill=PURPLE)
    badge = {"new_arrival": ("NEW IN", PURPLE), "price_drop": (
        f"-{int(item.get('discount_pct') or 30)}% OFF", (220, 38, 38))}.get(
        template, ("TOP PICK", (17, 24, 39)))
    _badge(d, (W - 60 - int(len(badge[0]) * 26), 66), badge[0], fill=badge[1], size=46)

    # product stage — soft radial glow + grounded elliptical shadow behind the hero shot
    cx, cy = W // 2, int(H * 0.37)
    glow = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((cx - 420, cy - 420, cx + 420, cy + 420), fill=(124, 58, 237, 40))
    canvas.alpha_composite(glow.filter(ImageFilter.GaussianBlur(95)))
    shadow = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).ellipse((cx - 280, int(H * 0.585), cx + 280, int(H * 0.63)),
                                   fill=(60, 40, 100, 75))
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(28)))
    _paste_center(canvas, photo, (120, 200, W - 120, int(H * 0.60)))

    # info zone
    code = str(item.get("item_code") or "")
    spec = str(item.get("display_name") or item.get("spec") or "").replace("\n", " ")
    y = int(H * 0.645)
    d.text((60, y), code, font=_font(66), fill=INK)
    for i, line in enumerate(_wrap(d, spec, _font(34, bold=False), W - 120, 2)):
        d.text((62, y + 90 + i * 44), line, font=_font(34, bold=False), fill=(107, 100, 128))

    # premium price chip (gradient pill) — sits above the CTA bar with a clear gap
    price, promo = item.get("price_bhd"), item.get("promo_price_bhd")
    py = int(H * 0.775)
    if price is not None:
        show = promo if (template == "price_drop" and promo) else price
        label = f"BHD {float(show):.3f}"
        ch = 104
        cw = int(d.textlength(label, font=_font(68)) + 80)
        chip = _v_gradient((cw, ch), (124, 58, 237), (76, 29, 149))
        mask = Image.new("L", (cw, ch), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, cw - 1, ch - 1), radius=ch // 2, fill=255)
        canvas.paste(chip, (60, py), mask)
        d.text((60 + 40, py + 18), label, font=_font(68), fill=(255, 255, 255))
        if template == "price_drop" and promo and price:
            ox = 60 + cw + 28
            old = f"BHD {float(price):.3f}"
            d.text((ox, py + 32), old, font=_font(40, bold=False), fill=(150, 145, 165))
            ow = d.textlength(old, font=_font(40, bold=False))
            d.line((ox, py + 56, ox + ow, py + 56), fill=(220, 38, 38), width=5)

    # CTA bar
    wa = os.getenv("WA_HUMAN_NUMBER", "")
    cta = f"Order on WhatsApp{(' +' + wa) if wa else ''}  ·  Trade prices"
    d.rounded_rectangle((60, H - 120, W - 60, H - 42), radius=22, fill=INK)
    f = _font(38)
    d.text(((W - d.textlength(cta, font=f)) / 2, H - 108), cta, font=f, fill=(255, 255, 255))

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
    # broad + local + niche tags for discovery / follower growth
    return ("#Bahrain #البحرين #Manama #MobileAccessories #اكسسوارات_موبايل #VFAN #YQBahrain "
            "#tech #gadgets #typec #fastcharging #WholesaleBahrain #بحرين #عروض #TradePrice")


def _catalog_link() -> str:
    try:
        from app.agents import _catalog_link as _cl
        return _cl()
    except Exception:  # noqa: BLE001
        return ""


def _engaging_caption(item: dict, kind: str = "image") -> tuple[str, str]:
    """A scroll-stopping, follower-growing caption (hook + follow CTA + order CTA + tags),
    EN + AR. LLM-written with a safe template fallback."""
    name = str(item.get("display_name") or item.get("spec") or item.get("item_code") or "").split(" (")[0]
    price = item.get("price_bhd")
    link = _catalog_link()
    hook = ""
    try:
        from app.llm_router import chat
        hook = (chat([
            {"role": "system", "content":
                "You are a witty social-media manager for YQ Bahrain, a VFAN mobile-accessories "
                "brand in Bahrain. Write ONE short scroll-stopping Instagram/TikTok caption "
                "(max 2 lines) with a strong hook and 1-2 emojis. Make people want to FOLLOW and "
                "order. Do NOT include hashtags or a price. Reply with the caption text only."},
            {"role": "user", "content": f"Product: {name}. Highlight the benefit, be punchy."},
        ], tier=1, task="write", max_tokens=90) or "").strip().strip('"')
    except Exception:  # noqa: BLE001
        hook = ""
    if not (10 < len(hook) < 260):
        hook = f"Upgrade your everyday carry with the {name} ⚡"
    price_line = f"💥 Trade price BHD {float(price):.3f}\n" if price is not None else ""
    en = (f"{hook}\n{price_line}📲 Follow @yqbahrain for daily deals · DM/WhatsApp to order"
          + (f"\n🛒 {link}" if link else "") + f"\n\n{_hashtags()}")
    ar = (f"{name} — الأفضل من YQ البحرين ⚡\n"
          + (f"💥 سعر الجملة {float(price):.3f} د.ب\n" if price is not None else "")
          + "📲 تابعونا لأحدث العروض · راسلونا واتساب للطلب"
          + (f"\n🛒 {link}" if link else ""))
    return en, ar


def content_engine(items_per_run: int = 4) -> dict:
    """Agent content_engine — premium SCENE ads (real product composited into an AI studio
    scene) + engaging captions + a cinematic Agnes video, queued as social_posts drafts."""
    from app.db_read import exec_sql
    from app import agnes, scene

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
    link = _catalog_link()

    made, skipped, scenes_made = [], 0, 0
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
        # premium AI-scene ad; fall back to the studio gradient card if no clean cut-out
        still, clean = scene.build_scene_ad(item, template, (1080, 1350))
        used_scene = still is not None
        if still is None:
            still = make_ad_card(item, template)
        if still is None:
            skipped += 1
            continue
        url = _upload(still, f"ads/{ts}-{code}-{template}.png", "image/png")
        if not url:
            continue
        clean_url = _upload(clean, f"scenes/{ts}-{code}.png", "image/png") if clean else None
        if used_scene:
            scenes_made += 1
        cap_en, cap_ar = _engaging_caption(item)
        get_client().table("social_posts").insert({
            "campaign": "hero", "item_code": code, "kind": "image", "template": template,
            "format": "feed", "lang": "en", "caption_en": cap_en, "caption_ar": cap_ar,
            "media_url": url, "platforms": ["instagram", "facebook"],
            "meta": {"price_bhd": prices.get(code), "scene": used_scene, "scene_url": clean_url},
        }).execute()
        made.append({"item_code": code, "template": template, "url": url, "item": item,
                     "photo_url": it.get("product_image_url"), "scene_url": clean_url})

    # ── Cinematic video: animate the in-scene composite (premium) via Agnes ──
    from app import agnes
    video_note = ""
    if made and agnes.enabled():
        hero = made[0]
        vcap_en, vcap_ar = _engaging_caption(hero["item"], kind="video")
        vprompt = (
            "High-end commercial product advertisement, cinematic slow dolly and gentle orbit "
            "around the product on its surface, elegant studio lighting, soft reflections, "
            "shallow depth of field, luxury tech aesthetic, photorealistic, ultra sharp, smooth "
            "24fps camera motion, professional colour grade")
        vsrc = hero.get("scene_url") or hero.get("photo_url") or hero["url"]
        vid = agnes.start_video(vprompt, image_url=vsrc, seconds=6, fps=24, portrait=True)
        if vid:
            price = hero["item"].get("price_bhd")
            script = (f"Meet the {str(hero['item'].get('display_name') or hero['item_code']).split(' (')[0]} "
                      f"from YQ Bahrain. Premium VFAN quality" +
                      (f", trade price {price:.3f} dinars" if price else "") +
                      ". Message us on WhatsApp to order today.")
            get_client().table("social_posts").insert({
                "campaign": "hero", "item_code": hero["item_code"], "kind": "video",
                "template": "reel", "format": "reel", "lang": "en",
                "caption_en": vcap_en, "caption_ar": vcap_ar, "media_url": "", "status": "rendering",
                "platforms": ["instagram", "facebook", "tiktok"],
                "meta": {"agnes_video_id": vid, "source_url": hero["url"],
                         "reel_item": {"item_code": hero["item_code"],
                                       "name": str(hero["item"].get("display_name") or hero["item_code"]),
                                       "price_bhd": price, "catalog_link": link},
                         "script_en": script},
            }).execute()
            video_note = " + 1 cinematic AI video rendering (~2 min)"

    total_active = (get_client().table("catalog_items").select("item_code", count="exact")
                    .eq("is_active", True).execute().count or 0)
    missing = max(0, total_active - len(items))
    return {
        "count": len(made) + (1 if video_note else 0),
        "summary": (f"Rendered {len(made)} premium ads ({scenes_made} in AI studio scenes)"
                    f"{video_note} → drafts in Marketing Studio for approval. "
                    f"{missing} active items still have no photo."),
        "ads": [{"item_code": m["item_code"], "url": m["url"]} for m in made],
        "items_missing_photos": missing,
    }


MAX_POLL_ATTEMPTS = 40  # ~40 hourly/tab polls before we give up (Agnes usually done in ~90s)
REEL = (1080, 1920)     # 9:16 (assembly runs async in content_poll, not a blocking request)


def _reel_card(reel: dict, kind: str):
    """Full-frame branded intro/outro card (PIL) for the reel."""
    from PIL import Image, ImageDraw
    W, H = REEL
    canvas = _v_gradient(REEL, (26, 20, 48), (12, 8, 26))
    d = ImageDraw.Draw(canvas)
    ImageDraw.Draw(canvas).ellipse((W // 2 - 380, H // 2 - 500, W // 2 + 380, H // 2 - 60),
                                   fill=(124, 58, 237, 30))
    from PIL import ImageFilter
    canvas = canvas.filter(ImageFilter.GaussianBlur(0))
    d = ImageDraw.Draw(canvas)
    if kind == "intro":
        big = _font(120)
        d.text(((W - d.textlength("YQ BAHRAIN", font=big)) / 2, H * 0.40), "YQ BAHRAIN",
               font=big, fill=(255, 255, 255))
        sub = "PREMIUM VFAN ACCESSORIES"
        d.text(((W - d.textlength(sub, font=_font(46, bold=False))) / 2, H * 0.40 + 150), sub,
               font=_font(46, bold=False), fill=(200, 190, 235))
    else:  # outro
        t1 = "Order on WhatsApp"
        d.text(((W - d.textlength(t1, font=_font(84))) / 2, H * 0.36), t1, font=_font(84),
               fill=(255, 255, 255))
        wa = os.getenv("WA_HUMAN_NUMBER", "")
        if wa:
            t2 = f"+{wa}"
            d.text(((W - d.textlength(t2, font=_font(72))) / 2, H * 0.36 + 120), t2,
                   font=_font(72), fill=(37, 211, 102))
        link = (reel or {}).get("catalog_link") or ""
        if link:
            d.text(((W - d.textlength(link, font=_font(38, bold=False))) / 2, H * 0.36 + 240),
                   link, font=_font(38, bold=False), fill=(200, 190, 235))
    b = io.BytesIO()
    canvas.convert("RGB").save(b, "PNG")
    return b.getvalue()


def _reel_overlay(reel: dict, caption: str):
    """Transparent WxH overlay: top caption band + bottom lower-third (item + price + CTA)."""
    from PIL import Image, ImageDraw
    W, H = REEL
    ov = Image.new("RGBA", REEL, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    # top scrim (brand legibility on light frames) + bottom scrim
    tscrim = Image.new("RGBA", (W, 220), (0, 0, 0, 0))
    for yy in range(220):
        ImageDraw.Draw(tscrim).line((0, yy, W, yy), fill=(8, 5, 20, int(150 * (1 - yy / 220))))
    ov.alpha_composite(tscrim, (0, 0))
    scrim = Image.new("RGBA", (W, 560), (0, 0, 0, 0))
    for yy in range(560):
        ImageDraw.Draw(scrim).line((0, yy, W, yy), fill=(8, 5, 20, int(205 * (yy / 560))))
    ov.alpha_composite(scrim, (0, H - 560))
    d = ImageDraw.Draw(ov)
    # brand chip top-left
    d.text((54, 60), "YQ BAHRAIN", font=_font(52), fill=(255, 255, 255))
    # caption (hook) near the bottom, wrapped (emojis stripped — they'd render as boxes)
    cap_font = _font(52)
    lines = _wrap(d, _no_emoji(caption), cap_font, W - 108, 2)
    cy = H - 470
    for ln in lines:
        d.text((54, cy), ln, font=cap_font, fill=(255, 255, 255))
        cy += 66
    # item + price + CTA
    name = _no_emoji(str(reel.get("name") or reel.get("item_code") or "").split(" (")[0])
    d.text((54, H - 300), name, font=_font(48), fill=(230, 224, 245))
    price = reel.get("price_bhd")
    if price is not None:
        label = f"BHD {float(price):.3f}"
        ch = 96
        cw = int(d.textlength(label, font=_font(60)) + 72)
        chip = _v_gradient((cw, ch), (124, 58, 237), (76, 29, 149))
        mask = Image.new("L", (cw, ch), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, cw - 1, ch - 1), radius=ch // 2, fill=255)
        ov.paste(chip, (54, H - 220), mask)
        ImageDraw.Draw(ov).text((54 + 36, H - 220 + 16), label, font=_font(60),
                                fill=(255, 255, 255))
    wa = os.getenv("WA_HUMAN_NUMBER", "")
    cta = f"Order on WhatsApp{(' +' + wa) if wa else ''}"
    d2 = ImageDraw.Draw(ov)
    d2.text((54, H - 108), cta, font=_font(40), fill=(255, 255, 255))
    b = io.BytesIO()
    ov.save(b, "PNG")
    return b.getvalue()


def _music_track() -> str | None:
    """First bundled CC0 track under assets/music/, if any (owner-droppable)."""
    import glob
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "music")
    tracks = sorted(glob.glob(os.path.join(root, "*.mp3")))
    return tracks[0] if tracks else None


def assemble_reel(motion_mp4: bytes, reel: dict, script: str) -> bytes | None:
    """Finish the Agnes motion clip into a branded ad: intro card → motion (with lower-third +
    caption overlay) → outro card, EN voiceover ducked over optional music. Best-effort: any
    failure returns None so the caller keeps the plain motion clip."""
    if not ffmpeg_available() or not motion_mp4:
        return None
    W, H = REEL
    P = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "24", "-preset", "veryfast", "-crf", "23"]
    try:
        with tempfile.TemporaryDirectory() as td:
            def w(name, data):
                p = os.path.join(td, name); open(p, "wb").write(data); return p
            main = w("main.mp4", motion_mp4)
            intro = w("intro.png", _reel_card(reel, "intro"))
            outro = w("outro.png", _reel_card(reel, "outro"))
            overlay = w("ov.png", _reel_overlay(reel, (reel.get("caption") or "").strip()))
            introv, outrov, main2, full = (os.path.join(td, n) for n in
                                           ("intro.mp4", "outro.mp4", "main2.mp4", "full.mp4"))

            def run(cmd):
                r = subprocess.run(cmd, capture_output=True, timeout=240)
                if r.returncode != 0:
                    raise RuntimeError(r.stderr.decode(errors="ignore")[-300:])

            run(["ffmpeg", "-y", "-loop", "1", "-t", "0.7", "-i", intro,
                 "-vf", f"scale={W}:{H}", *P, "-an", introv])
            run(["ffmpeg", "-y", "-loop", "1", "-t", "1.6", "-i", outro,
                 "-vf", f"scale={W}:{H}", *P, "-an", outrov])
            run(["ffmpeg", "-y", "-i", main, "-i", overlay, "-filter_complex",
                 f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}[bg];"
                 f"[bg][1:v]overlay=0:0[v]", "-map", "[v]", *P, "-an", main2])
            # concat (same params → concat demuxer works)
            lst = w("list.txt", ("file '%s'\nfile '%s'\nfile '%s'\n" % (introv, main2, outrov)).encode())
            run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", full])

            # audio: EN voiceover (delayed past the intro) + optional ducked music
            vo = os.path.join(td, "vo.mp3")
            has_vo = bool(script) and _tts(script, vo)
            music = _music_track()
            out = os.path.join(td, "final.mp4")
            if has_vo or music:
                cmd = ["ffmpeg", "-y", "-i", full]
                fc, amaps, idx = [], [], 1
                if has_vo:
                    cmd += ["-i", vo]
                    fc.append(f"[{idx}:a]adelay=700|700,volume=1.6[vo]"); amaps.append("[vo]"); idx += 1
                if music:
                    cmd += ["-stream_loop", "-1", "-i", music]
                    fc.append(f"[{idx}:a]volume=0.10[mus]"); amaps.append("[mus]"); idx += 1
                fc.append(f"{''.join(amaps)}amix=inputs={len(amaps)}:duration=first:dropout_transition=0[a]")
                cmd += ["-filter_complex", ";".join(fc), "-map", "0:v", "-map", "[a]",
                        "-c:v", "copy", "-c:a", "aac", "-shortest", "-movflags", "+faststart", out]
                run(cmd)
            else:
                run(["ffmpeg", "-y", "-i", full, "-c", "copy", "-movflags", "+faststart", out])
            with open(out, "rb") as f:
                return f.read()
    except Exception as e:  # noqa: BLE001
        log.warning("assemble_reel failed (keeping plain clip): %s", str(e)[:220])
        return None


def _finalize_video(row: dict, url: str) -> None:
    """Copy the completed Agnes MP4 into our bucket, ASSEMBLE the branded reel (intro/overlay/
    outro/voiceover/music) if possible, and flip the row to a draft."""
    final_url = url
    try:
        import re
        import requests
        data = requests.get(url, timeout=120).content
        meta = row.get("meta") or {}
        reel = dict(meta.get("reel_item") or {})
        reel["caption"] = (row.get("caption_en") or "").split("\n")[0]
        assembled = assemble_reel(data, reel, meta.get("script_en") or "")
        if assembled:
            data = assembled
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        safe = re.sub(r"[^A-Za-z0-9_-]+", "_", str(row.get("item_code") or "item"))
        tag = "reel" if assembled else "agnes"
        up = _upload(data, f"videos/{tag}-{safe}-{stamp}.mp4", "video/mp4")
        if up:
            final_url = up
    except Exception as e:  # noqa: BLE001
        log.warning("agnes mp4 copy failed: %s", str(e)[:120])
    get_client().table("social_posts").update(
        {"media_url": final_url, "status": "draft",
         "meta": {**(row.get("meta") or {}), "agnes_status": "completed"}}
    ).eq("id", row["id"]).execute()


def resolve_pending_videos(limit: int = 20) -> dict:
    """Agent content_poll — poll Agnes for videos still rendering; when complete, copy the
    MP4 into our public bucket (stable URL + CSP-friendly) and flip the draft to 'draft'.

    Also RECOVERS videos previously marked 'failed' that Agnes actually finished — a
    transient poll blip should never lose a good render. Only gives up after
    MAX_POLL_ATTEMPTS or an explicit Agnes rejection (content policy)."""
    from app import agnes
    if not agnes.enabled():
        return {"count": 0, "summary": "Agnes not configured — no async videos to resolve."}
    rows = (get_client().table("social_posts").select("*")
            .in_("status", ["rendering", "failed"]).eq("kind", "video")
            .limit(limit).execute().data or [])
    done, still, failed = 0, 0, 0
    for row in rows:
        meta = row.get("meta") or {}
        vid = meta.get("agnes_video_id")
        if not vid:
            if row["status"] == "failed":
                continue  # nothing to recover (e.g. old ffmpeg failure)
            continue
        st = agnes.poll_video(vid)
        if st["status"] == "completed" and st.get("url"):
            _finalize_video(row, st["url"])          # recovers 'failed' rows too
            done += 1
        elif st["status"] == "failed":               # explicit rejection (content policy)
            get_client().table("social_posts").update(
                {"status": "failed",
                 "meta": {**meta, "agnes_error": str(st.get("error"))[:200]}}
            ).eq("id", row["id"]).execute()
            failed += 1
        else:                                        # queued / in_progress / transient
            attempts = int(meta.get("poll_attempts") or 0) + 1
            patch = {"meta": {**meta, "poll_attempts": attempts}}
            if row["status"] == "failed":
                patch["status"] = "rendering"        # un-fail: give it back to the poller
            if attempts >= MAX_POLL_ATTEMPTS:
                patch["status"] = "failed"
                patch["meta"] = {**patch["meta"], "agnes_error": "timed out"}
                failed += 1
            else:
                still += 1
            get_client().table("social_posts").update(patch).eq("id", row["id"]).execute()
    return {"count": done, "resolved": done, "pending": still, "failed": failed,
            "summary": (f"Agnes renders: {done} completed and ready to approve, "
                        f"{still} still rendering, {failed} failed.")}
