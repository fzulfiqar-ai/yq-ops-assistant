---
name: "yq-brief"
description: "Sales brief from the loaded Focus data: the latest day, the last 3 and 7 days, the last full week, the month so far against the KPI step, accounts and reps to watch, sold-out items still in demand, and up to five things to do today. Use it when Furqan asks in plain words how sales were yesterday, over the last days, last week or this month, how a salesman is doing, what needs his attention today, or whether the data is up to date and what is waiting to be loaded. Also when he asks for the team message for the salesmen's group."
argument-hint: "[day|3d|7d|week|last-week|mtd|last-month|YYYY-MM|A..B] [rep:<name>] [top:N] [again] [team]"
allowed-tools:
  - "Bash(python -m scripts.ops brief *)"
  - "PowerShell(python -m scripts.ops brief *)"
  - "Bash(python -m scripts.ops status *)"
  - "PowerShell(python -m scripts.ops status *)"
---

# /yq-brief: sales, what changed, what to do today

Run exactly `python -m scripts.ops brief $ARGUMENTS`: it starts with `python`, with no `cd`, no variable prefix and no pipe. Use the Bash tool with its timeout set to 600000 ms. Add a word only when he asked for it: a window (the forms are in the argument hint; `A..B` is two days, written `YYYY-MM-DD..YYYY-MM-DD`), `rep:<name>` for one salesman, `top:N` for the number of rows in a window's tables, `team` on its own for the team message. Run nothing else that loads, writes or deploys.

1. Show the screen unchanged, in a code block. Every figure comes from the program: type no number yourself.
2. If the output opens with LOAD INTERRUPTED or LAST LOAD FAILED, say so first, in plain words, and point him to /yq-load: it resumes a load that was cut off, and after a failed load it starts the one that replaces it. Change nothing yourself.
3. After the block, write at most three lines of your own, in plain words, that use no number that is not on the screen: which item to do first and why.
4. Never accuse anyone. An item is "to check": say what differs, never who is to blame.
5. When there is no new data the screen is two lines (the data date and what is waiting): show them and add nothing. If he wants the full brief anyway, run it again with `again` added.
6. The full action list, and marking an item done or explained, are /yq-watch: when he asks for either, use that skill. DO TODAY lists the five most pressing and counts the rest.
7. With `team` the screen is the team message the last load made, for him to copy into the salesmen's group: show the text under its first line in one code block of its own, unchanged, and add nothing. Never send it anywhere yourself.

If he only asks whether the data is up to date or what is waiting, run `python -m scripts.ops status` instead and show it the same way.
