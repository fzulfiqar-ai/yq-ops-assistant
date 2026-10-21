"""python -m scripts.ops check <commentary file>   (the monthly report's words, checked before his approval)

The monthly workbook (/yq-month) carries ten comment lines (five Highlights, five Points to note) and three notes
beside its tables (under the top products, under the top accounts, on the Customers sheet). Claude writes them in
words and placeholders only: every figure, name of a figure or date comes from the month's facts, never typed.
The build writes two files beside its record (scripts.ops.workbook.facts.write_files):

  report-month-<M>.facts.json        every fact a line may name: {month, data_date, digest, facts}, rewritten by
                                     each build
  report-month-<M>.commentary.json   the template, written once and never over itself: thirteen empty slots

Claude fills the slots in the template, then runs this command on it. It checks every slot, prints each line as it
will read and, only when all thirteen pass, a last line FINAL: the command that builds the final workbook with these
words, its token tied to the exact bytes of the file and to the figures. It writes nothing.

PLACEHOLDERS. {key} or {key|fmt}: key is a fact of the facts file (small letters, digits, _ and ., as rep1.net);
fmt one of FORMATS that fits the fact's kind (FITS); {key} alone takes the kind's DEFAULT_FORMAT.

  kind    what it holds                              formats (the first is the default)
  bhd     an amount in BHD                           bhd0 '5,962'  bhd1 '2.4'  bhd3 '2.394'
  int     a count                                    int '1,234'  words 'eleven'  Words 'Eleven' (a word up to
                                                     twenty, nought as 'no'/'No'; above twenty as int)
  pct     a share of a whole (0.42)                  pct0 '42%'  pct1 '42.0%'
  signed  a change against the month before (0.25)   pct0 '+25%'  pct1 '+25.0%'  abs_pct0 '25%' (no sign)
                                                     updown 'up' / 'down' / 'unchanged'
  date    a day                                      day '3 March'  month 'March 2026'
  month   a month                                    month 'March 2026'
  text    a name or a label                          text, as the workbook writes it

Amounts are rounded half away from zero on the value as a spreadsheet shows it (fifteen significant digits), so a
line reads what the workbook's cell reads. A fact with no value (a change with nothing in the month before to compare
with) has no rendering: a placeholder that names it is refused.

A SLOT IS REFUSED, with each reason printed, when:
  * outside its placeholders it holds a figure: a digit (any script, a fraction sign or a superscript too), a % sign
    (or one of its look-alikes), the word percent or "per cent", or a number word (NUMBER WORDS below);
  * a placeholder names no fact, or a format that does not exist or does not fit the fact's kind, or a fact with no
    value; or a brace is not part of a placeholder;
  * a highlight or a point holds no placeholder (the three notes may hold none);
  * a direction word contradicts the change it is said of, or is said with no change at all (THE DIRECTION RULE
    below), or a change written without its sign ({x|abs_pct0}) is said with no direction;
  * the line as it will read holds a nine-digit run, a run of fifteen or more digits (an ID or a card number), an
    e-mail address or a phone-like number (eight digits or more, single spaces or hyphens between them);
  * it is empty, still opens with DRAFT (the workbook's stand-in for an empty slot), holds a line break or a control
    character, or is no text.
The whole file is refused when its layout is not "commentary v1", it is for another month than the facts, a slot is
missing or one it should not have is there, the facts file's figures no longer match its own digest (changed by
hand), or the facts file's digest differs from the template's facts_digest (the month's figures moved after the draft
was started: read the facts again, check every line, then set facts_digest to the facts file's digest).
A capitalised word inside a sentence that is no word of any fact (and not BHD, VAT ...) is a note, never a refusal:
a name should come from a placeholder.

NUMBER WORDS (decided 21-Oct-2026). Refused outside a placeholder, whole words in any case: nought and zero to twenty,
the tens (thirty ... ninety), hundred, thousand, million, billion (and plurals), half/halves/halved, quarter(s),
double(d), twice, triple(d), treble(d), thrice, dozen(s), and the -fold words (twofold ... tenfold, hundredfold).
A hyphen splits a word: "twenty-one" and "one-off" are refused. The edge cases:
  * "one" names a figure, except in "one of" followed by the, its, his, her, their, our, your, these, those, them,
    this or that ("one of the largest accounts": a member of a group, no count), in "no one" and in "one another".
    "one account", "only one of {x|words} accounts" and "one-off" are refused: say the count with {key|words}.
  * Ordinals are words, not figures ("the first invoice", "the third week"), except a share or a date: third ... tenth
    (and plurals) after "a" or "an" ("a third") or before "of" ("a fifth of", "two thirds of"), and every ordinal
    from first to thirty-first before "of" ("the fifth of March", "first of all"): use a placeholder.
  * "someone", "none", "phone", "alone" are other words; "no" is not a number word.

THE DIRECTION RULE. In each sentence (ended by . ! or ?), every direction word is checked against the change nearest
to it in that sentence (by words; on a tie the one after the word):
  up:   rose rise(s) rising risen up higher grew grow(s) growing grown growth gain(s) gained gaining increase(s)
        increased increasing climb(s) climbed climbing jump(s) jumped jumping improve(s) improved improving
  down: fell fall(s) falling fallen down lower drop(s) dropped dropping lose loses lost losing loss decline(s)
        declined declining decrease(s) decreased decreasing slip(s) slipped slipping slid shrink(s) shrank shrunk
        shrinking
  (more, less, fewer, ahead, behind, stronger and weaker are no direction words: they rank more often than they move.)
  A change is a {key} of kind signed (its sign; zero when its figure as written reads 0%), or "from {a} to {b}" /
  "to {b} from {a}" with a and b two facts of one kind among bhd, int and pct (an optional BHD before each; the sign
  of b less a, zero when both read the same). A direction word refuses the slot when the sign differs (a zero change
  takes no direction word); after not, never, no, nor, without or an n't word it refuses when the sign is the word's
  own ("did not fall" with a fall). A direction word in a sentence that holds no change is refused too: a direction
  is always said with the figure that proves it (decided 21-Oct-2026, stricter than "contradicts": "fell to BHD
  {x|bhd0}" alone cannot be checked). Not a direction: a word joined by a hyphen ("lower-priced"), "up to", "made up"
  and the like, "broken down" and the like, "down payment".
  A {key|abs_pct0} must be the change a direction word in its sentence is checked against, or stand beside
  {key|updown}: a change written without its sign is said with its direction.

CONTRACT (scripts.ops.workbook.facts and the build's final step rely on it)
  KINDS, FORMATS, FITS, DEFAULT_FORMAT, SLOTS, LAYOUT
  make_fact(key, value, kind) -> {"value", "kind", "shown"}
      one fact as the facts file holds it: the value made plain (an amount, a share or a change a float as a
      spreadsheet shows it, a count an int when it is whole, a date 'YYYY-MM-DD', a month 'YYYY-MM', a text as given;
      None stays None) and its rendering in every format that fits its kind (none when the value is None). A key that
      is not small letters, digits, _ and . (starting with a letter), an unknown kind, a value of the wrong type:
      ValueError.
  render_value(value, kind, fmt) -> str      one rendering (ValueError: no value, or a format that does not fit)
  digest_of(facts) -> 'sha256:<hex>'          the sha256 of the canonical JSON of the facts mapping (keys sorted)
  token_for(commentary_bytes, facts_digest) -> str
      12 letters a to p (hex digit 0 -> a ... f -> p): the first twelve hex digits of the sha256 of the canonical JSON
      of {"commentary": sha256 of the file's bytes, "facts": the facts digest}. Any byte of the file, or any figure,
      changes it.
  read_commentary(path) -> (doc, raw bytes)   CommentaryUnreadable (a ValueError, one sentence) when the file cannot be
                                              read, is no JSON object, has another layout or no slots
  facts_file_of(commentary_path, doc) -> Path the facts file the template names (beside it when the name is relative)
  read_facts(path) -> facts doc               FactsUnreadable (a ValueError, one sentence) when it cannot be read
  check_doc(doc, facts_doc) -> Checked        every check above; nothing is read or written
      Checked: month, label, rendered {slot: text} (None for a refused slot), refused {slot: [reason]}, problems
      [reason] (the whole file), notes [text]; ok when nothing is refused
  render_slots(doc, facts_doc) -> {slot: text}
      the thirteen slots as they will read; CommentaryRefused (a ValueError carrying the Checked) when anything is
      refused. The final build calls it with the facts it has just worked out again, so a draft whose figures moved
      is refused.
  commentary_fields(rendered) -> dict         keyword arguments of workbook.sheets_core.Commentary
  add_arguments(parser) / run(args) -> {ok, command, data_date, text, flags, files, warnings, exit}
      exit 0: every slot passes and the last line of the text is FINAL; 2: something is refused (no FINAL line);
      1: the file cannot be read. The notes are printed above the FINAL line (and kept in flags["notes"]), so FINAL
      stays the last line.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

COMMAND = "check"
LAYOUT = "commentary v1"
SLOTS = (tuple(f"highlight_{i}" for i in range(1, 6)) + tuple(f"point_{i}" for i in range(1, 6))
         + ("products_note", "accounts_note", "customers_note"))
NEEDS_PLACEHOLDER = SLOTS[:10]                  # the highlights and points name a figure; the notes may not
KINDS = ("bhd", "int", "pct", "signed", "date", "month", "text")
FORMATS = ("bhd0", "bhd1", "bhd3", "int", "pct0", "pct1", "abs_pct0", "updown", "words", "Words", "day", "month", "text")
FITS: dict[str, tuple[str, ...]] = {
    "bhd": ("bhd0", "bhd1", "bhd3"),
    "int": ("int", "words", "Words"),
    "pct": ("pct0", "pct1"),
    "signed": ("pct0", "pct1", "abs_pct0", "updown"),
    "date": ("day", "month"),
    "month": ("month",),
    "text": ("text",),
}
DEFAULT_FORMAT = {kind: fits[0] for kind, fits in FITS.items()}
KIND_WORDS = {"bhd": "an amount in BHD", "int": "a count", "pct": "a share", "signed": "a change",
              "date": "a date", "month": "a month", "text": "a name or a label"}
TOKEN_LEN = 12
TOKEN_LETTERS = "abcdefghijklmnop"              # hex digit 0 -> a ... f -> p (scripts.ops.stock_orders.token's)
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")
SMALL = ("no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
         "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty")
SLOT_LABELS = {"products_note": "Under the top products", "accounts_note": "Under the top accounts",
               "customers_note": "On the Customers sheet"}
DRAFT_WORD = "DRAFT"                            # what an empty slot reads in the workbook (sheets_core.DRAFT)
_DRAFT = re.compile(r"\s*" + DRAFT_WORD + r"(?![^\W\d_])", re.IGNORECASE)     # the word itself, not 'Drafted'

KEY = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)*")
MONTH_KEY = re.compile(r"[0-9]{4}-(?:0[1-9]|1[0-2])")
_BRACES = re.compile(r"\{([^{}]*)\}")
_PLACEHOLDER = re.compile(r"([a-z][a-z0-9_]*(?:\.[a-z0-9_]+)*)(?:\|([A-Za-z0-9_]+))?")
# a word (letters of any script, joined by a hyphen or an apostrophe), a sentence end, a colon
_TOKEN = re.compile(r"(?P<word>[^\W\d_]+(?:[-'\u2019][^\W\d_]+)*)|(?P<end>[.!?]+)|(?P<colon>:)")
_LETTERS = re.compile(r"[^\W\d_]+")

# figures typed in words or signs
PERCENT_SIGNS = "%\uff05\u066a\u2030\u2031\ufe6a"      # % and its look-alikes (full width, Arabic, per mille ...)
NUMBER_WORDS = frozenset((
    "zero nought two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred hundreds thousand "
    "thousands million millions billion billions half halves halved halving quarter quarters quartered double doubled "
    "doubles doubling twice triple tripled triples tripling treble trebled trebles thrice dozen dozens").split())
_FOLD = re.compile(r"(?:two|three|four|five|six|seven|eight|nine|ten|twenty|hundred|thousand)fold")
FRACTION_ORDINALS = frozenset(("third thirds fourth fourths fifth fifths sixth sixths seventh sevenths eighth eighths "
                               "ninth ninths tenth tenths").split())
ORDINALS = FRACTION_ORDINALS | frozenset((
    "first second eleventh twelfth thirteenth fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth "
    "twentieth thirtieth").split())
ONE_OF_NEXT = frozenset("the its his her their our your these those them this that".split())
PERCENT_WORDS = frozenset(("percent", "pct"))

# the direction rule
UP_WORDS = frozenset((
    "rose rise rises rising risen up higher grew grow grows growing grown growth gain gains gained gaining increase "
    "increases increased increasing climb climbs climbed climbing jump jumps jumped jumping improve improves improved "
    "improving").split())
DOWN_WORDS = frozenset((
    "fell fall falls falling fallen down lower drop drops dropped dropping lose loses lost losing loss decline declines "
    "declined declining decrease decreases decreased decreasing slip slips slipped slipping slid shrink shrinks shrank "
    "shrunk shrinking").split())
NOT_UP_AFTER = frozenset((                      # "<word> up" that names no direction ("made up", "set up" ...)
    "made make makes making set sets setting pick picks picked picking follow follows followed following back backed "
    "sum sums summed summing end ends ended ending show shows showed showing turn turns turned open opens opened sign "
    "signs signed build builds built take takes took taken line lined top topped stock stocked round rounded clean "
    "cleaned catch caught mix mixed add added keep kept").split())
NOT_DOWN_AFTER = frozenset((                    # "broken down by salesman", "written down" ...
    "break breaks broke broken breaking write writes wrote written writing mark marks marked let shut settle settled "
    "calm").split())
NEGATORS = frozenset("not never no nor without".split())
# capitalised words that are no fact and need no note
COMMON_CAPITALS = frozenset("BHD VAT KPI KPIs ERP SKU SKUs YQ Focus".split())

_ID_RUNS = ((re.compile(r"(?<!\d)\d{9}(?!\d)"), "a nine-digit run (it reads like an ID number)"),
            (re.compile(r"\d{15,}"), "a run of fifteen or more digits (it reads like a card or an account number)"))
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?<!\d)(?:\+|00)?\d(?:[ \-]?\d){7,}(?!\d)")
_ISO_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


class CommentaryUnreadable(ValueError):
    """The commentary file cannot be read: said in one sentence."""


class FactsUnreadable(ValueError):
    """The facts file the commentary names cannot be read: said in one sentence."""


class CommentaryRefused(ValueError):
    """A slot or the file is refused: render_slots carries the whole Checked."""

    def __init__(self, checked: "Checked"):
        self.checked = checked
        first = (checked.problems or [f"{s}: {r[0]}" for s, r in checked.refused.items() if r] or ["refused"])[0]
        super().__init__(f"the commentary is refused ({len(checked.refused)} line(s), {len(checked.problems)} "
                         f"problem(s) with the file): {first}")


# ── facts: values and their renderings ─────────────────────────────────────────────────────────

def _r15(x: float) -> float:
    """x as a spreadsheet shows it: fifteen significant digits (scripts.ops.workbook.formula.r15)."""
    if x == 0 or not math.isfinite(x) or float(x).is_integer():
        return float(x)
    return float(f"{x:.15g}")


def _number(value, key: str):
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"the fact {key}: a figure, not {value!r}")
    if isinstance(value, (int, float, Decimal)):
        x = float(value)
    elif isinstance(value, str):
        try:
            x = float(Decimal(value.replace(",", "").strip()))
        except (InvalidOperation, ValueError):
            raise ValueError(f"the fact {key}: a figure, not {value!r}") from None
    else:
        raise ValueError(f"the fact {key}: a figure, not {type(value).__name__}")
    return _r15(x) if math.isfinite(x) else None


def _plain(key: str, value, kind: str):
    """The value as the facts file holds it (see make_fact)."""
    if kind in ("bhd", "pct", "signed"):
        return _number(value, key)
    if kind == "int":
        x = _number(value, key)
        return int(x) if x is not None and x.is_integer() else x
    if value is None:
        return None
    if kind == "date":
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        try:
            return date.fromisoformat(str(value).strip()[:10]).isoformat()
        except ValueError:
            raise ValueError(f"the fact {key}: a date, not {value!r}") from None
    if kind == "month":
        if isinstance(value, (date, datetime)):
            return f"{value:%Y-%m}"
        text = str(value).strip()
        if not MONTH_KEY.fullmatch(text):
            raise ValueError(f"the fact {key}: a month written YYYY-MM, not {value!r}")
        return text
    return str(value)


def make_fact(key: str, value, kind: str) -> dict:
    if not isinstance(key, str) or not KEY.fullmatch(key):
        raise ValueError(f"a fact's key is small letters, digits, _ and . (starting with a letter), not {key!r}")
    if kind not in FITS:
        raise ValueError(f"the fact {key}: no kind {kind!r} (the kinds: {', '.join(KINDS)})")
    plain = _plain(key, value, kind)
    shown = {} if plain is None else {fmt: render_value(plain, kind, fmt) for fmt in FITS[kind]}
    return {"value": plain, "kind": kind, "shown": shown}


def _dec(x) -> Decimal:
    if isinstance(x, Decimal):
        return x
    if isinstance(x, int):
        return Decimal(x)
    return Decimal(repr(_r15(float(x))))


def _quant(d: Decimal, dp: int) -> Decimal:
    q = d.quantize(Decimal(1).scaleb(-dp), rounding=ROUND_HALF_UP)
    return abs(q) if q == 0 else q            # never a "-0"


def _fixed(d: Decimal, dp: int) -> str:
    return f"{_quant(d, dp):,.{dp}f}"


def _count(d: Decimal) -> str:
    return f"{int(d):,}" if d == d.to_integral_value() else _fixed(d, 2)


def render_value(value, kind: str, fmt: str) -> str:
    if kind not in FITS:
        raise ValueError(f"no kind {kind!r}")
    if fmt not in FITS[kind]:
        raise ValueError(f"the format {fmt} does not fit {KIND_WORDS[kind]} ({', '.join(FITS[kind])})")
    if value is None:
        raise ValueError("no value to render")
    if fmt in ("bhd0", "bhd1", "bhd3"):
        return _fixed(_dec(value), int(fmt[-1]))
    if fmt == "int":
        return _count(_dec(value))
    if fmt in ("words", "Words"):
        d = _dec(value)
        if d == d.to_integral_value() and 0 <= int(d) < len(SMALL):
            word = SMALL[int(d)]
            return word[:1].upper() + word[1:] if fmt == "Words" else word
        return _count(d)
    if fmt in ("pct0", "pct1", "abs_pct0"):
        dp = 1 if fmt == "pct1" else 0
        q = _quant(_dec(value) * 100, dp)
        if fmt == "abs_pct0":
            q = abs(q)
        sign = "+" if kind == "signed" and fmt != "abs_pct0" and q > 0 else ""
        return f"{sign}{q:,.{dp}f}%"
    if fmt == "updown":
        x = float(value)
        return "up" if x > 0 else "down" if x < 0 else "unchanged"
    if fmt == "day":
        d = date.fromisoformat(str(value))
        return f"{d.day} {MONTHS[d.month - 1]}"
    if fmt == "month":
        y, m = (int(p) for p in str(value)[:7].split("-"))
        return f"{MONTHS[m - 1]} {y}"
    return str(value)


def digest_of(facts: dict) -> str:
    canon = json.dumps(facts, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return "sha256:" + hashlib.sha256(canon.encode("utf-8")).hexdigest()


def token_for(commentary_bytes: bytes, facts_digest: str) -> str:
    canon = json.dumps({"commentary": hashlib.sha256(bytes(commentary_bytes)).hexdigest(), "facts": str(facts_digest)},
                       sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canon.encode("utf-8")).hexdigest()
    return "".join(TOKEN_LETTERS[int(c, 16)] for c in digest[:TOKEN_LEN])


# ── reading the two files ──────────────────────────────────────────────────────────────────────

def read_commentary(path) -> tuple[dict, bytes]:
    path = Path(path)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise CommentaryUnreadable(f"No commentary file at {path}: the monthly build writes it beside its record, as "
                                   "report-month-YYYY-MM.commentary.json.") from None
    except OSError as exc:
        raise CommentaryUnreadable(f"The commentary file {path} cannot be read ({type(exc).__name__}).") from None
    try:
        doc = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise CommentaryUnreadable(f"{path.name} is not a commentary file: its JSON cannot be read "
                                   f"({str(exc)[:120]}).") from None
    if not isinstance(doc, dict):
        raise CommentaryUnreadable(f"{path.name} is not a commentary file: it holds no JSON object.")
    if doc.get("layout") != LAYOUT:
        raise CommentaryUnreadable(f"{path.name} is not a commentary file of layout '{LAYOUT}' (it says "
                                   f"{doc.get('layout')!r}).")
    if not isinstance(doc.get("slots"), dict):
        raise CommentaryUnreadable(f"{path.name} holds no slots: start again from the template the build writes.")
    return doc, raw


def facts_file_of(commentary_path, doc: dict) -> Path:
    name = doc.get("facts")
    if not isinstance(name, str) or not name.strip():
        raise CommentaryUnreadable("The commentary file names no facts file: start again from the template the build "
                                   "writes.")
    p = Path(name.strip())
    return p if p.is_absolute() else Path(commentary_path).parent / p


def read_facts(path) -> dict:
    path = Path(path)
    try:
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise FactsUnreadable(f"The facts file {path} is not there: build the month's report again (python -m "
                              "scripts.ops report month YYYY-MM).") from None
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise FactsUnreadable(f"The facts file {path.name} cannot be read ({type(exc).__name__}): build the month's "
                              "report again.") from None
    facts = doc.get("facts") if isinstance(doc, dict) else None
    if not isinstance(facts, dict) or not isinstance(doc.get("digest"), str) or not isinstance(doc.get("month"), str):
        raise FactsUnreadable(f"{path.name} is not a facts file (month, digest and facts): build the month's report "
                              "again.")
    for key, fact in facts.items():
        if (not isinstance(fact, dict) or fact.get("kind") not in FITS or not isinstance(fact.get("shown"), dict)
                or not all(isinstance(t, str) for t in fact["shown"].values())):
            raise FactsUnreadable(f"{path.name} holds a fact that is not one ({str(key)[:40]}): build the month's "
                                  "report again.")
    return doc


# ── the checks ─────────────────────────────────────────────────────────────────────────────────

@dataclass
class _Ph:
    raw: str
    key: str | None = None
    fmt: str | None = None
    fact: dict | None = None
    text: str = ""                    # the rendering ('' when refused)
    ok: bool = False

    @property
    def kind(self) -> str | None:
        return self.fact.get("kind") if self.fact else None


@dataclass
class _Tok:
    kind: str                         # 'word', 'end', 'colon', 'ph'
    text: str
    ph: _Ph | None = None


@dataclass
class _Change:
    start: int                        # token positions in the sentence (a pair spans several)
    end: int
    sign: int                         # +1, 0, -1 as the line will read
    label: str
    ph: _Ph | None = None             # the signed placeholder (None for a from-to pair)


@dataclass
class Checked:
    month: str | None
    label: str
    rendered: dict = field(default_factory=dict)
    refused: dict = field(default_factory=dict)
    problems: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems and not self.refused


def _tokens(text: str) -> list[_Tok]:
    out = []
    for m in _TOKEN.finditer(text):
        kind = m.lastgroup
        out.append(_Tok(kind, m.group(0)))
    return out


def _word(toks: list[_Tok], i: int) -> str | None:
    """The word at position i, folded; None when there is none (out of range, a placeholder or a sign)."""
    if 0 <= i < len(toks) and toks[i].kind == "word":
        return toks[i].text.casefold()
    return None


def _suggest(name: str, known) -> str:
    near = difflib.get_close_matches(name, list(known), n=1, cutoff=0.75)
    return f" (did you mean {near[0]}?)" if near else ""


def _placeholder(raw: str, inner: str, facts: dict, reasons: list[str]) -> _Ph:
    ph = _Ph(raw)
    m = _PLACEHOLDER.fullmatch(inner)
    if not m:
        reasons.append(f"{raw} is not a placeholder: write {{key}} or {{key|format}} (the key in small letters, "
                       "digits, _ and .)")
        return ph
    ph.key, ph.fmt = m.group(1), m.group(2)
    ph.fact = facts.get(ph.key)
    if ph.fact is None:
        reasons.append(f"{raw}: no fact is named {ph.key}{_suggest(ph.key, facts)}")
        return ph
    kind = ph.fact["kind"]
    if ph.fmt is None:
        ph.fmt = DEFAULT_FORMAT[kind]
    elif ph.fmt not in FORMATS:
        reasons.append(f"{raw}: no format is named {ph.fmt}{_suggest(ph.fmt, FORMATS)} (the formats of "
                       f"{KIND_WORDS[kind]}: {', '.join(FITS[kind])})")
        return ph
    elif ph.fmt not in FITS[kind]:
        reasons.append(f"{raw}: the format {ph.fmt} does not fit {ph.key}, {KIND_WORDS[kind]} (write "
                       f"{', '.join(FITS[kind])})")
        return ph
    shown = ph.fact["shown"].get(ph.fmt)
    if shown is None:
        reasons.append(f"{raw}: {ph.key} has no value this month"
                       + (" (the month before has nothing to compare with)" if kind == "signed" else ""))
        return ph
    ph.text, ph.ok = shown, True
    return ph


def _typed_figures(typed: str, reasons: list[str]) -> None:
    """Digits and % signs typed outside the placeholders."""
    found = [w.strip("()[]\"'.,;:!?") for w in typed.split() if any(ch.isnumeric() for ch in w)]
    if found:
        reasons.append("a figure typed outside a placeholder (" + ", ".join(f'"{w}"' for w in found[:3])
                       + "): every figure comes from a placeholder")
    signs = sorted({ch for ch in typed if ch in PERCENT_SIGNS})
    if signs:
        reasons.append(f'a "{signs[0]}" typed outside a placeholder: a share or a change comes from a placeholder '
                       "({key|pct0}, {key|abs_pct0} ...)")


def _number_words(toks: list[_Tok], reasons: list[str]) -> None:
    """The number words of NUMBER WORDS (module docstring), with their edge cases."""
    found: list[str] = []
    # a word joined by hyphens is split here: 'twenty-one', 'one-off'
    parts: list[tuple[str, int]] = []           # (part folded, token index) for every word part, in order
    for i, t in enumerate(toks):
        if t.kind == "word":
            for p in re.split(r"[-'\u2019]", t.text):
                parts.append((p.casefold(), i))
        else:
            parts.append(("", i))               # a placeholder or a sign ends a run of words
    for k, (w, _i) in enumerate(parts):
        if not w:
            continue
        prev = parts[k - 1][0] if k > 0 else ""
        nxt = parts[k + 1][0] if k + 1 < len(parts) else ""
        nxt2 = parts[k + 2][0] if k + 2 < len(parts) else ""
        if w == "one":
            if (nxt == "of" and nxt2 in ONE_OF_NEXT) or prev == "no" or nxt == "another":
                continue
            found.append("one")
        elif w in NUMBER_WORDS or _FOLD.fullmatch(w):
            found.append(w)
        elif w in PERCENT_WORDS or (w == "cent" and prev == "per"):
            reasons.append(f'"{"per cent" if w == "cent" else w}" typed outside a placeholder: a share or a change '
                           "comes from a placeholder ({key|pct0} ...)")
        elif w in ORDINALS and nxt == "of":
            reasons.append(f'"{w} of" types a share or a date: write it with a placeholder')
        elif w in FRACTION_ORDINALS and prev in ("a", "an"):
            reasons.append(f'"{prev} {w}" types a share: write it with a placeholder ({{key|pct0}})')
    if found:
        reasons.append("a number word typed outside a placeholder (" + ", ".join(f'"{w}"' for w in found[:3])
                       + "): say the count with {key|words} or {key|Words}")


def _direction(toks: list[_Tok], i: int) -> tuple[int, str | None] | None:
    """(+1 or -1, the negating word or None) for a direction word at position i; None when the word names no
    direction there."""
    w = _word(toks, i)
    if w is None:
        return None
    pol = 1 if w in UP_WORDS else -1 if w in DOWN_WORDS else 0
    if not pol:
        return None
    prev, nxt = _word(toks, i - 1), _word(toks, i + 1)
    if w == "up" and (prev in NOT_UP_AFTER or nxt == "to"):
        return None
    if w == "down" and (prev in NOT_DOWN_AFTER or nxt in ("payment", "payments")):
        return None
    negated = bool(prev) and (prev in NEGATORS or prev.endswith(("n't", "n\u2019t")))
    return pol, (toks[i - 1].text if negated else None)


def _as_float(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v) if math.isfinite(v) else None


def _shown_sign(ph: _Ph) -> int:
    """The sign of a signed placeholder as the line will read it: zero when its figure reads 0%."""
    v = _as_float(ph.fact.get("value"))
    raw = 0 if v is None else (1 if v > 0 else -1 if v < 0 else 0)
    if ph.fmt in ("pct0", "pct1", "abs_pct0") and not re.sub(r"[^0-9]", "", ph.text).strip("0"):
        return 0
    return raw


def _num_ph_after(sent: list[_Tok], i: int) -> int | None:
    """The position of a numeric placeholder right after position i, an optional BHD between."""
    j = i + 1
    if _word(sent, j) == "bhd":
        j += 1
    if j < len(sent) and sent[j].kind == "ph" and sent[j].ph.ok and sent[j].ph.kind in ("bhd", "int", "pct"):
        return j
    return None


def _changes(sent: list[_Tok]) -> list[_Change]:
    out: list[_Change] = []
    for i, t in enumerate(sent):
        if t.kind == "ph" and t.ph.ok and t.ph.kind == "signed":
            out.append(_Change(i, i, _shown_sign(t.ph), t.ph.raw, t.ph))
    for i in range(len(sent)):
        first = _word(sent, i)
        if first not in ("from", "to"):
            continue
        a = _num_ph_after(sent, i)
        if a is None or _word(sent, a + 1) != ("to" if first == "from" else "from"):
            continue
        b = _num_ph_after(sent, a + 1)
        if b is None or sent[a].ph.kind != sent[b].ph.kind:
            continue
        old, new = (sent[a].ph, sent[b].ph) if first == "from" else (sent[b].ph, sent[a].ph)
        x, y = _as_float(old.fact.get("value")), _as_float(new.fact.get("value"))
        if old.text == new.text or x is None or y is None:
            sign = 0
        else:
            sign = 1 if y > x else -1 if y < x else 0
        out.append(_Change(i, b, sign, f"from {old.raw} to {new.raw}"))
    return out


def _nearest(changes: list[_Change], i: int) -> _Change | None:
    def dist(c: _Change) -> tuple[int, int]:
        d = 0 if c.start <= i <= c.end else (c.start - i if c.start > i else i - c.end)
        return d, 0 if c.start > i else 1           # on a tie, the change after the word
    return min(changes, key=dist) if changes else None


def _sentences(toks: list[_Tok]) -> list[list[_Tok]]:
    out, cur = [], []
    for t in toks:
        if t.kind == "end":
            if cur:
                out.append(cur)
            cur = []
        else:
            cur.append(t)
    if cur:
        out.append(cur)
    return out


def _direction_rule(toks: list[_Tok], reasons: list[str]) -> None:
    for sent in _sentences(toks):
        changes = _changes(sent)
        matched: set[int] = set()
        for i, t in enumerate(sent):
            said = _direction(sent, i)
            if said is None:
                continue
            pol, negator = said
            if not changes:
                word = f"{negator} {t.text}" if negator else t.text
                reasons.append(f'"{word}" says a direction, but its sentence holds no change to check it against: say '
                               "the change with a placeholder ({key_change|abs_pct0}, or from {a} to {b})")
                continue
            c = _nearest(changes, i)
            if c.ph is not None:
                matched.add(id(c.ph))
            wrong = (c.sign == pol) if negator else (c.sign != pol)
            if wrong:
                what = "is unchanged as written" if c.sign == 0 else "went up" if c.sign > 0 else "went down"
                word = f"{negator} {t.text}" if negator else t.text
                reasons.append(f'"{word}" contradicts {c.label}, which {what}: the direction word must agree with '
                               "the figure")
        updown = {t.ph.key for t in sent if t.kind == "ph" and t.ph.ok and t.ph.fmt == "updown"}
        for t in sent:
            if (t.kind == "ph" and t.ph.ok and t.ph.fmt == "abs_pct0" and id(t.ph) not in matched
                    and t.ph.key not in updown):
                reasons.append(f"{t.ph.raw} drops the change's sign and nothing in its sentence says the direction: "
                               f"add a direction word (rose, fell, up, down ...) or {{{t.ph.key}|updown}}")


def _capitals(toks: list[_Tok], known: set[str], slot: str, notes: dict) -> None:
    started = False
    for t in toks:
        if t.kind in ("end", "colon"):
            started = False
            continue
        if t.kind == "ph":
            started = True
            continue
        initial, started = not started, True
        word = t.text
        if initial or len(word) < 2 or not word[:1].isupper() or word in COMMON_CAPITALS:
            continue
        if all(p in known for p in _LETTERS.findall(word)):
            continue
        notes.setdefault(word, []).append(slot)


def _rendered_ids(text: str, reasons: list[str]) -> None:
    for rx, why in _ID_RUNS:
        if rx.search(text):
            reasons.append(f"the line as it reads holds {why}: leave it out")
    if _EMAIL.search(text):
        reasons.append("the line as it reads holds an e-mail address: leave it out")
    for m in _PHONE.finditer(text):
        s = m.group(0)
        if re.fullmatch(r"\d{9}|\d{15,}", s) or _ISO_DAY.fullmatch(s):
            continue
        reasons.append("the line as it reads holds a phone-like number (eight digits or more): leave it out")
        break


def _check_slot(slot: str, text, facts: dict, known: set[str], notes: dict) -> tuple[str | None, list[str]]:
    if not isinstance(text, str):
        return None, ["not text: a slot holds one line of words"]
    if not text.strip():
        return None, ["empty: every line is written (a note with nothing to say says so in words)"]
    reasons: list[str] = []
    if _DRAFT.match(text):
        reasons.append("it still reads DRAFT, the build's stand-in: write the line itself")
    if any(unicodedata.category(ch) in ("Cc", "Zl", "Zp") for ch in text):
        reasons.append("a line break or a control character: a slot is one line")
    toks: list[_Tok] = []
    typed_parts: list[str] = []
    out_parts: list[str] = []
    placeholders = 0
    pos = 0
    for m in _BRACES.finditer(text):
        typed_parts.append(text[pos:m.start()])
        toks += _tokens(text[pos:m.start()])
        out_parts.append(text[pos:m.start()])
        ph = _placeholder(m.group(0), m.group(1), facts, reasons)
        placeholders += 1 if ph.key else 0
        toks.append(_Tok("ph", m.group(0), ph))
        out_parts.append(ph.text)
        pos = m.end()
    typed_parts.append(text[pos:])
    toks += _tokens(text[pos:])
    out_parts.append(text[pos:])
    typed = " ".join(typed_parts)
    if "{" in typed or "}" in typed:
        reasons.append("a brace that is not part of a placeholder: a placeholder is {key} or {key|format}")
    _typed_figures(typed, reasons)
    _number_words(toks, reasons)
    if slot in NEEDS_PLACEHOLDER and not placeholders:
        reasons.append("no placeholder: a highlight or a point names at least one of the month's figures, as "
                       "{key|format}")
    _direction_rule(toks, reasons)
    rendered = " ".join("".join(out_parts).split())
    _rendered_ids(rendered, reasons)
    _capitals(toks, known, slot, notes)
    return (None if reasons else rendered), list(dict.fromkeys(reasons))


def check_doc(doc: dict, facts_doc: dict) -> Checked:
    problems: list[str] = []
    facts_doc = facts_doc if isinstance(facts_doc, dict) else {}
    month = doc.get("month") if isinstance(doc, dict) else None
    facts = facts_doc.get("facts")
    if not isinstance(facts, dict) or not facts:
        facts = {}
        problems.append("the facts hold no figures: build the month's report again")
    facts = {k: f for k, f in facts.items() if isinstance(f, dict) and f.get("kind") in FITS
             and isinstance(f.get("shown"), dict)}
    label = str((facts.get("month") or {}).get("shown", {}).get("month") or month or "")
    out = Checked(month if isinstance(month, str) else None, label)
    if not isinstance(doc, dict):
        out.problems = problems + ["not a commentary file: it holds no JSON object"]
        return out
    if doc.get("layout") != LAYOUT:
        problems.append(f"not a commentary file of layout '{LAYOUT}'")
    if not isinstance(month, str) or not MONTH_KEY.fullmatch(month):
        problems.append(f"the commentary names no month written YYYY-MM ({month!r})")
    elif facts_doc.get("month") != month:
        problems.append(f"the commentary is for {month} and the facts are for {facts_doc.get('month')}: use the facts "
                        "file of the same month")
    digest = facts_doc.get("digest")
    try:
        own = digest_of(facts_doc.get("facts"))
    except (TypeError, ValueError):
        own = None
    if own != digest:
        problems.append("the facts file was changed after the build (its figures no longer match its digest): build "
                        "the month's report again")
    elif doc.get("facts_digest") != digest:
        problems.append("the month's figures changed after this draft was started (the facts file now carries "
                        f"{str(digest)[:19]}..., the draft was written against {str(doc.get('facts_digest'))[:19]}...):"
                        " read the facts again, check every line against them, then set facts_digest to the facts "
                        "file's digest")
    slots = doc.get("slots")
    if not isinstance(slots, dict):
        slots = {}
        problems.append("the commentary holds no slots: start again from the template the build writes")
    else:
        missing = [s for s in SLOTS if s not in slots]
        extra = [str(s) for s in slots if s not in SLOTS]
        if missing:
            problems.append("slots missing: " + ", ".join(missing))
        if extra:
            problems.append("slots that are not the report's: " + ", ".join(extra))
    known = {w for f in facts.values() for t in f["shown"].values() for w in _LETTERS.findall(str(t))}
    notes: dict[str, list[str]] = {}
    for slot in SLOTS:
        if slot not in slots:
            continue
        rendered, reasons = _check_slot(slot, slots[slot], facts, known, notes)
        out.rendered[slot] = rendered
        if reasons:
            out.refused[slot] = reasons
    out.problems = problems
    out.notes = [f'"{w}" is capitalised but no fact holds it ({", ".join(dict.fromkeys(s))}): a name should come '
                 "from a placeholder" for w, s in notes.items()]
    return out


def render_slots(doc: dict, facts_doc: dict) -> dict:
    checked = check_doc(doc, facts_doc)
    if not checked.ok:
        raise CommentaryRefused(checked)
    return {slot: checked.rendered[slot] for slot in SLOTS}


def commentary_fields(rendered: dict) -> dict:
    """The rendered slots as workbook.sheets_core.Commentary's keyword arguments."""
    return {"highlights": [rendered[f"highlight_{i}"] for i in range(1, 6)],
            "points": [rendered[f"point_{i}"] for i in range(1, 6)],
            "products_note": rendered["products_note"], "accounts_note": rendered["accounts_note"],
            "customers_note": rendered["customers_note"]}


# ── the command ────────────────────────────────────────────────────────────────────────────────

def add_arguments(parser) -> None:
    parser.add_argument("file", nargs="?", help="the filled-in commentary file (report-month-YYYY-MM.commentary.json)")


def _screen(checked: Checked) -> list[str]:
    lines: list[str] = []
    for slot in SLOTS:
        if slot not in checked.rendered and slot not in checked.refused:
            continue
        if slot == "highlight_1":
            lines.append("Highlights")
        elif slot == "point_1":
            lines.append("Points to note")
        name = (f"  {slot.rsplit('_', 1)[1]}." if slot.startswith(("highlight_", "point_"))
                else f"{SLOT_LABELS[slot]}:")
        if slot in checked.refused:
            lines.append(f"{name} REFUSED ({slot})")
            lines.extend(f"      - {r}" for r in checked.refused[slot])
        else:
            lines.append(f"{name} {checked.rendered[slot]}")
    return lines


def run(args) -> dict:
    out = {"ok": True, "command": COMMAND, "data_date": None, "text": "", "flags": {}, "files": [], "warnings": []}
    name = getattr(args, "file", None)
    if not name:
        return dict(out, ok=False, exit=1, text="Name the filled-in commentary file: python -m scripts.ops check "
                                                "<file> (the monthly build writes it beside its record, as "
                                                "report-month-YYYY-MM.commentary.json).")
    path = Path(name).expanduser().resolve()
    try:
        doc, raw = read_commentary(path)
        facts_doc = read_facts(facts_file_of(path, doc))
    except (CommentaryUnreadable, FactsUnreadable) as exc:
        return dict(out, ok=False, exit=1, text=str(exc))
    out["data_date"] = facts_doc.get("data_date")
    checked = check_doc(doc, facts_doc)
    month = checked.month or "?"
    notes = [f"Note: {n}." for n in checked.notes]
    flags = {"month": month, "slots": len(SLOTS), "refused": sorted(checked.refused), "problems": list(checked.problems),
             "notes": list(checked.notes)}
    if checked.ok:
        token = token_for(raw, facts_doc["digest"])
        final = f'python -m scripts.ops report month {month} --commentary "{path}" --token {token}'
        lines = [f"The comment lines for {checked.label}: all {len(SLOTS)} pass. As they will read:"]
        lines += _screen(checked) + notes + [f"FINAL: {final}"]
        return dict(out, ok=True, exit=0, text="\n".join(lines), flags=dict(flags, passed=True, token=token, final=final))
    n = len(checked.refused)
    lines = [f"The comment lines for {checked.label}: " + (f"{n} of {len(SLOTS)} refused" if n else "the lines pass")
             + (", and the file is refused" if checked.problems else "") + ". No FINAL line; nothing is written."]
    lines += [f"REFUSED: {p}." for p in checked.problems]
    lines += _screen(checked) + notes
    lines.append(f'Correct what is refused in "{path}" and run python -m scripts.ops check on it again.')
    return dict(out, ok=False, exit=2, text="\n".join(lines), flags=dict(flags, passed=False))
