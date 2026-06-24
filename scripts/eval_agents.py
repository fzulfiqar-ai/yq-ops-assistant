"""Eval harness for the agentic router — catch routing regressions as agents grow.

Checks that questions route to the right mode and (for 'agents') consult the right
specialists. Smalltalk is handled before routing; data/agents go through the real router
(LLM + deterministic keyword fallback). Run:  python -m scripts.eval_agents
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv()

from app.agents import AGENTS  # noqa: E402
from app.ai import _smalltalk  # noqa: E402
from app.orchestrator import route, _keyword_route  # noqa: E402

# (question, expected_mode, expected_agents) — agents check = non-empty intersection.
CASES: list[tuple[str, str, list[str]]] = [
    ("hi", "smalltalk", []),
    ("thanks, great work", "smalltalk", []),
    ("who are you?", "smalltalk", []),
    ("total outstanding receivables", "data", []),
    ("who owes us money?", "agents", ["collections"]),
    ("which items are out of stock?", "agents", ["inventory"]),
    ("who owes us money and are we low on stock?", "agents", ["collections", "inventory"]),
    ("which products are selling below cost?", "agents", ["margin", "anomaly"]),
    ("how is each salesman doing?", "agents", ["salesman_performance"]),
    ("which customers are churning?", "agents", ["customer_health"]),
    ("what's our cash position / receivables aging?", "agents", ["cashflow"]),
]


def main() -> int:
    allowed = list(AGENTS.keys())
    ok = True
    print(f"{'QUESTION':46} {'MODE':9} AGENTS / RESULT")
    print("-" * 92)
    for q, exp_mode, exp_agents in CASES:
        if _smalltalk(q):
            mode, agents = "smalltalk", []
        else:
            mode, agents, _ = route(q, allowed)
        mode_ok = mode == exp_mode
        agents_ok = (not exp_agents) or bool(set(exp_agents) & set(agents))
        passed = mode_ok and agents_ok
        ok = ok and passed
        tag = "PASS" if passed else f"FAIL (exp {exp_mode} {exp_agents})"
        print(f"{q[:45]:46} {mode:9} {agents}  {tag}")
    print("-" * 92)
    # The deterministic keyword router is the guaranteed safety net — it must never regress.
    kw_ok = (set(_keyword_route("who owes us money and are we low on stock?", allowed))
             >= {"collections", "inventory"})
    print(f"keyword-router safety net: {'PASS' if kw_ok else 'FAIL'}")
    print("ALL PASS" if (ok and kw_ok) else "SOME FAIL (LLM routing can vary; keyword net must hold)")
    return 0 if kw_ok else 2  # hard-fail only on the deterministic net


if __name__ == "__main__":
    raise SystemExit(main())
