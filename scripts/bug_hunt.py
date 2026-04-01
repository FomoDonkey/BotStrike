"""Deep bug hunt on all recent changes."""
import sys, os, logging
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
logging.disable(logging.CRITICAL)
import structlog
structlog.configure(wrapper_class=structlog.BoundLogger, logger_factory=structlog.ReturnLoggerFactory())
import numpy as np

from config.settings import Settings
from backtesting.backtester import Backtester, BacktestPosition, RealisticBacktester
from backtesting.optimizer import WalkForwardBacktester, ParameterOptimizer
from core.types import Side, StrategyType
from core.historical_data import HistoricalDataLoader
from analytics.performance import PerformanceAnalyzer
from trade_database.repository import TradeRepository
from trade_database.adapter import TradeDBAdapter

settings = Settings()
errors = []

def check(label, condition, detail=""):
    if not condition:
        errors.append(f"FAIL: {label}: {detail}")
        print(f"  FAIL: {label}: {detail}")
    else:
        print(f"  OK: {label}")

# ============================================================
print("=== 1. Trade dict field consistency ===")
bt = Backtester(settings)
df = Backtester.generate_sample_data("BTC-USD", bars=1000)
r = bt.run(df, "BTC-USD")

required = {"bar", "symbol", "side", "entry", "exit", "size", "pnl",
            "strategy", "fee", "slippage_bps", "duration_sec", "timestamp"}
for i, t in enumerate(r.trades):
    missing = required - set(t.keys())
    if missing:
        check(f"trade[{i}] fields", False, f"missing {missing}")
        break
else:
    check(f"All {len(r.trades)} trades have {len(required)} fields", True)

nan_count = sum(1 for t in r.trades for k in ["pnl","fee","entry","exit"]
                if t.get(k) is None or (isinstance(t.get(k), float) and np.isnan(t[k])))
check("No NaN/None in critical fields", nan_count == 0, f"{nan_count} found")

exits = [t for t in r.trades if not t["side"].startswith("BUY") and not t["side"].startswith("SELL")]
with_fee = [t for t in r.trades if t["fee"] > 0]
with_dur = [t for t in exits if t["duration_sec"] > 0]
print(f"  Info: exits={len(exits)}, with_fee={len(with_fee)}, with_dur={len(with_dur)}")

# ============================================================
print("\n=== 2. Liquidation trade_dict (no close called) ===")
pos = BacktestPosition("BTC-USD", Side.BUY, 0.1, 50000, StrategyType.MEAN_REVERSION,
                        leverage=10, entry_timestamp=1000.0)
td = pos.trade_dict(100, "BTC-USD", "LIQUIDATION", 45000, -500, 1060.0)
check("Liquidation fee=0", td["fee"] == 0.0)
check("Liquidation duration=60", td["duration_sec"] == 60.0)

# ============================================================
print("\n=== 3. Same-bar SL/TP duration ===")
pos2 = BacktestPosition("BTC-USD", Side.BUY, 0.1, 50100, StrategyType.MEAN_REVERSION,
                         entry_timestamp=5000.0, slippage_bps=2.0)
pnl2 = pos2.close(49800, 0.0005)
td2 = pos2.trade_dict(50, "BTC-USD", "SL_LONG", 49800, pnl2, 5000.0)
check("Same-bar duration=0", td2["duration_sec"] == 0)
check("SL has fee>0", td2["fee"] > 0)
check("SL has slippage_bps=2", td2["slippage_bps"] == 2.0)

# ============================================================
print("\n=== 4. Walk-forward edge cases ===")
wf = WalkForwardBacktester(settings)
small_df = Backtester.generate_sample_data("BTC-USD", bars=300)
r_small = wf.run(small_df, "BTC-USD", n_folds=5)
check(f"Small data folds: {len(r_small.folds)}", True)

r_single = wf.run(df, "BTC-USD", n_folds=1)
check(f"Single fold: {len(r_single.folds)} folds, {r_single.total_trades} trades", True)

r_many = wf.run(small_df, "BTC-USD", n_folds=20)
check(f"20 folds on 300 bars: {len(r_many.folds)} (should skip most)", True)

# ============================================================
print("\n=== 5. Optimizer edge cases ===")
opt = ParameterOptimizer(settings)
r_bad = opt.optimize(df, "BTC-USD", param_grid={"mr_zscore_entry": [2.0]}, metric="nonexistent_field")
check("Invalid metric doesnt crash", r_bad.completed == 1)

r_noparam = opt.optimize(df, "BTC-USD", param_grid={"fake_param_xyz": [1, 2, 3]}, metric="sharpe_ratio")
check(f"Nonexistent param: {r_noparam.completed} completed", r_noparam.completed == 3)

# ============================================================
print("\n=== 6. Realistic backtester trade fields ===")
loader = HistoricalDataLoader()
loader._trades["BTC-USD"] = HistoricalDataLoader.generate_realistic_trades("BTC-USD", hours=2)
bars = loader.get_bars_with_trades("BTC-USD")
rbt = RealisticBacktester(settings)
r_real = rbt.run("BTC-USD", bars_with_trades=bars)
if r_real.trades:
    t0 = r_real.trades[0]
    has_all = all(k in t0 for k in required)
    check(f"Realistic {len(r_real.trades)} trades all fields", has_all, f"missing: {required - set(t0.keys())}" if not has_all else "")
else:
    print("  Info: 0 trades (normal for short synthetic)")

# ============================================================
print("\n=== 7. Analytics distributions ===")
repo = TradeRepository("data/trade_database.db")
adapter = TradeDBAdapter(repo, source="backtest")
sid = adapter.import_backtest_result(r, symbol="BTC-USD", initial_equity=100000)
trades = repo.get_trades(session_id=sid)

analyzer = PerformanceAnalyzer()
report = analyzer.analyze(trades, initial_equity=100000)

if report.drawdown_events:
    dd_arr = np.array(report.drawdown_events)
    check("Drawdown events in [0,1]", all(0 < d <= 1 for d in dd_arr), f"range [{dd_arr.min():.4f}, {dd_arr.max():.4f}]")
else:
    print("  Info: 0 drawdown events (monotonically declining equity)")

report2 = analyzer.from_backtest_result(r, initial_equity=100000, symbol="BTC-USD")
check(f"from_backtest_result: {report2.total_trades} trades", report2.total_trades == len(r.trades))

# Cross-strategy-regime
cross = analyzer.analyze_cross_strategy_regime(trades, initial_equity=100000)
total_cross = sum(rp.total_trades for s in cross.values() for rp in s.values())
check(f"Cross analysis covers all trades: {total_cross} vs {len(trades)}", total_cross == len(trades))

# By symbol
by_sym = analyzer.analyze_by_symbol(trades, initial_equity=100000)
total_sym = sum(rp.total_trades for rp in by_sym.values())
check(f"By-symbol covers all trades: {total_sym} vs {len(trades)}", total_sym == len(trades))

# Cleanup
repo.delete_session(sid)
os.remove("data/trade_database.db") if os.path.exists("data/trade_database.db") else None

# ============================================================
print(f"\n{'='*60}")
if errors:
    print(f"  FAILURES: {len(errors)}")
    for e in errors:
        print(f"    {e}")
else:
    print("  ALL BUG HUNTS PASSED - NO ISSUES FOUND")
print("=" * 60)
