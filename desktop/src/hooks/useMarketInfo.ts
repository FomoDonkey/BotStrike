import { useMemo } from "react";
import { api, type MarketInfoResponse } from "@/lib/api";
import { useMarketStore } from "@/stores/marketStore";
import { useRiskStore } from "@/stores/riskStore";
import { useNow } from "./useNow";
import { useEndpoint } from "./useEndpoint";
import { change24h, fundingCountdownSec, spanLabel, stats24h } from "@/lib/market";

// The header now leads with the VENUE's mark rather than the streamed feed's last print, so this
// poll is what makes it move. At 10 s it visibly lagged the screen it gets compared against.
const MARKET_POLL_MS = 4_000;

export interface MarketView {
  symbol: string;
  price: number;
  prevPrice: number;
  mark: number;
  index: number;
  funding: number | null;
  countdownSec: number | null;
  change: number | null;
  high: number | null;
  low: number | null;
  volumeUsd: number;
  volumeBase: number | null;
  oi: number;
  /** the VENUE's live spread — what crossing this book actually costs */
  spreadBps: number | null;
  /** the reference feed's own last print and spread, shown only where the feed is the subject */
  feedPrice: number | null;
  feedSpreadBps: number | null;
  regime: string;
  regimeSince: number;
  regimeTf: number;
  /** "24h" or the real span of the candle window ("15h") */
  winLabel: string;
  windowIs24h: boolean;
  /** the venue publishes no 24 h block for this market (COIN-USD is in premiumIndex, not in ticker) */
  statsMissing: boolean;
  /** REST payload (bridge ≥ 2.15) or null */
  rest: MarketInfoResponse | null;
  restMissing: boolean;
  dataAgeSec: number | null;
}

/**
 * One view of the market header data for a symbol. GET /api/market/{sym} is the ONLY source for
 * anything that describes the market, because it is the only one that speaks for the venue; the 1m
 * candles in memory fill in a 24 h window the venue does not publish, and the streamed price is the
 * last resort when the bridge is unreachable. A payload fetched for the previous symbol is never
 * shown for the new one.
 */
export function useMarketInfo(symbol: string): MarketView {
  const now = useNow();
  const nowSec = now / 1000;
  // The socket only streams four symbols. For every other market the venue's own last price comes
  // over REST, so the header shows a price instead of "---" (2026-09-04).
  const streamed = useMarketStore((s) => s.prices[symbol] || 0);
  const prevPrice = useMarketStore((s) => s.prevPrices[symbol] || 0);
  const info = useMarketStore((s) => s.marketInfo[symbol]);
  const candles = useMarketStore((s) => s.candles[symbol]);
  const orderbook = useMarketStore((s) => s.orderbooks[symbol]);
  const wsRegime = useMarketStore((s) => s.regime[symbol]);
  const riskRegime = useRiskStore((s) => s.regimes[symbol]);
  const riskSince = useRiskStore((s) => s.regimeSince[symbol]);

  const ep = useEndpoint(() => api.market(symbol), MARKET_POLL_MS, symbol);
  const rest = ep.data && ep.data.engine !== false ? ep.data : null;

  const minute = Math.floor(nowSec / 60);
  const winStats = useMemo(() => stats24h(candles, minute * 60), [candles, minute]);

  return useMemo<MarketView>(() => {
    // EVERY FIGURE THAT DESCRIBES THE MARKET IS THE VENUE'S. The socket streams Binance, which is
    // the strategies' price reference and not the book an order reaches, so falling back to it here
    // printed Binance's market on a Strike header — 24 h volume out by a factor of 8,000 and open
    // interest by 30,000 (audit 2026-09-04). The stream survives where it is honestly the subject:
    // the chart, the tape, the book ladder, and the last-resort price when the bridge is unreachable.
    const price = rest?.price || rest?.mark_price || streamed || 0;
    const derivedChange = change24h(winStats, price);
    const mark = rest?.mark_price || 0;
    const index = rest?.index_price || 0;
    const funding = rest?.funding_rate ?? null;
    const restAgeSec = ep.at > 0 ? Math.max(0, (now - ep.at) / 1000) : 0;
    const countdownSec = typeof rest?.funding_countdown_sec === "number"
      ? Math.max(0, rest.funding_countdown_sec - restAgeSec)
      : typeof info?.funding_countdown_sec === "number" && info.updated
        ? Math.max(0, info.funding_countdown_sec - (nowSec - info.updated))
        : fundingCountdownSec();
    const change = rest?.change_24h_pct ?? derivedChange;
    const high = rest?.high_24h ?? (winStats.high !== null ? Math.max(winStats.high, price) : null);
    const lowRaw = rest?.low_24h ?? (winStats.low !== null ? Math.min(winStats.low, price || Infinity) : null);
    const low = lowRaw !== null && Number.isFinite(lowRaw) ? lowRaw : null;
    const bridgeChange = rest?.change_24h_pct;
    const bridgeWindowMin = typeof rest?.window_min === "number" ? rest.window_min : null;
    const spanSec = typeof bridgeChange === "number" ? (bridgeWindowMin !== null ? bridgeWindowMin * 60 : 24 * 3600) : winStats.span_sec;
    // The venue answered but carries no 24 h block for this market. That is a fact about the venue,
    // not a window still filling up, and it must not be dressed as one (COIN-USD, 2026-09-04).
    const statsMissing = rest !== null && rest.change_24h_pct === null && rest.change_24h_pct !== undefined;
    const windowIs24h = statsMissing || spanSec >= 23.5 * 3600;
    return {
      symbol,
      price,
      prevPrice,
      mark,
      index,
      funding,
      countdownSec,
      change,
      high,
      low,
      volumeUsd: rest?.volume_24h_usd ?? 0,
      volumeBase: typeof rest?.volume_24h_base === "number" ? rest.volume_24h_base : null,
      // zero open interest is a fact about four of the venue's markets, not a missing value
      oi: rest?.open_interest ?? 0,
      spreadBps: rest?.spread_bps ?? null,
      feedPrice: rest?.feed_price ?? (streamed || null),
      feedSpreadBps: rest?.feed_spread_bps ?? orderbook?.spread_bps ?? null,
      regime: rest?.regime || wsRegime || riskRegime || "UNKNOWN",
      regimeSince: rest?.regime_since || info?.regime_since || riskSince || 0,
      regimeTf: rest?.regime_timeframe_min || info?.regime_timeframe_min || 15,
      winLabel: windowIs24h ? "24h" : spanLabel(spanSec),
      windowIs24h,
      statsMissing,
      rest,
      restMissing: ep.missing,
      dataAgeSec: typeof rest?.data_age_sec === "number" ? rest.data_age_sec : null,
    };
  }, [symbol, streamed, prevPrice, info, rest, ep.missing, ep.at, winStats, orderbook, wsRegime, riskRegime, riskSince, now, nowSec]);
}
