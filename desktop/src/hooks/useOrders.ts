import { useMemo } from "react";
import { api, type PositionData, type ProtectiveOrder } from "@/lib/api";
import { isLong, pnlDistancePct } from "@/lib/market";
import { useEndpoint } from "./useEndpoint";

const POLL_MS = 5_000;

/** Protective orders synthesised from the positions' own SL/TP fields (bridge without /api/orders). */
export function ordersFromPositions(positions: PositionData[]): ProtectiveOrder[] {
  const out: ProtectiveOrder[] = [];
  for (const p of positions) {
    const long = isLong(p.side);
    const mark = p.mark_price > 0 ? p.mark_price : p.entry_price;
    if (typeof p.stop_loss === "number" && p.stop_loss > 0) {
      out.push({ symbol: p.symbol, type: "STOP", side: long ? "SELL" : "BUY", price: p.stop_loss, size: p.size, strategy: p.strategy, position_id: p.order_id, distance_pct: p.sl_distance_pct ?? pnlDistancePct(p.stop_loss, mark, p.side) });
    }
    if (typeof p.take_profit === "number" && p.take_profit > 0) {
      out.push({ symbol: p.symbol, type: "TAKE_PROFIT", side: long ? "SELL" : "BUY", price: p.take_profit, size: p.size, strategy: p.strategy, position_id: p.order_id, distance_pct: p.tp_distance_pct ?? pnlDistancePct(p.take_profit, mark, p.side) });
    }
  }
  return out;
}

/** GET /api/orders (5 s); derived from the positions on older bridges. Shared by the Orders tab and its count. */
export function useOrders(positions: PositionData[]) {
  const ep = useEndpoint(() => api.orders(), POLL_MS);
  const derived = useMemo(() => ordersFromPositions(positions), [positions]);
  const rows = ep.data?.orders ?? (ep.missing ? derived : ep.data ? [] : derived);
  return { rows, fromRest: !!ep.data, missing: ep.missing, error: ep.error };
}
