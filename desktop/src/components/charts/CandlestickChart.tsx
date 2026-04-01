import { useEffect, useRef, useCallback } from "react";
import { createChart, type IChartApi, type ISeriesApi, ColorType, CrosshairMode } from "lightweight-charts";
import { useMarketStore, type Candle } from "@/stores/marketStore";

interface CandlestickChartProps {
  symbol: string;
  className?: string;
}

export function CandlestickChart({ symbol, className }: CandlestickChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const lastCandleHash = useRef("");

  // Initialize chart
  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "#8898AA",
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: "rgba(255,255,255,0.03)" },
        horzLines: { color: "rgba(255,255,255,0.03)" },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: "rgba(0,212,170,0.3)", width: 1, style: 2, labelBackgroundColor: "#0B1120" },
        horzLine: { color: "rgba(0,212,170,0.3)", width: 1, style: 2, labelBackgroundColor: "#0B1120" },
      },
      rightPriceScale: {
        borderColor: "rgba(255,255,255,0.05)",
        scaleMargins: { top: 0.1, bottom: 0.25 },
      },
      timeScale: {
        borderColor: "rgba(255,255,255,0.05)",
        timeVisible: true,
        secondsVisible: false,
      },
      handleScroll: { vertTouchDrag: false },
    });

    const candleSeries = chart.addCandlestickSeries({
      upColor: "#00D4AA",
      downColor: "#FF4757",
      borderUpColor: "#00D4AA",
      borderDownColor: "#FF4757",
      wickUpColor: "#00D4AA",
      wickDownColor: "#FF4757",
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

    // Resize observer
    const resizeObserver = new ResizeObserver((entries) => {
      const { width, height } = entries[0].contentRect;
      chart.applyOptions({ width, height });
    });
    resizeObserver.observe(containerRef.current);

    return () => {
      resizeObserver.disconnect();
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
      volumeSeriesRef.current = null;
    };
  }, []);

  // Subscribe to candle updates
  const updateChart = useCallback(() => {
    const candles = useMarketStore.getState().candles[symbol];
    if (!candles || !candles.length || !seriesRef.current || !volumeSeriesRef.current) return;

    // Hash by length + last candle close to detect both new candles and updates
    const last = candles[candles.length - 1];
    const hash = `${candles.length}_${last.close}_${last.high}_${last.low}`;
    if (hash === lastCandleHash.current) return;
    lastCandleHash.current = hash;

    const candleData = candles.map((c: Candle) => ({
      time: c.time as any,
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
    }));

    const volumeData = candles.map((c: Candle) => ({
      time: c.time as any,
      value: c.volume,
      color: c.close >= c.open ? "rgba(0,212,170,0.2)" : "rgba(255,71,87,0.2)",
    }));

    seriesRef.current.setData(candleData);
    volumeSeriesRef.current.setData(volumeData);
  }, [symbol]);

  useEffect(() => {
    const unsub = useMarketStore.subscribe(updateChart);
    updateChart(); // initial
    return unsub;
  }, [updateChart]);

  return (
    <div ref={containerRef} className={className} style={{ width: "100%", height: "100%" }} />
  );
}
