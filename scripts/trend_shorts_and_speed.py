"""Can the book trade shorts, and should it react faster than once a day? (Edgar, 2026-09-04)

The validated book is long-only and rebalances once a day. Both are choices, and a choice that has
not been re-measured is only a habit. This script measures four short designs and two kinds of
"faster", on the same 10 years, the same costs and the same funding as the study that validated the
current configuration — so every number here is comparable to Sharpe 1.76 / maxDD 7.8 %.

    py -3.12 scripts/trend_shorts_and_speed.py

Every configuration is a recorded trial and is fed to the deflated Sharpe, which is what stops a
search from inventing an edge.
"""
from __future__ import annotations

import math
import os
import sys
from typing import Dict, List

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.trend_multi_research import (ANNUALIZATION, LOOKBACKS, backtest,  # noqa: E402
                                          deflated_sharpe, load_panel, metrics)
from strategies.trend_daily_model import asset_class               # noqa: E402

# Same classes as the validated study: single stocks stay out (Strike lists today's winners).
CLASSES = ["crypto", "metal", "index", "energy"]


# ── position rules ────────────────────────────────────────────────────────
def _mid(close: pd.Series, n: int):
    return 0.5 * (close.rolling(n).max() + close.rolling(n).min())


def long_only(df: pd.DataFrame, n: int, symbol: str = "") -> pd.Series:
    """The rule that runs today: enter on an n-day high, exit when the close breaks a rising stop."""
    c = df["close"].to_numpy(dtype=float)
    m = _mid(df["close"], n).to_numpy(dtype=float)
    rmax = df["close"].rolling(n).max().to_numpy(dtype=float)
    pos, in_pos, stop = np.zeros(len(c)), False, np.nan
    for i in range(len(c)):
        if np.isnan(m[i]):
            continue
        if not in_pos:
            if c[i] >= rmax[i]:
                in_pos, stop = True, m[i]
        else:
            stop = max(stop, m[i])
            if c[i] <= stop:
                in_pos, stop = False, np.nan
        pos[i] = 1.0 if in_pos else 0.0
    return pd.Series(pos, index=df.index)


def long_only_low_stop(df: pd.DataFrame, n: int, symbol: str = "") -> pd.Series:
    """Same, but the stop is checked against the day's LOW instead of its close.

    This is the closest daily proxy for "watch the stop intraday instead of once a day": the exit
    fires the moment price trades through the level, not at the next close.
    """
    c = df["close"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float) if "low" in df else c
    m = _mid(df["close"], n).to_numpy(dtype=float)
    rmax = df["close"].rolling(n).max().to_numpy(dtype=float)
    pos, in_pos, stop = np.zeros(len(c)), False, np.nan
    for i in range(len(c)):
        if np.isnan(m[i]):
            continue
        if not in_pos:
            if c[i] >= rmax[i]:
                in_pos, stop = True, m[i]
        else:
            stop = max(stop, m[i])
            if low[i] <= stop:
                in_pos, stop = False, np.nan
        pos[i] = 1.0 if in_pos else 0.0
    return pd.Series(pos, index=df.index)


def _ls(df: pd.DataFrame, n: int, short_size: float = 1.0, ma: int = 0, classes=None,
        symbol: str = "") -> pd.Series:
    """Symmetric Donchian, with the short side optionally filtered or shrunk.

    `ma`      short only while the close is under its own `ma`-day average (a downtrend filter)
    `classes` short only in these asset classes (metals/indices carry a structural drift up)
    """
    close = df["close"]
    c = close.to_numpy(dtype=float)
    m = _mid(close, n).to_numpy(dtype=float)
    rmax = close.rolling(n).max().to_numpy(dtype=float)
    rmin = close.rolling(n).min().to_numpy(dtype=float)
    trend_ok = (close < close.rolling(ma).mean()).to_numpy() if ma else np.ones(len(c), dtype=bool)
    class_ok = (classes is None) or (asset_class(symbol) in classes)
    pos, state, stop = np.zeros(len(c)), 0, np.nan
    for i in range(len(c)):
        if np.isnan(m[i]):
            continue
        if state == 0:
            if c[i] >= rmax[i]:
                state, stop = 1, m[i]
            elif c[i] <= rmin[i] and class_ok and bool(trend_ok[i]):
                state, stop = -1, m[i]
        elif state == 1:
            stop = max(stop, m[i])
            if c[i] <= stop:
                state, stop = 0, np.nan
        else:
            stop = min(stop, m[i])
            if c[i] >= stop:
                state, stop = 0, np.nan
        pos[i] = 1.0 if state == 1 else (-short_size if state == -1 else 0.0)
    return pd.Series(pos, index=df.index)


def run(data, label, trials, **kw):
    r = backtest(data, **kw)
    m = metrics(r["net"])
    m["label"] = label
    m["funding"] = float(r["funding"].sum())
    m["turnover"] = float(r["turnover"].sum() / (len(r["net"]) / ANNUALIZATION))
    return m


def main() -> int:
    data = load_panel(only_class=CLASSES)
    span = pd.DatetimeIndex(sorted(set().union(*[d.index for d in data.values()])))
    print(f"  {len(data)} markets | {span[0].date()} -> {span[-1].date()} ({len(span)} days)")
    rows: List[Dict] = []

    rows.append(run(data, "BASE long-only, daily rebalance (what runs today)", 1))

    print("  A. reacting faster to a trend change")
    for scale in (0.25, 0.4, 0.5, 0.75, 1.5):
        lb = [max(2, int(round(n * scale))) for n in LOOKBACKS]
        rows.append(run(data, f"lookbacks x{scale} {lb}", 1, lookbacks=lb))
    for thr in (0.0, 0.05, 0.10, 0.30):
        rows.append(run(data, f"rebalance threshold {thr:.2f}", 1, rebalance_threshold=thr))
    rows.append(run(data, "stop checked on the day's LOW (intraday-stop proxy)", 1, pos_fn=long_only_low_stop))

    print("  B. trading the short side")
    rows.append(run(data, "shorts: symmetric", 1, pos_fn=lambda d, n, s: _ls(d, n, symbol=s)))
    rows.append(run(data, "shorts: half size", 1, pos_fn=lambda d, n, s: _ls(d, n, short_size=0.5, symbol=s)))
    rows.append(run(data, "shorts: only under the 200d average", 1,
                    pos_fn=lambda d, n, s: _ls(d, n, ma=200, symbol=s)))
    rows.append(run(data, "shorts: only crypto+energy", 1,
                    pos_fn=lambda d, n, s: _ls(d, n, classes={"crypto", "energy"}, symbol=s)))
    rows.append(run(data, "shorts: half size AND under the 200d average", 1,
                    pos_fn=lambda d, n, s: _ls(d, n, short_size=0.5, ma=200, symbol=s)))

    base = rows[0]
    n_trials = len(rows)
    print(f"\n  {'CONFIGURATION':46s} {'Sharpe':>7s} {'CAGR':>7s} {'maxDD':>7s} {'turn/yr':>8s} {'DSR':>6s}")
    for r in rows:
        dsr = deflated_sharpe(r["sharpe"], r["days"], n_trials, r["skew"], r["kurt"])
        mark = "  <= base" if r is base else ("  BETTER" if r["sharpe"] > base["sharpe"] else "")
        print(f"  {r['label']:46s} {r['sharpe']:7.2f} {r['cagr']*100:6.1f}% {r['max_dd']*100:6.1f}% "
              f"{r['turnover']:8.1f} {dsr:6.2f}{mark}")
    print(f"\n  {n_trials} configurations recorded as trials; the DSR column already pays for that search.")

    # -- the candidate has to survive what the current configuration survived --
    def half(d, n, sym):
        return _ls(d, n, short_size=0.5, symbol=sym)

    print()
    print('  C. stressing the candidate (shorts at half size) against the base')
    print()
    print(f"  {'STRESS':34s} {'base Sharpe':>12s} {'cand Sharpe':>12s} {'base DD':>9s} {'cand DD':>9s}")
    stresses = [('costs 8 bps (as validated)', {}),
                ('costs 15 bps', {'cost_bps': 15.0}),
                ('costs 25 bps', {'cost_bps': 25.0}),
                ('funding x2', {'funding_mult': 2.0}),
                ('funding x3', {'funding_mult': 3.0}),
                ('target vol 0.10', {'target_vol': 0.10}),
                ('target vol 0.30', {'target_vol': 0.30}),
                ('N = 3 markets', {'n_assets': 3}),
                ('N = 10 markets', {'n_assets': 10}),
                ('correlation cap off', {'corr_cap': 1.01})]
    for name, kw in stresses:
        b_ = metrics(backtest(data, **kw)['net'])
        c_ = metrics(backtest(data, pos_fn=half, **kw)['net'])
        print(f"  {name:34s} {b_['sharpe']:12.2f} {c_['sharpe']:12.2f} {b_['max_dd']*100:8.1f}% {c_['max_dd']*100:8.1f}%")

    print()
    print('  D. sub-periods and the look-ahead audit')
    print()
    rb, rc = backtest(data), backtest(data, pos_fn=half)
    idx = rb['net'].index
    windows = [('first half', idx[: len(idx) // 2]), ('second half', idx[len(idx) // 2:]),
               ('2022+', idx[idx >= pd.Timestamp('2022-01-01', tz='UTC')]),
               ('2024+', idx[idx >= pd.Timestamp('2024-01-01', tz='UTC')])]
    print(f"  {'WINDOW':22s} {'base':>8s} {'candidate':>10s}")
    for name, ix in windows:
        print(f"  {name:22s} {metrics(rb['net'].reindex(ix))['sharpe']:8.2f} {metrics(rc['net'].reindex(ix))['sharpe']:10.2f}")
    print()
    print(f"  {'SHIFT':22s} {'base':>8s} {'candidate':>10s}   stability under an EXTRA delay = real edge")
    for k in (1, 2, 3):
        tag = 'forbidden' if k == 1 else ('spec' if k == 2 else 'one extra day')
        print(f"  shift {k} ({tag:14s}) {metrics(rb['pnl_at_shift'](k))['sharpe']:8.2f} {metrics(rc['pnl_at_shift'](k))['sharpe']:10.2f}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
