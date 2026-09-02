// Bridge endpoint lives in ./config (configurable via Settings → Connection).
// Network/timing constants — keep ping < stale < watchdog consistent.
export const API_TIMEOUT_MS = 30_000;             // generic REST call
export const HEALTH_TIMEOUT_MS = 4_000;           // /api/health probe (Settings → Test)
export const BACKTEST_TIMEOUT_MS = 10 * 60_000;   // /api/backtest/run
export const WS_PING_MS = 10_000;                 // client → bridge {"type":"ping"}
export const WS_STALE_MS = 25_000;                // no message/pong for this long → half-open socket → reconnect
export const WS_RECONNECT_BASE_MS = 3_000;
export const WS_RECONNECT_MAX_MS = 30_000;
export const WS_STAGGER_MS = 500;                 // delay between the 5 channel connects
export const HEALTH_WATCHDOG_TICK_MS = 5_000;
export const HEALTH_STALE_MS = 10_000;            // bridge health arrives every 3 s; >10 s → not connected
export const OVERLAY_CONNECTED_MS = 1_000;        // "Connected" splash before auto-dismiss
export const OVERLAY_CONNECT_TIMEOUT_MS = 15_000; // "connecting" → "unreachable"

export const WS_CHANNELS = {
  MARKET: "market",
  TRADING: "trading",
  MICRO: "micro",
  RISK: "risk",
  SYSTEM: "system",
} as const;
export const WS_CHANNEL_LIST: readonly string[] = Object.values(WS_CHANNELS);

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
  FIBONACCI_RETRACEMENT: "#F39C12",
  ORDER_FLOW_MOMENTUM: "#00CEC9",
  TREND_FOLLOWING: "#00B894",
  MARKET_MAKING: "#FDCB6E",
  TREND_DAILY: "#38BDF8",
  DIVERGENCE: "#F472B6",
};

export const STRATEGY_LABELS: Record<string, string> = {
  MEAN_REVERSION: "Mean Reversion",
  FIBONACCI_RETRACEMENT: "Fibonacci",
  ORDER_FLOW_MOMENTUM: "Order Flow",
  TREND_FOLLOWING: "Trend Following",
  MARKET_MAKING: "Market Making",
  TREND_DAILY: "Trend daily",
  DIVERGENCE: "Divergence",
};

// Store write batching — high-frequency WS channels (trades/signals/positions/logs) are
// queued and flushed at most this often so a replay burst on connect is one render, not 100.
export const STORE_FLUSH_MS = 100;
// A trade older than this on arrival is replayed history (bridge re-sends recent fills on
// connect) → no toast/sound for it.
export const TRADE_ALERT_MAX_AGE_MS = 60_000;

export const EXCHANGE_LABELS: Record<string, string> = {
  binance: "Binance",
  hyperliquid: "Hyperliquid",
};

export const REGIME_COLORS: Record<string, string> = {
  RANGING: "#74B9FF",
  TRENDING_UP: "#00D4AA",
  TRENDING_DOWN: "#F43F5E",
  BREAKOUT: "#E84393",
  UNKNOWN: "#6B7280",
};

// Direction colours — the ONLY place green/rose is defined for charts and chips.
export const COLOR_UP = "#00D4AA";
export const COLOR_DOWN = "#F43F5E";

/** Exit reasons reported on closed trades (contract §2) → chip label + tone. */
export const EXIT_REASON_LABELS: Record<string, { label: string; tone: "profit" | "loss" | "neutral" | "warning" }> = {
  SL: { label: "SL", tone: "loss" },
  stop_loss: { label: "SL", tone: "loss" },
  TP: { label: "TP", tone: "profit" },
  take_profit: { label: "TP", tone: "profit" },
  signal: { label: "Signal", tone: "neutral" },
  time: { label: "Time", tone: "warning" },
  max_hold: { label: "Time", tone: "warning" },
  rebalance: { label: "Rebal", tone: "neutral" },
  trend_exit: { label: "Trend exit", tone: "neutral" },
  close: { label: "Close", tone: "neutral" },
  circuit_breaker: { label: "Breaker", tone: "loss" },
  kill: { label: "Killed", tone: "loss" },
};

/** Market tape depth kept per symbol (marketStore) */
export const TAPE_SIZE = 60;
/** Bridge candle history is 16 h of 1m bars — 24h stats computed client-side are labelled by span. */
export const FUNDING_INTERVAL_SEC = 8 * 3600;
