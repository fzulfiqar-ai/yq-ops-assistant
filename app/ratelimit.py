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
"""
from __future__ import annotations

import hashlib

from starlette.requests import Request

from app.config import settings


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


def rate_limit_key(request: Request) -> str:
    """slowapi key: per user when a bearer token is present, per client IP otherwise."""
    auth = request.headers.get("authorization", "")
    if auth[:7].lower() == "bearer " and len(auth) > 27:
        digest = hashlib.sha256(auth[7:].strip().encode("utf-8")).hexdigest()
        return "u:" + digest[:16]
    return client_ip(request)
