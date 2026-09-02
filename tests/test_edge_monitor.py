"""Edge monitor (analytics/edge.py) + kill switch in the PortfolioManager (2026-09-02)."""
import math
from types import SimpleNamespace

import pytest

from analytics.edge import (VERDICT_INSUFFICIENT, VERDICT_KILL, VERDICT_OK, VERDICT_WARN,
                            compute_edge_stats)
from config.settings import Settings
from core.types import MarketRegime, StrategyType
from portfolio.portfolio_manager import PortfolioManager
from risk.risk_manager import RiskManager


def _t(strategy, pnl, fee=0.12, notional=150.0, ts=0.0, tt="EXIT", hold=600.0):
    qty = notional / 100.0
    return SimpleNamespace(strategy=strategy, pnl=pnl, fee=fee, quantity=qty, entry_price=100.0,
                           price=100.0, timestamp=ts, trade_type=tt, duration_sec=hold)


class Repo:
    def __init__(self, trades):
        self._t = trades

    def get_trades(self, source="paper", **kw):
        return list(self._t)


def test_stats_math_and_insufficient_verdict():
    trades = [_t("MEAN_REVERSION", -0.5, ts=i) for i in range(10)] + [_t("MEAN_REVERSION", 0.3, ts=20)]
    trades.append(_t("MEAN_REVERSION", 0.0, ts=30, tt="ENTRY"))   # entries never count
    out = compute_edge_stats(Repo(trades), window=200, min_trades=100)
    st = out["strategies"]["MEAN_REVERSION"]
    assert st["n"] == 11 and st["wins"] == 1
    assert st["net_pnl"] == pytest.approx(-4.7)
    assert st["fees"] == pytest.approx(11 * 0.12)
    assert st["gross_pnl"] == pytest.approx(-4.7 + 11 * 0.12)
    # gross bps per trade: (pnl+fee)/notional*1e4 → losers (-0.38/150)*1e4 = -25.33, winner +28
    assert st["mean_gross_bps"] == pytest.approx((10 * (-0.38) + 0.42) / 11 / 150 * 1e4, rel=1e-3)
    assert st["t_stat"] < 0
    assert st["verdict"] == VERDICT_INSUFFICIENT and "11 < 100" in st["reason"]
    # fee_share = fees / gross of the winning trades (0.42)
    assert st["fee_share"] == pytest.approx(min(11 * 0.12 / 0.42, 9.99), rel=1e-3)


def test_kill_on_t_stat_and_on_fee_share_and_window():
    losers = [_t("MEAN_REVERSION", -0.4 + 0.05 * (i % 3), ts=i) for i in range(150)]
    out = compute_edge_stats(Repo(losers), window=100, min_trades=100, t_kill=-2.0)
    st = out["strategies"]["MEAN_REVERSION"]
    assert st["n"] == 100                          # only the last `window` trades
    assert st["verdict"] == VERDICT_KILL and "t-stat" in st["reason"]
    # positive but fee-dominated edge: gross +0.05, fee 0.12 → fee share > 50%
    fee_heavy = [_t("FIBONACCI_RETRACEMENT", 0.05, fee=0.12, ts=i) for i in range(120)]
    st2 = compute_edge_stats(Repo(fee_heavy), min_trades=100)["strategies"]["FIBONACCI_RETRACEMENT"]
    assert st2["t_stat"] > 0 and st2["verdict"] == VERDICT_KILL and "fees eat" in st2["reason"]


def test_ok_and_warn_verdicts():
    winners = [_t("TREND_DAILY", 3.0 if i % 3 else -1.0, fee=0.05, ts=i) for i in range(120)]
    st = compute_edge_stats(Repo(winners), min_trades=100)["strategies"]["TREND_DAILY"]
    assert st["verdict"] == VERDICT_OK and st["profit_factor"] > 1
    mild = [_t("MEAN_REVERSION", -0.2 if i % 2 else 0.1, ts=i) for i in range(60)]
    st = compute_edge_stats(Repo(mild), min_trades=100)["strategies"]["MEAN_REVERSION"]
    assert st["verdict"] in (VERDICT_WARN, VERDICT_INSUFFICIENT)
    assert st["n"] == 60


def test_requested_strategies_appear_even_without_trades():
    out = compute_edge_stats(Repo([]), strategies=["TREND_DAILY"])
    assert out["strategies"]["TREND_DAILY"]["verdict"] == VERDICT_INSUFFICIENT
    assert out["strategies"]["TREND_DAILY"]["n"] == 0


def test_portfolio_kill_blocks_entries_and_is_reversible():
    s = Settings()
    s.trading.allocation_mean_reversion = 0.5
    s.get_symbol_config("ETH-USD").strategies = "MEAN_REVERSION"
    pm = PortfolioManager(s, RiskManager(s))
    mr = StrategyType.MEAN_REVERSION
    assert pm.should_strategy_trade(mr, MarketRegime.RANGING, symbol="ETH-USD")
    assert pm.kill_strategy(mr, "t-stat -3.1") is True
    assert pm.kill_strategy(mr, "again") is False            # idempotent
    assert not pm.should_strategy_trade(mr, MarketRegime.RANGING, symbol="ETH-USD")
    assert pm.unkill_strategy(mr) is True
    assert pm.should_strategy_trade(mr, MarketRegime.RANGING, symbol="ETH-USD")


def test_allocation_is_the_switch_and_regime_multiplier_applies():
    s = Settings()
    pm = PortfolioManager(s, RiskManager(s))
    mr = StrategyType.MEAN_REVERSION
    assert pm.base_weight(mr, MarketRegime.RANGING) == 0.0               # default: off
    s.trading.allocation_mean_reversion = 0.6                              # live edit (no restart)
    assert pm.base_weight(mr, MarketRegime.RANGING) == pytest.approx(0.6)
    assert pm.base_weight(mr, MarketRegime.TRENDING_UP) == 0.0
    fib = StrategyType.FIBONACCI_RETRACEMENT
    s.trading.allocation_fibonacci_retracement = 0.4
    assert pm.base_weight(fib, MarketRegime.TRENDING_DOWN) == pytest.approx(0.4)
    assert pm.base_weight(fib, MarketRegime.RANGING) == pytest.approx(0.2)
    # per-symbol eligibility (UI-editable) gates the symbol, not the strategy
    assert not pm.should_strategy_trade(fib, MarketRegime.TRENDING_DOWN, symbol="ETH-USD")
    assert pm.should_strategy_trade(fib, MarketRegime.TRENDING_DOWN, symbol="BTC-USD")
