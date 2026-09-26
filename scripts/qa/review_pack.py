"""
Review pack: before|after images a reviewer, human or agent, can read at full legibility.

    python scripts/qa/review_pack.py --before .../before --after .../after --out .../pack

For each review screen at each viewport, the pack holds one composite:
- side by side for portrait viewports (phones, tablet portrait);
- stacked for landscape ones (1024, 1366, 1920);
- in CSS pixels, with a labelled header, and no longer than about 1560 px on its long side.

A downscaled long page is unreadable, so the long pages (the `__full` shots at the lead viewports)
are cut into viewport-height tiles and paired tile by tile instead.

Each screen also gets findings.json: the harness hard failures and warnings for that screen, before
and after. index.md lists everything.

Pure: it reads the two review_run.py folders and writes PNGs and JSON.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SCREENS = ["home", "category", "search_q", "product", "cart_under", "checkout_met", "tracking", "brand"]
VIEWPORTS = ["360x780", "430x932", "768x1024", "1024x768", "1366x768", "1920x1080"]
LEAD = ["430x932", "1366x768"]
MAX_SIDE = 1560
GAP = 16
HEAD = 30
MAX_TILES = 8


def font(size: int):
    for name in ("segoeuib.ttf", "arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


FONT = font(18)


def css(im: Image.Image, css_w: int) -> Image.Image:
    """Back to CSS pixels, so a dpr-2 phone shot is not twice the size of the others."""
    im = im.convert("RGB")
    if im.width != css_w:
        im = im.resize((css_w, round(im.height * css_w / im.width)), Image.LANCZOS)
    return im


def labelled(im: Image.Image, text: str) -> Image.Image:
    out = Image.new("RGB", (im.width, im.height + HEAD), (246, 244, 239))
    d = ImageDraw.Draw(out)
    d.text((8, 5), text, fill=(27, 21, 34), font=FONT)
    d.line([(0, HEAD - 1), (im.width, HEAD - 1)], fill=(201, 193, 180))
    out.paste(im, (0, HEAD))
    return out


def blank(w: int, h: int, text: str) -> Image.Image:
    im = Image.new("RGB", (w, h), (255, 255, 255))
    ImageDraw.Draw(im).text((12, 12), text, fill=(110, 103, 118), font=FONT)
    return im


def compose(before: Image.Image, after: Image.Image, side_by_side: bool) -> Image.Image:
    if side_by_side:
        h = max(before.height, after.height)
        out = Image.new("RGB", (before.width + GAP + after.width, h), (201, 193, 180))
        out.paste(before, (0, 0))
        out.paste(after, (before.width + GAP, 0))
    else:
        w = max(before.width, after.width)
        out = Image.new("RGB", (w, before.height + GAP + after.height), (201, 193, 180))
        out.paste(before, (0, 0))
        out.paste(after, (0, before.height + GAP))
    long_side = max(out.size)
    if long_side > MAX_SIDE:
        k = MAX_SIDE / long_side
        out = out.resize((round(out.width * k), round(out.height * k)), Image.LANCZOS)
    return out


def load(p: Path, css_w: int) -> Image.Image | None:
    return css(Image.open(p), css_w) if p.exists() else None


def tiles(im: Image.Image, h: int) -> list[Image.Image]:
    return [im.crop((0, y, im.width, min(y + h, im.height))) for y in range(0, im.height, h)][:MAX_TILES]


def findings_for(folder: Path, key: str) -> dict:
    p = folder / "results.json"
    if not p.exists():
        return {"fails": [], "warns": []}
    res = json.loads(p.read_text(encoding="utf-8"))

    def mine(f: dict) -> bool:
        return str(f.get("state") or "").split(" · ")[0] == key

    return {"fails": [f for f in res.get("hard_failures") or [] if mine(f)], "warns": [f for f in res.get("warnings") or [] if mine(f)]}


def main() -> int:
    ap = argparse.ArgumentParser(description="before|after composites for reviewers")
    ap.add_argument("--before", required=True)
    ap.add_argument("--after", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    before, after, out = Path(args.before), Path(args.after), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    mb = json.loads((before / "meta.json").read_text(encoding="utf-8")) if (before / "meta.json").exists() else {}
    ma = json.loads((after / "meta.json").read_text(encoding="utf-8")) if (after / "meta.json").exists() else {}
    tag_b = "BEFORE " + str(mb.get("commit", ""))[:9]
    tag_a = "AFTER " + str(ma.get("commit", ""))[:9]

    index = ["# Review pack", "", tag_b + " vs " + tag_a + ". Portrait viewports are side by side (before left, after right); landscape ones are stacked (before on top).", ""]
    for key in SCREENS:
        sdir = out / key
        sdir.mkdir(exist_ok=True)
        index.append("## " + key)
        for vp in VIEWPORTS:
            w, h = (int(x) for x in vp.split("x"))
            ib = load(before / "shots" / vp / (key + ".png"), w) or blank(w, h, "no before shot")
            ia = load(after / "shots" / vp / (key + ".png"), w) or blank(w, h, "no after shot")
            comp = compose(labelled(ib, tag_b + " · " + vp), labelled(ia, tag_a + " · " + vp), side_by_side=h > w)
            name = key + "__" + vp + ".png"
            comp.save(sdir / name, optimize=True)
            index.append("- " + key + "/" + name + " (" + str(comp.width) + "×" + str(comp.height) + ")")
            if vp in LEAD:
                fb, fa = before / "shots" / vp / (key + "__full.png"), after / "shots" / vp / (key + "__full.png")
                if fb.exists() or fa.exists():
                    tb = tiles(load(fb, w), h) if fb.exists() else []
                    ta = tiles(load(fa, w), h) if fa.exists() else []
                    for i in range(max(len(tb), len(ta))):
                        cb = tb[i] if i < len(tb) else blank(w, h, "(before page ended)")
                        ca = ta[i] if i < len(ta) else blank(w, h, "(after page ended)")
                        comp = compose(labelled(cb, tag_b + " · " + vp + " · screen " + str(i + 1)), labelled(ca, tag_a + " · " + vp + " · screen " + str(i + 1)), side_by_side=h > w)
                        tname = key + "__" + vp + "__full-" + str(i + 1) + ".png"
                        comp.save(sdir / tname, optimize=True)
                        index.append("- " + key + "/" + tname + " (long page, screen " + str(i + 1) + ")")
        (sdir / "findings.json").write_text(json.dumps({"before": findings_for(before, key), "after": findings_for(after, key)}, indent=1), encoding="utf-8")
        index.append("- " + key + "/findings.json (harness findings, before and after)")
        index.append("")
    (out / "index.md").write_text("\n".join(index), encoding="utf-8")
    print("wrote " + str(out) + " (" + str(sum(1 for _ in out.rglob("*.png"))) + " images)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
