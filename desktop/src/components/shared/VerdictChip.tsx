import { cn } from "@/lib/utils";

// Edge-monitor verdict → colour: ok = profit, warn = warning, kill = loss, insufficient = muted
const STYLES: Record<string, string> = {
  ok: "bg-profit/10 text-profit",
  warn: "bg-warning/10 text-warning",
  kill: "bg-loss/10 text-loss",
  insufficient: "bg-white/5 text-text-muted",
};

export function VerdictChip({ verdict, className, title }: { verdict?: string | null; className?: string; title?: string }) {
  const v = verdict || "insufficient";
  return (
    <span
      title={title}
      className={cn(
        "inline-flex items-center px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider whitespace-nowrap",
        STYLES[v] ?? STYLES.insufficient,
        className,
      )}
    >
      {v}
    </span>
  );
}
