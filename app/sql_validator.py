"""SQL guardrails for the /ask endpoint.

Validates LLM-generated SQL before execution. Rules:
  1. Single SELECT statement only (a set operation of SELECTs counts as one).
  2. No DML / DDL / dangerous keywords or functions; no SELECT … INTO, no row locks.
  3. Must reference only the approved view allowlist — EVERY relation the statement touches,
     including subqueries, CTE bodies, joins, comma joins, IN/EXISTS/scalar subqueries and
     lateral joins. Quoted identifiers, schema-qualified names and CTEs named after an
     allowlisted view are rejected outright (each was a way past the old `FROM \\w+` scan).
  4. LIMIT injected at MAX_ROWS if missing, clamped if larger — on the PARSE TREE, so a limit
     hidden in a comment, a subquery's inner limit or a non-literal expression cannot dodge the
     cap; the returned SQL is regenerated from the tree.

The relation walk is sqlglot's Postgres parser (R1 security S7, 24-Sep-2026): a query that
does not parse is rejected, never guessed at. Every CTE body must itself be a SELECT (or a set
operation of SELECTs): `WITH x AS (TABLE t)` parses without an exp.Table node and is refused.
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
    # the bare TABLE command (`TABLE t` / `WITH x AS (TABLE t)`) reads a relation without a
    # FROM, which the relation walk cannot see — a data answer never needs it
    r"|table"
    r"|copy|pg_read_file|dblink|pg_exec|execute|perform"
    # functions that run SQL, read files, sleep or signal — none has a place in a data answer
    r"|query_to_xml|query_to_xml_and_xmlschema|table_to_xml|schema_to_xml|database_to_xml|cursor_to_xml"
    r"|pg_read_binary_file|pg_ls_dir|pg_stat_file|lo_import|lo_export|pg_sleep|pg_sleep_for|pg_sleep_until"
    r"|pg_terminate_backend|pg_cancel_backend|pg_notify|pg_advisory_lock|pg_advisory_xact_lock|set_config)\b",
    re.IGNORECASE,
)
_SEMICOLON_MID = re.compile(r";(?!\s*$)")
# E'…' / e'…' escape strings (also U&'…'), $$ / $tag$ dollar quoting, and any backslash.
_UNSAFE_LEXEMES = re.compile(r"(?i)(?<![a-z0-9_])(e|u&)'|\$[a-z_0-9]*\$|\\")
_WHOLE_NUMBER = re.compile(r"^\d+$")

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
        if not isinstance(cte.this, _READ_ONLY_ROOTS):
            # `WITH x AS (TABLE customer_contacts)` (MATERIALIZED or not) parses as a Column
            # named TABLE aliased to the relation — no exp.Table, nothing for the allowlist to
            # see. Only a SELECT / set-operation body can be reasoned about.
            raise SQLValidationError("Every CTE must be a SELECT.")
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

    # validate() returns sqlglot's re-rendering of the tree, so the text that runs must mean
    # exactly what was checked. Backslash escapes (E'…'), dollar quoting and bare backslashes are
    # where a dialect round-trip can move a string boundary; a data question never needs them.
    if _UNSAFE_LEXEMES.search(sql):
        raise SQLValidationError("Escape strings and dollar quoting are not allowed.")

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

    _cap_rows(tree)
    out = tree.sql(dialect="postgres")
    # Defence in depth: the SQL that will run is parsed again and must read exactly the same
    # relations through the same read-only root as the tree that was checked.
    again = _parse(out)
    if not isinstance(again, _READ_ONLY_ROOTS) or referenced_relations(again) != refs \
            or _UNSAFE_LEXEMES.search(out):
        log.warning("SQL rejected — rendering changed the statement's meaning")
        raise SQLValidationError("The query could not be validated.")
    return out


def _literal_rows(node: exp.Expression | None) -> int:
    """The row count of a LIMIT / FETCH operand, which must be a plain whole number. Anything
    else — a subquery, `5 + 5`, a string, a negative, a parameter — is refused: the cap is
    only enforceable on a value the validator can read."""
    if isinstance(node, exp.Literal) and not node.is_string and _WHOLE_NUMBER.match(str(node.this)):
        return int(node.this)
    raise SQLValidationError("LIMIT must be a plain whole number.")


def _cap_rows(tree: exp.Expression) -> None:
    """Enforce the hard row cap on the OUTER statement, in place on the parse tree. Inject
    LIMIT when absent (sqlglot already drops `LIMIT ALL`); clamp a literal larger than
    MAX_ROWS. Inner limits are left alone — the outer cap bounds the result either way."""
    top = tree.args.get("limit")
    if top is None:
        tree.set("limit", exp.Limit(expression=exp.Literal.number(MAX_ROWS)))
        return
    if isinstance(top, exp.Limit):
        if _literal_rows(top.expression) > MAX_ROWS:
            top.set("expression", exp.Literal.number(MAX_ROWS))
        return
    if isinstance(top, exp.Fetch):
        opts = top.args.get("limit_options")
        if opts is not None and opts.args.get("percent"):
            raise SQLValidationError("FETCH ... PERCENT is not allowed.")
        if _literal_rows(top.args.get("count")) > MAX_ROWS:
            top.set("count", exp.Literal.number(MAX_ROWS))
        return
    raise SQLValidationError("LIMIT must be a plain whole number.")
