import { SYMBOLS, SYMBOL_LABELS, SYMBOL_COLORS } from "@/lib/constants";
import { cn } from "@/lib/utils";

interface SymbolSelectorProps {
  value: string;
  onChange: (symbol: string) => void;
  variant?: "tabs" | "dropdown";
  className?: string;
}

export function SymbolSelector({ value, onChange, variant = "tabs", className }: SymbolSelectorProps) {
  if (variant === "dropdown") {
    return (
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={cn(
          "bg-bg-base border border-white/10 rounded-lg px-3 py-2 text-sm text-text-primary font-mono",
          "focus:outline-none focus:border-accent/50",
          className,
        )}
      >
        {SYMBOLS.map((sym) => (
          <option key={sym} value={sym}>{sym}</option>
        ))}
      </select>
    );
  }

  return (
    <div className={cn("flex items-center gap-0.5 bg-white/[0.03] rounded-lg p-0.5", className)}>
      {SYMBOLS.map((sym) => {
        const label = SYMBOL_LABELS[sym] || sym;
        const active = value === sym;
        return (
          <button
            key={sym}
            onClick={() => onChange(sym)}
            className={cn(
              "px-2.5 py-1 rounded text-[11px] font-mono font-medium transition-all",
              active
                ? "text-bg-base"
                : "text-text-muted hover:text-text-secondary",
            )}
            style={active ? { backgroundColor: SYMBOL_COLORS[sym] || "#6C5CE7" } : undefined}
          >
            {label}
          </button>
        );
      })}
    </div>
  );
}
