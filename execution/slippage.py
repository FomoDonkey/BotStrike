"""
Dynamic Slippage Model — Calcula slippage realista basado en condiciones de mercado.

Modelo de 7 componentes:
  1. Spread component: half-spread como base (no un numero magico)
  2. Size impact: concavo (sqrt) — trades grandes mueven menos que lineal
  3. Volatility scaling: ATR normaliza por volatilidad del activo
  4. Liquidity impact: Hawkes spike amplifica slippage
  5. Regime multiplier: BREAKOUT = mas slippage
  6. OBI adverse selection: imbalance contra tu orden = peor fill
  7. Empirical calibration: ajusta con datos reales si disponibles

Usado por: backtester, paper_simulator, execution_engine.

Uso:
    slip = compute_slippage(
        base_bps=2.0, price=50000, size_usd=5000,
        book_depth_usd=100000, hawkes_ratio=1.5, regime="BREAKOUT"
    )
    fill_price = price + slip  # para BUY
    fill_price = price - slip  # para SELL

    # Modelo avanzado:
    slip = compute_slippage_advanced(
        price=50000, size_usd=5000, spread_bps=3.0,
        book_depth_usd=100000, atr_bps=20.0, ...
    )
"""
from __future__ import annotations

# Multiplicadores de slippage por regimen de mercado
REGIME_SLIPPAGE_MULT = {
    "RANGING": 0.8,       # mercado lateral → spreads tight
    "TRENDING_UP": 1.0,   # normal
    "TRENDING_DOWN": 1.0,
    "BREAKOUT": 2.0,      # alta volatilidad → spreads amplios
    "UNKNOWN": 1.2,       # conservador
}


def compute_slippage(
    base_bps: float,
    price: float,
    size_usd: float = 0.0,
    book_depth_usd: float = 0.0,
    hawkes_ratio: float = 1.0,
    regime: str = "",
    atr: float = 0.0,
) -> float:
    """Calcula slippage dinamico en USD (modelo legacy compatible).

    Args:
        base_bps: Slippage base en basis points (de TradingConfig)
        price: Precio actual del activo
        size_usd: Tamano del trade en USD
        book_depth_usd: Profundidad del orderbook en USD (top 10 levels)
                        Si 0, se usa solo base + regime
        hawkes_ratio: Ratio de intensidad de Hawkes (1.0 = normal)
        regime: Regimen de mercado actual (para multiplicador)
        atr: ATR actual (para volatility impact)

    Returns:
        Slippage en USD (siempre positivo, sumar a BUY, restar a SELL)
    """
    if price <= 0:
        return 0.0

    # 1. Base slippage
    slip_bps = base_bps

    # 2. Regime multiplier
    regime_mult = REGIME_SLIPPAGE_MULT.get(regime, 1.0)
    slip_bps *= regime_mult

    # 3. Size impact: si el trade es grande relativo al book, mas slippage
    if book_depth_usd > 0 and size_usd > 0:
        # Impact proporcional: 0 si size=0, duplica si size=depth
        size_ratio = min(size_usd / book_depth_usd, 2.0)
        slip_bps += base_bps * size_ratio
    elif size_usd > 0 and atr > 0 and price > 0:
        # Fallback sin depth: usar ATR como proxy de liquidez
        # Ratio: size_usd / (ATR-equivalente en USD * factor)
        atr_notional = atr * (size_usd / price)  # ATR * units = price impact proxy
        if atr_notional <= 0:
            atr_notional = 1.0
        if atr_notional > 0:
            impact_ratio = min(size_usd / (atr_notional * 50), 2.0)
            slip_bps += base_bps * impact_ratio

    # 4. Hawkes impact: actividad anomala → mas slippage
    if hawkes_ratio > 1.5:
        hawkes_extra = min((hawkes_ratio - 1.0) * 0.5, 3.0) * base_bps
        slip_bps += hawkes_extra

    slip_usd = slip_bps * price / 10_000

    # Cap slippage as % of trade notional (prevents absurd slippage on micro-trades)
    # Max slippage = 1% of trade size for any single trade
    if size_usd > 0:
        max_slip = size_usd * 0.01  # 1% of notional
        slip_usd = min(slip_usd, max_slip)

    return slip_usd


def compute_slippage_bps(
    base_bps: float,
    size_usd: float = 0.0,
    book_depth_usd: float = 0.0,
    hawkes_ratio: float = 1.0,
    regime: str = "",
    atr: float = 0.0,
    price: float = 0.0,
) -> float:
    """Version que retorna slippage en bps (para tracking/analytics)."""
    if price <= 0:
        return base_bps
    slip_usd = compute_slippage(base_bps, price, size_usd, book_depth_usd,
                                 hawkes_ratio, regime, atr)
    return slip_usd / price * 10_000


# ======================================================================
# MODELO AVANZADO — Usado por smart_router y paper_simulator mejorado
# ======================================================================

import math


def compute_slippage_advanced(
    price: float,
    size_usd: float,
    spread_bps: float = 0.0,
    book_depth_usd: float = 0.0,
    atr_bps: float = 0.0,
    hawkes_ratio: float = 1.0,
    regime: str = "",
    obi_against: float = 0.0,
    vpin: float = 0.0,
    is_market_order: bool = True,
    empirical_avg_bps: float = 0.0,
    kyle_lambda_bps: float = 0.0,
) -> float:
    """Modelo avanzado de slippage con 7 componentes.

    Args:
        price: Precio actual
        size_usd: Tamano en USD
        spread_bps: Spread actual del orderbook en bps
        book_depth_usd: Profundidad del book en el lado relevante
        atr_bps: ATR en basis points (volatilidad)
        hawkes_ratio: Ratio de Hawkes (1.0 = normal)
        regime: Regimen de mercado
        obi_against: OBI contra la direccion de la orden (0 a 1)
                     e.g., si BUY y obi < 0 (sell pressure), obi_against = abs(obi)
        vpin: VPIN actual (0-1)
        is_market_order: True para market orders, False para limit
        empirical_avg_bps: Promedio empirico de slippage medido (0 = no disponible)

    Returns:
        Slippage estimado en USD (siempre positivo)
    """
    if price <= 0:
        return 0.0

    # ── 1. Spread component ──────────────────────────────────────
    # Para market orders: half-spread como baseline (cruzas el spread)
    # Para limit orders: puede ser 0 o negativo (price improvement)
    if is_market_order:
        spread_component = max(spread_bps / 2, 0.5)  # Minimo 0.5 bps
    else:
        spread_component = 0.0  # Limit orders no cruzan el spread

    # ── 2. Size impact (concavo — sqrt model) ────────────────────
    # Almgren-Chriss style: impact ~ sigma * sqrt(size/ADV)
    # Simplificado: impact_bps ~ sqrt(size_ratio) * base_factor
    size_impact = 0.0
    if book_depth_usd > 0 and size_usd > 0:
        size_ratio = size_usd / book_depth_usd
        # sqrt impact: $10k en $100k depth → sqrt(0.1) * 3 = ~0.95 bps
        size_impact = math.sqrt(min(size_ratio, 4.0)) * 3.0
    elif size_usd > 0 and atr_bps > 0 and price > 0:
        # Fallback: ratio de tamano vs volatilidad como proxy de liquidez
        # size_in_units / volatility_in_units = "cuantos ATRs de impacto"
        size_units = size_usd / price
        atr_units = atr_bps * price / 10_000
        if atr_units > 0:
            vol_normalized_size = min(size_units / (atr_units * 100), 2.0)
            size_impact = math.sqrt(vol_normalized_size) * 3.0

    # ── 3. Volatility scaling ────────────────────────────────────
    vol_scale = 1.0
    if atr_bps > 0:
        # ATR 10bps = low vol (0.7x), 20bps = normal (1.0x), 40bps = high (1.4x)
        vol_scale = 0.5 + min(atr_bps / 20.0, 2.0) * 0.5

    # ── 4. Hawkes liquidity impact ───────────────────────────────
    hawkes_impact = 0.0
    if hawkes_ratio > 1.5:
        hawkes_impact = min((hawkes_ratio - 1.0) * 0.8, 4.0)

    # ── 5. Regime multiplier ─────────────────────────────────────
    regime_mult = REGIME_SLIPPAGE_MULT.get(regime, 1.0)

    # ── 6. OBI adverse selection ─────────────────────────────────
    # Si hay presion CONTRA tu orden, el slippage es mayor
    # (adverse selection — los informados estan del otro lado)
    adverse_selection = 0.0
    if obi_against > 0.1:
        adverse_selection = obi_against * 2.0  # Hasta +2 bps en imbalance extremo

    # ── 7. VPIN toxicity premium ─────────────────────────────────
    toxicity_premium = 0.0
    if vpin > 0.4:
        toxicity_premium = (vpin - 0.4) * 3.0  # Hasta +1.8 bps en VPIN=1.0

    # ── 8. Kyle Lambda permanent impact ──────────────────────────
    # Impacto permanente estimado: lambda * sqrt(size/depth)
    permanent_impact = 0.0
    if kyle_lambda_bps > 0 and book_depth_usd > 0 and size_usd > 0:
        size_ratio = size_usd / book_depth_usd
        permanent_impact = kyle_lambda_bps * math.sqrt(min(size_ratio, 4.0))

    # ── Combinar ────────────────────────────────────────────────
    total_bps = (
        (spread_component + size_impact + hawkes_impact + adverse_selection + toxicity_premium)
        * vol_scale
        * regime_mult
        + permanent_impact  # Permanent impact se suma directamente (ya normalizado)
    )

    # ── Calibracion empirica ────────────────────────────────────
    # Si tenemos datos reales, blend con el modelo teorico
    if empirical_avg_bps > 0:
        # 60% empirico + 40% modelo (empirico es mas confiable)
        total_bps = 0.6 * empirical_avg_bps + 0.4 * total_bps

    # Floor: minimo 0.5 bps para market orders
    if is_market_order:
        total_bps = max(total_bps, 0.5)

    slip_usd = total_bps * price / 10_000

    # Cap slippage as % of trade notional (prevents absurd slippage on micro-trades)
    if size_usd > 0:
        max_slip = size_usd * 0.01  # 1% of notional
        slip_usd = min(slip_usd, max_slip)

    return slip_usd
