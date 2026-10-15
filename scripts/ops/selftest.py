"""python -m scripts.ops selftest [--repo-clean] [--live] [--build-denylist]

  (no flag)        the engine's own health: config keys, the data root, the write guard (in-process,
                   no network)
  --repo-clean     run before every commit of this work (the repository may be public). FAILS when a
                   staged file is outside the list of paths this work may touch, or holds a salesman,
                   warehouse or customer name read from the live data, or an amount of one thousand
                   or more written with three decimals (the way a real BHD total looks).
  --live           the read-only proof against production: the session reports read-only and a
                   CREATE TEMP TABLE is refused by the database itself.
  --build-denylist (re)read the names from the loaded Focus files into business_data/ops/denylist.json
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from scripts.ops import config, gitinfo, paths

# Paths a commit of this work may touch (prefix match). Anything else staged fails --repo-clean.
ALLOWED_PATHS = (
    ".claude/skills/yq-", "scripts/ops/", "scripts/yq_load_go.py", "scripts/yq_send.py", "scripts/yq_secrets.py",
    "scripts/yq_backup.py", "scripts/yq_photos.py", "tests/test_ops_", "docs/OPS_SKILLS.md", "docs/FOCUS_EXPORT.md",
    "CLAUDE.md", ".gitignore", ".github/workflows/ci.yml", "requirements-dev.txt",
    # the continuation record and the docs the 15-Oct hardening pack brought current
    "docs/STATUS.md", "docs/BACKUPS.md", "docs/ROADMAP.md", "docs/CLAUDE.md", "docs/MARKET_INTEL.md",
    "docs/MARKET_INTEL_RELEASE.md", ".env.example",
)
# Names that are places or system words, already public in this repository: never treated as a leak.
GENERIC_NAMES = {
    "cash customer", "accessories warehouse", "accessories damage warehouse", "sim warehouse",
    "samples warehouse", "causeway", "yq roadshow", "yq bahrain", "yq bahrain w.l.l", "sub total",
    "grand total", "total", "credit", "cash", "credit note", "customer", "salesman", "warehouse",
}
AMOUNT_3DP = re.compile(r"(?<![\w.,])(?:\d{1,3}(?:,\d{3})+|\d{4,})\.\d{3}(?!\d)")
MIN_NAME_LEN = 4


def denylist_file() -> Path:
    return paths.ops_dir() / "denylist.json"


def _clean_name(raw) -> str | None:
    s = " ".join(str(raw or "").split())
    if not s or s.lower() in ("nan", "none"):
        return None
    return s


def build_denylist() -> list[str]:
    """Names of people and customers as the loaded Focus files spell them: warehouse names minus
    the ' - Acc WH' / ' - SIM WH' suffix, and customer accounts. Read from the newest published
    folder (business_data/Focus ERP <date>/); written to business_data/ops/denylist.json."""
    import pandas as pd

    base = paths.data_root() / "business_data"
    folders = sorted((p for p in base.glob("Focus ERP 20*") if p.is_dir()), key=lambda p: p.name)
    names: set[str] = set()
    if folders:
        newest = folders[-1]
        for f in newest.glob("*.xls*"):
            low = f.name.lower()
            if f.name.startswith("~$"):
                continue
            try:
                if "sales_day_book" in low:
                    df = pd.read_excel(f, header=5)
                    for col in ("Customer Account", "Warehouse Name"):
                        if col in df.columns:
                            names.update(n for n in (_clean_name(v) for v in df[col].dropna().unique()) if n)
                elif "summary_sales_register" in low:
                    df = pd.read_excel(f, header=5)
                    for col in df.columns:
                        if str(col).strip().lower() in ("customer", "customer account", "salesman"):
                            names.update(n for n in (_clean_name(v) for v in df[col].dropna().unique()) if n)
            except Exception:  # noqa: BLE001  (an unreadable file only shrinks the list)
                continue
    out: set[str] = set()
    for n in names:
        for part in (n, re.split(r"\s+-\s+", n)[0]):
            p = part.strip()
            if len(p) >= MIN_NAME_LEN and p.lower() not in GENERIC_NAMES and not p.replace(" ", "").isdigit():
                out.add(p)
    names_sorted = sorted(out, key=str.lower)
    paths.write_text(denylist_file(), json.dumps(names_sorted, ensure_ascii=False, indent=0) + "\n")
    return names_sorted


def load_denylist() -> list[str] | None:
    f = denylist_file()
    if not f.is_file():
        return None
    try:
        names = json.loads(f.read_text(encoding="utf-8"))
    except ValueError:
        return None
    # a list built before a word was added to GENERIC_NAMES still honours it
    return [str(n) for n in names if str(n).strip().lower() not in GENERIC_NAMES]


def scan_text(text: str, names: list[str]) -> list[str]:
    """Problems in one file's text: a name from the live data (whole words, any case) or a
    3-decimal amount of 1,000 or more."""
    problems: list[str] = []
    low = text.lower()
    for n in names:
        nl = n.lower()
        if nl in low and re.search(r"(?<![a-z0-9])" + re.escape(nl) + r"(?![a-z0-9])", low):
            problems.append(f"a name from the live data ({len(n)} letters, starts '{n[:2]}')")
            break
    m = AMOUNT_3DP.search(text)
    if m:
        problems.append(f"an amount with three decimals ({m.group(0)[:3]}...)")
    return problems


def repo_clean(root: Path | None = None) -> tuple[bool, list[str]]:
    root = root or paths.CODE_ROOT
    lines: list[str] = []
    ok = True
    staged = gitinfo.staged(root)
    if not staged:
        return True, ["nothing is staged"]
    names = load_denylist()
    if names is None:
        try:
            names = build_denylist()
        except Exception as exc:  # noqa: BLE001
            names = []
            lines.append(f"name check skipped: no denylist could be built ({str(exc)[:80]})")
    if not names:
        lines.append("name check has no names (no loaded Focus folder found under the data root)")
    for rel in staged:
        if not any(rel == p or rel.startswith(p) for p in ALLOWED_PATHS):
            ok = False
            lines.append(f"NOT ALLOWED  {rel}  (outside the paths this work may touch)")
            continue
        f = Path(root) / rel
        if not f.is_file():
            continue                      # a deletion
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for problem in scan_text(text, names):
            ok = False
            lines.append(f"LEAK  {rel}: {problem}")
    lines.append(f"{len(staged)} staged file(s) checked: " + ("clean" if ok else "NOT clean"))
    return ok, lines


def guard_check() -> tuple[bool, list[str]]:
    """The write guard, in-process and without a network: a write through the client raises and
    nothing reaches the transport."""
    import httpx
    from scripts.ops import db

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        return httpx.Response(200, json=[])

    was = db.installed()
    try:
        client = db.install(inner=httpx.MockTransport(handler), url="https://ci.invalid", key="selftest")
        refused = 0
        for attempt in (lambda: client.table("selftest").insert({"a": 1}).execute(),
                        lambda: client.table("selftest").delete().eq("a", 1).execute(),
                        lambda: client.rpc("not_a_read", {}).execute()):
            try:
                attempt()
            except db.ReadOnlyViolation:
                refused += 1
            except Exception:  # noqa: BLE001  (wrapped by the SDK: still refused if nothing was sent)
                refused += 1
        wrote = [s for s in seen if not s.startswith(("GET", "HEAD")) and "run_readonly_query" not in s]
        ok = refused == 3 and not wrote
        return ok, [f"write guard: {refused} of 3 writes refused, {len(wrote)} reached the transport"]
    finally:
        db.uninstall()
        if was:
            db.install()


def live_check() -> tuple[bool, list[str]]:
    from scripts.ops import db
    lines: list[str] = []
    with db.session() as s:
        ro = s.q("show transaction_read_only")[0]
        lines.append(f"session read-only: {list(ro.values())[0]}")
        refused = False
        try:
            with s.conn.cursor() as cur:
                cur.execute("create temp table ops_selftest (a int)")
        except Exception as exc:  # noqa: BLE001
            refused = "read-only" in str(exc).lower()
            lines.append("create temp table: refused by the database" if refused else f"unexpected: {str(exc)[:80]}")
        else:
            lines.append("create temp table: ACCEPTED (the session is not read-only)")
    return refused, lines


def add_arguments(parser) -> None:
    parser.add_argument("--repo-clean", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--build-denylist", action="store_true")


def run(args) -> dict:
    lines: list[str] = []
    ok = True
    if args.build_denylist:
        names = build_denylist()
        lines.append(f"denylist: {len(names)} names written to {denylist_file()}")
    if args.repo_clean:
        good, out = repo_clean()
        ok = ok and good
        lines += out
    if args.live:
        good, out = live_check()
        ok = ok and good
        lines += out
    if not (args.repo_clean or args.live or args.build_denylist):
        lines.append(f"code root: {paths.CODE_ROOT}")
        lines.append(f"data root: {paths.data_root()}" + (" (worktree: data read from the main folder)"
                                                           if paths.is_worktree() else ""))
        missing = config.missing_keys()
        lines.append("config: " + ("all keys present" if not missing else "MISSING " + ", ".join(missing)))
        ok = ok and not missing
        good, out = guard_check()
        ok = ok and good
        lines += out
    return {"ok": ok, "command": "selftest", "text": "\n".join(lines)}
