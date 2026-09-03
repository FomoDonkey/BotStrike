/**
 * Order management endpoints.
 * All require API wallet authentication headers.
 */

import { API_BASE_URL } from "./config";
import { authenticatedFetch } from "./auth";
import type {
  CreateOrderRequest,
  CreateStrategyOrderRequest,
} from "../types/order";

/**
 * Place a regular order (market, limit, stop, take_profit, etc.).
 *
 * @example Market buy 0.5 BTC:
 *   await placeOrder({
 *     symbol: "BTC-USD",
 *     side: "buy",
 *     type: "market",
 *     size: "0.5",
 *   });
 *
 * @example Limit sell 1.0 ETH at $3,500:
 *   await placeOrder({
 *     symbol: "ETH-USD",
 *     side: "sell",
 *     type: "limit",
 *     size: "1.0",
 *     price: "3500.00",
 *     time_in_force: "GTC",
 *   });
 */
export async function placeOrder(params: CreateOrderRequest): Promise<any> {
  const res = await authenticatedFetch(
    `${API_BASE_URL}/v2/order`,
    "POST",
    params
  );

  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || `Place order failed: ${res.status}`);
  }

  return res.json();
}

/**
 * Place a strategy order (order + TP/SL).
 *
 * @example Market buy 0.5 BTC with TP at $45,000 and SL at $41,000:
 *   await placeStrategyOrder({
 *     strategy_id: crypto.randomUUID(),
 *     client_order_id: `strat-${strategyId}-primary`,
 *     symbol: "BTC-USD",
 *     side: "buy",
 *     type: "market",
 *     size: "0.5",
 *     take_profit_orders: [{
 *       type: "take_profit",
 *       size: "0.5",
 *       stop_price: "45000.00",
 *       client_order_id: `strat-${strategyId}-tp`,
 *       working_type: "mark_price",
 *     }],
 *     stop_loss_orders: [{
 *       type: "stop",
 *       size: "0.5",
 *       stop_price: "41000.00",
 *       client_order_id: `strat-${strategyId}-sl`,
 *       working_type: "mark_price",
 *     }],
 *   });
 */
export async function placeStrategyOrder(
  params: CreateStrategyOrderRequest
): Promise<any> {
  const res = await authenticatedFetch(
    `${API_BASE_URL}/v2/order/strategy`,
    "POST",
    params
  );

  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || `Strategy order failed: ${res.status}`);
  }

  return res.json();
}

/**
 * Cancel a single order.
 */
export async function cancelOrder(
  orderId: number,
  symbol: string
): Promise<any> {
  const res = await authenticatedFetch(
    `${API_BASE_URL}/v2/order/cancel`,
    "DELETE",
    { order_id: orderId, symbol }
  );

  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || `Cancel order failed: ${res.status}`);
  }

  return res.json();
}

/**
 * Cancel all orders (optionally filtered by symbol).
 */
export async function cancelAllOrders(symbol?: string): Promise<any> {
  const body: any = {};
  if (symbol) body.symbol = symbol;

  const res = await authenticatedFetch(
    `${API_BASE_URL}/v2/order/cancel-all`,
    "DELETE",
    body
  );

  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || `Cancel all failed: ${res.status}`);
  }

  return res.json();
}

/**
 * Replace an order atomically (cancel + create).
 */
export async function replaceOrder(
  cancelOrderId: number,
  cancelSymbol: string,
  newOrder: CreateOrderRequest
): Promise<any> {
  const res = await authenticatedFetch(
    `${API_BASE_URL}/v2/order/replace`,
    "POST",
    {
      cancel: { order_id: cancelOrderId, symbol: cancelSymbol },
      new_order: newOrder,
    }
  );

  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || `Replace order failed: ${res.status}`);
  }

  return res.json();
}

/**
 * Update leverage for a symbol.
 * API wallet auth identifies the account — no account_id needed.
 */
export async function updateLeverage(
  symbol: string,
  leverage: number
): Promise<any> {
  const res = await authenticatedFetch(
    `${API_BASE_URL}/v2/leverage`,
    "POST",
    { symbol, leverage }
  );

  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || `Update leverage failed: ${res.status}`);
  }

  return res.json();
}

/**
 * Update margin mode for a symbol.
 * API wallet auth identifies the account — no account_id needed.
 */
export async function updateMarginMode(
  symbol: string,
  marginMode: "cross" | "isolated"
): Promise<any> {
  const res = await authenticatedFetch(
    `${API_BASE_URL}/v2/marginMode`,
    "POST",
    { symbol, marginMode }
  );

  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || `Update margin mode failed: ${res.status}`);
  }

  return res.json();
}
