"""Runtime configuration overrides — the mechanism behind "everything is
configurable from the UI" (2026-09-02).

Layer model:   code defaults (settings.py)  <  data/config_overrides.json

- The JSON holds ONLY what the user changed, keyed by section and path:
      {"trading": {"max_drawdown_pct": 0.08}, "symbols": {"BTC-USD": {"leverage": 1}}}
- SCHEMA is the whitelist of editable fields with type, bounds, unit and help text.
  Anything not listed is rejected by PUT /api/config; bounds are validated here and
  coherence (drawdown ladder, exposure vs capital, allocations) by Settings.validate().
- `restart` marks fields that are read once at construction (risk models, the
  microstructure engine, the symbol list). Everything else applies live because the
  engine reads settings.trading / SymbolConfig attributes at use time.
- Settings.__post_init__ calls apply_saved_overrides(), so every Settings() the
  bridge builds already carries the user's choices. Tests set BOTSTRIKE_NO_OVERRIDES=1
  (tests/conftest.py) so a developer's local overrides never leak into assertions.
"""
from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

import structlog

logger = structlog.get_logger(__name__)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OVERRIDES_PATH = os.path.join(PROJECT_ROOT, "data", "config_overrides.json")


def overrides_path() -> str:
    return os.getenv("BOTSTRIKE_CONFIG_OVERRIDES", DEFAULT_OVERRIDES_PATH)


# ── Schema ─────────────────────────────────────────────────────────────────────
@dataclass
class FieldSpec:
    path: str                    # "trading.<field>" or "symbols.{symbol}.<field>"
    label: str
    type: str                    # number | int | percent | bool | string | select | list
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None
    unit: str = ""
    help: str = ""
    restart: bool = False
    options: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["restart_required"] = d.pop("restart")
        if not d["options"]:
            d.pop("options")
        return {k: v for k, v in d.items() if v is not None and v != ""} | {"path": self.path, "type": self.type}


def _t(name: str, label: str, type: str, **kw) -> FieldSpec:
    return FieldSpec(path=f"trading.{name}", label=label, type=type, **kw)


def _s(name: str, label: str, type: str, **kw) -> FieldSpec:
    return FieldSpec(path=f"symbols.{{symbol}}.{name}", label=label, type=type, **kw)


GROUPS: List[Dict[str, Any]] = [
    {"id": "capital", "label": "Capital & Risk", "fields": [
        _t("initial_capital", "Initial capital", "number", min=50, max=10_000_000, step=10, unit="$",
           help="Starting capital of the paper account and the base of every percentage limit.", restart=True),
        _t("compounding_enabled", "Compound gains", "bool",
           help="Size positions on the all-time equity (initial capital + realized PnL) so gains are reinvested. "
                "Off = always size on the fixed initial capital."),
        _t("max_drawdown_pct", "Max drawdown from peak", "percent", min=0.01, max=0.5, step=0.005,
           help="Halt and flatten when equity falls this far below its all-time peak (persisted across restarts)."),
        _t("max_daily_loss_pct", "Max daily loss", "percent", min=0.005, max=0.5, step=0.005,
           help="No new entries for the rest of the UTC day after losing this share of equity."),
        _t("max_weekly_loss_pct", "Max weekly loss", "percent", min=0.01, max=0.5, step=0.005,
           help="No new entries for the rest of the ISO week after losing this share of equity."),
        _t("risk_per_trade_pct", "Risk per trade", "percent", min=0.001, max=0.05, step=0.001,
           help="Distance to the stop × size = this share of equity (intraday strategies)."),
        _t("max_total_exposure_pct", "Max total exposure", "percent", min=0.05, max=1.0, step=0.05,
           help="Cap on the sum of open notionals as a share of equity × max leverage."),
        _t("max_leverage", "Max leverage", "int", min=1, max=20, step=1, unit="x"),
        _t("max_open_positions", "Max open positions", "int", min=1, max=20, step=1),
        _t("close_positions_on_shutdown", "Flatten on shutdown", "bool",
           help="Close every open position before the engine stops (recommended)."),
        _t("vol_target_annual", "Portfolio vol target", "percent", min=0.05, max=0.6, step=0.01, restart=True),
        _t("vol_target_min_scalar", "Vol scalar floor", "number", min=0.1, max=1.0, step=0.05, restart=True),
        _t("vol_target_max_scalar", "Vol scalar cap", "number", min=1.0, max=3.0, step=0.05, restart=True),
        _t("vol_target_lookback_days", "Vol lookback", "int", min=5, max=120, step=1, unit="days", restart=True),
        _t("kelly_min_trades", "Kelly min trades", "int", min=20, max=1000, step=10, restart=True),
        _t("kelly_floor_pct", "Kelly floor", "percent", min=0.001, max=0.05, step=0.001, restart=True),
        _t("kelly_ceiling_pct", "Kelly ceiling", "percent", min=0.005, max=0.1, step=0.001, restart=True),
        _t("ror_throttle_threshold", "Risk-of-ruin throttle", "percent", min=0.005, max=0.5, step=0.005, restart=True),
        _t("ror_pause_threshold", "Risk-of-ruin pause", "percent", min=0.01, max=0.9, step=0.01, restart=True),
        _t("corr_stress_threshold", "Correlation stress", "number", min=0.5, max=0.99, step=0.01, restart=True),
    ]},
    {"id": "strategies", "label": "Strategies", "fields": [
        _t("allocation_trend_daily", "Trend daily allocation", "percent", min=0, max=1, step=0.05,
           help="0 = disabled. 100% = the vol-targeted weights are applied in full."),
        _t("allocation_mean_reversion", "Mean Reversion allocation", "percent", min=0, max=1, step=0.05,
           help="0 = disabled. Frozen at 0 by the 2026-08-31 audit (no gross edge measured). The edge monitor "
                "will kill it again if its statistics stay negative."),
        _t("allocation_fibonacci_retracement", "Fibonacci allocation", "percent", min=0, max=1, step=0.05,
           help="0 = disabled. No published evidence (research §2.7)."),
        _t("allocation_divergence", "Divergence allocation", "percent", min=0, max=1, step=0.05,
           help="0 = disabled. Research 2026-09-02 (1,102 trades, 1h): NO edge - PF 0.77, t-stat -2.15. "
                "Enable only to study it in paper; the edge monitor will kill it if it keeps losing."),
        _t("regime_timeframe_min", "Regime timeframe", "int", min=1, max=240, step=1, unit="min",
           help="Bar size used to classify the market regime (ADX/momentum/vol). 1-minute bars flip "
                "every few minutes; 15 minutes matches the ~30 min holding time of the intraday strategies."),
        _t("regime_min_dwell_min", "Regime confirmation", "int", min=0, max=720, step=5, unit="min",
           help="A new regime must persist this long before it replaces the current one."),
        _t("strategy_interval_sec", "Intraday loop interval", "number", min=1, max=60, step=0.5, unit="s"),
        _t("data_stale_warn_sec", "Stale data warning", "number", min=1, max=600, step=1, unit="s"),
        _t("data_stale_block_sec", "Stale data block", "number", min=2, max=1800, step=1, unit="s"),
    ]},
    {"id": "trend_daily", "label": "Trend daily", "fields": [
        _t("trend_lookbacks", "Donchian lookbacks", "list", help="Comma-separated days, e.g. 5,10,20,30,60,90."),
        _t("trend_target_vol", "Target vol per asset", "percent", min=0.05, max=0.6, step=0.01),
        _t("trend_vol_window", "Realized vol window", "int", min=20, max=365, step=1, unit="days"),
        _t("trend_n_assets", "Assets in universe", "int", min=1, max=10, step=1),
        _t("trend_leverage_cap", "Vol scalar cap", "number", min=0.1, max=3.0, step=0.1, unit="x",
           help="1.0 = never more than 100% of the slot in one asset (no leverage)."),
        _t("trend_rebalance_threshold", "Rebalance threshold", "percent", min=0, max=1, step=0.05,
           help="Vol-induced size changes smaller than this are not traded (saves fees)."),
        _t("trend_execution_hour_utc", "Execution hour (UTC)", "int", min=0, max=23, step=1, unit="h"),
        _t("trend_execution_delay_min", "Execution delay", "int", min=1, max=600, step=1, unit="min"),
        _t("trend_min_order_usd", "Min order", "number", min=1, max=10_000, step=1, unit="$",
           help="Rebalances smaller than this notional are skipped (venue minimums: Binance 5-100 $, Hyperliquid 10 $)."),
        _t("trend_min_listing_days", "Min listing age", "int", min=30, max=2000, step=1, unit="days"),
        _t("trend_liq_enter_usd", "Liquidity to enter", "number", min=0, max=1e10, step=100_000, unit="$/day"),
        _t("trend_liq_exit_usd", "Liquidity to stay", "number", min=0, max=1e10, step=100_000, unit="$/day"),
        _t("trend_pool", "Candidate pool", "list", help="Binance spot symbols, comma-separated."),
    ]},
    {"id": "divergence", "label": "Divergence", "fields": [
        _t("div_timeframe_min", "Bar timeframe", "select", options=[
            {"value": "15", "label": "15 min"}, {"value": "30", "label": "30 min"}, {"value": "60", "label": "1 h"},
            {"value": "120", "label": "2 h"}, {"value": "240", "label": "4 h"}], restart=True),
        _t("div_rsi_period", "RSI period", "int", min=5, max=50, step=1),
        _t("div_pivot_k", "Pivot confirmation bars", "int", min=1, max=10, step=1,
           help="Bars on each side a pivot needs; the divergence exists only after them (no look-ahead)."),
        _t("div_rsi_os", "Bullish: first pivot RSI below", "number", min=10, max=60, step=1),
        _t("div_rsi_ob", "Bearish: first pivot RSI above", "number", min=40, max=90, step=1),
        _t("div_min_gap_bars", "Min bars between pivots", "int", min=2, max=100, step=1),
        _t("div_max_gap_bars", "Max bars between pivots", "int", min=5, max=300, step=1),
        _t("div_min_rsi_gap", "Min RSI gap", "number", min=0, max=30, step=0.5, unit="pts"),
        _t("div_trigger_window", "Trigger window", "int", min=1, max=30, step=1, unit="bars",
           help="Bars after confirmation in which the structure break (close beyond the pivot bar) must happen."),
        _t("div_require_macd", "Require MACD histogram confirmation", "bool"),
        _t("div_require_volume", "Require volume >= 20-bar average", "bool"),
        _t("div_atr_buffer", "Stop buffer (x ATR)", "number", min=0, max=3, step=0.1),
        _t("div_rr", "Take-profit (R multiple)", "number", min=0.5, max=6, step=0.25),
        _t("div_max_hold", "Time stop", "int", min=2, max=500, step=1, unit="bars"),
        _t("div_hidden", "Hidden (continuation) divergences", "bool"),
        _t("div_with_trend", "Only in the EMA200 direction", "bool",
           help="Regular divergences taken as pullbacks in the direction of the 200-bar EMA. Research: 13 trades "
                "in 4.7 years on 4h — not enough evidence either way."),
        _t("div_cooldown_min", "Cooldown after an exit", "int", min=0, max=1440, step=5, unit="min"),
    ]},
    {"id": "edge", "label": "Edge monitor", "fields": [
        _t("edge_monitor_enabled", "Auto-kill on negative edge", "bool"),
        _t("edge_window", "Trades in window", "int", min=30, max=2000, step=10),
        _t("edge_kill_min_trades", "Min trades before a verdict", "int", min=10, max=2000, step=10),
        _t("edge_kill_t_stat", "Kill below t-stat", "number", min=-5, max=0, step=0.1),
        _t("edge_kill_fee_share", "Kill above fee share", "percent", min=0.1, max=1.0, step=0.05,
           help="Fees ÷ gross profit of the winning trades."),
        _t("edge_check_interval_sec", "Check interval", "int", min=60, max=86_400, step=60, unit="s"),
    ]},
    {"id": "execution", "label": "Execution", "fields": [
        _t("exchange_venue", "Venue", "select", restart=True,
           options=[{"value": "binance", "label": "Binance Futures"}, {"value": "hyperliquid", "label": "Hyperliquid"}]),
        _t("maker_fee", "Maker fee", "percent", min=0, max=0.01, step=0.0001),
        _t("taker_fee", "Taker fee", "percent", min=0, max=0.01, step=0.0001),
        _t("slippage_bps", "Slippage model", "number", min=0, max=50, step=0.5, unit="bps"),
        _t("funding_rate_warn", "Funding warn", "number", min=0, max=0.01, step=0.00005, unit="/8h"),
        _t("funding_rate_block", "Funding block", "number", min=0, max=0.01, step=0.00005, unit="/8h"),
        _t("microstructure_enabled", "Microstructure analytics", "bool",
           help="VPIN / Hawkes / Kyle λ / order-book imbalance. Zero measured predictive power (audit R2); "
                "costs ~16% CPU. Off unless a strategy needs it."),
        _t("risk_check_interval_sec", "Risk loop interval", "number", min=0.5, max=60, step=0.5, unit="s"),
    ]},
    {"id": "notifications", "label": "Notifications", "fields": [
        _t("telegram_enabled", "Telegram", "bool"),
        _t("telegram_notify_trades", "Trade fills", "bool"),
        _t("telegram_notify_signals", "Validated signals", "bool"),
        _t("telegram_notify_regime", "Regime changes", "bool"),
        _t("telegram_regime_min_interval_min", "Regime messages: min interval", "int", min=0, max=1440,
           step=5, unit="min", help="Per symbol. 0 = every confirmed change."),
        _t("telegram_notify_portfolio", "Portfolio snapshot", "bool"),
        _t("telegram_portfolio_every_min", "Snapshot every", "int", min=5, max=1440, step=5, unit="min"),
        _t("telegram_notify_daily_digest", "Daily digest", "bool"),
        _t("telegram_digest_hour_utc", "Digest hour (UTC)", "int", min=0, max=23, step=1, unit="h"),
    ]},
    {"id": "symbols", "label": "Symbols", "per_symbol": True, "fields": [
        _s("leverage", "Leverage", "int", min=1, max=20, step=1, unit="x"),
        _s("max_position_usd", "Max position", "number", min=5, max=1_000_000, step=5, unit="$"),
        _s("strategies", "Eligible strategies", "list",
           help="Comma-separated: MEAN_REVERSION, FIBONACCI_RETRACEMENT. Empty = none."),
        _s("mr_zscore_entry", "MR z-score entry", "number", min=0.5, max=5, step=0.1),
        _s("mr_zscore_exit", "MR z-score exit", "number", min=0, max=3, step=0.1),
        _s("mr_lookback", "MR lookback", "int", min=20, max=500, step=5, unit="bars"),
        _s("mr_atr_mult_sl", "MR stop (×ATR)", "number", min=0.5, max=6, step=0.1),
        _s("mr_atr_mult_tp", "MR take-profit (×ATR)", "number", min=0.5, max=10, step=0.1),
        _s("vpin_enabled", "VPIN", "bool"),
        _s("vpin_toxic_threshold", "VPIN toxic threshold", "number", min=0.5, max=1.0, step=0.01),
        _s("hawkes_enabled", "Hawkes", "bool"),
        _s("hawkes_spike_mult", "Hawkes spike ×", "number", min=1, max=10, step=0.5),
        _s("regime_vol_lookback", "Regime vol lookback", "int", min=10, max=500, step=5, unit="bars"),
        _s("regime_momentum_lookback", "Regime momentum lookback", "int", min=5, max=200, step=5, unit="bars"),
        _s("regime_vol_threshold_low", "Regime vol low", "number", min=0.05, max=0.95, step=0.05),
        _s("regime_vol_threshold_high", "Regime vol high", "number", min=0.1, max=1.0, step=0.05),
    ]},
]

_FIELD_BY_PATH: Dict[str, FieldSpec] = {}
for _g in GROUPS:
    for _f in _g["fields"]:
        _FIELD_BY_PATH[_f.path] = _f


def schema(symbols: Optional[List[str]] = None) -> Dict[str, Any]:
    """Editable-field schema for the UI. `symbols` fills the per-symbol group."""
    out = []
    for g in GROUPS:
        item = {"id": g["id"], "label": g["label"], "fields": [f.to_dict() for f in g["fields"]]}
        if g.get("per_symbol"):
            item["per_symbol"] = True
            item["symbols"] = list(symbols or [])
        out.append(item)
    return {"groups": out}


def _spec_for(section: str, name: str) -> FieldSpec:
    key = f"trading.{name}" if section == "trading" else f"symbols.{{symbol}}.{name}"
    spec = _FIELD_BY_PATH.get(key)
    if spec is None:
        raise ValueError(f"{section}.{name}: not an editable field")
    return spec


def coerce(spec: FieldSpec, value: Any, label: str) -> Any:
    """Type-coerce and bound-check one value. Raises ValueError('<label>: ...')."""
    t = spec.type
    try:
        if t == "bool":
            if isinstance(value, bool):
                v: Any = value
            elif isinstance(value, (int, float)) and value in (0, 1):
                v = bool(value)
            elif isinstance(value, str) and value.lower() in ("true", "false", "1", "0", "on", "off"):
                v = value.lower() in ("true", "1", "on")
            else:
                raise ValueError("expected true/false")
        elif t == "int":
            if isinstance(value, bool):
                raise ValueError("expected an integer")
            v = int(round(float(value)))
            if abs(v - float(value)) > 1e-9:
                raise ValueError("expected an integer")
        elif t in ("number", "percent"):
            if isinstance(value, bool):
                raise ValueError("expected a number")
            v = float(value)
            if v != v or v in (float("inf"), float("-inf")):
                raise ValueError("expected a finite number")
        elif t == "select":
            v = str(value)
            allowed = [o["value"] for o in spec.options]
            if v not in allowed:
                raise ValueError(f"must be one of {allowed}")
            if spec.path.endswith("_min"):
                v = int(v)                      # numeric selects are stored as int
        elif t == "list":
            if isinstance(value, (list, tuple)):
                items = [str(x).strip() for x in value]
            else:
                items = [x.strip() for x in str(value).split(",")]
            v = ",".join(x for x in items if x)
        else:  # string
            v = str(value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"{label}: {e}") from None
    if t in ("int", "number", "percent"):
        if spec.min is not None and v < spec.min:
            raise ValueError(f"{label}: must be >= {spec.min}")
        if spec.max is not None and v > spec.max:
            raise ValueError(f"{label}: must be <= {spec.max}")
    return v


def _apply_section(settings, patch: Dict[str, Any], strict: bool) -> List[str]:
    """Apply {"trading": {...}, "symbols": {...}} to `settings` in place. Returns paths."""
    applied: List[str] = []
    trading = patch.get("trading") or {}
    if not isinstance(trading, dict):
        raise ValueError("trading: expected an object")
    for name, value in trading.items():
        try:
            spec = _spec_for("trading", name)
        except ValueError:
            if strict:
                raise
            logger.warning("config_override_unknown_ignored", path=f"trading.{name}")
            continue
        setattr(settings.trading, name, coerce(spec, value, f"trading.{name}"))
        applied.append(f"trading.{name}")
    symbols = patch.get("symbols") or {}
    if not isinstance(symbols, dict):
        raise ValueError("symbols: expected an object")
    by_name = {s.symbol: s for s in settings.symbols}
    for sym, fields in symbols.items():
        cfg = by_name.get(sym)
        if cfg is None:
            if strict:
                raise ValueError(f"symbols.{sym}: unknown symbol")
            logger.warning("config_override_unknown_symbol_ignored", symbol=sym)
            continue
        if not isinstance(fields, dict):
            raise ValueError(f"symbols.{sym}: expected an object")
        for name, value in fields.items():
            try:
                spec = _spec_for("symbols", name)
            except ValueError:
                if strict:
                    raise
                logger.warning("config_override_unknown_ignored", path=f"symbols.{sym}.{name}")
                continue
            setattr(cfg, name, coerce(spec, value, f"symbols.{sym}.{name}"))
            applied.append(f"symbols.{sym}.{name}")
    unknown = set(patch.keys()) - {"trading", "symbols"}
    if unknown and strict:
        raise ValueError(f"unknown section(s): {sorted(unknown)}")
    return applied


def restart_required_for(paths: List[str]) -> bool:
    for p in paths:
        key = p
        if p.startswith("symbols."):
            parts = p.split(".")
            key = f"symbols.{{symbol}}.{parts[-1]}"
        spec = _FIELD_BY_PATH.get(key)
        if spec is not None and spec.restart:
            return True
    return False


def validate_and_apply(settings, patch: Dict[str, Any]) -> Tuple[List[str], bool]:
    """Dry-run the patch on a deep copy (bounds + Settings.validate()), then apply it
    to the live `settings`. Returns (applied_paths, restart_required). Raises ValueError."""
    trial = copy.deepcopy(settings)
    applied = _apply_section(trial, patch, strict=True)
    trial.validate()
    _apply_section(settings, patch, strict=True)
    return applied, restart_required_for(applied)


# ── Persistence ────────────────────────────────────────────────────────────────
def load_overrides(path: Optional[str] = None) -> Dict[str, Any]:
    p = path or overrides_path()
    if not os.path.exists(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning("config_overrides_unreadable", path=p, error=str(e))
        return {}


def save_overrides(data: Dict[str, Any], path: Optional[str] = None) -> None:
    p = path or overrides_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp, p)


def merge_overrides(existing: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(existing) if existing else {}
    if patch.get("trading"):
        out.setdefault("trading", {}).update(patch["trading"])
    for sym, fields in (patch.get("symbols") or {}).items():
        out.setdefault("symbols", {}).setdefault(sym, {}).update(fields)
    return out


def clear_overrides(path: Optional[str] = None) -> None:
    p = path or overrides_path()
    if os.path.exists(p):
        os.remove(p)


def apply_saved_overrides(settings, path: Optional[str] = None) -> List[str]:
    """Called from Settings.__post_init__: apply the persisted user choices (lenient:
    unknown fields are logged and skipped so an old file can never block startup)."""
    if os.getenv("BOTSTRIKE_NO_OVERRIDES", "") == "1":
        return []
    data = load_overrides(path)
    if not data:
        return []
    applied = _apply_section(settings, data, strict=False)
    settings.validate()
    if applied:
        logger.info("config_overrides_applied", count=len(applied))
    return applied


def overrides_state(settings) -> Dict[str, Any]:
    data = load_overrides()
    paths = [f"trading.{k}" for k in (data.get("trading") or {})] + [
        f"symbols.{s}.{k}" for s, f in (data.get("symbols") or {}).items() for k in f]
    return {"overrides": data, "restart_required": restart_required_for(paths)}
