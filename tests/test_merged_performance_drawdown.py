"""The all-time max drawdown on the Risk page can never sit below the live one.

The realised chain (closed trades + funding) had never dipped, so /api/performance reported
max_drawdown 0.0 while the mark-to-market equity was 0.37 % under its peak (2026-09-05).
"""
from types import SimpleNamespace

from server import bridge


def _engine(peak: float):
    return SimpleNamespace(
        metrics=SimpleNamespace(get_metrics=lambda: {"total_pnl": 0.0, "total_trades": 0}),
        settings=SimpleNamespace(trading=SimpleNamespace(initial_capital=1000.0)),
        risk_manager=SimpleNamespace(equity_peak=peak),
    )


def _cum(max_dd: float):
    return {"initial_capital": 1000.0, "pnl": 9.6, "peak_equity": 1009.6, "max_drawdown": max_dd,
            "sharpe_valid": False, "profit_factor": 1.0, "total_trades": 3, "win_rate": 1.0}


def test_all_time_drawdown_is_floored_by_the_live_one(monkeypatch):
    monkeypatch.setattr(bridge.state, "engine", _engine(peak=1011.09))
    monkeypatch.setattr(bridge, "_cumulative_performance", lambda: _cum(0.0))
    monkeypatch.setattr(bridge, "_paper_unrealized_pnl", lambda: -2.24)      # equity 1007.36
    out = bridge._merged_performance()
    assert out["peak_equity"] == 1011.09
    assert out["current_drawdown"] == round((1011.09 - 1007.36) / 1011.09, 6)
    assert out["max_drawdown"] == out["current_drawdown"]                    # 0.0037, not 0.0


def test_a_worse_realised_drawdown_still_wins(monkeypatch):
    monkeypatch.setattr(bridge.state, "engine", _engine(peak=1011.09))
    monkeypatch.setattr(bridge, "_cumulative_performance", lambda: _cum(0.05))
    monkeypatch.setattr(bridge, "_paper_unrealized_pnl", lambda: -2.24)
    assert bridge._merged_performance()["max_drawdown"] == 0.05
