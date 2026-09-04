"""`/api/trades?limit=N` counts FILLS: funding settles hourly on every open market (six positions
write 144 rows a day), so a limit shared with them held a day or two of carry and no trades."""
import asyncio
from types import SimpleNamespace

from core.types import Side, StrategyType, Trade
from trade_database.adapter import TradeDBAdapter
from trade_database.repository import TradeRepository
import server.bridge as bridge

T0 = 1_788_400_000.0


def _fill(adapter, ts, pnl=0.0, trade_type="EXIT", symbol="BTC-USD"):
    t = Trade(symbol=symbol, side=Side.SELL if trade_type == "EXIT" else Side.BUY, price=80_000.0, quantity=0.001,
              fee=0.04, order_id=f"paper_{trade_type.lower()}_{int(ts)}", strategy=StrategyType.TREND_DAILY,
              timestamp=ts, pnl=pnl)
    adapter.on_trade(t, trade_type=trade_type, entry_price=79_000.0, duration_sec=3600.0 if trade_type == "EXIT" else 0.0)


def test_limit_counts_fills_and_keeps_the_funding_inside_their_window(tmp_path, monkeypatch):
    repo = TradeRepository(str(tmp_path / "t.db"))
    adapter = TradeDBAdapter(repo, source="paper")
    adapter.start_session()
    # three round trips, an hour apart, with 30 funding settlements between and after them
    for i, ts in enumerate((T0, T0 + 3600, T0 + 7200)):
        _fill(adapter, ts, trade_type="ENTRY")
        _fill(adapter, ts + 600, pnl=1.0 + i, trade_type="EXIT")
    for k in range(30):
        adapter.on_funding(symbol="BTC-USD", amount=-0.001, rate=0.00001, notional=80.0, strategy="TREND_DAILY",
                           ts=T0 + 300 + k * 300)
    monkeypatch.setattr(bridge.state, "engine", SimpleNamespace(trade_repo=repo))

    out = asyncio.run(bridge.get_trades(limit=4))
    rows = out["trades"]
    fills = [r for r in rows if r["trade_type"] != "FUNDING"]
    funding = [r for r in rows if r["trade_type"] == "FUNDING"]
    # the four NEWEST fills, most recent first — not four rows of carry
    assert len(fills) == 4
    assert [r["trade_type"] for r in fills] == ["EXIT", "ENTRY", "EXIT", "ENTRY"]
    assert fills[0]["pnl"] == 3.0
    # carry only from the oldest of those fills onward
    oldest = min(r["exit_ts"] or r["entry_ts"] for r in fills)
    assert funding and all(f["exit_ts"] >= oldest or f["entry_ts"] >= oldest for f in funding)
    assert len(funding) < 30
    # newest first throughout
    stamps = [r["exit_ts"] or r["entry_ts"] for r in rows]
    assert stamps == sorted(stamps, reverse=True)


def test_repository_exclusion_keeps_legacy_rows_without_a_type(tmp_path):
    repo = TradeRepository(str(tmp_path / "t.db"))
    adapter = TradeDBAdapter(repo, source="paper")
    adapter.start_session()
    _fill(adapter, T0, trade_type="ENTRY")
    adapter.on_funding(symbol="BTC-USD", amount=-0.001, rate=0.00001, notional=80.0, strategy="TREND_DAILY", ts=T0 + 1)
    with repo._connect() as conn:                      # a row written before trade_type existed
        conn.execute("UPDATE trades SET trade_type = NULL WHERE trade_type = 'ENTRY'")
    rows = repo.get_trades(source="paper", exclude_trade_type="FUNDING")
    assert len(rows) == 1 and rows[0].symbol == "BTC-USD" and rows[0].trade_type != "FUNDING"
