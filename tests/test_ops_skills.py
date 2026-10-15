"""Ops skills: the two skills of phase 1, the root CLAUDE.md, the docs and the small repo files.

    python -m tests.test_ops_skills

Same lightweight runner as the other suites (no pytest). No database, no network, no .env:
SUPABASE_URL, SUPABASE_KEY and DATABASE_URL are pinned to ci.invalid before any import, and no module
of the portal (app.*) is imported here. The files read are text files of this checkout; what a test
writes goes to a temporary data root, and nothing under the real data root is read. Every sample in
the negative tests is invented, and amounts are built at run time so that no amount sits in this file.

The skills and the page act on what three programs print: the load check (scripts/ops/load_check.py),
the writer (scripts/yq_load_go.py) and the brief (scripts/ops/sales.py). So the words are taken from
the programs, never from a copy kept here: the load check and the status are RUN in memory against a
temporary data root; the writer is read as text (its constants, its options, the lines it says),
lends two pure helpers (its own permission matcher and the ways its command can be typed), and its
launcher is called only in a world where nothing can start (a temporary data root, a box that
answers No, no way to spawn a worker), to hear how it refuses, checks again and takes a No; the
brief is read as text (its window words and the counts and phrases it prints). A change on either
side of the seam fails here and not on the owner's screen.

Covered:
  * every .claude/skills/yq-*/SKILL.md: frontmatter at line 1 that PyYAML parses to a dict with no key
    given twice, strings in double quotes, the five keys these skills use and no other (none that runs
    code, none that changes who can start the skill), name = folder, description, argument-hint,
    allowed-tools = read-only `python -m scripts.ops <word> *` in both shells with its twin, none of
    which matches the load writer, scripts.refresh or scripts.load_supabase in any form a permission
    rule can take (the matcher follows the documented Claude Code rules and is itself tested; the
    writer's own, wider matcher agrees), disable-model-invocation on yq-load only, no command
    injection, under 70 lines, no skill that shadows the weekly-review or monthly-review commands;
    the commands a skill spells are registered and pre-approved by that skill; each skill carries
    its rules, sentence by sentence;
  * the words of the programs: the decision words of the load check's table, its options and the
    words he says for each, the go's exit codes and the lines it says (refused, check again and No
    by its own launcher), the five last lines of `load status` and their exit codes, the two lines
    that open a command after a load that did not finish, the brief's windows and the counts its
    definitions name, the five lines of the status;
  * an interrupted load against what the load check really prints for one, the line the check opens
    its own text with after a failed load, the go.cmd the check writes and names, what a new check
    does to the earlier checks of the same folder, the rehearsal as the page tells it;
  * the checks bite: broken samples of each kind are caught;
  * CLAUDE.md: short, "Where am I?" first, the worktree rule, the main folder named by what it holds,
    any other checkout, /yq-load, the skill map = the folders;
  * docs/OPS_SKILLS.md: the six ask rules exactly, as a JSON block that parses and that matches the
    writer and the two old loaders and no read-only command, the spellings they do not cover, and no
    promise beyond them; decisions, definitions, places; what the page tells him to do when a load
    is cut off, failed or refused; docs/FOCUS_EXPORT.md: the Ledger is a month-end export now, as on
    the last day of the month, and the rest of the file is still there;
  * nothing committed here holds an amount of 1,000 or more with three decimals, the word for
    sell-through, a long digit run or an e-mail address; the skills, CLAUDE.md and the page state no
    limit, target or amount (their only numbers are a few design numbers); the commit gate's own
    content scan (scripts.ops.selftest.scan_text) is clean on every file of this work with a name
    list and with none (an invented list in a temporary data root); .gitignore and the requirements
    files;
  * last of all: nothing here imported the portal, a .env reader, a database driver or a report
    parser.
"""
from __future__ import annotations

import argparse
import ast
import io
import json
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

os.environ["SUPABASE_URL"] = "https://ci.invalid"
os.environ["SUPABASE_KEY"] = "ci-key"
os.environ["DATABASE_URL"] = "postgresql://ci:ci@ci.invalid:5432/ci"

from scripts.ops import db  # noqa: E402,F401  (first, as every command does)

import yaml  # noqa: E402

from scripts.ops import __main__ as cli  # noqa: E402
from scripts.ops import alerts, config, load_check, loadplan, paths, selftest, status  # noqa: E402

TESTS: list[tuple[str, object]] = []

SKILLS = ROOT / ".claude" / "skills"
OPS = ROOT / "scripts" / "ops"
WRITER_FILE = ROOT / "scripts" / "yq_load_go.py"       # the only writer: read as text here
BRIEF_FILE = OPS / "sales.py"                          # the brief: read as text here (it imports the portal)
STOCK_FILE = OPS / "stock.py"                          # the stock command: read as text too, for the same reason
PAGE = ROOT / "docs" / "OPS_SKILLS.md"
BOM = b"\xef\xbb\xbf"
FRONT = re.compile(r"\A---\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)
TOOL_FORM = re.compile(r"^(Bash|PowerShell)\(python -m scripts\.ops ([a-z]+) \*\)$")
# The plan's five ("Skill features") and docs/OPS_SKILLS.md section 7: a skill here carries these and no other.
ALLOWED_KEYS = {"name", "description", "argument-hint", "allowed-tools", "disable-model-invocation"}
# Keys Claude Code also reads. Each changes who may start a skill, when it loads or what it runs with, so none
# is accepted here: user-invocable false on the owner-only skill would leave nobody able to start it.
OTHER_KNOWN_KEYS = {"when_to_use", "arguments", "user-invocable", "disallowed-tools", "model", "effort", "paths",
                    "shell", "metadata", "license", "compatibility"}
CODE_RUNNING_KEYS = {"hooks", "context", "agent", "background"}      # hooks run code; context/agent fork
OWNER_ONLY = {"yq-load"}
OWN_COMMAND = {"yq-stock": "stock"}                 # the command a skill is for, where it is not the brief
SHADOWS = ("weekly-review", "monthly-review")       # the commands a same-named skill would hide
DESCRIPTION_CAP = 1536                              # Claude Code cuts description + when_to_use here
MAX_LINES = 70                                      # a SKILL.md stays under this many lines
INJECTION = re.compile(r"!`|```!")                  # inline and fenced shell injection
SKILL_REF = re.compile(r"(?<!\w)/(yq-[a-z][a-z-]*)")

WRITER = "python -m scripts.yq_load_go --run 010126_0900 --token x"
REHEARSAL = "python -m scripts.yq_load_go --rehearse"                 # the same program: asked about, never pre-approved
WORKER = "python -m scripts.yq_load_go --worker 010126_0900"
REFRESH = "python -m scripts.refresh x"
LOAD_SUPABASE = "python -m scripts.load_supabase"
MUST_NOT_MATCH = (
    WRITER, REHEARSAL, REHEARSAL + " --no-box", WORKER, REFRESH, LOAD_SUPABASE,
    "python -m scripts.ingest x", "python -m scripts.apply_sql x", "python -m scripts.apply_migration x",
    "python -m scripts.db_backup --restore x", "python -m scripts.render_deploy", "python -m scripts.deploy_cf",
    "python -m scripts.deploy_web market", "python scripts/yq_load_go.py --worker 010126_0900", "git push",
    "python -m scripts.ops load check && " + WRITER,
    "python -m scripts.ops brief | " + REFRESH,
)
SIX_ASK_RULES = [
    "Bash(python -m scripts.yq_load_go*)", "PowerShell(python -m scripts.yq_load_go*)",
    "Bash(python -m scripts.refresh*)", "PowerShell(python -m scripts.refresh*)",
    "Bash(python -m scripts.load_supabase*)", "PowerShell(python -m scripts.load_supabase*)",
]
READ_ONLY_COMMANDS = ("python -m scripts.ops load check 071026", "python -m scripts.ops load status --wait",
                      "python -m scripts.ops brief", "python -m scripts.ops brief 7d", "python -m scripts.ops status")


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


# -- how Claude Code turns a permission rule into a matcher ---------------------------------------

SEPARATORS = re.compile(r"&&|\|\||;|\||&")      # a compound command is split; every part must match


def spec_regex(spec: str) -> str:
    """The expression Claude Code builds from the text inside Bash(...) or PowerShell(...): * is any
    text; a trailing ' *' also matches the bare command; the older 'prefix:*' form."""
    if spec.endswith(":*"):
        return re.escape(spec[:-2]) + r"(?: .*)?"
    bare_ok = spec.endswith(" *")
    body = spec[:-2] if bare_ok else spec
    return ".*".join(re.escape(part) for part in body.split("*")) + (r"(?: .*)?" if bare_ok else "")


def rule_matches(rule: str, command: str) -> bool:
    """Would the Claude Code rule cover the command? Tool(pattern), or the tool's bare name, which covers
    every command of that tool. Bash patterns are exact about letter case, PowerShell ones are not."""
    m = re.fullmatch(r"(Bash|PowerShell)(?:\((.*)\))?", rule.strip(), re.S)
    if not m:
        return False
    if m.group(2) is None:
        return True
    flags = re.S | (re.I if m.group(1) == "PowerShell" else 0)
    rx = re.compile(spec_regex(m.group(2)), flags)
    parts = [p.strip() for p in SEPARATORS.split(command) if p.strip()]
    return bool(parts) and all(rx.fullmatch(p) for p in parts)


# -- the checks on one SKILL.md (pure: they take text, so broken samples can be tried) ------------

class UniqueKeyLoader(yaml.SafeLoader):
    """PyYAML keeps the last of two equal keys without a word. Claude Code's parser calls a repeated key
    malformed YAML and then loads the skill with no fields at all, the owner-only flag included: so a key
    given twice is an error here, at any depth."""

    def construct_mapping(self, node, deep=False):
        seen: list = []
        for key_node, _value_node in node.value:
            key = self.construct_object(key_node, deep=True)
            if key in seen:
                raise yaml.constructor.ConstructorError(
                    None, None, f"the key '{key}' is given twice (Claude Code reads that as broken YAML and "
                                "loads the skill with no fields)", key_node.start_mark)
            seen.append(key)
        return super().construct_mapping(node, deep=deep)


def parse_skill(raw: bytes):
    """-> (problems, frontmatter dict or None, raw frontmatter block, body, whole text)"""
    problems: list[str] = []
    if raw.startswith(BOM):
        problems.append("the file starts with a byte-order mark: the frontmatter must begin at byte 0")
    text = raw.decode("utf-8-sig")
    m = FRONT.match(text)
    if not m:
        problems.append("no frontmatter block: line 1 must be --- and a closing --- must follow")
        return problems, None, "", text, text
    block, body = m.group(1), text[m.end():]
    try:
        fm = yaml.load(block, Loader=UniqueKeyLoader)       # SafeLoader's types, plus the repeated-key check
    except yaml.YAMLError as exc:
        problems.append("the frontmatter is not valid YAML: " + (str(exc).strip().splitlines() or ["?"])[0])
        return problems, None, block, body, text
    if not isinstance(fm, dict):
        problems.append("the frontmatter does not parse to a mapping")
        return problems, None, block, body, text
    return problems, fm, block, body, text


def quoted(value: str) -> bool:
    v = value.strip()
    return len(v) >= 2 and v[0] == '"' and v[-1] == '"'


def problems_frontmatter(folder: str, fm: dict, block: str) -> list[str]:
    out: list[str] = []
    for key in fm:
        if key in CODE_RUNNING_KEYS:
            out.append(f"the key '{key}' runs code or forks: not allowed")
        elif key in OTHER_KNOWN_KEYS:
            out.append(f"the key '{key}' is not allowed here: these skills use " + ", ".join(sorted(ALLOWED_KEYS))
                       + " and nothing that changes who starts a skill or what it runs with")
        elif key not in ALLOWED_KEYS:
            out.append(f"unknown key '{key}' (Claude Code ignores it without a word)")
    if fm.get("name") != folder:
        out.append(f"name {fm.get('name')!r} is not the folder name {folder!r}")
    if fm.get("name") in SHADOWS:
        out.append(f"a skill named {fm.get('name')} would hide the command of that name")
    desc = fm.get("description")
    if not isinstance(desc, str) or not desc.strip():
        out.append("description must be a non-empty string")
    elif len(desc) > DESCRIPTION_CAP:
        out.append(f"description is {len(desc)} characters (Claude Code cuts it at {DESCRIPTION_CAP})")
    if not isinstance(fm.get("argument-hint"), str):
        out.append("argument-hint must be a string")
    for line in block.splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        item = re.match(r"^\s+-\s+(.*)$", line)
        if item:
            if not quoted(item.group(1)):
                out.append("a list item is not in double quotes: " + item.group(1)[:50])
            continue
        pair = re.match(r"^([A-Za-z][\w-]*):\s*(.*)$", line)
        if pair and pair.group(2).strip() and pair.group(2).strip() not in ("true", "false") \
                and not quoted(pair.group(2)):
            out.append(f"the value of '{pair.group(1)}' is not in double quotes")
    return out


def problems_tools(fm: dict) -> list[str]:
    tools = fm.get("allowed-tools")
    if not isinstance(tools, list) or not tools or not all(isinstance(t, str) for t in tools):
        return ["allowed-tools must be a YAML list of strings"]
    out: list[str] = []
    if len(set(tools)) != len(tools):
        out.append("allowed-tools lists an entry twice")
    words: dict[str, set[str]] = {"Bash": set(), "PowerShell": set()}
    for entry in tools:
        m = TOOL_FORM.match(entry)
        if not m:
            out.append("allowed-tools entry is not Bash(python -m scripts.ops <word> *) or its PowerShell "
                       "form: " + entry)
            continue
        words[m.group(1)].add(m.group(2))
    for tool, other in (("Bash", "PowerShell"), ("PowerShell", "Bash")):
        for word in sorted(words[tool] - words[other]):
            out.append(f"the {tool} entry for '{word}' has no {other} twin")
    for entry in tools:
        for command in MUST_NOT_MATCH:
            if rule_matches(entry, command):
                out.append(f"allowed-tools entry {entry} matches: {command}")
    return out


def problems_flags(folder: str, fm: dict) -> list[str]:
    flag = fm.get("disable-model-invocation")
    if folder in OWNER_ONLY:
        return [] if flag is True else ["disable-model-invocation must be true on an owner-only skill"]
    if flag is None or flag is False:
        return []
    return ["disable-model-invocation is reserved for the owner-only skills: " + ", ".join(sorted(OWNER_ONLY))]


def problems_text(text: str) -> list[str]:
    out: list[str] = []
    n = len(text.splitlines())
    if n >= MAX_LINES:
        out.append(f"{n} lines: a SKILL.md stays under {MAX_LINES}")
    if INJECTION.search(text):
        out.append("shell-command injection (a bang followed by a backtick, or a fenced bang block)")
    if "python -m scripts.yq_load_go" in text:
        out.append("the load writer's command is typed in the skill: it is never typed by hand")
    return out


def check_skill(folder: str, raw: bytes) -> dict[str, list[str]]:
    fmt_problems, fm, block, _body, text = parse_skill(raw)
    out = {"format": fmt_problems, "frontmatter": [], "tools": [], "flags": [], "text": problems_text(text)}
    if fm is not None:
        out["frontmatter"] = problems_frontmatter(folder, fm, block)
        out["tools"] = problems_tools(fm)
        out["flags"] = problems_flags(folder, fm)
    return out


def skill_files() -> list[Path]:
    return sorted(SKILLS.glob("yq-*/SKILL.md"))


def skill_folders() -> set[str]:
    return {p.parent.name for p in skill_files()}


def real_checks() -> dict[str, dict[str, list[str]]]:
    return {p.parent.name: check_skill(p.parent.name, p.read_bytes()) for p in skill_files()}


def assert_clean(category: str) -> None:
    checks = real_checks()
    assert checks, "no yq skill found"
    bad = {name: c[category] for name, c in checks.items() if c[category]}
    assert not bad, bad


GOOD_TOOLS = ['"Bash(python -m scripts.ops brief *)"', '"PowerShell(python -m scripts.ops brief *)"']


def make_skill(name="yq-demo", description='"Shows a demo."', hint='"[x]"', tools=None, extra=(),
               body="Run it.\n") -> str:
    tools = list(GOOD_TOOLS) if tools is None else tools
    head = ["---", f'name: "{name}"', f"description: {description}", f"argument-hint: {hint}"]
    head += list(extra)
    if tools:
        head.append("allowed-tools:")
        head += [f"  - {t}" for t in tools]
    head.append("---")
    return "\n".join(head) + "\n" + body


def expect(category: str, needle: str, folder: str = "yq-demo", **over) -> None:
    text = over.pop("text", None)
    cats = check_skill(folder, (text if text is not None else make_skill(**over)).encode("utf-8"))
    assert any(needle in p for p in cats[category]), (category, needle, cats)


# -- text scans -----------------------------------------------------------------------------------

AMOUNT_3DP = re.compile(r"(?<![\w.,])(?:\d{1,3}(?:,\d{3})+|\d{4,})\.\d{3}(?!\d)")    # as scripts/ops/selftest.py
SELL_THROUGH = re.compile(r"sell[\s-]*through", re.I)
LONG_DIGITS = re.compile(r"\d{8,}")                    # a phone number or an ID number
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def scan_text(text: str, words: bool = True, address: bool = True) -> list[str]:
    """What must not be committed: an amount, a phone or ID number, an e-mail address, the banned word.
    (This file names the banned word and pins a placeholder database address, so it is scanned with
    words=False and address=False.)"""
    out: list[str] = []
    if AMOUNT_3DP.search(text):
        out.append("an amount of 1,000 or more with three decimals")
    if LONG_DIGITS.search(text):
        out.append("a run of eight or more digits (a phone or ID number?)")
    if address and EMAIL.search(text):
        out.append("an e-mail address")
    if words and SELL_THROUGH.search(text):
        out.append("the word for sell-through")
    return out


def money(whole: int, frac: str = "500") -> str:
    return f"{whole:,}" + "." + frac


def committed_texts() -> dict[str, str]:
    files = {"CLAUDE.md": ROOT / "CLAUDE.md", "docs/OPS_SKILLS.md": ROOT / "docs" / "OPS_SKILLS.md",
             "docs/FOCUS_EXPORT.md": ROOT / "docs" / "FOCUS_EXPORT.md"}
    for p in skill_files():
        files[f".claude/skills/{p.parent.name}/SKILL.md"] = p
    return {name: read(p) for name, p in files.items()}


# ── the permission matcher ─────────────────────────────────────────────────────────────────────

@test("the permission matcher follows the documented rules")
def _():
    ops = "Bash(python -m scripts.ops load *)"
    assert rule_matches(ops, "python -m scripts.ops load check 071026")
    assert rule_matches("Bash(python -m scripts.ops status *)", "python -m scripts.ops status")     # the bare command
    assert not rule_matches("Bash(python -m scripts.ops status *)", "python -m scripts.ops statusx")
    assert rule_matches("Bash(python -m scripts.ops*)", "python -m scripts.opsx y")                  # no space: also x
    assert rule_matches("Bash(python -m scripts.yq_load_go:*)", WRITER)                              # the older form
    assert not rule_matches("Bash(python -m scripts.ops:*)", "python -m scripts.opsx y")
    assert rule_matches("PowerShell(python -m scripts.ops load *)", "PYTHON -m scripts.ops LOAD check")
    assert not rule_matches(ops, "PYTHON -m scripts.ops LOAD check")                                 # Bash is exact
    # blanket rules would cover every writer, so the checks below would catch them
    for blanket in ("Bash(python *)", "Bash(python -m scripts.*)", "Bash(python*)"):
        for command in (WRITER, REFRESH, LOAD_SUPABASE):
            assert rule_matches(blanket, command), (blanket, command)
    # the writer is named so that no scripts.ops rule can match it, with or without the space
    assert not rule_matches("Bash(python -m scripts.ops*)", WRITER)
    assert not rule_matches("PowerShell(python -m scripts.ops *)", WRITER)
    # a compound command is split and every part must match
    assert not rule_matches(ops, "python -m scripts.ops load check; " + WRITER)
    assert not rule_matches(ops, "python -m scripts.ops load check | " + REFRESH)
    assert rule_matches(ops, "python -m scripts.ops load check && python -m scripts.ops load status --wait")
    assert not rule_matches("Read(python -m scripts.ops load *)", "python -m scripts.ops load check")
    # a tool's bare name covers every command of that tool
    assert rule_matches("Bash", WRITER) and rule_matches("PowerShell", REFRESH)
    assert not rule_matches("Read", WRITER) and not rule_matches("Bashful", WRITER)


@test("the writer command used here is the one the load check prints")
def _():
    assert loadplan.go_line("010126_0900", "x") == WRITER
    assert loadplan.WRITER_MODULE == "scripts.yq_load_go"
    assert WRITER_FILE.is_file() and not (OPS / "yq_load_go.py").exists()       # outside the read-only package
    for command in (REHEARSAL, WORKER):
        assert command.startswith(f"python -m {loadplan.WRITER_MODULE} "), command


# Every form a Claude Code permission rule can take, each written so that it would cover the writer.
FORMS_THAT_COVER_THE_WRITER = (
    "Bash", "PowerShell",                                           # a bare tool name
    "Bash(*)", "PowerShell(*)",                                     # everything, spelt out
    "Bash(python *)", "Bash(python*)", "Bash(python:*)",            # a trailing ' *', no space, the older 'prefix:*'
    "Bash(python -m scripts.*)", "Bash(python -m scripts.yq_load_go *)", "Bash(python -m scripts.yq_load_go*)",
    "Bash(python -m scripts.yq_load_go:*)", "Bash(" + WRITER + ")",                 # down to the exact command
    "Bash(* --token *)", "Bash(*yq_load_go*)", "Bash(python -m * --run *)",         # a star anywhere
    "PowerShell(PYTHON -M SCRIPTS.YQ_LOAD_GO *)",                   # PowerShell rules ignore letter case
)
# ... and rules for the read-only package, however wide: the writer is named so that none of them covers it
FORMS_FOR_THE_PACKAGE = ("Bash(python -m scripts.ops *)", "Bash(python -m scripts.ops*)",
                         "Bash(python -m scripts.ops:*)", "PowerShell(python -m scripts.ops *)")


def writer_spellings() -> list[str]:
    """Every way the writer's command can be typed, by the writer's own list (python, python.exe, py, python3
    and this interpreter, each with and without -X utf8), as a go, as a rehearsal and as the worker. The module
    is imported for two pure helpers; nothing in it is started."""
    from scripts import yq_load_go as go
    asked = f"--run {RUN} --token {SEAM_TOKEN}"
    typed = go.own_command_lines(RUN, SEAM_TOKEN)
    assert loadplan.go_line(RUN, SEAM_TOKEN) in typed and all(line.endswith(asked) for line in typed), typed
    return typed + [line[:-len(asked)] + tail for line in typed for tail in ("--rehearse", f"--worker {RUN}")]


@test("no allowed-tools entry can match the writer, in any form a permission rule can take")
def _():
    from scripts import yq_load_go as go                      # for its own matcher: it decides what the writer refuses
    good = [t.strip('"') for t in GOOD_TOOLS]
    assert not problems_tools({"allowed-tools": good})
    for form in FORMS_THAT_COVER_THE_WRITER:
        assert rule_matches(form, WRITER) and go.rule_matches(form, WRITER), form
        assert problems_tools({"allowed-tools": [form] + good}), f"the allowed-tools entry {form} would pass"
    for form in FORMS_FOR_THE_PACKAGE:
        assert not rule_matches(form, WRITER) and not go.rule_matches(form, WRITER), form
        assert problems_tools({"allowed-tools": [form] + good}), f"{form} is wider than one command and would pass"
    # the real skills, against every spelling of the writer, by this file's matcher and by the writer's own
    # (which leans wide: it ignores letter case for both tools)
    spellings = writer_spellings()
    assert len(spellings) >= 24, len(spellings)
    entries = 0
    for p in skill_files():
        _problems, fm, _block, _body, _text = parse_skill(p.read_bytes())
        for entry in fm["allowed-tools"]:
            entries += 1
            for command in spellings + [WRITER, REHEARSAL, WORKER, REFRESH, LOAD_SUPABASE]:
                assert not rule_matches(entry, command), (p.parent.name, entry, command)
                assert not go.rule_matches(entry, command), (p.parent.name, entry, command)
    assert entries >= 4
    # ... while the rule the page gives as its example of one that would let a load start is seen by both
    for command in spellings:
        assert go.rule_matches("Bash(python *)", command) or not command.startswith("python "), command
    assert rule_matches("Bash(python *)", loadplan.go_line(RUN, SEAM_TOKEN))


MY_FILES = (".claude/skills/yq-load/SKILL.md", ".claude/skills/yq-brief/SKILL.md", "CLAUDE.md", "docs/OPS_SKILLS.md",
            "docs/FOCUS_EXPORT.md", ".gitignore", "requirements-dev.txt", "tests/test_ops_skills.py",
            ".claude/skills/yq-stock/SKILL.md")


@test("every file of this work is on the list `selftest --repo-clean` allows to be committed")
def _():
    for rel in MY_FILES:
        assert (ROOT / rel).is_file(), rel
        assert any(rel == p or rel.startswith(p) for p in selftest.ALLOWED_PATHS), rel


def gate_problems(names: list[str]) -> dict[str, list[str]]:
    """What `selftest --repo-clean` would print as LEAK for each file of this work, given the gate's name list:
    the gate's own scan (scripts.ops.selftest.scan_text) on the text as the gate reads it and with the list as
    the gate loads it. Not a copy of the rule, and no allowance of its own."""
    out: dict[str, list[str]] = {}
    for rel in MY_FILES:
        problems = selftest.scan_text((ROOT / rel).read_text(encoding="utf-8", errors="replace"), names)
        if problems:
            out[rel] = problems
    return out


# Words that are system accounts or column words of Focus, not people or customers, and that stand in the files
# of this work ("credit notes", "cash customers"). A name list read from a day book can carry such a word as an
# account, so the gate sets them apart (scripts.ops.selftest.GENERIC_NAMES).
SYSTEM_WORDS = ("Credit", "Cash", "Total", "Customer", "Cash Customer")


@test("the commit gate's own content scan is clean on every file of this work, with a name list and with none")
def _():
    invented = ["Shop " + "One Trading", "Van " + "Seven - Acc WH"]     # joined here: neither name is in this file
    spelt = invented[0].lower()
    assert selftest.scan_text(f"an order for {spelt} today", invented), "the gate's name scan does not bite"
    assert not selftest.scan_text(f"a work{spelt} day", invented)                     # whole words only
    assert selftest.scan_text("total " + money(1234), []), "the gate's amount scan does not bite"
    for word in SYSTEM_WORDS:
        assert word.lower() in selftest.GENERIC_NAMES, f"the gate would take the Focus word '{word}' for a name"
    with temp_root() as root:
        # no name list on this PC yet: the amount rule alone, which needs no names
        assert not selftest.denylist_file().exists() and selftest.load_denylist() is None
        assert selftest.denylist_file().is_relative_to(root)                # the owner's own list is never read
        found = gate_problems([])
        assert not found, found
        # a name list in the form the gate writes, with system words among the invented names
        paths.write_text(selftest.denylist_file(), json.dumps(invented + list(SYSTEM_WORDS), indent=0) + "\n")
        names = selftest.load_denylist()
        assert names == invented, names                                     # the system words are never names
        found = gate_problems(names)
        assert not found, ("`python -m scripts.ops selftest --repo-clean` would refuse these files: " + str(found))
        # ... and with that list the gate still bites on a name, and no longer on the Focus words these files use
        assert selftest.scan_text(f"statement for {spelt}", names)
        assert not selftest.scan_text("sales returns, receipts and credit notes; cash customers; the total", names)
        # a list written before those words were set apart is read the same way (nothing has to be rebuilt)
        paths.write_text(selftest.denylist_file(), json.dumps(list(SYSTEM_WORDS)) + "\n")
        assert selftest.load_denylist() == [] and not gate_problems(selftest.load_denylist())
    month_end = section(read(ROOT / "docs" / "FOCUS_EXPORT.md"), "Month end")
    assert "credit notes" in month_end and not selftest.scan_text(month_end, invented)


# ── the words of the programs ───────────────────────────────────────────────────────────────────────
# /yq-load, /yq-brief and the page act on what three programs print. Nothing below keeps a copy of those
# words: the load check and the status are run in memory against a temporary data root, the writer and the
# brief are read as text. A change on either side of the seam fails here and not on the owner's screen.

RUN = "010126_0900"                                   # an invented run: its name spells 1 January, 09:00
OTHER_RUN = "020126_0900"
SEAM_TOKEN = "abcdefgh"                               # letters only, as the load plan prints a token
GO_CMD = "business_data/ops/loads/<run>/go.cmd"       # where the skill and the page say the go line is kept
NUMBER_WORDS = {3: "three", 4: "four", 5: "five"}     # the counts the page spells out
A_FLAG = re.compile(r"(?<![\w-])--[a-z][a-z-]*")


def flat(text: str) -> str:
    """Whole sentences, however the lines are wrapped."""
    return " ".join(text.split())


def page_part(number: int) -> str:
    """Section <number> of docs/OPS_SKILLS.md: from its '## <number>.' heading to the next '## ' heading."""
    m = re.search(rf"^## {number}\. .*?(?=^## |\Z)", read(PAGE), re.S | re.M)
    assert m, f"docs/OPS_SKILLS.md has no section {number}"
    return m.group(0)


def module_constants(path: Path) -> dict:
    """The literal constants a module assigns at its top level (NAME = literal; A, B = 1, 2), read from its
    source: the module is not imported."""
    out: dict = {}
    for node in ast.parse(read(path)).body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target, value = node.targets[0], node.value
        if isinstance(target, ast.Tuple) and isinstance(value, ast.Tuple) and len(target.elts) == len(value.elts):
            pairs = list(zip(target.elts, value.elts))
        else:
            pairs = [(target, value)]
        for name, literal in pairs:
            if not isinstance(name, ast.Name):
                continue
            try:
                out[name.id] = ast.literal_eval(literal)
            except (ValueError, TypeError):
                continue                                         # computed: not a constant this file can read
    return out


def source_options(path: Path) -> set[str]:
    """Every --option a module hands to add_argument, read from its source."""
    out: set[str] = set()
    for node in ast.walk(ast.parse(read(path))):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "add_argument"
                and node.args and isinstance(node.args[0], ast.Constant) and str(node.args[0].value).startswith("--")):
            out.add(str(node.args[0].value))
    return out


def function_strings(path: Path, name: str) -> set[str]:
    """Every string literal inside the function `name` of a module, read from its source."""
    for node in ast.walk(ast.parse(read(path))):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return {n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    raise AssertionError(f"{path.name} has no function {name}")


def compiled_patterns(path: Path) -> dict[str, str]:
    """NAME = re.compile("...") at the top level of a module -> the pattern's text."""
    out: dict[str, str] = {}
    for node in ast.parse(read(path)).body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute)
                and node.value.func.attr == "compile" and node.value.args
                and isinstance(node.value.args[0], ast.Constant) and isinstance(node.value.args[0].value, str)):
            out[node.targets[0].id] = node.value.args[0].value
    return out


def options_of(add_arguments) -> dict[str, tuple | None]:
    """--option -> the values it takes (None when it takes none), from a parser the command itself filled."""
    parser = argparse.ArgumentParser(add_help=False)
    add_arguments(parser)
    out: dict[str, tuple | None] = {}
    for action in parser._actions:  # noqa: SLF001  (argparse has no public list of its options)
        for flag in action.option_strings:
            if flag.startswith("--"):
                out[flag] = tuple(action.choices) if action.choices else None
    return out


class temp_root:
    """A temporary data root, with a config of round, invented values in force."""

    def __enter__(self) -> Path:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self._tmp.name).resolve()
        paths.set_data_root(root)
        fill = {"number": 1, "int": 1, "str": "x", "list": [], "dict": {}}
        config.use({key: fill[kind] for key, (kind, _about) in config.KEYS.items()})
        return root

    def __exit__(self, *exc):
        paths.set_data_root(None)
        config.use(None)
        self._tmp.cleanup()
        return False


def standin_deps(worktree: bool = False):
    """The load check's contacts with the outside, all local: the project folder on the live commit and one
    database project. The lock and the journals are NOT stand-ins: the real readers read the files the test
    wrote. Every check run here ends in its preflight (step 1), before a folder is read: at a lock, or, with
    `worktree`, at the answer that this is a code worktree."""

    class Standin(load_check.Deps):
        deadline_s = 0                                       # run in this thread

        def say(self, line):
            pass

        def linked_worktree(self):
            return worktree

        def live_commit(self):
            return "abc123abc123"

        def site_state(self, live):
            return {"state": "same"}

        def untracked(self):
            return []

        def addresses(self):
            return ("https://abcdefghijklmnopqrst.supabase.co",
                    "postgresql://postgres.abcdefghijklmnopqrst:x@pooler.ci.invalid:5432/postgres")

        def session(self):
            raise RuntimeError("no database in this test")

        def read(self, sql):
            raise RuntimeError("no database in this test")

        def read_params(self, sql, params):
            raise RuntimeError("no database in this test")

    return Standin()


OLD_BEAT = "2026-01-01T06:00:10+00:00"                # a heartbeat long past: the lock's worker is gone


def dead_lock(run: str, step: str) -> None:
    """A lock as a worker that is gone leaves it: no live process, an old heartbeat."""
    paths.write_text(paths.lock_file(), json.dumps({"run": run, "pid": 0, "step": step, "nonce": "x",
                                                    "started": "2026-01-01T06:00:00+00:00", "heartbeat": OLD_BEAT}))


EMPTY_PARTS = {"drop": {}, "staging": {}, "head": "", "live": "", "fingerprint": {}}     # the parts of a token


def checked(run: str, root: Path, token: str = SEAM_TOKEN) -> Path:
    """A check of the folder the run is named for that ended with a GO line, as the load check leaves it: the
    run's plan with its token, and its go.cmd. -> the go.cmd"""
    loadplan.write_plan({"run": run, "drop": str(root / "Uplaods" / run[:6]), "created_at": "2026-01-01T09:00:00+03:00",
                         "verdicts": [], "expected": {"lines": []}, "options": {}, "stops": [], "token": token,
                         "token_parts": dict(EMPTY_PARTS)})
    return loadplan.write_go_cmd(run, token, root)


def cut_off_load(root: Path) -> Path:
    """A load of RUN that was cut off at the refresh, as the writer leaves it: a lock whose process is gone and
    whose heartbeat is old, the run's plan, its go.cmd and a journal with no end. -> the go.cmd"""
    go_cmd = checked(RUN, root)
    loadplan.journal_append(RUN, "started", pid=0)
    loadplan.journal_append(RUN, "step_start", step="refresh")
    dead_lock(RUN, "refresh")
    return go_cmd


class NothingStarts:
    """The world the writer's launcher is handed in this file: the project folder is the temporary data root, the
    files and the database are as the table showed them, and nothing in it can start. The Yes/No box is never
    shown (it answers No) and no worker can be spawned."""
    rehearsal = False
    prepare = None
    settings_files: list = []

    def __init__(self, root: Path):
        self.root, self.staging = root, root / "business_data" / "staging" / "none"

    def linked_worktree(self) -> bool:
        return False

    def collect_parts(self, plan, with_fingerprint):
        return dict(EMPTY_PARTS)

    def foreign_writes(self, plan):                          # nothing else wrote to the database since the check
        return []

    def box_notes(self):                                     # no line about the backup or the e-mail in the box
        return []

    def box(self, title, text, timeout_s):
        return "no"

    def spawn(self, *args, **kwargs):
        raise RuntimeError("nothing is started in this test")

    def __getattr__(self, name):                             # anything else the launcher may come to ask for
        raise AssertionError(f"the launcher asked this test's world for '{name}': it has none, nothing starts here")


def go_says(run: str, token: str, root: Path) -> tuple[int, str]:
    """(the exit code, what it said) of the go for a run: the writer's own launcher, in a temporary data root and
    in a world where nothing can start. It ends at a refusal, at "check again" or at the No of the box."""
    from scripts import yq_load_go as go
    assert paths.data_root() == root and root != ROOT and not (root / ".env").exists(), root
    said: list[str] = []
    code = go.launch(run, token, deps=NothingStarts(root), say=said.append)
    assert not paths.lock_file().exists() or status.lock_state().get("state") != "running", "a lock was taken"
    return int(code), "\n".join(said)


def failed_load(run: str = RUN) -> None:
    """A load that was closed as failed after its refresh had begun, as the writer journals it."""
    loadplan.journal_append(run, "started", pid=0)
    loadplan.journal_append(run, "step_start", step="refresh")
    loadplan.journal_append(run, "failed", result="failed", step="refresh", written=True)


def check_args(**over) -> argparse.Namespace:
    base = {"action": "check", "drop": None, "pricebook": None, "with_profitability": False, "replace_ok": False,
            "partial_ok": False, "dev": False, "json": True, "run": None, "wait": False}
    base.update(over)
    return argparse.Namespace(**base)


def offline() -> argparse.Namespace:
    return argparse.Namespace(offline=True)


# -- the load check: its decisions and its options -----------------------------------------------

@test("the decision words on the page, in the skill and in the writer's box are the ones the load check prints")
def _():
    words = load_check.WORDS
    assert set(words) == set(loadplan.VERDICTS), sorted(set(words) ^ set(loadplan.VERDICTS))
    decisions = tuple(words[v] for v in loadplan.VERDICTS)                 # in the order of the plan's table
    assert len(set(decisions)) == len(decisions), decisions
    table = page_part(3)
    explained = tuple(re.findall(r"^\| \*\*([^*|]+)\*\* \|", table, re.M))
    assert explained == decisions, f"the page explains {explained}; the load check prints {decisions}"
    assert module_constants(WRITER_FILE)["VERDICT_WORDS"] == words         # the Windows box shows the same words
    skill = read(SKILLS / "yq-load" / "SKILL.md")
    assert f"A line marked {words['needs_decision']} states its default" in skill
    # ... which it does: the check words the default on the line itself, and the box says it once more
    assert "left out unless you say load" in read(OPS / "load_check.py")
    assert "(left out of this load)" in read(WRITER_FILE)
    row = next(ln for ln in table.splitlines() if ln.startswith(f"| **{words['needs_decision']}** |"))
    assert "The default is to leave the file out and load the rest: with no answer that is what happens." in row
    assert f"to answer a **{words['needs_decision']}** line first, click **No**" in flat(table)


# What he says for each option of the load check, as the skill and the page give it.
SPOKEN = {"load the price book": "--pricebook load", "leave the price book out": "--pricebook skip",
          "load profitability anyway": "--with-profitability", "replace on purpose": "--replace-ok",
          "partial on purpose": "--partial-ok"}
# Options that are not his to say: the developer's proof run, and the two of `load status`.
NOT_SPOKEN = ("--dev", "--run", "--wait")


@test("every option of the load check has its words in the skill and on the page, and the skill names no other")
def _():
    options = options_of(load_check.add_arguments)
    skill = read(SKILLS / "yq-load" / "SKILL.md")
    step1 = section(skill, "Step 1")
    spoken = {flag.split()[0] for flag in SPOKEN.values()}
    assert set(options) - set(NOT_SPOKEN) == spoken, (f"the load check takes {sorted(set(options) - set(NOT_SPOKEN))}; "
                                                      f"the skill has words for {sorted(spoken)}")
    assert set(NOT_SPOKEN) <= set(options), sorted(options)
    # the skill names those and the --wait of the status it follows with, and no option of the writer at all
    named = set(A_FLAG.findall(skill))
    assert named == spoken | {"--wait"}, sorted(named)
    assert not named & source_options(WRITER_FILE) - set(options), sorted(named & source_options(WRITER_FILE))
    assert "`python -m scripts.ops load status --wait`" in skill
    decisions = flat(page_part(3))
    for words, flag in SPOKEN.items():
        name, _, value = flag.partition(" ")
        takes = options[name]
        assert (value in takes) if value else takes is None, f"{flag}: the load check takes {name} {takes}"
        assert re.search('"' + re.escape(words) + r'" \((?:add )?`' + re.escape(flag) + r"`\)", step1), \
            f"Step 1 does not give: \"{words}\" ({flag})"
        assert f'"{words}"' in decisions, f"the page does not give the words: {words}"
    assert set(options["--pricebook"]) == {f.split()[1] for f in SPOKEN.values() if f.startswith("--pricebook ")}
    assert "Run Step 1 again with that option only when he says it" in step1
    # the Windows box names every option that changes what a Yes does, by the same flags, and the page says so
    boxed = {flag for _key, flag, _does in module_constants(WRITER_FILE)["OPTION_WORDS"]}
    assert boxed and boxed <= set(SPOKEN.values()), boxed
    assert "ON YOUR WORD (" in read(WRITER_FILE) and "a line that starts `ON YOUR WORD`" in flat(page_part(1))
    # ... and what the check flagged, each on a line of its own
    assert '"ATTENTION: "' in read(WRITER_FILE) and "on lines that start `ATTENTION`" in flat(page_part(1))


# -- the brief: its windows, its words, its counts ------------------------------------------------

@test("the windows and words of /yq-brief are the ones the brief reads, and the page's counts are the brief's own")
def _():
    consts, patterns = module_constants(BRIEF_FILE), compiled_patterns(BRIEF_FILE)
    typed = function_strings(BRIEF_FILE, "parse_spec")
    _problems, fm, _block, body, _text = parse_skill((SKILLS / "yq-brief" / "SKILL.md").read_bytes())
    hint = fm["argument-hint"]
    groups = re.findall(r"\[([^\]]+)\]", hint)
    windows = groups[0].split("|")
    fixed = [w for w in windows if w not in ("YYYY-MM", "A..B")]
    assert tuple(fixed) == tuple(consts["KEYWORDS"]), f"the hint has {fixed}; the brief reads {consts['KEYWORDS']}"
    assert windows[len(fixed):] == ["YYYY-MM", "A..B"], windows
    month, days = re.compile(patterns["_MONTH"]), re.compile(patterns["_RANGE"])
    forms = {"YYYY-MM": month, "YYYY-MM-DD..YYYY-MM-DD": days}             # a month, two days
    for form, reads in forms.items():
        sample = form.replace("YYYY", "2026").replace("MM", "01").replace("DD", "02")
        assert reads.match(sample), f"the brief does not read {sample} ({form})"
        assert not any(other.match(sample) for f, other in forms.items() if f != form), sample
    assert not any(rx.match(word) for word in fixed for rx in forms.values())
    assert "`A..B` is two days, written `YYYY-MM-DD..YYYY-MM-DD`" in body
    # the words that are no window: one salesman, the rows of a window's tables, the same brief again
    for word, group in (("rep:", "rep:<name>"), ("top:", "top:N"), ("again", "again")):
        assert word in typed, f"the brief no longer reads the word {word}"
        assert group in groups[1:], f"the argument hint lacks [{group}]"
    usage = (ast.get_docstring(ast.parse(read(BRIEF_FILE))) or "").splitlines()[0]
    assert usage.startswith("python -m scripts.ops brief [window] "), usage
    assert re.findall(r"\[([^\]]+)\]", usage)[1:] == groups[1:], f"the brief's own usage line: {usage}; the hint: {hint}"
    assert A_FLAG.findall(body) == [] and {"--again", "--top"} <= source_options(BRIEF_FILE)   # the skill uses the words
    assert "`rep:<name>` for one salesman" in body and "`top:N` for the number of rows in a window's tables" in body
    assert "rows in the account and product tables of a window" in read(BRIEF_FILE)      # what top:N is, in its words
    assert "run it again with `again` added" in body
    # the page lists the same windows in the same order, and says what the two week words mean
    intro = flat(page_part(1))
    listed = ", ".join(f"`{w}`" for w in fixed) + ", a month as `YYYY-MM`, or two days as `YYYY-MM-DD..YYYY-MM-DD`"
    assert listed in intro, f"the page does not list: {listed}"
    assert "`rep:<name>`" in intro and "`top:<N>`" in intro and "`/yq-brief again`" in intro
    titles = function_strings(BRIEF_FILE, "resolve_window")
    assert {"This week so far", "Last full week"} <= titles, sorted(titles)
    assert "`week` is this week so far; `last-week` is the last full week" in intro
    # with no new data the brief answers in two lines, the data date and what is waiting
    src = read(BRIEF_FILE)
    for said in ("nothing new since the last brief", "Waiting: ", "/yq-brief again"):
        assert said in src, f"the brief no longer says: {said}"
    assert "two lines (the data date and what is waiting)" in body and "it prints two lines" in intro
    # the counts the page names are the brief's own
    defs = flat(page_part(4))
    five = NUMBER_WORDS[consts["DO_TODAY"]]
    for needle in (f"the average business day of the last {consts['AVERAGE_DAYS']} days",
                   f"worked out from the {consts['BASELINE_WEEKS']} full weeks before",
                   f"the average of the {NUMBER_WORDS[consts['AVERAGE_WEEKS']]} full weeks before",
                   f"the last or the first {NUMBER_WORDS[consts['MONTH_EDGE_DAYS']]} business days of a month",
                   f"At most {five} open items of the action list"):
        assert needle in defs, f"section 4 does not say: {needle}"
    assert f"up to {five} things to do today" in fm["description"] and f"up to {five} things to do today" in intro
    assert "in the last 60 days" in src and "still asked for in the last 60 days" in defs
    # the action list's own rule for an item he has explained (scripts/ops/alerts.py), and where its full list is
    assert alerts.REOPEN_GROWTH * 4 == 1 and "until it has grown by more than a quarter" in defs
    assert "has become more pressing, or names a voucher or an invoice that was not there" in defs
    assert "more:" in read(OPS / "alerts.py") and "when more are open, the screen says how many" in defs
    assert "The full list, and marking an item done or explained, come with a later command" in defs
    assert "yq-watch" not in skill_folders(), "the action-list skill exists now: section 4 still says it comes later"
    # ... and so are the words of a line
    for reading in consts["READINGS"].values():                            # inside, above, below the normal range
        assert reading.endswith(" the normal range") and f"*{reading.split()[0]}*" in defs, reading
    assert consts["NOT_ABOVE"] not in consts["READINGS"].values() and f"*{consts['NOT_ABOVE']}*" in defs
    assert "a low week cannot be judged" in consts["NOT_ABOVE_NOTE"] and "4-week average" in consts["NOT_ABOVE_NOTE"]
    assert "**Why a low week often cannot be judged.**" in defs and "the four-week average" in defs
    assert consts["MONTH_END_TAG"].startswith("month-end week") and "tagged *month-end week* and is not judged" in defs
    for said, on_page in (("KPI step not set", "the pace line reads *KPI step not set*"),
                          ("held back since", "*held back since* that day"),
                          ("Part day: ", "**A part day.**"),
                          ("the month to date includes the part day", "the month to date keeps it"),
                          ("the reports were exported", "The day the reports were exported is never complete"),
                          ("hold no sale after", "the screen says where they end and why"),
                          ("Last full week", "the last full week is the last Sunday-to-Saturday week that is complete"),
                          ("Four monthly figures exist", "the four monthly figures that exist today"),
                          ("(shown once)", "lists, once,"),
                          ("the portal's monthly target", "*The portal's target* is the monthly sales target"),
                          ("Focus cost of sales", "reads Focus's own cost of sales")):
        assert said in src, f"the brief no longer says: {said}"
        assert on_page in defs, f"section 4 does not say: {on_page}"
    assert consts["NO_BUSINESS_DAY"].startswith("no business day yet this month") and "no business day yet" in defs
    # a silent salesman: MORE THAN the set number of business days (the setting's own words; the Command Centre's rule)
    assert "more than this many business days" in config.KEYS["brief.silent_business_days"][1]
    assert re.search(r'"REPS +\{[^"\n]*with no invoice for over \{', src), "the REPS line no longer says 'for over N'"
    assert "no invoice for more than the set number of business days, which is the Command Centre's own rule" in defs
    # no figure of the average on the page: the brief prints the system's own
    assert "the average month of the last 12 months" in defs and "This page gives no figure for that average" in defs
    assert re.search(r"add_months\(\w+, -12\)", src), "the brief no longer averages the last 12 months"


@test("the words of /yq-stock are the ones the stock command reads and prints, and the page's counts are its own")
def _():
    consts, source = module_constants(STOCK_FILE), read(STOCK_FILE)
    _problems, fm, _block, body, _text = parse_skill((SKILLS / "yq-stock" / "SKILL.md").read_bytes())
    # the hint: the two view words the command reads, then an item code; its usage line says the same
    words = re.findall(r"\[([^\]]+)\]", fm["argument-hint"])[0].split("|")
    assert words[-1] == "<code>" and set(words[:-1]) == set(consts["VIEW_WORDS"].values()), words
    assert set(words[:-1]) <= set(consts["VIEW_WORDS"]), "the hint names a word the command does not read"
    usage = (ast.get_docstring(ast.parse(source)) or "").splitlines()[0]
    assert usage.startswith("python -m scripts.ops stock [reorder | ageing | <item code>]"), usage
    assert fm["allowed-tools"] == ["Bash(python -m scripts.ops stock *)", "PowerShell(python -m scripts.ops stock *)"]
    # what the skill acts on, in the command's own words
    for said in ("goods on order not deducted", "No purchase order is drafted.", ", to check", 'text + ("*" if floored'):
        assert said in source, f"the stock command no longer says: {said}"
    assert "goods on order not deducted" in body and "A rate marked with a star" in body
    # the page's definitions carry the command's own counts and settings
    defs, words_of = flat(page_part(4)), {3: "three", 10: "ten"}
    for needle in (f"the units sold in the last {consts['RATE_DAYS']} days divided by the days",
                   f"never fewer than {consts['RATE_MIN_DAYS']}",
                   f"rests on fewer than {consts['RATE_MIN_DAYS']} days in stock",
                   f"New arrivals are items first received in the last {consts['RATE_DAYS']} days",
                   f"over the {consts['RANK_MONTHS']} full months before the month",
                   f"up to {consts['AGE_LIMITS'][0]} days, up to {consts['AGE_LIMITS'][1]}, up to a year, over a year",
                   f"above it up to {consts['COVER_LIMITS'][0]} days, up to {consts['COVER_LIMITS'][1]}, up to a year",
                   f"With at least {words_of[consts['LEAD_MIN']]} shipments matched",
                   f"the median of the last {words_of[consts['LEAD_SHOWN']]}",
                   "*goods on order not deducted*", "No purchase order is drafted",
                   "A return from a van is no receipt, so it never makes stock younger"):
        assert needle in defs, f"section 4 does not say: {needle}"
    assert consts["AGE_LIMITS"][2] == consts["COVER_LIMITS"][2] == 365, "the page calls the last band 'a year'"
    for key in ("stock.lead_time_days", "stock.cover_kpi_days", "stock.fast_movers", "stock.display_items"):
        assert key in config.KEYS and f"`{key}`" in defs, key
    assert {"stock.fast_movers", "stock.cover_kpi_days"} <= config.OPTIONAL
    assert "the plan's value stands in and the screen names the key" in defs
    assert "the plan's default is used" in source


@test("the status prints the five lines the page lists, in that order")
def _():
    with temp_root():
        lines = status.run(offline())["text"].splitlines()
    assert [ln.split(":")[0] for ln in lines] == ["Data", "This folder", "Load lock", "Waiting", "Action list"], lines
    row = next(ln for ln in page_part(1).splitlines() if "where do things stand?" in ln)
    assert ("Five lines: the data dates and the last load; this folder against the live site; the load lock; "
            "reports waiting; open items on the action list.") in row, row
    assert "(Claude runs `python -m scripts.ops status`.)" in row and "status" in cli.COMMANDS
    for text in (read(SKILLS / "yq-brief" / "SKILL.md"), read(ROOT / "CLAUDE.md")):
        assert "`python -m scripts.ops status`" in text
    # what makes Claude run it when he asks in plain words: the description of the skill that holds the rule
    _problems, fm, _block, body, _text = parse_skill((SKILLS / "yq-brief" / "SKILL.md").read_bytes())
    assert "whether the data is up to date and what is waiting to be loaded" in fm["description"]
    assert "If he only asks whether the data is up to date or what is waiting, run `python -m scripts.ops status`" in body
    # the words the check stops with when this folder and the live site differ are the status's own
    stops = flat(page_part(3))
    for said, on_page in (("pull it", "(*pull it*:"), ("deploy it, then load", "(*deploy it, then load*:"),
                          ("tell Claude", "(*tell Claude*)")):
        assert any(text.endswith(": " + said) for text in status.SITE_TEXT.values()), said
        assert on_page in stops, on_page


# -- a load that did not finish -------------------------------------------------------------------

def resume_step(skill: str) -> str:
    """The bullet of the skill's Step 1 that deals with an interrupted load ('' when there is none)."""
    m = re.search(r"^- `LOAD INTERRUPTED at <step> \(run <run>\)`.*$", section(skill, "Step 1"), re.M)
    return m.group(0) if m else ""


# What the skill's resume step says: the check hands the interrupted run's own GO line over as its last line,
# and that line is run as Step 2 runs any GO line. A check that could not hand it over is a stop like any other.
RESUME_SAYS = ("/yq-load is how he resumes it", "Do not check again and add no option",
               "The check prints that load's own `GO: ` line as its last line", "the box will say RESUME",
               "do Step 2 with that line", "If no `GO: ` line ends the output, say what it says and wait",
               "never write the line yourself")


def problems_resume(skill: str, check_text: str) -> list[str]:
    """Can /yq-load resume the cut-off load of RUN from what `load check` printed? The check must name the run
    and, when it prints a GO line, print that run's own and print it last; the skill must have a step that runs
    that line and waits when there is none. Pure: both texts are arguments, so broken ones can be tried."""
    out: list[str] = []
    lines = check_text.splitlines()
    go = [ln for ln in lines if ln.startswith("GO: ")]
    own = "GO: " + loadplan.go_line(RUN, SEAM_TOKEN)
    if go and (go != [own] or lines[-1] != own):
        out.append("the check prints a GO line that is not the interrupted run's own, or not as its last line")
    said = f"LOAD INTERRUPTED at refresh (run {RUN})"
    if said not in check_text:
        out.append(f"the check does not say '{said}': the skill reads the step and the run from that sentence")
    step = resume_step(skill)
    if not step:
        out.append("the skill has no step for an interrupted load")
        return out
    out += [f"the resume step lacks: {needle}" for needle in RESUME_SAYS if needle not in step]
    return out


@test("an interrupted load is resumed through /yq-load: the real load check hands its GO line over, the skill runs it")
def _():
    skill = read(SKILLS / "yq-load" / "SKILL.md")
    own = "GO: " + loadplan.go_line(RUN, SEAM_TOKEN)
    with temp_root() as root:
        cut_off_load(root)
        res = load_check.run_check(check_args(), standin_deps())
        text = res.get("text") or ""
        assert res.get("ok") is False and res.get("exit") == 2, res                    # a stop, and it says so
        assert text.splitlines()[-1] == own, text                                      # ... that ends on the resume
        assert not problems_resume(skill, text), (problems_resume(skill, text), text)
        assert "RESUME" in text and "RESUME: the load of this folder was interrupted" in read(WRITER_FILE)
        # a stopped check opens no run of its own, so `load status` goes on following the interrupted one
        assert loadplan.latest_run() == RUN, loadplan.latest_run()
        # a developer's proof run prints no GO line, not even this one
        dev = load_check.run_check(check_args(dev=True), standin_deps()).get("text") or ""
        assert f"LOAD INTERRUPTED at refresh (run {RUN})" in dev and "GO: " not in dev, dev
        # the same load with its plan gone: there is no token to hand a line over with. The check still names the
        # run and prints no GO line, and the skill then says what the output says and waits
        (loadplan.run_dir(RUN) / "plan.json").unlink()
        bare = load_check.run_check(check_args(), standin_deps())
        assert bare.get("exit") == 2 and not [ln for ln in bare["text"].splitlines() if ln.startswith("GO: ")], bare
        assert not problems_resume(skill, bare["text"]), (problems_resume(skill, bare["text"]), bare["text"])


@test("the resume check bites: no resume step, a rule turned round, a check that names no run, a foreign GO line")
def _():
    skill = read(SKILLS / "yq-load" / "SKILL.md")
    said = f"LOAD CHECK: STOP\nLOAD INTERRUPTED at refresh (run {RUN}): an earlier load was cut off."
    handed = said + "\nGO: " + loadplan.go_line(RUN, SEAM_TOKEN)
    assert not problems_resume(skill, said) and not problems_resume(skill, handed)
    old_skill = skill.replace(resume_step(skill) + "\n", "")                      # the skill as it was first reviewed
    assert resume_step(skill) and not resume_step(old_skill)
    again = skill.replace("Do not check again and add no option.", "Check again.")
    by_hand = skill.replace("never write the line yourself", "write the line yourself")
    no_wait = skill.replace("If no `GO: ` line ends the output, say what it says and wait; ", "")
    for text in (said, handed):
        assert any("no step for an interrupted load" in p for p in problems_resume(old_skill, text))
        assert any("Do not check again" in p for p in problems_resume(again, text))
        assert any("never write the line yourself" in p for p in problems_resume(by_hand, text))
        assert any("say what it says and wait" in p for p in problems_resume(no_wait, text))
    assert any("does not say" in p for p in problems_resume(skill, "LOAD CHECK: STOP\nLOAD INTERRUPTED at refresh."))
    foreign = said + "\nGO: " + loadplan.go_line(OTHER_RUN, SEAM_TOKEN)
    assert any("not the interrupted run's own" in p for p in problems_resume(skill, foreign))
    not_last = handed + "\nNo token was issued. Nothing was changed."            # Step 2 runs a GO line that ends the output
    assert any("not as its last line" in p for p in problems_resume(skill, not_last))


@test("the two lines that open a command after a load that did not finish are the ones the skills and the page act on")
def _():
    rehearsal_run = status.REHEARSAL_PREFIX + "0900"
    with temp_root() as root:
        assert status.interrupted_banner() is None                                 # nothing to say on a quiet day
        cut_off_load(root)
        cut = status.interrupted_banner()
        cut_status = status.run(offline())["text"]
    with temp_root():
        failed_load()
        failed = status.interrupted_banner()
        failed_status = status.run(offline())["text"]
    with temp_root():
        dead_lock(rehearsal_run, "refresh (rehearsal)")                            # a rehearsal that was cut off
        left, quiet = status.lock_state(), status.interrupted_banner()
        left_status = status.run(offline())["text"]
    assert cut == "LOAD INTERRUPTED at refresh: run /yq-load", cut
    assert failed and failed.startswith(f"LAST LOAD FAILED at refresh (run {RUN}): "), failed
    assert failed.endswith(": run /yq-load") and "the database may hold part of it" in failed, failed
    assert "INTERRUPTED at refresh: run /yq-load" in cut_status, cut_status              # the status says the same
    assert f"LAST LOAD FAILED at refresh (run {RUN})" in failed_status, failed_status
    cut_form = cut.replace("refresh", "<step>")
    failed_form = failed.split(": ")[0].replace("refresh", "<step>").replace(RUN, "<run>")
    load, brief = read(SKILLS / "yq-load" / "SKILL.md"), read(SKILLS / "yq-brief" / "SKILL.md")
    claude, page = read(ROOT / "CLAUDE.md"), flat(read(PAGE))
    # the page gives him both lines as they are printed (section 3); both skills and CLAUDE.md act on their first words
    told = flat(page_part(3))
    assert f"- `{cut_form}`. A load was cut off" in told, cut_form
    assert f"- `{failed_form}`. The last load stopped" in told, failed_form
    assert "the brief and the status open with one of two lines, and the load check says the same in its own words" in told
    for head in (cut.split(" at ")[0], failed.split(" at ")[0]):
        assert head in ("LOAD INTERRUPTED", "LAST LOAD FAILED"), head
        assert head in load and head in brief and f"`{head}`" in claude, head
    assert "If a command opens with `LOAD INTERRUPTED` or `LAST LOAD FAILED`, type `/yq-load`" in page
    assert "If the output opens with LOAD INTERRUPTED or LAST LOAD FAILED, say so first" in brief
    assert "When a command opens with `LOAD INTERRUPTED` or `LAST LOAD FAILED`, tell the owner first" in claude
    # what a failed load means, in the same words everywhere: it had begun to write, and a new load replaces it
    step1 = section(load, "Step 1")
    assert "(other commands open with `LAST LOAD FAILED` for the same thing)" in step1
    assert "the last load stopped after it had begun to write, so the database may hold part of it" in step1
    assert "this /yq-load starts a new load that replaces it" in step1
    assert "The last load stopped after it had begun to write" in page and "so the database may hold part of it" in page
    assert "it starts a new load, and that load replaces the failed one" in page
    assert "after a failed load it starts the one that replaces it" in brief
    # ... but for the one failure after which the writer itself says not to load again before somebody has looked
    assert "its totals do not match the reports" in read(WRITER_FILE)
    assert "Ask Claude to look into these totals before anything is loaded again" in read(WRITER_FILE)
    assert ("if the failed load said that its totals do not match the reports, ask Claude to look into those totals "
            "before anything is loaded again") in page
    # the banner speaks only while that failed load is the newest: a load that finished since ends it
    with temp_root():
        failed_load()
        status.write_state({"last_load": {"run": OTHER_RUN, "finished_at": "2999-01-01T00:00:00+00:00"}})
        assert status.interrupted_banner() is None
    # a cut-off rehearsal is no interrupted load: nothing opens a command, and the next load clears its lock
    assert left.get("state") == "none" and left.get("rehearsal_left") == rehearsal_run and quiet is None, (left, quiet)
    assert "the next load clears it" in left_status, left_status
    assert module_constants(WRITER_FILE)["REHEARSAL_PREFIX"] == status.REHEARSAL_PREFIX
    assert "left-over lock is cleared" in read(WRITER_FILE)
    assert ("A rehearsal that was cut off needs nothing from you: it changed nothing, and the next load clears its "
            "lock") in page


@test("after a failed load the check says so in the line Step 1 waits for, before anything else, and goes on checking")
def _():
    skill = read(SKILLS / "yq-load" / "SKILL.md")
    step1 = section(skill, "Step 1")
    head = f"The last load (run {RUN}) failed at refresh after it had begun to write"
    with temp_root():
        failed_load()
        res = load_check.run_check(check_args(), standin_deps(worktree=True))  # it stops at its first step, for a
        lines = res["text"].splitlines()                                       # reason of its own (see below)
        # the two ways that line ends: on a check that stops, and on one that ends with a GO line
        stops = load_check.failed_line(status.failed_load(), starts=False)
        starts = load_check.failed_line(status.failed_load(), starts=True)
    # said before the check's own text, whatever the check finds (on the screen the lines of the steps that
    # finished stand above it, so the skill looks for the line and not for the first line of the output)
    assert lines[0] == stops and lines[1] == "", lines[:2]
    # ... and the check went on: it was not the failed load that stopped it. (What stops it here is the rule the
    # skill gives Claude in its own words: nothing runs from a code worktree.)
    assert "LOAD CHECK: STOP" in lines and "LOAD INTERRUPTED" not in res["text"] and res["exit"] == 2, res["text"]
    assert "This is a code worktree (.claude/worktrees): a load never runs from here" in res["text"], res["text"]
    assert "If the working folder path contains `.claude/worktrees/`, run nothing" in skill
    for line in (stops, starts):
        assert line.startswith(head + ": "), line
    assert stops.endswith("the database may hold part of it until a new load replaces it.")
    assert starts.endswith("this check starts a new load that replaces it.")
    form = head.replace(RUN, "<run>").replace("refresh", "<step>")
    assert f"- A line that starts `{form}`" in step1, form
    assert "once the output ends with a `GO: ` line" in step1                  # it replaces it only when it can go
    assert f"a line that starts `{form}`" in flat(page_part(7))
    with temp_root():                                                          # no failed load: no such line
        quiet = load_check.run_check(check_args(), standin_deps(worktree=True))["text"]
    assert quiet.splitlines()[0] == "LOAD CHECK: STOP" and "The last load" not in quiet, quiet


@test("a new check retires the earlier checks of the same folder as the page says: go line, go.cmd, staged copies")
def _():
    newer = RUN[:7] + "0905"                                                   # the same folder, five minutes later
    told = flat(page_part(3))
    with temp_root() as root:
        drop = root / "Uplaods" / RUN[:6]
        drop.mkdir(parents=True)
        go_cmd = cut_off_load(root)                                            # a check of that folder with a token ...
        paths.remove_file(paths.lock_file())                                   # ... that was never started:
        paths.remove_file(loadplan.journal_file(RUN))                          # no lock, no journal
        staged = paths.write_text(loadplan.staging_path(RUN) / "Sales_day_book.xlsx", "a stand-in for a staged report\n")
        assert loadplan.run_result(RUN) == "NOT_STARTED" and go_cmd.is_file() and staged.is_file()
        found = load_check.retire_earlier(newer, drop, standin_deps())
        said = load_check.retired_line(found) or ""
        plan = loadplan.read_plan(RUN)
        moved = paths.staging_dir() / "superseded" / RUN / staged.name
        assert found.get("retired") == [RUN], found
        assert plan.get("superseded_by") == newer and plan.get("token") == SEAM_TOKEN, plan
        assert not go_cmd.exists(), "the earlier check's go.cmd is still there"
        assert moved.is_file() and not loadplan.staging_path(RUN).exists(), "its staged copies did not move"
        after = load_check.run_status(check_args(action="status", run=RUN), standin_deps())["text"]
        # a check whose load has started is never touched: the same folder, a run with a journal
        started = RUN[:7] + "0800"
        loadplan.write_plan(dict(plan, run=started, superseded_by=None))
        loadplan.journal_append(started, "started", pid=0)
        kept = paths.write_text(loadplan.staging_path(started) / "Sales_day_book.xlsx", "a stand-in\n")
        again = load_check.retire_earlier(RUN[:7] + "0910", drop, standin_deps())
        assert started not in (again.get("retired") or []) and kept.is_file(), again
        assert not loadplan.read_plan(started).get("superseded_by")
    assert "its go line no longer works" in said and "business_data/staging/superseded" in said, said
    assert f"replaced by a newer one, run {newer}" in after and after.splitlines()[-1] == "NOT STARTED", after
    assert "This check was replaced by a newer one" in read(WRITER_FILE)      # what the writer answers to the old line
    for needle in ("Only the newest check of a folder counts",
                   "A new check of the same folder retires the earlier checks of it that were never loaded",
                   "their go lines no longer work (the load program refuses them and says that a newer check replaced them)",
                   "their `go.cmd` is removed", "their copies move to `business_data/staging/superseded/`",
                   "A check whose load has started, finished or failed is never touched"):
        assert needle in told, f"section 3 does not say: {needle}"
    assert "The copies of a check that a newer check of the same folder retired" in flat(page_part(5))


@test("the go.cmd the skill and the page point to is the file the load check writes and names, with one python line: the go")
def _():
    skill, page = read(SKILLS / "yq-load" / "SKILL.md"), flat(read(PAGE))
    with temp_root() as root:
        go_cmd = cut_off_load(root)
        assert go_cmd.relative_to(root).as_posix() == GO_CMD.replace("<run>", RUN)
        text = read(go_cmd)
        runs = [ln for ln in text.splitlines() if ln.startswith("python")]
        assert runs == [loadplan.go_line(RUN, SEAM_TOKEN)], runs               # the same line, so the same box opens
        assert text.splitlines()[-1] == "pause"                                # "it waits for a key at the end"
        # the check prints where that file lies, on the line above the GO line
        printed = load_check.run_check(check_args(), standin_deps())["text"].splitlines()
        named = load_check.go_cmd_line(RUN)
        assert printed[-1].startswith("GO: ") and printed[-2] == named, printed[-2:]
        assert named == f"If Claude may not start it: double-click {go_cmd}", named
        go_cmd.unlink()
        assert load_check.go_cmd_line(RUN) is None                             # no file, no line: nothing false is said
    step2 = section(skill, "Step 2")
    assert skill.count(f"`{GO_CMD}`") == 1 and f"double-click `{GO_CMD}` in the project folder" in step2
    assert "(the check printed its full path, on the line that starts `If Claude may not start it`)" in step2
    assert "the check prints the file's full path just above its go line" in page
    assert "the line above it that starts `If Claude may not start it: double-click`" in flat(page_part(7))
    assert "`go.cmd`, in `business_data/ops/loads/<run>/` of the project folder" in page
    assert "It runs the same line, so the same Yes/No box opens, and it waits for a key at the end" in page
    assert "the `go.cmd` the check named" not in skill and "the `go.cmd` it names" not in page   # no path was named
    assert f"loads/{RUN}/go.cmd" not in skill and RUN not in page                # placeholders, never a real run


# -- following a load: the last line of `load status` ---------------------------------------------

STATUS_LAST_LINES = ("RUNNING", "FINISHED", "FAILED", "INTERRUPTED", "NOT STARTED")
A_LAST_LINE = re.compile(r"^(" + "|".join(STATUS_LAST_LINES) + r")(?: at \S.*)?$")


def status_of(run: str | None = None) -> tuple[str, int]:
    """(the first words of the last line `load status` prints for a run, its exit code): the real command."""
    res = load_check.run_status(check_args(action="status", run=run), standin_deps())
    last = res["text"].splitlines()[-1]
    m = A_LAST_LINE.match(last)
    assert m, f"`load status` ended on a line the skill does not know: {last!r}"
    return m.group(1), int(res["exit"])


@test("the last lines `load status` ends on, and its exit codes, are the ones the skill follows to: the real status")
def _():
    seen: dict[str, int] = {}
    with temp_root():
        seen.update([status_of()])                                             # no load was ever checked
        assert list(seen) == ["NOT STARTED"], seen
        (paths.loads_dir() / RUN).mkdir(parents=True)                          # checked, never started
        assert status_of(RUN) == ("NOT STARTED", seen["NOT STARTED"])
        loadplan.journal_append(RUN, "started", pid=0)                         # the worker's first lines, a moment ago
        loadplan.journal_append(RUN, "step_start", step="backup")
        seen.update([status_of(RUN)])
        loadplan.journal_append(RUN, "finished", result="ok")
        seen.update([status_of(RUN)])
        failed_load(OTHER_RUN)
        seen.update([status_of(OTHER_RUN)])
    with temp_root() as root:
        cut_off_load(root)
        seen.update([status_of()])                                             # it follows the run that holds the lock
    assert set(seen) == set(STATUS_LAST_LINES), f"`load status` ended on {sorted(seen)}"
    finals = [w for w in STATUS_LAST_LINES if w != "RUNNING"]
    skill = read(SKILLS / "yq-load" / "SKILL.md")
    step3, step4 = section(skill, "Step 3"), section(skill, "Step 4")
    followed = re.search(r"until its last line says (.+?)\. ", step3)
    assert followed and re.split(r", | or ", followed.group(1)) == finals, (followed and followed.group(1), finals)
    for word in finals:
        assert re.search(r"^- " + word + r": ", step4, re.M), f"Step 4 has no line for {word}"
    assert "RUNNING" not in step4 and "Show only the new lines each time" in step3
    page = flat(page_part(7))
    assert "(" + ", ".join(f"`{w}`" for w in STATUS_LAST_LINES[:-1]) + f" or `{STATUS_LAST_LINES[-1]}`;" in page
    # exit codes: 0 while there is nothing to act on, 2 when the load needs him
    assert {w for w, code in seen.items() if code == 2} == {"FAILED", "INTERRUPTED"}, seen
    assert {w for w, code in seen.items() if code == 0} == {"RUNNING", "FINISHED", "NOT STARTED"}, seen
    assert "it exits with code 2 on `FAILED` and `INTERRUPTED`" in page


# -- the go: its exit codes and the lines it says -------------------------------------------------

@test("every exit code of the go has its line in Step 2, and the lines the go says are the ones the skill acts on")
def _():
    consts, source = module_constants(WRITER_FILE), read(WRITER_FILE)
    ok, error, refused, again, no_answer = (consts[k] for k in ("EXIT_OK", "EXIT_ERROR", "EXIT_REFUSED",
                                                                "EXIT_CHECK_AGAIN", "EXIT_NO_ANSWER"))
    assert sorted((ok, error, refused, again, no_answer)) == [0, 1, 2, 3, 4], (ok, error, refused, again, no_answer)
    step2 = section(read(SKILLS / "yq-load" / "SKILL.md"), "Step 2")
    bullets = [ln for ln in step2.splitlines() if ln.startswith("- ")]
    for needle in (f"- Exit code {ok} (it says `load started`): go to Step 3.",
                   f"- Exit code {again} (check again): run Step 1 once more with the same arguments, show the new table "
                   "and run its new line once.",
                   f"- Exit code {no_answer} (No, or no answer): say what the output says and wait for him: ",
                   f"- The output holds `Its resume: ` (exit code {refused}, or {error}): ",
                   f"- Exit code {error} or {refused}, or anything else: show the last lines unchanged and wait."):
        assert any(ln.startswith(needle) for ln in bullets), f"Step 2 lacks: {needle}"
    assert bullets[-1].startswith(f"- Exit code {error} or {refused}, or anything else"), bullets[-1]   # the catch-all
    # the go itself, by the writer's own launcher in a world where nothing can start: check again, No, refused
    token = loadplan.token_of(EMPTY_PARTS)
    with temp_root() as root:
        checked(OTHER_RUN, root, token)
        code, said = go_says(OTHER_RUN, "x" + token, root)                     # a line that is not this table's
        assert code == again and said.startswith("CHECK AGAIN") and "nothing was loaded" in said \
            and said.endswith("Type /yq-load to check again."), (code, said)
        code, said = go_says(OTHER_RUN, token, root)                           # the table still holds; the box says No
        assert code == no_answer and re.search(r"\bNo\b.*nothing (?:was )?loaded", said), (code, said)
        cut_off_load(root)                                                     # another load was cut off meanwhile
        code, said = go_says(OTHER_RUN, token, root)
        resume = [ln.split("Its resume: ", 1)[1] for ln in said.splitlines() if "Its resume: " in ln]
        assert code == refused and f"LOAD INTERRUPTED at refresh (run {RUN})" in said, (code, said)
        assert resume == [loadplan.go_line(RUN, SEAM_TOKEN)], said             # the text after it is the line to run
        (loadplan.run_dir(RUN) / "plan.json").unlink()                         # ... unless that load's plan is gone:
        code, said = go_says(OTHER_RUN, token, root)                           # then it is a sentence, and the skill waits
        resume = [ln.split("Its resume: ", 1)[1] for ln in said.splitlines() if "Its resume: " in ln]
        assert code == refused and len(resume) == 1 and not resume[0].startswith("python"), (code, said)
    with temp_root() as root:                                                  # a check that a newer one replaced
        checked(RUN, root, token)
        loadplan.write_plan(dict(loadplan.read_plan(RUN), superseded_by=OTHER_RUN))
        code, said = go_says(RUN, token, root)
        assert code == refused and "replaced by a newer one" in said and "/yq-load" in said, (code, said)
    # the page lists the codes as the writer's own text does
    listed = re.search(r"^Exit codes: (.+)\.$", source, re.M)
    assert listed, "the writer's text no longer lists its exit codes"
    assert listed.group(1) == (f"{ok} started, {error} error, {refused} refused, {again} check again, "
                               f"{no_answer} no answer at the box"), listed.group(1)
    assert f"({listed.group(1)})" in flat(page_part(7))
    # exit 0: the line the skill reads, the command it follows with, and the one sentence it passes on first
    assert consts["FOLLOW_LINE"] == "python -m scripts.ops load status --wait"
    assert "load started: follow it with {FOLLOW_LINE}" in source
    assert "Keep the Claude Code window open until LOAD FINISHED" in source
    assert "If it also says to keep the Claude Code window open, tell him that first" in step2
    assert "If Claude tells you to keep the Claude Code window open, do" in flat(page_part(1))
    # exit 3: once more, never a loop (the go itself sends him back to /yq-load: see above)
    assert f"If that go ends with exit code {again} as well, show its lines and wait for him: never loop" in step2
    # exit 4: a box that is closed or left unanswered is a No as well
    assert re.search(r"no answer, nothing (?:was )?loaded", source), "the go no longer says what no answer at the box means"
    # "Its resume: ": wherever the go says it, the line that resumes follows it to the end of the line (above, with
    # exit code 2; here, where its own worker stopped as it started, with exit code 1)
    after = re.findall(r"Its resume: ([^\n]*)", source)
    assert after and all(re.match(r'(?:\{|"\s*\+\s*)_resume_line\(\w+\)', rest) for rest in after), after
    assert re.search(r"Its resume: [^\n]*_resume_line\(\w+\)[^\n]*\n\s+return EXIT_ERROR\b", source), \
        "the go no longer names the resume when its worker stopped as it started"
    assert loadplan.go_line(RUN, "x").startswith("python ")
    for needle in ("Show the output unchanged", "The text after `Its resume: ` is the line that resumes it",
                   "only when he says so", "only if it starts with `python`"):
        assert needle in step2, f"Step 2 lacks: {needle}"
    assert "the words `Its resume: ` in what the go says" in flat(page_part(7))
    # exit 2, one refusal the page explains to him: a settings rule that would pre-approve the load
    assert "would let a load start without asking you" in source and "delete the entry" in source
    assert "it refuses to start, names the entry to delete and the file it is in, and loads nothing" in flat(page_part(2))
    assert "a rehearsal included" in flat(page_part(2))


@test("after the load: the skill, the page and CLAUDE.md act on the words the writer, the sender and the status print")
def _():
    writer, sender = read(WRITER_FILE), read(ROOT / "scripts" / "yq_send.py")
    load, brief, page = read(SKILLS / "yq-load" / "SKILL.md"), read(SKILLS / "yq-brief" / "SKILL.md"), flat(read(PAGE))
    # the state `load status` reads while the e-mail is made, and the line it ends on
    assert load_check.state_text("RUNNING", "delivery") == "RUNNING at delivery"
    assert 'return "RUNNING", "delivery"' in read(OPS / "load_check.py")
    # every last word of the writer and of the sender starts with `Delivery:`; each failure carries its retry
    assert writer.count('said = f"Delivery: ') + writer.count('said = (f"Delivery: ') >= 3, "the writer's Delivery lines"
    assert 'text="Delivery: (rehearsal)' in writer and sender.count('say(f"Delivery: ') >= 3
    assert writer.count("Its retry: ") >= 3 and writer.count("{SEND_LINE.format(run=run)}") >= 3
    assert sender.count("Its retry: {retry_line(run)}") >= 3
    consts = module_constants(WRITER_FILE)
    assert consts["SEND_LINE"] == load_check.SEND_RETRY == "python -m scripts.yq_send --run {run}"
    assert 'return f"python -m {MODULE} --run {run}"' in sender and 'MODULE = "scripts.yq_send"' in sender
    # an e-mail that did not go is never worded like a load that failed
    assert "FAILED" not in " ".join(load_check.DELIVERY_WORDS.values())
    # the retry the skill runs on his word is never pre-approved, and no rule of the skill names the sender
    for p in skill_files():
        _problems, fm, _block, _body, _text = parse_skill(p.read_bytes())
        for entry in fm["allowed-tools"]:
            assert "yq_send" not in entry and not rule_matches(entry, load_check.SEND_RETRY.format(run=RUN)), entry
    assert "yq_send" not in load and "yq_send" not in brief          # the skills never spell the sender
    # the team message: the brief's own word, the page's and CLAUDE.md's
    assert module_constants(BRIEF_FILE)["TEAM_WORD"] == "team"
    assert "`/yq-brief team`" in page and "`/yq-brief team`" in read(ROOT / "CLAUDE.md")
    assert "A load's e-mail goes once: never send it again, or send anything to WhatsApp, unless he asks for exactly " \
           "that." in read(ROOT / "CLAUDE.md")
    # what the page says each file and command is, as the programs name them
    for needle in ("`delivery.json`", "`backup.json`", "`drills.jsonl`", "`delivery.jsonl`", "`email.html`",
                   "`whatsapp.txt`", "`whatsapp.html`", "`manifest.json`", "`legacy\\`"):
        assert needle in page, needle
    for name in ("delivery.json", "backup.json", "drills.jsonl"):
        assert name in read(ROOT / "scripts" / "yq_load_go.py") + read(ROOT / "scripts" / "yq_backup.py") + sender, name
    assert "--test" in sender and "--resend" in sender and "--auto" in sender


@test("the rehearsal on the page is the one the writer runs: its command, its option, its length, its last line")
def _():
    consts, options, source = module_constants(WRITER_FILE), source_options(WRITER_FILE), read(WRITER_FILE)
    part = page_part(2)
    m = re.search(r"^\*\*Rehearse first\.\*\*.*?(?=^\*\*Also once)", part, re.S | re.M)
    assert m, "section 2 has no 'Rehearse first' paragraph before the backup password"
    rehearsal = flat(m.group(0))
    assert f"Claude runs `{REHEARSAL}` in the main project folder" in rehearsal
    named = set(A_FLAG.findall(rehearsal))
    assert named == {"--rehearse", "--no-box", "--wait"}, sorted(named)
    assert {"--rehearse", "--no-box"} <= options, sorted(options)
    assert "The first time, Claude leaves out `--no-box`, so that you see the box" in rehearsal
    assert "rehearsal only: do not show the Yes/No box" in source
    assert f"follows it with `{consts['FOLLOW_LINE']}`" in rehearsal
    minutes, rest = divmod(consts["REHEARSAL_SECONDS"], 60)
    assert rest == 0 and f"for about {NUMBER_WORDS[minutes]} minutes in all" in rehearsal, consts["REHEARSAL_SECONDS"]
    assert '" (REHEARSAL)"' in source and "its title ends with REHEARSAL" in rehearsal
    assert "REHEARSAL: nothing is loaded and the database is not touched." in source
    assert "A rehearsal loads nothing and never touches the database" in rehearsal
    assert "REHEARSAL FINISHED" in source and "with the line `REHEARSAL FINISHED`" in rehearsal
    assert "Each time the status must come to `FINISHED`" in rehearsal and "ends as `INTERRUPTED` instead" in rehearsal
    # ... and what the go says at the start when Windows keeps the worker inside the window that started it
    assert "Keep the Claude Code window open until REHEARSAL FINISHED: this rehearsal stops if it is closed." in source
    assert "to keep the Claude Code window open until the rehearsal has finished, the third trial is already answered" \
        in rehearsal
    # the three things to try while the status polls: let the call return, cancel it, close the window
    tried = re.findall(r"^(\d)\. (.+?)(?=^\d\. |^\S|\Z)", m.group(0), re.S | re.M)
    assert [n for n, _ in tried] == ["1", "2", "3"], [n for n, _ in tried]
    for (_n, text), needle in zip(tried, ("The command that started it comes back after a few seconds",
                                          "press Esc right after your **Yes**", "close the Claude Code window")):
        assert needle in flat(text), f"the rehearsal does not say: {needle}"
    # Claude Code asks before a rehearsal as it does before a load, in both shells
    for tool in ("Bash", "PowerShell"):
        assert [r for r in SIX_ASK_RULES if r.startswith(tool + "(") and rule_matches(r, REHEARSAL)], tool
    # CLAUDE.md sends a session that is asked for a rehearsal to this section
    claude = read(ROOT / "CLAUDE.md")
    assert "A rehearsal of the load" in claude and "`docs/OPS_SKILLS.md`, section 2" in claude


# ── the skills ─────────────────────────────────────────────────────────────────────────────────

@test("both phase-1 skills exist and nothing shadows the weekly-review or monthly-review commands")
def _():
    assert {"yq-load", "yq-brief"} <= skill_folders(), sorted(skill_folders())
    for d in SKILLS.iterdir():
        if not d.is_dir():
            continue
        assert d.name not in SHADOWS, f"the skill folder {d.name} would hide the command of that name"
        md = d / "SKILL.md"
        if md.is_file():
            _problems, fm, _block, _body, _text = parse_skill(md.read_bytes())
            assert not fm or fm.get("name") not in SHADOWS, d.name
    for name in SHADOWS:
        assert (ROOT / ".claude" / "commands" / f"{name}.md").is_file(), f"the {name} command is gone"


@test("each SKILL.md starts with frontmatter at line 1 that PyYAML parses to a dict, with no key given twice")
def _():
    assert_clean("format")


def make_skill_keys() -> list[str]:
    """The frontmatter keys the real skills carry, all of them."""
    keys: list[str] = []
    for p in skill_files():
        _problems, fm, _block, _body, _text = parse_skill(p.read_bytes())
        keys += list(fm or {})
    return keys


@test("frontmatter: name = folder, description, argument-hint, strings in double quotes, the five allowed keys only")
def _():
    assert_clean("frontmatter")
    assert set(make_skill_keys()) <= ALLOWED_KEYS, sorted(set(make_skill_keys()) - ALLOWED_KEYS)
    assert len(ALLOWED_KEYS) == 5


@test("allowed-tools: read-only scripts.ops commands, Bash and PowerShell twins, all registered")
def _():
    assert_clean("tools")
    for p in skill_files():
        _problems, fm, _block, _body, _text = parse_skill(p.read_bytes())
        words = {TOOL_FORM.match(t).group(2) for t in fm["allowed-tools"]}
        assert words <= set(cli.COMMANDS), (p.parent.name, sorted(words - set(cli.COMMANDS)))


@test("no allowed-tools entry matches the load writer, scripts.refresh, scripts.load_supabase or a deploy")
def _():
    assert_clean("tools")                                     # the matching is part of the tools checks
    for p in skill_files():
        _problems, fm, _block, _body, _text = parse_skill(p.read_bytes())
        for entry in fm["allowed-tools"]:
            for command in MUST_NOT_MATCH:
                assert not rule_matches(entry, command), (p.parent.name, entry, command)
        # ... and the entries do cover what the skill is for, so the test above is not vacuous
        own = OWN_COMMAND.get(p.parent.name, "brief")
        assert any(rule_matches(e, f"python -m scripts.ops {own}") for e in fm["allowed-tools"]
                   if e.startswith("Bash(")), p.parent.name


@test("only yq-load is owner-only (disable-model-invocation: true)")
def _():
    assert_clean("flags")
    folders = skill_folders()
    assert OWNER_ONLY <= folders
    for p in skill_files():
        _problems, fm, _block, _body, _text = parse_skill(p.read_bytes())
        flagged = fm.get("disable-model-invocation") is True
        assert flagged == (p.parent.name in OWNER_ONLY), p.parent.name


@test("no SKILL.md injects a shell command or declares hooks; each is under 70 lines")
def _():
    assert_clean("text")
    for p in skill_files():
        _problems, fm, _block, _body, text = parse_skill(p.read_bytes())
        assert "hooks" not in fm, p.parent.name
        assert len(text.splitlines()) < 70, (p.parent.name, len(text.splitlines()))


@test("every python command a skill spells is registered and pre-approved by that skill's own allowed-tools")
def _():
    for p in skill_files():
        _problems, fm, _block, body, _text = parse_skill(p.read_bytes())
        tools = fm["allowed-tools"]
        spelled = re.findall(r"`(python [^`]*)`", body)
        assert spelled, p.parent.name
        for command in spelled:
            assert command.startswith("python -m scripts.ops "), (p.parent.name, command)
            word = command.split()[3]
            assert word in cli.COMMANDS, (p.parent.name, command)
            sample = command.replace("$ARGUMENTS", "071026")
            for tool in ("Bash", "PowerShell"):
                assert any(rule_matches(t, sample) for t in tools if t.startswith(tool + "(")), \
                    (p.parent.name, tool, command)


# What each skill must say, as whole phrases: a rule with its key words left in but its sense turned round
# (run the line again until he clicks Yes) has to fail here.
MUST_CONTAIN = {
    "yq-load": [
        # the commands, exactly, and how they are run
        "python -m scripts.ops load check $ARGUMENTS", "python -m scripts.ops load status --wait",
        "python -m scripts.ops brief", "600000", "not in the background", "no pipe",
        # the rules
        "Never run scripts.refresh, scripts.ingest, scripts.load_supabase or any deploy",
        "Never edit, move or delete anything in the drop folder", "Show script output unchanged, in a code block",
        "Type no number yourself", "`.claude/worktrees/`, run nothing", "Do not fix a stop yourself",
        # step 1: an interrupted load is resumed by its own go line, which the check prints last
        "`LOAD INTERRUPTED at <step> (run <run>)`", "/yq-load is how he resumes it",
        "Do not check again and add no option", "The check prints that load's own `GO: ` line as its last line",
        "the box will say RESUME", "do Step 2 with that line",
        "If no `GO: ` line ends the output, say what it says and wait", "never write the line yourself",
        # ... a failed load is said first and plainly, and this load is the one that replaces it
        "A line that starts `The last load (run <run>) failed at <step> after it had begun to write`",
        "other commands open with `LAST LOAD FAILED` for the same thing", "tell him that first",
        "say plainly that the last load stopped after it had begun to write, so the database may hold part of it",
        "this /yq-load starts a new load that replaces it, once the output ends with a `GO: ` line",
        # ... any other stop waits for his words
        "Say why in the script's words, then stop", "load the price book", "--pricebook load",
        "leave the price book out", "--pricebook skip",
        "load profitability anyway", "--with-profitability", "replace on purpose", "--replace-ok",
        "partial on purpose", "--partial-ok", "only when he says it", "NEEDS YOUR DECISION",
        # step 2: the box, and one line for every way the go can end
        "GO: ", "Yes loads, No changes nothing", "run exactly the text after `GO: `",
        "Exit code 0 (it says `load started`): go to Step 3",
        "If it also says to keep the Claude Code window open, tell him that first",
        "Exit code 3 (check again): run Step 1 once more", "and run its new line once", "wait for him: never loop",
        "Exit code 4 (No, or no answer): say what the output says and wait for him",
        "at a RESUME box, the load stays interrupted",
        "The output holds `Its resume: `", "Show the output unchanged",
        "run it, as in this step, only when he says so, and only if it starts with `python`",
        "do not retry, rephrase or wrap it",
        "If he himself answered No to Claude Code's question, nothing was loaded: wait for him",
        "double-click `business_data/ops/loads/<run>/go.cmd`",
        "the check printed its full path, on the line that starts `If Claude may not start it`",
        "When he says he has answered it, go to Step 3",
        "Exit code 1 or 2, or anything else: show the last lines unchanged and wait",
        # steps 3 and 4: the four last lines `load status` stops on
        "FINISHED, FAILED, INTERRUPTED or NOT STARTED", "Show only the new lines each time",
        "if a line says that something needs a look, tell him which",
        "the data is loaded: name the metric the script names and do not go again",
        "typing /yq-load resumes it", "NOT STARTED: the load did not begin", "no deploy is needed",
        # after the load: the e-mail, said in one sentence; its retry only on his word; never a second copy
        "`RUNNING at delivery` means the load has ended and its e-mail is being made: keep following",
        "Say in one sentence what the line that starts `Delivery:` says",
        "after a load it is the brief the load made, the figures of the e-mail",
        "A line that holds `Its retry: `", "the e-mail did not go, and the load is not touched by that",
        "sends it again and loads nothing: run it only when he says so, and only if it starts with `python`",
        "Never send a load's e-mail a second time unless he asks for exactly that",
    ],
    "yq-brief": [
        "python -m scripts.ops brief $ARGUMENTS", "python -m scripts.ops status", "600000", "no pipe",
        "Add a word only when he asked for it",
        "Run nothing else that loads, writes or deploys", "Show the screen unchanged, in a code block",
        "type no number yourself",
        "If the output opens with LOAD INTERRUPTED or LAST LOAD FAILED, say so first, in plain words, and point him "
        "to /yq-load", "Change nothing yourself",
        "at most three lines", "no number that is not on the screen", "Never accuse anyone", "to check",
        "two lines (the data date and what is waiting)", "run it again with `again` added",
        "say that it arrives in a later phase",
        # the team message: his to copy, shown as it was made, never sent from here
        "`team` on its own for the team message", "in one code block of its own, unchanged, and add nothing",
        "Never send it anywhere yourself",
    ],
    "yq-stock": [
        "python -m scripts.ops stock $ARGUMENTS", "600000", "no pipe", "Add a word only when he asked for it",
        "`reorder` for the whole order list", "`ageing` for the ageing table", "or an item's code for that item",
        "ask which one he means and run it again with that code",
        "Run nothing else that loads, writes or deploys", "Show the screen unchanged, in a code block",
        "type no number yourself",
        "If the output opens with LOAD INTERRUPTED or LAST LOAD FAILED, say so first, in plain words, and point him "
        "to /yq-load", "Change nothing yourself",
        "at most three lines", "no number that is not on the screen",
        # the order list is a suggestion: no purchase order, nothing to a supplier, and what it does not allow for
        "draft no purchase order and write to no supplier", "When the screen says goods on order not deducted",
        "its quantities do not yet allow for goods already on their way",
        "Never present an estimate as a fact", "A rate marked with a star rests on few days in stock",
        "keep those words when you repeat a figure",
        "Never accuse anyone", "to check", "it comes with the movement audit, in a later phase",
    ],
}


def problems_rules(name: str, text: str) -> list[str]:
    """What a skill's text no longer says (pure: the text is an argument, so a changed rule can be tried)."""
    out = [f"missing: {needle}" for needle in MUST_CONTAIN[name] if needle not in text]
    if text.count("$ARGUMENTS") != 1:
        out.append("$ARGUMENTS must appear once, in the command")
    if "--token" in text:
        out.append("a writer command line is typed in the skill")
    if name == "yq-load":
        spots = [text.find(s) for s in ("`LOAD INTERRUPTED at <step>", "`The last load (run <run>) failed at <step>",
                                        "load the price book", "## Step 2", "## Step 3", "## Step 4")]
        if -1 in spots or spots != sorted(spots):
            out.append("a load that did not finish must be dealt with first in Step 1, before the options, and the "
                       "steps stay in order")
    return out


@test("each skill carries its rules: exact commands, the spoken options, the exit codes, the never-run list")
def _():
    for name in MUST_CONTAIN:
        problems = problems_rules(name, read(SKILLS / name / "SKILL.md"))
        assert not problems, (name, problems)


@test("the rules check bites: a rule turned round, a step dropped or a path left out is caught")
def _():
    load, brief = read(SKILLS / "yq-load" / "SKILL.md"), read(SKILLS / "yq-brief" / "SKILL.md")
    turned = (
        ("say what the output says and wait for him", "run the line again until he clicks Yes"),
        ("do not retry, rephrase or wrap it", "try it once more through PowerShell"),
        ("If he himself answered No to Claude Code's question, nothing was loaded: wait for him. Otherwise tell", "Tell"),
        ("double-click `business_data/ops/loads/<run>/go.cmd`", "double-click the `go.cmd` the check named"),
        ("When he says he has answered it, go to Step 3.", ""),
        ("Exit code 0 (it says `load started`): go to Step 3", "Exit code 0: wait for him"),
        ("If it also says to keep the Claude Code window open, tell him that first.", ""),
        ("Exit code 1 or 2, or anything else: show", "Any other result: show"),
        ("FINISHED, FAILED, INTERRUPTED or NOT STARTED", "FINISHED, FAILED or INTERRUPTED"),
        ("- NOT STARTED: the load did not begin. Show the lines, say nothing was loaded and wait.\n", ""),
        ("Do not check again and add no option", "Check again"),
        ("never write the line yourself", "write the line yourself"),
        ("If no `GO: ` line ends the output, say what it says and wait", "If no `GO: ` line ends the output, make one"),
        ("the last load stopped after it had begun to write, so the database may hold part of it", "nothing was loaded"),
        ("this /yq-load starts a new load that replaces it", "the failed load is resumed"),
        ("tell him that first and say plainly", "do not mention"),
        (", once the output ends with a `GO: ` line", ""),
        ("on the line that starts `If Claude may not start it`", "somewhere above"),
        ("and run its new line once", "and run its new line"),
        ("wait for him: never loop", "go on until it loads"),
        ("only when he says so, and only if it starts with `python`", "at once"),
        ("Show the output unchanged", "Say that all is well"),
        ("do not go again", "go again"),
        ("not in the background", "in the background"),
        ("Never edit, move or delete", "Tidy"),
        ("$ARGUMENTS", "$ARGUMENTS $ARGUMENTS"),
    )
    for old, new in turned:
        assert old in load, old
        assert problems_rules("yq-load", load.replace(old, new)), f"not caught: {old!r} -> {new!r}"
    moved = load.replace(resume_step(load) + "\n", "").replace("## Step 2", resume_step(load) + "\n\n## Step 2")
    assert any("dealt with first" in p for p in problems_rules("yq-load", moved))          # the resume after the options
    assert problems_rules("yq-load", load + "\nThen run it with --token.\n")
    for old, new in (("say so first, in plain words, and point him to /yq-load", "carry on"),
                     ("LOAD INTERRUPTED or LAST LOAD FAILED", "LOAD INTERRUPTED"),
                     ("Change nothing yourself", "Clear the lock yourself"), ("Never accuse anyone", "Name who did it"),
                     ("Add a word only when he asked for it", "Add the words you think fit"),
                     ("no number that is not on the screen", "any number"), ("unchanged, in a code block", "tidied")):
        assert old in brief, old
        assert problems_rules("yq-brief", brief.replace(old, new)), f"not caught: {old!r} -> {new!r}"


@test("the /yq-stock rules check bites: a rule turned round or dropped is caught")
def _():
    text = read(SKILLS / "yq-stock" / "SKILL.md")
    assert not problems_rules("yq-stock", text)
    for old, new in (("draft no purchase order and write to no supplier", "draft the purchase order for him"),
                     ("Never present an estimate as a fact", "Round the figures off"),
                     ("its quantities do not yet allow for goods already on their way", "the list is complete"),
                     ("A rate marked with a star rests on few days in stock", "A rate is a rate"),
                     ("say so first, in plain words, and point him to /yq-load", "carry on"),
                     ("Run nothing else that loads, writes or deploys", "Fix what looks wrong"),
                     ("no number that is not on the screen", "any number"), ("Never accuse anyone", "Name who did it"),
                     ("$ARGUMENTS", "$ARGUMENTS $ARGUMENTS")):
        assert old in text, old
        assert problems_rules("yq-stock", text.replace(old, new)), f"not caught: {old!r} -> {new!r}"


@test("the checks bite: broken skills of each kind are caught")
def _():
    good = check_skill("yq-demo", make_skill().encode("utf-8"))
    assert all(not v for v in good.values()), good
    # format
    expect("format", "not valid YAML", description="Shows a demo: with an unquoted colon")
    expect("format", "no frontmatter block", text="no frontmatter here\n")
    expect("format", "byte-order mark", text="\ufeff" + make_skill())
    # ... a key given twice: PyYAML would keep the last one and every other check would pass
    assert yaml.safe_load("a: 1\na: 2") == {"a": 2}
    blanket_first = make_skill().replace("allowed-tools:\n", 'allowed-tools:\n  - "Bash(python *)"\nallowed-tools:\n', 1)
    assert blanket_first.count("allowed-tools:") == 2
    expect("format", "the key 'allowed-tools' is given twice", text=blanket_first)
    expect("format", "the key 'disable-model-invocation' is given twice", folder="yq-load", name="yq-load",
           extra=["disable-model-invocation: false", "disable-model-invocation: true"])
    expect("format", "the key 'description' is given twice", extra=['description: "A second description."'])
    nested = make_skill(extra=["metadata:", '  owner: "a"', '  owner: "b"'])
    expect("format", "the key 'owner' is given twice", text=nested)
    # frontmatter
    expect("frontmatter", "not in double quotes", description="Shows a demo")
    expect("frontmatter", "list item is not in double quotes",
           tools=['"Bash(python -m scripts.ops brief *)"', "PowerShell(python -m scripts.ops brief *)"])
    expect("frontmatter", "is not the folder name", name="yq-other")
    expect("frontmatter", "non-empty string", description='""')
    expect("frontmatter", "characters", description='"' + "x" * 1600 + '"')
    expect("frontmatter", "argument-hint must be a string", hint='["x"]')
    expect("frontmatter", "runs code", extra=['hooks: {}'])
    expect("frontmatter", "runs code or forks", extra=['context: "fork"'])
    expect("frontmatter", "unknown key", extra=['allowed_tools: "x"'])
    # ... keys Claude Code knows but these skills do not use: each would change who starts a skill or how it runs
    for other in ("user-invocable: false", 'paths: "web/**"', 'shell: "powershell"', 'model: "haiku"',
                  'disallowed-tools: "Bash"', 'when_to_use: "Always."', 'effort: "low"', 'arguments: "day"'):
        expect("frontmatter", "is not allowed here", extra=[other])
    expect("frontmatter", "'user-invocable' is not allowed here", folder="yq-load", name="yq-load",
           extra=["disable-model-invocation: true", "user-invocable: false"])      # nobody could start the load
    assert set(make_skill_keys()) <= ALLOWED_KEYS and not (ALLOWED_KEYS & (OTHER_KNOWN_KEYS | CODE_RUNNING_KEYS))
    expect("frontmatter", "would hide the command", name="weekly-review", folder="weekly-review")
    # allowed-tools
    expect("tools", "must be a YAML list", tools=[])
    expect("tools", "has no PowerShell twin", tools=['"Bash(python -m scripts.ops brief *)"'])
    expect("tools", "has no Bash twin", tools=['"PowerShell(python -m scripts.ops brief *)"'])
    expect("tools", "lists an entry twice", tools=GOOD_TOOLS + [GOOD_TOOLS[0]])
    for bad in ('"Bash(python -m scripts.yq_load_go *)"', '"Bash(python *)"', '"Bash(python -m scripts.*)"',
                '"Bash(python -m scripts.refresh*)"', '"Bash(python -m scripts.ops *)"', '"Read(*)"',
                '"Bash(python -m scripts.ops load*)"', '"Bash(python -m scripts.ops brief)"'):
        expect("tools", "is not Bash(python -m scripts.ops <word> *)", tools=[bad] + GOOD_TOOLS)
    expect("tools", "matches: " + WRITER, tools=['"Bash(python -m scripts.yq_load_go*)"'] + GOOD_TOOLS)
    expect("tools", "matches: " + REFRESH, tools=['"PowerShell(python -m scripts.*)"'] + GOOD_TOOLS)
    # flags
    expect("flags", "reserved for the owner-only", extra=["disable-model-invocation: true"])
    expect("flags", "must be true", folder="yq-load", name="yq-load")
    # text
    expect("text", "injection", body="Today: " + "!" + "`date`\n")
    expect("text", "injection", body="```" + "!\ndate\n```\n")
    expect("text", "lines", body="\n".join(["line"] * 80) + "\n")
    expect("text", "typed in the skill", body="Run python -m scripts.yq_load_go --run a --token b\n")


# ── CLAUDE.md ──────────────────────────────────────────────────────────────────────────────────

CLAUDE_REQUIRED = (
    "Where am I?", ".claude/worktrees", "/yq-load", "scripts.refresh", "scripts.ingest", "scripts.load_supabase",
    "scripts.apply_sql", "docs/CLAUDE.md", "docs/OPS_SKILLS.md",
    # a code worktree: the main-folder rules are off, and nothing that reaches production runs from it
    "The main-folder rules below do not apply", "or a deploy script from here", "would reach production",
    # the main folder is named by what it holds, never by exclusion: a clone or a worktree elsewhere is neither
    "if this folder holds `business_data/` and the drop folder (`Uplaods/`, or `Uploads/`), it is the main project folder",
    "It stays on branch `main`", "Any other checkout", "is a code checkout too",
    "run none of the commands named in the first rule",
    # the main folder: a request in plain words is answered with the line to type
    "Reports are loaded only through /yq-load", "answer with the exact line to type: `/yq-load <ddmmyy>`",
    "Never run scripts.refresh, scripts.ingest or scripts.load_supabase directly",
    # a rehearsal is the one run of the load program he asks for in plain words: the page says how
    "A rehearsal of the load, when he asks for one in plain words, is in `docs/OPS_SKILLS.md`, section 2: it loads nothing",
    # where things stand, asked in plain words
    "Asked whether the data is up to date or what is waiting, it runs `python -m scripts.ops status` (read-only)",
    # a load that did not finish: said first, settled by /yq-load, never by hand (true in every kind of checkout)
    "When a command opens with `LOAD INTERRUPTED` or `LAST LOAD FAILED`, tell the owner first",
    "typing /yq-load in the main folder is what settles it",
    "Never clear a lock or a load's folder unless he asks for exactly that",
)
CLAUDE_WHERE_ORDER = (".claude/worktrees", "if this folder holds `business_data/`", "Any other checkout")


def skill_refs(text: str) -> set[str]:
    return set(SKILL_REF.findall(text))


def problems_claude_md(text: str, folders: set[str]) -> list[str]:
    out: list[str] = []
    lines = text.splitlines()
    if len(lines) >= 30:
        out.append(f"{len(lines)} lines: CLAUDE.md stays under 30")
    first = next((ln for ln in lines if ln.strip()), "")
    if "Where am I?" not in first:
        out.append("CLAUDE.md must start with 'Where am I?'")
    for needle in CLAUDE_REQUIRED:
        if needle not in text:
            out.append(f"CLAUDE.md does not mention {needle}")
    where = section(text, "Where am I?")
    spots = [where.find(s) for s in CLAUDE_WHERE_ORDER]
    if -1 in spots or spots != sorted(spots):
        out.append("'Where am I?' must go: a worktree under .claude/worktrees, then the main folder by what it holds, "
                   "then any other checkout")
    if re.search(r"Otherwise,? this is the main", text):
        out.append("the main folder is named by exclusion: that is false in another clone or a worktree elsewhere")
    # a worktree's commands read the main folder's lock too: the rule for a load that did not finish holds everywhere
    if "`LOAD INTERRUPTED` or `LAST LOAD FAILED`" not in section(text, "Standing rules"):
        out.append("the rule for a load that did not finish must stand under 'Standing rules', not among the "
                   "main-folder rules a worktree sets aside")
    for command in re.findall(r"`(python [^`]*)`", text):          # a command the file spells is read-only and built
        if not command.startswith("python -m scripts.ops ") or command.split()[3] not in cli.COMMANDS:
            out.append(f"CLAUDE.md spells a command that is not a read-only scripts.ops command: {command}")
    refs = skill_refs(text)
    for name in sorted(refs - folders):
        out.append(f"CLAUDE.md names /{name}, which has no skill folder")
    for name in sorted(folders - refs):
        out.append(f"CLAUDE.md does not list the skill /{name} that exists")
    return out


@test("CLAUDE.md: short, 'Where am I?' first and true in every kind of checkout, /yq-load, skill map = skill folders")
def _():
    path = ROOT / "CLAUDE.md"
    assert path.is_file(), "CLAUDE.md is missing"
    problems = problems_claude_md(read(path), skill_folders())
    assert not problems, problems


@test("the CLAUDE.md check bites")
def _():
    base = read(ROOT / "CLAUDE.md")
    folders = skill_folders()
    assert not problems_claude_md(base, folders)
    assert any("under 30" in p for p in problems_claude_md(base + "x\n" * 30, folders))
    assert any("start with" in p for p in problems_claude_md("# Notes\n" + base, folders))
    assert any(".claude/worktrees" in p for p in problems_claude_md(base.replace(".claude/worktrees", "worktrees"), folders))
    assert any("scripts.load_supabase" in p for p in problems_claude_md(base.replace("scripts.load_supabase", "x"), folders))
    assert any("no skill folder" in p for p in problems_claude_md(base + "\n- `/yq-" + "week`: later\n", folders))
    assert any("does not list" in p for p in problems_claude_md(base.replace("/yq-brief", "/brief"), folders))
    # the file as it was reviewed: every folder that is not under .claude/worktrees was called the main folder
    holds = ("Otherwise, if this folder holds `business_data/` and the drop folder (`Uplaods/`, or `Uploads/`), it is the "
             "main project folder.")
    assert holds in base
    reviewed = base.replace(holds, "Otherwise this is the main project folder.")
    assert any("by exclusion" in p for p in problems_claude_md(reviewed, folders))
    other = next(ln for ln in base.splitlines() if ln.startswith("- Any other checkout"))
    assert any("Any other checkout" in p for p in problems_claude_md(base.replace(other + "\n", ""), folders))
    swapped = base.replace(other + "\n", "").replace("- If the working folder", other + "\n- If the working folder")
    assert any("must go" in p for p in problems_claude_md(swapped, folders))
    # each sentence a session acts on, dropped one at a time
    for sentence in ("It stays on branch `main`; code changes are made in worktrees.",
                     "When asked in plain words to load reports, answer with the exact line to type: `/yq-load <ddmmyy>`.",
                     "or a deploy script from here: the main folder's `.env` is found automatically, so they would reach "
                     "production",
                     "Never run scripts.refresh, scripts.ingest or scripts.load_supabase directly.",
                     "A rehearsal of the load, when he asks for one in plain words, is in `docs/OPS_SKILLS.md`, section 2: "
                     "it loads nothing.",
                     "Asked whether the data is up to date or what is waiting, it runs `python -m scripts.ops status` "
                     "(read-only).",
                     "typing /yq-load in the main folder is what settles it.",
                     "Never clear a lock or a load's folder unless he asks for exactly that."):
        assert sentence in base, sentence
        assert problems_claude_md(base.replace(sentence, ""), folders), f"not caught: {sentence}"
    # the rule for a load that did not finish, moved among the main-folder rules; a command that writes, spelt out
    rule = next(ln for ln in base.splitlines() if ln.startswith("- When a command opens with `LOAD INTERRUPTED`"))
    moved = base.replace(rule + "\n", "").replace("## Skills", rule + "\n\n## Skills")
    assert any("must stand under 'Standing rules'" in p for p in problems_claude_md(moved, folders))
    assert any("not a read-only scripts.ops command" in p for p in problems_claude_md(
        base + "- To load by hand: `" + "python -m scripts." + "refresh <folder>`\n", folders))
    assert any("not a read-only scripts.ops command" in p for p in problems_claude_md(
        base + "- `python -m scripts.ops " + "reload`\n", folders))


# ── docs/OPS_SKILLS.md and docs/FOCUS_EXPORT.md ─────────────────────────────────────────────────

HEADINGS = ("1. What each command is for", "2. Paste once and rehearse first", "3. What the load check's decisions mean",
            "4. Definitions used by the brief", "5. Where things are kept", "6. What these commands cannot do",
            "7. For the developer")
# What the page tells the owner, as the programs really behave:
OPS_DOC_SAYS = (
    # what the pasted rules do, and no more than that
    "from then on Claude Code asks you before a load that is started in its usual form",
    "It is a second question, not a lock: a command typed another way is not caught by these rules",
    "The Windows box is the real gate", "The two older loaders have no box",
    # a rule that pre-approves the load: the program refuses, in a rehearsal as well
    "it refuses to start, names the entry to delete and the file it is in, and loads nothing",
    # rehearse first
    "**Rehearse first.** Before the first real load, ask Claude to rehearse it",
    "A rehearsal that was cut off needs nothing from you",
    # one check, one run: a new check retires the earlier ones of the same folder
    "A new check of the same folder retires the earlier checks of it that were never loaded",
    "their go lines no longer work", "their `go.cmd` is removed",
    "their copies move to `business_data/staging/superseded/`",
    # a load that was cut off: /yq-load starts that same load again, the box says RESUME, nothing else loads meanwhile
    "Claude does not check the folder again: it starts that same load once more",
    "the Windows box says **RESUME**", "no other load can start until it is resumed",
    "the cut-off load is closed instead and the folder is checked afresh",
    # a load that failed after it had begun to write: a new load replaces it
    "The last load stopped after it had begun to write", "it starts a new load, and that load replaces the failed one",
    # a go that Claude Code refuses: the file to double-click, by its place, and the way back
    "`go.cmd`, in `business_data/ops/loads/<run>/` of the project folder",
    "Then tell Claude that you have answered the box",
    # the month-end Ledger comes with the first load of the new month (docs/FOCUS_EXPORT.md)
    "with the first load of a new month, the Ledger report of the month just ended",
    # for the developer: the frontmatter, the forms a rule can take, the limit of the ask rules, the seam, the gate
    "These five keys and no other, and none of them twice",
    "in any form a permission rule can take (a bare tool name, a `*` anywhere, the older `prefix:*`)",
    "Another spelling of the same program", "is not matched", "the Windows box still asks, whatever the spelling",
    "after which the check's last line is that run's own go line, so the resume is Step 2 unchanged",
    "Its name list is `business_data/ops/denylist.json`",
    "with an invented list in a temporary folder, and never reads yours",
    # the e-mail and the team message: set up once, sent once, never by a program to WhatsApp
    "**Also once: the backup password and the e-mail.**", "`cmdkey /generic:YQ_BACKUP_PASSWORD /user:yq /pass`",
    "`cmdkey /generic:YQ_RESEND_API_KEY /user:resend /pass`", "a load without the password is refused before it "
    "writes anything", "Each backup is read back whole before the load goes on", "a load's e-mail goes once",
    "They post nothing to WhatsApp", "the team message is sent by you", "You send it yourself",
    # the two safety gaps of phase 1, closed, and what is left of the second
    "a loader row more than this run's own starts of its refresh, or an upload in `audit_log` since the go first "
    "started", "closes the cut-off load", "by a load or a rehearsal", "the slowest of the last five refreshes",
    "a lock from another PC is never judged, resumed or cleared on this one",
    "two PCs that start a load within OneDrive's sync delay of each other",
    # the seam after the load
    "`RUNNING at delivery`", "A line that holds `Its retry: ` is followed by the line that sends the e-mail again; "
    "it loads nothing", "Nothing of it changes the load's result, journal or lock",
)
OPS_DOC_NO_LONGER = (
    "it offers to carry on from where it stopped", "double-click the `go.cmd` it names",
    "at month end the Ledger report as well",
    # six rules that match one spelling of three programs do not ask "before every load"
    "asks you before every load", "asks before every load",
    # the resume is no longer read from a file: the check hands the go line over
    "and otherwise from `business_data/ops/loads/<run>/go.cmd`",
    # the two limits of phase 1 are closed; the commands send the e-mail after a load now
    "Two limits are known and left for a later step", "A resume does not look at the database",
    "send anything (a message, an e-mail, a workbook)",
)


def decision_words() -> tuple[str, ...]:
    """The words of the load check's Decision column, in the order of the plan's table."""
    return tuple(load_check.WORDS[v] for v in loadplan.VERDICTS)


def ask_block(text: str):
    for m in re.finditer(r"```(?:json)?[ \t]*\n(.*?)\n```", text, re.S):
        if '"permissions"' in m.group(1):
            return json.loads("{" + m.group(1) + "}")
    return None


def problems_ops_doc(text: str, folders: set[str]) -> list[str]:
    out: list[str] = []
    for rule in SIX_ASK_RULES:
        if '"' + rule + '"' not in text:
            out.append(f"the ask rule {rule} is not in the page")
    try:
        block = ask_block(text)
    except ValueError as exc:
        block = None
        out.append("the settings block is not valid JSON: " + str(exc))
    if block is None:
        out.append("no settings block with a 'permissions' member")
    elif block != {"permissions": {"ask": SIX_ASK_RULES}}:
        out.append("the settings block is not exactly the six ask rules in a permissions.ask list")
    for head in HEADINGS:
        if not re.search(r"^## " + re.escape(head), text, re.M):
            out.append(f"missing the section '{head}'")
    for label in decision_words():
        if "| **" + label + "** |" not in text:
            out.append(f"the decision {label} is not explained")
    for phrase in SPOKEN:
        if '"' + phrase + '"' not in text:
            out.append(f"what you can say is missing: {phrase}")
    for needle in ("business_data/ops", "exports/ops", "exports/reports", "config.json", "KPI step", "month-end week",
                   "normal range", "Stock Request", "counted stock", "/reload-skills", "docs/FOCUS_EXPORT.md",
                   "none of it is in git"):
        if needle.lower() not in text.lower():
            out.append(f"the page does not mention {needle}")
    whole = flat(text)                                       # whole sentences, however the lines are wrapped
    for needle in OPS_DOC_SAYS:
        if needle not in whole:
            out.append(f"the page does not say: {needle}")
    for gone in OPS_DOC_NO_LONGER:
        if gone in whole:
            out.append(f"the page still promises what the programs do not do: {gone}")
    refs = skill_refs(text)
    for name in sorted(refs - folders):
        out.append(f"the page names /{name}, which has no skill folder")
    for name in sorted(folders - refs):
        out.append(f"the page does not describe the skill /{name} that exists")
    return out


@test("docs/OPS_SKILLS.md: the six ask rules exactly, decisions, definitions, places, limits")
def _():
    path = ROOT / "docs" / "OPS_SKILLS.md"
    assert path.is_file(), "docs/OPS_SKILLS.md is missing"
    problems = problems_ops_doc(read(path), skill_folders())
    assert not problems, problems


@test("the six ask rules cover the writer and the two old loaders in both shells, and no read-only command")
def _():
    for command in (WRITER, REHEARSAL, WORKER, REFRESH, LOAD_SUPABASE):
        for tool in ("Bash", "PowerShell"):
            hits = [r for r in SIX_ASK_RULES if r.startswith(tool + "(") and rule_matches(r, command)]
            assert len(hits) == 1, (tool, command, hits)
    for rule in SIX_ASK_RULES:
        assert re.fullmatch(r"(Bash|PowerShell)\(python -m scripts\.[a-z_]+\*\)", rule), rule
        for command in READ_ONLY_COMMANDS:
            assert not rule_matches(rule, command), (rule, command)
    for p in skill_files():                                  # what the skills pre-approve is never asked about
        _problems, fm, _block, _body, _text = parse_skill(p.read_bytes())
        for entry in fm["allowed-tools"]:
            word = TOOL_FORM.match(entry).group(2)
            for sample in (f"python -m scripts.ops {word}", f"python -m scripts.ops {word} x --y"):
                assert not any(rule_matches(r, sample) for r in SIX_ASK_RULES), (p.parent.name, sample)


@test("the ask rules match the command as it is spelt here, and the page says which spellings they do not cover")
def _():
    # the spellings the skill, go.cmd and CLAUDE.md use are asked about ...
    with temp_root() as root:
        typed = [ln for ln in read(loadplan.write_go_cmd(RUN, SEAM_TOKEN, root)).splitlines() if ln.startswith("python")]
    assert typed == [loadplan.go_line(RUN, SEAM_TOKEN)]
    for command in typed + [REFRESH, LOAD_SUPABASE]:
        assert any(rule_matches(r, command) for r in SIX_ASK_RULES), command
    # ... other spellings of the same three programs are not: the page must not promise more than the rules do
    other = ("py -m scripts.refresh x", "python -X utf8 -m scripts.yq_load_go --run 010126_0900 --token x",
             "python scripts/refresh.py x", "py -m scripts.load_supabase")
    for command in other:
        assert not any(rule_matches(r, command) for r in SIX_ASK_RULES), command
    page = " ".join(read(ROOT / "docs" / "OPS_SKILLS.md").split())
    for needle in ("Another spelling of the same program (`py -m ...`, `python -X utf8 -m ...`, the path of the file) "
                   "is not matched", "the Windows box still asks, whatever the spelling",
                   "the only other guard is the standing order in `CLAUDE.md` never to run them"):
        assert needle in page, needle
    assert "Never run scripts.refresh, scripts.ingest or scripts.load_supabase directly" in read(ROOT / "CLAUDE.md")


@test("the OPS_SKILLS check bites")
def _():
    base = read(ROOT / "docs" / "OPS_SKILLS.md")
    folders = skill_folders()
    assert not problems_ops_doc(base, folders)
    rule = SIX_ASK_RULES[2]
    assert any(rule in p for p in problems_ops_doc(base.replace('"' + rule + '"', '"' + rule + ' "'), folders))
    assert any("not exactly the six" in p for p in problems_ops_doc(base.replace('    "' + rule + '",\n', ""), folders))
    assert any("not valid JSON" in p for p in problems_ops_doc(base.replace(
        '"PowerShell(python -m scripts.load_supabase*)"\n', '"PowerShell(python -m scripts.load_supabase*)",\n'), folders))
    assert any("NOT LOADED" in p for p in problems_ops_doc(base.replace("**NOT LOADED**", "NOT LOADED"), folders))
    assert any("no skill folder" in p for p in problems_ops_doc(base + "\nType /yq-" + "week.\n", folders))
    assert any("does not describe" in p for p in problems_ops_doc(base.replace("/yq-brief", "/brief"), folders))
    assert any("2. Paste once" in p for p in problems_ops_doc(base.replace("## 2. Paste once", "## Paste once"), folders))
    assert any("NEEDS YOUR DECISION" in p for p in problems_ops_doc(
        base.replace("| **NEEDS YOUR DECISION** |", "| **ASK** |"), folders))
    assert any("leave the price book out" in p for p in problems_ops_doc(
        base.replace('"leave the price book out"', '"skip it"'), folders))
    # the page as it was reviewed: a resume the check "offers", a go.cmd it "names", the Ledger "at month end",
    # a question "before every load"; and what the page says now, turned round
    for now, was in (("the Windows box says **RESUME**", "it offers to carry on from where it stopped"),
                     ("`go.cmd`, in", "double-click the `go.cmd` it names, in"),
                     ("These five keys and no other, and none of them twice", "These keys"),
                     ("Another spelling of the same program", "Every spelling of the same program"),
                     ("with the first load of a new month, the Ledger report of the month just ended",
                      "at month end the Ledger report as well"),
                     ("a load that is started in its usual form", "every load"),
                     ("It is a second question, not a lock", "It is a lock"),
                     ("retires the earlier checks of it that were never loaded", "keeps the earlier checks of it"),
                     ("that load replaces the failed one", "the failed load carries on"),
                     ("needs nothing from you", "has to be cleared by hand"),
                     ("and never reads yours", "and reads yours when it is there")):
        wrapped = re.compile(r"\s+".join(re.escape(word) for word in now.split()))      # however the lines break
        assert len(wrapped.findall(base)) == 1, now
        problems = problems_ops_doc(wrapped.sub(lambda _m: was, base), folders)
        assert any("does not say" in p or "still promises" in p for p in problems), f"not caught: {was}"


def section(text: str, heading: str) -> str:
    m = re.search(r"^#{2,3} " + re.escape(heading) + r".*?(?=^#{1,3} |\Z)", text, re.S | re.M)
    return m.group(0) if m else ""


# What the month-end section says, as the plan asks: the Ledger (all accounts, the whole month) goes in the drop
# folder with the first load of the new month, and holds the sales returns, the receipts and the credit notes.
MONTH_END_SAYS = ("**Ledger** (all accounts, the whole month)", "drop folder", "never on the Data page",
                  "Sales returns with their values, receipts and credit notes", "/yq-month",
                  "on the first morning of the new month", "as on the last day of the month just ended",
                  "so that it covers the whole month", "`/yq-load` lists it as", "*NOT LOADED*", "prints its \"as on\" date",
                  "**the load never loads it**", "Keep it out of the Data-page upload too")
# The rest of the file, which this work does not touch: its headings, and how many reports each table lists.
FOCUS_EXPORT_KEPT = ("# Focus → YQ Portal — daily data upload", "## What to export",
                     "### Daily (6 reports — export these every day, date range **up to yesterday**)",
                     "### Weekly / occasional (3 reports — change rarely)",
                     "### Do NOT export (the portal ignores these — they add nothing)", "## How to export (in Focus)",
                     "## How to upload (in the YQ dashboard)", "### What the result means (21-Sep-2026)",
                     "## Phase 2 (later — after ~1 month of Phase 1)")
FOCUS_EXPORT_ROWS = {"Daily": 6, "Weekly / occasional": 3, "Month end": 1}       # report rows of each table
DO_NOT_EXPORT = ("- **Stock_movement** (plain)", "- The other 3 **Customer_ageing** variants", "- **Ledger_detail**",
                 "- The **Mobile Accessories Monthly** workbooks")


def problems_focus_export(text: str) -> list[str]:
    out: list[str] = []
    do_not = section(text, "Do NOT export")
    if not do_not:
        out.append("the 'Do NOT export' list is gone")
    if "Ledger / Ledger_detail" in do_not or re.search(r"^- \*\*Ledger\*\*", do_not, re.M):
        out.append("the Ledger is still in the do-not-export list")
    bullets = [ln for ln in do_not.splitlines() if ln.startswith("- ")]
    if do_not and (len(bullets) != len(DO_NOT_EXPORT)
                   or not all(b.startswith(start) for b, start in zip(bullets, DO_NOT_EXPORT))):
        out.append("the do-not-export list changed: it holds " + "; ".join(b[:40] for b in bullets))
    month = section(text, "Month end")
    if not month:
        out.append("no month-end section for the Ledger")
    for needle in MONTH_END_SAYS:
        if month and needle not in flat(month):
            out.append(f"the month-end section does not say: {needle}")
    if re.search(r"the last day, or", month):
        out.append("the month-end section lets the Ledger be exported on the last day itself: that day's receipts and "
                   "returns would be missing")
    for anchor in FOCUS_EXPORT_KEPT:
        if anchor not in text.splitlines():
            out.append(f"the rest of the file changed: the line '{anchor}' is gone")
    for heading, rows in FOCUS_EXPORT_ROWS.items():
        found = len(re.findall(r"^\| \*\*", section(text, heading), re.M))
        if found != rows:
            out.append(f"the rest of the file changed: the '{heading}' table lists {found} reports, not {rows}")
    spots = [text.find("### " + h) for h in ("Daily", "Weekly / occasional", "Month end", "Do NOT export")]
    if -1 in spots or spots != sorted(spots):
        out.append("the month-end section is not between the weekly reports and the do-not-export list")
    return out


@test("docs/FOCUS_EXPORT.md: the Ledger is a month-end export for the drop folder; the load never loads it")
def _():
    problems = problems_focus_export(read(ROOT / "docs" / "FOCUS_EXPORT.md"))
    assert not problems, problems
    base = read(ROOT / "docs" / "FOCUS_EXPORT.md")
    assert any("still in the do-not-export" in p for p in problems_focus_export(
        base.replace("- **Ledger_detail**", "- **Ledger / Ledger_detail**")))
    assert any("no month-end section" in p for p in problems_focus_export(base.replace("### Month end", "### Other")))
    assert any("never loads" in p for p in problems_focus_export(base.replace("never loads", "loads")))
    # the row as it was reviewed: exported on the last day, the Ledger misses that day (it is an "as on date" report)
    when = "on the first morning of the new month, **as on the last day of the month just ended**"
    assert when in base
    reviewed = base.replace(when, "at month end (the last day, or the first morning of the next month)")
    assert any("on the last day itself" in p for p in problems_focus_export(reviewed))
    assert any("as on the last day" in p for p in problems_focus_export(
        base.replace("**as on the last day", "**on the last day")))
    # the daily rule of the same file ("up to yesterday") is what the month-end row now agrees with
    assert "date range **up to yesterday**" in base and "Set the **date range up to yesterday**" in base
    # the words the plan asks for stand in the file as they are: the name gate no longer takes one of them for a name
    assert "receipts and credit notes" in section(base, "Month end")
    # the load check's word for it is the one this file gives, and its line says that the export is never loaded
    assert "*" + load_check.WORDS["not_loaded"] + "*" in section(base, "Month end")
    assert "the Ledger export is never loaded by this command" in read(OPS / "load_check.py")
    # ... and the rest of the file is as it was: a report row lost, a heading reworded or a bullet added is caught
    row = next(ln for ln in base.splitlines() if ln.startswith("| **Stock_ledger** |"))
    assert any("'Daily' table lists 5 reports" in p for p in problems_focus_export(base.replace(row + "\n", "")))
    assert any("'## How to export (in Focus)' is gone" in p for p in problems_focus_export(
        base.replace("## How to export (in Focus)", "## How to export")))
    last = "- The **Mobile Accessories Monthly**"
    assert any("do-not-export list changed" in p for p in problems_focus_export(
        base.replace(last, "- **Stock_balance** too\n" + last)))
    assert any("do-not-export list changed" in p for p in problems_focus_export(
        base.replace(next(ln for ln in base.splitlines() if ln.startswith(DO_NOT_EXPORT[-1])) + "\n", "")))
    moved = base.replace(section(base, "Month end"), "") + "\n" + section(base, "Month end")
    assert any("not between the weekly reports and the do-not-export list" in p for p in problems_focus_export(moved))


# ── nothing private, nothing unbuilt ───────────────────────────────────────────────────────────────

@test("the scan for amounts, long digit runs and e-mail addresses finds what it should and agrees with selftest")
def _():
    long_number = "1234" + "5678"                             # built at run time: no such literal in this file
    address = "someone" + "@" + "example.com"
    flagged = [money(1234), money(12345), money(1000), "total " + money(98765, "001") + " ex-VAT", f"{1234}.{500}",
               "call " + long_number, "mail me at " + address, "the Sell-" + "Through rate", "sell " + "through"]
    for sample in flagged:
        assert scan_text(sample), sample
    clean = [money(999), "1,234.50", "version 3.1.5", "0.001", "row 1234 of 5678", "12,499 lines", "a minimum of 7",
             "071026 and 010126_0900", "ttl 600000"]
    for sample in clean:
        assert not scan_text(sample), sample
    for sample in flagged[:5] + clean:                        # the same amount rule as `selftest --repo-clean`
        assert bool(AMOUNT_3DP.search(sample)) == bool(selftest.scan_text(sample, [])), sample


@test("CLAUDE.md, the docs and the skills hold no 3-decimal amount of 1,000 or more, no sell-through, no long number")
def _():
    texts = committed_texts()
    assert len(texts) >= 5, sorted(texts)
    for name, text in texts.items():
        assert not scan_text(text), (name, scan_text(text))
    me = read(Path(__file__))                  # this file is staged too: no amount and no long number in it
    assert not scan_text(me, words=False, address=False), scan_text(me, words=False, address=False)


# The only numbers of two or more digits the skills, CLAUDE.md and the page may state: fixed parts of the design
# and of this work. Every limit, share, step and target is a value in business_data/ops/config.json and is named
# on the page by what it is, never by its figure.
DESIGN_NUMBERS = {
    "12": "the full weeks the normal range is worked out from; the months behind the average month",
    "30": "the days behind the average business day; the line cap of CLAUDE.md",
    "60": "the days of sales that show a sold-out item was still asked for (the Command Centre's rule)",
    "70": "the line cap of a SKILL.md",
    "600000": "the tool timeout, in milliseconds",
    "90": "the days behind an item's daily rate (the stock command); its first band of age and of cover",
    "180": "the stock command's second band of age and of cover",
}
A_NUMBER = re.compile(r"\d[\d,.]*\d|\d")
VALUE_SHAPES = (("an amount in BHD", re.compile(r"\bBHD\s*\d")), ("a percentage", re.compile(r"\d\s*%")),
                ("a count of business days", re.compile(r"\d\s+(?:business|working)\s+days?", re.I)))


def problems_values(text: str) -> list[str]:
    """A limit, a target or an amount stated in a text that must state none: any number of two or more digits
    that is not a design number, any decimal, any thousands separator, any BHD amount, percentage or count of
    business days. Single digits pass: they number the steps and sections and name the 3- and 7-day windows."""
    out: list[str] = []
    for token in sorted(set(A_NUMBER.findall(text))):
        if len(token) > 1 and token not in DESIGN_NUMBERS:
            out.append(f"the number {token}")
    for what, shape in VALUE_SHAPES:
        if shape.search(text):
            out.append(what)
    return out


@test("the skills, CLAUDE.md and the page state no limit, target or amount: their only numbers are design numbers")
def _():
    texts = {name: text for name, text in committed_texts().items() if name != "docs/FOCUS_EXPORT.md"}
    assert len(texts) >= 4, sorted(texts)
    for name, text in texts.items():
        assert not problems_values(text), (name, problems_values(text))
    assert "config.json" in texts["docs/OPS_SKILLS.md"]      # where the page sends him for every such value
    # ... and the check bites. The figures are made up here, so that no working value stands in this file.
    lead, step, share = 40 + 1, f"{4 * 1111:,}", f"{1 / 4:.2f}"
    for sample in (f"a lead time of {lead} days", f"the KPI step is {step}", f"a gap above {share} holds it back",
                   f"BHD {2 + 3} a day", f"below {lead + 2}% of last month", f"below {4 + 4}% of it",
                   f"silent for {4 + 4} business days", f"the newest {lead} archives are kept"):
        assert problems_values(sample), sample
    for sample in ("the last 3 days, the last 7 days", "the 12 full weeks before", "under 70 lines", "Exit code 4",
                   "timeout 600000 ms", "the last or the first five business days", "section 2"):
        assert not problems_values(sample), sample


def bare_placeholders(text: str) -> list[str]:
    """<ddmmyy>-style placeholders outside code: Markdown viewers take them for HTML and drop them."""
    out: list[str] = []
    fenced = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            fenced = not fenced
        elif not fenced:
            out += re.findall(r"<[A-Za-z][^>]*>", re.sub(r"`[^`]*`", "", line))
    return out


@test("placeholders such as <ddmmyy> sit inside code, so that Markdown viewers do not hide them")
def _():
    for name in ("CLAUDE.md", "docs/OPS_SKILLS.md"):
        assert not bare_placeholders(read(ROOT / name)), (name, bare_placeholders(read(ROOT / name)))
    for p in skill_files():
        _problems, _fm, _block, body, _text = parse_skill(p.read_bytes())
        assert not bare_placeholders(body), (p.parent.name, bare_placeholders(body))
    assert bare_placeholders("a box titled YQ load <" + "ddmmyy> opens") == ["<ddmmyy>"]
    assert not bare_placeholders("a box titled `YQ load <ddmmyy>` opens\n```\n<x>\n```")


@test("no skill, doc or CLAUDE.md line carries a phone number, an ID number or an e-mail address")
def _():
    for name, text in committed_texts().items():
        for line in text.splitlines():
            assert not EMAIL.search(line) and not LONG_DIGITS.search(line), (name, line[:60])


# ── .gitignore and the requirements files ──────────────────────────────────────────────────────────

def git_ignore_rule(path: str):
    """The .gitignore pattern that makes git ignore `path` ('' when none does; rules only, the file need not
    exist); None without git. Read-only: `git check-ignore` changes nothing."""
    git = shutil.which("git")
    if not git:
        return None
    try:
        r = subprocess.run([git, "-C", str(ROOT), "check-ignore", "-v", "--no-index", "--", path],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode == 1:
        return ""
    m = re.match(r"^.*?:\d+:(.*)$", r.stdout.split("\t")[0].strip())      # <file>:<line>:<pattern> TAB <path>
    return m.group(1) if (r.returncode == 0 and m) else None


def git_ignored(path: str):
    """True / False from git check-ignore; None without git."""
    rule = git_ignore_rule(path)
    return None if rule is None else bool(rule)


@test(".gitignore ignores /Uplaods/, /Uploads/, Jahez/ and /Jahez*/ and kept its earlier rules")
def _():
    text = read(ROOT / ".gitignore")
    lines = {ln.strip() for ln in text.splitlines()}
    for needed in ("/Uplaods/", "/Uploads/", "Jahez/", "/Jahez*/"):
        assert needed in lines, f".gitignore lacks {needed}"
    for kept in (".env", "business_data/", "exports/", "Focus ERP*/", "*.xlsx", "backups/",
                 "/[0-9][0-9][0-9][0-9][0-9][0-9]/"):
        assert kept in lines, f".gitignore lost {kept}"
    # the comment must not say that the dated folders inside the drop folder were ignored before: they were not
    assert "already caught above" not in text
    dated_pdf = "Uplaods/010126/supplier_invoice.pdf"
    sample_ignored = ("Uplaods/supplier_invoice.pdf", "Uplaods/photos/item_01.jpg", dated_pdf, "Uploads/notes.txt",
                      "Jahez/terms.docx", "Jahez-pack/terms.pdf")
    sample_tracked = ("CLAUDE.md", "docs/OPS_SKILLS.md", ".claude/skills/yq-load/SKILL.md",
                      "tests/test_ops_skills.py", "requirements-dev.txt")
    if git_ignored(sample_ignored[0]) is None:
        print("SKIP  git check-ignore is not available here (the .gitignore lines were checked)")
        return
    for path in sample_ignored:
        assert git_ignored(path) is True, f"git would add {path}"
    for path in sample_tracked:
        assert git_ignored(path) is False, f"git would ignore {path}"
    # a supplier PDF in a dated folder inside the drop folder is kept out by the new rule and by no older one:
    # the dated-folder rule is anchored at the top of the repository
    assert git_ignore_rule(dated_pdf) == "/Uplaods/", git_ignore_rule(dated_pdf)
    assert git_ignore_rule("010126/supplier_invoice.pdf") == "/[0-9][0-9][0-9][0-9][0-9][0-9]/"


@test("XlsxWriter is pinned in requirements-dev.txt only, never in the API image")
def _():
    def pins(name: str) -> list[str]:
        return [ln for ln in read(ROOT / name).splitlines() if re.match(r"^\s*xlsxwriter\b", ln, re.I)]
    assert any(re.match(r"^XlsxWriter==3\.2\.9(\s|$)", ln) for ln in pins("requirements-dev.txt")), \
        pins("requirements-dev.txt")
    assert len(pins("requirements-dev.txt")) == 1
    assert not pins("requirements.txt") and not pins("requirements.lock")
    assert "-r requirements.txt" in read(ROOT / "requirements-dev.txt")        # the file kept its first rules


# ── last: what this file itself touched ─────────────────────────────────────────────────────────────

# Modules that would mean a test here reached further than it says: the portal (app.*, which reads the project's
# .env), the .env reader itself, a database driver or client, the report parsers of the engine and the libraries
# they read workbooks with. Every check, status and go run above ends before any of them is needed.
NOT_IMPORTED = ("app", "dotenv", "psycopg", "supabase", "httpx", "pandas", "openpyxl", "scripts.ingest",
                "scripts.refresh", "scripts.load_supabase", "scripts.verify_numbers", "scripts.ops.sales")


@test("LAST: this file imported no portal module, no .env reader, no database driver and no report parser")
def _():
    loaded = sorted(name for name in sys.modules
                    if any(name == bad or name.startswith(bad + ".") for bad in NOT_IMPORTED))
    assert not loaded, f"imported while these tests ran: {loaded[:12]}"
    assert "scripts.ops.load_check" in sys.modules and "scripts.yq_load_go" in sys.modules      # ... and what it did use


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
