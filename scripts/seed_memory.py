"""Seed the RAG memory (kb_chunks) with a PII-free business glossary / SOP so the assistant can
recall definitions, rules, and decisions — e.g. "what counts as revenue?", "how is low stock
defined?", "how does a stock transfer work?", "where do categories come from?".

Re-runnable: it clears the prior glossary set (meta.source = 'glossary') and re-inserts.

  python -m scripts.seed_memory
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv()

from app.database import get_client  # noqa: E402
from app.knowledge import remember  # noqa: E402

# Public business definitions only (no customer names / PII).
GLOSSARY: list[str] = [
    "Revenue (gross) = COALESCE(total_amount_bhd, gross_bhd), VAT-inclusive. The verified "
    "company total is BHD 51,661.69. Net (ex-VAT) revenue = COALESCE(taxable_bhd, gross_bhd/1.1). "
    "Always report from v_sales.revenue_bhd; show gross by default.",

    "Receivables = the Customer ageing report (view v_receivables), trade debtors only. The "
    "verified total outstanding is BHD 10,495. Overdue = 31+ days. Never sum ledger_entries "
    "balances (that is the whole trial balance, ~208k, and is wrong).",

    "Stock value = the Stock_balance_by_warehouse snapshot (v_current_stock) valued at selling "
    "rate. Verified total BHD 126,825 across ~42,104 units / 131 items. Do NOT derive stock from "
    "the stock ledger (it over-counts ~8.6x).",

    "Below cost = v_product_margin where gp_margin_pct < 0 (Focus's own gross-margin %). Do NOT "
    "compare per-unit list price to cogs_bhd (cogs_bhd is cumulative period COGS, not unit cost).",

    "Low stock = v_stock_health status in ('urgent_out_of_stock','low_stock'); days_cover = "
    "current stock / (sold_90d / 90); alert when days_cover < 30. A fast mover at zero stock is "
    "urgent (lost sales).",

    "Channel: B2C / retail = the warehouse/salesman is Causeway or YQ Roadshow; B2B / wholesale = "
    "every other salesman route. 'Cash Customer' is the walk-in bucket (~28% of sales) — exclude "
    "it from named top-customer and churn analysis.",

    "Warehouse model: each salesman IS a warehouse. The central 'Accessories Warehouse' is the hub; "
    "stock is ISSUED to a salesman's van warehouse, then drawn down by his sales. 'Accessories "
    "Damage Warehouse' holds damaged stock; 'Sim'/'SIM Warehouse' is the telecom hub.",

    "Stock-transfer flow in Focus: Stock Request Voucher -> Stock Issue Voucher (issues from a "
    "warehouse to a destination) -> Stock Receive Voucher -> Stock Transfer (a request is "
    "optional). v_stock_transfers exposes from->to moves; v_salesman_stock_recon reconciles stock "
    "issued to each salesman vs sold + on-hand and flags counted shortages and stock-to-damage.",

    "Product categories come from Focus's own item-groups in the Multi_level_stock_movement report "
    "(Cable, Charger, Power Bank, Headphones, Wireless HFs, Car Charger, Car Accessories, Wireless "
    "Speaker, Sim, Batelco TRA Devices, Postpaid Giveaway, Miscellaneous). Coarse division: "
    "Accessories vs Telecom. Use v_sales_by_category.",

    "Data freshness: there is no Focus API. Data comes from Focus Excel exports uploaded on the "
    "Data page. Daily reports: Sales_day_book, Summary_sales_register, Stock_balance_by_warehouse, "
    "Stock_ledger, Customer_summary_ageing_by_due_date, Product_Profitability_Report. Weekly/"
    "occasional: MASellingPriceBook, ModernTradeSellerBook, Multi_level_stock_movement (categories). "
    "Every upload is verified against the reports before it goes live.",

    "Vendor: VFAN is currently the sole supplier; the vendor is embedded as a '(VFAN)' tag inside "
    "item names. Cost source of truth for pricing is purchase_costs.landed_cost_bhd, never the ERP "
    "stock valuation rate.",

    "Time windows anchor to MAX(line_date) — the data's latest day — not CURRENT_DATE, because the "
    "server clock can run ahead of the last loaded data. 'This month' = since DATE_TRUNC('month', "
    "MAX(sale_date)).",
]


def main() -> int:
    c = get_client()
    try:  # clear the prior glossary so re-running never duplicates
        c.table("kb_chunks").delete().eq("meta->>source", "glossary").execute()
    except Exception as e:  # noqa: BLE001
        print(f"(could not clear old glossary: {e})")
    n = sum(1 for text in GLOSSARY if remember(text, kind="knowledge", meta={"source": "glossary"}))
    print(f"Seeded {n}/{len(GLOSSARY)} glossary chunks into kb_chunks.")
    return 0 if n else 1


if __name__ == "__main__":
    raise SystemExit(main())
