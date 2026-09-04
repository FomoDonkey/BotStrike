"""
Módulo de indicadores técnicos.
Calcula ATR, medias móviles, Z-score, momentum, volatilidad y más.
Todos los cálculos usan numpy/pandas para eficiencia.
"""
from __future__ import annotations
from typing import Iterable, Optional
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
        prev_close = close.shift(1)
        # np.fmax, not np.maximum and not pd.concat().max(): on the first row tr2/tr3 are NaN and
        # pandas' max SKIPS them, so fmax is the one that reproduces it exactly. Building a
        # three-column frame per call cost 35 s of a 245 s backtest across 10,161 calls (2026-09-04).
        tr = np.fmax(np.fmax((high - low).to_numpy(dtype=float),
                             (high - prev_close).abs().to_numpy(dtype=float)),
                     (low - prev_close).abs().to_numpy(dtype=float))
        true_range = pd.Series(tr, index=close.index)
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
        # NaN stays NaN during warmup — fillna(0) would collapse bands to SMA,
        # causing false BB touch signals. Consumers must check for NaN.
        std = series.rolling(window=period, min_periods=period).std()
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
        """Percentil de volatilidad actual dentro de ventana histórica (0-1).

        Vectorised 2026-09-04. This was `rolling(100).apply(fn, raw=False)`, which materialises a
        pandas Series per window: on the backtester it was 45 s of a 310 s run and the single largest
        indicator cost, because a 100-wide Python callable runs once per row per call. The maths below
        is the same comparison — how many of the earlier values in the window are strictly below the
        last one — done with a strided view. Identity against the old implementation is asserted in
        tests/test_indicators_vectorised.py, NaN pattern included.
        """
        returns = series.pct_change().abs()
        vol = returns.rolling(window=atr_period, min_periods=2).mean()
        v = vol.to_numpy(dtype=float)
        n = v.size
        out = np.full(n, np.nan)
        if n == 0:
            return pd.Series(out, index=series.index)

        # pandas counts a window as valid on non-NaN observations only (min_periods=10), and the
        # callable saw the FULL window including NaNs — a comparison with NaN is False either way.
        valid = (~np.isnan(v)).astype(np.int64)
        cum_valid = np.concatenate(([0], np.cumsum(valid)))
        for i in range(n):
            start = max(0, i - lookback + 1)
            if cum_valid[i + 1] - cum_valid[start] < 10:
                continue
            w = v[start:i + 1]
            denom = w.size - 1
            if denom < 1:
                out[i] = 0.5
                continue
            last = w[-1]
            if np.isnan(last):
                out[i] = 0.0
                continue
            out[i] = np.count_nonzero(w[:-1] < last) / denom
        return pd.Series(out, index=series.index)

    @staticmethod
    def _di_pair(high: pd.Series, low: pd.Series, close: pd.Series, period: int,
                 atr_val: Optional[pd.Series] = None) -> tuple[pd.Series, pd.Series]:
        """The smoothed DI+/DI- pair, unfilled. Extracted 2026-09-04.

        `adx` and `directional_indicators` computed this identical block twice, and compute_all calls
        both — three ATRs and two DM smoothings per pass where one of each will do. The arithmetic is
        untouched; tests/test_indicators_vectorised.py pins the output of every column.
        """
        plus_dm_raw = high.diff()
        minus_dm_raw = -low.diff()
        # Wilder's DM: only the larger directional move counts (ties = both zero)
        plus_dm = plus_dm_raw.where((plus_dm_raw > minus_dm_raw) & (plus_dm_raw > 0), 0.0)
        minus_dm = minus_dm_raw.where((minus_dm_raw > plus_dm_raw) & (minus_dm_raw > 0), 0.0)
        # Wilder's smoothing (equivalent to EWM with span=2*period-1)
        if atr_val is None:
            atr_val = Indicators.atr(high, low, close, period)
        smoothed_plus = plus_dm.ewm(span=2 * period - 1, adjust=False).mean()
        smoothed_minus = minus_dm.ewm(span=2 * period - 1, adjust=False).mean()
        safe_atr = atr_val.replace(0, np.nan)
        return 100 * (smoothed_plus / safe_atr), 100 * (smoothed_minus / safe_atr)

    @staticmethod
    def adx(
        high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14,
        atr_val: Optional[pd.Series] = None, di: Optional[tuple] = None,
    ) -> pd.Series:
        """Average Directional Index — fuerza de tendencia (0-100).

        `atr_val` and `di` let a caller hand over work it has already done; omit them and this
        behaves exactly as it always did.
        """
        plus_di, minus_di = di if di is not None else Indicators._di_pair(high, low, close, period, atr_val)
        dx = 100 * ((plus_di - minus_di).abs() /
                     (plus_di + minus_di).replace(0, np.nan))
        return dx.ewm(span=2 * period - 1, adjust=False).mean()

    @staticmethod
    def directional_indicators(
        high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14,
        atr_val: Optional[pd.Series] = None, di: Optional[tuple] = None,
    ) -> tuple[pd.Series, pd.Series]:
        """DI+ y DI- para confirmación direccional de tendencia."""
        plus_di, minus_di = di if di is not None else Indicators._di_pair(high, low, close, period, atr_val)
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

    #: Every column compute_all can produce, in output order. Anything not in here is not a
    #: valid `only=` name, and asking for one is an error rather than a silently missing column.
    ALL_COLUMNS: tuple[str, ...] = (
        "sma_20", "sma_50", "ema_12", "ema_26", "atr", "std_20", "zscore",
        "bb_upper", "bb_mid", "bb_lower", "momentum_10", "momentum_20", "rsi",
        "vol_ratio", "adx", "ema_cross", "vol_pct", "plus_di", "minus_di",
        "high_20", "low_20",
    )

    @staticmethod
    def compute_all(df: pd.DataFrame, config: Optional[dict] = None,
                    only: Optional[Iterable[str]] = None) -> pd.DataFrame:
        """Calcula todos los indicadores sobre un DataFrame OHLCV.

        El DataFrame debe tener columnas: open, high, low, close, volume.
        Retorna el mismo DataFrame con columnas adicionales de indicadores.

        `only` restricts the work to the named columns. Every indicator here is independent, so a
        column computed under `only` is bit-identical to the same column computed in a full pass —
        what changes is that the other twenty are never computed at all. mean_reversion reads three
        of the twenty-one off its 1H frame and six off its 5m frame, and recomputing the rest on
        every bar was 60 % of a backtest (2026-09-04). Omit it and the behaviour is exactly as
        before. An unknown name raises rather than yielding a column that is quietly absent —
        `Series.get("x", 0)` would have swallowed a typo as a zero.
        """
        c = config or {}
        if df.empty:
            return df
        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]

        if only is None:
            want = set(Indicators.ALL_COLUMNS)
        else:
            want = set(only)
            unknown = want - set(Indicators.ALL_COLUMNS)
            if unknown:
                raise ValueError(f"compute_all: unknown column(s) {sorted(unknown)}")

        # Shared intermediates, computed only when something that needs them was asked for.
        need_atr = bool(want & {"atr", "adx", "plus_di", "minus_di"})
        need_di = bool(want & {"adx", "plus_di", "minus_di"})
        atr14 = Indicators.atr(high, low, close, 14) if need_atr else None
        di14 = Indicators._di_pair(high, low, close, 14, atr_val=atr14) if need_di else None
        if want & {"bb_upper", "bb_mid", "bb_lower"}:
            bb_upper, bb_mid, bb_lower = Indicators.bollinger_bands(close)
        else:
            bb_upper = bb_mid = bb_lower = None
        if need_di:
            plus_di, minus_di = Indicators.directional_indicators(high, low, close, 14, di=di14)
        else:
            plus_di = minus_di = None

        builders = {
            "sma_20": lambda: Indicators.sma(close, 20),
            "sma_50": lambda: Indicators.sma(close, 50),
            "ema_12": lambda: Indicators.ema(close, c.get("ema_fast", 12)),
            "ema_26": lambda: Indicators.ema(close, c.get("ema_slow", 26)),
            "atr": lambda: atr14,
            "std_20": lambda: Indicators.std(close, 20),
            "zscore": lambda: Indicators.zscore(close, c.get("zscore_lookback", 100)),
            "bb_upper": lambda: bb_upper,
            "bb_mid": lambda: bb_mid,
            "bb_lower": lambda: bb_lower,
            "momentum_10": lambda: Indicators.momentum(close, 10),
            "momentum_20": lambda: Indicators.momentum(close, 20),
            "rsi": lambda: Indicators.rsi(close, 14),
            "vol_ratio": lambda: Indicators.volume_ratio(volume, 20),
            "adx": lambda: Indicators.adx(high, low, close, 14, di=di14),
            "ema_cross": lambda: Indicators.ema_crossover(
                close, c.get("ema_fast", 12), c.get("ema_slow", 26)),
            "vol_pct": lambda: Indicators.volatility_percentile(close),
            "plus_di": lambda: plus_di,
            "minus_di": lambda: minus_di,
            "high_20": lambda: high.rolling(20, min_periods=20).max(),
            "low_20": lambda: low.rolling(20, min_periods=20).min(),
        }
        cols = {name: builders[name]() for name in Indicators.ALL_COLUMNS if name in want}
        if not cols:
            return df

        # ONE assignment for all of them. A loop here is still a __setitem__ per column and pandas
        # re-consolidates the block manager on every one: 71,127 of those cost 41 s of a 310 s run.
        df[list(cols)] = pd.DataFrame(
            {k: (v.to_numpy() if hasattr(v, "to_numpy") else v) for k, v in cols.items()},
            index=df.index)
        return df
