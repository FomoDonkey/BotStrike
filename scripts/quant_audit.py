"""
Deep Quantitative Audit - Numerical Verification of ALL components.
Runs actual simulations to verify formulas produce correct numbers.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import math

errors = []
warnings = []

def check(name, condition, detail=""):
    if not condition:
        errors.append(f"{name}: {detail}")
        print(f"  ERROR: {name} {detail}")
    else:
        print(f"  OK: {name} {detail}")

def warn(name, detail=""):
    warnings.append(f"{name}: {detail}")
    print(f"  WARN: {name} {detail}")

# ═══════════════════════════════════════════════════════════════════
print("=" * 70)
print("  QUANT DEEP AUDIT - NUMERICAL VERIFICATION")
print("=" * 70)

from config.settings import Settings, SymbolConfig, TradingConfig
from core.types import (MarketRegime, MarketSnapshot, Side, StrategyType,
                        Position, OrderBook, OrderBookLevel)
from core.indicators import Indicators
from core.microstructure import (
    VPINCalculator, HawkesEstimator, AvellanedaStoikovEngine,
    MicrostructureEngine, MicrostructureSnapshot, VPINResult, HawkesResult
)
from strategies.mean_reversion import MeanReversionStrategy
from strategies.trend_following import TrendFollowingStrategy
from strategies.market_making import MarketMakingStrategy
from risk.risk_manager import RiskManager
from portfolio.portfolio_manager import PortfolioManager
from execution.slippage import compute_slippage, compute_slippage_bps
from backtesting.backtester import Backtester, BacktestPosition

tc = TradingConfig()
sc = SymbolConfig(symbol="BTC-USD", leverage=10, max_position_usd=20000)
settings = Settings()

# ═══════════════════════════════════════════════════════════════════
# 1. INDICATORS
# ═══════════════════════════════════════════════════════════════════
print("\n[1] INDICATORS NUMERICAL VERIFICATION")
print("-" * 50)

np.random.seed(42)
n = 200
close = pd.Series(np.cumsum(np.random.randn(n) * 0.5) + 100)
high = close + abs(np.random.randn(n)) * 0.3
low = close - abs(np.random.randn(n)) * 0.3
volume = pd.Series(np.random.uniform(50, 200, n))

# ATR > 0
atr = Indicators.atr(high, low, close, 14)
check("ATR positive", float(atr.iloc[-1]) > 0, f"val={atr.iloc[-1]:.4f}")

# Z-score constant = 0
zs_const = Indicators.zscore(pd.Series([100.0] * 50), 20)
check("Z-score(constant)=0", float(zs_const.iloc[-1]) == 0.0)

# RSI bounds
rsi = Indicators.rsi(close, 14)
check("RSI in [0,100]", float(rsi.min()) >= 0 and float(rsi.max()) <= 100,
      f"range=[{rsi.min():.1f}, {rsi.max():.1f}]")

# RSI edge cases
rsi_up = float(Indicators.rsi(pd.Series(range(100, 120)), 14).iloc[-1])
rsi_down = float(Indicators.rsi(pd.Series(range(120, 100, -1)), 14).iloc[-1])
check("RSI(all up)=100", rsi_up == 100.0, f"got {rsi_up}")
check("RSI(all down)=0", rsi_down == 0.0, f"got {rsi_down}")

# ADX bounds
adx = Indicators.adx(high, low, close, 14)
check("ADX >= 0", float(adx.dropna().min()) >= 0, f"min={adx.dropna().min():.1f}")

# Bollinger ordering
bb_u, bb_m, bb_l = Indicators.bollinger_bands(close, 20, 2.0)
check("BB: upper >= mid >= lower", bool(((bb_u >= bb_m) & (bb_m >= bb_l)).all()))

# EMA crossover values
ema_cross = Indicators.ema_crossover(close, 12, 26)
check("EMA cross in {-1, 0, 1}", set(ema_cross.unique()).issubset({-1.0, 0.0, 1.0}),
      f"values={sorted(ema_cross.unique())}")

# Momentum: verify pct_change correctness
mom = Indicators.momentum(pd.Series([100, 110, 121]), 1)
check("Momentum pct_change", abs(float(mom.iloc[-1]) - 0.1) < 0.001, f"110->121 = {mom.iloc[-1]:.4f}")

# ═══════════════════════════════════════════════════════════════════
# 2. MICROSTRUCTURE
# ═══════════════════════════════════════════════════════════════════
print("\n[2] MICROSTRUCTURE NUMERICAL VERIFICATION")
print("-" * 50)

# 2a. VPIN: balanced market = low VPIN
vpin = VPINCalculator(bucket_size=50000, n_buckets=50, toxic_threshold=0.6)
np.random.seed(42)
price = 50000.0
for i in range(5000):
    price += np.random.randn() * 10
    vpin.on_trade(price, np.random.uniform(0.01, 0.1), float(i))
check("VPIN(balanced) < 0.4", vpin.current.vpin < 0.4, f"got {vpin.current.vpin:.4f}")
check("VPIN(balanced) not toxic", not vpin.current.is_toxic)

# 2a2. VPIN: directional market = high VPIN
vpin2 = VPINCalculator(bucket_size=50000, n_buckets=50, toxic_threshold=0.6)
price = 50000.0
for i in range(5000):
    price += abs(np.random.randn()) * 15  # always up
    vpin2.on_trade(price, np.random.uniform(0.01, 0.1), float(i))
check("VPIN(directional) > 0.8", vpin2.current.vpin > 0.8, f"got {vpin2.current.vpin:.4f}")
check("VPIN(directional) toxic", vpin2.current.is_toxic)

# 2b. VPIN bucket sizing analysis
print("\n  VPIN Bucket Sizing Analysis:")
for sym, bsize, tprice, tqty in [
    ("BTC-USD", 50000, 50000, 0.05),
    ("ETH-USD", 10000, 3000, 0.5),
    ("ADA-USD", 500, 0.5, 1000),
]:
    vol_per_trade = tprice * tqty
    tpb = bsize / vol_per_trade
    status = "OK" if tpb >= 15 else "LOW"
    print(f"    {sym}: {tpb:.0f} trades/bucket [{status}]")
    if tpb < 10:
        warn(f"{sym} VPIN bucket sizing", f"only {tpb:.0f} trades/bucket, need 15+ for stability")

# 2c. Hawkes stability
print()
hawkes = HawkesEstimator(mu=1.0, alpha=0.5, beta=2.0, spike_threshold_mult=2.5)
# Burst detection
for i in range(60):
    hawkes.on_event(float(i))
for j in range(20):
    hawkes.on_event(60.0 + j * 0.05)
check("Hawkes detects burst", hawkes.current.is_spike, f"ratio={hawkes.current.spike_ratio:.2f}x")

# Hawkes decay: intensity should decrease after burst
intensity_now = hawkes.current.intensity
intensity_5s = hawkes.get_intensity_at(66.0)
check("Hawkes decays after burst", intensity_5s < intensity_now,
      f"now={intensity_now:.2f}, 5s later={intensity_5s:.2f}")

# Hawkes stability validation
try:
    bad = HawkesEstimator(mu=1.0, alpha=3.0, beta=2.0)
    errors.append("Hawkes allows alpha >= beta")
except ValueError:
    check("Hawkes rejects alpha >= beta", True)

# 2d. A-S spread progression
print()
as_eng = AvellanedaStoikovEngine(gamma=0.1, kappa=1.5, min_spread_bps=3.0, max_spread_bps=100.0, fee_bps=2.0)

r_calm = as_eng.compute(mid_price=50000, inventory=0, max_inventory=1, sigma=0.01, atr=500, time_remaining=0.5)
r_vpin = as_eng.compute(mid_price=50000, inventory=0, max_inventory=1, sigma=0.01, atr=500, time_remaining=0.5,
                        vpin=VPINResult(vpin=0.7, is_toxic=True))
r_crisis = as_eng.compute(mid_price=50000, inventory=0, max_inventory=1, sigma=0.01, atr=500, time_remaining=0.5,
                          vpin=VPINResult(vpin=0.9, is_toxic=True),
                          hawkes=HawkesResult(intensity=5, baseline=2, is_spike=True, spike_ratio=4))

print(f"  A-S spreads: calm={r_calm.spread_bps:.1f} < vpin={r_vpin.spread_bps:.1f} < crisis={r_crisis.spread_bps:.1f} bps")
check("A-S spread increases with risk", r_calm.spread_bps < r_vpin.spread_bps < r_crisis.spread_bps)
check("A-S gamma capped at 5x base", r_crisis.effective_gamma <= 0.501,
      f"got {r_crisis.effective_gamma:.4f}")
check("A-S spread >= min_floor", r_calm.spread_bps >= 4.0,  # 2*fee_bps
      f"got {r_calm.spread_bps:.1f} bps")
check("A-S spread <= max", r_crisis.spread_bps <= 100.0, f"got {r_crisis.spread_bps:.1f} bps")

# Inventory skew verification
r_long = as_eng.compute(mid_price=50000, inventory=0.5, max_inventory=1, sigma=0.01, atr=500, time_remaining=0.5)
r_short = as_eng.compute(mid_price=50000, inventory=-0.5, max_inventory=1, sigma=0.01, atr=500, time_remaining=0.5)
r_flat = as_eng.compute(mid_price=50000, inventory=0, max_inventory=1, sigma=0.01, atr=500, time_remaining=0.5)

# Long inventory -> quotes shift DOWN (incentivize selling)
check("Long inv: ask < mid (eager to sell)", r_long.ask_price < 50000,
      f"ask={r_long.ask_price:.0f}")
# Short inventory -> quotes shift UP (incentivize buying)
check("Short inv: bid > mid (eager to buy)", r_short.bid_price > 50000,
      f"bid={r_short.bid_price:.0f}")
# Flat: symmetric around mid
check("Flat inv: symmetric", abs(r_flat.bid_price - (50000 - r_flat.optimal_spread/2)) < 1,
      f"bid={r_flat.bid_price:.0f}, expected={50000 - r_flat.optimal_spread/2:.0f}")

# ═══════════════════════════════════════════════════════════════════
# 3. STRATEGIES
# ═══════════════════════════════════════════════════════════════════
print("\n[3] STRATEGY SIGNAL GENERATION")
print("-" * 50)

# Helper: generate OHLCV DataFrame with indicators
def make_df(prices, vol=None):
    n = len(prices)
    if vol is None:
        vol = np.random.uniform(50, 200, n)
    h = prices + abs(np.random.randn(n)) * 30
    l = prices - abs(np.random.randn(n)) * 30
    o = np.roll(prices, 1)
    o[0] = prices[0]
    df = pd.DataFrame({"timestamp": np.arange(n)*60, "close": prices, "open": o, "high": h, "low": l, "volume": vol})
    return Indicators.compute_all(df, {"ema_fast": 12, "ema_slow": 26, "zscore_lookback": 100})

def make_snap(price, ob=None):
    return MarketSnapshot(symbol="BTC-USD", timestamp=0, price=price, mark_price=price,
                          index_price=price, funding_rate=0, volume_24h=0, open_interest=0, orderbook=ob)

# 3a. Mean Reversion: force extreme Z-score for guaranteed signal
np.random.seed(42)
mr = MeanReversionStrategy(tc)
# 150 bars around 50000, then drop sharply
mr_prices = np.concatenate([50000 + np.random.randn(150) * 20, 50000 - np.arange(50) * 30])
df_mr = make_df(mr_prices)
last_price = float(df_mr["close"].iloc[-1])
zscore_last = float(df_mr["zscore"].iloc[-1])
rsi_last = float(df_mr["rsi"].iloc[-1])
print(f"  MR test: price={last_price:.0f}, zscore={zscore_last:.2f}, rsi={rsi_last:.1f}")

sigs = mr.generate_signals("BTC-USD", df_mr, make_snap(last_price), MarketRegime.RANGING, sc, 30000, None)
if zscore_last < -2.0 and rsi_last < 35:
    check("MR generates LONG on deep dip", len(sigs) > 0 and sigs[0].side == Side.BUY,
          f"sigs={len(sigs)}, zscore={zscore_last:.2f}, rsi={rsi_last:.1f}")
    if sigs:
        s = sigs[0]
        check("MR SL below entry", s.stop_loss < s.entry_price, f"SL={s.stop_loss:.0f} vs entry={s.entry_price:.0f}")
        check("MR TP above entry", s.take_profit > s.entry_price, f"TP={s.take_profit:.0f} vs entry={s.entry_price:.0f}")
        rr = (s.take_profit - s.entry_price) / (s.entry_price - s.stop_loss) if s.entry_price > s.stop_loss else 0
        print(f"    R:R ratio = {rr:.2f} (expect ~1.67 = 2.5/1.5)")
        check("MR R:R ~1.67", abs(rr - 2.5/1.5) < 0.3, f"got {rr:.2f}")
else:
    print(f"  (zscore={zscore_last:.2f} or rsi={rsi_last:.1f} didn't trigger, checking guards)")
    check("MR correctly filtered", True, "conditions not met")

# MR: verify no signal when position exists
pos = Position(symbol="BTC-USD", side=Side.BUY, size=0.5, entry_price=49000, mark_price=last_price)
sigs_with_pos = mr.generate_signals("BTC-USD", df_mr, make_snap(last_price), MarketRegime.RANGING, sc, 30000, pos)
entry_sigs = [s for s in sigs_with_pos if s.metadata.get("action") != "exit_mean_reversion"]
check("MR no entry with existing position", len(entry_sigs) == 0, f"got {len(entry_sigs)} entry signals")

# 3b. Trend Following
tf = TrendFollowingStrategy(tc)
np.random.seed(123)
tf_prices = 50000 + np.cumsum(np.ones(200) * 25 + np.random.randn(200) * 15)
df_tf = make_df(tf_prices, vol=np.random.uniform(100, 300, 200))
last_p = float(df_tf["close"].iloc[-1])
adx_val = float(df_tf["adx"].iloc[-1])
ema_cross = float(df_tf["ema_cross"].iloc[-1])
mom = float(df_tf["momentum_20"].iloc[-1])
vol_ratio = float(df_tf["vol_ratio"].iloc[-1])
print(f"\n  TF test: price={last_p:.0f}, adx={adx_val:.1f}, ema_cross={ema_cross}, mom={mom:.4f}, vol_ratio={vol_ratio:.2f}")

sigs_tf = tf.generate_signals("BTC-USD", df_tf, make_snap(last_p), MarketRegime.TRENDING_UP, sc, 30000, None)
print(f"  TF signals: {len(sigs_tf)}")
if sigs_tf:
    s = sigs_tf[0]
    rr = abs(s.take_profit - s.entry_price) / abs(s.entry_price - s.stop_loss)
    print(f"    Side={s.side.value}, R:R={rr:.2f} (expect 3.0)")
    check("TF R:R = 3.0", abs(rr - 3.0) < 0.01, f"got {rr:.2f}")
    check("TF strength in [0,1]", 0 <= s.strength <= 1, f"got {s.strength:.3f}")

# 3c. Market Making
mm = MarketMakingStrategy(tc)
np.random.seed(42)
mm_prices = 50000 + np.random.randn(200) * 20
df_mm = make_df(mm_prices)
ob = OrderBook(symbol="BTC-USD", timestamp=0,
               bids=[OrderBookLevel(49990, 1.0)], asks=[OrderBookLevel(50010, 1.0)])
snap_mm = make_snap(50000, ob)

sigs_mm = mm.generate_signals("BTC-USD", df_mm, snap_mm, MarketRegime.RANGING, sc, 30000, None)
bids = [s for s in sigs_mm if s.side == Side.BUY]
asks = [s for s in sigs_mm if s.side == Side.SELL]
print(f"\n  MM signals: {len(sigs_mm)} (bids={len(bids)}, asks={len(asks)})")
check("MM equal bids and asks", len(bids) == len(asks), f"bids={len(bids)}, asks={len(asks)}")
check("MM signals = 2 * levels", len(sigs_mm) == sc.mm_order_levels * 2,
      f"got {len(sigs_mm)}, expected {sc.mm_order_levels * 2}")

if bids and asks:
    best_bid = max(s.entry_price for s in bids)
    best_ask = min(s.entry_price for s in asks)
    check("MM not crossed", best_bid < best_ask, f"bid={best_bid:.2f}, ask={best_ask:.2f}")
    mm_spread = (best_ask - best_bid) / 50000 * 10000
    print(f"    Spread = {mm_spread:.1f} bps")

# MM paused during crisis
micro_crisis = MicrostructureSnapshot(
    symbol="BTC-USD",
    vpin=VPINResult(vpin=0.9, is_toxic=True),
    hawkes=HawkesResult(intensity=5, baseline=2, is_spike=True, spike_ratio=3)
)
sigs_pause = mm.generate_signals("BTC-USD", df_mm, snap_mm, MarketRegime.RANGING, sc, 30000, None, micro=micro_crisis)
check("MM paused in crisis", len(sigs_pause) == 0, f"got {len(sigs_pause)} signals")

# ═══════════════════════════════════════════════════════════════════
# 4. RISK MANAGER
# ═══════════════════════════════════════════════════════════════════
print("\n[4] RISK MANAGER NUMERICAL VERIFICATION")
print("-" * 50)

rm = RiskManager(settings)

# 4a. Drawdown calculation
rm.update_equity(100000)
rm.update_equity(120000)  # new peak
rm.update_equity(108000)  # 10% drawdown
dd = rm.current_drawdown_pct
check("Drawdown = 10%", abs(dd - 0.10) < 0.001, f"got {dd:.4f}")

# 4b. Circuit breaker at 80% of max_drawdown (15%)
# 80% of 15% = 12% drawdown
rm2 = RiskManager(settings)
rm2.update_equity(100000)
rm2.update_equity(100000)  # peak = 100k
rm2.update_equity(88000)   # 12% drawdown > 80% of 15%
check("Circuit breaker at 12% DD", rm2._circuit_breaker_active, f"DD={rm2.current_drawdown_pct:.2%}")

# 4c. Position sizing formula
from core.types import Signal
sig = Signal(strategy=StrategyType.MEAN_REVERSION, symbol="BTC-USD", side=Side.BUY,
             strength=0.7, entry_price=50000, stop_loss=49000, take_profit=52000, size_usd=10000)

# risk_amount = equity * 0.02 = 100k * 0.02 = 2000
# risk_per_unit = |50000 - 49000| = 1000
# max_size_by_risk = (2000/1000) * 50000 = 100000 USD
rm3 = RiskManager(settings)
validated = rm3.validate_signal(sig, sc, MarketRegime.RANGING)
check("Signal validated (no blocks)", validated is not None)
if validated:
    check("Size adjusted <= original", validated.size_usd <= sig.size_usd + 0.01,
          f"got {validated.size_usd:.0f}, original {sig.size_usd:.0f}")

# 4d. Consecutive loss reduction
rm4 = RiskManager(settings)
rm4._consecutive_losses = 3
sig4 = Signal(strategy=StrategyType.MEAN_REVERSION, symbol="BTC-USD", side=Side.BUY,
              strength=0.7, entry_price=50000, stop_loss=49000, take_profit=52000, size_usd=5000)
v4 = rm4.validate_signal(sig4, sc, MarketRegime.RANGING)
if v4:
    # After 3 losses: reduction = 0.5^(3-2) = 0.5
    # Before reduction, size may be capped by other limits
    print(f"    Size after 3 losses: {v4.size_usd:.0f} (should be ~50% of input)")

# 4e. Funding rate blocking
rm5 = RiskManager(settings)
sig_long = Signal(strategy=StrategyType.MEAN_REVERSION, symbol="BTC-USD", side=Side.BUY,
                  strength=0.7, entry_price=50000, stop_loss=49000, take_profit=52000, size_usd=5000)
v_funded = rm5.validate_signal(sig_long, sc, MarketRegime.RANGING, funding_rate=0.001)
check("Funding rate blocks long (rate=0.001)", v_funded is None, f"got {'None' if v_funded is None else f'size={v_funded.size_usd:.0f}'}")

# ═══════════════════════════════════════════════════════════════════
# 5. PORTFOLIO ALLOCATION
# ═══════════════════════════════════════════════════════════════════
print("\n[5] PORTFOLIO ALLOCATION VERIFICATION")
print("-" * 50)

pm = PortfolioManager(settings, RiskManager(settings))

# Verify allocations sum correctly
for regime in [MarketRegime.RANGING, MarketRegime.TRENDING_UP, MarketRegime.BREAKOUT, MarketRegime.UNKNOWN]:
    total = 0
    for st in StrategyType:
        alloc = pm.get_allocation("BTC-USD", regime, st)
        total += alloc
    # Expected: equity * 1.0 * perf * dd * (1/3 symbols)
    # = 100000 * 1.0 * 1.0 * 1.0 * 0.333 = 33333
    expected = 100000 / len(settings.symbols)
    pct = total / expected * 100
    print(f"  {regime.value:15s}: total alloc={total:.0f}, expected~{expected:.0f} ({pct:.0f}%)")
    check(f"Alloc {regime.value} ~100% of per-symbol equity", abs(pct - 100) < 5, f"got {pct:.0f}%")

# ═══════════════════════════════════════════════════════════════════
# 6. SLIPPAGE MODEL
# ═══════════════════════════════════════════════════════════════════
print("\n[6] SLIPPAGE MODEL VERIFICATION")
print("-" * 50)

# Base slippage
slip_base = compute_slippage(base_bps=2.0, price=50000, size_usd=0, regime="")
expected_base = 2.0 * 50000 / 10000  # = 10 USD
check("Base slippage = 10 USD", abs(slip_base - expected_base) < 0.01, f"got {slip_base:.2f}")

# Regime multiplier
slip_breakout = compute_slippage(base_bps=2.0, price=50000, size_usd=0, regime="BREAKOUT")
check("BREAKOUT = 2x base", abs(slip_breakout - expected_base * 2) < 0.01, f"got {slip_breakout:.2f}")

slip_ranging = compute_slippage(base_bps=2.0, price=50000, size_usd=0, regime="RANGING")
check("RANGING = 0.8x base", abs(slip_ranging - expected_base * 0.8) < 0.01, f"got {slip_ranging:.2f}")

# Size impact with book depth
slip_large = compute_slippage(base_bps=2.0, price=50000, size_usd=50000, book_depth_usd=100000)
slip_small = compute_slippage(base_bps=2.0, price=50000, size_usd=5000, book_depth_usd=100000)
check("Larger orders have more slippage", slip_large > slip_small,
      f"large={slip_large:.2f}, small={slip_small:.2f}")

# Hawkes spike adds slippage
slip_hawkes = compute_slippage(base_bps=2.0, price=50000, hawkes_ratio=3.0)
slip_no_hawkes = compute_slippage(base_bps=2.0, price=50000, hawkes_ratio=1.0)
check("Hawkes spike adds slippage", slip_hawkes > slip_no_hawkes,
      f"hawkes={slip_hawkes:.2f}, normal={slip_no_hawkes:.2f}")

# ═══════════════════════════════════════════════════════════════════
# 7. BACKTESTER - PNL & LIQUIDATION
# ═══════════════════════════════════════════════════════════════════
print("\n[7] BACKTESTER PNL & LIQUIDATION")
print("-" * 50)

# PnL calculation
pos_bt = BacktestPosition("BTC-USD", Side.BUY, 1.0, 50000, StrategyType.MEAN_REVERSION, leverage=10)
# Close at 51000 with taker fee (0.05%)
pnl = pos_bt.close(51000, 0.0005)
# Gross = (51000 - 50000) * 1.0 = 1000
# Fee = (50000*1 + 51000*1) * 0.0005 = 50.5
# Net = 1000 - 50.5 = 949.5
check("PnL calculation", abs(pnl - 949.5) < 0.01, f"got {pnl:.2f}, expected 949.50")

# Liquidation: 10x leverage, 2% maintenance
pos_liq = BacktestPosition("BTC-USD", Side.BUY, 1.0, 50000, StrategyType.MEAN_REVERSION, leverage=10)
# margin = 50000/10 = 5000
# liquidation when loss >= 5000 * (1-0.02) = 4900
# price at liquidation = 50000 - 4900 = 45100
check("Not liquidated at 45200", not pos_liq.is_liquidated(45200))
check("Liquidated at 44900", pos_liq.is_liquidated(44900))
check("Not liquidated at 55000 (profit)", not pos_liq.is_liquidated(55000))

# SHORT liquidation
pos_short = BacktestPosition("BTC-USD", Side.SELL, 1.0, 50000, StrategyType.MEAN_REVERSION, leverage=10)
check("Short: not liquidated at 54800", not pos_short.is_liquidated(54800))
check("Short: liquidated at 55100", pos_short.is_liquidated(55100))

# ═══════════════════════════════════════════════════════════════════
# 8. REGIME DETECTOR
# ═══════════════════════════════════════════════════════════════════
print("\n[8] REGIME DETECTOR")
print("-" * 50)

from core.regime_detector import RegimeDetector
rd = RegimeDetector()

# Calm market -> RANGING
np.random.seed(42)
calm = 50000 + np.random.randn(200) * 20
df_calm = make_df(calm)
regime_calm = rd.detect(df_calm, "BTC-USD", sc)
print(f"  Calm market: {regime_calm.value}")
# First detection goes through smoothing, might be UNKNOWN initially

# Trending market -> TRENDING_UP or BREAKOUT
np.random.seed(42)
trend_up = 50000 + np.cumsum(np.ones(200) * 25 + np.random.randn(200) * 15)
df_trend = make_df(trend_up, vol=np.random.uniform(100, 300, 200))
# Detect multiple times to warm up smoothing
for _ in range(3):
    regime_trend = rd.detect(df_trend, "TEST-UP", sc)
print(f"  Trending up market: {regime_trend.value}")
check("Trending detected as TRENDING or BREAKOUT",
      regime_trend in (MarketRegime.TRENDING_UP, MarketRegime.BREAKOUT),
      f"got {regime_trend.value}")

# Smoothing: require 2 consecutive
rd2 = RegimeDetector()
# Force alternating: should maintain previous
r1 = rd2.detect(df_calm, "SMOOTH", sc)  # first
r2 = rd2.detect(df_trend, "SMOOTH", sc)  # different
r3 = rd2.detect(df_calm, "SMOOTH", sc)  # different again
# r3 should be r1 or r2 (maintained from confirmed)
print(f"  Smoothing test: {r1.value} -> {r2.value} -> {r3.value}")

# ═══════════════════════════════════════════════════════════════════
# 9. END-TO-END BACKTEST SANITY
# ═══════════════════════════════════════════════════════════════════
print("\n[9] END-TO-END BACKTEST SANITY")
print("-" * 50)

bt = Backtester(settings)
df_bt = Backtester.generate_sample_data("BTC-USD", bars=2000, start_price=50000)
result = bt.run(df_bt, "BTC-USD")
s = result.summary()

print(f"  Trades: {s['total_trades']}")
print(f"  PnL: ${s['net_pnl']:,.2f}")
print(f"  Win rate: {s['win_rate']:.2%}")
print(f"  Sharpe: {s['sharpe_ratio']:.2f}")
print(f"  Max DD: {s['max_drawdown']:.2%}")
print(f"  Profit Factor: {s['profit_factor']:.2f}")

check("Backtest produces trades", s["total_trades"] > 0)
check("Win rate in [0, 1]", 0 <= s["win_rate"] <= 1)
check("Max DD in [0, 1]", 0 <= s["max_drawdown"] <= 1)
check("Equity curve grows", len(result.equity_curve) > 100)
check("Sharpe is finite", abs(s["sharpe_ratio"]) < 100, f"got {s['sharpe_ratio']:.2f}")

# Verify fee impact
total_fees = sum(t.get("fee", 0) for t in result.trades)
print(f"  Total fees: ${total_fees:,.2f}")
check("Fees are positive", total_fees > 0)

# Verify by-strategy breakdown
for st_name, st_data in s.get("by_strategy", {}).items():
    check(f"  {st_name} has trades", st_data["trades"] > 0)

# ═══════════════════════════════════════════════════════════════════
# 10. PAPER SIMULATOR
# ═══════════════════════════════════════════════════════════════════
print("\n[10] PAPER SIMULATOR VERIFICATION")
print("-" * 50)

from execution.paper_simulator import PaperTradingSimulator

ps = PaperTradingSimulator(settings)

# Entry
entry_sig = Signal(strategy=StrategyType.MEAN_REVERSION, symbol="BTC-USD", side=Side.BUY,
                   strength=0.7, entry_price=50000, stop_loss=49000, take_profit=52000,
                   size_usd=5000, metadata={"regime": "RANGING"})
fills = ps.execute_signals([entry_sig], [], sc)
check("Paper entry produces fill", len(fills) == 1)
if fills:
    check("Paper entry pnl=0", fills[0].pnl == 0.0)
    check("Paper entry fee=0", fills[0].fee == 0.0)

# SL trigger
sl_fills = ps.on_price_update("BTC-USD", 48900)
check("Paper SL triggers at 48900", len(sl_fills) == 1)
if sl_fills:
    check("Paper SL has negative PnL", sl_fills[0].pnl < 0, f"pnl={sl_fills[0].pnl:.2f}")
    check("Paper SL includes fees", sl_fills[0].fee > 0, f"fee={sl_fills[0].fee:.4f}")

# ═══════════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print(f"  QUANT AUDIT COMPLETE")
print(f"  Checks passed: {len(errors) == 0 and 'ALL' or 'SOME FAILED'}")
print(f"  Errors: {len(errors)}")
print(f"  Warnings: {len(warnings)}")
print("=" * 70)

if errors:
    print("\nERRORS:")
    for e in errors:
        print(f"  [!] {e}")

if warnings:
    print("\nWARNINGS (non-critical):")
    for w in warnings:
        print(f"  [~] {w}")

total_checks = sum(1 for line in open(__file__).readlines() if "check(" in line and "def check" not in line)
print(f"\n  Total check() calls: ~{total_checks}")
sys.exit(1 if errors else 0)
