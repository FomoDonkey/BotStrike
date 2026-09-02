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
