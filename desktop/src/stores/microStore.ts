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

const MAX_MICRO_HISTORY = 120; // 2 min @ 1Hz — sufficient for charts

export const useMicroStore = create<MicroState>((set) => ({
  snapshots: {},
  history: {},

  onUpdate: (data) =>
    set((s) => {
      // Mutate history array in-place to avoid spreading entire history object
      const sym = data.symbol;
      const prev = s.history[sym];
      let arr: MicroData[];
      if (!prev) {
        arr = [data];
      } else if (prev.length >= MAX_MICRO_HISTORY) {
        // Reuse array, shift out oldest, push new
        arr = prev.slice(-(MAX_MICRO_HISTORY - 1));
        arr.push(data);
      } else {
        arr = [...prev, data];
      }
      return {
        snapshots: { ...s.snapshots, [sym]: data },
        history: { ...s.history, [sym]: arr },
      };
    }),
}));
