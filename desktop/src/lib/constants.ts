export const BRIDGE_URL = "http://127.0.0.1:9420";
export const BRIDGE_WS_URL = "ws://127.0.0.1:9420";

export const WS_CHANNELS = {
  MARKET: "market",
  TRADING: "trading",
  MICRO: "micro",
  RISK: "risk",
  SYSTEM: "system",
} as const;

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
