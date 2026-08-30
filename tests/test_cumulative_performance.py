"""Tests for multi-session cumulative performance analysis (bridge v2.13.1).

Ground truth (CT trade DB audit 2026-08-31): TradeRecord.pnl is NET of fees
(equity_after - equity_before == pnl on every row) and each paper session
restarts equity_after at initial_capital. A multi-session equity curve must
therefore chain pnl (use_equity_after=False) — using equity_after directly
produces a sawtooth that resets to initial_capital on every service restart.
"""
import pytest

from analytics.performance import PerformanceAnalyzer
from trade_database.models import TradeRecord


def _exit(ts: float, pnl: float, equity_before: float, fee: float = 0.1) -> TradeRecord:
    return TradeRecord(
        symbol="BTC-USD",
        side="BUY",
        trade_type="EXIT",
        timestamp=ts,
        pnl=pnl,
        fee=fee,
        equity_before=equity_before,
        equity_after=equity_before + pnl,
        source="paper",
    )


def test_annualization_is_crypto_365():
    # Aligned with logging_metrics/logger.py and the backtester (audit v2.5.0);
    # analytics/performance.py lagged behind at 252 until 2026-08-31.
    assert PerformanceAnalyzer.ANNUALIZATION_FACTOR == 365


def test_equity_curve_chains_pnl_across_sessions():
    # Session 1 ends at 994; session 2's equity_after restarts at the 1000 baseline.
    trades = [
        _exit(1000.0, -6.0, 1000.0),   # session 1: 1000 -> 994
        _exit(2000.0, +2.0, 1000.0),   # session 2 (restarted): DB says 1000 -> 1002
    ]
    rep = PerformanceAnalyzer().analyze(
        trades, initial_equity=1000.0, use_equity_after=False)
    assert rep.equity_curve == [1000.0, 994.0, 996.0]  # continuous, no sawtooth
    assert rep.final_equity == pytest.approx(996.0)
    assert rep.total_pnl == pytest.approx(-4.0)


def test_equity_curve_legacy_uses_equity_after():
    trades = [_exit(1000.0, -6.0, 1000.0), _exit(2000.0, +2.0, 1000.0)]
    rep = PerformanceAnalyzer().analyze(trades, initial_equity=1000.0)  # default
    assert rep.equity_curve == [1000.0, 994.0, 1002.0]  # legacy behavior preserved


def test_max_drawdown_on_chained_curve():
    trades = [
        _exit(1000.0, -6.0, 1000.0),
        _exit(2000.0, -4.0, 994.0),
        _exit(3000.0, +5.0, 990.0),
    ]
    rep = PerformanceAnalyzer().analyze(
        trades, initial_equity=1000.0, use_equity_after=False)
    assert rep.max_drawdown == pytest.approx(0.010, abs=1e-6)  # (1000-990)/1000
