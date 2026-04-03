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

export const useTradingStore = create<TradingState>((set) => ({
  positions: {},
  recentTrades: [],
  recentSignals: [],
  metrics: {
    equity: 300,
    pnl: 0,
    total_trades: 0,
    win_rate: 0,
    sharpe_ratio: 0,
    max_drawdown: 0,
    total_fees: 0,
  },

  onPositions: (symbol, positions) =>
    set((s) => ({ positions: { ...s.positions, [symbol]: positions } })),

  onTrade: (trade) =>
    set((s) => ({ recentTrades: [...s.recentTrades.slice(-99), trade] })),

  onSignal: (signal) =>
    set((s) => ({ recentSignals: [...s.recentSignals.slice(-49), signal] })),

  onMetrics: (metrics) => set({ metrics }),
}));
