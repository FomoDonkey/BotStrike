import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useMarketStore, type Candle, type OrderBookData, type Tick } from "@/stores/marketStore";
import { useUnstreamed } from "./useVenueMarkets";

const KLINE_POLL_MS = 10_000;
const BOOK_POLL_MS = 2_500;
const TAPE_POLL_MS = 4_000;

/**
 * Fill the market store from the VENUE for a market the engine does not stream.
 *
 * The engine streams four symbols, so the chart, the order book and the tape read their data from
 * frames that only exist for those four: picking any of the other 27 opened a panel with live
 * numbers in the header and nothing underneath (Edgar, 2026-09-04). Strike publishes klines, depth
 * and trades for all 31, so this pulls them for the ONE market being looked at and writes them into
 * the same store the socket writes into — which means the chart, the indicators, the ladder, the
 * depth chart and the tape all work with no change of their own.
 *
 * Only ever one symbol at a time, and only when that symbol has no stream, so the cost is a handful
 * of requests every few seconds and nothing at all on a streamed market.
 */
export function useVenueFallback(symbol: string, timeframe: string) {
  const unstreamed = useUnstreamed(symbol);
  // what the venue could actually fill: a market too thin for the chosen bar gets a
  // coarser one, and the chart has to say so rather than mislabel its own candles
  const [servedInterval, setServedInterval] = useState<string | null>(null);

  // ── candles ────────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!unstreamed || !symbol) return;
    let alive = true;
    const interval = timeframe && timeframe !== "" ? timeframe : "1m";

    const pull = async () => {
      try {
        const r = await api.marketKlines(symbol, interval, 500);
        if (!alive || !r?.candles?.length) return;
        setServedInterval(r.interval && r.interval !== interval ? r.interval : null);
        const candles: Candle[] = r.candles.map((c) => ({
          time: c.timestamp, open: c.open, high: c.high, low: c.low, close: c.close,
          volume: c.volume,
        }));
        useMarketStore.getState().onCandles(symbol, candles);
        // the header's live price comes from REST; the chart's last close keeps the store's price
        // in step with it so the crosshair and the ladder do not read a stale number
        const last = candles[candles.length - 1];
        if (last?.close > 0) {
          useMarketStore.setState((s) => ({
            prices: { ...s.prices, [symbol]: last.close },
            prevPrices: { ...s.prevPrices, [symbol]: s.prices[symbol] ?? last.close },
          }));
        }
      } catch { /* an empty chart is handled by the panel itself */ }
    };

    void pull();
    const t = setInterval(pull, KLINE_POLL_MS);
    return () => { alive = false; clearInterval(t); };
  }, [symbol, timeframe, unstreamed]);

  // ── order book ─────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!unstreamed || !symbol) return;
    let alive = true;

    const pull = async () => {
      try {
        const r = await api.marketBook(symbol, 20);
        if (!alive || !r?.bids?.length || !r?.asks?.length) return;
        const bids = r.bids.map(([price, quantity]) => ({ price, quantity }));
        const asks = r.asks.map(([price, quantity]) => ({ price, quantity }));
        const bestBid = bids[0]?.price ?? null;
        const bestAsk = asks[0]?.price ?? null;
        const mid = bestBid !== null && bestAsk !== null ? (bestBid + bestAsk) / 2 : null;
        const book: OrderBookData = {
          symbol, bids, asks, best_bid: bestBid, best_ask: bestAsk, mid_price: mid,
          spread: bestBid !== null && bestAsk !== null ? bestAsk - bestBid : 0,
          spread_bps: r.spread_bps ?? 0,
          microprice: mid,
        };
        useMarketStore.setState((s) => ({ orderbooks: { ...s.orderbooks, [symbol]: book } }));
      } catch { /* the ladder shows its own empty state */ }
    };

    void pull();
    const t = setInterval(pull, BOOK_POLL_MS);
    return () => { alive = false; clearInterval(t); };
  }, [symbol, unstreamed]);

  // ── tape ───────────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!unstreamed || !symbol) return;
    let alive = true;

    const pull = async () => {
      try {
        const r = await api.marketTrades(symbol, 60);
        if (!alive || !r?.trades?.length) return;
        // the store keeps the tape newest-first
        const ticks: Tick[] = r.trades
          .slice()
          .reverse()
          .map((t) => ({
            symbol, price: t.price, quantity: t.quantity,
            side: t.side === "sell" ? "SELL" : "BUY",
            notional: t.price * t.quantity, timestamp: t.timestamp,
          }));
        useMarketStore.setState((s) => ({ tape: { ...s.tape, [symbol]: ticks } }));
      } catch { /* the tape shows its own empty state */ }
    };

    void pull();
    const t = setInterval(pull, TAPE_POLL_MS);
    return () => { alive = false; clearInterval(t); };
  }, [symbol, unstreamed]);

  return { unstreamed, servedInterval };
}
