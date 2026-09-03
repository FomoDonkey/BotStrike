import { useMemo } from "react";
import type { PositionData, ProtectiveOrder } from "@/lib/api";
import { useOrders } from "@/hooks/useOrders";
import { Chip, SideChip, StrategyTag } from "@/components/ui/Chip";
import { DataTable, type Column } from "@/components/ui/DataTable";
import { cn, formatPrice, formatSignedPct, formatSize, formatUSD } from "@/lib/utils";
import { isLong, pnlDistancePct } from "@/lib/market";

interface OrdersTableProps {
  /** Open positions — fallback source of SL/TP levels when /api/orders is missing (bridge 2.14) */
  positions: PositionData[];
  symbol?: string;
  /** Row filter (bottom panel filters) */
  filter?: (o: ProtectiveOrder) => boolean;
}

function typeLabel(t: string): string {
  const u = t.toUpperCase();
  if (u === "STOP" || u === "STOP_MARKET" || u === "SL") return "Stop loss";
  if (u === "TAKE_PROFIT" || u === "TP" || u === "TAKE_PROFIT_MARKET") return "Take profit";
  return t.replace(/_/g, " ");
}

/** Orders tab: live SL/TP orders from GET /api/orders (5 s), derived from positions on older bridges. */
export function OrdersTable({ positions, symbol, filter }: OrdersTableProps) {
  const { rows: all, fromRest, missing, error } = useOrders(positions);
  const rows = filter ? all.filter(filter) : all;
  const markBySymbol = useMemo(() => {
    const m: Record<string, number> = {};
    for (const p of positions) if (p.mark_price > 0) m[p.symbol] = p.mark_price;
    return m;
  }, [positions]);

  const columns: Column<ProtectiveOrder>[] = [
    { id: "symbol", label: "Symbol", align: "l", sortValue: (o) => o.symbol, render: (o) => <span className="font-semibold">{o.symbol}</span> },
    { id: "type", label: "Type", align: "l", render: (o) => <Chip tone={/stop|sl/i.test(o.type) ? "rose" : "mint"} size="xs">{typeLabel(o.type)}</Chip> },
    { id: "side", label: "Side", align: "l", render: (o) => <span className="inline-flex items-center gap-1.5"><SideChip side={o.side === "SELL" ? "BUY" : "SELL"} size="xs" compact /><span className="font-medium" title="Closing side">{o.side}</span></span> },
    { id: "price", label: "Trigger price", sortValue: (o) => o.price, render: (o) => <span className="num">{formatPrice(o.price)}</span> },
    {
      id: "distance", label: "Distance", hint: "Distance from mark in PnL direction of the position: negative = adverse (towards the stop), positive = favourable (towards the target)",
      render: (o) => {
        const posSide = o.side === "SELL" ? "BUY" : "SELL";
        const mark = markBySymbol[o.symbol] ?? 0;
        const d = fromRest
          ? (mark > 0 ? pnlDistancePct(o.price, mark, posSide) : typeof o.distance_pct === "number" ? (isLong(posSide) ? o.distance_pct : -o.distance_pct) : null)
          : (typeof o.distance_pct === "number" ? o.distance_pct : pnlDistancePct(o.price, mark, posSide));
        return <span className={cn("num", d === null ? "text-text-3" : d < 0 ? "text-rose" : "text-mint")}>{d === null ? "---" : formatSignedPct(d)}</span>;
      },
    },
    { id: "size", label: "Size", sortValue: (o) => o.size, render: (o) => <span className="num">{formatSize(o.size)}</span> },
    { id: "value", label: "Value", sortValue: (o) => o.price * o.size, render: (o) => <span className="num">{formatUSD(o.price * o.size)}</span> },
    { id: "strategy", label: "Strategy", align: "l", render: (o) => <StrategyTag strategy={o.strategy} /> },
    { id: "position", label: "Position", align: "l", render: (o) => <span className="font-medium text-text-2">{o.position_id ?? "---"}</span> },
  ];

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(o, i) => `${o.symbol}-${o.type}-${o.position_id ?? i}`}
        rowClassName={(o) => (symbol && o.symbol === symbol ? "is-open" : undefined)}
        minWidth="900px"
        emptyText="No open orders"
        emptySub={error ? error : missing ? "GET /api/orders needs bridge ≥ 2.15 — showing SL/TP carried by the positions (none reported)" : "Trend daily positions carry no SL/TP; MR / divergence positions show their protective orders here"}
      />
      {missing && rows.length > 0 && (
        <p className="px-3 py-1.5 text-[12px] font-medium text-text-2 border-t border-hairline shrink-0">Derived from the positions' SL/TP fields — GET /api/orders needs bridge ≥ 2.15.</p>
      )}
    </div>
  );
}
