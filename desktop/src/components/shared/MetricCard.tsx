import { GlassPanel } from "./GlassPanel";
import { AnimatedNumber } from "./AnimatedNumber";
import { cn } from "@/lib/utils";

interface MetricCardProps {
  label: string;
  value: number;
  format?: (v: number) => string;
  icon?: React.ReactNode;
  colorize?: boolean;
  glow?: boolean;
  className?: string;
  subtext?: string;
  /** Render this text instead of the number (e.g. "n/a" when a statistic is not valid yet). */
  display?: string;
  /** Explanatory tooltip → the label gets the dotted "hint" underline. */
  hint?: string;
}

export function MetricCard({ label, value, format, icon, colorize, glow, className, subtext, display, hint }: MetricCardProps) {
  return (
    <GlassPanel
      glow={glow}
      className={cn("flex flex-col gap-1 px-4 py-3 min-w-0", className)}
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2 }}
    >
      <div className="flex items-center gap-2 text-[10.5px] text-text-muted uppercase tracking-[0.06em] truncate">
        {icon}
        <span className={cn(hint && "hint")} title={hint}>{label}</span>
      </div>
      {display !== undefined ? (
        <span className="text-xl font-semibold font-mono text-text-muted tabular-nums">{display}</span>
      ) : (
        <AnimatedNumber
          value={value}
          format={format}
          colorize={colorize}
          className="text-xl font-semibold font-mono text-text-primary"
        />
      )}
      {subtext && <span className="text-[11px] text-text-muted truncate">{subtext}</span>}
    </GlassPanel>
  );
}
