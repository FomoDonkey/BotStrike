"""Venue liquidity is judged on a median of informative readings, not on one 24 h tick.

Sunday 2026-09-20 04:05Z on the CT: gold showed 144 $ of 24 h volume at Strike because the CME
session had been closed since Friday; the one-reading floor dropped it from the universe (a forced
UNIVERSE exit at -2.86 $) and the re-pick let BNB in on a single spike 6 % over the enter floor.
"""
import asyncio
from datetime import datetime, timezone

import pandas as pd
import pytest

from strategies.trend_daily import (BookPosition, TrendState, VENUE_VOL_DAYS, _venue_reading_informative,
                                    to_ui_symbol)
from test_trend_daily import _engine, _frame

SUNDAY = datetime(2026, 9, 20, 4, 5, tzinfo=timezone.utc).timestamp()
MONDAY = datetime(2026, 9, 21, 4, 5, tzinfo=timezone.utc).timestamp()
TUESDAY = datetime(2026, 9, 22, 4, 5, tzinfo=timezone.utc).timestamp()
SATURDAY = datetime(2026, 9, 19, 4, 5, tzinfo=timezone.utc).timestamp()


def test_tradfi_readings_on_sunday_and_monday_are_not_informative():
    for ts, expect in ((SATURDAY, True), (SUNDAY, False), (MONDAY, False), (TUESDAY, True)):
        assert _venue_reading_informative("XAU-USD", pd.Timestamp(ts, unit="s", tz="UTC")) is expect
        assert _venue_reading_informative("BTCUSDT", pd.Timestamp(ts, unit="s", tz="UTC")) is True


def test_sunday_zero_does_not_drop_a_tradfi_member(tmp_path):
    eng, _, _ = _engine(tmp_path, {"UPUSDT": _frame("up")})
    st = eng.state
    st.venue_volume_log = {"XAU-USD": [["2026-09-18", 70_000.0], ["2026-09-19", 71_634.0]]}
    eff = eng._effective_venue_volumes({"XAU-USD": 144.0, "UPUSDT": 5e6}, SUNDAY, "2026-09-20")
    assert eff["XAU-USD"] == pytest.approx(70_817.0)          # median of the two real readings
    assert st.venue_volume_log["XAU-USD"][-1][0] == "2026-09-19"   # the Sunday tick was not logged
    assert eff["UPUSDT"] == 5e6 and st.venue_volume_log["UPUSDT"] == [["2026-09-20", 5e6]]


def test_a_single_spike_does_not_clear_the_enter_floor(tmp_path):
    eng, _, _ = _engine(tmp_path, {"UPUSDT": _frame("up")})
    eng.state.venue_volume_log = {"BNBUSDT": [[f"2026-09-1{i}", 15_000.0] for i in range(5, 9)]}
    eff = eng._effective_venue_volumes({"BNBUSDT": 27_600.0}, SUNDAY, "2026-09-20")
    assert eff["BNBUSDT"] == 15_000.0                          # median: 4 x 15k + 1 spike
    # ... but a market that really faded still leaves once most readings say so
    eng.state.venue_volume_log = {"SP500-USD": [[f"2026-09-1{i}", 800.0] for i in range(5, 9)]}
    eff = eng._effective_venue_volumes({"SP500-USD": 900.0}, TUESDAY, "2026-09-22")
    assert eff["SP500-USD"] == 800.0


def test_log_keeps_one_row_per_day_and_at_most_n_days(tmp_path):
    eng, _, _ = _engine(tmp_path, {"UPUSDT": _frame("up")})
    for i in range(1, 12):
        day = f"2026-09-{i:02d}"
        ts = datetime(2026, 9, i, 4, 5, tzinfo=timezone.utc).timestamp()
        eng._effective_venue_volumes({"BTCUSDT": float(i)}, ts, day)
    eng._effective_venue_volumes({"BTCUSDT": 99.0}, datetime(2026, 9, 11, 9, tzinfo=timezone.utc).timestamp(),
                                 "2026-09-11")                 # a re-run replaces the day's row
    rows = eng.state.venue_volume_log["BTCUSDT"]
    assert len(rows) == VENUE_VOL_DAYS and rows[-1] == ["2026-09-11", 99.0]
    assert len({r[0] for r in rows}) == VENUE_VOL_DAYS


def test_market_without_informative_reading_is_absent_not_zero(tmp_path):
    eng, _, _ = _engine(tmp_path, {"UPUSDT": _frame("up")})
    eff = eng._effective_venue_volumes({"XAU-USD": 144.0}, SUNDAY, "2026-09-20")
    assert "XAU-USD" not in eff            # enter floor refuses it, exit check leaves it alone


def test_state_round_trips_the_volume_log():
    st = TrendState(venue_volume_log={"BTCUSDT": [["2026-09-19", 1.5e6]]})
    again = TrendState.from_json(st.to_json())
    assert again.venue_volume_log == {"BTCUSDT": [["2026-09-19", 1.5e6]]}


def test_run_once_keeps_gold_on_a_sunday_with_a_closed_session(tmp_path):
    """The real code path: a mixed pool, gold a member with a healthy log, the venue reporting 144 $
    for it on Sunday. It must stay in the universe and must not be closed as UNIVERSE."""
    frames = {"UPUSDT": _frame("up"), "XAU-USD": _frame("up", seed=7)}
    eng, fills, _ = _engine(tmp_path, frames, trend_n_assets=2, clock=SUNDAY)
    eng._venue_volumes = lambda syms: {"UPUSDT": 5_000_000.0, "XAU-USD": 144.0}
    st = eng.state
    st.universe = ["UPUSDT", "XAU-USD"]
    st.universe_month = "2026-09"
    st.universe_key = f"{','.join(eng.pool())}|2"
    st.venue_volume_log = {"XAU-USD": [["2026-09-18", 70_000.0], ["2026-09-19", 71_634.0]],
                           "UPUSDT": [["2026-09-19", 5_000_000.0]]}
    st.positions["XAU-USD"] = BookPosition(symbol="XAU-USD", size=0.04, entry_price=4_400.0, entry_fee_rate=0.0005,
                                           weight=0.16, opened="2026-09-03", opened_ts=SUNDAY - 17 * 86_400,
                                           mark_price=4_400.0)
    st.weights["XAU-USD"] = 0.16
    asyncio.run(eng.run_once())
    assert "XAU-USD" in st.universe
    assert not any(t.order_id.startswith("trend_universe_") for t in fills.trades)
    assert eng.last_liquidity["markets"][to_ui_symbol("XAU-USD")]["ok_exit"] is True
