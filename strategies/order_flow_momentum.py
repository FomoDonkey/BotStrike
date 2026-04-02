"""
Order Flow Momentum Strategy — Microstructure scalping.

Entry: Weighted convergence of OBI + Microprice + Hawkes + Depth.
Exit: Setup invalidation (score drops below threshold), trailing
      microprice stop, or hard SL/TP based on spread (not ATR).

SL/TP calibrated for scalping:
  - SL: 2x current spread (tight, microstructure-based)
  - TP: 4x current spread (2:1 R:R)
  - Emergency SL: 25 bps max loss cap

Exits are SETUP-BASED, not time-based:
  1. Score invalidation: re-evaluate entry score each cycle.
     If score drops below exit threshold → close.
  2. Trailing microprice stop: if microprice diverges against
     position by > spread, close (market shifted against us).
  3. Profit lock: if unrealized PnL > 1x spread, tighten SL to
     breakeven (move stop to entry price).
"""
from __future__ import annotations
from typing import Dict, List, Optional
from dataclasses import dataclass

import pandas as pd

from config.settings import SymbolConfig, TradingConfig
from core.types import (
    Signal, MarketRegime, MarketSnapshot, StrategyType, Side, Position,
)
from strategies.base import BaseStrategy
import structlog

logger = structlog.get_logger(__name__)

# ── Constants ────────────────────────────────────────────────────
COOLDOWN_SEC = 30           # Seconds between trades
MIN_ENTRY_SCORE = 0.55      # Minimum score to enter
EXIT_SCORE_THRESHOLD = 0.25 # Close if score drops below this
SL_SPREAD_MULT = 2.0        # SL = 2x spread
TP_SPREAD_MULT = 4.0        # TP = 4x spread (R:R 2:1)
MAX_SL_BPS = 25.0           # Emergency hard cap: 25 bps max loss
MIN_SPREAD_BPS = 3.0        # Minimum spread for SL/TP calc (floor)
PROFIT_LOCK_MULT = 1.0      # Lock profit at 1x spread gain


@dataclass
class OFMState:
    """Tracks per-position state for intelligent exits."""
    entry_time: float = 0
    entry_score: float = 0
    entry_spread_bps: float = 0
    best_pnl_pct: float = 0   # Peak unrealized PnL %
    breakeven_locked: bool = False


class OrderFlowMomentumStrategy(BaseStrategy):
    """Order Flow Momentum: scalp basado en flujo institucional."""

    def __init__(self, trading_config: TradingConfig) -> None:
        super().__init__(StrategyType.ORDER_FLOW_MOMENTUM, trading_config)
        self._states: Dict[str, OFMState] = {}
        self._last_exit_time: Dict[str, float] = {}

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
        ts = _time.time()

        if pd.isna(atr) or atr <= 0:
            return signals

        # ── Extract microstructure ───────────────────────────────
        micro = kwargs.get("micro")
        obi = kwargs.get("obi")
        kelly_pct = kwargs.get("kelly_risk_pct")

        if not micro or not obi:
            return signals

        obi_imbalance = obi.weighted_imbalance if obi else 0
        obi_delta = obi.delta if obi else 0

        # Microprice
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

        # Spread
        spread_bps = snapshot.orderbook.spread_bps if snapshot.orderbook else 5.0
        effective_spread = max(spread_bps, MIN_SPREAD_BPS)

        # Depth
        depth_ratio = obi.depth_ratio if obi and obi.depth_ratio else 1.0

        # ── Compute current score (used for entry AND exit) ──────
        long_score, short_score = self._compute_scores(
            obi_imbalance, obi_delta, microprice_adj_bps, hawkes_ratio,
            vpin, depth_ratio, atr, price, kwargs.get("trend_info"),
        )

        # ── Exit logic (setup invalidation) ──────────────────────
        if current_position is not None:
            exit_signal = self._check_exit(
                symbol, price, ts, current_position,
                long_score, short_score, microprice_adj_bps,
                effective_spread,
            )
            if exit_signal:
                signals.append(exit_signal)
            return signals

        # ── Cooldown ─────────────────────────────────────────────
        last_exit = self._last_exit_time.get(symbol, 0)
        if ts > 0 and last_exit > 0 and (ts - last_exit) < COOLDOWN_SEC:
            return signals

        # ── Quality filters ──────────────────────────────────────
        if vpin > 0.75:
            return signals
        if hawkes_count < 3:
            return signals
        if micro.kyle_lambda and micro.kyle_lambda.is_valid:
            if micro.kyle_lambda.impact_stress > 1.5:
                return signals
        if spread_bps > 15:
            return signals

        # ── SL/TP based on spread (microstructure-calibrated) ────
        sl_bps = min(effective_spread * SL_SPREAD_MULT, MAX_SL_BPS)
        tp_bps = effective_spread * TP_SPREAD_MULT
        sl_distance = price * sl_bps / 10000
        tp_distance = price * tp_bps / 10000

        # ── LONG ─────────────────────────────────────────────────
        if long_score >= MIN_ENTRY_SCORE and long_score > short_score + 0.10:
            stop_loss = price - sl_distance
            take_profit = price + tp_distance

            size = self._calc_position_size(
                allocated_capital, price, stop_loss,
                sym_config.leverage, kelly_risk_pct=kelly_pct,
            )
            size_usd = size * price
            strength = min(long_score, 1.0)

            if size_usd > 10:
                self._states[symbol] = OFMState(
                    entry_time=ts,
                    entry_score=long_score,
                    entry_spread_bps=effective_spread,
                )
                signals.append(Signal(
                    strategy=self.strategy_type, symbol=symbol,
                    side=Side.BUY, strength=strength,
                    entry_price=price, stop_loss=stop_loss, take_profit=take_profit,
                    size_usd=size_usd,
                    metadata={
                        "obi_imbalance": round(obi_imbalance, 3),
                        "obi_delta": round(obi_delta, 3),
                        "microprice_adj_bps": round(microprice_adj_bps, 2),
                        "hawkes_ratio": round(hawkes_ratio, 2),
                        "vpin": round(vpin, 3),
                        "depth_ratio": round(depth_ratio, 2),
                        "score": round(long_score, 3),
                        "sl_bps": round(sl_bps, 1),
                        "tp_bps": round(tp_bps, 1),
                        "spread_bps": round(spread_bps, 1),
                        "regime": regime.value,
                    },
                ))

        # ── SHORT ────────────────────────────────────────────────
        elif short_score >= MIN_ENTRY_SCORE and short_score > long_score + 0.10:
            stop_loss = price + sl_distance
            take_profit = price - tp_distance

            size = self._calc_position_size(
                allocated_capital, price, stop_loss,
                sym_config.leverage, kelly_risk_pct=kelly_pct,
            )
            size_usd = size * price
            strength = min(short_score, 1.0)

            if size_usd > 10:
                self._states[symbol] = OFMState(
                    entry_time=ts,
                    entry_score=short_score,
                    entry_spread_bps=effective_spread,
                )
                signals.append(Signal(
                    strategy=self.strategy_type, symbol=symbol,
                    side=Side.SELL, strength=strength,
                    entry_price=price, stop_loss=stop_loss, take_profit=take_profit,
                    size_usd=size_usd,
                    metadata={
                        "obi_imbalance": round(obi_imbalance, 3),
                        "obi_delta": round(obi_delta, 3),
                        "microprice_adj_bps": round(microprice_adj_bps, 2),
                        "hawkes_ratio": round(hawkes_ratio, 2),
                        "vpin": round(vpin, 3),
                        "depth_ratio": round(depth_ratio, 2),
                        "score": round(short_score, 3),
                        "sl_bps": round(sl_bps, 1),
                        "tp_bps": round(tp_bps, 1),
                        "spread_bps": round(spread_bps, 1),
                        "regime": regime.value,
                    },
                ))

        return signals

    def _compute_scores(
        self,
        obi_imbalance: float,
        obi_delta: float,
        microprice_adj_bps: float,
        hawkes_ratio: float,
        vpin: float,
        depth_ratio: float,
        atr: float,
        price: float,
        trend_info,
    ) -> tuple:
        """Compute long/short scores from microstructure signals."""
        long_score = 0.0
        short_score = 0.0

        # Signal 1: OBI (40%)
        if obi_imbalance > 0.10 and obi_delta > 0.01:
            long_score += 0.40
        elif obi_imbalance < -0.10 and obi_delta < -0.01:
            short_score += 0.40

        # Signal 2: Microprice (30%)
        microprice_threshold = max(1.5, atr / price * 5000) if price > 0 else 2.0
        if microprice_adj_bps > microprice_threshold:
            long_score += 0.30
        elif microprice_adj_bps < -microprice_threshold:
            short_score += 0.30

        # Signal 3: Hawkes (20%)
        hawkes_threshold = 2.5 if vpin < 0.4 else 3.5
        if hawkes_ratio > hawkes_threshold and obi_imbalance > 0.05:
            long_score += 0.20
        elif hawkes_ratio > hawkes_threshold and obi_imbalance < -0.05:
            short_score += 0.20

        # Signal 4: Depth (10%)
        if depth_ratio > 2.0:
            long_score += 0.10
        elif depth_ratio < 0.5:
            short_score += 0.10

        # Trend scalar
        daily_trend = 0
        if trend_info is not None:
            daily_trend = trend_info.macro_trend
        if daily_trend == 1:
            long_score *= 1.1
            short_score *= 0.3
        elif daily_trend == -1:
            long_score *= 0.3
            short_score *= 1.1

        return long_score, short_score

    def _check_exit(
        self,
        symbol: str,
        price: float,
        ts: float,
        position: Position,
        long_score: float,
        short_score: float,
        microprice_adj_bps: float,
        current_spread_bps: float,
    ) -> Optional[Signal]:
        """Exit based on setup invalidation — no time-based exits."""
        state = self._states.get(symbol)
        if state is None:
            # Position exists but no state (legacy) — use fallback
            state = OFMState(entry_time=ts, entry_score=0.5, entry_spread_bps=current_spread_bps)
            self._states[symbol] = state

        # Current score in our direction
        our_score = long_score if position.side == Side.BUY else short_score
        against_score = short_score if position.side == Side.BUY else long_score

        # Track best PnL for profit lock
        current_pnl_pct = position.pnl_pct if position.pnl_pct else 0
        state.best_pnl_pct = max(state.best_pnl_pct, current_pnl_pct)

        should_exit = False
        exit_reason = ""

        # Exit 1: Setup invalidated — our score dropped below threshold
        if our_score < EXIT_SCORE_THRESHOLD:
            should_exit = True
            exit_reason = "score_invalidated"

        # Exit 2: Counter-signal — opposing score is now strong
        elif against_score >= MIN_ENTRY_SCORE and against_score > our_score + 0.15:
            should_exit = True
            exit_reason = "counter_signal"

        # Exit 3: Microprice reversal — fair value shifted against us
        elif position.side == Side.BUY and microprice_adj_bps < -current_spread_bps:
            should_exit = True
            exit_reason = "microprice_reversal"
        elif position.side == Side.SELL and microprice_adj_bps > current_spread_bps:
            should_exit = True
            exit_reason = "microprice_reversal"

        # Exit 4: Profit lock — if we gained > spread, and now giving back
        profit_lock_bps = state.entry_spread_bps * PROFIT_LOCK_MULT
        profit_lock_pct = profit_lock_bps / 10000
        if state.best_pnl_pct > profit_lock_pct and current_pnl_pct < profit_lock_pct * 0.3:
            should_exit = True
            exit_reason = "profit_lock"

        if should_exit:
            self._states.pop(symbol, None)
            self._last_exit_time[symbol] = ts
            close_side = Side.SELL if position.side == Side.BUY else Side.BUY
            exit_size = abs(position.size * price) if position.size else 0
            if exit_size <= 0:
                exit_size = position.notional if position.notional > 0 else 100

            hold_time = ts - state.entry_time if state.entry_time > 0 else 0

            logger.info(
                "ofm_exit",
                symbol=symbol, reason=exit_reason,
                hold_sec=round(hold_time, 1),
                our_score=round(our_score, 3),
                against_score=round(against_score, 3),
                pnl_pct=round(current_pnl_pct * 100, 3),
                best_pnl_pct=round(state.best_pnl_pct * 100, 3),
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
                    "action": "exit_ofm",
                    "exit_reason": exit_reason,
                    "hold_time_sec": round(hold_time, 1),
                    "our_score": round(our_score, 3),
                    "against_score": round(against_score, 3),
                    "microprice_bps": round(microprice_adj_bps, 2),
                    "pnl_pct": round(current_pnl_pct * 100, 3),
                },
            )

        return None
