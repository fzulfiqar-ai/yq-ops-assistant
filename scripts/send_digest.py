"""Phase 2.5 — Standalone digest email sender.

Sends daily ops digest or alert emails via SMTP (Gmail or any SMTP server).
No n8n or Railway needed — runs directly with Supabase credentials.

Usage:
  python -m scripts.send_digest --type daily
  python -m scripts.send_digest --type alerts
  python -m scripts.send_digest --type all

Schedule via:
  - Windows Task Scheduler (local)
  - Railway cron job (cloud)
  - n8n Execute Command node
"""
from __future__ import annotations

import os
import smtplib
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv()

from app.digest import all_alerts, daily_summary  # noqa: E402

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
EMAIL_TO   = os.getenv("ALERT_EMAIL_TO", "")

PURPLE = "#7c3aed"
DARK   = "#0f0820"


def _send(subject: str, html: str) -> None:
    if not SMTP_USER or not EMAIL_TO:
        print("SMTP not configured — set SMTP_USER, SMTP_PASS, ALERT_EMAIL_TO in .env")
        print(f"Would send: {subject}")
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = SMTP_USER
    msg["To"]      = EMAIL_TO
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
        s.starttls()
        s.login(SMTP_USER, SMTP_PASS)
        s.sendmail(SMTP_USER, EMAIL_TO.split(","), msg.as_string())
    print(f"✓ Sent: {subject} → {EMAIL_TO}")


def _header(title: str) -> str:
    return f"""
    <div style="background:{DARK};padding:0;margin:0;font-family:Inter,Arial,sans-serif;">
    <div style="max-width:600px;margin:0 auto;padding:24px 16px;">
    <div style="background:linear-gradient(135deg,{PURPLE},{DARK});border-radius:16px;padding:24px 28px;margin-bottom:20px;">
      <div style="font-size:1.5rem;font-weight:800;color:#e9d5ff;">YQ Bahrain · AI Operations</div>
      <div style="font-size:.85rem;color:#a78bfa;margin-top:4px;">{title} · {datetime.now().strftime('%d %b %Y %H:%M')}</div>
    </div>
    """


def _footer() -> str:
    return """
    <div style="text-align:center;font-size:.72rem;color:#475569;margin-top:24px;padding-top:16px;border-top:1px solid #1e1b4b;">
      YQ Bahrain W.L.L · AI Operations Assistant · Authorised internal use only
    </div></div></div>
    """


def _card(title: str, value: str, sub: str = "", color: str = "#a78bfa") -> str:
    return f"""
    <div style="background:#1a0d35;border:1px solid #3730a3;border-radius:12px;padding:16px 20px;margin-bottom:12px;">
      <div style="font-size:.75rem;color:#6366f1;font-weight:600;text-transform:uppercase;letter-spacing:.5px;">{title}</div>
      <div style="font-size:1.6rem;font-weight:800;color:{color};margin:6px 0 4px;">{value}</div>
      <div style="font-size:.78rem;color:#94a3b8;">{sub}</div>
    </div>"""


def _table(headers: list[str], rows: list[list[str]], color: str = "#7c3aed") -> str:
    th = "".join(f'<th style="padding:8px 12px;text-align:left;font-size:.72rem;color:#6366f1;font-weight:600;text-transform:uppercase;border-bottom:1px solid #3730a3;">{h}</th>' for h in headers)
    tr_html = ""
    for i, row in enumerate(rows):
        bg = "#0f0820" if i % 2 == 0 else "#130d25"
        td = "".join(f'<td style="padding:8px 12px;font-size:.82rem;color:#e2e8f0;">{c}</td>' for c in row)
        tr_html += f'<tr style="background:{bg};">{td}</tr>'
    return f"""
    <table style="width:100%;border-collapse:collapse;background:#1a0d35;border-radius:12px;overflow:hidden;margin-bottom:16px;">
      <thead><tr>{th}</tr></thead>
      <tbody>{tr_html}</tbody>
    </table>"""


def send_daily(data: dict | None = None) -> None:
    if data is None:
        data = daily_summary()
    rev_mtd   = data.get("rev_mtd", 0)
    rev_prev  = data.get("rev_prev_month", 0)
    delta_pct = ((rev_mtd - rev_prev) / rev_prev * 100) if rev_prev > 0 else 0
    delta_str = f"{'↑' if delta_pct >= 0 else '↓'} {abs(delta_pct):.1f}% vs last month"
    delta_col = "#10b981" if delta_pct >= 0 else "#ef4444"

    top_rows = [
        [r.get("customer_name", "")[:35], f"BHD {float(r.get('total_revenue_bhd', 0)):,.2f}", str(r.get("order_count", 0))]
        for r in data.get("top_customers", [])
    ]

    html = _header("Daily Operations Digest")
    html += f"""
    <div style="display:grid;gap:12px;margin-bottom:20px;">
      {_card("Revenue Today", f"BHD {data.get('rev_today', 0):,.2f}", f"{data.get('orders_today', 0)} invoices today")}
      {_card("Revenue MTD", f"BHD {rev_mtd:,.2f}", delta_str, delta_col)}
      {_card("Orders MTD", str(data.get('orders_mtd', 0)), "Invoices processed this month")}
      {_card("Outstanding Receivables", f"BHD {data.get('total_receivables', 0):,.2f}", "Total debtor balance", "#f59e0b")}
    </div>
    """
    if top_rows:
        html += f'<div style="font-size:.9rem;font-weight:700;color:#c4b5fd;margin:16px 0 8px;">🏆 Top Customers This Month</div>'
        html += _table(["Customer", "Revenue", "Orders"], top_rows)

    html += _footer()
    _send(f"YQ Ops Daily Digest — {datetime.now().strftime('%d %b %Y')}", html)


def send_alerts(data: dict | None = None) -> None:
    if data is None:
        data = all_alerts()
    if not data.get("has_alerts"):
        print("No alerts to send today.")
        return

    html = _header("Operations Alert")

    if data.get("low_stock"):
        rows = [
            [r.get("item_name", "")[:40], r.get("warehouse_name", ""), str(r.get("balance_qty", 0))]
            for r in data["low_stock"][:15]
        ]
        html += f'<div style="font-size:.9rem;font-weight:700;color:#f59e0b;margin:16px 0 8px;">⚠️ Low Stock ({data["low_stock_count"]} items)</div>'
        html += _table(["Item", "Warehouse", "Qty"], rows, "#f59e0b")

    if data.get("overdue_receivables"):
        rows = [
            [r.get("account", "")[:35], f"BHD {float(r.get('outstanding_bhd', 0)):,.2f}", f"{r.get('days_outstanding', 0)} days"]
            for r in data["overdue_receivables"][:10]
        ]
        html += f'<div style="font-size:.9rem;font-weight:700;color:#ef4444;margin:16px 0 8px;">🏦 Overdue Receivables — BHD {data["overdue_total_bhd"]:,.2f} ({data["overdue_count"]} accounts)</div>'
        html += _table(["Account", "Outstanding", "Days Overdue"], rows, "#ef4444")

    if data.get("negative_margins"):
        rows = [
            [r.get("item_name", "")[:35], f"{float(r.get('gp_margin_pct', 0)):+.1f}%", r.get("category_name", "")]
            for r in data["negative_margins"][:10]
        ]
        html += f'<div style="font-size:.9rem;font-weight:700;color:#ec4899;margin:16px 0 8px;">📉 Negative Margins ({data["negative_margin_count"]} products)</div>'
        html += _table(["Product", "GP Margin", "Category"], rows, "#ec4899")

    html += _footer()
    alerts_count = data["low_stock_count"] + data["overdue_count"] + data["negative_margin_count"]
    _send(f"YQ Ops Alert — {alerts_count} Issues Detected — {datetime.now().strftime('%d %b %Y')}", html)


def main(argv: list[str]) -> int:
    dtype = "all"
    for arg in argv:
        if arg.startswith("--type="):
            dtype = arg.split("=", 1)[1]
        elif arg in {"daily", "alerts", "all"}:
            dtype = arg

    if dtype in {"daily", "all"}:
        send_daily()
    if dtype in {"alerts", "all"}:
        send_alerts()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
