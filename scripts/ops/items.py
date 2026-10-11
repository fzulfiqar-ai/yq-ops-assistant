"""Item names: the bridge from the stock ledger's names to the names every other report uses.

The Focus stock ledger does not name an item the way the stock balance and the sales day book do.
It writes "<name> <item code>": the name the other reports use, a space, then the item code. No
ledger name equals a stock or sales name, so a join on equality silently finds nothing.

Item codes contain spaces, so the code cannot be cut off by counting words. The rule that holds on
every item of the real ledger, with no item claimed twice, is:

    a ledger name stands for the LONGEST known name that equals it, or that it starts with
    followed by a space.

"Known names" are whatever the caller wants to join to. Build one Bridge per target: the stock
balance names for stock joins, the day book names for sales joins. With the two lists merged the
longest match can be a name from the other report, and the join then misses.

Which items are accessories is told by the ledger as well: an item that has EVER moved in the main
store is an accessory. SIM packs and handsets sit inside the vans' warehouses and an accessory can
have "sim" in its name, so neither a warehouse name nor a word in the item name can tell.

Nothing here reads a file or the database. The exceptions and the never-an-accessory list come
from config (business_data/ops/config.json); no item name lives in this file.
"""
from __future__ import annotations

from scripts.ops import config, ledger


def _blank(name) -> bool:
    """None, or a blank spreadsheet cell (pandas reads one as NaN)."""
    return name is None or (isinstance(name, float) and name != name)


def norm(name) -> str:
    """A name as it is compared: whitespace collapsed, case ignored ('' for nothing)."""
    return "" if _blank(name) else " ".join(str(name).split()).casefold()


def leading_code(name) -> str:
    """The model code a name starts with: its first word, in capitals ('' for a blank name).

    Variants of one model share it (a colour, a length), so it is never a key for one item: join
    items through Bridge. Some portal views group costs on this word (split_part(item_name, ' ', 1));
    a figure read from them is per model, not per item."""
    words = [] if _blank(name) else str(name).split()
    return words[0].upper() if words else ""


class Bridge:
    """Ledger item name -> the known name it stands for.

        bridge = Bridge(stock_names)
        bridge.get("ITEM-01 Cable One C 01")      # -> "ITEM-01 Cable One", or None

    get() answers with a name this bridge was given, or None: never with anything else.

    config 'items.bridge_exceptions' (ledger name -> known name) wins over the rule, always: it is
    the owner's word on what the item is. When that word is not a name this bridge was given (a
    slip of the pen, a name only the other report has, an empty value) the item is unplaced: it
    shows in unmapped(), in a block of its own, where it can be seen and put right. The rule is
    not tried behind an exception, so a slip can never pass as a match.
    """

    def __init__(self, known_names, exceptions: dict | None = None):
        self._known: dict[str, str] = {}
        for name in known_names or ():
            key = norm(name)
            if key:
                self._known.setdefault(key, name if isinstance(name, str) else str(name))
        raw = config.get("items.bridge_exceptions") if exceptions is None else exceptions
        self._exceptions: dict[str, object] = {norm(k): v for k, v in dict(raw).items() if norm(k)}
        self._memo: dict[str, str | None] = {}

    def __len__(self) -> int:
        return len(self._known)

    def get(self, ledger_name) -> str | None:
        """The known name as it was given, or None when the ledger name stands for none of them."""
        key = norm(ledger_name)
        if not key:
            return None
        if key not in self._memo:
            self._memo[key] = self._find(key)
        return self._memo[key]

    def _find(self, key: str) -> str | None:
        if key in self._exceptions:
            # None when the target is empty or is not a known name ('' is never a key of _known)
            return self._known.get(norm(self._exceptions[key]))
        # Both sides are whitespace-collapsed, so "equals it, or is a prefix of it followed by a
        # space" is: the known name is the first n whole words of the ledger name. Longest first.
        words = key.split(" ")
        for n in range(len(words), 0, -1):
            hit = self._known.get(" ".join(words[:n]))
            if hit is not None:
                return hit
        return None

    def unmapped(self, ledger_names) -> list[str]:
        """The ledger names this bridge cannot place, each once, in the order given. They belong in
        a block of their own: an item nobody can name enters no accessory figure."""
        out: list[str] = []
        seen: set[str] = set()
        for name in ledger_names:
            key = norm(name)
            if key and key not in seen and self.get(name) is None:
                out.append(name)
            seen.add(key)
        return out

    def collisions(self, ledger_names) -> dict[str, list[str]]:
        """{known name: [ledger names]} wherever more than one of these ledger names stands for the
        same known name. Empty on a sound item list; anything here is to check before the two are
        added together."""
        by_known: dict[str, list[str]] = {}
        seen: set[str] = set()
        for name in ledger_names:
            key = norm(name)
            if not key or key in seen:
                continue
            seen.add(key)
            known = self.get(name)
            if known is not None:
                by_known.setdefault(known, []).append(name)
        return {known: names for known, names in by_known.items() if len(names) > 1}


def accessory_items(rows, main: str | None = None, bridge: Bridge | None = None) -> set[str]:
    """The ledger item names that count as accessories: every item that has EVER moved in the main
    store (config 'warehouses.main', or `main`), less config 'items.not_accessories'.

    `rows` are the movements of the WHOLE ledger (scripts.ops.ledger: each has 'item' and 'wh'),
    never a window of it: "ever" is judged on the rows given. Rows with not one movement in the
    main store mean the store's name is misspelt or a window was passed, and this stops
    (ValueError) instead of answering "no accessories".

    The never-an-accessory list is compared with the ledger name and with the name the bridge gives
    it, so the owner can write an item the way any report names it. A list that is not empty
    therefore needs the Bridge: without one this stops (TypeError), because a name written the way
    the stock report has it would be passed over without a word. Never a filter on a warehouse name
    and never on a word such as "sim" inside the item name."""
    rows = rows if isinstance(rows, (list, tuple)) else list(rows)
    never = {norm(name) for name in config.get("items.not_accessories")}
    never.discard("")                          # a blank entry names no item (and no unplaced one)
    if never and bridge is None:
        raise TypeError("accessory_items: pass a Bridge. config 'items.not_accessories' is not empty, and a name "
                        "written the way the other reports have it is matched only through one")
    held = ledger.main_items(rows, main)
    if rows and not held:
        source = "config 'warehouses.main'" if main is None else "the `main` argument"
        store = config.get("warehouses.main") if main is None else main
        raise ValueError(f"accessory_items: no movement was found in the main store {store!r} ({source}): "
                         "check how the name is spelt, and pass the whole ledger, never a window of it")
    if not never:
        return held
    out: set[str] = set()
    for item in held:
        if norm(item) in never:
            continue
        known = bridge.get(item)
        if known is not None and norm(known) in never:
            continue
        out.add(item)
    return out
