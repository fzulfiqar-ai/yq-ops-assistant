"""Browser check of the portal's first seconds (ship 2, items 14 and 15) — synthetic data only.

    cd web && npm run build && npx vite preview --outDir dist --port 5184 --strictPort
    python web/scripts/portal_boot_check.py [--url http://localhost:5184] [--out <dir>] [--only case,case]

The same stand-ins as admin_render_check.py (imported from it): a fake Supabase session in localStorage
where a case is signed in (valid nowhere), the API host (VITE_API_URL in web/.env) answered from its
invented fixtures — /me delayed where a case says so — Supabase answered in the browser, the password
sign-in included (a fake session again), and every other request aborted. No account, no API, no
database, no network: every request leaves the browser only to the preview server.

  S7     first_paint_*  the app's JavaScript never arrives, so what is on screen is what the HTML and
                        its first script paint: the night for a signed-out visit, the shell's outline
                        for a returning user (dark for a dark user), the field app's for a salesman —
                        never a blank page
         timeline_*     a normal load at 4x CPU, every frame Chromium composites from the navigation on
                        (CDP screencast): no blank white frame before the app's first screen, and no
                        light frame at all on the way to a dark one
  4 s    signin_*       a real sign-in through the form: /me in 300 ms goes straight to the shell and
                        never shows "Connecting… taking longer"; /me in 9 s shows the shell's outline
                        and the connecting screen only after 4 s
  intro  intro_*        a signed-out visit plays it with every word fully visible for its hold; a new tab
                        the same week does not, a week later (or the old intro's 'done') does; a tap and a
                        key skip it (the key presses nothing under it); never over a signed-in shell or
                        on a salesman's phone; reduced motion: the still frame, then a short fade
  login  login_*        no blur and no backdrop blur, only transform and opacity animating, the plum
                        button, 16 px fields, the served frame handed over; light and dark, 390 and 1366
One JSON line per case; exit 1 if any case failed. Screenshots (the boot frames, the login, intro frames)
go to --out.
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import re
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
import admin_render_check as arc  # noqa: E402 -- the shared stand-ins (fake session, fixtures)

INTRO_KEY = "yq-intro-v2"   # PortalRoot's storageKey
DUMP = False                # --dump: every screencast frame of a timeline case to --out
WORDS = ["YQ Bahrain", "Mobile Accessories", "Intelligence"]
SALES_ME = {**arc.ME, "email": "qa-rep@example.com", "role": "salesman", "full_name": "QA Rep", "features": ["Catalog", "Shop Orders"]}
# the intro already seen in this tab: the old intro reads localStorage 'done', the weekly one this tab's stamp
INTRO_SEEN = {"local": {INTRO_KEY: "done"}, "session": {INTRO_KEY: "1"}}


def stats(png: bytes, size: int = 64) -> dict:
    """A frame: its dominant colour, the share of the frame near it, its luminance (portal_qa.py frame_stats)."""
    from PIL import Image

    img = Image.open(io.BytesIO(png)).convert("RGB").resize((size, size))
    raw = img.tobytes()
    px = [(raw[i], raw[i + 1], raw[i + 2]) for i in range(0, len(raw), 3)]
    dom = Counter((r // 6, g // 6, b // 6) for r, g, b in px).most_common(1)[0][0]
    rgb = tuple(c * 6 + 3 for c in dom)
    near = sum(1 for q in px if all(abs(q[k] - rgb[k]) <= 12 for k in range(3))) / len(px)
    lum = 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]
    white = min(rgb) >= 235
    return {"dominant": rgb, "uniform": round(near, 3), "lum": round(lum), "white": white, "blank": white and near >= 0.97}


# ── the cases' own checks ───────────────────────────────────────────────────────────────────────────

def fail(result: dict, note: str) -> None:
    result["ok"] = False
    result["notes"].append(note)


async def first_paint(page, result, case, out):
    await page.wait_for_timeout(800)
    attrs = await page.evaluate("() => [document.documentElement.getAttribute('data-boot'), document.documentElement.getAttribute('data-theme')]")
    png = await page.screenshot(path=str(out / f"{case['name']}.png"))
    st = stats(png)
    result["notes"].append(f"data-boot={attrs[0]} data-theme={attrs[1]} frame={st}")
    if st["blank"]:
        fail(result, "a blank white page is all there is before the app")
    if attrs[0] != case["want"]:
        fail(result, f"the frame is {attrs[0]!r}, not {case['want']!r}")
    if (case["want"] == "night" or case.get("dark")) and st["lum"] > 80:
        fail(result, f"a light first frame (luminance {st['lum']}) for a {'dark user' if case.get('dark') else 'signed-out visit'}")
    if case["want"] in ("shell", "sales") and not case.get("dark") and st["uniform"] >= 0.97:
        fail(result, "the outline is too faint to read as a shell")
    # the frame a browser can paint while still parsing: <body> there, the node not yet. Its base is on
    # <body>: never light for the night or a dark user, never bare for the office desktop (its night
    # sidebar); a light phone shell's, or the field app's (a white rail on cream), is that shell's own
    # light background, which is what the app paints there too
    await page.evaluate("() => document.getElementById('yq-boot')?.remove()")
    png2 = await page.screenshot(path=str(out / f"{case['name']}_mid_parse.png"))
    st2 = stats(png2)
    result["notes"].append(f"mid-parse (no node yet) frame={st2}")
    if (case["want"] == "night" or case.get("dark")) and st2["lum"] > 80:
        fail(result, f"mid-parse, the page is light (luminance {st2['lum']})")
    if case["want"] == "shell" and case.get("viewport", (1366, 800))[0] >= 1024 and st2["blank"]:
        fail(result, "mid-parse, the office desktop is bare (no sidebar)")


async def timeline(page, result, case, out, cast: list):
    ready = case["ready"]
    try:
        await page.locator(ready).first.wait_for(state="attached", timeout=20000)
    except Exception:  # noqa: BLE001
        fail(result, f"the app's first screen ({ready}) never came")
    await page.wait_for_timeout(250)
    origin = float(await page.evaluate("() => performance.timeOrigin"))
    ready_ms = await page.evaluate("() => performance.now()")
    frames = [(round(ts * 1000 - origin), data) for ts, data in cast if ts * 1000 >= origin - 5]
    import base64
    blank, light, seen = [], [], 0
    for t, data in frames:
        if DUMP:
            (out / f"{case['name']}_frame_{t:06d}ms.png").write_bytes(base64.b64decode(data))
        if t > ready_ms:
            break
        st = stats(base64.b64decode(data), 32)
        seen += 1
        if st["blank"]:
            blank.append(t)
            (out / f"{case['name']}_blank_{t}ms.png").write_bytes(base64.b64decode(data))
        if case.get("dark") and st["lum"] > 170 and st["uniform"] >= 0.9:
            light.append(t)
    result["notes"].append(f"{seen} frames before the app's first screen at {round(ready_ms)} ms (4x CPU); blank white at {blank[:6]}"
                           + (f"; light at {light[:6]}" if case.get("dark") else ""))
    if not seen:
        fail(result, "no frame was captured")
    if blank:
        fail(result, f"{len(blank)} blank white frame(s) before the app (first at {blank[0]} ms)")
    if light:
        fail(result, f"{len(light)} light frame(s) on the way to a dark app (first at {light[0]} ms)")
    await page.screenshot(path=str(out / f"{case['name']}.png"))


async def signin(page, result, case, out):
    await page.locator("input[type=email]").wait_for(timeout=15000)
    await page.fill("input[type=email]", "qa-admin@example.com")
    await page.fill("input[type=password]", "qa-not-a-password")
    t0 = time.perf_counter()
    await page.click("button[type=submit]")
    connecting = outline = shell = None
    shot = False
    while time.perf_counter() - t0 < case["watch_s"]:
        t = round((time.perf_counter() - t0) * 1000)
        try:
            body = await page.inner_text("body", timeout=400)
        except Exception:  # noqa: BLE001 -- navigating
            body = ""
        if connecting is None and ("Connecting to the server" in body or "taking longer than usual" in body or "The server hit an error" in body):
            connecting = t
            await page.screenshot(path=str(out / f"{case['name']}_connecting.png"))
        if outline is None and await page.locator(".yqb-shell[data-on], .yqb-sales[data-on]").count():
            outline = t
        if shell is None and await page.locator("nav a[href='/shop-orders']").count():
            shell = t
        if outline is not None and not shot and t > 600:
            shot = True
            await page.screenshot(path=str(out / f"{case['name']}_waiting.png"))
        if shell is not None and case.get("stop_at_shell"):
            break
        await page.wait_for_timeout(40)
    asked = [round((t - t0) * 1000) for t in result.get("_me_t", []) if t >= t0]
    result["notes"].append(f"after the click: /me asked at {asked} ms, the shell's outline at {outline} ms, the connecting screen at {connecting} ms, the shell at {shell} ms")
    if case["me_delay_ms"] < 1000:
        if connecting is not None:
            fail(result, f"a normal sign-in flashed the connecting screen at {connecting} ms")
        if shell is None:
            fail(result, "the shell never came")
    else:
        if connecting is not None and connecting < 4000:
            fail(result, f"the connecting screen came at {connecting} ms, before 4 s")
        if connecting is None or connecting > 6500:
            fail(result, f"the connecting screen did not come after 4 s (at {connecting})")
        if outline is None or (connecting is not None and outline > connecting):
            fail(result, "no shell outline while /me was on the way")
    await page.screenshot(path=str(out / f"{case['name']}.png"))


# a per-frame sampler of the intro overlay, from the very first frame (an init script)
SAMPLER = """(() => { window.__qaIntro = []; const tick = () => {
  const o = document.querySelector('[data-intro]') || document.querySelector('section[aria-label="Intro"] > div.z-30');
  let row = [performance.now(), null, [], false];
  if (o) {
    const op = parseFloat(getComputedStyle(o).opacity);
    const ws = [...o.querySelectorAll('[data-intro-word]')];
    const words = (ws.length ? ws : [...o.querySelectorAll('span')]).map((e) => [e.textContent.trim(), parseFloat(getComputedStyle(e).opacity), getComputedStyle(e).transform]);
    row = [performance.now(), op, words, !!o.querySelector('[data-intro-still]')];
  }
  window.__qaIntro.push(row); requestAnimationFrame(tick) }; requestAnimationFrame(tick) })()"""


def holds(samples: list) -> dict:
    """per word: the longest run (ms) it was fully visible (word and overlay both at >= 0.98)."""
    best: dict[str, float] = {}
    run: dict[str, float] = {}
    prev_t = None
    for t, op, words, _still in samples:
        full = {w for w, o, _ in words if op is not None and op >= 0.98 and o >= 0.98}
        for w in list(run):
            if w not in full:
                best[w] = max(best.get(w, 0), t - run.pop(w))
        for w in full:
            run.setdefault(w, t)
        prev_t = t
    for w, start in run.items():
        best[w] = max(best.get(w, 0), (prev_t or start) - start)
    return {w: round(v) for w, v in best.items()}


def overlay_span(samples: list) -> tuple[float | None, float | None]:
    on = [t for t, op, _w, _s in samples if op is not None and op > 0.02]
    return (on[0], on[-1]) if on else (None, None)


async def intro_plays(page, result, case, out):
    shots = case.get("frames")
    t0 = time.perf_counter()
    if shots:
        for i, at in enumerate(shots):
            await page.wait_for_timeout(max(0, at - round((time.perf_counter() - t0) * 1000)))
            await page.screenshot(path=str(out / f"{case['name']}_{i + 1}_{at}ms.png"))
    else:
        # the whole intro, however late a busy boot starts it: on screen, then gone
        try:
            await page.wait_for_function("() => { const s = window.__qaIntro || []; const on = s.findIndex((r) => r[1] !== null && r[1] > 0.02);"
                                         " return on >= 0 && s.slice(on).some((r) => r[1] === null) }", timeout=20000)
        except Exception:  # noqa: BLE001
            pass
        await page.wait_for_timeout(200)
    samples = await page.evaluate("() => window.__qaIntro || []")
    first, last = overlay_span(samples)
    hold = holds(samples)
    stamp = await page.evaluate(f"() => [localStorage.getItem('{INTRO_KEY}'), sessionStorage.getItem('{INTRO_KEY}')]")
    result["notes"].append(f"overlay from {first and round(first)} to {last and round(last)} ms; full holds {hold}; stamps {stamp}")
    if first is None:
        fail(result, "the intro did not play")
        return
    if not shots:
        for w in WORDS:
            if hold.get(w, 0) < 250:
                fail(result, f"{w!r} was fully visible for {hold.get(w, 0)} ms (needs its full hold, >= 250)")
        if last - first > 3400:
            fail(result, f"the intro stayed {round(last - first)} ms")
        try:
            if not (stamp[0] and abs(float(stamp[0]) - time.time() * 1000) < 120000 and stamp[1]):
                fail(result, f"the device and the tab are not stamped: {stamp}")
        except ValueError:
            fail(result, f"the device stamp is not a time: {stamp}")
    body = await page.inner_text("body")
    if "Welcome back" not in body:
        fail(result, "the login is not there after the intro")
    await page.screenshot(path=str(out / f"{case['name']}_after.png"))


async def intro_absent(page, result, case, out):
    await page.wait_for_timeout(case.get("watch_ms", 1800))
    samples = await page.evaluate("() => window.__qaIntro || []")
    first, last = overlay_span(samples)
    result["notes"].append(f"overlay on screen from {first and round(first)} to {last and round(last)} ms")
    if first is not None:
        fail(result, "the intro played where it must not")
    await page.screenshot(path=str(out / f"{case['name']}.png"))


async def intro_skip(page, result, case, out):
    # once the intro is on screen (the app boots under the served night first), a little into it
    try:
        await page.wait_for_function("() => (window.__qaIntro || []).some((s) => s[1] !== null && s[2].some((w) => w[1] > 0.5))", timeout=20000)
    except Exception:  # noqa: BLE001
        fail(result, "the intro did not play, so nothing to skip")
        return
    await page.wait_for_timeout(250)
    await page.evaluate("() => { window.__qaSkip = null; const at = () => { if (window.__qaSkip === null) window.__qaSkip = performance.now() };"
                        " addEventListener('pointerdown', at, true); addEventListener('keydown', at, true) }")
    if case.get("touch"):
        vw = case["viewport"]
        await page.touchscreen.tap(vw[0] // 2, vw[1] // 2)
    else:
        await page.keyboard.press(case.get("key", "Enter"))
    await page.wait_for_timeout(700)
    at = await page.evaluate("() => window.__qaSkip")
    samples = await page.evaluate("() => window.__qaIntro || []")
    if at is None:
        fail(result, "the tap or key never reached the page")
        return
    # the skip's fade is 200 ms; a busy machine gets a few frames of slack
    after = [s for s in samples if s[0] > at + 400]
    still_on = [s for s in after if s[1] is not None and s[1] > 0.02]
    body = await page.inner_text("body")
    hit = await page.evaluate("""() => { const i = document.querySelector('input[type=email]'); if (!i) return null;
        const r = i.getBoundingClientRect(); const e = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2); return e === i }""")
    result["notes"].append(f"skipped at {round(at)} ms; overlay frames 400 ms later: {len(still_on)}; the email field takes the next tap: {hit}")
    if still_on:
        fail(result, "the intro was still on screen 400 ms after the skip")
    if not hit:
        fail(result, "the login's email field is not reachable after the skip")
    if "Please enter your email" in body:
        fail(result, "the key that skipped also submitted the form under the night")
    await page.screenshot(path=str(out / f"{case['name']}.png"))


async def intro_still(page, result, case, out):
    await page.wait_for_timeout(300)
    await page.screenshot(path=str(out / f"{case['name']}_still.png"))
    await page.wait_for_timeout(1500)
    samples = await page.evaluate("() => window.__qaIntro || []")
    first, last = overlay_span(samples)
    early = [s for s in samples if s[1] is not None and s[0] < (first or 0) + 400]
    still = any(s[3] for s in early) or any(len([w for w in s[2] if w[1] >= 0.98]) >= 3 for s in early)
    moving = {w[2] for s in samples for w in s[2] if w[2] not in ("none", "")}
    result["notes"].append(f"overlay from {first and round(first)} to {last and round(last)} ms; the still frame: {still}; word transforms seen: {sorted(moving)[:3]}")
    if first is None or not still:
        fail(result, "no still frame under reduced motion")
    if moving:
        fail(result, "words moved under reduced motion")
    if last is not None and last - (first or 0) > 1400:
        fail(result, f"the still frame stayed {round(last - first)} ms")
    await page.screenshot(path=str(out / f"{case['name']}.png"))


async def login_look(page, result, case, out):
    await page.locator("input[type=email]").wait_for(state="attached", timeout=20000)
    await page.wait_for_timeout(300)
    early = await page.evaluate(ANIMS)
    await page.wait_for_timeout(1600)
    late = await page.evaluate(ANIMS)
    look = await page.evaluate("""() => { const b = document.querySelector('button[type=submit]'); const i = document.querySelector('input[type=email]');
        const back = [...document.querySelectorAll('*')].filter((e) => { const s = getComputedStyle(e); return (s.backdropFilter && s.backdropFilter !== 'none') || (s.webkitBackdropFilter && s.webkitBackdropFilter !== 'none') }).length;
        const filt = [...document.querySelectorAll('*')].filter((e) => { const f = getComputedStyle(e).filter; return f && f !== 'none' }).length;
        const r = i && i.getBoundingClientRect(); const hit = r ? document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2) === i : null;
        return { button: b && getComputedStyle(b).backgroundColor, field_px: i && parseFloat(getComputedStyle(i).fontSize), backdrop: back, filtered: filt,
                 boot_node: !!document.getElementById('yq-boot'), hit, theme: document.documentElement.getAttribute('data-theme') } }""")
    result["notes"].append(f"look {look}; animating at 0.3 s {early}; after it settles {late}")
    bad = sorted({p for a in early + late for p in a["props"] if p not in ("opacity", "transform")})
    if bad:
        fail(result, f"animates {bad}")
    if look["backdrop"]:
        fail(result, f"{look['backdrop']} element(s) with a backdrop blur")
    if look["filtered"]:
        fail(result, f"{look['filtered']} element(s) with a filter")
    if look["button"] != "rgb(109, 64, 145)":
        fail(result, f"the button is {look['button']}, not the logo plum")
    if not look["field_px"] or look["field_px"] < 16:
        fail(result, f"the fields are {look['field_px']} px (an iPhone zooms under 16)")
    if look["boot_node"]:
        fail(result, "the served first frame was never handed over")
    if not look["hit"]:
        fail(result, "something covers the email field")
    await page.screenshot(path=str(out / f"{case['name']}.png"))


# every running animation on the page and the properties it animates (CSS animations and motion's WAAPI)
ANIMS = """() => document.getAnimations().filter((a) => a.playState === 'running').map((a) => {
  const kf = a.effect && a.effect.getKeyframes ? a.effect.getKeyframes() : [];
  const props = [...new Set(kf.flatMap((k) => Object.keys(k)).filter((p) => !['offset', 'easing', 'composite', 'computedOffset'].includes(p)))];
  return { name: a.animationName || a.id || 'waapi', props } })"""


async def handoff_shell(page, result, case, out):
    await page.locator("nav a[href='/shop-orders']").first.wait_for(state="attached", timeout=15000)
    await page.wait_for_timeout(500)
    left = await page.evaluate("() => [!!document.getElementById('yq-boot'), document.querySelectorAll('.yqb[data-on]').length]")
    result["notes"].append(f"served node still there: {left[0]}; React outline still there: {left[1]}")
    if left[0] or left[1]:
        fail(result, "the shell is up but a frame is still on the page")
    await page.screenshot(path=str(out / f"{case['name']}.png"))


# ── the runner ──────────────────────────────────────────────────────────────────────────────────────

async def run_case(base: str, web: Path, out: Path, case: dict) -> dict:
    ref = arc.supabase_ref(web)
    api = arc.api_host(web)
    me = case.get("me", arc.ME)
    result = {"case": case["name"], "ok": True, "notes": [], "_me": 0}
    cast: list[tuple[float, str]] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        vw = case.get("viewport", (1366, 800))
        ctx = await browser.new_context(viewport={"width": vw[0], "height": vw[1]}, reduced_motion=case.get("reduced", "no-preference"),
                                        has_touch=case.get("touch", False), is_mobile=case.get("touch", False))
        local = dict(case.get("local", {}))
        session = dict(case.get("session", {}))
        if case.get("signed_in"):
            local[f"sb-{ref}-auth-token"] = json.dumps(arc.fake_session())
            local.setdefault("yq-role", me["role"])
            if case.get("cached_me", True):
                local["yq-me-v1"] = json.dumps({"uid": arc.UID, "me": me, "at": int(time.time() * 1000)})
        if case.get("theme"):
            local["yq-theme"] = case["theme"]
        await ctx.add_init_script(
            "(() => { if (sessionStorage.getItem('qa-seeded')) return; sessionStorage.setItem('qa-seeded','1');"
            f" const l = {json.dumps(local)}, s = {json.dumps(session)};"
            " for (const k in l) localStorage.setItem(k, l[k]); for (const k in s) sessionStorage.setItem(k, s[k]); })()")
        if case.get("sample_intro"):
            await ctx.add_init_script(SAMPLER)

        async def router(route):
            u = urlparse(route.request.url)
            if u.netloc == urlparse(base).netloc:
                # the app's JavaScript never arrives: the served HTML, its CSS and its first script only
                if case.get("block_app") and re.search(r"/assets/[^/]+\.js$", u.path) and "/assets/portal-boot-" not in u.path:
                    return await route.abort()
                return await route.continue_()
            cors = {"access-control-allow-origin": "*", "access-control-allow-headers": "*", "access-control-allow-methods": "*"}
            if route.request.method == "OPTIONS":
                return await route.fulfill(status=204, headers=cors)
            if u.netloc == api:
                if u.path == "/me":
                    result["_me"] += 1
                    result.setdefault("_me_t", []).append(time.perf_counter())
                    if case.get("me_delay_ms"):
                        await asyncio.sleep(case["me_delay_ms"] / 1000)
                    return await route.fulfill(status=200, headers={**cors, "content-type": "application/json"}, body=json.dumps(me))
                body = arc.answer(u.path, parse_qs(u.query))
                return await route.fulfill(status=200, headers={**cors, "content-type": "application/json"}, body=json.dumps(body))
            if u.netloc.startswith(ref):
                if u.path.endswith("/auth/v1/token"):   # the password sign-in: a session valid nowhere
                    return await route.fulfill(status=200, headers={**cors, "content-type": "application/json"}, body=json.dumps(arc.fake_session()))
                return await route.fulfill(status=200, headers={**cors, "content-type": "application/json"}, body="{}")
            return await route.abort()

        await ctx.route("**/*", router)
        page = await ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        cdp = None
        if case.get("cast"):
            cdp = await ctx.new_cdp_session(page)
            await cdp.send("Emulation.setCPUThrottlingRate", {"rate": 4})

            def on_frame(ev):
                cast.append((float(ev.get("metadata", {}).get("timestamp") or 0), ev.get("data") or ""))
                asyncio.ensure_future(cdp.send("Page.screencastFrameAck", {"sessionId": ev["sessionId"]}))

            cdp.on("Page.screencastFrame", on_frame)
            await cdp.send("Page.startScreencast", {"format": "png", "maxWidth": 320, "maxHeight": 320, "everyNthFrame": 1})
        try:
            await page.goto(base.rstrip("/") + case["path"], wait_until="commit")
            if case["kind"] == "timeline":
                await timeline(page, result, case, out, cast)
            else:
                await case["check"](page, result, case, out)
        except Exception as e:  # noqa: BLE001
            fail(result, f"error: {str(e).splitlines()[0]}")
        if cdp:
            try:
                await cdp.send("Page.stopScreencast")
            except Exception:  # noqa: BLE001
                pass
        if errors and not case.get("block_app"):
            fail(result, f"page errors: {errors[:3]}")
        result["me_requests"] = result.pop("_me")
        result.pop("_me_t", None)
        await ctx.unroute_all(behavior="ignoreErrors")
        await ctx.close()
        await browser.close()
    return result


NIGHT = {"local": INTRO_SEEN["local"], "session": INTRO_SEEN["session"]}
PHONE = (390, 844)
DESK = (1366, 800)
CASES = [
    # S7 — the first paint, the app's code held back
    {"name": "first_paint_night_1366", "path": "/login", "block_app": True, "want": "night", "check": first_paint, **NIGHT},
    {"name": "first_paint_night_390", "path": "/", "viewport": PHONE, "touch": True, "block_app": True, "want": "night", "check": first_paint, **NIGHT},
    {"name": "first_paint_shell_1366_light", "path": "/shop-orders", "signed_in": True, "block_app": True, "want": "shell", "check": first_paint},
    {"name": "first_paint_shell_1366_dark", "path": "/shop-orders", "signed_in": True, "theme": "dark", "dark": True, "block_app": True, "want": "shell", "check": first_paint},
    {"name": "first_paint_shell_390_light", "path": "/", "viewport": PHONE, "touch": True, "signed_in": True, "block_app": True, "want": "shell", "check": first_paint},
    {"name": "first_paint_shell_390_dark", "path": "/", "viewport": PHONE, "touch": True, "signed_in": True, "theme": "dark", "dark": True, "block_app": True, "want": "shell", "check": first_paint},
    {"name": "first_paint_sales_390", "path": "/today", "viewport": PHONE, "touch": True, "signed_in": True, "me": SALES_ME, "block_app": True, "want": "sales", "check": first_paint},
    {"name": "first_paint_sales_1366", "path": "/today", "signed_in": True, "me": SALES_ME, "block_app": True, "want": "sales", "check": first_paint},
    # S7 — every frame of a normal load at 4x CPU
    {"name": "timeline_signed_out_390", "path": "/", "viewport": PHONE, "touch": True, "kind": "timeline", "cast": True, "ready": "text=Welcome back", "dark": True, **NIGHT},
    {"name": "timeline_shell_1366_light", "path": "/shop-orders", "signed_in": True, "kind": "timeline", "cast": True, "ready": "nav a[href='/shop-orders']"},
    {"name": "timeline_shell_390_dark", "path": "/shop-orders", "viewport": PHONE, "touch": True, "signed_in": True, "theme": "dark", "dark": True, "kind": "timeline", "cast": True,
     "ready": "nav a[href='/shop-orders']"},
    {"name": "handoff_shell_1366", "path": "/shop-orders", "signed_in": True, "check": handoff_shell},
    # the 4 s rule, through the real sign-in form
    {"name": "signin_fast", "path": "/login", "me_delay_ms": 300, "watch_s": 4.5, "check": signin, **NIGHT},
    {"name": "signin_slow", "path": "/login", "me_delay_ms": 9000, "watch_s": 7.0, "check": signin, **NIGHT},
    {"name": "signin_fast_phone", "path": "/login", "viewport": PHONE, "touch": True, "me_delay_ms": 300, "watch_s": 4.0, "check": signin, **NIGHT},
    # the intro, once a week per device
    {"name": "intro_first_visit", "path": "/login", "sample_intro": True, "check": intro_plays},
    {"name": "intro_frames_390", "path": "/login", "viewport": PHONE, "touch": True, "sample_intro": True, "check": intro_plays, "frames": [120, 450, 1150, 1950, 2500, 2950]},
    {"name": "intro_frames_1366", "path": "/login", "sample_intro": True, "check": intro_plays, "frames": [120, 450, 1150, 1950, 2500, 2950]},
    {"name": "intro_same_week", "path": "/login", "sample_intro": True, "local": {INTRO_KEY: str(int(time.time() * 1000) - 2 * 86400000)}, "check": intro_absent},
    {"name": "intro_after_a_week", "path": "/login", "sample_intro": True, "local": {INTRO_KEY: str(int(time.time() * 1000) - 8 * 86400000)}, "check": intro_plays},
    {"name": "intro_old_done_stamp", "path": "/login", "sample_intro": True, "local": {INTRO_KEY: "done"}, "check": intro_plays},
    {"name": "intro_skip_tap_390", "path": "/login", "viewport": PHONE, "touch": True, "sample_intro": True, "check": intro_skip},
    {"name": "intro_skip_key_1366", "path": "/login", "sample_intro": True, "key": "Enter", "check": intro_skip},
    {"name": "intro_never_signed_in", "path": "/", "signed_in": True, "sample_intro": True, "check": intro_absent, "watch_ms": 2200},
    {"name": "intro_never_salesman_phone", "path": "/login", "viewport": PHONE, "touch": True, "local": {"yq-role": "salesman"}, "sample_intro": True, "check": intro_absent},
    {"name": "intro_reduced_still", "path": "/login", "reduced": "reduce", "sample_intro": True, "check": intro_still},
    # the sign-in page
    {"name": "login_1366_light", "path": "/login", "check": login_look, **NIGHT},
    {"name": "login_1366_dark", "path": "/login", "theme": "dark", "check": login_look, **NIGHT},
    {"name": "login_390_light", "path": "/login", "viewport": PHONE, "touch": True, "check": login_look, **NIGHT},
    {"name": "login_390_dark", "path": "/login", "viewport": PHONE, "touch": True, "theme": "dark", "check": login_look, **NIGHT},
    {"name": "login_390_reduced", "path": "/login", "viewport": PHONE, "touch": True, "reduced": "reduce", "check": login_look, **NIGHT},
]
for c in CASES:
    c.setdefault("kind", "check")


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
    ap.add_argument("--url", default="http://localhost:5184")
    ap.add_argument("--web", default=str(Path(__file__).resolve().parents[1]), help="the web/ folder (its .env)")
    ap.add_argument("--out", default=str(Path(tempfile.gettempdir()) / "yq-portal-boot"))
    ap.add_argument("--only", default="")
    ap.add_argument("--dump", action="store_true", help="save every screencast frame of the timeline cases")
    a = ap.parse_args()
    global DUMP
    DUMP = a.dump
    return asyncio.run(main_async(a))


if __name__ == "__main__":
    sys.exit(main())
