/**
 * API Wallet Authentication for Builder Codes.
 *
 * Every authenticated request uses 4 headers:
 *   X-API-Wallet-Public-Key: <64 hex chars, Ed25519 public key>
 *   X-API-Wallet-Signature: <Ed25519 signature of message>
 *   X-API-Wallet-Timestamp: <unix seconds>
 *   X-API-Wallet-Nonce: <uuid>
 *
 * Signature message format:
 *   {METHOD}:{PATH}:{TIMESTAMP}:{NONCE}:{SHA256(body)}
 *
 * The Ed25519 keypair is generated client-side during the connect flow
 * and stored locally. The private key signs all subsequent API requests.
 */

import * as ed from "@noble/ed25519";

// @noble/ed25519 v2 requires sha512 configured for sync operations.
import { sha512 } from "@noble/hashes/sha2.js";
if (ed.etc) {
  ed.etc.sha512Sync = (...m: Uint8Array[]) => {
    const h = sha512.create();
    for (const msg of m) h.update(msg);
    return h.digest();
  };
}

// ── Key Management ──

export interface ApiWalletKeys {
  publicKey: string; // 64 hex chars
  privateKey: string; // 64 hex chars
}

/**
 * Generate a new Ed25519 keypair for API wallet authentication.
 * Call this once during the builder connect flow.
 */
export function generateApiWalletKeys(): ApiWalletKeys {
  const privateKeyBytes = ed.utils.randomPrivateKey();
  const publicKeyBytes = ed.getPublicKey(privateKeyBytes);

  return {
    privateKey: bytesToHex(privateKeyBytes),
    publicKey: bytesToHex(publicKeyBytes),
  };
}

// ── In-memory key storage (no localStorage) ──

let _currentKeys: ApiWalletKeys | null = null;

export function storeApiWalletKeys(keys: ApiWalletKeys): void {
  _currentKeys = keys;
}

export function loadApiWalletKeys(): ApiWalletKeys | null {
  return _currentKeys;
}

export function clearApiWalletKeys(): void {
  _currentKeys = null;
}

// ── Request Signing ──

/**
 * Build the 4 authentication headers for an API request.
 *
 * @param method     - HTTP method (GET, POST, DELETE)
 * @param path       - URL path (e.g. "/v2/deposit/quote")
 * @param body       - Request body string (empty string for GET)
 * @param privateKey - Ed25519 private key (64 hex chars)
 * @param publicKey  - Ed25519 public key (64 hex chars)
 *
 * @example
 *   const headers = await signRequest("POST", "/v2/deposit/quote", '{"blockchain":"solana"}', privKey, pubKey);
 *   // headers = {
 *   //   "X-API-Wallet-Public-Key": "a1b2c3...",
 *   //   "X-API-Wallet-Signature": "def456...",
 *   //   "X-API-Wallet-Timestamp": "1700000000",
 *   //   "X-API-Wallet-Nonce": "550e8400-e29b-41d4-a716-446655440000"
 *   // }
 */
export async function signRequest(
  method: string,
  path: string,
  body: string,
  privateKey: string,
  publicKey: string
): Promise<Record<string, string>> {
  const timestamp = Math.floor(Date.now() / 1000).toString();
  const nonce = crypto.randomUUID();

  // SHA-256 hash of the body (always hash, even empty string for GET)
  const bodyBytes = new TextEncoder().encode(body);
  const bodyHash = bytesToHex(
    new Uint8Array(await crypto.subtle.digest("SHA-256", bodyBytes))
  );

  // Message: {METHOD}:{PATH}:{TIMESTAMP}:{NONCE}:{BODY_HASH}
  const message = `${method.toUpperCase()}:${path}:${timestamp}:${nonce}:${bodyHash}`;

  // Sign with Ed25519
  const messageBytes = new TextEncoder().encode(message);
  const privateKeyBytes = hexToBytes(privateKey);
  const signature = ed.sign(messageBytes, privateKeyBytes);

  const sigHex = bytesToHex(signature);

  // Debug logging
  console.log("[Auth] ── Request Signing ──");
  console.log("[Auth] Method:", method.toUpperCase());
  console.log("[Auth] Path:", path);
  console.log("[Auth] Timestamp:", timestamp);
  console.log("[Auth] Nonce:", nonce);
  console.log("[Auth] Body:", body ? body.slice(0, 200) : "(empty)");
  console.log("[Auth] BodyHash:", bodyHash || "(empty)");
  console.log("[Auth] Message to sign:", message);
  console.log("[Auth] PublicKey:", publicKey);
  console.log("[Auth] Signature:", sigHex);
  console.log("[Auth] Signature length:", sigHex.length, "(expected 128)");
  console.log("[Auth] PrivKey length:", privateKey.length, "(expected 64)");
  console.log("[Auth] PubKey length:", publicKey.length, "(expected 64)");

  // Verify our own signature
  try {
    const valid = ed.verify(signature, messageBytes, hexToBytes(publicKey));
    console.log("[Auth] Self-verify:", valid);
  } catch (e) {
    console.error("[Auth] Self-verify FAILED:", e);
  }

  return {
    "X-API-Wallet-Public-Key": publicKey,
    "X-API-Wallet-Signature": sigHex,
    "X-API-Wallet-Timestamp": timestamp,
    "X-API-Wallet-Nonce": nonce,
  };
}

/**
 * Make an authenticated API request using API wallet headers.
 * If the server returns 401, clears stored session (keys are invalid/expired).
 */
export async function authenticatedFetch(
  url: string,
  method: string,
  body?: object
): Promise<Response> {
  const keys = loadApiWalletKeys();
  if (!keys) throw new Error("No API wallet keys found. Connect first.");

  const parsedUrl = new URL(url);
  const path = parsedUrl.pathname + parsedUrl.search;
  const isGet = method.toUpperCase() === "GET";
  const bodyStr = (!isGet && body) ? JSON.stringify(body) : "";

  const authHeaders = await signRequest(
    method,
    path,
    bodyStr,
    keys.privateKey,
    keys.publicKey
  );

  const headers: Record<string, string> = {
    ...authHeaders,
  };
  if (!isGet) {
    headers["Content-Type"] = "application/json";
  }

  const res = await fetch(url, {
    method,
    headers,
    body: isGet ? undefined : (bodyStr || undefined),
  });

  // Log response status for debugging
  if (!res.ok) {
    const errBody = await res.clone().text().catch(() => "");
    console.error(`[Auth] ${res.status} ${res.statusText} — ${method} ${path}`);
    console.error("[Auth] Response body:", errBody);
  }

  // If 401, the API wallet is not recognized — clear keys and notify
  if (res.status === 401) {
    console.error("[Auth] 401 — API wallet not recognized. Clearing session.");
    clearApiWalletKeys();
    window.dispatchEvent(new Event("strike:session-expired"));
  }

  return res;
}

// ── Hex utilities ──

function bytesToHex(bytes: Uint8Array): string {
  return Array.from(bytes)
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

function hexToBytes(hex: string): Uint8Array {
  const bytes = new Uint8Array(hex.length / 2);
  for (let i = 0; i < hex.length; i += 2) {
    bytes[i / 2] = parseInt(hex.substring(i, i + 2), 16);
  }
  return bytes;
}
