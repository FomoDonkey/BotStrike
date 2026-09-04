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

Aggressive therefore runs at cap 3 (Edgar, 2026-09-04): it buys half a point of CAGR for no extra
drawdown, and passes 6/6 of the gates. But it is a small lever by construction, and anyone reaching
for it to "trade harder" wants the OTHER dial. Target volatility, at cap 3, is what actually scales:

    target vol 0.30 -> Sharpe 1.92, CAGR 17.2 %, maxDD 11.3 %   (aggressive today)
    target vol 0.45 -> Sharpe 1.92, CAGR 25.8 %, maxDD 16.5 %   beyond the validated range
    target vol 0.60 -> Sharpe 1.88, CAGR 33.1 %, maxDD 21.4 %   beyond the validated range
    target vol 0.80 -> Sharpe 1.84, CAGR 41.5 %, maxDD 27.5 %   beyond the validated range

Sharpe is flat across all of it: there is no free return up there, only a bigger position and a
proportionally bigger hole. Raising target_vol past 0.30 also needs the loss ladder raised with it or
the circuit breaker halts the bot on an ordinary drawdown.
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
    # Aggressive takes the 3x ceiling Edgar asked for on 2026-09-04. Measured, not assumed:
    # scripts/leverage_cap_study.py over the validated 14-market panel, 3,654 days.
    "aggressive":   {"trend_target_vol": 0.30, "max_drawdown_pct": 0.15,
                     "max_daily_loss_pct": 0.030, "max_weekly_loss_pct": 0.070,
                     "trend_leverage_cap": 3.0},
}

# What the research measured for each profile, so the UI never has to guess.
EXPECTED: Dict[str, Dict[str, float]] = {
    "conservative": {"sharpe": 1.93, "cagr": 0.056, "vol": 0.028, "max_dd": 0.039},
    "balanced":     {"sharpe": 1.92, "cagr": 0.113, "vol": 0.057, "max_dd": 0.076},
    # re-measured at the 3x cap (leverage_cap_study, 2026-09-04): +0.5 points of CAGR for no extra
    # drawdown, because the cap only ever bound on the quietest 5.6 % of asset-days
    "aggressive":   {"sharpe": 1.92, "cagr": 0.172, "vol": 0.084, "max_dd": 0.113},
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
    return {
        "profile": key, "validated": True,
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
        "note": ("Same strategy, same Sharpe: return and drawdown scale together. "
                 "The loss limits move with the profile so an ordinary losing streak does not "
                 "halt the bot."),
        "leverage_note": ("Ceiling on the position scalar, not a fixed multiplier: each market is "
                          "sized at target vol / its own realised vol, and this caps the result on "
                          "the quietest days. Measured over 10 years it binds on 5.6 % of "
                          "asset-days at 2x and 0.9 % at 3x."),
    }


def catalog(equity: float = 1000.0) -> List[Dict[str, Any]]:
    return [describe(name, equity) for name in ("conservative", "balanced", "aggressive")]
