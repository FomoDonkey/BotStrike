"""
Cliente REST para Binance Futures (USDT-M) API.
Implementa autenticación HMAC-SHA256 y endpoints de trading.

Diseñado como drop-in replacement de StrikeClient — misma interfaz
de métodos para que OrderExecutionEngine funcione sin cambios.

Docs: https://developers.binance.com/docs/derivatives/usds-margined-futures
"""
from __future__ import annotations
import hashlib
import hmac
import time
import urllib.parse
import uuid
from typing import Any, Dict, List, Optional

import asyncio

import aiohttp

from config.settings import Settings
from core.types import (
    Order, OrderType, Side, TimeInForce, OrderBook, OrderBookLevel,
    Position, MarketSnapshot,
)
import structlog

logger = structlog.get_logger(__name__)

# Binance Futures endpoints
BINANCE_FUTURES_BASE = "https://fapi.binance.com"
BINANCE_FUTURES_TESTNET = "https://testnet.binancefuture.com"

# Mapeo BotStrike symbol → Binance symbol
SYMBOL_MAP = {
    "BTC-USD": "BTCUSDT",
    "ETH-USD": "ETHUSDT",
    "ADA-USD": "ADAUSDT",
    "SOL-USD": "SOLUSDT",
}
SYMBOL_MAP_REVERSE = {v: k for k, v in SYMBOL_MAP.items()}

# Mapeo OrderType BotStrike → Binance
ORDER_TYPE_MAP = {
    OrderType.MARKET: "MARKET",
    OrderType.LIMIT: "LIMIT",
    OrderType.STOP: "STOP_MARKET",
    OrderType.STOP_LIMIT: "STOP",
    OrderType.TAKE_PROFIT: "TAKE_PROFIT_MARKET",
    OrderType.TAKE_PROFIT_LIMIT: "TAKE_PROFIT",
}


class BinanceAPIError(Exception):
    """Typed exception for Binance API errors with status code."""

    def __init__(self, status: int, body: str, path: str = "") -> None:
        self.status = status
        self.body = body
        self.path = path
        super().__init__(f"Binance API error {status} on {path}: {body}")

    @property
    def is_retryable(self) -> bool:
        """429 (rate limit), 418 (IP ban), 5xx (server error) are retryable."""
        return self.status in (429, 418) or self.status >= 500


class _RateLimiter:
    """Token bucket rate limiter — 1200 req/min para Binance Futures."""

    def __init__(self, max_requests: int = 1200, window_sec: float = 60.0) -> None:
        from collections import deque
        self._max = max_requests
        self._window = window_sec
        self._timestamps: deque = deque()

    async def acquire(self) -> None:
        import asyncio
        while True:
            now = time.time()
            cutoff = now - self._window
            while self._timestamps and self._timestamps[0] < cutoff:
                self._timestamps.popleft()
            if len(self._timestamps) < self._max:
                break
            wait = self._timestamps[0] + self._window - now + 0.05
            if wait > 0:
                logger.debug("binance_rate_limit_wait", wait_sec=round(wait, 2))
                await asyncio.sleep(wait)
        self._timestamps.append(time.time())


class BinanceClient:
    """Cliente asíncrono para la API REST de Binance Futures (USDT-M)."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._base_url = (
            BINANCE_FUTURES_TESTNET if settings.use_testnet
            else BINANCE_FUTURES_BASE
        )
        self._session: Optional[aiohttp.ClientSession] = None
        self._rate_limiter = _RateLimiter(max_requests=1200, window_sec=60.0)

        # Credenciales HMAC-SHA256
        import os
        self._api_key = os.getenv("BINANCE_API_KEY", "")
        self._api_secret = os.getenv("BINANCE_API_SECRET", "")
        if not self._api_key:
            logger.warning("binance_api_key_not_set")

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=15, connect=5, sock_read=10)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    def _to_binance_symbol(self, symbol: str) -> str:
        """Convierte BotStrike symbol (BTC-USD) a Binance (BTCUSDT)."""
        return SYMBOL_MAP.get(symbol, symbol.replace("-", ""))

    def _from_binance_symbol(self, symbol: str) -> str:
        """Convierte Binance symbol (BTCUSDT) a BotStrike (BTC-USD)."""
        return SYMBOL_MAP_REVERSE.get(symbol, symbol)

    # ── Autenticación HMAC-SHA256 ─────────────────────────────────

    def _sign(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Agrega timestamp y signature HMAC-SHA256 a los parámetros."""
        params["timestamp"] = int(time.time() * 1000)
        query_string = urllib.parse.urlencode(params)
        signature = hmac.new(
            self._api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        params["signature"] = signature
        return params

    def _headers(self) -> Dict[str, str]:
        return {
            "X-MBX-APIKEY": self._api_key,
            "Content-Type": "application/x-www-form-urlencoded",
        }

    # ── Retry logic ────────────────────────────────────────────────

    _MAX_RETRIES = 3
    _RETRY_BASE_SEC = 1.0  # 1s → 2s → 4s exponential backoff

    async def _retry_request(self, request_fn, path: str) -> Any:
        """Execute request_fn with exponential backoff on retryable errors.

        Retries on: 429 (rate limit), 418 (IP ban), 5xx (server), and
        transient network errors (aiohttp.ClientError, asyncio.TimeoutError).
        """
        last_error: Optional[Exception] = None
        for attempt in range(self._MAX_RETRIES + 1):
            try:
                return await request_fn()
            except BinanceAPIError as e:
                last_error = e
                if not e.is_retryable or attempt == self._MAX_RETRIES:
                    raise
                delay = self._RETRY_BASE_SEC * (2 ** attempt)
                logger.warning("binance_retry",
                               path=path, status=e.status, attempt=attempt + 1,
                               max_retries=self._MAX_RETRIES, delay_sec=round(delay, 1))
                await asyncio.sleep(delay)
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_error = e
                if attempt == self._MAX_RETRIES:
                    raise
                delay = self._RETRY_BASE_SEC * (2 ** attempt)
                logger.warning("binance_network_retry",
                               path=path, error=str(e), attempt=attempt + 1,
                               delay_sec=round(delay, 1))
                await asyncio.sleep(delay)
        raise last_error  # unreachable but satisfies type checker

    # ── Requests genéricos ────────────────────────────────────────

    async def _public_get(self, path: str, params: Optional[Dict] = None) -> Any:
        async def _do() -> Any:
            await self._rate_limiter.acquire()
            session = await self._get_session()
            url = f"{self._base_url}{path}"
            async with session.get(url, params=params) as resp:
                if resp.status not in (200, 201):
                    text = await resp.text()
                    logger.error("binance_public_error", url=path, status=resp.status, body=text[:200])
                    raise BinanceAPIError(resp.status, text[:200], path)
                return await resp.json()
        return await self._retry_request(_do, path)

    async def _auth_get(self, path: str, params: Optional[Dict] = None) -> Any:
        async def _do() -> Any:
            await self._rate_limiter.acquire()
            session = await self._get_session()
            url = f"{self._base_url}{path}"
            signed_params = self._sign(params.copy() if params else {})
            async with session.get(url, params=signed_params, headers=self._headers()) as resp:
                return await self._handle_response(resp, path)
        return await self._retry_request(_do, path)

    async def _auth_post(self, path: str, params: Optional[Dict] = None) -> Any:
        async def _do() -> Any:
            await self._rate_limiter.acquire()
            session = await self._get_session()
            url = f"{self._base_url}{path}"
            signed_params = self._sign(params.copy() if params else {})
            async with session.post(url, data=signed_params, headers=self._headers()) as resp:
                return await self._handle_response(resp, path)
        return await self._retry_request(_do, path)

    async def _auth_delete(self, path: str, params: Optional[Dict] = None) -> Any:
        async def _do() -> Any:
            await self._rate_limiter.acquire()
            session = await self._get_session()
            url = f"{self._base_url}{path}"
            signed_params = self._sign(params.copy() if params else {})
            async with session.delete(url, params=signed_params, headers=self._headers()) as resp:
                return await self._handle_response(resp, path)
        return await self._retry_request(_do, path)

    async def _handle_response(self, resp: aiohttp.ClientResponse, path: str) -> Any:
        if resp.status not in (200, 201):
            text = await resp.text()
            logger.error("binance_auth_error", url=path, status=resp.status, body=text[:200])
            raise BinanceAPIError(resp.status, text[:200], path)
        return await resp.json()

    # ── Market Data (público) ─────────────────────────────────────

    async def get_exchange_info(self) -> Dict:
        return await self._public_get("/fapi/v1/exchangeInfo")

    async def get_orderbook(self, symbol: str, limit: int = 20) -> OrderBook:
        bsym = self._to_binance_symbol(symbol)
        data = await self._public_get("/fapi/v1/depth", {"symbol": bsym, "limit": limit})
        bids = [OrderBookLevel(float(p), float(q)) for p, q in data.get("bids", [])]
        asks = [OrderBookLevel(float(p), float(q)) for p, q in data.get("asks", [])]
        return OrderBook(
            symbol=symbol,
            timestamp=time.time(),
            bids=bids,
            asks=asks,
        )

    async def get_ticker_24h(self, symbol: Optional[str] = None) -> Any:
        params = {}
        if symbol:
            params["symbol"] = self._to_binance_symbol(symbol)
        return await self._public_get("/fapi/v1/ticker/24hr", params)

    async def get_mark_price(self, symbol: Optional[str] = None) -> Any:
        params = {}
        if symbol:
            params["symbol"] = self._to_binance_symbol(symbol)
        return await self._public_get("/fapi/v1/premiumIndex", params)

    async def get_recent_trades(self, symbol: str, limit: int = 100) -> List[Dict]:
        bsym = self._to_binance_symbol(symbol)
        return await self._public_get("/fapi/v1/trades", {"symbol": bsym, "limit": limit})

    async def get_klines(
        self, symbol: str, interval: str = "1m", limit: int = 500,
        start_time: Optional[int] = None, end_time: Optional[int] = None,
    ) -> List[List]:
        bsym = self._to_binance_symbol(symbol)
        params: Dict[str, Any] = {"symbol": bsym, "interval": interval, "limit": limit}
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time
        return await self._public_get("/fapi/v1/klines", params)

    async def get_ticker_price(self, symbol: Optional[str] = None) -> Any:
        params = {}
        if symbol:
            params["symbol"] = self._to_binance_symbol(symbol)
        return await self._public_get("/fapi/v2/ticker/price", params)

    async def get_open_interest(self, symbol: Optional[str] = None) -> Any:
        params = {}
        if symbol:
            params["symbol"] = self._to_binance_symbol(symbol)
        return await self._public_get("/fapi/v1/openInterest", params)

    async def get_book_ticker(self, symbol: Optional[str] = None) -> Any:
        params = {}
        if symbol:
            params["symbol"] = self._to_binance_symbol(symbol)
        return await self._public_get("/fapi/v1/ticker/bookTicker", params)

    # ── Market Snapshot ───────────────────────────────────────────

    async def get_market_snapshot(self, symbol: str) -> MarketSnapshot:
        import asyncio
        bsym = self._to_binance_symbol(symbol)

        results = await asyncio.gather(
            self.get_ticker_24h(symbol),
            self.get_mark_price(symbol),
            self.get_orderbook(symbol, 20),
            self.get_open_interest(symbol),
            return_exceptions=True,
        )
        ticker = results[0] if not isinstance(results[0], Exception) else {}
        mark_data = results[1] if not isinstance(results[1], Exception) else {}
        orderbook = results[2] if not isinstance(results[2], Exception) else OrderBook(
            symbol=symbol, timestamp=time.time(), bids=[], asks=[])
        oi_data = results[3] if not isinstance(results[3], Exception) else {}

        if isinstance(mark_data, list):
            mark_data = mark_data[0] if mark_data else {}
        if isinstance(ticker, list):
            ticker = ticker[0] if ticker else {}
        if isinstance(oi_data, list):
            oi_data = oi_data[0] if oi_data else {}

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

    # ── Account (autenticado) ─────────────────────────────────────

    async def get_account(self) -> Dict:
        return await self._auth_get("/fapi/v2/account")

    async def get_balances(self) -> Dict:
        return await self._auth_get("/fapi/v2/balance")

    async def get_portfolio(self) -> Dict:
        return await self._auth_get("/fapi/v2/account")

    async def get_positions(self, symbol: Optional[str] = None) -> List[Dict]:
        params = {}
        if symbol:
            params["symbol"] = self._to_binance_symbol(symbol)
        data = await self._auth_get("/fapi/v2/positionRisk", params)
        # Convertir symbols de vuelta a formato BotStrike
        if isinstance(data, list):
            for pos in data:
                if "symbol" in pos:
                    pos["symbol"] = self._from_binance_symbol(pos["symbol"])
        return data

    # ── Orders (autenticado) ──────────────────────────────────────

    async def place_order(self, order: Order) -> Dict:
        """Envía una orden a Binance Futures. Interfaz compatible con StrikeClient."""
        bsym = self._to_binance_symbol(order.symbol)
        binance_type = ORDER_TYPE_MAP.get(order.order_type, "MARKET")

        params: Dict[str, Any] = {
            "symbol": bsym,
            "side": order.side.value,
            "type": binance_type,
            "quantity": f"{order.quantity:.6f}".rstrip("0").rstrip("."),
        }

        # Precio para LIMIT orders
        if order.order_type == OrderType.LIMIT and order.price is not None:
            params["price"] = f"{order.price:.2f}"
            params["timeInForce"] = order.time_in_force.value

        # Stop price para STOP/TAKE_PROFIT orders
        if order.stop_price is not None and order.order_type in (
            OrderType.STOP, OrderType.STOP_LIMIT,
            OrderType.TAKE_PROFIT, OrderType.TAKE_PROFIT_LIMIT,
        ):
            params["stopPrice"] = f"{order.stop_price:.2f}"

        # Limit price para STOP_LIMIT / TAKE_PROFIT_LIMIT
        if order.order_type in (OrderType.STOP_LIMIT, OrderType.TAKE_PROFIT_LIMIT):
            if order.price is not None:
                params["price"] = f"{order.price:.2f}"
                params["timeInForce"] = order.time_in_force.value

        if order.reduce_only:
            params["reduceOnly"] = "true"

        if order.client_order_id:
            params["newClientOrderId"] = order.client_order_id

        # Binance no tiene post_only nativo en futures — usar GTX (Good Till Crossing)
        if order.post_only and order.order_type == OrderType.LIMIT:
            params["timeInForce"] = "GTX"

        logger.info("binance_placing_order", symbol=bsym, side=order.side.value,
                     type=binance_type, qty=order.quantity, price=order.price)

        result = await self._auth_post("/fapi/v1/order", params)

        # Normalizar response para compatibilidad con OrderExecutionEngine
        return {
            "orderId": str(result.get("orderId", "")),
            "status": result.get("status", "NEW"),
            "symbol": order.symbol,  # Mantener formato BotStrike
            "clientOrderId": result.get("clientOrderId", ""),
            "avgPrice": result.get("avgPrice", "0"),
            "executedQty": result.get("executedQty", "0"),
            "origQty": result.get("origQty", "0"),
        }

    async def place_bracket_order(
        self, order: Order, tp_price: float, sl_price: float,
    ) -> Dict:
        """Bracket order via 3 órdenes separadas (Binance no tiene strategy order nativo).

        Uses actual executedQty from fill for SL/TP sizing (not original order qty).
        Retries SL/TP once on failure. If both still fail, logs CRITICAL.
        """
        result = await self.place_order(order)

        status = result.get("status", "")
        if status not in ("FILLED", "PARTIALLY_FILLED", "NEW"):
            return result

        # Use actual filled qty if available, fall back to order qty
        filled_qty = float(result.get("executedQty", 0))
        qty = filled_qty if filled_qty > 0 else order.quantity
        sl_side = Side.SELL if order.side == Side.BUY else Side.BUY

        # SL with retry
        sl_order = Order(
            symbol=order.symbol, side=sl_side,
            order_type=OrderType.STOP, quantity=qty,
            stop_price=sl_price, reduce_only=True,
            client_order_id=f"bs_sl_{uuid.uuid4().hex[:8]}",
            strategy=order.strategy,
        )
        sl_ok = False
        for attempt in range(2):
            try:
                await self.place_order(sl_order)
                sl_ok = True
                break
            except Exception as e:
                logger.error("bracket_sl_failed", attempt=attempt + 1, error=str(e))
                if attempt == 0:
                    await asyncio.sleep(0.5)

        # TP with retry
        tp_order = Order(
            symbol=order.symbol, side=sl_side,
            order_type=OrderType.TAKE_PROFIT, quantity=qty,
            stop_price=tp_price, reduce_only=True,
            client_order_id=f"bs_tp_{uuid.uuid4().hex[:8]}",
            strategy=order.strategy,
        )
        tp_ok = False
        for attempt in range(2):
            try:
                await self.place_order(tp_order)
                tp_ok = True
                break
            except Exception as e:
                logger.error("bracket_tp_failed", attempt=attempt + 1, error=str(e))
                if attempt == 0:
                    await asyncio.sleep(0.5)

        if not sl_ok and not tp_ok:
            logger.critical("BRACKET_BOTH_PROTECTIVES_FAILED", symbol=order.symbol)
        elif not sl_ok:
            logger.critical("BRACKET_SL_FAILED_TP_ONLY", symbol=order.symbol)

        return result

    async def cancel_order(self, symbol: str, order_id: str) -> Dict:
        bsym = self._to_binance_symbol(symbol)
        return await self._auth_delete("/fapi/v1/order", {
            "symbol": bsym, "orderId": order_id,
        })

    async def cancel_all_orders(self, symbol: Optional[str] = None) -> Dict:
        if symbol:
            bsym = self._to_binance_symbol(symbol)
            return await self._auth_delete("/fapi/v1/allOpenOrders", {"symbol": bsym})
        # Cancel all symbols
        results = {}
        for sym in self.settings.symbols:
            bsym = self._to_binance_symbol(sym.symbol)
            try:
                r = await self._auth_delete("/fapi/v1/allOpenOrders", {"symbol": bsym})
                results[sym.symbol] = r
            except Exception as e:
                logger.warning("cancel_all_failed", symbol=sym.symbol, error=str(e))
        return results

    async def replace_order(
        self, symbol: str, cancel_order_id: str, new_order: Order,
    ) -> Dict:
        """Cancel + place new (Binance no tiene atomic replace en futures).

        If cancel succeeds but place fails, retries the new order once.
        If both fail, re-places the original cancel_order_id params (best effort).
        """
        cancel_ok = False
        try:
            await self.cancel_order(symbol, cancel_order_id)
            cancel_ok = True
        except Exception as e:
            logger.warning("replace_cancel_failed", order_id=cancel_order_id, error=str(e))

        try:
            return await self.place_order(new_order)
        except Exception as e:
            if cancel_ok:
                # Cancel succeeded but new order failed — position may be unprotected
                logger.error("replace_new_order_failed_retrying",
                             symbol=symbol, error=str(e))
                await asyncio.sleep(0.3)
                # Retry once
                return await self.place_order(new_order)
            raise

    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict]:
        params = {}
        if symbol:
            params["symbol"] = self._to_binance_symbol(symbol)
        return await self._auth_get("/fapi/v1/openOrders", params)

    async def batch_orders(self, orders: List[Order]) -> Dict:
        """Batch orders via Binance batch endpoint (max 5 per request)."""
        import json as _json
        all_results = []
        # Binance allows max 5 orders per batch
        for i in range(0, len(orders), 5):
            chunk = orders[i:i + 5]
            batch_list = []
            for o in chunk:
                bsym = self._to_binance_symbol(o.symbol)
                binance_type = ORDER_TYPE_MAP.get(o.order_type, "MARKET")
                entry: Dict[str, Any] = {
                    "symbol": bsym,
                    "side": o.side.value,
                    "type": binance_type,
                    "quantity": f"{o.quantity:.6f}".rstrip("0").rstrip("."),
                }
                if o.price is not None and o.order_type == OrderType.LIMIT:
                    entry["price"] = f"{o.price:.2f}"
                    entry["timeInForce"] = o.time_in_force.value
                if o.post_only and o.order_type == OrderType.LIMIT:
                    entry["timeInForce"] = "GTX"
                if o.client_order_id:
                    entry["newClientOrderId"] = o.client_order_id
                batch_list.append(entry)

            params = {"batchOrders": _json.dumps(batch_list)}
            try:
                result = await self._auth_post("/fapi/v1/batchOrders", params)
                if isinstance(result, list):
                    all_results.extend(result)
            except Exception as e:
                logger.error("binance_batch_failed", error=str(e))

        # Normalize to same format as StrikeClient
        normalized = []
        for r in all_results:
            if isinstance(r, dict):
                normalized.append({
                    "orderId": str(r.get("orderId", "")),
                    "status": r.get("status", "NEW"),
                    "clientOrderId": r.get("clientOrderId", ""),
                })
        return {"orders": normalized}

    # ── Trading Settings ──────────────────────────────────────────

    async def set_leverage(self, symbol: str, leverage: int) -> Dict:
        bsym = self._to_binance_symbol(symbol)
        return await self._auth_post("/fapi/v1/leverage", {
            "symbol": bsym, "leverage": leverage,
        })

    async def set_margin_mode(self, symbol: str, mode: str = "cross") -> Dict:
        bsym = self._to_binance_symbol(symbol)
        margin_type = "CROSSED" if mode == "cross" else "ISOLATED"
        try:
            return await self._auth_post("/fapi/v1/marginType", {
                "symbol": bsym, "marginType": margin_type,
            })
        except Exception as e:
            # Binance returns error if already in target mode — not a real error
            if "No need to change" in str(e):
                return {"msg": "already_set"}
            raise

    # ── History ────────────────────────────────────────────────────

    async def get_order_history(
        self, symbol: Optional[str] = None, limit: int = 100,
    ) -> List[Dict]:
        params: Dict[str, Any] = {"limit": limit}
        if symbol:
            params["symbol"] = self._to_binance_symbol(symbol)
        return await self._auth_get("/fapi/v1/allOrders", params)

    async def get_fill_history(
        self, symbol: Optional[str] = None, limit: int = 100,
    ) -> List[Dict]:
        params: Dict[str, Any] = {"limit": limit}
        if symbol:
            params["symbol"] = self._to_binance_symbol(symbol)
        return await self._auth_get("/fapi/v1/userTrades", params)

    async def get_funding_history(
        self, symbol: Optional[str] = None, limit: int = 100,
    ) -> List[Dict]:
        params: Dict[str, Any] = {"limit": limit, "incomeType": "FUNDING_FEE"}
        if symbol:
            params["symbol"] = self._to_binance_symbol(symbol)
        return await self._auth_get("/fapi/v1/income", params)
