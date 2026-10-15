"""The owner's thresholds and targets: key names and types here, VALUES in business_data/ops/config.json.

The repository may be public, so no working number lives in this package. config.json is written
once from the approved plan (git-ignored) and changed when the owner says so. A missing key stops
the command and names the key; tests inject a fixture with round numbers through use().
"""
from __future__ import annotations

import json
from decimal import Decimal

from scripts.ops import paths

# key -> (type, what it is). Types: number, int, str, list, dict.
KEYS: dict[str, tuple[str, str]] = {
    "api.health_url": ("str", "GET address that returns the live API commit"),
    "sales.kpi_steps": ("list", "monthly accessories steps, ex-VAT: [{'from': 'YYYY-MM', 'bhd': n}]; "
                                "empty until the owner names a figure"),
    "load.profit_gap_max": ("number", "profitability hold-back: largest accepted gap between the ledger's "
                                      "cost of sales and the report's, as a share"),
    "load.blank_cost_share_max": ("number", "profitability hold-back: largest accepted share of net sales "
                                            "on items with a blank cost"),
    "load.backup_keep": ("int", "pre-load backup archives kept"),
    "brief.rep_share_min": ("number", "a rep is listed when month to date is below this share of the same "
                                      "business days last month"),
    "brief.silent_business_days": ("int", "a rep with no invoice for more than this many business days is "
                                          "listed (the Command Centre's own rule)"),
    "brief.bulk_invoice_bhd": ("number", "an invoice at or above this ex-VAT value is a bulk order"),
    "stock.lead_time_days": ("int", "days from supplier invoice to receipt"),
    "stock.display_items": ("list", "item codes left out of cover, ageing and dead stock (display, packaging)"),
    "items.bridge_exceptions": ("dict", "ledger item name -> the name the stock balance / day book has for "
                                        "it, where the longest-prefix rule needs help; a target that report "
                                        "does not have leaves the item in the unmapped block"),
    "items.not_accessories": ("list", "item names that are never accessories (handsets and the like)"),
    "warehouses.main": ("str", "name of the main store in Focus"),
    "warehouses.aliases": ("dict", "warehouse name as one report spells it -> the name used everywhere else"),
    "warehouses.not_counted": ("list", "warehouses whose stock never counts (damage, samples, consignment)"),
    "report.category_names": ("dict", "monthly report: the Categories row for an item code the code rule does "
                                      "not know, keyed by the code itself or by the category on its Upcoming or "
                                      "catalog card; empty until the owner names one"),
    "report.prepared_by": ("str", "monthly report: the name on 'Prepared by' (empty: the line reads 'Prepared')"),
}

_TYPES = {"number": (int, float), "int": (int,), "str": (str,), "list": (list,), "dict": (dict,)}
# Keys with a default in code: an empty table or an empty text, never a threshold. A config.json without
# them reads the default, so they are not "missing"; once present they must have their type.
DEFAULTS: dict[str, object] = {"report.category_names": {}, "report.prepared_by": ""}
_cfg: dict | None = None


class ConfigMissing(RuntimeError):
    """A key is absent from config.json, or the file itself is."""


def use(values: dict | None) -> None:
    """Replace the loaded config (tests pass a fixture; None forgets it so the file is read again)."""
    global _cfg
    _cfg = dict(values) if values is not None else None


def load(force: bool = False) -> dict:
    global _cfg
    if _cfg is not None and not force:
        return _cfg
    f = paths.config_file()
    if not f.is_file():
        raise ConfigMissing(f"config missing: {f} does not exist (it is written once from the approved plan)")
    _cfg = json.loads(f.read_text(encoding="utf-8"))
    return _cfg


def get(key: str):
    if key not in KEYS:
        raise KeyError(f"config: '{key}' is not a known key (add it to scripts/ops/config.py KEYS)")
    if key in DEFAULTS:
        try:
            cfg = load()
        except ConfigMissing:
            cfg = {}
        if key not in cfg:
            return json.loads(json.dumps(DEFAULTS[key]))          # a fresh copy of the default
    cfg = load()
    if key not in cfg:
        raise ConfigMissing(f"config missing: '{key}' ({KEYS[key][1]}) is not in {paths.config_file()}")
    val = cfg[key]
    kind = KEYS[key][0]
    if isinstance(val, bool) or not isinstance(val, _TYPES[kind]):
        raise ConfigMissing(f"config: '{key}' must be a {kind} in {paths.config_file()}")
    return val


def number(key: str) -> Decimal:
    return Decimal(str(get(key)))


def kpi_step(month: str) -> Decimal | None:
    """The monthly sales step in force for 'YYYY-MM': the newest entry whose 'from' is not after
    it. None while the owner has not named a figure. An entry that cannot be read, or a figure
    that is no number at all (NaN, infinity), stops the command in one sentence naming the key."""
    best: tuple[str, Decimal] | None = None
    for row in get("sales.kpi_steps"):
        try:
            start = str(row.get("from") or "")[:7]
            step = Decimal(str(row.get("bhd")).replace(",", "").strip())
        except (AttributeError, ArithmeticError, ValueError):
            raise ConfigMissing(f"config: every entry of 'sales.kpi_steps' is {{\"from\": \"YYYY-MM\", \"bhd\": number}} "
                                f"in {paths.config_file()}") from None
        if not step.is_finite():
            raise ConfigMissing(f"config: 'sales.kpi_steps' holds a figure that is not a number in {paths.config_file()}")
        if start and step > 0 and start <= month and (best is None or start >= best[0]):
            best = (start, step)
    return best[1] if best else None


def missing_keys() -> list[str]:
    """Keys of KEYS absent from config.json (selftest prints them); a key with a default never is."""
    try:
        cfg = load()
    except ConfigMissing:
        return sorted(k for k in KEYS if k not in DEFAULTS)
    return sorted(k for k in KEYS if k not in cfg and k not in DEFAULTS)
