"""TREND_DAILY — two faults found on the CT on 2026-09-20 after 18 live days.

1. The tracking record scored yesterday's return with TODAY's weights: `_record_tracking` read
   `st.weights` after `_execute_symbol` had already overwritten them. Replayed on the live data the
   engine's model return was +11.8 % against +7.3 % without the look-ahead, and the inflation was
   >= 0 on every single day. The record must use the weights held BEFORE today's trades.
2. Every full close wore the `trend_exit_` order id, so `/api/trades` and `analytics/edge` read a
   manual close, a universe drop and a risk-halt flatten as trailing-stop exits (the CT's "4 trades,
   profit factor 21" contained zero stop exits). Each cause now has its own prefix and exit_reason.
"""
import asyncio

import pytest

from strategies.trend_daily import BookPosition, _close_kind
from strategies.trend_daily_model import model_daily_return
from test_trend_daily import NOW, _engine, _frame


# ── 1. tracking uses the weights that earned the return ────────────────────────
def test_tracking_scores_the_weights_held_not_the_weights_just_set(tmp_path):
    eng, _, s = _engine(tmp_path, {"BTCUSDT": _frame("up")})
    st = eng.state
    st.opens_prev = {"BTCUSDT": 100.0}
    st.last_run_date = "2026-09-01"
    st.weights = {"BTCUSDT": 1.0}                 # what today's run has just set (after trading)
    held_yesterday = {"BTCUSDT": 0.5}             # what actually rode 100 -> 110
    eng._record_tracking("2026-09-02", {"BTCUSDT": 110.0}, 0.5, 1_050.0, prev_equity=1_000.0,
                         weights_prev=held_yesterday)
    cost_bps = s.trading.taker_fee * 1e4 + s.trading.slippage_bps
    expected = model_daily_return(held_yesterday, {"BTCUSDT": 100.0}, {"BTCUSDT": 110.0}, 0.5, cost_bps)
    assert st.tracking[-1]["model_ret"] == pytest.approx(expected, abs=1e-9)
    assert st.tracking[-1]["model_ret"] < 0.10    # 0.5 x 10 % minus costs - NOT the 1.0 x 10 % the bug gave


def test_run_once_records_yesterdays_weights_after_resizing_today(tmp_path):
    """End to end: day 1 enters; before day 2 the held weight is halved by hand so the model resizes
    it (> threshold). The day-2 record must score the HALVED weight, not the resized one."""
    import pandas as pd
    frames = {"UPUSDT": _frame("up")}
    eng, fills, s = _engine(tmp_path, frames, trend_n_assets=1)
    asyncio.run(eng.run_once())
    st = eng.state
    w_model = st.weights["UPUSDT"]
    assert w_model > 0
    opens_day1 = dict(st.opens_prev)
    # simulate a book that drifted to half the model weight (e.g. a manual trim)
    st.weights["UPUSDT"] = w_model / 2
    st.positions["UPUSDT"].size /= 2
    held = dict(st.weights)
    # day 2: a forming candle 5 % higher (the decision still reads rows <= yesterday, so the
    # model weight is unchanged and the halved book is resized back up)
    o2 = float(frames["UPUSDT"]["close"].iloc[-1]) * 1.05
    frames["UPUSDT"].loc[pd.Timestamp("2026-09-03")] = [o2, o2, o2, o2, 0.0, 0.0]
    eng._clock = lambda: NOW + 86_400
    n_before = len(fills.trades)
    asyncio.run(eng.run_once())
    assert len(fills.trades) > n_before                         # it did resize (buy) today
    assert st.weights["UPUSDT"] == pytest.approx(w_model, rel=1e-6)
    rec = st.tracking[-1]
    cost_bps = s.trading.taker_fee * 1e4 + s.trading.slippage_bps
    expected = model_daily_return(held, opens_day1, st.opens_prev, rec["turnover"], cost_bps)
    assert rec["model_ret"] == pytest.approx(expected, abs=1e-6)
    wrong = model_daily_return(st.weights, opens_day1, st.opens_prev, rec["turnover"], cost_bps)
    assert rec["model_ret"] != pytest.approx(wrong, abs=1e-6)   # the look-ahead number differs


# ── 2. a close says why it happened ────────────────────────────────────────────
def test_close_kind_maps_every_cause_to_its_own_prefix():
    assert _close_kind("exit", True) == ("exit", "TREND_EXIT")
    assert _close_kind("exit", False) == ("rebalance", "REBALANCE")
    assert _close_kind("flip", True) == ("exit", "TREND_FLIP")
    assert _close_kind("manual", True) == ("manual", "MANUAL")
    assert _close_kind("universe", True) == ("universe", "UNIVERSE")
    assert _close_kind("halt", True) == ("halt", "RISK_HALT")


def test_manual_close_is_not_a_trend_exit(tmp_path):
    eng, fills, _ = _engine(tmp_path, {"UPUSDT": _frame("up")}, trend_n_assets=1)
    asyncio.run(eng.run_once())
    res = asyncio.run(eng.close_symbol("UP-USD", reason="manual"))
    assert res["closed"] is True
    t = fills.trades[-1]
    assert t.order_id.startswith("trend_manual_")
    assert t.signal_features["exit_reason"] == "MANUAL"


def test_risk_halt_flatten_is_not_a_trend_exit(tmp_path):
    eng, fills, _ = _engine(tmp_path, {"UPUSDT": _frame("up")}, trend_n_assets=1)
    asyncio.run(eng.run_once())
    asyncio.run(eng.close_all(reason="max_drawdown"))
    t = fills.trades[-1]
    assert t.order_id.startswith("trend_halt_")
    assert t.signal_features["exit_reason"] == "RISK_HALT"


def test_market_dropped_from_the_universe_is_not_a_trend_exit(tmp_path):
    """A position in a market the pick no longer holds is closed with the UNIVERSE reason; a
    universe member whose weight went to zero is still a TREND_EXIT."""
    frames = {"UPUSDT": _frame("up"), "OTHERUSDT": _frame("up", seed=5)}
    frames["OTHERUSDT"]["quote_volume"] = 1e6          # ranks below UPUSDT -> not picked with n=1
    eng, fills, _ = _engine(tmp_path, frames, trend_n_assets=1)
    asyncio.run(eng.run_once())
    assert eng.state.universe == ["UPUSDT"]
    # a leftover position in the market that is NOT in the universe (e.g. picked last month)
    eng.state.positions["OTHERUSDT"] = BookPosition(symbol="OTHERUSDT", size=0.5, entry_price=100.0,
                                                    entry_fee_rate=0.0005, weight=0.3, opened="2026-08-15",
                                                    opened_ts=NOW - 20 * 86_400, mark_price=100.0)
    eng.state.weights["OTHERUSDT"] = 0.3
    eng._clock = lambda: NOW + 86_400
    asyncio.run(eng.run_once())
    closes = [t for t in fills.trades if t.symbol == "OTHER-USD" and t.side.name == "SELL"]
    assert closes, "the dropped market must be closed"
    assert closes[-1].order_id.startswith("trend_universe_")
    assert closes[-1].signal_features["exit_reason"] == "UNIVERSE"
    assert "OTHERUSDT" not in eng.state.positions


def test_bridge_derives_the_new_reasons_from_the_order_id():
    from server.bridge import _trade_row

    class Row:
        trade_type = "EXIT"; duration_sec = 10.0; timestamp = 1_789_000_000.0
        symbol = "ZEC-USD"; side = "SELL"; price = 1.0; quantity = 1.0; entry_price = 1.0; pnl = 0.0
        fee = 0.0; strategy = "TREND_DAILY"; leverage = 1.0; regime = None; id = 1; trade_id = "x"
        signal_strength = 0.0; slippage_bps = 0.0; spread_bps = 0.0; mae_bps = 0.0; mfe_bps = 0.0
        order_type = "MARKET"; equity_after = 0.0; expected_price = None; entry_fee_charged = 0.0
        cash_effect = 0.0; hold_sec = 10.0; pnl_bps = 0.0; roe_pct = 0.0

        def __init__(self, oid):
            self.order_id = oid

    assert _trade_row(Row("trend_manual_abc"))["exit_reason"] == "manual"
    assert _trade_row(Row("trend_universe_abc"))["exit_reason"] == "universe"
    assert _trade_row(Row("trend_halt_abc"))["exit_reason"] == "halt"
    assert _trade_row(Row("trend_exit_abc"))["exit_reason"] == "trend_exit"
    assert _trade_row(Row("trend_rebalance_abc"))["exit_reason"] == "rebalance"


def test_edge_statistics_skip_forced_exits():
    from analytics.edge import is_non_strategy_exit, is_rebalance_row

    class T:
        def __init__(self, oid):
            self.order_id = oid

    assert is_non_strategy_exit(T("trend_manual_1")) and is_non_strategy_exit(T("trend_universe_1"))
    assert is_non_strategy_exit(T("trend_halt_1"))
    assert not is_non_strategy_exit(T("trend_exit_1")) and not is_rebalance_row(T("trend_exit_1"))


def test_alltime_statistics_skip_forced_exits_but_keep_their_cash():
    """A manual close / universe drop / risk halt realises money (it is in the all-time PnL) but is
    not a round trip of the strategy: it must not move win rate, PF or the trade count."""
    from types import SimpleNamespace
    from analytics.alltime import compute_alltime_performance

    def row(ts, ttype, pnl, oid):
        return SimpleNamespace(timestamp=ts, symbol="ZEC-USD", side="SELL" if ttype == "EXIT" else "BUY",
                               trade_type=ttype, pnl=pnl, fee=0.0, price=100.0, quantity=1.0, strategy="TREND_DAILY",
                               duration_sec=3600.0, regime="", order_id=oid, trade_id="t", entry_price=100.0,
                               exit_price=100.0, equity_after=0.0, session_id="s", source="paper", fee_asset="USD",
                               mae_bps=0.0, mfe_bps=0.0, slippage_bps=0.0, order_type="MARKET", signal_strength=0.0,
                               spread_bps=0.0, notional=100.0, equity_before=0.0, atr=0.0, pnl_pct=0.0,
                               micro_vpin=0.0, micro_risk_score=0.0, expected_cost_bps=0.0, fill_probability=0.0,
                               id=0, is_win=pnl > 0)

    rows = [row(1.0, "ENTRY", 0.0, "trend_entry_a"), row(2.0, "EXIT", 21.9, "trend_manual_a"),      # Edgar's close
            row(3.0, "ENTRY", 0.0, "trend_entry_b"), row(4.0, "EXIT", -2.9, "trend_universe_b"),    # dropped market
            row(5.0, "ENTRY", 0.0, "trend_entry_c"), row(6.0, "EXIT", -1.0, "trend_rebalance_c"),   # a trim
            row(7.0, "ENTRY", 0.0, "trend_entry_d"), row(8.0, "EXIT", 5.0, "trend_exit_d")]         # the strategy
    repo = SimpleNamespace(get_trades=lambda **kw: list(rows))
    p = compute_alltime_performance(repo, 1000.0, source="paper")
    assert p["total_trades"] == 1 and p["win_rate"] == 1.0          # only the strategy's own exit
    assert p["forced_exits"] == 2 and p["rebalance_trims"] == 1
    assert p["pnl"] == pytest.approx(21.9 - 2.9 - 1.0 + 5.0)           # every close is still cash
    # with no strategy exit at all the counters are still reported
    q = compute_alltime_performance(SimpleNamespace(get_trades=lambda **kw: rows[:6]), 1000.0, source="paper")
    assert q["total_trades"] == 0 and q["forced_exits"] == 2 and q["rebalance_trims"] == 1


# ── 3. what the operator sees is in venue prices; a stale estimate says so ──────────────────────
def test_exit_ladder_is_re_expressed_at_the_venue_mark():
    from strategies.trend_daily import _ladder_in_venue_prices
    lad = {"price": 100.30, "active": 2, "total": 5, "first_exit": 98.66, "full_exit": 87.19, "worst_case_pct": -0.1307,
           "levels": [{"lookback": 10, "stop": 98.66, "distance_pct": -0.0164}, {"lookback": 60, "stop": 87.19, "distance_pct": -0.1307}]}
    out = _ladder_in_venue_prices(dict(lad), 96.66)          # Strike's WTI mark, basis -3.6 %
    assert out["price_space"] == "venue" and out["price"] == 96.66 and out["price_source"] == 100.30
    assert out["basis"] == pytest.approx(96.66 / 100.30 - 1, abs=1e-5)
    assert out["first_exit"] == pytest.approx(98.66 * 96.66 / 100.30, rel=1e-9)   # now BELOW the mark
    assert out["first_exit"] < 96.66 and out["levels"][0]["stop_source"] == 98.66
    assert out["levels"][0]["distance_pct"] == -0.0164                            # ratios are untouched
    # no venue mark -> the source ladder, and it says so
    assert _ladder_in_venue_prices(dict(lad), None)["price_space"] == "source"


def test_params_changed_since_run_lists_what_moved(tmp_path):
    eng, _, s = _engine(tmp_path, {"UPUSDT": _frame("up")}, trend_n_assets=1)
    assert eng.params_changed_since_run() == []                # nothing recorded yet
    asyncio.run(eng.run_once())
    assert eng.state.params_at_run["target_vol"] == s.trading.trend_target_vol
    assert eng.params_changed_since_run() == []
    s.trading.trend_target_vol = 0.30                           # the operator clicks another risk level
    s.trading.trend_lookbacks = "10,20,30,60,90"
    assert eng.params_changed_since_run() == ["lookbacks", "target_vol"]
    assert eng.status()["params_changed_since_run"] == ["lookbacks", "target_vol"]


# ── 4. the basis guard: a venue dislocated from the signal gets no entry / add, exits still run ────
def test_basis_guard_holds_entries_on_a_basis_jump_but_never_exits(tmp_path):
    from strategies.trend_daily import BASIS_GUARD_MIN_READINGS
    frames = {"UPUSDT": _frame("up")}
    eng, fills, s = _engine(tmp_path, frames, trend_n_assets=1)
    s.trading.trend_basis_guard_pct = 0.02
    ref_close = float(frames["UPUSDT"]["close"].iloc[-2])          # last settled reference close
    # ten runs of history with the venue trading exactly at the reference (basis 0)
    eng.state.basis_log = {"UPUSDT": [[f"2026-08-{d:02d}", 0.0] for d in range(1, 11)]}
    eng.set_venue_mark("UP-USD", ref_close * 0.93)                   # today the venue is 7 % under
    asyncio.run(eng.run_once())
    assert "UPUSDT" not in eng.state.positions                       # the entry was held
    assert eng.state.last_basis_blocked == {"UP-USD": pytest.approx(-0.07, abs=0.002)}
    assert eng.status()["basis_guard"]["held"] == eng.state.last_basis_blocked
    assert eng.state.basis_log["UPUSDT"][-1][0] == "2026-09-02"      # today's reading was logged
    # the venue comes back to the reference the next day -> the entry goes through
    eng.set_venue_mark("UP-USD", ref_close)
    eng._clock = lambda: NOW + 86_400
    asyncio.run(eng.run_once())
    assert "UPUSDT" in eng.state.positions and eng.state.last_basis_blocked == {}
    # a jump the other way holds ADDS but a full exit is executed regardless
    eng.state.basis_log["UPUSDT"] = [[f"2026-08-{d:02d}", 0.0] for d in range(1, 11)]
    eng.set_venue_mark("UP-USD", ref_close * 1.08)
    eng._clock = lambda: NOW + 2 * 86_400
    res = asyncio.run(eng.close_symbol("UP-USD", reason="manual"))    # a close is never a held add
    assert res["closed"] is True and "UPUSDT" not in eng.state.positions
    # with too little history the guard has no reference and holds nothing
    eng2, fills2, s2 = _engine(tmp_path / "b", {"UPUSDT": _frame("up")}, trend_n_assets=1)
    s2.trading.trend_basis_guard_pct = 0.02
    eng2.state.basis_log = {"UPUSDT": [["2026-08-01", 0.0]] * (BASIS_GUARD_MIN_READINGS - 1)}
    eng2.set_venue_mark("UP-USD", ref_close * 0.93)
    asyncio.run(eng2.run_once())
    assert "UPUSDT" in eng2.state.positions and eng2.state.last_basis_blocked == {}
