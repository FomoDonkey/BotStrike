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
}

export function MetricCard({ label, value, format, icon, colorize, glow, className, subtext, display }: MetricCardProps) {
  return (
    <GlassPanel
      glow={glow}
      className={cn("flex flex-col gap-1 p-4 min-w-0", className)}
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
    >
      <div className="flex items-center gap-2 text-xs text-text-secondary uppercase tracking-wider truncate">
        {icon}
        {label}
      </div>
      {display !== undefined ? (
        <span className="text-2xl font-semibold font-mono text-text-muted tabular-nums">{display}</span>
      ) : (
        <AnimatedNumber
          value={value}
          format={format}
          colorize={colorize}
          className="text-2xl font-semibold font-mono"
        />
      )}
      {subtext && <span className="text-xs text-text-muted truncate">{subtext}</span>}
    </GlassPanel>
  );
}
