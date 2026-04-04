import { useEffect, useRef, useState, useCallback } from "react";
import { useMarketStore, type Candle } from "@/stores/marketStore";
import { type TradeData } from "@/stores/tradingStore";

export type Timeframe = "1m" | "5m" | "15m" | "1h" | "4h";

const TF_SECONDS: Record<Timeframe, number> = {
  "1m": 60,
  "5m": 300,
  "15m": 900,
  "1h": 3600,
  "4h": 14400,
};

interface CandlestickChartProps {
  symbol: string;
  className?: string;
  trades?: TradeData[];
  timeframe?: Timeframe;
}

/** Resample 1m candles to a higher timeframe client-side. */
function resampleCandles(candles: Candle[], tfSeconds: number): Candle[] {
  if (tfSeconds <= 60 || !candles.length) return candles;

  const buckets = new Map<number, Candle>();
  for (const c of candles) {
    const key = Math.floor(c.time / tfSeconds) * tfSeconds;
    const existing = buckets.get(key);
    if (!existing) {
      buckets.set(key, { time: key, open: c.open, high: c.high, low: c.low, close: c.close, volume: c.volume });
    } else {
      existing.high = Math.max(existing.high, c.high);
      existing.low = Math.min(existing.low, c.low);
      existing.close = c.close;
      existing.volume += c.volume;
    }
  }

  return Array.from(buckets.values()).sort((a, b) => a.time - b.time);
}

export function CandlestickChart({ symbol, className, trades, timeframe = "1m" }: CandlestickChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<any>(null);
  const seriesRef = useRef<any>(null);
  const volumeSeriesRef = useRef<any>(null);
  const lastCandleHash = useRef("");
  const lastMarkersHash = useRef("");
  const lastCandleCount = useRef(0);
  const [error, setError] = useState<string | null>(null);
  const [chartReady, setChartReady] = useState(false);

  // Step 1: Initialize chart (async)
  useEffect(() => {
    if (!containerRef.current) return;
    let destroyed = false;
    let resizeObs: ResizeObserver | null = null;

    (async () => {
      try {
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

        resizeObs = new ResizeObserver((entries) => {
          if (destroyed) return;
          const { width, height } = entries[0].contentRect;
          if (width > 0 && height > 0) chart.applyOptions({ width, height });
        });
        resizeObs.observe(containerRef.current);

        setChartReady(true);
      } catch (e: any) {
        console.error("[Chart] init error:", e);
        setError(e.message || "Chart failed to load");
      }
    })();

    return () => {
      destroyed = true;
      resizeObs?.disconnect();
      if (chartRef.current) {
        chartRef.current.remove();
        chartRef.current = null;
        seriesRef.current = null;
        volumeSeriesRef.current = null;
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

    const last = candles[candles.length - 1];
    const hash = `${candles.length}_${last.time}_${last.open}_${last.close}_${last.high}_${last.low}_${last.volume}`;
    if (hash === lastCandleHash.current) return;
    lastCandleHash.current = hash;

    try {
      // Detect incremental update (same count or +1 bar)
      const isIncremental = candles.length === lastCandleCount.current || candles.length === lastCandleCount.current + 1;

      if (isIncremental && lastCandleCount.current > 0) {
        const lastCandle = {
          time: last.time as any,
          open: last.open,
          high: last.high,
          low: last.low,
          close: last.close,
        };
        seriesRef.current.update(lastCandle);
        volumeSeriesRef.current.update({
          time: last.time as any,
          value: last.volume,
          color: last.close >= last.open ? "rgba(0,212,170,0.2)" : "rgba(255,71,87,0.2)",
        });
      } else {
        // Full redraw: initial load, timeframe change, or large data change
        seriesRef.current.setData(
          candles.map((c: Candle) => ({
            time: c.time as any,
            open: c.open,
            high: c.high,
            low: c.low,
            close: c.close,
          }))
        );
        volumeSeriesRef.current.setData(
          candles.map((c: Candle) => ({
            time: c.time as any,
            value: c.volume,
            color: c.close >= c.open ? "rgba(0,212,170,0.2)" : "rgba(255,71,87,0.2)",
          }))
        );
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
        try { seriesRef.current.setMarkers([]); } catch {}
        lastMarkersHash.current = "";
      }
      return;
    }

    const hash = `${trades.length}_${trades[trades.length - 1]?.timestamp}_${timeframe}`;
    if (hash === lastMarkersHash.current) return;
    lastMarkersHash.current = hash;

    try {
      const markers: any[] = [];

      for (const t of trades) {
        if (!t.timestamp || !t.price) continue;
        // Align trade timestamp to timeframe bucket
        const time = Math.floor(t.timestamp / tfSeconds) * tfSeconds;

        if (t.trade_type === "ENTRY") {
          const isBuy = t.side === "BUY";
          markers.push({
            time,
            position: isBuy ? "belowBar" : "aboveBar",
            color: isBuy ? "#00D4AA" : "#FF4757",
            shape: isBuy ? "arrowUp" : "arrowDown",
            text: `${isBuy ? "L" : "S"} $${t.price.toFixed(0)}`,
          });
        } else {
          const isWin = t.pnl > 0;
          const pnlStr = t.pnl >= 0 ? `+${t.pnl.toFixed(2)}` : t.pnl.toFixed(2);
          const wasLong = t.side === "SELL";
          markers.push({
            time,
            position: wasLong ? "aboveBar" : "belowBar",
            color: isWin ? "#00D4AA" : "#FF4757",
            shape: "circle",
            text: `$${pnlStr}`,
          });
        }
      }

      markers.sort((a, b) => a.time - b.time);
      seriesRef.current.setMarkers(markers);
    } catch (e) {
      console.error("[Chart] markers error:", e);
    }
  }, [trades, chartReady, timeframe, tfSeconds]);

  if (error) {
    return (
      <div className={className} style={{ display: "flex", alignItems: "center", justifyContent: "center", color: "#FF4757", fontSize: 12 }}>
        Chart error: {error}
      </div>
    );
  }

  return <div ref={containerRef} className={className} style={{ width: "100%", height: "100%" }} />;
}
