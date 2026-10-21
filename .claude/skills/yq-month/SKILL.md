---
name: "yq-month"
description: "The monthly management sales workbook for a whole month, laid out like his September Sales Report (Dashboard, Salesmen, Categories, Products, Customers, Daily, Data, Notes), built from the loaded Focus data, every figure a formula over the month's day-book lines. Use it when Furqan asks for the monthly sales report, the month's management workbook or the month-end Excel file. It runs only once the month is whole; its ten comment lines stay DRAFT until a later phase. Also the month-end stock count: the blind count sheets for each van and outlet and the main store's cycle list (prep, or prep full for a full or quarterly count), and a counted copy read back against the books (count with the file)."
argument-hint: "[YYYY-MM] | prep [full] | count <file>"
allowed-tools:
  - "Bash(python -m scripts.ops report *)"
  - "PowerShell(python -m scripts.ops report *)"
  - "Bash(python -m scripts.ops recon *)"
  - "PowerShell(python -m scripts.ops recon *)"
  - "Bash(python -m scripts.ops status *)"
  - "PowerShell(python -m scripts.ops status *)"
---

# /yq-month: the monthly sales workbook, and the month-end count sheets

When his words start with `prep` or `count`, follow "The count sheets" below. Otherwise run exactly `python -m scripts.ops report month $ARGUMENTS`: it starts with `python`, with no `cd`, no variable prefix and no pipe. Use the Bash tool with its timeout set to 600000 ms. With no month it builds the last whole month; a month is written `YYYY-MM`. Add no word or option he did not ask for. Run nothing else that loads, writes or deploys.

1. Show the output unchanged, in a code block. Every figure comes from the program: type no number yourself.
2. If the output opens with LOAD INTERRUPTED or LAST LOAD FAILED, say so first, in plain words, and point him to /yq-load. Change nothing yourself.
3. If it says the month is not whole yet, say in one sentence what it waits for, in the screen's words, and stop: never build a part month another way.
4. The file it names is a DRAFT: the ten comment lines (five Highlights, five Points to note) and three notes beside the tables are placeholders until the approval step of a later phase. Never write them yourself, never open, change, move or delete the file, and never send it anywhere.
5. If a line starts `CHECKS TO LOOK AT`, tell him which check differs, in the screen's words, and that the workbook should not go out until that is explained.
6. A line that starts `Note:` is for him: say it in plain words (an item that fell to Other needs a row from him, for example).
7. A second build of the same month gets a new name next to the first; the first is never overwritten.
8. Never accuse anyone. An item is "to check": say what differs, never who is to blame.

## The count sheets: prep and count

Rules 1, 2 and 8 above hold here too. Use the Bash tool with its timeout set to 600000 ms.

- For the count sheets run exactly `python -m scripts.ops recon prep`. Run `python -m scripts.ops recon prep full` instead only when he asks for a full or quarterly count.
- The screen names one workbook of blind sheets: he prints them and counts. Never open, change, move or delete the file. The sheets are blind: never state, look up or work out a book quantity, for an item or a store.
- For a counted copy run exactly `python -m scripts.ops recon count "<file>"`, with the file's name as he gives it inside the quotes. With no file named, run `python -m scripts.ops recon count` alone: it reads the newest workbook in `exports/reports/counts`.
- `COUNTS NOT READ` means nothing was kept: say each of its lines plainly; he fixes the file, saves it and asks again.
- A store whose book side waits for a load is worked out when he asks again after the next load.
- Never repeat a Note typed on a sheet, or who counted or checked it. A difference is "to check": say what differs, never who is to blame.

If he asks for the weekly workbook or the weekly review, use /yq-week: it builds the week's workbook, then runs the weekly review of the same week.

If he only asks whether the data is up to date or what is waiting, run `python -m scripts.ops status` instead and show it the same way.
