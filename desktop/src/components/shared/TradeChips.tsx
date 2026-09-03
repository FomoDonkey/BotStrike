import { cn, formatClock, formatSignedPct, formatSignedUSD } from "@/lib/utils";

// Side / exit / strategy chips moved to components/ui/Chip — re-exported so imports keep working.
export { SideChip, ExitReasonChip, StrategyTag } from "@/components/ui/Chip";

/** Signed money coloured by sign; optional ROE beside / beneath. Zero is white. */
export function PnlCell({ pnl, roe, inline, className }: { pnl: number; roe?: number | null; inline?: boolean; className?: string }) {
  const tone = pnl > 0 ? "text-mint" : pnl < 0 ? "text-rose" : "text-text";
  return (
    <span className={cn("num inline-flex font-semibold", inline ? "items-baseline gap-1.5" : "flex-col items-end leading-tight", tone, className)}>
      <span>{formatSignedUSD(pnl)}</span>
      {typeof roe === "number" && Number.isFinite(roe) && <span className="text-[11.5px] font-medium">({formatSignedPct(roe)})</span>}
    </span>
  );
}

/** Percentage coloured by sign (ROE, 24h change). */
export function SignedPct({ value, decimals = 2, className }: { value: number | null | undefined; decimals?: number; className?: string }) {
  if (typeof value !== "number" || !Number.isFinite(value)) return <span className={cn("text-text-3", className)}>---</span>;
  return (
    <span className={cn("num font-semibold", value > 0 ? "text-mint" : value < 0 ? "text-rose" : "text-text", className)}>
      {formatSignedPct(value, decimals)}
    </span>
  );
}

/** Hold time "hh:mm:ss" (seconds); "---" when unknown. */
export function HoldTime({ seconds, className }: { seconds: number | null | undefined; className?: string }) {
  if (typeof seconds !== "number" || !Number.isFinite(seconds)) return <span className={cn("text-text-3", className)}>---</span>;
  return <span className={cn("num text-text", className)}>{formatClock(seconds)}</span>;
}
