"""Browser check of the portal's layers (ship 2, items 17 and 18, 30-Oct-2026) — synthetic data only.

    cd web && npm run build && npx vite preview --outDir dist --port 5185 --strictPort
    python web/scripts/portal_layers_check.py [--url http://localhost:5185] [--out <dir>] [--only case,case]

The stand-in of admin_render_check.py: a fake Supabase session in localStorage gets the SPA past its sign-in
check, and EVERY request leaves the browser only to the preview server: the API host (VITE_API_URL in web/.env)
is answered from the invented fixtures below (a photo with a tiny generated PNG), Supabase answers {}, anything
else is aborted. No account, no API, no network. Every shop, rep, code and amount below is invented.

  modal_*          each of the twelve dialogs the seven office pages open (Shop Orders, Catalog x3, Leads,
                   Finds x3, Salesmen x2, Price tracker, Shop rules): it animates in; it is a labelled modal
                   dialog holding the focus; Tab and Shift+Tab stay inside; Escape closes it; it animates
                   out (something of it is still moving 60 ms after Escape); the focus goes back to its opener
  s11_toast_*      a toast raised from an open sheet is on top of it (the point at its centre is the toast)
                   and covers none of the sheet's footer, where the next action is — desktop drawer, phone sheet
  s6_drag          on a phone the sheet follows the finger 1:1, springs back without passing its rest, and a
                   long pull closes it
  s6_enter         the sheet's entrance never passes its resting place (the old curve overshot ~7 px)
  palette_over_sheet  Ctrl K over an open drawer: the palette is on top; Escape closes it alone, then the drawer
  reduced_fade     under reduced motion the sheet fades in and out: an opacity-only animation of 120 ms or more
  toast_order_bar  the rep's catalog with a cart: the toast stack starts above the order bar (its Review button)
  photo_fade       a product photo's tile waits under a tint, then shows; a photo already loaded shows at once

Screenshots go to --out. One JSON line per case; exit 1 if any case failed.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import re
import struct
import sys
import tempfile
import time
import zlib
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright

UID = "00000000-0000-4000-8000-00000000qa02"
ADMIN = {"email": "qa-admin@example.com", "role": "admin", "features": [], "full_name": "QA Admin",
         "must_reset": False, "read_only": False}
REP = {"email": "qa-rep@example.com", "role": "salesman", "features": ["Catalog", "Shop Orders"], "full_name": "QA Rep",
       "must_reset": False, "read_only": False}
TODAY = "2026-10-27"
PHONE = {"viewport": (390, 844), "touch": True, "mobile": True}


def env_value(web: Path, key: str) -> str:
    for line in (web / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip()
    raise SystemExit(f"{key} missing from web/.env")


def fake_session() -> dict:
    now = int(time.time())
    b64 = lambda o: base64.urlsafe_b64encode(json.dumps(o).encode()).decode().rstrip("=")  # noqa: E731
    jwt = f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64({'sub': UID, 'exp': now + 7200, 'role': 'authenticated'})}.x"
    user = {"id": UID, "aud": "authenticated", "role": "authenticated", "email": "qa@example.com",
            "app_metadata": {}, "user_metadata": {}, "created_at": "2026-09-01T00:00:00Z"}
    return {"access_token": jwt, "token_type": "bearer", "expires_in": 7200, "expires_at": now + 7200,
            "refresh_token": "qa-not-a-token", "user": user}


def png(w: int = 8, h: int = 8, rgb=(210, 200, 230)) -> bytes:
    """A tiny solid PNG (the photo every image request gets)."""
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)  # noqa: E731
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


PHOTO = png()
IMG_HOST = "img.example.invalid"

# ── synthetic fixtures (invented codes, names and amounts) ──────────────────────────────────────────────


def order_row(i: int) -> dict:
    return {"id": 10_000 - i, "order_no": f"QA-{10_000 - i}", "status": "new", "customer_name": f"Test Shop {i}",
            "customer_shop": f"Shop {i}", "customer_area": "Area", "salesman_name": "Rep A", "salesman_id": 1,
            "total_bhd": 21.5, "items_count": 3, "units_count": 12, "created_at": "2026-10-20T08:00:00Z",
            "notify_failed": False, "notify_attempts": 1}


def order_detail(actions: list[str]) -> dict:
    o = order_row(0)
    o.update({"status": "confirmed" if "deliver" in actions else "new",
              "lines": [{"id": 1, "item_code": "TST-A", "display_name": "Test cable", "qty": 4, "unit_price_bhd": 1.5,
                         "line_total_bhd": 6.0}], "events": [], "actions": actions, "subtotal_bhd": 21.5,
              "delivery_bhd": 0, "discount_bhd": 0})
    return o


def shop_item(i: int) -> dict:
    return {"item_code": f"QA-CBL-{i}", "display_name": f"QA Example Cable {i}", "category": "QA Cables", "brand": "QA",
            "price_bhd": 1.0 + i / 10, "stock_status": "in_stock", "stock_qty": 50, "moq": 1,
            "thumb_url": f"https://{IMG_HOST}/qa-{i}.png", "product_image_url": None}


BASE_FIX = {
    "/freshness": {"focus_to": TODAY, "focus_label": "27 Oct", "focus_days_behind": 1, "stale": False, "marketplace_live": True},
    "/auth/features": {"features": []},
    "/shop/me": {"salesman": None},
    "/shop/orders": {"orders": [order_row(i) for i in range(12)], "count": 12, "counts": {"new": 12}},
    "/shop/orders/:id": order_detail(["confirm", "cancel"]),
    "/shop/assignment-queue": {"orders": [], "count": 0, "sla_min": 120},
    "/shop/focus/candidates": {"rows": [], "count": 0},
    "/catalog": {"items": [{"item_code": "QA-C1", "spec": "Test cable, one metre", "category": "CABLE", "brand": "VFAN",
                            "standard_rate": 1.5, "b2c_rate": 2.0, "product_image_url": None, "package_image_url": None,
                            "is_active": True, "stock_status": "in_stock"}], "count": 1, "categories": ["CABLE"]},
    "/catalog/share-link": {"url": "/c/qa-not-a-token"},
    "/leads": {"pipeline": {"by_status": {"new": {"leads": 1, "avg_fit": 70}}, "total": 1, "stages": ["new"]},
               "leads": [{"id": 1, "name": "QA Lead Shop", "category": "mobile", "area": "Area", "phone": None, "website": None,
                          "fit_score": 70, "status": "new", "segment": "mobile", "brand": None}], "attribution": ""},
    "/finds": [{"id": 1, "name": "QA find", "status": "new", "image_url": None, "price_bhd": 1.0, "note": "test note",
                "category": "CABLE", "posted_by": "qa@example.com", "posted_at": "2026-10-20T08:00:00Z", "promoted_item_code": None}],
    "/finds/share-link": {"url": "/f/qa-not-a-token"},
    "/shop/salesmen": {"salesmen": [{"id": 1, "name": "Rep A", "referral_code": "rep-a", "is_active": True, "user_email": None}]},
    "/team": {"users": []},
    "/prices/tracker": {"rows": [{"sku_code": "QA-P1", "item_name": "QA test item", "brand": "VFAN", "division": "Accessories",
                                  "category": "CABLE", "sell_now": 2.0, "sell_prev": 2.2, "sell_changed_on": "2026-10-01",
                                  "sell_change_pct": -9.1, "cost_now": 1.0, "cost_prev": 0.9, "last_bought_on": "2026-09-20",
                                  "cost_change_pct": 11.1, "margin_now_pct": 50}],
                        "count": 1, "divisions": ["Accessories"], "brands": ["VFAN"], "categories": ["CABLE"]},
    "/prices/history": {"sku_code": "QA-P1", "events": [], "count": 0, "selling": []},
    "/shop/rules": {"rules": []},
    "/shop/catalog": {"company": "QA Example Co", "mode": "salesman", "prices_updated": TODAY, "stock_as_of": TODAY, "stock_fresh": True,
                      "categories": ["QA Cables"], "items": [shop_item(i) for i in range(1, 7)], "salesmen": [], "offers": [],
                      "campaigns": [], "pairs": [], "settings": {"min_order_bhd": 0, "allow_backorder": True},
                      "me": {"salesman_id": 9001, "salesman_name": "QA Rep One", "is_admin": False}},
}


def answer(path: str, fix: dict):
    if re.fullmatch(r"/shop/orders/\d+", path):
        return fix["/shop/orders/:id"]
    return fix.get(path, {})


async def run_case(base: str, web: Path, out: Path, case: dict) -> dict:
    ref = urlparse(env_value(web, "VITE_SUPABASE_URL")).hostname.split(".")[0]
    api = urlparse(env_value(web, "VITE_API_URL")).netloc
    me = case.get("me", ADMIN)
    fix = {**BASE_FIX, "/me": me, **case.get("fix", {})}
    fail = set(case.get("fail", []))
    hits: list[str] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        vw = case.get("viewport", (1366, 860))
        ctx = await browser.new_context(viewport={"width": vw[0], "height": vw[1]}, reduced_motion=case.get("reduced", "no-preference"),
                                        has_touch=case.get("touch", False), is_mobile=case.get("mobile", False))
        init = {f"sb-{ref}-auth-token": json.dumps(fake_session()), "yq-intro-v2": "done", "yq-role": me["role"],
                "yq-me-v1": json.dumps({"uid": UID, "me": me, "at": int(time.time() * 1000)}), "yq-theme": case.get("theme", "light")}
        await ctx.add_init_script(
            "(() => { if (sessionStorage.getItem('qa-seeded')) return; sessionStorage.setItem('qa-seeded','1');"
            f" const kv = {json.dumps(init)}; for (const k in kv) localStorage.setItem(k, kv[k]); }})()")

        async def router(route):
            u = urlparse(route.request.url)
            if u.netloc == urlparse(base).netloc:
                return await route.continue_()
            cors = {"access-control-allow-origin": "*", "access-control-allow-headers": "*", "access-control-allow-methods": "*"}
            if route.request.method == "OPTIONS":
                return await route.fulfill(status=204, headers=cors)
            if u.netloc == IMG_HOST:
                await asyncio.sleep(case.get("photo_ms", 0) / 1000)
                return await route.fulfill(status=200, headers={**cors, "content-type": "image/png", "cache-control": "max-age=3600"}, body=PHOTO)
            if u.netloc == api:
                hits.append((route.request.method + " " if route.request.method != "GET" else "") + u.path)
                key = u.path if not re.fullmatch(r"/shop/orders/\d+", u.path) else "/shop/orders/:id"
                if key in fail or (route.request.method != "GET" and u.path in fail):
                    return await route.fulfill(status=500, headers={**cors, "content-type": "application/json"},
                                               body=json.dumps({"detail": "Server error", "ref": "qa0002"}))
                body = answer(u.path, fix) if route.request.method == "GET" else {}
                return await route.fulfill(status=200, headers={**cors, "content-type": "application/json"}, body=json.dumps(body))
            if u.netloc.startswith(ref):
                return await route.fulfill(status=200, headers={**cors, "content-type": "application/json"}, body="{}")
            return await route.abort()

        await ctx.route("**/*", router)
        page = await ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.goto(base.rstrip("/") + case["path"], wait_until="domcontentloaded")
        await page.wait_for_timeout(case.get("wait_ms", 3500))
        result = {"case": case["name"], "ok": True, "notes": []}
        try:
            await case["check"](page, result, case)
        except Exception as e:  # noqa: BLE001
            result["ok"] = False
            result["notes"].append(f"error: {str(e).splitlines()[0][:300]}")
        if errors:
            result["ok"] = False
            result["notes"].append(f"page errors: {errors[:3]}")
        await page.screenshot(path=str(out / f"{case['name']}.png"), full_page=False)
        result["api_calls"] = len(hits)
        await page.unroute_all(behavior="ignoreErrors")
        await ctx.unroute_all(behavior="ignoreErrors")
        await browser.close()
    return result


def fail(result: dict, note: str) -> None:
    result["ok"] = False
    result["notes"].append(note)


# ── in-page helpers ─────────────────────────────────────────────────────────────────────────────────────

# the open dialog's state: is there one, is it a labelled modal, where is the focus
DIALOG_STATE = """() => {
  const d = document.querySelector('[role="dialog"]')
  if (!d) return null
  const label = d.getAttribute('aria-labelledby')
  const named = label ? (document.getElementById(label)?.textContent || '').trim() : (d.getAttribute('aria-label') || '')
  return { modal: d.getAttribute('aria-modal') === 'true', named, focusInside: d.contains(document.activeElement),
           moving: d.getAnimations().filter((a) => a.playState === 'running').length }
}"""

# anything still animating whose element holds `text` (the exit of a dialog that has just closed)
LEAVING = """(text) => document.getAnimations().filter((a) => a.playState === 'running' && a.effect && a.effect.target
  && (a.effect.target.textContent || '').includes(text)).length"""


async def open_dialog(page, opener, result: dict) -> dict | None:
    """Click `opener` (marked first, to check the focus comes back to it) and read the dialog at once."""
    await opener.scroll_into_view_if_needed()
    await opener.evaluate("el => { document.querySelectorAll('[data-qa-opener]').forEach((x) => x.removeAttribute('data-qa-opener')); el.setAttribute('data-qa-opener', '') }")
    await opener.click()
    await page.wait_for_timeout(40)
    st = await page.evaluate(DIALOG_STATE)
    if not st:
        fail(result, "no role=dialog after opening")
        return None
    if not st["moving"]:
        fail(result, "the dialog does not animate in")
    await page.wait_for_timeout(450)
    return await page.evaluate(DIALOG_STATE)


async def modal_check(page, result: dict, case: dict) -> None:
    opener = case["opener"](page)
    st = await open_dialog(page, opener, result)
    if not st:
        return
    title = st["named"]
    result["notes"].append(f"dialog {title!r}")
    if not st["modal"] or not title:
        fail(result, f"not a labelled modal dialog: {st}")
    if not st["focusInside"]:
        fail(result, "the focus is not inside the dialog")
    for key, n in (("Tab", 14), ("Shift+Tab", 14)):
        for i in range(n):
            await page.keyboard.press(key)
            if not await page.evaluate("() => !!document.querySelector('[role=dialog]')?.contains(document.activeElement)"):
                fail(result, f"{key} #{i + 1} left the dialog")
                break
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(60)
    leaving = await page.evaluate(LEAVING, title) if title else 0
    if not leaving:
        fail(result, "nothing of the dialog animates out after Escape")
    await page.wait_for_timeout(500)
    still = await page.evaluate("() => document.querySelectorAll('[role=dialog]').length")
    if still:
        fail(result, "Escape did not close the dialog")
    if case.get("focus_back", True):
        back = await page.evaluate("() => !!document.activeElement && document.activeElement.hasAttribute('data-qa-opener')")
        if not back:
            who = await page.evaluate("() => document.activeElement ? document.activeElement.outerHTML.slice(0, 80) : null")
            fail(result, f"the focus did not go back to the opener (it is on {who})")


def by_role(name: str, exact: bool = True):
    return lambda page: page.get_by_role("button", name=name, exact=exact).first


MODALS = [
    ("modal_shop_orders_drawer", "/shop-orders", lambda page: page.get_by_role("button", name="QA-10000", exact=True).first, {}),
    ("modal_catalog_detail", "/catalog", lambda page: page.locator("main .aspect-square.cursor-pointer").first, {"focus_back": False}),
    ("modal_catalog_edit", "/catalog", by_role("Add item"), {}),
    ("modal_catalog_share", "/catalog", by_role("Share"), {}),
    ("modal_leads_edit", "/leads", by_role("Edit"), {}),
    ("modal_finds_edit", "/finds", by_role("Edit"), {}),
    ("modal_finds_promote", "/finds", by_role("Promote"), {}),
    ("modal_finds_share", "/finds", by_role("Share"), {}),
    ("modal_salesmen_add", "/salesmen", by_role("Add salesman"), {}),
    ("modal_salesmen_qr", "/salesmen", lambda page: page.get_by_title("Show QR").first, {}),
    ("modal_prices_history", "/prices", lambda page: page.get_by_role("button", name=re.compile(r"^QA-P1")).first, {}),
    ("modal_shop_rules_rule", "/shop-rules", by_role("New rule"), {}),
]


# ── S11: a toast raised from an open sheet ──────────────────────────────────────────────────────────────

TOAST_VS_SHEET = """() => {
  const t = [...document.querySelectorAll('[role="status"]')].find((x) => x.textContent.trim().length > 0 && x.getBoundingClientRect().height > 0)
  const d = document.querySelector('[role="dialog"]')
  if (!t || !d) return { toast: !!t, dialog: !!d }
  const r = t.getBoundingClientRect()
  const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)
  // the sheet's footer: its last child when that sits under the scroller (the pinned actions)
  const kids = [...d.children]
  const foot = kids.length > 2 ? kids[kids.length - 1].getBoundingClientRect() : null
  const overlap = (a, b) => !!a && !!b && a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom
  return { toast: true, dialog: true, text: t.textContent.trim().slice(0, 60), onTop: !!hit && t.contains(hit),
           coversFooter: overlap(r, foot), toastRect: [r.left, r.top, r.right, r.bottom].map(Math.round),
           footRect: foot && [foot.left, foot.top, foot.right, foot.bottom].map(Math.round) }
}"""


async def s11_check(page, result: dict, case: dict) -> None:
    await page.get_by_role("button", name="QA-10000", exact=True).first.click()
    await page.wait_for_timeout(1200)
    await page.locator('[role="dialog"]').get_by_role("button", name=re.compile(r"^\s*Delivered")).first.click()
    await page.wait_for_timeout(700)
    st = await page.evaluate(TOAST_VS_SHEET)
    result["notes"].append(json.dumps(st))
    if not st.get("toast") or not st.get("dialog"):
        return fail(result, "need an open sheet and a toast")
    if not st["onTop"]:
        fail(result, "the toast is under the sheet")
    if st["coversFooter"]:
        fail(result, "the toast covers the sheet's footer (its next action)")


# ── S6: the drag and the entrance ───────────────────────────────────────────────────────────────────────

TOPS = """(ms) => new Promise((done) => {
  const d = document.querySelector('[role="dialog"]'); const tops = []; const t0 = performance.now()
  const step = () => { if (d) tops.push(d.getBoundingClientRect().top); if (performance.now() - t0 < ms) requestAnimationFrame(step); else done(tops) }
  requestAnimationFrame(step)
})"""


async def phone_sheet(page):
    """An order's sheet on a phone (the drawer becomes a bottom sheet under 640 px)."""
    await page.get_by_role("button", name="QA-10000", exact=True).first.click()


async def s6_enter(page, result: dict, case: dict) -> None:
    # the sampler runs from before the tap, so the entrance's first frames are in it
    await page.evaluate("""() => { window.__tops = []; const t0 = performance.now()
      const step = () => { const d = document.querySelector('[role="dialog"]'); if (d) window.__tops.push(d.getBoundingClientRect().top)
        if (performance.now() - t0 < 2500) requestAnimationFrame(step) }
      requestAnimationFrame(step) }""")
    await phone_sheet(page)
    await page.wait_for_timeout(1200)
    tops = await page.evaluate("() => window.__tops")
    if not tops:
        return fail(result, "no dialog")
    rest = tops[-1]
    least = min(tops)
    result["notes"].append(f"entrance from {round(tops[0])} to {round(rest)}; furthest {round(rest - least, 1)} px past the rest")
    if least < rest - 0.5:
        fail(result, f"the entrance overshoots by {round(rest - least, 1)} px")


async def s6_drag(page, result: dict, case: dict) -> None:
    await phone_sheet(page)
    await page.wait_for_timeout(700)
    box = await page.evaluate("""() => { const d = document.querySelector('[role="dialog"]'); const h = d && d.firstElementChild
        const r = h && h.getBoundingClientRect(); return r ? { x: r.left + r.width / 2, y: r.top + r.height / 2, top: d.getBoundingClientRect().top } : null }""")
    if not box:
        return fail(result, "no sheet")
    rest = box["top"]
    await page.mouse.move(box["x"], box["y"])
    await page.mouse.down()
    for k in range(1, 9):
        await page.mouse.move(box["x"], box["y"] + k * 10)
        await page.wait_for_timeout(30)
    mid = await page.evaluate("() => document.querySelector('[role=\"dialog\"]').getBoundingClientRect().top")
    follow = mid - rest
    result["notes"].append(f"finger down 80 px, sheet down {round(follow, 1)} px")
    if abs(follow - 80) > 3:
        fail(result, f"the sheet does not follow the finger ({round(follow, 1)} px for 80)")
    await page.wait_for_timeout(250)  # a slow release springs back
    await page.mouse.up()
    tops = await page.evaluate(TOPS, 500)
    least = min(tops) if tops else rest
    result["notes"].append(f"spring back ends at {round(tops[-1] - rest, 1) if tops else '?'} px, passes the rest by {round(max(0, rest - least), 1)} px")
    if tops and (abs(tops[-1] - rest) > 1 or least < rest - 0.5):
        fail(result, "the spring back does not settle at its rest without passing it")
    # a long pull closes it
    await page.mouse.move(box["x"], box["y"])
    await page.mouse.down()
    for k in range(1, 13):
        await page.mouse.move(box["x"], box["y"] + k * 25)
        await page.wait_for_timeout(16)
    await page.mouse.up()
    await page.wait_for_timeout(600)
    if await page.evaluate("() => document.querySelectorAll('[role=dialog]').length"):
        fail(result, "a 300 px pull did not close the sheet")


# ── reduced motion ──────────────────────────────────────────────────────────────────────────────────────

ANIM_PROPS = """(sel) => { const el = document.querySelector(sel); if (!el) return null
  return el.getAnimations().map((a) => ({ d: a.effect.getTiming().duration,
    props: [...new Set(a.effect.getKeyframes().flatMap((k) => Object.keys(k).filter((x) => !['offset', 'easing', 'composite', 'computedOffset'].includes(x))))] })) }"""


async def reduced_fade(page, result: dict, case: dict) -> None:
    await page.get_by_role("button", name="Edit", exact=True).first.click()
    await page.wait_for_timeout(40)
    anims = await page.evaluate(ANIM_PROPS, '[role="dialog"]')
    result["notes"].append(f"entrance {anims}")
    if not anims or not all(a["props"] == ["opacity"] and a["d"] >= 120 for a in anims):
        fail(result, "under reduced motion the dialog should fade (opacity only, >= 120 ms)")
    await page.wait_for_timeout(400)
    title = (await page.evaluate(DIALOG_STATE) or {}).get("named", "")
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(40)
    leaving = await page.evaluate("""(text) => document.getAnimations().filter((a) => a.playState === 'running' && a.effect && a.effect.target
        && (a.effect.target.textContent || '').includes(text)).map((a) => [...new Set(a.effect.getKeyframes().flatMap((k) => Object.keys(k)
        .filter((x) => !['offset', 'easing', 'composite', 'computedOffset'].includes(x))))])""", title)
    result["notes"].append(f"exit {leaving}")
    if not leaving or not all(p == ["opacity"] for p in leaving):
        fail(result, "under reduced motion the dialog should fade out (opacity only)")


# ── Ctrl K over an open sheet (the R7e review: the palette above a drawer or dialog) ────────────────────

async def palette_over_sheet(page, result: dict, case: dict) -> None:
    await page.get_by_role("button", name="QA-10000", exact=True).first.click()
    await page.wait_for_timeout(900)
    await page.keyboard.press("Control+k")
    await page.wait_for_timeout(500)
    st = await page.evaluate("""() => { const i = document.querySelector('input[placeholder^="Search pages"]'); if (!i) return null
      const r = i.getBoundingClientRect(); const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)
      return { onTop: hit === i || i.contains(hit), focused: document.activeElement === i } }""")
    result["notes"].append(f"palette {st}")
    if not st or not st["onTop"]:
        return fail(result, "Ctrl K opens the palette under the open sheet")
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(500)
    left = await page.evaluate("""() => ({ palette: !!document.querySelector('input[placeholder^="Search pages"]'),
      sheet: document.querySelectorAll('[role=dialog]').length })""")
    result["notes"].append(f"after one Escape {left}")
    if left["palette"] or left["sheet"] != 1:
        fail(result, "the first Escape should close the palette only")
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(500)
    if await page.evaluate("() => document.querySelectorAll('[role=dialog]').length"):
        fail(result, "the second Escape should close the sheet")


# ── the rep's toast stack and the order bar ─────────────────────────────────────────────────────────────

async def toast_order_bar(page, result: dict, case: dict) -> None:
    add = page.get_by_role("button", name=re.compile(r"^Add", re.I)).first
    await add.click()
    await page.wait_for_timeout(900)
    st = await page.evaluate("""() => {
      const bar = [...document.querySelectorAll('button')].find((b) => b.textContent.trim() === 'Review')
      const stack = document.querySelector('body > [aria-live="polite"]')
      if (!bar || !stack) return { bar: !!bar, stack: !!stack }
      const b = bar.closest('div.fixed') || bar
      return { bar: true, stack: true, barTop: Math.round(b.getBoundingClientRect().top), stackBottom: Math.round(stack.getBoundingClientRect().bottom) }
    }""")
    result["notes"].append(json.dumps(st))
    if not st.get("bar") or not st.get("stack"):
        return fail(result, "need the order bar and the toast stack")
    if st["stackBottom"] > st["barTop"] - 4:
        fail(result, f"the toast stack starts at {st['stackBottom']} px, over the order bar (top {st['barTop']} px)")


# ── photo fade-in ───────────────────────────────────────────────────────────────────────────────────────

async def photo_fade(page, result: dict, case: dict) -> None:
    # the photos answer after photo_ms: at first every tile waits under its tint
    st = await page.evaluate("""() => { const imgs = [...document.querySelectorAll('main img[src*="img.example.invalid"]')]
      return imgs.slice(0, 4).map((i) => ({ state: i.parentElement.dataset.state || null, done: i.complete && i.naturalWidth > 0,
        tint: getComputedStyle(i.parentElement, '::after').opacity })) }""")
    result["notes"].append(f"while loading {st}")
    if not st or not all(x["state"] == "wait" and x["tint"] == "1" for x in st if not x["done"]) or all(x["done"] for x in st):
        fail(result, "a photo still loading should wait under a tint (data-state=wait)")
    await page.wait_for_timeout(case.get("photo_ms", 0) + 900)
    after = await page.evaluate("""() => [...document.querySelectorAll('main img[src*="img.example.invalid"]')].slice(0, 4)
      .map((i) => ({ state: i.parentElement.dataset.state || null, tint: getComputedStyle(i.parentElement, '::after').opacity }))""")
    result["notes"].append(f"loaded {after}")
    if not after or not all(x["state"] == "in" and x["tint"] == "0" for x in after):
        fail(result, "a loaded photo should show (data-state=in, the tint gone)")


CASES = [
    *({"name": n, "path": path, "check": modal_check, "opener": opener, **extra} for n, path, opener, extra in MODALS),
    {"name": "s11_toast_drawer", "path": "/shop-orders", "check": s11_check, "fix": {"/shop/orders/:id": order_detail(["deliver"])}},
    {"name": "s11_toast_phone", "path": "/shop-orders", "check": s11_check, "fix": {"/shop/orders/:id": order_detail(["deliver"])}, **PHONE},
    {"name": "s6_enter", "path": "/shop-orders", "check": s6_enter, **PHONE},
    {"name": "s6_drag", "path": "/shop-orders", "check": s6_drag, **PHONE},
    {"name": "palette_over_sheet", "path": "/shop-orders", "check": palette_over_sheet},
    {"name": "reduced_fade", "path": "/leads", "check": reduced_fade, "reduced": "reduce"},
    {"name": "toast_order_bar", "path": "/shop", "check": toast_order_bar, "me": REP, **PHONE},
    {"name": "photo_fade", "path": "/shop", "check": photo_fade, "me": REP, "photo_ms": 1500, "wait_ms": 2600, **PHONE},
]


async def main_async(a) -> int:
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    only = set(a.only.split(",")) if a.only else None
    bad = 0
    for c in CASES:
        if only and c["name"] not in only:
            continue
        r = await run_case(a.url, Path(a.web), out, c)
        bad += 0 if r["ok"] else 1
        print(json.dumps(r), flush=True)
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:5185")
    ap.add_argument("--web", default=str(Path(__file__).resolve().parents[1]), help="the web/ folder (its .env)")
    ap.add_argument("--out", default=str(Path(tempfile.gettempdir()) / "yq-portal-layers"))
    ap.add_argument("--only", default="")
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
