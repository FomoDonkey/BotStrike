import type { ActivityEvent } from "@/lib/api";
import { useActivity } from "@/hooks/useActivity";
import { cn, formatDateTime, formatSignedPct, formatSignedUSD } from "@/lib/utils";
import { Chip, SideChip } from "./Chip";
import { EmptyState } from "./Panel";

interface ActivityFeedProps {
  limit?: number;
  /** Only this symbol */
  symbol?: string;
  /** Only these kinds */
  kinds?: readonly string[];
  className?: string;
  compact?: boolean;
}

function kindTone(e: ActivityEvent): "mint" | "rose" | "amber" | "blue" | "neutral" {
  if (e.kind === "kill" || e.level === "error") return "rose";
  if (e.kind === "risk" || e.level === "warning") return "amber";
  if (e.kind === "regime") return "blue";
  if (e.kind === "run" || e.kind === "system" || e.kind === "config") return "neutral";
  return "neutral";
}

function kindLabel(kind: string): string {
  switch (kind) {
    case "fill": return "Fill";
    case "run": return "Run";
    case "regime": return "Regime";
    case "risk": return "Risk";
    case "kill": return "Kill";
    case "system": return "System";
    case "config": return "Config";
    case "signal": return "Signal";
    default: return kind;
  }
}

/** One activity row exactly like Strike's Recent activity: chip · title · time · detail · PnL line. */
export function ActivityRow({ e, compact }: { e: ActivityEvent; compact?: boolean }) {
  const danger = e.kind === "kill" || e.level === "error";
  const warn = e.kind === "risk" || e.level === "warning";
  return (
    <div className={cn("flex gap-2.5 px-3 border-b border-hairline-soft last:border-b-0", compact ? "py-1.5" : "py-2")}>
      <div className="pt-0.5 shrink-0">
        {e.kind === "fill" && e.side ? <SideChip side={e.side} size="xs" /> : <Chip tone={kindTone(e)} size="xs">{kindLabel(e.kind)}</Chip>}
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-baseline gap-2 min-w-0">
          <span className={cn("text-[12.5px] font-semibold truncate", danger ? "text-rose" : warn ? "text-amber" : "text-text")}>{e.title}</span>
          <span className="ml-auto num text-[11.5px] font-medium text-text-3 whitespace-nowrap">{formatDateTime(e.ts)}</span>
        </div>
        {e.detail && <p className="text-[12px] font-medium text-text-2 break-words leading-snug">{e.detail}</p>}
        {typeof e.pnl === "number" && Number.isFinite(e.pnl) && (
          <p className={cn("num text-[12px] font-semibold", e.pnl > 0 ? "text-mint" : e.pnl < 0 ? "text-rose" : "text-text")}>
            {formatSignedUSD(e.pnl)}
            {typeof e.roe_pct === "number" && Number.isFinite(e.roe_pct) && <span> ({formatSignedPct(e.roe_pct)})</span>}
          </p>
        )}
      </div>
    </div>
  );
}

/** `/api/activity` list, newest first; empty and "not on this bridge" states in white. */
export function ActivityFeed({ limit = 100, symbol, kinds, className, compact }: ActivityFeedProps) {
  const { events, loaded, missing, error } = useActivity(limit);
  const rows = events.filter((e) => (!symbol || !e.symbol || e.symbol === symbol) && (!kinds || kinds.includes(e.kind)));

  if (missing) return <EmptyState sub="GET /api/activity needs bridge ≥ 2.16">Activity feed not available on this bridge</EmptyState>;
  if (error && rows.length === 0) return <EmptyState sub={error}>Activity unavailable</EmptyState>;
  if (!loaded) return <EmptyState>Loading activity…</EmptyState>;
  if (rows.length === 0) return <EmptyState>No activity yet</EmptyState>;

  return (
    <div className={cn("flex-1 min-h-0 overflow-y-auto", className)}>
      {rows.map((e, i) => <ActivityRow key={`${e.ts}-${e.kind}-${i}`} e={e} compact={compact} />)}
    </div>
  );
}
