import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export interface TabItem<T extends string> {
  id: T;
  label: ReactNode;
  /** Count rendered after the label (Positions 3) */
  count?: number;
  title?: string;
}

interface TabBarProps<T extends string> {
  tabs: readonly TabItem<T>[];
  value: T;
  onChange: (id: T) => void;
  /** Toolbar slot at the right end */
  right?: ReactNode;
  className?: string;
  size?: "sm" | "md";
  /** No bottom hairline (when the parent draws one) */
  flush?: boolean;
}

/** Text `--text-2`, active white with a 2 px mint underline; counts as white numbers. Scrolls when narrow. */
export function TabBar<T extends string>({ tabs, value, onChange, right, className, size = "md", flush }: TabBarProps<T>) {
  return (
    <div className={cn("flex items-stretch shrink-0 min-w-0", !flush && "border-b border-hairline", className)}>
      <div role="tablist" className="flex items-stretch overflow-x-auto scrollbar-none min-w-0">
        {tabs.map((t) => {
          const active = t.id === value;
          return (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={active}
              title={t.title}
              onClick={() => onChange(t.id)}
              className={cn(
                "relative flex items-center gap-1.5 whitespace-nowrap px-3 font-medium transition-colors",
                size === "md" ? "h-10 text-[13px]" : "h-9 text-[12.5px]",
                active ? "text-text" : "text-text-2 hover:text-text",
              )}
            >
              {t.label}
              {typeof t.count === "number" && (
                <span className={cn("num text-[11.5px] font-semibold", active ? "text-text" : "text-text-2")}>{t.count}</span>
              )}
              {active && <span className="absolute left-2 right-2 bottom-0 h-[2px] rounded-full bg-mint" />}
            </button>
          );
        })}
      </div>
      {right && <div className="ml-auto flex items-center gap-2 pl-2 pr-2 shrink-0">{right}</div>}
    </div>
  );
}
