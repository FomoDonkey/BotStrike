"""Risk profiles: the honest way to trade a strategy harder (roadmap, Edgar 2026-09-03).

Leverage does not create edge. Measured on the validated multi-asset book (10 years, funding
charged, engine selection rule), the Sharpe is FLAT while return and drawdown scale together:

    target vol 0.10  ->  Sharpe 1.78, CAGR  5.1 %, vol 2.8 %, maxDD  4.2 %
    target vol 0.20  ->  Sharpe 1.76, CAGR 10.2 %, vol 5.6 %, maxDD  7.8 %
    target vol 0.30  ->  Sharpe 1.77, CAGR 15.2 %, vol 8.2 %, maxDD 11.3 %

So the correct control is **target volatility**, not a leverage multiplier: it scales the whole
book coherently and keeps the vol-targeting logic that made the strategy pass its gates. Raising it
without also raising the loss ladder just makes the circuit breaker halt the bot on an ordinary
losing streak, which is why a profile always moves both together.

The range 0.10-0.30 is what has been validated. Anything above that is NOT backed by the research
and the UI must say so.
"""
from __future__ import annotations

from typing import Any, Dict, List

# profile -> {target_vol, max_drawdown_pct, max_daily_loss_pct, max_weekly_loss_pct}
# The ladder is set so a normal drawdown for that risk level does NOT trip the breaker:
# max_drawdown_pct is ~1.3x the historical maxDD, weekly ~0.6x, daily ~0.25x.
PROFILES: Dict[str, Dict[str, float]] = {
    "conservative": {"trend_target_vol": 0.10, "max_drawdown_pct": 0.06,
                     "max_daily_loss_pct": 0.010, "max_weekly_loss_pct": 0.030},
    "balanced":     {"trend_target_vol": 0.20, "max_drawdown_pct": 0.10,
                     "max_daily_loss_pct": 0.020, "max_weekly_loss_pct": 0.050},
    "aggressive":   {"trend_target_vol": 0.30, "max_drawdown_pct": 0.15,
                     "max_daily_loss_pct": 0.030, "max_weekly_loss_pct": 0.070},
}

# What the research measured for each profile, so the UI never has to guess.
EXPECTED: Dict[str, Dict[str, float]] = {
    "conservative": {"sharpe": 1.78, "cagr": 0.051, "vol": 0.028, "max_dd": 0.042},
    "balanced":     {"sharpe": 1.76, "cagr": 0.102, "vol": 0.056, "max_dd": 0.078},
    "aggressive":   {"sharpe": 1.77, "cagr": 0.152, "vol": 0.082, "max_dd": 0.113},
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
    }


def catalog(equity: float = 1000.0) -> List[Dict[str, Any]]:
    return [describe(name, equity) for name in ("conservative", "balanced", "aggressive")]
