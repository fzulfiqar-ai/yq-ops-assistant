"""A small spreadsheet-formula evaluator, so that every formula is written WITH the value it computes.

The workbook is opened on phones, in mail previews and by viewers that never recalculate, so each
formula cell carries its cached value. That value is worked out here from the formula TEXT itself,
not by a second copy of the arithmetic: what the cell says and what it shows cannot drift apart.

It covers the subset of Excel the report uses, with Excel's own semantics where they matter:

  * text comparison ignores letter case (=, <>, <, >, MATCH, SUMIFS / COUNTIFS criteria);
  * criteria strings carry their operator ('>0', '>=46235', '<>x', 'Cash') and, for = and <>, the
    wildcards * and ? (~ escapes them); a whole cell must match;
  * MATCH(x, range, 0) is an exact, case-blind match that honours wildcards in a text x;
  * numbers compare as Excel shows them, to 15 significant digits (so 0.1 + 0.2 = 0.3);
  * ROUND and FIXED round half away from zero on the decimal value;
  * errors (#N/A, #DIV/0!, #VALUE!, #REF!) are values: they flow through arithmetic and IFERROR
    catches them; blank is 0 in arithmetic and "" in text;
  * a range compared with, or divided into, a value gives an array (SUMPRODUCT(--(A>x)),
    LOOKUP(2, 1/(A=x), B)).

Functions: SUM SUMIFS COUNTIFS COUNTIF COUNTA SUMPRODUCT INDEX MATCH LOOKUP IF IFERROR AND OR NOT
ROUND FIXED DATE YEAR MONTH DAY EDATE CHOOSE SUBSTITUTE MIN MAX ABS. Anything else raises
FormulaError, so a formula the evaluator does not know can never be written with a made-up value.

Pure: no I/O, no eval(). The workbook model (scripts/ops/workbook/book.py) supplies the cells.
"""
from __future__ import annotations

import math
import re
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

EPOCH = date(1899, 12, 30)          # Excel's day 0 in the 1900 system (serial 1 = 1 Jan 1900, after Feb 1900)


class FormulaError(ValueError):
    """The formula cannot be read or uses something this evaluator does not implement."""


class Err:
    """An Excel error value (#N/A, #DIV/0! ...). Errors are values, not exceptions."""
    __slots__ = ("code",)

    def __init__(self, code: str):
        self.code = code

    def __repr__(self) -> str:
        return self.code

    def __eq__(self, other) -> bool:
        return isinstance(other, Err) and other.code == self.code

    def __hash__(self) -> int:
        return hash(self.code)


NA, DIV0, VALUE, REF, NUM = Err("#N/A"), Err("#DIV/0!"), Err("#VALUE!"), Err("#REF!"), Err("#NUM!")


def serial(d: date) -> int:
    """A date as Excel's serial day number."""
    return (d - EPOCH).days


def from_serial(n) -> date:
    return EPOCH + timedelta(days=int(math.floor(float(n))))


# ── references ──────────────────────────────────────────────────────────────────────────────────

def col_index(letters: str) -> int:
    n = 0
    for ch in letters.upper():
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def col_letters(c: int) -> str:
    s = ""
    c += 1
    while c:
        c, rem = divmod(c - 1, 26)
        s = chr(65 + rem) + s
    return s


class Range:
    """A rectangular block of cells on one sheet (0-based, inclusive)."""
    __slots__ = ("sheet", "r1", "c1", "r2", "c2")

    def __init__(self, sheet: str, r1: int, c1: int, r2: int, c2: int):
        self.sheet = sheet
        self.r1, self.r2 = min(r1, r2), max(r1, r2)
        self.c1, self.c2 = min(c1, c2), max(c1, c2)

    @property
    def rows(self) -> int:
        return self.r2 - self.r1 + 1

    @property
    def cols(self) -> int:
        return self.c2 - self.c1 + 1

    def __len__(self) -> int:
        return self.rows * self.cols

    def __repr__(self) -> str:
        return f"{self.sheet}!{col_letters(self.c1)}{self.r1 + 1}:{col_letters(self.c2)}{self.r2 + 1}"


_CELL = re.compile(r"^\$?([A-Za-z]{1,3})\$?(\d+)$")


def parse_cell(text: str) -> tuple[int, int]:
    m = _CELL.match(text.strip())
    if not m:
        raise FormulaError(f"not a cell reference: {text!r}")
    return int(m.group(2)) - 1, col_index(m.group(1))


def parse_range(text: str, default_sheet: str) -> Range:
    """'Data!$A$5:$A$9' / 'A1' / "'My sheet'!B2:C3" -> Range."""
    text = text.strip().lstrip("=")
    sheet = default_sheet
    if "!" in text:
        sheet, text = text.rsplit("!", 1)
        sheet = sheet.strip("'").replace("''", "'")
    if ":" in text:
        a, b = text.split(":", 1)
        (r1, c1), (r2, c2) = parse_cell(a), parse_cell(b)
    else:
        r1, c1 = parse_cell(text)
        r2, c2 = r1, c1
    return Range(sheet, r1, c1, r2, c2)


# ── tokenizer ───────────────────────────────────────────────────────────────────────────────────

_TOKEN = re.compile(r"""
    (?P<ws>\s+)
  | (?P<str>"(?:[^"]|"")*")
  | (?P<ref>(?:(?:'(?:[^']|'')+'|[A-Za-z_][A-Za-z0-9_.]*)!)?\$?[A-Za-z]{1,3}\$?\d+(?::\$?[A-Za-z]{1,3}\$?\d+)?(?![A-Za-z0-9_(]))
  | (?P<num>(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)
  | (?P<name>[A-Za-z_][A-Za-z0-9_.]*)
  | (?P<op><=|>=|<>|[-+*/^&=<>%(),:])
""", re.X)


def tokenize(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    pos = 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m:
            raise FormulaError(f"cannot read the formula at {text[pos:pos + 12]!r}: {text!r}")
        pos = m.end()
        kind = m.lastgroup
        if kind == "ws":
            continue
        out.append((kind, m.group(kind)))
    return out


# ── parser (Excel precedence: : > negation > % > ^ > * / > + - > & > comparison) ─────────────────

class _Parser:
    def __init__(self, tokens: list[tuple[str, str]], text: str):
        self.t, self.i, self.text = tokens, 0, text

    def peek(self) -> tuple[str, str] | None:
        return self.t[self.i] if self.i < len(self.t) else None

    def take(self) -> tuple[str, str]:
        tok = self.peek()
        if tok is None:
            raise FormulaError(f"the formula ends too soon: {self.text!r}")
        self.i += 1
        return tok

    def expect(self, value: str) -> None:
        tok = self.take()
        if tok[1] != value:
            raise FormulaError(f"expected {value!r}, found {tok[1]!r}: {self.text!r}")

    def parse(self):
        node = self.comparison()
        if self.peek() is not None:
            raise FormulaError(f"unexpected {self.peek()[1]!r}: {self.text!r}")
        return node

    def comparison(self):
        node = self.concat()
        while (tok := self.peek()) and tok[0] == "op" and tok[1] in ("=", "<>", "<", ">", "<=", ">="):
            self.take()
            node = ("bin", tok[1], node, self.concat())
        return node

    def concat(self):
        node = self.additive()
        while (tok := self.peek()) and tok == ("op", "&"):
            self.take()
            node = ("bin", "&", node, self.additive())
        return node

    def additive(self):
        node = self.term()
        while (tok := self.peek()) and tok[0] == "op" and tok[1] in ("+", "-"):
            self.take()
            node = ("bin", tok[1], node, self.term())
        return node

    def term(self):
        node = self.power()
        while (tok := self.peek()) and tok[0] == "op" and tok[1] in ("*", "/"):
            self.take()
            node = ("bin", tok[1], node, self.power())
        return node

    def power(self):
        node = self.unary()
        while (tok := self.peek()) and tok == ("op", "^"):
            self.take()
            node = ("bin", "^", node, self.unary())
        return node

    def unary(self):
        tok = self.peek()
        if tok and tok[0] == "op" and tok[1] in ("-", "+"):
            self.take()
            inner = self.unary()
            return ("neg", inner) if tok[1] == "-" else ("pos", inner)
        node = self.primary()
        while (tok := self.peek()) and tok == ("op", "%"):
            self.take()
            node = ("pct", node)
        return node

    def primary(self):
        kind, val = self.take()
        if kind == "num":
            return ("num", float(val) if any(ch in val for ch in ".eE") else int(val))
        if kind == "str":
            return ("str", val[1:-1].replace('""', '"'))
        if kind == "ref":
            return ("ref", val)
        if kind == "name":
            nxt = self.peek()
            if nxt == ("op", "("):
                self.take()
                args = []
                if self.peek() != ("op", ")"):
                    while True:
                        args.append(self.comparison())
                        if self.peek() == ("op", ","):
                            self.take()
                            continue
                        break
                self.expect(")")
                return ("fn", val.upper(), args)
            if val.upper() in ("TRUE", "FALSE"):
                return ("bool", val.upper() == "TRUE")
            return ("name", val)
        if (kind, val) == ("op", "("):
            node = self.comparison()
            self.expect(")")
            return node
        raise FormulaError(f"unexpected {val!r}: {self.text!r}")


_PARSED: dict[str, tuple] = {}


def parse(text: str):
    """Formula text (with or without the leading '=') -> syntax tree (cached)."""
    key = text.lstrip("=")
    tree = _PARSED.get(key)
    if tree is None:
        tree = _Parser(tokenize(key), key).parse()
        _PARSED[key] = tree
    return tree


def references(text: str) -> list[str]:
    """The cell and range references a formula spells (for the build's self-checks)."""
    return [v for k, v in tokenize(text.lstrip("=")) if k == "ref"]


# ── value helpers (Excel semantics) ─────────────────────────────────────────────────────────────

def is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def r15(x: float) -> float:
    """x as Excel shows it: 15 significant digits (whole numbers, dates among them, are kept as they are)."""
    if isinstance(x, int) or x == 0 or not math.isfinite(x) or float(x).is_integer():
        return float(x)
    return float(f"{x:.15g}")


def to_num(v):
    """A scalar in arithmetic: blank 0, TRUE 1, numeric text read, other text #VALUE!."""
    if isinstance(v, Err):
        return v
    if v is None:
        return 0
    if isinstance(v, bool):
        return 1 if v else 0
    if is_num(v):
        return v
    if isinstance(v, str):
        s = v.strip().replace(",", "")
        try:
            return float(s) if s else VALUE
        except ValueError:
            return VALUE
    return VALUE


def general_text(v) -> str:
    """A value as text in a formula (&, SUBSTITUTE): Excel's General format."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if is_num(v):
        if float(v).is_integer() and abs(v) < 1e15:
            return str(int(v))
        s = f"{r15(float(v)):.15g}"
        return s
    return str(v)


def fold(s: str) -> str:
    return s.casefold()


def _cmp_scalars(a, b) -> int:
    """Excel's ordering: numbers < text < logical; blank is 0 against a number, "" against text.
    Text ignores case; numbers compare to 15 significant digits."""
    if a is None:
        a = "" if isinstance(b, str) else 0
    if b is None:
        b = "" if isinstance(a, str) else 0
    rank = lambda v: 2 if isinstance(v, bool) else 1 if isinstance(v, str) else 0  # noqa: E731
    ra, rb = rank(a), rank(b)
    if ra != rb:
        return -1 if ra < rb else 1
    if ra == 0:
        x, y = r15(float(a)), r15(float(b))
        return (x > y) - (x < y)
    if ra == 1:
        x, y = fold(a), fold(b)
        return (x > y) - (x < y)
    return (a > b) - (a < b)


def compare(op: str, a, b):
    if isinstance(a, Err):
        return a
    if isinstance(b, Err):
        return b
    c = _cmp_scalars(a, b)
    return {"=": c == 0, "<>": c != 0, "<": c < 0, ">": c > 0, "<=": c <= 0, ">=": c >= 0}[op]


def excel_round(x: float, digits: int) -> float:
    q = Decimal(1).scaleb(-int(digits))
    d = Decimal(repr(float(x))).quantize(q, rounding=ROUND_HALF_UP)
    return float(d)


def wildcard_regex(pattern: str) -> re.Pattern:
    """An Excel criteria / MATCH pattern (* any run, ? one character, ~ escapes) as a whole-text,
    case-blind regular expression."""
    out = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "~" and i + 1 < len(pattern) and pattern[i + 1] in "*?~":
            out.append(re.escape(pattern[i + 1]))
            i += 2
            continue
        out.append(".*" if ch == "*" else "." if ch == "?" else re.escape(ch))
        i += 1
    return re.compile("".join(out), re.S | re.I)


def has_wildcard(s: str) -> bool:
    return any(ch in s for ch in "*?~")


_CRIT_OP = re.compile(r"^(<=|>=|<>|=|<|>)?(.*)$", re.S)


def criterion(crit):
    """A SUMIFS / COUNTIFS criterion -> predicate on one cell value."""
    if isinstance(crit, Err):
        return lambda v: False
    if crit is None:
        crit = 0
    if isinstance(crit, bool):
        return lambda v, c=crit: isinstance(v, bool) and v == c
    if is_num(crit):
        target = r15(float(crit))
        return lambda v, t=target: is_num(v) and r15(float(v)) == t
    s = str(crit)
    op, rest = _CRIT_OP.match(s).groups()
    op = op or "="
    num = None
    t = rest.strip().replace(",", "")
    if t:
        try:
            num = float(t)
        except ValueError:
            num = None
    if num is not None:
        target = r15(num)
        same_text = fold(rest.strip())

        def equal(v, x=target, txt=same_text) -> bool:      # a number criterion also matches its own text
            if is_num(v):
                return r15(float(v)) == x
            return isinstance(v, str) and fold(v.strip()) == txt

        if op == "=":
            return equal
        if op == "<>":
            return lambda v, f=equal: not f(v)
        cmp = {"<": lambda a, b: a < b, ">": lambda a, b: a > b, "<=": lambda a, b: a <= b, ">=": lambda a, b: a >= b}[op]
        return lambda v, x=target, f=cmp: is_num(v) and f(r15(float(v)), x)
    if op in ("=", "<>"):
        if rest == "":
            pred = (lambda v: v is None or v == "")
        elif has_wildcard(rest):
            rx = wildcard_regex(rest)
            pred = (lambda v, r=rx: isinstance(v, str) and r.fullmatch(v) is not None)
        else:
            low = fold(rest)
            pred = (lambda v, x=low: isinstance(v, str) and fold(v) == x)
        if op == "=":
            return pred
        return lambda v, p=pred: not p(v)
    low = fold(rest)
    cmp = {"<": lambda a, b: a < b, ">": lambda a, b: a > b, "<=": lambda a, b: a <= b, ">=": lambda a, b: a >= b}[op]
    return lambda v, x=low, f=cmp: isinstance(v, str) and f(fold(v), x)


# ── the evaluator ───────────────────────────────────────────────────────────────────────────────

class Evaluator:
    """Evaluates formula text against a cell source.

    source must provide:
        value(sheet, row, col)      the value of one cell (formula cells evaluated on demand)
        column(sheet, col, r1, r2)  the values of one column slice, as a list
        name(name)                  a defined name -> Range (or None when it is not defined)
    """

    def __init__(self, source):
        self.src = source

    # -- public
    def evaluate(self, text: str, sheet: str):
        v = self._eval(parse(text), sheet)
        if isinstance(v, Range):
            v = self._scalar_of_range(v)
        elif isinstance(v, list):
            v = v[0] if v else VALUE
        return v

    # -- internals
    def _scalar_of_range(self, rng: Range):
        if len(rng) == 1:
            return self.src.value(rng.sheet, rng.r1, rng.c1)
        return VALUE                         # implicit intersection is not used by the report

    def values(self, rng: Range) -> list:
        """A range's values, row by row."""
        if rng.cols == 1:
            return self.src.column(rng.sheet, rng.c1, rng.r1, rng.r2)
        out = []
        for r in range(rng.r1, rng.r2 + 1):
            for c in range(rng.c1, rng.c2 + 1):
                out.append(self.src.value(rng.sheet, r, c))
        return out

    def _ref(self, text: str, sheet: str) -> Range:
        return parse_range(text, sheet)

    def _eval(self, node, sheet):
        kind = node[0]
        if kind in ("num", "str", "bool"):
            return node[1]
        if kind == "ref":
            rng = self._ref(node[1], sheet)
            if len(rng) == 1:
                return self.src.value(rng.sheet, rng.r1, rng.c1)
            return rng
        if kind == "name":
            rng = self.src.name(node[1])
            if rng is None:
                raise FormulaError(f"the name {node[1]!r} is not defined")
            if len(rng) == 1:
                return self.src.value(rng.sheet, rng.r1, rng.c1)
            return rng
        if kind == "neg":
            return self._unary(self._eval(node[1], sheet), lambda x: -x)
        if kind == "pos":
            return self._eval(node[1], sheet)
        if kind == "pct":
            return self._unary(self._eval(node[1], sheet), lambda x: x / 100)
        if kind == "bin":
            return self._binary(node[1], self._eval(node[2], sheet), self._eval(node[3], sheet))
        if kind == "fn":
            fn = getattr(self, "fn_" + node[1], None)
            if fn is None:
                raise FormulaError(f"the function {node[1]} is not implemented here")
            return fn(node[2], sheet)
        raise FormulaError(f"unknown node {kind}")

    def _arr(self, v):
        """Range -> list; list stays; scalar stays."""
        if isinstance(v, Range):
            return self.values(v) if len(v) > 1 else self.src.value(v.sheet, v.r1, v.c1)
        return v

    def _unary(self, v, f):
        v = self._arr(v)
        if isinstance(v, list):
            return [self._unary(x, f) for x in v]
        n = to_num(v)
        return n if isinstance(n, Err) else f(n)

    def _binary(self, op, a, b):
        a, b = self._arr(a), self._arr(b)
        if isinstance(a, list) or isinstance(b, list):
            la = a if isinstance(a, list) else None
            lb = b if isinstance(b, list) else None
            n = len(la if la is not None else lb)
            if la is not None and lb is not None and len(la) != len(lb):
                raise FormulaError("arrays of different lengths")
            return [self._binary(op, la[i] if la is not None else a, lb[i] if lb is not None else b) for i in range(n)]
        if op in ("=", "<>", "<", ">", "<=", ">="):
            return compare(op, a, b)
        if op == "&":
            if isinstance(a, Err):
                return a
            if isinstance(b, Err):
                return b
            return general_text(a) + general_text(b)
        x, y = to_num(a), to_num(b)
        if isinstance(x, Err):
            return x
        if isinstance(y, Err):
            return y
        if op == "+":
            return x + y
        if op == "-":
            return x - y
        if op == "*":
            return x * y
        if op == "/":
            return DIV0 if y == 0 else x / y
        if op == "^":
            try:
                return float(x) ** float(y)
            except (OverflowError, ValueError, ZeroDivisionError):
                return NUM
        raise FormulaError(f"unknown operator {op}")

    def _scalar(self, node, sheet):
        v = self._eval(node, sheet)
        if isinstance(v, Range):
            return self._scalar_of_range(v)
        if isinstance(v, list):
            return v[0] if v else VALUE
        return v

    def _range_arg(self, node, sheet) -> Range:
        v = self._eval_keep_range(node, sheet)
        if not isinstance(v, Range):
            raise FormulaError("a range was expected")
        return v

    def _eval_keep_range(self, node, sheet):
        """Like _eval, but a one-cell reference stays a Range (SUMIFS(A1, ...) is legal)."""
        if node[0] == "ref":
            return self._ref(node[1], sheet)
        if node[0] == "name":
            rng = self.src.name(node[1])
            if rng is None:
                raise FormulaError(f"the name {node[1]!r} is not defined")
            return rng
        return self._eval(node, sheet)

    def _flat_numbers(self, args, sheet) -> list:
        """SUM-style arguments: numbers from ranges (text, logical and blank skipped); scalars coerced."""
        out = []
        for a in args:
            v = self._eval_keep_range(a, sheet)
            if isinstance(v, Range):
                for x in self.values(v):
                    if isinstance(x, Err):
                        return [x]
                    if is_num(x):
                        out.append(x)
            elif isinstance(v, list):
                for x in v:
                    if isinstance(x, Err):
                        return [x]
                    if is_num(x):
                        out.append(x)
            else:
                n = to_num(v)
                if isinstance(n, Err):
                    return [n]
                out.append(n)
        return out

    # -- functions (args are syntax trees: each function decides what it evaluates)
    def fn_SUM(self, args, sheet):
        nums = self._flat_numbers(args, sheet)
        if nums and isinstance(nums[0], Err):
            return nums[0]
        total = 0
        for x in nums:
            total += x
        return total

    def fn_MIN(self, args, sheet):
        nums = self._flat_numbers(args, sheet)
        if nums and isinstance(nums[0], Err):
            return nums[0]
        return min(nums) if nums else 0

    def fn_MAX(self, args, sheet):
        nums = self._flat_numbers(args, sheet)
        if nums and isinstance(nums[0], Err):
            return nums[0]
        return max(nums) if nums else 0

    def fn_ABS(self, args, sheet):
        n = to_num(self._scalar(args[0], sheet))
        return n if isinstance(n, Err) else abs(n)

    def _criteria_rows(self, pairs, sheet) -> tuple[list[int], int]:
        """Indices (within the ranges) of the rows every criterion holds for; and the range length."""
        ranges, preds = [], []
        for rng_node, crit_node in pairs:
            rng = self._range_arg(rng_node, sheet)
            ranges.append(rng)
            preds.append(criterion(self._scalar(crit_node, sheet)))
        n = len(ranges[0])
        if any(len(r) != n for r in ranges):
            raise FormulaError("criteria ranges of different sizes")
        cols = [self.values(r) for r in ranges]
        idx = [i for i, v in enumerate(cols[0]) if preds[0](v)]
        for col, pred in zip(cols[1:], preds[1:]):
            if not idx:
                break
            idx = [i for i in idx if pred(col[i])]
        return idx, n

    def fn_SUMIFS(self, args, sheet):
        if len(args) < 3 or len(args) % 2 == 0:
            raise FormulaError("SUMIFS takes a sum range and criteria pairs")
        sum_rng = self._range_arg(args[0], sheet)
        pairs = list(zip(args[1::2], args[2::2]))
        idx, n = self._criteria_rows(pairs, sheet)
        if len(sum_rng) != n:
            raise FormulaError("SUMIFS ranges of different sizes")
        vals = self.values(sum_rng)
        total = 0
        for i in idx:
            v = vals[i]
            if isinstance(v, Err):
                return v
            if is_num(v):
                total += v
        return total

    def fn_COUNTIFS(self, args, sheet):
        if not args or len(args) % 2:
            raise FormulaError("COUNTIFS takes criteria pairs")
        idx, _n = self._criteria_rows(list(zip(args[0::2], args[1::2])), sheet)
        return len(idx)

    def fn_COUNTIF(self, args, sheet):
        return self.fn_COUNTIFS(args, sheet)

    def fn_COUNTA(self, args, sheet):
        n = 0
        for a in args:
            v = self._eval_keep_range(a, sheet)
            items = self.values(v) if isinstance(v, Range) else v if isinstance(v, list) else [v]
            n += sum(1 for x in items if x is not None)
        return n

    def fn_SUMPRODUCT(self, args, sheet):
        arrays = []
        for a in args:
            v = self._arr(self._eval_keep_range(a, sheet))
            arrays.append(v if isinstance(v, list) else [v])
        n = len(arrays[0])
        if any(len(x) != n for x in arrays):
            return VALUE
        total = 0
        for i in range(n):
            p = 1
            for arr in arrays:
                x = arr[i]
                if isinstance(x, Err):
                    return x
                p *= x if is_num(x) else 0
            total += p
        return total

    def fn_INDEX(self, args, sheet):
        rng = self._range_arg(args[0], sheet)
        r = to_num(self._scalar(args[1], sheet))
        if isinstance(r, Err):
            return r
        c = to_num(self._scalar(args[2], sheet)) if len(args) > 2 else None
        if isinstance(c, Err):
            return c
        r = int(r)
        if c is None:
            if rng.rows == 1:                       # INDEX(row range, n) = the nth column
                r, c = 1, r
            else:
                c = 1
        c = int(c)
        if r < 1 or c < 1 or r > rng.rows or c > rng.cols:
            return REF
        return self.src.value(rng.sheet, rng.r1 + r - 1, rng.c1 + c - 1)

    def fn_MATCH(self, args, sheet):
        x = self._scalar(args[0], sheet)
        if isinstance(x, Err):
            return x
        kind = to_num(self._scalar(args[2], sheet)) if len(args) > 2 else 1
        if kind != 0:
            raise FormulaError("only MATCH(..., 0) is implemented")
        vals = self.values(self._range_arg(args[1], sheet))
        if isinstance(x, str):
            if has_wildcard(x):
                rx = wildcard_regex(x)
                hit = lambda v: isinstance(v, str) and rx.fullmatch(v) is not None  # noqa: E731
            else:
                low = fold(x)
                hit = lambda v: isinstance(v, str) and fold(v) == low  # noqa: E731
        elif is_num(x):
            t = r15(float(x))
            hit = lambda v: is_num(v) and r15(float(v)) == t  # noqa: E731
        elif isinstance(x, bool):
            hit = lambda v: isinstance(v, bool) and v == x  # noqa: E731
        else:
            return NA
        for i, v in enumerate(vals):
            if hit(v):
                return i + 1
        return NA

    def fn_LOOKUP(self, args, sheet):
        """LOOKUP(x, lookup_vector, result_vector): the last position whose value is not above x
        (errors skipped): the 2, 1/(cond) idiom returns the last row the condition holds for."""
        x = self._scalar(args[0], sheet)
        look = self._arr(self._eval_keep_range(args[1], sheet))
        look = look if isinstance(look, list) else [look]
        res_v = self._eval_keep_range(args[2], sheet) if len(args) > 2 else None
        res = self._arr(res_v) if res_v is not None else look
        res = res if isinstance(res, list) else [res]
        pos = None
        for i, v in enumerate(look):
            if isinstance(v, Err) or v is None:
                continue
            same_kind = (is_num(v) and is_num(x)) or (isinstance(v, str) and isinstance(x, str))
            if same_kind and compare("<=", v, x) is True:
                pos = i
        if pos is None or pos >= len(res):
            return NA
        return res[pos]

    def fn_IF(self, args, sheet):
        cond = self._scalar(args[0], sheet)
        if isinstance(cond, Err):
            return cond
        if isinstance(cond, str):
            return VALUE
        truth = bool(to_num(cond)) if cond is not None else False
        if truth:
            return self._scalar(args[1], sheet) if len(args) > 1 else True
        return self._scalar(args[2], sheet) if len(args) > 2 else False

    def fn_IFERROR(self, args, sheet):
        v = self._scalar(args[0], sheet)
        return self._scalar(args[1], sheet) if isinstance(v, Err) else v

    def _logical(self, args, sheet) -> list:
        out = []
        for a in args:
            v = self._arr(self._eval_keep_range(a, sheet))
            for x in (v if isinstance(v, list) else [v]):
                if isinstance(x, Err):
                    return [x]
                if x is None or isinstance(x, str):
                    continue
                out.append(bool(to_num(x)))
        return out

    def fn_AND(self, args, sheet):
        vals = self._logical(args, sheet)
        if vals and isinstance(vals[0], Err):
            return vals[0]
        return all(vals) if vals else VALUE

    def fn_OR(self, args, sheet):
        vals = self._logical(args, sheet)
        if vals and isinstance(vals[0], Err):
            return vals[0]
        return any(vals) if vals else VALUE

    def fn_NOT(self, args, sheet):
        v = to_num(self._scalar(args[0], sheet))
        return v if isinstance(v, Err) else not bool(v)

    def fn_ROUND(self, args, sheet):
        x = to_num(self._scalar(args[0], sheet))
        d = to_num(self._scalar(args[1], sheet)) if len(args) > 1 else 0
        if isinstance(x, Err):
            return x
        if isinstance(d, Err):
            return d
        return excel_round(x, int(d))

    def fn_FIXED(self, args, sheet):
        x = to_num(self._scalar(args[0], sheet))
        d = to_num(self._scalar(args[1], sheet)) if len(args) > 1 else 2
        no_commas = bool(to_num(self._scalar(args[2], sheet))) if len(args) > 2 else False
        if isinstance(x, Err):
            return x
        if isinstance(d, Err):
            return d
        d = int(d)
        q = Decimal(1).scaleb(-max(d, 0))
        v = Decimal(repr(float(x))).quantize(q, rounding=ROUND_HALF_UP)
        if d < 0:
            step = Decimal(10) ** (-d)
            v = (v / step).quantize(Decimal(1), rounding=ROUND_HALF_UP) * step
        text = f"{v:.{max(d, 0)}f}" if no_commas else f"{v:,.{max(d, 0)}f}"
        return "0" if text in ("-0", "-0.0") else text

    def _date_arg(self, node, sheet):
        n = to_num(self._scalar(node, sheet))
        if isinstance(n, Err):
            return n
        if n < 1:
            return NUM
        return from_serial(n)

    def fn_DATE(self, args, sheet):
        y, m, d = (to_num(self._scalar(a, sheet)) for a in args[:3])
        for v in (y, m, d):
            if isinstance(v, Err):
                return v
        y, m, d = int(y), int(m), int(d)
        y += (m - 1) // 12
        m = (m - 1) % 12 + 1
        return serial(date(y, m, 1) + timedelta(days=d - 1))

    def fn_YEAR(self, args, sheet):
        d = self._date_arg(args[0], sheet)
        return d if isinstance(d, Err) else d.year

    def fn_MONTH(self, args, sheet):
        d = self._date_arg(args[0], sheet)
        return d if isinstance(d, Err) else d.month

    def fn_DAY(self, args, sheet):
        d = self._date_arg(args[0], sheet)
        return d if isinstance(d, Err) else d.day

    def fn_EDATE(self, args, sheet):
        d = self._date_arg(args[0], sheet)
        k = to_num(self._scalar(args[1], sheet))
        if isinstance(d, Err):
            return d
        if isinstance(k, Err):
            return k
        months = d.year * 12 + (d.month - 1) + int(k)
        y, m = divmod(months, 12)
        m += 1
        nxt = date(y + (m == 12), m % 12 + 1, 1)
        last = (nxt - timedelta(days=1)).day
        return serial(date(y, m, min(d.day, last)))

    def fn_CHOOSE(self, args, sheet):
        i = to_num(self._scalar(args[0], sheet))
        if isinstance(i, Err):
            return i
        i = int(i)
        if i < 1 or i >= len(args):
            return VALUE
        return self._scalar(args[i], sheet)

    def fn_SUBSTITUTE(self, args, sheet):
        vals = [self._scalar(a, sheet) for a in args]
        for v in vals:
            if isinstance(v, Err):
                return v
        text, old, new = (general_text(v) for v in vals[:3])
        if not old:
            return text
        if len(vals) > 3:
            nth = int(to_num(vals[3]))
            parts = text.split(old)
            if nth < 1 or nth >= len(parts):
                return text
            return old.join(parts[:nth]) + new + old.join(parts[nth:])
        return text.replace(old, new)
