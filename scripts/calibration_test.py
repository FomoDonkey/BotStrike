"""Calibration diagnostic for microstructure engine."""
import sys, os, math, logging
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
logging.disable(logging.CRITICAL)
import structlog
structlog.configure(wrapper_class=structlog.BoundLogger, logger_factory=structlog.ReturnLoggerFactory())

import numpy as np
from core.microstructure import (
    VPINCalculator, HawkesEstimator, AvellanedaStoikovEngine,
    MicrostructureSnapshot, VPINResult, HawkesResult,
)

print("=" * 70)
print("  MICROSTRUCTURE CALIBRATION DIAGNOSTIC")
print("=" * 70)

# ================================================================
# 1. VPIN WITH CORRECT BUCKET SIZES
# ================================================================
print("\n--- 1. VPIN CALIBRATION ---")

for sym, bucket_sz, start_p in [("BTC-USD", 50000, 50000.0), ("ETH-USD", 10000, 3000.0), ("ADA-USD", 500, 0.5)]:
    np.random.seed(42)

    # NORMAL: balanced random walk (accumulating)
    vpin_n = VPINCalculator(bucket_size=bucket_sz, n_buckets=50, toxic_threshold=0.6)
    price = start_p
    for i in range(5000):
        price *= (1 + np.random.randn() * 0.0003)  # random walk
        qty = abs(np.random.lognormal(-4, 1.5))
        vpin_n.on_trade(price, qty, 1000.0 + i * 2)

    # TOXIC: directional sweep (accumulating upward)
    np.random.seed(42)
    vpin_t = VPINCalculator(bucket_size=bucket_sz, n_buckets=50, toxic_threshold=0.6)
    price = start_p
    for i in range(5000):
        price *= (1 + abs(np.random.randn()) * 0.0003)  # only goes up
        qty = abs(np.random.lognormal(-4, 1.5))
        vpin_t.on_trade(price, qty, 1000.0 + i * 2)

    rn = vpin_n.current
    rt = vpin_t.current
    sep = "OK" if (rn.vpin < 0.5 and rt.vpin > 0.7) else "REVIEW"
    print(f"  {sym} (bucket=${bucket_sz:,}):")
    print(f"    Normal: VPIN={rn.vpin:.3f} toxic={rn.is_toxic}  buckets={rn.bucket_count}")
    print(f"    Sweep:  VPIN={rt.vpin:.3f} toxic={rt.is_toxic}  buckets={rt.bucket_count}")
    print(f"    Separation: [{sep}]")

# ================================================================
# 2. A-S SPREAD TABLE
# ================================================================
print("\n--- 2. A-S SPREAD vs VPIN/sigma (BTC mid=50000, ATR=500) ---")
as_e = AvellanedaStoikovEngine(gamma=0.1, kappa=1.5, min_spread_bps=3.0, max_spread_bps=100.0, fee_bps=3.5)
print(f"  {'sigma':>8s} {'VPIN':>6s} {'gamma':>8s} {'bps':>8s} {'quality':>10s}")
for sigma in [0.005, 0.01, 0.02, 0.05]:
    for vv in [0.0, 0.3, 0.6, 0.8]:
        vr = VPINResult(vpin=vv, is_toxic=vv >= 0.6)
        r = as_e.compute(mid_price=50000, inventory=0, max_inventory=0.4, sigma=sigma, atr=500,
                         time_remaining=0.5, vpin=vr, hawkes=HawkesResult(spike_ratio=1.0))
        print(f"  {sigma:8.4f} {vv:6.2f} {r.effective_gamma:8.4f} {r.spread_bps:8.2f} {r.spread_quality:>10s}")

# ================================================================
# 3. HAWKES EFFECT ON SPREAD
# ================================================================
print(f"\n--- 3. HAWKES EFFECT (BTC, sigma=0.01, ATR=500) ---")
print(f"  {'VPIN':>6s} {'Hk_rat':>7s} {'gamma':>8s} {'bps':>8s} {'quality':>10s}")
for vv in [0.0, 0.3, 0.6]:
    for hr in [1.0, 2.0, 3.0, 5.0]:
        vr = VPINResult(vpin=vv, is_toxic=vv >= 0.6)
        hw = HawkesResult(spike_ratio=hr, is_spike=hr > 2.5, intensity=hr, baseline=1.0)
        r = as_e.compute(mid_price=50000, inventory=0, max_inventory=0.4, sigma=0.01, atr=500,
                         time_remaining=0.5, vpin=vr, hawkes=hw)
        print(f"  {vv:6.2f} {hr:7.2f} {r.effective_gamma:8.4f} {r.spread_bps:8.2f} {r.spread_quality:>10s}")

# ================================================================
# 4. INVENTORY EFFECT
# ================================================================
print(f"\n--- 4. INVENTORY EFFECT (BTC, sigma=0.01, ATR=500) ---")
print(f"  {'inv':>6s} {'reserv':>10s} {'skew$':>8s} {'bid':>10s} {'ask':>10s} {'bps':>8s}")
for inv in [-0.3, -0.1, 0, 0.1, 0.3]:
    r = as_e.compute(mid_price=50000, inventory=inv, max_inventory=0.4, sigma=0.01, atr=500, time_remaining=0.5)
    print(f"  {inv:6.2f} {r.reservation_price:10.2f} {r.inventory_skew:8.2f} {r.bid_price:10.2f} {r.ask_price:10.2f} {r.spread_bps:8.2f}")

# ================================================================
# 5. MULTI-ASSET
# ================================================================
print(f"\n--- 5. MULTI-ASSET ---")
for sym, mid, atr_v in [("BTC-USD", 50000, 500), ("ETH-USD", 3000, 40), ("ADA-USD", 0.5, 0.01)]:
    for vv in [0.0, 0.6, 0.8]:
        vr = VPINResult(vpin=vv, is_toxic=vv >= 0.6)
        r = as_e.compute(mid_price=mid, inventory=0, max_inventory=1.0, sigma=0.01, atr=atr_v,
                         time_remaining=0.5, vpin=vr, hawkes=HawkesResult(spike_ratio=1.0))
        print(f"  {sym:8s} VPIN={vv:.1f}  spread={r.spread_bps:6.2f} bps  quality={r.spread_quality}")

# ================================================================
# 6. FULL DYNAMIC RANGE
# ================================================================
print(f"\n--- 6. DYNAMIC RANGE (should span 7 to 60+ bps) ---")
cases = [
    ("Calm market",          0.005, 200,  0.0, 1.0),
    ("Normal, safe",         0.01,  500,  0.0, 1.0),
    ("Normal, VPIN mild",    0.01,  500,  0.3, 1.0),
    ("Normal, VPIN high",    0.01,  500,  0.7, 1.0),
    ("Normal, Hawkes spike", 0.01,  500,  0.0, 4.0),
    ("VPIN+Hawkes combined", 0.01,  500,  0.7, 3.0),
    ("High vol, safe",       0.03,  1500, 0.0, 1.0),
    ("High vol, VPIN toxic", 0.03,  1500, 0.8, 1.0),
    ("Crisis: everything",   0.05,  2500, 0.8, 5.0),
]
print(f"  {'Scenario':>25s} {'bps':>8s} {'gamma':>8s} {'quality':>10s}")
for label, sig, atr_v, vv, hr in cases:
    vr = VPINResult(vpin=vv, is_toxic=vv >= 0.6)
    hw = HawkesResult(spike_ratio=hr, is_spike=hr > 2.5, intensity=hr, baseline=1.0)
    r = as_e.compute(mid_price=50000, inventory=0, max_inventory=0.4, sigma=sig, atr=atr_v,
                     time_remaining=0.5, vpin=vr, hawkes=hw)
    print(f"  {label:>25s} {r.spread_bps:8.2f} {r.effective_gamma:8.4f} {r.spread_quality:>10s}")

print("\n" + "=" * 70)
print("  COMPLETE")
print("=" * 70)
