"""BotStrike wiring for v2.14: compounding, microstructure switch, edge monitor kill,
trend book survives shutdown. Builds a real BotStrike (no network at construction)."""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from config.settings import Settings
from core.types import MarketRegime, StrategyType
from main import BotStrike
from test_telegram_sync import _make_bot, _process_and_settle


class Repo:
    def __init__(self, trades):
        self._t = trades

    def get_trades(self, source="paper", **kw):
        return list(self._t)


def _close(strategy, pnl, ts, fee=0.12, notional=150.0):
    return SimpleNamespace(strategy=strategy, pnl=pnl, fee=fee, quantity=notional / 100.0,
                           entry_price=100.0, price=100.0, timestamp=ts, trade_type="EXIT", duration_sec=600)


class RecordingNotifier:
    def __init__(self):
        self.risk_events = []

    async def notify_risk_event(self, event, details=None):
        self.risk_events.append((event, details))

    async def notify_error(self, *a, **k):
        pass


# ── microstructure switch ────────────────────────────────────────────

def test_process_symbol_skips_microstructure_when_disabled():
    bot, symbol, sym_cfg = _make_bot()
    bot.risk_manager.validate_signal = lambda sig, *a, **k: sig
    bot.microstructure.get_snapshot = MagicMock(return_value=None)
    bot.settings.trading.microstructure_enabled = False
    asyncio.run(_process_and_settle(bot, symbol, sym_cfg))
    bot.microstructure.get_snapshot.assert_not_called()
    bot.settings.trading.microstructure_enabled = True          # live switch, no restart
    asyncio.run(_process_and_settle(bot, symbol, sym_cfg))
    bot.microstructure.get_snapshot.assert_called()


# ── compounding / restored risk state ────────────────────────────────

def test_restore_history_compounds_equity_and_keeps_peak():
    s = Settings()
    bot = BotStrike(settings=s, paper=True)
    now = time.time()
    bot.trade_repo = Repo([_close("MEAN_REVERSION", -20.0, now - 5 * 86400),
                           _close("TREND_DAILY", +35.0, now - 3600)])
    bot._restore_history()
    assert bot.risk_manager.current_equity == pytest.approx(1015.0)
    assert bot.risk_manager.equity_peak == pytest.approx(1015.0)
    assert bot._sizing_equity() == pytest.approx(1015.0)
    s.trading.compounding_enabled = False
    assert bot._sizing_equity() == pytest.approx(1000.0)


def test_restore_history_without_compounding_keeps_ladder():
    s = Settings()
    s.trading.compounding_enabled = False
    bot = BotStrike(settings=s, paper=True)
    now = time.time()
    bot.trade_repo = Repo([_close("MEAN_REVERSION", -40.0, now - 60)])
    bot._restore_history()
    assert bot.risk_manager.current_equity == pytest.approx(1000.0)
    assert bot.risk_manager.daily_pnl == pytest.approx(-40.0)
    assert bot.risk_manager.equity_peak == pytest.approx(1000.0)


def test_sizing_equity_includes_open_trend_pnl():
    s = Settings()
    bot = BotStrike(settings=s, paper=True)
    bot.trend_engine = SimpleNamespace(unrealized_pnl=lambda: 7.5, positions_as_positions=lambda: [])
    assert bot._sizing_equity() == pytest.approx(1000.0 + 7.5)


# ── edge monitor kill ────────────────────────────────────────────────

def test_edge_monitor_kills_and_notifies_then_recovers():
    s = Settings()
    s.trading.allocation_mean_reversion = 0.5
    bot = BotStrike(settings=s, paper=True)
    bot.notifier = RecordingNotifier()
    now = time.time()
    bot.trade_repo = Repo([_close("MEAN_REVERSION", -0.4, now - i) for i in range(150)])
    asyncio.run(bot._edge_monitor_tick(force=True))
    assert StrategyType.MEAN_REVERSION in bot.portfolio_manager.killed
    assert bot.edge_stats["strategies"]["MEAN_REVERSION"]["verdict"] == "kill"
    assert not bot.portfolio_manager.should_strategy_trade(
        StrategyType.MEAN_REVERSION, MarketRegime.RANGING, symbol="ETH-USD")
    asyncio.run(asyncio.sleep(0))
    assert bot.notifier.risk_events and bot.notifier.risk_events[0][0] == "edge_kill"
    # statistics recover → kill lifted automatically
    bot.trade_repo = Repo([_close("MEAN_REVERSION", 2.0 if i % 3 else -0.5, now - i) for i in range(150)])
    asyncio.run(bot._edge_monitor_tick(force=True))
    assert StrategyType.MEAN_REVERSION not in bot.portfolio_manager.killed


def test_edge_monitor_respects_the_switch():
    s = Settings()
    s.trading.edge_monitor_enabled = False
    bot = BotStrike(settings=s, paper=True)
    bot.notifier = RecordingNotifier()
    now = time.time()
    bot.trade_repo = Repo([_close("MEAN_REVERSION", -0.4, now - i) for i in range(150)])
    asyncio.run(bot._edge_monitor_tick(force=True))
    assert bot.edge_stats["strategies"]["MEAN_REVERSION"]["verdict"] == "kill"   # still measured
    assert bot.portfolio_manager.killed == {}                                     # but not enforced


# ── trend book vs flatten ────────────────────────────────────────────

def test_shutdown_flatten_never_touches_the_trend_book():
    s = Settings()
    bot = BotStrike(settings=s, paper=True)
    calls = []

    async def close_all(reason):
        calls.append(reason)
        return 1
    bot.trend_engine = SimpleNamespace(close_all=close_all, unrealized_pnl=lambda: 0.0,
                                       positions_as_positions=lambda: [], stop=lambda: None,
                                       save_state=lambda: None)
    asyncio.run(bot._flatten_all(reason="shutdown"))
    assert calls == []
    asyncio.run(bot._flatten_all(reason="max_drawdown"))
    assert calls == ["max_drawdown"]


def test_risk_snapshot_shape():
    s = Settings()
    bot = BotStrike(settings=s, paper=True)
    snap = bot.risk_snapshot()
    for k in ("equity", "peak_equity", "drawdown_pct", "daily_pnl", "daily_limit", "weekly_pnl",
              "weekly_limit", "killed_strategies", "compounding_enabled", "equity_basis"):
        assert k in snap
    assert snap["daily_limit"] == pytest.approx(1000.0 * 0.02)
