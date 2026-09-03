import { Fragment, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { TradeRecord } from "@/lib/api";
import { Hint } from "@/components/shared/Hint";
import { HINTS } from "@/lib/hints";
import { HoldTime, PnlCell } from "@/components/shared/TradeChips";
import { ExitReasonChip, SideChip, StrategyTag } from "@/components/ui/Chip";
import { DetailCell } from "@/components/ui/DataTable";
import { EmptyState } from "@/components/ui/Panel";
import { cn, formatDateTime, formatPrice, formatSignedBps, formatSize, formatUSD, pnlBps } from "@/lib/utils";
import { tradeHoldSec, tradeNotional, tradePositionSide, tradeRoe } from "@/lib/market";

interface TradeHistoryTableProps {
  trades: TradeRecord[];
  loading?: boolean;
  error?: string | null;
  symbol?: string;
  /** Rows shown (newest first) */
  limit?: number;
}

function rowKey(t: TradeRecord, i: number): string {
  return `${t.symbol}-${t.entry_ts ?? t.entry_time}-${t.exit_ts ?? t.exit_time}-${i}`;
}

/** Closed trades with every §2 field; click a row for the full detail grid. */
export function TradeHistoryTable({ trades, loading, error, symbol, limit = 150 }: TradeHistoryTableProps) {
  const [open, setOpen] = useState<string | null>(null);
  const rows = trades.slice(0, limit);

  if (rows.length === 0) {
    return <EmptyState sub={error ?? (loading ? undefined : "Closed round-trips appear here with PnL, fees, MAE / MFE and exit reason")}>{loading ? "Loading trade history…" : "No closed trades found"}</EmptyState>;
  }

  return (
    <div className="overflow-auto flex-1 min-h-0">
      <table className="term-table" style={{ minWidth: 1240 }}>
        <thead>
          <tr>
            <th className="l w-6" />
            <th className="l">Closed</th>
            <th className="l">Symbol</th>
            <th className="l">Side</th>
            <th>Size</th>
            <th>Entry</th>
            <th>Exit</th>
            <th><Hint title={`Realised PnL net of fees. ${HINTS.roe}`}>PNL (ROE %)</Hint></th>
            <th><Hint title={HINTS.bps}>bps</Hint></th>
            <th>Fee</th>
            <th className="l"><Hint title={HINTS.exitReason}>Exit</Hint></th>
            <th><Hint title="Time between entry and exit fills">Hold</Hint></th>
            <th className="l">Strategy</th>
            <th className="l">Regime</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((t, i) => {
            const key = rowKey(t, i);
            const side = tradePositionSide(t);
            const roe = tradeRoe(t);
            const bps = typeof t.pnl_bps === "number" ? t.pnl_bps : pnlBps(t.pnl || 0, tradeNotional(t));
            const hold = tradeHoldSec(t);
            const expanded = open === key;
            const win = (t.pnl || 0) >= 0;
            return (
              <Fragment key={key}>
                <tr className={cn("cursor-pointer", symbol && t.symbol === symbol && "is-open")} onClick={() => setOpen(expanded ? null : key)} aria-expanded={expanded}>
                  <td className="l">{expanded ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}</td>
                  <td className="l">{formatDateTime(t.exit_ts || t.exit_time)}</td>
                  <td className="l font-semibold">{t.symbol}</td>
                  <td className="l"><SideChip side={side} size="xs" /></td>
                  <td className="num">{formatSize(t.quantity)}</td>
                  <td className="num">{formatPrice(t.entry_price || 0)}</td>
                  <td className="num">{formatPrice(t.exit_price || 0)}</td>
                  <td><PnlCell pnl={t.pnl || 0} roe={roe} inline /></td>
                  <td className={cn("num", bps === null ? "text-text-3" : win ? "text-mint" : "text-rose")}>{formatSignedBps(bps)}</td>
                  <td className="num">{formatUSD(t.fee || 0)}</td>
                  <td className="l"><ExitReasonChip reason={t.exit_reason} /></td>
                  <td><HoldTime seconds={hold} /></td>
                  <td className="l"><StrategyTag strategy={t.strategy} /></td>
                  <td className="l">{t.regime ? t.regime.replace(/_/g, " ") : "---"}</td>
                </tr>
                {expanded && (
                  <tr className="detail">
                    <td colSpan={14}>
                      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 xl:grid-cols-8 gap-x-4 gap-y-2">
                        <DetailCell label="Opened" value={formatDateTime(t.entry_ts || t.entry_time)} />
                        <DetailCell label="Closed" value={formatDateTime(t.exit_ts || t.exit_time)} />
                        <DetailCell label="Notional" value={formatUSD(tradeNotional(t))} hint={HINTS.notional} />
                        <DetailCell label="Leverage" value={`${t.leverage ?? 1}x`} />
                        <DetailCell label="Gross PnL" value={formatUSD((t.pnl || 0) + (t.fee || 0))} tone={(t.pnl || 0) + (t.fee || 0) >= 0 ? "mint" : "rose"} />
                        <DetailCell label="MAE" value={formatSignedBps(t.mae_bps) + (typeof t.mae_bps === "number" ? " bps" : "")} hint={HINTS.mae} tone={typeof t.mae_bps === "number" ? "rose" : undefined} />
                        <DetailCell label="MFE" value={formatSignedBps(t.mfe_bps) + (typeof t.mfe_bps === "number" ? " bps" : "")} hint={HINTS.mfe} tone={typeof t.mfe_bps === "number" ? "mint" : undefined} />
                        <DetailCell label="Slippage" value={formatSignedBps(t.slippage_bps) + (typeof t.slippage_bps === "number" ? " bps" : "")} hint={HINTS.slippage} />
                        <DetailCell label="Order type" value={t.order_type ?? "---"} />
                        <DetailCell label="Trigger" value={t.trigger ?? "---"} hint={HINTS.trigger} />
                        <DetailCell label="Exit reason" value={t.exit_reason ?? "---"} hint={HINTS.exitReason} />
                        <DetailCell label="Equity after" value={typeof t.equity_after === "number" ? formatUSD(t.equity_after) : "---"} hint={HINTS.equityAfter} />
                        <DetailCell label="Spread at entry" value={typeof t.spread_bps === "number" ? `${t.spread_bps.toFixed(2)} bps` : "---"} hint={HINTS.spread} />
                        <DetailCell label="Regime" value={t.regime || "---"} />
                        <DetailCell label="Trade id" value={t.trade_id ?? (t.id ? String(t.id) : "---")} />
                      </div>
                      {t.exit_reason === undefined && t.mae_bps === undefined && (
                        <p className="text-[12px] font-medium text-text-2 mt-2">MAE / MFE / slippage / exit reason / trigger need bridge ≥ 2.15 (contract §2).</p>
                      )}
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
