import { create } from "zustand";

export interface Candle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface Tick {
  symbol: string;
  price: number;
  quantity: number;
  side: "BUY" | "SELL";
  notional: number;
  timestamp: number;
}

export interface OrderBookLevel {
  price: number;
  quantity: number;
}

export interface OrderBookData {
  symbol: string;
  bids: OrderBookLevel[];
  asks: OrderBookLevel[];
  best_bid: number | null;
  best_ask: number | null;
  mid_price: number | null;
  spread_bps: number;
  microprice: number | null;
}

interface MarketState {
  // Price data per symbol
  prices: Record<string, number>;
  prevPrices: Record<string, number>;
  candles: Record<string, Candle[]>;
  orderbooks: Record<string, OrderBookData>;
  recentTicks: Record<string, Tick[]>;
  regime: Record<string, string>;
  tps: Record<string, number>;

  // Actions
  onTick: (tick: Tick) => void;
  onCandles: (symbol: string, candles: Candle[]) => void;
  onSnapshot: (data: any) => void;
}

export const useMarketStore = create<MarketState>((set, get) => ({
  prices: {},
  prevPrices: {},
  candles: {},
  orderbooks: {},
  recentTicks: {},
  regime: {},
  tps: {},

  onTick: (tick) =>
    set((s) => {
      const currentPrice = s.prices[tick.symbol];
      return {
        prevPrices: { ...s.prevPrices, [tick.symbol]: currentPrice ?? tick.price },
        prices: { ...s.prices, [tick.symbol]: tick.price },
        recentTicks: {
          ...s.recentTicks,
          [tick.symbol]: [...(s.recentTicks[tick.symbol] || []).slice(-99), tick],
        },
      };
    }),

  onCandles: (symbol, candles) =>
    set((s) => ({
      candles: { ...s.candles, [symbol]: candles },
    })),

  onSnapshot: (data) =>
    set((s) => {
      const updates: Partial<MarketState> = {};
      const sym = data.symbol;
      if (data.price) {
        updates.prevPrices = { ...s.prevPrices, [sym]: s.prices[sym] || data.price };
        updates.prices = { ...s.prices, [sym]: data.price };
      }
      if (data.orderbook) {
        updates.orderbooks = { ...s.orderbooks, [sym]: data.orderbook };
      }
      if (data.regime) {
        updates.regime = { ...s.regime, [sym]: data.regime };
      }
      return updates;
    }),
}));
