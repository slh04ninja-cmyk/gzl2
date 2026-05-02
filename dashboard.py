"""
Trading Bot Dashboard — Streamlit V4
Accessible sur mobile via Streamlit Cloud
"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from supabase import create_client
import os
from datetime import datetime, timedelta

st.set_page_config(
    page_title="Trading Bot V4 Dashboard",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="collapsed",
)

SUPABASE_URL = st.secrets.get("SUPABASE_URL", "")
SUPABASE_KEY = st.secrets.get("SUPABASE_ANON_KEY", "")


@st.cache_resource
def get_supabase():
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def fetch_df(table, columns="*", order=None, limit=None, filters=None):
    try:
        supabase = get_supabase()
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
        st.error(f"Erreur: {e}")
        return pd.DataFrame()


st.markdown("""
<style>
    .stMetric {background: #1e1e2e; border-radius: 10px; padding: 15px;}
    .stMetric label {font-size: 14px !important;}
    div[data-testid="stHorizontalBlock"] > div {padding: 0 5px;}
</style>
""", unsafe_allow_html=True)

st.title("🤖 Trading Bot V4 Dashboard")

st.markdown(
    '<meta http-equiv="refresh" content="30">',
    unsafe_allow_html=True
)

# Session actuelle
sessions = fetch_df("sessions", order="started_at", limit=1)

if sessions.empty:
    st.warning("⏳ Aucune session active. Démarrez le bot pour voir les données.")
    st.stop()

session = sessions.iloc[0]
session_id = session["id"]
is_running = session["status"] == "running"

col_status, col_mode = st.columns([3, 1])
with col_status:
    if is_running:
        st.success(f"🟢 Session active — démarrée le {session['started_at'][:16]}")
    else:
        st.info(f"🔴 Session terminée — {session['started_at'][:16]}")
with col_mode:
    st.metric("Mode", session.get("mode", "DEMO"))

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

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("💰 P&L", f"{total_pnl:+.2f}$",
                delta=f"{total_pnl:+.2f}$" if total_pnl != 0 else None)
    col2.metric("📊 Trades", total_trades)
    col3.metric("✅ Win Rate", f"{win_rate:.1f}%")
    col4.metric("🟢 Ouverts", still_open)
    col5.metric("❌ Losses", losses)
else:
    st.info("Aucun trade cette session.")
    total_pnl = 0
    total_trades = 0
    wins = 0
    losses = 0
    still_open = 0
    win_rate = 0

# Trades ouverts
st.markdown("---")
st.subheader("🟢 Trades ouverts")

open_trades = trades[trades["result"] == "OPEN"] if not trades.empty else pd.DataFrame()

if not open_trades.empty:
    for _, t in open_trades.iterrows():
        emoji = "🟢" if t["action"] == "BUY" else "🔴"
        tps_str = ", ".join([f"TP{i+1}={v}" for i, v in enumerate(t.get("tps", []))])
        with st.container():
            cols = st.columns([2, 1, 1, 1, 1])
            cols[0].markdown(f"{emoji} **{t['symbol']}** {t['action']}")
            cols[1].markdown(f"📍 {t.get('entry_price', '—')}")
            cols[2].markdown(f"❌ SL: {t['sl']}")
            cols[3].markdown(f"🎯 {tps_str}")
            cols[4].markdown(f"📡 {t['canal']}")
else:
    st.info("Aucun trade ouvert.")

# Performance par canal
st.markdown("---")
st.subheader("📊 Performance par canal")

try:
    supabase = get_supabase()
    canal_result = supabase.table("canal_stats").select("*").execute()
    canal_df = pd.DataFrame(canal_result.data) if canal_result.data else pd.DataFrame()
except Exception:
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
        "canal": "Canal",
        "total_trades": "Trades",
        "wins": "Wins",
        "losses": "Losses",
        "win_rate": "Win %",
        "total_pnl": "P&L ($)",
        "profit_factor": "PF",
    }
    cols_available = [c for c in display_cols if c in canal_df.columns]
    st.dataframe(
        canal_df[cols_available].rename(columns=display_cols),
        use_container_width=True,
        hide_index=True,
    )

    fig = px.bar(
        canal_df, x="canal", y="total_pnl",
        color="total_pnl",
        color_continuous_scale=["red", "yellow", "green"],
        title="P&L par canal",
        labels={"canal": "Canal", "total_pnl": "P&L ($)"},
    )
    fig.update_layout(height=300)
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("Pas assez de données pour les stats par canal.")

# Courbe de P&L
st.markdown("---")
st.subheader("📈 Courbe de P&L")

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
            line=dict(color="#00d4aa", width=2),
            fill="tozeroy",
            fillcolor="rgba(0, 212, 170, 0.1)",
        ))
        fig.update_layout(
            title="Evolution du P&L",
            xaxis_title="Trade #",
            yaxis_title="P&L ($)",
            height=300,
        )
        st.plotly_chart(fig, use_container_width=True)

# Historique
st.markdown("---")
st.subheader("📜 Historique des trades")

hist = fetch_df("trades", filters={"session_id": session_id},
                order="opened_at", limit=50)

if not hist.empty:
    for _, t in hist.iterrows():
        if t["result"] == "WIN":
            emoji = "✅"
        elif t["result"] == "LOSS":
            emoji = "❌"
        elif t["result"] == "BE":
            emoji = "⬜"
        else:
            emoji = "🔵"

        pnl_str = f"{t['pnl']:+.2f}$" if t["result"] != "OPEN" else "—"
        time_str = t["opened_at"][11:16] if t["opened_at"] else "—"

        st.markdown(
            f"{emoji} **{time_str}** | {t['symbol']} {t['action']} | "
            f"{t['canal']} | P&L: **{pnl_str}** | {t['result']}"
        )
else:
    st.info("Aucun historique.")

# Config
st.markdown("---")
with st.expander("⚙️ Configuration"):
    st.markdown(f"""
    - **Lot size**: {session.get('lot_size', '—')}
    - **Mode**: {session.get('mode', 'DEMO')}
    - **Runtime**: {session.get('runtime_minutes', '—')} min
    - **Canaux**: {', '.join(session.get('channels', []))}
    - **Session ID**: `{session_id}`
    """)

st.markdown("---")
st.caption(f"🔄 Rafraîchissement auto toutes les 30s — {datetime.now():%H:%M:%S}")
