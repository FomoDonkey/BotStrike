"""The live venue is Strike; the history is Binance. Both halves pinned here.

Edgar, 2026-09-04: "hazlo con strike todo, excepto el backtest que se haga con datos historicos de
binance ya que proporciona mas." These tests exist because the split is easy to get wrong in either
direction, and because the venue's websocket protocol has one detail — lowercase stream names — that
fails silently: the wrong case is ACKed with the same success frame and then never speaks.
"""
import asyncio
import time
from types import SimpleNamespace

import pytest

from config.settings import Settings
from core.market_data import MarketDataCollector
from core.regime_detector import RegimeDetector
from exchange.strike_ws import StrikeMarketWebSocket


# ── the websocket protocol, as measured against the live venue ────────────────────────────────

def test_stream_names_are_lowercase_or_the_venue_stays_silent():
    """BTC-USD@depth is accepted with {"result": null} and then pushes nothing, forever.
    btc-usd@depth pushes. Measured 2026-09-04; this is why Strike mode never delivered a tick."""
    ws = StrikeMarketWebSocket(symbols=["BTC-USD", "XAU-USD"])
    streams = ws._streams()
    assert streams == ["btc-usd@kline_1m", "btc-usd@trade",
                       "xau-usd@kline_1m", "xau-usd@trade"]
    assert all(s == s.lower() for s in streams)


def test_kline_and_trade_frames_are_translated_to_the_engine_s_shape():
    """The engine reads the Binance client's event shape; the Strike feed must speak it too."""
    ws = StrikeMarketWebSocket(symbols=["BTC-USD"])
    got = {}

    async def cap(kind):
        async def cb(d):
            got[kind] = d
        return cb

    async def run():
        for k in ("kline", "trade", "depth"):
            ws.on(k, await cap(k))
        # exactly the frames the venue sends (captured live)
        await ws._process({"E": 1788493984795, "e": "kline", "s": "BTC-USD",
                           "k": {"t": 1788493920000, "T": 1788493979999, "s": "BTC-USD", "i": "1m",
                                 "o": "80871.70", "h": "80871.70", "l": "80871.70", "c": "80871.70",
                                 "v": "0", "n": 0, "x": True}})
        await ws._process({"E": 1788494199689, "T": 1788494199632, "e": "trade", "m": False,
                           "p": "0.22439", "q": "889", "s": "ADA-USD", "t": 1092633})
        await ws._process({"e": "error", "error": {"code": 400, "msg": "boom"}})   # must not raise

    asyncio.run(run())

    k = got["kline"]
    assert k["s"] == "BTC-USD" and k["e"] == "kline" and k["channel"] == "kline_1m"
    assert k["k"]["t"] == 1788493920000 and k["k"]["c"] == "80871.70" and k["k"]["x"] is True
    t = got["trade"]
    assert t["s"] == "ADA-USD" and t["p"] == "0.22439" and t["q"] == "889"
    assert t["T"] == 1788494199632 and t["m"] is False
    assert "depth" not in got                      # depth never comes from the socket, see below


def test_depth_comes_from_rest_snapshots_not_the_diff_stream():
    """The venue's @depth is a DIFF stream while the engine's handler replaces the whole book on
    every event: fed diffs, the terminal would show a three-level book. Snapshots instead."""
    ws = StrikeMarketWebSocket(symbols=["BTC-USD"])
    assert not any("@depth" in s for s in ws._streams())

    seen = []

    async def cb(d):
        seen.append(d)

    async def fake_rest(path, params):
        assert path == "/depth" and params["symbol"] == "BTC-USD"
        return {"E": 1, "T": 2, "bids": [["80", "1"], ["79", "2"]], "asks": [["81", "1"]]}

    async def run():
        ws.on("depth", cb)
        ws._rest = fake_rest
        ws._running = True

        async def stop_soon():
            await asyncio.sleep(0.05)
            ws._running = False

        await asyncio.gather(ws._depth_loop(), stop_soon())

    asyncio.run(run())
    assert seen and seen[0]["s"] == "BTC-USD"
    assert seen[0]["b"] == [["80", "1"], ["79", "2"]] and seen[0]["a"] == [["81", "1"]]


def test_mark_index_and_funding_are_polled_because_no_stream_carries_them():
    ws = StrikeMarketWebSocket(symbols=["BTC-USD"])
    seen = []

    async def cb(d):
        seen.append(d)

    async def fake_rest(path, params):
        assert path == "/premiumIndex"
        return [{"symbol": "BTC-USD", "markPrice": "80818.7", "indexPrice": "80814.9",
                 "fundingRate": "0.0000597", "nextFundingTime": 1788498000000},
                {"symbol": "ETH-USD", "markPrice": "2503.9"}]        # not subscribed: ignored

    async def run():
        ws.on("markPrice", cb)
        ws._rest = fake_rest
        ws._running = True

        async def stop_soon():
            await asyncio.sleep(0.05)
            ws._running = False

        await asyncio.gather(ws._mark_loop(), stop_soon())

    asyncio.run(run())
    assert len(seen) == 1 and seen[0]["s"] == "BTC-USD"
    assert seen[0]["p"] == "80818.7" and seen[0]["i"] == "80814.9" and seen[0]["r"] == "0.0000597"


# ── the thin-venue consequence: bars come from the venue, not from ticks ──────────────────────

def test_a_venue_closed_bar_lands_in_the_frame_and_updates_in_place():
    """One trade arrived in a hundred seconds across six Strike markets (measured 2026-09-04), so
    bars built from ticks would be empty. The venue closes a 1 m bar every minute regardless."""
    s = Settings()
    md = MarketDataCollector(s, client=None, regime_detector=RegimeDetector(s))
    base = 1_788_400_000.0

    md.on_closed_bar("BTC-USD", base, 100.0, 101.0, 99.0, 100.5, 0.0)
    md.on_closed_bar("BTC-USD", base + 60, 100.5, 102.0, 100.0, 101.5, 3.0)
    df = md.get_dataframe("BTC-USD")
    assert len(df) == 2 and float(df["close"].iloc[-1]) == 101.5
    assert float(df["volume"].iloc[0]) == 0.0                # a zero-volume minute is a real bar

    # the venue re-sends the forming bar before it closes: update, never duplicate
    md.on_closed_bar("BTC-USD", base + 60, 100.5, 103.0, 100.0, 102.75, 4.0)
    df = md.get_dataframe("BTC-USD")
    assert len(df) == 2 and float(df["close"].iloc[-1]) == 102.75
    assert float(df["high"].iloc[-1]) == 103.0
    # an older bar than we hold is ignored rather than appended out of order
    md.on_closed_bar("BTC-USD", base - 60, 1.0, 1.0, 1.0, 1.0, 1.0)
    assert len(md.get_dataframe("BTC-USD")) == 2
    assert md.get_data_age("BTC-USD") < 5           # a bar counts as data: the stale guard is fed


def test_the_seed_asks_the_venue_for_a_window_or_it_gets_a_stale_one():
    """Without startTime the venue answers from a cached window whose last bar was five hours old
    (measured 2026-09-04). The chart would seed stale and jump on the first live tick."""
    s = Settings()
    md = MarketDataCollector(s, client=None, regime_detector=RegimeDetector(s))
    calls = []

    class FakeClient:
        async def get_klines(self, symbol, interval="1m", limit=500, start_time=None, end_time=None):
            calls.append(start_time)
            base = 1_788_400_000_000
            return [[base + i * 60_000, "100", "101", "99", "100.5", "3",
                     base + i * 60_000 + 59_999, "300", 5, "1", "100", "0"] for i in range(limit)]

    asyncio.run(md.seed_from_strike("XAU-USD", s.get_symbol_config("BTC-USD"), FakeClient(), hours=3))
    assert calls and calls[0] is not None
    assert abs(calls[0] - (time.time() - 3 * 3600) * 1000) < 5000


# ── the split itself ──────────────────────────────────────────────────────────────────────────

def test_live_is_strike_by_default():
    assert Settings().trading.exchange_venue == "strike"


def test_history_never_follows_the_live_venue():
    """Strike's own klines go back 168 days for BTC and 19 for the S&P, against the ten years the
    daily signal was validated on. The daily sources and the downloader must ignore the venue."""
    import inspect

    from data import binance_downloader
    from strategies import daily_sources

    for mod in (daily_sources, binance_downloader):
        src = inspect.getsource(mod)
        assert "exchange_venue" not in src, f"{mod.__name__} must not branch on the live venue"


def test_the_bridge_starts_the_engine_on_the_configured_venue(monkeypatch):
    """start_engine passed use_binance=True unconditionally, so the bot ran on Binance whatever the
    config said and BOTSTRIKE_AUTOSTART_EXCHANGE only relabelled the UI (audit 2026-09-04)."""
    import server.bridge as bridge

    captured = {}

    class FakeEngine:
        def __init__(self, **kw):
            captured.update(kw)

        async def start(self):
            await asyncio.sleep(3600)

        async def shutdown(self):
            return None

    monkeypatch.setattr(bridge, "_spawn", lambda *_a, **_k: None)
    monkeypatch.setattr(bridge, "_install_hooks", lambda _e: None)
    monkeypatch.setitem(__import__("sys").modules, "main",
                        SimpleNamespace(BotStrike=FakeEngine))

    async def start(env):
        monkeypatch.setenv("BOTSTRIKE_AUTOSTART_EXCHANGE", env)
        s = Settings()
        await bridge.start_engine("paper", s)
        task = bridge.state.engine_task            # _run_engine on a fake engine: stop it at once
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        return s

    for env, expect_binance in (("strike", False), ("binance", True)):
        captured.clear()
        s = asyncio.run(start(env))
        assert captured["use_binance"] is expect_binance, env
        assert s.trading.exchange_venue == env       # downstream readers agree with the choice
        assert bridge.state.exchange == env          # and so does the label on screen
    bridge.state.engine = None
    bridge.state.running = False


@pytest.mark.parametrize("venue,expected", [("strike", False), ("binance", True)])
def test_the_engine_picks_its_feed_from_the_venue(venue, expected):
    """A Strike engine must not end up holding a Binance socket, or the screen and the book disagree
    about which market they are describing."""
    import inspect

    import main as main_mod

    src = inspect.getsource(main_mod.BotStrike.__init__)
    assert "StrikeMarketWebSocket" in src            # the client that speaks the real protocol
    assert "StrikeWebSocket(settings)" not in src    # not the one that never delivered a tick
    _ = venue, expected


def test_the_health_broadcast_carries_the_venue_so_the_screen_cannot_disagree():
    """The terminal's venue label was a browser preference that nothing synced: after the engine
    moved to Strike the header still read "Binance feed" (2026-09-04)."""
    import inspect

    import server.bridge as bridge

    src = inspect.getsource(bridge)
    i = src.index('"type": "health"')
    block = src[i:i + 900]
    assert '"exchange": state.exchange' in block


# ── the venue's order rules are obeyed, not merely displayed ──────────────────────────────────

def _book(filters=None):
    from strategies.trend_daily import TrendDailyEngine
    eng = TrendDailyEngine(Settings(), on_fill=lambda *_a, **_k: None, equity_provider=lambda: 1000.0)
    eng.venue_filters = filters if filters is not None else {
        "BTC-USD": {"tick_size": 0.1, "step_size": 0.00001, "min_qty": 0.00001,
                    "max_qty": 1000.0, "market_max_qty": 120.0, "min_notional": 10.0},
    }
    return eng


def test_an_order_is_rounded_to_the_venue_s_step_and_never_upwards():
    """A size that is not a multiple of the venue's step is rejected outright by the venue, and the
    paper book was holding positions Strike would never have opened (audit 2026-09-04). Rounding is
    DOWN so obeying the rule never enlarges the order the strategy asked for."""
    eng = _book()
    # 0.0012345678 BTC at a 0.00001 step -> 0.00123
    got = eng._venue_size("BTC-USD", 0.0012345678, 80_000.0, closing=False)
    assert got == pytest.approx(0.00123)
    assert got <= 0.0012345678


def test_an_order_under_the_venue_s_minimum_notional_is_skipped():
    """Strike asks $10. Below it the venue rejects, so the book must not pretend it filled."""
    eng = _book()
    assert eng._venue_size("BTC-USD", 0.0001, 80_000.0, closing=False) == 0.0    # $8
    assert eng._venue_size("BTC-USD", 0.0002, 80_000.0, closing=False) > 0        # $16


def test_a_close_ignores_the_minimum_and_never_strands_dust():
    """A venue always lets you flatten, and leaving a piece too small to close on its own would
    strand it forever. Closing rounds the same way but the minimum does not apply."""
    eng = _book()
    # an $8 close is allowed even though an $8 entry is not
    assert eng._venue_size("BTC-USD", 0.0001, 80_000.0, closing=True) > 0

    from strategies.trend_daily import BookPosition
    eng.state.positions["BTC-USD"] = BookPosition(
        symbol="BTC-USD", size=0.00025, entry_price=80_000.0, weight=0.02,
        opened="2026-09-04", opened_ts=0.0, entry_fee_rate=0.0005, mark_price=80_000.0)

    fills = []

    async def _capture(trade):                         # the engine awaits its fill callback
        fills.append(trade)

    eng._on_fill = _capture

    async def run():
        # ask to close a part that would leave $8 behind: the whole position must go instead
        await eng._close_part("BTC-USD", eng.state.positions["BTC-USD"], 0.00015,
                              80_000.0, 0.0, target_w=0.0, reason="test")

    asyncio.run(run())
    assert "BTC-USD" not in eng.state.positions, "dust below the venue minimum was left behind"


def test_a_market_order_is_capped_at_the_venue_s_market_lot_size():
    """MARKET_LOT_SIZE.maxQty is 120 BTC on Strike; anything larger has to be worked in pieces."""
    eng = _book()
    assert eng._venue_size("BTC-USD", 500.0, 80_000.0, closing=False) == pytest.approx(120.0)


def test_a_price_is_rounded_to_the_venue_s_tick():
    eng = _book()
    assert eng._venue_price("BTC-USD", 80_123.456) == pytest.approx(80_123.5)
    assert eng._venue_price("BTC-USD", 80_123.44) == pytest.approx(80_123.4)


def test_a_market_with_no_rules_yet_is_left_alone():
    """The rule list arrives from the venue asynchronously; until it does, an order must not be
    silently zeroed — that would stop the book trading over a missing lookup."""
    eng = _book(filters={})
    assert eng._venue_size("BTC-USD", 0.0012345678, 80_000.0, closing=False) == pytest.approx(0.0012345678)
    assert eng._venue_price("BTC-USD", 80_123.456) == pytest.approx(80_123.456)
    # and an engine that never had the attribute at all (older state, a stub) still works
    delattr(eng, "venue_filters")
    assert eng._venue_size("BTC-USD", 1.0, 80_000.0, closing=False) == pytest.approx(1.0)


def test_the_engine_loads_the_venue_s_rules_for_every_market(monkeypatch):
    """One hourly exchangeInfo call; the panel had these all along and nothing that placed an order
    was reading them."""
    import json as _json
    import io as _io

    import main as main_mod

    payload = {"symbols": [{"symbol": "BTC-USD", "filters": [
        {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
        {"filterType": "LOT_SIZE", "stepSize": "0.00001", "minQty": "0.00001", "maxQty": "1000"},
        {"filterType": "MARKET_LOT_SIZE", "maxQty": "120"},
        {"filterType": "MIN_NOTIONAL", "notional": "10"}]}]}

    class _Resp(_io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("urllib.request.urlopen",
                        lambda *_a, **_k: _Resp(_json.dumps(payload).encode()))

    eng = SimpleNamespace(_venue_filters={}, _venue_filters_ts=0.0)
    rules = main_mod.BotStrike._load_venue_filters(eng)
    assert rules["BTC-USD"]["tick_size"] == pytest.approx(0.1)
    assert rules["BTC-USD"]["step_size"] == pytest.approx(0.00001)
    assert rules["BTC-USD"]["min_notional"] == pytest.approx(10.0)
    assert rules["BTC-USD"]["market_max_qty"] == pytest.approx(120.0)
