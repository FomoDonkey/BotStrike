"""Strategy Configuration & AI Analysis."""
import streamlit as st
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

st.set_page_config(page_title="Strategy", page_icon="⚙️", layout="wide")
st.markdown('<style>.stApp{background-color:#0a0e17;}</style>', unsafe_allow_html=True)

st.markdown("# ⚙️ Strategy Configuration")

# Active strategies
st.markdown("### Active Strategies")

col1, col2 = st.columns(2)
with col1:
    st.markdown("""
    <div style="background:#00d4aa15;border:1px solid #00d4aa;border-radius:12px;padding:20px;margin:8px 0;">
        <h3 style="color:#00d4aa;margin:0;">📊 Divergence (MR) — 40%</h3>
        <p style="color:#ccc;margin:8px 0 0 0;">Real RSI+OBV divergence with recovery confirmation on 15m</p>
        <br/>
        <table style="color:#aaa;width:100%;">
            <tr><td><b>LONG</b></td><td>Price lower low + RSI was &lt;30 + RSI recovers &gt;45 + OBV confirms</td></tr>
            <tr><td><b>SHORT</b></td><td>Price higher high + RSI was &gt;70 + RSI falls &lt;55 + OBV confirms</td></tr>
            <tr><td><b>ADX Filter</b></td><td>&lt;35 (weak trend = mean reversion zone)</td></tr>
            <tr><td><b>Lookback</b></td><td>10 bars (2.5 hours at 15m)</td></tr>
            <tr><td><b>SL / TP</b></td><td>1.5x ATR / 3.0x ATR (R:R 1:2)</td></tr>
            <tr><td><b>Dip Confirm</b></td><td>Price must be within 0.5 ATR of swing extreme</td></tr>
            <tr><td><b>Auto-resample</b></td><td>Resamples to 15m if input is 1m (backtester)</td></tr>
        </table>
    </div>
    """, unsafe_allow_html=True)

with col2:
    st.markdown("""
    <div style="background:#a855f715;border:1px solid #a855f7;border-radius:12px;padding:20px;margin:8px 0;">
        <h3 style="color:#a855f7;margin:0;">⚡ Order Flow Momentum — 60%</h3>
        <p style="color:#ccc;margin:8px 0 0 0;">Weighted scoring with macro trend alignment</p>
        <br/>
        <table style="color:#aaa;width:100%;">
            <tr><td><b>OBI</b></td><td>40% — imbalance &gt;0.10 + delta &gt;0.01</td></tr>
            <tr><td><b>Microprice</b></td><td>30% — dynamic threshold (ATR-scaled)</td></tr>
            <tr><td><b>Hawkes</b></td><td>20% — spike &gt;2.5x (3.5x when VPIN high)</td></tr>
            <tr><td><b>Depth</b></td><td>10% — bid/ask ratio &gt;2.0x</td></tr>
            <tr><td><b>Min Score</b></td><td>0.55 (OBI + one more signal minimum)</td></tr>
            <tr><td><b>Trend</b></td><td>4H+1D Binance klines (x1.1 with / x0.3 against)</td></tr>
            <tr><td><b>Filters</b></td><td>VPIN &lt;0.75, spread &lt;15bps, Kyle Lambda &lt;1.5</td></tr>
            <tr><td><b>Exit</b></td><td>Momentum reversal (30s+) / Hawkes fade (60s+) / 3min max</td></tr>
            <tr><td><b>SL / TP</b></td><td>1.5x ATR / 3.0x ATR (R:R 1:2)</td></tr>
        </table>
    </div>
    """, unsafe_allow_html=True)

# Disabled strategies
st.markdown("### Disabled Strategies")
d1, d2 = st.columns(2)
with d1:
    st.markdown("""
    <div style="background:#33333315;border:1px solid #444;border-radius:12px;padding:16px;opacity:0.5;">
        <h4 style="color:#888;margin:0;">📉 Trend Following — OFF</h4>
        <p style="color:#666;font-size:0.85rem;">Breakout loses in all timeframes. Needs ML filter.</p>
    </div>
    """, unsafe_allow_html=True)
with d2:
    st.markdown("""
    <div style="background:#33333315;border:1px solid #444;border-radius:12px;padding:16px;opacity:0.5;">
        <h4 style="color:#888;margin:0;">🏪 Market Making — OFF</h4>
        <p style="color:#666;font-size:0.85rem;">Fees exceed edge with $300 capital.</p>
    </div>
    """, unsafe_allow_html=True)

# System config
st.markdown("### System Configuration")
st.markdown("""
| Parameter | Value |
|-----------|-------|
| **Capital** | $300 |
| **Symbol** | BTC-USD only |
| **Leverage** | 2x (max 5x) |
| **Risk/Trade** | 1.5% ($4.50) |
| **Bar Interval** | 15 minutes |
| **Strategy Eval** | Every 5 seconds |
| **Max Drawdown** | 10% ($30) |
| **Max Exposure** | 60% equity x leverage |
| **Slippage** | 8 bps base (1% notional cap) |
| **Trend Source** | Binance 4H+1D klines (cached 15min) |
| **Cooldown OFM** | 60 seconds after exit |
| **Data Source** | Binance WebSocket (trade + depth + kline) |
""")

# AI Analysis
st.markdown("---")
st.markdown("### 🤖 AI Daily Analysis")
try:
    from core.ai_analyst import AIAnalyst
    analyst = AIAnalyst()
    result = analyst.analyze(
        trades=[], equity=300, initial_capital=300,
        market_state={"regime": "RANGING", "adx": 15, "momentum": 0, "vol_pct": 0.5, "price": 67000, "rsi": 50},
        current_config={"leverage": 2, "sl_mult": 1.5, "tp_mult": 3.0, "rsi_oversold": 30, "rsi_overbought": 70, "adx_max": 35, "risk_pct": 1.5},
    )
    st.info(f"**{result['summary']}**")
    for rec in result.get("recommendations", []):
        st.markdown(f"- {rec}")
    st.caption(f"Risk: {result['risk_assessment']} | Confidence: {result['confidence']:.0%} | Source: {result['source']}")
except Exception as e:
    st.warning(f"AI Analysis unavailable: {e}")
