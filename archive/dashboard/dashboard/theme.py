"""
Tema visual compartido para todos los dashboards.
Colores, estilos de gráficos y funciones de formato reutilizables.
"""
from __future__ import annotations

# ── Paleta de colores ──────────────────────────────────────────────

COLORS = {
    # Estrategias
    "MEAN_REVERSION": "#6C5CE7",     # violeta
    "TREND_FOLLOWING": "#00B894",     # verde esmeralda
    "MARKET_MAKING": "#FDCB6E",      # amarillo dorado
    # Señales
    "BUY": "#00B894",
    "SELL": "#E17055",
    "LONG": "#00B894",
    "SHORT": "#E17055",
    # Regímenes
    "RANGING": "#74B9FF",            # azul claro
    "TRENDING_UP": "#00B894",
    "TRENDING_DOWN": "#E17055",
    "BREAKOUT": "#E84393",           # rosa fuerte
    "UNKNOWN": "#636E72",
    # General
    "profit": "#00B894",
    "loss": "#E17055",
    "neutral": "#636E72",
    "bg_dark": "#0E1117",
    "bg_card": "#1E2130",
    "text": "#FAFAFA",
    "text_dim": "#A0A0A0",
    "accent": "#6C5CE7",
    "warning": "#FDCB6E",
    "danger": "#E17055",
    "equity_line": "#74B9FF",
    "drawdown_fill": "#E17055",
    "grid": "#2D3436",
    # Microestructura
    "vpin": "#E84393",              # rosa
    "hawkes": "#FF7675",            # rojo coral
    "as_spread": "#00CEC9",         # cyan
}

# ── Layout de Plotly compartido ────────────────────────────────────

PLOTLY_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(color=COLORS["text"], size=12),
    margin=dict(l=40, r=20, t=40, b=30),
    xaxis=dict(gridcolor=COLORS["grid"], zeroline=False),
    yaxis=dict(gridcolor=COLORS["grid"], zeroline=False),
    legend=dict(
        bgcolor="rgba(0,0,0,0)",
        font=dict(size=11),
    ),
    hoverlabel=dict(
        bgcolor=COLORS["bg_card"],
        font_size=12,
    ),
)


def format_usd(value: float, decimals: int = 2) -> str:
    """Formatea un valor como USD."""
    if abs(value) >= 1_000_000:
        return f"${value / 1_000_000:,.{decimals}f}M"
    if abs(value) >= 1_000:
        return f"${value:,.{decimals}f}"
    return f"${value:.{decimals}f}"


def format_pct(value: float, decimals: int = 2) -> str:
    """Formatea un valor como porcentaje."""
    return f"{value * 100:.{decimals}f}%"


def format_delta(value: float) -> str:
    """Formatea un delta con signo y color."""
    if value >= 0:
        return f"+${value:,.2f}"
    return f"-${abs(value):,.2f}"


def pnl_color(value: float) -> str:
    """Retorna color basado en si el valor es positivo o negativo."""
    if value > 0:
        return COLORS["profit"]
    if value < 0:
        return COLORS["loss"]
    return COLORS["neutral"]


def regime_emoji(regime: str) -> str:
    """Retorna indicador visual para un régimen."""
    mapping = {
        "RANGING": "↔ Lateral",
        "TRENDING_UP": "↑ Tendencia Alcista",
        "TRENDING_DOWN": "↓ Tendencia Bajista",
        "BREAKOUT": "⚡ Breakout",
        "UNKNOWN": "? Desconocido",
    }
    return mapping.get(regime, regime)


def strategy_icon(strategy: str) -> str:
    """Retorna indicador visual para una estrategia."""
    mapping = {
        "MEAN_REVERSION": "↩ Mean Reversion",
        "TREND_FOLLOWING": "➜ Trend Following",
        "MARKET_MAKING": "⇄ Market Making",
    }
    return mapping.get(strategy, strategy)
