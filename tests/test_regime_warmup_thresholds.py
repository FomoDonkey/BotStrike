"""The regime thresholds never learn from the ADX warm-up, and never leave textbook bands.

Seen on the CT 2026-09-05: after each of five restarts the engine held ~15 h of bars, the 60th
percentile of that ADX series was 61 (Wilder's ADX reads 60+ for its first periods), so nothing
could be TRENDING and every symbol showed RANGING for a day. Measured on 30 days of settled
15-minute bars the same classifier gives RANGING 56-60 %, TRENDING 30-35 %, BREAKOUT 9-10 %.
"""
import numpy as np
import pandas as pd

from config.settings import Settings
from core.regime_detector import ADX_WARMUP_BARS, MIN_THRESHOLD_BARS, RegimeDetector
from core.types import MarketRegime


def _frame(n: int, adx, momentum=0.004, vol_pct=None, ema_cross=0.0) -> pd.DataFrame:
    ts = 1_700_000_000.0 + 900.0 * np.arange(1, n + 1)
    return pd.DataFrame({
        "timestamp": ts, "close": 100.0, "adx": np.asarray(adx, dtype=float),
        "vol_pct": np.linspace(0.0, 1.0, n) if vol_pct is None else np.full(n, float(vol_pct)),
        "momentum_20": np.full(n, float(momentum)), "ema_cross": np.full(n, float(ema_cross)),
    })


def _cfg():
    return Settings().get_symbol_config("BTC-USD")


def test_a_short_seed_falls_back_to_textbook_thresholds_not_to_warmup_percentiles():
    n = 55                                              # what a 15 h seed gives at 15 minutes
    adx = np.r_[np.linspace(90, 60, ADX_WARMUP_BARS), np.full(n - ADX_WARMUP_BARS, 18.0)]
    thr = RegimeDetector()._update_adaptive_thresholds(_frame(n, adx), "BTC-USD", _cfg())
    assert thr["adx_trend"] == 25.0                     # not 61
    assert thr["mom_threshold"] == 0.005
    assert thr["vol_low"] < thr["vol_high"]


def test_settled_bars_set_the_threshold_and_the_band_holds_it():
    n = ADX_WARMUP_BARS + 200
    settled = np.linspace(10, 40, 200)
    adx = np.r_[np.full(ADX_WARMUP_BARS, 80.0), settled]
    thr = RegimeDetector()._update_adaptive_thresholds(_frame(n, adx), "BTC-USD", _cfg())
    assert abs(thr["adx_trend"] - np.percentile(settled, 60)) < 1e-9   # the warm-up 80s never counted
    assert 20.0 <= thr["adx_trend"] <= 30.0
    # a window where ADX sat at 60+ for real (a week-long trend) still cannot push the bar past 30
    thr2 = RegimeDetector()._update_adaptive_thresholds(_frame(n, np.full(n, 65.0)), "ETH-USD", _cfg())
    assert thr2["adx_trend"] == 30.0
    assert len(settled) >= MIN_THRESHOLD_BARS


def test_a_trend_is_detectable_right_after_a_restart_seed():
    n = 132                                             # a 33 h seed at 15 minutes
    adx = np.r_[np.linspace(90, 60, ADX_WARMUP_BARS), np.full(n - ADX_WARMUP_BARS, 35.0)]
    det = RegimeDetector()                              # legacy: tf 1 (frame already 15 m), 2-step smoothing
    df = _frame(n, adx, momentum=0.02, vol_pct=0.5, ema_cross=1.0)
    for _ in range(2):
        regime = det.detect(df, "BTC-USD", _cfg())
    assert regime == MarketRegime.TRENDING_UP
