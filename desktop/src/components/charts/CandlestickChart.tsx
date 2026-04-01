import { useEffect, useRef, useCallback, useState } from "react";
import { useMarketStore, type Candle } from "@/stores/marketStore";

interface CandlestickChartProps {
  symbol: string;
  className?: string;
}

export function CandlestickChart({ symbol, className }: CandlestickChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<any>(null);
  const seriesRef = useRef<any>(null);
  const volumeSeriesRef = useRef<any>(null);
  const lastCandleHash = useRef("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    let destroyed = false;

    async function initChart() {
      try {
        // Dynamic import to avoid SSR/WebView issues
        const lc = await import("lightweight-charts");

        if (destroyed || !containerRef.current) return;

        const chart = lc.createChart(containerRef.current, {
          layout: {
            background: { type: lc.ColorType.Solid, color: "transparent" },
            textColor: "#8898AA",
            fontFamily: "'JetBrains Mono', monospace",
            fontSize: 11,
          },
          grid: {
            vertLines: { color: "rgba(255,255,255,0.03)" },
            horzLines: { color: "rgba(255,255,255,0.03)" },
          },
          crosshair: {
            mode: lc.CrosshairMode.Normal,
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
          if (destroyed) return;
          const { width, height } = entries[0].contentRect;
          if (width > 0 && height > 0) {
            chart.applyOptions({ width, height });
          }
        });
        resizeObserver.observe(containerRef.current);

        // Cleanup on destroy
        return () => {
          destroyed = true;
          resizeObserver.disconnect();
          chart.remove();
          chartRef.current = null;
          seriesRef.current = null;
          volumeSeriesRef.current = null;
        };
      } catch (e: any) {
        console.error("[CandlestickChart] init error:", e);
        setError(e.message || "Chart failed to load");
      }
    }

    const cleanupPromise = initChart();

    return () => {
      destroyed = true;
      cleanupPromise.then((cleanup) => cleanup?.());
    };
  }, []);

  // Subscribe to candle updates
  const updateChart = useCallback(() => {
    const candles = useMarketStore.getState().candles[symbol];
    if (!candles || !candles.length || !seriesRef.current || !volumeSeriesRef.current) return;

    const last = candles[candles.length - 1];
    const hash = `${candles.length}_${last.close}_${last.high}_${last.low}`;
    if (hash === lastCandleHash.current) return;
    lastCandleHash.current = hash;

    try {
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
    } catch (e) {
      console.error("[CandlestickChart] update error:", e);
    }
  }, [symbol]);

  useEffect(() => {
    const unsub = useMarketStore.subscribe(updateChart);
    updateChart();
    return unsub;
  }, [updateChart]);

  if (error) {
    return (
      <div className={className} style={{ display: "flex", alignItems: "center", justifyContent: "center", color: "#FF4757", fontSize: 12, fontFamily: "monospace" }}>
        Chart error: {error}
      </div>
    );
  }

  return (
    <div ref={containerRef} className={className} style={{ width: "100%", height: "100%" }} />
  );
}
