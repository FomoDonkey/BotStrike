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
COOLDOWN_SEC = 60           # Seconds between trades (applies to strategy exits AND SL/TP exits)
MIN_ENTRY_SCORE = 0.50      # Needs 2+ microstructure signals confirming
EXIT_SCORE_THRESHOLD = 0.15 # Exit when signal is mostly gone
CONFIRM_TICKS = 3           # Score must persist 3 consecutive evals (15s) — was 5 (25s), too slow for scalping
OBI_DELTA_EMA_ALPHA = 0.05  # Slow EMA: filters noise, only passes sustained flow (alpha=0.05 → ~20 tick halflife)
SL_SPREAD_MULT = 3.0        # SL = 3x spread
TP_RR_MULT = 2.0            # TP = SL * 2 (R:R 2:1)
MAX_SL_BPS = 50.0           # Emergency hard cap: 50 bps max loss (was 30 — too tight after fee-floor fix)
MIN_SPREAD_BPS = 3.0        # Minimum spread for SL/TP calc (floor)
MIN_SL_ATR_MULT = 0.3       # ATR-based SL floor: SL >= 0.3x ATR (prevents tiny SLs during low spread)
FEE_SL_MULT = 2.0           # SL must be >= 2x round-trip cost (ensures net R:R >= 1:1 after fees)
PROFIT_LOCK_MULT = 2.0      # Lock profit at 2x spread gain
MAX_HOLD_SEC = 1800          # 30 min max hold — prevent indefinite exposure
MIN_HOLD_BEFORE_MICRO_EXIT = 30  # Seconds: don't exit on microprice reversal until 30s held (avoids noise exits)


@dataclass
class OFMState:
    """Tracks per-position state for intelligent exits."""
    entry_time: float = 0
    entry_score: float = 0
    entry_spread_bps: float = 0
    entry_sl_bps: float = 0    # Actual SL used (spread/ATR/fee-based)
    best_pnl_pct: float = 0   # Peak unrealized PnL %
    breakeven_locked: bool = False


class OrderFlowMomentumStrategy(BaseStrategy):
    """Order Flow Momentum: scalp basado en flujo institucional."""

    def __init__(self, trading_config: TradingConfig) -> None:
        super().__init__(StrategyType.ORDER_FLOW_MOMENTUM, trading_config)
        self._states: Dict[str, OFMState] = {}
        self._last_exit_time: Dict[str, float] = {}
        # Confirmation tracker: signal must persist N consecutive evaluations
        self._confirm_long: Dict[str, int] = {}
        self._confirm_short: Dict[str, int] = {}
        self._confirm_side: Dict[str, str] = {}
        # EMA-smoothed OBI delta per symbol (removes tick-to-tick noise)
        self._obi_delta_ema: Dict[str, float] = {}

    def should_activate(self, regime: MarketRegime) -> bool:
        return regime != MarketRegime.UNKNOWN

    def notify_external_exit(self, symbol: str, ts: float) -> None:
        """Called when paper_sim closes position via SL/TP (not strategy-generated exit).

        Updates cooldown and cleans up state so OFM doesn't re-enter immediately.
        """
        self._last_exit_time[symbol] = ts
        self._states.pop(symbol, None)
        self._confirm_long[symbol] = 0
        self._confirm_short[symbol] = 0

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
        if price <= 0:
            return signals
        atr = current.get("atr", 0)
        ts = _time.time()

        if pd.isna(atr) or atr <= 0:
            return signals

        # ── Extract microstructure ───────────────────────────────
        micro = kwargs.get("micro")
        obi = kwargs.get("obi")
        kelly_pct = kwargs.get("kelly_risk_pct")

        if not micro or not obi:
            logger.debug("ofm_no_microstructure", symbol=symbol, has_micro=bool(micro), has_obi=bool(obi))
            return signals

        obi_imbalance = obi.weighted_imbalance if obi else 0
        obi_delta_raw = obi.delta if obi else 0
        # Smooth OBI delta with EMA to filter tick-to-tick noise
        # Initialize to raw value on first observation (avoids ~100s convergence lag from 0.0)
        if symbol not in self._obi_delta_ema:
            obi_delta = obi_delta_raw
        else:
            prev_ema = self._obi_delta_ema[symbol]
            obi_delta = OBI_DELTA_EMA_ALPHA * obi_delta_raw + (1 - OBI_DELTA_EMA_ALPHA) * prev_ema
        self._obi_delta_ema[symbol] = obi_delta

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
            effective_spread,
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
            logger.debug("ofm_filtered", symbol=symbol, reason="vpin_toxic", vpin=round(vpin, 3))
            return signals
        # Hawkes count: lowered from 3→1 for less liquid exchanges
        if hawkes_count < 1:
            logger.debug("ofm_filtered", symbol=symbol, reason="no_hawkes_events", count=hawkes_count)
            return signals
        if micro.kyle_lambda and micro.kyle_lambda.is_valid:
            if micro.kyle_lambda.impact_stress > 1.5:
                logger.debug("ofm_filtered", symbol=symbol, reason="impact_stress", stress=round(micro.kyle_lambda.impact_stress, 2))
                return signals
        if spread_bps > 20:  # Widened from 15→20 bps for less liquid venues
            logger.debug("ofm_filtered", symbol=symbol, reason="spread_wide", spread_bps=round(spread_bps, 1))
            return signals

        # ── SL/TP based on spread (microstructure-calibrated) ────
        # Three SL floors ensure viability:
        #   1. Spread-based: SL >= 3x spread (microstructure calibrated)
        #   2. ATR-based:    SL >= 0.3x ATR (prevents gap vulnerability)
        #   3. Fee-based:    SL >= 2x round-trip cost (ensures net R:R >= 1:1)
        # Without the fee floor, tight spreads on Binance (1-2 bps) create
        # SL=9bps vs 14bps round-trip cost → fees dominate, R:R collapses.
        atr_bps = (atr / price * 10000) if price > 0 else 10.0
        atr_sl_floor = atr_bps * MIN_SL_ATR_MULT
        round_trip_bps = (
            self.trading_config.slippage_bps * 2
            + self.trading_config.taker_fee * 10_000 * 2
        )
        fee_sl_floor = round_trip_bps * FEE_SL_MULT
        sl_bps = min(
            max(effective_spread * SL_SPREAD_MULT, atr_sl_floor, fee_sl_floor),
            MAX_SL_BPS,
        )
        tp_bps = sl_bps * TP_RR_MULT  # Maintain 2:1 R:R regardless of SL source
        sl_distance = price * sl_bps / 10000
        tp_distance = price * tp_bps / 10000

        # ── Persistence filter: score must hold for N consecutive evals ──
        long_ok = long_score >= MIN_ENTRY_SCORE and long_score > short_score + 0.10
        short_ok = short_score >= MIN_ENTRY_SCORE and short_score > long_score + 0.10

        if long_ok:
            self._confirm_long[symbol] = self._confirm_long.get(symbol, 0) + 1
            self._confirm_short[symbol] = 0
        elif short_ok:
            self._confirm_short[symbol] = self._confirm_short.get(symbol, 0) + 1
            self._confirm_long[symbol] = 0
        else:
            self._confirm_long[symbol] = 0
            self._confirm_short[symbol] = 0

        long_confirmed = self._confirm_long.get(symbol, 0) >= CONFIRM_TICKS
        short_confirmed = self._confirm_short.get(symbol, 0) >= CONFIRM_TICKS

        # Log scores for diagnostics (every evaluation)
        logger.debug("ofm_scores", symbol=symbol,
                     long=round(long_score, 3), short=round(short_score, 3),
                     threshold=MIN_ENTRY_SCORE,
                     confirm_l=self._confirm_long.get(symbol, 0),
                     confirm_s=self._confirm_short.get(symbol, 0),
                     need=CONFIRM_TICKS,
                     obi=round(obi_imbalance, 3), microprice=round(microprice_adj_bps, 2),
                     hawkes=round(hawkes_ratio, 2), depth=round(depth_ratio, 2),
                     vpin=round(vpin, 3), spread=round(spread_bps, 1))

        # ── LONG ─────────────────────────────────────────────────
        if long_confirmed:
            stop_loss = price - sl_distance
            take_profit = price + tp_distance

            size = self._calc_position_size(
                allocated_capital, price, stop_loss,
                sym_config.leverage, kelly_risk_pct=kelly_pct,
            )
            size_usd = size * price
            strength = min(long_score, 1.0)

            if size_usd > 10:
                self._confirm_long[symbol] = 0  # Reset after entry
                self._confirm_short[symbol] = 0
                self._states[symbol] = OFMState(
                    entry_time=ts,
                    entry_score=long_score,
                    entry_spread_bps=effective_spread,
                    entry_sl_bps=sl_bps,
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
        elif short_confirmed:
            stop_loss = price + sl_distance
            take_profit = price - tp_distance

            size = self._calc_position_size(
                allocated_capital, price, stop_loss,
                sym_config.leverage, kelly_risk_pct=kelly_pct,
            )
            size_usd = size * price
            strength = min(short_score, 1.0)

            if size_usd > 10:
                self._confirm_long[symbol] = 0  # Reset after entry
                self._confirm_short[symbol] = 0
                self._states[symbol] = OFMState(
                    entry_time=ts,
                    entry_score=short_score,
                    entry_spread_bps=effective_spread,
                    entry_sl_bps=sl_bps,
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
        effective_spread: float = 3.0,
    ) -> tuple:
        """Compute long/short scores from microstructure signals.

        Uses DELTA (change) for OBI, not absolute level — absolute OBI has
        persistent structural bias in BTC (more ask depth than bid depth).
        Delta captures actual shifts in pressure, not static book shape.
        """
        long_score = 0.0
        short_score = 0.0

        # Signal 1: OBI DELTA (35%) — change in imbalance is predictive, level is not
        # Positive delta = buying pressure increasing, negative = selling increasing
        if obi_delta > 0.02:
            long_score += 0.35
        elif obi_delta < -0.02:
            short_score += 0.35

        # Signal 2: Microprice (30%)
        # Threshold based on spread (transaction cost) — not ATR.
        # In low-vol with tight spread, we need MORE microprice shift to justify entry.
        # In high-vol with wide spread, threshold scales naturally.
        microprice_threshold = max(0.8, effective_spread * 0.4) if price > 0 else 1.5
        if microprice_adj_bps > microprice_threshold:
            long_score += 0.30
        elif microprice_adj_bps < -microprice_threshold:
            short_score += 0.30

        # Signal 3: Hawkes (20%) — activity spike + delta confirmation
        hawkes_threshold = 1.8 if vpin < 0.4 else 2.5
        if hawkes_ratio > hawkes_threshold and obi_delta > 0.01:
            long_score += 0.20
        elif hawkes_ratio > hawkes_threshold and obi_delta < -0.01:
            short_score += 0.20

        # Signal 4: Depth ratio (15%) — relative to neutral (1.0), not absolute
        # Use deviation from 1.0 to avoid structural bias
        depth_dev = depth_ratio - 1.0
        if depth_dev > 0.3:
            long_score += 0.15
        elif depth_dev < -0.3:
            short_score += 0.15

        # Trend scalar
        daily_trend = 0
        if trend_info is not None:
            daily_trend = trend_info.macro_trend
        if daily_trend == 1:
            long_score *= 1.15
            short_score *= 0.85
        elif daily_trend == -1:
            long_score *= 0.85
            short_score *= 1.15

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
        """Exit based on setup invalidation + max hold time safety."""
        state = self._states.get(symbol)
        if state is None:
            # Position exists but no state (legacy) — use fallback
            state = OFMState(entry_time=ts, entry_score=0.5, entry_spread_bps=current_spread_bps)
            self._states[symbol] = state

        # Current score in our direction
        our_score = long_score if position.side == Side.BUY else short_score
        against_score = short_score if position.side == Side.BUY else long_score

        # Track best PnL for profit lock
        current_pnl_pct = position.pnl_pct if (position.pnl_pct and not pd.isna(position.pnl_pct)) else 0
        state.best_pnl_pct = max(state.best_pnl_pct, current_pnl_pct)

        should_exit = False
        exit_reason = ""
        hold_secs = ts - state.entry_time if state.entry_time > 0 else 0

        # Exit 1: Setup invalidated — score fully collapsed
        # Requires minimum hold time to avoid noise exits in the first few evaluations
        # after entry. Score can temporarily dip due to EMA lag then recover.
        if our_score < EXIT_SCORE_THRESHOLD and hold_secs >= MIN_HOLD_BEFORE_MICRO_EXIT:
            should_exit = True
            exit_reason = "score_invalidated"

        # Exit 2: Counter-signal — opposing score is now strong
        # Also requires minimum hold time — counter signals can be noise
        elif against_score >= MIN_ENTRY_SCORE and against_score > our_score + 0.15 and hold_secs >= MIN_HOLD_BEFORE_MICRO_EXIT:
            should_exit = True
            exit_reason = "counter_signal"

        # Exit 3: Microprice reversal — fair value shifted against us
        # Requires minimum hold time to avoid noise exits from tick-level microprice fluctuation
        if not should_exit and hold_secs >= MIN_HOLD_BEFORE_MICRO_EXIT:
            if position.side == Side.BUY and microprice_adj_bps < -current_spread_bps:
                should_exit = True
                exit_reason = "microprice_reversal"
            elif position.side == Side.SELL and microprice_adj_bps > current_spread_bps:
                should_exit = True
                exit_reason = "microprice_reversal"

        # Exit 4: Max hold time — prevent indefinite exposure
        hold_time = ts - state.entry_time if state.entry_time > 0 else 0
        if hold_time > MAX_HOLD_SEC:
            should_exit = True
            exit_reason = "max_hold_time"

        # Exit 5: Profit lock — if we gained > SL distance, and now giving back
        # Use SL-based threshold (not spread) — matches actual risk/reward
        profit_lock_bps = (state.entry_sl_bps if state.entry_sl_bps > 0 else state.entry_spread_bps * SL_SPREAD_MULT) * PROFIT_LOCK_MULT
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
