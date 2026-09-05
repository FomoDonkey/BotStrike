"""The restart seed covers the whole live frame (33 h), paging the venue at 1 500 bars.

15 h (55 regime bars) was chosen to clear the detector's 50-bar minimum, but the ADX warm-up
and the 100-bar volatility percentile were not settled for a day after every restart.
"""
import asyncio
import math
from types import SimpleNamespace

from config.settings import Settings
from core.market_data import MAX_BARS, VENUE_KLINE_LIMIT, MarketDataCollector
from core.regime_detector import ADX_WARMUP_BARS, RegimeDetector


class _Client:
    """Answers `limit` one-minute bars from `start_time`, like the venue."""

    def __init__(self):
        self.calls = []

    async def get_klines(self, symbol, interval="1m", limit=500, start_time=None, end_time=None):
        self.calls.append((limit, start_time))
        base = int(start_time)
        return [[base + i * 60_000, "100", "101", "99", "100.5", "3", base + i * 60_000 + 59_999,
                 "300", 5, "1", "100", "0"] for i in range(limit)]


def test_strike_seed_pages_past_the_venue_limit():
    s = Settings()
    md = MarketDataCollector(s, client=None, regime_detector=RegimeDetector(s))
    client = _Client()
    asyncio.run(md.seed_from_strike("BTC-USD", s.get_symbol_config("BTC-USD"), client, hours=33))
    assert [c[0] for c in client.calls] == [VENUE_KLINE_LIMIT, 33 * 60 - VENUE_KLINE_LIMIT]
    assert client.calls[1][1] == client.calls[0][1] + VENUE_KLINE_LIMIT * 60_000   # second page starts after the first
    df = md.get_dataframe("BTC-USD")
    assert len(df) == 33 * 60 <= MAX_BARS
    assert (df["timestamp"].diff().dropna() == 60.0).all()                       # no overlap, no gap


def test_a_short_seed_is_still_one_request():
    s = Settings()
    md = MarketDataCollector(s, client=None, regime_detector=RegimeDetector(s))
    client = _Client()
    asyncio.run(md.seed_from_strike("XAU-USD", s.get_symbol_config("BTC-USD"), client, hours=2))
    assert [c[0] for c in client.calls] == [120]


def test_seed_hours_cover_the_volatility_percentile_and_the_adx_warmup():
    from main import BotStrike
    s = Settings()
    hours = BotStrike._seed_hours(SimpleNamespace(settings=s))
    tf = int(s.trading.regime_timeframe_min)
    needed = math.ceil(tf * (100 + ADX_WARMUP_BARS + 5) / 60.0) + 1
    assert hours == min(MAX_BARS // 60, needed)
    assert hours * 60 // tf >= 100 + ADX_WARMUP_BARS or hours == MAX_BARS // 60
