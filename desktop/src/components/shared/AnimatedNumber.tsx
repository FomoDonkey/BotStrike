import { memo, useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";

interface AnimatedNumberProps {
  value: number;
  format?: (v: number) => string;
  className?: string;
  colorize?: boolean;
  flash?: boolean;
}

export const AnimatedNumber = memo(function AnimatedNumber({
  value,
  format,
  className,
  colorize,
  flash,
}: AnimatedNumberProps) {
  const [display, setDisplay] = useState(value);
  const [flashDir, setFlashDir] = useState<"up" | "down" | null>(null);
  const prev = useRef(value);
  const animRef = useRef<number | undefined>(undefined);

  useEffect(() => {
    const from = prev.current;
    const to = value;
    const diff = to - from;
    prev.current = to;

    if (Math.abs(diff) < 1e-10) {
      setDisplay(to);
      return;
    }

    if (flash) {
      setDisplay(to);
      setFlashDir(diff > 0 ? "up" : "down");
      const t = setTimeout(() => setFlashDir(null), 300);
      return () => clearTimeout(t);
    }

    // Animate over 200ms with ease-out cubic
    if (animRef.current) cancelAnimationFrame(animRef.current);
    const start = performance.now();
    const duration = 200;

    const tick = (now: number) => {
      const elapsed = now - start;
      const t = Math.min(elapsed / duration, 1);
      const eased = 1 - Math.pow(1 - t, 3);
      setDisplay(from + diff * eased);
      if (t < 1) {
        animRef.current = requestAnimationFrame(tick);
      } else {
        animRef.current = undefined;
      }
    };

    animRef.current = requestAnimationFrame(tick);

    return () => {
      if (animRef.current) {
        cancelAnimationFrame(animRef.current);
        animRef.current = undefined;
      }
    };
  }, [value, flash]);

  const safeDisplay = Number.isFinite(display) ? display : 0;
  const formatted = format ? format(safeDisplay) : safeDisplay.toFixed(2);

  return (
    <span
      className={cn(
        "tabular-nums transition-colors duration-200",
        colorize && value > 0 && "text-profit",
        colorize && value < 0 && "text-loss",
        colorize && value === 0 && "",
        flash && flashDir === "up" && "text-profit",
        flash && flashDir === "down" && "text-loss",
        className
      )}
    >
      {formatted}
    </span>
  );
});
