"""The owner's thresholds and targets: key names and types here, VALUES in business_data/ops/config.json.

The repository may be public, so no working number lives in this package. config.json is written
once from the approved plan (git-ignored) and changed when the owner says so. A missing key stops
the command and names the key; tests inject a fixture with round numbers through use().
"""
from __future__ import annotations

import json
import math
import re
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
    "watch.floor_bhd": ("number", "the action list: a movement item valued below this in BHD stays off the brief's "
                                  "Do today (absent: the figure the replay worked out)"),
    "watch.act_bhd": ("number", "the action list: a movement item valued at this in BHD or more reads ACT, not CHECK "
                                "(absent: the figure the replay worked out)"),
    "watch.act_days": ("int", "the action list: a movement item open for more than this many working days reads ACT, "
                              "not CHECK (absent: the figure the replay worked out)"),
    "stock.lead_time_days": ("int", "days from supplier invoice to receipt"),
    "stock.display_items": ("list", "item codes left out of cover, ageing and dead stock (display, packaging)"),
    "stock.fast_movers": ("int", "fast movers: how many best sellers by units, over the year of full months "
                                 "before the month"),
    "stock.cover_kpi_days": ("list", "KPI one's band of stock cover in days, [low, high]: an item is ordered when "
                                     "its cover falls below lead time + low, up to lead time + high"),
    "stock.clear_target_days": ("int", "ageing: the days within which the stock left of an old or price-cut item "
                                       "should sell at its pace (the owner's clear target)"),
    "stock.suppliers": ("dict", "item code -> supplier, where neither the catalog's brand nor the last purchase "
                                "order names the right one"),
    "stock.supplier_aliases": ("dict", "a supplier as one source spells it -> the name used everywhere else"),
    "stock.order_every_days": ("int", "the order rhythm: the days between two orders to a supplier (each order covers "
                                      "them as well as the lead time)"),
    "stock.supplier_every_days": ("dict", "supplier -> its own order rhythm in days, where it differs"),
    "stock.supplier_lead_days": ("dict", "supplier -> its lead time in days, where none can be measured (a local "
                                         "supplier, a new one)"),
    "stock.season_lift": ("dict", "month number (January is one) -> the season lift that stands in for last year's "
                                  "own figure"),
    "stock.pack_sizes": ("dict", "item code -> units in a carton: a suggested order is rounded up to whole cartons"),
    "stock.position_stores": ("list", "the order position: the stock beside the main store that counts, of 'vans', "
                                      "'outlets' and 'transit' (an empty list: the main store only)"),
    "stock.van_floor_days": ("int", "the order position: the days of its own sales a van keeps back as its working "
                                    "minimum, not counted beside the main store"),
    "items.bridge_exceptions": ("dict", "ledger item name -> the name the stock balance / day book has for "
                                        "it, where the longest-prefix rule needs help; a target that report "
                                        "does not have leaves the item in the unmapped block"),
    "items.not_accessories": ("list", "item names that are never accessories (handsets and the like)"),
    "warehouses.main": ("str", "name of the main store in Focus"),
    "warehouses.aliases": ("dict", "warehouse name as one report spells it -> the name used everywhere else"),
    "warehouses.not_counted": ("list", "warehouses whose stock never counts (damage, samples, consignment)"),
    "warehouses.damage": ("str", "name of the damage store in Focus, for the damage figures (empty: not set up)"),
    "report.category_names": ("dict", "monthly report: the Categories row for an item code the code rule does "
                                      "not know, keyed by the code itself or by the category on its Upcoming or "
                                      "catalog card; empty until the owner names one"),
    "report.prepared_by": ("str", "monthly report: the name on 'Prepared by' (empty: the line reads 'Prepared')"),
    "report.bulk_invoice_bhd": ("number", "the sales workbooks: an invoice at or above this ex-VAT value is a bulk "
                                          "order (absent: the workbook's own figure)"),
    "kpi.weekly_due_weekday": ("str", "KPI seven: the weekly report is due by this weekday, the first one after the "
                                      "week it covers (a weekday name)"),
    "kpi.monthly_due_working_day": ("int", "KPI seven: the monthly report is due by this working day of the next "
                                           "month (the first working day counts as one)"),
    "kpi.working_days": ("list", "KPI seven: the weekdays that are working days, by name"),
    "kpi.fast_mover_availability_min": ("number", "KPI one: the share of fast-mover days in stock that meets the "
                                                  "target, from nought to one"),
    "kpi.new_skus_band": ("list", "KPI two: the new items a month that meet the target, [low, high]"),
    "kpi.major_purchase_bhd": ("number", "KPI three: a supplier receipt above this value in BHD is a major purchase, "
                                         "which needs quotations"),
    "kpi.shop_min_bhd": ("number", "the KPI sheet: a shop counts as buying in a month when it buys at least this "
                                   "value in BHD"),
    "kpi.supplier_register": ("str", "KPI three: the supplier register, a file outside the project folder, read and "
                                     "never written (empty: not set up)"),
    "kpi.plan_targets": ("dict", "the plan rows of the KPI sheet by month: 'YYYY-MM' -> that month's targets "
                                 "(empty until the owner names them)"),
}

_TYPES = {"number": (int, float), "int": (int,), "str": (str,), "list": (list,), "dict": (dict,)}
# Keys config.json may lack (added after it was first written): one list, OPTIONAL. Absent, they are not
# "missing" (missing_keys() never names one, so the selftest stays clean); once present they must have their
# type (and pass their check, CHECKS below, where they have one). Two ways to stand in for one:
#  * DEFAULTS: a default in code, an empty table or an empty text, never a threshold; get() returns it.
#  * every other OPTIONAL key: get() still stops with ConfigMissing, so no other command gets a silent value;
#    the command that reads it carries the approved plan's default and says so on its screen (names only
#    here, never a value).
DEFAULTS: dict[str, object] = {"report.category_names": {}, "report.prepared_by": "", "warehouses.damage": "",
                               "stock.suppliers": {}, "stock.supplier_aliases": {}, "stock.supplier_every_days": {},
                               "stock.supplier_lead_days": {}, "stock.season_lift": {}, "stock.pack_sizes": {},
                               "kpi.plan_targets": {}, "kpi.supplier_register": ""}
OPTIONAL = frozenset({"stock.fast_movers", "stock.cover_kpi_days", "stock.clear_target_days", "stock.order_every_days",
                      # the movement audit and its action list, the order position, the workbooks, the KPI sheet
                      "watch.floor_bhd", "watch.act_bhd", "watch.act_days", "stock.position_stores",
                      "stock.van_floor_days", "report.bulk_invoice_bhd", "kpi.weekly_due_weekday",
                      "kpi.monthly_due_working_day", "kpi.working_days", "kpi.fast_mover_availability_min",
                      "kpi.new_skus_band", "kpi.major_purchase_bhd", "kpi.shop_min_bhd",
                      *DEFAULTS})

# What a key must hold beyond its type, where its type is not enough: key -> (test, the words of the refusal).
# The test is handed a value of the right type; one it refuses stops the command in one sentence that names
# the key, as a wrong type does. JSON reads NaN and Infinity as numbers, so an amount must be finite too.
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")   # as date.weekday() counts
POSITION_STORES = ("vans", "outlets", "transit")         # what 'stock.position_stores' may name beside the main store
_MONTH_KEY = re.compile(r"[0-9]{4}-(?:0[1-9]|1[0-2])")


def _day_name(x) -> str | None:
    """A weekday as config writes it, folded ('Sunday', ' sunday ' -> 'sunday'); None for anything else."""
    folded = " ".join(x.split()).casefold() if isinstance(x, str) else None
    return folded if folded in WEEKDAYS else None


def _amount(v) -> bool:
    return (not isinstance(v, float) or math.isfinite(v)) and v >= 0


def _whole(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


CHECKS: dict[str, tuple] = {
    "watch.floor_bhd": (_amount, "an amount in BHD, nought or more"),
    "watch.act_bhd": (_amount, "an amount in BHD, nought or more"),
    "watch.act_days": (lambda v: v >= 0, "a whole number of working days, nought or more"),
    "stock.position_stores": (lambda v: all(isinstance(x, str) and x in POSITION_STORES for x in v)
                              and len(set(v)) == len(v),
                              "a list of 'vans', 'outlets' and 'transit', each at most once (an empty list: the main "
                              "store only)"),
    "stock.van_floor_days": (lambda v: v >= 0, "a whole number of days, nought or more"),
    "report.bulk_invoice_bhd": (_amount, "an amount in BHD, nought or more"),
    "kpi.weekly_due_weekday": (lambda v: _day_name(v) is not None, "a weekday name (Sunday, for example)"),
    "kpi.monthly_due_working_day": (lambda v: v >= 1, "a working day of the month, one or more"),
    "kpi.working_days": (lambda v: bool(v) and all(_day_name(x) for x in v) and len({_day_name(x) for x in v}) == len(v),
                         "a list of weekday names, at least one, each at most once"),
    "kpi.fast_mover_availability_min": (lambda v: 0 <= v <= 1, "a share from nought to one"),
    "kpi.new_skus_band": (lambda v: len(v) == 2 and all(_whole(x) and x >= 0 for x in v) and v[0] <= v[1],
                          "[low, high], two whole numbers, nought or more, low not above high"),
    "kpi.major_purchase_bhd": (_amount, "an amount in BHD, nought or more"),
    "kpi.shop_min_bhd": (_amount, "an amount in BHD, nought or more"),
    "kpi.plan_targets": (lambda v: all(isinstance(k, str) and _MONTH_KEY.fullmatch(k) for k in v),
                         "an object whose keys are months written YYYY-MM"),
}
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
    check = CHECKS.get(key)
    if check is not None and not check[0](val):
        raise ConfigMissing(f"config: '{key}' must be {check[1]} in {paths.config_file()}")
    return val


def number(key: str) -> Decimal:
    return Decimal(str(get(key)))


def weekday(name) -> int:
    """A weekday as config names it ('Sunday'; case and spaces aside) -> its number as date.weekday()
    counts it (Monday is nought). ValueError for anything else."""
    folded = _day_name(name)
    if folded is None:
        raise ValueError(f"config: {name!r} is not a weekday name")
    return WEEKDAYS.index(folded)


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
    """Keys of KEYS absent from config.json, the OPTIONAL ones aside (selftest prints them); with no
    config.json at all, every key but those."""
    try:
        cfg = load()
    except ConfigMissing:
        cfg = {}
    return sorted(k for k in KEYS if k not in cfg and k not in OPTIONAL)
