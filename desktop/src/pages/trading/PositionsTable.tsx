import type { PositionData } from "@/lib/api";
import { useNow } from "@/hooks/useNow";
import { HINTS } from "@/lib/hints";
import { HoldTime, PnlCell } from "@/components/shared/TradeChips";
import { SideChip, StrategyTag } from "@/components/ui/Chip";
import { DataTable, type Column } from "@/components/ui/DataTable";
import { ExitLadderCell } from "@/components/ui/ExitLadder";
import { cn, formatPrice, formatSignedBps, formatSignedMoney, formatSignedPct, formatSize, formatUSD } from "@/lib/utils";
import {
  exitLadderOf, pnlDistancePct, positionHoldSec, positionLeverage, positionLiquidation, positionMargin, positionNotional, positionRoe,
} from "@/lib/market";
import { ClosePositionButton } from "./ClosePosition";

interface PositionsTableProps {
  positions: PositionData[];
  /** Highlight the rows of the symbol shown on the chart */
  symbol?: string;
  /** Fewer columns (Portfolio) */
  compact?: boolean;
  emptyText?: string;
}

function Level({ level, mark, pct, side }: { level: number | undefined; mark: number; pct?: number | null; side: string }) {
  if (typeof level !== "number" || !(level > 0)) return <span className="text-text-3">---</span>;
  const d = typeof pct === "number" ? pct : pnlDistancePct(level, mark, side);
  return (
    <span className="num">
      {formatPrice(level)}
      {d !== null && <span className={cn("text-[11.5px] ml-1", d < 0 ? "text-rose" : "text-mint")}>({formatSignedPct(d, 1)})</span>}
    </span>
  );
}

/** Every §2 field of an open position in one dense, sortable row. Scrolls inside the panel. */
export function PositionsTable({ positions, symbol, compact, emptyText = "No open positions found" }: PositionsTableProps) {
  const now = useNow();

  const columns: Column<PositionData>[] = [
    { id: "symbol", label: "Symbol", align: "l", sortValue: (p) => p.symbol, render: (p) => <span className="font-semibold">{p.symbol}</span> },
    { id: "side", label: "Side", align: "l", render: (p) => <SideChip side={p.side} size="xs" /> },
    { id: "size", label: "Size", sortValue: (p) => p.size, render: (p) => <span className="num">{formatSize(p.size)}</span> },
    { id: "notional", label: "Notional", hint: HINTS.notional, sortValue: positionNotional, render: (p) => <span className="num">{formatUSD(positionNotional(p))}</span> },
    { id: "entry", label: "Entry", sortValue: (p) => p.entry_price, render: (p) => <span className="num" title={typeof p.spread_at_entry_bps === "number" ? `Spread at entry ${p.spread_at_entry_bps.toFixed(2)} bps${typeof p.expected_cost_bps === "number" ? ` · expected cost ${p.expected_cost_bps.toFixed(2)} bps` : ""}${p.order_type ? ` · ${p.order_type}` : ""}` : undefined}>{formatPrice(p.entry_price)}</span> },
    { id: "mark", label: "Mark", hint: HINTS.mark, sortValue: (p) => p.mark_price, render: (p) => <span className="num">{p.mark_price > 0 ? formatPrice(p.mark_price) : "---"}</span> },
  ];
  if (!compact) {
    columns.push(
      { id: "liq", label: "Liq. Price", hint: HINTS.liq, render: (p) => { const liq = positionLiquidation(p); return liq.price ? <span className="num" title={liq.estimated ? "Estimated from the paper formula (bridge did not report it)" : undefined}>{formatPrice(liq.price)}{liq.estimated && <span className="text-[11px] ml-0.5 text-text-2">est</span>}</span> : <span className="text-text-3" title="Leverage 1 — spot-like, no liquidation">---</span>; } },
      { id: "margin", label: "Margin", hint: HINTS.margin, sortValue: positionMargin, render: (p) => <span className="num">{formatUSD(positionMargin(p))}</span> },
    );
  }
  columns.push(
    { id: "lev", label: "Lev", sortValue: positionLeverage, render: (p) => <span className="num">{positionLeverage(p)}x</span> },
    { id: "pnl", label: "PNL (ROE %)", hint: `${HINTS.pnl} ${HINTS.roe}`, sortValue: (p) => p.unrealized_pnl ?? 0, render: (p) => <PnlCell pnl={p.unrealized_pnl ?? 0} roe={positionRoe(p)} inline /> },
  );
  if (!compact) {
    columns.push(
      {
        id: "exits", label: "Exits", hint: HINTS.exitLegs,
        sortValue: (p) => exitLadderOf(p)?.active ?? -1,
        render: (p) => {
          const l = exitLadderOf(p);
          if (!l) return <span className="text-text-3" title="Intraday strategy — one stop-loss and one take-profit, in the next two columns">---</span>;
          return <span className="num" title={HINTS.exitLadder}><span className="font-semibold">{l.active}/{l.total}</span><span className="text-text-2 font-medium"> legs</span></span>;
        },
      },
      {
        id: "sl", label: "SL / Exit ladder", hint: `${HINTS.sl} ${HINTS.exitLadder}`,
        render: (p) => {
          const l = exitLadderOf(p);
          if (l) return <ExitLadderCell ladder={l} />;
          return <Level level={p.stop_loss} mark={p.mark_price > 0 ? p.mark_price : p.entry_price} pct={p.sl_distance_pct} side={p.side} />;
        },
      },
      {
        id: "tp", label: "TP", hint: `${HINTS.tp} Distance in PnL direction: positive = favourable.`,
        render: (p) => {
          if (exitLadderOf(p)) return <span className="text-text-2 font-medium" title={HINTS.exitLadder}>none · by design</span>;
          return <Level level={p.take_profit} mark={p.mark_price > 0 ? p.mark_price : p.entry_price} pct={p.tp_distance_pct} side={p.side} />;
        },
      },
      { id: "mae", label: "MAE / MFE", hint: `${HINTS.mae} / ${HINTS.mfe}`, render: (p) => (typeof p.mae_bps === "number" || typeof p.mfe_bps === "number") ? <span className="num"><span className="text-rose">{formatSignedBps(p.mae_bps)}</span><span className="text-text-2"> / </span><span className="text-mint">{formatSignedBps(p.mfe_bps)}</span></span> : <span className="text-text-3" title="MAE/MFE need bridge ≥ 2.15">---</span> },
    );
  }
  columns.push(
    { id: "hold", label: "Hold", hint: HINTS.hold, sortValue: (p) => positionHoldSec(p, now) ?? -1, render: (p) => <HoldTime seconds={positionHoldSec(p, now)} /> },
    { id: "strategy", label: "Strategy", align: "l", sortValue: (p) => p.strategy ?? "", render: (p) => <StrategyTag strategy={p.strategy} /> },
  );
  if (!compact) {
    columns.push(
      { id: "trigger", label: "Trigger", align: "l", hint: HINTS.trigger, render: (p) => p.trigger ? <span className="font-medium">{p.trigger}</span> : <span className="text-text-3">---</span> },
      { id: "regime", label: "Regime", align: "l", hint: "Market regime when the position was opened", render: (p) => p.regime_at_entry ? <span className="font-medium">{p.regime_at_entry.replace(/_/g, " ")}</span> : <span className="text-text-3">---</span> },
      { id: "fees", label: "Fees", hint: HINTS.fees, sortValue: (p) => p.fees_paid ?? 0, render: (p) => typeof p.fees_paid === "number" ? <span className="num">{formatUSD(p.fees_paid, 4)}</span> : <span className="text-text-3">---</span> },
    );
  }
  columns.push(
    {
      id: "funding", label: "Funding", hint: HINTS.fundingPaid,
      sortValue: (p) => p.funding_paid ?? 0,
      render: (p) => typeof p.funding_paid === "number"
        ? <span className={cn("num", p.funding_paid < 0 ? "text-rose" : p.funding_paid > 0 ? "text-mint" : "text-text")}>{formatSignedMoney(p.funding_paid, 4)}</span>
        : <span className="text-text-3" title="Funding needs bridge ≥ 2.16">---</span>,
    },
    { id: "close", label: "", align: "c", stickyRight: true, className: "w-[72px]", render: (p) => <ClosePositionButton position={p} /> },
  );

  return (
    <DataTable
      columns={columns}
      rows={positions}
      rowKey={(p, i) => `${p.symbol}-${p.strategy ?? ""}-${p.order_id ?? i}`}
      rowClassName={(p) => (symbol && p.symbol === symbol ? "is-open" : undefined)}
      minWidth={compact ? "900px" : "1980px"}
      emptyText={emptyText}
    />
  );
}
