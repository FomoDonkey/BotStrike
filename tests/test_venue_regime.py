"""Every venue market gets a regime, not only the four the engine streams.

The header read UNKNOWN on 27 of 31 markets and the Risk page listed four chips while the book
held six markets (2026-09-05). The bridge now runs the same detector on the venue's own 15-minute
bars for any other market, once a minute, and /api/regime covers the live book's pool and holdings.
"""
import asyncio
from types import SimpleNamespace

import numpy as np

import server.bridge as bridge
from config.settings import Settings
from core.regime_detector import RegimeDetector
from strategies.trend_daily import to_ui_symbol


def _candles(n=200, seed=3):
    rng = np.random.default_rng(seed)
    close = 100.0 * np.cumprod(1 + rng.normal(0, 0.002, n))
    t0 = 1_788_400_000.0
    return [{"timestamp": t0 + 900.0 * i, "open": float(c * 0.999), "high": float(c * 1.002),
             "low": float(c * 0.998), "close": float(c), "volume": 1.0} for i, c in enumerate(close)]


def _engine(monkeypatch, pool=("BTCUSDT", "XAUUSDT"), held=()):
    s = Settings()
    engine = SimpleNamespace(
        settings=s, regime_detector=RegimeDetector(s),
        trend_engine=SimpleNamespace(pool=lambda: list(pool), state=SimpleNamespace(positions={h: None for h in held})),
    )
    fresh = bridge.BridgeState()
    fresh.engine = engine
    monkeypatch.setattr(bridge, "state", fresh)
    monkeypatch.setattr(bridge, "_VENUE_REGIME", {})
    return engine


def test_a_venue_market_is_classified_from_its_own_bars_and_cached(monkeypatch):
    _engine(monkeypatch)
    calls = []

    async def fake_klines(symbol, interval="1m", limit=500):
        calls.append((symbol, interval, limit))
        return {"symbol": symbol, "interval": "15m", "candles": _candles()}

    monkeypatch.setattr(bridge, "get_market_klines", fake_klines)
    rs = asyncio.run(bridge._venue_regime("XAU-USD"))
    assert rs["source"] == "venue" and rs["bars"] == 200 and rs["timeframe_min"] == 15
    assert rs["regime"] in {"RANGING", "TRENDING_UP", "TRENDING_DOWN", "BREAKOUT"}   # not UNKNOWN
    assert rs["inputs"]["adx"] > 0
    assert asyncio.run(bridge._venue_regime("XAU-USD")) is rs                      # within the minute: cached
    assert len(calls) == 1


def test_a_thin_market_is_honest_unknown(monkeypatch):
    _engine(monkeypatch)

    async def fake_klines(symbol, interval="1m", limit=500):
        return {"symbol": symbol, "interval": "1h", "candles": _candles(12)}

    monkeypatch.setattr(bridge, "get_market_klines", fake_klines)
    rs = asyncio.run(bridge._venue_regime("SP500-USD"))
    assert rs["regime"] == "UNKNOWN" and rs["bars"] == 12 and rs["timeframe_min"] == 60


def test_api_regime_covers_the_engine_symbols_and_the_book(monkeypatch):
    _engine(monkeypatch, pool=("BTCUSDT", "XAUUSDT"), held=("WTIUSDT",))

    async def fake_klines(symbol, interval="1m", limit=500):
        return {"symbol": symbol, "interval": "15m", "candles": _candles()}

    monkeypatch.setattr(bridge, "get_market_klines", fake_klines)
    out = asyncio.run(bridge.get_regime())
    syms = out["symbols"]
    assert set(Settings().symbol_names) <= set(syms)
    assert syms["BTC-USD"]["source"] == "engine"
    assert syms[to_ui_symbol("XAUUSDT")]["source"] == "venue"
    assert syms[to_ui_symbol("WTIUSDT")]["source"] == "venue"
