"""Telegram ↔ realidad del bot (fixes 2026-09-02).

Tres bugs que hacian que las notificaciones "no cuadraran con nada":
  1. El snapshot de portfolio usaba estado de SESION (se resetea en cada
     restart del servicio) → cada 5 min llegaba "equity intacto, 0 trades"
     aunque la DB tuviera semanas de historial. Ahora usa la vista all-time
     (trade DB + unrealized), la MISMA fuente que la UI (v2.13.1).
  2. Las señales se notificaban ANTES del risk manager → llegaban señales
     que se bloqueaban y jamas se operaban. Ahora solo las validadas.
  3. HTML sin escapar (audit R2 P1): un error con '<' o '&' hacia que
     Telegram devolviera 400 y el mensaje se perdiera EN SILENCIO.

Mutation-verified: revertir cada fix pone su test en rojo (regla lessons.md).
"""
import asyncio
import types

import pandas as pd
import pytest

from config.settings import Settings
from core.types import MarketRegime, Side, Signal, StrategyType
from main import BotStrike
from notifications.telegram import (
    PORTFOLIO_SUMMARY_EVERY,
    NullNotifier,
    TelegramNotifier,
)


# ── helpers ──────────────────────────────────────────────────────

@pytest.fixture
def notifier():
    # Sin start(): los mensajes quedan en la cola interna y se leen de ahi.
    return TelegramNotifier("dummy-token", "12345")


def _drain(n: TelegramNotifier):
    out = []
    while not n._queue.empty():
        out.append(n._queue.get_nowait())
    return out


ALLTIME = {
    "equity": 989.04, "initial_capital": 1000.0, "realized_pnl": -10.96,
    "unrealized_pnl": 0.0, "total_trades": 18, "win_rate": 0.3889,
    "total_fees": 2.31, "max_drawdown": 0.011,
    "session_pnl": 0.0, "session_trades": 0,
}

SESSION_SUMMARY = {
    "equity": 1000.0,
    "weights": {}, "strategy_pnl": {}, "strategy_trades": {},
    "risk": {"total_pnl": 0.0, "positions": {}, "risk_of_ruin": 0.0,
             "vol_target_scalar": 1.0, "vol_realized": 0.0},
}


def _snapshot_message(n: TelegramNotifier, summary: dict) -> str:
    for _ in range(PORTFOLIO_SUMMARY_EVERY):
        asyncio.run(n.notify_portfolio_snapshot(summary))
    msgs = _drain(n)
    assert len(msgs) == 1
    return msgs[0]


# ── 1. Portfolio snapshot: vista all-time, no la sesion ──────────

def test_portfolio_snapshot_uses_alltime_view(notifier):
    msg = _snapshot_message(notifier, {**SESSION_SUMMARY, "alltime": dict(ALLTIME)})
    assert "$989.04" in msg                      # equity historico (DB), no el de sesion
    assert "$-10.96" in msg                      # PnL historico
    assert "18 ops" in msg
    assert "38.9%" in msg                        # win rate historico
    assert "Sesion actual" in msg                # la sesion va etiquetada aparte
    # El equity de sesion (reseteado a initial_capital) NO encabeza el mensaje
    assert "Equity: <b>$1,000.00</b>" not in msg


def test_portfolio_snapshot_alltime_includes_unrealized(notifier):
    at = {**ALLTIME, "unrealized_pnl": 3.5, "equity": 992.54}
    msg = _snapshot_message(notifier, {**SESSION_SUMMARY, "alltime": at})
    assert "$992.54" in msg
    assert "$+3.50" in msg                       # PnL abierto visible


def test_portfolio_snapshot_legacy_without_alltime(notifier):
    # Sin "alltime" (DB caida / modo live) el formato antiguo sigue
    # funcionando y ADEMAS declara que es solo la sesion actual.
    msg = _snapshot_message(notifier, dict(SESSION_SUMMARY))
    assert "Equity: <b>$1,000.00</b>" in msg
    assert "Solo sesion actual" in msg


def test_alltime_provider_called_lazily(notifier):
    # El provider implica un scan de la DB: solo debe invocarse en la
    # llamada que SI envia (1 de cada PORTFOLIO_SUMMARY_EVERY).
    calls = []

    def provider():
        calls.append(1)
        return dict(ALLTIME)

    for _ in range(PORTFOLIO_SUMMARY_EVERY - 1):
        asyncio.run(notifier.notify_portfolio_snapshot(
            dict(SESSION_SUMMARY), alltime_provider=provider))
    assert calls == []
    asyncio.run(notifier.notify_portfolio_snapshot(
        dict(SESSION_SUMMARY), alltime_provider=provider))
    assert len(calls) == 1
    msg = _drain(notifier)[0]
    assert "$989.04" in msg


def test_portfolio_snapshot_cadence_every_5(notifier):
    for _ in range(PORTFOLIO_SUMMARY_EVERY - 1):
        asyncio.run(notifier.notify_portfolio_snapshot(dict(SESSION_SUMMARY)))
    assert notifier._queue.empty()


# ── 2. Startup con contexto historico ────────────────────────────

def test_startup_shows_alltime_equity(notifier):
    asyncio.run(notifier.notify_startup(
        "paper", ["BTC-USD"],
        config={"initial_capital": 1000.0, "alltime": dict(ALLTIME)}))
    msgs = _drain(notifier)
    assert len(msgs) == 1
    assert "$989.04" in msgs[0]                  # equity actual, no solo el capital
    assert "$1,000" in msgs[0]                   # capital inicial sigue visible
    assert "18 ops historicas" in msgs[0]


def test_startup_without_history_unchanged(notifier):
    asyncio.run(notifier.notify_startup(
        "paper", ["BTC-USD"], config={"initial_capital": 1000.0, "alltime": None}))
    msgs = _drain(notifier)
    assert "Equity actual" not in msgs[0]


# ── 3. Escapado HTML (audit R2 P1) ──────────────────────────────

def test_error_with_html_is_escaped(notifier):
    asyncio.run(notifier.notify_error(
        "strategy", "ValueError: <PaperPosition object at 0x7f> & friends"))
    msgs = _drain(notifier)
    assert len(msgs) == 1
    assert "&lt;PaperPosition" in msgs[0]
    assert "<PaperPosition" not in msgs[0]
    assert "&amp; friends" in msgs[0]


def test_risk_event_details_escaped(notifier):
    asyncio.run(notifier.notify_risk_event(
        "evento <raro>", {"detalle": "a<b & c"}))
    msgs = _drain(notifier)
    assert "&lt;raro&gt;" in msgs[0]
    assert "a&lt;b &amp; c" in msgs[0]
    assert "<raro>" not in msgs[0]


# ── 4. Señales: solo las VALIDADAS llegan a Telegram ────────────

class _RecordingNotifier(NullNotifier):
    def __init__(self):
        self.signals = []

    async def notify_signal(self, signal):
        self.signals.append(signal)


class _StubMarketData:
    def __init__(self, price: float = 100.0):
        self._price = price

    def get_data_age(self, symbol):
        return 0.0

    def get_dataframe(self, symbol):
        return pd.DataFrame({"close": [self._price] * 10})

    def get_snapshot(self, symbol):
        return types.SimpleNamespace(
            price=self._price, orderbook=None, regime=None, mark_price=0.0)

    def get_funding_rate(self, symbol):
        return 0.0


class _StubStrategy:
    strategy_type = StrategyType.MEAN_REVERSION

    def should_activate(self, regime):
        return True

    def generate_signals(self, symbol, df, snapshot, regime, sym_config,
                         allocated, current_pos, **kwargs):
        return [Signal(
            strategy=self.strategy_type, symbol=symbol, side=Side.BUY,
            strength=0.9, entry_price=100.0, stop_loss=99.0,
            take_profit=102.0, size_usd=50.0,
        )]


def _make_bot():
    s = Settings()
    bot = BotStrike(settings=s, paper=True)
    bot.notifier = _RecordingNotifier()
    sym_cfg = s.symbols[0]
    symbol = sym_cfg.symbol
    bot.market_data = _StubMarketData()
    bot.strategies = [_StubStrategy()]
    bot.regime_detector.detect = lambda df, sym, cfg: MarketRegime.RANGING
    bot._last_regime[symbol] = MarketRegime.RANGING  # sin cambio de regimen
    bot.portfolio_manager.should_strategy_trade = lambda *a, **k: True
    bot.portfolio_manager.get_allocation = lambda *a, **k: 100.0
    bot.trading_logger.log_signal = lambda sig: None
    bot.paper_sim.execute_signals = lambda validated, orders, cfg: []
    return bot, symbol, sym_cfg


async def _process_and_settle(bot, symbol, sym_cfg):
    await bot._process_symbol(symbol, sym_cfg)
    # deja ejecutarse las tareas de asyncio.ensure_future(notify_signal)
    for _ in range(3):
        await asyncio.sleep(0)


def test_blocked_signal_not_notified():
    bot, symbol, sym_cfg = _make_bot()
    bot.risk_manager.validate_signal = lambda *a, **k: None  # riesgo rechaza todo
    asyncio.run(_process_and_settle(bot, symbol, sym_cfg))
    assert bot.notifier.signals == []


def test_validated_signal_is_notified_with_adjusted_size():
    # El risk manager devuelve una COPIA ajustada: Telegram debe recibir ESA
    # (el tamaño real que se opera), no el objeto pre-riesgo.
    import copy as _copy
    bot, symbol, sym_cfg = _make_bot()

    def _validate(sig, *a, **k):
        adjusted = _copy.copy(sig)
        adjusted.size_usd = 12.34
        return adjusted

    bot.risk_manager.validate_signal = _validate
    asyncio.run(_process_and_settle(bot, symbol, sym_cfg))
    assert len(bot.notifier.signals) == 1
    assert bot.notifier.signals[0].symbol == symbol
    assert bot.notifier.signals[0].size_usd == 12.34
