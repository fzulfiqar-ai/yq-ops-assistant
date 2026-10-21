"""Phase 2b (r9): the release script and its runbook, pinned as text.

    python -m tests.test_r9_release_script

Same lightweight runner as tests/test_r8m_release_script.py (no pytest). Nothing here runs the release script, reaches a
database or calls a network: scripts/r9_release.ps1, docs/RELEASE.md and docs/MIGRATIONS.md are read as text. When a
PowerShell is on the PATH (powershell.exe on the owner's PC, pwsh on GitHub's runners) the script is PARSED by it
(ParseFile: nothing in it is executed), and a few of its helpers are run ALONE in a throw-away PowerShell, with invented
values and temporary folders: the GitHub read-token header, and the pure helpers (no git, no python, no network). Without
a PowerShell those tests print SKIP.

Pinned:
  * every part exists (check, backup, migrate, api, web, verify, migrate2, rollback): the ValidateSet, one region and
    one function per part, the dispatch; the header gives the release order; -RestoreTables belongs to the rollback;
  * every production command (apply_sql, yq_backup release, the Render deploy and rollback, the restore with --yes)
    sits after a YES question with no other production command in between; the question reads "Type YES to continue",
    and anything but YES stops a forward part (the rollback skips the piece);
  * every program the script runs is checked by its exit code (the first failure stops the part);
  * the migrations: r9_stock_moves, then r9_team_messages (migrate), r9_transfer_flow_drop alone (migrate2, only once
    verify passed for the API that is live, and only while /health shows it); --rehearse before each apply;
    audit_grants right after each apply; apply_sql nowhere else;
  * the backup is yq_backup release r9 (exit 2 = refused; its last line is the archive, which must exist), the gate
    snapshot beside it, the first backup kept as the rollback point; no plain db_backup --all any more;
  * the API part needs the migrations of this commit, writes the rollback target down, deploys this exact commit and
    polls /health; the web part (reads only) needs the API first, the cf-deploy run (both deploy steps) of a build at or
    after the release commit, the live build ids; verify runs moves portal (PASS), then the gate compare;
  * the rollback's order: v_transfer_flow back, the API, the two reverses held while the release API answers, a restore
    only with -RestoreTables (unpack outside OneDrive, verify, dry run, YES, --yes), the gate compare; the narration mask
    is said to stay;
  * the guards: a code worktree refused before anything else; branch main, level with origin/main, clean, the load LOCK
    absent and never deleted, CI green through the GitHub API (the GH_READ_TOKEN Bearer header on the GitHub calls
    alone, run here); from the web part on, a later commit only when nothing the API is built from changed;
  * no secret is echoed (.env only tested, and read once, for GH_READ_TOKEN; every line masked: the masker's patterns
    run here on invented secrets); Windows PowerShell 5.1 (ASCII only, no PowerShell 7 syntax);
  * the runbook (docs/RELEASE.md, Release 2b) and the ownership rows (docs/MIGRATIONS.md); nothing private;
  * the other 2b packages, once merged into this checkout, provide what the script calls (the six SQL files, yq_backup
    release / unpack --to, moves portal's PASS); until then each says it is not here yet.
"""
from __future__ import annotations

import base64
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
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


SCRIPT = ROOT / "scripts" / "r9_release.ps1"
RELEASE_MD = ROOT / "docs" / "RELEASE.md"
MIGRATIONS_MD = ROOT / "docs" / "MIGRATIONS.md"
CF_DEPLOY = ROOT / ".github" / "workflows" / "cf-deploy.yml"
PARTS = ("check", "backup", "migrate", "api", "web", "verify", "migrate2", "rollback")
RELEASE_ORDER = PARTS[:-1]          # the rollback runs only when something is wrong
SQL = {"R9_MOVES": r"scripts\r9_stock_moves_migration.sql", "R9_TEAM": r"scripts\r9_team_messages_migration.sql",
       "R9_DROP": r"scripts\r9_transfer_flow_drop_migration.sql",
       "R9_DROP_REV": r"scripts\r9_transfer_flow_drop_reverse.sql",
       "R9_TEAM_REV": r"scripts\r9_team_messages_reverse.sql", "R9_MOVES_REV": r"scripts\r9_stock_moves_reverse.sql"}
# the files of the other 2b packages (and of release A) that the check part looks for; they may not be merged here yet
PENDING = {f.replace("\\", "/") for f in SQL.values()} | {"scripts/ops/pairing.py", "scripts/ops/moves.py",
                                                          "scripts/ops/moves_portal.py", "app/transfer_audit.py",
                                                          "scripts/yq_team_post.py"}

# Production commands: each must sit after a YES question (Confirm-Step / Ask-Yes) with no other production command
# in between. A rehearsal counts: it runs on production, even though it always rolls back. The backup only reads
# production, but it is the step every later one depends on: it is asked for too.
PROD = {
    "apply_sql": re.compile(r"'scripts\.apply_sql'"),
    "backup": re.compile(r"'scripts\.yq_backup',\s*'release'"),
    "restore": re.compile(r"'scripts\.db_backup',\s*'--restore'.*'--yes'"),
    "render deploy": re.compile(r"'scripts\.render_deploy',\s*'deploy'"),
    "render rollback": re.compile(r"'scripts\.render_deploy',\s*'rollback'"),
}
PROD_COUNT = {"apply_sql": 4, "backup": 1, "restore": 1, "render deploy": 1, "render rollback": 1}
YES = re.compile(r"\b(Confirm-Step|Ask-Yes)\b")
# what a real secret looks like (the masker's own shapes); never in the script or the docs
SECRET_SHAPES = (r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.", r"\brnd_[A-Za-z0-9]{8,}",
                 r"\bsb_(secret|publishable)_[A-Za-z0-9_-]{8,}", r"\bgh[pousr]_[A-Za-z0-9]{16,}",
                 r"\bgithub_pat_[A-Za-z0-9_]{16,}", r"\bsk-[A-Za-z0-9_-]{16,}", r"postgres(?:ql)?://[^:/@\s]+:[^@\s]+@")


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
    assert len(pairs) >= 8, pairs

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


def _powershell() -> str | None:
    return shutil.which("powershell") or shutil.which("pwsh")


def _run_ps(exe: str, code: str, env: dict) -> dict:
    """Runs `code` in a throw-away PowerShell (-EncodedCommand), returns its KEY=value lines as a dict."""
    encoded = base64.b64encode(code.encode("utf-16-le")).decode("ascii")
    out = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded], env=env,
                         capture_output=True, text=True, timeout=180)
    assert out.returncode == 0, (out.stdout + out.stderr)[-1500:]
    return dict(ln.split("=", 1) for ln in out.stdout.splitlines() if "=" in ln)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. the parts and their order
# ═══════════════════════════════════════════════════════════════════════════════

@test("every part exists: the ValidateSet, one region and one function per part, the dispatch, the order in the header")
def _():
    src = _src()
    m = re.search(r"\[ValidateSet\(([^)]*)\)\]\s*\n\s*\[string\]\$Part,\s*\n\s*\[string\]\$RestoreTables = ''\s*\n\)", src)
    assert m, "the -Part ValidateSet, then -RestoreTables"
    assert tuple(re.findall(r"'([a-z0-9]+)'", m.group(1))) == PARTS
    run = _run_section(src)
    for p in PARTS:
        fn = "Invoke-Part" + p.capitalize()
        assert re.search(rf"^function {fn} \{{", _region(src, p), flags=re.M), fn
        assert re.search(rf"^\s*'{p}'\s+\{{ {fn} \}}$", run, flags=re.M), f"the dispatch of {p}"
    assert "Order: check -> backup -> migrate -> api -> web -> verify -> migrate2." in src
    heads = [src.index(f"scripts\\r9_release.ps1 -Part {p}\n") for p in PARTS]
    assert heads == sorted(heads), "the header lists the parts in the release order, the rollback last"
    assert "scripts\\r9_release.ps1 -Part rollback -RestoreTables <t1,t2>\n" in src
    # -RestoreTables is a word for the rollback alone; another part refuses it before anything else runs
    guard = "if ($RestoreTables -and $Part -ne 'rollback') { Stop-Part 2 "
    assert guard in run and run.index(guard) < run.index("switch ($Part)")
    rb = _region(src, "rollback")
    assert src.count("$RestoreTables") == rb.count("$RestoreTables") + 1 + run.count("$RestoreTables"), \
        "-RestoreTables is read in the rollback part and the run section only"


@test("every part starts with the guards; check, web and verify write nothing to production")
def _():
    src = _src()
    want = {"check": "Assert-Ready -NewTrain", "backup": "Assert-Ready -NewTrain", "web": "Assert-Ready -AfterApi",
            "verify": "Assert-Ready -AfterApi", "migrate2": "Assert-Ready -AfterApi", "rollback": "Assert-Ready -Lenient"}
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
    # the hand checks (GitHub unreadable, the portal build id not found) are YES too, never a default
    for p in ("check", "web"):
        for m in re.finditer(r"\$a = Read-Host '([^']+)'\n\s*if \(\(\[string\]\$a\)\.Trim\(\) -ne 'YES'\)", _region(src, p)):
            assert m.group(1).startswith("Type YES if "), m.group(1)


@test("every program the script runs is checked by its exit code; nothing starts a program around the two runners")
def _():
    src = _src()
    code = _code(src)
    calls = 0
    for idx, (no, line) in enumerate(code):
        if line.startswith("function ") or "return (Invoke-Native 'python'" in line:
            continue
        for _m in re.finditer(r"\((Invoke-Py|Invoke-Native) ", line):
            calls += 1
            if re.search(r"\(\((Invoke-Py|Invoke-Native) .*\) -(ne|eq) 0\)", line):
                continue
            # the one program whose exit 2 means something of its own (yq_backup: refused) is kept, then read twice
            m = re.match(r"\s*\$(\w+) = \((Invoke-Py|Invoke-Native) .*\)$", line)
            assert m, f"line {no}: unchecked: {line.strip()}"
            var = m.group(1)
            assert code[idx + 1][1].strip().startswith(f"if (${var} -eq 2) {{ Stop-Part 2 "), f"line {no}: exit 2"
            assert code[idx + 2][1].strip().startswith(f"if (${var} -ne 0) {{ Fail "), f"line {no}: any other exit"
        bare = _strip_strings(line)
        assert not re.search(r"(^|[\s;(&])(python|py|npx|wrangler|psql|curl|7z)(\.exe)?\b", bare), \
            f"line {no}: a program started outside Invoke-Py / Invoke-Native"
    assert calls >= 20, calls
    native = _function(src, "Invoke-Native")
    for need in ("$global:LASTEXITCODE = 0", "& $exe @argv 2>&1 | ForEach-Object", "$code = $LASTEXITCODE",
                 "return $code", "Get-Command $exe"):
        assert need in native, need
    assert re.search(r"\[void\]\(Invoke-Git @\('fetch', '-q', 'origin', 'main'\)\)\n\s*if \(\$script:GitCode -ne 0\) \{ Fail ", src)
    assert "$ErrorActionPreference = 'Continue'" in src and "$env:PYTHONUNBUFFERED = '1'" in src


# ═══════════════════════════════════════════════════════════════════════════════
# 3. the backup, the migrations, the API, the web, verify, migrate2, the rollback
# ═══════════════════════════════════════════════════════════════════════════════

@test("backup: yq_backup release r9 (r9-2 ... on a later attempt) behind one YES (exit 2 = refused), its last line the archive, the gate snapshot, the first backup kept")
def _():
    src = _src()
    b = _region(src, "backup")
    marks = [b.index(s) for s in ("if ([string]$st.checked -ne $script:HEAD) { Stop-Part 2 ",
                                  "if ([string]$st.backup_runs -match '^\\d+$') { $runs = [int]$st.backup_runs + 1 }",
                                  "$label = 'r9'", "if ($runs -gt 1) { $label = 'r9-' + $runs }",
                                  "Confirm-Step ", "Set-State 'backup_runs' $runs",
                                  "$code = (Invoke-Py @('-m', 'scripts.yq_backup', 'release', $label))",
                                  "if ($code -eq 2) { Stop-Part 2 ", "if ($code -ne 0) { Fail ",
                                  "$arc = Get-LastLine $script:LastOutput",
                                  "-not (Test-Path -LiteralPath $arc -PathType Leaf)) { Fail ",
                                  "'scripts.prod_gate', 'snapshot', '--label', 'pre-r9'",
                                  "Set-State 'release_sha' $script:HEAD")]
    assert marks == sorted(marks), marks
    # the attempt is counted after the YES, before yq_backup runs: a run refused or stopped half-way never blocks the next
    assert "run -Part backup again (the next run takes a new label)" in b
    # the first backup and before-snapshot of the release stay the rollback point; a later run is only "the latest"
    keep = b.index("if (-not $hasFirst) {")
    assert keep < b.index("Set-State 'backup_archive' $arc") and keep < b.index("Set-State 'gate_pre'")
    assert "Set-State 'backup_latest' $arc" in b and "$kept = [string]$st.backup_archive" in b
    # release backups are encrypted now (R1): no plain db_backup --all under business_data any more
    assert "'--all'" not in src and "business_data\\backups" not in src
    assert "Set-State 'checked' $script:HEAD" in _region(src, "check"), "check passed for this commit first"
    mig = _region(src, "migrate")
    assert "Stop-Part 2 'There is no backup of this release yet" in mig and "[string](Get-State).backup_archive" in mig


@test("migrations: r9_stock_moves then r9_team_messages, the drop alone in migrate2, the reverses the other way round; rehearse, apply, audit")
def _():
    src = _src()
    files = dict(re.findall(r"^\$(R9_[A-Z_]+?)\s*=\s*'([^']+)'", src, flags=re.M))
    assert files == SQL, files
    mig = _region(src, "migrate")
    assert mig.index("Invoke-Migration 'r9_stock_moves' $R9_MOVES ") < mig.index("Invoke-Migration 'r9_team_messages' $R9_TEAM ")
    assert "$R9_DROP" not in mig.replace("$R9_DROP_REV", ""), "the drop waits for migrate2"
    m2 = _region(src, "migrate2")
    assert m2.count("Invoke-Migration ") == 1 and "Invoke-Migration 'r9_transfer_flow_drop' $R9_DROP " in m2
    rb = _region(src, "rollback")
    order = [rb.index(s) for s in ("Undo-Migration 'r9_transfer_flow_drop' $R9_DROP_REV ",
                                   "Undo-Migration 'r9_team_messages' $R9_TEAM_REV ",
                                   "Undo-Migration 'r9_stock_moves' $R9_MOVES_REV ")]
    assert order == sorted(order), "the reverses in reverse order"
    for name in ("Invoke-Migration", "Undo-Migration"):
        body = _function(src, name)
        rehearse = body.index("'scripts.apply_sql', '--rehearse', $file")
        apply = body.index("'scripts.apply_sql', $file))")
        audit = body.index("'scripts.audit_grants'")
        assert rehearse < apply < audit, name
        assert body[apply:audit].count("Invoke-Py") == 1, f"{name}: nothing between the apply and the audit"
    assert "Set-State ('migrated_' + $label) $script:HEAD" in _function(src, "Invoke-Migration")
    outside = src
    for name in ("Invoke-Migration", "Undo-Migration"):
        outside = outside.replace(_function(src, name), "")
    assert "scripts.apply_sql" not in "\n".join(ln for _, ln in _code(outside))
    # apply_sql takes the flag on either side of the file (it drops '--rehearse' from its arguments)
    assert 'rehearse = "--rehearse" in argv' in _read(ROOT / "scripts" / "apply_sql.py")
    # the YES before each apply says what it changes; the narration mask is said to stay
    assert "The mask cannot be undone" in mig and "team_messages" in mig


@test("api: the migrations of this commit first, the live deploy written down, one YES, the deploy of this exact commit, /health")
def _():
    src = _src()
    api = _region(src, "api")
    need_mig = "[string]$st.migrated_r9_stock_moves -ne $script:HEAD -or [string]$st.migrated_r9_team_messages -ne $script:HEAD"
    marks = [api.index(s) for s in (need_mig, "'scripts.render_deploy', 'status'", "Set-State 'api_previous_deploy' $prev",
                                    "'scripts.render_deploy', 'deploy', '--commit', $script:HEAD, '--wait'",
                                    "Wait-Health $script:HEAD 8", "Set-State 'api_sha' $script:HEAD")]
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
    live = f"{'dep-d1abc2def3'}  {'live':16s} {'0123456789ab'[:7]}  merge: something  2026-10-22T10:00:00Z"
    old = f"{'dep-d0abc2def3'}  {'deactivated':16s} {'fedcba987654'[:7]}  older  2026-10-01T10:00:00Z"
    m = re.search(pat, live)
    assert m and m.group(1) == "dep-d1abc2def3" and m.group(2) == "0123456", m
    assert not re.search(pat, old)
    assert "pushes the web commit" in api, "the API part hands over to Claude's web push"


@test("web: reads only, after the API; the cf-deploy run of this commit (both deploy steps), a build at or after the release commit, the live builds")
def _():
    src = _src()
    web = _region(src, "web")
    marks = [web.index(s) for s in ("Test-SameCommit ([string]$st.api_sha) $rel)) { Stop-Part 2 ",
                                    "Find-Run $w 'cf-deploy'", "'/actions/workflows/cf-deploy.yml/runs?branch=main&per_page=1'",
                                    "'diff', '--quiet'", "'merge-base', '--is-ancestor', $rel, $sha",
                                    "[string]$cf.conclusion -ne 'success'", "Test-CfDeploySteps $cf",
                                    "Get-MarketBuild", "Find-PortalBuild $want", "Set-State 'web_live' $sha")]
    assert marks == sorted(marks), marks
    assert "not pushed yet" in web and "git pull --ff-only" in web
    steps = _function(src, "Test-CfDeploySteps")
    wf = _read(CF_DEPLOY)
    for prefix in ("Deploy the marketplace", "Deploy the portal"):
        assert f"-like '{prefix}*'" in steps
        assert re.search(rf"- name: {prefix} \(Worker yq-", wf), prefix
    assert "BUILD_ID: ${{ github.sha }}" in wf and "needs: ci-passed" in wf and "- 'web/**'" in wf
    vite = _read(ROOT / "web" / "vite.config.ts")
    assert "env.GITHUB_SHA?.slice(0, 12)" in vite and "fileName: 'version.json'" in vite and "build: buildId" in vite
    assert "$MARKET + '/version.json" in _function(src, "Get-MarketBuild")
    assert "$PORTAL + '/assets/' + $f" in _function(src, "Find-PortalBuild")
    # the hand check names the page the release changes (route /stock, the notice W1 added and 2b removes)
    assert "'/stock'" in _read(ROOT / "web" / "src" / "App.tsx").replace('"', "'") or 'path="stock"' in _read(ROOT / "web" / "src" / "App.tsx")
    assert "Being corrected" in web


@test("verify: the release API on /health, moves portal must print PASS, then the gate compare against the backup's snapshot")
def _():
    src = _src()
    v = _region(src, "verify")
    marks = [v.index(s) for s in ("Test-SameCommit $api ([string]$st.release_sha))) { Stop-Part 2 ",
                                  "if (-not (Test-SameCommit $hc $api)) { Fail ",
                                  "if ((Invoke-Py @('-m', 'scripts.ops', 'moves', 'portal')) -eq 0) { $movesOk = Test-MovesPass $script:LastOutput }",
                                  "'scripts.prod_gate', 'snapshot', '--label', 'post-r9'",
                                  "'scripts.prod_gate', 'compare', $pre",
                                  "if (-not $movesOk) { Fail ", "Set-State 'verified' $api")]
    assert marks == sorted(marks), marks
    assert src.count("Set-State 'verified'") == 1, "only verify marks the release verified"
    assert "$pre = [string]$st.gate_pre" in v and "'*_pre-r9.json'" in v and "'*_post-r9.json'" in v
    for need in ("Being corrected", "/command", "Copy button", "/yq-moves"):
        assert need in v, need


@test("migrate2: only once verify passed for the API that is live, and only while /health shows it")
def _():
    src = _src()
    m2 = _region(src, "migrate2")
    marks = [m2.index(s) for s in ("Test-SameCommit ([string]$st.verified) $api)",
                                   "Stop-Part 2 'The release is not verified yet", "$hc = Get-HealthCommit",
                                   "if (-not (Test-SameCommit $hc $api)) { Stop-Part 2 ",
                                   "Invoke-Migration 'r9_transfer_flow_drop' $R9_DROP ")]
    assert marks == sorted(marks), marks
    assert "Test-SameCommit $api ([string]$st.release_sha)" in m2


@test("rollback: v_transfer_flow back, the API, the two reverses (held while the release API answers or /health is silent), a restore only with -RestoreTables, the gate, the load note")
def _():
    src = _src()
    rb = _region(src, "rollback")
    marks = [rb.index(s) for s in ("Undo-Migration 'r9_transfer_flow_drop' $R9_DROP_REV ",
                                   "'scripts.render_deploy', 'status'",
                                   "Warning: v_transfer_flow is not back (R1 above)",
                                   "'scripts.render_deploy', 'rollback', '--deploy', $dep, '--wait'",
                                   "$hc = Get-HealthCommit\n  $hold = Get-ReverseHold $hc ([string]$st.api_sha)",
                                   "if ($hold) {",
                                   "The narration mask cannot be undone",
                                   "Undo-Migration 'r9_team_messages' $R9_TEAM_REV ", "Undo-Migration 'r9_stock_moves' $R9_MOVES_REV ",
                                   "if (-not $RestoreTables) {",
                                   "'scripts.yq_backup', 'unpack', $arc, '--to', $scratch",
                                   "$dir = Get-LastLine $script:LastOutput",
                                   "(Join-Path $dir 'manifest.json') -PathType Leaf)) { Fail ",
                                   "'scripts.db_backup', '--verify', $dir",
                                   "'scripts.db_backup', '--restore', $dir, '--tables', $tables))",
                                   "Ask-Yes ('RESTORE ",
                                   "'scripts.db_backup', '--restore', $dir, '--tables', $tables, '--yes'",
                                   "'post-r9-rollback'", "Set-State 'rolled_back'",
                                   "/yq-load now refuses", "Rollback finished for the pieces you said YES to.")]
    assert marks == sorted(marks), marks
    # both reverses run only in the else of the hold: never while the release API may still be live
    held = rb[rb.index("if ($hold) {"):]
    held = held[:held.index("} else {")]
    assert "Undo-Migration" not in held and "Say ('  ' + $hold) Yellow" in held
    assert rb.count("Undo-Migration 'r9_team_messages'") == 1 and rb.count("Undo-Migration 'r9_stock_moves'") == 1
    # the hold: silence first (a /health that does not answer proves nothing), then the release still live
    hold = _function(src, "Get-ReverseHold")
    assert hold.index("if (-not $health) {") < hold.index("if ($release -and (Test-SameCommit $health $release)) {") \
        < hold.index("return ''")
    assert "No /health answer: the reverses wait" in hold and "wait until the API is back" in hold
    # after an API rollback the load refuses (main still carries the 2b engine files): said at the end of the part
    assert "main still carries the 2b engine files" in rb
    # when migrate2 dropped v_transfer_flow and R1 did not put it back, the API rollback is preceded by a warning
    assert "if ([string]$st.migrated_r9_transfer_flow_drop -and -not $okD -and -not [string]$st.reversed_r9_transfer_flow_drop) {" in rb
    for need in ("'^dep-[A-Za-z0-9]+$'", "$st.api_previous_deploy", "-cnotmatch '^[a-z_][a-z0-9_]*(,[a-z_][a-z0-9_]*)*$'",
                 "Split-Path -Parent $arc", "[string]$st.backup_archive", "'_unpacked_'"):
        assert need in rb, need
    undo = _function(src, "Undo-Migration")
    assert "return $false" in undo and "refused" in undo and "return $true" in undo
    assert "wrangler" not in src.lower() and "deploy_cf" not in src, "the web is never deployed or switched by this script"
    relmd = _read(RELEASE_MD)
    assert "versions deploy <previous-version-id>@100% -c wrangler.market.jsonc -y" in relmd, "the web switch back, by hand"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. guards, the GitHub token, secrets, Windows PowerShell 5.1
# ═══════════════════════════════════════════════════════════════════════════════

@test("a code worktree is refused before anything else: before the log folder, the log, git or python")
def _():
    src = _src()
    guard = _function(src, "Assert-MainFolder")
    rx = re.search(r"\$ROOT -match '([^']+)'", guard).group(1)
    assert rx == r"\\\.claude\\worktrees\\", rx
    assert re.search(rx, r"C:\Users\someone\Desktop\YQ\.claude\worktrees\r9-release", flags=re.I)
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


@test("guards: branch main, level with origin/main, clean, LOCK absent and never deleted, CI green; after the API only a later commit with the API image unchanged")
def _():
    src = _src()
    ready = _function(src, "Assert-Ready")
    for need in ("'rev-parse', '--abbrev-ref', 'HEAD'", "-ne 'main'", "'fetch', '-q', 'origin', 'main'",
                 "'rev-parse', 'origin/main'", "'status', '--porcelain', '--untracked-files=no'",
                 "Test-Path -LiteralPath $LOCK", "/yq-load", "Never delete the lock",
                 "Test-Path -LiteralPath $ENVFILE -PathType Leaf", "git pull --ff-only", "release_sha",
                 "[switch]$NewTrain, [switch]$AfterApi, [switch]$Lenient"):
        assert need in ready, need
    after = ready[ready.index("} elseif ($AfterApi) {"):]
    after = after[:after.index("} else {")]
    for need in ("Test-LaterCommit $was", "@(Get-ApiImageChanges $was)", "$moved.Count -gt 0", "Stop-Part 2 "):
        assert need in after, need
    assert "'merge-base', '--is-ancestor', $base, 'HEAD'" in _function(src, "Test-LaterCommit")
    assert "'diff', '--name-only', $base, 'HEAD'" in _function(src, "Get-ApiImageChanges")
    # what the API image is built from: the Dockerfile copies the requirements, app/ and scripts/
    docker = _read(ROOT / "Dockerfile")
    for line in ("COPY requirements.txt requirements.lock ./", "COPY app ./app", "COPY scripts ./scripts"):
        assert line in docker, line
    sel = _function(src, "Select-ApiImagePaths")
    for need in ("'Dockerfile'", "'requirements.txt'", "'requirements.lock'", "StartsWith('app/')", "StartsWith('scripts/')"):
        assert need in sel, need
    assert re.search(r"^\$LOCK\s*=\s*Join-Path \$ROOT 'business_data\\ops\\LOCK'$", src, flags=re.M)
    assert "'/actions/runs?head_sha=' + $sha" in _function(src, "Get-Runs")
    assert "'https://api.github.com/repos/' + $REPO" in _function(src, "Get-GhJson")
    check = _region(src, "check")
    for need in ("Find-Run $w 'ci'", "[string]$ci.status -ne 'completed'", "[string]$ci.conclusion -ne 'success'",
                 "'DATABASE_URL'", "'RENDER_API_KEY'", "'SUPABASE_URL'", "'SUPABASE_KEY'",
                 "$R9_MOVES, $R9_MOVES_REV, $R9_TEAM, $R9_TEAM_REV, $R9_DROP, $R9_DROP_REV",
                 "'ls-files', '--error-unmatch'", "/yq-load refuses"):
        assert need in check, need
    bare = "\n".join(_strip_strings(ln) for _, ln in _code(src))
    raw = "\n".join(ln for _, ln in _code(src))
    for bad in ("Remove-Item", "Clear-Content", "Move-Item", "Rename-Item"):
        assert bad not in bare, bad
    for bad in ("'push'", "git push", "scripts.refresh", "scripts.load_supabase", "scripts.ingest", "scripts.yq_load_go",
                "--allow-newer", "'--force'", "Set-Content -LiteralPath $ENVFILE", "Out-File"):
        assert bad not in raw, bad


@test("the GitHub read token (C1): both GitHub calls carry Get-GhHeaders, our own sites never do; .env read there alone")
def _():
    src = _src()
    gh = _function(src, "Get-GhHeaders")
    for need in ("$env:GH_READ_TOKEN", "'Bearer '", "$h['Authorization']", "$script:Secrets += $tok", "return $script:GhHeaders",
                 "[System.IO.File]::ReadAllLines($ENVFILE)", "'^\\s*GH_READ_TOKEN\\s*=\\s*(.*)$'"):
        assert need in gh, need
    hits = [ln for _, ln in _code(src) if re.search(r"Invoke-(RestMethod|WebRequest)", ln) and ("api.github.com" in ln or "jobs_url" in ln)]
    assert len(hits) == 2 and all("-Headers (Get-GhHeaders)" in ln for ln in hits), hits
    for _, ln in _code(src):
        if "(Get-GhHeaders)" in ln and "Invoke-" in ln:
            assert "api.github.com" in ln or "jobs_url" in ln, "the token goes to GitHub alone, never to our own sites: " + ln.strip()
    for name in ("Get-HealthCommit", "Get-MarketBuild", "Find-PortalBuild"):
        assert "Headers" not in _function(src, name), name
    # the token reaches the same function that the release script of Market Intel v2 uses after C1
    assert "$script:GhHeaders = $null" in src


@test("the GitHub read token, run in PowerShell: no header while GH_READ_TOKEN is empty, a Bearer header from the window or else from .env, known to the masker; SKIP without PowerShell")
def _():
    exe = _powershell()
    if not exe:
        print("        SKIP: no PowerShell on this machine (the text pins above still apply)")
        return
    # Only Get-GhHeaders is run, in a throw-away PowerShell: nothing else of the release script, no network.
    code = ("$ErrorActionPreference = 'Stop'\r\n$ProgressPreference = 'SilentlyContinue'\r\n"
            "$ENVFILE = $env:YQ_T_ENVFILE\r\n$script:Secrets = @()\r\n$script:GhHeaders = $null\r\n"
            + _function(_src(), "Get-GhHeaders") + "\r\n"
            "$h = Get-GhHeaders\r\n"
            "[Console]::Out.WriteLine('KEYS=' + ((@($h.Keys) | Sort-Object) -join ','))\r\n"
            "[Console]::Out.WriteLine('AUTH=' + [string]$h['Authorization'])\r\n"
            "[Console]::Out.WriteLine('SECRETS=' + (@($script:Secrets) -join '|'))\r\n"
            "[Console]::Out.WriteLine('SAME=' + [object]::ReferenceEquals($h, (Get-GhHeaders)))\r\n")

    def run(window, env_lines):
        """(the variable of the PowerShell window or None, the lines of a .env or None for no .env) -> what it did."""
        with tempfile.TemporaryDirectory() as tmp:
            envfile = Path(tmp) / ".env"
            if env_lines is not None:
                envfile.write_text("\n".join(env_lines) + "\n", encoding="utf-8")
            env = {k: v for k, v in os.environ.items() if k != "GH_READ_TOKEN"}
            env["YQ_T_ENVFILE"] = str(envfile)
            if window is not None:
                env["GH_READ_TOKEN"] = window
            return _run_ps(exe, code, env)

    tok_window, tok_file = "tok-window-0123456789", "tok-file-0123456789"      # invented
    for window, lines in ((None, None), ("   ", None), (None, ["# a comment", "DATABASE_URL=x", "GH_READ_TOKEN_OTHER=" + tok_file]),
                          (None, ["GH_READ_TOKEN="])):
        got = run(window, lines)
        assert got["KEYS"] == "" and got["AUTH"] == "" and got["SECRETS"] == "", (window, lines, got)    # public: no header
    got = run(tok_window, None)
    assert got["KEYS"] == "Authorization" and got["AUTH"] == "Bearer " + tok_window and got["SECRETS"] == tok_window, got
    got = run(None, ["OTHER=1", '  GH_READ_TOKEN = "' + tok_file + '"  ', "LAST=2"])
    assert got["AUTH"] == "Bearer " + tok_file and got["SECRETS"] == tok_file, got      # quotes and spaces are stripped
    got = run(tok_window, ["GH_READ_TOKEN=" + tok_file])
    assert got["AUTH"] == "Bearer " + tok_window and got["SECRETS"] == tok_window and got["SAME"] == "True", got    # window first, once


@test("the pure helpers, run in PowerShell: what the API image is built from, moves portal's verdict, yq_backup's last line, the rollback's hold; SKIP without PowerShell")
def _():
    exe = _powershell()
    if not exe:
        print("        SKIP: no PowerShell on this machine (the text pins above still apply)")
        return
    src = _src()
    fns = "\r\n".join(_function(src, n) for n in ("Select-ApiImagePaths", "Test-MovesPass", "Get-LastLine",
                                                  "Test-SameCommit", "Get-ReverseHold"))
    code = ("$ErrorActionPreference = 'Stop'\r\n" + fns + "\r\n"
            "function Put([string]$k, $v) { [Console]::Out.WriteLine($k + '=' + [string]$v) }\r\n"
            "$A = 'a' * 40\r\n"
            # R3 waits while /health is silent or names the release; it may run once /health names another commit
            "Put 'H1' ('[' + (Get-ReverseHold '' $A) + ']')\r\n"
            "Put 'H2' ('[' + (Get-ReverseHold $A $A) + ']')\r\n"
            "Put 'H3' ('[' + (Get-ReverseHold 'aaaaaaa' $A) + ']')\r\n"
            "Put 'H4' ('[' + (Get-ReverseHold ('c' * 40) $A) + ']')\r\n"
            "Put 'H5' ('[' + (Get-ReverseHold ('c' * 40) '') + ']')\r\n"
            "Put 'H6' ('[' + (Get-ReverseHold '' '') + ']')\r\n"
            "Put 'API' (@(Select-ApiImagePaths @('web/src/pages/StockMovement.tsx', 'docs/STATUS.md', 'app/main.py', "
            "'scripts\\ops\\pairing.py', 'scripts/r9_release.ps1', 'scripts/ai_head/README.md', 'Dockerfile', "
            "'requirements.lock', 'requirements.txt', 'tests/test_x.py', '.github/workflows/ci.yml', '', '  ')) -join ',')\r\n"
            "Put 'API0' (@(Select-ApiImagePaths @('web/a.ts', 'docs/b.md', '')).Count)\r\n"
            "Put 'P1' (Test-MovesPass @('LAST LOAD FAILED at refresh (run 7): run /yq-load', '', 'moves portal: PASS (14 days)'))\r\n"
            "Put 'P2' (Test-MovesPass @('PASS'))\r\n"
            "Put 'P3' (Test-MovesPass @('moves portal: FAIL, 3 issues differ', 'PASS for 2 of 3'))\r\n"
            "Put 'P4' (Test-MovesPass @('PASSED', 'pass', 'nothing to say'))\r\n"
            "Put 'P5' (Test-MovesPass @())\r\n"
            "Put 'L1' (Get-LastLine @('Release backup 2026-10-22_pre-r9: every table', 'C:\\x\\release\\2026-10-22_pre-r9.7z', '', '   '))\r\n"
            "Put 'L2' (Get-LastLine @('  \"C:\\x y\\a.7z\"  '))\r\n"
            "Put 'L3' ('[' + (Get-LastLine @()) + ']')\r\n"
            "Put 'L4' (Get-LastLine @('  The restore: python -m scripts.db_backup --restore C:\\s\\u\\inner --tables <t1,t2,...>', 'C:\\s\\u\\inner'))\r\n")
    got = _run_ps(exe, code, dict(os.environ))
    assert got["API"] == "app/main.py,scripts/ops/pairing.py,Dockerfile,requirements.lock,requirements.txt", got["API"]
    assert got["API0"] == "0", got
    assert (got["P1"], got["P2"], got["P3"], got["P4"], got["P5"]) == ("True", "True", "False", "False", "False"), got
    assert got["L1"] == r"C:\x\release\2026-10-22_pre-r9.7z" and got["L2"] == r"C:\x y\a.7z" and got["L3"] == "[]", got
    assert got["L4"] == r"C:\s\u\inner", got
    assert got["H1"].startswith("[No /health answer: the reverses wait") and got["H6"] == got["H1"], got
    assert got["H2"].startswith("[The API still runs the release (" + "a" * 40 + ")") and got["H3"].startswith("[The API still runs"), got
    assert (got["H4"], got["H5"]) == ("[]", "[]"), got


@test("no secret is echoed: .env only tested and read once (GH_READ_TOKEN, never shown), every line masked; no secret in the files")
def _():
    src = _src()
    code = _code(src)
    for no, line in code:
        if "$ENVFILE" not in line or line.startswith("$ENVFILE"):
            continue
        assert ("Test-Path -LiteralPath $ENVFILE" in line
                or re.search(r"Select-String -LiteralPath \$ENVFILE .*-Quiet", line)
                or "[System.IO.File]::ReadAllLines($ENVFILE)" in line), f"line {no}: .env used another way: {line.strip()}"
    assert src.count("ReadAllLines($ENVFILE)") == 1, "GH_READ_TOKEN alone (no Cloudflare keys: this script never touches the web)"
    gh = _function(src, "Get-GhHeaders")
    for out in ("Say ", "Write-Host", "Write-Output", "Add-LogLine", "Write-Verbose", "Set-Content", "Add-Content"):
        assert out not in gh, out
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
    for path in (SCRIPT, RELEASE_MD, MIGRATIONS_MD):
        text = _read(path)
        for shape in SECRET_SHAPES:
            assert not re.search(shape, text), (path.name, shape)


@test("the masker hides a database password, a JWT, Render / Supabase / GitHub (classic and fine-grained) / OpenAI keys and key=value pairs; plain lines pass")
def _():
    mask = _masker(_src())
    fake = "x9" * 8                     # invented, 16 characters
    for s in ("postgresql://postgres.abcdefghij:" + fake + "@pooler.ci.invalid:5432/postgres",
              "jwt eyJ" + "a" * 12 + "." + "b" * 12 + "." + fake,
              "key rnd_" + fake, "sb_secret_" + fake, "sb_publishable_" + fake, "ghp_" + fake + fake, "sk-" + fake + fake,
              "token github_pat_" + fake + "_" + fake, "RENDER_API_KEY=" + fake, "GH_READ_TOKEN = " + fake,
              "SUPABASE_KEY: " + fake, "password=" + fake, "Authorization: Bearer " + fake):
        out = mask(s)
        assert fake not in out, (s, out)
    for s in ("Rehearsal OK: every statement ran; nothing was committed.", "moves portal: PASS",
              "dep-d1abc2def3  live             0123456  merge: 2b", "  ok RENDER_API_KEY", "  ok GH_READ_TOKEN (GitHub is read with it)",
              r"C:\Users\someone\AppData\Local\yq-backups\release\2026-10-22_pre-r9.7z",
              "Dry run OK (order: stock_movements). Re-run with --yes to apply"):
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
    exe = _powershell()
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


@test("the log and the state: business_data\\releases, every shown line and every program logged, what later parts need kept")
def _():
    src = _src()
    assert re.search(r"^\$RELDIR\s*=\s*Join-Path \$ROOT 'business_data\\releases'$", src, flags=re.M)
    assert re.search(r"^\$LOG\s*=\s*Join-Path \$RELDIR \(\$DAY \+ '_r9\.log'\)$", src, flags=re.M)
    assert re.search(r"^\$STATE\s*=\s*Join-Path \$RELDIR 'r9_state\.json'$", src, flags=re.M)
    native = _function(src, "Invoke-Native")
    for need in ("Add-LogLine ('RUN  ' + $exe", "Add-LogLine ('EXIT ' + $code)", "Add-LogLine ('  ' + $line)"):
        assert need in native, need
    assert "Add-Content -LiteralPath $LOG" in _function(src, "Add-LogLine")
    for key in ("checked", "release_sha", "backup_archive", "backup_latest", "gate_pre", "api_previous_deploy",
                "api_previous_commit", "api_sha", "api_live", "web_live", "verified", "rolled_back"):
        assert f"Set-State '{key}'" in src, key
    for key in ("migrated_r9_stock_moves", "migrated_r9_team_messages", "migrated_r9_transfer_flow_drop"):
        assert f"$st.{key}" in src, key


# ═══════════════════════════════════════════════════════════════════════════════
# 5. the runbook, the ownership rows, and the other 2b packages
# ═══════════════════════════════════════════════════════════════════════════════

@test("RELEASE.md: Release 2b with the commands in order, each part's time, the encrypted backup, the web after the API, the rollback; the private repository")
def _():
    md = _read(RELEASE_MD)
    sec = md[md.index("## Release 2b (r9)"):]
    nxt = re.search(r"^## (?!Release 2b)", sec[3:], flags=re.M)
    sec = sec[:nxt.start() + 3] if nxt else sec
    cmds = [sec.index(f"scripts\\r9_release.ps1 -Part {p}\n") for p in PARTS]
    assert cmds == sorted(cmds), "check, backup, migrate, api, web, verify, migrate2, then the rollback"
    for p in RELEASE_ORDER:
        assert re.search(rf"^\| {p} \|.*\| (about )?[0-9-]+ min", sec, flags=re.M), p
    for need in ("python -m scripts.yq_backup release r9", "%LOCALAPPDATA%\\yq-backups\\release", "encrypted",
                 "after the API", "git pull --ff-only", "Type YES to continue", "business_data\\releases\\<date>_r9.log",
                 "r9_state.json", "moves portal", "PASS", "-RestoreTables", "yq_backup unpack <archive> --to <scratch>",
                 "narration mask cannot be undone", "r9_transfer_flow_drop_reverse", "r9_team_messages_reverse",
                 "r9_stock_moves_reverse", "`/yq-load`", "`.claude\\worktrees\\...`"):
        assert need in sec, need
    assert sec.index("r9_transfer_flow_drop_reverse") < sec.index("render_deploy rollback") < sec.index("r9_team_messages_reverse") \
        < sec.index("r9_stock_moves_reverse"), "the rollback order"
    # the protocol: release backups are encrypted by yq_backup release; CI on a private repository; Render on API only
    for need in ("python -m scripts.yq_backup release <rN>", "GH_READ_TOKEN", "confirm CI by hand", "Auto-Deploy is No"):
        assert need in md, need
    # the review of 21-Oct: the reverses wait on a silent /health; the load refuses after an API rollback; row_hash
    flat = " ".join(sec.split())
    for need in ("or gives no answer at all, the script holds them",
                 "After an API rollback `/yq-load` refuses", "`main` still carries the 2b engine files",
                 "The mask covers the text only: `row_hash` beside it is a hash of the raw row",
                 "No view shows `row_hash` (the AI and the portal never see it): treat it as sensitive",
                 "a rehash and a reload"):
        assert need in flat, need
    assert "gone for good" not in md and "nowhere else" not in sec


@test("MIGRATIONS.md: who owns v_transfer_lines, v_salesman_stock_recon, team_messages and v_transfer_flow; the r9 section; keepalive_ping's readers")
def _():
    md = _read(MIGRATIONS_MD)
    table = md[md.index("## Who owns which view"):md.index("### The guard protects the file")]
    rows = {m.group(1): m.group(0) for m in re.finditer(r"^\| `([a-z_]+)`[^\n]*$", table, flags=re.M)}
    assert "r9_stock_moves_migration.sql" in rows["v_transfer_lines"]
    assert "r9_stock_moves_migration.sql" in rows["v_salesman_stock_recon"] and "stock_transfer_migration.sql" in rows["v_salesman_stock_recon"]
    assert "r9_team_messages_migration.sql" in rows["team_messages"]
    flow = rows["v_transfer_flow"]
    assert "r9_transfer_flow_drop_migration.sql" in flow and "r9_transfer_flow_drop_reverse.sql" in flow and "daily_ops2_migration.sql" in flow
    sec = md[md.index("## Release 2b (r9"):]
    for need in ("r9_stock_moves_migration.sql", "r9_team_messages_migration.sql", "r9_transfer_flow_drop_migration.sql",
                 "r9_transfer_flow_drop_reverse.sql", "r9_team_messages_reverse.sql", "r9_stock_moves_reverse.sql",
                 "moves portal", "scripts\\r9_release.ps1", "audit_grants", "narration mask"):
        assert need in sec, need
    # row_hash: made from the raw cells, so the masked numbers can be worked back from it; no view shows it
    flat = " ".join(sec.split())
    for need in ("**`row_hash` is sensitive.**", "the unmasked narration included", "a million candidates",
                 "No view shows `row_hash`", "the numbers are not gone from the database", "a rehash and a reload"):
        assert need in flat, need
    assert "gone for good" not in sec
    assert "the one thing the keepalive workflow and the yq-keepwarm Worker (publishable key, every 10 minutes) may read" \
        in " ".join(md.split())


@test("nothing private in the script or the docs: no name from the live data, no 3-decimal amount, no user folder")
def _():
    from scripts.ops import selftest
    names = selftest.load_denylist() or []
    for path in (SCRIPT, RELEASE_MD, MIGRATIONS_MD):
        text = _read(path)
        assert not selftest.scan_text(text, names), (path.name, selftest.scan_text(text, names))
        assert "fahmed" not in text.lower() and "c:\\users\\furqan" not in text.lower(), path.name
    assert "c:\\users\\" not in _src().lower(), "the script names no user folder"
    if not names:
        print("        note: no denylist on this machine: the names were not checked (the amounts were)")


@test("the other 2b packages, once merged here, provide what the script calls; until then each is named as not here yet")
def _():
    src = _src()
    # every file the check part looks for exists here, or belongs to a package not merged into this checkout yet
    check = _region(src, "check")
    listed = re.findall(r"'(scripts\\[A-Za-z0-9_\\.]+|app\\[A-Za-z0-9_\\.]+)'", check)
    listed = [f.replace("\\", "/") for f in listed] + [f.replace("\\", "/") for f in SQL.values()]
    missing = sorted(f for f in set(listed) if not (ROOT / f).is_file())
    assert not [f for f in missing if f not in PENDING], f"the check part looks for files no package makes: {missing}"
    if missing:
        print("        note: not in this checkout yet: " + ", ".join(missing))
    # the SQL files: all six together, each saying what the script says it does
    present = {k: ROOT / v.replace("\\", "/") for k, v in SQL.items() if (ROOT / v.replace("\\", "/")).is_file()}
    if present:
        assert len(present) == len(SQL), f"some r9 SQL files are here, not all: {sorted(present)}"
        body = {k: p.read_text(encoding="utf-8") for k, p in present.items()}
        assert "v_transfer_lines" in body["R9_MOVES"] and "v_salesman_stock_recon" in body["R9_MOVES"]
        assert "team_messages" in body["R9_TEAM"] and "team_messages" in body["R9_TEAM_REV"]
        assert "v_transfer_flow" in body["R9_DROP"] and "v_transfer_flow" in body["R9_DROP_REV"]
        # the narration mask, said as it is: the text is masked, row_hash (made from the raw cells) is sensitive
        for k in ("R9_MOVES", "R9_MOVES_REV"):
            prose = " ".join(line.lstrip("- ") for line in body[k].splitlines() if line.lstrip().startswith("--"))
            prose = " ".join(prose.split())
            assert "gone for good" not in prose and "holds them nowhere else" not in prose, k
            assert "row_hash" in prose and "sensitive" in prose and "a rehash and a reload" in prose, k
        for k, text in body.items():
            assert not re.search(r"^\s*(commit|end)\s*;", text, flags=re.I | re.M), f"{k}: its own COMMIT (apply_sql --rehearse refuses it)"
    # yq_backup (R1): release <label> ends with the archive, unpack <archive> --to <folder> with the folder it checked;
    # the labels the script gives (r9, then r9-2, r9-3 ...) are ones it accepts
    yb = _read(ROOT / "scripts" / "yq_backup.py")
    if re.search(r"add_parser\(\s*[\"']release[\"']", yb):
        assert re.search(r"add_parser\(\s*[\"']unpack[\"']", yb) and '"--to"' in yb, "yq_backup unpack <archive> --to <folder>"
        flat = " ".join(re.sub(r"\"\s+\"", "", yb).split())
        assert "the archive's path is the last line" in flat and "the folder is the last line" in flat
        lab = re.search(r"^LABEL = re\.compile\(r\"([^\"]+)\"\)", yb, flags=re.M)
        assert lab, "yq_backup's LABEL"
        for label in ("r9", "r9-2", "r9-13"):
            assert re.fullmatch(lab.group(1), label), label
    else:
        print("        note: yq_backup release / unpack are not in this checkout yet (R1)")
    # moves portal (release E): moves.py hands the word on to moves_portal.py, whose verdict line reads PASS or FAIL
    mv, mp = ROOT / "scripts" / "ops" / "moves.py", ROOT / "scripts" / "ops" / "moves_portal.py"
    if mp.is_file():
        assert "moves_portal" in _read(mv) and 'PORTAL_WORD = "portal"' in _read(mv), "moves.py dispatches moves portal"
        portal = _read(mp)
        assert '{VERDICT} PASS - ' in portal and '{VERDICT} FAIL - ' in portal and 'VERDICT = "moves portal:"' in portal, \
            "moves portal prints the words the script reads"
    else:
        print("        note: moves portal is not in this checkout yet (E-OPS)")


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
