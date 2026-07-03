"""SQL guardrails for the /ask endpoint.

Validates LLM-generated SQL before execution. Rules:
  1. Single SELECT statement only.
  2. No DML / DDL / dangerous keywords.
  3. Must reference only the approved view allowlist.
  4. LIMIT injected at MAX_ROWS if missing.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

# LLM may only query these views — never raw tables.
VIEW_ALLOWLIST: frozenset[str] = frozenset({
    "v_sales",
    "v_current_stock",
    "v_product_margin",
    "v_receivables",
    "v_top_customers",
    "v_sales_by_period",
    "v_low_stock",
    "v_stock_health",
    "v_item_velocity",
    "v_sales_by_salesman",
    "v_sales_by_channel",
    "v_sales_by_category",
    "v_inventory_aging",
    "v_price_list",
    "v_price_list_by_book",
    "v_product_economics",
    "v_price_history",
    "v_purchase_history",
    "v_cost_change",
    "v_price_change",
    "v_stock_transfers",
    "v_salesman_stock_recon",
    "v_po_price_history",
    "v_po_cost_change",
    "v_purchase_lifecycle",
    "v_margin_leakage",
    "v_sales_by_month_channel",
    "v_basket_affinity",
    "v_vendor_scorecard",
    "v_landed_margin",
    "mrn_lines",
    "v_supplier_price_history",
    "supplier_prices",
    "shipments",
    # Phase C additions
    "v_returns",
    "v_return_rates",
    "v_customer_ltv",
    # v3: divisions, cash/credit, catalog & leads
    "v_sales_by_payment",
    "v_sales_by_division",
    "v_catalog",
    "leads",
})

# Which feature page gates each PO view (procurement → Inventory).

# Which feature page "owns" each view — used to feature-scope free-text data queries so a member
# can't pull data outside their granted pages (admins/agent callers pass allowed_features=None).
VIEW_FEATURE: dict[str, str] = {
    "v_sales": "Sales", "v_sales_by_period": "Sales", "v_sales_by_salesman": "Sales",
    "v_sales_by_channel": "Sales", "v_sales_by_category": "Sales", "v_top_customers": "Sales",
    "v_price_list": "Sales", "v_price_list_by_book": "Sales", "v_price_history": "Sales",
    "v_price_change": "Sales", "v_sales_by_month_channel": "Sales", "v_basket_affinity": "Sales",
    "v_current_stock": "Inventory", "v_low_stock": "Inventory", "v_stock_health": "Inventory",
    "v_item_velocity": "Inventory", "v_inventory_aging": "Inventory", "v_stock_transfers": "Inventory",
    "v_salesman_stock_recon": "Inventory", "v_purchase_history": "Inventory",
    "v_cost_change": "Inventory", "shipments": "Inventory",
    "v_po_price_history": "Inventory", "v_po_cost_change": "Inventory", "v_purchase_lifecycle": "Inventory",
    "v_vendor_scorecard": "Inventory", "mrn_lines": "Inventory",
    "v_product_margin": "Margins", "v_product_economics": "Margins", "v_margin_leakage": "Margins",
    "v_landed_margin": "Margins",
    "v_supplier_price_history": "Inventory", "supplier_prices": "Inventory",
    "v_returns": "Inventory", "v_return_rates": "Inventory",
    "v_customer_ltv": "Sales",
    "v_receivables": "Receivables",
    "v_sales_by_payment": "Sales", "v_sales_by_division": "Sales",
    "v_catalog": "Catalog", "leads": "Leads",
}

MAX_ROWS = 200

_BANNED = re.compile(
    r"\b(insert|update|delete|truncate|drop|create|alter|grant|revoke"
    r"|copy|pg_read_file|dblink|pg_exec|execute|perform)\b",
    re.IGNORECASE,
)
_LIMIT = re.compile(r"\bLIMIT\s+(\d+)", re.IGNORECASE)
_SEMICOLON_MID = re.compile(r";(?!\s*$)")
_TABLE_REF = re.compile(r"\bFROM\s+(\w+)|\bJOIN\s+(\w+)", re.IGNORECASE)


class SQLValidationError(ValueError):
    pass


class FeatureAccessError(SQLValidationError):
    """The SQL is valid but references data the caller's feature pages don't cover."""


def validate(sql: str, allowed_features: set[str] | None = None) -> str:
    """Validate and return cleaned SQL (LIMIT injected if missing).

    Raises SQLValidationError with a safe message on any violation.
    Never raises on valid SELECT-only queries against the view allowlist.

    `allowed_features` feature-scopes the query: if given (a non-admin caller), every referenced
    view's owning feature must be in the set, else FeatureAccessError. None = unrestricted
    (admins / trusted agent-key callers).
    """
    sql = sql.strip().rstrip(";")

    if not sql.upper().lstrip().startswith("SELECT"):
        raise SQLValidationError("Only SELECT statements are allowed.")

    if _BANNED.search(sql):
        raise SQLValidationError("SQL contains a disallowed keyword.")

    if _SEMICOLON_MID.search(sql):
        raise SQLValidationError("Only a single SQL statement is allowed.")

    refs = {m.group(1) or m.group(2) for m in _TABLE_REF.finditer(sql)}
    bad = {r for r in refs if r and r.lower() not in VIEW_ALLOWLIST}
    if bad:
        # Log the specifics server-side; never echo the allowlist to the caller.
        log.info("SQL rejected — non-allowlisted refs: %s", ", ".join(sorted(bad)))
        raise SQLValidationError("Query references data outside the allowed views.")

    if allowed_features is not None:
        denied = sorted({VIEW_FEATURE[r.lower()] for r in refs
                         if r and r.lower() in VIEW_FEATURE and VIEW_FEATURE[r.lower()] not in allowed_features})
        if denied:
            raise FeatureAccessError(
                f"This question needs access you don't have: {', '.join(denied)}. Ask an admin to grant it."
            )

    # Enforce a hard row cap. Inject LIMIT if absent; clamp it down if the LLM supplied a
    # larger one (a bare "skip if present" check let `LIMIT 1000000` through).
    m = _LIMIT.search(sql)
    if not m:
        sql = f"{sql} LIMIT {MAX_ROWS}"
    else:
        try:
            n = int(m.group(1))
        except (TypeError, ValueError):
            n = MAX_ROWS + 1
        if n > MAX_ROWS:
            sql = sql[:m.start()] + f"LIMIT {MAX_ROWS}" + sql[m.end():]

    return sql
