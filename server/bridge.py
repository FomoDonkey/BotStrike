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
import json
import os
import sys
import time
from collections import deque
from contextlib import asynccontextmanager
from typing import Dict, Optional, Set

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import structlog

logger = structlog.get_logger(__name__)

from config.settings import Settings
from core.types import MarketRegime, StrategyType, Side
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
async def start_engine(mode: str = "paper"):
    """Start the BotStrike trading engine."""
    # Update market data first (incremental — only downloads new candles)
    await update_market_data()

    from main import BotStrike

    settings = Settings()
    settings.use_testnet = False  # Always mainnet for real price data
    is_paper = mode == "paper"
    is_dry_run = mode == "dry_run"

    # Identical config to: python main.py --paper --binance --no-testnet
    state.engine = BotStrike(
        settings=settings,
        dry_run=is_dry_run,
        paper=is_paper,
        use_binance=True,
    )
    state.mode = mode
    state.running = True

    _install_hooks(state.engine)
    state.engine_task = asyncio.create_task(_run_engine())


async def _run_engine():
    """Run the engine with error handling."""
    try:
        await state.engine.start()
    except asyncio.CancelledError:
        pass
    except Exception as e:
        await state.channels.broadcast("system", {
            "type": "engine_error",
            "error": str(e),
            "timestamp": time.time(),
        })
    finally:
        state.running = False


async def stop_engine():
    """Gracefully stop the engine."""
    if state.engine:
        state.engine._running = False
    if state.engine_task and not state.engine_task.done():
        state.engine_task.cancel()
        try:
            await asyncio.wait_for(state.engine_task, timeout=10)
        except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
            pass
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

            # Atomic swap: latest tick per symbol
            state._market_queue[symbol] = {
                "type": "tick",
                "symbol": symbol,
                "price": price,
                "quantity": qty,
                "side": "BUY" if is_buy else "SELL",
                "notional": price * qty,
                "timestamp": ts,
            }

        engine.websocket.on("trade", on_trade_hook)

    engine._setup_ws_callbacks = patched_setup

    # Intercept _process_symbol for signal + state broadcast
    original_process = engine._process_symbol

    async def patched_process(symbol, sym_config):
        await original_process(symbol, sym_config)
        await _broadcast_symbol_state(engine, symbol)

    engine._process_symbol = patched_process

    # Intercept paper fills for trade broadcast + live logs
    if hasattr(engine, '_process_paper_fill'):
        original_paper_fill = engine._process_paper_fill

        def patched_paper_fill(trade):
            original_paper_fill(trade)
            serialized = serialize_trade(trade)
            state.recent_trades.append(serialized)
            state._pending_signals.append({
                "type": "trade",
                "data": serialized,
            })
            # Send to live logs
            side = trade.side.value if hasattr(trade.side, 'value') else str(trade.side)
            strat = trade.strategy.value if trade.strategy and hasattr(trade.strategy, 'value') else ""
            pnl_str = f" PnL: ${trade.pnl:+.4f}" if trade.pnl != 0 else ""
            state._pending_signals.append({
                "type": "log_entry",
                "channel": "system",
                "data": {
                    "type": "log",
                    "timestamp": time.time(),
                    "level": "info" if trade.pnl >= 0 else "warn",
                    "message": f"Trade: {side} {trade.symbol} @ ${trade.price:,.2f} [{strat}]{pnl_str}",
                },
            })

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

    # Positions (paper mode)
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

    # Risk state
    rm = engine.risk_manager
    await state.channels.broadcast("risk", {
        "type": "risk_update",
        "timestamp": time.time(),
        "equity": float(rm.current_equity),
        "drawdown_pct": float(rm.current_drawdown_pct),
        "max_drawdown_pct": float(engine.settings.trading.max_drawdown_pct),
        "circuit_breaker_active": bool(rm.is_circuit_breaker_active),
        "regime": engine._last_regime.get(symbol, MarketRegime.UNKNOWN).value,
    })

    # Broadcast pending signals/trades
    while state._pending_signals:
        msg = state._pending_signals.popleft()
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
    """Broadcast candles from market data collector."""
    _last_candle_hash: Dict[str, str] = {}

    while True:
        try:
            if state.engine and state.running:
                for sym_config in state.engine.settings.symbols:
                    symbol = sym_config.symbol
                    df = state.engine.market_data.get_dataframe(symbol)
                    if df is None or df.empty or len(df) < 2:
                        continue

                    rows = df.tail(200)  # More history for chart

                    # Hash to avoid re-sending identical data
                    last = rows.iloc[-1]
                    ts_val = last.get("timestamp", 0)
                    cache_key = f"{len(rows)}_{ts_val}_{last.get('close',0)}"
                    if _last_candle_hash.get(symbol) == cache_key:
                        continue
                    _last_candle_hash[symbol] = cache_key

                    candles = []
                    for _, row in rows.iterrows():
                        ts = float(row.get("timestamp", 0))
                        # lightweight-charts needs time in seconds (Unix)
                        if ts > 1e12:
                            ts = ts / 1000  # Convert ms to s
                        candles.append({
                            "time": int(ts),  # Must be integer seconds for lightweight-charts
                            "open": float(row.get("open", 0)),
                            "high": float(row.get("high", 0)),
                            "low": float(row.get("low", 0)),
                            "close": float(row.get("close", 0)),
                            "volume": float(row.get("volume", 0)),
                        })

                    if candles:
                        await state.channels.broadcast("market", {
                            "type": "candles",
                            "symbol": symbol,
                            "data": candles,
                        })
        except Exception as e:
            logger.warning("candle_broadcast_error", error=str(e), error_type=type(e).__name__)
        await asyncio.sleep(5)


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


# ── FastAPI App ──────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    loops = [
        asyncio.create_task(market_broadcast_loop()),
        asyncio.create_task(candle_broadcast_loop()),
        asyncio.create_task(metrics_broadcast_loop()),
        asyncio.create_task(system_broadcast_loop()),
    ]

    await start_engine(state.mode)
    yield

    await stop_engine()
    for t in loops:
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass


app = FastAPI(title="BotStrike Bridge", version="1.0.0", lifespan=lifespan)

# CORS: allow all localhost origins (Tauri uses varying origin formats)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1|tauri\.localhost)(:\d+)?$|^tauri://localhost$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
async def health():
    return {
        "status": "ok",
        "engine_running": state.running,
        "mode": state.mode,
        "uptime_sec": time.time() - state.start_time,
        "clients": state.channels.client_count,
    }


@app.get("/api/config")
async def get_config():
    if state.engine:
        return serialize_settings(state.engine.settings)
    return {"error": "Engine not started"}


@app.post("/api/bot/start")
async def bot_start(mode: str = "paper"):
    if state.running:
        return {"status": "already_running", "mode": state.mode}
    await start_engine(mode)
    return {"status": "starting", "mode": mode}


@app.post("/api/bot/stop")
async def bot_stop():
    if not state.running:
        return {"status": "not_running"}
    await stop_engine()
    return {"status": "stopped"}


@app.get("/api/bot/status")
async def bot_status():
    return {
        "running": state.running,
        "mode": state.mode,
        "uptime_sec": time.time() - state.start_time if state.running else 0,
        "equity": state.equity,
        "pnl": state.pnl,
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
        StrategyType.TREND_FOLLOWING: state.engine.settings.trading.allocation_trend_following,
        StrategyType.MARKET_MAKING: state.engine.settings.trading.allocation_market_making,
        StrategyType.ORDER_FLOW_MOMENTUM: state.engine.settings.trading.allocation_order_flow_momentum,
    }
    for s in state.engine.strategies:
        strategies.append({
            "type": s.strategy_type.value,
            "name": s.__class__.__name__,
            "active": s.strategy_type in [StrategyType.MEAN_REVERSION, StrategyType.ORDER_FLOW_MOMENTUM],
            "allocation": alloc_map.get(s.strategy_type, 0),
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
        return {"trades": list(state.recent_trades)}


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


# ── Main ─────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="BotStrike Bridge Server")
    parser.add_argument("--port", type=int, default=9420, help="Server port")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Server host")
    parser.add_argument("--live", action="store_true", help="Live trading mode")
    parser.add_argument("--dry-run", action="store_true", help="Dry run mode")
    parser.add_argument("--dev", action="store_true", help="Dev mode with reload")
    args = parser.parse_args()

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
