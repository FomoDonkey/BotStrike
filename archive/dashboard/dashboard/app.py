"""BotStrike Dashboard — Hub principal."""
import streamlit as st

st.set_page_config(
    page_title="BotStrike Trading",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Complete dark theme CSS + sidebar styling
st.markdown("""
<style>
    /* Main background */
    .stApp { background-color: #0a0e17; }

    /* Sidebar */
    section[data-testid="stSidebar"] {
        background: linear-gradient(180deg, #0f1419 0%, #0a0e17 100%);
        border-right: 1px solid #1a1f2e;
    }
    section[data-testid="stSidebar"] > div { padding-top: 1rem; }

    /* Sidebar nav links */
    [data-testid="stSidebarNav"] {
        padding: 0 0.5rem;
    }
    [data-testid="stSidebarNav"] li {
        margin: 2px 0;
    }
    [data-testid="stSidebarNav"] a {
        display: flex;
        align-items: center;
        padding: 10px 16px;
        border-radius: 10px;
        color: #8899aa !important;
        font-weight: 500;
        font-size: 0.95rem;
        transition: all 0.2s;
        text-decoration: none !important;
        border: 1px solid transparent;
    }
    [data-testid="stSidebarNav"] a:hover {
        background: rgba(0, 212, 170, 0.08);
        color: #00d4aa !important;
        border-color: rgba(0, 212, 170, 0.2);
    }
    [data-testid="stSidebarNav"] a[aria-selected="true"] {
        background: rgba(0, 212, 170, 0.12);
        color: #00d4aa !important;
        border-color: #00d4aa;
        font-weight: 700;
    }
    [data-testid="stSidebarNav"] a span {
        margin-right: 8px;
    }

    /* Hide default streamlit elements */
    #MainMenu { visibility: hidden; }
    header { visibility: hidden; }
    footer { visibility: hidden; }

    /* Metrics */
    [data-testid="stMetricValue"] { font-size: 1.8rem; }
    [data-testid="stMetricDelta"] { font-size: 1rem; }

    /* General */
    div[data-testid="stHorizontalBlock"] { gap: 0.5rem; }
    .big-price { font-size: 3.5rem; font-weight: 800; text-align: center; margin: 0; }
</style>
""", unsafe_allow_html=True)

# Landing page
st.markdown("""
<div style="text-align:center;padding:60px 0 30px 0;">
    <h1 style="color:#00d4aa;font-size:3.5rem;font-weight:900;margin:0;">⚡ BotStrike</h1>
    <p style="color:#667;font-size:1.2rem;margin-top:8px;">Real-Time Quantitative Trading</p>
</div>
""", unsafe_allow_html=True)

c1, c2, c3, c4 = st.columns(4)
pages = [
    (c1, "📊", "Live Trading", "Charts, price, microstructure", "#00d4aa"),
    (c2, "📈", "Performance", "Trades, PnL, equity, stats", "#3b82f6"),
    (c3, "🐋", "Order Flow", "Whales, buy/sell pressure", "#ffa502"),
    (c4, "⚙️", "Strategy", "Config, AI analysis", "#a855f7"),
]
for col, icon, title, desc, color in pages:
    col.markdown(f"""
    <div style="background:linear-gradient(135deg,#1a1f2e,#141824);border:1px solid #2a3042;
                border-radius:16px;padding:30px 20px;text-align:center;
                border-top:3px solid {color};">
        <div style="font-size:2.5rem;">{icon}</div>
        <div style="color:#ddd;font-weight:700;font-size:1.1rem;margin:8px 0;">{title}</div>
        <div style="color:#556;font-size:0.85rem;">{desc}</div>
    </div>
    """, unsafe_allow_html=True)

st.markdown("""
<div style="text-align:center;padding:30px 0;color:#334;">
    <p>Select a page from the sidebar to begin</p>
</div>
""", unsafe_allow_html=True)
