/**
 * Withdrawal Flow (2 steps):
 *
 * 1. POST /v2/withdraw/quote  - Get quote with fee and message_to_sign
 * 2. POST /v2/withdraw        - Confirm with user's wallet signature
 *
 * IMPORTANT: The backend automatically uses the account's registered wallet
 * address as the recipient. No recipient_address field is accepted — this
 * prevents API wallet holders from redirecting withdrawals.
 */

import { API_BASE_URL } from "./config";
import { authenticatedFetch } from "./auth";

// ── Step 1: Get Withdraw Quote ──

export interface WithdrawQuoteParams {
  usd_value: string; // USD amount to withdraw
  blockchain: "ethereum" | "solana" | "cardano";
  asset?: string; // e.g. "ADA", "USDC" — defaults to chain's native asset
}

export interface WithdrawQuoteResponse {
  withdraw_id: string;
  fee: string; // withdrawal fee in USD
  message_to_sign: string; // human-readable message for wallet signing
}

/**
 * Step 1: Get a withdrawal quote.
 * Returns a message for the user to sign in their wallet.
 *
 * @example
 *   const quote = await getWithdrawQuote({
 *     usd_value: "500",
 *     blockchain: "solana",
 *     asset: "USDC",
 *   });
 *   // quote.withdraw_id = "def-456"
 *   // quote.fee = "0.50"
 *   // quote.message_to_sign = "Authorize withdrawal from Strike..."
 *   // -> User signs this message with their wallet
 */
export async function getWithdrawQuote(
  params: WithdrawQuoteParams
): Promise<WithdrawQuoteResponse> {
  const res = await authenticatedFetch(
    `${API_BASE_URL}/v2/withdraw/quote`,
    "POST",
    params
  );

  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || `Withdraw quote failed: ${res.status}`);
  }

  return res.json();
}

// ── Step 2: Confirm Withdrawal ──

export interface WithdrawConfirmParams {
  withdraw_id: string;
  wallet_signature: string; // user's wallet signature over message_to_sign
}

export interface WithdrawConfirmResponse {
  request_id: string;
  account_id: string;
  status: "pending" | "completed" | "failed";
  sequence_id: number;
  message_id: string;
}

/**
 * Step 2: Confirm the withdrawal with the user's wallet signature.
 *
 * Signature format by chain:
 *   Ethereum: hex-encoded EIP-191 personal sign
 *   Solana:   base58-encoded Ed25519 signature
 *   Cardano:  CIP-30 COSE format ({coseSign1Hex}:{coseKeyHex})
 *
 * @example
 *   const result = await confirmWithdraw({
 *     withdraw_id: "def-456",
 *     wallet_signature: "0xdef...",
 *   });
 *   // result.status = "pending"
 */
export async function confirmWithdraw(
  params: WithdrawConfirmParams
): Promise<WithdrawConfirmResponse> {
  const res = await authenticatedFetch(
    `${API_BASE_URL}/v2/withdraw`,
    "POST",
    params
  );

  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || `Withdraw confirm failed: ${res.status}`);
  }

  return res.json();
}
