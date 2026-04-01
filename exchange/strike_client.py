"""
Cliente REST para Strike Finance Perpetuals API.
Implementa autenticación Ed25519 y todos los endpoints necesarios.
"""
from __future__ import annotations
import hashlib
import json
import time
import uuid
from typing import Any, Dict, List, Optional

import aiohttp
from nacl.signing import SigningKey

from config.settings import Settings
from core.types import (
    Order, OrderType, Side, TimeInForce, OrderBook, OrderBookLevel,
    Position, MarketSnapshot,
)
import structlog

logger = structlog.get_logger(__name__)


class _RateLimiter:
    """Token bucket rate limiter para requests HTTP."""

    def __init__(self, max_requests: int = 50, window_sec: float = 10.0) -> None:
        self._max = max_requests
        self._window = window_sec
        self._timestamps: list = []

    async def acquire(self) -> None:
        """Espera si es necesario para respetar el rate limit."""
        import asyncio
        while True:
            now = time.time()
            # Limpiar timestamps fuera de la ventana
            self._timestamps = [t for t in self._timestamps if now - t < self._window]
            if len(self._timestamps) < self._max:
                break
            # Esperar hasta que el timestamp más viejo expire, then re-check
            wait = self._timestamps[0] + self._window - now + 0.05
            if wait > 0:
                logger.debug("rate_limit_wait", wait_sec=round(wait, 2))
                await asyncio.sleep(wait)
        self._timestamps.append(time.time())


class StrikeClient:
    """Cliente asíncrono para la API REST de Strike Finance."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._base_url = settings.api_base_url
        self._price_url = settings.api_price_url
        self._session: Optional[aiohttp.ClientSession] = None
        # Rate limiter: 50 requests per 10 seconds (conservative)
        self._rate_limiter = _RateLimiter(max_requests=50, window_sec=10.0)

        # Preparar signing key si hay credenciales
        if settings.api_private_key:
            key_bytes = bytes.fromhex(settings.api_private_key[:64])
            self._signing_key = SigningKey(key_bytes)
            self._public_key = settings.api_public_key
        else:
            self._signing_key = None
            self._public_key = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=15, connect=5, sock_read=10)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    # ── Autenticación ──────────────────────────────────────────────

    def _sign_request(
        self, method: str, path: str, body: str = ""
    ) -> Dict[str, str]:
        """Genera headers de autenticación Ed25519 para un request."""
        timestamp = str(int(time.time()))
        nonce = str(uuid.uuid4())
        body_hash = hashlib.sha256(body.encode()).hexdigest()
        message = f"{method}:{path}:{timestamp}:{nonce}:{body_hash}"
        signed = self._signing_key.sign(message.encode())
        signature = signed.signature.hex()
        return {
            "X-API-Wallet-Public-Key": self._public_key,
            "X-API-Wallet-Signature": signature,
            "X-API-Wallet-Timestamp": timestamp,
            "X-API-Wallet-Nonce": nonce,
            "Content-Type": "application/json",
        }

    # ── Requests genéricos ─────────────────────────────────────────

    async def _public_get(self, path: str, params: Optional[Dict] = None) -> Any:
        """GET a endpoint público (market data)."""
        await self._rate_limiter.acquire()
        session = await self._get_session()
        url = f"{self._price_url}{path}"
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                text = await resp.text()
                logger.error("public_get_error", url=url, status=resp.status, body=text)
                raise Exception(f"API error {resp.status}: {text}")
            return await resp.json()

    async def _auth_request(
        self, method: str, path: str, body: Optional[Dict] = None
    ) -> Any:
        """Request autenticado (GET/POST/DELETE)."""
        await self._rate_limiter.acquire()
        session = await self._get_session()
        url = f"{self._base_url}{path}"
        body_str = ""
        if body is not None:
            body_str = json.dumps(body)

        if not self._signing_key:
            raise Exception("API credentials not configured. Set api_private_key and api_public_key.")
        headers = self._sign_request(method.upper(), path, body_str)

        if method.upper() == "GET":
            async with session.get(url, headers=headers) as resp:
                return await self._handle_response(resp, url)
        elif method.upper() == "POST":
            async with session.post(url, headers=headers, data=body_str) as resp:
                return await self._handle_response(resp, url)
        elif method.upper() == "DELETE":
            async with session.delete(url, headers=headers, data=body_str) as resp:
                return await self._handle_response(resp, url)
        else:
            raise ValueError(f"Unsupported HTTP method: {method}")

    async def _handle_response(self, resp: aiohttp.ClientResponse, url: str) -> Any:
        if resp.status not in (200, 201):
            text = await resp.text()
            logger.error("auth_request_error", url=url, status=resp.status, body=text)
            raise Exception(f"API error {resp.status}: {text}")
        return await resp.json()

    # ── Market Data (público) ──────────────────────────────────────

    async def get_exchange_info(self) -> Dict:
        """Obtiene reglas de trading, símbolos y rate limits."""
        return await self._public_get("/v2/exchangeInfo")

    async def get_orderbook(self, symbol: str, limit: int = 20) -> OrderBook:
        """Obtiene el libro de órdenes."""
        data = await self._public_get("/v2/depth", {"symbol": symbol, "limit": limit})
        bids = [OrderBookLevel(float(p), float(q)) for p, q in data.get("bids", [])]
        asks = [OrderBookLevel(float(p), float(q)) for p, q in data.get("asks", [])]
        return OrderBook(
            symbol=symbol,
            timestamp=time.time(),
            bids=bids,
            asks=asks,
        )

    async def get_ticker_24h(self, symbol: Optional[str] = None) -> Any:
        """Estadísticas 24h."""
        params = {"symbol": symbol} if symbol else {}
        return await self._public_get("/v2/ticker/24hr", params)

    async def get_mark_price(self, symbol: Optional[str] = None) -> Any:
        """Mark price y funding rate."""
        params = {"symbol": symbol} if symbol else {}
        return await self._public_get("/v2/premiumIndex", params)

    async def get_recent_trades(self, symbol: str, limit: int = 100) -> List[Dict]:
        """Trades recientes."""
        return await self._public_get("/v2/trades", {"symbol": symbol, "limit": limit})

    async def get_klines(
        self, symbol: str, interval: str = "1m", limit: int = 500,
        start_time: Optional[int] = None, end_time: Optional[int] = None,
    ) -> List[List]:
        """Klines/velas históricas. Retorna arrays [[open_time, o, h, l, c, vol, ...]]."""
        params: Dict[str, Any] = {"symbol": symbol, "interval": interval, "limit": limit}
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time
        return await self._public_get("/v2/klines", params)

    async def get_ticker_price(self, symbol: Optional[str] = None) -> Any:
        """Último precio."""
        params = {"symbol": symbol} if symbol else {}
        return await self._public_get("/v2/ticker/price", params)

    async def get_open_interest(self, symbol: Optional[str] = None) -> Any:
        params = {"symbol": symbol} if symbol else {}
        return await self._public_get("/v2/openInterest", params)

    async def get_book_ticker(self, symbol: Optional[str] = None) -> Any:
        """Mejor bid/ask."""
        params = {"symbol": symbol} if symbol else {}
        return await self._public_get("/v2/ticker/bookTicker", params)

    # ── Market Snapshot (combina múltiples datos) ──────────────────

    async def get_market_snapshot(self, symbol: str) -> MarketSnapshot:
        """Construye snapshot completo del mercado para un símbolo."""
        import asyncio
        ticker_task = asyncio.ensure_future(self.get_ticker_24h(symbol))
        mark_task = asyncio.ensure_future(self.get_mark_price(symbol))
        ob_task = asyncio.ensure_future(self.get_orderbook(symbol, 20))
        oi_task = asyncio.ensure_future(self.get_open_interest(symbol))

        results = await asyncio.gather(
            ticker_task, mark_task, ob_task, oi_task, return_exceptions=True
        )
        # Unpack, replacing exceptions with safe defaults
        ticker = results[0] if not isinstance(results[0], Exception) else {}
        mark_data = results[1] if not isinstance(results[1], Exception) else {}
        orderbook = results[2] if not isinstance(results[2], Exception) else OrderBook(symbol=symbol, timestamp=time.time(), bids=[], asks=[])
        oi_data = results[3] if not isinstance(results[3], Exception) else {}

        # Normalizar campos (pueden ser dict o list)
        if isinstance(mark_data, list):
            mark_data = next((m for m in mark_data if m.get("symbol") == symbol), mark_data[0] if mark_data else {})
        if isinstance(ticker, list):
            ticker = next((t for t in ticker if t.get("symbol") == symbol), ticker[0] if ticker else {})
        if isinstance(oi_data, list):
            oi_data = next((o for o in oi_data if o.get("symbol") == symbol), oi_data[0] if oi_data else {})

        return MarketSnapshot(
            symbol=symbol,
            timestamp=time.time(),
            price=float(ticker.get("lastPrice", 0)),
            mark_price=float(mark_data.get("markPrice", 0)),
            index_price=float(mark_data.get("indexPrice", 0)),
            funding_rate=float(mark_data.get("lastFundingRate", 0)),
            volume_24h=float(ticker.get("quoteVolume", 0)),
            open_interest=float(oi_data.get("openInterest", 0)),
            orderbook=orderbook,
        )

    # ── Account (autenticado) ──────────────────────────────────────

    async def get_account(self) -> Dict:
        return await self._auth_request("GET", "/v2/account")

    async def get_balances(self) -> Dict:
        return await self._auth_request("GET", "/v2/balances")

    async def get_portfolio(self) -> Dict:
        return await self._auth_request("GET", "/v2/portfolio")

    async def get_positions(self, symbol: Optional[str] = None) -> List[Dict]:
        path = "/v2/positions"
        if symbol:
            path += f"?symbol={symbol}"
        return await self._auth_request("GET", path)

    # ── Orders (autenticado) ───────────────────────────────────────

    async def place_order(self, order: Order) -> Dict:
        """Envía una orden al exchange."""
        body: Dict[str, Any] = {
            "symbol": order.symbol,
            "side": order.side.value,
            "type": order.order_type.value,
            "quantity": str(order.quantity),
        }
        if order.price is not None:
            body["price"] = str(order.price)
        if order.stop_price is not None:
            body["stopPrice"] = str(order.stop_price)
        if order.time_in_force != TimeInForce.GTC:
            body["timeInForce"] = order.time_in_force.value
        if order.post_only:
            body["postOnly"] = True
        if order.reduce_only:
            body["reduceOnly"] = True
        if order.client_order_id:
            body["clientOrderId"] = order.client_order_id

        logger.info("placing_order", symbol=order.symbol, side=order.side.value,
                     type=order.order_type.value, qty=order.quantity, price=order.price)
        result = await self._auth_request("POST", "/v2/order", body)
        return result

    async def place_bracket_order(
        self, order: Order, tp_price: float, sl_price: float
    ) -> Dict:
        """Orden con take profit y stop loss (strategy order)."""
        body: Dict[str, Any] = {
            "symbol": order.symbol,
            "side": order.side.value,
            "type": order.order_type.value,
            "quantity": str(order.quantity),
        }
        if order.price is not None:
            body["price"] = str(order.price)
        body["takeProfitPrice"] = str(tp_price)
        body["stopLossPrice"] = str(sl_price)
        if order.client_order_id:
            body["clientOrderId"] = order.client_order_id

        return await self._auth_request("POST", "/v2/order/strategy", body)

    async def cancel_order(self, symbol: str, order_id: str) -> Dict:
        body = {"symbol": symbol, "orderId": order_id}
        return await self._auth_request("DELETE", "/v2/order/cancel", body)

    async def cancel_all_orders(self, symbol: Optional[str] = None) -> Dict:
        body = {}
        if symbol:
            body["symbol"] = symbol
        return await self._auth_request("DELETE", "/v2/order/cancel-all", body)

    async def replace_order(
        self, symbol: str, cancel_order_id: str, new_order: Order
    ) -> Dict:
        """Cancel atómico + nueva orden."""
        body: Dict[str, Any] = {
            "symbol": symbol,
            "cancelOrderId": cancel_order_id,
            "side": new_order.side.value,
            "type": new_order.order_type.value,
            "quantity": str(new_order.quantity),
        }
        if new_order.price is not None:
            body["price"] = str(new_order.price)
        return await self._auth_request("POST", "/v2/order/replace", body)

    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict]:
        path = "/v2/openOrders"
        if symbol:
            path += f"?symbol={symbol}"
        return await self._auth_request("GET", path)

    async def batch_orders(self, orders: List[Order]) -> Dict:
        """Envía batch de órdenes."""
        batch = []
        for o in orders:
            entry: Dict[str, Any] = {
                "symbol": o.symbol,
                "side": o.side.value,
                "type": o.order_type.value,
                "quantity": str(o.quantity),
            }
            if o.price is not None:
                entry["price"] = str(o.price)
            if o.client_order_id:
                entry["clientOrderId"] = o.client_order_id
            if o.post_only:
                entry["postOnly"] = True
            batch.append(entry)
        return await self._auth_request("POST", "/v2/orders/batch", {"orders": batch})

    # ── Trading Settings ───────────────────────────────────────────

    async def set_leverage(self, symbol: str, leverage: int) -> Dict:
        return await self._auth_request(
            "POST", "/v2/leverage", {"symbol": symbol, "leverage": leverage}
        )

    async def set_margin_mode(self, symbol: str, mode: str = "cross") -> Dict:
        return await self._auth_request(
            "POST", "/v2/marginMode", {"symbol": symbol, "marginMode": mode}
        )

    # ── History ────────────────────────────────────────────────────

    async def get_order_history(
        self, symbol: Optional[str] = None, limit: int = 100
    ) -> List[Dict]:
        params = f"?limit={limit}"
        if symbol:
            params += f"&symbol={symbol}"
        return await self._auth_request("GET", f"/v2/history/order{params}")

    async def get_fill_history(
        self, symbol: Optional[str] = None, limit: int = 100
    ) -> List[Dict]:
        params = f"?limit={limit}"
        if symbol:
            params += f"&symbol={symbol}"
        return await self._auth_request("GET", f"/v2/history/fill{params}")

    async def get_funding_history(
        self, symbol: Optional[str] = None, limit: int = 100
    ) -> List[Dict]:
        params = f"?limit={limit}"
        if symbol:
            params += f"&symbol={symbol}"
        return await self._auth_request("GET", f"/v2/history/funding{params}")

    # ── Platform Stats — Datos históricos (público) ────────────────

    async def get_coin_oi_history(
        self, symbol: str, interval: str = "1h", start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> Any:
        """Historial de Open Interest. interval: 10m,30m,1h,4h,1d."""
        params: Dict[str, Any] = {"symbol": symbol, "interval": interval}
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time
        return await self._public_get("/v1/stats/coin/history/open-interest", params)

    async def get_coin_funding_history(
        self, symbol: str, days: int = 30,
    ) -> Any:
        """Historial de funding rate (8h intervals). days: 1-90."""
        return await self._public_get(
            "/v1/stats/coin/history/funding",
            {"symbol": symbol, "days": min(days, 90)},
        )

    async def get_coin_basis_history(
        self, symbol: str, interval: str = "1h",
        start_time: Optional[int] = None, end_time: Optional[int] = None,
    ) -> Any:
        """Historial de mark price, index price y basis. interval: 10m,30m,1h,4h,1d."""
        params: Dict[str, Any] = {"symbol": symbol, "interval": interval}
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time
        return await self._public_get("/v1/stats/coin/history/basis", params)

    async def get_coin_spread_history(
        self, symbol: str, interval: str = "1h",
        start_time: Optional[int] = None, end_time: Optional[int] = None,
    ) -> Any:
        """Historial de bid-ask spread."""
        params: Dict[str, Any] = {"symbol": symbol, "interval": interval}
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time
        return await self._public_get("/v1/stats/coin/history/spread", params)

    async def get_coin_long_short_ratio(
        self, symbol: str, interval: str = "1h",
        start_time: Optional[int] = None, end_time: Optional[int] = None,
    ) -> Any:
        """Historial de long/short ratio."""
        params: Dict[str, Any] = {"symbol": symbol, "interval": interval}
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time
        return await self._public_get("/v1/stats/coin/history/long-short-ratio", params)

    async def get_coin_oi_marketcap_ratio(
        self, symbol: str, interval: str = "1h",
        start_time: Optional[int] = None, end_time: Optional[int] = None,
    ) -> Any:
        """Historial de OI / market cap ratio."""
        params: Dict[str, Any] = {"symbol": symbol, "interval": interval}
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time
        return await self._public_get("/v1/stats/coin/history/oi-marketcap-ratio", params)

    async def get_platform_stats(self) -> Any:
        """Resumen del dashboard de la plataforma."""
        return await self._public_get("/v1/stats/dashboard/summary")
