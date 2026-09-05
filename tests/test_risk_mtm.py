"""The risk manager measures every limit against mark-to-market equity.

Found 2026-09-05: the backtester that validated the three risk profiles fed the risk manager
`equity + unrealized` on every bar, while the live engine fed it fills only — so a trend book
20 % under water could not trip the 36 % drawdown halt, the circuit breaker or the exposure cap
until it closed. `update_unrealized` closes that gap; a fill still lands on the realised ledger.
"""
import asyncio

import pytest

from config.settings import Settings
from risk.risk_manager import RiskManager


def _rm(initial=1000.0, max_dd=0.36):
    s = Settings()
    s.trading.initial_capital = initial
    s.trading.max_drawdown_pct = max_dd
    return RiskManager(s)


def test_open_pnl_moves_equity_peak_and_drawdown():
    rm = _rm()
    assert rm.current_equity == 1000.0 and rm.realized_equity == 1000.0
    rm.update_unrealized(-50.0)
    assert rm.current_equity == 950.0 and rm.realized_equity == 1000.0
    assert rm.current_drawdown_pct == 0.05
    assert rm.equity_peak == 1000.0
    rm.update_unrealized(30.0)                     # the peak follows mark-to-market
    assert rm.current_equity == 1030.0 and rm.equity_peak == 1030.0 and rm.current_drawdown_pct == 0.0
    rm.update_unrealized(float("nan"))             # junk is ignored, the last mark stands
    assert rm.current_equity == 1030.0


def test_a_fill_lands_on_the_realised_ledger_not_on_the_mark():
    rm = _rm()
    rm.update_unrealized(20.0)                     # a position 20 $ up
    # ...closes: the engine adds its PnL to the ledger AND says what open PnL remains
    rm.update_equity(rm.realized_equity + 20.0, unrealized=0.0)
    assert rm.realized_equity == 1020.0 and rm.current_equity == 1020.0 and rm.equity_peak == 1020.0


def test_backtester_path_is_unchanged():
    """The backtester calls update_equity with mark-to-market equity and never marks separately."""
    rm = _rm()
    for eq in (1000.0, 1050.0, 900.0):
        rm.update_equity(eq)
    assert rm.current_equity == 900.0 and rm.equity_peak == 1050.0
    assert abs(rm.current_drawdown_pct - (150.0 / 1050.0)) < 1e-12


def test_open_losses_arm_the_circuit_breaker_and_the_drawdown_halt():
    rm = _rm(max_dd=0.10)
    rm.update_unrealized(-85.0)                    # 8.5 % > 80 % of the 10 % budget
    assert rm.is_circuit_breaker_active
    rm2 = _rm(max_dd=0.10)
    rm2.update_unrealized(-100.0)
    assert rm2._check_max_drawdown()               # the halt sees the open loss too


def test_limits_scale_with_mark_to_market_equity():
    rm = _rm()
    rm.update_unrealized(-200.0)
    summary = rm.get_risk_summary()
    assert summary["equity"] == 800.0
    assert summary["max_daily_loss"] == round(800.0 * rm.config.max_daily_loss_pct, 2)


def test_safe_variant_takes_the_lock():
    rm = _rm()
    asyncio.run(rm.update_unrealized_safe(-10.0))
    assert rm.current_equity == 990.0


def test_funding_is_a_cash_flow_not_a_losing_trade():
    """Four hourly negative settlements armed the consecutive-loss pause (2026-09-04 17:00Z)."""
    rm = _rm()
    for _ in range(6):
        rm.record_cash_flow(-0.01)
    assert rm.daily_pnl == pytest.approx(-0.06) and rm.weekly_pnl == pytest.approx(-0.06)
    assert rm._consecutive_losses == 0 and not rm._consecutive_loss_pause
    rm.record_trade_result(-1.0)                   # a real loss still counts
    assert rm._consecutive_losses == 1


def test_daily_and_weekly_limits_are_mark_to_market():
    """A day that opens flat and sees the book fall 12 % must trip the 11 % daily limit before
    anything closes — the realised-only measure said 0 all day."""
    rm = _rm()
    rm.update_unrealized(0.0)                      # first mark anchors the day and the week
    rm.record_cash_flow(-1.0)                      # funding paid today
    rm.update_unrealized(-40.0)                    # the open book moved against us
    assert rm.daily_pnl == -1.0                    # realised, as before
    assert rm.daily_pnl_mtm == pytest.approx(-41.0) and rm.weekly_pnl_mtm == pytest.approx(-41.0)
    s = rm.get_risk_summary()
    assert s["daily_pnl"] == pytest.approx(-41.0) and s["daily_pnl_realised"] == -1.0
    rm.check_daily_reset()                         # a new day anchors on today's open PnL
    rm._last_daily_reset_date = "1970-01-01"
    rm.check_daily_reset()
    assert rm.daily_pnl_mtm == 0.0
    rm.update_unrealized(-45.0)
    assert rm.daily_pnl_mtm == -5.0


def test_raise_peak_only_raises():
    rm = _rm()
    rm.raise_peak(1_050.0)
    assert rm.equity_peak == 1_050.0
    rm.raise_peak(900.0)
    rm.raise_peak(float("nan"))
    assert rm.equity_peak == 1_050.0
