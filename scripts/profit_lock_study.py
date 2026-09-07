"""Should the trend book move its stops up to lock in profit once a trade has run? (2026-09-07)

Edgar's question, seen on ZEC (+10 % in a day) and SOL: the exit ladder trails the Donchian channel
MID and sits below the entry price after a big move, so nothing is "secured". The base model is
the validated one; every variant below keeps the same entries and only changes the stop:

  base           stop = max(stop, channel mid)                      (the bot)
  breakeven      base, and once close >= entry x (1 + 5 %) the stop is at least the entry
  lock50         base, and the stop is at least entry + 50 % of the best close's gain
  lock25         base, and the stop is at least entry + 25 % of the best close's gain
  chandelier3    base, and the stop is at least highest close - 3 x ATR(20)
  chandelier2    base, and the stop is at least highest close - 2 x ATR(20)
  donchian_low   stop = max(stop, n-day LOW) instead of the mid (looser than base)

Same harness, costs and funding as tasks/research_trend_multi_2026-09-03.md.
Run: py -3.12 scripts/profit_lock_study.py [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Callable, Dict

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import trend_multi_research as R  # noqa: E402


def _atr(frame: pd.DataFrame, n: int = 20) -> np.ndarray:
    h, l, c = frame["high"].to_numpy(float), frame["low"].to_numpy(float), frame["close"].to_numpy(float)
    pc = np.roll(c, 1); pc[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    return pd.Series(tr).rolling(n).mean().to_numpy()


def make_pos_fn(variant: str) -> Callable:
    def pos_fn(frame: pd.DataFrame, n: int, symbol: str) -> pd.Series:
        close = frame["close"]
        c = close.to_numpy(float)
        rmax = close.rolling(n).max().to_numpy(float)
        rmin = close.rolling(n).min().to_numpy(float)
        mid = 0.5 * (rmax + rmin)
        atr = _atr(frame, 20) if variant.startswith("chandelier") else None
        pos = np.zeros(len(c))
        state = 0
        stop = np.nan
        entry = np.nan
        best = np.nan
        for i in range(len(c)):
            if np.isnan(mid[i]):
                continue
            if state == 0:
                if c[i] >= rmax[i]:
                    state = 1
                    entry = c[i]
                    best = c[i]
                    stop = rmin[i] if variant == "donchian_low" else mid[i]
            else:
                best = max(best, c[i])
                trail = rmin[i] if variant == "donchian_low" else mid[i]
                stop = max(stop, trail)
                if variant == "breakeven" and c[i] >= entry * 1.05:
                    stop = max(stop, entry)
                elif variant == "lock50":
                    stop = max(stop, entry + 0.5 * (best - entry))
                elif variant == "lock25":
                    stop = max(stop, entry + 0.25 * (best - entry))
                elif variant == "chandelier3" and atr is not None and not np.isnan(atr[i]):
                    stop = max(stop, best - 3.0 * atr[i])
                elif variant == "chandelier2" and atr is not None and not np.isnan(atr[i]):
                    stop = max(stop, best - 2.0 * atr[i])
                if c[i] <= stop:
                    state = 0
                    stop = np.nan
            pos[i] = 1.0 if state == 1 else 0.0
        return pd.Series(pos, index=close.index)
    return pos_fn


def run(label: str, data, variant: str) -> Dict:
    t0 = time.time()
    res = R.backtest(data, pos_fn=None if variant == "base" else make_pos_fn(variant))
    m = R.metrics(res["net"])
    m.update({"label": label, "variant": variant, "turnover_yr": float(res["turnover"].mean() * R.ANNUALIZATION),
              "closed_trades": res["closed_trades"], "secs": time.time() - t0})
    # subsamples: does the change hold in both halves?
    net = res["net"]
    h1, h2 = R.metrics(net.iloc[: len(net) // 2]), R.metrics(net.iloc[len(net) // 2:])
    m["sharpe_h1"], m["sharpe_h2"] = h1["sharpe"], h2["sharpe"]
    print(f"  {label:14s} Sharpe {m['sharpe']:5.2f} | CAGR {m['cagr']*100:6.1f}% | maxDD {m['max_dd']*100:5.1f}% "
          f"| turnover {m['turnover_yr']:5.1f}x/yr | trades {m['closed_trades']:5d} | halves {h1['sharpe']:.2f} / {h2['sharpe']:.2f} | {m['secs']:.0f}s", flush=True)
    return m


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="")
    args = ap.parse_args()
    data = R.load_panel(only_class=["crypto", "metal", "index", "energy"])
    print(f"== panel: {len(data)} markets", flush=True)
    out = []
    for v in ("base", "breakeven", "lock25", "lock50", "chandelier3", "chandelier2", "donchian_low"):
        out.append(run(v, data, v))
    if args.json:
        Path(args.json).write_text(json.dumps(out, indent=2, default=float), encoding="utf-8")
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
