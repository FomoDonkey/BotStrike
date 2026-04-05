"""
Hyperliquid WebSocket client for real-time market data.

Connects to wss://api.hyperliquid.xyz/ws
Provides same callback interface as BinanceWebSocket for drop-in compatibility.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Callable, Dict, List, Optional

import websockets
import structlog

logger = structlog.get_logger(__name__)

HL_WS_URL = "wss://api.hyperliquid.xyz/ws"

# Reuse symbol maps from client
from exchange.hyperliquid_client import SYMBOL_MAP, SYMBOL_MAP_REVERSE


class HyperliquidWebSocket:
    """WebSocket client for Hyperliquid real-time market data."""

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        wallet_address: str = "",
        use_testnet: bool = False,
    ):
        self.symbols = symbols or ["BTC-USD", "ETH-USD"]
        self._wallet = wallet_address
        self._callbacks: Dict[str, List[Callable]] = {}
        self._running = False
        self._connected = False
        self._ws = None
        self._reconnect_delay = 1
        self._max_reconnect_delay = 30
        self._on_market_connect_cb = None

    def on(self, event: str, callback: Callable) -> None:
        if event not in self._callbacks:
            self._callbacks[event] = []
        self._callbacks[event].append(callback)

    async def _emit(self, event: str, data: Any) -> None:
        for cb in self._callbacks.get(event, []):
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(data)
                else:
                    cb(data)
            except Exception as e:
                logger.error("hl_ws_callback_error", event=event, error=str(e))

    async def subscribe(self, channel: str, symbol: str) -> None:
        """Compatibility method — subscriptions are done on connect."""
        pass

    async def connect_market(self) -> None:
        """Connect and subscribe to market data for all symbols."""
        self._running = True

        while self._running:
            try:
                async with websockets.connect(HL_WS_URL, ping_interval=20) as ws:
                    self._ws = ws
                    self._connected = True
                    self._reconnect_delay = 1
                    logger.info("hl_ws_connected", symbols=len(self.symbols))

                    # Subscribe to trades + l2Book for each symbol
                    for sym in self.symbols:
                        coin = SYMBOL_MAP.get(sym, sym.replace("-USD", ""))
                        # Trades
                        await ws.send(json.dumps({
                            "method": "subscribe",
                            "subscription": {"type": "trades", "coin": coin},
                        }))
                        # Order book
                        await ws.send(json.dumps({
                            "method": "subscribe",
                            "subscription": {"type": "l2Book", "coin": coin},
                        }))
                        # Candles 1m
                        await ws.send(json.dumps({
                            "method": "subscribe",
                            "subscription": {"type": "candle", "coin": coin, "interval": "1m"},
                        }))

                    await self._emit("connected", {})
                    if self._on_market_connect_cb:
                        self._on_market_connect_cb()

                    async for raw_msg in ws:
                        if not self._running:
                            break
                        try:
                            msg = json.loads(raw_msg)
                            await self._handle_message(msg)
                        except json.JSONDecodeError:
                            pass

            except (websockets.ConnectionClosed, ConnectionError, OSError) as e:
                self._connected = False
                if self._running:
                    logger.warning("hl_ws_disconnected", error=str(e),
                                   reconnect_in=self._reconnect_delay)
                    await asyncio.sleep(self._reconnect_delay)
                    self._reconnect_delay = min(
                        self._reconnect_delay * 2, self._max_reconnect_delay
                    )
            except Exception as e:
                self._connected = False
                logger.error("hl_ws_error", error=str(e))
                if self._running:
                    await asyncio.sleep(5)

    async def connect_user(self) -> None:
        """Connect to user data stream for fills and position updates."""
        if not self._wallet:
            return

        while self._running:
            try:
                async with websockets.connect(HL_WS_URL, ping_interval=20) as ws:
                    # Subscribe to user events
                    await ws.send(json.dumps({
                        "method": "subscribe",
                        "subscription": {"type": "userEvents", "user": self._wallet},
                    }))
                    await ws.send(json.dumps({
                        "method": "subscribe",
                        "subscription": {"type": "userFills", "user": self._wallet},
                    }))

                    logger.info("hl_user_ws_connected", wallet=self._wallet[:10] + "...")

                    async for raw_msg in ws:
                        if not self._running:
                            break
                        try:
                            msg = json.loads(raw_msg)
                            await self._handle_user_message(msg)
                        except json.JSONDecodeError:
                            pass

            except Exception as e:
                if self._running:
                    logger.warning("hl_user_ws_error", error=str(e))
                    await asyncio.sleep(5)

    async def _handle_message(self, msg: dict) -> None:
        """Route market data messages to appropriate callbacks."""
        channel = msg.get("channel", "")
        data = msg.get("data", {})

        if channel == "trades":
            # Trade updates
            for trade in data if isinstance(data, list) else [data]:
                coin = trade.get("coin", "")
                bs_symbol = SYMBOL_MAP_REVERSE.get(coin, f"{coin}-USD")
                await self._emit("trade", {
                    "s": bs_symbol,
                    "p": trade.get("px", "0"),
                    "q": trade.get("sz", "0"),
                    "T": trade.get("time", int(time.time() * 1000)),
                    "m": trade.get("side") == "A",  # is seller maker
                })

        elif channel == "l2Book":
            # Order book snapshot
            coin = data.get("coin", "")
            bs_symbol = SYMBOL_MAP_REVERSE.get(coin, f"{coin}-USD")
            levels = data.get("levels", [[], []])
            bids = [{"p": l["px"], "q": l["sz"]} for l in levels[0]] if len(levels) > 0 else []
            asks = [{"p": l["px"], "q": l["sz"]} for l in levels[1]] if len(levels) > 1 else []
            await self._emit("depth", {
                "s": bs_symbol,
                "bids": bids,
                "asks": asks,
                "T": int(time.time() * 1000),
            })

        elif channel == "candle":
            # Kline/candle data
            coin = data.get("s", "")
            bs_symbol = SYMBOL_MAP_REVERSE.get(coin, f"{coin}-USD")
            await self._emit("kline", {
                "s": bs_symbol,
                "k": {
                    "t": data.get("t", 0),
                    "o": data.get("o", "0"),
                    "h": data.get("h", "0"),
                    "l": data.get("l", "0"),
                    "c": data.get("c", "0"),
                    "v": data.get("v", "0"),
                    "T": data.get("t", 0),
                    "x": True,
                },
            })

    async def _handle_user_message(self, msg: dict) -> None:
        """Route user data messages (fills, positions)."""
        channel = msg.get("channel", "")
        data = msg.get("data", {})

        if channel == "userFills":
            for fill in data if isinstance(data, list) else [data]:
                coin = fill.get("coin", "")
                bs_symbol = SYMBOL_MAP_REVERSE.get(coin, f"{coin}-USD")
                await self._emit("ORDER_TRADE_UPDATE", {
                    "s": bs_symbol,
                    "S": "BUY" if fill.get("side") == "B" else "SELL",
                    "i": str(fill.get("oid", "")),
                    "x": "TRADE",
                    "X": "FILLED",
                    "L": fill.get("px", "0"),
                    "l": fill.get("sz", "0"),
                    "n": fill.get("fee", "0"),
                    "rp": fill.get("closedPnl", "0"),
                    "T": fill.get("time", int(time.time() * 1000)),
                })

        elif channel == "userEvents":
            # Position/account updates
            if isinstance(data, list):
                for event in data:
                    if "fills" in event:
                        for fill in event["fills"]:
                            coin = fill.get("coin", "")
                            bs_symbol = SYMBOL_MAP_REVERSE.get(coin, f"{coin}-USD")
                            await self._emit("ORDER_TRADE_UPDATE", {
                                "s": bs_symbol,
                                "S": "BUY" if fill.get("side") == "B" else "SELL",
                                "i": str(fill.get("oid", "")),
                                "x": "TRADE",
                                "X": "FILLED",
                                "L": fill.get("px", "0"),
                                "l": fill.get("sz", "0"),
                                "rp": fill.get("closedPnl", "0"),
                            })

    async def stop(self) -> None:
        self._running = False
        self._connected = False
        if self._ws:
            await self._ws.close()
