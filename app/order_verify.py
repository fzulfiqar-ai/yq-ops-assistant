"""Verify a vendor order .xlsx before it's sent / paid — the human-in-the-loop gate.

Parses the uploaded order sheet (VFAN layout) and runs four checks per line:
  1. Price vs last VFAN — the order's net RMB vs v_supplier_price_history.latest_rmb.
  2. Math & discount     — qty x net ?= amount, and net ?= list x (1 - 18%).
  3. Margin              — at the MA selling price (BHD); flag thin (<20%) / below cost.
  4. Qty sanity          — vs v_stock_health velocity & stock; flag over/under-order.

Returns a per-line report + an overall verdict (pass / review / fail). Advise-only: a human
reviews the flags and approves. Reuses the same RMB->BHD + freight basis as reorder_proposal.
"""
from __future__ import annotations

import io
import json
import re

from app.db_read import exec_sql_params

RMB_BHD = 0.0525   # RMB -> BHD (matches the VFAN cost sheet exchange)
FREIGHT = 1.15     # ~15% freight loading when no actual landed cost is known yet
DISCOUNT = 0.18    # standard VFAN trade discount
_CODE = re.compile(r"^[A-Za-z]{1,3}\d")   # F15, X05, UK10 …


def _num(x: object) -> float | None:
    s = re.sub(r"[^\d.\-]", "", str(x or "").replace(",", ""))
    try:
        return float(s) if s not in ("", "-", ".") else None
    except ValueError:
        return None


def _q(sql: str, params: list) -> list:
    try:
        return exec_sql_params(sql, params) or []
    except Exception:  # noqa: BLE001
        return []


def _parse(data: bytes, filename: str) -> list[dict]:
    import pandas as pd
    df = pd.read_excel(io.BytesIO(data), header=None)
    rows = df.values.tolist()
    header, hdr_i = None, 0
    for i, r in enumerate(rows):
        cells = [str(c or "").strip().lower() for c in r]
        if any(c == "model" for c in cells) and any("qty" in c for c in cells):
            header, hdr_i = cells, i
            break
    if header is None:
        return []

    def col(*opts: str) -> int | None:
        for j, c in enumerate(header):
            if any(o in c for o in opts):
                return j
        return None

    mi, si, qi = col("model"), col("spec"), col("qty")
    ui, di, ai = col("unit price", "unit"), col("dis", "net"), col("amount", "amt")
    out: list[dict] = []
    for r in rows[hdr_i + 1:]:
        model = str(r[mi] or "").strip() if mi is not None and mi < len(r) else ""
        if not model or not _CODE.match(model) or model.lower() in ("nan", "total"):
            continue
        out.append({
            "model": model.upper(),
            "spec": (str(r[si]).strip() if si is not None and si < len(r) and r[si] else ""),
            "qty": _num(r[qi]) if qi is not None and qi < len(r) else None,
            "unit_price_rmb": _num(r[ui]) if ui is not None and ui < len(r) else None,
            "net_price_rmb": _num(r[di]) if di is not None and di < len(r) else None,
            "amount_rmb": _num(r[ai]) if ai is not None and ai < len(r) else None,
        })
    return out


def verify_order(data: bytes, filename: str) -> dict:
    rows = _parse(data, filename)
    if not rows:
        return {"ok": False, "verdict": "unreadable", "flags": 0, "lines": [],
                "summary": "Couldn't read an order table — need a Model / QTY / Unit Price layout."}

    codes = sorted({r["model"] for r in rows})
    names = json.dumps(codes)
    _in = "(SELECT jsonb_array_elements_text($1::jsonb))"
    vfan = {x["model"]: x for x in _q(
        "SELECT model, latest_rmb, latest_list_rmb, change_pct FROM v_supplier_price_history "
        f"WHERE model IN {_in}", [names])}
    sell = {x["code"]: float(x["s"]) for x in _q(
        "SELECT sku_code AS code, MAX(rate_bhd) AS s FROM selling_prices "
        "WHERE price_book='MA_base' AND warehouse_name IS NULL AND rate_bhd > 0 "
        f"AND sku_code IN {_in} GROUP BY sku_code", [names])}
    health = {x["code"]: x for x in _q(
        "SELECT SPLIT_PART(item_name,' ',1) AS code, MAX(current_stock) AS current_stock, "
        "MAX(avg_daily) AS avg_daily FROM v_stock_health "
        f"WHERE SPLIT_PART(item_name,' ',1) IN {_in} GROUP BY 1", [names])}

    out_lines: list[dict] = []
    total_flags = 0
    for r in rows:
        m = r["model"]
        qty = r.get("qty") or 0
        unit, net, amt = r.get("unit_price_rmb"), r.get("net_price_rmb"), r.get("amount_rmb")
        checks: list[tuple[str, str, str]] = []

        # 1 — price vs last VFAN (compare like-for-like: net price)
        v = vfan.get(m)
        if v and v.get("latest_rmb") and net:
            base = float(v["latest_rmb"])
            chg = round((net - base) / base * 100, 1)
            if abs(chg) < 0.5:
                checks.append(("price", "ok", f"matches last VFAN (RMB {base:g})"))
            else:
                checks.append(("price", "warn" if abs(chg) < 15 else "fail",
                               f"{'+' if chg > 0 else ''}{chg}% vs last VFAN (RMB {base:g})"))
        else:
            checks.append(("price", "info", "no VFAN price history to compare"))

        # 2 — math & discount
        if net and qty:
            exp_amt = round(qty * net, 2)
            if amt is None:
                checks.append(("math", "info", "no amount column to check"))
            elif abs(exp_amt - amt) <= max(0.5, exp_amt * 0.01):
                checks.append(("math", "ok", f"qty x net = RMB {exp_amt:,.0f}"))
            else:
                checks.append(("math", "fail", f"amount RMB {amt:,.0f} != qty x net RMB {exp_amt:,.0f}"))
        if unit and net:
            exp_net = round(unit * (1 - DISCOUNT), 2)
            if abs(exp_net - net) > max(0.5, unit * 0.02):
                checks.append(("discount", "warn",
                               f"net RMB {net:g} != 18% off list RMB {unit:g} (= RMB {exp_net:g})"))

        # 3 — margin at the MA selling price
        cost_rmb = net if net else (round(unit * (1 - DISCOUNT), 2) if unit else None)
        cost_bhd = round(cost_rmb * RMB_BHD * FREIGHT, 4) if cost_rmb else None
        s = sell.get(m)
        margin = None
        if s and cost_bhd:
            margin = round((s - cost_bhd) / s * 100, 1)
            if cost_bhd >= s:
                checks.append(("margin", "fail", f"below cost — sells BHD {s:g}, lands ~BHD {cost_bhd:g}"))
            elif margin < 20:
                checks.append(("margin", "warn", f"thin {margin}% (sells BHD {s:g})"))
            else:
                checks.append(("margin", "ok", f"{margin}% at BHD {s:g}"))
        else:
            checks.append(("margin", "info", "no selling price on file"))

        # 4 — qty sanity vs velocity
        h = health.get(m)
        cover = None
        if h and h.get("avg_daily"):
            avg = float(h["avg_daily"])
            stock = float(h.get("current_stock") or 0)
            cover = round((stock + qty) / avg) if avg else None
            if cover is not None and cover > 120:
                checks.append(("qty", "warn", f"buys ~{cover} days cover (>120 — overstock)"))
            elif cover is not None and cover < 15:
                checks.append(("qty", "warn", f"only ~{cover} days cover (<15 — under-order)"))
            else:
                checks.append(("qty", "ok", f"~{cover} days cover" if cover is not None else "ok"))
        else:
            checks.append(("qty", "info", "no velocity on file"))

        worst = "ok"
        for _, st, _ in checks:
            if st == "fail":
                worst = "fail"
                break
            if st == "warn" and worst != "fail":
                worst = "warn"
        line_flags = sum(1 for _, st, _ in checks if st in ("warn", "fail"))
        total_flags += line_flags
        out_lines.append({
            "model": m, "spec": r.get("spec"), "qty": qty,
            "unit_price_rmb": unit, "net_price_rmb": net, "amount_rmb": amt,
            "cost_bhd": cost_bhd, "sell_bhd": s, "margin_pct": margin,
            "cover_days": cover, "status": worst,
            "checks": [{"name": n, "status": st, "note": nt} for n, st, nt in checks],
        })

    fails = sum(1 for x in out_lines if x["status"] == "fail")
    warns = sum(1 for x in out_lines if x["status"] == "warn")
    verdict = "fail" if fails else ("review" if warns else "pass")
    summary = (f"{len(out_lines)} line(s): {fails} failing, {warns} to review. "
               + ("Fix the failing lines before sending."
                  if fails else "Review the flagged lines, then approve."
                  if warns else "All checks passed — ready to send."))
    total_amt = round(sum((x.get("amount_rmb") or 0) for x in out_lines), 2)
    return {"ok": True, "verdict": verdict, "flags": total_flags, "fails": fails, "warns": warns,
            "lines": out_lines, "total_amount_rmb": total_amt, "summary": summary, "vendor": "VFAN"}
