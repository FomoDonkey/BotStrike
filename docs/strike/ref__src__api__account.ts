/**
 * Account and data fetching endpoints.
 *
 * With API wallet auth, the server identifies the account from the
 * X-API-Wallet-Public-Key header — no account_id param needed.
 */

import { API_BASE_URL, PRICE_API_URL } from "./config";
import { authenticatedFetch } from "./auth";
import type { Account } from "../types/account";
import type { Market, MarketsResponse } from "../types/market";

/**
 * Fetch account data. Server identifies account from API wallet auth headers.
 */
export async function getAccount(): Promise<Account> {
  const res = await authenticatedFetch(`${API_BASE_URL}/v2/account`, "GET");

  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || `Get account failed: ${res.status}`);
  }

  const data = await res.json();
  return data.data || data;
}

/**
 * Fetch all markets (public, no auth needed).
 */
export async function getMarkets(): Promise<Record<string, Market>> {
  const res = await fetch(`${API_BASE_URL}/v2/markets`);
  if (!res.ok) throw new Error(`Get markets failed: ${res.status}`);
  const data: MarketsResponse = await res.json();
  return data.markets;
}

/**
 * Fetch positions. Server identifies account from API wallet auth headers.
 */
export async function getPositions(): Promise<any[]> {
  const res = await authenticatedFetch(`${API_BASE_URL}/v2/positions`, "GET");

  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || `Get positions failed: ${res.status}`);
  }

  const data = await res.json();
  return data.positions ?? data.data?.positions ?? [];
}

/**
 * Fetch open orders. Server identifies account from API wallet auth headers.
 */
export async function getOpenOrders(): Promise<any[]> {
  const res = await authenticatedFetch(
    `${API_BASE_URL}/v2/openOrders`,
    "GET"
  );

  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || `Get open orders failed: ${res.status}`);
  }

  const data = await res.json();
  return data.orders ?? [];
}

/**
 * Fetch order book depth (public, no auth needed). Uses PRICE API.
 */
export async function getDepth(
  symbol: string,
  limit: number = 100
): Promise<any> {
  const res = await fetch(
    `${PRICE_API_URL}/v2/depth?symbol=${symbol}&limit=${limit}`
  );
  if (!res.ok) throw new Error(`Get depth failed: ${res.status}`);
  return res.json();
}

/**
 * Fetch mark price snapshot (public). Uses PRICE API.
 */
export async function getMarkPrice(symbol?: string): Promise<any> {
  const url = symbol
    ? `${PRICE_API_URL}/v2/markPrice?symbol=${symbol}`
    : `${PRICE_API_URL}/v2/markPrice`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Get mark price failed: ${res.status}`);
  return res.json();
}

/**
 * Fetch kline/candlestick data (public). Uses PRICE API.
 * Matches Strike app PriceContext getHistoricalBars endpoint.
 *
 * @param symbol    - e.g. "BTC-USD"
 * @param interval  - e.g. "1m", "5m", "15m", "1h", "4h", "1d"
 * @param startTime - Unix ms
 * @param endTime   - Unix ms
 * @param limit     - Max bars to return (default 500, max 5000)
 */
export async function getKlines(
  symbol: string,
  interval: string,
  startTime: number,
  endTime: number,
  limit: number = 500,
  priceType: string = "mark"
): Promise<any[]> {
  const params = new URLSearchParams({
    symbol: symbol.toUpperCase(),
    interval,
    priceType,
    startTime: startTime.toString(),
    endTime: endTime.toString(),
    limit: limit.toString(),
  });
  const res = await fetch(`${PRICE_API_URL}/v2/klines?${params}`);
  if (!res.ok) throw new Error(`Get klines failed: ${res.status}`);
  const data = await res.json();
  return Array.isArray(data) ? data : data.data ?? data.klines ?? [];
}

/**
 * Fetch fee tiers (public).
 */
export async function getFeeTiers(): Promise<any[]> {
  const res = await fetch(`${API_BASE_URL}/v2/fee-tiers`);
  if (!res.ok) throw new Error(`Get fee tiers failed: ${res.status}`);
  return res.json();
}

/**
 * Fetch portfolio summary. Server identifies account from API wallet auth headers.
 */
export async function getPortfolio(): Promise<any> {
  const res = await authenticatedFetch(`${API_BASE_URL}/v2/portfolio`, "GET");

  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || `Get portfolio failed: ${res.status}`);
  }

  return res.json();
}
