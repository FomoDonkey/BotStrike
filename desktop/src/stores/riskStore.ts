import { create } from "zustand";

interface RiskState {
  equity: number;
  drawdown_pct: number;
  max_drawdown_pct: number;
  circuit_breaker_active: boolean;
  regime: string;
  // Per-symbol regimes to avoid oscillation when multiple symbols broadcast
  regimes: Record<string, string>;

  onUpdate: (data: Record<string, unknown>) => void;
}

/** Safe numeric extraction — returns fallback if value is null, undefined, NaN, or non-number */
function safeNum(val: unknown, fallback: number): number {
  if (typeof val !== "number" || Number.isNaN(val)) return fallback;
  return val;
}

function safeGetItem(key: string): string | null {
  try { return localStorage.getItem(key); } catch { return null; }
}

function safeSetItem(key: string, value: string) {
  try { localStorage.setItem(key, value); } catch { /* ignore */ }
}

// Restore last known equity from localStorage (prevents flash of $1000 on reload)
const savedEquity = parseFloat(safeGetItem("botstrike-last-equity") || "1000");

export const useRiskStore = create<RiskState>((set) => ({
  equity: Number.isFinite(savedEquity) ? savedEquity : 1000,
  drawdown_pct: 0,
  max_drawdown_pct: 0.10,
  circuit_breaker_active: false,
  regime: "UNKNOWN",
  regimes: {},

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

      return {
        equity: newEquity,
        drawdown_pct: safeNum(data.drawdown_pct, s.drawdown_pct),
        max_drawdown_pct: safeNum(data.max_drawdown_pct, s.max_drawdown_pct),
        circuit_breaker_active: typeof data.circuit_breaker_active === "boolean"
          ? data.circuit_breaker_active
          : s.circuit_breaker_active,
        regime: displayRegime,
        regimes: updatedRegimes,
      };
    }),
}));
