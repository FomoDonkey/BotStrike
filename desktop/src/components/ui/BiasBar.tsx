import { cn, formatUSD } from "@/lib/utils";

/** Long / Short direction bias bar: `100 % · Long $268 · $0 Short · 0 %`. */
export function BiasBar({ longNotional, shortNotional, className }: { longNotional: number; shortNotional: number; className?: string }) {
  const l = Math.max(0, Number.isFinite(longNotional) ? longNotional : 0);
  const s = Math.max(0, Number.isFinite(shortNotional) ? shortNotional : 0);
  const total = l + s;
  const longPct = total > 0 ? (l / total) * 100 : 0;
  const shortPct = total > 0 ? 100 - longPct : 0;
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <div className="flex h-2 rounded-full overflow-hidden bg-white/10">
        {total > 0 && <div className="h-full bg-mint" style={{ width: `${longPct}%` }} />}
        {total > 0 && <div className="h-full bg-rose" style={{ width: `${shortPct}%` }} />}
      </div>
      <div className="flex items-center justify-between text-[12px] font-medium num">
        <span className="text-text"><span className="text-mint">{longPct.toFixed(0)}%</span> · Long {formatUSD(l, 0)}</span>
        <span className="text-text">{formatUSD(s, 0)} Short · <span className="text-rose">{shortPct.toFixed(0)}%</span></span>
      </div>
    </div>
  );
}
