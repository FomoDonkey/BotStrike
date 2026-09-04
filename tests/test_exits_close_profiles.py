"""Exit ladder, manual close, funding attribution and risk profiles (Edgar 2026-09-03).

These answer three operator questions the bot could not answer before: when does a position close,
how do I close it myself, and what does funding cost me per position.
"""
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

import server.bridge as bridge
from config import risk_profiles as rp
from config.settings import Settings
from core.types import Side, Signal, StrategyType
from execution.paper_simulator import PaperTradingSimulator
from risk.risk_manager import RiskManager
from strategies.trend_daily_model import TrendParams, exit_ladder


def _uptrend(n=400, start=100.0, step=0.25):
    """A clean uptrend: every Donchian lookback is long, so the ladder has one level per lookback."""
    idx = pd.date_range("2025-01-01", periods=n, freq="D")
    return pd.Series(start + step * np.arange(n, dtype=float), index=idx)


def test_exit_ladder_has_one_level_per_active_lookback_and_shrinks_the_weight():
    p = TrendParams()
    L = exit_ladder(_uptrend(), p)
    assert L["active"] == L["total"] == len(p.lookbacks)
    assert len(L["levels"]) == len(p.lookbacks)
    # nearest stop first, weight decreasing to zero, every stop below the price
    stops = [lv["stop"] for lv in L["levels"]]
    assert stops == sorted(stops, reverse=True)
    assert all(s < L["price"] for s in stops)
    assert L["levels"][-1]["weight_after"] == 0.0
    assert L["levels"][0]["weight_after"] == pytest.approx(1 - 1 / len(p.lookbacks))
    assert all(lv["distance_pct"] < 0 for lv in L["levels"])
    assert L["first_exit"] == stops[0] and L["full_exit"] == stops[-1]
    assert L["worst_case_pct"] == pytest.approx(stops[-1] / L["price"] - 1.0, abs=1e-6)


def test_exit_ladder_is_empty_when_nothing_is_long():
    idx = pd.date_range("2025-01-01", periods=300, freq="D")
    down = pd.Series(200 - 0.3 * np.arange(300, dtype=float), index=idx)   # pure downtrend
    L = exit_ladder(down, TrendParams())
    assert L["active"] == 0 and L["levels"] == [] and L["full_exit"] is None
    assert exit_ladder(pd.Series(dtype=float), TrendParams())["active"] == 0


def test_paper_simulator_closes_one_symbol_on_demand():
    s = Settings()
    sim = PaperTradingSimulator(s)
    sim._router.route = lambda **kw: SimpleNamespace(order_type="MARKET", limit_price=0.0,
                                                     fill_probability=1.0, expected_cost_bps=4.0, reason="t")
    for sym, px in (("ETH-USD", 2000.0), ("SOL-USD", 100.0)):
        sig = Signal(strategy=StrategyType.MEAN_REVERSION, symbol=sym, side=Side.BUY, strength=0.8,
                     entry_price=px, stop_loss=px * 0.99, take_profit=px * 1.02, size_usd=200.0,
                     metadata={"trigger": "t"})
        assert sim.execute_signals([sig], [], s.get_symbol_config(sym))
    sim.on_price_update("ETH-USD", 2010.0)
    assert sim.position_count == 2
    trades = sim.close_symbol("ETH-USD", reason="manual")
    assert len(trades) == 1 and trades[0].symbol == "ETH-USD" and trades[0].side == Side.SELL
    assert trades[0].order_id.startswith("paper_manual_")
    assert sim.position_count == 1                              # the other symbol is untouched
    assert sim.close_symbol("ETH-USD") == []                    # idempotent


def test_risk_profiles_scale_target_vol_and_the_ladder_together():
    s = Settings()
    assert rp.profile_of(s.trading) == "balanced"                # the shipped default
    changed = rp.apply_profile(s.trading, "aggressive")
    # Aggressive sits at 0.80 target vol since 2026-09-04 — Edgar's explicit choice, made against the
    # measured menu in dollars, and deliberately OUTSIDE the 0.10-0.30 range the research validated.
    # The loss ladder comes from the measured tail at this size (worst day -8.28 %, worst week
    # -11.46 %, worst drawdown -27.51 %), not from a ratio copied off a calmer profile: a breaker
    # tuned for a 3 % day would halt the bot on an ordinary one here.
    assert s.trading.trend_target_vol == 0.80 and s.trading.max_drawdown_pct == 0.36
    assert s.trading.max_daily_loss_pct == 0.11 and s.trading.max_weekly_loss_pct == 0.14
    # aggressive also raises the vol-scalar ceiling to 3x (Edgar, 2026-09-04). Measured on the
    # validated 14-market panel: +0.5 pts of CAGR for no extra drawdown, because the cap only bound
    # on the quietest 5.6 % of asset-days to begin with (scripts/leverage_cap_study.py).
    assert s.trading.trend_leverage_cap == 3.0
    # balanced already runs the 3x ceiling, so switching to aggressive changes everything except it
    assert set(changed) >= {"trend_target_vol", "max_drawdown_pct", "max_daily_loss_pct",
                            "max_weekly_loss_pct"}
    assert set(changed) <= {"trend_target_vol", "max_drawdown_pct", "max_daily_loss_pct",
                            "max_weekly_loss_pct", "trend_leverage_cap"}
    assert rp.profile_of(s.trading) == "aggressive"
    rp.apply_profile(s.trading, "conservative")
    assert s.trading.trend_target_vol == 0.10 and s.trading.max_drawdown_pct == 0.06
    assert s.trading.trend_leverage_cap == 2.0        # and the ceiling comes back down with it
    assert rp.apply_profile(s.trading, "nonsense") == {}         # unknown profile changes nothing
    s.trading.trend_target_vol = 0.17
    assert rp.profile_of(s.trading) == "custom"


def test_profile_description_is_honest_about_the_trade_off():
    d = rp.describe("aggressive", equity=1000.0)
    c = rp.describe("conservative", equity=1000.0)
    assert d["expected_cagr"] > c["expected_cagr"] and d["expected_max_dd"] > c["expected_max_dd"]
    # the edge barely changes: 1.84 at 0.80 target vol against 1.93 conservative. The extra return
    # is a bigger position, not a better strategy — which is the whole point of the profile page.
    assert abs(d["sharpe"] - c["sharpe"]) < 0.12
    # Aggressive was PUT THROUGH the book's own eleven gates at its own settings and passed 11/11
    # (scripts/validate_aggressive.py, 2026-09-04) — the range was not widened to make room for it.
    assert d["beyond_validated_range"] is False and c["beyond_validated_range"] is False
    assert d["gates_passed"] == 11 and d["gates_total"] == 11 and d["dsr"] >= 0.95
    assert d["longest_underwater_days"] == 620      # validated does not mean comfortable
    # Re-measured 2026-09-04 with funding taken from Strike instead of guessed per asset class: the
    # old figures (152 / 113 on 1,000) understated the book, which the Risk page was quoting as fact.
    # 415 / 275 on 1,000: aggressive at 0.80 target vol (aggressive_080_study, 2026-09-04)
    assert d["expected_year_usd"] == pytest.approx(415.0) and d["expected_worst_drawdown_usd"] == pytest.approx(275.0)
    assert c["expected_year_usd"] == pytest.approx(56.0)
    assert rp.describe("custom")["validated"] is False
    assert [p["profile"] for p in rp.catalog()] == ["conservative", "balanced", "aggressive"]


@pytest.fixture
def st(monkeypatch, tmp_path):
    monkeypatch.delenv("BOTSTRIKE_AUTOSTART", raising=False)
    monkeypatch.setenv("BOTSTRIKE_CONFIG_OVERRIDES", str(tmp_path / "o.json"))
    fresh = bridge.BridgeState()
    monkeypatch.setattr(bridge, "state", fresh)
    monkeypatch.setattr(bridge, "_EXPOSE_TOKEN", True)
    return fresh


def _engine(sim, s):
    rm = RiskManager(s)
    rm.restore_history(equity=1000.0, peak=1000.0, daily_pnl=0.0, weekly_pnl=0.0)
    return SimpleNamespace(settings=s, paper_sim=sim, paper=True, risk_manager=rm, trend_engine=None,
                           market_data=SimpleNamespace(get_snapshot=lambda x: None, get_24h_stats=lambda x: {},
                                                       get_data_age=lambda x: 0.1),
                           regime_detector=SimpleNamespace(status=lambda x: {}),
                           portfolio_manager=SimpleNamespace(killed={}), edge_stats={},
                           metrics=SimpleNamespace(get_metrics=lambda: {}), trade_repo=None, notifier=None,
                           funding=None, _unrealized_total=lambda: 0.0)


def test_close_endpoint_closes_a_paper_position(st):
    s = Settings()
    sim = PaperTradingSimulator(s)
    sim._router.route = lambda **kw: SimpleNamespace(order_type="MARKET", limit_price=0.0,
                                                     fill_probability=1.0, expected_cost_bps=4.0, reason="t")
    sig = Signal(strategy=StrategyType.MEAN_REVERSION, symbol="ETH-USD", side=Side.BUY, strength=0.8,
                 entry_price=2000.0, stop_loss=1980.0, take_profit=2040.0, size_usd=200.0, metadata={})
    sim.execute_signals([sig], [], s.get_symbol_config("ETH-USD"))
    st.engine, st.running = _engine(sim, s), True
    client = TestClient(bridge.app)
    assert client.post("/api/positions/close", json={}).status_code == 400
    assert client.post("/api/positions/close", json={"symbol": "BTC-USD"}).status_code == 404
    r = client.post("/api/positions/close", json={"symbol": "ETH-USD"})
    assert r.status_code == 200 and r.json()["closed"] is True and r.json()["source"] == "paper"
    assert sim.position_count == 0


def test_risk_profile_endpoints(st):
    s = Settings()
    st.engine, st.running = _engine(PaperTradingSimulator(s), s), True
    client = TestClient(bridge.app)
    body = client.get("/api/risk/profiles").json()
    assert body["current"] == "balanced" and len(body["profiles"]) == 3
    # 0.80 after aggressive passed the full suite there; anything above it is still unstudied
    assert body["validated_target_vol_range"] == [0.10, 0.80]
    agg = next(p for p in body["profiles"] if p["profile"] == "aggressive")
    assert agg["expected_cagr"] > 0.40 and agg["expected_max_dd"] > 0.27
    assert agg["beyond_validated_range"] is False and agg["gates_passed"] == 11
    assert client.post("/api/risk/profile", json={"profile": "nope"}).status_code == 400
    r = client.post("/api/risk/profile", json={"profile": "aggressive"})
    assert r.status_code == 200 and r.json()["profile"] == "aggressive"
    assert "trading.trend_target_vol" in r.json()["applied"]
    assert client.get("/api/risk/profiles").json()["current"] == "aggressive"
