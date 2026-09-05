"""The venue liquidity floor is applied every run, and never silently skipped.

Found 2026-09-05: the first universe pick (3 Sep) ran with no venue volumes - the fetch failed and
the code fell back to "no floor" - and admitted the S&P and silver perps. Two days later the book
held 416 $ of an S&P perp that printed 788 $ of volume in 24 h: 53 % of the venue's day. Live,
that position could not be exited. Now: a member under the EXIT floor leaves the same day and is
replaced; with no volumes for a mixed pool the universe is left unchanged and the run says so.
"""
import asyncio

import pandas as pd

from strategies.trend_daily_model import TrendParams, venue_floors
from test_trend_daily import NOW, _engine, _frame


def _mixed_frames():
    return {"BTC-USD": _frame("up", seed=1), "SOL-USD": _frame("up", seed=2),
            "SP500-USD": _frame("up", seed=3), "XAU-USD": _frame("up", seed=4)}


def _month_key(ts=NOW):
    return pd.Timestamp(ts, unit="s").strftime("%Y-%m")


def test_floors_scale_with_one_position_and_exit_stays_under_enter():
    p = TrendParams(liq_exit_usd_venue=5_000.0, liq_venue_multiple=50.0, liq_venue_exit_multiple=10.0,
                    position_notional=504.0)
    enter, exit_ = venue_floors(p)
    assert enter == 25_200.0 and exit_ == 5_040.0
    p.position_notional = 10.0                     # tiny account: the hard minimum rules both
    assert venue_floors(p) == (5_000.0, 5_000.0)


def test_an_illiquid_member_leaves_the_same_day_and_is_replaced(tmp_path, monkeypatch):
    eng, fills, s = _engine(tmp_path, _mixed_frames(), trend_min_order_usd=1e12)   # never actually trade
    st = eng.state
    st.universe = ["BTC-USD", "SP500-USD", "XAU-USD"]
    st.universe_month = _month_key()
    st.universe_key = f"{','.join(eng.pool())}|{s.trading.trend_n_assets}"
    # one position = 1000 x 3 / 3 = 1000 $ -> enter 50 000 $, exit 10 000 $ of 24 h venue volume
    vol = {"BTC-USD": 1.6e6, "SOL-USD": 3.0e5, "SP500-USD": 788.0, "XAU-USD": 1.1e5}
    monkeypatch.setattr(eng, "_venue_volumes", lambda syms: vol)
    asyncio.run(eng.run_once(NOW))
    assert "SP500-USD" not in st.universe
    assert st.universe[:2] == ["BTC-USD", "XAU-USD"] and "SOL-USD" in st.universe
    liq = eng.status()["liquidity"]
    assert liq["enter_floor"] == 50_000.0 and liq["exit_floor"] == 10_000.0 and liq["available"]
    assert liq["markets"]["SP500-USD"] == {"venue_24h": 788.0, "member": False, "ok_enter": False, "ok_exit": False}
    assert liq["markets"]["XAU-USD"]["ok_exit"] and liq["markets"]["XAU-USD"]["ok_enter"]
    assert st.liquidity_note == ""


def test_no_venue_volumes_means_no_universe_change_and_a_note(tmp_path, monkeypatch):
    eng, fills, s = _engine(tmp_path, _mixed_frames(), trend_min_order_usd=1e12)
    st = eng.state
    st.universe = ["BTC-USD", "SP500-USD", "XAU-USD"]
    st.universe_month = "2000-01"                   # a re-pick is due
    st.universe_key = "stale"
    monkeypatch.setattr(eng, "_venue_volumes", lambda syms: {})
    asyncio.run(eng.run_once(NOW))
    assert st.universe == ["BTC-USD", "SP500-USD", "XAU-USD"]          # unchanged, not re-picked blind
    assert st.universe_month == "2000-01"                               # so tomorrow retries
    assert "unavailable" in st.liquidity_note
    assert eng.status()["liquidity"]["available"] is False


def test_a_first_pick_without_volumes_holds_nothing(tmp_path, monkeypatch):
    eng, fills, s = _engine(tmp_path, _mixed_frames())
    monkeypatch.setattr(eng, "_venue_volumes", lambda syms: {})
    asyncio.run(eng.run_once(NOW))
    assert eng.state.universe == [] and fills.trades == []
    assert "unavailable" in eng.state.liquidity_note


def test_a_crypto_only_pool_needs_no_venue_volumes(tmp_path, monkeypatch):
    eng, fills, s = _engine(tmp_path, {"BTC-USD": _frame("up", seed=1), "ETH-USD": _frame("up", seed=2)})
    called = []
    monkeypatch.setattr(eng, "_venue_volumes", lambda syms: called.append(syms) or {})
    asyncio.run(eng.run_once(NOW))
    assert called == []                                                 # single-class rule: data volume
    assert eng.state.universe and eng.state.liquidity_note == ""
