// Derived market / position maths shared by the terminal, Dashboard and Performance.
// Everything here is a pure function of bridge payloads; when a ≥ 2.15 field is present it
// wins, otherwise the value is derived and the caller labels it as such.
import type { ExitLadder, ExitLadderLevel, PositionData, TradeRecord } from "@/lib/api";
import type { Candle } from "@/stores/marketStore";
import { SYMBOL_LABELS } from "@/lib/constants";

/** "BTC" from "BTC-USD" — works for the non-crypto markets of the multi-asset pool too. */
export function marketLabel(symbol: string): string {
  return SYMBOL_LABELS[symbol] ?? symbol.split("-")[0];
}

/**
 * The engine's venue key (BTCUSDT) as the market name every table shows (BTC-USD).
 *
 * The trend panels listed "BTCUSDT, SOLUSDT, WTI-USD, SP500-USD" side by side: two naming schemes
 * for one book, in one list (audit 2026-09-03).
 */
export function marketName(symbol: string): string {
  const s = (symbol ?? "").toUpperCase();
  if (s.includes("-")) return s;
  if (s.endsWith("USDT")) return `${s.slice(0, -4)}-USD`;
  if (s.endsWith("USD")) return `${s.slice(0, -3)}-USD`;
  return s;
}

/** Maintenance margin used by the paper liquidation estimate (contract §2). */
export const PAPER_MAINTENANCE_MARGIN = 0.005;

/** A trend position exits in steps; `null` for intraday strategies, which carry a real SL/TP. */
export function exitLadderOf(p: PositionData): ExitLadder | null {
  const l = p.exit_ladder;
  return l && Array.isArray(l.levels) && l.levels.length > 0 ? l : null;
}

/**
 * Two Donchian lookbacks often share the same stop price (D20 and D30 on the same channel low).
 * Merge them so one price = one rung: the shares add up and the chart draws a single line.
 */
export function mergedLadderLevels(ladder: ExitLadder): ExitLadderLevel[] {
  const out: ExitLadderLevel[] = [];
  for (const lv of ladder.levels ?? []) {
    if (!(lv.stop > 0)) continue;
    const prev = out.find((o) => Math.abs(o.stop - lv.stop) < 1e-9);
    if (prev) {
      prev.share_exiting += lv.share_exiting ?? 0;
      prev.weight_after = Math.min(prev.weight_after, lv.weight_after ?? 0);
      prev.lookback = lv.lookback;
    } else {
      out.push({ ...lv });
    }
  }
  return out;
}

/** `exit 25 %` … `full exit` — the label on the chart line and in the hover card. */
export function ladderLevelLabel(lv: ExitLadderLevel): string {
  if ((lv.weight_after ?? 0) <= 1e-9) return "full exit";
  return `exit ${Math.round((lv.share_exiting ?? 0) * 100)} %`;
}

export interface Stats24h {
  /** open of the first candle inside the window (reference for the change) */
  ref_open: number | null;
  high: number | null;
  low: number | null;
  /** seconds actually covered by the candle window (bridge keeps ~16 h of 1m bars) */
  span_sec: number;
  volume_base: number;
}

/**
 * 24h reference open / high / low from the 1m candles in memory. The bridge keeps ~16 h, so the
 * result is labelled by the real span (a 16 h window is NOT a 24 h window and the UI says so).
 * Fold the live price in with `change24h()` so this memo only recomputes when candles change.
 */
export function stats24h(candles: Candle[] | undefined, nowSec: number): Stats24h {
  if (!candles?.length) return { ref_open: null, high: null, low: null, span_sec: 0, volume_base: 0 };
  const cutoff = nowSec - 24 * 3600;
  let first: Candle | null = null;
  let high = -Infinity;
  let low = Infinity;
  let vol = 0;
  for (const c of candles) {
    if (c.time < cutoff) continue;
    if (!first) first = c;
    if (c.high > high) high = c.high;
    if (c.low < low) low = c.low;
    vol += c.volume || 0;
  }
  if (!first) return { ref_open: null, high: null, low: null, span_sec: 0, volume_base: 0 };
  const last = candles[candles.length - 1];
  return {
    ref_open: first.open > 0 ? first.open : first.close,
    high: Number.isFinite(high) ? high : null,
    low: Number.isFinite(low) ? low : null,
    span_sec: Math.max(0, last.time + 60 - first.time),
    volume_base: vol,
  };
}

/** Change ratio vs the window's reference open, with the live price folded in. */
export function change24h(stats: Stats24h, price: number): number | null {
  if (!stats.ref_open || !(price > 0)) return null;
  return (price - stats.ref_open) / stats.ref_open;
}

/** Human span label for a client-side window: 86400 → "24h", 57600 → "16h". */
export function spanLabel(spanSec: number): string {
  if (spanSec >= 23.5 * 3600) return "24h";
  const h = spanSec / 3600;
  return h >= 1 ? `${Math.round(h)}h` : `${Math.max(1, Math.round(spanSec / 60))}m`;
}

/**
 * The venue's settlement cadence is a SERVER fact (Strike settles hourly, Binance every 8 h), so the
 * client never guesses it. While the market payload is still in flight this returns null and the UI
 * shows "---": the old hard-coded 8 h fallback displayed a confident "04:27:03" on an hourly venue
 * for the first seconds after every page load (audit 2026-09-03).
 */
export function fundingCountdownSec(): number | null {
  return null;
}

/** "HH:MM:SS" countdown. */
export function formatCountdown(sec: number | null | undefined): string {
  if (sec === null || sec === undefined || !Number.isFinite(sec) || sec < 0) return "--:--:--";
  const s = Math.floor(sec);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}`;
}

export function isLong(side: string | undefined): boolean {
  const s = (side ?? "").toUpperCase();
  return s === "BUY" || s === "LONG";
}

/** Notional at mark (falls back to entry) — never negative. */
export function positionNotional(p: PositionData): number {
  if (typeof p.notional === "number" && Number.isFinite(p.notional) && p.notional > 0) return p.notional;
  const px = p.mark_price > 0 ? p.mark_price : p.entry_price;
  return Math.abs((p.size || 0) * (px || 0));
}

export function positionLeverage(p: PositionData): number {
  return typeof p.leverage === "number" && Number.isFinite(p.leverage) && p.leverage > 0 ? p.leverage : 1;
}

/** Margin = notional / leverage unless the bridge reports it. */
export function positionMargin(p: PositionData): number {
  if (typeof p.margin === "number" && Number.isFinite(p.margin) && p.margin > 0) return p.margin;
  const lev = positionLeverage(p);
  const entryNotional = Math.abs((p.size || 0) * (p.entry_price || 0));
  return (entryNotional > 0 ? entryNotional : positionNotional(p)) / lev;
}

/** ROE = unrealized / margin (bridge `roe_pct` wins). Ratio, not percent. */
export function positionRoe(p: PositionData): number | null {
  if (typeof p.roe_pct === "number" && Number.isFinite(p.roe_pct)) return p.roe_pct;
  const m = positionMargin(p);
  if (m <= 0) return null;
  return (p.unrealized_pnl || 0) / m;
}

export interface LiqEstimate {
  price: number | null;
  /** true when computed here from the contract formula rather than reported by the bridge */
  estimated: boolean;
}

/**
 * Liquidation price: bridge value when > 0; otherwise the paper formula (contract §2) for
 * leveraged positions; null for leverage ≤ 1 (spot-like, e.g. trend daily).
 */
export function positionLiquidation(p: PositionData): LiqEstimate {
  if (typeof p.liquidation_price === "number" && Number.isFinite(p.liquidation_price) && p.liquidation_price > 0) {
    return { price: p.liquidation_price, estimated: false };
  }
  const lev = positionLeverage(p);
  if (lev <= 1 || !(p.entry_price > 0)) return { price: null, estimated: false };
  const mm = PAPER_MAINTENANCE_MARGIN;
  const px = isLong(p.side) ? p.entry_price * (1 - 1 / lev + mm) : p.entry_price * (1 + 1 / lev - mm);
  return { price: px, estimated: true };
}

/** Signed distance of a level from the mark, as a ratio (negative = below mark). */
export function distancePct(level: number | null | undefined, mark: number): number | null {
  if (typeof level !== "number" || !Number.isFinite(level) || level <= 0 || !(mark > 0)) return null;
  return (level - mark) / mark;
}

/**
 * Distance in PnL direction — the bridge's convention for SL/TP on positions: negative =
 * adverse (towards the stop) for BOTH sides. long: level/mark − 1; short: 1 − level/mark.
 */
export function pnlDistancePct(level: number | null | undefined, mark: number, side: string): number | null {
  const d = distancePct(level, mark);
  if (d === null) return null;
  return isLong(side) ? d : -d;
}

/** Epoch seconds the position was opened (≥ 2.15 `opened_ts`, 2.14 `timestamp`). */
export function positionOpenedTs(p: PositionData): number {
  const v = p.opened_ts ?? p.timestamp ?? 0;
  return typeof v === "number" && Number.isFinite(v) && v > 0 ? (v > 1e11 ? v / 1000 : v) : 0;
}

/** Live hold time in seconds (bridge `hold_sec` is a snapshot; the clock keeps running). */
export function positionHoldSec(p: PositionData, nowMs: number): number | null {
  const opened = positionOpenedTs(p);
  if (opened > 0) return Math.max(0, nowMs / 1000 - opened);
  if (typeof p.hold_sec === "number" && Number.isFinite(p.hold_sec)) return p.hold_sec;
  return null;
}

/** Position side of a closed trade: an EXIT row's side is the fill side (SELL closed a long). */
export function tradePositionSide(t: TradeRecord): "LONG" | "SHORT" {
  if (t.trade_type === "EXIT") return t.side === "SELL" ? "LONG" : "SHORT";
  return t.side === "BUY" ? "LONG" : "SHORT";
}

export function tradeNotional(t: TradeRecord): number {
  return Math.abs((t.entry_price || 0) * (t.quantity || 0));
}

/** ROE of a closed trade: bridge `roe_pct`, else pnl / (notional / leverage). Ratio. */
export function tradeRoe(t: TradeRecord): number | null {
  if (typeof t.roe_pct === "number" && Number.isFinite(t.roe_pct)) return t.roe_pct;
  const n = tradeNotional(t);
  if (n <= 0) return null;
  const lev = typeof t.leverage === "number" && t.leverage > 0 ? t.leverage : 1;
  return (t.pnl || 0) / (n / lev);
}

export function tradeHoldSec(t: TradeRecord): number | null {
  if (typeof t.hold_sec === "number" && Number.isFinite(t.hold_sec) && t.hold_sec > 0) return t.hold_sec;
  if (typeof t.duration_sec === "number" && t.duration_sec > 0) return t.duration_sec;
  if (t.entry_ts && t.exit_ts && t.exit_ts > t.entry_ts) return t.exit_ts - t.entry_ts;
  return null;
}

/**
 * What the position returns AGAINST ITS ENTRY if every remaining leg trails out at today's stops.
 *
 * The ladder's own `worst_case_pct` is measured from the CURRENT price, which answers "how far can
 * it still fall" but not the question an operator actually asks: "if this trails out from here, do
 * I keep a profit?" A position can show +6.5 % unrealised and still have every stop below its entry
 * (BTC on 2026-09-03: entry 76,571.65, full exit 69,437.15).
 */
export function ladderOutcomeVsEntry(ladder: ExitLadder, entry: number | null | undefined): number | null {
  const levels = ladder.levels ?? [];
  if (!entry || entry <= 0 || levels.length === 0) return null;
  let weight = 0;
  let acc = 0;
  for (const lv of levels) {
    const share = lv.share_exiting ?? 0;
    if (!(share > 0) || !(lv.stop > 0)) continue;
    weight += share;
    acc += share * (lv.stop / entry - 1);
  }
  return weight > 0 ? acc / weight : null;
}

/**
 * Funding colour and wording.
 *
 * The venue colours the RATE by its sign (positive mint, negative rose) and states the direction in
 * words — "Long Pays Short" / "Short Pays Long". We matched that on 2026-09-04: colouring positive as
 * a cost looked wrong beside Strike's green, and our own picker was already sign-coloured, so the UI
 * disagreed with itself. The meaning for a long-only book is carried by the words, which cannot be
 * misread whichever palette you are used to.
 *
 * Cash already paid or received is a different quantity: that stays signed as money (rose = out).
 */
export function fundingTone(rate: number | null | undefined): "mint" | "rose" | "" {
  if (typeof rate !== "number" || rate === 0) return "";
  return rate > 0 ? "mint" : "rose";
}

export function fundingDirection(rate: number | null | undefined): string {
  if (typeof rate !== "number" || rate === 0) return "Nothing changes hands this settlement";
  return rate > 0 ? "Long pays short" : "Short pays long";
}

/** What it means for THIS book, which is long-only. */
export function fundingMeaning(rate: number | null | undefined): string {
  if (typeof rate !== "number" || rate === 0) return "the book neither pays nor is paid";
  return rate > 0 ? "the book pays" : "the book is paid";
}
