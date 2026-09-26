"""
Scroll videos for motion reviews. Screenshots cannot show motion, so this records it.

    python scripts/qa/scroll_video.py --before http://localhost:5173 --after http://localhost:5174 \
        --out business_data/design_review/<date>/motion [--routes /,/t/cable] [--viewports 1366x768,430x932]

For every route × viewport it records the same scripted scroll on both builds and joins them into
one MP4 (H.264; it plays on a phone and in WhatsApp):
- the scroll: 700 px/s down, driven by requestAnimationFrame, then a short hold and a quick scroll
  back to the top (so reveals that replay on the way up are visible);
- desktop viewports are stacked (before on top), phones are side by side (before on the left);
- each side is labelled.

Safety is the harness's: service workers are blocked; every non-GET /public/* request is answered
in the browser; and the builds are expected to be pointed at scripts/qa/readonly_api.py. Nothing is
written anywhere. The splash is skipped (sessionStorage), so the page itself is recorded.

Needs ffmpeg on PATH.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import Route, sync_playwright

CORS = {"access-control-allow-origin": "*", "access-control-allow-headers": "*", "access-control-allow-methods": "*"}
SPLASH_KEY = "yq-splash-session"
CART_KEY = "yq-shop-cart:market"

SCROLL_JS = """
async ({ speed, maxPx, hold, back }) => {
  const doc = document.scrollingElement || document.documentElement;
  const end = Math.min(doc.scrollHeight - innerHeight, maxPx);
  const run = (from, to, pxPerS) => new Promise((done) => {
    const t0 = performance.now(), dist = to - from, dur = Math.abs(dist) / pxPerS * 1000;
    const step = (t) => {
      const k = Math.min(1, (t - t0) / dur);
      window.scrollTo(0, from + dist * k);
      k < 1 ? requestAnimationFrame(step) : done();
    };
    requestAnimationFrame(step);
  });
  await run(0, end, speed);
  await new Promise((r) => setTimeout(r, hold));
  if (back) await run(end, 0, speed * 3);
  await new Promise((r) => setTimeout(r, 600));
  return end;
}
"""


def no_prod_writes(route: Route) -> None:
    req = route.request
    if req.method in ("GET", "HEAD") or req.url.split("?")[0].endswith("/quote"):
        route.fallback()
        return
    if req.method == "OPTIONS":
        route.fulfill(status=204, headers=CORS, body="")
        return
    route.fulfill(status=200, headers=CORS, content_type="application/json", body=json.dumps({"ok": True, "qa": "not sent"}))


def record(pw, base: str, route: str, w: int, h: int, tmp: Path, speed: int, max_px: int) -> tuple[Path, float]:
    phone = w < 768
    browser = pw.chromium.launch()
    vdir = tmp / ("v" + str(time.time_ns()))
    ctx = browser.new_context(
        viewport={"width": w, "height": h},
        device_scale_factor=1,
        is_mobile=phone,
        has_touch=w < 1366,
        service_workers="block",
        reduced_motion="no-preference",
        locale="en-GB",
        record_video_dir=str(vdir),
        record_video_size={"width": w, "height": h},
    )
    ctx.add_init_script("(() => { try { sessionStorage.setItem(" + json.dumps(SPLASH_KEY) + ", '1'); localStorage.removeItem(" + json.dumps(CART_KEY) + "); } catch (e) {} })();")
    ctx.route("**/public/**", no_prod_writes)
    t0 = time.monotonic()
    page = ctx.new_page()
    page.goto(base + route, wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:  # noqa: BLE001
        pass
    page.wait_for_timeout(1500)
    start = time.monotonic() - t0 - 0.4  # keep a beat of the settled first screen
    page.evaluate(SCROLL_JS, {"speed": speed, "maxPx": max_px, "hold": 900, "back": True})
    video = page.video
    ctx.close()
    browser.close()
    path = Path(video.path())
    return path, max(0.0, start)


def join(before: tuple[Path, float], after: tuple[Path, float], out: Path, w: int, h: int, label_b: str, label_a: str) -> None:
    stack = "hstack" if h > w else "vstack"
    font = "fontsize=" + str(max(16, w // 40)) + ":fontcolor=white:box=1:boxcolor=black@0.65:boxborderw=6:x=10:y=10"
    fc = (
        "[0:v]setpts=PTS-STARTPTS,drawtext=text='" + label_b + "':" + font + "[b];"
        "[1:v]setpts=PTS-STARTPTS,drawtext=text='" + label_a + "':" + font + "[a];"
        "[b][a]" + stack + "=inputs=2:shortest=0,format=yuv420p[v]"
    )
    cmd = [
        "ffmpeg", "-v", "error", "-y",
        "-ss", f"{before[1]:.2f}", "-i", str(before[0]),
        "-ss", f"{after[1]:.2f}", "-i", str(after[0]),
        "-filter_complex", fc, "-map", "[v]", "-r", "30", "-c:v", "libx264", "-crf", "26", "-preset", "medium", "-movflags", "+faststart", str(out),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg failed: " + r.stderr[-600:])


def main() -> int:
    ap = argparse.ArgumentParser(description="before/after scroll videos")
    ap.add_argument("--before", required=True, help="base URL of the before build (local preview)")
    ap.add_argument("--after", required=True, help="base URL of the after build (local preview)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--routes", default="/,/t/cable")
    ap.add_argument("--viewports", default="1366x768,430x932")
    ap.add_argument("--speed", type=int, default=700, help="scroll speed, px per second")
    ap.add_argument("--max-px", type=int, default=6000, help="scroll at most this far")
    ap.add_argument("--label-before", default="BEFORE (live today)")
    ap.add_argument("--label-after", default="AFTER (R4)")
    args = ap.parse_args()
    for base in (args.before, args.after):
        if not base.startswith(("http://localhost:", "http://127.0.0.1:")):
            print("refusing: " + base + " is not a local preview", file=sys.stderr)
            return 2
    if not shutil.which("ffmpeg"):
        print("ffmpeg is not on PATH", file=sys.stderr)
        return 2
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    made = []
    with tempfile.TemporaryDirectory() as td, sync_playwright() as pw:
        tmp = Path(td)
        for vp in [v.strip() for v in args.viewports.split(",") if v.strip()]:
            w, h = (int(x) for x in vp.split("x"))
            for route in [r.strip() for r in args.routes.split(",") if r.strip()]:
                name = ("home" if route == "/" else route.strip("/").replace("/", "-").replace("?", "_")) + "__" + vp + ".mp4"
                print("- " + vp + " " + route, flush=True)
                b = record(pw, args.before.rstrip("/"), route, w, h, tmp, args.speed, args.max_px)
                a = record(pw, args.after.rstrip("/"), route, w, h, tmp, args.speed, args.max_px)
                join(b, a, out / name, w, h, args.label_before, args.label_after)
                made.append(name)
    print("wrote " + ", ".join(made) + " in " + str(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
