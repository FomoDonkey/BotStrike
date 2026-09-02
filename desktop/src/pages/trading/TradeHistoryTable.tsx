import { Fragment, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { TradeRecord } from "@/lib/api";
import { Hint } from "@/components/shared/Hint";
import { HINTS } from "@/lib/hints";
import { ExitReasonChip, HoldTime, PnlCell, SideChip, StrategyTag } from "@/components/shared/TradeChips";
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

function Detail({ label, value, hint, tone }: { label: string; value: string; hint?: string; tone?: "profit" | "loss" }) {
  return (
    <div className="min-w-0">
      <p className="text-[10px] uppercase tracking-[0.06em] text-text-muted truncate">{hint ? <Hint title={hint}>{label}</Hint> : label}</p>
      <p className={cn("num text-[12px] truncate", tone === "profit" && "text-profit", tone === "loss" && "text-loss", !tone && "text-text-primary")}>{value}</p>
    </div>
  );
}

/** Closed trades with every §2 field; click a row for the full detail grid. */
export function TradeHistoryTable({ trades, loading, error, symbol, limit = 150 }: TradeHistoryTableProps) {
  const [open, setOpen] = useState<string | null>(null);
  const rows = trades.slice(0, limit);

  if (rows.length === 0) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center text-text-faint text-xs py-8 gap-1">
        <span>{loading ? "Loading trade history…" : "No closed trades recorded"}</span>
        {error && <span className="text-[10.5px] text-loss font-mono">{error}</span>}
      </div>
    );
  }

  return (
    <div className="overflow-auto flex-1 min-h-0">
      <table className="term-table min-w-[1180px]">
        <thead>
          <tr>
            <th className="l w-6" />
            <th className="l">Closed</th>
            <th className="l">Symbol</th>
            <th className="l">Side</th>
            <th>Size</th>
            <th>Entry</th>
            <th>Exit</th>
            <th><Hint title={`Realised PnL net of fees. ${HINTS.roe}`}>PnL (ROE %)</Hint></th>
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
                <tr
                  className={cn("cursor-pointer", symbol && t.symbol === symbol && "is-open")}
                  onClick={() => setOpen(expanded ? null : key)}
                  aria-expanded={expanded}
                >
                  <td className="l text-text-faint">{expanded ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}</td>
                  <td className="l text-text-secondary">{formatDateTime(t.exit_ts || t.exit_time)}</td>
                  <td className="l font-medium">{t.symbol}</td>
                  <td className="l"><SideChip side={side} /></td>
                  <td className="num">{formatSize(t.quantity)}</td>
                  <td className="num">{formatPrice(t.entry_price || 0)}</td>
                  <td className="num">{formatPrice(t.exit_price || 0)}</td>
                  <td><PnlCell pnl={t.pnl || 0} roe={roe} inline /></td>
                  <td className={cn("num", bps === null ? "text-text-faint" : win ? "text-profit" : "text-loss")}>{formatSignedBps(bps)}</td>
                  <td className="num text-text-secondary">{formatUSD(t.fee || 0)}</td>
                  <td className="l"><ExitReasonChip reason={t.exit_reason} /></td>
                  <td><HoldTime seconds={hold} /></td>
                  <td className="l"><StrategyTag strategy={t.strategy} /></td>
                  <td className="l text-text-muted text-[11px]">{t.regime || "---"}</td>
                </tr>
                {expanded && (
                  <tr className="detail">
                    <td colSpan={14}>
                      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 xl:grid-cols-8 gap-x-4 gap-y-2">
                        <Detail label="Opened" value={formatDateTime(t.entry_ts || t.entry_time)} />
                        <Detail label="Closed" value={formatDateTime(t.exit_ts || t.exit_time)} />
                        <Detail label="Notional" value={formatUSD(tradeNotional(t))} hint={HINTS.notional} />
                        <Detail label="Leverage" value={`${t.leverage ?? 1}x`} />
                        <Detail label="Gross PnL" value={formatUSD((t.pnl || 0) + (t.fee || 0))} tone={(t.pnl || 0) + (t.fee || 0) >= 0 ? "profit" : "loss"} />
                        <Detail label="MAE" value={formatSignedBps(t.mae_bps) + (typeof t.mae_bps === "number" ? " bps" : "")} hint={HINTS.mae} tone={typeof t.mae_bps === "number" ? "loss" : undefined} />
                        <Detail label="MFE" value={formatSignedBps(t.mfe_bps) + (typeof t.mfe_bps === "number" ? " bps" : "")} hint={HINTS.mfe} tone={typeof t.mfe_bps === "number" ? "profit" : undefined} />
                        <Detail label="Slippage" value={formatSignedBps(t.slippage_bps) + (typeof t.slippage_bps === "number" ? " bps" : "")} hint={HINTS.slippage} />
                        <Detail label="Order type" value={t.order_type ?? "---"} />
                        <Detail label="Trigger" value={t.trigger ?? "---"} hint={HINTS.trigger} />
                        <Detail label="Exit reason" value={t.exit_reason ?? "---"} hint={HINTS.exitReason} />
                        <Detail label="Equity after" value={typeof t.equity_after === "number" ? formatUSD(t.equity_after) : "---"} hint={HINTS.equityAfter} />
                        <Detail label="Regime" value={t.regime || "---"} />
                        <Detail label="Trade id" value={t.id ? String(t.id) : "---"} />
                      </div>
                      {t.exit_reason === undefined && t.mae_bps === undefined && (
                        <p className="text-[10.5px] text-text-faint mt-2">MAE / MFE / slippage / exit reason / trigger need bridge ≥ 2.15 (contract §2).</p>
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
