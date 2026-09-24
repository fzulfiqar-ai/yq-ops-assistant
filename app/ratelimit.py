"""Who is calling? Proxy-aware client identity for rate limiting and abuse logging.

THE BUG THIS FIXES (16-Sep-2026). Render terminates TLS and forwards every request from its
own proxy, so `request.client.host` is the proxy's address for all traffic. slowapi's default
key function uses exactly that, which put every merchant into ONE bucket: the public order
endpoint's "5/minute" was effectively a global limit, and two shops ordering in the same
minute would have blocked each other. The same `parts[0]` mistake fed the hashed IP stored on
orders and events, so abuse logging pointed at whatever the client wrote into the header.

HOW TRUST WORKS. A proxy appends the address it saw to `X-Forwarded-For`, so with N trusted
proxy hops in front of us the real client is the N-th entry FROM THE RIGHT. Anything further
left was supplied by the client and is ignored. `TRUSTED_PROXY_HOPS` is 1 on Render (which
also exports RENDER=true, used as the default) and 0 everywhere else, where the header is not
trusted at all and the socket peer is used.

WHY NOT uvicorn --proxy-headers. With `--forwarded-allow-ips='*'` uvicorn walks the chain and
returns the LEFTMOST address once every hop is trusted, i.e. whatever the client wrote. The
hop count is the safe rule.

KEYS. Authenticated calls are keyed per user (a short hash of the bearer token) so the whole
office behind one NAT address never shares a bucket; public calls are keyed by client IP.

THE SECOND BUG (R1 security S5, 24-Sep-2026). The per-user key used to be handed to ANY
`Bearer` header, so a bot could mint a fresh bucket per request with junk tokens and the
public limits never bit. Now every /public/*, /team/* and /health call is keyed by client IP
only, and elsewhere a token earns a user bucket only after app.auth.get_current_user has
VERIFIED it.

THE KEY FUNCTION NEVER VERIFIES ANYTHING (review of R1). slowapi's middleware calls
`rate_limit_key` synchronously on the event loop for every request. A first version decoded
the token here; with an asymmetric header naming an unknown `kid`, PyJWT refreshes the JWKS
over the network (up to 30 s), so a burst of made-up tokens could freeze the single loop and
trip Render's health check (the ea49ea6 outage mode). Now `rate_limit_key` only looks a token
digest up in `_verified`, which `remember_verified` fills from get_current_user (threadpool)
after a successful decode. Unknown token → the IP bucket, always, at zero cost.
"""
from __future__ import annotations

import hashlib
import time

from starlette.requests import Request

from app.config import settings

# Paths where the caller's identity must never influence the bucket.
IP_ONLY_PREFIXES: tuple[str, ...] = ("/public/", "/team/")
IP_ONLY_PATHS: frozenset[str] = frozenset({"/health"})

_VERIFIED_TTL_S = 300      # a verified token keeps its user bucket this long without re-verifying
_CACHE_MAX = 4096
_verified: dict[str, float] = {}     # token digest → monotonic expiry


def reset_cache() -> None:
    """Forget every remembered token (tests)."""
    _verified.clear()


def _prune(cache: dict[str, float], now: float) -> None:
    if len(cache) < _CACHE_MAX:
        return
    for k in [k for k, exp in cache.items() if exp <= now]:
        cache.pop(k, None)
    if len(cache) >= _CACHE_MAX:     # still full of live entries: start over rather than grow
        cache.clear()


def token_digest(token: str) -> str:
    """The short, stable id of a bearer token — what the user bucket is keyed on."""
    return hashlib.sha256(token.strip().encode("utf-8")).hexdigest()[:16]


def remember_verified(token: str) -> None:
    """Called by app.auth.get_current_user (threadpool) after a token DECODED successfully.
    From now on (for _VERIFIED_TTL_S) requests carrying this token are keyed per user."""
    token = (token or "").strip()
    if len(token) < 20:
        return
    now = time.monotonic()
    _prune(_verified, now)
    _verified[token_digest(token)] = now + _VERIFIED_TTL_S


def client_ip(request: Request) -> str:
    """Best-effort client address: N-th-from-the-right X-Forwarded-For entry when N trusted
    proxy hops are configured, else the socket peer. Never the client-controlled left end."""
    hops = int(getattr(settings, "trusted_proxy_hops", 0) or 0)
    if hops > 0:
        xff = request.headers.get("x-forwarded-for", "")
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if len(parts) >= hops:
            return parts[-hops][:64]
    peer = request.client.host if request.client and request.client.host else "127.0.0.1"
    return peer[:64]


def user_key(token: str) -> str | None:
    """'u:<hash>' for a token get_current_user has already verified, None for anything else.
    A pure dictionary lookup: no decoding, no signature work, no network — ever."""
    token = token.strip()
    if len(token) < 20:
        return None
    digest = token_digest(token)
    if _verified.get(digest, 0.0) > time.monotonic():
        return "u:" + digest
    return None


def rate_limit_key(request: Request) -> str:
    """slowapi key: client IP on the public surface; per user only for a bearer token that
    get_current_user has verified; client IP for everything else (no token, a junk token, an
    expired one, or a valid token's very first request)."""
    path = request.scope.get("path") or ""
    if path in IP_ONLY_PATHS or path.startswith(IP_ONLY_PREFIXES):
        return client_ip(request)
    auth = request.headers.get("authorization", "")
    if auth[:7].lower() == "bearer ":
        key = user_key(auth[7:])
        if key:
            return key
    return client_ip(request)
