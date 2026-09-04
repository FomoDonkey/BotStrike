import { create } from "zustand";
import { TAPE_SIZE } from "@/lib/constants";

export interface Candle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

/** A window of bars fetched over REST for one (symbol, timeframe) — the chart's history. The
 *  socket only carries the last 500 one-minute bars; see lib/chartData.ts for how the two meet. */
export interface KlineWindow {
  /** the interval the bars are actually at — coarser than asked on a thin venue market */
  interval: string;
  /** "engine" (its own 90-day frame) or "venue" */
  source: string;
  candles: Candle[];
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
  spread: number;
  spread_bps: number;
  microprice: number | null;
}

/**
 * Per-symbol market stats from the WS `snapshot` (bridge 2.14 sends mark/index/funding/vol/OI;
 * ≥ 2.15 adds the 24h block, funding countdown and regime_since — contract §4). Missing fields
 * stay 0 / undefined and the UI derives them client-side from the 1m candles when it can.
 */
export interface MarketInfo {
  funding_rate: number;
  volume_24h: number;
  open_interest: number;
  mark_price: number;
  index_price: number;
  funding_countdown_sec?: number;
  change_24h_pct?: number;
  high_24h?: number;
  low_24h?: number;
  spread_bps?: number;
  regime_since?: number;
  regime_timeframe_min?: number;
  /** epoch seconds of the snapshot */
  updated?: number;
}

export interface SnapshotData {
  symbol?: string;
  timestamp?: number;
  price?: number;
  orderbook?: OrderBookData | null;
  regime?: string;
  funding_rate?: number;
  volume_24h?: number;
  volume_24h_usd?: number;
  open_interest?: number;
  mark_price?: number;
  index_price?: number;
  funding_countdown_sec?: number;
  change_24h_pct?: number;
  high_24h?: number;
  low_24h?: number;
  spread_bps?: number;
  regime_since?: number;
  regime_timeframe_min?: number;
}

interface MarketState {
  prices: Record<string, number>;
  prevPrices: Record<string, number>;
  candles: Record<string, Candle[]>;
  /** REST history keyed `${symbol}:${timeframe}` (lib/chartData.ts `klineKey`) */
  klines: Record<string, KlineWindow>;
  orderbooks: Record<string, OrderBookData>;
  regime: Record<string, string>;
  marketInfo: Record<string, MarketInfo>;
  /** Rolling tape of the last TAPE_SIZE ticks per symbol, newest first */
  tape: Record<string, Tick[]>;
  /** Date.now() of the last price flush (footer "feed age") */
  lastTickAt: number;

  onTick: (tick: Tick) => void;
  onCandles: (symbol: string, candles: Candle[]) => void;
  onKlines: (key: string, window: KlineWindow) => void;
  onSnapshot: (data: SnapshotData) => void;
}

// Throttle price/tape updates to max 4/sec to prevent re-render storm
let _priceFlushTimer: ReturnType<typeof setInterval> | null = null;
const _pendingPrices: Record<string, { price: number; prev: number }> = {};
const _pendingTape: Record<string, Tick[]> = {};

let _idleCount = 0;

function startPriceThrottle() {
  if (_priceFlushTimer) return;
  _priceFlushTimer = setInterval(() => {
    const keys = Object.keys(_pendingPrices);
    const tapeKeys = Object.keys(_pendingTape);
    if (keys.length === 0 && tapeKeys.length === 0) {
      _idleCount++;
      if (_idleCount > 40 && _priceFlushTimer) {
        clearInterval(_priceFlushTimer);
        _priceFlushTimer = null;
        _idleCount = 0;
      }
      return;
    }
    _idleCount = 0;

    const state = useMarketStore.getState();
    const updates: Partial<MarketState> = {};

    // Only touch prices if any price actually changed — avoids re-render storm
    let changed = false;
    for (const sym of keys) {
      if (state.prices[sym] !== _pendingPrices[sym].price) {
        changed = true;
        break;
      }
    }
    if (changed) {
      const prices = { ...state.prices };
      const prevPrices = { ...state.prevPrices };
      for (const sym of keys) {
        const p = _pendingPrices[sym];
        prevPrices[sym] = p.prev;
        prices[sym] = p.price;
      }
      updates.prices = prices;
      updates.prevPrices = prevPrices;
    }
    for (const sym of keys) delete _pendingPrices[sym];

    if (tapeKeys.length) {
      const tape = { ...state.tape };
      for (const sym of tapeKeys) {
        const fresh = _pendingTape[sym];
        // pending ticks are in arrival order → newest first in the tape
        tape[sym] = [...fresh.reverse(), ...(state.tape[sym] ?? [])].slice(0, TAPE_SIZE);
        delete _pendingTape[sym];
      }
      updates.tape = tape;
    }

    // ticks arrived (even at an unchanged price) → refresh the feed-age stamp at most once a second
    const nowMs = Date.now();
    if (keys.length && nowMs - state.lastTickAt >= 1000) updates.lastTickAt = nowMs;
    if (Object.keys(updates).length) useMarketStore.setState(updates);
  }, 250);
}

function isFiniteNum(v: unknown): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

export const useMarketStore = create<MarketState>((set, get) => ({
  prices: {},
  prevPrices: {},
  candles: {},
  klines: {},
  orderbooks: {},
  regime: {},
  marketInfo: {},
  tape: {},
  lastTickAt: 0,

  onTick: (tick) => {
    if (!tick?.symbol || !isFiniteNum(tick.price) || tick.price <= 0) return;
    // Buffer price — don't trigger React re-render on every tick
    const current = get().prices[tick.symbol] ?? tick.price;
    _pendingPrices[tick.symbol] = { price: tick.price, prev: current };
    if (isFiniteNum(tick.quantity) && tick.quantity > 0) {
      const arr = _pendingTape[tick.symbol] ?? (_pendingTape[tick.symbol] = []);
      arr.push(tick);
      if (arr.length > TAPE_SIZE) arr.splice(0, arr.length - TAPE_SIZE);
    }
    startPriceThrottle();
  },

  onCandles: (symbol, candles) =>
    set((s) => ({
      candles: { ...s.candles, [symbol]: candles },
    })),

  onKlines: (key, window) =>
    set((s) => ({
      klines: { ...s.klines, [key]: window },
    })),

  onSnapshot: (data) => {
    const sym = data.symbol;
    if (!sym) return;
    const s = get();
    const updates: Partial<MarketState> = {};

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

    // Market info fields (funding, volume, OI, mark/index; ≥ 2.15: 24h block, countdown…)
    const hasInfo = ["funding_rate", "volume_24h", "volume_24h_usd", "open_interest", "mark_price", "index_price",
      "change_24h_pct", "high_24h", "low_24h", "funding_countdown_sec", "spread_bps", "regime_since"]
      .some((k) => isFiniteNum((data as Record<string, unknown>)[k]));
    if (hasInfo) {
      const prev = s.marketInfo[sym] ?? { funding_rate: 0, volume_24h: 0, open_interest: 0, mark_price: 0, index_price: 0 };
      const next: MarketInfo = {
        funding_rate: isFiniteNum(data.funding_rate) ? data.funding_rate : prev.funding_rate,
        volume_24h: isFiniteNum(data.volume_24h_usd) ? data.volume_24h_usd : isFiniteNum(data.volume_24h) ? data.volume_24h : prev.volume_24h,
        open_interest: isFiniteNum(data.open_interest) ? data.open_interest : prev.open_interest,
        mark_price: isFiniteNum(data.mark_price) ? data.mark_price : prev.mark_price,
        index_price: isFiniteNum(data.index_price) ? data.index_price : prev.index_price,
        funding_countdown_sec: isFiniteNum(data.funding_countdown_sec) ? data.funding_countdown_sec : prev.funding_countdown_sec,
        change_24h_pct: isFiniteNum(data.change_24h_pct) ? data.change_24h_pct : prev.change_24h_pct,
        high_24h: isFiniteNum(data.high_24h) ? data.high_24h : prev.high_24h,
        low_24h: isFiniteNum(data.low_24h) ? data.low_24h : prev.low_24h,
        spread_bps: isFiniteNum(data.spread_bps) ? data.spread_bps : (data.orderbook && isFiniteNum(data.orderbook.spread_bps) ? data.orderbook.spread_bps : prev.spread_bps),
        regime_since: isFiniteNum(data.regime_since) ? data.regime_since : prev.regime_since,
        regime_timeframe_min: isFiniteNum(data.regime_timeframe_min) ? data.regime_timeframe_min : prev.regime_timeframe_min,
        updated: isFiniteNum(data.timestamp) ? data.timestamp : Date.now() / 1000,
      };
      updates.marketInfo = { ...s.marketInfo, [sym]: next };
    }

    if (Object.keys(updates).length > 0) {
      set(updates);
    }
  },
}));
