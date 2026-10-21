---
name: "yq-watch"
description: "The action list after each load: every transfer between stores that is to check (not received after its deadline, sent to the damage store and not booked in, received with other quantities, received in another store, identical transfers not all received, a receipt with no issue), each ACT or CHECK with its BHD at cost, and beside them, read-only, the load check's and the Command Centre's open items. Items he has explained stay off the list. Use it when Furqan asks in plain words for the action list or the full list, what is to check or to act on, which transfers are not received or differ, to see one item in full, to explain an item or mark one done, or how the floor and the ACT thresholds were set."
argument-hint: "[all|<id>|explain <id> <his words>|done <id> [his words]|replay]"
allowed-tools:
  - "Bash(python -m scripts.ops watch *)"
  - "PowerShell(python -m scripts.ops watch *)"
---

# /yq-watch: the action list

Run exactly `python -m scripts.ops watch $ARGUMENTS`: it starts with `python`, with no `cd`, no variable prefix and no pipe. Use the Bash tool with its timeout set to 600000 ms. Add a word only when he asked for it: `all` for every open item (the older ones counted once at the first run and the ones under the floor too), an item's id such as `A7` for that item in full, `replay` for the floor and the ACT thresholds and how they were worked out, `explain` and `done` as step 4 says. Run nothing else that loads, writes or deploys.

1. Show the screen unchanged, in a code block. Every figure comes from the program: type no number yourself.
2. If the output opens with LOAD INTERRUPTED or LAST LOAD FAILED, say so first, in plain words, and point him to /yq-load: the list may be older than he thinks. Change nothing yourself.
3. After the block, write at most three lines of your own, in plain words, that use no number that is not on the screen: which item to look at first, and why.
4. `explain` and `done` change the list, so run them only on his own words, never on your own judgement. When he says an item is explained (for example "A7 is explained: booked under another number"), run `explain` with that id and his words as he said them; when he says an item is done, run `done` with that id and his words if he gave any. The screen says the item's title back: if it is not the item he meant, say so and ask which one. Never explain or close an item he did not name.
5. An explained item stays off the list while it is what he explained: it comes back by itself when it grows by more than a quarter, becomes more pressing, or names a voucher that was not there. Say that in one sentence when he explains one. A done item rests until its facts change.
6. The blocks FROM THE LOAD CHECK and FROM THE COMMAND CENTRE are read-only here: the load and the brief keep them, and their BHD is worked out when the screen is made. He can still explain one or mark it done.
7. The first screen shows the BASELINE (what each rule found already in the data on its first run: counted once, not listed) and the THRESHOLDS once; `replay` shows the thresholds again. A value he sets in the settings wins over the worked-out one: if he wants one changed, say which setting the screen names, and change nothing yourself.
8. If the screen says the movements were not recorded this time, say why in the screen's words: the list below it is the one that stood before.
9. Never accuse anyone. Every item is "to check": say what differs, never who is to blame, and never name a salesman or a van beyond what the screen shows. Draft no message to a storekeeper or a salesman unless he asks for one.
- The line `Not checked yet` names the two checks that come with the month-end release: repeat it if he asks whether those are covered.
