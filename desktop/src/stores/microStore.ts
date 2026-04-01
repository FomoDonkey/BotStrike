import { create } from "zustand";

export interface MicroData {
  symbol: string;
  timestamp: number;
  risk_score: number;
  vpin?: { vpin: number; cdf: number; is_toxic: boolean };
  hawkes?: { intensity: number; multiplier: number; is_spike: boolean };
  as_spread?: { bid_spread_bps: number; ask_spread_bps: number; reservation_price: number };
  kyle_lambda?: { lambda_bps: number; impact_stress: number; adverse_selection_bps: number };
}

interface MicroState {
  snapshots: Record<string, MicroData>;
  history: Record<string, MicroData[]>;

  onUpdate: (data: MicroData) => void;
}

export const useMicroStore = create<MicroState>((set) => ({
  snapshots: {},
  history: {},

  onUpdate: (data) =>
    set((s) => ({
      snapshots: { ...s.snapshots, [data.symbol]: data },
      history: {
        ...s.history,
        [data.symbol]: [...(s.history[data.symbol] || []).slice(-299), data],
      },
    })),
}));
