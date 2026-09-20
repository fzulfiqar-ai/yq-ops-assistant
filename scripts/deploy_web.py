"""Deploy web/vercel.json to the two Vercel projects - since 20-Sep-2026 that file only REDIRECTS.

    python -m scripts.deploy_web market     # yq-marketplace  -> 308 to https://yqmarketplace.com
    python -m scripts.deploy_web portal     # yq-bahrain-ops  -> 308 to https://ops.yqmarketplace.com

Cloudflare hosts both apps now (scripts/deploy_cf.py): Vercel Hobby hit its 100 GB/month transfer
cap and forbids commercial use, so the old *.vercel.app hostnames (in WhatsApp messages and printed
QR codes) live on as redirects that cost a few hundred bytes each. Run this only when the redirect
rules in web/vercel.json change. Both projects deploy the same `web/` directory; the Vercel CLI links
a directory to ONE project through web/.vercel/project.json, so this script points the link at the
requested project, runs a production deploy, and always restores the previous link.

Needs: `npx vercel whoami` logged in (team fzulfiqar-ai-s-projects). Set VERCEL_SCOPE to override.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
LINK = WEB / ".vercel" / "project.json"
PROJECTS = {"market": "yq-marketplace", "portal": "yq-bahrain-ops"}


def _vercel(*args: str, check: bool = True) -> str:
    scope = os.environ.get("VERCEL_SCOPE", "fzulfiqar-ai-s-projects")
    cmd = ["npx", "vercel", *args, "--scope", scope]
    proc = subprocess.run(cmd, cwd=WEB, text=True, capture_output=True, shell=(os.name == "nt"),
                          encoding="utf-8", errors="replace")
    out = (proc.stdout or "") + (proc.stderr or "")
    if check and proc.returncode != 0:
        print(out[-2000:])
        raise SystemExit(f"vercel {args[0]} failed ({proc.returncode})")
    return out


def main(argv: list[str]) -> int:
    target = (argv[0] if argv else "").strip().lower()
    if target not in PROJECTS:
        print(__doc__)
        return 1
    project = PROJECTS[target]
    previous = LINK.read_text(encoding="utf-8") if LINK.exists() else None
    try:
        _vercel("link", "--yes", "--project", project)
        print(f"Deploying web/ to {project} (production) …", flush=True)
        out = _vercel("deploy", "--prod", "--yes")
        urls = sorted(set(re.findall(r"https://[a-z0-9.-]+\.vercel\.app", out)))
        alias = [u for u in urls if u.startswith(f"https://{project}.")]
        print("Deployed:", *(alias or urls))
        build = re.search(r"Build Completed in /vercel/output \[(\d+s)\]", out)
        if build:
            print("Build time:", build.group(1))
    finally:
        if previous is not None:
            LINK.parent.mkdir(exist_ok=True)
            LINK.write_text(previous, encoding="utf-8")
        elif LINK.exists():
            LINK.unlink()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
