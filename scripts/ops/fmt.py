"""Text rendering for the ops engine: every number a skill shows is formatted here, never by the model.

Money is BHD to the fils (3 decimals, half-up, Decimal all the way). Text is plain ASCII where it
can be, so it prints the same in PowerShell 5.1, Git Bash and the VS Code panel.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

Q3 = Decimal("0.001")
Q1 = Decimal("0.1")

# Words an audit line may never use about a voucher or a person (findings are "to check").
_BANNED = re.compile(r"\b(fraud\w*|theft|thief|stolen|steal\w*|embezzl\w*|leak\w*)\b", re.I)


def dec(x) -> Decimal:
    if x is None or x == "":
        return Decimal(0)
    if isinstance(x, Decimal):
        return x
    try:
        return Decimal(str(x).replace(",", "").strip())
    except Exception:  # noqa: BLE001
        return Decimal(0)


def q3(x) -> Decimal:
    return dec(x).quantize(Q3, rounding=ROUND_HALF_UP)


def bhd(x, dp: int = 3) -> str:
    """A number as BHD text: thousands grouped, dp decimals (three by default), half-up."""
    q = Decimal(1).scaleb(-dp) if dp else Decimal(1)
    return f"{dec(x).quantize(q, rounding=ROUND_HALF_UP):,.{dp}f}"


def units(x) -> str:
    v = dec(x)
    return f"{int(v):,}" if v == v.to_integral_value() else f"{v:,.2f}"


def pct(part, whole, dp: int = 1) -> str:
    """part / whole as '12.3%'; 'n/a' when there is nothing to divide by."""
    w = dec(whole)
    if w == 0:
        return "n/a"
    q = Decimal(1).scaleb(-dp) if dp else Decimal(1)
    return f"{(dec(part) / w * 100).quantize(q, rounding=ROUND_HALF_UP):,.{dp}f}%"


def change(cur, prev, dp: int = 0) -> str:
    """(cur - prev) / prev as '+12%' / '-8%'; 'n/a' with nothing to compare."""
    p = dec(prev)
    if p == 0:
        return "n/a"
    q = Decimal(1).scaleb(-dp) if dp else Decimal(1)
    v = ((dec(cur) - p) / p * 100).quantize(q, rounding=ROUND_HALF_UP)
    return f"{'+' if v > 0 else ''}{v:,.{dp}f}%"


def as_date(x) -> date | None:
    if x is None or x == "":
        return None
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    try:
        return date.fromisoformat(str(x)[:10])
    except ValueError:
        return None


def day(x) -> str:
    """'Tue 06 Oct' (with the year when it is not this data year is the caller's job)."""
    d = as_date(x)
    return d.strftime("%a %d %b") if d else "n/a"


def day_long(x) -> str:
    d = as_date(x)
    return f"{d.day} {d.strftime('%B %Y')}" if d else "n/a"


def grid(headers: list[str], rows: list[list], aligns: str | None = None) -> str:
    """A plain fixed-width table. aligns: one letter per column, 'l' or 'r' (default: first column
    left, the rest right)."""
    cells = [[str(c) if c is not None else "" for c in r] for r in rows]
    n = len(headers)
    aligns = (aligns or ("l" + "r" * (n - 1))).ljust(n, "r")
    widths = [max([len(headers[i])] + [len(r[i]) for r in cells if i < len(r)]) for i in range(n)]

    def line(vals: list[str]) -> str:
        parts = []
        for i in range(n):
            v = vals[i] if i < len(vals) else ""
            parts.append(v.ljust(widths[i]) if aligns[i] == "l" else v.rjust(widths[i]))
        return "  ".join(parts).rstrip()
    return "\n".join([line(headers)] + [line(r) for r in cells])


def careful(text: str) -> str:
    """Audit wording guard: raises when a line uses a word that reads as an accusation."""
    m = _BANNED.search(text or "")
    if m:
        raise ValueError(f"audit wording: '{m.group(0)}' is not used about a voucher or a person; say 'to check'")
    return text
