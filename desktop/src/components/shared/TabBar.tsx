import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export interface TabItem<T extends string> {
  id: T;
  label: string;
  /** Small count/badge rendered after the label */
  badge?: ReactNode;
}

interface TabBarProps<T extends string> {
  tabs: readonly TabItem<T>[];
  value: T;
  onChange: (id: T) => void;
  /** Rendered at the right end of the bar (toolbar slot) */
  right?: ReactNode;
  className?: string;
  size?: "sm" | "md";
}

/** Panel-title tabs of the reference (Chart | Funding | Depth…): underline on the active tab, hairline below. */
export function TabBar<T extends string>({ tabs, value, onChange, right, className, size = "md" }: TabBarProps<T>) {
  return (
    <div className={cn("flex items-stretch border-b border-hairline shrink-0 min-w-0", className)}>
      <div className="flex items-stretch overflow-x-auto scrollbar-none min-w-0">
        {tabs.map((t) => {
          const active = t.id === value;
          return (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => onChange(t.id)}
              className={cn(
                "relative flex items-center gap-1.5 whitespace-nowrap px-3 font-medium transition-colors -mb-px",
                size === "md" ? "h-9 text-[12.5px]" : "h-8 text-[11.5px]",
                active ? "text-text-primary" : "text-text-muted hover:text-text-secondary",
              )}
            >
              {t.label}
              {t.badge !== undefined && t.badge !== null && (
                <span className={cn("font-mono text-[10px] px-1 rounded", active ? "bg-white/10 text-text-primary" : "bg-white/5 text-text-muted")}>
                  {t.badge}
                </span>
              )}
              {active && <span className="absolute left-2 right-2 bottom-0 h-px bg-accent" />}
            </button>
          );
        })}
      </div>
      {right && <div className="ml-auto flex items-center gap-2 pl-2 pr-2 shrink-0">{right}</div>}
    </div>
  );
}
