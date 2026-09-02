import type { PositionData } from "@/lib/api";
import { useNow } from "@/hooks/useNow";
import { Hint } from "@/components/shared/Hint";
import { HINTS } from "@/lib/hints";
import { HoldTime, PnlCell, SideChip, StrategyTag } from "@/components/shared/TradeChips";
import { cn, formatPrice, formatSignedBps, formatSignedPct, formatSize, formatUSD } from "@/lib/utils";
import {
  pnlDistancePct, positionHoldSec, positionLeverage, positionLiquidation, positionMargin, positionNotional, positionRoe,
} from "@/lib/market";

interface PositionsTableProps {
  positions: PositionData[];
  /** Highlight the rows of the symbol shown on the chart */
  symbol?: string;
  /** Fewer columns (Dashboard card) */
  compact?: boolean;
  emptyText?: string;
}

function Level({ level, mark, pct, side }: { level: number | undefined; mark: number; pct?: number | null; side: string }) {
  if (typeof level !== "number" || !(level > 0)) return <span className="text-text-faint">---</span>;
  const d = typeof pct === "number" ? pct : pnlDistancePct(level, mark, side);
  return (
    <span className="num">
      {formatPrice(level)}
      {d !== null && <span className={cn("text-[10.5px] ml-1", d < 0 ? "text-loss" : "text-profit")}>({formatSignedPct(d, 1)})</span>}
    </span>
  );
}

/** Every §2 field of an open position in one dense row (32 px). Horizontal scroll inside the panel. */
export function PositionsTable({ positions, symbol, compact, emptyText = "No open positions" }: PositionsTableProps) {
  const now = useNow();

  if (positions.length === 0) {
    return <div className="flex-1 flex items-center justify-center text-text-faint text-xs py-8">{emptyText}</div>;
  }

  return (
    <div className="overflow-auto flex-1 min-h-0">
      <table className={cn("term-table", compact ? "min-w-[720px]" : "min-w-[1600px]")}>
        <thead>
          <tr>
            <th className="l">Symbol</th>
            <th className="l">Side</th>
            <th>Size</th>
            <th><Hint title={HINTS.notional}>Notional</Hint></th>
            <th>Entry</th>
            <th><Hint title={HINTS.mark}>Mark</Hint></th>
            {!compact && <th><Hint title={HINTS.liq}>Liq. Price</Hint></th>}
            {!compact && <th><Hint title={HINTS.margin}>Margin</Hint></th>}
            <th>Lev</th>
            <th><Hint title={`${HINTS.pnl} ${HINTS.roe}`}>PnL (ROE %)</Hint></th>
            {!compact && <th><Hint title={`${HINTS.sl} Distance in PnL direction: negative = adverse.`}>SL</Hint></th>}
            {!compact && <th><Hint title={`${HINTS.tp} Distance in PnL direction: positive = favourable.`}>TP</Hint></th>}
            {!compact && <th><Hint title={`${HINTS.mae} / ${HINTS.mfe}`}>MAE / MFE</Hint></th>}
            <th><Hint title={HINTS.hold}>Hold</Hint></th>
            <th className="l">Strategy</th>
            {!compact && <th className="l"><Hint title={HINTS.trigger}>Trigger</Hint></th>}
            {!compact && <th className="l"><Hint title="Market regime when the position was opened">Regime</Hint></th>}
            {!compact && <th><Hint title={HINTS.fees}>Fees</Hint></th>}
          </tr>
        </thead>
        <tbody>
          {positions.map((p, i) => {
            const notional = positionNotional(p);
            const lev = positionLeverage(p);
            const margin = positionMargin(p);
            const roe = positionRoe(p);
            const liq = positionLiquidation(p);
            const hold = positionHoldSec(p, now);
            const mark = p.mark_price > 0 ? p.mark_price : p.entry_price;
            const hasMae = typeof p.mae_bps === "number" || typeof p.mfe_bps === "number";
            const fees = (p.fees_paid ?? 0) + (p.funding_paid ?? 0);
            return (
              <tr key={`${p.symbol}-${p.strategy ?? ""}-${p.order_id ?? i}`} className={cn(symbol && p.symbol === symbol && "is-open")}>
                <td className="l font-medium">{p.symbol}</td>
                <td className="l"><SideChip side={p.side} /></td>
                <td className="num">{formatSize(p.size)}</td>
                <td className="num">{formatUSD(notional)}</td>
                <td className="num" title={typeof p.spread_at_entry_bps === "number" ? `Spread at entry ${p.spread_at_entry_bps.toFixed(2)} bps${typeof p.expected_cost_bps === "number" ? ` · expected cost ${p.expected_cost_bps.toFixed(2)} bps` : ""}${p.order_type ? ` · ${p.order_type}` : ""}` : undefined}>{formatPrice(p.entry_price)}</td>
                <td className="num">{mark > 0 ? formatPrice(mark) : "---"}</td>
                {!compact && (
                  <td className="num">
                    {liq.price ? (
                      <span title={liq.estimated ? "Estimated from the paper formula (bridge did not report it)" : undefined} className={cn(liq.estimated && "text-text-secondary")}>
                        {formatPrice(liq.price)}{liq.estimated && <span className="text-text-faint text-[10px] ml-0.5">est</span>}
                      </span>
                    ) : <span className="text-text-faint" title="Leverage 1 — spot-like, no liquidation">---</span>}
                  </td>
                )}
                {!compact && <td className="num">{formatUSD(margin)}</td>}
                <td className="num text-text-secondary">{lev}x</td>
                <td><PnlCell pnl={p.unrealized_pnl ?? 0} roe={roe} inline /></td>
                {!compact && <td><Level level={p.stop_loss} mark={mark} pct={p.sl_distance_pct} side={p.side} /></td>}
                {!compact && <td><Level level={p.take_profit} mark={mark} pct={p.tp_distance_pct} side={p.side} /></td>}
                {!compact && (
                  <td className="num">
                    {hasMae ? (
                      <>
                        <span className="text-loss">{formatSignedBps(p.mae_bps)}</span>
                        <span className="text-text-faint"> / </span>
                        <span className="text-profit">{formatSignedBps(p.mfe_bps)}</span>
                      </>
                    ) : <span className="text-text-faint" title="MAE/MFE need bridge ≥ 2.15">---</span>}
                  </td>
                )}
                <td><HoldTime seconds={hold} /></td>
                <td className="l"><StrategyTag strategy={p.strategy} /></td>
                {!compact && <td className="l text-text-secondary text-[11.5px]">{p.trigger || <span className="text-text-faint">---</span>}</td>}
                {!compact && <td className="l text-text-muted text-[11px]">{p.regime_at_entry || <span className="text-text-faint">---</span>}</td>}
                {!compact && <td className="num text-text-secondary">{typeof p.fees_paid === "number" ? formatUSD(fees) : <span className="text-text-faint">---</span>}</td>}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
