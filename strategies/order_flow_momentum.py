"""
Order Flow Momentum Strategy — Scalping basado en microestructura.

Redesigned: requires 3/4 signal convergence, trend as scalar (not zero),
coherent 30s-180s hold time, 60s cooldown.

Signals:
  - OBI (Order Book Imbalance): presión direccional del libro
  - Microprice: fair value ajustado por flujo
  - Hawkes: detección de clusters de actividad
  - Depth ratio: balance de profundidad bid/ask

Entry: 3+ signals must converge in same direction + macro trend aligned
Exit: momentum reversal (30s+), hawkes decay (60s+), or max 3min hold
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


class OrderFlowMomentumStrategy(BaseStrategy):
    """Order Flow Momentum: scalp basado en flujo institucional."""

    def __init__(self, trading_config: TradingConfig) -> None:
        super().__init__(StrategyType.ORDER_FLOW_MOMENTUM, trading_config)
        self._entry_timestamps: Dict[str, float] = {}
        self._last_exit_time: Dict[str, float] = {}
        self.COOLDOWN_SEC = 60  # 1 min cooldown (was 5 min — too long for scalper)

    def should_activate(self, regime: MarketRegime) -> bool:
        return regime != MarketRegime.UNKNOWN

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

        if df.empty or len(df) < 20:
            return signals

        import time as _time
        current = df.iloc[-1]
        price = snapshot.price if snapshot.price > 0 else float(current["close"])
        atr = current.get("atr", 0)
        # Always use wall clock for hold time tracking (bar timestamps don't update between bars)
        ts = _time.time()

        if pd.isna(atr) or atr <= 0:
            return signals

        # ── Extract microstructure data ──────────────────────────
        micro = kwargs.get("micro")
        obi = kwargs.get("obi")
        kelly_pct = kwargs.get("kelly_risk_pct")

        if not micro or not obi:
            return signals

        obi_imbalance = obi.weighted_imbalance if obi else 0
        obi_delta = obi.delta if obi else 0

        # Microprice divergence from mid
        microprice_adj_bps = 0
        if snapshot.orderbook and hasattr(snapshot.orderbook, "microprice"):
            mp = snapshot.orderbook.microprice
            mid = snapshot.orderbook.mid_price
            if mp and mid and mid > 0:
                microprice_adj_bps = (mp - mid) / mid * 10000

        # Hawkes
        hawkes_ratio = micro.hawkes.spike_ratio if micro.hawkes else 1.0
        hawkes_count = micro.hawkes.event_count_1m if micro.hawkes else 0

        # VPIN
        vpin = micro.vpin.vpin if micro.vpin else 0

        # ── Exit if position open ────────────────────────────────
        if current_position is not None:
            exit_signal = self._check_exit(
                symbol, price, ts, current_position,
                obi_delta, hawkes_ratio, atr,
            )
            if exit_signal:
                signals.append(exit_signal)
            return signals

        # ── Cooldown ─────────────────────────────────────────────
        last_exit = self._last_exit_time.get(symbol, 0)
        if ts > 0 and last_exit > 0 and (ts - last_exit) < self.COOLDOWN_SEC:
            return signals

        # ── Macro trend (4H + 1D from Binance klines) ────────────
        daily_trend = 0
        trend_info = kwargs.get("trend_info")
        if trend_info is not None:
            daily_trend = trend_info.macro_trend
        else:
            # Fallback for backtest
            if len(df) >= 50:
                ema_short = df["close"].ewm(span=20).mean().iloc[-1]
                ema_long = df["close"].ewm(span=50).mean().iloc[-1]
                if not pd.isna(ema_short) and not pd.isna(ema_long):
                    daily_trend = 1 if ema_short > ema_long else -1

        # ── Quality filters ──────────────────────────────────────
        if vpin > 0.75:
            return signals

        if hawkes_count < 3:
            return signals

        if micro.kyle_lambda and micro.kyle_lambda.is_valid:
            if micro.kyle_lambda.impact_stress > 1.5:
                return signals

        if snapshot.orderbook and snapshot.orderbook.spread_bps > 15:
            return signals

        # ── Weighted signal scoring ───────────────────────────────
        # OBI is strongest predictor (40%), microprice (30%), hawkes (20%), depth (10%)
        long_score = 0.0
        short_score = 0.0
        depth_ratio = obi.depth_ratio if obi and obi.depth_ratio else 1.0

        # Signal 1: OBI direction + momentum (weight: 0.40) — strongest signal
        if obi_imbalance > 0.10 and obi_delta > 0.01:
            long_score += 0.40
        elif obi_imbalance < -0.10 and obi_delta < -0.01:
            short_score += 0.40

        # Signal 2: Microprice divergence (weight: 0.30)
        # Dynamic threshold: scale by volatility (ATR-normalized)
        microprice_threshold = max(1.5, atr / price * 5000) if price > 0 else 2.0
        if microprice_adj_bps > microprice_threshold:
            long_score += 0.30
        elif microprice_adj_bps < -microprice_threshold:
            short_score += 0.30

        # Signal 3: Hawkes spike (weight: 0.20)
        # Higher threshold when VPIN is elevated (noisier environment)
        hawkes_threshold = 2.5 if vpin < 0.4 else 3.5
        if hawkes_ratio > hawkes_threshold and obi_imbalance > 0.05:
            long_score += 0.20
        elif hawkes_ratio > hawkes_threshold and obi_imbalance < -0.05:
            short_score += 0.20

        # Signal 4: Depth ratio (weight: 0.10) — weakest but confirming
        if depth_ratio > 2.0:
            long_score += 0.10
        elif depth_ratio < 0.5:
            short_score += 0.10

        # ── Trend filter as SCALAR ───────────────────────────────
        if daily_trend == 1:
            long_score *= 1.1    # 10% boost aligned
            short_score *= 0.3   # 70% penalty against
        elif daily_trend == -1:
            long_score *= 0.3
            short_score *= 1.1
        # neutral: no change

        # SL/TP: 1.5 ATR stop, 3.0 ATR target (1:2 R:R)
        OFM_SL_MULT = 1.5
        OFM_TP_MULT = 3.0

        # Minimum score: 0.60 = OBI(0.40) + one of microprice(0.30)/hawkes(0.20)
        # With trend boost: 0.60 × 1.1 = 0.66 passes
        # Against trend: 0.60 × 0.3 = 0.18 blocked
        min_effective_score = 0.55

        # ── LONG ─────────────────────────────────────────────────
        if long_score >= min_effective_score and long_score > short_score + 0.10:
            stop_loss = price - OFM_SL_MULT * atr
            take_profit = price + OFM_TP_MULT * atr

            size = self._calc_position_size(
                allocated_capital, price, stop_loss,
                sym_config.leverage, kelly_risk_pct=kelly_pct,
            )
            size_usd = size * price

            strength = min(long_score, 1.0)

            if size_usd > 10:
                self._entry_timestamps[symbol] = ts
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
                        "obi_imbalance": round(obi_imbalance, 3),
                        "obi_delta": round(obi_delta, 3),
                        "microprice_adj_bps": round(microprice_adj_bps, 2),
                        "hawkes_ratio": round(hawkes_ratio, 2),
                        "hawkes_count_1m": hawkes_count,
                        "vpin": round(vpin, 3),
                        "depth_ratio": round(depth_ratio, 2),
                        "long_score": round(long_score, 3),
                        "daily_trend": daily_trend,
                        "trend_4h": trend_info.trend_4h if trend_info else 0,
                        "trend_1d": trend_info.trend_1d if trend_info else 0,
                        "atr": float(atr),
                        "regime": regime.value,
                    },
                ))

        # ── SHORT ────────────────────────────────────────────────
        elif short_score >= min_effective_score and short_score > long_score + 0.10:
            stop_loss = price + OFM_SL_MULT * atr
            take_profit = price - OFM_TP_MULT * atr

            size = self._calc_position_size(
                allocated_capital, price, stop_loss,
                sym_config.leverage, kelly_risk_pct=kelly_pct,
            )
            size_usd = size * price

            strength = min(short_score, 1.0)

            if size_usd > 10:
                self._entry_timestamps[symbol] = ts
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
                        "obi_imbalance": round(obi_imbalance, 3),
                        "obi_delta": round(obi_delta, 3),
                        "microprice_adj_bps": round(microprice_adj_bps, 2),
                        "hawkes_ratio": round(hawkes_ratio, 2),
                        "hawkes_count_1m": hawkes_count,
                        "vpin": round(vpin, 3),
                        "depth_ratio": round(depth_ratio, 2),
                        "short_score": round(short_score, 3),
                        "daily_trend": daily_trend,
                        "trend_4h": trend_info.trend_4h if trend_info else 0,
                        "trend_1d": trend_info.trend_1d if trend_info else 0,
                        "atr": float(atr),
                        "regime": regime.value,
                    },
                ))

        return signals

    def _check_exit(
        self,
        symbol: str,
        price: float,
        ts: float,
        position: Position,
        obi_delta: float,
        hawkes_ratio: float,
        atr: float,
    ) -> Optional[Signal]:
        """Exit logic: quick reversal, hawkes decay, or time limit."""
        entry_ts = self._entry_timestamps.get(symbol, 0)
        hold_time = ts - entry_ts if entry_ts > 0 else 0

        # Exit 1: Momentum reversal (after 30s minimum)
        momentum_reversed = False
        if hold_time >= 30:
            if position.side == Side.BUY and obi_delta < -0.20:
                momentum_reversed = True
            elif position.side == Side.SELL and obi_delta > 0.20:
                momentum_reversed = True

        # Exit 2: Hawkes decay — activity dried up (after 60s)
        hawkes_fading = hawkes_ratio < 0.5

        # Exit 3: Time-based — max 3 minutes
        time_exit = hold_time > 180

        should_exit = (
            momentum_reversed
            or (hawkes_fading and hold_time > 60)
            or time_exit
        )

        if should_exit:
            self._entry_timestamps.pop(symbol, None)
            self._last_exit_time[symbol] = ts
            close_side = Side.SELL if position.side == Side.BUY else Side.BUY
            exit_size = abs(position.size * price) if position.size else 0
            if exit_size <= 0:
                exit_size = position.notional if position.notional > 0 else 100

            exit_reason = "momentum_reversal" if momentum_reversed else (
                "hawkes_fading" if hawkes_fading else "time_exit"
            )

            return Signal(
                strategy=self.strategy_type,
                symbol=symbol,
                side=close_side,
                strength=0.9,
                entry_price=price,
                stop_loss=price,
                take_profit=price,
                size_usd=exit_size,
                metadata={
                    "action": "exit_mean_reversion",
                    "exit_reason": exit_reason,
                    "hold_time_sec": round(hold_time, 1),
                    "obi_delta_at_exit": round(obi_delta, 3),
                    "hawkes_at_exit": round(hawkes_ratio, 2),
                },
            )

        return None
