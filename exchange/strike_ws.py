"""Live market data from Strike Finance — the venue this bot actually trades on.

WHAT THIS IS FOR
    The engine used to take its live feed from Binance while executing on Strike. That is defensible
    for a price reference and indefensible for everything else: measured on 2026-09-04, Strike turned
    over $1.9 M of BTC on a day Binance did $16 bn, carried 3.78 BTC of open interest against
    Binance's 113,100, and quoted a book seven times wider. A paper book filled against Binance is
    not simulating Strike. This module is the live half of the split Edgar asked for: **everything
    live comes from Strike, everything HISTORICAL keeps coming from Binance** (see
    `strategies/daily_sources.py` and the backtest), because Strike's own history is only 168 days
    for BTC and 19 for the S&P — far short of what the daily signal is fitted on.

THE PROTOCOL, AS MEASURED
    Strike speaks a Binance-compatible protocol at wss://api.strikefinance.org/ws/price. Control
    frames are ``{"method": "SUBSCRIBE", "params": [...], "id": N}`` and are ACKed with
    ``{"result": null, "id": N}``; errors come back as ``{"e": "error", "error": {...}}`` and
    ``LIST_SUBSCRIPTIONS`` is not implemented.

    **Stream names take the symbol in lowercase.** ``btc-usd@depth`` pushes; ``BTC-USD@depth`` is
    accepted with the same success ACK and then stays silent forever. That single detail is why the
    repo's previous Strike client — which sent a different frame shape entirely — never delivered a
    tick, and why the bot has been running on Binance all along.

WHAT COMES FROM WHERE, AND WHY
    * ``@kline_1m`` (WS) — Strike closes a 1 m bar every minute even when nothing trades, so the
      chart is continuous rather than gapped on a venue this thin.
    * ``@trade`` (WS) — real prints. Verified against REST: in a 100 s window exactly one trade
      happened across BTC/ETH/SOL/ADA/XAU/NIGHT, and the stream delivered exactly that one.
    * depth — REST snapshots, NOT the ``@depth`` stream. The venue's stream is a diff feed, while
      the engine's handler replaces the whole book on every event; feeding it diffs would leave a
      three-level book on screen. Strike's book changes about once every three seconds, so a 2 s
      snapshot costs nothing and removes a resync state machine that has no business existing here.
    * mark / index / funding — no stream exists, so premiumIndex is polled: one request covers all
      31 markets.

    Budget: 4 streamed symbols at one depth call each per 2 s plus one premiumIndex call per 5 s is
    about 130 requests a minute against the venue's published 2,400 weight/minute.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Callable, Dict, List, Optional

import structlog
import websockets

logger = structlog.get_logger(__name__)

STRIKE_WS_URL = "wss://api.strikefinance.org/ws/price"
STRIKE_REST_BASE = "https://api.strikefinance.org/price/v2"

DEPTH_POLL_SEC = 2.0
DEPTH_LEVELS = 20
MARK_POLL_SEC = 5.0
MAX_RECONNECT_SEC = 60.0


class StrikeMarketWebSocket:
    """Strike's live market data, shaped exactly like the Binance client the engine already reads.

    Emits the same event names and payload keys as `exchange.binance_ws.BinanceWebSocket`
    (`trade`, `depth`/`depthUpdate`, `kline`/`kline_1m`, `markPrice`/`markPriceUpdate`) so nothing
    downstream has to know which venue is feeding it.
    """

    def __init__(self, symbols: Optional[List[str]] = None, ws_url: str = STRIKE_WS_URL,
                 rest_base: str = STRIKE_REST_BASE) -> None:
        self.symbols: List[str] = [s.upper() for s in (symbols or [])]
        self.ws_url = ws_url
        self.rest_base = rest_base
        self._callbacks: Dict[str, List[Callable]] = {}
        self._running = False
        self._connected = False
        self._ws = None
        self._reconnect_delay = 1.0
        self._session = None
        self._on_market_connect_cb: Optional[Callable] = None
        self._tasks: List[asyncio.Task] = []

    # ── event plumbing ─────────────────────────────────────────────

    def on(self, event: str, callback: Callable) -> None:
        self._callbacks.setdefault(event, []).append(callback)

    async def _emit(self, event: str, data: Dict) -> None:
        for cb in self._callbacks.get(event, []):
            try:
                res = cb(data)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as e:  # noqa: BLE001 - one bad handler must not kill the feed
                logger.error("strike_ws_callback_error", event=event, error=str(e)[:200])

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _streams(self) -> List[str]:
        """Lowercase, or the venue ACKs the subscription and then never speaks (measured)."""
        out: List[str] = []
        for sym in self.symbols:
            low = sym.lower()
            out.append(f"{low}@kline_1m")
            out.append(f"{low}@trade")
        return out

    # ── REST helpers ───────────────────────────────────────────────

    async def _get_session(self):
        import aiohttp
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15),
                headers={"User-Agent": "botstrike/1.0"})
        return self._session

    async def _rest(self, path: str, params: Dict[str, Any]) -> Any:
        session = await self._get_session()
        async with session.get(f"{self.rest_base}{path}", params=params) as r:
            r.raise_for_status()
            return await r.json()

    # ── the two REST pollers ───────────────────────────────────────

    async def _depth_loop(self) -> None:
        """Full book snapshots. See the module docstring for why this is not the @depth stream."""
        while self._running:
            for sym in self.symbols:
                if not self._running:
                    break
                try:
                    book = await self._rest("/depth", {"symbol": sym, "limit": DEPTH_LEVELS})
                    bids, asks = book.get("bids") or [], book.get("asks") or []
                    if bids and asks:
                        now_ms = int(time.time() * 1000)
                        payload = {"s": sym, "b": bids, "a": asks,
                                   "E": int(book.get("E") or now_ms),
                                   "T": int(book.get("T") or book.get("E") or now_ms)}
                        await self._emit("depth", payload)
                        await self._emit("depthUpdate", payload)
                except Exception as e:  # noqa: BLE001 - a poll that fails is retried next pass
                    logger.debug("strike_depth_poll_error", symbol=sym, error=str(e)[:160])
            await asyncio.sleep(DEPTH_POLL_SEC)

    async def _mark_loop(self) -> None:
        """Mark, index and funding for every market in one request — no stream exists for these."""
        while self._running:
            try:
                rows = await self._rest("/premiumIndex", {})
                wanted = set(self.symbols)
                for row in rows if isinstance(rows, list) else [rows]:
                    sym = str(row.get("symbol", "")).upper()
                    if sym not in wanted:
                        continue
                    await self._emit("markPrice", {
                        "s": sym, "p": str(row.get("markPrice") or "0"),
                        "i": str(row.get("indexPrice") or "0"),
                        "r": str(row.get("fundingRate") or "0"),
                        "T": int(row.get("nextFundingTime") or 0),
                        "e": "markPriceUpdate",
                    })
            except Exception as e:  # noqa: BLE001
                logger.debug("strike_mark_poll_error", error=str(e)[:160])
            await asyncio.sleep(MARK_POLL_SEC)

    # ── the websocket ──────────────────────────────────────────────

    async def _process(self, msg: Dict) -> None:
        event = msg.get("e")
        if event == "error":
            logger.warning("strike_ws_error_frame", error=str(msg.get("error"))[:200])
            return
        if event == "kline":
            k = msg.get("k") or {}
            sym = str(k.get("s") or msg.get("s") or "").upper()
            if not sym:
                return
            payload = {"s": sym, "e": "kline", "channel": "kline_1m",
                       "k": {"s": sym, "t": int(k.get("t") or 0), "o": k.get("o", "0"),
                             "h": k.get("h", "0"), "l": k.get("l", "0"), "c": k.get("c", "0"),
                             "v": k.get("v", "0"), "x": bool(k.get("x", False))}}
            await self._emit("kline", payload)
            await self._emit("kline_1m", payload)
        elif event == "trade":
            sym = str(msg.get("s") or "").upper()
            if not sym:
                return
            ts = int(msg.get("T") or msg.get("E") or time.time() * 1000)
            await self._emit("trade", {"s": sym, "p": str(msg.get("p", "0")),
                                       "q": str(msg.get("q", "0")), "T": ts,
                                       "m": bool(msg.get("m", False)),
                                       "t": msg.get("t", 0), "E": ts})

    async def connect_market(self) -> None:
        """Connect, subscribe, and keep the feed alive. Starts the REST pollers alongside it."""
        self._running = True
        self._tasks = [asyncio.create_task(self._depth_loop()),
                       asyncio.create_task(self._mark_loop())]
        streams = self._streams()
        try:
            while self._running:
                try:
                    async with websockets.connect(self.ws_url, ping_interval=20, ping_timeout=20,
                                                  open_timeout=20, max_size=4_000_000) as ws:
                        self._ws = ws
                        self._connected = True
                        self._reconnect_delay = 1.0
                        await ws.send(json.dumps({"method": "SUBSCRIBE", "params": streams, "id": 1}))
                        logger.info("strike_ws_connected", streams=len(streams),
                                    symbols=len(self.symbols))
                        await self._emit("connected", {})
                        if self._on_market_connect_cb:
                            self._on_market_connect_cb()
                        async for raw in ws:
                            if not self._running:
                                break
                            try:
                                msg = json.loads(raw)
                            except (json.JSONDecodeError, TypeError):
                                continue
                            if "result" in msg:          # subscription ACK
                                continue
                            await self._process(msg)
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001 - reconnect on anything
                    self._connected = False
                    if not self._running:
                        break
                    logger.warning("strike_ws_disconnected", error=str(e)[:200],
                                   reconnect_sec=self._reconnect_delay)
                    await asyncio.sleep(self._reconnect_delay)
                    self._reconnect_delay = min(self._reconnect_delay * 2, MAX_RECONNECT_SEC)
        finally:
            self._connected = False

    async def connect_user(self) -> None:
        """No user stream is used: this bot is paper-only on Strike (nothing places an order yet)."""
        while self._running:
            await asyncio.sleep(3600)

    async def subscribe(self, channel: str, symbol: str) -> None:
        if not self._ws:
            return
        stream = f"{symbol.lower()}@{channel}"
        await self._ws.send(json.dumps({"method": "SUBSCRIBE", "params": [stream],
                                        "id": int(time.time() * 1000) % 100000}))

    async def stop(self) -> None:
        self._running = False
        self._connected = False
        for t in self._tasks:
            t.cancel()
        self._tasks = []
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001
                pass
        if self._session is not None and not self._session.closed:
            await self._session.close()
