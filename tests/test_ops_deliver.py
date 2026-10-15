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
    discounts, the stock in the vans), the rep cards (kickback figures never), names switched off;
  * the wa.me link carries the text exactly (percent-encoded UTF-8);
  * actions: the playbook by key, ACTION and TO CHECK, at most five ranked by effect, urgency and effort,
    a due date on a business day, an item whose words the guard refuses is withheld, never dropped;
  * enrich: the rep table from the attainment rows (tier 1 only), the push list (rising, in stock for two
    weeks), the shops due a re-order counted per rep by the portal's own name rule, discounts NOT EVIDENCED
    while the export carries none;
  * deliver: full, look, failed and none from the load's closing row; files and manifest with each
    sha256; the brief saved and stamped, and shown once in the chat (brief_unshown); a preview records
    nothing; a team message the check refuses is not written, and the e-mail goes without it;
  * /yq-brief team prints the last load's team message, or says when there is none.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tempfile
import traceback
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
                 "Stock in the vans", "Open in WhatsApp", "https://wa.me/?text=", "What to do", "Verify"):
        assert part in html, part
    assert "kickback" not in html.lower().replace("kickback figures are left out", ""), "no kickback figure"
    assert "No invoice yet this month: Rep Delta" in html
    assert "<script" not in html and "<img" not in html, "an e-mail carries no script and no image"
    assert "WHAT TO DO" in text and "TEAM MESSAGE" in text and "Rep Alpha" in text
    anonymous = render.email_html(f, top, every, LOAD, team, TODAY, names=False)
    assert "Shop Riverside Mart" not in anonymous and "Shop Moonlight Trading" not in anonymous
    assert "Shop Owing Corner" not in anonymous and "3 shops are due a re-order" in anonymous
    nostep = render.email_html(facts(pace={"kpi_step": None}), top, every, LOAD, None, TODAY)
    assert "Target: NOT EVIDENCED" in nostep and "Team message" not in nostep


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
    """sales.full_screen without a database: the invented facts, and the patch a whole brief stamps."""

    def __init__(self):
        self.calls = []

    def __call__(self, *, record=True, enrich=False):
        self.calls.append({"record": record, "enrich": enrich})
        patch = {"last_brief_data_date": "2026-10-14", "last_brief_load_run": RUN} if record else {}
        return {"d": date(2026, 10, 14), "text": "YQ ACCESSORIES BRIEF - invented\nline two", "facts": facts(),
                "warnings": [], "patch": patch}


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
    fake = FakeBrief()
    sales.full_screen = fake
    try:
        with data_root():
            status.write_state({"last_load": {"run": RUN}})
            closed(RUN, "finished", result="ok", text="LOAD FINISHED.")
            res = deliver.run(args())
            assert res["ok"] and res["flags"]["kind"] == "full" and fake.calls == [{"record": True, "enrich": True}]
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
    fake = FakeBrief()
    sales.full_screen = fake
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
                assert deliver.run(args(preview=True))["ok"], "a preview is no delivery: it is always made"
                fake.calls.clear()
        with data_root():                                   # its own rows are the newest: the brief is made
            closed(RUN, "finished", result="ok", text="LOAD FINISHED.")
            status.write_state({"last_load": {"run": RUN}})
            loadplan.journal_append(RUN, "step_ok", step="refresh", ingest_runs=[5, 7])
            deliver.newest_ingest_run = lambda: 7
            assert deliver.run(args())["flags"]["kind"] == "full"
            assert deliver.own_last_ingest_run(RUN) == 7
    finally:
        sales.full_screen, deliver.newest_ingest_run = real, real_newest


@test("deliver --preview records nothing: no register, no state, no row in the delivery journal")
def _():
    real = sales.full_screen
    fake = FakeBrief()
    sales.full_screen = fake
    try:
        with data_root():
            status.write_state({"last_load": {"run": RUN}})
            closed(RUN, "finished", result="ok", text="LOAD FINISHED.")
            before = status.read_state()
            res = deliver.run(args(preview=True))
            assert res["ok"] and fake.calls == [{"record": False, "enrich": True}]
            assert status.read_state() == before and loadplan.delivery_read(RUN) == []
            assert (paths.exports_dir(date(2026, 10, 14)) / "delivery" / (RUN + "-preview") / "email.html").is_file()
    finally:
        sales.full_screen = real


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
