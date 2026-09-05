"""
BotStrike Bridge Server — FastAPI + WebSocket bridge.

Wraps the existing BotStrike trading engine and exposes it via:
- WebSocket channels for real-time streaming (market, trading, micro, risk, system)
- REST API for request/response operations (config, bot control, performance)

Usage:
    python -m server.bridge                 # Start bridge (paper mode, Binance)
    python -m server.bridge --live          # Live trading mode
    python -m server.bridge --dev           # Dev mode with auto-reload
    python -m server.bridge --port 9420     # Custom port
"""
from __future__ import annotations

import argparse
import asyncio
import functools
import json
import math
import logging
import os
import re
import sys
import time
from collections import deque
from contextlib import asynccontextmanager
from typing import Dict, Optional, Set

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import Depends, FastAPI, Header, HTTPException, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import secrets
import structlog
from analytics.activity import get_activity_log
from analytics.portfolio import compute_portfolio

logger = structlog.get_logger(__name__)

from config.settings import Settings
from config import overrides as cfg_overrides
from core.types import MarketRegime, StrategyType, Side

# Single source of truth for the bridge version (reported by /api/health and the OpenAPI schema).
BRIDGE_VERSION = "2.16.0"

# Auth token for mutating endpoints (live start / live stop).
# Server deployments set BOTSTRIKE_AUTH_TOKEN in .env so the desktop can be configured with it;
# otherwise a random per-process token is generated (desktop-local mode reads it from /api/bot/status).
_AUTH_TOKEN = os.getenv("BOTSTRIKE_AUTH_TOKEN", "").strip() or secrets.token_hex(16)

_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1", "[::1]")

# Only expose the token over HTTP when bound to loopback (same-machine desktop). On 0.0.0.0 it would
# hand live-trading control to anyone on the LAN/tailnet.
# When False (non-loopback bind) start/stop/backtest REQUIRE the token in every mode.
#
# Derived at MODULE level from BOTSTRIKE_HOST (audit R2 security_supply-05): main() alone was not
# enough because `--dev` runs uvicorn with reload=True, and the reload worker imports
# "server.bridge:app" WITHOUT executing main() — the flag stayed at its True default, which on a
# non-loopback bind leaked the token via /api/bot/status, served /docs and accepted mutations with
# no token at all (verified: 200 on POST /api/bot/stop without credentials). main() also exports
# BOTSTRIKE_HOST so the reload child inherits the right value.
_EXPOSE_TOKEN = os.getenv("BOTSTRIKE_HOST", "127.0.0.1").strip() in _LOOPBACK_HOSTS

# Deploy-level kill switch for live trading (audit R1 03-P0-2, R2 security_supply-03). Defence in
# depth: with it unset, a leaked token cannot start real-money trading. Set BOTSTRIKE_ALLOW_LIVE=1
# only on a host that is actually meant to trade live.
_ALLOW_LIVE = os.getenv("BOTSTRIKE_ALLOW_LIVE", "0").strip() == "1"

VALID_MODES = {"paper", "dry_run", "live"}
VALID_EXCHANGES = ("binance", "hyperliquid", "strike")

# uvicorn's access log writes the full request line — including `?token=...` — to stdout, i.e. to
# journald on the server (audit R2 security_supply-01, reproduced). Redact it instead of turning
# the access log off: the log stays useful, the credential never lands on disk.
_TOKEN_IN_URL_RE = re.compile(r"(token=)[^&\s\"']+", re.IGNORECASE)


class _RedactTokenFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _TOKEN_IN_URL_RE.sub(r"\1***", record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(
                _TOKEN_IN_URL_RE.sub(r"\1***", a) if isinstance(a, str) else a for a in record.args
            )
        return True


def _install_token_redaction() -> None:
    """Attach at import time so the `--dev` reload worker gets it too. dictConfig (which uvicorn
    runs on startup) replaces handlers but keeps filters already attached to the logger object."""
    for name in ("uvicorn.access", "uvicorn.error"):
        lg = logging.getLogger(name)
        if not any(isinstance(f, _RedactTokenFilter) for f in lg.filters):
            lg.addFilter(_RedactTokenFilter())


_install_token_redaction()

# ── Health / watchdog thresholds ─────────────────────────────────
# /api/health answers 503 when the engine is expected but dead, or running with no ticks for this long.
HEALTH_STALE_TICK_SEC = 120.0
# Internal watchdog (only active with BOTSTRIKE_AUTOSTART): checked every 30 s; ticks older than
# 300 s on 3 consecutive checks (or engine not running) → restart engine in-process with backoff;
# after 5 attempts inside a 10-min window → os._exit(3) so systemd (Restart=always) restarts us.
_WATCHDOG_INTERVAL_SEC = 30.0
_WATCHDOG_STALE_SEC = 300.0
_WATCHDOG_STALE_STRIKES = 3
_WATCHDOG_BACKOFF_SEC = (10.0, 30.0, 60.0, 60.0, 60.0)
_WATCHDOG_MAX_ATTEMPTS = 5
_WATCHDOG_WINDOW_SEC = 600.0
_WATCHDOG_EXIT_CODE = 3
# Engine tasks (index order from BotStrike.start) whose death must never be silently ignored.
_CRITICAL_ENGINE_TASKS = ("ws_market", "strategy", "risk_monitor")
from server.serializers import (
    serialize_orderbook, serialize_signal, serialize_position,
    serialize_trade, serialize_market_snapshot, serialize_micro_snapshot,
    serialize_settings,
)
from portfolio.portfolio_manager import strategy_allocation


# ── WebSocket Connection Manager ─────────────────────────────────
class ChannelManager:
    """Manages WebSocket connections per channel with broadcast capability."""

    VALID_CHANNELS = {"market", "trading", "micro", "risk", "system"}

    def __init__(self):
        self._channels: Dict[str, Set[WebSocket]] = {
            ch: set() for ch in self.VALID_CHANNELS
        }
        # The last message per (channel, key), replayed to a client the moment it connects. The
        # candle loop only broadcasts when a bar CHANGED, and on a market where 91 % of the bars
        # are flat the next change can be minutes away — a freshly opened page sat on an empty
        # chart until then, which read as a broken chart (2026-09-04).
        self._retained: Dict[str, Dict[str, str]] = {ch: {} for ch in self.VALID_CHANNELS}

    async def connect(self, channel: str, ws: WebSocket):
        if channel not in self._channels:
            return
        await ws.accept()
        self._channels[channel].add(ws)
        for message in tuple(self._retained[channel].values()):
            try:
                await ws.send_text(message)
            except Exception:
                self._channels[channel].discard(ws)
                return

    def disconnect(self, channel: str, ws: WebSocket):
        if channel in self._channels:
            self._channels[channel].discard(ws)

    async def broadcast(self, channel: str, data: dict, retain: Optional[str] = None):
        """Send `data` to every client on `channel`. With `retain`, the message is also kept under
        that key and replayed to the next client that connects — for snapshots, never for ticks."""
        if channel not in self._channels:
            return
        message = json.dumps(data, default=_json_default)
        if retain is not None:
            self._retained[channel][retain] = message
        clients = self._channels[channel]
        if not clients:
            return
        dead = []
        # Snapshot: send_text awaits, and a client can disconnect (set.discard) meanwhile.
        # Iterating the live set raised "Set changed size during iteration" 348x on the CT
        # (2026-09-02 04:28Z) and dropped that tick's market broadcast for everyone.
        for ws in tuple(clients):
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            clients.discard(ws)

    @property
    def client_count(self) -> int:
        return sum(len(conns) for conns in self._channels.values())


def _json_default(obj):
    """Handle numpy and enum types in JSON serialization."""
    if hasattr(obj, "item"):
        return obj.item()
    if hasattr(obj, "value"):
        return obj.value
    return str(obj)


# ── Bridge State ─────────────────────────────────────────────────
class BridgeState:
    """Holds the bridge server state and engine reference."""

    def __init__(self):
        self.channels = ChannelManager()
        self.engine = None  # BotStrike instance
        self.engine_task: Optional[asyncio.Task] = None
        self.running = False
        self.start_time = time.time()
        self.mode = "paper"
        self.exchange = "binance"

        # Supervision (health + watchdog)
        self.autostart_mode = ""            # "paper"|"dry_run" when BOTSTRIKE_AUTOSTART is set
        self.engine_expected = False        # True: autostart configured or started via API, not stopped by operator
        self.engine_started_at = 0.0
        self.last_tick_ts = 0.0             # last raw Binance trade tick seen by the bridge hook
        self.shutting_down = False
        self.restart_in_progress = False
        self.restart_attempts: deque = deque()   # timestamps of watchdog restart attempts
        self.watchdog_stale_strikes = 0
        self.bg_tasks: Set[asyncio.Task] = set()
        self.backtest_running = False
        # Settings shown/edited while the engine is stopped (carries the saved overrides)
        self.pending_settings: Optional[Settings] = None

        # Throttled broadcast: swap-and-drain pattern (thread-safe for asyncio)
        self._market_queue: Dict[str, dict] = {}
        self._pending_signals: deque = deque(maxlen=50)

        # Recent events for new connections
        self.recent_signals: deque = deque(maxlen=50)
        self.recent_trades: deque = deque(maxlen=100)

        # Performance metrics cache
        self.equity = 300.0
        self.pnl = 0.0
        self.total_trades = 0
        self.win_rate = 0.0


state = BridgeState()


# ── Data Update on Startup ───────────────────────────────────────
async def update_market_data():
    """Download/update 90 days of Binance klines on startup (incremental)."""
    try:
        from data.binance_downloader import BinanceDownloader

        settings = Settings()
        symbols = settings.symbol_names  # ["BTC-USD", ...]

        await state.channels.broadcast("system", {
            "type": "log",
            "timestamp": time.time(),
            "level": "info",
            "message": f"Updating market data for {symbols}...",
        })

        downloader = BinanceDownloader(
            data_dir=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "binance"),
            symbols=symbols,
        )

        for sym in symbols:
            try:
                path = await downloader.download_klines(sym, days=90, interval="1m")
                await state.channels.broadcast("system", {
                    "type": "log",
                    "timestamp": time.time(),
                    "level": "info",
                    "message": f"Klines updated: {sym} -> {path}",
                })
            except Exception as e:
                await state.channels.broadcast("system", {
                    "type": "log",
                    "timestamp": time.time(),
                    "level": "warn",
                    "message": f"Kline update failed for {sym}: {e}",
                })

        await downloader.close()

        await state.channels.broadcast("system", {
            "type": "log",
            "timestamp": time.time(),
            "level": "info",
            "message": "Market data update complete",
        })

    except Exception as e:
        # Non-critical — engine works without historical data
        logger.warning("market_data_update_skipped", error=str(e))


# ── Engine Integration ───────────────────────────────────────────
def _spawn(coro) -> asyncio.Task:
    """create_task + keep a strong reference (asyncio may GC unreferenced tasks mid-flight)."""
    task = asyncio.create_task(coro)
    state.bg_tasks.add(task)
    task.add_done_callback(state.bg_tasks.discard)
    return task


def _build_settings(exchange: str = "binance") -> Settings:
    """Settings for a given venue (fees/slippage). Binance keeps the plain defaults."""
    settings = Settings()
    settings.trading.exchange_venue = exchange
    if exchange == "hyperliquid":
        settings.trading.maker_fee = 0.00015   # 1.5 bps
        settings.trading.taker_fee = 0.00045   # 4.5 bps
        settings.trading.slippage_bps = 2.0    # DEX has slightly wider spread
    return settings


async def start_engine(mode: str = "paper", settings: Optional[Settings] = None):
    """Start the BotStrike trading engine.

    `settings` lets the caller pass venue-specific config (fees/slippage); when omitted a default
    Settings() is used.

    THE VENUE IS NOT HARD-CODED ANY MORE. This function passed `use_binance=True` unconditionally,
    so the bot ran on the Binance feed no matter what the config said, and BOTSTRIKE_AUTOSTART_EXCHANGE
    only changed a label in the UI — the screen could claim one venue while the engine read another
    (2026-09-04). One source of truth now: the env var when set, else `trading.exchange_venue`.

    This is the LIVE half only. Historical data — the daily bars the trend signal is computed from,
    and everything the backtester reads — stays on Binance and Yahoo whatever this says, because
    Strike's own history is 168 days for BTC and 19 for the S&P against the ten years the strategy
    was validated on. `update_market_data()` above and `strategies/daily_sources.py` never consult
    the venue, and must not start.
    """
    # Update market data in background — don't block engine start
    _spawn(update_market_data())

    from main import BotStrike

    settings = settings if settings is not None else Settings()
    # Paper/dry-run: always use mainnet for real price data (testnet prices differ).
    # Live mode: respect settings.use_testnet from .env (user may want testnet for testing).
    is_paper = mode == "paper"
    is_dry_run = mode == "dry_run"
    if is_paper or is_dry_run:
        settings.use_testnet = False

    venue = (os.getenv("BOTSTRIKE_AUTOSTART_EXCHANGE", "").strip().lower()
             or str(getattr(settings.trading, "exchange_venue", "strike")).strip().lower()
             or "strike")
    settings.trading.exchange_venue = venue          # so every downstream reader agrees
    state.engine = BotStrike(
        settings=settings,
        dry_run=is_dry_run,
        paper=is_paper,
        use_binance=(venue == "binance"),
    )
    state.exchange = venue                           # the label the UI shows is now the same fact
    logger.info("engine_venue_selected", venue=venue, note="history stays on binance/yahoo")
    state.mode = mode
    state.running = True
    state.engine_expected = True
    state.engine_started_at = time.time()
    state.last_tick_ts = 0.0

    # Set leverage on exchange (match CLI behavior — main.py:162-169)
    if not is_dry_run and not is_paper:
        for sym in settings.symbols:
            try:
                await state.engine.client.set_leverage(sym.symbol, sym.leverage)
                logger.info("leverage_set", symbol=sym.symbol, leverage=sym.leverage)
            except Exception as e:
                logger.warning("leverage_set_failed", symbol=sym.symbol, error=str(e))

    _install_hooks(state.engine)
    state.engine_task = asyncio.create_task(_run_engine())


async def _run_engine():
    """Run the engine. A crash is logged with full traceback (journald) and, under autostart,
    handed to the watchdog restart path. A normal return while the engine is still expected
    (e.g. _supervise_tasks gave up) is treated the same way — never silently."""
    failure: Optional[str] = None
    try:
        await state.engine.start()
    except asyncio.CancelledError:
        pass
    except Exception as e:
        failure = f"{type(e).__name__}: {e}"
        logger.exception("engine_crashed", error=str(e), error_type=type(e).__name__,
                         mode=state.mode, exchange=state.exchange)
        try:
            await state.channels.broadcast("system", {
                "type": "engine_error",
                "error": str(e),
                "timestamp": time.time(),
            })
        except Exception:
            pass
    else:
        if state.engine_expected and not state.shutting_down:
            failure = "engine.start() returned while still expected"
            logger.error("engine_exited_unexpectedly", mode=state.mode, exchange=state.exchange,
                         hint="a critical task died or _supervise_tasks gave up; see previous log lines")
    finally:
        state.running = False

    if failure and state.autostart_mode and state.engine_expected and not state.shutting_down:
        _spawn(_restart_engine_after_failure(failure))


async def stop_engine(manual: bool = False):
    """Gracefully stop the engine — mirrors CLI shutdown sequence (main.py:1080-1104).

    manual=True (operator via /api/bot/stop): the engine is no longer *expected*, so health
    stays 200 with engine_running=false and the watchdog does not resurrect it until the next
    start (or process restart, where BOTSTRIKE_AUTOSTART applies again).
    """
    if manual:
        state.engine_expected = False
        if state.autostart_mode:
            logger.warning("engine_stopped_by_operator", autostart=state.autostart_mode,
                           hint="watchdog disabled until next start or service restart")
    engine = state.engine
    if engine:
        engine._running = False

        # Close positions FIRST, then cancel orders — via the engine's own _flatten_all,
        # exactly like the CLI (main.py:893-894). Audit R2 fix_core-02 (P0): this path
        # used to call cancel_all() directly, which removes the exchange SL/TP and
        # leaves the position OPEN AND UNPROTECTED. Round 1 fixed the naked-position
        # bug in the CLI only, while systemd runs the bridge — so production kept the
        # bug the audit believed closed. _flatten_all handles paper (simulator fills
        # through the normal pipeline), dry_run (no-op) and live.
        if engine.settings.trading.close_positions_on_shutdown:
            try:
                await engine._flatten_all(reason="shutdown")
            except Exception as e:
                logger.error("shutdown_flatten_failed", error=str(e))
        elif not engine.dry_run and not engine.paper:
            try:
                await engine.execution_engine.cancel_all()
            except Exception as e:
                logger.warning("shutdown_cancel_all_failed", error=str(e))

    if state.engine_task and not state.engine_task.done():
        state.engine_task.cancel()
        try:
            await asyncio.wait_for(state.engine_task, timeout=10)
        except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
            pass

    # Flush metrics, end DB session, notify — match CLI shutdown
    if engine:
        try:
            engine.trade_db.end_session(
                final_equity=engine.risk_manager.current_equity,
                max_drawdown=engine.risk_manager.current_drawdown_pct,
            )
        except Exception as e:
            logger.warning("shutdown_db_end_failed", error=str(e))

        try:
            metrics = engine.metrics.get_metrics()
            logger.info("final_metrics", **metrics)
            engine.trading_logger._flush_metrics()
        except Exception as e:
            logger.warning("shutdown_metrics_flush_failed", error=str(e))

        try:
            metrics = engine.metrics.get_metrics()
            await engine.notifier.notify_shutdown(metrics)
            await engine.notifier.stop()
        except Exception as e:
            logger.warning("shutdown_notify_failed", error=str(e))

    state.running = False
    state.engine = None
    state.engine_task = None


def _install_hooks(engine):
    """Install event hooks on the BotStrike engine to capture data for broadcast.

    Monkey-patches callbacks WITHOUT modifying engine source code.
    """
    original_setup = engine._setup_ws_callbacks

    def patched_setup():
        original_setup()

        # Add trade tick hook for market channel (runs after original handler)
        async def on_trade_hook(data: dict):
            symbol = data.get("s", "")
            price = float(data.get("p", 0))
            qty = float(data.get("q", 0))
            if not symbol or price <= 0:
                return
            is_buy = not data.get("m", False)
            ts = float(data.get("T", time.time() * 1000)) / 1000.0
            state.last_tick_ts = time.time()  # health/watchdog: raw feed liveness

            # Normalize Binance symbol format to match config (BTCUSDT → BTC-USD)
            normalized = symbol
            if symbol.endswith("USDT"):
                normalized = symbol[:-4] + "-USD"
            elif symbol.endswith("USD") and "-" not in symbol:
                normalized = symbol[:-3] + "-USD"

            # Atomic swap: latest tick per symbol
            state._market_queue[normalized] = {
                "type": "tick",
                "symbol": normalized,
                "price": price,
                "quantity": qty,
                "side": "BUY" if is_buy else "SELL",
                "notional": price * qty,
                "timestamp": ts,
            }

        engine.websocket.on("trade", on_trade_hook)

    engine._setup_ws_callbacks = patched_setup

    # Critical-task watch: main._supervise_tasks only restarts metrics/data_refresh and only
    # stops the engine after the 4th crash of a critical task; a single death (or a plain
    # return) of ws_market/strategy/risk_monitor would otherwise leave a "running" engine that
    # does nothing. Attach done-callbacks that stop the engine so _run_engine/watchdog react.
    original_supervise = engine._supervise_tasks

    async def patched_supervise(tasks):
        _watch_critical_tasks(engine, tasks)
        await original_supervise(tasks)

    engine._supervise_tasks = patched_supervise

    # Intercept _process_symbol for signal + state broadcast
    original_process = engine._process_symbol

    async def patched_process(symbol, sym_config):
        await original_process(symbol, sym_config)
        # Fire-and-forget: broadcast MUST NOT block the trading loop
        # OFM evaluates every 3s — even 200ms broadcast latency degrades alpha
        asyncio.ensure_future(_broadcast_symbol_state(engine, symbol))

    engine._process_symbol = patched_process

    # Intercept paper fills for trade broadcast + live logs
    # CRITICAL: original is async def — patch MUST also be async
    if hasattr(engine, '_process_paper_fill'):
        original_paper_fill = engine._process_paper_fill

        async def patched_paper_fill(trade):
            await original_paper_fill(trade)
            try:
                serialized = serialize_trade(trade)
                state.recent_trades.append(serialized)
                _activity_fill(trade, serialized)
                state._pending_signals.append({
                    "type": "trade",
                    "data": serialized,
                })
                # Send to live logs — show position side, not closing side
                raw_side = trade.side.value if hasattr(trade.side, 'value') else str(trade.side)
                strat = trade.strategy.value if trade.strategy and hasattr(trade.strategy, 'value') else ""
                is_exit = trade.pnl != 0 or trade.fee > 0
                if is_exit:
                    pos_side = "SHORT" if raw_side == "BUY" else "LONG"
                    pnl_str = f" PnL: ${trade.pnl:+.4f}"
                    msg = f"Close {pos_side} {trade.symbol} @ ${trade.price:,.2f} [{strat}]{pnl_str}"
                    level = "info" if trade.pnl >= 0 else "warn"
                else:
                    pos_side = "LONG" if raw_side == "BUY" else "SHORT"
                    msg = f"Open {pos_side} {trade.symbol} @ ${trade.price:,.2f} [{strat}]"
                    level = "info"
                state._pending_signals.append({
                    "type": "log_entry",
                    "channel": "system",
                    "data": {
                        "type": "log",
                        "timestamp": time.time(),
                        "level": level,
                        "message": msg,
                    },
                })
            except Exception as e:
                logger.error("trade_broadcast_error", error=str(e))

        engine._process_paper_fill = patched_paper_fill

    # Intercept signal logging to broadcast to desktop
    original_log_signal = engine.trading_logger.log_signal

    def patched_log_signal(signal):
        original_log_signal(signal)
        state._pending_signals.append({
            "type": "signal",
            "data": serialize_signal(signal),
        })
        # Also send to live logs
        side = signal.side.value if hasattr(signal.side, 'value') else str(signal.side)
        strat = signal.strategy.value if signal.strategy and hasattr(signal.strategy, 'value') else ""
        is_exit = signal.metadata.get("action", "").startswith("exit") or signal.metadata.get("exit_reason")
        if not is_exit:
            state._pending_signals.append({
                "type": "log_entry",
                "channel": "system",
                "data": {
                    "type": "log",
                    "timestamp": time.time(),
                    "level": "info",
                    "message": f"Signal: {side} {signal.symbol} @ ${signal.entry_price:,.2f} str={signal.strength:.2f} [{strat}]",
                },
            })

    engine.trading_logger.log_signal = patched_log_signal

    # Intercept live order fills (on_order_update) for trade broadcast
    if hasattr(engine, 'execution_engine'):
        original_on_order_update = engine.execution_engine.on_order_update

        def patched_on_order_update(data):
            trade = original_on_order_update(data)
            if trade is not None:
                serialized = serialize_trade(trade)
                state.recent_trades.append(serialized)
                _activity_fill(trade, serialized)
                state._pending_signals.append({
                    "type": "trade",
                    "data": serialized,
                })
                # Log to system channel
                raw_side = trade.side.value if hasattr(trade.side, 'value') else str(trade.side)
                strat = trade.strategy.value if trade.strategy and hasattr(trade.strategy, 'value') else ""
                is_exit = trade.pnl != 0
                if is_exit:
                    pos_side = "SHORT" if raw_side == "BUY" else "LONG"
                    msg = f"[LIVE] Close {pos_side} {trade.symbol} @ ${trade.price:,.2f} [{strat}] PnL: ${trade.pnl:+.4f}"
                else:
                    pos_side = "LONG" if raw_side == "BUY" else "SHORT"
                    msg = f"[LIVE] Open {pos_side} {trade.symbol} @ ${trade.price:,.2f} [{strat}]"
                state._pending_signals.append({
                    "type": "log_entry",
                    "channel": "system",
                    "data": {
                        "type": "log",
                        "timestamp": time.time(),
                        "level": "info" if not is_exit or trade.pnl >= 0 else "warn",
                        "message": msg,
                    },
                })
            return trade

        engine.execution_engine.on_order_update = patched_on_order_update


def _watch_critical_tasks(engine, tasks) -> None:
    for idx, name in enumerate(_CRITICAL_ENGINE_TASKS):
        if idx >= len(tasks):
            break
        tasks[idx].add_done_callback(functools.partial(_on_critical_task_done, engine, name))


def _on_critical_task_done(engine, name: str, task: asyncio.Task) -> None:
    """A critical engine task finished. If the engine was still running this is a failure:
    log it loudly and flip engine._running so BotStrike.start() unwinds; _run_engine then
    reports engine_exited_unexpectedly and (under autostart) the watchdog restarts it."""
    if task.cancelled():
        return
    if not getattr(engine, "_running", False) or state.engine is not engine or state.shutting_down:
        return  # orderly shutdown in progress
    exc = task.exception()
    if exc is not None:
        logger.critical("critical_task_died", task=name, error=str(exc), error_type=type(exc).__name__,
                        exc_info=exc, action="stopping engine so the watchdog can restart it")
    else:
        logger.critical("critical_task_exited", task=name,
                        action="stopping engine so the watchdog can restart it")
    engine._running = False


async def _broadcast_symbol_state(engine, symbol: str):
    """Broadcast current state for a symbol after strategy processing."""
    # Market snapshot
    snapshot = engine.market_data.get_snapshot(symbol)
    if snapshot:
        payload = serialize_market_snapshot(snapshot)
        # The snapshot's funding rate is the intraday FEED's 8-hour rate. The book is charged the
        # venue's rate on the venue's clock, and the header falls back to this payload until the REST
        # call lands — so it briefly showed +0.0079 % where the venue said +0.0016 % (audit
        # 2026-09-03). One rate, from the venue, on every path.
        payload["funding_rate"] = _market_funding_rate(engine, symbol, snapshot)
        payload["funding_countdown_sec"] = _funding_countdown_sec(time.time(), _funding_interval(engine))
        await state.channels.broadcast("market", {
            "type": "snapshot",
            "data": payload,
        })

    # Microstructure
    micro = engine.microstructure.get_snapshot(symbol)
    if micro:
        serialized = serialize_micro_snapshot(micro)
        if serialized:
            await state.channels.broadcast("micro", {
                "type": "micro_update",
                "data": serialized,
            })

    # Positions (paper mode and live mode)
    if engine.paper_sim:
        positions = [p for p in _paper_position_rows(engine) if p["symbol"] == symbol]
        await state.channels.broadcast("trading", {
            "type": "positions",
            "symbol": symbol,
            "data": positions,
        })
    else:
        # Live mode: broadcast positions from engine._positions (synced by risk monitor)
        live_pos = engine._positions.get(symbol)
        if live_pos:
            await state.channels.broadcast("trading", {
                "type": "positions",
                "symbol": symbol,
                "data": [serialize_position(live_pos)],
            })
        else:
            await state.channels.broadcast("trading", {
                "type": "positions",
                "symbol": symbol,
                "data": [],
            })

    # Risk state (include symbol for per-symbol regime tracking in UI)
    rm = engine.risk_manager
    risk_msg = {
        "type": "risk_update",
        "timestamp": time.time(),
        "symbol": symbol,
        "equity": float(rm.current_equity),
        "drawdown_pct": float(rm.current_drawdown_pct),
        "max_drawdown_pct": float(engine.settings.trading.max_drawdown_pct),
        "circuit_breaker_active": bool(rm.is_circuit_breaker_active),
        "regime": engine._last_regime.get(symbol, MarketRegime.UNKNOWN).value,
    }
    if hasattr(engine.regime_detector, "status"):
        try:
            rs = engine.regime_detector.status(symbol)
            risk_msg["regime_candidate"] = rs.get("candidate", "")
            risk_msg["regime_since"] = rs.get("confirmed_since", 0.0)
        except Exception:
            pass
    if hasattr(engine, "risk_snapshot"):
        try:
            snap = engine.risk_snapshot()
            risk_msg.update({k: snap[k] for k in (
                "peak_equity", "daily_pnl", "daily_limit", "weekly_pnl", "weekly_limit",
                "drawdown_halted", "compounding_enabled", "equity_basis") if k in snap})
            acct = _account_overview(engine)
            risk_msg["account"] = acct
        except Exception as e:
            logger.debug("risk_snapshot_error", error=str(e))
    await state.channels.broadcast("risk", risk_msg)

    # Broadcast pending signals/trades (route log_entry to system channel)
    while state._pending_signals:
        msg = state._pending_signals.popleft()
        if msg.get("type") == "log_entry":
            await state.channels.broadcast("system", msg["data"])
        else:
            await state.channels.broadcast("trading", msg)


# ── Broadcast Loops ──────────────────────────────────────────────
async def market_broadcast_loop():
    """Broadcast market ticks at throttled rate (4/sec)."""
    while True:
        try:
            if state._market_queue:
                queue = state._market_queue.copy()
                state._market_queue.clear()
                for tick in queue.values():
                    await state.channels.broadcast("market", tick)
            # Drain pending signals/trades/logs
            while state._pending_signals:
                msg = state._pending_signals.popleft()
                if msg.get("type") == "log_entry":
                    await state.channels.broadcast("system", msg["data"])
                else:
                    await state.channels.broadcast("trading", msg)
        except Exception as e:
            logger.debug("market_broadcast_error", error=str(e))
        await asyncio.sleep(0.25)  # 4/sec — matches frontend throttle


async def candle_broadcast_loop():
    """Broadcast candles from market data collector every second.

    Sends closed bars + the forming bar (current tick buffer) so the
    chart updates in real-time, not just when bars close.
    """
    _last_candle_hash: Dict[str, str] = {}
    import math

    while True:
        try:
            if state.engine and state.running:
                for sym_config in state.engine.settings.symbols:
                    symbol = sym_config.symbol
                    df = state.engine.market_data.get_dataframe(symbol)
                    if df is None or df.empty:
                        continue

                    # ── Build forming bar from tick buffer ────────────
                    forming = None
                    try:
                        forming = state.engine.market_data.get_forming_bar(symbol)
                    except Exception:
                        pass  # get_forming_bar may not exist in older engine

                    # ── Dedup: skip if nothing changed ───────────────
                    last_close = float(df["close"].iloc[-1]) if len(df) > 0 else 0
                    forming_close = forming["close"] if forming else 0
                    cache_key = f"{len(df)}_{last_close}_{forming_close}"
                    if _last_candle_hash.get(symbol) == cache_key:
                        continue
                    _last_candle_hash[symbol] = cache_key

                    # ── Collect closed bars ───────────────────────────
                    # Send ALL available bars (up to 500) — let the frontend decide window
                    n = min(500, len(df))
                    df_tail = df.tail(n)

                    candles = []
                    has_ts = "timestamp" in df_tail.columns
                    has_vol = "volume" in df_tail.columns

                    if has_ts:
                        timestamps = df_tail["timestamp"].values
                    else:
                        # Fallback: generate synthetic timestamps (60s apart)
                        now = time.time()
                        timestamps = [now - (n - 1 - i) * 60 for i in range(n)]

                    opens = df_tail["open"].values
                    highs = df_tail["high"].values
                    lows = df_tail["low"].values
                    closes = df_tail["close"].values
                    volumes = df_tail["volume"].values if has_vol else [0] * n

                    for i in range(len(timestamps)):
                        ts = float(timestamps[i])
                        if math.isnan(ts) or ts <= 0:
                            continue
                        # Normalize ms → s
                        if ts > 1e12:
                            ts = ts / 1000
                        o = float(opens[i])
                        h = float(highs[i])
                        lo = float(lows[i])
                        c = float(closes[i])
                        v = float(volumes[i])
                        if any(math.isnan(x) for x in [o, h, lo, c]):
                            continue
                        candles.append({
                            "time": int(ts),
                            "open": o, "high": h, "low": lo, "close": c,
                            "volume": v if not math.isnan(v) else 0,
                        })

                    # ── Append forming bar (real-time candle) ────────
                    if forming and candles:
                        fb_ts = forming["timestamp"]
                        if fb_ts > 1e12:
                            fb_ts = fb_ts / 1000
                        # Only append if timestamp is after last closed bar
                        if int(fb_ts) > candles[-1]["time"]:
                            candles.append({
                                "time": int(fb_ts),
                                "open": forming["open"],
                                "high": forming["high"],
                                "low": forming["low"],
                                "close": forming["close"],
                                "volume": forming["volume"],
                            })
                        else:
                            # Same timestamp as last bar — update in-place
                            candles[-1] = {
                                "time": int(fb_ts),
                                "open": forming["open"],
                                "high": forming["high"],
                                "low": forming["low"],
                                "close": forming["close"],
                                "volume": forming["volume"],
                            }

                    if candles:
                        await state.channels.broadcast("market", {
                            "type": "candles",
                            "symbol": symbol,
                            "data": candles,
                        }, retain=f"candles:{symbol}")
        except Exception as e:
            logger.warning("candle_broadcast_error", error=str(e), error_type=type(e).__name__)
        await asyncio.sleep(1)  # 1s broadcast — real-time feel


# ── Cumulative performance (trade DB = source of truth) ──────────
# The engine's MetricsCollector resets on every service restart; the trade DB
# persists. Realized performance therefore ALWAYS comes from the DB and the
# running engine only contributes live/unrealized state. Verified against the
# CT DB (2026-08-31): pnl is NET of fees (equity_after - equity_before == pnl)
# and every session restarts equity_after at initial_capital, so the curve is
# rebuilt by chaining pnl (use_equity_after=False), never from equity_after.
_perf_cache: Dict[str, object] = {"ts": 0.0, "data": None}
_PERF_CACHE_TTL_SEC = 5.0


def _paper_unrealized_pnl() -> float:
    """Mark-to-market PnL of open paper positions, intraday + trend book (0.0 when none)."""
    try:
        if state.engine and hasattr(state.engine, "_unrealized_total"):
            return float(state.engine._unrealized_total())
        if state.engine and state.engine.paper_sim:
            return float(sum(
                getattr(p, "unrealized_pnl", 0.0) or 0.0
                for p in state.engine.paper_sim.get_all_positions().values()
            ))
    except Exception as e:
        logger.debug("paper_unrealized_error", error=str(e))
    return 0.0


def _cumulative_performance() -> Optional[Dict]:
    """All-time paper performance from the trade DB (survives restarts). Cached 5 s.

    The computation itself lives in analytics.alltime so Telegram and the UI
    share ONE builder and can never show different numbers."""
    now = time.time()
    if _perf_cache["data"] is not None and now - _perf_cache["ts"] < _PERF_CACHE_TTL_SEC:
        return _perf_cache["data"]  # type: ignore[return-value]
    engine = state.engine
    if not engine:
        return None
    from analytics.alltime import compute_alltime_performance
    data = compute_alltime_performance(
        engine.trade_repo,
        float(engine.settings.trading.initial_capital),
        source="paper",
    )
    if data is None:
        return None
    _perf_cache["ts"] = now
    _perf_cache["data"] = data
    return data


def _merged_performance() -> Optional[Dict]:
    """Combined UI view: all-time realized (DB) + live unrealized + session extras."""
    engine = state.engine
    if not engine:
        return None
    m = engine.metrics.get_metrics()
    session_pnl = float(m.get("total_pnl", 0))
    session_trades = int(m.get("total_trades", 0))
    cum = _cumulative_performance()
    if cum is None:
        # DB unavailable → legacy session-only numbers (never blank the UI)
        return {
            "initial_capital": float(engine.settings.trading.initial_capital),
            "equity": float(engine.risk_manager.current_equity),
            "pnl": session_pnl, "realized_pnl": session_pnl, "unrealized_pnl": 0.0,
            "session_pnl": session_pnl, "session_trades": session_trades,
            "total_trades": session_trades,
            "win_rate": float(m.get("win_rate", 0)),
            "sharpe_ratio": float(m.get("sharpe_ratio", 0)),
            "sortino_ratio": 0.0,
            "max_drawdown": float(m.get("max_drawdown", 0)),
            "total_fees": float(m.get("total_fees", 0)),
            "avg_win": float(m.get("avg_win", 0)),
            "avg_loss": float(m.get("avg_loss", 0)),
            "profit_factor": float(m.get("profit_factor", 0)),
            "equity_curve_ts": [],
        }
    unrealized = _paper_unrealized_pnl()
    out = dict(cum)
    equity = cum["initial_capital"] + cum["pnl"] + unrealized
    # ONE peak on every screen: the risk manager's is mark-to-market and ≥ the realised chain's,
    # so the Account panel (rm.equity_peak) and Portfolio (this) no longer show two peaks
    # (1,009.64 next to 1,010.30 at the same instant, audit 2026-09-05).
    rm_peak = float(getattr(engine.risk_manager, "equity_peak", 0.0) or 0.0)
    peak = max(float(cum.get("peak_equity", cum["initial_capital"])), rm_peak, equity)
    # A Sharpe of 175 off three trades and a profit factor of 9999.99 are sentinels, not
    # statistics: they travel as null until the sample is one (the UI already hides them).
    if not cum.get("sharpe_valid", False):
        out["sharpe_ratio"] = None
        out["sortino_ratio"] = None
    if float(cum.get("profit_factor") or 0.0) >= 9999:
        out["profit_factor"] = None
    out.update({
        "equity": round(equity, 4),
        "pnl": round(cum["pnl"] + unrealized, 4),
        "realized_pnl": cum["pnl"],
        "unrealized_pnl": round(unrealized, 4),
        "session_pnl": session_pnl,
        "session_trades": session_trades,
        "peak_equity": round(peak, 4),
        "current_drawdown": round((peak - equity) / peak, 6) if peak > 0 else 0.0,
    })
    return out


async def metrics_broadcast_loop():
    """Broadcast performance metrics every 2 seconds.

    Sends the MERGED view (trade DB all-time + live unrealized) so the UI never
    resets to 0 after a service restart. state.equity/pnl feed /api/bot/status."""
    while True:
        try:
            if state.engine and state.running:
                p = _merged_performance()
                if p:
                    await state.channels.broadcast("trading", {
                        "type": "metrics",
                        "timestamp": time.time(),
                        "equity": p["equity"],
                        "pnl": p["pnl"],
                        "total_trades": p["total_trades"],
                        "win_rate": p["win_rate"],
                        "sharpe_ratio": p["sharpe_ratio"] if p.get("sharpe_ratio") is not None else 0.0,
                        "max_drawdown": p["max_drawdown"],
                        "total_fees": p["total_fees"],
                        "unrealized_pnl": p["unrealized_pnl"],
                        "session_pnl": p["session_pnl"],
                        "session_trades": p["session_trades"],
                    })
                    state.equity = p["equity"]
                    state.pnl = p["pnl"]
                await _broadcast_trend_positions()
        except Exception as e:
            logger.debug("metrics_broadcast_error", error=str(e))
        await asyncio.sleep(2)


_trend_symbols_sent: Set[str] = set()
_positions_sent: Dict[str, str] = {}      # symbol -> the rows last broadcast, minus the clock


def _leverage_of(engine):
    def _f(symbol: str) -> int:
        try:
            return int(engine.settings.get_symbol_config(symbol).leverage)
        except Exception:
            return 1
    return _f


def _opened_ts_of(row: dict) -> float:
    """When this position opened: explicit stamp, else derived from how long it has been held."""
    try:
        ts = float(row.get("opened_ts") or 0.0)
        if ts > 0:
            return ts
        hold = float(row.get("hold_sec") or 0.0)
        return (time.time() - hold) if hold > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


def _position_funding(acc, symbol: str, opened_ts: float, lifetime: dict) -> float:
    """Per-operation funding, falling back to the market lifetime total when the open time is unknown."""
    if acc is not None and opened_ts:
        try:
            return round(float(acc.since(symbol, float(opened_ts))), 6)
        except Exception:  # noqa: BLE001 - a display figure must never break the positions feed
            pass
    return round(float(lifetime.get(symbol, 0.0)), 6)


def _trend_position_rows(engine) -> list:
    trend = getattr(engine, "trend_engine", None)
    rows = []
    if trend is None:
        return rows
    try:
        ladders = trend.exit_ladders()
    except Exception:  # noqa: BLE001
        ladders = {}
    acc = getattr(engine, "funding", None)
    funding_by_symbol = dict(getattr(acc, "by_symbol", {}) or {}) if acc is not None else {}
    try:
        excursions = trend.excursions()
    except Exception:  # noqa: BLE001 - a display figure must never break the positions feed
        excursions = {}
    for p in trend.status().get("positions", []):
        mark = p["mark_price"] or p["entry_price"]
        pos_st = trend.state.positions.get(p["symbol"])
        # A short's return is the mirror of the price move, and its size is reported as a magnitude
        # with the direction in `side` — the table, the chips and the PnL colours all read that.
        short = bool(p.get("short")) or float(p.get("size") or 0.0) < 0
        move = (mark / p["entry_price"] - 1.0) if p["entry_price"] else 0.0
        ret = -move if short else move
        rows.append({
            "symbol": p["ui_symbol"], "side": "SELL" if short else "BUY", "size": abs(p["size"]),
            "entry_price": p["entry_price"],
            "mark_price": mark, "notional": p["notional"], "unrealized_pnl": p["unrealized_pnl"],
            "pnl_pct": ret,
            "roe_pct": ret,
            "leverage": 1, "margin": p["notional"], "liquidation_price": 0.0,
            "stop_loss": 0.0, "take_profit": 0.0, "sl_distance_pct": None, "tp_distance_pct": None,
            "strategy": StrategyType.TREND_DAILY.value,
            "opened_ts": getattr(pos_st, "opened_ts", 0.0), "hold_sec": max(0.0, time.time() - getattr(pos_st, "opened_ts", time.time())),
            "mae_bps": (excursions.get(p["symbol"]) or {}).get("mae_bps"),
            "mfe_bps": (excursions.get(p["symbol"]) or {}).get("mfe_bps"),
            "entry_fee_rate": getattr(pos_st, "entry_fee_rate", 0.0),
            "fees_paid": p["entry_price"] * p["size"] * getattr(pos_st, "entry_fee_rate", 0.0),
            "funding_paid": _position_funding(acc, p["ui_symbol"], getattr(pos_st, "opened_ts", 0.0),
                                              funding_by_symbol),
            "order_id": "", "order_type": "MARKET", "trigger": "donchian_ensemble", "weight": p["weight"],
            "exit_ladder": ladders.get(p["symbol"]),
            "timestamp": getattr(pos_st, "opened_ts", 0.0),
        })
    return rows


def _paper_position_rows(engine) -> list:
    """Intraday paper positions (rich) + trend book positions, one list."""
    rows = []
    acc = getattr(engine, "funding", None)
    funding_by_symbol = dict(getattr(acc, "by_symbol", {}) or {}) if acc is not None else {}
    sim = getattr(engine, "paper_sim", None)
    if sim is not None and hasattr(sim, "get_position_details"):
        try:
            rows += sim.get_position_details(leverage_of=_leverage_of(engine))
        except Exception as e:
            logger.debug("position_details_error", error=str(e))
    for r in rows:
        # what THIS position paid since it opened, not what the market has paid since the bot started
        r["funding_paid"] = _position_funding(acc, r.get("symbol"), _opened_ts_of(r), funding_by_symbol)
    rows += _trend_position_rows(engine)
    return rows


async def _broadcast_trend_positions() -> None:
    """Push every open position over the trading channel, and clear the ones that closed.

    Two bugs lived here (found 2026-09-03 by reading the socket in the browser: only 4 of 6 markets
    ever arrived, and the Portfolio page, which reads the socket rather than REST, showed 4):

    * symbols in the intraday feed were SKIPPED on the assumption that the tick loop streams them.
      It does not when no intraday strategy is running, so BTC-USD and SOL-USD were never sent.
      Every symbol is broadcast here now, with the same rows the tick loop would send
      (`_paper_position_rows` = paper positions + the trend book), so the two writers agree.
    * `_trend_symbols_sent` was cleared instead of being set to what had just been sent, so it was
      always empty and the "this symbol closed" broadcast never fired: a closed position stayed on
      screen until a reload.
    """
    engine = state.engine
    trend = getattr(engine, "trend_engine", None) if engine else None
    if trend is None:
        return
    by_symbol: Dict[str, list] = {}
    for row in _paper_position_rows(engine):
        by_symbol.setdefault(row["symbol"], []).append(row)
    for sym, rows in by_symbol.items():
        # Every two seconds, every symbol, whether or not anything moved: 22 identical frames per
        # market per 40 s on the socket (measured 2026-09-05). A frame goes out when the rows
        # changed; the retained copy is what a client that connects in between receives. `hold_sec`
        # is a clock, not a change — the UI derives it from `opened_ts` itself.
        key = json.dumps([{k: v for k, v in r.items() if k != "hold_sec"} for r in rows],
                         sort_keys=True, default=_json_default)
        if _positions_sent.get(sym) == key:
            continue
        _positions_sent[sym] = key
        await state.channels.broadcast("trading", {"type": "positions", "symbol": sym, "data": rows},
                                       retain=f"positions:{sym}")
    for sym in sorted(_trend_symbols_sent - set(by_symbol)):
        _positions_sent.pop(sym, None)
        await state.channels.broadcast("trading", {"type": "positions", "symbol": sym, "data": []},
                                       retain=f"positions:{sym}")
    _trend_symbols_sent.clear()
    _trend_symbols_sent.update(by_symbol)


async def system_broadcast_loop():
    """Broadcast system health every 3 seconds + periodic status logs."""
    _log_counter = 0
    while True:
        try:
            ws_connected = False
            if state.engine:
                ws_connected = bool(getattr(state.engine.websocket, "_connected", False))

            await state.channels.broadcast("system", {
                "type": "health",
                "timestamp": time.time(),
                "engine_running": state.running,
                "mode": state.mode,
                "uptime_sec": time.time() - state.start_time,
                "ws_connected": ws_connected,
                "clients_connected": state.channels.client_count,
                # The venue the ENGINE is on. The terminal used to take this from a browser
                # preference nothing ever synced, so the screen said "Binance feed" while the bot
                # ran on Strike (2026-09-04). A label that can disagree with the engine is worse
                # than no label.
                "exchange": state.exchange,
            })

            # Send periodic engine status to Live Logs (every ~15s = 5 health cycles)
            _log_counter += 1
            if _log_counter >= 5 and state.engine and state.running:
                _log_counter = 0
                m = state.engine.metrics.get_metrics()
                rm = state.engine.risk_manager
                # These counters live in the process and reset on every restart, and the regime is
                # ONE symbol's, picked arbitrarily from a dict. Printed as "Engine: 0 trades | PnL
                # $+0.00 | Regime RANGING" they read as the bot's totals and the book's regime, on a
                # bot holding six positions and up $14 (audit 2026-09-04). Say whose they are.
                pairs = list(state.engine._last_regime.items())
                sym, reg = (pairs[0][0], pairs[0][1].value) if pairs else ("", "UNKNOWN")
                since = f"{m.get('total_trades', 0)} trades | PnL ${m.get('total_pnl', 0):+.2f}"
                await state.channels.broadcast("system", {
                    "type": "log",
                    "timestamp": time.time(),
                    "level": "info",
                    "message": (f"Engine · since restart: {since} | drawdown from peak "
                                f"{rm.current_drawdown_pct:.2%} | {sym or 'regime'} {reg}"),
                })
        except Exception as e:
            logger.debug("system_broadcast_error", error=str(e))
        await asyncio.sleep(3)


# ── Health snapshot + Watchdog ───────────────────────────────────
def _last_tick_age() -> Optional[float]:
    """Seconds since the most recent market tick (bridge raw hook or engine's accepted ticks).
    None when no tick has been seen since the engine started."""
    latest = float(state.last_tick_ts or 0.0)
    md = getattr(state.engine, "market_data", None) if state.engine else None
    times = getattr(md, "_last_data_time", None)
    if isinstance(times, dict) and times:
        try:
            latest = max(latest, max(float(v) for v in times.values()))
        except (TypeError, ValueError):
            pass
    if latest <= 0:
        return None
    return max(0.0, time.time() - latest)


def _ws_connected() -> bool:
    eng = state.engine
    if not eng:
        return False
    return bool(getattr(getattr(eng, "websocket", None), "_connected", False))


def _health_snapshot() -> dict:
    """Real health: 'degraded' when the engine is expected but dead, or running without ticks."""
    now = time.time()
    tick_age = _last_tick_age()
    engine_running = bool(state.running)
    task_alive = bool(state.engine_task and not state.engine_task.done())
    engine_age = (now - state.engine_started_at) if state.engine_started_at else 0.0
    reasons = []
    if state.engine_expected and not engine_running:
        reasons.append("engine_not_running")
    if engine_running:
        if tick_age is not None and tick_age > HEALTH_STALE_TICK_SEC:
            reasons.append("stale_ticks")
        elif tick_age is None and engine_age > HEALTH_STALE_TICK_SEC:
            reasons.append("no_ticks")
    degraded = bool(reasons)
    return {
        "status": "degraded" if degraded else "ok",
        "degraded": degraded,
        "reasons": reasons,
        "version": BRIDGE_VERSION,
        "engine_running": engine_running,
        "engine_expected": bool(state.engine_expected),
        "engine_task_alive": task_alive,
        "ws_connected": _ws_connected(),
        "last_tick_age_sec": round(tick_age, 3) if tick_age is not None else None,
        "autostart": state.autostart_mode or None,
        "mode": state.mode,
        "exchange": state.exchange,
        "uptime_sec": now - state.start_time,
        "clients": state.channels.client_count,
        "telegram_failures": _telegram_failures(),
        "microstructure_enabled": bool(getattr(getattr(state.engine, "settings", None), "trading", None)
                                       and state.engine.settings.trading.microstructure_enabled),
        "trend_daily_enabled": bool(getattr(getattr(state.engine, "trend_engine", None), "enabled", False)),
    }


def _telegram_failures() -> int:
    try:
        notifier = getattr(state.engine, "notifier", None)
        if notifier is not None and hasattr(notifier, "delivery_stats"):
            return int(notifier.delivery_stats().get("failures", 0))
    except Exception:
        pass
    return 0


def _hard_exit(code: int) -> None:
    """Leave the process so systemd (Restart=always) brings a fresh one up."""
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(code)


def _register_restart_attempt() -> Optional[int]:
    """Count an attempt inside the sliding window. Returns the attempt number, or None when
    the budget (_WATCHDOG_MAX_ATTEMPTS per _WATCHDOG_WINDOW_SEC) is exhausted."""
    now = time.time()
    while state.restart_attempts and now - state.restart_attempts[0] > _WATCHDOG_WINDOW_SEC:
        state.restart_attempts.popleft()
    if len(state.restart_attempts) >= _WATCHDOG_MAX_ATTEMPTS:
        return None
    state.restart_attempts.append(now)
    return len(state.restart_attempts)


async def _restart_engine_after_failure(reason: str) -> None:
    """Crash-only recovery (autostart hosts): retry start_engine with backoff; when the budget
    is exhausted exit the process with _WATCHDOG_EXIT_CODE for systemd to restart it."""
    if state.restart_in_progress or state.shutting_down or not state.autostart_mode:
        return
    state.restart_in_progress = True
    try:
        while True:
            attempt = _register_restart_attempt()
            if attempt is None:
                logger.critical("engine_restart_budget_exhausted", reason=reason,
                                attempts=_WATCHDOG_MAX_ATTEMPTS, window_sec=_WATCHDOG_WINDOW_SEC,
                                exit_code=_WATCHDOG_EXIT_CODE,
                                action="exiting so systemd restarts the service")
                _hard_exit(_WATCHDOG_EXIT_CODE)
                return
            delay = _WATCHDOG_BACKOFF_SEC[min(attempt - 1, len(_WATCHDOG_BACKOFF_SEC) - 1)]
            logger.warning("engine_restart_scheduled", reason=reason, attempt=attempt,
                           max_attempts=_WATCHDOG_MAX_ATTEMPTS, delay_sec=delay)
            await asyncio.sleep(delay)
            if state.shutting_down or not state.engine_expected:
                logger.info("engine_restart_aborted", reason="shutdown or operator stop")
                return
            try:
                await stop_engine()  # tear down the dead instance (idempotent)
                await start_engine(state.autostart_mode, settings=_build_settings(state.exchange))
                state.watchdog_stale_strikes = 0
                logger.info("engine_restarted", attempt=attempt, mode=state.autostart_mode,
                            exchange=state.exchange)
                return
            except Exception as e:
                logger.exception("engine_restart_failed", attempt=attempt, error=str(e),
                                 error_type=type(e).__name__)
                reason = f"restart failed: {type(e).__name__}: {e}"
    finally:
        state.restart_in_progress = False


def _watchdog_tick() -> Optional[str]:
    """One watchdog check. Returns a failure reason when a restart must be triggered."""
    if not state.autostart_mode or not state.engine_expected or state.restart_in_progress \
            or state.shutting_down:
        state.watchdog_stale_strikes = 0
        return None
    if not state.running:
        state.watchdog_stale_strikes = 0
        return "watchdog: engine not running"
    tick_age = _last_tick_age()
    engine_age = time.time() - state.engine_started_at if state.engine_started_at else 0.0
    stale = (tick_age is not None and tick_age > _WATCHDOG_STALE_SEC) or \
            (tick_age is None and engine_age > _WATCHDOG_STALE_SEC)
    if not stale:
        state.watchdog_stale_strikes = 0
        return None
    state.watchdog_stale_strikes += 1
    logger.warning("watchdog_stale_ticks", tick_age_sec=tick_age, strikes=state.watchdog_stale_strikes,
                   strikes_needed=_WATCHDOG_STALE_STRIKES, ws_connected=_ws_connected())
    if state.watchdog_stale_strikes >= _WATCHDOG_STALE_STRIKES:
        state.watchdog_stale_strikes = 0
        return f"watchdog: no ticks for >{int(_WATCHDOG_STALE_SEC)}s on {_WATCHDOG_STALE_STRIKES} checks"
    return None


async def _engine_watchdog_loop():
    """Every _WATCHDOG_INTERVAL_SEC: engine dead or feed stale → restart path (autostart only)."""
    while True:
        await asyncio.sleep(_WATCHDOG_INTERVAL_SEC)
        try:
            reason = _watchdog_tick()
            if reason:
                logger.error("watchdog_triggered", reason=reason)
                _spawn(_restart_engine_after_failure(reason))
        except Exception as e:
            logger.exception("watchdog_error", error=str(e))


# ── FastAPI App ──────────────────────────────────────────────────
async def _autostart_engine(mode: str, delay_sec: float = 2.0):
    """Start the engine shortly after the port is open (systemd autostart)."""
    await asyncio.sleep(delay_sec)
    if state.running:
        return
    try:
        await start_engine(mode, settings=_build_settings(state.exchange))
        logger.info("engine_autostarted", mode=mode, exchange=state.exchange)
    except Exception as e:
        logger.exception("engine_autostart_failed", mode=mode, error=str(e), error_type=type(e).__name__)
        await state.channels.broadcast("system", {
            "type": "engine_error", "error": f"autostart failed: {e}", "timestamp": time.time(),
        })
        _spawn(_restart_engine_after_failure(f"autostart failed: {type(e).__name__}: {e}"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle.

    Desktop-local: do NOT start the engine here — the user selects exchange and mode from the
    desktop UI, then clicks Start which calls POST /api/bot/start. The bridge opens the port
    immediately so the desktop can connect.
    Server (systemd): BOTSTRIKE_AUTOSTART=paper|dry_run starts the engine and arms the watchdog.
    """
    state.shutting_down = False
    loops = [
        asyncio.create_task(market_broadcast_loop()),
        asyncio.create_task(candle_broadcast_loop()),
        asyncio.create_task(metrics_broadcast_loop()),
        asyncio.create_task(system_broadcast_loop()),
        asyncio.create_task(_engine_watchdog_loop()),
        asyncio.create_task(_venue_open_interest_loop()),
    ]

    try:
        get_activity_log().add("system", "Bridge started", f"v{BRIDGE_VERSION} · mode {state.mode}")
    except Exception:  # noqa: BLE001
        pass
    logger.info("bridge_ready", version=BRIDGE_VERSION, port=int(os.getenv("BOTSTRIKE_PORT", "9420")),
                token_exposed=_EXPOSE_TOKEN)

    # Headless/server deployments (systemd): BOTSTRIKE_AUTOSTART=paper|dry_run starts the
    # engine without a desktop click. Unset/empty (desktop-local) keeps the old behaviour.
    # "live" is REFUSED here on purpose: live must be started explicitly with the auth token.
    autostart = os.getenv("BOTSTRIKE_AUTOSTART", "").strip().lower()
    if autostart in ("paper", "dry_run"):
        state.autostart_mode = autostart
        state.engine_expected = True
        state.exchange = os.getenv("BOTSTRIKE_AUTOSTART_EXCHANGE", "binance").strip().lower() or "binance"
        loops.append(asyncio.create_task(_autostart_engine(autostart)))
    elif autostart == "live":
        logger.error("autostart_live_refused",
                     hint="start live from the desktop with the auth token; never via BOTSTRIKE_AUTOSTART")
    elif autostart:
        logger.error("autostart_invalid_mode", value=autostart, valid=["paper", "dry_run"])
    yield

    state.shutting_down = True
    await stop_engine()
    for t in list(loops) + list(state.bg_tasks):
        t.cancel()
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass


app = FastAPI(title="BotStrike Bridge", version=BRIDGE_VERSION, lifespan=lifespan)

# CORS: allow all localhost origins (Tauri uses varying origin formats)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1|tauri\.localhost)(:\d+)?$|^tauri://localhost$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_DOCS_PATHS = {"/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"}


@app.middleware("http")
async def _hide_docs_when_remote(request, call_next):
    """API docs are only served on a loopback bind (desktop-local). Also: the web UI
    uses a HashRouter, so a typed/bookmarked `/performance` (no `#`) used to answer a
    JSON 404 (audit 2026-09-02) — redirect any extension-less non-API path to `/#/…`."""
    if not _EXPOSE_TOKEN and request.url.path in _DOCS_PATHS:
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    response = await call_next(request)
    path = request.url.path
    if (response.status_code == 404 and request.method == "GET" and path != "/"
            and not path.startswith(("/api", "/ws", "/assets", "/docs", "/redoc", "/openapi"))
            and "." not in path.rsplit("/", 1)[-1] and _WEBUI_DIR.is_dir()):
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=f"/#{path}", status_code=302)
    return response


def _token_ok(supplied: str) -> bool:
    return bool(supplied) and secrets.compare_digest(supplied, _AUTH_TOKEN)


async def supplied_token(token: str = "", x_botstrike_token: str = Header(default="")) -> str:
    """The caller's token from either source. `X-BotStrike-Token` is what clients SHOULD use
    (the query string ends up in access logs, proxies and browser history — audit R2
    security_supply-01); `?token=` stays accepted so older desktop builds keep working."""
    return token or x_botstrike_token


async def require_token_when_remote(supplied: str = Depends(supplied_token)):
    """Loopback bind (desktop-local): unchanged — paper/dry_run need no token.
    Non-loopback bind (server on 0.0.0.0): every mutation needs BOTSTRIKE_AUTH_TOKEN
    (header `X-BotStrike-Token`, or legacy query `token=`)."""
    if _EXPOSE_TOKEN:
        return
    if not _token_ok(supplied):
        raise HTTPException(status_code=401, detail="auth token required (BOTSTRIKE_AUTH_TOKEN)")


# ── WebSocket Endpoints ──────────────────────────────────────────
@app.websocket("/ws/{channel}")
async def websocket_endpoint(ws: WebSocket, channel: str):
    if channel not in ChannelManager.VALID_CHANNELS:
        await ws.close(code=4000, reason=f"Unknown channel: {channel}")
        return

    await state.channels.connect(channel, ws)
    try:
        while True:
            data = await ws.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await ws.send_text(json.dumps({"type": "pong"}))
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        state.channels.disconnect(channel, ws)


# ── REST Endpoints ───────────────────────────────────────────────
@app.get("/api/health")
async def health(response: Response):
    """200 when healthy; 503 (same JSON, status=degraded) when the engine is expected but not
    running, or running without market ticks for > HEALTH_STALE_TICK_SEC."""
    snap = _health_snapshot()
    response.status_code = 503 if snap["degraded"] else 200
    return snap


def _config_settings() -> Settings:
    """The live engine settings when running, otherwise a fresh Settings() that
    already carries the persisted overrides — so the UI can edit before starting."""
    if state.engine:
        return state.engine.settings
    if state.pending_settings is None:
        state.pending_settings = Settings()
    return state.pending_settings


def _config_payload() -> dict:
    s = _config_settings()
    out = serialize_settings(s)
    out.update(cfg_overrides.overrides_state(s))
    out["engine_running"] = bool(state.running)
    return out


@app.get("/api/config")
async def get_config():
    return _config_payload()


@app.get("/api/config/schema")
async def get_config_schema():
    return cfg_overrides.schema(symbols=_config_settings().symbol_names)


@app.put("/api/config", dependencies=[Depends(require_token_when_remote)])
async def put_config(body: dict = {}):
    """Partial update {trading: {...}, symbols: {SYM: {...}}} — validated (bounds +
    Settings.validate()), applied LIVE to the running engine's settings object and
    persisted to data/config_overrides.json so the next start keeps it."""
    if not isinstance(body, dict) or not body:
        raise HTTPException(status_code=400, detail="empty patch")
    s = _config_settings()
    try:
        applied, restart_now = cfg_overrides.validate_and_apply(s, body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    merged = cfg_overrides.merge_overrides(cfg_overrides.load_overrides(), body)
    cfg_overrides.save_overrides(merged)
    if state.engine is not None:
        _after_live_config_change(applied)
    logger.info("config_updated", applied=applied, restart_required=restart_now)
    try:
        get_activity_log().add("config", "Config changed", ", ".join(map(str, applied))[:240]
                               + (" · restart required" if restart_now else ""))
    except Exception:  # noqa: BLE001
        pass
    payload = _config_payload()
    return {"status": "ok", "applied": applied,
            "restart_required": bool(restart_now or payload.get("restart_required")),
            "config": payload}


def _after_live_config_change(applied: list) -> None:
    """Hooks for fields the engine caches (everything else is read at use time)."""
    engine = state.engine
    try:
        if any(p.startswith("trading.allocation_") for p in applied):
            engine.portfolio_manager._current_weights = {
                st: strategy_allocation(engine.settings.trading, st) for st in StrategyType}
        if "trading.telegram_enabled" in applied:
            logger.info("telegram_enabled_change_needs_restart")
        if any(p.startswith("trading.edge_") for p in applied):
            engine._last_edge_check = 0.0   # re-evaluate on the next metrics tick
    except Exception as e:
        logger.warning("live_config_hook_error", error=str(e))


@app.post("/api/config/reset", dependencies=[Depends(require_token_when_remote)])
async def reset_config():
    cfg_overrides.clear_overrides()
    state.pending_settings = None
    payload = _config_payload()
    payload["restart_required"] = True
    return {"status": "ok", "restart_required": True, "config": payload}


@app.post("/api/bot/restart", dependencies=[Depends(require_token_when_remote)])
async def bot_restart(supplied: str = Depends(supplied_token)):
    """Stop + start in the same mode/exchange (config changes marked restart_required)."""
    mode, exchange = state.mode, state.exchange
    if mode == "live" and not _token_ok(supplied):
        raise HTTPException(status_code=401, detail="Invalid or missing auth token for live mode")
    if state.running:
        await stop_engine(manual=True)
    state.pending_settings = None
    settings = _build_settings(exchange)
    await start_engine(mode, settings=settings)
    return {"status": "restarting", "mode": mode, "exchange": exchange}


@app.get("/api/edge")
async def get_edge():
    """Live edge monitor. Retired strategies are not part of the product any more, so they are not
    monitored here: the endpoint listed MEAN_REVERSION, FIBONACCI_RETRACEMENT and DIVERGENCE with a
    row of zeros each, which reads as three strategies running and producing nothing (audit
    2026-09-04). The trades they made are still in the trade record; this is the live monitor, and
    the filter belongs here rather than in analytics/edge.py, which is arithmetic, not product."""
    engine = state.engine
    if not engine:
        return {"window": 0, "min_trades": 0, "strategies": {}}
    stats = getattr(engine, "edge_stats", None)
    if not stats:
        await engine._edge_monitor_tick(force=True)
        stats = engine.edge_stats
    from core.types import RETIRED_STRATEGIES
    out = dict(stats or {})
    out["strategies"] = {k: v for k, v in (out.get("strategies") or {}).items()
                         if k not in RETIRED_STRATEGIES}
    return out


@app.get("/api/trend")
async def get_trend():
    engine = state.engine
    trend = getattr(engine, "trend_engine", None) if engine else None
    if trend is None:
        s = _config_settings()
        return {"enabled": float(s.trading.allocation_trend_daily) > 0, "allocation": s.trading.allocation_trend_daily,
                "mode": state.mode, "engine": False, "positions": [], "targets": {}, "universe": [],
                "tracking": {"days": 0, "records": []}, "next_run_utc": "", "last_run_utc": "",
                "last_run_status": "engine not running", "last_error": ""}
    out = trend.status()
    out["mode"] = state.mode
    out["engine"] = True
    return out


@app.get("/api/orders")
async def get_orders():
    """Protective SL/TP of open paper positions as pseudo-orders (contract §2)."""
    engine = state.engine
    sim = getattr(engine, "paper_sim", None) if engine else None
    if sim is None or not hasattr(sim, "get_protective_orders"):
        return {"orders": []}
    return _json_safe({"orders": sim.get_protective_orders()})


@app.get("/api/positions")
async def get_positions():
    engine = state.engine
    if not engine:
        return {"positions": []}
    return _json_safe({"positions": _paper_position_rows(engine)})


def _account_overview(engine) -> dict:
    tc = engine.settings.trading
    rows = _paper_position_rows(engine)
    position_value = float(sum(r.get("notional", 0.0) or 0.0 for r in rows))
    margin_used = float(sum(r.get("margin", 0.0) or 0.0 for r in rows))
    unrealized = float(sum(r.get("unrealized_pnl", 0.0) or 0.0 for r in rows))
    perf = _merged_performance() or {}
    equity = float(perf.get("equity", engine.risk_manager.current_equity))
    realized = float(perf.get("realized_pnl", 0.0))
    fees_today = 0.0
    try:
        import datetime as _dt
        day0 = _dt.datetime.now(_dt.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        fees_today = float(sum((t.fee or 0.0) for t in engine.trade_repo.get_trades(
            source="paper" if engine.paper else "live", start_time=day0)))
    except Exception:
        pass
    rm = engine.risk_manager
    return {
        "mode": state.mode, "equity": round(equity, 4), "initial_capital": float(tc.initial_capital),
        "realized_pnl": round(realized, 4), "unrealized_pnl": round(unrealized, 4),
        "position_value": round(position_value, 4), "margin_used": round(margin_used, 4),
        "available": round(max(0.0, equity - margin_used), 4),
        "margin_ratio": round(margin_used / equity, 6) if equity > 0 else 0.0,
        "exposure_pct": round(position_value / equity, 6) if equity > 0 else 0.0,
        "leverage_effective": round(position_value / equity, 4) if equity > 0 else 0.0,
        "open_positions": len(rows), "fees_today": round(fees_today, 4),
        "daily_pnl": round(float(getattr(rm, "daily_pnl_mtm", rm.daily_pnl)), 4),
        "weekly_pnl": round(float(getattr(rm, "weekly_pnl_mtm", rm.weekly_pnl)), 4),
        "daily_pnl_realised": round(float(rm.daily_pnl), 4), "weekly_pnl_realised": round(float(rm.weekly_pnl), 4),
        "peak_equity": round(float(rm.equity_peak), 4), "drawdown_pct": round(float(rm.current_drawdown_pct), 6),
        "max_leverage": int(tc.max_leverage), "max_total_exposure_pct": float(tc.max_total_exposure_pct),
    }


@app.get("/api/account")
async def get_account():
    engine = state.engine
    if not engine:
        s = _config_settings()
        return {"engine": False, "mode": state.mode, "initial_capital": s.trading.initial_capital}
    out = _account_overview(engine)
    out["engine"] = True
    acc = getattr(engine, "funding", None)
    if acc is not None:
        out["funding_paid"] = round(float(acc.total_paid), 6)
    return _json_safe(out)


def _funding_countdown_sec(now: float, interval_hours: int = 1) -> int:
    """Seconds to the venue's next settlement. Strike settles hourly; a hardcoded 8 h clock told the
    operator the next payment was 4 h 55 m away when it was 55 minutes (audit 2026-09-03)."""
    period = max(1, int(interval_hours)) * 3600
    return int(period - (now % period))


def _json_safe(obj):
    """Replace non-finite floats (inf/nan) with None, recursively. Starlette's JSON encoder
    raises on them → 500. Seen on /api/market during startup: data_age_sec = inf before the
    first tick (local smoke 2026-09-02, 8x 500 while the engine seeded)."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


def _activity_fill(trade, serialized: dict) -> None:
    """Record a fill in the activity feed (never raises). Exits are detected like the live log:
    the serialized trade may not carry trade_type."""
    try:
        ttype = getattr(trade, "trade_type", None)
        ttype = getattr(ttype, "value", ttype)
        if not ttype:
            ttype = "EXIT" if (float(getattr(trade, "pnl", 0) or 0) != 0 or float(getattr(trade, "fee", 0) or 0) > 0) else "ENTRY"
        row = dict(serialized)
        row.setdefault("timestamp", getattr(trade, "timestamp", None))
        row["trade_type"] = str(ttype)
        get_activity_log().record_fill(row)
    except Exception:  # noqa: BLE001
        pass


@app.get("/api/activity")
async def get_activity(limit: int = 100, kind: Optional[str] = None):
    """Operator timeline (spec v2.16 §5.2): fills, daily runs, regime changes, kills, risk, config, system."""
    return {"events": get_activity_log().list(limit=limit, kind=kind)}


@app.get("/api/portfolio")
async def get_portfolio():
    """Strike-style Portfolio page data (spec v2.16 §5.1). Same trade DB and account numbers as
    /api/trades and /api/account, so the page cannot disagree with them."""
    engine = state.engine
    if not engine:
        return {"engine": False, "mode": state.mode}
    repo = getattr(engine, "trade_repo", None)
    trades = []
    if repo is not None:
        try:
            trades = await asyncio.to_thread(repo.get_trades, source="paper" if getattr(engine, "paper", True) else "live")
        except Exception as e:  # noqa: BLE001
            logger.warning("portfolio_trades_unavailable", error=str(e))
    positions = _paper_position_rows(engine) if getattr(engine, "paper_sim", None) else []
    acct = _account_overview(engine)
    tcfg = engine.settings.trading
    out = compute_portfolio(
        trades, float(tcfg.initial_capital), positions, time.time(),
        equity=float(acct.get("equity") or 0.0), margin_used=float(acct.get("margin_used") or 0.0),
        unrealized_pnl=float(acct.get("unrealized_pnl") or 0.0),
        fees_taker=float(getattr(tcfg, "taker_fee", 0.0004)), fees_maker=float(getattr(tcfg, "maker_fee", 0.0002)),
    )
    out.update({"engine": True, "mode": state.mode})
    return _json_safe(out)


_FUNDING_CACHE: dict = {}
STRIKE_STATS_BASE = os.getenv("BOTSTRIKE_STRIKE_STATS", "https://api.strikefinance.org/stat/v1/stats/coin")
FUNDING_CACHE_SEC = 300


async def _fetch_strike_funding_history(symbol: str, days: int) -> list:
    """Strike's own funding history: [{ts_ms, funding_rate}] hourly, up to 90 days. Patched in tests.

    This is the venue the book executes on, so it is the only history that describes what a position
    actually pays. The stats host rejects a default User-Agent with 403.
    """
    import httpx
    url = f"{STRIKE_STATS_BASE}/history/funding"
    async with httpx.AsyncClient(timeout=15.0, headers={"User-Agent": "botstrike/1.0"}) as client:
        r = await client.get(url, params={"symbol": symbol, "days": int(days)})
        r.raise_for_status()
        payload = r.json()
    cols = payload.get("columns") or []
    return [dict(zip(cols, row)) for row in (payload.get("data") or [])]


async def _fetch_funding_history(binance_symbol: str, limit: int) -> list:
    """Binance USDⓈ-M public funding history (no key). Patched in tests."""
    import httpx
    url = "https://fapi.binance.com/fapi/v1/fundingRate"
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(url, params={"symbol": binance_symbol, "limit": int(limit)})
        r.raise_for_status()
        return r.json()


@app.get("/api/market/{symbol}/funding_history")
async def get_funding_history(symbol: str, limit: int = 200):
    """Funding-rate history for the Funding tab: the VENUE's own settlements, positive = longs pay.

    Strike first, because that is where the book executes and its markets are not Binance's: asking
    Binance for XAU-USD returned its own XAUUSDT perp (a different market with different funding),
    and SP500-USD / WTI-USD returned nothing at all (audit 2026-09-03).
    """
    from exchange.binance_client import SYMBOL_MAP as _BSYM
    limit = max(10, min(int(limit), 1000))
    bsym = _BSYM.get(symbol, symbol.replace("-", "").replace("USD", "USDT") if "USDT" not in symbol else symbol)
    now = time.time()
    cached = _FUNDING_CACHE.get((symbol, limit))
    if cached and now - cached["cached_at"] < FUNDING_CACHE_SEC:
        return cached
    points, source, err = [], "strike", ""
    try:
        days = max(1, min(90, -(-limit // 24)))          # the venue publishes hourly rows
        for row in await _fetch_strike_funding_history(symbol, days):
            try:
                points.append({"ts": float(row["ts"]) / 1000.0, "rate": float(row["funding_rate"]),
                               "mark_price": None})
            except (KeyError, TypeError, ValueError):
                continue
        points = points[-limit:]
    except Exception as e:  # noqa: BLE001
        logger.warning("strike_funding_history_unavailable", symbol=symbol, error=str(e))
        err = f"{type(e).__name__}"
    if not points:
        source = "binance_fapi"
        try:
            raw = await _fetch_funding_history(bsym, limit)
        except Exception as e:  # noqa: BLE001
            logger.warning("funding_history_unavailable", symbol=symbol, error=str(e))
            if cached:
                return cached
            return {"symbol": symbol, "points": [], "cumulative": [], "source": source, "cached_at": now,
                    "error": err or f"{type(e).__name__}"}
        for row in raw:
            try:
                points.append({"ts": float(row["fundingTime"]) / 1000.0, "rate": float(row["fundingRate"]),
                               "mark_price": float(row.get("markPrice") or 0.0) or None})
            except (KeyError, TypeError, ValueError):
                continue
    points.sort(key=lambda p: p["ts"])
    cum, cumulative = 0.0, []
    for p in points:
        cum += p["rate"]
        cumulative.append({"ts": p["ts"], "value": cum})
    out = {"symbol": symbol, "binance_symbol": bsym, "points": points, "cumulative": cumulative,
           "source": source, "cached_at": now}
    _FUNDING_CACHE[(symbol, limit)] = out
    return out


@app.get("/api/risk/profiles")
async def get_risk_profiles():
    """Risk profiles: the validated way to trade the same strategy harder or softer.

    Leverage does not create edge — the Sharpe is flat across profiles while return and drawdown
    scale together (config/risk_profiles.py has the measured numbers). Each profile moves the
    target volatility AND the loss ladder, so raising risk does not make the circuit breaker halt
    the bot on an ordinary losing streak.
    """
    from config import risk_profiles as rp
    engine = state.engine
    s = engine.settings if engine else _config_settings()
    equity = 0.0
    if engine is not None:
        try:
            equity = float(engine.risk_manager.current_equity)
        except Exception:  # noqa: BLE001
            equity = float(s.trading.initial_capital)
    else:
        equity = float(s.trading.initial_capital)
    # Price the profiles on the basis the engine SIZES on (equity including open positions), not on
    # the risk manager's realised equity: the header read "current equity $1,009.64" beside an equity
    # card showing $1,016.07 on the same page (audit 2026-09-03).
    basis = equity
    if engine is not None:
        try:
            basis = float(engine._sizing_equity())
        except Exception:  # noqa: BLE001
            basis = equity
    return _json_safe({
        "current": rp.profile_of(s.trading),
        "equity": round(equity, 2),
        "equity_basis": round(basis, 2),
        "validated_target_vol_range": list(rp.VALIDATED_RANGE),
        "profiles": rp.catalog(basis),
        "current_values": {"trend_target_vol": float(s.trading.trend_target_vol),
                           "max_drawdown_pct": float(s.trading.max_drawdown_pct),
                           "max_daily_loss_pct": float(s.trading.max_daily_loss_pct),
                           "max_weekly_loss_pct": float(s.trading.max_weekly_loss_pct)},
        "source": "tasks/research_trend_multi_2026-09-03.md",
    })


@app.post("/api/risk/profile", dependencies=[Depends(require_token_when_remote)])
async def set_risk_profile(body: dict):
    """Apply a risk profile (conservative | balanced | aggressive). Persisted like any other
    config change and applied live; the new target volatility takes effect at the next daily run."""
    from config import risk_profiles as rp
    name = str((body or {}).get("profile") or "").strip().lower()
    if name not in rp.PROFILES:
        raise HTTPException(status_code=400, detail=f"unknown profile '{name}' (use {list(rp.PROFILES)})")
    patch = {"trading": dict(rp.PROFILES[name])}
    s = _config_settings()
    try:
        applied, restart_now = cfg_overrides.validate_and_apply(s, patch)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    merged = cfg_overrides.merge_overrides(cfg_overrides.load_overrides(), patch)
    cfg_overrides.save_overrides(merged)
    if state.engine is not None:
        _after_live_config_change(applied)
    logger.info("risk_profile_applied", profile=name, applied=applied)
    try:
        get_activity_log().add("config", f"Risk profile: {name}",
                               ", ".join(f"{k.split('.')[-1]}={v}" for k, v in rp.PROFILES[name].items()))
    except Exception:  # noqa: BLE001
        pass
    return _json_safe({"status": "ok", "profile": name, "applied": applied,
                       "restart_required": bool(restart_now), "describe": rp.describe(name)})


_COSTS_CACHE: dict = {"mtime": 0.0, "data": {}}


def _measured_funding_90d() -> dict:
    """{symbol: annualised fraction} measured on the venue over 90 days (scripts/strike_market_stats.py).

    One hour of funding annualised swings between -80 % and +90 %/yr, which is true and useless on
    its own. Shown next to the measured median it becomes readable: "now" versus "normally".
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "data", "strike_costs.json")
    try:
        mtime = os.path.getmtime(path)
        if mtime != _COSTS_CACHE["mtime"]:
            with open(path, encoding="utf-8") as fh:
                raw = json.load(fh)
            _COSTS_CACHE["data"] = {k: float(v["funding"]["annualized_pct"])
                                    for k, v in (raw.get("markets") or {}).items()
                                    if isinstance(v, dict) and (v.get("funding") or {}).get("annualized_pct") is not None}
            _COSTS_CACHE["mtime"] = mtime
    except Exception:  # noqa: BLE001 - the snapshot is optional context, never a hard dependency
        return _COSTS_CACHE.get("data") or {}
    return _COSTS_CACHE["data"]


def _venue_quote_age(engine) -> Optional[float]:
    """How old the cached venue quote is. A rate that moves every second will differ in its last
    printed digit between two clients; showing the age turns that into a fact instead of a doubt."""
    ts = float(getattr(engine, "_venue_funding_ts", 0.0) or 0.0)
    return round(max(0.0, time.time() - ts), 1) if ts else None


def _live_funding_rates(engine, interval_hours: int) -> dict:
    """The rate each market is charged at, as the ENGINE charges it — never a different number.

    Before this (audit 2026-09-03) the panel read the intraday feed only: it annualised Binance's
    8-hour rate on the venue's hourly clock (87 %/yr for BTC), listed two markets the book does not
    hold and omitted the four it does. Reads cached state only: no network call in a request handler.
    """
    from analytics.funding import annualized_pct
    venue = dict(getattr(engine, "_venue_funding", {}) or {})
    scale = max(1, int(interval_hours)) / 8.0          # the feed quotes an 8 h rate
    try:
        held = {str(p.get("symbol")) for p in (engine._funding_positions() or [])}
    except Exception:  # noqa: BLE001 - a display figure must not break the endpoint
        held = set()
    # Also price the markets the daily run MAY buy tomorrow: a trend book rotates, and the carry of a
    # candidate is exactly what decides whether holding it is worth it (audit 2026-09-03: BNB, NAS100,
    # XRP and ZEC were in the pool with no rate anywhere on screen).
    pool = set()
    try:
        for x in str(getattr(engine.settings.trading, "trend_pool", "") or "").split(","):
            x = x.strip().upper()
            if x:
                pool.add(x if "-" in x else (x[:-4] + "-USD" if x.endswith("USDT") else x))
    except Exception:  # noqa: BLE001
        pool = set()
    out, measured = {}, _measured_funding_90d()
    for sym in sorted(held | pool | set(getattr(engine.settings, "symbol_names", []) or [])):
        fresh = (_VENUE_MD["premium"] or {}).get(sym, {}).get("fundingRate")
        if fresh is not None:
            rate, source = _num(fresh) or 0.0, "venue"      # the freshest quote, as everywhere else
        elif sym in venue:
            rate, source = float(venue[sym] or 0.0), "venue"
        else:
            snap = engine.market_data.get_snapshot(sym)
            raw = float(snap.funding_rate) if snap is not None and snap.funding_rate else 0.0
            rate, source = raw * scale, ("feed" if raw else "none")
        out[sym] = {"rate": rate, "annualized_pct": round(annualized_pct(rate, interval_hours), 6),
                    "held": sym in held, "candidate": sym in pool, "source": source,
                    "annualized_90d": measured.get(sym)}
    return out


@app.get("/api/funding")
async def get_funding():
    """Perpetual funding accrued on the paper book (analytics/funding.py): cumulative cost, per
    symbol, recent settlements, and the live rate per symbol with its annualized equivalent."""
    engine = state.engine
    if not engine:
        return {"engine": False, "enabled": bool(getattr(_config_settings().trading, "funding_enabled", True))}
    from analytics.funding import annualized_pct
    acc = getattr(engine, "funding", None)
    out = acc.status() if acc is not None else {"enabled": False}
    out["enabled"] = bool(getattr(engine.settings.trading, "funding_enabled", True))
    out["engine"] = True
    out["rates"] = _live_funding_rates(engine, int(out.get("interval_hours") or 1))
    out["quote_age_sec"] = _venue_quote_age(engine)
    return _json_safe(out)


@app.get("/api/markets")
async def get_markets():
    """Every market the bot could operate, not only the four with an intraday feed.

    The picker listed a hard-coded crypto four while the trend book was holding gold, silver, the S&P
    and oil (audit 2026-09-04). A market is tagged with what it actually offers here: `feed` = a live
    intraday stream (chart, order book, tape), `pool` = a candidate the daily run may buy, `held` = an
    open position right now. Reads the cached venue snapshot: no network call in a request handler.
    """
    engine = state.engine
    if not engine:
        return {"engine": False, "markets": []}
    venue = dict(getattr(engine, "_venue_funding", {}) or {})
    feed = [str(x).upper() for x in (getattr(engine.settings, "symbol_names", []) or [])]
    pool = set()
    try:
        for x in str(getattr(engine.settings.trading, "trend_pool", "") or "").split(","):
            x = x.strip().upper()
            if x:
                pool.add(x if "-" in x else (x[:-4] + "-USD" if x.endswith("USDT") else x))
    except Exception:  # noqa: BLE001
        pass
    try:
        held = {str(p.get("symbol")) for p in (engine._funding_positions() or [])}
    except Exception:  # noqa: BLE001
        held = set()
    measured = _measured_funding_90d()
    interval = _funding_interval(engine)
    from analytics.funding import annualized_pct
    # The picker's price, 24 h change, volume and open-interest columns were read off the intraday
    # feed, so they were Binance's for the four streamed markets and empty for the other 27 — a
    # single row of the list mixed two venues (audit 2026-09-04). One cached venue snapshot answers
    # every column for every market, and the open interest comes from the background refresh below.
    md = await _venue_market_data()
    oi_cache = _VENUE_MD.get("oi") or {}
    out = []
    for sym in sorted(set(venue) | set(feed) | pool | held | set(md["premium"])):
        prem, tick = md["premium"].get(sym) or {}, md["ticker"].get(sym) or {}
        # The bridge's snapshot is five seconds old, the engine's up to a minute: same field, same
        # endpoint, and the panel shows the fresher one — so this list shows it too.
        rate = _num(prem.get("fundingRate"))
        if rate is None and sym in venue:
            rate = float(venue[sym] or 0.0)
        oi = oi_cache.get(sym)
        out.append({"symbol": sym, "feed": sym in feed, "pool": sym in pool, "held": sym in held,
                    "funding_rate": rate,
                    "annualized_pct": round(annualized_pct(rate, interval), 6) if rate is not None else None,
                    "annualized_90d": measured.get(sym),
                    "price": _num(prem.get("markPrice")) or _num(tick.get("lastPrice")),
                    "change_24h_pct": ((_num(tick.get("priceChangePercent")) or 0.0) / 100.0) if tick else None,
                    "volume_24h_usd": _num(tick.get("quoteVolume")) if tick else None,
                    "open_interest": oi[1] if isinstance(oi, tuple) else None})
    return _json_safe({"engine": True, "venue": str(getattr(engine.settings.trading, "exchange_venue", "") or ""),
                       "interval_hours": interval, "quote_age_sec": _venue_quote_age(engine), "markets": out})


@app.get("/api/ops")
async def get_ops():
    """Last ops-monitor evaluation (scripts/ops_monitor.py, CT timer) for the System page (spec §5.4)."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    last_path = os.getenv("BOTSTRIKE_OPS_LAST", os.path.join(root, "data", "ops_monitor_last.json"))
    state_path = os.getenv("BOTSTRIKE_OPS_STATE", os.path.join(root, "data", "ops_monitor_state.json"))

    def _read(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:  # noqa: BLE001
            return None

    last = _read(last_path)
    st = _read(state_path) or {}
    if not last:
        return {"available": False, "last_check": None, "alerts": [], "sent": [], "summary_sent": False, "facts": {},
                "journal_15": {}, "state": st}
    return {"available": True, "last_check": last.get("ts"), "alerts": last.get("alerts", []), "sent": last.get("sent", []),
            "summary_sent": bool(last.get("summary_sent")), "facts": last.get("facts", {}),
            # Faults seen once and not yet confirmed. Without this the page showed a bare
            # "bridge down" beside a green ALL CLEAR and no way to tell why (audit 2026-09-03).
            "pending": last.get("pending") or {},
            "journal_15": last.get("journal_15", {}), "journal_60": last.get("journal_60", {}),
            "state": {"last_summary_date": st.get("last_summary_date"), "last_alerts": st.get("last_alerts", {}),
                      "last_run": st.get("last_run")}}


@app.get("/api/trades/export.csv")
async def export_trades_csv(symbol: Optional[str] = None, strategy: Optional[str] = None):
    """All trades as CSV (spec §5.5) — same rows as /api/trades, oldest first."""
    import csv
    import io as _io
    engine = state.engine
    repo = getattr(engine, "trade_repo", None) if engine else None
    rows = []
    if repo is not None:
        try:
            trades = await asyncio.to_thread(repo.get_trades, source="paper" if getattr(engine, "paper", True) else "live",
                                             symbol=symbol, strategy=strategy)
            rows = [_trade_row(t) for t in sorted(trades, key=lambda t: float(t.timestamp or 0.0))]
        except Exception as e:  # noqa: BLE001
            logger.warning("trades_export_failed", error=str(e))
    cols = ["trade_id", "timestamp", "entry_time", "exit_time", "symbol", "side", "trade_type", "strategy", "regime",
            "entry_price", "exit_price", "quantity", "pnl", "pnl_bps", "roe_pct", "fee", "leverage", "hold_sec",
            "mae_bps", "mfe_bps", "slippage_bps", "spread_bps", "order_type", "exit_reason", "equity_after",
            "signal_strength", "order_id"]
    buf = _io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({c: r.get(c, "") for c in cols})
    return Response(content=buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="botstrike_trades.csv"'})


@app.get("/api/market/{symbol}")
async def get_market(symbol: str):
    engine = state.engine
    if not engine:
        return {"symbol": symbol, "engine": False}
    snap = engine.market_data.get_snapshot(symbol)
    stats = engine.market_data.get_24h_stats(symbol) if hasattr(engine.market_data, "get_24h_stats") else {}
    det = engine.regime_detector
    rs = det.status(symbol) if hasattr(det, "status") else {}
    ob = snap.orderbook if snap else None
    # THIS HEADER DESCRIBES A STRIKE MARKET, SO EVERY FIGURE IN IT IS STRIKE'S (audit 2026-09-04).
    # The engine's intraday feed is Binance (exchange_venue="binance"), a price reference for the
    # strategies — not the venue an order reaches. Reading the header off that feed printed Binance's
    # market on a Strike screen: 24 h volume of 199,026 BTC against Strike's own 23.89, open interest
    # of 113,100 BTC against Strike's 3.78, a 0.012 bps spread against Strike's 0.09. Every one of
    # those made a thin venue look deep, which is exactly the error that gets a size wrong. The feed
    # keeps the chart, the tape and the book ladder — it is labelled as the reference there.
    md = await _venue_market_data()
    prem = md["premium"].get(symbol.upper(), {})
    tick = md["ticker"].get(symbol.upper(), {})
    v_mark, v_index = _num(prem.get("markPrice")), _num(prem.get("indexPrice"))
    v_last = _num(tick.get("lastPrice"))
    # ONE BOOK, ONE AGE. The engine's own feed is the venue's now, so its book is the book the
    # ladder on screen is drawing. Making a second depth call here gave the header a different
    # snapshot from the panel beside it — 0.01 bps against 3.02 on the same screen (2026-09-04).
    # The extra call is only for the 27 markets the engine does not stream.
    v_depth = None
    if ob is not None and ob.best_bid and ob.best_ask:
        v_depth = {"best_bid": ob.best_bid, "best_ask": ob.best_ask,
                   "spread_bps": round(float(ob.spread_bps), 4)}
    else:
        v_depth = await _venue_depth(symbol)
    v_oi = await _venue_open_interest(symbol)
    v_stats = {}
    if tick:
        # field names the market view actually reads (volume_24h_usd / _base), not invented ones
        v_stats = {"change_24h_pct": (_num(tick.get("priceChangePercent")) or 0.0) / 100.0,
                   "high_24h": _num(tick.get("highPrice")), "low_24h": _num(tick.get("lowPrice")),
                   "volume_24h_base": _num(tick.get("volume")),
                   "volume_24h_usd": _num(tick.get("quoteVolume")),
                   "trades_24h": tick.get("count"), "window_min": 1440, "source": "venue"}
    # The venue's 24 h high/low are TRADED extremes, while the header leads with the MARK. On a thin
    # market the two drift apart and the price sits outside its own range: CRCL traded once all day
    # at 88.94 and marks at 101.45, so the panel read "24h High 88.94" under a price of 101.45
    # (audit 2026-09-04). Both figures are Strike's and both are right; presented side by side they
    # read as a bug. Fold the live mark into the range, which is what a range is for.
    live = v_mark or v_last
    if v_stats and live:
        if v_stats.get("high_24h") is not None:
            v_stats["high_24h"] = max(float(v_stats["high_24h"]), float(live))
        if v_stats.get("low_24h") is not None:
            v_stats["low_24h"] = min(float(v_stats["low_24h"]), float(live))
    venue_spread = (v_depth or {}).get("spread_bps")
    if venue_spread is None:
        venue_spread = _venue_spread_bps(symbol)          # the measured median, still Strike's
    return _json_safe({
        "symbol": symbol, "engine": True,
        "feed": snap is not None,
        # the venue's mark is the number Strike's own header leads with, so ours leads with it too;
        # its last print is the fallback, and for a market absent from the ticker (COIN-USD) the mark
        # is all there is — without it the panel showed "---" on a market that trades (2026-09-04)
        "price": v_mark or v_last or (float(snap.price) if snap else None),
        "mark_price": v_mark if v_mark is not None else (float(snap.mark_price) if snap else None),
        "index_price": v_index if v_index is not None else (float(snap.index_price) if snap else None),
        "funding_rate": _market_funding_rate(engine, symbol, snap),
        "funding_countdown_sec": _funding_countdown_sec(time.time(), _funding_interval(engine)),
        "open_interest": v_oi if v_oi is not None else (float(snap.open_interest) if snap else None),
        "spread_bps": venue_spread,
        "best_bid": (v_depth or {}).get("best_bid"), "best_ask": (v_depth or {}).get("best_ask"),
        # what the reference feed says, kept apart and labelled as such wherever the UI shows it
        "feed_price": float(snap.price) if snap else None,
        # same venue as `spread_bps` since the engine moved to Strike; kept so a client that still
        # reads it does not break, and so the two can be compared if they ever diverge again
        "feed_spread_bps": float(ob.spread_bps) if ob else None,
        "feed_age_sec": round(engine.market_data.get_data_age(symbol), 3) if snap else None,
        # the venue's 24 h block when it answered, otherwise whatever the engine had (possibly empty)
        **(v_stats or stats),
        "regime": rs.get("regime", "UNKNOWN"), "regime_since": rs.get("confirmed_since", 0.0),
        "regime_candidate": rs.get("candidate", ""), "regime_timeframe_min": rs.get("timeframe_min", 1),
        # age of what this header is actually showing — the venue quote — not of the reference feed
        "data_age_sec": round(time.time() - float(md["ts"]), 1) if md.get("ts") else None,
        "symbol_config": _symbol_config_view(engine, symbol),
        "venue_filters": md["filters"].get(symbol.upper()),
    })


# Per-symbol venue data for the market the operator is LOOKING at. Only ever one symbol at a time,
# so these cost a request every few seconds, not 31 of them.
KLINES_TTL_SEC = 10.0
BOOK_TTL_SEC = 2.0
TAPE_TTL_SEC = 3.0
_INTERVAL_SEC = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
                 "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "12h": 43200,
                 "1d": 86400, "1w": 604800}
# Where to go when the requested resolution is too fine for how often a market trades.
_LADDER = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d", "1w"]
_COARSER = {iv: _LADDER[i + 1:] for i, iv in enumerate(_LADDER)}


_APP_ROOT = Path(__file__).resolve().parents[1]
_STORE_CACHE: Dict[str, tuple] = {}     # symbol -> (file mtime, 1 m frame in seconds)


def _engine_store(sym: str):
    """The engine's one-minute history on disk (`data/binance/klines/<sym>/1m.parquet`, the ninety
    days `update_market_data` keeps current), as a frame in seconds — cached until the file changes.
    None when the symbol has no store."""
    path = _APP_ROOT / "data" / "binance" / "klines" / sym / "1m.parquet"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    hit = _STORE_CACHE.get(sym)
    if hit is not None and hit[0] == mtime:
        return hit[1]
    import pandas as pd
    df = pd.read_parquet(path, columns=["timestamp", "open", "high", "low", "close", "volume"])
    ts = pd.to_numeric(df["timestamp"], errors="coerce").astype(float)
    df["timestamp"] = ts.where(ts <= 1e12, ts / 1000.0)
    df = df.dropna(subset=["timestamp"]).sort_values("timestamp").drop_duplicates("timestamp", keep="last")
    df = df.reset_index(drop=True)
    _STORE_CACHE[sym] = (mtime, df)
    return df


def _engine_klines(sym: str, interval: str, limit: int) -> Optional[list]:
    """The engine's own bars for a market it streams, resampled to `interval` — or None.

    The socket sends the last 500 one-minute bars every second: enough for a 5 m chart, two
    candles on a 4 h one. The engine's live frame is capped at 2,000 bars (core/market_data.py
    MAX_BARS), so the depth comes from the same ninety-day store the strategies are seeded from,
    with the live frame laid over its end — the chart then shows what the bot itself has seen.
    The venue's klines are deliberately NOT used for these markets: this frame is Binance history
    with Strike's live prints on top, and a Strike window joined onto it would meet at a seam
    between two different tapes (2026-09-04).
    """
    engine = state.engine
    if engine is None or not state.running:
        return None
    try:
        if sym not in {s.symbol for s in engine.settings.symbols}:
            return None
        secs = _INTERVAL_SEC.get(interval)
        if not secs:
            return None
        df = engine.market_data.get_dataframe(sym)
        if df is None or df.empty or "timestamp" not in df.columns:
            return None
        import pandas as pd
        col = df["timestamp"]
        if pd.api.types.is_datetime64_any_dtype(col):
            ts = col.astype("int64") / 1e9
        else:
            ts = pd.to_numeric(col, errors="coerce").astype(float)
            ts = ts.where(ts <= 1e12, ts / 1000.0)          # ms → s, like the socket loop
        live = pd.DataFrame({
            "timestamp": ts.to_numpy(),
            "open": df["open"].astype(float).to_numpy(),
            "high": df["high"].astype(float).to_numpy(),
            "low": df["low"].astype(float).to_numpy(),
            "close": df["close"].astype(float).to_numpy(),
            "volume": df["volume"].astype(float).to_numpy() if "volume" in df.columns else 0.0,
        }).dropna(subset=["timestamp"])
        store = _engine_store(sym)
        if store is not None and not live.empty:
            # the live frame owns everything from its first bar on; the store supplies the past
            older = store[store["timestamp"] < float(live["timestamp"].iloc[0])]
            live = pd.concat([older, live], ignore_index=True)
        # a bucket needs secs/60 one-minute rows; one extra covers the bucket the tail starts in
        tail = live.tail(limit * max(1, secs // 60) + secs // 60)
        frame = tail.drop(columns=["timestamp"])
        frame.index = pd.to_datetime(tail["timestamp"].to_numpy(), unit="s", utc=True)
        frame = frame[~frame.index.isna()]
        bars = frame.resample(f"{secs}s", label="left", closed="left").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        ).dropna(subset=["open"]).tail(limit)
        return [{"timestamp": float(t.timestamp()), "open": float(r.open), "high": float(r.high),
                 "low": float(r.low), "close": float(r.close),
                 "volume": float(r.volume) if r.volume == r.volume else 0.0}
                for t, r in bars.iterrows()]
    except Exception as e:  # noqa: BLE001 - the venue path below is the fallback
        logger.debug("engine_klines_error", symbol=sym, interval=interval, error=str(e)[:160])
        return None


async def _venue_kline_rows(fetch, interval: str, limit: int, now: Optional[float] = None) -> list:
    """The venue's newest `limit` bars at `interval`, walking the window forward page by page.

    The venue answers the FIRST bars after `startTime`, and caps each answer. Asked for 1,000
    daily bars in one request it returned the oldest of them — a BTC chart that ended at
    64,870 $ under a header reading 79,798 $ (2026-09-04). So the window opens `limit` bars back
    and is walked forward until it reaches the present, keeping the newest `limit` bars. A market
    that answers fewer bars than the cap is done in one request, as before.
    """
    secs = _INTERVAL_SEC.get(interval, 60)
    now = time.time() if now is None else now
    start = int((now - limit * secs) * 1000)
    rows: list = []
    seen: set = set()
    for _ in range(8):
        batch = [k for k in await fetch({"interval": interval, "limit": limit, "startTime": start})
                 if isinstance(k, list) and len(k) >= 6 and k[0] not in seen]
        if not batch:
            break
        seen.update(k[0] for k in batch)
        rows.extend(batch)
        last_ms = float(batch[-1][0])
        if len(batch) < 2 or last_ms >= (now - 2 * secs) * 1000:
            break
        start = int(last_ms + secs * 1000)
    rows.sort(key=lambda k: float(k[0]))
    return rows[-limit:]


@app.get("/api/market/{symbol}/klines")
async def get_market_klines(symbol: str, interval: str = "1m", limit: int = 500):
    """Candles for ANY market the venue lists, not only the four the engine streams.

    The chart, the depth panel and the tape were fed from the engine's own frames, and the engine
    holds four symbols — so picking any of the other 27 opened a market panel with live numbers in
    the header and an empty chart underneath (Edgar, 2026-09-04). Strike publishes klines for all 31.

    `startTime` is mandatory in practice: asked without one the venue answers from a cached window
    whose last bar was five hours old. Cached per (symbol, interval) for KLINES_TTL_SEC.
    """
    sym, iv = symbol.upper(), str(interval or "1m")
    limit = max(1, min(int(limit or 500), 1500))
    engine_bars = _engine_klines(sym, iv, limit)
    if engine_bars is not None:
        return {"symbol": sym, "interval": iv, "requested_interval": iv, "candles": engine_bars, "source": "engine"}
    key = f"{sym}:{iv}:{limit}"
    cached = _VENUE_MD.setdefault("klines", {}).get(key)
    if cached and time.time() - cached[0] < KLINES_TTL_SEC:
        return cached[1]
    out = {"symbol": sym, "interval": iv, "requested_interval": iv, "candles": [], "source": "venue"}
    import httpx
    try:
        async with httpx.AsyncClient(timeout=15.0, headers={"User-Agent": "botstrike/1.0"}) as c:

            async def fetch(params: dict) -> list:
                r = await c.get(f"{STRIKE_PRICE_BASE}/klines", params={"symbol": sym, **params})
                r.raise_for_status()
                return r.json()

            async def page(interval: str) -> list:
                return await _venue_kline_rows(fetch, interval, limit)

            # The venue writes a bar only for a period in which something TRADED, and it returns the
            # FIRST `limit` bars after startTime — so on a thin market a 1 m window holds a single
            # candle and widening it only reaches further into the past. Silver, XRP, the S&P, GOOGL
            # and NIGHT all drew a chart of one dot (Edgar, 2026-09-04).
            #
            # The honest answer is not more 1 m bars that do not exist: it is a coarser bar. At 15 m
            # silver has 283 candles over 70 hours where at 1 m it has one. So the requested interval
            # is tried first and, if the venue barely fills it, the next resolution up is tried in
            # turn. `interval` reports what actually came back, and the chart says so.
            rows = await page(iv)
            if len(rows) < limit * 0.3:
                for coarser in _COARSER.get(iv, []):
                    better = await page(coarser)
                    if len(better) > len(rows):
                        rows, out["interval"] = better, coarser
                    if len(rows) >= limit * 0.3:
                        break
        out["candles"] = [{"timestamp": float(k[0]) / 1000.0, "open": _num(k[1]), "high": _num(k[2]),
                           "low": _num(k[3]), "close": _num(k[4]), "volume": _num(k[5])}
                          for k in rows[-limit:]]
    except Exception as e:  # noqa: BLE001 - an empty chart beats a 500
        logger.debug("venue_klines_error", symbol=sym, interval=iv, error=str(e)[:160])
    _VENUE_MD["klines"][key] = (time.time(), out)
    return out


@app.get("/api/market/{symbol}/book")
async def get_market_book(symbol: str, limit: int = 20):
    """The venue's order book for ANY market, so the ladder is not blank on the 27 unstreamed ones."""
    sym = symbol.upper()
    limit = max(5, min(int(limit or 20), 50))
    key = f"{sym}:{limit}"
    cached = _VENUE_MD.setdefault("books", {}).get(key)
    if cached and time.time() - cached[0] < BOOK_TTL_SEC:
        return cached[1]
    out = {"symbol": sym, "bids": [], "asks": [], "source": "venue", "spread_bps": None}
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10.0, headers={"User-Agent": "botstrike/1.0"}) as c:
            r = await c.get(f"{STRIKE_PRICE_BASE}/depth", params={"symbol": sym, "limit": limit})
            r.raise_for_status()
            book = r.json()
        out["bids"] = [[_num(px), _num(qty)] for px, qty in (book.get("bids") or [])]
        out["asks"] = [[_num(px), _num(qty)] for px, qty in (book.get("asks") or [])]
        if out["bids"] and out["asks"]:
            bid, ask = out["bids"][0][0], out["asks"][0][0]
            if bid and ask:
                out["spread_bps"] = round((ask - bid) / ((ask + bid) / 2) * 1e4, 4)
    except Exception as e:  # noqa: BLE001
        logger.debug("venue_book_error", symbol=sym, error=str(e)[:160])
    _VENUE_MD["books"][key] = (time.time(), out)
    return out


@app.get("/api/market/{symbol}/trades")
async def get_market_trades(symbol: str, limit: int = 50):
    """The venue's recent prints for ANY market. Strike returns them newest-first; the tape reads
    them oldest-first like every other feed, so they are reversed here."""
    sym = symbol.upper()
    limit = max(1, min(int(limit or 50), 500))
    cached = _VENUE_MD.setdefault("tape", {}).get(sym)
    if cached and time.time() - cached[0] < TAPE_TTL_SEC:
        return cached[1]
    out = {"symbol": sym, "trades": [], "source": "venue"}
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10.0, headers={"User-Agent": "botstrike/1.0"}) as c:
            r = await c.get(f"{STRIKE_PRICE_BASE}/trades", params={"symbol": sym, "limit": limit})
            r.raise_for_status()
            rows = r.json()
        trades = [{"price": _num(t.get("price")), "quantity": _num(t.get("qty")),
                   "timestamp": float(t.get("time") or 0) / 1000.0,
                   "side": "sell" if t.get("isBuyerMaker") else "buy"}
                  for t in rows if isinstance(t, dict)]
        trades.sort(key=lambda t: t["timestamp"])
        out["trades"] = trades
    except Exception as e:  # noqa: BLE001
        logger.debug("venue_trades_error", symbol=sym, error=str(e)[:160])
    _VENUE_MD["tape"][sym] = (time.time(), out)
    return out


_VENUE_MD: dict = {"ts": 0.0, "ts_tick": 0.0, "ts_info": 0.0,
                   "premium": {}, "ticker": {}, "filters": {}, "depth": {}}
# The mark moves every second and the operator reads this header beside the venue's own screen, so
# the premium index is refreshed on roughly the venue's cadence. The 24 h block moves slowly and the
# order filters change about never, so they get their own, far longer, TTLs instead of dragging two
# extra requests along on every refresh (2026-09-04).
VENUE_MD_TTL_SEC = 5.0
VENUE_TICKER_TTL_SEC = 20.0
VENUE_INFO_TTL_SEC = 900.0
VENUE_DEPTH_TTL_SEC = 4.0
STRIKE_PRICE_BASE = os.getenv("BOTSTRIKE_STRIKE_PRICE", "https://api.strikefinance.org/price/v2")


async def _fetch_venue_market_data(parts: tuple = ("premium", "ticker", "filters")) -> dict:
    """Mark, index, 24 h stats and the venue's own order filters for EVERY market it lists.

    The terminal only streams four symbols, so picking any other market showed a panel of "---" even
    though the book holds four of them and the venue publishes all of it publicly (Edgar, 2026-09-04).
    Patched in tests. Each part is cached on its own TTL; `parts` says which to actually go and get.
    """
    import httpx
    out = {}
    async with httpx.AsyncClient(timeout=15.0, headers={"User-Agent": "botstrike/1.0"}) as c:
        async def _skip():
            return None
        prem, tick, info = await asyncio.gather(
            c.get(f"{STRIKE_PRICE_BASE}/premiumIndex") if "premium" in parts else _skip(),
            c.get(f"{STRIKE_PRICE_BASE}/ticker/24hr") if "ticker" in parts else _skip(),
            c.get(f"{STRIKE_PRICE_BASE}/exchangeInfo") if "filters" in parts else _skip(),
            return_exceptions=True)
    for key in parts:
        out[key] = {}
    for resp, key in ((prem, "premium"), (tick, "ticker")):
        if resp is None or isinstance(resp, Exception) or resp.status_code != 200:
            continue
        for row in resp.json():
            sym = str(row.get("symbol", "")).upper()
            if sym:
                out[key][sym] = row
    if info is not None and not isinstance(info, Exception) and info.status_code == 200:
        for sm in (info.json().get("symbols") or []):
            sym = str(sm.get("symbol", "")).upper()
            filt = {f.get("filterType"): f for f in (sm.get("filters") or []) if isinstance(f, dict)}
            # Everything the venue states about how an order on this market must look. The panel
            # showed a hard-coded $20 minimum against Strike's real $10 until 2026-09-04; these are
            # the rest of the rules it publishes and nothing was reading — the cap on a market order
            # in particular is a real constraint on sizing, not a curiosity.
            out["filters"][sym] = {
                "tick_size": _num(filt.get("PRICE_FILTER", {}).get("tickSize")),
                "step_size": _num(filt.get("LOT_SIZE", {}).get("stepSize")),
                "min_qty": _num(filt.get("LOT_SIZE", {}).get("minQty")),
                "max_qty": _num(filt.get("LOT_SIZE", {}).get("maxQty")),
                "market_max_qty": _num(filt.get("MARKET_LOT_SIZE", {}).get("maxQty")),
                "min_notional": _num((filt.get("MIN_NOTIONAL") or filt.get("NOTIONAL") or {}).get("notional")),
                "min_price": _num(filt.get("PRICE_FILTER", {}).get("minPrice")),
                "max_price": _num(filt.get("PRICE_FILTER", {}).get("maxPrice")),
                "liquidation_fee": _num(sm.get("liquidationFee")),
                "price_precision": sm.get("pricePrecision"),
                "qty_precision": sm.get("quantityPrecision"),
                "margin_asset": sm.get("marginAsset"),
                "status": sm.get("status"),
            }
    return out


def _venue_spread_bps(symbol: str):
    """The market's measured median spread, so a market with no live book still shows what it costs."""
    from strategies.trend_daily import _venue_half_spread_bps
    half = _venue_half_spread_bps(symbol)
    return round(half * 2, 3) if half is not None else None


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


async def _venue_open_interest(symbol: str):
    """Latest open interest from the venue's stats API — the premium index does not carry it."""
    cached = _VENUE_MD.setdefault("oi", {}).get(symbol.upper())
    if cached and time.time() - cached[0] < 300:
        return cached[1]
    import httpx
    try:
        async with httpx.AsyncClient(timeout=12.0, headers={"User-Agent": "botstrike/1.0"}) as c:
            r = await c.get(f"{STRIKE_STATS_BASE}/history/open-interest",
                            params={"symbol": symbol, "interval": "1d"})
            r.raise_for_status()
            payload = r.json()
        rows = payload.get("data") or []
        cols = payload.get("columns") or []
        if not rows:
            return None
        last = dict(zip(cols, rows[-1]))
        # `a or b` treats a genuine zero as absent, and four of the venue's markets really do carry
        # no open interest — they showed "---" as if the venue had not answered (audit 2026-09-04).
        val = None
        for key in ("open_interest", "openInterest", "value", "oi"):
            if key in last:
                val = _num(last[key])
                break
    except Exception as e:  # noqa: BLE001
        logger.debug("venue_open_interest_error", symbol=symbol, error=str(e)[:120])
        val = None
    _VENUE_MD["oi"][symbol.upper()] = (time.time(), val)
    return val


def _md_lock() -> "asyncio.Lock":
    """One refresh at a time. The terminal fires several market requests per second (measured on the
    CT), and without this every one of them that arrived on an expired TTL would open its own
    connection to the venue — a stampede that gets slower the busier the screen is."""
    lock = _VENUE_MD.get("lock")
    if lock is None:
        lock = _VENUE_MD["lock"] = asyncio.Lock()
    return lock


async def _venue_open_interest_loop():
    """Keep every market's open interest warm, so the picker shows the venue's own figure.

    Open interest has no bulk endpoint — it is one stats call per market — so a request handler can
    neither afford 31 of them nor serve "---" forever. One sweep every five minutes costs 0.1 req/s
    and makes the column true for all 31 markets instead of Binance's for four (2026-09-04).
    """
    while True:
        try:
            md = await _venue_market_data()
            syms = sorted(md["premium"]) or sorted(getattr(state.engine.settings, "symbol_names", []) or [])
            sem = asyncio.Semaphore(6)

            async def one(sym):
                async with sem:
                    await _venue_open_interest(sym)

            await asyncio.gather(*(one(s) for s in syms), return_exceptions=True)
        except Exception as e:  # noqa: BLE001 - a display figure must never kill the loop
            logger.debug("venue_oi_loop_error", error=str(e)[:160])
        await asyncio.sleep(270)     # inside the 300 s entry TTL, so a sweep always refreshes


async def _venue_market_data() -> dict:
    """The venue's own view of every market, each part refreshed on the cadence it deserves."""
    now = time.time()
    parts = []
    if now - float(_VENUE_MD["ts"]) >= VENUE_MD_TTL_SEC or not _VENUE_MD["premium"]:
        parts.append("premium")
    if now - float(_VENUE_MD.get("ts_tick") or 0.0) >= VENUE_TICKER_TTL_SEC or not _VENUE_MD["ticker"]:
        parts.append("ticker")
    if now - float(_VENUE_MD.get("ts_info") or 0.0) >= VENUE_INFO_TTL_SEC or not _VENUE_MD["filters"]:
        parts.append("filters")
    if not parts:
        return _VENUE_MD
    async with _md_lock():
        now = time.time()                           # whoever held the lock may have just refreshed
        parts = [p for p, stamp, ttl in (("premium", "ts", VENUE_MD_TTL_SEC),
                                         ("ticker", "ts_tick", VENUE_TICKER_TTL_SEC),
                                         ("filters", "ts_info", VENUE_INFO_TTL_SEC))
                 if p in parts and (now - float(_VENUE_MD.get(stamp) or 0.0) >= ttl or not _VENUE_MD[p])]
        if not parts:
            return _VENUE_MD
        try:
            fresh = await _fetch_venue_market_data(tuple(parts))
            for key, stamp in (("premium", "ts"), ("ticker", "ts_tick"), ("filters", "ts_info")):
                if fresh.get(key):                  # a stale cache beats an empty panel
                    _VENUE_MD[key] = fresh[key]
                    _VENUE_MD[stamp] = now
        except Exception as e:  # noqa: BLE001 — a stale cache beats an empty panel
            logger.debug("venue_market_data_error", error=str(e))
    return _VENUE_MD


async def _venue_depth(symbol: str):
    """Top of the VENUE's book, which is the book an order would actually cross.

    The terminal streams Binance, so the header was quoting Binance's spread on a Strike market:
    0.012 bps for BTC where Strike's own screen says 0.001 % (0.09 bps), and 4.5 bps for ADA where
    Strike's book is 6.3 wide (audit 2026-09-04). Half a spread is what a market order pays, so this
    is not cosmetic. One symbol per call, cached briefly.
    """
    sym = symbol.upper()
    cached = _VENUE_MD.setdefault("depth", {}).get(sym)
    if cached and time.time() - cached[0] < VENUE_DEPTH_TTL_SEC:
        return cached[1]
    import httpx
    out = None
    try:
        async with httpx.AsyncClient(timeout=8.0, headers={"User-Agent": "botstrike/1.0"}) as c:
            r = await c.get(f"{STRIKE_PRICE_BASE}/depth", params={"symbol": sym, "limit": 5})
            r.raise_for_status()
            book = r.json()
        bid, ask = _num((book.get("bids") or [[None]])[0][0]), _num((book.get("asks") or [[None]])[0][0])
        if bid and ask and ask > 0 and bid > 0:
            mid = (bid + ask) / 2.0
            out = {"best_bid": bid, "best_ask": ask, "spread_bps": round((ask - bid) / mid * 1e4, 4)}
    except Exception as e:  # noqa: BLE001 - the measured median is the fallback
        logger.debug("venue_depth_error", symbol=sym, error=str(e)[:120])
    _VENUE_MD["depth"][sym] = (time.time(), out)
    return out


def _market_slippage_bps(engine, symbol: str) -> float:
    """Half the market's measured spread on the venue, floored at the configured default."""
    from strategies.trend_daily import _venue_half_spread_bps
    base = float(getattr(engine.settings.trading, "slippage_bps", 1.5) or 1.5)
    half = _venue_half_spread_bps(symbol)
    return round(max(base, half) if half is not None else base, 3)


def _strategies_on(engine, symbol: str, sc=None) -> list:
    """Strategies that would actually trade this market — never one the product has retired.

    The panel listed Fibonacci and Divergence — both disabled — for a market whose open position
    belongs to TREND_DAILY, which was missing entirely (audit 2026-09-03). It then kept advertising
    MEAN_REVERSION and DIVERGENCE on ETH and ADA and FIBONACCI_RETRACEMENT on BTC after all three
    were retired with evidence (audit 2026-09-04): a per-symbol config row outlives the strategy it
    names, so the list is filtered against what the product still runs, not against the config file.
    Membership comes from the trend UNIVERSE, not only from today's open positions — a market the
    daily run rebalances is traded by TREND_DAILY whether or not it happens to be flat right now.
    """
    from core.types import RETIRED_STRATEGIES
    out = [s for s in str(getattr(sc, "strategies", "") if sc is not None else "").split(",")
           if s and s not in RETIRED_STRATEGIES]
    try:
        status = getattr(engine, "trend_engine", None).status()
        want = {symbol.upper()}
        universe = set()
        for u in (status.get("universe") or []):
            u = str(u).upper()
            universe.add(u if "-" in u else (u[:-4] + "-USD" if u.endswith("USDT") else u))
        held = {str(r.get("ui_symbol") or r.get("symbol") or "").upper()
                for r in (status.get("positions") or [])}
        if (universe | held) & want and "TREND_DAILY" not in out:
            out.insert(0, "TREND_DAILY")
    except Exception:  # noqa: BLE001 - a label must never break the market endpoint
        pass
    return out


def _funding_interval(engine) -> int:
    """The venue's settlement cadence in hours, as the engine is configured to charge it."""
    try:
        return max(1, int(getattr(engine.settings.trading, "funding_interval_hours", 1) or 1))
    except Exception:  # noqa: BLE001
        return 1


def _market_funding_rate(engine, symbol: str, snap):
    """The rate THIS market is charged at: the venue's, scaled feed only where the venue is silent.

    The market panel used to read the feed snapshot raw, so it showed Binance's 8 h rate (+0.0100 %)
    for a book charged Strike's hourly rate (+0.00116 %) — a factor of nine (audit 2026-09-03).
    """
    # ONE NUMBER, ONE SOURCE. The engine keeps its own copy of the venue rates, refreshed once a
    # minute; the bridge refreshes the same field from the same endpoint every five seconds. Reading
    # one here and the other in the picker put two ages of the same quote on one screen — the panel
    # said -0.008971 % and the list -0.008986 % for oil at the same instant (audit 2026-09-04). The
    # freshest wins everywhere, and the engine's copy is what settles the charge, which is a
    # different event and already recorded in the funding ledger.
    prem = (_VENUE_MD["premium"] or {}).get(symbol.upper()) or {}
    if prem.get("fundingRate") is not None:
        return _num(prem.get("fundingRate"))
    venue = getattr(engine, "_venue_funding", None) or {}
    if symbol in venue:
        return float(venue[symbol] or 0.0)
    if snap is not None and snap.funding_rate:
        return float(snap.funding_rate) * _funding_interval(engine) / 8.0
    return None


def _symbol_config_view(engine, symbol: str) -> dict:
    try:
        sc = engine.settings.get_symbol_config(symbol)
        t = engine.settings.trading
        filt = (_VENUE_MD["filters"] or {}).get(symbol.upper()) or {}
        return {"leverage": int(getattr(sc, "leverage", 1)), "max_position_usd": float(getattr(sc, "max_position_usd", 0.0)),
                # the venue's own minimum when we know it, not a hard-coded 20
                "min_notional_usd": filt.get("min_notional") or 20.0,
                "tick_size": filt.get("tick_size"), "step_size": filt.get("step_size"),
                "strategies": _strategies_on(engine, symbol, sc),
                # what THIS market's book actually costs to cross, not the global default
                "slippage_bps": _market_slippage_bps(engine, symbol),
                "taker_fee": float(getattr(t, "taker_fee", 0.0004)), "maker_fee": float(getattr(t, "maker_fee", 0.0002)),
                "maintenance_margin": 0.005, "max_leverage": int(getattr(t, "max_leverage", 5)),
                "risk_per_trade_pct": float(getattr(t, "risk_per_trade_pct", 0.0))}
    except Exception:  # noqa: BLE001
        # Gold, silver, the S&P and oil have no per-symbol config — only the four intraday feed
        # symbols do — so this returned {} and their panel showed "---" for every field, on the very
        # markets the book is holding. Fall back to the account-level values, which are what actually
        # applies to them (2026-09-04).
        try:
            t = engine.settings.trading
            filt = (_VENUE_MD["filters"] or {}).get(symbol.upper()) or {}
            # A market with no per-symbol row has NO fixed cap and NO fixed leverage: the daily run
            # sizes it by volatility up to trend_leverage_cap. Reporting 0.0 and 1 printed "$0" and
            # "1x" on gold and the S&P as if they were configured that way (audit 2026-09-04).
            return {"leverage": float(getattr(t, "trend_leverage_cap", 2.0)), "max_position_usd": None,
                    "min_notional_usd": filt.get("min_notional") or float(t.trend_min_order_usd),
                    "tick_size": filt.get("tick_size"), "step_size": filt.get("step_size"),
                    "strategies": _strategies_on(engine, symbol, None),
                    "slippage_bps": _market_slippage_bps(engine, symbol),
                    "taker_fee": float(getattr(t, "taker_fee", 0.0004)),
                    "maker_fee": float(getattr(t, "maker_fee", 0.0002)),
                    "maintenance_margin": 0.005, "max_leverage": int(getattr(t, "max_leverage", 5)),
                    "risk_per_trade_pct": float(getattr(t, "risk_per_trade_pct", 0.0)),
                    "account_level": True}
        except Exception:  # noqa: BLE001
            return {}


@app.post("/api/positions/close", dependencies=[Depends(require_token_when_remote)])
async def close_position(body: dict):
    """Operator override: close ONE open position now, at the current price.

    The trend book normally exits through its trailing ladder (GET /api/positions ->
    `exit_ladder`), and intraday strategies through their own stop/target; this is the manual
    brake. Paper only for now: in live mode the venue client must place the reduce-only order,
    which is gated behind the canary (roadmap P2).
    """
    engine = state.engine
    if not engine:
        raise HTTPException(status_code=409, detail="engine not running")
    symbol = str((body or {}).get("symbol") or "").upper()
    if not symbol:
        raise HTTPException(status_code=400, detail="symbol required")
    if not getattr(engine, "paper", True):
        raise HTTPException(status_code=409, detail="manual close is paper-only until the live canary lands")
    out = {"symbol": symbol, "closed": False}
    sim = getattr(engine, "paper_sim", None)
    if sim is not None and hasattr(sim, "close_symbol"):
        try:
            res = sim.close_symbol(symbol, reason="manual")
            if res:
                out.update({"closed": True, "source": "paper", "detail": res})
        except Exception as e:  # noqa: BLE001
            logger.warning("manual_close_paper_failed", symbol=symbol, error=str(e))
    if not out["closed"]:
        trend = getattr(engine, "trend_engine", None)
        if trend is not None:
            res = await trend.close_symbol(symbol, reason="manual")
            out.update({"closed": bool(res.get("closed")), "source": "trend", "detail": res})
    if not out["closed"]:
        raise HTTPException(status_code=404, detail=f"no open position for {symbol}")
    try:
        get_activity_log().add("fill", f"Manual close {symbol}", "closed by the operator from the UI",
                               symbol=symbol, level="warning")
    except Exception:  # noqa: BLE001
        pass
    logger.warning("position_closed_manually", symbol=symbol, source=out.get("source"))
    return _json_safe(out)


@app.post("/api/trend/run", dependencies=[Depends(require_token_when_remote)])
async def trend_run_now():
    """Operator button: execute today's daily decision now (data refresh + rebalance).
    Idempotent per day: a second call the same day re-evaluates but only trades if
    the targets changed beyond the rebalance threshold."""
    engine = state.engine
    trend = getattr(engine, "trend_engine", None) if engine else None
    if trend is None:
        raise HTTPException(status_code=409, detail="trend engine not running (paper mode only)")
    if not trend.enabled:
        raise HTTPException(status_code=409, detail="trend daily is disabled (allocation 0)")
    result = await trend.run_once()
    return {"result": result, "status": trend.status()}


@app.get("/api/regime")
async def get_regime():
    """Confirmed/candidate regime per symbol with the inputs behind it (15-min bars + dwell)."""
    engine = state.engine
    if not engine:
        return {"symbols": {}}
    det = engine.regime_detector
    return {"symbols": {sym: det.status(sym) for sym in engine.settings.symbol_names},
            "timeframe_min": det.params()[0], "min_dwell_min": det.params()[1]}


@app.get("/api/risk")
async def get_risk():
    engine = state.engine
    if not engine or not hasattr(engine, "risk_snapshot"):
        s = _config_settings()
        return {"engine": False, "max_drawdown_pct": s.trading.max_drawdown_pct,
                "max_daily_loss_pct": s.trading.max_daily_loss_pct,
                "max_weekly_loss_pct": s.trading.max_weekly_loss_pct,
                "compounding_enabled": s.trading.compounding_enabled}
    snap = engine.risk_snapshot()
    snap["engine"] = True
    # The account limits ride on the WS risk message but not on this one, so the Risk page could not
    # show its exposure CAP until the socket delivered them — seconds of a panel with a total and no
    # budget beside it (2026-09-04). REST answers the same question now.
    try:
        snap["account"] = _account_overview(engine)
    except Exception as e:  # noqa: BLE001 - the snapshot must survive a missing overview
        logger.debug("risk_account_overview_error", error=str(e))
    return _json_safe(snap)


@app.post("/api/bot/start", dependencies=[Depends(require_token_when_remote)])
async def bot_start(mode: str = "paper", exchange: str = "binance",
                    supplied: str = Depends(supplied_token)):
    # Auth: live always needs the token (any bind); other modes only on non-loopback (dependency)
    if mode == "live" and not _token_ok(supplied):
        raise HTTPException(status_code=401, detail="Invalid or missing auth token for live mode")
    # Deploy-level kill switch: a valid token is NOT enough to trade real money
    if mode == "live" and not _ALLOW_LIVE:
        raise HTTPException(
            status_code=403,
            detail="live trading disabled on this host (set BOTSTRIKE_ALLOW_LIVE=1 to enable)")
    if mode not in VALID_MODES:
        raise HTTPException(status_code=400, detail=f"Invalid mode: {mode!r}. Valid: {sorted(VALID_MODES)}")
    if exchange not in VALID_EXCHANGES:
        raise HTTPException(status_code=400, detail=f"Invalid exchange: {exchange!r}")
    if state.running:
        return {"status": "already_running", "mode": state.mode}

    # Apply exchange-specific fee configuration and hand it to the engine
    settings = _build_settings(exchange)
    state.exchange = exchange

    await start_engine(mode, settings=settings)
    return {"status": "starting", "mode": mode, "exchange": exchange}


@app.post("/api/bot/stop", dependencies=[Depends(require_token_when_remote)])
async def bot_stop(supplied: str = Depends(supplied_token)):
    if not state.running:
        return {"status": "not_running"}
    # Live always needs the token to stop (any bind); paper on loopback stops without it
    if state.mode == "live" and not _token_ok(supplied):
        raise HTTPException(status_code=401, detail="Invalid or missing auth token to stop live trading")
    await stop_engine(manual=True)
    return {"status": "stopped"}


@app.get("/api/bot/status")
async def bot_status():
    return {
        "running": state.running,
        "mode": state.mode,
        "uptime_sec": time.time() - state.start_time if state.running else 0,
        "equity": state.equity,
        "pnl": state.pnl,
        "auth_token": _AUTH_TOKEN if _EXPOSE_TOKEN else None,
        "auth_token_exposed": _EXPOSE_TOKEN,
        "exchange": state.exchange,
    }


@app.get("/api/performance")
async def get_performance():
    """Merged all-time performance (trade DB) + live unrealized. Same source as
    the WS metrics broadcast, so every UI surface shows the same numbers."""
    p = _merged_performance()
    if p is None:
        return {"error": "Engine not started"}
    pts = p.get("equity_curve_ts") or []
    # Legacy flat list kept for older clients; new clients use equity_curve_ts
    p["equity_curve"] = [v for _, v in pts] if pts else [p["initial_capital"]]
    return p


_STRATEGY_NAMES = {
    StrategyType.MEAN_REVERSION: "Mean Reversion",
    StrategyType.FIBONACCI_RETRACEMENT: "Fibonacci retracement",
    StrategyType.TREND_DAILY: "Trend daily (Donchian ensemble)",
    StrategyType.DIVERGENCE: "Divergence (RSI + structure break)",
    StrategyType.TREND_FOLLOWING: "Trend following (archived)",
    StrategyType.MARKET_MAKING: "Market making (archived)",
    StrategyType.ORDER_FLOW_MOMENTUM: "Order flow momentum (archived)",
}


def _strategy_view(settings: Settings, st: StrategyType) -> dict:
    """Description + params generated from the LIVE configuration (never a stale string)."""
    tc = settings.trading
    symbols = [s.symbol for s in settings.symbols
               if st.value in [x.strip().upper() for x in str(s.strategies).split(",")]]
    if st == StrategyType.TREND_DAILY:
        params = {"lookbacks": tc.trend_lookbacks, "target_vol": tc.trend_target_vol,
                  "vol_window": tc.trend_vol_window, "n_assets": tc.trend_n_assets,
                  "leverage_cap": tc.trend_leverage_cap, "rebalance_threshold": tc.trend_rebalance_threshold,
                  "execution_hour_utc": tc.trend_execution_hour_utc, "min_order_usd": tc.trend_min_order_usd}
        # The selection rule depends on the pool. Describing a mixed pool as "top-N by 30d volume"
        # advertised the rule that was REMOVED for being meaningless across asset classes (an index
        # reports its constituents' summed share volume, a metal reports contracts) — audit 2026-09-03.
        from strategies.trend_daily_model import asset_class
        pool = [x.strip().upper() for x in str(getattr(tc, "trend_pool", "") or "").split(",") if x.strip()]
        classes = {asset_class(x) for x in pool}
        rule = ("%d markets: one per asset class, longest history, correlation cap, venue liquidity floor"
                % tc.trend_n_assets if len(classes) > 1
                else "top-%d by 30d volume" % tc.trend_n_assets)
        desc = (f"Daily Donchian ensemble {tc.trend_lookbacks} · long-only · vol target "
                f"{tc.trend_target_vol:.0%} ({tc.trend_vol_window}d) · {rule} · "
                f"signal at close, executed at {tc.trend_execution_hour_utc:02d}:00 UTC open + "
                f"{tc.trend_execution_delay_min} min")
        return {"description": desc, "params": params, "group": "trend_daily",
                "symbols": [f"universe re-picked monthly and whenever the pool or size changes "
                            f"({len(pool)} candidates, {'/'.join(sorted(classes))})"]}
    if st == StrategyType.DIVERGENCE:
        params = {"timeframe_min": tc.div_timeframe_min, "rsi_period": tc.div_rsi_period, "pivot_k": tc.div_pivot_k,
                  "rsi_os": tc.div_rsi_os, "rsi_ob": tc.div_rsi_ob, "min_rsi_gap": tc.div_min_rsi_gap,
                  "trigger_window": tc.div_trigger_window, "require_macd": tc.div_require_macd,
                  "atr_buffer": tc.div_atr_buffer, "rr": tc.div_rr, "max_hold": tc.div_max_hold,
                  "hidden": tc.div_hidden, "with_trend": tc.div_with_trend}
        tf = int(tc.div_timeframe_min)
        tf_label = f"{tf // 60}h" if tf >= 60 else f"{tf}m"
        desc = (f"RSI{tc.div_rsi_period} {'hidden' if tc.div_hidden else 'regular'} divergence on {tf_label} bars · "
                f"pivots ±{tc.div_pivot_k} · first pivot RSI <{tc.div_rsi_os:g}/>{tc.div_rsi_ob:g} · "
                f"trigger = close beyond the pivot bar within {tc.div_trigger_window} bars"
                f"{' + MACD' if tc.div_require_macd else ''} · stop pivot ∓{tc.div_atr_buffer:g}×ATR · TP {tc.div_rr:g}R · "
                f"time stop {tc.div_max_hold} bars · RESEARCH: 1h NO-GO, and confirmed dead OUT OF SAMPLE "
                f"on 6 markets it had never seen (gross −1.2 bps over 1,136 trades). The 4h variant is the only "
                f"line still alive: gross +50.4 bps, net +34.4 bps over 323 out-of-sample trades, t +1.09 — "
                f"unproven, not dead (the bar is t ≥ 2)")
        return {"description": desc, "params": params, "symbols": symbols, "group": "divergence",
                # Updated 2026-09-04 with the out-of-sample run (LTC, DOGE, LINK, AVAX, DOT, ATOM):
                # the hypothesis the first study singled out — hidden divergences — DIED there
                # (PF 0.84, gross -13.3 bps, t -1.21 over 1,347 trades). Only 4h has a pulse.
                "research": {"verdict": "UNPROVEN", "checks": "2/7", "trades": 1136, "profit_factor": 0.91,
                             "t_stat": -0.08,
                             "note": "1h dead out of sample (gross -1.2 bps); hidden divergences DIED out of sample "
                                     "(t -1.21); only 4h has positive gross AND net out of sample (+50.4 / +34.4 bps, "
                                     "t +1.09) and needs ~1,000 trades, i.e. more symbols, to settle"}}
    if st == StrategyType.MEAN_REVERSION:
        ref = next((s for s in settings.symbols if s.symbol in symbols), settings.symbols[0])
        params = {"zscore_entry": ref.mr_zscore_entry, "zscore_exit": ref.mr_zscore_exit,
                  "lookback_bars": ref.mr_lookback, "stop_atr": ref.mr_atr_mult_sl,
                  "take_profit_atr": ref.mr_atr_mult_tp, "regimes": "RANGING only"}
        desc = (f"1m z-score reversion · entry |z| > {ref.mr_zscore_entry:g} · exit |z| < {ref.mr_zscore_exit:g} · "
                f"stop {ref.mr_atr_mult_sl:g}×ATR · TP {ref.mr_atr_mult_tp:g}×ATR · RANGING regime only · "
                f"{', '.join(symbols) or 'no symbol'}")
        return {"description": desc, "params": params, "symbols": symbols, "group": "symbols"}
    if st == StrategyType.FIBONACCI_RETRACEMENT:
        desc = (f"15m impulse–retracement at the 50–61.8% zone · trending regimes only · "
                f"{', '.join(symbols) or 'no symbol'}")
        return {"description": desc, "params": {"regimes": "TRENDING_UP / TRENDING_DOWN"}, "symbols": symbols,
                "group": "symbols"}
    return {"description": "archived", "params": {}, "symbols": [], "group": "strategies"}


@app.get("/api/strategies")
async def get_strategies():
    settings = _config_settings()
    engine = state.engine
    killed = {}
    if engine is not None:
        killed = {k.value: v for k, v in getattr(engine.portfolio_manager, "killed", {}).items()}
        trend = getattr(engine, "trend_engine", None)
        if trend is not None and getattr(trend, "killed", False):
            killed.setdefault(StrategyType.TREND_DAILY.value, "edge monitor")
    edge = (getattr(engine, "edge_stats", None) or {}).get("strategies", {}) if engine else {}
    # Retired strategies are not offered. Greyed-out cards took space and suggested that one day
    # someone would switch them on; they have no gross edge, so that day is not coming. The record
    # travels in `retired` so the page can state the verdict once, in a line (Edgar, 2026-09-04).
    from core.types import RETIRED_STRATEGIES
    order = [st for st in (StrategyType.TREND_DAILY, StrategyType.DIVERGENCE,
                           StrategyType.MEAN_REVERSION, StrategyType.FIBONACCI_RETRACEMENT)
             if st.value not in RETIRED_STRATEGIES]
    strategies = []
    for st in order:
        alloc = strategy_allocation(settings.trading, st)
        view = _strategy_view(settings, st)
        is_killed = st.value in killed
        strategies.append({
            "type": st.value,
            "name": _STRATEGY_NAMES.get(st, st.value),
            "enabled": alloc > 0,
            "active": alloc > 0 and not is_killed and engine is not None,
            "allocation": alloc,
            "killed": is_killed,
            "kill_reason": killed.get(st.value, ""),
            "description": view["description"],
            "params": view["params"],
            "symbols": view["symbols"],
            "settings_group": view["group"],
            "edge": edge.get(st.value),
            "research": view.get("research"),
        })
    return {"strategies": strategies,
            # Only the ones that were once offered as real options; the three archived engines never
            # were, so listing them would be noise rather than a record.
            "retired": [{"type": k, "name": _STRATEGY_NAMES.get(StrategyType(k), k), "reason": v}
                        for k, v in RETIRED_STRATEGIES.items()
                        if k in (StrategyType.MEAN_REVERSION.value, StrategyType.FIBONACCI_RETRACEMENT.value,
                                 StrategyType.DIVERGENCE.value)]}


def _iso_utc(ts: float) -> str:
    """TZ-aware ISO (…+00:00) so browsers in any timezone render the correct local time."""
    import datetime as _dt
    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).isoformat()


def _trade_row(r) -> dict:
    """One /api/trades row (also used by the CSV export). Exit rows carry entry/exit times derived
    from duration_sec; exit_reason is derived from the order id prefix."""
    entry_ts = float(r.timestamp) if r.timestamp else 0.0
    exit_ts = 0.0
    entry_time = _iso_utc(entry_ts) if entry_ts else None
    exit_time = None
    if getattr(r, "trade_type", None) == "EXIT" and getattr(r, "duration_sec", None) and getattr(r, "duration_sec", None) > 0:
        exit_ts = entry_ts
        entry_ts = exit_ts - getattr(r, "duration_sec", None)
        exit_time = _iso_utc(exit_ts)
        entry_time = _iso_utc(entry_ts)

    oid = getattr(r, "order_id", None) or ""
    if oid.startswith("paper_sl"):
        exit_reason = "SL"
    elif oid.startswith("paper_tp"):
        exit_reason = "TP"
    elif oid.startswith("paper_exit"):
        exit_reason = "signal"
    elif oid.startswith("paper_close"):
        exit_reason = "close"
    elif oid.startswith("trend_exit"):
        exit_reason = "trend_exit"
    elif oid.startswith("trend_rebalance"):
        exit_reason = "rebalance"
    else:
        exit_reason = ""
    entry_px = getattr(r, "entry_price", None) or r.price
    notional = float(entry_px or 0) * float(r.quantity or 0)
    lev = 1
    try:
        lev = int(state.engine.settings.get_symbol_config(r.symbol).leverage) if r.strategy != "TREND_DAILY" else 1
    except Exception:
        lev = 1
    margin = notional / max(lev, 1)
    return {
        "id": getattr(r, "id", 0) or 0,
        "trade_id": getattr(r, "trade_id", ""),
        "symbol": r.symbol,
        "side": r.side,
        "trade_type": getattr(r, "trade_type", None) or "",
        "strategy": r.strategy,
        "entry_price": entry_px,
        "exit_price": getattr(r, "exit_price", None) or (r.price if getattr(r, "trade_type", None) == "EXIT" else 0),
        "quantity": r.quantity,
        "pnl": r.pnl,
        "fee": r.fee,
        "pnl_bps": (r.pnl / notional * 1e4) if notional > 0 else 0.0,
        "roe_pct": (r.pnl / margin) if margin > 0 else 0.0,
        "leverage": lev,
        "mae_bps": getattr(r, "mae_bps", None) or 0.0,
        "mfe_bps": getattr(r, "mfe_bps", None) or 0.0,
        "slippage_bps": getattr(r, "slippage_bps", None) or 0.0,
        "order_type": getattr(r, "order_type", None) or "",
        "exit_reason": exit_reason,
        "duration_sec": getattr(r, "duration_sec", None) or 0,
        "hold_sec": getattr(r, "duration_sec", None) or 0,
        "entry_time": entry_time,
        "exit_time": exit_time,
        "entry_ts": entry_ts,   # epoch seconds (UTC) — chart markers
        "exit_ts": exit_ts,     # 0 when still open / ENTRY record
        "regime": getattr(r, "regime", None) or "",
        "equity_after": getattr(r, "equity_after", None) or 0.0,
        "signal_strength": getattr(r, "signal_strength", None) or 0.0,
        "spread_bps": getattr(r, "spread_bps", None) or 0.0,
        "order_id": oid,
    }


@app.get("/api/trades")
async def get_trades(limit: int = 100):
    if not state.engine:
        return {"trades": []}
    try:
        # newest_first: `limit` must keep the LAST N trades, not the first N
        # (audit R2 persistence-02). The rows still arrive chronologically.
        #
        # `limit` counts FILLS. Funding settles hourly on every open market — six positions write
        # 144 rows a day — so a limit shared with them would soon hold a day or two of carry and
        # no trades at all (2026-09-05). The settlements are still returned: those that fall inside
        # the window the fills span, so Order History shows the carry next to the fills it belongs to.
        repo = state.engine.trade_repo
        records = repo.get_trades(source="paper", limit=limit, newest_first=True, exclude_trade_type="FUNDING")
        if records:
            oldest = min(float(getattr(r, "timestamp", 0.0) or 0.0) for r in records)
            records = records + repo.get_trades(source="paper", trade_type="FUNDING", start_time=oldest, newest_first=True)
            records.sort(key=lambda r: float(getattr(r, "timestamp", 0.0) or 0.0))
        trades = [_trade_row(r) for r in records]
        # Return most recent first
        trades.reverse()
        return {"trades": trades}
    except Exception as e:
        logger.debug("trades_api_error", error=str(e))
        return {"trades": []}


@app.get("/api/data/catalog")
async def get_data_catalog():
    # Try multiple paths (project dir, cwd, exe dir)
    candidates = [
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "catalog.json"),
        os.path.join(os.getcwd(), "data", "catalog.json"),
    ]
    for catalog_path in candidates:
        try:
            if os.path.exists(catalog_path):
                with open(catalog_path, "r") as f:
                    return json.load(f)
        except Exception:
            continue

    # Build catalog from binance klines if available
    data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "binance", "klines")
    if not os.path.exists(data_dir):
        data_dir = os.path.join(os.getcwd(), "data", "binance", "klines")
    now = time.time()
    if _catalog_cache["data"] is not None and now - _catalog_cache["ts"] < 300:
        return _catalog_cache["data"]
    datasets = await asyncio.to_thread(_scan_catalog, data_dir)
    # Daily spot klines used by TREND_DAILY (data/binance_daily/<SYM>.parquet)
    daily_dir = os.path.join(os.path.dirname(data_dir.rstrip("/\\")), "..", "binance_daily")
    daily_dir = os.path.normpath(daily_dir)
    if os.path.isdir(daily_dir):
        datasets += await asyncio.to_thread(_scan_flat_catalog, daily_dir, "1d")
    result = {"datasets": datasets}
    _catalog_cache["ts"], _catalog_cache["data"] = now, result
    return result


_catalog_cache: Dict[str, object] = {"ts": 0.0, "data": None}


def _parquet_meta(fpath: str) -> tuple:
    """(records, date_range) read from the parquet itself — no more hardcoded zeros."""
    records, date_range = 0, ""
    try:
        import pyarrow.parquet as pq
        pf = pq.ParquetFile(fpath)
        records = int(pf.metadata.num_rows)
        names = pf.schema_arrow.names
        col = next((c for c in ("timestamp", "open_time", "time", "date", "ts") if c in names), None)
        if col is None and pf.schema_arrow.pandas_metadata:
            idx = pf.schema_arrow.pandas_metadata.get("index_columns") or []
            col = idx[0] if idx and isinstance(idx[0], str) and idx[0] in names else None
        if col is not None and records > 0:
            first = pf.read_row_group(0, columns=[col]).column(0)[0].as_py()
            last = pf.read_row_group(pf.num_row_groups - 1, columns=[col]).column(0)[-1].as_py()
            def _fmt(v):
                try:
                    if isinstance(v, (int, float)):
                        v = float(v)
                        v = v / 1000.0 if v > 1e11 else v
                        import datetime as _dt
                        return _dt.datetime.fromtimestamp(v, _dt.timezone.utc).strftime("%Y-%m-%d")
                    return str(v)[:10]
                except Exception:
                    return str(v)[:10]
            date_range = f"{_fmt(first)} → {_fmt(last)}"
    except Exception as e:
        logger.debug("catalog_meta_error", path=fpath, error=str(e))
    return records, date_range


def _scan_catalog(data_dir: str) -> list:
    datasets = []
    if os.path.exists(data_dir):
        for sym_dir in sorted(os.listdir(data_dir)):
            sym_path = os.path.join(data_dir, sym_dir)
            if os.path.isdir(sym_path):
                for f in sorted(os.listdir(sym_path)):
                    if f.endswith(".parquet"):
                        fpath = os.path.join(sym_path, f)
                        size_mb = os.path.getsize(fpath) / (1024 * 1024)
                        records, date_range = _parquet_meta(fpath)
                        datasets.append({
                            "symbol": sym_dir, "type": f.replace(".parquet", ""),
                            "records": records, "size_mb": round(size_mb, 2),
                            "date_range": date_range,
                        })
    return datasets


def _scan_flat_catalog(data_dir: str, kind: str) -> list:
    datasets = []
    for f in sorted(os.listdir(data_dir)):
        if f.endswith(".parquet"):
            fpath = os.path.join(data_dir, f)
            records, date_range = _parquet_meta(fpath)
            datasets.append({"symbol": f.replace(".parquet", ""), "type": kind, "records": records,
                             "size_mb": round(os.path.getsize(fpath) / (1024 * 1024), 2),
                             "date_range": date_range})
    return datasets


# ── Backtest ─────────────────────────────────────────────────────
_to_thread = asyncio.to_thread  # indirection so tests can assert the off-loop execution


@app.post("/api/backtest/run", dependencies=[Depends(require_token_when_remote)])
async def run_backtest(body: dict = {}):
    """Run a backtest with the specified parameters.

    Accepts: { symbol, strategy, start_date?, end_date?, bars? }
    Returns flat structure matching desktop BacktestResult interface.

    The CPU-bound backtest runs in a worker thread so the trading loops, risk monitor and
    Binance WS keep-alives on the event loop are never blocked. One backtest at a time (409).
    """
    symbol = body.get("symbol", "BTC-USD")
    if not isinstance(symbol, str) or symbol not in Settings().symbol_names:
        raise HTTPException(status_code=400, detail=f"Unknown symbol {symbol!r}")
    if state.backtest_running:
        raise HTTPException(status_code=409, detail="A backtest is already running")
    state.backtest_running = True
    try:
        return await _to_thread(_run_backtest_sync, body)
    finally:
        state.backtest_running = False


def _run_backtest_sync(body: dict) -> dict:
    """Blocking part of /api/backtest/run (parquet load + Backtester.run). Runs off the loop."""
    try:
        from backtesting.backtester import Backtester

        symbol = body.get("symbol", "BTC-USD")
        start_date = body.get("start_date", "")
        end_date = body.get("end_date", "")
        # Accept both singular "strategy" (from desktop) and plural "strategies" (from scripts)
        strategy_param = body.get("strategy", "")
        strategies_list = body.get("strategies", [])
        if strategy_param and not strategies_list:
            strategies_list = [strategy_param]
        max_bars = body.get("bars", 0)  # 0 = use all available data
        try:
            max_bars = int(max_bars or 0)
        except (TypeError, ValueError):
            return {"error": f"Invalid bars: {max_bars!r}"}

        settings = Settings()
        bt = Backtester(settings)

        # Load klines — directory uses BotStrike symbol format (BTC-USD).
        # FUTURES first (audit R2 backtest_parity-03/13): the engine trades USDT-M
        # futures (fapi), but this endpoint used to read data/binance/ = SPOT, whose
        # last candle is 2026-04-03. The correct dataset was written by one script and
        # read by none. data/binance/ stays as a fallback for old checkouts.
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        parquet_path = None
        for sub in ("binance_futures", "binance"):
            data_dir = os.path.join(root, "data", sub, "klines")
            for name in (symbol, symbol.replace("-", "")):  # dashed + legacy format
                candidate = os.path.join(data_dir, name, "1m.parquet")
                if os.path.exists(candidate):
                    parquet_path = candidate
                    break
            if parquet_path:
                break
        if not parquet_path:
            return {"error": f"No data for {symbol}. Run: python scripts/download_futures_klines.py"}

        import pandas as pd
        df = pd.read_parquet(parquet_path)
        # Normalise ms → s BEFORE filtering (audit R2 backtest_parity-03). The parquet
        # stores milliseconds while pd.Timestamp(...).timestamp() returns seconds, so
        # every start_date was silently ignored, every end_date returned 0 bars, and the
        # ms leaked into the metrics: the UI reported Sharpe -0.27 when the real figure
        # was -15.97 (59x) and a mean trade duration of 22 days instead of 32 minutes.
        if len(df) and float(df["timestamp"].median()) > 1e12:
            df = df.copy()
            df["timestamp"] = df["timestamp"] / 1000.0
        if start_date:
            df = df[df["timestamp"] >= pd.Timestamp(start_date).timestamp()]
        if end_date:
            df = df[df["timestamp"] <= pd.Timestamp(end_date).timestamp()]
        if max_bars > 0 and len(df) > max_bars:
            df = df.tail(max_bars).reset_index(drop=True)

        if len(df) < 100:
            return {"error": f"Insufficient data: {len(df)} bars (need 100+)"}

        # Pass strategy filter to backtester (default: MEAN_REVERSION only)
        result = bt.run(df, symbol=symbol,
                        strategies=strategies_list if strategies_list else None)
        summary = result.summary()

        # Return flat structure matching desktop BacktestResult interface
        equity_curve = result.equity_curve
        if len(equity_curve) > 500:
            # Downsample to ~500 points for chart performance
            step = max(1, len(equity_curve) // 500)
            equity_curve = equity_curve[::step]

        return {
            "equity_curve": equity_curve,
            "total_trades": summary.get("total_trades", 0),
            "win_rate": summary.get("win_rate", 0),
            "pnl": summary.get("net_pnl", 0),
            "sharpe_ratio": summary.get("sharpe_ratio", 0),
            "max_drawdown": summary.get("max_drawdown", 0),
            "profit_factor": summary.get("profit_factor", 0),
            "avg_trade_pnl": summary.get("avg_trade_pnl", 0),
            "total_fees": summary.get("total_fees", 0),
            "return_pct": summary.get("return_pct", 0),
            "by_strategy": summary.get("by_strategy", {}),
            "bars_tested": len(df),
        }
    except Exception as e:
        logger.exception("backtest_api_error", error=str(e), error_type=type(e).__name__)
        return {"error": str(e)}


# ── Web UI (built desktop frontend, served as an SPA) ────────────
# Built with `npm run build:web` (desktop/ → server/webui/). Registered AFTER every
# API/WS route so /api/* and /ws/* always win; the app uses a HashRouter, so plain
# StaticFiles(html=True) is enough (no history-fallback needed). When the directory
# is absent (dev checkout without a web build) the bridge behaves exactly as before.
class _SpaStatic(StaticFiles):
    """StaticFiles that never lets a browser cache the entry document.

    index.html was served with only an ETag, so Chrome kept a heuristically cached copy and went on
    loading the PREVIOUS bundle after a deploy — the operator saw an outdated UI until a hard reload
    (seen 2026-09-03). The asset filenames carry a content hash, so those stay immutable for a year;
    only the document that points at them must be revalidated on every load.
    """

    async def get_response(self, path: str, scope):
        resp = await super().get_response(path, scope)
        # Starlette normalises "/" to "." and joins with the OS separator (a backslash on Windows),
        # so normalise before matching: only content-hashed assets may be cached, everything else
        # revalidates.
        if path.replace("\\", "/").startswith("assets/"):
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            resp.headers["Cache-Control"] = "no-cache, must-revalidate"
        return resp


_WEBUI_DIR = Path(__file__).resolve().parent / "webui"
if _WEBUI_DIR.is_dir():
    app.mount("/", _SpaStatic(directory=str(_WEBUI_DIR), html=True), name="webui")


# ── Main ─────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="BotStrike Bridge Server")
    parser.add_argument("--port", type=int, default=9420, help="Server port")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Server host")
    parser.add_argument("--live", action="store_true", help="Live trading mode")
    parser.add_argument("--dry-run", action="store_true", help="Dry run mode")
    parser.add_argument("--dev", action="store_true", help="Dev mode with reload")
    args = parser.parse_args()

    global _EXPOSE_TOKEN
    # Export before computing: with --dev the reload worker re-imports this module in a CHILD
    # process that never runs main(), and derives _EXPOSE_TOKEN from this env var (R2 sec-05).
    os.environ["BOTSTRIKE_HOST"] = args.host
    _EXPOSE_TOKEN = args.host in _LOOPBACK_HOSTS
    if not _EXPOSE_TOKEN and not os.getenv("BOTSTRIKE_AUTH_TOKEN", "").strip():
        logger.warning("auth_token_random_on_public_bind",
                       hint="set BOTSTRIKE_AUTH_TOKEN in .env: on a non-loopback bind start/stop/backtest "
                            "require it in every mode and it is never exposed over HTTP")

    if args.live:
        state.mode = "live"
    elif args.dry_run:
        state.mode = "dry_run"
    else:
        state.mode = "paper"

    import uvicorn
    uvicorn.run(
        "server.bridge:app" if args.dev else app,
        host=args.host,
        port=args.port,
        reload=args.dev,
        log_level="info",
    )


if __name__ == "__main__":
    main()
