"""Agentic orchestrator — one smart entry point for a user question.

Principle: the LLM only CLASSIFIES into the closed AGENTS allowlist and PHRASES prose.
Python does selection, chaining, execution, validation. No autonomous loops:
route once → run a fixed (≤3) set → synthesize once. Degrades to deterministic agent
summaries when the LLM is unavailable.
"""
from __future__ import annotations

import json
import logging
import re

from app.agents import AGENTS, run_agent
from app.ai import _entity_names, _smalltalk, _stream_restore, _stream_words, ask, ask_stream
from app.llm_router import Redactor, chat, chat_stream

log = logging.getLogger(__name__)

MAX_AGENTS = 3
_FENCE = re.compile(r"```\w*\s*", re.I)
_META = {"agent", "description", "generated_at", "summary", "count", "email", "changes",
         "anomaly_count", "at_risk_count", "urgent_count", "idle_value_bhd", "total_overdue_bhd"}

# Which feature gates each agent (members only see agents they may access; admins see all).
AGENT_FEATURE: dict[str, str] = {
    "collections": "Receivables", "cashflow": "Receivables",
    "inventory": "Inventory", "inventory_aging": "Inventory", "sales_push": "Inventory",
    "margin": "Margins", "anomaly": "Margins",
    "sales_insights": "Sales", "salesman_performance": "Sales", "customer_health": "Sales",
}

# Deterministic keyword router (fallback when the LLM JSON is unusable).
_KEYWORDS: list[tuple[str, str]] = [
    (r"\bowe|owed|overdue|debtor|receivab|collect|outstand", "collections"),
    (r"\bcash ?flow|ag(e)?ing|90 ?day|concentrat", "cashflow"),
    (r"\bstock|inventory|reorder|out of stock|low stock|replenish", "inventory"),
    (r"\bdead ?stock|idle|slow.?mov", "inventory_aging"),
    (r"\bmargin|profit|gross|\bgp\b|below cost|cogs", "margin"),
    (r"\banomal|negative stock", "anomaly"),
    (r"\bsalesman|sales ?rep|sales ?person|salesmen|b2b|b2c|channel|retail|wholesale", "salesman_performance"),
    (r"\btop sell|best sell|fast mov|cross.?sell|clear stock|push", "sales_push"),
    (r"\bchurn|declin|at.?risk|losing custom", "customer_health"),
    (r"\bsales trend|revenue trend|monthly sales|top customer", "sales_insights"),
]


def allowed_agents(user) -> list[str]:
    """Agents the caller may consult (feature-gated; admins see all)."""
    from app.auth import has_feature
    if getattr(user, "role", "") == "admin":
        return list(AGENTS.keys())
    return [n for n in AGENTS if has_feature(user, AGENT_FEATURE.get(n, "Dashboard"))]


def _keyword_route(question: str, allowed: list[str]) -> list[str]:
    q = question.lower()
    hits: list[str] = []
    for pat, agent in _KEYWORDS:
        if agent in allowed and agent not in hits and re.search(pat, q):
            hits.append(agent)
    return hits[:MAX_AGENTS]


def _catalog(allowed: list[str]) -> str:
    return "\n".join(f"- {n}: {AGENTS[n].description}" for n in allowed)


def route(question: str, allowed: list[str], history: list[dict] | None = None) -> tuple[str, list[str], str]:
    """Classify the question. Returns (mode, agents, reason). LLM → closed list, keyword fallback."""
    catalog = _catalog(allowed)
    hist = ""
    if history:
        hist = "Recent conversation:\n" + "\n".join(
            f"{h.get('role')}: {str(h.get('content', ''))[:200]}" for h in history[-6:]) + "\n\n"
    sys = (
        "You route a question for a Bahrain mobile-accessories distributor's analyst. "
        "Pick ONE mode; for 'agents' pick up to 3 specialists FROM THE LIST ONLY.\n"
        "- agents: the question is about a specialist's DOMAIN (overdue/debtors, stock health & "
        "reorder, margins/below-cost, salesman/channel performance, customer churn, cash/aging) — "
        "give a briefing. PREFER THIS for operational questions.\n"
        "- data: ONLY a precise figure/lookup (a single total, a count, one named customer's "
        "balance, a top-N list) that needs no briefing.\n"
        "- smalltalk: greeting/thanks/identity\n\n"
        f"SPECIALISTS (closed list):\n{catalog}\n\n"
        'Return ONLY JSON: {"mode":"agents|data|smalltalk","agents":["name"],"reason":"…"}'
    )
    msgs = [{"role": "system", "content": sys}, {"role": "user", "content": hist + "Question: " + question}]
    try:
        raw = chat(msgs, tier=1, temperature=0.1, max_tokens=200, model_name="fast")
        raw = _FENCE.sub("", raw).replace("```", "").strip()
        m = re.search(r"\{.*\}", raw, re.S)
        obj = json.loads(m.group(0) if m else raw)
        mode = obj.get("mode")
        agents = [a for a in (obj.get("agents") or []) if a in allowed][:MAX_AGENTS]
        if mode == "agents" and agents:
            return "agents", agents, str(obj.get("reason", ""))[:120]
        if mode in ("data", "smalltalk"):
            return mode, [], str(obj.get("reason", ""))[:120]
    except Exception as e:  # noqa: BLE001
        log.warning("router LLM failed: %s", e)
    kw = _keyword_route(question, allowed)
    if kw:
        return "agents", kw, "keyword-route"
    return "data", [], "fallback-data"


def _first_rows(result: dict, n: int = 5) -> list[dict]:
    for k, v in result.items():
        if k not in _META and isinstance(v, list) and v and isinstance(v[0], dict):
            return v[:n]
    return []


def _all_rows(results: list[dict]) -> list[dict]:
    out: list[dict] = []
    for r in results:
        for k, v in r.items():
            if k not in _META and isinstance(v, list) and v and isinstance(v[0], dict):
                out.extend(v[:8])
    return out


def _key_figures(results: list[dict]) -> str:
    """Deterministic ground-truth block — the numbers the user sees, regardless of LLM prose."""
    lines = ["**Key figures**"]
    for r in results:
        lines.append(f"- **{str(r.get('agent', '')).replace('_', ' ').title()}:** {r.get('summary', '')}")
    return "\n".join(lines)


def recommendations(results: list[dict]) -> list[str]:
    """Deterministic next-step suggestions derived from agent results."""
    recs: list[str] = []
    for r in results:
        a = r.get("agent")
        if a == "inventory" and (r.get("urgent_count") or 0) > 0:
            recs.append(f"Draft reorders for {r['urgent_count']} urgent out-of-stock items")
        elif a == "collections" and (r.get("count") or 0) > 0:
            recs.append(f"Send reminders to {r['count']} overdue accounts (BHD {r.get('total_overdue_bhd', 0):,.0f} past due)")
        elif a == "margin" and (r.get("negative_count") or 0) > 0:
            recs.append(f"Review {r['negative_count']} products selling below cost")
        elif a == "anomaly" and len(r.get("dead_stock") or []) > 0:
            recs.append(f"Clear/write off {len(r['dead_stock'])} dead-stock lines")
        elif a == "inventory_aging" and (r.get("count") or 0) > 0:
            recs.append(f"Review {r['count']} idle items (BHD {r.get('idle_value_bhd', 0):,.0f} sitting)")
    return recs[:3]


def _synth_prompt(question: str, results: list[dict], redactor: Redactor) -> list[dict]:
    compact = []
    for r in results:
        block = f"## {r.get('agent')}: {r.get('summary', '')}"
        rows = _first_rows(r)
        if rows:
            block += "\nrows: " + json.dumps(rows, default=str)
        ch = r.get("changes")
        if ch and not ch.get("first_run"):
            block += "\nchanges_since_last_run: " + json.dumps(ch, default=str)
        compact.append(block)
    text = redactor.redact("\n\n".join(compact), _entity_names(_all_rows(results)))
    sys = (
        "You are the chief-of-staff analyst for YQ Bahrain Mobile Accessories. You receive briefings "
        "from specialist agents. Write ONE concise, executive combined briefing that directly answers "
        "the user's question, weaving the findings together. Bold key numbers; use short bullets. "
        "If 'changes_since_last_run' are present, call out the notable ones. NEVER invent numbers — "
        "use only what's provided. No filler."
    )
    return [{"role": "system", "content": sys}, {"role": "user", "content": f"Question: {question}\n\n{text}"}]


def _deterministic_briefing(results: list[dict]) -> str:
    """LLM-free fallback: concatenated agent summaries."""
    return "Here's what the team found:\n\n" + "\n".join(
        f"• **{str(r.get('agent', '')).replace('_', ' ').title()}** — {r.get('summary', '')}" for r in results)


def orchestrate(question: str, user, history: list[dict] | None = None, model_name: str | None = None) -> dict:
    """Non-streaming orchestration → {reply, mode, agents_used, results, recommendations, changes}."""
    sm = _smalltalk(question)
    if sm:
        return {"reply": sm, "mode": "smalltalk", "agents_used": [], "results": [], "recommendations": []}

    allow = allowed_agents(user)
    mode, agents, _reason = route(question, allow, history)

    if mode == "data" or not agents:
        r = ask(question, user_email=getattr(user, "email", "system"), model_name=model_name)
        return {"reply": r["reply"], "mode": "data", "agents_used": [], "results": [], "recommendations": []}

    results = [run_agent(n, triggered_by="user") for n in agents]
    redactor = Redactor()
    try:
        prose = redactor.restore(chat(_synth_prompt(question, results, redactor), tier=2,
                                      temperature=0.3, max_tokens=700, model_name=model_name))
    except Exception as e:  # noqa: BLE001
        log.warning("synthesis failed: %s", e)
        prose = _deterministic_briefing(results)
    reply = f"{prose}\n\n{_key_figures(results)}"
    return {
        "reply": reply, "mode": "agents", "agents_used": agents, "results": results,
        "recommendations": recommendations(results),
        "changes": [r.get("changes") for r in results if r.get("changes")],
    }


def orchestrate_stream(question: str, user, history: list[dict] | None = None, model_name: str | None = None):
    """Streaming orchestration: routing preamble → per-agent headlines → synthesis → key figures."""
    sm = _smalltalk(question)
    if sm:
        yield from _stream_words(sm)
        return

    allow = allowed_agents(user)
    mode, agents, _reason = route(question, allow, history)

    if mode == "data" or not agents:
        yield from ask_stream(question, user_email=getattr(user, "email", "system"), model_name=model_name)
        return

    pretty = " · ".join(a.replace("_", " ").title() for a in agents)
    yield f"_Consulting: {pretty}…_\n\n"
    results = []
    for n in agents:
        r = run_agent(n, triggered_by="user")
        results.append(r)
        yield f"✓ **{n.replace('_', ' ').title()}** — {r.get('summary', '')}\n"
    yield "\n"

    redactor = Redactor()
    try:
        streamed = False
        for piece in _stream_restore(
            chat_stream(_synth_prompt(question, results, redactor), tier=2, temperature=0.3,
                        max_tokens=700, model_name=model_name), redactor.restore):
            streamed = True
            yield piece
        if not streamed:
            yield _deterministic_briefing(results)
    except Exception as e:  # noqa: BLE001
        log.warning("synthesis stream failed: %s", e)
        yield _deterministic_briefing(results)

    yield "\n\n" + _key_figures(results)
    recs = recommendations(results)
    if recs:
        yield "\n\n**Recommended next steps**\n" + "\n".join(f"- {x}" for x in recs)
