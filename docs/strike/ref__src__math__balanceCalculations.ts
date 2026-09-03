import type { Position } from "../types/position";
import type { Market } from "../types/market";
import type { MarkPriceUpdate } from "../types/ws";
import { getMaxLeverage } from "./marginTiers";

// ═══════════════════════════════════════════════════════════════════
// OPEN ORDER COSTS (matches Strike app UserStreamContext lines 1019-1100)
// ═══════════════════════════════════════════════════════════════════

/**
 * Calculate margin locked by open orders using the netting formula.
 *
 * Per symbol: aggregate bid/ask notional from open limit/market orders,
 * then net against existing position direction:
 *   - No position: max(bidNotional, askNotional)
 *   - LONG: buys add exposure, sells reduce (up to 2x position notional)
 *   - SHORT: sells add exposure, buys reduce (up to 2x position notional)
 *   - Cost = nettedNotional / leverage
 */
export function calculateOpenOrderCosts(
  orders: any[],
  positions: Position[],
  prices: Record<string, MarkPriceUpdate>,
  symbolSettings: Record<string, { leverage: number }> | null | undefined,
  markets: Record<string, Market>
): number {
  if (!orders.length) return 0;

  // Step 1: Build bid/ask notional aggregates per symbol
  const symbolAgg: Record<string, { bidNotional: number; askNotional: number }> = {};

  for (const order of orders) {
    // Only include Limit or Market orders that are Pending or Open
    const type = String(order.Type ?? order.type ?? "").toUpperCase();
    const status = String(order.Status ?? order.status ?? "").toUpperCase();
    if (
      (type !== "LIMIT" && type !== "MARKET") ||
      (status !== "PENDING" && status !== "OPEN" && status !== "NEW" && status !== "PARTIALLY_FILLED")
    ) continue;

    const sym = order.Symbol ?? order.symbol ?? "";
    const side = String(order.Side ?? order.side ?? "").toUpperCase();
    const remaining = Math.max(0, parseFloat(order.Size ?? order.size ?? "0") - parseFloat(order.Filled ?? order.filled ?? "0"));
    const price = parseFloat(order.Price ?? order.price ?? "0");
    if (remaining <= 0 || price <= 0) continue;

    const notional = remaining * price;
    if (!symbolAgg[sym]) symbolAgg[sym] = { bidNotional: 0, askNotional: 0 };

    if (side === "BUY") {
      symbolAgg[sym].bidNotional += notional;
    } else {
      symbolAgg[sym].askNotional += notional;
    }
  }

  // Step 2-4: Apply netting formula per symbol and sum
  let total = 0;
  for (const sym of Object.keys(symbolAgg)) {
    const { bidNotional, askNotional } = symbolAgg[sym];
    if (bidNotional === 0 && askNotional === 0) continue;

    const position = positions.find((p) => p.symbol === sym);
    const positionSize = position ? position.size : 0;
    const direction = position
      ? (position.positionSide === "LONG" ? 1 : position.positionSide === "SHORT" ? -1 : 0)
      : 0;

    const markPriceData = prices[sym];
    const mp = markPriceData ? parseFloat(markPriceData.p || markPriceData.i || "0") : 0;
    const posNotional = mp * Math.abs(positionSize);
    const notionalTwice = posNotional * 2;

    // Netting formula (matches Strike app exactly)
    let f: number;
    if (direction === 0) {
      f = Math.max(bidNotional, askNotional);
    } else if (direction > 0) {
      // LONG position
      f = askNotional <= notionalTwice
        ? bidNotional
        : Math.max(bidNotional, askNotional - notionalTwice);
    } else {
      // SHORT position
      f = bidNotional <= notionalTwice
        ? askNotional
        : Math.max(askNotional, bidNotional - notionalTwice);
    }

    const leverage =
      symbolSettings?.[sym]?.leverage ||
      markets[sym]?.default_leverage ||
      getMaxLeverage(markets[sym]) ||
      10;

    total += f / leverage;
  }

  return total;
}

// ═══════════════════════════════════════════════════════════════════
// BALANCE METRICS
// ═══════════════════════════════════════════════════════════════════

/**
 * Calculate real-time account balance metrics from positions and wallet balance.
 *
 * @example
 *   walletBalance = $10,500
 *   positions: 1 isolated (isoBalance=$1,075), 1 cross (IM=$1,000, uPnL=$200, MM=$45)
 *   openOrderCosts = $500, lockedRewards = $100
 *
 *   marginBalance = $10,500 + $200 = $10,700
 *   crossRequirement = max($1,000 - $200, $45) = $800
 *   availableBalance = max(0, $10,500 - $500 - $1,075 - $800) = $8,125
 *   withdrawable = max(0, ($10,500 - $500 - $1,075) - $800 - $100) = $8,025
 */
export function calculateBalances(
  walletBalance: number,
  positions: Position[],
  openOrderCosts: number = 0,
  lockedRewards: number = 0
) {
  let totalIsolatedMargin = 0;
  let crossInitialMargin = 0;
  let crossUPnL = 0;
  let crossMaintenanceMargin = 0;
  let totalUnrealizedPnL = 0;

  for (const p of positions) {
    totalUnrealizedPnL += p.uPnL ?? 0;
    if (p.marginMode === "isolated") {
      totalIsolatedMargin += p.isoBalance ?? 0;
    } else {
      crossInitialMargin += p.currentMargin ?? 0;
      crossUPnL += p.uPnL ?? 0;
      crossMaintenanceMargin += p.maintenanceMargin ?? 0;
    }
  }

  const marginBalance = walletBalance + totalUnrealizedPnL;

  const crossRequirement = Math.max(
    crossInitialMargin - crossUPnL,
    crossMaintenanceMargin
  );

  const availableBalance = Math.max(
    0,
    walletBalance - openOrderCosts - totalIsolatedMargin - crossRequirement
  );

  const baseBalance = walletBalance - openOrderCosts - totalIsolatedMargin;
  const withdrawableBalance = Math.max(
    0,
    baseBalance - crossRequirement - lockedRewards
  );

  const positionValue = positions.reduce(
    (sum, p) => sum + (p.currentMargin ?? 0) + (p.uPnL ?? 0),
    0
  );

  return {
    marginBalance,
    availableBalance,
    withdrawableBalance,
    positionValue,
    totalUnrealizedPnL,
  };
}
