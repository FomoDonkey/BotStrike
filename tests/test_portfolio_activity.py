"""v2.16 backend for the Strike-style UI: portfolio analytics, activity feed, funding history, ops, CSV."""
import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import server.bridge as bridge
from analytics import activity as act
from analytics.portfolio import compute_portfolio
from config.settings import Settings
from core.types import MarketSnapshot
from execution.paper_simulator import PaperTradingSimulator
from risk.risk_manager import RiskManager

DAY = 86400.0
T0 = 1_788_000_000.0                       # 2026-08-25 ~ UTC


def _t(ts, symbol="ETH-USD", side="BUY", ttype="ENTRY", pnl=0.0, fee=0.04, price=2000.0, qty=0.05,
       strategy="MEAN_REVERSION", dur=0.0):
    return SimpleNamespace(timestamp=ts, symbol=symbol, side=side, trade_type=ttype, pnl=pnl, fee=fee, price=price,
                           quantity=qty, strategy=strategy, duration_sec=dur, regime="RANGING", order_id="x",
                           trade_id="id", entry_price=price, exit_price=price, equity_after=0.0, session_id="s",
                           source="paper", fee_asset="USD")


def _trades():
    return [
        _t(T0, ttype="ENTRY"), _t(T0 + 3600, ttype="EXIT", pnl=5.0, dur=3600),                    # day 1 win
        _t(T0 + DAY, ttype="ENTRY"), _t(T0 + DAY + 7200, ttype="EXIT", pnl=-2.0, dur=7200),         # day 2 loss
        _t(T0 + 2 * DAY, ttype="ENTRY", strategy="TREND_DAILY", symbol="BTC-USD", price=70000.0, qty=0.002),
        _t(T0 + 2 * DAY + DAY, ttype="EXIT", pnl=3.0, dur=DAY, strategy="TREND_DAILY", symbol="BTC-USD",
           price=71500.0, qty=0.002),                                                                # day 4 win
    ]


def test_compute_portfolio_numbers():
    now = T0 + 5 * DAY + 100
    positions = [{"symbol": "SOL-USD", "side": "BUY", "notional": 80.0, "unrealized_pnl": 1.5, "strategy": "TREND_DAILY"}]
    p = compute_portfolio(_trades(), 1000.0, positions, now, equity=1007.5, margin_used=80.0, unrealized_pnl=1.5)
    assert p["realized_pnl"] == 6.0 and p["alltime_pnl"] == 7.5 and p["cash"] == 927.5
    assert p["fees_paid"] == pytest.approx(0.24) and p["alltime_volume"] == pytest.approx(4 * 100.0 + 140.0 + 143.0)
    assert p["leverage"] == pytest.approx(80 / 1007.5, abs=1e-6) and p["margin_usage"] == pytest.approx(80 / 1007.5, abs=1e-6)
    assert p["trend_book_notional"] == 80.0 and p["bias"]["long_pct"] == 1.0
    a = p["analysis"]
    assert a["closed_trades"] == 3 and a["longest_win_streak_days"] == 1 and a["trading_style"] == "Day trader"
    assert a["median_hold_sec"] == 7200 and a["avg_hold_sec"] == pytest.approx((3600 + 7200 + DAY) / 3, rel=1e-6)
    assert p["perf_30d"]["trades"] == 3 and p["perf_30d"]["win_rate"] == pytest.approx(2 / 3, abs=1e-4)
    assert p["perf_30d"]["sharpe"] is None and p["perf_30d"]["sharpe_valid"] is False
    assert p["perf_30d"]["drawdown"] == pytest.approx(2.0 / 1000.0)
    assert len(p["daily"]) == 6 and p["daily"][0]["pnl"] == 5.0 and p["daily"][1]["pnl"] == -2.0
    assert p["daily"][-1]["equity"] == pytest.approx(1000 + 6.0 + 1.5) and p["daily"][3]["equity"] == 1006.0
    assert len(p["win_days"]) == 18 and [d["result"] for d in p["win_days"][-6:]] == ["win", "loss", "flat", "win", "flat", "flat"]
    by = {s["strategy"]: s for s in p["by_strategy"]}
    assert set(by) == {"MEAN_REVERSION", "TREND_DAILY"}
    assert by["MEAN_REVERSION"]["trades"] == 2 and by["MEAN_REVERSION"]["realized"] == 3.0
    assert by["MEAN_REVERSION"]["profit_factor"] == 2.5 and by["MEAN_REVERSION"]["win_rate"] == 0.5
    assert by["TREND_DAILY"]["open_positions"] == 1 and by["TREND_DAILY"]["unrealized"] == 1.5 and by["TREND_DAILY"]["pnl"] == 4.5
    assert by["TREND_DAILY"]["equity_curve"] == [[pytest.approx(T0 + 3 * DAY), 3.0]]


def test_compute_portfolio_empty():
    p = compute_portfolio([], 300.0, [], T0, equity=300.0, margin_used=0.0, unrealized_pnl=0.0)
    assert p["alltime_pnl"] == 0.0 and p["leverage"] == 0.0 and p["bias"]["long_pct"] is None
    assert p["analysis"]["trading_style"] == "n/a" and len(p["daily"]) == 1 and p["by_strategy"] == []


def test_activity_log_persists_and_maps_events(tmp_path):
    path = str(tmp_path / "activity.json")
    log = act.ActivityLog(path, maxlen=5)
    log.record_fill({"symbol": "BTC-USD", "side": "BUY", "trade_type": "ENTRY", "quantity": 0.0015, "price": 70000.0,
                     "strategy": "TREND_DAILY", "timestamp": T0})
    log.record_fill({"symbol": "BTC-USD", "side": "BUY", "trade_type": "EXIT", "quantity": 0.0015, "price": 71000.0,
                     "strategy": "TREND_DAILY", "timestamp": T0 + 10, "pnl": 1.5, "roe_pct": 0.0143, "exit_reason": "take_profit"})
    rows = log.list()
    assert rows[0]["title"] == "Closed LONG BTC-USD" and rows[0]["pnl"] == 1.5 and "take profit" in rows[0]["detail"]
    assert rows[1]["title"] == "Opened LONG BTC-USD" and "$105.00" in rows[1]["detail"] and "Trend Daily" in rows[1]["detail"]
    # ring buffer + persistence
    for i in range(6):
        log.add("system", f"e{i}", ts=T0 + 100 + i)
    assert len(log.list(limit=50)) == 5 and log.list(limit=1)[0]["title"] == "e5"
    again = act.ActivityLog(path)
    assert [r["title"] for r in again.list(limit=50)] == ["e5", "e4", "e3", "e2", "e1"]
    # event mapping
    assert act.build_event_row("regime_changed", {"symbol": "BTC-USD", "new": "RANGING", "old": "UNKNOWN"}) is None
    r = act.build_event_row("regime_changed", {"symbol": "BTC-USD", "new": "RANGING", "old": "BREAKOUT"})
    assert r["kind"] == "regime" and r["title"] == "Regime RANGING"
    r = act.build_event_row("trend_daily_run_ok", {"positions": 3, "targets": {"BTCUSDT": 0.118}})
    assert r["kind"] == "run" and "3 positions" in r["detail"] and "BTCUSDT 11.8%" in r["detail"]
    r = act.build_event_row("strategy_disabled_by_performance", {"strategy": "MEAN_REVERSION", "reason": "t -3"})
    assert r["kind"] == "kill" and r["level"] == "warning"
    assert act.build_event_row("tick_quality", {}) is None
    # processor never alters the event dict
    act._LOG = log
    ev = {"event": "circuit_breaker_triggered", "daily_pnl": -21.0}
    assert act.activity_processor(None, "warning", ev) is ev
    assert log.list(limit=1)[0]["kind"] == "risk"
    act._LOG = None


@pytest.fixture
def st(monkeypatch, tmp_path):
    monkeypatch.delenv("BOTSTRIKE_AUTOSTART", raising=False)
    monkeypatch.setenv("BOTSTRIKE_CONFIG_OVERRIDES", str(tmp_path / "o.json"))
    monkeypatch.setenv("BOTSTRIKE_OPS_LAST", str(tmp_path / "ops_last.json"))
    monkeypatch.setenv("BOTSTRIKE_OPS_STATE", str(tmp_path / "ops_state.json"))
    fresh = bridge.BridgeState()
    monkeypatch.setattr(bridge, "state", fresh)
    monkeypatch.setattr(bridge, "_EXPOSE_TOKEN", True)
    monkeypatch.setattr(act, "_LOG", act.ActivityLog(str(tmp_path / "activity.json")))
    bridge._FUNDING_CACHE.clear()
    return fresh


class _MD:
    def get_snapshot(self, symbol):
        return MarketSnapshot(symbol=symbol, timestamp=time.time(), price=100.5, mark_price=100.6, index_price=100.4,
                              funding_rate=0.0001, volume_24h=0, open_interest=123.0)

    def get_24h_stats(self, symbol):
        return {"change_24h_pct": 0.01, "high_24h": 102.0, "low_24h": 98.0, "volume_24h_base": 1.0,
                "volume_24h_usd": 100.0, "window_min": 1440}

    def get_data_age(self, symbol):
        return 0.1


def _engine(trades):
    s = Settings()
    sim = PaperTradingSimulator(s)
    rm = RiskManager(s)
    rm.restore_history(equity=1000.0, peak=1000.0, daily_pnl=0.0, weekly_pnl=0.0)
    det = SimpleNamespace(status=lambda sym: {"regime": "RANGING", "confirmed_since": 1.0, "candidate": "", "timeframe_min": 15})
    return SimpleNamespace(settings=s, paper_sim=sim, paper=True, risk_manager=rm, trend_engine=None, market_data=_MD(),
                           regime_detector=det, portfolio_manager=SimpleNamespace(killed={}), edge_stats={},
                           metrics=SimpleNamespace(get_metrics=lambda: {}), notifier=None, _unrealized_total=lambda: 0.0,
                           trade_repo=SimpleNamespace(get_trades=lambda **kw: [t for t in trades
                                                                                if (kw.get("symbol") in (None, t.symbol))]))


def test_portfolio_activity_ops_csv_endpoints(st, tmp_path, monkeypatch):
    st.engine, st.running = _engine(_trades()), True
    client = TestClient(bridge.app)
    p = client.get("/api/portfolio").json()
    assert p["engine"] is True and p["realized_pnl"] == 6.0 and p["initial_capital"] == 1000.0
    assert {s["strategy"] for s in p["by_strategy"]} == {"MEAN_REVERSION", "TREND_DAILY"} and len(p["win_days"]) == 18
    # activity: bridge start row + fills recorded through the fill hook
    bridge._activity_fill(SimpleNamespace(trade_type="ENTRY", pnl=0.0, fee=0.0, timestamp=T0),
                          {"symbol": "ETH-USD", "side": "BUY", "quantity": 0.05, "price": 2000.0, "strategy": "MEAN_REVERSION"})
    ev = client.get("/api/activity?limit=10").json()["events"]
    assert ev[0]["kind"] == "fill" and ev[0]["title"] == "Opened LONG ETH-USD"
    assert client.get("/api/activity?kind=run").json()["events"] == []
    # ops: not available, then available
    assert client.get("/api/ops").json()["available"] is False
    (tmp_path / "ops_last.json").write_text(json.dumps({"ts": "2026-09-02T19:25:40+00:00", "alerts": [{"key": "x", "text": "y"}],
                                                       "sent": [], "summary_sent": False, "facts": {"bridge": "ok"},
                                                       "journal_15": {"errors": 0}}), encoding="utf-8")
    (tmp_path / "ops_state.json").write_text(json.dumps({"last_summary_date": "2026-09-02", "last_alerts": {}}), encoding="utf-8")
    o = client.get("/api/ops").json()
    assert o["available"] is True and o["alerts"][0]["key"] == "x" and o["state"]["last_summary_date"] == "2026-09-02"
    # csv export
    r = client.get("/api/trades/export.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    lines = r.text.strip().splitlines()
    assert lines[0].startswith("trade_id,timestamp,") and len(lines) == 1 + 6
    assert client.get("/api/trades/export.csv?symbol=BTC-USD").text.strip().count("\n") == 2
    # symbol_config on /api/market
    m = client.get("/api/market/ETH-USD").json()
    assert m["symbol_config"]["leverage"] >= 1 and m["symbol_config"]["taker_fee"] == 0.0004
    # ETH-USD's config row still names MEAN_REVERSION and DIVERGENCE, both retired with evidence.
    # A retired strategy is never advertised, so this list is empty rather than misleading -- the
    # panel says "not in the trend universe" instead of naming something that cannot trade.
    from core.types import RETIRED_STRATEGIES
    assert not (set(m["symbol_config"]["strategies"]) & set(RETIRED_STRATEGIES))


def test_funding_history_endpoint_parses_and_caches(st, monkeypatch):
    st.engine, st.running = _engine([]), True
    calls = []

    async def fake(bsym, limit):
        calls.append((bsym, limit))
        return [{"symbol": bsym, "fundingTime": 1788336000000, "fundingRate": "0.00010000", "markPrice": "77120.5"},
                {"symbol": bsym, "fundingTime": 1788307200000, "fundingRate": "-0.00005000", "markPrice": "77000.0"}]

    async def no_strike(symbol, days):
        raise RuntimeError("stats offline")

    monkeypatch.setattr(bridge, "_fetch_strike_funding_history", no_strike)
    monkeypatch.setattr(bridge, "_fetch_funding_history", fake)
    client = TestClient(bridge.app)
    f = client.get("/api/market/BTC-USD/funding_history?limit=50").json()
    assert f["source"] == "binance_fapi"                            # only as a fallback
    assert f["binance_symbol"] == "BTCUSDT" and [p["rate"] for p in f["points"]] == [-0.00005, 0.0001]
    assert f["points"][0]["ts"] == 1788307200.0 and f["cumulative"][-1]["value"] == pytest.approx(0.00005)
    client.get("/api/market/BTC-USD/funding_history?limit=50")
    assert len(calls) == 1                                          # cached

    async def boom(bsym, limit):
        raise RuntimeError("offline")

    monkeypatch.setattr(bridge, "_fetch_funding_history", boom)
    bridge._FUNDING_CACHE.clear()
    e = client.get("/api/market/ETH-USD/funding_history").json()
    assert e["points"] == [] and e["error"] == "RuntimeError"


def test_funding_history_prefers_the_venue_the_book_executes_on(st, monkeypatch):
    """Binance was asked for XAU-USD and answered with its OWN gold perp, a different market with
    different funding; SP500-USD and WTI-USD returned nothing (audit 2026-09-03)."""
    st.engine, st.running = _engine([]), True
    seen = []

    async def strike(symbol, days):
        seen.append((symbol, days))
        return [{"ts": 1788307200000, "funding_rate": 1.25e-05},
                {"ts": 1788310800000, "funding_rate": 0.0}]          # hourly, and a real zero

    async def binance(bsym, limit):
        raise AssertionError("Binance must not be asked when the venue answers")

    monkeypatch.setattr(bridge, "_fetch_strike_funding_history", strike)
    monkeypatch.setattr(bridge, "_fetch_funding_history", binance)
    bridge._FUNDING_CACHE.clear()
    client = TestClient(bridge.app)
    f = client.get("/api/market/XAU-USD/funding_history?limit=48").json()
    assert f["source"] == "strike" and seen == [("XAU-USD", 2)]
    assert [p["rate"] for p in f["points"]] == [1.25e-05, 0.0]
    assert f["cumulative"][-1]["value"] == pytest.approx(1.25e-05)


def test_the_spa_entry_document_is_never_cached_but_hashed_assets_are():
    """index.html carried only an ETag, so Chrome served a heuristically cached copy and kept loading
    the previous bundle after a deploy: the operator saw an outdated UI (2026-09-03)."""
    client = TestClient(bridge.app)
    r = client.get("/")
    if r.status_code != 200:
        pytest.skip("no web build in this checkout")
    assert "no-cache" in r.headers.get("cache-control", "")
    import re as _re
    m = _re.search(r"assets/(index-[A-Za-z0-9_-]+\.js)", r.text)
    assert m, "the entry document must reference a hashed bundle"
    a = client.get(f"/assets/{m.group(1)}")
    assert a.status_code == 200 and "immutable" in a.headers.get("cache-control", "")


def test_every_open_market_reaches_the_socket_and_closures_are_cleared(monkeypatch):
    """Read off the live socket on 2026-09-03: only 4 of 6 markets were ever broadcast, because the
    feed symbols were skipped on the assumption that the intraday tick loop streams them — it does
    not when no intraday strategy runs. The Portfolio page reads the socket, so it showed 4 of 6."""
    sent = []

    class _Ch:
        async def broadcast(self, channel, msg):
            sent.append((msg["symbol"], len(msg["data"])))

    rows = [{"symbol": s} for s in ("BTC-USD", "SOL-USD", "SP500-USD", "WTI-USD")]
    monkeypatch.setattr(bridge, "_paper_position_rows", lambda eng: rows)
    monkeypatch.setattr(bridge.state, "engine", type("E", (), {"trend_engine": object()})())
    monkeypatch.setattr(bridge.state, "channels", _Ch())
    bridge._trend_symbols_sent.clear()

    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(bridge._broadcast_trend_positions())
    assert sorted(sent) == [("BTC-USD", 1), ("SOL-USD", 1), ("SP500-USD", 1), ("WTI-USD", 1)]
    assert bridge._trend_symbols_sent == {"BTC-USD", "SOL-USD", "SP500-USD", "WTI-USD"}

    # WTI closes: the socket must say so, or it stays on screen until a reload
    sent.clear()
    rows.pop()
    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(bridge._broadcast_trend_positions())
    assert ("WTI-USD", 0) in sent
    assert "WTI-USD" not in bridge._trend_symbols_sent


def test_markets_endpoint_lists_the_whole_venue_not_only_the_feed(st, monkeypatch):
    """The picker stopped at four crypto while the trend book held gold, silver, the S&P and oil.
    Every market the bot could operate is listed, tagged with what it offers (2026-09-04)."""
    from types import SimpleNamespace as NS

    eng = NS(_venue_funding={"BTC-USD": 1.6e-05, "XAU-USD": 0.0, "ZEC-USD": -1.9e-05, "DOGE-USD": 2e-05},
             settings=NS(symbol_names=["BTC-USD", "ETH-USD"],
                         trading=NS(funding_interval_hours=1, trend_pool="BTCUSDT,XAU-USD,ZEC-USD",
                                    exchange_venue="strike")),
             _funding_positions=lambda: [{"symbol": "XAU-USD"}])
    st.engine, st.running = eng, True
    r = TestClient(bridge.app).get("/api/markets").json()

    by = {m["symbol"]: m for m in r["markets"]}
    # the venue layer is stubbed out by conftest, so this lists exactly what the engine knows
    assert set(by) == {"BTC-USD", "ETH-USD", "XAU-USD", "ZEC-USD", "DOGE-USD"}
    assert by["BTC-USD"]["feed"] is True and by["BTC-USD"]["pool"] is True
    assert by["XAU-USD"]["held"] is True and by["XAU-USD"]["feed"] is False   # daily book only
    assert by["DOGE-USD"]["feed"] is False and by["DOGE-USD"]["pool"] is False  # listed, not traded
    assert by["ZEC-USD"]["funding_rate"] == pytest.approx(-1.9e-05)
    assert by["ETH-USD"]["funding_rate"] is None            # the venue did not quote it
    assert r["interval_hours"] == 1 and r["venue"] == "strike"
