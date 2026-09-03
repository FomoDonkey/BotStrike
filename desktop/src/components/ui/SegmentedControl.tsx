import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export interface SegmentOption<T extends string> {
  id: T;
  label: ReactNode;
  title?: string;
  disabled?: boolean;
}

interface SegmentedControlProps<T extends string> {
  options: readonly SegmentOption<T>[];
  value: T;
  onChange: (id: T) => void;
  size?: "sm" | "md";
  className?: string;
  /** Read-only display (Strike's "Cross · 100x · One-Way" header): every segment rendered, none clickable */
  static?: boolean;
}

/** Track `--panel-2`, active segment `--active` with white text, inactive `--text-2`. */
export function SegmentedControl<T extends string>({ options, value, onChange, size = "md", className, static: isStatic }: SegmentedControlProps<T>) {
  return (
    <div role="group" className={cn("inline-flex items-stretch rounded-[6px] bg-panel-2 p-[2px] gap-[2px]", className)}>
      {options.map((o) => {
        const active = o.id === value;
        return (
          <button
            key={o.id}
            type="button"
            aria-pressed={active}
            title={o.title}
            disabled={o.disabled || isStatic}
            onClick={() => onChange(o.id)}
            className={cn(
              "rounded-[5px] font-medium whitespace-nowrap transition-colors",
              size === "sm" ? "h-6 px-2 text-[12px]" : "h-7 px-3 text-[13px]",
              active ? "bg-active text-text" : "text-text-2 hover:text-text hover:bg-hover",
              isStatic && "cursor-default",
              o.disabled && !isStatic && "opacity-50",
            )}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

/** Pills (24H / 1W / 1M, 7D / 30D / ALL) — same look as a small segmented control. */
export function RangePills<T extends string>({ options, value, onChange, className }: { options: readonly SegmentOption<T>[]; value: T; onChange: (id: T) => void; className?: string }) {
  return <SegmentedControl options={options} value={value} onChange={onChange} size="sm" className={className} />;
}
