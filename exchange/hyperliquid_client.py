"""
Hyperliquid Perpetual Futures Client.

Uses the official hyperliquid-python-sdk for REST and signing.
Implements the same interface as BinanceClient/StrikeClient for drop-in compatibility.

API: POST https://api.hyperliquid.xyz/info (reads) + /exchange (trades)
Auth: EIP-712 signing with Ethereum private key
Symbols: plain tickers ("BTC", "ETH", "SOL", "ADA")
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

from config.settings import Settings
from core.types import Order, OrderType, Side, OrderBook, OrderBookLevel, MarketSnapshot
import structlog

logger = structlog.get_logger(__name__)

# Hyperliquid uses plain ticker names
SYMBOL_MAP = {
    "BTC-USD": "BTC",
    "ETH-USD": "ETH",
    "SOL-USD": "SOL",
    "ADA-USD": "ADA",
    "BNB-USD": "BNB",
    "DOGE-USD": "DOGE",
    "XRP-USD": "XRP",
}
SYMBOL_MAP_REVERSE = {v: k for k, v in SYMBOL_MAP.items()}

# Order type mapping
ORDER_TYPE_MAP = {
    OrderType.MARKET: "market",
    OrderType.LIMIT: "limit",
    OrderType.STOP: "stop",
    OrderType.TAKE_PROFIT: "tp",
}


class HyperliquidClient:
    """Async client for Hyperliquid perpetual futures.

    Uses the SDK synchronously in a thread executor to maintain
    async interface compatibility with BinanceClient.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._private_key = settings.hyperliquid_private_key
        self._wallet = settings.hyperliquid_wallet_address
        self._info = None
        self._exchange = None
        self._loop = None

        if not self._private_key:
            logger.warning("hyperliquid_private_key_not_set")

    def _ensure_sdk(self):
        """Lazy-init SDK objects (must be called from async context)."""
        if self._info is not None:
            return

        try:
            from hyperliquid.info import Info
            from hyperliquid.exchange import Exchange
            from hyperliquid.utils import constants

            base_url = constants.MAINNET_API_URL
            self._info = Info(base_url, skip_ws=True)

            if self._private_key:
                from eth_account import Account
                account = Account.from_key(self._private_key)
                self._wallet = account.address
                self._exchange = Exchange(account, base_url)
                logger.info("hyperliquid_client_initialized",
                            wallet=self._wallet[:10] + "...")
            else:
                logger.warning("hyperliquid_no_private_key_read_only")
        except ImportError as e:
            logger.error("hyperliquid_sdk_not_installed", error=str(e))
            raise

    def _to_hl_symbol(self, symbol: str) -> str:
        return SYMBOL_MAP.get(symbol, symbol.replace("-USD", ""))

    def _from_hl_symbol(self, symbol: str) -> str:
        return SYMBOL_MAP_REVERSE.get(symbol, f"{symbol}-USD")

    async def _run_sync(self, func, *args):
        """Run a synchronous SDK call in a thread executor."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, func, *args)

    # ── Market Data (public, no auth) ────────────────────────────

    async def get_market_snapshot(self, symbol: str) -> MarketSnapshot:
        self._ensure_sdk()
        coin = self._to_hl_symbol(symbol)

        def _fetch():
            meta = self._info.meta_and_asset_ctxs()
            # meta is [meta_info, [asset_ctx_0, asset_ctx_1, ...]]
            asset_ctxs = meta[1] if len(meta) > 1 else []
            universe = meta[0].get("universe", []) if meta else []

            for i, asset in enumerate(universe):
                if asset.get("name") == coin and i < len(asset_ctxs):
                    ctx = asset_ctxs[i]
                    return {
                        "mark_price": float(ctx.get("markPx", 0)),
                        "mid_price": float(ctx.get("midPx", 0)),
                        "funding_rate": float(ctx.get("funding", 0)),
                        "open_interest": float(ctx.get("openInterest", 0)),
                        "volume_24h": float(ctx.get("dayNtlVlm", 0)),
                    }
            return {}

        data = await self._run_sync(_fetch)
        price = data.get("mid_price", 0) or data.get("mark_price", 0)
        return MarketSnapshot(
            symbol=symbol,
            price=price,
            timestamp=time.time(),
            orderbook=OrderBook(symbol=symbol, timestamp=time.time(), bids=[], asks=[]),
            funding_rate=data.get("funding_rate", 0),
            volume_24h=data.get("volume_24h", 0),
            open_interest=data.get("open_interest", 0),
            mark_price=data.get("mark_price", 0),
            index_price=data.get("mark_price", 0),
        )

    async def get_orderbook(self, symbol: str, limit: int = 20) -> OrderBook:
        self._ensure_sdk()
        coin = self._to_hl_symbol(symbol)

        def _fetch():
            return self._info.l2_snapshot(coin)

        data = await self._run_sync(_fetch)
        bids = [OrderBookLevel(float(p["px"]), float(p["sz"]))
                for p in data.get("levels", [[]])[0][:limit]]
        asks = [OrderBookLevel(float(p["px"]), float(p["sz"]))
                for p in data.get("levels", [[], []])[1][:limit]]

        return OrderBook(
            symbol=symbol,
            timestamp=time.time(),
            bids=bids,
            asks=asks,
        )

    async def get_klines(
        self, symbol: str, interval: str = "1m", limit: int = 500,
        start_time: Optional[int] = None, end_time: Optional[int] = None,
    ) -> List[List]:
        self._ensure_sdk()
        coin = self._to_hl_symbol(symbol)

        # Convert interval to seconds for snapshot endpoint
        interval_map = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400}
        interval_sec = interval_map.get(interval, 60)
        end_ms = end_time or int(time.time() * 1000)
        start_ms = start_time or (end_ms - limit * interval_sec * 1000)

        def _fetch():
            return self._info.candles_snapshot(coin, interval, start_ms, end_ms)

        data = await self._run_sync(_fetch)
        # Convert to Binance kline format: [open_time, open, high, low, close, volume, ...]
        result = []
        for c in data[-limit:]:
            result.append([
                c.get("t", 0),           # open time
                c.get("o", "0"),          # open
                c.get("h", "0"),          # high
                c.get("l", "0"),          # low
                c.get("c", "0"),          # close
                c.get("v", "0"),          # volume
                c.get("t", 0),           # close time
                "0", "0", "0", "0", "0",  # padding for Binance compat
            ])
        return result

    async def get_recent_trades(self, symbol: str, limit: int = 100) -> List[Dict]:
        self._ensure_sdk()
        coin = self._to_hl_symbol(symbol)

        def _fetch():
            return self._info.recent_trades(coin)

        data = await self._run_sync(_fetch)
        return data[:limit]

    # ── Account (authenticated) ──────────────────────────────────

    async def get_positions(self, symbol: Optional[str] = None) -> List[Dict]:
        self._ensure_sdk()
        if not self._wallet:
            return []

        def _fetch():
            state = self._info.user_state(self._wallet)
            positions = []
            for pos in state.get("assetPositions", []):
                p = pos.get("position", {})
                coin = p.get("coin", "")
                size = float(p.get("szi", 0))
                if size == 0:
                    continue
                bs_symbol = self._from_hl_symbol(coin)
                positions.append({
                    "symbol": bs_symbol,
                    "positionAmt": str(size),
                    "entryPrice": p.get("entryPx", "0"),
                    "markPrice": p.get("positionValue", "0"),
                    "unrealizedProfit": p.get("unrealizedPnl", "0"),
                    "leverage": p.get("leverage", {}).get("value", 1),
                })
            return positions

        positions = await self._run_sync(_fetch)
        if symbol:
            return [p for p in positions if p["symbol"] == symbol]
        return positions

    async def get_account_balance(self) -> Dict:
        self._ensure_sdk()
        if not self._wallet:
            return {"balance": 0}

        def _fetch():
            state = self._info.user_state(self._wallet)
            margin = state.get("marginSummary", {})
            return {
                "balance": float(margin.get("accountValue", 0)),
                "available": float(margin.get("totalRawUsd", 0)),
                "margin_used": float(margin.get("totalMarginUsed", 0)),
            }

        return await self._run_sync(_fetch)

    # ── Orders (authenticated) ───────────────────────────────────

    async def place_order(self, order: Order) -> Dict:
        self._ensure_sdk()
        if not self._exchange:
            raise RuntimeError("Hyperliquid client not authenticated (no private key)")

        coin = self._to_hl_symbol(order.symbol)
        is_buy = order.side == Side.BUY
        sz = order.quantity

        def _place():
            if order.order_type == OrderType.MARKET:
                # SDK market_open places IOC limit with slippage
                result = self._exchange.market_open(
                    coin, is_buy, sz, None, 0.01  # 1% slippage tolerance
                )
            elif order.order_type == OrderType.LIMIT:
                result = self._exchange.order(
                    coin, is_buy, sz, order.price or 0,
                    {"limit": {"tif": "Gtc"}},
                    reduce_only=order.reduce_only,
                )
            elif order.order_type == OrderType.STOP:
                # Stop market order
                result = self._exchange.order(
                    coin, is_buy, sz, order.stop_price or 0,
                    {"trigger": {"triggerPx": str(order.stop_price), "isMarket": True, "tpsl": "sl"}},
                    reduce_only=order.reduce_only,
                )
            elif order.order_type == OrderType.TAKE_PROFIT:
                result = self._exchange.order(
                    coin, is_buy, sz, order.stop_price or 0,
                    {"trigger": {"triggerPx": str(order.stop_price), "isMarket": True, "tpsl": "tp"}},
                    reduce_only=order.reduce_only,
                )
            else:
                result = self._exchange.order(
                    coin, is_buy, sz, order.price or 0,
                    {"limit": {"tif": "Gtc"}},
                    reduce_only=order.reduce_only,
                )
            return result

        result = await self._run_sync(_place)

        # Normalize response to match Binance format
        status_data = result.get("response", {}).get("data", {})
        statuses = status_data.get("statuses", [{}])
        first = statuses[0] if statuses else {}

        if "resting" in first:
            oid = first["resting"].get("oid", "")
            return {"orderId": str(oid), "status": "NEW", "symbol": order.symbol}
        elif "filled" in first:
            filled = first["filled"]
            return {
                "orderId": str(filled.get("oid", "")),
                "status": "FILLED",
                "symbol": order.symbol,
                "executedQty": str(filled.get("totalSz", sz)),
                "avgPrice": str(filled.get("avgPx", 0)),
            }
        elif "error" in first:
            raise RuntimeError(f"Hyperliquid order error: {first['error']}")

        return {"orderId": "", "status": "UNKNOWN", "raw": result}

    async def cancel_order(self, symbol: str, order_id: str) -> Dict:
        self._ensure_sdk()
        if not self._exchange:
            return {}
        coin = self._to_hl_symbol(symbol)

        def _cancel():
            return self._exchange.cancel(coin, int(order_id))

        return await self._run_sync(_cancel)

    async def cancel_all_orders(self, symbol: Optional[str] = None) -> Dict:
        self._ensure_sdk()
        if not self._exchange:
            return {}

        def _cancel_all():
            open_orders = self._info.open_orders(self._wallet)
            results = []
            for o in open_orders:
                coin = o.get("coin", "")
                oid = o.get("oid", 0)
                if symbol and self._from_hl_symbol(coin) != symbol:
                    continue
                try:
                    r = self._exchange.cancel(coin, oid)
                    results.append(r)
                except Exception as e:
                    logger.warning("hl_cancel_failed", coin=coin, oid=oid, error=str(e))
            return {"cancelled": len(results)}

        return await self._run_sync(_cancel_all)

    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict]:
        self._ensure_sdk()
        if not self._wallet:
            return []

        def _fetch():
            orders = self._info.open_orders(self._wallet)
            result = []
            for o in orders:
                bs_sym = self._from_hl_symbol(o.get("coin", ""))
                if symbol and bs_sym != symbol:
                    continue
                result.append({
                    "orderId": str(o.get("oid", "")),
                    "symbol": bs_sym,
                    "side": "BUY" if o.get("side") == "B" else "SELL",
                    "price": o.get("limitPx", "0"),
                    "origQty": o.get("sz", "0"),
                    "status": "NEW",
                })
            return result

        return await self._run_sync(_fetch)

    async def set_leverage(self, symbol: str, leverage: int) -> Dict:
        self._ensure_sdk()
        if not self._exchange:
            return {}
        coin = self._to_hl_symbol(symbol)

        def _set():
            return self._exchange.update_leverage(leverage, coin, is_cross=True)

        return await self._run_sync(_set)

    async def close(self) -> None:
        """Cleanup — SDK doesn't need explicit close."""
        pass
