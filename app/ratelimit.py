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

THE THIRD BUG (R7d, audit SEC-11, 27-Sep-2026). Render sits behind Cloudflare, so the entry the
hop rule picks (the right-most one) is a Cloudflare edge address, not the merchant: a read-only
count on 27-Sep-2026 found 3 distinct hashed addresses a day across 225 devices in shop_events.
Every merchant in Bahrain still shared a handful of buckets. Cloudflare OVERWRITES the
CF-Connecting-IP header with the address it saw (a client cannot choose it through Cloudflare), so
behind a trusted proxy (TRUSTED_PROXY_HOPS > 0) that header is the client; without it the hop rule
applies as before. Locally (hops 0) no header is trusted at all. TRUST_CF_CONNECTING_IP=0 turns the
header off should the API ever stop sitting behind Cloudflare. The first request of each process
logs the SHAPE of what arrived (how many X-Forwarded-For entries, whether CF-Connecting-IP was
there — never an address), which is the one-off verification the audit asked for.

PER REAL CLIENT (R7d). Many shops share one address (mobile carriers put thousands of phones
behind one NAT), so the order, cancel and restock routes carry TWO limits: a generous one per
address (the net against a flood) and the real one per address + device (`device_key`: the
device_id in the JSON body) or address + order token (`order_token_key`: the cancel link). A
request without a device falls back to the address alone, i.e. exactly the old strictness. The
per-phone and per-device daily caps in app.shop.create_order stay as they are.
"""
from __future__ import annotations

import hashlib
import ipaddress
import logging
import time

from starlette.requests import Request

from app.config import settings

log = logging.getLogger(__name__)
_shape_logged = False

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


def _cf_ip(request: Request) -> str | None:
    """CF-Connecting-IP when it holds one syntactically valid address, else None."""
    raw = (request.headers.get("cf-connecting-ip") or "").strip()
    if not raw or len(raw) > 64:
        return None
    try:
        ipaddress.ip_address(raw)
    except ValueError:
        return None
    return raw


def _log_shape_once(request: Request) -> None:
    """One INFO line per process: how the proxy chain presents the client — the number of
    X-Forwarded-For entries and whether CF-Connecting-IP arrived. Never an address."""
    global _shape_logged
    if _shape_logged:
        return
    _shape_logged = True
    xff = request.headers.get("x-forwarded-for", "")
    n = len([p for p in xff.split(",") if p.strip()])
    log.info("client ip shape: x-forwarded-for entries=%d, cf-connecting-ip=%s, trusted hops=%d, cf trusted=%s",
             n, "present" if _cf_ip(request) else "absent", int(getattr(settings, "trusted_proxy_hops", 0) or 0),
             bool(getattr(settings, "trust_cf_connecting_ip", True)))


def client_ip(request: Request) -> str:
    """Best-effort client address. Behind a trusted proxy (hops > 0): CF-Connecting-IP when
    Cloudflare fronts the request (it overwrites that header, so the client cannot pick it), else
    the N-th-from-the-right X-Forwarded-For entry. No trusted proxy: the socket peer. Never the
    client-controlled left end of X-Forwarded-For."""
    hops = int(getattr(settings, "trusted_proxy_hops", 0) or 0)
    if hops > 0:
        _log_shape_once(request)
        if getattr(settings, "trust_cf_connecting_ip", True):
            cf = _cf_ip(request)
            if cf:
                return cf
        xff = request.headers.get("x-forwarded-for", "")
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if len(parts) >= hops:
            return parts[-hops][:64]
    peer = request.client.host if request.client and request.client.host else "127.0.0.1"
    return peer[:64]


def _short(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _body_device(request: Request) -> str | None:
    """The device_id of the JSON body FastAPI already parsed for this request (Starlette keeps it
    on the request as `_json`), or an `x-device-id` header. Never reads the stream itself: the key
    function runs synchronously, and a route whose body was not parsed simply has no device."""
    dev = None
    body = getattr(request, "_json", None)
    if isinstance(body, dict):
        dev = body.get("device_id")
    if not dev:
        dev = request.headers.get("x-device-id")
    dev = str(dev or "").strip()
    return dev[:64] or None


def device_key(request: Request) -> str:
    """slowapi key: client address + device (hashed) — one bucket per phone even when a whole
    carrier NAT shares the address; the address alone when the request names no device."""
    ip = client_ip(request)
    dev = _body_device(request)
    return f"{ip}|d:{_short(dev)}" if dev else ip


def order_token_key(request: Request) -> str:
    """slowapi key for the merchant's order-token routes (cancel): address + that order's token
    (hashed). A token is one merchant's one order, so neighbours behind one NAT never share it."""
    ip = client_ip(request)
    tok = str((request.path_params or {}).get("order_token") or "").strip()
    return f"{ip}|o:{_short(tok)}" if tok else ip


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
