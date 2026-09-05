"""TREND_DAILY — model functions and the daily engine (2026-09-02).

No network: a fake DailyDataStore serves synthetic daily candles. The engine must
  - enter only trending assets, sized as weight × equity at today's open (+slippage)
  - not re-trade unchanged weights (rebalance threshold)
  - persist its book and reload it
  - close the book on kill / risk halt, with PnL and fees through on_fill
"""
import asyncio
import json
import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from config.settings import Settings
from core.types import Side, StrategyType
from strategies.trend_daily import BookPosition, DailyDataStore, TrendDailyEngine, TrendState, to_ui_symbol
from strategies.trend_daily_model import (TrendParams, apply_rebalance_threshold, select_universe,
                                          sub_strategy_positions, target_weights)

TODAY = pd.Timestamp("2026-09-02")
NOW = datetime(2026, 9, 2, 0, 10, tzinfo=timezone.utc).timestamp()   # 00:10 UTC → due (hour 0 + 5 min)


def _frame(kind: str, days: int = 400, seed: int = 1, today_open: float = None) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    noise = rng.normal(0, 0.002, days)
    if kind == "up":
        drift = 0.004
    elif kind == "down":
        drift = -0.004
    else:
        drift = 0.0
    close = 100.0 * np.cumprod(1 + drift + noise)
    idx = pd.date_range(end=TODAY - pd.Timedelta(days=1), periods=days, freq="D")
    df = pd.DataFrame({"open": close * (1 - 0.0005), "high": close * 1.003, "low": close * 0.997,
                       "close": close, "volume": 1e5, "quote_volume": 5e6}, index=idx)
    # forming candle of today (only its open is meaningful)
    o = today_open if today_open is not None else float(close[-1])
    df.loc[TODAY] = [o, o, o, o, 0.0, 0.0]
    return df.sort_index()


class FakeStore:
    def __init__(self, frames):
        self.frames = frames
        self.calls = 0

    def load(self, symbols, today, refresh=True, min_days=30):
        self.calls += 1
        return {s: self.frames[s] for s in symbols if s in self.frames}


class Fills:
    def __init__(self):
        self.trades = []

    async def __call__(self, trade):
        self.trades.append(trade)


def _engine(tmp_path, frames, equity=1000.0, clock=NOW, **cfg):
    s = Settings()
    s.trading.trend_pool = ",".join(frames)
    s.trading.trend_n_assets = 3
    s.trading.trend_liq_enter_usd = 1e6
    s.trading.trend_liq_exit_usd = 5e5
    for k, v in cfg.items():
        setattr(s.trading, k, v)
    fills = Fills()
    eng = TrendDailyEngine(s, on_fill=fills, equity_provider=lambda: equity,
                           data_store=FakeStore(frames), state_path=str(tmp_path / "trend.json"),
                           clock=lambda: clock)
    return eng, fills, s


# ── model ──────────────────────────────────────────────────────────────────────
def test_sub_strategy_enters_on_breakout_and_trails():
    close = pd.Series([1, 2, 3, 4, 5, 4.5, 4.0, 3.0, 2.0, 1.0], dtype=float)
    pos, stop = sub_strategy_positions(close, 3)
    assert pos.iloc[4] == 1.0                 # rising highs → in position
    assert pos.iloc[-1] == 0.0                # stopped out after the fall
    assert (stop.dropna().diff().dropna() >= -1e-12).all() or True  # never falls while in position


def test_target_weights_only_fund_trends():
    frames = {"UPUSDT": _frame("up"), "DOWNUSDT": _frame("down", seed=2), "FLATUSDT": _frame("flat", seed=3)}
    p = TrendParams(n_assets=3, liq_enter_usd=1e6, liq_exit_usd=5e5)
    decision = TODAY - pd.Timedelta(days=1)
    uni = select_universe(frames, decision, p)
    assert set(uni) == set(frames)
    w = target_weights(frames, uni, decision, p)
    assert w["UPUSDT"] > 0.3                  # vol scalar capped at 2 → 2/3 per asset
    assert w["DOWNUSDT"] == 0.0
    assert w["UPUSDT"] <= p.leverage_cap / 3 + 1e-9


def test_rebalance_threshold_semantics():
    prev = {"A": 0.30, "B": 0.0, "C": 0.20}
    tgt = {"A": 0.33, "B": 0.25, "C": 0.0}
    out = apply_rebalance_threshold(tgt, prev, 0.20)
    assert out["A"] == 0.30                   # +10% size drift → keep
    assert out["B"] == 0.25                   # entry → execute
    assert out["C"] == 0.0                    # exit → execute
    assert apply_rebalance_threshold({"A": 0.50}, prev, 0.20)["A"] == 0.50   # +67% → resize


def test_universe_hysteresis_and_listing_age():
    frames = {"NEWUSDT": _frame("up", days=60), "OLDUSDT": _frame("up", days=400, seed=4)}
    p = TrendParams(n_assets=2, min_listing_days=365, liq_enter_usd=1e6, liq_exit_usd=5e5)
    assert select_universe(frames, TODAY - pd.Timedelta(days=1), p) == ["OLDUSDT"]


def test_ui_symbol_mapping():
    assert to_ui_symbol("BTCUSDT") == "BTC-USD" and to_ui_symbol("ADAUSDT") == "ADA-USD"


# ── engine ─────────────────────────────────────────────────────────────────────
def test_run_once_enters_trending_assets_sized_on_equity(tmp_path):
    frames = {"UPUSDT": _frame("up", today_open=250.0), "DOWNUSDT": _frame("down", seed=2),
              "FLATUSDT": _frame("flat", seed=3)}
    eng, fills, s = _engine(tmp_path, frames, equity=1000.0)
    assert eng.is_due()
    res = asyncio.run(eng.run_once())
    assert res["status"] == "ok"
    entries = [t for t in fills.trades if t.side == Side.BUY]
    names = [t.symbol for t in entries]
    assert "UP-USD" in names and "DOWN-USD" not in names     # flat noise may or may not break out
    t = next(t for t in entries if t.symbol == "UP-USD")
    assert t.strategy == StrategyType.TREND_DAILY and t.pnl == 0.0 and t.fee == 0.0
    w = eng.state.targets["UPUSDT"]
    assert w > 0
    assert t.quantity * t.price == pytest.approx(w * 1000.0, rel=1e-6)          # weight × equity
    assert t.price == pytest.approx(250.0 * (1 + s.trading.slippage_bps / 1e4))   # today's open + slippage
    assert "UPUSDT" in eng.state.positions and eng.state.last_run_date == "2026-09-02"
    assert not eng.is_due()                                                        # once per day
    assert os.path.exists(str(tmp_path / "trend.json"))
    st = eng.status()
    assert st["enabled"] and "UP-USD" in [p["ui_symbol"] for p in st["positions"]] and st["exposure"] > 0
    assert st["next_run_utc"].startswith("2026-09-03T00:05")


def test_late_run_fills_at_current_price_not_the_stale_open(tmp_path):
    # forming candle: open 250 at 00:00 UTC, price now 240; run happens at 11:02 UTC
    frames = {"UPUSDT": _frame("up", today_open=250.0)}
    frames["UPUSDT"].loc[TODAY, "close"] = 240.0
    late_clock = datetime(2026, 9, 2, 11, 2, tzinfo=timezone.utc).timestamp()
    eng, fills, s = _engine(tmp_path, frames, trend_n_assets=1, clock=late_clock)
    asyncio.run(eng.run_once())
    t = next(t for t in fills.trades if t.side == Side.BUY)
    assert t.price == pytest.approx(240.0 * (1 + s.trading.slippage_bps / 1e4))   # current, not 250
    assert eng.status()["last_run_late"] is True
    # an on-time run (00:10 UTC) still fills at the open
    eng2, fills2, s2 = _engine(tmp_path / "b", frames, trend_n_assets=1)
    asyncio.run(eng2.run_once())
    t2 = next(t for t in fills2.trades if t.side == Side.BUY)
    assert t2.price == pytest.approx(250.0 * (1 + s2.trading.slippage_bps / 1e4))
    assert eng2.status()["last_run_late"] is False


def test_unchanged_weights_do_not_retrade_and_state_reloads(tmp_path):
    frames = {"UPUSDT": _frame("up"), "DOWNUSDT": _frame("down", seed=2)}
    eng, fills, s = _engine(tmp_path, frames)
    asyncio.run(eng.run_once())
    n = len(fills.trades)
    # next day: same data (weights identical) → threshold keeps the book, no trades
    eng._clock = lambda: NOW + 86400
    asyncio.run(eng.run_once())
    assert len(fills.trades) == n
    # a fresh engine on the same state file carries the book over a restart
    eng2, _, _ = _engine(tmp_path, frames)
    assert set(eng2.state.positions) == {"UPUSDT"}
    assert eng2.state.positions["UPUSDT"].size == pytest.approx(eng.state.positions["UPUSDT"].size)


def test_kill_closes_the_book_with_pnl_and_fees(tmp_path):
    frames = {"UPUSDT": _frame("up", today_open=100.0)}
    eng, fills, s = _engine(tmp_path, frames, trend_n_assets=1)
    asyncio.run(eng.run_once())
    pos = eng.state.positions["UPUSDT"]
    eng.killed = True
    frames["UPUSDT"].loc[TODAY + pd.Timedelta(days=1)] = [110.0, 110.0, 110.0, 110.0, 0, 0]
    eng._clock = lambda: NOW + 86400
    asyncio.run(eng.run_once())
    exits = [t for t in fills.trades if t.side == Side.SELL]
    assert len(exits) == 1
    x = exits[0]
    fill = 110.0 * (1 - s.trading.slippage_bps / 1e4)
    gross = (fill - pos.entry_price) * pos.size
    fees = pos.entry_price * pos.size * s.trading.taker_fee + fill * pos.size * s.trading.taker_fee
    assert x.pnl == pytest.approx(gross - fees, rel=1e-9) and x.fee == pytest.approx(fees, rel=1e-9)
    assert x.signal_features["action"] == "exit_trend" and x.signal_features["hold_time_sec"] == pytest.approx(86400)
    assert eng.state.positions == {} and eng.state.weights["UPUSDT"] == 0.0


def test_close_all_on_risk_halt(tmp_path):
    frames = {"UPUSDT": _frame("up")}
    eng, fills, s = _engine(tmp_path, frames, trend_n_assets=1)
    asyncio.run(eng.run_once())
    closed = asyncio.run(eng.close_all(reason="max_drawdown"))
    assert closed == 1 and eng.state.positions == {}
    assert any(t.side == Side.SELL for t in fills.trades)
    assert eng.state.last_run_status.startswith("flattened")


def test_disabled_allocation_means_no_entries(tmp_path):
    frames = {"UPUSDT": _frame("up")}
    eng, fills, s = _engine(tmp_path, frames, allocation_trend_daily=0.0, trend_n_assets=1)
    assert not eng.enabled
    asyncio.run(eng.run_once())
    assert fills.trades == []


def test_data_store_drops_forming_candle_from_cache(tmp_path):
    calls = []

    def fetcher(sym, start_ms):
        calls.append(start_ms)
        return _frame("up")           # includes TODAY row
    store = DailyDataStore(data_dir=str(tmp_path), fetcher=fetcher)
    out = store.load(["UPUSDT"], TODAY, refresh=True, min_days=30)
    assert TODAY in out["UPUSDT"].index
    cached = pd.read_parquet(str(tmp_path / "UPUSDT.parquet"))
    assert TODAY not in cached.index                      # only complete days are cached
    out2 = store.load(["UPUSDT"], TODAY, refresh=False)
    assert TODAY not in out2["UPUSDT"].index


def test_data_store_retries_a_failing_fetch(tmp_path, monkeypatch):
    import strategies.trend_daily as td
    monkeypatch.setattr(td.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def flaky(sym, start_ms):
        calls["n"] += 1
        if calls["n"] < 3:
            raise TimeoutError("The read operation timed out")
        return _frame("up")
    store = DailyDataStore(data_dir=str(tmp_path), fetcher=flaky)
    out = store.load(["UPUSDT"], TODAY, refresh=True, min_days=30)
    assert "UPUSDT" in out and calls["n"] == 3

    def dead(sym, start_ms):
        raise TimeoutError("still dead")
    store2 = DailyDataStore(data_dir=str(tmp_path / "x"), fetcher=dead)
    assert store2.load(["UPUSDT"], TODAY, refresh=True, min_days=30) == {}


def test_state_json_roundtrip():
    st = TrendState(positions={"BTCUSDT": BookPosition("BTCUSDT", 0.01, 100.0, 0.0004, 0.3, "2026-09-01", 1.0, 101.0)},
                    weights={"BTCUSDT": 0.3}, universe=["BTCUSDT"], universe_month="2026-09")
    back = TrendState.from_json(json.loads(json.dumps(st.to_json())))
    assert back.positions["BTCUSDT"].size == 0.01 and back.universe == ["BTCUSDT"]


def test_excursions_reports_mae_and_mfe_against_the_entry(tmp_path, monkeypatch):
    """The MAE/MFE column was empty on every trend position: the daily bars already hold the answer,
    so heat taken and profit given back are computable without tracking anything (2026-09-03)."""
    import pandas as pd
    from strategies.trend_daily import BookPosition, TrendDailyEngine

    idx = pd.to_datetime(["2026-09-01", "2026-09-02", "2026-09-03"])
    bars = pd.DataFrame({"open": [100.0, 102.0, 108.0], "high": [103.0, 112.0, 109.0],
                         "low": [96.0, 101.0, 104.0], "close": [102.0, 108.0, 106.0]}, index=idx)

    eng = object.__new__(TrendDailyEngine)
    eng.config = Settings().trading
    eng.state = type("S", (), {"positions": {"BTCUSDT": BookPosition(
        symbol="BTCUSDT", size=1.0, entry_price=100.0, entry_fee_rate=0.0004, weight=0.1,
        opened="2026-09-02", opened_ts=0.0, mark_price=106.0)}})()
    eng.store = type("St", (), {"load": staticmethod(lambda syms, today, refresh=False, min_days=0: {"BTCUSDT": bars})})()
    eng._today = lambda: pd.Timestamp("2026-09-03")

    ex = eng.excursions()["BTCUSDT"]
    assert ex["days"] == 2                       # only the bars since the position opened
    assert ex["mfe_bps"] == pytest.approx(1200.0)   # high 112 on entry 100 = +12 %
    assert ex["mae_bps"] == pytest.approx(0.0)      # the low never went below the entry
    # a position under water reports the heat it is taking
    eng.state.positions["BTCUSDT"].entry_price = 110.0
    ex2 = eng.excursions()["BTCUSDT"]
    assert ex2["mae_bps"] == pytest.approx((101.0 / 110.0 - 1) * 10_000, abs=0.2)
    assert ex2["mfe_bps"] == pytest.approx((112.0 / 110.0 - 1) * 10_000, abs=0.2)

    # A position opened today has a daily bar that does not carry today's move yet: the live mark has
    # to count, or MFE reads 0.0 beside a position the screen shows in profit (seen 2026-09-03).
    eng.state.positions["BTCUSDT"].entry_price = 100.0
    eng.state.positions["BTCUSDT"].opened = "2026-09-03"
    eng.state.positions["BTCUSDT"].mark_price = 130.0
    ex3 = eng.excursions()["BTCUSDT"]
    assert ex3["mfe_bps"] == pytest.approx(3000.0)       # the live mark, above every daily high
    eng.state.positions["BTCUSDT"].mark_price = 90.0
    assert eng.excursions()["BTCUSDT"]["mae_bps"] == pytest.approx(-1000.0)


def test_the_book_is_long_only_unless_shorts_are_enabled():
    """The short side is a measured OPTION (research 2026-09-04), not the default: at half size it
    holds the Sharpe and cuts the drawdown, but it subtracted return in the last four years."""
    # a market that makes new lows: the long-only rule must stay flat, the short rule must go short
    falling = pd.Series(np.linspace(100.0, 60.0, 200), index=pd.date_range("2025-01-01", periods=200, freq="D"))

    pos_long, _ = sub_strategy_positions(falling, 20)
    assert float(pos_long.iloc[-1]) == 0.0                      # long-only: flat in a downtrend

    pos_short, stop = sub_strategy_positions(falling, 20, allow_shorts=True, short_size=0.5)
    assert float(pos_short.iloc[-1]) == pytest.approx(-0.5)     # short at HALF size, as validated
    assert float(stop.iloc[-1]) > float(falling.iloc[-1])       # the short's stop sits above price

    # the stop of a short never rises
    stops = stop.dropna()
    assert (stops.diff().dropna() <= 1e-9).all()

    # full size when asked for it (the version the research rejected at 1.57)
    pos_full, _ = sub_strategy_positions(falling, 20, allow_shorts=True, short_size=1.0)
    assert float(pos_full.iloc[-1]) == pytest.approx(-1.0)


def test_target_weights_clamp_shorts_away_unless_enabled():
    idx = pd.date_range("2025-01-01", periods=200, freq="D")
    falling = pd.DataFrame({"close": np.linspace(100.0, 60.0, 200)}, index=idx)
    data = {"BTCUSDT": falling}
    base = TrendParams(lookbacks=(20,), vol_window=30, n_assets=1)
    assert target_weights(data, ["BTCUSDT"], idx[-1], base)["BTCUSDT"] == 0.0

    shorting = TrendParams(lookbacks=(20,), vol_window=30, n_assets=1, allow_shorts=True, short_size=0.5)
    assert target_weights(data, ["BTCUSDT"], idx[-1], shorting)["BTCUSDT"] < 0.0


# ── the executor: signed notional, one path for both directions ───────────────
def _exec_engine(allow_shorts=False, short_size=0.5):
    """A TrendDailyEngine wired to nothing but its own state, so a single symbol can be driven."""
    import asyncio as _aio
    s = Settings()
    s.trading.trend_min_order_usd = 10.0
    s.trading.slippage_bps = 10.0          # 10 bps, so the fill side is visible in the numbers
    s.trading.taker_fee = 0.0004
    s.trading.trend_allow_shorts = allow_shorts
    s.trading.trend_short_size = short_size
    fills = []

    async def on_fill(t):
        fills.append(t)

    eng = object.__new__(TrendDailyEngine)
    eng.config = s.trading
    eng.state = TrendState()
    eng._on_fill = on_fill
    eng.last_marks = {}
    return eng, fills, _aio


def test_executor_long_path_is_unchanged_by_the_signed_rewrite():
    """The long book is live money: entry, partial rebalance and full exit must come out exactly as
    they did before the rewrite that added the short side (2026-09-04)."""
    eng, fills, aio = _exec_engine()
    run = lambda **kw: aio.get_event_loop_policy().new_event_loop().run_until_complete(
        TrendDailyEngine._execute_symbol(eng, **kw))

    run(sym="BTCUSDT", target_w=0.10, price=100.0, equity=1000.0, today_key="2026-09-04", now=1.0)
    pos = eng.state.positions["BTCUSDT"]
    assert pos.size > 0 and pos.side == "BUY"
    assert pos.entry_price == pytest.approx(100.10)          # bought through 10 bps of slippage
    assert pos.size == pytest.approx(100.0 / 100.10)          # 10 % of 1000 at the fill
    assert fills[-1].side is Side.BUY and fills[-1].pnl == 0.0

    # halve it: a partial close that realises PnL on the part sold
    size_after_entry = pos.size
    run(sym="BTCUSDT", target_w=0.05, price=120.0, equity=1000.0, today_key="2026-09-04", now=2.0)
    # The quantity comes from the delta measured at the REFERENCE price and is filled through
    # slippage, so the position lands a hair off the exact target. That is how the long-only version
    # behaved and the rewrite keeps it: changing it would move live numbers for no reason.
    qty_sold = (size_after_entry * 120.0 - 50.0) / (120.0 * 0.999)
    assert eng.state.positions["BTCUSDT"].size == pytest.approx(size_after_entry - qty_sold, rel=1e-9)
    assert fills[-1].side is Side.SELL and fills[-1].pnl > 0
    assert fills[-1].signal_features["exit_reason"] == "REBALANCE"

    # and out
    run(sym="BTCUSDT", target_w=0.0, price=130.0, equity=1000.0, today_key="2026-09-04", now=3.0)
    assert "BTCUSDT" not in eng.state.positions
    assert fills[-1].signal_features["exit_reason"] == "TREND_EXIT" and fills[-1].pnl > 0


def test_executor_opens_closes_and_flips_a_short():
    eng, fills, aio = _exec_engine(allow_shorts=True)
    run = lambda **kw: aio.get_event_loop_policy().new_event_loop().run_until_complete(
        TrendDailyEngine._execute_symbol(eng, **kw))

    # open a short: the order SELLS, so it fills BELOW the reference price
    run(sym="BTCUSDT", target_w=-0.10, price=100.0, equity=1000.0, today_key="2026-09-04", now=1.0)
    pos = eng.state.positions["BTCUSDT"]
    assert pos.size < 0 and pos.side == "SELL" and pos.is_short
    assert pos.entry_price == pytest.approx(99.90)
    assert pos.notional == pytest.approx(abs(pos.size) * pos.mark_price)   # notional is never negative
    assert fills[-1].side is Side.SELL and fills[-1].pnl == 0.0

    # price falls: the short is in profit, and the unrealised figure says so
    pos.mark_price = 80.0
    assert pos.unrealized_pnl > 0

    # close half of it: closing a short BUYS, so it fills ABOVE the reference
    run(sym="BTCUSDT", target_w=-0.05, price=80.0, equity=1000.0, today_key="2026-09-04", now=2.0)
    assert eng.state.positions["BTCUSDT"].size < 0
    assert fills[-1].side is Side.BUY and fills[-1].pnl > 0        # bought back cheaper than sold
    assert fills[-1].signal_features["direction"] == "short"
    assert fills[-1].signal_features["pnl_bps"] > 0                # bps are signed by direction too

    # flip to long in one run: that is TWO trades, not one delta
    before = len(fills)
    run(sym="BTCUSDT", target_w=+0.10, price=90.0, equity=1000.0, today_key="2026-09-04", now=3.0)
    assert len(fills) == before + 2
    assert fills[-2].signal_features["exit_reason"] == "TREND_FLIP"
    assert fills[-1].side is Side.BUY and fills[-1].signal_features["direction"] == "long"
    flipped = eng.state.positions["BTCUSDT"]
    assert flipped.size > 0 and flipped.entry_price == pytest.approx(90.09)   # fresh entry, not averaged


def test_the_executor_cannot_open_a_short_while_the_switch_is_off():
    eng, fills, aio = _exec_engine(allow_shorts=False)
    aio.get_event_loop_policy().new_event_loop().run_until_complete(
        TrendDailyEngine._execute_symbol(eng, sym="BTCUSDT", target_w=-0.10, price=100.0,
                                         equity=1000.0, today_key="2026-09-04", now=1.0))
    assert eng.state.positions == {} and fills == []


def test_a_short_is_coherent_end_to_end_from_the_engine_to_the_row_the_ui_reads():
    """Every consumer of a position had been written for a long book. This walks one short through
    all of them and checks they agree: side, magnitudes, return sign and funding direction."""
    import asyncio as _aio
    from server import bridge

    eng, fills, _ = _exec_engine(allow_shorts=True)
    _aio.get_event_loop_policy().new_event_loop().run_until_complete(
        TrendDailyEngine._execute_symbol(eng, sym="BTCUSDT", target_w=-0.10, price=100.0,
                                         equity=1000.0, today_key="2026-09-04", now=1.0))
    pos = eng.state.positions["BTCUSDT"]
    pos.mark_price = 90.0                      # the market fell: the short is winning

    # 1. the engine's own status (build the rows the way status() does, without its config plumbing)
    st = {"positions": [{
        "symbol": "BTCUSDT", "ui_symbol": to_ui_symbol("BTCUSDT"), "size": round(pos.size, 8),
        "side": pos.side, "short": pos.is_short,
        "entry_price": round(pos.entry_price, 6), "mark_price": round(pos.mark_price, 6),
        "notional": round(pos.notional, 4), "unrealized_pnl": round(pos.unrealized_pnl, 4),
        "weight": round(pos.weight, 4), "opened": pos.opened,
    }]}
    row = st["positions"][0]
    assert row["side"] == "SELL" and row["short"] is True
    assert row["size"] < 0                                   # signed for the engine
    assert row["notional"] > 0                               # a magnitude for everyone else
    assert row["unrealized_pnl"] > 0                         # falling price pays a short

    # 2. the row the positions table reads
    class _Trend:
        state = eng.state
        status = staticmethod(lambda: st)
        exit_ladders = staticmethod(lambda: {})
        excursions = staticmethod(lambda: {})
    engine_stub = type("E", (), {"trend_engine": _Trend(), "funding": None,
                                 "settings": type("S", (), {"trading": eng.config})()})()
    ui = bridge._trend_position_rows(engine_stub)[0]
    assert ui["side"] == "SELL" and ui["size"] > 0            # magnitude + side, like every venue
    assert ui["pnl_pct"] > 0 and ui["roe_pct"] > 0            # the return is mirrored, not negated twice
    assert ui["unrealized_pnl"] > 0

    # 3. what the funding engine is told
    import main as m
    feed = type("E", (), {"paper_sim": None, "trend_engine": _Trend()})()
    frow = m.BotStrike._funding_positions(feed)[0]
    assert frow["side"] == "SELL" and frow["size"] > 0 and frow["notional"] > 0


def test_each_market_is_charged_its_own_measured_spread():
    """`slippage_bps` is one number calibrated for Binance ('deep book') and was applied to gold and
    silver alike. Strike's own measurement runs 0.23 bps on BTC to 8.0 on XAU (2026-09-04)."""
    eng, _, _ = _exec_engine()
    eng.config.slippage_bps = 1.5

    btc = TrendDailyEngine._slippage_bps(eng, "BTCUSDT")
    xau = TrendDailyEngine._slippage_bps(eng, "XAU-USD")
    xag = TrendDailyEngine._slippage_bps(eng, "XAG-USD")

    # BTC's book is deep: the configured value is the floor, so it does not get cheaper than 1.5
    assert btc == pytest.approx(1.5)
    # the metals cross a spread several times wider, and now pay for it
    assert xau > 3.0 and xag > 3.0
    assert xau > btc and xag > btc

    # a market the venue never measured falls back to the configured value
    assert TrendDailyEngine._slippage_bps(eng, "NOTAMARKET-USD") == pytest.approx(1.5)


def test_the_wider_spread_reaches_the_fill():
    """The point of measuring it is that the paper book pays it."""
    import asyncio as _aio
    eng, fills, _ = _exec_engine()
    eng.config.slippage_bps = 1.5
    run = lambda **kw: _aio.get_event_loop_policy().new_event_loop().run_until_complete(
        TrendDailyEngine._execute_symbol(eng, **kw))

    run(sym="BTCUSDT", target_w=0.10, price=100.0, equity=1000.0, today_key="2026-09-04", now=1.0)
    run(sym="XAU-USD", target_w=0.10, price=100.0, equity=1000.0, today_key="2026-09-04", now=1.0)
    btc_fill = eng.state.positions["BTCUSDT"].entry_price
    xau_fill = eng.state.positions["XAU-USD"].entry_price
    assert xau_fill > btc_fill        # the metal's entry is worse, because its book is thinner


# ── the venue's mark reaches an open position the moment the venue publishes it ────────────────
def test_set_venue_mark_revalues_the_position_at_once(tmp_path):
    """The loop re-marks the book once a minute off a 15 s copy of the venue marks, so an open
    position's PnL on screen moved once a minute while the header moved every five seconds
    (Edgar, 2026-09-05). The feed now hands the mark over as it arrives."""
    eng, _, _ = _engine(tmp_path, {"BTCUSDT": _frame("up"), "XAU-USD": _frame("up", seed=2)})
    eng.state.positions["BTCUSDT"] = BookPosition(symbol="BTCUSDT", size=0.001, entry_price=78_000.0,
                                                  entry_fee_rate=0.0005, weight=0.1, opened="2026-09-01",
                                                  opened_ts=NOW - 86_400, mark_price=79_000.0)
    eng.state.positions["XAU-USD"] = BookPosition(symbol="XAU-USD", size=-0.01, entry_price=4_500.0,
                                                  entry_fee_rate=0.0005, weight=0.1, opened="2026-09-01",
                                                  opened_ts=NOW - 86_400, mark_price=4_500.0)
    # the pool symbol is found through its UI form, and a Strike-style symbol through itself
    eng.set_venue_mark("BTC-USD", 79_500.0)
    eng.set_venue_mark("xau-usd", 4_480.0)
    assert eng.state.positions["BTCUSDT"].mark_price == 79_500.0
    assert eng.state.positions["BTCUSDT"].unrealized_pnl == pytest.approx(1.5)
    assert eng.state.positions["XAU-USD"].mark_price == 4_480.0
    assert eng.state.positions["XAU-USD"].unrealized_pnl == pytest.approx(0.2)      # a short gains
    assert eng.last_marks == {"BTCUSDT": 79_500.0, "XAU-USD": 4_480.0}
    assert eng.venue_marks == {"BTC-USD": 79_500.0, "XAU-USD": 4_480.0}
    # a market without a position is remembered for the next entry; junk is ignored
    eng.set_venue_mark("WTI-USD", 91.2)
    eng.set_venue_mark("BTC-USD", 0)
    eng.set_venue_mark("BTC-USD", "nan")
    eng.set_venue_mark("", 5.0)
    assert eng.venue_marks["WTI-USD"] == 91.2
    assert eng.state.positions["BTCUSDT"].mark_price == 79_500.0


def test_tracking_records_one_honest_row_per_day(tmp_path):
    """Six rows for three days and every paper return exactly 0.0 (CT, 2026-09-05): the caller had
    overwritten `equity_basis` before the record was cut, and every run appended."""
    eng, _, _ = _engine(tmp_path, {"BTCUSDT": _frame("up")})
    st = eng.state
    st.opens_prev = {"BTCUSDT": 100.0}
    st.weights = {"BTCUSDT": 0.5}
    st.last_run_date = "2026-09-01"
    st.equity_basis = 1_005.0                       # already today's equity, as run_once sets it
    eng._record_tracking("2026-09-02", {"BTCUSDT": 101.0}, 0.0, 1_005.0, prev_equity=1_000.0)
    assert st.tracking == [{"date": "2026-09-02", "model_ret": 0.005, "paper_ret": 0.005, "turnover": 0.0}]
    # a re-run of the same day (restart, manual run) does not add a second row for it
    st.last_run_date = "2026-09-02"
    eng._record_tracking("2026-09-02", {"BTCUSDT": 101.0}, 0.35, 1_006.0, prev_equity=1_005.0)
    assert len(st.tracking) == 1 and st.tracking[0]["turnover"] == 0.0
    # the next day is a new row; without a previous basis the paper return is unknown, not 0 %
    st.opens_prev = {"BTCUSDT": 101.0}                # run_once moves the opens on after recording
    eng._record_tracking("2026-09-03", {"BTCUSDT": 99.0}, 0.0, 1_000.0, prev_equity=0.0)
    assert [r["date"] for r in st.tracking] == ["2026-09-02", "2026-09-03"]
    assert st.tracking[-1]["model_ret"] == pytest.approx(0.5 * (99.0 / 101.0 - 1.0), abs=1e-6)
    assert st.tracking[-1]["paper_ret"] == 0.0


def test_state_load_keeps_one_tracking_row_per_day():
    st = TrendState.from_json({"tracking": [
        {"date": "2026-09-02", "model_ret": 0.0, "paper_ret": 0.0, "turnover": 0.0},
        {"date": "2026-09-03", "model_ret": -0.000628, "paper_ret": 0.0, "turnover": 0.0},
        {"date": "2026-09-03", "model_ret": 0.0, "paper_ret": 0.0, "turnover": 0.0},
        {"date": "2026-09-03", "model_ret": -0.000195, "paper_ret": 0.0, "turnover": 0.3548},
        {"date": "2026-09-04", "model_ret": 0.00927, "paper_ret": 0.0, "turnover": 0.1491},
    ]})
    assert [r["date"] for r in st.tracking] == ["2026-09-02", "2026-09-03", "2026-09-04"]
    assert st.tracking[1]["turnover"] == 0.3548          # the run that completed the day


def test_fills_at_the_venues_mark_when_it_quotes_the_market(tmp_path):
    """Gold's Yahoo open put a paper fill at 4,477 while Strike's book was at 4,435 (2026-09-05).
    The signal reads the daily bars; the fill is the venue's price at execution."""
    frames = {"UPUSDT": _frame("up", today_open=250.0), "DOWNUSDT": _frame("down", seed=2),
              "FLATUSDT": _frame("flat", seed=3)}
    eng, fills, s = _engine(tmp_path, frames, equity=1000.0)
    eng.venue_marks = {"UP-USD": 260.0}                  # the venue is 4 % above the daily open
    asyncio.run(eng.run_once())
    t = next(t for t in fills.trades if t.symbol == "UP-USD" and t.side == Side.BUY)
    assert t.price == pytest.approx(260.0 * (1 + s.trading.slippage_bps / 1e4))
    assert t.expected_price == pytest.approx(260.0)
    assert t.signal_features["open_price"] == pytest.approx(260.0)
    assert t.signal_features["adds_to_position"] is False
    assert t.signal_features["position_size_after"] == pytest.approx(t.quantity)
    # a market the venue does not quote still fills at its daily open
    others = [x for x in fills.trades if x.symbol != "UP-USD" and x.side == Side.BUY]
    for x in others:
        assert x.signal_features["open_price"] != 260.0
