import type { Market, MarginTier } from "../types/market";

const DEFAULT_MMR = 0.01;
const DEFAULT_MAX_LEVERAGE = 10;

/**
 * Find the applicable margin tier for a given notional value.
 * Tiers are sorted by max_notional ascending; returns first tier where max_notional >= notional.
 *
 * @example
 *   tiers = [{ max_notional: "50000", ... }, { max_notional: "250000", ... }]
 *   getMarginTier(tiers, 21750)  -> tier[0] (50000 >= 21750)
 *   getMarginTier(tiers, 80000)  -> tier[1] (250000 >= 80000)
 */
export function getMarginTier(
  tiers: MarginTier[],
  notional: number
): MarginTier | undefined {
  if (!tiers || tiers.length === 0) return undefined;
  for (const tier of tiers) {
    if (parseFloat(tier.max_notional) >= notional) return tier;
  }
  return tiers[tiers.length - 1]; // fallback to last tier
}

/**
 * Max leverage for a market (tier 1 = highest leverage).
 * @example getMaxLeverage(btcMarket) -> 50
 */
export function getMaxLeverage(market: Market | null | undefined): number {
  if (!market?.margin_tiers?.length) return DEFAULT_MAX_LEVERAGE;
  return market.margin_tiers[0].max_leverage;
}

/**
 * Maintenance Margin Rate for a given notional.
 * @example
 *   notional = $21,750 -> Tier 1 -> mmr = 0.004
 *   notional = $80,000 -> Tier 2 -> mmr = 0.005
 */
export function getMMR(
  market: Market | null | undefined,
  notional: number
): number {
  if (!market?.margin_tiers?.length) return DEFAULT_MMR;
  const tier = getMarginTier(market.margin_tiers, notional);
  return tier ? parseFloat(tier.maintenance_margin_rate) : DEFAULT_MMR;
}

/**
 * Maintenance Amount (constant subtracted in the MM formula).
 * @example
 *   notional = $21,750 -> Tier 1 -> ma = $0
 *   notional = $80,000 -> Tier 2 -> ma = $50
 */
export function getMaintenanceAmount(
  market: Market | null | undefined,
  notional: number
): number {
  if (!market?.margin_tiers?.length) return 0;
  const tier = getMarginTier(market.margin_tiers, notional);
  return tier ? parseFloat(tier.maintenance_amount) : 0;
}

/**
 * Max leverage allowed at a specific notional size.
 * @example
 *   notional = $80,000 -> Tier 2 (max_notional=$250k) -> max_leverage = 25
 */
export function getMaxLeverageForNotional(
  market: Market | null | undefined,
  notional: number
): number {
  if (!market?.margin_tiers?.length) return DEFAULT_MAX_LEVERAGE;
  const tier = getMarginTier(market.margin_tiers, notional);
  return tier ? tier.max_leverage : DEFAULT_MAX_LEVERAGE;
}

/**
 * Max position size (notional) for a given leverage.
 * Finds highest-notional tier whose max_leverage >= leverage.
 *
 * @example
 *   leverage = 30 -> Tier 1 (max_leverage=50 >= 30) -> max $50,000
 *   leverage = 20 -> Tier 3 (max_leverage=20 >= 20) -> max $1,000,000
 */
export function getMaxPositionSizeForLeverage(
  market: Market | null | undefined,
  leverage: number
): number | null {
  if (!market?.margin_tiers?.length) return null;
  let result: MarginTier | null = null;
  for (const tier of market.margin_tiers) {
    if (tier.max_leverage >= leverage) result = tier;
  }
  return result ? parseFloat(result.max_notional) : null;
}
