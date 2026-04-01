import { create } from "zustand";

interface RiskState {
  equity: number;
  drawdown_pct: number;
  max_drawdown_pct: number;
  circuit_breaker_active: boolean;
  regime: string;

  onUpdate: (data: any) => void;
}

export const useRiskStore = create<RiskState>((set) => ({
  equity: 300,
  drawdown_pct: 0,
  max_drawdown_pct: 0.10,
  circuit_breaker_active: false,
  regime: "UNKNOWN",

  onUpdate: (data) =>
    set({
      equity: data.equity ?? 300,
      drawdown_pct: data.drawdown_pct ?? 0,
      max_drawdown_pct: data.max_drawdown_pct ?? 0.10,
      circuit_breaker_active: data.circuit_breaker_active ?? false,
      regime: data.regime ?? "UNKNOWN",
    }),
}));
