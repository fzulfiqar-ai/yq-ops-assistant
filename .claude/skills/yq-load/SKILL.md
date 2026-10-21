---
name: "yq-load"
description: "Checks the Focus reports in a dated drop folder, shows one table of what would load, then, after Furqan clicks Yes in a Windows box, loads them, verifies, prints the brief and e-mails it, and makes the day's Stock & Order Plan workbook. Only Furqan starts it, by typing /yq-load."
argument-hint: "[ddmmyy]"
disable-model-invocation: true
allowed-tools:
  - "Bash(python -m scripts.ops load *)"
  - "PowerShell(python -m scripts.ops load *)"
  - "Bash(python -m scripts.ops brief *)"
  - "PowerShell(python -m scripts.ops brief *)"
  - "Bash(python -m scripts.ops status *)"
  - "PowerShell(python -m scripts.ops status *)"
  - "Bash(python -m scripts.ops stock *)"
  - "PowerShell(python -m scripts.ops stock *)"
---

# /yq-load: check, ask, load, verify, brief

Rules, before anything else:
- Run each command exactly as written below: it starts with `python`, with no `cd`, no variable prefix, no pipe and no redirect. Use the Bash tool with its timeout set to 600000 ms.
- Never run scripts.refresh, scripts.ingest, scripts.load_supabase or any deploy. Never edit, move or delete anything in the drop folder.
- Show script output unchanged, in a code block. Type no number yourself.
- If the working folder path contains `.claude/worktrees/`, run nothing: tell him to open Claude Code in the main project folder.
- Do not fix a stop yourself: say what the output says and wait for him. The one exception is an interrupted load (Step 1).

## Step 1. Check
Run `python -m scripts.ops load check $ARGUMENTS` and show its block: from the line that starts `The last load` or `LOAD CHECK`, whichever comes first, to the last line. Leave out the progress lines above it; the whole check, with the longer lists, is in the `check.txt` the block names.
- `LOAD INTERRUPTED at <step> (run <run>)` in the output: an earlier load was cut off, and /yq-load is how he resumes it. Do not check again and add no option. The check prints that load's own `GO: ` line as its last line: tell him the box will say RESUME (Yes runs the whole load again from the same files; a newer folder is checked afterwards, when he types /yq-load again), then do Step 2 with that line. If no `GO: ` line ends the output, say what it says and wait; never write the line yourself.
- A line that starts `The last load (run <run>) failed at <step> after it had begun to write` (other commands open with `LAST LOAD FAILED` for the same thing): tell him that first and say plainly that the last load stopped after it had begun to write, so the database may hold part of it, and that this /yq-load starts a new load that replaces it, once the output ends with a `GO: ` line. Then carry on with the table.
- The last line starts `Nothing new in <ddmmyy>`: everything this load would apply is already loaded, so there is no `GO: ` line and nothing to load now. Tell him that plainly (no box opens and no e-mail goes). If the output lists what is not part of this load, name those lines too: his words below still load them, through Step 1 again.
- Any other output without a `GO: ` line at the end means the check stopped. Say why in the script's words, then stop. In his words he can say: "load the price book" (add `--pricebook load`), "leave the price book out" (`--pricebook skip`), "load profitability anyway" (`--with-profitability`), "replace on purpose" (`--replace-ok`), "partial on purpose" (`--partial-ok`). Run Step 1 again with that option only when he says it.
- A line marked NEEDS YOUR DECISION states its default. Say it in one sentence; when the output ends with a `GO: ` line, add that No at the box lets him answer first.

## Step 2. Go
In the same turn, when the output ends with a `GO: ` line, first tell him a Yes/No box opens on the PC when the line runs: Yes loads, No changes nothing. Then run exactly the text after `GO: ` (Bash tool, timeout 600000 ms, not in the background). Claude Code may ask him to approve it first: that is expected.
- Exit code 0 (it says `load started`): go to Step 3. If it also says to keep the Claude Code window open, tell him that first.
- Exit code 3 (check again): run Step 1 once more with the same arguments, show the new table and run its new line once. If that go ends with exit code 3 as well, show its lines and wait for him: never loop.
- Exit code 4 (No, or no answer): say what the output says and wait for him: nothing was loaded, or, at a RESUME box, the load stays interrupted.
- The output holds `Its resume: ` (exit code 2, or 1): a load was cut off and has to be finished first. Show the output unchanged. The text after `Its resume: ` is the line that resumes it: run it, as in this step, only when he says so, and only if it starts with `python`.
- If the permission layer refuses the line: do not retry, rephrase or wrap it. If he himself answered No to Claude Code's question, nothing was loaded: wait for him. Otherwise tell him to double-click `business_data/ops/loads/<run>/go.cmd` in the project folder (the check printed its full path, on the line that starts `If Claude may not start it`): the same box opens. When he says he has answered it, go to Step 3.
- Exit code 1 or 2, or anything else: show the last lines unchanged and wait.

## Step 3. Follow
Run `python -m scripts.ops load status --wait` again and again until its last line says FINISHED, FAILED, INTERRUPTED or NOT STARTED. Show only the new lines each time. `RUNNING at delivery` means the load has ended and its e-mail is being made: keep following. `RUNNING at wait_tick` after a line `Waiting about N min, to write at about HH:MM` is the load waiting on purpose for a quiet moment (never in the half hour around production's 8 o'clock run): tell him at once at what time it will write, and keep following.

## Step 4. Finish
- FINISHED: if a line says that something needs a look, tell him which. Say in one sentence what the line that starts `Delivery:` says. Then run `python -m scripts.ops brief` and show it: after a load it is the brief the load made, the figures of the e-mail. Then run `python -m scripts.ops stock` and show its screen in a code block of its own: it makes the day's Stock & Order Plan workbook and names it on its FILE line; say in one sentence where the file is. If that screen fails, say so in one sentence: the load itself is done, and nothing is run again.
- A line that holds `Its retry: ` (FINISHED or FAILED): the e-mail did not go, and the load is not touched by that. The text after `Its retry: ` sends it again and loads nothing: run it only when he says so, and only if it starts with `python`. Never send a load's e-mail a second time unless he asks for exactly that.
- A cut-off `Delivery:` line that says `No retry is needed` (a newer load has its own e-mail) or `No retry:` (this load's brief can no longer be sent): say what the line says, in one sentence; there is nothing to run.
- FAILED: show the last lines. If they say the load stopped and rolled back, nothing of this load was written: say so; /yq-load checks the folder again. If the verify failed, the data is loaded: name the metric the script names and do not go again.
- INTERRUPTED: show the last lines and tell him that typing /yq-load resumes it (Step 1). Nothing else loads until then.
- NOT STARTED: the load did not begin. Show the lines, say nothing was loaded and wait.
- End with what he may notice: the portal follows by itself within minutes, and no deploy is needed.
