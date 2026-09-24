"""Transactional batch importer for the Focus exports (release R2a, 24-Sep-2026; plan section 9
step 2 items 1 and 3; audit A3, A5, A13).

The old path (POST /ingest -> scripts.refresh) parses, upserts straight into the live tables and
verifies afterwards. This module keeps the same parsers (scripts.ingest) and the same per-table
loader rules (scripts.load_supabase: dedupe keys, snapshot aggregation, the Focus price-book
snapshot rule) but changes WHEN the database is touched:

    parse_folder()  -> rows per target, prepared exactly as the loader would upsert them
    stage           -> ingest_stage (PostgREST from the API; a TEMP table from the read-only CLI)
    build_preview() -> per target: insert / update / unchanged / would-remove, file totals vs the
                       database, per-day and per-salesman sales, price changes, stock deltas,
                       unaliased items, unclassified divisions, MRN coverage, cost coverage,
                       plus an exception list (blocking ones must be acknowledged)
    commit_batch()  -> ingest_commit(batch, expected) in ONE database transaction; the RPC
                       re-asserts every count and money sum the preview promised and raises
                       (full rollback) on any mismatch
    undo_batch()    -> ingest_undo(batch): the batch's inserts go, its replaced rows come back

Every database conversation is a psycopg session (PgBackend): the read-only CLI hands the parsed
rows to the diff as one jsonb parameter, a local replay cluster uses the real ingest_* tables,
and the API uses DirectBackend -- its OWN session on DATABASE_URL (the Supabase session pooler)
with a statement timeout the commit can meet. Nothing goes through PostgREST: its RPCs run under
the authenticator role's 8 s statement_timeout (read on production 24-Sep-2026) and a commit
measured ~10 s on the local replay; and the diff does not need yq_readonly (the role behind the
AI's SQL allowlist) to be widened onto the raw Focus tables. One diff SQL (diff_sql) serves every
session, so the preview is one implementation. The per-target rules (TARGETS) mirror
_ingest_target_cfg() in scripts/ingest_batches_migration.sql; the R2 tests assert the two agree.

Money is Decimal throughout (3 dp, ROUND_HALF_UP). Row values keep the parser's numbers; sums
are never computed in float. Nothing here deletes a batch or a replaced row; staged rows are
pruned by the database functions once they can no longer matter (see ingest_prune()).
"""
from __future__ import annotations

import csv
import json
import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PREVIEW_DIR = ROOT / "business_data" / "ingest_previews"   # gitignored (business_data/)
CHUNK = 1000
THREE = Decimal("0.001")
JOIN_MIN = 0.80                    # the sales voucher<->invoice join gate (scripts/ingest.py)
UNALIASED_ALERT_SHARE = 0.02       # plan: unmapped-share alert above 2 % of the file's gross
SNAPSHOT_MAX_STALE_SHARE = 0.10    # scripts/load_supabase.py mass-void guard
SNAPSHOT_MIN_SKU_COVER = 0.90
REMOVED_MAX_SHARE = 0.05           # a span replace that removes more than this share of the rows in scope
                                   # (or more rows than it inserts) is BLOCKING, not a warning
STAGE_WARN_MB = 100                # ingest_stage + ingest_replaced above this: warn (free tier caps the DB at 500 MB)
DEFAULT_STATEMENT_TIMEOUT_S = 600  # the API's own session (INGEST_STATEMENT_TIMEOUT_S); PostgREST's RPCs get 8 s
SAMPLE = 50
# Exceptions an admin cannot acknowledge away: the preview itself is unreliable, or the owner's
# data rule 3 (a voucher<->invoice join under 80 % is a HARD FAIL in scripts/ingest.py) applies.
HARD_BLOCKING = frozenset({"db_diff_failed", "empty_report", "snapshot_without_date", "sales_join_below_80", "check_failed"})

# Mirrors _ingest_target_cfg() in scripts/ingest_batches_migration.sql (tested for equality).
#   keys   natural key = the old loader's upsert key
#   scope  what one export is the whole truth for: an invoice/move date span, a snapshot
#          partition, or (price books) the whole Focus book
#   within for a span: only the values of these (text) columns that the file carries are in
#          scope -- a per-account Ledger export or a Stock_ledger filtered to one item must
#          never delete everyone else's rows in its date span
#   money  the sums asserted inside ingest_commit after the load
TARGETS: dict[str, dict[str, Any]] = {
    "orders": {"keys": ["invoice_no"], "scope": "span", "span_col": "order_date",
               "money": ["gross_bhd"], "report": "Summary_sales_register"},
    "order_lines": {"keys": ["invoice_no", "line_no"], "scope": "span", "span_col": "line_date",
                    "money": ["gross_bhd", "taxable_bhd"], "report": "Sales_day_book"},
    "stock_movements": {"keys": ["voucher", "item_name", "row_hash"], "scope": "span", "span_col": "move_date",
                        "within": ["item_name"], "money": ["received_qty", "issued_qty"], "report": "Stock_ledger"},
    "ledger_entries": {"keys": ["account", "voucher", "row_hash"], "scope": "span", "span_col": "entry_date",
                       "within": ["account"], "money": ["debit_bhd", "credit_bhd"], "report": "Ledger"},
    "stock_balance": {"keys": ["item_name", "warehouse_name", "as_of_date"], "scope": "partition",
                      "partition": ["as_of_date", "warehouse_name"], "money": ["net_qty", "total_value_bhd"],
                      "report": "Stock_balance_by_warehouse"},
    "ar_ageing": {"keys": ["account", "as_of_date"], "scope": "partition", "partition": ["as_of_date"],
                  "money": ["balance_bhd"], "report": "Customer_summary_ageing_by_due_date"},
    "product_profitability": {"keys": ["item_name", "report_date"], "scope": "partition", "partition": ["report_date"],
                              "money": ["gross_bhd", "net_amount_bhd"], "report": "Product_Profitability_Report"},
    "selling_prices": {"keys": ["sku_code", "price_book", "customer_code", "warehouse_name", "start_date"],
                       "scope": "book", "partition": ["price_book"], "money": ["rate_bhd"], "report": "Price book"},
}
# Columns the importer never writes (backfills and the snapshot-rule columns it only touches by rule).
PROTECTED = {"id", "imported_at", "created_at", "updated_at", "product_id", "order_id", "customer_id",
             "voided_at", "void_reason"}
# ingest.classify() kind -> target table
KIND_TO_TARGET = {"orders": "orders", "order_lines": "order_lines", "stock_movements": "stock_movements",
                  "ledger_entries": "ledger_entries", "product_profitability": "product_profitability",
                  "receivables": "ar_ageing", "stock_balance": "stock_balance"}
FOCUS_BOOK_STEMS = {"MA_base": "masellingpricebook", "modern_trade": "moderntradesellerbook"}
BLOCKING, WARNING, INFO = "blocking", "warning", "info"


# ── money ───────────────────────────────────────────────────────────────────────
def dec(v: Any) -> Decimal | None:
    """Exact Decimal of a parsed value; None for blanks/NaN. Floats go through str() (the shortest
    repr), which is the same text the JSON payload carries, so file and database agree digit for digit."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, Decimal):
        return v
    if isinstance(v, float):
        return None if math.isnan(v) else Decimal(str(v))
    if isinstance(v, int):
        return Decimal(v)
    s = str(v).strip()
    if not s or s.lower() in ("nan", "none"):
        return None
    try:
        return Decimal(s)
    except Exception:  # noqa: BLE001
        return None


def money(d: Decimal | None) -> str:
    return str((d or Decimal(0)).quantize(THREE, rounding=ROUND_HALF_UP))


def _jsonable(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(str(v))          # a JSON number with the same digits (no arithmetic happened)
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


def _pg_array(vals: list[str]) -> str:
    """A Postgres text[] literal (params reach the read-only RPC as text)."""
    def q(s: str) -> str:
        return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'
    return "{" + ",".join(q(v) for v in vals) + "}"


# ── 1. parse a drop folder with the Phase-0 parsers ─────────────────────────────
@dataclass
class Parsed:
    folder: str
    files: list[dict] = field(default_factory=list)       # [{file, target, report, rows, raw_rows}]
    ignored: list[dict] = field(default_factory=list)     # [{file, reason}]
    rows: dict[str, list[dict]] = field(default_factory=dict)   # target -> prepared rows
    notes: dict[str, dict] = field(default_factory=dict)  # target -> prepare() notes
    as_on: dict[str, str | None] = field(default_factory=dict)  # target -> 'as on date' from the title
    ar_grand_total: Decimal | None = None
    mrn_xml: list[dict] = field(default_factory=list)     # [{file, doc_no, lines}]
    export_dates: dict[str, str] = field(default_factory=dict)  # price book -> export day


def _grand_total(grid) -> Decimal | None:
    """Focus's own 'Grand Total' balance on the ageing report (the sum of rows loses credit signs)."""
    for i in range(len(grid)):
        c = grid.iat[i, 0]
        if isinstance(c, str) and "grand total" in c.lower():
            return dec(grid.iat[i, 1]) if grid.shape[1] > 1 else None
    return None


def parse_folder(folder: str | Path, row_hook=None) -> Parsed:
    """Every recognised Focus export in `folder`, newest of each report type (the rule the old
    ingest applies), parsed and prepared per target. Read-only: the folder is never written.
    row_hook(target, row) -> row, when given, is applied to every prepared row (the local replay
    uses it to mask merchant names the same way its copy of the database is masked)."""
    from scripts import ingest as ing
    src = Path(folder)
    out = Parsed(folder=str(src))
    if not src.is_dir():
        raise FileNotFoundError(f"drop folder not found: {src}")
    candidates: dict[str, Path] = {}
    for path in sorted(src.iterdir()):
        if not path.is_file() or path.name.startswith("~$"):
            continue
        if path.suffix.lower() not in (".xlsx", ".xls"):
            if not (path.suffix.lower() == ".xml" and path.name.startswith("Transactions_")):
                out.ignored.append({"file": path.name, "reason": "not an Excel export: the batch importer reads the Focus "
                                    ".xlsx/.xls reports only (a .csv goes through the default Upload & refresh)"})
            continue
        kind = ing.classify(path.name)
        if kind is None:
            out.ignored.append({"file": path.name, "reason": "not a recognised Focus report"})
            continue
        if kind.startswith("skip:"):
            out.ignored.append({"file": path.name, "reason": kind[5:]})
            continue
        if kind == "categories":
            out.ignored.append({"file": path.name, "reason": "product categories (category_backfill, not a batch target)"})
            continue
        if not kind.startswith("selling_prices") and not ing.sniff_focus(path):
            out.ignored.append({"file": path.name, "reason": "does not look like a Focus export (no 'YQ Bahrain' title)"})
            continue
        prev = candidates.get(kind)
        if prev is None or path.stat().st_mtime > prev.stat().st_mtime:
            if prev is not None:
                out.ignored.append({"file": prev.name, "reason": f"older export of the same report; {path.name} is newer"})
            candidates[kind] = path
        else:
            out.ignored.append({"file": path.name, "reason": f"older export of the same report; {prev.name} is newer"})

    raw: dict[str, list[dict]] = {}
    for kind, path in candidates.items():
        grid = ing.read_grid(path)
        if kind == "orders":
            recs = ing.parse_orders(grid, path.name)
        elif kind == "order_lines":
            recs = ing.parse_order_lines(grid, path.name)
        elif kind == "stock_movements":
            recs = ing.parse_stock(grid, path.name)
        elif kind == "ledger_entries":
            recs = ing.parse_ledger(grid, path.name)
        elif kind == "product_profitability":
            recs = ing.parse_profitability(grid, path.name)
            out.as_on["product_profitability"] = ing.report_date_from_title(grid)
        elif kind == "receivables":
            recs = ing.parse_receivables(grid, path.name)
            out.as_on["ar_ageing"] = ing.report_date_from_title(grid)
            out.ar_grand_total = _grand_total(grid)
        elif kind == "stock_balance":
            recs = ing.parse_stock_balance(grid, path.name)
            out.as_on["stock_balance"] = ing.report_date_from_title(grid)
        elif kind.startswith("selling_prices:"):
            book = kind.split(":", 1)[1]
            recs = ing.parse_pricebook(grid, path.name, book)
            try:
                from scripts.verify_numbers import export_date
                out.export_dates[book] = export_date(path)
            except Exception:  # noqa: BLE001
                out.export_dates[book] = date.fromtimestamp(path.stat().st_mtime).isoformat()
            kind = "selling_prices"
        else:
            continue
        target = KIND_TO_TARGET.get(kind, kind)
        raw.setdefault(target, []).extend(recs)
        out.files.append({"file": path.name, "target": target, "report": TARGETS[target]["report"], "raw_rows": len(recs)})

    for target, recs in raw.items():
        rows, notes = prepare(target, recs)
        if row_hook is not None:
            rows = [row_hook(target, r) for r in rows]
        out.rows[target] = rows
        out.notes[target] = notes
        for f in out.files:
            if f["target"] == target:
                f["rows"] = len(rows)

    # MRN XML dropped beside the reports: reported, never imported by a batch
    for xml in sorted(list(src.glob("Transactions_*.xml")) + list(src.glob("*/Transactions_*.xml"))):
        try:
            from scripts.ingest_mrn import parse_mrn
            lines = parse_mrn(str(xml))
            docs = sorted({ln.get("doc_no") for ln in lines if ln.get("doc_no")})
            out.mrn_xml.append({"file": str(xml.relative_to(src)), "doc_nos": docs, "lines": len(lines)})
        except Exception as e:  # noqa: BLE001
            out.mrn_xml.append({"file": str(xml.relative_to(src)), "doc_nos": [], "lines": 0, "error": str(e)[:120]})
    return out


# ── 2. prepare rows exactly as scripts/load_supabase.py upserts them ────────────
def _key(row: dict, keys: list[str]) -> tuple:
    return tuple("" if row.get(k) is None else str(row.get(k)).strip() for k in keys)


def _dedupe(rows: list[dict], keys: list[str], keep: str) -> tuple[list[dict], int]:
    seen: dict[tuple, int] = {}
    out: list[dict] = []
    for r in rows:
        k = _key(r, keys)
        if k in seen:
            if keep == "last":
                out[seen[k]] = r
            continue
        seen[k] = len(out)
        out.append(r)
    return out, len(rows) - len(out)


def _first_non_null(vals):
    for v in vals:
        if v is not None and not (isinstance(v, float) and math.isnan(v)):
            return v
    return None


def prepare(target: str, recs: list[dict]) -> tuple[list[dict], dict]:
    """The loader's per-table rules, applied before staging so the natural keys are unique and the
    snapshot tables carry exactly what the old path would have upserted. Returns (rows, notes)."""
    notes: dict[str, Any] = {}
    rows = [dict(r) for r in recs]
    if target == "orders":
        rows = [r for r in rows if r.get("invoice_no")]
        rows, notes["folded"] = _dedupe(rows, ["invoice_no"], "last")
    elif target == "order_lines":
        rows = [r for r in rows if r.get("invoice_no")]
        rows, notes["folded"] = _dedupe(rows, ["invoice_no", "line_no"], "last")
    elif target == "stock_movements":
        before = len(rows)
        rows = [r for r in rows if r.get("item_name")]
        notes["dropped_null_item"] = before - len(rows)
        rows, notes["folded"] = _dedupe(rows, ["voucher", "item_name", "row_hash"], "first")
    elif target == "ledger_entries":
        before = len(rows)
        rows = [r for r in rows if r.get("account")]
        notes["dropped_null_account"] = before - len(rows)
        rows, notes["folded"] = _dedupe(rows, ["account", "voucher", "row_hash"], "first")
    elif target == "product_profitability":
        before = len(rows)
        rows = [r for r in rows if r.get("item_name")]
        notes["dropped_null_item"] = before - len(rows)
        rows, notes["folded"] = _dedupe(rows, ["item_name", "report_date"], "last")
    elif target == "selling_prices":
        rows, notes["folded"] = _dedupe(rows, TARGETS[target]["keys"], "last")
    elif target == "ar_ageing":
        before = len(rows)
        rows = [r for r in rows if r.get("account")]
        notes["dropped_null_account"] = before - len(rows)
        num = ["balance_bhd", "bucket_0_30", "bucket_31_60", "bucket_61_90", "bucket_91_120", "bucket_121_150",
               "bucket_151_180", "bucket_181_210", "bucket_over_210", "total_bhd"]
        groups: dict[tuple, list[dict]] = {}
        for r in rows:
            groups.setdefault(_key(r, ["account", "as_of_date"]), []).append(r)
        merged = []
        for grp in groups.values():
            g = dict(grp[0])
            for c in num:
                # pandas .sum() semantics (the old loader): a group with no values sums to 0, not NULL
                g[c] = sum((v for v in (dec(x.get(c)) for x in grp) if v is not None), Decimal(0))
            for c in g:
                if c not in num and c not in ("account", "as_of_date"):
                    g[c] = _first_non_null([x.get(c) for x in grp])
            merged.append(g)
        notes["aggregated_same_name"] = len(rows) - len(merged)
        rows = merged
    elif target == "stock_balance":
        before = len(rows)
        rows = [r for r in rows if r.get("item_name")]
        notes["dropped_null_item"] = before - len(rows)
        for r in rows:
            r["warehouse_name"] = r.get("warehouse_name") or "(unassigned)"
        groups = {}
        for r in rows:
            groups.setdefault(_key(r, ["item_name", "warehouse_name", "as_of_date"]), []).append(r)
        merged = []
        for grp in groups.values():
            g = dict(grp[0])
            for c in ("net_qty", "total_value_bhd"):   # pandas .sum(): no values -> 0; .mean(): no values -> NULL
                g[c] = sum((v for v in (dec(x.get(c)) for x in grp) if v is not None), Decimal(0))
            rates = [v for v in (dec(x.get("selling_rate_bhd")) for x in grp) if v is not None]
            g["selling_rate_bhd"] = (rates[0] if len(rates) == 1 else
                                     (sum(rates, Decimal(0)) / len(rates)).quantize(Decimal("0.000001")) if rates else None)
            g["source_file"] = _first_non_null([x.get("source_file") for x in grp])
            merged.append(g)
        notes["aggregated_same_item"] = len(rows) - len(merged)
        rows = merged
    else:
        raise ValueError(f"not a batch target: {target}")
    return [{k: _jsonable(v) for k, v in r.items()} for r in rows], notes


# ── 3. what the file says: totals and scope (pure) ──────────────────────────────
def file_totals(target: str, rows: list[dict]) -> dict:
    """Rows and money sums the database must show for this file's scope after the load. Span
    targets count only dated rows (an undated row can never sit inside the span)."""
    cfg = TARGETS[target]
    scoped = rows if cfg["scope"] != "span" else [r for r in rows if r.get(cfg["span_col"])]
    sums = {}
    for m in cfg["money"]:
        tot = Decimal(0)
        for r in scoped:
            d = dec(r.get(m))
            if d is not None:
                tot += d
        sums[m] = money(tot)
    return {"rows": len(scoped), "sums": sums}


def scope_of(target: str, rows: list[dict]) -> dict:
    cfg = TARGETS[target]
    if cfg["scope"] == "span":
        dts = sorted(str(r[cfg["span_col"]])[:10] for r in rows if r.get(cfg["span_col"]))
        within = {w: len({str(r.get(w)) for r in rows if r.get(w) is not None}) for w in cfg.get("within", [])}
        return {"kind": "span", "col": cfg["span_col"], "from": dts[0] if dts else None, "to": dts[-1] if dts else None,
                "within": within}
    if cfg["scope"] == "partition":
        parts = sorted({tuple("" if r.get(c) is None else str(r.get(c)) for c in cfg["partition"]) for r in rows})
        return {"kind": "partition", "cols": cfg["partition"], "partitions": [list(p) for p in parts]}
    books = sorted({str(r.get("price_book")) for r in rows if r.get("price_book")})
    files = sorted({str(r.get("source_file")) for r in rows if r.get("source_file")})
    return {"kind": "book", "books": books, "files": files}


def expected_from_summary(summary: dict, actor: str | None) -> dict:
    """The contract ingest_commit re-asserts: per target rows, sums and per-action counts."""
    targets = {}
    for t, s in (summary.get("targets") or {}).items():
        if not s.get("actions"):
            raise ValueError(f"{t}: the preview has no database diff, so there is nothing to assert")
        targets[t] = {"rows": s["file_rows"], "sums": dict(s["sums"]), "actions": dict(s["actions"])}
    return {"actor": actor, "targets": targets}


# ── 4. the diff SQL both backends run ───────────────────────────────────────────
def _cast(src: str, col: str, typ: str) -> str:
    if typ == "jsonb":
        return f"({src}->'{col}')"
    if typ == "json":
        return f"({src}->'{col}')::json"
    return f"({src}->>'{col}')::{typ}"


def _eq(lhs: str, rhs: str, typ: str) -> str:
    """NULL-safe key equality the planner can hash/merge-join. `IS NOT DISTINCT FROM` is not an
    operator, so every anti-join became a 36k x 36k nested loop (measured: minutes); coalescing
    both sides to a type-appropriate sentinel keeps NULL = NULL and gets a merge join (seconds).
    Mirrored by _ingest_eq() in the migration."""
    if typ == "date":
        z = "'0001-01-01'::date"
    elif typ.startswith("timestamp"):
        z = "'0001-01-01'::timestamptz"
    elif typ in ("integer", "bigint", "smallint"):
        z = "-9223372036854775807"
    elif typ in ("numeric", "double precision", "real"):
        z = "'-1e30'::numeric"
    else:
        return f"coalesce({lhs}::text, '') = coalesce({rhs}::text, '')"
    return f"coalesce({lhs}, {z}) = coalesce({rhs}, {z})"


def diff_sql(target: str, col_types: dict[str, str], payload_cols: list[str], scope: dict,
             source: str = "table") -> tuple[str, list[str]]:
    """One SELECT returning removed / updated / revived / unchanged / inserted counts, the database
    rows + money sums in the file's scope and two samples. The staged rows come from
    ingest_stage (source='table': batch=$1, target=$2) or from ONE jsonb array parameter
    (source='param': $1 = the rows, $2 = target) -- the read-only CLI has no table to stage in.
    Placeholders are $n (text params; cast inside). The predicates are written exactly as
    _ingest_apply_target builds them, so the commit reproduces the preview.
    source_file is written on every update but never decides that a row changed (it differs on
    every re-export); in a price book it does, because the snapshot rule reads it."""
    cfg = TARGETS[target]
    keys = cfg["keys"]
    keym = " and ".join(_eq(f'L."{k}"', _cast("s.row", k, col_types[k]), col_types[k]) for k in keys)
    is_book = cfg["scope"] == "book"
    diffs = " or ".join(f'L."{c}" is distinct from {_cast("s.row", c, col_types[c])}'
                        for c in payload_cols if c != "source_file" or is_book)
    stage = ("select row from ingest_stage where batch_id = $1::bigint and target = $2" if source == "table"
             else "select value as row from jsonb_array_elements($1::jsonb)")
    key_json = ", ".join(f"'{k}', L.\"{k}\"" for k in keys)
    key_order = ", ".join(f'L."{k}"' for k in keys)
    params: list[str] = []
    if cfg["scope"] == "span":
        sc = f'L."{cfg["span_col"]}" between $3::date and $4::date'
        # ... and only for the accounts / items the file carries (mirrors the migration's 'within')
        for w in cfg.get("within", []):
            sc += f' and L."{w}" in (select distinct sw.row->>\'{w}\' from s sw)'
        params = [scope["from"], scope["to"]]
        sample_extra = f", '{cfg['span_col']}', L.\"{cfg['span_col']}\""
    elif cfg["scope"] == "partition":
        partm = " and ".join(_eq(f'L."{p}"', _cast("sp.row", p, col_types[p]), col_types[p]) for p in cfg["partition"])
        sc = f"exists (select 1 from s sp where {partm})"
        sample_extra = ""
    else:
        sc = ("L.price_book = any($3::text[]) and L.voided_at is null and L.source_file <> all($4::text[]) "
              "and lower(coalesce(L.source_file, '')) like (case L.price_book when 'MA_base' then 'masellingpricebook%' "
              "when 'modern_trade' then 'moderntradesellerbook%' end)")
        params = [_pg_array(scope["books"]), _pg_array(scope["files"])]
        sample_extra = ", 'source_file', L.source_file"
    was_voided = "L.voided_at is not null" if is_book else "false"
    sums = ", ".join(f'(select round(coalesce(sum(L."{m}"), 0)::numeric, 3)::text from sc L) as "db_sum_{m}"'
                     for m in cfg["money"])
    sql = f"""
with s as ({stage}),
     sc as (select L.* from "{target}" L where {sc}),
     m as (select L.id, ({diffs}) as changed, {was_voided} as was_voided
             from "{target}" L join s on {keym})
select
  (select count(*) from sc L where not exists (select 1 from s where {keym})) as removed,
  (select count(*) from m where changed and not was_voided) as updated,
  (select count(*) from m where was_voided) as revived,
  (select count(*) from m where not changed and not was_voided) as unchanged,
  (select count(*) from s where not exists (select 1 from "{target}" L where {keym})) as inserted,
  (select count(*) from sc) as db_rows,
  {sums},
  (select json_agg(x) from (select json_build_object({key_json}{sample_extra}) as x from sc L
      where not exists (select 1 from s where {keym}) order by {key_order} limit {SAMPLE}) q) as removed_sample,
  (select json_agg(x) from (select json_build_object({key_json}{sample_extra}) as x from "{target}" L join s on {keym}
      where ({diffs}) or {was_voided} order by {key_order} limit {SAMPLE}) q) as updated_sample
"""
    return sql, params


# ── 5. backends ─────────────────────────────────────────────────────────────────
class Backend:
    """What the importer needs from a database. mode: 'rest' (API) or 'pg' (psycopg).
    stage_source: 'table' (rows live in ingest_stage) or 'param' (rows are handed to the diff
    as one jsonb parameter; nothing is written anywhere)."""
    mode = "none"
    read_only = True
    stage_source = "table"

    def query(self, sql: str, params: list[str] | None = None) -> list[dict]:
        raise NotImplementedError

    def tables_ready(self) -> bool:
        """True when ingest_batches_migration.sql has been applied."""
        raise NotImplementedError

    def can_stage(self) -> bool:
        return self.stage_source == "param" or self.tables_ready()

    def stage(self, batch_id: int, target: str, rows: list[dict]) -> int:
        raise NotImplementedError

    def staged(self, batch_id: int, target: str) -> list[dict]:
        """The rows staged for (batch, target) when stage_source == 'param'."""
        raise NotImplementedError

    def after_stage(self, batch_id: int) -> None:
        """Planner hygiene once every target is staged (statistics for the diff)."""
        return None

    def new_batch(self, files: list[dict], actor: str | None) -> int:
        raise NotImplementedError

    def update_batch(self, batch_id: int, **fields) -> None:
        raise NotImplementedError

    def get_batch(self, batch_id: int) -> dict | None:
        raise NotImplementedError

    def list_batches(self, limit: int = 20) -> list[dict]:
        raise NotImplementedError

    def rpc(self, name: str, args: dict) -> dict:
        raise NotImplementedError

    def merge_summary(self, batch_id: int, patch: dict, only_status: str | None = None) -> bool:
        """summary := summary || patch, server side, only while the batch is in `only_status`."""
        raise NotImplementedError

    def reject(self, batch_id: int, patch: dict) -> bool:
        """status := 'rejected' and summary := summary || patch in ONE statement, only while the
        batch is still 'previewed'. False when it moved (a commit landed first): the caller answers
        409 and nothing is overwritten."""
        raise NotImplementedError

    def prune(self) -> dict | None:
        """Retention (ingest_prune()) before a preview; None when not applicable."""
        return None

    def storage(self) -> dict | None:
        """Sizes of the ingest tables; None when not applicable."""
        return None

    def close(self) -> None:
        return None


class PgBackend(Backend):
    """psycopg connection. read_only=True (the CLI against production): the session is READ ONLY
    (SELECT and SET only -- a read-only transaction refuses even CREATE TEMP TABLE), the parsed
    rows are handed to the diff as one jsonb parameter, and rpc() refuses. read_only=False (the
    API's DirectBackend, or a local replay cluster): the real ingest_* tables and the real RPCs."""
    mode = "pg"

    def __init__(self, conn, read_only: bool = True):
        self.conn, self.read_only = conn, read_only
        self._mem: dict[tuple[int, str], list[dict]] = {}
        if read_only:
            self.stage_source = "param"
            conn.read_only = True
        # a jsonb parameter has no statistics, and freshly staged rows have none until ANALYZE;
        # without this the planner nest-loops the 36k-row ledger diff (measured in minutes), with
        # it every join is a hash/merge join (seconds). ingest_commit sets the same for itself.
        with conn.cursor() as cur:
            cur.execute("set enable_nestloop = off")

    @staticmethod
    def _positional(sql: str, params: list[str] | None) -> tuple[str, list]:
        """$n placeholders -> psycopg %s (a literal % in the SQL, e.g. LIKE 'stem%', becomes %%)."""
        out: list = []

        def sub(m):
            out.append((params or [])[int(m.group(1)) - 1])
            return "%s"
        return re.sub(r"\$(\d+)", sub, sql.replace("%", "%%")), out

    def query(self, sql: str, params: list[str] | None = None) -> list[dict]:
        from psycopg.rows import dict_row
        q, p = self._positional(sql, params)
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(q, p)
            return [dict(r) for r in cur.fetchall()]

    def tables_ready(self) -> bool:
        r = self.query("select to_regclass('public.ingest_stage') is not null and "
                       "to_regclass('public.ingest_batches') is not null as ok")
        return bool(r and r[0]["ok"])

    def stage(self, batch_id: int, target: str, rows: list[dict]) -> int:
        from psycopg.types.json import Jsonb
        if self.read_only:
            self._mem[(batch_id, target)] = rows
            return len(rows)
        with self.conn.cursor() as cur:
            with cur.copy("copy ingest_stage (batch_id, target, row) from stdin") as cp:
                for r in rows:
                    cp.write_row((batch_id, target, Jsonb(r)))
        return len(rows)

    def staged(self, batch_id: int, target: str) -> list[dict]:
        return self._mem.get((batch_id, target), [])

    def after_stage(self, batch_id: int) -> None:
        if not self.read_only:
            with self.conn.cursor() as cur:
                cur.execute("analyze ingest_stage")

    def new_batch(self, files: list[dict], actor: str | None) -> int:
        if self.read_only:
            return 0
        r = self.query("insert into ingest_batches (status, files, created_by) values ('parsing', $1::jsonb, $2) returning id",
                       [json.dumps(files), actor or ""])
        return int(r[0]["id"])

    def update_batch(self, batch_id: int, **fields) -> None:
        if self.read_only or not batch_id:
            return
        sets, params = [], []
        for i, (k, v) in enumerate(fields.items(), start=1):
            if k in ("summary", "exceptions", "files"):
                sets.append(f'"{k}" = ${i}::jsonb')
                params.append(json.dumps(v))
            else:
                sets.append(f'"{k}" = ${i}')
                params.append(v)
        params.append(str(batch_id))
        self.query(f"update ingest_batches set {', '.join(sets)} where id = ${len(params)}::bigint returning id", params)

    def get_batch(self, batch_id: int) -> dict | None:
        r = self.query("select * from ingest_batches where id = $1::bigint", [str(batch_id)])
        return _batch_row(r[0]) if r else None

    def list_batches(self, limit: int = 20) -> list[dict]:
        return [_batch_row(r) for r in self.query("select * from ingest_batches order by id desc limit $1::int", [str(limit)])]

    def rpc(self, name: str, args: dict) -> dict:
        if self.read_only:
            raise RuntimeError(f"{name}: this connection is read-only (rehearsal / preview only)")
        keys = list(args)
        q = f"select {name}(" + ", ".join(f"{k} := ${i}{'::jsonb' if isinstance(args[k], (dict, list)) else ''}"
                                          for i, k in enumerate(keys, start=1)) + ") as r"
        r = self.query(q, [json.dumps(args[k]) if isinstance(args[k], (dict, list)) else args[k] for k in keys])
        return r[0]["r"]

    def merge_summary(self, batch_id: int, patch: dict, only_status: str | None = None) -> bool:
        if self.read_only or not batch_id:
            return False
        r = self.query("update ingest_batches set summary = summary || $1::jsonb where id = $2::bigint "
                       "and ($3 = '' or status = $3) returning id", [json.dumps(patch), str(batch_id), only_status or ""])
        return bool(r)

    def reject(self, batch_id: int, patch: dict) -> bool:
        if self.read_only or not batch_id:
            return False
        r = self.query("update ingest_batches set status = 'rejected', summary = summary || $1::jsonb "
                       "where id = $2::bigint and status = 'previewed' returning id", [json.dumps(patch), str(batch_id)])
        return bool(r)

    def prune(self) -> dict | None:
        if self.read_only or not self.tables_ready():
            return None
        r = self.query("select ingest_prune() as r")
        return r[0]["r"] if r else None

    def storage(self) -> dict | None:
        if not self.tables_ready():
            return None
        r = self.query("select pg_total_relation_size('public.ingest_stage') as stage_bytes, "
                       "pg_total_relation_size('public.ingest_replaced') as replaced_bytes, "
                       "(select count(*) from ingest_stage) as stage_rows, "
                       "(select count(*) from ingest_batches where status = 'committed') as committed_batches")
        return {k: int(v) for k, v in r[0].items()} if r else None

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:  # noqa: BLE001
            pass


class DirectDbUnavailable(RuntimeError):
    """The API has no DATABASE_URL (or no psycopg): the batch path cannot run."""


def connect_direct(dsn: str | None = None, statement_timeout_s: int | None = None,
                   application_name: str = "yq-ingest-batch"):
    """The API's own database session for the batch path: psycopg over DATABASE_URL (the Supabase
    session-pooler URI), autocommit, with a statement timeout the commit can meet
    (INGEST_STATEMENT_TIMEOUT_S, default 600 s) and a lock timeout so a stuck lock fails fast.
    Every RPC through PostgREST runs under the authenticator role's statement_timeout = 8 s (read
    on production 24-Sep-2026; service_role has no override), and the replay measured the commit
    at ~10 s and the ledger diff above that before planner hygiene -- so the API never uses it."""
    import os
    dsn = dsn or os.getenv("DATABASE_URL")
    if not dsn:
        raise DirectDbUnavailable("DATABASE_URL is not set on this API: the batch importer needs its own database "
                                  "session (the Supabase session-pooler URI); the default Upload & refresh still works")
    try:
        import psycopg
    except ImportError as e:  # pragma: no cover
        raise DirectDbUnavailable("psycopg is not installed on this API (requirements.txt: psycopg[binary])") from e
    if statement_timeout_s is None:
        try:
            statement_timeout_s = int(os.getenv("INGEST_STATEMENT_TIMEOUT_S", "") or DEFAULT_STATEMENT_TIMEOUT_S)
        except ValueError:
            statement_timeout_s = DEFAULT_STATEMENT_TIMEOUT_S
    conn = psycopg.connect(dsn, connect_timeout=30, autocommit=True, application_name=application_name)
    with conn.cursor() as cur:
        cur.execute(f"set statement_timeout = '{int(statement_timeout_s)}s'")
        cur.execute("set lock_timeout = '30s'")
        cur.execute("set idle_in_transaction_session_timeout = '120s'")
    return conn


class DirectBackend(PgBackend):
    """The API's backend: PgBackend over connect_direct(). Owns its connection (close() it)."""
    mode = "direct"

    def __init__(self, conn, statement_timeout_s: int | None = None):
        super().__init__(conn, read_only=False)
        self.statement_timeout_s = statement_timeout_s

    @classmethod
    def open(cls, dsn: str | None = None, statement_timeout_s: int | None = None) -> "DirectBackend":
        conn = connect_direct(dsn, statement_timeout_s=statement_timeout_s)
        be = cls(conn, statement_timeout_s)
        try:
            be.statement_timeout_s = int(str(be.query("show statement_timeout")[0]["statement_timeout"]).rstrip("s"))
        except Exception:  # noqa: BLE001
            pass
        return be


def _batch_row(r: dict) -> dict:
    out = dict(r)
    for k in ("files", "summary", "exceptions"):
        if isinstance(out.get(k), str):
            try:
                out[k] = json.loads(out[k])
            except Exception:  # noqa: BLE001
                pass
    for k in ("created_at", "committed_at", "undone_at"):
        if isinstance(out.get(k), datetime):
            out[k] = out[k].isoformat()
    return out


def rpc_error_message(exc: Exception) -> str:
    """The RAISE text of a failed RPC, whatever wrapper raised it."""
    for attr in ("message", "details", "hint"):
        v = getattr(exc, attr, None)
        if v:
            return str(v)
    if isinstance(exc, dict):
        return str(exc.get("message") or exc)
    return str(exc)


# ── 6. the preview ──────────────────────────────────────────────────────────────
def _exc(code: str, severity: str, message: str, target: str | None = None, count: int | None = None,
         items: list | None = None, **extra) -> dict:
    e = {"code": code, "severity": severity, "target": target, "message": message,
         "count": count if count is not None else (len(items) if items is not None else None),
         "items": (items or [])[:SAMPLE]}
    e.update(extra)
    return e


def _guard_failed(check: str, what: str, target: str, e: Exception) -> dict:
    """A guard that cannot run is a guard that FAILS: the day-shrink, warehouse-set and price-book
    checks block the commit (un-acknowledgeably) when their query throws, instead of silently
    disappearing from the exception list."""
    return _exc("check_failed", BLOCKING,
                f"{check} ({what}) could not be checked: {str(e)[:140]}; the commit stays closed until the preview can run it",
                target, check=check)


def _col_types(backend: Backend, target: str) -> dict[str, str]:
    rows = backend.query("select column_name, data_type from information_schema.columns "
                         "where table_schema = 'public' and table_name = $1 order by ordinal_position", [target])
    return {r["column_name"]: r["data_type"] for r in rows}


def _target_diff(backend: Backend, batch_id: int, target: str, rows: list[dict], scope: dict) -> dict:
    types = _col_types(backend, target)
    if not types:
        raise RuntimeError(f"{target}: table not readable")
    payload = [c for c in rows[0].keys() if c in types and c not in PROTECTED]
    sql, params = diff_sql(target, types, payload, scope, source=backend.stage_source)
    first = json.dumps(backend.staged(batch_id, target)) if backend.stage_source == "param" else str(batch_id)
    r = backend.query(sql, [first, target] + params)[0]
    cfg = TARGETS[target]
    actions = {"inserted": int(r["inserted"]), "updated": int(r["updated"]), "unchanged": int(r["unchanged"])}
    if cfg["scope"] == "book":
        actions["voided"] = int(r["removed"])
        actions["revived"] = int(r["revived"])
    else:
        actions["deleted"] = int(r["removed"])
    samples = {}
    for k in ("removed_sample", "updated_sample"):
        v = r.get(k)
        samples[k] = (json.loads(v) if isinstance(v, str) else v) or []
    return {"actions": actions, "db_rows": int(r["db_rows"]),
            "db_sums": {m: str(r[f"db_sum_{m}"]) for m in cfg["money"]}, "payload_cols": payload, **samples}


def _sales_comparisons(backend: Backend, rows: list[dict], scope: dict) -> dict:
    """Per-day and per-salesman totals of the day book vs the database inside the file's span."""
    def agg(keyf):
        out: dict[str, dict] = {}
        for r in rows:
            k = keyf(r)
            if k is None:
                continue
            a = out.setdefault(k, {"rows": 0, "gross": Decimal(0), "taxable": Decimal(0)})
            a["rows"] += 1
            a["gross"] += dec(r.get("gross_bhd")) or Decimal(0)
            a["taxable"] += dec(r.get("taxable_bhd")) or Decimal(0)
        return out
    per_day = agg(lambda r: str(r["line_date"])[:10] if r.get("line_date") else None)
    per_rep = agg(lambda r: r.get("warehouse_name") or "(none)")
    db_day = {str(x["d"])[:10]: x for x in backend.query(
        "select line_date::text as d, count(*) as n, round(coalesce(sum(gross_bhd),0)::numeric,3)::text as g, "
        "round(coalesce(sum(taxable_bhd),0)::numeric,3)::text as t from order_lines "
        "where line_date between $1::date and $2::date group by 1", [scope["from"], scope["to"]])}
    db_rep = {(x["w"] or "(none)"): x for x in backend.query(
        "select warehouse_name as w, count(*) as n, round(coalesce(sum(gross_bhd),0)::numeric,3)::text as g, "
        "round(coalesce(sum(taxable_bhd),0)::numeric,3)::text as t from order_lines "
        "where line_date between $1::date and $2::date group by 1", [scope["from"], scope["to"]])}

    def merge(file_side, db_side):
        out = []
        for k in sorted(set(file_side) | set(db_side)):
            f = file_side.get(k)
            d = db_side.get(k)
            fg, dg = (money(f["gross"]) if f else "0.000"), (str(d["g"]) if d else "0.000")
            ft, dt = (money(f["taxable"]) if f else "0.000"), (str(d["t"]) if d else "0.000")
            out.append({"key": k, "file_rows": f["rows"] if f else 0, "db_rows": int(d["n"]) if d else 0,
                        "file_gross": fg, "db_gross": dg, "file_taxable": ft, "db_taxable": dt,
                        "delta_gross": money(Decimal(fg) - Decimal(dg)), "same": (f is not None and d is not None
                                                                                   and f["rows"] == int(d["n"]) and fg == dg and ft == dt)})
        return out
    days = merge(per_day, db_day)
    reps = merge(per_rep, db_rep)
    return {"per_day": days, "per_day_changed": [d for d in days if not d["same"]],
            "per_salesman": reps, "per_salesman_changed": [r for r in reps if not r["same"]]}


def _stock_snapshot(backend: Backend, rows: list[dict]) -> dict:
    """The stock_balance file per warehouse vs the database's latest snapshot per warehouse."""
    file_wh: dict[str, dict] = {}
    for r in rows:
        w = file_wh.setdefault(r.get("warehouse_name") or "(unassigned)", {"items": 0, "qty": Decimal(0), "value": Decimal(0)})
        w["items"] += 1
        w["qty"] += dec(r.get("net_qty")) or Decimal(0)
        w["value"] += dec(r.get("total_value_bhd")) or Decimal(0)
    db = backend.query("select as_of_date::text as d, warehouse_name as w, count(*) as items, "
                       "round(coalesce(sum(net_qty),0)::numeric,3)::text as qty, "
                       "round(coalesce(sum(total_value_bhd),0)::numeric,3)::text as value "
                       "from stock_balance where as_of_date = (select max(as_of_date) from stock_balance) group by 1, 2")
    latest = db[0]["d"] if db else None
    db_wh = {x["w"]: x for x in db}
    rows_out = []
    for w in sorted(set(file_wh) | set(db_wh)):
        f, d = file_wh.get(w), db_wh.get(w)
        rows_out.append({"warehouse": w, "file_items": f["items"] if f else 0, "db_items": int(d["items"]) if d else 0,
                         "file_qty": money(f["qty"]) if f else None, "db_qty": str(d["qty"]) if d else None,
                         "file_value": money(f["value"]) if f else None, "db_value": str(d["value"]) if d else None,
                         "delta_qty": money((f["qty"] if f else Decimal(0)) - (Decimal(str(d["qty"])) if d else Decimal(0))),
                         "delta_value": money((f["value"] if f else Decimal(0)) - (Decimal(str(d["value"])) if d else Decimal(0)))})
    return {"db_latest_as_of": latest, "file_warehouses": sorted(file_wh), "db_warehouses": sorted(db_wh),
            "missing_warehouses": sorted(set(db_wh) - set(file_wh)), "per_warehouse": rows_out}


def _price_changes(backend: Backend, rows: list[dict], today: str) -> dict:
    from scripts.verify_numbers import current_prices
    out: dict[str, dict] = {}
    by_book: dict[str, list[dict]] = {}
    for r in rows:
        by_book.setdefault(str(r.get("price_book")), []).append(r)
    for book, brows in by_book.items():
        want = current_prices(brows, today=today)
        live = {str(x["sku_code"]): dec(x["price_bhd"]) for x in backend.query(
            "select sku_code, price_bhd from v_price_list_by_book where price_book = $1", [book]) if x.get("sku_code")}
        changes, same, new, gone = [], 0, [], []
        for sku, p in want.items():
            fp = dec(p)
            if sku not in live:
                new.append(sku)
            elif live[sku] is not None and abs(live[sku] - fp) >= Decimal("0.0005"):
                changes.append({"sku": sku, "old": money(live[sku]), "new": money(fp)})
            else:
                same += 1
        gone = sorted(set(live) - set(want))
        stem = FOCUS_BOOK_STEMS.get(book, "")
        live_focus = backend.query(
            "select count(*) as n, count(distinct sku_code) as skus from selling_prices "
            "where price_book = $1 and voided_at is null and lower(source_file) like $2", [book, f"{stem}%"])
        live_skus = {str(x["s"]) for x in backend.query(
            "select distinct sku_code as s from selling_prices where price_book = $1 and voided_at is null "
            "and lower(source_file) like $2 and sku_code is not null", [book, f"{stem}%"])}
        file_skus = {str(r.get("sku_code")) for r in brows if r.get("sku_code")}
        out[book] = {"file_skus": len(want), "unchanged": same, "changes": changes, "new_skus": new, "dropped_skus": gone,
                     "live_focus_rows": int(live_focus[0]["n"]) if live_focus else 0,
                     "live_skus": len(live_skus), "live_skus_covered": len(live_skus & file_skus)}
    return out


def _is_sim(name: str) -> bool:
    """The name rule v_sales applies for the SIM division (division_payment_migration.sql)."""
    low = (name or "").lower()
    return "sim" in low or "batelco" in low


def _item_checks(backend: Backend, parsed: Parsed) -> dict:
    """Unaliased item names, unclassified divisions and cost coverage for the items in the drop."""
    names: dict[str, Decimal] = {}
    for r in parsed.rows.get("order_lines", []):
        if r.get("item_name"):
            names[r["item_name"]] = names.get(r["item_name"], Decimal(0)) + (dec(r.get("gross_bhd")) or Decimal(0))
    for t in ("stock_balance", "product_profitability", "stock_movements"):
        for r in parsed.rows.get(t, []):
            if r.get("item_name"):
                names.setdefault(r["item_name"], Decimal(0))
    if not names:
        return {}
    aliased: dict[str, dict] = {}
    lst = sorted(names)
    for i in range(0, len(lst), 400):
        for x in backend.query(
                "select a.alias_text as t, p.sku_code as sku, c.division as division "
                "from product_aliases a join products p on p.id = a.product_id left join categories c on c.id = p.category_id "
                "where a.alias_text in (select jsonb_array_elements_text($1::jsonb))", [json.dumps(lst[i:i + 400])]):
            aliased[x["t"]] = x
    gross_total = sum(names.values(), Decimal(0))
    # SIM starter packs are never in the price book (v_sales files them under SIM by name), so
    # they are not "unaliased accessories"; they are listed apart and stay out of the share.
    sim = sorted(n for n in names if n not in aliased and _is_sim(n))
    un = sorted((n for n in names if n not in aliased and not _is_sim(n)), key=lambda n: -names[n])
    un_gross = sum((names[n] for n in un), Decimal(0))
    unclassified = sorted(n for n, a in aliased.items()
                          if not a.get("division") and "sim" not in n.lower())
    skus = sorted({a["sku"] for a in aliased.values() if a.get("sku")})
    costed = set()
    for i in range(0, len(skus), 400):
        for x in backend.query(
                "select sku_code as s from mrn_landed_costs where sku_code in (select jsonb_array_elements_text($1::jsonb)) "
                "union select sku_code from purchase_costs where sku_code in (select jsonb_array_elements_text($1::jsonb))",
                [json.dumps(skus[i:i + 400])]):
            costed.add(x["s"])
    return {"items": len(names), "aliased": len(aliased), "unaliased": [{"item": n, "gross": money(names[n])} for n in un],
            "sim_items": sim, "unaliased_share": (float(un_gross / gross_total) if gross_total else 0.0),
            "unclassified": unclassified, "skus": len(skus), "uncosted_skus": sorted(set(skus) - costed)}


def _mrn_checks(backend: Backend, parsed: Parsed) -> dict:
    docs = sorted({str(r["voucher"]).split(":", 1)[1].strip() for r in parsed.rows.get("stock_movements", [])
                   if r.get("voucher") and str(r["voucher"]).upper().startswith("MRN")})
    loaded = {x["d"] for x in backend.query("select distinct doc_no as d from mrn_lines")}
    return {"in_ledger": docs, "with_cost": sorted(d for d in docs if d in loaded),
            "without_cost": sorted(d for d in docs if d not in loaded),
            "xml_in_folder": parsed.mrn_xml,
            "xml_already_loaded": sorted({d for x in parsed.mrn_xml for d in x.get("doc_nos", []) if d in loaded})}


def build_preview(parsed: Parsed, backend: Backend | None, batch_id: int = 0, today: str | None = None) -> tuple[dict, list[dict]]:
    """The preview summary and exception list. With no backend (or before the migration) the
    file-side figures are still reported; anything that needs the database says so."""
    today = today or date.today().isoformat()
    summary: dict[str, Any] = {"folder": Path(parsed.folder).name, "files": parsed.files, "ignored": parsed.ignored,
                               "as_on": parsed.as_on, "db_mode": backend.mode if backend else "none",
                               "targets": {}, "generated_at": datetime.now(timezone.utc).isoformat()}
    exceptions: list[dict] = []
    db_ok = backend is not None
    summary["migration_applied"] = bool(db_ok and backend.tables_ready())
    db_diff = db_ok and backend.can_stage()      # a param-staging backend diffs before the migration too

    for target, rows in parsed.rows.items():
        cfg = TARGETS[target]
        t: dict[str, Any] = {"file_rows_raw": len(rows), "notes": parsed.notes.get(target, {}),
                             "scope": scope_of(target, rows), **file_totals(target, rows)}
        t["file_rows"] = t.pop("rows")
        summary["targets"][target] = t
        if not rows:
            exceptions.append(_exc("empty_report", BLOCKING, f"{cfg['report']}: the export has no rows", target, 0))
            continue
        undated = [r for r in rows if cfg["scope"] == "span" and not r.get(cfg["span_col"])]
        if undated:
            exceptions.append(_exc("undated_rows", WARNING, f"{cfg['report']}: {len(undated)} rows have no "
                                   f"{cfg['span_col']}; they load but sit outside every date span", target, len(undated)))
        if cfg["scope"] == "partition" and any(not r.get(cfg["partition"][0]) for r in rows):
            exceptions.append(_exc("snapshot_without_date", BLOCKING,
                                   f"{cfg['report']}: rows without an as-of/report date (the title block was not read)", target))
        for k, v in (parsed.notes.get(target) or {}).items():
            if v:
                exceptions.append(_exc(f"prep_{k}", INFO, f"{cfg['report']}: {v} rows {k.replace('_', ' ')} (loader rule)", target, v))
        if db_diff:
            try:
                d = _target_diff(backend, batch_id, target, rows, t["scope"])
                t.update(d)
                removed = d["actions"].get("deleted", d["actions"].get("voided", 0))
                inserted, db_rows = d["actions"].get("inserted", 0), d.get("db_rows", 0)
                if removed:
                    if target in ("orders", "order_lines"):
                        sev, why = BLOCKING, "invoices in the database that this export no longer carries would be removed (deleted or re-lined in Focus?)"
                    elif cfg["scope"] == "book":
                        sev, why = INFO, "live rows of the same Focus book from an older export are not in this file and will be voided (never deleted)"
                    elif removed > REMOVED_MAX_SHARE * db_rows or removed > inserted:
                        sev, why = BLOCKING, (f"rows in the file's own scope are not in the file and would be removed -- more than "
                                              f"{REMOVED_MAX_SHARE:.0%} of the {db_rows} rows in scope or more than the {inserted} it inserts: "
                                              "a partial or filtered export?")
                    else:
                        sev, why = WARNING, "rows in the file's own scope are not in the file and would be removed (the stock ledger re-costs rows, so a replace here is normal)"
                    exceptions.append(_exc("rows_removed", sev, f"{cfg['report']}: {removed} {why}", target, removed, d["removed_sample"]))
                if cfg["scope"] == "book" and db_rows and removed > SNAPSHOT_MAX_STALE_SHARE * db_rows:
                    # the loader's mass-void guard, from the diff itself (no second query to fail)
                    exceptions.append(_exc("snapshot_void_guard", BLOCKING,
                                           f"{cfg['report']}: {removed} live rows would be voided, more than {SNAPSHOT_MAX_STALE_SHARE:.0%} of "
                                           f"the {db_rows} live Focus rows of {', '.join(t['scope'].get('books') or [])}: is the export complete?",
                                           target, removed, d["removed_sample"]))
            except Exception as e:  # noqa: BLE001
                t["diff_error"] = str(e)[:300]
                exceptions.append(_exc("db_diff_failed", BLOCKING, f"{cfg['report']}: the database diff failed: {str(e)[:160]}", target))

    if not db_ok:
        exceptions.append(_exc("db_comparison_unavailable", WARNING, "No database connection: file-side figures only; commit is not possible"))
    elif not db_diff:
        exceptions.append(_exc("db_comparison_unavailable", WARNING,
                               "ingest_batches_migration.sql is not applied: the per-row diff and commit are unavailable"))
    elif not summary["migration_applied"]:
        exceptions.append(_exc("migration_pending", INFO,
                               "ingest_batches_migration.sql is not applied on this database: preview only, no batch to commit"))

    # sales join gate (data rule 3)
    od, ol = parsed.rows.get("orders"), parsed.rows.get("order_lines")
    if od is not None and ol is not None:
        inv = {r["invoice_no"] for r in od}
        vou = {r["invoice_no"] for r in ol}
        denom = max(len(inv), len(vou)) or 1
        pct = len(inv & vou) / denom
        summary["join"] = {"overlap": len(inv & vou), "denom": denom, "pct": round(pct, 4)}
        if pct < JOIN_MIN:
            exceptions.append(_exc("sales_join_below_80", BLOCKING,
                                   f"Sales_day_book and Summary_sales_register share only {pct:.1%} of invoices (gate {JOIN_MIN:.0%})",
                                   "orders", items=sorted(inv ^ vou)))
    elif od is not None or ol is not None:
        exceptions.append(_exc("sales_pair_incomplete", WARNING,
                               "Only one of Sales_day_book / Summary_sales_register is in the drop; the pair is normally exported together",
                               "orders" if od is not None else "order_lines"))

    if ol:
        dts = sorted(str(r["line_date"])[:10] for r in ol if r.get("line_date"))
        as_on = parsed.as_on.get("stock_balance") or parsed.as_on.get("ar_ageing") or parsed.as_on.get("product_profitability")
        if dts and as_on and dts[-1] == as_on:
            exceptions.append(_exc("partial_day", INFO, f"{as_on} is the export day, so it is a partial day: the next drop must cover it again",
                                   "order_lines"))
        if db_ok:
            try:
                summary["sales"] = _sales_comparisons(backend, ol, summary["targets"]["order_lines"]["scope"])
                shrink = [d for d in summary["sales"]["per_day_changed"] if d["db_rows"] > d["file_rows"]]
                if shrink:
                    exceptions.append(_exc("days_shrink", BLOCKING, f"{len(shrink)} day(s) have MORE lines in the database than in the file",
                                           "order_lines", items=shrink))
            except Exception as e:  # noqa: BLE001
                summary["sales"] = {"error": str(e)[:200]}
                exceptions.append(_guard_failed("days_shrink", "per-day sales vs the database", "order_lines", e))

    sb = parsed.rows.get("stock_balance")
    if sb and db_ok:
        try:
            summary["stock"] = _stock_snapshot(backend, sb)
            if summary["stock"]["missing_warehouses"]:
                exceptions.append(_exc("warehouse_set_shrinks", BLOCKING,
                                       "Stock_balance covers fewer warehouses than the latest snapshot: "
                                       + ", ".join(summary["stock"]["missing_warehouses"]), "stock_balance",
                                       items=summary["stock"]["missing_warehouses"]))
        except Exception as e:  # noqa: BLE001
            summary["stock"] = {"error": str(e)[:200]}
            exceptions.append(_guard_failed("warehouse_set_shrinks", "the warehouse set vs the latest snapshot", "stock_balance", e))

    sp = parsed.rows.get("selling_prices")
    if sp and db_ok:
        try:
            summary["prices"] = _price_changes(backend, sp, today)
            for book, p in summary["prices"].items():
                if p["changes"]:
                    exceptions.append(_exc("price_changes", INFO, f"{book}: {len(p['changes'])} SKU prices change vs the live book",
                                           "selling_prices", items=p["changes"]))
                if p["new_skus"]:
                    exceptions.append(_exc("price_new_skus", INFO, f"{book}: {len(p['new_skus'])} SKUs are new to the book",
                                           "selling_prices", items=p["new_skus"]))
                # the void-share half of the mass-void guard comes from the diff (above); this is the SKU-cover half
                if p["live_skus"] and p["live_skus_covered"] < SNAPSHOT_MIN_SKU_COVER * p["live_skus"]:
                    exceptions.append(_exc("snapshot_void_guard", BLOCKING,
                                           f"{book}: the file names {p['live_skus_covered']} of the book's {p['live_skus']} live SKUs "
                                           f"(under {SNAPSHOT_MIN_SKU_COVER:.0%}): a filtered or truncated export?", "selling_prices"))
                exported = parsed.export_dates.get(book)
                late = [r for r in sp if r.get("price_book") == book and exported and str(r.get("start_date") or "")[:10] > exported]
                if late:
                    exceptions.append(_exc("prices_dated_after_export", WARNING,
                                           f"{book}: {len(late)} rows start after the export day {exported} (scheduled prices; "
                                           f"the 14-Sep day-first bug looked like this)", "selling_prices",
                                           items=[{"sku": r.get("sku_code"), "start": r.get("start_date"), "rate": r.get("rate_bhd")} for r in late]))
        except Exception as e:  # noqa: BLE001
            summary["prices"] = {"error": str(e)[:200]}
            exceptions.append(_guard_failed("snapshot_void_guard", "the file's SKU cover of the live price book", "selling_prices", e))

    if db_ok:
        try:
            items = _item_checks(backend, parsed)
            summary["items"] = items
            if items.get("unaliased"):
                sev = WARNING
                exceptions.append(_exc("unaliased_items", sev,
                                       f"{len(items['unaliased'])} item names have no product alias "
                                       f"({items['unaliased_share']:.1%} of the file's gross; alert level {UNALIASED_ALERT_SHARE:.0%})",
                                       None, items=items["unaliased"], share=items["unaliased_share"],
                                       above_alert=items["unaliased_share"] > UNALIASED_ALERT_SHARE))
            if items.get("unclassified"):
                exceptions.append(_exc("unclassified_division", INFO,
                                       f"{len(items['unclassified'])} aliased items have no division (counted as Accessories by v_sales today)",
                                       None, items=items["unclassified"]))
            if items.get("uncosted_skus"):
                exceptions.append(_exc("cost_coverage", INFO,
                                       f"{len(items['uncosted_skus'])} of {items['skus']} SKUs in the drop have no landed cost "
                                       f"(mrn_landed_costs / purchase_costs)", None, items=items["uncosted_skus"]))
        except Exception as e:  # noqa: BLE001
            summary["items"] = {"error": str(e)[:200]}
        try:
            mrn = _mrn_checks(backend, parsed)
            summary["mrn"] = mrn
            if mrn["without_cost"]:
                exceptions.append(_exc("mrn_without_cost", INFO,
                                       "MRNs in the stock ledger with no cost lines loaded: " + ", ".join(mrn["without_cost"]),
                                       "stock_movements", items=mrn["without_cost"]))
            if mrn["xml_already_loaded"]:
                exceptions.append(_exc("mrn_already_loaded", WARNING,
                                       "MRN XML in the drop is already loaded and is NOT re-imported (re-import blocked): "
                                       + ", ".join(mrn["xml_already_loaded"]), None, items=mrn["xml_already_loaded"], blocked=True))
            new_xml = [x for x in mrn["xml_in_folder"] if x.get("doc_nos") and not set(x["doc_nos"]) & set(mrn["xml_already_loaded"])]
            if new_xml:
                exceptions.append(_exc("mrn_xml_not_imported", INFO,
                                       "MRN XML in the drop is not part of a batch; upload it on the Orders page: "
                                       + ", ".join(x["file"] for x in new_xml), None, items=new_xml))
        except Exception as e:  # noqa: BLE001
            summary["mrn"] = {"error": str(e)[:200]}
    elif parsed.mrn_xml:
        exceptions.append(_exc("mrn_xml_not_imported", INFO, "MRN XML in the drop is not part of a batch",
                               None, items=parsed.mrn_xml))

    ar = parsed.rows.get("ar_ageing")
    if ar and parsed.ar_grand_total is not None:
        rows_sum = sum((dec(r.get("balance_bhd")) or Decimal(0) for r in ar), Decimal(0))
        summary["receivables"] = {"rows_sum": money(rows_sum), "focus_grand_total": money(parsed.ar_grand_total),
                                  "gap": money(rows_sum - parsed.ar_grand_total)}
        if rows_sum.quantize(THREE) != parsed.ar_grand_total.quantize(THREE):
            exceptions.append(_exc("ar_total_gap", WARNING,
                                   f"Receivables rows sum to {money(rows_sum)} but Focus's Grand Total is "
                                   f"{money(parsed.ar_grand_total)}: credit balances lose their sign in this export; "
                                   "both figures are kept, nothing is guessed", "ar_ageing"))

    # storage: the staged rows live in the same free-tier database as the marketplace
    if db_ok and summary["migration_applied"]:
        try:
            st = backend.storage()
            if st:
                summary["storage"] = st
                mb = (st.get("stage_bytes", 0) + st.get("replaced_bytes", 0)) / 1e6
                if mb > STAGE_WARN_MB:
                    exceptions.append(_exc("stage_storage_high", WARNING,
                                           f"ingest_stage + ingest_replaced hold {mb:.0f} MB (warn level {STAGE_WARN_MB} MB): reject or undo "
                                           "old batches, or run ingest_prune() with a shorter interval", None))
        except Exception as e:  # noqa: BLE001
            summary["storage"] = {"error": str(e)[:160]}

    blocking = [e for e in exceptions if e["severity"] == BLOCKING]
    summary["blocking_codes"] = sorted({e["code"] for e in blocking})
    # what a commit needs: a per-row diff for every target, and none of the HARD_BLOCKING exceptions
    # -- the preview itself being unreliable, a guard that could not run, or the owner's data rule 3
    # (join < 80 %), none of which a checkbox may clear. (Whether THIS session can commit is a
    # separate matter: the CLI never does, and the API needs the migration -- summary['batch_id'] says.)
    summary["hard_blocking_codes"] = sorted({e["code"] for e in blocking if e["code"] in HARD_BLOCKING})
    summary["commit_available"] = bool(db_diff) and all(t.get("actions") for t in summary["targets"].values()) \
        and not summary["hard_blocking_codes"]
    return summary, exceptions


# ── 7. orchestration ────────────────────────────────────────────────────────────
def run_preview(folder: str | Path, backend: Backend | None, actor: str | None = None,
                persist: bool = True, out_dir: Path | None = None, today: str | None = None,
                row_hook=None) -> dict:
    """Parse -> stage -> preview. With `persist` (and a writable backend) a batch row is created
    and left in status 'previewed' with its summary and exceptions; a read-only backend previews
    against a temp table and creates nothing. Writes summary.md + exceptions.csv under out_dir
    (default business_data/ingest_previews/<ts>/), best-effort."""
    parsed = parse_folder(folder, row_hook=row_hook)
    batch_id = 0
    pruned = None
    can_stage = backend is not None and backend.can_stage()
    if can_stage and persist and not backend.read_only and backend.tables_ready():
        try:
            pruned = backend.prune()     # retention first, so a stale preview never keeps 50k rows around
        except Exception as e:  # noqa: BLE001
            pruned = {"error": str(e)[:160]}
        batch_id = backend.new_batch(parsed.files, actor)
    staged: dict[str, int] = {}
    if can_stage:
        for target, rows in parsed.rows.items():
            if rows:
                staged[target] = backend.stage(batch_id, target, rows)
        backend.after_stage(batch_id)
    summary, exceptions = build_preview(parsed, backend, batch_id, today=today)
    summary["batch_id"] = batch_id or None
    summary["staged"] = staged
    if pruned is not None:
        summary["pruned"] = pruned
    if batch_id:
        backend.update_batch(batch_id, status="previewed", summary=summary, exceptions=exceptions)
    written = None
    try:
        written = write_preview_files(summary, exceptions, out_dir)
    except Exception as e:  # noqa: BLE001
        summary["preview_files_error"] = str(e)[:160]
    return {"batch_id": batch_id or None, "summary": summary, "exceptions": exceptions,
            "preview_dir": str(written) if written else None}


class CommitRefused(ValueError):
    """The commit did not start (unacknowledged blocking exceptions, wrong status, no diff)."""


class CommitFailed(RuntimeError):
    """The commit RPC was called and did not complete (raised, timed out, or the response was lost)."""


def commit_batch(batch_id: int, backend: Backend, actor: str | None, acknowledged: list[str] | None = None) -> dict:
    """Commit a previewed batch: every blocking exception must be acknowledged by code -- except
    the HARD_BLOCKING ones, which no acknowledgement clears -- then the RPC applies and
    re-asserts the preview's totals in one transaction."""
    b = backend.get_batch(batch_id)
    if not b:
        raise CommitRefused(f"batch {batch_id} not found")
    if b["status"] != "previewed":
        raise CommitRefused(f"batch {batch_id} is {b['status']}; only a previewed batch can be committed")
    summary, exceptions = b.get("summary") or {}, b.get("exceptions") or []
    hard = sorted({e["code"] for e in exceptions if e.get("severity") == BLOCKING and e["code"] in HARD_BLOCKING})
    if hard:
        raise CommitRefused("this preview cannot be committed, and no acknowledgement changes that: " + ", ".join(hard))
    if not summary.get("commit_available"):
        raise CommitRefused("this preview cannot be committed (see its exceptions)")
    ack = set(acknowledged or [])
    pending = sorted({e["code"] for e in exceptions if e.get("severity") == BLOCKING} - ack)
    if pending:
        raise CommitRefused("acknowledge the blocking exceptions first: " + ", ".join(pending))
    expected = expected_from_summary(summary, actor)
    expected["acknowledged"] = sorted(ack)
    return backend.rpc("ingest_commit", {"p_batch_id": batch_id, "p_expected": expected})


def undo_batch(batch_id: int, backend: Backend, actor: str | None) -> dict:
    return backend.rpc("ingest_undo", {"p_batch_id": batch_id, "p_actor": actor or ""})


# ── 8. files for the owner (summary.md + exceptions.csv) ────────────────────────
def render_summary_md(summary: dict, exceptions: list[dict]) -> str:
    L: list[str] = []
    L.append(f"# Ingest preview: {summary.get('folder')}")
    L.append("")
    L.append(f"Generated {summary.get('generated_at')} · database: {summary.get('db_mode')} · "
             f"migration applied: {summary.get('migration_applied')} · batch: {summary.get('batch_id') or '(none)'}")
    L.append("")
    L.append("## Files")
    for f in summary.get("files", []):
        L.append(f"- {f['file']} -> {f['target']} ({f.get('rows', f.get('raw_rows'))} rows)")
    for i in summary.get("ignored", []):
        L.append(f"- ignored: {i['file']} ({i['reason']})")
    L.append("")
    L.append("## Per target")
    L.append("| target | file rows | insert | update | unchanged | remove/void | file sums | DB rows in scope | DB sums (before) |")
    L.append("|---|---:|---:|---:|---:|---:|---|---:|---|")
    for t, s in summary.get("targets", {}).items():
        a = s.get("actions") or {}
        rem = a.get("deleted", a.get("voided", ""))
        L.append(f"| {t} | {s.get('file_rows')} | {a.get('inserted', '')} | {a.get('updated', '')} | {a.get('unchanged', '')} | {rem} "
                 f"| {', '.join(f'{k}={v}' for k, v in (s.get('sums') or {}).items())} | {s.get('db_rows', '')} "
                 f"| {', '.join(f'{k}={v}' for k, v in (s.get('db_sums') or {}).items())} |")
        sc = s.get("scope") or {}
        if sc.get("kind") == "span":
            L.append(f"  - scope: {sc.get('col')} {sc.get('from')} .. {sc.get('to')}")
        elif sc.get("kind") == "partition":
            L.append(f"  - scope: {sc.get('cols')} = {sc.get('partitions')}")
        elif sc.get("kind") == "book":
            L.append(f"  - scope: books {sc.get('books')} files {sc.get('files')}")
    sales = summary.get("sales") or {}
    if sales.get("per_day_changed"):
        L.append("")
        L.append("## Days that change (file vs DB)")
        L.append("| day | file rows | DB rows | file gross | DB gross | delta |")
        L.append("|---|---:|---:|---:|---:|---:|")
        for d in sales["per_day_changed"][:60]:
            L.append(f"| {d['key']} | {d['file_rows']} | {d['db_rows']} | {d['file_gross']} | {d['db_gross']} | {d['delta_gross']} |")
    if sales.get("per_salesman_changed"):
        L.append("")
        L.append("## Salesmen that change (file vs DB, gross)")
        for d in sales["per_salesman_changed"][:40]:
            L.append(f"- {d['key']}: file {d['file_gross']} ({d['file_rows']} lines) vs DB {d['db_gross']} ({d['db_rows']}); delta {d['delta_gross']}")
    st = summary.get("stock") or {}
    if st.get("per_warehouse"):
        L.append("")
        L.append(f"## Stock balance vs latest DB snapshot ({st.get('db_latest_as_of')})")
        for w in st["per_warehouse"]:
            L.append(f"- {w['warehouse']}: file {w['file_items']} items / {w['file_qty']} u / {w['file_value']} BHD vs "
                     f"DB {w['db_items']} / {w['db_qty']} / {w['db_value']}; delta {w['delta_qty']} u / {w['delta_value']} BHD")
    pr = summary.get("prices") or {}
    for book, p in pr.items():
        if isinstance(p, dict) and "file_skus" in p:
            L.append("")
            L.append(f"## Price book {book}: {p['file_skus']} SKUs, {p['unchanged']} unchanged, {len(p['changes'])} changes, "
                     f"{len(p['new_skus'])} new, {len(p['dropped_skus'])} dropped")
            for c in p["changes"][:60]:
                L.append(f"- {c['sku']}: {c['old']} -> {c['new']}")
    rc = summary.get("receivables")
    if rc:
        L.append("")
        L.append(f"## Receivables: rows {rc['rows_sum']} vs Focus Grand Total {rc['focus_grand_total']} (gap {rc['gap']})")
    it = summary.get("items") or {}
    if it.get("items"):
        L.append("")
        L.append(f"## Items: {it['items']} names, {it['aliased']} aliased, {len(it.get('unaliased', []))} unaliased "
                 f"({it.get('unaliased_share', 0):.1%} of gross), {len(it.get('unclassified', []))} unclassified, "
                 f"{len(it.get('uncosted_skus', []))} of {it.get('skus')} SKUs without a cost")
    mr = summary.get("mrn") or {}
    if mr.get("in_ledger"):
        L.append("")
        L.append(f"## MRNs in the ledger: {len(mr['in_ledger'])}; without cost lines: {mr.get('without_cost')}; "
                 f"XML already loaded: {mr.get('xml_already_loaded')}")
    L.append("")
    L.append(f"## Exceptions ({len(exceptions)}; blocking: {summary.get('blocking_codes')}; "
             f"cannot be acknowledged: {summary.get('hard_blocking_codes')})")
    for e in exceptions:
        L.append(f"- [{e['severity']}] {e['code']}{' (' + e['target'] + ')' if e.get('target') else ''}: {e['message']}")
    st = summary.get("storage") or {}
    if st.get("stage_bytes") is not None:
        L.append("")
        L.append(f"Storage: ingest_stage {st['stage_bytes'] / 1e6:.1f} MB ({st.get('stage_rows')} rows), "
                 f"ingest_replaced {st.get('replaced_bytes', 0) / 1e6:.1f} MB")
    L.append("")
    L.append(f"Commit available: {summary.get('commit_available')}")
    return "\n".join(L) + "\n"


def write_preview_files(summary: dict, exceptions: list[dict], out_dir: Path | None = None) -> Path:
    out = out_dir or (PREVIEW_DIR / datetime.now().strftime("%Y%m%d-%H%M%S"))
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.md").write_text(render_summary_md(summary, exceptions), encoding="utf-8")
    with (out / "exceptions.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["severity", "code", "target", "count", "message", "items"])
        for e in exceptions:
            w.writerow([e["severity"], e["code"], e.get("target") or "", e.get("count") if e.get("count") is not None else "",
                        e["message"], json.dumps(e.get("items") or [], ensure_ascii=False)])
    (out / "summary.json").write_text(json.dumps(summary, indent=1, default=str), encoding="utf-8")
    return out
