import { memo, useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";

interface AnimatedNumberProps {
  value: number;
  format?: (v: number) => string;
  className?: string;
  colorize?: boolean;
  flash?: boolean;
}

const ANIM_MS = 200;
const FLASH_MS = 300;

export const AnimatedNumber = memo(function AnimatedNumber({
  value,
  format,
  className,
  colorize,
  flash,
}: AnimatedNumberProps) {
  const [display, setDisplay] = useState(value);
  const [flashDir, setFlashDir] = useState<"up" | "down" | null>(null);
  const [seen, setSeen] = useState(value);
  const prev = useRef(value);
  const animRef = useRef<number | undefined>(undefined);

  // Flash mode: snap to the new value during render (state adjustment, no effect needed).
  if (value !== seen) {
    setSeen(value);
    if (flash) {
      setDisplay(value);
      setFlashDir(value > seen ? "up" : "down");
    }
  }

  // Clear the flash highlight after a short delay.
  useEffect(() => {
    if (!flash || !flashDir) return;
    const t = setTimeout(() => setFlashDir(null), FLASH_MS);
    return () => clearTimeout(t);
  }, [flash, flashDir, value]);

  // Animate towards the new value (ease-out cubic). All setState calls happen inside rAF callbacks.
  useEffect(() => {
    if (flash) return;
    const from = prev.current;
    const to = value;
    const diff = to - from;
    prev.current = to;

    if (animRef.current) cancelAnimationFrame(animRef.current);

    if (Math.abs(diff) < 1e-10) {
      // Nothing to animate; make sure a previously cancelled animation didn't leave us mid-way.
      animRef.current = requestAnimationFrame(() => { animRef.current = undefined; setDisplay(to); });
      return () => { if (animRef.current) cancelAnimationFrame(animRef.current); };
    }

    const start = performance.now();
    const tick = (now: number) => {
      const t = Math.min((now - start) / ANIM_MS, 1);
      const eased = 1 - Math.pow(1 - t, 3);
      setDisplay(from + diff * eased);
      animRef.current = t < 1 ? requestAnimationFrame(tick) : undefined;
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
