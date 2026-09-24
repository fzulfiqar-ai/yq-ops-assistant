"""SQL guardrails for the /ask endpoint.

Validates LLM-generated SQL before execution. Rules:
  1. Single SELECT statement only (a set operation of SELECTs counts as one).
  2. No DML / DDL / dangerous keywords or functions; no SELECT … INTO, no row locks.
  3. Must reference only the approved view allowlist — EVERY relation the statement touches,
     including subqueries, CTE bodies, joins, comma joins, IN/EXISTS/scalar subqueries and
     lateral joins. Quoted identifiers, schema-qualified names and CTEs named after an
     allowlisted view are rejected outright (each was a way past the old `FROM \\w+` scan).
  4. LIMIT injected at MAX_ROWS if missing, clamped if larger.

The relation walk is sqlglot's Postgres parser (R1 security S7, 24-Sep-2026): a query that
does not parse is rejected, never guessed at.
"""
from __future__ import annotations

import logging
import re

import sqlglot
from sqlglot import exp

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
    "v_catalog_stock",
    "v_catalog_velocity",
    "v_catalog_pairs",
    "v_shop_unpriced_stock",
    "v_shop_orders_agent",
    "v_shop_order_lines_agent",
    "leads",
    "v_price_tracker",
    # Division split (Accessories vs SIM) — see scripts/division_split_migration.sql
    "v_division_summary",
    "v_item_division",
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
    "v_catalog": "Catalog", "v_catalog_stock": "Catalog", "v_catalog_velocity": "Catalog",
    "v_catalog_pairs": "Catalog", "v_shop_unpriced_stock": "Shop Admin",
    "v_shop_orders_agent": "Shop Orders", "v_shop_order_lines_agent": "Shop Orders", "leads": "Leads", "v_price_tracker": "Margins",
    "v_division_summary": "Inventory", "v_item_division": "Inventory",
}

MAX_ROWS = 200

_BANNED = re.compile(
    r"\b(insert|update|delete|truncate|drop|create|alter|grant|revoke"
    r"|copy|pg_read_file|dblink|pg_exec|execute|perform"
    # functions that run SQL, read files, sleep or signal — none has a place in a data answer
    r"|query_to_xml|query_to_xml_and_xmlschema|table_to_xml|schema_to_xml|database_to_xml|cursor_to_xml"
    r"|pg_read_binary_file|pg_ls_dir|pg_stat_file|lo_import|lo_export|pg_sleep|pg_sleep_for|pg_sleep_until"
    r"|pg_terminate_backend|pg_cancel_backend|pg_notify|pg_advisory_lock|pg_advisory_xact_lock|set_config)\b",
    re.IGNORECASE,
)
_LIMIT = re.compile(r"\bLIMIT\s+(\d+)", re.IGNORECASE)
_LIMIT_ALL = re.compile(r"\bLIMIT\s+ALL\b", re.IGNORECASE)
_FETCH = re.compile(r"\bFETCH\s+(?:FIRST|NEXT)\s+(\d+)", re.IGNORECASE)
_SEMICOLON_MID = re.compile(r";(?!\s*$)")

# A set operation of SELECTs is still one read-only statement.
_SET_OPS: tuple[type, ...] = tuple(
    c for c in (getattr(exp, n, None) for n in ("Union", "Intersect", "Except")) if c is not None)
_READ_ONLY_ROOTS: tuple[type, ...] = (exp.Select, *_SET_OPS)


class SQLValidationError(ValueError):
    pass


class FeatureAccessError(SQLValidationError):
    """The SQL is valid but references data the caller's feature pages don't cover."""


def _parse(sql: str) -> exp.Expression:
    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except Exception as e:  # noqa: BLE001 — ParseError, TokenError, …
        log.info("SQL rejected — unparseable: %s", str(e)[:200])
        raise SQLValidationError("The query could not be parsed.") from e
    if len(statements) != 1:
        raise SQLValidationError("Only a single SQL statement is allowed.")
    return statements[0]


def referenced_relations(tree: exp.Expression) -> set[str]:
    """Every relation name the statement reads, lower-cased, across every scope — CTE
    references excluded (their bodies are walked like everything else). Raises on the
    constructs the allowlist cannot reason about."""
    ctes: set[str] = set()
    for cte in tree.find_all(exp.CTE):
        name = str(cte.alias_or_name or "").lower()
        alias_ident = cte.args.get("alias")
        ident = alias_ident.this if isinstance(alias_ident, exp.TableAlias) else None
        if isinstance(ident, exp.Identifier) and ident.quoted:
            raise SQLValidationError("Quoted identifiers are not allowed.")
        if not name:
            raise SQLValidationError("Every CTE needs a plain name.")
        if name in VIEW_ALLOWLIST:
            # WITH v_sales AS (SELECT * FROM secret) SELECT * FROM v_sales — the outer reference
            # would look allowlisted while reading whatever the CTE body reads.
            raise SQLValidationError("Query references data outside the allowed views.")
        ctes.add(name)

    refs: set[str] = set()
    for table in tree.find_all(exp.Table):
        if table.db or table.catalog:
            raise SQLValidationError("Schema-qualified names are not allowed.")
        ident = table.this
        if not isinstance(ident, exp.Identifier):
            # a table-valued function (generate_series, jsonb_each …): no relation is read,
            # and the dangerous functions are already banned by name
            continue
        if ident.quoted:
            raise SQLValidationError("Quoted identifiers are not allowed.")
        name = ident.name.lower()
        if name in ctes:
            continue
        refs.add(name)
    return refs


def validate(sql: str, allowed_features: set[str] | None = None) -> str:
    """Validate and return cleaned SQL (LIMIT injected if missing).

    Raises SQLValidationError with a safe message on any violation.
    Never raises on valid SELECT-only queries against the view allowlist.

    `allowed_features` feature-scopes the query: if given (a non-admin caller), every referenced
    view's owning feature must be in the set, else FeatureAccessError. None = unrestricted
    (admins / trusted agent-key callers).
    """
    sql = sql.strip().rstrip(";")

    if not sql.upper().lstrip().startswith(("SELECT", "WITH")):
        raise SQLValidationError("Only SELECT statements are allowed.")

    if _BANNED.search(sql):
        raise SQLValidationError("SQL contains a disallowed keyword.")

    if _SEMICOLON_MID.search(sql):
        raise SQLValidationError("Only a single SQL statement is allowed.")

    tree = _parse(sql)
    if not isinstance(tree, _READ_ONLY_ROOTS):
        raise SQLValidationError("Only SELECT statements are allowed.")
    if tree.find(exp.Into) is not None:
        raise SQLValidationError("SELECT INTO is not allowed.")
    if tree.find(exp.Lock) is not None:
        raise SQLValidationError("Row locks are not allowed.")

    refs = referenced_relations(tree)
    bad = {r for r in refs if r not in VIEW_ALLOWLIST}
    if bad:
        # Log the specifics server-side; never echo the allowlist to the caller.
        log.info("SQL rejected — non-allowlisted refs: %s", ", ".join(sorted(bad)))
        raise SQLValidationError("Query references data outside the allowed views.")

    if allowed_features is not None:
        denied = sorted({VIEW_FEATURE[r] for r in refs
                         if r in VIEW_FEATURE and VIEW_FEATURE[r] not in allowed_features})
        if denied:
            raise FeatureAccessError(
                f"This question needs access you don't have: {', '.join(denied)}. Ask an admin to grant it."
            )

    # Enforce a hard row cap on the OUTER statement. Inject LIMIT if absent; clamp it down if
    # the LLM supplied a larger one (a bare "skip if present" check let `LIMIT 1000000` through).
    # The parser says whether the top level is limited; the textual edit uses the LAST LIMIT in
    # the query, which is the outer one whenever the statement has one.
    top_limit = tree.args.get("limit")
    if isinstance(top_limit, exp.Fetch):
        m = _FETCH.search(sql)
        if m and int(m.group(1)) > MAX_ROWS:
            sql = sql[:m.start()] + f"FETCH FIRST {MAX_ROWS}" + sql[m.end():]
    elif top_limit is None:
        m = _LIMIT_ALL.search(sql)
        sql = (sql[:m.start()] + f"LIMIT {MAX_ROWS}" + sql[m.end():]) if m else f"{sql} LIMIT {MAX_ROWS}"
    else:
        matches = list(_LIMIT.finditer(sql))
        if matches:
            m = matches[-1]
            try:
                n = int(m.group(1))
            except (TypeError, ValueError):
                n = MAX_ROWS + 1
            if n > MAX_ROWS:
                sql = sql[:m.start()] + f"LIMIT {MAX_ROWS}" + sql[m.end():]
        else:
            sql = f"{sql} LIMIT {MAX_ROWS}"     # a limit the regex cannot see (e.g. an expression)

    return sql
