import { useMemo } from "react";
import { api, type VenueMarket } from "@/lib/api";
import { useEndpoint } from "./useEndpoint";

// The picker sits beside the market header, which refreshes every 4 s. At 30 s the two printed
// visibly different marks for the same market at the same instant (2026-09-04). This endpoint is
// served from the bridge's own cache, so polling it faster costs the venue nothing.
const VENUE_POLL_MS = 10_000;

/**
 * Every market as the VENUE describes it, keyed by symbol.
 *
 * The socket streams Binance — the strategies' price reference, not the book an order reaches — so
 * reading the picker's price, 24 h change, volume and open-interest columns off it printed Binance's
 * market on a Strike screen (24 h volume out by a factor of 8,000, open interest by 30,000) and left
 * the other 27 markets empty. One cached venue snapshot answers all of it (audit 2026-09-04).
 */
export function useVenueMarkets(enabled = true) {
  const ep = useEndpoint(() => api.markets(), VENUE_POLL_MS, "", enabled);
  return useMemo(() => {
    const byMarket = new Map<string, VenueMarket>();
    for (const v of ep.data?.markets ?? []) byMarket.set(v.symbol, v);
    return { byMarket, list: ep.data?.markets ?? [], missing: ep.missing, quoteAgeSec: ep.data?.quote_age_sec ?? null };
  }, [ep.data, ep.missing]);
}


/** True when the bridge does not stream this market: its ladder, tape and chart never fill in, so a
 *  "waiting…" placeholder is a promise the terminal cannot keep (2026-09-04). */
export function useUnstreamed(symbol: string): boolean {
  const { byMarket } = useVenueMarkets();
  const row = byMarket.get(symbol);
  return row ? row.feed === false : false;
}
