"""Pin core/indicators.py against the implementation it replaced.

The 2026-09-04 optimisation pass rewrote five things for speed and nothing for behaviour:

  * `volatility_percentile` lost its `rolling(100).apply(fn, raw=False)` Python callable,
  * `atr` builds the true range with `np.fmax` instead of a three-column frame,
  * `adx` and `directional_indicators` share one `_di_pair` instead of computing it twice,
  * `compute_all` writes all its columns in one assignment instead of twenty-one,
  * `compute_all` gained `only=`, which skips columns the caller never reads.

Every reference below is the *original* code, pasted verbatim. If an assertion here fails, the
optimisation changed a number — which is the one thing it is not allowed to do. NaN patterns are
compared as strictly as the values: the old rolling().apply() emitted NaN before min_periods was
met and the vectorised version has to emit NaN in exactly the same places, or a strategy that
gates on `pd.isna(...)` silently changes behaviour.
"""
import numpy as np
import pandas as pd
import pytest

from core.indicators import Indicators


# ──────────────────────────────────────────────────────────────────────────────
# The pre-optimisation implementations, verbatim.
# ──────────────────────────────────────────────────────────────────────────────
def ref_volatility_percentile(series, atr_period=14, lookback=100):
    returns = series.pct_change().abs()
    vol = returns.rolling(window=atr_period, min_periods=2).mean()

    def percentile_rank(window):
        if len(window) < 2:
            return 0.5
        return (window.values[:-1] < window.values[-1]).sum() / (len(window) - 1)

    return vol.rolling(window=lookback, min_periods=10).apply(percentile_rank, raw=False)


def ref_atr(high, low, close, period=14):
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return true_range.ewm(span=2 * period - 1, adjust=False).mean()


def ref_adx(high, low, close, period=14):
    plus_dm_raw = high.diff()
    minus_dm_raw = -low.diff()
    plus_dm = plus_dm_raw.where((plus_dm_raw > minus_dm_raw) & (plus_dm_raw > 0), 0.0)
    minus_dm = minus_dm_raw.where((minus_dm_raw > plus_dm_raw) & (minus_dm_raw > 0), 0.0)
    atr_val = ref_atr(high, low, close, period)
    smoothed_plus = plus_dm.ewm(span=2 * period - 1, adjust=False).mean()
    smoothed_minus = minus_dm.ewm(span=2 * period - 1, adjust=False).mean()
    plus_di = 100 * (smoothed_plus / atr_val.replace(0, np.nan))
    minus_di = 100 * (smoothed_minus / atr_val.replace(0, np.nan))
    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    return dx.ewm(span=2 * period - 1, adjust=False).mean()


def ref_directional_indicators(high, low, close, period=14):
    plus_dm_raw = high.diff()
    minus_dm_raw = -low.diff()
    plus_dm = plus_dm_raw.where((plus_dm_raw > minus_dm_raw) & (plus_dm_raw > 0), 0.0)
    minus_dm = minus_dm_raw.where((minus_dm_raw > plus_dm_raw) & (minus_dm_raw > 0), 0.0)
    atr_val = ref_atr(high, low, close, period)
    smoothed_plus = plus_dm.ewm(span=2 * period - 1, adjust=False).mean()
    smoothed_minus = minus_dm.ewm(span=2 * period - 1, adjust=False).mean()
    plus_di = 100 * (smoothed_plus / atr_val.replace(0, np.nan))
    minus_di = 100 * (smoothed_minus / atr_val.replace(0, np.nan))
    return plus_di.fillna(0), minus_di.fillna(0)


# ──────────────────────────────────────────────────────────────────────────────
# Frames of the shapes the engine really passes: 1m windows, 5m/1h resamples,
# frames shorter than the lookbacks, and a flat stretch with no trades.
# ──────────────────────────────────────────────────────────────────────────────
def make_ohlcv(n, seed=7, flat_from=None, zero_volume=False):
    rng = np.random.default_rng(seed)
    close = 30_000 + np.cumsum(rng.normal(0, 12, n))
    high = close + np.abs(rng.normal(0, 6, n))
    low = close - np.abs(rng.normal(0, 6, n))
    open_ = close + rng.normal(0, 4, n)
    volume = np.abs(rng.normal(50, 15, n))
    df = pd.DataFrame({"open": open_, "high": high, "low": low,
                       "close": close, "volume": volume})
    if flat_from is not None:
        # a stretch with no trades at all: identical OHLC, zero volume, zero true range
        v = float(df["close"].iloc[flat_from])
        df.loc[flat_from:, ["open", "high", "low", "close"]] = v
        df.loc[flat_from:, "volume"] = 0.0
    if zero_volume:
        df["volume"] = 0.0
    return df


CASES = {
    "1m_501": make_ohlcv(501),
    "1m_120": make_ohlcv(120, seed=11),
    "short_35": make_ohlcv(35, seed=13),        # shorter than most lookbacks
    "tiny_3": make_ohlcv(3, seed=17),           # shorter than min_periods everywhere
    "flat_tail": make_ohlcv(200, seed=19, flat_from=120),
    "no_volume": make_ohlcv(150, seed=23, zero_volume=True),
}
CONFIGS = [None,
           {"ema_fast": 12, "ema_slow": 26, "zscore_lookback": 100},
           {"ema_fast": 9, "ema_slow": 21, "zscore_lookback": 50}]


def assert_same(a, b, label):
    """Identical values AND an identical NaN pattern — no tolerance on either."""
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    assert a.shape == b.shape, f"{label}: shape {a.shape} != {b.shape}"
    assert np.array_equal(np.isnan(a), np.isnan(b)), f"{label}: NaN pattern changed"
    m = ~np.isnan(a)
    if m.any():
        worst = float(np.max(np.abs(a[m] - b[m])))
        assert worst <= 1e-12, f"{label}: max abs diff {worst}"


@pytest.mark.parametrize("name", sorted(CASES))
def test_volatility_percentile_matches_rolling_apply(name):
    df = CASES[name]
    assert_same(ref_volatility_percentile(df["close"]),
                Indicators.volatility_percentile(df["close"]), f"{name}.vol_pct")


@pytest.mark.parametrize("name", sorted(CASES))
@pytest.mark.parametrize("period", [7, 14, 20])
def test_atr_matches_concat_max(name, period):
    df = CASES[name]
    assert_same(ref_atr(df["high"], df["low"], df["close"], period),
                Indicators.atr(df["high"], df["low"], df["close"], period),
                f"{name}.atr{period}")


@pytest.mark.parametrize("name", sorted(CASES))
def test_adx_and_di_match_the_duplicated_originals(name):
    df = CASES[name]
    h, lo, c = df["high"], df["low"], df["close"]
    assert_same(ref_adx(h, lo, c, 14), Indicators.adx(h, lo, c, 14), f"{name}.adx")
    ref_p, ref_m = ref_directional_indicators(h, lo, c, 14)
    got_p, got_m = Indicators.directional_indicators(h, lo, c, 14)
    assert_same(ref_p, got_p, f"{name}.plus_di")
    assert_same(ref_m, got_m, f"{name}.minus_di")


def test_sharing_the_atr_and_di_changes_nothing():
    """The `atr_val=` / `di=` shortcuts exist for compute_all; they must not alter a number."""
    df = CASES["1m_501"]
    h, lo, c = df["high"], df["low"], df["close"]
    atr14 = Indicators.atr(h, lo, c, 14)
    di14 = Indicators._di_pair(h, lo, c, 14, atr_val=atr14)
    assert_same(Indicators.adx(h, lo, c, 14), Indicators.adx(h, lo, c, 14, di=di14), "adx via di")
    assert_same(Indicators.adx(h, lo, c, 14),
                Indicators.adx(h, lo, c, 14, atr_val=atr14), "adx via atr")
    ref_p, ref_m = Indicators.directional_indicators(h, lo, c, 14)
    got_p, got_m = Indicators.directional_indicators(h, lo, c, 14, di=di14)
    assert_same(ref_p, got_p, "plus_di via di")
    assert_same(ref_m, got_m, "minus_di via di")


@pytest.mark.parametrize("name", sorted(CASES))
@pytest.mark.parametrize("cfg_idx", range(len(CONFIGS)))
def test_compute_all_column_set_and_order(name, cfg_idx):
    df = CASES[name].copy()
    out = Indicators.compute_all(df, CONFIGS[cfg_idx])
    produced = [c for c in out.columns if c not in ("open", "high", "low", "close", "volume")]
    assert produced == list(Indicators.ALL_COLUMNS), "column order or set changed"


# The subsets the engine actually asks for. mean_reversion reads three columns off its 1H frame
# and six off its 5m frame; asking for the rest was the bulk of a backtest.
SUBSETS = [
    ("ema_12", "ema_26", "adx"),
    ("atr", "zscore", "adx", "rsi", "bb_upper", "bb_lower"),
    ("vol_pct",),
    ("plus_di", "minus_di"),
    ("atr",),
    tuple(Indicators.ALL_COLUMNS),
]


@pytest.mark.parametrize("name", sorted(CASES))
@pytest.mark.parametrize("subset", SUBSETS, ids=lambda s: "+".join(s)[:40])
def test_only_subset_is_identical_to_the_full_pass(name, subset):
    base = CASES[name]
    full = Indicators.compute_all(base.copy())
    part = Indicators.compute_all(base.copy(), only=subset)
    for col in subset:
        assert_same(full[col], part[col], f"{name}.{col} under only=")
    skipped = [c for c in Indicators.ALL_COLUMNS if c not in subset]
    assert not [c for c in skipped if c in part.columns], "only= produced unrequested columns"


def test_only_rejects_an_unknown_column():
    """A typo must raise. Series.get('typo', 0) would have handed a strategy a silent zero."""
    with pytest.raises(ValueError, match="momentum_11"):
        Indicators.compute_all(CASES["1m_120"].copy(), only=("atr", "momentum_11"))


def test_empty_frame_is_returned_untouched():
    empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    assert Indicators.compute_all(empty).empty
    assert Indicators.compute_all(empty, only=("atr",)).empty


# ──────────────────────────────────────────────────────────────────────────────
# The `only=` subsets mean_reversion declares must cover every column it reads.
# Every read there is `.get(col, default)`, so a column that stopped being computed
# would come back as its default — a silent behaviour change, not an exception.
# ──────────────────────────────────────────────────────────────────────────────
def test_mean_reversion_declares_every_indicator_column_it_reads():
    import re
    from pathlib import Path

    import strategies.mean_reversion as mr

    src = Path(mr.__file__).read_text(encoding="utf-8")
    # drop the declarations themselves and this module's own doc comments
    body = "\n".join(line for line in src.splitlines()
                     if not line.startswith(("M5_INDICATORS", "H1_INDICATORS", "#")))
    read = {m for m in re.findall(r'"([a-z_0-9]+)"', body) if m in Indicators.ALL_COLUMNS}
    declared = set(mr.M5_INDICATORS) | set(mr.H1_INDICATORS)
    undeclared = read - declared
    assert not undeclared, (
        f"mean_reversion reads {sorted(undeclared)} but does not ask compute_all for it — "
        f"the .get() default would be used instead of the real value. "
        f"Add it to M5_INDICATORS or H1_INDICATORS."
    )
