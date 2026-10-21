"""Read-only git questions. The ONLY module in scripts/ops that may import subprocess.

Every call is `git -C <root> <verb> ...` with the verb taken from VERBS, an argv list and no
shell; tests/test_ops_readonly.py asserts exactly that (and that no other module in the package
imports subprocess, os.system or os.popen).

"Is this checkout the live engine?" is answered on the ENGINE paths only: the Python under app/
and scripts/ and the two requirement files the API image is built from. SQL migration files, docs
and the ops skills themselves do not ship with an API deploy, so they never block a load. Nor do
the owner-side programs scripts/yq_*.py (the load's writer, the brief's sender, the secrets and
backup tools): nothing in the API runs them. One file of scripts/ops does ship with it and run
there (ENGINE_FILES_IN_OPS: the transfer pairing that /stock/flow runs). An exclude in a pathspec
list cannot be undone for one path inside it, so each engine question asks git twice, once per list.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

VERBS = ("rev-parse", "merge-base", "diff", "status", "cat-file", "ls-files")
ENGINE_PATHS = (
    ":(glob)app/**/*.py", ":(glob)scripts/**/*.py", "requirements.txt", "requirements.lock",
    ":(exclude)scripts/ops", ":(exclude,glob)scripts/yq_*.py",
)
ENGINE_FILES_IN_OPS = ("scripts/ops/pairing.py",)      # the one file inside scripts/ops that the API runs


def _git(root, verb: str, *args: str) -> subprocess.CompletedProcess:
    if verb not in VERBS:
        raise ValueError(f"gitinfo: '{verb}' is not an allowed git verb")
    return subprocess.run(["git", "-C", str(root), verb, *args], shell=False, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=60)


def _lines(cp: subprocess.CompletedProcess) -> list[str]:
    return [ln.strip() for ln in (cp.stdout or "").splitlines() if ln.strip()]


def head(root) -> str | None:
    cp = _git(root, "rev-parse", "HEAD")
    return cp.stdout.strip() if cp.returncode == 0 else None


def resolve(root, rev: str) -> str | None:
    """The full commit id for `rev` (a 12-character prefix from /health, a branch, HEAD); None when
    this clone does not have it."""
    cp = _git(root, "rev-parse", "--verify", "--quiet", rev + "^{commit}")
    return cp.stdout.strip() if cp.returncode == 0 and cp.stdout.strip() else None


def is_linked_worktree(root) -> bool:
    """True in a `git worktree add` checkout (its git dir is not the repository's common dir)."""
    a = _git(root, "rev-parse", "--git-dir")
    b = _git(root, "rev-parse", "--git-common-dir")
    if a.returncode != 0 or b.returncode != 0:
        return False

    def norm(p: str) -> Path:
        q = Path(p.strip())
        return (q if q.is_absolute() else Path(root) / q).resolve()
    return norm(a.stdout) != norm(b.stdout)


def is_ancestor(root, older: str, newer: str = "HEAD") -> bool:
    return _git(root, "merge-base", "--is-ancestor", older, newer).returncode == 0


def _engine_diff(root, *revs: str) -> list[str]:
    """`git diff --name-only <revs>` on ENGINE_PATHS and, a second time, on ENGINE_FILES_IN_OPS (the
    exclude of scripts/ops in the first list would drop them): the two answers merged, sorted, no repeats."""
    found = set(_lines(_git(root, "diff", "--name-only", *revs, "--", *ENGINE_PATHS)))
    found.update(_lines(_git(root, "diff", "--name-only", *revs, "--", *ENGINE_FILES_IN_OPS)))
    return sorted(found)


def engine_committed_diff(root, live: str, newer: str = "HEAD") -> list[str]:
    """Engine files that differ between the live commit and `newer` (committed work only)."""
    return _engine_diff(root, live, newer)


def engine_local_changes(root) -> list[str]:
    """Tracked engine files changed in this folder and not committed (staged or not)."""
    return _engine_diff(root, "HEAD")


def untracked(root, path: str = "scripts") -> list[str]:
    return _lines(_git(root, "ls-files", "--others", "--exclude-standard", "--", path))


def staged(root) -> list[str]:
    return _lines(_git(root, "diff", "--cached", "--name-only"))


def is_ignored_everywhere(root, paths: list[str]) -> list[str]:
    """Of `paths`, those git would track (not ignored): `ls-files --others --exclude-standard`
    lists untracked files that no ignore rule covers."""
    return _lines(_git(root, "ls-files", "--others", "--exclude-standard", "--", *paths)) if paths else []
