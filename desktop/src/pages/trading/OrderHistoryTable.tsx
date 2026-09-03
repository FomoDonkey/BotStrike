import { useMemo } from "react";
import type { TradeRecord } from "@/lib/api";
import { HINTS } from "@/lib/hints";
import { Chip, SideChip, StrategyTag } from "@/components/ui/Chip";
import { DataTable, type Column } from "@/components/ui/DataTable";
import { PnlCell } from "@/components/shared/TradeChips";
import { cn, formatDateTime, formatPrice, formatSize, formatUSD } from "@/lib/utils";
import { isClosedTrade } from "./useTradeHistory";

/** One fill (ENTRY or EXIT) derived from the trade DB rows. */
export interface OrderRow {
  key: string;
  ts: number | string;
  symbol: string;
  kind: "ENTRY" | "EXIT" | "FUNDING";
  side: string;
  price: number;
  size: number;
  orderType?: string;
  slippageBps?: number;
  spreadBps?: number;
  regime?: string;
  strategy: string;
  fee: number;
  pnl?: number;
  trigger?: string;
  exitReason?: string;
}

/** Explode trade DB rows into ENTRY / EXIT fills, newest first. */
function orderRows(trades: TradeRecord[]): OrderRow[] {
  const out: OrderRow[] = [];
  for (const t of trades) {
    // A funding settlement is a cash flow, not a fill. Rendered as an ENTRY it showed a phantom
    // order: side SELL (from the string "FUNDING"), a mark price and size 0 (audit 2026-09-03).
    if (t.trade_type === "FUNDING") {
      out.push({
        key: `f-${t.trade_id ?? t.id ?? ""}-${t.entry_ts ?? t.entry_time}`,
        ts: t.entry_ts || t.entry_time,
        symbol: t.symbol, kind: "FUNDING", side: "FUNDING", price: t.entry_price, size: 0,
        orderType: "funding", strategy: t.strategy, fee: 0, pnl: t.pnl, trigger: "8h settlement",
      });
      continue;
    }
    const closed = isClosedTrade(t);
    const kind: "ENTRY" | "EXIT" = t.trade_type === "ENTRY" ? "ENTRY" : closed ? "EXIT" : "ENTRY";
    if (kind === "EXIT") {
      out.push({
        key: `x-${t.id ?? t.trade_id ?? ""}-${t.exit_ts ?? t.exit_time}`,
        ts: t.exit_ts || t.exit_time,
        symbol: t.symbol, kind, side: t.side, price: t.exit_price, size: t.quantity, orderType: t.order_type,
        slippageBps: t.slippage_bps, spreadBps: t.spread_bps, regime: t.regime, strategy: t.strategy, fee: t.fee || 0, pnl: t.pnl,
        trigger: t.trigger, exitReason: t.exit_reason,
      });
      // the entry fill of the same round-trip
      if (t.entry_price > 0 && (t.entry_ts || t.entry_time)) {
        out.push({
          key: `e-${t.id ?? t.trade_id ?? ""}-${t.entry_ts ?? t.entry_time}`,
          ts: t.entry_ts || t.entry_time,
          symbol: t.symbol, kind: "ENTRY", side: t.side === "SELL" ? "BUY" : "SELL", price: t.entry_price, size: t.quantity, orderType: t.order_type,
          spreadBps: t.spread_bps, regime: t.regime, strategy: t.strategy, fee: 0, trigger: t.trigger,
        });
      }
    } else {
      out.push({
        key: `e-${t.id ?? t.trade_id ?? ""}-${t.entry_ts ?? t.entry_time}`,
        ts: t.entry_ts || t.entry_time,
        symbol: t.symbol, kind: "ENTRY", side: t.side, price: t.entry_price, size: t.quantity, orderType: t.order_type,
        slippageBps: t.slippage_bps, spreadBps: t.spread_bps, regime: t.regime, strategy: t.strategy, fee: t.fee || 0, trigger: t.trigger,
      });
    }
  }
  const ms = (v: number | string) => (typeof v === "number" ? (v > 1e11 ? v : v * 1000) : Date.parse(v) || 0);
  out.sort((a, b) => ms(b.ts) - ms(a.ts));
  return out;
}

interface OrderHistoryTableProps {
  trades: TradeRecord[];
  symbol?: string;
  loading?: boolean;
  filter?: (r: OrderRow) => boolean;
}

/** Order History (spec §3.1): ENTRY / EXIT rows with order type, slippage, spread, regime. */
export function OrderHistoryTable({ trades, symbol, loading, filter }: OrderHistoryTableProps) {
  const rows = useMemo(() => {
    const all = orderRows(trades);
    return filter ? all.filter(filter) : all;
  }, [trades, filter]);

  const columns: Column<OrderRow>[] = [
    { id: "time", label: "Time", align: "l", render: (r) => formatDateTime(r.ts) },
    { id: "symbol", label: "Symbol", align: "l", sortValue: (r) => r.symbol, render: (r) => <span className="font-semibold">{r.symbol}</span> },
    { id: "kind", label: "Order", align: "l", sortValue: (r) => r.kind, render: (r) => <Chip tone={r.kind === "ENTRY" ? "blue" : r.kind === "FUNDING" ? "amber" : "neutral"} size="xs">{r.kind}</Chip> },
    { id: "side", label: "Side", align: "l", render: (r) => r.kind === "FUNDING" ? <span className="text-text-2 font-medium">carry</span> : <SideChip side={r.side} size="xs" labels="order" /> },
    { id: "type", label: "Type", align: "l", render: (r) => <span className="font-medium">{r.orderType ? r.orderType.replace(/_/g, " ") : "market"}</span> },
    { id: "price", label: "Fill price", sortValue: (r) => r.price, render: (r) => <span className="num">{formatPrice(r.price || 0)}</span> },
    { id: "size", label: "Size", sortValue: (r) => r.size, render: (r) => r.kind === "FUNDING" ? <span className="text-text-3">---</span> : <span className="num">{formatSize(r.size)}</span> },
    { id: "value", label: "Value", sortValue: (r) => r.price * r.size, render: (r) => r.kind === "FUNDING" ? <span className="text-text-3">---</span> : <span className="num">{formatUSD((r.price || 0) * (r.size || 0))}</span> },
    { id: "slip", label: "Slippage", hint: HINTS.slippage, render: (r) => typeof r.slippageBps === "number" ? <span className={cn("num", r.slippageBps > 0 ? "text-rose" : "text-text")}>{r.slippageBps.toFixed(1)} bps</span> : <span className="text-text-3">---</span> },
    { id: "spread", label: "Spread", hint: HINTS.spread, render: (r) => typeof r.spreadBps === "number" ? <span className="num">{r.spreadBps.toFixed(2)} bps</span> : <span className="text-text-3">---</span> },
    { id: "fee", label: "Fee", render: (r) => <span className="num">{formatUSD(r.fee)}</span> },
    { id: "pnl", label: "PNL", render: (r) => typeof r.pnl === "number" ? <PnlCell pnl={r.pnl} inline /> : <span className="text-text-3">---</span> },
    { id: "reason", label: "Trigger / exit", align: "l", render: (r) => <span className="font-medium">{r.kind === "EXIT" ? (r.exitReason ?? "---") : (r.trigger ?? "---")}</span> },
    { id: "strategy", label: "Strategy", align: "l", render: (r) => <StrategyTag strategy={r.strategy} /> },
    { id: "regime", label: "Regime", align: "l", render: (r) => r.regime ? r.regime.replace(/_/g, " ") : "---" },
  ];

  return (
    <DataTable
      columns={columns}
      rows={rows}
      rowKey={(r) => r.key}
      rowClassName={(r) => (symbol && r.symbol === symbol ? "is-open" : undefined)}
      minWidth="1400px"
      emptyText={loading ? "Loading order history…" : "No orders found"}
      emptySub={loading ? undefined : "Every paper fill (entry and exit) is listed here once the engine trades"}
    />
  );
}
