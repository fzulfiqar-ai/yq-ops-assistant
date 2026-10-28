"""Server-side catalogue search for YQ Assist (the marketplace chat).

The browser searches with MiniSearch (web/src/market/lib/search.ts). The chat needs the same
search on the server, over the same ~180 PUBLIC items, so the assistant can ground its answer
on what it found. This is a small port of that file: the same synonym groups (built into each
item at index time), the same field weights, prefix and light fuzzy matching, AND first then OR,
and the same ranking (what can be had today first, then score, then best seller / trending,
then shelf order). Arabic is not read here: the chat's first model call turns any language into
the catalogue's English words, and when no model answers the browser runs its own search.

tests/test_market_assist.py holds SYNONYMS and CATEGORY_WORDS equal to search.ts, so the two
searches never drift apart.
"""
from __future__ import annotations

import re

SYNONYMS: dict[str, list[str]] = {
    "usb-c": ["type c", "typec", "type-c", "usb c", "usbc", "c type", "ctype"],
    "lightning": ["iphone", "apple", "ios", "ipad"],
    "micro": ["micro usb", "microusb", "android old", "v8"],
    "earbuds": ["tws", "airpods", "buds", "true wireless", "earpods", "pods"],
    "bluetooth headset": ["bt headset", "handsfree", "hands free", "hfs", "earpiece"],
    "fast charge": ["20w", "18w", "25w", "30w", "33w", "45w", "65w", "pd", "qc", "quick charge", "fast charging",
                    "super fast"],
    "charger": ["adapter", "adaptor", "plug", "wall charger", "travel charger", "home charger"],
    "car charger": ["car", "cigarette", "lighter", "vehicle"],
    "cable": ["wire", "lead", "data cable", "charging cable", "cord"],
    "power bank": ["powerbank", "battery", "portable charger", "mah"],
    "speaker": ["bluetooth speaker", "bt speaker", "sound", "music box"],
    "aux": ["3.5", "3.5mm", "audio jack", "headphone jack"],
    "magnetic": ["magsafe", "magnet", "wireless charging", "qi"],
    "holder": ["mount", "stand", "car holder", "phone holder"],
    "earphone": ["wired earphone", "headphone", "handsfree wired", "earphones"],
}

CATEGORY_WORDS: dict[str, str] = {
    "CABLE": "cable wire lead",
    "CHARGER": "charger adapter plug fast charge",
    "CAR CHARGER": "car charger vehicle cigarette",
    "POWER BANK": "power bank powerbank battery portable",
    "EARPHONE": "earphone wired headphone handsfree",
    "BLUETOOTH HEADSET": "bluetooth headset earbuds tws wireless",
    "BLUETOOTH SPEAKER": "bluetooth speaker sound music",
    "CAR ACCESSORIES": "car holder mount accessory",
}

# search.ts boosts: code 6, name 3, syn 2.5, category 1.5, brand 1.2, spec 1
_BOOST = {"code": 6.0, "name": 3.0, "syn": 2.5, "category": 1.5, "brand": 1.2, "spec": 1.0}

# words a question carries that are not about the product (the chat's fallback path reads raw text)
_STOP = frozenset("""a an the and or of to in on for with my me i we you your do does have has is are any
some this that it its please show need want get can could would will what which how much many price
prices cost there here fit fits work works good best new one ones also more other""".split())

_EXPAND: dict[str, list[str]] = {}
for _canon, _alts in SYNONYMS.items():
    _all = [_canon, *_alts]
    for _a in _all:
        _EXPAND[_a.lower()] = _all

_TOKEN_SPLIT = re.compile(r"[^a-z0-9+.]+")
_STOCK_RANK = {"in_stock": 0, "low_stock": 0, "out_of_stock": 1}


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_SPLIT.split(str(text or "").lower()) if t]


def synonyms_for(text: str) -> str:
    t = str(text or "").lower()
    out: list[str] = []
    for alt, group in _EXPAND.items():
        if alt in t:
            for a in group:
                if a not in out:
                    out.append(a)
    return " ".join(out)


def _doc(it: dict) -> dict[str, list[str]]:
    cat = str(it.get("category") or "")
    words = CATEGORY_WORDS.get(cat, "")
    blob = f"{it.get('item_code') or ''} {it.get('display_name') or ''} {it.get('spec') or ''} {cat} {words}"
    return {
        "code": tokenize(it.get("item_code") or ""),
        "name": tokenize(it.get("display_name") or ""),
        "spec": tokenize(it.get("spec") or ""),
        "category": tokenize(f"{cat} {words}"),
        "brand": tokenize(it.get("brand") or ""),
        "syn": tokenize(synonyms_for(blob)),
    }


def _edit_distance_le(a: str, b: str, limit: int) -> bool:
    """True when the Levenshtein distance of a and b is at most `limit` (banded, early exit)."""
    if abs(len(a) - len(b)) > limit:
        return False
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        best = cur[0]
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            best = min(best, cur[j])
        if best > limit:
            return False
        prev = cur
    return prev[-1] <= limit


def _term_score(q: str, tokens: list[str], fuzzy: float) -> float:
    """1 exact · 0.8 prefix (2+ letters) · 0.5 within the fuzzy edit budget (4+ letters)."""
    best = 0.0
    budget = int(len(q) * fuzzy)
    for t in tokens:
        if t == q:
            return 1.0
        if len(q) >= 2 and t.startswith(q):
            best = max(best, 0.8)
        elif budget >= 1 and len(q) >= 4 and _edit_distance_le(q, t, budget):
            best = max(best, 0.5)
    return best


def _score(doc: dict[str, list[str]], terms: list[str], fuzzy: float, need_all: bool) -> float:
    total = 0.0
    for q in terms:
        s = max((_BOOST[f] * _term_score(q, toks, fuzzy) for f, toks in doc.items()), default=0.0)
        if s <= 0 and need_all:
            return 0.0
        total += s
    return total


def query_terms(query: str) -> list[str]:
    """The words a query is searched with: stopwords dropped (a whole question still finds its
    product), unless that would leave nothing."""
    toks = tokenize(query)
    kept = [t for t in toks if t not in _STOP]
    return kept or toks


def search(items: list[dict], query: str, limit: int = 6) -> list[dict]:
    """The best public items for `query`: AND over the query's words (synonyms live in each item),
    then OR with a looser fuzzy budget when AND finds nothing. Sold-out items rank after
    everything that can be had today, as in the browser."""
    terms = query_terms(query)
    if not terms or not items:
        return []
    docs = [(n, it, _doc(it)) for n, it in enumerate(items)]
    scored = [(s, n, it) for n, it, d in docs if (s := _score(d, terms, 0.2, True)) > 0]
    if not scored:
        scored = [(s, n, it) for n, it, d in docs if (s := _score(d, terms, 0.3, False)) > 0]

    def hot(it: dict) -> int:
        return 0 if any(b in ("best_seller", "trending") for b in (it.get("badges") or [])) else 1

    top = max((s for s, _, _ in scored), default=0.0)

    def key(row):
        s, n, it = row
        # scores within 15% of the best are "the same" (search.ts): velocity, then shelf order decide
        band = 0 if s >= top * 0.85 else 1
        return (_STOCK_RANK.get(str(it.get("stock_status") or ""), 0), band, -s if band else 0, hot(it), n)

    scored.sort(key=key)
    return [it for _, _, it in scored[:limit]]
