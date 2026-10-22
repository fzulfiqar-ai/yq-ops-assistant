"""Release r10, "the long-number mask": every run of 10 or more digits in a Focus narration is masked (its last 3
digits kept), in the parser (scripts/text_mask.py) and in its SQL twin (scripts/r10_long_number_mask_migration.sql).

    python -m tests.test_r10_long_number_mask

Same lightweight runner as tests/test_r9_stock_moves.py (no pytest). No database, no network, no .env: SUPABASE_URL,
SUPABASE_KEY and DATABASE_URL are pinned to ci.invalid BEFORE any import (a worktree under the main checkout would
otherwise find the real .env). Every number and name is invented; the digit runs are built at run time.

Covered:
  * the Python mask: every run length from 1 to 25 at the start, in the middle and at the end of a text, glued to
    letters, between spaces or signs: 8 digits or fewer stay, exactly 9 become 6 stars and the last 3, 10 or more 12
    stars and the last 3; several runs in one text, each on its own; masking twice changes nothing, masked text stays as
    it is and holds no run of 9 digits; what stays by design (order and invoice numbers) and the known limits (grouped
    digits, Arabic-Indic digits); the long pattern is the review's, the 9-digit rule R7b's; scripts.ingest re-exports
    the very same objects; the module says the rule and its limits;
  * the next load stays idempotent: the data step run on what is stored today (text masked by R7b's rule) gives what
    the r10 parser gives from the raw cell, for every run length; parse_stock's row_hash and both tables' keys are the
    same whichever mask runs (the hash is made from the raw cells), so the ledger's range replace and the order lines'
    upsert match the stored rows and only the narration text can differ;
  * the migration and its reverse, read as text (comments set aside): ASCII only, lock_timeout first, no CASCADE, no
    security_invoker, no DROP, no COMMIT (apply_sql --rehearse rolls each back), a self-check in each, no GRANT, the two
    revokes plain statements and never inside a DO block; every statement outside a DO block parses as Postgres
    (sqlglot, the version the API pins);
  * v_sales: r7_narration_mask_migration.sql's statement verbatim but the long pattern, the comment naming r10; the
    reverse is r7's statement and comment verbatim and says the stored narrations cannot come back;
  * the data step: r7's and r9's statements verbatim but the long pattern (order_lines, stock_movements, the batch
    importer's copies of both targets); nothing else written, row_hash never;
  * every SQL spelling of the mask (the view, the data step, the self-check): the parser's two patterns and stars, the
    long one first; each, run with Python's engine, equals mask_personal_ids; R7b's spelling (the superseded r7 and r9
    files) differs from it exactly on the runs of 10 to 14 digits;
  * the self-check's samples: each expected value is mask_personal_ids of its raw text; together they hold runs of 8,
    9, 10, 11, 14, 15 and 20 digits, digits glued to letters, several runs in one text, masked text and both limits;
  * a local Postgres replay, only when YQ_LOCAL_PG_R10 names a loopback cluster (SKIP when it is not set; any other
    address is refused), on an EMPTY database, in one transaction rolled back: the view's own expression run by
    Postgres on every run length equals the Python mask; v_sales as production has it (R7b, with v_sales_agent, a
    dependent view and the grants) over seeded lines, stock rows and batch copies; the migration twice (v_sales and
    every stored narration = mask_personal_ids of the raw text, row_hash untouched, another target's copy untouched, the
    columns, the comment, the grants, the dependent view, the read role); the reverse twice (R7b's view again, the
    narrations stay masked); the migration again.
"""
from __future__ import annotations

import os

os.environ["SUPABASE_URL"] = "https://ci.invalid"          # before ANY import that could read a .env
os.environ["SUPABASE_KEY"] = "ci-key"
os.environ["DATABASE_URL"] = "postgresql://ci:ci@ci.invalid:5432/ci"

import io  # noqa: E402
import logging  # noqa: E402
import re  # noqa: E402
import sys  # noqa: E402
import traceback  # noqa: E402
from datetime import date, datetime  # noqa: E402
from pathlib import Path  # noqa: E402

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import text_mask  # noqa: E402
from scripts.text_mask import MASK_CPR, MASK_LONG, PERSONAL_ID_CPR, PERSONAL_ID_LONG  # noqa: E402
from scripts.text_mask import mask_personal_ids as mask  # noqa: E402

TESTS: list[tuple[str, object]] = []


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


SCRIPTS = ROOT / "scripts"
MIG = SCRIPTS / "r10_long_number_mask_migration.sql"
REV = SCRIPTS / "r10_long_number_mask_reverse.sql"
R10_FILES = (MIG, REV)
R7_VIEW = SCRIPTS / "r7_narration_mask_migration.sql"
R7_DATA = SCRIPTS / "r7_narration_mask_data_migration.sql"
R9_M1 = SCRIPTS / "r9_stock_moves_migration.sql"

REVIEW_LONG = r"(?<![0-9])[0-9]{7,}([0-9]{3})(?![0-9])"     # the review's pattern: a run of 10 digits or more
R7B_LONG = r"(?<![0-9])[0-9]{12,}([0-9]{3})(?![0-9])"       # R7b's, superseded: 15 digits or more
CPR = r"(?<![0-9])[0-9]{6}([0-9]{3})(?![0-9])"              # exactly 9 digits, unchanged since R7b
COMMIT_RE = re.compile(r"^\s*(commit|end)\s*;", re.I | re.M)       # what apply_sql --rehearse refuses
WRAPS = (("", ""), ("a", "b"), ("ID", "x"), ("x ", " y"), ("no:", "/"), ("(", ")"), ("*", "*"), ("-", "-"))


def digits(n: int, start: int = 1) -> str:
    """An invented run of n digits: 1234567890 1234... (or from another first digit)."""
    return "".join(str((start + i) % 10) for i in range(n))


def masked_run(run: str) -> str:
    """What the r10 rule makes of one whole run of ASCII digits."""
    if len(run) <= 8:
        return run
    return (MASK_CPR if len(run) == 9 else MASK_LONG) + run[-3:]


def arabic(run: str) -> str:
    """The same digits written in Arabic-Indic digits (U+0660 to U+0669)."""
    return "".join(chr(0x0660 + int(c)) for c in run)


_R7B = re.compile(R7B_LONG)


def r7b_mask(text):
    """R7b's rule, which the superseded r7 and r9 files spell and production's stored text carries today."""
    if not text:
        return text
    text = _R7B.sub(lambda m: MASK_LONG + m.group(1), text)
    return PERSONAL_ID_CPR.sub(lambda m: MASK_CPR + m.group(1), text)


def texts_every_length(top: int = 25) -> list[str]:
    """Every run length 1..top at the start, in the middle and at the end of a text, and in every wrap."""
    out = []
    for n in range(1, top + 1):
        for start in (1, 0, 7):
            run = digits(n, start)
            out += [run, run + " end", "at " + run, "a " + run + " b"]
            out += [pre + run + post for pre, post in WRAPS]
    return out


# ── reading SQL (the helpers of tests/test_r9_stock_moves.py) ─────────────────────

def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _strip(text: str) -> str:
    """SQL without -- comments. No literal in these files holds two dashes, so a line split is enough."""
    return "\n".join(ln.split("--", 1)[0] for ln in text.splitlines())


def _norm(text: str) -> str:
    return " ".join(text.split())


def _statements(text: str) -> list[str]:
    """The statements of a file, comments dropped, each without its ';'. Quotes and $$ bodies are kept whole."""
    out, cur, i, n = [], [], 0, len(text)
    while i < n:
        if text.startswith("--", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        ch = text[i]
        if ch == "'":
            j = i + 1
            while j < n:
                if text[j] == "'" and text[j + 1:j + 2] == "'":
                    j += 2
                    continue
                if text[j] == "'":
                    break
                j += 1
            cur.append(text[i:j + 1])
            i = j + 1
            continue
        if text.startswith("$$", i):
            j = text.index("$$", i + 2)
            cur.append(text[i:j + 2])
            i = j + 2
            continue
        if ch == ";":
            stmt = "".join(cur).strip()
            if stmt:
                out.append(stmt)
            cur = []
        else:
            cur.append(ch)
        i += 1
    stmt = "".join(cur).strip()
    if stmt:
        out.append(stmt)
    return out


def _stmt(path: Path, head: str) -> str:
    found = [s for s in _statements(_text(path)) if _norm(s).lower().startswith(head.lower())]
    assert len(found) == 1, (path.name, head, len(found))
    return found[0]


def _do_blocks(path: Path) -> list[str]:
    return [s for s in _statements(_text(path)) if _norm(s).lower().startswith("do $$")]


# a nested regexp_replace pair: the long pattern first, then the 9-digit one (tests/test_r7b_security.py's reader)
_SQL_TWIN = re.compile(
    r"regexp_replace\(\s*regexp_replace\(\s*(?P<src>[^,]+?),\s*'(?P<p1>[^']*)',\s*'(?P<r1>[^']*)',\s*'g'\s*\),\s*"
    r"'(?P<p2>[^']*)',\s*'(?P<r2>[^']*)',\s*'g'\s*\)")
_SAMPLE = re.compile(r"\((\d+),\s*(U&)?'((?:[^']|'')*)',\s*(U&)?'((?:[^']|'')*)'\)")


def _literal(body: str, unicode_escapes: bool) -> str:
    """A SQL string literal's text: '' is one quote; in a U&'' literal \\XXXX is a code point."""
    s = body.replace("''", "'")
    if unicode_escapes:
        s = re.sub(r"\\\+([0-9A-Fa-f]{6})|\\([0-9A-Fa-f]{4})", lambda m: chr(int(m.group(1) or m.group(2), 16)), s)
    return s


def self_check_samples() -> list[tuple[int, str, str]]:
    """(number, raw, expected) of every sample in the migration's self-check, in order."""
    block = next(b for b in _do_blocks(MIG) if "as v(i, raw, want)" in b)
    values = block[block.index("from (values"):block.index("as v(i, raw, want)")]
    listed = re.findall(r"^\s*\((\d+),", values, re.M)
    found = [(int(m.group(1)), _literal(m.group(3), bool(m.group(2))), _literal(m.group(5), bool(m.group(4))))
             for m in _SAMPLE.finditer(values)]
    assert len(found) == len(listed), ("a sample the reader missed", len(found), len(listed))
    return found


def sql_twin(match) -> callable:
    """One SQL spelling of the mask run with Python's engine: the same patterns (lookarounds, [0-9], {n,}) and the
    same \\1, behind the guard every statement carries (a 9-digit stretch), which never changes a result."""
    def run(text):
        if not text or not re.search("[0-9]{9}", text):
            return text
        return re.sub(match["p2"], match["r2"], re.sub(match["p1"], match["r1"], text))
    return run


# ═══════════════════════════════════════════════════════════════════════════════
# 1. the Python mask
# ═══════════════════════════════════════════════════════════════════════════════

@test("mask: every run of 1 to 25 digits at the start, in the middle, at the end, glued to letters or signs")
def _():
    for n in range(1, 26):
        for start in (1, 0, 7):
            run = digits(n, start)
            want = masked_run(run)
            assert mask(run) == want, (n, mask(run))
            assert mask(run + " end") == want + " end" and mask("at " + run) == "at " + want
            assert mask("a " + run + " b") == "a " + want + " b"
            for pre, post in WRAPS:
                assert mask(pre + run + post) == pre + want + post, (n, pre, post, mask(pre + run + post))
    # the rule in numbers: 8 stay, 9 keep their length, 10 and more are always 12 stars and 3
    assert [len(masked_run(digits(n))) for n in (8, 9, 10, 14, 15, 25)] == [8, 9, 15, 15, 15, 15]
    assert mask(digits(12)) == MASK_LONG + "012" and mask(digits(9)) == MASK_CPR + "789"


@test("mask: several runs in one text, each on its own; a sign keeps two runs apart, glued digits are one run")
def _():
    a9, a11, a14, a16, a8 = digits(9), digits(11, 3), digits(14, 5), digits(16, 2), digits(8, 4)
    text = f"cpr {a9} ph {a11} acct {a14}, order YQ-2610-0019 card {a16} inv {a8} end"
    want = (f"cpr {MASK_CPR}{a9[-3:]} ph {MASK_LONG}{a11[-3:]} acct {MASK_LONG}{a14[-3:]}, order YQ-2610-0019 "
            f"card {MASK_LONG}{a16[-3:]} inv {a8} end")
    assert mask(text) == want, mask(text)
    assert mask(a9 + "-" + digits(10)) == MASK_CPR + a9[-3:] + "-" + MASK_LONG + "890"
    assert mask(a9 + digits(10)) == MASK_LONG + (a9 + digits(10))[-3:], "glued, two runs are one run of 19"
    assert mask("x".join([digits(10)] * 3)) == "x".join([MASK_LONG + "890"] * 3)


@test("mask: masking twice changes nothing; masked text stays as it is and holds no run of 9 digits")
def _():
    for t in texts_every_length() + [f"id {digits(n)} / ref {digits(m, 4)}x" for n in (9, 10, 13) for m in (11, 15, 20)]:
        once = mask(t)
        assert mask(once) == once, t
        assert not re.search("[0-9]{9}", once), (t, once)
        assert r7b_mask(once) == once, "nor does R7b's rule find anything in it"
    for already in (MASK_CPR + "123", MASK_LONG + "890", "a" + MASK_LONG + "234b", f"ph {MASK_LONG}901 / {MASK_CPR}123"):
        assert mask(already) == already, already
    assert mask(None) is None and mask("") == ""


@test("mask: what stays by design (order and invoice numbers, 8 digits or fewer) and the known limits (grouped and Arabic-Indic digits)")
def _():
    for kept in ("order YQ-2610-0019", "YQ 2610 0019", "YQ26100019", "SI-YQ-26-10-110", "SIO:T-26-10-123",
                 "inv " + digits(8), digits(4) + " " + digits(4), "x" + digits(8) + "y"):
        assert mask(kept) == kept, kept
    grouped = " ".join(["4" + "1" * 3] + ["1" * 4] * 3)           # a card written in four groups of four
    hyphened = "-".join([digits(4), digits(4, 5), digits(4, 9)])
    for limit in (grouped, hyphened, "cpr " + arabic(digits(9)), "acct " + arabic(digits(14)), arabic(digits(20))):
        assert mask(limit) == limit, ("a known limit: not masked", limit)
    # a run of ASCII digits next to Arabic-Indic ones is masked on its ASCII part alone
    assert mask(arabic(digits(3)) + digits(10)) == arabic(digits(3)) + MASK_LONG + "890"
    # the Focus matcher still finds every typed order number next to masked runs
    matcher = r"([A-Za-z]{2,4})[ -]?([0-9]{4})[ -]?([0-9]{4})"
    typed = f"YQ-2610-0019 ph {digits(11)}, YQ 2610 0020 / YQ26100021 acct {digits(13)} yq-2610-0022"
    found = {f"{a.upper()}-{b}-{c}" for a, b, c in re.findall(matcher, mask(typed))}
    assert found == {"YQ-2610-0019", "YQ-2610-0020", "YQ-2610-0021", "YQ-2610-0022"}, found


@test("the patterns: the review's long pattern (10 digits or more), R7b's 9-digit rule, [0-9] never \\d; ingest re-exports the very same objects")
def _():
    assert PERSONAL_ID_LONG.pattern == REVIEW_LONG and PERSONAL_ID_CPR.pattern == CPR
    assert MASK_LONG == "*" * 12 and MASK_CPR == "*" * 6
    assert "\\d" not in PERSONAL_ID_LONG.pattern + PERSONAL_ID_CPR.pattern
    from scripts import ingest
    for name in ("PERSONAL_ID_LONG", "PERSONAL_ID_CPR", "MASK_LONG", "MASK_CPR", "mask_personal_ids"):
        assert getattr(ingest, name) is getattr(text_mask, name), f"ingest.{name} is text_mask.{name}"


@test("the module says the rule, what r10 changed, the known limits and where the SQL twin lives")
def _():
    doc = " ".join((text_mask.__doc__ or "").split())
    for piece in ("a run of 10 digits or more becomes twelve stars and its last 3", "Until release r10",
                  "grouped digits are not masked", "4111 1111 1111 1111",
                  "Arabic-Indic and other non-ASCII digits are not masked", "a run of 8 digits or fewer stays as typed",
                  "scripts/r10_long_number_mask_migration.sql", "tests/test_r10_long_number_mask.py"):
        assert piece in doc, piece
    ingest_note = " ".join((SCRIPTS / "ingest.py").read_text(encoding="utf-8").split())
    assert "scripts/r10_long_number_mask_migration.sql" in ingest_note and "(10+ digits:" in ingest_note


# ═══════════════════════════════════════════════════════════════════════════════
# 2. the next load stays idempotent
# ═══════════════════════════════════════════════════════════════════════════════

@test("next load: the data step on today's stored text (R7b's mask) gives what the r10 parser gives from the raw cell, every run length")
def _():
    twin = sql_twin(next(_SQL_TWIN.finditer(_strip(_stmt(MIG, "update order_lines")))))
    raws = texts_every_length() + [f"cpr {digits(9)} ph {digits(11, 4)} card {digits(16, 2)} acct {digits(14, 6)}",
                                   f"{digits(15)}x{digits(10)}y{digits(9)}", None, "", "back to main"]
    for raw in raws:
        stored_today = r7b_mask(raw)                   # the r7 / r9 data steps, or the 2b parser
        assert twin(stored_today) == mask(raw), (raw, twin(stored_today), mask(raw))
        assert twin(mask(raw)) == mask(raw), "a row the r10 parser wrote is left as it is"


@test("next load: parse_stock's row_hash and both tables' keys are the same whichever mask runs (the hash is of the raw cells)")
def _():
    import pandas as pd
    from scripts import ingest
    item, narr = "Cable C1 (TST)", f"for van A ph {digits(11)} acct {digits(14, 3)} CPR {digits(9, 5)}"
    ledger = pd.DataFrame([
        ["Stock Ledger"] + [None] * 14,
        ingest.EXPECTED_HEADERS["stock_movements"],
        [item] + [None] * 14,
        [datetime(2026, 10, 2), "SIO:T-26-10-1", None, None, 30, 0.5, 70, None, 15, 35, 0.5,
         "Accessories Warehouse", "Van A - Acc WH", narr, "Stock Issue Voucher"],
        [datetime(2026, 10, 3), "STRV:T-26-10-1", 30, None, None, None, 30, None, None, None, None,
         "Van A - Acc WH", None, narr, "Stock Receive Voucher"],
    ], dtype=object)
    header = ingest.EXPECTED_HEADERS["order_lines"]
    book = pd.DataFrame([["Sales Day Book"] + [None] * 12, header,
                         [datetime(2026, 10, 2), "SI : SI-T-26-10-001", "Test Shop", item, 2, 1.5, 3, 0, 2.727, 0.273,
                          3, f"YQ-2610-0019 ph {digits(12, 2)}", "Van A - Acc WH"]], dtype=object)
    new_stock = ingest.parse_stock(ledger, "Stock_ledger.xlsx")
    new_lines = ingest.parse_order_lines(book, "Sales_day_book.xlsx")
    saved = ingest.mask_personal_ids
    try:
        ingest.mask_personal_ids = r7b_mask                # the parser as release 2b shipped it
        old_stock = ingest.parse_stock(ledger, "Stock_ledger.xlsx")
        old_lines = ingest.parse_order_lines(book, "Sales_day_book.xlsx")
    finally:
        ingest.mask_personal_ids = saved
    key_s = lambda r: (r["voucher"], r["item_name"], r["row_hash"])  # noqa: E731  (the ledger's upsert key)
    key_o = lambda r: (r["invoice_no"], r["line_no"])                # noqa: E731  (the order lines' upsert key)
    assert [key_s(r) for r in new_stock] == [key_s(r) for r in old_stock], "row_hash never sees the mask"
    assert [key_o(r) for r in new_lines] == [key_o(r) for r in old_lines]
    for new, old in zip(new_stock + new_lines, old_stock + old_lines):
        assert {k: v for k, v in new.items() if k != "narration"} == {k: v for k, v in old.items() if k != "narration"}
        assert new["narration"] == mask(old["narration"]), "the r10 data step's result is the next load's text"
    for i, r in zip((3, 4), new_stock):
        cells = list(ledger.iloc[i])
        assert r["row_hash"] == ingest.row_hash([ingest.norm_date(cells[0]), ingest.txt(cells[1]), item, *cells[2:15]])
    assert new_stock[0]["narration"] == mask(narr) != narr


# ═══════════════════════════════════════════════════════════════════════════════
# 3. the two files as text
# ═══════════════════════════════════════════════════════════════════════════════

@test("files: ASCII only; lock_timeout first; no CASCADE, security_invoker, DROP or COMMIT; a self-check in each; no GRANT, the revokes plain")
def _():
    for path in R10_FILES:
        raw = path.read_bytes()
        assert raw.isascii(), f"{path.name}: ASCII only, so no client encoding can change a byte of it"
        text = _text(path)
        stmts = _statements(text)
        assert _norm(stmts[0]).lower() == "set lock_timeout = '2s'", (path.name, stmts[0][:60])
        code = _strip(text).lower()
        for word in ("cascade", "security_invoker", "drop ", "truncate", "delete from", "insert into", "row_hash"):
            assert word not in code, (path.name, word)
        assert not COMMIT_RE.search(text), f"{path.name}: apply_sql --rehearse would refuse it"
        assert "raise exception" in code and _do_blocks(path), f"{path.name}: no self-check"
        for block in _do_blocks(path):
            assert not re.search(r"\b(grant|revoke)\s", block.lower()), (path.name, "a GRANT or REVOKE inside a DO block")
        plain = [_norm(s).lower() for s in stmts if re.match(r"\s*(grant|revoke)\s", s, re.I)]
        assert plain == ["revoke all on table v_sales from anon, authenticated",
                         "revoke all on table v_sales_agent from anon, authenticated"], (path.name, plain)


@test("sqlglot: every statement outside a DO block parses as Postgres")
def _():
    import sqlglot
    from sqlglot import exp
    logging.getLogger("sqlglot").setLevel(logging.ERROR)
    for path in R10_FILES:
        for stmt in _statements(_text(path)):
            if _norm(stmt).lower().startswith("do $$"):
                continue
            tree = sqlglot.parse_one(stmt, read="postgres")
            assert not isinstance(tree, exp.Command), (path.name, _norm(stmt)[:80])


@test("v_sales: r7_narration_mask_migration.sql's statement verbatim but the long pattern; the comment names r10")
def _():
    r7 = _norm(_stmt(R7_VIEW, "create or replace view v_sales as"))
    mine = _norm(_stmt(MIG, "create or replace view v_sales as"))
    assert r7.count(R7B_LONG) == 1 and mine.count(REVIEW_LONG) == 1
    assert mine == r7.replace(R7B_LONG, REVIEW_LONG), "only the long pattern differs"
    assert "case when ol.narration ~ '[0-9]{9}' then regexp_replace(" in mine, "the guard of R7b, kept"
    comment = _norm(_stmt(MIG, "comment on column v_sales.narration is"))
    assert "every run of 10 or more digits keep only their last 3 digits (r10, scripts/r10_long_number_mask_migration.sql" in comment


@test("data step: r7's and r9's statements verbatim but the long pattern, on both tables and both targets' copies; nothing else written")
def _():
    for old, new in ((_stmt(R7_DATA, "update order_lines"), _stmt(MIG, "update order_lines")),
                     (_stmt(R9_M1, "update stock_movements"), _stmt(MIG, "update stock_movements"))):
        assert _norm(old).count(R7B_LONG) == 2
        assert _norm(new) == _norm(old).replace(R7B_LONG, REVIEW_LONG), _norm(new)[:60]
    olds = [next(b for b in _do_blocks(f) if "update ingest_stage" in b) for f in (R7_DATA, R9_M1)]
    news = [b for b in _do_blocks(MIG) if "update ingest_stage" in b]
    assert len(news) == 2, len(news)
    for old, new in zip(olds, news):
        assert _norm(new) == _norm(old).replace(R7B_LONG, REVIEW_LONG)
    assert _norm(news[0]).count("target = 'order_lines'") == 2 and _norm(news[1]).count("target = 'stock_movements'") == 2
    writes = [_norm(s).lower().split(" = ")[0] for s in _statements(_text(MIG)) if re.match(r"\s*(update|insert|delete)\s", s, re.I)]
    assert writes == ["update order_lines set narration", "update stock_movements set narration"], writes
    assert not any(re.match(r"\s*(update|insert|delete)\s", s, re.I) for s in _statements(_text(REV))), "the reverse writes no row"


@test("twins: every SQL spelling of the mask uses the parser's patterns and stars, the long one first, and equals mask_personal_ids")
def _():
    twins = list(_SQL_TWIN.finditer(_strip(_text(MIG))))
    # the view once; each UPDATE twice (its SET and its no-op guard); the copies four times; the self-check once
    assert [_norm(t["src"]) for t in twins] == (["ol.narration"] + ["narration"] * 4 + ["row ->> 'narration'"] * 4
                                                + ["s.raw"]), [_norm(t["src"]) for t in twins]
    raws = texts_every_length() + [r for _i, r, _w in self_check_samples()] + [None, "", arabic(digits(12))]
    for t in twins:
        assert (t["p1"], t["r1"], t["p2"], t["r2"]) == (PERSONAL_ID_LONG.pattern, MASK_LONG + "\\1",
                                                        PERSONAL_ID_CPR.pattern, MASK_CPR + "\\1"), t.group(0)[:90]
        twin = sql_twin(t)
        for raw in raws:
            assert twin(raw) == mask(raw), (_norm(t["src"]), raw, twin(raw))
    old = list(_SQL_TWIN.finditer(_strip(_text(REV))))
    assert len(old) == 1 and old[0]["p1"] == R7B_LONG and old[0]["p2"] == CPR, "the reverse spells R7b's rule"


@test("R7b's spelling (the r7 and r9 files, superseded by r10) differs from r10 exactly on the runs of 10 to 14 digits")
def _():
    for n in range(1, 26):
        r = digits(n)
        assert (r7b_mask(r) == mask(r)) == (not 10 <= n <= 14), (n, r7b_mask(r), mask(r))
    for f in (R7_VIEW, R7_DATA, R9_M1):
        assert R7B_LONG in _text(f) and REVIEW_LONG not in _text(f), f.name


@test("self-check: each expected value is mask_personal_ids of its raw text; runs of 8, 9, 10, 11, 14, 15 and 20, glued, several, masked text, both limits")
def _():
    samples = self_check_samples()
    assert [i for i, _r, _w in samples] == list(range(1, len(samples) + 1)), "numbered 1..N"
    for i, raw, want in samples:
        assert mask(raw) == want, (i, raw, mask(raw), want)
    raws = [r for _i, r, _w in samples]
    lengths = {len(r) for raw in raws for r in re.findall("[0-9]+", raw)}
    assert {8, 9, 10, 11, 14, 15, 20} <= lengths, lengths
    assert any(re.search(r"[A-Za-z][0-9]{9,}|[0-9]{9,}[A-Za-z]", raw) for raw in raws), "digits glued to letters"
    assert any(len([r for r in re.findall("[0-9]+", raw) if len(r) >= 9]) >= 2 for raw in raws), "several runs"
    assert any("*" in raw and mask(raw) == raw for raw in raws), "masked text, left as it is"
    assert any(re.fullmatch(r"[0-9]{4}( [0-9]{4}){3}", raw) for raw in raws), "grouped digits"
    assert any(any(0x0660 <= ord(c) <= 0x0669 for c in raw) and mask(raw) == raw for raw in raws), "Arabic-Indic digits"
    check = _norm(next(b for b in _do_blocks(MIG) if "as v(i, raw, want)" in b))
    for piece in ("got := case when s.raw ~ '[0-9]{9}' then regexp_replace(",
                  "if got is distinct from s.want then raise exception",
                  "if position('[0-9]{7,}([0-9]{3})' in def) = 0 or position('[0-9]{12,}' in def) > 0 then",
                  "if n_cols <> 31 then", "if exists (select 1 from order_lines where narration ~ '[0-9]{9}') then",
                  "if exists (select 1 from stock_movements where narration ~ '[0-9]{9}') then",
                  "if to_regclass('public.ingest_stage') is not null then if exists (select 1 from ingest_stage where "
                  "target in ('order_lines', 'stock_movements') and row ->> 'narration' ~ '[0-9]{9}') then",
                  "if to_regclass('public.ingest_replaced') is not null then if exists (select 1 from ingest_replaced "
                  "where target in ('order_lines', 'stock_movements') and row ->> 'narration' ~ '[0-9]{9}') then",
                  "if exists (select 1 from v_sales where narration ~ '[0-9]{9}') then",
                  "if exists (select 1 from v_sales_agent where narration ~ '[0-9]{9}') then"):
        assert piece in check, piece


@test("reverse: r7_narration_mask_migration.sql's v_sales and comment verbatim; it says the stored narrations cannot come back")
def _():
    for head in ("create or replace view v_sales as", "comment on column v_sales.narration is"):
        assert _norm(_stmt(REV, head)) == _norm(_stmt(R7_VIEW, head)), head
    prose = lambda p: " ".join(ln.strip().lstrip("-").strip() for ln in _text(p).splitlines())  # noqa: E731
    text = prose(REV)
    assert "CANNOT put back" in text and "the release backup taken before the migration" in text
    assert "which is never wanted for ID numbers" in text and "nothing here touches a row" in text
    check = _norm(_do_blocks(REV)[-1])
    assert "if position('[0-9]{12,}([0-9]{3})' in def) = 0 or position('[0-9]{7,}' in def) > 0 then" in check
    head = prose(MIG)
    assert "cannot be undone from SQL" in head and "yq_backup release r10" in head
    assert "From the MAIN project folder only" in head and "never during a Focus load" in head


# ═══════════════════════════════════════════════════════════════════════════════
# 4. local Postgres replay (only on a loopback cluster named by YQ_LOCAL_PG_R10)
# ═══════════════════════════════════════════════════════════════════════════════

LOOPBACK = {"localhost": "127.0.0.1", "127.0.0.1": "127.0.0.1", "::1": "::1"}


def _local_dsn(env=None) -> tuple[str | None, str | None, str]:
    """(dsn, hostaddr, why): the replay's cluster, from YQ_LOCAL_PG_R10 only, and only on this machine's loopback."""
    env = os.environ if env is None else env
    dsn = (env.get("YQ_LOCAL_PG_R10") or "").strip()
    if not dsn:
        return None, None, "YQ_LOCAL_PG_R10 is not set"
    try:
        from psycopg.conninfo import conninfo_to_dict
        info = conninfo_to_dict(dsn)
    except Exception:  # noqa: BLE001
        return None, None, "YQ_LOCAL_PG_R10 is not a connection string"
    host = str(info.get("host") or "")
    if "service" in info or host not in LOOPBACK or str(info.get("hostaddr") or LOOPBACK[host]) != LOOPBACK[host]:
        return None, None, "YQ_LOCAL_PG_R10 must name this machine's loopback (127.0.0.1, ::1 or localhost)"
    return dsn, LOOPBACK[host], ""


@test("replay guard: only a loopback cluster named by YQ_LOCAL_PG_R10; nothing set = SKIP, anything else refused")
def _():
    for dsn, ok in (("", None), ("postgresql://postgres@127.0.0.1:55432/r10", "127.0.0.1"),
                    ("host=::1 port=55432 dbname=r10", "::1"),
                    ("postgresql://postgres@db.example.invalid:5432/postgres", None),
                    ("postgresql://postgres@127.0.0.1:5432/x?hostaddr=10.0.0.5", None), ("service=prod", None),
                    ("postgresql:///x", None), ("not a dsn ===", None)):
        got_dsn, addr, why = _local_dsn({"YQ_LOCAL_PG_R10": dsn})
        assert addr == ok and (got_dsn is not None) == (ok is not None) and (why == "") == (ok is not None), (dsn, why)
    assert _local_dsn({})[2] == "YQ_LOCAL_PG_R10 is not set"


# Supabase-style roles, and the default ACL production had before the 16-Sep hotfix (every object postgres makes in
# public granted to the API roles), so only the files' own revokes close a view to anon / authenticated. The tables
# behind v_sales as scripts/schema.sql has them (the columns v_sales reads); stock_movements and the batch importer's
# copy tables as tests/test_r9_stock_moves.py has them.
REPLAY_SCHEMA = """
do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then create role anon nologin; end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then create role authenticated nologin; end if;
  if not exists (select 1 from pg_roles where rolname = 'service_role') then create role service_role nologin bypassrls; end if;
  if not exists (select 1 from pg_roles where rolname = 'yq_readonly') then create role yq_readonly nologin; end if;
end $$;
alter default privileges in schema public grant all on tables to anon, authenticated, service_role;
create table categories (id bigint generated always as identity primary key, name text unique not null, division text);
create table products (id bigint generated always as identity primary key, sku_code text unique not null,
  item_name text, category_id bigint references categories(id));
create table product_aliases (id bigint generated always as identity primary key,
  product_id bigint references products(id), alias_text text unique not null);
create table orders (id bigint generated always as identity primary key, invoice_no text unique not null,
  order_date date, customer_name text, salesman text, payment_mode text, sales_account_name text);
create table order_lines (id bigint generated always as identity primary key, invoice_no text not null, line_no int,
  line_date date, customer_account text, item_name text, quantity numeric, rate_bhd numeric, gross_bhd numeric,
  discount_bhd numeric, taxable_bhd numeric, vat_amount_bhd numeric, total_amount_bhd numeric, warehouse_name text,
  narration text, unique (invoice_no, line_no));
create table salesman_channels (salesman text primary key, channel text);
create table stock_movements (
  id bigint generated always as identity primary key, item_name text not null, move_date date, voucher text,
  voucher_type text, received_qty numeric, issued_qty numeric, warehouse_name text, to_warehouse_name text,
  narration text, row_hash text, unique (voucher, item_name, row_hash));
create table ingest_batches (id bigint generated always as identity primary key);
create table ingest_stage (id bigint generated always as identity primary key,
  batch_id bigint not null references ingest_batches(id) on delete restrict, target text not null,
  row jsonb not null, action text, row_id bigint);
create table ingest_replaced (id bigint generated always as identity primary key,
  batch_id bigint not null references ingest_batches(id) on delete restrict, target text not null,
  action text not null check (action in ('deleted', 'updated', 'revived', 'voided')), row jsonb not null);
"""

ITEM, VAN, INVOICE = "Cable C1 (TST)", "Van A - Acc WH", "SI : SI-T-26-10-001"
LINES = [  # line_no, the narration as Focus has it (raw)
    (1, f"CPR {digits(9)}"), (2, f"ph {digits(11)}"), (3, f"acct{digits(12, 3)}x"),
    (4, f"ref {digits(14, 5)} and cpr {digits(9, 2)}"), (5, f"card {digits(16, 4)}"),
    (6, f"order YQ-2610-0019 tel {digits(10, 6)}"), (7, "YQ26100019"), (8, None), (9, digits(13, 7)),
    (10, "back to main"), (11, " ".join(["4" + "1" * 3] + ["1" * 4] * 3)), (12, "cpr " + arabic(digits(9))),
    (13, f"two {digits(20, 8)} {digits(11, 9)}"),
]
STOCK = [  # voucher, type, narration (raw), row_hash; stored as production holds them: R7b's mask applied
    ("SIO:T-26-10-1", "Stock Issue Voucher", f"for van A ph {digits(11)}", "h01"),
    ("STRV:T-26-10-1", "Stock Receive Voucher", f"for van A ph {digits(11)}", "h02"),
    ("SI:T-26-10-1", "Sales Invoice", f"cust CPR {digits(9, 3)} acct {digits(14)}", "h03"),
    ("SIO:T-26-10-2", "Stock Issue Voucher", "order YQ-2610-0019", "h04"),
    ("MRN:T-26-10-1", "Material Receipt Note", None, "h05"),
    ("SIO:T-26-10-3", "Stock Issue Voucher", f"card {digits(16, 2)} ref {digits(12, 4)}", "h06"),
]
COPIES = [  # table, target, action, row: the batch importer's copies (raw text; the last two are other targets)
    ("ingest_stage", "order_lines", None, {"invoice_no": INVOICE, "line_no": 2, "narration": f"ph {digits(11)}"}),
    ("ingest_stage", "stock_movements", None, {"voucher": "SIO:T-26-10-1", "narration": f"for van A ph {digits(11)}",
                                               "row_hash": "h01"}),
    ("ingest_replaced", "order_lines", "updated", {"invoice_no": INVOICE, "line_no": 4,
                                                   "narration": f"ref {digits(14, 5)} and cpr {digits(9, 2)}"}),
    ("ingest_replaced", "stock_movements", "deleted", {"voucher": "SI:T-26-10-9", "narration": f"acct {digits(13)}",
                                                       "row_hash": "h99"}),
    ("ingest_stage", "ledger_entries", None, {"voucher": "RV:T-1", "narration": f"ref {digits(12)}"}),
    ("ingest_replaced", "selling_prices", "updated", {"narration": f"note {digits(10)}"}),
]


def _attrs(cur, view: str) -> list[str]:
    cur.execute("""select a.attname || ' ' || format_type(a.atttypid, a.atttypmod) from pg_attribute a
                    where a.attrelid = %s::regclass and a.attnum > 0 and not a.attisdropped order by a.attnum""",
                (f"public.{view}",))
    return [r[0] for r in cur.fetchall()]


def _can(cur, role: str, rel: str) -> bool:
    cur.execute("select has_table_privilege(%s, %s, 'SELECT')", (role, f"public.{rel}"))
    return cur.fetchone()[0]


def _check_view(cur, cols, rule, view_mask: str) -> None:
    """v_sales after a file: its columns, which mask it carries, every line through `rule`, the grants, the views on
    it, the read role."""
    assert _attrs(cur, "v_sales") == cols
    cur.execute("select pg_get_viewdef('public.v_sales'::regclass), "
                "col_description('public.v_sales'::regclass, (select attnum from pg_attribute "
                "where attrelid = 'public.v_sales'::regclass and attname = 'narration'))")
    viewdef, comment = cur.fetchone()
    assert (view_mask in viewdef) and ({REVIEW_LONG: R7B_LONG, R7B_LONG: REVIEW_LONG}[view_mask] not in viewdef)
    assert ("(r10," in comment) == (view_mask == REVIEW_LONG) and ("(R7b," in comment) == (view_mask == R7B_LONG)
    want = [(n, rule(raw)) for n, raw in LINES]
    for view in ("v_sales", "v_sales_agent"):
        cur.execute(f"select line_no, narration from {view} order by line_no")
        assert cur.fetchall() == want, view
    cur.execute("select narration from v_dep_narr order by line_id")
    assert [r[0] for r in cur.fetchall()] == [w for _n, w in want], "the view on v_sales follows"
    for view in ("v_sales", "v_sales_agent"):
        assert _can(cur, "yq_readonly", view)
        for role in ("anon", "authenticated"):
            assert not _can(cur, role, view), (role, view)
    cur.execute("set local role yq_readonly")
    cur.execute("select count(*) from v_sales")
    assert cur.fetchone()[0] == len(LINES)
    cur.execute("reset role")
    assert not _can(cur, "yq_readonly", "order_lines")


def _check_stored(cur, hashes) -> None:
    """Every stored narration the data step covers is mask_personal_ids of the raw text; row_hash untouched; the
    copies of other targets untouched."""
    cur.execute("select line_no, narration from order_lines order by line_no")
    assert cur.fetchall() == [(n, mask(raw)) for n, raw in LINES]
    cur.execute("select row_hash, narration from stock_movements order by id")
    stored = cur.fetchall()
    assert [h for h, _n in stored] == hashes, "row_hash untouched"
    assert stored == [(h, mask(raw)) for _v, _t, raw, h in STOCK]
    for table in ("ingest_stage", "ingest_replaced"):
        cur.execute(f"select target, row from {table} order by id")
        got = cur.fetchall()
        want = [(target, {**row, "narration": mask(row["narration"]) if target in ("order_lines", "stock_movements")
                          else row["narration"]}) for t, target, _a, row in COPIES if t == table]
        assert got == want, (table, got)


@test("local replay: the view's expression on every run length; the migration twice, its reverse twice, the migration again (SKIP without YQ_LOCAL_PG_R10)")
def _():
    dsn, hostaddr, why = _local_dsn()
    if dsn is None:
        if why == "YQ_LOCAL_PG_R10 is not set":
            print("  SKIP: YQ_LOCAL_PG_R10 is not set (an empty database on a loopback cluster; see the module docstring)")
            return
        raise AssertionError("refusing to connect: " + why)
    try:
        import psycopg
        from psycopg.types.json import Jsonb
    except ImportError:
        print("  SKIP: psycopg not installed")
        return
    conn = psycopg.connect(dsn, hostaddr=hostaddr, connect_timeout=3)
    try:
        cur = conn.cursor()
        cur.execute("select current_user, to_regclass('public.order_lines'), to_regclass('public.v_sales'), "
                    "current_setting('server_encoding')")
        user, has_lines, has_sales, enc = cur.fetchone()
        if user != "postgres" or has_lines is not None or has_sales is not None or enc != "UTF8":
            print(f"  SKIP: the replay needs an empty UTF8 database and the postgres superuser; got {user}, "
                  f"order_lines={has_lines}, v_sales={has_sales}, {enc}")
            return
        # the SQL engine itself, on the view's own expression: every run length, every wrap, both limits
        view = _norm(_stmt(MIG, "create or replace view v_sales as"))
        expr = re.search(r"(case when ol\.narration ~ '\[0-9\]\{9\}' then .*? else ol\.narration end) as narration",
                         view).group(1)
        probe = texts_every_length() + [r for _i, r, _w in self_check_samples()] + [arabic(digits(14)), None]
        cur.execute(f"select {expr.replace('ol.narration', 'u.t')} from unnest(%s::text[]) with ordinality as u(t, i) "
                    "order by u.i", (probe,))
        got = [r[0] for r in cur.fetchall()]
        bad = [(raw, g) for raw, g in zip(probe, got) if g != mask(raw)]
        assert len(got) == len(probe) and not bad, bad[:3]

        # production as it stands: R7b's v_sales (r7_narration_mask_migration.sql), v_sales_agent, a view on v_sales
        cur.execute(REPLAY_SCHEMA)
        cur.execute("insert into categories (name, division) values ('Cables', 'Accessories') returning id")
        cat = cur.fetchone()[0]
        cur.execute("insert into products (sku_code, item_name, category_id) values ('C1', %s, %s) returning id",
                    (ITEM, cat))
        cur.execute("insert into product_aliases (product_id, alias_text) values (%s, %s)", (cur.fetchone()[0], ITEM))
        cur.execute("insert into salesman_channels values (%s, 'B2B')", (VAN,))
        cur.execute("insert into orders (invoice_no, order_date, customer_name, salesman, payment_mode, "
                    "sales_account_name) values (%s, %s, 'Test Shop', %s, 'Credit', 'Credit Sales')",
                    (INVOICE, date(2026, 10, 2), VAN))
        cur.executemany("insert into order_lines (invoice_no, line_no, line_date, customer_account, item_name, quantity, "
                        "rate_bhd, gross_bhd, discount_bhd, taxable_bhd, vat_amount_bhd, total_amount_bhd, "
                        "warehouse_name, narration) values (%s, %s, %s, 'Test Shop', %s, 1, 1.5, 1.5, 0, 1.364, 0.136, "
                        "1.5, %s, %s)", [(INVOICE, n, date(2026, 10, 2), ITEM, VAN, raw) for n, raw in LINES])
        cur.executemany("insert into stock_movements (item_name, move_date, voucher, voucher_type, warehouse_name, "
                        "narration, row_hash) values (%s, %s, %s, %s, 'Accessories Warehouse', %s, %s)",
                        [(ITEM, date(2026, 10, 2), v, t, r7b_mask(raw), h) for v, t, raw, h in STOCK])
        cur.execute("insert into ingest_batches default values returning id")
        batch = cur.fetchone()[0]
        for table, target, action, row in COPIES:
            if table == "ingest_stage":
                cur.execute("insert into ingest_stage (batch_id, target, row) values (%s, %s, %s)",
                            (batch, target, Jsonb(row)))
            else:
                cur.execute("insert into ingest_replaced (batch_id, target, action, row) values (%s, %s, %s, %s)",
                            (batch, target, action, Jsonb(row)))
        cur.execute(_stmt(R7_VIEW, "create or replace view v_sales as"))
        cur.execute("create view v_sales_agent as select * from v_sales")
        cur.execute("create view v_dep_narr as select line_id, narration from v_sales")
        cur.execute("grant select on v_sales, v_sales_agent to yq_readonly")
        cur.execute(_text(R7_VIEW))                         # the whole R7b file: its comment, revokes, self-check
        cols = _attrs(cur, "v_sales")
        assert len(cols) == 31 and "narration text" in cols
        _check_view(cur, cols, r7b_mask, R7B_LONG)          # today: a run of 10 to 14 digits shows in clear
        assert any(r7b_mask(raw) != mask(raw) for _n, raw in LINES), "the seed holds runs R7b leaves in clear"
        cur.execute("select row_hash from stock_movements order by id")
        hashes = [r[0] for r in cur.fetchall()]
        mig, rev = _text(MIG), _text(REV)

        cur.execute(mig)
        _check_view(cur, cols, mask, REVIEW_LONG)
        _check_stored(cur, hashes)
        cur.execute(mig)                                     # idempotent: the same view, nothing masked twice
        _check_view(cur, cols, mask, REVIEW_LONG)
        _check_stored(cur, hashes)

        cur.execute(rev)
        cur.execute(rev)
        _check_view(cur, cols, lambda raw: r7b_mask(mask(raw)), R7B_LONG)   # R7b's view over masked text
        assert all(r7b_mask(mask(raw)) == mask(raw) for _n, raw in LINES), "the reverse cannot unmask"
        _check_stored(cur, hashes)

        cur.execute(mig)
        _check_view(cur, cols, mask, REVIEW_LONG)
        _check_stored(cur, hashes)
        print(f"  replay: the view's expression on {len(probe)} texts = the Python mask; migration x2, reverse x2, "
              "migration - all rolled back")
    finally:
        conn.rollback()
        conn.close()


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
