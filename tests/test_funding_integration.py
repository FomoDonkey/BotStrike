"""Funding rows are cash flows: they move equity everywhere, and never pollute trade statistics."""
from types import SimpleNamespace

import pytest

from analytics.alltime import compute_alltime_performance
from analytics.edge import compute_edge_stats
from analytics.portfolio import compute_portfolio
from risk.persistence import compute_historical_risk_state

DAY = 86400.0
T0 = 1_788_000_000.0


def _row(ts, ttype, pnl, symbol="BTC-USD", strategy="TREND_DAILY", price=70000.0, qty=0.01, fee=0.0):
    return SimpleNamespace(timestamp=ts, symbol=symbol, side="BUY" if ttype != "FUNDING" else "FUNDING",
                           trade_type=ttype, pnl=pnl, fee=fee, price=price, quantity=qty, strategy=strategy,
                           duration_sec=3600.0, regime="RANGING", order_id="o", trade_id="t",
                           entry_price=price, exit_price=price, equity_after=0.0, session_id="s",
                           source="paper", fee_asset="USD", mae_bps=0.0, mfe_bps=0.0, slippage_bps=0.0,
                           order_type="MARKET", signal_strength=0.0, spread_bps=0.0,
                           notional=price * qty, equity_before=0.0, atr=0.0, pnl_pct=0.0,
                           micro_vpin=0.0, micro_risk_score=0.0, expected_cost_bps=0.0, fill_probability=0.0,
                           id=0, is_win=pnl > 0)


def _repo(rows):
    return SimpleNamespace(get_trades=lambda **kw: list(rows))


ROWS = [
    _row(T0, "ENTRY", 0.0),
    _row(T0 + 3600, "EXIT", 10.0),                      # one winning trade
    _row(T0 + 4000, "FUNDING", -0.30),                  # funding paid on the open book
    _row(T0 + DAY, "ENTRY", 0.0),
    _row(T0 + DAY + 3600, "EXIT", -4.0),                # one losing trade
    _row(T0 + DAY + 8000, "FUNDING", -0.20),
]


def test_alltime_counts_two_trades_and_still_moves_equity():
    p = compute_alltime_performance(_repo(ROWS), 1000.0, source="paper")
    assert p["total_trades"] == 2 and p["win_rate"] == pytest.approx(0.5)
    assert p["trade_pnl"] == pytest.approx(10.0 - 4.0)  # trades only, for the statistics
    assert p["pnl"] == pytest.approx(5.5)               # all-time cash: trades + funding
    assert p["funding_paid"] == pytest.approx(-0.5)


def test_edge_stats_ignore_funding_rows():
    e = compute_edge_stats(_repo(ROWS), source="paper", window=200)
    st = e["strategies"]["TREND_DAILY"]
    assert st["n"] == 2                                  # not 4
    assert st["win_rate"] == pytest.approx(0.5)


def test_risk_state_treats_funding_as_realized_cash():
    hist = compute_historical_risk_state(_repo(ROWS), 1000.0, now=T0 + DAY + 9000)
    assert hist.equity == pytest.approx(1000.0 + 10.0 - 4.0 - 0.5)   # funding included
    assert hist.peak >= hist.equity


def test_portfolio_separates_funding_from_trades_and_volume():
    p = compute_portfolio(ROWS, 1000.0, [], T0 + 2 * DAY, equity=1005.5, margin_used=0.0, unrealized_pnl=0.0)
    assert p["realized_pnl"] == pytest.approx(5.5)       # 10 - 4 - 0.5
    assert p["funding_paid"] == pytest.approx(-0.5)
    assert p["analysis"]["closed_trades"] == 2
    assert p["perf_30d"]["trades"] == 2 and p["perf_30d"]["win_rate"] == pytest.approx(0.5, abs=1e-4)
    by = {s["strategy"]: s for s in p["by_strategy"]}
    assert by["TREND_DAILY"]["trades"] == 2 and by["TREND_DAILY"]["realized"] == pytest.approx(6.0)
    # funding rows carry quantity 0 → they must not inflate traded volume
    assert p["alltime_volume"] == pytest.approx(4 * 700.0)
    day0 = p["daily"][0]
    assert day0["pnl"] == pytest.approx(10.0 - 0.30) and day0["trades"] == 1


def test_funding_row_written_by_the_adapter(tmp_path):
    from trade_database.repository import TradeRepository
    from trade_database.adapter import TradeDBAdapter

    repo = TradeRepository(str(tmp_path / "t.db"))
    adapter = TradeDBAdapter(repo, source="paper")
    adapter.start_session()
    adapter.on_funding(symbol="ETH-USD", amount=-0.42, rate=0.0001, notional=4200.0, mark_price=2100.0,
                       strategy="TREND_DAILY", periods=2, equity_before=1000.0, equity_after=999.58, ts=T0)
    rows = repo.get_trades(source="paper")
    assert len(rows) == 1
    r = rows[0]
    assert r.trade_type == "FUNDING" and r.pnl == pytest.approx(-0.42) and r.quantity == 0.0
    assert r.symbol == "ETH-USD" and r.strategy == "TREND_DAILY" and r.side == "FUNDING"
    assert r.signal_strength == pytest.approx(0.0001)      # the rate is kept for the audit trail


def test_engine_prefers_the_venue_funding_rate_over_the_intraday_feed(monkeypatch):
    """Strike charges materially more than Binance on crypto and pays the longs on WTI/NAS100:
    the rate that must be charged is the one of the venue we will execute on."""
    import main as m
    from types import SimpleNamespace as NS

    eng = object.__new__(m.BotStrike)
    eng.settings = NS(symbol_names=["BTC-USD", "ETH-USD"], trading=NS(funding_interval_hours=8))
    eng.paper_sim = None
    eng.trend_engine = None
    eng._venue_funding = {"BTC-USD": 0.000098, "WTI-USD": -0.00018}      # Strike
    eng._venue_funding_ts = 9e18                                          # cache is fresh
    eng.market_data = NS(get_snapshot=lambda s: NS(funding_rate=0.000037))  # Binance feed
    eng._funding_positions = lambda: [{"symbol": "BTC-USD"}, {"symbol": "WTI-USD"}]

    rates = m.BotStrike._funding_rates(eng)
    assert rates["BTC-USD"] == pytest.approx(0.000098)   # venue wins over the feed's 0.000037
    assert rates["WTI-USD"] == pytest.approx(-0.00018)   # market outside the feed still charged
    assert rates["ETH-USD"] == pytest.approx(0.000037)   # feed only fills the gap, 8 h clock = no scaling

    # On the venue's real HOURLY clock the feed's 8 h rate has to be scaled, or it charges 8x too much
    eng.settings = NS(symbol_names=["BTC-USD", "ETH-USD"], trading=NS(funding_interval_hours=1))
    hourly = m.BotStrike._funding_rates(eng)
    assert hourly["ETH-USD"] == pytest.approx(0.000037 / 8)
    assert hourly["BTC-USD"] == pytest.approx(0.000098)   # a venue rate is already per settlement

    # a venue rate of exactly 0 is an answer, not a gap: XAU/XAG/SP500 price at 0 on many days
    eng._venue_funding = {"XAU-USD": 0.0}
    eng._funding_positions = lambda: [{"symbol": "XAU-USD"}]
    assert m.BotStrike._funding_rates(eng)["XAU-USD"] == 0.0


def test_alltime_pnl_and_equity_curve_include_funding():
    """/api/performance must agree with /api/portfolio and with the account: funding is realized
    cash. Before this fix performance reported +16.06 while the account held 16.04 (2026-09-03)."""
    p = compute_alltime_performance(_repo(ROWS), 1000.0, source="paper")
    assert p["total_trades"] == 2                       # funding rows are not trades
    assert p["trade_pnl"] == pytest.approx(6.0)
    assert p["funding_paid"] == pytest.approx(-0.5)
    assert p["pnl"] == pytest.approx(5.5)               # trades + funding
    curve = p["equity_curve_ts"]
    assert curve[-1][1] == pytest.approx(1000.0 + 5.5)  # the curve ends where the account is
    assert p["peak_equity"] >= curve[-1][1]
    # a book with no closed trades but paid funding still reports the cash flow
    only_funding = [r for r in ROWS if r.trade_type != "EXIT"]
    q = compute_alltime_performance(_repo(only_funding), 1000.0, source="paper")
    assert q["total_trades"] == 0 and q["pnl"] == pytest.approx(-0.5)


def test_funding_panel_shows_what_the_engine_charges(monkeypatch):
    """The panel annualised Binance's 8 h rate on the venue's hourly clock (87 %/yr for BTC), listed
    markets the book does not hold and omitted four it does (audit 2026-09-03)."""
    from types import SimpleNamespace as NS
    from server import bridge

    eng = NS(
        _venue_funding={"BTC-USD": 1.88e-05, "XAU-USD": 0.0},
        settings=NS(symbol_names=["BTC-USD", "ETH-USD"], trading=NS(funding_interval_hours=1)),
        market_data=NS(get_snapshot=lambda s: NS(funding_rate=0.0001)),
        _funding_positions=lambda: [{"symbol": "BTC-USD"}, {"symbol": "XAU-USD"}],
    )
    r = bridge._live_funding_rates(eng, 1)

    assert set(r) == {"BTC-USD", "ETH-USD", "XAU-USD"}          # every held market is present
    assert r["BTC-USD"]["rate"] == pytest.approx(1.88e-05) and r["BTC-USD"]["source"] == "venue"
    assert r["BTC-USD"]["annualized_pct"] == pytest.approx(0.1647, abs=1e-4)   # 16.5 %/yr, not 87 %
    assert r["XAU-USD"]["rate"] == 0.0 and r["XAU-USD"]["held"] is True        # a zero rate is shown
    assert r["ETH-USD"]["held"] is False
    assert r["ETH-USD"]["rate"] == pytest.approx(0.0001 / 8)     # the feed's 8 h rate, scaled
