import { useMemo, useState } from "react";
import { api, ApiError, type PositionData, type ProtectiveOrder } from "@/lib/api";
import { usePolling } from "@/hooks/usePolling";
import { Hint } from "@/components/shared/Hint";
import { HINTS } from "@/lib/hints";
import { SideChip, StrategyTag } from "@/components/shared/TradeChips";
import { cn, formatPrice, formatSignedPct, formatSize, formatUSD } from "@/lib/utils";
import { isLong, pnlDistancePct } from "@/lib/market";

const POLL_MS = 5_000;

interface OrdersTableProps {
  /** Open positions — fallback source of SL/TP levels when /api/orders is missing (bridge 2.14) */
  positions: PositionData[];
  symbol?: string;
}

/** Protective orders synthesised from the positions' own SL/TP fields (bridge ≥ 2.15 positions). */
function ordersFromPositions(positions: PositionData[]): ProtectiveOrder[] {
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

function typeLabel(t: string): string {
  const u = t.toUpperCase();
  if (u === "STOP" || u === "STOP_MARKET" || u === "SL") return "Stop loss";
  if (u === "TAKE_PROFIT" || u === "TP" || u === "TAKE_PROFIT_MARKET") return "Take profit";
  return t.replace(/_/g, " ");
}

/** Orders tab: live SL/TP orders from GET /api/orders (5 s), derived from positions on older bridges. */
export function OrdersTable({ positions, symbol }: OrdersTableProps) {
  const [orders, setOrders] = useState<ProtectiveOrder[] | null>(null);
  const [missing, setMissing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  usePolling(async () => {
    try {
      const r = await api.orders();
      setOrders(r.orders ?? []);
      setMissing(false);
      setError(null);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) { setMissing(true); setOrders(null); }
      else setError(e instanceof ApiError ? e.message : String(e));
    }
  }, POLL_MS);

  const derived = useMemo(() => ordersFromPositions(positions), [positions]);
  const rows = orders ?? derived;
  const markBySymbol = useMemo(() => {
    const m: Record<string, number> = {};
    for (const p of positions) if (p.mark_price > 0) m[p.symbol] = p.mark_price;
    return m;
  }, [positions]);

  return (
    <div className="flex flex-col flex-1 min-h-0">
      {rows.length === 0 ? (
        <div className="flex-1 flex flex-col items-center justify-center text-text-faint text-xs py-8 gap-1">
          <span>No protective orders</span>
          {missing && <span className="text-[10.5px]">GET /api/orders needs bridge ≥ 2.15 — showing SL/TP carried by the positions (none reported).</span>}
          {error && <span className="text-[10.5px] text-loss font-mono">{error}</span>}
        </div>
      ) : (
        <div className="overflow-auto flex-1 min-h-0">
          <table className="term-table min-w-[820px]">
            <thead>
              <tr>
                <th className="l">Symbol</th>
                <th className="l">Type</th>
                <th className="l">Side</th>
                <th>Trigger price</th>
                <th><Hint title="Distance from mark in PnL direction of the position: negative = adverse (towards the stop), positive = favourable (towards the target)">Distance</Hint></th>
                <th>Size</th>
                <th>Value</th>
                <th className="l">Strategy</th>
                <th className="l">Position</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((o, i) => {
                // The order's side is the CLOSING side → the position is long when the order sells.
                const posSide = o.side === "SELL" ? "BUY" : "SELL";
                const mark = markBySymbol[o.symbol] ?? 0;
                // /api/orders reports price-direction (px / mark − 1); positions report PnL-direction.
                const d = orders
                  ? (mark > 0 ? pnlDistancePct(o.price, mark, posSide) : typeof o.distance_pct === "number" ? (isLong(posSide) ? o.distance_pct : -o.distance_pct) : null)
                  : (typeof o.distance_pct === "number" ? o.distance_pct : pnlDistancePct(o.price, mark, posSide));
                const isSl = /stop|sl/i.test(o.type);
                return (
                  <tr key={`${o.symbol}-${o.type}-${o.position_id ?? i}`} className={cn(symbol && o.symbol === symbol && "is-open")}>
                    <td className="l font-medium">{o.symbol}</td>
                    <td className="l">
                      <span className={cn("inline-flex items-center h-5 px-1.5 rounded text-[10px] font-semibold uppercase tracking-wider", isSl ? "bg-loss/10 text-loss" : "bg-profit/10 text-profit")}>
                        {typeLabel(o.type)}
                      </span>
                    </td>
                    <td className="l"><SideChip side={posSide} compact /> <span className="text-text-secondary text-[11.5px] ml-1" title="Closing side">{o.side}</span></td>
                    <td className="num">{formatPrice(o.price)}</td>
                    <td className={cn("num", d === null ? "text-text-faint" : d < 0 ? "text-loss" : "text-profit")}>{d === null ? "---" : formatSignedPct(d)}</td>
                    <td className="num">{formatSize(o.size)}</td>
                    <td className="num text-text-secondary">{formatUSD(o.price * o.size)}</td>
                    <td className="l"><StrategyTag strategy={o.strategy} /></td>
                    <td className="l text-text-faint text-[11px] font-mono">{o.position_id ?? "---"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {missing && rows.length > 0 && (
        <p className="px-3 py-1.5 text-[10.5px] text-text-faint border-t border-hairline-soft shrink-0" title={HINTS.sl}>
          Derived from the positions' SL/TP fields — GET /api/orders needs bridge ≥ 2.15.
        </p>
      )}
    </div>
  );
}
