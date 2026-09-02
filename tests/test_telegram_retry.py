"""Telegram delivery: retry/backoff, failure accounting, live switches, daily digest."""
import asyncio

import pytest

from config.settings import Settings
from core.types import Side, StrategyType, Trade
from notifications import get_notifier
from notifications.telegram import NullNotifier, SEND_BACKOFF_SEC, TelegramNotifier


class _Resp:
    def __init__(self, status, body="ok"):
        self.status = status
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def text(self):
        return self._body

    async def json(self):
        return {"parameters": {"retry_after": 0}}


class _Session:
    """Scripted responses: each item is an int status or an Exception to raise."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0
        self.closed = False

    def post(self, url, json=None):
        self.calls += 1
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return _Resp(item)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    async def _fast(_):
        return None
    monkeypatch.setattr(asyncio, "sleep", _fast)


def test_send_retries_on_timeout_then_succeeds():
    n = TelegramNotifier("t", "c")
    n._session = _Session([asyncio.TimeoutError(), asyncio.TimeoutError(), 200])
    assert asyncio.run(n._send("hi")) is True
    assert n._session.calls == 3
    assert n.send_retries == 1 and n.send_failures == 0


def test_send_gives_up_after_backoff_and_records_the_loss():
    n = TelegramNotifier("t", "c")
    n._session = _Session([asyncio.TimeoutError()] * (len(SEND_BACKOFF_SEC) + 1))
    assert asyncio.run(n._send("hi")) is False
    assert n._session.calls == len(SEND_BACKOFF_SEC) + 1
    assert n.send_failures == 1 and "TimeoutError" in n.last_failure_error
    assert n.delivery_stats()["failures"] == 1


def test_send_never_retries_a_400():
    n = TelegramNotifier("t", "c")
    n._session = _Session([400, 200])
    assert asyncio.run(n._send("<bad>")) is False
    assert n._session.calls == 1 and n.send_failures == 1


def test_send_retries_5xx():
    n = TelegramNotifier("t", "c")
    n._session = _Session([502, 200])
    assert asyncio.run(n._send("x")) is True and n.send_retries == 1


def _trade():
    return Trade(symbol="ETH-USD", side=Side.BUY, price=100.0, quantity=1.0, fee=0.0,
                 strategy=StrategyType.MEAN_REVERSION)


def test_live_switches_gate_notifications():
    s = Settings()
    s.telegram_bot_token, s.telegram_chat_id = "t", "c"
    n = get_notifier(s)
    assert isinstance(n, TelegramNotifier) and not isinstance(n, NullNotifier)
    asyncio.run(n.notify_trade(_trade()))
    assert n._queue.qsize() == 1
    s.trading.telegram_notify_trades = False        # edited from the UI, no restart
    asyncio.run(n.notify_trade(_trade()))
    assert n._queue.qsize() == 1
    s.trading.telegram_portfolio_every_min = 2
    assert n._portfolio_every() == 2
    s.trading.telegram_enabled = False
    assert isinstance(get_notifier(s), NullNotifier)


def test_daily_digest_message_and_switch():
    s = Settings()
    n = TelegramNotifier("t", "c", settings=s)
    asyncio.run(n.notify_daily_digest(
        {"equity": 1012.5, "realized_pnl": 12.5, "unrealized_pnl": 0.4, "total_trades": 30,
         "win_rate": 0.5, "total_fees": 3.1, "max_drawdown": 0.011},
        {"strategies": {"TREND_DAILY": {"n": 12, "t_stat": 1.2, "profit_factor": 1.8, "verdict": "insufficient"}}},
        {"enabled": True, "positions": [{"ui_symbol": "BTC-USD", "weight": 0.3, "unrealized_pnl": 1.0}],
         "exposure": 0.6, "last_run_status": "ok"},
        {"drawdown_pct": 0.011, "max_drawdown_pct": 0.1, "daily_pnl": -1.0, "daily_limit": 20.0,
         "weekly_pnl": 2.0, "weekly_limit": 50.0},
    ))
    msg = n._queue.get_nowait()
    assert "$1,012.50" in msg and "TREND_DAILY" in msg and "BTC-USD" in msg and "insufficient" in msg
    s.trading.telegram_notify_daily_digest = False
    asyncio.run(n.notify_daily_digest({}, {}, {}, {}))
    assert n._queue.empty()
