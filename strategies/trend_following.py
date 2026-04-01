"""
Estrategia Trend Following — Rediseñada para datos reales de crypto 1m.

Señales de entrada basadas en:
  1. Breakout de N-bar high/low (20 barras) — captura inicio de tendencia
  2. ADX > 20 + DI directional confirmation — confirma fuerza
  3. Hawkes spike + OBI dominance — confirma flujo institucional

Trailing stop adaptativo basado en ATR.
TP a 2x risk (realista para 1m).
"""
from __future__ import annotations
from typing import Dict, List, Optional

import pandas as pd

from config.settings import SymbolConfig, TradingConfig
from core.types import (
    Signal, MarketRegime, MarketSnapshot, StrategyType, Side, Position,
)
from strategies.base import BaseStrategy
import structlog

logger = structlog.get_logger(__name__)


class TrendFollowingStrategy(BaseStrategy):
    """Trend Following: breakout + ADX + microestructura."""

    def __init__(self, trading_config: TradingConfig) -> None:
        super().__init__(StrategyType.TREND_FOLLOWING, trading_config)
        self._trailing_stops: Dict[str, float] = {}

    def should_activate(self, regime: MarketRegime) -> bool:
        """Desactivada — breakout pierde en todos los timeframes (1m/5m/15m/1h) en crypto.
        Se reactivará cuando ML filter tenga 50+ trades para filtrar señales.
        """
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
        **kwargs,
    ) -> List[Signal]:
        signals: List[Signal] = []

        if df.empty or len(df) < 25:
            return signals

        current = df.iloc[-1]
        prev = df.iloc[-2]
        price = snapshot.price if snapshot.price > 0 else float(current["close"])
        atr = current.get("atr", 0)
        adx = current.get("adx_5m", current.get("adx", 0))  # 5m ADX para confirmación
        momentum = current.get("momentum_20", 0)

        if pd.isna(atr) or atr <= 0 or pd.isna(adx):
            return signals

        # Filtro VPIN: no entrar si flujo es extremadamente tóxico
        micro = kwargs.get("micro")
        if micro and micro.vpin and micro.vpin.vpin > 0.85:
            return signals

        # N-bar breakout levels (5m timeframe para reducir ruido)
        high_20 = current.get("high_20_5m", current.get("high_20", 0))
        low_20 = current.get("low_20_5m", current.get("low_20", float("inf")))
        prev_high_20 = prev.get("high_20_5m", prev.get("high_20", 0))
        prev_low_20 = prev.get("low_20_5m", prev.get("low_20", float("inf")))

        # DI+/DI- directional confirmation
        plus_di = current.get("plus_di", 0)
        minus_di = current.get("minus_di", 0)

        # Microestructura
        micro = kwargs.get("micro")
        hawkes_ratio = micro.hawkes.spike_ratio if micro else 1.0

        # OBI
        obi_result = kwargs.get("obi")
        obi_imbalance = obi_result.weighted_imbalance if obi_result else 0.0

        kelly_pct = kwargs.get("kelly_risk_pct")

        # ── Trailing stop para posición existente ─────────────────
        if current_position is not None:
            exit_signal = self._manage_trailing_stop(
                symbol, price, atr, current_position, sym_config
            )
            if exit_signal:
                signals.append(exit_signal)
                return signals
            return signals  # no nuevas entradas si ya hay posición

        # Limpiar trailing stop stale
        self._trailing_stops.pop(symbol, None)

        # ── Señales de entrada ────────────────────────────────────

        # Breakout detection: precio rompe el high/low de las últimas 20 barras
        # Usamos prev_high_20 para evitar contar la propia barra actual
        breakout_long = (
            price > prev_high_20 > 0
            and adx > 25
            and plus_di > minus_di  # DI+ confirma dirección alcista
        )
        breakout_short = (
            price < prev_low_20 < float("inf")
            and prev_low_20 > 0
            and adx > 25
            and minus_di > plus_di  # DI- confirma dirección bajista
        )

        # Momentum confirmation (relajado)
        if not pd.isna(momentum):
            if breakout_long and momentum <= 0:
                breakout_long = False
            if breakout_short and momentum >= 0:
                breakout_short = False

        # ── LONG ──────────────────────────────────────────────────
        if breakout_long:
            # Strength: ADX normalizado + Hawkes boost + OBI boost
            strength = min(adx / 50.0, 0.8)
            if hawkes_ratio > 1.5:
                strength = min(strength + 0.15, 1.0)
            if obi_imbalance > 0.1:
                strength = min(strength + obi_imbalance * 0.15, 1.0)

            stop_loss = price - sym_config.tf_atr_mult_trail * atr
            take_profit = price + 1.5 * (price - stop_loss)

            adjusted_capital = allocated_capital * max(strength, 0.3)
            size = self._calc_position_size(
                adjusted_capital, price, stop_loss, sym_config.leverage,
                kelly_risk_pct=kelly_pct,
            )
            size_usd = size * price

            if size_usd > 10:
                self._trailing_stops[symbol] = stop_loss
                signals.append(Signal(
                    strategy=self.strategy_type,
                    symbol=symbol,
                    side=Side.BUY,
                    strength=strength,
                    entry_price=price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    size_usd=size_usd,
                    metadata={
                        "trigger": "breakout_high_20",
                        "adx": float(adx),
                        "plus_di": float(plus_di),
                        "minus_di": float(minus_di),
                        "momentum": float(momentum) if not pd.isna(momentum) else 0,
                        "hawkes_ratio": float(hawkes_ratio),
                        "obi": round(obi_imbalance, 3),
                        "atr": float(atr),
                        "prev_high_20": float(prev_high_20),
                    },
                ))

        # ── SHORT ─────────────────────────────────────────────────
        elif breakout_short:
            strength = min(adx / 50.0, 0.8)
            if hawkes_ratio > 1.5:
                strength = min(strength + 0.15, 1.0)
            if obi_imbalance < -0.1:
                strength = min(strength + abs(obi_imbalance) * 0.15, 1.0)

            stop_loss = price + sym_config.tf_atr_mult_trail * atr
            take_profit = price - 1.5 * (stop_loss - price)

            adjusted_capital = allocated_capital * max(strength, 0.3)
            size = self._calc_position_size(
                adjusted_capital, price, stop_loss, sym_config.leverage,
                kelly_risk_pct=kelly_pct,
            )
            size_usd = size * price

            if size_usd > 10:
                self._trailing_stops[symbol] = stop_loss
                signals.append(Signal(
                    strategy=self.strategy_type,
                    symbol=symbol,
                    side=Side.SELL,
                    strength=strength,
                    entry_price=price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    size_usd=size_usd,
                    metadata={
                        "trigger": "breakout_low_20",
                        "adx": float(adx),
                        "plus_di": float(plus_di),
                        "minus_di": float(minus_di),
                        "momentum": float(momentum) if not pd.isna(momentum) else 0,
                        "hawkes_ratio": float(hawkes_ratio),
                        "obi": round(obi_imbalance, 3),
                        "atr": float(atr),
                        "prev_low_20": float(prev_low_20),
                    },
                ))

        return signals

    def _manage_trailing_stop(
        self,
        symbol: str,
        price: float,
        atr: float,
        position: Position,
        sym_config: SymbolConfig,
    ) -> Optional[Signal]:
        """Gestiona trailing stop adaptativo."""
        current_stop = self._trailing_stops.get(symbol)
        if current_stop is None:
            if position.side == Side.BUY:
                self._trailing_stops[symbol] = price - sym_config.tf_atr_mult_trail * atr
            else:
                self._trailing_stops[symbol] = price + sym_config.tf_atr_mult_trail * atr
            return None

        trail_distance = sym_config.tf_atr_mult_trail * atr

        if position.side == Side.BUY:
            new_stop = max(current_stop, price - trail_distance)
            self._trailing_stops[symbol] = new_stop

            if price <= new_stop:
                del self._trailing_stops[symbol]
                exit_size = abs(position.size * price) if position.size else 0
                if exit_size <= 0:
                    exit_size = position.notional if position.notional > 0 else 100
                return Signal(
                    strategy=self.strategy_type,
                    symbol=symbol,
                    side=Side.SELL,
                    strength=1.0,
                    entry_price=price,
                    stop_loss=price,
                    take_profit=price,
                    size_usd=exit_size,
                    metadata={"action": "trailing_stop_hit", "stop_price": new_stop},
                )
        else:
            new_stop = min(current_stop, price + trail_distance)
            self._trailing_stops[symbol] = new_stop

            if price >= new_stop:
                del self._trailing_stops[symbol]
                exit_size = abs(position.size * price) if position.size else 0
                if exit_size <= 0:
                    exit_size = position.notional if position.notional > 0 else 100
                return Signal(
                    strategy=self.strategy_type,
                    symbol=symbol,
                    side=Side.BUY,
                    strength=1.0,
                    entry_price=price,
                    stop_loss=price,
                    take_profit=price,
                    size_usd=exit_size,
                    metadata={"action": "trailing_stop_hit", "stop_price": new_stop},
                )

        return None
