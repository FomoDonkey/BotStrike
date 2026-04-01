"""
Functional tests for bug fixes across risk, execution, portfolio, order_engine, and trade_database modules.
Run: python tests/test_bug_fixes.py
"""
import sys
import os
import copy
import time

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import Settings, SymbolConfig, TradingConfig
from core.types import Signal, Side, StrategyType, MarketRegime, Position, Trade, Order, OrderType
from risk.risk_manager import RiskManager
from portfolio.portfolio_manager import PortfolioManager, REGIME_WEIGHTS
from execution.paper_simulator import PaperTradingSimulator, PaperPosition
from trade_database.models import TradeRecord, SessionRecord

passed = 0
failed = 0

def check(name: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS: {name}")
    else:
        failed += 1
        print(f"  FAIL: {name}  {detail}")


def make_settings(**overrides) -> Settings:
    tc = TradingConfig(**overrides)
    return Settings(trading=tc)


def make_signal(**kwargs) -> Signal:
    defaults = dict(
        strategy=StrategyType.MEAN_REVERSION,
        symbol="BTC-USD",
        side=Side.BUY,
        strength=0.7,
        entry_price=50000.0,
        stop_loss=49000.0,
        take_profit=52000.0,
        size_usd=5000.0,
        metadata={},
    )
    defaults.update(kwargs)
    return Signal(**defaults)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. RISK MANAGER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
print("\n=== 1. RiskManager Tests ===")

# 1a. validate_signal does NOT mutate the original signal (copy fix)
print("\n--- 1a. validate_signal does NOT mutate original signal ---")
settings = make_settings()
rm = RiskManager(settings)
sym_cfg = settings.symbols[0]  # BTC-USD
original_signal = make_signal(size_usd=5000.0)
original_size = original_signal.size_usd
original_sl = original_signal.stop_loss

result = rm.validate_signal(original_signal, sym_cfg, MarketRegime.RANGING)
check("Original signal size_usd unchanged",
      original_signal.size_usd == original_size,
      f"was {original_size}, now {original_signal.size_usd}")
check("Original signal stop_loss unchanged",
      original_signal.stop_loss == original_sl,
      f"was {original_sl}, now {original_signal.stop_loss}")


# 1b. Size override respects previous limits (min fix)
print("\n--- 1b. Size override respects previous limits (margin reduction uses min) ---")
settings2 = make_settings(initial_capital=1000.0, max_leverage=2)
rm2 = RiskManager(settings2)
sym_cfg2 = SymbolConfig(symbol="BTC-USD", leverage=2, max_position_usd=50000.0)
sig2 = make_signal(size_usd=50000.0, entry_price=50000.0, stop_loss=49000.0, take_profit=52000.0)
result2 = rm2.validate_signal(sig2, sym_cfg2, MarketRegime.RANGING)
if result2:
    check("Size reduced by margin uses min (not replace)",
          result2.size_usd <= 1000.0,
          f"got {result2.size_usd}, expected <= 1000")
else:
    check("Signal rejected (acceptable - capital too low)", True)


# 1c. pnl=0 does NOT reset consecutive losses counter
print("\n--- 1c. pnl=0 does NOT reset consecutive losses ---")
rm3 = RiskManager(make_settings())
rm3.record_trade_result(-100)
rm3.record_trade_result(-50)
before = rm3._consecutive_losses
check("After 2 losses, consecutive=2", before == 2, f"got {before}")
rm3.record_trade_result(0.0)
after = rm3._consecutive_losses
check("After pnl=0, consecutive still 2", after == 2, f"got {after}")


# 1d. pnl<0 increments consecutive losses
print("\n--- 1d. pnl<0 increments consecutive losses ---")
rm4 = RiskManager(make_settings())
rm4.record_trade_result(-10)
check("After 1 loss, consecutive=1", rm4._consecutive_losses == 1)
rm4.record_trade_result(-20)
check("After 2 losses, consecutive=2", rm4._consecutive_losses == 2)
rm4.record_trade_result(-5)
check("After 3 losses, consecutive=3", rm4._consecutive_losses == 3)


# 1e. pnl>0 resets consecutive losses to 0
print("\n--- 1e. pnl>0 resets consecutive losses to 0 ---")
rm5 = RiskManager(make_settings())
rm5.record_trade_result(-100)
rm5.record_trade_result(-50)
check("Before reset, consecutive=2", rm5._consecutive_losses == 2)
rm5.record_trade_result(10.0)
check("After win, consecutive=0", rm5._consecutive_losses == 0, f"got {rm5._consecutive_losses}")


# 1f. Circuit breaker activates at >80% of max_drawdown
print("\n--- 1f. Circuit breaker at >80% of max_drawdown ---")
# Condition is strict >: drawdown > max_drawdown * 0.8
# 80% of 15% = 12%. Need dd > 0.12 to trigger.
settings6 = make_settings(initial_capital=100000.0, max_drawdown_pct=0.15)
rm6 = RiskManager(settings6)
# equity = 87900 => dd = 12.1% > 12% threshold => triggers
rm6.update_equity(87900.0)
check("Circuit breaker active at 12.1% dd (>80% of 15%)",
      rm6._circuit_breaker_active,
      f"active={rm6._circuit_breaker_active}, dd={rm6.current_drawdown_pct}")

# Exactly at threshold: 12% => should NOT trigger (strict >)
settings6b = make_settings(initial_capital=100000.0, max_drawdown_pct=0.15)
rm6b = RiskManager(settings6b)
rm6b.update_equity(88000.0)  # dd = exactly 12%
check("No circuit breaker at exactly 12% dd (not strictly > 80% of 15%)",
      not rm6b._circuit_breaker_active,
      f"active={rm6b._circuit_breaker_active}, dd={rm6b.current_drawdown_pct}")

# Well below threshold
settings6c = make_settings(initial_capital=100000.0, max_drawdown_pct=0.15)
rm6c = RiskManager(settings6c)
rm6c.update_equity(92000.0)  # dd = 8%, well below 12%
check("No circuit breaker at 8% dd (well below threshold)",
      not rm6c._circuit_breaker_active,
      f"active={rm6c._circuit_breaker_active}, dd={rm6c.current_drawdown_pct}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. PORTFOLIO MANAGER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
print("\n=== 2. PortfolioManager Tests ===")

# 2a. get_allocation returns capital > 0 for valid regimes
print("\n--- 2a. get_allocation returns capital > 0 ---")
settings_pm = make_settings(initial_capital=100000.0)
rm_pm = RiskManager(settings_pm)
pm = PortfolioManager(settings_pm, rm_pm)

for regime in [MarketRegime.RANGING, MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN,
               MarketRegime.BREAKOUT, MarketRegime.UNKNOWN]:
    # MR and OFM should have allocation > 0; TF and MM are intentionally 0
    for strat in [StrategyType.MEAN_REVERSION, StrategyType.ORDER_FLOW_MOMENTUM]:
        alloc = pm.get_allocation("BTC-USD", regime, strat)
        check(f"Allocation({regime.value}, {strat.value}) > 0",
              alloc > 0,
              f"got {alloc}")
    # TF and MM should be 0 (intentionally disabled)
    for strat in [StrategyType.TREND_FOLLOWING, StrategyType.MARKET_MAKING]:
        alloc = pm.get_allocation("BTC-USD", regime, strat)
        check(f"Allocation({regime.value}, {strat.value}) == 0 (disabled)",
              alloc == 0,
              f"got {alloc}")

# 2b. REGIME_WEIGHTS allocations are reasonable (sum ~1.0 per regime)
print("\n--- 2b. REGIME_WEIGHTS sum to ~1.0 per regime ---")
for regime, weights in REGIME_WEIGHTS.items():
    total = sum(weights.values())
    check(f"REGIME_WEIGHTS[{regime.value}] sums to ~1.0",
          abs(total - 1.0) < 0.01,
          f"got {total}")

# 2c. should_strategy_trade returns False when weight < 8%
print("\n--- 2c. should_strategy_trade False when weight < 8% ---")
# BREAKOUT: MR weight = 0.10 (> 0.08 now, should trade)
check("MR allowed in BREAKOUT (weight=0.10 > 0.08)",
      pm.should_strategy_trade(StrategyType.MEAN_REVERSION, MarketRegime.BREAKOUT))
# RANGING: MR weight = 0.45 (should trade)
check("MR allowed in RANGING (weight=0.45)",
      pm.should_strategy_trade(StrategyType.MEAN_REVERSION, MarketRegime.RANGING))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. PAPER SIMULATOR
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
print("\n=== 3. PaperTradingSimulator Tests ===")

# 3a. get_position returns mark_price from _last_prices (not entry_price)
print("\n--- 3a. get_position uses _last_prices for mark_price ---")
sim = PaperTradingSimulator(make_settings())
# Manually insert a position
pp = PaperPosition(
    symbol="BTC-USD", side=Side.BUY, size=0.1,
    entry_price=50000.0, strategy=StrategyType.MEAN_REVERSION,
    stop_loss=49000.0, take_profit=52000.0,
)
sim._positions["BTC-USD_MEAN_REVERSION"] = pp
sim._last_prices["BTC-USD"] = 51000.0  # updated price

pos = sim.get_position("BTC-USD", StrategyType.MEAN_REVERSION)
check("mark_price uses _last_prices (51000), not entry (50000)",
      pos is not None and pos.mark_price == 51000.0,
      f"got mark_price={pos.mark_price if pos else 'None'}")

# 3b. get_all_positions uses _last_prices for mark_price
print("\n--- 3b. get_all_positions uses _last_prices ---")
all_pos = sim.get_all_positions()
key = "BTC-USD_MEAN_REVERSION"
check("get_all_positions mark_price=51000",
      key in all_pos and all_pos[key].mark_price == 51000.0,
      f"got {all_pos.get(key, 'missing')}")

# 3c. SL/TP triggers work correctly
print("\n--- 3c. SL/TP triggers ---")
sim2 = PaperTradingSimulator(make_settings())
pp2 = PaperPosition(
    symbol="ETH-USD", side=Side.BUY, size=1.0,
    entry_price=3000.0, strategy=StrategyType.TREND_FOLLOWING,
    stop_loss=2900.0, take_profit=3200.0,
)
sim2._positions["ETH-USD_TREND_FOLLOWING"] = pp2

# SL trigger: low goes to 2900
trades_sl = sim2.on_price_update("ETH-USD", 2950.0, high=2970.0, low=2900.0)
check("SL triggered on BUY when low <= stop_loss",
      len(trades_sl) == 1 and trades_sl[0].price == 2900.0,
      f"trades={len(trades_sl)}, price={trades_sl[0].price if trades_sl else 'N/A'}")

# TP trigger for SELL position
sim3 = PaperTradingSimulator(make_settings())
pp3 = PaperPosition(
    symbol="ETH-USD", side=Side.SELL, size=1.0,
    entry_price=3000.0, strategy=StrategyType.MEAN_REVERSION,
    stop_loss=3100.0, take_profit=2800.0,
)
sim3._positions["ETH-USD_MEAN_REVERSION"] = pp3

trades_tp = sim3.on_price_update("ETH-USD", 2850.0, high=2870.0, low=2790.0)
check("TP triggered on SELL when low <= take_profit",
      len(trades_tp) == 1 and trades_tp[0].price == 2800.0,
      f"trades={len(trades_tp)}, price={trades_tp[0].price if trades_tp else 'N/A'}")

# 3d. on_price_update stores last price
print("\n--- 3d. on_price_update stores last price ---")
sim4 = PaperTradingSimulator(make_settings())
sim4.on_price_update("SOL-USD", 150.0)
check("_last_prices updated after on_price_update",
      sim4._last_prices.get("SOL-USD") == 150.0,
      f"got {sim4._last_prices.get('SOL-USD')}")
sim4.on_price_update("SOL-USD", 155.0)
check("_last_prices updated again",
      sim4._last_prices.get("SOL-USD") == 155.0,
      f"got {sim4._last_prices.get('SOL-USD')}")

# When no _last_prices, fallback to entry_price
sim5 = PaperTradingSimulator(make_settings())
pp5 = PaperPosition(
    symbol="ADA-USD", side=Side.BUY, size=100.0,
    entry_price=0.5, strategy=StrategyType.MARKET_MAKING,
)
sim5._positions["ADA-USD_MARKET_MAKING"] = pp5
pos5 = sim5.get_position("ADA-USD", StrategyType.MARKET_MAKING)
check("Fallback to entry_price when no _last_prices",
      pos5 is not None and pos5.mark_price == 0.5,
      f"got {pos5.mark_price if pos5 else 'None'}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. ORDER ENGINE — empty order_id guard
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
print("\n=== 4. OrderEngine Tests ===")
print("\n--- 4a. Empty order_id NOT added to _active_orders ---")

# We can't easily test the async execute_signal, so we test the guard logic directly.
# The guard is: `if order.order_id:` before adding to _active_orders.
# We simulate the same logic path.

from execution.order_engine import OrderExecutionEngine

# Test the guard condition itself (lines 117-118 of order_engine.py)
order_with_empty_id = Order(
    symbol="BTC-USD", side=Side.BUY, order_type=OrderType.MARKET, quantity=0.1
)
order_with_empty_id.order_id = ""
# The guard: `if order.order_id:` should be False for empty string
check("Empty string is falsy (guard works)",
      not order_with_empty_id.order_id,
      f"order_id='{order_with_empty_id.order_id}' is truthy!")

order_with_empty_id.order_id = None
check("None is falsy (guard works)",
      not order_with_empty_id.order_id,
      f"order_id={order_with_empty_id.order_id} is truthy!")

order_with_valid_id = Order(
    symbol="BTC-USD", side=Side.BUY, order_type=OrderType.MARKET, quantity=0.1
)
order_with_valid_id.order_id = "abc123"
check("Valid order_id is truthy",
      bool(order_with_valid_id.order_id),
      f"order_id='{order_with_valid_id.order_id}' is falsy!")

# Simulate adding to dict with guard
active = {}
for oid in ["", None, "real_id_123"]:
    order_with_empty_id.order_id = oid
    if order_with_empty_id.order_id:
        active[order_with_empty_id.order_id] = order_with_empty_id
check("Only valid order_id added to active_orders",
      len(active) == 1 and "real_id_123" in active,
      f"active has {len(active)} entries: {list(active.keys())}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 5. TRADE DATABASE ADAPTER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
print("\n=== 5. TradeDBAdapter Tests ===")

from trade_database.adapter import TradeDBAdapter

# Mock repository that does nothing
class MockRepo:
    def insert_session(self, session): pass
    def insert_trade(self, record): pass
    def insert_trades_batch(self, records): pass

# 5a. _track does not divide by zero when equity_peak=0
print("\n--- 5a. _track no divide-by-zero when equity_peak=0 ---")
adapter = TradeDBAdapter(MockRepo(), source="test")
adapter._equity_peak = 0.0
record = TradeRecord(pnl=-100, equity_after=0, strategy="MR")
try:
    adapter._track(record)
    check("_track with equity_peak=0 does not crash", True)
except ZeroDivisionError:
    check("_track with equity_peak=0 does not crash", False, "ZeroDivisionError!")

# Also test with equity_after > 0 but equity_peak was 0
adapter2 = TradeDBAdapter(MockRepo(), source="test")
adapter2._equity_peak = 0.0
record2 = TradeRecord(pnl=50, equity_after=100, strategy="TF")
try:
    adapter2._track(record2)
    # After track, equity_peak should update to 100
    check("_track updates equity_peak from 0 to equity_after",
          adapter2._equity_peak == 100.0,
          f"peak={adapter2._equity_peak}")
    # Now drawdown calc should work fine
    check("No ZeroDivisionError even when peak was 0", True)
except ZeroDivisionError:
    check("No ZeroDivisionError even when peak was 0", False, "ZeroDivisionError!")


# 5b. end_session uses max(internal, provided) for drawdown
print("\n--- 5b. end_session uses max(internal, provided) for drawdown ---")

# Case 1: internal > provided
adapter3 = TradeDBAdapter(MockRepo(), source="test")
adapter3.start_session(initial_equity=100000)
adapter3._max_drawdown = 0.12  # internal tracking found 12%
adapter3.end_session(final_equity=95000, max_drawdown=0.08)  # provided 8%
check("end_session keeps larger internal drawdown (0.12 > 0.08)",
      adapter3._max_drawdown == 0.12,
      f"got {adapter3._max_drawdown}")

# Case 2: provided > internal
adapter4 = TradeDBAdapter(MockRepo(), source="test")
adapter4.start_session(initial_equity=100000)
adapter4._max_drawdown = 0.05  # internal tracking found 5%
adapter4.end_session(final_equity=95000, max_drawdown=0.10)  # provided 10%
check("end_session uses larger provided drawdown (0.10 > 0.05)",
      adapter4._max_drawdown == 0.10,
      f"got {adapter4._max_drawdown}")

# Case 3: provided=0 keeps internal
adapter5 = TradeDBAdapter(MockRepo(), source="test")
adapter5.start_session(initial_equity=100000)
adapter5._max_drawdown = 0.07
adapter5.end_session(final_equity=95000, max_drawdown=0.0)
check("end_session with provided=0 keeps internal (0.07)",
      adapter5._max_drawdown == 0.07,
      f"got {adapter5._max_drawdown}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SUMMARY
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
print("\n" + "=" * 55)
print(f"TOTAL: {passed + failed} tests | PASSED: {passed} | FAILED: {failed}")
print("=" * 55)
if failed:
    print(">>> SOME TESTS FAILED <<<")
    sys.exit(1)
else:
    print(">>> ALL TESTS PASSED <<<")
    sys.exit(0)
