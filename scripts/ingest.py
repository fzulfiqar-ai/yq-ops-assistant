"""Phase 0 ingestion: Focus ERP exports -> cleaned CSVs in data/clean/.

Reads every file in "Focus ERP Data/", maps each to its table per docs/SCHEMA.md using the
real Focus column names, normalizes, and writes one CSV per table. Prints a data-quality
report and HARD-FAILS if the sales voucher<->invoice join match rate is below 80% (data rule 3).

Does NOT touch Supabase and does NOT read the vendor/pricing/research folders.

Usage:  python scripts/ingest.py
"""
from __future__ import annotations

import hashlib
import json
import sys
import warnings
from datetime import date, datetime
from pathlib import Path

import pandas as pd

warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "Focus ERP Data"
OUT_DIR = ROOT / "data" / "clean"

JOIN_MIN = 0.80
TOTAL_MARKERS = ("sub total", "grand total", "total", "opening balance", "closing balance")


# --------------------------------------------------------------------------- helpers
def norm_num(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "")
    if s in ("", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def norm_date(v) -> str | None:
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    if isinstance(v, (datetime, date)):
        return v.date().isoformat() if isinstance(v, datetime) else v.isoformat()
    s = str(v).strip()
    for fmt in ("%m/%d/%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%b-%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def txt(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def is_total(first_cell) -> bool:
    if first_cell is None:
        return False
    low = str(first_cell).strip().lower()
    return any(m in low for m in TOTAL_MARKERS)


def row_hash(values) -> str:
    return hashlib.md5("|".join("" if v is None else str(v) for v in values).encode()).hexdigest()[:16]


def read_grid(path: Path) -> pd.DataFrame:
    """Read a sheet with no header so we can handle Focus's title block + grouping."""
    return pd.read_excel(path, header=None, dtype=object, engine="openpyxl")


def report_date_from_title(grid: pd.DataFrame) -> str | None:
    """Pull '[As on date 01/06/2026]' from the top title block."""
    for r in range(min(6, len(grid))):
        for c in range(grid.shape[1]):
            cell = grid.iat[r, c]
            if isinstance(cell, str) and "as on date" in cell.lower():
                import re

                m = re.search(r"(\d{1,2}/\d{1,2}/\d{4})", cell)
                if m:
                    return norm_date(m.group(1))
    return None


# --------------------------------------------------------------------------- per-file parsers
def parse_orders(grid: pd.DataFrame, src: str) -> list[dict]:
    # header row 6 (index 5): Date, Invoice, Customer, Gross, Salesman, Payment Mode, Sales Account Name
    rows = []
    for i in range(6, len(grid)):
        r = list(grid.iloc[i])
        if is_total(r[0]):
            continue
        if norm_date(r[0]) is None and txt(r[1]) is None:
            continue
        rows.append({
            "invoice_no": txt(r[1]),
            "order_date": norm_date(r[0]),
            "customer_name": txt(r[2]),
            "gross_bhd": norm_num(r[3]),
            "salesman": txt(r[4]),
            "payment_mode": txt(r[5]),
            "sales_account_name": txt(r[6]) if len(r) > 6 else None,
            "source_file": src,
        })
    return [x for x in rows if x["invoice_no"]]


def parse_order_lines(grid: pd.DataFrame, src: str) -> list[dict]:
    # header row 6: Date,Voucher,Customer Account,Item,Quantity,Rate,Gross,Discount,Taxable,VAT Amount,Total Amount,Narration,Warehouse Name
    rows = []
    seq: dict[str, int] = {}
    for i in range(6, len(grid)):
        r = list(grid.iloc[i])
        if is_total(r[0]):
            continue
        inv = txt(r[1])
        if inv is None and norm_date(r[0]) is None:
            continue
        if not inv:
            continue
        seq[inv] = seq.get(inv, 0) + 1
        rows.append({
            "invoice_no": inv,
            "line_no": seq[inv],
            "line_date": norm_date(r[0]),
            "customer_account": txt(r[2]),
            "item_name": txt(r[3]),
            "quantity": norm_num(r[4]),
            "rate_bhd": norm_num(r[5]),
            "gross_bhd": norm_num(r[6]),
            "discount_bhd": norm_num(r[7]),
            "taxable_bhd": norm_num(r[8]),
            "vat_amount_bhd": norm_num(r[9]),
            "total_amount_bhd": norm_num(r[10]),
            "narration": txt(r[11]) if len(r) > 11 else None,
            "warehouse_name": txt(r[12]) if len(r) > 12 else None,
            "source_file": src,
        })
    return rows


def parse_stock(grid: pd.DataFrame, src: str) -> list[dict]:
    # grouped by item. header row 6 positions:
    # 0 Date,1 Voucher,2 RecvQty,3 Rate,4 IssQty,5 Rate,6 BalQty,7 Value,8 Value,9 Value,
    # 10 AvgRate,11 Warehouse,12 ToWarehouse,13 Narration,14 Voucher name
    rows = []
    current_item = None
    for i in range(6, len(grid)):
        r = list(grid.iloc[i])
        first = r[0]
        if is_total(first):
            continue
        if norm_date(first) is not None:  # detail row
            vals = [norm_date(first), txt(r[1]), current_item, *(r[2:15])]
            rows.append({
                "item_name": current_item,
                "move_date": norm_date(first),
                "voucher": txt(r[1]),
                "received_qty": norm_num(r[2]),
                "received_rate_bhd": norm_num(r[3]),
                "issued_qty": norm_num(r[4]),
                "issued_rate_bhd": norm_num(r[5]),
                "balance_qty": norm_num(r[6]),
                "received_value_bhd": norm_num(r[7]),
                "issued_value_bhd": norm_num(r[8]),
                "balance_value_bhd": norm_num(r[9]),
                "avg_rate_bhd": norm_num(r[10]),
                "warehouse_name": txt(r[11]) if len(r) > 11 else None,
                "to_warehouse_name": txt(r[12]) if len(r) > 12 else None,
                "narration": txt(r[13]) if len(r) > 13 else None,
                "voucher_type": (txt(r[14]) if len(r) > 14 else None)
                                or _voucher_prefix(txt(r[1])),
                "row_hash": row_hash(vals),
                "source_file": src,
            })
        elif txt(first) is not None:  # section header = item
            current_item = txt(first)
    return rows


def _voucher_prefix(voucher: str | None) -> str | None:
    if not voucher:
        return None
    head = voucher.split(":")[0].strip().upper()
    return {
        "MRN": "Material Receipt Note",
        "SI": "Sales Invoice",
        "SIO": "Sales Issue",
        "STRV": "Stock Reversal",
        "STN": "Stock Transfer",
        "PHY": "Physical Stock",
    }.get(head, head or None)


def parse_ledger(grid: pd.DataFrame, src: str) -> list[dict]:
    # grouped by account. header row 6:
    # 0 Date,1 Voucher,2 Account,3 Debit,4 Credit,5 Balance,6 Currency,7 Payment Mode,8 Salesman,9 Narration
    rows = []
    current_account = None
    for i in range(6, len(grid)):
        r = list(grid.iloc[i])
        first = r[0]
        if is_total(first):
            continue
        if norm_date(first) is not None:  # detail row
            vals = [norm_date(first), txt(r[1]), txt(r[2]), r[3], r[4], r[5]]
            rows.append({
                "account": current_account,
                "entry_date": norm_date(first),
                "voucher": txt(r[1]),
                "counter_account": txt(r[2]),
                "debit_bhd": norm_num(r[3]),
                "credit_bhd": norm_num(r[4]),
                "balance_bhd": norm_num(r[5]),
                "currency": txt(r[6]) if len(r) > 6 else None,
                "payment_mode": txt(r[7]) if len(r) > 7 else None,
                "salesman": txt(r[8]) if len(r) > 8 else None,
                "narration": txt(r[9]) if len(r) > 9 else None,
                "row_hash": row_hash(vals),
                "source_file": src,
            })
        elif txt(first) is not None:  # section header = account
            current_account = txt(first)
    return rows


def parse_profitability(grid: pd.DataFrame, src: str) -> list[dict]:
    rpt = report_date_from_title(grid)
    rows = []
    for i in range(6, len(grid)):
        r = list(grid.iloc[i])
        name = txt(r[0])
        if name is None or is_total(name):
            continue
        rows.append({
            "item_name": name,
            "report_date": rpt,
            "gross_bhd": norm_num(r[1]),
            "discount_pct": norm_num(r[2]),
            "net_amount_bhd": norm_num(r[3]),
            "cogs_bhd": norm_num(r[4]),
            "gross_profit_bhd": norm_num(r[5]),
            "gp_margin_pct": norm_num(r[6]),
            "misc_charges_bhd": norm_num(r[7]),
            "net_profit_bhd": norm_num(r[8]),
            "np_margin_pct": norm_num(r[9]),
            "source_file": src,
        })
    return rows


def parse_pricebook(grid: pd.DataFrame, src: str, price_book: str) -> list[dict]:
    # header row 1 (index 0). 28 cols; Val 1..Val 13 at indices 13..25.
    rows = []
    for i in range(1, len(grid)):
        r = list(grid.iloc[i])
        if is_total(r[0]) or txt(r[0]) is None:
            continue
        tiers = {f"val_{n}": norm_num(r[12 + n]) for n in range(1, 14) if len(r) > 12 + n}
        rows.append({
            "item_name": txt(r[0]),
            "sku_code": txt(r[1]),
            "customer_name": txt(r[2]),
            "customer_code": txt(r[3]),
            "warehouse_name": txt(r[4]),
            "warehouse_code": txt(r[5]),
            "price_book": price_book,
            "currency": txt(r[6]),
            "start_date": norm_date(r[7]),
            "end_date": norm_date(r[8]),
            "min_qty": norm_num(r[9]),
            "max_qty": norm_num(r[10]),
            "unit_name": txt(r[11]),
            "rate_bhd": norm_num(r[12]),
            "price_tiers": json.dumps(tiers),
            "status": txt(r[26]) if len(r) > 26 else None,
            "narration": txt(r[27]) if len(r) > 27 else None,
            "source_file": src,
        })
    return rows


def _clean_wh(name: str | None) -> str | None:
    """Focus repeats the warehouse name ('Devadas Devadas'); collapse the doubling."""
    if not name:
        return None
    words = name.split()
    n = len(words)
    if n % 2 == 0 and words[: n // 2] == words[n // 2:]:
        return " ".join(words[: n // 2])
    return name.strip()


def parse_stock_balance(grid: pd.DataFrame, src: str) -> list[dict]:
    """Stock balance by warehouse -> current on-hand qty + value per item+warehouse.

    AUTHORITATIVE current stock (Focus's own 'as on date' snapshot). Layout: a
    warehouse header row (item col filled, qty/rate/value empty) followed by its
    item rows. Cols: 0 Particulars(item), 1 Net Quantity, 2 Selling Rate, 3 Total Value.
    """
    as_of = report_date_from_title(grid)
    hrow = None
    for i in range(min(10, len(grid))):
        cells = [str(c).strip().lower() if c is not None else "" for c in grid.iloc[i]]
        if "particulars" in cells:
            hrow = i
            break
    start = (hrow + 1) if hrow is not None else 6
    rows = []
    current_wh = None
    for i in range(start, len(grid)):
        r = list(grid.iloc[i])
        name = txt(r[0])
        if name is None or is_total(name):
            continue
        qty = norm_num(r[1]) if len(r) > 1 else None
        rate = norm_num(r[2]) if len(r) > 2 else None
        val = norm_num(r[3]) if len(r) > 3 else None
        if qty is None and rate is None and val is None:
            current_wh = _clean_wh(name)  # warehouse header row
            continue
        rows.append({
            "item_name": name,
            "warehouse_name": current_wh,
            "net_qty": qty,
            "selling_rate_bhd": rate,
            "total_value_bhd": val,
            "as_of_date": as_of,
            "source_file": src,
        })
    return rows


def parse_receivables(grid: pd.DataFrame, src: str) -> list[dict]:
    """Customer ageing summary -> trade-debtor balances + aging buckets.

    This is the AUTHORITATIVE source of receivables (one row per customer, Focus's
    own AR). The Base-currency block is fixed-position: col 1 = Balance Amount,
    cols 6-13 = the eight aging buckets (0-30 ... >210), col 14 = Total. Account
    Code / Group Name / last-receipt are matched by header name (they sit far right,
    after the Transaction & Local repeats of the same bucket labels).
    """
    as_of = report_date_from_title(grid)
    # locate header row (col0 == 'Account', col1 startswith 'Balance')
    hrow = None
    for i in range(min(12, len(grid))):
        r = list(grid.iloc[i])
        if str(r[0]).strip().lower() == "account" and str(r[1]).strip().lower().startswith("balance"):
            hrow = i
            break
    if hrow is None:
        return []
    header = [str(c).strip() if c is not None else "" for c in grid.iloc[hrow]]

    def col(name: str) -> int | None:
        for j, h in enumerate(header):
            if h.lower() == name.lower():
                return j
        return None

    c_code = col("Account Code")
    c_group = col("Group Name")
    c_last = col("LastReceiptDate")
    rows = []
    for i in range(hrow + 1, len(grid)):
        r = list(grid.iloc[i])
        acct = txt(r[0])
        if acct is None or is_total(acct):
            continue
        bal = norm_num(r[1])
        if bal is None:
            continue
        rows.append({
            "account": acct,
            "account_code": txt(r[c_code]) if c_code is not None and len(r) > c_code else None,
            "group_name": txt(r[c_group]) if c_group is not None and len(r) > c_group else None,
            "balance_bhd": bal,
            "bucket_0_30": norm_num(r[6]),
            "bucket_31_60": norm_num(r[7]),
            "bucket_61_90": norm_num(r[8]),
            "bucket_91_120": norm_num(r[9]),
            "bucket_121_150": norm_num(r[10]),
            "bucket_151_180": norm_num(r[11]),
            "bucket_181_210": norm_num(r[12]),
            "bucket_over_210": norm_num(r[13]),
            "total_bhd": norm_num(r[14]) if len(r) > 14 else bal,
            "last_receipt_date": norm_date(r[c_last]) if c_last is not None and len(r) > c_last else None,
            "as_of_date": as_of,
            "source_file": src,
        })
    return rows


# --------------------------------------------------------------------------- dispatch
def classify(name: str) -> str | None:
    n = name.lower()
    if "summary_sales_register" in n:
        return "orders"
    if "sales_day_book" in n:
        return "order_lines"
    if "stock_balance_by_warehouse" in n:
        return "stock_balance"
    if "stock_ledger" in n:
        return "stock_movements"
    if "ledger_detail" in n:
        return "skip:ledger_detail (subset of Ledger)"
    if "ledger" in n:
        return "ledger_entries"
    if "product_profitability" in n:
        return "product_profitability"
    if "masellingpricebook" in n:
        return "selling_prices:MA_base"
    if "moderntradesellerbook" in n:
        return "selling_prices:modern_trade"
    # Receivables: use ONLY the by-due-date summary (one row/customer + Group Name).
    # The other three ageing exports are skipped to avoid double-counting.
    if "customer_summary_ageing_by_due_date" in n:
        return "receivables"
    if "customer_ageing" in n or "customer_summary_ageing" in n:
        return "skip:duplicate ageing report (using by_due_date summary)"
    return None


def main() -> int:
    # Optional source folder: `python scripts/ingest.py "Focus ERP Updated Reports"`.
    # Defaults to "Focus ERP Data". Reused by the email-to-ingest automation.
    src = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else SRC_DIR
    if not src.is_absolute():
        src = ROOT / src
    if not src.exists():
        print(f"ERROR: source folder not found: {src}")
        return 1
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    tables: dict[str, list[dict]] = {}
    print(f"Ingesting from: {src}\n" + "=" * 70)

    for path in sorted(src.glob("*.xls*")):
        kind = classify(path.name)
        if kind is None:
            print(f"  SKIP (unrecognized): {path.name}  -> stop & ask if this should map")
            continue
        if kind.startswith("skip:"):
            print(f"  SKIP {path.name}  -> {kind[5:]}")
            continue

        grid = read_grid(path)
        if kind == "orders":
            recs = parse_orders(grid, path.name)
        elif kind == "order_lines":
            recs = parse_order_lines(grid, path.name)
        elif kind == "stock_movements":
            recs = parse_stock(grid, path.name)
        elif kind == "ledger_entries":
            recs = parse_ledger(grid, path.name)
        elif kind == "product_profitability":
            recs = parse_profitability(grid, path.name)
        elif kind == "receivables":
            recs = parse_receivables(grid, path.name)
        elif kind == "stock_balance":
            recs = parse_stock_balance(grid, path.name)
        elif kind.startswith("selling_prices:"):
            recs = parse_pricebook(grid, path.name, kind.split(":")[1])
            kind = "selling_prices"
        else:
            continue

        tables.setdefault(kind, []).extend(recs)
        print(f"  OK  {path.name:52} -> {kind:22} {len(recs):6} rows")

    # write CSVs
    print("=" * 70)
    for tbl, recs in tables.items():
        df = pd.DataFrame(recs)
        out = OUT_DIR / f"{tbl}.csv"
        df.to_csv(out, index=False, encoding="utf-8")
        print(f"  wrote {out.relative_to(ROOT)}  ({len(df)} rows)")

    # data-quality: voucher<->invoice join (rule 3 & 4)
    print("=" * 70)
    ok = True
    if "orders" in tables and "order_lines" in tables:
        inv = {r["invoice_no"] for r in tables["orders"] if r["invoice_no"]}
        vou = {r["invoice_no"] for r in tables["order_lines"] if r["invoice_no"]}
        overlap = len(inv & vou)
        denom = max(len(inv), len(vou)) or 1
        pct = overlap / denom
        print(f"  Sales join (Voucher=Invoice, 'SI : N'): {overlap}/{denom} = {pct:.1%}")
        if pct < JOIN_MIN:
            ok = False
            print(f"  !! HARD FAIL: join match {pct:.1%} < {JOIN_MIN:.0%} (data rule 3).")
    else:
        print("  (orders or order_lines missing — skipping join check)")

    print("=" * 70)
    print("  Row summary:")
    for tbl in sorted(tables):
        print(f"    {tbl:24} {len(tables[tbl]):7}")
    print("Done." if ok else "FAILED data-quality gate.")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
