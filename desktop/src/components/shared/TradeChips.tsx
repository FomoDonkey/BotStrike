import { cn, formatClock, formatSignedPct, formatSignedUSD } from "@/lib/utils";
import { EXIT_REASON_LABELS, STRATEGY_COLORS, STRATEGY_LABELS } from "@/lib/constants";
import { isLong } from "@/lib/market";

/** LONG / SHORT chip — green/rose is reserved for direction and sign. */
export function SideChip({ side, className, compact }: { side: string; className?: string; compact?: boolean }) {
  const long = isLong(side);
  const label = compact ? (long ? "L" : "S") : long ? "LONG" : "SHORT";
  return (
    <span
      className={cn(
        "inline-flex items-center justify-center rounded px-1.5 h-5 text-[10px] font-bold tracking-wider",
        long ? "bg-profit/10 text-profit" : "bg-loss/10 text-loss",
        compact && "w-5 px-0",
        className,
      )}
    >
      {label}
    </span>
  );
}

/** Exit reason chip (SL / TP / Signal / Time / Rebal / Trend exit / Close). Unknown reasons render as-is. */
export function ExitReasonChip({ reason, className }: { reason: string | undefined | null; className?: string }) {
  if (!reason) return <span className="text-text-faint">---</span>;
  const meta = EXIT_REASON_LABELS[reason] ?? EXIT_REASON_LABELS[reason.toLowerCase()];
  const label = meta?.label ?? reason.replace(/_/g, " ");
  const tone = meta?.tone ?? "neutral";
  return (
    <span
      title={`Exit reason: ${reason}`}
      className={cn(
        "inline-flex items-center rounded px-1.5 h-5 text-[10px] font-semibold uppercase tracking-wider whitespace-nowrap",
        tone === "profit" && "bg-profit/10 text-profit",
        tone === "loss" && "bg-loss/10 text-loss",
        tone === "warning" && "bg-warning/10 text-warning",
        tone === "neutral" && "bg-white/[0.06] text-text-secondary",
        className,
      )}
    >
      {label}
    </span>
  );
}

/** Strategy name in its colour (dot + label). */
export function StrategyTag({ strategy, className, dotOnly }: { strategy: string | null | undefined; className?: string; dotOnly?: boolean }) {
  if (!strategy) return <span className="text-text-faint">---</span>;
  const color = STRATEGY_COLORS[strategy] ?? "#6B7280";
  return (
    <span className={cn("inline-flex items-center gap-1.5 whitespace-nowrap", className)} title={strategy}>
      <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ backgroundColor: color }} />
      {!dotOnly && <span className="text-text-secondary text-[11.5px]">{STRATEGY_LABELS[strategy] ?? strategy}</span>}
    </span>
  );
}

/** Signed money coloured by sign; optional ROE beneath / beside. */
export function PnlCell({ pnl, roe, inline, className }: { pnl: number; roe?: number | null; inline?: boolean; className?: string }) {
  const tone = pnl > 0 ? "text-profit" : pnl < 0 ? "text-loss" : "text-text-secondary";
  return (
    <span className={cn("num inline-flex", inline ? "items-baseline gap-1.5" : "flex-col items-end leading-tight", tone, className)}>
      <span>{formatSignedUSD(pnl)}</span>
      {typeof roe === "number" && Number.isFinite(roe) && (
        <span className={cn("text-[10.5px]", inline ? "" : "opacity-80")}>({formatSignedPct(roe)})</span>
      )}
    </span>
  );
}

/** Percentage coloured by sign (e.g. ROE, 24h change). */
export function SignedPct({ value, decimals = 2, className }: { value: number | null | undefined; decimals?: number; className?: string }) {
  if (typeof value !== "number" || !Number.isFinite(value)) return <span className={cn("text-text-faint", className)}>---</span>;
  return (
    <span className={cn("num", value > 0 ? "text-profit" : value < 0 ? "text-loss" : "text-text-secondary", className)}>
      {formatSignedPct(value, decimals)}
    </span>
  );
}

/** Hold time "hh:mm:ss" (pass seconds); "---" when unknown. */
export function HoldTime({ seconds, className }: { seconds: number | null | undefined; className?: string }) {
  if (typeof seconds !== "number" || !Number.isFinite(seconds)) return <span className={cn("text-text-faint", className)}>---</span>;
  return <span className={cn("num text-text-secondary", className)}>{formatClock(seconds)}</span>;
}
