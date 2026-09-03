<!-- source: https://docs.strikefinance.org/api/user/websocket.md -->
> For the complete documentation index, see [llms.txt](https://docs.strikefinance.org/llms.txt). Markdown versions of documentation pages are available by appending `.md` to page URLs; this page is available as [Markdown](https://docs.strikefinance.org/api/user/websocket.md).

# Websocket

Real-time account updates via WebSocket. Receive order updates, trade executions, balance changes, position updates, vault events, credit events, and strategy updates for your account.

### General Information

**Base endpoints:**

| Path | Auth method | Used by |
| -------------- | ------------------------------------- | ------------------------------- |
| `/ws/user-api` | `session.logon` (API wallet, Ed25519) | Bots / programmatic API clients |

Example hosts: `wss://api-v2.strikefinance.org` (mainnet), `wss://api-v2-testnet.strikefinance.org` (testnet).

* All user data streams require authentication before subscribing (except vault streams, which are public).
* A connection may subscribe to any userstreams it is authorized to access.
* The server sends WebSocket ping frames every **54 seconds**; connections that do not respond with a pong within **60 seconds** are disconnected.
* **Message framing:** The server may deliver multiple JSON events in a single WebSocket text frame, separated by newline (`\n`) characters. Clients must split incoming messages on newline boundaries and parse each JSON object individually. Treating the entire frame as a single JSON value will fail when multiple events are batched together, which commonly occurs during bursts of activity (e.g., batch order operations).
* All numeric values are strings to preserve decimal precision.
* All timestamps are in milliseconds (Unix epoch × 1000).
* Symbols use hyphen-separated format (e.g., `BTC-USDT`).

> **Schema note:** Event payloads mirror the Binance USDⓈ-M Futures user data stream schema (single-letter field keys like `e`, `E`, `s`, `B`, `P`, `x`, `X`). If you have an existing Binance Futures parser, most of it maps directly. Field-by-field tables are provided below for every event.

***

### Authentication

Authentication is done with an **API wallet**: an Ed25519 key pair registered to your account. You prove ownership by signing the logon request with the wallet's private key.

> **Endpoint:** API-wallet `session.logon` is served on the `/ws/user-api` path, e.g. `wss://api-v2.strikefinance.org/ws/user-api` (mainnet) or `wss://api-v2-testnet.strikefinance.org/ws/user-api` (testnet). If the server replies `authentication not available on this endpoint`, you are connected to the wrong path.

**Request:**

```json
{
"method": "session.logon",
"id": 1,
"params": {
"apiKey": "<PUBLIC_KEY_HEX>",
"signature": "<SIGNATURE_HEX>",
"timestamp": 1705000000000
}
}
```

**Response:**

```json
{
"id": 1,
"status": 200,
"result": {
"authenticated": true,
"account_id": "0199dc01-720d-7314-84d3-b3c3a102a9f9"
}
}
```

**What `apiKey`, `timestamp`, and `signature` are**

| Param | What it is |
| ----------- | --------------------------------------------------------------------------------------------------------------- |
| `apiKey` | Your API wallet's **public** key, hex-encoded (the same value registered server-side). |
| `timestamp` | Current time in **milliseconds** (`Date.now()`). Replay guard — the server rejects stale timestamps. |
| `signature` | Ed25519 signature of a canonical message string (below), signed with the wallet's **private** key, hex-encoded. |

**How to build `signature` (exact scheme)**

The signed message is the literal string:

```
session.logon:<timestamp>:<apiKey>
```

i.e. the three parts joined with colons — the method name `session.logon`, the same `timestamp` you send in `params`, and the `apiKey` (public key hex). Then:

```
signature = hex( Ed25519_Sign(privateKey, "session.logon:<timestamp>:<apiKey>") )
```

Reference Implementation:

{% tabs %}
{% tab title="Python" %}

```python
import asyncio
import json
import time

import websockets
from nacl.signing import SigningKey

WS_URL = "wss://api-v2-testnet.strikefinance.org/ws/user-api"
PRIVATE_KEY_HEX = "<PRIVATE_KEY_HEX>" # 64-char seed or 128-char full key
ACCOUNT_ID = "<ACCOUNT_ID>"

def make_keys(private_key_hex: str):
seed = bytes.fromhex(private_key_hex)[:32] # first 32 bytes = seed
signing_key = SigningKey(seed)
api_key = signing_key.verify_key.encode().hex() # public key hex
return signing_key, api_key

def build_logon(signing_key: SigningKey, api_key: str) -> dict:
timestamp = int(time.time() * 1000)
message = f"session.logon:{timestamp}:{api_key}".encode()
signature = signing_key.sign(message).signature.hex()
return {
"method": "session.logon",
"id": 1,
"params": {"apiKey": api_key, "signature": signature, "timestamp": timestamp},
}

def handle_event(event: dict) -> None:
e = event.get("e")
if e == "ORDER_TRADE_UPDATE":
print("order/trade:", event.get("data"))
elif e == "ACCOUNT_UPDATE":
print("account:", event.get("data"))
elif e == "strategyUpdate":
print("strategy:", event.get("data"))
elif e == "error":
print("error:", event.get("error"))
else:
print("event:", event)

async def keepalive(ws):
while True:
await asyncio.sleep(30)
await ws.send(json.dumps({"method": "ping", "id": "hb"}))

async def run():
signing_key, api_key = make_keys(PRIVATE_KEY_HEX)

async for ws in websockets.connect(WS_URL, ping_interval=None):
try:
# 1) logon
await ws.send(json.dumps(build_logon(signing_key, api_key)))
logon_resp = json.loads(await ws.recv())
if logon_resp.get("status") != 200:
raise RuntimeError(f"logon rejected: {logon_resp}")
print("authenticated:", logon_resp["result"]["account_id"])

# 2) subscribe
await ws.send(json.dumps({
"method": "subscribe", "channel": "userstream",
"account_id": ACCOUNT_ID, "id": 2,
}))

# 3) read loop + keepalive
ka = asyncio.create_task(keepalive(ws))
try:
async for frame in ws:
for line in frame.split("\n"): # newline-delimited batching
line = line.strip()
if not line:
continue
msg = json.loads(line)
if msg.get("method") == "pong":
continue
if msg.get("id") is not None and msg.get("result", "x") is None:
continue # subscribe/logon ack
handle_event(msg)
finally:
ka.cancel()
except websockets.ConnectionClosed:
print("disconnected, reconnecting…")
continue

if __name__ == "__main__":
asyncio.run(run())
```

{% endtab %}

{% tab title="TypeScript" %}

```typescript
import WebSocket from "ws";
import nacl from "tweetnacl";

const WS_URL = "wss://api-v2-testnet.strikefinance.org/ws/user-api";
const PRIVATE_KEY_HEX = "<PRIVATE_KEY_HEX>"; // 64-char seed or 128-char full key
const ACCOUNT_ID = "<ACCOUNT_ID>";

function makeKeys(privateKeyHex: string) {
const seed = Buffer.from(privateKeyHex, "hex").subarray(0, 32); // first 32 bytes
const keyPair = nacl.sign.keyPair.fromSeed(seed);
const apiKey = Buffer.from(keyPair.publicKey).toString("hex");
return { secretKey: keyPair.secretKey, apiKey };
}

function buildLogon(secretKey: Uint8Array, apiKey: string) {
const timestamp = Date.now();
const message = `session.logon:${timestamp}:${apiKey}`;
const signature = Buffer.from(
nacl.sign.detached(Buffer.from(message), secretKey),
).toString("hex");
return {
method: "session.logon",
id: 1,
params: { apiKey, signature, timestamp },
};
}

function handleEvent(event: any) {
switch (event.e) {
case "ORDER_TRADE_UPDATE":
console.log("order/trade:", event.data);
break;
case "ACCOUNT_UPDATE":
console.log("account:", event.data);
break;
case "strategyUpdate":
console.log("strategy:", event.data);
break;
case "error":
console.error("error:", event.error);
break;
default:
console.log("event:", event);
}
}

function connect() {
const { secretKey, apiKey } = makeKeys(PRIVATE_KEY_HEX);
const ws = new WebSocket(WS_URL);
let authed = false;
let pingTimer: NodeJS.Timeout;

ws.on("open", () => ws.send(JSON.stringify(buildLogon(secretKey, apiKey))));

ws.on("message", (raw: WebSocket.RawData) => {
// newline-delimited batching: split, parse each line
for (const line of raw.toString().split("\n")) {
const trimmed = line.trim();
if (!trimmed) continue;
const msg = JSON.parse(trimmed);

if (msg.method === "pong") continue;

if (!authed) {
if (msg.status === 200 && msg.result?.authenticated) {
authed = true;
console.log("authenticated:", msg.result.account_id);
ws.send(JSON.stringify({
method: "subscribe", channel: "userstream",
account_id: ACCOUNT_ID, id: 2,
}));
pingTimer = setInterval(
() => ws.send(JSON.stringify({ method: "ping", id: "hb" })),
30_000,
);
} else if (typeof msg.status === "number" && msg.status !== 200) {
console.error("logon rejected:", msg);
ws.close();
}
continue;
}

if (msg.id !== undefined && (msg.result === null || msg.result === undefined)) {
continue; // subscribe ack
}
handleEvent(msg);
}
});

ws.on("close", () => {
clearInterval(pingTimer);
console.log("disconnected, reconnecting in 5s…");
setTimeout(connect, 5_000);
});

ws.on("error", (err) => console.error("ws error:", err.message));
}

connect();
```

{% endtab %}

{% tab title="Go" %}

```go
package main

import (
"crypto/ed25519"
"encoding/hex"
"encoding/json"
"fmt"
"log"
"strings"
"time"

"github.com/gorilla/websocket"
)

const (
wsURL = "wss://api-v2-testnet.strikefinance.org/ws/user-api"
privateKeyHex = "<PRIVATE_KEY_HEX>" // 64-char seed or 128-char full key
accountID = "<ACCOUNT_ID>"
)

func parseKeys(hexKey string) (ed25519.PrivateKey, string, error) {
b, err := hex.DecodeString(hexKey)
if err != nil {
return nil, "", err
}
var priv ed25519.PrivateKey
switch len(b) {
case ed25519.SeedSize: // 32
priv = ed25519.NewKeyFromSeed(b)
case ed25519.PrivateKeySize: // 64
priv = ed25519.PrivateKey(b)
default:
return nil, "", fmt.Errorf("bad private key length: %d", len(b))
}
pub := priv.Public().(ed25519.PublicKey)
return priv, hex.EncodeToString(pub), nil
}

func main() {
priv, apiKey, err := parseKeys(privateKeyHex)
if err != nil {
log.Fatal(err)
}

conn, _, err := websocket.DefaultDialer.Dial(wsURL, nil)
if err != nil {
log.Fatal("dial:", err)
}
defer conn.Close()

// 1) session.logon
ts := time.Now().UnixMilli()
msg := fmt.Sprintf("session.logon:%d:%s", ts, apiKey)
sig := hex.EncodeToString(ed25519.Sign(priv, []byte(msg)))
_ = conn.WriteJSON(map[string]any{
"method": "session.logon",
"id": 1,
"params": map[string]any{"apiKey": apiKey, "signature": sig, "timestamp": ts},
})

// 2) subscribe
_ = conn.WriteJSON(map[string]any{
"method": "subscribe", "channel": "userstream",
"account_id": accountID, "id": 2,
})

// 3) keepalive ping every 30s
go func() {
t := time.NewTicker(30 * time.Second)
defer t.Stop()
for range t.C {
_ = conn.WriteJSON(map[string]any{"method": "ping", "id": "hb"})
}
}()

// 4) read loop — split frames on newline
for {
_, raw, err := conn.ReadMessage()
if err != nil {
log.Println("read:", err)
return
}
for _, line := range strings.Split(string(raw), "\n") {
line = strings.TrimSpace(line)
if line == "" {
continue
}
var ev struct {
E string `json:"e"`
Data json.RawMessage `json:"data"`
ID any `json:"id"`
Error json.RawMessage `json:"error"`
}
if err := json.Unmarshal([]byte(line), &ev); err != nil {
continue
}
switch ev.E {
case "ORDER_TRADE_UPDATE":
fmt.Println("order/trade:", string(ev.Data))
case "ACCOUNT_UPDATE":
fmt.Println("account:", string(ev.Data))
case "strategyUpdate":
fmt.Println("strategy:", string(ev.Data))
case "error":
fmt.Println("error:", string(ev.Error))
default:
// pong / acks / other
}
}
}
}
```

{% endtab %}

{% tab title="Rust" %}

```rust
use ed25519_dalek::{Signer, SigningKey};
use futures_util::{SinkExt, StreamExt};
use std::time::{SystemTime, UNIX_EPOCH};
use tokio_tungstenite::{connect_async, tungstenite::Message};

const WS_URL: &str = "wss://api-v2-testnet.strikefinance.org/ws/user-api";
const PRIVATE_KEY_HEX: &str = "<PRIVATE_KEY_HEX>"; // 64-char seed or 128-char full key
const ACCOUNT_ID: &str = "<ACCOUNT_ID>";

fn make_keys(hex_key: &str) -> (SigningKey, String) {
let bytes = hex::decode(hex_key).expect("invalid hex");
let seed: [u8; 32] = bytes[..32].try_into().expect("need >= 32 bytes"); // first 32 = seed
let signing_key = SigningKey::from_bytes(&seed);
let api_key = hex::encode(signing_key.verifying_key().to_bytes());
(signing_key, api_key)
}

fn now_ms() -> u128 {
SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_millis()
}

#[tokio::main]
async fn main() {
let (signing_key, api_key) = make_keys(PRIVATE_KEY_HEX);

let (ws_stream, _) = connect_async(WS_URL).await.expect("connect failed");
let (mut write, mut read) = ws_stream.split();

// 1) session.logon
let ts = now_ms();
let message = format!("session.logon:{}:{}", ts, api_key);
let signature = hex::encode(signing_key.sign(message.as_bytes()).to_bytes());
let logon = serde_json::json!({
"method": "session.logon",
"id": 1,
"params": { "apiKey": api_key, "signature": signature, "timestamp": ts as u64 }
});
write.send(Message::Text(logon.to_string())).await.unwrap();

// 2) subscribe
let sub = serde_json::json!({
"method": "subscribe", "channel": "userstream",
"account_id": ACCOUNT_ID, "id": 2
});
write.send(Message::Text(sub.to_string())).await.unwrap();

// 3) keepalive ping every 30s
tokio::spawn(async move {
let mut interval = tokio::time::interval(std::time::Duration::from_secs(30));
loop {
interval.tick().await;
let ping = serde_json::json!({ "method": "ping", "id": "hb" });
if write.send(Message::Text(ping.to_string())).await.is_err() {
break;
}
}
});

// 4) read loop — split frames on newline
while let Some(msg) = read.next().await {
let text = match msg {
Ok(Message::Text(t)) => t,
Ok(Message::Close(_)) | Err(_) => break,
_ => continue,
};
for line in text.split('\n') {
let line = line.trim();
if line.is_empty() {
continue;
}
let v: serde_json::Value = match serde_json::from_str(line) {
Ok(v) => v,
Err(_) => continue,
};
match v.get("e").and_then(|e| e.as_str()) {
Some("ORDER_TRADE_UPDATE") => println!("order/trade: {}", v["data"]),
Some("ACCOUNT_UPDATE") => println!("account: {}", v["data"]),
Some("strategyUpdate") => println!("strategy: {}", v["data"]),
Some("error") => println!("error: {}", v["error"]),
_ => {} // pong / acks / other
}
}
}
}
```

{% endtab %}
{% endtabs %}

Key formats:

* `privateKey` — Ed25519 private key, hex. Accepts **64 hex chars** (32-byte seed) or **128 hex chars** (64-byte expanded private key).
* `apiKey` — Ed25519 **public** key, hex (64 hex chars). It is derivable from the private key, so you only need to store the private key.

> An **API wallet** is an Ed25519 key pair registered to your `account_id`, with a name, an active flag, and an expiry. Generate the key pair, register the public key as an API wallet, and keep the private key secret — it is what signs `session.logon`.

***

### Subscribe to User Data Stream

After authentication, subscribe to the authenticated master account by omitting account selectors or by supplying its account\_id. To subscribe to an owned active subaccount, use sub\_account\_id; do not pass a subaccount ID as account\_id. \
\
**Example**:&#x20;

```json
{
"method":"subscribe",
"channel":"userstream",
"sub_account_id":"<SUBACCOUNT_ACCOUNT_ID>",
"id":"2"
}.
```

\
Public vault streams can use account\_id or vault\_id.

**Request:**

```json
{
"method": "subscribe",
"channel": "userstream",
"account_id": "0199dc01-720d-7314-84d3-b3c3a102a9f9",
"id": "1"
}
```

**Response (subscribe ack):**

```json
{
"id": "1",
"result": null
}
```

A `result: null` with the matching `id` is the subscribe acknowledgement, not an event — ignore it in your event handler.

***

### Unsubscribe

**Request:**

```json
{
"method": "unsubscribe",
"channel": "userstream",
"account_id": "0199dc01-720d-7314-84d3-b3c3a102a9f9",
"id": "1"
}
```

**Response:**

```json
{
"id": "1",
"result": null
}
```

Use the same account\_id, sub\_account\_id, or vault\_id selector when unsubscribing. Closing the WebSocket connection also stops all subscriptions.

***

### Ping / Pong

To check connection liveness from the client side:

**Request:**

```json
{
"method": "ping",
"id": 1
}
```

**Response:**

```json
{
"method": "pong",
"id": 1
}
```

`id` may be any string or number; it is echoed back. The web app sends `{ "method": "ping", "id": "hb" }` every 30 seconds and treats a missing pong within 10 seconds as a dead socket.

The server also sends WebSocket-level ping frames every 54 seconds. Your WebSocket library should handle pong responses automatically.

***

### Message Envelope

All event messages share a common outer envelope:

```json
{
"e": "<EVENT_TYPE>",
"E": 1705000000000,
"s": "BTC-USDT",
"data": { ... }
}
```

| Field | Type | Description |
| ------ | ------ | ---------------------------------------------------------------------- |
| `e` | string | Event type (`ACCOUNT_UPDATE`, `ORDER_TRADE_UPDATE`, `strategyUpdate`). |
| `E` | number | Event time (ms). |
| `s` | string | Symbol (present on symbol-scoped events). |
| `data` | object | Event payload — shape depends on `e` (see below). |

#### Event types

| `e` value | Meaning |
| -------------------- | ------------------------------------------------------------------- |
| `ACCOUNT_UPDATE` | Balance / position change, vault event, credit event, or tx status. |
| `ORDER_TRADE_UPDATE` | Order lifecycle change or trade execution. |
| `strategyUpdate` | TWAP / Grid strategy reached a terminal state. |
| `error` | Error envelope (see Error Handling). |

***

### ACCOUNT\_UPDATE

Pushed whenever your account balance or positions change. There are **four** payload shapes, distinguished by inspecting `data`:

| Condition on `data` | Shape |
| ------------------------------------------------------------- | ---------------------------------- |
| `data.event_type` is a **vault** event type | Vault event (see Vault Events) |
| `data.event_type` is a **credit** event type | Credit event (see Credit Events) |
| `data.event_type` exists and is `*_SETTLED` / `*_FAILED` (tx) | Transaction status update |
| otherwise (`data.B` / `data.P` arrays) | Standard balance / position update |

> Detection order in the web client: vault → credit → transaction status → standard. Check `data.event_type` first; if it is absent, treat it as a standard balance/position update.

#### Standard Balance / Position Update

```json
{
"e": "ACCOUNT_UPDATE",
"E": 1705000000000,
"data": {
"e": "ORDER",
"B": [
{
"a": "USDT",
"wb": "5000.50",
"cw": "5000.50",
"bc": "0"
}
],
"P": [
{
"s": "BTC-USDT",
"pa": "0.5",
"ep": "45000.00",
"mt": "cross",
"ib": "0",
"ps": "LONG",
"i": 12345
}
],
"r": "FILL",
"E": 1705000000000,
"T": 1705000000000
}
}
```

**`data` fields**

| Field | Type | Description |
| ----- | ------ | ----------------------------------------------------------- |
| `e` | string | Event reason type — why the update fired (see table below). |
| `B` | array | Balances that changed (Balance objects). |
| `P` | array | Positions that changed (Position objects). |
| `r` | string | Raw engine-level reason (e.g. `FILL`). |
| `E` | number | Event time (ms). |
| `T` | number | Transaction time (ms). |

**Event reason types (`data.e`)**

High-level reason the account update occurred.

| Value | Description |
| ------------- | --------------------------------- |
| `ORDER` | Order placed / filled / canceled. |
| `DEPOSIT` | Funds deposited. |
| `WITHDRAW` | Funds withdrawn. |
| `FUNDING` | Funding fee applied. |
| `ADL` | Auto-deleveraging. |
| `LIQUIDATION` | Liquidation engine adjustment. |

**Raw reason strings (`data.r`)**

Engine-level reason passed through in the `r` field. Common values:

| Value | Description |
| ---------- | ----------------------------------- |
| `FILL` | Position/balance changed by a fill. |
| `FUNDING` | Funding settlement. |
| `DEPOSIT` | Deposit credited. |
| `WITHDRAW` | Withdrawal debited. |

**Balance object (`B` array items)**

| Field | Type | Description |
| ----- | ------ | -------------------------------------------------- |
| `a` | string | Asset (e.g. `USDT`, `USD`). |
| `wb` | string | Wallet balance. |
| `cw` | string | Cross wallet balance (defaults to `wb` if absent). |
| `bc` | string | Balance change for this event (delta). |

**Position object (`P` array items)**

| Field | Type | Description |
| ----- | ------ | ------------------------------------------------------------- |
| `s` | string | Symbol. |
| `pa` | string | Position amount (size, base asset). Sign indicates direction. |
| `ep` | string | Entry price. |
| `mt` | string | Margin type — `cross` or `isolated`. |
| `ib` | string | Isolated balance (only meaningful for isolated positions). |
| `ps` | string | Position side — `LONG` or `SHORT`. |
| `i` | number | Position ID. |

> **Derived fields** (unrealized PnL, notional value, initial margin, maintenance margin, liquidation price) are **not** included. Calculate them client-side using mark price data.

**Position update reasons**

When a position update is tied to a special close condition, the account-update reason carries one of these. The web client raises a toast on each:

| Value | Description |
| -------------------- | ---------------------------------- |
| `PARTIAL_LIQUIDATED` | Position was partially liquidated. |
| `FULLY_LIQUIDATED` | Position was fully liquidated. |
| `ADL` | Position was auto-deleveraged. |

#### Transaction Status Update

Pushed when an on-chain withdrawal settles or fails.

```json
{
"e": "ACCOUNT_UPDATE",
"E": 1705000000000,
"data": {
"e": "WITHDRAWAL_SETTLED",
"B": [],
"P": [],
"th": "0xabc123def456...",
"E": 1705000000000,
"T": 1705000000000
}
}
```

| Field | Type | Description |
| ----- | ------ | ------------------------------------------------------ |
| `e` | string | `WITHDRAWAL_SETTLED` or `WITHDRAWAL_FAILED`. |
| `th` | string | On-chain transaction hash. |
| `B` | array | Balance objects (often empty for status-only updates). |
| `P` | array | Position objects (often empty). |
| `E` | number | Event time (ms). |
| `T` | number | Transaction time (ms). |

***

### ORDER\_TRADE\_UPDATE

Pushed when an order is created, filled, canceled, rejected, or expired, and when a trade executes.

```json
{
"e": "ORDER_TRADE_UPDATE",
"E": 1705000000000,
"data": {
"s": "BTC-USDT",
"c": "client-order-123",
"S": "BUY",
"o": "LIMIT",
"f": "GTC",
"q": "0.5",
"p": "45000.00",
"ap": "0",
"sp": "0",
"x": "NEW",
"X": "OPEN",
"i": 999888777,
"cr": "",
"l": "0",
"z": "0",
"L": "0",
"N": "USDT",
"n": "0",
"T": 1705000000000,
"t": 0,
"b": "0",
"a": "0",
"m": false,
"R": false,
"wt": "",
"ot": "LIMIT",
"ps": "BOTH",
"cp": false,
"AP": "0",
"CR": "0",
"rp": "0",
"act": "",
"E": 1705000000000
}
}
```

**`data` fields**

| Field | Type | Description |
| ----- | ------- | ---------------------------------------------------------------------- |
| `s` | string | Symbol. |
| `c` | string | Client order ID (auto-generated if not supplied). |
| `S` | string | Side — `BUY` / `SELL`. |
| `o` | string | Order type (see Order types). |
| `f` | string | Time in force (see Time in force). |
| `q` | string | Original quantity (order size, base asset). |
| `p` | string | Original price (`0` for market/stop/take-profit market orders). |
| `ap` | string | Average fill price. |
| `sp` | string | Stop / trigger price for conditional orders. |
| `x` | string | Execution type (see Execution types). |
| `X` | string | Order status (see Order statuses). |
| `i` | number | Order ID. |
| `cr` | string | Close reason (why the order closed; empty when not applicable). |
| `l` | string | Last filled quantity. |
| `z` | string | Cumulative filled quantity. |
| `L` | string | Last filled price. |
| `N` | string | Commission asset. |
| `n` | string | Commission amount. |
| `T` | number | Transaction time (ms). |
| `t` | number | Trade ID (`0` when the update is not a trade). |
| `b` | string | Bids notional. |
| `a` | string | Asks notional. |
| `m` | boolean | Is maker side. |
| `R` | boolean | Is reduce-only. |
| `wt` | string | Working type — which price triggers a conditional (see Working types). |
| `ot` | string | Original order type (type before a conditional order triggered). |
| `ps` | string | Position side — `LONG` / `SHORT` / `BOTH`. |
| `cp` | boolean | Is close-position order. |
| `AP` | string | Activation price (trailing stop). |
| `CR` | string | Callback rate, percent (trailing stop). |
| `rp` | string | Realized profit for this trade. |
| `act` | string | Auto-close type (see Auto-close types). Empty for user-placed orders. |
| `E` | number | Event time (ms). |

**Order types (`o` / `ot`)**

| Value | Description |
| ------------------------------------ | --------------------------- |
| `MARKET` | Market order. |
| `LIMIT` | Limit order. |
| `STOP` / `STOP_MARKET` | Stop-market order. |
| `STOP_LIMIT` | Stop-limit order. |
| `TAKE_PROFIT` / `TAKE_PROFIT_MARKET` | Take-profit market order. |
| `TAKE_PROFIT_LIMIT` | Take-profit limit order. |
| `TRAILING_STOP_MARKET` | Trailing stop market order. |

**Execution types (`x`)**

The reason this specific `ORDER_TRADE_UPDATE` was emitted.

| Value | Description |
| ------------ | ------------------------------------------ |
| `NEW` | Order accepted onto the book. |
| `TRADE` | Order (partially) filled — trade executed. |
| `CANCELED` | Order canceled. |
| `EXPIRED` | Order expired. |
| `REJECTED` | Order rejected. |
| `CALCULATED` | Conditional order calculated / triggered. |

**Order statuses (`X`)**

| Value | Description |
| ------------------ | ----------------------------- |
| `NEW` | Newly accepted. |
| `PENDING_NEW` | Accepted, pending activation. |
| `OPEN` | Resting on the book. |
| `PARTIALLY_FILLED` | Partially filled, still open. |
| `FILLED` | Fully filled (terminal). |
| `CANCELED` | Canceled (terminal). |
| `REJECTED` | Rejected (terminal). |
| `EXPIRED` | Expired (terminal). |

> `FILLED`, `CANCELED`, `REJECTED`, and `EXPIRED` are terminal — the order leaves the open-orders book.

**Time in force (`f`)**

| Value | Description |
| ----- | -------------------- |
| `GTC` | Good-til-canceled. |
| `IOC` | Immediate-or-cancel. |
| `FOK` | Fill-or-kill. |

**Working types (`wt`)**

| Value | Description |
| ---------------- | ----------------------------------------------- |
| `MARK_PRICE` | Conditional triggers off mark price. |
| `CONTRACT_PRICE` | Conditional triggers off contract (last) price. |

**Auto-close types (`act`)**

Indicates the order was created automatically by the system, not by the user. Empty / `none` for user-placed orders.

| Value | Description |
| ------------- | ---------------------------------- |
| \`\` (empty) | User-placed order. |
| `LIQUIDATION` | Created by the liquidation engine. |
| `ADL` | Created by auto-deleveraging. |

***

### strategyUpdate

Pushed when a TWAP or Grid strategy reaches a terminal status (completed, expired, cancelled, failed, or liquidated).

```json
{
"e": "strategyUpdate",
"E": 1705000000000,
"s": "BTC-USDT",
"data": {
"account_id": "0199dc01-720d-7314-84d3-b3c3a102a9f9",
"strategy_id": "strat_abc123",
"market": "BTC-USDT",
"status": "completed",
"side": "BUY",
"total_size": "5.0",
"filled_size": "4.8",
"duration_sec": 3600,
"slices_fired": 24,
"nominal_slices": 24,
"last_error": "",
"completed_at_ms": 1705000000000
}
}
```

**`data` fields**

| Field | Type | Description |
| ----------------- | ------ | ------------------------------------------- |
| `account_id` | string | Account the strategy belongs to. |
| `strategy_id` | string | Strategy identifier. |
| `market` | string | Market symbol (e.g. `BTC-USDT`). |
| `status` | string | Terminal status (see Strategy statuses). |
| `side` | string | `BUY` / `SELL`. |
| `total_size` | string | Total target size. |
| `filled_size` | string | Size actually filled. |
| `duration_sec` | number | Strategy run duration, seconds. |
| `slices_fired` | number | Number of slices actually fired. |
| `nominal_slices` | number | Number of slices originally planned. |
| `last_error` | string | Last error message (populated on `failed`). |
| `completed_at_ms` | number | Terminal timestamp (ms). |

**Strategy statuses**

| Value | Description |
| ------------ | ----------------------------------- |
| `completed` | Ran to completion. |
| `expired` | Duration elapsed before completing. |
| `cancelled` | Canceled by the user. |
| `failed` | Failed (`last_error` has details). |
| `liquidated` | Underlying position was liquidated. |

***

### Vault Events

Vault lifecycle events are delivered as `ACCOUNT_UPDATE` messages with a different `data` shape. Instead of balance/position arrays, vault events use an `event_type` + `event_data` envelope.

**How to distinguish:** `data.event_type` exists and is one of the vault event types below, and `data.event_data` exists.

#### Envelope format

```json
{
"e": "ACCOUNT_UPDATE",
"E": 1705000000000,
"data": {
"event_type": "VAULT_CREATED",
"event_data": { ... }
}
}
```

#### Vault event types

| Type | Category | Description |
| --------------------------------- | -------- | ------------------------------------------- |
| `VAULT_CREATED` | success | A vault was created. |
| `VAULT_DEPOSITED` | success | A deposit into a vault settled. |
| `VAULT_WITHDRAWAL_REQUESTED` | success | A withdrawal was requested (lockup begins). |
| `VAULT_WITHDRAWAL_EXECUTED` | success | A requested withdrawal executed. |
| `VAULT_WITHDRAWAL_CANCELLED` | success | A pending withdrawal was cancelled. |
| `VAULT_LEADER_FEE_EARNED` | success | Leader earned a performance fee. |
| `VAULT_CREATE_FAILED` | error | Vault creation failed. |
| `VAULT_DEPOSIT_FAILED` | error | Deposit failed. |
| `VAULT_WITHDRAWAL_FAILED` | error | Withdrawal failed. |
| `VAULT_WITHDRAWAL_REQUEST_FAILED` | error | Withdrawal request failed. |
| `VAULT_WITHDRAWAL_EXECUTE_FAILED` | error | Withdrawal execution failed. |
| `VAULT_WITHDRAWAL_CANCEL_FAILED` | error | Withdrawal cancel failed. |

> Error types all end in `_FAILED` and share the Vault Error payload below.

#### VAULT\_CREATED

```json
{
"event_type": "VAULT_CREATED",
"event_data": {
"request_id": "req_xyz",
"vault_id": "550e8400-e29b-41d4-a716-446655440000",
"account_id": "vault_account_id",
"vault_type": "user",
"name": "My Trading Vault",
"description": "MACD Strategy",
"leader_account_id": "leader_account_id",
"leader_commission_bps": 1000,
"initial_deposit": "5000.00",
"creation_fee": "100.00",
"total_shares": "5000.00",
"min_leader_share_pct": 5,
"lockup_days": 1,
"min_deposit": "100.00",
"allow_proportional_close": false,
"is_verified": false,
"timestamp": 1705000000000
}
}
```

#### VAULT\_DEPOSITED

```json
{
"event_type": "VAULT_DEPOSITED",
"event_data": {
"request_id": "req_xyz",
"vault_id": "550e8400-e29b-41d4-a716-446655440000",
"vault_name": "My Trading Vault",
"usd_amount": "1000.00",
"share_price": "1.05",
"shares": "952.38",
"liquidation_reset": false,
"timestamp": 1705000000000
}
}
```

#### VAULT\_WITHDRAWAL\_REQUESTED

```json
{
"event_type": "VAULT_WITHDRAWAL_REQUESTED",
"event_data": {
"withdrawal_id": "w_123",
"vault_id": "550e8400-e29b-41d4-a716-446655440000",
"vault_name": "My Trading Vault",
"account_id": "user_account_id",
"shares": "500.00",
"estimated_usd": "525.00",
"share_price": "1.05",
"request_timestamp": 1705000000000,
"execute_at_timestamp": 1705086400000,
"avg_entry_price": "42000.00"
}
}
```

#### VAULT\_WITHDRAWAL\_EXECUTED

```json
{
"event_type": "VAULT_WITHDRAWAL_EXECUTED",
"event_data": {
"withdrawal_id": "w_123",
"vault_id": "550e8400-e29b-41d4-a716-446655440000",
"shares_burned": "500.00",
"share_price": "1.05",
"gross_usd": "525.00",
"leader_fee": "52.50",
"leader_shares_minted": "50.00",
"leader_account_id": "leader_account_id",
"net_usd": "472.50",
"remaining_shares": "3000.00",
"actual_usd": "472.50",
"execution_share_price": "1.05",
"execution_timestamp": 1705000000000
}
}
```

#### VAULT\_WITHDRAWAL\_CANCELLED

```json
{
"event_type": "VAULT_WITHDRAWAL_CANCELLED",
"event_data": {
"withdrawal_id": "w_123",
"vault_id": "550e8400-e29b-41d4-a716-446655440000",
"account_id": "user_account_id",
"shares_unlocked": "500.00"
}
}
```

#### VAULT\_LEADER\_FEE\_EARNED

Sent to the vault leader when a depositor's withdrawal triggers a performance fee.

```json
{
"event_type": "VAULT_LEADER_FEE_EARNED",
"event_data": {
"vault_id": "550e8400-e29b-41d4-a716-446655440000",
"withdrawal_id": "w_123",
"shares_minted": "50.00",
"fee_usd": "52.50",
"share_price": "1.05",
"new_leader_shares": "5050.00",
"new_leader_avg_entry": "1.00",
"execution_timestamp": 1705000000000
}
}
```

#### Vault Error Events

All vault error events share the same payload structure:

```json
{
"event_type": "VAULT_CREATE_FAILED",
"event_data": {
"account_id": "user_account_id",
"request_id": "req_xyz",
"error": "Insufficient balance",
"timestamp": 1705000000000
}
}
```

| Field | Type | Description |
| ------------ | ------ | ---------------------------- |
| `account_id` | string | Account the request was for. |
| `request_id` | string | Originating request ID. |
| `error` | string | Human-readable error reason. |
| `timestamp` | number | Event time (ms). |

***

### Credit Events

Credit (collateralized / funded-trading) lifecycle events are also delivered as `ACCOUNT_UPDATE` messages using the same `event_type` + `event_data` envelope as vault events.

**How to distinguish:** `data.event_type` exists and is one of the credit event types below, and `data.event_data` exists.

#### Envelope format

```json
{
"e": "ACCOUNT_UPDATE",
"E": 1705000000000,
"data": {
"event_type": "CREDIT_COLLATERAL_PLEDGED",
"event_data": { ... }
}
}
```

#### Credit event types

| Type | Description |
| -------------------------------------------- | ------------------------------------------- |
| `CREDIT_SETTINGS_UPDATED` | Credit account settings changed. |
| `CREDIT_COLLATERAL_PRICE_UPDATED` | Collateral mark price updated. |
| `CREDIT_COLLATERAL_PLEDGED` | Collateral pledged into the credit account. |
| `CREDIT_COLLATERAL_RELEASED` | Collateral released back to the user. |
| `CREDIT_DEBT_REPAID` | Outstanding debt repaid. |
| `CREDIT_VAULT_SUBSCRIBED` | Subscribed to a credit/funded vault. |
| `CREDIT_COLLATERAL_CONVERTED` | Collateral converted to another asset. |
| `CREDIT_INSURANCE_FUND_COLLATERAL_PURCHASED` | Insurance fund purchased collateral. |
| `CREDIT_FUNDED_SHARES_REDEEMED` | Funded shares redeemed. |
| `CREDIT_FUNDED_SHARES_LIQUIDATED` | Funded shares liquidated. |
| `CREDIT_FUNDED_SHARES_UNLOCKED` | Funded shares unlocked. |
| `CREDIT_BAD_DEBT_RECORDED` | Bad debt recorded against the account. |
| `CREDIT_BAD_DEBT_REPAID` | Previously recorded bad debt repaid. |
| `CREDIT_FUNDED_WITHDRAWAL_REQUESTED` | Funded-balance withdrawal requested. |
| `CREDIT_FUNDED_WITHDRAWAL_CANCELLED` | Funded-balance withdrawal cancelled. |
| `CREDIT_POSITION_STATUS_UPDATED` | Credit position status changed. |

The `event_data` object is event-specific; treat it as a `Record<string, unknown>` keyed by the fields relevant to each credit operation (collateral amounts, debt amounts, share counts, prices, timestamps).

***

### Vault User Data Stream

Vault account streams are **public** — no authentication required. You can subscribe to a vault's userstream to receive the same `ACCOUNT_UPDATE` and `ORDER_TRADE_UPDATE` events for that vault's trading account.

Subscribe using either the vault's `account_id` or `vault_id`.

**By vault account ID:**

```json
{
"method": "subscribe",
"channel": "userstream",
"account_id": "<VAULT_ACCOUNT_ID>",
"id": "1"
}
```

**By vault ID (server resolves to account ID):**

```json
{
"method": "subscribe",
"channel": "userstream",
"vault_id": "<VAULT_ID>",
"id": "1"
}
```

***

### Error Handling

Errors are returned as WebSocket messages with `e: "error"`:

```json
{
"e": "error",
"E": 1705000000000,
"error": {
"code": 401,
"msg": "Authentication required: use AUTH or session.logon before subscribing to user data streams"
}
}
```

| Field | Type | Description |
| ------------ | ------ | ----------------------- |
| `error.code` | number | Error code. |
| `error.msg` | string | Human-readable message. |

Authentication failures may also surface as a WebSocket **close** with one of these codes:

| Close code | Meaning |
| ---------- | -------------------- |
| `1008` | Policy violation. |
| `4001` | Unauthorized. |
| `4003` | Forbidden. |
| `4401` | Unauthorized (auth). |
| `4403` | Forbidden (auth). |