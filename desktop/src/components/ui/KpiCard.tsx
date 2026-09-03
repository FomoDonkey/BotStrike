import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import { Hint } from "@/components/shared/Hint";
import { Panel } from "./Panel";

interface KpiCardProps {
  label: ReactNode;
  hint?: string;
  /** Hero value (24–32 px, 700) */
  value: ReactNode;
  /** Text right after the value (e.g. "PNL") */
  unit?: ReactNode;
  sub?: ReactNode;
  children?: ReactNode;
  className?: string;
  tone?: "mint" | "rose" | "text";
}

/** Strike's Portfolio KPI card: label · hero number · helper line · optional bar / dots. */
export function KpiCard({ label, hint, value, unit, sub, children, className, tone = "text" }: KpiCardProps) {
  return (
    <Panel className={cn("px-4 py-3 flex flex-col gap-1.5 min-w-0", className)}>
      <div className="text-[12.5px] font-medium text-text-2 truncate">{hint ? <Hint title={hint}>{label}</Hint> : label}</div>
      <div className="flex items-baseline gap-1.5 min-w-0">
        <span className={cn("num text-[24px] leading-none font-bold truncate", tone === "mint" ? "text-mint" : tone === "rose" ? "text-rose" : "text-text")}>{value}</span>
        {unit && <span className="text-[12.5px] font-medium text-text-2">{unit}</span>}
      </div>
      {children}
      {sub && <div className="text-[12px] font-medium text-text-2 leading-snug">{sub}</div>}
    </Panel>
  );
}

/** Horizontal usage bar (leverage / margin usage). */
export function ProgressBar({ ratio, tone = "mint", className, height = "h-1.5" }: { ratio: number; tone?: "mint" | "rose" | "amber" | "blue"; className?: string; height?: string }) {
  const w = Math.max(0, Math.min(1, Number.isFinite(ratio) ? ratio : 0)) * 100;
  const bg = tone === "rose" ? "bg-rose" : tone === "amber" ? "bg-amber" : tone === "blue" ? "bg-blue" : "bg-mint";
  return (
    <div className={cn("w-full rounded-full bg-white/10 overflow-hidden", height, className)}>
      <div className={cn("h-full rounded-full transition-[width] duration-500", bg)} style={{ width: `${w}%` }} />
    </div>
  );
}
