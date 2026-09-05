import { useMemo } from "react";
import { api, type TradeRecord } from "@/lib/api";
import type { TradeData } from "@/stores/tradingStore";
import { useEndpoint } from "@/hooks/useEndpoint";

const FETCH_LIMIT = 1000; // ENTRY + EXIT rows → the journal reads whole episodes, trims included
const POLL_MS = 15_000;

/** A closed trade is the EXIT row (round-trip PnL, fee, duration); legacy rows lack trade_type. */
export function isClosedTrade(t: TradeRecord): boolean {
  if (t.trade_type) return t.trade_type === "EXIT";
  return !!t.exit_time && (t.pnl || 0) !== 0;
}

/**
 * Historical fills from the trade DB (/api/trades) for the whole terminal: Trade History shows
 * the closed rows, Order History every ENTRY / EXIT row, the chart draws every fill as a marker.
 */
export function useTradeHistory() {
  const ep = useEndpoint(() => api.trades(FETCH_LIMIT), POLL_MS);
  const trades = useMemo(() => ep.data?.trades ?? [], [ep.data]);
  const closed = useMemo(() => trades.filter(isClosedTrade), [trades]);

  const markers = useMemo<TradeData[]>(() => {
    const out: TradeData[] = [];
    for (const t of trades) {
      if (t.trade_type === "FUNDING") continue;   // a settlement is not a fill: it drew a phantom marker
      const entryTs = t.entry_ts || (t.entry_time ? Date.parse(t.entry_time) / 1000 : 0);
      const exitTs = t.exit_ts || (t.exit_time ? Date.parse(t.exit_time) / 1000 : 0);
      if (entryTs > 0 && t.entry_price > 0) {
        out.push({ symbol: t.symbol, side: t.side, trade_type: "ENTRY", price: t.entry_price, quantity: t.quantity, fee: t.trade_type === "ENTRY" ? (t.fee || 0) : 0, strategy: t.strategy, timestamp: entryTs, pnl: 0, order_id: t.order_id });
      }
      if (exitTs > 0 && t.exit_price > 0 && isClosedTrade(t)) {
        out.push({ symbol: t.symbol, side: t.side, trade_type: "EXIT", price: t.exit_price, quantity: t.quantity, fee: t.fee || 0, strategy: t.strategy, timestamp: exitTs, pnl: t.pnl || 0, exit_reason: t.exit_reason, order_id: t.order_id });
      }
    }
    return out;
  }, [trades]);

  return { trades, closed, markers, loading: !ep.loaded, error: ep.error };
}
