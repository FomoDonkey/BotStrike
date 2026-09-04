"""What does raising the leverage cap actually buy, and what does it cost?

Edgar, 2026-09-04: "me gustaria que el bot usara en agresivo apalancamiento x3 en todo, asumiendo
mas riesgo pero esperando un mayor retorno si sale bien."

The vol-targeting scalar is `target_vol / realised_vol`, clipped at the cap. The cap therefore only
BINDS when realised volatility is low — a market quiet enough that the target asks for more than the
cap allows. Raising it from 2 to 3 changes nothing on a noisy day and lifts size on a calm one, so
the effect is not a uniform "3x on everything": it is more exposure in exactly the regimes where the
strategy has historically been most confident. Whether that is a good trade is a measurement, not an
opinion, so this runs the validated book at both caps across all three profiles, reports how often
the cap is the binding constraint at all, and then shows the dial that DOES scale risk.

    py -3.12 scripts/leverage_cap_study.py
"""
from __future__ import annotations

import math
import sys
from typing import Dict, List

import pandas as pd

sys.path.insert(0, ".")

import scripts.trend_multi_research as R  # noqa: E402

# The universe the book was validated on. Single stocks are excluded on purpose: Strike lists
# today's winners (NVDA, MU, COIN...), so including them imports hindsight selection. Running this
# unfiltered gives 22 markets and Sharpe 1.69 where the validated 14 give 1.92 — a wrong panel is a
# wrong answer, and it is easy to reach for by accident (2026-09-04).
CLASSES = ["crypto", "metal", "index", "energy"]


def run(cap: float, target_vol: float, data: Dict[str, pd.DataFrame]) -> Dict[str, float]:
    original = R.LEVERAGE_CAP
    R.LEVERAGE_CAP = cap
    try:
        return R.metrics(R.backtest(data, target_vol=target_vol)["net"])
    finally:
        R.LEVERAGE_CAP = original


def binding_share(cap: float, target_vol: float, data: Dict[str, pd.DataFrame]) -> float:
    """Fraction of asset-days on which the CAP is what limits the size, rather than the vol target.

    This is the number that decides whether raising the cap can matter at all: if the target rarely
    asks for more than 2x, a 3x ceiling is a ceiling nothing touches.
    """
    hits = total = 0
    for d in data.values():
        ret = d["close"].pct_change(fill_method=None)
        sigma = ret.rolling(R.VOL_WINDOW).std() * math.sqrt(R.ANNUALIZATION)
        raw = (target_vol / sigma).replace([float("inf"), float("-inf")], float("nan")).dropna()
        hits += int((raw > cap).sum())
        total += int(raw.shape[0])
    return hits / total if total else 0.0


def main() -> int:
    data = R.load_panel(only_class=CLASSES)
    print(f"panel: {len(data)} markets (validated classes, single stocks excluded)")
    span = pd.DatetimeIndex(sorted(set().union(*[d.index for d in data.values()])))
    print(f"span : {span[0].date()} -> {span[-1].date()} ({len(span)} days)\n")

    caps: List[float] = [2.0, 3.0]
    profiles = [("conservative", 0.10), ("balanced", 0.20), ("aggressive", 0.30)]

    print(f"{'profile':<14}{'cap':>5}{'Sharpe':>8}{'CAGR':>8}{'vol':>8}{'maxDD':>8}{'skew':>7}"
          f"{'cap binds':>11}")
    rows: Dict = {}
    for name, tv in profiles:
        for cap in caps:
            m = run(cap, tv, data)
            rows[(name, cap)] = m
            share = binding_share(cap, tv, data)
            print(f"{name:<14}{cap:>5.1f}{m['sharpe']:>8.2f}{m['cagr'] * 100:>7.1f}%"
                  f"{m['vol'] * 100:>7.1f}%{m['max_dd'] * 100:>7.1f}%{m['skew']:>7.2f}"
                  f"{share * 100:>10.1f}%")

    print("\n=== what raising the cap to 3 does, profile by profile")
    for name, _tv in profiles:
        a, b = rows[(name, 2.0)], rows[(name, 3.0)]
        d_cagr = (b["cagr"] - a["cagr"]) * 100
        d_dd = (b["max_dd"] - a["max_dd"]) * 100
        ratio = (d_cagr / d_dd) if abs(d_dd) > 1e-9 else float("nan")
        print(f"  {name:<14} CAGR {d_cagr:+5.2f} pts | maxDD {d_dd:+5.2f} pts | "
              f"Sharpe {b['sharpe'] - a['sharpe']:+6.3f} | return per point of extra DD {ratio:5.2f}")

    print("\n=== the dial that ACTUALLY scales risk: target volatility, at cap 3")
    print(f"{'target vol':<14}{'Sharpe':>8}{'CAGR':>8}{'vol':>8}{'maxDD':>8}{'skew':>7}{'cap binds':>11}")
    for tv in (0.30, 0.45, 0.60, 0.80):
        m = run(3.0, tv, data)
        share = binding_share(3.0, tv, data)
        flag = "" if tv <= 0.30 else "   <- beyond the validated range"
        print(f"{tv:<14.2f}{m['sharpe']:>8.2f}{m['cagr'] * 100:>7.1f}%{m['vol'] * 100:>7.1f}%"
              f"{m['max_dd'] * 100:>7.1f}%{m['skew']:>7.2f}{share * 100:>10.1f}%{flag}")

    print("\n=== GO/NO-GO for aggressive at cap 3 (the gates the book was validated against)")
    agg, base = rows[("aggressive", 3.0)], rows[("aggressive", 2.0)]
    checks = [
        ("Sharpe net >= 0.8", agg["sharpe"] >= 0.8),
        ("CAGR > 0", agg["cagr"] > 0),
        ("maxDD < 20 % (aggressive ceiling)", agg["max_dd"] < 0.20),
        ("skew > -0.5", agg["skew"] > -0.5),
        ("Sharpe not materially worse than cap 2", agg["sharpe"] >= base["sharpe"] - 0.10),
        ("more return than at cap 2", agg["cagr"] > base["cagr"]),
    ]
    for label, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    passed = sum(1 for _, ok in checks if ok)
    print(f"\n  {passed}/{len(checks)}")
    print(f"\n  aggressive at cap 3: Sharpe {agg['sharpe']:.2f} | CAGR {agg['cagr'] * 100:.1f}% | "
          f"vol {agg['vol'] * 100:.1f}% | maxDD {agg['max_dd'] * 100:.1f}%")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
