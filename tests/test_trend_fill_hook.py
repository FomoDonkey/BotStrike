"""The trend book's fills must go through whatever wraps `_process_paper_fill` at call time.

The bridge wraps that method after the engine is built (activity feed, socket "trade" frame,
recent fills); a bound method captured at construction skipped the wrapper, so the daily
rebalance's six fills never reached the Activity feed or the toasts (Edgar, 2026-09-05).
"""
import asyncio

from config.settings import Settings
from core.types import Side, StrategyType, Trade
from main import BotStrike


def test_trend_fills_reach_the_wrapped_paper_fill_handler():
    bot = BotStrike(settings=Settings(), paper=True)
    assert bot.trend_engine is not None
    seen = []

    async def wrapped(trade):
        seen.append(trade.symbol)

    bot._process_paper_fill = wrapped                 # what the bridge does after construction
    t = Trade(symbol="BTC-USD", side=Side.BUY, price=1.0, quantity=1.0, fee=0.0,
              strategy=StrategyType.TREND_DAILY, timestamp=0.0)
    asyncio.run(bot.trend_engine._on_fill(t))
    assert seen == ["BTC-USD"]
