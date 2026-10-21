"""Personal ID numbers masked in free text: the loader's mask, standard library only.

Staff type customers' CPR numbers (the Bahrain personal ID: 9 digits) and card / account numbers (15 digits and
more) into Focus narrations. Each is masked keeping its last 3 digits, so a person can still tell two apart: a run of
9 digits becomes six stars and its last 3 (it keeps its length), a run of 15 digits or more becomes twelve stars and
its last 3 (the length is not kept). A "run" is digits with no digit on either side, so the order number the
storekeeper types (YQ-2609-0019, YQ 2609 0019, YQ26090019: 4- and 8-digit runs) and invoice / voucher numbers pass
untouched, and masked text never matches again (masking twice changes nothing). [0-9], never \\d: Python's \\d also
matches Arabic-Indic digits, Postgres's [0-9] does not.

Who masks with it:
  scripts/ingest.py           at parse time (the Sales Day Book and the Stock_ledger narrations); it re-exports these
                              names unchanged, so `from scripts.ingest import mask_personal_ids` keeps working
  scripts/ops/ledger.py       before a transfer narration is hashed for the pairing (the file and the database path)
  app/transfer_audit.py       the portal's pairing, the same hash from the database's rows
  scripts/ops/recon_blocks.py a name from the data before it is printed

Its own module (release 2b) so that a reader needs no pandas for it: the API pairs the transfers once per data load
with this mask, and importing scripts.ingest costs pandas. The SQL twin of the two patterns lives in
scripts/r7_narration_mask_migration.sql (v_sales.narration), scripts/r7_narration_mask_data_migration.sql and
scripts/r9_stock_moves_migration.sql (the stock ledger's narration); tests/test_r7b_security.py and
tests/test_r9_stock_moves.py hold them equal to these.
"""
from __future__ import annotations

import re

PERSONAL_ID_LONG = re.compile(r"(?<![0-9])[0-9]{12,}([0-9]{3})(?![0-9])")
PERSONAL_ID_CPR = re.compile(r"(?<![0-9])[0-9]{6}([0-9]{3})(?![0-9])")
MASK_LONG, MASK_CPR = "*" * 12, "*" * 6


def mask_personal_ids(text: str | None) -> str | None:
    """Narration with every 9-digit run and every run of 15+ digits masked (last 3 kept)."""
    if not text:
        return text
    text = PERSONAL_ID_LONG.sub(lambda m: MASK_LONG + m.group(1), text)
    return PERSONAL_ID_CPR.sub(lambda m: MASK_CPR + m.group(1), text)
