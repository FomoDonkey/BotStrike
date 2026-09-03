// Chart overlay builders + appliers (lightweight-charts v4). Pure helpers so CandlestickChart
// stays readable: price lines for open positions (entry / SL / TP / liq) and divergence
// overlays (pivot-to-pivot line, pivot markers, trigger level) from DIVERGENCE signals.
import type { IChartApi, IPriceLine, ISeriesApi, LineStyle, SeriesMarker, Time, UTCTimestamp } from "lightweight-charts";
import type { PositionData } from "@/lib/api";
import type { DivergencePivot, SignalData, SignalMetadata } from "@/stores/tradingStore";
import { COLOR_DOWN, COLOR_UP, STRATEGY_COLORS } from "@/lib/constants";
import { isLong, positionLiquidation } from "@/lib/market";

export type LineStyleName = "solid" | "dashed" | "dotted";

export interface PriceLineSpec {
  id: string;
  price: number;
  color: string;
  title: string;
  style?: LineStyleName;
  width?: 1 | 2;
}

export interface DivergenceOverlay {
  id: string;
  side: string;
  color: string;
  pivots: [DivergencePivot, DivergencePivot];
  triggerLevel?: number;
  stopLoss?: number;
  takeProfit?: number;
  label: string;
}

/** lightweight-charts LineStyle enum values (Solid=0, Dotted=1, Dashed=2) without importing the runtime enum. */
const LINE_STYLE: Record<LineStyleName, LineStyle> = { solid: 0 as LineStyle, dotted: 1 as LineStyle, dashed: 2 as LineStyle };

/** Price lines for every open position on this symbol: entry (strategy colour), SL, TP, liq. */
export function positionPriceLines(positions: PositionData[], symbol: string): PriceLineSpec[] {
  const out: PriceLineSpec[] = [];
  positions.forEach((p, i) => {
    if (p.symbol !== symbol || !(p.entry_price > 0)) return;
    const long = isLong(p.side);
    const key = `${p.strategy ?? "pos"}-${i}`;
    const color = STRATEGY_COLORS[p.strategy ?? ""] ?? (long ? COLOR_UP : COLOR_DOWN);
    out.push({ id: `entry-${key}`, price: p.entry_price, color, title: `${long ? "L" : "S"} entry`, style: "solid", width: 1 });
    if (typeof p.stop_loss === "number" && p.stop_loss > 0) {
      out.push({ id: `sl-${key}`, price: p.stop_loss, color: COLOR_DOWN, title: "SL", style: "dashed" });
    }
    if (typeof p.take_profit === "number" && p.take_profit > 0) {
      out.push({ id: `tp-${key}`, price: p.take_profit, color: COLOR_UP, title: "TP", style: "dashed" });
    }
    const liq = positionLiquidation(p);
    if (liq.price && liq.price > 0) {
      out.push({ id: `liq-${key}`, price: liq.price, color: "#F5B942", title: liq.estimated ? "Liq (est.)" : "Liq", style: "dotted" });
    }
  });
  return out;
}

function toPivot(v: unknown): DivergencePivot | null {
  if (!v || typeof v !== "object") return null;
  const o = v as Record<string, unknown>;
  const ts = num(o.ts ?? o.time ?? o.timestamp);
  const price = num(o.price ?? o.px);
  if (ts === null || price === null || price <= 0) return null;
  const rsi = num(o.rsi);
  return { ts: ts > 1e11 ? ts / 1000 : ts, price, rsi: rsi ?? undefined };
}

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/** Tolerant reader: pivots as `[p1, p2]` or `{p1,p2}` / `{first,second}`; trigger as `trigger_level` or `trigger_price`. */
export function readDivergence(meta: SignalMetadata | null | undefined): { pivots: [DivergencePivot, DivergencePivot]; trigger: number | null; type: string; rsiGap: number | null } | null {
  if (!meta) return null;
  const raw = meta.pivots;
  let p1: DivergencePivot | null = null;
  let p2: DivergencePivot | null = null;
  if (Array.isArray(raw)) {
    p1 = toPivot(raw[0]);
    p2 = toPivot(raw[1]);
  } else if (raw && typeof raw === "object") {
    p1 = toPivot(raw.p1 ?? raw.first);
    p2 = toPivot(raw.p2 ?? raw.second);
  }
  if (!p1 || !p2) return null;
  if (p1.ts > p2.ts) [p1, p2] = [p2, p1];
  const trigger = num(meta.trigger_level) ?? num(meta.trigger_price);
  const type = String(meta.divergence_type ?? meta.type ?? "regular");
  return { pivots: [p1, p2], trigger, type, rsiGap: num(meta.rsi_gap) };
}

/** Divergence overlays for this symbol from the recent signal feed (newest signal per side wins). */
export function divergenceOverlays(signals: SignalData[], symbol: string): DivergenceOverlay[] {
  const out: DivergenceOverlay[] = [];
  const seen = new Set<string>();
  for (let i = signals.length - 1; i >= 0; i--) {
    const s = signals[i];
    if (s.symbol !== symbol || s.strategy !== "DIVERGENCE") continue;
    const d = readDivergence(s.metadata);
    if (!d) continue;
    const key = `${s.side}-${d.pivots[0].ts}-${d.pivots[1].ts}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const long = isLong(s.side);
    out.push({
      id: `div-${key}`,
      side: s.side,
      color: STRATEGY_COLORS.DIVERGENCE,
      pivots: d.pivots,
      triggerLevel: d.trigger ?? undefined,
      stopLoss: s.stop_loss > 0 ? s.stop_loss : undefined,
      takeProfit: s.take_profit > 0 ? s.take_profit : undefined,
      label: `${d.type} ${long ? "bullish" : "bearish"}`,
    });
    if (out.length >= 4) break;
  }
  return out;
}

// ── Appliers ────────────────────────────────────────────────────────

export function applyPriceLines(series: ISeriesApi<"Candlestick">, refs: Map<string, IPriceLine>, specs: PriceLineSpec[]) {
  for (const [, line] of refs) {
    try { series.removePriceLine(line); } catch { /* disposed */ }
  }
  refs.clear();
  for (const s of specs) {
    if (!(s.price > 0)) continue;
    const line = series.createPriceLine({
      price: s.price,
      color: s.color,
      lineWidth: s.width ?? 1,
      lineStyle: LINE_STYLE[s.style ?? "solid"],
      axisLabelVisible: true,
      title: s.title,
    });
    refs.set(s.id, line);
  }
}

export interface OverlayRefs {
  series: ISeriesApi<"Line">[];
}

/**
 * Draw each divergence: a dashed segment between the two price pivots, circle markers on the
 * pivots, and a dotted trigger-level line. A divergence whose first pivot predates the candle
 * history is skipped (its line would stretch the time axis to nothing), the trigger still draws.
 */
export function applyOverlays(chart: IChartApi, refs: OverlayRefs, overlays: DivergenceOverlay[], tfSeconds: number, firstCandleTime: number) {
  for (const s of refs.series) {
    try { chart.removeSeries(s); } catch { /* disposed */ }
  }
  refs.series = [];
  for (const o of overlays) {
    const t1 = Math.floor(o.pivots[0].ts / tfSeconds) * tfSeconds;
    const t2 = Math.floor(o.pivots[1].ts / tfSeconds) * tfSeconds;
    const inRange = t1 >= firstCandleTime - tfSeconds;
    const line = chart.addLineSeries({
      color: o.color,
      lineWidth: 1,
      lineStyle: LINE_STYLE.dashed,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
      title: "",
    });
    if (inRange && t2 > t1) {
      line.setData([
        { time: t1 as UTCTimestamp, value: o.pivots[0].price },
        { time: t2 as UTCTimestamp, value: o.pivots[1].price },
      ]);
      const long = isLong(o.side);
      const markers: SeriesMarker<Time>[] = [
        { time: t1 as UTCTimestamp, position: long ? "belowBar" : "aboveBar", color: o.color, shape: "circle", text: `P1${o.pivots[0].rsi != null ? ` RSI ${o.pivots[0].rsi.toFixed(0)}` : ""}` },
        { time: t2 as UTCTimestamp, position: long ? "belowBar" : "aboveBar", color: o.color, shape: "circle", text: `P2${o.pivots[1].rsi != null ? ` RSI ${o.pivots[1].rsi.toFixed(0)}` : ""}` },
      ];
      line.setMarkers(markers);
    } else if (t2 > 0) {
      // keep the series alive for the trigger line, anchored on the last pivot only
      line.setData([{ time: t2 as UTCTimestamp, value: o.pivots[1].price }]);
    }
    if (o.triggerLevel && o.triggerLevel > 0) {
      line.createPriceLine({ price: o.triggerLevel, color: o.color, lineWidth: 1, lineStyle: LINE_STYLE.dotted, axisLabelVisible: true, title: `trigger ${o.label}` });
    }
    refs.series.push(line);
  }
}
