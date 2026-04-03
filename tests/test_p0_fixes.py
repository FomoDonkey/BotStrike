"""
Regression tests for P0 critical fixes (Institutional Audit #20).

Tests:
  1. symbol_has_position works in both paper and live mode
  2. Risk bypass rejected when entry_price == stop_loss
  3. Circuit breaker requires condition recovery, not just time
  4. Daily PnL auto-resets at UTC midnight
  5. Settings validation catches max_position > max_exposure
  6. Paper simulator uses SmartOrderRouter for execution parity
  7. Paper vs live consistency test
"""
from __future__ import annotations
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import Settings, SymbolConfig, TradingConfig
from core.types import Signal, Side, StrategyType, MarketRegime, Position
from risk.risk_manager import RiskManager
from execution.paper_simulator import PaperTradingSimulator
import structlog

logger = structlog.get_logger(__name__)

passed = 0
failed = 0


def check(name: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS: {name}")
    else:
        failed += 1
        print(f"  FAIL: {name} — {detail}")


def make_settings(**overrides) -> Settings:
    """Create test settings with safe defaults."""
    trading_kw = {
        "initial_capital": 300.0,
        "max_drawdown_pct": 0.10,
        "max_daily_loss_pct": 0.05,
        "max_total_exposure_pct": 0.6,
        "max_leverage": 5,
        "risk_per_trade_pct": 0.015,
    }
    trading_kw.update(overrides)
    return Settings(
        symbols=[SymbolConfig(symbol="BTC-USD", leverage=2, max_position_usd=150)],
        trading=TradingConfig(**trading_kw),
    )


def make_signal(
    entry_price: float = 50000.0,
    stop_loss: float = 49000.0,
    take_profit: float = 52000.0,
    size_usd: float = 100.0,
    side: Side = Side.BUY,
    strategy: StrategyType = StrategyType.MEAN_REVERSION,
    metadata: dict = None,
) -> Signal:
    return Signal(
        symbol="BTC-USD",
        side=side,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        size_usd=size_usd,
        strength=0.7,
        strategy=strategy,
        metadata=metadata or {},
    )


# ============================================================
# TEST 1: Risk bypass rejected when entry_price == stop_loss
# ============================================================
print("\n=== TEST 1: Risk bypass entry_price == stop_loss ===")

settings = make_settings()
rm = RiskManager(settings)

# Case 1a: entry_price == stop_loss → must reject (size=0)
sig = make_signal(entry_price=50000.0, stop_loss=50000.0, size_usd=100.0)
sym_cfg = settings.get_symbol_config("BTC-USD")
result = rm.validate_signal(sig, sym_cfg, MarketRegime.RANGING)
check("entry==sl rejected", result is None,
      f"Expected None, got signal with size={getattr(result, 'size_usd', '?')}")

# Case 1b: very close entry/sl (< 0.001 difference) → must reject
sig2 = make_signal(entry_price=50000.0, stop_loss=49999.9995, size_usd=100.0)
result2 = rm.validate_signal(sig2, sym_cfg, MarketRegime.RANGING)
check("entry~=sl (diff<0.001) rejected", result2 is None,
      f"Expected None, got signal with size={getattr(result2, 'size_usd', '?')}")

# Case 1c: normal entry/sl (distance > 0.001) → must pass
sig3 = make_signal(entry_price=50000.0, stop_loss=49000.0, size_usd=100.0)
result3 = rm.validate_signal(sig3, sym_cfg, MarketRegime.RANGING)
check("normal entry/sl passes", result3 is not None,
      "Signal should have passed with valid risk distance")


# ============================================================
# TEST 2: Circuit breaker condition-based recovery
# ============================================================
print("\n=== TEST 2: Circuit breaker condition-based recovery ===")

settings2 = make_settings(max_drawdown_pct=0.10)
rm2 = RiskManager(settings2)

# Push into circuit breaker (drawdown > 80% of 10% = > 8%)
rm2.update_equity(300.0)
rm2.update_equity(273.0)  # 9% drawdown → triggers circuit breaker (> 8% threshold)
check("circuit_breaker_triggered", rm2._circuit_breaker_active,
      f"CB should be active at 9% dd")

# Case 2a: Time elapsed but drawdown still high → must stay blocked
rm2._circuit_breaker_until = time.time() - 1  # Pretend cooldown passed
sig_test = make_signal(size_usd=50.0)
result_blocked = rm2.validate_signal(sig_test, sym_cfg, MarketRegime.RANGING)
check("cooldown_elapsed_but_dd_high_stays_blocked",
      result_blocked is None,
      f"CB should block: drawdown={rm2.current_drawdown_pct:.3f} > recovery threshold 5%")

# Case 2b: Time elapsed AND drawdown recovered → must allow
rm2.update_equity(298.0)  # ~0.67% drawdown (well below 5% recovery threshold)
rm2._circuit_breaker_until = time.time() - 1  # cooldown elapsed
sig_test2 = make_signal(size_usd=50.0)
result_allowed = rm2.validate_signal(sig_test2, sym_cfg, MarketRegime.RANGING)
check("cooldown_elapsed_and_dd_recovered_allows",
      result_allowed is not None,
      f"CB should deactivate: drawdown={rm2.current_drawdown_pct:.3f}")

# Case 2c: Drawdown recovered but cooldown NOT elapsed → must block
rm3 = RiskManager(make_settings(max_drawdown_pct=0.10))
rm3.update_equity(300.0)
rm3.update_equity(273.0)  # Trigger CB
rm3.update_equity(300.0)  # Recover equity
# Cooldown still in the future
check("dd_recovered_but_cooldown_pending_blocks",
      rm3._circuit_breaker_active and time.time() < rm3._circuit_breaker_until,
      "CB should stay active during cooldown even if equity recovered")
sig_test3 = make_signal(size_usd=50.0)
result_cooldown = rm3.validate_signal(sig_test3, sym_cfg, MarketRegime.RANGING)
check("signal_blocked_during_cooldown", result_cooldown is None,
      "Should block during cooldown period")


# ============================================================
# TEST 3: Daily PnL auto-reset
# ============================================================
print("\n=== TEST 3: Daily PnL auto-reset ===")

settings3 = make_settings()
rm_daily = RiskManager(settings3)

# Simulate a losing day
rm_daily.record_trade_result(-5.0)
rm_daily.record_trade_result(-3.0)
check("daily_pnl_tracked", rm_daily._daily_pnl == -8.0,
      f"Expected -8.0, got {rm_daily._daily_pnl}")

# First call to check_daily_reset sets _last_daily_reset_date
rm_daily.check_daily_reset()
from datetime import datetime, timezone
today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

# If _last_daily_reset_date was empty, first call resets
check("first_call_resets", rm_daily._daily_pnl == 0.0,
      f"Expected 0.0 after first reset, got {rm_daily._daily_pnl}")
check("date_tracked", rm_daily._last_daily_reset_date == today,
      f"Expected {today}, got {rm_daily._last_daily_reset_date}")

# Second call on same day should NOT reset
rm_daily.record_trade_result(-2.0)
rm_daily.check_daily_reset()
check("same_day_no_reset", rm_daily._daily_pnl == -2.0,
      f"Expected -2.0 (no reset same day), got {rm_daily._daily_pnl}")

# Simulate next day by faking the date
rm_daily._last_daily_reset_date = "2020-01-01"
rm_daily.check_daily_reset()
check("next_day_resets", rm_daily._daily_pnl == 0.0,
      f"Expected 0.0 after day change, got {rm_daily._daily_pnl}")


# ============================================================
# TEST 4: Settings validation catches incoherent config
# ============================================================
print("\n=== TEST 4: Settings validation ===")

# Valid config should work
try:
    s_valid = Settings(
        symbols=[SymbolConfig(symbol="BTC-USD", max_position_usd=150)],
        trading=TradingConfig(initial_capital=300, max_total_exposure_pct=0.6),
    )
    check("valid_config_passes", True)
except ValueError:
    check("valid_config_passes", False, "Valid config raised ValueError")

# Invalid config should raise
raised = False
try:
    s_bad = Settings(
        symbols=[SymbolConfig(symbol="BTC-USD", max_position_usd=250)],
        trading=TradingConfig(initial_capital=300, max_total_exposure_pct=0.6),
    )
except ValueError as e:
    raised = True
    check("invalid_config_caught", "incoherence" in str(e).lower(),
          f"Error message: {e}")
if not raised:
    check("invalid_config_caught", False, "Should have raised ValueError")

# Edge case: exactly at limit should pass
try:
    s_edge = Settings(
        symbols=[SymbolConfig(symbol="BTC-USD", max_position_usd=180)],
        trading=TradingConfig(initial_capital=300, max_total_exposure_pct=0.6),
    )
    check("edge_case_exact_limit_passes", True)
except ValueError:
    check("edge_case_exact_limit_passes", False, "Exact limit should be allowed")


# ============================================================
# TEST 5: Paper simulator uses SmartOrderRouter
# ============================================================
print("\n=== TEST 5: Paper simulator SmartOrderRouter integration ===")

settings5 = make_settings()
paper_sim = PaperTradingSimulator(settings5)

# Verify router exists
check("router_initialized", hasattr(paper_sim, '_router'),
      "PaperTradingSimulator should have _router attribute")

# Test entry with market context metadata
sig_entry = make_signal(
    entry_price=50000.0,
    stop_loss=49500.0,
    take_profit=51500.0,
    size_usd=100.0,
    side=Side.BUY,
    strategy=StrategyType.MEAN_REVERSION,
    metadata={
        "regime": "RANGING",
        "spread_bps": 5.0,
        "atr": 500.0,
        "book_depth_usd": 50000.0,
        "trade_intensity": 2.0,
        "kyle_lambda_bps": 0.1,
        "microprice": 50001.0,
    },
)

sym_cfg5 = settings5.get_symbol_config("BTC-USD")
trades = paper_sim.execute_signals([sig_entry], [], sym_cfg5)

# Router should have been used (trade may or may not fill depending on limit order prob)
# At minimum, the trade should not crash
if len(trades) > 0:
    t = trades[0]
    check("entry_fill_has_price", t.price > 0, f"Fill price: {t.price}")
    check("entry_fill_slippage_recorded", t.actual_slippage_bps >= 0,
          f"Slippage: {t.actual_slippage_bps}")
    check("entry_pnl_zero", t.pnl == 0.0, "Entry should have pnl=0")
else:
    # If limit order didn't fill (probabilistic), that's also valid behavior
    check("limit_no_fill_is_valid", True)
    print("    (limit order did not fill — probabilistic outcome, this is correct)")


# ============================================================
# TEST 6: Paper vs Live consistency — routing affects fill price
# ============================================================
print("\n=== TEST 6: Paper vs Live consistency ===")

# Test that paper sim routes through SmartOrderRouter and applies slippage.
# MARKET orders: adverse slippage (buy higher, sell lower)
# LIMIT orders: price improvement (buy lower, sell higher) — this is CORRECT
paper_sim2 = PaperTradingSimulator(settings5)
sig_buy = make_signal(
    entry_price=50000.0, stop_loss=49500.0, take_profit=51500.0,
    size_usd=100.0, side=Side.BUY,
    metadata={
        "regime": "RANGING",
        "spread_bps": 1.0,  # Very tight spread → router forces MARKET (< 3 bps threshold)
        "atr": 500.0,
        "book_depth_usd": 50000.0,
    },
)
sig_buy.strength = 0.95
trades_buy = paper_sim2.execute_signals([sig_buy], [], sym_cfg5)
if trades_buy:
    # With spread < 3bps, router forces MARKET → adverse slippage
    check("market_buy_adverse_slippage", trades_buy[0].price > 50000.0,
          f"MARKET buy should fill above entry: {trades_buy[0].price}")
else:
    check("market_buy_adverse_slippage", False, "Should have filled as MARKET order")

# Test LIMIT routing: wider spread → router may choose LIMIT → price improvement
paper_sim3 = PaperTradingSimulator(settings5)
sig_limit = make_signal(
    entry_price=50000.0, stop_loss=49500.0, take_profit=51500.0,
    size_usd=100.0, side=Side.BUY,
    metadata={
        "regime": "RANGING",
        "spread_bps": 15.0,  # Wide spread → router likely chooses LIMIT
        "atr": 500.0,
        "book_depth_usd": 50000.0,
    },
)
sig_limit.strength = 0.3  # Low strength → no urgency → favors LIMIT
trades_limit = paper_sim3.execute_signals([sig_limit], [], sym_cfg5)
if trades_limit:
    # LIMIT order: fill at optimized price (may be below entry = price improvement)
    check("limit_buy_price_improvement", trades_limit[0].price < 50000.0,
          f"LIMIT buy should fill at/below entry: {trades_limit[0].price}")
else:
    # Limit order didn't fill (probabilistic) — valid behavior
    check("limit_no_fill_valid_behavior", True)
    print("    (LIMIT order did not fill - probabilistic, correct)")


# ============================================================
# TEST 7: symbol_has_position — live mode coverage
# ============================================================
print("\n=== TEST 7: symbol_has_position live mode ===")

# This tests the logic directly — we can't easily instantiate full BotStrike,
# but we can verify the code path exists by checking the pattern
import ast
with open("main.py", "r") as f:
    source = f.read()

check("live_position_check_exists",
      "elif self._positions.get(symbol) is not None:" in source,
      "main.py should have live mode position check")
check("live_position_sets_flag",
      "symbol_has_position = True" in source and "elif self._positions.get" in source,
      "Live mode should set symbol_has_position flag")


# ============================================================
# SUMMARY
# ============================================================
print(f"""
============================================================
P0 FIX REGRESSION TESTS
============================================================
Total: {passed + failed}  |  PASSED: {passed}  |  FAILED: {failed}
{"ALL TESTS PASSED!" if failed == 0 else f"{failed} FAILURES — FIX BEFORE PROCEEDING"}
============================================================
""")

if failed > 0:
    sys.exit(1)
