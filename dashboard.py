"""
Trading Bot Dashboard — Streamlit V4.3 (responsive redesign)
"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from supabase import create_client
import os
from datetime import datetime, timedelta

# v4.2: streamlit-autorefresh
try:
    from streamlit_autorefresh import st_autorefresh
    _HAS_AUTOREFRESH = True
except ImportError:
    _HAS_AUTOREFRESH = False

st.set_page_config(
    page_title="Trading Bot V4",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ============================================================
# CUSTOM CSS — Dark trading dashboard theme
# ============================================================
st.markdown("""
<style>
    /* Import Inter font */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');
    @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500&display=swap');

    /* Global dark theme */
    .stApp {
        background-color: #0a0a0f;
        font-family: 'Inter', -apple-system, sans-serif;
    }
    
    /* Remove Streamlit default padding */
    .block-container {
        padding-top: 1.5rem !important;
        padding-bottom: 1rem !important;
        max-width: 1200px;
    }

    /* Headers */
    h1, h2, h3, h4 {
        font-family: 'Inter', sans-serif !important;
        color: #f0f0f0 !important;
        letter-spacing: -0.02em;
    }
    h1 { font-weight: 800 !important; font-size: 1.8rem !important; }
    h2 { font-weight: 700 !important; font-size: 1.3rem !important; }
    h3 { font-weight: 600 !important; font-size: 1.1rem !important; }

    /* Metric cards */
    [data-testid="stMetric"] {
        background: linear-gradient(135deg, #12121a 0%, #1a1a2e 100%);
        border: 1px solid #1e1e30;
        border-radius: 12px;
        padding: 1rem 1.2rem;
        box-shadow: 0 4px 20px rgba(0,0,0,0.3);
        transition: transform 0.2s, box-shadow 0.2s;
    }
    [data-testid="stMetric"]:hover {
        transform: translateY(-2px);
        box-shadow: 0 8px 30px rgba(0,0,0,0.4);
        border-color: #2a2a45;
    }
    [data-testid="stMetricLabel"] {
        color: #8888aa !important;
        font-size: 0.75rem !important;
        font-weight: 500 !important;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }
    [data-testid="stMetricValue"] {
        color: #f0f0f0 !important;
        font-family: 'JetBrains Mono', monospace !important;
        font-size: 1.5rem !important;
        font-weight: 700 !important;
    }
    [data-testid="stMetricDelta"] {
        font-family: 'JetBrains Mono', monospace !important;
    }

    /* Status banner */
    .status-active {
        background: linear-gradient(135deg, #0d2818 0%, #0a1f15 100%);
        border: 1px solid #1a4d2e;
        border-radius: 10px;
        padding: 0.8rem 1.2rem;
        margin-bottom: 1rem;
    }
    .status-inactive {
        background: linear-gradient(135deg, #2d1a1a 0%, #1f1212 100%);
        border: 1px solid #4d1a1a;
        border-radius: 10px;
        padding: 0.8rem 1.2rem;
        margin-bottom: 1rem;
    }
    .status-text { color: #f0f0f0; font-weight: 500; font-size: 0.9rem; }
    .status-dot {
        display: inline-block;
        width: 8px; height: 8px;
        border-radius: 50%;
        margin-right: 8px;
        animation: pulse 2s infinite;
    }
    .dot-green { background: #00e676; box-shadow: 0 0 8px #00e67680; }
    .dot-red { background: #ff5252; box-shadow: 0 0 8px #ff525280; }
    @keyframes pulse {
        0%, 100% { opacity: 1; }
        50% { opacity: 0.5; }
    }

    /* Trade cards */
    .trade-card {
        background: linear-gradient(135deg, #12121a 0%, #1a1a2e 100%);
        border: 1px solid #1e1e30;
        border-radius: 12px;
        padding: 1rem 1.2rem;
        margin-bottom: 0.6rem;
        transition: border-color 0.2s;
    }
    .trade-card:hover { border-color: #3a3a55; }
    .trade-card-buy { border-left: 3px solid #00e676; }
    .trade-card-sell { border-left: 3px solid #ff5252; }
    .trade-symbol {
        font-weight: 700; font-size: 1rem; color: #f0f0f0;
        font-family: 'JetBrains Mono', monospace;
    }
    .trade-action-buy { color: #00e676; font-weight: 600; }
    .trade-action-sell { color: #ff5252; font-weight: 600; }
    .trade-detail { color: #8888aa; font-size: 0.82rem; }
    .trade-detail-value { color: #c0c0d0; font-family: 'JetBrains Mono', monospace; font-size: 0.82rem; }
    .trade-sl { color: #ff5252; font-family: 'JetBrains Mono', monospace; font-size: 0.82rem; }
    .trade-tps { color: #00e676; font-family: 'JetBrains Mono', monospace; font-size: 0.75rem; line-height: 1.6; }
    .trade-canal { color: #7c4dff; font-size: 0.78rem; font-weight: 500; }

    /* History items */
    .hist-item {
        background: #12121a;
        border: 1px solid #1a1a28;
        border-radius: 8px;
        padding: 0.6rem 1rem;
        margin-bottom: 0.4rem;
        display: flex;
        align-items: center;
        gap: 0.8rem;
        transition: border-color 0.2s;
    }
    .hist-item:hover { border-color: #2a2a45; }
    .hist-time { color: #666680; font-family: 'JetBrains Mono', monospace; font-size: 0.8rem; min-width: 45px; }
    .hist-symbol { color: #f0f0f0; font-weight: 600; font-size: 0.85rem; }
    .hist-canal { color: #7c4dff; font-size: 0.78rem; }
    .hist-pnl-pos { color: #00e676; font-family: 'JetBrains Mono', monospace; font-weight: 600; font-size: 0.85rem; }
    .hist-pnl-neg { color: #ff5252; font-family: 'JetBrains Mono', monospace; font-weight: 600; font-size: 0.85rem; }
    .hist-pnl-zero { color: #666680; font-family: 'JetBrains Mono', monospace; font-size: 0.85rem; }
    .hist-result { font-size: 0.75rem; font-weight: 600; padding: 2px 8px; border-radius: 4px; }
    .result-WIN { background: #0d2818; color: #00e676; }
    .result-LOSS { background: #2d1a1a; color: #ff5252; }
    .result-BE { background: #1a1a2e; color: #888; }
    .result-OPEN { background: #1a1a3e; color: #448aff; }

    /* Section dividers */
    .section-divider {
        border: none;
        border-top: 1px solid #1e1e30;
        margin: 1.5rem 0 1rem 0;
    }

    /* Expander styling */
    .streamlit-expanderHeader {
        background: #12121a !important;
        border: 1px solid #1e1e30 !important;
        border-radius: 8px !important;
        color: #8888aa !important;
        font-weight: 500 !important;
    }
    .streamlit-expanderContent {
        background: #12121a !important;
        border: 1px solid #1e1e30 !important;
        border-top: none !important;
        border-radius: 0 0 8px 8px !important;
    }

    /* Info boxes */
    .stAlert {
        background: #12121a !important;
        border-radius: 8px !important;
    }

    /* DataFrame styling */
    [data-testid="stDataFrame"] {
        border-radius: 8px;
        overflow: hidden;
    }

    /* Plotly chart background */
    .stPlotlyChart {
        border-radius: 8px;
        overflow: hidden;
    }

    /* Section header with icon */
    .section-header {
        display: flex;
        align-items: center;
        gap: 0.6rem;
        margin-bottom: 1rem;
    }
    .section-icon {
        width: 28px; height: 28px;
        border-radius: 8px;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 0.9rem;
    }
    .icon-green { background: #0d2818; }
    .icon-blue { background: #0d1b2d; }
    .icon-orange { background: #2d1a0d; }
    .icon-purple { background: #1a0d2d; }

    /* Responsive: mobile */
    @media (max-width: 768px) {
        .block-container { padding: 0.8rem !important; }
        [data-testid="stMetric"] { padding: 0.7rem 0.8rem; }
        [data-testid="stMetricValue"] { font-size: 1.2rem !important; }
        h1 { font-size: 1.3rem !important; }
    }

    /* Hide Streamlit hamburger menu and footer */
    #MainMenu { visibility: hidden; }
    footer { visibility: hidden; }
    header { visibility: hidden; }
    
    /* Scrollbar styling */
    ::-webkit-scrollbar { width: 6px; }
    ::-webkit-scrollbar-track { background: #0a0a0f; }
    ::-webkit-scrollbar-thumb { background: #2a2a45; border-radius: 3px; }
    ::-webkit-scrollbar-thumb:hover { background: #3a3a55; }
</style>
""", unsafe_allow_html=True)

# ============================================================
# AUTO-REFRESH
# ============================================================
if _HAS_AUTOREFRESH:
    st_autorefresh(interval=30 * 1000, key="refresh")
else:
    st.markdown('<meta http-equiv="refresh" content="30">', unsafe_allow_html=True)

# ============================================================
# CONNEXION SUPABASE
# ============================================================
@st.cache_resource
def get_supabase():
    supabase_url = ""
    supabase_key = ""
    try:
        supabase_url = st.secrets.get("SUPABASE_URL", "")
        supabase_key = st.secrets.get("SUPABASE_ANON_KEY", "")
    except Exception:
        supabase_url = os.getenv("SUPABASE_URL", "")
        supabase_key = os.getenv("SUPABASE_ANON_KEY", "")

    if not supabase_url or not supabase_key:
        return None
    try:
        return create_client(supabase_url, supabase_key)
    except Exception as e:
        st.error(f"❌ Erreur connexion Supabase: {e}")
        return None

supabase = get_supabase()

if supabase is None:
    st.error("❌ Secrets Supabase manquants!")
    st.info("""Vérifiez les secrets dans Streamlit Cloud :
    ```toml
    SUPABASE_URL = "https://votre-url.supabase.co"
    SUPABASE_ANON_KEY = "votre-cle"
    ```
    """)
    st.stop()

try:
    supabase.table("sessions").select("count", count="exact").limit(1).execute()
except Exception as e:
    st.error(f"❌ Erreur test connexion: {e}")
    st.stop()

# ============================================================
# FONCTIONS DE FETCH
# ============================================================
@st.cache_data(ttl=15)
def fetch_df(table, columns="*", order=None, limit=None, filters=None):
    try:
        query = supabase.table(table).select(columns)
        if filters:
            for col, val in filters.items():
                query = query.eq(col, val)
        if order:
            query = query.order(order, desc=True)
        if limit:
            query = query.limit(limit)
        result = query.execute()
        return pd.DataFrame(result.data) if result.data else pd.DataFrame()
    except Exception as e:
        st.error(f"Erreur requête {table}: {e}")
        return pd.DataFrame()

# ============================================================
# HELPER: metric card with custom color
# ============================================================
def metric_card(label, value, delta=None, color="#f0f0f0"):
    delta_html = ""
    if delta:
        delta_color = "#00e676" if "+" in str(delta) else ("#ff5252" if "-" in str(delta) else "#8888aa")
        delta_html = f'<div style="color:{delta_color};font-size:0.78rem;font-family:JetBrains Mono,monospace;margin-top:2px">{delta}</div>'
    st.markdown(f"""
    <div style="background:linear-gradient(135deg,#12121a 0%,#1a1a2e 100%);border:1px solid #1e1e30;border-radius:12px;padding:1rem 1.2rem;box-shadow:0 4px 20px rgba(0,0,0,0.3)">
        <div style="color:#8888aa;font-size:0.72rem;font-weight:500;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:6px">{label}</div>
        <div style="color:{color};font-family:'JetBrains Mono',monospace;font-size:1.5rem;font-weight:700">{value}</div>
        {delta_html}
    </div>
    """, unsafe_allow_html=True)

# ============================================================
# UI PRINCIPALE
# ============================================================

# Header
st.markdown("""
<div style="display:flex;align-items:center;gap:0.8rem;margin-bottom:1.5rem">
    <div style="font-size:2rem">🤖</div>
    <div>
        <div style="font-size:1.6rem;font-weight:800;color:#f0f0f0;letter-spacing:-0.02em;font-family:Inter,sans-serif">Trading Bot V4</div>
        <div style="color:#666680;font-size:0.78rem;font-family:'JetBrains Mono',monospace">Dashboard</div>
    </div>
</div>
""", unsafe_allow_html=True)

# Session actuelle
sessions = fetch_df("sessions", order="started_at", limit=1)

if sessions.empty:
    st.warning("⏳ Aucune session active. Démarrez le bot pour voir les données.")
    st.stop()

session = sessions.iloc[0]
session_id = session["id"]
is_running = session["status"] == "running"

# Status banner
if is_running:
    st.markdown(f"""
    <div class="status-active" style="display:flex;justify-content:space-between;align-items:center">
        <div class="status-text"><span class="status-dot dot-green"></span>Session active — démarrée le {session['started_at'][:16]}</div>
        <div style="display:flex;align-items:center;gap:0.6rem">
            <span style="color:#666680;font-size:0.75rem;text-transform:uppercase">Mode</span>
            <span style="color:#f0f0f0;font-weight:700;font-family:'JetBrains Mono',monospace;font-size:0.9rem">{session.get('mode', 'DEMO')}</span>
        </div>
    </div>
    """, unsafe_allow_html=True)
else:
    st.markdown(f"""
    <div class="status-inactive" style="display:flex;justify-content:space-between;align-items:center">
        <div class="status-text"><span class="status-dot dot-red"></span>Session terminée — {session['started_at'][:16]}</div>
        <div style="display:flex;align-items:center;gap:0.6rem">
            <span style="color:#666680;font-size:0.75rem;text-transform:uppercase">Mode</span>
            <span style="color:#f0f0f0;font-weight:700;font-family:'JetBrains Mono',monospace;font-size:0.9rem">{session.get('mode', 'DEMO')}</span>
        </div>
    </div>
    """, unsafe_allow_html=True)

# Métriques
trades = fetch_df("trades", filters={"session_id": session_id})

if not trades.empty:
    closed = trades[trades["result"].isin(["WIN", "LOSS", "BE"])]
    total_pnl = closed["pnl"].sum() if not closed.empty else 0
    total_trades = len(closed)
    wins = len(closed[closed["result"] == "WIN"]) if not closed.empty else 0
    losses = len(closed[closed["result"] == "LOSS"]) if not closed.empty else 0
    still_open = len(trades[trades["result"] == "OPEN"])
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0

    pnl_color = "#00e676" if total_pnl > 0 else ("#ff5252" if total_pnl < 0 else "#f0f0f0")
    pnl_delta = f"{total_pnl:+.2f}$" if total_pnl != 0 else None

    col1, col2, col3, col4, col5 = st.columns(5)
    with col1: metric_card("💰 P&L", f"{total_pnl:+.2f}$", delta=pnl_delta, color=pnl_color)
    with col2: metric_card("📊 Trades", str(total_trades))
    with col3: metric_card("✅ Win Rate", f"{win_rate:.1f}%")
    with col4: metric_card("🟢 Ouverts", str(still_open), color="#00e676")
    with col5: metric_card("❌ Losses", str(losses), color="#ff5252")
else:
    st.info("Aucun trade cette session.")
    total_pnl = 0; total_trades = 0; wins = 0; losses = 0; still_open = 0; win_rate = 0

# ============================================================
# TRADES OUVERTS
# ============================================================
st.markdown('<hr class="section-divider">', unsafe_allow_html=True)
st.markdown("""
<div class="section-header">
    <div class="section-icon icon-green">🟢</div>
    <h2 style="margin:0">Trades ouverts</h2>
</div>
""", unsafe_allow_html=True)

open_trades = trades[trades["result"] == "OPEN"] if not trades.empty else pd.DataFrame()

if not open_trades.empty:
    for _, t in open_trades.iterrows():
        is_buy = t["action"] == "BUY"
        card_class = "trade-card-buy" if is_buy else "trade-card-sell"
        action_class = "trade-action-buy" if is_buy else "trade-action-sell"
        action_emoji = "🟢" if is_buy else "🔴"
        tps_raw = t["tps"] if "tps" in t.index and isinstance(t["tps"], list) else []
        tps_lines = ""
        for i, v in enumerate(tps_raw):
            tps_lines += f'<span style="margin-right:12px">TP{i+1}={v}</span>'
            if (i + 1) % 3 == 0:
                tps_lines += "<br>"

        st.markdown(f"""
        <div class="trade-card {card_class}">
            <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:0.5rem">
                <div style="display:flex;align-items:center;gap:0.8rem;flex-wrap:wrap">
                    <span class="trade-symbol">{t['symbol']}</span>
                    <span class="{action_class}">{action_emoji} {t['action']}</span>
                    <span class="trade-canal">📡 {t['canal']}</span>
                </div>
                <div style="display:flex;gap:1.2rem;flex-wrap:wrap">
                    <span class="trade-detail">Entry <span class="trade-detail-value">{t.get('entry_price', '—')}</span></span>
                    <span class="trade-sl">SL {t['sl']}</span>
                </div>
            </div>
            <div class="trade-tps" style="margin-top:0.6rem">{tps_lines}</div>
        </div>
        """, unsafe_allow_html=True)
else:
    st.markdown("""
    <div style="color:#666680;font-size:0.85rem;padding:1rem;background:#12121a;border-radius:8px;border:1px solid #1e1e30;text-align:center">
        Aucun trade ouvert
    </div>
    """, unsafe_allow_html=True)

# ============================================================
# PERFORMANCE PAR CANAL
# ============================================================
st.markdown('<hr class="section-divider">', unsafe_allow_html=True)
st.markdown("""
<div class="section-header">
    <div class="section-icon icon-purple">📊</div>
    <h2 style="margin:0">Performance par canal</h2>
</div>
""", unsafe_allow_html=True)

canal_df = pd.DataFrame()
if not trades.empty:
    closed = trades[trades["result"].isin(["WIN", "LOSS", "BE"])]
    if not closed.empty:
        for canal in closed["canal"].unique():
            ct = closed[closed["canal"] == canal]
            w = len(ct[ct["result"] == "WIN"])
            l = len(ct[ct["result"] == "LOSS"])
            t = len(ct)
            pnl = ct["pnl"].sum()
            canal_df = pd.concat([canal_df, pd.DataFrame([{
                "canal": canal,
                "total_trades": t,
                "wins": w,
                "losses": l,
                "total_pnl": round(pnl, 2),
                "win_rate": round(w/t*100, 1) if t > 0 else 0,
            }])])

if not canal_df.empty:
    canal_df = canal_df.sort_values("total_pnl", ascending=False)

    display_cols = {
        "canal": "Canal", "total_trades": "Trades", "wins": "Wins",
        "losses": "Losses", "win_rate": "Win %", "total_pnl": "P&L ($)",
        "profit_factor": "PF",
    }
    cols_available = [c for c in display_cols if c in canal_df.columns]
    st.dataframe(
        canal_df[cols_available].rename(columns=display_cols),
        use_container_width=True, hide_index=True,
    )

    fig = px.bar(
        canal_df, x="canal", y="total_pnl",
        color="total_pnl",
        color_continuous_scale=["#ff5252", "#ffa726", "#00e676"],
        labels={"canal": "Canal", "total_pnl": "P&L ($)"},
    )
    fig.update_layout(
        height=300,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter, sans-serif", color="#8888aa"),
        xaxis=dict(gridcolor="#1e1e30"),
        yaxis=dict(gridcolor="#1e1e30"),
        margin=dict(l=20, r=20, t=30, b=20),
    )
    st.plotly_chart(fig, use_container_width=True)
else:
    st.markdown("""
    <div style="color:#666680;font-size:0.85rem;padding:1rem;background:#12121a;border-radius:8px;border:1px solid #1e1e30;text-align:center">
        Pas assez de données pour les stats par canal
    </div>
    """, unsafe_allow_html=True)

# ============================================================
# COURBE DE P&L
# ============================================================
st.markdown('<hr class="section-divider">', unsafe_allow_html=True)
st.markdown("""
<div class="section-header">
    <div class="section-icon icon-blue">📈</div>
    <h2 style="margin:0">Courbe de P&L</h2>
</div>
""", unsafe_allow_html=True)

if not trades.empty:
    closed = trades[trades["result"].isin(["WIN", "LOSS", "BE"])].copy()
    if not closed.empty:
        closed = closed.sort_values("closed_at")
        closed["pnl_cumsum"] = closed["pnl"].cumsum()

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=list(range(len(closed))),
            y=closed["pnl_cumsum"].tolist(),
            mode="lines+markers",
            name="P&L cumulé",
            line=dict(color="#00e676", width=2),
            fill="tozeroy",
            fillcolor="rgba(0, 230, 118, 0.08)",
            marker=dict(size=6, color="#00e676", line=dict(width=1, color="#0a0a0f")),
        ))
        fig.update_layout(
            height=300,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(family="Inter, sans-serif", color="#8888aa"),
            xaxis=dict(gridcolor="#1e1e30", title="Trade #"),
            yaxis=dict(gridcolor="#1e1e30", title="P&L ($)"),
            margin=dict(l=20, r=20, t=10, b=20),
            showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True)

# ============================================================
# HISTORIQUE
# ============================================================
st.markdown('<hr class="section-divider">', unsafe_allow_html=True)
st.markdown("""
<div class="section-header">
    <div class="section-icon icon-orange">📜</div>
    <h2 style="margin:0">Historique des trades</h2>
</div>
""", unsafe_allow_html=True)

hist = fetch_df("trades", filters={"session_id": session_id}, order="opened_at", limit=50)

if not hist.empty:
    for _, t in hist.iterrows():
        result = t["result"]
        if result == "WIN": emoji = "✅"; result_class = "result-WIN"
        elif result == "LOSS": emoji = "❌"; result_class = "result-LOSS"
        elif result == "BE": emoji = "⬜"; result_class = "result-BE"
        else: emoji = "🔵"; result_class = "result-OPEN"

        pnl_str = f"{t['pnl']:+.2f}$" if result != "OPEN" else "—"
        pnl_class = "hist-pnl-pos" if result == "WIN" else ("hist-pnl-neg" if result == "LOSS" else "hist-pnl-zero")
        time_str = t["opened_at"][11:16] if t["opened_at"] else "—"

        st.markdown(f"""
        <div class="hist-item">
            <span class="hist-time">{time_str}</span>
            <span style="font-size:0.9rem">{emoji}</span>
            <span class="hist-symbol">{t['symbol']} {t['action']}</span>
            <span class="hist-canal">{t['canal']}</span>
            <span class="{pnl_class}" style="margin-left:auto">{pnl_str}</span>
            <span class="hist-result {result_class}">{result}</span>
        </div>
        """, unsafe_allow_html=True)
else:
    st.markdown("""
    <div style="color:#666680;font-size:0.85rem;padding:1rem;background:#12121a;border-radius:8px;border:1px solid #1e1e30;text-align:center">
        Aucun historique
    </div>
    """, unsafe_allow_html=True)

# ============================================================
# CONFIG
# ============================================================
st.markdown('<hr class="section-divider">', unsafe_allow_html=True)
with st.expander("⚙️ Configuration"):
    st.markdown(f"""
    - **Lot size**: {session.get('lot_size', '—')}
    - **Mode**: {session.get('mode', 'DEMO')}
    - **Runtime**: {session.get('runtime_minutes', '—')} min
    - **Canaux**: {', '.join(session.get('channels', []))}
    - **Session ID**: `{session_id}`
    """)

# Footer
st.markdown(f"""
<div style="text-align:center;color:#444460;font-size:0.72rem;padding:0.5rem 0;font-family:'JetBrains Mono',monospace">
    🔄 Auto-refresh 30s — {datetime.now():%H:%M:%S}
</div>
""", unsafe_allow_html=True)
