"""
Adaptive Mean Reversion Strategy — Multi-timeframe with regime awareness.

QUANT ANALYSIS RESULTS (90 days BTC, Dec 2025 - Apr 2026):
- NO technical indicator combination achieves PF > 1.0 on any single timeframe
- Best performers: RSI extreme (PF=0.92), ADX trend (PF=0.86)
- Root cause: 14bps round-trip cost requires 30-50bps moves to profit
- BTC 1m ATR = 6bps (0.5x fees) — IMPOSSIBLE to profit
- BTC 5m ATR = 17bps (1.2x fees) — marginal
- BTC 15m ATR = 34bps (2.4x fees) — first viable timeframe

STRATEGY DESIGN:
Operates on 5m resampled bars (best balance of signal frequency and ATR/fee ratio).
Uses 1H trend as directional filter (trade WITH the trend, not against).
Entry on 5m oversold/overbought in trend direction (pullback buy / rally sell).
Conservative sizing — this is a "survive and learn" strategy, not alpha extraction.

The real edge comes from OFM (microstructure) in live trading with real orderbook data.
This MR strategy serves as a baseline that avoids catastrophic loss while OFM is validated.

TARGET: Breakeven to slightly positive while accumulating live data for OFM validation.
"""
from __future__ import annotations
from typing import Dict, List, Optional
from dataclasses import dataclass

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
RESAMPLE_MINUTES = 5        # Internal resampling to 5m bars
RESAMPLE_BUFFER = 200       # Keep 200 resampled bars (16+ hours)
COOLDOWN_SEC = 180          # 3 min cooldown (5m = ~36 signals/day max with cooldown)
RSI_OVERSOLD = 35           # Wilder's RSI oversold (35 for 5m crypto)
RSI_OVERBOUGHT = 65         # Wilder's RSI overbought
ADX_MIN_TREND = 20          # Minimum 1H ADX for trend confirmation
SL_ATR_MULT = 1.5           # 1.5x ATR stop loss — best for ETH/ADA (BTC structurally unprofitable with MR)
TP_ATR_MULT = 4.0           # 4x ATR take profit → gross R:R 2.67:1
MIN_BARS_1M = 30            # Minimum 1m bars needed
MIN_BARS_5M = 50            # Minimum resampled bars needed
# Trailing stop with progressive tightening:
TRAIL_ACTIVATE_ATR = 1.5    # Start trailing after 1.5x ATR profit
TRAIL_DISTANCE_ATR = 0.5    # Trail 0.5 ATR behind peak → min capture 1.0 ATR
TRAIL_TIGHT_AFTER_BARS = 20 # After 20 5m-bars (~1.7h), tighten trail
TRAIL_TIGHT_DISTANCE = 0.3  # Tighter trail for stale positions
MIN_CONFIRMATIONS = 2       # Require 2+ independent confirmations
# Stale position: close if price hasn't moved > STALE_ATR_THRESHOLD in STALE_HOURS
# Replaces max_hold — trades exit by PRICE (SL/TP/trail), not by clock
STALE_HOURS = 24            # 24h before checking if position is dead
STALE_ATR_THRESHOLD = 0.3   # Must have moved > 0.3 ATR from entry to justify holding

# Legacy compatibility for scripts that import TF_CONFIGS
@dataclass
class TFConfig:
    name: str = ""
    interval: str = ""
    sl_mult: float = SL_ATR_MULT
    tp_mult: float = TP_ATR_MULT
    risk_pct: float = 0.015
    cache_ttl: int = 0

TF_CONFIGS: Dict[str, TFConfig] = {
    "5m": TFConfig(name="5m Adaptive MR + Trailing Stop", interval="5m",
                   sl_mult=SL_ATR_MULT, tp_mult=TP_ATR_MULT, risk_pct=0.015),
}


@dataclass
class MRState:
    """Per-position exit management state."""
    entry_time: float = 0
    entry_bar_idx: int = 0
    best_pnl_atr: float = 0       # Track peak PnL in ATR units for trailing stop
    trail_active: bool = False      # Trailing stop activated after TRAIL_ACTIVATE_ATR
    sl_mult: float = SL_ATR_MULT   # Per-position SL multiplier (from SymbolConfig)
    tp_mult: float = TP_ATR_MULT   # Per-position TP multiplier (from SymbolConfig)


class MeanReversionStrategy(BaseStrategy):
    """Adaptive MR: 5m resampled bars + 1H trend filter."""

    def __init__(self, trading_config: TradingConfig) -> None:
        super().__init__(StrategyType.MEAN_REVERSION, trading_config)
        self._last_exit_time: Dict[str, float] = {}
        self._states: Dict[str, MRState] = {}
        self._eval_counter: Dict[str, int] = {}
        # Internal 5m bar cache (built from 1m input)
        self._resampled: Dict[str, pd.DataFrame] = {}
        self._last_resample_len: Dict[str, int] = {}
        # 1H trend cache
        self._h1_trend: Dict[str, int] = {}   # 1=up, -1=down, 0=neutral
        self._h1_adx: Dict[str, float] = {}
        self._h1_cache_bars: Dict[str, int] = {}
        self.backtest_mode: bool = False

    def should_activate(self, regime: MarketRegime) -> bool:
        # Allow UNKNOWN (startup) — the 1H trend filter already gates entries.
        # BREAKOUT is still blocked: MR bets on reversion, breakouts extend.
        return regime != MarketRegime.BREAKOUT

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

        # Increment eval counter
        self._eval_counter[symbol] = self._eval_counter.get(symbol, 0) + 1
        eval_count = self._eval_counter[symbol]

        # ── Resample to 5m (only when NEW 1m bar arrives) ────────
        # Detect new bars by checking the last row's close price + timestamp,
        # not len(df). In backtest the sliding window keeps len constant at ~501,
        # so len-based detection silently stops after bar 500.
        last_close = float(df.iloc[-1]["close"])
        last_ts = float(df.iloc[-1].get("timestamp", len(df)))
        bar_key = (last_close, last_ts)
        prev_key = self._last_resample_len.get(symbol)
        new_bar_arrived = bar_key != prev_key
        if new_bar_arrived or symbol not in self._resampled:
            self._last_resample_len[symbol] = bar_key
            self._resample_5m(symbol, df)

        m5 = self._resampled.get(symbol)
        if m5 is None or len(m5) < MIN_BARS_5M:
            return signals

        # ── Update 1H trend on every new 1m bar (cheap computation, prevents stale filter) ─
        if new_bar_arrived or symbol not in self._h1_trend:
            self._update_h1_trend(symbol, df)

        h1_trend = self._h1_trend.get(symbol, 0)
        h1_adx = self._h1_adx.get(symbol, 0)

        # ── EXIT check (every eval, fast response) ───────────────
        if current_position is not None:
            exit_sig = self._check_exit(symbol, m5, current_position, snapshot)
            if exit_sig:
                signals.append(exit_sig)
            return signals

        # ── Only evaluate entries when new data arrives ──────────
        if not new_bar_arrived:
            return signals

        # ── ENTRY LOGIC ──────────────────────────────────────────
        # In backtest mode, use bar timestamp for cooldown (wall-clock is meaningless).
        # Timestamps in data are ms-epoch; convert to seconds for cooldown math.
        if self.backtest_mode:
            raw_ts = snapshot.timestamp if snapshot.timestamp > 1e9 else float(df.iloc[-1].get("timestamp", 0))
            now = raw_ts / 1000 if raw_ts > 1e12 else raw_ts  # ms -> sec
        else:
            now = _time.time()
        last_exit = self._last_exit_time.get(symbol, 0)
        if last_exit > 0 and (now - last_exit) < COOLDOWN_SEC:
            return signals

        price = snapshot.price if snapshot.price > 0 else float(df.iloc[-1]["close"])
        if price <= 0:
            return signals

        bar = m5.iloc[-1]
        atr = float(bar.get("atr", 0))
        rsi = float(bar.get("rsi", 50))
        adx = float(bar.get("adx", 0))
        bb_lower = float(bar.get("bb_lower", 0))
        bb_upper = float(bar.get("bb_upper", 0))
        zscore = float(bar.get("zscore", 0))
        close_5m = float(bar.get("close", price))
        volume = float(bar.get("volume", 0))

        if pd.isna(atr) or atr <= 0 or pd.isna(bb_lower) or bb_lower == 0:
            return signals

        # ── TREND FILTER (1H) ────────────────────────────────────
        # Only trade in trend direction. No trend = no trade.
        # This is the key filter that turns losing MR into breakeven+
        if h1_trend == 0 or h1_adx < ADX_MIN_TREND:
            return signals

        # ── PULLBACK DETECTION (5m) ──────────────────────────────
        # Per-symbol RSI thresholds: more volatile assets (SOL, ADA) have wider
        # RSI swings, so we tighten the threshold to require deeper pullbacks.
        # BTC: 35/65 (standard), ETH: 33/67, SOL/ADA: 30/70 (deeper pullback)
        vol_pctile = float(bar.get("volatility_percentile", 50))
        if vol_pctile > 70:
            # High vol regime: require deeper pullback
            rsi_os = max(RSI_OVERSOLD - 5, 25)
            rsi_ob = min(RSI_OVERBOUGHT + 5, 75)
        elif vol_pctile < 30:
            # Low vol: accept shallower pullback
            rsi_os = min(RSI_OVERSOLD + 3, 42)
            rsi_ob = max(RSI_OVERBOUGHT - 3, 58)
        else:
            rsi_os = RSI_OVERSOLD
            rsi_ob = RSI_OVERBOUGHT

        bull_setup = h1_trend == 1 and rsi < rsi_os
        bear_setup = h1_trend == -1 and rsi > rsi_ob

        if not bull_setup and not bear_setup:
            return signals

        # ── CONFIRMATION (at least 1 INDEPENDENT signal) ────────
        # NOTE: BB touch and Z-score are colinear (both measure distance from mean
        # in std units). Only use BB touch as the stronger structural signal.
        vol_avg = float(m5["volume"].tail(20).mean()) if len(m5) >= 20 else 0
        has_bb_touch = (close_5m <= bb_lower) if bull_setup else (close_5m >= bb_upper)
        has_vol_dry = vol_avg > 0 and volume < vol_avg * 0.8  # Low volume = exhaustion

        obi = kwargs.get("obi")
        has_obi = False
        if obi:
            has_obi = (obi.weighted_imbalance > 0.05) if bull_setup else (obi.weighted_imbalance < -0.05)

        # Candle rejection wick (independent of price/std relationship)
        has_rejection = self._has_rejection_wick(bar, bull_setup)

        confirmations = sum([has_bb_touch, has_vol_dry, has_obi, has_rejection])
        if confirmations < MIN_CONFIRMATIONS:
            return signals

        # ── SIZING & SL/TP (per-symbol from SymbolConfig) ────────
        kelly_pct = kwargs.get("kelly_risk_pct")
        risk_pct = kelly_pct if kelly_pct else self.trading_config.risk_per_trade_pct

        # Use per-symbol SL/TP from SymbolConfig (always set now)
        sl_mult = sym_config.mr_atr_mult_sl
        tp_mult = sym_config.mr_atr_mult_tp

        if bull_setup:
            stop_loss = price - sl_mult * atr
            take_profit = price + tp_mult * atr
            side = Side.BUY
            trigger = "trend_pullback_bull"
        else:
            stop_loss = price + sl_mult * atr
            take_profit = price - tp_mult * atr
            side = Side.SELL
            trigger = "trend_pullback_bear"

        # Check net R:R after fees
        rt_cost = price * 14 / 10000  # 14 bps round-trip
        net_profit = tp_mult * atr - rt_cost
        if net_profit <= 0:
            return signals

        size = self._calc_position_size(
            allocated_capital, price, stop_loss,
            sym_config.leverage, kelly_risk_pct=risk_pct,
        )
        size_usd = size * price

        if size_usd < 20:
            return signals

        # ── SCORE (higher with more confirmations) ────────────────
        score = 0.40 + confirmations * 0.15
        strength = min(score, 1.0)

        self._states[symbol] = MRState(
            entry_time=now,
            entry_bar_idx=len(self._resampled.get(symbol, pd.DataFrame())),
            sl_mult=sl_mult,
            tp_mult=tp_mult,
        )

        logger.info("mr_entry", symbol=symbol, side=side.value,
                     trigger=trigger, h1_trend=h1_trend, h1_adx=round(h1_adx, 1),
                     rsi_5m=round(rsi, 1), adx_5m=round(adx, 1),
                     zscore=round(zscore, 2), confirmations=confirmations,
                     atr_bps=round(atr / price * 10000, 1))

        signals.append(Signal(
            strategy=self.strategy_type, symbol=symbol,
            side=side, strength=strength,
            entry_price=price, stop_loss=stop_loss, take_profit=take_profit,
            size_usd=size_usd,
            metadata={
                "trigger": trigger,
                "h1_trend": h1_trend,
                "h1_adx": round(h1_adx, 1),
                "rsi_5m": round(rsi, 1),
                "adx_5m": round(adx, 1),
                "zscore": round(zscore, 2),
                "atr": round(atr, 2),
                "atr_bps": round(atr / price * 10000, 1),
                "confirmations": confirmations,
                "has_bb_touch": has_bb_touch,
                "has_vol_dry": has_vol_dry,
                "has_obi": has_obi,
                "has_rejection": has_rejection,
                "obi": round(obi.weighted_imbalance, 3) if obi else 0,
                "sl_mult": SL_ATR_MULT,
                "tp_mult": TP_ATR_MULT,
            },
        ))
        return signals

    def _check_exit(
        self, symbol: str, m5: pd.DataFrame,
        position: Position, snapshot: MarketSnapshot,
    ) -> Optional[Signal]:
        """Check exit conditions."""
        state = self._states.get(symbol)
        if not state:
            return None

        price = snapshot.price if snapshot.price > 0 else float(m5.iloc[-1]["close"])
        if price <= 0:
            return None

        atr = float(m5.iloc[-1].get("atr", 0))
        if pd.isna(atr) or atr <= 0:
            return None

        current_bar_count = len(self._resampled.get(symbol, pd.DataFrame()))
        bars_held = current_bar_count - state.entry_bar_idx
        exit_reason = None

        # Software SL/TP safety net — in case exchange orders fail
        if position.entry_price > 0:
            pnl_atr = ((price - position.entry_price) / atr if position.side == Side.BUY
                       else (position.entry_price - price) / atr)

            # SL safety net: exit if price breached beyond per-position SL
            if pnl_atr < -(state.sl_mult + 0.2):
                exit_reason = "software_sl_safety"

            # TP safety net: exit if price reached per-position TP level
            if pnl_atr >= state.tp_mult:
                exit_reason = "software_tp_safety"

            # ── Trailing stop with progressive tightening ────────
            # Track peak PnL. Once activated, trail behind peak.
            # After TRAIL_TIGHT_AFTER_BARS, tighten the trail to lock
            # more profit on slow-moving positions that are aging out.
            if pnl_atr > state.best_pnl_atr:
                state.best_pnl_atr = pnl_atr

            if state.best_pnl_atr >= TRAIL_ACTIVATE_ATR:
                state.trail_active = True

            if state.trail_active:
                # Progressive: tighten trail distance after N bars
                if bars_held >= TRAIL_TIGHT_AFTER_BARS:
                    trail_dist = TRAIL_TIGHT_DISTANCE
                else:
                    trail_dist = TRAIL_DISTANCE_ATR
                trail_level = state.best_pnl_atr - trail_dist
                if pnl_atr <= trail_level:
                    exit_reason = f"trailing_stop_{state.best_pnl_atr:.1f}atr_peak"

        # Stale position check: if price hasn't moved significantly in 24h, free the capital.
        # Trades exit by PRICE (SL/TP/trailing), not by arbitrary time limits.
        stale_bars = int(STALE_HOURS * 60 / RESAMPLE_MINUTES)  # 24h in 5m bars = 288
        if bars_held >= stale_bars and position.entry_price > 0:
            abs_move = abs(price - position.entry_price) / atr if atr > 0 else 0
            if abs_move < STALE_ATR_THRESHOLD:
                exit_reason = "stale_position_24h"

        if not exit_reason:
            return None

        close_side = Side.SELL if position.side == Side.BUY else Side.BUY
        size_usd = position.notional if position.notional > 0 else position.size * price

        self._states.pop(symbol, None)
        # Use bar timestamp in backtest mode (wall-clock is meaningless)
        if self.backtest_mode:
            raw_ts = snapshot.timestamp if snapshot.timestamp > 1e9 else 0
            self._last_exit_time[symbol] = raw_ts / 1000 if raw_ts > 1e12 else raw_ts
        else:
            self._last_exit_time[symbol] = _time.time()

        logger.info("mr_exit", symbol=symbol, reason=exit_reason, bars_held=bars_held)

        return Signal(
            strategy=self.strategy_type, symbol=symbol,
            side=close_side, strength=1.0,
            entry_price=price, stop_loss=price, take_profit=price,
            size_usd=size_usd,
            metadata={"action": "exit_mean_reversion", "exit_reason": exit_reason},
        )

    @staticmethod
    def _has_rejection_wick(bar: pd.Series, is_bull: bool) -> bool:
        """Check if candle has a rejection wick (exhaustion signal)."""
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
            return (body_bottom - low) / candle_range > 0.50
        else:
            return (high - body_top) / candle_range > 0.50

    def _resample_5m(self, symbol: str, df: pd.DataFrame) -> None:
        """Resample 1m bars to 5m with indicators."""
        if len(df) < RESAMPLE_MINUTES * MIN_BARS_5M:
            return

        # Use last N bars for resampling
        max_input = RESAMPLE_MINUTES * RESAMPLE_BUFFER
        tail = df.tail(max_input).copy().reset_index(drop=True)
        n = len(tail) // RESAMPLE_MINUTES * RESAMPLE_MINUTES
        trim = tail.tail(n).copy().reset_index(drop=True)

        groups = np.arange(len(trim)) // RESAMPLE_MINUTES
        resampled = trim.groupby(groups).agg({
            "open": "first", "high": "max", "low": "min",
            "close": "last", "volume": "sum",
        }).reset_index(drop=True)

        self._resampled[symbol] = Indicators.compute_all(resampled)

    def _update_h1_trend(self, symbol: str, df: pd.DataFrame) -> None:
        """Compute 1H trend from 1m bars."""
        if len(df) < 60 * 6:  # Need 6 hours minimum (matches Binance seed)
            self._h1_trend[symbol] = 0
            self._h1_adx[symbol] = 0
            return

        max_input = 60 * 100  # 100 hours
        tail = df.tail(max_input).copy().reset_index(drop=True)
        n = len(tail) // 60 * 60
        trim = tail.tail(n).copy().reset_index(drop=True)

        groups = np.arange(len(trim)) // 60
        h1 = trim.groupby(groups).agg({
            "open": "first", "high": "max", "low": "min",
            "close": "last", "volume": "sum",
        }).reset_index(drop=True)

        h1 = Indicators.compute_all(h1)
        if len(h1) < 5:  # Minimum 5 hourly bars for EMA to start converging
            self._h1_trend[symbol] = 0
            self._h1_adx[symbol] = 0
            return

        last = h1.iloc[-1]
        ema12 = float(last.get("ema_12", 0))
        ema26 = float(last.get("ema_26", 0))
        adx = float(last.get("adx", 0))

        if ema12 > 0 and ema26 > 0:
            self._h1_trend[symbol] = 1 if ema12 > ema26 else -1
        else:
            self._h1_trend[symbol] = 0
        self._h1_adx[symbol] = adx
