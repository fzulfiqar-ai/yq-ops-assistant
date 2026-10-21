---
name: "yq-week"
description: "The weekly pack: the weekly sales workbook for a whole Sunday-to-Saturday week (Dashboard, Salesmen, Categories, Products, Customers, Daily, Stock, Movements, Actions, Data, Notes), every tile in four windows (the week, the four weeks before, the same days last month, the month to date against the KPI step), built from the loaded Focus data and his to forward; then the AI weekly review of the same week, whose Top 5 actions wait for his answers. Use it when Furqan asks in plain words for the weekly report, the week's workbook, the weekly pack or the weekly review."
argument-hint: "[YYYY-MM-DD]"
allowed-tools:
  - "Bash(python -m scripts.ops report *)"
  - "PowerShell(python -m scripts.ops report *)"
  - "Bash(python -m scripts.ops status *)"
  - "PowerShell(python -m scripts.ops status *)"
---

# /yq-week: the weekly workbook, then the weekly review

## Step 1: the workbook

Run exactly `python -m scripts.ops report week $ARGUMENTS`: it starts with `python`, with no `cd`, no variable prefix and no pipe. Use the Bash tool with its timeout set to 600000 ms. With no date it builds the last full week, the one /yq-brief last-week shows; a week is named by the Saturday it ends on, written `YYYY-MM-DD`. Add no word or option he did not ask for. Run nothing else that loads, writes or deploys.

1. Show the output unchanged, in a code block. Every figure comes from the program: type no number yourself.
2. If the output opens with LOAD INTERRUPTED or LAST LOAD FAILED, say so first, in plain words, and point him to /yq-load. Change nothing yourself.
3. If no file is named (the week is not complete yet, the day is not a Saturday, or no sales are loaded), say in one sentence what the screen says, in its words, and stop: never build a part week another way, and do not start Step 2.
4. If a line starts `CHECKS TO LOOK AT`, tell him which check differs, in the screen's words, and that the workbook should not go out until that is explained.
5. A line that starts `Not worked out this time` names the parts of the file that stand aside this time, each with its reason: say them plainly. The rest of the file stands.
6. A line that starts `Note:` is for him: say it in plain words. The `Due by` line says whether the file was built by the day it is due.
7. The file it names is his to forward. Never open, change, move, copy or send it. A second build of the same week gets a new name next to the first; the first is never overwritten.
8. Never accuse anyone. An item is "to check": say what differs, never who is to blame.

## Step 2: the weekly review of the same week

The screen's first line names the week by the Saturday it ends on (`the week ending` and the day): write that Saturday as `YYYY-MM-DD`. Then read `.claude/commands/weekly-review.md` and follow it unchanged, from its first step to its last, with `--week-ending YYYY-MM-DD` as its argument, that Saturday in place of `YYYY-MM-DD`.

- Its commands are not pre-approved here: Claude Code asks before each one, as usual. Its approval step writes the portal's AI insights only after his answers, and it sends nothing without his yes.
- If its pack fails, show the error and stop: the workbook and its build record stand as they are.
- Say once that the review counts B2B accessories while the workbook counts all accessories (B2B and the two outlets), so their sales figures differ.

If he only asks whether the data is up to date or what is waiting, run `python -m scripts.ops status` instead and show it the same way.
