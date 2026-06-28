"""Phase 1 — semantic memory (RAG). `remember()` stores a chunk with its local embedding;
`recall()` returns the top-k most relevant chunks for a query. Used to give the assistant
long-term recall of past briefings, decisions, and a seeded business glossary.

All embedding is LOCAL (app/embeddings) so customer names never leave the network. When a
recalled chunk is fed to an external LLM, the caller still redacts it (orchestrator does).
"""
from __future__ import annotations

import logging

from app.database import get_client
from app.embeddings import embed, to_pgvector

log = logging.getLogger(__name__)

KINDS = ("knowledge", "briefing", "decision", "qa")


def remember(content: str, kind: str = "knowledge", meta: dict | None = None) -> bool:
    """Store a memory chunk (best-effort; never raises).

    CONTRACT: `content` must be PII-generalized by the CALLER (no raw customer/account names).
    Stored chunks are recalled as LLM context, and the per-request Redactor cannot restore
    persisted tokens — so memory holds patterns/insights, never a name ledger. The orchestrator
    uses `_generalize_names()`; the glossary/field-note callers are name-free by construction.
    """
    content = (content or "").strip()
    if not content:
        return False
    try:
        get_client().table("kb_chunks").insert({
            "kind": kind if kind in KINDS else "knowledge",
            "content": content[:4000],
            "embedding": to_pgvector(embed(content)),
            "meta": meta or {},
        }).execute()
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("remember failed: %s", e)
        return False


def recall(query: str, k: int = 4, kinds: list[str] | None = None, min_sim: float = 0.35) -> list[dict]:
    """Return up to k relevant chunks ({content, kind, similarity, meta}) for the query."""
    query = (query or "").strip()
    if not query:
        return []
    try:
        qvec = to_pgvector(embed(query))
        r = get_client().rpc("match_kb", {"query_text": qvec, "k": k, "kinds": kinds}).execute()
        return [row for row in (r.data or []) if float(row.get("similarity") or 0) >= min_sim]
    except Exception as e:  # noqa: BLE001
        log.warning("recall failed: %s", e)
        return []


def recall_text(query: str, k: int = 4) -> str:
    """Recalled chunks as a compact context block for prompt injection (empty if none)."""
    hits = recall(query, k=k)
    if not hits:
        return ""
    return "\n".join(f"- {h['content']}" for h in hits)
