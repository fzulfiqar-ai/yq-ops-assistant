---
name: "yq-stock"
description: "Stock planning from the loaded Focus data, for the main store (the Accessories Warehouse): its stock as the portal shows it, at cost, the days of cover against KPI 1, the order list with the lead time it rests on, how often the fast movers were in stock, and how old the stock is; or one item's whole picture. Use it when Furqan asks in plain words what to order, what is running out or sold out, how many days of stock he holds, whether the best sellers are in stock, which stock is old or not selling, or how one item stands."
argument-hint: "[reorder|ageing|<code>]"
allowed-tools:
  - "Bash(python -m scripts.ops stock *)"
  - "PowerShell(python -m scripts.ops stock *)"
---

# /yq-stock: stock cover, what to order, fast movers, ageing

Run exactly `python -m scripts.ops stock $ARGUMENTS`: it starts with `python`, with no `cd`, no variable prefix and no pipe. Use the Bash tool with its timeout set to 600000 ms. Add a word only when he asked for it: `reorder` for the whole order list with its lead time and the fast movers, `ageing` for the ageing table, or an item's code for that item. When the screen lists several items for a code, ask which one he means and run it again with that code. Run nothing else that loads, writes or deploys.

1. Show the screen unchanged, in a code block. Every figure comes from the program: type no number yourself.
2. If the output opens with LOAD INTERRUPTED or LAST LOAD FAILED, say so first, in plain words, and point him to /yq-load: the stock may be older than he thinks. Change nothing yourself.
3. After the block, write at most three lines of your own, in plain words, that use no number that is not on the screen: what to order, push or clear first, and why.
4. The order list is a suggestion worked out from each item's sales rate and the lead time: draft no purchase order and write to no supplier. When the screen says goods on order not deducted, say in one sentence that its quantities do not yet allow for goods already on their way.
5. Never present an estimate as a fact. A rate marked with a star rests on few days in stock, the ageing places units on their newest supplier receipts, and the lead time is measured or taken from the settings as the screen says: keep those words when you repeat a figure.
6. Never accuse anyone. A line marked "to check" says what differs, never who is to blame.
7. If he asks for the stock in the vans or the outlets, say that it comes with the movement audit, in a later phase.
