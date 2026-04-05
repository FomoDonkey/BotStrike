"""
Binance WebSocket client para datos de mercado en tiempo real.

Provee trades, depth (orderbook), y klines via streams combinados.
También soporta user data stream para fills y posiciones en live trading.

Usa la misma interfaz de callbacks que StrikeWebSocket para
integración directa con BotStrike.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Callable, Dict, List, Optional

import aiohttp
import websockets
import structlog

logger = structlog.get_logger(__name__)

# Binance Futures WebSocket endpoints (mainnet)
BINANCE_WS_URL = "wss://fstream.binance.com/ws"
BINANCE_WS_COMBINED = "wss://fstream.binance.com/stream"
BINANCE_FAPI_BASE = "https://fapi.binance.com"
BINANCE_FAPI_WS = "wss://fstream.binance.com/ws"
# Testnet endpoints
BINANCE_WS_TESTNET = "wss://stream.binancefuture.com/ws"
BINANCE_WS_COMBINED_TESTNET = "wss://stream.binancefuture.com/stream"

# Mapeo BotStrike → Binance (lowercase for WS stream names)
# Uses the canonical map from binance_client as source of truth.
from exchange.binance_client import SYMBOL_MAP as _CLIENT_MAP, SYMBOL_MAP_REVERSE
SYMBOL_MAP = {k: v.lower() for k, v in _CLIENT_MAP.items()}


class BinanceWebSocket:
    """WebSocket client de Binance para datos de mercado en tiempo real."""

    def __init__(self, symbols: Optional[List[str]] = None, use_testnet: bool = False):
        self.symbols = symbols or ["BTC-USD", "ETH-USD", "ADA-USD"]
        self._use_testnet = use_testnet
        self._callbacks: Dict[str, List[Callable]] = {}
        self._running = False
        self._connected = False  # Bridge reads this for health status
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
            streams.append(f"{binance_sym}@markPrice@1s")
        return streams

    async def connect_market(self) -> None:
        """Conecta al stream combinado de Binance y procesa mensajes."""
        self._running = True
        streams = self._build_streams()
        base_url = BINANCE_WS_COMBINED_TESTNET if self._use_testnet else BINANCE_WS_COMBINED
        url = f"{base_url}?streams={'/'.join(streams)}"

        while self._running:
            try:
                async with websockets.connect(url, ping_interval=20) as ws:
                    self._ws = ws
                    self._reconnect_delay = 1
                    self._connected = True
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
                self._connected = False
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

        elif "@markPrice" in stream:
            # Mark price + funding rate update
            binance_sym = data.get("s", "")
            symbol = SYMBOL_MAP_REVERSE.get(binance_sym, binance_sym)
            mark_data = {
                "s": symbol,
                "p": data.get("p", "0"),        # mark price
                "r": data.get("r", "0"),         # funding rate
                "T": data.get("T", int(time.time() * 1000)),
                "e": "markPriceUpdate",
            }
            await self._emit("markPrice", mark_data)
            await self._emit("markPriceUpdate", mark_data)

    async def subscribe(self, channel: str, symbol: str) -> None:
        """No-op: Binance subscriptions are done via URL at connect time."""
        pass

    async def connect_user(self) -> None:
        """Connect to Binance Futures user data stream for order/position updates.

        Requires BINANCE_API_KEY env var. Creates a listenKey via REST,
        then connects to the WebSocket. Keeps listenKey alive every 30min.
        """
        api_key = os.getenv("BINANCE_API_KEY", "")
        if not api_key:
            logger.info("binance_user_stream_skipped", reason="no API key")
            return

        # Get listenKey from Binance Futures
        listen_key = await self._get_listen_key(api_key)
        if not listen_key:
            return

        url = f"{BINANCE_FAPI_WS}/{listen_key}"
        self._running = True
        reconnect_delay = 1

        while self._running:
            try:
                async with websockets.connect(url, ping_interval=20) as ws:
                    logger.info("binance_user_stream_connected")
                    reconnect_delay = 1

                    # Keep-alive task: PUT every 30 min to extend listenKey
                    async def keepalive():
                        while self._running:
                            await asyncio.sleep(1800)  # 30 min
                            await self._keepalive_listen_key(api_key, listen_key)

                    keepalive_task = asyncio.create_task(keepalive())

                    try:
                        async for raw_msg in ws:
                            if not self._running:
                                break
                            try:
                                data = json.loads(raw_msg)
                                event_type = data.get("e", "")
                                if event_type == "ORDER_TRADE_UPDATE":
                                    # Normalize to StrikeWebSocket-compatible format
                                    await self._emit("ORDER_TRADE_UPDATE", data)
                                elif event_type == "ACCOUNT_UPDATE":
                                    await self._emit("ACCOUNT_UPDATE", data)
                            except json.JSONDecodeError:
                                continue
                    finally:
                        keepalive_task.cancel()

            except (websockets.exceptions.ConnectionClosed, OSError) as e:
                if not self._running:
                    break
                logger.warning("binance_user_stream_disconnected", error=str(e),
                               reconnect_sec=reconnect_delay)
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 60)
                # Get fresh listenKey on reconnect
                listen_key = await self._get_listen_key(api_key)
                if listen_key:
                    url = f"{BINANCE_FAPI_WS}/{listen_key}"

    async def _get_listen_key(self, api_key: str) -> Optional[str]:
        """Get or create a listenKey for Binance Futures user data stream."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{BINANCE_FAPI_BASE}/fapi/v1/listenKey",
                    headers={"X-MBX-APIKEY": api_key},
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        key = data.get("listenKey", "")
                        if key:
                            logger.info("binance_listen_key_obtained")
                            return key
                    text = await resp.text()
                    logger.error("binance_listen_key_failed", status=resp.status, body=text[:100])
        except Exception as e:
            logger.error("binance_listen_key_error", error=str(e))
        return None

    async def _keepalive_listen_key(self, api_key: str, listen_key: str) -> None:
        """Extend listenKey validity (must be called every <60 min)."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.put(
                    f"{BINANCE_FAPI_BASE}/fapi/v1/listenKey",
                    headers={"X-MBX-APIKEY": api_key},
                ) as resp:
                    if resp.status == 200:
                        logger.debug("binance_listen_key_extended")
                    else:
                        logger.warning("binance_listen_key_extend_failed", status=resp.status)
        except Exception as e:
            logger.warning("binance_listen_key_extend_error", error=str(e))

    async def stop(self) -> None:
        """Detiene la conexión."""
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None
        logger.info("binance_ws_stopped")
