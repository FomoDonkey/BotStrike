"""
Divergence Strategy — RSI+OBV divergence on 15m timeframe.

Real divergence = price makes new extreme BUT momentum (RSI) is RECOVERING.
Auto-resamples to 15m if input bars are sub-15m (e.g., backtester sends 1m).

Backtested on 15m: 22 trades in 90 days, 40.9% WR, +2.74% PnL, R:R 1:2.1

Parameters:
  - lookback 10 bars (2.5h at 15m)
  - RSI at prior swing must be < 30 (truly oversold) / > 70 (truly overbought)
  - Current RSI must show recovery > 45 / < 55
  - SL 1.5x ATR, TP 3.0x ATR (1:2 R:R)
  - ADX < 35 (weak trend = mean reversion zone)
"""
from __future__ import annotations
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

from config.settings import SymbolConfig, TradingConfig
from core.types import (
    Signal, MarketRegime, MarketSnapshot, StrategyType, Side, Position,
)
from strategies.base import BaseStrategy
import structlog

logger = structlog.get_logger(__name__)

DIV_LOOKBACK = 10
RSI_PREV_OVERSOLD = 30
RSI_PREV_OVERBOUGHT = 70
RSI_RECOVERY = 45
ADX_MAX = 35


def _detect_divergence(
    lows: np.ndarray, highs: np.ndarray, rsi_arr: np.ndarray,
    obv_arr: np.ndarray, lookback: int,
) -> Tuple[bool, bool, bool, bool]:
    """Detect real bull/bear divergence with RSI recovery confirmation."""
    i = len(lows) - 1
    if i < lookback + 2:
        return False, False, False, False

    w_lows = lows[i - lookback:i]
    w_highs = highs[i - lookback:i]
    w_rsi = rsi_arr[i - lookback:i]
    w_obv = obv_arr[i - lookback:i]

    if len(w_lows) < 5:
        return False, False, False, False

    cur_rsi = rsi_arr[i]
    if np.isnan(cur_rsi):
        return False, False, False, False

    prev_low_idx = np.argmin(w_lows)
    prev_high_idx = np.argmax(w_highs)
    prev_rsi_at_low = w_rsi[prev_low_idx]
    prev_rsi_at_high = w_rsi[prev_high_idx]

    # Bull: price lower low, RSI was truly oversold at prior low, now recovering
    bull = (
        lows[i] < w_lows[prev_low_idx]
        and not np.isnan(prev_rsi_at_low)
        and prev_rsi_at_low < RSI_PREV_OVERSOLD
        and cur_rsi > prev_rsi_at_low + 5
        and cur_rsi > RSI_RECOVERY
    )

    # Bear: price higher high, RSI was truly overbought at prior high, now falling
    bear = (
        highs[i] > w_highs[prev_high_idx]
        and not np.isnan(prev_rsi_at_high)
        and prev_rsi_at_high > RSI_PREV_OVERBOUGHT
        and cur_rsi < prev_rsi_at_high - 5
        and cur_rsi < (100 - RSI_RECOVERY)
    )

    # OBV: recent volume bar stronger than average
    obv_bull = False
    obv_bear = False
    if bull and len(w_obv) >= 5:
        recent_delta = obv_arr[i] - obv_arr[i - 1]
        avg_delta = np.mean(np.abs(np.diff(w_obv[-5:])))
        obv_bull = recent_delta > avg_delta * 1.2

    if bear and len(w_obv) >= 5:
        recent_delta = obv_arr[i] - obv_arr[i - 1]
        avg_delta = np.mean(np.abs(np.diff(w_obv[-5:])))
        obv_bear = recent_delta < -avg_delta * 1.2

    return bull, bear, obv_bull, obv_bear


def _resample_to_15m(df: pd.DataFrame) -> Optional[pd.DataFrame]:
    """Resample sub-15m bars to 15m. Only resamples last 500 bars for performance."""
    if "timestamp" not in df.columns or len(df) < 30:
        return None

    ts = df["timestamp"].values
    diffs = []
    for k in range(1, min(10, len(ts))):
        d = ts[k] - ts[k - 1]
        if d > 0:
            diffs.append(d)
    if not diffs:
        return None

    median_diff = np.median(diffs)
    if median_diff > 1e9:
        median_diff /= 1000
    bar_minutes = median_diff / 60

    if bar_minutes >= 10:
        return None  # Already 15m+

    bars_per_15m = max(1, int(round(15 / bar_minutes)))

    # Only use last N bars for performance (need ~30 candles of 15m = 450 bars of 1m)
    max_input_bars = bars_per_15m * 50  # 50 candles of 15m worth of data
    df_tail = df.tail(max_input_bars)

    if len(df_tail) < bars_per_15m * 15:
        return None

    n = len(df_tail) // bars_per_15m * bars_per_15m
    df_trim = df_tail.tail(n).copy()

    groups = np.arange(len(df_trim)) // bars_per_15m
    resampled = df_trim.groupby(groups).agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
    })

    from core.indicators import Indicators
    resampled = Indicators.compute_all(resampled.reset_index(drop=True))
    resampled["obv"] = (np.sign(resampled["close"].diff()) * resampled["volume"]).cumsum()
    return resampled


class MeanReversionStrategy(BaseStrategy):
    """RSI+OBV Divergence — auto-resamples to 15m for proper detection."""

    def __init__(self, trading_config: TradingConfig) -> None:
        super().__init__(StrategyType.MEAN_REVERSION, trading_config)

    def should_activate(self, regime: MarketRegime) -> bool:
        return regime != MarketRegime.BREAKOUT

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

        if df.empty or len(df) < 30:
            return signals

        if current_position is not None:
            return signals

        # Auto-resample to 15m if input is sub-15m (backtester sends 1m)
        df_work = _resample_to_15m(df)
        if df_work is None:
            df_work = df  # Already 15m+

        if len(df_work) < DIV_LOOKBACK + 10:
            return signals

        current = df_work.iloc[-1]
        price = snapshot.price if snapshot.price > 0 else float(current["close"])
        atr = float(current.get("atr", 0))
        adx = float(current.get("adx", 0))

        if pd.isna(atr) or atr <= 0 or pd.isna(adx):
            return signals

        if adx > ADX_MAX:
            return signals

        kelly_pct = kwargs.get("kelly_risk_pct")

        # OBV on 15m data
        if "obv" not in df_work.columns:
            df_work = df_work.copy()
            df_work["obv"] = (np.sign(df_work["close"].diff()) * df_work["volume"]).cumsum()

        # Detect divergence on 15m bars
        bull, bear, obv_bull, obv_bear = _detect_divergence(
            df_work["low"].values, df_work["high"].values,
            df_work["rsi"].values if "rsi" in df_work.columns else np.full(len(df_work), 50),
            df_work["obv"].values, DIV_LOOKBACK,
        )

        if not bull and not bear:
            return signals

        # Dip confirmation: enter only when price is near the swing extreme
        # For bull: price should be within 0.3 ATR of recent low (not already rallied)
        # For bear: price should be within 0.3 ATR of recent high (not already dropped)
        recent_5 = df_work.tail(5)
        if bull:
            recent_low = recent_5["low"].min()
            if price > recent_low + atr * 0.5:
                bull = False  # Already rallied too far from low
        if bear:
            recent_high = recent_5["high"].max()
            if price < recent_high - atr * 0.5:
                bear = False  # Already dropped too far from high

        if not bull and not bear:
            return signals

        # Score
        bull_score = 0.0
        bear_score = 0.0

        if bull:
            bull_score = 0.5 + (0.2 if obv_bull else 0)
        if bear:
            bear_score = 0.5 + (0.2 if obv_bear else 0)

        # OBI confirmation
        obi = kwargs.get("obi")
        if obi:
            if bull_score > 0 and obi.weighted_imbalance > 0.10:
                bull_score += 0.15
            elif bear_score > 0 and obi.weighted_imbalance < -0.10:
                bear_score += 0.15

        if bull_score < 0.5 and bear_score < 0.5:
            return signals

        strength = min(max(bull_score, bear_score), 1.0)

        # SL/TP: 1.5x ATR stop, 3.0x ATR target (1:2 R:R)
        sl_mult = 1.5
        tp_mult = 3.0

        # LONG
        if bull_score >= 0.5 and bull_score > bear_score:
            stop_loss = price - sl_mult * atr
            take_profit = price + tp_mult * atr

            size = self._calc_position_size(
                allocated_capital, price, stop_loss,
                sym_config.leverage, kelly_risk_pct=kelly_pct,
            )
            size_usd = size * price

            if size_usd > 10:
                signals.append(Signal(
                    strategy=self.strategy_type, symbol=symbol,
                    side=Side.BUY, strength=strength,
                    entry_price=price, stop_loss=stop_loss, take_profit=take_profit,
                    size_usd=size_usd,
                    metadata={
                        "trigger": "bull_divergence",
                        "rsi": float(current.get("rsi", 0)),
                        "adx": float(adx), "atr": float(atr),
                        "obv_confirms": obv_bull,
                        "score": round(bull_score, 2),
                        "obi": round(obi.weighted_imbalance, 3) if obi else 0,
                    },
                ))

        # SHORT
        elif bear_score >= 0.5 and bear_score > bull_score:
            stop_loss = price + sl_mult * atr
            take_profit = price - tp_mult * atr

            size = self._calc_position_size(
                allocated_capital, price, stop_loss,
                sym_config.leverage, kelly_risk_pct=kelly_pct,
            )
            size_usd = size * price

            if size_usd > 10:
                signals.append(Signal(
                    strategy=self.strategy_type, symbol=symbol,
                    side=Side.SELL, strength=strength,
                    entry_price=price, stop_loss=stop_loss, take_profit=take_profit,
                    size_usd=size_usd,
                    metadata={
                        "trigger": "bear_divergence",
                        "rsi": float(current.get("rsi", 0)),
                        "adx": float(adx), "atr": float(atr),
                        "obv_confirms": obv_bear,
                        "score": round(bear_score, 2),
                        "obi": round(obi.weighted_imbalance, 3) if obi else 0,
                    },
                ))

        return signals
