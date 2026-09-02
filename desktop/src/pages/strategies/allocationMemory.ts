// Last non-zero allocation per strategy, so "enable" restores what the user had instead of a
// hard-coded default. localStorage only — it is a UI convenience, the bridge is the truth.

const KEY = (type: string) => `botstrike.alloc.${type}`;

export function rememberAllocation(type: string, value: number) {
  if (!Number.isFinite(value) || value <= 0) return;
  try {
    localStorage.setItem(KEY(type), String(value));
  } catch {
    /* storage unavailable */
  }
}

export function recallAllocation(type: string): number | null {
  try {
    const v = parseFloat(localStorage.getItem(KEY(type)) ?? "");
    return Number.isFinite(v) && v > 0 ? v : null;
  } catch {
    return null;
  }
}

/** Allocation to use when enabling a strategy that has none stored. */
export function defaultAllocation(type: string): number {
  return type === "TREND_DAILY" ? 1.0 : 0.5;
}
