<!-- source: https://docs.strikefinance.org/api/vault-leader.md -->
> For the complete documentation index, see [llms.txt](https://docs.strikefinance.org/llms.txt). Markdown versions of documentation pages are available by appending `.md` to page URLs; this page is available as [Markdown](https://docs.strikefinance.org/api/vault-leader.md).

# Vault Leader

Vaults allow leaders to manage pooled capital and trade on behalf of depositors. If you're already trading via API keys, trading on behalf of your vault requires minimal changes — the same endpoints, same authentication, just an additional `vault_id` field.

### Authentication

Your existing API key works for vault trading. No separate credentials are needed.

When you created your vault, it was linked to your account. The API resolves your API key to your account, and when you include a `vault_id` in your request, the system verifies that you are the leader of that vault before executing the trade on the vault's account.

### Getting Vault id

```
GET /v2/vaults?leader_account_id=<your-account-id>
```

**Response:**

```json
{
"vaults": [
{
"vault_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
"name": "My Trading Vault",
"leader_account_id": "account_123",
"status": "active",
"equity": "10500.00",
"share_price": "1.05"
}
]
}
```

### Trading on Behalf of Your Vault

All trading endpoints accept an optional `vault_id` field in the request body. When provided, the order is placed using the vault's account instead of your personal account.

#### Example: Placing an Order

**Personal account order:**

```json
POST /v2/order

{
"symbol": "BTC-USD",
"side": "BUY",
"type": "LIMIT",
"size": "0.01",
"price": "50000"
}
```

**Vault order — same request, just add `vault_id`:**

```json
POST /v2/order

{
"symbol": "BTC-USD",
"side": "BUY",
"type": "LIMIT",
"size": "0.01",
"price": "50000",
"vault_id": "your-vault-id"
}
```

That's it. The system checks that your API key belongs to the vault's leader and that the vault is active, then places the order on the vault's account.

#### Supported Trading Endpoints

The following endpoints all accept `vault_id` in the request body:

| Endpoint | Method | Description |
| ------------------------- | ------ | ------------------------------- |
| `/v2/order` | POST | Create order |
| `/v2/orders/batch` | POST | Create multiple orders |
| `/v2/order/cancel` | DELETE | Cancel order |
| `/v2/order/cancel-all` | DELETE | Cancel all orders |
| `/v2/order/replace` | POST | Replace order (cancel + create) |
| `/v2/order/replace-batch` | POST | Replace multiple orders |
| `/v2/order/strategy` | POST | Create strategy order (TP/SL) |
| `/v2/leverage` | POST | Update leverage |
| `/v2/marginMode` | POST | Update margin mode |
| `/v2/isoMargin` | POST | Modify isolated margin |

### Querying Vault Data

To read your vault's positions, balances, or order history, pass `vault_id` as a **query parameter** on the data endpoints.

#### Example: Getting Vault Positions

**Personal positions:**

```
GET /v2/positions
```

**Vault positions:**

```
GET /v2/positions?vault_id=your-vault-id
```

#### Supported Data Endpoints

| Endpoint | Method | Description |
| ------------------------- | ------ | ------------------- |
| `/v2/account` | GET | Account info |
| `/v2/balances` | GET | Balances |
| `/v2/portfolio` | GET | Portfolio summary |
| `/v2/positions` | GET | Open positions |
| `/v2/closedPositions` | GET | Closed positions |
| `/v2/openOrders` | GET | Open orders |
| `/v2/order` | GET | Get specific order |
| `/v2/order/strategy` | GET | Get strategy order |
| `/v2/trades` | GET | Trade history |
| `/v2/history/order` | GET | Order history |
| `/v2/history/fill` | GET | Fill history |
| `/v2/history/funding` | GET | Funding history |
| `/v2/history/transaction` | GET | Transaction history |

The response format is identical whether you're querying personal or vault data.

### WebSocket Streams

To receive real-time updates for your vault, connect to the WebSocket and subscribe with `vault_id`.

#### Connecting for Personal Updates

```json
{
"method": "subscribe",
"channel": "userstream",
"id": 1
}
```

#### Connecting for Vault Updates

```json
{
"method": "subscribe",
"channel": "userstream",
"vault_id": "your-vault-id",
"id": 1
}
```

Once subscribed, you receive the same event types as a personal account stream — order updates, position updates, balance changes — but for the vault's account.

### Quick Reference

| | Personal Account | Vault |
| -------------------- | -------------------------------- | ---------------------------------------------- |
| **Auth** | API key | Same API key |
| **Place orders** | `POST /v2/order` | `POST /v2/order` with `vault_id` in body |
| **Query data** | `GET /v2/positions` | `GET /v2/positions?vault_id=xxx` |
| **Stream subscribe** | `{"channel": "userstream"}` | `{"channel": "userstream", "vault_id": "xxx"}` |
| **Events** | Order, position, balance updates | Same + vault lifecycle events |