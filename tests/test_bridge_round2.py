"""Bridge round-2 fixes (audit 03, P1): real health, watchdog, backtest off-loop, residual auth.

No network, no real engine: start_engine/stop_engine/_run_backtest_sync/os._exit are mocked.
TestClient is used WITHOUT the context manager so the lifespan (broadcast loops, autostart)
never runs.
"""
import asyncio
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import server.bridge as bridge
from config.settings import Settings


class LogSpy:
    """Records structlog-style calls: (level, event, kwargs)."""

    def __init__(self):
        self.events = []

    def __getattr__(self, level):
        def _rec(event, *args, **kw):
            self.events.append((level, event, kw))
        return _rec

    def has(self, level, event):
        return any(l == level and e == event for l, e, _ in self.events)


@pytest.fixture
def st(monkeypatch):
    monkeypatch.delenv("BOTSTRIKE_AUTOSTART", raising=False)
    fresh = bridge.BridgeState()
    monkeypatch.setattr(bridge, "state", fresh)
    monkeypatch.setattr(bridge, "_EXPOSE_TOKEN", True)
    monkeypatch.setattr(bridge, "_AUTH_TOKEN", "t" * 32)
    return fresh


@pytest.fixture
def log(monkeypatch):
    spy = LogSpy()
    monkeypatch.setattr(bridge, "logger", spy)
    return spy


@pytest.fixture
def client():
    return TestClient(bridge.app)


def _fake_start(record, *, raise_exc=None):
    async def fake(mode="paper", settings=None):
        record.append({"mode": mode, "settings": settings})
        if raise_exc:
            raise raise_exc
        bridge.state.running = True
        bridge.state.mode = mode
        bridge.state.engine_expected = True
    return fake


def _fake_stop(record):
    async def fake(manual=False):
        record.append({"manual": manual})
        if manual:
            bridge.state.engine_expected = False
        bridge.state.running = False
    return fake


# ── 1. Health ────────────────────────────────────────────────────
def test_health_ok_when_engine_not_expected(st, client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["degraded"] is False
    assert body["engine_running"] is False and body["engine_expected"] is False
    assert body["ws_connected"] is False and body["last_tick_age_sec"] is None
    assert body["version"] == bridge.BRIDGE_VERSION == bridge.app.version


def test_health_503_when_engine_expected_but_dead(st, client):
    st.autostart_mode = "paper"
    st.engine_expected = True
    st.running = False
    r = client.get("/api/health")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "degraded" and body["degraded"] is True
    assert "engine_not_running" in body["reasons"]
    assert body["engine_expected"] is True and body["engine_running"] is False


def test_health_503_when_running_but_ticks_stale(st, client):
    st.running = True
    st.engine_expected = True
    st.engine_started_at = time.time() - 1000
    st.engine = SimpleNamespace(websocket=SimpleNamespace(_connected=True), market_data=None)
    st.last_tick_ts = time.time() - 200
    r = client.get("/api/health")
    assert r.status_code == 503
    assert "stale_ticks" in r.json()["reasons"]
    assert r.json()["ws_connected"] is True

    st.last_tick_ts = time.time() - 5
    r = client.get("/api/health")
    assert r.status_code == 200
    assert 4 <= r.json()["last_tick_age_sec"] <= 10


def test_health_uses_engine_market_data_last_tick(st):
    st.running = True
    st.engine_expected = True
    st.engine_started_at = time.time() - 1000
    st.last_tick_ts = 0.0
    st.engine = SimpleNamespace(websocket=SimpleNamespace(_connected=True),
                                market_data=SimpleNamespace(_last_data_time={"BTC-USD": time.time() - 3}))
    snap = bridge._health_snapshot()
    assert snap["degraded"] is False and 2 <= snap["last_tick_age_sec"] <= 10


def test_health_no_ticks_after_grace_is_degraded(st):
    st.running = True
    st.engine_expected = True
    st.engine_started_at = time.time() - 10  # within grace
    assert bridge._health_snapshot()["degraded"] is False
    st.engine_started_at = time.time() - 500
    assert "no_ticks" in bridge._health_snapshot()["reasons"]


# ── 4. Auth residual ─────────────────────────────────────────────
def test_remote_bind_requires_token_on_start_stop_backtest(st, client, monkeypatch):
    monkeypatch.setattr(bridge, "_EXPOSE_TOKEN", False)
    starts, stops = [], []
    monkeypatch.setattr(bridge, "start_engine", _fake_start(starts))
    monkeypatch.setattr(bridge, "stop_engine", _fake_stop(stops))

    assert client.post("/api/bot/start?mode=paper").status_code == 401
    assert client.post("/api/bot/start?mode=dry_run").status_code == 401
    assert client.post("/api/bot/start?mode=paper&token=wrong").status_code == 401
    st.running = True
    st.mode = "paper"
    assert client.post("/api/bot/stop").status_code == 401
    assert client.post("/api/backtest/run", json={"symbol": "BTC-USD"}).status_code == 401
    assert starts == [] and stops == []

    # Correct token (header) -> stop works and is a manual stop (engine no longer expected)
    r = client.post("/api/bot/stop", headers={"X-BotStrike-Token": bridge._AUTH_TOKEN})
    assert r.status_code == 200 and r.json()["status"] == "stopped"
    assert stops == [{"manual": True}] and st.engine_expected is False

    # Correct token (query) -> start works and settings are forwarded (fix 6)
    r = client.post(f"/api/bot/start?mode=paper&exchange=hyperliquid&token={bridge._AUTH_TOKEN}")
    assert r.status_code == 200 and r.json() == {"status": "starting", "mode": "paper", "exchange": "hyperliquid"}
    assert len(starts) == 1 and starts[0]["mode"] == "paper"
    assert isinstance(starts[0]["settings"], Settings)
    assert starts[0]["settings"].trading.exchange_venue == "hyperliquid"
    assert starts[0]["settings"].trading.taker_fee == pytest.approx(0.00045)


def test_loopback_paper_start_stop_without_token(st, client, monkeypatch):
    starts, stops = [], []
    monkeypatch.setattr(bridge, "start_engine", _fake_start(starts))
    monkeypatch.setattr(bridge, "stop_engine", _fake_stop(stops))

    r = client.post("/api/bot/start?mode=paper")
    assert r.status_code == 200 and r.json()["status"] == "starting"
    assert starts[0]["settings"].trading.exchange_venue == "binance"
    assert client.post("/api/bot/start?mode=paper").json()["status"] == "already_running"

    r = client.post("/api/bot/stop")
    assert r.status_code == 200 and r.json()["status"] == "stopped"
    assert stops == [{"manual": True}]

    # Live still needs the token even on loopback; bad mode/exchange -> 400
    assert client.post("/api/bot/start?mode=live").status_code == 401
    assert client.post("/api/bot/start?mode=evil").status_code == 400
    assert client.post("/api/bot/start?mode=paper&exchange=evil").status_code == 400
    assert len(starts) == 1


def test_docs_hidden_on_remote_bind(st, client, monkeypatch):
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200
    monkeypatch.setattr(bridge, "_EXPOSE_TOKEN", False)
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/api/health").status_code == 200


# ── 3. Backtest off the event loop ───────────────────────────────
def test_backtest_runs_in_worker_thread(st, client, monkeypatch):
    seen = {}

    def fake_sync(body):
        seen["thread"] = threading.current_thread().name
        seen["body"] = body
        return {"total_trades": 7}

    real_to_thread = bridge._to_thread
    calls = []

    async def spy_to_thread(fn, *a, **kw):
        calls.append((fn, threading.current_thread().name))
        return await real_to_thread(fn, *a, **kw)

    monkeypatch.setattr(bridge, "_run_backtest_sync", fake_sync)
    monkeypatch.setattr(bridge, "_to_thread", spy_to_thread)

    r = client.post("/api/backtest/run", json={"symbol": "BTC-USD", "bars": 500})
    assert r.status_code == 200 and r.json() == {"total_trades": 7}
    assert len(calls) == 1 and calls[0][0] is fake_sync
    loop_thread = calls[0][1]
    assert seen["thread"] != loop_thread, "backtest must not run on the event-loop thread"
    assert seen["body"]["bars"] == 500
    assert st.backtest_running is False


def test_backtest_rejects_unknown_symbol(st, client, monkeypatch):
    monkeypatch.setattr(bridge, "_run_backtest_sync", lambda body: pytest.fail("must not run"))
    assert client.post("/api/backtest/run", json={"symbol": "../../etc/passwd"}).status_code == 400
    assert client.post("/api/backtest/run", json={"symbol": 42}).status_code == 400


def test_backtest_409_when_one_already_running(st, monkeypatch):
    release = threading.Event()

    def blocking_sync(body):
        release.wait(timeout=10)
        return {"total_trades": 1}

    monkeypatch.setattr(bridge, "_run_backtest_sync", blocking_sync)
    results = {}

    def first():
        results["first"] = TestClient(bridge.app).post("/api/backtest/run", json={"symbol": "BTC-USD"})

    t = threading.Thread(target=first, daemon=True)
    t.start()
    deadline = time.time() + 5
    while not bridge.state.backtest_running and time.time() < deadline:
        time.sleep(0.01)
    assert bridge.state.backtest_running is True

    second = TestClient(bridge.app).post("/api/backtest/run", json={"symbol": "BTC-USD"})
    assert second.status_code == 409

    release.set()
    t.join(timeout=10)
    assert results["first"].status_code == 200
    assert bridge.state.backtest_running is False


# ── 2/5. Watchdog + crash visibility ─────────────────────────────
def test_watchdog_tick_engine_dead_and_stale_strikes(st):
    assert bridge._watchdog_tick() is None  # no autostart -> never fires
    st.autostart_mode = "paper"
    st.engine_expected = True
    st.running = False
    assert "not running" in bridge._watchdog_tick()

    st.running = True
    st.engine_started_at = time.time() - 1000
    st.last_tick_ts = time.time() - 400  # > 300 s
    assert bridge._watchdog_tick() is None
    assert bridge._watchdog_tick() is None
    reason = bridge._watchdog_tick()
    assert reason and "no ticks" in reason
    assert st.watchdog_stale_strikes == 0

    st.last_tick_ts = time.time() - 1  # fresh -> strikes reset
    st.watchdog_stale_strikes = 2
    assert bridge._watchdog_tick() is None and st.watchdog_stale_strikes == 0

    st.engine_expected = False  # operator stopped it -> watchdog stays quiet
    st.running = False
    assert bridge._watchdog_tick() is None


class _Exit(BaseException):
    pass


def test_restart_backoff_then_os_exit_after_budget(st, log, monkeypatch):
    st.autostart_mode = "paper"
    st.engine_expected = True
    monkeypatch.setattr(bridge, "_WATCHDOG_BACKOFF_SEC", (0.0, 0.0, 0.0, 0.0, 0.0))
    starts, stops, exits = [], [], []
    monkeypatch.setattr(bridge, "start_engine", _fake_start(starts, raise_exc=RuntimeError("ws down")))
    monkeypatch.setattr(bridge, "stop_engine", _fake_stop(stops))

    def fake_exit(code):
        exits.append(code)
        raise _Exit()

    monkeypatch.setattr(bridge.os, "_exit", fake_exit)

    with pytest.raises(_Exit):
        asyncio.run(bridge._restart_engine_after_failure("engine crashed"))

    assert len(starts) == bridge._WATCHDOG_MAX_ATTEMPTS == 5
    assert exits == [bridge._WATCHDOG_EXIT_CODE] == [3]
    assert len(st.restart_attempts) == 5
    assert st.restart_in_progress is False
    scheduled = [kw for l, e, kw in log.events if e == "engine_restart_scheduled"]
    assert [s["attempt"] for s in scheduled] == [1, 2, 3, 4, 5]
    assert log.has("exception", "engine_restart_failed")
    assert log.has("critical", "engine_restart_budget_exhausted")


def test_restart_backoff_values_and_success(st, log, monkeypatch):
    st.autostart_mode = "paper"
    st.engine_expected = True
    st.exchange = "binance"
    assert bridge._WATCHDOG_BACKOFF_SEC[:3] == (10.0, 30.0, 60.0)
    assert bridge._WATCHDOG_WINDOW_SEC == 600.0
    sleeps = []

    async def fake_sleep(d):
        sleeps.append(d)

    monkeypatch.setattr(bridge.asyncio, "sleep", fake_sleep)
    starts, stops, exits = [], [], []
    monkeypatch.setattr(bridge, "start_engine", _fake_start(starts))
    monkeypatch.setattr(bridge, "stop_engine", _fake_stop(stops))
    monkeypatch.setattr(bridge.os, "_exit", lambda code: exits.append(code))

    asyncio.run(bridge._restart_engine_after_failure("engine crashed"))
    assert sleeps == [10.0]
    assert len(starts) == 1 and starts[0]["mode"] == "paper"
    assert isinstance(starts[0]["settings"], Settings)
    assert stops == [{"manual": False}]
    assert exits == [] and st.running is True and st.restart_in_progress is False
    assert log.has("info", "engine_restarted")

    # Budget window: 5 attempts inside 10 min, the 6th is refused
    st.restart_attempts.clear()
    now = time.time()
    for i in range(5):
        st.restart_attempts.append(now - 60 * i)
    assert bridge._register_restart_attempt() is None
    st.restart_attempts.clear()
    st.restart_attempts.extend([now - 700] * 5)  # all older than the window -> pruned
    assert bridge._register_restart_attempt() == 1


def test_restart_noop_without_autostart(st, monkeypatch):
    starts = []
    monkeypatch.setattr(bridge, "start_engine", _fake_start(starts))
    st.autostart_mode = ""
    asyncio.run(bridge._restart_engine_after_failure("x"))
    assert starts == []


def test_run_engine_crash_is_logged_and_schedules_restart(st, log, monkeypatch):
    st.autostart_mode = "paper"
    st.engine_expected = True
    st.running = True

    async def boom():
        raise ValueError("bad config")

    st.engine = SimpleNamespace(start=boom)
    restarts = []

    async def fake_restart(reason):
        restarts.append(reason)

    monkeypatch.setattr(bridge, "_restart_engine_after_failure", fake_restart)

    async def main():
        await bridge._run_engine()
        await asyncio.sleep(0)  # let the spawned restart task run

    asyncio.run(main())
    assert st.running is False
    crashed = [kw for l, e, kw in log.events if l == "exception" and e == "engine_crashed"]
    assert crashed and crashed[0]["error_type"] == "ValueError"
    assert restarts == ["ValueError: bad config"]


def test_run_engine_normal_return_while_expected_triggers_restart(st, log, monkeypatch):
    st.autostart_mode = "paper"
    st.engine_expected = True
    st.running = True

    async def returns():
        return None

    st.engine = SimpleNamespace(start=returns)
    restarts = []

    async def fake_restart(reason):
        restarts.append(reason)

    monkeypatch.setattr(bridge, "_restart_engine_after_failure", fake_restart)

    async def main():
        await bridge._run_engine()
        await asyncio.sleep(0)

    asyncio.run(main())
    assert log.has("error", "engine_exited_unexpectedly")
    assert len(restarts) == 1


def test_run_engine_no_restart_when_not_autostart(st, log, monkeypatch):
    st.autostart_mode = ""
    st.engine_expected = True
    st.running = True

    async def boom():
        raise RuntimeError("x")

    st.engine = SimpleNamespace(start=boom)
    restarts = []

    async def fake_restart(reason):
        restarts.append(reason)

    monkeypatch.setattr(bridge, "_restart_engine_after_failure", fake_restart)

    async def main():
        await bridge._run_engine()
        await asyncio.sleep(0)

    asyncio.run(main())
    assert log.has("exception", "engine_crashed") and restarts == []


def test_critical_task_death_stops_engine(st, log):
    engine = SimpleNamespace(_running=True)
    st.engine = engine

    async def main():
        async def die():
            raise RuntimeError("strategy loop died")

        task = asyncio.create_task(die())
        try:
            await task
        except RuntimeError:
            pass
        bridge._on_critical_task_done(engine, "strategy", task)
        assert engine._running is False
        assert log.has("critical", "critical_task_died")

        # Orderly shutdown (engine._running already False) -> callback must stay silent
        engine2 = SimpleNamespace(_running=False)
        st.engine = engine2
        task2 = asyncio.create_task(die())
        try:
            await task2
        except RuntimeError:
            pass
        n = len(log.events)
        bridge._on_critical_task_done(engine2, "risk_monitor", task2)
        assert len(log.events) == n

        # Plain return while running is also a failure
        engine3 = SimpleNamespace(_running=True)
        st.engine = engine3

        async def ret():
            return None

        task3 = asyncio.create_task(ret())
        await task3
        bridge._on_critical_task_done(engine3, "ws_market", task3)
        assert engine3._running is False and log.has("critical", "critical_task_exited")

    asyncio.run(main())


def test_watch_critical_tasks_hooks_first_three(st):
    engine = SimpleNamespace(_running=True)
    st.engine = engine

    async def main():
        async def idle():
            await asyncio.sleep(0)

        tasks = [asyncio.create_task(idle()) for _ in range(5)]
        bridge._watch_critical_tasks(engine, tasks)
        await asyncio.gather(*tasks)
        await asyncio.sleep(0)  # done-callbacks run via call_soon

    asyncio.run(main())
    assert engine._running is False  # a critical task returning while running trips the guard


def test_manual_stop_clears_expected(st, log):
    st.autostart_mode = "paper"
    st.engine_expected = True
    st.engine = None
    st.engine_task = None
    asyncio.run(bridge.stop_engine(manual=True))
    assert st.engine_expected is False and st.running is False
    assert log.has("warning", "engine_stopped_by_operator")


def test_build_settings_venue_fees():
    s = bridge._build_settings("hyperliquid")
    assert s.trading.exchange_venue == "hyperliquid"
    assert s.trading.maker_fee == pytest.approx(0.00015)
    b = bridge._build_settings("binance")
    assert b.trading.exchange_venue == "binance"
    assert b.trading.taker_fee == Settings().trading.taker_fee
