import type { PositionData } from "@/lib/api";
import { useNow } from "@/hooks/useNow";
import { useMediaQuery } from "@/hooks/useMediaQuery";
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
import { useClosePosition } from "@/hooks/useClosePosition";

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


/** One position as a card: what a phone can actually show. Same numbers as the desktop row. */
function PositionCard({ p, now, highlight }: { p: PositionData; now: number; highlight?: boolean }) {
  const ladder = exitLadderOf(p);
  const funding = p.funding_paid;
  return (
    <div className={cn("rounded-[8px] border border-hairline bg-panel-2 p-3 flex flex-col gap-2",
                       highlight && "border-hairline-strong")}>
      <div className="flex items-center gap-2">
        <span className="font-semibold text-[14px]">{p.symbol}</span>
        <SideChip side={p.side} size="xs" />
        <span className="ml-auto"><PnlCell pnl={p.unrealized_pnl ?? 0} roe={positionRoe(p)} inline /></span>
      </div>
      <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-[12.5px]">
        <div className="flex justify-between"><span className="text-text-2 font-medium">Size</span><span className="num">{formatSize(p.size)}</span></div>
        <div className="flex justify-between"><span className="text-text-2 font-medium">Notional</span><span className="num">{formatUSD(positionNotional(p))}</span></div>
        <div className="flex justify-between"><span className="text-text-2 font-medium">Entry</span><span className="num">{formatPrice(p.entry_price)}</span></div>
        <div className="flex justify-between"><span className="text-text-2 font-medium">Mark</span><span className="num">{p.mark_price > 0 ? formatPrice(p.mark_price) : "---"}</span></div>
        <div className="flex justify-between">
          <span className="text-text-2 font-medium" title={HINTS.fundingPaid}>Funding</span>
          {typeof funding === "number"
            ? <span className={cn("num", funding < 0 ? "text-rose" : funding > 0 ? "text-mint" : "text-text")}>{formatSignedMoney(funding, 4)}</span>
            : <span className="text-text-3">---</span>}
        </div>
        <div className="flex justify-between"><span className="text-text-2 font-medium">Hold</span><HoldTime seconds={positionHoldSec(p, now)} /></div>
      </div>
      <div className="flex items-center gap-2 border-t border-hairline pt-2">
        <span className="text-[12.5px] text-text-2 font-medium" title={HINTS.exitLadder}>Exits</span>
        {ladder
          ? <><span className="num text-[12.5px]"><span className="font-semibold">{ladder.active}/{ladder.total}</span> legs</span>
              <span className="ml-auto"><ExitLadderCell ladder={ladder} entry={p.entry_price} /></span></>
          : <span className="ml-auto num text-[12.5px] text-text-2">SL {p.stop_loss ? formatPrice(p.stop_loss) : "---"} · TP {p.take_profit ? formatPrice(p.take_profit) : "---"}</span>}
      </div>
      <div className="flex items-center gap-2">
        <StrategyTag strategy={p.strategy} />
        <span className="ml-auto"><ClosePositionButton position={p} /></span>
      </div>
    </div>
  );
}

/** Every §2 field of an open position in one dense, sortable row. Scrolls inside the panel. */
export function PositionsTable({ positions, symbol, compact, emptyText = "No open positions found" }: PositionsTableProps) {
  const now = useNow();
  // Below 1024 px a 1980 px table hides everything that matters (measured on the CT at 390 px:
  // only Symbol/Side/Size/Notional were on screen), so positions render as cards instead.
  const narrow = useMediaQuery("(max-width: 1023px)");
  const { canClose } = useClosePosition();

  const columns: Column<PositionData>[] = [
    { id: "symbol", label: "Symbol", align: "l", sortValue: (p) => p.symbol, render: (p) => <span className="font-semibold">{p.symbol}</span> },
    { id: "side", label: "Side", align: "l", render: (p) => <SideChip side={p.side} size="xs" /> },
    { id: "size", label: "Size", sortValue: (p) => p.size, render: (p) => <span className="num">{formatSize(p.size)}</span> },
    { id: "notional", label: "Notional", hint: HINTS.notional, sortValue: positionNotional, render: (p) => <span className="num">{formatUSD(positionNotional(p))}</span> },
    { id: "entry", label: "Entry", sortValue: (p) => p.entry_price, render: (p) => <span className="num" title={typeof p.spread_at_entry_bps === "number" ? `Spread at entry ${p.spread_at_entry_bps.toFixed(2)} bps${typeof p.expected_cost_bps === "number" ? ` · expected cost ${p.expected_cost_bps.toFixed(2)} bps` : ""}${p.order_type ? ` · ${p.order_type}` : ""}` : undefined}>{formatPrice(p.entry_price)}</span> },
    { id: "mark", label: "Mark", hint: HINTS.mark, sortValue: (p) => p.mark_price, render: (p) => <span className="num">{p.mark_price > 0 ? formatPrice(p.mark_price) : "---"}</span> },
  ];
  // Column order = the questions a position must answer, left to right: what it is, where it stands,
  // WHAT IT COSTS, WHEN IT LEAVES, and only then the reference columns. Measured on the CT
  // 2026-09-03: with Funding near the end it sat at x=1852 inside a 1425 px container, i.e. the
  // number existed and nobody could see it.
  columns.push(
    { id: "pnl", label: "PNL (ROE %)", hint: `${HINTS.pnl} ${HINTS.roe}`, sortValue: (p) => p.unrealized_pnl ?? 0, render: (p) => <PnlCell pnl={p.unrealized_pnl ?? 0} roe={positionRoe(p)} inline /> },
    {
      id: "funding", label: "Funding", hint: HINTS.fundingPaid,
      sortValue: (p) => p.funding_paid ?? 0,
      render: (p) => typeof p.funding_paid === "number"
        ? <span className={cn("num", p.funding_paid < 0 ? "text-rose" : p.funding_paid > 0 ? "text-mint" : "text-text")}>{formatSignedMoney(p.funding_paid, 4)}</span>
        : <span className="text-text-3" title="Funding needs bridge ≥ 2.16">---</span>,
    },
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
          if (l) return <ExitLadderCell ladder={l} entry={p.entry_price} />;
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
      { id: "mae", label: "MAE / MFE", hint: `${HINTS.mae} / ${HINTS.mfe}`, render: (p) => (typeof p.mae_bps === "number" || typeof p.mfe_bps === "number") ? <span className="num"><span className="text-rose">{formatSignedBps(p.mae_bps)}</span><span className="text-text-2"> / </span><span className="text-mint">{formatSignedBps(p.mfe_bps)}</span></span> : <span className="text-text-3" title="No excursion data for this position yet">---</span> },
    );
  }
  columns.push(
    { id: "hold", label: "Hold", hint: HINTS.hold, sortValue: (p) => positionHoldSec(p, now) ?? -1, render: (p) => <HoldTime seconds={positionHoldSec(p, now)} /> },
    { id: "strategy", label: "Strategy", align: "l", sortValue: (p) => p.strategy ?? "", render: (p) => <StrategyTag strategy={p.strategy} /> },
  );
  if (!compact) {
    columns.push(
      { id: "liq", label: "Liq. Price", hint: HINTS.liq, render: (p) => { const liq = positionLiquidation(p); return liq.price ? <span className="num" title={liq.estimated ? "Estimated from the paper formula (bridge did not report it)" : undefined}>{formatPrice(liq.price)}{liq.estimated && <span className="text-[11px] ml-0.5 text-text-2">est</span>}</span> : <span className="text-text-2 font-medium" title={p.leverage && p.leverage > 1 ? `Cross margin at ${p.leverage}x: liquidation is account-level and sits far beyond the drawdown halt, so no single price liquidates this position` : "Leverage 1 — the position is fully funded, so no price liquidates it"}>none · {p.leverage ?? 1}x</span>; } },
      { id: "margin", label: "Margin", hint: HINTS.margin, sortValue: positionMargin, render: (p) => <span className="num">{formatUSD(positionMargin(p))}</span> },
      { id: "lev", label: "Lev", sortValue: positionLeverage, render: (p) => <span className="num">{positionLeverage(p)}x</span> },
      { id: "trigger", label: "Trigger", align: "l", hint: HINTS.trigger, render: (p) => p.trigger ? <span className="font-medium">{p.trigger.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase())}</span> : <span className="text-text-3">---</span> },
      // the daily book decides on daily bars: the 15 m regime is not an input, so a dash here read as
      // missing data (Edgar, 2026-09-05)
      { id: "regime", label: "Regime", align: "l", hint: "Market regime when the position was opened (intraday strategies). The daily trend book does not use the 15 m regime.", render: (p) => p.regime_at_entry ? <span className="font-medium">{p.regime_at_entry.replace(/_/g, " ")}</span> : p.strategy === "TREND_DAILY" ? <span className="text-text-2 font-medium" title="Decided on daily bars — the 15 m regime is not an input of this strategy">daily · n/a</span> : <span className="text-text-3">---</span> },
      { id: "fees", label: "Fees", hint: HINTS.fees, sortValue: (p) => p.fees_paid ?? 0, render: (p) => typeof p.fees_paid === "number" ? <span className="num">{formatUSD(p.fees_paid, 4)}</span> : <span className="text-text-3">---</span> },
    );
  }
  columns.push(
    { id: "close", label: "", align: "c", stickyRight: true, className: "w-[72px]", render: (p) => <ClosePositionButton position={p} /> },
  );

  // A row of locked Close buttons with the reason hidden in a tooltip reads as a broken feature:
  // say it once, above the table, with the place that fixes it (2026-09-04).
  const lockNote = !canClose && positions.length > 0 ? (
    <div className="px-3 py-1.5 border-b border-hairline text-[12px] font-medium text-amber shrink-0">
      Manual close is locked: this bridge is remote, so it needs the auth token — Settings → Connection.
    </div>
  ) : null;

  if (narrow) {
    if (!positions.length) {
      return <div className="p-6 text-center text-[12.5px] font-medium text-text-2">{emptyText}</div>;
    }
    return (
      <div className="flex flex-col min-h-0">
        {lockNote}
        <div className="flex flex-col gap-2 p-2">
          {positions.map((p, i) => (
            <PositionCard key={`${p.symbol}-${p.strategy ?? ""}-${p.order_id ?? i}`} p={p} now={now}
                          highlight={Boolean(symbol && p.symbol === symbol)} />
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col min-h-0 flex-1">
      {lockNote}
      <div className="flex-1 min-h-0">
        <DataTable
          columns={columns}
          rows={positions}
          rowKey={(p, i) => `${p.symbol}-${p.strategy ?? ""}-${p.order_id ?? i}`}
          rowClassName={(p) => (symbol && p.symbol === symbol ? "is-open" : undefined)}
          minWidth={compact ? "900px" : "1980px"}
          emptyText={emptyText}
        />
      </div>
    </div>
  );
}
