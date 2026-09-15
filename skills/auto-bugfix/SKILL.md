# Skill: auto-bugfix

Automatically diagnose and fix bugs in the YQ Bahrain AI Ops Assistant codebase.

## Trigger

Use this skill when:
- A test in `tests/stress_test.py` or `tests/run_eval.py` fails
- The Streamlit dashboard throws an error
- A FastAPI endpoint returns a 500
- The ingest pipeline fails with a known error pattern

## What this skill does

1. **Read the error** from the terminal, Streamlit logs, or Railway logs
2. **Locate the root cause** — check the relevant file and line
3. **Apply the fix** using Edit (never rewrite whole files unless necessary)
4. **Re-run the test** to confirm the fix works
5. **Commit** with a `fix:` prefix commit message

## Known bug patterns + fixes

### `ModuleNotFoundError: No module named 'app'`
**Cause:** Running script directly instead of as module  
**Fix:** Use `python -m scripts.script_name` not `python scripts/script_name.py`

### `null value in column violates not-null constraint`
**Cause:** Focus ERP export has blank rows in a required column  
**Fix:** Add `df = df.dropna(subset=["column_name"])` in `scripts/load_supabase.py`

### `ON CONFLICT DO UPDATE command cannot affect row a second time`
**Cause:** Duplicate rows in the source data  
**Fix:** Add `df = df.drop_duplicates(subset=["key_col1", "key_col2"])` before upsert

### `ERROR: cannot change name of view column`
**Cause:** `CREATE OR REPLACE VIEW` can only append columns — it cannot rename, reorder or drop them  
**Fix:** Edit the view's **canonical** definition, which for most views is a `scripts/*_migration.sql`
file, **not** `scripts/views.sql`. Look up the owning file in `docs/MIGRATIONS.md` first.
If a column genuinely must be renamed or removed, write a **new** migration that drops the view
*and re-creates every dependent view*, in order. Do not add `CASCADE` to an existing `DROP VIEW`
to make an error go away — that silently deletes the dependent views (see `docs/MIGRATIONS.md`).

### `total_amount_bhd` is null for all rows
**Cause:** Focus leaves this blank for zero-VAT transactions  
**Fix:** Revenue is `COALESCE(ol.total_amount_bhd, ol.gross_bhd) AS revenue_bhd` — the gross,
VAT-inclusive basis that reconciles to the verified total. Set in
`scripts/revenue_channel_migration.sql`, carried by `scripts/division_payment_migration.sql`.
Do **not** insert `taxable_bhd` into that fallback chain; it silently understates revenue.

### Login: `Invalid credentials or access not granted`
**Cause 1:** Email not in `user_roles` table  
**Fix:** `INSERT INTO user_roles (email, role) VALUES ('email@domain.com', 'admin');` in Supabase SQL Editor  
**Cause 2:** Supabase secrets not set on Streamlit Cloud  
**Fix:** Go to share.streamlit.io → app ⋮ → Settings → Secrets → paste TOML → Save

### Streamlit sidebar not visible
**Cause:** Sidebar collapsed or toggle button hidden by CSS  
**Fix:** Ensure `[data-testid="stSidebarCollapseButton"] { display: none; }` and sidebar has `min-width` set

### `run_readonly_query` RPC missing
**Cause:** the RPC (defined at the bottom of `scripts/views.sql`) was never applied  
**Fix:** Copy **only** the final `-- Safe SQL executor …` section of `scripts/views.sql` — the
`CREATE OR REPLACE FUNCTION run_readonly_query` block plus its `REVOKE`/`GRANT` lines — into the
Supabase SQL Editor and run that.
**Do NOT paste the whole file** on an existing database: it is the empty-DB bootstrap baseline and
would regress `v_sales`, `v_receivables` and `v_low_stock` to their pre-migration definitions.
The file carries a guard that aborts if you try, so a whole-file paste will simply error out.

### LLM returns SQL with raw table names
**Cause:** LLM ignored the view-only instruction  
**Fix:** The `app/sql_validator.py` catches this — check the allowlist includes the view name

### `SQLValidationError: Query references disallowed object`
**Cause:** LLM generated SQL referencing a raw table or subquery  
**Fix:** Add the view name to `VIEW_ALLOWLIST` in `app/sql_validator.py` if it's a legitimate new view, or improve the prompt in `app/ai.py` `_VIEW_SCHEMA`

## How to run tests

```powershell
# Integration + stress tests (requires live Supabase)
python -m tests.stress_test

# Template eval (offline, no Supabase needed)
python -m tests.run_eval

# Send test digest email
python -m scripts.send_digest --type all

# Watch folder for auto-ingest
python -m scripts.watch_ingest
```

## Commit conventions

```
fix: <one line describing what was broken and how it was fixed>
```

Never use `--no-verify`. Never amend pushed commits.

## Files most likely to need fixes

| File | Common issues |
|---|---|
| `app/sql_validator.py` | Allowlist missing a view, LIMIT not injecting |
| `app/templates.py` | Regex too greedy or too strict; ordering matters |
| `app/ai.py` | LLM prompt drift; cache not expiring; Redactor restore fails |
| `scripts/load_supabase.py` | Null constraint, duplicate key, column mismatch |
| `scripts/views.sql` | GENERATED + bootstrap-only — edit `VIEWS_SQL` in `scripts/migrate_views.py`. For live-DB view fixes edit the canonical `*_migration.sql` instead (`docs/MIGRATIONS.md`) |
| `dashboard/ui.py` | CSS hiding Streamlit elements; session state race |
| `app/digest.py` | Division by zero in delta calc; None from empty query |
