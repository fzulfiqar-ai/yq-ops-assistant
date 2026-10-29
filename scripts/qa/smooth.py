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


# ── M1: the photo's own fade ──────────────────────────────────────────────────────────────────────
PHOTO_FADE = r"""
() => {
  const shown = [...document.querySelectorAll('.m-photo-img')].filter((i) => {
    const r = i.getBoundingClientRect();
    return r.width > 40 && r.bottom > 0 && r.top < innerHeight;
  });
  // a photo that decoded mid-scroll shows at once by design (ProductImage's data-instant): not a pop-in
  const instant = shown.filter((i) => i.parentElement && 'instant' in i.parentElement.dataset);
  const imgs = shown.filter((i) => !instant.includes(i));
  const bad = imgs.map((i) => getComputedStyle(i).transitionProperty).filter((p) => !/\b(opacity|all)\b/.test(p));
  return { checked: imgs.length, instant: instant.length, without: bad.length, sample: [...new Set(bad)].slice(0, 3) };
}
"""


def photo_fade(page: Page) -> list[Finding]:
    """M1: every product photo on screen fades on its own opacity. A card's `transition-transform`
    utility (its hover zoom) used to replace the fade, so card photos popped in over the tint."""
    page.evaluate("() => window.scrollTo(0, 1200)")
    page.wait_for_timeout(900)
    r = page.evaluate(PHOTO_FADE)
    page.evaluate("() => window.scrollTo(0, 0)")
    if not r["checked"]:
        return [("warn", "photo-fade", "M1: no product photo on screen, not measured", r)]
    if r["without"]:
        return [("fail", "photo-fade", f"M1: {r['without']} of {r['checked']} photos on screen have no opacity transition (they pop in)", r)]
    return [("note", "photo-fade", f"M1: all {r['checked']} photos on screen fade in", r)]


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
    # sampled once the sheet is there: a script click sends no pointerdown, so the panel's code may
    # still be loading (it warms at idle or on a press) and the sheet, and its flight, come later
    try:
        page.wait_for_selector('[role="dialog"]', timeout=3000)
    except PWError:
        pass
    page.wait_for_timeout(60)
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
    """T9: a merchant who arrived from another site (WhatsApp, Google), tapped a filter chip (which
    REPLACES the history entry: a new key on the same first entry) and then the header's Back lands
    on the shop's Home — never back out of the shop."""
    page.goto("about:blank")
    page.goto(base + path, wait_until="domcontentloaded", timeout=40000)
    page.wait_for_timeout(3500)
    chip = page.locator('button[aria-pressed="false"]:not([aria-label])').first  # a filter chip (the view toggles carry labels)
    tapped = bool(chip.count())
    if tapped:
        chip.evaluate("(el) => el.click()")
        page.wait_for_timeout(900)
    btn = page.locator(f'header button[aria-label="{back_label}"], button[aria-label="{back_label}"]').first
    if not btn.count():
        return [("warn", "back-entry", f"T9: no Back button on {path}, not measured", None)]
    try:
        btn.evaluate("(el) => el.click()")
    except PWError:
        pass  # the page navigated away during the click: that is the bug this check looks for
    page.wait_for_timeout(1500)
    url = page.url
    vals = {"url": url, "chip_tapped": tapped}
    if url.startswith(base) and url[len(base):].split("?")[0] in ("", "/"):
        return [("note", "back-entry", f"T9: Back on the first page went Home ({url}){' after a filter chip' if tapped else ''}", vals)]
    return [("fail", "back-entry", f"T9: Back on the first page left the shop for {url}{' after a filter chip' if tapped else ''}", vals)]


# ── M3: a warmed page opens without a skeleton ────────────────────────────────────────────────────
# PageSkeleton (ui/Skeleton.tsx): aria-busy with the 28 px heading bar first (grids have their own)
SKELETON_WATCH = r"""
() => {
  const t0 = performance.now();
  const r = (window.__qaSk = { frames: 0, busy: 0, first: null });
  const loop = (t) => {
    r.frames++;
    if (document.querySelector('[aria-busy="true"] > .skeleton.h-7')) {
      r.busy++;
      if (r.first === null) r.first = Math.round(t - t0);
    }
    if (t - t0 < 1500) requestAnimationFrame(loop);
  };
  requestAnimationFrame(loop);
}
"""


def page_wait(page: Page, href: str = "/shop") -> list[Finding]:
    """M3: once the shell has sat idle, a tap to a main page shows that page, not its skeleton. (A fresh
    Suspense boundary commits the skeleton for a page whose code was never imported, and React holds
    a shown fallback for at least 300 ms, so every first visit flashed one.)"""
    page.wait_for_timeout(4000)  # the idle warm-up
    link = page.locator(f'nav a[href="{href}"]').first
    if not link.count():
        return [("warn", "page-wait", f"M3: no nav link to {href}, not measured", None)]
    page.evaluate(SKELETON_WATCH)
    link.evaluate("(el) => el.click()")
    page.wait_for_timeout(1700)
    r = page.evaluate("() => window.__qaSk")
    if r and r["busy"]:
        return [("fail", "page-wait", f"M3: tapping {href} after the shell sat idle showed the page skeleton for {r['busy']} frame(s), from {r['first']} ms", r)]
    return [("note", "page-wait", f"M3: {href} opened with no skeleton", r)]


# ── layers: a product opened from the palette ─────────────────────────────────────────────────────
LAYERS = r"""
() => {
  const ds = [...document.querySelectorAll('[role="dialog"]')].filter((d) => d.getBoundingClientRect().height > 0);
  const pal = ds.find((d) => d.querySelector('input'));
  const panel = ds.find((d) => d !== pal && d.querySelector('button[aria-label="Zoom photo"]'));
  const host = document.activeElement && document.activeElement.closest('[role="dialog"]');
  return { open: ds.length, palette: Boolean(pal), panel: Boolean(panel),
           focus: host ? (host === panel ? 'panel' : host === pal ? 'palette' : 'other dialog') : (document.activeElement || {}).tagName };
}
"""


def palette_layers(page: Page, query: str = "cable", quick_view: str = "Quick view") -> list[Finding]:
    """A product opened from a ⌘K result sits on top: the focus is in it and Escape closes it first,
    leaving the palette. (A sheet's open effect re-ran whenever its onClose changed identity: the
    palette re-pushed itself above the product, took the focus, and Escape closed the hidden one.)"""
    page.keyboard.press("Control+k")
    try:
        page.wait_for_selector('[role="dialog"] input', timeout=6000)
    except PWError:
        return [("warn", "palette-layers", "Ctrl+K opened no palette, not measured", None)]
    page.keyboard.type(query, delay=40)
    page.wait_for_timeout(1500)
    card = page.locator(f'[role="dialog"] button[aria-label^="{quick_view}"]').first
    if not card.count():
        page.keyboard.press("Escape")
        return [("warn", "palette-layers", f"no result card for “{query}”, not measured", None)]
    card.evaluate("(el) => el.click()")
    page.wait_for_timeout(1200)
    opened = page.evaluate(LAYERS)
    page.keyboard.press("Escape")
    page.wait_for_timeout(700)
    after = page.evaluate(LAYERS)
    for _ in range(2):
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
    vals = {"opened": opened, "after_escape": after}
    if not opened["panel"]:
        return [("warn", "palette-layers", "the result opened no product panel over the palette, not measured", vals)]
    if opened["focus"] != "panel" or after["panel"] or not after["palette"]:
        return [("fail", "palette-layers", f"a product opened from the palette: focus in the {opened['focus']}, and Escape left " + ("the product open" if after["panel"] else "no palette"), vals)]
    return [("note", "palette-layers", "the product opened on top with the focus; Escape closed it and left the palette", vals)]


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
# the dim behind the sheet (ui/Sheet: the backdrop is the panel's previous sibling)
DIM = "() => { const p = document.querySelector('[data-sheet-panel]'); const b = p && p.previousElementSibling;" \
      " return b ? Math.round(+getComputedStyle(b).opacity * 1000) / 1000 : null; }"


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
        dim = page.evaluate(DIM)
        cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    finally:
        try:
            cdp.detach()
        except PWError:
            pass
    moved = round((during or box)["top"] - box["top"], 1)
    vals = {"finger_px": 60, "sheet_px": moved, "dim": dim}
    if moved < 40:
        out.append(("fail", "sheet-drag", f"S6: with the finger 60 px down the sheet moved {moved}px", vals))
    else:
        out.append(("note", "sheet-drag", f"S6: the sheet followed the finger ({moved}px of 60)", vals))
    # the dim lifts with the drag (M5): an entrance animation that fills forwards pins it at full
    if moved >= 40 and dim is not None and dim > 0.97:
        out.append(("fail", "sheet-dim", f"S6: with the sheet {moved}px down the dim behind it stayed at {dim}", vals))
    page.wait_for_timeout(700)
    x_btn = page.locator(f'[role="dialog"] button[aria-label="{close}"]')
    if x_btn.count():
        x_btn.first.evaluate("(el) => el.click()")
        page.wait_for_timeout(500)
    return out


# ── Browse menu (29-Oct-2026) ──────────────────────────────────────────────────────────────────
# The owner's report: with a mouse, the category strip painted over the menu's first 45 px (each
# frosted layer was its own stacking context), so a click on the menu's first row opened the strip
# chip under it; and a click on Browse closed the menu that hovering had just opened.
MENU_STATE = r"""
() => {
  const btn = document.querySelector('header button[aria-haspopup]');
  const menu = document.querySelector('header [role="menu"]');
  const out = { expanded: btn ? btn.getAttribute('aria-expanded') === 'true' : null, menu: !!menu };
  const first = menu && menu.querySelector('[role="menuitem"]');
  if (!first) return out;
  const r = first.getBoundingClientRect();
  const x = r.left + Math.min(24, r.width / 2);
  const y = r.top + r.height / 2;
  const hit = document.elementFromPoint(x, y);
  return { ...out, first: (first.textContent || '').trim(), href: first.getAttribute('href'), x, y,
           onTop: !!(hit && menu.contains(hit)), hit: hit ? (hit.textContent || '').trim().slice(0, 40) : null };
}
"""


def _menu_open(state: dict) -> bool:
    return bool(state.get("expanded") and state.get("menu"))


def browse_menu(page: Page) -> list[Finding]:
    """With a mouse: (a) hovering Browse opens the menu with its first row on top, (b) a click on Browse
    while hovering keeps it open, (c) a click on the menu's first row opens that row's page."""
    btn = page.locator("header button[aria-haspopup]").first
    if btn.count() == 0:
        return [("warn", "browse-menu", "no Browse menu button in the header, not measured", None)]
    page.mouse.move(4, page.viewport_size["height"] - 4)
    page.wait_for_timeout(200)
    btn.hover()
    page.wait_for_timeout(700)
    a = page.evaluate(MENU_STATE)
    if not _menu_open(a):
        return [("fail", "browse-menu", "hovering Browse did not open the menu", a)]
    out: list[Finding] = []
    if not a.get("onTop"):
        out.append(("fail", "browse-menu", f"the menu's first row \"{a.get('first')}\" is covered: a click there lands on \"{a.get('hit')}\"", a))
    page.mouse.down()
    page.mouse.up()
    page.wait_for_timeout(600)
    b = page.evaluate(MENU_STATE)
    if not _menu_open(b):
        out.append(("fail", "browse-menu", "a click on Browse while hovering it closed the menu", b))
        page.mouse.move(4, page.viewport_size["height"] - 4)
        page.wait_for_timeout(500)
        btn.hover()
        page.wait_for_timeout(700)
        b = page.evaluate(MENU_STATE)
        if not _menu_open(b):
            return out + [("fail", "browse-menu", "the menu did not open again on hover", b)]
    # a real click at the first row's spot, reached the way a slow hand does: straight down from
    # Browse, resting 250 ms over the space between the button and the menu (a gap there let the
    # menu's 160 ms close timer win, and the click landed on the strip under it)
    # (from Browse's centre, so the path is the same in Arabic, where the menu hangs from its right)
    box = btn.bounding_box()
    x = box["x"] + box["width"] / 2 if box else b["x"]
    gap_y = box["y"] + box["height"] + 2 if box else b["y"]
    page.mouse.move(x, gap_y, steps=4)
    page.wait_for_timeout(250)
    page.mouse.move(x, b["y"], steps=4)
    page.wait_for_timeout(150)
    page.mouse.click(x, b["y"])
    try:
        page.wait_for_url(lambda u: urlparse(u).path != "/", timeout=4000)
    except PWError:
        pass
    page.wait_for_timeout(300)
    landed = urlparse(page.url).path
    vals = {"row": b.get("first"), "href": b.get("href"), "landed": landed}
    if landed != b.get("href"):
        out.append(("fail", "browse-menu", f"a click on the menu's first row \"{b.get('first')}\" opened {landed}, not {b.get('href')}", vals))
    if not out:
        out.append(("note", "browse-menu", f"hover opens the menu on top, a click keeps it open, its first row opens {landed}", vals))
    return out


MENU_ROWS = r"""
() => {
  const menu = document.querySelector('header [role="menu"]');
  if (!menu) return null;
  const rows = [...menu.querySelectorAll('[role="menuitem"]')].slice(0, 4).map((r) => {
    const b = r.getBoundingClientRect();
    return { x: b.left + b.width / 2, y: b.top + b.height / 2, left: b.left, right: b.right };
  });
  const title = menu.querySelector('h3');
  const panel = title ? title.parentElement.parentElement.getBoundingClientRect() : null;
  return { rows, title: title ? title.textContent.trim() : null, rtl: document.documentElement.dir === 'rtl',
           panel: panel ? { left: panel.left, right: panel.right, top: panel.top, bottom: panel.bottom } : null };
}
"""


INTENT_PROBE = r"""
() => {
  const btn = document.querySelector('header button[aria-haspopup]');
  window.__qaIntent = { enter: null, open: null };
  btn.parentElement.addEventListener('pointerenter', () => { if (window.__qaIntent.enter == null) window.__qaIntent.enter = performance.now(); });
  new MutationObserver(() => {
    if (btn.getAttribute('aria-expanded') === 'true' && window.__qaIntent.open == null) window.__qaIntent.open = performance.now();
  }).observe(btn, { attributes: true, attributeFilter: ['aria-expanded'] });
}
"""


def menu_intent(page: Page) -> list[Finding]:
    """K1: the menu opens after a short rest on Browse, so a mouse passing over it on its way
    elsewhere never flashes it open; resting on it still opens it. Timed inside the page, from the
    pointer's arrival to the menu opening, so a slow machine cannot skew it."""
    btn = page.locator("header button[aria-haspopup]").first
    if btn.count() == 0:
        return [("warn", "menu-intent", "no Browse menu button in the header, not measured", None)]
    page.mouse.move(4, page.viewport_size["height"] - 4)
    page.wait_for_timeout(300)
    page.evaluate(INTENT_PROBE)
    btn.hover()
    page.wait_for_timeout(700)
    r = page.evaluate("() => window.__qaIntent")
    if r["open"] is None or r["enter"] is None:
        return [("fail", "menu-intent", "resting on Browse did not open the menu", r)]
    delay = round(r["open"] - r["enter"])
    vals = {"open_after_ms": delay}
    if delay < 60:
        return [("fail", "menu-intent", f"the menu opened {delay} ms after the mouse arrived: a pass over Browse flashes it open", vals)]
    return [("note", "menu-intent", f"the menu opens {delay} ms after the mouse rests on Browse", vals)]


def menu_aim(page: Page) -> list[Finding]:
    """K1: a hand moving diagonally from the first category into its panel crosses the next rows
    without switching the panel to them; resting on a row still switches to it."""
    btn = page.locator("header button[aria-haspopup]").first
    if btn.count() == 0:
        return [("warn", "menu-aim", "no Browse menu button in the header, not measured", None)]
    page.mouse.move(4, page.viewport_size["height"] - 4)
    btn.hover()
    page.wait_for_timeout(700)
    m = page.evaluate(MENU_ROWS)
    if not m or len(m["rows"]) < 4 or not m["panel"]:
        return [("warn", "menu-aim", "the menu has fewer than four rows or no panel, not measured", m)]
    r0, r3 = m["rows"][0], m["rows"][3]
    page.mouse.move(r0["x"], r0["y"], steps=6)
    page.wait_for_timeout(400)
    at_first = page.evaluate(MENU_ROWS)
    if not at_first:
        return [("fail", "menu-aim", "the menu closed while the hand moved onto its first row", None)]
    first = at_first["title"]
    # diagonally into the panel, ending level with the fourth row: the second and third rows are crossed
    edge = m["panel"]["right"] - 40 if m["rtl"] else m["panel"]["left"] + 40
    page.mouse.move(edge, r3["y"], steps=12)
    page.wait_for_timeout(350)
    after = (page.evaluate(MENU_ROWS) or {}).get("title")
    out: list[Finding] = []
    vals = {"first": first, "after_diagonal": after}
    if after != first:
        out.append(("fail", "menu-aim", f"heading from \"{first}\" into its panel switched it to \"{after}\" on the way", vals))
    # back on the list, resting on the fourth row: it takes over
    page.mouse.move(r3["x"], r3["y"], steps=6)
    page.wait_for_timeout(450)
    rested = (page.evaluate(MENU_ROWS) or {}).get("title")
    vals["rested_on_fourth"] = rested
    if rested == first:
        out.append(("fail", "menu-aim", "resting on the fourth row did not switch the panel to it", vals))
    return out or [("note", "menu-aim", f"the diagonal keeps \"{first}\"; resting on a row switches to \"{rested}\"", vals)]


# ── K3 cards that respond, K5 press feel (29-Oct-2026) ────────────────────────────────────────────
CARD_STATE = r"""
(code) => {
  const art = document.querySelector('[data-qa-card="' + code + '"]');
  if (!art) return null;
  const ty = new DOMMatrixReadOnly(getComputedStyle(art).transform === 'none' ? undefined : getComputedStyle(art).transform).m42;
  const img = art.querySelector('.m-photo-img');
  const t = img && getComputedStyle(img).transform;
  const scale = t && t !== 'none' ? new DOMMatrixReadOnly(t).a : 1;
  return { lift: Math.round(ty * 10) / 10, scale: Math.round(scale * 1000) / 1000 };
}
"""


def _first_grid_card(page: Page):
    """the first visible grid card with a photo, marked for the probes (data-qa-card)"""
    return page.evaluate(r"""() => {
      const cards = [...document.querySelectorAll('main article[data-stock]')].filter((a) => {
        const r = a.getBoundingClientRect(); return r.width > 150 && r.top > 90 && r.bottom < innerHeight - 20 && a.querySelector('.m-photo-img');
      });
      const a = cards[0]; if (!a) return null;
      a.dataset.qaCard = 'probe';
      const r = a.getBoundingClientRect();
      return { code: 'probe', x: r.left + r.width / 2, y: r.top + r.height * 0.35 };
    }""")


def card_hover(page: Page) -> list[Finding]:
    """K3, a mouse: a card under the pointer lifts (~4 px) and its photo zooms slowly (~1.06)."""
    page.evaluate("() => window.scrollTo(0, Math.round(innerHeight * 0.9))")
    page.wait_for_timeout(1500)
    c = _first_grid_card(page)
    if not c:
        return [("warn", "card-hover", "K3: no grid card on screen, not measured", None)]
    page.mouse.move(c["x"], c["y"], steps=4)
    page.wait_for_timeout(900)
    s = page.evaluate(CARD_STATE, c["code"])
    page.mouse.move(4, 4)
    if not s:
        return [("warn", "card-hover", "K3: the probed card went away, not measured", None)]
    out: list[Finding] = []
    if s["lift"] > -3.5:
        out.append(("fail", "card-hover", f"K3: a hovered card lifts {abs(s['lift'])}px (it should rise about 4 px)", s))
    if s["scale"] < 1.05:
        out.append(("fail", "card-hover", f"K3: a hovered card's photo zooms to {s['scale']} (it should settle near 1.06)", s))
    return out or [("note", "card-hover", f"K3: a hovered card lifts {abs(s['lift'])}px and its photo zooms to {s['scale']}", s)]


PRESS_TRACK = r"""
(ms) => {
  const b = document.querySelector('[data-qa-press]');
  window.__qaPress = [];
  const t0 = performance.now();
  const loop = () => {
    const t = getComputedStyle(b).transform;
    window.__qaPress.push(t && t !== 'none' ? new DOMMatrixReadOnly(t).a : 1);
    if (performance.now() - t0 < ms) requestAnimationFrame(loop);
  };
  requestAnimationFrame(loop);
}
"""


def press_spring(page: Page) -> list[Finding]:
    """K5: a pressed button gives (scales down), and on release springs back past its size for a
    moment before it settles, the way a physical key does."""
    page.evaluate("() => window.scrollTo(0, Math.round(innerHeight * 0.9))")
    page.wait_for_timeout(1500)
    box = page.evaluate(r"""() => {
      const btns = [...document.querySelectorAll('main article[data-stock] button[aria-label]')].filter((b) => {
        const r = b.getBoundingClientRect(); const s = getComputedStyle(b);
        return r.width >= 36 && r.top > 90 && r.bottom < innerHeight - 20 && /^(Add|\+)/.test((b.getAttribute('aria-label') || '') + '') && s.visibility !== 'hidden';
      });
      const b = btns[0]; if (!b) return null;
      b.dataset.qaPress = '1';
      const r = b.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
    }""")
    if not box:
        return [("warn", "press-spring", "K5: no Add button on screen, not measured", None)]
    page.mouse.move(box["x"], box["y"])
    page.wait_for_timeout(300)
    page.mouse.down()
    page.wait_for_timeout(220)
    pressed = page.evaluate("() => { const t = getComputedStyle(document.querySelector('[data-qa-press]')).transform; return t && t !== 'none' ? new DOMMatrixReadOnly(t).a : 1 }")
    page.evaluate(PRESS_TRACK, 700)
    # release without a click landing on the card: move off first, then let go
    page.mouse.move(4, 4)
    page.mouse.up()
    page.wait_for_timeout(800)
    trace = page.evaluate("() => window.__qaPress || []")
    peak = max(trace) if trace else 1
    vals = {"pressed_scale": round(pressed, 3), "release_peak": round(peak, 4), "frames": len(trace)}
    page.keyboard.press("Escape")
    out: list[Finding] = []
    if pressed > 0.985:
        out.append(("fail", "press-spring", f"K5: a pressed Add button barely gives (scale {pressed:.3f})", vals))
    if peak <= 1.0005:
        out.append(("fail", "press-spring", "K5: on release the button eases back without a spring (it never passes its own size)", vals))
    return out or [("note", "press-spring", f"K5: a press gives to {pressed:.3f} and springs back past 1 (peak {peak:.4f})", vals)]


# ── F1 glide scrolling (29-Oct-2026) ───────────────────────────────────────────────────────────
GLIDE_TRACK = r"""
(ms) => {
  window.__qaGlide = [];
  const t0 = performance.now();
  let last = t0;
  const loop = (t) => {
    window.__qaGlide.push([Math.round(t - t0), Math.round(window.scrollY * 10) / 10, Math.round((t - last) * 10) / 10]);
    last = t;
    if (t - t0 < ms) requestAnimationFrame(loop);
  };
  requestAnimationFrame(loop);
}
"""


def glide_scroll(page: Page, notch: int = 100) -> list[Finding]:
    """F1, a mouse: one wheel notch moves the page in a glide, not a jump. It keeps moving for at least
    250 ms before 95% of the notch is covered and comes to rest within 1.5 s; its worst frame is
    reported as a measured number."""
    page.evaluate("() => window.scrollTo(0, 0)")
    page.wait_for_timeout(1800)  # the glide starts after the first paint
    vp = page.viewport_size
    page.mouse.move(vp["width"] * 0.4, vp["height"] * 0.6)
    page.evaluate(GLIDE_TRACK, 2000)
    page.wait_for_timeout(60)
    page.mouse.wheel(0, notch)
    page.wait_for_timeout(2100)
    s = page.evaluate("() => window.__qaGlide || []")
    if len(s) < 10:
        return [("warn", "glide-scroll", "F1: too few frames sampled, not measured", {"frames": len(s)})]
    y0, y1 = s[0][1], s[-1][1]
    dist = y1 - y0
    if dist <= 0:
        return [("fail", "glide-scroll", "F1: one wheel notch did not move the page", {"from": y0, "to": y1})]
    t_first = next(t for t, y, _ in s if y != y0)
    t95 = next(t for t, y, _ in s if y - y0 >= 0.95 * dist)
    t_rest = next(t for t, y, _ in s if y == y1)
    worst = max((d for t, y, d in s if t_first <= t <= t_rest), default=0)
    vals = {"px": dist, "to_95pct_ms": t95 - t_first, "rest_ms": t_rest - t_first, "worst_frame_ms": worst}
    out: list[Finding] = []
    if t95 - t_first < 250:
        out.append(("fail", "glide-scroll", f"F1: the wheel jumped: 95% of the notch in {t95 - t_first} ms (a glide takes 250 ms or more)", vals))
    if t_rest - t_first > 1500:
        out.append(("fail", "glide-scroll", f"F1: the page was still moving {t_rest - t_first} ms after the notch", vals))
    # frame times are the machine's as much as the page's (headless frames of 33-100 ms come with a
    # page standing still), so the worst frame is a measured number, as S1 is: a warning, never fatal
    if worst > 50:
        out.append(("warn", "glide-scroll", f"F1: a frame of the glide took {worst} ms (measured; compare builds on one machine)", vals))
    if not out:
        out.append(("note", "glide-scroll", f"F1: one notch glides {dist:.0f}px: 95% in {t95 - t_first} ms, at rest in {t_rest - t_first} ms, worst frame {worst} ms", vals))
    return out


def glide_layers(page: Page) -> list[Finding]:
    """F1 under a layer: with the search palette open, the wheel moves nothing behind it, and the page
    does not shift sideways (the scroll lock keeps the 10 px Windows scrollbar strip covered)."""
    page.evaluate("() => window.scrollTo(0, 400)")
    page.wait_for_timeout(1800)
    before = page.evaluate("() => ({ y: Math.round(window.scrollY), left: Math.round(document.querySelector('header').getBoundingClientRect().left) })")
    page.keyboard.press("Control+k")
    try:
        page.wait_for_selector('[role="dialog"]', timeout=6000)
    except PWError:
        return [("warn", "glide-layers", "Ctrl+K opened no palette, not measured", None)]
    page.wait_for_timeout(500)
    page.mouse.move(12, page.viewport_size["height"] - 12)
    page.mouse.wheel(0, 300)
    page.wait_for_timeout(1200)
    during = page.evaluate("() => ({ y: Math.round(window.scrollY), left: Math.round(document.querySelector('header').getBoundingClientRect().left) })")
    page.keyboard.press("Escape")
    page.wait_for_timeout(400)
    vals = {"before": before, "with_palette": during}
    out: list[Finding] = []
    if during["y"] != before["y"]:
        out.append(("fail", "glide-layers", f"the wheel moved the page {during['y'] - before['y']}px behind the open palette", vals))
    if during["left"] != before["left"]:
        out.append(("fail", "glide-layers", f"the page shifted {during['left'] - before['left']}px sideways when the palette opened", vals))
    return out or [("note", "glide-layers", "the wheel moves nothing behind the open palette, and nothing shifts", vals)]


def no_glide_on_touch(page: Page) -> list[Finding]:
    """F1 is for a mouse only: a touch screen keeps its native scrolling (no html.lenis)."""
    page.wait_for_timeout(1800)
    if page.evaluate("() => document.documentElement.classList.contains('lenis')"):
        return [("fail", "glide-scroll", "a touch screen got the wheel glide (html.lenis): it must keep native scrolling", None)]
    return [("note", "glide-scroll", "a touch screen keeps native scrolling", None)]


def browse_menu_touch(page: Page) -> list[Finding]:
    """A touch screen with the desktop header (1024x768): a tap on Browse opens the menu, a second tap
    closes it (there is no hover to leave by)."""
    btn = page.locator("header button[aria-haspopup]").first
    if btn.count() == 0:
        return [("warn", "browse-menu", "no Browse menu button in the header, not measured", None)]
    btn.tap()
    page.wait_for_timeout(600)
    first = page.evaluate(MENU_STATE)
    btn.tap()
    page.wait_for_timeout(600)
    second = page.evaluate(MENU_STATE)
    vals = {"after_tap": _menu_open(first), "after_second_tap": _menu_open(second)}
    if _menu_open(first) and not _menu_open(second):
        return [("note", "browse-menu", "a tap opens the menu and a second tap closes it", vals)]
    return [("fail", "browse-menu", "a tap on Browse should open the menu and a second tap close it", vals)]
