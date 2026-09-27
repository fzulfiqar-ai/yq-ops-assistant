"""R7b · R5 "Arabic" — the merchant marketplace in Arabic, with an EN / عربي switch.

    python -m tests.test_r7b_arabic

Same lightweight runner as tests/test_r1_soldout.py (no pytest). Everything here is pure: the
source files are read as text, and the modules themselves run in plain node through
web/scripts/arabic_test.mjs — that one SKIPS (prints, never fails) when node or web/node_modules is
not there; the CI web job should run it for real, beside soldout_order_test.mjs. No database, no
network, no build; a built web/dist-market is measured only when one is present.

Covered:
  * the i18n module — en.ts / ar.ts / index.ts, strings.ts only re-exports, `ar` typed `Strings`,
    no `as const` on the English (a translation is a different string of the same shape), the
    native-review flag on the Arabic;
  * the language rule and the boot script — ?lang → the saved choice → the phone, an external file
    (the CSP forbids inline scripts) that sets <html lang dir> before render, in the market HTML only,
    catalog-prefetch.js untouched;
  * money and digits — Intl with numberingSystem 'latn', 3 decimals, «د.ب» after the amount, no
    Arabic-Indic digit anywhere in the Arabic copy;
  * the fonts — IBM Plex Sans Arabic 400/600 woff2 (OFL, licence beside them), @font-face with an
    Arabic unicode-range, named only in the Arabic font stack, never precached for English phones;
  * RTL — the FloatingNav pill, the phone PromoSlider, the Rail arrows and the Lightbox keys follow
    the writing direction; unflipped arrows are gone; English runs are isolated (<bdi dir="ltr">);
  * the switch — in the desktop header, the phone's brand row and My YQ (the old "AR · Soon" is gone);
  * what the rep reads stays readable — WhatsApp texts to the rep are Arabic + the English line, the
    delivery choice goes into the order note in English, the area chip sends the English name;
  * quick paste and search in Arabic (in node), and the owner's wording rules in Arabic;
  * the QA harness (--lang ar, --widths) and the bundle gate (the Arabic faces on their own budget).
"""
from __future__ import annotations

import io
import re
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
MARKET = WEB / "src" / "market"

TESTS: list[tuple[str, object]] = []


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _m(rel: str) -> str:
    return (MARKET / rel).read_text(encoding="utf-8")


# ── the i18n module ────────────────────────────────────────────────────────────────────────────

@test("i18n: en.ts / ar.ts / index.ts; strings.ts only re-exports; ar is typed Strings; no `as const`")
def _():
    for f in ("i18n/en.ts", "i18n/ar.ts", "i18n/index.ts"):
        assert (MARKET / f).exists(), f + " is missing"
    strings = _m("strings.ts")
    code = [ln for ln in strings.splitlines() if ln.strip() and not ln.strip().startswith(("/*", "*", "//"))]
    assert code == ["export { locale, plural, S } from './i18n'"], code
    en = _m("i18n/en.ts")
    assert "export const en = {" in en and "export type Strings = typeof en" in en
    assert "as const\n" not in en.split("export const en = {", 1)[1][-40:], "the English object must not be `as const`"
    assert not re.search(r"^\}\s*as const", en, re.M)
    ar = _m("i18n/ar.ts")
    assert "export const ar: Strings = {" in ar, "ar must be declared Strings, so tsc fails on a missing key"
    assert "NATIVE REVIEW NEEDED" in ar.split("export const ar", 1)[0], "the Arabic file must carry the native-review flag"
    idx = _m("i18n/index.ts")
    assert "export const S: Strings = LANG === 'ar' ? ar : en" in idx
    assert "export const locale" in idx and "dir: LANG === 'ar' ? 'rtl' : 'ltr'" in idx
    # every component still imports S / locale from strings.ts — no call site imports a language
    for f in MARKET.rglob("*.tsx"):
        src = f.read_text(encoding="utf-8")
        assert "from '../i18n/ar'" not in src and "from './i18n/ar'" not in src, f"{f.name} imports the Arabic file directly"


@test("language rule: ?lang → saved yq-lang → the phone's first language; switchLang saves and reloads without a stale ?lang")
def _():
    idx = _m("i18n/index.ts")
    assert "export const LANG_KEY = 'yq-lang'" in idx
    assert "return fromUrl || norm(saved) || (norm(languages[0]) === 'ar' ? 'ar' : 'en')" in idx
    assert "document.documentElement.getAttribute('data-lang')" in idx, "the app reads the boot script's decision"
    assert "url.searchParams.delete('lang')" in idx and "window.location.replace(url.toString())" in idx
    assert "if (!saved) url.searchParams.set('lang', next)" in idx, "blocked storage keeps the choice in the URL"


@test("boot script: public/market-lang.js sets <html lang dir data-lang> before render — external (CSP), market build only")
def _():
    boot = _read("web/public/market-lang.js")
    for want in ("var KEY = 'yq-lang'", "root.setAttribute('lang', lang)", "root.setAttribute('dir', lang === 'ar' ? 'rtl' : 'ltr')",
                 "root.setAttribute('data-lang', lang)", "if (fromUrl) localStorage.setItem(KEY, fromUrl)",
                 "/fonts/ibm-plex-sans-arabic-400-v1.woff2", "/fonts/ibm-plex-sans-arabic-600-v1.woff2", "if (lang !== 'ar') return"):
        assert want in boot, "market-lang.js lost: " + want
    vite = _read("web/vite.config.ts")
    assert """.replace('<script src="/catalog-prefetch.js"', '<script src="/market-lang.js"></script>\\n    <script src="/catalog-prefetch.js"')""" in vite
    # inside marketHtml(): the portal never gets it
    market_html = vite[vite.index("function marketHtml()"):vite.index("function versionJson(")]
    assert "market-lang.js" in market_html
    assert "<script>" not in vite.replace("<script src=", ""), "no inline script (CSP script-src 'self')"
    assert "script-src 'self' https://static.cloudflareinsights.com;" in _read("web/public/_headers"), "the CSP is unchanged"
    # MarketApp still mirrors locale onto <html> (the boot script already did it; this keeps them equal)
    app = _read("web/src/MarketApp.tsx")
    assert "document.documentElement.lang = locale.lang" in app and "document.documentElement.dir = locale.dir" in app


@test("money and digits: Intl with numberingSystem 'latn', 3 decimals, «د.ب» after the amount; no Arabic-Indic digit in the Arabic copy")
def _():
    idx = _m("i18n/index.ts")
    assert "numberingSystem: 'latn'" in idx and "minimumFractionDigits: 3, maximumFractionDigits: 3, useGrouping: false" in idx
    assert "ar: 'ar-BH-u-nu-latn'" in idx
    assert "return lang === 'ar' ? `${amount} د.ب` : `BHD ${amount}`" in idx
    fmt = _m("lib/format.ts")
    assert "return moneyText(Number(n || 0))" in fmt and "return withCurrency(money(n))" in fmt
    assert "toLocaleDateString(DATE_LOCALE" in fmt and "'en-GB'" not in fmt, "dates follow the page language"
    ar = _m("i18n/ar.ts")
    assert not re.search(r"[٠-٩۰-۹]", ar), "Arabic-Indic digits in ar.ts (the rule is Western digits)"


@test("wording: «نفدت الكمية» for sold out; the tier word nowhere; rep-bound WhatsApp texts carry the English line")
def _():
    ar = _m("i18n/ar.ts")
    for key in ("soldOut", "stockOut", "stockOutAr"):
        assert f"    {key}: 'نفدت الكمية'," in ar, key
    for bad in ("غير متوفر في المخزون", "نفد من المخزون", "بريميوم", "أرقى", "فاخر", "premium"):
        assert bad not in ar, "ar.ts carries " + bad
    assert "upcoming: en.upcoming," in ar, "the WEKOME copy is one bilingual block (en.ts), shared"
    # a message the merchant sends TO the representative: Arabic first, then the English line
    for key, en_call in (("tellBackText", "en.card.tellBackText(first, code, name)"), ("askHave", "en.shop.askHave(first, q)"),
                         ("askMessage", "en.cart.askMessage(first)"), ("aboutOrder", "en.track.aboutOrder(first, no)")):
        line = next((ln for ln in ar.splitlines() if ln.strip().startswith(key + ":")), "")
        assert "bi(" in line and en_call in line, key + " must be bilingual for the rep: " + line.strip()[:120]


@test("fonts: IBM Plex Sans Arabic 400/600 woff2 + OFL; @font-face with an Arabic unicode-range; only in the Arabic stack; not precached")
def _():
    fonts = WEB / "public" / "fonts"
    for w in ("400", "600"):
        f = fonts / f"ibm-plex-sans-arabic-{w}-v1.woff2"
        assert f.exists() and f.read_bytes()[:4] == b"wOF2", f.name + " is not a woff2"
        assert 20_000 < f.stat().st_size < 60_000, f.name + " is not the Arabic subset"
    lic = (fonts / "OFL-ibm-plex-sans-arabic.txt").read_text(encoding="utf-8")
    assert "SIL Open Font License" in lic and "IBM Corp" in lic
    css = _m("market.css")
    faces = re.findall(r"@font-face \{[^}]*font-family: 'IBM Plex Sans Arabic';[^}]*\}", css)
    assert len(faces) == 2, faces
    for face in faces:
        assert "unicode-range: U+0600-06FF" in face and "U+0000-00FF" not in face and "font-display: swap" in face
    # the family is named in the Arabic stack only: the default :root stack never mentions it
    root = css[css.index("@layer base {"):css.index("color-scheme: only light;")]
    assert "IBM Plex Sans Arabic" not in root, "an English page would download the Arabic face"
    ar_block = css[css.index(":root[lang='ar'],"):]
    assert "--m-font-sans: 'Instrument Sans', 'IBM Plex Sans Arabic'" in ar_block
    assert ":root[lang='ar'] * {\n  letter-spacing: normal;" in ar_block, "no tracking on a joined script"
    vite = _read("web/vite.config.ts")
    assert "'fonts/ibm-plex-sans-arabic-*.woff2'" in vite, "the Arabic faces must stay out of the precache"
    assert "cacheName: 'yq-market-fonts'" in vite


@test("RTL: the nav pill, the phone slider, the rail arrows and the Lightbox keys follow the direction; arrows mirror")
def _():
    nav = _m("shell/FloatingNav.tsx")
    assert "translateX(calc(var(--i) * 100% * var(--m-dir, 1)))" in nav
    css = _m("market.css")
    assert ":root[dir='rtl'] {\n  --m-dir: -1;" in css
    slider = _m("components/PromoSlider.tsx")
    assert "const left = rtl ? Math.min(0, Math.max(-max, want)) : Math.max(0, Math.min(max, want))" in slider
    assert "(e.key === 'ArrowRight') !== (document.documentElement.dir === 'rtl')" in slider
    rail = _m("components/Rail.tsx")
    assert "el.scrollBy({ left: dir * (rtl ? -1 : 1) * el.clientWidth * 0.8" in rail
    lb = _m("components/Lightbox.tsx")
    assert "const forward = (e.key === 'ArrowRight') !== (document.documentElement.dir === 'rtl')" in lb
    # the side drawer (ui/Sheet) sits at the inline end: it slides from the left edge in Arabic
    assert css.count("transform: translate3d(calc(100% * var(--m-dir, 1)), 0, 0);") == 2, "m-drawer-in / m-drawer-out follow the direction"
    # the slide sash and scrim were already mirrored (market.css) — keep them
    assert "[dir='rtl'] .slide-sash,\n[dir='rtl'] .slide-scrim {\n  transform: scaleX(-1);" in css
    # every direction glyph mirrors under rtl (lucide chevrons / arrows)
    for f in MARKET.rglob("*.tsx"):
        for n, ln in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"<(ArrowRight|ArrowLeft|ChevronRight|ChevronLeft)\b", ln):
                assert "rtl:" in ln, f"{f.relative_to(ROOT)}:{n} an arrow that does not mirror: {ln.strip()[:100]}"
            if "group-hover:translate-x-0.5" in ln and "rtl:-scale-x-100" in ln:
                assert "rtl:group-hover:-translate-x-0.5" in ln, f"{f.relative_to(ROOT)}:{n} the hover nudge goes the wrong way in Arabic"


@test("bidi: English runs are islands — <bdi dir='ltr'> (ui/Ltr) / ltr() / dir='ltr' names; no raw control characters in source")
def _():
    ltr = _m("ui/Ltr.tsx")
    assert '<bdi dir="ltr">{children}</bdi>' in ltr and "if (locale.dir !== 'rtl') return <>{children}</>" in ltr
    idx = _m("i18n/index.ts")
    assert "export const ltrText: { dir?: 'ltr' } = LANG === 'ar' ? { dir: 'ltr' } : {}" in idx
    assert "return LANG === 'ar' ? `\\u2066${s}\\u2069` : String(s)" in idx
    track = _m("pages/TrackingPage.tsx")
    assert "<Ltr>{placed.order_no}</Ltr>" in track and "<Ltr>{data.order_no}</Ltr>" in track
    assert "statusLabel(data.status, data.status_label)" in track and "statusLabel(s.status, s.label)" in track
    card = _m("components/MarketCard.tsx")
    assert "<span {...ltrText} className=\"line-clamp-2 rtl:text-right\">" in card, "the card name reads LTR, cut at its end"
    assert "<span dir=\"ltr\" className={cn('flex min-w-0 items-baseline gap-1" in card, "the kicker VFAN · code stays one run"
    assert "proofText(item.social_proof)" in card
    checkout = _m("pages/CheckoutPage.tsx")
    assert 'id="yq-phone" type="tel" inputMode="tel" autoComplete="tel" dir="ltr"' in checkout
    # invisible controls are written as escapes, never pasted into source
    bad = re.compile("[\u200e\u200f\u202a-\u202e\u2066-\u2069]")
    for f in list(MARKET.rglob("*.ts")) + list(MARKET.rglob("*.tsx")) + [WEB / "public" / "market-lang.js", WEB / "scripts" / "arabic_test.mjs"]:
        assert not bad.search(f.read_text(encoding="utf-8")), f"{f.relative_to(ROOT)} carries a raw bidi control character"


@test("what the rep reads stays English: the delivery choice in the note, the area value, statuses from the key")
def _():
    checkout = _m("pages/CheckoutPage.tsx")
    assert "import { en } from '../i18n/en'" in checkout
    assert "`${en.checkout.deliveryLabel}: ${en.checkout.deliveryOptions[deliveryPref] ?? S.checkout.deliveryOptions[deliveryPref]}`" in checkout
    assert "{areaLabel(a)}" in checkout and "set('area', a)" in checkout, "the chip shows the label, the order keeps the English area"
    fmt = _m("lib/format.ts")
    assert "if (locale.lang === 'en') return apiLabel || S.status[key] || key" in fmt, "English shows the API's own words"
    en = _m("i18n/en.ts")
    status = re.search(r"status: \{(.*?)\} as Record<string, string>", en).group(1)
    shop = _read("app/shop.py")
    api = re.search(r"STATUS_LABELS = \{(.*?)\}", shop, re.S).group(1)
    for key, label in re.findall(r'"(\w+)": "([^"]+)"', api):
        assert f"{key}: '{label}'" in status, f"en.status.{key} drifted from app/shop.py STATUS_LABELS ({label})"
    assert 'return f"Ordered by {n} shops in the last 30 days"' in shop and "^Ordered by (\\d+) shops in the last 30 days$" in fmt, \
        "proofText() rebuilds the API's social-proof sentence; keep the two in step"


@test("the switch: EN / عربي in the desktop header's utility bar, the phone's brand row and My YQ; each name in its own script and lang")
def _():
    sw = _m("components/LangSwitch.tsx")
    assert "lang={l}" in sw and "lang={other}" in sw and "switchLang(next)" in sw
    assert "track('rail_click', { meta: { rail: 'lang'" in sw
    # desktop: the header's utility bar (the header row is budgeted to the pixel at 1024), always rendered
    bar = _m("components/PromiseBar.tsx")
    assert '<LangSwitch where="bar" tone="bar" />' in bar and "if (!promises.length && !installable) return null" not in bar
    assert "LangSwitch" not in _m("shell/StickyHeader.tsx"), "one switch on desktop, in the utility bar"
    ph = _m("shell/PhoneHeader.tsx")
    assert '<LangSwitch where="band" tone="plum" />' in ph and '<LangSwitch where="phone" />' in ph
    me = _m("pages/MyYQPage.tsx")
    assert '<LangSwitch variant="segmented" where="me" />' in me
    assert "title={S.me.soon}" not in me, "the old 'AR · Soon' placeholder is back"
    assert "S.me.install.split(' ')[0]" not in me, "a word cut out of a sentence does not translate"
    en, ar = _m("i18n/en.ts"), _m("i18n/ar.ts")
    assert "lang: { label: 'Language', english: 'English', arabic: 'عربي' }" in en
    assert "lang: { label: 'اللغة', english: 'English', arabic: 'عربي' }" in ar


@test("quick paste + search in Arabic: the parser and the index read Arabic digits, «،» «؛», حبة / قطعة / درزن and Arabic words")
def _():
    qp = _m("lib/quickParse.ts")
    assert ".split(/[\\n,;\\u060c\\u061b]+/)" in qp
    assert "حب(?:ة|ه|ات)|قطع(?:ة|ه)?" in qp and "درزن|دزن|دزين(?:ة|ه)" in qp and "unit && DOZEN_RE.test(unit) ? 12 : 1" in qp
    assert "const q = queryText(query)" in qp and "const asked = queryText(query)" in qp
    search = _m("lib/search.ts")
    assert "export function queryText(q: string): string" in search and "export function westernDigits(s: string): string" in search
    assert "const query = queryText(q).trim()" in search
    fmt = _m("lib/format.ts")
    assert ".replace(/[\\u0660-\\u0669\\u06f0-\\u06f9]/g" in fmt.split("export function codeKey", 1)[1][:400], "codeKey reads Arabic digits"


@test("QA harness: --lang ar walks the same states in Arabic (STR_AR mirrors STR), --widths 390,1366; English runs never load the Arabic font")
def _():
    qa = _read("scripts/qa/market_qa.py")
    assert 'ap.add_argument("--lang", choices=("en", "ar"), default="en"' in qa and 'ap.add_argument("--widths"' in qa
    assert 'locale="ar-BH" if LANG == "ar" else "en-GB"' in qa and "localStorage.setItem('yq-lang', " in qa
    str_keys = set(re.findall(r'^    "([\w.]+)": ', qa.split("STR = {", 1)[1].split("}\n", 1)[0], re.M))
    ar_keys = set(re.findall(r'^    "([\w.]+)": ', qa.split("STR_AR = {", 1)[1].split("}\n", 1)[0], re.M))
    missing = str_keys - ar_keys - {"avail.few", "head.list"}
    assert not missing, "STR_AR lacks " + ", ".join(sorted(missing))
    ar = _m("i18n/ar.ts") + _m("i18n/en.ts")
    body = qa.split("STR_AR = {", 1)[1].split("}\n", 1)[0]
    for key, value in re.findall(r'^    "([\w.]+)": "([^"]+)",', body, re.M):
        if key in ("slides.dotPrefix", "area.manama"):
            continue
        assert value in ar, f"STR_AR[{key}] = {value!r} is not in the locale files"
    assert re.search(r'^BANNED_AR = r"', qa, re.M)
    assert '"an English page downloaded the Arabic font: "' in qa and '"IBM Plex Sans Arabic did not load on an Arabic page (document.fonts)"' in qa
    assert "def locale_file(repo: Path, lang: str) -> Path:" in qa, "the copy checks read i18n/<lang>.ts, not strings.ts"


@test("bundle gate: the Arabic faces are budgeted on their own; the English page keeps the lighthouserc font budget")
def _():
    bs = _read("scripts/qa/bundle_sizes.py")
    assert "FONT_ARABIC_BUDGET = 92160" in bs and 'ARABIC_FONT = re.compile(r"^ibm-plex-sans-arabic-")' in bs
    assert '"font_arabic": font_arabic <= FONT_ARABIC_BUDGET,' in bs
    dist = WEB / "dist-market"
    if not (dist / "index.html").exists():
        print("        SKIP: no web/dist-market build — the gate itself was not run")
        return
    sys.path.insert(0, str(ROOT / "scripts" / "qa"))
    import bundle_sizes  # noqa: PLC0415
    m = bundle_sizes.measure(dist)
    assert m["gate"]["font"] and m["gate"]["font_arabic"], m["gate"]
    assert set(m["arabic_fonts"]) == {"ibm-plex-sans-arabic-400-v1.woff2", "ibm-plex-sans-arabic-600-v1.woff2"}, m["arabic_fonts"]
    html = (dist / "index.html").read_text(encoding="utf-8")
    assert html.index('<script src="/market-lang.js"></script>') < html.index('<script src="/catalog-prefetch.js"'), "the language is set before the prefetch runs"
    assert (dist / "market-lang.js").exists()


# ── the modules, in plain node ─────────────────────────────────────────────────

@test("node: key parity, Arabic values, the language rule, money, isolation, quick paste and search (web/scripts/arabic_test.mjs)")
def _():
    node = shutil.which("node")
    script = WEB / "scripts" / "arabic_test.mjs"
    if not node or not (WEB / "node_modules" / "typescript").exists() or not (WEB / "node_modules" / "minisearch").exists():
        print("        SKIP: node or web/node_modules not available (the CI web job runs it)")
        return
    r = subprocess.run([node, str(script)], cwd=str(WEB), capture_output=True, text=True, encoding="utf-8", timeout=240)
    out = (r.stdout or "") + (r.stderr or "")
    for line in out.splitlines():
        if "FAIL" in line or line.strip().startswith(("passed", "PASS")) is False and "passed," in line:
            print("        " + line)
    assert r.returncode == 0, "node Arabic tests failed:\n" + out[-2000:]


def main() -> int:
    passed = failed = 0
    for name, fn in TESTS:
        try:
            fn()
            print(f"  PASS  {name}")
            passed += 1
        except Exception:  # noqa: BLE001
            print(f"  FAIL  {name}")
            traceback.print_exc(limit=2)
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
