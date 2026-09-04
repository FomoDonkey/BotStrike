#!/usr/bin/env python
"""Divergence strategy — research & GO/NO-GO before any capital (2026-09-02).

What is tested (1-hour bars, Binance spot, 2022-01 -> today, 6 majors):
  REGULAR BULLISH divergence  : price pivot low L2 < L1 while RSI(L2) > RSI(L1)
  REGULAR BEARISH divergence  : price pivot high H2 > H1 while RSI(H2) < RSI(H1)
Pivots are confirmed with `k` bars on each side (the signal exists only k bars after
the pivot — no look-ahead by construction). A divergence is only a CANDIDATE; the
"verifier" turns it into an entry:
  1. the first pivot's RSI was in the extreme zone (< rsi_os for bullish, > rsi_ob bearish)
  2. pivots are 5–60 bars apart and the RSI difference is >= min_rsi_gap points
  3. TRIGGER within `trigger_window` bars after confirmation: a close beyond the
     confirmation level (bullish: the highest high between the two pivots... too far;
     we use the high of the second pivot bar; bearish: its low) — the "structure break"
  4. optional: MACD histogram rising (bullish) / falling (bearish) on the trigger bar
Entry at the NEXT bar open after the trigger close (research s.4.2: never at the signal
close). Stop below L2 (above H2) minus/plus atr_buffer×ATR; take-profit at rr×risk;
time stop after max_hold bars. Costs: taker fee + slippage per side (bps).

Outputs per configuration: trades, win rate, avg R, profit factor (net), expectancy in
bps, t-stat of the gross return per trade, Sharpe of daily PnL, max drawdown at 1%
risk per trade, and a look-ahead audit (fill at the signal close vs next open).
"""
from __future__ import annotations

import argparse
import itertools
import math
import os
import sys
from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.indicators import Indicators  # noqa: E402

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "binance_1h")
SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "ADAUSDT", "BNBUSDT", "XRPUSDT"]


@dataclass
class Params:
    rsi_period: int = 14
    pivot_k: int = 3
    rsi_os: float = 35.0
    rsi_ob: float = 65.0
    min_gap_bars: int = 5
    max_gap_bars: int = 60
    min_rsi_gap: float = 3.0
    trigger_window: int = 6
    require_macd: bool = True
    atr_buffer: float = 0.5
    rr: float = 2.0
    max_hold: int = 48
    hidden: bool = False          # hidden divergences (continuation) instead of regular
    cost_bps_side: float = 8.0    # taker 5 + slippage 3 per side
    with_trend: bool = False      # regular divergences only in the EMA200 direction (pullbacks)
    tf_hours: int = 1             # 1 = native 1h bars, 4 = aggregated 4h bars


def load(sym: str, tf_hours: int = 1) -> pd.DataFrame:
    df = pd.read_parquet(os.path.join(DATA_DIR, f"{sym}.parquet"))
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    if tf_hours > 1:
        df = df.resample(f"{tf_hours}h").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    close = df["close"]
    df["rsi"] = Indicators.rsi(close, 14)
    df["rsi21"] = Indicators.rsi(close, 21)
    df["atr"] = Indicators.atr(df["high"], df["low"], close, 14)
    ema12, ema26 = close.ewm(span=12, adjust=False).mean(), close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    df["macd_hist"] = macd - macd.ewm(span=9, adjust=False).mean()
    df["ema200"] = close.ewm(span=200, adjust=False).mean()
    return df.dropna()


def pivots(series: np.ndarray, k: int, low: bool) -> np.ndarray:
    """Boolean array: bar i is a pivot (strict local extreme over ±k). Known only at i+k."""
    n = len(series)
    out = np.zeros(n, dtype=bool)
    for i in range(k, n - k):
        w = series[i - k:i + k + 1]
        if low:
            out[i] = series[i] == w.min() and (w == series[i]).sum() == 1
        else:
            out[i] = series[i] == w.max() and (w == series[i]).sum() == 1
    return out


def simulate(df: pd.DataFrame, p: Params, fill_at_close: bool = False) -> List[dict]:
    o, h, l, c = (df[x].to_numpy() for x in ("open", "high", "low", "close"))
    rsi = df["rsi" if p.rsi_period == 14 else "rsi21"].to_numpy()
    atr = df["atr"].to_numpy()
    hist = df["macd_hist"].to_numpy()
    idx = df.index
    n = len(df)
    plow = pivots(l, p.pivot_k, low=True)
    phigh = pivots(h, p.pivot_k, low=False)
    trades: List[dict] = []
    last_low: Optional[int] = None
    last_high: Optional[int] = None
    pending: Optional[dict] = None          # confirmed divergence waiting for its trigger
    pos: Optional[dict] = None
    cost = p.cost_bps_side / 1e4
    for i in range(p.pivot_k, n):
        # ── manage open position on bar i ──
        if pos is not None:
            exit_px = None; reason = ""
            if pos["side"] == 1:
                if l[i] <= pos["stop"]:
                    exit_px, reason = pos["stop"], "stop"
                elif h[i] >= pos["tp"]:
                    exit_px, reason = pos["tp"], "tp"
            else:
                if h[i] >= pos["stop"]:
                    exit_px, reason = pos["stop"], "stop"
                elif l[i] <= pos["tp"]:
                    exit_px, reason = pos["tp"], "tp"
            if exit_px is None and i - pos["entry_i"] >= p.max_hold:
                exit_px, reason = c[i], "time"
            if exit_px is not None:
                gross = (exit_px / pos["entry"] - 1.0) * pos["side"]
                net = gross - 2 * cost
                risk = abs(pos["entry"] - pos["stop"]) / pos["entry"]
                trades.append({"symbol": pos["symbol"], "side": pos["side"], "entry_ts": idx[pos["entry_i"]],
                               "exit_ts": idx[i], "entry": pos["entry"], "exit": exit_px, "gross": gross,
                               "net": net, "r": net / risk if risk > 0 else 0.0, "reason": reason,
                               "hold": i - pos["entry_i"], "risk": risk})
                pos = None
        # ── pivots become known k bars later ──
        j = i - p.pivot_k
        if j < 0:
            continue
        if plow[j]:
            if last_low is not None:
                gap = j - last_low
                if p.min_gap_bars <= gap <= p.max_gap_bars:
                    if not p.hidden:
                        div = l[j] < l[last_low] and rsi[j] > rsi[last_low] + p.min_rsi_gap and rsi[last_low] < p.rsi_os
                    else:
                        div = l[j] > l[last_low] and rsi[j] < rsi[last_low] - p.min_rsi_gap and c[j] > df["ema200"].iloc[j]
                    if div and p.with_trend and c[j] < df["ema200"].iloc[j]:
                        div = False
                    if div and pos is None:
                        pending = {"side": 1, "level": h[j], "stop_ref": l[j], "born": i, "atr": atr[j],
                                   "rsi_gap": rsi[j] - rsi[last_low], "gap": gap}
            last_low = j
        if phigh[j]:
            if last_high is not None:
                gap = j - last_high
                if p.min_gap_bars <= gap <= p.max_gap_bars:
                    if not p.hidden:
                        div = h[j] > h[last_high] and rsi[j] < rsi[last_high] - p.min_rsi_gap and rsi[last_high] > p.rsi_ob
                    else:
                        div = h[j] < h[last_high] and rsi[j] > rsi[last_high] + p.min_rsi_gap and c[j] < df["ema200"].iloc[j]
                    if div and p.with_trend and c[j] > df["ema200"].iloc[j]:
                        div = False
                    if div and pos is None:
                        pending = {"side": -1, "level": l[j], "stop_ref": h[j], "born": i, "atr": atr[j],
                                   "rsi_gap": rsi[last_high] - rsi[j], "gap": gap}
            last_high = j
        # ── trigger: structure break within the window ──
        if pending is not None and pos is None:
            if i - pending["born"] > p.trigger_window:
                pending = None
            else:
                side = pending["side"]
                broke = c[i] > pending["level"] if side == 1 else c[i] < pending["level"]
                macd_ok = (hist[i] > hist[i - 1]) if side == 1 else (hist[i] < hist[i - 1])
                if broke and (macd_ok or not p.require_macd):
                    if fill_at_close:
                        entry, entry_i = c[i], i
                    else:
                        if i + 1 >= n:
                            pending = None
                            continue
                        entry, entry_i = o[i + 1], i + 1
                    buf = p.atr_buffer * pending["atr"]
                    stop = pending["stop_ref"] - buf if side == 1 else pending["stop_ref"] + buf
                    risk = abs(entry - stop)
                    if risk <= 0 or risk / entry < 0.001:
                        pending = None
                        continue
                    tp = entry + side * p.rr * risk
                    pos = {"side": side, "entry": entry, "stop": stop, "tp": tp, "entry_i": entry_i,
                           "symbol": df.attrs.get("symbol", "")}
                    pending = None
    return trades


def stats(trades: List[dict], risk_pct: float = 0.01) -> Dict[str, float]:
    if not trades:
        return {"n": 0}
    t = pd.DataFrame(trades)
    n = len(t)
    wins = (t["net"] > 0).sum()
    gross_bps = t["gross"] * 1e4
    se = gross_bps.std(ddof=1) / math.sqrt(n) if n > 1 else float("nan")
    tstat = gross_bps.mean() / se if se and se > 0 else float("nan")
    pf = t.loc[t["net"] > 0, "net"].sum() / max(-t.loc[t["net"] < 0, "net"].sum(), 1e-12)
    # equity at risk_pct of equity per trade (R-multiples compound)
    eq = (1 + risk_pct * t.sort_values("exit_ts")["r"]).cumprod()
    dd = float((1 - eq / eq.cummax()).max())
    daily = t.assign(d=pd.to_datetime(t["exit_ts"]).dt.floor("D")).groupby("d")["r"].sum() * risk_pct
    days = (pd.to_datetime(t["exit_ts"]).max() - pd.to_datetime(t["entry_ts"]).min()).days or 1
    full = daily.reindex(pd.date_range(daily.index.min(), daily.index.max(), freq="D"), fill_value=0.0)
    sharpe = float(full.mean() / full.std(ddof=1) * math.sqrt(365)) if full.std(ddof=1) > 0 else 0.0
    return {"n": n, "wr": wins / n, "avg_r": float(t["r"].mean()), "pf": float(pf),
            "exp_bps": float(t["net"].mean() * 1e4), "gross_bps": float(gross_bps.mean()), "t": float(tstat),
            "sharpe": sharpe, "maxdd": dd, "cagr": float(eq.iloc[-1] ** (365 / days) - 1),
            "hold": float(t["hold"].mean()), "stop_share": float((t["reason"] == "stop").mean()),
            "long_share": float((t["side"] == 1).mean()), "per_year": n / (days / 365)}


def run_all(data: Dict[str, pd.DataFrame], p: Params, fill_at_close: bool = False) -> Tuple[Dict, Dict[str, Dict]]:
    all_trades: List[dict] = []
    per: Dict[str, Dict] = {}
    for sym, df in data.items():
        tr = simulate(df, p, fill_at_close)
        for x in tr:
            x["symbol"] = sym
        per[sym] = stats(tr)
        all_trades += tr
    return stats(all_trades), per


def fmt(s: Dict) -> str:
    if s.get("n", 0) == 0:
        return "n=0"
    return (f"n={s['n']:4d} ({s['per_year']:.0f}/yr) WR={s['wr']*100:4.1f}% avgR={s['avg_r']:+.3f} PF={s['pf']:.2f} "
            f"exp={s['exp_bps']:+6.1f}bps gross={s['gross_bps']:+6.1f}bps t={s['t']:+.2f} Sharpe={s['sharpe']:.2f} "
            f"maxDD={s['maxdd']*100:.1f}% hold={s['hold']:.0f}h stops={s['stop_share']*100:.0f}%")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--extra", action="store_true")
    # The study's own §6 says the verdict changes only on a FRESH window. Six markets it never saw
    # is a genuine out-of-sample: same rule, data it was not tuned on (2026-09-04).
    ap.add_argument("--symbols", default="", help="comma-separated override, e.g. an out-of-sample set")
    args = ap.parse_args()
    syms = [x.strip().upper() for x in args.symbols.split(",") if x.strip()] or SYMS
    data = {s: load(s) for s in syms if os.path.exists(os.path.join(DATA_DIR, f"{s}.parquet"))}
    print(f"== markets: {sorted(data)}")
    for s, d in data.items():
        d.attrs["symbol"] = s
    print(f"== data: {len(data)} symbols, {min(len(d) for d in data.values())} bars each (1h)")
    base = Params()
    n_trials = 0
    print("\n== BASE (regular divergences, RSI14, k=3, structure break + MACD, next-open fill, 2R, 8 bps/side)")
    s, per = run_all(data, base); n_trials += 1
    print("  POOLED ", fmt(s))
    for sym, ps in per.items():
        print(f"  {sym:8s}", fmt(ps))
    print("\n== LOOK-AHEAD AUDIT: fill at the signal close (forbidden) vs next open (spec)")
    s_close, _ = run_all(data, base, fill_at_close=True)
    print("  close-fill", fmt(s_close)); print("  next-open ", fmt(s))
    print("\n== VARIANTS (each is a recorded trial)")
    variants = [
        ("no MACD confirmation", replace(base, require_macd=False)),
        ("RSI21", replace(base, rsi_period=21)),
        ("pivot k=5", replace(base, pivot_k=5)),
        ("rr=1.5", replace(base, rr=1.5)),
        ("rr=3", replace(base, rr=3.0)),
        ("extreme zone 30/70", replace(base, rsi_os=30.0, rsi_ob=70.0)),
        ("no extreme-zone filter", replace(base, rsi_os=100.0, rsi_ob=0.0)),
        ("min rsi gap 6", replace(base, min_rsi_gap=6.0)),
        ("trigger window 12", replace(base, trigger_window=12)),
        ("max hold 96", replace(base, max_hold=96)),
        ("HIDDEN divergences (continuation, EMA200 filter)", replace(base, hidden=True)),
        ("costs 15 bps/side", replace(base, cost_bps_side=15.0)),
        ("costs 25 bps/side", replace(base, cost_bps_side=25.0)),
    ]
    if args.quick:
        variants = variants[:4]
    if args.extra:
        data4 = {s_: load(s_, 4) for s_ in data}
        for s_, d in data4.items():
            d.attrs["symbol"] = s_
        for name, p, dd in [("WITH-TREND regular (EMA200 direction)", replace(base, with_trend=True), data),
                            ("4h bars, base", replace(base, tf_hours=4, max_hold=24), data4),
                            ("4h bars, with-trend", replace(base, tf_hours=4, max_hold=24, with_trend=True), data4),
                            ("4h bars, pivot k=5", replace(base, tf_hours=4, max_hold=24, pivot_k=5), data4)]:
            s_v, per_v = run_all(dd, p)
            print(f"  {name:48s}", fmt(s_v))
        return 0
    results = {}
    for name, p in variants:
        s_v, _ = run_all(data, p); n_trials += 1
        results[name] = s_v
        print(f"  {name:48s}", fmt(s_v))
    print("\n== LONG-ONLY vs SHORT-ONLY (base)")
    longs = []; shorts = []
    for sym, df in data.items():
        for tr in simulate(df, base):
            tr["symbol"] = sym
            (longs if tr["side"] == 1 else shorts).append(tr)
    print("  longs ", fmt(stats(longs))); print("  shorts", fmt(stats(shorts)))
    print("\n== BY YEAR (base, pooled net R at 1% risk)")
    allt = []
    for sym, df in data.items():
        for tr in simulate(df, base):
            tr["symbol"] = sym; allt.append(tr)
    t = pd.DataFrame(allt); t["year"] = pd.to_datetime(t["exit_ts"]).dt.year
    for y, g in t.groupby("year"):
        print(f"  {y}: n={len(g):3d} WR={(g['net']>0).mean()*100:4.1f}% sumR={g['r'].sum():+6.1f} PF={g.loc[g['net']>0,'net'].sum()/max(-g.loc[g['net']<0,'net'].sum(),1e-12):.2f}")
    print(f"\n== trials recorded: {n_trials}")
    print("== GO/NO-GO (research s.4.4, adapted): n>=300 pooled, PF net>=1.2, t>=2.0, Sharpe>=0.8, maxDD<15%, "
          "next-open Sharpe >= 50% of close-fill Sharpe, still PF>1 at 15 bps/side")
    checks = [
        ("n >= 300", s.get("n", 0) >= 300), ("PF net >= 1.2", s.get("pf", 0) >= 1.2),
        ("t-stat >= 2", s.get("t", 0) >= 2.0), ("Sharpe >= 0.8", s.get("sharpe", 0) >= 0.8),
        ("maxDD < 15%", s.get("maxdd", 1) < 0.15),
        ("no look-ahead artefact", s.get("sharpe", 0) >= 0.5 * max(s_close.get("sharpe", 0), 1e-9) or s_close.get("sharpe", 0) <= 0),
        ("PF > 1 at 15 bps/side", results.get("costs 15 bps/side", {}).get("pf", 0) > 1.0),
    ]
    ok = 0
    for name, passed in checks:
        ok += int(bool(passed)); print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
    print(f"\n  {ok}/{len(checks)} -> {'GO to paper (allocation > 0 allowed)' if ok == len(checks) else 'NO-GO: ship DISABLED (allocation 0), monitor only'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
