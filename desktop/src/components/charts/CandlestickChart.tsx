import { useEffect, useRef, useState, useCallback, type RefObject } from "react";
import type { IChartApi, IPriceLine, ISeriesApi, MouseEventParams, SeriesMarker, Time, UTCTimestamp } from "lightweight-charts";
import { useMarketStore, type Candle } from "@/stores/marketStore";
import { type TradeData } from "@/stores/tradingStore";
import { resampleCandles } from "@/lib/indicators";
import { COLOR_DOWN, COLOR_UP } from "@/lib/constants";
import { formatPrice } from "@/lib/utils";
import { applyOverlays, applyPriceLines, type DivergenceOverlay, type OverlayRefs, type PriceLineSpec } from "./chartOverlays";
import { CHART_THEME, TF_SECONDS, type Timeframe } from "./chartConfig";

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

interface CandlestickChartProps {
  symbol: string;
  className?: string;
  trades?: TradeData[];
  timeframe?: Timeframe;
  /** Live levels of open positions (entry / SL / TP / liq) */
  priceLines?: PriceLineSpec[];
  /** DIVERGENCE signals with pivots (contract §5) */
  overlays?: DivergenceOverlay[];
  /** Exposes the chart API (time-scale / crosshair sync with the indicator pane) */
  onChart?: (chart: IChartApi | null) => void;
  /** Candle colours (colour-blind mode swaps them) */
  upColor?: string;
  downColor?: string;
  /** OHLC legend element — updated from the crosshair without React state */
  legendRef?: RefObject<HTMLElement | null>;
}

export function CandlestickChart({ symbol, className, trades, timeframe = "1m", priceLines, overlays, onChart, upColor = COLOR_UP, downColor = COLOR_DOWN, legendRef }: CandlestickChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const priceLineRefs = useRef<Map<string, IPriceLine>>(new Map());
  const overlayRefs = useRef<OverlayRefs>({ series: [] });
  const firstTimeRef = useRef(0);
  /** first loaded candle time as STATE so effects that depend on the history window re-run when it loads */
  const [historyStart, setHistoryStart] = useState(0);
  const lastCandleHash = useRef("");
  const lastMarkersHash = useRef("");
  const lastLinesHash = useRef("");
  const lastOverlayHash = useRef("");
  const lastCandleCount = useRef(0);
  const [error, setError] = useState<string | null>(null);
  const [chartReady, setChartReady] = useState(false);
  const onChartRef = useRef(onChart);
  useEffect(() => { onChartRef.current = onChart; });
  const colorsRef = useRef({ up: upColor, down: downColor });
  useEffect(() => { colorsRef.current = { up: upColor, down: downColor }; });
  /** candles currently on the chart (legend lookup by time) */
  const shownRef = useRef<Map<number, Candle>>(new Map());

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
            scaleMargins: { top: 0.08, bottom: 0.24 },
            minimumWidth: CHART_THEME.priceScaleWidth,
          },
          // lightweight-charts labels the axis in UTC, while every table on these screens uses the
          // reader's local clock: the same fill read 19:07 on the chart and 21:07 in Trade History
          // (audit 2026-09-03). One clock, the reader's, everywhere.
          localization: {
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
        });

        const volumeSeries = chart.addHistogramSeries({
          priceFormat: { type: "volume" },
          priceScaleId: "volume",
        });

        chart.priceScale("volume").applyOptions({
          scaleMargins: { top: 0.8, bottom: 0 },
        });

        // OHLC legend (spec §3.1): crosshair candle, or the last candle when the pointer leaves
        chart.subscribeCrosshairMove((param: MouseEventParams) => {
          const el = legendRef?.current;
          if (!el) return;
          const t = typeof param.time === "number" ? param.time : null;
          const c = t !== null ? shownRef.current.get(t) : undefined;
          if (c) el.textContent = legendText(c);
          else {
            const all = [...shownRef.current.values()];
            const last = all.length ? all[all.length - 1] : null;
            el.textContent = last ? legendText(last) : "";
          }
        });

        chartRef.current = chart;
        seriesRef.current = candleSeries;
        volumeSeriesRef.current = volumeSeries;

        resizeObs = new ResizeObserver((entries) => {
          if (destroyed) return;
          const { width, height } = entries[0].contentRect;
          if (width > 0 && height > 0) chart.applyOptions({ width, height });
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
      }
    };
  }, [legendRef]);

  // Candle colours follow the colour-blind switch
  useEffect(() => {
    const s = seriesRef.current;
    if (!chartReady || !s) return;
    s.applyOptions({ upColor, downColor, borderUpColor: upColor, borderDownColor: downColor, wickUpColor: upColor, wickDownColor: downColor });
    lastCandleHash.current = ""; // volume histogram colours are per bar -> full redraw
    lastCandleCount.current = 0;
  }, [upColor, downColor, chartReady]);

  // Step 2: Subscribe to candle data and update chart
  const tfSeconds = TF_SECONDS[timeframe];

  const updateChart = useCallback(() => {
    const rawCandles = useMarketStore.getState().candles[symbol];
    if (!rawCandles?.length || !seriesRef.current || !volumeSeriesRef.current) return;

    // Resample if timeframe > 1m
    const candles = tfSeconds > 60 ? resampleCandles(rawCandles, tfSeconds) : rawCandles;
    if (!candles.length) return;
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
        shownRef.current.set(last.time, last);
        if (legendRef?.current) legendRef.current.textContent = legendText(last);
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
        const map = new Map<number, Candle>();
        for (const c of candles) map.set(c.time, c);
        shownRef.current = map;
        if (legendRef?.current) legendRef.current.textContent = legendText(last);
        // Scroll to show latest candles on initial load
        if (chartRef.current) {
          chartRef.current.timeScale().scrollToRealTime();
        }
        // overlays depend on the first candle time → re-apply after a full redraw
        lastOverlayHash.current = "";
      }
      lastCandleCount.current = candles.length;
    } catch (e) {
      console.error("[Chart] update error:", e);
    }
  }, [symbol, tfSeconds, legendRef]);

  useEffect(() => {
    if (!chartReady) return;

    // Reset state on timeframe or symbol change to force full redraw
    lastCandleHash.current = "";
    lastCandleCount.current = 0;

    let lastCandleRef: Candle[] | undefined;
    const unsub = useMarketStore.subscribe((state) => {
      const current = state.candles[symbol];
      if (current !== lastCandleRef) {
        lastCandleRef = current;
        updateChart();
      }
    });
    updateChart(); // Load existing data immediately
    return () => { unsub(); };
  }, [symbol, chartReady, timeframe, updateChart]);

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
