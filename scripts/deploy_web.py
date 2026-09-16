"""Deploy one of the two web builds to its Vercel project from the developer machine.

    python -m scripts.deploy_web market     # yq-marketplace  (VITE_APP=market is set on the project)
    python -m scripts.deploy_web portal     # yq-bahrain-ops

Both projects build the same `web/` directory; only the project (and its environment variables)
differs. The Vercel CLI links a directory to ONE project through web/.vercel/project.json, so this
script points the link at the requested project, runs a production deploy, and always restores the
previous link. Vercel builds the upload with the project's own env, so nothing local leaks in.

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
