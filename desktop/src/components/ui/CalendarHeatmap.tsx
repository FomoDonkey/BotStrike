import { useMemo, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import type { PortfolioDay } from "@/lib/api";
import { cn, formatSignedUSD } from "@/lib/utils";
import { IconButton } from "./Button";

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

/**
 * Month grid with one cell per calendar day; the cell colour is mint / rose with an intensity
 * proportional to |pnl| relative to the month's largest day. Value on hover (title) and inside
 * the cell when it fits. Navigable month by month within the covered range.
 */
export function CalendarHeatmap({ days, className, todayIso }: { days: readonly PortfolioDay[]; className?: string; todayIso?: string }) {
  const byDate = useMemo(() => {
    const m = new Map<string, PortfolioDay>();
    for (const d of days) m.set(d.date, d);
    return m;
  }, [days]);

  const months = useMemo(() => {
    const set = new Set<string>();
    for (const d of days) set.add(d.date.slice(0, 7));
    if (todayIso) set.add(todayIso.slice(0, 7));
    return [...set].sort();
  }, [days, todayIso]);

  const [idx, setIdx] = useState(() => Math.max(0, months.length - 1));
  const monthKey = months[Math.min(idx, months.length - 1)] ?? (todayIso ?? "").slice(0, 7);
  const [y, m] = monthKey ? monthKey.split("-").map(Number) : [NaN, NaN];

  const cells = useMemo(() => {
    if (!Number.isFinite(y) || !Number.isFinite(m)) return { grid: [] as (PortfolioDay | { date: string; empty: true } | null)[], maxAbs: 0 };
    const first = new Date(Date.UTC(y, m - 1, 1));
    const daysInMonth = new Date(Date.UTC(y, m, 0)).getUTCDate();
    const lead = (first.getUTCDay() + 6) % 7; // Monday-first
    const grid: (PortfolioDay | { date: string; empty: true } | null)[] = [];
    let maxAbs = 0;
    for (let i = 0; i < lead; i++) grid.push(null);
    for (let d = 1; d <= daysInMonth; d++) {
      const key = `${y}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
      const rec = byDate.get(key);
      if (rec) {
        if (Math.abs(rec.pnl) > maxAbs) maxAbs = Math.abs(rec.pnl);
        grid.push(rec);
      } else grid.push({ date: key, empty: true });
    }
    while (grid.length % 7 !== 0) grid.push(null);
    return { grid, maxAbs };
  }, [y, m, byDate]);

  if (!monthKey) {
    return <div className={cn("flex-1 flex items-center justify-center text-[13px] font-medium text-text", className)}>No daily data yet</div>;
  }

  return (
    <div className={cn("flex flex-col min-h-0 flex-1 p-3 gap-2", className)}>
      <div className="flex items-center gap-2">
        <IconButton onClick={() => setIdx((i) => Math.max(0, i - 1))} disabled={idx <= 0} aria-label="Previous month"><ChevronLeft className="w-4 h-4" /></IconButton>
        <span className="text-[13px] font-semibold text-text">{MONTHS[m - 1]} {y}</span>
        <IconButton onClick={() => setIdx((i) => Math.min(months.length - 1, i + 1))} disabled={idx >= months.length - 1} aria-label="Next month"><ChevronRight className="w-4 h-4" /></IconButton>
        <span className="ml-auto text-[12px] font-medium text-text-2">Daily PnL · UTC</span>
      </div>
      <div className="grid grid-cols-7 gap-1 text-[11px] font-medium text-text-2">
        {WEEKDAYS.map((w) => <div key={w} className="text-center h-5 leading-5">{w}</div>)}
      </div>
      <div className="grid grid-cols-7 gap-1 flex-1 min-h-0 auto-rows-[minmax(34px,1fr)]">
        {cells.grid.map((c, i) => {
          if (c === null) return <div key={`pad-${i}`} />;
          if ("empty" in c) {
            return (
              <div key={c.date} className="rounded-[6px] bg-panel-2 flex items-start justify-end p-1 min-h-[34px]">
                <span className="text-[11px] font-medium text-text-2">{Number(c.date.slice(8))}</span>
              </div>
            );
          }
          const intensity = cells.maxAbs > 0 ? Math.min(1, Math.abs(c.pnl) / cells.maxAbs) : 0;
          const bg = c.pnl > 0
            ? `rgba(78,250,176,${0.12 + 0.38 * intensity})`
            : c.pnl < 0
              ? `rgba(244,63,94,${0.12 + 0.38 * intensity})`
              : undefined;
          return (
            <div
              key={c.date}
              title={`${c.date} · ${formatSignedUSD(c.pnl)} · ${c.trades} trade${c.trades === 1 ? "" : "s"} · volume $${c.volume.toFixed(0)}`}
              className={cn("rounded-[6px] flex flex-col justify-between p-1 min-h-[34px] min-w-0", !bg && "bg-panel-2", c.date === todayIso && "ring-1 ring-mint")}
              style={bg ? { backgroundColor: bg } : undefined}
            >
              <span className="text-[11px] font-medium text-text self-end">{Number(c.date.slice(8))}</span>
              <span className={cn("num text-[11px] font-semibold truncate", c.pnl === 0 ? "text-text-2" : "text-text")}>{c.trades > 0 || c.pnl !== 0 ? formatSignedUSD(c.pnl) : ""}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
