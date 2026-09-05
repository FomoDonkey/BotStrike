"""The trend book takes the venue's fill (price, quantity, fee) when a live executor is plugged in.

Without `fill_fn` the paper fill stays exactly as before (mark ± measured half-spread + taker).
"""
import asyncio

import pandas as pd
import pytest

from config.settings import Settings
from core.types import Side
from strategies.trend_daily import TrendDailyEngine
from strategies.trend_live_executor import FillResult
from test_trend_daily import NOW, FakeStore, Fills, _frame


def _engine_with_fill(tmp_path, fill_fn, frames=None):
    frames = frames or {"BTC-USD": _frame("up", seed=1), "ETH-USD": _frame("up", seed=2), "SOL-USD": _frame("up", seed=3)}
    s = Settings()
    s.trading.trend_execution_hour_utc = 0
    s.trading.trend_pool = ",".join(frames)
    s.trading.trend_n_assets = 3
    s.trading.trend_liq_enter_usd = 1e6
    s.trading.trend_liq_exit_usd = 5e5
    fills = Fills()
    eng = TrendDailyEngine(s, on_fill=fills, equity_provider=lambda: 1000.0, data_store=FakeStore(frames),
                           state_path=str(tmp_path / "trend.json"), clock=lambda: NOW, fill_fn=fill_fn)
    return eng, fills, s


def test_entries_use_the_venue_price_quantity_and_fee(tmp_path):
    calls = []

    async def venue_fill(symbol, side, qty, ref_price, reduce_only):
        calls.append((symbol, side, qty, ref_price, reduce_only))
        return FillResult(qty=qty, price=ref_price * 1.002, fee=-0.001 * qty, passive_qty=qty)  # maker rebate

    eng, fills, s = _engine_with_fill(tmp_path, venue_fill)
    asyncio.run(eng.run_once(NOW))
    assert calls and all(side == Side.BUY and not ro for _, side, _, _, ro in calls)
    for t in fills.trades:
        sym = t.symbol
        pos = eng.state.positions[sym]
        assert t.price == pytest.approx(pos.entry_price)
        assert t.fee == pytest.approx(-0.001 * t.quantity)             # the venue's fee, not taker x notional
        assert t.signal_features["execution"] == "venue"
        assert pos.entry_fee_rate < 0                                  # a rebate, carried to the close


def test_an_unfilled_entry_opens_nothing(tmp_path):
    async def nothing(symbol, side, qty, ref_price, reduce_only):
        return FillResult(qty=0.0, note="passive timeout: unfilled, market fallback off")

    eng, fills, s = _engine_with_fill(tmp_path, nothing)
    asyncio.run(eng.run_once(NOW))
    assert eng.state.positions == {} and fills.trades == []
    assert all(w == 0.0 for w in eng.state.weights.values())


def test_a_close_is_reduce_only_and_settles_at_the_venue_price(tmp_path):
    seen = []

    async def venue_fill(symbol, side, qty, ref_price, reduce_only):
        seen.append((side, reduce_only))
        return FillResult(qty=qty, price=ref_price * (1.001 if side == Side.BUY else 0.999),
                          fee=0.0005 * qty * ref_price, market_qty=qty)

    eng, fills, s = _engine_with_fill(tmp_path, venue_fill)
    asyncio.run(eng.run_once(NOW))
    assert eng.state.positions
    sym = next(iter(eng.state.positions))
    pos = eng.state.positions[sym]
    # a flat market from now on: force a full exit through the close path
    asyncio.run(eng._close_part(sym, pos, abs(pos.size), pos.entry_price, NOW + 86400, target_w=0.0, reason="exit"))
    assert seen[-1] == (Side.SELL, True)
    last = fills.trades[-1]
    assert last.signal_features["action"] == "exit_trend" and last.signal_features["execution"] == "venue"
    assert last.price == pytest.approx(pos.entry_price * 0.999)
    assert sym not in eng.state.positions


def test_without_a_fill_fn_the_paper_fill_is_unchanged(tmp_path):
    eng, fills, s = _engine_with_fill(tmp_path, None)
    asyncio.run(eng.run_once(NOW))
    assert fills.trades and all(t.signal_features["execution"] == "paper" for t in fills.trades)
    for t in fills.trades:
        assert t.fee == pytest.approx(t.price * t.quantity * s.trading.taker_fee)
