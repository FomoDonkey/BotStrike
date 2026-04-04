import { useEffect, useRef, useState } from "react";
import { useMarketStore, type Candle } from "@/stores/marketStore";
import { type TradeData } from "@/stores/tradingStore";

interface CandlestickChartProps {
  symbol: string;
  className?: string;
  trades?: TradeData[];
}

export function CandlestickChart({ symbol, className, trades }: CandlestickChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<any>(null);
  const seriesRef = useRef<any>(null);
  const volumeSeriesRef = useRef<any>(null);
  const lastCandleHash = useRef("");
  const lastMarkersHash = useRef("");
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

        setChartReady(true); // Signal that chart is ready for data
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

  // Step 2: Subscribe to candle data ONLY after chart is ready
  const lastCandleCount = useRef(0);

  useEffect(() => {
    if (!chartReady) return;

    function updateChart() {
      const candles = useMarketStore.getState().candles[symbol];
      if (!candles?.length || !seriesRef.current || !volumeSeriesRef.current) return;

      const last = candles[candles.length - 1];
      const hash = `${candles.length}_${last.open}_${last.close}_${last.high}_${last.low}_${last.volume}`;
      if (hash === lastCandleHash.current) return;
      lastCandleHash.current = hash;

      try {
        const isIncremental = candles.length === lastCandleCount.current || candles.length === lastCandleCount.current + 1;

        if (isIncremental && lastCandleCount.current > 0) {
          // Incremental update: only update the last candle (much faster, no chart jump)
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
          // Full redraw: initial load or large data change
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
    }

    // Subscribe only to candle changes for this symbol (not entire store)
    const unsub = useMarketStore.subscribe(
      (state) => state.candles[symbol],
      () => updateChart()
    );
    updateChart(); // Load existing data
    return () => { unsub(); };
  }, [symbol, chartReady]);

  // Step 3: Update trade markers on chart when trades change
  useEffect(() => {
    if (!chartReady || !seriesRef.current || !trades?.length) {
      // Clear markers if no trades
      if (seriesRef.current && lastMarkersHash.current !== "") {
        try { seriesRef.current.setMarkers([]); } catch {}
        lastMarkersHash.current = "";
      }
      return;
    }

    // Dedupe check
    const hash = `${trades.length}_${trades[trades.length - 1]?.timestamp}`;
    if (hash === lastMarkersHash.current) return;
    lastMarkersHash.current = hash;

    try {
      const markers: any[] = [];

      for (const t of trades) {
        if (!t.timestamp || !t.price) continue;
        // Convert unix timestamp to chart time (seconds, floored to minute for 1m candles)
        const time = Math.floor(t.timestamp / 60) * 60;

        if (t.trade_type === "ENTRY") {
          // Entry marker: arrow pointing in trade direction
          const isBuy = t.side === "BUY";
          markers.push({
            time,
            position: isBuy ? "belowBar" : "aboveBar",
            color: isBuy ? "#00D4AA" : "#FF4757",
            shape: isBuy ? "arrowUp" : "arrowDown",
            text: `${isBuy ? "L" : "S"} $${t.price.toFixed(0)}`,
          });
        } else {
          // Exit marker: circle with PnL
          const isWin = t.pnl > 0;
          const pnlStr = t.pnl >= 0 ? `+${t.pnl.toFixed(2)}` : t.pnl.toFixed(2);
          // Position opposite to likely entry side (exit BUY = was short, exit SELL = was long)
          const wasLong = t.side === "SELL"; // Closing a long = sell
          markers.push({
            time,
            position: wasLong ? "aboveBar" : "belowBar",
            color: isWin ? "#00D4AA" : "#FF4757",
            shape: "circle",
            text: `$${pnlStr}`,
          });
        }
      }

      // lightweight-charts requires markers sorted by time
      markers.sort((a, b) => a.time - b.time);
      seriesRef.current.setMarkers(markers);
    } catch (e) {
      console.error("[Chart] markers error:", e);
    }
  }, [trades, chartReady]);

  if (error) {
    return (
      <div className={className} style={{ display: "flex", alignItems: "center", justifyContent: "center", color: "#FF4757", fontSize: 12 }}>
        Chart error: {error}
      </div>
    );
  }

  return <div ref={containerRef} className={className} style={{ width: "100%", height: "100%" }} />;
}
