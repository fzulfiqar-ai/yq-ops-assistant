"""
A local, read-only stand-in for the production API, for design reviews and lab runs.

    python scripts/qa/readonly_api.py --cache <dir> [--port 8002] [--upcoming business_data/upcoming_review/2026-09-24]

Build the market with VITE_API_URL=http://localhost:8002, and everything the page does on its own
goes through here: Lighthouse, the QA harness, or a person clicking around a local preview. That
includes the funnel `view` ping and the vitals beacon, which Playwright's route mocks never see
outside the harness.

- GET / HEAD: recorded once, then replayed from --cache. Two builds reviewed one after the other see
  byte-identical data, with no 60/min limit and no cold start.
- POST …/quote: recorded once per request body, then replayed. It prices a cart and writes nothing.
- Every other method is answered here, never forwarded: {"ok": true, "qa": "not sent"} (or a
  fictional order or receipt for the checkout flow), and logged to writes.log.
- Upstream is asked for GET, HEAD and POST …/quote only, and nothing else can be sent upstream.
  upstream.log records every call it makes, so a run can prove it wrote nothing.
- With --upcoming <review folder>, /public/market/upcoming answers the owner's pending WEKOME review
  rows (rows.json + thumbs/) as "Coming soon" cards. This is preview data, not published.

Binds to 127.0.0.1 only. Stdlib only.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

UPSTREAM = "https://yq-ops-assistant.onrender.com"
LOCAL_ORIGIN_PREFIXES = ("http://localhost:", "http://127.0.0.1:")

# The fictional order the checkout flow lands on (the harness mocks the same shapes in-page).
QA_ORDER = {"order_no": "QA-0001", "token": "qa-token", "order_kind": "standard", "status": "new", "status_label": "Received", "assigned": False}


def qa_receipt() -> dict:
    """A fictional tracking receipt: QA shop, QA rep, three real catalog lines and no real person."""
    now = dt.datetime.now().replace(microsecond=0)
    lines = [
        {"item_code": "UK04-C", "display_name": "UK04 20W Charger + Type-C Cable (2USB Port) (VFAN)", "qty": 12, "qty_confirmed": None, "line_status": "ok", "unit_price_bhd": 1.0, "line_total_bhd": 12.0, "stock_status": "in_stock"},
        {"item_code": "X13-L", "display_name": "X13 3A 1.2Mtr NYLON BRAIDED LIGHTNING USB Cable (VFAN)", "qty": 6, "qty_confirmed": None, "line_status": "ok", "unit_price_bhd": 1.1, "line_total_bhd": 6.6, "stock_status": "in_stock"},
        {"item_code": "UK15", "display_name": "UK15", "qty": 4, "qty_confirmed": None, "line_status": "ok", "unit_price_bhd": 1.5, "line_total_bhd": 6.0, "stock_status": "in_stock"},
    ]
    total = round(sum(ln["line_total_bhd"] for ln in lines), 3)
    stages = [("new", "Received"), ("confirmed", "Confirmed"), ("packed", "Preparing"), ("out_for_delivery", "On the way"), ("delivered", "Delivered")]
    return {
        "order_no": "QA-0001",
        "order_kind": "standard",
        "status": "new",
        "status_label": "Received",
        "steps": [{"status": s, "label": lbl, "done": i == 0, "current": i == 0, "at": now.isoformat() if i == 0 else None} for i, (s, lbl) in enumerate(stages)],
        "cancelled": False,
        "can_cancel": True,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "salesman": {"name": "QA Rep", "first_name": "QA", "whatsapp_url": "https://wa.me/97300000000", "email_url": None},
        "customer": {"name": "QA Merchant", "shop": "QA Test Shop", "area": "Manama"},
        "lines": lines,
        "subtotal_bhd": total,
        "discount_bhd": 0,
        "delivery_bhd": 0,
        "total_bhd": total,
        "total_confirmed_bhd": None,
        "has_changes": False,
        "has_backorder": False,
        "note": None,
        "timeline": [{"ts": now.isoformat(), "event": "placed", "note": None}],
    }


def month_label(month: str | None) -> tuple[str, str]:
    """ "Arriving October" while that month is ahead or current, then "Arriving soon" (as app/upcoming.py). """
    ar_months = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]
    try:
        m = dt.date.fromisoformat(str(month)[:10])
    except ValueError:
        return "Arriving soon", "قريباً"
    today = dt.date.today()
    if (m.year, m.month) < (today.year, today.month):
        return "Arriving soon", "قريباً"
    return "Arriving " + m.strftime("%B"), "يصل في " + ar_months[m.month - 1]


class Stub:
    def __init__(self, upstream: str, cache: Path, port: int, upcoming: Path | None, offline: bool) -> None:
        self.upstream = upstream.rstrip("/")
        self.cache = cache
        self.port = port
        self.offline = offline
        self.upcoming_dir = upcoming
        self.lock = threading.Lock()
        (cache / "bodies").mkdir(parents=True, exist_ok=True)
        self.logs = {k: open(cache / (k + ".log"), "a", encoding="utf-8") for k in ("requests", "writes", "upstream")}

    # ── logging ──
    def log(self, kind: str, line: str) -> None:
        with self.lock:
            f = self.logs[kind]
            f.write(dt.datetime.now().isoformat(timespec="seconds") + " " + line + "\n")
            f.flush()

    # ── the one place that talks to production ──
    def fetch_upstream(self, method: str, path: str, body: bytes | None, ctype: str | None) -> tuple[int, str, bytes]:
        is_quote = method == "POST" and urlsplit(path).path.endswith("/quote")
        if method not in ("GET", "HEAD") and not is_quote:
            raise RuntimeError("refusing to send " + method + " " + path + " upstream")  # never reached: callers mock writes
        if self.offline:
            return 503, "application/json", json.dumps({"detail": "stub is offline and this was never recorded"}).encode()
        req = urllib.request.Request(self.upstream + path, data=body if is_quote else None, method=method)
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", "yq-readonly-stub/1 (design review, GET only)")
        if is_quote and ctype:
            req.add_header("Content-Type", ctype)
        self.log("upstream", method + " " + path)
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return r.status, r.headers.get("Content-Type") or "application/octet-stream", r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Content-Type") or "application/json", e.read()
        except Exception as e:  # noqa: BLE001
            return 502, "application/json", json.dumps({"detail": "upstream unreachable: " + str(e)[:200]}).encode()

    def recorded(self, method: str, path: str, body: bytes | None, ctype: str | None) -> tuple[int, str, bytes, str]:
        key = hashlib.sha1((method + " " + path + " ").encode() + (body or b"")).hexdigest()
        meta_p, body_p = self.cache / "bodies" / (key + ".json"), self.cache / "bodies" / (key + ".bin")
        if meta_p.exists() and body_p.exists():
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
            return meta["status"], meta["ctype"], body_p.read_bytes(), "replay"
        status, rtype, data = self.fetch_upstream("GET" if method == "HEAD" else method, path, body, ctype)
        # record only what is stable; a 5xx or 429 is served as-is and asked again next time
        if status < 500 and status != 429:
            body_p.write_bytes(data)
            meta_p.write_text(json.dumps({"status": status, "ctype": rtype, "method": method, "path": path, "at": dt.datetime.now().isoformat(timespec="seconds")}), encoding="utf-8")
        return status, rtype, data, "upstream"

    # ── fixtures ──
    def upcoming_payload(self) -> dict | None:
        if not self.upcoming_dir:
            return None
        rows = json.loads((self.upcoming_dir / "rows.json").read_text(encoding="utf-8"))
        base = "http://localhost:" + str(self.port) + "/qa-upcoming/thumbs/"
        items = []
        for r in rows:
            en, ar = month_label(r.get("expected_month"))
            code = r["model_code"]
            items.append(
                {
                    "id": 900000 + int(r.get("sort_order") or len(items) + 1),
                    "brand": r.get("brand") or "WEKOME",
                    "model_code": code,
                    "category": r.get("category"),
                    "name_en": r.get("name_en"),
                    "name_ar": r.get("name_ar"),
                    "spec_en": r.get("spec_en"),
                    "spec_ar": r.get("spec_ar"),
                    "variants": [{"label": v.get("label"), "label_ar": v.get("label_ar")} for v in (r.get("variants") or [])],
                    "photo_url": base + code + "-product-512.webp",
                    "photo_thumb_urls": {s: base + code + "-product-" + s + ".webp" for s in ("160", "320", "512")},
                    "box_url": base + code + "-box-512.webp",
                    "box_thumb_urls": {s: base + code + "-box-" + s + ".webp" for s in ("160", "320", "512")},
                    "expected_label_en": en,
                    "expected_label_ar": ar,
                    "sort_order": r.get("sort_order"),
                }
            )
        en, ar = month_label(rows[0].get("expected_month") if rows else None)
        return {"enabled": True, "brand": "WEKOME", "count": len(items), "expected_label_en": en, "expected_label_ar": ar, "items": items}


def make_handler(stub: Stub):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # quiet console; requests.log has everything
            return

        def cors(self) -> dict:
            origin = self.headers.get("Origin") or ""
            h = {"Vary": "Origin"}
            if origin.startswith(LOCAL_ORIGIN_PREFIXES):
                h["Access-Control-Allow-Origin"] = origin
                h["Access-Control-Allow-Credentials"] = "true"
                h["Access-Control-Expose-Headers"] = "*"
            return h

        def send(self, status: int, ctype: str, data: bytes, source: str, head: bool = False) -> None:
            self.send_response(status)
            for k, v in self.cors().items():
                self.send_header(k, v)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Qa-Source", source)
            self.end_headers()
            if not head:
                self.wfile.write(data)
            stub.log("requests", self.command + " " + self.path + " " + str(status) + " " + source)

        def send_json(self, payload, source: str, status: int = 200) -> None:
            self.send(status, "application/json", json.dumps(payload).encode(), source, head=self.command == "HEAD")

        def body(self) -> bytes:
            n = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(n) if n > 0 else b""

        def do_OPTIONS(self):  # noqa: N802
            self.send_response(204)
            for k, v in self.cors().items():
                self.send_header(k, v)
            self.send_header("Access-Control-Allow-Methods", "GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", self.headers.get("Access-Control-Request-Headers") or "*")
            self.send_header("Access-Control-Max-Age", "600")
            self.send_header("Content-Length", "0")
            self.end_headers()
            stub.log("requests", "OPTIONS " + self.path + " 204 local")

        def do_GET(self):  # noqa: N802
            path = urlsplit(self.path).path
            if path.startswith("/qa-upcoming/thumbs/") and stub.upcoming_dir:
                f = (stub.upcoming_dir / "thumbs" / path.rsplit("/", 1)[-1]).resolve()
                if f.parent == (stub.upcoming_dir / "thumbs").resolve() and f.exists():
                    self.send(200, "image/webp", f.read_bytes(), "fixture", head=self.command == "HEAD")
                else:
                    self.send_json({"detail": "not found"}, "fixture", 404)
                return
            if path == "/public/market/upcoming" and stub.upcoming_dir:
                self.send_json(stub.upcoming_payload(), "fixture")
                return
            if path.startswith("/public/shop/order/qa-token"):
                self.send_json(qa_receipt(), "fixture")
                return
            status, ctype, data, source = stub.recorded(self.command, self.path, None, None)
            self.send(status, ctype, data, source, head=self.command == "HEAD")

        do_HEAD = do_GET

        def write(self) -> None:
            data = self.body()
            path = urlsplit(self.path).path
            if self.command == "POST" and path.endswith("/quote"):
                status, ctype, out, source = stub.recorded("POST", self.path, data, self.headers.get("Content-Type"))
                self.send(status, ctype, out, source)
                return
            stub.log("writes", self.command + " " + self.path + " " + data[:300].decode("utf-8", "replace").replace("\n", " "))
            if path == "/public/market/order":
                self.send_json(QA_ORDER, "mock")
            elif path == "/public/market/recognize":
                self.send_json({"known": None}, "mock")
            elif path == "/public/shop/my-orders":
                self.send_json({"orders": []}, "mock")
            else:
                self.send_json({"ok": True, "qa": "not sent"}, "mock")

        do_POST = do_PUT = do_PATCH = do_DELETE = write

    return Handler


def main() -> int:
    ap = argparse.ArgumentParser(description="local read-only API stub (record/replay GET, never forward a write)")
    ap.add_argument("--cache", required=True, help="record/replay folder; also holds requests.log, writes.log, upstream.log")
    ap.add_argument("--port", type=int, default=8002)
    ap.add_argument("--upstream", default=UPSTREAM)
    ap.add_argument("--upcoming", default="", help="an upcoming review folder (rows.json + thumbs/) served as the Coming soon list")
    ap.add_argument("--offline", action="store_true", help="replay only; never contact upstream")
    args = ap.parse_args()
    stub = Stub(args.upstream, Path(args.cache).resolve(), args.port, Path(args.upcoming).resolve() if args.upcoming else None, args.offline)
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(stub))
    print("readonly stub on http://127.0.0.1:" + str(args.port) + " -> " + stub.upstream + (" (offline)" if args.offline else "") + " | cache " + str(stub.cache), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
