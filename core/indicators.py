"""
Módulo de indicadores técnicos.
Calcula ATR, medias móviles, Z-score, momentum, volatilidad y más.
Todos los cálculos usan numpy/pandas para eficiencia.
"""
from __future__ import annotations
from typing import Optional
import numpy as np
import pandas as pd


class Indicators:
    """Calculadora de indicadores técnicos sobre series de precios."""

    @staticmethod
    def sma(series: pd.Series, period: int) -> pd.Series:
        """Simple Moving Average."""
        return series.rolling(window=period, min_periods=period).mean()

    @staticmethod
    def ema(series: pd.Series, period: int) -> pd.Series:
        """Exponential Moving Average."""
        return series.ewm(span=period, adjust=False).mean()

    @staticmethod
    def std(series: pd.Series, period: int) -> pd.Series:
        """Desviación estándar rolling."""
        return series.rolling(window=period, min_periods=2).std()

    @staticmethod
    def atr(
        high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
    ) -> pd.Series:
        """Average True Range — mide volatilidad."""
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        # Wilder's smoothing: equivalent to EWM with span = 2*period - 1
        return true_range.ewm(span=2 * period - 1, adjust=False).mean()

    @staticmethod
    def zscore(series: pd.Series, period: int) -> pd.Series:
        """Z-score: cuántas desviaciones estándar del precio respecto a su media."""
        mean = series.rolling(window=period, min_periods=2).mean()
        std = series.rolling(window=period, min_periods=2).std()
        deviation = series - mean
        # Guard against near-zero std: values below epsilon produce extreme z-scores
        safe_std = std.where(std > 1e-12, np.nan)
        result = deviation / safe_std
        # Fill NaN where std was 0 or near-zero (flat price → z-score = 0)
        result = result.fillna(0.0)
        return result

    @staticmethod
    def bollinger_bands(
        series: pd.Series, period: int = 20, num_std: float = 2.0
    ) -> tuple[pd.Series, pd.Series, pd.Series]:
        """Bandas de Bollinger: (upper, middle, lower)."""
        middle = series.rolling(window=period, min_periods=period).mean()
        std = series.rolling(window=period, min_periods=period).std().fillna(0)
        upper = middle + num_std * std
        lower = middle - num_std * std
        return upper, middle, lower

    @staticmethod
    def rsi(series: pd.Series, period: int = 14) -> pd.Series:
        """Relative Strength Index (Wilder's smoothing)."""
        delta = series.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)
        # Wilder's smoothing: span = 2*period - 1 (consistent with ATR)
        avg_gain = gain.ewm(span=2 * period - 1, adjust=False).mean()
        avg_loss = loss.ewm(span=2 * period - 1, adjust=False).mean()
        # Handle avg_loss=0 (pure uptrend → RSI=100) separately from initial NaN
        pure_gain = (avg_loss == 0) & (avg_gain > 0)
        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        rsi = rsi.where(~pure_gain, 100.0)  # Pure gains → RSI=100 (not 50)
        return rsi.fillna(50.0)  # Initial NaN (first bar) → neutral

    @staticmethod
    def momentum(series: pd.Series, period: int) -> pd.Series:
        """Momentum: retorno porcentual sobre N períodos."""
        return series.pct_change(periods=period)

    @staticmethod
    def volume_ratio(volume: pd.Series, period: int = 20) -> pd.Series:
        """Ratio de volumen actual vs media móvil."""
        avg_vol = volume.rolling(window=period, min_periods=max(period // 2, 2)).mean()
        return volume / avg_vol.replace(0, np.nan)

    @staticmethod
    def volatility_percentile(
        series: pd.Series, atr_period: int = 14, lookback: int = 100
    ) -> pd.Series:
        """Percentil de volatilidad actual dentro de ventana histórica.
        Retorna valor entre 0 y 1."""
        # Usamos retornos absolutos como proxy de volatilidad
        returns = series.pct_change().abs()
        vol = returns.rolling(window=atr_period, min_periods=2).mean()

        def percentile_rank(window):
            if len(window) < 2:
                return 0.5
            return (window.values[:-1] < window.values[-1]).sum() / (len(window) - 1)

        return vol.rolling(window=lookback, min_periods=10).apply(
            percentile_rank, raw=False
        )

    @staticmethod
    def adx(
        high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
    ) -> pd.Series:
        """Average Directional Index — fuerza de tendencia (0-100)."""
        plus_dm_raw = high.diff()
        minus_dm_raw = -low.diff()
        # Wilder's DM: only the larger directional move counts (ties = both zero)
        plus_dm = plus_dm_raw.where((plus_dm_raw > minus_dm_raw) & (plus_dm_raw > 0), 0.0)
        minus_dm = minus_dm_raw.where((minus_dm_raw > plus_dm_raw) & (minus_dm_raw > 0), 0.0)

        # Wilder's smoothing (equivalent to EWM with span=2*period-1)
        atr_val = Indicators.atr(high, low, close, period)
        smoothed_plus = plus_dm.ewm(span=2 * period - 1, adjust=False).mean()
        smoothed_minus = minus_dm.ewm(span=2 * period - 1, adjust=False).mean()
        plus_di = 100 * (smoothed_plus / atr_val.replace(0, np.nan))
        minus_di = 100 * (smoothed_minus / atr_val.replace(0, np.nan))

        dx = 100 * ((plus_di - minus_di).abs() /
                     (plus_di + minus_di).replace(0, np.nan))
        return dx.ewm(span=2 * period - 1, adjust=False).mean()

    @staticmethod
    def directional_indicators(
        high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
    ) -> tuple[pd.Series, pd.Series]:
        """DI+ y DI- para confirmación direccional de tendencia."""
        plus_dm_raw = high.diff()
        minus_dm_raw = -low.diff()
        plus_dm = plus_dm_raw.where((plus_dm_raw > minus_dm_raw) & (plus_dm_raw > 0), 0.0)
        minus_dm = minus_dm_raw.where((minus_dm_raw > plus_dm_raw) & (minus_dm_raw > 0), 0.0)
        atr_val = Indicators.atr(high, low, close, period)
        smoothed_plus = plus_dm.ewm(span=2 * period - 1, adjust=False).mean()
        smoothed_minus = minus_dm.ewm(span=2 * period - 1, adjust=False).mean()
        plus_di = 100 * (smoothed_plus / atr_val.replace(0, np.nan))
        minus_di = 100 * (smoothed_minus / atr_val.replace(0, np.nan))
        return plus_di.fillna(0), minus_di.fillna(0)

    @staticmethod
    def ema_crossover(
        series: pd.Series, fast: int, slow: int
    ) -> pd.Series:
        """Señal de cruce de EMAs: +1 si fast > slow, -1 si fast < slow, 0 recién cruzó."""
        ema_fast = Indicators.ema(series, fast)
        ema_slow = Indicators.ema(series, slow)
        diff = ema_fast - ema_slow
        signal = pd.Series(0, index=series.index, dtype=float)
        signal[diff > 0] = 1.0
        signal[diff < 0] = -1.0
        return signal

    @staticmethod
    def keltner_channels(
        high: pd.Series, low: pd.Series, close: pd.Series,
        ema_period: int = 20, atr_period: int = 14, multiplier: float = 2.0
    ) -> tuple[pd.Series, pd.Series, pd.Series]:
        """Keltner Channels: (upper, middle, lower)."""
        middle = Indicators.ema(close, ema_period)
        atr_val = Indicators.atr(high, low, close, atr_period)
        upper = middle + multiplier * atr_val
        lower = middle - multiplier * atr_val
        return upper, middle, lower

    @staticmethod
    def compute_all(df: pd.DataFrame, config: Optional[dict] = None) -> pd.DataFrame:
        """Calcula todos los indicadores sobre un DataFrame OHLCV.

        El DataFrame debe tener columnas: open, high, low, close, volume.
        Retorna el mismo DataFrame con columnas adicionales de indicadores.
        """
        c = config or {}
        if df.empty:
            return df
        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]

        # Medias móviles
        df["sma_20"] = Indicators.sma(close, 20)
        df["sma_50"] = Indicators.sma(close, 50)
        df["ema_12"] = Indicators.ema(close, c.get("ema_fast", 12))
        df["ema_26"] = Indicators.ema(close, c.get("ema_slow", 26))

        # Volatilidad
        df["atr"] = Indicators.atr(high, low, close, 14)
        df["std_20"] = Indicators.std(close, 20)

        # Z-score
        df["zscore"] = Indicators.zscore(close, c.get("zscore_lookback", 100))

        # Bollinger
        df["bb_upper"], df["bb_mid"], df["bb_lower"] = Indicators.bollinger_bands(close)

        # Momentum
        df["momentum_10"] = Indicators.momentum(close, 10)
        df["momentum_20"] = Indicators.momentum(close, 20)
        df["rsi"] = Indicators.rsi(close, 14)

        # Volumen
        df["vol_ratio"] = Indicators.volume_ratio(volume, 20)

        # Tendencia
        df["adx"] = Indicators.adx(high, low, close, 14)
        df["ema_cross"] = Indicators.ema_crossover(
            close, c.get("ema_fast", 12), c.get("ema_slow", 26)
        )

        # Volatilidad relativa (percentil)
        df["vol_pct"] = Indicators.volatility_percentile(close)

        # Indicadores direccionales (DI+/DI-)
        df["plus_di"], df["minus_di"] = Indicators.directional_indicators(high, low, close, 14)

        # N-bar breakout levels
        df["high_20"] = high.rolling(20, min_periods=20).max()
        df["low_20"] = low.rolling(20, min_periods=20).min()

        return df
