"""Order Flow & Whale Tracking."""
import streamlit as st
import plotly.graph_objects as go
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from dashboard.data_feed import get_store, start_ws_feed, parse_log

st.set_page_config(page_title="Order Flow", page_icon="🐋", layout="wide")
st.markdown("""
<style>
    .stApp { background-color: #0a0e17; }
    .whale-row {
        display: flex; align-items: center; justify-content: space-between;
        padding: 10px 16px; margin: 4px 0; border-radius: 8px;
        border-left: 4px solid; font-family: monospace;
    }
    .whale-buy { background: rgba(0,212,170,0.08); border-color: #00d4aa; }
    .whale-sell { background: rgba(255,71,87,0.08); border-color: #ff4757; }
    .whale-time { color: #556; font-size: 0.85rem; }
    .whale-side-buy { color: #00d4aa; font-weight: 800; font-size: 1rem; }
    .whale-side-sell { color: #ff4757; font-weight: 800; font-size: 1rem; }
    .whale-amount { color: #ddd; font-weight: 600; font-size: 1.1rem; }
    .whale-btc { color: #8899aa; font-size: 0.85rem; }
    .stat-card {
        background: linear-gradient(135deg, #1a1f2e 0%, #141824 100%);
        border: 1px solid #2a3042; border-radius: 12px; padding: 16px;
        text-align: center;
    }
    .stat-value { font-size: 1.8rem; font-weight: 800; margin: 0; }
    .stat-label { font-size: 0.8rem; color: #8899aa; margin: 0; }
</style>
""", unsafe_allow_html=True)

start_ws_feed()
parse_log()
s = get_store()

st.markdown("# 🐋 Order Flow")

# Buy/Sell pressure bar
total = s["volume_buy"] + s["volume_sell"]
buy_pct = s["volume_buy"] / total * 100 if total > 0 else 50

fig = go.Figure()
fig.add_trace(go.Bar(x=[buy_pct], y=[""], orientation="h",
    marker_color="#00d4aa", name=f"BUY {buy_pct:.0f}%", width=0.5))
fig.add_trace(go.Bar(x=[100 - buy_pct], y=[""], orientation="h",
    marker_color="#ff4757", name=f"SELL {100-buy_pct:.0f}%", width=0.5))
fig.update_layout(
    barmode="stack", template="plotly_dark",
    paper_bgcolor="#0a0e17", plot_bgcolor="#0f1419",
    height=60, margin=dict(l=0, r=0, t=0, b=0),
    xaxis=dict(visible=False), yaxis=dict(visible=False),
    legend=dict(orientation="h", x=0.3, y=1.3, font=dict(size=14)),
    showlegend=True,
)
st.plotly_chart(fig, width="stretch")

# Volume stats
c1, c2, c3, c4 = st.columns(4)
for col, label, value, color in [
    (c1, "BUY VOLUME", f"${s['volume_buy']/1e6:.1f}M", "#00d4aa"),
    (c2, "SELL VOLUME", f"${s['volume_sell']/1e6:.1f}M", "#ff4757"),
    (c3, "TPS", str(s["tps"]), "#3b82f6"),
    (c4, "TOTAL", f"${total/1e6:.1f}M", "#8899aa"),
]:
    col.markdown(f"""
    <div class="stat-card">
        <p class="stat-label">{label}</p>
        <p class="stat-value" style="color:{color}">{value}</p>
    </div>
    """, unsafe_allow_html=True)

st.markdown("---")

# Whale section
st.markdown("### 🐋 Whale Trades (> $250K)")

# Whale summary
wc1, wc2, wc3, wc4 = st.columns(4)
for col, label, value, color in [
    (wc1, "WHALE BUYS", str(s["whale_buy_count"]), "#00d4aa"),
    (wc2, "WHALE SELLS", str(s["whale_sell_count"]), "#ff4757"),
    (wc3, "WHALE BUY VOL", f"${s['whale_buy_vol']/1e6:.1f}M", "#00d4aa"),
    (wc4, "WHALE SELL VOL", f"${s['whale_sell_vol']/1e6:.1f}M", "#ff4757"),
]:
    col.markdown(f"""
    <div class="stat-card">
        <p class="stat-label">{label}</p>
        <p class="stat-value" style="color:{color}">{value}</p>
    </div>
    """, unsafe_allow_html=True)

st.markdown("")

# Whale trades as styled cards (not table)
whales = list(s["whale_trades"])
if whales:
    for w in reversed(list(whales)[:15]):
        side_class = "whale-buy" if w["side"] == "BUY" else "whale-sell"
        side_label = "whale-side-buy" if w["side"] == "BUY" else "whale-side-sell"
        st.markdown(f"""
        <div class="whale-row {side_class}">
            <span class="whale-time">{w["time"]}</span>
            <span class="{side_label}">{w["side"]}</span>
            <span class="whale-amount">${w["usd"]:,.0f}</span>
            <span class="whale-btc">{w["qty"]:.4f} BTC</span>
        </div>
        """, unsafe_allow_html=True)
else:
    st.caption("No whale trades detected yet...")

time.sleep(2)
st.rerun()
