import type { HTMLAttributes, ReactNode } from "react";
import { cn } from "@/lib/utils";

interface PanelProps extends HTMLAttributes<HTMLDivElement> {
  /** Mint hairline (active / alert state) */
  accent?: boolean;
  /** Rose hairline (halted / error) */
  danger?: boolean;
  noBorder?: boolean;
}

/** Plain panel: solid `--panel` surface, 1 px hairline, 8 px radius. No blur, no shadow, no gradient. */
export function Panel({ className, accent, danger, noBorder, children, ...props }: PanelProps) {
  return (
    <div
      className={cn(
        "rounded-lg bg-panel min-w-0",
        !noBorder && (danger ? "border border-rose/60" : accent ? "border border-mint/50" : "border border-hairline"),
        className,
      )}
      {...props}
    >
      {children}
    </div>
  );
}

interface PanelHeaderProps {
  title: ReactNode;
  right?: ReactNode;
  className?: string;
  /** Smaller (32 px) header used inside dense terminal panels */
  dense?: boolean;
}

/** Title row of a panel: white 600 title, optional right slot, hairline below. */
export function PanelHeader({ title, right, className, dense }: PanelHeaderProps) {
  return (
    <div className={cn("flex items-center gap-2 px-3 border-b border-hairline shrink-0", dense ? "h-8" : "h-10", className)}>
      <span className={cn("font-semibold text-text truncate", dense ? "text-[12.5px]" : "text-[13px]")}>{title}</span>
      {right && <div className="ml-auto flex items-center gap-2 shrink-0">{right}</div>}
    </div>
  );
}

/** Small uppercase section label (inside panels / list columns). */
export function SectionLabel({ children, className, right }: { children: ReactNode; className?: string; right?: ReactNode }) {
  return (
    <div className={cn("flex items-center h-7 text-[11px] font-semibold uppercase tracking-[0.06em] text-text-2", className)}>
      <span className="truncate">{children}</span>
      {right && <span className="ml-auto normal-case tracking-normal font-medium">{right}</span>}
    </div>
  );
}

/** Centered white empty-state text ("No open positions found"). */
export function EmptyState({ children, className, sub }: { children: ReactNode; className?: string; sub?: ReactNode }) {
  return (
    <div className={cn("flex-1 flex flex-col items-center justify-center gap-1 py-8 px-4 text-center", className)}>
      <span className="text-[13px] font-medium text-text">{children}</span>
      {sub && <span className="text-[12px] text-text-2">{sub}</span>}
    </div>
  );
}
