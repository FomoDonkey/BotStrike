import type { WinDay } from "@/lib/api";
import { cn, formatSignedUSD } from "@/lib/utils";

/** Strike's 18 win/loss-day dots: mint = win, rose = loss, grey = flat / no trades. Oldest first. */
export function WinDayDots({ days, count = 18, className }: { days: readonly WinDay[] | null | undefined; count?: number; className?: string }) {
  const list: (WinDay | null)[] = [];
  const src = days ?? [];
  const start = Math.max(0, src.length - count);
  for (let i = start; i < src.length; i++) list.push(src[i]);
  while (list.length < count) list.unshift(null);
  return (
    <div className={cn("flex items-center gap-[5px] flex-wrap", className)} aria-label="Win / loss days">
      {list.map((d, i) => (
        <span
          key={d ? d.date : `empty-${i}`}
          title={d ? `${d.date} · ${formatSignedUSD(d.pnl)} · ${d.trades} trade${d.trades === 1 ? "" : "s"}` : "no data"}
          className={cn(
            "w-2.5 h-2.5 rounded-full shrink-0",
            d?.result === "win" ? "bg-mint" : d?.result === "loss" ? "bg-rose" : "bg-white/25",
          )}
        />
      ))}
    </div>
  );
}
