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
        made.append({"item_code": code, "template": template, "kind": "image", "url": url,
                     "photo_url": it.get("product_image_url")})
        cards_for_video.append(png)

    # ── Video: Agnes AI (premium image-to-video) → FFmpeg ken-burns fallback ──
    from app import agnes
    video_made, video_note = None, ""
    if made:
        hero = made[0]
        vcap_en = (f"This week at YQ Bahrain 📱 {hero['item_code']} and more — genuine VFAN "
                   f"accessories at trade prices.\n" + (f"Catalog: {link}\n" if link else "")
                   + _hashtags())
        vcap_ar = f"جديد هذا الأسبوع لدى YQ البحرين — إكسسوارات VFAN بأسعار الجملة."
        if agnes.enabled():
            vprompt = (
                "High-end commercial product advertisement for a premium mobile accessory. "
                "Slow cinematic dolly and gentle orbit around the product, elegant studio "
                "lighting with soft reflections and shallow depth of field, glossy reflective "
                "surface, subtle floating motion, luxury tech aesthetic, photorealistic, "
                "ultra sharp, 4k, smooth 24fps motion, professional colour grade")
            # Animate the CLEAN product photo (image-to-video). The text-heavy branded card
            # trips Agnes' content moderation and warps overlaid text — the caption carries
            # the price/branding instead.
            vsrc = hero.get("photo_url") or hero["url"]
            vid = agnes.start_video(vprompt, image_url=vsrc, seconds=6, fps=24, portrait=True)
            if vid:
                get_client().table("social_posts").insert({
                    "campaign": "hero", "item_code": hero["item_code"], "kind": "video",
                    "template": "reel", "caption_en": vcap_en, "caption_ar": vcap_ar,
                    "media_url": "", "status": "rendering",
                    "platforms": ["instagram", "facebook", "tiktok"],
                    "meta": {"agnes_video_id": vid, "source_url": hero["url"]},
                }).execute()
                video_note = " + 1 AI video rendering (Agnes, ~2 min)"
        elif len(cards_for_video) >= 2 and ffmpeg_available():
            names = ", ".join(m["item_code"] for m in made[:3])
            mp4 = make_video(cards_for_video[:3],
                             f"New at YQ Bahrain: {names} — genuine VFAN accessories at trade "
                             f"prices. Message us on WhatsApp to order today.")
            if mp4:
                vurl = _upload(mp4, f"videos/{ts}-reel.mp4", "video/mp4")
                if vurl:
                    get_client().table("social_posts").insert({
                        "campaign": "hero", "item_code": hero["item_code"], "kind": "video",
                        "template": "reel", "caption_en": vcap_en, "caption_ar": vcap_ar,
                        "media_url": vurl, "platforms": ["instagram", "facebook", "tiktok"],
                    }).execute()
                    video_made, video_note = vurl, " + 1 video reel"

    total_active = (get_client().table("catalog_items").select("item_code", count="exact")
                    .eq("is_active", True).execute().count or 0)
    missing = max(0, total_active - len(items))
    engine = ("Agnes AI" if agnes.enabled() else
              ("FFmpeg" if ffmpeg_available() else "no video engine"))
    return {
        "count": len(made) + (1 if (video_made or "rendering" in video_note) else 0),
        "summary": (f"Rendered {len(made)} picture ads{video_note} → drafts in Marketing Studio "
                    f"for approval (video engine: {engine}). "
                    f"{missing} active items still have no photo."),
        "ads": made,
        "video_url": video_made,
        "items_missing_photos": missing,
    }


MAX_POLL_ATTEMPTS = 40  # ~40 hourly/tab polls before we give up (Agnes usually done in ~90s)


def _finalize_video(row: dict, url: str) -> None:
    """Copy a completed Agnes MP4 into our public bucket and flip the row to a draft."""
    final_url = url
    try:
        import re
        import requests
        data = requests.get(url, timeout=120).content
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        safe = re.sub(r"[^A-Za-z0-9_-]+", "_", str(row.get("item_code") or "item"))
        up = _upload(data, f"videos/agnes-{safe}-{stamp}.mp4", "video/mp4")
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
