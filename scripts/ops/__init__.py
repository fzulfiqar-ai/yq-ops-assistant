"""YQ ops engine: the deterministic, READ-ONLY programs behind the /yq-* Claude Code skills.

    python -m scripts.ops <command> [...]

Every figure a skill shows is computed here (SQL / pandas, Decimal) and printed as a ready text
block; the skill only relays it. Nothing in this package writes to the database: scripts/ops/db.py
is the one module that opens a connection and it refuses every write (see its docstring and
tests/test_ops_readonly.py). The single program that changes production is scripts/yq_load_go.py,
which lives outside this package on purpose.

Definitions and the owner's settings block: docs/OPS_SKILLS.md. Thresholds, targets and logs live
in business_data/ops/ (git-ignored), never in this package.
"""
