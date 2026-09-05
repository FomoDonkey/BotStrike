"""How often should the universe be re-picked? (2026-09-05, Edgar's question)

Same harness, same engine selection rule, same costs as tasks/research_trend_multi_2026-09-03.md;
the ONLY thing that changes is when `select_universe` is allowed to run:

  daily      every trading day
  weekly     first day of each ISO week
  monthly    first day of each month  (what the bot does)
  quarterly  first day of each quarter
  yearly     first day of each year
  static     once, at the first date with an eligible pool, then never
  event      monthly, AND any day a held pair's 120 d correlation crosses the cap (re-diversify on
             a correlation shock - the "adapt to the market" idea, made concrete and testable)

Also the correlation cap with the engine rule (0.6 / 0.7 / 0.85 / off), which the first study only
measured with the research-only rule.

Run: py -3.12 scripts/universe_repick_study.py [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import trend_multi_research as R  # noqa: E402
from strategies.trend_daily_model import TrendParams, select_universe  # noqa: E402


def _period_key(dt: pd.Timestamp, period: str):
    if period == "daily":
        return dt.date()
    if period == "weekly":
        iso = dt.isocalendar()
        return (iso[0], iso[1])
    if period == "monthly":
        return (dt.year, dt.month)
    if period == "quarterly":
        return (dt.year, (dt.month - 1) // 3)
    if period == "yearly":
        return dt.year
    if period == "static":
        return "static"
    raise ValueError(period)


def universe_factory(period: str, corr_cap: float):
    def universe(data: Dict[str, pd.DataFrame], dates: pd.DatetimeIndex, n_assets: int, _cap: float) -> pd.DataFrame:
        p = TrendParams(n_assets=n_assets, min_listing_days=R.MIN_HISTORY_DAYS)
        p.corr_cap = corr_cap
        p.corr_window = R.CORR_WINDOW
        frames = {s: d.copy() for s, d in data.items()}
        for d in frames.values():
            if "quote_volume" not in d.columns:
                d["quote_volume"] = d["close"] * d["volume"]
        selected = pd.DataFrame(0.0, index=dates, columns=list(data.keys()))
        current: List[str] = []
        key = None
        changes = 0
        closes = {s: frames[s]["close"] for s in frames}
        for dt in dates:
            repick = False
            if period == "event":
                k = (dt.year, dt.month)
                if key != k:
                    key, repick = k, True
                elif len(current) >= 2:
                    # a held pair correlated above the cap on the latest window -> re-pick today
                    rets = {s: closes[s][closes[s].index <= dt].pct_change(fill_method=None).tail(R.CORR_WINDOW)
                            for s in current}
                    for i, a in enumerate(current):
                        for b in current[i + 1:]:
                            j = pd.concat([rets[a], rets[b]], axis=1).dropna()
                            if len(j) >= 20 and abs(j.iloc[:, 0].corr(j.iloc[:, 1])) > corr_cap:
                                repick = True
                                break
                        if repick:
                            break
            else:
                k = _period_key(dt, period)
                if key != k and not (period == "static" and current):
                    key, repick = k, True
            if repick:
                new = select_universe(frames, dt, p, current=current)
                if period == "static" and not new:
                    key = None                      # nothing eligible yet: try again tomorrow
                if set(new) != set(current):
                    changes += 1
                current = new
            for s in current:
                selected.loc[dt, s] = 1.0 / max(len(current), 1)
        selected.attrs["changes"] = changes
        return selected
    return universe


def run(label: str, data, period: str, corr_cap: float) -> Dict:
    t0 = time.time()
    R.monthly_universe = universe_factory(period, corr_cap)       # the harness reads the module global
    res = R.backtest(data)
    m = R.metrics(res["net"])
    alloc = res["alloc"]
    changes = alloc.attrs.get("changes", 0)
    years = len(res["net"]) / R.ANNUALIZATION
    m.update({"label": label, "period": period, "corr_cap": corr_cap,
              "turnover_yr": float(res["turnover"].mean() * R.ANNUALIZATION),
              "closed_trades": res["closed_trades"], "universe_changes": int(changes),
              "changes_per_year": changes / years if years else 0.0,
              "avg_members": float((alloc > 0).sum(axis=1).mean()), "secs": time.time() - t0})
    print(f"  {label:28s} Sharpe {m['sharpe']:5.2f} | CAGR {m['cagr']*100:6.1f}% | maxDD {m['max_dd']*100:5.1f}% "
          f"| turnover {m['turnover_yr']:5.1f}x/yr | universe changes {changes:4d} ({m['changes_per_year']:.1f}/yr) "
          f"| avg members {m['avg_members']:.1f} | {m['secs']:.0f}s", flush=True)
    return m


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="")
    args = ap.parse_args()
    data = R.load_panel(only_class=["crypto", "metal", "index", "energy"])
    print(f"== panel: {len(data)} markets, {R.MIN_HISTORY_DAYS} d minimum history", flush=True)
    out = []
    print("\n== RE-PICK PERIOD (engine rule, cap 0.85)", flush=True)
    for period in ("monthly", "daily", "weekly", "quarterly", "yearly", "static", "event"):
        out.append(run(f"re-pick {period}", data, period, R.CORR_CAP))
    print("\n== CORRELATION CAP (engine rule, monthly)", flush=True)
    for cap in (0.6, 0.7, 0.95, 1.0):
        out.append(run(f"corr cap {cap}", data, "monthly", cap))
    if args.json:
        Path(args.json).write_text(json.dumps(out, indent=2, default=float), encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
