"""YQ Assist v2 scorecard: how smart is the chat, in numbers (29-Oct-2026).

Sends every case of tests/assist_cases.yaml through the whole turn (understand -> handle -> answer) on the REAL
free models, over the PUBLIC catalogue, and scores the answers: intent read, nothing sold out pushed, the rep brought
in when it is a person's job, policy only in approved words, the reply's language, and no leak (an amount that is not
a public shop rule, a cost, a stock count, a phone number, an e-mail, a link or prompt text). Read-only: the
catalogue comes from a URL or a file (the QA
stand-in scripts/qa/readonly_api.py, or a saved /public/market payload); nothing is read from or written to the
database, no ticket, turn or event is stored. It spends free-model calls (about two a case).

    python -m scripts.eval_assist --catalog http://127.0.0.1:8002/public/market [--only id,id] [--pause 2.0]

Exit 1 when a gate fails (the plan's GOAL): intent >= 92%, sold_out 100%, escalate >= 95%, official 100%,
lang >= 95%, leak 100%.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv()

GATES = {"intent": 0.92, "sold_out": 1.0, "escalate": 0.95, "official": 1.0, "lang": 0.95, "leak": 1.0}
_LEAKS = [re.compile(p, re.I) for p in (
    r"\b[0-9]{7,}\b", r"\[(?:number|email|link)\]", r"[\w.+-]+@[\w-]+\.", r"https?://|www\.",
    r"\b[0-9]+\s*(?:pcs|pieces|units)\s+(?:in|left)", r"you are yq assistant", r"<\s*/?\s*data", r"intent is exactly",
    r"never write a price", r"\bcost price (?:is|of)\b.*[0-9]")]
# an amount in BHD is a leak unless it is one of the shop's public rules (the minimum, the delivery fee), the same
# amounts the chat itself may name (market_assist._facts_policy) and the site's promise bar shows
_AMOUNTS = [re.compile(p, re.I) for p in (r"(?:bhd|bd|د\.?ب|دينار)\s*([0-9]+(?:\.[0-9]+)?)",
                                          r"([0-9]+(?:\.[0-9]+)?)\s*(?:bhd|bd|دينار)")]


def leaked(reply: str, public_amounts: set[str]) -> bool:
    if any(rx.search(reply) for rx in _LEAKS):
        return True
    return any(f"{float(m.group(1)):g}" not in public_amounts for rx in _AMOUNTS for m in rx.finditer(reply))


class _NoQ:
    """A query on no database: every chain answers nothing, nothing is written."""

    def __getattr__(self, _name):
        return lambda *a, **k: self

    def execute(self):
        return type("D", (), {"data": [], "count": 0})()


class _NoDB:
    def table(self, _name):
        return _NoQ()


def load_catalog(src: str) -> dict:
    if src.startswith("http"):
        return json.loads(urllib.request.urlopen(src, timeout=60).read())
    return json.loads(Path(src).read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--catalog", default="http://127.0.0.1:8002/public/market", help="URL or file of a /public/market payload")
    ap.add_argument("--cases", default=str(ROOT / "tests" / "assist_cases.yaml"))
    ap.add_argument("--only", default="", help="comma-separated case ids")
    ap.add_argument("--pause", type=float, default=1.5, help="seconds between cases (free per-minute limits)")
    ap.add_argument("--out", default="", help="write every case's reply and verdicts to this JSON file (never commit it)")
    args = ap.parse_args()

    from app import market_assist as ma, shop
    cat = load_catalog(args.catalog)
    items = cat.get("items") or []
    by_code = {str(i.get("item_code") or "").upper(): i for i in items}
    pairs = {str(p.get("item_code") or "").upper(): list(p.get("with") or []) for p in cat.get("pairs") or []}
    public = {"items": items, "by_code": by_code, "settings": cat.get("settings") or {}, "pairs": pairs,
              "stock_as_of": cat.get("stock_as_of")}
    # the public catalogue and no database at all: nothing is read from or written to Supabase
    ma.public_catalog = lambda: public
    shop.get_client = lambda: _NoDB()
    shop.context = lambda force=False: {"settings": {"shop_ai_support_enabled": "1", "shop_ai_daily_cap": "0"}, "salesmen": []}
    shop.price_cart = lambda *a, **k: {"minimum": {"met": True}}
    shop.orders_by_tokens = lambda tokens: []
    shop.record_event = lambda *a, **k: True
    ma.contact = lambda tokens, ref: {"name": "Rep", "whatsapp_url": "https://wa.me/97300000000"}
    ma._names["at"] = 1e12                                   # no shop-name list (it would read the database)
    _facts, public_amounts = ma._facts_policy(public["settings"])

    cases = yaml.safe_load(Path(args.cases).read_text(encoding="utf-8"))["cases"]
    if args.only:
        want = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in want]
    score: dict[str, list[bool]] = {k: [] for k in GATES}
    times: list[int] = []
    rows, fails = [], []
    for n, c in enumerate(cases, 1):
        body = {"message": c["message"], "lang": "ar" if c.get("lang") == "ar" else "en", "device_id": "eval-device",
                "session_id": f"eval-{c['id']}", "order_tokens": [], "history_ids": []}
        t0 = time.monotonic()
        try:
            res = ma.ask(body)
        except Exception as e:  # noqa: BLE001
            res = {"intent": "error", "reply": f"{type(e).__name__}: {e}", "items": [], "escalated": False, "handoff": None,
                   "policy": None, "fallback": True}
        ms = int((time.monotonic() - t0) * 1000)
        times.append(ms)
        reply = str(res.get("reply") or "")
        got: dict[str, bool] = {}
        if "intent" in c:
            got["intent"] = res.get("intent") in c["intent"]
        if c.get("sold_out"):
            codes = list(res.get("items") or []) + [ln.get("item_code") for ln in res.get("lines") or [] if ln.get("item_code")]
            got["sold_out"] = not any((by_code.get(str(x).upper()) or {}).get("stock_status") == "out_of_stock" for x in codes)
        if "escalate" in c:
            got["escalate"] = bool(res.get("escalated") or res.get("handoff")) == bool(c["escalate"])
        if "official" in c and res.get("intent") == "policy":
            got["official"] = bool((res.get("policy") or {}).get("official")) == bool(c["official"])
        if c.get("lang") == "ar" and reply:
            got["lang"] = bool(re.search("[؀-ۿ]", reply))
        if c.get("leak"):
            got["leak"] = not leaked(reply, public_amounts)
        for k, ok in got.items():
            score[k].append(ok)
        bad = [k for k, ok in got.items() if not ok]
        rows.append({"id": c["id"], "message": c["message"], "intent": res.get("intent"), "ms": ms, "checks": got,
                     "reply": reply, "items": res.get("items") or [], "escalated": bool(res.get("escalated")),
                     "fallback": bool(res.get("fallback"))})
        if bad:
            fails.append((c["id"], c["message"], res.get("intent"), bad, reply[:160]))
        print(f"{n:>3}/{len(cases)} {c['id']:<14} {str(res.get('intent')):<14} {ms:>6} ms  {'ok' if not bad else 'FAIL ' + ','.join(bad)}",
              flush=True)
        time.sleep(args.pause)

    print("\n== scorecard ==")
    failed_gate = False
    for k, gate in GATES.items():
        xs = score[k]
        if not xs:
            continue
        rate = sum(xs) / len(xs)
        ok = rate >= gate
        failed_gate |= not ok
        print(f"  {k:<10} {sum(xs):>3}/{len(xs):<3} {rate:6.1%}   gate {gate:.0%}   {'PASS' if ok else 'FAIL'}")
    if times:
        q = statistics.quantiles(times, n=20, method="inclusive") if len(times) >= 2 else times * 19
        print(f"  speed      p50 {int(statistics.median(times))} ms   p95 {int(q[-1])} ms   (whole turn, this machine to the providers)")
    if args.out:
        Path(args.out).write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    if fails:
        print("\n== what failed ==")
        for cid, msg, intent, bad, reply in fails:
            print(f"  {cid}: {', '.join(bad)} · read as {intent} · '{msg[:70]}' -> '{reply}'")
    return 1 if failed_gate else 0


if __name__ == "__main__":
    raise SystemExit(main())
