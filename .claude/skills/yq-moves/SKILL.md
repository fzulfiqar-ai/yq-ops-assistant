---
name: "yq-moves"
description: "The movement audit of the loaded Focus data: every transfer between the main store and the vans and outlets paired by what its two vouchers hold (never by their numbers), each van's and outlet's stock from its opening to its closing with its check, the main store's line, and what is to check in BHD at landed cost (not received after the deadline, booked in at another store, other quantities, identical issues without a receipt, receipts with no issue). Accessories only, SIM out. Use it when Furqan asks in plain words what went to the vans or came back, what was not received, how a van's or an outlet's stock moved, or what is to check on the transfers; rep:<name> for one van or outlet."
argument-hint: "[7d|mtd|YYYY-MM|A..B] [rep:<name>]"
allowed-tools:
  - "Bash(python -m scripts.ops moves *)"
  - "PowerShell(python -m scripts.ops moves *)"
---

# /yq-moves: transfers paired by contents, each van's and outlet's stock, what is to check

Run exactly `python -m scripts.ops moves $ARGUMENTS`: it starts with `python`, with no `cd`, no variable prefix and no pipe. Use the Bash tool with its timeout set to 600000 ms. Add a word only when he asked for it: a window (`7d` is the default; `mtd`, a month as `YYYY-MM`, or two days as `A..B`, written `YYYY-MM-DD..YYYY-MM-DD`), `rep:<name>` for one van or outlet (a salesman's name, or Causeway or YQ Roadshow). Run nothing else that loads, writes or deploys.

1. Show the screen unchanged, in a code block. Every figure comes from the program: type no number yourself.
2. If the output opens with LOAD INTERRUPTED or LAST LOAD FAILED, say so first, in plain words, and point him to /yq-load: the movements may be older than he thinks. Change nothing yourself.
3. After the block, write at most three lines of your own, in plain words, that use no number that is not on the screen: what to check first and why.
4. Never accuse anyone. A line marked "to check" says what differs, never who is to blame; never name a salesman or a van beside it in your own words.
5. Every store's check must read 0. If one does not, say that its figures need a look before anyone uses them, and that it is a fault of the program, not of the store.
6. If the screen says the per-store figures are not printed, say why in the screen's words: the transfers and the items to check still stand.
7. Pairs are made by what the two vouchers hold, never by their numbers. If he quotes the portal's Stock Movement page, say in one sentence that since the phase 2b release it pairs the transfers with the same code, so the same window shows the same pairs, as the screen's last lines say.
8. The behaviour columns (Friday vouchers, back-dated numbers, round trips, re-issues, month-end returns) are counts to read, never items to check: repeat them as counts.
9. If he asks to mark an item explained or done, or for the whole list of items to check, point him to /yq-watch.
