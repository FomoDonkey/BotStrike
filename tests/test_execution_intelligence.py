"""Tests para la capa de execution intelligence."""
import time
import sys
sys.path.insert(0, ".")

from core.microprice import MicropriceCalculator
from core.types import OrderBook, OrderBookLevel
from execution.smart_router import (
    FillProbabilityModel, QueuePositionModel, SmartOrderRouter,
    SpreadPredictor, TradeIntensityModel, VWAPEngine, ExecutionAnalytics,
)
from execution.slippage import compute_slippage_advanced

passed = 0
failed = 0

def test(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}: {detail}")


print("=" * 60)
print("TESTING EXECUTION INTELLIGENCE LAYER")
print("=" * 60)

# 1. Microprice
print("\n=== MICROPRICE ===")
mp = MicropriceCalculator()
ob = OrderBook(symbol="BTC-USD", timestamp=0,
    bids=[
        OrderBookLevel(price=50000, quantity=10.0),
        OrderBookLevel(price=49990, quantity=5.0),
        OrderBookLevel(price=49980, quantity=3.0),
    ],
    asks=[
        OrderBookLevel(price=50010, quantity=2.0),
        OrderBookLevel(price=50020, quantity=5.0),
        OrderBookLevel(price=50030, quantity=8.0),
    ])

r = mp.compute(ob)
test("microprice valid", r.is_valid)
test("mid_price correct", r.mid_price == 50005.0)
test("microprice > mid (bid_qty >> ask_qty)", r.microprice > r.mid_price,
     f"mp={r.microprice}, mid={r.mid_price}")
test("adjustment_bps positive (bullish)", r.adjustment_bps > 0)
test("adjustment_bps near 1.0", r.adjustment_bps > 0.5)  # ~1bps with 10:2 imbalance

# OrderBook.microprice property
test("OrderBook.microprice property", ob.microprice > ob.mid_price)
test("OrderBook.spread_bps > 0", ob.spread_bps > 0)

# Balanced book → microprice ≈ mid
ob_balanced = OrderBook(symbol="X", timestamp=0,
    bids=[OrderBookLevel(price=100, quantity=5.0)],
    asks=[OrderBookLevel(price=101, quantity=5.0)])
r_bal = mp.compute(ob_balanced)
test("balanced book: microprice == mid", abs(r_bal.microprice - r_bal.mid_price) < 0.01)

# 2. Fill Probability
print("\n=== FILL PROBABILITY ===")
fpm = FillProbabilityModel()
r_close = fpm.estimate(distance_bps=1, atr_bps=20, book_depth_at_level_usd=5000,
                        trade_intensity=2, horizon_sec=5, spread_bps=5)
r_far = fpm.estimate(distance_bps=10, atr_bps=20, book_depth_at_level_usd=50000,
                      trade_intensity=0.5, horizon_sec=5, spread_bps=5)
test("close > far fill prob", r_close.fill_prob > r_far.fill_prob)
test("fill prob in [0,1]", 0 <= r_close.fill_prob <= 1)
test("expected_wait > 0", r_far.expected_wait_sec > 0)

# 3. Queue Model
print("\n=== QUEUE MODEL ===")
qm = QueuePositionModel()
q = qm.estimate(50000, 2.0, 500, 1000)
test("queue position > 0", q.estimated_position > 0)
test("time_to_front > 0", q.time_to_front_sec > 0)

# 4. Smart Router
print("\n=== SMART ROUTER ===")
router = SmartOrderRouter()

r_exit = router.route(side="SELL", price=50000, size_usd=5000, spread_bps=10,
                       atr_bps=20, book_depth_usd=100000, trade_intensity=1, is_exit=True)
test("exit -> MARKET", r_exit.order_type == "MARKET")

r_mm = router.route(side="BUY", price=50000, size_usd=500, spread_bps=8,
                     atr_bps=20, book_depth_usd=100000, trade_intensity=2, is_mm=True)
test("MM -> LIMIT", r_mm.order_type == "LIMIT")

r_tight = router.route(side="BUY", price=50000, size_usd=1000, spread_bps=2,
                        atr_bps=20, book_depth_usd=100000, trade_intensity=2, signal_strength=0.7)
test("tight spread -> MARKET", r_tight.order_type == "MARKET")

r_big = router.route(side="BUY", price=50000, size_usd=25000, spread_bps=8,
                      atr_bps=20, book_depth_usd=100000, trade_intensity=1, signal_strength=0.5)
test("large order -> TWAP", r_big.use_twap)
test("TWAP slices > 1", r_big.twap_slices > 1)

# 5. Spread Predictor
print("\n=== SPREAD PREDICTOR ===")
sp = SpreadPredictor()
for i in range(20):
    sp.on_spread(8.0 + (i % 4))
pred = sp.predict(10.0, atr_bps=25, vpin=0.6, hawkes_ratio=2.0)
test("predicted spread > 0", pred.predicted_spread_bps > 0)
test("direction is string", pred.direction in ("widening", "tightening", "stable"))

# 6. Trade Intensity
print("\n=== TRADE INTENSITY ===")
tim = TradeIntensityModel()
ts = time.time()
for i in range(30):
    tim.on_trade(ts + i * 0.5, is_buy=(i % 3 != 0), size_usd=1000)
ir = tim.current
test("buy_intensity > sell_intensity", ir.buy_intensity > ir.sell_intensity)
test("buy_ratio > 0.5 (more buys)", ir.buy_ratio > 0.5)
test("total > 0", ir.total_intensity > 0)

# 7. VWAP
print("\n=== VWAP ENGINE ===")
vwap = VWAPEngine()
plan = vwap.create_plan("test", 15000, 5, 10)
test("5 slices", len(plan.slices) == 5)
test("each slice $3000", plan.slices[0].target_size_usd == 3000)
test("not complete", not plan.is_complete)
vwap.mark_slice_executed("test", 0, 50005, 3000)
test("20% complete", abs(plan.completion_pct - 0.2) < 0.01)

# 8. Execution Analytics
print("\n=== EXECUTION ANALYTICS ===")
ea = ExecutionAnalytics()
for i in range(20):
    ea.record_execution(50000, 50000 + (i % 5) - 2, 50000,
                         "MARKET" if i % 3 == 0 else "LIMIT",
                         "MR", 1000, latency_ms=15 + i)
report = ea.get_report()
test("total_trades == 20", report.total_trades == 20)
test("avg_slippage >= 0", report.avg_slippage_bps >= 0)

# 9. Advanced Slippage
print("\n=== ADVANCED SLIPPAGE MODEL ===")
s_market = compute_slippage_advanced(50000, 5000, spread_bps=8, book_depth_usd=100000,
                                      atr_bps=20, obi_against=0.5, vpin=0.6, is_market_order=True)
s_limit = compute_slippage_advanced(50000, 5000, spread_bps=8, book_depth_usd=100000,
                                     atr_bps=20, obi_against=0, vpin=0.2, is_market_order=False)
test("market+adverse > limit+favorable", s_market > s_limit)

# 10. Full import
print("\n=== INTEGRATION ===")
try:
    from main import BotStrike
    test("main.py imports OK", True)
except Exception as e:
    test("main.py imports OK", False, str(e))

# Run existing tests
print("\n=== EXISTING TESTS (regression check) ===")
import subprocess
r1 = subprocess.run([sys.executable, "tests/test_core_functional.py"], capture_output=True, text=True, timeout=30)
core_ok = "PASSED: 21" in r1.stdout
test("core functional: 21/21", core_ok, r1.stdout[-100:] if not core_ok else "")

r2 = subprocess.run([sys.executable, "tests/test_strategies_functional.py"], capture_output=True, text=True, timeout=30)
strat_ok = "15/15" in r2.stdout
test("strategy functional: 15/15", strat_ok, r2.stdout[-100:] if not strat_ok else "")

r3 = subprocess.run([sys.executable, "tests/test_bug_fixes.py"], capture_output=True, text=True, timeout=30)
bug_ok = "PASSED: 52" in r3.stdout
test("bug fixes: 52/52", bug_ok, r3.stdout[-100:] if not bug_ok else "")

# Summary
print()
print("=" * 60)
print(f"RESULTS: {passed}/{passed+failed} passed, {failed} failed")
if failed == 0:
    print("ALL TESTS PASSED!")
else:
    print(f"{failed} FAILURES")
print("=" * 60)
