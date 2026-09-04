"""Runtime configuration overrides (config/overrides.py) — the mechanism behind
"everything is configurable from the UI" (2026-09-02).

  - every schema path resolves to a real dataclass field (no dead switches in the UI)
  - type coercion + bounds, and Settings.validate() coherence (drawdown ladder)
  - a failing patch must not mutate the live settings (dry run on a deep copy)
  - persistence round-trip, merge, reset; BOTSTRIKE_NO_OVERRIDES keeps tests pure
"""
import json
import os

import pytest

from config import overrides as ov
from config.settings import Settings, SymbolConfig, TradingConfig


def test_every_schema_path_exists_on_the_dataclasses():
    s = Settings()
    sch = ov.schema(s.symbol_names)
    assert [g["id"] for g in sch["groups"]] == [
        "capital", "strategies", "trend_daily", "divergence", "edge", "execution", "notifications", "symbols"]
    for g in sch["groups"]:
        for f in g["fields"]:
            path = f["path"]
            if path.startswith("trading."):
                assert hasattr(TradingConfig, path.split(".", 1)[1]), path
            else:
                assert path.startswith("symbols.{symbol}."), path
                assert hasattr(SymbolConfig, path.rsplit(".", 1)[1]), path
            assert f["type"] in ("number", "int", "percent", "bool", "string", "select", "list")
    sym_group = next(g for g in sch["groups"] if g["id"] == "symbols")
    assert sym_group["per_symbol"] is True and sym_group["symbols"] == s.symbol_names


def test_coerce_types_and_bounds():
    spec = ov._spec_for("trading", "max_drawdown_pct")
    assert ov.coerce(spec, "0.08", "x") == 0.08
    with pytest.raises(ValueError, match="<= 0.5"):
        ov.coerce(spec, 0.9, "trading.max_drawdown_pct")
    with pytest.raises(ValueError, match=">= 0.01"):
        ov.coerce(spec, 0.001, "trading.max_drawdown_pct")
    b = ov._spec_for("trading", "compounding_enabled")
    assert ov.coerce(b, "false", "x") is False and ov.coerce(b, 1, "x") is True
    with pytest.raises(ValueError):
        ov.coerce(b, "maybe", "x")
    i = ov._spec_for("trading", "max_open_positions")
    assert ov.coerce(i, 3.0, "x") == 3
    with pytest.raises(ValueError):
        ov.coerce(i, 2.5, "x")
    lst = ov._spec_for("trading", "trend_lookbacks")
    assert ov.coerce(lst, [5, " 10", "", 20], "x") == "5,10,20"
    sel = ov._spec_for("trading", "exchange_venue")
    with pytest.raises(ValueError, match="one of"):
        ov.coerce(sel, "kraken", "x")
    with pytest.raises(ValueError, match="not an editable field"):
        ov._spec_for("trading", "binance_api_key")


def test_validate_and_apply_is_atomic_and_reports_restart():
    s = Settings()
    before = s.trading.max_drawdown_pct
    # incoherent ladder (daily > max) must be rejected WITHOUT touching `s`
    with pytest.raises(ValueError, match="drawdown ladder"):
        ov.validate_and_apply(s, {"trading": {"max_drawdown_pct": 0.03, "max_daily_loss_pct": 0.05}})
    assert s.trading.max_drawdown_pct == before
    applied, restart = ov.validate_and_apply(
        # the ladder invariant is daily <= weekly <= drawdown; 0.08 is now below the shipped
        # weekly (0.10), so the patch moves the whole ladder rather than one rung of it
        s, {"trading": {"max_drawdown_pct": 0.08, "max_weekly_loss_pct": 0.05,
                        "max_daily_loss_pct": 0.02, "allocation_trend_daily": 0.5},
            "symbols": {"BTC-USD": {"leverage": 1, "strategies": "FIBONACCI_RETRACEMENT"}}})
    assert set(applied) == {"trading.max_drawdown_pct", "trading.max_weekly_loss_pct",
                            "trading.max_daily_loss_pct", "trading.allocation_trend_daily",
                            "symbols.BTC-USD.leverage", "symbols.BTC-USD.strategies"}
    assert restart is False
    assert s.trading.max_drawdown_pct == 0.08 and s.get_symbol_config("BTC-USD").leverage == 1
    _, restart = ov.validate_and_apply(s, {"trading": {"vol_target_annual": 0.25}})
    assert restart is True
    # coherence (exposure vs capital) is enforced on the dry run too
    with pytest.raises(ValueError, match="max_total_exposure"):
        ov.validate_and_apply(s, {"trading": {"initial_capital": 300}})
    with pytest.raises(ValueError, match="unknown symbol"):
        ov.validate_and_apply(s, {"symbols": {"DOGE-USD": {"leverage": 2}}})
    with pytest.raises(ValueError, match="not an editable"):
        ov.validate_and_apply(s, {"trading": {"telegram_bot_token": "x"}})


def test_persistence_roundtrip_and_reset(tmp_path, monkeypatch):
    path = str(tmp_path / "overrides.json")
    monkeypatch.setenv("BOTSTRIKE_CONFIG_OVERRIDES", path)
    assert ov.load_overrides() == {}
    merged = ov.merge_overrides({}, {"trading": {"max_drawdown_pct": 0.08}})
    merged = ov.merge_overrides(merged, {"symbols": {"ETH-USD": {"leverage": 1}}})
    merged = ov.merge_overrides(merged, {"trading": {"compounding_enabled": False}})
    ov.save_overrides(merged)
    assert json.load(open(path)) == {
        "trading": {"max_drawdown_pct": 0.08, "compounding_enabled": False},
        "symbols": {"ETH-USD": {"leverage": 1}}}
    # a fresh Settings() applies them (BOTSTRIKE_NO_OVERRIDES is set by conftest → unset here)
    monkeypatch.delenv("BOTSTRIKE_NO_OVERRIDES", raising=False)
    s = Settings()
    assert s.trading.max_drawdown_pct == 0.08 and s.trading.compounding_enabled is False
    assert s.get_symbol_config("ETH-USD").leverage == 1
    st = ov.overrides_state(s)
    assert st["restart_required"] is False and st["overrides"]["trading"]["max_drawdown_pct"] == 0.08
    ov.clear_overrides()
    assert not os.path.exists(path)
    # a reset returns to the SHIPPED default, which tracks the balanced profile rather than a
    # number frozen in a test (config/risk_profiles.py is the source of truth)
    from config.risk_profiles import PROFILES
    assert Settings().trading.max_drawdown_pct == PROFILES["balanced"]["max_drawdown_pct"]


def test_saved_overrides_are_lenient_with_unknown_fields(tmp_path, monkeypatch):
    path = str(tmp_path / "o.json")
    monkeypatch.setenv("BOTSTRIKE_CONFIG_OVERRIDES", path)
    monkeypatch.delenv("BOTSTRIKE_NO_OVERRIDES", raising=False)
    ov.save_overrides({"trading": {"no_such_field": 1, "max_leverage": 3},
                       "symbols": {"XXX-USD": {"leverage": 9}}})
    s = Settings()          # must not raise
    assert s.trading.max_leverage == 3


def test_no_overrides_env_keeps_code_defaults(tmp_path, monkeypatch):
    path = str(tmp_path / "o.json")
    monkeypatch.setenv("BOTSTRIKE_CONFIG_OVERRIDES", path)
    monkeypatch.setenv("BOTSTRIKE_NO_OVERRIDES", "1")
    ov.save_overrides({"trading": {"max_leverage": 2}})
    assert Settings().trading.max_leverage == 5


def test_settings_validate_rejects_bad_lookbacks_and_allocations():
    s = Settings()
    s.trading.trend_lookbacks = "5,abc"
    with pytest.raises(ValueError, match="lookbacks"):
        s.validate()
    s = Settings()
    s.trading.allocation_mean_reversion = 1.5
    with pytest.raises(ValueError, match="within"):
        s.validate()
