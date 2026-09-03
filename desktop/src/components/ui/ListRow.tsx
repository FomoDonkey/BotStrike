import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import { Hint } from "@/components/shared/Hint";

interface ListRowProps {
  label: ReactNode;
  /** Tooltip → dotted underline on the label */
  hint?: string;
  children: ReactNode;
  className?: string;
  /** Larger value (hero rows) */
  size?: "sm" | "md";
}

/** Label `--text-2` left · white 600 value right — Strike's account/portfolio list rows. */
export function ListRow({ label, hint, children, className, size = "sm" }: ListRowProps) {
  return (
    <div className={cn("flex items-center justify-between gap-3 min-w-0", size === "sm" ? "h-7" : "h-8", className)}>
      <span className={cn("text-text-2 font-medium truncate", size === "sm" ? "text-[12.5px]" : "text-[13px]")}>
        {hint ? <Hint title={hint}>{label}</Hint> : label}
      </span>
      <span className={cn("num text-text font-semibold text-right whitespace-nowrap", size === "sm" ? "text-[12.5px]" : "text-[13px]")}>{children}</span>
    </div>
  );
}

/** Group of rows with an uppercase title and a hairline above. */
export function ListSection({ title, children, className, right, first }: { title?: ReactNode; children: ReactNode; className?: string; right?: ReactNode; first?: boolean }) {
  return (
    <div className={cn("px-3 py-2", !first && "border-t border-hairline", className)}>
      {title && (
        <div className="flex items-center h-7 mb-0.5">
          <span className="text-[11px] font-semibold uppercase tracking-[0.06em] text-text-2 truncate">{title}</span>
          {right && <span className="ml-auto text-[12px] font-medium text-text">{right}</span>}
        </div>
      )}
      {children}
    </div>
  );
}

/** Signed value coloured by sign (mint / rose / white for zero). */
export function Signed({ value, format, className, zeroClass = "text-text" }: { value: number | null | undefined; format: (v: number) => string; className?: string; zeroClass?: string }) {
  if (typeof value !== "number" || !Number.isFinite(value)) return <span className={cn("text-text-3", className)}>---</span>;
  return <span className={cn("num", value > 0 ? "text-mint" : value < 0 ? "text-rose" : zeroClass, className)}>{format(value)}</span>;
}
