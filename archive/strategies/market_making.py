"""
Estrategia Market Making con modelo Avellaneda-Stoikov mejorado.
Coloca órdenes Buy/Sell en spread dinámico, ajusta inventario según posición.
Detiene operación si surge tendencia fuerte.

Integración de microestructura:
    - VPIN alto → gamma se incrementa → spread más ancho (protección adverse selection)
    - Hawkes spike → gamma se incrementa → spread más ancho o pausa completa
    - Motor A-S mejorado con horizonte adaptativo, skew suave (tanh), bounds de spread
    - Si VPIN >= 0.8 Y Hawkes spike → pausa completa de Market Making
"""
from __future__ import annotations
from typing import List, Optional

import pandas as pd

from config.settings import SymbolConfig, TradingConfig
from core.types import (
    Signal, MarketRegime, MarketSnapshot, StrategyType, Side, Position,
)
from core.microstructure import (
    MicrostructureSnapshot, AvellanedaStoikovEngine,
)
from strategies.base import BaseStrategy
import structlog

logger = structlog.get_logger(__name__)


class MarketMakingStrategy(BaseStrategy):
    """Market Making: captura spread bid-ask con gestión de inventario A-S mejorado."""

    def __init__(self, trading_config: TradingConfig) -> None:
        super().__init__(StrategyType.MARKET_MAKING, trading_config)
        # El motor A-S se inicializa per-symbol desde MicrostructureEngine
        # pero mantenemos uno local como fallback
        self._fallback_as = AvellanedaStoikovEngine()

    def should_activate(self, regime: MarketRegime) -> bool:
        """Desactivada — no es rentable con $300 de capital (fees > edge)."""
        return False

    def generate_signals(
        self,
        symbol: str,
        df: pd.DataFrame,
        snapshot: MarketSnapshot,
        regime: MarketRegime,
        sym_config: SymbolConfig,
        allocated_capital: float,
        current_position: Optional[Position],
        micro: Optional[MicrostructureSnapshot] = None,
        **kwargs,
    ) -> List[Signal]:
        """Genera señales de Market Making.

        Args:
            micro: Snapshot de microestructura (VPIN, Hawkes, A-S).
                   Si es None, usa el cálculo A-S básico como fallback.
        """
        signals: List[Signal] = []

        if snapshot is None or snapshot.orderbook is None:
            return signals

        ob = snapshot.orderbook
        if ob.mid_price is None or ob.mid_price == 0:
            return signals

        mid_price = ob.mid_price
        # Usar microprice como fair value si disponible (superior al mid_price)
        microprice = ob.microprice
        fair_value = microprice if microprice and microprice > 0 else mid_price
        price = snapshot.price if snapshot.price > 0 else fair_value

        if price <= 0:
            return signals

        if df.empty or len(df) < 20:
            return signals

        current = df.iloc[-1]
        atr = current.get("atr", 0)
        if pd.isna(atr) or atr == 0:
            return signals

        # ── Verificar filtros de microestructura ──────────────────
        vpin_val = 0.0
        hawkes_spike = False
        hawkes_ratio = 0.0

        if micro is not None:
            vpin_val = micro.vpin.vpin
            hawkes_spike = micro.hawkes.is_spike
            hawkes_ratio = micro.hawkes.spike_ratio

            # PAUSA COMPLETA: solo en condiciones extremas
            if vpin_val >= 0.9 and hawkes_ratio >= 4.0:
                logger.warning(
                    "mm_paused_microstructure", symbol=symbol,
                    vpin=round(vpin_val, 3), hawkes_ratio=round(hawkes_ratio, 2),
                )
                return signals

        # Volatilidad
        returns = df["close"].pct_change().dropna()
        sigma = float(returns.tail(100).std()) if len(returns) > 10 else 0.01
        if sigma <= 0:
            sigma = 0.01  # fallback para evitar A-S con sigma=0

        # Inventario actual
        inventory = 0.0
        if current_position is not None:
            if current_position.side == Side.BUY:
                inventory = current_position.size
            else:
                inventory = -current_position.size

        max_inventory = (
            sym_config.mm_inventory_limit * sym_config.max_position_usd / price
        )

        # ── Calcular precios A-S ──────────────────────────────────
        # Preparar VPIN y Hawkes results para el motor A-S
        vpin_result = micro.vpin if micro else None
        hawkes_result = micro.hawkes if micro else None

        as_engine = self._fallback_as
        as_result = as_engine.compute(
            mid_price=fair_value,  # Microprice como fair value para A-S
            inventory=inventory,
            max_inventory=max_inventory,
            sigma=sigma,
            atr=float(atr),
            time_remaining=0.5,
            vpin=vpin_result,
            hawkes=hawkes_result,
        )

        bid_price = as_result.bid_price
        ask_price = as_result.ask_price
        spread = as_result.optimal_spread
        reservation_price = as_result.reservation_price

        # Guard: si A-S devolvió resultado vacío (sigma=0, mid=0), no generar señales
        if bid_price <= 0 or ask_price <= 0 or spread <= 0:
            return signals
        inventory_ratio = inventory / max_inventory if max_inventory > 0 else 0

        # ── OBI skew: desplazar fair value segun presion del book ────
        # OBI > 0 (buy pressure) → fair value sube → ambos precios suben
        # Esto hace que el bid sea mas agresivo (mas caro = mas prob de fill)
        # y el ask sea menos favorable para vendedores (higher ask)
        obi_result = kwargs.get("obi")
        obi_imbalance = obi_result.weighted_imbalance if obi_result else 0.0
        if abs(obi_imbalance) > 0.15:
            obi_skew = obi_imbalance * float(atr) * 0.3
            bid_price += obi_skew
            ask_price += obi_skew

        # ── Generar señales por nivel ─────────────────────────────
        order_size_usd = sym_config.mm_order_size_usd

        # Reducir tamaño si microestructura indica riesgo
        if micro and micro.risk_score > 0.5:
            size_reduction = 1.0 - micro.risk_score * 0.4  # reduce hasta 40%
            order_size_usd *= max(size_reduction, 0.5)

        for level in range(sym_config.mm_order_levels):
            level_offset = level * spread * 0.5

            # BID (compra) — solo si no tenemos demasiado inventario long
            if inventory < max_inventory:
                bid = bid_price - level_offset
                stop_loss = max(bid - 2.0 * float(atr), bid * 0.95)  # Floor: -5%
                take_profit = bid + spread

                signals.append(Signal(
                    strategy=self.strategy_type,
                    symbol=symbol,
                    side=Side.BUY,
                    strength=max(0.01, 0.5 * (1.0 - abs(inventory_ratio))),
                    entry_price=bid,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    size_usd=order_size_usd,
                    metadata={
                        "action": "mm_bid",
                        "level": level,
                        "reservation_price": reservation_price,
                        "optimal_spread": spread,
                        "spread_bps": as_result.spread_bps,
                        "inventory": inventory,
                        "inventory_ratio": inventory_ratio,
                        "sigma": sigma,
                        "effective_gamma": as_result.effective_gamma,
                        "vpin": vpin_val,
                        "hawkes_ratio": hawkes_ratio,
                        "obi": round(obi_imbalance, 3),
                        "kyle_lambda": micro.kyle_lambda.kyle_lambda_ema if (micro and micro.kyle_lambda and micro.kyle_lambda.is_valid) else 0.0,
                    },
                ))

            # ASK (venta) — solo si no tenemos demasiado inventario short
            if inventory > -max_inventory:
                ask = ask_price + level_offset
                stop_loss = ask + 2.0 * float(atr)  # Ceil: +5% max
                stop_loss = min(stop_loss, ask * 1.05)
                take_profit = ask - spread

                signals.append(Signal(
                    strategy=self.strategy_type,
                    symbol=symbol,
                    side=Side.SELL,
                    strength=max(0.01, 0.5 * (1.0 - abs(inventory_ratio))),
                    entry_price=ask,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    size_usd=order_size_usd,
                    metadata={
                        "action": "mm_ask",
                        "level": level,
                        "reservation_price": reservation_price,
                        "optimal_spread": spread,
                        "spread_bps": as_result.spread_bps,
                        "inventory": inventory,
                        "inventory_ratio": inventory_ratio,
                        "sigma": sigma,
                        "effective_gamma": as_result.effective_gamma,
                        "vpin": vpin_val,
                        "hawkes_ratio": hawkes_ratio,
                        "obi": round(obi_imbalance, 3),
                        "kyle_lambda": micro.kyle_lambda.kyle_lambda_ema if (micro and micro.kyle_lambda and micro.kyle_lambda.is_valid) else 0.0,
                    },
                ))

        if signals:
            logger.info(
                "mm_signals", symbol=symbol, num_signals=len(signals),
                mid=round(mid_price, 2), bid=round(bid_price, 2),
                ask=round(ask_price, 2), spread_bps=round(as_result.spread_bps, 2),
                inventory=round(inventory, 6),
                vpin=round(vpin_val, 3), hawkes=round(hawkes_ratio, 2),
                gamma_eff=round(as_result.effective_gamma, 4),
            )

        return signals
