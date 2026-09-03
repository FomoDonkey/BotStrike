/**
 * User WebSocket stream (authenticated via session.logon).
 *
 * Auth flow (matches Go mm-bot/websocket/userstream_client.go):
 * 1. Connect to plain WS URL (no query params)
 * 2. Send session.logon with Ed25519 signature
 *    - message format: "session.logon:{timestamp_ms}:{apiKey}"
 *    - apiKey = public key hex
 * 3. Wait for logon response { id: 1, status: 200 }
 * 4. Subscribe to "userstream" channel
 * 5. Route ACCOUNT_UPDATE and ORDER_TRADE_UPDATE events to listeners
 */

import { USER_WS_URL } from "../api/config";
import { loadApiWalletKeys } from "../api/auth";
import * as ed from "@noble/ed25519";

export type UserStreamCallback = (event: any) => void;

const MAX_RECONNECT_ATTEMPTS = 10;
const RECONNECT_DELAY = 5000;
const LOGON_REQUEST_ID = 1;
const SUBSCRIBE_REQUEST_ID = 2;

/** Convert hex string to Uint8Array */
function hexToBytes(hex: string): Uint8Array {
  const bytes = new Uint8Array(hex.length / 2);
  for (let i = 0; i < hex.length; i += 2) {
    bytes[i / 2] = parseInt(hex.substring(i, i + 2), 16);
  }
  return bytes;
}

/** Convert Uint8Array to hex string */
function bytesToHex(bytes: Uint8Array): string {
  return Array.from(bytes)
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

export class UserStream {
  private ws: WebSocket | null = null;
  private accountId: string | null = null;
  private listeners = new Map<string, Set<UserStreamCallback>>();
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private reconnectAttempts = 0;
  private isAuthenticated = false;
  private hasSubscribed = false;
  private keepAliveInterval: ReturnType<typeof setInterval> | null = null;

  async connect(accountId: string) {
    // Close existing connection
    if (this.ws) {
      this.ws.onclose = null;
      this.ws.onerror = null;
      this.ws.onmessage = null;
      this.ws.close();
      this.ws = null;
    }
    this.stopKeepAlive();

    this.accountId = accountId;
    this.isAuthenticated = false;
    this.hasSubscribed = false;

    const keys = loadApiWalletKeys();
    if (!keys) {
      console.error("[UserStream] No API wallet keys — did builder connect complete?");
      return;
    }

    // Connect to plain URL (no auth in query params)
    console.log("[UserStream] Connecting to:", USER_WS_URL);

    try {
      this.ws = new WebSocket(USER_WS_URL);
    } catch (e) {
      console.error("[UserStream] Failed to create WebSocket:", e);
      return;
    }

    this.ws.onopen = () => {
      console.log("[UserStream] Connected, sending session.logon...");
      this.reconnectAttempts = 0;
      this.sendLogon(keys.privateKey, keys.publicKey);
    };

    this.ws.onmessage = (event) => {
      let message: any;
      try {
        message = JSON.parse(event.data);
      } catch {
        return;
      }

      console.log("[UserStream] Message:", JSON.stringify(message).slice(0, 300));

      // Handle control responses (logon ack, subscribe ack) by matching id
      const msgId = message?.id;

      // Step 1: Logon response (id === LOGON_REQUEST_ID)
      if (!this.isAuthenticated && msgId === LOGON_REQUEST_ID) {
        if (message?.error || message?.status >= 400) {
          console.error("[UserStream] session.logon FAILED:", message);
          return;
        }
        if (message?.status === 200) {
          console.log("[UserStream] session.logon OK — authenticated");
          this.isAuthenticated = true;
          this.sendSubscribe(accountId);
          this.startKeepAlive();
          return;
        }
        console.warn("[UserStream] Unexpected logon response:", message);
        return;
      }

      // Step 2: Subscribe response (id === SUBSCRIBE_REQUEST_ID)
      if (msgId === SUBSCRIBE_REQUEST_ID) {
        if (message?.error || message?.status >= 400) {
          console.error("[UserStream] subscribe FAILED:", message);
          return;
        }
        console.log("[UserStream] Subscribed to userstream");
        this.hasSubscribed = true;
        return;
      }

      // Step 3: Skip other control messages (no event type)
      const eventType = (
        message?.e ||
        message?.data?.e ||
        ""
      ).toUpperCase();

      if (!eventType) {
        // Could be a pong or unknown control frame
        if (message?.id !== undefined) return; // control ack we don't care about
        console.log("[UserStream] Unknown message (no event type):", message);
        return;
      }

      // Step 4: Route data events to listeners
      // For ORDER_TRADE_UPDATE, order data is in message.data
      // For ACCOUNT_UPDATE, balance/position data is directly on message (B, P, r)
      const payload = message?.data ? { ...message, ...message.data } : message;

      console.log("[UserStream] Event:", eventType, "payload keys:", Object.keys(payload));

      const listeners = this.listeners.get(eventType);
      if (listeners) {
        for (const cb of listeners) {
          try {
            cb(payload);
          } catch (e) {
            console.error("[UserStream] Listener error:", e);
          }
        }
      }

      // Wildcard listeners
      const allListeners = this.listeners.get("*");
      if (allListeners) {
        for (const cb of allListeners) {
          try {
            cb(payload);
          } catch (e) {
            console.error("[UserStream] Listener error:", e);
          }
        }
      }
    };

    this.ws.onerror = (err) => {
      console.error("[UserStream] WebSocket error:", err);
    };

    this.ws.onclose = (event) => {
      console.log("[UserStream] Disconnected, code:", event.code, "reason:", event.reason);
      this.ws = null;
      this.isAuthenticated = false;
      this.hasSubscribed = false;
      this.stopKeepAlive();

      // Check if unauthorized — don't reconnect
      const reason = (event?.reason ?? "").toLowerCase();
      const isUnauthorized =
        event.code === 1008 ||
        event.code === 4001 ||
        event.code === 4003 ||
        event.code === 4401 ||
        reason.includes("unauth") ||
        reason.includes("token") ||
        reason.includes("forbidden");

      if (isUnauthorized) {
        console.error("[UserStream] Unauthorized — not reconnecting");
        return;
      }

      // Reconnect
      if (this.reconnectAttempts < MAX_RECONNECT_ATTEMPTS && this.accountId) {
        this.reconnectAttempts++;
        console.log(`[UserStream] Reconnecting in ${RECONNECT_DELAY}ms (attempt ${this.reconnectAttempts}/${MAX_RECONNECT_ATTEMPTS})`);
        this.reconnectTimer = setTimeout(() => {
          if (this.accountId) this.connect(this.accountId);
        }, RECONNECT_DELAY);
      }
    };
  }

  /**
   * Send session.logon — matches Go client exactly:
   *   message = "session.logon:{timestamp_ms}:{apiKey}"
   *   signature = ed25519.sign(privateKey, message)
   */
  private async sendLogon(privateKey: string, publicKey: string) {
    if (!this.ws) return;

    const timestamp = Date.now(); // milliseconds, matching Go client
    const message = `session.logon:${timestamp}:${publicKey}`;

    console.log("[UserStream] Logon message to sign:", message);

    const messageBytes = new TextEncoder().encode(message);
    const privateKeyBytes = hexToBytes(privateKey);
    const signature = ed.sign(messageBytes, privateKeyBytes);
    const sigHex = bytesToHex(signature);

    const logonMsg = {
      method: "session.logon",
      id: LOGON_REQUEST_ID,
      params: {
        apiKey: publicKey,
        signature: sigHex,
        timestamp,
      },
    };

    console.log("[UserStream] Sending session.logon:", JSON.stringify(logonMsg).slice(0, 200));
    this.ws.send(JSON.stringify(logonMsg));
  }

  /** Subscribe to userstream channel after successful logon. */
  private sendSubscribe(accountId: string) {
    if (!this.ws) return;

    const subMsg = {
      method: "subscribe",
      channel: "userstream",
      account_id: accountId,
      id: SUBSCRIBE_REQUEST_ID,
    };

    console.log("[UserStream] Subscribing:", JSON.stringify(subMsg));
    this.ws.send(JSON.stringify(subMsg));
  }

  /** Ping every 30s to keep connection alive (matches Go client). */
  private startKeepAlive() {
    this.stopKeepAlive();
    this.keepAliveInterval = setInterval(() => {
      if (this.ws?.readyState === WebSocket.OPEN) {
        // Browser WebSocket API doesn't expose ping frames directly,
        // but sending an empty object as a heartbeat works
        this.ws.send(JSON.stringify({ method: "ping" }));
      }
    }, 30_000);
  }

  private stopKeepAlive() {
    if (this.keepAliveInterval) {
      clearInterval(this.keepAliveInterval);
      this.keepAliveInterval = null;
    }
  }

  disconnect() {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.stopKeepAlive();
    if (this.ws) {
      this.ws.onclose = null;
      this.ws.close();
      this.ws = null;
    }
    this.accountId = null;
    this.isAuthenticated = false;
    this.hasSubscribed = false;
  }

  on(eventType: string, callback: UserStreamCallback): () => void {
    if (!this.listeners.has(eventType)) {
      this.listeners.set(eventType, new Set());
    }
    this.listeners.get(eventType)!.add(callback);
    return () => this.listeners.get(eventType)?.delete(callback);
  }
}

export const userStream = new UserStream();
