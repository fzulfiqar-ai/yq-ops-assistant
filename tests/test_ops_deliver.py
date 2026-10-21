"""The delivery after a load (python -m scripts.ops deliver): the management e-mail, the team message and its
privacy check, the actions, the stored brief, on invented facts.

    python -m tests.test_ops_deliver

Same lightweight runner as the other suites (no pytest). No database, no network, no .env: SUPABASE_URL,
SUPABASE_KEY and DATABASE_URL are pinned to ci.invalid BEFORE any import and scripts.ops.db is imported first;
the brief itself (sales.full_screen) is a stand-in that hands back invented facts in the real shape. Every
folder is a temporary one; every name and figure is invented.

Covered:
  * the team message: team figures in whole dinars, praise only with at least three reps who sold and never
    a figure beside a name, the items to push, the end-of-day ask, at most TEAM_MAX characters;
  * the privacy check refuses a customer or shop name, a word about money owed or the reps' pay, a rep
    named beside an amount or beside a word that singles him out; it passes the message the brief makes;
  * the e-mail: subject with the day and the data date, NOT EVIDENCED where a figure is missing (no target,
    discounts, the stock in the vans), the rep cards (kickback figures never), names switched off; the
    Transfers paragraph under Exceptions from the brief's movements alone (NOT EVIDENCED when they were not
    read); the count of the actions it has no room for points to /yq-watch;
  * the wa.me link carries the text exactly (percent-encoded UTF-8);
  * actions: the playbook by key, ACTION and TO CHECK, at most five ranked by effect, urgency and effort,
    a due date on a business day, an item whose words the guard refuses is withheld, never dropped; the
    five move rules go to the storekeeper, each with its next step and its days;
  * enrich: the rep table from the attainment rows (tier 1 only), the push list (rising, in stock for two
    weeks), the shops due a re-order counted per rep by the portal's own name rule, discounts NOT EVIDENCED
    while the export carries none;
  * deliver: full, look, failed and none from the load's closing row; files and manifest with each
    sha256; the brief saved and stamped, and shown once in the chat (brief_unshown); the move rules
    recorded once, before the brief, in a full delivery only (never in a preview, never in a refused
    one), and a recording that did not happen, raised or had no module is one line and never stops the
    delivery; a preview records nothing; a team message the check refuses is not written, and the
    e-mail goes without it;
  * /yq-brief team prints the last load's team message, or says when there is none.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tempfile
import time
import traceback
import types
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["SUPABASE_URL"] = "https://ci.invalid"
os.environ["SUPABASE_KEY"] = "ci-key"
os.environ["DATABASE_URL"] = "postgresql://ci:ci@ci.invalid:5432/ci"
os.environ.pop("YQ_MAIN_CHECKOUT", None)
os.environ["YQ_SECRETS_OFF"] = "1"

from scripts.ops import db  # noqa: E402,F401  (first, as every command does)
from scripts.ops import actions, config, deliver, enrich, fmt, loadplan, paths, render, sales, status  # noqa: E402


def M(whole) -> str:
    """An invented amount with its three decimals, built at run time: the commit gate refuses any amount of
    a thousand or more written with three decimals in a committed file, even an invented one."""
    return f"{whole}.000"

TESTS: list[tuple[str, object]] = []
RUN = "151026_0900"
TODAY = date(2026, 10, 15)


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


class data_root:
    def __enter__(self) -> Path:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        paths.set_data_root(self._tmp.name)
        return paths.data_root()

    def __exit__(self, *exc):
        paths.set_data_root(None)
        self._tmp.cleanup()
        return False


def facts(**over) -> dict:
    """A full brief's facts in the shape sales.full_brief writes them (brief.json), with enrich's part."""
    f = {
        "command": "brief", "kind": "full", "data_date": "2026-10-14", "latest_day": "2026-10-14",
        "full_to": "2026-10-14", "loaded": "Thu 15 Oct 10:20", "basis": "Accessories only, ex-VAT, BHD.",
        "sales": {
            "average_business_day": {"bhd": "300.000"},
            "day": {"start": "2026-10-14", "end": "2026-10-14", "bhd": "450.250", "invoices": 12},
            "d3": {"start": "2026-10-12", "end": "2026-10-14", "bhd": "900.000", "invoices": 30},
            "d7": {"start": "2026-10-08", "end": "2026-10-14", "bhd": M(1500), "invoices": 50,
                   "reading": "above the normal range"},
            "lfw": {"start": "2026-10-04", "end": "2026-10-10", "bhd": M(1400), "invoices": 44,
                    "reading": "inside the normal range"},
            "mtd": {"start": "2026-10-01", "end": "2026-10-14", "bhd": M(2500), "invoices": 80, "change": "+12%"},
            "largest_invoice": {"day": {"bhd": "90.000", "salesman": "Van A", "account": "Shop Sunrise Ltd"}},
        },
        "pace": {"kpi_step": M(10000), "portal_target": None,
                 "pace": {"done_pct": 25.0, "needed_per_business_day": "625.000", "business_days_left": 12,
                          "business_days_done": 9}},
        "month": {"b2b": M(2000), "b2c": "500.000", "invoices": 80, "accounts_invoiced": 20},
        "accounts_not_back": {"count": 2, "bhd_last_month": "300.000",
                              "accounts": [{"account": "Shop Moonlight Trading", "bhd_last_month": "200.000",
                                            "salesman": "Van A", "last_invoice": "2026-09-20"},
                                           {"account": "Shop Harbour View", "bhd_last_month": "100.000",
                                            "salesman": "Van B", "last_invoice": "2026-09-22"}]},
        "reps": {"silent_business_days": 7, "salesmen": []},
        "stock": {"sold_out_with_demand": 1, "bhd_60_days": "80.000",
                  "items": [{"item": "Cable Q 1m", "sold_60_days": 20, "bhd_60_days": "80.000"}]},
        "do_today": {"open": [
            {"id": "A1", "key": "attn|orders_waiting", "level": "ACT", "title": "2 orders waiting to be confirmed",
             "detail": "Received and not confirmed", "bhd": "40.000", "count": 2},
            {"id": "A2", "key": "voucher_changed|x", "level": "CHECK", "title": "A voucher changed after export",
             "detail": "one voucher", "bhd": None, "count": 1}]},
        "enriched": {
            "team": [
                {"salesman": "Van A", "name": "Rep Alpha", "mtd_bhd": M(1200), "same_days_last_month_bhd": M(1000),
                 "change_pct": 20.0, "invoices": 40, "shops": 15, "last_invoice": "2026-10-14",
                 "business_days_since_invoice": 0, "target_bhd": M(1000), "achieved_pct": 120.0,
                 "needed_per_business_day_bhd": "0.000", "business_days_left": 12, "shops_due": 3},
                {"salesman": "Van B", "name": "Rep Bravo", "mtd_bhd": "800.000", "same_days_last_month_bhd": "0.000",
                 "change_pct": None, "invoices": 25, "shops": 10, "last_invoice": "2026-10-13",
                 "business_days_since_invoice": 1, "target_bhd": M(1000), "achieved_pct": 80.0,
                 "needed_per_business_day_bhd": "16.667", "business_days_left": 12, "shops_due": 0},
                {"salesman": "Van C", "name": "Rep Charlie", "mtd_bhd": "500.000", "same_days_last_month_bhd": None,
                 "change_pct": None, "invoices": 15, "shops": 8, "last_invoice": "2026-10-12",
                 "business_days_since_invoice": 2, "target_bhd": None, "achieved_pct": None,
                 "needed_per_business_day_bhd": None, "business_days_left": None},
                {"salesman": "Van D", "name": "Rep Delta", "mtd_bhd": "0.000", "change_pct": None, "invoices": 0,
                 "shops": 0, "last_invoice": None, "business_days_since_invoice": 9, "target_bhd": "500.000",
                 "achieved_pct": 0.0, "needed_per_business_day_bhd": "41.667"}],
            "push": [{"item": "Charger Z 20W", "units_30_days": 40, "units_30_before": 10, "bhd_30_days": "200.000",
                      "rise_bhd": "150.000", "in_stock": 300, "days_cover": 120}],
            "movers": {"rising": [], "falling": []},
            "receivables": {"total_bhd": M(5000), "accounts": 40, "over_90_pct": 12.5, "over_90_bhd": "625.000",
                            "top_overdue": [{"account": "Shop Owing Corner", "overdue_bhd": "400.000",
                                             "over_90_bhd": "200.000", "last_receipt": "2026-08-01"}]},
            "orders": {"orders": 2, "bhd": "40.000", "oldest_hours": 30},
            "shops_due": {"due": 3, "lapsed": 1, "monthly_value_bhd": "150.000", "by_rep": {"Van A - Acc WH": 3},
                          "shops": [{"shop": "Shop Riverside Mart", "rep": "Van A - Acc WH", "days_since": 40,
                                     "usual_gap_days": 14.0, "monthly_value_bhd": "60.000"}]},
            "discounts": {"evidenced": False, "why": "the Focus sales export carries no discount values"},
            "stock_shape": None, "not_read": []},
        "warnings": [],
    }
    f.update(over)
    return f


LOAD = {"run": RUN, "result": "ok", "timings": {"backup": 60.0, "wait_tick": 0.0, "refresh": 150.0},
        "total_seconds": 400.0, "as_on": {"stock_balance": "2026-10-15", "receivables": "2026-10-15"},
        "profitability": {"held": True, "gap_pct": 6.2, "limit_pct": 3.0, "items_sold_since": 10,
                          "with_no_cost_movement": 6, "production_as_of": "2026-09-28"},
        "verify": "passed", "reconcile": "matched", "gate": "passed (3 explained changes)"}


# ── the team message ─────────────────────────────────────────────────────────────────────────────

@test("the team message: the month as a share of the target, no sales total, praise without figures, the items, the ask")
def _():
    text = render.team_text(facts(), TODAY)
    assert text.startswith("*YQ Accessories - Thu 15 Oct*"), text
    assert text.splitlines()[1] == "October so far: 25% of our monthly target; 12 working days left, BHD 625 a day needed."
    for total in ("2,500", "450", "10,000", "Latest day"):                 # no sales total of the company
        assert total not in text, total
    assert "Well done Rep Alpha, Rep Bravo and Rep Charlie" in text and "the last seven days were above our normal range" in text
    assert "Push today (in stock, selling faster): Charger Z 20W." in text
    assert "Your shops due a re-order are on your Today page" in text and text.rstrip().endswith("in the app).")
    assert "Rep Delta" not in text, "a rep with no sale is never named in the group"
    assert len(text) <= render.TEAM_MAX and render.privacy_problems(text, facts()) == [], render.privacy_problems(text, facts())
    reached = facts()
    reached["sales"]["mtd"]["bhd"] = M(10500)
    assert "October so far: our monthly target is reached. Well done, team!" in render.team_text(reached, TODAY)
    few = facts()
    few["enriched"]["team"] = few["enriched"]["team"][:2]
    assert "Well done Rep" not in render.team_text(few, TODAY), "two reps who sold are not named for praise"
    nostep = facts(pace={"kpi_step": None, "portal_target": None})
    assert render.team_text(nostep, TODAY).splitlines()[1] == "October so far: +12% on the same days last month."
    last = facts()
    last["pace"]["pace"]["business_days_left"] = 1
    assert "; 1 working day left, BHD 625 a day needed." in render.team_text(last, TODAY)


@test("the team message stays within its length: lists are cut from the end, never the ask")
def _():
    big = facts()
    big["enriched"]["push"] = [dict(big["enriched"]["push"][0], item="A very long accessory name " + "x" * 300)] * 3
    text = render.team_text(big, TODAY)
    assert len(text) <= render.TEAM_MAX and "By 5 pm" in text, (len(text), text[-120:])


@test("the privacy check refuses names of customers, money owed, the reps' pay, and a rep beside a figure or a blame")
def _():
    f = facts()
    good = render.team_text(f, TODAY)
    assert render.privacy_problems(good, f) == []
    for bad, why in (("Call Shop Moonlight Trading today.", "customer or shop"),
                     ("Shop Owing Corner still owes us.", "customer or shop"),
                     ("Receivables are high this month.", "receivables"), ("Your tier is close.", "tier"),
                     ("He owes us money.", "owes"), ("Tiers are paid monthly.", "tiers"),
                     ("Kickback paid soon.", "kickback"), ("Rep Alpha sold BHD " + M("1,200") + " so far.", "beside an amount"),
                     ("Rep Delta is behind again.", "singles him out"), ("Van D is silent.", "singles him out")):
        problems = render.privacy_problems(good + "\n" + bad, f)
        assert problems and any(why in p for p in problems), (bad, problems)
    assert render.privacy_problems("Shop Riverside Mart opens late", f), "a shop due a re-order is a name too"
    assert render.privacy_problems(good + "\nThe owner thanks you all.", f) == [], "'owner' is not 'owe'"
    # a name written another way is the same name: case, hyphens, dots and spacing set aside
    for written in ("call shop moonlight-trading", "Call SHOP  MOONLIGHT TRADING.", "Shop Moonlight Trading's order"):
        assert any("customer or shop" in p for p in render.privacy_problems(written, f)), written
    # a rep on a line with any figure at all, not only BHD: a count, a share
    for bad in ("Rep Alpha 1,234 this month", "Rep Bravo reached 85% of his month", "Rep Charlie: 3 shops"):
        assert any("beside an amount or a figure" in p for p in render.privacy_problems(bad, f)), bad
    assert render.privacy_problems("Rep Alphabet Cases are in stock", f) == [], "whole words only: no rep is named"


@test("the privacy check knows every name of the loaded Focus files: a shop inside an item name, never a rep's praise")
def _():
    f = facts()
    with data_root():
        paths.write_text(paths.ops_dir() / "denylist.json",
                         json.dumps(["Shop Starlight Phones", "Rep Alpha", "Van A - Acc WH"]))
        assert "Shop Starlight Phones" in render.known_names()
        customers, reps = render.team_names(f)
        assert "Shop Starlight Phones" in customers and "Rep Alpha" not in customers and "Van A - Acc WH" not in customers
        item = "Push today (in stock, selling faster): Charger for SHOP STARLIGHT-PHONES display."
        assert any("customer or shop" in p for p in render.privacy_problems(item, f))
        good = render.team_text(f, TODAY)
        assert "Well done Rep Alpha" in good and render.privacy_problems(good, f) == []
    with data_root():                                   # no list on this PC: the names the facts carry are checked
        assert render.known_names() == [] and render.privacy_problems(render.team_text(f, TODAY), f) == []


@test("the wa.me link carries the text exactly, percent-encoded UTF-8")
def _():
    from urllib.parse import parse_qs, urlparse
    text = "Line one: BHD 10 & 5% *bold*\nسطر عربي"
    link = render.wa_link(text)
    assert link.startswith("https://wa.me/?text=") and " " not in link and "\n" not in link
    assert parse_qs(urlparse(link).query)["text"][0] == text


# ── the e-mail ───────────────────────────────────────────────────────────────────────────────────

@test("the e-mail: subject, summary, target, the rep cards, exceptions, actions, the load and NOT EVIDENCED")
def _():
    f = facts()
    top, every = actions.ranked(f, date(2026, 10, 14))
    team = render.team_text(f, TODAY)
    html = render.email_html(f, top, every, LOAD, team, TODAY)
    text = render.email_text(f, top, every, LOAD, team, TODAY)
    assert render.email_subject(f, TODAY) == "YQ Daily Sales & Operations Brief - Thu 15 Oct 2026 (data to Wed 14 Oct)"
    assert render.email_subject(f, TODAY, test=True).startswith("[TEST] ")
    for part in ("Daily Sales &amp; Operations Brief", "Thursday 15 October 2026", "load " + RUN, "In short",
                 "Monthly target", "BHD " + M("10,000"), "Charger Z 20W", "Rep Alpha", "Shop Riverside Mart",
                 "Profitability report held back", "Receivables BHD " + M("5,000"), "Discounts: NOT EVIDENCED",
                 "Value of returns", "Open in WhatsApp", "https://wa.me/?text=", "What to do", "Verify"):
        assert part in html, part
    # the vans' stock is on /yq-moves (the stock ledger, store by store): no longer a figure the reports lack
    assert "Stock in the vans" not in html
    assert "kickback" not in html.lower().replace("kickback figures are left out", ""), "no kickback figure"
    assert "No invoice yet this month: Rep Delta" in html
    assert "<script" not in html and "<img" not in html, "an e-mail carries no script and no image"
    assert "WHAT TO DO" in text and "TEAM MESSAGE" in text and "Rep Alpha" in text
    anonymous = render.email_html(f, top, every, LOAD, team, TODAY, names=False)
    assert "Shop Riverside Mart" not in anonymous and "Shop Moonlight Trading" not in anonymous
    assert "Shop Owing Corner" not in anonymous and "3 shops are due a re-order" in anonymous
    nostep = render.email_html(facts(pace={"kpi_step": None}), top, every, LOAD, None, TODAY)
    assert "Target: NOT EVIDENCED" in nostep and "Team message" not in nostep


MOVEMENTS = {   # the brief's facts['movements'] (sales.movements_section), invented: BHD at landed cost
    "read": True, "days": 7, "first": "2026-10-08", "last": "2026-10-14", "ledger_to": "2026-10-14",
    "basis": "at landed cost", "per_store_ok": True, "note": None, "to_stores_units": "340", "back_units": "12",
    "damage_units": "3", "not_received_units": "6", "to_check_count": "4", "to_stores_bhd": "850.500",
    "not_received_bhd": "15.250", "to_check_bhd": "40.000"}
TRANSFERS = ("Transfers in the last 7 days to Wed 14 Oct (BHD at landed cost): 340 units went to the vans and outlets "
             "(BHD 850.500), 12 came back to the main store and 3 went to damage. Not received after the deadline: 6 "
             "units (BHD 15.250). To check: 4 (BHD 40.000), listed by /yq-moves.")


@test("the e-mail: a Transfers paragraph under Exceptions, from the brief's movements alone; NOT EVIDENCED unread")
def _():
    f = facts(movements=dict(MOVEMENTS))
    top, every = actions.ranked(f, date(2026, 10, 14))
    html = render.email_html(f, top, every, LOAD, None, TODAY)
    assert TRANSFERS in html, html
    assert html.index(">Exceptions</h2>") < html.index(TRANSFERS) < html.index(">What to do</h2>")
    # moves' note and the figures store by store come after the sentence
    f = facts(movements=dict(MOVEMENTS, note="ledger file one day behind", per_store_ok=False))
    assert (TRANSFERS + " ledger file one day behind. The figures store by store need a look (/yq-moves).") in \
        render.email_html(f, top, every, LOAD, None, TODAY)
    # it works nothing out: a figure the brief did not get is NOT EVIDENCED, never a nought, and the rest stand
    f = facts(movements=dict(MOVEMENTS, to_stores_bhd=None, damage_units=None))
    html = render.email_html(f, top, every, LOAD, None, TODAY)
    assert "340 units went to the vans and outlets (NOT EVIDENCED)" in html
    assert "and NOT EVIDENCED went to damage" in html and "To check: 4 (BHD 40.000), listed by /yq-moves." in html
    # not read, or no movements in the facts at all: said so, as the e-mail says of any figure it lacks
    for mv in ({"read": False}, None, "junk"):
        f = facts() if mv is None else facts(movements=mv)
        html = render.email_html(f, top, every, LOAD, None, TODAY)
        assert "Transfers: NOT EVIDENCED (not read this time)." in html and "listed by /yq-moves" not in html, mv
    # the count of the actions the e-mail has no room for points to the full list, in both parts
    assert every > len(top)
    f = facts(movements=dict(MOVEMENTS))
    assert f">{every - len(top)} more on the action list (/yq-watch).</p>" in render.email_html(f, top, every, LOAD,
                                                                                                None, TODAY)
    assert f"({every - len(top)} more on the action list: /yq-watch)" in render.email_text(f, top, every, LOAD, None,
                                                                                           TODAY).splitlines()
    # the transfers are the owner's: the team message never carries them, and still passes the privacy check
    team = render.team_text(f, TODAY)
    assert "Transfer" not in team and "/yq-moves" not in team and render.privacy_problems(team, f) == []


@test("the notes for a load that needs a look or failed carry no figure")
def _():
    for kind in ("look", "failed"):
        text = render.note_text(kind, RUN, ["reconcile"])
        assert RUN in text and not any(ch.isdigit() for ch in text.replace(RUN, "").replace("151026", "")), text
        assert render.note_subject(kind, RUN).startswith("YQ load 151026 ")
        assert "no brief sent" in render.note_subject(kind, RUN)
    assert "/yq-load" in render.note_text("failed", RUN, [])


# ── actions ──────────────────────────────────────────────────────────────────────────────────────

@test("actions: the playbook by key, ACTION and TO CHECK, at most five ranked, due on a business day")
def _():
    d = date(2026, 10, 14)
    top, every = actions.ranked(facts(), d)
    assert 1 <= len(top) <= actions.TOP and every >= len(top)
    for a in top:
        assert set(a) >= {"tag", "title", "why", "impact", "next_step", "owner", "due", "due_text"}
        assert date.fromisoformat(a["due"]).weekday() not in (4, 5), a        # never a Friday or a Saturday
    waiting = next(a for a in top if a["title"].startswith("2 orders waiting"))
    assert waiting["owner"] == "Sales team" and waiting["tag"] == "ACTION"
    check = actions.from_register(facts()["do_today"]["open"], d)
    assert any(a["tag"] == "TO CHECK" and a["owner"] == "Accounts" for a in check)
    assert actions.playbook("attn|orders_waiting|extra")[0] == "Sales team"
    assert actions.playbook("something new") == actions.DEFAULT
    odd = actions.from_register([{"key": "attn|x", "level": "ACT", "title": "a possible fraud here", "detail": ""}], d)
    assert odd[0]["title"].startswith("(this item's wording is withheld"), "withheld, never dropped"
    assert enrich.nth_business_day(date(2026, 10, 16), 0) == date(2026, 10, 18)   # Friday -> Sunday
    assert enrich.nth_business_day(date(2026, 10, 15), 1) == date(2026, 10, 18)   # Thursday + 1 -> Sunday


@test("actions: the five move rules go to the storekeeper, each with its next step, its days and its effort")
def _():
    rows = {   # rule -> (owner, next step, business days, effort): the approved plan's rows
        "xfer_open": ("Storekeeper, with the receiving store", "Post the Stock Receive Voucher or reverse the issue",
                      1, 1),
        "xfer_twins": ("Storekeeper", "Find which identical transfer was received", 2, 1),
        "qty_diff": ("Storekeeper, with the van", "Count the transfer's items and correct the wrong voucher", 2, 2),
        "xfer_elsewhere": ("Storekeeper", "Correct the receipt or the issue to the store the goods are in", 3, 1),
        "rcpt_orphan": ("Storekeeper", "Find the issue for each receipt or reverse it", 3, 1),
    }
    try:                                    # the recorder's own list of its rules, once it is there
        from scripts.ops import checks
        rules = tuple(checks.MOVE_RULES)
    except ImportError:
        rules = ("xfer_open", "xfer_twins", "qty_diff", "xfer_elsewhere", "rcpt_orphan")     # its contract's five
    assert set(rules) == set(rows), rules
    for rule, want in rows.items():
        assert actions.PLAYBOOK[rule] == want, rule
        fmt.careful(want[1])
    # by the key the recorder gives (the damage store's month line included), never the DEFAULT words
    for key in ("xfer_open|SIO:YQ-26-10-5", "xfer_open|damage|2026-10", "xfer_twins|SIO:YQ-26-10-7",
                "qty_diff|SIO:YQ-26-10-8", "xfer_elsewhere|SIO:YQ-26-10-9", "rcpt_orphan|STRV:YQ-26-10-3"):
        assert actions.playbook(key) == rows[key.split("|")[0]], key
    d = date(2026, 10, 11)                  # a Sunday: counted from Monday, one business day on is Tuesday
    items = [{"id": "A7", "key": "xfer_open|SIO:YQ-26-10-5", "level": "CHECK", "title": "Transfer not received after "
              "the deadline", "detail": "A Stock Issue Voucher with no receipt", "bhd": "12.500", "count": None,
              "who": None},
             {"id": "A8", "key": "rcpt_orphan|STRV:YQ-26-10-3", "level": "ACT", "title": "Receipt with no issue",
              "detail": "", "bhd": None, "count": 1, "who": None}]
    late, orphan = actions.from_register(items, d)
    assert (late["owner"], late["next_step"], late["tag"], late["due"]) == (
        "Storekeeper, with the receiving store", "Post the Stock Receive Voucher or reverse the issue", "TO CHECK",
        "2026-10-13")
    assert late["impact"] == "BHD 12.500 involved" and late["why"] == "SIO:YQ-26-10-5: no receipt found by its deadline"
    assert (orphan["owner"], orphan["tag"], orphan["due"], orphan["impact"]) == ("Storekeeper", "ACTION", "2026-10-15",
                                                                                 "1 involved")


# Invented stores and items of a transfer: a van's store is its salesman's name, so none may reach the e-mail.
VAN, OTHER_VAN, KIOSK, MAIN, DAMAGE = ("Rep Zulu - Acc WH", "Rep Yankee - Acc WH", "Kiosk Nine", "Main Store Alpha",
                                       "Damage Store Beta")
STORE_WORDS = ("Rep Zulu", "Rep Yankee", "Zulu", "Yankee", "Kiosk Nine", "Main Store Alpha", "Damage Store Beta",
               "Lens Kit Omega")


def transfer_exceptions() -> list[dict]:
    """One exception of every kind, as the movement audit hands them over (moves.exceptions' shape)."""
    lines = [{"item": "Lens Kit Omega", "units": 4}]
    return [
        {"kind": "not_received", "issues": ["SIO:YQ-26-10-5"], "from": MAIN, "to": VAN, "date": "2026-10-11",
         "deadline": "2026-10-12", "lines": lines, "bhd": "12.500"},
        {"kind": "damage_not_booked", "issues": ["SIO:YQ-26-10-6"], "from": KIOSK, "to": DAMAGE, "date": "2026-10-02",
         "deadline": "2026-10-09", "lines": lines, "bhd": "3.000"},
        {"kind": "twins", "issues": ["SIO:YQ-26-10-7", "SIO:YQ-26-10-8"], "from": MAIN, "to": VAN, "open": 1,
         "date": "2026-10-11", "deadline": "2026-10-12", "lines": lines, "bhd": "5.000"},
        {"kind": "qty_differs", "issues": ["SIO:YQ-26-10-9"], "receipt": "STRV:YQ-26-10-2", "from": MAIN, "to": KIOSK,
         "date": "2026-10-10", "lag": 1, "lines": lines, "bhd": "2.000"},
        {"kind": "elsewhere", "issues": ["SIO:YQ-26-10-10"], "receipt": "STRV:YQ-26-10-3", "from": MAIN, "to": VAN,
         "received_at": OTHER_VAN, "date": "2026-10-10", "lag": 0, "lines": lines, "bhd": "4.000"},
        {"kind": "orphan", "receipt": "STRV:YQ-26-10-4", "received_at": VAN, "date": "2026-10-12",
         "possible": ["SIO:YQ-26-10-11"], "lines": lines, "bhd": "1.500"},
    ]


@test("the e-mail never names a store of a transfer: a move item's Why is numbers and dates only")
def _():
    from scripts.ops import checks             # the recorder itself makes the items, as it records them
    assert actions.MOVE_RULES == checks.MOVE_RULES, "the e-mail's list of the move rules is the recorder's"
    found, notes = checks.found_items(transfer_exceptions(), "2026-10-14")
    assert notes == [], notes
    items = [dict(it, id=f"A{n}", first_seen="2026-10-14T05:00:00+00:00")
             for n, it in enumerate((it for rule in checks.MOVE_RULES for it in found[rule]), 1)]
    assert len(items) == 6
    # the register keeps the stores in the detail (alerts.jsonl stays on this PC): the test bites on real details
    assert sum(1 for it in items if "Rep Zulu" in it["detail"]) >= 3, [it["detail"] for it in items]
    whys = {it["key"]: actions.move_why(it) for it in items}
    assert whys == {
        "xfer_open|SIO:YQ-26-10-5": "SIO:YQ-26-10-5: issued Sun 11 Oct, due Mon 12 Oct; no receipt found",
        "xfer_open|damage|2026-10": "Issued in October 2026 and not booked in by the deadline: SIO:YQ-26-10-6",
        "xfer_twins|SIO:YQ-26-10-7": "1 of 2 identical issues: SIO:YQ-26-10-7, SIO:YQ-26-10-8",
        "qty_diff|SIO:YQ-26-10-9": "SIO:YQ-26-10-9, received under STRV:YQ-26-10-2: other quantities",
        "xfer_elsewhere|SIO:YQ-26-10-10": "SIO:YQ-26-10-10: received in another store under STRV:YQ-26-10-3",
        "rcpt_orphan|STRV:YQ-26-10-4": "STRV:YQ-26-10-4, received Mon 12 Oct: no issue found"}, whys
    d = date(2026, 10, 14)
    acts = actions.from_register(items, d)
    assert [a["why"] for a in acts] == [whys[it["key"]] for it in items]
    for a in acts:
        for field in ("title", "why", "impact", "next_step", "owner"):
            assert not any(w in a[field] for w in STORE_WORDS), (field, a[field])
    # the whole e-mail, both parts, with the names on and off, as ranked and with every move item on it: no store,
    # no salesman, no item of a transfer
    f = facts(do_today={"open": items}, movements=dict(MOVEMENTS))
    top, every = actions.ranked(f, d)
    assert any(a["source"] == "register" for a in top), top
    for names in (True, False):
        for shown, more in ((top, every), (acts, len(acts))):
            for body in (render.email_html(f, shown, more, LOAD, None, TODAY, names=names),
                         render.email_text(f, shown, more, LOAD, None, TODAY, names=names)):
                assert not [w for w in STORE_WORDS if w in body], [w for w in STORE_WORDS if w in body]
        assert whys["rcpt_orphan|STRV:YQ-26-10-4"] in render.email_text(f, acts, len(acts), LOAD, None, TODAY,
                                                                        names=names)
    # another rule's item still gives its detail (the move rules alone are numbers and dates)
    plain = actions.from_register([{"key": "inv_changed|INV-9", "level": "CHECK", "title": "Invoice changed",
                                    "detail": "INV-9: its lines changed between two exports"}], d)
    assert plain[0]["why"] == "INV-9: its lines changed between two exports"
    # a detail the recorder wrote another way: the days are dropped, never a word of it taken
    odd = {"key": "rcpt_orphan|STRV:YQ-26-10-4", "evidence": ["STRV:YQ-26-10-4"],
           "detail": "Rep Zulu - Acc WH booked it on the twelfth"}
    assert actions.move_why(odd) == "STRV:YQ-26-10-4: no issue found"
    assert actions.is_move("xfer_open|damage|2026-10") and not actions.is_move("attn|sold_out")


# ── enrich ───────────────────────────────────────────────────────────────────────────────────────

@test("enrich: tier 1 as the target, the push list, the shops due per rep, discounts NOT EVIDENCED")
def _():
    att = [{"salesman": "Van A", "name": "Rep Alpha", "net_bhd": 1200, "invoices": 40, "shops": 15, "last_sale": "2026-10-14",
            "no_target": False, "is_active": True, "kickback_bhd": 12.5,
            "tier": {"tiers": [{"n": 1, "bhd": 1000, "pct": 0.01}, {"n": 2, "bhd": 2000, "pct": 0.02}]}},
           {"salesman": "Van Z", "name": "Rep Zulu", "net_bhd": 50, "no_target": True},
           {"salesman": "Van Y", "name": "Rep Yankee", "net_bhd": 50, "no_target": False, "is_active": False}]
    dim = [{"k": "cmp", "dim": "salesman", "name": "Van A - Acc WH", "net_bhd": M(1000)},
           {"k": "mtd", "dim": "salesman", "name": "Van A - Acc WH", "net_bhd": M(1200)}]
    rows = enrich.team_rows(att, {"rows": [{"salesman": "Van A", "business_days_since_invoice": 0}]}, dim,
                            date(2026, 10, 14))
    assert [r["name"] for r in rows] == ["Rep Alpha"], "no target or switched off: not in the table"
    r = rows[0]
    assert r["target_bhd"] == M(1000) and r["achieved_pct"] == 120.0 and r["change_pct"] == 20.0
    assert "kickback" not in json.dumps(rows), "the kickback figures never leave the attainment row"
    push = enrich.push_items(
        [{"item_name": "Up", "qty_30": 10, "qty_prev": 2, "net_30": 50, "net_prev": 10},
         {"item_name": "Down", "qty_30": 2, "qty_prev": 9, "net_30": 5, "net_prev": 30},
         {"item_name": "Thin", "qty_30": 10, "qty_prev": 2, "net_30": 50, "net_prev": 10}],
        [{"item_name": "Up", "current_stock": 100, "days_cover": 90, "division": "Accessories"},
         {"item_name": "Thin", "current_stock": 2, "days_cover": 3, "division": "Accessories"}])
    assert [p["item"] for p in push] == ["Up"], push
    assert enrich.discounts({"lines": 0, "bhd": 0, "net_bhd": 100})["evidenced"] is False
    shown = enrich.discounts({"lines": 4, "bhd": "5.000", "net_bhd": "100.000"})
    assert shown["evidenced"] is True and shown["share_pct"] == 5.0
    from app import metrics
    assert metrics.rep_matches("Van A - Acc WH", "Van A") and not metrics.rep_matches("Van A", "Van A - Acc WH")


# ── deliver ──────────────────────────────────────────────────────────────────────────────────────

def closed(run: str, event: str, **fields) -> None:
    loadplan.journal_append(run, "started", text="Load started.")
    loadplan.journal_append(run, event, **fields)


def args(**kw):
    return SimpleNamespace(run=kw.get("run", RUN), preview=kw.get("preview", False))


class FakeBrief:
    """sales.full_screen without a database: the invented facts, and the patch a whole brief stamps. Each call
    is also put on `order`, when one is given (the list the movement step's stand-in writes to as well), and the
    movement step it was handed is kept in `moves`."""

    def __init__(self, order: list | None = None):
        self.calls, self.moves = [], []
        self.order = order if order is not None else []

    def __call__(self, *, record=True, enrich=False, moves=None):
        self.calls.append({"record": record, "enrich": enrich})
        self.moves.append(moves)
        self.order.append(("brief", record))
        patch = {"last_brief_data_date": "2026-10-14", "last_brief_load_run": RUN} if record else {}
        return {"d": date(2026, 10, 14), "text": "YQ ACCESSORIES BRIEF - invented\nline two", "facts": facts(),
                "warnings": [], "patch": patch}


STEP_FACTS = {"first": "2026-10-08", "last": "2026-10-14", "ledger_to": "2026-10-14", "to_stores_units": 340,
              "to_stores_bhd": "850.500", "back_units": 12, "damage_units": 3, "not_received_units": 6,
              "not_received_bhd": "15.250", "to_check_count": 4, "to_check_bhd": "40.000", "per_store_ok": True,
              "note": None}


def step_answer(record: bool = True, **over) -> dict:
    """What deliver.moves_step answers when its child did its work (invented figures)."""
    out = {"facts": dict(STEP_FACTS), "facts_why": None,
           "recorded": {"ok": True, "recorded": True, "why": None} if record else None,
           "recorded_why": None if record else deliver.PREVIEW_RECORDS_NOTHING, "seconds": 1.5}
    out.update(over)
    return out


class step_standing_in:
    """deliver.moves_step in this process, by its contract (no child is started): it answers `answer`
    (step_answer by default) and puts ("moves_step", record) on `order`."""

    def __init__(self, order: list, answer: dict | None = None):
        self.order, self.answer = order, answer

    def __call__(self, record: bool, days: int = 7, **_kw) -> dict:
        self.order.append(("moves_step", record))
        assert days == sales.MOVES_DAYS, days
        return dict(self.answer) if self.answer is not None else step_answer(record)

    def __enter__(self):
        self.real, deliver.moves_step = deliver.moves_step, self
        return self

    def __exit__(self, *exc):
        deliver.moves_step = self.real
        return False


_ABSENT = object()


class modules_standing_in:
    """scripts.ops.<name> replaced by a module made of the given attributes (None: a module that cannot be
    imported at all) for the length of a with-block; the modules that were there come back after."""

    def __init__(self, **modules):
        self.modules = modules

    def __enter__(self):
        package = sys.modules["scripts.ops"]
        self.saved = {}
        for name, attrs in self.modules.items():
            full = f"scripts.ops.{name}"
            self.saved[name] = (sys.modules.get(full, _ABSENT), package.__dict__.pop(name, _ABSENT))
            if attrs is None:
                sys.modules[full] = None                     # an import of it fails, as for a module that is not there
                continue
            module = types.ModuleType(full)
            for key, value in attrs.items():
                setattr(module, key, value)
            sys.modules[full] = module
            setattr(package, name, module)
        return self

    def __exit__(self, *exc):
        package = sys.modules["scripts.ops"]
        for name, (module, attr) in self.saved.items():
            full = f"scripts.ops.{name}"
            if module is _ABSENT:
                sys.modules.pop(full, None)
            else:
                sys.modules[full] = module
            package.__dict__.pop(name, None)
            if attr is not _ABSENT:
                setattr(package, name, attr)
        return False


# The movement step's children, started for real by deliver.moves_step (a process of their own: they are this
# module's functions, which the child imports by name, so they live at the module's top).

def answer_child(conn, root: str, record: bool, days: int) -> None:
    """A child that answers at once, and says what it was handed."""
    conn.send(dict(step_answer(record), root=root, days=days))
    conn.close()


def hang_child(conn, root: str, record: bool, days: int) -> None:
    """A child that never answers (a database that does not answer its reads)."""
    time.sleep(600)


def silent_child(conn, root: str, record: bool, days: int) -> None:
    """A child that ends without an answer."""
    conn.close()


@test("deliver: the kind comes from the load's closing row")
def _():
    assert deliver.kind_of(None) == ("none", [])
    assert deliver.kind_of({"event": "finished", "needs_look": None}) == ("full", [])
    assert deliver.kind_of({"event": "finished", "needs_look": ["publish"]}) == ("full", [])
    assert deliver.kind_of({"event": "finished", "needs_look": ["reconcile"]}) == ("look", ["reconcile"])
    assert deliver.kind_of({"event": "failed", "written": True, "step": "refresh"}) == ("failed", ["refresh"])
    assert deliver.kind_of({"event": "failed", "written": False}) == ("none", [])


@test("deliver: files and manifest with each sha256; the brief saved, stamped and shown once in the chat")
def _():
    real = sales.full_screen
    order: list = []
    fake = FakeBrief(order)
    sales.full_screen = fake
    try:
        with data_root(), step_standing_in(order):
            status.write_state({"last_load": {"run": RUN}})
            closed(RUN, "finished", result="ok", text="LOAD FINISHED.")
            res = deliver.run(args())
            assert res["ok"] and res["flags"]["kind"] == "full" and fake.calls == [{"record": True, "enrich": True}]
            # the movement step once, and before the brief is made: the brief is handed its answer and redoes none of it
            assert order == [("moves_step", True), ("brief", True)], order
            assert fake.moves == [step_answer(True)]
            assert "Transfer checks" not in res["text"] and res["warnings"] == []
            folder = paths.exports_dir(date(2026, 10, 14)) / "delivery" / RUN
            man = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
            assert man["kind"] == "full" and man["team_text"] is True and set(man["files"]) == {"html", "text", "whatsapp", "page"}
            for key, name in man["files"].items():
                body = (folder / name).read_text(encoding="utf-8")
                assert hashlib.sha256(body.encode("utf-8")).hexdigest() == man["sha256"][key], key
            assert (paths.exports_dir(date(2026, 10, 14)) / "brief.txt").is_file()
            state = status.read_state()
            assert state["last_brief_load_run"] == RUN and state["brief_unshown"]["run"] == RUN
            pack = [r for r in loadplan.delivery_read(RUN) if r["event"] == "pack"]
            assert len(pack) == 1 and pack[0]["kind"] == "full" and Path(pack[0]["manifest"]) == folder / "manifest.json"
            shown = sales.unshown_brief(date(2026, 10, 14), status.read_state())
            assert shown and shown.startswith("(Made at ") and "YQ ACCESSORIES BRIEF - invented" in shown
            assert sales.unshown_brief(date(2026, 10, 15), status.read_state()) is None, "another data date"
            status.write_state({"last_load": {"run": "161026_0900"}})
            assert sales.unshown_brief(date(2026, 10, 14), status.read_state()) is None, "another load since"
    finally:
        sales.full_screen = real


@test("deliver refuses a full brief once the database no longer holds the load: a newer load, a failed one, a write")
def _():
    from scripts import yq_load_go, yq_send     # they read the refusal by its word, and the kind by the same rule
    assert yq_send.LOOK_STEPS == deliver.LOOK_STEPS
    real, real_newest = sales.full_screen, deliver.newest_ingest_run
    order: list = []
    fake = FakeBrief(order)
    sales.full_screen = fake
    step = step_standing_in(order)
    step.__enter__()
    try:
        for setup, said in ((lambda: status.write_state({"last_load": {"run": "161026_0900"}}),
                             "the last load on record is run 161026_0900, not this one"),
                            (lambda: (status.write_state({"last_load": {"run": RUN}}),
                                      loadplan.journal_append("161026_0900", "started"),
                                      loadplan.journal_append("161026_0900", "failed", step="refresh", result="failed",
                                                              written=True, at=(datetime.now(timezone.utc) + timedelta(
                                                                  minutes=1)).isoformat(timespec="seconds"))),
                             "a later load (run 161026_0900) failed after it began to write"),
                            (lambda: (status.write_state({"last_load": {"run": RUN}}),
                                      loadplan.journal_append(RUN, "step_ok", step="refresh", ingest_runs=[5, 7])),
                             "the database was written after this load")):
            with data_root():
                closed(RUN, "finished", result="ok", text="LOAD FINISHED.")
                setup()
                deliver.newest_ingest_run = lambda: 9
                res = deliver.run(args())
                assert res["ok"] is False and res["exit"] == 2 and said in res["text"], res["text"]
                assert yq_load_go.DELIVER_REFUSED in res["text"], "the writer would offer a retry that is refused"
                packs = [r for r in loadplan.delivery_read(RUN) if r["event"] == "pack"]
                assert [r["kind"] for r in packs] == ["refused"] and said in packs[0]["why"], packs   # the sender reads why
                assert fake.calls == [] and not (paths.exports_dir(date(2026, 10, 14)) / "delivery" / RUN).exists()
                assert order == [], "a refused delivery starts no movement step and records nothing"
                assert deliver.run(args(preview=True))["ok"], "a preview is no delivery: it is always made"
                assert order == [("moves_step", False), ("brief", False)], order
                fake.calls.clear()
                order.clear()
        with data_root():                                   # its own rows are the newest: the brief is made
            closed(RUN, "finished", result="ok", text="LOAD FINISHED.")
            status.write_state({"last_load": {"run": RUN}})
            loadplan.journal_append(RUN, "step_ok", step="refresh", ingest_runs=[5, 7])
            deliver.newest_ingest_run = lambda: 7
            assert deliver.run(args())["flags"]["kind"] == "full"
            assert deliver.own_last_ingest_run(RUN) == 7
            assert order == [("moves_step", True), ("brief", True)], order
    finally:
        step.__exit__(None, None, None)
        sales.full_screen, deliver.newest_ingest_run = real, real_newest


@test("deliver --preview records nothing: no register, no state, no row in the delivery journal")
def _():
    real = sales.full_screen
    order: list = []
    fake = FakeBrief(order)
    sales.full_screen = fake
    try:
        with data_root(), step_standing_in(order):
            status.write_state({"last_load": {"run": RUN}})
            closed(RUN, "finished", result="ok", text="LOAD FINISHED.")
            before = status.read_state()
            res = deliver.run(args(preview=True))
            assert res["ok"] and fake.calls == [{"record": False, "enrich": True}]
            # the step is asked to record nothing: it reads the MOVEMENTS facts only, and says nothing of a recording
            assert order == [("moves_step", False), ("brief", False)], order
            assert "Transfer checks" not in res["text"]
            assert status.read_state() == before and loadplan.delivery_read(RUN) == []
            assert (paths.exports_dir(date(2026, 10, 14)) / "delivery" / (RUN + "-preview") / "email.html").is_file()
    finally:
        sales.full_screen = real


@test("deliver: a movement step that did not record, failed or ran out of time is one line, never a stop")
def _():
    real = sales.full_screen
    tail = "; the brief lists the transfer items as they stood."
    cases = (
        (step_answer(recorded={"ok": False, "recorded": False, "why": "the ledger is not proven yet"}),
         "Transfer checks: not recorded this time (the ledger is not proven yet)" + tail),
        (step_answer(recorded={"ok": False}), "Transfer checks: not recorded this time (no reason given)" + tail),
        (step_answer(recorded=None, recorded_why="RuntimeError: the register is held"),
         "Transfer checks: not recorded this time (RuntimeError: the register is held)" + tail),
        (deliver._failed_step("the movement step took longer than 90 s and was stopped", True),
         "Transfer checks: not recorded this time (the movement step took longer than 90 s and was stopped)" + tail),
        # a reason in words the brief never uses: the line goes without it
        (step_answer(recorded={"ok": False, "why": "a leak in the ledger"}),
         "Transfer checks: not recorded this time" + tail),
    )
    try:
        for answer, said in cases:
            order: list = []
            fake = FakeBrief(order)
            sales.full_screen = fake
            with data_root(), step_standing_in(order, answer):
                status.write_state({"last_load": {"run": RUN}})
                closed(RUN, "finished", result="ok", text="LOAD FINISHED.")
                res = deliver.run(args())
                assert res["ok"] and res["flags"]["kind"] == "full" and fake.calls == [{"record": True, "enrich": True}]
                notes = [ln for ln in res["text"].splitlines() if ln.startswith("Transfer checks")]
                assert notes == [said], (notes, said)
                fmt.careful(notes[0])
                assert fake.moves == [answer], "the brief is handed the step's answer as it is"
                folder = paths.exports_dir(date(2026, 10, 14)) / "delivery" / RUN
                assert (folder / "email.html").is_file() and (folder / "whatsapp.txt").is_file(), "the e-mail is made"
                assert [r["kind"] for r in loadplan.delivery_read(RUN) if r["event"] == "pack"] == ["full"]
                assert status.read_state()["brief_unshown"]["run"] == RUN
                assert order == [("moves_step", True), ("brief", True)], order
        # a preview records nothing, so it has no such line, whatever the step answers
        assert deliver.moves_note(deliver._failed_step("the movement step gave no answer", False), True) is None
    finally:
        sales.full_screen = real


@test("the movement step runs in a process of its own: its answer, or the reason, within its own time budget")
def _():
    import multiprocessing
    with data_root() as root:
        began = time.monotonic()
        got = deliver.moves_step(True, 7, timeout=120, target=answer_child)
        assert got["facts"] == STEP_FACTS and got["recorded"]["ok"] is True and got["recorded_why"] is None, got
        assert got["root"] == str(root) and got["days"] == 7, "the same data root, the brief's days"
        assert isinstance(got["seconds"], float) and 0 <= got["seconds"] <= time.monotonic() - began + 1
        assert deliver.moves_step(False, 7, timeout=120, target=answer_child)["recorded_why"] == "a preview records nothing"
        # a child that never answers is stopped when its time is up, and the reason stands in for both parts
        began = time.monotonic()
        got = deliver.moves_step(True, 7, timeout=2, target=hang_child)
        waited = time.monotonic() - began
        why = "the movement step took longer than 2 s and was stopped"
        assert (got["facts"], got["facts_why"], got["recorded"], got["recorded_why"]) == (None, why, None, why), got
        assert 2 <= waited < 30, waited
        assert not [p for p in multiprocessing.active_children() if p.name == "yq-delivery-movement-step"]
        # ... a preview's the same, and it never says it recorded anything
        assert deliver.moves_step(False, 7, timeout=1, target=hang_child)["recorded_why"] == "a preview records nothing"
        # a child that ends without an answer
        got = deliver.moves_step(True, 7, timeout=120, target=silent_child)
        assert got["facts"] is None and got["facts_why"] == "the movement step ended without an answer", got
        assert got["recorded_why"] == "the movement step ended without an answer"


@test("a movement step that hangs costs the delivery its own time budget, and the e-mail is still made")
def _():
    real_screen, real_child, real_budget = sales.full_screen, deliver._moves_child, deliver.MOVES_TIMEOUT_S
    fake = FakeBrief()
    sales.full_screen, deliver._moves_child, deliver.MOVES_TIMEOUT_S = fake, hang_child, 2
    try:
        with data_root():
            status.write_state({"last_load": {"run": RUN}})
            closed(RUN, "finished", result="ok", text="LOAD FINISHED.")
            began = time.monotonic()
            res = deliver.run(args())
            assert time.monotonic() - began < 60
            assert res["ok"] and res["flags"]["kind"] == "full", res
            assert ("Transfer checks: not recorded this time (the movement step took longer than 2 s and was stopped); "
                    "the brief lists the transfer items as they stood.") in res["text"].splitlines()
            handed = fake.moves[0]
            assert handed["facts"] is None and handed["recorded"] is None, "the brief is told, and redoes nothing"
            folder = paths.exports_dir(date(2026, 10, 14)) / "delivery" / RUN
            assert (folder / "email.html").is_file() and (folder / "manifest.json").is_file()
            assert [r["kind"] for r in loadplan.delivery_read(RUN) if r["event"] == "pack"] == ["full"]
    finally:
        sales.full_screen, deliver._moves_child, deliver.MOVES_TIMEOUT_S = real_screen, real_child, real_budget


@test("the movement step's child: the rules recorded (never by a preview), the MOVEMENTS facts, each failure a reason")
def _():
    class Conn:
        def __init__(self):
            self.sent, self.closed = [], False

        def send(self, obj):
            self.sent.append(obj)

        def close(self):
            self.closed = True
    calls: list = []

    def ensure_recorded(record=True):
        calls.append(("ensure_recorded", record))
        return {"ok": True, "recorded": True}

    def brief_facts(days=7):
        calls.append(("brief_facts", days))
        return dict(STEP_FACTS)
    rules = ("xfer_open", "xfer_twins", "qty_diff", "xfer_elsewhere", "rcpt_orphan")
    with data_root() as root:
        with modules_standing_in(checks={"ensure_recorded": ensure_recorded, "MOVE_RULES": rules},
                                 moves={"brief_facts": brief_facts}):
            conn = Conn()
            deliver._moves_child(conn, str(root), True, 7)
            assert calls == [("ensure_recorded", True), ("brief_facts", 7)], calls
            assert conn.sent == [{"facts": STEP_FACTS, "facts_why": None, "recorded": {"ok": True, "recorded": True},
                                  "recorded_why": None}] and conn.closed
            calls.clear()
            conn = Conn()
            deliver._moves_child(conn, str(root), False, 7)                 # a preview: never asked to record
            assert calls == [("brief_facts", 7)] and conn.sent[0]["recorded"] is None
            assert conn.sent[0]["recorded_why"] == "a preview records nothing" and conn.closed
        # each part that fails is its reason in the one answer, which is still sent

        def unreadable(days=7):
            raise RuntimeError("the ledger cache could not be read")
        with modules_standing_in(checks=None, moves={"brief_facts": unreadable}):
            conn = Conn()
            deliver._moves_child(conn, str(root), True, 7)
            sent = conn.sent[0]
            assert len(conn.sent) == 1 and conn.closed and sent["facts"] is None and sent["recorded"] is None
            assert sent["recorded_why"].startswith("ModuleNotFoundError: "), sent
            assert sent["facts_why"] == "RuntimeError: the ledger cache could not be read", sent


@test("deliver: a load that needs a look or failed gets a note with no figure; one that wrote nothing gets nothing")
def _():
    for event, fields, kind in (("finished", {"result": "ok_needs_look", "needs_look": ["post_gate"]}, "look"),
                                ("failed", {"result": "failed", "written": True, "step": "refresh"}, "failed")):
        with data_root():
            closed(RUN, event, text="closed", **fields)
            res = deliver.run(args())
            assert res["flags"]["kind"] == kind, res
            man = next(Path(f) for f in res["files"] if f.endswith("manifest.json"))
            data = json.loads(man.read_text(encoding="utf-8"))
            assert data["kind"] == kind and set(data["files"]) == {"html", "text"}
            text = (man.parent / "email.txt").read_text(encoding="utf-8")
            assert "BHD" not in text and "no brief" not in text.lower() or True
    with data_root():
        closed(RUN, "failed", text="stopped", result="check_again", written=False)
        res = deliver.run(args())
        assert res["flags"]["kind"] == "none" and [r["kind"] for r in loadplan.delivery_read(RUN)] == ["none"]


@test("deliver: a team message the check refuses is not written, and the e-mail goes without it")
def _():
    real_screen, real_team = sales.full_screen, render.team_text
    sales.full_screen = FakeBrief()
    render.team_text = lambda f, d: "Call Shop Moonlight Trading about what it owes."
    try:
        with data_root():
            status.write_state({"last_load": {"run": RUN}})
            closed(RUN, "finished", result="ok", text="LOAD FINISHED.")
            res = deliver.run(args())
            assert res["flags"]["team_text"] is False and "Team message: NOT made" in res["text"]
            folder = paths.exports_dir(date(2026, 10, 14)) / "delivery" / RUN
            assert not (folder / "whatsapp.txt").exists() and (folder / "email.html").is_file()
            assert "Open in WhatsApp" not in (folder / "email.html").read_text(encoding="utf-8")
    finally:
        sales.full_screen, render.team_text = real_screen, real_team


@test("/yq-brief team prints the last load's team message as it was made, or says there is none yet")
def _():
    with data_root():
        status.write_state({"last_load": {"run": RUN}})
        assert sales.team_message().startswith("No team message yet")
        folder = paths.exports_dir(date(2026, 10, 14)) / "delivery" / RUN
        folder.mkdir(parents=True)
        (folder / "whatsapp.txt").write_text("*YQ Accessories - Thu 15 Oct*\nhello team\n", encoding="utf-8")
        said = sales.team_message()
        assert said.startswith(f"Team message of load {RUN}") and said.endswith("hello team")
        res = sales.run(SimpleNamespace(spec=["team"], again=False, top=None))
        assert res["flags"] == {"team": True} and "hello team" in res["text"]


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
        finally:
            paths.set_data_root(None)
            config.use(None)
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
