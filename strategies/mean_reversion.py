"""
Multi-Timeframe Divergence Strategy — RSI divergence across 1D, 4H, 1H, 15m.

Scans for divergences on multiple timeframes simultaneously:
  - 1D: Rare, highest conviction. Position size 3% risk.
  - 4H: Regular, high conviction. Position size 2% risk.
  - 1H: Frequent, medium conviction. Position size 1.5% risk.
  - 15m: Most frequent, base conviction. Position size 1% risk.

Higher TF divergence = stronger signal = larger position.
Uses Binance kline data fetched on demand (cached 5min).

Entry: RSI divergence detected + OBV/OBI confirmation + dip proximity
Exit: SL/TP with R:R scaled by timeframe (higher TF = wider stops, bigger targets)
"""
from __future__ import annotations
from typing import List, Optional, Tuple, Dict
from dataclasses import dataclass

import numpy as np
import pandas as pd
import time as _time
import asyncio

from config.settings import SymbolConfig, TradingConfig
from core.types import (
    Signal, MarketRegime, MarketSnapshot, StrategyType, Side, Position,
)
from strategies.base import BaseStrategy
import structlog

logger = structlog.get_logger(__name__)

# ── Timeframe Configuration ──────────────────────────────────────
@dataclass
class TFConfig:
    """Configuration per timeframe for divergence detection."""
    name: str
    interval: str           # Binance interval string
    lookback: int           # Bars to look back for divergence
    rsi_oversold: float     # RSI threshold for oversold
    rsi_overbought: float   # RSI threshold for overbought
    rsi_recovery: float     # RSI recovery threshold
    adx_max: float          # Max ADX (trend filter)
    sl_mult: float          # Stop loss ATR multiplier
    tp_mult: float          # Take profit ATR multiplier
    risk_pct: float         # Risk per trade (overrides Kelly ceiling)
    strength_base: float    # Base signal strength
    cache_ttl: int          # Cache TTL in seconds
    min_bars: int           # Minimum bars needed


TF_CONFIGS: Dict[str, TFConfig] = {
    "1d": TFConfig(
        name="1D", interval="1d", lookback=14,
        rsi_oversold=30, rsi_overbought=70, rsi_recovery=40,
        adx_max=40, sl_mult=2.5, tp_mult=5.0,     # Wider SL for overnight gaps
        risk_pct=0.015, strength_base=0.95,         # 1.5% risk (was 3% — too much for $300)
        cache_ttl=900, min_bars=30,
    ),
    "4h": TFConfig(
        name="4H", interval="4h", lookback=12,
        rsi_oversold=30, rsi_overbought=70, rsi_recovery=42,
        adx_max=38, sl_mult=1.8, tp_mult=4.0,
        risk_pct=0.015, strength_base=0.85,         # 1.5% (was 2%)
        cache_ttl=600, min_bars=30,
    ),
    "1h": TFConfig(
        name="1H", interval="1h", lookback=10,
        rsi_oversold=28, rsi_overbought=72, rsi_recovery=43,  # Tighter than 15m (less noise)
        adx_max=36, sl_mult=1.5, tp_mult=3.5,
        risk_pct=0.015, strength_base=0.75, cache_ttl=300, min_bars=25,
    ),
    "15m": TFConfig(
        name="15m", interval="15m", lookback=10,
        rsi_oversold=25, rsi_overbought=75, rsi_recovery=45,  # Stricter — more noise at 15m
        adx_max=35, sl_mult=1.5, tp_mult=3.0,
        risk_pct=0.01, strength_base=0.65, cache_ttl=180, min_bars=20,
    ),
}

# Binance symbol mapping
_SYMBOL_MAP = {
    "BTC-USD": "BTCUSDT",
    "ETH-USD": "ETHUSDT",
    "ADA-USD": "ADAUSDT",
}


# ── Kline Cache ──────────────────────────────────────────────────
_kline_cache: Dict[str, Tuple[float, pd.DataFrame]] = {}  # key -> (timestamp, df)


async def _fetch_binance_klines(symbol: str, interval: str, limit: int = 100) -> Optional[pd.DataFrame]:
    """Fetch klines from Binance API with caching."""
    cache_key = f"{symbol}_{interval}"
    now = _time.time()

    tf_cfg = TF_CONFIGS.get(interval)
    ttl = tf_cfg.cache_ttl if tf_cfg else 300

    if cache_key in _kline_cache:
        ts, df = _kline_cache[cache_key]
        if now - ts < ttl:
            return df

    try:
        import aiohttp
        binance_sym = _SYMBOL_MAP.get(symbol, symbol.replace("-", ""))
        url = f"https://api.binance.com/api/v3/klines?symbol={binance_sym}&interval={interval}&limit={limit}"

        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()

        if not data:
            return None

        rows = []
        for k in data:
            rows.append({
                "timestamp": int(k[0]) / 1000,
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            })

        df = pd.DataFrame(rows)

        # Compute indicators
        from core.indicators import Indicators
        df = Indicators.compute_all(df)
        df["obv"] = (np.sign(df["close"].diff()) * df["volume"]).cumsum()

        _kline_cache[cache_key] = (now, df)
        return df

    except Exception as e:
        logger.debug("kline_fetch_failed", symbol=symbol, interval=interval, error=str(e))
        # Return cached if available
        if cache_key in _kline_cache:
            return _kline_cache[cache_key][1]
        return None


def _fetch_klines_sync(symbol: str, interval: str, limit: int = 100) -> Optional[pd.DataFrame]:
    """Synchronous wrapper for kline fetching (for use in generate_signals)."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # We're inside an async context — use a thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, _fetch_binance_klines(symbol, interval, limit))
                return future.result(timeout=15)
        else:
            return asyncio.run(_fetch_binance_klines(symbol, interval, limit))
    except Exception:
        # Fallback: check cache
        cache_key = f"{symbol}_{interval}"
        if cache_key in _kline_cache:
            return _kline_cache[cache_key][1]
        return None


# ── Divergence Detection ─────────────────────────────────────────
def _detect_divergence(
    lows: np.ndarray, highs: np.ndarray, rsi_arr: np.ndarray,
    obv_arr: np.ndarray, cfg: TFConfig,
) -> Tuple[bool, bool, bool, bool]:
    """Detect real bull/bear divergence with RSI recovery confirmation."""
    i = len(lows) - 1
    lookback = cfg.lookback

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

    # Bull: price lower low, RSI was oversold at prior low, now recovering
    bull = (
        lows[i] < w_lows[prev_low_idx]
        and not np.isnan(prev_rsi_at_low)
        and prev_rsi_at_low < cfg.rsi_oversold
        and cur_rsi > prev_rsi_at_low + 5
        and cur_rsi > cfg.rsi_recovery
    )

    # Bear: price higher high, RSI was overbought at prior high, now falling
    bear = (
        highs[i] > w_highs[prev_high_idx]
        and not np.isnan(prev_rsi_at_high)
        and prev_rsi_at_high > cfg.rsi_overbought
        and cur_rsi < prev_rsi_at_high - 5
        and cur_rsi < (100 - cfg.rsi_recovery)
    )

    # OBV confirmation
    obv_bull = False
    obv_bear = False
    if bull and len(w_obv) >= 5:
        recent_delta = obv_arr[i] - obv_arr[i - 1]
        avg_delta = np.mean(np.abs(np.diff(w_obv[-5:])))
        if avg_delta > 0:
            obv_bull = recent_delta > avg_delta * 1.2

    if bear and len(w_obv) >= 5:
        recent_delta = obv_arr[i] - obv_arr[i - 1]
        avg_delta = np.mean(np.abs(np.diff(w_obv[-5:])))
        if avg_delta > 0:
            obv_bear = recent_delta < -avg_delta * 1.2

    return bull, bear, obv_bull, obv_bear


# ── Resample helper (for backtester 1m input) ────────────────────
def _resample(df: pd.DataFrame, target_minutes: int) -> Optional[pd.DataFrame]:
    """Resample bars to target timeframe."""
    if "timestamp" not in df.columns or len(df) < 30:
        return None

    ts = df["timestamp"].values
    diffs = [ts[k] - ts[k-1] for k in range(1, min(10, len(ts))) if ts[k] - ts[k-1] > 0]
    if not diffs:
        return None

    median_diff = np.median(diffs)
    if median_diff > 1e9:
        median_diff /= 1000
    bar_minutes = median_diff / 60

    if bar_minutes >= target_minutes * 0.8:
        return None  # Already at or above target

    bars_per_target = max(1, int(round(target_minutes / bar_minutes)))
    max_input = bars_per_target * 60
    df_tail = df.tail(max_input)

    if len(df_tail) < bars_per_target * 15:
        return None

    n = len(df_tail) // bars_per_target * bars_per_target
    df_trim = df_tail.tail(n).copy()

    groups = np.arange(len(df_trim)) // bars_per_target
    resampled = df_trim.groupby(groups).agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
    })

    from core.indicators import Indicators
    resampled = Indicators.compute_all(resampled.reset_index(drop=True))
    resampled["obv"] = (np.sign(resampled["close"].diff()) * resampled["volume"]).cumsum()
    return resampled


# ── Strategy ─────────────────────────────────────────────────────
class MeanReversionStrategy(BaseStrategy):
    """Multi-Timeframe RSI Divergence — scans 1D, 4H, 1H, 15m simultaneously."""

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

        price = snapshot.price if snapshot.price > 0 else float(df.iloc[-1]["close"])
        kelly_pct = kwargs.get("kelly_risk_pct")
        obi = kwargs.get("obi")

        # Z-score MR disabled — does not work on BTC 1m (not stationary).
        # See tasks/lessons.md line 11. Only RSI divergence is viable for MR.

        # ── Multi-TF RSI Divergence (rare, high conviction) ──
        best_signal = None
        best_tf_priority = -1

        for tf_key, cfg in TF_CONFIGS.items():
            tf_df = self._get_tf_data(symbol, df, tf_key, cfg)
            if tf_df is None or len(tf_df) < cfg.min_bars:
                continue

            current_bar = tf_df.iloc[-1]
            atr = float(current_bar.get("atr", 0))
            adx = float(current_bar.get("adx", 0))

            if pd.isna(atr) or atr <= 0 or pd.isna(adx):
                continue

            if adx > cfg.adx_max:
                continue

            # Ensure OBV column exists
            if "obv" not in tf_df.columns:
                tf_df = tf_df.copy()
                tf_df["obv"] = (np.sign(tf_df["close"].diff()) * tf_df["volume"]).cumsum()

            rsi_col = tf_df["rsi"].values if "rsi" in tf_df.columns else np.full(len(tf_df), 50)

            bull, bear, obv_bull, obv_bear = _detect_divergence(
                tf_df["low"].values, tf_df["high"].values,
                rsi_col, tf_df["obv"].values, cfg,
            )

            if not bull and not bear:
                continue

            # Dip confirmation
            recent = tf_df.tail(5)
            if bull:
                recent_low = recent["low"].min()
                if price > recent_low + atr * 0.5:
                    bull = False
            if bear:
                recent_high = recent["high"].max()
                if price < recent_high - atr * 0.5:
                    bear = False

            if not bull and not bear:
                continue

            # Score
            score = cfg.strength_base
            if bull and obv_bull:
                score += 0.1
            if bear and obv_bear:
                score += 0.1
            if obi:
                if bull and obi.weighted_imbalance > 0.10:
                    score += 0.08
                elif bear and obi.weighted_imbalance < -0.10:
                    score += 0.08

            # Priority: 1D=4, 4H=3, 1H=2, 15m=1
            tf_priority = {"1d": 4, "4h": 3, "1h": 2, "15m": 1}[tf_key]

            if tf_priority > best_tf_priority:
                best_tf_priority = tf_priority
                best_signal = {
                    "tf": tf_key,
                    "cfg": cfg,
                    "bull": bull,
                    "bear": bear,
                    "score": score,
                    "atr": atr,
                    "adx": adx,
                    "rsi": float(current_bar.get("rsi", 0)),
                    "obv_bull": obv_bull,
                    "obv_bear": obv_bear,
                }

            logger.info(
                "mr_divergence_detected",
                symbol=symbol,
                timeframe=cfg.name,
                side="BULL" if bull else "BEAR",
                rsi=float(current_bar.get("rsi", 0)),
                adx=round(adx, 1),
                score=round(score, 2),
            )

        if best_signal is None:
            return signals

        # Build signal from best (highest TF) divergence
        cfg = best_signal["cfg"]
        atr = best_signal["atr"]
        score = best_signal["score"]
        strength = min(score, 1.0)

        # Override risk_pct based on TF (higher TF = bigger position)
        risk_override = cfg.risk_pct

        if best_signal["bull"]:
            stop_loss = price - cfg.sl_mult * atr
            take_profit = price + cfg.tp_mult * atr

            size = self._calc_position_size(
                allocated_capital, price, stop_loss,
                sym_config.leverage, kelly_risk_pct=risk_override,
            )
            size_usd = size * price

            if size_usd > 10:
                signals.append(Signal(
                    strategy=self.strategy_type, symbol=symbol,
                    side=Side.BUY, strength=strength,
                    entry_price=price, stop_loss=stop_loss, take_profit=take_profit,
                    size_usd=size_usd,
                    metadata={
                        "trigger": f"bull_divergence_{best_signal['tf']}",
                        "timeframe": cfg.name,
                        "rsi": best_signal["rsi"],
                        "adx": best_signal["adx"],
                        "atr": atr,
                        "obv_confirms": best_signal["obv_bull"],
                        "score": round(score, 2),
                        "sl_mult": cfg.sl_mult,
                        "tp_mult": cfg.tp_mult,
                        "risk_pct": risk_override,
                        "obi": round(obi.weighted_imbalance, 3) if obi else 0,
                    },
                ))

        elif best_signal["bear"]:
            stop_loss = price + cfg.sl_mult * atr
            take_profit = price - cfg.tp_mult * atr

            size = self._calc_position_size(
                allocated_capital, price, stop_loss,
                sym_config.leverage, kelly_risk_pct=risk_override,
            )
            size_usd = size * price

            if size_usd > 10:
                signals.append(Signal(
                    strategy=self.strategy_type, symbol=symbol,
                    side=Side.SELL, strength=strength,
                    entry_price=price, stop_loss=stop_loss, take_profit=take_profit,
                    size_usd=size_usd,
                    metadata={
                        "trigger": f"bear_divergence_{best_signal['tf']}",
                        "timeframe": cfg.name,
                        "rsi": best_signal["rsi"],
                        "adx": best_signal["adx"],
                        "atr": atr,
                        "obv_confirms": best_signal["obv_bear"],
                        "score": round(score, 2),
                        "sl_mult": cfg.sl_mult,
                        "tp_mult": cfg.tp_mult,
                        "risk_pct": risk_override,
                        "obi": round(obi.weighted_imbalance, 3) if obi else 0,
                    },
                ))

        return signals

    def _check_zscore_entry(
        self,
        symbol: str,
        df: pd.DataFrame,
        price: float,
        regime: MarketRegime,
        sym_config: SymbolConfig,
        allocated_capital: float,
        kelly_pct: Optional[float],
        obi,
    ) -> Optional[Signal]:
        """Z-score mean reversion: buy when price is N std below SMA, sell when above.

        This is the bread-and-butter MR signal — fires more frequently than
        RSI divergence, with tighter stops and smaller position sizes.
        Only activates in RANGING regime (highest edge for MR).
        """
        if regime != MarketRegime.RANGING:
            return None

        if len(df) < 50:
            return None

        current = df.iloc[-1]
        atr = float(current.get("atr", 0))
        adx = float(current.get("adx", 0))
        rsi = float(current.get("rsi", 50))

        if pd.isna(atr) or atr <= 0 or pd.isna(adx):
            return None

        # Only in low-trend environment (ADX < 30)
        if adx > 30:
            return None

        # Z-score: (price - SMA) / std
        close = df["close"].values
        lookback = min(100, len(close))
        window = close[-lookback:]
        sma = float(np.mean(window))
        std = float(np.std(window))
        if std <= 0:
            return None

        zscore = (price - sma) / std

        # Entry thresholds
        entry_z = 2.0     # Enter at 2 std deviation
        exit_z = 0.5      # TP at 0.5 std (mean reversion target)

        # RSI confirmation: don't buy if RSI not oversold, don't sell if not overbought
        bull = zscore < -entry_z and rsi < 35
        bear = zscore > entry_z and rsi > 65

        if not bull and not bear:
            logger.debug("mr_zscore_skip", symbol=symbol,
                         zscore=round(zscore, 2), rsi=round(rsi, 1), adx=round(adx, 1))
            return None

        # Position sizing — conservative for z-score (smaller than divergence)
        risk_pct = 0.01  # 1% risk per z-score trade
        sl_mult = 1.5     # Tighter SL than divergence
        tp_mult = 2.5     # Mean reversion target

        if bull:
            stop_loss = price - sl_mult * atr
            take_profit = price + tp_mult * atr
            side = Side.BUY
            trigger = "zscore_bull"
        else:
            stop_loss = price + sl_mult * atr
            take_profit = price - tp_mult * atr
            side = Side.SELL
            trigger = "zscore_bear"

        size = self._calc_position_size(
            allocated_capital, price, stop_loss,
            2,  # Conservative leverage for z-score
            kelly_risk_pct=risk_pct,
        )
        size_usd = size * price
        if size_usd < 10:
            return None

        strength = min(abs(zscore) / 3.0, 1.0)  # Normalize strength

        logger.info("mr_zscore_entry", symbol=symbol, side=side.value,
                    zscore=round(zscore, 2), rsi=round(rsi, 1), adx=round(adx, 1),
                    size_usd=round(size_usd, 2))

        return Signal(
            strategy=self.strategy_type, symbol=symbol,
            side=side, strength=strength,
            entry_price=price, stop_loss=stop_loss, take_profit=take_profit,
            size_usd=size_usd,
            metadata={
                "trigger": trigger,
                "zscore": round(zscore, 2),
                "rsi": round(rsi, 1),
                "adx": round(adx, 1),
                "sma": round(sma, 2),
                "std": round(std, 4),
                "risk_pct": risk_pct,
                "obi": round(obi.weighted_imbalance, 3) if obi else 0,
            },
        )

    def _get_tf_data(
        self, symbol: str, df: pd.DataFrame, tf_key: str, cfg: TFConfig,
    ) -> Optional[pd.DataFrame]:
        """Get dataframe for a specific timeframe.

        For 15m: resample from input df (which may be 1m or 15m).
        For 1H/4H/1D: fetch from Binance API (cached).
        """
        if tf_key == "15m":
            # Try resample from input bars
            resampled = _resample(df, 15)
            if resampled is not None:
                return resampled
            # If input is already 15m, use directly
            return df if len(df) >= cfg.min_bars else None

        # Higher TFs: fetch from Binance (cached)
        tf_minutes = {"1h": 60, "4h": 240, "1d": 1440}
        target_min = tf_minutes.get(tf_key)

        if target_min is None:
            return None

        # Try resample from 1m input first (backtester)
        if len(df) > target_min * cfg.min_bars:
            resampled = _resample(df, target_min)
            if resampled is not None and len(resampled) >= cfg.min_bars:
                return resampled

        # Live: fetch from Binance
        return _fetch_klines_sync(symbol, cfg.interval, limit=100)
