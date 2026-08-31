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
