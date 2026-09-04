"""ChannelManager.broadcast must survive a client disconnecting while another send awaits
(CT 2026-09-02 04:28Z: 348x 'Set changed size during iteration', whole tick dropped)."""
import asyncio

import server.bridge as bridge


class _WS:
    def __init__(self, mgr, channel, victim=None, fail=False):
        self.mgr, self.channel, self.victim, self.fail = mgr, channel, victim, fail
        self.received = []

    async def send_text(self, msg):
        await asyncio.sleep(0)                      # yield like a real socket write
        if self.victim is not None:
            self.mgr.disconnect(self.channel, self.victim)   # concurrent disconnect
        if self.fail:
            raise RuntimeError("closed")
        self.received.append(msg)


def test_broadcast_survives_concurrent_disconnect_and_prunes_dead_clients():
    mgr = bridge.ChannelManager()
    clients = mgr._channels["market"]
    a, b, c = _WS(mgr, "market"), _WS(mgr, "market"), _WS(mgr, "market", fail=True)
    a.victim = b                                    # a's send removes b mid-iteration
    d = _WS(mgr, "market")
    for ws in (a, b, c, d):
        clients.add(ws)
    asyncio.run(mgr.broadcast("market", {"x": 1}))  # must not raise
    assert a.received == ['{"x": 1}'] and d.received == ['{"x": 1}']
    assert c not in clients and b not in clients and a in clients and d in clients
    assert mgr.client_count == 2


class _AcceptWS(_WS):
    async def accept(self):
        pass


def test_retained_snapshot_is_replayed_to_a_new_client():
    """The candle loop broadcasts only on change; a fresh page must not wait for the next trade
    (on BTC 91 % of bars are flat — the chart sat blank for tens of seconds, 2026-09-04)."""
    mgr = bridge.ChannelManager()
    # retained even when nobody is connected, and the newest one wins
    asyncio.run(mgr.broadcast("market", {"type": "candles", "symbol": "BTC-USD", "data": [1]}, retain="candles:BTC-USD"))
    asyncio.run(mgr.broadcast("market", {"type": "candles", "symbol": "BTC-USD", "data": [1, 2]}, retain="candles:BTC-USD"))
    asyncio.run(mgr.broadcast("market", {"type": "tick"}))          # a tick is never retained
    ws = _AcceptWS(mgr, "market")
    asyncio.run(mgr.connect("market", ws))
    assert ws.received == ['{"type": "candles", "symbol": "BTC-USD", "data": [1, 2]}']
    assert ws in mgr._channels["market"]
    # another channel's client sees nothing of it
    other = _AcceptWS(mgr, "trading")
    asyncio.run(mgr.connect("trading", other))
    assert other.received == []


def test_replay_failure_drops_the_client():
    mgr = bridge.ChannelManager()
    asyncio.run(mgr.broadcast("market", {"a": 1}, retain="a"))
    ws = _AcceptWS(mgr, "market", fail=True)
    asyncio.run(mgr.connect("market", ws))
    assert ws not in mgr._channels["market"]


class _MarketData:
    def __init__(self, df):
        self._df = df

    def get_dataframe(self, sym):
        return self._df


def test_engine_klines_resamples_the_engine_frame(monkeypatch):
    """A streamed symbol's history comes from the engine's own 1 m frame, bucketed on the
    interval's grid; a symbol the engine does not stream falls through to the venue (None)."""
    import types
    import pandas as pd
    t0 = 1_699_999_800                      # a multiple of 300 s: the 5 m grid starts here
    n = 20
    df = pd.DataFrame({
        "timestamp": [(t0 + 60 * i) * 1000 for i in range(n)],   # milliseconds, like the feed
        "open": [float(i) for i in range(n)],
        "high": [float(i + 1) for i in range(n)],
        "low": [float(i - 1) for i in range(n)],
        "close": [i + 0.5 for i in range(n)],
        "volume": [1.0] * n,
    })
    engine = types.SimpleNamespace(
        market_data=_MarketData(df),
        settings=types.SimpleNamespace(symbols=[types.SimpleNamespace(symbol="BTC-USD")]),
    )
    monkeypatch.setattr(bridge.state, "engine", engine)
    monkeypatch.setattr(bridge.state, "running", True)
    monkeypatch.setattr(bridge, "_engine_store", lambda sym: None)      # no store on disk here

    bars = bridge._engine_klines("BTC-USD", "5m", 10)
    assert [b["timestamp"] for b in bars] == [t0, t0 + 300, t0 + 600, t0 + 900]
    first = bars[0]
    assert (first["open"], first["high"], first["low"], first["close"], first["volume"]) == (0.0, 5.0, -1.0, 4.5, 5.0)
    assert bridge._engine_klines("BTC-USD", "5m", 2) == bars[-2:]           # `limit` keeps the newest
    one = bridge._engine_klines("BTC-USD", "1m", 3)
    assert [b["timestamp"] for b in one] == [t0 + 17 * 60, t0 + 18 * 60, t0 + 19 * 60]
    assert one[-1]["close"] == 19.5
    assert bridge._engine_klines("ETH-USD", "5m", 10) is None
    assert bridge._engine_klines("BTC-USD", "7m", 10) is None                # not an interval it knows
    monkeypatch.setattr(bridge.state, "running", False)
    assert bridge._engine_klines("BTC-USD", "5m", 10) is None


def test_engine_klines_reach_back_into_the_disk_store(monkeypatch):
    """The live frame holds a day; the depth of a 4 h or 1 d chart comes from the 90-day store,
    and the live frame owns every bar from its first one on (it is the fresher of the two)."""
    import types
    import pandas as pd
    t0 = 1_699_999_800
    live_start = t0 + 60 * 60                        # the live frame begins an hour after the store
    live = pd.DataFrame({
        "timestamp": [(live_start + 60 * i) * 1000 for i in range(30)],
        "open": [100.0] * 30, "high": [101.0] * 30, "low": [99.0] * 30, "close": [100.5] * 30,
        "volume": [1.0] * 30,
    })
    # the store overlaps the live frame's first bars with DIFFERENT numbers — those must lose
    store = pd.DataFrame({
        "timestamp": [float(t0 + 60 * i) for i in range(90)],
        "open": [10.0] * 90, "high": [11.0] * 90, "low": [9.0] * 90, "close": [10.5] * 90,
        "volume": [2.0] * 90,
    })
    engine = types.SimpleNamespace(
        market_data=_MarketData(live),
        settings=types.SimpleNamespace(symbols=[types.SimpleNamespace(symbol="BTC-USD")]),
    )
    monkeypatch.setattr(bridge.state, "engine", engine)
    monkeypatch.setattr(bridge.state, "running", True)
    monkeypatch.setattr(bridge, "_engine_store", lambda sym: store if sym == "BTC-USD" else None)

    bars = bridge._engine_klines("BTC-USD", "5m", 100)
    assert [b["timestamp"] for b in bars] == [t0 + 300 * i for i in range(18)]     # 60 + 30 min
    assert bars[0]["close"] == 10.5 and bars[0]["volume"] == 10.0                  # store only
    assert bars[12]["open"] == 100.0 and bars[12]["volume"] == 5.0                 # live from its first bar
    assert bars[-1]["close"] == 100.5
    # `limit` keeps the newest, and 1 m bars come straight through
    assert [b["timestamp"] for b in bridge._engine_klines("BTC-USD", "1m", 2)] == [live_start + 28 * 60, live_start + 29 * 60]


def test_venue_kline_rows_walk_forward_past_the_venues_cap():
    """The venue answers the FIRST bars after startTime and caps each answer at 500: one request
    for 1,000 bars returned the oldest 500 and the chart ended months in the past (2026-09-04)."""
    now = 2_000_000 * 60.0
    # the venue holds 1,500 one-minute bars, the newest opened one minute ago
    held = [[int((now - (1500 - i) * 60) * 1000), 1, 1, 1, 1, 1] for i in range(1500)]
    calls = []

    async def fetch(params):
        calls.append(params["startTime"])
        return [k for k in held if k[0] >= params["startTime"]][:500]

    rows = asyncio.run(bridge._venue_kline_rows(fetch, "1m", 1000, now=now))
    assert len(rows) == 1000
    assert rows[0][0] == held[500][0] and rows[-1][0] == held[-1][0]
    assert len(calls) == 2                       # 500 + 500, then the present was reached

    # a thin market that answers fewer bars than the cap is one request, and keeps them all
    thin = held[-7:]

    async def fetch_thin(params):
        calls.append(params["startTime"])
        return [k for k in thin if k[0] >= params["startTime"]]

    calls.clear()
    rows = asyncio.run(bridge._venue_kline_rows(fetch_thin, "1m", 1000, now=now))
    assert rows == thin and len(calls) == 1

    # a market whose last trade is days old: one page, then an empty one, and no spin
    stale = held[600:603]

    async def fetch_stale(params):
        calls.append(params["startTime"])
        return [k for k in stale if k[0] >= params["startTime"]]

    calls.clear()
    rows = asyncio.run(bridge._venue_kline_rows(fetch_stale, "1m", 1000, now=now))
    assert rows == stale and len(calls) == 2
