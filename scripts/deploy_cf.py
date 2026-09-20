"""Deploy one of the two web builds to Cloudflare (Workers static assets) from the developer machine.

    python -m scripts.deploy_cf market     # Worker yq-marketplace  -> https://yqmarketplace.com
    python -m scripts.deploy_cf portal     # Worker yq-bahrain-ops  -> https://ops.yqmarketplace.com

Cloudflare replaced Vercel as the static host on 20-Sep-2026: Vercel Hobby caps Fast Data Transfer
at 100 GB/month, pauses every project in the team past it, and forbids commercial use. Static-asset
requests on Workers are free and unlimited and commercial use is allowed. "Cloudflare Pages" is now
this same product ("the latest version of Pages, part of Workers" - wrangler 4.135 delegates
`wrangler pages ...` to it), so each app is a Worker that serves a directory of assets and nothing
else: web/wrangler.market.jsonc and web/wrangler.portal.jsonc (SPA fallback, public/_headers).
Vercel now only redirects the old hostnames (web/vercel.json, deployed by scripts/deploy_web.py).

Unlike Vercel, the build runs HERE with the target's env set explicitly (TARGETS): the market bundle
bakes in the API origin, the portal additionally the public Supabase URL + anon key from web/.env.
BUILD_ID is the commit, which web/vite.config.ts stamps into /version.json and the service worker's
__BUILD_ID__ so a client can tell deployments apart.

Needs, in the repo-root .env: CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN_WRITE (an account token
with Workers Scripts + Workers Routes + DNS write). wrangler is fetched by npx on first use. The same
deploy runs on every push to main from .github/workflows/cf-deploy.yml once the CLOUDFLARE_*
repository secrets exist; this script is the manual path and the fallback.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

# wrangler prints emoji and box-drawing characters; a cp1252 console (Windows) must not crash on them
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
API_URL = "https://yq-ops-assistant.onrender.com"
WRANGLER = ["npx", "--yes", "wrangler@4"]

TARGETS = {
    "market": {
        "config": "wrangler.market.jsonc", "script": "build:market", "out": "dist-market",
        "env": {"VITE_APP": "market", "VITE_API_URL": API_URL},
    },
    "portal": {
        "config": "wrangler.portal.jsonc", "script": "build", "out": "dist",
        "env": {"VITE_API_URL": API_URL},  # + VITE_SUPABASE_URL / VITE_SUPABASE_ANON_KEY from web/.env
    },
}


def _dotenv(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$", line)
        if m and not line.lstrip().startswith("#"):
            out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return out


def _run(cmd: list[str], env: dict[str, str], check: bool = True) -> tuple[int, str]:
    proc = subprocess.run(cmd, cwd=WEB, env=env, text=True, capture_output=True,
                          shell=(os.name == "nt"), encoding="utf-8", errors="replace")
    out = (proc.stdout or "") + (proc.stderr or "")
    if check and proc.returncode != 0:
        print(out[-3000:])
        raise SystemExit(f"{' '.join(cmd[:4])} failed ({proc.returncode})")
    return proc.returncode, out


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, text=True, capture_output=True).stdout.strip()


def main(argv: list[str]) -> int:
    target = (argv[0] if argv else "").strip().lower()
    if target not in TARGETS:
        print(__doc__)
        return 1
    t = TARGETS[target]
    secrets = _dotenv(ROOT / ".env")
    account = secrets.get("CLOUDFLARE_ACCOUNT_ID", "")
    token = secrets.get("CLOUDFLARE_API_TOKEN_WRITE", "") or secrets.get("CLOUDFLARE_API_TOKEN", "")
    if not (account and token):
        raise SystemExit("CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_TOKEN_WRITE are missing from .env")

    sha = _git("rev-parse", "HEAD") or "local"
    dirty = " +dirty" if _git("status", "--porcelain", "--", "web") else ""

    env = {**os.environ, **t["env"], "BUILD_ID": sha[:12]}
    if target == "portal":
        web_env = _dotenv(WEB / ".env")
        for key in ("VITE_SUPABASE_URL", "VITE_SUPABASE_ANON_KEY"):
            if not web_env.get(key):
                raise SystemExit(f"{key} is missing from web/.env")
            env[key] = web_env[key]
    print(f"Building {target} (npm run {t['script']}) at {sha[:12]}{dirty} ...", flush=True)
    _run(["npm", "run", t["script"]], env)

    cf = {**os.environ, "CLOUDFLARE_ACCOUNT_ID": account, "CLOUDFLARE_API_TOKEN": token,
          "CI": "true", "WRANGLER_SEND_METRICS": "false"}
    print(f"Deploying web/{t['out']} with {t['config']} ...", flush=True)
    _, out = _run([*WRANGLER, "deploy", "-c", t["config"]], cf)
    urls = sorted(set(re.findall(r"https://[a-z0-9.-]+\.(?:workers\.dev|yqmarketplace\.com)", out)))
    print("Deployed:", *(urls or [out[-600:]]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
