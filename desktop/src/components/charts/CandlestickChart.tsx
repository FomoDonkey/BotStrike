import { useEffect, useRef, useState, useCallback } from "react";
import type { IChartApi, IPriceLine, ISeriesApi, SeriesMarker, Time, UTCTimestamp } from "lightweight-charts";
import { useMarketStore, type Candle } from "@/stores/marketStore";
import { type TradeData } from "@/stores/tradingStore";
import { resampleCandles } from "@/lib/indicators";
import { COLOR_DOWN, COLOR_UP } from "@/lib/constants";
import { applyOverlays, applyPriceLines, type DivergenceOverlay, type OverlayRefs, type PriceLineSpec } from "./chartOverlays";
import { CHART_THEME, TF_SECONDS, type Timeframe } from "./chartConfig";

export type { Timeframe };

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
}

export function CandlestickChart({ symbol, className, trades, timeframe = "1m", priceLines, overlays, onChart }: CandlestickChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const priceLineRefs = useRef<Map<string, IPriceLine>>(new Map());
  const overlayRefs = useRef<OverlayRefs>({ series: [] });
  const firstTimeRef = useRef(0);
  const lastCandleHash = useRef("");
  const lastMarkersHash = useRef("");
  const lastLinesHash = useRef("");
  const lastOverlayHash = useRef("");
  const lastCandleCount = useRef(0);
  const [error, setError] = useState<string | null>(null);
  const [chartReady, setChartReady] = useState(false);
  const onChartRef = useRef(onChart);
  useEffect(() => { onChartRef.current = onChart; });

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
          timeScale: {
            borderColor: CHART_THEME.border,
            timeVisible: true,
            secondsVisible: false,
            rightOffset: 5,         // Space after last candle
            barSpacing: 6,          // Compact bars to show more candles
            fixLeftEdge: false,
            fixRightEdge: false,
          },
          handleScroll: { vertTouchDrag: false },
        });

        const candleSeries = chart.addCandlestickSeries({
          upColor: COLOR_UP,
          downColor: COLOR_DOWN,
          borderUpColor: COLOR_UP,
          borderDownColor: COLOR_DOWN,
          wickUpColor: COLOR_UP,
          wickDownColor: COLOR_DOWN,
        });

        const volumeSeries = chart.addHistogramSeries({
          priceFormat: { type: "volume" },
          priceScaleId: "volume",
        });

        chart.priceScale("volume").applyOptions({
          scaleMargins: { top: 0.8, bottom: 0 },
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
  }, []);

  // Step 2: Subscribe to candle data and update chart
  const tfSeconds = TF_SECONDS[timeframe];

  const updateChart = useCallback(() => {
    const rawCandles = useMarketStore.getState().candles[symbol];
    if (!rawCandles?.length || !seriesRef.current || !volumeSeriesRef.current) return;

    // Resample if timeframe > 1m
    const candles = tfSeconds > 60 ? resampleCandles(rawCandles, tfSeconds) : rawCandles;
    if (!candles.length) return;
    firstTimeRef.current = candles[0].time;

    const last = candles[candles.length - 1];
    const hash = `${candles.length}_${last.time}_${last.open}_${last.close}_${last.high}_${last.low}_${last.volume}`;
    if (hash === lastCandleHash.current) return;
    lastCandleHash.current = hash;

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
          color: last.close >= last.open ? "rgba(0,212,170,0.25)" : "rgba(244,63,94,0.25)",
        });
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
            color: c.close >= c.open ? "rgba(0,212,170,0.25)" : "rgba(244,63,94,0.25)",
          }))
        );
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
  }, [symbol, tfSeconds]);

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

    // firstTime is part of the key: markers must be rebuilt when the candle history (re)loads
    const firstTime = firstTimeRef.current;
    const hash = `${trades.length}_${trades[0]?.timestamp}_${trades[trades.length - 1]?.timestamp}_${timeframe}_${firstTime}`;
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
            color: isBuy ? COLOR_UP : COLOR_DOWN,
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
            color: isWin ? COLOR_UP : COLOR_DOWN,
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
  }, [trades, chartReady, timeframe, tfSeconds]);

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
      <div className={className} style={{ display: "flex", alignItems: "center", justifyContent: "center", color: COLOR_DOWN, fontSize: 12 }}>
        Chart error: {error}
      </div>
    );
  }

  return <div ref={containerRef} className={className} style={{ width: "100%", height: "100%" }} />;
}
