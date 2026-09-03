/**
 * Deposit Flow (3 steps):
 *
 * 1. POST /v2/deposit/quote      - Get quote with exchange rate and vault address
 * 2. POST /v2/deposit/build-tx   - Get unsigned transaction for user to sign
 * 3. POST /v2/deposit            - Confirm with on-chain tx hash
 *
 * All steps require API wallet authentication headers.
 */

import { API_BASE_URL } from "./config";
import { authenticatedFetch } from "./auth";

// ── Step 1: Get Deposit Quote ──

export interface DepositQuoteParams {
  blockchain: "ethereum" | "solana" | "cardano";
  asset_symbol: string; // "USDC", "SOL", "ADA", "ETH"
  asset_amount: string; // in smallest unit (lamports, lovelace, wei)
}

export interface DepositQuote {
  account_id: string;
  blockchain: string;
  asset_symbol: string;
  asset_amount: string;
  usd_value: string;
  exchange_rate: string;
  confirmations: number;
  timestamp: number;
  expiration_at: number;
}

export interface DepositQuoteResponse {
  request_id: string;
  quote: DepositQuote;
  deposit_address: string; // Strike's vault address (hardcoded per chain)
  confirmations_required: number;
  signature: string;
}

/**
 * Step 1: Get a deposit quote.
 *
 * @example
 *   const quote = await getDepositQuote({
 *     blockchain: "solana",
 *     asset_symbol: "USDC",
 *     asset_amount: "1000000000",  // 1000 USDC (6 decimals)
 *   });
 *   // quote.request_id = "abc-123"
 *   // quote.deposit_address = "StrikeVau1t..."
 *   // quote.quote.usd_value = "1000"
 */
export async function getDepositQuote(
  params: DepositQuoteParams
): Promise<DepositQuoteResponse> {
  const res = await authenticatedFetch(
    `${API_BASE_URL}/v2/deposit/quote`,
    "POST",
    params
  );

  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || `Deposit quote failed: ${res.status}`);
  }

  return res.json();
}

// ── Step 2: Build Unsigned Transaction ──

export interface BuildTxParams {
  request_id: string; // from quote response
  user_address: string; // user's wallet address
  utxos?: string[]; // CIP-30 hex-encoded UTXOs (Cardano only)
}

export interface BuildTxResponseEVM {
  blockchain: "ethereum";
  unsigned_tx: {
    from: string;
    to: string;
    data: string;
    value: string;
    gas: string;
  };
  format: "evm_tx_params";
  approval_tx?: {
    from: string;
    to: string;
    data: string;
    value: string;
    gas: string;
  };
}

export interface BuildTxResponseSolana {
  blockchain: "solana";
  unsigned_tx: string; // Base64-encoded transaction
  format: "solana_base64";
  expires_at: number;
}

export interface BuildTxResponseCardano {
  blockchain: "cardano";
  unsigned_tx: string; // CBOR hex-encoded transaction
  format: "cardano_cbor";
  expires_at: number;
}

export type BuildTxResponse =
  | BuildTxResponseEVM
  | BuildTxResponseSolana
  | BuildTxResponseCardano;

/**
 * Step 2: Build an unsigned transaction.
 * The user's wallet signs this, then submits to the blockchain.
 *
 * Response format varies by chain:
 *   Ethereum: JSON object with from, to, data, value, gas
 *   Solana:   Base64-encoded transaction
 *   Cardano:  CBOR hex-encoded transaction
 *
 * @example
 *   const tx = await buildDepositTx({
 *     request_id: "abc-123",
 *     user_address: "9WzDXwBhzVFT...",
 *   });
 *
 *   if (tx.format === "solana_base64") {
 *     const decoded = Buffer.from(tx.unsigned_tx, "base64");
 *     const signed = await wallet.signTransaction(decoded);
 *     const txHash = await connection.sendRawTransaction(signed.serialize());
 *   }
 */
export async function buildDepositTx(
  params: BuildTxParams
): Promise<BuildTxResponse> {
  const res = await authenticatedFetch(
    `${API_BASE_URL}/v2/deposit/build-tx`,
    "POST",
    params
  );

  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || `Build TX failed: ${res.status}`);
  }

  return res.json();
}

// ── Step 3: Confirm Deposit ──

export interface DepositConfirmParams {
  request_id: string;
  tx_hash: string; // on-chain transaction hash
}

export interface DepositConfirmResponse {
  request_id: string;
  account_id: string;
  status: "pending";
  tx_hash: string;
  message: string;
}

/**
 * Step 3: Confirm the deposit with the on-chain tx hash.
 *
 * @example
 *   const result = await confirmDeposit({
 *     request_id: "abc-123",
 *     tx_hash: "5abc123def...",
 *   });
 *   // result.status = "pending"
 *   // Backend monitors blockchain for confirmations
 */
export async function confirmDeposit(
  params: DepositConfirmParams
): Promise<DepositConfirmResponse> {
  const res = await authenticatedFetch(
    `${API_BASE_URL}/v2/deposit`,
    "POST",
    params
  );

  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || `Deposit confirm failed: ${res.status}`);
  }

  return res.json();
}
