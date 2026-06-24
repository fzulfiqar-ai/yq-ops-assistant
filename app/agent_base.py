"""AgentSpec — the single primitive every agent registers as.

This registry is the canonical "living map" of the business: each ops (and later growth)
function is one spec. The orchestrator, memory diffing, action drafters, and any future
department-map view all read AgentSpec — so adding a capability is a new registration, never
new control flow.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class AgentSpec:
    name: str
    description: str
    run: Callable[[], dict]
    category: str = "ops"  # "ops" | "growth"
    # result -> {"metrics": {flat numbers}, "item_keys": [stable ids]} for memory diffing (Phase B)
    extractor: Callable[[dict], dict] | None = None
    # result -> [draft payloads] for human-approval actions (Phase C)
    drafter: Callable[[dict], list] | None = None
    # named tools the agent may use beyond SQL reads (Phase C): calc / communication / action
    tools: tuple[str, ...] = field(default_factory=tuple)
