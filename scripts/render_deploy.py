"""Deploy, roll back or inspect the Render API service -- WITHOUT ever touching its env vars.

    python -m scripts.render_deploy status                     # the last 5 deploys (id, status, commit)
    python -m scripts.render_deploy deploy --commit <sha> --wait   # deploy exactly that commit of main
    python -m scripts.render_deploy deploy --wait              # deploy the branch head
    python -m scripts.render_deploy rollback --deploy <dep-id> --wait

Why this exists (24-Sep-2026): Render does not deploy on push here (every deploy has
trigger=api), and the older scripts/deploy_render.py PUTs the whole env-var list from the
18-Aug .env.render for an existing service. Render's PUT replaces the ENTIRE env set, so that
script would revert ALLOWED_ORIGINS / APP_BASE_URL (breaking CORS for the marketplace, the
salesman app and checkout) and delete every key added since. Env changes are made in the Render
dashboard, one key at a time. This script never reads .env.render and never prints a secret.

RENDER_API_KEY and RENDER_SERVICE_ID come from .env (RENDER_SERVICE_ID defaults to the live
service srv-da20eavlk1mc73agsbk0).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

API = "https://api.render.com/v1"
DONE_OK = {"live"}
DONE_BAD = {"build_failed", "update_failed", "canceled", "pre_deploy_failed", "deactivated"}


def _call(method: str, path: str, body: dict | None = None) -> tuple[int, object]:
    token = os.environ.get("RENDER_API_KEY", "").strip()
    if not token:
        raise SystemExit("RENDER_API_KEY missing (.env)")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{API}{path}", data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/json")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw.strip() else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw[:300]


def _svc() -> str:
    return os.environ.get("RENDER_SERVICE_ID", "srv-da20eavlk1mc73agsbk0")


def _deploys(limit: int = 5) -> list[dict]:
    st, res = _call("GET", f"/services/{_svc()}/deploys?limit={limit}")
    if st != 200:
        raise SystemExit(f"could not list deploys (HTTP {st}): {res}")
    return [r.get("deploy", r) for r in res or []]


def _show(d: dict) -> str:
    c = d.get("commit") or {}
    return (f"{d.get('id')}  {d.get('status'):16s} {str(c.get('id') or '')[:7]}  "
            f"{(c.get('message') or '').splitlines()[0][:60] if c.get('message') else ''}  {d.get('createdAt', '')}")


def _wait(dep_id: str, minutes: int = 25) -> int:
    deadline, last = time.time() + minutes * 60, None
    while time.time() < deadline:
        time.sleep(15)
        st, res = _call("GET", f"/services/{_svc()}/deploys/{dep_id}")
        if st != 200:
            continue
        s = (res or {}).get("status")
        if s != last:
            print(f"  [{time.strftime('%H:%M:%S')}] {s}")
            last = s
        if s in DONE_OK:
            print("LIVE")
            return 0
        if s in DONE_BAD:
            print(f"FAILED: {s} -- see https://dashboard.render.com/web/{_svc()}/logs")
            return 1
    print("timed out waiting for the deploy")
    return 1


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    d = sub.add_parser("deploy")
    d.add_argument("--commit", help="exact commit sha to deploy (default: branch head)")
    d.add_argument("--wait", action="store_true")
    r = sub.add_parser("rollback")
    r.add_argument("--deploy", required=True, help="the earlier deploy id to roll back to")
    r.add_argument("--wait", action="store_true")
    a = ap.parse_args(argv)

    if a.cmd == "status":
        for dep in _deploys():
            print(_show(dep))
        return 0
    if a.cmd == "deploy":
        body = {"clearCache": "do_not_clear"}
        if a.commit:
            body["commitId"] = a.commit
        st, res = _call("POST", f"/services/{_svc()}/deploys", body)
        if st not in (200, 201, 202):
            print(f"deploy trigger failed (HTTP {st}): {res}")
            return 1
        dep = (res or {}).get("deploy", res) or {}
        print(f"triggered {dep.get('id')} for commit {a.commit or 'branch head'}")
        return _wait(dep["id"]) if a.wait and dep.get("id") else 0
    st, res = _call("POST", f"/services/{_svc()}/rollback", {"deployId": a.deploy})
    if st not in (200, 201, 202):
        print(f"rollback failed (HTTP {st}): {res}")
        return 1
    dep = (res or {}).get("deploy", res) or {}
    print(f"rollback triggered: {dep.get('id')} -> {a.deploy}")
    return _wait(dep["id"]) if a.wait and dep.get("id") else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
