# Rep follow-ups, statements and attainment (release R3a, 24-Sep-2026)

Code: `app/followups.py`, `app/statements.py`, `app/reports.py` (`salesman_attainment`), thin routes in
`app/shop_api.py`; portal `web/src/pages/sales/{Today,Customers,Account,FollowUps}.tsx`,
`web/src/pages/{Salesmen,SalesmenRollups,Dashboard}.tsx`. Tests: `python -m tests.test_r3_reps`.

## Follow-ups (`GET /shop/me/followups`)

Rules over Focus history — no model, no paid call, nothing written:

| Rule | Value | Where |
|---|---|---|
| A shop belongs to | the rep who sold to it on the most distinct dates in the last 365 days (ties: most recent seller) | `SHOPS_SQL` `owner` |
| Rep match | `salesman_resolved = name OR LIKE name || ' - %'` — the same rule as `app.shop.rep_month_sales` | `SHOPS_SQL` |
| Counted sales | Accessories, not giveaway, not cash customer, named shop, qty > 0 | `SHOPS_SQL` |
| Cadence | the shop's own median gap between distinct purchase dates; one visit → the rep's median cadence, else 30 d (`gap_source = default`) | `rank_shops` |
| Value | ex-VAT (`net_bhd`) Accessories sales over 180 days ÷ 6, Decimal 3 dp | `rank_shops` |
| Due | `days_since ÷ gap ≥ 0.8` (same threshold as `v_customer_regulars.due`) | `DUE_RATIO` |
| Lapsed (win-back) | `days_since > max(90, 4 × gap)`; ranked by history depth (visits), then how recently | `LAPSED_*` |
| Rank (due) | `overdue_ratio × monthly value` | `rank_shops` |
| Top SKUs | the shop's `v_customer_regulars` rows with `due = true`, most-bought first, 5 at most, with the median qty | `_top_skus` |
| Copy | "Usually every ~23 days · last bought 31 days ago" — **never a date** | `_why` |
| Cache | 5 minutes per rep in-process | `followups()` |

**Privacy.** A rep receives only shops the SQL assigns to him. `v_customer_regulars` carries names, so it is read with
the service client and filtered to that rep's shops server-side; it is never granted to the read-only RPC. Phones come
from `customer_contacts` (business contacts) when present. An admin may pass `?rep=<Focus name | salesman id>` or
nothing (every shop with its owner).

**Holdout (20 %).** `is_holdout(rep, shop)` = `sha1("<base rep name>|<shop>") % 5 == 0` (names lower-cased,
whitespace collapsed, the " - Acc WH" warehouse suffix stripped). Nothing is stored: the same pair always lands on
the same side, so the split survives restarts and reloads. Held-back shops are never sent to the rep (they are only
counted, `counts.holdout`); an admin sees them flagged (`holdout: true`). The Customers page tells the rep that a
random fifth is held back. Measurement: for a shown shop vs a held-back shop, count Focus invoices within 14 days of
the list being served; taps are recorded as `audit_log` rows `event = 'followup.tap'`
(`POST /shop/me/followups/tap` → `{salesman_id, rep, shop, kind: due|lapsed, channel: wa|call|order|view, rank}`).
To retire the holdout set `HOLDOUT_MOD` so that no share is held (or remove the check) — a code change, on purpose.

## Baskets from your link not sent (`GET /shop/me/baskets`)

`shop_events` rows carrying the rep's `referral_code` over 7 days, replayed per device in time order
(`add` sets the qty from `meta.count`, `qty` changes it, `remove` drops it). A device with an `order` event, or a
`shop_orders` row (non-test) in the window, is dropped. Output per basket: item codes with quantities, the value
at today's trade price (Decimal, 3 dp; lines without a price are shown without a value), first/last activity and a
6-character opaque label — never a device id, phone or name.

## My link this week (`GET /shop/me/link-week`)

`shop.analytics(7, salesman=<the caller's row>)` reduced to sessions / item views / adds / checkouts / orders /
value / AOV / shares and the top 3 products. Always scoped to the caller's own salesman row (an admin without one
gets the unlinked hint, not company-wide figures).

## Statements (`/shop/statements*`, admin only)

`salesman_kickback_statements` (M8) + `scripts/r3_statements_migration.sql` (paid_by, superseded_at/by/reason/by_id,
an index). A statement is written once; amounts are never edited.

```
draft ──► approved ──► paid
  │           │
  └───────────┴──► superseded      (a corrected month is a NEW draft; the old row is marked)
snapshot                           (a documented moment; never moved)
```

* `POST /shop/statements/draft {period}` freezes `app.statements.build_rows(period)` — the same
  `rep_month_sales` / `rep_target` / `tier_progress` the Today card uses (ex-VAT, giveaways out, SIM never, whole
  month at the reached tier, `returns_bhd = NULL` until a Sales Return register is loaded). One row per rep with a
  target. A rep whose draft/approved/paid row has the same `data_through` is skipped; an **older** open draft of
  the same rep/period/basis moves to `superseded` (forward, audited); approved/paid rows are never touched — the
  response notes them so the admin supersedes deliberately.
* `POST …/{id}/approve | paid | supersede {reason}`: compare-and-swap on the status the caller saw (`UPDATE … WHERE id
  AND status = <from>`); a lost race is a 409 ("changed a moment ago"). Only `status`, `approved_by/at`,
  `paid_at/by`, `superseded_*` are ever in an UPDATE. Every move writes `audit_log` `kickback.statement_<to>`.
* `GET /shop/me` gains `last_closed` (the rep's latest approved/paid statement) and `draft` (the open one). The Me
  screen shows "August final · Tier 2 · BHD 51.250 · approved 3 Sep".
* `scripts/close_kickback_month.py` now calls `app.statements.build_rows`, so the CLI and the button freeze the
  same figures.

## Attainment (`GET /shop/attainment`, Shop Admin; Dashboard `by_salesman`)

`reports.ATTAINMENT_SQL`: the effective `salesman_targets` row per rep (a `YYYY-MM` row beats the standing `''`
row) FULL OUTER JOINed to the current month's Accessories sales per rep (month of `MAX(sale_date)`, giveaways out,
`net_bhd` ex-VAT beside gross, invoices, named shops, last sale). Reps with sales but no target row appear with
`no_target = true` (outlets such as Causeway included). Each row then runs through `app.shop.tier_progress`, so the
Salesmen page's Attainment tab, the rep's Today card and a frozen statement can never disagree. The Dashboard's
"Top salesmen" is now this list (Accessories · current month · ex-VAT), labelled; the all-time all-division
`v_sales_by_salesman` rollup stays on the Sales page.
