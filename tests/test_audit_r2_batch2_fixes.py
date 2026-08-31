"""Shutdown-path P0 fixes from audit R2 batch 2 (2026-08-31).

fix_core-02: the PRODUCTION stop path (server.bridge.stop_engine, what systemd
  actually runs) called cancel_all() directly — removing the exchange SL/TP and
  leaving the position open and unprotected. Round 1 fixed the naked-position bug
  in the CLI only, so production kept the bug the audit believed closed.
fix_core-01: _flatten_all() cancelled the protective orders even when the close
  failed or was partial, producing exactly the naked position it exists to prevent.
"""
import asyncio
from types import SimpleNamespace

import pytest

import server.bridge as bridge
from config.settings import Settings


def _source(mod) -> str:
    from pathlib import Path
    return Path(mod.__file__).read_text(encoding="utf-8")


# ── fix_core-02: production stop must flatten, not just cancel ─────────────

def test_stop_engine_flattens_before_cancelling():
    src = _source(bridge)
    i_stop = src.index("async def stop_engine")
    window = src[i_stop:i_stop + 2500]
    assert "_flatten_all" in window, "the production stop path must close positions"
    assert "close_positions_on_shutdown" in window, "must honour the same flag as the CLI"


def test_bare_cancel_all_is_no_longer_the_default_path():
    """cancel_all may only remain as the explicit opt-out branch."""
    src = _source(bridge)
    i_stop = src.index("async def stop_engine")
    window = src[i_stop:i_stop + 2500]
    i_flat = window.index("_flatten_all")
    i_cancel = window.index("execution_engine.cancel_all")
    assert i_flat < i_cancel, "flatten must come before the cancel fallback"
    assert "elif not engine.dry_run and not engine.paper" in window


# ── fix_core-01: never cancel the stops while a position survives ──────────

class _Engine:
    """Minimal stand-in exercising BotStrike._flatten_all's live branch.

    close_result may be a dict (returned) or an Exception instance (raised).
    """

    def __init__(self, close_result):
        self.paper = False
        self.paper_sim = None
        self.dry_run = False
        self._positions = {}
        self.strategies = []
        self.cancel_all_called = False
        self.notifier = SimpleNamespace(
            notify_error=lambda *a, **k: asyncio.sleep(0))

        engine = self

        class _Exec:
            async def close_all_positions(self):
                if isinstance(close_result, Exception):
                    raise close_result
                return close_result

            async def cancel_all(self):
                engine.cancel_all_called = True

        self.execution_engine = _Exec()

    _notify_strategies_flat = lambda self, symbol, strategy_type: None


def _flatten(engine):
    from main import BotStrike
    asyncio.run(BotStrike._flatten_all(engine, reason="shutdown"))


def test_stops_are_kept_when_a_position_survives():
    eng = _Engine({"closed": [], "remaining": [{"symbol": "BTC-USD"}]})
    _flatten(eng)
    assert eng.cancel_all_called is False, "SL/TP cancelled while a position is open"


def test_stops_are_cancelled_once_everything_is_flat():
    eng = _Engine({"closed": [{"symbol": "BTC-USD"}], "remaining": []})
    _flatten(eng)
    assert eng.cancel_all_called is True


def test_partial_close_keeps_stops():
    eng = _Engine({"closed": [{"symbol": "BTC-USD"}],
                   "remaining": [{"symbol": "ETH-USD"}]})
    _flatten(eng)
    assert eng.cancel_all_called is False


def test_stops_are_kept_when_the_close_RAISES():
    """The hole the first version of this fix left (audit R2 tests_quality-05, P0).

    On an exception `result` stays {} so `remaining` is None — falsy — and the old
    guard sailed straight through to cancel_all(), deleting the SL/TP of a position
    that was almost certainly still open, precisely when the exchange is misbehaving.
    """
    eng = _Engine(RuntimeError("exchange unreachable"))
    _flatten(eng)
    assert eng.cancel_all_called is False, "stops cancelled after a FAILED close"


def test_stops_are_kept_when_the_close_reports_errors():
    eng = _Engine({"closed": [], "remaining": [], "errors": [{"symbol": "BTC-USD"}]})
    _flatten(eng)
    assert eng.cancel_all_called is False


def test_paper_mode_never_touches_the_exchange():
    """The freeze/soak runs in paper: the real close path must stay unreachable."""
    from main import BotStrike
    eng = _Engine({"closed": [], "remaining": []})
    eng.paper = True
    eng.paper_sim = SimpleNamespace(close_all_positions=lambda reason: [])
    asyncio.run(BotStrike._flatten_all(eng, reason="shutdown"))
    assert eng.cancel_all_called is False
