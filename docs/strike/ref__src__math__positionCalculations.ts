import type { Position, CrossLiquidationContext } from "../types/position";
import type { Market } from "../types/market";
import { getMMR, getMaintenanceAmount } from "./marginTiers";

// ═══════════════════════════════════════════════════════════════════
// LIQUIDATION PRICE
// ═══════════════════════════════════════════════════════════════════

/**
 * Liquidation price for an ISOLATED position.
 *
 * Formula:
 *   LP = (EP - (IsoBalance + MA) / Size) / (1 - Direction * MMR)
 *
 * @param entryPrice  - Entry price
 * @param size        - SIGNED size (+0.5 for Long, -0.5 for Short)
 * @param isoBalance  - Isolated margin balance
 * @param mmr         - Maintenance Margin Rate (e.g. 0.004)
 * @param ma          - Maintenance Amount from tier (e.g. 0 or 50)
 *
 * @example Long 0.5 BTC at $43,000, isoBalance=$1,075, mmr=0.004, ma=0:
 *   numerator = 43000 - (1075 + 0) / 0.5 = 43000 - 2150 = 40850
 *   denominator = 1 - 1 * 0.004 = 0.996
 *   LP = 40850 / 0.996 = $41,014.06
 *
 * @example Short 0.5 BTC at $43,000, isoBalance=$1,075, mmr=0.004, ma=0:
 *   size = -0.5, direction = -1
 *   numerator = 43000 - (1075 + 0) / (-0.5) = 43000 + 2150 = 45150
 *   denominator = 1 - (-1) * 0.004 = 1.004
 *   LP = 45150 / 1.004 = $44,960.16
 */
export function calculateIsolatedLiquidationPrice(
  entryPrice: number,
  size: number,
  isoBalance: number,
  mmr: number,
  ma: number = 0
): number {
  if (size === 0) return 0;

  const direction = size > 0 ? 1 : -1;
  const numerator = entryPrice - (isoBalance + ma) / size;
  const denominator = 1 - direction * mmr;

  if (denominator === 0) return 0;

  const lp = numerator / denominator;

  // Validation: Long LP must be < entry, Short LP must be > entry
  if (direction > 0 && lp >= entryPrice) return 0;
  if (direction < 0 && lp <= entryPrice) return 0;
  if (lp <= 0) return 0;

  return lp;
}

/**
 * Liquidation price for a CROSS position.
 *
 * Formula:
 *   LP = (EP - (W + TU - TM + MA) / Size) / (1 - Direction * MMR)
 *
 * Where:
 *   W  = WalletBalance - sum(all isolated IsoBalance)
 *   TU = sum(OTHER cross positions' uPnL)
 *   TM = sum(OTHER cross positions' maintenanceMargin)
 *
 * @example Cross Long 0.5 BTC at $43,000. Wallet=$10,500. Other cross ETH uPnL=+$100, mm=$30.
 *   One isolated position isoBalance=$500. mmr=0.004, ma=$0.
 *
 *   W = $10,500 - $500 = $10,000
 *   numerator = 43000 - (10000 + 100 - 30 + 0) / 0.5 = 43000 - 20140 = 22860
 *   denominator = 1 - 1 * 0.004 = 0.996
 *   LP = 22860 / 0.996 = $22,951.81
 */
export function calculateCrossLiquidationPrice(
  entryPrice: number,
  size: number,
  mmr: number,
  context: CrossLiquidationContext,
  ma: number = 0
): number {
  if (size === 0) return 0;

  const {
    walletBalance,
    totalIsolatedMargin,
    otherCrossUPnL,
    otherCrossMaintenanceMargin,
  } = context;

  const W = walletBalance - totalIsolatedMargin;
  const direction = size > 0 ? 1 : -1;
  const numerator =
    entryPrice - (W + otherCrossUPnL - otherCrossMaintenanceMargin + ma) / size;
  const denominator = 1 - direction * mmr;

  if (denominator === 0) return 0;

  const lp = numerator / denominator;
  if (lp <= 0) return 0;

  return lp;
}

// ═══════════════════════════════════════════════════════════════════
// POSITION DERIVED FIELDS
// ═══════════════════════════════════════════════════════════════════

/**
 * Calculate all derived fields for a position given the current mark price.
 * Call this on every markPriceUpdate for each position.
 *
 * @example Long 0.5 BTC, entry=$43,000, mark=$43,500, isolated, isoBalance=$1,075:
 *   uPnL = (43500 - 43000) * 0.5 = +$250.00
 *   notional = 43500 * 0.5 = $21,750
 *   currentMargin = $1,075 (isoBalance)
 *   maintenanceMargin = 21750 * 0.004 - 0 = $87.00
 *   leverage = 21750 / 1075 = 20.23x
 *   pnlPercentage = (250 / 1075) * 100 = +23.26%
 */
export function calculatePositionDerived(
  position: Position,
  markPrice: number,
  market?: Market
): Partial<Position> {
  if (!markPrice || markPrice <= 0 || position.size <= 0) {
    return {};
  }

  const { entryPrice, size, positionSide, marginMode, isoBalance } = position;

  // Unrealized PnL
  const priceDiff = markPrice - entryPrice;
  const uPnL = positionSide === "LONG" ? priceDiff * size : -priceDiff * size;

  // Notional
  const notional = markPrice * Math.abs(size);

  // Maintenance margin
  const mmr = getMMR(market, notional);
  const maintAmount = getMaintenanceAmount(market, notional);
  const maintenanceMargin =
    notional > 0 ? notional * mmr - maintAmount : 0;

  // Current margin
  let currentMargin: number;
  let requiredMargin: number | undefined;

  if (marginMode === "isolated") {
    currentMargin = isoBalance;
    const posLeverage = position.leverage ?? 1;
    requiredMargin = posLeverage > 0 ? notional / posLeverage : 0;
  } else {
    const lev = position.leverage || 1;
    currentMargin = lev > 0 ? notional / lev : 0;
  }

  // Leverage (actual)
  let leverage: number;
  if (marginMode === "isolated" && isoBalance > 0) {
    leverage = notional / isoBalance;
  } else {
    leverage = position.leverage ?? 0;
    if (leverage === 0 && currentMargin > 0) {
      leverage = notional / currentMargin;
    }
  }

  // PnL percentage (ROE)
  const pnlPercentage =
    currentMargin > 0 ? (uPnL / currentMargin) * 100 : 0;

  // Margin ratio (only when losing)
  const marginRatio =
    uPnL < 0 && currentMargin > 0
      ? (Math.abs(uPnL) / currentMargin) * 100
      : 0;

  // Liquidation price (isolated only here — cross needs full context)
  let liquidationPrice = 0;
  if (marginMode === "isolated") {
    const signedSize = positionSide === "LONG" ? size : -size;
    liquidationPrice = calculateIsolatedLiquidationPrice(
      entryPrice,
      signedSize,
      isoBalance,
      mmr,
      maintAmount
    );
  }

  return {
    uPnL,
    notional,
    currentMargin,
    requiredMargin,
    maintenanceMargin,
    leverage,
    pnlPercentage,
    marginRatio,
    liquidationPrice,
  };
}

/**
 * Enrich a position with all derived fields.
 * Returns a new position object with derived fields merged in.
 */
export function enrichPositionWithDerived(
  position: Position,
  markPrice: number,
  market?: Market
): Position {
  return {
    ...position,
    ...calculatePositionDerived(position, markPrice, market),
  };
}

// ═══════════════════════════════════════════════════════════════════
// PRE-TRADE LIQUIDATION ESTIMATION
// ═══════════════════════════════════════════════════════════════════

/**
 * Estimate liquidation price for an order BEFORE placing it.
 * Used for the immediate-liquidation safety check in the order form.
 *
 * For ISOLATED: builds context with just the order's own margin (notional/leverage).
 * For CROSS: aggregates all existing positions to build cross context.
 *
 * Returns null if inputs are insufficient, 0 if liq price is invalid.
 */
export function estimateOrderLiquidationPrice(params: {
  size: number;
  entryPrice: number;
  leverage: number;
  side: "Long" | "Short";
  markPrice: number;
  market: Market | null | undefined;
  marginMode: "Cross" | "Isolated";
  walletBalance: number;
  positions: Position[];
}): number | null {
  const {
    size, entryPrice, leverage, side, markPrice, market,
    marginMode, walletBalance, positions,
  } = params;

  if (!size || !entryPrice || !leverage || !market) return null;

  const signedSize = side === "Long" ? size : -size;
  const notional = size * entryPrice;
  const mmr = getMMR(market, notional);
  const ma = getMaintenanceAmount(market, notional);

  if (marginMode === "Isolated") {
    const isoBalance = notional / leverage;
    return calculateIsolatedLiquidationPrice(entryPrice, signedSize, isoBalance, mmr, ma);
  }

  // Cross: aggregate existing positions
  let totalIsolatedMargin = 0;
  let totalCrossUPnL = 0;
  let totalCrossMaintenanceMargin = 0;

  for (const p of positions) {
    if (p.marginMode === "isolated") {
      totalIsolatedMargin += p.isoBalance ?? 0;
    } else {
      totalCrossUPnL += p.uPnL ?? 0;
      totalCrossMaintenanceMargin += p.maintenanceMargin ?? 0;
    }
  }

  const context: CrossLiquidationContext = {
    walletBalance,
    totalIsolatedMargin,
    otherCrossUPnL: totalCrossUPnL,
    otherCrossMaintenanceMargin: totalCrossMaintenanceMargin,
  };

  return calculateCrossLiquidationPrice(entryPrice, signedSize, mmr, context, ma);
}

// ═══════════════════════════════════════════════════════════════════
// CROSS LIQUIDATION (MULTI-POSITION)
// ═══════════════════════════════════════════════════════════════════

/**
 * For an array of positions, calculate cross liquidation prices.
 * Must be called AFTER enrichPositionWithDerived for each position.
 *
 * @example
 *   const enriched = positions.map(p => enrichPositionWithDerived(p, markPrices[p.symbol], markets[p.symbol]));
 *   const final = applyCrossLiquidationPrices(enriched, walletBalance, markets);
 */
export function applyCrossLiquidationPrices(
  positions: Position[],
  walletBalance: number,
  markets: Record<string, Market>
): Position[] {
  // Aggregate totals
  let totalIsolatedMargin = 0;
  let totalCrossUPnL = 0;
  let totalCrossMaintenanceMargin = 0;

  for (const p of positions) {
    if (p.marginMode === "isolated") {
      totalIsolatedMargin += p.isoBalance ?? 0;
    } else {
      totalCrossUPnL += p.uPnL ?? 0;
      totalCrossMaintenanceMargin += p.maintenanceMargin ?? 0;
    }
  }

  return positions.map((p) => {
    if (p.marginMode !== "cross" || !p.notional) return p;

    const market = markets[p.symbol];
    const mmr = getMMR(market, p.notional);
    const ma = getMaintenanceAmount(market, p.notional);
    const signedSize = p.positionSide === "LONG" ? p.size : -p.size;

    const context: CrossLiquidationContext = {
      walletBalance,
      totalIsolatedMargin,
      otherCrossUPnL: totalCrossUPnL - (p.uPnL ?? 0),
      otherCrossMaintenanceMargin:
        totalCrossMaintenanceMargin - (p.maintenanceMargin ?? 0),
    };

    const liquidationPrice = calculateCrossLiquidationPrice(
      p.entryPrice,
      signedSize,
      mmr,
      context,
      ma
    );

    return { ...p, liquidationPrice };
  });
}
