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

    target vol 0.10 -> Sharpe 1.93, CAGR  5.6 %, maxDD  3.9 %, worst day -1.24 %   <- CONSERVATIVE
    target vol 0.30 -> Sharpe 1.92, CAGR 17.2 %, maxDD 11.3 %, worst day -3.73 %
    target vol 0.45 -> Sharpe 1.92, CAGR 25.8 %, maxDD 16.5 %, worst day -5.60 %   <- BALANCED
    target vol 0.60 -> Sharpe 1.88, CAGR 33.1 %, maxDD 21.4 %, worst day -6.80 %
    target vol 0.80 -> Sharpe 1.84, CAGR 41.5 %, maxDD 27.5 %, worst day -8.28 %   <- AGGRESSIVE

All three shipped levels went through the book's own eleven gates at their own settings and passed
11/11 (scripts/validate_profile.py — one generic validator, so no level gets an easier exam).

Sharpe is flat across all of it: there is no free return up there, only a bigger position and a
proportionally bigger hole.

**AGGRESSIVE SITS AT 0.80 AND IS VALIDATED THERE** (scripts/validate_aggressive.py, 2026-09-04).
Edgar chose the level against the measured menu, then asked for it to be validated like the other
two rather than merely measured — so it went through the book's OWN eleven GO/NO-GO gates at its own
settings, same maths, same panel, and passed 11/11:

    Sharpe 1.84 · CAGR 41.5 % · vol 20.0 % · maxDD 27.5 % · skew +0.58 · DSR 1.00 over 11 trials
    beats crypto-only at the SAME risk level on both Sharpe (1.84 vs 1.31) and drawdown
    survives 25 bps/side (Sharpe 1.48) and funding x3 (1.78)
    no look-ahead artefact (shift 3 -> 1.67, well above half of 1.84)
    holds out of sample: 2022+ 1.76 · first half 2.06 · second half 1.63

ONE GATE IS EVALUATED AGAINST THIS PROFILE'S OWN BUDGET, DELIBERATELY. "maxDD < 15 %" is a risk
BUDGET, not a test of whether the edge exists: vol targeting scales return and drawdown together at
constant Sharpe, so a higher target volatility is *supposed* to draw down more. Holding 0.80 to a
threshold written for 0.20 would be a category error, so it is checked against `max_drawdown_pct`
(36 %), which is itself derived from the measured tail. Every gate that asks whether the EDGE is real
is unchanged and passed on its own terms.

What the level costs, on a 1,014 $ book: +421 $/yr expected, a worst drawdown of 279 $, a worst
single day of 84 $, and — the number that matters most and is easiest to overlook — the book spent
its LONGEST stretch 620 days below its previous high, and 827 days more than 10 % under it. Validated
does not mean comfortable. Return and drawdown scale together; time underwater scales with them, and
the Risk card states all three.
"""
from __future__ import annotations

from typing import Any, Dict, List

# profile -> {target_vol, max_drawdown_pct, max_daily_loss_pct, max_weekly_loss_pct}
# The ladder is set so a normal drawdown for that risk level does NOT trip the breaker:
# max_drawdown_pct is ~1.3x the historical maxDD, weekly ~0.6x, daily ~0.25x.
PROFILES: Dict[str, Dict[str, float]] = {
    # Re-measured 2026-09-05 with each series annualised on its own calendar (TradFi 252 days):
    # worst day -1.45 %, worst week -2.00 %, maxDD 4.16 % -> daily 2 %, weekly 3 %, drawdown 6 %.
    # The old 1 % daily limit sat BELOW the worst day the validated strategy had seen (1.24 %).
    "conservative": {"trend_target_vol": 0.10, "max_drawdown_pct": 0.06,
                     "max_daily_loss_pct": 0.020, "max_weekly_loss_pct": 0.030,
                     "trend_leverage_cap": 2.0},
    # Balanced moved from 0.20 to 0.45 on 2026-09-04, Edgar's choice from the same measured menu.
    # Ladder from the measured tail at THIS size (2026-09-05, per-asset annualisation): worst day
    # -6.13 %, worst week -8.50 %, maxDD 17.6 % -> daily 8 %, weekly 11 %, drawdown 23 %.
    "balanced":     {"trend_target_vol": 0.45, "max_drawdown_pct": 0.23,
                     "max_daily_loss_pct": 0.080, "max_weekly_loss_pct": 0.110,
                     "trend_leverage_cap": 3.0},
    # AGGRESSIVE IS DELIBERATELY BEYOND THE VALIDATED RANGE (Edgar, 2026-09-04). He was shown the
    # measured menu in dollars on his own book and chose the top row. 0.80 target volatility with the
    # 3x ceiling: scripts/aggressive_080_study.py, same 14-market panel, 3,654 days — Sharpe 1.84,
    # CAGR 41.5 %, maxDD 27.5 %, 6/6 gates, and it survives 25 bps/side (CAGR 31.7 %) and funding x3.
    #
    # The loss ladder below is NOT the usual ratio copied off a calmer profile: it is set from the
    # measured tail at THIS size, because a breaker tuned for a 3 % day would halt the bot on an
    # ordinary one here. Re-measured 2026-09-05 with per-asset annualisation (TradFi on 252 days,
    # which sizes gold/silver/oil/the index ~20 % closer to the vol target): worst day -9.08 %,
    # worst week -12.38 %, worst drawdown -29.8 % -> daily 12 %, weekly 15 %, drawdown 39 %
    # (x1.25 / x1.20 / x1.30 over the tail, scripts/aggressive_080_study.py). 11/11 gates.
    "aggressive":   {"trend_target_vol": 0.80, "max_drawdown_pct": 0.39,
                     "max_daily_loss_pct": 0.120, "max_weekly_loss_pct": 0.150,
                     "trend_leverage_cap": 3.0},
}

# What the research measured for each profile, so the UI never has to guess.
EXPECTED: Dict[str, Dict[str, float]] = {
    "conservative": {"sharpe": 1.93, "cagr": 0.063, "vol": 0.032, "max_dd": 0.042,
                     "worst_day": 0.0145, "worst_week": 0.0200, "longest_underwater_days": 599,
                     "gates_passed": 11, "gates_total": 11, "dsr": 1.00},
    "balanced":     {"sharpe": 1.89, "cagr": 0.279, "vol": 0.135, "max_dd": 0.176,
                     "worst_day": 0.0613, "worst_week": 0.0850, "longest_underwater_days": 613,
                     "gates_passed": 11, "gates_total": 11, "dsr": 1.00},
    # measured at target vol 0.80 with the 3x cap, per-asset annualisation (2026-09-05)
    "aggressive":   {"sharpe": 1.84, "cagr": 0.439, "vol": 0.210, "max_dd": 0.298,
                     "worst_day": 0.0908, "worst_week": 0.1238, "longest_underwater_days": 721,
                     "gates_passed": 11, "gates_total": 11, "dsr": 1.00},
}

# 0.80 is in the range because it was PUT through the eleven gates and passed them, not because the
# bound was moved to make room (scripts/validate_aggressive.py, 2026-09-04). Anything above 0.80 is
# still unstudied, and the UI must keep saying so.
VALIDATED_RANGE = (0.10, 0.80)


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
        "note": (("BEYOND THE VALIDATED RANGE. Nothing above 0.80 target volatility has been "
                  "studied, so the numbers on this card do not apply to it.")
                 if beyond else
                 ("Same strategy, same Sharpe: return and drawdown scale together. "
                  "The loss limits move with the profile so an ordinary losing streak does not "
                  "halt the bot.")),
        # 11/11 on the book's own gates at this profile's settings. Kept as a field rather than a
        # sentence so the UI can show it without anyone having to trust a blurb.
        "gates_passed": exp.get("gates_passed"), "gates_total": exp.get("gates_total"),
        "dsr": exp.get("dsr"),
        "leverage_note": ("Ceiling on the position scalar, not a fixed multiplier: each market is "
                          "sized at target vol / its own realised vol, and this caps the result on "
                          "the quietest days. Measured over 10 years it binds on 5.6 % of "
                          "asset-days at 2x and 0.9 % at 3x."),
    }


def catalog(equity: float = 1000.0) -> List[Dict[str, Any]]:
    return [describe(name, equity) for name in ("conservative", "balanced", "aggressive")]
