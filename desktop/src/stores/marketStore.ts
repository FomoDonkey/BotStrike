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

export interface MarketInfo {
  funding_rate: number;
  volume_24h: number;
  open_interest: number;
  mark_price: number;
  index_price: number;
}

interface MarketState {
  prices: Record<string, number>;
  prevPrices: Record<string, number>;
  candles: Record<string, Candle[]>;
  orderbooks: Record<string, OrderBookData>;
  regime: Record<string, string>;
  marketInfo: Record<string, MarketInfo>;

  // Throttled tick buffer — NOT in state to avoid re-renders
  _tickBuffer: Record<string, Tick[]>;

  onTick: (tick: Tick) => void;
  onCandles: (symbol: string, candles: Candle[]) => void;
  onSnapshot: (data: any) => void;
}

// Throttle price updates to max 4/sec to prevent re-render storm
let _priceFlushTimer: ReturnType<typeof setInterval> | null = null;
const _pendingPrices: Record<string, { price: number; prev: number }> = {};

let _idleCount = 0;

function startPriceThrottle() {
  if (_priceFlushTimer) return;
  _priceFlushTimer = setInterval(() => {
    const keys = Object.keys(_pendingPrices);
    if (keys.length === 0) {
      _idleCount++;
      if (_idleCount > 40 && _priceFlushTimer) {
        clearInterval(_priceFlushTimer);
        _priceFlushTimer = null;
        _idleCount = 0;
      }
      return;
    }
    _idleCount = 0;

    // Only setState if any price actually changed — avoids re-render storm
    const state = useMarketStore.getState();
    let changed = false;
    for (const sym of keys) {
      if (state.prices[sym] !== _pendingPrices[sym].price) {
        changed = true;
        break;
      }
    }
    if (!changed) {
      // Prices identical — clear pending, skip setState
      for (const sym of keys) delete _pendingPrices[sym];
      return;
    }

    const prices = { ...state.prices };
    const prevPrices = { ...state.prevPrices };

    for (const sym of keys) {
      const p = _pendingPrices[sym];
      prevPrices[sym] = p.prev;
      prices[sym] = p.price;
      delete _pendingPrices[sym];
    }

    useMarketStore.setState({ prices, prevPrices });
  }, 250);
}

export const useMarketStore = create<MarketState>((set, get) => ({
  prices: {},
  prevPrices: {},
  candles: {},
  orderbooks: {},
  regime: {},
  marketInfo: {},
  _tickBuffer: {},

  onTick: (tick) => {
    // Buffer price — don't trigger React re-render on every tick
    const current = get().prices[tick.symbol] ?? tick.price;
    _pendingPrices[tick.symbol] = { price: tick.price, prev: current };
    startPriceThrottle();
  },

  onCandles: (symbol, candles) =>
    set((s) => ({
      candles: { ...s.candles, [symbol]: candles },
    })),

  onSnapshot: (data) => {
    const sym = data.symbol;
    if (!sym) return;
    const s = get();
    const updates: any = {};

    if (data.price) {
      _pendingPrices[sym] = { price: data.price, prev: s.prices[sym] ?? data.price };
      startPriceThrottle();
    }
    if (data.orderbook) {
      updates.orderbooks = { ...s.orderbooks, [sym]: data.orderbook };
    }
    if (data.regime) {
      updates.regime = { ...s.regime, [sym]: data.regime };
    }

    // Store market info fields from snapshot (funding, volume, OI, etc.)
    if (data.funding_rate !== undefined || data.volume_24h !== undefined || data.open_interest !== undefined) {
      const prev = s.marketInfo[sym] ?? { funding_rate: 0, volume_24h: 0, open_interest: 0, mark_price: 0, index_price: 0 };
      updates.marketInfo = {
        ...s.marketInfo,
        [sym]: {
          funding_rate: data.funding_rate ?? prev.funding_rate,
          volume_24h: data.volume_24h ?? prev.volume_24h,
          open_interest: data.open_interest ?? prev.open_interest,
          mark_price: data.mark_price ?? prev.mark_price,
          index_price: data.index_price ?? prev.index_price,
        },
      };
    }

    if (Object.keys(updates).length > 0) {
      set(updates);
    }
  },
}));
