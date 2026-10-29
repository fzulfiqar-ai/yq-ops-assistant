"""Which free models answer today: one small JSON task and one Arabic sentence per provider in
app/llm_router.py's rotation, timed. Read-only (two tiny chat calls per model; no database, no
file written); prints a table and never a key.

    python -m scripts.probe_models              # every provider whose key is set
    python -m scripts.probe_models groq mistral # only these hosts

Free model IDs move (28-Oct-2026: Groq's llama-3.3/3.1, Cerebras's zai-glm-4.7 and NVIDIA's
deepseek-v4-pro were all gone), so run it after any "All AI providers are busy" and before a
release that leans on the router. A model that fails here: set its *_MODEL override (see
.env.example) to one its host lists, or leave it, and the rotation benches it by itself.
"""
from __future__ import annotations

import io
import sys
import time

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

JSON_TASK = [
    {"role": "system", "content": 'Reply with ONE JSON object and nothing else: {"intent": "find" or "chat", '
                                  '"query": "the product in English catalogue words"}'},
    {"role": "user", "content": "عندكم شاحن سيارة سريع؟"},
]
ARABIC_TASK = [
    {"role": "system", "content": "Answer in Arabic, one short sentence, plain text."},
    {"role": "user", "content": "Facts: C07 is an 18W fast car charger, in stock. Question: do you have a fast car charger?"},
]
_ARABIC = range(0x0600, 0x0700)


def _one(client, prov, msgs, max_tokens):
    t = time.monotonic()
    r = client.chat.completions.create(model=prov.model, messages=msgs, max_tokens=max_tokens, temperature=0,
                                       **prov.extra)
    return (r.choices[0].message.content or "").strip(), int((time.monotonic() - t) * 1000)


def probe(only: set[str] | None = None, timeout: float = 20.0) -> list[dict]:
    from app.llm_router import _ROTATION, _client, parse_json_object
    rows = []
    for prov in _ROTATION:
        if only and prov.name.split(":")[0] not in only:
            continue
        row = {"provider": prov.name, "model": prov.model, "json": "", "arabic": "", "ms": None, "error": ""}
        client = _client(prov, timeout)
        try:
            text, ms = _one(client, prov, JSON_TASK, 300)
            d = parse_json_object(text)
            row["json"] = "ok" if d and d.get("intent") in ("find", "chat") else "bad"
            row["ms"] = ms
            text, ms2 = _one(client, prov, ARABIC_TASK, 200)
            row["arabic"] = "ok" if any(ord(ch) in _ARABIC for ch in text) else ("empty" if not text else "not arabic")
            row["ms"] = max(ms, ms2)
        except Exception as e:  # noqa: BLE001 — the error class and the first words, never the request
            msg = str(e).replace("\n", " ")
            row["error"] = f"{type(e).__name__}: {msg[:90]}"
        rows.append(row)
    return rows


def main(argv: list[str]) -> int:
    rows = probe(set(argv) or None)
    if not rows:
        print("No provider key is set (see .env.example).")
        return 1
    w = max(len(r["provider"]) for r in rows)
    print(f"{'provider':<{w}}  {'json':<5} {'arabic':<10} {'ms':>6}  model / error")
    good = 0
    for r in rows:
        ok = r["json"] == "ok" and r["arabic"] == "ok"
        good += ok
        tail = r["error"] or r["model"]
        ms = "" if r["ms"] is None else str(r["ms"])
        print(f"{r['provider']:<{w}}  {r['json'] or '-':<5} {r['arabic'] or '-':<10} {ms:>6}  {tail}")
    print(f"\n{good} of {len(rows)} models answer both tasks.")
    return 0 if good else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
