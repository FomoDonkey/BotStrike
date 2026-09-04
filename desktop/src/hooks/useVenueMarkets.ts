import { useMemo, useSyncExternalStore } from "react";
import { api, ApiError, type VenueMarket, type MarketsResponse } from "@/lib/api";

// The picker sits beside the market header, which refreshes every 4 s. At 30 s the two printed
// visibly different marks for the same market at the same instant (2026-09-04). This endpoint is
// served from the bridge's own cache, so polling it faster costs the venue nothing.
const VENUE_POLL_MS = 10_000;

interface Snapshot {
  data: MarketsResponse | null;
  missing: boolean;
}

// ONE poll for the whole page. Eleven components read the venue list (header, picker, footer,
// chart, book, tape, favourites…) and each used to run its own timer: 12 requests for the same
// payload in 16 s, measured on the Trade page (2026-09-05). The first subscriber starts the poll,
// the last one stops it, and everyone reads the same snapshot.
let snapshot: Snapshot = { data: null, missing: false };
const listeners = new Set<() => void>();
let timer: ReturnType<typeof setInterval> | null = null;
let inflight = false;

async function pull() {
  if (inflight) return;
  inflight = true;
  try {
    const data = await api.markets();
    snapshot = { data, missing: false };
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) snapshot = { data: null, missing: true };
    // any other failure keeps the last good snapshot on screen
  } finally {
    inflight = false;
  }
  for (const l of listeners) l();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  if (!timer) {
    timer = setInterval(() => { void pull(); }, VENUE_POLL_MS);
    void pull();
  }
  return () => {
    listeners.delete(listener);
    if (!listeners.size && timer) {
      clearInterval(timer);
      timer = null;
    }
  };
}

const NONE: Snapshot = { data: null, missing: false };

/**
 * Every market as the VENUE describes it, keyed by symbol.
 *
 * The socket streams Binance — the strategies' price reference, not the book an order reaches — so
 * reading the picker's price, 24 h change, volume and open-interest columns off it printed Binance's
 * market on a Strike screen (24 h volume out by a factor of 8,000, open interest by 30,000) and left
 * the other 27 markets empty. One cached venue snapshot answers all of it (audit 2026-09-04).
 */
export function useVenueMarkets(enabled = true) {
  const snap = useSyncExternalStore(enabled ? subscribe : () => () => undefined, () => (enabled ? snapshot : NONE), () => NONE);
  return useMemo(() => {
    const byMarket = new Map<string, VenueMarket>();
    for (const v of snap.data?.markets ?? []) byMarket.set(v.symbol, v);
    return { byMarket, list: snap.data?.markets ?? [], missing: snap.missing, quoteAgeSec: snap.data?.quote_age_sec ?? null };
  }, [snap]);
}


/** True when the bridge does not stream this market: its ladder, tape and chart never fill in, so a
 *  "waiting…" placeholder is a promise the terminal cannot keep (2026-09-04). */
export function useUnstreamed(symbol: string): boolean {
  const { byMarket } = useVenueMarkets();
  const row = byMarket.get(symbol);
  return row ? row.feed === false : false;
}
