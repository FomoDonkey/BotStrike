"""Strike user-WS logon (documented `session.logon`) and market-data seeding from Strike klines."""
import asyncio
import time
import json

from nacl.signing import SigningKey, VerifyKey

from config.settings import Settings
from core.market_data import MarketDataCollector
from exchange.websocket_client import StrikeWebSocket


def test_logon_message_matches_docs_and_signature_verifies():
    sk = SigningKey.generate()
    msg = StrikeWebSocket.build_logon(sk.encode().hex(), now_ms=1705000000000)
    assert msg["method"] == "session.logon" and msg["id"] == 1
    p = msg["params"]
    assert p["apiKey"] == sk.verify_key.encode().hex() and p["timestamp"] == 1705000000000
    VerifyKey(bytes.fromhex(p["apiKey"])).verify(f"session.logon:{p['timestamp']}:{p['apiKey']}".encode(),
                                                 bytes.fromhex(p["signature"]))
    # a 128-hex "full key" is accepted: only the 32-byte seed is used
    full = sk.encode().hex() + sk.verify_key.encode().hex()
    assert StrikeWebSocket.build_logon(full, now_ms=1)["params"]["apiKey"] == p["apiKey"]


def test_user_ws_authentication_parses_documented_response():
    s = Settings()
    sk = SigningKey.generate()
    s.api_private_key = sk.encode().hex()
    s.api_public_key = sk.verify_key.encode().hex()
    ws_client = StrikeWebSocket(s)

    class FakeWS:
        def __init__(self, reply):
            self.sent = []
            self.reply = reply

        async def send(self, m):
            self.sent.append(json.loads(m))

        async def recv(self):
            return json.dumps(self.reply)

    ok = FakeWS({"id": 1, "status": 200, "result": {"authenticated": True, "account_id": "acc-1"}})
    assert asyncio.run(ws_client._authenticate_user_ws(ok)) == "acc-1"
    assert ok.sent[0]["method"] == "session.logon"
    bad = FakeWS({"id": 1, "status": 401, "error": {"message": "bad signature"}})
    assert asyncio.run(ws_client._authenticate_user_ws(bad)) is None


def test_listener_splits_newline_frames_and_skips_acks():
    s = Settings()
    ws_client = StrikeWebSocket(s)
    got = []

    async def cb(data):
        got.append(data["e"])

    ws_client.on("markPriceUpdate", cb)

    class FakeStream:
        def __init__(self, frames):
            self.frames = frames

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self.frames:
                raise StopAsyncIteration
            return self.frames.pop(0)

    frames = ['{"id":1,"result":null}\n{"method":"pong","id":"hb"}\n{"e":"markPriceUpdate","s":"BTC-USD","p":"1"}',
              '{"e":"markPriceUpdate","s":"ETH-USD","p":"2"}\n']
    asyncio.run(ws_client._listen(FakeStream(frames), "market"))
    assert got == ["markPriceUpdate", "markPriceUpdate"]


def test_seed_from_strike_uses_native_symbols_and_builds_frame():
    from core.regime_detector import RegimeDetector
    s = Settings()
    md = MarketDataCollector(s, client=None, regime_detector=RegimeDetector(s))
    calls = []

    class FakeClient:
        async def get_klines(self, symbol, interval="1m", limit=500, start_time=None, end_time=None):
            calls.append((symbol, interval, limit, start_time))
            base = 1_788_400_000_000
            return [[base + i * 60_000, "100", "101", "99", "100.5", "3", base + i * 60_000 + 59_999, "300", 5, "1", "100", "0"]
                    for i in range(limit)]

    cfg = s.get_symbol_config("BTC-USD")
    before_ms = time.time() * 1000
    asyncio.run(md.seed_from_strike("XAU-USD", cfg, FakeClient(), hours=2))
    assert len(calls) == 1
    sym, interval, limit, start_time = calls[0]
    assert (sym, interval, limit) == ("XAU-USD", "1m", 120)
    # A start_time is REQUIRED, not optional: asked without one the venue answers from a cached
    # window whose last bar was five hours old (measured 2026-09-04), so the chart would seed stale.
    assert start_time is not None
    assert before_ms - 2 * 3600 * 1000 - 5000 <= start_time <= before_ms - 2 * 3600 * 1000 + 5000
    df = md.get_dataframe("XAU-USD")
    assert df is not None and len(df) == 120 and float(df["close"].iloc[-1]) == 100.5
    snap = md.get_snapshot("XAU-USD")
    assert snap is not None and snap.price == 100.5
