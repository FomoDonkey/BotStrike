"""Risk profiles: the honest way to trade a strategy harder (roadmap, Edgar 2026-09-03).

Leverage does not create edge. Measured on the validated multi-asset book (10 years, funding
charged, engine selection rule), the Sharpe is FLAT while return and drawdown scale together:

    target vol 0.10  ->  Sharpe 1.93, CAGR  5.6 %, vol 2.8 %, maxDD  3.9 %
    target vol 0.20  ->  Sharpe 1.92, CAGR 11.2 %, vol 5.6 %, maxDD  7.6 %
    target vol 0.30  ->  Sharpe 1.92, CAGR 16.7 %, vol 8.2 %, maxDD 11.2 %

Re-measured 2026-09-04. The previous figures (1.78 / 1.76 / 1.77, CAGR 5.1 / 10.2 / 15.2 %) came from
the run that GUESSED funding by asset class. Funding is now measured on Strike itself, where a
diversified long book turns out to be nearly carry-neutral (the 10-year cost fell from 10.6 to 1.5
points of equity), so the old numbers understated every profile and the Risk page was quoting a
pessimistic expectation as if it were the measurement.

So the correct control is **target volatility**, not a leverage multiplier: it scales the whole
book coherently and keeps the vol-targeting logic that made the strategy pass its gates. Raising it
without also raising the loss ladder just makes the circuit breaker halt the bot on an ordinary
losing streak, which is why a profile always moves both together.

The range 0.10-0.30 is what has been validated. Anything above that is NOT backed by the research
and the UI must say so.

THE LEVERAGE CAP IS A CEILING, NOT A MULTIPLIER (measured 2026-09-04, scripts/leverage_cap_study.py,
same 14-market panel, 3,654 days). The vol scalar is target_vol / realised_vol clipped at the cap, so
the cap only binds when a market is quiet enough that the target asks for more than it allows:

    profile        cap 2 -> cap 3                       days the cap binds
    conservative   5.6 % CAGR  -> 5.6 %,  DD 3.9 %       0.0 %
    balanced      11.2 % CAGR -> 11.3 %,  DD 7.6 %       0.9 % (was 0.9 % at cap 2)
    aggressive    16.7 % CAGR -> 17.2 %,  DD 11.3 %      5.6 % at cap 2, 0.9 % at cap 3

Aggressive runs at cap 3 (Edgar, 2026-09-04). The cap is a small lever by construction; the one that
actually scales is target volatility, measured at cap 3 over the same panel:

    target vol 0.30 -> Sharpe 1.92, CAGR 17.2 %, maxDD 11.3 %, worst day -3.73 %   VALIDATED RANGE
    target vol 0.45 -> Sharpe 1.92, CAGR 25.8 %, maxDD 16.5 %, worst day -5.60 %
    target vol 0.60 -> Sharpe 1.88, CAGR 33.1 %, maxDD 21.4 %, worst day -6.80 %
    target vol 0.80 -> Sharpe 1.84, CAGR 41.5 %, maxDD 27.5 %, worst day -8.28 %   <- AGGRESSIVE

Sharpe is flat across all of it: there is no free return up there, only a bigger position and a
proportionally bigger hole.

**AGGRESSIVE SITS AT 0.80, OUTSIDE THE 0.10-0.30 RANGE THE RESEARCH VALIDATED.** That is Edgar's
choice, made against the measured menu in dollars on his own book, and it is recorded here rather
than hidden: `describe()` returns `beyond_validated_range` for it and the Risk page says so on the
card. What that buys and costs on a 1,014 $ book: +421 $/yr expected, a worst drawdown of 279 $, a
worst single day of 84 $, and — the number that matters most and is easiest to overlook — the book
spent its LONGEST stretch 620 days below its previous high, and 827 days more than 10 % under it.
Return and drawdown scale together; time underwater scales with them.
"""
from __future__ import annotations

from typing import Any, Dict, List

# profile -> {target_vol, max_drawdown_pct, max_daily_loss_pct, max_weekly_loss_pct}
# The ladder is set so a normal drawdown for that risk level does NOT trip the breaker:
# max_drawdown_pct is ~1.3x the historical maxDD, weekly ~0.6x, daily ~0.25x.
PROFILES: Dict[str, Dict[str, float]] = {
    "conservative": {"trend_target_vol": 0.10, "max_drawdown_pct": 0.06,
                     "max_daily_loss_pct": 0.010, "max_weekly_loss_pct": 0.030,
                     "trend_leverage_cap": 2.0},
    "balanced":     {"trend_target_vol": 0.20, "max_drawdown_pct": 0.10,
                     "max_daily_loss_pct": 0.020, "max_weekly_loss_pct": 0.050,
                     "trend_leverage_cap": 2.0},
    # AGGRESSIVE IS DELIBERATELY BEYOND THE VALIDATED RANGE (Edgar, 2026-09-04). He was shown the
    # measured menu in dollars on his own book and chose the top row. 0.80 target volatility with the
    # 3x ceiling: scripts/aggressive_080_study.py, same 14-market panel, 3,654 days — Sharpe 1.84,
    # CAGR 41.5 %, maxDD 27.5 %, 6/6 gates, and it survives 25 bps/side (CAGR 31.7 %) and funding x3.
    #
    # The loss ladder below is NOT the usual ratio copied off a calmer profile: it is set from the
    # measured tail at THIS size, because a breaker tuned for a 3 % day would halt the bot on an
    # ordinary one here. Worst day seen -8.28 %, worst week -11.46 %, worst drawdown -27.51 %.
    "aggressive":   {"trend_target_vol": 0.80, "max_drawdown_pct": 0.36,
                     "max_daily_loss_pct": 0.110, "max_weekly_loss_pct": 0.140,
                     "trend_leverage_cap": 3.0},
}

# What the research measured for each profile, so the UI never has to guess.
EXPECTED: Dict[str, Dict[str, float]] = {
    "conservative": {"sharpe": 1.93, "cagr": 0.056, "vol": 0.028, "max_dd": 0.039},
    "balanced":     {"sharpe": 1.92, "cagr": 0.113, "vol": 0.057, "max_dd": 0.076},
    # measured at target vol 0.80 with the 3x cap (aggressive_080_study, 2026-09-04)
    "aggressive":   {"sharpe": 1.84, "cagr": 0.415, "vol": 0.200, "max_dd": 0.275,
                     "worst_day": 0.0828, "worst_week": 0.1146, "longest_underwater_days": 620},
}

VALIDATED_RANGE = (0.10, 0.30)


def profile_of(trading: Any) -> str:
    """Which profile the current settings match, or 'custom'."""
    for name, values in PROFILES.items():
        if all(abs(float(getattr(trading, k, float("nan"))) - v) < 1e-9 for k, v in values.items()):
            return name
    return "custom"


def apply_profile(trading: Any, name: str) -> Dict[str, float]:
    """Apply a profile in place. Returns the fields that changed. Unknown name -> no change."""
    values = PROFILES.get(str(name or "").strip().lower())
    if not values:
        return {}
    changed: Dict[str, float] = {}
    for k, v in values.items():
        if abs(float(getattr(trading, k, float("nan"))) - v) > 1e-9:
            setattr(trading, k, v)
            changed[k] = v
    return changed


def describe(name: str, equity: float = 1000.0) -> Dict[str, Any]:
    """Human-facing description of a profile at a given account size."""
    key = str(name or "").strip().lower()
    exp = EXPECTED.get(key)
    cfg = PROFILES.get(key)
    if not exp or not cfg:
        return {"profile": key or "custom", "validated": False,
                "note": "Custom settings: no validated expectation for this combination."}
    lo, hi = VALIDATED_RANGE
    beyond = not (lo - 1e-9 <= float(cfg["trend_target_vol"]) <= hi + 1e-9)
    return {
        "profile": key, "validated": True,
        # A named profile can still sit outside the range the research covers: aggressive does, by
        # Edgar's explicit choice. Saying "validated: true" and nothing else would hide that.
        "beyond_validated_range": beyond,
        "worst_day": exp.get("worst_day"), "worst_week": exp.get("worst_week"),
        "longest_underwater_days": exp.get("longest_underwater_days"),
        "target_vol": cfg["trend_target_vol"],
        # the ceiling on the vol scalar. It is NOT "the leverage the bot uses": the size is
        # target_vol / realised_vol, and this only clips it on the quietest days.
        "leverage_cap": cfg.get("trend_leverage_cap", 2.0),
        "expected_cagr": exp["cagr"], "expected_vol": exp["vol"], "expected_max_dd": exp["max_dd"],
        "sharpe": exp["sharpe"],
        "expected_year_usd": round(equity * exp["cagr"], 2),
        "expected_worst_drawdown_usd": round(equity * exp["max_dd"], 2),
        "limits": {"max_drawdown_pct": cfg["max_drawdown_pct"],
                   "max_daily_loss_pct": cfg["max_daily_loss_pct"],
                   "max_weekly_loss_pct": cfg["max_weekly_loss_pct"]},
        "note": (("BEYOND THE VALIDATED RANGE. The research covers target volatility 0.10-0.30; "
                  "this level was chosen deliberately against the measured numbers. Same strategy "
                  "and nearly the same Sharpe — the extra return comes entirely from a bigger "
                  "position, and the drawdown and the time spent underwater grow with it.")
                 if beyond else
                 ("Same strategy, same Sharpe: return and drawdown scale together. "
                  "The loss limits move with the profile so an ordinary losing streak does not "
                  "halt the bot.")),
        "leverage_note": ("Ceiling on the position scalar, not a fixed multiplier: each market is "
                          "sized at target vol / its own realised vol, and this caps the result on "
                          "the quietest days. Measured over 10 years it binds on 5.6 % of "
                          "asset-days at 2x and 0.9 % at 3x."),
    }


def catalog(equity: float = 1000.0) -> List[Dict[str, Any]]:
    return [describe(name, equity) for name in ("conservative", "balanced", "aggressive")]
