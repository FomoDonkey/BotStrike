import { useEffect, useRef } from "react";

/**
 * Run `fn` now, every `intervalMs`, and again the moment the tab becomes visible.
 * The latest `fn` is always used without re-arming the interval — pass an inline function freely.
 *
 * Ticks are NOT skipped while `document.visibilityState` is "hidden" (they were until 2026-09-08).
 * Chrome on Windows reports an OCCLUDED window as hidden — a terminal or another app in front of
 * the browser — and then every polled panel froze (Portfolio's side panel sat on "updated 60 s
 * ago · stale") while the WebSocket kept the headline moving: exactly the "metrics that do not
 * update by themselves" Edgar saw. A truly hidden tab is throttled by the browser itself (timers
 * at 1/min after five minutes), which is all the saving we need.
 */
export function usePolling(fn: () => void | Promise<unknown>, intervalMs: number, enabled = true) {
  const fnRef = useRef(fn);
  useEffect(() => {
    fnRef.current = fn;
  });

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    const run = () => {
      if (cancelled) return;
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
