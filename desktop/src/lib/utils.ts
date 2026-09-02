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
