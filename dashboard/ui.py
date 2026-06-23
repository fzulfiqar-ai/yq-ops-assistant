"""YQ Bahrain — AI Agent Portal + Operations Dashboard.

Premium light corporate portal: white + purple, Space Grotesk display type, warm
paper canvas, hairline cards, purple brand sidebar, SVG line-icons, full-bleed
desktop login, 3D KPI cards, professional chat with model selector + sessions.
Session persists across nav + refresh via a process-level token store.

Run: streamlit run dashboard/ui.py
"""
from __future__ import annotations

import base64
import secrets
import sys
import uuid
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LOGO_PATH = ROOT / "YQ LOGO" / "LOGO.jpeg"


def _logo_b64() -> str:
    return base64.b64encode(LOGO_PATH.read_bytes()).decode() if LOGO_PATH.exists() else ""


_LOGO = _logo_b64()

try:
    from PIL import Image
    _PAGE_ICON = Image.open(LOGO_PATH) if LOGO_PATH.exists() else "🟣"
except Exception:
    _PAGE_ICON = "🟣"

st.set_page_config(page_title="YQ Bahrain | AI Portal", page_icon=_PAGE_ICON, layout="wide", initial_sidebar_state="expanded")

from app.ai import ask as ai_ask, exec_sql  # noqa: E402
from app import user_auth                   # noqa: E402

# ── SVG icons ────────────────────────────────────────────────────────────────
_ICON_PATHS = {
    "grid": '<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/>',
    "cpu": '<rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><line x1="9" y1="1" x2="9" y2="4"/><line x1="15" y1="1" x2="15" y2="4"/><line x1="9" y1="20" x2="9" y2="23"/><line x1="15" y1="20" x2="15" y2="23"/><line x1="20" y1="9" x2="23" y2="9"/><line x1="20" y1="14" x2="23" y2="14"/><line x1="1" y1="9" x2="4" y2="9"/><line x1="1" y1="14" x2="4" y2="14"/>',
    "message": '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
    "box": '<path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/>',
    "trending": '<polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/>',
    "percent": '<line x1="19" y1="5" x2="5" y2="19"/><circle cx="6.5" cy="6.5" r="2.5"/><circle cx="17.5" cy="17.5" r="2.5"/>',
    "card": '<rect x="1" y="4" width="22" height="16" rx="2" ry="2"/><line x1="1" y1="10" x2="23" y2="10"/>',
    "logout": '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/>',
    "dollar": '<line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/>',
    "file": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>',
    "alert": '<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
    "bank": '<line x1="3" y1="21" x2="21" y2="21"/><line x1="3" y1="10" x2="21" y2="10"/><polyline points="5 6 12 3 19 6"/><line x1="4" y1="10" x2="4" y2="21"/><line x1="20" y1="10" x2="20" y2="21"/><line x1="8" y1="14" x2="8" y2="17"/><line x1="12" y1="14" x2="12" y2="17"/><line x1="16" y1="14" x2="16" y2="17"/>',
    "users": '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
    "wallet": '<path d="M20 12V8H6a2 2 0 0 1-2-2c0-1.1.9-2 2-2h12v4"/><path d="M4 6v12a2 2 0 0 0 2 2h14v-4"/><path d="M18 12a2 2 0 0 0 0 4h4v-4z"/>',
    "zap": '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>',
    "check": '<polyline points="20 6 9 17 4 12"/>',
    "plus": '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
    "award": '<circle cx="12" cy="8" r="7"/><polyline points="8.21 13.89 7 23 12 20 17 23 15.79 13.88"/>',
    "sparkles": '<path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/>',
}


def icon(name: str, size: int = 20, stroke: str = "currentColor", sw: float = 2) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 24 24" '
            f'fill="none" stroke="{stroke}" stroke-width="{sw}" stroke-linecap="round" stroke-linejoin="round">'
            f'{_ICON_PATHS.get(name, "")}</svg>')


NAV = [("Dashboard", "grid"), ("AI Agents", "cpu"), ("AI Assistant", "message"),
       ("Inventory", "box"), ("Sales", "trending"), ("Margins", "percent"), ("Receivables", "card")]

QUOTES = [
    "Artificial intelligence is the new electricity.  —  Andrew Ng",
    "The best way to predict the future is to invent it.  —  Alan Kay",
    "AI won't replace you — someone using AI will.",
    "Data is the new oil; intelligence is the refinery.",
    "Automate the predictable, so your team can focus on the exceptional.",
    "The future is already here — it's just not evenly distributed.  —  William Gibson",
    "Machines that think, so people are free to create.",
]


def _quotes_html(big: bool = False) -> str:
    n, dur = len(QUOTES), 4
    cls = "quote-big" if big else "quote-sm"
    spans = "".join(f'<div class="quote-line" style="animation-delay:{i*dur}s;animation-duration:{n*dur}s;">{q}</div>' for i, q in enumerate(QUOTES))
    return f'<div class="quote-rail {cls}">{spans}</div>'


# ── CSS — premium light (white + purple) ──────────────────────────────────────
CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=Space+Grotesk:wght@400;500;600;700&display=swap');
:root{--purple-700:#4c1d95;--purple-600:#6d28d9;--purple-500:#7c3aed;--purple-400:#8b5cf6;--purple-100:#ece7f9;--purple-50:#f7f5fb;--green:#0f7a52;--gold:#b45309;--red:#be123c;--blue:#2563eb;--bg:#f4f3ef;--card:#ffffff;--surface2:#f7f5fb;--border:#e9e6e0;--border-bright:#dcd6ec;--text:#181820;--text-muted:#6b6b76;--text-soft:#9b9ba6;--radius:12px;--radius-lg:16px;--shadow:0 1px 2px rgba(20,16,40,0.04),0 10px 26px rgba(20,16,40,0.05);--shadow-hover:0 14px 40px rgba(20,16,40,0.12);--head:'Space Grotesk','Inter',sans-serif;}
html,body,[class*="css"],.stApp{font-family:'Inter',sans-serif!important;background:var(--bg)!important;color:var(--text)!important;-webkit-font-smoothing:antialiased;}
html,body,.stApp,[data-testid="stAppViewContainer"]{overflow-x:hidden!important;max-width:100vw!important;}
.ph-title,.kpi-value,.lc-title,.lb-tag,.lb-name,.section-header,.glass-title,.agent-name,.model-name,[data-testid="stMetricValue"]{font-family:var(--head)!important;letter-spacing:-.012em;}
.kpi-value,[data-testid="stMetricValue"]{font-variant-numeric:tabular-nums;}
[data-testid="stSidebar"]{background:linear-gradient(180deg,#2a1259 0%,#1c0b3f 100%)!important;border-right:1px solid rgba(255,255,255,0.06)!important;}
[data-testid="stSidebar"] *{color:#e9e4fb;}
[data-testid="stSidebarNav"]{display:none;}
#MainMenu,footer,[data-testid="stToolbar"],[data-testid="stDecoration"],[data-testid="stStatusWidget"]{visibility:hidden;}
[data-testid="stHeader"]{display:none!important;height:0!important;}
[data-testid="stAppViewContainer"]{padding-top:0!important;}
[data-testid="stAppViewContainer"]>.main>.block-container{padding-top:1.2rem!important;}
[data-testid="stSidebar"]{transform:none!important;min-width:266px!important;width:266px!important;}
[data-testid="stSidebarCollapseButton"],[data-testid="collapsedControl"]{display:none!important;}
.block-container{max-width:100%!important;padding:1.4rem 2.2rem 3rem!important;}

.yq-side{display:flex;flex-direction:column;min-height:calc(100vh - 2.2rem);}
.yq-logo{display:flex;justify-content:center;padding:4px 0 2px;}
.yq-logo .logo-3d{width:76px;height:76px;border-radius:20px;object-fit:cover;box-shadow:0 10px 30px rgba(0,0,0,0.4),0 0 0 1px rgba(255,255,255,0.08);animation:logoFloat 4.5s ease-in-out infinite;transition:transform .45s cubic-bezier(.2,.8,.2,1);transform-style:preserve-3d;}
.yq-logo .logo-3d:hover{transform:rotateY(20deg) rotateX(10deg) scale(1.06);box-shadow:0 18px 44px rgba(124,58,237,0.55);}
@keyframes logoFloat{0%,100%{transform:translateY(0) rotateZ(0)}50%{transform:translateY(-7px) rotateZ(-1.2deg)}}
.yq-brand{text-align:center;margin:9px 0 4px;}
.yq-brand-name{font-size:1.08rem;font-weight:800;color:#fff;}
.yq-brand-sub{font-size:.7rem;color:#b9aee6;margin-top:2px;letter-spacing:.3px;}
.yq-sec{font-size:.62rem;font-weight:700;color:#9b8fd0;letter-spacing:1.2px;text-transform:uppercase;margin:18px 0 8px;padding-left:6px;}
.yq-nav{display:flex;flex-direction:column;gap:3px;}
.yq-nav a{display:flex;align-items:center;gap:11px;padding:11px 13px;border-radius:11px;font-size:.89rem;font-weight:500;color:#cfc6ee!important;text-decoration:none!important;transition:all .18s;}
.yq-nav a:hover{background:rgba(255,255,255,0.08);color:#fff!important;}
.yq-nav a.active{background:linear-gradient(135deg,rgba(124,58,237,0.6),rgba(124,58,237,0.32));color:#fff!important;box-shadow:0 6px 18px rgba(124,58,237,0.35);}
.yq-nav a svg{flex-shrink:0;opacity:.95;}
.yq-bottom{margin-top:auto;padding-top:18px;}
.yq-user{background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.1);border-radius:13px;padding:11px 13px;display:flex;align-items:center;gap:10px;}
.yq-ava{width:38px;height:38px;border-radius:50%;background:linear-gradient(135deg,var(--purple-500),var(--purple-400));display:flex;align-items:center;justify-content:center;font-size:1rem;font-weight:700;color:#fff;flex-shrink:0;}
.yq-uname{font-size:.84rem;font-weight:600;color:#fff;}
.yq-upill{display:inline-block;margin-top:2px;padding:1px 8px;border-radius:9px;background:rgba(245,158,11,0.2);color:#fcd34d;font-size:.6rem;font-weight:700;letter-spacing:.5px;}
.yq-signout{display:flex;align-items:center;justify-content:center;gap:8px;margin-top:10px;padding:10px;border-radius:11px;background:rgba(255,255,255,0.08);border:1px solid rgba(255,255,255,0.16);color:#e9e4fb!important;text-decoration:none!important;font-size:.84rem;font-weight:600;transition:all .18s;}
.yq-signout:hover{background:rgba(255,255,255,0.16);}
.yq-foot{text-align:center;font-size:.63rem;color:#8478b3;margin-top:12px;}

.page-header{display:flex;align-items:center;gap:16px;padding:22px 26px;background:var(--card);border:1px solid var(--border);border-radius:var(--radius-lg);margin-bottom:24px;box-shadow:var(--shadow);}
.ph-ic{width:46px;height:46px;border-radius:12px;background:var(--purple-50);display:flex;align-items:center;justify-content:center;color:var(--purple-600);flex-shrink:0;border:1px solid var(--border-bright);}
.ph-title{font-size:1.45rem;font-weight:600;color:var(--text);letter-spacing:-.02em;}
.ph-sub{font-size:.82rem;color:var(--text-muted);margin-top:2px;}
.ph-badge{margin-left:auto;background:rgba(15,122,82,0.1);border:1px solid rgba(15,122,82,0.25);color:var(--green);padding:5px 13px;border-radius:20px;font-size:.74rem;font-weight:600;display:flex;align-items:center;gap:6px;white-space:nowrap;}
.dot{width:8px;height:8px;background:#10b981;border-radius:50%;animation:dot 2s infinite;}
@keyframes dot{0%,100%{transform:scale(1);opacity:1}50%{transform:scale(1.4);opacity:.6}}

.quote-rail{position:relative;}
.quote-sm{height:20px;margin-top:6px;}.quote-big{height:120px;margin-top:30px;}
.quote-line{position:absolute;left:0;top:0;width:100%;opacity:0;animation-name:qRot;animation-iteration-count:infinite;animation-timing-function:ease-in-out;}
.quote-sm .quote-line{font-size:.8rem;color:var(--purple-600);font-weight:500;font-style:italic;}
.quote-big .quote-line{font-size:1.55rem;color:#f1ecff;font-weight:600;line-height:1.45;}
@keyframes qRot{0%{opacity:0;transform:translateY(13px)}3%{opacity:1;transform:translateY(0)}14%{opacity:1;transform:translateY(0)}17%{opacity:0;transform:translateY(-13px)}100%{opacity:0}}

.kpi-wrap{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:16px;margin:4px 0 22px;perspective:1400px;}
.kpi-card{background:var(--card);border:1px solid var(--border);border-radius:var(--radius-lg);padding:24px 22px 20px;position:relative;overflow:hidden;box-shadow:var(--shadow);transition:transform .4s cubic-bezier(.2,.8,.2,1),box-shadow .4s;transform-style:preserve-3d;}
.kpi-card::before{content:'';position:absolute;top:0;left:0;right:0;height:3px;background:var(--accent,var(--purple-500));}
.kpi-card::after{content:'';position:absolute;inset:0;background:radial-gradient(120% 80% at 50% -10%,color-mix(in srgb,var(--accent,#7c3aed) 9%,transparent),transparent 60%);opacity:0;transition:opacity .4s;pointer-events:none;}
.kpi-card:hover{transform:translateY(-7px) rotateX(5deg) rotateY(-3deg) scale(1.02);box-shadow:0 28px 54px rgba(20,16,40,0.18);}
.kpi-card:hover::after{opacity:1;}
.kpi-wrap>.kpi-card:nth-child(1){--accent:#6d28d9;}
.kpi-wrap>.kpi-card:nth-child(2){--accent:#2563eb;}
.kpi-wrap>.kpi-card:nth-child(3){--accent:#d97706;}
.kpi-wrap>.kpi-card:nth-child(4){--accent:#0f7a52;}
.kpi-ic{color:var(--accent,var(--purple-500));margin-bottom:14px;opacity:.9;}
.kpi-value{font-size:2rem;font-weight:600;color:var(--text);line-height:1.05;}
.kpi-label{font-size:.72rem;color:var(--text-muted);margin-top:6px;font-weight:600;letter-spacing:.5px;text-transform:uppercase;}
.kpi-delta{font-size:.8rem;margin-top:8px;font-weight:600;}
.kpi-delta.up{color:var(--green);}.kpi-delta.warn{color:var(--gold);}.kpi-delta.down{color:var(--red);}

.glass{background:var(--card);border:1px solid var(--border);border-radius:var(--radius-lg);padding:20px;box-shadow:var(--shadow);}
.glass-title{font-size:.96rem;font-weight:700;color:var(--text);margin-bottom:14px;display:flex;align-items:center;gap:8px;}
.glass-title svg{color:var(--purple-600);}
.section-header{font-size:1rem;font-weight:700;color:var(--text);margin:26px 0 14px;display:flex;align-items:center;gap:9px;}
.section-header svg{color:var(--purple-600);}
.section-header::after{content:'';flex:1;height:1px;background:linear-gradient(90deg,var(--border),transparent);}

.styled-table{width:100%;border-collapse:collapse;font-size:.83rem;}
.styled-table th{background:var(--purple-50);color:var(--purple-700);padding:10px 14px;font-weight:700;font-size:.7rem;letter-spacing:.5px;text-transform:uppercase;border-bottom:1px solid var(--border);text-align:left;}
.styled-table td{padding:9px 14px;border-bottom:1px solid var(--border);color:var(--text);}
.styled-table tr:hover td{background:var(--purple-50);}
.badge{display:inline-block;padding:2px 9px;border-radius:12px;font-size:.7rem;font-weight:600;}
.badge-purple{background:var(--purple-100);color:var(--purple-700);}

.agent-card{background:var(--card);border:1px solid var(--border);border-radius:var(--radius-lg);padding:18px;box-shadow:var(--shadow);transition:transform .25s,box-shadow .25s;}
.agent-card:hover{transform:translateY(-4px);box-shadow:var(--shadow-hover);}
.agent-ic{width:42px;height:42px;border-radius:12px;display:flex;align-items:center;justify-content:center;color:#fff;margin-bottom:12px;}
.agent-name{font-size:.98rem;font-weight:700;color:var(--text);}
.agent-desc{font-size:.8rem;color:var(--text-muted);margin-top:5px;line-height:1.45;min-height:34px;}
.agent-summary{font-size:.82rem;color:var(--purple-700);font-weight:600;background:var(--purple-50);border:1px solid var(--border);border-radius:10px;padding:9px 12px;margin-top:10px;}

/* Model selector tiles */
.model-wrap{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:2px 0 20px;}
.model-tile{display:flex;flex-direction:column;background:var(--card);border:1px solid var(--border);border-radius:var(--radius-lg);padding:16px 18px;text-decoration:none!important;box-shadow:var(--shadow);transition:transform .2s,box-shadow .2s,border-color .2s;position:relative;}
.model-tile:hover{transform:translateY(-3px);box-shadow:var(--shadow-hover);}
.model-tile.sel{border-color:var(--purple-500);box-shadow:0 0 0 3px rgba(124,58,237,0.15),var(--shadow);}
.model-top{display:flex;align-items:center;justify-content:space-between;}
.model-ic{width:34px;height:34px;border-radius:10px;background:var(--purple-50);color:var(--purple-600);display:flex;align-items:center;justify-content:center;border:1px solid var(--border-bright);}
.model-bdg{font-size:.64rem;font-weight:700;text-transform:uppercase;letter-spacing:.5px;padding:3px 9px;border-radius:20px;}
.model-bdg.pro{background:rgba(109,40,217,0.12);color:var(--purple-700);}
.model-bdg.thinking{background:rgba(37,99,235,0.12);color:var(--blue);}
.model-bdg.fast{background:rgba(180,83,9,0.12);color:var(--gold);}
.model-name{font-size:.95rem;font-weight:700;color:var(--text);margin-top:12px;}
.model-desc{font-size:.76rem;color:var(--text-muted);margin-top:4px;line-height:1.45;}
.model-chk{position:absolute;top:14px;right:14px;width:20px;height:20px;border-radius:50%;background:var(--purple-600);color:#fff;display:none;align-items:center;justify-content:center;}
.model-tile.sel .model-chk{display:flex;}
.model-tile.sel .model-bdg{visibility:hidden;}

.chat2{display:flex;flex-direction:column;gap:18px;padding:10px 4px 8px;min-height:320px;}
.cm{display:flex;gap:12px;align-items:flex-start;max-width:780px;}
.cm-ai{align-self:flex-start;}
.cm-user{align-self:flex-end;flex-direction:row-reverse;}
.cm-av{width:34px;height:34px;border-radius:10px;display:flex;align-items:center;justify-content:center;flex-shrink:0;font-size:.78rem;font-weight:700;}
.cm-av-ai{background:linear-gradient(135deg,var(--purple-600),var(--purple-500));color:#fff;box-shadow:0 4px 12px rgba(124,58,237,0.3);}
.cm-av-user{background:#e7e4f2;color:var(--purple-700);}
.cm-bub{border-radius:14px;padding:13px 17px;font-size:.9rem;line-height:1.62;}
.cm-bub-ai{background:#fff;border:1px solid var(--border);color:var(--text);box-shadow:var(--shadow);border-top-left-radius:4px;}
.cm-bub-user{background:linear-gradient(135deg,var(--purple-600),var(--purple-500));color:#fff;border-top-right-radius:4px;}
.cm-meta{font-size:.7rem;color:var(--text-soft);margin-top:9px;display:flex;gap:8px;align-items:center;}
.ai-label{display:inline-flex;align-items:center;gap:6px;font-size:.68rem;font-weight:700;color:var(--purple-600);margin-bottom:7px;letter-spacing:.4px;text-transform:uppercase;}
.chat2-empty{display:flex;flex-direction:column;align-items:center;justify-content:center;height:300px;color:var(--text-soft);text-align:center;}
.chat-hist-h{font-size:.66rem;font-weight:700;color:var(--text-muted);letter-spacing:.6px;text-transform:uppercase;margin:14px 0 8px;}
[data-testid="stChatInput"]{border:1px solid var(--border)!important;border-radius:14px!important;background:#fff!important;box-shadow:var(--shadow)!important;}
[data-testid="stBottomBlockContainer"],[data-testid="stChatInput"] > div{background:transparent!important;}

.stTextInput input,.stTextArea textarea,.stSelectbox div[data-baseweb="select"]>div{background:#fff!important;border:1px solid var(--border)!important;border-radius:10px!important;color:var(--text)!important;font-family:'Inter',sans-serif!important;}
.stTextInput input:focus,.stTextArea textarea:focus{border-color:var(--purple-400)!important;box-shadow:0 0 0 3px rgba(124,58,237,0.15)!important;}
.stTextInput input::placeholder{color:var(--text-soft)!important;}
.stButton>button{background:linear-gradient(135deg,var(--purple-600),var(--purple-500))!important;border:none!important;color:#fff!important;border-radius:10px!important;font-weight:600!important;font-family:'Inter',sans-serif!important;transition:all .2s!important;box-shadow:0 4px 14px rgba(124,58,237,0.25)!important;}
.stButton>button:hover{transform:translateY(-2px)!important;box-shadow:0 8px 22px rgba(124,58,237,0.35)!important;}
[data-testid="stExpander"]{border:1px solid var(--border)!important;border-radius:var(--radius-lg)!important;background:var(--card)!important;box-shadow:var(--shadow)!important;overflow:hidden;}
[data-testid="stExpander"] summary{font-weight:700!important;color:var(--text)!important;}
[data-testid="stMetricValue"]{color:var(--purple-700)!important;font-weight:800!important;}
[data-testid="stMetricLabel"]{color:var(--text-muted)!important;}
.js-plotly-plot .plotly{background:transparent!important;}
</style>
"""

# Full-bleed desktop login (edge-to-edge split, fills the whole screen)
LOGIN_CSS = """
<style>
[data-testid="stSidebar"]{display:none!important;}
[data-testid="stAppViewContainer"]{overflow:hidden!important;}
.block-container{padding:0 23% 0 50%!important;max-width:100%!important;height:100vh!important;display:flex!important;flex-direction:column!important;justify-content:center!important;}
.lb{position:fixed;left:0;top:0;width:44%;height:100vh;background:linear-gradient(160deg,#2a1259 0%,#3b1d7a 52%,#1c0b3f 100%);padding:0 5.5%;display:flex;flex-direction:column;justify-content:center;overflow:hidden;z-index:5;}
.lb::after{content:'';position:absolute;right:-150px;bottom:-150px;width:460px;height:460px;border-radius:50%;background:radial-gradient(circle,rgba(139,92,246,0.4),transparent 70%);}
.lb-logo .logo-3d{width:108px;height:108px;border-radius:26px;box-shadow:0 22px 50px rgba(0,0,0,0.5);animation:logoIntro 1.05s cubic-bezier(.2,.85,.25,1) both,logoFloat 5s ease-in-out 1.1s infinite;}
@keyframes logoIntro{0%{opacity:0;transform:translateY(28px) scale(.55) rotateY(-50deg);}60%{opacity:1;}100%{opacity:1;transform:translateY(0) scale(1) rotateY(0);}}
@keyframes fadeUp{from{opacity:0;transform:translateY(18px);}to{opacity:1;transform:translateY(0);}}
.lb-name{color:#fff;font-size:1.24rem;font-weight:800;margin-top:24px;animation:fadeUp .7s ease .3s both;}
.lb-tag{color:#fff;font-size:2.35rem;font-weight:800;line-height:1.16;margin-top:26px;letter-spacing:-.6px;animation:fadeUp .7s ease .45s both;}
.lb-feat{margin-top:32px;display:flex;flex-direction:column;gap:15px;animation:fadeUp .7s ease .6s both;}
.lb-feat div{color:#d8cffb;font-size:.95rem;display:flex;align-items:center;gap:12px;}
.lb-feat span{width:32px;height:32px;border-radius:10px;background:rgba(255,255,255,0.12);display:flex;align-items:center;justify-content:center;color:#fff;}
.quote-big{animation:fadeUp .7s ease .75s both;}
.lc-title{font-size:1.62rem;font-weight:800;color:var(--text);margin-bottom:5px;}
.lc-sub{font-size:.86rem;color:var(--text-muted);margin-bottom:24px;}
.lc-err{background:rgba(190,18,60,0.08);border:1px solid rgba(190,18,60,0.25);color:#9f1239;border-radius:10px;padding:10px 14px;font-size:.82rem;margin-bottom:16px;}
.lc-foot{color:var(--text-soft);font-size:.72rem;margin-top:18px;}
.block-container .stTextInput input{background:#eef0f6!important;border:1px solid transparent!important;border-radius:12px!important;padding:13px 16px!important;font-size:.95rem!important;}
.block-container .stTextInput input:focus{background:#fff!important;border-color:var(--purple-400)!important;box-shadow:0 0 0 3px rgba(124,58,237,0.15)!important;}
</style>
"""


# ── Data helpers ─────────────────────────────────────────────────────────────

def _safe(sql: str) -> list[dict]:
    try:
        return exec_sql(sql) or []
    except Exception:
        return []


@st.cache_data(ttl=300, show_spinner=False)
def _kpi_data() -> dict:
    rev = _safe("SELECT COALESCE(SUM(total_amount_bhd),0) AS rev, COUNT(DISTINCT invoice_no) AS orders FROM v_sales WHERE DATE_TRUNC('month',sale_date)=DATE_TRUNC('month',CURRENT_DATE) LIMIT 1")
    prev = _safe("SELECT COALESCE(SUM(total_amount_bhd),0) AS rev FROM v_sales WHERE DATE_TRUNC('month',sale_date)=DATE_TRUNC('month',CURRENT_DATE-INTERVAL '1 month') LIMIT 1")
    low = _safe("SELECT COUNT(*) AS n FROM v_low_stock LIMIT 1")
    rec = _safe("SELECT COALESCE(SUM(outstanding_bhd),0) AS total FROM v_receivables LIMIT 1")
    return {"rev": float(rev[0]["rev"]) if rev else 0, "orders": int(rev[0]["orders"]) if rev else 0,
            "prev_rev": float(prev[0]["rev"]) if prev else 0, "low_stock": int(low[0]["n"]) if low else 0,
            "receivables": float(rec[0]["total"]) if rec else 0}


@st.cache_data(ttl=300, show_spinner=False)
def _digest_data() -> dict:
    try:
        from app.digest import all_alerts, daily_summary
        return {**daily_summary(), **all_alerts(), "ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@st.cache_data(ttl=300, show_spinner=False)
def _monthly_trend() -> list[dict]:
    return _safe("SELECT period_month, net_revenue_bhd, order_count, total_qty FROM v_sales_by_period ORDER BY period_month LIMIT 24")


@st.cache_data(ttl=300, show_spinner=False)
def _top_customers() -> list[dict]:
    return _safe("SELECT customer_name, total_revenue_bhd, order_count, last_order_date FROM v_top_customers LIMIT 8")


@st.cache_data(ttl=300, show_spinner=False)
def _query(sql: str) -> list[dict]:
    return _safe(sql)


@st.cache_resource
def _store() -> dict:
    """Process-wide session store — survives reruns AND full page reloads."""
    return {}


def _check_login(email: str, password: str) -> dict | None:
    """Per-user login via Supabase Auth (no shared password)."""
    return user_auth.verify_login(email, password)


# ── Login (full-bleed light split) ───────────────────────────────────────────

def login_page():
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown(LOGIN_CSS, unsafe_allow_html=True)
    logo = f'<img class="logo-3d" src="data:image/jpeg;base64,{_LOGO}" />' if _LOGO else "🟣"
    err = ""
    if st.session_state.get("login_error"):
        err = f'<div class="lc-err">⚠ {st.session_state.login_error}</div>'
        st.session_state.login_error = ""
    st.markdown(f"""
    <div class="lb">
      <div class="lb-logo">{logo}</div>
      <div class="lb-name">YQ Bahrain · AI Portal</div>
      <div class="lb-tag">Run your whole<br>business from<br>one place.</div>
      <div class="lb-feat">
        <div><span>{icon('cpu',16)}</span> A full team of AI agents working for you</div>
        <div><span>{icon('message',16)}</span> Ask anything — sales, stock, margins, receivables</div>
        <div><span>{icon('alert',16)}</span> Secure, role-based, audited access</div>
      </div>
      {_quotes_html(big=True)}
    </div>
    """, unsafe_allow_html=True)
    st.markdown('<div class="lc-title">Sign in</div>', unsafe_allow_html=True)
    st.markdown('<div class="lc-sub">Welcome back — sign in to your control room.</div>', unsafe_allow_html=True)
    if err:
        st.markdown(err, unsafe_allow_html=True)
    email = st.text_input("Email", placeholder="you@example.com", key="li_email")
    pwd = st.text_input("Password", type="password", placeholder="••••••••", key="li_pwd")
    if st.button("Sign In  →", use_container_width=True):
        if not email or not pwd:
            st.session_state.login_error = "Please enter your email and password."
            st.rerun()
        user = _check_login(email, pwd)
        if user:
            _start_session(user)
        else:
            st.session_state.login_error = "Invalid credentials or access not granted."
            st.rerun()
    st.markdown('<div class="lc-foot">Authorised access only · YQ Bahrain W.L.L</div>', unsafe_allow_html=True)


def _start_session(user: dict):
    """Create a token session for a logged-in user and redirect into the portal."""
    token = secrets.token_urlsafe(16)
    _store()[token] = {"user": user, "chats": {}, "active_chat": None, "agent_results": {}}
    st.query_params.clear()
    st.query_params["t"] = token
    st.rerun()


def set_password_page(*, invite: dict | None = None, reset_user: dict | None = None):
    """Set-password screen for (a) accepting an email invite, or (b) a forced
    first-login reset for a temp-password account."""
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown(LOGIN_CSS, unsafe_allow_html=True)
    logo = f'<img class="logo-3d" src="data:image/jpeg;base64,{_LOGO}" />' if _LOGO else "🟣"
    if invite is not None:
        email = invite["email"]
        role = invite.get("role", "member")
        headline = f"You're invited as a <b>{role.title()}</b>"
        cta = "Activate my account  →"
    else:
        email = reset_user["email"]
        headline = "Set a new password to continue"
        cta = "Update password  →"
    err = ""
    if st.session_state.get("sp_error"):
        err = f'<div class="lc-err">⚠ {st.session_state.sp_error}</div>'
        st.session_state.sp_error = ""
    st.markdown(f"""
    <div class="lb">
      <div class="lb-logo">{logo}</div>
      <div class="lb-name">YQ Bahrain · AI Portal</div>
      <div class="lb-tag">Set your<br>password to<br>get started.</div>
      <div class="lb-feat">
        <div><span>{icon('alert',16)}</span> Your password is encrypted and never shared</div>
        <div><span>{icon('check',16)}</span> Use at least 8 characters</div>
      </div>
      {_quotes_html(big=True)}
    </div>
    """, unsafe_allow_html=True)
    st.markdown('<div class="lc-title">Welcome</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="lc-sub">{headline}<br><b>{email}</b></div>', unsafe_allow_html=True)
    if err:
        st.markdown(err, unsafe_allow_html=True)
    p1 = st.text_input("New password", type="password", placeholder="••••••••", key="sp_p1")
    p2 = st.text_input("Confirm password", type="password", placeholder="••••••••", key="sp_p2")
    if st.button(cta, use_container_width=True):
        if not p1 or len(p1) < 8:
            st.session_state.sp_error = "Password must be at least 8 characters."
            st.rerun()
        if p1 != p2:
            st.session_state.sp_error = "Passwords do not match."
            st.rerun()
        try:
            if invite is not None:
                user = user_auth.accept_invite(invite["token"], p1)
                if not user:
                    st.session_state.sp_error = "This invite is no longer valid."
                    st.rerun()
                user["must_reset"] = False
                _start_session(user)
            else:
                user_auth.set_password(reset_user["email"], p1)
                reset_user["must_reset"] = False
                st.rerun()
        except Exception:
            st.session_state.sp_error = "Could not set the password. Please try again."
            st.rerun()
    st.markdown('<div class="lc-foot">Authorised access only · YQ Bahrain W.L.L</div>', unsafe_allow_html=True)


# ── Sidebar ──────────────────────────────────────────────────────────────────

def _nav_for(user: dict) -> list[tuple[str, str]]:
    """Nav items this user may see: admins get everything + Team; members get
    only their granted feature pages."""
    role = (user.get("role") or "member").lower()
    if role == "admin":
        return NAV + [("Team", "users")]
    feats = set(user.get("features") or [])
    return [(n, ic) for n, ic in NAV if n in feats]


def render_sidebar(active: str, token: str, user: dict):
    logo = f'<img class="logo-3d" src="data:image/jpeg;base64,{_LOGO}" />' if _LOGO else "🟣"
    email = user.get("email", "")
    initials = (email[:1] or "U").upper()
    role = user.get("role", "viewer").lower()
    pill = '<span class="yq-upill">ADMIN</span>' if role == "admin" else f'<span class="yq-upill" style="background:rgba(124,58,237,0.25);color:#c4b5fd">{role.upper()}</span>'
    items = _nav_for(user)
    nav = "".join(
        f'<a class="{"active" if n == active else ""}" href="?t={token}&page={n.replace(" ", "%20")}" target="_self">{icon(ic,19)}<span>{n}</span></a>'
        for n, ic in items)
    st.sidebar.markdown(f"""
    <div class="yq-side">
      <div class="yq-logo">{logo}</div>
      <div class="yq-brand"><div class="yq-brand-name">YQ Bahrain</div><div class="yq-brand-sub">Mobile Accessories · AI Ops</div></div>
      <div class="yq-sec">Portal</div>
      <div class="yq-nav">{nav}</div>
      <div class="yq-bottom">
        <div class="yq-user"><div class="yq-ava">{initials}</div><div><div class="yq-uname">{email.split('@')[0].replace('.', ' ').title() or 'User'}</div>{pill}</div></div>
        <a class="yq-signout" href="?signout=1" target="_self">{icon('logout',17)} Sign Out</a>
        <div class="yq-foot">v0.4.0 · {datetime.now().strftime('%d %b %Y')}</div>
      </div>
    </div>
    """, unsafe_allow_html=True)


def page_header(title: str, subtitle: str, ic: str = "grid", quote: bool = False):
    q = _quotes_html(big=False) if quote else ""
    st.markdown(f"""
    <div class="page-header"><div class="ph-ic">{icon(ic,24)}</div>
      <div style="flex:1;"><div class="ph-title">{title}</div><div class="ph-sub">{subtitle}</div>{q}</div>
      <div class="ph-badge"><div class="dot"></div> Live</div></div>
    """, unsafe_allow_html=True)


def html_table(rows: list[dict], cols: list[tuple[str, str]], money: set[str] | None = None):
    money = money or set()
    if not rows:
        st.markdown('<div style="color:var(--text-soft);padding:18px;text-align:center;">No data.</div>', unsafe_allow_html=True)
        return
    head = "".join(f"<th>{label}</th>" for _k, label in cols)
    body = ""
    for r in rows:
        tds = ""
        for k, _label in cols:
            v = r.get(k)
            if v is None:
                v = "—"
            elif k in money:
                try:
                    v = f"BHD {float(v):,.2f}"
                except (TypeError, ValueError):
                    pass
            tds += f"<td>{v}</td>"
        body += f"<tr>{tds}</tr>"
    st.markdown(f'<table class="styled-table"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>', unsafe_allow_html=True)


def render_kpis(kpi: dict):
    rev, prev = kpi["rev"], kpi["prev_rev"]
    d = ((rev - prev) / prev * 100) if prev > 0 else 0
    delta = f'<div class="kpi-delta up">▲ {d:+.1f}% vs last month</div>' if d >= 0 else f'<div class="kpi-delta down">▼ {d:.1f}% vs last month</div>'
    low, recv = kpi["low_stock"], kpi["receivables"]
    st.markdown(f"""
    <div class="kpi-wrap">
      <div class="kpi-card"><div class="kpi-ic">{icon('dollar',22)}</div><div class="kpi-value">BHD {rev:,.0f}</div><div class="kpi-label">Revenue This Month</div>{delta}</div>
      <div class="kpi-card"><div class="kpi-ic">{icon('file',22)}</div><div class="kpi-value">{kpi['orders']:,}</div><div class="kpi-label">Orders This Month</div><div class="kpi-delta up">Invoices processed</div></div>
      <div class="kpi-card"><div class="kpi-ic">{icon('alert',22)}</div><div class="kpi-value">{low}</div><div class="kpi-label">Low-Stock Items</div><div class="kpi-delta {'warn' if low>0 else 'up'}">{'Needs attention' if low>0 else 'Healthy'}</div></div>
      <div class="kpi-card"><div class="kpi-ic">{icon('bank',22)}</div><div class="kpi-value">BHD {recv:,.0f}</div><div class="kpi-label">Outstanding Receivables</div><div class="kpi-delta warn">Total debtor balance</div></div>
    </div>""", unsafe_allow_html=True)


def render_monthly_chart(rows: list[dict]):
    if not rows:
        st.info("No monthly data yet.")
        return
    df = pd.DataFrame(rows)
    df["period_month"] = pd.to_datetime(df["period_month"])
    df["net_revenue_bhd"] = pd.to_numeric(df["net_revenue_bhd"], errors="coerce").fillna(0)
    fig = go.Figure(go.Scatter(x=df["period_month"], y=df["net_revenue_bhd"], mode="lines+markers",
        line=dict(color="#7c3aed", width=3, shape="spline"), marker=dict(size=8, color="#6d28d9", line=dict(color="#fff", width=2)),
        fill="tozeroy", fillcolor="rgba(124,58,237,0.10)", hovertemplate="<b>%{x|%b %Y}</b><br>BHD %{y:,.2f}<extra></extra>"))
    fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(family="Inter", color="#6b7280", size=11),
        margin=dict(l=8, r=8, t=8, b=8), height=240, showlegend=False, hovermode="x unified",
        xaxis=dict(showgrid=False, zeroline=False, tickformat="%b %y", tickfont=dict(size=10, color="#9aa1ad")),
        yaxis=dict(showgrid=True, gridcolor="rgba(124,58,237,0.08)", zeroline=False, tickprefix="BHD ", tickfont=dict(size=10, color="#9aa1ad")))
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


# ── Model selector (light tiles, wired to ai_ask) ────────────────────────────

_MODELS = [
    {"id": "pro", "name": "Llama 3.3 70B", "badge": "pro", "icon": "sparkles", "desc": "Maximum reasoning for complex questions & database queries."},
    {"id": "thinking", "name": "DeepSeek / Qwen", "badge": "thinking", "icon": "cpu", "desc": "Deep multi-step analytical logic for tricky asks."},
    {"id": "fast", "name": "Llama 3.1 8B", "badge": "fast", "icon": "zap", "desc": "Sub-second answers for quick lookups & simple metrics."},
]


def render_model_selector(token: str, current: str):
    tiles = ""
    for m in _MODELS:
        sel = "sel" if m["id"] == current else ""
        href = f"?t={token}&page=AI%20Assistant&model={m['id']}"
        tiles += f"""
        <a class="model-tile {sel}" href="{href}" target="_self">
          <div class="model-chk">{icon('check',13,stroke='#fff')}</div>
          <div class="model-top"><div class="model-ic">{icon(m['icon'],18)}</div><span class="model-bdg {m['badge']}">{m['badge']}</span></div>
          <div class="model-name">{m['name']}</div>
          <div class="model-desc">{m['desc']}</div>
        </a>"""
    st.markdown(f'<div class="model-wrap">{tiles}</div>', unsafe_allow_html=True)


# ── Pages ────────────────────────────────────────────────────────────────────

def page_dashboard(state: dict):
    page_header("AI Operations Center", datetime.now().strftime('%A, %d %B %Y · %H:%M') + " — Mobile Accessories Intelligence", "grid", quote=True)
    render_kpis(_kpi_data())
    with st.expander("Daily Operations Digest", expanded=True):
        dig = _digest_data()
        if not dig.get("ok"):
            st.warning(f"Digest unavailable: {dig.get('error', '')}")
        else:
            rev_mtd, rev_prev = dig.get("rev_mtd", 0), dig.get("rev_prev_month", 0)
            d = ((rev_mtd - rev_prev) / rev_prev * 100) if rev_prev else 0
            c = st.columns(5)
            c[0].metric("Today Revenue", f"BHD {dig.get('rev_today',0):,.2f}", f"{dig.get('orders_today',0)} invoices")
            c[1].metric("MTD Revenue", f"BHD {rev_mtd:,.2f}", f"{d:+.1f}% vs last month")
            c[2].metric("MTD Orders", str(dig.get("orders_mtd", 0)))
            c[3].metric("Low Stock", str(dig.get("low_stock_count", 0)))
            c[4].metric("Overdue Accts", str(dig.get("overdue_count", 0)), f"BHD {dig.get('overdue_total_bhd',0):,.0f}")
            if dig.get("overdue_count", 0) > 0:
                st.markdown(f'<div style="background:rgba(190,18,60,0.07);border:1px solid rgba(190,18,60,0.22);border-radius:10px;padding:10px 16px;font-size:.83rem;color:#9f1239;margin-top:8px;"><b>{dig["overdue_count"]} accounts</b> overdue 30+ days — total BHD {dig.get("overdue_total_bhd",0):,.2f}.</div>', unsafe_allow_html=True)
            if dig.get("low_stock_count", 0) > 0:
                st.markdown(f'<div style="background:rgba(180,83,9,0.08);border:1px solid rgba(180,83,9,0.22);border-radius:10px;padding:10px 16px;font-size:.83rem;color:#92400e;margin-top:6px;"><b>{dig["low_stock_count"]} items</b> below minimum stock level.</div>', unsafe_allow_html=True)
    c1, c2 = st.columns([1.6, 1])
    with c1:
        st.markdown(f'<div class="section-header">{icon("trending",18)} Monthly Revenue (BHD)</div>', unsafe_allow_html=True)
        st.markdown('<div class="glass">', unsafe_allow_html=True)
        render_monthly_chart(_monthly_trend())
        st.markdown('</div>', unsafe_allow_html=True)
    with c2:
        st.markdown(f'<div class="section-header">{icon("award",18)} Top Customers</div>', unsafe_allow_html=True)
        st.markdown('<div class="glass">', unsafe_allow_html=True)
        html_table(_top_customers(), [("customer_name", "Customer"), ("total_revenue_bhd", "Revenue"), ("order_count", "Orders")], money={"total_revenue_bhd"})
        st.markdown('</div>', unsafe_allow_html=True)


_AGENT_STYLE = {
    "collections": ("Collections", "card", "#dc2626"), "inventory": ("Inventory & Reorder", "box", "#d97706"),
    "margin": ("Margin Guardian", "percent", "#7c3aed"), "sales_insights": ("Sales Insights", "trending", "#0f7a52"),
    "sales_push": ("Sales Push", "zap", "#2563eb"), "customer_health": ("Customer Health", "users", "#db2777"),
    "cashflow": ("Cash-flow Forecast", "wallet", "#0891b2"), "anomaly": ("Anomaly Watch", "alert", "#ca8a04"),
}
_RESULT_LIST_KEYS = ["items", "negative_margins", "top_sellers", "cross_sell_opportunities", "at_risk", "top_debtors", "priced_below_cost", "trend"]


def page_agents(state: dict):
    from app.agents import AGENTS, run_agent
    page_header("AI Agents", "Your automated team — run any agent for an instant briefing", "cpu")
    st.markdown('<div style="font-size:.86rem;color:var(--text-muted);margin:-8px 0 18px;">These agents also run automatically on a schedule (n8n). Run any one on demand here.</div>', unsafe_allow_html=True)
    results = state["agent_results"]
    cols = st.columns(2)
    for i, name in enumerate(AGENTS.keys()):
        title, ic, color = _AGENT_STYLE.get(name, (name.title(), "cpu", "#6d28d9"))
        with cols[i % 2]:
            st.markdown(f'<div class="agent-card"><div class="agent-ic" style="background:linear-gradient(135deg,{color},{color}cc);">{icon(ic,22)}</div><div class="agent-name">{title}</div><div class="agent-desc">{AGENTS[name][1]}</div></div>', unsafe_allow_html=True)
            if st.button(f"Run {title}", key=f"run_{name}", use_container_width=True):
                with st.spinner(f"{title} is analysing…"):
                    try:
                        results[name] = run_agent(name)
                    except Exception as e:
                        results[name] = {"error": str(e)}
            res = results.get(name)
            if res:
                if res.get("error"):
                    st.error(res["error"])
                else:
                    st.markdown(f'<div class="agent-summary">{res.get("summary","Done.")}</div>', unsafe_allow_html=True)
                    lk = next((k for k in _RESULT_LIST_KEYS if res.get(k)), None)
                    if lk:
                        with st.expander(f"View details ({lk.replace('_',' ')})"):
                            st.dataframe(pd.DataFrame(res[lk]), use_container_width=True, hide_index=True)


QUICK = ["Sales this month", "Low stock alert", "Top customers", "Product margins", "Receivables", "Monthly trend"]


def _new_chat(state: dict) -> str:
    cid = uuid.uuid4().hex[:8]
    state["chats"][cid] = {"title": "New chat", "messages": []}
    state["active_chat"] = cid
    return cid


def _ensure_chats(state: dict):
    if not state.get("chats"):
        state["chats"] = {}
        _new_chat(state)
    if state.get("active_chat") not in state["chats"]:
        state["active_chat"] = next(iter(state["chats"]))


def _esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _send_chat(state: dict, chat: dict, question: str, model_name: str | None = None):
    chat["messages"].append({"role": "user", "content": question})
    if chat["title"] == "New chat":
        chat["title"] = question[:40]
    with st.spinner("Thinking…"):
        start = datetime.now()
        result = ai_ask(question, user_email=state["user"].get("email", "system"), model_name=model_name)
        ms = int((datetime.now() - start).total_seconds() * 1000)
    chat["messages"].append({"role": "assistant", "content": result["reply"], "sql": result.get("sql_used", ""),
        "cached": result.get("cached", False), "rows": result.get("row_count", 0), "ms": ms, "ts": datetime.now().strftime("%H:%M")})


def page_ai_assistant(state: dict):
    _ensure_chats(state)
    token = st.query_params.get("t", "")
    model = st.query_params.get("model", "pro")
    page_header("AI Assistant", "Ask anything — your data, answered in plain English", "message")
    render_model_selector(token, model)
    initials = (state["user"].get("email", "U")[:1] or "U").upper()
    rail, main_col = st.columns([1, 3.4])
    with rail:
        st.markdown('<div class="glass" style="padding:14px;">', unsafe_allow_html=True)
        if st.button("＋  New chat", use_container_width=True):
            _new_chat(state)
            st.rerun()
        st.markdown('<div class="chat-hist-h">Recent</div>', unsafe_allow_html=True)
        for cid, c in reversed(list(state["chats"].items())):
            label = (c["title"][:22] + ("…" if len(c["title"]) > 22 else "")) or "New chat"
            if st.button(label, key=f"chat_{cid}", use_container_width=True):
                state["active_chat"] = cid
                st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)
    with main_col:
        chat = state["chats"][state["active_chat"]]
        st.markdown('<div class="glass" style="padding:18px 20px;">', unsafe_allow_html=True)
        if chat["messages"]:
            parts = []
            for m in chat["messages"]:
                if m["role"] == "user":
                    parts.append(f'<div class="cm cm-user"><div class="cm-av cm-av-user">{initials}</div><div class="cm-bub cm-bub-user">{_esc(m["content"])}</div></div>')
                else:
                    cached = ' · cached' if m.get("cached") else ""
                    sqld = f'<details style="margin-top:9px;"><summary style="font-size:.72rem;color:var(--purple-600);cursor:pointer;font-weight:600;">View SQL</summary><div style="background:#1c0b3f;color:#d6bcfa;border-radius:8px;padding:10px;font-size:.74rem;font-family:monospace;margin-top:6px;white-space:pre-wrap;">{_esc(m["sql"])}</div></details>' if m.get("sql") else ""
                    body = _esc(m["content"]).replace(chr(10), "<br>")
                    parts.append(f'<div class="cm cm-ai"><div class="cm-av cm-av-ai">{icon("sparkles",16)}</div><div class="cm-bub cm-bub-ai"><div class="ai-label">{icon("sparkles",12)} YQ AI{cached}</div>{body}{sqld}<div class="cm-meta">{m.get("rows","")} rows · {m.get("ms",0)} ms · {m.get("ts","")}</div></div></div>')
            st.markdown(f'<div class="chat2">{"".join(parts)}</div>', unsafe_allow_html=True)
        else:
            st.markdown(f'<div class="chat2-empty">{icon("sparkles",44,"#c9bdf0")}<div style="font-weight:700;color:var(--purple-600);margin-top:12px;font-size:1.05rem;">How can I help?</div><div style="font-size:.86rem;margin-top:6px;max-width:440px;">Ask about sales, stock, margins, receivables, or any product — in plain English.</div></div>', unsafe_allow_html=True)
            sc = st.columns(3)
            for i, q in enumerate(QUICK[:6]):
                if sc[i % 3].button(q, key=f"sg_{i}", use_container_width=True):
                    _send_chat(state, chat, q, model)
                    st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)
    prompt = st.chat_input("Ask about sales, stock, margins, receivables…  (Enter to send)")
    if prompt:
        _send_chat(state, state["chats"][state["active_chat"]], prompt, model)
        st.rerun()


def page_inventory(state: dict):
    page_header("Inventory", "Current stock positions across warehouses", "box")
    rows = _query("SELECT item_name, warehouse_name, balance_qty, balance_value_bhd, category_name, as_of_date FROM v_current_stock ORDER BY balance_qty ASC LIMIT 300")
    low = [r for r in rows if (r.get("balance_qty") or 0) <= 10]
    c = st.columns(3)
    c[0].metric("Stock lines", f"{len(rows):,}")
    c[1].metric("Low-stock items", f"{len(low):,}")
    c[2].metric("Stock value", f"BHD {sum(float(r.get('balance_value_bhd') or 0) for r in rows):,.0f}")
    st.markdown(f'<div class="section-header">{icon("alert",18)} Low Stock (≤ 10 units)</div>', unsafe_allow_html=True)
    st.markdown('<div class="glass">', unsafe_allow_html=True)
    html_table(low[:60], [("item_name", "Item"), ("warehouse_name", "Salesman/WH"), ("balance_qty", "Qty"), ("category_name", "Category")])
    st.markdown('</div>', unsafe_allow_html=True)


def page_sales(state: dict):
    page_header("Sales", "Revenue trend and recent invoiced lines", "trending")
    st.markdown('<div class="glass">', unsafe_allow_html=True)
    render_monthly_chart(_monthly_trend())
    st.markdown('</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="section-header">{icon("file",18)} Recent Sales</div>', unsafe_allow_html=True)
    rows = _query("SELECT sale_date, invoice_no, customer_name, item_name, quantity, total_amount_bhd, salesman_resolved FROM v_sales ORDER BY sale_date DESC NULLS LAST LIMIT 80")
    st.markdown('<div class="glass">', unsafe_allow_html=True)
    html_table(rows, [("sale_date", "Date"), ("invoice_no", "Invoice"), ("customer_name", "Customer"), ("item_name", "Item"), ("quantity", "Qty"), ("total_amount_bhd", "Amount"), ("salesman_resolved", "Salesman")], money={"total_amount_bhd"})
    st.markdown('</div>', unsafe_allow_html=True)


def page_margins(state: dict):
    page_header("Margins", "Product profitability (Focus COGS basis)", "percent")
    rows = _query("SELECT item_name, category_name, gp_margin_pct, np_margin_pct, cogs_bhd, list_price_bhd FROM v_product_margin ORDER BY gp_margin_pct ASC NULLS LAST LIMIT 200")
    neg = [r for r in rows if (r.get("gp_margin_pct") or 0) < 0]
    c = st.columns(3)
    c[0].metric("Products", f"{len(rows):,}")
    c[1].metric("Negative margin", f"{len(neg):,}")
    vals = [float(r.get("gp_margin_pct") or 0) for r in rows if r.get("gp_margin_pct") is not None]
    c[2].metric("Avg GP %", f"{(sum(vals)/len(vals)):.1f}%" if vals else "—")
    st.markdown(f'<div class="section-header">{icon("alert",18)} Lowest Margins</div>', unsafe_allow_html=True)
    st.markdown('<div class="glass">', unsafe_allow_html=True)
    html_table(rows[:60], [("item_name", "Item"), ("category_name", "Category"), ("gp_margin_pct", "GP %"), ("np_margin_pct", "NP %"), ("cogs_bhd", "COGS"), ("list_price_bhd", "List")], money={"cogs_bhd", "list_price_bhd"})
    st.markdown('</div>', unsafe_allow_html=True)


def page_receivables(state: dict):
    page_header("Receivables", "Outstanding debtor balances and ageing", "card")
    rows = _query("SELECT account, outstanding_bhd, days_outstanding, salesman, last_entry_date FROM v_receivables ORDER BY outstanding_bhd DESC LIMIT 200")
    total = sum(float(r.get("outstanding_bhd") or 0) for r in rows)
    overdue = [r for r in rows if (r.get("days_outstanding") or 0) >= 30]
    c = st.columns(3)
    c[0].metric("Total receivable", f"BHD {total:,.0f}")
    c[1].metric("Accounts", f"{len(rows):,}")
    c[2].metric("Overdue 30+ days", f"{len(overdue):,}")
    st.markdown(f'<div class="section-header">{icon("bank",18)} Debtor Balances</div>', unsafe_allow_html=True)
    st.markdown('<div class="glass">', unsafe_allow_html=True)
    html_table(rows[:80], [("account", "Account"), ("outstanding_bhd", "Outstanding"), ("days_outstanding", "Days"), ("salesman", "Salesman")], money={"outstanding_bhd"})
    st.markdown('</div>', unsafe_allow_html=True)


# ── Team & Access (admin) ─────────────────────────────────────────────────────

def _feature_checkboxes(prefix: str, selected: set[str]) -> list[str]:
    cols = st.columns(4)
    picked: list[str] = []
    for i, f in enumerate(user_auth.FEATURES):
        if cols[i % 4].checkbox(f, value=(f in selected), key=f"{prefix}_{f}"):
            picked.append(f)
    return picked


def page_team(state: dict):
    user = state["user"]
    if (user.get("role") or "").lower() != "admin":
        page_header("Team", "Access restricted", "users")
        st.warning("Only admins can manage the team.")
        return
    page_header("Team & Access", "Invite teammates and control what each person can see", "users")

    # one-time flash (temp password / invite link) survives the rerun
    flash = st.session_state.pop("team_flash", None)
    if flash:
        getattr(st, flash["kind"])(flash["msg"])

    # ── Invite panel ─────────────────────────────────────────────────────────
    with st.expander("➕  Invite a team member", expanded=False):
        c = st.columns(2)
        full_name = c[0].text_input("Full name", key="inv_name")
        email = c[1].text_input("Email", key="inv_email")
        role = st.radio("Role", ["member", "admin"], horizontal=True, key="inv_role",
                        help="Admins get every page + Team management. Members see only granted pages.")
        feats: list[str] = []
        if role == "member":
            st.caption("Feature access")
            feats = _feature_checkboxes("inv", {"Dashboard"})
        method = st.radio(
            "How do they get access?",
            ["Temp password (works now)", "Email invite link (needs verified domain)"],
            key="inv_method")
        if st.button("Create / Invite", type="primary", key="inv_submit"):
            em = (email or "").strip().lower()
            if not em or "@" not in em:
                st.error("Enter a valid email address.")
            elif role == "member" and not feats:
                st.error("Grant at least one feature, or make them an admin.")
            else:
                grant = feats if role == "member" else list(user_auth.FEATURES)
                try:
                    if method.startswith("Temp"):
                        tmp = user_auth.generate_temp_password()
                        user_auth.create_member(em, full_name, role, grant, tmp,
                                                invited_by=user["email"], must_reset=True)
                        st.session_state["team_flash"] = {
                            "kind": "success",
                            "msg": f"✅ Account created for **{em}**. Share this temporary "
                                   f"password securely — they'll set their own on first login:\n\n"
                                   f"### `{tmp}`",
                        }
                    else:
                        res = user_auth.create_email_invite(em, full_name, role, grant,
                                                            invited_by=user["email"])
                        emstat = res.get("email", {})
                        if emstat.get("emailed"):
                            st.session_state["team_flash"] = {
                                "kind": "success", "msg": f"✅ Invite emailed to **{em}**."}
                        else:
                            st.session_state["team_flash"] = {
                                "kind": "warning",
                                "msg": f"Invite created, but email could not be sent "
                                       f"({emstat.get('reason','')}). Share this link:\n\n{res.get('link')}"}
                    st.rerun()
                except Exception as e:
                    st.error(f"Could not create the account: {e}")

    # ── Members list ─────────────────────────────────────────────────────────
    data = user_auth.list_members()
    st.markdown(f'<div class="section-header">{icon("users",18)} Team members ({len(data["users"])})</div>',
                unsafe_allow_html=True)
    for u in data["users"]:
        em = u["email"]
        rl = (u.get("role") or "member").lower()
        status = u.get("status", "active")
        feats_now = set(u.get("features") or [])
        is_self = em == user["email"]
        label = f"{u.get('full_name') or em.split('@')[0].title()}  ·  {rl.upper()}  ·  {status}"
        with st.expander(label):
            st.caption(em + ("   (you)" if is_self else ""))
            new_role = st.radio("Role", ["member", "admin"], index=(1 if rl == "admin" else 0),
                                horizontal=True, key=f"r_{em}", disabled=is_self)
            new_feats = list(feats_now)
            if new_role == "member":
                new_feats = _feature_checkboxes(f"f_{em}", feats_now or set())
            else:
                st.caption("Admins have access to every page.")
            new_status = st.selectbox("Status", ["active", "disabled"],
                                      index=(0 if status == "active" else 1),
                                      key=f"s_{em}", disabled=is_self)
            b = st.columns([1, 1, 4])
            if b[0].button("Save", key=f"save_{em}"):
                grant = new_feats if new_role == "member" else list(user_auth.FEATURES)
                user_auth.update_access(em, role=new_role, features=grant, status=new_status)
                st.session_state["team_flash"] = {"kind": "success", "msg": f"Updated {em}."}
                st.rerun()
            if not is_self and b[1].button("Remove", key=f"rm_{em}"):
                user_auth.remove_user(em)
                st.session_state["team_flash"] = {"kind": "success", "msg": f"Removed {em}."}
                st.rerun()

    # ── Pending invites ──────────────────────────────────────────────────────
    if data.get("invites"):
        st.markdown(f'<div class="section-header">{icon("message",18)} Pending invites ({len(data["invites"])})</div>',
                    unsafe_allow_html=True)
        for inv in data["invites"]:
            cc = st.columns([3, 2, 1])
            cc[0].write(inv.get("email"))
            cc[1].write((inv.get("role") or "member").title())
            if cc[2].button("Revoke", key=f"rev_{inv.get('token')}"):
                user_auth.revoke_invite(inv.get("token"))
                st.rerun()


PAGES = {"Dashboard": page_dashboard, "AI Agents": page_agents, "AI Assistant": page_ai_assistant,
         "Inventory": page_inventory, "Sales": page_sales, "Margins": page_margins,
         "Receivables": page_receivables, "Team": page_team}


def main():
    store = _store()
    qp = st.query_params

    # 1 — accept an email invite (?invite=<token>) before any auth
    invite_token = qp.get("invite")
    if invite_token:
        inv = user_auth.get_invite(invite_token)
        if inv:
            set_password_page(invite=inv)
            return
        st.session_state.login_error = "That invite link is invalid or has expired."
        st.query_params.clear()

    token = qp.get("t")
    if qp.get("signout"):
        if token:
            store.pop(token, None)
        st.query_params.clear()
        st.rerun()

    state = store.get(token) if token else None
    if not state:
        login_page()
        return

    user = state["user"]

    # 2 — forced first-login password reset (temp-password accounts)
    if user.get("must_reset"):
        set_password_page(reset_user=user)
        return

    st.markdown(CSS, unsafe_allow_html=True)

    # 3 — resolve which pages this user may open
    allowed = {n for n, _ic in _nav_for(user)}
    page = qp.get("page", "Dashboard")
    if page not in allowed:
        page = "Dashboard" if "Dashboard" in allowed else (next(iter(allowed), "Dashboard"))

    render_sidebar(page, token, user)
    if page in allowed and page in PAGES:
        PAGES[page](state)
    else:
        page_header("No access yet", "You haven't been granted any pages. Please ask an admin.", "alert")


main()
