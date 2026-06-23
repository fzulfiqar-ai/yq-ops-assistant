"""Free LLM provider rotation.

Design (see docs/CLAUDE.md):
- ~10 FREE provider/model entries across OpenRouter, Groq, Cerebras, Scaleway, Together.
- On 403 / quota: skip that provider immediately (and open its circuit breaker).
- On 429: wait 2^n seconds and retry the SAME provider (a few times) before moving on.
- Tier 1 (parsing/classification): full rotation.
- Tier 2 (final answers): strongest free models first.
- Redaction: redact()/restore() tokenize commercial PII (customer/account names) before any
  external call and map it back locally. Only redacted text leaves the network.

All providers are OpenAI-compatible, so we use the `openai` SDK with per-provider base_url.
No Anthropic, no paid APIs, no Claude CLI.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from openai import OpenAI
from openai import APIStatusError, RateLimitError

from app.config import settings


@dataclass
class Provider:
    name: str
    base_url: str
    api_key: str
    model: str
    tier2: bool = False  # eligible/strong for final answers
    # circuit breaker
    disabled_until: float = 0.0
    failures: int = 0


def _providers() -> list[Provider]:
    """Build the rotation from whatever keys are configured (missing keys are skipped).

    Tier-1 rotation = the whole list. Tier-2 = entries flagged tier2 first.
    Model ids are free tiers and can be tuned without code changes elsewhere.
    """
    p: list[Provider] = []
    if settings.openrouter_api_key:
        base = "https://openrouter.ai/api/v1"
        k = settings.openrouter_api_key
        # GLM-5.2 is the preferred strong free model; others are rotation fillers.
        p.append(Provider("openrouter:glm", base, k, "z-ai/glm-4.6:free", tier2=True))
        p.append(Provider("openrouter:deepseek", base, k, "deepseek/deepseek-chat-v3.1:free", tier2=True))
        p.append(Provider("openrouter:llama", base, k, "meta-llama/llama-3.3-70b-instruct:free"))
        p.append(Provider("openrouter:qwen", base, k, "qwen/qwen-2.5-72b-instruct:free"))
    if settings.groq_api_key:
        p.append(Provider("groq:llama70b", "https://api.groq.com/openai/v1",
                          settings.groq_api_key, "llama-3.3-70b-versatile", tier2=True))
        p.append(Provider("groq:llama8b", "https://api.groq.com/openai/v1",
                          settings.groq_api_key, "llama-3.1-8b-instant"))
    if settings.cerebras_api_key:
        p.append(Provider("cerebras:llama70b", "https://api.cerebras.ai/v1",
                          settings.cerebras_api_key, "llama-3.3-70b", tier2=True))
        p.append(Provider("cerebras:llama8b", "https://api.cerebras.ai/v1",
                          settings.cerebras_api_key, "llama3.1-8b"))
    if settings.scaleway_api_key:
        p.append(Provider("scaleway:llama", "https://api.scaleway.ai/v1",
                          settings.scaleway_api_key, "llama-3.3-70b-instruct"))
    if settings.together_api_key:
        p.append(Provider("together:llama", "https://api.together.xyz/v1",
                          settings.together_api_key,
                          "meta-llama/Llama-3.3-70B-Instruct-Turbo-Free", tier2=True))
    return p


_ROTATION: list[Provider] = _providers()

# circuit breaker / backoff tuning
_CB_COOLDOWN = 300.0      # seconds a provider stays disabled after a 403/quota
_MAX_429_RETRIES = 4      # per provider before moving on


class AllProvidersFailed(RuntimeError):
    pass


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


# --------------------------------------------------------------------------- chat
def _candidates(tier: int, model_name: str | None = None) -> list[Provider]:
    now = time.time()
    live = [p for p in _ROTATION if p.disabled_until <= now]
    if model_name:
        matching = []
        others = []
        for p in live:
            is_match = False
            if model_name == "pro":
                is_match = ("70b" in p.model.lower() or "glm" in p.model.lower() or p.tier2)
            elif model_name == "thinking":
                is_match = ("deepseek" in p.model.lower() or "glm" in p.model.lower() or "qwen" in p.model.lower())
            elif model_name == "fast":
                is_match = ("8b" in p.model.lower())
            
            if is_match:
                matching.append(p)
            else:
                others.append(p)
        return matching + others

    if tier == 2:
        return [p for p in live if p.tier2] + [p for p in live if not p.tier2]
    return live


def chat(messages: list[dict[str, str]], *, tier: int = 1,
         temperature: float = 0.2, max_tokens: int = 1024, model_name: str | None = None) -> str:
    """Send a chat completion through the rotation. Returns the assistant text.

    Caller is responsible for redacting PII in `messages` (use Redactor).
    """
    if not _ROTATION:
        raise AllProvidersFailed(
            "No LLM providers configured. Set at least one provider key in .env."
        )

    last_err: Exception | None = None
    for prov in _candidates(tier, model_name):
        client = OpenAI(base_url=prov.base_url, api_key=prov.api_key)
        for attempt in range(_MAX_429_RETRIES):
            try:
                resp = client.chat.completions.create(
                    model=prov.model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                prov.failures = 0
                return resp.choices[0].message.content or ""
            except RateLimitError as exc:  # 429 -> backoff, retry SAME provider
                last_err = exc
                time.sleep(2 ** attempt)
                continue
            except APIStatusError as exc:
                last_err = exc
                if exc.status_code in (401, 402, 403):  # auth/quota -> skip provider now
                    prov.disabled_until = time.time() + _CB_COOLDOWN
                    prov.failures += 1
                break  # other API errors -> next provider
            except Exception as exc:  # noqa: BLE001 - network etc -> next provider
                last_err = exc
                break

    raise AllProvidersFailed(f"All providers failed. Last error: {last_err}")


def chat_stream(messages: list[dict[str, str]], *, tier: int = 2,
                temperature: float = 0.3, max_tokens: int = 700, model_name: str | None = None):
    """Stream a chat completion, yielding text deltas as they arrive.

    Falls across providers on connect/auth errors. Caller redacts PII (Redactor)."""
    if not _ROTATION:
        yield "No LLM providers configured."
        return
    last_err: Exception | None = None
    for prov in _candidates(tier, model_name):
        client = OpenAI(base_url=prov.base_url, api_key=prov.api_key)
        try:
            stream = client.chat.completions.create(
                model=prov.model, messages=messages, temperature=temperature,
                max_tokens=max_tokens, stream=True,
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
            last_err = exc
            continue
        except APIStatusError as exc:
            last_err = exc
            if exc.status_code in (401, 402, 403):
                prov.disabled_until = time.time() + _CB_COOLDOWN
                prov.failures += 1
            continue
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            continue
    yield "\n(All AI providers are busy right now — please try again.)"


def health() -> list[dict]:
    """Snapshot of the rotation for observability (no secrets)."""
    now = time.time()
    return [
        {
            "name": p.name,
            "model": p.model,
            "tier2": p.tier2,
            "available": p.disabled_until <= now,
            "cooldown_s": max(0, round(p.disabled_until - now)),
            "failures": p.failures,
        }
        for p in _ROTATION
    ]
