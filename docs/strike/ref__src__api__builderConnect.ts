/**
 * Builder Connect Flow (2 steps):
 *
 * 1. POST /auth/builder/request-signature
 *    - Send: address, chain, builder code, public_key, max_fee_bps
 *    - Receive: nonce + message_to_sign
 *
 * 2. POST /auth/builder/verify-signature
 *    - Send: address, chain, nonce, wallet_signature
 *    - Receive: account_id, api_wallet_id, etc.
 *
 * The dApp generates an Ed25519 keypair client-side. The public_key
 * is sent in step 1. The private key is stored locally and used to
 * sign all subsequent API requests.
 */

import { API_BASE_URL } from "./config";
import type { BuilderConnectResult } from "../types/account";

export interface RequestSignatureParams {
  address: string;
  chain: "ethereum" | "solana" | "cardano";
  publicKey: string; // 64 hex chars (Ed25519)
  builderCode: string; // your builder code (e.g. "gero", "minswap")
  maxFeeBps?: number; // 0-100 (basis points), defaults to 0
}

export interface RequestSignatureResult {
  nonce: string;
  message_to_sign: string;
}

/**
 * Step 1: Request a signature challenge.
 *
 * @example
 *   const { nonce, message_to_sign } = await requestSignature({
 *     address: "addr1qx2fxv2...",
 *     chain: "cardano",
 *     publicKey: "a1b2c3d4...",
 *     maxFeeBps: 50,
 *   });
 *   // User signs message_to_sign with their wallet
 */
export async function requestSignature(
  params: RequestSignatureParams
): Promise<RequestSignatureResult> {
  const res = await fetch(`${API_BASE_URL}/auth/builder/request-signature`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      address: params.address,
      chain: params.chain,
      code: params.builderCode,
      max_fee_bps: params.maxFeeBps ?? 0,
      public_key: params.publicKey,
    }),
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || `Request signature failed: ${res.status}`);
  }

  return res.json();
}

export interface VerifySignatureParams {
  address: string;
  chain: "ethereum" | "solana" | "cardano";
  nonce: string;
  walletSignature: string;
}

/**
 * Step 2: Verify the wallet signature and complete the connect flow.
 *
 * On success, the backend:
 * 1. Creates a Strike account if the user is new
 * 2. Approves the builder for this account
 * 3. Creates a new API wallet linked to the builder code
 *
 * @example
 *   const result = await verifySignature({
 *     address: "addr1qx2fxv2...",
 *     chain: "cardano",
 *     nonce: "019d4a2b-...",
 *     walletSignature: "845846a2...",
 *   });
 *   // result.account_id = "a1b2c3d4-..."
 *   // result.api_wallet_public_key = "a1b2c3d4..."
 */
export async function verifySignature(
  params: VerifySignatureParams
): Promise<BuilderConnectResult> {
  const res = await fetch(`${API_BASE_URL}/auth/builder/verify-signature`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      address: params.address,
      chain: params.chain,
      nonce: params.nonce,
      wallet_signature: params.walletSignature,
    }),
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || `Verify signature failed: ${res.status}`);
  }

  return res.json();
}
