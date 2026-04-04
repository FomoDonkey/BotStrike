import { create } from "zustand";

export interface PositionData {
  symbol: string;
  side: string;
  size: number;
  entry_price: number;
  mark_price: number;
  unrealized_pnl: number;
  realized_pnl: number;
  leverage: number;
  liquidation_price: number;
  strategy: string | null;
  notional: number;
  pnl_pct: number;
}

export interface TradeData {
  symbol: string;
  side: string;
  trade_type: "ENTRY" | "EXIT";
  price: number;
  quantity: number;
  fee: number;
  strategy: string | null;
  timestamp: number;
  pnl: number;
  actual_slippage_bps?: number;
  signal_features?: {
    mae_bps?: number;
    mfe_bps?: number;
    pnl_bps?: number;
    hold_time_sec?: number;
    order_type?: string;
    expected_cost_bps?: number;
    fill_probability?: number;
    routing_reason?: string;
    regime_at_entry?: string;
    spread_at_entry_bps?: number;
    exit_reason?: string;
    [key: string]: unknown;
  };
}

export interface SignalData {
  strategy: string;
  symbol: string;
  side: string;
  strength: number;
  entry_price: number;
  stop_loss: number;
  take_profit: number;
  size_usd: number;
  timestamp: number;
}

export interface MetricsData {
  equity: number;
  pnl: number;
  total_trades: number;
  win_rate: number;
  sharpe_ratio: number;
  max_drawdown: number;
  total_fees: number;
}

interface TradingState {
  positions: Record<string, PositionData[]>;
  recentTrades: TradeData[];
  recentSignals: SignalData[];
  metrics: MetricsData;

  onPositions: (symbol: string, positions: PositionData[]) => void;
  onTrade: (trade: TradeData) => void;
  onSignal: (signal: SignalData) => void;
  onMetrics: (metrics: MetricsData) => void;
}

// Restore last known metrics from localStorage to avoid showing stale $300 on reconnect
function loadCachedMetrics(): MetricsData {
  const fallback: MetricsData = {
    equity: 300, pnl: 0, total_trades: 0, win_rate: 0,
    sharpe_ratio: 0, max_drawdown: 0, total_fees: 0,
  };
  try {
    const raw = localStorage.getItem("bs_last_metrics");
    if (raw) {
      const parsed = JSON.parse(raw);
      return { ...fallback, ...parsed };
    }
  } catch {}
  return fallback;
}

export const useTradingStore = create<TradingState>((set) => ({
  positions: {},
  recentTrades: [],
  recentSignals: [],
  metrics: loadCachedMetrics(),

  onPositions: (symbol, positions) =>
    set((s) => ({ positions: { ...s.positions, [symbol]: positions } })),

  onTrade: (trade) =>
    set((s) => ({ recentTrades: [...s.recentTrades.slice(-99), trade] })),

  onSignal: (signal) =>
    set((s) => ({ recentSignals: [...s.recentSignals.slice(-49), signal] })),

  onMetrics: (metrics) => {
    try { localStorage.setItem("bs_last_metrics", JSON.stringify(metrics)); } catch {}
    set({ metrics });
  },
}));
