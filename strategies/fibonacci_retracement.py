"""
Fibonacci Impulse-Retracement Strategy — Account Growth Engine.

DESIGN PHILOSOPHY:
This strategy targets aggressive account growth by trading high-probability
retracements of strong impulse moves. It uses Fibonacci levels as structural
zones where institutional order flow concentrates (self-fulfilling prophecy).

MECHANISM:
1. Detect strong impulse moves (> 2.5 ATR in a direction) on 15m bars
2. Wait for price to retrace to the 50-61.8% Fibonacci zone
3. Confirm with declining volume + RSI divergence + rejection wick
4. Enter at the Fib zone with tight SL below 78.6%
5. Target Fib extensions (1.0, 1.618) with trailing stop

RISK PROFILE:
- 4% risk per trade (aggressive for account growth)
- R:R gross: 2.5:1 to 5:1
- Expected WR: 45-55% (Fib levels are self-fulfilling in crypto)
- Expected PF: 1.5-2.0
- Max DD tolerance: 25-30%

TIMEFRAME: 15m (ATR ~34bps = 2.4x fees, first viable TF for BTC)
"""
from __future__ import annotations
from typing import Dict, List, Optional
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import time as _time

from config.settings import SymbolConfig, TradingConfig
from core.types import (
    Signal, MarketRegime, MarketSnapshot, StrategyType, Side, Position,
)
from strategies.base import BaseStrategy
from core.indicators import Indicators
import structlog

logger = structlog.get_logger(__name__)

# ── Configuration ────────────────────────────────────────────────
RESAMPLE_MINUTES = 15       # 15m bars for impulse detection
RESAMPLE_BUFFER = 150       # Keep 150 bars (~37 hours)
COOLDOWN_SEC = 300          # 5 min cooldown between trades (15m TF = fewer opportunities)
MIN_BARS_1M = 60            # 1 hour minimum raw data
MIN_BARS_15M = 30           # 30 bars of 15m = 7.5 hours

# Impulse detection
IMPULSE_LOOKBACK = 20       # Scan last 20 bars for swing high/low
MIN_IMPULSE_ATR = 3.0       # Impulse must be >= 3x ATR (was 2.5 — too many false impulses)
MAX_IMPULSE_AGE = 30        # Impulse expires after 30 bars (~7.5h, was 40 — stale impulses lose edge)
MIN_IMPULSE_BARS = 4        # Impulse must develop over at least 4 bars (structural move, not wick)

# Fibonacci levels
FIB_ENTRY_UPPER = 0.500     # Entry zone: 50% retracement
FIB_ENTRY_LOWER = 0.618     # Entry zone: 61.8% retracement
FIB_SL = 0.786              # Stop loss at 78.6% retracement
FIB_TP1 = 0.0               # TP1 at 0% = back to impulse extreme (100% extension)
FIB_TP2 = -0.618            # TP2 at 161.8% extension

# Exit management
TRAIL_ACTIVATE_ATR = 2.0    # Start trailing after 2 ATR profit (near TP1)
TRAIL_DISTANCE_ATR = 0.8    # Wide trail — let winners run to extensions

# Stale position: close if < 0.3 ATR movement after 24h (replaces max_hold)
STALE_HOURS = 24
STALE_ATR_THRESHOLD = 0.3

# Risk — aggressive for account growth
RISK_PER_TRADE = 0.04       # 4% per trade ($12 on $300)
MIN_CONFIRMATIONS = 2       # Need 2+ confirmations at the Fib level


# ── Fibonacci calculations ─────────────────────────────────────
def fib_level(swing_low: float, swing_high: float, level: float) -> float:
    """Calculate a Fibonacci retracement level.

    level=0.0 → swing_high (0% retrace)
    level=0.382 → 38.2% retrace
    level=0.618 → 61.8% retrace
    level=1.0 → swing_low (100% retrace)
    level=-0.618 → 161.8% extension above swing_high
    """
    return swing_high - (swing_high - swing_low) * level


@dataclass
class FibImpulse:
    """Detected impulse move with Fibonacci levels."""
    swing_low: float
    swing_high: float
    direction: int              # 1=bullish (low→high), -1=bearish (high→low)
    impulse_atr: float          # Impulse size in ATR units
    bar_idx_start: int          # Bar index where impulse started
    bar_idx_end: int            # Bar index where impulse peaked
    timestamp: float = 0        # Timestamp of detection

    @property
    def range(self) -> float:
        return self.swing_high - self.swing_low

    def fib(self, level: float) -> float:
        """Get price at Fibonacci level (for bullish impulse)."""
        if self.direction == 1:
            return fib_level(self.swing_low, self.swing_high, level)
        else:
            # Bearish: invert — swing_high is the start, swing_low is the end
            return fib_level(self.swing_high, self.swing_low, level)

    @property
    def entry_zone_top(self) -> float:
        return self.fib(FIB_ENTRY_UPPER)

    @property
    def entry_zone_bottom(self) -> float:
        return self.fib(FIB_ENTRY_LOWER)

    @property
    def sl_price(self) -> float:
        return self.fib(FIB_SL)

    @property
    def tp1_price(self) -> float:
        return self.fib(FIB_TP1)

    @property
    def tp2_price(self) -> float:
        return self.fib(FIB_TP2)


@dataclass
class FibState:
    """Per-position exit management state."""
    entry_time: float = 0
    entry_bar_idx: int = 0
    impulse: Optional[FibImpulse] = None
    best_pnl_atr: float = 0
    trail_active: bool = False


class FibonacciRetracementStrategy(BaseStrategy):
    """Fibonacci Impulse-Retracement: aggressive account growth strategy."""

    def __init__(self, trading_config: TradingConfig) -> None:
        super().__init__(StrategyType.FIBONACCI_RETRACEMENT, trading_config)
        self._last_exit_time: Dict[str, float] = {}
        self._states: Dict[str, FibState] = {}
        self._eval_counter: Dict[str, int] = {}
        # Internal 15m bar cache
        self._resampled: Dict[str, pd.DataFrame] = {}
        self._last_resample_key: Dict[str, tuple] = {}
        # Tracked impulses per symbol (most recent)
        self._impulses: Dict[str, Optional[FibImpulse]] = {}
        self.backtest_mode: bool = False

    def should_activate(self, regime: MarketRegime) -> bool:
        # Fib retracements work best in trending markets.
        # Also allow RANGING — impulses can start from ranges.
        # Block UNKNOWN only during warmup.
        return regime != MarketRegime.UNKNOWN

    def notify_external_exit(self, symbol: str, ts: float) -> None:
        self._last_exit_time[symbol] = ts
        self._states.pop(symbol, None)

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

        if df.empty or len(df) < MIN_BARS_1M:
            return signals

        self._eval_counter[symbol] = self._eval_counter.get(symbol, 0) + 1

        # ── Resample to 15m bars ─────────────────────────────────
        last_close = float(df.iloc[-1]["close"])
        last_ts = float(df.iloc[-1].get("timestamp", len(df)))
        bar_key = (last_close, last_ts)
        prev_key = self._last_resample_key.get(symbol)
        new_bar_arrived = bar_key != prev_key

        if new_bar_arrived or symbol not in self._resampled:
            self._last_resample_key[symbol] = bar_key
            self._resample_15m(symbol, df)

        m15 = self._resampled.get(symbol)
        if m15 is None or len(m15) < MIN_BARS_15M:
            return signals

        # ── EXIT check (every eval for fast response) ────────────
        if current_position is not None:
            exit_sig = self._check_exit(symbol, m15, current_position, snapshot)
            if exit_sig:
                signals.append(exit_sig)
            return signals

        # ── Only evaluate entries on new bars ────────────────────
        if not new_bar_arrived:
            return signals

        # ── COOLDOWN ─────────────────────────────────────────────
        if self.backtest_mode:
            raw_ts = snapshot.timestamp if snapshot.timestamp > 1e9 else float(df.iloc[-1].get("timestamp", 0))
            now = raw_ts / 1000 if raw_ts > 1e12 else raw_ts
        else:
            now = _time.time()
        last_exit = self._last_exit_time.get(symbol, 0)
        if last_exit > 0 and (now - last_exit) < COOLDOWN_SEC:
            return signals

        price = snapshot.price if snapshot.price > 0 else float(df.iloc[-1]["close"])
        if price <= 0:
            return signals

        bar = m15.iloc[-1]
        atr = float(bar.get("atr", 0))
        rsi = float(bar.get("rsi", 50))
        adx = float(bar.get("adx", 0))
        volume = float(bar.get("volume", 0))

        if pd.isna(atr) or atr <= 0:
            return signals

        # ADX filter: impulses require directional strength
        if pd.isna(adx) or adx < 18:
            return signals

        # ── DETECT IMPULSES ──────────────────────────────────────
        impulse = self._detect_impulse(symbol, m15, atr)
        if impulse:
            self._impulses[symbol] = impulse

        active_impulse = self._impulses.get(symbol)
        if not active_impulse:
            return signals

        # Check impulse age — expire stale impulses
        current_bar_idx = len(m15)
        impulse_age = current_bar_idx - active_impulse.bar_idx_end
        if impulse_age > MAX_IMPULSE_AGE:
            self._impulses[symbol] = None
            return signals

        # ── CHECK IF PRICE IS IN FIB ENTRY ZONE ─────────────────
        zone_top = active_impulse.entry_zone_top
        zone_bottom = active_impulse.entry_zone_bottom

        if active_impulse.direction == 1:
            # Bullish: entry zone is below current impulse high
            in_zone = zone_bottom <= price <= zone_top
        else:
            # Bearish: entry zone is above current impulse low
            in_zone = zone_top <= price <= zone_bottom

        if not in_zone:
            return signals

        # ── CONFIRMATIONS ────────────────────────────────────────
        confirmations = 0

        # 1. Volume declining during retracement (exhaustion)
        vol_avg = float(m15["volume"].tail(10).mean()) if len(m15) >= 10 else 0
        has_vol_decline = vol_avg > 0 and volume < vol_avg * 0.75
        if has_vol_decline:
            confirmations += 1

        # 2. RSI at appropriate level for the direction
        if active_impulse.direction == 1:
            has_rsi = rsi < 40  # Oversold on pullback in bullish impulse
        else:
            has_rsi = rsi > 60  # Overbought on rally in bearish impulse
        if has_rsi:
            confirmations += 1

        # 3. Rejection wick at the Fib level
        has_wick = self._has_rejection_wick(bar, active_impulse.direction == 1)
        if has_wick:
            confirmations += 1

        # 4. OBI (Order Book Imbalance) confirmation
        obi = kwargs.get("obi")
        has_obi = False
        if obi:
            if active_impulse.direction == 1:
                has_obi = obi.weighted_imbalance > 0.05
            else:
                has_obi = obi.weighted_imbalance < -0.05
        if has_obi:
            confirmations += 1

        if confirmations < MIN_CONFIRMATIONS:
            return signals

        # ── NET R:R CHECK ────────────────────────────────────────
        sl_price = active_impulse.sl_price
        tp_price = active_impulse.tp2_price  # Target the 161.8% extension

        risk_dist = abs(price - sl_price)
        reward_dist = abs(tp_price - price)
        if risk_dist <= 0:
            return signals

        rt_cost = price * 14 / 10000  # 14 bps round-trip
        net_reward = reward_dist - rt_cost
        net_risk = risk_dist + rt_cost
        if net_reward <= 0 or net_reward / net_risk < 1.5:
            return signals  # Need at least 1.5:1 net R:R

        # ── ENTRY ────────────────────────────────────────────────
        if active_impulse.direction == 1:
            side = Side.BUY
            stop_loss = sl_price
            take_profit = tp_price
            trigger = "fib_retrace_bull"
        else:
            side = Side.SELL
            stop_loss = sl_price
            take_profit = tp_price
            trigger = "fib_retrace_bear"

        # Aggressive sizing for account growth
        size = self._calc_position_size(
            allocated_capital, price, stop_loss,
            sym_config.leverage, kelly_risk_pct=RISK_PER_TRADE,
        )
        size_usd = size * price

        if size_usd < 20:
            return signals

        # ── CREATE SIGNAL ────────────────────────────────────────
        score = 0.50 + confirmations * 0.12
        strength = min(score, 1.0)

        self._states[symbol] = FibState(
            entry_time=now,
            entry_bar_idx=len(m15),
            impulse=active_impulse,
        )

        # Consume the impulse (don't re-enter on the same impulse)
        self._impulses[symbol] = None

        logger.info("fib_entry", symbol=symbol, side=side.value,
                     trigger=trigger, direction=active_impulse.direction,
                     impulse_atr=round(active_impulse.impulse_atr, 1),
                     fib_zone=f"{FIB_ENTRY_UPPER*100:.0f}-{FIB_ENTRY_LOWER*100:.0f}%",
                     rsi=round(rsi, 1), confirmations=confirmations,
                     sl=round(sl_price, 2), tp=round(tp_price, 2),
                     rr=round(net_reward / net_risk, 2))

        signals.append(Signal(
            strategy=self.strategy_type, symbol=symbol,
            side=side, strength=strength,
            entry_price=price, stop_loss=stop_loss, take_profit=take_profit,
            size_usd=size_usd,
            metadata={
                "trigger": trigger,
                "impulse_atr": round(active_impulse.impulse_atr, 2),
                "fib_zone": f"{FIB_ENTRY_UPPER}-{FIB_ENTRY_LOWER}",
                "sl_fib": FIB_SL,
                "tp_fib": FIB_TP2,
                "rsi": round(rsi, 1),
                "atr": round(atr, 2),
                "confirmations": confirmations,
                "has_vol_decline": has_vol_decline,
                "has_rsi": has_rsi,
                "has_wick": has_wick,
                "has_obi": has_obi,
                "net_rr": round(net_reward / net_risk, 2),
                "impulse_age_bars": impulse_age,
            },
        ))
        return signals

    # ── IMPULSE DETECTION ────────────────────────────────────────

    def _detect_impulse(
        self, symbol: str, m15: pd.DataFrame, current_atr: float,
    ) -> Optional[FibImpulse]:
        """Detect a strong impulse move in the last N bars.

        An impulse is a directional move > MIN_IMPULSE_ATR * ATR
        that develops over at least MIN_IMPULSE_BARS bars.
        """
        if len(m15) < IMPULSE_LOOKBACK + 1:
            return None

        window = m15.tail(IMPULSE_LOOKBACK)
        highs = window["high"].values
        lows = window["low"].values

        # Find the highest high and lowest low in the lookback window
        max_idx = int(np.argmax(highs))
        min_idx = int(np.argmin(lows))
        highest = float(highs[max_idx])
        lowest = float(lows[min_idx])

        move = highest - lowest
        if current_atr <= 0 or move < MIN_IMPULSE_ATR * current_atr:
            return None

        # Determine direction: which extreme came LAST is the impulse direction
        # Bullish: low came first, then high (price went UP)
        # Bearish: high came first, then low (price went DOWN)
        bars_between = abs(max_idx - min_idx)
        if bars_between < MIN_IMPULSE_BARS:
            return None  # Too fast — likely a wick, not a structural impulse

        current_len = len(m15)
        base_idx = current_len - IMPULSE_LOOKBACK

        if min_idx < max_idx:
            # Bullish impulse
            # Only valid if the high is recent (in last 60% of window)
            if max_idx < IMPULSE_LOOKBACK * 0.4:
                return None  # High is too old — impulse already played out
            return FibImpulse(
                swing_low=lowest,
                swing_high=highest,
                direction=1,
                impulse_atr=move / current_atr,
                bar_idx_start=base_idx + min_idx,
                bar_idx_end=base_idx + max_idx,
                timestamp=_time.time(),
            )
        else:
            # Bearish impulse
            if min_idx < IMPULSE_LOOKBACK * 0.4:
                return None
            return FibImpulse(
                swing_low=lowest,
                swing_high=highest,
                direction=-1,
                impulse_atr=move / current_atr,
                bar_idx_start=base_idx + max_idx,
                bar_idx_end=base_idx + min_idx,
                timestamp=_time.time(),
            )

    # ── EXIT MANAGEMENT ──────────────────────────────────────────

    def _check_exit(
        self, symbol: str, m15: pd.DataFrame,
        position: Position, snapshot: MarketSnapshot,
    ) -> Optional[Signal]:
        state = self._states.get(symbol)
        if not state:
            return None

        price = snapshot.price if snapshot.price > 0 else float(m15.iloc[-1]["close"])
        if price <= 0:
            return None

        atr = float(m15.iloc[-1].get("atr", 0))
        if pd.isna(atr) or atr <= 0:
            return None

        current_bar_count = len(m15)
        bars_held = current_bar_count - state.entry_bar_idx
        exit_reason = None

        if position.entry_price > 0:
            pnl_atr = ((price - position.entry_price) / atr if position.side == Side.BUY
                       else (position.entry_price - price) / atr)

            # SL safety net
            sl_dist_atr = abs(position.entry_price - state.impulse.sl_price) / atr if state.impulse and atr > 0 else 2.0
            if pnl_atr < -(sl_dist_atr + 0.2):
                exit_reason = "software_sl_safety"

            # TP safety net (at 161.8% extension)
            if state.impulse:
                tp_dist = abs(state.impulse.tp2_price - position.entry_price)
                tp_dist_atr = tp_dist / atr if atr > 0 else 5.0
                if pnl_atr >= tp_dist_atr:
                    exit_reason = "software_tp_extension"

            # ── Trailing stop ────────────────────────────────────
            if pnl_atr > state.best_pnl_atr:
                state.best_pnl_atr = pnl_atr

            if state.best_pnl_atr >= TRAIL_ACTIVATE_ATR:
                state.trail_active = True

            if state.trail_active:
                trail_level = state.best_pnl_atr - TRAIL_DISTANCE_ATR
                if pnl_atr <= trail_level:
                    exit_reason = f"trailing_stop_{state.best_pnl_atr:.1f}atr"

        # Stale position: close if price hasn't moved after 24h
        stale_bars = int(STALE_HOURS * 60 / RESAMPLE_MINUTES)  # 24h in 15m bars = 96
        if bars_held >= stale_bars and position.entry_price > 0:
            abs_move = abs(price - position.entry_price) / atr if atr > 0 else 0
            if abs_move < STALE_ATR_THRESHOLD:
                exit_reason = "stale_position_24h"

        if not exit_reason:
            return None

        close_side = Side.SELL if position.side == Side.BUY else Side.BUY
        size_usd = position.notional if position.notional > 0 else position.size * price

        self._states.pop(symbol, None)
        if self.backtest_mode:
            raw_ts = snapshot.timestamp if snapshot.timestamp > 1e9 else 0
            self._last_exit_time[symbol] = raw_ts / 1000 if raw_ts > 1e12 else raw_ts
        else:
            self._last_exit_time[symbol] = _time.time()

        logger.info("fib_exit", symbol=symbol, reason=exit_reason, bars_held=bars_held,
                     best_pnl_atr=round(state.best_pnl_atr, 2))

        return Signal(
            strategy=self.strategy_type, symbol=symbol,
            side=close_side, strength=1.0,
            entry_price=price, stop_loss=price, take_profit=price,
            size_usd=size_usd,
            metadata={"action": "exit_fibonacci", "exit_reason": exit_reason},
        )

    # ── RESAMPLING ───────────────────────────────────────────────

    def _resample_15m(self, symbol: str, df: pd.DataFrame) -> None:
        """Resample 1m bars to 15m with indicators."""
        tail = df.tail(RESAMPLE_BUFFER * RESAMPLE_MINUTES + RESAMPLE_MINUTES)
        n = len(tail) // RESAMPLE_MINUTES * RESAMPLE_MINUTES
        if n < RESAMPLE_MINUTES:
            return
        trim = tail.tail(n)

        groups = [trim.iloc[i:i + RESAMPLE_MINUTES] for i in range(0, n, RESAMPLE_MINUTES)]
        bars = []
        for g in groups:
            bars.append({
                "timestamp": float(g.iloc[0].get("timestamp", 0)),
                "open": float(g.iloc[0]["open"]),
                "high": float(g["high"].max()),
                "low": float(g["low"].min()),
                "close": float(g.iloc[-1]["close"]),
                "volume": float(g["volume"].sum()),
            })

        m15 = pd.DataFrame(bars)
        if len(m15) < 14:
            return

        m15 = Indicators.compute_all(m15)
        self._resampled[symbol] = m15.tail(RESAMPLE_BUFFER)

    # ── HELPERS ──────────────────────────────────────────────────

    @staticmethod
    def _has_rejection_wick(bar: pd.Series, is_bull: bool) -> bool:
        """Check for rejection wick at Fibonacci level."""
        high = float(bar.get("high", 0))
        low = float(bar.get("low", 0))
        open_ = float(bar.get("open", 0))
        close = float(bar.get("close", 0))
        candle_range = high - low
        if candle_range <= 0:
            return False
        body_top = max(open_, close)
        body_bottom = min(open_, close)

        if is_bull:
            # Bullish rejection: long lower wick (buying pressure at Fib)
            lower_wick = body_bottom - low
            return lower_wick / candle_range > 0.45
        else:
            # Bearish rejection: long upper wick (selling pressure at Fib)
            upper_wick = high - body_top
            return upper_wick / candle_range > 0.45
