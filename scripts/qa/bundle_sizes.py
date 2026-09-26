"""
Bundle sizes of a market build, the way .github/lighthouserc.json budgets them.

    python scripts/qa/bundle_sizes.py web/dist-market [--json]

Reads the built index.html, takes every script and stylesheet it references on the first load
(the entry, its modulepreloads and its CSS — not the lazy route chunks) and prints their gzip
sizes, plus the bytes of every woff2 under fonts/ (fonts are served as-is; woff2 is already
compressed). Budgets: script <= 184320 B, stylesheet <= 35840 B, font <= 97280 B.

The fonts figure is the whole folder — what a session that touches every route could load — and
the per-file list says which of them the served HTML preloads. Pure: no network, no build.
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


def gz_size(path: Path) -> int:
    return len(gzip.compress(path.read_bytes(), 9))


def measure(dist: Path) -> dict:
    html = (dist / "index.html").read_text(encoding="utf-8")
    refs = re.findall(r'(?:src|href)="/(assets/[^"]+\.(?:js|css))"', html)
    seen: list[str] = []
    for r in refs:
        if r not in seen:
            seen.append(r)
    scripts = {p: gz_size(dist / p) for p in seen if p.endswith(".js")}
    styles = {p: gz_size(dist / p) for p in seen if p.endswith(".css")}
    fonts_dir = dist / "fonts"
    fonts = {f.name: f.stat().st_size for f in sorted(fonts_dir.glob("*.woff2"))} if fonts_dir.exists() else {}
    preloaded = re.findall(r'<link rel="preload" as="font"[^>]*href="/fonts/([^"]+)"', html)
    return {
        "dist": str(dist),
        "script_gz": sum(scripts.values()),
        "stylesheet_gz": sum(styles.values()),
        "font_bytes": sum(fonts.values()),
        "font_preloaded_bytes": sum(fonts.get(f, 0) for f in preloaded),
        "scripts": scripts,
        "stylesheets": styles,
        "fonts": fonts,
        "preloaded_fonts": preloaded,
        "budget": BUDGET,
        "within": {
            "script": sum(scripts.values()) <= BUDGET["script"],
            "stylesheet": sum(styles.values()) <= BUDGET["stylesheet"],
            "font": sum(fonts.values()) <= BUDGET["font"],
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="market bundle sizes vs the Lighthouse budgets")
    ap.add_argument("dist", help="a built market folder (web/dist-market)")
    ap.add_argument("--json", action="store_true", help="print JSON only")
    args = ap.parse_args()
    dist = Path(args.dist)
    if not (dist / "index.html").exists():
        print("no index.html in " + str(dist), file=sys.stderr)
        return 2
    m = measure(dist)
    if args.json:
        print(json.dumps(m, indent=1))
        return 0
    print("initial script gz  " + str(m["script_gz"]).rjust(7) + " B  (budget " + str(BUDGET["script"]) + ")")
    for p, n in m["scripts"].items():
        print("    " + str(n).rjust(7) + "  " + p)
    print("stylesheet gz      " + str(m["stylesheet_gz"]).rjust(7) + " B  (budget " + str(BUDGET["stylesheet"]) + ")")
    for p, n in m["stylesheets"].items():
        print("    " + str(n).rjust(7) + "  " + p)
    print("fonts (woff2)      " + str(m["font_bytes"]).rjust(7) + " B  (budget " + str(BUDGET["font"]) + ")  preloaded " + str(m["font_preloaded_bytes"]) + " B")
    for p, n in m["fonts"].items():
        print("    " + str(n).rjust(7) + "  " + p + ("  [preload]" if p in m["preloaded_fonts"] else ""))
    ok = all(m["within"].values())
    print("within budgets: " + ("yes" if ok else "NO " + json.dumps(m["within"])))
    return 0 if ok else 1


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    raise SystemExit(main())
