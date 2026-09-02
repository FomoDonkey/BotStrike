"""DIVERGENCE strategy (candidate → verifier → trigger → signal, time stop) and the
terminal endpoints (positions details, orders, account, market). 2026-09-02."""
import asyncio
import time
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

import server.bridge as bridge
from config.settings import Settings
from core.bars import aggregate_1m
from core.types import MarketRegime, MarketSnapshot, Side, Signal, StrategyType
from execution.paper_simulator import PaperTradingSimulator
from portfolio.portfolio_manager import PortfolioManager, eligible_strategies, strategy_allocation
from risk.risk_manager import RiskManager
from strategies.divergence import DivergenceStrategy

T0 = 1_699_999_200.0   # multiple of 3600 → complete hourly buckets


def _hour_bars(closes, highs=None, lows=None, volume=100.0, start=T0):
    """Hourly bars as the strategy's seeded history (timestamp = close time)."""
    n = len(closes)
    closes = np.asarray(closes, dtype=float)
    highs = np.asarray(highs if highs is not None else closes * 1.002, dtype=float)
    lows = np.asarray(lows if lows is not None else closes * 0.998, dtype=float)
    ts = start + 3600.0 * np.arange(1, n + 1)
    return pd.DataFrame({"timestamp": ts, "open": closes, "high": highs, "low": lows, "close": closes,
                         "volume": volume})


def _minute_frame(last_close_ts: float, price: float) -> pd.DataFrame:
    """A 1-minute frame whose last complete hour ends at `last_close_ts` (flat prices)."""
    ts = last_close_ts - 60.0 * np.arange(119, -1, -1)
    return pd.DataFrame({"timestamp": ts, "open": price, "high": price, "low": price, "close": price, "volume": 1.0})


def _bullish_divergence_history():
    """Regular bullish divergence: pivot low L1 (RSI oversold) then a LOWER low L2 with a
    HIGHER RSI, then a bar that closes above L2's high (the trigger)."""
    rng = np.random.default_rng(3)
    base = 100 + np.cumsum(rng.normal(0, 0.05, 300))          # gentle drift, RSI ~50
    seq = list(base)

    def seg(ratio, n):                                        # n NEW points (start point excluded → strict pivots)
        seq.extend(np.linspace(seq[-1], seq[-1] * ratio, n + 1)[1:])

    seg(0.90, 12)                                             # sell-off to L1: RSI collapses well below 35
    seg(1.03, 8)                                              # bounce
    seg(0.975, 10)                                            # second leg: LOWER low with shallower momentum
    seg(0.995, 6)                                             # slow grind to the lower low L2 (RSI higher than L1)
    l2 = seq[-1]
    seq += [l2 * 1.004, l2 * 1.006, l2 * 1.008]               # k=3 bars up → L2 confirmed
    return np.array(seq), l2


def test_aggregate_1m_complete_buckets():
    df = _minute_frame(T0 + 3600.0 * 2, 100.0)
    out = aggregate_1m(df, 60)
    assert len(out) == 2 and out["timestamp"].iloc[-1] == pytest.approx(T0 + 7200.0)


def test_divergence_candidate_then_trigger_then_signal():
    s = Settings()
    s.trading.div_timeframe_min = 60
    s.trading.div_require_macd = False
    strat = DivergenceStrategy(s.trading)
    closes, l2 = _bullish_divergence_history()
    hist = _hour_bars(closes)
    strat._history["ETH-USD"] = hist
    last_ts = float(hist["timestamp"].iloc[-1])
    cfg = s.get_symbol_config("ETH-USD")
    snap = MarketSnapshot(symbol="ETH-USD", timestamp=last_ts, price=float(closes[-1]), mark_price=0, index_price=0,
                          funding_rate=0, volume_24h=0, open_interest=0)
    # 1) the confirmation bar of L2 creates the candidate — never a signal on the same bar
    sigs = strat.generate_signals("ETH-USD", _minute_frame(last_ts, float(closes[-1])), snap, MarketRegime.RANGING,
                                  cfg, 1000.0, None, kelly_risk_pct=0.01)
    assert sigs == []
    cand = strat.candidate_view("ETH-USD")
    assert cand is not None and cand["side"] == "BUY" and cand["kind"] == "regular"
    assert cand["p2"]["price"] < cand["p1"]["price"] and cand["p2"]["rsi"] > cand["p1"]["rsi"]
    assert cand["trigger_level"] == pytest.approx(l2 * 1.002, rel=1e-6)   # = HIGH of the L2 bar
    # 2) next closed bar breaks above the trigger level → precise entry
    px = l2 * 1.010
    strat._history["ETH-USD"] = pd.concat([hist, _hour_bars([px], start=last_ts)], ignore_index=True)
    last_ts += 3600.0
    snap = MarketSnapshot(symbol="ETH-USD", timestamp=last_ts, price=px, mark_price=0, index_price=0,
                          funding_rate=0, volume_24h=0, open_interest=0)
    sigs = strat.generate_signals("ETH-USD", _minute_frame(last_ts, px), snap, MarketRegime.RANGING,
                                  cfg, 1000.0, None, kelly_risk_pct=0.01)
    assert len(sigs) == 1
    sig = sigs[0]
    assert sig.strategy == StrategyType.DIVERGENCE and sig.side == Side.BUY
    assert sig.stop_loss < sig.entry_price < sig.take_profit
    md = sig.metadata
    assert md["trigger"] == "divergence_regular_bull" and len(md["pivots"]) == 2
    assert md["pivots"][1]["price"] == pytest.approx(l2 * 0.998, rel=1e-6)   # pivot = bar LOW
    assert md["rsi_gap"] > 3.0 and md["trigger_level"] > 0 and md["rr"] == 2.0 and md["bars_to_trigger"] == 1
    assert sig.entry_price == pytest.approx(px)
    assert (sig.take_profit - sig.entry_price) == pytest.approx(2.0 * (sig.entry_price - sig.stop_loss), rel=1e-6)
    assert strat.candidate_view("ETH-USD") is None                         # consumed


def test_no_signal_without_divergence():
    s = Settings()
    s.trading.div_timeframe_min = 60
    strat = DivergenceStrategy(s.trading)
    rng = np.random.default_rng(7)
    closes = 100 + np.cumsum(rng.normal(0, 0.02, 400))                     # noise, RSI stays mid-range
    hist = _hour_bars(closes)
    strat._history["BTC-USD"] = hist
    last_ts = float(hist["timestamp"].iloc[-1])
    cfg = s.get_symbol_config("BTC-USD")
    snap = MarketSnapshot(symbol="BTC-USD", timestamp=last_ts, price=float(closes[-1]), mark_price=0, index_price=0,
                          funding_rate=0, volume_24h=0, open_interest=0)
    sigs = strat.generate_signals("BTC-USD", _minute_frame(last_ts, float(closes[-1])), snap, MarketRegime.RANGING,
                                  cfg, 1000.0, None)
    assert sigs == []


def test_time_stop_exit_signal():
    s = Settings()
    s.trading.div_timeframe_min = 60
    s.trading.div_max_hold = 5
    strat = DivergenceStrategy(s.trading)
    closes = 100 + np.zeros(300)
    hist = _hour_bars(closes)
    strat._history["SOL-USD"] = hist
    last_ts = float(hist["timestamp"].iloc[-1])
    from strategies.divergence import DivState
    strat._states["SOL-USD"] = DivState(entry_ts=last_ts - 6 * 3600, entry_bar_ts=last_ts - 6 * 3600)
    pos = SimpleNamespace(side=Side.BUY, notional=100.0, size=1.0, entry_price=100.0)
    snap = MarketSnapshot(symbol="SOL-USD", timestamp=last_ts, price=100.0, mark_price=0, index_price=0,
                          funding_rate=0, volume_24h=0, open_interest=0)
    sigs = strat.generate_signals("SOL-USD", _minute_frame(last_ts, 100.0), snap, MarketRegime.RANGING,
                                  s.get_symbol_config("SOL-USD"), 1000.0, pos)
    assert len(sigs) == 1 and sigs[0].metadata["action"] == "exit_divergence"
    assert sigs[0].metadata["exit_reason"] == "time_stop" and sigs[0].side == Side.SELL


def test_divergence_is_wired_but_disabled_by_default():
    s = Settings()
    assert strategy_allocation(s.trading, StrategyType.DIVERGENCE) == 0.0
    assert StrategyType.DIVERGENCE in eligible_strategies(s, "ETH-USD")
    pm = PortfolioManager(s, RiskManager(s))
    assert not pm.should_strategy_trade(StrategyType.DIVERGENCE, MarketRegime.RANGING, symbol="ETH-USD")
    s.trading.allocation_divergence = 0.3
    assert pm.should_strategy_trade(StrategyType.DIVERGENCE, MarketRegime.RANGING, symbol="ETH-USD")
    assert not pm.should_strategy_trade(StrategyType.DIVERGENCE, MarketRegime.BREAKOUT, symbol="ETH-USD")


# ── terminal data ─────────────────────────────────────────────────

def _open_paper_position(sim: PaperTradingSimulator, s: Settings, side=Side.BUY):
    sig = Signal(strategy=StrategyType.MEAN_REVERSION, symbol="ETH-USD", side=side, strength=0.8,
                 entry_price=2000.0, stop_loss=1980.0 if side == Side.BUY else 2020.0,
                 take_profit=2040.0 if side == Side.BUY else 1960.0, size_usd=200.0,
                 metadata={"trigger": "trend_pullback_bull", "regime": "RANGING", "atr": 5.0})
    sim._router.route = lambda **kw: SimpleNamespace(order_type="MARKET", limit_price=0.0, fill_probability=1.0,
                                                     expected_cost_bps=4.0, reason="test")
    trades = sim.execute_signals([sig], [], s.get_symbol_config("ETH-USD"))
    assert len(trades) == 1
    return trades[0]


def test_position_details_and_protective_orders():
    s = Settings()
    sim = PaperTradingSimulator(s)
    _open_paper_position(sim, s)
    sim.on_price_update("ETH-USD", 2010.0)
    rows = sim.get_position_details(leverage_of=lambda sym: 2)
    assert len(rows) == 1
    r = rows[0]
    assert r["symbol"] == "ETH-USD" and r["side"] == "BUY" and r["leverage"] == 2
    assert r["margin"] == pytest.approx(r["entry_price"] * r["size"] / 2)
    assert r["liquidation_price"] == pytest.approx(r["entry_price"] * (1 - 0.5 + 0.005))
    assert r["unrealized_pnl"] > 0 and r["roe_pct"] == pytest.approx(r["unrealized_pnl"] / r["margin"])
    assert r["stop_loss"] == 1980.0 and r["take_profit"] == 2040.0
    assert r["sl_distance_pct"] < 0 < r["tp_distance_pct"]
    assert r["mfe_bps"] > 0 and r["trigger"] == "trend_pullback_bull" and r["hold_sec"] >= 0
    orders = sim.get_protective_orders()
    assert {o["type"] for o in orders} == {"STOP", "TAKE_PROFIT"}
    assert all(o["side"] == "SELL" for o in orders)
    # a short position mirrors the liquidation estimate
    sim2 = PaperTradingSimulator(s)
    _open_paper_position(sim2, s, side=Side.SELL)
    r2 = sim2.get_position_details(leverage_of=lambda sym: 4)[0]
    assert r2["liquidation_price"] == pytest.approx(r2["entry_price"] * (1 + 0.25 - 0.005))


@pytest.fixture
def st(monkeypatch, tmp_path):
    monkeypatch.delenv("BOTSTRIKE_AUTOSTART", raising=False)
    monkeypatch.setenv("BOTSTRIKE_CONFIG_OVERRIDES", str(tmp_path / "o.json"))
    fresh = bridge.BridgeState()
    monkeypatch.setattr(bridge, "state", fresh)
    monkeypatch.setattr(bridge, "_EXPOSE_TOKEN", True)
    return fresh


class _MD:
    def __init__(self, df):
        self._df = df

    def get_snapshot(self, symbol):
        return MarketSnapshot(symbol=symbol, timestamp=time.time(), price=100.5, mark_price=100.6,
                              index_price=100.4, funding_rate=0.0001, volume_24h=0, open_interest=123.0)

    def get_24h_stats(self, symbol):
        return {"change_24h_pct": 0.012, "high_24h": 102.0, "low_24h": 98.0, "volume_24h_base": 10.0,
                "volume_24h_usd": 1000.0, "window_min": 1440}

    def get_data_age(self, symbol):
        return 0.2


def test_terminal_endpoints(st):
    s = Settings()
    sim = PaperTradingSimulator(s)
    _open_paper_position(sim, s)
    sim.on_price_update("ETH-USD", 2010.0)
    rm = RiskManager(s)
    rm.restore_history(equity=1000.0, peak=1000.0, daily_pnl=0.0, weekly_pnl=0.0)
    det = SimpleNamespace(status=lambda sym: {"regime": "RANGING", "confirmed_since": 1.0, "candidate": "",
                                              "timeframe_min": 15}, params=lambda: (15, 30))
    eng = SimpleNamespace(settings=s, paper_sim=sim, paper=True, risk_manager=rm, trend_engine=None,
                          market_data=_MD(None), regime_detector=det, portfolio_manager=SimpleNamespace(killed={}),
                          edge_stats={}, metrics=SimpleNamespace(get_metrics=lambda: {"total_pnl": 0.0, "total_trades": 0}),
                          trade_repo=SimpleNamespace(get_trades=lambda **kw: []), notifier=None,
                          _unrealized_total=lambda: sim.get_position_details()[0]["unrealized_pnl"])
    st.engine, st.running = eng, True
    client = TestClient(bridge.app)
    pos = client.get("/api/positions").json()["positions"]
    assert len(pos) == 1 and pos[0]["leverage"] == 2 and pos[0]["liquidation_price"] > 0
    orders = client.get("/api/orders").json()["orders"]
    assert {o["type"] for o in orders} == {"STOP", "TAKE_PROFIT"}
    acct = client.get("/api/account").json()
    assert acct["engine"] is True and acct["open_positions"] == 1
    assert acct["margin_used"] == pytest.approx(pos[0]["margin"])
    assert acct["available"] == pytest.approx(acct["equity"] - acct["margin_used"])
    assert acct["position_value"] == pytest.approx(pos[0]["notional"])
    mk = client.get("/api/market/ETH-USD").json()
    assert mk["mark_price"] == 100.6 and mk["high_24h"] == 102.0 and mk["regime"] == "RANGING"
    assert 0 < mk["funding_countdown_sec"] <= 8 * 3600
    strategies = client.get("/api/strategies").json()["strategies"]
    div = next(x for x in strategies if x["type"] == "DIVERGENCE")
    assert div["enabled"] is False and div["research"]["verdict"] == "NO-GO"
    assert "NO-GO" in div["description"] and div["params"]["timeframe_min"] == 240


def _born_candidate():
    s = Settings()
    s.trading.div_timeframe_min = 60
    s.trading.div_require_macd = False
    strat = DivergenceStrategy(s.trading)
    closes, l2 = _bullish_divergence_history()
    hist = _hour_bars(closes)
    strat._history["ETH-USD"] = hist
    last_ts = float(hist["timestamp"].iloc[-1])
    cfg = s.get_symbol_config("ETH-USD")

    def evaluate(px, ts):
        snap = MarketSnapshot(symbol="ETH-USD", timestamp=ts, price=px, mark_price=0, index_price=0,
                              funding_rate=0, volume_24h=0, open_interest=0)
        return strat.generate_signals("ETH-USD", _minute_frame(ts, px), snap, MarketRegime.RANGING, cfg, 1000.0, None)

    assert evaluate(float(closes[-1]), last_ts) == []
    assert strat.candidate_view("ETH-USD") is not None
    return strat, hist, last_ts, l2, evaluate


def test_no_entry_without_structure_break():
    strat, hist, last_ts, l2, evaluate = _born_candidate()
    px = l2 * 1.001                                       # above L2 but BELOW the trigger level (L2 bar high)
    strat._history["ETH-USD"] = pd.concat([hist, _hour_bars([px], start=last_ts)], ignore_index=True)
    assert evaluate(px, last_ts + 3600.0) == []
    assert strat.candidate_view("ETH-USD") is not None    # still waiting for the break


def test_candidate_expires_after_trigger_window():
    strat, hist, last_ts, l2, evaluate = _born_candidate()
    window = int(strat._p("trigger_window", 6))
    px = l2 * 1.001
    for i in range(1, window + 2):                        # window+1 non-breaking bars → expired
        strat._history["ETH-USD"] = pd.concat([hist, _hour_bars([px] * i, start=last_ts)], ignore_index=True)
        assert evaluate(px, last_ts + 3600.0 * i) == []
        if i <= window:
            assert strat.candidate_view("ETH-USD") is not None, i
    assert strat.candidate_view("ETH-USD") is None
    # a late break after expiry is NOT taken
    strat._history["ETH-USD"] = pd.concat([hist, _hour_bars([px] * (window + 1) + [l2 * 1.02], start=last_ts)],
                                          ignore_index=True)
    assert evaluate(l2 * 1.02, last_ts + 3600.0 * (window + 2)) == []


def test_market_endpoint_is_json_safe_before_first_tick(st):
    """Startup: no snapshot yet, data age = inf, 24h stats NaN → 200 with nulls, never a 500."""
    class _NoData:
        def get_snapshot(self, symbol):
            return None

        def get_24h_stats(self, symbol):
            return {"change_24h_pct": float("nan"), "high_24h": float("inf"), "low_24h": None,
                    "volume_24h_base": 0.0, "volume_24h_usd": 0.0, "window_min": 0}

        def get_data_age(self, symbol):
            return float("inf")

    s = Settings()
    det = SimpleNamespace(status=lambda sym: {"regime": "UNKNOWN", "confirmed_since": 0.0, "candidate": "",
                                              "timeframe_min": 15})
    eng = SimpleNamespace(settings=s, paper_sim=PaperTradingSimulator(s), paper=True, risk_manager=RiskManager(s),
                          trend_engine=None, market_data=_NoData(), regime_detector=det,
                          portfolio_manager=SimpleNamespace(killed={}), edge_stats={},
                          metrics=SimpleNamespace(get_metrics=lambda: {}), trade_repo=None, notifier=None,
                          _unrealized_total=lambda: 0.0)
    st.engine, st.running = eng, True
    client = TestClient(bridge.app)
    r = client.get("/api/market/BTC-USD")
    assert r.status_code == 200
    body = r.json()
    assert body["engine"] is True and body["price"] is None
    assert body["data_age_sec"] is None and body["change_24h_pct"] is None and body["high_24h"] is None
    assert client.get("/api/account").status_code == 200
    assert client.get("/api/positions").json() == {"positions": []}
