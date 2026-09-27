# Weekly AI Head (release R7b, plan §19 / §20 / §22)

A business review the owner runs in Claude Code on his own PC, once a week (and once a month). The
data comes from a **read-only pack**; Claude writes the report and proposes a Top 5; **the owner
decides**; only then are the approved statements loaded for management to see.

```
/weekly-review                  (Claude Code, in this repo)  → .claude/commands/weekly-review.md
/monthly-review 2026-09                                      → .claude/commands/monthly-review.md
```

| Piece | What it does | Writes |
|---|---|---|
| `scripts/r7b_ai_head_migration.sql` | 14 `v_agent_*` views (no phone, email, token, IP or raw device id), the `ai_insights` table, the `ai_head_ro` login | the schema, once (integrator) |
| `python -m scripts.ai_head.pack` | the dated pack `exports/ai_head/<week-ending>/` (CSV / JSON, phones masked) | local files only (gitignored) |
| `python -m scripts.ai_head.render <folder>` | `weekly.html` → `weekly.pdf`, the CSVs → `weekly.xlsx` | local files only |
| `python -m scripts.ai_head.load_insights <file>` | validates `insights.json`; dry run by default | `ai_insights`, only with `--commit` |
| `GET /management/insights` | what the owner approved (admin: everything) | nothing |

## One-time setup

1. **Integrator** applies the migration (rehearse first):
   ```
   python -m scripts.apply_sql scripts/r7b_ai_head_migration.sql --rehearse
   python -m scripts.apply_sql scripts/r7b_ai_head_migration.sql
   python -m scripts.audit_grants
   ```
2. **Owner** gives the login a password — Supabase dashboard → SQL editor, once:
   ```sql
   alter role ai_head_ro password '<a long random secret>';
   ```
   The migration never sets one: until this runs nobody can log in as `ai_head_ro`.
3. **Owner** adds the login to `.env` (never committed), next to `DATABASE_URL`: the same host and port
   as the session-pooler URI, with the user `ai_head_ro.<project-ref>` (the part after `postgres.` in
   `DATABASE_URL`'s user name):
   ```
   AI_HEAD_DATABASE_URL=postgresql://ai_head_ro.<project-ref>:<password>@<pooler-host>:5432/postgres
   ```
4. Check it: `python -m scripts.ai_head.pack` prints `connection: ai_head_ro as ai_head_ro`. Without
   `AI_HEAD_DATABASE_URL` the pack still works on `DATABASE_URL` inside a read-only transaction and
   says so with a warning in the pack and in the report's Data quality section.

What the login can do: SELECT on the 14 `v_agent_*` views and nothing else — it is a member of no
role (not of `yq_readonly`, which reads customer phones and owns the assistant's SECURITY DEFINER
functions), every session starts read-only, a statement stops after 15 s, an idle transaction after
60 s, and it holds at most 3 connections. Rotate the password with the same `alter role … password`;
lock it out at once with `alter role ai_head_ro nologin`.

## The Sunday routine (about 20 minutes)

The week runs Sunday–Saturday (Bahrain). On Sunday morning:

1. **Upload the latest Focus daily reports** as usual (Data page). The review leads with the data
   trust gate: if the last upload is older than 3 days it says the figures are stale before anything else.
2. **Run `/weekly-review`** in Claude Code (this folder). It builds the pack
   (`python -m scripts.ai_head.pack --verify`), analyses it in the §19 format, researches only the top
   1–3 market items, writes `weekly.html` + `insights.json` into `exports/ai_head/<week-ending>/`,
   prints `weekly.pdf` / `weekly.xlsx`, and shows you the Top 5.
3. **Decide the Top 5** — tell Claude which to approve and which to reject. It runs
   `load_insights` as a dry run, shows you the result, and only on your "yes" runs it with `--commit`.
   Management sees approved statements only.
4. **The management email** (optional, your call): preview to yourself, then send.
   ```
   python -m scripts.weekly_report --send --preview --to you@example.com
   python -m scripts.weekly_report --send --to <the list you choose>
   ```

Once a month, on or after the 1st, after the month's last Focus upload: `/monthly-review YYYY-MM`.
It refuses (and says why) until an export dated on or after the 1st of the next month is loaded.

## Scheduling the management email (Windows Task Scheduler)

The review itself needs you (it asks for your decisions), so it is never scheduled. The management
email can be, once you have chosen the recipients. From a Command Prompt in this folder:

```
schtasks /Create /TN "YQ weekly report" /SC WEEKLY /D SUN /ST 09:00 /F /TR "\"%CD%\scripts\ai_head\weekly_report_task.cmd\" \"a@example.com,b@example.com\""
```

`scripts\ai_head\weekly_report_task.cmd` runs `python -m scripts.weekly_report --send --to <list>`
from the repository folder and appends the output to `%LOCALAPPDATA%\yq_weekly_report.log`. Replace
the two example addresses with your list (comma-separated, no spaces). Check it once with
`schtasks /Run /TN "YQ weekly report"`, and remove it with `schtasks /Delete /TN "YQ weekly report" /F`.
The task runs only while you are signed in unless you add `/RU <you> /RP` (Windows asks for the
password); `python` must be on the PATH of that account (or put the full path to `python.exe` in the
`.cmd`). Test with `--preview --to` yourself before scheduling the real list.

## What the pack contains

`manifest.json` lists every file with its row count and a one-line description; `trust.json` is the
data trust gate (read first). The rest: `weekly_metrics.json` (the numbers of the management email,
from `scripts/weekly_report.compute()`), `focus_context.json`, `anomalies.json`, `items.json`,
`merchants.csv`, `regulars_due.csv`, `followups_holdout.csv`, `shop_orders.csv`, `shop_lines.csv`,
`funnel_daily.csv`, `search_demand.csv`, `rep_governance.csv`, `statements.csv`, `focus_links.csv`,
`items.csv`, `market_signals.csv`, `focus_lines_week.csv`, `insights_history.csv`, and with
`--verify` `verify_numbers.txt`; with `--monthly` also `monthly.json`.

Everything under `exports/` is gitignored (commercial data) — the repository is public.
