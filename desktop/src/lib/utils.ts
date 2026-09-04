import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatUSD(value: number, decimals = 2): string {
  const prefix = value < 0 ? "-$" : "$";
  return `${prefix}${Math.abs(value).toFixed(decimals)}`;
}

export function formatPrice(value: number): string {
  if (value >= 1000) return value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (value >= 1) return value.toFixed(4);
  return value.toFixed(6);
}

export function formatPct(value: number, decimals = 2): string {
  return `${(value * 100).toFixed(decimals)}%`;
}

export function formatBps(value: number): string {
  return `${value.toFixed(1)} bps`;
}

export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.floor(seconds % 60)}s`;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return `${h}h ${m}m`;
}

export function formatTime(timestamp: number): string {
  // Auto-detect seconds vs milliseconds
  const ms = timestamp > 1e11 ? timestamp : timestamp * 1000;
  return new Date(ms).toLocaleTimeString("en-US", { hour12: false });
}

/** Thousands separator, no decimals: 1234567 → "1,234,567". */
export function formatInt(value: number): string {
  if (!Number.isFinite(value)) return "---";
  return Math.round(value).toLocaleString("en-US");
}

/** Round-trip cost/return in basis points from a PnL and a notional (entry_price × quantity). */
export function pnlBps(pnl: number, notional: number): number | null {
  if (!Number.isFinite(pnl) || !Number.isFinite(notional) || notional <= 0) return null;
  return (pnl / notional) * 1e4;
}

/** "2026-09-03T00:05:00Z" → local "Sep 3, 02:05" (falls back to the raw string). */
export function formatLocalDateTime(iso: string | null | undefined): string {
  if (!iso) return "---";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

/** Signed relative time: "in 4h 12m" / "3m ago" (ms difference). */
export function formatRelative(targetMs: number, nowMs: number): string {
  const diff = targetMs - nowMs;
  const abs = Math.abs(diff);
  const s = Math.floor(abs / 1000);
  let txt: string;
  if (s < 60) txt = `${s}s`;
  else if (s < 3600) txt = `${Math.floor(s / 60)}m`;
  else if (s < 86400) txt = `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
  else txt = `${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h`;
  return diff >= 0 ? `in ${txt}` : `${txt} ago`;
}

/** Epoch seconds or milliseconds → milliseconds. */
export function toMs(ts: number | undefined | null): number {
  if (typeof ts !== "number" || !Number.isFinite(ts) || ts <= 0) return 0;
  return ts > 1e11 ? ts : ts * 1000;
}

/** Finite number or fallback — WS payloads occasionally carry null/undefined fields. */
export function finiteOr(v: unknown, fallback: number): number {
  return typeof v === "number" && Number.isFinite(v) ? v : fallback;
}

/** Signed percent from a ratio: 0.0123 → "+1.23%". */
export function formatSignedPct(value: number, decimals = 2): string {
  if (!Number.isFinite(value)) return "---";
  const s = (value * 100).toFixed(decimals);
  return `${value > 0 ? "+" : ""}${s}%`;
}

/** Signed money: 1.5 → "+$1.50", -0.2 → "-$0.20". */
export function formatSignedUSD(value: number, decimals = 2): string {
  if (!Number.isFinite(value)) return "---";
  return `${value > 0 ? "+" : ""}${formatUSD(value, decimals)}`;
}

/** Compact USD for volumes / OI: 12_595_740_564 → "$12.60B". */
export function formatCompactUSD(value: number): string {
  if (!Number.isFinite(value) || value < 0) return "---";
  // Zero is a value. Markets on the venue really do go a whole day without a trade, and printing
  // "---" there says "we could not fetch it" about a number we fetched successfully (2026-09-04).
  if (value === 0) return "$0";
  const abs = Math.abs(value);
  if (abs >= 1e9) return `$${(value / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `$${(value / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `$${(value / 1e3).toFixed(1)}K`;
  return `$${value.toFixed(0)}`;
}

/** Compact plain number (contracts / coins): 108103.4 → "108.1K". */
export function formatCompact(value: number, decimals = 1): string {
  if (!Number.isFinite(value)) return "---";
  const abs = Math.abs(value);
  if (abs >= 1e9) return `${(value / 1e9).toFixed(decimals)}B`;
  if (abs >= 1e6) return `${(value / 1e6).toFixed(decimals)}M`;
  if (abs >= 1e3) return `${(value / 1e3).toFixed(decimals)}K`;
  return value.toFixed(abs >= 100 ? 0 : 2);
}

/** Position size with sensible precision by magnitude: 0.00152 BTC / 12.5 SOL / 1500 ADA. */
export function formatSize(value: number): string {
  if (!Number.isFinite(value)) return "---";
  const abs = Math.abs(value);
  if (abs === 0) return "0";
  if (abs < 0.01) return value.toFixed(6);
  if (abs < 1) return value.toFixed(4);
  if (abs < 100) return value.toFixed(3);
  return value.toFixed(1);
}

/** Basis points with sign: 12.34 → "+12.3", -5 → "-5.0"; null/undefined → "---". */
export function formatSignedBps(value: number | null | undefined, decimals = 1): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "---";
  return `${value > 0 ? "+" : ""}${value.toFixed(decimals)}`;
}

/** "1h 05m 12s"-style clock for live hold times (compact, always 2-digit minutes/seconds). */
export function formatClock(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "---";
  const s = Math.floor(seconds);
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(sec).padStart(2, "0");
  if (d > 0) return `${d}d ${String(h).padStart(2, "0")}:${mm}:${ss}`;
  return `${String(h).padStart(2, "0")}:${mm}:${ss}`;
}

/** "HH:MM:SS" (24 h) from epoch seconds or milliseconds. */
export function formatTimeShort(timestamp: number): string {
  const ms = toMs(timestamp);
  if (!ms) return "---";
  return new Date(ms).toLocaleTimeString("en-US", { hour12: false });
}

/** "Sep 2, 11:06" from epoch seconds/milliseconds or an ISO string. */
export function formatDateTime(ts: number | string | null | undefined): string {
  if (typeof ts === "string") return formatLocalDateTime(ts);
  const ms = toMs(ts);
  if (!ms) return "---";
  return new Date(ms).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

/** Money with thousands separators: 1003.42 → "$1,003.42"; negatives "-$0.20". */
export function formatMoney(value: number, decimals = 2): string {
  if (!Number.isFinite(value)) return "---";
  const abs = Math.abs(value).toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
  return `${value < 0 ? "-$" : "$"}${abs}`;
}

/** Signed money with thousands separators: "+$1,003.42". */
export function formatSignedMoney(value: number, decimals = 2): string {
  if (!Number.isFinite(value)) return "---";
  return `${value > 0 ? "+" : ""}${formatMoney(value, decimals)}`;
}

/** Feed age "0.1 s" / "12 s" / "3 min". */
export function formatAge(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds)) return "---";
  // the store stamps ticks with Date.now() while useNow() ticks once a second → up to ~1 s "negative" age
  if (seconds < 0) {
    if (seconds > -5) seconds = 0;
    else return "---";
  }
  if (seconds < 10) return `${seconds.toFixed(1)} s`;
  if (seconds < 90) return `${Math.round(seconds)} s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min`;
  return `${(seconds / 3600).toFixed(1)} h`;
}

/** "5h 37m" style duration for schedules; seconds → compact text. */
export function formatDurationShort(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "---";
  const s = Math.floor(seconds);
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h < 24) return `${h}h ${String(m).padStart(2, "0")}m`;
  return `${Math.floor(h / 24)}d ${h % 24}h`;
}

/** Capitalise the first letter: "paper" → "Paper". */
export function capitalize(s: string): string {
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
}
