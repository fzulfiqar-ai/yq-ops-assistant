---
description: Monthly AI Head business review (plan §20) — the weekly mechanism, deeper; runs only on a whole-month export
argument-hint: "YYYY-MM (the month to close, e.g. 2026-09)"
---

# Monthly AI Head review

Same rules, tags, freshness stamps and approval flow as `.claude/commands/weekly-review.md` — read
its "Hard rules" and follow them exactly: read-only, never send anything without the owner's explicit
yes, no contact details, every statement tagged FACT / ANALYSIS / RECOMMENDATION /
LOW-CONFIDENCE HYPOTHESIS with confidence and freshness.

## Step 0 — build the month pack

```
python -m scripts.ai_head.pack --monthly $ARGUMENTS --verify
```

It writes `exports/ai_head/month-<YYYY-MM>/`. Open `trust.json` → `monthly`. **If `ready` is false,
stop**: tell the owner why (the review runs only on an export dated on or after the 1st of the next
month, with Focus data reaching the month's end) and do nothing else.

## Step 1 — data trust gate

As in the weekly review (`trust.json`), plus: is the month whole in Focus (`monthly.focus_as_of`)?

## Step 2 — the month (`monthly.json`, `focus_context.json`, `items.json`, the CSVs)

- current month vs previous month vs trailing 3-month and 12-month averages (B2B Accessories, ex-VAT);
  say how many months the 12-month baseline really has (`months_in_12m_baseline`);
- accelerating and declining SKUs, merchants and reps (`skus_*`, `merchants_*`, `reps`);
- customer concentration: top-10 share and HHI (`concentration`);
- category mix (`categories`);
- repeated unavailable demand (`repeated_zero_result_terms`: zero-result terms in 2+ weeks) and
  repeated field requests (`repeated_field_requests`) — reports until corroborated;
- lost-sales ESTIMATE (`lost_sales_estimate`: sold-out days × 90-day run rate) — always labelled an
  estimate, never a fact;
- promotion incrementality: not measurable yet (no holdout / offer ledger) — say so;
- margin drift (`margin_drift`: landed-cost moves received this month) and margins / below cost
  (`items.json`), with the landed-cost coverage;
- rep month-close checks: `statements` for the month (draft → approved → paid; ex-VAT Accessories,
  net of returns, whole month at the reached tier) against `reps`. Flag any rep without a statement.

External research only for the top 1–3 items, cited.

## Step 3 — the report: `monthly.html` in the pack folder

Sections (exact names): Executive summary · Top actions (Top 5 for approval) · Sales · Order health ·
Merchant movement · Product movement · Team · Offers · Profitability · Lost demand ·
Market intelligence · Risks and anomalies · Data quality. The plum marketplace look, A4 printable.

```
python -m scripts.ai_head.render exports/ai_head/month-<YYYY-MM> --name monthly
```

## Step 4 — `insights.json` and the owner's decision

As in the weekly review, with `"kind": "monthly"` and `"week_ending"` = the month's last day. Validate
with a dry run, show the owner the Top 5, and load with `--commit` only after he answers.
