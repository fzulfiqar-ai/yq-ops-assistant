"""YQ Bahrain AI Operations Dashboard — Phase 1.

Premium corporate UI: purple brand, glassmorphism, animated particles, 3D cards.
Run: streamlit run dashboard/ui.py
"""
from __future__ import annotations

import base64
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# ── Must be first Streamlit call ─────────────────────────────────────────────
st.set_page_config(
    page_title="YQ Bahrain | AI Operations",
    page_icon="🟣",
    layout="wide",
    initial_sidebar_state="expanded",
)

from app.ai import ask as ai_ask, exec_sql  # noqa: E402
from app.config import settings             # noqa: E402
from app.database import get_client        # noqa: E402

# ── Logo ─────────────────────────────────────────────────────────────────────
LOGO_PATH = ROOT / "YQ LOGO" / "LOGO.jpeg"


def _logo_b64() -> str:
    if LOGO_PATH.exists():
        return base64.b64encode(LOGO_PATH.read_bytes()).decode()
    return ""


_LOGO = _logo_b64()

# ── CSS — full premium design ─────────────────────────────────────────────────
CSS = """
<style>
/* ── Fonts ── */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap');

/* ── Root variables ── */
:root {
    --purple-900: #1e0a35;
    --purple-800: #2d1356;
    --purple-700: #4a1d8a;
    --purple-600: #6d28d9;
    --purple-500: #7c3aed;
    --purple-400: #8b5cf6;
    --purple-300: #a78bfa;
    --purple-200: #c4b5fd;
    --purple-100: #ede9fe;
    --gold:        #f59e0b;
    --gold-light:  #fbbf24;
    --green:       #10b981;
    --red:         #ef4444;
    --bg:          #080514;
    --surface:     rgba(124,58,237,0.08);
    --surface2:    rgba(124,58,237,0.15);
    --border:      rgba(139,92,246,0.25);
    --border-bright: rgba(167,139,250,0.5);
    --text:        #f1f5f9;
    --text-muted:  #94a3b8;
    --radius:      16px;
    --radius-lg:   24px;
}

/* ── App base ── */
html, body, [class*="css"], .stApp {
    font-family: 'Inter', sans-serif !important;
    background: var(--bg) !important;
    color: var(--text) !important;
}

/* ── Animated gradient background ── */
.stApp {
    background: linear-gradient(135deg,
        #080514 0%, #120825 25%,
        #1a0d35 50%, #0f0620 75%,
        #080514 100%) !important;
    background-size: 400% 400% !important;
    animation: bgShift 20s ease infinite !important;
}
@keyframes bgShift {
    0%  { background-position: 0% 50%; }
    50% { background-position: 100% 50%; }
    100%{ background-position: 0% 50%; }
}

/* ── Sidebar ── */
[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #100724 0%, #0d0520 100%) !important;
    border-right: 1px solid var(--border) !important;
}
[data-testid="stSidebar"] * { color: var(--text) !important; }
[data-testid="stSidebarNav"] { display: none; }

/* ── Hide Streamlit chrome ── */
#MainMenu, footer, header, [data-testid="stToolbar"] { visibility: hidden; }
.block-container { padding: 1.5rem 2rem 2rem !important; max-width: 100% !important; }

/* ── KPI cards ── */
.kpi-wrap { display: grid; grid-template-columns: repeat(4,1fr); gap: 18px; margin: 18px 0 24px; }
.kpi-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    padding: 24px 20px;
    text-align: center;
    position: relative;
    overflow: hidden;
    backdrop-filter: blur(20px);
    -webkit-backdrop-filter: blur(20px);
    transition: transform 0.35s cubic-bezier(.4,0,.2,1),
                box-shadow 0.35s cubic-bezier(.4,0,.2,1),
                border-color 0.35s ease;
    animation: floatCard 6s ease-in-out infinite;
    cursor: default;
}
.kpi-card:hover {
    transform: translateY(-10px) scale(1.02);
    box-shadow: 0 24px 60px rgba(124,58,237,0.45),
                0 0 0 1px var(--border-bright);
    border-color: var(--border-bright);
}
.kpi-card::before {
    content: '';
    position: absolute;
    top: -50%; left: -50%;
    width: 200%; height: 200%;
    background: radial-gradient(circle at 60% 40%,
        rgba(124,58,237,0.12) 0%, transparent 60%);
    animation: shimmer 4s ease-in-out infinite;
    pointer-events: none;
}
@keyframes floatCard {
    0%,100% { transform: translateY(0); }
    50%      { transform: translateY(-6px); }
}
@keyframes shimmer {
    0%,100% { transform: rotate(0deg); }
    50%      { transform: rotate(180deg); }
}
.kpi-icon  { font-size: 2rem; margin-bottom: 8px; filter: drop-shadow(0 0 12px rgba(167,139,250,0.6)); }
.kpi-value {
    font-size: 1.9rem;
    font-weight: 800;
    background: linear-gradient(135deg, #c4b5fd 0%, #a78bfa 50%, #8b5cf6 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    line-height: 1.1;
    letter-spacing: -0.5px;
}
.kpi-label { font-size: .78rem; color: var(--text-muted); margin-top: 6px; font-weight: 500; letter-spacing: .5px; text-transform: uppercase; }
.kpi-delta { font-size: .82rem; margin-top: 8px; font-weight: 600; }
.kpi-delta.up   { color: var(--green); }
.kpi-delta.warn { color: var(--gold);  }
.kpi-delta.down { color: var(--red);   }
.kpi-card-revenue  { animation-delay: 0s;    }
.kpi-card-orders   { animation-delay: 1.5s;  }
.kpi-card-stock    { animation-delay: 3s;    }
.kpi-card-recv     { animation-delay: 4.5s;  }

/* ── Page header ── */
.page-header {
    display: flex; align-items: center; gap: 16px;
    padding: 20px 28px;
    background: linear-gradient(135deg,
        rgba(109,40,217,0.25) 0%, rgba(124,58,237,0.15) 50%, rgba(91,33,182,0.25) 100%);
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    margin-bottom: 24px;
    backdrop-filter: blur(20px);
    position: relative;
    overflow: hidden;
}
.page-header::after {
    content:'';
    position:absolute; top:0; left:-100%; width:60%; height:100%;
    background: linear-gradient(90deg, transparent, rgba(167,139,250,0.08), transparent);
    animation: headerGlow 4s ease-in-out infinite;
}
@keyframes headerGlow { 0%,100%{left:-100%} 50%{left:150%} }
.header-title {
    font-size: 1.55rem; font-weight: 800;
    background: linear-gradient(135deg, #e9d5ff 0%, #c4b5fd 40%, #a78bfa 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    letter-spacing: -.3px;
}
.header-subtitle { font-size: .82rem; color: var(--text-muted); margin-top: 2px; }
.header-badge {
    margin-left: auto;
    background: rgba(16,185,129,0.15);
    border: 1px solid rgba(16,185,129,0.3);
    color: #34d399;
    padding: 4px 12px;
    border-radius: 20px;
    font-size: .75rem;
    font-weight: 600;
    display: flex; align-items: center; gap: 5px;
}
.pulse-dot {
    width:8px; height:8px;
    background: #10b981;
    border-radius: 50%;
    animation: pulseDot 2s ease infinite;
}
@keyframes pulseDot {
    0%,100%{ transform:scale(1); opacity:1; }
    50%    { transform:scale(1.5); opacity:.6; }
}

/* ── Glass panel ── */
.glass {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    padding: 24px;
    backdrop-filter: blur(20px);
    -webkit-backdrop-filter: blur(20px);
}
.glass-title {
    font-size: 1rem; font-weight: 700;
    color: var(--purple-200);
    margin-bottom: 18px;
    display: flex; align-items: center; gap: 8px;
}

/* ── Chat ── */
.chat-container {
    background: rgba(8,5,20,0.6);
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    padding: 20px;
    min-height: 360px;
    max-height: 500px;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 16px;
    backdrop-filter: blur(16px);
    scroll-behavior: smooth;
}
.chat-container::-webkit-scrollbar { width: 4px; }
.chat-container::-webkit-scrollbar-track { background: transparent; }
.chat-container::-webkit-scrollbar-thumb { background: var(--purple-600); border-radius: 4px; }

.msg-user {
    align-self: flex-end;
    background: linear-gradient(135deg, var(--purple-600), var(--purple-500));
    color: #fff;
    padding: 12px 18px;
    border-radius: 18px 18px 4px 18px;
    max-width: 78%;
    font-size: .9rem;
    line-height: 1.5;
    box-shadow: 0 4px 20px rgba(124,58,237,0.4);
    animation: slideRight .3s ease;
}
@keyframes slideRight { from{opacity:0;transform:translateX(20px)} to{opacity:1;transform:translateX(0)} }

.msg-ai {
    align-self: flex-start;
    background: rgba(15,8,35,0.85);
    border: 1px solid var(--border);
    color: var(--text);
    padding: 16px 20px;
    border-radius: 18px 18px 18px 4px;
    max-width: 85%;
    font-size: .88rem;
    line-height: 1.65;
    box-shadow: 0 4px 24px rgba(0,0,0,0.3);
    animation: slideLeft .3s ease;
}
@keyframes slideLeft { from{opacity:0;transform:translateX(-20px)} to{opacity:1;transform:translateX(0)} }

.msg-meta {
    font-size: .7rem;
    color: var(--text-muted);
    margin-top: 6px;
    display: flex; align-items: center; gap: 6px;
}
.msg-cached { color: var(--gold); }
.ai-label {
    display:inline-flex; align-items:center; gap:6px;
    font-size:.72rem; font-weight:600;
    color: var(--purple-300);
    margin-bottom: 8px;
    letter-spacing: .3px;
}

/* ── Typing indicator ── */
.typing-indicator {
    display: flex; align-items: center; gap: 5px;
    padding: 14px 18px;
    background: rgba(15,8,35,0.7);
    border: 1px solid var(--border);
    border-radius: 18px 18px 18px 4px;
    width: fit-content;
    animation: fadeIn .3s ease;
}
@keyframes fadeIn { from{opacity:0} to{opacity:1} }
.typing-dot {
    width:8px; height:8px;
    background: var(--purple-400);
    border-radius: 50%;
    animation: typingBounce 1.4s ease-in-out infinite;
}
.typing-dot:nth-child(2){ animation-delay:.2s }
.typing-dot:nth-child(3){ animation-delay:.4s }
@keyframes typingBounce {
    0%,60%,100%{ transform:translateY(0) }
    30%         { transform:translateY(-8px) }
}

/* ── Sidebar items ── */
.sidebar-logo { text-align:center; padding:20px 0 10px; }
.sidebar-logo img { width:90px; border-radius:16px; box-shadow:0 0 30px rgba(124,58,237,0.5); }
.sidebar-brand { text-align:center; margin:10px 0 20px; }
.sidebar-brand-name { font-size:1.05rem; font-weight:800; color:#c4b5fd; }
.sidebar-brand-sub  { font-size:.72rem; color: var(--text-muted); margin-top:2px; }
.sidebar-divider { border:none; border-top:1px solid var(--border); margin:12px 0; }
.sidebar-section { font-size:.65rem; font-weight:700; color:var(--text-muted); letter-spacing:1px; text-transform:uppercase; margin:14px 0 6px; padding-left:4px; }
.nav-item {
    display:flex; align-items:center; gap:10px;
    padding:10px 14px;
    border-radius:10px;
    font-size:.88rem;
    font-weight:500;
    color:var(--text-muted);
    cursor:pointer;
    transition:all .2s;
    margin-bottom:4px;
}
.nav-item.active, .nav-item:hover { background:var(--surface2); color:var(--purple-200); }
.nav-item-icon { font-size:1.1rem; }
.user-card {
    background:var(--surface);
    border:1px solid var(--border);
    border-radius:12px;
    padding:12px 14px;
    display:flex; align-items:center; gap:10px;
    margin-top:auto;
}
.user-avatar {
    width:36px; height:36px;
    background:linear-gradient(135deg, var(--purple-600), var(--purple-400));
    border-radius:50%;
    display:flex; align-items:center; justify-content:center;
    font-size:1rem; font-weight:700; color:#fff;
    flex-shrink:0;
}
.user-info-name  { font-size:.82rem; font-weight:600; color:var(--text); }
.user-info-role  { font-size:.7rem; color:var(--purple-300); }

/* ── Quick actions ── */
.quick-chip {
    display:inline-flex; align-items:center; gap:6px;
    background:var(--surface);
    border:1px solid var(--border);
    border-radius:20px;
    padding:6px 14px;
    font-size:.78rem;
    color:var(--purple-200);
    cursor:pointer;
    transition:all .25s;
    margin:4px 4px 4px 0;
    font-weight:500;
}
.quick-chip:hover { background:var(--surface2); border-color:var(--border-bright); transform:translateY(-2px); }

/* ── Table ── */
.styled-table { width:100%; border-collapse:collapse; font-size:.83rem; }
.styled-table th {
    background:rgba(124,58,237,0.2);
    color:var(--purple-200);
    padding:10px 14px;
    font-weight:600;
    font-size:.72rem;
    letter-spacing:.5px;
    text-transform:uppercase;
    border-bottom:1px solid var(--border);
}
.styled-table td {
    padding:9px 14px;
    border-bottom:1px solid rgba(139,92,246,0.1);
    color:var(--text);
}
.styled-table tr:hover td { background:rgba(124,58,237,0.07); }
.badge {
    display:inline-block;
    padding:2px 9px;
    border-radius:12px;
    font-size:.7rem;
    font-weight:600;
}
.badge-purple { background:rgba(124,58,237,0.2); color:var(--purple-300); }
.badge-green  { background:rgba(16,185,129,0.2); color:#34d399; }
.badge-gold   { background:rgba(245,158,11,0.2); color:var(--gold-light); }

/* ── Section divider ── */
.section-header {
    font-size:1rem; font-weight:700;
    color:var(--purple-200);
    margin:28px 0 14px;
    display:flex; align-items:center; gap:10px;
}
.section-header::after {
    content:''; flex:1;
    height:1px;
    background:linear-gradient(90deg,var(--border),transparent);
}

/* ── SQL expander ── */
.sql-box {
    background:rgba(5,3,15,0.8);
    border:1px solid rgba(139,92,246,0.2);
    border-radius:10px;
    padding:14px 16px;
    font-family:'Courier New',monospace;
    font-size:.76rem;
    color:#c4b5fd;
    margin-top:10px;
    white-space:pre-wrap;
    word-break:break-word;
}

/* ── Inputs ── */
.stTextInput input, .stTextArea textarea {
    background:rgba(15,8,35,0.7) !important;
    border:1px solid var(--border) !important;
    border-radius:10px !important;
    color:var(--text) !important;
    font-family:'Inter',sans-serif !important;
}
.stTextInput input:focus, .stTextArea textarea:focus {
    border-color:var(--purple-400) !important;
    box-shadow:0 0 0 2px rgba(124,58,237,0.25) !important;
}
.stButton > button {
    background:linear-gradient(135deg, var(--purple-600), var(--purple-500)) !important;
    border:none !important;
    color:#fff !important;
    border-radius:10px !important;
    font-weight:600 !important;
    font-family:'Inter',sans-serif !important;
    transition:all .25s !important;
    box-shadow:0 4px 20px rgba(124,58,237,0.35) !important;
}
.stButton > button:hover {
    transform:translateY(-2px) !important;
    box-shadow:0 8px 28px rgba(124,58,237,0.5) !important;
}

/* ── Login ── */
.login-container {
    display:flex; justify-content:center; align-items:center;
    min-height:90vh;
}
.login-card {
    background:rgba(15,8,35,0.85);
    border:1px solid var(--border);
    border-radius:28px;
    padding:48px 44px;
    width:420px;
    backdrop-filter:blur(30px);
    box-shadow:0 40px 100px rgba(0,0,0,0.6), 0 0 60px rgba(124,58,237,0.15);
    animation:loginFadeIn .6s ease;
}
@keyframes loginFadeIn { from{opacity:0;transform:translateY(30px)} to{opacity:1;transform:translateY(0)} }
.login-logo { text-align:center; margin-bottom:28px; }
.login-logo img { width:100px; border-radius:20px; box-shadow:0 0 40px rgba(124,58,237,0.5); }
.login-title { text-align:center; font-size:1.5rem; font-weight:800; background:linear-gradient(135deg,#e9d5ff,#a78bfa); -webkit-background-clip:text; -webkit-text-fill-color:transparent; background-clip:text; margin-bottom:6px; }
.login-sub { text-align:center; font-size:.82rem; color:var(--text-muted); margin-bottom:32px; }
.login-error { background:rgba(239,68,68,0.1); border:1px solid rgba(239,68,68,0.3); color:#fca5a5; border-radius:10px; padding:10px 14px; font-size:.82rem; margin-bottom:16px; }

/* ── Metrics ── */
[data-testid="metric-container"] { display:none !important; }

/* ── Particles canvas ── */
#yq-particles { position:fixed; top:0; left:0; width:100%; height:100%; z-index:0; pointer-events:none; }
.stApp > * { position:relative; z-index:1; }

/* ── Plotly ── */
.js-plotly-plot .plotly, .js-plotly-plot .plot-container { background:transparent !important; }
</style>
"""

PARTICLES_JS = """
<canvas id="yq-particles"></canvas>
<script>
(function(){
  const c = document.getElementById('yq-particles');
  if(!c) return;
  const ctx = c.getContext('2d');
  function resize(){ c.width=window.innerWidth; c.height=window.innerHeight; }
  resize();
  window.addEventListener('resize', resize);
  const N = 60, pts = [];
  const colors = ['rgba(124,58,237,', 'rgba(139,92,246,', 'rgba(167,139,250,'];
  for(let i=0;i<N;i++) pts.push({
    x: Math.random()*c.width, y: Math.random()*c.height,
    r: Math.random()*2.5+.5,
    vx:(Math.random()-.5)*.4, vy:(Math.random()-.5)*.4,
    op:Math.random()*.6+.15,
    col:colors[Math.floor(Math.random()*colors.length)]
  });
  function draw(){
    ctx.clearRect(0,0,c.width,c.height);
    pts.forEach(p=>{
      p.x+=p.vx; p.y+=p.vy;
      if(p.x<0)p.x=c.width; if(p.x>c.width)p.x=0;
      if(p.y<0)p.y=c.height; if(p.y>c.height)p.y=0;
      ctx.beginPath(); ctx.arc(p.x,p.y,p.r,0,Math.PI*2);
      ctx.fillStyle=p.col+p.op+')'; ctx.fill();
    });
    for(let i=0;i<pts.length;i++) for(let j=i+1;j<pts.length;j++){
      const dx=pts[i].x-pts[j].x, dy=pts[i].y-pts[j].y;
      const d=Math.sqrt(dx*dx+dy*dy);
      if(d<130){ ctx.beginPath(); ctx.moveTo(pts[i].x,pts[i].y);
        ctx.lineTo(pts[j].x,pts[j].y);
        ctx.strokeStyle='rgba(124,58,237,'+(0.12*(1-d/130))+')';
        ctx.lineWidth=.6; ctx.stroke(); }
    }
    requestAnimationFrame(draw);
  }
  draw();
})();
</script>
"""


# ── Data helpers ─────────────────────────────────────────────────────────────

@st.cache_data(ttl=300, show_spinner=False)
def _kpi_data() -> dict:
    try:
        rev = exec_sql(
            "SELECT COALESCE(SUM(total_amount_bhd),0) AS rev, "
            "COUNT(DISTINCT invoice_no) AS orders FROM v_sales "
            "WHERE DATE_TRUNC('month',sale_date)=DATE_TRUNC('month',CURRENT_DATE) LIMIT 1"
        )
        prev = exec_sql(
            "SELECT COALESCE(SUM(total_amount_bhd),0) AS rev FROM v_sales "
            "WHERE DATE_TRUNC('month',sale_date)=DATE_TRUNC('month',CURRENT_DATE-INTERVAL '1 month') LIMIT 1"
        )
        low = exec_sql("SELECT COUNT(*) AS n FROM v_low_stock LIMIT 1")
        rec = exec_sql("SELECT COALESCE(SUM(outstanding_bhd),0) AS total FROM v_receivables LIMIT 1")
        return {
            "rev": float(rev[0]["rev"]) if rev else 0,
            "orders": int(rev[0]["orders"]) if rev else 0,
            "prev_rev": float(prev[0]["rev"]) if prev else 0,
            "low_stock": int(low[0]["n"]) if low else 0,
            "receivables": float(rec[0]["total"]) if rec else 0,
        }
    except Exception:
        return {"rev": 0, "orders": 0, "prev_rev": 0, "low_stock": 0, "receivables": 0}


@st.cache_data(ttl=300, show_spinner=False)
def _monthly_trend() -> list[dict]:
    try:
        return exec_sql("SELECT period_month, net_revenue_bhd, order_count, total_qty FROM v_sales_by_period ORDER BY period_month LIMIT 24")
    except Exception:
        return []


@st.cache_data(ttl=300, show_spinner=False)
def _top_customers() -> list[dict]:
    try:
        return exec_sql("SELECT customer_name, total_revenue_bhd, order_count, last_order_date FROM v_top_customers LIMIT 8")
    except Exception:
        return []


@st.cache_data(ttl=300, show_spinner=False)
def _low_stock_items() -> list[dict]:
    try:
        return exec_sql("SELECT item_name, warehouse_name, balance_qty, as_of_date FROM v_low_stock ORDER BY balance_qty ASC LIMIT 10")
    except Exception:
        return []


_ADMIN_EMAILS = {"fzulfiqar@pie-int.com", "furqanahmed223@gmail.com"}


def _check_login(email: str, password: str) -> dict | None:
    """Verify password then look up role in user_roles (falls back to local list)."""
    secret = getattr(settings, "dashboard_secret", None) or "yq2024"
    em = email.strip().lower()
    if password != secret:
        return None
    # Try Supabase first
    try:
        client = get_client()
        r = client.table("user_roles").select("email,role").eq("email", em).limit(1).execute()
        if r.data:
            return r.data[0]
    except Exception:
        pass
    # Fallback: known admin list (works even if Supabase secrets not yet set)
    if em in _ADMIN_EMAILS:
        return {"email": em, "role": "admin"}
    return None


# ── Login page ───────────────────────────────────────────────────────────────

def login_page():
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown(PARTICLES_JS, unsafe_allow_html=True)

    logo_html = f'<img src="data:image/jpeg;base64,{_LOGO}" />' if _LOGO else "🟣"
    error_html = ""
    if st.session_state.get("login_error"):
        error_html = f'<div class="login-error">⚠️ {st.session_state.login_error}</div>'
        st.session_state.login_error = ""

    st.markdown(f"""
    <div class="login-container">
      <div class="login-card">
        <div class="login-logo">{logo_html}</div>
        <div class="login-title">YQ Bahrain Operations</div>
        <div class="login-sub">AI-powered internal management platform</div>
        {error_html}
      </div>
    </div>
    """, unsafe_allow_html=True)

    _, col, _ = st.columns([1, 1.4, 1])
    with col:
        email = st.text_input("Email address", placeholder="your@email.com", label_visibility="collapsed", key="li_email")
        pwd   = st.text_input("Password", type="password", placeholder="Password", label_visibility="collapsed", key="li_pwd")
        if st.button("Sign In →", use_container_width=True):
            if not email or not pwd:
                st.session_state.login_error = "Please enter your email and password."
                st.rerun()
            user = _check_login(email, pwd)
            if user:
                st.session_state.authenticated = True
                st.session_state.user = user
                st.session_state.chat_history = []
                st.rerun()
            else:
                st.session_state.login_error = "Invalid credentials or access not granted."
                st.rerun()
        st.markdown('<div style="text-align:center;font-size:.72rem;color:#475569;margin-top:14px;">Authorised access only · YQ Bahrain W.L.L</div>', unsafe_allow_html=True)


# ── Sidebar ──────────────────────────────────────────────────────────────────

def render_sidebar():
    user = st.session_state.get("user", {})
    logo_html = f'<img src="data:image/jpeg;base64,{_LOGO}" />' if _LOGO else "🟣"
    initials = (user.get("email", "U")[0]).upper()
    email    = user.get("email", "")
    role     = user.get("role", "viewer").title()

    st.sidebar.markdown(f"""
    <div class="sidebar-logo">{logo_html}</div>
    <div class="sidebar-brand">
      <div class="sidebar-brand-name">YQ Bahrain</div>
      <div class="sidebar-brand-sub">Mobile Accessories · AI Ops</div>
    </div>
    <hr class="sidebar-divider"/>
    <div class="sidebar-section">Navigation</div>
    <div class="nav-item active"><span class="nav-item-icon">📊</span> Dashboard</div>
    <div class="nav-item"><span class="nav-item-icon">💬</span> AI Assistant</div>
    <div class="nav-item"><span class="nav-item-icon">📦</span> Inventory</div>
    <div class="nav-item"><span class="nav-item-icon">💰</span> Sales</div>
    <div class="nav-item"><span class="nav-item-icon">📈</span> Margins</div>
    <div class="nav-item"><span class="nav-item-icon">🏦</span> Receivables</div>
    <hr class="sidebar-divider"/>
    <div class="user-card">
      <div class="user-avatar">{initials}</div>
      <div>
        <div class="user-info-name">{email.split('@')[0].replace('.',' ').title()}</div>
        <div class="user-info-role">{role}</div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    if st.sidebar.button("Sign Out", use_container_width=True):
        for k in ["authenticated", "user", "chat_history"]:
            st.session_state.pop(k, None)
        st.rerun()

    st.sidebar.markdown(f'<div style="text-align:center;font-size:.65rem;color:#334155;margin-top:12px;">v0.2.0 · Phase 1 · {datetime.now().strftime("%d %b %Y")}</div>', unsafe_allow_html=True)


# ── KPI row ──────────────────────────────────────────────────────────────────

def render_kpis(kpi: dict):
    rev, prev = kpi["rev"], kpi["prev_rev"]
    delta_pct  = ((rev - prev) / prev * 100) if prev > 0 else 0
    delta_html = (
        f'<div class="kpi-delta up">↑ {delta_pct:+.1f}% vs last month</div>' if delta_pct >= 0
        else f'<div class="kpi-delta down">↓ {delta_pct:.1f}% vs last month</div>'
    )
    low  = kpi["low_stock"]
    recv = kpi["receivables"]

    st.markdown(f"""
    <div class="kpi-wrap">
      <div class="kpi-card kpi-card-revenue">
        <div class="kpi-icon">💰</div>
        <div class="kpi-value">BHD {rev:,.0f}</div>
        <div class="kpi-label">Revenue This Month</div>
        {delta_html}
      </div>
      <div class="kpi-card kpi-card-orders">
        <div class="kpi-icon">🧾</div>
        <div class="kpi-value">{kpi['orders']:,}</div>
        <div class="kpi-label">Orders This Month</div>
        <div class="kpi-delta up">Invoices processed</div>
      </div>
      <div class="kpi-card kpi-card-stock">
        <div class="kpi-icon">⚠️</div>
        <div class="kpi-value">{low}</div>
        <div class="kpi-label">Low-Stock Items</div>
        <div class="kpi-delta {'warn' if low > 0 else 'up'}">{'Needs attention' if low > 0 else 'Stock healthy'}</div>
      </div>
      <div class="kpi-card kpi-card-recv">
        <div class="kpi-icon">🏦</div>
        <div class="kpi-value">BHD {recv:,.0f}</div>
        <div class="kpi-label">Outstanding Receivables</div>
        <div class="kpi-delta warn">Total debtor balance</div>
      </div>
    </div>
    """, unsafe_allow_html=True)


# ── Charts ───────────────────────────────────────────────────────────────────

def render_monthly_chart(rows: list[dict]):
    if not rows:
        st.info("No monthly data yet.")
        return
    df = pd.DataFrame(rows)
    df["period_month"] = pd.to_datetime(df["period_month"])
    df["net_revenue_bhd"] = pd.to_numeric(df["net_revenue_bhd"], errors="coerce").fillna(0)

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["period_month"],
        y=df["net_revenue_bhd"],
        mode="lines+markers",
        name="Revenue (BHD)",
        line=dict(color="#a78bfa", width=3, shape="spline"),
        marker=dict(size=8, color="#7c3aed",
                    line=dict(color="#c4b5fd", width=2),
                    symbol="circle"),
        fill="tozeroy",
        fillcolor="rgba(124,58,237,0.12)",
        hovertemplate="<b>%{x|%b %Y}</b><br>BHD %{y:,.2f}<extra></extra>",
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter", color="#94a3b8", size=11),
        margin=dict(l=8, r=8, t=8, b=8),
        height=220,
        xaxis=dict(
            showgrid=False, zeroline=False,
            tickformat="%b %y",
            tickfont=dict(size=10, color="#64748b"),
            linecolor="rgba(139,92,246,0.15)",
        ),
        yaxis=dict(
            showgrid=True,
            gridcolor="rgba(139,92,246,0.08)",
            zeroline=False,
            tickprefix="BHD ",
            tickfont=dict(size=10, color="#64748b"),
        ),
        hovermode="x unified",
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


# ── Chat ─────────────────────────────────────────────────────────────────────

QUICK_QUESTIONS = [
    "📊 Sales this month",
    "📦 Low stock alert",
    "🏆 Top customers",
    "📈 Product margins",
    "🏦 Receivables",
    "📅 Monthly trend",
]


def render_chat():
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []

    history = st.session_state.chat_history

    # Quick action chips
    chips_html = "".join(f'<span class="quick-chip">{q}</span>' for q in QUICK_QUESTIONS)
    st.markdown(f'<div style="margin-bottom:14px;">{chips_html}</div>', unsafe_allow_html=True)

    # Chat window
    if history:
        msgs_html = ""
        for m in history:
            if m["role"] == "user":
                msgs_html += f'<div class="msg-user">{m["content"]}</div>'
            else:
                cached_badge = '<span class="msg-cached">⚡ cached</span>' if m.get("cached") else ""
                sql_html = ""
                if m.get("sql"):
                    sql_html = f'<details style="margin-top:10px;"><summary style="font-size:.72rem;color:#7c3aed;cursor:pointer;font-weight:600;">🔍 View SQL</summary><div class="sql-box">{m["sql"]}</div></details>'
                msgs_html += f"""
                <div class="msg-ai">
                  <div class="ai-label">🤖 YQ AI &nbsp;·&nbsp; {m.get('ts','')}{cached_badge}</div>
                  {m["content"].replace(chr(10), '<br>')}
                  {sql_html}
                  <div class="msg-meta">{m.get('rows','')} rows · {m.get('ms',0)}ms</div>
                </div>"""
        st.markdown(f'<div class="chat-container">{msgs_html}</div>', unsafe_allow_html=True)
    else:
        st.markdown("""
        <div class="chat-container" style="justify-content:center;align-items:center;">
          <div style="text-align:center;color:#475569;">
            <div style="font-size:2.5rem;margin-bottom:12px;filter:drop-shadow(0 0 20px rgba(124,58,237,0.6));">🤖</div>
            <div style="font-size:1rem;font-weight:600;color:#7c3aed;margin-bottom:6px;">YQ AI Assistant</div>
            <div style="font-size:.82rem;">Ask me anything about your sales, stock, margins, or receivables.</div>
          </div>
        </div>
        """, unsafe_allow_html=True)

    # Input row
    col_inp, col_btn = st.columns([6, 1])
    with col_inp:
        question = st.text_input(
            "question",
            placeholder="Ask anything — e.g. 'Total sales this month' or 'Which products have low stock?'",
            label_visibility="collapsed",
            key="chat_input",
        )
    with col_btn:
        send = st.button("Send →", use_container_width=True)

    # Handle quick chips via button clicks (workaround)
    chip_cols = st.columns(len(QUICK_QUESTIONS))
    for i, q in enumerate(QUICK_QUESTIONS):
        with chip_cols[i]:
            if st.button(q, key=f"chip_{i}", use_container_width=True):
                question = q.split(" ", 1)[1]
                send = True

    if send and question:
        user_email = st.session_state.get("user", {}).get("email", "system")
        st.session_state.chat_history.append({"role": "user", "content": question})

        with st.spinner(""):
            start = datetime.now()
            result = ai_ask(question, user_email=user_email)
            ms = int((datetime.now() - start).total_seconds() * 1000)

        # Format markdown reply as simple HTML (basic conversion)
        reply_html = result["reply"].replace("**", "<b>").replace("**", "</b>")

        st.session_state.chat_history.append({
            "role": "assistant",
            "content": result["reply"],
            "sql": result.get("sql_used", ""),
            "cached": result.get("cached", False),
            "rows": result.get("row_count", 0),
            "ms": ms,
            "ts": datetime.now().strftime("%H:%M"),
        })
        st.rerun()


# ── Main dashboard ───────────────────────────────────────────────────────────

def main_dashboard():
    render_sidebar()
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown(PARTICLES_JS, unsafe_allow_html=True)

    # ── Header
    now = datetime.now()
    st.markdown(f"""
    <div class="page-header">
      <div>
        <div class="header-title">YQ Bahrain · AI Operations Center</div>
        <div class="header-subtitle">{now.strftime('%A, %d %B %Y · %H:%M')} — Mobile Accessories Intelligence Platform</div>
      </div>
      <div class="header-badge">
        <div class="pulse-dot"></div> Live Data
      </div>
    </div>
    """, unsafe_allow_html=True)

    # ── KPIs
    kpi = _kpi_data()
    render_kpis(kpi)

    # ── Charts + Top Customers
    col_chart, col_customers = st.columns([1.65, 1])

    with col_chart:
        st.markdown('<div class="glass">', unsafe_allow_html=True)
        st.markdown('<div class="glass-title">📈 Monthly Revenue (BHD)</div>', unsafe_allow_html=True)
        render_monthly_chart(_monthly_trend())
        st.markdown('</div>', unsafe_allow_html=True)

    with col_customers:
        st.markdown('<div class="glass" style="height:100%">', unsafe_allow_html=True)
        st.markdown('<div class="glass-title">🏆 Top Customers</div>', unsafe_allow_html=True)
        customers = _top_customers()
        if customers:
            rows_html = ""
            for i, c in enumerate(customers, 1):
                name = str(c.get("customer_name", ""))[:22]
                rev  = c.get("total_revenue_bhd") or 0
                ords = c.get("order_count", 0)
                rows_html += f"""<tr>
                  <td><span style="color:#7c3aed;font-weight:700;">#{i}</span></td>
                  <td style="font-weight:500;">{name}</td>
                  <td style="color:#a78bfa;font-weight:700;">BHD {float(rev):,.0f}</td>
                  <td><span class="badge badge-purple">{ords}</span></td>
                </tr>"""
            st.markdown(f"""
            <table class="styled-table">
              <thead><tr><th>#</th><th>Customer</th><th>Revenue</th><th>Orders</th></tr></thead>
              <tbody>{rows_html}</tbody>
            </table>
            """, unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

    # ── Low stock + AI Chat
    st.markdown('<div class="section-header">💬 AI Assistant</div>', unsafe_allow_html=True)
    col_chat, col_low = st.columns([1.7, 1])

    with col_chat:
        render_chat()

    with col_low:
        st.markdown('<div class="glass">', unsafe_allow_html=True)
        st.markdown('<div class="glass-title">⚠️ Low Stock Alert</div>', unsafe_allow_html=True)
        items = _low_stock_items()
        if items:
            rows_html = ""
            for it in items:
                name = str(it.get("item_name",""))[:30]
                qty  = it.get("balance_qty", 0) or 0
                color = "#ef4444" if float(qty) <= 3 else "#f59e0b"
                rows_html += f"""<tr>
                  <td style="font-size:.8rem;">{name}</td>
                  <td style="text-align:right;font-weight:700;color:{color};">{qty}</td>
                </tr>"""
            st.markdown(f"""
            <table class="styled-table">
              <thead><tr><th>Item</th><th style="text-align:right">Qty</th></tr></thead>
              <tbody>{rows_html}</tbody>
            </table>
            """, unsafe_allow_html=True)
        else:
            st.markdown('<div style="text-align:center;color:#10b981;padding:20px;font-size:.85rem;">✅ All stock levels healthy</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    if not st.session_state.get("authenticated"):
        login_page()
    else:
        main_dashboard()


if __name__ == "__main__":
    main()
else:
    main()
