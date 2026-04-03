#!/usr/bin/env python
"""
Full portfolio backtest — MR + OFM on BTC 1m data.
Validates economics after audit #20 fixes.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Suppress debug logging for speed
import logging
logging.disable(logging.DEBUG)
os.environ["LOG_LEVEL"] = "WARNING"

import structlog
structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING),
)

import pandas as pd
import numpy as np
from config.settings import Settings
from backtesting.backtester import Backtester


def run_backtest(label: str, strategies: list, settings: Settings, df: pd.DataFrame) -> dict:
    """Run a single backtest and return summary."""
    print(f"\n{'='*70}")
    print(f"  {label}")
    print(f"  {len(df):,} bars | Strategies: {', '.join(strategies)}")
    print(f"{'='*70}")

    bt = Backtester(settings)
    result = bt.run(df.copy(), symbol="BTC-USD", strategies=strategies)
    summary = result.summary()

    # Print results
    print(f"\n  Total Trades:      {summary.get('total_trades', 0)}")
    print(f"  Net PnL:           ${summary.get('net_pnl', 0):.2f}")
    print(f"  Return:            {summary.get('return_pct', 0):.2f}%")
    print(f"  Win Rate:          {summary.get('win_rate', 0):.1%}")
    print(f"  Profit Factor:     {summary.get('profit_factor', 0):.2f}")
    print(f"  Sharpe Ratio:      {summary.get('sharpe_ratio', 0):.2f}")
    print(f"  Sortino Ratio:     {summary.get('sortino_ratio', 0):.2f}")
    print(f"  Calmar Ratio:      {summary.get('calmar_ratio', 0):.2f}")
    print(f"  Max Drawdown:      {summary.get('max_drawdown', 0):.2%}")
    print(f"  Avg Win:           ${summary.get('avg_win', 0):.4f}")
    print(f"  Avg Loss:          ${summary.get('avg_loss', 0):.4f}")
    print(f"  Expectancy:        ${summary.get('expectancy', 0):.4f}")
    print(f"  Total Fees:        ${summary.get('total_fees', 0):.2f}")
    print(f"  Max Consec Losses: {summary.get('max_consecutive_losses', 0)}")
    print(f"  Avg Duration:      {summary.get('avg_duration_min', 0):.1f} min")
    print(f"  Signals Gen/Exec:  {summary.get('signals_generated', 0)}/{summary.get('signals_executed', 0)}")

    # By strategy
    by_strat = summary.get("by_strategy", {})
    if by_strat:
        print(f"\n  --- By Strategy ---")
        for st, data in by_strat.items():
            print(f"  {st:25s}  trades={data['trades']:4d}  pnl=${data['pnl']:8.2f}  WR={data['win_rate']:.1%}")

    # Equity curve stats
    if result.equity_curve:
        eq = np.array(result.equity_curve)
        print(f"\n  Equity: ${eq[0]:.2f} -> ${eq[-1]:.2f}")
        peak = np.maximum.accumulate(eq)
        dd = (peak - eq) / np.where(peak > 0, peak, 1)
        print(f"  Peak equity: ${peak.max():.2f}")

    return summary


def main():
    settings = Settings()

    # Load data
    df = pd.read_parquet("data/binance/klines/BTC-USD/1m.parquet")
    if df["timestamp"].iloc[0] > 1e12:
        df["timestamp"] = df["timestamp"] / 1000

    # Use last 30 days for speed (~43k bars)
    cutoff = df["timestamp"].iloc[-1] - 30 * 86400
    df_30d = df[df["timestamp"] >= cutoff].reset_index(drop=True)

    # Also prepare last 60 days
    cutoff_60 = df["timestamp"].iloc[-1] - 60 * 86400
    df_60d = df[df["timestamp"] >= cutoff_60].reset_index(drop=True)

    print("\n" + "=" * 70)
    print("  BOTSTRIKE FULL PORTFOLIO BACKTEST — Post Audit #20")
    print(f"  Capital: ${settings.trading.initial_capital}")
    print(f"  Leverage: {settings.symbols[0].leverage}x")
    print(f"  Fees: maker={settings.trading.maker_fee*10000:.1f}bps, taker={settings.trading.taker_fee*10000:.1f}bps")
    print(f"  Slippage: {settings.trading.slippage_bps:.1f}bps")
    print(f"  OFM: TP_RR=3:1, CONFIRM=2 ticks, MAX_HOLD=600s")
    print(f"  MR: Multi-TF divergence, cooldown=300s")
    print(f"  Data: {len(df_30d):,} bars (30d) / {len(df_60d):,} bars (60d)")
    print("=" * 70)

    # === 30-DAY TESTS ===
    print("\n" + "#" * 70)
    print("  30-DAY BACKTEST")
    print("#" * 70)

    # 1. MR only (30d)
    mr_30 = run_backtest("MR ONLY — 30 days", ["MEAN_REVERSION"], settings, df_30d)

    # 2. OFM only (30d)
    ofm_30 = run_backtest("OFM ONLY — 30 days", ["ORDER_FLOW_MOMENTUM"], settings, df_30d)

    # 3. Combined (30d)
    comb_30 = run_backtest("COMBINED MR+OFM — 30 days", ["MEAN_REVERSION", "ORDER_FLOW_MOMENTUM"], settings, df_30d)

    # === 60-DAY TEST (combined only) ===
    print("\n" + "#" * 70)
    print("  60-DAY BACKTEST")
    print("#" * 70)
    comb_60 = run_backtest("COMBINED MR+OFM — 60 days", ["MEAN_REVERSION", "ORDER_FLOW_MOMENTUM"], settings, df_60d)

    # === COMPARISON ===
    print("\n" + "=" * 70)
    print("  COMPARISON SUMMARY (30-day)")
    print("=" * 70)
    print(f"  {'':20s} {'MR Only':>12s} {'OFM Only':>12s} {'Combined':>12s}")
    print(f"  {'─'*56}")
    for key, label, fmt in [
        ("total_trades", "Trades", "d"),
        ("net_pnl", "Net PnL $", ".2f"),
        ("return_pct", "Return %", ".2f"),
        ("win_rate", "Win Rate", ".1%"),
        ("profit_factor", "Profit Factor", ".2f"),
        ("sharpe_ratio", "Sharpe", ".2f"),
        ("max_drawdown", "Max DD", ".1%"),
        ("expectancy", "Expectancy $", ".4f"),
        ("total_fees", "Total Fees $", ".2f"),
    ]:
        mr_v = mr_30.get(key, 0)
        ofm_v = ofm_30.get(key, 0)
        comb_v = comb_30.get(key, 0)
        print(f"  {label:20s} {mr_v:>12{fmt}} {ofm_v:>12{fmt}} {comb_v:>12{fmt}}")

    # === QUANT VERDICT ===
    print("\n" + "=" * 70)
    print("  QUANT VERDICT (30-day combined)")
    print("=" * 70)
    c = comb_30
    net_pnl = c.get("net_pnl", 0)
    wr = c.get("win_rate", 0)
    pf = c.get("profit_factor", 0)
    sharpe = c.get("sharpe_ratio", 0)
    max_dd = c.get("max_drawdown", 0)
    trades = c.get("total_trades", 0)
    fees = c.get("total_fees", 0)
    expectancy = c.get("expectancy", 0)

    issues = []
    passes = []

    if net_pnl > 0:
        passes.append(f"  [PASS] Net profitable: ${net_pnl:.2f} ({c.get('return_pct', 0):.1f}%)")
    else:
        issues.append(f"  [FAIL] Net negative: ${net_pnl:.2f}")

    if trades >= 20:
        passes.append(f"  [PASS] Sufficient trades: {trades}")
    else:
        issues.append(f"  [WARN] Low trade count: {trades} (need 20+)")

    if wr >= 0.375:
        passes.append(f"  [PASS] Win rate {wr:.1%} >= OFM breakeven 37.5%")
    else:
        issues.append(f"  [WARN] Win rate {wr:.1%} < OFM breakeven 37.5%")

    if pf >= 1.0:
        passes.append(f"  [PASS] Profit Factor {pf:.2f} >= 1.0")
    else:
        issues.append(f"  [FAIL] Profit Factor {pf:.2f} < 1.0")

    if max_dd <= 0.10:
        passes.append(f"  [PASS] Max DD {max_dd:.1%} <= 10% limit")
    elif max_dd <= 0.15:
        issues.append(f"  [WARN] Max DD {max_dd:.1%} between 10-15%")
    else:
        issues.append(f"  [FAIL] Max DD {max_dd:.1%} > 15% — too risky")

    if expectancy > 0:
        passes.append(f"  [PASS] Positive expectancy: ${expectancy:.4f}/trade")
    else:
        issues.append(f"  [FAIL] Negative expectancy: ${expectancy:.4f}/trade")

    for p in passes:
        print(p)
    for i in issues:
        print(i)

    print()
    if not [x for x in issues if "[FAIL]" in x]:
        print("  >>> VERDICT: System is viable for paper trading <<<")
    else:
        print("  >>> VERDICT: Issues found — analyze and fix before going live <<<")

    # 60-day comparison
    print(f"\n  60-day Combined: ${comb_60.get('net_pnl', 0):.2f} PnL, "
          f"{comb_60.get('win_rate', 0):.1%} WR, "
          f"{comb_60.get('total_trades', 0)} trades, "
          f"Sharpe {comb_60.get('sharpe_ratio', 0):.2f}")


if __name__ == "__main__":
    main()
