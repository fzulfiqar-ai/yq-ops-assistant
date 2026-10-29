"""Free LLM provider rotation — the "never-die" brain stack.

Design (see docs/CLAUDE.md):
- Many FREE provider/model endpoints across Cerebras, Groq, OpenRouter, Gemini, and
  (when keys are present) GitHub Models, Cloudflare, Mistral, Cohere. One OpenRouter key
  already unlocks ~20 free models, so the rotation spans 20+ independent free quotas.
- Each model carries capability tags (code/math/reason/write/multilingual/speed); the router
  picks the brain whose strengths match the TASK, biased by the user's UI tier, and always
  keeps a reliable fast host near the front. Throttled endpoints sort last.
- Circuit breaker: on 429 (per-minute) skip to the next provider; on auth/quota exhaustion
  disable that provider until the next UTC midnight (daily reset) so traffic auto-shifts.
- Tier 1 (parsing/classification): fast small models. Tier 2 (final answers): strong models.
- Redaction: redact()/restore() tokenize commercial PII (customer/account names) before any
  external call and map it back locally. Only redacted text leaves the network.

All providers are OpenAI-compatible, so we use the `openai` SDK with per-provider base_url.
No Anthropic, no paid APIs, no Claude CLI. Model IDs are env-overridable so a future model
rename is a config change, not a code change.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from openai import OpenAI
from openai import APIStatusError, RateLimitError

from app.config import settings


def _env(name: str, default: str) -> str:
    v = os.getenv(name)
    return v.strip() if v and v.strip() else default


# capability keys used for task-aware routing
_CAPS = ("code", "math", "reason", "write", "multilingual", "speed")


@dataclass
class Provider:
    name: str
    base_url: str
    api_key: str
    model: str
    caps: dict[str, int] = field(default_factory=dict)   # 0..3 per capability
    quality: int = 1            # overall strength 1..3
    fast_host: bool = False     # very low latency + reliable (Groq/Cerebras/Gemini)
    tier2: bool = False         # eligible/strong for final answers
    throttled: bool = False     # known rate-limited/slow free endpoint -> sort LAST
    reasoning: bool = False     # chain-of-thought model: great for synthesis, BAD for route/sql
                                # (it spends tokens thinking and may emit empty content for JSON)
    extra: dict = field(default_factory=dict)   # provider-specific request fields (reasoning_effort)
    vision: bool = False        # reads images (measured 28-Oct-2026: a product box and a written list)
    # circuit breaker
    disabled_until: float = 0.0
    failures: int = 0


def _providers() -> list[Provider]:
    """Build the rotation from whatever keys are configured (missing keys are skipped).

    Only MEASURED-VALID free models are included as primaries; known-throttled OpenRouter
    free models are kept as last-resort (throttled=True). Capability tags drive task routing.
    """
    p: list[Provider] = []
    # Measured 28-Oct-2026 (scripts/probe_models.py): the June IDs were gone (Groq llama-3.3/3.1,
    # Cerebras zai-glm-4.7, NVIDIA deepseek-v4-pro); Groq's qwen3.8-27b answered the chat's JSON task
    # in ~0.2 s and Arabic in ~0.1 s, gpt-oss-120b in ~0.5-0.9 s. Re-probe whenever a host changes.

    # ---- Cerebras: fast host; this key's org answered 402 "payment required" on 28-Oct (benched
    # until UTC midnight by the quota rule, so it costs one call a day until billing is sorted) ----
    if settings.cerebras_api_key:
        base = "https://api.cerebras.ai/v1"
        k = settings.cerebras_api_key
        p.append(Provider(
            "cerebras:qwen3-27b", base, k, _env("CEREBRAS_FAST_MODEL", "qwen-3.8-27b"),
            caps={"code": 2, "math": 2, "reason": 2, "write": 3, "multilingual": 3, "speed": 3},
            quality=2, fast_host=True, tier2=True, vision=True))
        p.append(Provider(
            "cerebras:gpt-oss-120b", base, k, _env("CEREBRAS_OSS_MODEL", "gpt-oss-120b"),
            caps={"code": 3, "math": 2, "reason": 2, "write": 3, "multilingual": 1, "speed": 3},
            quality=3, fast_host=True, tier2=True, extra={"reasoning_effort": "low"}))

    # ---- Groq: the backbone today; fast, good Arabic, JSON on the first try ----
    if settings.groq_api_key:
        base = "https://api.groq.com/openai/v1"
        k = settings.groq_api_key
        p.append(Provider(
            "groq:qwen3-27b", base, k, _env("GROQ_FAST_MODEL", "qwen/qwen3.8-27b"),
            caps={"code": 2, "math": 2, "reason": 2, "write": 3, "multilingual": 3, "speed": 3},
            quality=2, fast_host=True, tier2=True, vision=True))
        p.append(Provider(
            "groq:gpt-oss-120b", base, k, _env("GROQ_STRONG_MODEL", "openai/gpt-oss-120b"),
            caps={"code": 3, "math": 3, "reason": 3, "write": 3, "multilingual": 2, "speed": 2},
            quality=3, fast_host=True, tier2=True, extra={"reasoning_effort": "low"}))
        p.append(Provider(
            "groq:gpt-oss-20b", base, k, _env("GROQ_SMALL_MODEL", "openai/gpt-oss-20b"),
            caps={"code": 2, "math": 2, "reason": 2, "write": 2, "multilingual": 2, "speed": 3},
            quality=1, fast_host=True, extra={"reasoning_effort": "low"}))

    # ---- Gemini: strong, sees images; but 28-Oct the free 2.5-flash quota was spent and
    # flash-lite took 15-70 s, so it is not a fast host until a probe says otherwise ----
    if settings.gemini_api_key:
        p.append(Provider(
            "gemini:flash-lite", "https://generativelanguage.googleapis.com/v1beta/openai/",
            settings.gemini_api_key, _env("GEMINI_MODEL", "gemini-flash-lite-latest"),
            caps={"code": 2, "math": 2, "reason": 2, "write": 3, "multilingual": 3, "speed": 1},
            quality=2, tier2=True, vision=True))

    # ---- SambaNova: very fast free host (Llama 3.3 70B etc.) ----
    if settings.sambanova_api_key:
        p.append(Provider(
            "sambanova:llama70b", "https://api.sambanova.ai/v1",
            settings.sambanova_api_key, _env("SAMBANOVA_MODEL", "Meta-Llama-3.3-70B-Instruct"),
            caps={"code": 2, "math": 2, "reason": 2, "write": 3, "multilingual": 2, "speed": 3},
            quality=2, fast_host=True, tier2=True))

    # ---- NVIDIA NIM: free API; deepseek-v4-pro reached end of life 7-Aug-2026, and on 28-Oct every
    # listed model either timed out or 404'd for this key: last resort only ----
    if settings.nvidia_api_key:
        p.append(Provider(
            "nvidia:deepseek-v4", "https://integrate.api.nvidia.com/v1",
            settings.nvidia_api_key, _env("NVIDIA_MODEL", "deepseek-ai/deepseek-v4.1-flash"),
            caps={"code": 3, "math": 3, "reason": 3, "write": 2, "multilingual": 2, "speed": 1},
            quality=3, tier2=True))

    # ---- Moonshot / Kimi: OpenAI-compatible; Kimi K2 = strong long-context reasoning ----
    if settings.moonshot_api_key:
        p.append(Provider(
            "moonshot:kimi-k2", _env("MOONSHOT_BASE_URL", "https://api.moonshot.ai/v1"),
            settings.moonshot_api_key, _env("MOONSHOT_MODEL", "kimi-k2-0905-preview"),
            caps={"code": 3, "math": 2, "reason": 3, "write": 3, "multilingual": 3, "speed": 2},
            quality=3, tier2=True))

    # ---- OpenRouter: one key unlocks the free models it lists (IDs from its /models, 28-Oct-2026).
    # This deployment's key answered 401 "User not found" that day: a new key is the owner's step ----
    if settings.openrouter_api_key:
        base = "https://openrouter.ai/api/v1"
        k = settings.openrouter_api_key
        p.append(Provider(
            "openrouter:nemotron-ultra", base, k,
            _env("OR_NEMOTRON_MODEL", "nvidia/nemotron-3-ultra-550b-a55b:free"),
            caps={"code": 3, "math": 3, "reason": 3, "write": 2, "multilingual": 2, "speed": 1},
            quality=3, tier2=True))
        p.append(Provider(
            "openrouter:gemma-31b", base, k,
            _env("OR_GEMMA_MODEL", "google/gemma-4-31b-it:free"),
            caps={"code": 1, "math": 1, "reason": 2, "write": 2, "multilingual": 3, "speed": 2},
            quality=2, tier2=True))
        p.append(Provider(
            "openrouter:inkling", base, k,
            _env("OR_INKLING_MODEL", "thinkingmachines/inkling:free"),
            caps={"code": 2, "math": 2, "reason": 2, "write": 3, "multilingual": 2, "speed": 1},
            quality=2, tier2=True))
        # free and listed, but a reasoning model that measured slow on another host -> last resort
        p.append(Provider(
            "openrouter:nemotron-lightning", base, k,
            _env("OR_LIGHTNING_MODEL", "nvidia/nemotron-3.5-lightning:free"),
            caps={"code": 2, "math": 2, "reason": 2, "write": 2, "multilingual": 2, "speed": 0},
            quality=2, throttled=True))

    # ---- Optional first-party providers: auto-join when their key is present ----
    # Mistral's free tier is about one request a second for the whole key: two sizes share it
    if settings.mistral_api_key:
        p.append(Provider(
            "mistral:medium", "https://api.mistral.ai/v1",
            settings.mistral_api_key, _env("MISTRAL_MODEL", "mistral-medium-latest"),
            caps={"code": 2, "math": 2, "reason": 2, "write": 3, "multilingual": 3, "speed": 2},
            quality=3, fast_host=True, tier2=True, vision=True))
        p.append(Provider(
            "mistral:ministral", "https://api.mistral.ai/v1",
            settings.mistral_api_key, _env("MISTRAL_FAST_MODEL", "ministral-8b-latest"),
            caps={"code": 1, "math": 1, "reason": 1, "write": 2, "multilingual": 2, "speed": 3},
            quality=1, fast_host=True, vision=True))
    if settings.cohere_api_key:
        p.append(Provider(
            "cohere:command", "https://api.cohere.ai/compatibility/v1",
            settings.cohere_api_key, _env("COHERE_MODEL", "command-a-03-2025"),
            caps={"code": 1, "math": 1, "reason": 2, "write": 3, "multilingual": 2, "speed": 2},
            quality=2, tier2=True))
    if settings.github_models_token:
        p.append(Provider(
            "github:gpt-4o-mini", "https://models.github.ai/inference",
            settings.github_models_token, _env("GITHUB_MODEL", "openai/gpt-4o-mini"),
            caps={"code": 2, "math": 2, "reason": 2, "write": 2, "multilingual": 2, "speed": 2},
            quality=2, tier2=True))
    if settings.cloudflare_account_id and settings.cloudflare_api_token:
        p.append(Provider(
            "cloudflare:llama70b",
            f"https://api.cloudflare.com/client/v4/accounts/{settings.cloudflare_account_id}/ai/v1",
            settings.cloudflare_api_token,
            _env("CLOUDFLARE_MODEL", "@cf/meta/llama-3.3-70b-instruct-fp8-fast"),
            caps={"code": 1, "math": 1, "reason": 2, "write": 2, "multilingual": 1, "speed": 2},
            quality=2))

    # Reasoning/CoT models — excellent for synthesis/math but they "think" before answering,
    # so they're a poor fit for routing/SQL (empty content at small budgets, slow first token).
    reasoning_names = {
        "cerebras:gpt-oss-120b", "groq:gpt-oss-120b", "groq:gpt-oss-20b",
        "openrouter:nemotron-ultra", "openrouter:nemotron-lightning", "nvidia:deepseek-v4",
    }
    for prov in p:
        if prov.name in reasoning_names:
            prov.reasoning = True
    return p


_ROTATION: list[Provider] = _providers()

# circuit breaker / backoff tuning
_CB_COOLDOWN = 300.0      # seconds a provider stays disabled after a transient/network error
_RL_COOLDOWN = 90.0       # short bench after a 429 (per-minute limit) so we skip it for a bit
_MAX_429_RETRIES = 4      # per provider before moving on (default; callers cap lower)
_QUOTA_WORDS = ("quota", "insufficient", "exhaust", "credit", "billing", "out of free")


class AllProvidersFailed(RuntimeError):
    pass


# --------------------------------------------------------------------------- task routing
# Map a logical task to the capability that matters most for it.
_TASK_CAP = {
    "route": "speed", "sql": "code", "math": "math", "reason": "reason",
    "write": "write", "synthesis": "write", "multilingual": "multilingual",
    # the marketplace chat (app/market_assist.py): any language in, a short answer out, fast
    "assist": "multilingual",
}
# tasks that want a direct answer from a reliable fast host: reasoning models (which spend a small
# token budget thinking and can return empty content) and slow hosts are pushed down hard
_LATENCY_TASKS = ("route", "sql", "assist")


def _next_utc_midnight() -> float:
    now = datetime.now(timezone.utc)
    nxt = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return nxt.timestamp()


def _is_quota(exc: Exception) -> bool:
    return any(w in str(exc).lower() for w in _QUOTA_WORDS)


def _client(prov: Provider, timeout: float) -> OpenAI:
    """One provider's client. max_retries=0: the rotation does its own fail-over, and the SDK's two
    silent retries would turn an 18 s timeout into ~54 s before the next provider is tried."""
    return OpenAI(base_url=prov.base_url, api_key=prov.api_key, timeout=timeout, max_retries=0)


# --------------------------------------------------------------------------- usage today
# Per provider, per UTC day, in this process: answers, 429s, other failures and replies that were
# not the JSON asked for. Shown by health() (GET /llm/health) so the free headroom is visible.
_USAGE: dict[str, dict] = {}
_OUTCOMES = ("ok", "429", "fail", "bad")
_last = threading.local()     # the provider that answered this thread's latest chat() call


def last_provider() -> str:
    """Name of the provider that answered this thread's latest chat() call ('' if none yet)."""
    return getattr(_last, "name", "")


def _note(prov: Provider, outcome: str) -> None:
    day = datetime.now(timezone.utc).date().isoformat()
    u = _USAGE.get(prov.name)
    if u is None or u.get("day") != day:
        u = _USAGE[prov.name] = {"day": day, **{k: 0 for k in _OUTCOMES}}
    u[outcome] = u.get(outcome, 0) + 1


# --------------------------------------------------------------------------- redaction
_CUSTOMER_TOKEN = re.compile(r"\bCUST_\d+\b")


@dataclass
class Redactor:
    """Tokenize sensitive names before an external call; restore them in the reply."""

    mapping: dict[str, str] = field(default_factory=dict)
    _n: int = 0

    def redact(self, text: str, names: list[str]) -> str:
        for name in sorted(set(n for n in names if n), key=len, reverse=True):
            if name not in self.mapping:
                self._n += 1
                self.mapping[name] = f"CUST_{self._n}"
            text = text.replace(name, self.mapping[name])
        return text

    def restore(self, text: str) -> str:
        inverse = {tok: name for name, tok in self.mapping.items()}
        return _CUSTOMER_TOKEN.sub(lambda m: inverse.get(m.group(0), m.group(0)), text)


# --------------------------------------------------------------------------- candidate ordering
def _score(p: Provider, task: str | None, model_name: str | None) -> float:
    """Higher = better fit. Capability match dominates; tier biases; fast hosts win ties."""
    cap = _TASK_CAP.get(task or "", "write")
    s = float(p.caps.get(cap, 0) * 3)
    latency_sensitive = task in _LATENCY_TASKS
    if latency_sensitive:
        # route/sql want direct structured output FROM A RELIABLE FAST HOST — push CoT and slow
        # (non-fast-host, e.g. OpenRouter free) endpoints down hard so we never stall on them
        if p.reasoning:
            s -= 6.0
        if not p.fast_host:
            s -= 4.0
        if p.fast_host:
            s += 2.0
        s += p.caps.get("speed", 0) * 0.5   # among fast hosts the quickest model leads
    else:
        # quality tasks (synthesis/math/reason/multilingual): capability + brainpower lead;
        # fast hosts get only a small nudge so the strongest brain wins, not merely the fastest
        if p.fast_host:
            s += 0.5
    if model_name == "fast":
        s += p.caps.get("speed", 0)
    elif model_name == "thinking":
        s += p.caps.get("reason", 0)
    elif model_name == "pro":
        s += p.quality
    if task == "route":
        s -= p.quality * 0.5   # routing is trivial → prefer the cheapest INSTANT model (8B)
    s += p.quality * 0.3       # overall brainpower tiebreak
    return s


def _candidates(tier: int = 1, model_name: str | None = None,
                task: str | None = None) -> list[Provider]:
    """Ordered list of live providers for this call.

    Task-aware: best capability match first, a reliable fast host guaranteed within the first
    two entries, throttled endpoints always last. Falls back to tier defaults when no task.
    """
    if task is None:
        task = "route" if tier == 1 else "write"
    now = time.time()
    live = [p for p in _ROTATION if p.disabled_until <= now]
    if not live:
        # everything is cooling down — try them all anyway (best-effort) rather than give up
        live = list(_ROTATION)

    fresh = [p for p in live if not p.throttled]
    throttled = [p for p in live if p.throttled]
    fresh.sort(key=lambda p: _score(p, task, model_name), reverse=True)
    throttled.sort(key=lambda p: _score(p, task, model_name), reverse=True)
    ordered = fresh + throttled

    # guarantee a reliable fast host appears within the first 2 entries
    if ordered and not any(p.fast_host for p in ordered[:2]):
        fast = next((p for p in ordered if p.fast_host), None)
        if fast:
            ordered.remove(fast)
            ordered.insert(1, fast)
    return ordered


# --------------------------------------------------------------------------- chat
def chat(messages: list[dict[str, str]], *, tier: int = 1,
         temperature: float = 0.2, max_tokens: int = 1024, model_name: str | None = None,
         task: str | None = None, request_timeout: float | None = None,
         max_429_retries: int | None = None, max_providers: int | None = None) -> str:
    """Send a chat completion through the rotation. Returns the assistant text.

    Latency caps (so a slow free model never hangs a request): `request_timeout` per call,
    `max_429_retries` (0 = no backoff), `max_providers` (try at most N then give up).
    `task` drives capability-aware provider ordering. Caller redacts PII (use Redactor).
    """
    if not _ROTATION:
        raise AllProvidersFailed(
            "No LLM providers configured. Set at least one provider key in .env."
        )

    retries = _MAX_429_RETRIES if max_429_retries is None else max_429_retries
    cands = _candidates(tier, model_name, task)
    if max_providers:
        cands = cands[:max_providers]

    last_err: Exception | None = None
    for prov in cands:
        # never inherit the SDK's 10-minute default — cap so a slow provider can't hang a request
        client = _client(prov, request_timeout if request_timeout is not None else 18)
        for attempt in range(retries + 1):  # retries=0 -> a single attempt, no backoff
            try:
                resp = client.chat.completions.create(
                    model=prov.model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **prov.extra,
                )
                prov.failures = 0
                _note(prov, "ok")
                _last.name = prov.name
                return resp.choices[0].message.content or ""
            except RateLimitError as exc:  # 429
                last_err = exc
                _note(prov, "429")
                if _is_quota(exc):  # daily budget gone -> bench until UTC reset, move on
                    prov.disabled_until = _next_utc_midnight()
                    prov.failures += 1
                    break
                if attempt < retries:
                    time.sleep(min(2 ** attempt, 4))
                    continue
                # per-minute limit -> bench briefly so we don't re-hit a throttled provider
                # (OpenRouter free can take 30-60s just to RETURN a 429 — never twice)
                prov.disabled_until = time.time() + _RL_COOLDOWN
                break
            except APIStatusError as exc:
                last_err = exc
                _note(prov, "fail")
                if exc.status_code in (401, 402, 403) or _is_quota(exc):  # auth/quota -> bench
                    prov.disabled_until = _next_utc_midnight() if _is_quota(exc) else time.time() + _CB_COOLDOWN
                    prov.failures += 1
                break  # other API errors -> next provider
            except Exception as exc:  # noqa: BLE001 - network/timeout -> next provider
                last_err = exc
                _note(prov, "fail")
                prov.disabled_until = time.time() + _CB_COOLDOWN
                break

    raise AllProvidersFailed(f"All providers failed. Last error: {last_err}")


_JSON_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def parse_json_object(text: str | None) -> dict | None:
    """The JSON object in a model's reply (code fences and chatter around it tolerated), or None."""
    t = _JSON_FENCE.sub("", str(text or "")).strip()
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(t[start:end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def chat_json(messages: list[dict], *, validate=None, tier: int = 1, task: str = "assist",
              temperature: float = 0.0, max_tokens: int = 300, request_timeout: float = 10.0,
              max_providers: int = 3, vision: bool = False) -> tuple[dict, str]:
    """One structured answer: `(object, provider name)` from the first provider whose reply parses
    as a JSON object and passes `validate`. A reply that is not the JSON asked for moves on to the
    next provider instead of failing the call. No 429 backoff and never a throttled host: the
    caller has a deterministic fallback. `vision=True`: only models that read images (the messages
    carry an image_url part). Caller redacts PII."""
    if not _ROTATION:
        raise AllProvidersFailed("No LLM providers configured.")
    cands = [p for p in _candidates(tier, None, task) if not p.throttled and (p.vision or not vision)][:max_providers]
    last_err: Exception | None = None
    for prov in cands:
        client = _client(prov, request_timeout)
        try:
            resp = client.chat.completions.create(model=prov.model, messages=messages,
                                                  temperature=temperature, max_tokens=max_tokens, **prov.extra)
            data = parse_json_object(resp.choices[0].message.content)
            if data is None or (validate is not None and not validate(data)):
                _note(prov, "bad")
                last_err = ValueError(f"{prov.name}: the reply was not the JSON asked for")
                continue
            prov.failures = 0
            _note(prov, "ok")
            return data, prov.name
        except RateLimitError as exc:
            last_err = exc
            _note(prov, "429")
            prov.disabled_until = _next_utc_midnight() if _is_quota(exc) else time.time() + _RL_COOLDOWN
        except APIStatusError as exc:
            last_err = exc
            _note(prov, "fail")
            if exc.status_code in (401, 402, 403) or _is_quota(exc):
                prov.disabled_until = _next_utc_midnight() if _is_quota(exc) else time.time() + _CB_COOLDOWN
                prov.failures += 1
        except Exception as exc:  # noqa: BLE001 - network/timeout -> next provider
            last_err = exc
            _note(prov, "fail")
            prov.disabled_until = time.time() + _CB_COOLDOWN
    raise AllProvidersFailed(f"json: all providers failed. Last error: {last_err}")


def chat_tools(messages: list[dict], tools: list[dict], *, tier: int = 2,
               temperature: float = 0.2, max_tokens: int = 1024, model_name: str | None = None,
               task: str | None = "synthesis", request_timeout: float = 20,
               max_providers: int = 3) -> dict:
    """One tool-calling step for the capable models. Returns
    {"content": str|None, "tool_calls": [{"id","name","arguments"}], "provider": name}.

    Reliable hosts only (throttled OpenRouter-free can't be driven through a loop). Fails fast
    (no 429 backoff) so the caller can fall back to the deterministic path. Caller redacts PII."""
    if not _ROTATION:
        raise AllProvidersFailed("No LLM providers configured.")
    cands = [p for p in _candidates(tier, model_name, task) if p.fast_host and not p.throttled][:max_providers]
    if not cands:
        raise AllProvidersFailed("No tool-capable provider available.")
    last_err: Exception | None = None
    for prov in cands:
        client = _client(prov, request_timeout)
        try:
            kwargs = {"model": prov.model, "messages": messages, "temperature": temperature,
                      "max_tokens": max_tokens, **prov.extra}
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"
            resp = client.chat.completions.create(**kwargs)
            msg = resp.choices[0].message
            tc = [{"id": t.id, "name": t.function.name, "arguments": t.function.arguments}
                  for t in (getattr(msg, "tool_calls", None) or [])]
            prov.failures = 0
            return {"content": msg.content, "tool_calls": tc, "provider": prov.name}
        except RateLimitError as exc:
            last_err = exc
            prov.disabled_until = _next_utc_midnight() if _is_quota(exc) else time.time() + _RL_COOLDOWN
        except APIStatusError as exc:
            last_err = exc
            if exc.status_code in (401, 402, 403) or _is_quota(exc):
                prov.disabled_until = _next_utc_midnight() if _is_quota(exc) else time.time() + _CB_COOLDOWN
        except Exception as exc:  # noqa: BLE001
            last_err = exc
    raise AllProvidersFailed(f"tool-calling: all providers failed. Last error: {last_err}")


def chat_stream(messages: list[dict[str, str]], *, tier: int = 2,
                temperature: float = 0.3, max_tokens: int = 700, model_name: str | None = None,
                task: str | None = None, request_timeout: float | None = None,
                max_providers: int | None = None):
    """Stream a chat completion, yielding text deltas as they arrive.

    Falls across providers on connect/auth errors. `request_timeout` caps time-to-first-token
    so a stalled free endpoint fails over fast. Caller redacts PII (Redactor)."""
    if not _ROTATION:
        yield "No LLM providers configured."
        return
    cands = _candidates(tier, model_name, task)
    if max_providers:
        cands = cands[:max_providers]
    for prov in cands:
        client = _client(prov, request_timeout if request_timeout is not None else 18)
        try:
            stream = client.chat.completions.create(
                model=prov.model, messages=messages, temperature=temperature,
                max_tokens=max_tokens, stream=True, **prov.extra,
            )
            got = False
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta.content
                if delta:
                    got = True
                    yield delta
            if got:
                prov.failures = 0
                return
        except RateLimitError as exc:
            prov.disabled_until = _next_utc_midnight() if _is_quota(exc) else time.time() + _RL_COOLDOWN
            continue
        except APIStatusError as exc:
            if exc.status_code in (401, 402, 403) or _is_quota(exc):
                prov.disabled_until = _next_utc_midnight() if _is_quota(exc) else time.time() + _CB_COOLDOWN
                prov.failures += 1
            continue
        except Exception:  # noqa: BLE001
            prov.disabled_until = time.time() + _CB_COOLDOWN
            continue
    yield "\n(All AI providers are busy right now — please try again.)"


# --------------------------------------------------------------------------- speech to text
# Whisper on Groq first (free, ~0.3 s), Voxtral on Mistral second. Measured 28-Oct-2026.
# Whisper "hears" these in silence or noise: a transcript that is only one of them is no speech.
_WHISPER_GHOSTS = frozenset({
    "thank you", "thank you.", "thanks for watching", "thanks for watching!", "you", "bye", "bye.",
    "شكرا", "شكراً", "شكرا لكم", "ترجمة نانسي قنقر", "اشتركوا في القناة", "مرحبا",
})


def _transcribers() -> list[tuple[str, str, str, str]]:
    out = []
    if settings.groq_api_key:
        out.append(("groq:whisper", "https://api.groq.com/openai/v1", settings.groq_api_key,
                    _env("GROQ_WHISPER_MODEL", "whisper-large-v3-turbo")))
    if settings.mistral_api_key:
        out.append(("mistral:voxtral", "https://api.mistral.ai/v1", settings.mistral_api_key,
                    _env("MISTRAL_STT_MODEL", "voxtral-mini-latest")))
    return out


def transcribe(audio: bytes, filename: str, content_type: str, *, prompt: str | None = None,
               request_timeout: float = 20.0) -> tuple[str, str]:
    """Speech to text: `(text, provider)`. '' when the recording holds no speech. Raises
    AllProvidersFailed when no transcriber answers. The audio is sent, never stored."""
    hosts = _transcribers()
    if not hosts:
        raise AllProvidersFailed("No speech-to-text provider configured.")
    last_err: Exception | None = None
    for name, base, key, model in hosts:
        client = OpenAI(base_url=base, api_key=key, timeout=request_timeout, max_retries=0)
        try:
            kw = {"prompt": prompt} if prompt and name.startswith("groq") else {}
            r = client.audio.transcriptions.create(model=model, file=(filename, audio, content_type), **kw)
            text = str(getattr(r, "text", "") or "").strip()
            return ("" if text.lower().strip(" .!") in {g.strip(" .!") for g in _WHISPER_GHOSTS} else text), name
        except Exception as exc:  # noqa: BLE001 — the next host
            last_err = exc
    raise AllProvidersFailed(f"speech: all providers failed. Last error: {last_err}")


def health() -> list[dict]:
    """Snapshot of the rotation for observability (no secrets)."""
    now = time.time()
    return [
        {
            "name": p.name,
            "model": p.model,
            "quality": p.quality,
            "fast_host": p.fast_host,
            "throttled": p.throttled,
            "tier2": p.tier2,
            "available": p.disabled_until <= now,
            "cooldown_s": max(0, round(p.disabled_until - now)),
            "failures": p.failures,
            "today": dict(_USAGE.get(p.name) or {}),
        }
        for p in _ROTATION
    ]
