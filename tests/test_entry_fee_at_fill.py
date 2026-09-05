"""The entry fee leaves the account at the entry fill, as a venue debits it (2026-09-05).

Before: both legs' fees were charged at the close, so a position's entry fee sat in the balance
for as long as it lived (~0.05 % of notional). Now every row has ONE cash effect
(trade_database.models.cash_effect) that the live ledger, the restore chain, the all-time
analytics and the Portfolio page all share; an EXIT row's `pnl` stays the round-trip net so no
trade statistic changes.
"""
import asyncio
import sqlite3
from types import SimpleNamespace

import pytest

from config.settings import Settings
from core.types import Side, StrategyType, Trade
from execution.paper_simulator import PaperPosition, _build_exit_features
from risk.persistence import compute_historical_risk_state
from server.serializers import serialize_trade
from trade_database.adapter import TradeDBAdapter
from trade_database.models import TradeRecord, cash_effect, fee_paid
from trade_database.repository import TradeRepository

T0 = 1_788_400_000.0


def _row(ttype, pnl=0.0, fee=0.0, charged=0.0, ts=T0):
    return SimpleNamespace(trade_type=ttype, pnl=pnl, fee=fee, entry_fee_charged=charged, timestamp=ts)


def test_the_two_cash_rules():
    assert cash_effect(_row("ENTRY", fee=0.5)) == -0.5
    assert cash_effect(_row("EXIT", pnl=10.0, fee=1.1, charged=0.5)) == pytest.approx(10.5)
    assert cash_effect(_row("FUNDING", pnl=-0.02)) == -0.02
    assert fee_paid(_row("ENTRY", fee=0.5)) == 0.5
    assert fee_paid(_row("EXIT", fee=1.1, charged=0.5)) == pytest.approx(0.6)     # its own leg
    assert fee_paid(_row("FUNDING", pnl=-0.02)) == 0.0
    # rows written before the change chain exactly as they always did
    assert cash_effect(_row("ENTRY", fee=0.0)) == 0.0
    assert cash_effect(_row("EXIT", pnl=10.0, fee=1.1)) == 10.0 and fee_paid(_row("EXIT", fee=1.1)) == 1.1
    assert cash_effect(SimpleNamespace(trade_type="", pnl=3.0, fee=0.2)) == 0.0      # untyped: never chained


def test_repository_migrates_and_stores_the_column(tmp_path):
    db = str(tmp_path / "old.db")
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE schema_version (version INTEGER NOT NULL);
        INSERT INTO schema_version VALUES (2);
        CREATE TABLE sessions (session_id TEXT PRIMARY KEY);
        CREATE TABLE trades (
            trade_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'live',
            symbol TEXT NOT NULL, side TEXT NOT NULL, price REAL NOT NULL, quantity REAL NOT NULL,
            fee REAL DEFAULT 0, fee_asset TEXT DEFAULT 'USD', pnl REAL DEFAULT 0, order_id TEXT,
            strategy TEXT, regime TEXT, trade_type TEXT, equity_before REAL, equity_after REAL,
            entry_price REAL, exit_price REAL, duration_sec REAL, micro_vpin REAL, micro_risk_score REAL,
            slippage_bps REAL DEFAULT 0, expected_cost_bps REAL DEFAULT 0, fill_probability REAL DEFAULT 0,
            order_type TEXT DEFAULT '', mae_bps REAL DEFAULT 0, mfe_bps REAL DEFAULT 0,
            signal_strength REAL DEFAULT 0, spread_bps REAL DEFAULT 0, atr REAL DEFAULT 0,
            pnl_pct REAL DEFAULT 0, timestamp REAL NOT NULL);
    """)
    conn.commit(); conn.close()
    repo = TradeRepository(db)                       # migrates 2 -> 3
    cols = {r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(trades)")}
    assert "entry_fee_charged" in cols
    assert sqlite3.connect(db).execute("SELECT version FROM schema_version").fetchone()[0] == 3
    repo.insert_trade(TradeRecord(session_id="s", source="paper", symbol="BTC-USD", side="SELL", price=2.0,
                                  quantity=1.0, fee=1.1, pnl=10.0, trade_type="EXIT", entry_fee_charged=0.5,
                                  timestamp=T0))
    row = repo.get_trades(source="paper")[0]
    assert row.entry_fee_charged == 0.5 and cash_effect(row) == pytest.approx(10.5)


def _write_round_trip(repo, adapter):
    """ENTRY fee 0.5 at T0, EXIT at T0+600 with round-trip net 10 (gross 11.1, fees 0.5 + 0.6),
    a funding settlement of -0.02 between them, and a second ENTRY (fee 0.3) still open."""
    e = Trade(symbol="BTC-USD", side=Side.BUY, price=100.0, quantity=1.0, fee=0.5, order_id="trend_entry_1",
              strategy=StrategyType.TREND_DAILY, timestamp=T0, signal_features={"action": "entry_trend"})
    adapter.on_trade(e, trade_type="ENTRY", entry_price=100.0)
    adapter.on_funding(symbol="BTC-USD", amount=-0.02, rate=0.0001, notional=100.0, strategy="TREND_DAILY", ts=T0 + 300)
    x = Trade(symbol="BTC-USD", side=Side.SELL, price=111.1, quantity=1.0, fee=1.1, order_id="trend_exit_1",
              strategy=StrategyType.TREND_DAILY, timestamp=T0 + 600, pnl=10.0,
              signal_features={"action": "exit_trend", "entry_fee_charged": 0.5})
    adapter.on_trade(x, trade_type="EXIT", entry_price=100.0, duration_sec=600.0, entry_fee_charged=0.5)
    e2 = Trade(symbol="SOL-USD", side=Side.BUY, price=50.0, quantity=1.0, fee=0.3, order_id="trend_entry_2",
               strategy=StrategyType.TREND_DAILY, timestamp=T0 + 900, signal_features={"action": "entry_trend"})
    adapter.on_trade(e2, trade_type="ENTRY", entry_price=50.0)


def test_restore_chain_and_analytics_agree_on_the_balance(tmp_path):
    repo = TradeRepository(str(tmp_path / "t.db"))
    adapter = TradeDBAdapter(repo, source="paper")
    adapter.start_session()
    _write_round_trip(repo, adapter)
    expected = 1000.0 - 0.5 - 0.02 + 10.5 - 0.3               # = 1009.68

    hist = compute_historical_risk_state(repo, 1000.0, source="paper", now=T0 + 1000)
    assert hist.equity == pytest.approx(expected)
    assert hist.closes == 1                                   # one round trip, not four rows
    assert hist.peak == pytest.approx(1000.0 - 0.5 - 0.02 + 10.5)

    from analytics.alltime import compute_alltime_performance
    p = compute_alltime_performance(repo, 1000.0, source="paper")
    assert p["pnl"] == pytest.approx(expected - 1000.0)      # the balance's move
    assert p["trade_pnl"] == pytest.approx(10.0)             # the trade statistic: round-trip net
    assert p["total_fees"] == pytest.approx(0.5 + 0.6 + 0.3)  # every fee actually charged, once
    assert p["total_trades"] == 1 and p["win_rate"] == 1.0
    curve = [v for _, v in p["equity_curve_ts"]]
    assert curve[0] == 1000.0 and curve[1] == pytest.approx(999.5) and curve[-1] == pytest.approx(expected)

    from analytics.portfolio import compute_portfolio
    rows = repo.get_trades(source="paper")
    port = compute_portfolio(rows, initial_capital=1000.0, positions=[], now_ts=T0 + 1000, equity=expected,
                             margin_used=50.0, unrealized_pnl=0.0)
    assert port["realized_pnl"] == pytest.approx(expected - 1000.0)
    assert port["fees_paid"] == pytest.approx(1.4)
    day = next(d for d in port["daily"] if d["trades"] == 1)
    assert day["pnl"] == pytest.approx(expected - 1000.0) and day["fees"] == pytest.approx(1.4)


def test_engine_applies_the_cash_rule_at_each_fill(monkeypatch):
    from main import BotStrike
    bot = BotStrike(settings=Settings(), paper=True)
    written = []
    bot.trade_db = SimpleNamespace(on_trade=lambda trade, **kw: written.append((trade, kw)),
                                   on_funding=lambda **kw: None)
    entry = Trade(symbol="BTC-USD", side=Side.BUY, price=100.0, quantity=1.0, fee=0.05, order_id="trend_entry_9",
                  strategy=StrategyType.TREND_DAILY, timestamp=T0, expected_price=100.0,
                  signal_features={"action": "entry_trend"})
    asyncio.run(bot._process_paper_fill(entry))
    assert bot.risk_manager.realized_equity == pytest.approx(1000.0 - 0.05)
    assert bot.risk_manager._consecutive_losses == 0                      # a fee is not a losing trade
    assert written[-1][1]["trade_type"] == "ENTRY" and written[-1][1]["entry_fee_charged"] == 0.0
    assert written[-1][1]["equity_after"] == pytest.approx(999.95)
    exit_ = Trade(symbol="BTC-USD", side=Side.SELL, price=101.0, quantity=1.0, fee=0.1005, order_id="trend_exit_9",
                  strategy=StrategyType.TREND_DAILY, timestamp=T0 + 60, pnl=1.0 - 0.1005, expected_price=100.0,
                  signal_features={"action": "exit_trend", "entry_price": 100.0, "entry_fee_charged": 0.05})
    asyncio.run(bot._process_paper_fill(exit_))
    # gross 1.0, fees 0.05 + 0.0505: the balance ends at 1000 + 1.0 - 0.1005
    assert bot.risk_manager.realized_equity == pytest.approx(1000.0 + 1.0 - 0.1005)
    assert written[-1][1]["trade_type"] == "EXIT" and written[-1][1]["entry_fee_charged"] == 0.05
    assert bot.risk_manager._consecutive_losses == 0                      # a win


def test_simulator_reports_the_entry_share_on_close():
    pos = PaperPosition(symbol="ETH-USD", side=Side.BUY, size=2.0, entry_price=100.0,
                        strategy=StrategyType.MEAN_REVERSION, stop_loss=90.0, take_profit=120.0, order_id="paper_entry_x")
    pos.entry_fee_rate = 0.0005
    pos.entry_fee_paid = 0.1                                   # debited at the entry fill
    pnl, fee = pos.close(110.0, 0.0005)
    assert pnl == pytest.approx(20.0 - 0.1 - 0.11) and fee == pytest.approx(0.21)
    assert pos.last_entry_fee == pytest.approx(0.1)
    feats = _build_exit_features(pos, 110.0, 60.0, "exit_signal", "SIGNAL")
    assert feats["entry_fee_charged"] == pytest.approx(0.1)


def test_serializer_no_longer_reads_a_fee_as_an_exit():
    entry = Trade(symbol="BTC-USD", side=Side.BUY, price=1.0, quantity=1.0, fee=0.05, order_id="trend_entry_1",
                  strategy=StrategyType.TREND_DAILY, signal_features={"action": "entry_trend"})
    assert serialize_trade(entry)["trade_type"] == "ENTRY" and serialize_trade(entry)["side"] == "BUY"
    rebalance = Trade(symbol="BTC-USD", side=Side.SELL, price=1.0, quantity=1.0, fee=0.1, order_id="trend_rebalance_1",
                      strategy=StrategyType.TREND_DAILY, pnl=0.0)
    assert serialize_trade(rebalance)["trade_type"] == "EXIT"


def test_simulator_reports_only_the_paid_entry_share():
    legacy = PaperPosition(symbol="ETH-USD", side=Side.BUY, size=2.0, entry_price=100.0,
                           strategy=StrategyType.MEAN_REVERSION, stop_loss=90.0, take_profit=120.0, order_id="paper_entry_l")
    legacy.entry_fee_rate = 0.0005
    legacy.close(110.0, 0.0005)
    assert legacy.last_entry_fee == 0.0                                    # nothing was paid at entry
    fresh = PaperPosition(symbol="ETH-USD", side=Side.BUY, size=2.0, entry_price=100.0,
                          strategy=StrategyType.MEAN_REVERSION, stop_loss=90.0, take_profit=120.0, order_id="paper_entry_f")
    fresh.entry_fee_rate = 0.0005
    fresh.entry_fee_paid = 0.1
    fresh.close(110.0, 0.0005)
    assert fresh.last_entry_fee == pytest.approx(0.1)
