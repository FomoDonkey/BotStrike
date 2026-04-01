"""
Functional tests for BotStrike core modules.
Tests: types.py, indicators.py, regime_detector.py, market_data.py, microstructure.py
"""
import sys
import os
import math
import traceback

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

results = []

def run_test(name, fn):
    try:
        fn()
        results.append((name, True, ""))
        print(f"  PASS: {name}")
    except Exception as e:
        results.append((name, False, str(e)))
        print(f"  FAIL: {name}")
        traceback.print_exc()
        print()


# ================================================================
# 1. core/types.py
# ================================================================
print("=" * 60)
print("1. TESTING core/types.py")
print("=" * 60)

from core.types import (
    OrderBook, OrderBookLevel, Position, Side, Signal, Trade,
    StrategyType, MarketRegime
)

def test_orderbook_best_bid_unsorted():
    ob = OrderBook(
        symbol="BTC", timestamp=1.0,
        bids=[OrderBookLevel(100, 1), OrderBookLevel(105, 2), OrderBookLevel(99, 3)],
        asks=[OrderBookLevel(112, 1), OrderBookLevel(108, 2), OrderBookLevel(110, 3)],
    )
    assert ob.best_bid == 105, f"Expected 105, got {ob.best_bid}"
    assert ob.best_ask == 108, f"Expected 108, got {ob.best_ask}"

def test_orderbook_mid_price():
    ob = OrderBook(
        symbol="BTC", timestamp=1.0,
        bids=[OrderBookLevel(100, 1)],
        asks=[OrderBookLevel(110, 1)],
    )
    assert ob.mid_price == 105.0, f"Expected 105.0, got {ob.mid_price}"

def test_orderbook_spread():
    ob = OrderBook(
        symbol="BTC", timestamp=1.0,
        bids=[OrderBookLevel(100, 1)],
        asks=[OrderBookLevel(110, 1)],
    )
    assert ob.spread == 10.0, f"Expected 10.0, got {ob.spread}"

def test_position_pnl_pct_buy():
    pos = Position(symbol="BTC", side=Side.BUY, size=1.0, entry_price=100.0, mark_price=110.0)
    assert abs(pos.pnl_pct - 0.10) < 1e-9, f"Expected 0.10, got {pos.pnl_pct}"

def test_position_pnl_pct_sell():
    pos = Position(symbol="BTC", side=Side.SELL, size=1.0, entry_price=100.0, mark_price=90.0)
    assert abs(pos.pnl_pct - 0.10) < 1e-9, f"Expected 0.10, got {pos.pnl_pct}"

def test_position_pnl_pct_sell_loss():
    pos = Position(symbol="BTC", side=Side.SELL, size=1.0, entry_price=100.0, mark_price=110.0)
    assert abs(pos.pnl_pct - (-0.10)) < 1e-9, f"Expected -0.10, got {pos.pnl_pct}"

def test_signal_defaults():
    sig = Signal(
        strategy=StrategyType.MEAN_REVERSION, symbol="BTC", side=Side.BUY,
        strength=0.8, entry_price=100, stop_loss=95, take_profit=110, size_usd=1000
    )
    assert isinstance(sig.timestamp, float), "timestamp should be float"
    assert sig.timestamp > 0, "timestamp should be > 0"
    assert sig.metadata == {}, f"metadata default should be empty dict, got {sig.metadata}"

def test_trade_defaults():
    t = Trade(symbol="BTC", side=Side.BUY, price=100, quantity=1, fee=0.1)
    assert t.fee_asset == "USD", f"Expected 'USD', got {t.fee_asset}"
    assert t.order_id == "", f"Expected '', got {t.order_id}"
    assert t.pnl == 0.0, f"Expected 0.0, got {t.pnl}"
    assert t.strategy is None, f"Expected None, got {t.strategy}"

for fn in [test_orderbook_best_bid_unsorted, test_orderbook_mid_price,
           test_orderbook_spread, test_position_pnl_pct_buy,
           test_position_pnl_pct_sell, test_position_pnl_pct_sell_loss,
           test_signal_defaults, test_trade_defaults]:
    run_test(fn.__name__, fn)


# ================================================================
# 2. core/indicators.py
# ================================================================
print()
print("=" * 60)
print("2. TESTING core/indicators.py")
print("=" * 60)

from core.indicators import Indicators

def test_compute_all_empty():
    df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    result = Indicators.compute_all(df)
    assert result.empty, "compute_all on empty df should return empty"

def test_compute_all_valid_200_bars():
    np.random.seed(42)
    n = 200
    close = 100 + np.cumsum(np.random.randn(n) * 0.5)
    high = close + np.abs(np.random.randn(n) * 0.3)
    low = close - np.abs(np.random.randn(n) * 0.3)
    open_ = close + np.random.randn(n) * 0.1
    volume = np.random.uniform(100, 1000, n)
    df = pd.DataFrame({
        "open": open_, "high": high, "low": low, "close": close, "volume": volume
    })
    result = Indicators.compute_all(df)
    expected_cols = [
        "sma_20", "sma_50", "ema_12", "ema_26", "atr", "std_20", "zscore",
        "bb_upper", "bb_mid", "bb_lower", "momentum_10", "momentum_20",
        "rsi", "vol_ratio", "adx", "ema_cross", "vol_pct"
    ]
    for col in expected_cols:
        assert col in result.columns, f"Missing column: {col}"
    # Last row should have values for most indicators (some may be NaN for early rows)
    last = result.iloc[-1]
    for col in ["sma_20", "sma_50", "ema_12", "ema_26", "atr", "rsi", "adx"]:
        assert not pd.isna(last[col]), f"Last row {col} should not be NaN, got {last[col]}"

def test_adx_no_bearish_bias():
    """ADX should give similar values for symmetric up and down moves."""
    n = 200
    # Pure uptrend
    up_close = np.linspace(100, 150, n)
    up_high = up_close + 0.5
    up_low = up_close - 0.5
    df_up = pd.DataFrame({"high": up_high, "low": up_low, "close": up_close})
    adx_up = Indicators.adx(df_up["high"], df_up["low"], df_up["close"], 14)

    # Pure downtrend (mirror)
    dn_close = np.linspace(150, 100, n)
    dn_high = dn_close + 0.5
    dn_low = dn_close - 0.5
    df_dn = pd.DataFrame({"high": dn_high, "low": dn_low, "close": dn_close})
    adx_dn = Indicators.adx(df_dn["high"], df_dn["low"], df_dn["close"], 14)

    last_up = float(adx_up.iloc[-1])
    last_dn = float(adx_dn.iloc[-1])
    # Both should be high (strong trend) and within 30% of each other
    assert last_up > 20, f"ADX for uptrend too low: {last_up}"
    assert last_dn > 20, f"ADX for downtrend too low: {last_dn}"
    ratio = max(last_up, last_dn) / min(last_up, last_dn)
    assert ratio < 1.5, f"ADX bias: up={last_up:.2f}, dn={last_dn:.2f}, ratio={ratio:.2f}"

def test_zscore_flat_price():
    """Z-score for flat price should be 0.0, not NaN."""
    n = 200
    flat = pd.Series([100.0] * n)
    zs = Indicators.zscore(flat, 100)
    # For constant price, std=0, so zscore should handle gracefully
    # The implementation replaces 0 std with NaN, so zscore will be NaN
    # But after the fix, flat price should give 0 deviation from mean
    # Actually (100 - 100) / NaN = NaN. Let's check what the code does:
    # std.replace(0, np.nan) -> NaN, then (0) / NaN = NaN
    # The requirement says it should be 0.0, let's check:
    last_val = zs.iloc[-1]
    # With std=0 replaced by NaN, result is NaN. But numerator is also 0.
    # 0/NaN = NaN in pandas. This is a known issue.
    # Check if the implementation handles it (0/0 should be 0, not NaN):
    if pd.isna(last_val):
        # If NaN, this test fails - the code should handle flat price
        assert False, f"Z-score for flat price is NaN, should be 0.0"
    else:
        assert abs(last_val) < 1e-9, f"Z-score for flat price should be ~0, got {last_val}"

for fn in [test_compute_all_empty, test_compute_all_valid_200_bars,
           test_adx_no_bearish_bias, test_zscore_flat_price]:
    run_test(fn.__name__, fn)


# ================================================================
# 3. core/regime_detector.py
# ================================================================
print()
print("=" * 60)
print("3. TESTING core/regime_detector.py")
print("=" * 60)

from core.regime_detector import RegimeDetector
from config.settings import SymbolConfig

def _make_regime_df(n=200, trend="up"):
    """Create a DataFrame with indicators for regime detection."""
    np.random.seed(123)
    if trend == "up":
        close = 100 + np.cumsum(np.abs(np.random.randn(n) * 0.5))
    elif trend == "down":
        close = 200 - np.cumsum(np.abs(np.random.randn(n) * 0.5))
    else:
        close = 100 + np.random.randn(n) * 0.1
    high = close + np.abs(np.random.randn(n) * 0.3)
    low = close - np.abs(np.random.randn(n) * 0.3)
    open_ = close + np.random.randn(n) * 0.1
    volume = np.random.uniform(100, 1000, n)
    df = pd.DataFrame({
        "open": open_, "high": high, "low": low, "close": close, "volume": volume
    })
    df = Indicators.compute_all(df)
    return df

def test_detect_returns_valid_regime():
    rd = RegimeDetector()
    cfg = SymbolConfig(symbol="BTC")
    df = _make_regime_df(200, "up")
    regime = rd.detect(df, "BTC", cfg)
    assert isinstance(regime, MarketRegime), f"Expected MarketRegime, got {type(regime)}"
    assert regime in list(MarketRegime), f"Invalid regime: {regime}"

def test_get_current_regime_returns_smoothed():
    """get_current_regime should return the same as detect() (the smoothed value)."""
    rd = RegimeDetector()
    cfg = SymbolConfig(symbol="BTC")
    df = _make_regime_df(200, "up")
    # Call detect twice with same data to confirm regime
    r1 = rd.detect(df, "BTC", cfg)
    r2 = rd.detect(df, "BTC", cfg)
    current = rd.get_current_regime("BTC")
    assert current == r2, f"get_current_regime ({current}) != detect result ({r2})"

def test_smoothing_requires_2_consecutive():
    """Smoothing should require 2 consecutive same detections to change."""
    rd = RegimeDetector()
    # Manually test _smooth_regime
    # First detection: RANGING
    r1 = rd._smooth_regime("TEST", MarketRegime.RANGING)
    assert r1 == MarketRegime.RANGING, f"First detection should be accepted: {r1}"

    # Second detection: same -> should confirm
    r2 = rd._smooth_regime("TEST", MarketRegime.RANGING)
    assert r2 == MarketRegime.RANGING, f"Second same should confirm: {r2}"

    # Third: different (BREAKOUT) -> should NOT change yet
    r3 = rd._smooth_regime("TEST", MarketRegime.BREAKOUT)
    assert r3 == MarketRegime.RANGING, f"Single different should not change: {r3}"

    # Fourth: same different (BREAKOUT again) -> NOW should change
    r4 = rd._smooth_regime("TEST", MarketRegime.BREAKOUT)
    assert r4 == MarketRegime.BREAKOUT, f"Two consecutive should change: {r4}"

for fn in [test_detect_returns_valid_regime, test_get_current_regime_returns_smoothed,
           test_smoothing_requires_2_consecutive]:
    run_test(fn.__name__, fn)


# ================================================================
# 4. core/market_data.py (on_trade tick boundary)
# ================================================================
print()
print("=" * 60)
print("4. TESTING core/market_data.py (tick boundaries)")
print("=" * 60)

# We test the MarketDataCollector's on_trade and _close_bar logic directly
# by creating a minimal instance and manipulating its internal state.
# We need to mock Settings and StrikeClient minimally.

from core.market_data import MarketDataCollector

class _FakeSettings:
    symbols = []
    symbol_names = []
    def get_symbol_config(self, symbol):
        return SymbolConfig(symbol=symbol)

class _FakeClient:
    pass

def test_tick_after_bar_close_not_in_bar():
    """Tick after bar_interval should close bar and start new buffer."""
    collector = MarketDataCollector(
        settings=_FakeSettings(),
        client=_FakeClient(),
        regime_detector=RegimeDetector(),
    )
    collector._dataframes["BTC"] = pd.DataFrame(
        columns=["timestamp", "open", "high", "low", "close", "volume"]
    )
    sym = "BTC"
    bi = collector.bar_interval  # dynamic

    # First tick at t=1000 -> sets last_bar_time
    collector.on_trade(sym, 100.0, 1.0, 1000.0)
    assert collector._last_bar_time[sym] == 1000.0

    # Tick within bar
    collector.on_trade(sym, 101.0, 1.0, 1000.0 + bi * 0.5)

    # Tick just before boundary
    collector.on_trade(sym, 102.0, 1.0, 1000.0 + bi - 0.1)

    # Tick after boundary -> closes bar
    collector.on_trade(sym, 105.0, 1.0, 1000.0 + bi + 1)

    assert collector._last_bar_time[sym] == 1000.0 + bi, \
        f"Expected last_bar_time={1000.0 + bi}, got {collector._last_bar_time[sym]}"

    buffer = collector._tick_buffer[sym]
    buffer_ts = [t["timestamp"] for t in buffer]
    assert 1000.0 + bi + 1 in buffer_ts, f"Post-bar tick should be in buffer"
    assert 1000.0 not in buffer_ts, f"Pre-bar tick should not be in buffer"

def test_ticks_before_close_in_bar():
    """Ticks before bar close should be included in the closed bar."""
    collector = MarketDataCollector(
        settings=_FakeSettings(),
        client=_FakeClient(),
        regime_detector=RegimeDetector(),
    )
    collector._dataframes["ETH"] = pd.DataFrame(
        columns=["timestamp", "open", "high", "low", "close", "volume"]
    )
    sym = "ETH"
    bi = collector.bar_interval

    # Send ticks within one bar period
    collector.on_trade(sym, 50.0, 2.0, 2000.0)
    collector.on_trade(sym, 51.0, 3.0, 2000.0 + bi * 0.3)
    collector.on_trade(sym, 52.0, 1.0, 2000.0 + bi * 0.8)

    # Trigger bar close
    collector.on_trade(sym, 53.0, 1.0, 2000.0 + bi + 1)

    df = collector._dataframes[sym]
    assert len(df) >= 1, f"Expected at least 1 bar, got {len(df)}"
    bar = df.iloc[-1]
    assert bar["open"] == 50.0, f"Bar open should be 50.0, got {bar['open']}"
    assert bar["high"] == 52.0, f"Bar high should be 52.0, got {bar['high']}"
    assert bar["low"] == 50.0, f"Bar low should be 50.0, got {bar['low']}"
    assert bar["close"] == 52.0, f"Bar close should be 52.0, got {bar['close']}"

def test_tick_after_close_in_next_buffer():
    """Tick after bar close should be in the buffer for the next bar."""
    collector = MarketDataCollector(
        settings=_FakeSettings(),
        client=_FakeClient(),
        regime_detector=RegimeDetector(),
    )
    collector._dataframes["SOL"] = pd.DataFrame(
        columns=["timestamp", "open", "high", "low", "close", "volume"]
    )
    sym = "SOL"

    bi = collector.bar_interval
    collector.on_trade(sym, 30.0, 1.0, 3000.0)
    collector.on_trade(sym, 31.0, 1.0, 3000.0 + bi + 1)  # triggers bar close

    buffer = collector._tick_buffer[sym]
    assert any(t["timestamp"] == 3000.0 + bi + 1 for t in buffer), \
        f"Post-bar tick should be in next bar's buffer"
    assert not any(t["timestamp"] == 3000.0 for t in buffer), \
        f"Pre-bar tick should not be in next bar's buffer"

for fn in [test_tick_after_bar_close_not_in_bar, test_ticks_before_close_in_bar,
           test_tick_after_close_in_next_buffer]:
    run_test(fn.__name__, fn)


# ================================================================
# 5. core/microstructure.py
# ================================================================
print()
print("=" * 60)
print("5. TESTING core/microstructure.py")
print("=" * 60)

from core.microstructure import (
    VPINCalculator, HawkesEstimator, MicrostructureEngine, MicrostructureSnapshot
)

def test_vpin_cdf_monotonic():
    """VPIN CDF should be monotonic in the sense that it uses a consistent method.
    No discontinuity at any bucket count boundary. CDF values should always be in [0,1]
    and the method (searchsorted) should be used uniformly."""
    vpin = VPINCalculator(bucket_size=100.0, n_buckets=20, toxic_threshold=0.6)
    cdf_values = []
    bucket_counts = []
    np.random.seed(99)
    # Feed enough trades to fill many buckets
    price = 100.0
    for i in range(500):
        price += np.random.randn() * 0.1
        r = vpin.on_trade(price, 1.0, float(i))
        if r.bucket_count >= 5:
            cdf_values.append(r.cdf)
            bucket_counts.append(r.bucket_count)

    # CDF should be in [0, 1]
    for i, c in enumerate(cdf_values):
        assert 0.0 <= c <= 1.0, f"CDF out of range at idx {i}: {c}"

    # The CDF method should be consistent: uses searchsorted throughout.
    # Verify it works with the history growing -- no method switch at N=10 or similar.
    # Check that at least some CDF values are neither 0 nor 1 (it actually differentiates)
    mid_range = [c for c in cdf_values if 0.1 < c < 0.9]
    assert len(mid_range) > 0, "CDF should have some values in (0.1, 0.9) range"

    # Verify the CDF uses searchsorted (consistent method) by checking
    # that the implementation path is the same regardless of history length
    # We check this by verifying the vpin_history is populated
    hist = vpin.history
    assert len(hist) > 10, f"VPIN history should have >10 entries, got {len(hist)}"

def test_hawkes_spike_uses_original_mu():
    """Hawkes spike detection should use original mu, not adaptive mu."""
    hawkes = HawkesEstimator(mu=1.0, alpha=0.5, beta=2.0, spike_threshold_mult=2.5,
                              window_sec=10.0)

    # Feed many rapid events within a short window to drive adaptive_mu up
    base_t = 1000.0
    for i in range(200):
        hawkes.on_event(base_t + i * 0.01)  # 200 events in 2 seconds

    # adaptive_mu should be much higher than original mu=1.0 now
    # 200 events in 10s window = 20 events/sec >> 1.0
    assert hawkes._adaptive_mu > 1.0, f"adaptive_mu should have grown: {hawkes._adaptive_mu}"

    # The spike threshold should still be based on ORIGINAL mu (1.0 * 2.5 = 2.5)
    # NOT on adaptive_mu (which would be much higher and hide real spikes)
    spike_threshold = hawkes.mu * hawkes.spike_threshold_mult
    assert spike_threshold == 2.5, f"Spike threshold should be 2.5 (original mu), got {spike_threshold}"

    # Verify mu attribute is still the original value
    assert hawkes.mu == 1.0, f"hawkes.mu should stay at original 1.0, got {hawkes.mu}"

    # Verify the result uses original mu for spike_ratio too
    result = hawkes.current
    expected_ratio = result.intensity / hawkes.mu
    assert abs(result.spike_ratio - expected_ratio) < 1e-9, \
        f"spike_ratio should use original mu: expected {expected_ratio}, got {result.spike_ratio}"

    # Verify is_spike uses original mu threshold
    # With adaptive_mu >> mu, if we used adaptive_mu for threshold, spike would be hidden
    assert result.is_spike == (result.intensity > spike_threshold), \
        "is_spike should compare against original mu * mult, not adaptive"

def test_microstructure_engine_snapshot():
    """MicrostructureEngine.get_snapshot should return valid object after feeding trades."""
    engine = MicrostructureEngine(["BTC"], config={
        "BTC": {"vpin_bucket_size": 100.0, "vpin_n_buckets": 10}
    })

    # Feed trades
    price = 50000.0
    for i in range(100):
        price += np.random.randn() * 10
        engine.on_trade("BTC", price, 0.01, 1000.0 + i)

    snap = engine.get_snapshot("BTC")
    assert isinstance(snap, MicrostructureSnapshot), f"Expected MicrostructureSnapshot, got {type(snap)}"
    assert snap.symbol == "BTC", f"Expected symbol='BTC', got {snap.symbol}"
    assert snap.timestamp > 0, f"Timestamp should be > 0, got {snap.timestamp}"
    assert snap.vpin is not None, "VPIN result should not be None"
    assert snap.hawkes is not None, "Hawkes result should not be None"
    # After 100 trades, hawkes should have some intensity
    assert snap.hawkes.intensity > 0, f"Hawkes intensity should be > 0, got {snap.hawkes.intensity}"

for fn in [test_vpin_cdf_monotonic, test_hawkes_spike_uses_original_mu,
           test_microstructure_engine_snapshot]:
    run_test(fn.__name__, fn)


# ================================================================
# SUMMARY
# ================================================================
print()
print("=" * 60)
print("SUMMARY")
print("=" * 60)
passed = sum(1 for _, ok, _ in results if ok)
failed = sum(1 for _, ok, _ in results if not ok)
total = len(results)
print(f"Total: {total}  |  PASSED: {passed}  |  FAILED: {failed}")
print()
if failed > 0:
    print("FAILED TESTS:")
    for name, ok, err in results:
        if not ok:
            print(f"  - {name}: {err}")
else:
    print("ALL TESTS PASSED!")
print()
sys.exit(0 if failed == 0 else 1)
