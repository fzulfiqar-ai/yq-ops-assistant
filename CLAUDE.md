## Where am I?
- If the working folder path contains `.claude/worktrees/` (backslashes on Windows), this is a code worktree. The main-folder rules below do not apply: work on your branch as usual. Never run /yq-load, scripts.refresh, scripts.load_supabase, scripts.apply_sql or a deploy script from here: the main folder's `.env` is found automatically, so they would reach production. A stock run here makes no day file unless `--data-root` names another folder (the file belongs to the main folder).
- Otherwise, if this folder holds `business_data/` and the drop folder (`Uplaods/`, or `Uploads/`), it is the main project folder. It stays on branch `main`; code changes are made in worktrees.
- Any other checkout (another clone, a worktree somewhere else) is a code checkout too: work on your branch as usual and run none of the commands named in the first rule.

## Focus reports (main folder only)
- Reports are loaded only through /yq-load. When asked in plain words to load reports, answer with the exact line to type: `/yq-load <ddmmyy>`.
- Never run scripts.refresh, scripts.ingest or scripts.load_supabase directly. Never edit anything in `Uplaods/` (the owner's drop folders).
- A rehearsal of the load, when he asks for one in plain words, is in `docs/OPS_SKILLS.md`, section 2: it loads nothing.

## Skills (`.claude/skills/`)
- `/yq-load [ddmmyy]`: check a drop folder, ask, load, verify, brief, then the e-mail and the day's stock file. Owner only.
- `/yq-brief [window] [rep:<name>]`: sales brief and what to do today; `/yq-brief team` prints the team message for him to copy. Asked whether the data is up to date or what is waiting, it runs `python -m scripts.ops status` (read-only).
- `/yq-week [YYYY-MM-DD]`: the weekly sales workbook for a whole week (the last full one by default), under `exports/reports/weekly/`, then the AI weekly review of the same week; its Top 5 is stored only after his answers.
- `/yq-month [YYYY-MM]`: the monthly sales workbook (the layout of his September one) for a whole month, under `exports/reports/monthly/`; its comment lines stay DRAFT for now.
- `/yq-stock [reorder|ageing|damage|file|<code>]`: the main store's stock and cover, what to order per supplier (counting the vans and outlets), the fast movers in stock, ageing and how the old and price-cut items sell, the damage store; the day's editable Stock & Order Plan workbook under `exports/reports/stock/` (once per data date); one item by its code (read-only).
- `/yq-moves [7d|mtd|YYYY-MM|A..B] [rep:<name>]`: transfers between the main store and the vans and outlets, paired by what the two vouchers hold; each van's and outlet's stock from opening to closing with its check; what is to check in BHD (read-only).
- `/yq-watch [all|<id>|explain <id> <words>|done <id> [words]|replay]`: the action list after each load: transfers to check as ACT or CHECK with their BHD, the load check's and the Command Centre's items read-only; explain or done only on his words.
- More skills arrive in later phases and are listed here once they exist.

## Standing rules
- When a command opens with `LOAD INTERRUPTED` or `LAST LOAD FAILED`, tell the owner first: typing /yq-load in the main folder is what settles it. Never clear a lock or a load's folder unless he asks for exactly that.
- A load's e-mail goes once: never send it again, or send anything to WhatsApp, unless he asks for exactly that.
- `docs/CLAUDE.md`: the standing orders (stack, data rules, migrations).
- `docs/OPS_SKILLS.md`: what each command does, the one-time settings block, definitions.
- `docs/STATUS.md`: where the work stands, open items and the order of work.
- This repository is public: no names, phone numbers, ID numbers, prices or totals in anything committed.
