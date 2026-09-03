import { useMemo } from "react";
import { useShallow } from "zustand/shallow";
import { useMarketStore } from "@/stores/marketStore";
import { SYMBOLS } from "@/lib/constants";
import { change24h, stats24h } from "@/lib/market";

/** Per-symbol 24h change: bridge value when sent, else derived from the 1m candles in memory (recomputed once a minute). */
export function useSymbolChanges(nowSec: number): Record<string, number | null> {
  const candles = useMarketStore(useShallow((s) => s.candles));
  const prices = useMarketStore(useShallow((s) => s.prices));
  const info = useMarketStore(useShallow((s) => s.marketInfo));
  const minute = Math.floor(nowSec / 60);
  return useMemo(() => {
    const out: Record<string, number | null> = {};
    for (const sym of SYMBOLS) {
      const bridge = info[sym]?.change_24h_pct;
      out[sym] = typeof bridge === "number" ? bridge : change24h(stats24h(candles[sym], minute * 60), prices[sym] || 0);
    }
    return out;
  }, [candles, prices, info, minute]);
}
