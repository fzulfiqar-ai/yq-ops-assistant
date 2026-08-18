#!/usr/bin/env python3
"""Create (or update) the Render free-tier service for the ops API and deploy it.

Replaces Railway, whose trial lapsed and whose deployments were REMOVED.

Usage:
    export RENDER_API_KEY=rnd_xxx          # Render -> Account Settings -> API Keys
    python scripts/deploy_render.py

Reads the 24 app env vars from .env.render (gitignored), which is produced by:
    railway variables --kv | grep -v '^RAILWAY_' > .env.render

Idempotent: if the service already exists it syncs env vars and redeploys
instead of erroring, so re-running after a failure is safe.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

API = "https://api.render.com/v1"
SERVICE_NAME = "yq-ops-assistant"
REPO = "https://github.com/fzulfiqar-ai/yq-ops-assistant"
BRANCH = "main"
REGION = "frankfurt"          # closest free region to Bahrain
ENV_FILE = ".env.render"

# Deploy statuses that mean "stop polling".
DONE_OK = {"live"}
DONE_BAD = {"build_failed", "update_failed", "canceled", "pre_deploy_failed", "deactivated"}


def die(msg: str) -> None:
    print(f"\nERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def call(method: str, path: str, token: str, body: dict | list | None = None) -> tuple[int, object]:
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
            return e.code, raw
    except urllib.error.URLError as e:
        die(f"cannot reach the Render API: {e.reason}")
        raise  # unreachable, keeps type checkers happy


def load_env_vars() -> list[dict]:
    if not os.path.exists(ENV_FILE):
        die(
            f"{ENV_FILE} not found. Export the Railway values FIRST — they are\n"
            "       unrecoverable once the Railway project is deleted:\n"
            "         railway variables --kv | grep -v '^RAILWAY_' > .env.render"
        )
    out, seen = [], set()
    for line in open(ENV_FILE, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        if k.startswith("RAILWAY_") or k in seen:
            continue          # platform-injected, or a duplicate
        seen.add(k)
        out.append({"key": k, "value": v})
    if not out:
        die(f"{ENV_FILE} contained no usable KEY=VALUE lines")
    return out


def main() -> None:
    token = os.environ.get("RENDER_API_KEY", "").strip()
    if not token:
        die("RENDER_API_KEY is not set.\n"
            "       Render Dashboard -> Account Settings -> API Keys -> Create API Key")

    env_vars = load_env_vars()
    print(f"Loaded {len(env_vars)} env vars from {ENV_FILE}")

    status, owners = call("GET", "/owners?limit=20", token)
    if status == 401:
        die("Render rejected the API key (401). Generate a fresh one.")
    if status != 200 or not owners:
        die(f"could not list Render owners (HTTP {status}): {owners}")
    owner = owners[0]["owner"]
    owner_id = owner["id"]
    print(f"Render account: {owner.get('email') or owner.get('name')}  (owner {owner_id})")

    # Idempotency: reuse the service if a previous run already made it.
    status, existing = call("GET", f"/services?name={SERVICE_NAME}&limit=20", token)
    svc = None
    if status == 200 and existing:
        for row in existing:
            s = row.get("service", row)
            if s.get("name") == SERVICE_NAME:
                svc = s
                break

    if svc:
        svc_id = svc["id"]
        print(f"Service already exists ({svc_id}) — syncing env vars and redeploying.")
        status, res = call("PUT", f"/services/{svc_id}/env-vars", token, env_vars)
        if status not in (200, 201):
            die(f"failed to set env vars (HTTP {status}): {res}")
        print(f"  env vars synced ({len(env_vars)})")
        status, res = call("POST", f"/services/{svc_id}/deploys", token, {"clearCache": "do_not_clear"})
        if status not in (200, 201):
            die(f"failed to trigger deploy (HTTP {status}): {res}")
    else:
        print(f"Creating web service '{SERVICE_NAME}' (docker, free, {REGION})...")
        payload = {
            "type": "web_service",
            "name": SERVICE_NAME,
            "ownerId": owner_id,
            "repo": REPO,
            "branch": BRANCH,
            "autoDeploy": "yes",
            "serviceDetails": {
                "env": "docker",
                "region": REGION,
                "plan": "free",
                "healthCheckPath": "/health",
                "envSpecificDetails": {
                    "dockerfilePath": "./Dockerfile",
                    "dockerContext": ".",
                },
            },
            "envVars": env_vars,
        }
        status, res = call("POST", "/services", token, payload)
        if status not in (200, 201):
            die(f"service creation failed (HTTP {status}): {json.dumps(res, indent=2)[:1500]}")
        svc = res.get("service", res)
        svc_id = svc["id"]
        print(f"  created: {svc_id}")

    url = (svc.get("serviceDetails") or {}).get("url") or f"https://{SERVICE_NAME}.onrender.com"

    # Free-tier Docker builds are slow (ffmpeg + wheels); allow ~20 min.
    print("\nBuilding. First Docker build takes 5-15 min on the free tier.")
    deadline = time.time() + 20 * 60
    last = None
    while time.time() < deadline:
        time.sleep(20)
        status, deploys = call("GET", f"/services/{svc_id}/deploys?limit=1", token)
        if status != 200 or not deploys:
            continue
        d = deploys[0].get("deploy", deploys[0])
        st = d.get("status")
        if st != last:
            print(f"  [{time.strftime('%H:%M:%S')}] {st}")
            last = st
        if st in DONE_OK:
            print(f"\nLIVE: {url}")
            print("\nNext: point Vercel's VITE_API_URL at that URL and rebuild the frontend")
            print("      (VITE_* values are baked in at build time):")
            print("        cd web && npx vercel deploy --prod")
            return
        if st in DONE_BAD:
            die(f"deploy ended as '{st}'. Check the build log:\n"
                f"       https://dashboard.render.com/web/{svc_id}/logs")
    die("timed out after 20 min. Check https://dashboard.render.com")


if __name__ == "__main__":
    main()
