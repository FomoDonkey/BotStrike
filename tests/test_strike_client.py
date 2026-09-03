"""Strike V2 client: request bodies conform to the official OpenAPI spec (docs/strike), signatures verify,
responses are normalised to the engine's Binance-like shape."""
import hashlib
import io
import re

import pytest
from nacl.signing import SigningKey, VerifyKey

from config.settings import Settings
from core.types import Order, OrderType, Side, TimeInForce
from exchange.strike_client import STATUS_MAP, StrikeClient

SPEC = "docs/strike/skills__openapi__trade-api.yaml"


def _schema_props(name: str):
    s = io.open(SPEC, encoding="utf-8", errors="replace").read()
    i = s.index(f"\n    {name}:\n")
    block = s[i + 1:]
    m = re.search(r"\n    [A-Z][A-Za-z]+:\n", block[1:])
    block = block[: m.start() + 1] if m else block
    props = re.findall(r"^        ([A-Za-z_]+):\n", block, re.M)
    req = re.search(r"required:\n((?:        - [a-z_]+\n)+)", block)
    required = re.findall(r"- ([a-z_]+)", req.group(1)) if req else []
    enums = {}
    for pm in re.finditer(r"^        ([a-z_]+):\n(?:          [^\n]*\n)*?          enum:\n((?:            - [A-Za-z_]+\n)+)", block, re.M):
        enums[pm.group(1)] = re.findall(r"- ([A-Za-z_]+)", pm.group(2))
    return set(props), required, enums


def _client(sub=None):
    s = Settings()
    sk = SigningKey.generate()
    s.api_private_key = sk.encode().hex()
    s.api_public_key = sk.verify_key.encode().hex()
    return StrikeClient(s, sub_account_id=sub), sk


def _order(**kw):
    base = dict(symbol="BTC-USD", side=Side.BUY, order_type=OrderType.LIMIT, quantity=0.01, price=50000.0,
                client_order_id="bs-test-1")
    base.update(kw)
    return Order(**base)


def test_signature_verifies_with_public_key_and_covers_query_string():
    c, sk = _client()
    body = '{"symbol":"BTC-USD"}'
    h = c._sign_request("POST", "/v2/order?sub_account_id=x", body)
    assert h["X-API-Wallet-Public-Key"] == sk.verify_key.encode().hex()
    msg = f"POST:/v2/order?sub_account_id=x:{h['X-API-Wallet-Timestamp']}:{h['X-API-Wallet-Nonce']}:{hashlib.sha256(body.encode()).hexdigest()}"
    VerifyKey(bytes.fromhex(h["X-API-Wallet-Public-Key"])).verify(msg.encode(), bytes.fromhex(h["X-API-Wallet-Signature"]))
    # GET: body hash of the empty string
    g = c._sign_request("GET", "/v2/account", "")
    msg = f"GET:/v2/account:{g['X-API-Wallet-Timestamp']}:{g['X-API-Wallet-Nonce']}:{hashlib.sha256(b'').hexdigest()}"
    VerifyKey(bytes.fromhex(g["X-API-Wallet-Public-Key"])).verify(msg.encode(), bytes.fromhex(g["X-API-Wallet-Signature"]))


def test_order_body_matches_create_order_request_schema():
    c, _ = _client()
    props, required, enums = _schema_props("CreateOrderRequest")
    body = c._order_body(_order(stop_price=49000.0, time_in_force=TimeInForce.IOC, post_only=True, reduce_only=True))
    assert set(body) <= props, set(body) - props
    assert all(r in body for r in required)
    assert body["side"] in enums["side"] and body["type"] in enums["type"] and body["time_in_force"] in enums["time_in_force"]
    assert body["size"] == "0.01" and body["price"] == "50000" and body["stop_price"] == "49000"
    assert body["post_only"] is True and body["reduce_only"] is True and body["client_order_id"] == "bs-test-1"
    # market orders never carry a price; sell side lowercase
    m = c._order_body(_order(order_type=OrderType.MARKET, side=Side.SELL, price=123.0))
    assert "price" not in m and m["side"] == "sell" and m["type"] == "market"


def test_bracket_cancel_replace_and_scoping_bodies(monkeypatch):
    c, _ = _client(sub="sub-1")
    calls = []

    async def fake(method, path, body=None, params=None):
        calls.append((method, path, body, params))
        return {"client_order_id": (body or {}).get("client_order_id"), "sequence_id": 7, "strategy_id": "st-1"}

    monkeypatch.setattr(c, "_auth_request", fake)
    import asyncio
    asyncio.run(c.place_bracket_order(_order(), tp_price=55000, sl_price=48000))
    m, p, body, _ = calls[-1]
    assert (m, p) == ("POST", "/v2/order/strategy") and body["sub_account_id"] == "sub-1"
    assert body["tp_order"]["type"] == "take_profit" and body["tp_order"]["stop_price"] == "55000"
    assert body["sl_order"]["type"] == "stop" and body["sl_order"]["stop_price"] == "48000" and body["sl_order"]["size"] == "0.01"
    props, required, _ = _schema_props("CreateStrategyOrderRequest")
    assert set(body) - {"tp_order", "sl_order", "sub_account_id"} <= props
    asyncio.run(c.cancel_order("BTC-USD", "12345"))
    assert calls[-1][:3] == ("DELETE", "/v2/order/cancel", {"order_id": 12345, "symbol": "BTC-USD", "sub_account_id": "sub-1"})
    asyncio.run(c.cancel_all_orders("BTC-USD"))
    assert calls[-1][2] == {"symbol": "BTC-USD", "sub_account_id": "sub-1"}
    asyncio.run(c.replace_order("BTC-USD", "9", _order(price=51000.0)))
    body = calls[-1][2]
    assert body["cancel"] == {"order_id": 9, "symbol": "BTC-USD"} and body["new_order"]["price"] == "51000"
    asyncio.run(c.set_leverage("XAU-USD", 3))
    assert calls[-1][:3] == ("POST", "/v2/leverage", {"symbol": "XAU-USD", "leverage": 3, "sub_account_id": "sub-1"})
    asyncio.run(c.get_positions("ETH-USD"))
    assert calls[-1][0] == "GET" and calls[-1][3] == {"symbol": "ETH-USD", "sub_account_id": "sub-1"}


def test_place_order_confirms_state_and_normalises(monkeypatch):
    c, _ = _client()
    seen = []

    async def fake(method, path, body=None, params=None):
        seen.append((method, path))
        if path == "/v2/order" and method == "POST":
            return {"client_order_id": body["client_order_id"], "sequence_id": 1, "message_id": "m"}
        return {"order": {"ID": 777, "ClientOrderID": "bs-test-1", "Symbol": "BTC-USD", "Side": "buy", "Status": "filled",
                          "Type": "market", "Size": "0.01", "Filled": "0.01", "Price": "50010"}}

    monkeypatch.setattr(c, "_auth_request", fake)

    async def no_sleep(_):
        return None

    monkeypatch.setattr("exchange.strike_client.asyncio.sleep", no_sleep)
    import asyncio
    r = asyncio.run(c.place_order(_order(order_type=OrderType.MARKET, price=None)))
    assert r["orderId"] == "777" and r["status"] == "FILLED" and r["executedQty"] == 0.01 and r["clientOrderId"] == "bs-test-1"
    assert seen[0] == ("POST", "/v2/order") and seen[1] == ("GET", "/v2/order")


def test_normalisation_of_orders_and_positions():
    o = StrikeClient.normalize_order({"ID": 5, "ClientOrderID": "x", "Symbol": "ETH-USD", "Side": "sell", "Status": "open",
                                      "Size": "1.5", "Filled": "0.5", "Price": "2000", "ReduceOnly": True})
    assert o["status"] == "PARTIALLY_FILLED" and o["side"] == "SELL" and o["executedQty"] == 0.5 and o["reduceOnly"] is True
    for strike, engine in STATUS_MAP.items():
        assert StrikeClient.normalize_order({"Status": strike})["status"] == engine
    p = StrikeClient.normalize_position({"symbol": "XAU-USD", "PositionID": 3, "Side": "sell", "Size": "0.5", "EntryPrice": "4700",
                                         "MarginMode": "cross", "Leverage": 5, "upnl": "-3.2", "liquidation_price": "5100"})
    assert p["positionAmt"] == -0.5 and p["entryPrice"] == 4700 and p["unrealizedProfit"] == -3.2
    assert p["leverage"] == 5 and p["liquidationPrice"] == 5100 and p["side"] == "sell"


def test_markets_parsing_and_size_rounding(monkeypatch):
    c, _ = _client()
    info = {"symbols": [{"symbol": "XAU-USD", "status": "trading", "baseAsset": "XAU", "quoteAsset": "USD", "contractType": "PERPETUAL",
                         "pricePrecision": 8, "quantityPrecision": 8, "liquidationFee": "0.0125",
                         "filters": [{"filterType": "PRICE_FILTER", "tickSize": "0.01", "minPrice": "0.01", "maxPrice": "200000"},
                                     {"filterType": "LOT_SIZE", "minQty": "0.001", "stepSize": "0.001", "maxQty": "600"}]}]}

    async def fake_public(path, params=None):
        return info

    monkeypatch.setattr(c, "_public_get", fake_public)
    import asyncio
    m = asyncio.run(c.get_markets())["XAU-USD"]
    assert m["tick_size"] == 0.01 and m["min_qty"] == 0.001 and m["step_size"] == 0.001 and m["liquidation_fee"] == 0.0125
    assert asyncio.run(c.round_size("XAU-USD", 0.0129)) == pytest.approx(0.012)
    assert asyncio.run(c.round_size("XAU-USD", 0.0004)) == 0.0          # below min_qty → 0, never a rejected order
    assert asyncio.run(c.round_price("XAU-USD", 4711.237)) == pytest.approx(4711.24)


def test_market_snapshot_reads_funding_rate(monkeypatch):
    c, _ = _client()

    async def fake_public(path, params=None):
        return {"/v2/ticker/24hr": [{"symbol": "BTC-USD", "lastPrice": "77000", "quoteVolume": "1000"}],
                "/v2/premiumIndex": [{"symbol": "BTC-USD", "markPrice": "77010", "indexPrice": "77020", "fundingRate": "0.0000169"}],
                "/v2/depth": {"bids": [["76999", "1"]], "asks": [["77001", "2"]]},
                "/v2/openInterest": {"symbol": "BTC-USD", "openInterest": "12.5"}}[path]

    monkeypatch.setattr(c, "_public_get", fake_public)
    import asyncio
    snap = asyncio.run(c.get_market_snapshot("BTC-USD"))
    assert snap.mark_price == 77010 and snap.funding_rate == pytest.approx(0.0000169) and snap.open_interest == 12.5
    assert snap.orderbook.bids[0].price == 76999 and snap.price == 77000
