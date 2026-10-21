---
name: "yq-month"
description: "The month-end pack for a whole month: his September Sales Report's sheets (Dashboard, Salesmen, Categories, Products, Customers, Daily, Data, Notes) with Stock, Movements, Reconciliation, KPI and KPI log between Daily and Data, built from the loaded Focus data, every sales figure a formula over the month's day-book lines. Use it when Furqan asks for the monthly sales report, the month's management workbook, the month-end pack or Excel file, or how a month reconciles. It runs only once the month is whole. Its ten comment lines and three notes are drafted with placeholders from the month's facts, checked, and written into the final workbook only on his approval; then the AI monthly review of the same month. Also the month-end stock count: the blind count sheets for each van and outlet and the main store's cycle list (prep, or prep full for a full or quarterly count), and a counted copy read back against the books (count with the file)."
argument-hint: "[YYYY-MM] | prep [full] | count <file>"
allowed-tools:
  - "Bash(python -m scripts.ops report *)"
  - "PowerShell(python -m scripts.ops report *)"
  - "Bash(python -m scripts.ops check *)"
  - "PowerShell(python -m scripts.ops check *)"
  - "Bash(python -m scripts.ops recon *)"
  - "PowerShell(python -m scripts.ops recon *)"
  - "Bash(python -m scripts.ops status *)"
  - "PowerShell(python -m scripts.ops status *)"
---

# /yq-month: the month-end pack, its comment lines, the count sheets

When his words start with `prep` or `count`, follow "The count sheets" below. Otherwise run exactly `python -m scripts.ops report month $ARGUMENTS`: it starts with `python`, with no `cd`, no variable prefix and no pipe. Use the Bash tool with its timeout set to 600000 ms. With no month it builds the last whole month; a month is written `YYYY-MM`. Add no word or option he did not ask for. Run nothing else that loads, writes or deploys.

1. Show the output unchanged, in a code block. Every figure comes from the program: type no number yourself.
2. If the output opens with LOAD INTERRUPTED or LAST LOAD FAILED, say so first, in plain words, and point him to /yq-load. Change nothing yourself.
3. If it says the month is not whole yet, say in one sentence what it waits for, in the screen's words, and stop: never build a part month another way.
4. The file it names is a DRAFT: the ten comment lines (five Highlights, five Points to note) and three notes beside the tables are placeholders until he approves them (below). Never write them into the workbook yourself, never open, change, move or delete the file, and never send it anywhere.
5. If a line starts `CHECKS TO LOOK AT`, tell him which check differs, in the screen's words, and that the workbook should not go out until that is explained.
6. A line that starts `Note:` is for him: say it in plain words (an item that fell to Other needs a row from him, for example). Relay a sheet the `Month-end sheets` line calls not worked out this time as the line says it.
7. A second build of the same month gets a new name next to the first; the first is never overwritten.
8. Never accuse anyone. An item is "to check": say what differs, never who is to blame.

## The comment lines: drafted, checked, approved

- The screen names a Facts file and a Commentary file. Draft all thirteen slots of the Commentary file (five highlights, five points, three notes) in words and `{key|fmt}` placeholders only, each key taken from the Facts file and each format one that fits its kind. Never type a figure, a number word or a percent sign, and never copy a value out of the Facts file.
- Run exactly `python -m scripts.ops check "<file>"` with the Commentary file's name, and show him the lines as they will read, unchanged. Correct each line it refuses, in the Commentary file, and check again.
- Only on his "approved", run the text after `FINAL: ` exactly as printed, never typing or changing its token or any other part of it. If he changes a word, edit the Commentary file and check again before he approves.
- If the final step says a load changed the month's figures, build the month again, read every line against the new Facts file, check again and ask him again.
- The DRAFT stays beside the final workbook. Never open, move or send either.

Then read `.claude/commands/monthly-review.md` and follow it unchanged for the same month, from its first step to its last, with that month (`YYYY-MM`) as its argument. It stops by itself when the month is not ready; its commands are not pre-approved here and ask as usual; its only write comes after his answers. Say once that the review counts B2B accessories while the workbook counts all accessories (B2B and the two outlets).

If he asks how a month reconciles (the stock, the sales and receivables, the purchases), run exactly `python -m scripts.ops recon YYYY-MM` for that month and show it unchanged: a difference is "to check", and a missing paper is an action, never a difference. If he names the month's Focus Ledger export file, add it: `python -m scripts.ops recon YYYY-MM --ledger-file "<path>"`.

## The count sheets: prep and count

Rules 1, 2 and 8 above hold here too. Use the Bash tool with its timeout set to 600000 ms.

- For the count sheets run exactly `python -m scripts.ops recon prep`. Run `python -m scripts.ops recon prep full` instead only when he asks for a full or quarterly count.
- The screen names one workbook of blind sheets: he prints them and counts. Never open, change, move or delete the file. The sheets are blind: never state, look up or work out a book quantity, for an item or a store.
- For a counted copy run exactly `python -m scripts.ops recon count "<file>"`, with the file's name as he gives it inside the quotes. With no file named, run `python -m scripts.ops recon count` alone: it reads the newest workbook in `exports/reports/counts`.
- `COUNTS NOT READ` means nothing was kept: say each of its lines plainly; he fixes the file, saves it and asks again.
- A store whose book side waits for a load is worked out when he asks again after the next load.
- Never repeat a Note typed on a sheet, or who counted or checked it. A difference is "to check": say what differs, never who is to blame.

If he asks for the weekly workbook or the weekly review, use /yq-week: it builds the week's workbook, then runs the weekly review of the same week. For his KPI scorecard, or to log a sentence in the KPI log, use /yq-kpi.

If he only asks whether the data is up to date or what is waiting, run `python -m scripts.ops status` instead and show it the same way.
