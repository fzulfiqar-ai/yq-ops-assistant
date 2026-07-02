"""Phase F — the platform watches ITSELF (the "predictive maintenance" module).

The generic ask was "notice a support-ticket spike after a campaign and auto-draft a KB
article". This platform has no end-customer support desk, so the internal analogue is:

  1. Ingest health   — verify-failure streaks / stale data in ingest_runs.
  2. Agent health    — a spike in runs that reported partial/failed data (agent_runs).
  3. Event health    — unconsumed critical events piling up (agent_events).
  4. Knowledge gaps  — CLUSTERS of similar questions users asked the assistant (audit_log),
                       that the knowledge base doesn't already answer. Each such cluster is the
                       internal "ticket spike": people keep asking the same thing and we have no
                       canned answer. ops_sentinel drafts a KB article into pending_actions; on
                       admin approval the kb_article apply-hook publishes it to kb_chunks so the
                       assistant answers that question class next time.

Reads the governance tables (audit_log / ingest_runs / agent_runs / agent_events) via the
PostgREST client — they are deliberately NOT exposed to the SQL RPC. Clustering uses the LOCAL
embedding model (no egress). The LLM only DRAFTS article text; a human approves before anything
is published. Read-mostly: its only write is a pending_actions draft (human-gated).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.database import get_client

log = logging.getLogger(__name__)

LOOKBACK_DAYS = 14
CLUSTER_MIN = 3          # a "spike": this many similar questions
COSINE_MIN = 0.85        # similarity threshold for the same question class
# bge-small runs a high similarity baseline (~0.5-0.6 even for loosely related text), so the
# "already answered" bar must be discriminating — below this, a matching chunk is only vaguely
# related and the question is still a genuine gap.
KB_COVERED_SIM = 0.72


def _client():
    return get_client()


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


# ── individual health checks (each returns a list of finding dicts) ───────────

def _ingest_health() -> list[dict]:
    try:
        rows = (_client().table("ingest_runs").select("*")
                .order("id", desc=True).limit(5).execute().data or [])
    except Exception as e:  # noqa: BLE001
        log.warning("ingest_runs read failed: %s", e)
        return []
    out = []
    fails = [r for r in rows if not (r.get("ok") if "ok" in r else r.get("verify_ok", True))]
    if rows and fails and rows[0] in fails:
        out.append({"kind": "ingest_fail", "severity": "critical",
                    "detail": f"Latest ingest failed verification ({len(fails)} of last {len(rows)} runs failed)."})
    return out


def _agent_health() -> list[dict]:
    try:
        since = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        rows = (_client().table("agent_runs").select("agent,summary,ran_at")
                .gte("ran_at", since).limit(500).execute().data or [])
    except Exception as e:  # noqa: BLE001
        log.warning("agent_runs read failed: %s", e)
        return []
    partial = [r for r in rows if "failed" in (r.get("summary") or "").lower()
               or "incomplete" in (r.get("summary") or "").lower()]
    if len(partial) >= 3:
        agents = sorted({r.get("agent") for r in partial})
        return [{"kind": "agent_errors", "severity": "warn",
                 "detail": f"{len(partial)} agent runs reported partial/failed data in 3 days: {', '.join(agents[:6])}."}]
    return []


def _event_health() -> list[dict]:
    try:
        rows = (_client().table("agent_events").select("id,severity,event_type")
                .eq("severity", "critical").is_("processed_at", "null").limit(50).execute().data or [])
    except Exception as e:  # noqa: BLE001
        log.warning("agent_events read failed: %s", e)
        return []
    if len(rows) >= 3:
        return [{"kind": "unprocessed_criticals", "severity": "warn",
                 "detail": f"{len(rows)} critical events are unprocessed — is the dispatcher running?"}]
    return []


def _knowledge_gaps() -> list[dict]:
    """Cluster recent assistant questions; a cluster the KB doesn't answer is a gap to document."""
    try:
        since = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).isoformat()
        rows = (_client().table("audit_log").select("question")
                .in_("event", ["orchestrate", "orchestrate_stream"]).gte("ts", since)
                .not_.is_("question", "null").limit(500).execute().data or [])
    except Exception as e:  # noqa: BLE001
        log.warning("audit_log read failed: %s", e)
        return []
    questions = [q["question"].strip() for q in rows if (q.get("question") or "").strip()]
    if len(questions) < CLUSTER_MIN:
        return []
    try:
        from app.embeddings import embed
        from app.knowledge import recall
    except Exception:  # noqa: BLE001
        return []

    # greedy single-link clustering by cosine (small N; fine without a library)
    vecs = [(q, embed(q)) for q in questions]
    clusters: list[list[tuple[str, list[float]]]] = []
    for q, v in vecs:
        placed = False
        for cl in clusters:
            if _cosine(v, cl[0][1]) >= COSINE_MIN:
                cl.append((q, v)); placed = True; break
        if not placed:
            clusters.append([(q, v)])

    gaps = []
    for cl in clusters:
        if len(cl) < CLUSTER_MIN:
            continue
        rep = max((q for q, _ in cl), key=len)   # longest question = most specific representative
        hits = recall(rep, k=1)
        covered = hits and float(hits[0].get("similarity") or 0) >= KB_COVERED_SIM
        if covered:
            continue
        gaps.append({"kind": "knowledge_gap", "severity": "info",
                     "representative": rep, "count": len(cl),
                     "examples": [q for q, _ in cl[:5]],
                     "detail": f"{len(cl)} similar questions with no KB answer: “{rep[:80]}”"})
    return gaps


def _draft_kb_article(gap: dict) -> int | None:
    """LLM drafts a short, grounded KB article for a knowledge-gap cluster → pending_actions."""
    try:
        from app.actions import submit_action
        from app.llm_router import chat
    except Exception:  # noqa: BLE001
        return None
    prompt = [
        {"role": "system", "content": (
            "You write concise internal knowledge-base articles for YQ Bahrain Mobile Accessories' "
            "ops assistant. Given a cluster of similar user questions, draft a SHORT article (title + "
            "3-6 sentences) that would let the assistant answer this question class. Only give general "
            "guidance on HOW to find/interpret the answer in the platform — do NOT invent specific "
            "numbers. Format: first line 'TITLE: ...', then the body.")},
        {"role": "user", "content": "Questions:\n- " + "\n- ".join(gap["examples"])},
    ]
    try:
        text = chat(prompt, tier=2, max_tokens=350, task="synthesis",
                    request_timeout=15, max_429_retries=0, max_providers=3)
    except Exception as e:  # noqa: BLE001
        log.warning("kb draft LLM failed: %s", e)
        return None
    if not text or not text.strip():
        return None
    title = "Knowledge base article"
    body = text.strip()
    if body.upper().startswith("TITLE:"):
        first, _, rest = body.partition("\n")
        title = first.split(":", 1)[1].strip() or title
        body = rest.strip()
    try:
        a = submit_action("kb_article",
                          {"item": title, "title": title, "markdown": body,
                           "source_questions": gap["examples"], "source": "agent:ops_sentinel"},
                          requested_by="agent@yqbahrain.local")
        return a.get("id")
    except Exception as e:  # noqa: BLE001
        log.warning("kb_article submit failed: %s", e)
        return None


def ops_sentinel() -> dict:
    """The self-monitoring run: health checks + knowledge-gap → drafted KB articles for approval."""
    findings: list[dict] = []
    findings += _ingest_health()
    findings += _agent_health()
    findings += _event_health()
    gaps = _knowledge_gaps()
    findings += gaps

    drafted = []
    for g in gaps[:3]:                     # cap: at most 3 article drafts per run
        aid = _draft_kb_article(g)
        if aid:
            drafted.append({"action_id": aid, "topic": g["representative"][:80]})

    crit = [f for f in findings if f.get("severity") == "critical"]
    summary = (f"{len(findings)} platform signal(s): {len(crit)} critical, "
               f"{len(gaps)} knowledge gap(s), {len(drafted)} KB article(s) drafted for approval."
               if findings else "Platform healthy — no ingest, agent, or knowledge issues detected.")
    return {
        "count": len(findings),
        "critical_count": len(crit),
        "knowledge_gaps": len(gaps),
        "articles_drafted": len(drafted),
        "drafted": drafted,
        "findings": findings,
        "summary": summary,
    }
