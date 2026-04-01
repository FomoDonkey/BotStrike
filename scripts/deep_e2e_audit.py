"""Deep end-to-end coherence audit of the entire BotStrike system."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np, pandas as pd, json, tempfile

errors = []

print("=" * 70)
print("  DEEP E2E COHERENCE AUDIT")
print("=" * 70)

from config.settings import Settings
from core.types import MarketRegime, Side, StrategyType, Signal, Position, MarketSnapshot, OrderBook, OrderBookLevel
from core.indicators import Indicators
from core.microstructure import MicrostructureEngine
from strategies.mean_reversion import MeanReversionStrategy
from strategies.trend_following import TrendFollowingStrategy
from strategies.market_making import MarketMakingStrategy
from risk.risk_manager import RiskManager
from portfolio.portfolio_manager import PortfolioManager
from execution.paper_simulator import PaperTradingSimulator

settings = Settings()
rm = RiskManager(settings)
pm = PortfolioManager(settings, rm)
ps = PaperTradingSimulator(settings)
sc = settings.get_symbol_config("BTC-USD")
micro = MicrostructureEngine(symbols=["BTC-USD"], config=settings.get_microstructure_config())

# ═══════ 1. PIPELINE COHERENCE ═══════
print("\n[1] Signal -> Risk -> Paper Execution Pipeline")

np.random.seed(42)
prices = np.concatenate([50000 + np.random.randn(150) * 20, 50000 - np.arange(50) * 40])
df = pd.DataFrame({
    "timestamp": np.arange(200) * 60, "close": prices,
    "open": np.roll(prices, 1), "volume": np.random.uniform(50, 200, 200),
    "high": prices + abs(np.random.randn(200)) * 30,
    "low": prices - abs(np.random.randn(200)) * 30,
})
df.loc[0, "open"] = df.loc[0, "close"]
df = Indicators.compute_all(df, {"ema_fast": 12, "ema_slow": 26, "zscore_lookback": 100})

last_price = float(df["close"].iloc[-1])
snap = MarketSnapshot(symbol="BTC-USD", timestamp=0, price=last_price, mark_price=last_price,
                      index_price=last_price, funding_rate=0, volume_24h=0, open_interest=0)

for _, row in df.iterrows():
    micro.on_bar("BTC-USD", float(row["open"]), float(row["high"]), float(row["low"]),
                 float(row["close"]), float(row["volume"]), float(row["timestamp"]))
micro_snap = micro.get_snapshot("BTC-USD")

mr = MeanReversionStrategy(settings.trading)
alloc = pm.get_allocation("BTC-USD", MarketRegime.RANGING, StrategyType.MEAN_REVERSION)
mr_sigs = mr.generate_signals("BTC-USD", df, snap, MarketRegime.RANGING, sc, alloc, None, micro=micro_snap)
print(f"  MR signals generated: {len(mr_sigs)}")

validated = []
for sig in mr_sigs:
    v = rm.validate_signal(sig, sc, MarketRegime.RANGING, micro=micro_snap)
    if v:
        validated.append(v)
        if v.size_usd <= 0:
            errors.append(f"Validated signal with size_usd={v.size_usd}")
print(f"  Validated: {len(validated)}")

for v in validated:
    v.metadata["regime"] = "RANGING"
fills = ps.execute_signals(validated, [], sc)
print(f"  Paper fills: {len(fills)}")
for f in fills:
    if f.price <= 0:
        errors.append(f"Fill with price<=0")
print("  Pipeline coherence: OK" if not errors else f"  ERRORS: {errors}")

# ═══════ 2. BACKTEST ARITHMETIC ═══════
print("\n[2] Backtest Arithmetic Verification")

from backtesting.backtester import Backtester
bt = Backtester(settings)
bt_df = Backtester.generate_sample_data("BTC-USD", bars=3000, start_price=50000)
result = bt.run(bt_df, "BTC-USD")
s = result.summary()

trade_pnl_sum = sum(t["pnl"] for t in result.trades)
print(f"  Sum of trade PnLs: {trade_pnl_sum:.2f}")
print(f"  Reported net_pnl:  {s['net_pnl']:.2f}")
if abs(trade_pnl_sum - s["net_pnl"]) > 0.1:
    errors.append(f"PnL mismatch: sum={trade_pnl_sum:.2f} vs reported={s['net_pnl']:.2f}")
else:
    print("  PnL arithmetic: MATCH")

wins = sum(1 for t in result.trades if t["pnl"] > 0)
total = len(result.trades)
calc_wr = wins / total if total > 0 else 0
if abs(s["win_rate"] - calc_wr) > 0.0001:
    errors.append(f"Win rate mismatch: {s['win_rate']} vs {calc_wr}")
else:
    print(f"  Win rate consistency: OK ({s['win_rate']:.4f})")

neg_fees = [t for t in result.trades if t.get("fee", 0) < 0]
if neg_fees:
    errors.append(f"{len(neg_fees)} trades with negative fees")
else:
    print(f"  All fees >= 0: OK ({total} trades)")

zero_size = [t for t in result.trades if t.get("size", 0) <= 0]
if zero_size:
    errors.append(f"{len(zero_size)} trades with size <= 0")
else:
    print(f"  All sizes > 0: OK")

strat_pnl = sum(d["pnl"] for d in s.get("by_strategy", {}).values())
if abs(strat_pnl - s["net_pnl"]) > 1.0:
    errors.append(f"Strategy PnL sum ({strat_pnl:.2f}) != total ({s['net_pnl']:.2f})")
else:
    print(f"  Strategy PnL consistency: OK ({strat_pnl:.2f})")

# ═══════ 3. TRADE DB ROUND-TRIP ═══════
print("\n[3] Trade Database Round-Trip")

from trade_database.repository import TradeRepository
from trade_database.adapter import TradeDBAdapter

tmp = tempfile.mktemp(suffix=".db")
repo = TradeRepository(tmp)
adapter = TradeDBAdapter(repo, source="audit_test")
session_id = adapter.import_backtest_result(result, symbol="BTC-USD", initial_equity=100000)

trades_back = repo.get_trades(source="audit_test")
print(f"  Written: {len(result.trades)}, Read back: {len(trades_back)}")

db_pnl = sum(t.pnl for t in trades_back)
if abs(db_pnl - trade_pnl_sum) > 1.0:
    errors.append(f"DB PnL mismatch: {db_pnl:.2f} vs {trade_pnl_sum:.2f}")
else:
    print(f"  PnL round-trip: OK ({db_pnl:.2f})")

by_strat = repo.get_pnl_by_strategy(source="audit_test")
print(f"  DB strategies: {list(by_strat.keys())}")

os.unlink(tmp)

# ═══════ 4. TYPE CONSISTENCY ═══════
print("\n[4] Type Consistency")

pos = Position(symbol="BTC-USD", side=Side.BUY, size=0.1, entry_price=50000, mark_price=51000)
assert pos.notional == abs(0.1 * 51000), f"notional={pos.notional}"
assert abs(pos.pnl_pct - 0.02) < 0.001, f"pnl_pct={pos.pnl_pct}"
print(f"  Position math: OK (notional={pos.notional:.0f}, pnl_pct={pos.pnl_pct:.4f})")

ob = OrderBook(symbol="BTC-USD", timestamp=0, bids=[], asks=[])
assert ob.best_bid is None and ob.best_ask is None and ob.mid_price is None
print("  OrderBook empty: OK")

# ═══════ 5. JSON SERIALIZATION ═══════
print("\n[5] JSON Serialization")

for name, obj in [
    ("BacktestResult.summary()", s),
    ("RiskManager.get_risk_summary()", rm.get_risk_summary()),
    ("PortfolioManager.get_portfolio_summary()", pm.get_portfolio_summary()),
]:
    try:
        json.dumps(obj)
        print(f"  {name}: OK")
    except (TypeError, ValueError) as e:
        errors.append(f"{name} not JSON serializable: {e}")

from analytics.performance import PerformanceAnalyzer
from trade_database.models import TradeRecord
analyzer = PerformanceAnalyzer()
test_recs = [TradeRecord(pnl=10, timestamp=i*60, symbol="BTC", side="BUY", price=50000, quantity=0.1) for i in range(20)]
report = analyzer.analyze(test_recs)
try:
    json.dumps(report.to_dict())
    print("  PerformanceReport.to_dict(): OK")
except (TypeError, ValueError) as e:
    errors.append(f"PerformanceReport not serializable: {e}")

from core.market_data import MarketDataCollector
from core.regime_detector import RegimeDetector
from exchange.strike_client import StrikeClient
mdc = MarketDataCollector(settings, StrikeClient(settings), RegimeDetector())
try:
    json.dumps(mdc.get_tick_quality_stats())
    print("  TickQuality stats: OK")
except (TypeError, ValueError) as e:
    errors.append(f"TickQuality not serializable: {e}")

# ═══════ 6. MICROSTRUCTURE COHERENCE ═══════
print("\n[6] Microstructure Coherence")

ms = micro.get_snapshot("BTC-USD")
print(f"  VPIN: {ms.vpin.vpin:.4f}, toxic={ms.vpin.is_toxic}")
print(f"  Hawkes: intensity={ms.hawkes.intensity:.3f}, spike={ms.hawkes.is_spike}")
print(f"  Risk score: {ms.risk_score:.4f}")
print(f"  should_pause_mm: {ms.should_pause_mm}")
print(f"  should_filter_mr: {ms.should_filter_mr}")

# Verify risk_score formula
expected_vpin_score = min(ms.vpin.vpin / 0.8, 1.0)
expected_hawkes_score = max(0, min((ms.hawkes.spike_ratio - 1.0) / 3.0, 1.0))
expected_risk = max(expected_vpin_score, expected_hawkes_score)
if abs(ms.risk_score - expected_risk) > 0.001:
    errors.append(f"Risk score mismatch: {ms.risk_score} vs expected {expected_risk}")
else:
    print(f"  Risk score formula: OK")

# ═══════ SUMMARY ═══════
print("\n" + "=" * 70)
if errors:
    print(f"  AUDIT FOUND {len(errors)} ERRORS:")
    for e in errors:
        print(f"  [!] {e}")
    sys.exit(1)
else:
    print("  DEEP AUDIT COMPLETE: ALL CHECKS PASSED")
    print("  System is coherent, consistent, and correct.")
    print("=" * 70)
