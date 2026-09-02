"""Risk state that survives restarts + compounding (2026-09-02).

Audit: RiskManager reset its equity peak to initial_capital on every start, so the
10% circuit breaker and the daily limit were per session. Now peak / daily / weekly
PnL are rebuilt from the trade DB at startup and a weekly-loss gate exists.
"""
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from config.settings import Settings
from core.types import MarketRegime, Side, Signal, StrategyType
from risk.persistence import compute_historical_risk_state, restore_risk_state
from risk.risk_manager import RiskManager


def _close(pnl, ts, tt="EXIT"):
    return SimpleNamespace(pnl=pnl, timestamp=ts, trade_type=tt, fee=0.1)


class Repo:
    def __init__(self, trades):
        self._t = trades

    def get_trades(self, source="paper", **kw):
        return list(self._t)


def test_history_chains_pnl_peak_daily_and_weekly():
    now = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)   # Wednesday
    t = now.timestamp()
    trades = [
        _close(+50.0, t - 20 * 86400),           # long ago: equity 1050 (peak)
        _close(-30.0, t - 8 * 86400),            # last week: 1020
        _close(-5.0, t - 2 * 86400),             # Monday this week: 1015
        _close(+2.0, t - 3600),                  # today: 1017
        _close(0.0, t - 100, tt="ENTRY"),        # entries never count
    ]
    st = compute_historical_risk_state(Repo(trades), 1000.0, now=t)
    assert st.equity == pytest.approx(1017.0)
    assert st.peak == pytest.approx(1050.0)
    assert st.daily_pnl == pytest.approx(2.0)
    assert st.weekly_pnl == pytest.approx(-3.0)
    assert st.closes == 4


def test_history_is_safe_when_the_db_fails():
    class Broken:
        def get_trades(self, **kw):
            raise RuntimeError("db locked")
    st = compute_historical_risk_state(Broken(), 1000.0)
    assert (st.equity, st.peak, st.daily_pnl, st.weekly_pnl, st.closes) == (1000.0, 1000.0, 0.0, 0.0, 0)


def test_restore_with_and_without_compounding():
    s = Settings()
    now = time.time()
    trades = [_close(-40.0, now - 86400 * 3), _close(+10.0, now - 60)]
    hist = compute_historical_risk_state(Repo(trades), 1000.0, now=now)
    rm = RiskManager(s)
    restore_risk_state(rm, hist, compounding=True)
    assert rm.current_equity == pytest.approx(970.0)          # gains/losses carried over
    assert rm.equity_peak == pytest.approx(1000.0)
    assert rm.current_drawdown_pct == pytest.approx(0.03)      # drawdown is NOT reset by a restart
    rm2 = RiskManager(s)
    restore_risk_state(rm2, hist, compounding=False)
    assert rm2.current_equity == pytest.approx(1000.0)         # fixed capital ...
    assert rm2.equity_peak == pytest.approx(1000.0)
    assert rm2.daily_pnl == pytest.approx(10.0)                # ... but the ladder still restored
    # the first daily/weekly reset of the session must NOT wipe the restored amounts
    rm.check_daily_reset()
    assert rm.daily_pnl == pytest.approx(10.0) and rm.weekly_pnl == pytest.approx(10.0)


def _entry():
    return Signal(strategy=StrategyType.MEAN_REVERSION, symbol="ETH-USD", side=Side.BUY,
                  strength=0.9, entry_price=100.0, stop_loss=99.0, take_profit=103.0, size_usd=50.0)


def test_weekly_loss_gate_blocks_entries_but_not_exits():
    s = Settings()
    s.trading.max_weekly_loss_pct = 0.05
    s.trading.max_daily_loss_pct = 0.02
    rm = RiskManager(s)
    rm.restore_history(equity=1000.0, peak=1000.0, daily_pnl=0.0, weekly_pnl=-60.0)  # -6% this week
    sym = s.get_symbol_config("ETH-USD")
    assert rm.validate_signal(_entry(), sym, MarketRegime.RANGING) is None
    exit_sig = _entry()
    exit_sig.metadata = {"action": "exit_mean_reversion"}
    assert rm.validate_signal(exit_sig, sym, MarketRegime.RANGING) is not None
    # mutation guard: with the weekly loss inside the limit the entry passes
    rm.restore_history(equity=1000.0, peak=1000.0, daily_pnl=0.0, weekly_pnl=-10.0)
    assert rm.validate_signal(_entry(), sym, MarketRegime.RANGING) is not None


def test_weekly_pnl_resets_on_a_new_iso_week(monkeypatch):
    s = Settings()
    rm = RiskManager(s)
    rm.restore_history(equity=1000.0, peak=1000.0, daily_pnl=-1.0, weekly_pnl=-20.0)
    import risk.risk_manager as rmod

    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 7, 0, 5, tzinfo=timezone.utc)   # next Monday
    monkeypatch.setattr("datetime.datetime", _DT)
    rm.check_daily_reset()
    assert rm.weekly_pnl == 0.0 and rm.daily_pnl == 0.0


def test_risk_summary_exposes_the_ladder():
    s = Settings()
    rm = RiskManager(s)
    rm.restore_history(equity=990.0, peak=1000.0, daily_pnl=-3.0, weekly_pnl=-7.0)
    summ = rm.get_risk_summary()
    assert summ["weekly_pnl"] == -7.0
    assert summ["max_weekly_loss"] == pytest.approx(990.0 * 0.05, rel=1e-6)
    assert summ["equity_peak"] == 1000.0 and summ["drawdown_halted"] is False
