import { cn } from "@/lib/utils";

interface PulsingDotProps {
  active: boolean;
  className?: string;
}

export function PulsingDot({ active, className }: PulsingDotProps) {
  return (
    <span className={cn("relative flex h-2.5 w-2.5", className)}>
      {active && (
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent opacity-75" />
      )}
      <span
        className={cn(
          "relative inline-flex h-2.5 w-2.5 rounded-full",
          active ? "bg-accent" : "bg-text-muted"
        )}
      />
    </span>
  );
}
