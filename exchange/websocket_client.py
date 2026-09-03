"""
Cliente WebSocket para Strike Finance.
Maneja streams de market data y user data en tiempo real.
"""
from __future__ import annotations
import asyncio
import json
import time
from typing import Any, Callable, Coroutine, Dict, List, Optional, Set

import websockets
from nacl.signing import SigningKey

from config.settings import Settings
import structlog

logger = structlog.get_logger(__name__)

Callback = Callable[[Dict[str, Any]], Coroutine[Any, Any, None]]


class StrikeWebSocket:
    """Gestor de conexiones WebSocket a Strike Finance."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._market_ws = None
        self._user_ws = None
        self._running = False
        self._callbacks: Dict[str, List[Callback]] = {}
        self._subscriptions: Set[str] = set()
        self._market_reconnect_delay = 1.0
        self._user_reconnect_delay = 1.0
        self._max_reconnect_delay = 60.0
        # Callback para notificar reconexión (usado por tick quality guards)
        self._on_market_connect_cb: Optional[Callable] = None

    # ── Market Data WebSocket ──────────────────────────────────────

    async def connect_market(self) -> None:
        """Conecta al WebSocket de market data y mantiene la conexión."""
        self._running = True
        while self._running:
            try:
                async with websockets.connect(
                    self.settings.ws_market_url,
                    ping_interval=30,
                    ping_timeout=10,
                ) as ws:
                    self._market_ws = ws
                    self._market_reconnect_delay = 1.0
                    logger.info("market_ws_connected")

                    # Notificar reconexión para tick quality warmup
                    if self._on_market_connect_cb:
                        self._on_market_connect_cb()

                    # Re-suscribir canales tras reconexión
                    for sub_key in self._subscriptions:
                        parts = sub_key.split(":")
                        if len(parts) == 2:
                            await self._send_subscribe(ws, parts[0], parts[1])

                    await self._listen(ws, "market")

            except (websockets.ConnectionClosed, ConnectionError, OSError) as e:
                logger.warning("market_ws_disconnected", error=str(e),
                               reconnect_in=self._market_reconnect_delay)
                await asyncio.sleep(self._market_reconnect_delay)
                self._market_reconnect_delay = min(
                    self._market_reconnect_delay * 2, self._max_reconnect_delay
                )

    async def subscribe(self, channel: str, symbol: str) -> None:
        """Suscribe a un canal de market data."""
        sub_key = f"{channel}:{symbol}"
        self._subscriptions.add(sub_key)
        if self._market_ws:
            await self._send_subscribe(self._market_ws, channel, symbol)

    async def subscribe_all_tickers(self) -> None:
        """Suscribe a todos los tickers (sin símbolo)."""
        self._subscriptions.add("!miniticker@arr:")
        if self._market_ws:
            msg = {"method": "subscribe", "channel": "!miniticker@arr", "id": 1}
            await self._market_ws.send(json.dumps(msg))

    async def _send_subscribe(self, ws, channel: str, symbol: str) -> None:
        msg: Dict[str, Any] = {
            "method": "subscribe",
            "channel": channel,
            "id": int(time.time() * 1000) % 100000,
        }
        if symbol:
            msg["symbol"] = symbol
        await ws.send(json.dumps(msg))
        logger.debug("subscribed", channel=channel, symbol=symbol)

    # ── User Data WebSocket ────────────────────────────────────────

    async def connect_user(self) -> None:
        """Conecta al WebSocket de user data con autenticación."""
        if not self.settings.api_private_key:
            logger.warning("no_api_key_for_user_ws")
            return

        self._running = True
        while self._running:
            try:
                async with websockets.connect(
                    self.settings.ws_user_url,
                    ping_interval=30,
                    ping_timeout=10,
                ) as ws:
                    self._user_ws = ws
                    self._user_reconnect_delay = 1.0

                    # Autenticación
                    account_id = await self._authenticate_user_ws(ws)
                    if not account_id:
                        logger.error("user_ws_auth_failed")
                        await asyncio.sleep(5)
                        continue

                    # Suscribir al user stream
                    sub_msg = {
                        "method": "subscribe",
                        "channel": "userstream",
                        "account_id": account_id,
                        "id": 1,
                    }
                    await ws.send(json.dumps(sub_msg))
                    logger.info("user_ws_connected", account_id=account_id)

                    ka = asyncio.create_task(self._keepalive(ws))
                    try:
                        await self._listen(ws, "user")
                    finally:
                        ka.cancel()

            except (websockets.ConnectionClosed, ConnectionError, OSError) as e:
                logger.warning("user_ws_disconnected", error=str(e))
                await asyncio.sleep(self._user_reconnect_delay)
                self._user_reconnect_delay = min(
                    self._user_reconnect_delay * 2, self._max_reconnect_delay
                )

    @staticmethod
    def build_logon(private_key_hex: str, public_key_hex: Optional[str] = None, now_ms: Optional[int] = None) -> Dict:
        """`session.logon` request exactly as documented (docs/strike/docs__api__user__websocket.md):
        message = f"session.logon:{timestamp_ms}:{apiKey}", Ed25519-signed with the API wallet seed."""
        signing_key = SigningKey(bytes.fromhex(private_key_hex.strip()[:64]))
        api_key = (public_key_hex or signing_key.verify_key.encode().hex()).strip().lower()
        ts = int(now_ms if now_ms is not None else time.time() * 1000)
        signature = signing_key.sign(f"session.logon:{ts}:{api_key}".encode()).signature.hex()
        return {"method": "session.logon", "id": 1, "params": {"apiKey": api_key, "signature": signature, "timestamp": ts}}

    async def _authenticate_user_ws(self, ws) -> Optional[str]:
        """Logon on /ws/user-api. Returns the account_id or None (the response carries status 200 +
        result.authenticated)."""
        await ws.send(json.dumps(self.build_logon(self.settings.api_private_key, self.settings.api_public_key)))
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
            data = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8", errors="replace"))
        except (asyncio.TimeoutError, json.JSONDecodeError):
            return None
        if data.get("status") not in (None, 200) or data.get("error"):
            logger.error("user_ws_logon_rejected", response=str(data)[:200])
            return None
        result = data.get("result") or data
        if result.get("authenticated") is False:
            return None
        return result.get("account_id") or data.get("account_id")

    async def _keepalive(self, ws) -> None:
        """App-level ping every 30 s (the server also sends protocol pings every 54 s)."""
        while True:
            await asyncio.sleep(30)
            try:
                await ws.send(json.dumps({"method": "ping", "id": "hb"}))
            except Exception:  # noqa: BLE001 — the read loop will notice the closed socket
                return

    # ── Listener genérico ──────────────────────────────────────────

    async def _listen(self, ws, stream_type: str) -> None:
        """Escucha mensajes y despacha a callbacks."""
        async for raw_message in ws:
            # Strike puede enviar múltiples JSON separados por \n
            msg_str = raw_message if isinstance(raw_message, str) else raw_message.decode("utf-8", errors="replace")
            for line in msg_str.split("\n"):
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if data.get("method") == "pong" or (data.get("id") is not None and "e" not in data and "result" in data):
                    continue                                  # keepalive / subscribe ack
                # Determinar canal/event para dispatch
                event_type = data.get("e") or data.get("channel") or stream_type
                await self._dispatch(event_type, data)

    async def _dispatch(self, event_type: str, data: Dict) -> None:
        """Despacha evento a callbacks registrados."""
        callbacks = self._callbacks.get(event_type, [])
        callbacks += self._callbacks.get("*", [])  # wildcard
        for cb in callbacks:
            try:
                await cb(data)
            except Exception as e:
                logger.error("callback_error", event=event_type, error=str(e))

    # ── Registro de callbacks ──────────────────────────────────────

    def on(self, event_type: str, callback: Callback) -> None:
        """Registra callback para un tipo de evento.

        event_type puede ser: 'trade', 'depth', 'markprice', 'miniticker',
        'ACCOUNT_UPDATE', 'ORDER_TRADE_UPDATE', o '*' para todos.
        """
        if event_type not in self._callbacks:
            self._callbacks[event_type] = []
        self._callbacks[event_type].append(callback)

    # ── Control ────────────────────────────────────────────────────

    async def stop(self) -> None:
        """Detiene todas las conexiones WebSocket."""
        self._running = False
        if self._market_ws:
            await self._market_ws.close()
        if self._user_ws:
            await self._user_ws.close()
        logger.info("websockets_stopped")
