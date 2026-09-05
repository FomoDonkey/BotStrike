"""Daily bars the trend book decides on must be SETTLED bars.

Found 2026-09-05 on the CT: silver's cached "3 Sep" bar was o 67.63 h 67.69 l 67.51 c 67.59 — one
hour of the 4 Sep Globex session, which Yahoo shows under the previous date between 18:00 ET
and midnight ET. Read at 00:05 UTC and cached as complete, it doubled the silver weight on a
breakout that never printed. Two guards: the Yahoo fetcher never returns the current exchange
day's bar, and the cache re-reads its last days on every refresh so a bar cached before its
source settled it is replaced.
"""
import json
from datetime import datetime, timezone

import pandas as pd
import pytest

from strategies import daily_sources
from strategies.trend_daily import DailyDataStore, HEAL_DAYS


def _payload(rows, tz="America/New_York"):
    """rows: [(epoch_seconds, open, high, low, close, volume)] as Yahoo returns them."""
    return {"chart": {"result": [{
        "meta": {"exchangeTimezoneName": tz},
        "timestamp": [r[0] for r in rows],
        "indicators": {"quote": [{
            "open": [r[1] for r in rows], "high": [r[2] for r in rows], "low": [r[3] for r in rows],
            "close": [r[4] for r in rows], "volume": [r[5] for r in rows],
        }]},
    }]}}


class _Resp:
    def __init__(self, data):
        self._b = json.dumps(data).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_yahoo_bars_are_dated_by_the_exchange_day_and_todays_bar_is_dropped(monkeypatch):
    # 20:05 ET on 3 Sep 2026 = 00:05 UTC on 4 Sep: the run time that cached the polluted bar
    now_utc = datetime(2026, 9, 4, 0, 5, tzinfo=timezone.utc)
    monkeypatch.setattr(pd.Timestamp, "now", classmethod(lambda cls, tz=None: pd.Timestamp(now_utc).tz_convert(tz)))
    sep2 = int(datetime(2026, 9, 2, 4, 0, tzinfo=timezone.utc).timestamp())      # 00:00 ET 2 Sep
    sep3 = int(datetime(2026, 9, 3, 4, 0, tzinfo=timezone.utc).timestamp())      # 00:00 ET 3 Sep
    live = int(datetime(2026, 9, 3, 23, 0, tzinfo=timezone.utc).timestamp())     # the new session, stamped "3 Sep" ET
    rows = [(sep2, 63.36, 65.05, 63.36, 64.723, 1138), (sep3, 65.555, 67.30, 65.44, 66.973, 71),
            (live, 67.63, 67.69, 67.51, 67.59, 526)]
    monkeypatch.setattr(daily_sources.urllib.request, "urlopen", lambda req, timeout=30: _Resp(_payload(rows)))
    df = daily_sources.fetch_daily_yahoo("XAG-USD")
    assert [str(d.date()) for d in df.index] == ["2026-09-02"]          # 3 Sep is today in New York: not settled
    assert df["close"].iloc[-1] == pytest.approx(64.723)
    # after midnight ET the 3 Sep bar is stable and comes through, the live 4 Sep one does not
    now_utc = datetime(2026, 9, 4, 4, 5, tzinfo=timezone.utc)
    monkeypatch.setattr(pd.Timestamp, "now", classmethod(lambda cls, tz=None: pd.Timestamp(now_utc).tz_convert(tz)))
    sep4_live = int(datetime(2026, 9, 4, 4, 0, tzinfo=timezone.utc).timestamp())
    rows2 = [(sep2, 63.36, 65.05, 63.36, 64.723, 1138), (sep3, 65.555, 67.30, 65.44, 66.973, 71),
             (sep4_live, 67.63, 67.81, 65.33, 66.82, 1000)]
    monkeypatch.setattr(daily_sources.urllib.request, "urlopen", lambda req, timeout=30: _Resp(_payload(rows2)))
    df = daily_sources.fetch_daily_yahoo("XAG-USD")
    assert [str(d.date()) for d in df.index] == ["2026-09-02", "2026-09-03"]
    assert df["close"].iloc[-1] == pytest.approx(66.973)


def test_cache_rereads_its_last_days_and_lets_the_settled_bar_win(tmp_path):
    calls = []
    good = {"2026-09-01": 64.618, "2026-09-02": 64.723, "2026-09-03": 66.973}

    def fetcher(symbol, start_ms):
        calls.append(start_ms)
        idx = pd.to_datetime(list(good))
        df = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": list(good.values()), "volume": 1.0,
                           "quote_volume": 1.0}, index=idx)
        return df[df.index >= pd.Timestamp(start_ms, unit="ms").normalize()]

    store = DailyDataStore(data_dir=str(tmp_path), fetcher=fetcher)
    # a cache holding a polluted 3 Sep close, as the CT did
    bad = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": [64.618, 64.723, 67.59], "volume": 1.0,
                        "quote_volume": 1.0}, index=pd.to_datetime(list(good)))
    bad.to_parquet(store._path("XAG-USD"))
    out = store.load(["XAG-USD"], pd.Timestamp("2026-09-05"), refresh=True, min_days=1)["XAG-USD"]
    assert out.loc["2026-09-03", "close"] == pytest.approx(66.973)          # the settled bar replaced it
    assert pd.Timestamp(calls[0], unit="ms") <= pd.Timestamp("2026-09-03") - pd.Timedelta(days=HEAL_DAYS - 1)
    assert pd.read_parquet(store._path("XAG-USD")).loc["2026-09-03", "close"] == pytest.approx(66.973)
