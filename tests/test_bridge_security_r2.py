"""Security fixes from audit round 2 (security_supply-01/03/05), verified 2026-08-31.

Each test pins a hole that was reproduced against a running bridge before being fixed:
  - sec-05: `--dev` on a non-loopback bind was a FULL auth bypass (token handed out via
    /api/bot/status, /docs served, mutations accepted with no credential at all).
  - sec-01: the token travelled in the query string and uvicorn wrote it verbatim to the
    access log (journald on the server).
  - sec-03: a valid token was the ONLY thing standing between an API call and real money.
"""
import importlib
import logging

import pytest
from fastapi.testclient import TestClient

import server.bridge as bridge


@pytest.fixture
def st(monkeypatch):
    fresh = bridge.BridgeState()
    monkeypatch.setattr(bridge, "state", fresh)
    monkeypatch.setattr(bridge, "_AUTH_TOKEN", "t" * 32)
    return fresh


# ── security_supply-05: _EXPOSE_TOKEN must not depend on main() having run ──────────

def test_expose_token_derived_from_env_at_import(monkeypatch):
    """The --dev reload worker imports this module without running main()."""
    monkeypatch.setenv("BOTSTRIKE_HOST", "0.0.0.0")
    reloaded = importlib.reload(bridge)
    try:
        assert reloaded._EXPOSE_TOKEN is False, "non-loopback bind must never expose the token"
    finally:
        monkeypatch.setenv("BOTSTRIKE_HOST", "127.0.0.1")
        importlib.reload(bridge)


def test_expose_token_true_on_loopback(monkeypatch):
    monkeypatch.setenv("BOTSTRIKE_HOST", "127.0.0.1")
    reloaded = importlib.reload(bridge)
    assert reloaded._EXPOSE_TOKEN is True  # desktop-local convenience preserved


def test_mutation_without_token_rejected_on_remote_bind(st, monkeypatch):
    # TestClient without the context manager: the lifespan (broadcast loops, autostart) must not run.
    monkeypatch.setattr(bridge, "_EXPOSE_TOKEN", False)
    st.running = True
    st.mode = "paper"
    assert TestClient(bridge.app).post("/api/bot/stop").status_code == 401


def test_status_hides_token_on_remote_bind(st, monkeypatch):
    monkeypatch.setattr(bridge, "_EXPOSE_TOKEN", False)
    body = TestClient(bridge.app).get("/api/bot/status").json()
    assert body["auth_token"] is None
    assert body["auth_token_exposed"] is False


# ── security_supply-01: token must never reach the logs ────────────────────────────

def test_access_log_filter_redacts_token_in_args():
    f = bridge._RedactTokenFilter()
    rec = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1,
                            '%s - "%s %s HTTP/%s" %d', None, None)
    rec.args = ("127.0.0.1:5555", "GET", "/api/bot/status?token=SUPERSECRET123", "1.1", 200)
    f.filter(rec)
    assert "SUPERSECRET123" not in str(rec.args)
    assert "token=***" in str(rec.args)


def test_access_log_filter_redacts_token_in_msg():
    f = bridge._RedactTokenFilter()
    rec = logging.LogRecord("uvicorn.error", logging.INFO, __file__, 1,
                            "connect to /api/bot/start?mode=live&token=abc123XYZ", None, None)
    f.filter(rec)
    assert "abc123XYZ" not in rec.msg
    assert "token=***" in rec.msg


def test_filter_installed_on_uvicorn_loggers():
    for name in ("uvicorn.access", "uvicorn.error"):
        assert any(isinstance(x, bridge._RedactTokenFilter)
                   for x in logging.getLogger(name).filters), name


def test_header_token_is_accepted_for_mutations(st, monkeypatch):
    """The UI now sends X-BotStrike-Token instead of ?token= — it must still authenticate."""
    monkeypatch.setattr(bridge, "_EXPOSE_TOKEN", False)
    st.running = False
    r = TestClient(bridge.app).post("/api/bot/stop", headers={"X-BotStrike-Token": "t" * 32})
    assert r.status_code == 200
    assert r.json()["status"] == "not_running"


def test_legacy_query_token_still_accepted(st, monkeypatch):
    """Older desktop builds must not break."""
    monkeypatch.setattr(bridge, "_EXPOSE_TOKEN", False)
    st.running = False
    r = TestClient(bridge.app).post(f"/api/bot/stop?token={'t' * 32}")
    assert r.status_code == 200


# ── security_supply-03: live needs a deploy-level switch, not just a token ─────────

def test_live_refused_without_allow_live_even_with_valid_token(st, monkeypatch):
    monkeypatch.setattr(bridge, "_EXPOSE_TOKEN", False)
    monkeypatch.setattr(bridge, "_ALLOW_LIVE", False)
    r = TestClient(bridge.app).post("/api/bot/start?mode=live",
                                    headers={"X-BotStrike-Token": "t" * 32})
    assert r.status_code == 403
    assert "BOTSTRIKE_ALLOW_LIVE" in r.json()["detail"]


def test_live_still_needs_a_valid_token_when_allowed(st, monkeypatch):
    monkeypatch.setattr(bridge, "_ALLOW_LIVE", True)
    monkeypatch.setattr(bridge, "_EXPOSE_TOKEN", True)  # even on loopback
    r = TestClient(bridge.app).post("/api/bot/start?mode=live",
                                    headers={"X-BotStrike-Token": "wrong"})
    assert r.status_code == 401


def test_paper_unaffected_by_the_kill_switch(st, monkeypatch):
    """The switch must gate live only — paper is what the CT runs 24/7."""
    monkeypatch.setattr(bridge, "_ALLOW_LIVE", False)
    monkeypatch.setattr(bridge, "_EXPOSE_TOKEN", True)
    st.running = True
    st.mode = "paper"
    r = TestClient(bridge.app).post("/api/bot/start?mode=paper")
    assert r.status_code == 200
    assert r.json()["status"] == "already_running"
