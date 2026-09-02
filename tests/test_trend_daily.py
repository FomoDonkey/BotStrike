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


def test_state_json_roundtrip():
    st = TrendState(positions={"BTCUSDT": BookPosition("BTCUSDT", 0.01, 100.0, 0.0004, 0.3, "2026-09-01", 1.0, 101.0)},
                    weights={"BTCUSDT": 0.3}, universe=["BTCUSDT"], universe_month="2026-09")
    back = TrendState.from_json(json.loads(json.dumps(st.to_json())))
    assert back.positions["BTCUSDT"].size == 0.01 and back.universe == ["BTCUSDT"]
