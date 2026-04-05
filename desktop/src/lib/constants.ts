export const BRIDGE_URL = "http://127.0.0.1:9420";
export const BRIDGE_WS_URL = "ws://127.0.0.1:9420";

export const WS_CHANNELS = {
  MARKET: "market",
  TRADING: "trading",
  MICRO: "micro",
  RISK: "risk",
  SYSTEM: "system",
} as const;

// All tradeable symbols — single source of truth for UI
export const SYMBOLS = ["BTC-USD", "ETH-USD", "SOL-USD", "ADA-USD"] as const;
export type SymbolId = (typeof SYMBOLS)[number];

export const SYMBOL_LABELS: Record<string, string> = {
  "BTC-USD": "BTC",
  "ETH-USD": "ETH",
  "SOL-USD": "SOL",
  "ADA-USD": "ADA",
};

export const SYMBOL_COLORS: Record<string, string> = {
  "BTC-USD": "#F7931A",
  "ETH-USD": "#627EEA",
  "SOL-USD": "#00FFA3",
  "ADA-USD": "#0033AD",
};

export const STRATEGY_COLORS: Record<string, string> = {
  MEAN_REVERSION: "#6C5CE7",
  ORDER_FLOW_MOMENTUM: "#00CEC9",
  TREND_FOLLOWING: "#00B894",
  MARKET_MAKING: "#FDCB6E",
};

export const STRATEGY_LABELS: Record<string, string> = {
  MEAN_REVERSION: "Mean Reversion",
  ORDER_FLOW_MOMENTUM: "Order Flow",
  TREND_FOLLOWING: "Trend Following",
  MARKET_MAKING: "Market Making",
};

export const REGIME_COLORS: Record<string, string> = {
  RANGING: "#74B9FF",
  TRENDING_UP: "#00D4AA",
  TRENDING_DOWN: "#FF4757",
  BREAKOUT: "#E84393",
  UNKNOWN: "#4A5568",
};
