import os

# Runtime overrides (data/config_overrides.json, edited from the UI) must never leak
# into test assertions: every Settings() built by the suite uses code defaults.
os.environ.setdefault("BOTSTRIKE_NO_OVERRIDES", "1")
# The TREND_DAILY engine must never read/write the developer's real book or cache.
import tempfile as _tempfile
_tmp = _tempfile.mkdtemp(prefix="botstrike_tests_")
os.environ.setdefault("BOTSTRIKE_TREND_STATE", os.path.join(_tmp, "trend_state.json"))
os.environ.setdefault("BOTSTRIKE_TREND_DATA_DIR", os.path.join(_tmp, "binance_daily"))
# The activity feed (analytics/activity.py) must never write into data/activity.json from tests:
# the CT test gate polluted the production feed with synthetic trend runs (2026-09-02).
os.environ.setdefault("BOTSTRIKE_ACTIVITY_PATH", os.path.join(_tmp, "activity.json"))

# Script-style test files: they run assertions at import time via a `check(name, cond)`
# helper instead of pytest functions, so collecting them would execute side effects.
#
# ⚠️ DEUDA CONOCIDA (audit R2 tests_quality-07, P0). These four are excluded here AND
# nobody runs them anywhere else, so they are dead weight, not coverage. Measured
# 2026-08-31 by running each one standalone (`py -3.12 tests/<file>.py`):
#
#   test_bug_fixes.py              exit 1    (red)
#   test_self_audit.py             exit 124  (hangs — times out)
#   test_p0_fixes.py               exit 1    (red)
#   test_execution_intelligence.py exit 0    (green, and the only one worth porting as-is)
#
# They cover paths the pytest suite does NOT: paper SL/TP fills, execution intelligence,
# self-audit invariants. Do not delete them and do not silently re-enable them either —
# porting them to real pytest functions is the actual task (tests_quality-07). Until then
# the honest statement is "138 pytest tests", never "all tests pass".
collect_ignore = [
    "test_bug_fixes.py",
    "test_self_audit.py",
    "test_p0_fixes.py",
    "test_execution_intelligence.py",
]


# ---------------------------------------------------------------------------
# NO TEST REACHES THE VENUE.
#
# The market endpoints call the venue's public API for the figures that describe a Strike market
# (mark, 24 h block, book, open interest). Two tests were quietly doing that for real: one asserted
# a mark of 100.6 and got 2,500.53 — the live price of ether — the day the endpoint started asking
# (2026-09-04). A test that reaches the network is a test that fails on a train, and worse, one that
# passes for the wrong reason. Every test starts with the venue stubbed out; a test that wants venue
# data patches these itself, and its patch wins because it is applied after this one.
import pytest as _pytest


@_pytest.fixture(autouse=True)
def _no_venue_network(monkeypatch):
    try:
        from server import bridge as _bridge
    except Exception:  # noqa: BLE001 - suites that never import the bridge
        return

    async def _md():
        return {"ts": 0.0, "ts_tick": 0.0, "ts_info": 0.0,
                "premium": {}, "ticker": {}, "filters": {}, "depth": {}, "oi": {}}

    async def _depth(_symbol):
        return None

    async def _oi(_symbol):
        return None

    monkeypatch.setattr(_bridge, "_venue_market_data", _md, raising=False)
    monkeypatch.setattr(_bridge, "_venue_depth", _depth, raising=False)
    monkeypatch.setattr(_bridge, "_venue_open_interest", _oi, raising=False)
