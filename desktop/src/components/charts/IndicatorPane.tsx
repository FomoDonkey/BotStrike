import { useEffect, useRef, useState } from "react";
import type { IChartApi, ISeriesApi, LogicalRange, MouseEventParams, UTCTimestamp } from "lightweight-charts";
import { useMarketStore, type Candle } from "@/stores/marketStore";
import { macd, resampleCandles, rsi } from "@/lib/indicators";
import { CHART_THEME, TF_SECONDS, type Timeframe } from "./chartConfig";

export type IndicatorKind = "rsi" | "macd";

interface IndicatorPaneProps {
  symbol: string;
  timeframe: Timeframe;
  kind: IndicatorKind;
  /** The main candlestick chart — time scale and crosshair are kept in sync both ways */
  mainChart: IChartApi | null;
  className?: string;
}

const LINE_COLOR = "#38BDF8";
const SIGNAL_COLOR = "#F59E0B";
const HIST_UP = "rgba(0,212,170,0.55)";
const HIST_DOWN = "rgba(244,63,94,0.55)";

interface PaneSeries {
  a: ISeriesApi<"Line"> | null;
  b: ISeriesApi<"Line"> | null;
  h: ISeriesApi<"Histogram"> | null;
}

/**
 * Second lightweight-charts instance under the price chart (RSI 14 or MACD 12/26/9), synced with
 * the main chart: same right-scale width, mirrored visible logical range and crosshair.
 * The legend is written straight to the DOM (no state) so a candle burst never re-renders React.
 */
export function IndicatorPane({ symbol, timeframe, kind, mainChart, className }: IndicatorPaneProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const legendRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<PaneSeries>({ a: null, b: null, h: null });
  /** time → value of the primary line, for crosshair sync */
  const valuesRef = useRef<Map<number, number>>(new Map());
  const [ready, setReady] = useState(false);
  const tfSeconds = TF_SECONDS[timeframe];

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
          rightPriceScale: { borderColor: CHART_THEME.border, scaleMargins: { top: 0.15, bottom: 0.08 }, minimumWidth: CHART_THEME.priceScaleWidth },
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
      seriesRef.current = { a: null, b: null, h: null };
    };
  }, []);

  // (re)build series for the indicator kind and feed data from the store
  useEffect(() => {
    const chart = chartRef.current;
    if (!ready || !chart) return;
    const prev = seriesRef.current;
    for (const s of [prev.a, prev.b, prev.h]) {
      if (s) { try { chart.removeSeries(s); } catch { /* disposed */ } }
    }
    const cur: PaneSeries = { a: null, b: null, h: null };
    seriesRef.current = cur;
    valuesRef.current = new Map();

    if (kind === "rsi") {
      cur.a = chart.addLineSeries({
        color: LINE_COLOR, lineWidth: 1, priceLineVisible: false, lastValueVisible: true, crosshairMarkerVisible: false,
        priceFormat: { type: "price", precision: 1, minMove: 0.1 },
        // fixed 0–100 scale like every RSI pane
        autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 100 } }),
      });
      cur.a.createPriceLine({ price: 70, color: "rgba(244,63,94,0.45)", lineWidth: 1, lineStyle: 1, axisLabelVisible: false, title: "" });
      cur.a.createPriceLine({ price: 30, color: "rgba(0,212,170,0.45)", lineWidth: 1, lineStyle: 1, axisLabelVisible: false, title: "" });
    } else {
      cur.h = chart.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false, priceFormat: { type: "price", precision: 2, minMove: 0.01 } });
      cur.a = chart.addLineSeries({ color: LINE_COLOR, lineWidth: 1, priceLineVisible: false, lastValueVisible: true, crosshairMarkerVisible: false });
      cur.b = chart.addLineSeries({ color: SIGNAL_COLOR, lineWidth: 1, priceLineVisible: false, lastValueVisible: true, crosshairMarkerVisible: false });
    }

    const setLegend = (text: string) => { if (legendRef.current) legendRef.current.textContent = text; };
    let lastRef: Candle[] | undefined;
    let lastHash = "";
    const feed = () => {
      const raw = useMarketStore.getState().candles[symbol];
      if (!raw?.length) return;
      const candles = tfSeconds > 60 ? resampleCandles(raw, tfSeconds) : raw;
      const last = candles[candles.length - 1];
      const hash = `${candles.length}_${last.time}_${last.close}`;
      if (hash === lastHash) return;
      lastHash = hash;
      try {
        const values = new Map<number, number>();
        if (kind === "rsi" && cur.a) {
          const pts = rsi(candles, 14);
          cur.a.setData(pts.map((p) => ({ time: p.time as UTCTimestamp, value: p.value })));
          for (const p of pts) values.set(p.time, p.value);
          const v = pts.length ? pts[pts.length - 1].value : NaN;
          setLegend(`RSI 14   ${Number.isFinite(v) ? v.toFixed(1) : "---"}`);
        } else if (kind === "macd" && cur.h && cur.a && cur.b) {
          const pts = macd(candles, 12, 26, 9);
          cur.h.setData(pts.map((p) => ({ time: p.time as UTCTimestamp, value: p.hist, color: p.hist >= 0 ? HIST_UP : HIST_DOWN })));
          cur.a.setData(pts.map((p) => ({ time: p.time as UTCTimestamp, value: p.macd })));
          cur.b.setData(pts.map((p) => ({ time: p.time as UTCTimestamp, value: p.signal })));
          for (const p of pts) values.set(p.time, p.macd);
          const l = pts[pts.length - 1];
          setLegend(l ? `MACD 12 26 close 9   ${l.macd.toFixed(2)}   ${l.signal.toFixed(2)}   ${l.hist.toFixed(2)}` : "MACD 12 26 close 9");
        }
        valuesRef.current = values;
      } catch (e) {
        console.error("[IndicatorPane] data error:", e);
      }
    };
    const unsub = useMarketStore.subscribe((state) => {
      const c = state.candles[symbol];
      if (c !== lastRef) { lastRef = c; feed(); }
    });
    feed();
    return () => { unsub(); };
  }, [ready, kind, symbol, tfSeconds]);

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
      const s = seriesRef.current.a;
      if (!s) return;
      try {
        if (param.time === undefined) { sub.clearCrosshairPosition(); return; }
        const t = param.time as UTCTimestamp;
        const v = valuesRef.current.get(t as number);
        if (v === undefined) { sub.clearCrosshairPosition(); return; }
        sub.setCrosshairPosition(v, t, s);
      } catch { /* series without that time */ }
    };
    mainChart.subscribeCrosshairMove(onMainCrosshair);
    return () => {
      try { mainChart.timeScale().unsubscribeVisibleLogicalRangeChange(fromMain); } catch { /* disposed */ }
      try { sub.timeScale().unsubscribeVisibleLogicalRangeChange(fromSub); } catch { /* disposed */ }
      try { mainChart.unsubscribeCrosshairMove(onMainCrosshair); } catch { /* disposed */ }
    };
  }, [ready, mainChart]);

  return (
    <div className={className} style={{ position: "relative", width: "100%", height: "100%" }}>
      <div ref={containerRef} style={{ position: "absolute", inset: 0 }} />
      <div ref={legendRef} className="absolute left-2 top-1 z-[2] text-[10.5px] font-mono text-text-muted pointer-events-none select-none whitespace-pre">
        {kind === "rsi" ? "RSI 14" : "MACD 12 26 close 9"}
      </div>
    </div>
  );
}
