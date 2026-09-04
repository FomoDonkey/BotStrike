import { useEffect, useRef, useState } from "react";
import type { AutoscaleInfo, IChartApi, ISeriesApi, LogicalRange, MouseEventParams, UTCTimestamp } from "lightweight-charts";
import { X } from "lucide-react";
import { useMarketStore } from "@/stores/marketStore";
import { chartInputs, readChartCandles } from "@/lib/chartData";
import { CHART_THEME, type Timeframe } from "./chartConfig";
import { formatIndicatorValue, indicatorPrecision, type IndicatorDef, type SeriesSpec } from "./chartIndicators";

interface IndicatorPaneProps {
  symbol: string;
  timeframe: Timeframe;
  def: IndicatorDef;
  /** The main candlestick chart — time scale and crosshair are kept in sync both ways */
  mainChart: IChartApi | null;
  onRemove?: () => void;
  className?: string;
}

/** lightweight-charts LineStyle values (Solid=0, Dotted=1, Dashed=2) without the runtime enum. */
const LINE_STYLE = { solid: 0, dotted: 1, dashed: 2 } as const;

type PaneSeries =
  | { kind: "line"; api: ISeriesApi<"Line"> }
  | { kind: "histogram"; api: ISeriesApi<"Histogram"> };

/**
 * One indicator under the price chart — a second lightweight-charts instance synced with the
 * main one: same right-scale width, mirrored visible logical range, mirrored crosshair.
 *
 * The axis follows the bars ON SCREEN, computed here from the indicator's own values rather than
 * left to the library: the MACD pane used to run to −400 while every visible value sat between
 * 0 and 56, squashing the lines into the top fifth of the pane (2026-09-04). A fixed-scale
 * indicator (RSI) keeps its fixed scale. The legend is written straight to the DOM (no state)
 * so a candle burst never re-renders React, and it follows the crosshair like Strike's.
 */
export function IndicatorPane({ symbol, timeframe, def, mainChart, onRemove, className }: IndicatorPaneProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const legendRef = useRef<HTMLSpanElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<PaneSeries[]>([]);
  const specsRef = useRef<SeriesSpec[]>([]);
  /** time → index of the drawn bars, for the crosshair legend */
  const indexRef = useRef<Map<number, number>>(new Map());
  const countRef = useRef(0);
  const refPriceRef = useRef(0);
  const [ready, setReady] = useState(false);

  // create / destroy the chart
  useEffect(() => {
    if (!containerRef.current) return;
    let destroyed = false;
    let resizeObs: ResizeObserver | null = null;
    (async () => {
      try {
        const lc = await import("lightweight-charts");
        if (destroyed || !containerRef.current) return;
        const chart = lc.createChart(containerRef.current, {
          layout: { background: { type: lc.ColorType.Solid, color: "transparent" }, textColor: CHART_THEME.textColor, fontFamily: CHART_THEME.fontFamily, fontSize: CHART_THEME.fontSize },
          grid: { vertLines: { color: CHART_THEME.grid }, horzLines: { color: CHART_THEME.grid } },
          crosshair: {
            mode: lc.CrosshairMode.Normal,
            vertLine: { color: CHART_THEME.crosshair, width: 1, style: 2, labelBackgroundColor: CHART_THEME.labelBg },
            horzLine: { color: CHART_THEME.crosshair, width: 1, style: 2, labelBackgroundColor: CHART_THEME.labelBg },
          },
          rightPriceScale: { borderColor: CHART_THEME.border, scaleMargins: { top: 0.22, bottom: 0.08 }, minimumWidth: CHART_THEME.priceScaleWidth },
          timeScale: { borderColor: CHART_THEME.border, timeVisible: true, secondsVisible: false, rightOffset: 5, barSpacing: 6, visible: false },
          handleScroll: { vertTouchDrag: false },
        });
        chartRef.current = chart;
        resizeObs = new ResizeObserver((entries) => {
          if (destroyed) return;
          const { width, height } = entries[0].contentRect;
          if (width > 0 && height > 0) chart.applyOptions({ width, height });
        });
        resizeObs.observe(containerRef.current);
        setReady(true);
      } catch (e) {
        console.error("[IndicatorPane] init error:", e);
      }
    })();
    return () => {
      destroyed = true;
      resizeObs?.disconnect();
      if (chartRef.current) {
        chartRef.current.remove();
        chartRef.current = null;
      }
      seriesRef.current = [];
    };
  }, []);

  // (re)build the series for this indicator and feed them from the store
  useEffect(() => {
    const chart = chartRef.current;
    if (!ready || !chart) return;
    for (const s of seriesRef.current) {
      try { chart.removeSeries(s.api); } catch { /* disposed */ }
    }
    seriesRef.current = [];
    indexRef.current = new Map();
    countRef.current = 0;

    // The axis: fixed for a bounded oscillator, otherwise the min/max of every series over the
    // bars currently visible (plus zero when the indicator lives around it).
    const autoscale = (original: () => AutoscaleInfo | null): AutoscaleInfo | null => {
      try {
        const fixed = def.scale?.fixed;
        if (fixed) return { priceRange: { minValue: fixed[0], maxValue: fixed[1] } };
        const n = countRef.current;
        if (!n) return original();
        const vis = chart.timeScale().getVisibleLogicalRange();
        const from = vis ? Math.max(0, Math.floor(vis.from)) : 0;
        const to = vis ? Math.min(n - 1, Math.ceil(vis.to)) : n - 1;
        let lo = Infinity;
        let hi = -Infinity;
        for (const spec of specsRef.current) {
          const v = spec.values;
          for (let i = from; i <= to; i++) {
            const x = v[i];
            if (!Number.isFinite(x)) continue;
            if (x < lo) lo = x;
            if (x > hi) hi = x;
          }
        }
        if (def.scale?.includeZero) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
        if (!Number.isFinite(lo) || !Number.isFinite(hi)) return original();
        if (hi === lo) {
          const p = Math.abs(hi) * 0.05 || 1;
          return { priceRange: { minValue: lo - p, maxValue: hi + p } };
        }
        const pad = (hi - lo) * 0.08;
        return { priceRange: { minValue: lo - pad, maxValue: hi + pad } };
      } catch {
        return original();
      }
    };
    const priceFormat = () => {
      const p = indicatorPrecision(def, refPriceRef.current);
      return { type: "custom" as const, formatter: (v: number) => formatIndicatorValue(def, v, refPriceRef.current), minMove: Math.pow(10, -p) };
    };

    const specs = def.compute([]);
    specsRef.current = specs;
    const created: PaneSeries[] = specs.map((spec) => {
      const common = { priceLineVisible: false, lastValueVisible: true, priceFormat: priceFormat(), autoscaleInfoProvider: autoscale };
      if (spec.kind === "histogram") {
        return { kind: "histogram", api: chart.addHistogramSeries({ ...common, color: spec.color, lastValueVisible: false }) };
      }
      return {
        kind: "line",
        api: chart.addLineSeries({ ...common, color: spec.color, lineWidth: spec.width ?? 1, lineStyle: LINE_STYLE[spec.style ?? "solid"], crosshairMarkerVisible: false }),
      };
    });
    seriesRef.current = created;
    const anchor = created[0]?.api;
    if (anchor) {
      for (const lv of def.scale?.levels ?? []) {
        anchor.createPriceLine({ price: lv.value, color: lv.color, lineWidth: 1, lineStyle: 1, axisLabelVisible: false, title: "" });
      }
    }

    const writeLegend = (idx: number | null) => {
      const el = legendRef.current;
      if (!el) return;
      const n = countRef.current;
      const i = idx ?? n - 1;
      const frag = document.createDocumentFragment();
      specsRef.current.forEach((spec, k) => {
        const v = i >= 0 ? spec.values[i] : NaN;
        const span = document.createElement("span");
        span.style.color = spec.kind === "histogram" && spec.colorOf && Number.isFinite(v) ? spec.colorOf(v) : spec.color;
        span.style.marginLeft = k ? "10px" : "8px";
        span.textContent = formatIndicatorValue(def, v, refPriceRef.current);
        span.title = spec.name;
        frag.append(span);
      });
      el.replaceChildren(frag);
    };

    let lastHash = "";
    let lastPrecision = -1;
    const feed = () => {
      const { candles } = readChartCandles(useMarketStore.getState(), symbol, timeframe);
      if (!candles.length) return;
      const last = candles[candles.length - 1];
      const hash = `${candles.length}_${last.time}_${last.close}_${last.high}_${last.low}`;
      if (hash === lastHash) return;
      lastHash = hash;
      try {
        refPriceRef.current = last.close;
        const p = indicatorPrecision(def, last.close);
        if (p !== lastPrecision) {
          lastPrecision = p;
          for (const s of seriesRef.current) s.api.applyOptions({ priceFormat: priceFormat() });
        }
        const computed = def.compute(candles);
        specsRef.current = computed;
        // one point per candle, whitespace where the indicator has no value: the main chart and
        // this pane are synced by LOGICAL index, so the pane must not be shifted by the warm-up
        computed.forEach((spec, k) => {
          const s = seriesRef.current[k];
          if (!s) return;
          if (s.kind === "histogram") {
            s.api.setData(candles.map((c, i) => {
              const v = spec.values[i];
              return Number.isFinite(v)
                ? { time: c.time as UTCTimestamp, value: v, color: spec.colorOf ? spec.colorOf(v) : spec.color }
                : { time: c.time as UTCTimestamp };
            }));
          } else {
            s.api.setData(candles.map((c, i) => {
              const v = spec.values[i];
              return Number.isFinite(v) ? { time: c.time as UTCTimestamp, value: v } : { time: c.time as UTCTimestamp };
            }));
          }
        });
        const index = new Map<number, number>();
        candles.forEach((c, i) => index.set(c.time, i));
        indexRef.current = index;
        countRef.current = candles.length;
        writeLegend(null);
      } catch (e) {
        console.error("[IndicatorPane] data error:", e);
      }
    };
    let lastInputs: unknown[] = [];
    const unsub = useMarketStore.subscribe((state) => {
      const inputs = chartInputs(state, symbol, timeframe);
      if (inputs[0] !== lastInputs[0] || inputs[1] !== lastInputs[1]) { lastInputs = inputs; feed(); }
    });
    feed();

    // the legend follows the main chart's crosshair, and returns to the last bar when it leaves
    const onMainCrosshair = (param: MouseEventParams) => {
      const t = typeof param.time === "number" ? param.time : null;
      const idx = t !== null ? indexRef.current.get(t) : undefined;
      writeLegend(idx === undefined ? null : idx);
    };
    mainChart?.subscribeCrosshairMove(onMainCrosshair);
    return () => {
      unsub();
      try { mainChart?.unsubscribeCrosshairMove(onMainCrosshair); } catch { /* disposed */ }
    };
  }, [ready, def, symbol, timeframe, mainChart]);

  // time-scale + crosshair sync with the main chart (both directions, loop-guarded)
  useEffect(() => {
    const sub = chartRef.current;
    if (!ready || !sub || !mainChart) return;
    let syncing = false;
    const mirror = (to: IChartApi) => (range: LogicalRange | null) => {
      if (syncing || !range) return;
      syncing = true;
      try { to.timeScale().setVisibleLogicalRange(range); } catch { /* disposed */ }
      syncing = false;
    };
    const fromMain = mirror(sub);
    const fromSub = mirror(mainChart);
    mainChart.timeScale().subscribeVisibleLogicalRangeChange(fromMain);
    sub.timeScale().subscribeVisibleLogicalRangeChange(fromSub);
    const initial = mainChart.timeScale().getVisibleLogicalRange();
    if (initial) fromMain(initial);

    const onMainCrosshair = (param: MouseEventParams) => {
      const s = seriesRef.current.find((x) => x.kind === "line") ?? seriesRef.current[0];
      if (!s) return;
      try {
        if (param.time === undefined) { sub.clearCrosshairPosition(); return; }
        const t = param.time as UTCTimestamp;
        const idx = indexRef.current.get(t as number);
        const k = seriesRef.current.indexOf(s);
        const v = idx === undefined ? NaN : specsRef.current[k]?.values[idx];
        if (!Number.isFinite(v)) { sub.clearCrosshairPosition(); return; }
        sub.setCrosshairPosition(v, t, s.api);
      } catch { /* series without that time */ }
    };
    mainChart.subscribeCrosshairMove(onMainCrosshair);
    return () => {
      try { mainChart.timeScale().unsubscribeVisibleLogicalRangeChange(fromMain); } catch { /* disposed */ }
      try { sub.timeScale().unsubscribeVisibleLogicalRangeChange(fromSub); } catch { /* disposed */ }
      try { mainChart.unsubscribeCrosshairMove(onMainCrosshair); } catch { /* disposed */ }
    };
  }, [ready, mainChart]);

  // absolute inset-0: the parent block is sized by min-height on mobile and a percentage
  // height never resolves against that (tasks/lessons.md) → the ResizeObserver saw 0 px and
  // the pane stayed blank at 390 px. The parent is position: relative.
  return (
    <div className={className} style={{ position: "absolute", inset: 0 }}>
      <div ref={containerRef} style={{ position: "absolute", inset: 0 }} />
      <div className="absolute left-2 top-1 z-[2] flex items-center text-[11px] font-medium num text-text pointer-events-none select-none whitespace-pre" title={`engine: ${def.engine}`}>
        <span className="font-semibold">{def.short}</span>
        <span ref={legendRef} />
      </div>
      {onRemove && (
        <button
          type="button"
          onClick={onRemove}
          title={`Remove ${def.label}`}
          aria-label={`Remove ${def.label}`}
          className="absolute top-0.5 z-[2] inline-flex items-center justify-center w-5 h-5 rounded-[4px] text-text-2 hover:text-text hover:bg-hover transition-colors"
          style={{ right: CHART_THEME.priceScaleWidth + 6 }}
        >
          <X className="w-3 h-3" />
        </button>
      )}
    </div>
  );
}
