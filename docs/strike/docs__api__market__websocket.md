<!-- source: https://docs.strikefinance.org/api/market/websocket.md -->
> For the complete documentation index, see [llms.txt](https://docs.strikefinance.org/llms.txt). Markdown versions of documentation pages are available by appending `.md` to page URLs; this page is available as [Markdown](https://docs.strikefinance.org/api/market/websocket.md).

# Websocket

## Strike Perpetuals - Market WebSocket API

Real-time market price and market streams for Strike Perpetuals trading platform.

### Connection

| Environment | URL |
| ----------- | ------------------------------------------------- |
| Testnet | `wss://api-v2-testnet.strikefinance.org/ws/price` |
| Mainnet | `wss://api.strikefinance.org/ws/price` |

### Authentication

No authentication required. All market data streams are public.

***

### Subscription Protocol

#### Subscribe Request

```json
{
"method": "subscribe",
"channel": "<channel_name>",
"symbol": "BTC-USD",
"id": 1
}
```

| Field | Type | Required | Description |
| ------- | ------- | ----------- | ------------------------------------- |
| method | string | Yes | Must be `subscribe` |
| channel | string | Yes | Channel name (see Available Channels) |
| symbol | string | Conditional | Required for symbol-specific channels |
| id | integer | No | Request ID for response correlation |

#### Unsubscribe Request

```json
{
"method": "unsubscribe",
"channel": "<channel_name>",
"symbol": "BTC-USD",
"id": 2
}
```

#### Success Response

```json
{
"result": null,
"id": 1
}
```

#### Error Response

```json
{
"error": {
"code": 400,
"msg": "Invalid channel"
},
"id": 1
}
```

***

### Keep-Alive

#### Client Ping

Send periodic pings to maintain connection:

```json
{
"method": "ping",
"id": 99
}
```

#### Server Pong

```json
{
"method": "pong",
"id": 99
}
```

#### Timeouts

* Server sends WebSocket ping frames every **54 seconds**
* Connection closed if no pong response within **60 seconds**

***

### Available Channels

| Channel | Symbol Required | Update Frequency | Description |
| ------------------ | --------------- | ---------------- | ---------------------------------- |
| `markprice` | Yes | 3 seconds | Mark price updates for a symbol |
| `!markprice@arr` | No | 3 seconds | Mark price updates for all symbols |
| `kline_{interval}` | Yes | Real-time | Candlestick data |
| `miniticker` | Yes | 1 second | Mini ticker for a symbol |
| `!miniticker@arr` | No | 1 second | Mini ticker for all symbols |
| `depth` | Yes | Real-time | Order book updates |
| `trade` | Yes | Real-time | Trade stream |

#### Kline Intervals

Available intervals for `kline_{interval}` channel:

| Minutes | Hours | Days/Weeks/Months |
| ------------------------------ | ----------------------------------- | ---------------------- |
| `1m`, `3m`, `5m`, `15m`, `30m` | `1h`, `2h`, `4h`, `6h`, `8h`, `12h` | `1d`, `3d`, `1w`, `1M` |

**Example:** `kline_1h` for hourly candlesticks

***

### Channel Details

#### Mark Price (`markprice`)

Real-time mark price and funding rate updates.

**Subscribe:**

```json
{
"method": "subscribe",
"channel": "markprice",
"symbol": "BTC-USD",
"id": 1
}
```

**Event:**

```json
{
"e": "markPriceUpdate",
"E": 1704067200000,
"s": "BTC-USD",
"p": "94250.50",
"i": "94248.00",
"P": "0",
"r": "0.0001",
"T": 1704070800000
}
```

| Field | Type | Description |
| ----- | ------ | ----------------------------- |
| e | string | Event type: `markPriceUpdate` |
| E | int64 | Event time (ms) |
| s | string | Symbol |
| p | string | Mark price |
| i | string | Index price |
| P | string | Estimated settle price |
| r | string | Funding rate |
| T | int64 | Next funding time (ms) |

***

#### All Mark Prices (`!markprice@arr`)

Mark price updates for all symbols in a single stream.

**Subscribe:**

```json
{
"method": "subscribe",
"channel": "!markprice@arr",
"id": 1
}
```

**Event:**

```json
[
{
"e": "markPriceUpdate",
"E": 1704067200000,
"s": "BTC-USD",
"p": "94250.50",
"i": "94248.00",
"P": "0",
"r": "0.0001",
"T": 1704070800000
},
{
"e": "markPriceUpdate",
"E": 1704067200000,
"s": "ETH-USD",
"p": "3350.25",
"i": "3349.50",
"P": "0",
"r": "0.00008",
"T": 1704070800000
}
]
```

***

#### Kline / Candlestick (`kline_{interval}`)

Real-time candlestick updates.

**Subscribe:**

```json
{
"method": "subscribe",
"channel": "kline_1h",
"symbol": "BTC-USD",
"id": 1
}
```

**Event:**

```json
{
"e": "kline",
"E": 1704067200000,
"s": "BTC-USD",
"k": {
"t": 1704067200000,
"T": 1704070799999,
"s": "BTC-USD",
"i": "1h",
"o": "42000.00",
"c": "42300.00",
"h": "42500.00",
"l": "41800.00",
"v": "150.5",
"n": 523,
"x": false,
"q": "6350000.00",
"V": "80.2",
"Q": "3380000.00"
}
}
```

| Field | Type | Description |
| ----- | ------- | ---------------------------- |
| e | string | Event type: `kline` |
| E | int64 | Event time (ms) |
| s | string | Symbol |
| k.t | int64 | Kline open time (ms) |
| k.T | int64 | Kline close time (ms) |
| k.s | string | Symbol |
| k.i | string | Interval |
| k.o | string | Open price |
| k.c | string | Close price |
| k.h | string | High price |
| k.l | string | Low price |
| k.v | string | Base asset volume |
| k.n | integer | Number of trades |
| k.x | boolean | Is kline closed? |
| k.q | string | Quote asset volume |
| k.V | string | Taker buy base asset volume |
| k.Q | string | Taker buy quote asset volume |

***

#### Mini Ticker (`miniticker`)

24-hour rolling window mini ticker.

**Subscribe:**

```json
{
"method": "subscribe",
"channel": "miniticker",
"symbol": "BTC-USD",
"id": 1
}
```

**Event:**

```json
{
"e": "24hrMiniTicker",
"E": 1704067200000,
"s": "BTC-USD",
"c": "94250.50",
"o": "93000.00",
"h": "95000.00",
"l": "92500.00",
"v": "15234.567",
"q": "1425000000.00"
}
```

| Field | Type | Description |
| ----- | ------ | ---------------------------- |
| e | string | Event type: `24hrMiniTicker` |
| E | int64 | Event time (ms) |
| s | string | Symbol |
| c | string | Close price (last price) |
| o | string | Open price (24h ago) |
| h | string | High price (24h) |
| l | string | Low price (24h) |
| v | string | Base asset volume (24h) |
| q | string | Quote asset volume (24h) |

***

#### All Mini Tickers (`!miniticker@arr`)

Mini ticker updates for all symbols in a single stream.

**Subscribe:**

```json
{
"method": "subscribe",
"channel": "!miniticker@arr",
"id": 1
}
```

**Event:**

```json
[
{
"e": "24hrMiniTicker",
"E": 1704067200000,
"s": "BTC-USD",
"c": "94250.50",
"o": "93000.00",
"h": "95000.00",
"l": "92500.00",
"v": "15234.567",
"q": "1425000000.00"
},
{
"e": "24hrMiniTicker",
"E": 1704067200000,
"s": "ETH-USD",
"c": "3350.25",
"o": "3300.00",
"h": "3400.00",
"l": "3280.00",
"v": "45678.123",
"q": "153000000.00"
}
]
```

***

#### Order Book Depth (depth)

Real-time order book updates.

Subscribe

```json
{
"method": "subscribe",
"channel": "depth",
"symbol": "BTC-USD",
"id": 1
}
```

Event

```json
{
"e": "depthUpdate",
"E": 1704067200000,
"s": "BTC-USD",
"U": 128742991,
"u": 128742991,
"b": [
["94249.50", "2.5"],
["94248.00", "1.2"],
["94247.00", "0"]
],
"a": [
["94251.00", "1.8"],
["94252.50", "3.0"]
]
}
```

Fields

| Field | Type | Description |
| ----- | ------ | ----------------------------------------- |
| `e` | string | Event type: `depthUpdate` |
| `E` | int64 | Event time (ms) |
| `s` | string | Symbol |
| `U` | uint64 | Order book update ID (engine sequence ID) |
| `u` | uint64 | Order book update ID (engine sequence ID) |
| `b` | array | Bid updates: `[price, quantity]` |
| `a` | array | Ask updates: `[price, quantity]` |

Note: A quantity of `"0"` means the price level should be removed from the order book.

Note: `U`/`u` are globally monotonic update IDs, not per-symbol contiguous IDs.

Note: For current depth events, `U` and `u` are typically equal for a single event.

#### How to Maintain a Local Order Book

1. Get a depth snapshot via REST API: `GET /price/v2/depth?symbol=BTC-USD&limit=1000`.
2. Subscribe to the depth WebSocket stream.
3. Buffer incoming depth events while snapshot loads.
4. Set local `lastUpdateId = snapshot.lastUpdateId`.
5. For each depth event:
* Parse `U/u/lastUpdateId` as `uint64`.
* If `u <= lastUpdateId`, drop as stale/duplicate.
* Otherwise apply `b/a` updates (`"0"` quantity removes the level).
* Set `lastUpdateId = u`.
6. Do not require per-symbol contiguous IDs (`u != lastUpdateId + 1` can be valid).

***

#### Trade Stream (`trade`)

Real-time trade executions.

**Subscribe:**

```json
{
"method": "subscribe",
"channel": "trade",
"symbol": "BTC-USD",
"id": 1
}
```

**Event:**

```json
{
"e": "trade",
"E": 1704067200000,
"s": "BTC-USD",
"t": 123456789,
"p": "94250.50",
"q": "0.5",
"T": 1704067200000,
"m": false
}
```

| Field | Type | Description |
| ----- | ------- | ------------------------------------------------------------------------- |
| e | string | Event type: `trade` |
| E | int64 | Event time (ms) |
| s | string | Symbol |
| t | int64 | Trade ID |
| p | string | Price |
| q | string | Quantity |
| T | int64 | Trade time (ms) |
| m | boolean | Is buyer the market maker? (true = sell aggressor, false = buy aggressor) |

***

### Example: Multiple Subscriptions

You can subscribe to multiple channels over a single WebSocket connection:

```json
{"method": "subscribe", "channel": "markprice", "symbol": "BTC-USD", "id": 1}
{"method": "subscribe", "channel": "depth", "symbol": "BTC-USD", "id": 2}
{"method": "subscribe", "channel": "trade", "symbol": "BTC-USD", "id": 3}
{"method": "subscribe", "channel": "kline_1h", "symbol": "ETH-USD", "id": 4}
```

Each subscription will receive its own success response with the corresponding `id`.

***

### Error Codes

| Code | Message | Description |
| ---- | ------------------- | ----------------------------------- |
| 400 | Invalid channel | Unknown or malformed channel name |
| 400 | Invalid symbol | Unknown trading pair symbol |
| 400 | Symbol required | Channel requires a symbol parameter |
| 429 | Rate limit exceeded | Too many subscriptions or messages |