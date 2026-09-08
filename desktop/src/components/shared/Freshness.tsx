import { useNow } from "@/hooks/useNow";
import { cn, formatAge } from "@/lib/utils";

/**
 * "updated 4 s ago" — or, in rose, "refresh failed 2 min ago: …" — for a polled panel.
 *
 * A poll that fails keeps the last payload on screen (useEndpoint), which is right, but until
 * 2026-09-08 nothing said so: a panel could sit on numbers minutes old and look current
 * ("hay métricas que no se actualizan solas", Edgar). Every polled panel carries this line now.
 */
export function Freshness({ at, error, every, className }: { at: number; error?: string | null; every?: number; className?: string }) {
  const now = useNow();
  const age = at > 0 ? (now - at) / 1000 : null;
  const stale = age !== null && every !== undefined && age > every / 1000 * 3 + 5;
  if (error) {
    return (
      <span className={cn("text-[11px] font-medium text-rose whitespace-nowrap", className)} title={error}>
        refresh failed{age !== null ? ` · last good ${formatAge(age)} ago` : ""}
      </span>
    );
  }
  if (age === null) return <span className={cn("text-[11px] font-medium text-text-3 whitespace-nowrap", className)}>waiting for data…</span>;
  return (
    <span className={cn("text-[11px] font-medium whitespace-nowrap", stale ? "text-amber" : "text-text-3", className)}
          title={every ? `Polled every ${Math.round(every / 1000)} s while the tab is visible` : undefined}>
      updated {formatAge(age)} ago{stale ? " · stale" : ""}
    </span>
  );
}
