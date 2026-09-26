"""
Side-by-side design review. It lays out two review_run.py folders (before / after) as one
self-contained index.html: the 8 key screens × 6 viewports, plus the long home and restock pages at
the lead viewports. Every image is embedded as a WebP data URI no wider than 600 px, so the file
travels on its own (WhatsApp, e-mail, a USB stick).

It also prints the numbers a review asks for, before vs after:
- harness hard failures by check, with the ones a BEFORE build cannot pass labelled "expected";
- tap-target warnings;
- lab Lighthouse, the median of 3;
- bundle sizes against the budgets.

    python scripts/qa/design_review.py --before .../before --after .../after --out .../index.html [--notes notes.html]

Pure: it reads the two folders (meta.json, results.json, full/results.json, lighthouse.json,
bundle.json, shots/) and writes one file.
"""

from __future__ import annotations

import argparse
import base64
import html as htmllib
import io
import json
import sys
from collections import Counter
from pathlib import Path

from PIL import Image

SCREENS = [
    ("home", "Home"),
    ("category", "Category · Cable"),
    ("search_q", "Search · charger"),
    ("product", "Product · UK04-C"),
    ("cart_under", "Restock, under the BHD 20 minimum"),
    ("checkout_met", "Checkout · wholesale order"),
    ("tracking", "Order tracking (fictional QA order)"),
    ("brand", "WEKOME · Coming soon"),
]
VIEWPORTS = ["360x780", "430x932", "768x1024", "1024x768", "1366x768", "1920x1080"]
LONG = [("home", "430x932"), ("home", "1366x768"), ("cart_under", "430x932"), ("cart_under", "1366x768")]
MAX_W = 600
QUALITY = 78

# Hard checks a pre-R4 build fails by construction: the rule itself arrived with R4.
EXPECTED_BEFORE = {
    "soldout-divider": "the ruled “Not in stock now” divider is new in R4",
    "fonts": "R4 swapped Sora for IBM Plex Mono; the harness now checks the new font",
}
TOUCH_FROM_R4 = "1024x768"  # a touch viewport from R4 on, so pre-R4 desktop controls under 44 px fail there


def webp_uri(path: Path, max_screens: float = 4.0) -> str | None:
    if not path.exists():
        return None
    im = Image.open(path).convert("RGB")
    if im.width > MAX_W:
        im = im.resize((MAX_W, round(im.height * MAX_W / im.width)), Image.LANCZOS)
    cap = int(im.width * 2.2 * max_screens)  # a phone full page can be 30k px; the review wants the first screens
    if im.height > cap:
        im = im.crop((0, 0, im.width, cap))
    buf = io.BytesIO()
    im.save(buf, "WEBP", quality=QUALITY, method=4)
    return "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def load(folder: Path) -> dict:
    out: dict = {}
    for k, f in (("meta", "meta.json"), ("results", "results.json"), ("full", "full/results.json"), ("lighthouse", "lighthouse.json"), ("bundle", "bundle.json")):
        p = folder / f
        out[k] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    return out


def esc(s: object) -> str:
    return htmllib.escape(str(s), quote=True)


def num(v, unit="", digits=0):
    if v is None:
        return "—"
    return (f"{v:,.{digits}f}" if isinstance(v, (int, float)) else str(v)) + unit


def fail_rows(res: dict | None) -> list[dict]:
    return list((res or {}).get("hard_failures") or [])


def warn_rows(res: dict | None) -> list[dict]:
    return list((res or {}).get("warnings") or [])


def expected(f: dict, side: str) -> str | None:
    if side != "before":
        return None
    if f.get("check") in EXPECTED_BEFORE:
        return EXPECTED_BEFORE[f["check"]]
    if f.get("check") == "tap-target" and f.get("viewport") == TOUCH_FROM_R4:
        return "1024×768 became a touch viewport in R4 (44 px targets)"
    return None


def harness_table(title: str, rb: dict | None, ra: dict | None) -> str:
    if not rb and not ra:
        return ""
    fb, fa = fail_rows(rb), fail_rows(ra)
    checks = sorted({f.get("check") for f in fb + fa})
    rows = []
    for c in checks:
        b_all = [f for f in fb if f.get("check") == c]
        a_all = [f for f in fa if f.get("check") == c]
        b_exp = [f for f in b_all if expected(f, "before")]
        why = "; ".join(sorted({expected(f, "before") for f in b_exp}))
        rows.append(
            "<tr><th>" + esc(c) + "</th><td>" + str(len(b_all)) + ("" if not b_exp else " <small>(" + str(len(b_exp)) + " expected)</small>") + "</td><td"
            + (' class="bad"' if a_all else "") + ">" + str(len(a_all)) + "</td><td class=\"note\">" + esc(why) + "</td></tr>"
        )
    tb = sum(int((w.get("values") or {}).get("count") or 1) for w in warn_rows(rb) if w.get("check") == "tap-target")
    ta = sum(int((w.get("values") or {}).get("count") or 1) for w in warn_rows(ra) if w.get("check") == "tap-target")
    return (
        "<h3>" + esc(title) + "</h3><table><tr><th></th><th>before</th><th>after</th><th>note</th></tr>"
        + "<tr><th>page runs</th><td>" + num((rb or {}).get("runs")) + "</td><td>" + num((ra or {}).get("runs")) + "</td><td></td></tr>"
        + "<tr><th>hard failures</th><td>" + str(len(fb)) + "</td><td" + (' class="bad"' if fa else ' class="good"') + ">" + str(len(fa)) + "</td><td></td></tr>"
        + "".join(rows)
        + "<tr><th>warnings (all)</th><td>" + str(len(warn_rows(rb))) + "</td><td>" + str(len(warn_rows(ra))) + "</td><td></td></tr>"
        + "<tr><th>controls 36–44 px (touch)</th><td>" + str(tb) + "</td><td>" + str(ta) + "</td><td class=\"note\">warning, not a failure</td></tr>"
        + "</table>"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="before/after design review page")
    ap.add_argument("--before", required=True)
    ap.add_argument("--after", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--notes", default="", help="an HTML fragment (what changed, decisions for the owner) placed under the header")
    ap.add_argument("--title", default="YQ Marketplace · R4 “Stockbook” · before / after")
    args = ap.parse_args()
    before, after = Path(args.before), Path(args.after)
    b, a = load(before), load(after)
    mb, ma = b["meta"] or {}, a["meta"] or {}

    nav, rows, missing = [], [], 0
    for key, label in SCREENS:
        nav.append('<a href="#' + key + '">' + esc(label.split(" · ")[0].split(",")[0]) + "</a>")
        block = ['<section class="screen" id="' + key + '"><h2>' + esc(label) + "</h2>"]
        for vp in VIEWPORTS:
            cells = []
            for side, folder in (("before", before), ("after", after)):
                uri = webp_uri(folder / "shots" / vp / (key + ".png"))
                if uri is None:
                    missing += 1
                    cells.append('<figure class="cell miss"><figcaption>' + side + "</figcaption>no shot</figure>")
                else:
                    cells.append('<figure class="cell"><figcaption>' + side + '</figcaption><img loading="lazy" alt="' + esc(label + ", " + vp + ", " + side) + '" src="' + uri + '"></figure>')
            block.append('<div class="pair"><h3>' + esc(vp.replace("x", " × ")) + (" · touch" if vp in ("360x780", "430x932", "768x1024", "1024x768") else "") + "</h3>" + "".join(cells) + "</div>")
        for lk, lvp in LONG:
            if lk != key:
                continue
            cells = []
            for side, folder in (("before", before), ("after", after)):
                uri = webp_uri(folder / "shots" / lvp / (key + "__full.png"), max_screens=4.0)
                cells.append('<figure class="cell"><figcaption>' + side + "</figcaption>" + ('<img loading="lazy" alt="' + esc(label + " full page " + lvp + " " + side) + '" src="' + uri + '">' if uri else "no shot") + "</figure>")
            block.append('<div class="pair long"><h3>' + esc(lvp.replace("x", " × ")) + " · the whole page (first screens)</h3>" + "".join(cells) + "</div>")
        block.append("</section>")
        rows.append("".join(block))

    def lh_row(form: str) -> str:
        lb = (b["lighthouse"] or {}).get(form) or {}
        la = (a["lighthouse"] or {}).get(form) or {}
        cells = []
        for k, unit, d in (("performance", "", 0), ("accessibility", "", 0), ("best_practices", "", 0), ("lcp_ms", " ms", 0), ("tbt_ms", " ms", 0), ("cls", "", 3), ("si_ms", " ms", 0)):
            cells.append("<td>" + num(lb.get(k), unit, d) + "</td><td>" + num(la.get(k), unit, d) + "</td>")
        return "<tr><th>" + form + "</th>" + "".join(cells) + "</tr>"

    bb, ba = b["bundle"] or {}, a["bundle"] or {}

    def bundle_row(label: str, key: str, budget: int) -> str:
        va = ba.get(key)
        over = isinstance(va, int) and va > budget
        return "<tr><th>" + label + "</th><td>" + num(bb.get(key), " B") + "</td><td" + (' class="bad"' if over else ' class="good"') + ">" + num(va, " B") + "</td><td>" + num(budget, " B") + "</td></tr>"

    notes = Path(args.notes).read_text(encoding="utf-8") if args.notes and Path(args.notes).exists() else ""
    sub = (
        "Before = <b>" + esc(mb.get("commit", "?")) + "</b> (what is live today). After = <b>" + esc(ma.get("commit", "?")) + "</b> (the R4 branch). "
        "Both builds ran on this PC as local previews, reading the same recorded copy of the live catalog through a read-only stand-in for the API, so nothing was written to production. "
        "Screens were taken with reduced motion so both sides show the same frame. "
        "<b>Preview data:</b> the WEKOME “Coming soon” cards are the 34 models from the pending review sheet (not published), and the tracking screen is a fictional QA order."
    )
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>R4 before and after</title>
<style>
:root{{--paper:#F6F4EF;--sheet:#fff;--ink:#1B1522;--ink2:#4B4455;--ink3:#6E6776;--rule:#C9C1B4;--line:#E2DDD4;--plum:#6D4091;--ok:#1D6F47;--bad:#A3273C}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}}
header{{padding:24px 16px 8px;max-width:1400px;margin:0 auto}}
main{{padding:0 16px 48px;max-width:1400px;margin:0 auto}}
h1{{font-size:24px;line-height:1.2;margin:0 0 6px}}
h2{{font-size:20px;margin:36px 0 10px;padding-bottom:6px;border-bottom:1px solid var(--rule)}}
h3{{font-size:14px;margin:0 0 6px;color:var(--ink2);font-weight:600}}
.sub{{color:var(--ink2);margin:0 0 12px;max-width:80ch}}
nav{{position:sticky;top:0;z-index:2;background:var(--paper);border-bottom:1px solid var(--rule);padding:8px 16px;display:flex;gap:6px;flex-wrap:wrap}}
nav a{{color:var(--plum);text-decoration:none;font-weight:600;font-size:13px;padding:6px 10px;border:1px solid var(--line);border-radius:8px;background:var(--sheet)}}
table{{border-collapse:collapse;font-variant-numeric:tabular-nums;margin:0 0 12px;background:var(--sheet)}}
th,td{{border-bottom:1px solid var(--line);padding:6px 10px;text-align:right;vertical-align:top}} th{{text-align:left;font-weight:600}}
td.bad{{color:var(--bad);font-weight:700}} td.good{{color:var(--ok);font-weight:700}} .note{{text-align:left;color:var(--ink3);font-size:13px}}
.scroll{{overflow-x:auto}}
.pair{{display:grid;grid-template-columns:1fr 1fr;gap:10px;align-items:start;padding:12px 0;border-bottom:1px solid var(--line)}}
.pair h3{{grid-column:1/-1}}
.cell{{margin:0;background:var(--sheet);border:1px solid var(--line);border-radius:8px;overflow:hidden}}
.cell figcaption{{font-size:12px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--ink3);padding:6px 8px;border-bottom:1px solid var(--line)}}
.cell img{{display:block;width:100%;height:auto}} .miss{{padding:0 0 20px;color:var(--ink3);font-size:13px}}
.notes{{background:var(--sheet);border:1px solid var(--line);border-radius:10px;padding:12px 16px;margin:12px 0}}
small{{color:var(--ink3)}}
</style></head><body>
<header>
<h1>{esc(args.title)}</h1>
<p class="sub">{sub}</p>
{('<div class="notes">' + notes + '</div>') if notes else ''}
</header>
<nav aria-label="Screens">{''.join(nav)}<a href="#numbers">Numbers</a></nav>
<main>
{''.join(rows)}
<section id="numbers">
<h2>Numbers</h2>
<div class="scroll">{harness_table("QA harness · the 8 review screens (reduced motion)", b["results"], a["results"])}</div>
<div class="scroll">{harness_table("QA harness · full pass/fail run (every state, motion on)", b["full"], a["full"])}</div>
<h3>Lighthouse · lab, local preview, median of 3 runs (compare the change, not the absolute numbers)</h3>
<div class="scroll"><table><tr><th>form</th><th>perf b</th><th>perf a</th><th>a11y b</th><th>a11y a</th><th>bp b</th><th>bp a</th><th>LCP b</th><th>LCP a</th><th>TBT b</th><th>TBT a</th><th>CLS b</th><th>CLS a</th><th>SI b</th><th>SI a</th></tr>
{lh_row('mobile')}
{lh_row('desktop')}
</table></div>
<h3>Bundle · first load, gzip (fonts as served)</h3>
<div class="scroll"><table><tr><th></th><th>before</th><th>after</th><th>budget</th></tr>
{bundle_row('initial script', 'script_gz', 184320)}
{bundle_row('stylesheet', 'stylesheet_gz', 35840)}
{bundle_row('fonts (all files)', 'font_bytes', 97280)}
<tr><th>fonts preloaded</th><td>{num(bb.get('font_preloaded_bytes'), ' B')}</td><td>{num(ba.get('font_preloaded_bytes'), ' B')}</td><td></td></tr>
</table></div>
</section>
</main></body></html>"""
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    fb, fa = fail_rows(b["results"]), fail_rows(a["results"])
    print("wrote " + str(out) + " (" + str(out.stat().st_size // 1024) + " KB, " + str(missing) + " missing shots)")
    print(json.dumps({"before_fails": dict(Counter(f.get("check") for f in fb)), "after_fails": dict(Counter(f.get("check") for f in fa)), "after_full_fails": dict(Counter(f.get("check") for f in fail_rows(a["full"])))}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
