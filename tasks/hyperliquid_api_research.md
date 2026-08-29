# Hyperliquid Perpetual Futures API — Complete Research Report

**Date:** 2026-04-04
**Purpose:** Precise API reference for integration into BotStrike trading bot

---

## 1. BASE URLS

| Environment | REST | WebSocket |
|-------------|------|-----------|
| **Mainnet** | `https://api.hyperliquid.xyz` | `wss://api.hyperliquid.xyz/ws` |
| **Testnet** | `https://api.hyperliquid-testnet.xyz` | `wss://api.hyperliquid-testnet.xyz/ws` |

All REST requests are POST with `Content-Type: application/json`.

Two main REST endpoints:
- **Info:** `POST /info` — read-only queries (no auth needed)
- **Exchange:** `POST /exchange` — trading actions (requires EIP-712 signature)

---

## 2. SYMBOL NAMING CONVENTION

Hyperliquid uses **plain ticker symbols** for API calls: `"BTC"`, `"ETH"`, `"SOL"`, `"ADA"`, etc.

- NOT `BTC-PERP` or `BTCUSDT` — just `"BTC"`
- Internally, each coin maps to an **integer asset index** (BTC=0 on mainnet)
- The SDK resolves names to indices automatically via the `meta` endpoint
- Spot tokens use format `"PURR/USDC"` or index `10000 + spotIndex`
- Builder-deployed perps use format `"{dex}:{coin}"`

To get the full asset list and indices:
```json
POST /info
{"type": "meta"}
```
Response includes `universe` array where each item has `name`, `szDecimals`, `maxLeverage`.

---

## 3. AUTHENTICATION

### Mechanism: EIP-712 Structured Data Signing (Wallet-Based)
- **NO API keys** — uses Ethereum private key signing
- Each trading action is signed with EIP-712 typed data
- Uses `eth_account` Python library for signing

### Two Signing Modes:

**A) Agent-key (recommended for bots):**
- Create a dedicated "agent wallet" via `approveAgent` action
- Agent can sign orders, cancels, leverage updates (trading actions)
- Agent CANNOT sign withdrawals, transfers (account-level actions)
- More secure: agent key has no withdrawal permissions

**B) Direct private key:**
- Sign everything with your main wallet private key
- Required for: transfers, withdrawals, sub-account creation

### Nonce System:
- Nonces are millisecond timestamps (not sequential)
- System stores the 100 highest nonces per address
- Valid range: `(T - 2 days, T + 1 day)` where T = current block time
- Typically use `int(time.time() * 1000)` as nonce

### Python Authentication Setup:
```python
import eth_account
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants

wallet = eth_account.Account.from_key("0xYOUR_PRIVATE_KEY")
exchange = Exchange(wallet, constants.MAINNET_API_URL)

# Or with agent wallet (for sub-accounts):
exchange = Exchange(
    agent_wallet,
    constants.MAINNET_API_URL,
    account_address="0xMASTER_ADDRESS"
)
```

---

## 4. REST API — INFO ENDPOINT (Read-Only)

All requests: `POST https://api.hyperliquid.xyz/info`

### 4.1 Get All Mid Prices
```json
{"type": "allMids"}
```
**Response:** `{"BTC": "65432.5", "ETH": "3456.7", ...}`

### 4.2 Get Orderbook (L2)
```json
{"type": "l2Book", "coin": "BTC", "nSigFigs": 5}
```
- `nSigFigs`: 2-5 (optional, price aggregation level)
- `mantissa`: 1|2|5 (optional)

**Response:**
```json
{
  "coin": "BTC",
  "time": 1234567890,
  "levels": [
    [{"px": "65430.0", "sz": "1.5", "n": 3}, ...],  // bids
    [{"px": "65435.0", "sz": "0.8", "n": 1}, ...]   // asks
  ]
}
```

### 4.3 Get Candles/Klines
```json
{
  "type": "candleSnapshot",
  "req": {
    "coin": "BTC",
    "interval": "1m",
    "startTime": 1700000000000,
    "endTime": 1700100000000
  }
}
```
- **Intervals:** `1m|3m|5m|15m|30m|1h|2h|4h|8h|12h|1d|3d|1w|1M`
- **Limit:** 5000 most recent candles max
- **Weight:** 1 per 60 candles

**Response:** Array of:
```json
{
  "t": 1700000000000,  // open time (ms)
  "T": 1700000060000,  // close time (ms)
  "s": "BTC",          // symbol
  "i": "1m",           // interval
  "o": "65400.0",      // open
  "c": "65450.0",      // close
  "h": "65460.0",      // high
  "l": "65390.0",      // low
  "v": "123.45",       // volume
  "n": 456             // number of trades
}
```

### 4.4 Get User Positions & Balance (clearinghouseState)
```json
{"type": "clearinghouseState", "user": "0xADDRESS"}
```
**Response:**
```json
{
  "marginSummary": {
    "accountValue": "10000.0",
    "totalNtlPos": "5000.0",
    "totalRawUsd": "5000.0",
    "totalMarginUsed": "500.0"
  },
  "crossMarginSummary": {
    "accountValue": "10000.0",
    "totalNtlPos": "5000.0",
    "totalRawUsd": "5000.0",
    "totalMarginUsed": "500.0"
  },
  "withdrawable": "4500.0",
  "assetPositions": [
    {
      "position": {
        "coin": "BTC",
        "szi": "0.1",
        "leverage": {"type": "cross", "value": 10},
        "entryPx": "65000.0",
        "positionValue": "6543.2",
        "unrealizedPnl": "43.2",
        "returnOnEquity": "0.066",
        "liquidationPx": "58000.0",
        "marginUsed": "654.32",
        "maxLeverage": 50
      },
      "type": "oneWay"
    }
  ]
}
```

### 4.5 Get Open Orders
```json
{"type": "openOrders", "user": "0xADDRESS"}
```
**Response:** Array of:
```json
{
  "coin": "BTC",
  "limitPx": "64000.0",
  "oid": 77738308,
  "side": "B",
  "sz": "0.1",
  "timestamp": 1700000000000
}
```

### 4.6 Get Open Orders (Frontend/Extended)
```json
{"type": "frontendOpenOrders", "user": "0xADDRESS"}
```
Adds: `orderType`, `origSz`, `isTrigger`, `triggerPx`, `isPositionTpsl`, `reduceOnly`

### 4.7 Get User Fills (Trade History)
```json
{"type": "userFills", "user": "0xADDRESS"}
```
**Response:** Array of (max 2000):
```json
{
  "coin": "BTC",
  "px": "65000.0",
  "sz": "0.1",
  "side": "B",
  "time": 1700000000000,
  "fee": "2.925",
  "feeToken": "USDC",
  "closedPnl": "0.0",
  "dir": "Open Long",
  "tid": 123456
}
```

### 4.8 Get User Fills by Time Range
```json
{
  "type": "userFillsByTime",
  "user": "0xADDRESS",
  "startTime": 1700000000000,
  "endTime": 1700100000000
}
```

### 4.9 Get Order Status
```json
{"type": "orderStatus", "user": "0xADDRESS", "oid": 77738308}
```
**Response:**
```json
{
  "status": "open",
  "order": {...},
  "statusTimestamp": 1700000000000
}
```
Statuses: `open|filled|canceled|triggered|rejected|marginCanceled`

### 4.10 Exchange Metadata + Asset Contexts
```json
{"type": "metaAndAssetCtxs"}
```
**Response:** `[meta, [assetCtx, ...]]` where each assetCtx has:
```json
{
  "funding": "0.0001",
  "markPx": "65432.0",
  "midPx": "65430.0",
  "openInterest": "1234.5",
  "oraclePx": "65431.0",
  "premium": "0.00001",
  "dayNtlVlm": "50000000.0",
  "prevDayPx": "64500.0"
}
```

### 4.11 Funding History
```json
{
  "type": "fundingHistory",
  "coin": "BTC",
  "startTime": 1700000000000,
  "endTime": 1700100000000
}
```

### 4.12 Predicted Fundings
```json
{"type": "predictedFundings"}
```

### 4.13 User Rate Limit Status
```json
{"type": "userRateLimit", "user": "0xADDRESS"}
```
**Response:** `{cumVlm, nRequestsUsed, nRequestsCap, nRequestsSurplus}`

---

## 5. REST API — EXCHANGE ENDPOINT (Authenticated)

All requests: `POST https://api.hyperliquid.xyz/exchange`

Every request body wraps:
```json
{
  "action": { "type": "...", ...params },
  "nonce": 1700000000000,
  "signature": { "r": "...", "s": "...", "v": 27 },
  "vaultAddress": null,
  "expiresAfter": null
}
```

### 5.1 Place Order(s)
```json
{
  "action": {
    "type": "order",
    "orders": [
      {
        "a": 0,              // asset index (BTC=0)
        "b": true,           // isBuy
        "p": "65000.0",      // price (string)
        "s": "0.1",          // size (string)
        "r": false,          // reduceOnly
        "t": {               // order type
          "limit": {"tif": "Gtc"}
        },
        "c": "0x..."         // cloid (optional, 16-byte hex)
      }
    ],
    "grouping": "na"
  }
}
```

**Order Type variants:**

| Order Type | `"t"` field |
|-----------|-------------|
| **Limit GTC** | `{"limit": {"tif": "Gtc"}}` |
| **Limit IOC** (market-like) | `{"limit": {"tif": "Ioc"}}` |
| **Limit ALO** (post-only) | `{"limit": {"tif": "Alo"}}` |
| **Stop Loss** | `{"trigger": {"isMarket": true, "triggerPx": "60000", "tpsl": "sl"}}` |
| **Take Profit** | `{"trigger": {"isMarket": true, "triggerPx": "70000", "tpsl": "tp"}}` |
| **Stop Limit** | `{"trigger": {"isMarket": false, "triggerPx": "60000", "tpsl": "sl"}}` |

**Grouping values:**
- `"na"` — standalone order
- `"normalTpsl"` — TP/SL attached to a normal order (batch: [entry, tp, sl])
- `"positionTpsl"` — TP/SL attached to existing position

**Response (filled):**
```json
{
  "status": "ok",
  "response": {
    "type": "order",
    "data": {
      "statuses": [{"filled": {"totalSz": "0.1", "avgPx": "65000.0", "oid": 77738308}}]
    }
  }
}
```

**Response (resting):**
```json
{
  "status": "ok",
  "response": {
    "type": "order",
    "data": {
      "statuses": [{"resting": {"oid": 77738308}}]
    }
  }
}
```

### 5.2 Cancel Order(s)
```json
{
  "action": {
    "type": "cancel",
    "cancels": [{"a": 0, "o": 77738308}]
  }
}
```
- `a` = asset index
- `o` = order ID (oid)

### 5.3 Cancel by CLOID
```json
{
  "action": {
    "type": "cancelByCloid",
    "cancels": [{"asset": 0, "cloid": "0x..."}]
  }
}
```

### 5.4 Modify Order
```json
{
  "action": {
    "type": "modify",
    "oid": 77738308,
    "order": {
      "a": 0, "b": true, "p": "65100.0", "s": "0.1",
      "r": false, "t": {"limit": {"tif": "Gtc"}}
    }
  }
}
```

### 5.5 Batch Modify Orders
```json
{
  "action": {
    "type": "batchModify",
    "modifies": [
      {"oid": 77738308, "order": {...}},
      {"oid": 77738309, "order": {...}}
    ]
  }
}
```

### 5.6 Update Leverage
```json
{
  "action": {
    "type": "updateLeverage",
    "asset": 0,
    "isCross": true,
    "leverage": 10
  }
}
```

### 5.7 Update Isolated Margin
```json
{
  "action": {
    "type": "updateIsolatedMargin",
    "asset": 0,
    "isBuy": true,
    "ntli": 1000000
  }
}
```
Note: `ntli` is in 6 decimals (1000000 = 1 USD)

### 5.8 TWAP Order
```json
{
  "action": {
    "type": "twapOrder",
    "twap": {
      "a": 0,        // asset
      "b": true,     // isBuy
      "s": "1.0",    // size
      "r": false,    // reduceOnly
      "m": 30,       // minutes duration
      "t": true      // randomize
    }
  }
}
```

### 5.9 Schedule Cancel (Dead Man's Switch)
```json
{
  "action": {
    "type": "scheduleCancel",
    "time": 1700000060000
  }
}
```
Cancels all open orders if no new action within the scheduled time.

### 5.10 Approve Agent (API Wallet)
```json
{
  "action": {
    "type": "approveAgent",
    "hyperliquidChain": "Mainnet",
    "signatureChainId": "0xa4b1",
    "agentAddress": "0xAGENT_ADDRESS",
    "agentName": "my_bot",
    "nonce": 1700000000000
  }
}
```

### 5.11 Withdraw
```json
{
  "action": {
    "type": "withdraw3",
    "hyperliquidChain": "Mainnet",
    "signatureChainId": "0xa4b1",
    "amount": "100",
    "time": 1700000000000,
    "destination": "0xADDRESS"
  }
}
```

---

## 6. WEBSOCKET API

**Connect to:** `wss://api.hyperliquid.xyz/ws`

### Subscription Format
```json
{
  "method": "subscribe",
  "subscription": {
    "type": "<channel_type>",
    ...params
  }
}
```

### Unsubscribe
```json
{
  "method": "unsubscribe",
  "subscription": { "type": "<channel_type>", ...same_params }
}
```

### 6.1 Trades Stream
```json
{"method": "subscribe", "subscription": {"type": "trades", "coin": "BTC"}}
```
**Data:** `WsTrade[]`
```json
{
  "coin": "BTC",
  "side": "B",
  "px": "65432.0",
  "sz": "0.1",
  "hash": "0x...",
  "time": 1700000000000,
  "tid": 123456,
  "users": ["0xbuyer", "0xseller"]
}
```

### 6.2 L2 Orderbook Stream
```json
{"method": "subscribe", "subscription": {"type": "l2Book", "coin": "BTC"}}
```
**Data:** `WsBook`
```json
{
  "coin": "BTC",
  "levels": [
    [{"px": "65430.0", "sz": "1.5", "n": 3}, ...],
    [{"px": "65435.0", "sz": "0.8", "n": 1}, ...]
  ],
  "time": 1700000000000
}
```

### 6.3 Best Bid/Offer Stream
```json
{"method": "subscribe", "subscription": {"type": "bbo", "coin": "BTC"}}
```

### 6.4 Candle Stream
```json
{"method": "subscribe", "subscription": {"type": "candle", "coin": "BTC", "interval": "1m"}}
```
**Data:** `Candle[]` (same format as REST candles)

### 6.5 All Mid Prices Stream
```json
{"method": "subscribe", "subscription": {"type": "allMids"}}
```

### 6.6 User Order Updates
```json
{"method": "subscribe", "subscription": {"type": "orderUpdates", "user": "0xADDRESS"}}
```
**Data:** `WsOrder[]` — order status changes

### 6.7 User Fills Stream
```json
{"method": "subscribe", "subscription": {"type": "userFills", "user": "0xADDRESS"}}
```
**Data:** `WsUserFills` with fill details including `coin`, `px`, `sz`, `side`, `fee`, `oid`, `crossed`, `liquidation`

### 6.8 User Events Stream
```json
{"method": "subscribe", "subscription": {"type": "userEvents", "user": "0xADDRESS"}}
```

### 6.9 Clearinghouse State Stream (Positions/Balance)
```json
{"method": "subscribe", "subscription": {"type": "clearinghouseState", "user": "0xADDRESS"}}
```

### 6.10 Open Orders Stream
```json
{"method": "subscribe", "subscription": {"type": "openOrders", "user": "0xADDRESS"}}
```

### 6.11 Active Asset Context (Mark Price, Funding, OI)
```json
{"method": "subscribe", "subscription": {"type": "activeAssetCtx", "coin": "BTC"}}
```

### WebSocket Limits
| Limit | Value |
|-------|-------|
| Max concurrent connections | 10 |
| Max new connections/min | 30 |
| Max subscriptions total | 1000 |
| Max distinct users (user subs) | 10 |
| Max messages/min (all conns) | 2000 |
| Max inflight post messages | 100 |

---

## 7. RATE LIMITS (REST)

### IP-Based (Weight Budget)
- **Total budget:** 1200 weight per minute per IP

| Endpoint Category | Weight |
|-------------------|--------|
| Exchange actions (unbatched) | 1 |
| Exchange actions (batched) | `1 + floor(batch_length / 40)` |
| `l2Book`, `allMids`, `clearinghouseState`, `orderStatus` | 2 |
| Most other info endpoints | 20 |
| `userRole` | 60 |
| `candleSnapshot` | 1 per 60 candles |
| Paginated endpoints (fills, history, funding) | +1 per 20 items |

### Address-Based
- **Initial buffer:** 10,000 requests per new address
- **Accrual:** +1 request per 1 USDC traded (cumulative lifetime)
- **When throttled:** 1 request per 10 seconds
- **Cancel allowance:** `min(limit + 100000, limit * 2)`
- **Open order cap:** 1000 + 1 per 5M USDC volume (max 5000)

---

## 8. FEE STRUCTURE

### Perpetual Fees (Base Tiers)

| Tier | 14-Day Volume | Taker | Maker |
|------|--------------|-------|-------|
| 0 | Base | 0.045% | 0.015% |
| 1 | > $5M | 0.040% | 0.012% |
| 2 | > $25M | 0.035% | 0.008% |
| 3 | > $100M | 0.030% | 0.004% |
| 4 | > $500M | 0.028% | 0.000% |
| 5 | > $2B | 0.026% | 0.000% |
| 6 | > $7B | 0.024% | 0.000% |

### Discounts
- **HYPE Staking:** 5% (>10 HYPE) to 40% (>500k HYPE) fee reduction
- **Aligned quote assets:** 20% lower taker, 50% better maker rebates
- **Stable pairs:** 80% lower fees
- **Referral program:** Additional discount available

### Spot Fees (Higher)
- Base: 0.070% taker / 0.040% maker
- Scales down similarly with volume

---

## 9. PYTHON SDK

### Installation
```bash
pip install hyperliquid-python-sdk
```

### Requirements
- Python >= 3.9, < 4.0
- Dependencies: `eth_account` (for signing)

### Key Classes

#### `Info` — Read-only queries
```python
from hyperliquid.info import Info
from hyperliquid.utils import constants

info = Info(constants.MAINNET_API_URL, skip_ws=True)

# Methods:
info.all_mids()                                    # → {"BTC": "65000.0", ...}
info.l2_snapshot("BTC")                            # → orderbook
info.candles_snapshot("BTC", "1m", start, end)     # → candles
info.user_state("0xADDRESS")                       # → positions + margin
info.open_orders("0xADDRESS")                      # → open orders
info.user_fills("0xADDRESS")                       # → trade history
info.user_fills_by_time("0xADDRESS", start, end)   # → fills in range
info.meta()                                         # → asset metadata
info.spot_meta()                                    # → spot metadata
info.funding_history("BTC", start, end)             # → funding rates
info.query_order_by_oid("0xADDRESS", oid)          # → order status
info.query_order_by_cloid("0xADDRESS", cloid)      # → order by cloid

# WebSocket subscriptions:
info.subscribe({"type": "trades", "coin": "BTC"}, callback)
info.subscribe({"type": "l2Book", "coin": "BTC"}, callback)
info.unsubscribe(subscription, sub_id)
```

#### `Exchange` — Trading operations
```python
import eth_account
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants

wallet = eth_account.Account.from_key("0xPRIVATE_KEY")
exchange = Exchange(wallet, constants.MAINNET_API_URL)

# Place limit order:
exchange.order(
    name="BTC",          # coin name
    is_buy=True,         # direction
    sz=0.1,              # size
    limit_px=65000.0,    # price
    order_type={"limit": {"tif": "Gtc"}},
    reduce_only=False,
    cloid=None            # optional client order ID
)

# Market open (IOC with slippage):
exchange.market_open(
    name="BTC",
    is_buy=True,
    sz=0.1,
    px=None,              # auto-fetches mid price
    slippage=0.05         # 5% slippage tolerance
)

# Market close:
exchange.market_close(
    coin="BTC",
    sz=None,              # None = close entire position
    px=None,
    slippage=0.05
)

# Bulk orders:
exchange.bulk_orders([order_request1, order_request2, ...])

# Cancel:
exchange.cancel(name="BTC", oid=77738308)
exchange.cancel_by_cloid(name="BTC", cloid=cloid_obj)
exchange.bulk_cancel([{"coin": "BTC", "oid": 123}, ...])

# Modify order:
exchange.modify_order(
    oid=77738308,
    name="BTC",
    is_buy=True,
    sz=0.1,
    limit_px=65100.0,
    order_type={"limit": {"tif": "Gtc"}}
)

# Leverage:
exchange.update_leverage(leverage=10, name="BTC", is_cross=True)

# Isolated margin:
exchange.update_isolated_margin(amount=100.0, name="BTC")

# Transfers:
exchange.usd_transfer(amount=100.0, destination="0xADDRESS")
exchange.withdraw_from_bridge(amount=100.0, destination="0xADDRESS")

# Create API wallet (agent):
response, agent_private_key = exchange.approve_agent(name="my_bot")
```

---

## 10. ORDER TYPES SUPPORTED

| Order Type | TIF/Config | Description |
|-----------|------------|-------------|
| Limit GTC | `{"limit": {"tif": "Gtc"}}` | Good-til-canceled |
| Limit IOC | `{"limit": {"tif": "Ioc"}}` | Immediate-or-cancel (market-like) |
| Limit ALO | `{"limit": {"tif": "Alo"}}` | Add liquidity only (post-only) |
| Stop Market | `{"trigger": {"isMarket": true, "triggerPx": "X", "tpsl": "sl"}}` | Stop loss, market fill |
| Stop Limit | `{"trigger": {"isMarket": false, "triggerPx": "X", "tpsl": "sl"}}` | Stop loss, limit fill |
| Take Profit Market | `{"trigger": {"isMarket": true, "triggerPx": "X", "tpsl": "tp"}}` | TP, market fill |
| Take Profit Limit | `{"trigger": {"isMarket": false, "triggerPx": "X", "tpsl": "tp"}}` | TP, limit fill |
| TWAP | Separate `twapOrder` action | Time-weighted average price execution |

**Flags:**
- `reduceOnly` (`r`): Only reduces position, never opens new
- `cloid` (`c`): Client order ID (16-byte hex), for tracking
- `grouping`: `"na"` | `"normalTpsl"` | `"positionTpsl"` for bracket orders

**NOT supported:** FOK (Fill-or-Kill) is not mentioned in the API docs.

---

## 11. KEY DIFFERENCES FROM STRIKE/BINANCE

| Feature | Strike Finance | Hyperliquid |
|---------|---------------|-------------|
| Auth | Ed25519 signing | EIP-712 (Ethereum wallet) |
| Symbol format | `"BTC-PERP"` | `"BTC"` |
| API structure | Multiple REST endpoints | 2 POST endpoints (/info, /exchange) |
| Order ID | String | Integer (oid) |
| Price/Size format | Number | String |
| Market order | Native | IOC limit with slippage |
| WebSocket | Custom protocol | Standard JSON subscribe/unsubscribe |
| Rate limits | Per-endpoint | Weight-based budget (1200/min) |
| SDK | None | Official: `hyperliquid-python-sdk` |

---

## 12. IMPLEMENTATION NOTES FOR BOTSTRIKE

1. **All prices and sizes are STRINGS** in the raw API (SDK handles conversion)
2. **Asset indices** must be resolved from `meta` endpoint at startup
3. **No native market orders** — use IOC limit at slippage price (SDK's `market_open` does this)
4. **Nonces = timestamps in ms** — not sequential
5. **Agent wallets** are strongly recommended for bot security
6. **Dead man's switch** (`scheduleCancel`) is valuable for risk management
7. **Batch operations** available for orders, cancels, and modifications
8. **WebSocket reconnection** needed — no built-in keepalive mentioned
9. **Weight budget** of 1200/min means careful request planning needed
10. **Address budget** of 10k requests initially — increases with trading volume
