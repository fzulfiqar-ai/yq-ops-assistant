"""API smoke check — hits every parameter-less GET endpoint in-process as an admin.

    python -m scripts.smoke_check

Uses FastAPI's TestClient with auth dependencies overridden (no server, no JWT),
so it exercises the full route table + real DB reads. Read-only endpoints only.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

import warnings  # noqa: E402
warnings.filterwarnings("ignore")

# Endpoints that mutate, stream, need params, or hit external services — not smoke targets.
# /bi/price-simulator and /coaching/brief require query params (422 without them is correct).
SKIP_PREFIXES = ("/ask", "/orchestrate", "/leads/discover", "/scheduler", "/escalation",
                 "/events/dispatch", "/ingest", "/openapi", "/docs", "/redoc",
                 "/bi/price-simulator", "/coaching/brief")


def main() -> int:
    from fastapi.testclient import TestClient

    from app.auth import CurrentUser, get_caller, get_current_user
    from app.main import app

    admin = CurrentUser(user_id="smoke", email="smoke@local", role="admin")
    app.dependency_overrides[get_current_user] = lambda: admin
    app.dependency_overrides[get_caller] = lambda: admin

    client = TestClient(app, raise_server_exceptions=False)
    ok = bad = 0
    for route in sorted(app.routes, key=lambda r: getattr(r, "path", "")):
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        if "GET" not in methods or "{" in path or path.startswith(SKIP_PREFIXES):
            continue
        t0 = time.perf_counter()
        try:
            r = client.get(path)
            ms = (time.perf_counter() - t0) * 1000
            good = r.status_code == 200
            ok += 1 if good else 0
            bad += 0 if good else 1
            print(f"  {'OK ' if good else 'ERR'} {r.status_code} {path:28} {ms:7.0f}ms")
        except Exception as e:  # noqa: BLE001
            bad += 1
            print(f"  ERR ---  {path:28} {type(e).__name__}: {str(e)[:80]}")
    print(f"\n{ok} OK, {bad} failing")
    return 0 if bad == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
