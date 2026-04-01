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
}

export function MetricCard({ label, value, format, icon, colorize, glow, className, subtext }: MetricCardProps) {
  return (
    <GlassPanel
      glow={glow}
      className={cn("flex flex-col gap-1 p-4", className)}
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
    >
      <div className="flex items-center gap-2 text-xs text-text-secondary uppercase tracking-wider">
        {icon}
        {label}
      </div>
      <AnimatedNumber
        value={value}
        format={format}
        colorize={colorize}
        className="text-2xl font-semibold font-mono"
      />
      {subtext && <span className="text-xs text-text-muted">{subtext}</span>}
    </GlassPanel>
  );
}
