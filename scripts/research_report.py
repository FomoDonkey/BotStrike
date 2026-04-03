#!/usr/bin/env python3
"""
Research Report -- Quantitative analysis of paper trading performance.

Reads persisted trades from SQLite and generates a comprehensive report
with per-strategy, per-regime breakdowns and kill-switch recommendations.

Usage:
    python scripts/research_report.py                    # All paper trades
    python scripts/research_report.py --last 50          # Last 50 trades
    python scripts/research_report.py --session abc123   # Specific session
    python scripts/research_report.py --strategy MEAN_REVERSION
    python scripts/research_report.py --json             # JSON output
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trade_database.repository import TradeRepository
from trade_database.models import TradeRecord


# -- Kill Switch Thresholds ---------------------------------------
KILL_MIN_TRADES = 30
KILL_PROFIT_FACTOR = 1.0
KILL_WIN_RATE_FLOOR = 0.20
KILL_MAX_CONSECUTIVE_LOSSES = 10


@dataclass
class StrategyMetrics:
    """Computed metrics for a group of trades (after fees)."""
    name: str = ""
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    total_fees: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    sharpe: float = 0.0
    max_drawdown: float = 0.0
    max_consecutive_losses: int = 0
    avg_hold_sec: float = 0.0
    avg_mae_bps: float = 0.0
    avg_mfe_bps: float = 0.0
    avg_slippage_bps: float = 0.0
    # Kill switch
    kill_triggered: bool = False
    kill_reason: str = ""
    # Regime breakdown
    regime_pnl: Dict[str, float] = field(default_factory=dict)
    regime_count: Dict[str, int] = field(default_factory=dict)


def compute_metrics(trades: List[TradeRecord], name: str = "ALL") -> StrategyMetrics:
    """Compute comprehensive metrics from a list of trade records."""
    m = StrategyMetrics(name=name)

    # Only closed trades (pnl != 0 or trade_type in EXIT/SL/TP)
    closed = [t for t in trades if t.pnl != 0 or t.trade_type in ("EXIT", "SL", "TP")]
    if not closed:
        return m

    m.total_trades = len(closed)
    pnls = [t.pnl for t in closed]
    m.total_pnl = sum(pnls)
    m.total_fees = sum(t.fee for t in closed)

    winners = [t.pnl for t in closed if t.pnl > 0]
    losers = [t.pnl for t in closed if t.pnl < 0]
    m.wins = len(winners)
    m.losses = len(losers)
    m.gross_profit = sum(winners)
    m.gross_loss = sum(losers)

    m.avg_win = (m.gross_profit / m.wins) if m.wins > 0 else 0
    m.avg_loss = (m.gross_loss / m.losses) if m.losses > 0 else 0
    m.win_rate = m.wins / m.total_trades if m.total_trades > 0 else 0
    m.profit_factor = abs(m.gross_profit / m.gross_loss) if m.gross_loss != 0 else 0
    m.expectancy = m.total_pnl / m.total_trades if m.total_trades > 0 else 0

    # Sharpe (annualized, from trade PnLs)
    if len(pnls) >= 2:
        import statistics
        mean_pnl = statistics.mean(pnls)
        std_pnl = statistics.stdev(pnls)
        if std_pnl > 0:
            # Approximate: assume ~8 trades/day (scalping)
            trades_per_year = 8 * 365
            m.sharpe = (mean_pnl / std_pnl) * math.sqrt(trades_per_year)

    # Max drawdown from equity curve
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in closed:
        equity += t.pnl
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
    m.max_drawdown = max_dd

    # Consecutive losses
    max_consec = 0
    current_consec = 0
    for t in closed:
        if t.pnl < 0:
            current_consec += 1
            max_consec = max(max_consec, current_consec)
        else:
            current_consec = 0
    m.max_consecutive_losses = max_consec

    # Averages
    durations = [t.duration_sec for t in closed if t.duration_sec > 0]
    m.avg_hold_sec = (sum(durations) / len(durations)) if durations else 0

    mae_vals = [t.mae_bps for t in closed if t.mae_bps != 0]
    m.avg_mae_bps = (sum(mae_vals) / len(mae_vals)) if mae_vals else 0

    mfe_vals = [t.mfe_bps for t in closed if t.mfe_bps != 0]
    m.avg_mfe_bps = (sum(mfe_vals) / len(mfe_vals)) if mfe_vals else 0

    slip_vals = [t.slippage_bps for t in closed if t.slippage_bps != 0]
    m.avg_slippage_bps = (sum(slip_vals) / len(slip_vals)) if slip_vals else 0

    # Regime breakdown
    for t in closed:
        regime = t.regime or "UNKNOWN"
        m.regime_pnl[regime] = m.regime_pnl.get(regime, 0) + t.pnl
        m.regime_count[regime] = m.regime_count.get(regime, 0) + 1

    # Kill switch evaluation
    if m.total_trades >= KILL_MIN_TRADES:
        if m.profit_factor < KILL_PROFIT_FACTOR and m.profit_factor > 0:
            m.kill_triggered = True
            m.kill_reason = f"PF={m.profit_factor:.2f} < {KILL_PROFIT_FACTOR}"
        elif m.win_rate < KILL_WIN_RATE_FLOOR:
            m.kill_triggered = True
            m.kill_reason = f"WR={m.win_rate:.1%} < {KILL_WIN_RATE_FLOOR:.0%}"
        elif m.max_consecutive_losses >= KILL_MAX_CONSECUTIVE_LOSSES:
            m.kill_triggered = True
            m.kill_reason = f"{m.max_consecutive_losses} consecutive losses >= {KILL_MAX_CONSECUTIVE_LOSSES}"

    return m


def format_report(
    overall: StrategyMetrics,
    by_strategy: Dict[str, StrategyMetrics],
    trades: List[TradeRecord],
) -> str:
    """Format a human-readable research report."""
    lines = []
    lines.append("=" * 70)
    lines.append("  BOTSTRIKE RESEARCH REPORT -- PAPER TRADING ANALYSIS")
    lines.append("=" * 70)
    lines.append(f"  Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(f"  Trades analyzed: {overall.total_trades}")
    if trades:
        t0 = min(t.timestamp for t in trades)
        t1 = max(t.timestamp for t in trades)
        lines.append(f"  Period: {datetime.fromtimestamp(t0, timezone.utc).strftime('%Y-%m-%d %H:%M')} ->"
                      f"{datetime.fromtimestamp(t1, timezone.utc).strftime('%Y-%m-%d %H:%M')}")
        hours = (t1 - t0) / 3600
        lines.append(f"  Duration: {hours:.1f} hours")
    lines.append("")

    # -- PORTFOLIO SUMMARY -----------------------------------------
    lines.append("-" * 70)
    lines.append("  PORTFOLIO SUMMARY (after fees)")
    lines.append("-" * 70)
    lines.append(f"  Total PnL:          ${overall.total_pnl:+.4f}")
    lines.append(f"  Total Fees:         ${overall.total_fees:.4f}")
    lines.append(f"  Net PnL / Trade:    ${overall.expectancy:+.4f}")
    lines.append(f"  Win Rate:           {overall.win_rate:.1%}  ({overall.wins}W / {overall.losses}L)")
    lines.append(f"  Profit Factor:      {overall.profit_factor:.2f}")
    lines.append(f"  Sharpe Ratio:       {overall.sharpe:.2f}")
    lines.append(f"  Max Drawdown:       ${overall.max_drawdown:.4f}")
    lines.append(f"  Max Consec Losses:  {overall.max_consecutive_losses}")
    lines.append(f"  Avg Win:            ${overall.avg_win:+.4f}")
    lines.append(f"  Avg Loss:           ${overall.avg_loss:+.4f}")
    lines.append(f"  Avg Hold Time:      {overall.avg_hold_sec:.0f}s")
    lines.append("")

    # -- EXECUTION QUALITY -----------------------------------------
    lines.append("-" * 70)
    lines.append("  EXECUTION QUALITY")
    lines.append("-" * 70)
    lines.append(f"  Avg Slippage:       {overall.avg_slippage_bps:.2f} bps")
    lines.append(f"  Avg MAE:            {overall.avg_mae_bps:.2f} bps  (max pain before exit)")
    lines.append(f"  Avg MFE:            {overall.avg_mfe_bps:.2f} bps  (max profit before exit)")
    if overall.avg_mfe_bps > 0:
        capture = (overall.expectancy / (overall.avg_mfe_bps / 10000 * 300) * 100) if overall.avg_mfe_bps > 0 else 0
        lines.append(f"  MFE Capture Ratio:  ~{capture:.0f}%  (how much of max profit you keep)")
    lines.append("")

    # -- PER-STRATEGY BREAKDOWN ------------------------------------
    if by_strategy:
        lines.append("-" * 70)
        lines.append("  PER-STRATEGY BREAKDOWN")
        lines.append("-" * 70)
        header = f"  {'Strategy':<25} {'Trades':>6} {'WR':>6} {'PF':>6} {'PnL':>10} {'Expect':>8} {'Sharpe':>7}"
        lines.append(header)
        lines.append("  " + "-" * 68)
        for name, sm in sorted(by_strategy.items()):
            status = " KILL" if sm.kill_triggered else ""
            lines.append(
                f"  {name:<25} {sm.total_trades:>6} {sm.win_rate:>5.1%} {sm.profit_factor:>6.2f} "
                f"${sm.total_pnl:>+9.4f} ${sm.expectancy:>+7.4f} {sm.sharpe:>7.2f}{status}"
            )
            if sm.kill_triggered:
                lines.append(f"    >>> KILL SWITCH: {sm.kill_reason}")
        lines.append("")

    # -- PER-REGIME BREAKDOWN --------------------------------------
    if overall.regime_pnl:
        lines.append("-" * 70)
        lines.append("  PER-REGIME BREAKDOWN")
        lines.append("-" * 70)
        header = f"  {'Regime':<20} {'Trades':>6} {'PnL':>10} {'Avg PnL':>10}"
        lines.append(header)
        lines.append("  " + "-" * 46)
        for regime in sorted(overall.regime_pnl.keys()):
            count = overall.regime_count[regime]
            pnl = overall.regime_pnl[regime]
            avg = pnl / count if count > 0 else 0
            lines.append(f"  {regime:<20} {count:>6} ${pnl:>+9.4f} ${avg:>+9.4f}")
        lines.append("")

    # -- ALERTS ----------------------------------------------------
    alerts = []
    if overall.profit_factor > 0 and overall.profit_factor < 1.0 and overall.total_trades >= 10:
        alerts.append(f"WARNING: Portfolio PF={overall.profit_factor:.2f} < 1.0 -- LOSING MONEY after fees")
    if overall.win_rate < 0.30 and overall.total_trades >= 10:
        alerts.append(f"WARNING: Win rate {overall.win_rate:.1%} is very low")
    if overall.max_drawdown > 15:
        alerts.append(f"WARNING: Max drawdown ${overall.max_drawdown:.2f} is elevated")
    if overall.max_consecutive_losses >= 7:
        alerts.append(f"WARNING: {overall.max_consecutive_losses} consecutive losses -- consider pausing")
    for name, sm in by_strategy.items():
        if sm.kill_triggered:
            alerts.append(f"KILL SWITCH: {name} should be DISABLED -- {sm.kill_reason}")

    if alerts:
        lines.append("-" * 70)
        lines.append("  ALERTS")
        lines.append("-" * 70)
        for a in alerts:
            lines.append(f"  {a}")
        lines.append("")

    # -- RECENT TRADES (last 10) -----------------------------------
    closed = [t for t in trades if t.pnl != 0 or t.trade_type in ("EXIT", "SL", "TP")]
    if closed:
        recent = closed[-10:]
        lines.append("-" * 70)
        lines.append("  LAST 10 TRADES")
        lines.append("-" * 70)
        header = f"  {'Time':<16} {'Strategy':<15} {'Side':<5} {'PnL':>9} {'Hold':>6} {'MAE':>6} {'MFE':>6} {'Slip':>5} {'Regime':<12}"
        lines.append(header)
        lines.append("  " + "-" * 80)
        for t in recent:
            ts = datetime.fromtimestamp(t.timestamp, timezone.utc).strftime("%m-%d %H:%M")
            strat = (t.strategy or "?")[:14]
            side = t.side[:5] if t.side else "?"
            hold = f"{t.duration_sec:.0f}s" if t.duration_sec > 0 else "--"
            mae = f"{t.mae_bps:.1f}" if t.mae_bps != 0 else "--"
            mfe = f"{t.mfe_bps:.1f}" if t.mfe_bps != 0 else "--"
            slip = f"{t.slippage_bps:.1f}" if t.slippage_bps != 0 else "--"
            regime = (t.regime or "?")[:11]
            lines.append(
                f"  {ts:<16} {strat:<15} {side:<5} ${t.pnl:>+8.4f} {hold:>6} {mae:>6} {mfe:>6} {slip:>5} {regime:<12}"
            )
        lines.append("")

    # -- SAMPLE TRADE LOG ------------------------------------------
    if closed:
        sample = closed[-1]
        lines.append("-" * 70)
        lines.append("  SAMPLE TRADE LOG (last trade -- full fields)")
        lines.append("-" * 70)
        for key, val in sample.to_dict().items():
            if isinstance(val, float):
                lines.append(f"  {key:<25} {val:.6f}")
            else:
                lines.append(f"  {key:<25} {val}")
        lines.append("")

    lines.append("=" * 70)
    lines.append("  END OF REPORT")
    lines.append("=" * 70)
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="BotStrike Research Report")
    parser.add_argument("--db", default="data/trade_database.db", help="Database path")
    parser.add_argument("--source", default="paper", help="Trade source filter (paper/live/backtest)")
    parser.add_argument("--session", help="Filter by session ID")
    parser.add_argument("--strategy", help="Filter by strategy name")
    parser.add_argument("--last", type=int, help="Only last N trades")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    if not os.path.exists(args.db):
        print(f"Database not found: {args.db}")
        print("Run paper trading first to generate trades.")
        sys.exit(1)

    repo = TradeRepository(args.db)

    # Fetch trades
    kwargs = {}
    if args.source:
        kwargs["source"] = args.source
    if args.session:
        kwargs["session_id"] = args.session
    if args.strategy:
        kwargs["strategy"] = args.strategy
    if args.last:
        kwargs["limit"] = args.last

    trades = repo.get_trades(**kwargs)
    if args.last and not args.session:
        # get_trades returns oldest first with LIMIT -- we want most recent
        all_trades = repo.get_trades(source=args.source)
        trades = all_trades[-args.last:] if len(all_trades) > args.last else all_trades

    if not trades:
        print("No trades found with the specified filters.")
        print(f"  DB: {args.db}")
        print(f"  Source: {args.source}")
        # Show available sessions
        sessions = repo.get_sessions(limit=5)
        if sessions:
            print("\nAvailable sessions:")
            for s in sessions:
                print(f"  {s.session_id} | {s.source} | trades={s.total_trades} | pnl=${s.total_pnl:.2f}")
        sys.exit(0)

    # Compute overall metrics
    overall = compute_metrics(trades, name="PORTFOLIO")

    # Per-strategy breakdown
    by_strategy: Dict[str, StrategyMetrics] = {}
    strategy_trades: Dict[str, List[TradeRecord]] = defaultdict(list)
    for t in trades:
        if t.strategy:
            strategy_trades[t.strategy].append(t)

    for strat_name, strat_trades in strategy_trades.items():
        by_strategy[strat_name] = compute_metrics(strat_trades, name=strat_name)

    if args.json:
        output = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_trades": overall.total_trades,
            "portfolio": {
                "total_pnl": overall.total_pnl,
                "total_fees": overall.total_fees,
                "win_rate": overall.win_rate,
                "profit_factor": overall.profit_factor,
                "expectancy": overall.expectancy,
                "sharpe": overall.sharpe,
                "max_drawdown": overall.max_drawdown,
                "avg_mae_bps": overall.avg_mae_bps,
                "avg_mfe_bps": overall.avg_mfe_bps,
                "avg_slippage_bps": overall.avg_slippage_bps,
            },
            "strategies": {
                name: {
                    "trades": sm.total_trades,
                    "win_rate": sm.win_rate,
                    "profit_factor": sm.profit_factor,
                    "pnl": sm.total_pnl,
                    "expectancy": sm.expectancy,
                    "sharpe": sm.sharpe,
                    "kill_triggered": sm.kill_triggered,
                    "kill_reason": sm.kill_reason,
                }
                for name, sm in by_strategy.items()
            },
            "regime_breakdown": overall.regime_pnl,
        }
        print(json.dumps(output, indent=2))
    else:
        report = format_report(overall, by_strategy, trades)
        print(report)


if __name__ == "__main__":
    main()
