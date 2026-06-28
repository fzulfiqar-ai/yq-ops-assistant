"""Notification channels — Telegram (free) + the existing email layer. Every send is
best-effort and gated on config: no token => that channel is simply skipped. Used by the
escalation/alerts and the debtor-reminder drafts.

Telegram setup: talk to @BotFather -> /newbot -> copy the token into TELEGRAM_BOT_TOKEN;
get your chat id (message the bot, then GET /getUpdates) into TELEGRAM_CHAT_ID.
"""
from __future__ import annotations

import logging

import requests

from app.config import settings

log = logging.getLogger(__name__)


def telegram_enabled() -> bool:
    return bool(settings.telegram_bot_token and settings.telegram_chat_id)


def send_telegram(text: str, chat_id: str | None = None) -> bool:
    if not settings.telegram_bot_token:
        return False
    cid = chat_id or settings.telegram_chat_id
    if not cid:
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
            json={"chat_id": cid, "text": text[:4000], "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=10,
        )
        if not r.ok:
            log.warning("telegram %s: %s", r.status_code, r.text[:120])
        return r.ok
    except Exception as e:  # noqa: BLE001
        log.warning("telegram send failed: %s", e)
        return False


def notify(subject: str, body_text: str, html: str | None = None) -> dict:
    """Fan out to every enabled channel. Returns {channel: bool}."""
    out: dict[str, bool] = {}
    if telegram_enabled():
        out["telegram"] = send_telegram(f"<b>{subject}</b>\n\n{body_text}")
    try:
        from app.emailer import send_html
        res = send_html(subject, html or f"<pre>{body_text}</pre>")
        out["email"] = bool(res.get("emailed"))  # the dict is always truthy — read the real flag
        if not out["email"]:
            log.warning("email not sent: %s", res.get("reason"))
    except Exception as e:  # noqa: BLE001
        log.warning("email notify failed: %s", e)
        out["email"] = False
    return out


def channels() -> list[str]:
    """Which channels are currently live (for status/health)."""
    import os
    live = []
    if telegram_enabled():
        live.append("telegram")
    if os.getenv("RESEND_API_KEY") or os.getenv("BREVO_API_KEY") or os.getenv("SMTP_HOST"):
        live.append("email")
    return live
