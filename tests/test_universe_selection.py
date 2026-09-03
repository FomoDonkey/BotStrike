"""Universe selection: crypto-only keeps the validated volume ranking; a mixed pool uses class
diversity + correlation cap + the VENUE liquidity floor (dollar volume is not comparable across
asset classes — see tasks/research_trend_multi_2026-09-03.md)."""
import numpy as np
import pandas as pd
import pytest

from strategies.trend_daily_model import TrendParams, asset_class, select_universe

AS_OF = pd.Timestamp("2026-09-03")


def _frame(days: int, quote_vol: float, seed: int = 0, drift: float = 0.0005):
    idx = pd.date_range(end=AS_OF, periods=days, freq="D")
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + rng.normal(drift, 0.02, days))
    return pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
                         "volume": 1.0, "quote_volume": quote_vol}, index=idx)


def _params(**kw):
    p = TrendParams(n_assets=kw.pop("n_assets", 3), min_listing_days=kw.pop("min_listing_days", 365))
    for k, v in kw.items():
        setattr(p, k, v)
    return p


def test_asset_class_defaults_to_crypto():
    assert asset_class("BTCUSDT") == "crypto" and asset_class("ADAUSDT") == "crypto"
    assert asset_class("XAU-USD") == "metal" and asset_class("SP500-USD") == "index"
    assert asset_class("WTI-USD") == "energy" and asset_class("NVDA-USD") == "equity"


def test_crypto_only_pool_keeps_the_validated_volume_ranking():
    data = {"BTCUSDT": _frame(800, 900e6, 1), "ETHUSDT": _frame(800, 400e6, 2),
            "ADAUSDT": _frame(800, 20e6, 3), "TINYUSDT": _frame(800, 1e6, 4)}
    sel = select_universe(data, AS_OF, _params(n_assets=3))
    assert sel == ["BTCUSDT", "ETHUSDT", "ADAUSDT"]      # ranked by volume, TINY below liq_enter
    # hysteresis: a current member stays while above liq_exit
    sel2 = select_universe(data, AS_OF, _params(n_assets=2), current=["ADAUSDT"])
    assert sel2[0] == "ADAUSDT" and len(sel2) == 2


def test_mixed_pool_ignores_incomparable_volume_and_diversifies_by_class():
    # NAS100 reports an absurd Yahoo "quote volume" and silver an absurdly small one: a volume
    # ranking would take the indices and drop the metals. The mixed rule must not.
    data = {"BTCUSDT": _frame(900, 900e6, 1), "ETHUSDT": _frame(900, 400e6, 2),
            "NAS100-USD": _frame(900, 227e12, 3), "SP500-USD": _frame(900, 37e12, 4),
            "XAG-USD": _frame(900, 1_590.0, 5), "XAU-USD": _frame(900, 2.9e6, 6),
            "WTI-USD": _frame(900, 21e6, 7)}
    sel = select_universe(data, AS_OF, _params(n_assets=4))
    classes = {asset_class(s) for s in sel}
    assert len(sel) == 4 and len(classes) == 4           # crypto, index, metal, energy
    assert any(s in ("XAG-USD", "XAU-USD") for s in sel), sel   # a metal survives despite its volume


def test_mixed_pool_applies_the_venue_liquidity_floor():
    data = {"BTCUSDT": _frame(900, 900e6, 1), "XAU-USD": _frame(900, 2.9e6, 2),
            "GOOGL-USD": _frame(900, 5e9, 3)}
    venue = {"BTCUSDT": 1_090_000.0, "XAU-USD": 199_000.0, "GOOGL-USD": 94.0}   # measured on Strike
    p = _params(n_assets=3, liq_exit_usd_venue=50_000.0)
    sel = select_universe(data, AS_OF, p, venue_volume=venue)
    assert "GOOGL-USD" not in sel and set(sel) == {"BTCUSDT", "XAU-USD"}
    # without venue data the floor cannot be applied and nothing is dropped for liquidity
    assert len(select_universe(data, AS_OF, p)) == 3


def test_mixed_pool_drops_a_correlated_candidate():
    base = _frame(900, 1e9, 11)
    twin = base.copy()                                   # perfectly correlated with base
    data = {"BTCUSDT": base, "ETHUSDT": twin, "XAU-USD": _frame(900, 3e6, 12)}
    sel = select_universe(data, AS_OF, _params(n_assets=3, corr_cap=0.9))
    assert len(sel) == 2 and "XAU-USD" in sel            # one of the twins is dropped
    loose = select_universe(data, AS_OF, _params(n_assets=3, corr_cap=1.01))
    assert len(loose) == 3                               # cap off -> both twins allowed


def test_short_history_is_never_selected():
    data = {"BTCUSDT": _frame(900, 900e6, 1), "NEW-USD": _frame(100, 5e9, 2)}
    assert select_universe(data, AS_OF, _params(n_assets=3)) == ["BTCUSDT"]


def test_venue_floor_scales_with_position_size():
    data = {"BTCUSDT": _frame(900, 900e6, 1), "XAU-USD": _frame(900, 3e6, 2),
            "SP500-USD": _frame(900, 37e12, 3), "NAS100-USD": _frame(900, 227e12, 4)}
    venue = {"BTCUSDT": 1_451_020.0, "XAU-USD": 197_210.0, "SP500-USD": 40_347.0, "NAS100-USD": 3_989.0}
    # small account: one position is ~333 $, so a market needs >= 16 650 $/24 h
    small = _params(n_assets=4, liq_exit_usd_venue=5_000.0, liq_venue_multiple=50.0, position_notional=333.0)
    sel = select_universe(data, AS_OF, small, venue_volume=venue)
    assert set(sel) == {"BTCUSDT", "XAU-USD", "SP500-USD"}      # NAS100 too thin even for 333 $
    # bigger account: one position is 10 000 $, so it needs >= 500 000 $/24 h
    big = _params(n_assets=4, liq_exit_usd_venue=5_000.0, liq_venue_multiple=50.0, position_notional=10_000.0)
    assert select_universe(data, AS_OF, big, venue_volume=venue) == ["BTCUSDT"]
    # hard minimum still applies when no multiple is configured
    hard = _params(n_assets=4, liq_exit_usd_venue=100_000.0, liq_venue_multiple=0.0, position_notional=0.0)
    assert set(select_universe(data, AS_OF, hard, venue_volume=venue)) == {"BTCUSDT", "XAU-USD"}
