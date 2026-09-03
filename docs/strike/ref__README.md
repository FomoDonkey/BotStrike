# Strike Builder Reference

A standalone React + TypeScript reference app for integrating with Strike Finance via the **Builder Codes API**.

Demonstrates the complete integration flow:
- Builder connect (wallet signing, Ed25519 API wallet)
- Deposits (3-step: quote -> build-tx -> confirm)
- Withdrawals (2-step: quote -> sign -> confirm)
- Order placement (market, limit, strategy with TP/SL)
- Real-time WebSocket data (order book, mark prices, positions)
- All trading math (PnL, liquidation, margin tiers, VWAP, fees)

## Quick Start

```bash
npm install
npm run dev
```

Open http://localhost:5173

## Configuration

Create a `.env` file or set environment variables:

```env
VITE_API_URL=https://api.strikefinance.org
VITE_PRICE_WS_URL=wss://v2.strikefinance.org/ws/stream
VITE_USER_WS_URL=wss://v2.strikefinance.org/ws
VITE_BUILDER_CODE=your-builder-code
```

## Project Structure

```
src/
  types/           TypeScript interfaces for all data structures
  math/            Pure calculation functions (no React dependencies)
    positionCalculations.ts   PnL, liquidation price, margin, leverage
    marginTiers.ts            Tier lookups for MMR, max leverage
    orderBookUtils.ts         VWAP market fill estimation, grouping
    balanceCalculations.ts    Available balance, withdrawable, margin balance
    formatUtils.ts            Price/currency/size formatting
  api/             REST API integration
    auth.ts                   Ed25519 keypair + API wallet header signing
    builderConnect.ts         Builder connect flow (2 steps)
    deposit.ts                Deposit flow (3 steps)
    withdraw.ts               Withdrawal flow (2 steps)
    orders.ts                 Order placement, cancel, replace
    account.ts                Account, positions, markets, depth
  ws/              WebSocket connections
    priceStream.ts            Public: markPrice, depth, trade, kline
    userStream.ts             Authenticated: positions, orders, balance
  hooks/           React hooks
    useMarkets.ts             Fetch market config
    usePriceStream.ts         Live prices + depth with throttling
    useUserStream.ts          Live positions + orders + balance
    usePositions.ts           Enrich positions with derived fields
  components/      UI components
    ConnectPanel.tsx           Builder connect flow
    DepositPanel.tsx           Deposit flow
    WithdrawPanel.tsx          Withdrawal flow
    OrderBook.tsx              Order book with grouping + depth bars
    PositionsTable.tsx         Positions with all derived fields
    OrderForm.tsx              Order form with est. entry, fees, margin
    AccountPanel.tsx           Balance display
    PriceBar.tsx               Mark price, funding rate, countdown
```

## API Authentication

All authenticated requests use **API wallet headers** (not JWT):

```
X-API-Wallet-Public-Key: <64 hex chars>
X-API-Wallet-Signature: <Ed25519 signature>
X-API-Wallet-Timestamp: <unix seconds>
X-API-Wallet-Nonce: <uuid>
```

Signature message format:
```
{METHOD}:{PATH}:{TIMESTAMP}:{NONCE}:{SHA256(body)}
```

See `src/api/auth.ts` for the full implementation.

## Key Formulas

All formulas are in `src/math/` with JSDoc examples. Key ones:

**Unrealized PnL:**
```
Long:  uPnL = (markPrice - entryPrice) * size
Short: uPnL = -(markPrice - entryPrice) * size
```

**Liquidation Price (Isolated):**
```
LP = (EP - (IsoBalance + MA) / Size) / (1 - Direction * MMR)
```

**Liquidation Price (Cross):**
```
LP = (EP - (W + TU - TM + MA) / Size) / (1 - Direction * MMR)
```

**Maintenance Margin:**
```
maintenanceMargin = notional * MMR - MA
```

See `docs/INTEGRATOR_GUIDE.md` in the main repo for worked numerical examples.

## Builder Codes API Flow

### Connect
1. Generate Ed25519 keypair client-side
2. `POST /auth/builder/request-signature` -> challenge message
3. User signs with wallet
4. `POST /auth/builder/verify-signature` -> account_id + api_wallet

### Deposit
1. `POST /v2/deposit/quote` -> quote with vault address
2. `POST /v2/deposit/build-tx` -> unsigned transaction
3. User signs + submits on-chain
4. `POST /v2/deposit` -> confirm with tx hash

### Withdraw
1. `POST /v2/withdraw/quote` -> message_to_sign + fee
2. User signs message with wallet
3. `POST /v2/withdraw` -> confirm with wallet signature

### Trade
```
POST /v2/order          - place order
POST /v2/order/strategy - order with TP/SL
POST /v2/order/replace  - atomic cancel + create
DELETE /v2/order/cancel  - cancel order
```
