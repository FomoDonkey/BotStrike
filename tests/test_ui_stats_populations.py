"""UI audit round 21 (2026-09-21): what the statistics say when the strategy has closed no round
trip yet, and what the activity feed calls a fill that grows a held position.

- Holding times belong to every close that FLATTENED a position (round trips and forced closes);
  a rebalance trim leaves the position open and has none. With 0 round trips and 5 forced closes
  the Portfolio page said "Trading style n/a · Avg trade duration ---" beside 31 closes.
- The 30-day window reports every close and how many ended positive, so a window with no round
  trip still says what the money did.
- An ENTRY that adds to a position the book already holds is "Added to LONG", not "Opened LONG":
  five re-buys at 22:08Z printed as five new trades.
"""
from types import SimpleNamespace

from analytics.activity import ActivityLog
from analytics.portfolio import compute_portfolio

DAY = 86400.0


def _row(ttype, symbol, ts, pnl=0.0, order_id="", duration=0.0, qty=1.0, price=100.0):
    return SimpleNamespace(trade_type=ttype, symbol=symbol, timestamp=ts, pnl=pnl, order_id=order_id,
                           duration_sec=duration, quantity=qty, price=price, fee=0.0, strategy="TREND_DAILY",
                           exit_reason=("rebalance" if order_id.startswith("trend_rebalance_") else
                                        "universe" if order_id.startswith("trend_universe_") else ""),
                           entry_fee_charged=0.0, side="SELL" if ttype == "EXIT" else "BUY", leverage=1)


def test_holding_times_come_from_flattening_closes_not_only_round_trips():
    t0 = 1_760_000_000.0
    rows = [
        _row("ENTRY", "BTC-USD", t0),
        _row("EXIT", "BTC-USD", t0 + 2 * DAY, pnl=1.0, order_id="trend_rebalance_1", duration=2 * DAY),   # trim: no hold
        _row("EXIT", "BTC-USD", t0 + 10 * DAY, pnl=5.0, order_id="trend_universe_1", duration=10 * DAY),  # forced: hold 10 d
        _row("ENTRY", "ETH-USD", t0),
        _row("EXIT", "ETH-USD", t0 + 4 * DAY, pnl=-1.0, order_id="trend_manual_1", duration=4 * DAY),     # forced: hold 4 d
    ]
    p = compute_portfolio(rows, 1000.0, [], t0 + 12 * DAY, 1005.0, 0.0, 0.0)
    a = p["analysis"]
    assert a["closed_trades"] == 0                       # the statistics population is still empty
    assert a["flattened"] == 2 and a["forced"] == 2
    assert a["avg_hold_sec"] == 7 * DAY and a["median_hold_sec"] == 7 * DAY
    assert a["trading_style"] == "Position"              # 7 days median -> not "n/a"
    w = p["perf_30d"]
    assert w["trades"] == 0                               # no round trip in the window ...
    assert w["closes_all"] == 3 and w["closes_positive"] == 2   # ... but three closes, two positive
    assert w["forced"] == 2 and w["trims"] == 1


def test_activity_feed_says_added_to_for_a_fill_on_a_held_position(tmp_path):
    log = ActivityLog(path=str(tmp_path / "activity.json"))
    opened = log.record_fill({"symbol": "ADA-USD", "side": "BUY", "trade_type": "ENTRY", "quantity": 274,
                              "price": 0.245, "strategy": "TREND_DAILY", "timestamp": 1.0})
    added = log.record_fill({"symbol": "ADA-USD", "side": "BUY", "trade_type": "ENTRY", "quantity": 274,
                             "price": 0.245, "strategy": "TREND_DAILY", "timestamp": 2.0,
                             "adds_to_position": True, "position_size_after": 327.0})
    assert opened["title"] == "Opened LONG ADA-USD"
    assert added["title"] == "Added to LONG ADA-USD"
    assert "→ 327 ADA held" in added["detail"]
