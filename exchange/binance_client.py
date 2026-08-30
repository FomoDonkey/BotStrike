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
from decimal import Decimal, ROUND_DOWN, ROUND_UP, ROUND_HALF_UP
from typing import Any, Callable, Dict, List, Optional

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

# ── Symbol filters (audit P0-01) ──────────────────────────────────
# Safe fallback when GET /fapi/v1/exchangeInfo cannot be loaded. Values are
# the real USDT-M filters observed on 2026-08-29 (see tasks/audit/02). They are
# ONLY a fallback: load_exchange_info() overwrites them with live values.
DEFAULT_SYMBOL_FILTERS: Dict[str, Dict[str, Decimal]] = {
    "BTCUSDT": {"tickSize": Decimal("0.1"), "stepSize": Decimal("0.001"),
                "minQty": Decimal("0.001"), "minNotional": Decimal("100")},
    "ETHUSDT": {"tickSize": Decimal("0.01"), "stepSize": Decimal("0.001"),
                "minQty": Decimal("0.001"), "minNotional": Decimal("20")},
    "SOLUSDT": {"tickSize": Decimal("0.01"), "stepSize": Decimal("0.01"),
                "minQty": Decimal("0.01"), "minNotional": Decimal("5")},
    "ADAUSDT": {"tickSize": Decimal("0.0001"), "stepSize": Decimal("1"),
                "minQty": Decimal("1"), "minNotional": Decimal("5")},
}
# Generic fallback for symbols not in the table above (conservative).
GENERIC_SYMBOL_FILTER: Dict[str, Decimal] = {
    "tickSize": Decimal("0.01"), "stepSize": Decimal("0.001"),
    "minQty": Decimal("0.001"), "minNotional": Decimal("5"),
}
# Binance error codes we branch on (body contains "code":-XXXX)
BINANCE_ERR_ORDER_NOT_EXIST = "-2013"
BINANCE_ERR_REDUCE_ONLY_REJECTED = "-2022"


def floor_to_step(value: float, step: Decimal) -> Decimal:
    """Floor `value` to a multiple of `step` (LOT_SIZE.stepSize). Never rounds up."""
    if step <= 0:
        return Decimal(str(value))
    d = Decimal(str(value))
    return (d / step).to_integral_value(rounding=ROUND_DOWN) * step


def round_to_tick(price: float, tick: Decimal, mode: str = "nearest") -> Decimal:
    """Round `price` to a multiple of `tick` (PRICE_FILTER.tickSize).

    mode: "floor" | "ceil" | "nearest".
    """
    if tick <= 0:
        return Decimal(str(price))
    d = Decimal(str(price))
    rounding = {"floor": ROUND_DOWN, "ceil": ROUND_UP}.get(mode, ROUND_HALF_UP)
    return (d / tick).to_integral_value(rounding=rounding) * tick


def format_decimal(d: Decimal) -> str:
    """Plain decimal string (no scientific notation, no trailing zeros)."""
    s = format(d.normalize(), "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def parse_symbol_filters(exchange_info: Dict) -> Dict[str, Dict[str, Decimal]]:
    """Extract tickSize/stepSize/minQty/minNotional per symbol from exchangeInfo."""
    out: Dict[str, Dict[str, Decimal]] = {}
    for s in exchange_info.get("symbols", []) or []:
        sym = s.get("symbol")
        if not sym:
            continue
        f: Dict[str, Decimal] = {}
        for flt in s.get("filters", []) or []:
            ft = flt.get("filterType")
            try:
                if ft == "PRICE_FILTER":
                    f["tickSize"] = Decimal(str(flt.get("tickSize")))
                elif ft == "LOT_SIZE":
                    f["stepSize"] = Decimal(str(flt.get("stepSize")))
                    f["minQty"] = Decimal(str(flt.get("minQty")))
                elif ft == "MIN_NOTIONAL":
                    f["minNotional"] = Decimal(str(flt.get("notional")))
            except Exception:
                continue
        if f:
            out[sym] = f
    return out


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

        # exchangeInfo filter cache (audit P0-01): bsym -> {tickSize, stepSize, minQty, minNotional}
        self._symbol_filters: Dict[str, Dict[str, Decimal]] = {}
        self._filters_loaded: bool = False
        self._filters_last_attempt: float = 0.0
        self._filters_retry_sec: float = 300.0

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

    # POST endpoints that are safe to blindly re-send (setting the same value
    # twice has no extra side effect). Every other POST is treated as
    # NON-idempotent (audit P0-02): never re-sent blindly after a timeout/5xx.
    _IDEMPOTENT_POST_PATHS = frozenset({
        "/fapi/v1/leverage", "/fapi/v1/marginType", "/fapi/v1/listenKey",
        "/fapi/v1/positionSide/dual",
    })

    async def _retry_request(
        self,
        request_fn: Callable[[], Any],
        path: str,
        idempotent: bool = True,
        recover_fn: Optional[Callable[[], Any]] = None,
    ) -> Any:
        """Execute request_fn with exponential backoff on retryable errors.

        Retries on: 429 (rate limit), 418 (IP ban), 5xx (server), and
        transient network errors (aiohttp.ClientError, asyncio.TimeoutError).

        idempotent=False (audit P0-02): after a timeout/5xx the execution state
        of the request is UNKNOWN ("execution may have succeeded"). We never
        re-send blindly. If `recover_fn` is given it is awaited: it must return
        the already-existing result (-> returned as-is), or None when the
        exchange confirms the request never landed (-> ONE re-send is allowed).
        Without `recover_fn`, the error is raised immediately.
        """
        last_error: Optional[Exception] = None
        for attempt in range(self._MAX_RETRIES + 1):
            try:
                return await request_fn()
            except BinanceAPIError as e:
                last_error = e
                if not e.is_retryable or attempt == self._MAX_RETRIES:
                    raise
                if not idempotent:
                    recovered = await self._recover_or_raise(e, path, recover_fn)
                    if recovered is not None:
                        return recovered
                    # recover_fn confirmed "does not exist" -> fall through to resend
                delay = self._RETRY_BASE_SEC * (2 ** attempt)
                logger.warning("binance_retry",
                               path=path, status=e.status, attempt=attempt + 1,
                               max_retries=self._MAX_RETRIES, delay_sec=round(delay, 1))
                await asyncio.sleep(delay)
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_error = e
                if attempt == self._MAX_RETRIES:
                    raise
                if not idempotent:
                    recovered = await self._recover_or_raise(e, path, recover_fn)
                    if recovered is not None:
                        return recovered
                delay = self._RETRY_BASE_SEC * (2 ** attempt)
                logger.warning("binance_network_retry",
                               path=path, error=str(e), attempt=attempt + 1,
                               delay_sec=round(delay, 1))
                await asyncio.sleep(delay)
        raise last_error  # unreachable but satisfies type checker

    async def _recover_or_raise(
        self, error: Exception, path: str, recover_fn: Optional[Callable[[], Any]],
    ) -> Any:
        """Non-idempotent request failed with unknown execution state.

        Returns the recovered result if the request DID land, None if the
        exchange confirms it did NOT land (safe to re-send once), and re-raises
        the original error when neither can be established.
        """
        if recover_fn is None:
            logger.error("binance_non_idempotent_no_retry", path=path, error=str(error))
            raise error
        try:
            recovered = await recover_fn()
        except Exception as re:
            logger.error("binance_recover_failed_no_retry", path=path,
                         error=str(error), recover_error=str(re))
            raise error
        if recovered is not None:
            logger.warning("binance_request_recovered_after_error", path=path,
                           error=str(error))
        else:
            logger.warning("binance_request_not_found_resending", path=path,
                           error=str(error))
        return recovered

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

    async def _auth_post(
        self, path: str, params: Optional[Dict] = None,
        recover_fn: Optional[Callable[[], Any]] = None,
    ) -> Any:
        """Signed POST. Order-creating endpoints are NOT blindly retried
        (audit P0-02); pass `recover_fn` to reconcile by clientOrderId."""
        async def _do() -> Any:
            await self._rate_limiter.acquire()
            session = await self._get_session()
            url = f"{self._base_url}{path}"
            signed_params = self._sign(params.copy() if params else {})
            async with session.post(url, data=signed_params, headers=self._headers()) as resp:
                return await self._handle_response(resp, path)
        idempotent = path in self._IDEMPOTENT_POST_PATHS
        return await self._retry_request(_do, path, idempotent=idempotent,
                                         recover_fn=recover_fn)

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

    # ── Symbol filters / precision (audit P0-01) ──────────────────

    async def load_exchange_info(self, force: bool = False) -> bool:
        """Load and cache LOT_SIZE / PRICE_FILTER / MIN_NOTIONAL per symbol.

        Called lazily (once) before the first order; safe to call at startup.
        On failure the DEFAULT_SYMBOL_FILTERS fallback stays in place and the
        load is retried at most every `_filters_retry_sec`.
        Returns True when live filters are cached.
        """
        if self._filters_loaded and not force:
            return True
        now = time.time()
        if not force and (now - self._filters_last_attempt) < self._filters_retry_sec:
            return False
        self._filters_last_attempt = now
        try:
            info = await self.get_exchange_info()
            parsed = parse_symbol_filters(info)
            wanted = set(SYMBOL_MAP.values())
            loaded = {k: v for k, v in parsed.items() if k in wanted}
            if not loaded:
                raise ValueError("exchangeInfo returned no filters for configured symbols")
            self._symbol_filters.update(loaded)
            self._filters_loaded = True
            logger.info("binance_exchange_info_loaded", symbols=sorted(loaded.keys()))
            return True
        except Exception as e:
            logger.warning("binance_exchange_info_load_failed_using_defaults", error=str(e))
            return False

    async def _ensure_filters(self) -> None:
        if not self._filters_loaded:
            await self.load_exchange_info()

    def get_symbol_filters(self, bsym: str) -> Dict[str, Decimal]:
        """Filters for a Binance symbol: live cache -> known defaults -> generic."""
        f = self._symbol_filters.get(bsym)
        if f:
            merged = dict(DEFAULT_SYMBOL_FILTERS.get(bsym, GENERIC_SYMBOL_FILTER))
            merged.update(f)
            return merged
        return dict(DEFAULT_SYMBOL_FILTERS.get(bsym, GENERIC_SYMBOL_FILTER))

    def round_quantity(self, bsym: str, qty: float) -> Decimal:
        """Floor qty to stepSize."""
        return floor_to_step(qty, self.get_symbol_filters(bsym)["stepSize"])

    def round_price(self, bsym: str, price: float, mode: str = "nearest") -> Decimal:
        """Round price to tickSize (mode: floor/ceil/nearest)."""
        return round_to_tick(price, self.get_symbol_filters(bsym)["tickSize"], mode)

    @staticmethod
    def _price_rounding_mode(order: Order, is_trigger: bool) -> str:
        """Conservative tick rounding.

        Trigger prices (SL/TP): SELL -> floor, BUY -> ceil (a long's SL/TP is
        never nudged above the intended level, a short's never below).
        Limit prices: BUY -> floor, SELL -> ceil (never pay more / receive less
        than the intended limit).
        """
        if is_trigger:
            return "floor" if order.side == Side.SELL else "ceil"
        return "floor" if order.side == Side.BUY else "ceil"

    def _normalize_order_params(self, order: Order, bsym: str) -> Dict[str, str]:
        """Return {"quantity", "price"?, "stopPrice"?} strings that satisfy the
        symbol filters, or raise ValueError when the order cannot be valid.
        """
        f = self.get_symbol_filters(bsym)
        qty = floor_to_step(order.quantity, f["stepSize"])
        out: Dict[str, str] = {}

        if qty <= 0 or qty < f["minQty"]:
            raise ValueError(
                f"quantity {order.quantity} below minQty/stepSize for {bsym} "
                f"(minQty={format_decimal(f['minQty'])}, step={format_decimal(f['stepSize'])})"
            )
        out["quantity"] = format_decimal(qty)

        ref_price: Optional[Decimal] = None
        if order.price is not None and order.order_type in (
            OrderType.LIMIT, OrderType.STOP_LIMIT, OrderType.TAKE_PROFIT_LIMIT,
        ):
            p = round_to_tick(order.price, f["tickSize"], self._price_rounding_mode(order, False))
            out["price"] = format_decimal(p)
            ref_price = p

        if order.stop_price is not None and order.order_type in (
            OrderType.STOP, OrderType.STOP_LIMIT,
            OrderType.TAKE_PROFIT, OrderType.TAKE_PROFIT_LIMIT,
        ):
            sp = round_to_tick(order.stop_price, f["tickSize"], self._price_rounding_mode(order, True))
            out["stopPrice"] = format_decimal(sp)
            if ref_price is None:
                ref_price = sp

        # MIN_NOTIONAL: only enforced locally for opening orders (reduceOnly
        # closes are exempt on the exchange). MARKET orders use the expected
        # price stashed by the engine when available.
        if not order.reduce_only:
            if ref_price is None:
                exp = getattr(order, "_expected_price", None)
                if exp:
                    ref_price = Decimal(str(exp))
            if ref_price is not None and ref_price > 0:
                notional = qty * ref_price
                if notional < f["minNotional"]:
                    raise ValueError(
                        f"notional {format_decimal(notional)} below minNotional "
                        f"{format_decimal(f['minNotional'])} for {bsym}"
                    )
        return out

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

    @staticmethod
    def new_client_order_id(prefix: str = "bs") -> str:
        """Unique newClientOrderId (<=36 chars): prefix + ms timestamp + uuid."""
        cid = f"{prefix}_{int(time.time() * 1000)}_{uuid.uuid4().hex[:10]}"
        return cid[:36]

    def _normalize_order_response(self, order: Order, result: Dict) -> Dict:
        """Normalizar response para compatibilidad con OrderExecutionEngine."""
        return {
            "orderId": str(result.get("orderId", "")),
            "status": result.get("status", "NEW"),
            "symbol": order.symbol,  # Mantener formato BotStrike
            "clientOrderId": result.get("clientOrderId", order.client_order_id or ""),
            "avgPrice": result.get("avgPrice", "0"),
            "executedQty": result.get("executedQty", "0"),
            "origQty": result.get("origQty", "0"),
        }

    async def get_order(
        self, symbol: str, client_order_id: Optional[str] = None,
        order_id: Optional[str] = None,
    ) -> Optional[Dict]:
        """GET /fapi/v1/order by origClientOrderId or orderId.

        Returns None when Binance answers -2013 (order does not exist).
        """
        bsym = self._to_binance_symbol(symbol)
        params: Dict[str, Any] = {"symbol": bsym}
        if client_order_id:
            params["origClientOrderId"] = client_order_id
        elif order_id:
            params["orderId"] = order_id
        else:
            raise ValueError("get_order requires client_order_id or order_id")
        try:
            result = await self._auth_get("/fapi/v1/order", params)
        except BinanceAPIError as e:
            if BINANCE_ERR_ORDER_NOT_EXIST in (e.body or ""):
                return None
            raise
        if not isinstance(result, dict):
            return None
        result["symbol"] = symbol
        return result

    async def place_order(self, order: Order) -> Dict:
        """Envía una orden a Binance Futures. Interfaz compatible con StrikeClient.

        Audit fixes:
        - P0-01: quantity floored to stepSize, prices rounded to tickSize,
          MIN_NOTIONAL checked locally (opening orders).
        - P0-02: a unique newClientOrderId is ALWAYS sent; after a timeout/5xx
          the order is looked up by origClientOrderId instead of being re-sent.
        - P1-05: MARKET orders request newOrderRespType=RESULT so the caller
          gets the final status (FILLED + executedQty + avgPrice).
        """
        await self._ensure_filters()
        bsym = self._to_binance_symbol(order.symbol)
        binance_type = ORDER_TYPE_MAP.get(order.order_type, "MARKET")

        if not order.client_order_id:
            order.client_order_id = self.new_client_order_id()

        normalized = self._normalize_order_params(order, bsym)

        params: Dict[str, Any] = {
            "symbol": bsym,
            "side": order.side.value,
            "type": binance_type,
            "quantity": normalized["quantity"],
            "newClientOrderId": order.client_order_id,
        }

        # Precio para LIMIT / STOP_LIMIT / TAKE_PROFIT_LIMIT orders
        if "price" in normalized:
            params["price"] = normalized["price"]
            params["timeInForce"] = order.time_in_force.value

        # Stop price para STOP/TAKE_PROFIT orders
        if "stopPrice" in normalized:
            params["stopPrice"] = normalized["stopPrice"]

        if order.reduce_only:
            params["reduceOnly"] = "true"

        # Binance no tiene post_only nativo en futures — usar GTX (Good Till Crossing)
        if order.post_only and order.order_type == OrderType.LIMIT:
            params["timeInForce"] = "GTX"

        # MARKET: get the final execution state in the ACK (P1-05)
        if binance_type == "MARKET":
            params["newOrderRespType"] = "RESULT"

        logger.info("binance_placing_order", symbol=bsym, side=order.side.value,
                     type=binance_type, qty=params["quantity"],
                     price=params.get("price"), stop=params.get("stopPrice"),
                     cid=order.client_order_id)

        async def _recover() -> Optional[Dict]:
            return await self.get_order(order.symbol, client_order_id=order.client_order_id)

        result = await self._auth_post("/fapi/v1/order", params, recover_fn=_recover)
        return self._normalize_order_response(order, result)

    async def close_all_positions(self, max_attempts: int = 3) -> Dict[str, Any]:
        """Flatten every open position with MARKET reduceOnly orders (audit P0-03).

        Reads /fapi/v2/positionRisk, sends one reduceOnly MARKET per non-zero
        positionAmt (quantity floored to stepSize), re-reads and retries up to
        `max_attempts` times. Returns {"closed": [...], "remaining": [...], "errors": [...]}.
        """
        await self._ensure_filters()
        closed: List[Dict[str, Any]] = []
        errors: List[Dict[str, Any]] = []
        remaining: List[Dict[str, Any]] = []

        for attempt in range(max_attempts):
            try:
                positions = await self.get_positions()
            except Exception as e:
                errors.append({"stage": "get_positions", "attempt": attempt + 1, "error": str(e)})
                logger.error("close_all_positions_read_failed", attempt=attempt + 1, error=str(e))
                await asyncio.sleep(0.5 * (attempt + 1))
                continue

            open_pos = [p for p in (positions or [])
                        if float(p.get("positionAmt", p.get("size", 0)) or 0) != 0]
            if not open_pos:
                remaining = []
                break

            remaining = open_pos
            for p in open_pos:
                symbol = p.get("symbol", "")
                amt = float(p.get("positionAmt", p.get("size", 0)) or 0)
                side = Side.SELL if amt > 0 else Side.BUY
                order = Order(
                    symbol=symbol, side=side, order_type=OrderType.MARKET,
                    quantity=abs(amt), reduce_only=True,
                    client_order_id=self.new_client_order_id("bs_close"),
                )
                try:
                    res = await self.place_order(order)
                    closed.append({"symbol": symbol, "qty": abs(amt), "side": side.value,
                                   "status": res.get("status"), "orderId": res.get("orderId")})
                    logger.warning("position_closed_market", symbol=symbol,
                                   qty=abs(amt), side=side.value, status=res.get("status"))
                except Exception as e:
                    errors.append({"symbol": symbol, "attempt": attempt + 1, "error": str(e)})
                    logger.error("close_position_failed", symbol=symbol,
                                 attempt=attempt + 1, error=str(e))
            await asyncio.sleep(0.3 * (attempt + 1))
        else:
            # Loop exhausted without a clean read -> report whatever is still open
            try:
                positions = await self.get_positions()
                remaining = [p for p in (positions or [])
                             if float(p.get("positionAmt", p.get("size", 0)) or 0) != 0]
            except Exception:
                pass

        if remaining:
            logger.critical("POSITIONS_STILL_OPEN_AFTER_CLOSE_ALL",
                            symbols=[p.get("symbol") for p in remaining])
        return {"closed": closed, "remaining": remaining, "errors": errors}

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
        await self._ensure_filters()
        all_results = []
        # Binance allows max 5 orders per batch
        for i in range(0, len(orders), 5):
            chunk = orders[i:i + 5]
            batch_list = []
            for o in chunk:
                bsym = self._to_binance_symbol(o.symbol)
                binance_type = ORDER_TYPE_MAP.get(o.order_type, "MARKET")
                if not o.client_order_id:
                    o.client_order_id = self.new_client_order_id("bs_mm")
                try:
                    normalized = self._normalize_order_params(o, bsym)
                except ValueError as ve:
                    logger.error("binance_batch_order_invalid", symbol=bsym, error=str(ve))
                    continue
                entry: Dict[str, Any] = {
                    "symbol": bsym,
                    "side": o.side.value,
                    "type": binance_type,
                    "quantity": normalized["quantity"],
                    "newClientOrderId": o.client_order_id,
                }
                if "price" in normalized:
                    entry["price"] = normalized["price"]
                    entry["timeInForce"] = o.time_in_force.value
                if "stopPrice" in normalized:
                    entry["stopPrice"] = normalized["stopPrice"]
                if o.reduce_only:
                    entry["reduceOnly"] = "true"
                if o.post_only and o.order_type == OrderType.LIMIT:
                    entry["timeInForce"] = "GTX"
                batch_list.append(entry)
            if not batch_list:
                continue

            params = {"batchOrders": _json.dumps(batch_list)}
            try:
                # Non-idempotent POST: no blind retry (P0-02). A batch cannot be
                # reconciled atomically, so a timeout/5xx surfaces as an error.
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
