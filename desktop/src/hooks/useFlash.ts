import { useEffect, useRef, type RefObject } from "react";

/**
 * Flash an element when a number changes — up/down classes are toggled straight on the DOM
 * node from an effect, so no React state is involved (the previous render-phase
 * `if (value !== seen) setSeen(...)` pattern is what looped into React #185 when a NaN
 * slipped in). Pass `null` to disable. A change from/to 0 ("---" placeholder) never flashes.
 */
export function useFlashOnChange(
  ref: RefObject<HTMLElement | null>,
  value: number | null,
  upClasses: string,
  downClasses: string,
  ms = 400,
) {
  const prev = useRef<number | null>(null);
  useEffect(() => {
    const before = prev.current;
    prev.current = value;
    const el = ref.current;
    if (!el || value === null || before === null || before === value || before === 0 || value === 0) return;
    if (!Number.isFinite(value) || !Number.isFinite(before)) return;
    const cls = (value > before ? upClasses : downClasses).split(" ").filter(Boolean);
    el.classList.add(...cls);
    const t = setTimeout(() => el.classList.remove(...cls), ms);
    return () => {
      clearTimeout(t);
      el.classList.remove(...cls);
    };
  }, [value, ref, upClasses, downClasses, ms]);
}
