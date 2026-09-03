"""
Strike Finance V2 REST client (perpetuals CLOB, https://docs.strikefinance.org).

Rewritten 2026-09-03 against the official OpenAPI specs (docs/strike/skills__openapi__*.yaml) and the
builder reference. What matters:
  * Auth = Ed25519 "API wallet": headers X-API-Wallet-{Public-Key,Signature,Timestamp,Nonce}, message
    `{METHOD}:{PATH}:{TIMESTAMP}:{NONCE}:{SHA256(body)}` where PATH includes the query string and the
    body hash of a GET is sha256(""). The API wallet can trade but can NOT withdraw.
  * Order fields are snake_case and lowercase (`side` buy|sell, `type` limit|market|stop|…, `size` as a
    decimal string). POST /v2/order only acknowledges (client_order_id, sequence_id); the order state is
    read with GET /v2/order (fields ID, Status, Size, Filled, …). This client NORMALIZES every response
    to the Binance-like shape the execution engine already understands (orderId, clientOrderId, status,
    executedQty, avgPrice; positions with positionAmt/entryPrice/unrealizedProfit/liquidationPrice).
  * Sub-accounts: `sub_account_id` on trading/read endpoints; vaults: `vault_id`. Both optional here.
  * Base URLs: mainnet https://api.strikefinance.org (+ /price), testnet https://api-v2-testnet.strikefinance.org.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from collections import deque
from typing import Any, Dict, List, Optional

import aiohttp
import structlog
from nacl.signing import SigningKey

from config.settings import Settings
from core.types import (
    MarketSnapshot, Order, OrderBook, OrderBookLevel, OrderType, Side, TimeInForce,
)

logger = structlog.get_logger(__name__)

# Strike order status → engine status (Binance vocabulary used by execution/order_engine.py)
STATUS_MAP = {
    "pending": "NEW", "open": "NEW", "untriggered": "NEW", "partially_filled": "PARTIALLY_FILLED",
    "filled": "FILLED", "canceled": "CANCELED", "cancelled": "CANCELED", "rejected": "REJECTED",
    "expired": "EXPIRED", "none": "NEW",
}
ORDER_TYPES = {"limit", "market", "stop", "stop_limit", "take_profit", "take_profit_limit", "trailing_stop_market"}


def _f(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _get(d: Dict, *keys: str, default: Any = None) -> Any:
    """First present key among several spellings (the API mixes `Size`, `size`, `positionAmt`…)."""
    for k in keys:
        if isinstance(d, dict) and k in d and d[k] is not None:
            return d[k]
    return default


class _RateLimiter:
    """Token bucket: at most `max_requests` per `window_sec` (exchangeInfo: 2400 weight/min, 1200 orders/min)."""

    def __init__(self, max_requests: int = 50, window_sec: float = 10.0) -> None:
        self._max = max_requests
        self._window = window_sec
        self._times: deque = deque(maxlen=max_requests)

    async def acquire(self) -> None:
        now = time.monotonic()
        if len(self._times) == self._max:
            oldest = self._times[0]
            wait = self._window - (now - oldest)
            if wait > 0:
                await asyncio.sleep(wait)
        self._times.append(time.monotonic())


class StrikeClient:
    """Async client for the Strike V2 trade/user/market APIs."""

    def __init__(self, settings: Settings, sub_account_id: Optional[str] = None,
                 vault_id: Optional[str] = None) -> None:
        self.settings = settings
        self._base_url = settings.api_base_url.rstrip("/")
        self._price_url = settings.api_price_url.rstrip("/")
        self._session: Optional[aiohttp.ClientSession] = None
        self._rate_limiter = _RateLimiter(max_requests=50, window_sec=10.0)
        self.sub_account_id = sub_account_id
        self.vault_id = vault_id
        self._markets: Dict[str, Dict] = {}
        if settings.api_private_key:
            key_bytes = bytes.fromhex(settings.api_private_key.strip()[:64])
            self._signing_key: Optional[SigningKey] = SigningKey(key_bytes)
            derived = self._signing_key.verify_key.encode().hex()
            self._public_key = (settings.api_public_key or derived).strip().lower()
            if self._public_key != derived:
                logger.warning("strike_public_key_mismatch", configured=self._public_key[:8], derived=derived[:8])
        else:
            self._signing_key = None
            self._public_key = None

    @property
    def has_credentials(self) -> bool:
        return self._signing_key is not None

    # ── HTTP plumbing ─────────────────────────────────────────────

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    def _sign_request(self, method: str, path: str, body: str = "") -> Dict[str, str]:
        """Ed25519 API-wallet headers. `path` MUST include the query string (reference: pathname + search)."""
        if not self._signing_key:
            raise RuntimeError("Strike API wallet not configured (STRIKE_PRIVATE_KEY)")
        timestamp = str(int(time.time()))
        nonce = str(uuid.uuid4())
        body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        message = f"{method.upper()}:{path}:{timestamp}:{nonce}:{body_hash}"
        signature = self._signing_key.sign(message.encode("utf-8")).signature.hex()
        return {
            "X-API-Wallet-Public-Key": self._public_key,
            "X-API-Wallet-Signature": signature,
            "X-API-Wallet-Timestamp": timestamp,
            "X-API-Wallet-Nonce": nonce,
            "Content-Type": "application/json",
        }

    @staticmethod
    def _qs(params: Optional[Dict[str, Any]]) -> str:
        if not params:
            return ""
        from urllib.parse import urlencode
        return "?" + urlencode({k: v for k, v in params.items() if v is not None})

    async def _public_get(self, path: str, params: Optional[Dict] = None) -> Any:
        await self._rate_limiter.acquire()
        session = await self._get_session()
        url = f"{self._price_url}{path}{self._qs(params)}"
        async with session.get(url) as resp:
            if resp.status != 200:
                text = await resp.text()
                logger.error("strike_public_get_error", url=url, status=resp.status, body=text[:300])
                raise RuntimeError(f"Strike API error {resp.status}: {text[:300]}")
            return await resp.json()

    async def _auth_request(self, method: str, path: str, body: Optional[Dict] = None,
                            params: Optional[Dict] = None) -> Any:
        """Signed request. Query params become part of the signed path; bodies are JSON (compact)."""
        await self._rate_limiter.acquire()
        session = await self._get_session()
        full_path = f"{path}{self._qs(params)}"
        body_str = json.dumps(body, separators=(",", ":")) if body is not None else ""
        headers = self._sign_request(method, full_path, body_str)
        url = f"{self._base_url}{full_path}"
        m = method.upper()
        req = {"GET": session.get, "POST": session.post, "DELETE": session.delete, "PUT": session.put}.get(m)
        if req is None:
            raise ValueError(f"Unsupported HTTP method: {method}")
        kwargs: Dict[str, Any] = {"headers": headers}
        if m != "GET":
            kwargs["data"] = body_str
        async with req(url, **kwargs) as resp:
            if resp.status not in (200, 201, 202):
                text = await resp.text()
                logger.error("strike_auth_request_error", method=m, path=full_path, status=resp.status, body=text[:300])
                raise RuntimeError(f"Strike API error {resp.status}: {text[:300]}")
            if resp.content_type == "application/json":
                return await resp.json()
            return {"raw": await resp.text()}

    def _scope(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """Attach sub-account / vault scoping to a trading body when configured."""
        if self.sub_account_id:
            body["sub_account_id"] = self.sub_account_id
        if self.vault_id:
            body["vault_id"] = self.vault_id
        return body

    def _scope_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        if self.sub_account_id:
            params["sub_account_id"] = self.sub_account_id
        if self.vault_id:
            params["vault_id"] = self.vault_id
        return params

    # ── Market data (public) ──────────────────────────────────────

    async def get_exchange_info(self) -> Dict:
        return await self._public_get("/v2/exchangeInfo")

    async def get_markets(self, refresh: bool = False) -> Dict[str, Dict]:
        """Symbol → {status, tick_size, min_qty, step_size, max_qty, min_notional, price_precision,
        quantity_precision, liquidation_fee, filters}. Cached; the dynamic universe reads this."""
        if self._markets and not refresh:
            return self._markets
        info = await self.get_exchange_info()
        out: Dict[str, Dict] = {}
        for s in info.get("symbols", []):
            filters = {f.get("filterType"): f for f in s.get("filters", []) if isinstance(f, dict)}
            pf, lot, mlot = filters.get("PRICE_FILTER", {}), filters.get("LOT_SIZE", {}), filters.get("MARKET_LOT_SIZE", {})
            out[s["symbol"]] = {
                "symbol": s["symbol"], "status": s.get("status"), "base": s.get("baseAsset"), "quote": s.get("quoteAsset"),
                "contract_type": s.get("contractType"), "tick_size": _f(pf.get("tickSize"), 0.0),
                "min_price": _f(pf.get("minPrice")), "max_price": _f(pf.get("maxPrice")),
                "min_qty": _f(lot.get("minQty")), "step_size": _f(lot.get("stepSize")), "max_qty": _f(lot.get("maxQty")),
                "market_max_qty": _f(mlot.get("maxQty")) or _f(lot.get("maxQty")),
                "min_notional": _f(filters.get("MIN_NOTIONAL", {}).get("notional"), 0.0),
                "price_precision": int(s.get("pricePrecision") or 8), "quantity_precision": int(s.get("quantityPrecision") or 8),
                "liquidation_fee": _f(s.get("liquidationFee")), "market_take_bound": _f(s.get("marketTakeBound")),
                "filters": filters,
            }
        self._markets = out
        return out

    async def get_orderbook(self, symbol: str, limit: int = 20) -> OrderBook:
        data = await self._public_get("/v2/depth", {"symbol": symbol, "limit": limit})
        bids = [OrderBookLevel(float(p), float(q)) for p, q in data.get("bids", [])]
        asks = [OrderBookLevel(float(p), float(q)) for p, q in data.get("asks", [])]
        return OrderBook(symbol=symbol, timestamp=time.time(), bids=bids, asks=asks)

    async def get_ticker_24h(self, symbol: Optional[str] = None) -> Any:
        return await self._public_get("/v2/ticker/24hr", {"symbol": symbol} if symbol else None)

    async def get_premium_index(self, symbol: Optional[str] = None) -> Any:
        """markPrice, indexPrice, fundingRate (per 8 h), nextFundingTime (ms), interestRate."""
        return await self._public_get("/v2/premiumIndex", {"symbol": symbol} if symbol else None)

    get_mark_price = get_premium_index      # backwards compatibility

    async def get_recent_trades(self, symbol: str, limit: int = 100) -> List[Dict]:
        return await self._public_get("/v2/trades", {"symbol": symbol, "limit": limit})

    async def get_klines(self, symbol: str, interval: str = "1m", limit: int = 500,
                         start_time: Optional[int] = None, end_time: Optional[int] = None) -> List[List]:
        """[[openTime, o, h, l, c, volume, closeTime, quoteVolume, trades, …], …] (Binance layout)."""
        params: Dict[str, Any] = {"symbol": symbol, "interval": interval, "limit": limit}
        if start_time:
            params["startTime"] = int(start_time)
        if end_time:
            params["endTime"] = int(end_time)
        data = await self._public_get("/v2/klines", params)
        return data if isinstance(data, list) else (data.get("data") or data.get("klines") or [])

    async def get_ticker_price(self, symbol: Optional[str] = None) -> Any:
        return await self._public_get("/v2/ticker/price", {"symbol": symbol} if symbol else None)

    async def get_open_interest(self, symbol: Optional[str] = None) -> Any:
        return await self._public_get("/v2/openInterest", {"symbol": symbol} if symbol else None)

    async def get_book_ticker(self, symbol: Optional[str] = None) -> Any:
        return await self._public_get("/v2/ticker/bookTicker", {"symbol": symbol} if symbol else None)

    @staticmethod
    def _pick(data: Any, symbol: str) -> Dict:
        if isinstance(data, list):
            return next((d for d in data if d.get("symbol") == symbol), data[0] if data else {})
        return data or {}

    async def get_market_snapshot(self, symbol: str) -> MarketSnapshot:
        results = await asyncio.gather(
            self.get_ticker_24h(symbol), self.get_premium_index(symbol), self.get_orderbook(symbol, 20),
            self.get_open_interest(symbol), return_exceptions=True)
        ticker = self._pick(results[0], symbol) if not isinstance(results[0], Exception) else {}
        mark = self._pick(results[1], symbol) if not isinstance(results[1], Exception) else {}
        ob = results[2] if not isinstance(results[2], Exception) else OrderBook(symbol=symbol, timestamp=time.time(), bids=[], asks=[])
        oi = self._pick(results[3], symbol) if not isinstance(results[3], Exception) else {}
        return MarketSnapshot(
            symbol=symbol, timestamp=time.time(),
            price=_f(ticker.get("lastPrice")) or _f(mark.get("markPrice")),
            mark_price=_f(mark.get("markPrice")), index_price=_f(mark.get("indexPrice")),
            funding_rate=_f(_get(mark, "fundingRate", "lastFundingRate", default=0.0)),
            volume_24h=_f(ticker.get("quoteVolume")), open_interest=_f(oi.get("openInterest")), orderbook=ob,
        )

    # ── Account / user (authenticated) ─────────────────────────────

    async def get_account(self, account_id: Optional[str] = None) -> Dict:
        params = self._scope_params({"account_id": account_id} if account_id else {})
        return await self._auth_request("GET", "/v2/account", params=params or None)

    async def get_balances(self) -> Dict:
        return await self._auth_request("GET", "/v2/balances", params=self._scope_params({}) or None)

    async def get_portfolio(self) -> Dict:
        return await self._auth_request("GET", "/v2/portfolio", params=self._scope_params({}) or None)

    async def get_sub_accounts(self) -> List[Dict]:
        data = await self._auth_request("GET", "/v2/sub-accounts")
        return data.get("sub_accounts", []) if isinstance(data, dict) else data

    @staticmethod
    def normalize_position(p: Dict) -> Dict:
        """Strike Position (mixed-case keys) → Binance-like row the engine reads (positionAmt signed)."""
        size = _f(_get(p, "Size", "size", "positionAmt", default=0.0))
        side = str(_get(p, "Side", "side", default="")).lower()
        signed = -abs(size) if side in ("sell", "short") else abs(size)
        return {
            "symbol": _get(p, "symbol", "Symbol"), "positionAmt": signed, "size": abs(size), "side": side,
            "entryPrice": _f(_get(p, "EntryPrice", "entry_price", "entryPrice")),
            "unrealizedProfit": _f(_get(p, "upnl", "unrealized_pnl", "unrealizedProfit")),
            "leverage": int(_f(_get(p, "Leverage", "leverage", default=1)) or 1),
            "marginType": str(_get(p, "MarginMode", "margin_mode", default="cross")).lower(),
            "isolatedMargin": _f(_get(p, "IsolatedMargin", "isolated_margin")),
            "liquidationPrice": _f(_get(p, "liquidation_price", "liquidationPrice")),
            "bankruptcyPrice": _f(_get(p, "bankruptcy_price")),
            "maintMargin": _f(_get(p, "maintenance_margin", "maintMargin")),
            "positionId": _get(p, "PositionID", "position_id"),
        }

    async def get_positions(self, symbol: Optional[str] = None) -> List[Dict]:
        params = self._scope_params({"symbol": symbol} if symbol else {})
        data = await self._auth_request("GET", "/v2/positions", params=params or None)
        rows = data.get("positions", []) if isinstance(data, dict) else (data or [])
        out = [self.normalize_position(p) for p in rows]
        return [p for p in out if p["positionAmt"] != 0]

    async def get_closed_positions(self, symbol: Optional[str] = None, limit: int = 100) -> List[Dict]:
        params = self._scope_params({"symbol": symbol, "limit": limit})
        data = await self._auth_request("GET", "/v2/closedPositions", params=params)
        return data.get("positions", data.get("closed_positions", [])) if isinstance(data, dict) else data

    # ── Orders ────────────────────────────────────────────────────

    @staticmethod
    def _fmt(x: float) -> str:
        return format(float(x), "f").rstrip("0").rstrip(".") if float(x) != int(float(x)) else str(int(float(x)))

    def _order_body(self, order: Order) -> Dict[str, Any]:
        """CreateOrderRequest exactly as the OpenAPI schema defines it."""
        otype = order.order_type.value.lower()
        if otype not in ORDER_TYPES:
            raise ValueError(f"unsupported Strike order type: {otype}")
        body: Dict[str, Any] = {
            "symbol": order.symbol,
            "side": order.side.value.lower(),
            "type": otype,
            "size": self._fmt(order.quantity),
        }
        if order.price is not None and otype != "market":
            body["price"] = self._fmt(order.price)
        if order.stop_price is not None:
            body["stop_price"] = self._fmt(order.stop_price)
        if order.time_in_force != TimeInForce.GTC:
            body["time_in_force"] = order.time_in_force.value
        if order.post_only:
            body["post_only"] = True
        if order.reduce_only:
            body["reduce_only"] = True
        if order.client_order_id:
            body["client_order_id"] = str(order.client_order_id)[:36]
        return body

    @staticmethod
    def normalize_order(o: Dict) -> Dict:
        """GET /v2/order `Order` (ID/Status/Size/Filled…) → engine shape (orderId/status/executedQty…)."""
        status = str(_get(o, "Status", "status", default="none")).lower()
        size = _f(_get(o, "Size", "size"))
        filled = _f(_get(o, "Filled", "filled", "executedQty"))
        if status == "open" and 0 < filled < size:
            status = "partially_filled"
        return {
            "orderId": str(_get(o, "ID", "id", "order_id", "orderId", default="")),
            "clientOrderId": _get(o, "ClientOrderID", "client_order_id", "clientOrderId", default=""),
            "symbol": _get(o, "Symbol", "symbol"), "side": str(_get(o, "Side", "side", default="")).upper(),
            "type": str(_get(o, "Type", "type", default="")).upper(),
            "status": STATUS_MAP.get(status, status.upper()), "strike_status": status,
            "origQty": size, "executedQty": filled, "price": _f(_get(o, "Price", "price")),
            "stopPrice": _f(_get(o, "StopPrice", "stop_price")), "avgPrice": _f(_get(o, "AvgPrice", "avg_price", "Price", "price")),
            "reduceOnly": bool(_get(o, "ReduceOnly", "reduce_only", default=False)),
            "closeReason": _get(o, "CloseReason", "close_reason", default=""), "raw": o,
        }

    async def get_order(self, symbol: str, order_id: Optional[str] = None,
                        client_order_id: Optional[str] = None) -> Dict:
        params: Dict[str, Any] = {"symbol": symbol}
        if order_id:
            params["order_id"] = order_id
        elif client_order_id:
            params["client_order_id"] = client_order_id
        else:
            raise ValueError("order_id or client_order_id required")
        data = await self._auth_request("GET", "/v2/order", params=self._scope_params(params))
        return self.normalize_order(data.get("order", data) if isinstance(data, dict) else data)

    async def place_order(self, order: Order, confirm: bool = True) -> Dict:
        """POST /v2/order (ack only) then GET /v2/order to return the real state in engine shape."""
        body = self._scope(self._order_body(order))
        logger.info("strike_placing_order", **{k: v for k, v in body.items() if k != "sub_account_id"})
        ack = await self._auth_request("POST", "/v2/order", body)
        cid = (ack or {}).get("client_order_id") or body.get("client_order_id")
        result = {"orderId": "", "clientOrderId": cid, "status": "NEW", "executedQty": 0.0, "avgPrice": 0.0,
                  "sequenceId": (ack or {}).get("sequence_id"), "messageId": (ack or {}).get("message_id"), "ack": ack}
        if confirm and cid:
            for delay in (0.15, 0.4, 0.8):
                await asyncio.sleep(delay)
                try:
                    st = await self.get_order(order.symbol, client_order_id=cid)
                    result.update({k: st[k] for k in ("orderId", "status", "executedQty", "avgPrice", "strike_status")})
                    if st["status"] in ("FILLED", "CANCELED", "REJECTED", "EXPIRED") or st["orderId"]:
                        break
                except Exception as e:  # noqa: BLE001 — ack is still valid; the engine polls again
                    logger.warning("strike_order_confirm_pending", client_order_id=cid, error=str(e)[:120])
        return result

    async def place_bracket_order(self, order: Order, tp_price: float, sl_price: float) -> Dict:
        """POST /v2/order/strategy: entry + OCO take-profit/stop placed after the entry fills."""
        body = self._order_body(order)
        size = body["size"]
        body["tp_order"] = {"type": "take_profit", "size": size, "stop_price": self._fmt(tp_price),
                            "price": self._fmt(tp_price), "time_in_force": "GTC", "working_type": "mark_price",
                            "post_only": False, "price_protect": False}
        body["sl_order"] = {"type": "stop", "size": size, "stop_price": self._fmt(sl_price), "price": self._fmt(sl_price),
                            "time_in_force": "GTC", "working_type": "mark_price", "post_only": False, "price_protect": False}
        ack = await self._auth_request("POST", "/v2/order/strategy", self._scope(body))
        return {"clientOrderId": body.get("client_order_id"), "strategyId": (ack or {}).get("strategy_id"), "ack": ack,
                "status": "NEW", "orderId": "", "executedQty": 0.0}

    async def cancel_order(self, symbol: str, order_id: str) -> Dict:
        oid: Any = int(order_id) if str(order_id).isdigit() else order_id
        return await self._auth_request("DELETE", "/v2/order/cancel", self._scope({"order_id": oid, "symbol": symbol}))

    async def cancel_all_orders(self, symbol: Optional[str] = None) -> Dict:
        body: Dict[str, Any] = {"symbol": symbol} if symbol else {}
        return await self._auth_request("DELETE", "/v2/order/cancel-all", self._scope(body))

    async def replace_order(self, symbol: str, cancel_order_id: str, new_order: Order) -> Dict:
        oid: Any = int(cancel_order_id) if str(cancel_order_id).isdigit() else cancel_order_id
        body = self._scope({"cancel": {"order_id": oid, "symbol": symbol}, "new_order": self._order_body(new_order)})
        return await self._auth_request("POST", "/v2/order/replace", body)

    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict]:
        params = self._scope_params({"symbol": symbol} if symbol else {})
        data = await self._auth_request("GET", "/v2/openOrders", params=params or None)
        rows = data.get("orders", []) if isinstance(data, dict) else (data or [])
        return [self.normalize_order(o) for o in rows]

    async def batch_orders(self, orders: List[Order]) -> Dict:
        body = self._scope({"orders": [self._order_body(o) for o in orders]})
        return await self._auth_request("POST", "/v2/orders/batch", body)

    # ── Trading settings ──────────────────────────────────────────

    async def set_leverage(self, symbol: str, leverage: int) -> Dict:
        return await self._auth_request("POST", "/v2/leverage", self._scope({"symbol": symbol, "leverage": int(leverage)}))

    async def set_margin_mode(self, symbol: str, mode: str = "cross") -> Dict:
        return await self._auth_request("POST", "/v2/marginMode", self._scope({"symbol": symbol, "marginMode": mode}))

    # ── History ───────────────────────────────────────────────────

    async def get_order_history(self, symbol: Optional[str] = None, limit: int = 100) -> List[Dict]:
        data = await self._auth_request("GET", "/v2/history/order", params=self._scope_params({"symbol": symbol, "limit": limit}))
        rows = data.get("orders", data.get("results", [])) if isinstance(data, dict) else data
        return [self.normalize_order(o) for o in rows]

    async def get_fill_history(self, symbol: Optional[str] = None, limit: int = 100) -> List[Dict]:
        data = await self._auth_request("GET", "/v2/history/fill", params=self._scope_params({"symbol": symbol, "limit": limit}))
        return data.get("fills", data.get("results", [])) if isinstance(data, dict) else data

    async def get_funding_history(self, symbol: Optional[str] = None, limit: int = 100) -> List[Dict]:
        data = await self._auth_request("GET", "/v2/history/funding", params=self._scope_params({"symbol": symbol, "limit": limit}))
        return data.get("funding", data.get("results", [])) if isinstance(data, dict) else data

    # ── Sizing helpers ────────────────────────────────────────────

    async def round_size(self, symbol: str, size: float) -> float:
        """Round DOWN to the market's step size and reject below min_qty (audit R2: Hyperliquid failed
        100 % of orders on this)."""
        m = (await self.get_markets()).get(symbol)
        if not m:
            return size
        step = m.get("step_size") or 0.0
        if step > 0:
            size = int(size / step + 1e-9) * step
        return round(size, m.get("quantity_precision", 8)) if size >= (m.get("min_qty") or 0.0) else 0.0

    async def round_price(self, symbol: str, price: float) -> float:
        m = (await self.get_markets()).get(symbol)
        tick = (m or {}).get("tick_size") or 0.0
        if tick > 0:
            price = round(round(price / tick) * tick, 10)
        return price
