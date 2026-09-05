"""Passive-then-aggressive execution for the trend book on the real venue (2026-09-05).

Rest post-only at the touch (maker rebate), follow the touch a bounded number of times, cross the
book after the timeout for whatever is left. Every path is exercised against a fake venue with a
fake clock; the real venue is exercised by the canary, not by tests.
"""
import asyncio

import pytest

from config.settings import Settings
from core.types import OrderType, Side
from strategies.trend_live_executor import DONE, FillResult, TrendLiveExecutor


class Clock:
    def __init__(self):
        self.now = 1_000.0

    def __call__(self):
        return self.now

    async def sleep(self, s):
        self.now += float(s)


class FakeVenue:
    """A book at (bid, ask); limit orders fill when scripted, market orders fill at the far touch."""

    def __init__(self, bid=100.0, ask=100.1, passive_fill_after=None, passive_fill_qty=None,
                 market_fills=True, touch_step_per_poll=0.0):
        self.bid, self.ask = bid, ask
        self.passive_fill_after, self.passive_fill_qty = passive_fill_after, passive_fill_qty
        self.market_fills, self.touch_step = market_fills, touch_step_per_poll
        self.orders, self.placed, self.cancelled, self.polls = {}, [], [], 0

    async def get_book_ticker(self, symbol):
        return [{"symbol": symbol, "bidPrice": self.bid, "askPrice": self.ask}]

    async def round_price(self, symbol, p):
        return round(p, 2)

    async def place_order(self, order, confirm=True):
        oid = str(len(self.placed) + 1)
        self.placed.append(order)
        o = {"orderId": oid, "status": "NEW", "executedQty": 0.0, "avgPrice": 0.0,
             "type": order.order_type, "qty": order.quantity, "price": order.price}
        if order.order_type == OrderType.MARKET and self.market_fills:
            o.update(status="FILLED", executedQty=order.quantity,
                     avgPrice=self.ask if order.side == Side.BUY else self.bid)
        self.orders[oid] = o
        return dict(o)

    async def get_order(self, symbol, order_id=None, client_order_id=None):
        o = self.orders[str(order_id)]
        self.polls += 1
        if (o["type"] == OrderType.LIMIT and o["status"] == "NEW" and self.passive_fill_after is not None
                and self.polls >= self.passive_fill_after):
            q = self.passive_fill_qty if self.passive_fill_qty is not None else o["qty"]
            o.update(executedQty=q, avgPrice=o["price"], status="FILLED" if q >= o["qty"] else "PARTIALLY_FILLED")
        if self.touch_step:
            self.bid = round(self.bid + self.touch_step, 2)
            self.ask = round(self.ask + self.touch_step, 2)
        return dict(o)

    async def cancel_order(self, symbol, oid):
        self.cancelled.append(str(oid))
        o = self.orders[str(oid)]
        if o["status"] not in DONE:
            o["status"] = "CANCELED"
        return {"ok": True}


def _executor(venue, clock, timeout=60, poll=5, repegs=2, fallback=True):
    s = Settings()
    s.trading.trend_live_passive_timeout_sec = timeout
    s.trading.trend_live_poll_sec = poll
    s.trading.trend_live_max_repegs = repegs
    s.trading.trend_live_fallback_market = fallback
    return TrendLiveExecutor(venue, s, clock=clock, sleep=clock.sleep), s


def test_a_passive_fill_earns_the_maker_rebate():
    clock, venue = Clock(), FakeVenue(passive_fill_after=2)
    ex, s = _executor(venue, clock)
    res = asyncio.run(ex.fill("BTC-USD", Side.BUY, 1.0, 100.05))
    assert res.qty == 1.0 and res.price == 100.0                       # at the bid, not the mark
    assert res.passive_qty == 1.0 and res.market_qty == 0.0
    assert res.fee == pytest.approx(100.0 * s.trading.maker_fee) and res.fee < 0
    assert len(venue.placed) == 1 and venue.placed[0].post_only and venue.placed[0].order_type == OrderType.LIMIT
    assert venue.cancelled == [] and res.repegs == 0


def test_the_timeout_crosses_the_book_for_the_remainder():
    clock, venue = Clock(), FakeVenue()                                  # the limit never fills
    ex, s = _executor(venue, clock, timeout=60, poll=5)
    res = asyncio.run(ex.fill("BTC-USD", Side.BUY, 1.0, 100.05, reduce_only=True))
    assert res.market_qty == 1.0 and res.passive_qty == 0.0 and res.price == 100.1
    assert res.fee == pytest.approx(100.1 * s.trading.taker_fee)
    assert [o.order_type for o in venue.placed] == [OrderType.LIMIT, OrderType.MARKET]
    assert all(o.reduce_only for o in venue.placed)                     # a close stays a close
    assert venue.cancelled == ["1"] and res.elapsed_sec >= 60


def test_a_partial_passive_fill_and_the_market_for_the_rest():
    clock, venue = Clock(), FakeVenue(passive_fill_after=2, passive_fill_qty=0.4)
    ex, s = _executor(venue, clock, timeout=30, poll=5)
    res = asyncio.run(ex.fill("ETH-USD", Side.BUY, 1.0, 100.05))
    assert res.passive_qty == pytest.approx(0.4) and res.market_qty == pytest.approx(0.6)
    assert res.qty == pytest.approx(1.0)
    assert res.price == pytest.approx((0.4 * 100.0 + 0.6 * 100.1) / 1.0)
    assert res.fee == pytest.approx(0.4 * 100.0 * s.trading.maker_fee + 0.6 * 100.1 * s.trading.taker_fee)


def test_follows_the_touch_a_bounded_number_of_times():
    clock, venue = Clock(), FakeVenue(touch_step_per_poll=0.5)          # the bid walks away every poll
    ex, s = _executor(venue, clock, timeout=120, poll=5, repegs=2)
    res = asyncio.run(ex.fill("SOL-USD", Side.BUY, 1.0, 100.05))
    limits = [o for o in venue.placed if o.order_type == OrderType.LIMIT]
    assert len(limits) == 3 and res.repegs == 2                          # original + two re-pegs
    assert [o.price for o in limits] == sorted(o.price for o in limits)  # each one at the new, higher bid
    assert res.market_qty == 1.0                                         # then the timeout crosses


def test_without_the_market_fallback_the_remainder_stays_unfilled():
    clock, venue = Clock(), FakeVenue()
    ex, s = _executor(venue, clock, timeout=20, poll=5, fallback=False)
    res = asyncio.run(ex.fill("XAU-USD", Side.SELL, 2.0, 100.05))
    assert res.qty == 0.0 and res.market_qty == 0.0
    assert "unfilled" in res.note and "fallback off" in res.note
    assert [o.order_type for o in venue.placed] == [OrderType.LIMIT] and venue.cancelled == ["1"]


def test_a_sell_rests_at_the_ask():
    clock, venue = Clock(), FakeVenue(passive_fill_after=1)
    ex, s = _executor(venue, clock)
    res = asyncio.run(ex.fill("XAG-USD", Side.SELL, 3.0, 100.05, reduce_only=True))
    assert res.price == pytest.approx(100.1) and venue.placed[0].price == pytest.approx(100.1) and venue.placed[0].reduce_only


def test_a_venue_error_never_leaves_an_order_resting():
    class Broken(FakeVenue):
        async def get_order(self, symbol, order_id=None, client_order_id=None):
            self.polls += 1
            if self.polls == 1:
                raise RuntimeError("venue hiccup")
            return await super().get_order(symbol, order_id=order_id, client_order_id=client_order_id)

    clock, venue = Clock(), Broken()
    ex, s = _executor(venue, clock)
    res = asyncio.run(ex.fill("BTC-USD", Side.BUY, 1.0, 100.05))
    assert res.qty == 0.0 and res.note.startswith("error")
    assert venue.cancelled == ["1"]                                      # cancelled on the way out
    assert isinstance(res, FillResult)
