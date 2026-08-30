"""ADA guard fix (audit R2 risk_sizing-01, applied 2026-08-31).

The entry≈stop guard in RiskManager._adjust_position_size used an ABSOLUTE
price-unit threshold (`risk_per_unit < 0.001`) that equalled ~50 bps on ADA at
$0.20 — MR's 2×ATR stop (~39 bps) never cleared it, so every ADA signal was
silently rejected (0 ADA trades in the paper DB, confirmed 2026-08-31).
The guard is now RELATIVE: 1e-5 (0.1 bps) of entry price.
"""
from config.settings import Settings
from core.types import Signal, Side, StrategyType
from risk.risk_manager import RiskManager


def _mgr_and_cfg(symbol: str):
    settings = Settings()
    mgr = RiskManager(settings)
    cfg = next(s for s in settings.symbols if s.symbol == symbol)
    return mgr, cfg


def _signal(symbol: str, entry: float, sl: float) -> Signal:
    return Signal(
        strategy=StrategyType.MEAN_REVERSION, symbol=symbol, side=Side.BUY,
        strength=0.8, entry_price=entry, stop_loss=sl,
        take_profit=entry * 1.01 if entry > 0 else 0.0, size_usd=100.0,
    )


def test_ada_39bps_stop_is_sized_not_rejected():
    # MR's real stop on ADA: 2×ATR ≈ 39 bps at $0.2007 → 0.00078 price units,
    # below the old absolute 0.001 threshold → was always rejected.
    mgr, cfg = _mgr_and_cfg("ADA-USD")
    sig = _signal("ADA-USD", 0.2007, 0.2007 * (1 - 0.0039))
    assert mgr._adjust_position_size(sig, cfg) > 0.0


def test_degenerate_stop_still_rejected():
    mgr, cfg = _mgr_and_cfg("ADA-USD")
    sig = _signal("ADA-USD", 0.2007, 0.2007)  # entry == stop → undefined risk
    assert mgr._adjust_position_size(sig, cfg) == 0.0


def test_zero_entry_price_rejected():
    mgr, cfg = _mgr_and_cfg("ADA-USD")
    sig = _signal("ADA-USD", 0.0, 0.0)
    assert mgr._adjust_position_size(sig, cfg) == 0.0


def test_btc_normal_stop_unaffected():
    # BTC at $78k with a 40 bps stop: fine before, fine now.
    mgr, cfg = _mgr_and_cfg("BTC-USD")
    sig = _signal("BTC-USD", 78_000.0, 78_000.0 * (1 - 0.004))
    assert mgr._adjust_position_size(sig, cfg) > 0.0
