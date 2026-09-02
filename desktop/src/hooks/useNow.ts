import { useSyncExternalStore } from "react";

// One shared 1 s ticker for every "in 4h 12m" / "3m ago" label — reading Date.now() during
// render is impure (and flagged by the compiler lint), so components subscribe to this instead.
let nowValue = Date.now();
const listeners = new Set<() => void>();
let timer: ReturnType<typeof setInterval> | null = null;

function subscribe(cb: () => void) {
  listeners.add(cb);
  if (!timer) {
    timer = setInterval(() => {
      nowValue = Date.now();
      listeners.forEach((l) => l());
    }, 1000);
  }
  return () => {
    listeners.delete(cb);
    if (listeners.size === 0 && timer) {
      clearInterval(timer);
      timer = null;
    }
  };
}

function getSnapshot() {
  return nowValue;
}

/** Current epoch milliseconds, refreshed once per second while mounted. */
export function useNow(): number {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}
