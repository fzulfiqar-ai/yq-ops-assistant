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
from concurrent.futures import ThreadPoolExecutor, as_completed

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
    "margin": "Margins", "risk_watch": "Margins",
    "salesman_stock_recon": "Margins",
    "sales_insights": "Sales", "salesman_performance": "Sales", "customer_health": "Sales",
    "trend": "Sales", "marketing": "Sales",
    "catalog_watch": "Dashboard", "vendor_sourcing": "Inventory",
    "demand_forecast": "Inventory", "abc_xyz": "Inventory", "deadstock_liquidation": "Inventory",
    "winback": "Sales", "credit_exposure": "Receivables",
    "working_capital": "Receivables", "pricing_optimization": "Margins",
    "purchase_insights": "Inventory", "reorder_proposal": "Inventory",
    "procurement_status": "Inventory",
    "cross_sell": "Sales", "vendor_scorecard": "Inventory", "trend_radar": "Sales",
    "lead_gen": "Sales", "research_scout": "Sales",
}

# Deterministic keyword router (fallback when the LLM JSON is unusable).
_KEYWORDS: list[tuple[str, str]] = [
    (r"\bowe|owed|overdue|debtor|receivab|collect|outstand", "collections"),
    (r"\bcash ?flow|ag(e)?ing|90 ?day|concentrat", "cashflow"),
    (r"\bstock|inventory|reorder|out of stock|low stock|replenish", "inventory"),
    (r"\bdead ?stock|idle|slow.?mov", "inventory_aging"),
    (r"\bmargin|profit|gross|\bgp\b|below cost|cogs", "margin"),
    (r"\banomal|negative stock", "risk_watch"),
    (r"\bsalesman|sales ?rep|sales ?person|salesmen", "salesman_performance"),
    (r"\btop sell|best sell|fast mov|clear stock|push", "sales_push"),
    (r"\bcross.?sell|bundle|bought together|sells together|sold together|attach rate|basket size|frequently bought|pair", "cross_sell"),
    (r"\bvendor (score|performance|rating|reliab)|supplier (score|performance|rating)|best (vendor|supplier)|cost creep|which (vendor|supplier)", "vendor_scorecard"),
    (r"\bchurn|declin|at.?risk|losing custom", "customer_health"),
    (r"\bsales trend|revenue trend|monthly sales|top customer", "sales_insights"),
    (r"\bbuy(ing)?\b|bought|purchas|reorder|re-order|vendor|supplier|shipment|procure|landed cost|cost (went )?up", "purchase_insights"),
    (r"\bfraud|leakage|suspicious|integrity|discount (abuse|leak)|unauthor|audit", "risk_watch"),
    (r"\bstock (transfer|issue|recon|reconcil)|transfer(red)?.{0,12}(warehouse|salesman|van)|issued to|van stock|salesman (stock|warehouse|inventory)|stock shortage|missing stock|stock leakage|shrinkage|stock recon", "salesman_stock_recon"),
    (r"\btrend|rising|falling|fading|momentum|growing|declin|hot item|gaining|popular", "trend"),
    (r"\bheating up|what.?s hot|hot (right now|seller|product)|going viral|trending (online|now|on)|new trend|stock up|riding the trend|what.?s trending", "trend_radar"),
    (r"\blead(s)?\b|new (customer|buyer|retailer|account|shop|b2b)|\bbuyer|prospect|find (new )?(customers|buyers|shops|retailers|accounts|leads)|shops? to sell|sell to|cold (call|outreach)|grow (the )?(b2b|wholesale|sales)|new account", "lead_gen"),
    (r"\bpromo|promotion|campaign|bundle|market|offer|clear stock|push (high|margin)", "marketing"),
    (r"\bwhat.?s? (new|changed)|new sku|new product|newly added|price change|cost change|what changed", "catalog_watch"),
    (r"\bnew (vendor|supplier)|source|sourcing|find (a )?supplier|alternative supplier|where (can|to) buy", "vendor_sourcing"),
    (r"\bforecast|stock ?out|run(ning)? out|reorder point|when (to |do we )?(re-?order|buy)|days of cover|order by|predict", "demand_forecast"),
    (r"\babc|xyz|pareto|80[/ -]?20|classif(y|ication)", "abc_xyz"),
    (r"\bdead ?stock|liquidat|clearance|markdown|trapped (cash|capital|stock)|write.?off|clear (out )?stock", "deadstock_liquidation"),
    (r"\bwin.?back|lapsed|lost custom|dormant|re.?engage|gone quiet|inactive custom|haven.?t (ordered|bought)", "winback"),
    (r"\bcredit (limit|exposure|risk)|exposure|over limit|aged debt|bad debt|credit risk", "credit_exposure"),
    (r"\bworking capital|capital (tied|locked|trapped)|cash (tied|locked|trapped)|cash conversion|liquidity", "working_capital"),
    (r"\bpric(e|ing) (optim|review|strateg|increase|decrease)|mispric|under ?pric|over ?pric|raise (the )?price|cut (the )?price|repric", "pricing_optimization"),
    (r"\bpurchase order|\bpo\b|order (tracking|history)|(cost|price).{0,16}(across|over|vs).{0,8}orders|paid.{0,16}(last|previous|prior) (order|time)|cost comparison|track.{0,8}orders|what (did|do) we pay", "purchase_insights"),
    (r"\b(draft|propose|prepare|build|create|suggest(ed)?|generate) (a |an |the |me )?(purchase |re-?)?order|order proposal|reorder proposal|purchase proposal|what should (i|we) (re-?)?order|order to raise|raise (a |an )?(new )?order", "reorder_proposal"),
    (r"\border status|procurement (status|pipeline|board)|pipeline|stuck order|open orders|where (is|are) (my |the )?order|order(s)? (waiting|pending|in progress)|chase (the )?order|status of (my |the )?order", "procurement_status"),
]

# Refinement / comparison / lookup phrasing → answer from DATA (context-aware SQL), never a
# canned agent briefing. This is what makes follow-ups ("compare to last month", "break it
# down by salesman", "B2C vs B2B", "only the top 5") re-query instead of repeating an agent.
_REFINE = re.compile(
    r"\b(compare|compared|comparison|vs\.?|versus|break ?it ?down|break ?down|broken down|"
    r"only|just the|instead|difference|trend|by (salesman|month|category|channel|customer|week|day)|"
    r"this month|last month|previous month|year[- ]on[- ]year|yoy|mom|month[- ]on[- ]month|"
    r"\btop \d+|\bbottom \d+|b2c|b2b|retail vs|wholesale vs|channel)\b", re.I)

# Genuinely multi-step questions where bounded tool-calling (admin + capable model) chains
# search_data/run_agent. Conservative — everything else uses the deterministic path; tool-calling
# always falls back to it on any failure.
_MULTISTEP = re.compile(
    r"\b(and then|for each|both .* and|as well as|along with|combine|cross[- ]reference|"
    r"compare .* (and|vs\.?|to) .* (and|then|by)|which .* and (what|who|how|why))\b", re.I)


def _is_multistep(q: str) -> bool:
    return bool(_MULTISTEP.search(q)) or (len(q) > 160 and q.count("?") >= 2)


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
    # refinements/comparisons/lookups → data mode (re-query with context), not a canned briefing
    if _REFINE.search(question):
        return "data", [], "refinement"
    catalog = _catalog(allowed)
    hist = ""
    if history:
        # only the last couple of turns, and only to resolve references — not to bias the topic
        hist = "Recent conversation (for resolving references only):\n" + "\n".join(
            f"{h.get('role')}: {str(h.get('content', ''))[:160]}" for h in history[-3:]) + "\n\n"
    sys = (
        "You route a question for a Bahrain mobile-accessories distributor's analyst. "
        "Decide based on THE CURRENT QUESTION; use history only to resolve pronouns/ellipsis "
        "(it/them/that), never to repeat the previous topic. Pick ONE mode; for 'agents' pick up "
        "to 3 specialists FROM THE LIST ONLY.\n"
        "- agents: a broad request for a briefing or recommendation in a specialist's DOMAIN "
        "(e.g. 'who owes us money', 'are we low on stock', 'how are margins', 'what should I do "
        "about X') — give a multi-point briefing.\n"
        "- data: a specific figure, lookup, list, comparison, breakdown, or trend (a total, a "
        "count, a named customer's balance, a top-N, 'compare X to Y', 'by month/salesman', "
        "'B2C vs B2B'). PREFER data for anything specific or comparative.\n"
        "- smalltalk: greeting/thanks/identity/acknowledgement\n\n"
        f"SPECIALISTS (closed list):\n{catalog}\n\n"
        'Return ONLY JSON: {"mode":"agents|data|smalltalk","agents":["name"],"reason":"…"}'
    )
    msgs = [{"role": "system", "content": sys}, {"role": "user", "content": hist + "Question: " + question}]
    try:
        # routing must never hang: fast model, short timeout, no backoff, few providers —
        # any failure drops straight to the deterministic keyword router below.
        raw = chat(msgs, tier=1, temperature=0.1, max_tokens=400, model_name="fast",
                   task="route", request_timeout=7, max_429_retries=0, max_providers=3)
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


def _generalize_names(text: str, results: list[dict]) -> str:
    """Replace customer/account names (extracted from the result rows, plus anything in the
    question) with a generic placeholder so STORED memory carries the insight, not PII.

    We generalize, not tokenize: the RAG Redactor is per-request/ephemeral, so a persisted
    `CUST_n` token would collide across rows and could never be restored. Memory is for
    patterns ("an account crossed 90 days"), never a name ledger at rest.
    """
    for name in sorted({n for n in _entity_names(_all_rows(results)) if n}, key=len, reverse=True):
        text = text.replace(name, "[account]")
    return text


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
        elif a == "risk_watch" and len(r.get("dead_stock") or []) > 0:
            recs.append(f"Clear/write off {len(r['dead_stock'])} dead-stock lines")
        elif a == "inventory_aging" and (r.get("count") or 0) > 0:
            recs.append(f"Review {r['count']} idle items (BHD {r.get('idle_value_bhd', 0):,.0f} sitting)")
    return recs[:3]


def priority_actions(results: list[dict], k: int = 5) -> list[dict]:
    """Today's ranked actions — a deterministic, explainable scorer over the agent results.

    Each action carries a BHD-at-risk and an urgency tier (3 = lost sales / below cost / will run
    out this week, 2 = cash owed / churn, 1 = optimisation / trapped capital). Ranked by
    (urgency, BHD-at-risk) so the team sees what to do FIRST, with the money attached. No LLM.
    """
    by = {r.get("agent"): r for r in results}
    acts: list[dict] = []

    def add(agent: str, action: str, bhd: float = 0.0, urgency: int = 2) -> None:
        acts.append({"agent": agent, "action": action,
                     "bhd_at_risk": round(float(bhd or 0), 0), "urgency": urgency})

    inv = by.get("inventory", {})
    if (inv.get("urgent_count") or 0) > 0:
        add("inventory", f"Reorder {inv['urgent_count']} fast movers that are OUT of stock — recover lost sales", urgency=3)
    df = by.get("demand_forecast", {})
    if (df.get("order_now_count") or 0) > 0:
        add("demand_forecast", f"Place orders THIS WEEK for {df['order_now_count']} items before they run out", urgency=3)
    rw = by.get("risk_watch", {})
    if rw.get("priced_below_cost"):
        add("risk_watch", f"Fix pricing on {len(rw['priced_below_cost'])} items selling below cost", urgency=3)
    col = by.get("collections", {})
    if (col.get("count") or 0) > 0:
        add("collections", f"Chase {col['count']} overdue accounts", col.get("total_overdue_bhd") or 0, urgency=2)
    wb = by.get("winback", {})
    if (wb.get("count") or 0) > 0:
        add("winback", f"Re-engage {wb['count']} lapsed customers before they're gone for good", urgency=2)
    dead = rw.get("dead_stock") or []
    if dead:
        val = sum(float(d.get("stock_value") or 0) for d in dead)
        add("risk_watch", f"Liquidate {len(dead)} dead-stock lines — free up trapped capital", val, urgency=1)
    ia = by.get("inventory_aging", {})
    if (ia.get("count") or 0) > 0:
        add("inventory_aging", f"Clear {ia['count']} idle items sitting on the shelf", ia.get("idle_value_bhd") or 0, urgency=1)
    po = by.get("pricing_optimization", {})
    if (po.get("raise_count") or 0) > 0:
        add("pricing_optimization", f"Raise price on {po['raise_count']} thin-margin fast movers — recover margin", urgency=1)

    acts.sort(key=lambda a: (a["urgency"], a["bhd_at_risk"]), reverse=True)
    return acts[:k]


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
    # semantic memory: weave in relevant past context (glossary, prior briefings, decisions),
    # redacted with the same map before it reaches the LLM
    try:
        from app.knowledge import recall_text
        mem = recall_text(question, k=3)
        if mem:
            mem = redactor.redact(mem, _entity_names(_all_rows(results)))
            text = "Relevant context from memory:\n" + mem + "\n\n---\n\n" + text
    except Exception:  # noqa: BLE001
        pass
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


def _run_agents(agents: list[str]) -> list[dict]:
    """Run the selected agents CONCURRENTLY — each is I/O-bound on Supabase, so 3 in parallel
    is ~3x faster than sequential. Order preserved (ex.map)."""
    if len(agents) <= 1:
        return [run_agent(n, triggered_by="user") for n in agents]
    with ThreadPoolExecutor(max_workers=MAX_AGENTS) as ex:
        return list(ex.map(lambda n: run_agent(n, triggered_by="user"), agents))


def orchestrate(question: str, user, history: list[dict] | None = None, model_name: str | None = None) -> dict:
    """Non-streaming orchestration → {reply, mode, agents_used, results, recommendations, changes}."""
    sm = _smalltalk(question)
    if sm:
        return {"reply": sm, "mode": "smalltalk", "agents_used": [], "results": [], "recommendations": []}

    allow = allowed_agents(user)
    mode, agents, _reason = route(question, allow, history)

    if mode == "data" or not agents:
        from app.auth import feature_set
        feats = feature_set(user)
        # admin (unrestricted) + capable model + multi-step → bounded tool-calling, else fall back
        if feats is None and model_name in ("pro", "thinking") and _is_multistep(question):
            try:
                from app.tool_loop import answer_with_tools
                reply = answer_with_tools(question, model_name=model_name, history=history)
                if reply and reply.strip():
                    return {"reply": reply, "mode": "tools", "agents_used": [], "results": [], "recommendations": []}
            except Exception as e:  # noqa: BLE001
                log.warning("tool-calling failed, falling back: %s", e)
        r = ask(question, user_email=getattr(user, "email", "system"), model_name=model_name,
                history=history, allowed_features=feats)
        return {"reply": r["reply"], "mode": "data", "agents_used": [], "results": [], "recommendations": []}

    results = _run_agents(agents)
    redactor = Redactor()
    try:
        prose = redactor.restore(chat(_synth_prompt(question, results, redactor), tier=2,
                                      temperature=0.3, max_tokens=700, model_name=model_name,
                                      task="synthesis", request_timeout=18, max_429_retries=1,
                                      max_providers=4))
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
        from app.auth import feature_set
        feats = feature_set(user)
        if feats is None and model_name in ("pro", "thinking") and _is_multistep(question):
            try:  # bounded tool-calling for capable models; falls back to SQL Q&A on any failure
                from app.tool_loop import answer_with_tools
                reply = answer_with_tools(question, model_name=model_name, history=history)
                if reply and reply.strip():
                    yield from _stream_words(reply)
                    return
            except Exception as e:  # noqa: BLE001
                log.warning("tool-calling stream failed, falling back: %s", e)
        yield from ask_stream(question, user_email=getattr(user, "email", "system"),
                              model_name=model_name, history=history, allowed_features=feats)
        return

    # structured marker the chat parses into consulted-agent chips (stripped from the text)
    yield f"⟦agents:{','.join(agents)}⟧"
    results = []
    if len(agents) <= 1:
        for n in agents:
            r = run_agent(n, triggered_by="user")
            results.append(r)
            yield f"✓ **{n.replace('_', ' ').title()}** — {r.get('summary', '')}\n"
    else:  # run concurrently; stream each headline as it finishes
        with ThreadPoolExecutor(max_workers=MAX_AGENTS) as ex:
            futs = {ex.submit(run_agent, n, "user"): n for n in agents}
            for fut in as_completed(futs):
                r = fut.result()
                results.append(r)
                yield f"✓ **{futs[fut].replace('_', ' ').title()}** — {r.get('summary', '')}\n"
    yield "\n"

    redactor = Redactor()
    try:
        streamed = False
        for piece in _stream_restore(
            chat_stream(_synth_prompt(question, results, redactor), tier=2, temperature=0.3,
                        max_tokens=700, model_name=model_name, task="synthesis",
                        request_timeout=15, max_providers=4), redactor.restore):
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
    # remember this briefing's ground-truth so future questions can recall it
    try:
        from app.knowledge import remember
        remember(_generalize_names(f"Briefing — {question}\n{_key_figures(results)}", results),
                 kind="briefing", meta={"agents": agents})
    except Exception:  # noqa: BLE001
        pass
