"""Fixes for the P0 findings of audit R2 batch 1 (2026-08-31).

  strategies-01       every strategy frozen at 0.00 — the bot must take no entries
  risk_sizing-01      risk-of-ruin pause was global, silent and permanent (deadlock)
  backtest_parity-01  exit_fibonacci ignored by the backtesters (live-only fix in R1)
  backtest_parity-02  501-bar window vs the live 2000 → 42.9% signal overlap
"""
import time

import pytest

from config.settings import Settings
from core.quant_models import RiskOfRuin
from core.types import Signal, Side, StrategyType, MarketRegime
from execution.order_engine import OrderExecutionEngine
from portfolio.portfolio_manager import REGIME_MULTIPLIER, PortfolioManager
from risk.risk_manager import RiskManager, ROR_PROBATION_SEC


# ── strategies-01: the freeze must hold at every gate ──────────────────────
# 2026-09-02: the switch moved from hardcoded tables to Settings.trading.allocation_*
# (editable from the UI). With the code defaults (all intraday allocations 0.0) no
# strategy may open a position in any regime on any symbol.

def test_no_intraday_strategy_has_capital_in_any_regime_by_default():
    settings = Settings()
    pm = PortfolioManager(settings, RiskManager(settings))
    for regime in MarketRegime:
        for strategy in (StrategyType.MEAN_REVERSION, StrategyType.FIBONACCI_RETRACEMENT):
            assert pm.base_weight(strategy, regime) == 0.0, f"{strategy} funded in {regime}"
            for sym in settings.symbol_names:
                assert not pm.should_strategy_trade(strategy, regime, symbol=sym)


def test_mean_reversion_never_funded_outside_ranging():
    # Paper audit 2026-09-02: 98% of the gross loss came from MR trades opened
    # outside RANGING. Even when the owner funds MR, trending/breakout stay at 0.
    for regime in (MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN,
                   MarketRegime.BREAKOUT, MarketRegime.UNKNOWN):
        assert REGIME_MULTIPLIER[regime][StrategyType.MEAN_REVERSION] == 0.0


def test_settings_allocations_are_zero():
    t = Settings().trading
    assert t.allocation_mean_reversion == 0.0
    assert t.allocation_fibonacci_retracement == 0.0


# ── backtest_parity-01: exits must be recognised identically on both sides ──

def _sig(action: str) -> Signal:
    return Signal(strategy=StrategyType.FIBONACCI_RETRACEMENT, symbol="BTC-USD",
                  side=Side.SELL, strength=1.0, entry_price=100.0, stop_loss=101.0,
                  take_profit=99.0, size_usd=50.0, metadata={"action": action})


@pytest.mark.parametrize("action", [
    "exit_fibonacci",        # the one the hardcoded backtest list missed
    "exit_mean_reversion",
    "trailing_stop_hit",
    "mm_unwind",
])
def test_exit_actions_recognised(action):
    assert OrderExecutionEngine.is_exit_signal(_sig(action)) is True


def test_entry_action_is_not_an_exit():
    assert OrderExecutionEngine.is_exit_signal(_sig("mr_entry")) is False


def test_backtester_uses_the_shared_exit_helper():
    """Guards against the hardcoded action tuples coming back."""
    src = (__import__("pathlib").Path(__file__).resolve().parents[1]
           / "backtesting" / "backtester.py").read_text(encoding="utf-8")
    assert 'OrderExecutionEngine.is_exit_signal' in src
    assert '"exit_mean_reversion", "trailing_stop_hit"' not in src


# ── backtest_parity-02: the backtest window must match the live buffer ──────

def test_backtester_window_matches_live_buffer():
    from core.market_data import MAX_BARS
    src = (__import__("pathlib").Path(__file__).resolve().parents[1]
           / "backtesting" / "backtester.py").read_text(encoding="utf-8")
    assert "i - MAX_BARS + 1" in src, "window must be sized from MAX_BARS"
    assert "max(0, i - 500)" not in src, "the 501-bar window is back"
    assert MAX_BARS == 2000


def test_window_yields_enough_hourly_candles_for_adx():
    """501 bars → 8 hourly candles (ADX(14) cannot converge); 2000 → 33."""
    from core.market_data import MAX_BARS
    assert 501 // 60 < 14           # the old window could not converge
    assert MAX_BARS // 60 >= 33     # the new one can


# ── risk_sizing-01: the pause must be per strategy, loud and recoverable ────

def _losing_manager() -> RiskManager:
    mgr = RiskManager(Settings())
    # 30 closed losses on MR only: enough to trip min_trades with a negative edge
    for _ in range(15):
        mgr.record_trade_result(-1.0, strategy=StrategyType.MEAN_REVERSION)
        mgr.record_trade_result(0.4, strategy=StrategyType.MEAN_REVERSION)
    return mgr


def test_ror_is_tracked_per_strategy_not_globally():
    mgr = _losing_manager()
    hurt = mgr._ror_for(StrategyType.MEAN_REVERSION).current
    other = mgr._ror_for(StrategyType.TREND_FOLLOWING).current
    assert hurt.sample_size >= 30
    # The untouched strategy must not inherit the other's verdict
    assert other.sample_size == 0
    assert other.should_pause is False


def test_negative_edge_produces_a_pause_verdict():
    mgr = _losing_manager()
    ror = mgr._ror_for(StrategyType.MEAN_REVERSION).current
    assert ror.edge <= 0
    assert ror.should_pause is True  # pausing a negative-edge strategy is CORRECT


def test_pause_is_not_permanent_probation_resets_the_window():
    mgr = _losing_manager()
    st = StrategyType.MEAN_REVERSION
    assert mgr._ror_probation_expired(st) is False          # first sighting: arm the clock
    mgr._ror_paused_since[st] = time.time() - ROR_PROBATION_SEC - 1
    assert mgr._ror_probation_expired(st) is True           # expired → caller re-measures
    mgr._ror_for(st).reset()
    assert mgr._ror_for(st).current.sample_size == 0        # window cleared → no deadlock


def test_reset_clears_the_verdict_too():
    m = RiskOfRuin(min_trades=5)
    # compute() early-returns without storing a verdict unless there is at least one
    # win AND one loss, so the sample must contain both to exercise reset() properly.
    for _ in range(8):
        m.record_trade(-1.0)
        m.record_trade(0.3)
    m.compute(1000.0)
    assert m.current.sample_size == 16
    assert m.current.should_pause is True   # negative edge
    m.reset()
    assert m.current.sample_size == 0
    assert m.current.should_pause is False
