# Strike Finance API - Complete Reference

## Base URLs

| Service | Mainnet | Testnet |
|---------|---------|---------|
| Trading / User | `https://api.strikefinance.org` | `https://api-v2-testnet.strikefinance.org` |
| Market Data (REST) | `https://api.strikefinance.org/price` | `https://api-v2-testnet.strikefinance.org/price` |
| Statistics | `https://api.strikefinance.org/stat` | `https://api-v2-testnet.strikefinance.org/stat` |
| Market WS | `wss://api-v2.strikefinance.org/ws/price` | `wss://api-v2-testnet.strikefinance.org/ws/price` |
| User WS | `wss://api-v2.strikefinance.org/ws/user-api` | `wss://api-v2-testnet.strikefinance.org/ws/user-api` |

---

## Authentication (Ed25519 API Wallet)

### Required Headers (for authenticated endpoints)
| Header | Description |
|--------|-------------|
| `X-API-Wallet-Public-Key` | 64 hex chars (32 bytes), raw Ed25519 public key |
| `X-API-Wallet-Signature` | 128 hex chars, Ed25519 signature |
| `X-API-Wallet-Timestamp` | Unix timestamp (seconds) |
| `X-API-Wallet-Nonce` | UUID v4, unique per request |

### Signature Message Format
```
{METHOD}:{PATH}:{TIMESTAMP}:{NONCE}:{BODY_HASH}
```
- METHOD: GET/POST/DELETE (uppercase)
- PATH: Full path with query string
- BODY_HASH: SHA-256 of JSON body (empty string hash for GET)

### Security Rules
- Timestamp must be within +/- 3 minutes of server time
- Nonce must be unique (replay protection)
- SSH key format NOT supported

---

## Rate Limits

Rate limits are returned in the `GET /v2/exchangeInfo` response under `rateLimits`:
- `rateLimitType` (string)
- `interval` (string)
- `intervalNum` (integer)
- `limit` (integer)

WebSocket error code 429 = rate limit exceeded.

---

# COMMON ENDPOINTS

## GET /v2/ping
- **Auth:** None
- **Params:** None
- **Response:** Empty JSON `{}`

## GET /v2/time
- **Auth:** None
- **Params:** None
- **Response:** `{ "serverTime": int64 }` (Unix milliseconds)

## GET /v2/fee-tiers
- **Auth:** None
- **Params:** None
- **Response:**
  ```
  { "feeTiers": [
    { "tier": int, "minVolume": number, "takerRate": number, "makerRate": number }
  ]}
  ```
  - Rates are decimals (0.0005 = 0.05%)
  - Based on trailing 30-day volume (USD)

---

# MARKET DATA ENDPOINTS (base: /price)

## GET /v2/exchangeInfo
- **Auth:** None
- **Params:** None
- **Response:**
  - `timezone` (string)
  - `serverTime` (int64)
  - `rateLimits` (array of RateLimit)
  - `symbols` (array of SymbolInfo):
    - `symbol`, `pair`, `contractType`, `status`
    - `baseAsset`, `quoteAsset`, `marginAsset`
    - `pricePrecision`, `quantityPrecision`, `baseAssetPrecision`, `quotePrecision` (integers)
    - `underlyingType`, `underlyingSubType[]`
    - `settlePlan`, `triggerProtect`, `liquidationFee`
    - `limitTakeBound`, `marketTakeBound`
    - `filters[]`: PRICE_FILTER (maxPrice, minPrice, tickSize), LOT_SIZE (maxQty, minQty, stepSize), etc.
    - `orderType[]`, `timeInForce[]`

## GET /v2/depth (Order Book)
- **Auth:** None
- **Params:**
  - `symbol` (string, required)
  - `limit` (integer, optional, default: 20, max: 1000)
- **Response:**
  - `lastUpdateId` (uint64) - monotonically increasing engine sequence ID
  - `E` (int64) - event time (Unix ms)
  - `T` (int64) - transaction time (Unix ms)
  - `bids` (string[][]) - `[price, quantity]`, highest first
  - `asks` (string[][]) - `[price, quantity]`, lowest first
- **Cache:** 5 seconds server-side (X-Cache header: HIT/MISS)
- **Note:** lastUpdateId can exceed Number.MAX_SAFE_INTEGER; use BigInt

## GET /v2/trades (Recent Trades)
- **Auth:** None
- **Params:**
  - `symbol` (string, required)
  - `limit` (integer, optional, default: 100, max: 1000)
- **Response:** Array of:
  - `id` (int64) - trade ID
  - `price` (string)
  - `qty` (string)
  - `quoteQty` (string)
  - `time` (int64) - Unix ms
  - `isBuyerMaker` (boolean)
- **NOTE:** No pagination tokens or time range params. Returns most recent trades only.

## GET /v2/premiumIndex (Mark Price + Funding Rate)
- **Auth:** None
- **Params:**
  - `symbol` (string, optional - omit for all symbols)
- **Response:**
  - `symbol` (string)
  - `markPrice` (string)
  - `indexPrice` (string)
  - `estimatedSettlePrice` (string)
  - `lastFundingRate` (string)
  - `nextFundingTime` (int64, Unix ms)
  - `interestRate` (string)
  - `time` (int64, Unix ms)

## GET /v2/markPrice
- **Auth:** None
- **Params:**
  - `symbol` (string, optional)
- **Response:**
  - `e`: "markPriceUpdate"
  - `E` (int64) - event time (Unix ms)
  - `s` (string) - symbol
  - `p` (string) - mark price
  - `i` (string) - index price
  - `P` (string) - estimated settle price
  - `r` (string) - funding rate
  - `T` (int64) - next funding time (Unix ms)

## GET /v2/indexPrice
- **Auth:** None
- **Params:**
  - `symbol` (string, optional)
- **Response:**
  - `e`: "indexPriceUpdate"
  - `E` (int64) - event time (Unix ms)
  - `s` (string) - symbol
  - `p` (string) - index price

## GET /v2/ticker/24hr (24h Ticker)
- **Auth:** None
- **Params:**
  - `symbol` (string, optional - omit for all symbols)
- **Response:**
  - `symbol`, `priceChange`, `priceChangePercent`, `weightedAvgPrice`
  - `lastPrice`, `lastQty`, `openPrice`, `highPrice`, `lowPrice`
  - `volume` (base), `quoteVolume`
  - `openTime`, `closeTime` (int64, Unix ms)
  - `firstId`, `lastId` (int64, trade IDs)
  - `count` (int64, trade count)

## GET /v2/ticker/price (Latest Price)
- **Auth:** None
- **Params:**
  - `symbol` (string, optional)
- **Response:**
  - `symbol` (string), `price` (string), `time` (int64)

## GET /v2/ticker/bookTicker (Best Bid/Ask)
- **Auth:** None
- **Params:**
  - `symbol` (string, optional)
- **Response:**
  - `symbol`, `bidPrice`, `bidQty`, `askPrice`, `askQty`, `time` (int64)

## GET /v2/openInterest
- **Auth:** None
- **Params:**
  - `symbol` (string, optional - omit for all)
- **Response:**
  - `symbol` (string)
  - `openInterest` (string) - base-asset units
  - `time` (int64)
- **NOTE:** Current snapshot only. No historical time range support on this endpoint.

---

# MARKET DATA WEBSOCKET

## Connection
- URL: `wss://api-v2.strikefinance.org/ws/price`
- No authentication required
- Server pings every 54s; connection closed if no pong within 60s

## Subscribe/Unsubscribe
```json
{ "method": "subscribe", "channel": "<channel>", "symbol": "BTC-USD", "id": 1 }
{ "method": "unsubscribe", "channel": "<channel>", "symbol": "BTC-USD", "id": 1 }
```

## Keep-Alive
```json
{ "method": "ping", "id": 99 }
// Response: { "method": "pong", "id": 99 }
```

## Channels

### markprice (per symbol, every 3s)
```json
{ "e": "markPriceUpdate", "E": int64, "s": "BTC-USD",
  "p": "94250.50", "i": "94248.00", "P": "0", "r": "0.0001", "T": int64 }
```

### !markprice@arr (all symbols, every 3s)
Same schema, array of all symbols.

### kline_{interval} (per symbol, real-time)
Intervals: 1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 8h, 12h, 1d, 3d, 1w, 1M
```json
{ "e": "kline", "E": int64, "s": "BTC-USD",
  "k": {
    "t": int64, "T": int64, "s": "BTC-USD", "i": "1h",
    "o": "42000.00", "c": "42300.00", "h": "42500.00", "l": "41800.00",
    "v": "150.5", "n": 523, "x": false,
    "q": "6350000.00", "V": "80.2", "Q": "3380000.00"
  }
}
```
Fields: t=open time, T=close time, i=interval, o/c/h/l=OHLC, v=volume, n=trade count, x=is closed, q=quote volume, V=taker buy volume, Q=taker buy quote volume

### miniticker (per symbol, every 1s)
```json
{ "e": "24hrMiniTicker", "E": int64, "s": "BTC-USD",
  "c": "94250.50", "o": "93000.00", "h": "95000.00", "l": "92500.00",
  "v": "15234.567", "q": "1425000000.00" }
```

### !miniticker@arr (all symbols, every 1s)
Array of miniticker events.

### depth (per symbol, real-time)
```json
{ "e": "depthUpdate", "E": int64, "s": "BTC-USD",
  "U": int64, "u": int64,
  "b": [["94249.50", "2.5"]], "a": [["94251.00", "1.8"]] }
```
Qty "0" = remove level. U/u are globally monotonic.

### trade (per symbol, real-time)
```json
{ "e": "trade", "E": int64, "s": "BTC-USD",
  "t": int64, "p": "94250.50", "q": "0.5", "T": int64, "m": false }
```
t=trade ID, m=buyer is maker

## Error Codes
- 400: Invalid channel / Invalid symbol / Symbol required
- 429: Rate limit exceeded

---

# ORDER / TRADING ENDPOINTS (base: api.strikefinance.org)

## POST /v2/order (Create Order)
- **Auth:** Required
- **Body:**
  - `symbol` (string, required)
  - `side` (enum: "buy"|"sell", required)
  - `type` (enum: "limit"|"market"|"stop"|"stop_limit"|"take_profit"|"take_profit_limit", required)
  - `size` (string, required)
  - `client_order_id` (string, optional)
  - `price` (string, required for limit types)
  - `stop_price` (string, for conditional types)
  - `time_in_force` (enum: GTC|IOC|FOK, default: GTC)
  - `working_type` (enum: mark_price|contract_price, default: mark_price)
  - `post_only` (boolean, default: false)
  - `reduce_only` (boolean, default: false)
  - `close_position` (boolean, default: false)
  - `price_protect` (boolean, default: false)
  - `vault_id` (string, optional, vault leader only)
- **Response:**
  - `client_order_id`, `account_id`, `symbol`, `sequence_id` (int64), `message_id`

## GET /v2/order (Get Order by ID)
- **Auth:** Required (or vault_id for public vault)
- **Params:**
  - `symbol` (string, required)
  - `order_id` (int64, optional)
  - `client_order_id` (string, optional)
  - `vault_id` (string, optional)
  - At least one of order_id or client_order_id required
- **Response:** Full Order object (see models below)

## DELETE /v2/order/cancel
- **Auth:** Required
- **Body:**
  - `order_id` (int64, required)
  - `symbol` (string, required)
  - `vault_id` (string, optional)
- **Response:** `order_id`, `symbol`, `sequence_id`, `message_id`

## DELETE /v2/order/cancel-all
- **Auth:** Required
- **Body:**
  - `symbol` (string, optional - empty = all symbols)
  - `vault_id` (string, optional)
- **Response:** `account_id`, `symbol`, `canceled_count` (-1 if async), `sequence_id`, `message_id`

## POST /v2/order/strategy (Bracket Order TP/SL)
- **Auth:** Required
- **Body:**
  - `strategy_id` (string, required)
  - `client_order_id` (string, optional)
  - All primary order fields (symbol, side, type, size, price, etc.)
  - `tp_order` (optional): { client_order_id, type, size, price, stop_price (required), time_in_force, working_type, post_only, price_protect }
  - `sl_order` (optional): same schema
  - At least one of tp_order or sl_order required
- **Response:** `strategy_id`, `primary_client_order_id`, `tp_client_order_id`, `sl_client_order_id`, `account_id`, `symbol`, `sequence_id`, `message_id`

## POST /v2/orders/batch
- **Auth:** Required
- **Body:**
  - `orders` (array of CreateOrderRequest, min 1, required)
  - `vault_id` (string, optional)
- **Response:**
  - `successful` (array of CreateOrderResponse)
  - `failed` (array of { index: int, error: string })
  - Always HTTP 200; check individual results

## POST /v2/order/replace (Atomic Cancel + Create)
- **Auth:** Required
- **Body:**
  - `cancel` (CancelOrderRequest, required)
  - `new_order` (CreateOrderRequest, required)
  - `vault_id` (string, optional)
- **Response:** `{ cancel: CancelOrderResponse, new_order: CreateOrderResponse }`

## POST /v2/order/replace-batch
- **Auth:** Required
- **Body:**
  - `replacements` (array of ReplaceOrderRequest, min 1)
  - `vault_id` (string, optional)
- **Response:** `{ results: [ReplaceOrderResponse] }`
- Entire batch aborts if any cancel fails

## GET /v2/openOrders
- **Auth:** Required (or vault_id for public)
- **Params:**
  - `symbol` (string, optional)
  - `vault_id` (string, optional)
- **Response:** `{ orders: [Order], count: int }`

## POST /v2/leverage
- **Auth:** Required
- **Body:**
  - `symbol` (string, required)
  - `leverage` (integer, required, 1-125)
  - `vault_id` (string, optional)
- **Response:** `{ leverage: int, maxNotionalValue: string, symbol: string }`
- Only affects new positions

## POST /v2/marginMode
- **Auth:** Required
- **Body:**
  - `symbol` (string, required)
  - `marginMode` (enum: "cross"|"isolated", required)
  - `vault_id` (string, optional)
- **Response:** `{ marginMode: string, symbol: string }`
- Cannot change while position open

## POST /v2/isoMargin
- **Auth:** Required
- **Body:**
  - `symbol` (string, required)
  - `amount` (string, required)
  - `modify_type` (enum: "add"|"remove", required)
  - `vault_id` (string, optional)
- **Response:** `{ symbol, sequence_id, message_id }`

---

# Order Object Model (PascalCase in responses)
- `ID` (int64)
- `ClientOrderID` (string)
- `AccountID` (string)
- `Symbol` (string)
- `Strategy` (nullable): { ID: string, IsPrimary: boolean }
- `Side` (enum): buy|sell|none
- `Status` (enum): pending|open|filled|canceled|untriggered|rejected|expired|none
- `Type` (enum): limit|market|stop|stop_limit|take_profit|take_profit_limit
- `OriginType` (enum): original type before trigger
- `AutoCloseType` (enum): liquidation|adl|bankrupt|if_transfer|""
- `TimeInForce` (enum): GTC|IOC|FOK
- `WorkingType` (enum): mark_price|contract_price|none
- `Size`, `Filled`, `Price`, `StopPrice`, `BoundPrice` (strings)
- `PostOnly`, `ReduceOnly`, `ClosePosition`, `PriceProtect` (booleans)
- `CreateTimestamp` (int64, ms) - API receipt
- `EntryTimestamp` (int64, ms) - Engine receipt
- `EventTimestamp` (int64, ms) - Last event
- `CloseReason` (string)

---

# USER / ACCOUNT ENDPOINTS (base: api.strikefinance.org)

## GET /v2/account
- **Auth:** Required (or vault_id)
- **Params:** `vault_id` (optional)
- **Response:**
  - `account_id`, `blockchain`, `blockchain_address`
  - `wallet_balance`, `available_balance`, `unrealized_pnl`
  - `margin_balance`, `total_margin`, `position_initial_margin`, `maintenance_margin`
  - `symbol_settings` (map): { margin_mode: "cross"|"isolated", leverage: int, allow_pre_trade: bool }

## GET /v2/balances
- **Auth:** Required (or vault_id)
- **Params:** `vault_id` (optional)
- **Response:** Array of:
  - `asset`, `walletBalance`, `unrealizedPnl`, `marginBalance`
  - `maintMargin`, `initialMargin`, `positionInitialMargin`, `openOrderInitialMargin`
  - `crossWalletBalance`, `crossUnPnl`, `availableBalance`, `maxWithdrawAmount`
  - `marginAvailable` (bool), `updateTime` (int64, ms)

## GET /v2/portfolio
- **Auth:** Required (or vault_id)
- **Params:** `vault_id` (optional)
- **Response:**
  - `account`: { accountValue, positionValue, availableBalance, allTimePnl, realizedPnl, unrealizedPnl, allTimeVolume, currentPositionSize }
  - `volume` (30-day), `fees` (30-day)
  - `history` (array): [timestamp_ms, accountValue, realizedPnl, unrealizedPnl]
  - `feeTier` (int), `feeTiers[]`: { tier, min_volume, maker_fee, taker_fee }
  - `volume_history[]`: { date: "YYYY-MM-DD", exchange_volume, weighted_maker_volume, weighted_taker_volume }
  - `feeDiscountRate` (number), `isTradingEnabled` (bool)

## GET /v2/positions
- **Auth:** Required (or vault_id)
- **Params:**
  - `symbol` (optional)
  - `position_id` (optional, requires symbol)
  - `vault_id` (optional)
- **Response:**
  - `positions[]`: { symbol, PositionID (int64), Side (long|short|none), Size, EntryPrice, MarginMode (cross|isolated), Leverage (int), IsolatedMargin, upnl, maintenance_margin, bankruptcy_price, liquidation_price }
  - `count` (int)

## GET /v2/closedPositions
- **Auth:** Required (or vault_id)
- **Params:**
  - `symbol` (optional)
  - `startTime` (int64, Unix ms, optional)
  - `endTime` (int64, Unix ms, optional)
  - `limit` (int, default: 100, max: 1000)
  - `vault_id` (optional)
- **Response:**
  - `positions[]`: { symbol, position_id, side (long|short), size, entry_price, exit_price, realized_pnl, margin_mode, leverage, opened_at (int64 ms), closed_at (int64 ms) }
  - `count` (int)

---

# HISTORY ENDPOINTS (base: api.strikefinance.org)

All use cursor-based pagination. All time filters in Unix milliseconds.

## GET /v2/history/order
- **Auth:** Required (or vault_id)
- **Params:**
  - `symbol` (string, optional)
  - `status` (int, optional): 2=open, 3=filled, 4=canceled, 5=untriggered, 6=rejected, 7=expired
  - `order_id` (int64, optional)
  - `startTime`, `endTime` (int64, Unix ms, optional)
  - `limit` (int, default: 100, max: 1000)
  - `fromOrderID` (int64, pagination cursor)
  - `vault_id` (optional)
- **Response:**
  - `orders[]`: { order_id, client_order_id, symbol, side, type, status, price, size, filled, created_at (ms), updated_at (ms) }
  - `count` (int)

## GET /v2/history/fill
- **Auth:** Required (or vault_id)
- **Params:**
  - `symbol` (string, optional)
  - `order_id` (int64, optional)
  - `startTime`, `endTime` (int64, Unix ms, optional)
  - `limit` (int, default: 500, max: 1000)
  - `fromId` (int64, pagination cursor)
  - `vault_id` (optional)
- **Response:**
  - `fills[]`: { id, order_id, symbol, side, price, qty, quote_qty, commission, commission_asset, realized_pnl, is_maker (bool), time (ms), auto_close_type (nullable: liquidation|adl|bankrupt|if_transfer) }
  - `count` (int)

## GET /v2/history/funding
- **Auth:** Required (or vault_id)
- **Params:**
  - `symbol` (string, optional)
  - `startTime`, `endTime` (int64, Unix ms, optional)
  - `limit` (int, default: 500, max: 1000)
  - `fromId` (int64, pagination cursor)
  - `vault_id` (optional)
- **Response:**
  - `funding[]`: { id, symbol, income (positive=received, negative=paid), asset, time (ms) }
  - `count` (int)

## GET /v2/history/transaction
- **Auth:** Required (or vault_id)
- **Params:**
  - `type` (string, comma-separated or repeated): 1=deposit, 2=withdraw, 3=fee, 4=realized_pnl, 5=liquidation
  - `status` (int, optional): 1=pending, 2=completed, 3=pending_settlement, 4=settled, 5=failed, 6=cancelled
  - `startTime`, `endTime` (int64, Unix ms, optional)
  - `limit` (int, default: 100, max: 1000)
  - `fromId` (int64, pagination cursor)
  - `vault_id` (optional)
- **Response:**
  - `transactions[]`: { id, type (deposit|withdraw|fee|realized_pnl|liquidation), status, amount, asset, time (ms) }
  - `count` (int)

---

# USER WEBSOCKET

## Connection
- URL: `wss://api-v2.strikefinance.org/ws/user-api`
- Auth required before subscribing

## Authentication
```json
{ "method": "session.logon", "apiKey": "<public_key>", "signature": "<ed25519_sig>", "timestamp": "<unix_seconds>" }
```
Signature signs: `session.logon:{timestamp}:{apiKey}`
Timestamp must be within 3 minutes of server time.

## Subscribe
```json
{ "method": "subscribe", "channel": "userstream", "account_id": "<ACCOUNT_ID>", "id": 1 }
```

## Event Types

### ACCOUNT_UPDATE
Triggered by: balance changes, position updates, deposits, withdrawals, funding fees.
Contains balance objects and position objects.

### ORDER_TRADE_UPDATE
Triggered by: order lifecycle (NEW, TRADE, CANCELED, REJECTED, EXPIRED).
Contains full order details.

### Vault Streams (Public, no auth)
Subscribe by vault_id or vault_account_id for same events.

## Error Codes: 400, 401, 403, 404

---

# PLATFORM STATISTICS ENDPOINTS (base: api.strikefinance.org/stat)

All cached 3 minutes server-side unless noted.

## GET /v1/dashboard/summary
- **Params:** None
- **Response:** { total_users, total_volume_usd, total_deposits_usd, total_withdrawals_usd, total_liquidated_usd, total_revenue_usd, open_interest_usd }

## GET /v1/dashboard/volumes
- **Params:** None
- **Response:** CompactSeriesWrapper - tuples [timestamp_ms, symbol, value]

## GET /v1/dashboard/open-interest
- **Params:** None
- **Response:** CompactSeriesWrapper - tuples [timestamp_ms, symbol, value]

## GET /v1/dashboard/annualized-funding-rate
- **Params:** None
- **Response:** CompactSeriesWrapper - tuples [timestamp_ms, symbol, value]

## GET /v1/dashboard/liquidations
- **Params:** None
- **Response:** CompactSeriesWrapper - tuples [timestamp_ms, symbol, value]

## GET /v1/dashboard/users
- **Params:** None
- **Response:** { daily_active_users: [[ts, count]], new_users: [[ts, count]], unique_traders_by_coin: [[ts, symbol, count]] }

## GET /v1/dashboard/trades
- **Params:** None
- **Response:** CompactSeriesWrapper - tuples [timestamp_ms, symbol, value]

## GET /v1/dashboard/transfers
- **Params:** None
- **Response:** { deposits: [[ts, blockchain, amount]], withdrawals: [[ts, blockchain, amount]] }

## GET /v1/dashboard/revenue
- **Params:** None
- **Response:** CompactSeriesWrapper - tuples [timestamp_ms, symbol, value]

## GET /v1/dashboard/treasury
- **Params:** None
- **Response:** { columns: [...], data: [[...]] }

## GET /v1/stats/account/{account_id}
- **Params:** account_id (path, required)
- **Response:**
  - `account_id`, `address`, `account_value`
  - `realized_pnl`: { 24h, 7d, 30d, all_time }
  - `unrealized_pnl`
  - `volume`: { 24h, 7d, 30d, all_time }
  - `trading_fees`, `funding_fees`, `liquidations`

## GET /v1/leaderboard
- **Params:** None
- **Response:** { updated_at, leaderboards: { realized_pnl, volume, trading_fees } }
  - Each: { entries: [[address, account_id, rank, account_value, [24h_stats], [7d_stats], [30d_stats], [all_time_stats]]] }
  - Stats: [pnl_total, pnl_realized, volume, trading_fees, funding_fees, liquidation, roi]

## GET /v1/leaderboard/rank/{account_id}
- **Params:**
  - `account_id` (path, required)
  - `type` (query, optional): realized_pnl (default)|total_pnl|volume|trading_fees
  - `period` (query, optional): 24h|7d|30d|all_time (default)
- **Response:** { account_id, type, period, rank (1-based), total }

## GET /v1/stats/daily
- **Params:**
  - `start_date` (optional, YYYY-MM-DD, default: 30 days ago)
  - `end_date` (optional, YYYY-MM-DD, default: today)
  - `symbol` (optional, default: ALL)
- **Response:** { start_date, end_date, symbol, data: [DailyStatsEntry] }
  - DailyStatsEntry: { date, symbol, volume, volume_long, volume_short, realized_pnl, fee, revenue, funding_fee, notional_liquidated, liquidation_fee, trades, trades_long, trades_short }

## GET /v1/stats/live-positions
- **Params:** `symbol` (optional)
- **Response:** { columns: [...], data: [[...]] }

## POST /v1/stats/daily/rerun
- **Params:** `date` (query, required, YYYY-MM-DD)
- Admin ETL endpoint

## POST /v1/stats/open-interest/snapshot
- **Params:** None
- Admin snapshot trigger

---

# COIN HISTORY ENDPOINTS (base: api.strikefinance.org/stat)

All return HistoryResponse: { symbol, interval, days, columns: [...], data: [[...]] }

## GET /v1/stats/coin/history/open-interest
- **Params:**
  - `symbol` (required)
  - `interval` (optional): 10m|15m|30m|1h|12h|1d (default: 10m)

## GET /v1/stats/coin/history/funding
- **Params:**
  - `symbol` (required)
  - `days` (optional, 1-90, default: 30)
- Returns 8-hour interval funding rates

## GET /v1/stats/coin/history/basis
- **Params:**
  - `symbol` (required)
  - `interval` (optional): 10m|15m|30m|1h|12h|1d (default: 10m)
- Returns mark price, index price, basis (mark - index)

## GET /v1/stats/coin/history/spread
- **Params:**
  - `symbol` (required)
  - `interval` (optional): 10m|15m|30m|1h|12h|1d (default: 10m)
- Returns bid-ask spread and ratio

## GET /v1/stats/coin/history/oi-marketcap-ratio
- **Params:**
  - `symbol` (required)
  - `interval` (optional): 10m|15m|30m|1h|12h|1d (default: 10m)
- Returns OI, market cap, price

## GET /v1/stats/coin/history/long-short-ratio
- **Params:**
  - `symbol` (required)
  - `interval` (optional): 10m|15m|30m|1h|12h|1d (default: 10m)

## GET /v1/stats/coin/history/top-trader-long-short-ratio
- **Params:**
  - `symbol` (required)
  - `interval` (optional): 10m|15m|30m|1h|12h|1d (default: 10m)
- Top 20% traders by position size

---

# COIN STATISTICS ENDPOINTS (base: api.strikefinance.org/stat)

## GET /v1/stats/coin/liquidation-map
- **Params:**
  - `symbol` (required)
  - `bins` (optional, 10-1000, default: 200)
- **Response:** { symbol, version, snapshot_at (int64 ms), bins, columns, data: [[price, long_base, short_base, cum_long_base, cum_short_base]] }

## GET /v1/stats/coin/funding-rate-comparison
- **Params:** None
- **Response:** { data: [{ symbol, strike, bybit, binance, lighter, okx, hyperliquid }] }
  - Each exchange: { rate (string decimal), timestamp (unix seconds) } or null

---

# VAULT ENDPOINTS

## Public Vault Endpoints (base: api.strikefinance.org or api.strike.finance)

### GET /v2/vaults (List Vaults)
- **Auth:** None
- **Params:**
  - `limit` (int, 1-500, default: 100)
  - `offset` (int, default: 0)
  - `period` (enum: 24h|7d|30d|6m|1y|all, default: 30d)
  - `leader_account_id` (string, optional)
  - `depositor_account_id` (string, optional)
  - `type` (enum: user|protocol, optional)
  - `is_verified` (string: "true"|"false", optional)
  - `status` (enum: active|paused|closed, default: active)
- **Response:** { vaults: [VaultInfo], count, limit, offset }

### GET /v2/vault/{id} (Single Vault)
- **Auth:** None
- **Params:** id (UUID, path)
- **Response:** VaultInfoResponse (see model below)

### GET /v2/vault/{id}/history
- **Auth:** None
- **Params:**
  - `id` (UUID, path)
  - `limit` (1-500, default: 100)
  - `offset` (default: 0)
  - `type` (deposit|withdrawal, optional)
  - `status` (completed|pending|cancelled|failed, optional)
- **Response:** { vault_id, history: [VaultHistoryEntry], count, limit, offset }

### GET /v2/vault/{id}/portfolio
- **Auth:** None
- **Params:**
  - `id` (UUID, path)
  - `period` (24h|7d|30d|6m|1y|all, default: 30d)
- **Response:** { vault_id, total_value_locked, apr, all_time_pnl, return_24h, period_return, volume, depositors, fee, fee_earned, sharpe_ratio, max_drawdown, created_at, history: [[ts, tvl, pnl, feesEarned, depositors]] }

### GET /v2/vault/{id}/depositors
- **Auth:** None
- **Params:**
  - `id` (UUID, path)
  - `limit` (1-500, default: 100), `offset` (default: 0)
- **Response:** { depositors: [{ address, deposited, current_equity, share_percentage, pnl, deposited_since }], count, limit, offset }

## Authenticated Vault Endpoints

### GET /v2/vault/position
- **Auth:** Required
- **Params:** `vault_id` (UUID, required)
- **Response:** { vault_id, shares, shares_locked, available_shares, total_deposited, total_withdrawn, avg_entry_price, current_value, net_contribution, unrealized_pnl, total_pnl }

### GET /v2/vault/positions
- **Auth:** Required
- **Params:** None
- **Response:** { positions: [UserVaultPositionResponse] }

### GET /v2/vault/history
- **Auth:** Required
- **Params:**
  - `vault_id` (UUID, optional)
  - `limit` (1-500, default: 100), `offset` (default: 0)
  - `type` (deposit|withdrawal, optional)
  - `status` (completed|pending|cancelled|failed, optional)
- **Response:** { history: [VaultHistoryEntry], count, limit, offset }

### GET /v2/vault/my-deposits/history
- **Auth:** Required
- **Params:** `vault_id` (UUID, optional)
- **Response:** { history: [[timestamp, equity, pnl]] }

## Vault Leader Endpoints
Leaders use the standard trading endpoints with `vault_id` in body/query:
- POST /v2/order, POST /v2/orders/batch, DELETE /v2/order/cancel, DELETE /v2/order/cancel-all
- POST /v2/order/replace, POST /v2/order/replace-batch, POST /v2/order/strategy
- POST /v2/leverage, POST /v2/marginMode, POST /v2/isoMargin
- GET /v2/account, /v2/balances, /v2/portfolio, /v2/positions, /v2/closedPositions
- GET /v2/openOrders, /v2/order, /v2/order/strategy, /v2/trades
- GET /v2/history/order, /v2/history/fill, /v2/history/funding, /v2/history/transaction

WebSocket: `{ "method": "subscribe", "channel": "userstream", "vault_id": "xxx", "id": 1 }`

## VaultInfoResponse Model
- `vault_id` (UUID), `type` (protocol|user), `name`, `description`
- `account_id`, `leader_account_id`, `leader_commission_bps` (int, basis points)
- `total_shares`, `share_price`, `equity` (decimal strings)
- `min_leader_share_pct` (int), `lockup_days` (int), `min_deposit` (decimal string)
- `allow_proportional_close` (bool), `status` (active|paused|closed), `is_verified` (bool)
- `created_at` (int64 ms)
- `apr`, `all_time_pnl`, `return_24h`, `period_return` (strings)
- `depositors` (int), `fee_earned` (decimal string), `sharpe_ratio` (string, nullable)

---

# DATA PIPELINE ANSWERS

## Historical Trades
- **REST:** `GET /v2/trades` on /price base - returns last N trades only (max 1000). NO pagination, NO time range.
- **Authenticated:** `GET /v2/history/fill` - user's own fills with `startTime`, `endTime`, `fromId` cursor, max 1000 per page.
- **WebSocket:** Subscribe to `trade` channel for real-time stream.
- **Recommendation:** For historical trade data, use the `trade` WebSocket channel to collect in real-time, or use `/v2/history/fill` for your own account's fills.

## Historical Klines / Candlesticks
- **REST:** NO REST endpoint for historical klines.
- **WebSocket:** Subscribe to `kline_{interval}` channel (1m to 1M intervals). Real-time only.
- **Recommendation:** Collect via WebSocket and store locally. No backfill endpoint exists.

## Funding Rate History
- **Current:** `GET /v2/premiumIndex` - current funding rate snapshot
- **Historical:** `GET /v1/stats/coin/history/funding` (stat base) - params: `symbol` (required), `days` (1-90, default 30). Returns 8-hour intervals.
- **Per-account:** `GET /v2/history/funding` - your funding payments with time range + pagination.

## Orderbook Snapshots
- **REST:** `GET /v2/depth` - current snapshot, up to 1000 levels per side. Cached 5s.
- **WebSocket:** `depth` channel for real-time incremental updates.
- **No historical orderbook endpoint.**

## Mark Price / Index Price History
- **Current:** `GET /v2/markPrice`, `GET /v2/indexPrice`, `GET /v2/premiumIndex`
- **Historical:** `GET /v1/stats/coin/history/basis` - returns mark price, index price, and basis over time with configurable interval (10m to 1d).
- **WebSocket:** `markprice` channel (every 3s).

## 24h Ticker Data
- **REST:** `GET /v2/ticker/24hr` - full 24h rolling stats (OHLCV, trade count, price change).
- **REST:** `GET /v2/ticker/price` - last price only.
- **REST:** `GET /v2/ticker/bookTicker` - best bid/ask.
- **WebSocket:** `miniticker` channel (every 1s).

## Open Interest History
- **Current:** `GET /v2/openInterest` - snapshot only.
- **Historical:** `GET /v1/stats/coin/history/open-interest` - with configurable interval (10m to 1d).
- **Dashboard:** `GET /v1/dashboard/open-interest` - daily aggregated series.

## Historical Data Export
- **No dedicated bulk export endpoint.**
- Use cursor-based pagination on history endpoints (fill, order, funding, transaction).
- Use stat endpoints for aggregated historical series (daily stats, coin history).
- For tick-level data: WebSocket collection is the only path.
