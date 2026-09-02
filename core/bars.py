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
