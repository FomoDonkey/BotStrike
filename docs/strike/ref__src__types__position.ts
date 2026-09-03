// ── Position data (base from API/WS + derived from mark price) ──

export interface Position {
  // Base fields from API / WebSocket ACCOUNT_UPDATE
  symbol: string; // e.g. "BTC-USD"
  size: number; // absolute position size (always positive)
  entryPrice: number;
  positionSide: "LONG" | "SHORT";
  marginMode: "cross" | "isolated";
  isoBalance: number; // isolated margin balance (only for isolated)
  leverage: number;
  accumulatedFundingFees: number; // cumulative funding paid/received
  id?: string;

  // Derived fields (recalculated on every mark price update)
  uPnL?: number; // unrealized PnL
  notional?: number; // markPrice * abs(size)
  currentMargin?: number; // isolated: isoBalance, cross: notional/leverage
  requiredMargin?: number; // notional / setLeverage (isolated only)
  maintenanceMargin?: number; // notional * mmr - maintAmount
  pnlPercentage?: number; // (uPnL / currentMargin) * 100
  marginRatio?: number; // (abs(uPnL) / currentMargin) * 100 when negative
  liquidationPrice?: number;
}

/**
 * Context needed to calculate cross-margin liquidation price.
 * For each cross position, you need data about ALL OTHER cross positions.
 */
export interface CrossLiquidationContext {
  walletBalance: number;
  totalIsolatedMargin: number; // sum of isoBalance for all isolated positions
  otherCrossUPnL: number; // sum of uPnL for OTHER cross positions
  otherCrossMaintenanceMargin: number; // sum of maintenanceMargin for OTHER cross positions
}
