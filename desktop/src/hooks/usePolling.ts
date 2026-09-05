import { useEffect, useRef } from "react";

/**
 * Run `fn` now, every `intervalMs`, and again the moment the tab becomes visible.
 * Ticks are skipped while the tab is hidden (no point polling a throttled tab) and
 * the latest `fn` is always used without re-arming the interval — pass an inline
 * function freely.
 */
export function usePolling(fn: () => void | Promise<unknown>, intervalMs: number, enabled = true) {
  const fnRef = useRef(fn);
  useEffect(() => {
    fnRef.current = fn;
  });

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    let first = true;
    const run = () => {
      // The first fetch always goes out, hidden or not: a page opened in a background tab used
      // to sit on "---" and "Loading…" until it was brought to the front (2026-09-05). Only the
      // periodic ticks wait for the tab to be visible.
      if (cancelled || (!first && document.visibilityState === "hidden")) return;
      first = false;
      void fnRef.current();
    };
    run();
    const iv = setInterval(run, intervalMs);
    const onVisible = () => {
      if (document.visibilityState === "visible") run();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      clearInterval(iv);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [intervalMs, enabled]);
}
