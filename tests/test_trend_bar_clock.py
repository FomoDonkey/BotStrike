"""The daily rule on a 4 h clock (research 2026-09-20 §5): same lookbacks in days, evaluated at every
4 h bar close + delay on the Binance markets; Yahoo markets keep the daily decision at the execution
hour. The daily clock (trend_bar_hours = 24) must stay byte-for-byte the old behaviour - that is
what the rest of the suite asserts; this file covers the 4 h clock.
"""
import asyncio
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from config.settings import Settings
from core.types import Side
from strategies.trend_daily import TrendDailyEngine, DailyDataStore
from strategies.trend_daily_model import TrendParams, asset_weight, exit_ladder
from test_trend_daily import FakeStore, Fills, _frame

RUN_08 = datetime(2026, 9, 2, 8, 6, tzinfo=timezone.utc).timestamp()      # bar 04-08 closed at 08:00


def _frame4h(kind: str, days: int = 400, seed: int = 1, forming_open: float = None) -> pd.DataFrame:
    """4 h bars ending with the FORMING bar that opens at 08:00 on 2026-09-02 (the bar the 08:05 run
    fills at). Six bars a day; the drift per day matches _frame's daily drift."""
    rng = np.random.default_rng(seed)
    n = days * 6
    drift = {"up": 0.004, "down": -0.004}.get(kind, 0.0) / 6.0
    close = 100.0 * np.cumprod(1 + drift + rng.normal(0, 0.002 / np.sqrt(6), n))
    idx = pd.date_range(end=pd.Timestamp("2026-09-02 04:00"), periods=n, freq="4h")
    df = pd.DataFrame({"open": close * (1 - 0.0003), "high": close * 1.002, "low": close * 0.998,
                       "close": close, "volume": 1e4, "quote_volume": 8e5}, index=idx)
    o = forming_open if forming_open is not None else float(close[-1])
    df.loc[pd.Timestamp("2026-09-02 08:00")] = [o, o, o, o, 0.0, 0.0]
    return df.sort_index()


def _engine4h(tmp_path, frames, clock=RUN_08, **cfg):
    s = Settings()
    s.trading.trend_bar_hours = 4
    s.trading.trend_execution_hour_utc = 4
    s.trading.trend_pool = ",".join(frames)
    s.trading.trend_n_assets = len(frames)
    s.trading.trend_liq_enter_usd = 1e6
    s.trading.trend_liq_exit_usd = 5e5
    for k, v in cfg.items():
        setattr(s.trading, k, v)
    fills = Fills()
    eng = TrendDailyEngine(s, on_fill=fills, equity_provider=lambda: 1000.0, data_store=FakeStore(frames),
                           state_path=str(tmp_path / "trend4h.json"), clock=lambda: clock)
    return eng, fills, s


# ── schedule ────────────────────────────────────────────────────────────────────
def test_four_hour_clock_runs_at_every_bar_close_from_the_execution_hour(tmp_path):
    eng, _, _ = _engine4h(tmp_path, {"UPUSDT": _frame4h("up")})
    assert eng._run_hours() == [0, 4, 8, 12, 16, 20]
    close_dt, key = eng._last_scheduled(RUN_08)
    assert close_dt.hour == 8 and key == "2026-09-02T08"
    assert eng.is_due()                                   # 08:06 > 08:05, the T08 bar has not run
    at_0759 = datetime(2026, 9, 2, 7, 59, tzinfo=timezone.utc).timestamp()
    assert eng._last_scheduled(at_0759)[1] == "2026-09-02T04"
    eng.state.last_run_date = "2026-09-02T08"
    assert not eng.is_due()
    nxt = datetime.fromtimestamp(eng.next_run_ts(RUN_08), timezone.utc)
    assert (nxt.hour, nxt.minute) == (12, 5)
    eng._clock = lambda: datetime(2026, 9, 2, 12, 6, tzinfo=timezone.utc).timestamp()
    assert eng.is_due() and eng._last_scheduled(eng._clock())[1] == "2026-09-02T12"


def test_bars_per_day_follows_the_source_not_the_clock(tmp_path):
    eng, _, _ = _engine4h(tmp_path, {"UPUSDT": _frame4h("up"), "XAU-USD": _frame("up", seed=3)})
    assert eng._bars_per_day("UPUSDT") == 6 and eng._bars_per_day("XAU-USD") == 1
    close_dt, _ = eng._last_scheduled(RUN_08)
    assert eng._decision_for("UPUSDT", close_dt) == pd.Timestamp("2026-09-02 04:00")   # the bar just closed
    assert eng._forming_start("UPUSDT", close_dt) == pd.Timestamp("2026-09-02 08:00")
    assert eng._decision_for("XAU-USD", close_dt) == pd.Timestamp("2026-09-01")        # yesterday's daily bar
    assert eng._forming_start("XAU-USD", close_dt) == pd.Timestamp("2026-09-02")


def test_daily_clock_is_the_old_behaviour(tmp_path):
    s = Settings()
    assert s.trading.trend_bar_hours == 24
    eng = TrendDailyEngine(s, on_fill=Fills(), equity_provider=lambda: 1000.0, data_store=FakeStore({"UPUSDT": _frame("up")}),
                           state_path=str(tmp_path / "t.json"), clock=lambda: RUN_08)
    assert eng._bars_per_day("UPUSDT") == 1 and eng._run_hours() == [4]
    assert eng.is_due()                                   # 08:06 is after 04:05 and today has not run
    eng.state.last_run_date = "2026-09-02"
    assert not eng.is_due()
    assert datetime.fromtimestamp(eng.next_run_ts(), timezone.utc).strftime("%Y-%m-%dT%H:%M") == "2026-09-03T04:05"


def test_config_rejects_an_unaligned_clock():
    s = Settings()
    s.trading.trend_bar_hours = 12
    with pytest.raises(ValueError):
        s.validate()


# ── the model scales days into bars ─────────────────────────────────────────────
def test_model_lookbacks_and_vol_window_scale_with_bars_per_day():
    p = TrendParams(lookbacks=(10, 20), vol_window=30, target_vol=0.45, leverage_cap=3.0)
    df = _frame4h("up", days=200)
    close = df["close"].loc[: pd.Timestamp("2026-09-02 04:00")]
    w6 = asset_weight(close, p, 365, bars_per_day=6)
    w1 = asset_weight(close, p, 365, bars_per_day=1)
    assert float(w6.iloc[-1]) > 0                          # a rising series is long on the 4 h clock
    # the scaled version needs 6x the bars before it says anything; the unscaled one is a different rule
    assert w6.ne(0).idxmax() > w1.ne(0).idxmax()
    lad = exit_ladder(close, p, bars_per_day=6)
    assert lad["active"] == 2 and lad["total"] == 2 and lad["first_exit"] < float(close.iloc[-1])


# ── the run on the 4 h clock ────────────────────────────────────────────────────
def test_run_once_on_the_four_hour_clock_enters_and_keys_the_bar(tmp_path):
    frames = {"UPUSDT": _frame4h("up", forming_open=250.0)}
    eng, fills, s = _engine4h(tmp_path, frames)
    res = asyncio.run(eng.run_once())
    assert res["status"] == "ok" and res["date"] == "2026-09-02T08"
    t = next(t for t in fills.trades if t.side == Side.BUY)
    assert t.symbol == "UP-USD"
    assert t.price == pytest.approx(250.0 * (1 + s.trading.slippage_bps / 1e4))   # the forming 4 h bar's open
    assert "UPUSDT" in eng.state.positions and eng.state.last_run_date == "2026-09-02T08"
    assert eng.state.params_at_run["bar_hours"] == 4
    assert not eng.is_due()
    # the next bar: due at 12:05, keyed T12, and the tracking row is per bar
    eng._clock = lambda: datetime(2026, 9, 2, 12, 6, tzinfo=timezone.utc).timestamp()
    frames["UPUSDT"].loc[pd.Timestamp("2026-09-02 12:00")] = [252.0, 252.0, 252.0, 252.0, 0.0, 0.0]
    assert eng.is_due()
    asyncio.run(eng.run_once())
    assert eng.state.last_run_date == "2026-09-02T12"
    assert eng.state.tracking[-1]["date"] == "2026-09-02T12"
    assert eng.status()["tracking"]["runs_per_day"] == 6 and eng.status()["params"]["bar_hours"] == 4


def test_a_yahoo_market_only_decides_at_the_execution_hour(tmp_path):
    """Gold has daily bars: between execution hours its target is whatever it holds, so the 08:05
    and 12:05 runs never trade it; the 04:05 run does."""
    frames = {"UPUSDT": _frame4h("up"), "XAU-USD": _frame("up", seed=3)}
    eng, fills, s = _engine4h(tmp_path, frames)
    eng._venue_volumes = lambda syms: {x: 5e6 for x in syms}              # a mixed pool needs venue volumes
    asyncio.run(eng.run_once())                                            # 08:06 run
    assert "UPUSDT" in eng.state.positions
    assert "XAU-USD" not in eng.state.positions                            # not decided at 08:05
    assert not any(t.symbol == "XAU-USD" for t in fills.trades)
    # the 04:05 run of the next day decides gold on its settled daily bar
    frames["XAU-USD"].loc[pd.Timestamp("2026-09-03")] = frames["XAU-USD"].iloc[-1].values
    eng._clock = lambda: datetime(2026, 9, 3, 4, 6, tzinfo=timezone.utc).timestamp()
    assert eng.is_due() and eng._last_scheduled(eng._clock())[1] == "2026-09-03T04"
    asyncio.run(eng.run_once())
    assert "XAU-USD" in eng.state.positions
    held = eng.state.weights["XAU-USD"]
    # 08:05 again: gold keeps its weight, no trade on it
    frames["UPUSDT"].loc[pd.Timestamp("2026-09-03 04:00")] = frames["UPUSDT"].iloc[-1].values
    frames["UPUSDT"].loc[pd.Timestamp("2026-09-03 08:00")] = frames["UPUSDT"].iloc[-1].values
    eng._clock = lambda: datetime(2026, 9, 3, 8, 6, tzinfo=timezone.utc).timestamp()
    n_gold = sum(1 for t in fills.trades if t.symbol == "XAU-USD")
    asyncio.run(eng.run_once())
    assert eng.state.weights["XAU-USD"] == held
    assert sum(1 for t in fills.trades if t.symbol == "XAU-USD") == n_gold


def test_daily_view_resamples_the_fast_series(tmp_path):
    eng, _, _ = _engine4h(tmp_path, {"UPUSDT": _frame4h("up"), "XAU-USD": _frame("up", seed=3)})
    view = eng._daily_view({"UPUSDT": _frame4h("up"), "XAU-USD": _frame("up", seed=3)})
    assert (view["UPUSDT"].index.hour == 0).all()
    assert len(view["UPUSDT"]) == 401 and "quote_volume" in view["UPUSDT"].columns
    assert view["XAU-USD"] is not None and len(view["XAU-USD"]) == 401


def test_store_keeps_one_cache_per_interval(tmp_path):
    a = DailyDataStore(str(tmp_path), fetcher=lambda *a, **k: None)
    b = DailyDataStore(str(tmp_path), fetcher=lambda *a, **k: None, interval="4h")
    assert a._path("BTCUSDT").endswith("BTCUSDT.parquet")
    assert b._path("BTCUSDT").endswith("BTCUSDT.4h.parquet")


# ── continuity: the clock changes on a RUNNING book, nothing is reset ─────────────
def test_switching_a_running_book_to_the_four_hour_clock_keeps_its_record(tmp_path):
    """The live book has daily tracking rows and a daily run key when the clock moves to 4 h: the next
    4 h bar is simply due, the new row is keyed by the hour, the old rows stay, and the summary
    annualises each row by its own length."""
    frames = {"UPUSDT": _frame4h("up", forming_open=250.0)}
    eng, fills, s = _engine4h(tmp_path, frames)
    st = eng.state
    st.last_run_date = "2026-09-02"                                    # written by the daily clock at 04:05
    st.tracking = [{"date": "2026-09-01", "model_ret": 0.01, "paper_ret": 0.012, "turnover": 0.1},
                   {"date": "2026-09-02", "model_ret": -0.005, "paper_ret": -0.004, "turnover": 0.0}]
    st.opens_prev = {"UPUSDT": 240.0}
    assert eng.is_due()                                                # "2026-09-02" != "2026-09-02T08"
    asyncio.run(eng.run_once())
    assert st.last_run_date == "2026-09-02T08"
    assert [r["date"] for r in st.tracking] == ["2026-09-01", "2026-09-02", "2026-09-02T08"]
    summ = eng.tracking_summary()
    assert summ["days"] == 3 and summ["runs_per_day"] == 6
    assert summ["span_days"] == pytest.approx(2 + 4 / 24, abs=1e-6)     # two daily rows + one 4 h row
    assert summ["model_return"] == pytest.approx((1.01 * 0.995 * (1 + st.tracking[-1]["model_ret"])) - 1, abs=1e-9)
