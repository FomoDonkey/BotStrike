"""
Functional tests for strategy modules.
Tests bug fixes and core logic for base, mean_reversion, trend_following, market_making.
"""
import sys
import os
import time
import math
import traceback

import pandas as pd
import numpy as np

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import SymbolConfig, TradingConfig
from core.types import (
    Signal, MarketRegime, MarketSnapshot, StrategyType, Side, Position,
    OrderBook, OrderBookLevel,
)
from core.microstructure import (
    MicrostructureSnapshot, AvellanedaStoikovEngine, VPINResult, HawkesResult,
)
from strategies.base import BaseStrategy
from strategies.mean_reversion import MeanReversionStrategy
from archive.strategies.trend_following import TrendFollowingStrategy
from archive.strategies.market_making import MarketMakingStrategy


# ── Helpers ──────────────────────────────────────────────────────────

results = []

def run_test(name, fn):
    try:
        fn()
        results.append((name, True, ""))
        print(f"  PASS  {name}")
    except Exception as e:
        results.append((name, False, str(e)))
        print(f"  FAIL  {name}")
        traceback.print_exc()


def make_trading_config(**overrides):
    defaults = dict(risk_per_trade_pct=0.02, initial_capital=100_000)
    defaults.update(overrides)
    return TradingConfig(**defaults)


def make_sym_config(**overrides):
    defaults = dict(
        symbol="BTC-USD",
        leverage=10,
        max_position_usd=20_000,
        mr_zscore_entry=2.0,
        mr_zscore_exit=0.5,
        mr_lookback=5,
        mr_atr_mult_sl=1.5,
        mr_atr_mult_tp=2.5,
        tf_ema_fast=12,
        tf_ema_slow=26,
        tf_atr_mult_trail=2.0,
        tf_volume_filter=1.2,
        mm_order_levels=3,
        mm_order_size_usd=500.0,
        mm_inventory_limit=0.5,
        mm_gamma=0.1,
        mm_kappa=1.5,
        mm_max_spread_bps=100.0,
        mm_base_spread_bps=10.0,
    )
    defaults.update(overrides)
    return SymbolConfig(**defaults)


def make_snapshot(price=50000.0, orderbook=None):
    return MarketSnapshot(
        symbol="BTC-USD",
        timestamp=time.time(),
        price=price,
        mark_price=price,
        index_price=price,
        funding_rate=0.0001,
        volume_24h=1e9,
        open_interest=5e8,
        orderbook=orderbook,
    )


def make_orderbook(mid=50000.0, spread=10.0):
    half = spread / 2
    return OrderBook(
        symbol="BTC-USD",
        timestamp=time.time(),
        bids=[OrderBookLevel(mid - half - i * 5, 1.0) for i in range(5)],
        asks=[OrderBookLevel(mid + half + i * 5, 1.0) for i in range(5)],
    )


def make_df(n=50, close=50000.0, zscore=0.0, atr=500.0, rsi=50.0, vol_pct=0.5,
            adx=30.0, momentum=0.001, vol_ratio=1.5, ema_cross=0, bb_upper=None, bb_lower=None):
    """Build a DataFrame with n rows and indicators on the last row."""
    closes = [close - (n - i) * 10 for i in range(n)]
    closes[-1] = close
    df = pd.DataFrame({
        "open": [c - 5 for c in closes],
        "high": [c + 50 for c in closes],
        "low": [c - 50 for c in closes],
        "close": closes,
        "volume": [1000.0] * n,
    })
    # Set indicators on all rows (for pct_change calculations) then override last
    df["zscore"] = 0.0
    df["atr"] = atr
    df["rsi"] = 50.0
    df["vol_pct"] = vol_pct
    df["adx"] = adx
    df["momentum_20"] = momentum
    df["vol_ratio"] = vol_ratio
    df["ema_cross"] = 0
    df["bb_upper"] = float("inf") if bb_upper is None else bb_upper
    df["bb_lower"] = 0.0 if bb_lower is None else bb_lower

    # Set last-row specific values
    df.loc[df.index[-1], "zscore"] = zscore
    df.loc[df.index[-1], "rsi"] = rsi
    df.loc[df.index[-1], "ema_cross"] = ema_cross
    # For trend following crossover detection, set prev row too
    if n > 1:
        df.loc[df.index[-2], "ema_cross"] = -ema_cross  # opposite for crossover
    return df


def make_position(side=Side.BUY, size=0.1, entry_price=49000.0, mark_price=0.0, notional_override=None):
    pos = Position(
        symbol="BTC-USD",
        side=side,
        size=size,
        entry_price=entry_price,
        mark_price=mark_price if mark_price > 0 else entry_price,
    )
    return pos


# ══════════════════════════════════════════════════════════════════════
# 1. BASE STRATEGY TESTS
# ══════════════════════════════════════════════════════════════════════
print("\n=== BASE STRATEGY ===")


# We need a concrete subclass to test base methods
class _ConcreteStrategy(BaseStrategy):
    def generate_signals(self, *a, **kw):
        return []
    def should_activate(self, regime):
        return True


def test_calc_position_size_price_zero():
    tc = make_trading_config()
    s = _ConcreteStrategy(StrategyType.MEAN_REVERSION, tc)
    result = s._calc_position_size(capital=10000, price=0, stop_loss=49000)
    assert result == 0.0, f"Expected 0 for price=0, got {result}"

run_test("_calc_position_size returns 0 when price=0", test_calc_position_size_price_zero)


def test_calc_position_size_price_negative():
    tc = make_trading_config()
    s = _ConcreteStrategy(StrategyType.MEAN_REVERSION, tc)
    result = s._calc_position_size(capital=10000, price=-100, stop_loss=-200)
    assert result == 0.0, f"Expected 0 for price<0, got {result}"

run_test("_calc_position_size returns 0 when price<0", test_calc_position_size_price_negative)


def test_calc_position_size_normal():
    tc = make_trading_config(risk_per_trade_pct=0.02)
    s = _ConcreteStrategy(StrategyType.MEAN_REVERSION, tc)
    result = s._calc_position_size(capital=100000, price=50000, stop_loss=49000, leverage=10)
    # risk_amount = 100000 * 0.02 = 2000
    # raw_size = 2000 / 1000 = 2.0, raw_notional = 100000
    # friction_bps = 2*2 + 5*2 = 14 bps, friction_cost = 100000 * 14/10000 = 140
    # adjusted_risk = max(2000 - 140, 1000) = 1860
    # size_units = 1860 / 1000 = 1.86
    # max_units = (100000 * 10) / 50000 = 20
    assert result > 0, f"Expected positive size, got {result}"
    assert abs(result - 1.86) < 0.05, f"Expected ~1.86, got {result}"

run_test("_calc_position_size returns valid size for normal inputs", test_calc_position_size_normal)


def test_win_rate_tracking():
    tc = make_trading_config()
    s = _ConcreteStrategy(StrategyType.MEAN_REVERSION, tc)
    s.update_pnl(100.0)   # win
    s.update_pnl(-50.0)   # loss
    s.update_pnl(0.0)     # break-even: should NOT count
    s.update_pnl(200.0)   # win
    assert s.win_count == 2, f"Expected 2 wins, got {s.win_count}"
    assert s.loss_count == 1, f"Expected 1 loss, got {s.loss_count}"
    assert abs(s.win_rate - 2/3) < 0.001, f"Expected ~0.667 win_rate, got {s.win_rate}"

run_test("win_rate tracks wins/losses; break-even pnl=0 not counted", test_win_rate_tracking)


# ══════════════════════════════════════════════════════════════════════
# 2. MEAN REVERSION TESTS
# ══════════════════════════════════════════════════════════════════════
print("\n=== MEAN REVERSION ===")


def test_mr_no_entry_when_position_exists():
    tc = make_trading_config()
    sc = make_sym_config()
    strat = MeanReversionStrategy(tc)
    df = make_df(zscore=-3.0, rsi=20, atr=500)
    snap = make_snapshot()
    pos = make_position()
    signals = strat.generate_signals(
        "BTC-USD", df, snap, MarketRegime.RANGING, sc, 30000, current_position=pos
    )
    entry_signals = [s for s in signals if s.metadata.get("action") != "exit_mean_reversion"]
    assert len(entry_signals) == 0, f"Expected no entry signals with position, got {len(entry_signals)}"

run_test("MR: no entry signal when current_position exists (doubling fix)", test_mr_no_entry_when_position_exists)


def test_mr_no_signal_high_adx():
    tc = make_trading_config()
    sc = make_sym_config()
    strat = MeanReversionStrategy(tc)
    # ADX > 30 should block divergence signals
    df = make_df(n=50, rsi=30, atr=500, adx=40)
    snap = make_snapshot(price=50000)
    signals = strat.generate_signals(
        "BTC-USD", df, snap, MarketRegime.RANGING, sc, 50000, current_position=None
    )
    assert len(signals) == 0, f"Expected 0 signals (ADX too high), got {len(signals)}"

run_test("MR: no signals when ADX > 30 (trending market)", test_mr_no_signal_high_adx)


def test_mr_no_signal_with_position():
    tc = make_trading_config()
    sc = make_sym_config()
    strat = MeanReversionStrategy(tc)
    df = make_df(n=50, rsi=30, atr=500, adx=20)
    snap = make_snapshot(price=50000)
    pos = make_position(side=Side.BUY, size=0.1, entry_price=49000)
    signals = strat.generate_signals(
        "BTC-USD", df, snap, MarketRegime.RANGING, sc, 50000, current_position=pos
    )
    assert len(signals) == 0, f"Expected 0 signals with existing position, got {len(signals)}"

run_test("MR: no signals with existing position (SL/TP only exit)", test_mr_no_signal_with_position)


def test_mr_no_signal_in_breakout():
    tc = make_trading_config()
    sc = make_sym_config()
    strat = MeanReversionStrategy(tc)
    df = make_df(n=50, rsi=30, atr=500, adx=20)
    snap = make_snapshot(price=50000)
    signals = strat.generate_signals(
        "BTC-USD", df, snap, MarketRegime.BREAKOUT, sc, 50000, current_position=None
    )
    assert len(signals) == 0, f"Expected 0 signals in BREAKOUT regime, got {len(signals)}"

run_test("MR: no signals in BREAKOUT regime", test_mr_no_signal_in_breakout)


def test_mr_divergence_metadata():
    tc = make_trading_config()
    sc = make_sym_config()
    strat = MeanReversionStrategy(tc)
    # Divergence strategy should include trigger and rsi in metadata when it fires
    # (Hard to trigger divergence in synthetic data, just verify no crash)
    df = make_df(n=50, rsi=30, atr=500, adx=20)
    snap = make_snapshot(price=50000)
    signals = strat.generate_signals(
        "BTC-USD", df, snap, MarketRegime.RANGING, sc, 50000, current_position=None
    )
    # May or may not generate signals (depends on synthetic divergence pattern)
    # Just verify no crash and correct types
    assert isinstance(signals, list), "Should return a list"

run_test("MR: divergence strategy runs without crash", test_mr_divergence_metadata)


# ══════════════════════════════════════════════════════════════════════
# 3. TREND FOLLOWING TESTS
# ══════════════════════════════════════════════════════════════════════
print("\n=== TREND FOLLOWING ===")


def test_tf_no_entry_when_position_exists():
    tc = make_trading_config()
    sc = make_sym_config(tf_ema_slow=5)
    strat = TrendFollowingStrategy(tc)
    # EMA crossover long setup
    df = make_df(n=50, ema_cross=1, adx=40, momentum=0.01, vol_ratio=2.0)
    snap = make_snapshot(price=50000)
    pos = make_position(side=Side.BUY, size=0.1)
    signals = strat.generate_signals(
        "BTC-USD", df, snap, MarketRegime.TRENDING_UP, sc, 50000, current_position=pos
    )
    # Should only get trailing stop management, not new entry
    entry_signals = [s for s in signals if s.metadata.get("action") != "trailing_stop_hit"]
    assert len(entry_signals) == 0, f"Expected no entry signals with position, got {len(entry_signals)}"

run_test("TF: no entry signal when current_position exists", test_tf_no_entry_when_position_exists)


def test_tf_nan_vol_ratio_bypass():
    tc = make_trading_config()
    sc = make_sym_config(tf_ema_slow=5)
    strat = TrendFollowingStrategy(tc)
    df = make_df(n=50, ema_cross=1, adx=40, momentum=0.01, vol_ratio=float("nan"))
    snap = make_snapshot(price=50000)
    signals = strat.generate_signals(
        "BTC-USD", df, snap, MarketRegime.TRENDING_UP, sc, 50000, current_position=None
    )
    assert len(signals) == 0, f"Expected empty signals for NaN vol_ratio, got {len(signals)}"

run_test("TF: returns empty when vol_ratio is NaN (NaN bypass fix)", test_tf_nan_vol_ratio_bypass)


def test_tf_trailing_stop_notional_zero_fallback():
    tc = make_trading_config()
    sc = make_sym_config(tf_ema_slow=5, tf_atr_mult_trail=2.0)
    strat = TrendFollowingStrategy(tc)

    # Create position with mark_price=0 so notional = size * 0 = 0
    pos = Position(
        symbol="BTC-USD", side=Side.BUY, size=0.5,
        entry_price=50000, mark_price=0.0,
    )
    price = 50000.0
    atr = 500.0

    # Pre-set trailing stop that will be hit
    # Trail distance = 2.0 * 500 = 1000
    # Set stop above current price to trigger it
    strat._trailing_stops["BTC-USD"] = price + 100  # stop at 50100, price is 50000 <= 50100 -> triggers

    df = make_df(n=50, atr=atr, vol_ratio=2.0, adx=30)
    snap = make_snapshot(price=price)

    signals = strat.generate_signals(
        "BTC-USD", df, snap, MarketRegime.TRENDING_UP, sc, 50000, current_position=pos
    )
    assert len(signals) == 1, f"Expected 1 trailing stop exit, got {len(signals)}"
    sig = signals[0]
    assert sig.metadata.get("action") == "trailing_stop_hit"
    # notional is 0 (mark_price=0), so fallback is size * price = 0.5 * 50000 = 25000
    expected_size = pos.size * price
    assert abs(sig.size_usd - expected_size) < 0.01, \
        f"Expected size_usd={expected_size} (fallback), got {sig.size_usd}"

run_test("TF: trailing stop uses size*price fallback when notional=0", test_tf_trailing_stop_notional_zero_fallback)


# ══════════════════════════════════════════════════════════════════════
# 4. MARKET MAKING TESTS
# ══════════════════════════════════════════════════════════════════════
print("\n=== MARKET MAKING ===")


def test_mm_strength_always_gte_001():
    tc = make_trading_config()
    sc = make_sym_config()
    strat = MarketMakingStrategy(tc)

    ob = make_orderbook(mid=50000, spread=10)
    snap = make_snapshot(price=50000, orderbook=ob)
    df = make_df(n=50, atr=500)

    # Create position with huge inventory so inventory_ratio > 1
    # max_inventory = mm_inventory_limit * max_position_usd / price = 0.5 * 20000 / 50000 = 0.2
    # set size = 0.5 >> 0.2 so ratio = 0.5/0.2 = 2.5 >> 1
    pos = make_position(side=Side.BUY, size=0.5, mark_price=50000)

    signals = strat.generate_signals(
        "BTC-USD", df, snap, MarketRegime.RANGING, sc, 50000, current_position=pos
    )
    for sig in signals:
        assert sig.strength >= 0.01, f"strength {sig.strength} < 0.01 for {sig.metadata.get('action')}"

run_test("MM: strength is always >= 0.01 even when inventory_ratio > 1", test_mm_strength_always_gte_001)


def test_mm_paused_when_should_pause():
    tc = make_trading_config()
    sc = make_sym_config()
    strat = MarketMakingStrategy(tc)

    ob = make_orderbook(mid=50000, spread=10)
    snap = make_snapshot(price=50000, orderbook=ob)
    df = make_df(n=50, atr=500)

    # Create MicrostructureSnapshot with extreme conditions (vpin >= 0.9 AND hawkes >= 4.0)
    micro = MicrostructureSnapshot(
        symbol="BTC-USD",
        vpin=VPINResult(vpin=0.95, is_toxic=True),
        hawkes=HawkesResult(is_spike=True, spike_ratio=5.0),
    )

    signals = strat.generate_signals(
        "BTC-USD", df, snap, MarketRegime.RANGING, sc, 50000,
        current_position=None, micro=micro,
    )
    assert len(signals) == 0, f"Expected 0 signals when paused, got {len(signals)}"

run_test("MM: no signals when should_pause_mm is True", test_mm_paused_when_should_pause)


def test_mm_generates_bid_and_ask():
    tc = make_trading_config()
    sc = make_sym_config(mm_order_levels=2)
    strat = MarketMakingStrategy(tc)

    ob = make_orderbook(mid=50000, spread=10)
    snap = make_snapshot(price=50000, orderbook=ob)
    df = make_df(n=50, atr=500)

    signals = strat.generate_signals(
        "BTC-USD", df, snap, MarketRegime.RANGING, sc, 50000, current_position=None,
    )
    buy_sigs = [s for s in signals if s.side == Side.BUY]
    sell_sigs = [s for s in signals if s.side == Side.SELL]
    assert len(buy_sigs) > 0, f"Expected bid signals, got 0"
    assert len(sell_sigs) > 0, f"Expected ask signals, got 0"
    # With 2 levels, expect 2 bids + 2 asks = 4
    assert len(signals) == 4, f"Expected 4 signals (2 levels x 2 sides), got {len(signals)}"

run_test("MM: generates bid and ask signals when conditions met", test_mm_generates_bid_and_ask)


# ══════════════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("\n" + "=" * 60)
    passed = sum(1 for _, ok, _ in results if ok)
    failed = sum(1 for _, ok, _ in results if not ok)
    total = len(results)
    print(f"RESULTS: {passed}/{total} passed, {failed} failed")

    if failed:
        print("\nFailed tests:")
        for name, ok, err in results:
            if not ok:
                print(f"  - {name}: {err}")
        print()
        sys.exit(1)
    else:
        print("All tests passed!")
        sys.exit(0)
