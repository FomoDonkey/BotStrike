"""Regime detector horizon + dwell hysteresis (2026-09-02).

CT evidence: 885 regime flips in 48 h on 1-minute bars (median regime 5 min, 320
A→B→A round-trips under 5 min), every one sent to Telegram. The detector now
aggregates 1-minute bars into complete N-minute bars and confirms a new regime only
after it persisted `regime_min_dwell_min` minutes; Telegram regime messages are
rate-limited per symbol. RegimeDetector() without settings keeps the legacy
behaviour so older tests stay meaningful.
"""
import asyncio
import time

import numpy as np
import pandas as pd
import pytest

from config import overrides as ov
from config.settings import Settings
from core.regime_detector import RegimeDetector
from core.types import MarketRegime
from notifications.telegram import TelegramNotifier


def _bars(n_minutes: int, start_ts: float = 1_699_999_200.0, seed: int = 0) -> pd.DataFrame:
    # start_ts is a multiple of 900 s → the first 15-minute bucket is complete
    rng = np.random.default_rng(seed)
    close = 100.0 * np.cumprod(1 + rng.normal(0, 0.001, n_minutes))
    ts = start_ts + 60.0 * np.arange(1, n_minutes + 1)     # bar CLOSE times, 1 min apart
    return pd.DataFrame({"timestamp": ts, "open": close * 0.999, "high": close * 1.001,
                         "low": close * 0.998, "close": close, "volume": 1.0})


def test_defaults_are_15_minutes_and_30_minutes_dwell():
    t = Settings().trading
    assert (t.regime_timeframe_min, t.regime_min_dwell_min, t.telegram_regime_min_interval_min) == (15, 30, 60)
    paths = {f["path"] for g in ov.schema(["BTC-USD"])["groups"] for f in g["fields"]}
    assert {"trading.regime_timeframe_min", "trading.regime_min_dwell_min",
            "trading.telegram_regime_min_interval_min"} <= paths


def test_resample_aggregates_only_complete_buckets():
    s = Settings()
    det = RegimeDetector(settings=s)
    cfg = s.symbols[0]
    df = _bars(15 * 6 + 7)                      # 6 complete 15-min buckets + 7 stray minutes
    out = det._resample(df, "BTC-USD", cfg, 15)
    assert len(out) == 6
    first = df.iloc[:15]
    assert out["open"].iloc[0] == pytest.approx(first["open"].iloc[0])
    assert out["close"].iloc[0] == pytest.approx(first["close"].iloc[-1])
    assert out["high"].iloc[0] == pytest.approx(first["high"].max())
    assert out["volume"].iloc[0] == pytest.approx(15.0)
    assert out["timestamp"].iloc[0] == pytest.approx(first["timestamp"].iloc[-1])   # bucket close time
    assert {"adx", "momentum_20", "vol_pct", "ema_cross"} <= set(out.columns)
    # cached until a new 1-minute bar arrives
    assert det._resample(df, "BTC-USD", cfg, 15) is out
    df2 = _bars(15 * 6 + 8)
    assert det._resample(df2, "BTC-USD", cfg, 15) is not out


def test_detect_is_unknown_until_enough_regime_bars():
    s = Settings()
    det = RegimeDetector(settings=s)
    cfg = s.symbols[0]
    assert det.detect(_bars(15 * 20), "BTC-USD", cfg) == MarketRegime.UNKNOWN     # 20 < 50 bars
    assert det.detect(_bars(15 * 60), "BTC-USD", cfg) != MarketRegime.UNKNOWN     # 60 >= 50 bars


def test_dwell_hysteresis_ignores_short_lived_regimes(monkeypatch):
    s = Settings()
    s.trading.regime_timeframe_min = 1          # isolate the dwell logic
    s.trading.regime_min_dwell_min = 30
    det = RegimeDetector(settings=s)
    cfg = s.symbols[0]
    script = {"regime": MarketRegime.RANGING}
    monkeypatch.setattr(det, "_classify", lambda **kw: script["regime"])
    monkeypatch.setattr(det, "_update_adaptive_thresholds", lambda *a, **k: {
        "vol_low": 0.3, "vol_high": 0.7, "adx_trend": 25.0, "mom_threshold": 0.01})
    base = _bars(200)

    def at(minute):                                # a frame whose last bar closes `minute` later
        df = base.copy()
        df["timestamp"] = df["timestamp"] + 60.0 * minute
        return df
    assert det.detect(at(0), "BTC-USD", cfg) == MarketRegime.RANGING          # first regime accepted
    script["regime"] = MarketRegime.TRENDING_UP
    for m in range(1, 31):                                                     # candidate since minute 1
        assert det.detect(at(m), "BTC-USD", cfg) == MarketRegime.RANGING       # < 30 min: not confirmed
    st = det.status("BTC-USD")
    assert st["candidate"] == "TRENDING_UP" and st["regime"] == "RANGING"
    assert det.detect(at(31), "BTC-USD", cfg) == MarketRegime.TRENDING_UP      # 30 min → confirmed
    # a 5-minute blip back to RANGING does not flip it
    script["regime"] = MarketRegime.RANGING
    for m in range(32, 37):
        assert det.detect(at(m), "BTC-USD", cfg) == MarketRegime.TRENDING_UP
    script["regime"] = MarketRegime.TRENDING_UP                                # candidate resets
    assert det.detect(at(37), "BTC-USD", cfg) == MarketRegime.TRENDING_UP
    assert det.status("BTC-USD")["candidate"] == ""


def test_legacy_detector_without_settings_keeps_two_step_smoothing(monkeypatch):
    det = RegimeDetector()
    assert det.params() == (1, 0)
    cfg = Settings().symbols[0]
    monkeypatch.setattr(det, "_update_adaptive_thresholds", lambda *a, **k: {
        "vol_low": 0.3, "vol_high": 0.7, "adx_trend": 25.0, "mom_threshold": 0.01})
    seq = iter([MarketRegime.RANGING, MarketRegime.RANGING, MarketRegime.TRENDING_UP, MarketRegime.TRENDING_UP])
    monkeypatch.setattr(det, "_classify", lambda **kw: next(seq))
    df = _bars(200)
    assert det.detect(df, "BTC-USD", cfg) == MarketRegime.RANGING
    assert det.detect(df, "BTC-USD", cfg) == MarketRegime.RANGING
    assert det.detect(df, "BTC-USD", cfg) == MarketRegime.RANGING      # 1 detection: keep
    assert det.detect(df, "BTC-USD", cfg) == MarketRegime.TRENDING_UP  # 2 consecutive: change


def test_telegram_regime_messages_are_rate_limited_per_symbol(monkeypatch):
    s = Settings()
    s.trading.telegram_notify_regime = True
    s.trading.telegram_regime_min_interval_min = 60
    n = TelegramNotifier("t", "c", settings=s)
    asyncio.run(n.notify_regime_change("BTC-USD", MarketRegime.RANGING, MarketRegime.TRENDING_UP))
    asyncio.run(n.notify_regime_change("BTC-USD", MarketRegime.TRENDING_UP, MarketRegime.RANGING))
    asyncio.run(n.notify_regime_change("ETH-USD", MarketRegime.RANGING, MarketRegime.TRENDING_UP))
    assert n._queue.qsize() == 2                                        # BTC second message throttled
    n._last_regime_sent["BTC-USD"] = time.time() - 3601
    asyncio.run(n.notify_regime_change("BTC-USD", MarketRegime.TRENDING_UP, MarketRegime.RANGING))
    assert n._queue.qsize() == 3
    s.trading.telegram_notify_regime = False                            # default in v2.14
    asyncio.run(n.notify_regime_change("SOL-USD", MarketRegime.RANGING, MarketRegime.BREAKOUT))
    assert n._queue.qsize() == 3
