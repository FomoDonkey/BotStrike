<!-- source: https://docs.strikefinance.org/api/getting-started.md -->
> For the complete documentation index, see [llms.txt](https://docs.strikefinance.org/llms.txt). Markdown versions of documentation pages are available by appending `.md` to page URLs; this page is available as [Markdown](https://docs.strikefinance.org/api/getting-started.md).

# Getting Started

## API Wallet Authentication

API Wallets provide secure, programmatic access to the Strike Perpetuals trading API using Ed25519 digital signatures.

### Overview

API wallets use **asymmetric cryptography** (Ed25519 key pairs):

* **Public Key** - Stored on server, used to verify your signatures
* **Private Key** - Kept by you (never shared), used to sign requests

**Benefits:**

* Private key never leaves the client
* No shared secrets stored on server
* Non-repudiation (signatures prove authenticity)
* Can perform trading operations but **cannot withdraw funds**

### Skills

We've created agent skills to help you navigate through the apis. To install simply run

```
npx strike-finance-skills install
```

The repo for the skills can be found here: <https://github.com/strike-finance/strike-finance-skills>

***

### Quick Start

#### 1. Generate Key Pair

Generate an Ed25519 key pair on the [API Wallets page](https://app.strikefinance.org/api-keys) or locally using the examples below.

#### 2. Register Your Public Key

Save your public key on the [API Wallets page](https://app.strikefinance.org/api-keys).

#### 3. Sign Requests

Every authenticated request requires these headers:

* `X-API-Wallet-Public-Key` - Your public key (64 hex chars)
* `X-API-Wallet-Signature` - Ed25519 signature of the message
* `X-API-Wallet-Timestamp` - Unix timestamp (seconds)
* `X-API-Wallet-Nonce` - Unique UUID per request

***

### Key Format

* **Public Key**: 64 hex characters (32 bytes), Raw Ed25519
* **Private Key**: 64 or 128 hex characters, Seed or Full key

> **Note:** SSH key format (`ssh-ed25519 AAAA...`) is NOT compatible. Use raw Ed25519 hex keys.

***

### Signature Message Format

```
{METHOD}:{PATH}:{TIMESTAMP}:{NONCE}:{BODY_HASH}
```

* **METHOD** - HTTP method (uppercase): `GET`, `POST`, `DELETE`
* **PATH** - Full path with query string: `/v2/order`
* **TIMESTAMP** - Unix timestamp in seconds: `1704067200`
* **NONCE** - UUID v4: `550e8400-e29b-41d4-a716-446655440000`
* **BODY\_HASH** - SHA-256 of JSON body (empty string hash for GET)

***

### Complete Examples

{% tabs %}
{% tab title="Python" %}

```python
"""
Strike API Wallet Authentication - Python Example
Requirements: pip install cryptography requests
"""

import hashlib
import json
import time
import uuid
import requests
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

BASE_URL = "https://api.strikefinance.org"

def generate_key_pair():
"""Generate a new Ed25519 key pair."""
private_key = Ed25519PrivateKey.generate()
public_key = private_key.public_key()

private_key_hex = private_key.private_bytes(
encoding=serialization.Encoding.Raw,
format=serialization.PrivateFormat.Raw,
encryption_algorithm=serialization.NoEncryption()
).hex()

public_key_hex = public_key.public_bytes(
encoding=serialization.Encoding.Raw,
format=serialization.PublicFormat.Raw
).hex()

return private_key_hex, public_key_hex

def serialize_body(body: dict = None) -> str:
"""Serialize JSON once so the signed body exactly matches the sent body."""
if body is None:
return ""
return json.dumps(body, separators=(",", ":"))

def sign_request(private_key_hex: str, public_key_hex: str, method: str, path: str, body_str: str = "") -> dict:
"""Sign a request and return the required headers."""
private_key_bytes = bytes.fromhex(private_key_hex)

# Accept 32-byte seed/private key or 64-byte expanded private key.
if len(private_key_bytes) == 64:
private_key_bytes = private_key_bytes[:32]

private_key = Ed25519PrivateKey.from_private_bytes(private_key_bytes)

timestamp = int(time.time())
nonce = str(uuid.uuid4())
body_hash = hashlib.sha256(body_str.encode()).hexdigest()

message = f"{method.upper()}:{path}:{timestamp}:{nonce}:{body_hash}"
signature = private_key.sign(message.encode())

return {
"X-API-Wallet-Public-Key": public_key_hex,
"X-API-Wallet-Signature": signature.hex(),
"X-API-Wallet-Timestamp": str(timestamp),
"X-API-Wallet-Nonce": nonce,
}

def get_account(private_key_hex: str, public_key_hex: str) -> dict:
path = "/v2/account"
headers = sign_request(private_key_hex, public_key_hex, "GET", path)
response = requests.get(f"{BASE_URL}{path}", headers=headers)
return response.json()

def get_positions(private_key_hex: str, public_key_hex: str, symbol: str = None) -> dict:
path = "/v2/positions"
if symbol:
path = f"{path}?symbol={symbol}"

headers = sign_request(private_key_hex, public_key_hex, "GET", path)
response = requests.get(f"{BASE_URL}{path}", headers=headers)
return response.json()

def create_order(private_key_hex: str, public_key_hex: str, symbol: str, side: str,
order_type: str, size: str, price: str = None) -> dict:
path = "/v2/order"
body = {"symbol": symbol, "side": side, "type": order_type, "size": size}
if price:
body["price"] = price

body_json = serialize_body(body)
headers = sign_request(private_key_hex, public_key_hex, "POST", path, body_json)
headers["Content-Type"] = "application/json"

response = requests.post(f"{BASE_URL}{path}", headers=headers, data=body_json)
return response.json()

def cancel_order(private_key_hex: str, public_key_hex: str, order_id: int, symbol: str) -> dict:
path = "/v2/order/cancel"
body = {"order_id": order_id, "symbol": symbol}

body_json = serialize_body(body)
headers = sign_request(private_key_hex, public_key_hex, "DELETE", path, body_json)
headers["Content-Type"] = "application/json"

response = requests.delete(f"{BASE_URL}{path}", headers=headers, data=body_json)
return response.json()

if __name__ == "__main__":
PRIVATE_KEY = "your_private_key_hex"
PUBLIC_KEY = "your_public_key_hex"

account = get_account(PRIVATE_KEY, PUBLIC_KEY)
print("Account:", json.dumps(account, indent=2))

positions = get_positions(PRIVATE_KEY, PUBLIC_KEY)
print("Positions:", json.dumps(positions, indent=2))

order = create_order(PRIVATE_KEY, PUBLIC_KEY, "BTC-USD", "buy", "limit", "0.001", "50000")
print("Order:", json.dumps(order, indent=2))
```

{% endtab %}

{% tab title="TypeScript" %}

```typescript
/**
* Strike API Wallet Authentication - TypeScript Example
* Requirements: npm install @noble/ed25519 @noble/hashes uuid
*/

import { ed25519 } from '@noble/ed25519';
import { sha512 } from '@noble/hashes/sha512';
import { sha256 } from '@noble/hashes/sha256';
import { v4 as uuidv4 } from 'uuid';

ed25519.etc.sha512Sync = (...m: Uint8Array[]) => sha512(ed25519.etc.concatBytes(...m));

const BASE_URL = 'https://api.strikefinance.org';
const encoder = new TextEncoder();

interface SignedHeaders {
'X-API-Wallet-Public-Key': string;
'X-API-Wallet-Signature': string;
'X-API-Wallet-Timestamp': string;
'X-API-Wallet-Nonce': string;
'Content-Type'?: string;
}

async function generateKeyPair(): Promise<{ privateKeyHex: string; publicKeyHex: string }> {
const privateKey = ed25519.utils.randomPrivateKey();
const publicKey = await ed25519.getPublicKey(privateKey);

return {
privateKeyHex: Buffer.from(privateKey).toString('hex'),
publicKeyHex: Buffer.from(publicKey).toString('hex'),
};
}

function serializeBody(body?: object): string {
return body ? JSON.stringify(body) : '';
}

function normalizePrivateKey(privateKeyHex: string): Uint8Array {
const privateKey = Buffer.from(privateKeyHex, 'hex');

// Accept 32-byte seed/private key or 64-byte expanded private key.
if (privateKey.length === 64) {
return privateKey.subarray(0, 32);
}

return privateKey;
}

async function signRequest(
privateKeyHex: string,
publicKeyHex: string,
method: string,
path: string,
bodyStr = ''
): Promise<SignedHeaders> {
const timestamp = Math.floor(Date.now() / 1000);
const nonce = uuidv4();

const bodyHash = Buffer.from(sha256(encoder.encode(bodyStr))).toString('hex');
const message = `${method.toUpperCase()}:${path}:${timestamp}:${nonce}:${bodyHash}`;

const privateKey = normalizePrivateKey(privateKeyHex);
const signature = await ed25519.sign(encoder.encode(message), privateKey);

return {
'X-API-Wallet-Public-Key': publicKeyHex,
'X-API-Wallet-Signature': Buffer.from(signature).toString('hex'),
'X-API-Wallet-Timestamp': timestamp.toString(),
'X-API-Wallet-Nonce': nonce,
};
}

async function getAccount(privateKeyHex: string, publicKeyHex: string): Promise<any> {
const path = '/v2/account';
const headers = await signRequest(privateKeyHex, publicKeyHex, 'GET', path);

const response = await fetch(`${BASE_URL}${path}`, {
method: 'GET',
headers,
});

return response.json();
}

async function getPositions(privateKeyHex: string, publicKeyHex: string, symbol?: string): Promise<any> {
let path = '/v2/positions';
if (symbol) {
path = `${path}?symbol=${encodeURIComponent(symbol)}`;
}

const headers = await signRequest(privateKeyHex, publicKeyHex, 'GET', path);

const response = await fetch(`${BASE_URL}${path}`, {
method: 'GET',
headers,
});

return response.json();
}

async function createOrder(
privateKeyHex: string,
publicKeyHex: string,
symbol: string,
side: 'buy' | 'sell',
type: 'limit' | 'market',
size: string,
price?: string
): Promise<any> {
const path = '/v2/order';
const body: any = { symbol, side, type, size };
if (price) {
body.price = price;
}

const bodyJson = serializeBody(body);
const headers = await signRequest(privateKeyHex, publicKeyHex, 'POST', path, bodyJson);
headers['Content-Type'] = 'application/json';

const response = await fetch(`${BASE_URL}${path}`, {
method: 'POST',
headers,
body: bodyJson,
});

return response.json();
}

async function cancelOrder(
privateKeyHex: string,
publicKeyHex: string,
orderId: number,
symbol: string
): Promise<any> {
const path = '/v2/order/cancel';
const body = { order_id: orderId, symbol };

const bodyJson = serializeBody(body);
const headers = await signRequest(privateKeyHex, publicKeyHex, 'DELETE', path, bodyJson);
headers['Content-Type'] = 'application/json';

const response = await fetch(`${BASE_URL}${path}`, {
method: 'DELETE',
headers,
body: bodyJson,
});

return response.json();
}

async function main() {
const PRIVATE_KEY = 'your_private_key_hex';
const PUBLIC_KEY = 'your_public_key_hex';

const account = await getAccount(PRIVATE_KEY, PUBLIC_KEY);
console.log('Account:', account);

const positions = await getPositions(PRIVATE_KEY, PUBLIC_KEY);
console.log('Positions:', positions);

const order = await createOrder(PRIVATE_KEY, PUBLIC_KEY, 'BTC-USD', 'buy', 'limit', '0.001', '50000');
console.log('Order:', order);
}

main().catch(console.error);
```

{% endtab %}

{% tab title="Go" %}

```go
/*
Strike API Wallet Authentication - Go Example
Requirements: go get github.com/google/uuid
*/

package main

import (
"bytes"
"crypto/ed25519"
"crypto/rand"
"crypto/sha256"
"encoding/hex"
"encoding/json"
"fmt"
"io"
"net/http"
"net/url"
"strings"
"time"

"github.com/google/uuid"
)

const BaseURL = "https://api.strikefinance.org"

type Client struct {
privateKey ed25519.PrivateKey
publicKey string
httpClient *http.Client
}

func GenerateKeyPair() (privateKeyHex, publicKeyHex string, err error) {
pub, priv, err := ed25519.GenerateKey(rand.Reader)
if err != nil {
return "", "", err
}
return hex.EncodeToString(priv), hex.EncodeToString(pub), nil
}

func NewClient(privateKeyHex, publicKeyHex string) (*Client, error) {
privBytes, err := hex.DecodeString(privateKeyHex)
if err != nil {
return nil, err
}

var privateKey ed25519.PrivateKey
switch len(privBytes) {
case 32:
privateKey = ed25519.NewKeyFromSeed(privBytes)
case 64:
privateKey = ed25519.PrivateKey(privBytes)
default:
return nil, fmt.Errorf("invalid private key length: %d", len(privBytes))
}

return &Client{
privateKey: privateKey,
publicKey: publicKeyHex,
httpClient: &http.Client{Timeout: 30 * time.Second},
}, nil
}

func (c *Client) signRequest(method, path string, bodyBytes []byte) map[string]string {
timestamp := time.Now().Unix()
nonce := uuid.New().String()

bodyHash := sha256.Sum256(bodyBytes)
bodyHashHex := hex.EncodeToString(bodyHash[:])

message := fmt.Sprintf(
"%s:%s:%d:%s:%s",
strings.ToUpper(method),
path,
timestamp,
nonce,
bodyHashHex,
)

signature := ed25519.Sign(c.privateKey, []byte(message))

return map[string]string{
"X-API-Wallet-Public-Key": c.publicKey,
"X-API-Wallet-Signature": hex.EncodeToString(signature),
"X-API-Wallet-Timestamp": fmt.Sprintf("%d", timestamp),
"X-API-Wallet-Nonce": nonce,
}
}

func (c *Client) doRequest(method, path string, body any) ([]byte, error) {
var bodyBytes []byte

if body != nil {
var err error
bodyBytes, err = json.Marshal(body)
if err != nil {
return nil, err
}
}

headers := c.signRequest(method, path, bodyBytes)

req, err := http.NewRequest(strings.ToUpper(method), BaseURL+path, bytes.NewReader(bodyBytes))
if err != nil {
return nil, err
}

for k, v := range headers {
req.Header.Set(k, v)
}

if body != nil {
req.Header.Set("Content-Type", "application/json")
}

resp, err := c.httpClient.Do(req)
if err != nil {
return nil, err
}
defer resp.Body.Close()

respBody, err := io.ReadAll(resp.Body)
if err != nil {
return nil, err
}

if resp.StatusCode < 200 || resp.StatusCode >= 300 {
return respBody, fmt.Errorf("request failed with status %d: %s", resp.StatusCode, string(respBody))
}

return respBody, nil
}

func (c *Client) GetAccount() ([]byte, error) {
return c.doRequest("GET", "/v2/account", nil)
}

func (c *Client) GetPositions(symbol string) ([]byte, error) {
path := "/v2/positions"
if symbol != "" {
path = fmt.Sprintf("%s?symbol=%s", path, url.QueryEscape(symbol))
}
return c.doRequest("GET", path, nil)
}

func (c *Client) CreateOrder(symbol, side, orderType, size, price string) ([]byte, error) {
body := map[string]any{
"symbol": symbol,
"side": side,
"type": orderType,
"size": size,
}

if price != "" {
body["price"] = price
}

return c.doRequest("POST", "/v2/order", body)
}

func (c *Client) CancelOrder(orderID int64, symbol string) ([]byte, error) {
body := map[string]any{
"order_id": orderID,
"symbol": symbol,
}

return c.doRequest("DELETE", "/v2/order/cancel", body)
}

func main() {
privateKeyHex := "your_private_key_hex"
publicKeyHex := "your_public_key_hex"

client, err := NewClient(privateKeyHex, publicKeyHex)
if err != nil {
panic(err)
}

account, err := client.GetAccount()
if err != nil {
panic(err)
}
fmt.Println("Account:", string(account))

positions, err := client.GetPositions("")
if err != nil {
panic(err)
}
fmt.Println("Positions:", string(positions))

order, err := client.CreateOrder("BTC-USD", "buy", "limit", "0.001", "50000")
if err != nil {
panic(err)
}
fmt.Println("Order:", string(order))
}
```

{% endtab %}

{% tab title="Rust" %}

```rust
//! Strike API Wallet Authentication - Rust Example
//! Requirements:
//! cargo add ed25519-dalek --features rand_core
//! cargo add hex reqwest serde_json sha2 tokio uuid
//! cargo add rand_core@0.6 --features getrandom

use ed25519_dalek::{Signer, SigningKey};
use rand_core::OsRng;
use reqwest::Client;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::time::{SystemTime, UNIX_EPOCH};
use uuid::Uuid;

const BASE_URL: &str = "https://api.strikefinance.org";

pub struct StrikeClient {
signing_key: SigningKey,
public_key_hex: String,
http_client: Client,
}

impl StrikeClient {
pub fn generate_key_pair() -> (String, String) {
let mut csprng = OsRng;
let signing_key = SigningKey::generate(&mut csprng);
let verifying_key = signing_key.verifying_key();

(
hex::encode(signing_key.to_bytes()),
hex::encode(verifying_key.to_bytes()),
)
}

pub fn new(private_key_hex: &str, public_key_hex: &str) -> Result<Self, Box<dyn std::error::Error>> {
let priv_bytes = hex::decode(private_key_hex)?;

let key_bytes: [u8; 32] = match priv_bytes.len() {
32 => priv_bytes.try_into()?,
64 => priv_bytes[..32].try_into()?,
_ => return Err("invalid private key length".into()),
};

Ok(Self {
signing_key: SigningKey::from_bytes(&key_bytes),
public_key_hex: public_key_hex.to_string(),
http_client: Client::new(),
})
}

fn sign_request(&self, method: &str, path: &str, body: &[u8]) -> HashMap<String, String> {
let timestamp = SystemTime::now()
.duration_since(UNIX_EPOCH)
.unwrap()
.as_secs();

let nonce = Uuid::new_v4().to_string();

let body_hash = hex::encode(Sha256::digest(body));
let message = format!(
"{}:{}:{}:{}:{}",
method.to_uppercase(),
path,
timestamp,
nonce,
body_hash
);

let signature = self.signing_key.sign(message.as_bytes());

HashMap::from([
("X-API-Wallet-Public-Key".to_string(), self.public_key_hex.clone()),
("X-API-Wallet-Signature".to_string(), hex::encode(signature.to_bytes())),
("X-API-Wallet-Timestamp".to_string(), timestamp.to_string()),
("X-API-Wallet-Nonce".to_string(), nonce),
])
}

pub async fn get_account(&self) -> Result<Value, Box<dyn std::error::Error>> {
let path = "/v2/account";
let headers = self.sign_request("GET", path, &[]);

let mut request = self.http_client.get(format!("{}{}", BASE_URL, path));
for (k, v) in headers {
request = request.header(&k, &v);
}

Ok(request.send().await?.json().await?)
}

pub async fn get_positions(&self, symbol: Option<&str>) -> Result<Value, Box<dyn std::error::Error>> {
let path = match symbol {
Some(s) => format!("/v2/positions?symbol={}", urlencoding::encode(s)),
None => "/v2/positions".to_string(),
};

let headers = self.sign_request("GET", &path, &[]);

let mut request = self.http_client.get(format!("{}{}", BASE_URL, path));
for (k, v) in headers {
request = request.header(&k, &v);
}

Ok(request.send().await?.json().await?)
}

pub async fn create_order(
&self,
symbol: &str,
side: &str,
order_type: &str,
size: &str,
price: Option<&str>,
) -> Result<Value, Box<dyn std::error::Error>> {
let path = "/v2/order";

let mut body = json!({
"symbol": symbol,
"side": side,
"type": order_type,
"size": size,
});

if let Some(p) = price {
body["price"] = json!(p);
}

let body_bytes = serde_json::to_vec(&body)?;
let headers = self.sign_request("POST", path, &body_bytes);

let mut request = self.http_client
.post(format!("{}{}", BASE_URL, path))
.header("Content-Type", "application/json")
.body(body_bytes);

for (k, v) in headers {
request = request.header(&k, &v);
}

Ok(request.send().await?.json().await?)
}

pub async fn cancel_order(&self, order_id: i64, symbol: &str) -> Result<Value, Box<dyn std::error::Error>> {
let path = "/v2/order/cancel";

let body = json!({
"order_id": order_id,
"symbol": symbol,
});

let body_bytes = serde_json::to_vec(&body)?;
let headers = self.sign_request("DELETE", path, &body_bytes);

let mut request = self.http_client
.delete(format!("{}{}", BASE_URL, path))
.header("Content-Type", "application/json")
.body(body_bytes);

for (k, v) in headers {
request = request.header(&k, &v);
}

Ok(request.send().await?.json().await?)
}
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
let private_key_hex = "your_private_key_hex";
let public_key_hex = "your_public_key_hex";

let client = StrikeClient::new(private_key_hex, public_key_hex)?;

let account = client.get_account().await?;
println!("Account: {}", serde_json::to_string_pretty(&account)?);

let positions = client.get_positions(None).await?;
println!("Positions: {}", serde_json::to_string_pretty(&positions)?);

let order = client
.create_order("BTC-USD", "buy", "limit", "0.001", Some("50000"))
.await?;

println!("Order: {}", serde_json::to_string_pretty(&order)?);

Ok(())
}
```

{% endtab %}
{% endtabs %}

***

### Permissions

* **Allowed**: View account, balances, positions, place/cancel orders, modify leverage, view history
* **Not Allowed**: Withdraw funds, deposit funds (requires JWT)

***

### Security Validation

The server validates each request:

* **Timestamp** - Must be within 3 minutes of server time
* **Nonce** - Must be unique (prevents replay attacks)
* **Signature** - Must be valid Ed25519 signature
* **Wallet status** - Must be active (not revoked/expired)

***

### Error Reference

* `missing API wallet authentication headers` - Include all 4 required headers
* `signature expired or invalid timestamp` - Use current Unix timestamp
* `authentication failed` - Check signing algorithm and key format
* `wallet expired or revoked` - Create a new wallet
* `nonce already used` - Generate new UUID for each request

***

### Best Practices

**Key Management**

* Store private keys in environment variables or secure key management systems
* Never commit private keys to version control
* Use different wallets for different bots/environments

**Request Handling**

* Generate a fresh UUID nonce for every request
* Sync your system clock with NTP servers

**Rotation & Monitoring**

* Rotate API wallets every 90-180 days
* Revoke immediately if compromised