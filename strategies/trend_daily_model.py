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
# A series is annualised by ITS OWN calendar: crypto prints 365 bars a year, a futures or index
# series ~252. With one factor for all, a TradFi vol came out sqrt(365/252) = 1.20x too high and
# its position ~17 % smaller than the vol target asked for (audit 2026-09-05). Re-validated with
# scripts/validate_profile.py at the change.
TRADING_DAYS = {"crypto": 365}
DEFAULT_TRADING_DAYS = 252


def periods_per_year(symbol: str) -> int:
    return TRADING_DAYS.get(asset_class(symbol), DEFAULT_TRADING_DAYS)


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
    # mixed-pool selection (multi-asset): correlation cap and the VENUE liquidity floor
    corr_window: int = 120
    corr_cap: float = 0.85
    liq_exit_usd_venue: float = 5_000.0      # hard minimum 24 h volume at the venue
    liq_venue_multiple: float = 50.0         # ... and at least 50x one position's notional
    position_notional: float = 0.0           # set per run from equity/leverage/n_assets
    # Short side. OFF by default: measured 2026-09-04 (tasks/research_shorts_and_speed_2026-09-04.md)
    # it holds the Sharpe (1.92) and cuts the drawdown in all ten stress scenarios (7.6 % -> 5.6 %),
    # and it is the book's only natural hedge against expensive funding (a short RECEIVES it) — but
    # it SUBTRACTED return in the last four years (2022+: 1.73 vs 1.94), so it is a hedge with a
    # premium, not an edge. Half size is the sizing that worked; symmetric shorts measured 1.57.
    allow_shorts: bool = False
    short_size: float = 0.5

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
            corr_window=int(getattr(tc, "trend_corr_window", 120) or 120),
            corr_cap=float(getattr(tc, "trend_corr_cap", 0.85) or 0.85),
            liq_exit_usd_venue=float(getattr(tc, "trend_liq_venue_usd", 5_000.0) or 0.0),
            liq_venue_multiple=float(getattr(tc, "trend_liq_venue_multiple", 50.0) or 0.0),
            allow_shorts=bool(getattr(tc, "trend_allow_shorts", False)),
            short_size=float(getattr(tc, "trend_short_size", 0.5) or 0.5),
        )

    @property
    def min_history_days(self) -> int:
        return max(max(self.lookbacks), self.vol_window) + 2


def sub_strategy_positions(close: pd.Series, n: int, allow_shorts: bool = False,
                           short_size: float = 0.5) -> Tuple[pd.Series, pd.Series]:
    """Position and trailing stop of ONE lookback. Uses only data <= t.

    Long-only by default (0/1). With `allow_shorts` the mirror image is added: an n-day LOW opens a
    short of `short_size`, whose stop never rises, and the position is -short_size until the close
    breaks back above it. Validated 2026-09-04 at half size — see TrendParams.
    """
    roll_max = close.rolling(n).max()
    roll_min = close.rolling(n).min()
    mid = 0.5 * (roll_max + roll_min)
    pos = np.zeros(len(close))
    stop = np.full(len(close), np.nan)
    state = 0                            # 0 flat · 1 long · -1 short
    cur_stop = np.nan
    c = close.to_numpy(dtype=float)
    m = mid.to_numpy(dtype=float)
    rmax = roll_max.to_numpy(dtype=float)
    rmin = roll_min.to_numpy(dtype=float)
    for i in range(len(c)):
        if np.isnan(m[i]):
            continue
        if state == 0:
            if c[i] >= rmax[i]:          # close is the n-day high → enter long
                state = 1
                cur_stop = m[i]          # initial stop = Donchian mid at entry
            elif allow_shorts and c[i] <= rmin[i]:   # n-day low → enter short
                state = -1
                cur_stop = m[i]
        elif state == 1:
            cur_stop = max(cur_stop, m[i])   # trailing: never falls
            if c[i] <= cur_stop:
                state = 0
                cur_stop = np.nan
        else:
            cur_stop = min(cur_stop, m[i])   # mirror image: never rises
            if c[i] >= cur_stop:
                state = 0
                cur_stop = np.nan
        pos[i] = 1.0 if state == 1 else (-abs(short_size) if state == -1 else 0.0)
        stop[i] = cur_stop
    return pd.Series(pos, index=close.index), pd.Series(stop, index=close.index)


def asset_weight(close: pd.Series, p: TrendParams, periods: int = ANNUALIZATION) -> pd.Series:
    """Target weight of one asset: mean over lookbacks of (vol scalar × position).
    `periods` is the series' own bars per year (periods_per_year)."""
    ret = close.pct_change(fill_method=None)
    sigma = ret.rolling(p.vol_window).std() * np.sqrt(periods)
    vol_scalar = (p.target_vol / sigma).clip(upper=p.leverage_cap)
    vol_scalar = vol_scalar.replace([np.inf, -np.inf], np.nan)
    weights = []
    for n in p.lookbacks:
        pos, _ = sub_strategy_positions(close, n, p.allow_shorts, p.short_size)
        weights.append(vol_scalar * pos)
    w = pd.concat(weights, axis=1).mean(axis=1)
    return w.fillna(0.0)


# Asset class of each market, for the diversified selection rule. Anything not listed (every
# BINANCE USDT pair) is crypto.
ASSET_CLASS: Dict[str, str] = {
    "XAU-USD": "metal", "XAG-USD": "metal", "WTI-USD": "energy",
    "SP500-USD": "index", "NAS100-USD": "index",
    "NVDA-USD": "equity", "TSLA-USD": "equity", "GOOGL-USD": "equity", "COIN-USD": "equity",
    "MU-USD": "equity", "SNDK-USD": "equity", "CRCL-USD": "equity", "AAOI-USD": "equity",
    "SKHYNIX-USD": "equity",
}


def asset_class(symbol: str) -> str:
    return ASSET_CLASS.get(symbol.upper(), "crypto")


def select_universe(data: Dict[str, pd.DataFrame], as_of: pd.Timestamp, p: TrendParams,
                    current: Optional[List[str]] = None,
                    venue_volume: Optional[Dict[str, float]] = None) -> List[str]:
    """Point-in-time universe at `as_of`. Uses only rows <= as_of.

    Two rules, chosen by whether the pool is single-class or mixed:

    * **Crypto-only pool** (the original, validated in research §11.2): top-N by 30-day median
      dollar volume with liquidity hysteresis.
    * **Mixed pool** (validated 2026-09-03, tasks/research_trend_multi_2026-09-03.md): rank one
      market per asset class first and then by longest history, applying a correlation cap. Dollar
      volume canNOT be compared across classes — an index reports the summed share volume of its
      constituents (NAS100 measured 227e12 on Yahoo) while a silver future reports contracts
      (1 590), so a volume ranking would always pick the indices and always drop the metals.
      Liquidity is instead enforced with the VENUE's own 24 h volume when the caller provides it.

    `venue_volume` is {symbol: 24 h quote volume at the venue}; markets below `liq_exit_usd` there
    are dropped. Without it (research, tests) only history and correlation apply.
    """
    current = list(current or [])
    eligible: List[str] = []
    hist: Dict[str, pd.DataFrame] = {}
    for sym, df in data.items():
        d = df[df.index <= as_of]
        if len(d) < 30 or (as_of - d.index[0]).days < p.min_listing_days:
            continue
        hist[sym] = d
        eligible.append(sym)
    if not eligible:
        return []

    mixed = len({asset_class(s) for s in eligible}) > 1
    if not mixed:
        scores = {}
        for sym in eligible:
            med = float(hist[sym]["quote_volume"].tail(30).median())
            if not np.isnan(med):
                scores[sym] = med
        ok = [s for s, v in scores.items() if v >= p.liq_enter_usd]
        ranked = sorted(ok, key=lambda s: scores[s], reverse=True)
        keep = [s for s in current if s in scores and scores[s] >= p.liq_exit_usd and s in ok]
        for s in ranked:
            if len(keep) >= p.n_assets:
                break
            if s not in keep:
                keep.append(s)
        return keep[:p.n_assets]

    # ── mixed pool: venue liquidity floor, then class diversity, then correlation cap ──
    if venue_volume:
        # The floor scales with what we would actually trade: a market must show at least
        # `liq_venue_multiple` times the notional of one position in 24 h. A 1 000 $ account can
        # use a market that a 100 000 $ account must skip, and thin markets drop out on their own
        # as the account grows. `liq_exit_usd_venue` is the hard minimum below which we never trade.
        per_position = float(getattr(p, "position_notional", 0.0) or 0.0)
        floor = max(float(getattr(p, "liq_exit_usd_venue", 0.0) or 0.0),
                    float(getattr(p, "liq_venue_multiple", 0.0) or 0.0) * per_position)
        eligible = [s for s in eligible if float(venue_volume.get(s, 0.0)) >= floor]
        if not eligible:
            return []

    first_seen = {s: hist[s].index[0] for s in eligible}
    by_class: Dict[str, List[str]] = {}
    for s in eligible:
        by_class.setdefault(asset_class(s), []).append(s)
    pools = {k: sorted(v, key=lambda x: first_seen[x]) for k, v in by_class.items()}
    order: List[str] = []
    while any(pools.values()):
        for k in sorted(pools):
            if pools[k]:
                order.append(pools[k].pop(0))

    window = int(getattr(p, "corr_window", 120) or 120)
    cap = float(getattr(p, "corr_cap", 0.85) or 0.85)
    rets = {s: hist[s]["close"].pct_change(fill_method=None).tail(window) for s in eligible}

    def too_correlated(a: str, b: str) -> bool:
        ra, rb = rets.get(a), rets.get(b)
        if ra is None or rb is None:
            return False
        joined = pd.concat([ra, rb], axis=1).dropna()
        if len(joined) < 20:
            return False
        c = joined.iloc[:, 0].corr(joined.iloc[:, 1])
        return bool(c is not None and not np.isnan(c) and abs(c) > cap)

    keep: List[str] = []
    for s in [x for x in current if x in eligible] + [x for x in order if x not in current]:
        if len(keep) >= p.n_assets:
            break
        if s in keep:
            continue
        if any(too_correlated(s, t) for t in keep):
            continue
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
        w = asset_weight(close, p, periods_per_year(sym))
        # The clamp is what makes the book long-only; with the short side enabled a negative weight
        # is a short of that size (execution path still to be reviewed — see the research note).
        val = float(w.iloc[-1])
        out[sym] = (val if p.allow_shorts else max(0.0, val)) * share
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


def exit_ladder(close: pd.Series, p: TrendParams, short: bool = False) -> Dict[str, Any]:
    """Where this position actually exits, and how much leaves at each level.

    A trend book has no single stop-loss: the weight is the average of `len(p.lookbacks)`
    sub-strategies, each with its own never-falling trailing stop at the Donchian mid. As price
    falls, sub-strategies drop out one by one and the position shrinks in steps. Taking a fixed
    profit instead would destroy the edge — the whole return comes from the few long trends — so
    what the operator needs is not a TP but VISIBILITY of this ladder.

    Returns
        {"price", "active", "total", "levels": [{"lookback", "stop", "distance_pct",
         "share_exiting", "weight_after"}], "full_exit", "first_exit", "worst_case_pct"}
    where the levels are ordered from the nearest stop (the first to trigger) downwards.
    """
    out: Dict[str, Any] = {"price": None, "active": 0, "total": len(p.lookbacks), "levels": [],
                           "full_exit": None, "first_exit": None, "worst_case_pct": None}
    if close is None or len(close) == 0:
        return out
    price = float(close.iloc[-1])
    out["price"] = price
    levels = []
    for n in p.lookbacks:
        pos, stop = sub_strategy_positions(close, n, p.allow_shorts, p.short_size)
        last = float(pos.iloc[-1]) if len(pos) else 0.0
        # a short position's legs are the ones holding SHORT, and their stops sit ABOVE the price
        if (last <= 0) if not short else (last >= 0):
            continue
        s = float(stop.iloc[-1])
        if np.isnan(s):
            continue
        levels.append((int(n), s))
    if not levels:
        return out
    # nearest stop first: below the price for a long, above it for a short
    levels.sort(key=lambda x: x[1] if short else -x[1])
    out["active"] = len(levels)
    share = 1.0 / len(levels)                              # of the CURRENT position, not of the max
    remaining = 1.0
    for n, s in levels:
        remaining = max(0.0, remaining - share)
        out["levels"].append({
            "lookback": n, "stop": round(s, 8),
            "distance_pct": round(s / price - 1.0, 6) if price else None,
            "share_exiting": round(share, 6), "weight_after": round(remaining, 6),
        })
    out["first_exit"] = out["levels"][0]["stop"]
    out["full_exit"] = out["levels"][-1]["stop"]
    out["short"] = bool(short)
    # "worst case" is always the loss it can still take: for a short that is price RISING to the stop
    if price:
        move = out["full_exit"] / price - 1.0
        out["worst_case_pct"] = round(-move if short else move, 6)
    return out
