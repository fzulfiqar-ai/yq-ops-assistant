---
name: "yq-kpi"
description: "The monthly KPI scorecard from the loaded data and the KPI log: every KPI of his KPI sheet (stock cover, fast-mover availability, ageing, new items, the checks before an order, launches, quotations, terms, delivery, incoming checks, damage and claims, sales against the KPI step, SKU productivity, reports on time) marked MET, NOT MET, NOT EVIDENCED or NOTHING DUE with the evidence it rests on, then the 90-day plan's rows. Use it when Furqan asks how he stands on his KPIs, or for the scorecard of a month. Also when he says in plain words that he assessed a product, got a quotation, agreed terms with a supplier, checked a delivery, raised or settled a claim, or sent a report: that sentence becomes one line of the KPI log, logged only after his yes."
argument-hint: "[YYYY-MM] | log <kind> --field value ... | log sent weekly <saturday> | log sent monthly <YYYY-MM>"
allowed-tools:
  - "Bash(python -m scripts.ops kpi *)"
  - "PowerShell(python -m scripts.ops kpi *)"
  - "Bash(python -m scripts.ops status *)"
  - "PowerShell(python -m scripts.ops status *)"
---

# /yq-kpi: the KPI scorecard, and the KPI log

Run exactly `python -m scripts.ops kpi $ARGUMENTS`: it starts with `python`, with no `cd`, no variable prefix and no pipe. Use the Bash tool with its timeout set to 600000 ms. With no word it scores the last whole month; a month is written `YYYY-MM`. Add no word or option he did not ask for. Run nothing else that loads, writes or deploys.

## The scorecard

1. Show the screen unchanged, in a code block. Every figure and every status comes from the program: type no number yourself, and never change a status or call a row met, late or missed in words of your own.
2. If the output opens with LOAD INTERRUPTED or LAST LOAD FAILED, say so first, in plain words, and point him to /yq-load. Change nothing yourself.
3. After the block, write at most three lines of your own, in plain words, that use no number that is not on the screen: which NOT MET to work on first, and which NOT EVIDENCED needs only a sentence from him.
4. A row is MET only with the evidence named under it on the screen. NOT EVIDENCED means nothing on file shows either way; NOTHING DUE means nothing fell due, or the KPI has no target yet.
5. Never accuse anyone. A line marked "to check" says what differs, never who is to blame.

## The KPI log: a sentence he says

When he says he did one of these things, it becomes one line of the KPI log, of one kind:

| He says he | Kind | It needs |
|---|---|---|
| assessed a product before ordering it (price, quality, demand, competitor) | `candidate` | `--ref` (a short name) or `--item-code`; `--check-types` for the checks he did |
| got a quotation for a purchase | `quote` | `--ref` (the receipt's MRN number, or the order's name) and `--supplier` |
| agreed cost, payment terms, warranty or a delivery day | `terms` | `--supplier` and `--note` (the terms in words); `--due-date` for a delivery day |
| checked a delivery when it came in | `receipt_check` | `--ref` (the MRN number), `--qty` (units counted), `--check-types` |
| raised or settled a claim | `claim` | `--ref` (what it is for) and `--supplier`; `--due-date`, `--state closed` |
| sent a report | `sent` | `sent weekly <saturday>` or `sent monthly <YYYY-MM>` |

6. Preview it first: `python -m scripts.ops kpi log <kind> --field value ...`, one kind at a time, a value with spaces in double quotes; for a report, `python -m scripts.ops kpi log sent weekly <saturday>` or `python -m scripts.ops kpi log sent monthly <YYYY-MM>`. With no kind, `python -m scripts.ops kpi log` lists the kinds and every field each one takes.
7. Give only what he said. Never invent a date, an amount, a quantity, a supplier or a check he did not name: when the line needs a field he did not give, ask him for it. A day that is not today goes in `--event-date`. Put no phone number, ID number, e-mail address or person's name in any field.
8. Show the preview unchanged, in a code block, and ask him in one sentence whether to log it. Run its last line exactly as printed, only after he says yes, and never type or change its token yourself. If it says the line changed, run the preview again. If it says the log is being written by another command, say so, and run that last line again only when he says so.
9. If the preview refuses, say why in its words and ask him; a report can be logged as sent only once it was built. When it says the log already holds the line, nothing more is needed. One sentence can need two lines (a quotation and the terms agreed with it): preview and log them one at a time.

If he only asks whether the data is up to date or what is waiting, run `python -m scripts.ops status` instead and show it the same way.
