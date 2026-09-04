import { useEffect } from "react";
import { api } from "@/lib/api";
import { useMarketStore, type Candle, type OrderBookData, type Tick } from "@/stores/marketStore";
import { klineKey } from "@/lib/chartData";
import { useUnstreamed } from "./useVenueMarkets";

const KLINE_POLL_MS = 10_000;      // a market fed over REST: the poll IS its feed
const KLINE_REFRESH_MS = 60_000;   // a streamed market: the socket carries the live edge
const KLINE_LIMIT = 1000;
const BOOK_POLL_MS = 2_500;
const TAPE_POLL_MS = 4_000;

/**
 * Fill the market store from the bridge for the ONE market being looked at.
 *
 * Candles come over REST for EVERY market, at the timeframe the chart asked for: the engine's own
 * 90-day frame for a symbol it streams, the venue's klines for the other 27. Until 2026-09-04 a
 * streamed market drew only the socket's last 500 one-minute bars, so its 1 h chart held eight
 * candles and its 4 h chart two — Strike's terminal shows months at any resolution. The socket
 * still carries the live edge (lib/chartData.ts lays it over the history), which is why a streamed
 * market refreshes its history once a minute while a venue-fed one polls every ten seconds.
 *
 * The order book and the tape are only pulled for a market the engine does not stream; the
 * streamed ones get theirs tick by tick.
 */
export function useVenueFallback(symbol: string, timeframe: string) {
  const unstreamed = useUnstreamed(symbol);
  const interval = timeframe && timeframe !== "" ? timeframe : "1m";
  // what the venue could actually fill: a market too thin for the chosen bar gets a
  // coarser one, and the chart has to say so rather than mislabel its own candles
  const window = useMarketStore((s) => s.klines[klineKey(symbol, interval)]);
  const servedInterval = window && window.interval !== interval ? window.interval : null;

  // ── candles ────────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!symbol) return;
    let alive = true;

    const pull = async () => {
      try {
        const r = await api.marketKlines(symbol, interval, KLINE_LIMIT);
        if (!alive || !r?.candles?.length) return;
        const candles: Candle[] = r.candles.map((c) => ({
          time: c.timestamp, open: c.open, high: c.high, low: c.low, close: c.close,
          volume: c.volume,
        }));
        useMarketStore.getState().onKlines(klineKey(symbol, interval), { interval: r.interval || interval, source: r.source ?? "venue", candles });
        // A venue-fed market has no ticks: the header's live price comes from REST, and the
        // chart's last close keeps the store's price in step with it so the crosshair and the
        // ladder do not read a stale number. A streamed market's price is the tick stream's.
        const last = candles[candles.length - 1];
        if (unstreamed && last?.close > 0) {
          useMarketStore.setState((s) => ({
            prices: { ...s.prices, [symbol]: last.close },
            prevPrices: { ...s.prevPrices, [symbol]: s.prices[symbol] ?? last.close },
          }));
        }
      } catch { /* an empty chart is handled by the panel itself */ }
    };

    void pull();
    const t = setInterval(pull, unstreamed ? KLINE_POLL_MS : KLINE_REFRESH_MS);
    return () => { alive = false; clearInterval(t); };
  }, [symbol, interval, unstreamed]);

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
