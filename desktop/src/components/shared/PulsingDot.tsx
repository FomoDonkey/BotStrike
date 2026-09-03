import { cn } from "@/lib/utils";

interface PulsingDotProps {
  active: boolean;
  className?: string;
  /** Colour when inactive (default rose) */
  inactive?: "rose" | "amber" | "grey";
}

export function PulsingDot({ active, className, inactive = "rose" }: PulsingDotProps) {
  return (
    <span className={cn("relative flex h-2.5 w-2.5 shrink-0", className)}>
      {active && <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-mint opacity-60" />}
      <span className={cn("relative inline-flex h-2.5 w-2.5 rounded-full", active ? "bg-mint" : inactive === "amber" ? "bg-amber" : inactive === "grey" ? "bg-white/40" : "bg-rose")} />
    </span>
  );
}
