"""Passive-then-aggressive execution for the daily trend book on the real venue (2026-09-05).

The daily rebalance is not in a hurry, so it should not pay to be in a hurry:

  1. rest a POST-ONLY limit at the touch (buy at the best bid, sell at the best ask) — a maker
     fill earns Strike's tier-0 rebate (-0.5 bps) instead of paying the taker fee (5 bps);
  2. follow the touch if it walks away, a bounded number of times;
  3. after `trend_live_passive_timeout_sec`, cancel and cross the book with a market order for
     whatever is left, so the rebalance always completes the same day.

Measured lever (tasks/audit_optimality_2026-09-05.md): 15.9 turns a year x 5.5 bps ≈ 0.9 % of
equity a year at 1x, scaling with the book's exposure. Never used in paper: paper fills stay at
the mark ± the market's measured half-spread + taker fee, which assumes nothing about queue
position. Fees are ESTIMATED from the venue's rates until the fill history is reconciled.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import structlog

from core.types import Order, OrderType, Side, StrategyType, TimeInForce

logger = structlog.get_logger(__name__)

DONE = {"FILLED", "CANCELED", "REJECTED", "EXPIRED"}


@dataclass
class FillResult:
    qty: float = 0.0                 # filled quantity (base units)
    price: float = 0.0               # average fill price over both phases
    fee: float = 0.0                 # venue fee, negative when the rebate outweighs the taker leg
    passive_qty: float = 0.0
    market_qty: float = 0.0
    order_ids: List[str] = field(default_factory=list)
    elapsed_sec: float = 0.0
    repegs: int = 0
    fee_estimated: bool = True
    note: str = ""


def _num(x: Any, default: float = 0.0) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    return v if v == v else default


def _pick(data: Any, symbol: str) -> Dict:
    if isinstance(data, list):
        return next((d for d in data if str(d.get("symbol", "")).upper() == symbol.upper()),
                    data[0] if data else {})
    return data or {}


class TrendLiveExecutor:
    def __init__(self, client: Any, settings: Any, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], Any] = asyncio.sleep) -> None:
        tc = settings.trading
        self.client = client
        self.timeout = float(getattr(tc, "trend_live_passive_timeout_sec", 600) or 600)
        self.poll = max(0.2, float(getattr(tc, "trend_live_poll_sec", 5.0) or 5.0))
        self.max_repegs = max(0, int(getattr(tc, "trend_live_max_repegs", 5) or 0))
        self.fallback_market = bool(getattr(tc, "trend_live_fallback_market", True))
        self.maker_fee = float(getattr(tc, "maker_fee", -0.00005))
        self.taker_fee = float(getattr(tc, "taker_fee", 0.0005))
        self._clock = clock
        self._sleep = sleep

    # ── venue helpers ──────────────────────────────────────────────────────
    async def touch(self, symbol: str) -> Tuple[float, float]:
        d = _pick(await self.client.get_book_ticker(symbol), symbol)
        bid = _num(d.get("bidPrice", d.get("bid_price", d.get("bid"))))
        ask = _num(d.get("askPrice", d.get("ask_price", d.get("ask"))))
        if bid <= 0 or ask <= 0 or ask < bid:
            raise RuntimeError(f"no usable book for {symbol}: bid={bid} ask={ask}")
        return bid, ask

    async def _status(self, symbol: str, oid: str, cid: str) -> Dict:
        if oid:
            return await self.client.get_order(symbol, order_id=oid)
        return await self.client.get_order(symbol, client_order_id=cid)

    async def _cancel(self, symbol: str, oid: str, cid: str) -> Dict:
        try:
            await self.client.cancel_order(symbol, oid)
        except Exception as e:  # noqa: BLE001 - the state read below is what counts
            logger.warning("trend_live_cancel_error", symbol=symbol, order_id=oid, error=str(e)[:120])
        st: Dict = {}
        for _ in range(3):
            st = await self._status(symbol, oid, cid)
            if st.get("status") in DONE:
                break
            await self._sleep(min(self.poll, 1.0))
        return st

    async def _place(self, symbol: str, side: Side, qty: float, price: Optional[float],
                     reduce_only: bool, market: bool) -> Tuple[Dict, str, str]:
        cid = f"trend-{uuid.uuid4().hex[:12]}"
        order = Order(symbol=symbol, side=side, order_type=OrderType.MARKET if market else OrderType.LIMIT,
                      quantity=qty, price=None if market else price, time_in_force=TimeInForce.GTC,
                      post_only=not market, reduce_only=reduce_only, client_order_id=cid,
                      strategy=StrategyType.TREND_DAILY)
        ack = await self.client.place_order(order, confirm=True)
        return ack or {}, str((ack or {}).get("orderId") or ""), cid

    # ── the fill ───────────────────────────────────────────────────────────
    async def fill(self, symbol: str, side: Side, qty: float, ref_price: float,
                   reduce_only: bool = False) -> FillResult:
        """Fill `qty` of `symbol` on `side`; returns what filled, at what average price, and the fee."""
        start = self._clock()
        res = FillResult()
        remaining = float(qty)
        cost_passive = cost_market = 0.0
        buy = side == Side.BUY
        oid = cid = ""
        resting = 0.0
        seen = 0.0                       # executedQty already absorbed for the resting order

        def absorb(st: Dict) -> None:
            nonlocal remaining, seen, cost_passive
            ex = _num(st.get("executedQty"))
            new = max(0.0, ex - seen)
            if new > 0:
                px = _num(st.get("avgPrice")) or resting or ref_price
                cost_passive += new * px
                remaining -= new
                seen = ex
                res.passive_qty += new

        try:
            while remaining > 1e-12 and self._clock() - start < self.timeout:
                if not oid and not cid:
                    bid, ask = await self.touch(symbol)
                    want = await self.client.round_price(symbol, bid if buy else ask)
                    ack, oid, cid = await self._place(symbol, side, remaining, want, reduce_only, market=False)
                    resting, seen = float(want), 0.0
                    if oid:
                        res.order_ids.append(oid)
                    if str(ack.get("status", "")).upper() == "REJECTED":
                        # post-only would have crossed: the touch moved between the read and the send
                        oid = cid = ""
                        res.repegs += 1
                        if res.repegs > self.max_repegs:
                            break
                        await self._sleep(min(self.poll, 1.0))
                        continue
                    absorb(ack)
                    if remaining <= 1e-12:
                        oid = cid = ""
                        break
                    await self._sleep(self.poll)
                st = await self._status(symbol, oid, cid)
                oid = oid or str(st.get("orderId") or "")
                absorb(st)
                status = str(st.get("status", "")).upper()
                if status == "FILLED" or remaining <= 1e-12:
                    oid = cid = ""
                    break
                if status in ("CANCELED", "REJECTED", "EXPIRED"):
                    oid = cid = ""
                    res.repegs += 1
                    if res.repegs > self.max_repegs:
                        break
                    continue
                # still resting: is it still at the touch?
                bid, ask = await self.touch(symbol)
                now_touch = bid if buy else ask
                moved_away = (buy and now_touch > resting + 1e-12) or ((not buy) and now_touch < resting - 1e-12)
                if moved_away and res.repegs < self.max_repegs:
                    st = await self._cancel(symbol, oid, cid)
                    absorb(st)
                    oid = cid = ""
                    res.repegs += 1
                    continue
                await self._sleep(self.poll)
            if oid or cid:                                       # timed out or out of re-pegs
                st = await self._cancel(symbol, oid, cid)
                absorb(st)
                oid = cid = ""
            if remaining > 1e-12:
                if not self.fallback_market:
                    res.note = f"passive timeout: {remaining:.8g} unfilled, market fallback off"
                else:
                    ack, moid, mcid = await self._place(symbol, side, remaining, None, reduce_only, market=True)
                    if moid:
                        res.order_ids.append(moid)
                    st = ack
                    for _ in range(6):
                        if str(st.get("status", "")).upper() in DONE:
                            break
                        await self._sleep(min(self.poll, 1.0))
                        st = await self._status(symbol, moid, mcid)
                    ex = _num(st.get("executedQty"))
                    if ex > 0:
                        bid, ask = await self.touch(symbol)
                        px = _num(st.get("avgPrice")) or (ask if buy else bid)
                        cost_market += ex * px
                        remaining -= ex
                        res.market_qty += ex
                    if remaining > 1e-12:
                        res.note = f"market fallback left {remaining:.8g} unfilled ({st.get('status')})"
        except Exception as e:  # noqa: BLE001 - never leave an order resting after an error
            logger.error("trend_live_fill_error", symbol=symbol, side=side.value, error=str(e)[:200])
            res.note = f"error: {str(e)[:160]}"
            if oid or cid:
                try:
                    st = await self._cancel(symbol, oid, cid)
                    absorb(st)
                except Exception:  # noqa: BLE001
                    pass
        res.qty = max(0.0, float(qty) - max(remaining, 0.0))
        res.price = (cost_passive + cost_market) / res.qty if res.qty > 0 else 0.0
        res.fee = cost_passive * self.maker_fee + cost_market * self.taker_fee
        res.elapsed_sec = self._clock() - start
        logger.info("trend_live_fill", symbol=symbol, side=side.value, wanted=round(float(qty), 8),
                    filled=round(res.qty, 8), price=round(res.price, 6), passive=round(res.passive_qty, 8),
                    market=round(res.market_qty, 8), fee=round(res.fee, 6), repegs=res.repegs,
                    elapsed_sec=round(res.elapsed_sec, 1), note=res.note)
        return res
