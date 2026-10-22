"""Personal ID numbers masked in free text: the loader's mask, standard library only.

Staff type customers' CPR numbers (the Bahrain personal ID: 9 digits), phone numbers with their country code, bank
account and card numbers into Focus narrations. Every run of 9 digits or more is masked keeping its last 3 digits, so
a person can still tell two apart: a run of exactly 9 digits becomes six stars and its last 3 (it keeps its length), a
run of 10 digits or more becomes twelve stars and its last 3 (the length is not kept). A "run" is digits with no digit
on either side, so the order number the storekeeper types (YQ-2609-0019, YQ 2609 0019, YQ26090019: 4- and 8-digit
runs) and invoice / voucher numbers pass untouched, and masked text never matches again (it holds no run of 9 digits,
so masking twice changes nothing). [0-9], never \\d: Python's \\d also matches Arabic-Indic digits, Postgres's [0-9]
does not.

Until release r10 the long rule began at 15 digits, so a run of 10 to 14 digits (a phone number with its country code,
a bank account number) stayed in clear; r10 masks it.

Known limits, the same in Python and in SQL, so the two never disagree on them:
  * grouped digits are not masked: "4111 1111 1111 1111" or "1234-5678-9012" is a row of short runs, each below 9;
  * Arabic-Indic and other non-ASCII digits are not masked: [0-9] is ASCII only;
  * a run of 8 digits or fewer stays as typed (the order numbers are 8-digit runs), and so does a local phone number
    typed without its country code.

Who masks with it:
  scripts/ingest.py           at parse time (the Sales Day Book and the Stock_ledger narrations); it re-exports these
                              names unchanged, so `from scripts.ingest import mask_personal_ids` keeps working
  scripts/ops/ledger.py       before a transfer narration is hashed for the pairing (the file and the database path)
  app/transfer_audit.py       the portal's pairing, the same hash from the database's rows
  scripts/ops/recon_blocks.py a name from the data before it is printed

Its own module (release 2b) so that a reader needs no pandas for it: the API pairs the transfers once per data load
with this mask, and importing scripts.ingest costs pandas. The SQL twin of the two patterns lives in
scripts/r10_long_number_mask_migration.sql: v_sales.narration, and the data step over the stored narrations
(order_lines, stock_movements and the batch importer's copies of both); tests/test_r10_long_number_mask.py holds every
spelling there equal to these, and the file's own self-check proves it on the database. The earlier rule (9 digits, or
15 and more) is spelt in scripts/r7_narration_mask_migration.sql, r7_narration_mask_data_migration.sql and
r9_stock_moves_migration.sql: history, held to that rule by tests/test_r7b_security.py (docs/MIGRATIONS.md: r10 owns
v_sales now).
"""
from __future__ import annotations

import re

PERSONAL_ID_LONG = re.compile(r"(?<![0-9])[0-9]{7,}([0-9]{3})(?![0-9])")     # 10 digits or more (r10; R7b: 15)
PERSONAL_ID_CPR = re.compile(r"(?<![0-9])[0-9]{6}([0-9]{3})(?![0-9])")       # exactly 9 digits
MASK_LONG, MASK_CPR = "*" * 12, "*" * 6


def mask_personal_ids(text: str | None) -> str | None:
    """Narration with every run of 9 digits or more masked, its last 3 digits kept: exactly 9 -> 6 stars + 3,
    10 or more -> 12 stars + 3. Shorter runs, grouped digits and non-ASCII digits stay as they are."""
    if not text:
        return text
    text = PERSONAL_ID_LONG.sub(lambda m: MASK_LONG + m.group(1), text)
    return PERSONAL_ID_CPR.sub(lambda m: MASK_CPR + m.group(1), text)
