"""Test that paper trading exit signals work (Bug #1 fix verification)."""
import sys, os, logging
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
logging.disable(logging.CRITICAL)
import structlog
structlog.configure(wrapper_class=structlog.BoundLogger, logger_factory=structlog.ReturnLoggerFactory())

from config.settings import Settings
from core.types import Signal, Side, StrategyType
from execution.paper_simulator import PaperTradingSimulator

settings = Settings()
sim = PaperTradingSimulator(settings)
sym = settings.get_symbol_config("BTC-USD")

print("=== Paper Trading Exit Signal Tests ===")

# 1. Open MR position
entry = Signal(strategy=StrategyType.MEAN_REVERSION, symbol="BTC-USD", side=Side.BUY,
               strength=0.7, entry_price=50000, stop_loss=49500, take_profit=51000, size_usd=5000)
fills = sim.execute_signals([entry], [], sym)
assert len(fills) == 1
pos = sim.get_position("BTC-USD", StrategyType.MEAN_REVERSION)
assert pos is not None, "Position should exist after entry"
print(f"1. Entry: position exists, size={pos.size:.6f}")

# 2. Verify get_position returns valid Position for strategy to see
assert pos.side == Side.BUY
assert pos.size > 0
assert pos.entry_price > 0
print(f"2. Position visible: side={pos.side.value}, entry={pos.entry_price:.2f}")

# 3. Send exit signal (like MR would generate when zscore crosses back)
exit_sig = Signal(strategy=StrategyType.MEAN_REVERSION, symbol="BTC-USD", side=Side.SELL,
                  strength=0.8, entry_price=50200, stop_loss=50200, take_profit=50200, size_usd=5000,
                  metadata={"action": "exit_mean_reversion", "zscore": 0.3})
exit_fills = sim.execute_signals([exit_sig], [], sym)
assert len(exit_fills) == 1, f"Exit signal should produce 1 fill, got {len(exit_fills)}"
assert exit_fills[0].pnl != 0, "Exit should have non-zero PnL"
print(f"3. MR exit: PnL=${exit_fills[0].pnl:.2f}, fee=${exit_fills[0].fee:.4f}")

# 4. Position should be gone
pos2 = sim.get_position("BTC-USD", StrategyType.MEAN_REVERSION)
assert pos2 is None, "Position should be closed after exit"
print(f"4. Position closed: OK")

# 5. Open TF position and test trailing stop exit
tf_entry = Signal(strategy=StrategyType.TREND_FOLLOWING, symbol="ETH-USD", side=Side.SELL,
                  strength=0.6, entry_price=3000, stop_loss=3100, take_profit=2800, size_usd=3000)
sym_eth = settings.get_symbol_config("ETH-USD")
fills2 = sim.execute_signals([tf_entry], [], sym_eth)
assert len(fills2) == 1

tf_exit = Signal(strategy=StrategyType.TREND_FOLLOWING, symbol="ETH-USD", side=Side.BUY,
                 strength=1.0, entry_price=2950, stop_loss=2950, take_profit=2950, size_usd=3000,
                 metadata={"action": "trailing_stop_hit", "stop_price": 2950})
tf_fills = sim.execute_signals([tf_exit], [], sym_eth)
assert len(tf_fills) == 1
print(f"5. TF trailing stop exit: PnL=${tf_fills[0].pnl:.2f}")

# 6. Verify fee is not double-counted
# Entry fee should be 0, exit fee should cover both sides
entry2 = Signal(strategy=StrategyType.MEAN_REVERSION, symbol="BTC-USD", side=Side.BUY,
                strength=0.7, entry_price=50000, stop_loss=49000, take_profit=51000, size_usd=10000)
fills3 = sim.execute_signals([entry2], [], sym)
assert fills3[0].fee == 0.0, f"Entry fee should be 0, got {fills3[0].fee}"
print(f"6. Entry fee=0: OK")

exit2 = Signal(strategy=StrategyType.MEAN_REVERSION, symbol="BTC-USD", side=Side.SELL,
               strength=0.8, entry_price=50500, stop_loss=50500, take_profit=50500, size_usd=10000,
               metadata={"action": "exit_mean_reversion"})
fills4 = sim.execute_signals([exit2], [], sym)
size = fills3[0].quantity
expected_fee = (fills3[0].price * size + 50500 * size) * settings.trading.taker_fee
actual_fee = fills4[0].fee
print(f"7. Exit fee=${actual_fee:.4f}, expected~=${expected_fee:.4f} (covers both sides)")
assert abs(actual_fee - expected_fee) < 0.01, f"Fee mismatch: {actual_fee} vs {expected_fee}"

print("\n=== ALL EXIT TESTS PASSED ===")
