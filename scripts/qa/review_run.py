"""
One evidence run for a design review. It serves a built market folder locally, takes lab Lighthouse
(mobile + desktop, median of 3) on its home, and walks it with the QA harness at the six viewports.

    python scripts/qa/review_run.py --dist <built dist-market> --out business_data/design_review/<date>/after \
        --label after --commit <sha> [--port 5174] [--full-check] [--skip-lighthouse]

Both sides of a review must be built with VITE_API_URL=http://localhost:8002 and reviewed while
scripts/qa/readonly_api.py is running on that port. The stub records production GETs once and
replays them, and never forwards a write. That keeps Lighthouse and the app's own beacons (which
Playwright's mocks never see) away from the live API, and gives both sides the same data.

The preview is `vite preview --strictPort` on --port. The API's CORS allows 5173-5175, but the stub
does not care. The preview is started here and stopped at the end.

Output in --out:
- meta.json: label, commit, dist, when, the stub cache.
- bundle.json: from scripts/qa/bundle_sizes.py.
- lighthouse-{mobile,desktop}-{1,2,3}.report.{json,html}, lighthouse.json (the median run per form)
  and lighthouse_runs.json (all runs).
- results.json, index.md, shots/<viewport>/<state>.png: the review screens, run with --reduced-motion
  so the frames are stable.
- full/: with --full-check, every harness state with motion on (the pass/fail run).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"
STUB = "http://localhost:8002"
REVIEW_SCREENS = "home,category,search_q,product,cart_under,checkout_met,tracking,brand"
LH_RUNS = 3


def wait_http(url: str, seconds: float) -> bool:
    t0 = time.time()
    while time.time() - t0 < seconds:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5)
    return False


def chrome_path() -> str | None:
    if os.environ.get("CHROME_PATH"):
        return os.environ["CHROME_PATH"]
    for p in (
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
    ):
        if os.path.exists(p):
            return p
    return None


def lighthouse_once(base: str, out: Path, form: str, n: int) -> dict | None:
    target = out / ("lighthouse-" + form + "-" + str(n))
    cmd = (
        "npx --yes lighthouse " + base + "/ --output=json --output=html --output-path=" + json.dumps(str(target))
        + " --only-categories=performance,accessibility,best-practices --quiet"
        + ' --chrome-flags="--headless=new --no-sandbox --disable-gpu"'
        + (" --preset=desktop" if form == "desktop" else "")
    )
    env = dict(os.environ)
    cp = chrome_path()
    if cp:
        env["CHROME_PATH"] = cp
    print("· lighthouse " + form + " #" + str(n), flush=True)
    r = subprocess.run(cmd, shell=True, cwd=str(ROOT), env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    report = Path(str(target) + ".report.json")
    # the report is the result: on Windows chrome-launcher can exit non-zero while deleting its
    # temp profile AFTER the report was written (rmSync EBUSY), so the exit code alone decides nothing
    if not report.exists():
        print("  lighthouse " + form + " failed: " + (r.stderr or r.stdout)[-800:], flush=True)
        return None
    return summarize(report, form)


def summarize(report: Path, form: str) -> dict:
    data = json.loads(report.read_text(encoding="utf-8"))
    cats = data.get("categories", {})
    aud = data.get("audits", {})

    def num(k: str):
        return (aud.get(k) or {}).get("numericValue")

    s = {
        "form": form,
        "report": report.name,
        "performance": round((cats.get("performance", {}).get("score") or 0) * 100),
        "accessibility": round((cats.get("accessibility", {}).get("score") or 0) * 100),
        "best_practices": round((cats.get("best-practices", {}).get("score") or 0) * 100),
        "lcp_ms": num("largest-contentful-paint"),
        "fcp_ms": num("first-contentful-paint"),
        "tbt_ms": num("total-blocking-time"),
        "cls": num("cumulative-layout-shift"),
        "si_ms": num("speed-index"),
        "tti_ms": num("interactive"),
        "lighthouse": data.get("lighthouseVersion"),
    }
    print("  " + json.dumps(s), flush=True)
    return s


def median_run(runs: list[dict]) -> dict:
    """The run whose performance score is the median (ties broken by LCP), so every number is one real run."""
    ranked = sorted(runs, key=lambda r: (r["performance"], -(r["lcp_ms"] or 0)))
    pick = ranked[len(ranked) // 2]
    return dict(pick, runs=len(runs), perf_spread=[r["performance"] for r in runs], lcp_median_ms=statistics.median([r["lcp_ms"] or 0 for r in runs]))


def harness(base: str, out: Path, api: str, only: str, reduced: bool) -> int:
    cmd = [sys.executable, str(ROOT / "scripts" / "qa" / "market_qa.py"), "--base", base, "--api", api, "--out", str(out)]
    if only:
        cmd += ["--only", only]
    if reduced:
        cmd.append("--reduced-motion")
    print("- harness -> " + str(out) + (" (reduced motion)" if reduced else ""), flush=True)
    return subprocess.run(cmd, cwd=str(ROOT)).returncode


def main() -> int:
    ap = argparse.ArgumentParser(description="serve a market build, Lighthouse it, walk it with the QA harness")
    ap.add_argument("--dist", required=True, help="a built market folder (built with VITE_API_URL=" + STUB + ")")
    ap.add_argument("--out", required=True, help="output folder (under business_data/, gitignored)")
    ap.add_argument("--label", required=True, help="before | after | after-r1 …")
    ap.add_argument("--commit", required=True, help="the commit the dist was built from")
    ap.add_argument("--port", type=int, default=5174)
    ap.add_argument("--api", default=STUB, help="the API the harness asks for a rep slug (the stub)")
    ap.add_argument("--only", default=REVIEW_SCREENS, help="harness states for the review screens")
    ap.add_argument("--full-check", action="store_true", help="also run every harness state with motion on into <out>/full")
    ap.add_argument("--skip-lighthouse", action="store_true")
    ap.add_argument("--skip-harness", action="store_true")
    args = ap.parse_args()

    dist = Path(args.dist).resolve()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    base = "http://localhost:" + str(args.port)

    js = " ".join(p.read_text(encoding="utf-8", errors="replace") for p in (dist / "assets").glob("*.js"))
    if "%VITE_API_URL%" in (dist / "index.html").read_text(encoding="utf-8") or "onrender.com" in js or "localhost:8002" not in (js + (dist / "index.html").read_text(encoding="utf-8")):
        print("refusing: " + str(dist) + " is not built against the local stub (VITE_API_URL=" + STUB + ")", file=sys.stderr)
        return 2
    if not wait_http(STUB + "/public/market", 120):
        print("the read-only stub is not answering on " + STUB + " — start scripts/qa/readonly_api.py first", file=sys.stderr)
        return 2

    (out / "meta.json").write_text(
        json.dumps({"label": args.label, "commit": args.commit, "dist": str(dist), "at": dt.datetime.now().isoformat(timespec="seconds"), "api": STUB, "screens": args.only.split(",")}, indent=1),
        encoding="utf-8",
    )
    sizes = subprocess.run([sys.executable, str(ROOT / "scripts" / "qa" / "bundle_sizes.py"), str(dist), "--json"], capture_output=True, text=True, encoding="utf-8")
    if sizes.returncode in (0, 1) and sizes.stdout.strip():
        (out / "bundle.json").write_text(sizes.stdout, encoding="utf-8")

    preview = subprocess.Popen(
        "npx vite preview --outDir " + json.dumps(str(dist)) + " --port " + str(args.port) + " --strictPort",
        shell=True,
        cwd=str(WEB),
        env=dict(os.environ, VITE_APP="market"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        if not wait_http(base + "/", 60):
            print("preview did not come up on " + base, file=sys.stderr)
            return 2
        print("preview up at " + base + " serving " + str(dist), flush=True)

        if not args.skip_lighthouse:
            lh, all_runs = {}, {}
            for form in ("mobile", "desktop"):
                runs = [s for s in (lighthouse_once(base, out, form, n) for n in range(1, LH_RUNS + 1)) if s]
                all_runs[form] = runs
                if runs:
                    lh[form] = median_run(runs)
            (out / "lighthouse.json").write_text(json.dumps(lh, indent=1), encoding="utf-8")
            (out / "lighthouse_runs.json").write_text(json.dumps(all_runs, indent=1), encoding="utf-8")

        rc = 0
        if not args.skip_harness:
            rc = harness(base, out, args.api, args.only, reduced=True)
            if args.full_check:
                rc = max(rc, harness(base, out / "full", args.api, "", reduced=False))
        return rc
    finally:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(preview.pid), "/T", "/F"], capture_output=True)
        else:
            preview.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
