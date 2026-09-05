"""Edge monitor — per-strategy statistics with a verdict (2026-09-02).

This is the panel the audit found missing: it answers "does this strategy have an
edge?" from the bot's own closed trades, and it is the input of the automatic kill.

Definitions (per closed trade, from the trade DB):
  gross      = pnl + fee                    (TradeRecord.pnl is NET of fees)
  notional   = entry_price × quantity       (falls back to exit price × quantity)
  ret_bps    = gross / notional × 1e4
  t_stat     = mean(ret_bps) / (std(ret_bps) / sqrt(n))
  fee_share  = Σ fee / Σ gross of the WINNING trades  (1.0 when there are no winners)
Verdict:
  insufficient  n < min_trades
  kill          n >= min_trades and (t_stat <= t_kill or fee_share >= fee_kill)
  warn          n >= min_trades/2 and t_stat <= -1.0
  ok            otherwise
The window is the LAST `window` closed trades so a repaired strategy can recover.
"""
from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Optional

import structlog

logger = structlog.get_logger(__name__)

VERDICT_INSUFFICIENT = "insufficient"
VERDICT_OK = "ok"
VERDICT_WARN = "warn"
VERDICT_KILL = "kill"


REBALANCE_ORDER_PREFIXES = ("trend_rebalance_",)


def is_rebalance_row(t: Any) -> bool:
    """True for a partial re-alignment of an open position (the trend book's drift trims)."""
    oid = str(getattr(t, "order_id", "") or "")
    return oid.startswith(REBALANCE_ORDER_PREFIXES)


def _stats_for(trades: List[Any], min_trades: int, t_kill: float, fee_kill: float) -> Dict[str, Any]:
    n = len(trades)
    if n == 0:
        return {"n": 0, "wins": 0, "win_rate": 0.0, "net_pnl": 0.0, "gross_pnl": 0.0, "fees": 0.0,
                "mean_gross_bps": 0.0, "se_bps": 0.0, "t_stat": 0.0, "profit_factor": 0.0,
                "fee_share": 0.0, "expectancy_usd": 0.0, "avg_hold_min": 0.0,
                "verdict": VERDICT_INSUFFICIENT, "reason": f"0 < {min_trades} trades"}
    rets: List[float] = []
    net = gross = fees = 0.0
    wins = 0
    gross_wins = 0.0
    win_sum = loss_sum = 0.0
    hold = 0.0
    for t in trades:
        pnl = float(t.pnl or 0.0)
        fee = float(t.fee or 0.0)
        g = pnl + fee
        qty = float(t.quantity or 0.0)
        entry = float(t.entry_price or 0.0) or float(t.price or 0.0)
        notional = entry * qty
        if notional > 0:
            rets.append(g / notional * 1e4)
        net += pnl
        gross += g
        fees += fee
        if pnl > 0:
            wins += 1
            win_sum += pnl
        elif pnl < 0:
            loss_sum += -pnl
        if g > 0:
            gross_wins += g
        hold += float(t.duration_sec or 0.0)
    m = len(rets)
    mean = sum(rets) / m if m else 0.0
    var = sum((r - mean) ** 2 for r in rets) / (m - 1) if m > 1 else 0.0
    se = math.sqrt(var / m) if m > 1 and var > 0 else 0.0
    if se > 0:
        t_stat = mean / se
    elif m > 1 and mean != 0:
        t_stat = 99.0 if mean > 0 else -99.0   # identical returns: zero variance, sign is certain
    else:
        t_stat = 0.0
    pf = (win_sum / loss_sum) if loss_sum > 0 else (float("inf") if win_sum > 0 else 0.0)
    fee_share = (fees / gross_wins) if gross_wins > 0 else (1.0 if fees > 0 else 0.0)
    if n < min_trades:
        verdict, reason = VERDICT_INSUFFICIENT, f"{n} < {min_trades} trades"
    elif t_stat <= t_kill:
        verdict, reason = VERDICT_KILL, f"t-stat {t_stat:.2f} <= {t_kill:.2f}"
    elif fee_share >= fee_kill:
        verdict, reason = VERDICT_KILL, f"fees eat {fee_share:.0%} of gross wins (>= {fee_kill:.0%})"
    elif n >= max(min_trades // 2, 1) and t_stat <= -1.0:
        verdict, reason = VERDICT_WARN, f"t-stat {t_stat:.2f}"
    else:
        verdict, reason = VERDICT_OK, ""
    return {
        "n": n, "wins": wins, "win_rate": round(wins / n, 4),
        "net_pnl": round(net, 4), "gross_pnl": round(gross, 4), "fees": round(fees, 4),
        "mean_gross_bps": round(mean, 2), "se_bps": round(se, 2), "t_stat": round(t_stat, 2),
        "profit_factor": round(pf, 2) if pf != float("inf") else 99.0,
        "fee_share": round(min(fee_share, 9.99), 4),
        "expectancy_usd": round(net / n, 4), "avg_hold_min": round(hold / n / 60.0, 1),
        "verdict": verdict, "reason": reason,
    }


def compute_edge_stats(trade_repo, source: str = "paper", window: int = 200,
                       min_trades: int = 100, t_kill: float = -2.0, fee_kill: float = 0.5,
                       strategies: Optional[List[str]] = None) -> Dict[str, Any]:
    """Edge statistics per strategy over the last `window` closed trades. Never raises."""
    out: Dict[str, Any] = {
        "window": window, "min_trades": min_trades, "t_stat_kill": t_kill,
        "fee_share_kill": fee_kill, "computed_at": time.time(), "strategies": {},
    }
    try:
        trades = trade_repo.get_trades(source=source)
    except Exception as e:
        logger.warning("edge_stats_unavailable", error=str(e), error_type=type(e).__name__)
        trades = []
    closes = [t for t in trades if t.trade_type and t.trade_type not in ("ENTRY", "FUNDING")]
    # A rebalance trim is housekeeping, not a closed trade: the trend book trims a winner after it
    # grew and adds to a loser after it shrank, so counting trims as trades biased the statistic
    # towards the last move and drowned the ~90 real exits a year in drift rows (2026-09-05).
    trims = [t for t in closes if is_rebalance_row(t)]
    closes = [t for t in closes if not is_rebalance_row(t)]
    closes.sort(key=lambda t: float(t.timestamp or 0.0))
    out["rebalance_rows_excluded"] = len(trims)
    by_strategy: Dict[str, List[Any]] = {}
    for t in closes:
        by_strategy.setdefault(t.strategy or "UNKNOWN", []).append(t)
    names = list(strategies or [])
    for s in by_strategy:
        if s not in names:
            names.append(s)
    for name in names:
        rows = by_strategy.get(name, [])[-window:]
        out["strategies"][name] = _stats_for(rows, min_trades, t_kill, fee_kill)
    return out
