"""
Smoothness checks (the 28-Oct-2026 polish, plan S1–S6). market_qa.py runs them on its lead
viewports; portal_qa.py reuses the generic ones (scroll_jank, back_scroll, lazy_open).

Every function drives a page the caller has already opened and returns its findings as
(level, check, detail, values) tuples. "fail" is a hard failure, "warn" a warning, and "note" a
measured number within its target, which the caller keeps as a run note. Nothing here writes
anywhere: the pages run inside the caller's no-prod-writes context.

  S1 scroll-jank      frame times of a scripted scroll at 4x CPU (a mid phone): measured, never fatal
  S2 photo-blank      a photo tile 150 ms after landing on a screen is loaded or drawing a placeholder
  S3 panel-scroll     closing a product panel leaves the page where it was (within 2 px)
  S4 back-scroll      Back to a scrolled page lands where it was (within 24 px)
  S5 lazy-blank       a page opened while its code is still downloading shows the shell and a skeleton
  S6 sheet-drag       a sheet follows the finger, and never rises above its resting place on the way in
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from playwright.sync_api import Error as PWError
from playwright.sync_api import Page

Finding = tuple[str, str, str, Any]

# ── S1 ─────────────────────────────────────────────────────────────────────────────────────────
SCROLL_FRAMES = r"""
async ({ speed, px }) => {
  const doc = document.scrollingElement || document.documentElement;
  const end = Math.min(doc.scrollHeight - innerHeight, px);
  const deltas = [];
  let last = performance.now();
  await new Promise((done) => {
    const t0 = performance.now(), dur = Math.max(1, end) / speed * 1000;
    const step = (t) => {
      deltas.push(t - last); last = t;
      const k = Math.min(1, (t - t0) / dur);
      window.scrollTo(0, end * k);
      k < 1 ? requestAnimationFrame(step) : done();
    };
    requestAnimationFrame(step);
  });
  deltas.shift();
  const s = deltas.slice().sort((a, b) => a - b);
  const q = (p) => (s.length ? s[Math.min(s.length - 1, Math.floor(p * s.length))] : 0);
  return { end, frames: s.length, p50: +q(0.5).toFixed(1), p95: +q(0.95).toFixed(1),
           max: s.length ? +s[s.length - 1].toFixed(1) : 0, over50: s.filter((d) => d > 50).length };
}
"""


def scroll_jank(page: Page, cpu: int = 4, px: int = 6000, speed: int = 900) -> list[Finding]:
    """S1: a rAF-driven scroll (900 px/s, the first 6000 px) under CPU throttling."""
    cdp = page.context.new_cdp_session(page)
    try:
        page.evaluate("() => window.scrollTo(0, 0)")
        page.wait_for_timeout(400)
        cdp.send("Emulation.setCPUThrottlingRate", {"rate": cpu})
        r = page.evaluate(SCROLL_FRAMES, {"speed": speed, "px": px})
    finally:
        try:
            cdp.send("Emulation.setCPUThrottlingRate", {"rate": 1})
            cdp.detach()
        except PWError:
            pass
    page.evaluate("() => window.scrollTo(0, 0)")
    detail = (f"S1 scroll at {cpu}x CPU: p95 {r['p95']} ms, {r['over50']} frame(s) over 50 ms, "
              f"worst {r['max']} ms ({r['frames']} frames)")
    return [("warn" if r["p95"] > 20 or r["over50"] else "note", "scroll-jank", detail, r)]


# ── S2 ─────────────────────────────────────────────────────────────────────────────────────────
PHOTO_TILES = r"""
() => {
  const vh = innerHeight, vw = innerWidth;
  const out = { checked: 0, loaded: 0, drawing: 0, blank: [] };
  const plain = new Set(['rgba(0, 0, 0, 0)', 'transparent', 'rgb(255, 255, 255)']);
  const running = (el) => el.getAnimations({ subtree: true }).some((a) => a.playState === 'running');
  const drawn = (el) => {
    const cs = getComputedStyle(el);
    if (cs.backgroundImage !== 'none' || !plain.has(cs.backgroundColor)) return true;
    for (const pe of ['::before', '::after']) {
      const p = getComputedStyle(el, pe);
      if (p.content !== 'none' && p.content !== 'normal' && (p.backgroundImage !== 'none' || !plain.has(p.backgroundColor))) return true;
    }
    return false;
  };
  for (const img of document.querySelectorAll('img:not([aria-hidden="true"])')) {
    const src = (img.currentSrc || img.src || '') + ' ' + (img.getAttribute('srcset') || '');
    if (!/\/thumbs\/|\/storage\/v1\/object\//.test(src)) continue;
    const r = img.getBoundingClientRect();
    if (r.width < 40 || r.height < 40 || r.bottom <= 0 || r.top >= vh || r.right <= 0 || r.left >= vw) continue;
    out.checked++;
    if (img.complete && img.naturalWidth > 0) { out.loaded++; continue; }
    // not painted yet: its tile has to draw something meanwhile (a shimmer, a tint, a low-res photo)
    const tile = img.parentElement;
    const lowRes = tile && [...tile.querySelectorAll('img')].some((o) => o !== img && o.complete && o.naturalWidth > 0);
    if (tile && (running(tile) || drawn(tile) || lowRes)) { out.drawing++; continue; }
    out.blank.push((img.alt || src.split('/').pop() || '?').slice(0, 60));
  }
  return out;
}
"""


def photo_tiles(page: Page, stops: tuple[float, ...] = (1.2, 3.0, 5.0)) -> list[Finding]:
    """S2: jump to each stop (in screens), wait 150 ms, and look at every product photo in view."""
    vh = page.evaluate("() => innerHeight")
    blank: list[str] = []
    seen: list[dict] = []
    checked = 0
    for k in stops:
        page.evaluate("(y) => window.scrollTo(0, y)", int(vh * k))
        page.wait_for_timeout(150)
        r = page.evaluate(PHOTO_TILES)
        checked += r["checked"]
        seen.append({"at_screens": k, "checked": r["checked"], "loaded": r["loaded"], "drawing": r["drawing"], "blank": len(r["blank"])})
        blank += r["blank"]
    page.evaluate("() => window.scrollTo(0, 0)")
    page.wait_for_timeout(300)
    if not checked:
        return [("warn", "photo-blank", "S2: no product photo was in view at the stops, not measured", seen)]
    if blank:
        return [("fail", "photo-blank", f"S2: {len(blank)} photo tile(s) empty 150 ms after landing "
                 "(no shimmer, tint or low-res photo): " + ", ".join(blank[:4]), seen)]
    return [("note", "photo-blank", f"S2: {checked} photo tiles in view at 150 ms, every one loaded or drawing a placeholder", seen)]


# ── S3 ─────────────────────────────────────────────────────────────────────────────────────────
def _scroll_y(page: Page) -> int:
    return int(page.evaluate("() => Math.round(window.scrollY)"))


def _quick_view(page: Page, label: str, min_y: int = 0, in_view: bool = False):
    """The first card's quick-view button at least `min_y` px down the page (or inside the viewport)."""
    btns = page.locator(f'main button[aria-label^="{label}:"]')
    for i in range(min(btns.count(), 80)):
        b = btns.nth(i)
        try:
            box = b.evaluate("(el) => { const r = el.getBoundingClientRect();"
                             " return { top: r.top, bottom: r.bottom, y: r.top + window.scrollY, w: r.width }; }")
        except PWError:
            continue
        if box["w"] < 20:
            continue
        if in_view and (box["top"] < 80 or box["bottom"] > page.viewport_size["height"] - 120):
            continue
        if box["y"] >= min_y:
            return b
    return None


def panel_close_scroll(page: Page, quick_view: str, close: str, min_y: int = 1400) -> list[Finding]:
    """S3: open a card's panel well down the page, close it, and compare the scroll position."""
    target = _quick_view(page, quick_view, min_y)
    if target is None:
        return [("warn", "panel-scroll", f"S3: no quick-view card below {min_y}px, not measured", None)]
    target.scroll_into_view_if_needed()
    page.wait_for_timeout(500)
    before = _scroll_y(page)
    target.evaluate("(el) => el.click()")
    try:
        page.wait_for_selector('[role="dialog"]', timeout=8000)
    except PWError:
        return [("fail", "panel-scroll", "S3: the quick view did not open a dialog", {"before": before})]
    page.wait_for_timeout(700)
    x = page.locator(f'[role="dialog"] button[aria-label="{close}"]')
    if x.count():
        x.first.evaluate("(el) => el.click()")
    else:
        page.keyboard.press("Escape")
    page.wait_for_timeout(900)
    after = _scroll_y(page)
    vals = {"before": before, "after": after}
    if abs(after - before) > 2:
        return [("fail", "panel-scroll", f"S3: closing the product panel moved the page from {before}px to {after}px", vals)]
    return [("note", "panel-scroll", f"S3: the page stayed at {after}px after the panel closed", vals)]


# ── S4 ─────────────────────────────────────────────────────────────────────────────────────────
FOLLOW_LINK = r"""
(pattern) => {
  const re = new RegExp(pattern);
  const a = [...document.querySelectorAll('a[href]')].find((el) => re.test(el.getAttribute('href') || ''));
  if (!a) return null;
  a.click();
  return a.getAttribute('href');
}
"""


ANCHOR = r"""
(label) => {
  // the card the merchant was looking at: the first quick-view button below the sticky chrome
  const btns = [...document.querySelectorAll('main button[aria-label]')]
    .filter((b) => /:/.test(b.getAttribute('aria-label') || ''));
  if (label == null) {
    const hit = btns.find((b) => { const r = b.getBoundingClientRect(); return r.width > 20 && r.top > 160 && r.top < innerHeight - 160; });
    return hit ? { label: hit.getAttribute('aria-label'), top: Math.round(hit.getBoundingClientRect().top) } : null;
  }
  const same = btns.filter((b) => b.getAttribute('aria-label') === label && b.getBoundingClientRect().width > 20);
  return same.length ? same.map((b) => Math.round(b.getBoundingClientRect().top)) : null;
}
"""


def back_scroll(page: Page, y: int = 1600, link: str = r"^/(shop|t/)") -> list[Finding]:
    """S4: scroll, follow an in-app link, press Back, and compare what is on screen: the same card at
    the same height (within 24 px), else the scroll position. Chrome's scroll anchoring may move
    scrollY when content above finishes rendering, while keeping exactly this card in place."""
    page.evaluate("(y) => window.scrollTo(0, y)", y)
    page.wait_for_timeout(700)
    before = _scroll_y(page)
    anchor = page.evaluate(ANCHOR, None)
    url0 = page.url
    href = page.evaluate(FOLLOW_LINK, link)
    if not href:
        return [("warn", "back-scroll", f"S4: no in-app link matching {link}, not measured", None)]
    try:
        page.wait_for_url(lambda u: u != url0, timeout=8000)
    except PWError:
        return [("warn", "back-scroll", f"S4: following {href} did not change the page, not measured", None)]
    page.wait_for_timeout(1200)
    page.go_back(wait_until="commit")
    page.wait_for_timeout(1500)
    after = _scroll_y(page)
    vals: dict = {"before": before, "after": after, "via": href}
    if anchor:
        tops = page.evaluate(ANCHOR, anchor["label"]) or []
        shift = min((abs(t - anchor["top"]) for t in tops), default=None)
        vals.update({"card": anchor["label"][:60], "card_top_before": anchor["top"], "card_tops_after": tops[:3]})
        if shift is not None and shift <= 24:
            return [("note", "back-scroll", f"S4: Back from {href} put the same card back on screen ({shift}px off; page at {after}px)", vals)]
    if abs(after - before) > 24:
        return [("fail", "back-scroll", f"S4: Back from {href} landed at {after}px, not where the page was ({before}px)", vals)]
    return [("note", "back-scroll", f"S4: Back from {href} returned to {after}px", vals)]


# ── S5 ─────────────────────────────────────────────────────────────────────────────────────────
LAZY_PROBE = r"""
() => {
  const vis = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect(), cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none' && +cs.opacity > 0.05;
  };
  const header = [...document.querySelectorAll('header, nav, [role="banner"]')].some(vis);
  const busy = [...document.querySelectorAll('[aria-busy="true"]')].some(vis);
  const main = document.querySelector('main');
  const mainText = ((main && main.innerText) || '').trim().length;
  const root = document.getElementById('root');
  const painted = root ? [...root.querySelectorAll('*')].filter((el) => vis(el) && el.getBoundingClientRect().height > 20).length : 0;
  return { header, busy, mainText, painted };
}
"""


def lazy_open(page: Page, url: str, look_at: int = 900) -> list[Finding]:
    """S5: open `url` with every code chunk the HTML does not name held back, and look at the page
    while they wait. The entry and its preloads load normally, so this is the moment a phone on a
    slow network sees between the app starting and the page's own code arriving."""
    html = page.context.request.get(url).text()
    boot = set(re.findall(r'(?:src|href)="(/assets/[^"]+\.js)"', html))
    cdp = page.context.new_cdp_session(page)
    held: list[str] = []
    release = {"now": False, "closed": False}

    def paused(ev: dict) -> None:
        if release["closed"]:
            return  # a pause that raced Fetch.disable: the request goes on by itself
        path = urlparse(ev["request"]["url"]).path
        if release["now"] or path in boot:
            try:
                cdp.send("Fetch.continueRequest", {"requestId": ev["requestId"]})
            except PWError:
                pass
        else:
            held.append(ev["requestId"])

    cdp.on("Fetch.requestPaused", paused)
    cdp.send("Fetch.enable", {"patterns": [{"urlPattern": "*/assets/*.js", "requestStage": "Request"}]})
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=40000)
        page.wait_for_timeout(look_at)
        r = page.evaluate(LAZY_PROBE)
    finally:
        release["now"] = True
        for rid in held:
            try:
                cdp.send("Fetch.continueRequest", {"requestId": rid})
            except PWError:
                pass
        release["closed"] = True
        try:
            cdp.send("Fetch.disable")
            cdp.detach()
        except PWError:
            pass
    r["held_chunks"] = len(held)
    if not held:
        return [("warn", "lazy-blank", "S5: the page asked for no code beyond the HTML's, not measured", r)]
    if r["header"] and (r["busy"] or r["mainText"] > 40):
        return [("note", "lazy-blank", f"S5: with {len(held)} chunk(s) still loading the shell and a skeleton show", r)]
    what = "no header" if not r["header"] else "an empty page area (no skeleton)"
    return [("fail", "lazy-blank", f"S5: with {len(held)} chunk(s) still loading the page shows {what}", r)]


# ── reveal v2 (motion list M6, M7, M16) ───────────────────────────────────────────────────────────
REVEAL_STATE = r"""
() => {
  const all = [...document.querySelectorAll('.reveal')];
  const fold = innerHeight;
  const below = all.filter((el) => el.getBoundingClientRect().top >= fold);
  // stuck = still hidden although it is in or above the viewport (below the fold it is just not reached)
  const hidden = all.filter((el) => el.getBoundingClientRect().top < fold && +getComputedStyle(el).opacity < 0.99);
  return { total: all.length, below: below.length, hidden: hidden.length,
           instant: all.filter((el) => el.classList.contains('is-instant')).length,
           lite: 'lite' in document.documentElement.dataset };
}
"""


def reveal_phone(page: Page) -> list[Finding]:
    """M6 on a phone: the home has sections that rise in below the fold (before: phones got none)."""
    page.evaluate("() => window.scrollTo(0, 0)")
    page.wait_for_timeout(300)
    r = page.evaluate(REVEAL_STATE)
    if r["below"] == 0:
        return [("fail", "reveal-phone", f"M6: no scroll reveal below the fold on this phone ({r['total']} .reveal in all)", r)]
    return [("note", "reveal-phone", f"M6: {r['below']} section(s) rise in below the fold", r)]


def reveal_flick(page: Page, speed: int = 4000, px: int = 6000) -> list[Finding]:
    """M7: a flick faster than 2000 px/s reveals at once (`is-instant`), and nothing stays hidden after it."""
    page.evaluate("() => window.scrollTo(0, 0)")
    page.wait_for_timeout(300)
    page.evaluate(SCROLL_FRAMES, {"speed": speed, "px": px})
    page.wait_for_timeout(450)
    r = page.evaluate(REVEAL_STATE)
    page.evaluate("() => window.scrollTo(0, 0)")
    out: list[Finding] = []
    if r["total"] and not r["instant"]:
        out.append(("fail", "reveal-flick", f"M7: a {speed} px/s flick still animated every reveal (none instant of {r['total']})", r))
    if r["hidden"]:
        out.append(("fail", "reveal-stuck", f"M7: {r['hidden']} .reveal still hidden after the flick", r))
    return out or [("note", "reveal-flick", f"M7: {r['instant']} of {r['total']} reveals were instant during the flick", r)]


LOW_MEMORY = "Object.defineProperty(navigator, 'deviceMemory', { get: () => 1, configurable: true });"


def lite_mode(page: Page) -> list[Finding]:
    """M16: a page opened with navigator.deviceMemory = 1 (the caller's init script LOW_MEMORY) runs lite:
    html[data-lite] is set and no reveal hides anything."""
    page.evaluate("() => window.scrollTo(0, document.documentElement.scrollHeight)")
    page.wait_for_timeout(200)
    r = page.evaluate(REVEAL_STATE)
    page.evaluate("() => window.scrollTo(0, 0)")
    if not r["lite"]:
        return [("fail", "lite-mode", "M16: a 1 GB phone did not get html[data-lite]", r)]
    if r["hidden"]:
        return [("fail", "lite-mode", f"M16: lite mode still hides {r['hidden']} .reveal", r)]
    return [("note", "lite-mode", "M16: lite mode on, nothing hidden", r)]


# ── layers and focus (T6: Escape stack, autoFocus kept) ───────────────────────────────────────────
DIALOGS = "() => [...document.querySelectorAll('[role=\"dialog\"]')].filter((d) => d.getBoundingClientRect().height > 0).length"


def escape_stack(page: Page, quick_view: str, zoom: str = "Zoom photo") -> list[Finding]:
    """Escape in the photo viewer over a product panel closes the viewer only, never both."""
    btn = _quick_view(page, quick_view, in_view=True) or _quick_view(page, quick_view)
    if btn is None:
        return [("warn", "escape-stack", "no quick-view card, not measured", None)]
    btn.scroll_into_view_if_needed()
    btn.evaluate("(el) => el.click()")
    try:
        page.wait_for_selector('[role="dialog"]', timeout=8000)
        page.wait_for_timeout(500)
        z = page.locator(f'[role="dialog"] button[aria-label="{zoom}"]')
        if not z.count():
            return [("warn", "escape-stack", "the panel has no zoom button, not measured", None)]
        z.first.evaluate("(el) => el.click()")
        page.wait_for_timeout(500)
        before = page.evaluate(DIALOGS)
        page.keyboard.press("Escape")
        page.wait_for_timeout(600)
        after = page.evaluate(DIALOGS)
    except PWError as exc:
        return [("fail", "escape-stack", "Playwright error: " + str(exc)[:160], None)]
    vals = {"open_before": before, "open_after": after}
    if before >= 2 and after == before - 1:
        return [("note", "escape-stack", "Escape closed the photo viewer and left the product panel open", vals)]
    return [("fail", "escape-stack", f"Escape over the photo viewer left {after} of {before} layers open (expected {before - 1})", vals)]


def palette_focus(page: Page) -> list[Finding]:
    """Ctrl+K opens the search palette with the typing focus in its field (autoFocus kept)."""
    page.keyboard.press("Control+k")
    try:
        page.wait_for_selector('[role="dialog"] input', timeout=6000)
    except PWError:
        return [("warn", "palette-focus", "Ctrl+K opened no palette with a field, not measured", None)]
    page.wait_for_timeout(400)
    r = page.evaluate("() => { const a = document.activeElement; return { tag: a && a.tagName, inDialog: !!(a && a.closest('[role=\"dialog\"]')), label: a && (a.getAttribute('aria-label') || a.getAttribute('placeholder') || '') } }")
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    if r["tag"] == "INPUT" and r["inDialog"]:
        return [("note", "palette-focus", "the palette opened with the typing focus in its field", r)]
    return [("fail", "palette-focus", f"the palette opened with the focus on {r['tag']} ({r['label'][:40]}), not its field", r)]


FLYING = r"""
() => {
  const clones = [...document.querySelectorAll('body > img[aria-hidden="true"]')].filter((i) => getComputedStyle(i).position === 'fixed');
  const hero = document.querySelector('[role="dialog"] .m-photo-img');
  return { clones: clones.length, heroOpacity: hero ? getComputedStyle(hero).opacity : null, heroInline: hero ? hero.style.opacity : null };
}
"""


def photo_flight(page: Page, quick_view: str) -> list[Finding]:
    """M4: tapping a card's photo flies a clone of it into the sheet; it lands and is removed, and the
    sheet's own photo is visible afterwards."""
    btn = _quick_view(page, quick_view)
    if btn is None:
        return [("warn", "photo-flight", "M4: no quick-view card, not measured", None)]
    # the first card, centred on screen with its photo decoded: the flight only starts from a visible photo
    btn.evaluate("(el) => el.scrollIntoView({ block: 'center' })")
    page.wait_for_timeout(600)
    try:
        page.wait_for_function("(el) => { const i = el.querySelector('img'); return i && i.complete && i.naturalWidth > 0 }", arg=btn.element_handle(), timeout=6000)
    except PWError:
        return [("warn", "photo-flight", "M4: the card's photo did not load, not measured", None)]
    btn.evaluate("(el) => el.click()")
    page.wait_for_timeout(80)
    during = page.evaluate(FLYING)
    page.wait_for_timeout(900)
    after = page.evaluate(FLYING)
    vals = {"during": during, "after": after}
    x = page.locator('[role="dialog"] button[aria-label="Close"]')
    if x.count():
        x.first.evaluate("(el) => el.click()")
        page.wait_for_timeout(400)
    if not during["clones"]:
        return [("fail", "photo-flight", "M4: no photo flew from the card into the sheet", vals)]
    if after["clones"] or after["heroInline"] == "0":
        return [("fail", "photo-flight", "M4: the flight left a clone or a hidden photo behind", vals)]
    return [("note", "photo-flight", "M4: the card's photo flew into the sheet and handed over", vals)]


def back_from_entry(page: Page, base: str, path: str = "/shop", back_label: str = "Back") -> list[Finding]:
    """T9: a merchant who arrived from another site (WhatsApp, Google) and taps the header's Back on
    the first page lands on the shop's Home — never back out of the shop."""
    page.goto("about:blank")
    page.goto(base + path, wait_until="domcontentloaded", timeout=40000)
    page.wait_for_timeout(3500)
    btn = page.locator(f'header button[aria-label="{back_label}"], button[aria-label="{back_label}"]').first
    if not btn.count():
        return [("warn", "back-entry", f"T9: no Back button on {path}, not measured", None)]
    try:
        btn.evaluate("(el) => el.click()")
    except PWError:
        pass  # the page navigated away during the click: that is the bug this check looks for
    page.wait_for_timeout(1500)
    url = page.url
    if url.startswith(base) and url[len(base):].split("?")[0] in ("", "/"):
        return [("note", "back-entry", f"T9: Back on the first page went Home ({url})", None)]
    return [("fail", "back-entry", f"T9: Back on the first page left the shop for {url}", {"url": url})]


# ── S6 ─────────────────────────────────────────────────────────────────────────────────────────
TRACK_TOPS = r"""
(ms) => {
  window.__qaTops = [];
  const t0 = performance.now();
  const loop = () => {
    const d = document.querySelector('[role="dialog"]');
    if (d) window.__qaTops.push(Math.round(d.getBoundingClientRect().top * 10) / 10);
    if (performance.now() - t0 < ms) requestAnimationFrame(loop);
  };
  requestAnimationFrame(loop);
}
"""

DIALOG_TOP = "() => { const d = document.querySelector('[role=\"dialog\"]'); if (!d) return null;" \
             " const r = d.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + 10, top: r.top }; }"


def sheet_drag(page: Page, quick_view: str, close: str) -> list[Finding]:
    """S6 (touch): open a product sheet while tracking its top edge, then drag it 60 px down
    without letting go and see whether it follows."""
    out: list[Finding] = []
    page.evaluate("() => window.scrollTo(0, 0)")
    page.wait_for_timeout(300)
    btn = _quick_view(page, quick_view, in_view=True) or _quick_view(page, quick_view)
    if btn is None:
        return [("warn", "sheet-drag", "S6: no quick-view card, not measured", None)]
    btn.scroll_into_view_if_needed()
    page.wait_for_timeout(300)
    page.evaluate(TRACK_TOPS, 1300)
    btn.evaluate("(el) => el.click()")
    page.wait_for_timeout(1400)
    tops = page.evaluate("() => window.__qaTops || []")
    if not tops:
        return [("fail", "sheet-drag", "S6: the quick view opened no dialog", None)]
    rest = tops[-1]
    lift = round(rest - min(tops), 1)
    if lift > 1.5:
        out.append(("fail", "sheet-overshoot", f"S6: the sheet rose {lift}px above its resting place on the way in", {"rest": rest, "highest": min(tops)}))
    box = page.evaluate(DIALOG_TOP)
    cdp = page.context.new_cdp_session(page)
    try:
        x, y0 = box["x"], box["y"]
        cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y0}]})
        for dy in range(6, 61, 6):
            cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": x, "y": y0 + dy}]})
            page.wait_for_timeout(16)
        page.wait_for_timeout(80)
        during = page.evaluate(DIALOG_TOP)
        cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    finally:
        try:
            cdp.detach()
        except PWError:
            pass
    moved = round((during or box)["top"] - box["top"], 1)
    vals = {"finger_px": 60, "sheet_px": moved}
    if moved < 40:
        out.append(("fail", "sheet-drag", f"S6: with the finger 60 px down the sheet moved {moved}px", vals))
    else:
        out.append(("note", "sheet-drag", f"S6: the sheet followed the finger ({moved}px of 60)", vals))
    page.wait_for_timeout(700)
    x_btn = page.locator(f'[role="dialog"] button[aria-label="{close}"]')
    if x_btn.count():
        x_btn.first.evaluate("(el) => el.click()")
        page.wait_for_timeout(500)
    return out
