"""Bridge v2.14: configuration API, strategies/risk/trend views, hash-route redirect,
catalog metadata. No engine, no network (TestClient without lifespan)."""
import os
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import server.bridge as bridge
from config import overrides as ov
from config.settings import Settings
from core.types import StrategyType


@pytest.fixture
def st(monkeypatch, tmp_path):
    monkeypatch.delenv("BOTSTRIKE_AUTOSTART", raising=False)
    monkeypatch.setenv("BOTSTRIKE_CONFIG_OVERRIDES", str(tmp_path / "overrides.json"))
    fresh = bridge.BridgeState()
    monkeypatch.setattr(bridge, "state", fresh)
    monkeypatch.setattr(bridge, "_EXPOSE_TOKEN", True)
    monkeypatch.setattr(bridge, "_AUTH_TOKEN", "t" * 32)
    return fresh


@pytest.fixture
def client():
    return TestClient(bridge.app)


def _fake_engine():
    s = Settings()
    pm = SimpleNamespace(killed={}, _current_weights={})
    return SimpleNamespace(settings=s, portfolio_manager=pm, edge_stats={}, trend_engine=None,
                           _last_edge_check=0.0, notifier=None)


def test_config_is_available_before_the_engine_starts(st, client):
    r = client.get("/api/config")
    assert r.status_code == 200
    body = r.json()
    assert body["engine_running"] is False
    assert body["trading"]["allocation_trend_daily"] == 1.0
    assert body["trading"]["max_weekly_loss_pct"] == 0.05
    assert body["symbols"][0]["strategies"] == "FIBONACCI_RETRACEMENT,DIVERGENCE"
    assert body["overrides"] == {} and body["restart_required"] is False
    sch = client.get("/api/config/schema").json()
    assert [g["id"] for g in sch["groups"]][0] == "capital"
    assert any(f["path"] == "trading.compounding_enabled" for g in sch["groups"] for f in g["fields"])


def test_put_config_validates_persists_and_applies_live(st, client, tmp_path):
    eng = _fake_engine()
    st.engine, st.running = eng, True
    r = client.put("/api/config", json={"trading": {"max_drawdown_pct": 0.9}})
    assert r.status_code == 400 and "<= 0.5" in r.json()["detail"]
    r = client.put("/api/config", json={"trading": {"max_drawdown_pct": 0.03, "max_daily_loss_pct": 0.05}})
    assert r.status_code == 400 and "ladder" in r.json()["detail"]
    r = client.put("/api/config", json={"trading": {"allocation_mean_reversion": 0.5, "microstructure_enabled": True},
                                        "symbols": {"ETH-USD": {"mr_zscore_entry": 2.5}}})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["restart_required"] is False
    assert set(body["applied"]) == {"trading.allocation_mean_reversion", "trading.microstructure_enabled",
                                    "symbols.ETH-USD.mr_zscore_entry"}
    # applied LIVE to the engine's settings object and mirrored in the portfolio weights
    assert eng.settings.trading.allocation_mean_reversion == 0.5
    assert eng.settings.get_symbol_config("ETH-USD").mr_zscore_entry == 2.5
    assert eng.portfolio_manager._current_weights[StrategyType.MEAN_REVERSION] == 0.5
    # persisted for the next start
    assert ov.load_overrides()["trading"]["allocation_mean_reversion"] == 0.5
    assert body["config"]["overrides"]["symbols"]["ETH-USD"]["mr_zscore_entry"] == 2.5
    # restart-only field is flagged
    r = client.put("/api/config", json={"trading": {"initial_capital": 300}})
    assert r.status_code == 400 and "max_total_exposure" in r.json()["detail"]   # coherence
    r = client.put("/api/config", json={"trading": {"vol_target_annual": 0.25}})
    assert r.status_code == 200 and r.json()["restart_required"] is True
    assert client.get("/api/config").json()["restart_required"] is True
    r = client.put("/api/config", json={})
    assert r.status_code == 400


def test_put_config_requires_token_when_remote(st, client, monkeypatch):
    monkeypatch.setattr(bridge, "_EXPOSE_TOKEN", False)
    r = client.put("/api/config", json={"trading": {"max_leverage": 3}})
    assert r.status_code == 401
    r = client.put("/api/config", json={"trading": {"max_leverage": 3}},
                   headers={"X-BotStrike-Token": "t" * 32})
    assert r.status_code == 200
    r = client.post("/api/config/reset")
    assert r.status_code == 401


def test_reset_clears_overrides(st, client):
    client.put("/api/config", json={"trading": {"max_leverage": 3}})
    assert ov.load_overrides()["trading"]["max_leverage"] == 3
    r = client.post("/api/config/reset")
    assert r.status_code == 200 and r.json()["restart_required"] is True
    assert ov.load_overrides() == {}
    assert client.get("/api/config").json()["trading"]["max_leverage"] == 5


def test_strategies_view_is_generated_from_config(st, client):
    r = client.get("/api/strategies").json()
    types = [s["type"] for s in r["strategies"]]
    assert types == ["TREND_DAILY", "DIVERGENCE", "MEAN_REVERSION", "FIBONACCI_RETRACEMENT"]
    trend = r["strategies"][0]
    assert trend["enabled"] is True and trend["active"] is False        # engine not running
    assert "Donchian" in trend["description"] and trend["params"]["lookbacks"] == "5,10,20,30,60,90"
    mr = next(x for x in r["strategies"] if x["type"] == "MEAN_REVERSION")
    assert mr["enabled"] is False and mr["symbols"] == ["ETH-USD", "SOL-USD", "ADA-USD"]
    assert "RANGING" in mr["description"]
    eng = _fake_engine()
    eng.portfolio_manager.killed[StrategyType.MEAN_REVERSION] = "t-stat -3"
    eng.settings.trading.allocation_mean_reversion = 0.5
    eng.edge_stats = {"strategies": {"MEAN_REVERSION": {"n": 120, "verdict": "kill"}}}
    st.engine, st.running = eng, True
    mr = next(x for x in client.get("/api/strategies").json()["strategies"] if x["type"] == "MEAN_REVERSION")
    assert mr["killed"] is True and mr["active"] is False and mr["kill_reason"] == "t-stat -3"
    assert mr["edge"]["verdict"] == "kill"


def test_risk_and_trend_without_engine(st, client):
    r = client.get("/api/risk").json()
    assert r["engine"] is False and r["max_weekly_loss_pct"] == 0.05 and r["compounding_enabled"] is True
    t = client.get("/api/trend").json()
    assert t["engine"] is False and t["enabled"] is True and t["positions"] == []


def test_hash_route_redirect(st, client):
    if not bridge._WEBUI_DIR.is_dir():
        pytest.skip("web UI not built in this checkout")
    r = client.get("/performance", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/#/performance"
    assert client.get("/api/does-not-exist").status_code == 404
    assert client.get("/assets/nothing.js", follow_redirects=False).status_code == 404


def test_catalog_reads_parquet_metadata(tmp_path):
    pd = pytest.importorskip("pandas")
    pytest.importorskip("pyarrow")
    sym_dir = tmp_path / "BTC-USD"
    sym_dir.mkdir()
    ts = pd.date_range("2026-06-01", periods=5, freq="D")
    pd.DataFrame({"timestamp": ts, "close": [1.0] * 5}).to_parquet(sym_dir / "1m.parquet")
    rows = bridge._scan_catalog(str(tmp_path))
    assert rows[0]["symbol"] == "BTC-USD" and rows[0]["records"] == 5
    assert rows[0]["date_range"] == "2026-06-01 → 2026-06-05"


def test_health_reports_new_flags(st, client):
    h = client.get("/api/health").json()
    assert h["version"] == "2.16.0"
    assert "telegram_failures" in h and h["trend_daily_enabled"] is False
