"""R8m / R8n: the Market Intel v2 release script and its runbook, pinned as text.

    python -m tests.test_r8m_release_script

Same lightweight runner as tests/test_r7a_focus.py (no pytest). Nothing here runs the release script, reaches a
database or calls a network: scripts/mi_v2_release.ps1 and docs/MARKET_INTEL_RELEASE.md are read as text. When a
PowerShell is on the PATH (powershell.exe on the owner's PC, pwsh on GitHub's runners) the script is only PARSED by it
(System.Management.Automation.Language.Parser.ParseFile: nothing in it is executed); without one that test prints SKIP.

Pinned:
  * every part exists (check, backup, migrate, api, web, import, verify, rollback): the ValidateSet, one region and one
    function per part, the dispatch; the header and the runbook give the release order;
  * every production command (apply_sql, the backup, the Render deploy and rollback, the importer's and the thumbnail
    backfill's --apply, the wrangler switch, the restore with --yes) sits after a YES question with no other production
    command in between; the question reads "Type YES to continue", and anything but YES stops a forward part;
  * every program the script runs is checked by its exit code (the first failure stops the part);
  * the migration order: r8m, then r8n, and the rollback r8n, then r8m; --rehearse before each apply; audit_grants right
    after each apply; apply_sql nowhere else;
  * the importer: its dry run first (the r8m columns required), the trial (--limit 5), the full run, then a second run
    that must create 0; the thumbnail backfill's dry run before its --apply; the importer's own words the script reads;
  * a code worktree is refused before anything else (before the log folder exists), and so is a folder whose .git is
    not a folder or that lacks business_data or the drop folder;
  * no secret is echoed: .env is only tested (Test-Path, Select-String -Quiet) and read in one place, for the Cloudflare
    keys of a web rollback, which are never printed; every screen and log line passes the masker, whose patterns are
    run here on invented secrets;
  * the guards: branch main, level with origin/main, CI green through the public GitHub API, the load LOCK absent and
    never deleted; nothing pushes, forces the r8m reverse, restores over newer rows or loads Focus files;
  * Windows PowerShell 5.1: ASCII only, param first, no &&, ||, ??, ?. or ternary, no "$name:" inside a double-quoted
    string, no parameter that only PowerShell 7 knows;
  * the API part polls /health until it reports the release commit; the web part reads the cf-deploy run (both deploy
    steps) and the live build ids; verify compares the gate snapshots; the rollback's order;
  * the runbook: the order and the commands, durations, the rollback, the after-release list; no 3-decimal amount, no
    secret; ci.yml runs this suite in the api job's Market Intel step, after the other r8m suites.
"""
from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Nothing here connects anywhere; pinned all the same, as in every suite (a worktree would otherwise find the
# project folder's .env, which is production).
os.environ["SUPABASE_URL"] = "https://ci.invalid"
os.environ["SUPABASE_KEY"] = "ci-key"
os.environ["DATABASE_URL"] = "postgresql://ci:ci@ci.invalid:5432/ci"

TESTS: list[tuple[str, object]] = []


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


SCRIPT = ROOT / "scripts" / "mi_v2_release.ps1"
RUNBOOK = ROOT / "docs" / "MARKET_INTEL_RELEASE.md"
CI = ROOT / ".github" / "workflows" / "ci.yml"
CF_DEPLOY = ROOT / ".github" / "workflows" / "cf-deploy.yml"
PARTS = ("check", "backup", "migrate", "api", "web", "import", "verify", "rollback")
RELEASE_ORDER = PARTS[:-1]          # the rollback runs only when something is wrong

# Production commands: each must sit after a YES question (Confirm-Step / Ask-Yes) with no other production command
# in between. A rehearsal counts: it runs on production, even though it always rolls back.
PROD = {
    "apply_sql": re.compile(r"'scripts\.apply_sql'"),
    "backup": re.compile(r"'scripts\.db_backup',\s*'--all'"),
    "restore": re.compile(r"'scripts\.db_backup',\s*'--restore'.*'--yes'"),
    "render deploy": re.compile(r"'scripts\.render_deploy',\s*'deploy'"),
    "render rollback": re.compile(r"'scripts\.render_deploy',\s*'rollback'"),
    "import --apply": re.compile(r"'scripts\.market_intel_import',\s*'--apply'"),
    "thumbs --apply": re.compile(r"'scripts\.market_intel_thumbs',\s*'--apply'"),
    "wrangler switch": re.compile(r"'versions',\s*'deploy'"),
}
PROD_COUNT = {"apply_sql": 4, "backup": 1, "restore": 1, "render deploy": 1, "render rollback": 1,
              "import --apply": 3, "thumbs --apply": 1, "wrangler switch": 1}
YES = re.compile(r"\b(Confirm-Step|Ask-Yes)\b")
# what a real secret looks like (the masker's own shapes); never in the script or the runbook
SECRET_SHAPES = (r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.", r"\brnd_[A-Za-z0-9]{8,}",
                 r"\bsb_(secret|publishable)_[A-Za-z0-9_-]{8,}", r"\bgh[pousr]_[A-Za-z0-9]{16,}",
                 r"\bsk-[A-Za-z0-9_-]{16,}", r"postgres(?:ql)?://[^:/@\s]+:[^@\s]+@")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _src() -> str:
    return _read(SCRIPT)


def _code(text: str) -> list[tuple[int, str]]:
    """(line number, line) of every line that is neither blank nor a whole-line # comment."""
    return [(i, ln) for i, ln in enumerate(text.split("\n"), 1) if ln.strip() and not ln.lstrip().startswith("#")]


def _region(text: str, name: str) -> str:
    start = text.index(f"#region part:{name}\n")
    return text[start:text.index(f"#endregion part:{name}\n", start)]


def _function(text: str, name: str) -> str:
    """`function <name>` up to the next top-level function, region marker or the run section."""
    m = re.search(rf"^function {re.escape(name)}\b.*$", text, flags=re.M)
    assert m, f"function {name} is missing"
    rest = text[m.end():]
    nxt = re.search(r"^(function |#region|#endregion|# -{20,})", rest, flags=re.M)
    return text[m.start():m.end() + (nxt.start() if nxt else len(rest))]


def _run_section(text: str) -> str:
    m = re.search(r"^# -{20,} run$", text, flags=re.M)
    assert m, "the run section marker is missing"
    return text[m.end():]


def _prod_kind(line: str) -> str | None:
    for kind, rx in PROD.items():
        if rx.search(line):
            return kind
    return None


def _strip_strings(line: str) -> str:
    """A code line without its quoted strings and its trailing comment ('' escapes ' inside '...')."""
    out, i, n = [], 0, len(line)
    while i < n:
        ch = line[i]
        if ch == "'":
            i += 1
            while i < n:
                if line[i] == "'" and i + 1 < n and line[i + 1] == "'":
                    i += 2
                    continue
                if line[i] == "'":
                    break
                i += 1
            i += 1
            out.append("''")
            continue
        if ch == '"':
            i += 1
            while i < n and line[i] != '"':
                i += 2 if line[i] == "`" else 1
            i += 1
            out.append('""')
            continue
        if ch == "#":
            break
        out.append(ch)
        i += 1
    return "".join(out)


def _masker(src: str):
    """The script's Hide-Secret as a Python function: its -replace pairs, in order (.NET $1 / ${1} -> \\g<1>)."""
    body = _function(src, "Hide-Secret")
    pairs = re.findall(r"-replace '((?:[^']|'')*)', '((?:[^']|'')*)'", body)
    assert len(pairs) >= 7, pairs

    def py_rep(rep: str) -> str:
        rep = rep.replace("''", "'")
        rep = re.sub(r"\$\{(\d+)\}", r"\\g<\1>", rep)
        return re.sub(r"\$(\d+)", r"\\g<\1>", rep)

    rules = [(re.compile(p.replace("''", "'")), py_rep(r)) for p, r in pairs]

    def mask(s: str) -> str:
        for rx, rep in rules:
            s = rx.sub(rep, s)
        return s
    return mask


# ═══════════════════════════════════════════════════════════════════════════════
# 1. the parts and their order
# ═══════════════════════════════════════════════════════════════════════════════

@test("every part exists: the ValidateSet, one region and one function per part, the dispatch, the order in the header")
def _():
    src = _src()
    m = re.search(r"\[ValidateSet\(([^)]*)\)\]\s*\n\s*\[string\]\$Part", src)
    assert m, "the -Part ValidateSet"
    assert tuple(re.findall(r"'([a-z]+)'", m.group(1))) == PARTS
    run = _run_section(src)
    for p in PARTS:
        fn = "Invoke-Part" + p.capitalize()
        assert re.search(rf"^function {fn} \{{", _region(src, p), flags=re.M), fn
        assert re.search(rf"^\s*'{p}'\s+\{{ {fn} \}}$", run, flags=re.M), f"the dispatch of {p}"
    assert "Order: check -> backup -> migrate -> api -> web -> import -> verify." in src
    heads = [src.index(f"scripts\\mi_v2_release.ps1 -Part {p}\n") for p in PARTS]
    assert heads == sorted(heads), "the header lists the parts in the release order, the rollback last"


@test("every part starts with the guards; check, web and verify write nothing to production")
def _():
    src = _src()
    want = {"check": "Assert-Ready -NewTrain", "backup": "Assert-Ready -NewTrain", "rollback": "Assert-Ready -Lenient"}
    for p in PARTS:
        body = _code(_function(src, "Invoke-Part" + p.capitalize()))
        assert body[1][1].strip() == want.get(p, "Assert-Ready"), (p, body[1][1])
    for p in ("check", "web", "verify"):
        assert not [ln for _, ln in _code(_region(src, p)) if _prod_kind(ln)], p


# ═══════════════════════════════════════════════════════════════════════════════
# 2. YES before every production step, and the first failure stops
# ═══════════════════════════════════════════════════════════════════════════════

@test("every production command sits after a YES question, with no other production command in between")
def _():
    lines = _code(_src())
    seen: dict[str, int] = {}
    for idx, (no, line) in enumerate(lines):
        kind = _prod_kind(line)
        if not kind:
            continue
        seen[kind] = seen.get(kind, 0) + 1
        if YES.search(line[:PROD[kind].search(line).start()]):
            continue
        for j in range(idx - 1, -1, -1):
            pno, prev = lines[j]
            assert not prev.startswith("function "), f"line {no} ({kind}): no YES question before it in its function"
            if YES.search(prev):
                break
            other = _prod_kind(prev)
            assert not other, f"line {no} ({kind}) follows line {pno} ({other}) with no YES question in between"
        else:
            raise AssertionError(f"line {no} ({kind}): no YES question before it")
    assert seen == PROD_COUNT, seen


@test("the question reads 'Type YES to continue'; only YES goes on; a forward part stops on anything else, the rollback skips")
def _():
    src = _src()
    ask = _function(src, "Ask-Yes")
    assert "Read-Host 'Type YES to continue'" in ask and "PRODUCTION STEP: " in ask
    assert re.search(r"\$ok = \(\[string\]\$a\)\.Trim\(\) -eq 'YES'", ask) and "return $ok" in ask
    assert src.count("'Type YES to continue'") == 1, "one question, in Ask-Yes"
    conf = _function(src, "Confirm-Step")
    assert re.search(r"if \(-not \(Ask-Yes \$what\)\) \{ Stop-Part 2 ", conf)
    for p in RELEASE_ORDER:
        assert "Ask-Yes" not in _region(src, p), f"{p}: a forward part stops (Confirm-Step), it never skips"
    rb = _region(src, "rollback")
    assert "Confirm-Step" not in rb and rb.count("Ask-Yes") >= 4
    assert "exit $code" in _function(src, "Stop-Part") and "Stop-Part 1 " in _function(src, "Fail")


@test("every program the script runs is checked by its exit code; nothing starts a program around the two runners")
def _():
    src = _src()
    calls = 0
    for no, line in _code(src):
        if line.startswith("function ") or "return (Invoke-Native 'python'" in line:
            continue
        for _m in re.finditer(r"\((Invoke-Py|Invoke-Native) ", line):
            calls += 1
            assert re.search(r"\(\((Invoke-Py|Invoke-Native) .*\) -ne 0\)", line), f"line {no}: unchecked: {line.strip()}"
        bare = _strip_strings(line)
        assert not re.search(r"(^|[\s;(&])(python|py|npx|wrangler|psql|curl)(\.exe)?\b", bare), \
            f"line {no}: a program started outside Invoke-Py / Invoke-Native"
    assert calls >= 30, calls
    native = _function(src, "Invoke-Native")
    for need in ("$global:LASTEXITCODE = 0", "& $exe @argv 2>&1 | ForEach-Object", "$code = $LASTEXITCODE",
                 "return $code", "Get-Command $exe"):
        assert need in native, need
    assert re.search(r"\[void\]\(Invoke-Git @\('fetch', '-q', 'origin', 'main'\)\)\n\s*if \(\$script:GitCode -ne 0\) \{ Fail ", src)
    assert "$ErrorActionPreference = 'Continue'" in src and "$env:PYTHONUNBUFFERED = '1'" in src


# ═══════════════════════════════════════════════════════════════════════════════
# 3. migrations, the import, the API, the web, verify, the rollback
# ═══════════════════════════════════════════════════════════════════════════════

@test("migrations: r8m then r8n, the rollback r8n then r8m; --rehearse before each apply; audit_grants right after; apply_sql nowhere else")
def _():
    src = _src()
    files = dict(re.findall(r"^\$(R8[MN](?:_REV)?)\s*=\s*'([^']+)'", src, flags=re.M))
    assert files == {"R8M": r"scripts\r8m_market_quality_migration.sql", "R8N": r"scripts\r8n_ai_market_signals_migration.sql",
                     "R8N_REV": r"scripts\r8n_ai_market_signals_reverse.sql",
                     "R8M_REV": r"scripts\r8m_market_quality_reverse.sql"}, files
    for f in files.values():
        assert (ROOT / f.replace("\\", "/")).is_file(), f
    mig = _region(src, "migrate")
    assert mig.index("Invoke-Migration 'r8m' $R8M\n") < mig.index("Invoke-Migration 'r8n' $R8N\n")
    rb = _region(src, "rollback")
    assert rb.index("Undo-Migration 'r8n' $R8N_REV ") < rb.index("Undo-Migration 'r8m' $R8M_REV ")
    # the order the files themselves allow: r8m after r7b, r8n after r7b + r7b_ai_head, either way round
    assert "Needs scripts/r7b_market_intel_migration.sql" in _read(ROOT / "scripts" / "r8m_market_quality_migration.sql")
    assert "Order with r8m_market_quality_migration.sql: either" in _read(ROOT / "scripts" / "r8n_ai_market_signals_migration.sql")
    for name in ("Invoke-Migration", "Undo-Migration"):
        body = _function(src, name)
        rehearse = body.index("'scripts.apply_sql', $file, '--rehearse'")
        apply = body.index("'scripts.apply_sql', $file))")
        audit = body.index("'scripts.audit_grants'")
        assert rehearse < apply < audit, name
        assert body[apply:audit].count("Invoke-Py") == 1, f"{name}: nothing between the apply and the audit"
    outside = src
    for name in ("Invoke-Migration", "Undo-Migration"):
        outside = outside.replace(_function(src, name), "")
    assert "scripts.apply_sql" not in "\n".join(ln for _, ln in _code(outside))
    assert "Stop-Part 2 'There is no backup of this release yet" in mig, "no migration without the backup"


@test("import: the dry run first (r8m required), the trial of 5, everything, a second run that must create 0; thumbnails dry run first")
def _():
    src = _src()
    imp = _region(src, "import")
    runs = [m.start() for m in re.finditer(r"'scripts\.market_intel_import'", imp)]
    assert len(runs) == 4, runs
    lines = [imp[r:imp.index("\n", r)] for r in runs]
    assert "--apply" not in lines[0], "the dry run comes first"
    assert "'--apply', '--limit', '5'" in lines[1]
    assert "'--apply'))" in lines[2] and "'--apply'))" in lines[3] and "--limit" not in lines[2] + lines[3]
    schema = imp.index("Test-OutputHas '^schema: the R7b tables with every r8m column'")
    assert runs[0] < schema < runs[1], "r8m must be applied before anything is copied"
    zero = imp.index("Test-OutputHas '^created: 0 sightings'")
    assert zero > runs[3] and "Test-OutputHas '^completed \\('" in imp[runs[3]:]
    th = [m.start() for m in re.finditer(r"'scripts\.market_intel_thumbs'", imp)]
    tl = [imp[r:imp.index("\n", r)] for r in th]
    assert len(th) == 3 and "--apply" not in tl[0] and "'--apply'" in tl[1] and "--apply" not in tl[2], tl
    assert runs[3] < th[0], "the thumbnails after the import"
    # the words the script reads are the importer's and the backfill's own
    importer = _read(ROOT / "scripts" / "market_intel_import.py")
    for said in ('say("schema: the R7b tables with every r8m column (client_observed_at, thumb_path, role, sha256)"',
                 'verb = "created" if self.apply else "to create"',
                 "say(f\"{verb}: {_n(len(self.created), 'sighting')}\"",
                 'return f"{n} {word}" + ("" if n == 1 else "s")',
                 "say(f\"{'completed' if self.apply else 'to complete'} (copied before, photos missing): \"",
                 "def in_worktree(root: Path) -> bool:"):
        assert said in importer, said
    thumbs = _read(ROOT / "scripts" / "market_intel_thumbs.py")
    assert "print(f\"Photos without a thumbnail: {out['missing_thumbs']}; flagged photos without a blurred copy: \"" in thumbs
    assert "Get-Count '^Photos without a thumbnail: (\\d+)'" in imp
    assert "Get-Count 'flagged photos without a blurred copy: (\\d+)'" in imp


@test("api: the live deploy written down first, one YES, the Render deploy of this exact commit, then /health until it reports it")
def _():
    src = _src()
    api = _region(src, "api")
    marks = [api.index(s) for s in ("'scripts.render_deploy', 'status'", "Set-State 'api_previous_deploy' $prev",
                                    "'scripts.render_deploy', 'deploy', '--commit', $script:HEAD, '--wait'",
                                    "Wait-Health $script:HEAD 8")]
    assert marks == sorted(marks), marks
    # a second try keeps the deploy that was live BEFORE the release as the rollback target
    assert api.index("if ([string]$st.api_previous_deploy) {") < api.index("Set-State 'api_previous_deploy' $prev")
    wait = _function(src, "Wait-Health")
    assert "Get-HealthCommit" in wait and "Start-Sleep -Seconds 10" in wait and "Test-SameCommit $c $want" in wait
    hc = _function(src, "Get-HealthCommit")
    assert "($API + '/health')" in hc and "$h.commit" in hc
    assert re.search(r"^\$API\s*=\s*'https://api\.yqmarketplace\.com'", src, flags=re.M)
    assert '@app.get("/health")' in _read(ROOT / "app" / "main.py") and '"commit": commit' in _read(ROOT / "app" / "main.py")
    rd = _read(ROOT / "scripts" / "render_deploy.py")
    assert 'd.add_argument("--commit"' in rd and 'r.add_argument("--deploy", required=True' in rd
    assert "f\"{d.get('id')}  {d.get('status'):16s} {str(c.get('id') or '')[:7]}" in rd
    # the script's pattern finds the live deploy in a line printed the way render_deploy prints it
    pat = re.search(r"\$l -match '([^']+)'", api).group(1)
    live = f"{'dep-d1abc2def3'}  {'live':16s} {'0123456789ab'[:7]}  merge: something  2026-10-14T10:00:00Z"
    old = f"{'dep-d0abc2def3'}  {'deactivated':16s} {'fedcba987654'[:7]}  older  2026-10-01T10:00:00Z"
    m = re.search(pat, live)
    assert m and m.group(1) == "dep-d1abc2def3" and m.group(2) == "0123456", m
    assert not re.search(pat, old)


@test("web: reads only; the cf-deploy run of this commit with both deploy steps, the marketplace version.json, the portal build id")
def _():
    src = _src()
    web = _region(src, "web")
    for need in ("Find-Run $w 'cf-deploy'", "Test-CfDeploySteps $cf", "Get-MarketBuild", "Find-PortalBuild $want",
                 "'/actions/workflows/cf-deploy.yml/runs?branch=main&per_page=1'", "'diff', '--quiet'",
                 "[string]$cf.conclusion -ne 'success'"):
        assert need in web, need
    steps = _function(src, "Test-CfDeploySteps")
    wf = _read(CF_DEPLOY)
    for prefix in ("Deploy the marketplace", "Deploy the portal"):
        assert f"-like '{prefix}*'" in steps
        assert re.search(rf"- name: {prefix} \(Worker yq-", wf), prefix
    assert "BUILD_ID: ${{ github.sha }}" in wf and "needs: ci-passed" in wf
    vite = _read(ROOT / "web" / "vite.config.ts")
    assert "env.GITHUB_SHA?.slice(0, 12)" in vite and "fileName: 'version.json'" in vite and "build: buildId" in vite
    assert "$MARKET + '/version.json" in _function(src, "Get-MarketBuild")
    assert "$PORTAL + '/assets/' + $f" in _function(src, "Find-PortalBuild")


@test("verify and backup: the before- and after-snapshots compared, /health on the release commit, the owner's portal checks")
def _():
    src = _src()
    v = _region(src, "verify")
    assert v.index("'scripts.prod_gate', 'snapshot', '--label', 'post-mi-v2'") < v.index("'scripts.prod_gate', 'compare', $pre")
    assert "Test-SameCommit $hc $script:HEAD" in v
    for need in ("Lists", "Spotted", "Cases", "Supplier reports", "Library", "release test"):
        assert need in v, need
    b = _region(src, "backup")
    for need in ("'scripts.db_backup', '--all', '--out', $rel", "'scripts.db_backup', '--verify', $rel",
                 "'scripts.prod_gate', 'snapshot', '--label', 'pre-mi-v2'", "'business_data\\backups\\' + $DAY + '_pre-mi-v2'",
                 "Get-Date -Format 'HHmmss'", "Set-State 'backup_dir' $rel", "Set-State 'gate_pre'"):
        assert need in b, need
    assert b.index("'--all', '--out'") < b.index("'--verify'") < b.index("'snapshot'")
    # the first backup and before-snapshot of the release stay the rollback point; a later run is only "the latest"
    keep = b.index("if (-not $hasFirst) {")
    assert keep < b.index("Set-State 'backup_dir' $rel") and keep < b.index("Set-State 'gate_pre'")
    assert "Set-State 'backup_latest' $rel" in b


@test("rollback: web, the API, the reverses (r8n, r8m), the restore (dry run first, never over newer rows), a gate compare")
def _():
    src = _src()
    rb = _region(src, "rollback")
    marks = [rb.index(s) for s in ("'versions', 'list'", "'versions', 'deploy'", "'scripts.render_deploy', 'rollback'",
                                   "Undo-Migration 'r8n'", "Undo-Migration 'r8m'",
                                   "'scripts.db_backup', '--restore', $bk, '--tables', $tables))",
                                   "'--tables', $tables, '--yes'", "'post-mi-v2-rollback'")]
    assert marks == sorted(marks), marks
    assert rb.index("wrangler.portal.jsonc") < rb.index("wrangler.market.jsonc"), "the portal first"
    for need in ("Use-CloudflareKeys", "($id + '@100%')", "'^dep-[A-Za-z0-9]+$'", "$st.api_previous_deploy",
                 "-cnotmatch '^[a-z_][a-z0-9_]*(,[a-z_][a-z0-9_]*)*$'",
                 "'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'"):
        assert need in rb, need
    undo = _function(src, "Undo-Migration")
    assert "return $false" in undo and "refused" in undo
    relmd = _read(ROOT / "docs" / "RELEASE.md")
    assert "versions deploy <previous-version-id>@100% -c wrangler.market.jsonc -y" in relmd


# ═══════════════════════════════════════════════════════════════════════════════
# 4. guards, secrets, Windows PowerShell 5.1
# ═══════════════════════════════════════════════════════════════════════════════

@test("a code worktree is refused before anything else: before the log folder, the log, git or python")
def _():
    src = _src()
    guard = _function(src, "Assert-MainFolder")
    rx = re.search(r"\$ROOT -match '([^']+)'", guard).group(1)
    assert rx == r"\\\.claude\\worktrees\\", rx
    assert re.search(rx, r"C:\Users\someone\Desktop\YQ\.claude\worktrees\mi-v2-release", flags=re.I)
    assert not re.search(rx, r"C:\Users\someone\Desktop\YQ", flags=re.I)
    assert "-match '/\\.claude/worktrees/'" in guard
    for need in ("(Join-Path $ROOT '.git') -PathType Container", "'business_data'", "'Uplaods'", "'Uploads'",
                 "exit 2", "REFUSED: "):
        assert need in guard, need
    assert "Add-LogLine" not in guard and "Say " not in guard and "Invoke-" not in guard, "a refusal writes nothing"
    run = _run_section(src)
    assert _code(run)[0][1].strip() == "Assert-MainFolder"
    order = [run.index(s) for s in ("Assert-MainFolder", "Set-Location -LiteralPath $ROOT", "New-Item -ItemType Directory",
                                    "Add-LogLine", "switch ($Part)")]
    assert order == sorted(order), order
    assert src.count("Assert-MainFolder") == 2, "defined once, called once"
    assert "$PSScriptRoot" in re.search(r"^\$ROOT\s*=\s*(.+)$", src, flags=re.M).group(1), "the script's own folder"


@test("guards: branch main, level with origin/main, clean tracked files, CI green via the public GitHub API, LOCK absent, never deleted")
def _():
    src = _src()
    ready = _function(src, "Assert-Ready")
    for need in ("'rev-parse', '--abbrev-ref', 'HEAD'", "-ne 'main'", "'fetch', '-q', 'origin', 'main'",
                 "'rev-parse', 'origin/main'", "'status', '--porcelain', '--untracked-files=no'",
                 "Test-Path -LiteralPath $LOCK", "/yq-load", "Never delete the lock",
                 "Test-Path -LiteralPath $ENVFILE -PathType Leaf", "git pull --ff-only", "release_sha"):
        assert need in ready, need
    assert re.search(r"^\$LOCK\s*=\s*Join-Path \$ROOT 'business_data\\ops\\LOCK'$", src, flags=re.M)
    assert "'/actions/runs?head_sha=' + $sha" in _function(src, "Get-Runs")
    assert "'https://api.github.com/repos/' + $REPO" in _function(src, "Get-GhJson")
    check = _region(src, "check")
    for need in ("Find-Run $w 'ci'", "[string]$ci.status -ne 'completed'", "[string]$ci.conclusion -ne 'success'",
                 "'DATABASE_URL'", "'RENDER_API_KEY'", "'SUPABASE_URL'", "'SUPABASE_KEY'", "$R8M, $R8M_REV, $R8N, $R8N_REV",
                 "'scripts\\market_intel_import.py'", "'scripts\\market_intel_thumbs.py'", "'ls-files', '--error-unmatch'"):
        assert need in check, need
    bare = "\n".join(_strip_strings(ln) for _, ln in _code(src))
    raw = "\n".join(ln for _, ln in _code(src))
    for bad in ("Remove-Item", "Clear-Content", "Move-Item", "Rename-Item"):
        assert bad not in bare, bad
    for bad in ("'push'", "git push", "scripts.refresh", "scripts.load_supabase", "scripts.ingest", "scripts.yq_load_go",
                "--allow-newer", "yq.r8m_drop", "'--force'", "Set-Content -LiteralPath $ENVFILE", "Out-File"):
        assert bad not in raw, bad


@test("no secret is echoed: .env only tested, read once for the Cloudflare keys (never shown), every line masked")
def _():
    src = _src()
    code = _code(src)
    for no, line in code:
        if "$ENVFILE" not in line or line.startswith("$ENVFILE"):
            continue
        assert ("Test-Path -LiteralPath $ENVFILE" in line
                or re.search(r"Select-String -LiteralPath \$ENVFILE .*-Quiet", line)
                or "[System.IO.File]::ReadAllLines($ENVFILE)" in line), f"line {no}: .env used another way: {line.strip()}"
    assert src.count("ReadAllLines($ENVFILE)") == 1
    keys = _function(src, "Use-CloudflareKeys")
    assert "ReadAllLines($ENVFILE)" in keys and "$script:Secrets += $tok" in keys
    for out in ("Say ", "Write-Host", "Write-Output", "Add-LogLine", "Write-Verbose", "Set-Content", "Add-Content"):
        assert out not in keys, out
    for bad in ("Get-Content $ENVFILE", "Get-Content -LiteralPath $ENVFILE", "Get-Content .env", "type .env", "cat .env"):
        assert bad not in src, bad
    for no, line in code:
        if re.search(r"\b(Say|Write-Host|Write-Output|Add-LogLine)\b", line):
            assert "$env:" not in line and "$tok" not in line and "$found" not in line, f"line {no}: {line.strip()}"
        for m in re.finditer(r"Write-Host\s+(.{0,24})", line):
            assert re.match(r"(\$t\b|'|\('  ' \+ \$line\)|\('REFUSED: ' \+ \$ROOT)", m.group(1)), f"line {no}: {m.group(1)}"
    assert "$t = Hide-Secret $text" in _function(src, "Say") and "Add-LogLine $t" in _function(src, "Say")
    assert "(Hide-Secret $text)" in _function(src, "Add-LogLine")
    assert "$line = Hide-Secret ([string]$_)" in _function(src, "Invoke-Native")
    assert "foreach ($v in $script:Secrets)" in _function(src, "Hide-Secret")
    for path in (SCRIPT, RUNBOOK):
        text = _read(path)
        for shape in SECRET_SHAPES:
            assert not re.search(shape, text), (path.name, shape)


@test("the masker hides a database password, a JWT, Render / Supabase / GitHub / OpenAI keys and key=value pairs; plain lines pass")
def _():
    mask = _masker(_src())
    fake = "x9" * 8                     # invented, 16 characters
    for s in ("postgresql://postgres.abcdefghij:" + fake + "@pooler.ci.invalid:5432/postgres",
              "jwt eyJ" + "a" * 12 + "." + "b" * 12 + "." + fake,
              "key rnd_" + fake, "sb_secret_" + fake, "sb_publishable_" + fake, "ghp_" + fake + fake, "sk-" + fake + fake,
              "RENDER_API_KEY=" + fake, "CLOUDFLARE_API_TOKEN_WRITE = " + fake, "SUPABASE_KEY: " + fake,
              "password=" + fake, "Authorization: Bearer " + fake):
        out = mask(s)
        assert fake not in out, (s, out)
    for s in ("Rehearsal OK: every statement ran; nothing was committed.", "created: 0 sightings",
              "dep-d1abc2def3  live             0123456  merge: Market Intel v2", "  ok RENDER_API_KEY",
              "order 12: token_hash unchanged", "schema: the R7b tables with every r8m column",
              "Photos without a thumbnail: 3; flagged photos without a blurred copy: 0."):
        assert mask(s) == s, (s, mask(s))


@test("Windows PowerShell 5.1: ASCII only, param first, no &&, ||, ??, ?. or ternary, no \"$name:\" in a double-quoted string")
def _():
    src = _src()
    odd = sorted({c for c in src if ord(c) > 126 or (ord(c) < 32 and c not in "\n\t")})
    assert not odd, f"ASCII only (5.1 reads a file without a BOM in the ANSI code page): {odd!r}"
    assert _code(src)[0][1].startswith("param("), "param() is the first statement"
    for no, line in _code(src):
        bare = _strip_strings(line)
        for tok in ("&&", "||", "??", "?."):
            assert tok not in bare, f"line {no}: {tok} is PowerShell 7 only"
        assert not re.search(r"\s\?\s", bare), f"line {no}: a ternary is PowerShell 7 only"
        for m in re.finditer(r'"([^"]*)"', line):
            assert not re.search(r"\$(?!env:|script:|global:)[A-Za-z_]\w*:", m.group(1)), f"line {no}: $name: in a string"
    for ps7 in ("-AsHashtable", "-SkipCertificateCheck", "-AdditionalChildPath", "ConvertFrom-Json -Depth", "-Parallel",
                "Get-Error", "$IsWindows"):
        assert ps7 not in src, ps7


@test("the script parses in PowerShell when one is on this machine (ParseFile only: nothing is run); SKIP otherwise")
def _():
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        print("        SKIP: no PowerShell on this machine (the text pins above still apply)")
        return
    probe = ("$t = $null; $e = $null; "
             "[void][System.Management.Automation.Language.Parser]::ParseFile($env:YQ_RELEASE_PS1, [ref]$t, [ref]$e); "
             "if ($e -and $e.Count -gt 0) { foreach ($x in $e) { [Console]::Out.WriteLine(('{0}:{1} {2}' -f "
             "$x.Extent.StartLineNumber, $x.Extent.StartColumnNumber, $x.Message)) }; exit 1 }; exit 0")
    env = {**os.environ, "YQ_RELEASE_PS1": str(SCRIPT)}
    out = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-Command", probe], env=env, capture_output=True,
                         text=True, timeout=180)
    assert out.returncode == 0, (out.stdout + out.stderr)[-2000:]


@test("the log and the state: business_data\\releases, every shown line and every program logged, the rollback target kept")
def _():
    src = _src()
    assert re.search(r"^\$RELDIR\s*=\s*Join-Path \$ROOT 'business_data\\releases'$", src, flags=re.M)
    assert re.search(r"^\$LOG\s*=\s*Join-Path \$RELDIR \(\$DAY \+ '_mi_v2\.log'\)$", src, flags=re.M)
    assert re.search(r"^\$STATE\s*=\s*Join-Path \$RELDIR 'mi_v2_state\.json'$", src, flags=re.M)
    native = _function(src, "Invoke-Native")
    for need in ("Add-LogLine ('RUN  ' + $exe", "Add-LogLine ('EXIT ' + $code)", "Add-LogLine ('  ' + $line)"):
        assert need in native, need
    assert "Add-Content -LiteralPath $LOG" in _function(src, "Add-LogLine")
    for key in ("release_sha", "backup_dir", "gate_pre", "api_previous_deploy", "api_previous_commit"):
        assert f"Set-State '{key}'" in src, key


# ═══════════════════════════════════════════════════════════════════════════════
# 5. the runbook and CI
# ═══════════════════════════════════════════════════════════════════════════════

@test("runbook: the commands in the release order, each part with its time, the rollback, the after-release list; nothing private")
def _():
    md = _read(RUNBOOK)
    cmds = [md.index(f"scripts\\mi_v2_release.ps1 -Part {p}\n") for p in PARTS]
    assert cmds == sorted(cmds), "check, backup, migrate, api, web, import, verify, then the rollback"
    for p in RELEASE_ORDER:
        assert re.search(rf"^### {p} \([^)]*minute", md, flags=re.M), p
    for need in ("## If something is wrong: the rollback", "git pull --ff-only", "Type YES to continue",
                 "business_data\\releases\\<date>_mi_v2.log", "**r8n first, then r8m**", "REFUSES while r8m holds any data",
                 "It must create 0", "`/yq-load`", "Never", "`.claude\\worktrees\\...`"):
        assert need in md, need
    after = md[md.index("## After the release"):]
    for need in ("Team & access", "Market Intel page", "Lists", "suppliers", "brands", "issue types",
                 "MARKET_INTEL_HOWTO.md", "/weekly-review", "Safety"):
        assert need in after, need
    from scripts.ops import selftest
    for path in (SCRIPT, RUNBOOK):
        text = _read(path)
        assert not selftest.scan_text(text, []), (path.name, selftest.scan_text(text, []))
        assert "fahmed" not in text.lower() and "c:\\users\\" not in text.lower(), path.name


@test("CI: the api job runs this suite in the Market Intel step, after the other r8m suites")
def _():
    ci = _read(CI)
    api = ci[ci.index("\n  api:"):ci.index("\n  web:")]
    at = api.index("python -m tests.test_r7b_market_intel\n")
    step = api[api.rindex("- name:", 0, at):]
    step = step[:step.index("\n      - name:")]
    me = step.index("python -m tests.test_r8m_release_script")
    for other in ("test_r8m_market_quality_replay", "test_r8m_market_intel_import", "test_r8m_supplier_reports",
                  "test_r8n_ai_market_signals", "test_r8m_market_quality\n"):
        assert step.index("python -m tests." + other) < me, other
    assert "SUPABASE_URL: https://ci.invalid" in step


def main() -> int:
    passed = failed = 0
    for name, fn in TESTS:
        try:
            fn()
            print(f"PASS  {name}")
            passed += 1
        except Exception:  # noqa: BLE001
            print(f"FAIL  {name}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
