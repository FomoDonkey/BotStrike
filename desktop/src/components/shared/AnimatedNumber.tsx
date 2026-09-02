import { memo, useLayoutEffect, useRef } from "react";
import { cn } from "@/lib/utils";
import { useFlashOnChange } from "@/hooks/useFlash";

interface AnimatedNumberProps {
  value: number;
  format?: (v: number) => string;
  className?: string;
  colorize?: boolean;
  /** Snap to the new value and flash the text colour instead of tweening. */
  flash?: boolean;
}

const ANIM_MS = 200;
const FLASH_MS = 300;
const FLASH_UP = "text-profit";
const FLASH_DOWN = "text-loss";

function defaultFormat(v: number) {
  return v.toFixed(2);
}

/**
 * Tweened number without any React state: React always renders the FINAL value; the
 * ease-out tween writes intermediate frames straight into the text node from a layout
 * effect (rAF), and the flash is a class toggled on the DOM node. Zero setState → it can
 * never take part in an update loop, and a NaN/undefined value is coerced to 0 up front.
 */
export const AnimatedNumber = memo(function AnimatedNumber({
  value,
  format,
  className,
  colorize,
  flash,
}: AnimatedNumberProps) {
  const v = Number.isFinite(value) ? value : 0;
  const ref = useRef<HTMLSpanElement>(null);
  const formatRef = useRef<(v: number) => string>(format ?? defaultFormat);
  const shownRef = useRef(v); // last number written to the DOM (mid-tween included)

  useLayoutEffect(() => {
    formatRef.current = format ?? defaultFormat;
  });

  useFlashOnChange(ref, flash ? v : null, FLASH_UP, FLASH_DOWN, FLASH_MS);

  useLayoutEffect(() => {
    const el = ref.current;
    const from = shownRef.current;
    const to = v;
    if (!el || flash || Math.abs(to - from) < 1e-9) {
      shownRef.current = to;
      return;
    }
    const write = (x: number) => {
      const text = formatRef.current(x);
      const tn = el.firstChild;
      if (tn && tn.nodeType === Node.TEXT_NODE) tn.nodeValue = text;
      else el.textContent = text;
    };
    // React just committed the final text; put the previous value back before paint so the
    // tween starts where the eye left off.
    write(from);
    const start = performance.now();
    let raf = 0;
    const tick = (now: number) => {
      const t = Math.min((now - start) / ANIM_MS, 1);
      const eased = 1 - Math.pow(1 - t, 3);
      const cur = t >= 1 ? to : from + (to - from) * eased;
      shownRef.current = cur;
      write(cur);
      if (t < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    // Cleanup only cancels the frame: shownRef keeps the last value actually written, so a
    // value that changes mid-tween starts the next tween from what is on screen (no jump).
    return () => cancelAnimationFrame(raf);
  }, [v, flash]);

  const text = (format ?? defaultFormat)(v);

  return (
    <span
      ref={ref}
      className={cn(
        "tabular-nums transition-colors duration-200",
        colorize && v > 0 && "text-profit",
        colorize && v < 0 && "text-loss",
        className,
      )}
    >
      {text}
    </span>
  );
});
