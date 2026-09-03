"""Measure Strike's own funding and spread history per market (roadmap P0.2).

Strike publishes both on its stats API — a base path the old client had wrong, so the bot had been
guessing these costs from Binance. Real numbers matter: the trend book is long-only, so funding is
a permanent drag, and the spread sets the floor of the execution cost the research assumes.

    py -3.12 scripts/strike_market_stats.py                 # print + write data/strike_costs.json
    py -3.12 scripts/strike_market_stats.py --days 90

Endpoints (public, no auth):
    GET /stat/v1/stats/coin/history/funding?symbol=&days=   -> [[ts_ms, rate_per_8h], ...]
    GET /stat/v1/stats/coin/history/spread?symbol=&interval= -> [[ts_ms, spread_abs, ratio_pct], ...]
`ratio` is a PERCENT of price (verified against the live book: spread/(ratio/100) reproduces the
mark price), so basis points = ratio * 100.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import urllib.request
from typing import Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "strike_costs.json")
BASE = os.getenv("BOTSTRIKE_STRIKE_STATS", "https://api.strikefinance.org/stat/v1/stats/coin")
UA = "botstrike-research/1.0"
DEFAULT_MARKETS = ["BTC-USD", "ETH-USD", "SOL-USD", "ADA-USD", "XRP-USD", "BNB-USD", "ZEC-USD",
                   "XAU-USD", "XAG-USD", "SP500-USD", "NAS100-USD", "WTI-USD"]


def _get(path: str, attempts: int = 3, **params) -> Optional[dict]:
    q = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{BASE}{path}?{q}"
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except Exception as e:  # noqa: BLE001
            if i == attempts - 1:
                print(f"  ! {path} {params}: {type(e).__name__}", file=sys.stderr)
                return None
            time.sleep(2 * (i + 1))
    return None


def funding_stats(symbol: str, days: int = 90) -> Optional[Dict]:
    d = _get("/history/funding", symbol=symbol, days=days)
    rows = (d or {}).get("data") or []
    if len(rows) < 10:
        return None
    rates = [float(r[1]) for r in rows]
    span_h = max((rows[-1][0] - rows[0][0]) / 3_600_000, 1.0)
    return {
        "periods": len(rates),
        "days": round(span_h / 24, 1),
        "annualized_pct": round(sum(rates) / span_h * 24 * 365, 6),   # fraction of notional per year
        "median": statistics.median(rates),
        "p90": sorted(rates)[int(0.9 * len(rates))],
        "share_positive": round(sum(1 for r in rates if r > 0) / len(rates), 4),
    }


def spread_stats(symbol: str, interval: str = "1d") -> Optional[Dict]:
    d = _get("/history/spread", symbol=symbol, interval=interval)
    rows = [r for r in ((d or {}).get("data") or []) if r[2] is not None]
    if not rows:
        return None
    bps = sorted(float(r[2]) * 100.0 for r in rows)          # ratio is a percent -> bps
    return {"days": len(bps), "median_bps": round(statistics.median(bps), 3),
            "p90_bps": round(bps[int(0.9 * len(bps))], 3), "worst_bps": round(bps[-1], 3),
            "half_spread_bps": round(statistics.median(bps) / 2, 3)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--markets", default=",".join(DEFAULT_MARKETS))
    ap.add_argument("--taker-bps", type=float, default=4.5, help="venue taker fee in bps")
    args = ap.parse_args()
    markets = [m.strip().upper() for m in args.markets.split(",") if m.strip()]

    out: Dict[str, Dict] = {}
    print(f"=== STRIKE FUNDING, {args.days} d (positive = LONGS PAY)")
    print(f"{'market':11s} {'periods':>8s} {'ann/yr':>9s} {'% periods +':>12s}")
    for m in markets:
        f = funding_stats(m, args.days)
        if f:
            out.setdefault(m, {})["funding"] = f
            print(f"{m:11s} {f['periods']:8d} {f['annualized_pct']*100:8.2f}% {f['share_positive']*100:11.0f}%")
        else:
            print(f"{m:11s}     no funding data")

    print(f"\n=== STRIKE SPREAD (bps of price) and total cost per side with a {args.taker_bps} bps taker fee")
    print(f"{'market':11s} {'median':>8s} {'p90':>8s} {'half':>7s} {'cost/side':>10s}")
    for m in markets:
        s = spread_stats(m)
        if s:
            cost = round(s["half_spread_bps"] + args.taker_bps, 2)
            s["cost_per_side_bps"] = cost
            out.setdefault(m, {})["spread"] = s
            print(f"{m:11s} {s['median_bps']:7.2f} {s['p90_bps']:7.2f} {s['half_spread_bps']:6.2f} {cost:9.2f}")
        else:
            print(f"{m:11s}     no spread data")

    costs = [v["spread"]["cost_per_side_bps"] for v in out.values() if "spread" in v]
    fundings = [v["funding"]["annualized_pct"] for v in out.values() if "funding" in v]
    summary = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "days": args.days, "taker_bps": args.taker_bps,
        "median_cost_per_side_bps": round(statistics.median(costs), 2) if costs else None,
        "worst_cost_per_side_bps": round(max(costs), 2) if costs else None,
        "median_funding_annual": round(statistics.median(fundings), 4) if fundings else None,
        "markets": out,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(summary, open(OUT, "w", encoding="utf-8"), indent=1)
    print(f"\nmedian cost/side {summary['median_cost_per_side_bps']} bps, worst {summary['worst_cost_per_side_bps']} bps")
    print(f"median funding {summary['median_funding_annual']*100:.2f} %/yr" if fundings else "")
    print(f"written to {OUT}")
    return 0


def load_costs() -> Dict:
    try:
        return json.load(open(OUT, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


if __name__ == "__main__":
    raise SystemExit(main())
