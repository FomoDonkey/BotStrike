"""Multi-asset daily sources: symbol routing, Yahoo parsing, and the engine's UI-symbol mapping."""
import json
from types import SimpleNamespace

import pandas as pd
import pytest

from strategies import daily_sources as ds
from strategies.trend_daily import DailyDataStore, to_ui_symbol


def test_symbol_routing():
    assert ds.is_yahoo_symbol("XAU-USD") and ds.is_yahoo_symbol("SP500-USD") and ds.is_yahoo_symbol("NAS100-USD")
    assert not ds.is_yahoo_symbol("BTCUSDT") and not ds.is_yahoo_symbol("ADAUSDT")
    assert not ds.is_yahoo_symbol("UNKNOWN-USD")          # unmapped market stays on the venue path


def test_ui_symbol_mapping_keeps_strike_markets_intact():
    assert to_ui_symbol("BTCUSDT") == "BTC-USD" and to_ui_symbol("ADAUSDT") == "ADA-USD"
    assert to_ui_symbol("XAU-USD") == "XAU-USD" and to_ui_symbol("SP500-USD") == "SP500-USD"
    assert to_ui_symbol("NAS100-USD") == "NAS100-USD"


def _yahoo_payload(n=5, start=1_788_000_000):
    ts = [start + i * 86400 for i in range(n)]
    return {"chart": {"result": [{"timestamp": ts, "indicators": {"quote": [{
        "open": [100.0 + i for i in range(n)], "high": [101.0 + i for i in range(n)],
        "low": [99.0 + i for i in range(n)], "close": [100.5 + i for i in range(n)],
        "volume": [10 + i for i in range(n)]}]}}]}}


def test_yahoo_fetch_parses_and_computes_quote_volume(monkeypatch):
    class FakeResp:
        def __init__(self, data):
            self._d = json.dumps(data).encode()

        def read(self):
            return self._d

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(ds.urllib.request, "urlopen", lambda req, timeout=0: FakeResp(_yahoo_payload()))
    df = ds.fetch_daily_yahoo("XAU-USD")
    assert list(df.columns) == ["open", "high", "low", "close", "volume", "quote_volume"]
    assert len(df) == 5 and df.index.tz is None          # tz-naive UTC, same as the Binance fetcher
    assert df["close"].iloc[0] == 100.5 and df["quote_volume"].iloc[0] == pytest.approx(100.5 * 10)
    assert df.index.is_monotonic_increasing
    assert ds.fetch_daily_yahoo("NOT-A-MARKET") is None


def test_yahoo_fetch_raises_after_retries(monkeypatch):
    monkeypatch.setattr(ds.time, "sleep", lambda s: None)

    def boom(req, timeout=0):
        raise OSError("offline")

    monkeypatch.setattr(ds.urllib.request, "urlopen", boom)
    with pytest.raises(RuntimeError) as e:
        ds.fetch_daily_yahoo("XAU-USD")
    assert "yahoo fetch failed" in str(e.value)


def test_make_fetcher_dispatches_by_symbol(monkeypatch):
    seen = []

    def binance(symbol, start_ms=0, **kw):
        seen.append(("binance", symbol))
        return "binance-frame"

    monkeypatch.setattr(ds, "fetch_daily_yahoo", lambda symbol, start_ms=0: seen.append(("yahoo", symbol)) or "yahoo-frame")
    f = ds.make_fetcher(binance)
    assert f("BTCUSDT", 0) == "binance-frame"
    assert f("XAU-USD", 0) == "yahoo-frame"
    assert seen == [("binance", "BTCUSDT"), ("yahoo", "XAU-USD")]


def test_data_store_defaults_to_the_multi_source_fetcher(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(ds, "fetch_daily_yahoo", lambda symbol, start_ms=0: calls.append(symbol) or None)
    store = DailyDataStore(data_dir=str(tmp_path))
    today = pd.Timestamp.utcnow().normalize()
    store.load(["XAU-USD"], today, refresh=True)
    assert calls == ["XAU-USD"]                          # routed to Yahoo, not to Binance
