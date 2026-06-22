"""Template eval: verifies that each deterministic question matches the right template.

Does NOT hit the database — validates template regex logic only.
Run after any change to app/templates.py.

Usage:  python tests/run_eval.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.templates import match  # noqa: E402


def main() -> int:
    data = yaml.safe_load((ROOT / "tests" / "eval_set.yaml").read_text(encoding="utf-8"))
    questions = data["questions"]

    passed = failed = skipped = 0
    for q in questions:
        expected = q.get("expected_template")
        if expected is None:
            skipped += 1
            continue
        result = match(q["question"])
        if result is None:
            print(f"FAIL [{q['id']:02d}] No template matched: {q['question']!r}")
            failed += 1
        elif result[0] != expected:
            print(f"FAIL [{q['id']:02d}] Expected '{expected}', got '{result[0]}'")
            print(f"       question: {q['question']!r}")
            failed += 1
        else:
            passed += 1

    print(
        f"\nTemplate eval: {passed} passed  |  {failed} failed  "
        f"|  {skipped} LLM-path (skipped)"
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
