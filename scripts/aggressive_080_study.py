"""Everything that has to be true before the aggressive profile runs at target vol 0.80.

Edgar, 2026-09-04: he wants aggressive to be the top row of the menu — +421 $/yr on a 1,014 $ book,
with a worst historical drawdown of 279 $. That is 0.80 target volatility, well outside the
0.10-0.30 range the book was validated on, so it does not get set on the strength of one CAGR
number. This measures the whole thing: the gates, the stress tests, and — the part that actually
decides whether the bot can RUN there — the distribution of daily and weekly losses, which is what
the circuit breaker has to be set above or it halts the bot on an ordinary bad week.

    py -3.12 scripts/aggressive_080_study.py
"""
from __future__ import annotations

import math
import sys
from typing import Dict

import numpy as np
import pandas as pd

sys.path.insert(0, ".")

import scripts.trend_multi_research as R  # noqa: E402

CLASSES = ["crypto", "metal", "index", "energy"]
CAP = 3.0
LEVELS = [0.30, 0.45, 0.60, 0.80]


def run(target_vol: float, data, **kw) -> Dict:
    original = R.LEVERAGE_CAP
    R.LEVERAGE_CAP = CAP
    try:
        res = R.backtest(data, target_vol=target_vol, **kw)
        m = R.metrics(res["net"])
        m["net"] = res["net"]
        return m
    finally:
        R.LEVERAGE_CAP = original


def loss_tail(net: pd.Series) -> Dict[str, float]:
    """What a bad day and a bad week actually look like. The circuit breaker lives here."""
    daily = net.dropna()
    weekly = net.rolling(5).sum().dropna()
    dd = (1 + net.fillna(0)).cumprod()
    drawdown = (dd / dd.cummax() - 1.0)
    return {
        "worst_day": float(daily.min()),
        "p01_day": float(np.percentile(daily, 1)),
        "worst_week": float(weekly.min()),
        "p01_week": float(np.percentile(weekly, 1)),
        "worst_dd": float(drawdown.min()),
        "days_in_dd_over_10pct": int((drawdown < -0.10).sum()),
        "longest_dd_days": int(_longest_underwater(drawdown)),
    }


def _longest_underwater(dd: pd.Series) -> int:
    best = cur = 0
    for v in dd.values:
        cur = cur + 1 if v < -1e-9 else 0
        best = max(best, cur)
    return best


def main() -> int:
    data = R.load_panel(only_class=CLASSES)
    span = pd.DatetimeIndex(sorted(set().union(*[d.index for d in data.values()])))
    print(f"panel: {len(data)} markets · {span[0].date()} -> {span[-1].date()} ({len(span)} days) · cap {CAP}x\n")

    print("=== the menu, measured")
    print(f"{'target vol':<12}{'Sharpe':>8}{'CAGR':>8}{'vol':>8}{'maxDD':>8}{'skew':>7}"
          f"{'worst day':>11}{'worst week':>12}{'longest DD':>12}")
    rows = {}
    for tv in LEVELS:
        m = run(tv, data)
        t = loss_tail(m["net"])
        rows[tv] = (m, t)
        print(f"{tv:<12.2f}{m['sharpe']:>8.2f}{m['cagr']*100:>7.1f}%{m['vol']*100:>7.1f}%"
              f"{m['max_dd']*100:>7.1f}%{m['skew']:>7.2f}{t['worst_day']*100:>10.2f}%"
              f"{t['worst_week']*100:>11.2f}%{t['longest_dd_days']:>10}d")

    tv = 0.80
    m, t = rows[tv]
    print(f"\n=== target vol {tv} in detail — this is what Edgar asked for")
    eq = 1014.55
    print(f"  on {eq:,.2f} $:  +{eq*m['cagr']:,.0f} $/yr expected · worst drawdown seen "
          f"{eq*m['max_dd']:,.0f} $ · worst single day {eq*abs(t['worst_day']):,.0f} $ "
          f"· worst week {eq*abs(t['worst_week']):,.0f} $")
    print(f"  time underwater: longest stretch {t['longest_dd_days']} days, "
          f"{t['days_in_dd_over_10pct']} days spent more than 10 % below the peak")

    print("\n=== stress: does it still hold at this size?")
    for label, kw in (("costs 8 bps/side (base)", {}),
                      ("costs 15 bps/side", {"cost_bps": 15.0}),
                      ("costs 25 bps/side", {"cost_bps": 25.0}),
                      ("funding x3", {"funding_mult": 3.0})):
        mm = run(tv, data, **kw)
        print(f"  {label:<26} Sharpe {mm['sharpe']:5.2f} | CAGR {mm['cagr']*100:6.1f}% "
              f"| maxDD {mm['max_dd']*100:5.1f}%")

    print("\n=== the loss ladder this level NEEDS")
    # The breaker must sit clear of an ORDINARY bad day/week at this size, or it halts the bot on
    # noise. Set from the measured tail with a margin, not from a ratio copied off a calmer profile.
    daily_limit = math.ceil(abs(t["worst_day"]) * 1.25 * 100) / 100
    weekly_limit = math.ceil(abs(t["worst_week"]) * 1.20 * 100) / 100
    dd_limit = math.ceil(abs(t["max_dd" if "max_dd" in t else "worst_dd"]) * 1.30 * 100) / 100
    dd_limit = math.ceil(abs(m["max_dd"]) * 1.30 * 100) / 100
    print(f"  daily  {daily_limit*100:5.1f}%  (worst day seen {abs(t['worst_day'])*100:.2f}% x1.25)")
    print(f"  weekly {weekly_limit*100:5.1f}%  (worst week seen {abs(t['worst_week'])*100:.2f}% x1.20)")
    print(f"  peak   {dd_limit*100:5.1f}%  (worst drawdown seen {m['max_dd']*100:.2f}% x1.30)")
    print(f"  -> on {eq:,.2f} $: the bot halts itself after {eq*daily_limit:,.0f} $ in a day, "
          f"{eq*weekly_limit:,.0f} $ in a week, or {eq*dd_limit:,.0f} $ below its peak")

    print("\n=== gates (the same ones the book was validated against)")
    base = rows[0.30][0]
    checks = [
        ("Sharpe net >= 0.8", m["sharpe"] >= 0.8),
        ("CAGR > 0", m["cagr"] > 0),
        ("skew > -0.5", m["skew"] > -0.5),
        ("survives 25 bps/side", run(tv, data, cost_bps=25.0)["cagr"] > 0),
        ("survives funding x3", run(tv, data, funding_mult=3.0)["cagr"] > 0),
        ("Sharpe within 0.15 of the validated 0.30 level", m["sharpe"] >= base["sharpe"] - 0.15),
    ]
    for label, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    passed = sum(1 for _, ok in checks if ok)
    print(f"\n  {passed}/{len(checks)}")
    print("\n  NOTE: passing these does NOT make 0.80 'validated'. The research range is 0.10-0.30;")
    print("  this level is chosen deliberately, with the drawdown it implies stated in dollars.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
