"""YQ Assist v2 tests (29-Oct-2026): the chat that sells, supports and escalates.

    python -m tests.test_assist_v2

Same lightweight runner and stand-ins as tests/test_market_assist.py (no pytest; nothing is sent anywhere and
nothing is written). The rules pinned here are the owner's: a recommendation is never sold out, a policy answer
is only the office's approved text, a ticket only lands on an order this device holds, and nothing a model sees
is more than the public catalogue already shows.
"""
from __future__ import annotations

import io
import json
import sys
import traceback
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import warnings  # noqa: E402
warnings.filterwarnings("ignore")

TESTS: list[tuple[str, object]] = []


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


# ── 1. data ────────────────────────────────────────────────────────────────────────────────

@test("migration: tickets + timeline service-role only, sequences revoked, refs by trigger, settings, self-check")
def _():
    sql = (ROOT / "scripts" / "market_assist_v2_migration.sql").read_text(encoding="utf-8").lower()
    for t in ("shop_tickets", "shop_ticket_events"):
        assert f"create table if not exists {t}" in sql
        assert f"alter table {t} enable row level security" in sql
        assert f"revoke all on table {t} from anon, authenticated" in sql
        assert f"revoke all on sequence {t}_id_seq from anon, authenticated" in sql
    assert "before insert on shop_tickets" in sql and "'t-' ||" in sql          # T-YYMM-NNNN
    assert "before update on shop_ticket_events" in sql and "before truncate on shop_ticket_events" in sql
    assert "on delete cascade" in sql                                            # the retention purge
    for k, v in (("shop_ai_support_enabled", "0"), ("shop_ai_retention_days", "180"),
                 ("shop_ticket_keep_days", "730"), ("shop_ticket_sla_hours", "24"), ("shop_ai_policies", "[]")):
        assert f"('{k}', '{v}'" in sql, k
    assert "on conflict (key) do nothing" in sql and "raise exception" in sql
    assert "'ticket', 'callback', 'ticket_sla'" in sql                          # shop_notifications.kind
    body = sql.split("-- additive", 1)[1]           # the header's reverse recipe may name drops
    assert not any(s in body for s in ("drop table", "drop view", "delete from", "truncate table", "drop column"))


@test("settings: the five v2 defaults match the migration's seeds (no business number lives only in code)")
def _():
    from app import shop
    sql = (ROOT / "scripts" / "market_assist_v2_migration.sql").read_text(encoding="utf-8")
    for k in ("shop_ai_support_enabled", "shop_ai_retention_days", "shop_ticket_keep_days",
              "shop_ticket_sla_hours", "shop_ai_policies"):
        assert k in shop.SETTING_DEFAULTS, k
        assert f"('{k}', '{shop.SETTING_DEFAULTS[k]}'" in sql, k


@test("policies: only known topics, English required, one answer per topic, long text refused, empty clears")
def _():
    from app import shop
    ok = shop.validate_policies('[{"topic": "Payment", "en": " Cash or BenefitPay on delivery. ", "ar": ""}]')
    assert json.loads(ok) == [{"topic": "payment", "en": "Cash or BenefitPay on delivery."}]
    assert shop.validate_policies("") == "[]" and shop.validate_policies(None) == "[]"
    for bad in ('{"topic": "payment"}', '[{"topic": "refunds", "en": "x"}]', '[{"topic": "returns"}]',
                '[{"topic": "returns", "en": "a"}, {"topic": "returns", "en": "b"}]',
                json.dumps([{"topic": "credit", "en": "x" * 601}]), "not json"):
        try:
            shop.validate_policies(bad)
        except shop.ShopError:
            continue
        raise AssertionError(f"accepted: {bad[:40]}")
    pol = shop.policies({"shop_ai_policies": '[{"topic": "returns", "en": "Ask your rep.", "ar": "اسأل مندوبك."}]'})
    assert pol == {"returns": {"en": "Ask your rep.", "ar": "اسأل مندوبك."}}
    assert shop.policies({"shop_ai_policies": "{broken"}) == {} and shop.policies({}) == {}


def main() -> int:
    passed = failed = 0
    for name, fn in TESTS:
        try:
            fn()
            print(f"  PASS  {name}")
            passed += 1
        except Exception:  # noqa: BLE001
            print(f"  FAIL  {name}")
            traceback.print_exc(limit=3)
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
