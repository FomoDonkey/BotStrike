#!/usr/bin/env python3
"""
Exit Analysis -- Compare exit strategies on paper trading data.

Reads trades from the ResearchEngine's in-memory store (via running engine)
or generates a synthetic demo to show how the system works.

Usage:
    python scripts/exit_analysis.py --demo           # Synthetic data demo
    python scripts/exit_analysis.py --from-db        # Analyze DB trades (needs price_path)

The real value comes from running paper trading first:
    python main.py --paper --binance
    (accumulate 50+ trades with price paths)
    Then: python scripts/exit_analysis.py --from-db
"""
from __future__ import annotations

import argparse
import math
import os
import random
import sys
from typing import List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics.exit_optimizer import ExitOptimizer, ExitReport


def generate_synthetic_trades(
    n: int = 100,
    win_rate: float = 0.45,
    avg_sl_bps: float = 30.0,
    seed: int = 42,
) -> List[dict]:
    """Generate synthetic trades with realistic BTC price paths for demo.

    Simulates OFM-style scalping trades on BTC with:
    - 3-second price sampling (matching paper_sim)
    - Realistic intra-trade volatility (~2-5 bps per sample)
    - MAE/MFE tracked from path
    - Some winners that overshoot then retrace (left money on table)
    - Some losers that briefly went positive (could have exited earlier)
    """
    rng = random.Random(seed)
    trades = []
    entry_price = 65000.0

    for i in range(n):
        side = rng.choice(["BUY", "SELL"])
        is_winner = rng.random() < win_rate

        # Generate price path (3s samples, 30-300s hold time)
        hold_samples = rng.randint(10, 100)  # 30-300 seconds
        path: List[Tuple[float, float]] = []

        price = entry_price
        tick_vol = entry_price * 0.0003  # ~3 bps per tick
        best_pnl_bps = 0.0
        worst_pnl_bps = 0.0

        # Create realistic paths:
        # Winners: drift up then maybe retrace
        # Losers: might spike up briefly, then drift down to SL
        if is_winner:
            # Winner: gradual favorable move with noise
            drift = tick_vol * rng.uniform(0.15, 0.4) * (1 if side == "BUY" else -1)
            peak_sample = rng.randint(int(hold_samples * 0.3), int(hold_samples * 0.8))
        else:
            # Loser: might go favorable briefly, then adverse
            drift = tick_vol * rng.uniform(-0.15, -0.35) * (1 if side == "BUY" else -1)
            peak_sample = rng.randint(2, max(3, int(hold_samples * 0.3)))

        for s in range(hold_samples):
            elapsed = (s + 1) * 3.0  # 3-second intervals

            # Price walk with drift reversal after peak
            if s < peak_sample:
                noise = rng.gauss(drift, tick_vol)
            else:
                # Retrace or continue depending on outcome
                if is_winner:
                    noise = rng.gauss(-drift * 0.3, tick_vol * 0.8)  # Slow giveback
                else:
                    noise = rng.gauss(drift * 1.5, tick_vol)  # Accelerating loss

            price += noise
            price = max(price, entry_price * 0.95)  # Floor
            path.append((elapsed, round(price, 2)))

            if side == "BUY":
                pnl_bps = (price - entry_price) / entry_price * 10_000
            else:
                pnl_bps = (entry_price - price) / entry_price * 10_000
            best_pnl_bps = max(best_pnl_bps, pnl_bps)
            worst_pnl_bps = min(worst_pnl_bps, pnl_bps)

        # Actual exit (current strategy)
        exit_price = path[-1][1]
        if side == "BUY":
            actual_pnl_bps = (exit_price - entry_price) / entry_price * 10_000
        else:
            actual_pnl_bps = (entry_price - exit_price) / entry_price * 10_000

        # Force some to hit SL
        if not is_winner and actual_pnl_bps > -avg_sl_bps * 0.5:
            # Push the last price to near SL
            sl_price = entry_price * (1 - avg_sl_bps / 10_000) if side == "BUY" else entry_price * (1 + avg_sl_bps / 10_000)
            path[-1] = (path[-1][0], round(sl_price, 2))
            exit_price = sl_price
            if side == "BUY":
                actual_pnl_bps = (exit_price - entry_price) / entry_price * 10_000
            else:
                actual_pnl_bps = (entry_price - exit_price) / entry_price * 10_000

        # SL/TP levels
        sl_distance = avg_sl_bps + rng.uniform(-5, 5)
        if side == "BUY":
            stop_loss = entry_price * (1 - sl_distance / 10_000)
            take_profit = entry_price * (1 + sl_distance * 2.5 / 10_000)
        else:
            stop_loss = entry_price * (1 + sl_distance / 10_000)
            take_profit = entry_price * (1 - sl_distance * 2.5 / 10_000)

        trades.append({
            "entry_price": entry_price,
            "exit_price": exit_price,
            "side": side,
            "price_path": path,
            "mae_bps": round(abs(worst_pnl_bps), 2),
            "mfe_bps": round(best_pnl_bps, 2),
            "pnl_bps": round(actual_pnl_bps, 2),
            "stop_loss": round(stop_loss, 2),
            "take_profit": round(take_profit, 2),
            "hold_time_sec": path[-1][0],
            "action": "exit_ofm" if i % 3 else "exit_mean_reversion",
        })

    return trades


def print_report(report: ExitReport, optimizer: ExitOptimizer):
    """Print formatted exit analysis report."""
    text = optimizer.format_report(report)
    # Clean Unicode for Windows console
    text = text.replace("\u2500", "-").replace("\u2550", "=")
    print(text)

    # Additional summary
    print()
    print("=" * 70)
    print("  QUANTITATIVE CONCLUSION")
    print("=" * 70)

    m = report.mae_mfe
    c = report.current_stats

    print(f"\n  Current exit captures only {m.avg_capture_ratio:.0%} of available MFE.")
    print(f"  You're leaving {m.avg_unused_mfe_bps:.1f} bps on the table per trade.")

    if report.validated_best:
        vb = report.validation[report.validated_best]
        print(f"\n  RECOMMENDED EXIT: {report.validated_best}")
        print(f"    OOS Expectancy: {vb.oos_expectancy_bps:+.1f} bps/trade")
        print(f"    OOS Profit Factor: {vb.oos_pf:.2f}")
        print(f"    Improvement vs current: {report.validated_improvement_bps:+.1f} bps/trade")
        print(f"    Verdict: {vb.verdict} (passed out-of-sample validation)")
    elif report.best_strategy:
        best = report.shadow_stats[report.best_strategy]
        print(f"\n  BEST IN-SAMPLE EXIT: {report.best_strategy}")
        print(f"    Expectancy: {best.expectancy_bps:+.1f} bps/trade")
        print(f"    Improvement: {best.improvement_bps:+.1f} bps/trade vs current")
        if report.validation:
            v = report.validation.get(report.best_strategy)
            if v and v.is_overfit:
                print(f"    WARNING: OVERFIT detected (IS positive, OOS negative)")
        else:
            print(f"    NOTE: Need {optimizer.MIN_TRADES_FOR_VALIDATION}+ trades for OOS validation")

    # Top 3 strategies
    sorted_shadows = sorted(report.shadow_stats.values(),
                            key=lambda s: s.expectancy_bps, reverse=True)
    if sorted_shadows:
        print("\n  TOP 3 EXIT STRATEGIES:")
        for i, s in enumerate(sorted_shadows[:3]):
            validated = ""
            if report.validation:
                v = report.validation.get(s.name)
                if v:
                    validated = f" [{v.verdict}]"
            print(f"    {i+1}. {s.name:<30} {s.expectancy_bps:>+7.1f} bps  "
                  f"PF={s.profit_factor:.2f}  WR={s.win_rate:.0%}{validated}")

    print("\n" + "=" * 70)


def run_demo():
    """Run exit analysis on synthetic data."""
    print("Generating 100 synthetic BTC scalping trades...")
    print("(WR=45%, avg SL=30bps, 3s price sampling)\n")

    trades = generate_synthetic_trades(n=100, win_rate=0.45, avg_sl_bps=30.0)
    optimizer = ExitOptimizer(fee_bps=14.0)  # 14 bps round-trip (Binance Futures)
    report = optimizer.analyze(trades)
    print_report(report, optimizer)


def run_from_db():
    """Run exit analysis on real paper trading data."""
    from trade_database.repository import TradeRepository

    db_path = "data/trade_database.db"
    if not os.path.exists(db_path):
        print(f"Database not found: {db_path}")
        print("Run paper trading first to generate trades.")
        sys.exit(1)

    repo = TradeRepository(db_path)
    trades = repo.get_trades(source="paper")

    # We need trades with price_path data — these are stored in signal_features
    # which is NOT persisted to DB. Price paths live in ResearchEngine (in-memory).
    # So for DB-only analysis, we can only do MAE/MFE analysis (not shadow simulation).
    exits = [t for t in trades if t.pnl != 0]

    if not exits:
        print("No closed trades found in database.")
        sys.exit(0)

    has_mae = sum(1 for t in exits if t.mae_bps != 0)
    if has_mae == 0:
        print(f"Found {len(exits)} closed trades but NONE have MAE/MFE data.")
        print("This means all trades were recorded before the v2 schema migration.")
        print("\nTo get full exit analysis:")
        print("  1. Run paper trading: python main.py --paper --binance")
        print("  2. Accumulate 50+ trades (new trades will have MAE/MFE + price paths)")
        print("  3. The ResearchEngine will auto-generate exit reports every 20 trades")
        print("  4. Reports are sent to Telegram and logged")
        print(f"\nMeanwhile, run: python scripts/exit_analysis.py --demo")
        sys.exit(0)

    # Build trade dicts compatible with ExitOptimizer
    trade_dicts = []
    for t in exits:
        if t.mae_bps == 0 and t.mfe_bps == 0:
            continue  # Skip pre-migration trades
        side = "BUY" if "BUY" in t.side else "SELL"
        sl = t.entry_price * (1 - 30 / 10_000) if side == "BUY" else t.entry_price * (1 + 30 / 10_000)
        tp = t.entry_price * (1 + 75 / 10_000) if side == "BUY" else t.entry_price * (1 - 75 / 10_000)

        d = {
            "entry_price": t.entry_price or t.price,
            "exit_price": t.exit_price or t.price,
            "side": side,
            "mae_bps": t.mae_bps,
            "mfe_bps": t.mfe_bps,
            "pnl_bps": (t.pnl / (t.entry_price * t.quantity) * 10_000) if t.entry_price and t.quantity else 0,
            "stop_loss": sl,
            "take_profit": tp,
            "hold_time_sec": t.duration_sec,
            "price_path": [],  # Not persisted to DB -- shadow simulation requires in-memory data
        }
        trade_dicts.append(d)

    if not trade_dicts:
        print("No trades with MAE/MFE data found.")
        sys.exit(0)

    print(f"Analyzing {len(trade_dicts)} trades with MAE/MFE data...\n")

    # MAE/MFE analysis only (no shadow simulation without price paths)
    optimizer = ExitOptimizer(fee_bps=14.0)
    report = optimizer.analyze(trade_dicts)

    if report.total_trades_analyzed > 0:
        print_report(report, optimizer)
    else:
        print("Not enough trades with price paths for shadow simulation.")
        print("Price paths are only available in-memory during paper trading.")
        print("\nMAE/MFE Summary from DB:")
        import numpy as np
        maes = [t["mae_bps"] for t in trade_dicts]
        mfes = [t["mfe_bps"] for t in trade_dicts]
        print(f"  MAE: mean={np.mean(maes):.1f}  p50={np.median(maes):.1f}  p90={np.percentile(maes, 90):.1f} bps")
        print(f"  MFE: mean={np.mean(mfes):.1f}  p50={np.median(mfes):.1f}  p90={np.percentile(mfes, 90):.1f} bps")


def main():
    parser = argparse.ArgumentParser(description="BotStrike Exit Analysis")
    parser.add_argument("--demo", action="store_true", help="Run synthetic data demo")
    parser.add_argument("--from-db", action="store_true", help="Analyze real paper trades from DB")
    parser.add_argument("--trades", type=int, default=100, help="Number of synthetic trades (demo mode)")
    parser.add_argument("--wr", type=float, default=0.45, help="Win rate for synthetic trades")
    args = parser.parse_args()

    if args.from_db:
        run_from_db()
    elif args.demo:
        run_demo()
    else:
        print("Usage:")
        print("  python scripts/exit_analysis.py --demo       # Synthetic data demo")
        print("  python scripts/exit_analysis.py --from-db    # Real paper trade data")
        sys.exit(0)


if __name__ == "__main__":
    main()
