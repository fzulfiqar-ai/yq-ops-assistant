"""Backend SMTP sender for agent briefings.

The backend emails the briefing itself so schedulers (n8n / GitHub Actions) only
need to make ONE authenticated HTTP call — no SMTP credentials configured in n8n.

Reads the same env vars as scripts/send_digest.py:
  SMTP_HOST (default smtp.gmail.com) · SMTP_PORT (587) · SMTP_USER · SMTP_PASS ·
  ALERT_EMAIL_TO (comma-separated recipients)

`send_agent(result)` renders a clean light/purple HTML email from any agent result
dict (it auto-discovers the agent's list fields) and returns a status dict:
  {"emailed": True, "to": "..."}                      — sent
  {"emailed": False, "reason": "smtp_not_configured"} — env vars missing
  {"emailed": False, "reason": "smtp_error: ..."}     — SMTP raised
"""
from __future__ import annotations

import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

PURPLE = "#6d28d9"
PURPLE_DARK = "#4c1d95"

# result keys that are metadata, not displayable data tables
_META_KEYS = {"agent", "description", "generated_at", "summary", "email", "count"}


def smtp_configured() -> bool:
    return bool(os.getenv("SMTP_USER") and os.getenv("SMTP_PASS") and os.getenv("ALERT_EMAIL_TO"))


def send_html(subject: str, html: str) -> dict:
    user = os.getenv("SMTP_USER", "")
    pw = os.getenv("SMTP_PASS", "")
    to = os.getenv("ALERT_EMAIL_TO", "")
    host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    port = int(os.getenv("SMTP_PORT", "587"))
    if not (user and pw and to):
        return {"emailed": False, "reason": "smtp_not_configured"}
    recipients = [a.strip() for a in to.split(",") if a.strip()]
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(html, "html"))
    try:
        with smtplib.SMTP(host, port, timeout=20) as s:
            s.starttls()
            s.login(user, pw)
            s.sendmail(user, recipients, msg.as_string())
        return {"emailed": True, "to": to}
    except Exception as e:
        return {"emailed": False, "reason": f"smtp_error: {type(e).__name__}: {e}"}


def _fmt_cell(key: str, val) -> str:
    if val is None:
        return "—"
    if isinstance(val, bool):
        return "Yes" if val else "No"
    if isinstance(val, (int, float)) and "bhd" in key.lower():
        return f"BHD {float(val):,.2f}"
    if isinstance(val, float):
        return f"{val:,.2f}"
    return str(val)


def _table(title: str, rows: list[dict]) -> str:
    if not rows:
        return ""
    # column order from the first row; keep it compact (max 5 columns)
    cols = list(rows[0].keys())[:5]
    th = "".join(
        f'<th style="text-align:left;padding:8px 12px;font-size:.7rem;font-weight:700;'
        f'text-transform:uppercase;letter-spacing:.4px;color:#6d28d9;'
        f'border-bottom:2px solid #ede9fe;">{c.replace("_"," ").title()}</th>'
        for c in cols
    )
    body = ""
    for i, r in enumerate(rows[:15]):
        bg = "#ffffff" if i % 2 == 0 else "#faf9ff"
        tds = "".join(
            f'<td style="padding:8px 12px;font-size:.82rem;color:#1f2937;'
            f'border-bottom:1px solid #f1eefe;">{_fmt_cell(c, r.get(c))}</td>'
            for c in cols
        )
        body += f'<tr style="background:{bg};">{tds}</tr>'
    extra = (
        f'<div style="font-size:.72rem;color:#9ca3af;margin:6px 2px 0;">'
        f'… and {len(rows) - 15} more</div>'
        if len(rows) > 15 else ""
    )
    return (
        f'<div style="font-size:.9rem;font-weight:700;color:#4c1d95;margin:22px 0 8px;">{title}</div>'
        f'<table style="width:100%;border-collapse:collapse;background:#fff;border:1px solid #ede9fe;'
        f'border-radius:12px;overflow:hidden;">{th and f"<thead><tr>{th}</tr></thead>"}'
        f'<tbody>{body}</tbody></table>{extra}'
    )


def _agent_html(result: dict) -> str:
    title = result.get("description") or str(result.get("agent", "Agent")).replace("_", " ").title()
    summary = result.get("summary", "")
    generated = result.get("generated_at", "")

    tables = ""
    for key, val in result.items():
        if key in _META_KEYS:
            continue
        if isinstance(val, list) and val and isinstance(val[0], dict):
            tables += _table(key.replace("_", " ").title(), val)

    return f"""\
<div style="background:#f4f3ef;padding:24px 12px;font-family:Inter,Arial,sans-serif;">
  <div style="max-width:660px;margin:0 auto;background:#fff;border:1px solid #e9e6e0;border-radius:16px;overflow:hidden;">
    <div style="background:linear-gradient(135deg,{PURPLE},{PURPLE_DARK});color:#fff;padding:20px 26px;">
      <div style="font-size:1.15rem;font-weight:800;letter-spacing:.2px;">YQ Bahrain · {title}</div>
      <div style="font-size:.8rem;color:#ddd6fe;margin-top:3px;">AI Agent Briefing</div>
    </div>
    <div style="padding:24px 26px;">
      <p style="font-size:1.02rem;line-height:1.55;color:#111827;font-weight:600;margin:0 0 4px;">{summary}</p>
      {tables}
      <p style="font-size:.72rem;color:#9ca3af;margin-top:24px;padding-top:14px;border-top:1px solid #f1eefe;">
        Generated {generated} · YQ Bahrain AI agent team. AI-generated — verify figures before acting.
      </p>
    </div>
  </div>
</div>"""


def send_agent(result: dict) -> dict:
    """Render + email an agent result. Returns a status dict (never raises)."""
    name = str(result.get("agent", "agent"))
    title = result.get("description") or name.replace("_", " ").title()
    subject = f"YQ {title} — {datetime.now().strftime('%d %b %Y')}"
    try:
        return send_html(subject, _agent_html(result))
    except Exception as e:  # defensive: emailing must never break the agent run
        return {"emailed": False, "reason": f"render_error: {type(e).__name__}: {e}"}
