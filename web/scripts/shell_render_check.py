"""Browser check of the portal's shells (ship 2, plan items 12, 13, 23, 24) — synthetic data only.

    cd web && npm run build && npx vite preview --outDir dist --port 5183 --strictPort
    python web/scripts/shell_render_check.py [--url http://localhost:5183] [--out <dir>] [--only case,case] [--shots <dir>]

The same stand-ins as admin_render_check.py (imported from it): a fake Supabase session in localStorage, the
API host answered from invented fixtures inside the browser, Supabase answered {}, anything else aborted. No
account, no API, no network. Every shop, rep, code and amount is invented. Headless Chromium, one at a time.

The cases (acceptance targets of the plan in brackets):
  s9_window_scrolls       the window scrolls, not a box; the sidebar and the solid top bar stay; `sticky` works
                          inside a page [S9]
  s9_phone_drawer         the same on a phone, and the open drawer holds the page still [S9]
  s4_back_restores        a new page opens at the top; Back lands where the page was (± 2 px) [S4]
  s5_in_app_skeleton      a click on a page whose code is still downloading: the shell and one page-shaped
                          skeleton at once — not the old page, not a spinner, never blank [S5]
  s5_direct_skeleton      the same page opened directly [S5]
  s8_click_paints_fast    with its code fetched ahead (a resting hover), a click is acknowledged within 100 ms,
                          a light page paints within 150 ms, at most one skeleton; chart pages measured [S8]
  glide_calm              the page glide: transform + opacity, 200 ms; reduced motion = opacity alone; never on
                          the first page
  sidebar_collapse        the collapse swaps the width at once (no width animation) and no chart replays [23]
  page_error_keeps_shell  a page whose code cannot load shows its own error with Reload; the menu still works [24]
  rep_tabs_keep_place     the salesman app: a tab tapped again lands where it was left; the open tab tapped
                          again goes to its top; a new page opens at the top [S4, item 12]

One JSON line per case; exit 1 if any case failed. --shots also writes the review screenshots (390 / 1366 /
1920, light and dark) to that folder.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from admin_render_check import ME, UID, answer, api_host, fake_session, supabase_ref  # noqa: E402

REP = {"email": "qa-rep@example.com", "role": "salesman", "features": ["Shop Orders", "Catalog"], "full_name": "QA Rep",
       "must_reset": False, "read_only": False}
def book_shop(i: int) -> dict:
    return {"key": f"m:9730000{i:04d}", "name": f"Test Shop {i}", "focus_name": None, "contact_name": None,
            "phone": f"9730000{i:04d}", "area": "Area A", "email": None, "sources": ["app"], "last_focus_date": None,
            "last_app_order_at": "2026-10-20T08:00:00Z", "last_order_date": "2026-10-20", "app_orders": 3, "days_since": 7,
            "status": "ok", "due": False, "why": None, "monthly_value_bhd": "12.500", "credit": None, "rank": None}


REP_FIX = {
    "/me": REP,
    "/shop/me": {"salesman": {"id": 1, "name": "QA Rep", "referral_code": "QA1"}, "link": None, "kpis": {}},
    "/shop/customers": {"customers": []},
    "/shop/me/book": {"shops": [book_shop(i) for i in range(24)], "count": 24, "counts": {"due": 0, "focus": 0, "app": 24},
                      "data_through": "2026-10-27"},
}

# a page whose chunk a case holds back or breaks: the Sales report (its own chunk, assets/Sales-<hash>.js)
SALES_CHUNK = re.compile(r"/assets/Sales-[\w-]+\.js$")
NO_WARM = "Object.defineProperty(navigator, 'connection', { configurable: true, value: { saveData: true, effectiveType: '4g' } })"


async def open_page(p, base: str, web: Path, *, me: dict = ME, viewport=(1366, 860), mobile=False, reduced="no-preference",
                    theme="light", hold: float = 0.0, abort_chunk: bool = False, no_warm: bool = False, path="/"):
    """A browser on `path` with the fake session; returns (browser, page, state)."""
    ref = supabase_ref(web)
    api = api_host(web)
    state = {"hits": [], "errors": [], "chunk_requests": 0}
    browser = await p.chromium.launch()
    ctx = await browser.new_context(viewport={"width": viewport[0], "height": viewport[1]}, reduced_motion=reduced,
                                    has_touch=mobile, is_mobile=mobile)
    init = {f"sb-{ref}-auth-token": json.dumps(fake_session()), "yq-intro-v2": "done", "yq-role": me["role"],
            "yq-me-v1": json.dumps({"uid": UID, "me": me, "at": int(time.time() * 1000)}), "yq-theme": theme}
    await ctx.add_init_script(
        "(() => { if (sessionStorage.getItem('qa-seeded')) return; sessionStorage.setItem('qa-seeded','1');"
        f" const kv = {json.dumps(init)}; for (const k in kv) localStorage.setItem(k, kv[k]); }})()")
    if no_warm:
        await ctx.add_init_script(NO_WARM)
    fix = REP_FIX if me["role"] == "salesman" else {}

    async def router(route):
        u = urlparse(route.request.url)
        if u.netloc == urlparse(base).netloc:
            if SALES_CHUNK.search(u.path):
                state["chunk_requests"] += 1
                if abort_chunk:
                    return await route.abort()
                if hold:
                    await asyncio.sleep(hold)
            return await route.continue_()
        cors = {"access-control-allow-origin": "*", "access-control-allow-headers": "*", "access-control-allow-methods": "*"}
        if route.request.method == "OPTIONS":
            return await route.fulfill(status=204, headers=cors)
        if u.netloc == api:
            state["hits"].append(u.path)
            body = fix[u.path] if u.path in fix else answer(u.path, parse_qs(u.query))
            return await route.fulfill(status=200, headers={**cors, "content-type": "application/json"}, body=json.dumps(body))
        if u.netloc.startswith(ref):
            return await route.fulfill(status=200, headers={**cors, "content-type": "application/json"}, body="{}")
        return await route.abort()

    await ctx.route("**/*", router)
    page = await ctx.new_page()
    page.on("pageerror", lambda e: state["errors"].append(str(e)))
    await page.goto(base.rstrip("/") + path, wait_until="domcontentloaded")
    return browser, page, state


# the page's scroller, whichever it is: the window (this branch) or the shell's <main> box (before it)
SCROLL_GET = """() => { const m = document.querySelector('main');
  const box = m && /(auto|scroll)/.test(getComputedStyle(m).overflowY) && m.scrollHeight > m.clientHeight + 1;
  return box ? { who: 'main', y: Math.round(m.scrollTop) } : { who: 'window', y: Math.round(scrollY) } }"""
SCROLL_SET = """(y) => { const m = document.querySelector('main');
  const box = m && /(auto|scroll)/.test(getComputedStyle(m).overflowY) && m.scrollHeight > m.clientHeight + 1;
  if (box) m.scrollTo(0, y); else window.scrollTo(0, y) }"""


def fail(result: dict, note: str) -> None:
    result["ok"] = False
    result["notes"].append(note)


async def wait_text(page, text: str, timeout=8000) -> None:
    await page.get_by_text(text, exact=False).first.wait_for(timeout=timeout)


# ── the cases ───────────────────────────────────────────────────────────────────────────────────

async def s9_window_scrolls(p, a, r):
    # the Dashboard: tall in the window on a desktop (with a mouse a long table is its own scroller, DataTable)
    browser, page, st = await open_page(p, a.url, a.web, path="/")
    try:
        await wait_text(page, "Accessories sales this month")
        await page.wait_for_timeout(1500)
        geo = await page.evaluate("""async () => {
          const main = document.querySelector('main'); const header = document.querySelector('header'); const aside = document.querySelector('aside');
          const tall = document.scrollingElement.scrollHeight > innerHeight + 400;
          const frame = main.firstElementChild;
          const probe = document.createElement('div'); probe.style.cssText = 'position:sticky;top:64px;height:2px';
          frame.prepend(probe);
          window.scrollTo(0, 900);
          await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
          const hs = getComputedStyle(header);
          const bg = hs.backgroundColor.match(/[\\d.]+/g).map(Number);
          const out = { tall, scrollY: Math.round(scrollY), mainOverflowY: getComputedStyle(main).overflowY,
            headerTop: Math.round(header.getBoundingClientRect().top), headerBlur: hs.backdropFilter || hs.webkitBackdropFilter || 'none',
            headerAlpha: bg.length > 3 ? bg[3] : 1, asideTop: Math.round(aside.getBoundingClientRect().top),
            asideH: Math.round(aside.getBoundingClientRect().height), vh: innerHeight,
            probeTop: Math.round(probe.getBoundingClientRect().top), topbarVar: getComputedStyle(document.documentElement).getPropertyValue('--yq-topbar').trim() };
          probe.remove(); return out }""")
        r["notes"].append(geo)
        if not geo["tall"]:
            fail(r, "the document is not taller than the window: something else scrolls")
        if geo["scrollY"] != 900:
            fail(r, f"window.scrollTo(0, 900) left scrollY at {geo['scrollY']}")
        if geo["mainOverflowY"] in ("auto", "scroll"):
            fail(r, f"<main> is a scroller (overflow-y {geo['mainOverflowY']})")
        if geo["headerTop"] != 0 or geo["asideTop"] != 0 or abs(geo["asideH"] - geo["vh"]) > 1:
            fail(r, "the top bar or the sidebar did not stay in view")
        if geo["headerBlur"] not in ("none", "") or geo["headerAlpha"] < 1:
            fail(r, f"the top bar is not solid (blur {geo['headerBlur']}, alpha {geo['headerAlpha']})")
        if geo["probeTop"] != 64:
            fail(r, f"a sticky element inside the page did not stick under the top bar (top {geo['probeTop']})")
        await page.screenshot(path=str(a.out / "s9_window_scrolls.png"))
    finally:
        await browser.close()
    return st


async def s9_phone_drawer(p, a, r):
    browser, page, st = await open_page(p, a.url, a.web, viewport=(390, 844), mobile=True, path="/shop-orders")
    try:
        await wait_text(page, "Latest 100 of 342 orders")
        geo = await page.evaluate("""async () => { window.scrollTo(0, 1500);
          await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
          return { scrollY: Math.round(scrollY), headerTop: Math.round(document.querySelector('header').getBoundingClientRect().top) } }""")
        r["notes"].append(geo)
        if geo["scrollY"] != 1500 or geo["headerTop"] != 0:
            fail(r, "on a phone the window does not scroll under a sticky top bar")
        await page.get_by_title("Menu").click()
        await page.wait_for_timeout(400)
        locked = await page.evaluate("() => [document.body.style.overflow, Math.round(scrollY)]")
        await page.screenshot(path=str(a.out / "s9_phone_drawer.png"))
        await page.mouse.click(370, 400)  # the backdrop, right of the 260 px drawer
        await page.wait_for_timeout(400)
        released = await page.evaluate("() => [document.body.style.overflow, Math.round(scrollY)]")
        r["notes"].append({"drawer_open": locked, "drawer_closed": released})
        if locked[0] != "hidden" or released[0] == "hidden" or released[1] != 1500:
            fail(r, "the open drawer did not hold the page still, or closing it moved the page")
    finally:
        await browser.close()
    return st


async def back_restores(p, a, r, *, path: str, ready: str, to: str, to_title: str, y: int, viewport, mobile=False):
    browser, page, st = await open_page(p, a.url, a.web, path=path, viewport=viewport, mobile=mobile)
    try:
        await wait_text(page, ready)
        await page.wait_for_function(f"() => document.scrollingElement.scrollHeight > innerHeight + {y} || "
                                     f"(document.querySelector('main') && document.querySelector('main').scrollHeight > {y} + 400)", timeout=8000)
        await page.evaluate(SCROLL_SET, y)
        await page.wait_for_timeout(300)
        before = await page.evaluate(SCROLL_GET)
        if mobile:
            await page.get_by_title("Menu").click()
            await page.wait_for_timeout(400)
        await page.locator("aside").get_by_role("link", name=to, exact=True).click()
        await page.locator("main h1", has_text=to_title).first.wait_for(timeout=8000)
        await page.wait_for_timeout(600)
        opened = await page.evaluate(SCROLL_GET)
        await page.go_back()
        await wait_text(page, ready)
        await page.wait_for_timeout(1500)
        back = await page.evaluate(SCROLL_GET)
        r["notes"].append({"page": path, "scrolled": before, "new_page": opened, "after_back": back})
        if before["y"] != y:
            fail(r, f"{path}: could not scroll the page to {y} ({before})")
        if opened["y"] != 0:
            fail(r, f"{path}: the new page opened at {opened['y']}, not at the top")
        if abs(back["y"] - y) > 2:
            fail(r, f"{path}: Back landed at {back['y']}, not where the page was ({y})")
        await page.screenshot(path=str(a.out / f"s4_back_restores_{viewport[0]}.png"))
    finally:
        await browser.close()
    return st


async def s4_back_restores(p, a, r):
    st = await back_restores(p, a, r, path="/", ready="Accessories sales this month", to="Inventory", to_title="Inventory",
                             y=900, viewport=(1366, 860))
    st2 = await back_restores(p, a, r, path="/shop-orders", ready="Latest 100 of 342 orders", to="Inventory",
                              to_title="Inventory", y=1200, viewport=(390, 844), mobile=True)
    return {"errors": st["errors"] + st2["errors"]}


async def s5_in_app_skeleton(p, a, r):
    browser, page, st = await open_page(p, a.url, a.web, path="/", hold=6.0, no_warm=True)
    try:
        await wait_text(page, "Accessories sales this month")
        await page.wait_for_timeout(800)
        seen = await page.evaluate("""async () => {
          const link = [...document.querySelectorAll('aside a')].find((x) => x.textContent.trim() === 'Sales');
          link.click();
          await new Promise((r) => setTimeout(r, 150));
          const main = document.querySelector('main');
          return { skeleton: !!main.querySelector('[data-page-skeleton]'), spinner: !!main.querySelector('.animate-spin'),
            oldPage: main.innerText.toLowerCase().includes('accessories sales this month'), shell: !!document.querySelector('aside nav a') && !!document.querySelector('header'),
            mainText: main.innerText.trim().length } }""")
        r["notes"].append(seen)
        await page.screenshot(path=str(a.out / "s5_in_app_skeleton.png"))
        if not seen["shell"]:
            fail(r, "the shell is not on screen")
        if seen["oldPage"]:
            fail(r, "150 ms after the click the old page is still there (it waits for the old page to leave)")
        if not seen["skeleton"] or seen["spinner"]:
            fail(r, "no page-shaped skeleton (or a spinner) while the page's code downloads")
        await page.locator("main h1", has_text="Sales").first.wait_for(timeout=12000)
    finally:
        await browser.close()
    return st


async def s5_direct_skeleton(p, a, r):
    browser, page, st = await open_page(p, a.url, a.web, path="/sales", hold=5.0, no_warm=True)
    try:
        await page.locator("aside nav a").first.wait_for(timeout=8000)
        await page.wait_for_timeout(300)
        seen = await page.evaluate("""() => { const main = document.querySelector('main');
          return { skeleton: !!(main && main.querySelector('[data-page-skeleton]')), spinner: !!(main && main.querySelector('.animate-spin')),
            header: !!document.querySelector('header') } }""")
        r["notes"].append(seen)
        await page.screenshot(path=str(a.out / "s5_direct_skeleton.png"))
        if not seen["header"] or not seen["skeleton"] or seen["spinner"]:
            fail(r, "a page opened directly while its code downloads does not show the shell and its skeleton")
        await page.locator("main h1", has_text="Sales").first.wait_for(timeout=12000)
    finally:
        await browser.close()
    return st


CLICK_AND_TIME = """async (label) => {
  const link = [...document.querySelectorAll('aside a')].find((x) => x.textContent.trim() === label);
  const seen = { skeletons: 0, spinner: false };
  const busy = () => !!document.querySelector('main [data-page-skeleton], main [aria-busy="true"]');
  let was = busy();
  const mo = new MutationObserver(() => {
    const now = busy(); if (now && !was) seen.skeletons += 1; was = now;
    if (document.querySelector('main .animate-spin')) seen.spinner = true;
  });
  mo.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['aria-busy'] });
  let lit = -1;
  const t0 = performance.now();
  link.click();
  return await new Promise((resolve) => {
    const tick = () => {
      if (lit < 0 && link.className.includes('bg-white/15')) lit = Math.round(performance.now() - t0);
      const h1 = [...document.querySelectorAll('main h1')].find((h) => h.textContent.trim() === label);
      if (h1) { requestAnimationFrame(() => { mo.disconnect(); resolve({ ms: Math.round(performance.now() - t0), lit_ms: lit < 0 ? Math.round(performance.now() - t0) : lit, ...seen }) }); return }
      if (performance.now() - t0 > 6000) { mo.disconnect(); resolve({ ms: -1, lit_ms: lit, ...seen }); return }
      requestAnimationFrame(tick);
    };
    tick();
  });
}"""


async def s8_click_paints_fast(p, a, r):
    """S8, with its code and read fetched ahead (a resting hover). Hard checks, the shell's part: the click is
    acknowledged within 100 ms (the menu item lit), at most one skeleton and never a spinner, and a light page
    (Inventory, a table) painted within 150 ms. The chart pages' paint is MEASURED, as the plan treats S1's
    frame times: it is their own render (recharts' first draw), and it moves with the machine's load."""
    browser, page, st = await open_page(p, a.url, a.web, path="/", no_warm=True)
    try:
        await wait_text(page, "Accessories sales this month")
        await page.wait_for_timeout(1000)
        runs = []
        for label in ("Inventory", "Sales", "Receivables"):
            link = page.locator("aside").get_by_role("link", name=label, exact=True)
            await link.hover()  # a resting hover: the page's code and its read are fetched ahead
            await page.wait_for_timeout(1500)
            await page.mouse.move(700, 500)
            runs.append({"page": label, **(await page.evaluate(CLICK_AND_TIME, label))})
            await page.wait_for_timeout(700)
        r["notes"].append(runs)
        r["measured_paint_ms"] = {x["page"]: x["ms"] for x in runs}
        light = next(x for x in runs if x["page"] == "Inventory")
        if light["ms"] < 0 or light["ms"] > 150:
            fail(r, f"a light page took {light['ms']} ms to paint after the click (target 150 ms)")
        if any(x["lit_ms"] < 0 or x["lit_ms"] > 100 for x in runs):
            fail(r, f"a click was not acknowledged within 100 ms: {[(x['page'], x['lit_ms']) for x in runs]}")
        if any(x["skeletons"] > 1 or x["spinner"] for x in runs):
            fail(r, "more than one skeleton, or a spinner, on the way to the next page")
        over = [f"{x['page']} {x['ms']} ms" for x in runs if x["page"] != "Inventory" and x["ms"] > 150]
        if over:
            r["notes"].append("measured, over the 150 ms target (the page's own render): " + ", ".join(over))
        await page.screenshot(path=str(a.out / "s8_click_paints_fast.png"))
    finally:
        await browser.close()
    return st


FRAME_ANIM = """async (label) => {
  const first = document.querySelector('[data-page-frame]');
  const atLoad = first ? first.getAnimations().length : -1;
  const link = [...document.querySelectorAll('aside a')].find((x) => x.textContent.trim() === label);
  link.click();
  for (let i = 0; i < 60; i++) {
    await new Promise((r) => requestAnimationFrame(r));
    const f = document.querySelector('[data-page-frame]');
    if (f && f !== first) {
      await new Promise((r) => requestAnimationFrame(r)); // the glide starts in the frame after the commit
      const anims = f.getAnimations().map((x) => ({ duration: x.effect.getTiming().duration,
        props: [...new Set(x.effect.getKeyframes().flatMap((k) => Object.keys(k).filter((p) => !['offset', 'easing', 'composite', 'computedOffset'].includes(p))))] }));
      return { atLoad, anims };
    }
  }
  return { atLoad, anims: null };
}"""


async def glide_calm(p, a, r):
    errors: list[str] = []
    for reduced in ("no-preference", "reduce"):
        browser, page, st = await open_page(p, a.url, a.web, path="/", reduced=reduced)
        errors = st["errors"]
        try:
            await wait_text(page, "Accessories sales this month")
            await page.locator("aside").get_by_role("link", name="Sales", exact=True).hover()
            await page.wait_for_timeout(1200)
            got = await page.evaluate(FRAME_ANIM, "Sales")
            r["notes"].append({"reduced": reduced, **got})
            anims = got["anims"]
            if got["atLoad"] != 0:
                fail(r, f"{reduced}: the first page glides (or has no frame): {got['atLoad']}")
            if not anims or len(anims) != 1:
                fail(r, f"{reduced}: the next page has no single glide: {anims}")
                continue
            g = anims[0]
            if reduced == "reduce":
                if g["props"] != ["opacity"] or g["duration"] > 120:
                    fail(r, f"reduced motion: {g} (an opacity fade of 120 ms or less, nothing rises)")
            elif sorted(g["props"]) != ["opacity", "transform"] or g["duration"] != 200:
                fail(r, f"the glide: {g} (transform and opacity, 200 ms)")
        finally:
            await browser.close()
    return {"errors": errors}


async def sidebar_collapse(p, a, r):
    browser, page, st = await open_page(p, a.url, a.web, path="/")
    try:
        await wait_text(page, "Accessories sales this month")
        await page.wait_for_timeout(2500)  # the charts' first-view draw has settled
        got = await page.evaluate("""async () => {
          const aside = document.querySelector('aside');
          const bars = () => [...document.querySelectorAll('main .recharts-bar-rectangle path, main .recharts-rectangle')].map((el) => Math.round(el.getBBox().height));
          const before = bars();
          const widths = [];
          document.querySelector('button[title="Toggle sidebar"]').click();
          const t0 = performance.now(); const samples = [];
          while (performance.now() - t0 < 700) {
            await new Promise((r) => requestAnimationFrame(r));
            widths.push(Math.round(aside.getBoundingClientRect().width));
            if (samples.length < 40) samples.push(bars());
          }
          const after = bars();
          const shrunk = samples.some((s) => s.some((h, i) => before[i] !== undefined && h < before[i] - 1));
          return { widths: [...new Set(widths)], bars: before.length, shrunk, transition: getComputedStyle(aside).transitionProperty, sameAfter: JSON.stringify(after) === JSON.stringify(before) } }""")
        r["notes"].append(got)
        if any(w not in (76, 260) for w in got["widths"]):
            fail(r, f"the sidebar's width animated on collapse: {got['widths']}")
        if got["bars"] and got["shrunk"]:
            fail(r, "a chart replayed its draw on collapse")
        await page.screenshot(path=str(a.out / "sidebar_collapse.png"))
    finally:
        await browser.close()
    return st


async def page_error_keeps_shell(p, a, r):
    browser, page, st = await open_page(p, a.url, a.web, path="/", abort_chunk=True, no_warm=True)
    try:
        await wait_text(page, "Accessories sales this month")
        await page.locator("aside").get_by_role("link", name="Sales", exact=True).click()
        await page.wait_for_timeout(4000)  # the reload rule may reload once (one per build); the page then says so
        body = await page.inner_text("body")
        seen = {"own_error": await page.locator("[data-page-error]").count(), "menu": await page.locator("aside nav a").count(),
                "app_wide": "Something went wrong" in body, "reload": await page.get_by_role("button", name="Reload").count()}
        # no loop: once the error is on screen the page's code is not asked for again and again
        asked = st["chunk_requests"]
        await page.wait_for_timeout(1500)
        seen["chunk_requests"] = [asked, st["chunk_requests"]]
        await page.screenshot(path=str(a.out / "page_error_keeps_shell.png"))
        if st["chunk_requests"] > asked or asked > 4:
            fail(r, f"the failed page's code was asked for {st['chunk_requests']} times (a retry loop)")
        if seen["own_error"] != 1 or seen["menu"] < 5 or seen["app_wide"] or seen["reload"] < 1:
            fail(r, f"the page's own error with Reload inside the shell: {seen}")
        else:
            await page.locator("aside").get_by_role("link", name="Inventory", exact=True).click()
            await page.locator("main h1", has_text="Inventory").first.wait_for(timeout=8000)
            seen["next_page_opens"] = await page.locator("[data-page-error]").count() == 0
            if not seen["next_page_opens"]:
                fail(r, "the next page still shows the last page's error")
        r["notes"].append(seen)
    finally:
        await browser.close()
    return st


async def rep_tabs_keep_place(p, a, r):
    browser, page, st = await open_page(p, a.url, a.web, me=REP, viewport=(390, 844), mobile=True, path="/shop-orders")
    try:
        await page.locator("nav[aria-label='Main'].fixed a[href='/shop-orders']").wait_for(timeout=8000)
        await page.wait_for_function("() => document.scrollingElement.scrollHeight > innerHeight + 1200", timeout=8000)
        await page.evaluate("() => window.scrollTo(0, 900)")
        await page.wait_for_timeout(300)
        tabs = page.locator("nav[aria-label='Main'].fixed")
        await tabs.locator("a[href='/customers']").tap()
        await page.wait_for_timeout(900)
        shops = await page.evaluate("() => Math.round(scrollY)")
        await tabs.locator("a[href='/shop-orders']").tap()
        await page.wait_for_timeout(1500)
        orders = await page.evaluate("() => Math.round(scrollY)")
        await tabs.locator("a[href='/shop-orders']").tap()  # the open tab, again: back to its top
        await page.wait_for_timeout(1200)
        again = await page.evaluate("() => Math.round(scrollY)")
        r["notes"].append({"orders_scrolled": 900, "shops_tab": shops, "orders_tab_again": orders, "open_tab_tapped": again})
        if shops != 0:
            fail(r, f"the Shops tab opened at {shops}, not at its top")
        if abs(orders - 900) > 2:
            fail(r, f"the Orders tab came back at {orders}, not where it was left (900)")
        if again != 0:
            fail(r, f"the open tab tapped again stayed at {again}")
        await page.screenshot(path=str(a.out / "rep_tabs_keep_place.png"))
    finally:
        await browser.close()
    return st


CASES = {
    "s9_window_scrolls": s9_window_scrolls,
    "s9_phone_drawer": s9_phone_drawer,
    "s4_back_restores": s4_back_restores,
    "s5_in_app_skeleton": s5_in_app_skeleton,
    "s5_direct_skeleton": s5_direct_skeleton,
    "s8_click_paints_fast": s8_click_paints_fast,
    "glide_calm": glide_calm,
    "sidebar_collapse": sidebar_collapse,
    "page_error_keeps_shell": page_error_keeps_shell,
    "rep_tabs_keep_place": rep_tabs_keep_place,
}


# ── review screenshots ──────────────────────────────────────────────────────────────────────────

async def shots(p, a, folder: Path):
    folder.mkdir(parents=True, exist_ok=True)
    made = []
    for w, h in ((390, 844), (1366, 860), (1920, 1080)):
        mobile = w < 500
        for theme in ("light", "dark"):
            views = [("dashboard", "/", 0), ("dashboard_scrolled", "/", 900)]
            if mobile:  # a phone's orders are cards in the page; a desktop's long table is its own scroller
                views.append(("orders_scrolled", "/shop-orders", 1200))
            for name, path, scroll in views:
                browser, page, _ = await open_page(p, a.url, a.web, viewport=(w, h), mobile=mobile, theme=theme, path=path)
                try:
                    await page.wait_for_timeout(3500)
                    if scroll:
                        await page.evaluate(f"() => window.scrollTo(0, {scroll})")
                        await page.wait_for_timeout(400)
                    f = folder / f"office_{name}_{w}_{theme}.png"
                    await page.screenshot(path=str(f))
                    made.append(f.name)
                    if name == "dashboard" and not mobile:
                        await page.click('button[title="Toggle sidebar"]')
                        await page.wait_for_timeout(600)
                        f = folder / f"office_{name}_collapsed_{w}_{theme}.png"
                        await page.screenshot(path=str(f))
                        made.append(f.name)
                        await page.click('button[title="Toggle sidebar"]')  # leave it expanded for the next run
                        await page.wait_for_timeout(300)
                finally:
                    await browser.close()
        for name, kw in (("skeleton", {"hold": 8.0, "no_warm": True}), ("page_error", {"abort_chunk": True, "no_warm": True})):
            browser, page, _ = await open_page(p, a.url, a.web, viewport=(w, h), mobile=mobile, path="/sales", **kw)
            try:
                await page.wait_for_timeout(4000 if name == "page_error" else 2500)
                f = folder / f"office_{name}_{w}_light.png"
                await page.screenshot(path=str(f))
                made.append(f.name)
            finally:
                await browser.close()
        browser, page, _ = await open_page(p, a.url, a.web, me=REP, viewport=(w, h), mobile=mobile, path="/shop-orders")
        try:
            await page.wait_for_timeout(3500)
            await page.evaluate("() => window.scrollTo(0, 900)")
            await page.wait_for_timeout(400)
            f = folder / f"rep_orders_scrolled_{w}_light.png"
            await page.screenshot(path=str(f))
            made.append(f.name)
        finally:
            await browser.close()
    print(json.dumps({"shots": len(made), "folder": str(folder)}))


async def main_async(a) -> int:
    a.out = Path(a.out)
    a.out.mkdir(parents=True, exist_ok=True)
    a.web = Path(a.web)
    only = set(a.only.split(",")) if a.only else None
    bad = 0
    async with async_playwright() as p:
        if a.shots:
            await shots(p, a, Path(a.shots))
            if not a.only:
                return 0
        for name, fn in CASES.items():
            if only and name not in only:
                continue
            r = {"case": name, "ok": True, "notes": []}
            try:
                st = await fn(p, a, r)
                if st and st.get("errors"):
                    fail(r, f"page errors: {st['errors'][:3]}")
            except Exception as e:  # noqa: BLE001
                fail(r, f"error: {str(e).splitlines()[0][:300]}")
            bad += 0 if r["ok"] else 1
            print(json.dumps(r, default=str))
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:5183")
    ap.add_argument("--web", default=str(Path(__file__).resolve().parents[1]), help="the web/ folder (its .env)")
    ap.add_argument("--out", default=str(Path(tempfile.gettempdir()) / "yq-shell-render"))
    ap.add_argument("--only", default="")
    ap.add_argument("--shots", default="", help="also write the review screenshots to this folder")
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
