"""
JSON serializers for BotStrike core types.
Converts dataclasses/enums to JSON-safe dicts for WebSocket/REST transport.
"""
from __future__ import annotations
from dataclasses import asdict, fields, is_dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from core.types import (
    OHLCV, Order, OrderBook, OrderBookLevel, Position, Signal, Trade,
    MarketSnapshot, Side, OrderType, TimeInForce, MarketRegime, StrategyType,
)
from config.settings import Settings, TradingConfig, SymbolConfig


def _enum_val(v: Any) -> Any:
    return v.value if isinstance(v, Enum) else v


def serialize_ohlcv(o: OHLCV) -> Dict:
    return {
        "timestamp": o.timestamp,
        "open": o.open,
        "high": o.high,
        "low": o.low,
        "close": o.close,
        "volume": o.volume,
    }


def serialize_orderbook_level(l: OrderBookLevel) -> Dict:
    return {"price": l.price, "quantity": l.quantity}


def serialize_orderbook(ob: OrderBook) -> Dict:
    return {
        "symbol": ob.symbol,
        "timestamp": ob.timestamp,
        "bids": [serialize_orderbook_level(l) for l in ob.bids[:10]],
        "asks": [serialize_orderbook_level(l) for l in ob.asks[:10]],
        "best_bid": ob.best_bid,
        "best_ask": ob.best_ask,
        "mid_price": ob.mid_price,
        "spread": ob.spread,
        "spread_bps": ob.spread_bps,
        "microprice": ob.microprice,
    }


def serialize_signal(s: Signal) -> Dict:
    return {
        "strategy": _enum_val(s.strategy),
        "symbol": s.symbol,
        "side": _enum_val(s.side),
        "strength": s.strength,
        "entry_price": s.entry_price,
        "stop_loss": s.stop_loss,
        "take_profit": s.take_profit,
        "size_usd": s.size_usd,
        "timestamp": s.timestamp,
        "metadata": _serialize_metadata(s.metadata),
    }


def serialize_position(p: Position) -> Dict:
    return {
        "symbol": p.symbol,
        "side": _enum_val(p.side),
        "size": p.size,
        "entry_price": p.entry_price,
        "mark_price": p.mark_price,
        "unrealized_pnl": p.unrealized_pnl,
        "realized_pnl": p.realized_pnl,
        "leverage": p.leverage,
        "liquidation_price": p.liquidation_price,
        "strategy": _enum_val(p.strategy) if p.strategy else None,
        "timestamp": p.timestamp,
        "notional": p.notional,
        "pnl_pct": p.pnl_pct,
    }


def serialize_trade(t: Trade) -> Dict:
    return {
        "symbol": t.symbol,
        "side": _enum_val(t.side),
        "price": t.price,
        "quantity": t.quantity,
        "fee": t.fee,
        "fee_asset": t.fee_asset,
        "order_id": t.order_id,
        "strategy": _enum_val(t.strategy) if t.strategy else None,
        "timestamp": t.timestamp,
        "pnl": t.pnl,
        "expected_price": t.expected_price,
        "actual_slippage_bps": t.actual_slippage_bps,
        "latency_ms": t.latency_ms,
    }


def serialize_market_snapshot(ms: MarketSnapshot) -> Dict:
    return {
        "symbol": ms.symbol,
        "timestamp": ms.timestamp,
        "price": ms.price,
        "mark_price": ms.mark_price,
        "index_price": ms.index_price,
        "funding_rate": ms.funding_rate,
        "volume_24h": ms.volume_24h,
        "open_interest": ms.open_interest,
        "orderbook": serialize_orderbook(ms.orderbook) if ms.orderbook else None,
        "regime": _enum_val(ms.regime),
    }


def serialize_micro_snapshot(micro) -> Optional[Dict]:
    """Serialize MicrostructureSnapshot from core.microstructure."""
    if micro is None:
        return None
    result = {
        "symbol": getattr(micro, "symbol", ""),
        "timestamp": getattr(micro, "timestamp", 0),
        "risk_score": getattr(micro, "risk_score", 0),
    }
    # VPIN
    vpin = getattr(micro, "vpin", None)
    if vpin:
        result["vpin"] = {
            "vpin": getattr(vpin, "vpin", 0),
            "cdf": getattr(vpin, "cdf", 0),
            "is_toxic": getattr(vpin, "is_toxic", False),
        }
    # Hawkes
    hawkes = getattr(micro, "hawkes", None)
    if hawkes:
        result["hawkes"] = {
            "intensity": getattr(hawkes, "intensity", 0),
            "multiplier": getattr(hawkes, "spike_ratio", 0),
            "is_spike": getattr(hawkes, "is_spike", False),
        }
    # A-S spread
    a_s = getattr(micro, "avellaneda_stoikov", None)
    if a_s:
        result["as_spread"] = {
            "bid_spread_bps": getattr(a_s, "spread_bps", 0) / 2,
            "ask_spread_bps": getattr(a_s, "spread_bps", 0) / 2,
            "reservation_price": getattr(a_s, "reservation_price", 0),
        }
    # Kyle Lambda
    kyle = getattr(micro, "kyle_lambda", None)
    if kyle:
        result["kyle_lambda"] = {
            "lambda_bps": getattr(kyle, "kyle_lambda_ema", 0),
            "impact_stress": getattr(kyle, "impact_stress", 0),
            "adverse_selection_bps": getattr(kyle, "adverse_selection_bps", 0),
        }
    return result


def serialize_settings(s: Settings) -> Dict:
    """Serialize Settings for REST API (excludes secrets)."""
    return {
        "use_testnet": s.use_testnet,
        "symbols": [_serialize_symbol_config(sc) for sc in s.symbols],
        "trading": _serialize_trading_config(s.trading),
        "log_level": s.log_level,
        "has_api_key": bool(s.api_public_key),
        "has_telegram": bool(s.telegram_bot_token and s.telegram_chat_id),
    }


def _serialize_symbol_config(sc: SymbolConfig) -> Dict:
    return {
        "symbol": sc.symbol,
        "leverage": sc.leverage,
        "max_position_usd": sc.max_position_usd,
        "mr_zscore_entry": sc.mr_zscore_entry,
        "mr_atr_mult_sl": sc.mr_atr_mult_sl,
        "mr_atr_mult_tp": sc.mr_atr_mult_tp,
        "vpin_bucket_size": sc.vpin_bucket_size,
        "vpin_toxic_threshold": sc.vpin_toxic_threshold,
        "hawkes_spike_mult": sc.hawkes_spike_mult,
        "mm_gamma": sc.mm_gamma,
        "obi_levels": sc.obi_levels,
    }


def _serialize_trading_config(tc: TradingConfig) -> Dict:
    return {
        "initial_capital": tc.initial_capital,
        "max_drawdown_pct": tc.max_drawdown_pct,
        "max_leverage": tc.max_leverage,
        "max_total_exposure_pct": tc.max_total_exposure_pct,
        "risk_per_trade_pct": tc.risk_per_trade_pct,
        "allocation_mean_reversion": tc.allocation_mean_reversion,
        "allocation_trend_following": tc.allocation_trend_following,
        "allocation_market_making": tc.allocation_market_making,
        "allocation_order_flow_momentum": tc.allocation_order_flow_momentum,
        "maker_fee": tc.maker_fee,
        "taker_fee": tc.taker_fee,
        "slippage_bps": tc.slippage_bps,
        "vol_target_annual": tc.vol_target_annual,
        "kelly_min_trades": tc.kelly_min_trades,
        "kelly_floor_pct": tc.kelly_floor_pct,
        "kelly_ceiling_pct": tc.kelly_ceiling_pct,
    }


def _serialize_metadata(meta: dict) -> dict:
    """Serialize metadata dict, converting enums and numpy types."""
    result = {}
    for k, v in meta.items():
        if isinstance(v, Enum):
            result[k] = v.value
        elif hasattr(v, "item"):  # numpy scalar
            result[k] = v.item()
        elif isinstance(v, (dict,)):
            result[k] = _serialize_metadata(v)
        else:
            result[k] = v
    return result
