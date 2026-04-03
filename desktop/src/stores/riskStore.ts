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
    set((s) => ({
      equity: data.equity ?? s.equity,
      drawdown_pct: data.drawdown_pct ?? s.drawdown_pct,
      max_drawdown_pct: data.max_drawdown_pct ?? s.max_drawdown_pct,
      circuit_breaker_active: data.circuit_breaker_active ?? s.circuit_breaker_active,
      regime: data.regime ?? s.regime,
    })),
}));
