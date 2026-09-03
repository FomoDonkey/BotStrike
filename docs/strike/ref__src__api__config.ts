// ── API Configuration ──
// Defaults to testnet. Set env vars to switch to mainnet.

// Main API (trading, account, orders)
export const API_BASE_URL =
  import.meta.env.VITE_API_URL ?? "https://api-v2-testnet.strikefinance.org";

// Price API (depth, markPrice, klines, trades — REST)
export const PRICE_API_URL =
  import.meta.env.VITE_PRICE_URL ?? "https://api-v2-testnet.strikefinance.org/price";

// Price WebSocket (markPrice, depth, trade, kline, miniTicker)
export const PRICE_WS_URL =
  import.meta.env.VITE_PRICE_WS_URL ?? "wss://api-v2-testnet.strikefinance.org/ws/price";

// User WebSocket (authenticated — positions, orders, balance)
// /ws-api supports API wallet session.logon auth (builder codes)
// /ws/user is for JWT auth (Strike app) — do NOT use for builders
export const USER_WS_URL =
  import.meta.env.VITE_USER_WS_URL ?? "wss://api-v2-testnet.strikefinance.org/ws/user-api";

// Stats API (leaderboard, analytics)
export const STATS_API_URL =
  import.meta.env.VITE_STATS_URL ?? "https://api-v2-testnet.strikefinance.org/stat";

// Builder code for your integration
export const BUILDER_CODE =
  import.meta.env.VITE_BUILDER_CODE ?? "your-builder-code";
