import { useMemo, useState } from "react";
import type { TradeRecord } from "@/lib/api";
import { HINTS } from "@/lib/hints";
import { Chip, SideChip, StrategyTag } from "@/components/ui/Chip";
import { DataTable, type Column } from "@/components/ui/DataTable";
import { PnlCell } from "@/components/shared/TradeChips";
import { cn, formatDateTime, formatPrice, formatSize, formatUSD, formatSignedMoney} from "@/lib/utils";
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
  /** an ENTRY that grew a position the book already held (a rebalance add or a re-buy), not an opening */
  add?: boolean;
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
        // Not "8h settlement": the venue's cadence is configurable and Strike settles hourly.
        orderType: "funding", strategy: t.strategy, fee: 0, pnl: t.pnl, trigger: "funding settlement",
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
      // The entry fill of the same round trip — ONLY for a legacy row without a trade_type, which
      // carried both legs. Since schema v3 every fill is its own row, so an EXIT row's entry
      // fields describe the position's average entry, not a fill: synthesising one from each EXIT
      // printed a phantom "ENTRY · BUY · fee $0.00" per trim and per exit, at the size of the exit,
      // beside the real ENTRY rows (2026-09-08; the Journal had the same bug on 2026-09-05).
      if (!t.trade_type && t.entry_price > 0 && (t.entry_ts || t.entry_time)) {
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
  // Walk the fills oldest-first per market: an ENTRY while a position is still held is an ADD.
  // Five re-buys at 22:08Z on 2026-09-21 printed as five ENTRY orders on positions that never closed.
  const held = new Map<string, number>();
  for (let i = out.length - 1; i >= 0; i--) {
    const r = out[i];
    if (r.kind === "FUNDING") continue;
    const k = `${r.symbol}|${r.strategy ?? ""}`;
    const cur = held.get(k) ?? 0;
    if (r.kind === "ENTRY") {
      r.add = cur > 0.02 * (cur + r.size);     // dust left by a full exit is not a held position
      held.set(k, cur + r.size);
    } else {
      held.set(k, Math.max(0, cur - r.size));
    }
  }
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
  // Strike settles funding EVERY HOUR: 1,655 settlement rows buried the 52 orders (2026-09-20).
  // They stay one click away, with their count, so the money is never hidden - only the noise.
  const [showFunding, setShowFunding] = useState(false);
  const { rows, fundingRows } = useMemo(() => {
    const all = orderRows(trades);
    const scoped = filter ? all.filter(filter) : all;
    const fundingRows = scoped.filter((r) => r.kind === "FUNDING").length;
    return { rows: showFunding ? scoped : scoped.filter((r) => r.kind !== "FUNDING"), fundingRows };
  }, [trades, filter, showFunding]);

  const columns: Column<OrderRow>[] = [
    { id: "time", label: "Time", align: "l", render: (r) => formatDateTime(r.ts) },
    { id: "symbol", label: "Symbol", align: "l", sortValue: (r) => r.symbol, render: (r) => <span className="font-semibold">{r.symbol}</span> },
    { id: "kind", label: "Order", align: "l", sortValue: (r) => (r.kind === "ENTRY" && r.add ? "ADD" : r.kind), render: (r) => <Chip tone={r.kind === "ENTRY" ? "blue" : r.kind === "FUNDING" ? "amber" : "neutral"} size="xs" title={r.kind === "ENTRY" && r.add ? "Grew a position the book already held (rebalance add or re-buy) — not a new trade" : undefined}>{r.kind === "ENTRY" && r.add ? "ADD" : r.kind}</Chip> },
    { id: "side", label: "Side", align: "l", render: (r) => r.kind === "FUNDING" ? <span className="text-text-2 font-medium">carry</span> : <SideChip side={r.side} size="xs" labels="order" /> },
    { id: "type", label: "Type", align: "l", render: (r) => <span className="font-medium">{r.orderType ? r.orderType.replace(/_/g, " ") : "market"}</span> },
    { id: "price", label: "Fill price", sortValue: (r) => r.price, render: (r) => <span className="num">{formatPrice(r.price || 0)}</span> },
    { id: "size", label: "Size", sortValue: (r) => r.size, render: (r) => r.kind === "FUNDING" ? <span className="text-text-3">---</span> : <span className="num">{formatSize(r.size)}</span> },
    { id: "value", label: "Value", sortValue: (r) => r.price * r.size, render: (r) => r.kind === "FUNDING" ? <span className="text-text-3">---</span> : <span className="num">{formatUSD((r.price || 0) * (r.size || 0))}</span> },
    { id: "slip", label: "Slippage", hint: HINTS.slippage, render: (r) => typeof r.slippageBps === "number" ? <span className={cn("num", r.slippageBps > 0 ? "text-rose" : "text-text")}>{r.slippageBps.toFixed(1)} bps</span> : <span className="text-text-3">---</span> },
    { id: "spread", label: "Spread", hint: HINTS.spread, render: (r) => typeof r.spreadBps === "number" ? <span className="num">{r.spreadBps.toFixed(2)} bps</span> : <span className="text-text-3">---</span> },
    { id: "fee", label: "Fee", render: (r) => <span className="num">{formatUSD(r.fee)}</span> },
    // A funding settlement is cents: at two decimals every row read "-$0.00", a signed zero.
    { id: "pnl", label: "PNL", render: (r) => typeof r.pnl !== "number" ? <span className="text-text-3">---</span>
        : r.kind === "FUNDING"
          ? <span className={cn("num", r.pnl < 0 ? "text-rose" : r.pnl > 0 ? "text-mint" : "text-text")}>{formatSignedMoney(r.pnl, 4)}</span>
          : <PnlCell pnl={r.pnl} inline /> },
    { id: "reason", label: "Trigger / exit", align: "l", render: (r) => <span className="font-medium">{r.kind === "EXIT" ? (r.exitReason ?? "---") : (r.trigger ?? "---")}</span> },
    { id: "strategy", label: "Strategy", align: "l", render: (r) => <StrategyTag strategy={r.strategy} /> },
    // the daily book decides on daily bars: same convention as the Positions table
    { id: "regime", label: "Regime", align: "l", render: (r) => r.strategy === "TREND_DAILY" ? <span className="text-text-2 font-medium" title="Decided on daily bars — the 15 m regime is not an input of this strategy">daily · n/a</span> : r.regime ? r.regime.replace(/_/g, " ") : "---" },
  ];

  return (
    <div className="flex flex-col flex-1 min-h-0">
      {fundingRows > 0 && (
        <div className="px-3 py-1.5 border-b border-hairline text-[12px] font-medium text-text-2 flex items-center gap-2">
          <span>{fundingRows.toLocaleString()} funding settlement{fundingRows === 1 ? "" : "s"} {showFunding ? "shown" : "hidden"} (hourly on Strike · already in realised PNL)</span>
          <button type="button" className="text-mint hover:underline" onClick={() => setShowFunding((v) => !v)}>{showFunding ? "Hide" : "Show"}</button>
        </div>
      )}
    <DataTable
      columns={columns}
      rows={rows}
      rowKey={(r) => r.key}
      rowClassName={(r) => (symbol && r.symbol === symbol ? "is-open" : undefined)}
      minWidth="1400px"
      emptyText={loading ? "Loading order history…" : "No orders found"}
      emptySub={loading ? undefined : "Every paper fill (entry and exit) is listed here once the engine trades"}
    />
    </div>
  );
}
