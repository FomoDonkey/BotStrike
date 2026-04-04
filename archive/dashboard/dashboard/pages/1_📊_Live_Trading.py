"""Live Trading Dashboard — Real-time charts and microstructure."""
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import time
from datetime import datetime
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from dashboard.data_feed import get_store, start_ws_feed, parse_log

st.set_page_config(page_title="Live Trading", page_icon="📊", layout="wide")

st.markdown("""
<style>
    .stApp { background-color: #0a0e17; }
    [data-testid="stMetricValue"] { font-size: 1.6rem; }
    .big-num { font-size: 2.8rem; font-weight: 800; margin: 0; line-height: 1.1; }
</style>
""", unsafe_allow_html=True)

start_ws_feed()
parse_log()
s = get_store()

# Sidebar
with st.sidebar:
    st.markdown("## ⚡ BotStrike")
    refresh = st.selectbox("Refresh", [1, 2, 5, 10], index=1, format_func=lambda x: f"{x}s")
    st.markdown("---")

    if s["ws_connected"]:
        st.success("🟢 Binance Connected")
    else:
        st.error("🔴 Disconnected")

    total_pnl = s["pnl"] + s["floating_pnl"]
    eq = 300 + total_pnl
    eq_clr = "#00d4aa" if total_pnl > 0.001 else ("#ff4757" if total_pnl < -0.001 else "#8899aa")
    fp = s["floating_pnl"]
    fp_clr = "#00d4aa" if fp > 0.001 else ("#ff4757" if fp < -0.001 else "#8899aa")
    st.markdown(f"""
    <div style="margin:4px 0;">
        <div style="color:#667;font-size:0.75rem;">EQUITY</div>
        <div style="color:{eq_clr};font-size:1.4rem;font-weight:800;">${eq:,.2f}</div>
        <div style="color:{eq_clr};font-size:0.85rem;">{total_pnl:+,.2f}</div>
    </div>
    <div style="margin:8px 0;">
        <div style="color:#667;font-size:0.75rem;">FLOATING PnL</div>
        <div style="color:{fp_clr};font-size:1.2rem;font-weight:700;">${fp:+,.2f}</div>
        <div style="color:#556;font-size:0.8rem;">{len(s['open_positions'])} positions</div>
    </div>
    """, unsafe_allow_html=True)
    st.metric("Bot Trades", s["total_trades"])
    st.metric("TPS", s["tps"])

    st.markdown("---")

    # Daily trend indicator
    dt = s.get("daily_trend", "...")
    dt_colors = {"BULLISH": "#00d4aa", "WEAK BULL": "#00d4aa", "BEARISH": "#ff4757",
                 "WEAK BEAR": "#ff4757", "NEUTRAL": "#ffa502", "MIXED": "#ffa502"}
    dt_icons = {"BULLISH": "📈", "WEAK BULL": "↗️", "BEARISH": "📉",
                "WEAK BEAR": "↘️", "NEUTRAL": "➡️", "MIXED": "↔️"}
    dt_clr = dt_colors.get(dt, "#888")
    dt_icon = dt_icons.get(dt, "⏳")
    st.markdown(f"""
    <div style="text-align:center;padding:8px;border-radius:10px;margin:4px 0;
                background:{dt_clr}15;border:1px solid {dt_clr};">
        <div style="color:#667;font-size:0.7rem;">TREND 4H+1D</div>
        <div style="color:{dt_clr};font-weight:800;font-size:1.1rem;">{dt_icon} {dt}</div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("---")
    regime = s["regime"]
    rc = {"RANGING": "#ffa502", "TRENDING_UP": "#00d4aa",
          "TRENDING_DOWN": "#ff4757", "BREAKOUT": "#ff6b81"}.get(regime, "#888")
    st.markdown(f'<div style="text-align:center;padding:8px;border-radius:10px;'
                f'background:{rc}22;border:1px solid {rc};color:{rc};font-weight:700;">'
                f'{regime}</div>', unsafe_allow_html=True)

# Price header
price = s["price"]
chg = s["change_24h"]
chg_color = "#00d4aa" if chg >= 0 else "#ff4757"

col1, col2, col3, col4, col5 = st.columns([2, 1, 1, 1, 1])
with col1:
    st.markdown(f'<p class="big-num" style="color:{chg_color}">${price:,.2f}</p>',
                unsafe_allow_html=True)
    st.caption(f"BTC/USDT  {chg:+.2f}%  H:${s['high_24h']:,.0f}  L:${s['low_24h']:,.0f}")
with col2:
    st.metric("ADX", f"{s['adx']:.1f}")
with col3:
    st.metric("Momentum", f"{s['momentum']:.4f}")
with col4:
    st.metric("Vol %", f"{s['vol_pct']:.2f}")
with col5:
    st.metric("Runtime", f"{s['runtime']:.1f}h")

st.markdown("---")

# Candlestick chart
candles = list(s["candles_1m"])
if len(candles) >= 3:
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        row_heights=[0.85, 0.15], vertical_spacing=0.02)

    times = [c["time"] for c in candles]
    opens = [c["open"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    closes = [c["close"] for c in candles]
    vols = [c["volume"] for c in candles]
    colors = ["#00d4aa" if c >= o else "#ff4757" for o, c in zip(opens, closes)]

    fig.add_trace(go.Candlestick(
        x=times, open=opens, high=highs, low=lows, close=closes,
        increasing_line_color="#00d4aa", decreasing_line_color="#ff4757",
        increasing_fillcolor="#00d4aa", decreasing_fillcolor="#ff4757",
        name="BTC",
    ), row=1, col=1)

    # Volume bars (normalized to not dominate)
    max_vol = max(vols) if vols else 1
    vol_normalized = [v / max_vol for v in vols]
    fig.add_trace(go.Bar(
        x=times, y=vol_normalized, marker_color=colors, opacity=0.5,
        name="Volume", showlegend=False,
    ), row=2, col=1)

    # Trade markers
    for t in s["bot_trades"]:
        if t["type"] == "ENTRY" and t["price"] > 0:
            mc = "#00ff88" if t["side"] == "BUY" else "#ff4444"
            sym = "triangle-up" if t["side"] == "BUY" else "triangle-down"
            fig.add_trace(go.Scatter(
                x=[times[-1]], y=[t["price"]], mode="markers",
                marker=dict(size=14, color=mc, symbol=sym),
                name=f"{t['side']}", showlegend=True,
            ), row=1, col=1)

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#0a0e17", plot_bgcolor="#0f1419",
        height=450, margin=dict(l=50, r=10, t=10, b=0),
        xaxis_rangeslider_visible=False,
        font=dict(color="#8899aa"),
        legend=dict(bgcolor="rgba(0,0,0,0)", x=0, y=1.05, orientation="h"),
        hovermode="x unified",
    )
    fig.update_xaxes(gridcolor="#1a1f2e", zeroline=False)
    fig.update_yaxes(gridcolor="#1a1f2e", zeroline=False, row=1, col=1)
    fig.update_yaxes(gridcolor="#1a1f2e", zeroline=False, showticklabels=False, row=2, col=1)

    st.plotly_chart(fig, width="stretch", key="candle_chart")

# Microstructure — simple metric cards with color bars
st.markdown("### Microstructure")
g1, g2, g3, g4 = st.columns(4)

adx_val = s["adx"]
mom_val = s["momentum"]
vol_val = s["vol_pct"]
total_vol = s["volume_buy"] + s["volume_sell"]
buy_pct = s["volume_buy"] / total_vol * 100 if total_vol > 0 else 50

def micro_card(col, title, value_str, bar_pct, bar_color, interpretation, key):
    """Simple card with colored progress bar instead of complex gauge."""
    bar_pct = max(0, min(100, bar_pct))
    col.markdown(f"""
    <div style="background:linear-gradient(135deg,#1a1f2e,#141824);border:1px solid #2a3042;
                border-radius:12px;padding:14px;text-align:center;">
        <div style="color:#667;font-size:0.75rem;text-transform:uppercase;letter-spacing:1px;">{title}</div>
        <div style="color:#fff;font-size:1.8rem;font-weight:800;margin:4px 0;">{value_str}</div>
        <div style="background:#1a1f2e;border-radius:6px;height:8px;margin:8px 0;overflow:hidden;">
            <div style="background:{bar_color};height:100%;width:{bar_pct}%;border-radius:6px;
                        transition:width 0.3s;"></div>
        </div>
        <div style="color:#667;font-size:0.78rem;">{interpretation}</div>
    </div>
    """, unsafe_allow_html=True)

# ADX: 0-60, <20 green, 20-30 yellow, >30 red
adx_pct = min(adx_val / 60 * 100, 100)
adx_color = "#00d4aa" if adx_val < 20 else ("#ffa502" if adx_val < 30 else "#ff4757")
adx_text = "Sin tendencia ✓" if adx_val < 20 else ("Tendencia débil" if adx_val < 30 else "Tendencia fuerte ✗")
micro_card(g1, "ADX", f"{adx_val:.1f}", adx_pct, adx_color, adx_text, "adx")

# Momentum: show direction and magnitude
mom_abs = abs(mom_val)
mom_pct = min(mom_abs * 10000 / 200 * 100, 100)
if mom_val > 0.001:
    mom_color, mom_text = "#00d4aa", f"📈 Alcista"
elif mom_val < -0.001:
    mom_color, mom_text = "#ff4757", f"📉 Bajista"
else:
    mom_color, mom_text = "#8899aa", "➡️ Neutral"
micro_card(g2, "Momentum", f"{mom_val:+.4f}", mom_pct, mom_color, mom_text, "mom")

# Vol%: 0-100
vol_pct_display = vol_val * 100
vol_color = "#3b82f6" if vol_val < 0.3 else ("#ffa502" if vol_val < 0.7 else "#ff4757")
vol_text = "Baja — tranquilo" if vol_val < 0.3 else ("Normal" if vol_val < 0.7 else "Alta — cuidado")
micro_card(g3, "Volatilidad", f"{vol_pct_display:.0f}%", vol_pct_display, vol_color, vol_text, "vol")

# Buy pressure: 0-100
buy_color = "#00d4aa" if buy_pct > 55 else ("#ff4757" if buy_pct < 45 else "#ffa502")
buy_text = "🟢 Compradores" if buy_pct > 55 else ("🔴 Vendedores" if buy_pct < 45 else "⚖️ Equilibrado")
micro_card(g4, "Buy Flow", f"{buy_pct:.0f}%", buy_pct, buy_color, buy_text, "buy")

# Signals & Trades side by side
sig_col, trade_col = st.columns(2)

CARD_H = "height:48px;display:flex;align-items:center;"

with sig_col:
    st.markdown(f"### Signals &nbsp; `{len(s['signals_generated'])} entries` `{s['signals_validated']} filled` `{s['signals_blocked']} blocked` `{s['signals_exits']} exits`")

    signals = list(s["signals_generated"])
    items = list(reversed(signals[-8:])) if signals else []
    for sig in items:
        status = sig["status"]
        if status == "validated":
            icon, color = "✅", "#00d4aa"
        elif status == "blocked":
            icon, color = "🚫", "#ff4757"
        else:
            icon, color = "⏳", "#ffa502"
        side_color = "#00d4aa" if sig["side"] == "BUY" else "#ff4757"
        st.markdown(f"""
        <div style="{CARD_H}padding:0 12px;margin:2px 0;border-radius:6px;
                    background:rgba(26,31,46,0.8);border-left:3px solid {color};
                    font-size:0.85rem;justify-content:space-between;">
            <span>{icon} <span style="color:{side_color};font-weight:700;">{sig["side"]}</span>
                <span style="color:#889;">{sig["strategy"][:8]}</span></span>
            <span style="color:#aaa;">${sig["price"]:,.0f}</span>
            <span style="color:#667;">str:{sig["strength"]:.2f}</span>
            <span style="color:#445;">{sig["time"]}</span>
        </div>
        """, unsafe_allow_html=True)
    # Pad to 8 rows
    for _ in range(8 - len(items)):
        st.markdown(f'<div style="{CARD_H}padding:0 12px;margin:2px 0;border-radius:6px;background:rgba(20,24,36,0.4);"><span style="color:#333;">---</span></div>', unsafe_allow_html=True)

with trade_col:
    st.markdown(f"### Trades &nbsp; `{s['total_trades']} total` `{len(s['open_positions'])} open`")
    trades = list(s["bot_trades"])
    curr_price = s["price"]
    trade_items = list(reversed(list(trades)[-8:])) if trades else []
    if trade_items:
        for t in trade_items:
            tp_type = t.get("type", "?")
            status = t.get("status", "")
            if tp_type == "ENTRY" and status == "OPEN":
                icon, color = "🟡", "#ffa502"
            elif tp_type == "ENTRY" and status == "CLOSED":
                icon, color = "⚪", "#666"
            elif tp_type == "EXIT":
                icon, color = ("🟢", "#00d4aa") if t.get("pnl", 0) > 0 else ("🔴", "#ff4757")
            elif tp_type == "TP":
                icon, color = "🟢", "#00d4aa"
            elif tp_type == "SL":
                icon, color = "🔴", "#ff4757"
            else:
                icon, color = "⚪", "#666"

            side_color = "#00d4aa" if t.get("side") == "BUY" else "#ff4757"

            # Calculate floating PnL for open positions
            if status == "OPEN" and t.get("price", 0) > 0 and curr_price > 0:
                size = t.get("size", 0)
                if t.get("side") == "BUY":
                    fpnl = (curr_price - t["price"]) * size
                else:
                    fpnl = (t["price"] - curr_price) * size
                pnl_str = f"${fpnl:+,.2f}"
                pnl_color = "#00d4aa" if fpnl > 0 else "#ff4757"
            elif t.get("pnl") is not None:
                pnl_str = f"${t['pnl']:+,.2f}"
                pnl_color = "#00d4aa" if t["pnl"] > 0 else "#ff4757"
            else:
                pnl_str = ""
                pnl_color = "#555"
            price_str = f"${t['price']:,.0f}" if t.get("price", 0) > 0 else ""
            sl_str = f"SL:${t['sl']:,.0f}" if t.get("sl", 0) > 0 else ""
            tp_str = f"TP:${t['tp']:,.0f}" if t.get("tp", 0) > 0 else ""
            strat_short = t.get("strategy", "")[:6]

            st.markdown(f"""
            <div style="{CARD_H}padding:0 12px;margin:2px 0;border-radius:6px;
                        background:rgba(26,31,46,0.8);border-left:3px solid {color};
                        font-size:0.85rem;justify-content:space-between;">
                <span>{icon} <b style="font-size:0.75rem;">{tp_type}</b>
                    <span style="color:{side_color};font-weight:700;">{t.get('side','')}</span>
                    <span style="color:#445;font-size:0.7rem;">{strat_short}</span></span>
                <span style="color:#aaa;">{price_str}</span>
                <span style="color:#ff4757;font-size:0.75rem;">{sl_str}</span>
                <span style="color:#00d4aa;font-size:0.75rem;">{tp_str}</span>
                <span style="color:{pnl_color};font-weight:700;">{pnl_str}</span>
                <span style="color:#445;font-size:0.75rem;">{t.get('time','')}</span>
            </div>
            """, unsafe_allow_html=True)
    # Pad to 8 rows
    for _ in range(8 - len(trade_items)):
        st.markdown(f'<div style="{CARD_H}padding:0 12px;margin:2px 0;border-radius:6px;background:rgba(20,24,36,0.4);"><span style="color:#333;">---</span></div>', unsafe_allow_html=True)

# Auto refresh
time.sleep(refresh)
st.rerun()
