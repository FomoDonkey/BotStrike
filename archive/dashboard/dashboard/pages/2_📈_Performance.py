"""Performance & Trade History — Professional dark dashboard."""
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import sys, os, time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from dashboard.data_feed import get_store, start_ws_feed, parse_log

st.set_page_config(page_title="Performance", page_icon="📈", layout="wide")
st.markdown("""
<style>
    .stApp { background-color: #0a0e17; }
    .metric-box {
        background: linear-gradient(135deg, #1a1f2e 0%, #141824 100%);
        border: 1px solid #2a3042; border-radius: 14px; padding: 18px;
        text-align: center; min-height: 90px;
    }
    .metric-val { font-size: 1.9rem; font-weight: 800; margin: 0; line-height: 1.2; }
    .metric-lbl { font-size: 0.75rem; color: #667; margin: 0; text-transform: uppercase; letter-spacing: 1px; }
    .trade-row {
        display: flex; align-items: center; gap: 12px;
        padding: 10px 14px; margin: 5px 0; border-radius: 10px;
        border-left: 4px solid; font-size: 0.88rem;
    }
    .t-open { background: rgba(255,165,2,0.06); border-color: #ffa502; }
    .t-win { background: rgba(0,212,170,0.06); border-color: #00d4aa; }
    .t-loss { background: rgba(255,71,87,0.06); border-color: #ff4757; }
    .t-closed { background: rgba(100,100,120,0.06); border-color: #555; }
    .t-tag { display:inline-block; padding:2px 8px; border-radius:4px; font-size:0.75rem; font-weight:700; }
</style>
""", unsafe_allow_html=True)

start_ws_feed()
parse_log()
s = get_store()

st.markdown("# 📈 Performance")

# Top metrics with floating PnL
realized = s["pnl"]
floating = s["floating_pnl"]
total_pnl = realized + floating
equity = 300.0 + total_pnl
ret_pct = total_pnl / 300 * 100

c1, c2, c3, c4, c5, c6 = st.columns(6)
def _clr(val):
    """Verde si positivo, rojo si negativo, gris si cero."""
    if val > 0.001: return "#00d4aa"
    if val < -0.001: return "#ff4757"
    return "#8899aa"

metrics = [
    (c1, "EQUITY", f"${equity:,.2f}", _clr(total_pnl)),
    (c2, "FLOATING PnL", f"${floating:+,.2f}", _clr(floating)),
    (c3, "REALIZED PnL", f"${realized:+,.2f}", _clr(realized)),
    (c4, "TOTAL PnL", f"${total_pnl:+,.2f}", _clr(total_pnl)),
    (c5, "RETURN", f"{ret_pct:+.2f}%", _clr(total_pnl)),
    (c6, "POSITIONS", str(len(s["open_positions"])), "#ffa502" if s["open_positions"] else "#8899aa"),
]
for col, lbl, val, clr in metrics:
    col.markdown(f'<div class="metric-box"><p class="metric-lbl">{lbl}</p><p class="metric-val" style="color:{clr}">{val}</p></div>', unsafe_allow_html=True)

st.markdown("")

# Open positions
if s["open_positions"]:
    st.markdown("### Open Positions")
    for pos in s["open_positions"]:
        side = pos.get("side", "?")
        entry = pos.get("price", 0)
        sl = pos.get("sl", 0)
        tp = pos.get("tp", 0)
        size = pos.get("size", 0)
        strat = pos.get("strategy", "")
        # Floating PnL
        curr_price = s["price"]
        if side == "BUY":
            fpnl = (curr_price - entry) * size
        else:
            fpnl = (entry - curr_price) * size
        fpnl_color = "#00d4aa" if fpnl >= 0 else "#ff4757"
        side_color = "#00d4aa" if side == "BUY" else "#ff4757"
        # Distance to SL/TP
        if curr_price > 0:
            if side == "BUY":
                sl_dist = (curr_price - sl) / curr_price * 100
                tp_dist = (tp - curr_price) / curr_price * 100
            else:
                sl_dist = (sl - curr_price) / curr_price * 100
                tp_dist = (curr_price - tp) / curr_price * 100
        else:
            sl_dist = 0
            tp_dist = 0

        st.markdown(f"""
        <div class="trade-row t-open">
            <span class="t-tag" style="background:{side_color}22;color:{side_color};">{side}</span>
            <span style="color:#ddd;font-weight:600;">${entry:,.2f}</span>
            <span style="color:#ff4757;font-size:0.8rem;">SL ${sl:,.0f} ({sl_dist:.1f}%)</span>
            <span style="color:#00d4aa;font-size:0.8rem;">TP ${tp:,.0f} ({tp_dist:.1f}%)</span>
            <span style="color:{fpnl_color};font-weight:800;font-size:1.1rem;margin-left:auto;">${fpnl:+,.2f}</span>
            <span style="color:#556;font-size:0.75rem;">{strat[:10]}</span>
        </div>
        """, unsafe_allow_html=True)

# PnL Chart (floating, real-time)
st.markdown("### PnL (Floating)")
pnl_hist = list(s["pnl_history"])
if len(pnl_hist) > 1:
    times = [datetime.fromtimestamp(p["t"]) for p in pnl_hist]
    vals = [p["v"] for p in pnl_hist]

    fig = go.Figure()
    # Area fill: green above 0, red below
    fig.add_trace(go.Scatter(
        x=times, y=[max(0, v) for v in vals],
        mode="lines", line=dict(width=0),
        fill="tozeroy", fillcolor="rgba(0,212,170,0.15)",
        showlegend=False, hoverinfo="skip",
    ))
    fig.add_trace(go.Scatter(
        x=times, y=[min(0, v) for v in vals],
        mode="lines", line=dict(width=0),
        fill="tozeroy", fillcolor="rgba(255,71,87,0.15)",
        showlegend=False, hoverinfo="skip",
    ))
    # Main line
    last_color = "#00d4aa" if vals[-1] >= 0 else "#ff4757"
    fig.add_trace(go.Scatter(
        x=times, y=vals, mode="lines",
        line=dict(color=last_color, width=2.5),
        name=f"PnL ${vals[-1]:+,.2f}",
    ))
    fig.add_hline(y=0, line_dash="solid", line_color="#334", line_width=1)

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#0a0e17", plot_bgcolor="#0f1419",
        height=250, margin=dict(l=50, r=10, t=10, b=30),
        yaxis_title="$", showlegend=True,
        legend=dict(x=0.02, y=0.98, bgcolor="rgba(0,0,0,0)", font=dict(size=14)),
        hovermode="x unified",
    )
    fig.update_xaxes(gridcolor="#1a1f2e")
    fig.update_yaxes(gridcolor="#1a1f2e")
    st.plotly_chart(fig, width="stretch", key="pnl_float")

st.markdown("---")

# Trade history as cards + Stats
col_left, col_right = st.columns([3, 2])
trades = list(s["bot_trades"])

with col_left:
    st.markdown("### Trade History")
    if trades:
        for t in reversed(trades[-15:]):
            tp_type = t.get("type", "?")
            status = t.get("status", "")
            pnl_val = t.get("pnl")
            price = t.get("price", 0)
            sl = t.get("sl", 0)
            tp = t.get("tp", 0)
            side = t.get("side", "")
            strat = t.get("strategy", "")

            if tp_type == "ENTRY" and status == "OPEN":
                card_cls = "t-open"
                icon = "🟡"
            elif pnl_val is not None and pnl_val > 0:
                card_cls = "t-win"
                icon = "🟢"
            elif pnl_val is not None and pnl_val <= 0:
                card_cls = "t-loss"
                icon = "🔴"
            else:
                card_cls = "t-closed"
                icon = "⚪"

            side_clr = "#00d4aa" if side == "BUY" else ("#ff4757" if side == "SELL" else "#555")
            pnl_html = f'<span style="color:{"#00d4aa" if pnl_val > 0 else "#ff4757"};font-weight:800;">${pnl_val:+,.2f}</span>' if pnl_val is not None else ""
            sl_html = f'<span style="color:#ff4757;font-size:0.78rem;">SL ${sl:,.0f}</span>' if sl > 0 else ""
            tp_html = f'<span style="color:#00d4aa;font-size:0.78rem;">TP ${tp:,.0f}</span>' if tp > 0 else ""

            st.markdown(f"""
            <div class="trade-row {card_cls}">
                <span>{icon}</span>
                <span class="t-tag" style="background:{'#3b82f622' if tp_type=='ENTRY' else '#55555522'};color:{'#3b82f6' if tp_type=='ENTRY' else '#888'};">{tp_type}</span>
                <span class="t-tag" style="background:{side_clr}22;color:{side_clr};">{side}</span>
                <span style="color:#ccc;font-weight:600;">{'${:,.2f}'.format(price) if price > 0 else ''}</span>
                {sl_html} {tp_html} {pnl_html}
                <span style="color:#445;font-size:0.75rem;margin-left:auto;">{strat[:10]} {t.get('time','')}</span>
            </div>
            """, unsafe_allow_html=True)
    else:
        st.caption("No trades yet.")

with col_right:
    st.markdown("### Stats")

    # Count closed trades only (not open entries)
    closed = [t for t in trades if t.get("pnl") is not None]
    wins_count = sum(1 for t in closed if t["pnl"] > 0)
    losses_count = sum(1 for t in closed if t["pnl"] <= 0)
    open_count = len(s["open_positions"])

    if closed:
        wr = wins_count / len(closed) * 100
        fig_donut = go.Figure(go.Pie(
            values=[max(wins_count, 0.1), max(losses_count, 0.1)],
            labels=["Wins", "Losses"],
            marker=dict(colors=["#00d4aa", "#ff4757"]),
            hole=0.7, textinfo="label+value", textfont=dict(size=13),
        ))
        fig_donut.add_annotation(
            text=f"{wr:.0f}%", x=0.5, y=0.5,
            font=dict(size=32, color="#fff", family="Arial Black"), showarrow=False,
        )
        fig_donut.update_layout(
            paper_bgcolor="#0a0e17", height=190,
            margin=dict(l=0, r=0, t=0, b=0), showlegend=False,
        )
        st.plotly_chart(fig_donut, width="stretch", key="donut")
    elif open_count > 0:
        st.markdown(f"""
        <div style="text-align:center;padding:20px;color:#ffa502;">
            <p style="font-size:2rem;">🟡</p>
            <p style="font-weight:700;">{open_count} open position{'s' if open_count > 1 else ''}</p>
            <p style="color:#556;font-size:0.85rem;">Floating: <span style="color:{'#00d4aa' if s['floating_pnl'] >= 0 else '#ff4757'};font-weight:700;">${s['floating_pnl']:+,.2f}</span></p>
            <p style="color:#445;font-size:0.8rem;">Stats appear after first close</p>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.markdown("""
        <div style="text-align:center;padding:20px;color:#445;">
            <p style="font-size:1.5rem;">📊</p>
            <p>Waiting for trades...</p>
        </div>
        """, unsafe_allow_html=True)

    st.metric("Runtime", f"{s['runtime']:.1f}h")
    st.metric("Open Positions", open_count)
    st.metric("Signals", f"{len(s['signals_generated'])} gen / {s['signals_blocked']} blocked")

time.sleep(2)
st.rerun()
