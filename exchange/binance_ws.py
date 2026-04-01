"""
Binance WebSocket client para datos de mercado en tiempo real.

Solo lectura — para paper trading con liquidez real.
Provee trades, depth (orderbook), y klines via streams combinados.

Usa la misma interfaz de callbacks que StrikeWebSocket para
integración directa con BotStrike.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Callable, Dict, List, Optional

import websockets
import structlog

logger = structlog.get_logger(__name__)

# Binance WebSocket endpoints
BINANCE_WS_URL = "wss://stream.binance.com:9443/ws"
BINANCE_WS_COMBINED = "wss://stream.binance.com:9443/stream"

# Mapeo BotStrike → Binance
SYMBOL_MAP = {
    "BTC-USD": "btcusdt",
    "ETH-USD": "ethusdt",
    "ADA-USD": "adausdt",
}
SYMBOL_MAP_REVERSE = {v.upper(): k for k, v in SYMBOL_MAP.items()}


class BinanceWebSocket:
    """WebSocket client de Binance para datos de mercado en tiempo real."""

    def __init__(self, symbols: Optional[List[str]] = None):
        self.symbols = symbols or ["BTC-USD", "ETH-USD", "ADA-USD"]
        self._callbacks: Dict[str, List[Callable]] = {}
        self._running = False
        self._ws = None
        self._reconnect_delay = 1
        self._max_reconnect_delay = 30
        # Compatibility with StrikeWebSocket interface
        self._on_market_connect_cb: Optional[Callable] = None

    def on(self, event: str, callback: Callable) -> None:
        """Registra callback para un tipo de evento."""
        self._callbacks.setdefault(event, []).append(callback)

    async def _emit(self, event: str, data: Dict) -> None:
        """Emite evento a todos los callbacks registrados."""
        for cb in self._callbacks.get(event, []):
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(data)
                else:
                    cb(data)
            except Exception as e:
                logger.warning("callback_error", event=event, error=str(e))

    def _build_streams(self) -> List[str]:
        """Construye lista de streams para conexión combinada."""
        streams = []
        for sym in self.symbols:
            binance_sym = SYMBOL_MAP.get(sym, sym.replace("-", "").lower())
            streams.append(f"{binance_sym}@trade")
            streams.append(f"{binance_sym}@depth20@100ms")
            streams.append(f"{binance_sym}@kline_1m")
        return streams

    async def connect_market(self) -> None:
        """Conecta al stream combinado de Binance y procesa mensajes."""
        self._running = True
        streams = self._build_streams()
        url = f"{BINANCE_WS_COMBINED}?streams={'/'.join(streams)}"

        while self._running:
            try:
                async with websockets.connect(url, ping_interval=20) as ws:
                    self._ws = ws
                    self._reconnect_delay = 1
                    logger.info("binance_ws_connected", streams=len(streams))
                    await self._emit("connected", {})
                    if self._on_market_connect_cb:
                        self._on_market_connect_cb()

                    for sym in self.symbols:
                        logger.debug("subscribed", symbol=sym,
                                     channels=["trade", "depth", "kline_1m"])

                    async for raw_msg in ws:
                        if not self._running:
                            break
                        try:
                            msg = json.loads(raw_msg)
                            data = msg.get("data", msg)
                            stream = msg.get("stream", "")
                            await self._process_message(stream, data)
                        except json.JSONDecodeError:
                            continue

            except (websockets.exceptions.ConnectionClosed, OSError) as e:
                if not self._running:
                    break
                logger.warning("binance_ws_disconnected", error=str(e),
                               reconnect_sec=self._reconnect_delay)
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(
                    self._reconnect_delay * 2, self._max_reconnect_delay
                )

    async def _process_message(self, stream: str, data: Dict) -> None:
        """Procesa un mensaje del stream y emite el evento correcto."""
        if "@trade" in stream:
            # Trade: convertir a formato compatible con StrikeWebSocket
            binance_sym = data.get("s", "")
            symbol = SYMBOL_MAP_REVERSE.get(binance_sym, binance_sym)
            trade_data = {
                "s": symbol,
                "p": data.get("p", "0"),
                "q": data.get("q", "0"),
                "T": data.get("T", int(time.time() * 1000)),
                "m": data.get("m", False),  # isBuyerMaker
                "t": data.get("t", 0),  # trade id
                "E": data.get("T", int(time.time() * 1000)),
            }
            await self._emit("trade", trade_data)

        elif "@depth" in stream:
            # Orderbook depth: convertir formato
            binance_sym = stream.split("@")[0].upper()
            symbol = SYMBOL_MAP_REVERSE.get(binance_sym, binance_sym)
            depth_data = {
                "s": symbol,
                "b": data.get("bids", []),
                "a": data.get("asks", []),
                "E": int(time.time() * 1000),
            }
            await self._emit("depth", depth_data)
            await self._emit("depthUpdate", depth_data)

        elif "@kline" in stream:
            # Kline: convertir formato
            k = data.get("k", {})
            binance_sym = k.get("s", data.get("s", ""))
            symbol = SYMBOL_MAP_REVERSE.get(binance_sym, binance_sym)
            kline_data = {
                "s": symbol,
                "k": {
                    "s": symbol,
                    "t": k.get("t", 0),
                    "o": k.get("o", "0"),
                    "h": k.get("h", "0"),
                    "l": k.get("l", "0"),
                    "c": k.get("c", "0"),
                    "v": k.get("v", "0"),
                    "x": k.get("x", False),  # is closed
                },
                "channel": "kline_1m",
                "e": "kline",
            }
            await self._emit("kline", kline_data)
            await self._emit("kline_1m", kline_data)

    async def subscribe(self, channel: str, symbol: str) -> None:
        """No-op: Binance subscriptions are done via URL at connect time."""
        pass

    async def connect_user(self) -> None:
        """No-op: paper trading doesn't need user stream."""
        pass

    async def stop(self) -> None:
        """Detiene la conexión."""
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None
        logger.info("binance_ws_stopped")
