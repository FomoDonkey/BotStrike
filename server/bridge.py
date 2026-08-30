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
import os
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
import secrets
import structlog

logger = structlog.get_logger(__name__)

from config.settings import Settings
from core.types import MarketRegime, StrategyType, Side

# Single source of truth for the bridge version (reported by /api/health and the OpenAPI schema).
BRIDGE_VERSION = "2.12.1"

# Auth token for mutating endpoints (live start / live stop).
# Server deployments set BOTSTRIKE_AUTH_TOKEN in .env so the desktop can be configured with it;
# otherwise a random per-process token is generated (desktop-local mode reads it from /api/bot/status).
_AUTH_TOKEN = os.getenv("BOTSTRIKE_AUTH_TOKEN", "").strip() or secrets.token_hex(16)
# Only expose the token over HTTP when bound to loopback (same-machine desktop). On 0.0.0.0 it would
# hand live-trading control to anyone on the LAN/tailnet. Set in main() from --host.
# When False (non-loopback bind) start/stop/backtest REQUIRE the token in every mode.
_EXPOSE_TOKEN = True
VALID_MODES = {"paper", "dry_run", "live"}
VALID_EXCHANGES = ("binance", "hyperliquid", "strike")

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


# ── WebSocket Connection Manager ─────────────────────────────────
class ChannelManager:
    """Manages WebSocket connections per channel with broadcast capability."""

    VALID_CHANNELS = {"market", "trading", "micro", "risk", "system"}

    def __init__(self):
        self._channels: Dict[str, Set[WebSocket]] = {
            ch: set() for ch in self.VALID_CHANNELS
        }

    async def connect(self, channel: str, ws: WebSocket):
        if channel not in self._channels:
            return
        await ws.accept()
        self._channels[channel].add(ws)

    def disconnect(self, channel: str, ws: WebSocket):
        if channel in self._channels:
            self._channels[channel].discard(ws)

    async def broadcast(self, channel: str, data: dict):
        if channel not in self._channels:
            return
        clients = self._channels[channel]
        if not clients:
            return
        dead = []
        message = json.dumps(data, default=_json_default)
        for ws in clients:
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

    `settings` lets the caller pass venue-specific config (fees/slippage); when omitted
    a default Settings() is used. Binance stays the data/execution backend (use_binance=True).
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

    state.engine = BotStrike(
        settings=settings,
        dry_run=is_dry_run,
        paper=is_paper,
        use_binance=True,
    )
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

        # Cancel live orders if in live mode (match CLI: main.py:1084-1085)
        if not engine.dry_run and not engine.paper:
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
        await state.channels.broadcast("market", {
            "type": "snapshot",
            "data": serialize_market_snapshot(snapshot),
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
        positions = []
        for strat in StrategyType:
            pos = engine.paper_sim.get_position(symbol, strat)
            if pos:
                positions.append(serialize_position(pos))
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
    await state.channels.broadcast("risk", {
        "type": "risk_update",
        "timestamp": time.time(),
        "symbol": symbol,
        "equity": float(rm.current_equity),
        "drawdown_pct": float(rm.current_drawdown_pct),
        "max_drawdown_pct": float(engine.settings.trading.max_drawdown_pct),
        "circuit_breaker_active": bool(rm.is_circuit_breaker_active),
        "regime": engine._last_regime.get(symbol, MarketRegime.UNKNOWN).value,
    })

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
                        })
        except Exception as e:
            logger.warning("candle_broadcast_error", error=str(e), error_type=type(e).__name__)
        await asyncio.sleep(1)  # 1s broadcast — real-time feel


async def metrics_broadcast_loop():
    """Broadcast performance metrics every 2 seconds."""
    while True:
        try:
            if state.engine and state.running:
                # MetricsCollector.get_metrics() returns a dict, not attributes
                m = state.engine.metrics.get_metrics()
                equity = float(state.engine.risk_manager.current_equity)
                pnl = float(m.get("total_pnl", 0))
                await state.channels.broadcast("trading", {
                    "type": "metrics",
                    "timestamp": time.time(),
                    "equity": equity,
                    "pnl": pnl,
                    "total_trades": int(m.get("total_trades", 0)),
                    "win_rate": float(m.get("win_rate", 0)),
                    "sharpe_ratio": float(m.get("sharpe_ratio", 0)),
                    "max_drawdown": float(m.get("max_drawdown", 0)),
                    "total_fees": float(m.get("total_fees", 0)),
                })
                state.equity = equity
                state.pnl = pnl
        except Exception as e:
            logger.debug("metrics_broadcast_error", error=str(e))
        await asyncio.sleep(2)


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
            })

            # Send periodic engine status to Live Logs (every ~15s = 5 health cycles)
            _log_counter += 1
            if _log_counter >= 5 and state.engine and state.running:
                _log_counter = 0
                m = state.engine.metrics.get_metrics()
                rm = state.engine.risk_manager
                regime = list(state.engine._last_regime.values())
                regime_str = regime[0].value if regime else "UNKNOWN"
                await state.channels.broadcast("system", {
                    "type": "log",
                    "timestamp": time.time(),
                    "level": "info",
                    "message": f"Engine: {m.get('total_trades', 0)} trades | PnL ${m.get('total_pnl', 0):+.2f} | DD {rm.current_drawdown_pct:.2%} | Regime {regime_str}",
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
    }


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
    ]

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
    """API docs are only served on a loopback bind (desktop-local)."""
    if not _EXPOSE_TOKEN and request.url.path in _DOCS_PATHS:
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    return await call_next(request)


def _token_ok(supplied: str) -> bool:
    return bool(supplied) and secrets.compare_digest(supplied, _AUTH_TOKEN)


async def require_token_when_remote(token: str = "", x_botstrike_token: str = Header(default="")):
    """Loopback bind (desktop-local): unchanged — paper/dry_run need no token.
    Non-loopback bind (server on 0.0.0.0): every mutation needs BOTSTRIKE_AUTH_TOKEN
    (query `token=` or header `X-BotStrike-Token`)."""
    if _EXPOSE_TOKEN:
        return
    if not _token_ok(token or x_botstrike_token):
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


@app.get("/api/config")
async def get_config():
    if state.engine:
        return serialize_settings(state.engine.settings)
    return {"error": "Engine not started"}


@app.post("/api/bot/start", dependencies=[Depends(require_token_when_remote)])
async def bot_start(mode: str = "paper", exchange: str = "binance", token: str = ""):
    # Auth: live always needs the token (any bind); other modes only on non-loopback (dependency)
    if mode == "live" and not _token_ok(token):
        raise HTTPException(status_code=401, detail="Invalid or missing auth token for live mode")
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
async def bot_stop(token: str = ""):
    if not state.running:
        return {"status": "not_running"}
    # Live always needs the token to stop (any bind); paper on loopback stops without it
    if state.mode == "live" and not _token_ok(token):
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
    if not state.engine:
        return {"error": "Engine not started"}

    m = state.engine.metrics.get_metrics()
    equity_curve = list(state.engine.metrics._equity_curve)[-500:]

    return {
        "equity": float(state.engine.risk_manager.current_equity),
        "pnl": float(m.get("total_pnl", 0)),
        "total_trades": int(m.get("total_trades", 0)),
        "win_rate": float(m.get("win_rate", 0)),
        "sharpe_ratio": float(m.get("sharpe_ratio", 0)),
        "max_drawdown": float(m.get("max_drawdown", 0)),
        "total_fees": float(m.get("total_fees", 0)),
        "avg_win": float(m.get("avg_win", 0)),
        "avg_loss": float(m.get("avg_loss", 0)),
        "profit_factor": float(m.get("profit_factor", 0)),
        "equity_curve": equity_curve,
    }


@app.get("/api/strategies")
async def get_strategies():
    if not state.engine:
        return {"error": "Engine not started"}

    strategies = []
    alloc_map = {
        StrategyType.MEAN_REVERSION: state.engine.settings.trading.allocation_mean_reversion,
        StrategyType.FIBONACCI_RETRACEMENT: state.engine.settings.trading.allocation_fibonacci_retracement,
        StrategyType.TREND_FOLLOWING: state.engine.settings.trading.allocation_trend_following,
        StrategyType.MARKET_MAKING: state.engine.settings.trading.allocation_market_making,
        StrategyType.ORDER_FLOW_MOMENTUM: state.engine.settings.trading.allocation_order_flow_momentum,
    }
    for s in state.engine.strategies:
        alloc_active = alloc_map.get(s.strategy_type, 0) > 0
        # Check kill switch from research engine
        research_active, kill_reason = state.engine.research.get_strategy_status(s.strategy_type)
        strategies.append({
            "type": s.strategy_type.value,
            "name": s.__class__.__name__,
            "active": alloc_active and research_active,
            "allocation": alloc_map.get(s.strategy_type, 0),
            "killed": not research_active,
            "kill_reason": kill_reason,
        })
    return {"strategies": strategies}


@app.get("/api/trades")
async def get_trades(limit: int = 100):
    if not state.engine:
        return {"trades": []}
    try:
        records = state.engine.trade_repo.get_trades(
            source="paper", limit=limit,
        )
        trades = []
        for r in records:
            # Format timestamps for display
            import datetime
            entry_time = datetime.datetime.fromtimestamp(r.timestamp).isoformat() if r.timestamp else None
            exit_time = None
            if r.trade_type == "EXIT" and r.duration_sec and r.duration_sec > 0:
                exit_time = datetime.datetime.fromtimestamp(r.timestamp).isoformat()
                entry_time = datetime.datetime.fromtimestamp(r.timestamp - r.duration_sec).isoformat()

            trades.append({
                "id": r.id if hasattr(r, 'id') else 0,
                "symbol": r.symbol,
                "side": r.side,
                "strategy": r.strategy,
                "entry_price": r.entry_price or r.price,
                "exit_price": r.exit_price or (r.price if r.trade_type == "EXIT" else 0),
                "quantity": r.quantity,
                "pnl": r.pnl,
                "fee": r.fee,
                "duration_sec": r.duration_sec or 0,
                "entry_time": entry_time,
                "exit_time": exit_time,
                "regime": r.regime or "",
            })
        # Return most recent first
        trades.reverse()
        return {"trades": trades[:limit]}
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
    datasets = []
    if os.path.exists(data_dir):
        for sym_dir in os.listdir(data_dir):
            sym_path = os.path.join(data_dir, sym_dir)
            if os.path.isdir(sym_path):
                for f in os.listdir(sym_path):
                    if f.endswith(".parquet"):
                        fpath = os.path.join(sym_path, f)
                        size_mb = os.path.getsize(fpath) / (1024 * 1024)
                        datasets.append({
                            "symbol": sym_dir, "type": f.replace(".parquet", ""),
                            "records": 0, "size_mb": round(size_mb, 2),
                            "date_range": "",
                        })
    return {"datasets": datasets}


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

        # Load Binance klines — directory uses BotStrike symbol format (BTC-USD)
        data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "data", "binance", "klines")
        parquet_path = os.path.join(data_dir, symbol, "1m.parquet")

        if not os.path.exists(parquet_path):
            # Fallback: try without dash (legacy format)
            legacy_path = os.path.join(data_dir, symbol.replace("-", ""), "1m.parquet")
            if os.path.exists(legacy_path):
                parquet_path = legacy_path
            else:
                return {"error": f"No data for {symbol}. Run: python main.py --download-binance"}

        import pandas as pd
        df = pd.read_parquet(parquet_path)
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
    _EXPOSE_TOKEN = args.host in ("127.0.0.1", "localhost", "::1")
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
