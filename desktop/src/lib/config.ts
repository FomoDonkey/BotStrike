// Single source of truth for the bridge endpoint (REST + WS) and the auth token.
// Persisted in localStorage; resolved lazily by api.ts / ws.ts on every call so that
// changing the URL in Settings → Connection only requires a reconnect.
import { useSyncExternalStore } from "react";

export const DEFAULT_BRIDGE_PORT = 9420;
export const DEFAULT_BRIDGE_URL = `http://127.0.0.1:${DEFAULT_BRIDGE_PORT}`;

/**
 * True when this build is being served BY the bridge itself (server/webui mounted by
 * bridge.py on the CT). In that case the page origin IS the bridge → zero-config connect.
 * Tauri (tauri://localhost / http://tauri.localhost) and `vite dev` keep the loopback default.
 */
export const SERVED_FROM_BRIDGE: boolean =
  import.meta.env.PROD &&
  typeof window !== "undefined" &&
  (window.location.protocol === "http:" || window.location.protocol === "https:") &&
  window.location.hostname !== "tauri.localhost" &&
  window.location.port !== "1420"; // vite preview safety

const INITIAL_BRIDGE_URL = SERVED_FROM_BRIDGE ? window.location.origin : DEFAULT_BRIDGE_URL;

const URL_KEY = "botstrike.bridgeUrl";
const TOKEN_KEY = "botstrike.authToken";
const LOCAL_HOSTS = new Set(["127.0.0.1", "localhost", "[::1]", "::1", "0.0.0.0"]);

export type BridgeMode = "local" | "remote";

export interface BridgeConfig {
  url: string;
  wsUrl: string;
  token: string;
  mode: BridgeMode;
  isLocal: boolean;
  port: number;
}

/**
 * Tolerant URL normalisation:
 *   "192.168.1.204" | "host:9420" | "http://host:9420/x" | "https://bridge.tailnet.ts.net"
 * → "http://host:9420" (origin). Port defaults to 9420 when absent. Returns null if invalid.
 */
export function normalizeBridgeUrl(raw: string): string | null {
  let s = raw.trim();
  if (!s) return null;
  if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(s)) s = `http://${s}`;
  let u: URL;
  try {
    u = new URL(s);
  } catch {
    return null;
  }
  if (u.protocol !== "http:" && u.protocol !== "https:") return null;
  if (!u.hostname) return null;
  if (u.username || u.password) return null;
  if (!u.port) u.port = String(DEFAULT_BRIDGE_PORT);
  return u.origin;
}

/** Human-readable reason when `normalizeBridgeUrl` rejects a value (for the Settings form). */
export function validateBridgeUrl(raw: string): string | null {
  const s = raw.trim();
  if (!s) return "URL is empty";
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(s) && !/^https?:\/\//i.test(s)) return "Only http:// or https:// are supported";
  if (!normalizeBridgeUrl(s)) return "Invalid URL (expected host[:port] or http://host:port)";
  return null;
}

function safeGet(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeSet(key: string, v: string) {
  try {
    if (v) localStorage.setItem(key, v);
    else localStorage.removeItem(key);
  } catch {
    /* storage unavailable (private mode, WebView quirks) — keep in-memory value */
  }
}

let currentUrl: string = normalizeBridgeUrl(safeGet(URL_KEY) ?? "") ?? INITIAL_BRIDGE_URL;
let currentToken: string = (safeGet(TOKEN_KEY) ?? "").trim();
let snapshot: BridgeConfig = buildSnapshot();

const listeners = new Set<() => void>();

function buildSnapshot(): BridgeConfig {
  const mode = getBridgeMode(currentUrl);
  return {
    url: currentUrl,
    wsUrl: getBridgeWsUrl(currentUrl),
    token: currentToken,
    mode,
    isLocal: mode === "local",
    port: getBridgePort(currentUrl),
  };
}

function notify() {
  snapshot = buildSnapshot();
  listeners.forEach((l) => l());
}

export function getBridgeUrl(): string {
  return currentUrl;
}

/** http → ws, https → wss. */
export function getBridgeWsUrl(url: string = currentUrl): string {
  return url.replace(/^https:/i, "wss:").replace(/^http:/i, "ws:");
}

export function getBridgeMode(url: string = currentUrl): BridgeMode {
  try {
    return LOCAL_HOSTS.has(new URL(url).hostname) ? "local" : "remote";
  } catch {
    return "remote";
  }
}

export function isLocalBridge(url: string = currentUrl): boolean {
  return getBridgeMode(url) === "local";
}

export function getBridgePort(url: string = currentUrl): number {
  try {
    const u = new URL(url);
    if (u.port) return Number(u.port);
    return u.protocol === "https:" ? 443 : 80;
  } catch {
    return DEFAULT_BRIDGE_PORT;
  }
}

export function getBridgeToken(): string {
  return currentToken;
}

/** Persist + notify. Returns the normalised URL, or null (state unchanged) if invalid. */
export function setBridgeUrl(raw: string): string | null {
  const n = normalizeBridgeUrl(raw);
  if (!n) return null;
  if (n !== currentUrl) {
    currentUrl = n;
    safeSet(URL_KEY, n);
    notify();
  }
  return n;
}

export function setBridgeToken(token: string) {
  const t = token.trim();
  if (t !== currentToken) {
    currentToken = t;
    safeSet(TOKEN_KEY, t);
    notify();
  }
}

export function subscribeBridgeConfig(l: () => void): () => void {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
}

function getSnapshot(): BridgeConfig {
  return snapshot;
}

/** React hook: re-renders when the URL or token change (Settings → Save). */
export function useBridgeConfig(): BridgeConfig {
  return useSyncExternalStore(subscribeBridgeConfig, getSnapshot, getSnapshot);
}
