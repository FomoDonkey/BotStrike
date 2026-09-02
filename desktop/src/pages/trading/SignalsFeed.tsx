import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { SignalData, SignalMetadata } from "@/stores/tradingStore";
import { Hint } from "@/components/shared/Hint";
import { HINTS } from "@/lib/hints";
import { SideChip, StrategyTag } from "@/components/shared/TradeChips";
import { cn, formatDateTime, formatPrice, formatTimeShort, formatUSD } from "@/lib/utils";
import { readDivergence } from "@/components/charts/chartOverlays";
import { distancePct } from "@/lib/market";

interface SignalsFeedProps {
  signals: SignalData[];
  /** Only this symbol (chart "Signals" tab) */
  symbol?: string;
  limit?: number;
}

const HIDDEN_KEYS = new Set(["pivots", "trigger", "confirmations", "divergence_type", "type", "rsi_gap", "trigger_level", "trigger_price", "macd", "macd_hist", "macd_state", "action", "exit_reason"]);

function fmtVal(v: unknown): string {
  if (v === null || v === undefined) return "---";
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : Math.abs(v) >= 100 ? v.toFixed(2) : v.toFixed(4);
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (typeof v === "string") return v;
  try { return JSON.stringify(v); } catch { return String(v); }
}

function confirmations(meta: SignalMetadata): string[] {
  const c = meta.confirmations;
  if (!c) return [];
  if (Array.isArray(c)) return c.map(String);
  return Object.entries(c).map(([k, v]) => (v === true ? k : v === false ? `${k} ✗` : `${k} ${fmtVal(v)}`));
}

function KV({ k, v, hint, tone }: { k: string; v: string; hint?: string; tone?: "profit" | "loss" }) {
  return (
    <div className="min-w-0">
      <p className="text-[10px] uppercase tracking-[0.06em] text-text-muted truncate">{hint ? <Hint title={hint}>{k}</Hint> : k}</p>
      <p className={cn("num text-[12px] truncate", tone === "profit" && "text-profit", tone === "loss" && "text-loss", !tone && "text-text-primary")} title={v}>{v}</p>
    </div>
  );
}

/** Full signal metadata (trigger, confirmations, indicators, SL/TP, size; DIVERGENCE pivots). */
export function SignalsFeed({ signals, symbol, limit = 100 }: SignalsFeedProps) {
  const [open, setOpen] = useState<string | null>(null);
  const rows = useMemo(() => {
    const list = symbol ? signals.filter((s) => s.symbol === symbol) : signals;
    return [...list].reverse().slice(0, limit);
  }, [signals, symbol, limit]);

  if (rows.length === 0) {
    return <div className="flex-1 flex items-center justify-center text-text-faint text-xs py-8">No signals yet{symbol ? ` for ${symbol}` : ""}</div>;
  }

  return (
    <div className="overflow-auto flex-1 min-h-0">
      <table className="term-table min-w-[900px]">
        <thead>
          <tr>
            <th className="l w-6" />
            <th className="l">Time</th>
            <th className="l">Strategy</th>
            <th className="l">Side</th>
            <th className="l">Symbol</th>
            <th><Hint title={HINTS.strength}>Strength</Hint></th>
            <th>Entry</th>
            <th><Hint title={HINTS.sl}>SL</Hint></th>
            <th><Hint title={HINTS.tp}>TP</Hint></th>
            <th>Size</th>
            <th className="l"><Hint title={HINTS.trigger}>Trigger</Hint></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((s, i) => {
            const key = `${s.timestamp}-${s.symbol}-${s.strategy}-${i}`;
            const meta = s.metadata ?? {};
            const isExit = (typeof meta.action === "string" && meta.action.startsWith("exit")) || !!meta.exit_reason;
            const expanded = open === key;
            const slD = distancePct(s.stop_loss, s.entry_price);
            const tpD = distancePct(s.take_profit, s.entry_price);
            const div = s.strategy === "DIVERGENCE" ? readDivergence(s.metadata) : null;
            const conf = confirmations(meta);
            const extra = Object.entries(meta).filter(([k, v]) => !HIDDEN_KEYS.has(k) && v !== null && v !== undefined && typeof v !== "object");
            return (
              <FragmentRow key={key}>
                <tr className={cn("cursor-pointer", symbol && !isExit && "")} onClick={() => setOpen(expanded ? null : key)} aria-expanded={expanded}>
                  <td className="l text-text-faint">{expanded ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}</td>
                  <td className="l text-text-secondary" title={formatDateTime(s.timestamp)}>{formatTimeShort(s.timestamp)}</td>
                  <td className="l"><StrategyTag strategy={s.strategy} /></td>
                  <td className="l">
                    <SideChip side={s.side} />
                    {isExit && <span className="ml-1 text-[10px] uppercase tracking-wider text-text-muted">exit{meta.exit_reason ? ` · ${String(meta.exit_reason)}` : ""}</span>}
                  </td>
                  <td className="l font-medium">{s.symbol}</td>
                  <td className="num">
                    <span className="inline-flex items-center gap-1.5">
                      <span className="w-12 h-1 rounded-full bg-white/10 overflow-hidden"><span className="block h-full bg-accent" style={{ width: `${Math.min(100, Math.max(0, s.strength * 100))}%` }} /></span>
                      {(s.strength * 100).toFixed(0)}%
                    </span>
                  </td>
                  <td className="num">{s.entry_price > 0 ? formatPrice(s.entry_price) : "---"}</td>
                  <td className="num">{s.stop_loss > 0 ? <>{formatPrice(s.stop_loss)}{slD !== null && <span className="text-loss text-[10.5px] ml-1">({(slD * 100).toFixed(2)}%)</span>}</> : <span className="text-text-faint">---</span>}</td>
                  <td className="num">{s.take_profit > 0 ? <>{formatPrice(s.take_profit)}{tpD !== null && <span className="text-profit text-[10.5px] ml-1">(+{(tpD * 100).toFixed(2)}%)</span>}</> : <span className="text-text-faint">---</span>}</td>
                  <td className="num">{s.size_usd > 0 ? formatUSD(s.size_usd) : "---"}</td>
                  <td className="l text-text-secondary text-[11.5px] max-w-[220px] truncate" title={meta.trigger ?? undefined}>{meta.trigger ?? <span className="text-text-faint">---</span>}</td>
                </tr>
                {expanded && (
                  <tr className="detail">
                    <td colSpan={11}>
                      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 xl:grid-cols-8 gap-x-4 gap-y-2">
                        <KV k="Trigger" v={meta.trigger ?? "---"} hint={HINTS.trigger} />
                        <KV k="Confirmations" v={conf.length ? conf.join(" · ") : "---"} />
                        {typeof meta.rsi === "number" && <KV k="RSI" v={meta.rsi.toFixed(1)} />}
                        {typeof meta.adx === "number" && <KV k="ADX" v={meta.adx.toFixed(1)} />}
                        {typeof (meta.zscore ?? meta.z_score) === "number" && <KV k="z-score" v={Number(meta.zscore ?? meta.z_score).toFixed(2)} />}
                        {typeof meta.atr_bps === "number" && <KV k="ATR" v={`${meta.atr_bps.toFixed(1)} bps`} />}
                        {typeof meta.atr === "number" && typeof meta.atr_bps !== "number" && <KV k="ATR" v={meta.atr.toFixed(4)} />}
                        {meta.regime && <KV k="Regime" v={String(meta.regime)} />}
                        <KV k="Risk to SL" v={s.stop_loss > 0 && s.entry_price > 0 ? formatUSD(Math.abs(s.entry_price - s.stop_loss) / s.entry_price * s.size_usd) : "---"} hint="size × |entry − SL| / entry" />
                        <KV k="R:R" v={s.stop_loss > 0 && s.take_profit > 0 ? (Math.abs(s.take_profit - s.entry_price) / Math.max(1e-9, Math.abs(s.entry_price - s.stop_loss))).toFixed(2) : "---"} hint="|TP − entry| / |entry − SL|" />
                        {extra.map(([k, v]) => <KV key={k} k={k.replace(/_/g, " ")} v={fmtVal(v)} />)}
                      </div>
                      {div && (
                        <div className="mt-3 pt-2 border-t border-hairline-soft">
                          <p className="text-[10px] uppercase tracking-[0.06em] text-[#F472B6] mb-1.5">Divergence · {div.type} {s.side === "BUY" ? "bullish" : "bearish"}</p>
                          <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 xl:grid-cols-8 gap-x-4 gap-y-2">
                            <KV k="Pivot 1" v={`${formatDateTime(div.pivots[0].ts)} · ${formatPrice(div.pivots[0].price)}`} />
                            <KV k="Pivot 1 RSI" v={div.pivots[0].rsi != null ? div.pivots[0].rsi.toFixed(1) : "---"} />
                            <KV k="Pivot 2" v={`${formatDateTime(div.pivots[1].ts)} · ${formatPrice(div.pivots[1].price)}`} />
                            <KV k="Pivot 2 RSI" v={div.pivots[1].rsi != null ? div.pivots[1].rsi.toFixed(1) : "---"} />
                            <KV k="RSI gap" v={div.rsiGap != null ? div.rsiGap.toFixed(1) : (div.pivots[0].rsi != null && div.pivots[1].rsi != null ? Math.abs(div.pivots[1].rsi - div.pivots[0].rsi).toFixed(1) : "---")} />
                            <KV k="Trigger level" v={div.trigger ? formatPrice(div.trigger) : "---"} hint="Structure break: close beyond the second pivot's candle extreme within the trigger window" />
                            <KV k="MACD" v={macdText(meta)} hint="MACD histogram must confirm the direction on the trigger candle" />
                            <KV k="Bars apart" v={String(Math.round(Math.abs(div.pivots[1].ts - div.pivots[0].ts) / (60 * (meta.timeframe_min ?? 60))))} hint="Separation between pivots in candles (5–60 allowed)" />
                          </div>
                        </div>
                      )}
                    </td>
                  </tr>
                )}
              </FragmentRow>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function macdText(meta: SignalMetadata): string {
  const m = meta.macd;
  if (typeof m === "number") return m.toFixed(4);
  if (typeof m === "string") return m;
  if (m && typeof m === "object") {
    const parts: string[] = [];
    if (typeof m.hist === "number") parts.push(`hist ${m.hist.toFixed(4)}`);
    if (typeof m.state === "string") parts.push(m.state);
    if (parts.length) return parts.join(" · ");
  }
  if (typeof meta.macd_hist === "number") return `hist ${meta.macd_hist.toFixed(4)}${meta.macd_state ? ` · ${meta.macd_state}` : ""}`;
  if (typeof meta.macd_state === "string") return meta.macd_state;
  return "---";
}

// Fragment with a key — kept as a tiny wrapper so the row + detail pair reads as one unit
function FragmentRow({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
