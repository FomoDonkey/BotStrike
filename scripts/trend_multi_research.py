"""Multi-asset trend research on the Strike universe (roadmap P1).

Same model that passed 11/11 on crypto (research §11.2: Donchian ensemble, never-falling trailing
stop, 20 % vol target on a 90-day window, signal at close t executed at the OPEN of t+1), now over
everything Strike lists that has real daily history: crypto, gold, silver, S&P 500, Nasdaq 100,
WTI and single stocks (scripts/download_daily.py).

Why this is the highest-value change: time-series momentum is the one family with 40 years of
out-of-sample evidence, and its edge comes from DIVERSIFICATION across uncorrelated markets, not
from better parameters. Crypto-only, the same model has a 12.6 % drawdown; the question this
script answers is whether adding metals, indices, energy and equities lowers it without giving
back return, AFTER costs and AFTER funding.

    py -3.12 scripts/trend_multi_research.py                 # base + variants + GO/NO-GO
    py -3.12 scripts/trend_multi_research.py --quick         # base only

Every configuration evaluated is counted as a trial and fed to the deflated Sharpe ratio, exactly
like the crypto study, so the verdict cannot be inflated by searching.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.download_daily import OUT_DIR, load_daily  # noqa: E402
from strategies.trend_daily_model import TrendParams, select_universe  # noqa: E402

# ── Specification (identical to the validated crypto study unless stated) ──
LOOKBACKS = [5, 10, 20, 30, 60, 90]
TARGET_VOL = 0.20
VOL_WINDOW = 90
ANNUALIZATION = 365          # the portfolio calendar is 365 days (crypto trades every day)
LEVERAGE_CAP = 2.0
N_ASSETS = 6                 # more markets than crypto-only (3): the point is diversification
REBALANCE_THRESHOLD = 0.20
COST_BPS = 8.0               # Strike taker 4.5 bps + slippage; stressed later
MIN_HISTORY_DAYS = 365
CORR_WINDOW = 120
CORR_CAP = 0.85              # drop a candidate correlated above this with an already-picked market

# Annualized funding applied to LONG exposure. Class fallback only: the real numbers come from the
# VENUE itself (data/strike_costs.json, scripts/strike_market_stats.py — Strike's own 90-day funding
# history). Measured 2026-09-03: BTC +8.6 %, ETH +7.8 %, SOL +7.1 %, ADA +10.9 %, XAG +15.1 %,
# SP500 +8.4 %, XAU +0.9 %/yr paid by longs, while NAS100 -3.7 % and WTI -15.7 % PAY the longs.
# The old class averages (3-4 %) were guesses from Binance and understated the crypto cost by ~2x.
FUNDING_ANNUAL = {"crypto": 0.08, "metal": 0.08, "index": 0.04, "energy": -0.05, "equity": 0.06}


def measured_funding() -> Dict[str, float]:
    """Per-market annualized funding measured on Strike; {} when the file has not been generated."""
    try:
        from scripts.strike_market_stats import load_costs
        data = load_costs().get("markets", {})
        return {m: float(v["funding"]["annualized_pct"]) for m, v in data.items() if "funding" in v}
    except Exception:  # noqa: BLE001
        return {}

ASSET_CLASS = {
    "BTC-USD": "crypto", "ETH-USD": "crypto", "SOL-USD": "crypto", "ADA-USD": "crypto",
    "XRP-USD": "crypto", "BNB-USD": "crypto", "ZEC-USD": "crypto", "NEAR-USD": "crypto",
    "HYPE-USD": "crypto",
    "XAU-USD": "metal", "XAG-USD": "metal", "WTI-USD": "energy",
    "SP500-USD": "index", "NAS100-USD": "index",
    "NVDA-USD": "equity", "TSLA-USD": "equity", "GOOGL-USD": "equity", "COIN-USD": "equity",
    "MU-USD": "equity", "SNDK-USD": "equity", "CRCL-USD": "equity", "AAOI-USD": "equity",
    "SKHYNIX-USD": "equity",
}


# ── data ──────────────────────────────────────────────────────────────────
def load_panel(only_class: Optional[List[str]] = None) -> Dict[str, pd.DataFrame]:
    """Daily frames indexed by UTC date, forward-filled onto the union calendar.

    TradFi markets close at weekends: on a closed day the position is simply held and the
    open-to-open return is 0, which is exactly what a perp position does (the perp trades but
    tracks a stale index; treating it as flat is the conservative choice for the SIGNAL, and the
    execution study is done separately on Strike marks)."""
    raw = load_daily()
    if not raw:
        raise SystemExit(f"no daily data in {OUT_DIR} — run: py -3.12 scripts/download_daily.py")
    out: Dict[str, pd.DataFrame] = {}
    for sym, df in raw.items():
        if only_class and ASSET_CLASS.get(sym) not in only_class:
            continue
        d = df.copy()
        d.index = pd.to_datetime(d["timestamp"], unit="s", utc=True).dt.normalize()
        d = d[~d.index.duplicated(keep="last")].sort_index()
        if len(d) < MIN_HISTORY_DAYS:
            continue
        out[sym] = d[["open", "high", "low", "close", "volume"]]
    return out


# ── model (identical maths to strategies/trend_daily_model.py) ────────────
def sub_strategy_positions(close: pd.Series, n: int) -> pd.Series:
    roll_max = close.rolling(n).max()
    mid = 0.5 * (roll_max + close.rolling(n).min())
    pos = np.zeros(len(close))
    in_pos, cur_stop = False, np.nan
    c, m, rmax = close.to_numpy(), mid.to_numpy(), roll_max.to_numpy()
    for i in range(len(c)):
        if np.isnan(m[i]):
            continue
        if not in_pos:
            if c[i] >= rmax[i]:
                in_pos, cur_stop = True, m[i]
        else:
            cur_stop = max(cur_stop, m[i])
            if c[i] <= cur_stop:
                in_pos, cur_stop = False, np.nan
        pos[i] = 1.0 if in_pos else 0.0
    return pd.Series(pos, index=close.index)


def sub_strategy_positions_ls(close: pd.Series, n: int) -> pd.Series:
    """Long/short variant: symmetric Donchian breakout (recorded as a separate trial)."""
    roll_max, roll_min = close.rolling(n).max(), close.rolling(n).min()
    mid = 0.5 * (roll_max + roll_min)
    pos = np.zeros(len(close))
    state, cur_stop = 0, np.nan
    c, m, rmax, rmin = close.to_numpy(), mid.to_numpy(), roll_max.to_numpy(), roll_min.to_numpy()
    for i in range(len(c)):
        if np.isnan(m[i]):
            continue
        if state == 0:
            if c[i] >= rmax[i]:
                state, cur_stop = 1, m[i]
            elif c[i] <= rmin[i]:
                state, cur_stop = -1, m[i]
        elif state == 1:
            cur_stop = max(cur_stop, m[i])
            if c[i] <= cur_stop:
                state, cur_stop = 0, np.nan
        else:
            cur_stop = min(cur_stop, m[i])
            if c[i] >= cur_stop:
                state, cur_stop = 0, np.nan
        pos[i] = float(state)
    return pd.Series(pos, index=close.index)


def asset_weight(close: pd.Series, lookbacks: List[int], target_vol: float, vol_window: int,
                 long_short: bool = False, pos_fn=None, df: Optional[pd.DataFrame] = None,
                 symbol: str = "") -> pd.Series:
    """Target weight of one asset. `pos_fn(df, n, symbol) -> Series` overrides the position rule so a
    study can test a different one without duplicating the vol targeting or the ensemble average."""
    ret = close.pct_change(fill_method=None)
    sigma = ret.rolling(vol_window).std() * math.sqrt(ANNUALIZATION)
    scalar = (target_vol / sigma).clip(upper=LEVERAGE_CAP).replace([np.inf, -np.inf], np.nan)
    if pos_fn is not None:
        frame = df if df is not None else close.to_frame("close")
        legs = [scalar * pos_fn(frame, n, symbol) for n in lookbacks]
    else:
        f = sub_strategy_positions_ls if long_short else sub_strategy_positions
        legs = [scalar * f(close, n) for n in lookbacks]
    w = pd.concat(legs, axis=1).mean(axis=1)
    return w.fillna(0.0)


def monthly_universe(data: Dict[str, pd.DataFrame], dates: pd.DatetimeIndex, n_assets: int,
                     corr_cap: float) -> pd.DataFrame:
    """Point-in-time universe rebalanced monthly, using the ENGINE's own selection function
    (strategies/trend_daily_model.select_universe) so that what is validated here is exactly what
    the bot executes. Only data <= the decision date is used, and the ranking never looks at past
    returns."""
    p = TrendParams(n_assets=n_assets, min_listing_days=MIN_HISTORY_DAYS)
    p.corr_cap = corr_cap
    p.corr_window = CORR_WINDOW
    frames = {s: d.copy() for s, d in data.items()}
    for d in frames.values():
        if "quote_volume" not in d.columns:
            d["quote_volume"] = d["close"] * d["volume"]
    selected = pd.DataFrame(0.0, index=dates, columns=list(data.keys()))
    current: List[str] = []
    month = None
    for dt in dates:
        if month != (dt.year, dt.month):
            month = (dt.year, dt.month)
            current = select_universe(frames, dt, p, current=current)
        for s in current:
            selected.loc[dt, s] = 1.0 / max(len(current), 1)
    return selected

def backtest(data: Dict[str, pd.DataFrame], cost_bps: float = COST_BPS, lookbacks: Optional[List[int]] = None,
             target_vol: float = TARGET_VOL, vol_window: int = VOL_WINDOW, n_assets: int = N_ASSETS,
             corr_cap: float = CORR_CAP, long_short: bool = False, funding: bool = True,
             funding_mult: float = 1.0, pos_fn=None,
             rebalance_threshold: float = REBALANCE_THRESHOLD) -> Dict:
    lookbacks = lookbacks or LOOKBACKS
    dates = pd.DatetimeIndex(sorted(set().union(*[d.index for d in data.values()])))
    alloc = monthly_universe(data, dates, n_assets, corr_cap)
    w_assets, r_oo = {}, {}
    for s, d in data.items():
        dd = d.reindex(dates).ffill()
        w_assets[s] = asset_weight(dd["close"], lookbacks, target_vol, vol_window, long_short,
                                   pos_fn=pos_fn, df=dd, symbol=s).reindex(dates).fillna(0.0)
        r_oo[s] = (dd["open"] / dd["open"].shift(1) - 1.0).reindex(dates).fillna(0.0)
    W = pd.DataFrame(w_assets) * alloc
    R = pd.DataFrame(r_oo)

    W_exec = W.copy()
    prev = pd.Series(0.0, index=W.columns)
    for dt in W.index:
        tgt, newp = W.loc[dt], prev.copy()
        for s in W.columns:
            if (tgt[s] == 0) != (prev[s] == 0):
                newp[s] = tgt[s]
            elif prev[s] != 0 and abs(tgt[s] - prev[s]) / abs(prev[s]) > rebalance_threshold:
                newp[s] = tgt[s]
        W_exec.loc[dt] = newp
        prev = newp

    gross = (W_exec.shift(2) * R).sum(axis=1)
    turnover = W_exec.diff().abs().sum(axis=1).shift(2).fillna(0.0)
    costs = turnover * (cost_bps / 10_000.0)
    fund = pd.Series(0.0, index=dates)
    if funding:
        venue = measured_funding()
        daily_rate = {s: (venue.get(s, FUNDING_ANNUAL.get(ASSET_CLASS.get(s, "crypto"), 0.08))
                          * funding_mult / ANNUALIZATION) for s in W_exec.columns}
        # longs pay, shorts receive
        fund = sum(W_exec[s].shift(2).fillna(0.0) * daily_rate[s] for s in W_exec.columns)
    net = (gross - costs - fund).fillna(0.0)

    holding = (W_exec != 0).astype(int)
    return {"net": net, "gross": gross, "costs": costs, "funding": fund, "weights": W_exec,
            "closed_trades": int((holding.diff() == -1).sum().sum()),
            "turnover": turnover, "returns": R, "alloc": alloc,
            "pnl_at_shift": lambda k: ((W_exec.shift(k) * R).sum(axis=1)
                                       - W_exec.diff().abs().sum(axis=1).shift(k).fillna(0.0) * (cost_bps / 10_000.0)
                                       ).fillna(0.0)}


# ── metrics ───────────────────────────────────────────────────────────────
def metrics(net: pd.Series) -> Dict[str, float]:
    net = net.dropna()
    if len(net) < 30:
        return {"sharpe": 0.0, "cagr": 0.0, "vol": 0.0, "max_dd": 1.0, "skew": 0.0, "days": len(net), "kurt": 3.0}
    eq = (1 + net).cumprod()
    years = len(net) / ANNUALIZATION
    cagr = eq.iloc[-1] ** (1 / years) - 1 if years > 0 and eq.iloc[-1] > 0 else -1.0
    vol = net.std() * math.sqrt(ANNUALIZATION)
    sharpe = (net.mean() / net.std() * math.sqrt(ANNUALIZATION)) if net.std() > 0 else 0.0
    dd = float((eq / eq.cummax() - 1).min())
    return {"sharpe": float(sharpe), "cagr": float(cagr), "vol": float(vol), "max_dd": abs(dd),
            "skew": float(net.skew()), "kurt": float(net.kurtosis() + 3.0), "days": int(len(net)),
            "final": float(eq.iloc[-1])}


def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _norm_ppf(p: float) -> float:
    if p <= 0 or p >= 1:
        return 0.0
    lo, hi = -10.0, 10.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if _norm_cdf(mid) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def deflated_sharpe(sharpe: float, n_obs: int, n_trials: int, skew: float, kurt: float = 3.0) -> float:
    """Bailey & López de Prado DSR. IDENTICAL implementation to the validated crypto study
    (scripts/trend_daily_research.py) so both numbers are comparable: the trial threshold is
    expressed in per-observation Sharpe units, whose standard error is 1/sqrt(n_obs)."""
    if n_obs < 30 or n_trials < 1:
        return 0.0
    emc = 0.5772156649
    e_max = (1 - emc) * _norm_ppf(1 - 1.0 / n_trials) + emc * _norm_ppf(1 - 1.0 / (n_trials * math.e))
    sr_star = e_max / math.sqrt(n_obs)
    sr = sharpe / math.sqrt(ANNUALIZATION)
    denom = math.sqrt(max(1e-12, 1 - skew * sr + (kurt - 1) / 4 * sr ** 2))
    return _norm_cdf((sr - sr_star) * math.sqrt(n_obs - 1) / denom)


def summary(name: str, res: Dict) -> Dict:
    m = metrics(res["net"])
    print(f"  {name:34s} Sharpe {m['sharpe']:5.2f} | CAGR {m['cagr']*100:6.1f}% | vol {m['vol']*100:5.1f}% "
          f"| maxDD {m['max_dd']*100:5.1f}% | skew {m['skew']:+.2f} | {m['days']:5d} d | trades {res['closed_trades']:4d}")
    return m


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--classes", default="crypto,metal,index,energy",
                    help="asset classes in the universe. Single stocks are OFF by default: Strike lists "
                         "today's winners (NVDA, MU, COIN...), so including them imports hindsight "
                         "selection. Use 'all' to include every class.")
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    only = None if args.classes.strip().lower() == "all" else [c.strip() for c in args.classes.split(",") if c.strip()]
    data = load_panel(only_class=only)
    classes: Dict[str, int] = {}
    for s in data:
        classes[ASSET_CLASS.get(s, "other")] = classes.get(ASSET_CLASS.get(s, "other"), 0) + 1
    venue = measured_funding()
    print(f"== universe: {len(data)} markets with >= {MIN_HISTORY_DAYS} d of daily history {classes}")
    if venue:
        shown = {k: f"{v*100:+.1f}%" for k, v in sorted(venue.items()) if k in data}
        print(f"== funding: MEASURED on Strike (90 d) {shown}")
    else:
        print("== funding: class averages (run scripts/strike_market_stats.py for venue-measured rates)")
    span = pd.DatetimeIndex(sorted(set().union(*[d.index for d in data.values()])))
    print(f"== span: {span[0].date()} -> {span[-1].date()} ({len(span)} days)")

    trials = 0
    print("\n== BASE (spec model, multi-asset, 8 bps/side, funding on)")
    base = backtest(data)
    trials += 1
    m = summary("multi-asset", base)
    print(f"     funding cost {base['funding'].sum()*100:.1f} pts of equity over the sample "
          f"| turnover {base['turnover'].mean()*252:.1f}x/yr")

    print("\n== SELECTION (markets actually held by the base configuration)")
    sel = (base["alloc"] > 0).sum(axis=0)
    for sym, days in sel[sel > 0].sort_values(ascending=False).items():
        print(f"  {sym:12s} {ASSET_CLASS.get(sym, 'other'):7s} {int(days):5d} days in the portfolio")

    print("\n== REFERENCE (same code, crypto only — the shipped strategy)")
    crypto = backtest({k: v for k, v in data.items() if ASSET_CLASS.get(k) == "crypto"}, n_assets=3)
    trials += 1
    m_crypto = summary("crypto only, N=3", crypto)

    print("\n== LOOK-AHEAD AUDIT (extra delay must not improve the result)")
    for k in (1, 2, 3):
        mm = metrics(base["pnl_at_shift"](k))
        print(f"  shift {k} (spec = 2): Sharpe {mm['sharpe']:5.2f} | CAGR {mm['cagr']*100:6.1f}%")

    results = {"base": m, "crypto_only": m_crypto}
    if not args.quick:
        print("\n== COST AND FUNDING ROBUSTNESS")
        for c in (8.0, 15.0, 25.0, 50.0):
            r = backtest(data, cost_bps=c)
            trials += 1
            results[f"cost_{c:.0f}bps"] = summary(f"{c:.0f} bps/side", r)
        for fm, label in ((0.0, "funding off"), (2.0, "funding x2"), (3.0, "funding x3")):
            r = backtest(data, funding_mult=fm, funding=fm > 0)
            trials += 1
            results[label.replace(" ", "_")] = summary(label, r)

        print("\n== PARAMETER SENSITIVITY (+-50 %)")
        for label, kw in (("target_vol 0.10", {"target_vol": 0.10}), ("target_vol 0.30", {"target_vol": 0.30}),
                          ("vol_window 45", {"vol_window": 45}), ("vol_window 135", {"vol_window": 135}),
                          ("lookbacks x0.5", {"lookbacks": [3, 5, 10, 15, 30, 45]}),
                          ("lookbacks x1.5", {"lookbacks": [8, 15, 30, 45, 90, 135]})):
            r = backtest(data, **kw)
            trials += 1
            results[label.replace(" ", "_")] = summary(label, r)

        print("\n== STRUCTURE")
        for label, kw in (("N=3", {"n_assets": 3}), ("N=8", {"n_assets": 8}), ("N=10", {"n_assets": 10}),
                          ("corr cap 0.6", {"corr_cap": 0.6}), ("corr cap 1.0 (off)", {"corr_cap": 1.0}),
                          ("long/short", {"long_short": True})):
            r = backtest(data, **kw)
            trials += 1
            results[label.replace(" ", "_").replace("/", "_")] = summary(label, r)

        print("\n== SUBSAMPLES (base)")
        net = base["net"]
        for label, sl in (("2022+", net[net.index >= "2022-01-01"]), ("2024+", net[net.index >= "2024-01-01"]),
                          ("first half", net.iloc[: len(net) // 2]), ("second half", net.iloc[len(net) // 2:])):
            mm = metrics(sl)
            print(f"  {label:34s} Sharpe {mm['sharpe']:5.2f} | CAGR {mm['cagr']*100:6.1f}% | {mm['days']:5d} d")
            results[f"sub_{label.replace(' ', '_')}"] = mm

        print("\n== CONTRIBUTION BY ASSET CLASS (base weights)")
        W, R = base["weights"], base["returns"]
        for cls in sorted(set(ASSET_CLASS.get(s, "other") for s in W.columns)):
            cols = [s for s in W.columns if ASSET_CLASS.get(s) == cls]
            pnl = (W[cols].shift(2) * R[cols]).sum(axis=1)
            days_held = int((W[cols] != 0).any(axis=1).sum())
            print(f"  {cls:10s} gross {pnl.sum()*100:7.1f} pts | days with exposure {days_held:5d} "
                  f"| markets {len(cols)}")

    dsr = deflated_sharpe(m["sharpe"], m["days"], max(trials, 1), m["skew"], m["kurt"])
    print(f"\n== trials recorded: {trials} | deflated Sharpe probability: {dsr:.2f}")

    checks = [
        ("Sharpe net >= 0.8", m["sharpe"] >= 0.8),
        ("CAGR > 0", m["cagr"] > 0),
        ("maxDD < 15 %", m["max_dd"] < 0.15),
        ("maxDD lower than crypto-only", m["max_dd"] < m_crypto["max_dd"]),
        ("Sharpe >= crypto-only", m["sharpe"] >= m_crypto["sharpe"]),
        ("skew > -0.5", m["skew"] > -0.5),
        ("DSR >= 0.95", dsr >= 0.95),
        ("survives 25 bps/side", results.get("cost_25bps", {}).get("sharpe", 0) > 0.5 if not args.quick else None),
        ("survives funding x3", results.get("funding_x3", {}).get("sharpe", 0) > 0.5 if not args.quick else None),
        ("no look-ahead artefact", metrics(base["pnl_at_shift"](3))["sharpe"] >= 0.5 * m["sharpe"]),
        ("2022+ Sharpe >= 0.5", results.get("sub_2022+", {}).get("sharpe", 0) >= 0.5 if not args.quick else None),
    ]
    checks = [(n, v) for n, v in checks if v is not None]
    ok = sum(1 for _, v in checks if v)
    print("\n== GO/NO-GO")
    for n, v in checks:
        print(f"  [{'PASS' if v else 'FAIL'}] {n}")
    verdict = "GO to paper (multi-asset allocation > 0 allowed)" if ok == len(checks) else "NO-GO: keep it in research"
    print(f"\n  {ok}/{len(checks)} -> {verdict}")

    if args.json:
        json.dump({"metrics": results, "trials": trials, "dsr": dsr,
                   "checks": {n: bool(v) for n, v in checks}, "verdict": verdict},
                  open(args.json, "w", encoding="utf-8"), indent=1, default=float)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
