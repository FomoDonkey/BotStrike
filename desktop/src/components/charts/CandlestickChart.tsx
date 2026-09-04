import { useEffect, useRef, useState, useCallback, type RefObject } from "react";
import type { IChartApi, IPriceLine, ISeriesApi, MouseEventParams, SeriesMarker, Time, UTCTimestamp } from "lightweight-charts";
import { useMarketStore, type Candle } from "@/stores/marketStore";
import { type TradeData } from "@/stores/tradingStore";
import { chartInputs, readChartCandles } from "@/lib/chartData";
import { COLOR_DOWN, COLOR_UP } from "@/lib/constants";
import { formatPrice } from "@/lib/utils";
import { applyOverlays, applyPriceLines, type DivergenceOverlay, type OverlayRefs, type PriceLineSpec } from "./chartOverlays";
import type { AutoscaleInfo } from "lightweight-charts";
import { CHART_THEME, TF_SECONDS, type Timeframe } from "./chartConfig";
import { formatIndicatorValue, type IndicatorDef, type SeriesSpec } from "./chartIndicators";

export type { Timeframe };

function hexToRgba(hex: string, alpha: number): string {
  const h = hex.replace("#", "");
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

/** "O 77,120.50  H 77,210.00  L 77,050.00  C 77,180.25  +0.08%" — written straight to the DOM. */
function legendText(c: Candle): string {
  const d = c.open > 0 ? (c.close - c.open) / c.open : 0;
  const sign = d > 0 ? "+" : "";
  return `O ${formatPrice(c.open)}   H ${formatPrice(c.high)}   L ${formatPrice(c.low)}   C ${formatPrice(c.close)}   ${sign}${(d * 100).toFixed(2)}%`;
}

/** lightweight-charts LineStyle values (Solid=0, Dotted=1, Dashed=2) without the runtime enum. */
const LINE_STYLE = { solid: 0, dotted: 1, dashed: 2 } as const;

interface OverlayLayer {
  def: IndicatorDef;
  specs: SeriesSpec[];
  series: ISeriesApi<"Line">[];
}

interface CandlestickChartProps {
  symbol: string;
  className?: string;
  trades?: TradeData[];
  timeframe?: Timeframe;
  /** Live levels of open positions (entry / SL / TP / liq) */
  priceLines?: PriceLineSpec[];
  /** DIVERGENCE signals with pivots (contract §5) */
  overlays?: DivergenceOverlay[];
  /** Indicators drawn ON the price (moving averages, bands, channels) — computed here from the
   *  very bars the candles are drawn from, so the two can never disagree. */
  overlayDefs?: IndicatorDef[];
  /** Exposes the chart API (time-scale / crosshair sync with the indicator panes) */
  onChart?: (chart: IChartApi | null) => void;
  /** Candle colours (colour-blind mode swaps them) */
  upColor?: string;
  downColor?: string;
  /** OHLC legend element — updated from the crosshair without React state */
  legendRef?: RefObject<HTMLElement | null>;
  /** Overlay legend element — "SMA 20 79,701.20 · BB 20 2 …", coloured per series */
  overlayLegendRef?: RefObject<HTMLElement | null>;
  /** How many legend rows sit over the top-left of the plot: the candles are kept below them */
  legendRows?: number;
}

export function CandlestickChart({ symbol, className, trades, timeframe = "1m", priceLines, overlays, overlayDefs, onChart, upColor = COLOR_UP, downColor = COLOR_DOWN, legendRef, overlayLegendRef, legendRows = 1 }: CandlestickChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const priceLineRefs = useRef<Map<string, IPriceLine>>(new Map());
  const overlayRefs = useRef<OverlayRefs>({ series: [] });
  const layersRef = useRef<OverlayLayer[]>([]);
  const firstTimeRef = useRef(0);
  /** first loaded candle time as STATE so effects that depend on the history window re-run when it loads */
  const [historyStart, setHistoryStart] = useState(0);
  const lastCandleHash = useRef("");
  const lastMarkersHash = useRef("");
  const lastLinesHash = useRef("");
  const lastOverlayHash = useRef("");
  const lastCandleCount = useRef(0);
  const drawnRef = useRef<Candle[]>([]);
  /** time → index of the drawn candles (legend lookups) */
  const indexRef = useRef<Map<number, number>>(new Map());
  const heightRef = useRef(0);
  const legendRowsRef = useRef(legendRows);
  const [error, setError] = useState<string | null>(null);
  const [chartReady, setChartReady] = useState(false);
  const onChartRef = useRef(onChart);
  useEffect(() => { onChartRef.current = onChart; });
  const colorsRef = useRef({ up: upColor, down: downColor });
  useEffect(() => { colorsRef.current = { up: upColor, down: downColor }; });

  /** OHLC legend for a bar index (or the last bar), coloured by the bar's direction like Strike's */
  const writeLegend = useCallback((idx: number | null) => {
    const el = legendRef?.current;
    if (!el) return;
    const c = drawnRef.current;
    const k = idx ?? c.length - 1;
    const bar = c[k];
    if (!bar) { el.textContent = ""; return; }
    el.textContent = legendText(bar);
    el.style.color = bar.close >= bar.open ? colorsRef.current.up : colorsRef.current.down;
  }, [legendRef]);

  /** Overlay legend: title in white, one coloured value per series, at the bar index (or the last bar) */
  const writeOverlayLegend = useCallback((idx: number | null) => {
    const el = overlayLegendRef?.current;
    if (!el) return;
    const layers = layersRef.current;
    const c = drawnRef.current;
    if (!layers.length || !c.length) { el.replaceChildren(); return; }
    const i = idx ?? c.length - 1;
    const ref = c[c.length - 1].close;
    const frag = document.createDocumentFragment();
    layers.forEach((layer, li) => {
      const title = document.createElement("span");
      title.textContent = `${li ? "   " : ""}${layer.def.short}`;
      title.className = "font-semibold";
      title.title = `engine: ${layer.def.engine}`;
      frag.append(title);
      for (const spec of layer.specs) {
        const span = document.createElement("span");
        span.style.color = spec.color;
        span.style.marginLeft = "6px";
        span.textContent = formatIndicatorValue(layer.def, spec.values[i] ?? NaN, ref);
        span.title = spec.name;
        frag.append(span);
      }
    });
    el.replaceChildren(frag);
  }, [overlayLegendRef]);

  /** The candles stay below the legend rows: top margin from the rows' height over the pane's. */
  const applyMargins = useCallback(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const h = heightRef.current || 300;
    const top = Math.min(0.3, Math.max(0.06, (legendRowsRef.current * 15 + 12) / h));
    try { chart.priceScale("right").applyOptions({ scaleMargins: { top, bottom: 0.18 } }); } catch { /* disposed */ }
  }, []);

  // Step 1: Initialize chart (async)
  useEffect(() => {
    if (!containerRef.current) return;
    let destroyed = false;
    let resizeObs: ResizeObserver | null = null;
    const lineRefs = priceLineRefs.current;
    const ovRefs = overlayRefs.current;

    (async () => {
      try {
        const lc = await import("lightweight-charts");
        if (destroyed || !containerRef.current) return;

        const chart = lc.createChart(containerRef.current, {
          layout: {
            background: { type: lc.ColorType.Solid, color: "transparent" },
            textColor: CHART_THEME.textColor,
            fontFamily: CHART_THEME.fontFamily,
            fontSize: CHART_THEME.fontSize,
          },
          grid: {
            vertLines: { color: CHART_THEME.grid },
            horzLines: { color: CHART_THEME.grid },
          },
          crosshair: {
            mode: lc.CrosshairMode.Normal,
            vertLine: { color: CHART_THEME.crosshair, width: 1, style: 2, labelBackgroundColor: CHART_THEME.labelBg },
            horzLine: { color: CHART_THEME.crosshair, width: 1, style: 2, labelBackgroundColor: CHART_THEME.labelBg },
          },
          rightPriceScale: {
            borderColor: CHART_THEME.border,
            scaleMargins: { top: 0.12, bottom: 0.18 },
            minimumWidth: CHART_THEME.priceScaleWidth,
          },
          // lightweight-charts labels the axis in UTC, while every table on these screens uses the
          // reader's local clock: the same fill read 19:07 on the chart and 21:07 in Trade History
          // (audit 2026-09-03). One clock, the reader's, everywhere. And one number format: the
          // axis read "80400.00" beside a header that reads "80,400.00".
          localization: {
            priceFormatter: (p: number) => formatPrice(p),
            timeFormatter: (t: number) =>
              new Date(t * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }),
          },
          timeScale: {
            borderColor: CHART_THEME.border,
            timeVisible: true,
            secondsVisible: false,
            rightOffset: 5,         // Space after last candle
            barSpacing: 6,          // Compact bars to show more candles
            fixLeftEdge: false,
            fixRightEdge: false,
            tickMarkFormatter: (t: number, tickType: number) => {
              const d = new Date(t * 1000);
              // 0 Year · 1 Month · 2 DayOfMonth · 3 Time · 4 TimeWithSeconds
              if (tickType <= 2) return d.toLocaleDateString([], { month: "short", day: "numeric" });
              return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
            },
          },
          handleScroll: { vertTouchDrag: false },
        });

        const candleSeries = chart.addCandlestickSeries({
          upColor: colorsRef.current.up,
          downColor: colorsRef.current.down,
          borderUpColor: colorsRef.current.up,
          borderDownColor: colorsRef.current.down,
          wickUpColor: colorsRef.current.up,
          wickDownColor: colorsRef.current.down,
          // THE PRICE SCALE BELONGS TO THE PRICE ACTION, NOT TO THE LINES DRAWN OVER IT.
          //
          // An exit ladder sits several percent below a trend position and lightweight-charts
          // includes price lines in its autoscale, so on silver — whose candles moved 0.5 % across
          // the session — the entry line pinned the top of the axis at 67.25 and squashed every
          // candle into an unreadable sliver. That is the "weird candles" (Edgar, 2026-09-04).
          // Set here rather than in an effect: an effect keyed on chartReady ran before the series
          // existed and the option was silently never applied.
          autoscaleInfoProvider: (original: () => AutoscaleInfo | null): AutoscaleInfo | null => {
            try {
              const c = drawnRef.current;
              if (!c.length) return original();
              const vis = chart.timeScale().getVisibleLogicalRange();
              const from = vis ? Math.max(0, Math.floor(vis.from)) : 0;
              const to = vis ? Math.min(c.length - 1, Math.ceil(vis.to)) : c.length - 1;
              let lo = Infinity;
              let hi = -Infinity;
              for (let i = from; i <= to; i++) {
                const k = c[i];
                if (!k) continue;
                if (k.low < lo) lo = k.low;
                if (k.high > hi) hi = k.high;
              }
              if (!Number.isFinite(lo) || !Number.isFinite(hi) || hi <= 0) return original();
              // a flat stretch has zero range: give it a band so it does not collapse to a line
              const pad = (hi - lo) * 0.12 || hi * 0.002;
              return { priceRange: { minValue: lo - pad, maxValue: hi + pad } };
            } catch {
              return original();
            }
          },
        });

        // Volume along the bottom sixth, with no label of its own on the price axis: its last
        // value ("0.002000") used to sit under the price labels as if it were a price.
        const volumeSeries = chart.addHistogramSeries({
          priceFormat: { type: "volume" },
          priceScaleId: "volume",
          priceLineVisible: false,
          lastValueVisible: false,
        });

        chart.priceScale("volume").applyOptions({
          scaleMargins: { top: 0.84, bottom: 0 },
        });

        // Legends (spec §3.1): the crosshair bar, or the last bar when the pointer leaves
        chart.subscribeCrosshairMove((param: MouseEventParams) => {
          const t = typeof param.time === "number" ? param.time : null;
          const idx = t !== null ? indexRef.current.get(t) : undefined;
          writeLegend(idx === undefined ? null : idx);
          writeOverlayLegend(idx === undefined ? null : idx);
        });

        chartRef.current = chart;
        seriesRef.current = candleSeries;
        volumeSeriesRef.current = volumeSeries;

        resizeObs = new ResizeObserver((entries) => {
          if (destroyed) return;
          const { width, height } = entries[0].contentRect;
          if (width > 0 && height > 0) {
            heightRef.current = height;
            chart.applyOptions({ width, height });
            applyMargins();
          }
        });
        resizeObs.observe(containerRef.current);

        setChartReady(true);
        onChartRef.current?.(chart);
      } catch (e) {
        console.error("[Chart] init error:", e);
        setError(e instanceof Error && e.message ? e.message : "Chart failed to load");
      }
    })();

    return () => {
      destroyed = true;
      resizeObs?.disconnect();
      onChartRef.current?.(null);
      if (chartRef.current) {
        chartRef.current.remove();
        chartRef.current = null;
        seriesRef.current = null;
        volumeSeriesRef.current = null;
        lineRefs.clear();
        ovRefs.series = [];
        layersRef.current = [];
      }
    };
  }, [writeLegend, writeOverlayLegend, applyMargins]);

  // the legend grows a row (overlays, ladder, notes) → the candles move down to stay clear of it
  useEffect(() => {
    legendRowsRef.current = legendRows;
    if (chartReady) applyMargins();
  }, [legendRows, chartReady, applyMargins]);

  // Candle colours follow the colour-blind switch
  useEffect(() => {
    const s = seriesRef.current;
    if (!chartReady || !s) return;
    s.applyOptions({ upColor, downColor, borderUpColor: upColor, borderDownColor: downColor, wickUpColor: upColor, wickDownColor: downColor });
    lastCandleHash.current = ""; // volume histogram colours are per bar -> full redraw
    lastCandleCount.current = 0;
  }, [upColor, downColor, chartReady]);

  // Step 2: candle data
  const tfSeconds = TF_SECONDS[timeframe];

  /** Recompute every overlay on the bars just drawn and push the lines */
  const feedOverlays = useCallback((candles: Candle[]) => {
    for (const layer of layersRef.current) {
      try {
        layer.specs = layer.def.compute(candles);
        layer.specs.forEach((spec, k) => {
          const s = layer.series[k];
          if (!s) return;
          s.setData(candles.map((c, i) => {
            const v = spec.values[i];
            return Number.isFinite(v) ? { time: c.time as UTCTimestamp, value: v } : { time: c.time as UTCTimestamp };
          }));
        });
      } catch (e) {
        console.error("[Chart] overlay error:", layer.def.id, e);
      }
    }
    writeOverlayLegend(null);
  }, [writeOverlayLegend]);

  const updateChart = useCallback(() => {
    if (!seriesRef.current || !volumeSeriesRef.current) return;
    // REST history at this timeframe with the socket's live edge over it (lib/chartData.ts)
    const { candles } = readChartCandles(useMarketStore.getState(), symbol, timeframe);
    if (!candles.length) return;
    drawnRef.current = candles;              // the autoscale provider above reads this

    if (firstTimeRef.current !== candles[0].time) {
      firstTimeRef.current = candles[0].time;
      setHistoryStart(candles[0].time);          // store callback, not render: safe to set state here
    }

    const last = candles[candles.length - 1];
    const hash = `${candles.length}_${last.time}_${last.open}_${last.close}_${last.high}_${last.low}_${last.volume}`;
    if (hash === lastCandleHash.current) return;
    lastCandleHash.current = hash;
    const upC = hexToRgba(colorsRef.current.up, 0.4);
    const downC = hexToRgba(colorsRef.current.down, 0.4);

    try {
      // Detect incremental update (same count or +1 bar)
      const isIncremental = candles.length === lastCandleCount.current || candles.length === lastCandleCount.current + 1;

      if (isIncremental && lastCandleCount.current > 0) {
        seriesRef.current.update({
          time: last.time as UTCTimestamp,
          open: last.open,
          high: last.high,
          low: last.low,
          close: last.close,
        });
        volumeSeriesRef.current.update({
          time: last.time as UTCTimestamp,
          value: last.volume,
          color: last.close >= last.open ? upC : downC,
        });
        indexRef.current.set(last.time, candles.length - 1);
      } else {
        // Full redraw: initial load, timeframe change, or large data change
        seriesRef.current.setData(
          candles.map((c: Candle) => ({
            time: c.time as UTCTimestamp,
            open: c.open,
            high: c.high,
            low: c.low,
            close: c.close,
          }))
        );
        volumeSeriesRef.current.setData(
          candles.map((c: Candle) => ({
            time: c.time as UTCTimestamp,
            value: c.volume,
            color: c.close >= c.open ? upC : downC,
          }))
        );
        const map = new Map<number, number>();
        candles.forEach((c, i) => map.set(c.time, i));
        indexRef.current = map;
        // Scroll to show latest candles on initial load
        if (chartRef.current) {
          chartRef.current.timeScale().scrollToRealTime();
        }
        // overlays depend on the first candle time → re-apply after a full redraw
        lastOverlayHash.current = "";
      }
      writeLegend(null);
      feedOverlays(candles);
      lastCandleCount.current = candles.length;
    } catch (e) {
      console.error("[Chart] update error:", e);
    }
  }, [symbol, timeframe, writeLegend, feedOverlays]);

  useEffect(() => {
    if (!chartReady) return;

    // Reset state on timeframe or symbol change to force full redraw
    lastCandleHash.current = "";
    lastCandleCount.current = 0;

    let lastInputs: unknown[] = [];
    const unsub = useMarketStore.subscribe((state) => {
      const inputs = chartInputs(state, symbol, timeframe);
      if (inputs[0] !== lastInputs[0] || inputs[1] !== lastInputs[1]) {
        lastInputs = inputs;
        updateChart();
      }
    });
    updateChart(); // Load existing data immediately
    return () => { unsub(); };
  }, [symbol, chartReady, timeframe, updateChart]);

  // Step 2b: indicator overlays — one line series per component, on the price scale
  useEffect(() => {
    const chart = chartRef.current;
    if (!chartReady || !chart) return;
    const defs = overlayDefs ?? [];
    const layers = layersRef.current;
    const wanted = new Set(defs.map((d) => d.id));
    for (const layer of layers.filter((l) => !wanted.has(l.def.id))) {
      for (const s of layer.series) { try { chart.removeSeries(s); } catch { /* disposed */ } }
    }
    const kept = layers.filter((l) => wanted.has(l.def.id));
    const next: OverlayLayer[] = defs.map((def) => {
      const existing = kept.find((l) => l.def.id === def.id);
      if (existing) return existing;
      const specs = def.compute([]);
      const series = specs.map((spec) => chart.addLineSeries({
        color: spec.color,
        lineWidth: spec.width ?? 1,
        lineStyle: LINE_STYLE[spec.style ?? "solid"],
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
        priceScaleId: "right",
      }));
      return { def, specs, series };
    });
    layersRef.current = next;
    feedOverlays(drawnRef.current);
  }, [overlayDefs, chartReady, feedOverlays]);

  // Step 3: Update trade markers
  useEffect(() => {
    if (!chartReady || !seriesRef.current || !trades?.length) {
      if (seriesRef.current && lastMarkersHash.current !== "") {
        try { seriesRef.current.setMarkers([]); } catch { /* series already disposed */ }
        lastMarkersHash.current = "";
      }
      return;
    }

    // historyStart is a dependency: markers are rebuilt when the candle history (re)loads
    const firstTime = historyStart;
    const hash = `${trades.length}_${trades[0]?.timestamp}_${trades[trades.length - 1]?.timestamp}_${timeframe}_${firstTime}_${upColor}`;
    if (hash === lastMarkersHash.current) return;
    lastMarkersHash.current = hash;

    try {
      const markers: SeriesMarker<Time>[] = [];

      for (const t of trades) {
        if (!t.timestamp || !t.price) continue;
        // Align trade timestamp to timeframe bucket
        const time = (Math.floor(t.timestamp / tfSeconds) * tfSeconds) as UTCTimestamp;
        // Trades older than the loaded candles have no bar to sit on: lightweight-charts would
        // stack them all on the first visible bar (seen on the CT: 24 closed trades piled up at
        // the left edge). Keep only markers that fall inside the loaded history.
        if (firstTime && (time as number) < firstTime) continue;

        if (t.trade_type === "ENTRY") {
          const isBuy = t.side === "BUY";
          markers.push({
            time,
            position: isBuy ? "belowBar" : "aboveBar",
            color: isBuy ? colorsRef.current.up : colorsRef.current.down,
            shape: isBuy ? "arrowUp" : "arrowDown",
            text: `${isBuy ? "L" : "S"} $${t.price.toFixed(0)}`,
          });
        } else {
          const isWin = t.pnl > 0;
          const pnlStr = t.pnl >= 0 ? `+${t.pnl.toFixed(2)}` : t.pnl.toFixed(2);
          // serialize_trade sends the POSITION side on exits (BUY = closed long)
          const wasLong = t.side === "BUY";
          markers.push({
            time,
            position: wasLong ? "aboveBar" : "belowBar",
            color: isWin ? colorsRef.current.up : colorsRef.current.down,
            shape: "circle",
            text: `$${pnlStr}`,
          });
        }
      }

      markers.sort((a, b) => (a.time as number) - (b.time as number));
      seriesRef.current.setMarkers(markers);
    } catch (e) {
      console.error("[Chart] markers error:", e);
    }
  }, [trades, chartReady, timeframe, tfSeconds, historyStart, upColor, downColor]);

  // Step 4: live price lines (entry / SL / TP / liq of open positions)
  useEffect(() => {
    if (!chartReady || !seriesRef.current) return;
    const specs = priceLines ?? [];
    const hash = specs.map((s) => `${s.id}:${s.price.toFixed(4)}:${s.title}`).join("|");
    if (hash === lastLinesHash.current) return;
    lastLinesHash.current = hash;
    try {
      applyPriceLines(seriesRef.current, priceLineRefs.current, specs);
    } catch (e) {
      console.error("[Chart] price lines error:", e);
    }
  }, [priceLines, chartReady]);

  // Step 5: divergence overlays (pivot line + trigger level)
  useEffect(() => {
    if (!chartReady || !chartRef.current) return;
    const list = overlays ?? [];
    const hash = `${timeframe}_${firstTimeRef.current}_` + list.map((o) => `${o.id}:${o.triggerLevel ?? ""}`).join("|");
    if (hash === lastOverlayHash.current) return;
    lastOverlayHash.current = hash;
    try {
      applyOverlays(chartRef.current, overlayRefs.current, list, tfSeconds, firstTimeRef.current);
    } catch (e) {
      console.error("[Chart] overlays error:", e);
    }
  }, [overlays, chartReady, timeframe, tfSeconds]);

  if (error) {
    return (
      <div className={className} style={{ display: "flex", alignItems: "center", justifyContent: "center", color: "#FFFFFF", fontSize: 13, fontWeight: 500 }}>
        Chart error: {error}
      </div>
    );
  }

  return <div ref={containerRef} className={className} style={{ width: "100%", height: "100%" }} />;
}
