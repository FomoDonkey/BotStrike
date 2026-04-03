# Script-style test files that run code at module level (not pytest-compatible).
# They can be run directly: python tests/test_bug_fixes.py
# Exclude them from pytest collection to prevent import-time side effects.
collect_ignore = [
    "test_bug_fixes.py",
    "test_self_audit.py",
    "test_p0_fixes.py",
    "test_execution_intelligence.py",
]
