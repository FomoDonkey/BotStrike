"""Rebalance trims are not closed trades for the edge monitor.

The trend book trims a winner after it grew and adds to a loser after it shrank; the trims land in
the DB as EXIT rows (order_id trend_rebalance_*). Counted as trades they biased the per-strategy
t-stat towards the last move and drowned the ~90 real exits a year (2026-09-05).
"""
from types import SimpleNamespace

from analytics.edge import compute_edge_stats, is_rebalance_row
from test_edge_monitor import Repo, _t


def _trim(pnl, ts):
    r = _t("TREND_DAILY", pnl, ts=ts)
    r.order_id = "trend_rebalance_deadbeef"
    return r


def _exit(pnl, ts):
    r = _t("TREND_DAILY", pnl, ts=ts)
    r.order_id = "trend_exit_deadbeef"
    return r


def test_trims_are_recognised_by_their_order_id():
    assert is_rebalance_row(SimpleNamespace(order_id="trend_rebalance_1"))
    assert not is_rebalance_row(SimpleNamespace(order_id="trend_exit_1"))
    assert not is_rebalance_row(SimpleNamespace())                       # legacy rows without an id


def test_trims_do_not_enter_the_strategy_statistics():
    rows = [_trim(+0.9, i) for i in range(150)] + [_exit(-2.0, 200 + i) for i in range(12)]
    out = compute_edge_stats(Repo(rows), window=200, min_trades=100)
    st = out["strategies"]["TREND_DAILY"]
    assert out["rebalance_rows_excluded"] == 150
    assert st["n"] == 12                                                 # the real exits only
    assert st["verdict"] == "insufficient"                               # 12 < 100, not "ok" on 162 trims
