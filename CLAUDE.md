## Where am I?
- If the working folder path contains `.claude/worktrees/` (backslashes on Windows), this is a code worktree. The main-folder rules below do not apply: work on your branch as usual. Never run /yq-load, scripts.refresh, scripts.load_supabase, scripts.apply_sql or a deploy script from here: the main folder's `.env` is found automatically, so they would reach production.
- Otherwise, if this folder holds `business_data/` and the drop folder (`Uplaods/`, or `Uploads/`), it is the main project folder. It stays on branch `main`; code changes are made in worktrees.
- Any other checkout (another clone, a worktree somewhere else) is a code checkout too: work on your branch as usual and run none of the commands named in the first rule.

## Focus reports (main folder only)
- Reports are loaded only through /yq-load. When asked in plain words to load reports, answer with the exact line to type: `/yq-load <ddmmyy>`.
- Never run scripts.refresh, scripts.ingest or scripts.load_supabase directly. Never edit anything in `Uplaods/` (the owner's drop folders).
- A rehearsal of the load, when he asks for one in plain words, is in `docs/OPS_SKILLS.md`, section 2: it loads nothing.

## Skills (`.claude/skills/`)
- `/yq-load [ddmmyy]`: check a drop folder, ask, load, verify, brief, then the e-mail. Owner only.
- `/yq-brief [window] [rep:<name>]`: sales brief and what to do today; `/yq-brief team` prints the team message for him to copy. Asked whether the data is up to date or what is waiting, it runs `python -m scripts.ops status` (read-only).
- `/yq-stock [reorder|ageing|<code>]`: the main store's stock and cover, what to order, the fast movers in stock, ageing; one item by its code (read-only).
- More skills arrive in later phases and are listed here once they exist.

## Standing rules
- When a command opens with `LOAD INTERRUPTED` or `LAST LOAD FAILED`, tell the owner first: typing /yq-load in the main folder is what settles it. Never clear a lock or a load's folder unless he asks for exactly that.
- A load's e-mail goes once: never send it again, or send anything to WhatsApp, unless he asks for exactly that.
- `docs/CLAUDE.md`: the standing orders (stack, data rules, migrations).
- `docs/OPS_SKILLS.md`: what each command does, the one-time settings block, definitions.
- This repository is public: no names, phone numbers, ID numbers, prices or totals in anything committed.
