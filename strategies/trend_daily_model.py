"""Trend daily model — pure functions shared by the live engine and research.

Exact specification of tasks/research_r2_trend_evidence.md §11.2 (validated 11/11
GO/NO-GO on 2026-08-31 with scripts/trend_daily_research.py): Donchian ensemble,
long-only, vol-targeted, never-falling trailing stop, equal-weight universe chosen
point-in-time by 30-day median dollar volume. Everything here uses only data at or
before the decision date — the caller passes the frame already truncated.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

ANNUALIZATION = 365


@dataclass
class TrendParams:
    lookbacks: Tuple[int, ...] = (5, 10, 20, 30, 60, 90)
    target_vol: float = 0.20
    vol_window: int = 90
    leverage_cap: float = 2.0
    n_assets: int = 3
    rebalance_threshold: float = 0.20
    min_listing_days: int = 365
    liq_enter_usd: float = 2_000_000.0
    liq_exit_usd: float = 1_000_000.0

    @classmethod
    def from_config(cls, tc) -> "TrendParams":
        lbs = tuple(int(x) for x in str(tc.trend_lookbacks).split(",") if x.strip())
        return cls(
            lookbacks=lbs or (5, 10, 20, 30, 60, 90),
            target_vol=float(tc.trend_target_vol),
            vol_window=int(tc.trend_vol_window),
            leverage_cap=float(tc.trend_leverage_cap),
            n_assets=int(tc.trend_n_assets),
            rebalance_threshold=float(tc.trend_rebalance_threshold),
            min_listing_days=int(tc.trend_min_listing_days),
            liq_enter_usd=float(tc.trend_liq_enter_usd),
            liq_exit_usd=float(tc.trend_liq_exit_usd),
        )

    @property
    def min_history_days(self) -> int:
        return max(max(self.lookbacks), self.vol_window) + 2


def sub_strategy_positions(close: pd.Series, n: int) -> Tuple[pd.Series, pd.Series]:
    """Position (0/1) and trailing stop of ONE lookback. Uses only data <= t."""
    roll_max = close.rolling(n).max()
    roll_min = close.rolling(n).min()
    mid = 0.5 * (roll_max + roll_min)
    pos = np.zeros(len(close))
    stop = np.full(len(close), np.nan)
    in_pos = False
    cur_stop = np.nan
    c = close.to_numpy(dtype=float)
    m = mid.to_numpy(dtype=float)
    rmax = roll_max.to_numpy(dtype=float)
    for i in range(len(c)):
        if np.isnan(m[i]):
            continue
        if not in_pos:
            if c[i] >= rmax[i]:          # close is the n-day high → enter
                in_pos = True
                cur_stop = m[i]          # initial stop = Donchian mid at entry
        else:
            cur_stop = max(cur_stop, m[i])   # trailing: never falls
            if c[i] <= cur_stop:
                in_pos = False
                cur_stop = np.nan
        pos[i] = 1.0 if in_pos else 0.0
        stop[i] = cur_stop
    return pd.Series(pos, index=close.index), pd.Series(stop, index=close.index)


def asset_weight(close: pd.Series, p: TrendParams) -> pd.Series:
    """Target weight of one asset: mean over lookbacks of (vol scalar × position)."""
    ret = close.pct_change(fill_method=None)
    sigma = ret.rolling(p.vol_window).std() * np.sqrt(ANNUALIZATION)
    vol_scalar = (p.target_vol / sigma).clip(upper=p.leverage_cap)
    vol_scalar = vol_scalar.replace([np.inf, -np.inf], np.nan)
    weights = []
    for n in p.lookbacks:
        pos, _ = sub_strategy_positions(close, n)
        weights.append(vol_scalar * pos)
    w = pd.concat(weights, axis=1).mean(axis=1)
    return w.fillna(0.0)


def select_universe(data: Dict[str, pd.DataFrame], as_of: pd.Timestamp, p: TrendParams,
                    current: Optional[List[str]] = None) -> List[str]:
    """Point-in-time universe at `as_of`: top-N by 30-day median dollar volume with
    liquidity hysteresis (enter >= liq_enter, stay >= liq_exit) and a minimum listing
    age. Uses only rows <= as_of."""
    current = list(current or [])
    scores: Dict[str, float] = {}
    for sym, df in data.items():
        d = df[df.index <= as_of]
        if len(d) < 30 or (as_of - d.index[0]).days < p.min_listing_days:
            continue
        med = float(d["quote_volume"].tail(30).median())
        if not np.isnan(med):
            scores[sym] = med
    eligible = [s for s, v in scores.items() if v >= p.liq_enter_usd]
    ranked = sorted(eligible, key=lambda s: scores[s], reverse=True)
    keep = [s for s in current if s in scores and scores[s] >= p.liq_exit_usd and s in eligible]
    for s in ranked:
        if len(keep) >= p.n_assets:
            break
        if s not in keep:
            keep.append(s)
    return keep[:p.n_assets]


def target_weights(data: Dict[str, pd.DataFrame], universe: List[str], as_of: pd.Timestamp,
                   p: TrendParams) -> Dict[str, float]:
    """Final weight per universe member at the close of `as_of`: asset weight × 1/N."""
    if not universe:
        return {}
    share = 1.0 / len(universe)
    out: Dict[str, float] = {}
    for sym in universe:
        df = data.get(sym)
        if df is None:
            out[sym] = 0.0
            continue
        close = df.loc[df.index <= as_of, "close"]
        if len(close) < p.min_history_days:
            out[sym] = 0.0
            continue
        w = asset_weight(close, p)
        out[sym] = float(max(0.0, w.iloc[-1])) * share
    return out


def apply_rebalance_threshold(target: Dict[str, float], previous: Dict[str, float],
                              threshold: float) -> Dict[str, float]:
    """Entries/exits always execute; size changes only when they exceed `threshold`
    (relative to the previous weight) — spec §11.2, saves fees on vol drift."""
    out: Dict[str, float] = {}
    for sym in set(target) | set(previous):
        t = float(target.get(sym, 0.0))
        prev = float(previous.get(sym, 0.0))
        if (t == 0) != (prev == 0):
            out[sym] = t                      # signal change → always execute
        elif prev > 0 and abs(t - prev) / prev > threshold:
            out[sym] = t                      # vol-induced resize above threshold
        else:
            out[sym] = prev                   # keep what we have
    return out


def model_daily_return(weights_prev: Dict[str, float], open_prev: Dict[str, float],
                       open_now: Dict[str, float], turnover: float, cost_bps: float) -> float:
    """Open-to-open return of yesterday's executed weights, net of turnover costs."""
    g = 0.0
    for sym, w in weights_prev.items():
        o0, o1 = open_prev.get(sym), open_now.get(sym)
        if w and o0 and o1 and o0 > 0:
            g += w * (o1 / o0 - 1.0)
    return g - turnover * cost_bps / 10_000.0
