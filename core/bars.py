"""Bar aggregation shared by the regime detector and the intraday strategies.

`aggregate_1m(df, tf_min)` turns the engine's 1-minute frame (timestamp = bar CLOSE
time in seconds) into COMPLETE `tf_min`-minute bars aligned to wall-clock buckets
(00:00, 00:15, …). Partial buckets are dropped so indicators never see a forming bar.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd


def aggregate_1m(df: pd.DataFrame, tf_min: int) -> Optional[pd.DataFrame]:
    """Complete tf-minute OHLCV bars from 1-minute bars. Returns None when the frame
    has no usable columns; an empty frame when no bucket is complete yet."""
    if df is None or df.empty or "timestamp" not in df.columns:
        return None
    if not {"open", "high", "low", "close"}.issubset(df.columns):
        return None
    tf = max(1, int(tf_min))
    if tf == 1:
        return df
    secs = tf * 60
    ts = df["timestamp"].astype(float).to_numpy()
    open_ts = ts - 60.0                       # 1-min bar close → its open time
    bucket = np.floor(open_ts / secs) * secs  # open time of the tf-minute bucket
    g = df.assign(_b=bucket).groupby("_b", sort=True)
    agg = g.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
                close=("close", "last"), n=("close", "size"))
    agg["volume"] = g["volume"].sum() if "volume" in df.columns else 0.0
    agg = agg[agg["n"] >= tf].drop(columns=["n"])   # only complete buckets
    if agg.empty:
        return agg
    agg["timestamp"] = agg.index.to_numpy(dtype=float) + secs   # close time
    return agg.reset_index(drop=True)


def aggregate_blocks(df: pd.DataFrame, step: int) -> pd.DataFrame:
    """OHLCV aggregation of contiguous `step`-row blocks counted from the START of `df`.

    Bit-exact replacement for::

        df.groupby(np.arange(len(df)) // step).agg(
            {"open": "first", "high": "max", "low": "min",
             "close": "last", "volume": "sum"}).reset_index(drop=True)

    ~20x faster than that line, for a reason that has nothing to do with the aggregation:
    handing `groupby` a raw ndarray key makes pandas call `Index.get_loc(key)`, catch the
    failure, and — while building the exception message it is about to throw away — render the
    entire key array to a string. The 5m path's key is exactly 1,000 elements, one under numpy's
    summarisation threshold, so all 1,000 got formatted, once per bar. Measured 2026-09-04:
    5.7 s of a 34.6 s backtest went into printing group labels nobody ever read.

    The volume sum uses Kahan compensated summation because that is what pandas' Cython
    group_sum does; a plain `np.sum(axis=1)` is off by ~1e-12, which is small until it flips a
    comparison. Verified bit-identical to the groupby above over 160 cases including all-zero
    stretches and values spanning 1e-4 to 1e6 (tests/test_indicators_vectorised.py).

    A trailing partial block is aggregated too, exactly as groupby would; callers that want only
    complete bars trim to a multiple of `step` first.
    """
    cols = ("open", "high", "low", "close", "volume")
    if df is None or df.empty or step < 1:
        return pd.DataFrame(columns=list(cols))

    n = len(df)
    k, rest = divmod(n, step)
    out: dict = {}

    def block(name: str, how: str):
        arr = df[name].to_numpy()
        full = arr[:k * step].reshape(k, step) if k else None
        if how == "first":
            head = arr[:k * step:step] if k else arr[:0]
            tail = arr[k * step:k * step + 1] if rest else arr[:0]
        elif how == "last":
            head = arr[step - 1:k * step:step] if k else arr[:0]
            tail = arr[n - 1:n] if rest else arr[:0]
        elif how == "max":
            head = full.max(axis=1) if k else arr[:0]
            tail = arr[k * step:].max(keepdims=True) if rest else arr[:0]
        elif how == "min":
            head = full.min(axis=1) if k else arr[:0]
            tail = arr[k * step:].min(keepdims=True) if rest else arr[:0]
        else:  # sum
            head = _kahan_rows(full) if k else arr[:0]
            tail = _kahan_rows(arr[k * step:].reshape(1, rest)) if rest else arr[:0]
        out[name] = np.concatenate([head, tail]) if rest else head

    for name, how in zip(cols, ("first", "max", "min", "last", "sum")):
        if name in df.columns:
            block(name, how)
    return pd.DataFrame(out)


def _kahan_rows(m: np.ndarray) -> np.ndarray:
    """Row-wise compensated summation, matching pandas' group_sum bit for bit.

    Integer input is summed exactly by numpy already, so it is left alone: running Kahan on it
    would silently widen the column to float and change the dtype groupby would have produced.
    """
    if not np.issubdtype(m.dtype, np.floating):
        return m.sum(axis=1)
    total = np.zeros(m.shape[0], dtype=float)
    comp = np.zeros(m.shape[0], dtype=float)
    for i in range(m.shape[1]):
        y = m[:, i] - comp
        t = total + y
        comp = (t - total) - y
        total = t
    return total
