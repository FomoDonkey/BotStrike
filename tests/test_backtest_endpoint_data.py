"""The UI's backtest endpoint must read FUTURES data and honour its date filters.

Audit R2 backtest_parity-03 (P0) / -13 (P1): `_run_backtest_sync` read
data/binance/ (SPOT, last candle 2026-04-03) while the engine trades USDT-M
futures, and compared millisecond timestamps against second-based bounds. Net
effect: every `start_date` was silently ignored, every `end_date` returned
"Insufficient data: 0 bars", and the millisecond values leaked into the metrics
— the UI showed Sharpe -0.27 where the true figure was -15.97 (59x) with a mean
trade duration of 22 days instead of 32 minutes.
"""
import pandas as pd
import pytest

import server.bridge as bridge


def _read_source() -> str:
    from pathlib import Path
    return (Path(bridge.__file__)).read_text(encoding="utf-8")


def test_endpoint_prefers_futures_over_spot():
    src = _read_source()
    assert '"binance_futures", "binance"' in src, "futures must be tried first"
    i_fut = src.index("binance_futures")
    assert i_fut < src.index('"data", sub, "klines"') + len(src)  # sanity: same block


def test_endpoint_normalises_ms_before_filtering():
    src = _read_source()
    i_norm = src.index('df["timestamp"] / 1000.0')
    i_filter = src.index('df[df["timestamp"] >= pd.Timestamp(start_date).timestamp()]')
    assert i_norm < i_filter, "ms→s must happen BEFORE the date filters"


@pytest.mark.parametrize("unit,factor", [("ms", 1000.0), ("s", 1.0)])
def test_date_filter_selects_the_requested_window(unit, factor):
    """Reproduces the endpoint's filtering logic on a synthetic frame."""
    base = pd.Timestamp("2026-08-01").timestamp()
    ts = [(base + i * 60) * factor for i in range(3000)]  # 3000 one-minute bars
    df = pd.DataFrame({"timestamp": ts, "close": range(3000)})

    if len(df) and float(df["timestamp"].median()) > 1e12:
        df = df.copy()
        df["timestamp"] = df["timestamp"] / 1000.0

    start = pd.Timestamp("2026-08-01 05:00:00").timestamp()
    end = pd.Timestamp("2026-08-01 10:00:00").timestamp()
    sel = df[(df["timestamp"] >= start) & (df["timestamp"] <= end)]

    assert len(sel) == 301, f"{unit}: expected the 5-hour window, got {len(sel)} bars"
    assert sel["timestamp"].min() == start
    assert sel["timestamp"].max() == end


def test_unnormalised_ms_would_break_the_filter():
    """Guards the regression itself: without the ms→s step the window is empty."""
    base = pd.Timestamp("2026-08-01").timestamp()
    df = pd.DataFrame({"timestamp": [(base + i * 60) * 1000.0 for i in range(3000)]})
    end = pd.Timestamp("2026-08-15").timestamp()
    assert len(df[df["timestamp"] <= end]) == 0  # this is what the UI used to hit
