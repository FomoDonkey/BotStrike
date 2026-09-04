import { useMemo } from "react";
import { useShallow } from "zustand/shallow";
import { useMarketStore } from "@/stores/marketStore";
import { SYMBOLS } from "@/lib/constants";
import { change24h, stats24h } from "@/lib/market";
import { useVenueMarkets } from "./useVenueMarkets";

/**
 * Per-symbol 24 h change: the VENUE's own figure, falling back to the candles in memory.
 *
 * The marquee is read beside the venue's own ticker, where BTC said +3.72 % and ours said +3.98 %
 * because the stream is Binance and the two exchanges close their day on different prints
 * (audit 2026-09-04).
 */
export function useSymbolChanges(nowSec: number): Record<string, number | null> {
  const candles = useMarketStore(useShallow((s) => s.candles));
  const prices = useMarketStore(useShallow((s) => s.prices));
  const { byMarket } = useVenueMarkets();
  const minute = Math.floor(nowSec / 60);
  return useMemo(() => {
    const out: Record<string, number | null> = {};
    for (const sym of SYMBOLS) {
      const venue = byMarket.get(sym)?.change_24h_pct;
      out[sym] = typeof venue === "number" ? venue : change24h(stats24h(candles[sym], minute * 60), prices[sym] || 0);
    }
    return out;
  }, [candles, prices, byMarket, minute]);
}
