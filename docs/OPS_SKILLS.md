# Ops skills: one command for each part of your routine

Written for you first; the last section is for the developer.

You type a short command in Claude Code, opened on the main project folder. A program works out every
number the way the portal does; Claude shows that screen as it is and adds words only. Nothing here
changes the live database except the load, and the load asks you every time. Findings are worded
"to check", "differs" or "not received yet", never as an accusation. The values you can change
(limits, sales steps) are kept in `business_data/ops/config.json`, not on this page.

## 1. What each command is for, and when to type it

| You type | When | You get |
|---|---|---|
| `/yq-load` or `/yq-load <ddmmyy>` | You have put Focus reports in a folder named with the date, for example `Uplaods/<ddmmyy>/`. With no date it takes the newest folder. | One table. Each file is marked: loaded; loaded in part (only the main-warehouse block of the stock file); not loaded this time; or a question for you. If a check fails the load stops and says why. After you click **Yes** it loads, verifies, and prints your brief. Then it e-mails you the brief and makes the team message (section 2 sets the e-mail up). |
| `/yq-brief` | Any morning. Or ask in plain words: how were sales yesterday, how is a salesman doing, what needs me today. | Sales for the latest day, the last 3 days, the last 7 days, the last full week and the month, the pace against your KPI step, accounts and salesmen to look at, sold-out items that are still in demand, and up to five things to do today. The first brief after a load is the one the load made: the same figures as your e-mail. |
| `/yq-stock`, `/yq-stock reorder`, `/yq-stock ageing` or `/yq-stock <code>` | When you plan an order. Or ask in plain words: what should I order, what is running out, how many days of stock do I hold, which stock is old. | The main store's stock as the portal shows it and at cost, the days of cover against KPI 1, the order list (what to order and how much, and the lead time it rests on), how often the fast movers were in stock, and the ageing in one line. `reorder` shows the whole order list, the supplier invoices the lead time is measured on and the fast movers with their sales ranking; `ageing` the ageing table; an item's code that one item. No purchase order is drafted (section 4 says how each figure is worked out). |
| Ask in plain words: *where do things stand?* | Before you start, or when something looks stuck. | Five lines: the data dates and the last load; this folder against the live site; the load lock; reports waiting; open items on the action list. (Claude runs `python -m scripts.ops status`.) |

`/yq-brief` on its own is the whole morning screen. Name a window (`day`, `3d`, `7d`, `week`,
`last-week`, `mtd`, `last-month`, a month as `YYYY-MM`, or two days as `YYYY-MM-DD..YYYY-MM-DD`) or
one salesman (`rep:<name>`) to see just that, in detail: the total, each salesman, the largest
accounts and products (`top:<N>` sets how many of them). `week` is this week so far; `last-week` is
the last full week. If nothing new has been loaded since your last brief it prints two lines; type
`/yq-brief again` for the whole screen anyway. `/yq-brief team` prints the team message the last load
made, in a block you can copy into the salesmen's group (section 4 says what it holds).

The rest of the routine (the weekly and monthly workbooks, the movement checks, the full action
list, the KPI scorecard) follows in later phases. This table gets a row for each one when it
exists. After a new command is added, type `/reload-skills` (or restart Claude Code) so it appears.

`/yq-load` is yours alone: Claude does not start it by itself. If you ask Claude in plain words to
load the reports, it answers with the line to type.

**Each time you load**
1. Put the daily Focus reports in the dated folder (see `docs/FOCUS_EXPORT.md`): the price book when
   prices change and, with the first load of a new month, the Ledger report of the month just ended.
2. Type `/yq-load`. Read the table. If a line asks for a decision, answer it in words (section 3).
3. Once you have pasted the block in section 2, Claude Code first asks you to approve the load:
   answer **Yes**. Then a Windows box titled `YQ load <ddmmyy>` lists the folder, one line per file
   and the figures expected after the load. What the check flagged is there too, on lines that start
   `ATTENTION`, and anything you asked for on purpose, on a line that starts `ON YOUR WORD`. **Yes**
   loads. **No**, closing the box, or no answer changes nothing. A **Yes** in the first three
   seconds does not count (it could be a key you typed for another window): the box comes back and
   says so.
4. Keep the PC plugged in and awake until it says the load finished, and do not upload on the Data
   page meanwhile. If Claude tells you to keep the Claude Code window open, do: on this PC the load
   would stop with it. The load then prints your brief, e-mails it to you and makes the team message
   (the line that starts `Delivery:` says what went out). The portal follows by itself within minutes;
   nothing needs deploying.
5. If a command opens with `LOAD INTERRUPTED` or `LAST LOAD FAILED`, type `/yq-load`. Section 3 says
   what each of the two means.
6. If Claude says it may not start the load, it names a file for you to double-click: `go.cmd`, in
   `business_data/ops/loads/<run>/` of the project folder (the run is named in the heading of the
   table, and the check prints the file's full path just above its go line). It runs the same line,
   so the same Yes/No box opens, and it waits for a key at the end. Then tell Claude that you have
   answered the box.
7. After each shipment, upload the purchase order, the MRN XML and the supplier invoice on the Orders
   page. The load does not take them.

## 2. Paste once and rehearse first

**Paste once.** Open `C:\Users\<you>\.claude\settings.json` in Notepad and paste this block straight
after the first `{` of the file, followed by a comma when other settings come after it:

```json
"permissions": {
  "ask": [
    "Bash(python -m scripts.yq_load_go*)",
    "PowerShell(python -m scripts.yq_load_go*)",
    "Bash(python -m scripts.refresh*)",
    "PowerShell(python -m scripts.refresh*)",
    "Bash(python -m scripts.load_supabase*)",
    "PowerShell(python -m scripts.load_supabase*)"
  ]
}
```

Save the file; Claude Code reads the change by itself (if the file does not exist yet, create it with
`{`, the block and `}`). What it changes: from then on Claude Code asks you before a load that is
started in its usual form, even if a broader "allow" rule exists or someone later clicks "don't ask
again". Answer **Yes**; never choose "don't ask again". It is a second question, not a lock: a
command typed another way is not caught by these rules.
The six rules cover the load program and the two older loaders (`scripts.refresh` and
`scripts.load_supabase`), each in the two shells Claude Code can use. If the file already has a
`"permissions"` section, do not paste a second one: add the six lines to the `"ask"` list inside it,
or ask Claude to do it with you. The read-only commands (check, status, brief) need nothing pasted.

The Windows box is the real gate. The load program shows it itself, so it appears however the
program is started and however Claude Code is set. The two older loaders have no box: for them the
pasted rules and the standing order in `CLAUDE.md` never to run them are what stands in the way.
The load program also reads the Claude Code settings each time, a rehearsal included: if a rule there
would let a load start without asking you (one that allows every `python` command, for example), it
refuses to start, names the entry to delete and the file it is in, and loads nothing.

**Rehearse first.** Before the first real load, ask Claude to rehearse it. Claude runs
`python -m scripts.yq_load_go --rehearse` in the main project folder. A rehearsal loads nothing and
never touches the database: every step is a stand-in that only waits, for about three minutes in
all. The lock, the journal and the Windows box are the real ones, so it shows whether a load keeps
running on this PC once the command that started it is gone. The first time, Claude leaves out
`--no-box`, so that you see the box (its title ends with REHEARSAL) and click **Yes**. Claude then
follows it with `python -m scripts.ops load status --wait`. Try three things, one rehearsal each:
1. Leave it alone. The command that started it comes back after a few seconds; the rehearsal must
   run on by itself.
2. Start another one and press Esc right after your **Yes**, while Claude's command is still
   running, so that it is cancelled.
3. Start a third one and close the Claude Code window. Open it again and ask where things stand.

Each time the status must come to `FINISHED`, with the line `REHEARSAL FINISHED`. If one of them
ends as `INTERRUPTED` instead, a real load would stop the same way: tell Claude before the first
load. (If Claude tells you at the start to keep the Claude Code window open until the rehearsal has
finished, the third trial is already answered: on this PC a load stops when that window closes.)
A rehearsal that was cut off needs nothing from you: it changed nothing, and the next load clears its
lock.

**Also once: the backup password and the e-mail.** The database is backed up before every load. Set
the backup password once, in a Command Prompt: `cmdkey /generic:YQ_BACKUP_PASSWORD /user:yq /pass` (it
asks for the password and shows nothing as you type). Keep a copy of it away from this PC: without it
no backup can be read. Until it is set, each backup is kept unencrypted on this PC and the Windows box
says so on a line that starts `ATTENTION`; once one backup has been encrypted, a load without the
password is refused before it writes anything. Each backup is read back whole before the load goes on.

The e-mail after each load needs two things. First a sending key of the mail service, Resend (a key
with *Sending access* only, on the company's sending domain), set the same way:
`cmdkey /generic:YQ_RESEND_API_KEY /user:resend /pass` (paste the key when it asks). Then the file
`business_data/ops/delivery.json`, which Claude writes with you: the sender, who gets the e-mail, the
test address, and `mode`: `test` for the first days (every e-mail goes to the test address only,
marked TEST), then `on`; `off` stops it. Ask Claude for one test e-mail of the last load before you
switch it on. The key is never written in a file or shown on the screen, and that file is never
committed.

## 3. What the load check's decisions mean

| Decision | What it means | What you can say |
|---|---|---|
| **LOAD** | The file passed its checks and will be loaded as it is. Anything noticed on the way (an invoice changed or removed since the last export, a voucher entered back-dated) is listed under the table as an item *to check*; it does not stop the load. | Nothing. |
| **LOAD (main-store block only)** | The stock file lists more warehouses than the portal counts. Only the main store's block (the Accessories Warehouse) is loaded, after the program has proved that the trimmed copy has that block's rows, units, value and date and equals Focus's own sub total. If it cannot prove that, it stops. | Nothing. |
| **HOLD BACK** | The profitability report is not loaded this time: its cost of sales is short of the stock ledger's by more than the allowed gap, or too large a share of sales sits on items with a blank cost (both limits are in `config.json`), or the drop has no stock ledger to check it against. The portal keeps the margins it already has, and later screens say *held back since* a date. Held back twice in a row, it goes on the action list. | "load profitability anyway" |
| **LEAVE OUT** | A price book that is not loaded: it is exactly what the portal already holds (no price added, changed or dropped), or you said to leave it out. | Nothing. |
| **NEEDS YOUR DECISION** | A price book that differs from the portal's. Each difference is listed row by row: a general price changed; a price for one van or one customer (shown with the general price and the invoices already issued at that rate); a dropped price; a scheduled price; an end date changed. It is one decision for the whole file. The default is to leave the file out and load the rest: with no answer that is what happens. | "load the price book", or "leave the price book out" (the same as saying nothing). |
| **NOT LOADED** | A file this command does not load, or a report missing from the drop. Supplier papers (purchase order, MRN XML, supplier invoice: upload them on the Orders page), photos and anything in a sub-folder are listed with where each goes. The month-end Ledger is kept for the monthly report. A missing report stays on its older data, and the table gives that date. | Nothing. Put the missing report in the folder and run it again if you want it. |
| **STOP** | Nothing from this folder is loaded and no go line is printed for it; the screen says why. The reasons are listed under this table. | Do what the line says, then type `/yq-load` again. |

Say these in your own words. Claude adds the matching setting and runs the check again, and the
choice is shown in the Windows box and written in that load's journal. The Windows box follows the
table at once: to answer a **NEEDS YOUR DECISION** line first, click **No**, say your decision, and
the check runs again.

**Why a check stops**
- The folder: a workbook of it is open in Excel, a file is still being written, a report is there
  twice, or the Focus reports sit in more than one sub-folder (*which set?*: say which).
- The reports are older than what is already loaded, or one of them ends before it. An older report
  is never loaded, because it would put older figures back: export it again from Focus.
- The programs: this folder is behind the live site (*pull it*: ask Claude to bring the folder up to
  date), a portal release is merged but not live (*deploy it, then load*: ask Claude to put the
  release live first), or a program file was changed on this PC (*tell Claude*).
- The loader would refuse: the sales file would remove many invoices from its own date span, a
  salesman with invoices in that span is missing from it (it looks like a filtered export), the stock
  file holds fewer warehouses than the last one, or the price book looks like only a part of the
  book. If the sales file or the stock file really is meant to be like that, say "replace on purpose"
  (sales) or "partial on purpose" (stock).
- A file or the database changed while the check ran: run it again.
- The live site or the database did not answer, or a load or a rehearsal is still running: wait a
  minute, then run it again.

**One check, one run.** Every check is a *run*, named `<ddmmyy>_<HHMM>`: its copies of the reports,
its table and its go line belong together. Only the newest check of a folder counts. A new check of
the same folder retires the earlier checks of it that were never loaded: their go lines no longer
work (the load program refuses them and says that a newer check replaced them), their `go.cmd` is
removed, and their copies move to `business_data/staging/superseded/`. A check whose load has
started, finished or failed is never touched.

**When a load did not finish.** Until a load has finished again, the brief and the status open with
one of two lines, and the load check says the same in its own words:
- `LOAD INTERRUPTED at <step>: run /yq-load`. A load was cut off before its end: the PC was switched
  off or went to sleep, for example. Type `/yq-load`. Claude does not check the folder again: it
  starts that same load once more, and the Windows box says **RESUME**. **Yes** runs the whole load
  again from the same files, with the backup already taken. **No** leaves it interrupted, and no
  other load can start until it is resumed. If a file or a program changed in the meantime, the
  cut-off load is closed instead and the folder is checked afresh.
- `LAST LOAD FAILED at <step> (run <run>)`. The last load stopped after it had begun to write, and
  no load has finished since, so the database may hold part of it. Type `/yq-load`: it starts a new
  load, and that load replaces the failed one. One case first: if the failed load said that its
  totals do not match the reports, ask Claude to look into those totals before anything is loaded
  again.

## 4. Definitions used by the brief

- **The basis.** Every sales figure is for Accessories: all divisions except SIM and Devices, giveaway
  items left out, ex-VAT, up to the *data date*, which is the last day with loaded sales (never
  today). Returns are not deducted. B2C is the two retail outlets; everyone else is B2B. Weeks run
  Sunday to Saturday. The sales pace counts the working days, Sunday to Thursday. "At cost" has one
  meaning everywhere: the latest landed cost, else the latest receipt rate. A line on another basis
  says so: the item for products selling below cost reads Focus's own cost of sales, so it prints the
  date of the profitability report and, when a load held a newer report back, *held back since* that
  day.
- **A part day.** When the last loaded sale day is also the day the reports were exported, that day
  is still being invoiced. The latest day is then the loaded sale day before it; the 3-day, 7-day
  and full-week lines stop at the day before it; the month to date keeps it; and the screen says so.
- **The last complete week.** The day the reports were exported is never complete; every day before
  it is, whether or not it sold anything. (When nothing on record says which day that was, the days
  up to the data date count as complete.) The 3-day, 7-day and full-week lines end on the last
  complete day, so the last full week is the last Sunday-to-Saturday week that is complete. Reports
  exported on a Sunday morning show the week that ended the day before, even when nothing sold on
  its Friday and Saturday; a week whose Saturday is the export day is not over yet. When those
  lines do not end on the data date, the screen says where they end and why.
- **Why the latest day and the last 3 days carry no percentage.** A selling day is only a handful of
  invoices, so one large invoice, or one late one, swings a percentage so far that it means nothing.
  For these two windows the brief shows the amount, the number of invoices and the largest invoice,
  beside the average business day of the last 30 days, and leaves the comparison to you.
- **Normal range.** For the last 7 days and the last full week the brief shows the average of the four
  full weeks before, and says whether the window is *inside*, *above* or *below* the normal range: the
  band that weekly sales usually stay in, worked out from the 12 full weeks before. Inside means
  ordinary ups and downs. Above or below means something changed and is worth a look.
- **Why a low week often cannot be judged.** The band cannot go below nought, and weekly sales swing
  so widely that its low end usually sits there. A low week then cannot be told from an ordinary one:
  the line reads *not above the normal range*, and one line under the table says to compare the week
  with the four-week average instead.
- **Month-end week.** A window that touches the last or the first five business days of a month is
  tagged *month-end week* and is not judged: invoicing bunches up there, so comparing it with an
  ordinary week would mislead.
- **Month to date.** Compared with the same business days last month, in percent. While the month has
  had no business day yet (it opened on a weekend) it is not compared.
- **The two targets.** *Your KPI step* is the monthly Accessories sales figure (ex-VAT) that you name.
  It is kept in `business_data/ops/config.json` with the month it starts from; to set or change it,
  tell Claude the figure and the month. *The portal's target* is the monthly sales target on the
  portal's Settings page. The pace line (what is still needed per remaining business day) uses your
  KPI step and says so; when the portal's target is different, the same line adds the pace against
  it. Until you name a step the pace line reads *KPI step not set* and gives the pace against the
  portal's target alone, and the first brief lists, once, the four monthly figures that exist today
  so that you can choose: your KPI sheet's step, the portal's target, the salesman tiers added up,
  and the average month of the last 12 months. This page gives no figure for that average: the brief
  prints the one the system works out from the loaded sales.
- **Accounts and salesmen to look at.** An account is listed when it bought last month and has not
  bought this month (cash customers are not counted), with the largest few by last month's value. A
  salesman is listed when his month to date is below the set share of the same business days last
  month, or when he has had no invoice for more than the set number of business days, which is the
  Command Centre's own rule (both settings are in `config.json`).
- **Stock.** Items that are sold out although they were still asked for in the last 60 days: the
  Command Centre's list of sold-out items with demand. What to order before an item runs out, and
  how many days of stock you hold, is on `/yq-stock` (its definitions follow this list); the brief
  gains its warnings in a later phase.
- **Do today.** At most five open items of the action list, the most pressing first; when more are
  open, the screen says how many. The Command Centre's attention items are put on that list by the
  brief. The full list, and marking an item done or explained, come with a later command. An item
  you have explained then stays off the list until it has grown by more than a quarter, has become
  more pressing, or names a voucher or an invoice that was not there when you explained it.
- **The e-mail after a load.** The brief's own figures, worked out once for the chat and the e-mail
  alike, and what a chat screen has no room for: each salesman's month beside the same days last
  month and against his first tier (for you alone), items in stock to push, shops due a re-order,
  money owed by age, orders waiting, what the load checked and how long it took. Only a load that
  wrote data sends one: after a clean load the whole brief; after one where something needs a look,
  or one that failed after it began to write, a short note with no figure. A load that wrote nothing
  sends nothing, and a load's e-mail goes once.
- **Actions.** The e-mail lists up to five, the most worthwhile first. Each says why, what it is worth
  (an amount or a count, and what that rests on), the next step, who does it and by when. What it is
  worth, how sure that is, how soon it is due and how much work it is set the order. *ACTION* is what
  the figures say; *TO CHECK* means something differs and the reason is not known: a question, never a
  finding.
- **NOT EVIDENCED.** A figure the portal holds no data for (shop visits, calls, quotations, the stock
  in the vans, and discounts for as long as Focus exports none) reads *NOT EVIDENCED*. It is never
  guessed.
- **The team message.** Written for the salesmen's group: the month so far as a share of your KPI
  step and what is still needed per working day (never a sales total), items in stock to push,
  today's priorities and the end-of-day ask. Salesmen are named only to praise them, never beside a
  figure. It never holds a customer's or a shop's name, money owed, a tier, a commission or a
  salesman's own figure: a message that would is not made, and the e-mail says why. You send it yourself: copy it from
  `/yq-brief team`, or press *Open in WhatsApp* in the e-mail, choose the group and send.

### Stock (`/yq-stock`)

- **The main store** is the Focus *Accessories Warehouse* (`warehouses.main` in `config.json`). An
  item counts when it has ever moved there, so SIM packs are out by that rule, never by a word in a
  name. The headline's items, units and selling value are the portal's own figures (the Inventory
  page's read of the newest stock balance). The stock ledger's balance of the main store is set
  beside them item by item, and an item where the two differ is listed *to check*. The vans and the
  two outlets come with the movement audit, in a later phase.
- **At cost** is the latest landed cost of the item's code, else the latest supplier receipt's rate,
  as everywhere. The portal's own figure (Focus average cost, else the latest landed cost) is printed
  beside it when the two differ. An item with neither is counted apart, never valued at nothing.
- **Units sold** are the Sales Invoice units of every warehouse (the vans and the outlets sell; the
  main store issues to them), customer returns not deducted.
- **Daily rate and cover.** An item's rate is the units sold in the last 90 days divided by the days
  of those on which the main store held it at the end of the day, never fewer than 30. A rate marked
  with a star rests on fewer than 30 days in stock, so it may understate the demand of an item that
  was out most of the time. Cover is the units on hand divided by the rate.
- **KPI 1, cover.** The aggregate cover is the stock at cost divided by the daily cost of sales at
  the same costs, with display items and new arrivals left out; beside it, the share of that value
  on items above the top of your KPI band, and the same figure on the last day of last month (at
  today's costs). New arrivals are items first received in the last 90 days: their rate is too
  short to judge, so they are a band of their own. The bands are by count and value: below the KPI
  band, inside it, above it up to 90 days, up to 180, up to a year, over a year, and no sale in the
  last 90 days.
- **The order list.** An item that sold in the last 90 days is listed when its cover is below the
  lead time plus the low end of your KPI band; the quantity brings it to the lead time plus the top
  of the band, less what is on order, in whole units. Goods on order are the promise lines you log,
  one per item from a proforma or a packing list, each counted until a receipt of that item is dated
  after it; until one is logged the list says *goods on order not deducted*. No purchase order is
  drafted.
- **The lead time** is measured: each supplier invoice on file (the proforma invoices uploaded on the
  Orders page) is set beside the main store's receipt, on or after its date, that holds the same
  quantity of most of its models (at least half of them); the days between the two are that
  shipment's lead time. With at least three
  shipments matched, the lead time is the median of the last ten, and the screen lists them with
  their basis; otherwise it is `stock.lead_time_days` from `config.json`, and the screen says so.
- **Fast movers** are your best sellers by units over the 12 full months before the month (how many:
  `stock.fast_movers`). Their availability is the share of SKU-days on which the main store's
  day-end balance was above nothing and covered at least one day of that item's rate; beside it the
  same for every item that sold (the wider basis), and the fast movers that were out on every day
  of the month.
- **Ageing.** The main store's units are placed on the item's supplier receipts, newest first, and
  aged by the days since that receipt: up to 90 days, up to 180, up to a year, over a year, and no
  receipt on file for units no receipt explains. A return from a van is no receipt, so it never
  makes stock younger. Each band is crossed with the days since the item last sold and carries a
  suggested action; the bands add up to the main store, display items on a row of their own.
- **Your settings** for this command in `config.json`: `stock.lead_time_days`, `stock.cover_kpi_days`
  (your KPI band, low and high), `stock.fast_movers` and `stock.display_items` (display and
  packaging, left out of cover, the order list and the ageing). While one of the two newer keys is
  missing, the plan's value stands in and the screen names the key.

## 5. Where things are kept

Everything in the first six rows sits in the project folder. None of it is in git: `.gitignore` covers
`business_data/`, `exports/` and the drop folders (`/Uplaods/`, `/Uploads/`), and the repository is
public.

| Folder | What is in it |
|---|---|
| `Uplaods/<ddmmyy>/` | Your drop folders. The commands only read them; they never change a file in them. |
| `business_data/ops/` | The commands' own notes: `config.json` (limits and KPI steps you can change), `state.json` (last load, last brief), `LOCK` (exists only while a load runs, or when one was cut off), `alerts.jsonl` (the action list), `delivery.json` (who gets the e-mail, and whether it is on), `backup.json` (the day backups began to be encrypted), `drills.jsonl` (each restore drill and its result) and `loads/<run>/` (for each check: what you were shown, `go.cmd`, a journal of the steps once it loads, and `delivery.jsonl`, what became of its e-mail). Later commands add `explained.json`, `kpi_log.jsonl` and `golden/`. |
| `business_data/staging/<run>/` | The copies a load works from. The copies of a check that a newer check of the same folder retired, and a loaded set that a newer load of the same day replaced, are in `superseded/`. |
| `business_data/Focus ERP <YYYY-MM-DD>/` | The set that is loaded now (the verify tool reads the newest). |
| `exports/ops/<data-date>/` | A text and a JSON copy of what each command printed for that data date, and in `delivery/<run>/` what each load delivered: `email.html` and `email.txt` (the e-mail as sent), `whatsapp.txt` (the team message), `whatsapp.html` (the same in a box you can change, with a copy button and one that opens WhatsApp) and `manifest.json` (a checksum of each, checked again before the e-mail goes). They carry customer and salesman names, which is one more reason they stay out of git. |
| `exports/reports/weekly/` and `exports/reports/monthly/` | The workbooks, once those commands exist. |
| `%LOCALAPPDATA%\yq-backups` | Backups, including the one taken before each load, and in `legacy\` the older backups that sat in OneDrive, encrypted. Outside OneDrive (see `docs/BACKUPS.md`). |

## 6. What these commands cannot do

- Time from a Stock Request to the Stock Issue: Focus exports no request vouchers.
- Who entered a voucher, and when: Focus exports no user or time on a voucher.
- Anything about counted stock, until someone counts it.
- They send one thing: the e-mail after a load, to the addresses in `business_data/ops/delivery.json`.
  They post nothing to WhatsApp. WhatsApp's own business service posts only to small groups (up to 8
  people) and only for a verified business account, and a program that drives WhatsApp Web breaks
  its rules and can get the number banned: the team message is sent by you (section 4).
- They do not run by themselves on a schedule, store briefs in the portal, draft purchase orders or
  make PDFs.

## 7. For the developer

- **Skills** are `.claude/skills/yq-*/SKILL.md`, one folder each. Frontmatter: `name`, `description`,
  `argument-hint`, `allowed-tools` (a YAML list) and, for `/yq-load` only,
  `disable-model-invocation: true`. Every string value is in double quotes, because Claude Code loads
  a skill with no fields at all when the YAML is malformed, and the owner-only flag goes with them.
  These five keys and no other, and none of them twice: Claude Code reads a repeated key as malformed
  YAML although PyYAML accepts it, so the test refuses both. No shell-command injection in the text,
  no `hooks`, no fork.
- **`allowed-tools`** lists each read-only command twice, as `Bash(python -m scripts.ops <command> *)`
  and `PowerShell(python -m scripts.ops <command> *)`, and pre-approves it only for the turn in which
  the skill runs. Nothing there may match the load program, `scripts.refresh` or
  `scripts.load_supabase`, in any form a permission rule can take (a bare tool name, a `*` anywhere,
  the older `prefix:*`). The one program that writes is `scripts/yq_load_go.py`, outside
  `scripts/ops` and named so that no `scripts.ops` rule can match it; the section 2 rules keep Claude
  Code asking before it.
- **The ask rules** of section 2 match the command text as the skills, `go.cmd` and `CLAUDE.md` spell
  it: `python -m scripts.<name> ...`. Another spelling of the same program (`py -m ...`,
  `python -X utf8 -m ...`, the path of the file) is not matched. For the load program the Windows box
  still asks, whatever the spelling; for the two older loaders the only other guard is the standing
  order in `CLAUDE.md` never to run them.
- **A skill** runs its command text exactly as written (`python` first; no `cd`, no variable prefix,
  no pipe), relays the output unchanged and types no number of its own. Each `SKILL.md` stays under
  70 lines and `CLAUDE.md` under 30. The commands themselves are in `scripts/ops` (read-only by
  construction, see `tests/test_ops_readonly.py`).
- **The seam with the load programs.** `/yq-load` acts on what they print, and on nothing else:
  the last line `GO: ...` of a check, and the line above it that starts
  `If Claude may not start it: double-click` (a check that stops exits with code 2 and prints no go
  line, but for the next case); the sentence `LOAD INTERRUPTED at <step> (run <run>)`, after which
  the check's last line is that run's own go line, so the resume is Step 2 unchanged; a line that
  starts `The last load (run <run>) failed at <step> after it had begun to write`, the check's way of
  saying what the brief and the status open with as `LAST LOAD FAILED at <step> (run <run>)`; the
  exit code of the go (0 started, 1 error, 2 refused, 3 check again, 4 no answer at the box); the
  words `Its resume: ` in what the go says, which the line that resumes a cut-off load follows; and
  the last line of `load status` (`RUNNING`, `FINISHED`, `FAILED`, `INTERRUPTED` or `NOT STARTED`;
  it exits with code 2 on `FAILED` and `INTERRUPTED`).
  `tests/test_ops_skills.py` reads the decision words, the options, the exit codes, the window words
  and these lines from the programs and compares the skills and this page with them, so change the
  two together.
- **When a load goes wrong** (`scripts/yq_load_go.py`, tested in `tests/test_ops_load_go.py`): the
  one identical re-run compares the files and the commits with the table again first; a program file
  changed in the folder and not committed counts as a change of the programs; the worker runs in a
  Windows job of its own, so ending it ends its loader too, and the lock names the loader while it
  runs; a load that had begun keeps its lock until its closing line is in the journal;
  `LAST LOAD FAILED` stays until a load finishes, whatever was checked or rehearsed in between.
  A resume does not compare the database's fingerprint, but it reads what was written since the
  check (`loadplan.foreign_writes`): a row of `ingest_runs` that names another folder or a batch, a
  loader row more than this run's own starts of its refresh, or an upload in `audit_log` since the
  go first started (one that recognised no report wrote nothing and does not count) closes the
  cut-off load, and a new check is needed. The lock names the PC it was taken on: a lock from
  another PC is never judged, resumed or cleared on this one, by a load or a rehearsal, and the check
  stops and names that PC; a lock that is no lock and was written in the last minutes is left alone
  too. What is left: two PCs that start a load within OneDrive's sync delay of each other, and an
  upload that runs at the very moment of a resume. Closing that needs a lock in the database that the
  portal's upload takes as well, a change of the portal for later. The start window after a tick is
  worked out from the slowest of the last five refreshes on record.
- **After the load** (`Job.deliver_after` in `scripts/yq_load_go.py`): once the closing row is in the
  journal and the lock is gone, the worker runs `python -m scripts.ops deliver --run <run>` (the brief,
  the e-mail and the team message, worked out once), then `python -m scripts.yq_send --run <run> --auto`,
  and writes one row that starts `Delivery:`. Nothing of it changes the load's result, journal or
  lock: its rows are in `loads/<run>/delivery.jsonl`. Until that row is written, `load status` reads
  `RUNNING at delivery` (after a load that finished, or one that failed after it began to write), for
  up to ten minutes; then it shows the `Delivery:` line, and the brief prints the stored brief once. A
  line that holds `Its retry: ` is followed by the line that sends the e-mail again; it loads nothing.
  A retry never makes a full brief from figures that are no longer that load's: once a newer load is
  on record, a later load failed, or anything was written to the database since, it is refused.
  `scripts/yq_send.py` is the one program that sends: exit code 0 sent or nothing to send, 1 not sent
  (recorded, with its retry), 2 refused (that load's e-mail went already, to any list of addresses, and
  `--resend` asks at the Windows box first; or it is not that load's own brief any more). `--test`
  sends to the test address only and never counts as sent. `scripts/ops` imports none of `scripts/yq_send.py`,
  `scripts/yq_secrets.py` and `scripts/yq_backup.py` (`tests/test_ops_readonly.py`), and the engine
  paths that decide a deploy leave out every `scripts/yq_*.py`.
- **Backups** (`scripts/yq_backup.py`): `drill` restores the newest pre-load archive into a
  throwaway PostgreSQL on this PC (the database address must be on this PC; the schema is read from
  production, read-only) and compares every table's rows with the archive's manifest; `legacy` packs
  the old plain backups, reads each one back and only then deletes the plain copy.
- **Adding a skill:** a new folder with the same rules, one row in section 1, one line in
  `CLAUDE.md`, then `python -m tests.test_ops_skills` and `python -m tests.test_ops_readonly`.
- **Before a commit:** `python -m scripts.ops selftest --repo-clean`. It refuses a staged file outside
  this work's paths, one that holds a name read from the loaded Focus files, and one that holds an
  amount of a thousand or more written with three decimals. Its name list is
  `business_data/ops/denylist.json`, built from the loaded Focus files; system words of Focus (cash,
  credit, total, customer and the like) are never taken for a name. `tests/test_ops_skills.py` runs
  the same scan on the files of this work with an invented list in a temporary folder, and never
  reads yours.
- **XlsxWriter** (for the report workbooks) is pinned in `requirements-dev.txt` only. It is not in
  `requirements.txt` or `requirements.lock`, so it never enters the API image.
