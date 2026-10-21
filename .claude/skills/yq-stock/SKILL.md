---
name: "yq-stock"
description: "Stock planning from the loaded Focus data, for the main store (the Accessories Warehouse): its stock as the portal shows it, at cost, the days of cover against KPI 1, the order list with each item's supplier and the lead time it rests on, how often the fast movers were in stock, how old the stock is and how the old and price-cut items are selling against the clear target, the damage store; and the day's editable Stock & Order Plan workbook. Or one item's whole picture. Use it when Furqan asks in plain words what to order, what is running out or sold out, how many days of stock he holds, whether the best sellers are in stock, which stock is old or not selling, whether a price cut worked, how much stock is damaged, for the order file, or how one item stands."
argument-hint: "[reorder|ageing|damage|file|team|ordered <supplier>|cancel <supplier>|<code>]"
allowed-tools:
  - "Bash(python -m scripts.ops stock *)"
  - "PowerShell(python -m scripts.ops stock *)"
---

# /yq-stock: stock cover, what to order, fast movers, ageing, damage, the order file

Run exactly `python -m scripts.ops stock $ARGUMENTS`: it starts with `python`, with no `cd`, no variable prefix and no pipe. Use the Bash tool with its timeout set to 600000 ms. Add a word only when he asked for it: `reorder` for the whole order list with each item's supplier, the lead time and the fast movers, `ageing` for the ageing table and its review, `damage` for the damage store in full, `file` for a new workbook even though today's exists, `team` for the team copy of his newest workbook, `ordered <supplier>` when he says he has placed an order with that supplier (`--line CODE=QTY` for lines he names himself, `--date YYYY-MM-DD` for another day), `cancel <supplier> --date YYYY-MM-DD` when he cancels one, or an item's code for that item. When the screen lists several items for a code, ask which one he means and run it again with that code. Run nothing else that loads, writes or deploys.

1. Show the screen unchanged, in a code block. Every figure comes from the program: type no number yourself.
2. If the output opens with LOAD INTERRUPTED or LAST LOAD FAILED, say so first, in plain words, and point him to /yq-load: the stock may be older than he thinks. Change nothing yourself.
3. After the block, write at most three lines of your own, in plain words, that use no number that is not on the screen: what to order, push or clear first, and why.
4. The FILE line names the day's Stock & Order Plan workbook: say in one sentence where it is (one file per data date; `file` makes another). Never open, change, move, copy or send the file yourself: it is his to edit and share. If the line says the file was not made this time, say why in the screen's words.
5. The order list is a suggestion worked out from each item's sales rate and the lead time: draft no purchase order and write to no supplier. When the screen says goods on order not deducted, say in one sentence that its quantities do not yet allow for goods already on their way.
6. Never present an estimate as a fact. A rate marked with a star rests on few days in stock, the ageing places units on their newest supplier receipts, damage is counted when it is sent to the damage store, and the lead time is measured or taken from the settings as the screen says: keep those words when you repeat a figure.
7. The only lever on old stock is the sales team (his own rule): never suggest a price cut, a markdown or a bundle. Repeat a weekly push as the screen gives it.
8. Never accuse anyone. A line marked "to check" says what differs, never who is to blame; never name a salesman or a van.
9. If he asks for the stock in the vans or the outlets, say that the order list counts the vans, the outlets and stock moving between them beside the main store, as its Vans column shows; one store's own figures and its transfers are on /yq-moves.
- `ordered` and `cancel` show what would be logged as goods on the way and end with the line that logs it (`--commit`). Show the screen, then ask him in one sentence whether to log it. Run that last line exactly as printed, only after he says yes, and never type or change its token yourself. If it says the file or the lines changed, run the preview again. Nothing is ever sent to a supplier.
- `team` makes the team copy (no cost or value): say where it is in one sentence, and never send it yourself.
