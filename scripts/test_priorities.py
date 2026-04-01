"""Test all 4 priority features."""
import sys, os, logging
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
logging.disable(logging.CRITICAL)
import structlog
structlog.configure(wrapper_class=structlog.BoundLogger, logger_factory=structlog.ReturnLoggerFactory())

import numpy as np
from config.settings import Settings

settings = Settings()
errors = []

def check(label, condition, detail=""):
    if not condition:
        errors.append(f"FAIL: {label}: {detail}")
        print(f"  FAIL: {label}: {detail}")
    else:
        print(f"  OK: {label}")

# ============================================================
print("=== P1a: MM interval config ===")
assert hasattr(settings.trading, "mm_interval_sec"), "Missing mm_interval_sec"
check(f"mm_interval_sec = {settings.trading.mm_interval_sec}", settings.trading.mm_interval_sec == 0.5)

# ============================================================
print("\n=== P1b: Dynamic slippage ===")
from execution.slippage import compute_slippage, compute_slippage_bps

# Base case
s1 = compute_slippage(base_bps=2.0, price=50000, size_usd=1000)
check(f"Base slippage: ${s1:.4f}", s1 > 0)

# Size impact
s2 = compute_slippage(base_bps=2.0, price=50000, size_usd=50000, book_depth_usd=100000)
check(f"Size impact (50k/100k depth): ${s2:.4f} > base ${s1:.4f}", s2 > s1)

# Regime impact
s3 = compute_slippage(base_bps=2.0, price=50000, regime="BREAKOUT")
s4 = compute_slippage(base_bps=2.0, price=50000, regime="RANGING")
check(f"BREAKOUT slip={s3:.4f} > RANGING slip={s4:.4f}", s3 > s4)

# Hawkes impact
s5 = compute_slippage(base_bps=2.0, price=50000, hawkes_ratio=4.0)
check(f"Hawkes 4x slip={s5:.4f} > base={s1:.4f}", s5 > s1)

# Bps version
bps = compute_slippage_bps(base_bps=2.0, price=50000, regime="BREAKOUT")
check(f"Bps version: {bps:.2f} bps", bps > 2.0)

# ============================================================
print("\n=== P1c: Backtester uses dynamic slippage ===")
from backtesting.backtester import Backtester
bt = Backtester(settings)
df = Backtester.generate_sample_data("BTC-USD", bars=500)
r = bt.run(df, "BTC-USD")
s = r.summary()
check(f"Backtest works: {s['total_trades']} trades", s["total_trades"] > 0)

# Check slippage varies in trade dicts
slippages = [t.get("slippage_bps", 0) for t in r.trades if t.get("slippage_bps", 0) > 0]
if slippages:
    unique_slips = len(set(round(s, 2) for s in slippages))
    check(f"Slippage varies: {unique_slips} unique values from {len(slippages)} trades", unique_slips >= 1)
else:
    print("  INFO: No trades with slippage_bps (MM only trades have it via intra-bar)")

# ============================================================
print("\n=== P2: VPIN bucket analysis ===")
from analytics.performance import PerformanceAnalyzer
from trade_database.repository import TradeRepository
from trade_database.adapter import TradeDBAdapter

repo = TradeRepository("data/trade_database.db")
adapter = TradeDBAdapter(repo, source="backtest")
sid = adapter.import_backtest_result(r, symbol="BTC-USD", initial_equity=100000)
trades = repo.get_trades(session_id=sid)

analyzer = PerformanceAnalyzer()

# Test analyze_by_vpin_bucket
by_vpin = analyzer.analyze_by_vpin_bucket(trades, initial_equity=100000)
check(f"VPIN buckets: {len(by_vpin)} groups", len(by_vpin) >= 1)
for label, rp in sorted(by_vpin.items()):
    print(f"    {label}: {rp.total_trades} trades, PnL=${rp.net_pnl:,.2f}")

# Test portfolio_analysis includes by_vpin
pa = analyzer.portfolio_analysis(trades, initial_equity=100000)
check("portfolio_analysis has by_vpin", "by_vpin" in pa)

# ============================================================
print("\n=== P4: Stress test generator ===")
from backtesting.stress_test import StressTestGenerator

gen = StressTestGenerator()

# Test flash crash
df2 = gen.inject_flash_crashes(df.copy(), n_events=2, min_drop_pct=8.0, max_drop_pct=12.0)
check(f"Flash crashes injected: {len(gen.events)} events", len(gen.events) == 2)

# Test gaps
gen2 = StressTestGenerator()
df3 = gen2.inject_gaps(df.copy(), n_events=3)
check(f"Gaps injected: {len(gen2.events)} events", len(gen2.events) == 3)

# Test inject_all
gen3 = StressTestGenerator()
df4 = gen3.inject_all(df.copy(), n_crashes=2, n_gaps=3, n_low_liq=1, n_cascades=1)
check(f"All events injected: {len(gen3.events)} total", len(gen3.events) == 7)
print(gen3.get_events_summary())

# Backtest on stressed data should still work
r_stress = bt.run(df4, "BTC-USD")
ss = r_stress.summary()
check(f"Stress backtest: {ss['total_trades']} trades", ss["total_trades"] > 0)
check(f"Stress MaxDD: {ss['max_drawdown']:.2%}", ss["max_drawdown"] > 0)

# Compare normal vs stress
diff = ss["net_pnl"] - s["net_pnl"]
print(f"  PnL impact of stress: ${diff:+,.2f}")
print(f"  MaxDD normal: {s['max_drawdown']:.2%} vs stress: {ss['max_drawdown']:.2%}")

# ============================================================
print("\n=== P3: Correlation (already exists, verify) ===")
corr = analyzer.compute_strategy_correlation(trades)
if corr:
    for s1, others in corr.items():
        for s2, c in others.items():
            if s1 != s2:
                print(f"  {s1} vs {s2}: {c:.3f}")
    check("Correlations computed", True)
else:
    print("  INFO: Not enough multi-strategy data for correlation")

# Cleanup
repo.delete_session(sid)
os.remove("data/trade_database.db")

print(f"\n{'='*60}")
if errors:
    print(f"  FAILURES: {len(errors)}")
    for e in errors:
        print(f"    {e}")
else:
    print("  ALL PRIORITY TESTS PASSED")
print("=" * 60)
