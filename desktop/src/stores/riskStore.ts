import { create } from "zustand";
import type { RiskResponse } from "@/lib/api";

export type KilledStrategies = RiskResponse["killed_strategies"];

interface RiskState {
  equity: number;
  drawdown_pct: number;
  max_drawdown_pct: number;
  circuit_breaker_active: boolean;
  regime: string;
  // Per-symbol regimes to avoid oscillation when multiple symbols broadcast
  regimes: Record<string, string>;

  // Bridge ≥ 2.14 — /api/risk + the same fields on the WS `risk_update` broadcast
  peak_equity: number;
  daily_pnl: number;
  daily_limit: number;
  max_daily_loss_pct: number;
  weekly_pnl: number;
  weekly_limit: number;
  max_weekly_loss_pct: number;
  drawdown_halted: boolean;
  killed_strategies: KilledStrategies;
  compounding_enabled: boolean | null; // null → bridge did not say (older bridge)
  equity_basis: number;
  /** Date.now() of the last /api/risk snapshot (0 = never) */
  restLoadedAt: number;

  onUpdate: (data: Record<string, unknown>) => void;
  onRiskSnapshot: (data: RiskResponse) => void;
}

/** Safe numeric extraction — returns fallback if value is null, undefined, NaN, or non-number */
function safeNum(val: unknown, fallback: number): number {
  if (typeof val !== "number" || Number.isNaN(val)) return fallback;
  return val;
}

function safeBool(val: unknown, fallback: boolean): boolean {
  return typeof val === "boolean" ? val : fallback;
}

function safeGetItem(key: string): string | null {
  try { return localStorage.getItem(key); } catch { return null; }
}

function safeSetItem(key: string, value: string) {
  try { localStorage.setItem(key, value); } catch { /* ignore */ }
}

/** Reason text for a killed strategy — the bridge sends a string or an object with `reason`. */
export function killReason(v: KilledStrategies[string]): string {
  if (typeof v === "string") return v;
  if (v && typeof v === "object" && typeof v.reason === "string") return v.reason;
  return "killed by edge monitor";
}

// Restore last known equity from localStorage (prevents flash of $1000 on reload)
const savedEquity = parseFloat(safeGetItem("botstrike-last-equity") || "1000");

/** Fields shared by the WS broadcast and the REST snapshot (all optional on the wire). */
function extendedFields(data: Record<string, unknown>, s: RiskState): Partial<RiskState> {
  const out: Partial<RiskState> = {};
  if ("peak_equity" in data) out.peak_equity = safeNum(data.peak_equity, s.peak_equity);
  if ("daily_pnl" in data) out.daily_pnl = safeNum(data.daily_pnl, s.daily_pnl);
  if ("daily_limit" in data) out.daily_limit = safeNum(data.daily_limit, s.daily_limit);
  if ("max_daily_loss_pct" in data) out.max_daily_loss_pct = safeNum(data.max_daily_loss_pct, s.max_daily_loss_pct);
  if ("weekly_pnl" in data) out.weekly_pnl = safeNum(data.weekly_pnl, s.weekly_pnl);
  if ("weekly_limit" in data) out.weekly_limit = safeNum(data.weekly_limit, s.weekly_limit);
  if ("max_weekly_loss_pct" in data) out.max_weekly_loss_pct = safeNum(data.max_weekly_loss_pct, s.max_weekly_loss_pct);
  if ("drawdown_halted" in data) out.drawdown_halted = safeBool(data.drawdown_halted, s.drawdown_halted);
  if ("compounding_enabled" in data) out.compounding_enabled = safeBool(data.compounding_enabled, s.compounding_enabled ?? false);
  if ("equity_basis" in data) out.equity_basis = safeNum(data.equity_basis, s.equity_basis);
  if (data.killed_strategies && typeof data.killed_strategies === "object") {
    out.killed_strategies = data.killed_strategies as KilledStrategies;
  }
  return out;
}

export const useRiskStore = create<RiskState>((set) => ({
  equity: Number.isFinite(savedEquity) ? savedEquity : 1000,
  drawdown_pct: 0,
  max_drawdown_pct: 0.10,
  circuit_breaker_active: false,
  regime: "UNKNOWN",
  regimes: {},

  peak_equity: 0,
  daily_pnl: 0,
  daily_limit: 0,
  max_daily_loss_pct: 0,
  weekly_pnl: 0,
  weekly_limit: 0,
  max_weekly_loss_pct: 0,
  drawdown_halted: false,
  killed_strategies: {},
  compounding_enabled: null,
  equity_basis: 0,
  restLoadedAt: 0,

  onUpdate: (data) =>
    set((s) => {
      const newEquity = safeNum(data.equity, s.equity);
      // Persist equity to localStorage for reload resilience
      if (newEquity !== s.equity) {
        safeSetItem("botstrike-last-equity", newEquity.toFixed(2));
      }

      // Track per-symbol regime to avoid oscillation
      const symbol = typeof data.symbol === "string" ? data.symbol : "";
      const newRegime = typeof data.regime === "string" && data.regime ? data.regime : "";
      const updatedRegimes = symbol && newRegime
        ? { ...s.regimes, [symbol]: newRegime }
        : s.regimes;

      // Display regime: prefer BTC, then first available
      const displayRegime = updatedRegimes["BTC-USD"]
        || Object.values(updatedRegimes)[0]
        || s.regime;

      // WS sends `circuit_breaker_active`; REST sends `circuit_breaker` — accept both.
      const cb = typeof data.circuit_breaker_active === "boolean"
        ? data.circuit_breaker_active
        : typeof data.circuit_breaker === "boolean"
          ? data.circuit_breaker
          : s.circuit_breaker_active;

      return {
        equity: newEquity,
        drawdown_pct: safeNum(data.drawdown_pct, s.drawdown_pct),
        max_drawdown_pct: safeNum(data.max_drawdown_pct, s.max_drawdown_pct),
        circuit_breaker_active: cb,
        regime: displayRegime,
        regimes: updatedRegimes,
        ...extendedFields(data, s),
      };
    }),

  onRiskSnapshot: (data) =>
    set((s) => ({
      equity: safeNum(data.equity, s.equity),
      drawdown_pct: safeNum(data.drawdown_pct, s.drawdown_pct),
      max_drawdown_pct: safeNum(data.max_drawdown_pct, s.max_drawdown_pct),
      circuit_breaker_active: safeBool(data.circuit_breaker, s.circuit_breaker_active),
      ...extendedFields(data as unknown as Record<string, unknown>, s),
      restLoadedAt: Date.now(),
    })),
}));
