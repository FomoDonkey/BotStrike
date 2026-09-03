<!-- source: https://docs.strikefinance.org/api/builder-codes.md -->
> For the complete documentation index, see [llms.txt](https://docs.strikefinance.org/llms.txt). Markdown versions of documentation pages are available by appending `.md` to page URLs; this page is available as [Markdown](https://docs.strikefinance.org/api/builder-codes.md).

# Builder Codes

## Builder Integration Guide

Integrate Strike's perpetual futures trading into your dApp and earn fees on every trade your users make. Your users get the same trading infrastructure — you earn basis points on their order fills, credited instantly to your account balance, withdrawable anytime.

Strike supports **Ethereum**, **Solana**, and **Cardano** — your users can deposit, trade, and withdraw from any supported chain.

All existing trading, data query, and WebSocket endpoints work the same way with API wallet authentication. The only addition is the `X-Builder-Fee-Bps` header on order requests to collect your fee:

We've created an repo as a guide to help you get started: <https://github.com/strike-finance/strike-builder-reference>

```http
POST /v2/order
Headers:
X-API-Wallet-Public-Key: <your-key>
X-API-Wallet-Signature: <signature>
X-API-Wallet-Timestamp: <timestamp>
X-API-Wallet-Nonce: <uuid>
X-Builder-Fee-Bps: 50

{
"symbol": "BTC-USD",
"side": "BUY",
"type": "LIMIT",
"size": "0.01",
"price": "50000"
}
```

***

### 1. Register as a Builder

Register through the Strike dashboard to get your unique builder code and configure your fee share (max 100 BPS / 1%).

Register at [Strike Builder Dashboard](http://app.strikefinance.org/builder-codes)

Once registered, you'll have:

* A unique **builder code** linked to your account
* A configured **fee share** (in BPS) — the maximum fee you can charge per order

***

### 2. Onboarding Users

Before a user can trade through your dApp, they need a Strike account and an **API wallet** — a key pair that lets your dApp act on their behalf.

This happens through a two-step wallet connect flow. Your dApp requests a signature challenge, the user signs it in their wallet, and Strike returns API wallet credentials that your dApp stores and uses for all subsequent requests.

{% hint style="info" %}
The API wallet created here is **not** the same as the personal API wallets on the API Wallets page. Those are for users trading directly. The builder connect flow creates a special API wallet linked to your builder code — it lets your dApp place orders, deposit, and withdraw on behalf of the user, while automatically associating all trades with your builder code for fee collection.
{% endhint %}

#### Step 1: Request Signature Challenge

Your dApp generates an Ed25519 key pair and sends the public key along with the user's wallet address:

```http
POST /auth/builder/request-signature

{
"address": "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM",
"chain": "solana",
"code": "your-builder-code",
"fee_share_bps": 50,
"public_key": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
}
```

Response:

```json
{
"nonce": "019d4a2b-1234-7890-abcd-ef1234567890",
"message_to_sign": "Sign this message to connect to Strike via your-builder-code..."
}
```

| Field | Description |
| --------------- | -------------------------------------------------------------------------------------- |
| `address` | User's wallet address on the specified chain |
| `chain` | `"ethereum"`, `"solana"`, or `"cardano"` |
| `code` | Your builder code from registration |
| `fee_share_bps` | Maximum fee (in BPS) the user is approving you to charge (0–100) |
| `public_key` | Ed25519 public key your dApp generated (64 hex chars). This becomes the API wallet key |

#### Step 2: Verify Signature

The user signs the `message_to_sign` in their wallet (Phantom, Rabby, Nami, etc.), then your dApp submits the signature:

```http
POST /auth/builder/verify-signature

{
"address": "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM",
"chain": "solana",
"nonce": "019d4a2b-1234-7890-abcd-ef1234567890",
"wallet_signature": "0xabc123..."
}
```

Response:

```json
{
"account_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
"builder_code": "your-builder-code",
"fee_share_bps": 50,
"api_wallet_id": 123456789,
"api_wallet_public_key": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
"api_wallet_created_at": "2026-04-02T12:34:56Z"
}
```

**Store the `api_wallet_public_key` and the corresponding private key you generated** — you'll use them to sign and authenticate all subsequent requests for this user.

{% hint style="warning" %}
The wallet signature format differs by chain:

* **Ethereum** — EIP-191 personal\_sign (65-byte hex)
* **Solana** — Ed25519 signature (base58-encoded)
* **Cardano** — CIP-30 format: `"{coseSign1Hex}:{coseKeyHex}"`
{% endhint %}

{% hint style="info" %}
For full endpoint details, see the API Reference.
{% endhint %}

***

### 3. Authentication

Every request your dApp makes on behalf of a user is authenticated using the API wallet created during onboarding. You sign each request with the Ed25519 private key you generated, and include these four headers:

| Header | Description |
| ------------------------- | ----------------------------------------------------- |
| `X-API-Wallet-Public-Key` | The Ed25519 public key from onboarding (64 hex chars) |
| `X-API-Wallet-Signature` | Ed25519 signature of the request message |
| `X-API-Wallet-Timestamp` | Current Unix timestamp (seconds) |
| `X-API-Wallet-Nonce` | Unique UUID per request (prevents replay attacks) |

Your API wallet is linked to your builder code automatically — no separate builder code header is needed.

#### Signature Message Format

```
{METHOD}:{PATH}:{TIMESTAMP}:{NONCE}:{BODY_HASH}
```

* `METHOD` — HTTP method (uppercase): `GET`, `POST`, `DELETE`
* `PATH` — Full path with query string: `/v2/order?symbol=BTC-USD`
* `TIMESTAMP` — Unix timestamp in seconds
* `NONCE` — UUID v4
* `BODY_HASH` — SHA-256 hex digest of the JSON body (for GET requests, hash an empty string)

#### Example

```http
GET /v2/positions
Headers:
X-API-Wallet-Public-Key: a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2
X-API-Wallet-Signature: <ed25519-signature-hex>
X-API-Wallet-Timestamp: 1712054096
X-API-Wallet-Nonce: 550e8400-e29b-41d4-a716-446655440000
```

{% hint style="info" %}
For complete code examples in Python, TypeScript, Go, and Rust, see the API Wallet Authentication Reference.
{% endhint %}

***

### 4. Deposits

Users can deposit from any supported chain. The flow builds an unsigned transaction server-side, the user signs it in their wallet, and your dApp submits the signed transaction hash.

**Supported chains and assets:**

| Chain | Assets | Amount unit |
| -------- | --------- | -------------------------------- |
| Ethereum | ETH, USDC | Wei (1 ETH = 10^18 wei) |
| Solana | SOL, USDC | Lamports (1 SOL = 10^9 lamports) |
| Cardano | ADA | Lovelace (1 ADA = 10^6 lovelace) |

Minimum deposit: **$5 USD**

#### Step 1: Request Deposit Quote

```http
POST /v2/deposit/quote
Headers: [API Wallet Auth Headers]

{
"blockchain": "solana",
"asset_symbol": "USDC",
"asset_amount": "10000000"
}
```

Response:

```json
{
"request_id": "dep_abc123",
"quote": {
"account_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
"request_id": "dep_abc123",
"blockchain": "solana",
"asset_symbol": "USDC",
"asset_amount": "10000000",
"usd_value": "10.00",
"exchange_rate": "1.00",
"confirmations": 32,
"timestamp": 1712054096000,
"expiration_at": 1712054996000
},
"signature": "server-signature...",
"deposit_address": "StrikeVau1t...",
"confirmations_required": 32
}
```

#### Step 2: Build Transaction

Strike builds an unsigned transaction for the user's chain. Your dApp passes it to the user's wallet for signing.

```http
POST /v2/deposit/build-tx
Headers: [API Wallet Auth Headers]

{
"request_id": "dep_abc123",
"user_address": "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"
}
```

{% hint style="warning" %}
**Cardano only:** You must include a `utxos` field — an array of hex-encoded CIP-30 UTXOs from the user's wallet.

```json
{
"request_id": "dep_abc123",
"user_address": "addr1q...",
"utxos": ["82825820...", "82825820..."]
}
```

{% endhint %}

The response format varies by chain:

{% tabs %}
{% tab title="Solana" %}

```json
{
"blockchain": "solana",
"unsigned_tx": "<base64-encoded-transaction>",
"format": "solana_base64",
"expires_at": 1712054996000
}
```

{% endtab %}

{% tab title="Ethereum" %}

```json
{
"blockchain": "ethereum",
"unsigned_tx": {
"from": "0xUserAddress...",
"to": "0xStrikeVault...",
"data": "0x...",
"value": "0x...",
"gas": "0x..."
},
"format": "evm_tx_params",
"approval_tx": { ... }
}
```

`approval_tx` is included only for ERC-20 tokens (e.g. USDC) that need an allowance approval first.
{% endtab %}

{% tab title="Cardano" %}

```json
{
"blockchain": "cardano",
"unsigned_tx": "<cbor-hex-encoded-transaction>",
"format": "cardano_cbor",
"expires_at": 1712054996000
}
```

{% endtab %}
{% endtabs %}

The user signs this transaction in their wallet. Strike controls the deposit address, so the user always sees the real destination before confirming.

#### Step 3: Submit Signed Transaction

After the user signs, submit the blockchain transaction hash:

```http
POST /v2/deposit
Headers: [API Wallet Auth Headers]

{
"request_id": "dep_abc123",
"tx_hash": "5UxGq..."
}
```

Response:

```json
{
"request_id": "dep_abc123",
"account_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
"status": "pending",
"tx_hash": "5UxGq...",
"message": "Deposit submission confirmed, awaiting blockchain confirmation"
}
```

The user's balance is credited after the required blockchain confirmations.

{% hint style="info" %}
For full endpoint details, see the API Reference.
{% endhint %}

***

### 5. Withdrawals

Users can withdraw to their registered wallet on any supported chain. The recipient address is locked server-side to the wallet the user connected with during onboarding — your dApp cannot redirect funds.

**Supported chains:** Ethereum, Solana, Cardano

Minimum withdrawal: **$5 USD** | Withdrawal fee: **$1 USD** (flat, all chains)

#### Step 1: Request Withdrawal Quote

```http
POST /v2/withdraw/quote
Headers: [API Wallet Auth Headers]

{
"usd_value": "50.00",
"blockchain": "solana",
"asset": "USDC"
}
```

Response:

```json
{
"withdraw_id": "wd_xyz789",
"fee": "1.00",
"message_to_sign": "Withdraw $50.00 from Strike to 9WzDXw... on solana..."
}
```

| Field | Description |
| ------------ | ------------------------------------------------------------------------------------------------------ |
| `usd_value` | Amount to withdraw in USD (required) |
| `blockchain` | `"ethereum"`, `"solana"`, or `"cardano"` (required) |
| `asset` | Withdrawal asset (e.g. `"USDC"`, `"SOL"`, `"ADA"`, `"ETH"`). Defaults to chain native asset if omitted |

The `message_to_sign` includes the destination address, amount, and chain — the user confirms by signing it in their wallet.

#### Step 2: Submit Wallet Signature

```http
POST /v2/withdraw
Headers: [API Wallet Auth Headers]

{
"withdraw_id": "wd_xyz789",
"wallet_signature": "0xdef456..."
}
```

Response:

```json
{
"request_id": "wd_xyz789",
"account_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
"status": "pending"
}
```

{% hint style="warning" %}
The wallet signature here is the **user's wallet signature** (same wallet they connected with during onboarding), not the API wallet signature. This ensures the user explicitly authorizes every withdrawal.
{% endhint %}

{% hint style="info" %}
For full endpoint details, see the API Reference.
{% endhint %}

***

### 6. Trading — Earning Fees

Everything works exactly like normal trading. The only addition is the `X-Builder-Fee-Bps` header on order requests to earn your fee.

{% tabs %}
{% tab title="Normal Order" %}

```http
POST /v2/order
Headers: [API Wallet Auth Headers]

{
"symbol": "BTC-USD",
"side": "BUY",
"type": "LIMIT",
"size": "0.01",
"price": "50000"
}
```

{% endtab %}

{% tab title="Builder Order" %}

```http
POST /v2/order
Headers: [API Wallet Auth Headers]
X-Builder-Fee-Bps: 50

{
"symbol": "BTC-USD",
"side": "BUY",
"type": "LIMIT",
"size": "0.01",
"price": "50000"
}
```

{% endtab %}
{% endtabs %}

**Fee rules:**

* X-Builder-Fee-Bps must be <= the user's approved `fee_share_bps` from onboarding
* `X-Builder-Fee-Bps` must be <= your configured `fee_share_bps` from registration
* Fee is charged when the order fills, not when placed
* Earnings are credited to your Strike account balance instantly
* If the fee exceeds the user's approved fee\_share\_bps, your configured `fee_share_bps`, or the deployment cap, the order is rejected with 400 and is not placed.

{% hint style="info" %}
For full endpoint details, see the API Reference.
{% endhint %}

***

### 7. All Other Endpoints

All existing trading, data query, and WebSocket endpoints work exactly the same with API wallet authentication. Positions, balances, order history, fills, funding history, and real-time WebSocket streams are all accessible — just authenticate with your API wallet headers as described above.

{% hint style="info" %}
For the full list of available endpoints, see the API Reference.
{% endhint %}