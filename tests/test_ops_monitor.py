"""Ops monitor decision logic (scripts/ops_monitor.py): alerts, daily summary, de-dup, recovery."""
from datetime import datetime, timezone

from scripts import ops_monitor as om


def _now(h, m):
    return datetime(2026, 9, 3, h, m, tzinfo=timezone.utc)


HEALTH = {"status": "ok", "degraded": False, "reasons": [], "engine_running": True, "engine_expected": True,
          "ws_connected": True, "last_tick_age_sec": 0.5, "telegram_failures": 0}
TREND = {"enabled": True, "killed": False, "last_run_utc": "2026-09-03T00:05:41Z", "last_run_status": "ok",
         "last_error": "", "last_run_late": False, "universe": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
         "positions": [{}, {}, {}]}
RISK = {"circuit_breaker": False, "drawdown_halted": False, "killed_strategies": {}}
ACCOUNT = {"equity": 1003.4, "realized_pnl": 0.0, "unrealized_pnl": 3.4, "open_positions": 3, "daily_pnl": 0.0,
           "weekly_pnl": 0.0, "drawdown_pct": 0.0, "exposure_pct": 0.27}
J_OK = {"available": True, "errors": 0, "first_error": "", "regime_changed": 1, "telegram_sent": 3,
        "telegram_failed": 0, "restarts": 0}


def test_healthy_before_deadline_is_silent():
    rep = om.evaluate(_now(0, 10), HEALTH, {**TREND, "last_run_utc": "2026-09-02T00:05:41Z"}, RISK, ACCOUNT,
                      J_OK, J_OK, J_OK, {})
    assert rep.alerts == [] and rep.summary is None


def test_missing_trend_run_after_deadline_and_daily_summary_once():
    stale = {**TREND, "last_run_utc": "2026-09-02T00:05:41Z"}
    rep = om.evaluate(_now(0, 33), HEALTH, stale, RISK, ACCOUNT, J_OK, J_OK, J_OK, {})
    assert [a["key"] for a in rep.alerts] == ["trend_missing"]
    assert rep.summary and "resumen diario 2026-09-03" in rep.summary and "1003.40" in rep.summary
    again = om.evaluate(_now(0, 48), HEALTH, stale, RISK, ACCOUNT, J_OK, J_OK, J_OK,
                        {"last_summary_date": "2026-09-03"})
    assert again.summary is None                                   # only once per day
    ok = om.evaluate(_now(0, 33), HEALTH, TREND, RISK, ACCOUNT, J_OK, J_OK, J_OK, {"last_summary_date": "2026-09-03"})
    assert ok.alerts == []


def test_bridge_down_engine_down_stale_feed_and_risk_halts():
    rep = om.evaluate(_now(12, 0), {"_error": "URLError: refused"}, None, None, None, J_OK, J_OK, J_OK, {})
    assert [a["key"] for a in rep.alerts] == ["bridge_down"]
    rep = om.evaluate(_now(12, 0), {**HEALTH, "engine_running": False}, TREND, RISK, ACCOUNT, J_OK, J_OK, J_OK, {})
    assert "engine_down" in [a["key"] for a in rep.alerts]
    rep = om.evaluate(_now(12, 0), {**HEALTH, "ws_connected": False, "last_tick_age_sec": 900}, TREND, RISK, ACCOUNT,
                      J_OK, J_OK, J_OK, {})
    assert {"ws_down", "stale_ticks"} <= {a["key"] for a in rep.alerts}
    rep = om.evaluate(_now(12, 0), HEALTH, {**TREND, "last_run_status": "error", "last_error": "boom"},
                      {**RISK, "circuit_breaker": True, "drawdown_halted": True,
                       "killed_strategies": {"MEAN_REVERSION": "t -3"}},
                      ACCOUNT, J_OK, J_OK, J_OK, {})
    keys = {a["key"] for a in rep.alerts}
    assert {"trend_error", "circuit_breaker", "drawdown_halt", "killed:MEAN_REVERSION"} <= keys


def test_journal_errors_restart_loop_and_regime_flood():
    two = {**J_OK, "restarts": 2}                                  # a deploy: not a loop
    assert om.evaluate(_now(12, 0), HEALTH, TREND, RISK, ACCOUNT, two, J_OK, J_OK, {}).alerts == []
    bad15 = {**J_OK, "errors": 4, "first_error": "RuntimeError: x", "restarts": 3}
    flood60 = {**J_OK, "regime_changed": 20}
    rep = om.evaluate(_now(12, 0), HEALTH, TREND, RISK, ACCOUNT, bad15, flood60, J_OK, {})
    keys = [a["key"] for a in rep.alerts]
    assert keys == ["journal_errors", "restart_loop", "regime_flood"]
    assert "RuntimeError: x" in rep.alerts[0]["text"]


def test_plan_sends_dedups_alerts_and_notifies_recovery():
    rep = om.Report(alerts=[{"key": "ws_down", "text": "feed"}])
    now = 1_000_000.0
    assert [p["kind"] for p in om.plan_sends(rep, {}, now)] == ["alert"]
    recent = {"last_alerts": {"ws_down": now - 3600}}
    assert om.plan_sends(rep, recent, now) == []                  # same alert < 6 h ago → silent
    old = {"last_alerts": {"ws_down": now - 7 * 3600}}
    assert [p["kind"] for p in om.plan_sends(rep, old, now)] == ["alert"]
    # alert cleared → one recovery notice, not repeated once notified
    clear = om.Report()
    plan = om.plan_sends(clear, {"last_alerts": {"ws_down": now - 60}}, now)
    assert [p["kind"] for p in plan] == ["recovered"] and "ws_down" in plan[0]["text"]
    assert om.plan_sends(clear, {"last_alerts": {"ws_down": now - 60}, "recovered_notified": {"ws_down": now - 60}},
                         now) == []
    # summary rides along
    with_sum = om.Report(summary="<b>resumen</b>")
    assert [p["kind"] for p in om.plan_sends(with_sum, {}, now)] == ["summary"]
