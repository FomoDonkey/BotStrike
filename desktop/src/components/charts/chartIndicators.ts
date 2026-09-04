// The indicator catalogue: what the chart can draw, computed with the engine's own definitions.
//
// The engine derives twenty-one columns for every bar it trades on (core/indicators.py,
// `Indicators.ALL_COLUMNS`). Until 2026-09-04 the chart offered two of them — RSI or MACD, one at
// a time — and not a single line on the price itself, on a book whose live strategy is a Donchian
// channel. Every entry here names the engine column it reproduces, so what the operator sees is
// what the bot computes, at the same parameters (mr_lookback = 100 for the z-score, 14 for the
// Wilder family, 20/50 for the means, 20 · 2σ for the bands).
import type { Candle } from "@/stores/marketStore";
import * as I from "@/lib/indicators";
import { COLOR_BLUE, COLOR_DOWN, COLOR_UP, COLOR_AMBER } from "@/lib/constants";

export type IndicatorPlace = "overlay" | "pane";

export interface SeriesSpec {
  key: string;
  /** legend name — "SMA 20", "signal", "+DI" */
  name: string;
  color: string;
  kind: "line" | "histogram";
  width?: 1 | 2;
  style?: "solid" | "dashed" | "dotted";
  /** aligned to the candles; NaN where the indicator has no value yet */
  values: number[];
  /** histogram bar colour by value */
  colorOf?: (v: number) => string;
}

export interface PaneLevel {
  value: number;
  color: string;
}

export interface PaneScale {
  /** a fixed axis (RSI 0–100) rather than one that follows the visible bars */
  fixed?: [number, number];
  /** always keep zero on the axis (oscillators around zero) */
  includeZero?: boolean;
  levels?: PaneLevel[];
}

export interface IndicatorDef {
  id: string;
  /** full label for the menu — "Bollinger Bands 20 · 2σ" */
  label: string;
  /** compact legend title — "BB 20 2" */
  short: string;
  place: IndicatorPlace;
  group: string;
  /** the engine column(s) this reproduces */
  engine: string;
  /** legend decimals: a number, or derived from the price level (ATR, standard deviation) */
  precision: number | "price";
  /** values are fractions shown as per cent */
  pct?: boolean;
  scale?: PaneScale;
  compute: (candles: Candle[]) => SeriesSpec[];
}

// Three panes under the price is where a 900 px window stops being a chart (Strike opens with two).
export const MAX_PANES = 3;

const VIOLET = "#A78BFA";
const PINK = "#F472B6";
const CYAN = "#22D3EE";
const ORANGE = "#FF8A3D";
const GREY = "rgba(255,255,255,0.55)";
const WHITE = "rgba(255,255,255,0.88)";
const HIST_UP = "rgba(78,250,176,0.55)";
const HIST_DOWN = "rgba(244,63,94,0.55)";

const closes = (c: Candle[]) => c.map((k) => k.close);
const volumes = (c: Candle[]) => c.map((k) => k.volume);

/** Hide the seed transient of an EMA-type line: the engine's value exists from bar 0 (pandas
 *  seeds with the first close) but the first `n` bars only show the seed settling. */
function blankHead(values: number[], n: number): number[] {
  const out = values.slice();
  for (let i = 0; i < Math.min(n, out.length); i++) out[i] = NaN;
  return out;
}

const line = (key: string, name: string, color: string, values: number[], extra: Partial<SeriesSpec> = {}): SeriesSpec =>
  ({ key, name, color, kind: "line", width: 1, values, ...extra });

export const INDICATORS: IndicatorDef[] = [
  // ── price overlays ─────────────────────────────────────────────────────────
  {
    id: "sma20", label: "SMA 20", short: "SMA 20", place: "overlay", group: "Moving averages", engine: "sma_20", precision: "price",
    compute: (c) => [line("sma", "SMA 20", COLOR_AMBER, I.sma(closes(c), 20))],
  },
  {
    id: "sma50", label: "SMA 50", short: "SMA 50", place: "overlay", group: "Moving averages", engine: "sma_50", precision: "price",
    compute: (c) => [line("sma", "SMA 50", VIOLET, I.sma(closes(c), 50))],
  },
  {
    id: "ema12", label: "EMA 12", short: "EMA 12", place: "overlay", group: "Moving averages", engine: "ema_12", precision: "price",
    compute: (c) => [line("ema", "EMA 12", COLOR_BLUE, blankHead(I.ema(closes(c), 12), 12))],
  },
  {
    id: "ema26", label: "EMA 26", short: "EMA 26", place: "overlay", group: "Moving averages", engine: "ema_26", precision: "price",
    compute: (c) => [line("ema", "EMA 26", PINK, blankHead(I.ema(closes(c), 26), 26))],
  },
  {
    id: "bb20", label: "Bollinger 20 · 2σ", short: "BB 20 2", place: "overlay", group: "Channels", engine: "bb_upper · mid · lower", precision: "price",
    compute: (c) => {
      const b = I.bollinger(closes(c), 20, 2);
      return [
        line("upper", "upper", CYAN, b.upper),
        line("mid", "mid", CYAN, b.mid, { style: "dotted" }),
        line("lower", "lower", CYAN, b.lower),
      ];
    },
  },
  {
    id: "dc20", label: "Donchian Channel 20", short: "DC 20", place: "overlay", group: "Channels", engine: "high_20 · low_20", precision: "price",
    compute: (c) => {
      const d = I.donchian(c, 20);
      return [
        line("upper", "high", COLOR_UP, d.upper, { style: "dashed" }),
        line("mid", "mid", GREY, d.mid, { style: "dotted" }),
        line("lower", "low", COLOR_DOWN, d.lower, { style: "dashed" }),
      ];
    },
  },
  // ── panes ──────────────────────────────────────────────────────────────────
  {
    id: "macd", label: "MACD 12 · 26 · 9", short: "MACD 12 26 close 9", place: "pane", group: "Trend", engine: "ema_12 − ema_26", precision: "price",
    scale: { includeZero: true },
    compute: (c) => {
      const m = I.macd(closes(c), 12, 26, 9);
      return [
        { key: "hist", name: "hist", color: HIST_UP, kind: "histogram", values: blankHead(m.hist, 26), colorOf: (v) => (v >= 0 ? HIST_UP : HIST_DOWN) },
        line("macd", "MACD", COLOR_BLUE, blankHead(m.macd, 26)),
        line("signal", "signal", COLOR_AMBER, blankHead(m.signal, 26)),
      ];
    },
  },
  {
    id: "adx14", label: "ADX 14 · DI±", short: "ADX 14", place: "pane", group: "Trend", engine: "adx · plus_di · minus_di", precision: 1,
    scale: { levels: [{ value: 25, color: GREY }] },
    compute: (c) => {
      const a = I.adx(c, 14);
      return [
        line("adx", "ADX", WHITE, a.adx, { width: 2 }),
        line("plus", "+DI", COLOR_UP, a.plusDi),
        line("minus", "−DI", COLOR_DOWN, a.minusDi),
      ];
    },
  },
  {
    id: "rsi14", label: "RSI 14", short: "RSI 14", place: "pane", group: "Momentum", engine: "rsi", precision: 1,
    scale: { fixed: [0, 100], levels: [{ value: 70, color: "rgba(244,63,94,0.6)" }, { value: 30, color: "rgba(78,250,176,0.6)" }] },
    compute: (c) => [line("rsi", "RSI", COLOR_UP, blankHead(I.rsi(closes(c), 14), 14))],
  },
  {
    id: "momentum", label: "Momentum 10 · 20", short: "MOM 10 20", place: "pane", group: "Momentum", engine: "momentum_10 · _20", precision: 2, pct: true,
    scale: { includeZero: true, levels: [{ value: 0, color: GREY }] },
    compute: (c) => [
      line("m10", "10", COLOR_BLUE, I.momentum(closes(c), 10)),
      line("m20", "20", PINK, I.momentum(closes(c), 20)),
    ],
  },
  {
    id: "zscore", label: "Z-score 100", short: "Z 100", place: "pane", group: "Momentum", engine: "zscore · mr_lookback", precision: 2,
    scale: { includeZero: true, levels: [{ value: 2, color: "rgba(244,63,94,0.6)" }, { value: 0, color: GREY }, { value: -2, color: "rgba(78,250,176,0.6)" }] },
    compute: (c) => [line("z", "Z", VIOLET, I.zscore(closes(c), 100))],
  },
  {
    id: "atr14", label: "ATR 14", short: "ATR 14", place: "pane", group: "Volatility", engine: "atr", precision: "price",
    scale: { includeZero: true },
    compute: (c) => [line("atr", "ATR", COLOR_AMBER, blankHead(I.atr(c, 14), 14))],
  },
  {
    id: "std20", label: "Standard deviation 20", short: "STD 20", place: "pane", group: "Volatility", engine: "std_20", precision: "price",
    scale: { includeZero: true },
    compute: (c) => [line("std", "σ", CYAN, I.rollingStd(closes(c), 20, 2))],
  },
  {
    id: "volpct", label: "Volatility percentile 14 / 100", short: "VOL% 14 100", place: "pane", group: "Volatility", engine: "vol_pct", precision: 2,
    scale: { fixed: [0, 1], levels: [{ value: 0.8, color: "rgba(244,63,94,0.6)" }, { value: 0.2, color: "rgba(78,250,176,0.6)" }] },
    compute: (c) => [line("vp", "pct", ORANGE, I.volatilityPercentile(closes(c), 14, 100))],
  },
  {
    id: "volratio", label: "Volume ratio 20", short: "VOL/AVG 20", place: "pane", group: "Volume", engine: "vol_ratio", precision: 2,
    scale: { includeZero: true, levels: [{ value: 1, color: GREY }] },
    compute: (c) => [{ key: "vr", name: "ratio", color: CYAN, kind: "histogram", values: I.volumeRatio(volumes(c), 20), colorOf: (v) => (v >= 1 ? "rgba(34,211,238,0.7)" : "rgba(34,211,238,0.3)") }],
  },
];

export const INDICATOR_BY_ID: Record<string, IndicatorDef> = Object.fromEntries(INDICATORS.map((d) => [d.id, d]));
export const OVERLAY_DEFS = INDICATORS.filter((d) => d.place === "overlay");
export const PANE_DEFS = INDICATORS.filter((d) => d.place === "pane");

/** Decimals a price-denominated value needs at this price level (matches formatPrice). */
export function pricePrecision(price: number): number {
  if (price >= 1000) return 2;
  if (price >= 1) return 4;
  return 6;
}

export function indicatorPrecision(def: IndicatorDef, refPrice: number): number {
  return def.precision === "price" ? pricePrecision(refPrice) : def.precision;
}

export function formatIndicatorValue(def: IndicatorDef, v: number, refPrice: number): string {
  if (!Number.isFinite(v)) return "---";
  if (def.pct) return `${(v * 100).toFixed(def.precision === "price" ? 2 : def.precision)}%`;
  const p = indicatorPrecision(def, refPrice);
  return v >= 1000 || v <= -1000 ? v.toLocaleString("en-US", { minimumFractionDigits: p, maximumFractionDigits: p }) : v.toFixed(p);
}
