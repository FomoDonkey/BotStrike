import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import { isLong } from "@/lib/market";
import { EXIT_REASON_LABELS, REGIME_COLORS, STRATEGY_COLORS, STRATEGY_LABELS } from "@/lib/constants";

export type ChipTone = "mint" | "rose" | "amber" | "blue" | "neutral" | "outline";

const TONE: Record<ChipTone, string> = {
  mint: "bg-mint-soft text-mint",
  rose: "bg-rose-soft text-rose",
  amber: "bg-amber-soft text-amber",
  blue: "bg-blue-soft text-blue",
  neutral: "bg-panel-2 text-text",
  outline: "border border-hairline-strong text-text bg-transparent",
};

interface ChipProps {
  tone?: ChipTone;
  children: ReactNode;
  className?: string;
  title?: string;
  size?: "xs" | "sm" | "md";
  /** Leading dot in the tone colour */
  dot?: boolean;
  uppercase?: boolean;
}

/** Status / side chip: 6 px radius, 600, 13 px (sm) — colours per spec §1. */
export function Chip({ tone = "neutral", children, className, title, size = "sm", dot, uppercase = true }: ChipProps) {
  return (
    <span
      title={title}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-[6px] font-semibold whitespace-nowrap leading-none",
        size === "xs" ? "h-[18px] px-1.5 text-[10.5px]" : size === "sm" ? "h-[22px] px-2 text-[12px]" : "h-7 px-2.5 text-[13px]",
        uppercase && "uppercase tracking-[0.04em]",
        TONE[tone],
        className,
      )}
    >
      {dot && <span className="w-1.5 h-1.5 rounded-full bg-current shrink-0" />}
      {children}
    </span>
  );
}

/** LONG / SHORT (or BUY / SELL) — mint / rose. */
export function SideChip({ side, className, compact, size = "sm", labels = "position" }: { side: string; className?: string; compact?: boolean; size?: ChipProps["size"]; labels?: "position" | "order" }) {
  const long = isLong(side);
  const label = compact ? (long ? "L" : "S") : labels === "order" ? (long ? "BUY" : "SELL") : long ? "LONG" : "SHORT";
  return (
    <Chip tone={long ? "mint" : "rose"} size={size} className={cn(compact && "w-[22px] px-0 justify-center", className)}>
      {label}
    </Chip>
  );
}

export type StatusKind = "active" | "enabled" | "paper" | "live" | "dry_run" | "killed" | "disabled" | "ok" | "error" | "warning" | "running" | "stopped" | "online" | "offline" | "halted" | "normal";

const STATUS_TONE: Record<StatusKind, ChipTone> = {
  active: "mint", enabled: "mint", running: "mint", ok: "mint", online: "mint", normal: "mint",
  paper: "amber", dry_run: "blue", warning: "amber", enabledAmber: "amber",
  live: "rose", killed: "rose", error: "rose", stopped: "rose", offline: "rose", halted: "rose",
  disabled: "neutral",
} as Record<StatusKind, ChipTone>;

/** ACTIVE (mint) · PAPER (amber) · KILLED (rose) · DISABLED (panel-2, white text) … */
export function StatusChip({ status, label, className, size, title }: { status: StatusKind | string; label?: string; className?: string; size?: ChipProps["size"]; title?: string }) {
  const key = String(status).toLowerCase() as StatusKind;
  const tone = STATUS_TONE[key] ?? "neutral";
  return (
    <Chip tone={tone} size={size} className={className} title={title}>
      {label ?? String(status).replace(/_/g, " ")}
    </Chip>
  );
}

/** Regime chip — RANGING blue · TRENDING_UP mint · TRENDING_DOWN rose · BREAKOUT amber · UNKNOWN white. */
export function RegimeChip({ regime, className, size = "sm", suffix }: { regime: string | undefined | null; className?: string; size?: ChipProps["size"]; suffix?: ReactNode }) {
  const r = regime || "UNKNOWN";
  const tone: ChipTone = r === "RANGING" ? "blue" : r === "TRENDING_UP" ? "mint" : r === "TRENDING_DOWN" ? "rose" : r === "BREAKOUT" ? "amber" : "neutral";
  return (
    <Chip tone={tone} size={size} className={className} title={`Regime ${r}`}>
      <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ backgroundColor: REGIME_COLORS[r] ?? REGIME_COLORS.UNKNOWN }} />
      {r.replace(/_/g, " ")}
      {suffix}
    </Chip>
  );
}

/** Exit reason chip (SL / TP / Signal / Time / Rebal / Trend exit / Close). */
export function ExitReasonChip({ reason, className }: { reason: string | undefined | null; className?: string }) {
  if (!reason) return <span className="text-text-3">---</span>;
  const meta = EXIT_REASON_LABELS[reason] ?? EXIT_REASON_LABELS[reason.toLowerCase()];
  const label = meta?.label ?? reason.replace(/_/g, " ");
  const tone: ChipTone = meta?.tone === "profit" ? "mint" : meta?.tone === "loss" ? "rose" : meta?.tone === "warning" ? "amber" : "neutral";
  return <Chip tone={tone} size="xs" className={className} title={`Exit reason: ${reason}`}>{label}</Chip>;
}

/** Strategy name with its colour dot (white text). */
export function StrategyTag({ strategy, className, dotOnly }: { strategy: string | null | undefined; className?: string; dotOnly?: boolean }) {
  if (!strategy) return <span className="text-text-3">---</span>;
  const color = STRATEGY_COLORS[strategy] ?? "#FFFFFF";
  return (
    <span className={cn("inline-flex items-center gap-1.5 whitespace-nowrap", className)} title={strategy}>
      <span className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: color }} />
      {!dotOnly && <span className="text-text font-medium">{STRATEGY_LABELS[strategy] ?? strategy}</span>}
    </span>
  );
}
