"""Portal boot under a slow or failing /me — deterministic, no real login, no API or DB traffic.

    python -m scripts.perf_auth_check --url http://localhost:5173      # a `vite preview` of web/dist
    python -m scripts.perf_auth_check --url https://ops.yqmarketplace.com   # the live build

Every API call is answered by Playwright (page.route), and a fake Supabase session is put in
localStorage, so nothing reaches the API or the database and no account is needed. The session is
never valid anywhere: it only gets the SPA past its "am I signed in" check.

Scenarios (perf-2609 — the "Waking the server" incident of 29-Sep-2026):
  returning_slow_me   a returning user, /me takes 8 s    -> the shell should be up at once
  returning_me_500    a returning user, /me always 500   -> the app must stay up
  first_me_500        no cached /me, /me always 500      -> an honest error, never "Waking"
  first_me_ok         no cached /me, /me answers 300 ms  -> exactly ONE /me request
For each: ms until the shell is visible, the /me request count, and which screens appeared.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
UID = "00000000-0000-4000-8000-00000000qa01"
ME = {"email": "qa-perf@example.com", "role": "admin", "features": [], "full_name": "QA Perf",
      "must_reset": False, "read_only": False}


def _supabase_ref() -> str:
    for line in (ROOT / "web" / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("VITE_SUPABASE_URL="):
            return urlparse(line.split("=", 1)[1].strip()).hostname.split(".")[0]
    raise SystemExit("VITE_SUPABASE_URL missing from web/.env")


def _fake_session() -> dict:
    now = int(time.time())
    b64 = lambda o: base64.urlsafe_b64encode(json.dumps(o).encode()).decode().rstrip("=")  # noqa: E731
    jwt = f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64({'sub': UID, 'exp': now + 7200, 'role': 'authenticated'})}.x"
    user = {"id": UID, "aud": "authenticated", "role": "authenticated", "email": ME["email"],
            "app_metadata": {}, "user_metadata": {}, "created_at": "2026-09-01T00:00:00Z"}
    return {"access_token": jwt, "token_type": "bearer", "expires_in": 7200, "expires_at": now + 7200,
            "refresh_token": "qa-not-a-token", "user": user}


async def run(url: str, scenario: str, cached: bool, me_delay_ms: int, me_status: int, watch_s: float) -> dict:
    ref = _supabase_ref()
    counts = {"me": 0}
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx = await browser.new_context(viewport={"width": 1366, "height": 800})
        init = {f"sb-{ref}-auth-token": json.dumps(_fake_session()), "yq-intro-v2": "done", "yq-role": "admin"}
        if cached:
            init["yq-me-v1"] = json.dumps({"uid": UID, "me": ME, "at": int(time.time() * 1000)})
        await ctx.add_init_script(
            "(() => { if (sessionStorage.getItem('qa-seeded')) return; sessionStorage.setItem('qa-seeded','1');"
            f" const kv = {json.dumps(init)}; for (const k in kv) localStorage.setItem(k, kv[k]); }})()")

        async def api(route):
            path = urlparse(route.request.url).path
            if route.request.method == "OPTIONS":
                return await route.fulfill(status=204, headers={"access-control-allow-origin": "*",
                                                         "access-control-allow-headers": "*",
                                                         "access-control-allow-methods": "*"})
            cors = {"access-control-allow-origin": "*", "content-type": "application/json"}
            if path == "/me":
                counts["me"] += 1
                if me_delay_ms:
                    await asyncio.sleep(me_delay_ms / 1000)
                if me_status != 200:
                    return await route.fulfill(status=me_status, headers=cors,
                                         body=json.dumps({"detail": "Server error", "ref": "qa0001"}))
                return await route.fulfill(status=200, headers=cors, body=json.dumps(ME))
            return await route.fulfill(status=200, headers=cors, body="{}")

        for host in ("https://api.yqmarketplace.com/**", "http://localhost:8001/**"):
            await ctx.route(host, api)
        await ctx.route(f"https://{ref}.supabase.co/**", lambda r: r.fulfill(status=200, body="{}",
                                                                       headers={"content-type": "application/json",
                                                                                "access-control-allow-origin": "*"}))
        page = await ctx.new_page()
        seen: set[str] = set()
        t0 = time.perf_counter()
        await page.goto(url.rstrip("/") + "/shop-orders", wait_until="commit")
        shell_ms = None
        while time.perf_counter() - t0 < watch_s:
            try:
                body = await page.inner_text("body", timeout=500)
            except Exception:  # noqa: BLE001 -- navigating
                body = ""
            for label, needle in (("waking", "Waking the server"), ("server-error", "The server hit an error"),
                                  ("connecting", "Connecting to the server"), ("cant-reach", "Can't reach the server")):
                if needle in body:
                    seen.add(label)
            if shell_ms is None and await page.locator("nav a[href='/shop-orders']").count() > 0:
                shell_ms = round((time.perf_counter() - t0) * 1000)
            await page.wait_for_timeout(100)
        shell_now = await page.locator("nav a[href='/shop-orders']").count() > 0
        await browser.close()
    return {"scenario": scenario, "shell_ms": shell_ms, "shell_at_end": shell_now,
            "me_requests": counts["me"], "screens": sorted(seen)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:5173")
    a = ap.parse_args()
    cases = [("returning_slow_me", True, 8000, 200, 12), ("returning_me_500", True, 0, 500, 16),
             ("first_me_500", False, 0, 500, 16), ("first_me_ok", False, 300, 200, 6)]
    out = [asyncio.run(run(a.url, *c)) for c in cases]
    for r in out:
        print(json.dumps(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
