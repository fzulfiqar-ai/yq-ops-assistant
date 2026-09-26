"""
Bundle sizes of a market build, the way .github/lighthouserc.json budgets them.

    python scripts/qa/bundle_sizes.py web/dist-market [--json] [--gate]

Reads the built index.html, takes every script and stylesheet it references on the first load
(the entry, its modulepreloads and its CSS — not the lazy route chunks) and prints their gzip
sizes, plus the bytes of the woff2 files the MARKET loads (fonts are served as-is; woff2 is already
compressed). Budgets: script <= 184320 B, stylesheet <= 35840 B, font <= 97280 B.

The home routes (/ and /{slug}) also fetch the essentials chunk up front — catalog-prefetch.js
modulepreloads what the build lists on its tag as data-home — so that figure is reported beside
the initial script as `home_script_gz` (the initial script plus those chunks).

The fonts figure is the market's own fonts — every woff2 under fonts/ that a file of the build
names: a stylesheet's @font-face url, the HTML's preloads, a script (a lazy FontFace, the service
worker's precache list, catalog-prefetch.js). That is the most any market session can load, the
Arabic faces included once they ship. The build copies the portal's fonts too (web/public is shared:
inter, space-grotesk), but nothing in the market names them, so they are listed as "not loaded by
the market" and never counted (review F77). `font_refs` is the proof: for each counted file, the
built files that name it. Pure: no network, no build.

--gate (CI, .github/workflows/ci.yml) exits 1 when a budget is broken. The budgets are READ from
.github/lighthouserc.json (resource-summary:script|stylesheet|font:size), so the two cannot drift,
and the gate measures what the market really loads: the scripts the home routes fetch up front
(home_script_gz: the entry, its modulepreloads and the essentials chunks), the stylesheet, and the
woff2 files the market references (its CSS @font-face urls, the HTML and the prefetch) — not the
portal's fonts that share the public/ folder.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sys
from pathlib import Path

BUDGET = {"script": 184320, "stylesheet": 35840, "font": 97280}
LHR = Path(__file__).resolve().parents[2] / ".github" / "lighthouserc.json"


def budgets() -> dict:
    """The Lighthouse budgets, from .github/lighthouserc.json (BUDGET is only the fallback)."""
    out = dict(BUDGET)
    try:
        rules = json.loads(LHR.read_text(encoding="utf-8"))["ci"]["assert"]["assertions"]
        for kind in out:
            rule = rules.get("resource-summary:" + kind + ":size")
            if rule and isinstance(rule[1], dict) and "maxNumericValue" in rule[1]:
                out[kind] = int(rule[1]["maxNumericValue"])
    except (OSError, KeyError, ValueError, IndexError, TypeError):
        pass
    return out


def gz_size(path: Path) -> int:
    return len(gzip.compress(path.read_bytes(), 9))


# the built files that can make the market fetch a font: stylesheets, the HTML, scripts (the entry,
# lazy chunks, the service worker's precache list, catalog-prefetch.js) and the manifest
TEXT_EXT = {".html", ".css", ".js", ".mjs", ".webmanifest", ".json"}


def font_references(dist: Path, fonts: dict[str, int]) -> dict[str, list[str]]:
    """{woff2 file: [built files that name it]} — every text file of the build, fonts/ itself excluded."""
    refs: dict[str, list[str]] = {f: [] for f in fonts}
    for path in sorted(dist.rglob("*")):
        if not path.is_file() or path.suffix not in TEXT_EXT or "fonts" in path.relative_to(dist).parts[:-1]:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for f in fonts:
            if "fonts/" + f in text:
                refs[f].append(path.relative_to(dist).as_posix())
    return refs


def measure(dist: Path) -> dict:
    html = (dist / "index.html").read_text(encoding="utf-8")
    refs = re.findall(r'(?:src|href)="/(assets/[^"]+\.(?:js|css))"', html)
    seen: list[str] = []
    for r in refs:
        if r not in seen:
            seen.append(r)
    scripts = {p: gz_size(dist / p) for p in seen if p.endswith(".js")}
    home = re.search(r'<script src="/catalog-prefetch\.js"[^>]*\sdata-home="([^"]*)"', html)
    home_chunks = {f.lstrip("/"): gz_size(dist / f.lstrip("/")) for f in (home.group(1).split() if home else [])}
    styles = {p: gz_size(dist / p) for p in seen if p.endswith(".css")}
    fonts_dir = dist / "fonts"
    fonts = {f.name: f.stat().st_size for f in sorted(fonts_dir.glob("*.woff2"))} if fonts_dir.exists() else {}
    preloaded = re.findall(r'<link rel="preload" as="font"[^>]*href="/fonts/([^"]+)"', html)
    # the woff2 the market itself can ask for — named by one of its own built files (see the docstring)
    refs = font_references(dist, fonts)
    market_fonts = sorted(f for f, where in refs.items() if where)
    unused = sorted(f for f, where in refs.items() if not where)
    font_market = sum(fonts[f] for f in market_fonts)
    budget = budgets()
    home_gz = sum(scripts.values()) + sum(home_chunks.values())
    return {
        "dist": str(dist),
        "script_gz": sum(scripts.values()),
        "home_script_gz": sum(scripts.values()) + sum(home_chunks.values()),
        "home_chunks": home_chunks,
        "stylesheet_gz": sum(styles.values()),
        # the market's fonts only (review F77); the whole folder is font_folder_bytes
        "font_bytes": font_market,
        "font_folder_bytes": sum(fonts.values()),
        "font_preloaded_bytes": sum(fonts.get(f, 0) for f in preloaded),
        "font_market_bytes": font_market,
        "market_fonts": market_fonts,
        "font_refs": {f: refs[f] for f in market_fonts},
        "fonts_not_loaded": {f: fonts[f] for f in unused},
        "scripts": scripts,
        "stylesheets": styles,
        "fonts": {f: fonts[f] for f in market_fonts},
        "preloaded_fonts": preloaded,
        "budget": BUDGET,
        "within": {
            "script": sum(scripts.values()) <= BUDGET["script"],
            "stylesheet": sum(styles.values()) <= BUDGET["stylesheet"],
            "font": font_market <= BUDGET["font"],
        },
        # what --gate enforces, against the lighthouserc budgets
        "gate_budget": budget,
        "gate": {
            "script": home_gz <= budget["script"],
            "stylesheet": sum(styles.values()) <= budget["stylesheet"],
            "font": font_market <= budget["font"],
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="market bundle sizes vs the Lighthouse budgets")
    ap.add_argument("dist", help="a built market folder (web/dist-market)")
    ap.add_argument("--json", action="store_true", help="print JSON only")
    ap.add_argument("--gate", action="store_true", help="exit 1 when the home scripts, the stylesheet or the market's fonts break a lighthouserc budget")
    args = ap.parse_args()
    dist = Path(args.dist)
    if not (dist / "index.html").exists():
        print("no index.html in " + str(dist), file=sys.stderr)
        return 2
    m = measure(dist)
    if args.gate:
        b = m["gate_budget"]
        rows = (("home scripts gz", m["home_script_gz"], "script"), ("stylesheet gz", m["stylesheet_gz"], "stylesheet"), ("market fonts", m["font_market_bytes"], "font"))
        for label, value, kind in rows:
            print(("ok    " if m["gate"][kind] else "OVER  ") + label.ljust(16) + str(value).rjust(8) + " B  (budget " + str(b[kind]) + ")")
        print("market fonts: " + ", ".join(m["market_fonts"]))
        return 0 if all(m["gate"].values()) else 1
    if args.json:
        print(json.dumps(m, indent=1))
        return 0
    print("initial script gz  " + str(m["script_gz"]).rjust(7) + " B  (budget " + str(BUDGET["script"]) + ")")
    for p, n in m["scripts"].items():
        print("    " + str(n).rjust(7) + "  " + p)
    if m["home_chunks"]:
        print("home routes add     " + str(m["home_script_gz"] - m["script_gz"]).rjust(7) + " B  (the essentials chunk, modulepreloaded: " + str(m["home_script_gz"]) + " B in all)")
        for p, n in m["home_chunks"].items():
            print("    " + str(n).rjust(7) + "  " + p)
    print("stylesheet gz      " + str(m["stylesheet_gz"]).rjust(7) + " B  (budget " + str(BUDGET["stylesheet"]) + ")")
    for p, n in m["stylesheets"].items():
        print("    " + str(n).rjust(7) + "  " + p)
    print("fonts (woff2)      " + str(m["font_bytes"]).rjust(7) + " B  (budget " + str(BUDGET["font"]) + ")  preloaded " + str(m["font_preloaded_bytes"]) + " B  — the market's own")
    for p, n in m["fonts"].items():
        print("    " + str(n).rjust(7) + "  " + p + ("  [preload]" if p in m["preloaded_fonts"] else "") + "  named by " + ", ".join(m["font_refs"][p]))
    if m["fonts_not_loaded"]:
        print("info: not loaded by the market (the portal's, copied from the shared public/fonts; not counted):")
        for p, n in m["fonts_not_loaded"].items():
            print("    " + str(n).rjust(7) + "  " + p)
    ok = all(m["within"].values())
    print("within budgets: " + ("yes" if ok else "NO " + json.dumps(m["within"])))
    return 0 if ok else 1


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    raise SystemExit(main())
