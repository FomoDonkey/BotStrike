import { create } from "zustand";
import { STORE_FLUSH_MS } from "@/lib/constants";
import type { PositionData } from "@/lib/api";

// PositionData lives in lib/api.ts (contract §2) — re-exported so existing imports keep working.
export type { PositionData };

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

/** One price/RSI pivot of a divergence signal (contract §2 / §5). */
export interface DivergencePivot {
  ts: number;
  price: number;
  rsi?: number;
}

/**
 * Signal metadata as the strategies emit it (`Signal.metadata`, serialised as-is). Only the keys
 * the UI knows how to render are typed; everything else is shown generically in the feed.
 */
export interface SignalMetadata {
  action?: string;
  exit_reason?: string;
  trigger?: string;
  confirmations?: string[] | Record<string, boolean | number | string>;
  rsi?: number;
  adx?: number;
  zscore?: number;
  z_score?: number;
  atr?: number;
  atr_bps?: number;
  regime?: string;
  timeframe_min?: number;
  /** DIVERGENCE */
  divergence_type?: string; // regular | hidden
  type?: string;
  pivots?: DivergencePivot[] | { p1?: DivergencePivot; p2?: DivergencePivot; first?: DivergencePivot; second?: DivergencePivot };
  rsi_gap?: number;
  trigger_level?: number;
  trigger_price?: number;
  macd_hist?: number;
  macd?: number | string | { hist?: number; state?: string; signal?: number; macd?: number };
  macd_state?: string;
  volume_ok?: boolean;
  [key: string]: unknown;
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
  metadata?: SignalMetadata | null;
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
  onMetrics: (metrics: Partial<MetricsData>) => void;
}

const MAX_TRADES = 100;
const MAX_SIGNALS = 50;

const FALLBACK_METRICS: MetricsData = {
  equity: 1000, pnl: 0, total_trades: 0, win_rate: 0,
  sharpe_ratio: 0, max_drawdown: 0, total_fees: 0,
};

/**
 * Keep only finite numbers: a `metrics` broadcast with a missing/null field used to land as
 * `undefined` in the store → `pnl / x` = NaN → AnimatedNumber's `value !== seen` was always true
 * → render loop → React #185 on every page. Never let NaN into the store.
 */
function sanitizeMetrics(input: Partial<MetricsData> | null | undefined, base: MetricsData): MetricsData {
  const out: MetricsData = { ...base };
  if (!input) return out;
  for (const key of Object.keys(FALLBACK_METRICS) as (keyof MetricsData)[]) {
    const v = input[key];
    if (typeof v === "number" && Number.isFinite(v)) out[key] = v;
  }
  return out;
}

// Restore last known metrics from localStorage to avoid showing stale value on reconnect
function loadCachedMetrics(): MetricsData {
  try {
    const raw = localStorage.getItem("bs_last_metrics");
    if (raw) return sanitizeMetrics(JSON.parse(raw) as Partial<MetricsData>, FALLBACK_METRICS);
  } catch {
    /* corrupt or unavailable localStorage — use defaults */
  }
  return { ...FALLBACK_METRICS };
}

// ── Write batching ───────────────────────────────────────────────
// On (re)connect the bridge replays the recent trades/signals and broadcasts positions for
// every symbol; each used to be its own synchronous set() → one React commit per message.
// Everything is queued here and flushed at most every STORE_FLUSH_MS in a single set().

const pendingTrades: TradeData[] = [];
const pendingSignals: SignalData[] = [];
const pendingPositions = new Map<string, PositionData[]>();
let flushTimer: ReturnType<typeof setTimeout> | null = null;

function scheduleFlush() {
  if (flushTimer) return;
  flushTimer = setTimeout(flushPending, STORE_FLUSH_MS);
}

function shallowEqualRecord(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (!a || !b || typeof a !== "object" || typeof b !== "object") return false;
  const ka = Object.keys(a as object);
  const kb = Object.keys(b as object);
  if (ka.length !== kb.length) return false;
  const ra = a as Record<string, unknown>;
  const rb = b as Record<string, unknown>;
  for (const k of ka) {
    if (!Object.is(ra[k], rb[k])) return false;
  }
  return true;
}

/** Same length and every position shallow-equal → nothing to re-render. */
export function positionsEqual(a: PositionData[] | undefined, b: PositionData[]): boolean {
  if (!a) return false; // first broadcast for a symbol always lands
  if (a === b) return true;
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (!shallowEqualRecord(a[i], b[i])) return false;
  }
  return true;
}

function flushPending() {
  flushTimer = null;
  const s = useTradingStore.getState();
  const patch: Partial<TradingState> = {};

  if (pendingTrades.length) {
    patch.recentTrades = [...s.recentTrades, ...pendingTrades].slice(-MAX_TRADES);
    pendingTrades.length = 0;
  }
  if (pendingSignals.length) {
    patch.recentSignals = [...s.recentSignals, ...pendingSignals].slice(-MAX_SIGNALS);
    pendingSignals.length = 0;
  }
  if (pendingPositions.size) {
    let next: Record<string, PositionData[]> | null = null;
    for (const [sym, arr] of pendingPositions) {
      const cur = s.positions[sym];
      if (cur && positionsEqual(cur, arr)) continue;
      if (!cur && arr.length === 0) continue; // "no positions" for a symbol we never had — noise
      if (!next) next = { ...s.positions };
      next[sym] = arr;
    }
    pendingPositions.clear();
    if (next) patch.positions = next;
  }

  if (Object.keys(patch).length > 0) useTradingStore.setState(patch);
}

/** Test/diagnostic hook: apply whatever is queued right now. */
export function flushTradingStore() {
  if (flushTimer) {
    clearTimeout(flushTimer);
    flushTimer = null;
  }
  flushPending();
}

export const useTradingStore = create<TradingState>((set) => ({
  positions: {},
  recentTrades: [],
  recentSignals: [],
  metrics: loadCachedMetrics(),

  onPositions: (symbol, positions) => {
    if (!symbol) return;
    pendingPositions.set(symbol, Array.isArray(positions) ? positions : []);
    scheduleFlush();
  },

  onTrade: (trade) => {
    pendingTrades.push(trade);
    if (pendingTrades.length > MAX_TRADES) pendingTrades.splice(0, pendingTrades.length - MAX_TRADES);
    scheduleFlush();
  },

  onSignal: (signal) => {
    pendingSignals.push(signal);
    if (pendingSignals.length > MAX_SIGNALS) pendingSignals.splice(0, pendingSignals.length - MAX_SIGNALS);
    scheduleFlush();
  },

  onMetrics: (incoming) => {
    set((s) => {
      const metrics = sanitizeMetrics(incoming, s.metrics);
      if (shallowEqualRecord(metrics, s.metrics)) return {}; // identical broadcast → no render
      try { localStorage.setItem("bs_last_metrics", JSON.stringify(metrics)); } catch { /* storage unavailable */ }
      return { metrics };
    });
  },
}));
