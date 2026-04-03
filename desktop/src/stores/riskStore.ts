import { create } from "zustand";

interface RiskState {
  equity: number;
  drawdown_pct: number;
  max_drawdown_pct: number;
  circuit_breaker_active: boolean;
  regime: string;

  onUpdate: (data: Record<string, unknown>) => void;
}

/** Safe numeric extraction — returns fallback if value is null, undefined, NaN, or non-number */
function safeNum(val: unknown, fallback: number): number {
  if (typeof val !== "number" || Number.isNaN(val)) return fallback;
  return val;
}

export const useRiskStore = create<RiskState>((set) => ({
  equity: 300,
  drawdown_pct: 0,
  max_drawdown_pct: 0.10,
  circuit_breaker_active: false,
  regime: "UNKNOWN",

  onUpdate: (data) =>
    set((s) => ({
      equity: safeNum(data.equity, s.equity),
      drawdown_pct: safeNum(data.drawdown_pct, s.drawdown_pct),
      max_drawdown_pct: safeNum(data.max_drawdown_pct, s.max_drawdown_pct),
      circuit_breaker_active: typeof data.circuit_breaker_active === "boolean"
        ? data.circuit_breaker_active
        : s.circuit_breaker_active,
      regime: typeof data.regime === "string" && data.regime
        ? data.regime
        : s.regime,
    })),
}));
