"""
Round-2 P0/P1 audit fixes — unit tests (no network, mocks only).

Covers (tasks/audit/01 + 02):
  F02 / exits         -> any action startswith("exit") is an EXIT in live AND paper
  F03 / perf factor   -> normalized, >=20 closed trades, never permanent, floor 0.5
  F03+F09 / exits     -> open positions are managed even when entries are gated
  P0-01 / precision   -> stepSize / tickSize / minQty / minNotional (+ fallback)
  P0-02 / retries     -> POST /order is NEVER blindly re-sent; recovered by clientOrderId
  P1-04 / depth WS    -> "b"/"a" keys parsed (with "bids"/"asks" compat)
  P1-05 / SL-TP       -> protectives only after FILLED, sized on executedQty, -2022 retried
  F01 / P0-03         -> close_all_positions() runs BEFORE cancel_all() on shutdown and DD halt
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from decimal import Decimal
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest
from structlog.testing import capture_logs

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import Settings  # noqa: E402
from core.types import (  # noqa: E402
    MarketRegime, MarketSnapshot, Order, OrderType, Position, Side, Signal,
    StrategyType, Trade,
)
from exchange.binance_client import (  # noqa: E402
    BinanceAPIError, BinanceClient, DEFAULT_SYMBOL_FILTERS, floor_to_step,
    format_decimal, parse_symbol_filters, round_to_tick,
)
from exchange.binance_ws import BinanceWebSocket  # noqa: E402
from execution.order_engine import OrderExecutionEngine  # noqa: E402
from execution.paper_simulator import PaperPosition, PaperTradingSimulator  # noqa: E402
from portfolio.portfolio_manager import (  # noqa: E402
    PERF_BLOCK_COOLDOWN_SEC, PERF_FLOOR, PERF_MIN_TRADES, PortfolioManager,
)
from risk.risk_manager import RiskManager  # noqa: E402


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def settings() -> Settings:
    s = Settings()
    s.binance_api_key = ""
    s.binance_api_secret = ""
    s.api_private_key = ""
    return s


def make_signal(action: str = "entry", symbol: str = "ETH-USD", side: Side = Side.BUY,
                strategy: StrategyType = StrategyType.MEAN_REVERSION,
                price: float = 3000.0, size_usd: float = 300.0, **meta) -> Signal:
    md = {"action": action}
    md.update(meta)
    sl = price * 0.99 if side == Side.BUY else price * 1.01
    tp = price * 1.02 if side == Side.BUY else price * 0.98
    return Signal(strategy=strategy, symbol=symbol, side=side, strength=0.8,
                  entry_price=price, stop_loss=sl, take_profit=tp,
                  size_usd=size_usd, metadata=md)


# ──────────────────────────────────────────────────────────────────────
# Fake exchange client for the execution engine (no network)
# ──────────────────────────────────────────────────────────────────────
class FakeClient:
    def __init__(self, entry_status: str = "FILLED", entry_executed: Optional[str] = None):
        self.orders: List[Order] = []
        self.entry_status = entry_status
        self.entry_executed = entry_executed
        self.fail_reduce_only_times = 0
        self.get_order_responses: List[Dict] = []
        self.get_order_calls: List[Dict] = []
        self.positions: List[Dict] = []
        self.calls: List[str] = []

    async def place_order(self, order: Order) -> Dict:
        self.orders.append(order)
        if order.reduce_only and self.fail_reduce_only_times > 0:
            self.fail_reduce_only_times -= 1
            raise BinanceAPIError(
                400, '{"code":-2022,"msg":"ReduceOnly Order is rejected."}', "/fapi/v1/order")
        oid = str(len(self.orders))
        if order.reduce_only or order.order_type in (OrderType.STOP, OrderType.TAKE_PROFIT):
            return {"orderId": oid, "status": "NEW", "executedQty": "0"}
        executed = self.entry_executed
        if executed is None:
            executed = str(order.quantity) if self.entry_status == "FILLED" else "0"
        return {"orderId": oid, "status": self.entry_status, "executedQty": executed}

    async def get_order(self, symbol: str, client_order_id: Optional[str] = None,
                        order_id: Optional[str] = None) -> Optional[Dict]:
        self.get_order_calls.append({"symbol": symbol, "cid": client_order_id})
        if self.get_order_responses:
            return self.get_order_responses.pop(0)
        return None

    async def cancel_all_orders(self, symbol: Optional[str] = None) -> Dict:
        self.calls.append("cancel_all")
        return {}

    async def get_positions(self, symbol: Optional[str] = None) -> List[Dict]:
        self.calls.append("get_positions")
        return list(self.positions)


# ══════════════════════════════════════════════════════════════════════
# F02 — startswith("exit") in the live engine (parity with paper)
# ══════════════════════════════════════════════════════════════════════
def test_is_exit_signal_startswith_exit():
    for action in ("exit_fibonacci", "exit_mean_reversion", "exit_ofm", "exit_stale",
                   "trailing_stop_hit", "mm_unwind"):
        assert OrderExecutionEngine.is_exit_signal(make_signal(action)), action
    assert OrderExecutionEngine.is_exit_signal(make_signal("close", exit_reason="SL"))
    for action in ("entry", "entry_pullback", "fib_entry", ""):
        assert not OrderExecutionEngine.is_exit_signal(make_signal(action)), action
    assert not OrderExecutionEngine.is_exit_signal(
        Signal(StrategyType.MEAN_REVERSION, "ETH-USD", Side.BUY, 0.5, 1, 1, 1, 1))


def test_exit_fibonacci_routed_as_market_reduce_only(settings):
    client = FakeClient()
    engine = OrderExecutionEngine(settings, client, RiskManager(settings))
    sig = make_signal("exit_fibonacci", symbol="BTC-USD", side=Side.SELL,
                      strategy=StrategyType.FIBONACCI_RETRACEMENT, price=60000.0,
                      exit_reason="trailing_stop")
    order = run(engine.execute_signal(sig, settings.get_symbol_config("BTC-USD")))
    assert order is not None
    assert len(client.orders) == 1, "exit must not place SL/TP protectives"
    sent = client.orders[0]
    assert sent.order_type == OrderType.MARKET
    assert sent.reduce_only is True


def test_paper_and_live_share_exit_criterion(settings):
    """Paper simulator closes on exit_fibonacci exactly like the live engine does."""
    sim = PaperTradingSimulator(settings)
    pos = PaperPosition("BTC-USD", Side.BUY, 0.01, 60000.0,
                        StrategyType.FIBONACCI_RETRACEMENT, 59000.0, 62000.0)
    sim._positions["BTC-USD_FIBONACCI_RETRACEMENT"] = pos
    exit_sig = make_signal("exit_fibonacci", symbol="BTC-USD", side=Side.SELL,
                           strategy=StrategyType.FIBONACCI_RETRACEMENT, price=61000.0)
    fills = sim.execute_signals([exit_sig], [], settings.get_symbol_config("BTC-USD"))
    assert len(fills) == 1 and fills[0].pnl != 0
    assert sim.position_count == 0
    assert OrderExecutionEngine.is_exit_signal(exit_sig)


# ══════════════════════════════════════════════════════════════════════
# F03 — performance factor normalized and never permanent
# ══════════════════════════════════════════════════════════════════════
@pytest.fixture
def funded(monkeypatch):
    """Every strategy is frozen at 0.00 since audit R2 batch 1 (strategies-01), so
    `should_strategy_trade` is False for everything. These tests exercise the
    PERFORMANCE gate, not the freeze — give them a funded world to gate on."""
    import portfolio.portfolio_manager as pmod
    weights = {r: dict(w) for r, w in pmod.REGIME_WEIGHTS.items()}
    weights[MarketRegime.RANGING][StrategyType.MEAN_REVERSION] = 0.65
    monkeypatch.setattr(pmod, "REGIME_WEIGHTS", weights)
    monkeypatch.setattr(pmod, "SYMBOL_STRATEGY_MAP",
                        {"ETH-USD": {StrategyType.MEAN_REVERSION}})


def test_performance_factor_requires_min_closed_trades(settings, funded):
    pm = PortfolioManager(settings, RiskManager(settings))
    mr = StrategyType.MEAN_REVERSION
    # Entries (pnl=0) never count — 100 of them leave the factor neutral.
    for _ in range(100):
        pm.update_strategy_pnl(mr, 0.0)
    assert pm._performance_factor(mr) == 1.0
    # 19 losing closes: still neutral (below PERF_MIN_TRADES).
    for _ in range(PERF_MIN_TRADES - 1):
        pm.update_strategy_pnl(mr, -0.05)   # the old formula killed the strategy at -$0.03
    assert pm._performance_factor(mr) == 1.0
    # 20 small losses (-$0.05 vs $15 risk budget) -> tiny reduction, NOT a block.
    pm.update_strategy_pnl(mr, -0.05)
    f = pm._performance_factor(mr)
    assert 0.99 < f < 1.0
    assert pm.should_strategy_trade(mr, MarketRegime.RANGING, symbol="ETH-USD")


def test_performance_factor_floor_and_warning(settings):
    pm = PortfolioManager(settings, RiskManager(settings))
    mr = StrategyType.MEAN_REVERSION
    with capture_logs() as logs:
        for _ in range(PERF_MIN_TRADES):
            pm.update_strategy_pnl(mr, -500.0)     # catastrophic losses
        f = pm._performance_factor(mr)
    assert f == PERF_FLOOR
    assert any(l.get("event") == "strategy_allocation_reduced_by_performance"
               and l.get("log_level") == "warning" for l in logs)


def test_performance_block_is_not_permanent(settings, funded):
    pm = PortfolioManager(settings, RiskManager(settings))
    mr = StrategyType.MEAN_REVERSION
    t0 = 1_000_000.0
    pm._now = lambda: t0
    for _ in range(PERF_MIN_TRADES):
        pm.update_strategy_pnl(mr, -30.0)      # -2R per trade -> factor ~0.5
    with capture_logs() as logs:
        assert not pm.should_strategy_trade(mr, MarketRegime.RANGING, symbol="ETH-USD")
    assert any(l.get("event") == "strategy_disabled_by_performance" for l in logs)
    # Still blocked shortly after (no new trades can arrive while blocked)
    pm._now = lambda: t0 + 60
    assert not pm.should_strategy_trade(mr, MarketRegime.RANGING, symbol="ETH-USD")
    # After the cooldown the strategy is re-enabled on probation with a fresh window
    pm._now = lambda: t0 + PERF_BLOCK_COOLDOWN_SEC + 1
    with capture_logs() as logs:
        assert pm.should_strategy_trade(mr, MarketRegime.RANGING, symbol="ETH-USD")
    assert any(l.get("event") == "strategy_reenabled_after_cooldown" for l in logs)
    assert pm._performance_factor(mr) == 1.0
    assert mr not in pm._perf_blocked_since


# ══════════════════════════════════════════════════════════════════════
# F03 + F09 — exits are processed even when entries are gated
# ══════════════════════════════════════════════════════════════════════
class FakeStrategy:
    def __init__(self, strategy_type: StrategyType, activate: bool):
        self.strategy_type = strategy_type
        self._activate = activate
        self.calls: List[Optional[Position]] = []

    def should_activate(self, regime):
        return self._activate

    def generate_signals(self, symbol, df, snapshot, regime, sym_config,
                         allocated_capital, current_position, **kwargs):
        self.calls.append(current_position)
        if current_position is not None:
            return [make_signal("exit_mean_reversion", symbol=symbol, side=Side.SELL,
                                strategy=self.strategy_type, exit_reason="software_sl")]
        return [make_signal("entry_pullback", symbol=symbol, strategy=self.strategy_type)]


def _make_bot(settings: Settings, strategy: FakeStrategy, position: Optional[Position],
              perf_allows: bool = True):
    from main import BotStrike
    bot = BotStrike.__new__(BotStrike)
    bot.settings = settings
    bot.paper = False
    bot.dry_run = False
    bot.paper_sim = None
    bot._positions = {} if position is None else {position.symbol: position}
    bot._last_regime = {}
    bot.strategies = [strategy]
    bot.risk_manager = RiskManager(settings)
    bot.portfolio_manager = MagicMock()
    bot.portfolio_manager.should_strategy_trade.return_value = perf_allows
    bot.portfolio_manager.get_allocation.return_value = 100.0
    bot.portfolio_manager.on_price_update = MagicMock()
    bot.market_data = MagicMock()
    bot.market_data.get_data_age.return_value = 0.0
    bot.market_data.get_dataframe.return_value = pd.DataFrame({"close": [3000.0] * 5})
    snap = MarketSnapshot(symbol="ETH-USD", timestamp=0.0, price=3000.0, mark_price=3000.0,
                          index_price=3000.0, funding_rate=0.0, volume_24h=0.0,
                          open_interest=0.0)
    bot.market_data.get_snapshot.return_value = snap
    bot.market_data.get_funding_rate.return_value = 0.0
    bot.regime_detector = MagicMock()
    bot.regime_detector.detect.return_value = MarketRegime.BREAKOUT
    bot.trading_logger = MagicMock()
    bot.notifier = MagicMock()
    bot.notifier.notify_regime_change = AsyncMock()
    bot.notifier.notify_signal = AsyncMock()
    bot.microstructure = MagicMock()
    bot.microstructure.get_snapshot.return_value = None
    bot.obi = {}
    bot.microprice = {}
    bot.execution_engine = MagicMock()
    bot.execution_engine.trade_intensity = {}
    bot.execution_engine.spread_predictor = {}
    bot.execution_engine.execute_signal = AsyncMock(return_value=Order(
        "ETH-USD", Side.SELL, OrderType.MARKET, 0.1))
    return bot


def test_exit_executed_when_regime_gate_blocks_entries(settings):
    strat = FakeStrategy(StrategyType.MEAN_REVERSION, activate=False)  # e.g. BREAKOUT
    pos = Position("ETH-USD", Side.BUY, 0.1, 3000.0, mark_price=2990.0)
    bot = _make_bot(settings, strat, pos)
    run(bot._process_symbol("ETH-USD", settings.get_symbol_config("ETH-USD")))
    assert strat.calls == [pos], "generate_signals must run for the open position"
    bot.portfolio_manager.get_allocation.assert_not_called()
    assert bot.execution_engine.execute_signal.await_count == 1
    executed = bot.execution_engine.execute_signal.await_args.args[0]
    assert OrderExecutionEngine.is_exit_signal(executed)


def test_exit_executed_when_performance_gate_blocks_entries(settings):
    strat = FakeStrategy(StrategyType.MEAN_REVERSION, activate=True)
    pos = Position("ETH-USD", Side.BUY, 0.1, 3000.0, mark_price=2990.0)
    bot = _make_bot(settings, strat, pos, perf_allows=False)
    run(bot._process_symbol("ETH-USD", settings.get_symbol_config("ETH-USD")))
    assert strat.calls == [pos]
    assert bot.execution_engine.execute_signal.await_count == 1


def test_no_entry_when_gates_block_and_no_position(settings):
    strat = FakeStrategy(StrategyType.MEAN_REVERSION, activate=False)
    bot = _make_bot(settings, strat, None)
    run(bot._process_symbol("ETH-USD", settings.get_symbol_config("ETH-USD")))
    assert strat.calls == [], "no position + entries blocked -> strategy skipped"
    bot.execution_engine.execute_signal.assert_not_called()


def test_entry_signal_generated_when_gates_allow(settings):
    strat = FakeStrategy(StrategyType.MEAN_REVERSION, activate=True)
    bot = _make_bot(settings, strat, None)
    run(bot._process_symbol("ETH-USD", settings.get_symbol_config("ETH-USD")))
    assert strat.calls == [None]
    bot.portfolio_manager.get_allocation.assert_called_once()
    logged = [c.args[0] for c in bot.trading_logger.log_signal.call_args_list]
    assert len(logged) == 1 and not OrderExecutionEngine.is_exit_signal(logged[0])


# ══════════════════════════════════════════════════════════════════════
# P0-01 — stepSize / tickSize / minQty / minNotional
# ══════════════════════════════════════════════════════════════════════
EXCHANGE_INFO = {"symbols": [
    {"symbol": "BTCUSDT", "filters": [
        {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
        {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
        {"filterType": "MIN_NOTIONAL", "notional": "100"}]},
    {"symbol": "ETHUSDT", "filters": [
        {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
        {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
        {"filterType": "MIN_NOTIONAL", "notional": "20"}]},
    {"symbol": "SOLUSDT", "filters": [
        {"filterType": "PRICE_FILTER", "tickSize": "0.0100"},
        {"filterType": "LOT_SIZE", "stepSize": "0.01", "minQty": "0.01"},
        {"filterType": "MIN_NOTIONAL", "notional": "5"}]},
    {"symbol": "ADAUSDT", "filters": [
        {"filterType": "PRICE_FILTER", "tickSize": "0.00010"},
        {"filterType": "LOT_SIZE", "stepSize": "1", "minQty": "1"},
        {"filterType": "MIN_NOTIONAL", "notional": "5"}]},
    {"symbol": "XRPUSDT", "filters": []},
]}


def _loaded_client(settings: Settings) -> BinanceClient:
    c = BinanceClient(settings)
    c.get_exchange_info = AsyncMock(return_value=EXCHANGE_INFO)
    assert run(c.load_exchange_info()) is True
    return c


def test_rounding_helpers():
    assert floor_to_step(0.007692, Decimal("0.001")) == Decimal("0.007")
    assert floor_to_step(1.666667, Decimal("0.01")) == Decimal("1.66")
    assert floor_to_step(428.571429, Decimal("1")) == Decimal("428")
    assert floor_to_step(0.125, Decimal("0.001")) == Decimal("0.125")
    assert round_to_tick(65432.15, Decimal("0.1"), "floor") == Decimal("65432.1")
    assert round_to_tick(65432.15, Decimal("0.1"), "ceil") == Decimal("65432.2")
    assert round_to_tick(0.34871, Decimal("0.0001"), "floor") == Decimal("0.3487")
    assert format_decimal(Decimal("65432.10")) == "65432.1"
    assert format_decimal(Decimal("1E-7")) == "0.0000001"   # no scientific notation
    assert format_decimal(Decimal("428")) == "428"


def test_parse_exchange_info_filters():
    f = parse_symbol_filters(EXCHANGE_INFO)
    assert f["ADAUSDT"]["stepSize"] == Decimal("1")
    assert f["BTCUSDT"]["tickSize"] == Decimal("0.10")
    assert f["SOLUSDT"]["minNotional"] == Decimal("5")
    assert "XRPUSDT" not in f


def test_order_params_respect_filters(settings):
    c = _loaded_client(settings)
    # BTC MARKET entry: qty floored to 0.001
    p = c._normalize_order_params(Order("BTC-USD", Side.BUY, OrderType.MARKET, 0.007692), "BTCUSDT")
    assert p["quantity"] == "0.007"
    # BTC LIMIT BUY: price floored to tick 0.1
    p = c._normalize_order_params(
        Order("BTC-USD", Side.BUY, OrderType.LIMIT, 0.01, price=65432.15), "BTCUSDT")
    assert p["price"] == "65432.1"
    # BTC SL (SELL STOP_MARKET) rounded DOWN to tick, BUY stop rounded UP
    p = c._normalize_order_params(
        Order("BTC-USD", Side.SELL, OrderType.STOP, 0.01, stop_price=65000.05, reduce_only=True), "BTCUSDT")
    assert p["stopPrice"] == "65000"
    p = c._normalize_order_params(
        Order("BTC-USD", Side.BUY, OrderType.STOP, 0.01, stop_price=65000.05, reduce_only=True), "BTCUSDT")
    assert p["stopPrice"] == "65000.1"
    # SOL qty step 0.01
    p = c._normalize_order_params(Order("SOL-USD", Side.BUY, OrderType.MARKET, 1.666667), "SOLUSDT")
    assert p["quantity"] == "1.66"
    # ADA: integer qty + 0.0001 tick on the stop (was "0.35" = 37 bps off)
    p = c._normalize_order_params(
        Order("ADA-USD", Side.SELL, OrderType.STOP, 428.571429, stop_price=0.34871, reduce_only=True), "ADAUSDT")
    assert p["quantity"] == "428" and p["stopPrice"] == "0.3487"
    # ETH unchanged when already valid
    p = c._normalize_order_params(
        Order("ETH-USD", Side.BUY, OrderType.LIMIT, 0.125, price=3210.12), "ETHUSDT")
    assert p["quantity"] == "0.125" and p["price"] == "3210.12"


def test_min_qty_and_min_notional_rejected_locally(settings):
    c = _loaded_client(settings)
    with pytest.raises(ValueError):
        c._normalize_order_params(Order("BTC-USD", Side.BUY, OrderType.MARKET, 0.0005), "BTCUSDT")
    with pytest.raises(ValueError):
        c._normalize_order_params(
            Order("ETH-USD", Side.BUY, OrderType.LIMIT, 0.001, price=3000.0), "ETHUSDT")  # $3 < $20
    # reduceOnly closes are exempt from MIN_NOTIONAL
    p = c._normalize_order_params(
        Order("ETH-USD", Side.SELL, OrderType.LIMIT, 0.001, price=3000.0, reduce_only=True), "ETHUSDT")
    assert p["quantity"] == "0.001"
    # MARKET entry uses the engine's expected price for the notional check
    o = Order("ETH-USD", Side.BUY, OrderType.MARKET, 0.001)
    o._expected_price = 3000.0
    with pytest.raises(ValueError):
        c._normalize_order_params(o, "ETHUSDT")


def test_filters_fallback_when_exchange_info_fails(settings):
    c = BinanceClient(settings)
    c.get_exchange_info = AsyncMock(side_effect=BinanceAPIError(503, "down", "/fapi/v1/exchangeInfo"))
    assert run(c.load_exchange_info()) is False
    assert c._filters_loaded is False
    # Known defaults still enforce the real step sizes
    assert c.get_symbol_filters("ADAUSDT")["stepSize"] == DEFAULT_SYMBOL_FILTERS["ADAUSDT"]["stepSize"]
    p = c._normalize_order_params(Order("ADA-USD", Side.SELL, OrderType.MARKET, 428.571429, reduce_only=True), "ADAUSDT")
    assert p["quantity"] == "428"
    # Unknown symbol -> generic conservative filter, never a crash
    assert c.get_symbol_filters("XYZUSDT")["stepSize"] > 0
    # Retry is throttled (does not hammer the endpoint every order)
    assert run(c.load_exchange_info()) is False
    assert c.get_exchange_info.await_count == 1


# ──────────────────────────────────────────────────────────────────────
# Fake aiohttp session for BinanceClient REST paths
# ──────────────────────────────────────────────────────────────────────
class _FakeResp:
    def __init__(self, status: int, payload: Any):
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        return self._payload

    async def text(self):
        return json.dumps(self._payload)


class _FakeSession:
    """post()/get() pop scripted responses; an Exception instance is raised."""
    closed = False

    def __init__(self, post: List[Any], get: List[Any]):
        self._post = list(post)
        self._get = list(get)
        self.post_calls: List[Dict] = []
        self.get_calls: List[Dict] = []

    def post(self, url, data=None, headers=None):
        self.post_calls.append({"url": url, "data": dict(data or {})})
        item = self._post.pop(0)
        if isinstance(item, Exception):
            raise item
        return _FakeResp(*item)

    def get(self, url, params=None, headers=None):
        self.get_calls.append({"url": url, "params": dict(params or {})})
        item = self._get.pop(0)
        if isinstance(item, Exception):
            raise item
        return _FakeResp(*item)


def _client_with_session(settings: Settings, post: List[Any], get: List[Any]):
    c = BinanceClient(settings)
    c._filters_loaded = True                      # skip exchangeInfo
    c._symbol_filters = parse_symbol_filters(EXCHANGE_INFO)
    c._RETRY_BASE_SEC = 0.0                       # no sleeping in tests
    session = _FakeSession(post, get)
    c._get_session = AsyncMock(return_value=session)
    return c, session


def test_place_order_sends_rounded_params_and_result_resp_type(settings):
    c, session = _client_with_session(
        settings, post=[(200, {"orderId": 7, "status": "FILLED", "executedQty": "0.007",
                                "avgPrice": "65000", "clientOrderId": "x"})], get=[])
    o = Order("BTC-USD", Side.BUY, OrderType.MARKET, 0.007692)
    o._expected_price = 65000.0
    res = run(c.place_order(o))
    sent = session.post_calls[0]["data"]
    assert sent["quantity"] == "0.007"
    assert sent["newOrderRespType"] == "RESULT"
    assert sent["newClientOrderId"] == o.client_order_id and o.client_order_id
    assert res["orderId"] == "7" and res["status"] == "FILLED"
    # every order gets its own unique clientOrderId
    o2 = Order("BTC-USD", Side.BUY, OrderType.MARKET, 0.007692)
    assert BinanceClient.new_client_order_id() != BinanceClient.new_client_order_id()
    assert o2.client_order_id is None


# ══════════════════════════════════════════════════════════════════════
# P0-02 — no blind re-send of POST /fapi/v1/order
# ══════════════════════════════════════════════════════════════════════
def test_retry_request_non_idempotent_raises_without_recover(settings):
    c = BinanceClient(settings)
    c._RETRY_BASE_SEC = 0.0
    calls = {"n": 0}

    async def fn():
        calls["n"] += 1
        raise asyncio.TimeoutError()

    with pytest.raises(asyncio.TimeoutError):
        run(c._retry_request(fn, "/fapi/v1/order", idempotent=False))
    assert calls["n"] == 1, "non-idempotent request must be sent exactly once"

    # idempotent GET still retries
    calls["n"] = 0

    async def fn2():
        calls["n"] += 1
        if calls["n"] == 1:
            raise asyncio.TimeoutError()
        return "ok"

    assert run(c._retry_request(fn2, "/fapi/v1/openOrders", idempotent=True)) == "ok"
    assert calls["n"] == 2


def test_place_order_timeout_recovers_by_client_order_id(settings):
    """POST times out -> GET /fapi/v1/order?origClientOrderId=... finds it -> NO re-send."""
    c, session = _client_with_session(
        settings,
        post=[asyncio.TimeoutError(), (200, {"orderId": 99, "status": "FILLED"})],
        get=[(200, {"orderId": 42, "status": "FILLED", "executedQty": "0.007",
                    "avgPrice": "65000.0"})],
    )
    o = Order("BTC-USD", Side.BUY, OrderType.MARKET, 0.007, client_order_id="bs_test_cid")
    res = run(c.place_order(o))
    assert len(session.post_calls) == 1, "POST /order must NOT be re-sent after a timeout"
    assert session.get_calls[0]["url"].endswith("/fapi/v1/order")
    assert session.get_calls[0]["params"]["origClientOrderId"] == "bs_test_cid"
    assert res["orderId"] == "42" and res["status"] == "FILLED"


def test_place_order_5xx_resends_only_if_order_does_not_exist(settings):
    """POST 503 -> GET says -2013 (does not exist) -> ONE re-send with the same cid."""
    c, session = _client_with_session(
        settings,
        post=[(503, {"code": -1001, "msg": "Internal error"}),
              (200, {"orderId": 100, "status": "NEW"})],
        get=[(400, {"code": -2013, "msg": "Order does not exist."})],
    )
    o = Order("BTC-USD", Side.BUY, OrderType.MARKET, 0.007, client_order_id="bs_cid_2")
    res = run(c.place_order(o))
    assert len(session.post_calls) == 2
    assert session.post_calls[0]["data"]["newClientOrderId"] == "bs_cid_2"
    assert session.post_calls[1]["data"]["newClientOrderId"] == "bs_cid_2"
    assert res["orderId"] == "100"


def test_place_order_5xx_with_unknown_state_raises(settings):
    """POST 503 and the status lookup also fails -> raise, never re-send."""
    c, session = _client_with_session(
        settings,
        post=[(503, {"code": -1001, "msg": "Internal error"}), (200, {"orderId": 1})],
        get=[(503, {"code": -1001, "msg": "Internal error"})] * 4,
    )
    o = Order("BTC-USD", Side.BUY, OrderType.MARKET, 0.007, client_order_id="bs_cid_3")
    with pytest.raises(BinanceAPIError):
        run(c.place_order(o))
    assert len(session.post_calls) == 1


def test_batch_orders_not_resent_on_timeout(settings):
    c, session = _client_with_session(settings, post=[asyncio.TimeoutError()], get=[])
    orders = [Order("ETH-USD", Side.BUY, OrderType.LIMIT, 0.1, price=3000.0, post_only=True)]
    res = run(c.batch_orders(orders))
    assert res == {"orders": []}
    assert len(session.post_calls) == 1


# ══════════════════════════════════════════════════════════════════════
# P1-04 — depth stream uses "b"/"a"
# ══════════════════════════════════════════════════════════════════════
def test_ws_depth_parser_reads_b_and_a():
    ws = BinanceWebSocket(symbols=["BTC-USD"])
    received: List[Dict] = []
    ws.on("depth", lambda d: received.append(d))
    payload = {"e": "depthUpdate", "E": 1725000000123, "T": 1725000000100, "s": "BTCUSDT",
               "b": [["65000.1", "1.5"], ["65000.0", "2.0"]],
               "a": [["65000.2", "0.7"], ["65000.3", "3.1"]]}
    run(ws._process_message("btcusdt@depth20@100ms", payload))
    assert len(received) == 1
    d = received[0]
    assert d["s"] == "BTC-USD"
    assert d["b"] == payload["b"] and d["a"] == payload["a"]
    assert d["E"] == 1725000000123 and d["T"] == 1725000000100
    # REST-shaped payload (bids/asks) still works
    received.clear()
    run(ws._process_message("btcusdt@depth20@100ms",
                            {"bids": [["1", "1"]], "asks": [["2", "2"]]}))
    assert received[0]["b"] == [["1", "1"]] and received[0]["a"] == [["2", "2"]]


# ══════════════════════════════════════════════════════════════════════
# P1-05 — protectives only after FILLED, sized on executedQty, -2022 retried
# ══════════════════════════════════════════════════════════════════════
def _engine(settings: Settings, client: FakeClient) -> OrderExecutionEngine:
    e = OrderExecutionEngine(settings, client, RiskManager(settings))
    e._FILL_POLL_DELAY_SEC = 0.0
    e._PROTECTIVE_BACKOFF_SEC = (0.0, 0.0, 0.0)
    return e


def _protectives(client: FakeClient) -> List[Order]:
    return [o for o in client.orders if o.order_type in (OrderType.STOP, OrderType.TAKE_PROFIT)]


def test_protectives_placed_immediately_when_ack_is_filled(settings):
    client = FakeClient(entry_status="FILLED", entry_executed="0.09")
    engine = _engine(settings, client)
    run(engine.execute_signal(make_signal("entry"), settings.get_symbol_config("ETH-USD")))
    prot = _protectives(client)
    assert len(prot) == 2 and all(o.reduce_only for o in prot)
    assert all(o.quantity == 0.09 for o in prot), "protectives sized on executedQty"
    assert client.get_order_calls == []


def test_protectives_wait_for_fill_when_ack_is_new(settings):
    client = FakeClient(entry_status="NEW", entry_executed="0")
    client.get_order_responses = [
        {"status": "NEW", "executedQty": "0"},
        {"status": "PARTIALLY_FILLED", "executedQty": "0.05"},
        {"status": "FILLED", "executedQty": "0.1"},
    ]
    engine = _engine(settings, client)
    run(engine.execute_signal(make_signal("entry"), settings.get_symbol_config("ETH-USD")))
    assert len(client.get_order_calls) == 3
    assert client.get_order_calls[0]["cid"] == client.orders[0].client_order_id
    prot = _protectives(client)
    assert len(prot) == 2 and all(o.quantity == 0.1 for o in prot)
    # protectives were placed AFTER the last status poll (order of operations)
    assert client.orders.index(prot[0]) == 1 and len(client.orders) == 3


def test_no_protectives_when_entry_expired_unfilled(settings):
    client = FakeClient(entry_status="EXPIRED", entry_executed="0")
    engine = _engine(settings, client)
    order = run(engine.execute_signal(make_signal("entry"), settings.get_symbol_config("ETH-USD")))
    assert order is not None
    assert _protectives(client) == []
    assert len(client.orders) == 1
    assert engine.active_order_count == 0


def test_reduce_only_rejected_is_retried_before_emergency(settings):
    client = FakeClient(entry_status="FILLED", entry_executed="0.1")
    client.fail_reduce_only_times = 2   # SL fails twice (-2022) then succeeds
    engine = _engine(settings, client)
    run(engine.execute_signal(make_signal("entry"), settings.get_symbol_config("ETH-USD")))
    emergencies = [o for o in client.orders if o.order_type == OrderType.MARKET and o.reduce_only]
    assert emergencies == [], "no emergency close when retries succeed"
    stops = [o for o in client.orders if o.order_type == OrderType.STOP]
    assert len(stops) == 3 and stops[-1].order_id  # 2 rejected attempts + 1 accepted
    assert len([o for o in client.orders if o.order_type == OrderType.TAKE_PROFIT]) == 1


def test_emergency_close_after_all_protective_retries_fail(settings):
    client = FakeClient(entry_status="FILLED", entry_executed="0.1")
    client.fail_reduce_only_times = 6   # SL x3 + TP x3 all -2022
    engine = _engine(settings, client)
    run(engine.execute_signal(make_signal("entry"), settings.get_symbol_config("ETH-USD")))
    assert len([o for o in client.orders if o.order_type == OrderType.STOP]) == 3
    assert len([o for o in client.orders if o.order_type == OrderType.TAKE_PROFIT]) == 3
    emergencies = [o for o in client.orders if o.order_type == OrderType.MARKET and o.reduce_only]
    assert len(emergencies) == 1 and emergencies[0].client_order_id.startswith("bs_emg")
    assert emergencies[0].quantity == 0.1


# ══════════════════════════════════════════════════════════════════════
# F01 / P0-03 — close_all_positions() BEFORE cancel_all()
# ══════════════════════════════════════════════════════════════════════
def test_engine_close_all_positions_fallback_uses_get_positions(settings):
    client = FakeClient()
    client.positions = [{"symbol": "ETH-USD", "positionAmt": "0.5"},
                        {"symbol": "BTC-USD", "positionAmt": "-0.01"},
                        {"symbol": "ADA-USD", "positionAmt": "0"}]
    engine = _engine(settings, client)

    async def scenario():
        task = asyncio.create_task(engine.close_all_positions())
        await asyncio.sleep(0.05)
        client.positions = []            # exchange reports flat after the closes
        return await task

    res = run(scenario())
    closes = [o for o in client.orders if o.reduce_only and o.order_type == OrderType.MARKET]
    assert {(o.symbol, o.side, o.quantity) for o in closes} == {
        ("ETH-USD", Side.SELL, 0.5), ("BTC-USD", Side.BUY, 0.01)}
    assert res["remaining"] == [] and len(res["closed"]) == 2


def test_binance_close_all_positions_rounds_and_reduce_only(settings):
    reads = [
        [{"symbol": "ADA-USD", "positionAmt": "428.0"}, {"symbol": "BTC-USD", "positionAmt": "-0.0075"},
         {"symbol": "ETH-USD", "positionAmt": "0"}],
        [],
    ]
    c, session = _client_with_session(
        settings,
        post=[(200, {"orderId": 1, "status": "FILLED"}), (200, {"orderId": 2, "status": "FILLED"})],
        get=[],
    )
    c.get_positions = AsyncMock(side_effect=lambda *a, **k: reads.pop(0))
    res = run(c.close_all_positions())
    sent = [p["data"] for p in session.post_calls]
    assert len(sent) == 2
    ada = next(d for d in sent if d["symbol"] == "ADAUSDT")
    btc = next(d for d in sent if d["symbol"] == "BTCUSDT")
    assert ada["side"] == "SELL" and ada["quantity"] == "428" and ada["reduceOnly"] == "true"
    assert btc["side"] == "BUY" and btc["quantity"] == "0.007" and btc["reduceOnly"] == "true"
    assert all(d["type"] == "MARKET" and d["newOrderRespType"] == "RESULT" for d in sent)
    assert res["remaining"] == [] and len(res["closed"]) == 2


def test_paper_close_all_positions(settings):
    sim = PaperTradingSimulator(settings)
    sim._positions["ETH-USD_MEAN_REVERSION"] = PaperPosition(
        "ETH-USD", Side.BUY, 0.1, 3000.0, StrategyType.MEAN_REVERSION, 2970.0, 3060.0)
    sim._positions["BTC-USD_FIBONACCI_RETRACEMENT"] = PaperPosition(
        "BTC-USD", Side.SELL, 0.01, 60000.0, StrategyType.FIBONACCI_RETRACEMENT, 61000.0, 58000.0)
    sim._last_prices = {"ETH-USD": 3030.0, "BTC-USD": 59000.0}
    fills = sim.close_all_positions(reason="max_drawdown")
    assert len(fills) == 2 and sim.position_count == 0
    by_sym = {t.symbol: t for t in fills}
    assert by_sym["ETH-USD"].side == Side.SELL and by_sym["ETH-USD"].pnl > 0
    assert by_sym["BTC-USD"].side == Side.BUY and by_sym["BTC-USD"].pnl > 0
    assert by_sym["ETH-USD"].price < 3030.0, "adverse slippage applied on flatten"
    assert by_sym["ETH-USD"].signal_features["exit_reason"] == "MAX_DRAWDOWN"


def _shutdown_bot(settings: Settings, paper: bool = False, dry_run: bool = False):
    from main import BotStrike
    bot = BotStrike.__new__(BotStrike)
    bot.settings = settings
    bot.paper = paper
    bot.dry_run = dry_run
    bot._running = True
    bot._positions = {"ETH-USD": Position("ETH-USD", Side.BUY, 0.1, 3000.0)}
    bot._dd_flattened = False
    bot._shutdown_flatten_done = False
    bot.strategies = []
    bot.order: List[str] = []
    bot.execution_engine = MagicMock()

    async def _close(*a, **k):
        bot.order.append("close_all_positions")
        return {"closed": [{"symbol": "ETH-USD"}], "remaining": [], "errors": []}

    async def _cancel(*a, **k):
        bot.order.append("cancel_all")

    bot.execution_engine.close_all_positions = AsyncMock(side_effect=_close)
    bot.execution_engine.cancel_all = AsyncMock(side_effect=_cancel)
    bot.execution_engine.cleanup_stale_orders = MagicMock(return_value=0)
    bot.execution_engine.reconcile_orders_with_exchange = AsyncMock(return_value=0)
    bot.paper_sim = None
    if paper:
        bot.paper_sim = MagicMock()
        bot.paper_sim.close_all_positions = MagicMock(return_value=[])
        bot.paper_sim.get_all_positions = MagicMock(return_value={})
    bot.websocket = MagicMock()
    bot.websocket.stop = AsyncMock()
    bot.client = MagicMock()
    bot.client.close = AsyncMock()
    bot.trade_db = MagicMock()
    bot.metrics = MagicMock()
    bot.metrics.get_metrics.return_value = {}
    bot.trading_logger = MagicMock()
    bot.notifier = MagicMock()
    bot.notifier.notify_shutdown = AsyncMock()
    bot.notifier.stop = AsyncMock()
    bot.notifier.notify_risk_event = AsyncMock()
    bot.notifier.notify_error = AsyncMock()
    bot.risk_manager = MagicMock()
    bot.risk_manager.current_equity = 900.0
    bot.risk_manager.current_drawdown_pct = 0.0
    bot.risk_manager._drawdown_halted = False
    bot.risk_manager._positions = {}
    bot.risk_manager.check_daily_reset_safe = AsyncMock()
    bot.risk_manager.update_position_safe = AsyncMock()
    return bot


def test_shutdown_closes_positions_before_cancel_all(settings):
    settings.trading.close_positions_on_shutdown = True
    bot = _shutdown_bot(settings)
    run(bot.shutdown())
    assert bot.order == ["close_all_positions", "cancel_all"]
    assert bot._positions == {}
    # a second shutdown() call (signal handler + finally) does not flatten again
    run(bot.shutdown())
    assert bot.order == ["close_all_positions", "cancel_all"]


def test_shutdown_keep_positions_policy_never_cancels_protectives(settings):
    settings.trading.close_positions_on_shutdown = False
    bot = _shutdown_bot(settings)
    run(bot.shutdown())
    assert bot.order == [], "keeping positions must keep their SL/TP alive (no cancel_all)"


def test_shutdown_paper_and_dry_run_never_touch_exchange(settings):
    settings.trading.close_positions_on_shutdown = True
    bot = _shutdown_bot(settings, paper=True)
    run(bot.shutdown())
    bot.paper_sim.close_all_positions.assert_called_once()
    assert bot.order == []
    bot = _shutdown_bot(settings, dry_run=True)
    run(bot.shutdown())
    assert bot.order == []


def test_drawdown_halt_flattens_once_then_cancels(settings):
    settings.trading.risk_check_interval_sec = 0.005
    bot = _shutdown_bot(settings)
    bot.risk_manager.current_drawdown_pct = 0.2   # >= max_drawdown_pct (0.10)

    async def scenario():
        task = asyncio.create_task(bot._risk_monitor_loop())
        await asyncio.sleep(0.1)                   # ~20 iterations of the loop
        bot._running = False
        await task

    run(scenario())
    assert bot.order == ["close_all_positions", "cancel_all"], \
        "flatten exactly once, close BEFORE cancel, not every risk cycle"
    assert bot.risk_manager._drawdown_halted is True
    assert bot.trading_logger.log_risk_event.call_count == 1
