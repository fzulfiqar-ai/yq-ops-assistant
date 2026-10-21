"""The one action register: business_data/ops/alerts.jsonl and explained.json.

Every command that finds something to do or to check records it here, so the brief, the watch
screen and the weekly workbook show the same items under the same ids.

  * An item's KEY is its rule plus a Focus number: 'xfer_open|SIO:YQ-26-01-5', 'qty_diff|SIO:YQ-26-01-5',
    'inv_changed|<invoice>'. An item with no voucher is keyed on rule + item code (+ a date). A van or
    a rep never appears in a key by name: use ref_hash(). Row ids of the database are never used
    (the loader rewrites them on every load).
  * alerts.jsonl is append-only: one line per change of an item (open, cleared, explained, done).
    The current state of a key is its last line.
  * The first time a rule runs, everything it finds is the BASELINE: counted once, not listed day
    after day. Only what appears later is new.
  * An item he explains is kept in explained.json by its key and stays off the list.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal

from scripts.ops import fmt, paths

LEVELS = ("ACT", "CHECK")
STATES = ("open", "cleared", "explained", "done")
_LEVEL_RANK = {"ACT": 0, "CHECK": 1}
REOPEN_GROWTH = Decimal(1) / Decimal(4)     # an explained item returns when it is a quarter larger
WHAT_WIDTH = 60                             # the What column of the list; the full text is in the file


def alerts_file():
    return paths.ops_dir() / "alerts.jsonl"


def explained_file():
    return paths.ops_dir() / "explained.json"


def make_key(rule: str, ref: str, *more: str) -> str:
    parts = [rule, ref] + [m for m in more if m]
    if any("|" in p for p in parts):
        raise ValueError("a register key part may not contain '|'")
    return "|".join(parts)


def ref_hash(name: str) -> str:
    """A stable stand-in for a van or rep inside a key (the name itself never goes in a key)."""
    digest = hashlib.sha256(" ".join(str(name).lower().split()).encode("utf-8")).hexdigest()
    return "h" + digest.translate(str.maketrans("0123456789", "ghijklmnop"))[:8]


def _read_lines() -> list[dict]:
    f = alerts_file()
    if not f.is_file():
        return []
    rows = []
    for line in f.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def _append(rows: list[dict]) -> None:
    if not rows:
        return
    f = alerts_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    with open(f, "a", encoding="utf-8", newline="\n") as out:
        for r in rows:
            out.write(json.dumps(r, sort_keys=True, default=str) + "\n")


def current() -> dict[str, dict]:
    """key -> its latest line."""
    out: dict[str, dict] = {}
    for row in _read_lines():
        if row.get("key"):
            out[row["key"]] = row
    return out


def explained() -> dict:
    f = explained_file()
    if not f.is_file():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except ValueError:
        return {}


def _next_id(existing: dict[str, dict]) -> int:
    nums = [int(str(r.get("id", "A0"))[1:]) for r in existing.values() if str(r.get("id", "")).startswith("A")
            and str(r.get("id"))[1:].isdigit()]
    return max(nums, default=0) + 1


def _facts(item: dict) -> str:
    return json.dumps({k: item.get(k) for k in ("level", "title", "detail", "bhd", "evidence")},
                      sort_keys=True, default=str)


def has_run(rule: str) -> bool:
    """True once `rule` has recorded anything, its first-run marker included."""
    return any(r.get("rule") == rule for r in _read_lines())


def _grew(now, then) -> bool:
    """True when a size (BHD or a count) is more than a quarter above what he explained."""
    if now is None:
        return False
    a, b = Decimal(str(now)), Decimal(str(then or 0))
    return a > b * (1 + REOPEN_GROWTH) and a > b


def _still_explained(item: dict, note: dict) -> bool:
    """An explained item stays off while it is the thing he explained: it comes back when its
    level is more pressing, its BHD or count is more than a quarter larger, or it names a voucher
    or invoice that was not there when he explained it."""
    was = note.get("facts")
    if not isinstance(was, dict):
        return True                           # explained before facts were kept: off for good
    if _LEVEL_RANK.get(item.get("level"), 9) < _LEVEL_RANK.get(was.get("level"), 9):
        return False
    if _grew(item.get("bhd"), was.get("bhd")) or _grew(item.get("count"), was.get("count")):
        return False
    return not (set(item.get("evidence") or []) - set(was.get("evidence") or []))


def record(rule: str, found: list[dict], data_date: str | None, baseline: bool = True,
           clear_missing: bool = True) -> dict:
    """Record what `rule` found on this run. Each found item: {key, level, title, detail, who, due,
    bhd, count, evidence[list of voucher or invoice numbers]}.

    baseline=True       the rule's FIRST run is a baseline (counted once, not listed). Pass False for
                        a rule whose first finds must be listed (a fixed-word key, an event).
    clear_missing=True  items of this rule that were open and are no longer found are marked
                        cleared. Pass False for an EVENT rule (an invoice changed at this load): the
                        item stays open until he marks it done or explained.
    Returns counts {new, same, cleared, baseline, explained}."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = _read_lines()
    state: dict[str, dict] = {r["key"]: r for r in lines if r.get("key")}
    off = explained()
    # a rule's first run leaves a marker line even when it finds nothing, so only that run is the baseline
    first_run = not any(r.get("rule") == rule for r in lines)
    as_baseline = first_run and baseline
    counts = {"new": 0, "same": 0, "cleared": 0, "baseline": 0, "explained": 0}
    out: list[dict] = []
    reopened: list[str] = []
    if first_run:
        out.append({"at": now, "rule": rule, "event": "first_run", "found": len(found), "data_date": data_date})
    next_id = _next_id(state)
    seen: set[str] = set()
    for item in found:
        key = item["key"]
        if not key.startswith(rule + "|"):
            raise ValueError(f"item key {key!r} does not belong to rule {rule!r}")
        if item.get("level") not in LEVELS:
            raise ValueError(f"level must be one of {LEVELS}")
        for field in ("title", "detail"):
            fmt.careful(str(item.get(field) or ""))
        seen.add(key)
        if key in off:
            if _still_explained(item, off[key]):
                counts["explained"] += 1
                continue
            reopened.append(key)
        prev = state.get(key)
        if prev and prev.get("state") == "open" and prev.get("facts") == _facts(item):
            counts["same"] += 1
            continue
        if prev and prev.get("state") == "done" and prev.get("facts") == _facts(item):
            counts["same"] += 1
            continue
        row = {
            "at": now, "key": key, "rule": rule, "state": "open", "level": item["level"],
            "title": item.get("title"), "detail": item.get("detail"), "who": item.get("who"),
            "due": item.get("due"), "bhd": str(fmt.q3(item.get("bhd"))) if item.get("bhd") is not None else None,
            "evidence": list(item.get("evidence") or []), "data_date": data_date, "facts": _facts(item),
            "count": item.get("count"),
            "id": prev.get("id") if prev else f"A{next_id}",
            "first_seen": prev.get("first_seen") if prev else now,
            # a key that comes back after it was cleared or explained is news, never baseline
            "baseline": bool(as_baseline) if not prev else (bool(prev.get("baseline"))
                                                            and prev.get("state") == "open"),
        }
        if key in reopened:
            row["reopened"] = True
        if not prev:
            next_id += 1
        counts["baseline" if (as_baseline and not prev) else "new"] += 1
        out.append(row)
    if clear_missing:
        for key, prev in state.items():
            if prev.get("rule") == rule and prev.get("state") == "open" and key not in seen:
                out.append(dict(prev, at=now, state="cleared", data_date=data_date))
                counts["cleared"] += 1
    _append(out)
    # an explanation ends with its condition: a key of this rule that he explained and that this
    # run no longer finds is forgotten, so the day the same thing happens again it is news
    ended = [k for k in off if clear_missing and k.startswith(rule + "|") and k not in seen]
    if reopened or ended:
        for key in reopened + ended:
            off.pop(key, None)
        paths.write_text(explained_file(), json.dumps(off, indent=1, sort_keys=True) + "\n")
    return counts


def _below(row: dict, floor, floor_rules) -> bool:
    """True when `row` is an item of floor_rules valued below the floor. An item with no BHD is never below."""
    if floor is None or row.get("rule") not in floor_rules or row.get("bhd") in (None, ""):
        return False
    try:
        return Decimal(str(row["bhd"])) < Decimal(str(floor))
    except ArithmeticError:
        return False


def open_items(include_baseline: bool = False, *, floor=None, floor_rules=()) -> list[dict]:
    """Open items, most pressing first: ACT before CHECK, then the larger BHD, then the older. With a floor,
    the items of `floor_rules` valued below it are folded under it, left out of the list (under_floor
    counts them); an item with no BHD, and an item of any other rule, is never folded."""
    rows = [r for r in current().values() if r.get("state") == "open"
            and (include_baseline or not r.get("baseline")) and not _below(r, floor, floor_rules)]
    return sorted(rows, key=lambda r: (_LEVEL_RANK.get(r.get("level"), 9), -Decimal(str(r.get("bhd") or 0)),
                                       str(r.get("first_seen") or "")))


def under_floor(floor, floor_rules) -> dict[str, int]:
    """{rule: how many open items open_items folds under the floor}, rules with none left out. The baseline
    is not counted here: it is counted once, as the baseline."""
    out: dict[str, int] = {}
    for r in current().values():
        if r.get("state") == "open" and not r.get("baseline") and _below(r, floor, floor_rules):
            out[r["rule"]] = out.get(r["rule"], 0) + 1
    return dict(sorted(out.items()))


def baseline_summary() -> list[dict]:
    """[{rule, count, bhd}] of the open baseline items (shown once, as one table)."""
    acc: dict[str, dict] = {}
    for r in current().values():
        if r.get("state") == "open" and r.get("baseline"):
            a = acc.setdefault(r["rule"], {"rule": r["rule"], "count": 0, "bhd": Decimal(0)})
            a["count"] += 1
            a["bhd"] += Decimal(str(r.get("bhd") or 0))
    return sorted(acc.values(), key=lambda a: a["rule"])


def find(id_or_key: str) -> dict | None:
    state = current()
    if id_or_key in state:
        return state[id_or_key]
    for row in state.values():
        if str(row.get("id", "")).lower() == id_or_key.lower():
            return row
    return None


def set_state(id_or_key: str, new_state: str, reason: str | None = None) -> dict:
    """Mark an item explained (kept off, by key, while it stays what he explained: see
    _still_explained) or done. His words go in `reason`."""
    if new_state not in ("explained", "done"):
        raise ValueError("an item can be set to 'explained' or 'done'")
    row = find(id_or_key)
    if row is None:
        raise KeyError(f"no item {id_or_key!r} on the register")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    new = dict(row, at=now, state=new_state, reason=reason)
    _append([new])
    if new_state == "explained":
        off = explained()
        off[row["key"]] = {"reason": reason, "at": now, "id": row.get("id"),
                           "facts": {"level": row.get("level"), "bhd": row.get("bhd"), "count": row.get("count"),
                                     "evidence": list(row.get("evidence") or [])}}
        paths.write_text(explained_file(), json.dumps(off, indent=1, sort_keys=True) + "\n")
    return new


def _short(text: str, width: int = WHAT_WIDTH) -> str:
    t = " ".join(str(text or "").split())
    if len(t) <= width:
        return t
    cut = t[:width - 3].rsplit(" ", 1)[0] if " " in t[:width - 3] else t[:width - 3]
    return cut.rstrip(" ,.:;-") + "..."


def render(items: list[dict], limit: int | None = None) -> str:
    """The items as a fixed-width block: id, level, who, what, by, evidence, BHD. A long 'what' is
    cut at WHAT_WIDTH characters so one item cannot widen the whole list. When `limit` leaves items
    out, the last line counts them, "(+3 more open)", and points nowhere: no command lists the whole
    register yet, and a line must not send a reader to one that is not built."""
    shown = items[:limit] if limit else items
    if not shown:
        return "Nothing open."
    rows = []
    for r in shown:
        rows.append([r.get("id", ""), r.get("level", ""), r.get("who") or "", _short(r.get("title")),
                     fmt.day(r.get("due")) if r.get("due") else "", ", ".join((r.get("evidence") or [])[:3]),
                     fmt.bhd(r.get("bhd")) if r.get("bhd") is not None else ""])
    text = fmt.grid(["ID", "Level", "Who", "What", "By", "Evidence", "BHD"], rows, "lllllll"[:6] + "r")
    more = len(items) - len(shown)
    return text + (f"\n(+{more} more open)" if more > 0 else "")
