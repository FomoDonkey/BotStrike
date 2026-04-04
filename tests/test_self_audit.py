"""Self-audit tests to verify all bug fixes work correctly end-to-end."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import logging
logging.disable(logging.CRITICAL)

import structlog
structlog.configure(wrapper_class=structlog.BoundLogger, logger_factory=structlog.ReturnLoggerFactory())

from collections import defaultdict
import numpy as np

from config.settings import Settings
from core.types import Signal, Side, StrategyType, Trade, MarketRegime
from core.market_data import MarketDataCollector
from core.indicators import Indicators
from execution.paper_simulator import PaperTradingSimulator, PaperPosition
from execution.order_engine import OrderExecutionEngine
from risk.risk_manager import RiskManager
from backtesting.backtester import Backtester
from archive.backtesting.optimizer import WalkForwardBacktester
from analytics.performance import PerformanceAnalyzer
from trade_database.models import TradeRecord
from logging_metrics.logger import MetricsCollector

s = Settings()
passed = 0
failed = 0
issues = []

def check(name, condition, detail=""):
    global passed, failed, issues
    if condition:
        print(f"  PASS: {name}")
        passed += 1
    else:
        print(f"  FAIL: {name} -- {detail}")
        failed += 1
        issues.append(f"{name}: {detail}")


# ============================================================
print("=== 1: mm_unwind recognized as exit ===")
# ============================================================
ps = PaperTradingSimulator(s)
pos = PaperPosition("BTC-USD", Side.BUY, 0.01, 50000, StrategyType.MARKET_MAKING)
ps._positions["BTC-USD_MARKET_MAKING"] = pos
ps._last_prices["BTC-USD"] = 50500

unwind = Signal(
    strategy=StrategyType.MARKET_MAKING, symbol="BTC-USD", side=Side.SELL,
    strength=1.0, entry_price=50500, stop_loss=50500, take_profit=50500,
    size_usd=505, metadata={"action": "mm_unwind", "reason": "regime_change"}
)
fills = ps.execute_signals([unwind], [], s.symbols[0])
check("mm_unwind produces fill", len(fills) == 1, f"got {len(fills)} fills")
check("mm_unwind closes position", "BTC-USD_MARKET_MAKING" not in ps._positions)

# ============================================================
print("\n=== 2: Sharpe ratio sanity ===")
# ============================================================
bt = Backtester(s)
df = Backtester.generate_sample_data("BTC-USD", 5000, 50000)
result = bt.run(df, "BTC-USD")
summary = result.summary()
sharpe = summary.get("sharpe_ratio", 0)
check("Sharpe not absurd (<100)", abs(sharpe) < 100, f"Sharpe={sharpe}")
check("Sharpe is float", isinstance(sharpe, (int, float)))

# ============================================================
print("\n=== 3: Bar boundary no contamination ===")
# ============================================================
mdc = MarketDataCollector.__new__(MarketDataCollector)
mdc._tick_buffer = defaultdict(list)
mdc._last_bar_time = {}
mdc._last_data_time = {}
mdc._dataframes = {}
mdc._snapshots = {}
mdc.bar_interval = 60
mdc.settings = s
# Tick quality guard state (pre-warmed for unit test)
mdc._ws_connect_time = 0.0  # no warmup
mdc._first_tick_skipped = {"BTC-USD": True}  # skip already done
mdc._last_accepted_price = {}
mdc._tick_jitter_ema = {}
mdc._last_tick_time = {}
mdc._ticks_accepted = 0
mdc._ticks_rejected_warmup = 0
mdc._ticks_rejected_stale = 0
mdc._ticks_rejected_first = 0

mdc.on_trade("BTC-USD", 50000, 0.1, 1000.0)
mdc.on_trade("BTC-USD", 50100, 0.2, 1030.0)
mdc.on_trade("BTC-USD", 50200, 0.3, 1059.0)
mdc.on_trade("BTC-USD", 50300, 0.4, 1061.0)

df_bar = mdc._dataframes.get("BTC-USD")
check("Bar created", df_bar is not None and len(df_bar) == 1)
if df_bar is not None and len(df_bar) == 1:
    bar = df_bar.iloc[0]
    check("Bar close=50200 (not 50300)", float(bar["close"]) == 50200, f"close={bar['close']}")
    check("Bar high=50200 (not 50300)", float(bar["high"]) == 50200, f"high={bar['high']}")
buf = mdc._tick_buffer["BTC-USD"]
check("Buffer has 1 tick (50300)", len(buf) == 1 and buf[0]["price"] == 50300)

# ============================================================
print("\n=== 4: MR entry+exit lifecycle ===")
# ============================================================
ps2 = PaperTradingSimulator(s)
entry = Signal(
    strategy=StrategyType.MEAN_REVERSION, symbol="BTC-USD", side=Side.BUY,
    strength=0.5, entry_price=50000, stop_loss=49000, take_profit=52000,
    size_usd=5000, metadata={}
)
ef = ps2.execute_signals([entry], [], s.symbols[0])
check("MR entry creates position", "BTC-USD_MEAN_REVERSION" in ps2._positions)

exit_sig = Signal(
    strategy=StrategyType.MEAN_REVERSION, symbol="BTC-USD", side=Side.SELL,
    strength=0.8, entry_price=50500, stop_loss=50500, take_profit=50500,
    size_usd=5000, metadata={"action": "exit_mean_reversion"}
)
xf = ps2.execute_signals([exit_sig], [], s.symbols[0])
check("MR exit produces fill with PnL", len(xf) == 1 and xf[0].pnl != 0, f"fills={len(xf)}")
check("MR exit closes position", "BTC-USD_MEAN_REVERSION" not in ps2._positions)

# ============================================================
print("\n=== 5: TF SL trigger in paper sim ===")
# ============================================================
ps3 = PaperTradingSimulator(s)
entry_tf = Signal(
    strategy=StrategyType.TREND_FOLLOWING, symbol="BTC-USD", side=Side.BUY,
    strength=0.7, entry_price=50000, stop_loss=49500, take_profit=53000,
    size_usd=5000, metadata={"trigger": "ema_cross"}
)
ps3.execute_signals([entry_tf], [], s.symbols[0])
check("TF entry creates position", "BTC-USD_TREND_FOLLOWING" in ps3._positions)

sl_trades = ps3.on_price_update("BTC-USD", 49400, high=50000, low=49400)
check("TF SL triggers", len(sl_trades) == 1 and sl_trades[0].pnl < 0)
check("TF position closed after SL", "BTC-USD_TREND_FOLLOWING" not in ps3._positions)

# ============================================================
print("\n=== 6: Risk limits + signal immutability ===")
# ============================================================
rm = RiskManager(s)
big = Signal(
    strategy=StrategyType.MEAN_REVERSION, symbol="BTC-USD", side=Side.BUY,
    strength=0.5, entry_price=50000, stop_loss=49000, take_profit=52000,
    size_usd=50000, metadata={}
)
validated = rm.validate_signal(big, s.symbols[0], MarketRegime.RANGING)
if validated:
    check("Size capped <= 20000", validated.size_usd <= 20000, f"size={validated.size_usd}")
else:
    check("Signal rejected (exceeds limits)", True)
check("Original signal immutable", big.size_usd == 50000, f"mutated to {big.size_usd}")

# ============================================================
print("\n=== 7: Consecutive losses tracking ===")
# ============================================================
rm2 = RiskManager(s)
rm2.record_trade_result(-10)
rm2.record_trade_result(-10)
check("2 losses -> consecutive=2", rm2._consecutive_losses == 2)
rm2.record_trade_result(0)  # break-even
check("pnl=0 no reset", rm2._consecutive_losses == 2, f"was {rm2._consecutive_losses}")
rm2.record_trade_result(10)  # win
check("win resets to 0", rm2._consecutive_losses == 0)

# ============================================================
print("\n=== 8: Paper sim mark_price tracking ===")
# ============================================================
ps4 = PaperTradingSimulator(s)
pos4 = PaperPosition("ETH-USD", Side.BUY, 1.0, 3000, StrategyType.MEAN_REVERSION)
ps4._positions["ETH-USD_MEAN_REVERSION"] = pos4
ps4.on_price_update("ETH-USD", 3200)
position = ps4.get_position("ETH-USD", StrategyType.MEAN_REVERSION)
check("mark_price=3200 (not entry 3000)", position.mark_price == 3200, f"mark={position.mark_price}")
check("unrealized_pnl > 0", position.unrealized_pnl > 0, f"pnl={position.unrealized_pnl}")

# ============================================================
print("\n=== 9: MetricsCollector cumulative ===")
# ============================================================
mc = MetricsCollector()
for i in range(6000):
    mc.add_trade(Trade(symbol="BTC", side=Side.BUY, price=100, quantity=1, fee=0.01, pnl=0.5))
metrics = mc.get_metrics()
check("total_trades=6000 (not 2500)", metrics["total_trades"] == 6000)
check("total_pnl=3000.0", metrics["total_pnl"] == 3000.0)
check("net_pnl == total_pnl (no double-count)", metrics["net_pnl"] == metrics["total_pnl"])

# ============================================================
print("\n=== 10: Stress test OHLCV validity ===")
# ============================================================
from archive.backtesting.stress_test import StressTestGenerator
df_base = Backtester.generate_sample_data("BTC-USD", 2000, 50000)
gen = StressTestGenerator()
stressed = gen.inject_all(df_base, n_crashes=3, n_gaps=5, n_low_liq=2, n_cascades=1)
check("high >= max(O,C)", (stressed["high"] >= stressed[["open","close"]].max(axis=1) - 0.01).all())
check("low <= min(O,C)", (stressed["low"] <= stressed[["open","close"]].min(axis=1) + 0.01).all())
check("All prices > 0", (stressed[["open","high","low","close"]] > 0).all().all())

# ============================================================
print("\n=== 11: Walk-forward optimizes (not just backtests) ===")
# ============================================================
wf = WalkForwardBacktester(s)
df_wf = Backtester.generate_sample_data("BTC-USD", 2000, 50000)
wf_result = wf.run(df_wf, "BTC-USD", n_folds=2, train_pct=0.7)
for f in wf_result.folds:
    check(f"Fold {f.fold_idx} has best_params", len(f.best_params) > 0, f"params={f.best_params}")
    print(f"    params={f.best_params}, trades={f.total_trades}, PnL=${f.net_pnl:.2f}")

# ============================================================
print("\n=== 12: Performance analyzer daily Sharpe + JSON ===")
# ============================================================
analyzer = PerformanceAnalyzer()
all_win = [
    TradeRecord(symbol="BTC", side="BUY", price=100, quantity=1, fee=0, pnl=10.0,
                timestamp=1000000 + i * 86400)
    for i in range(5)
]
r = analyzer.analyze(all_win, initial_equity=10000)
check("profit_factor=9999.99", r.profit_factor == 9999.99, f"got {r.profit_factor}")

import json
try:
    json.dumps({"pf": r.profit_factor, "sharpe": r.sharpe_ratio})
    check("JSON serializable", True)
except Exception as e:
    check("JSON serializable", False, str(e))


# ============================================================
if __name__ == "__main__":
    # FINAL SUMMARY
    # ============================================================
    print(f"\n{'='*50}")
    print(f"  SELF-AUDIT COMPLETE: {passed} PASSED, {failed} FAILED")
    if issues:
        print(f"  ISSUES:")
        for i in issues:
            print(f"    - {i}")
    else:
        print(f"  NO ISSUES FOUND")
    print(f"{'='*50}")
    sys.exit(0 if failed == 0 else 1)
