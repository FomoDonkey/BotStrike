import { useMemo } from "react";
import { api, type MarketInfoResponse } from "@/lib/api";
import { useMarketStore } from "@/stores/marketStore";
import { useRiskStore } from "@/stores/riskStore";
import { useNow } from "./useNow";
import { useEndpoint } from "./useEndpoint";
import { change24h, fundingCountdownSec, spanLabel, stats24h } from "@/lib/market";

const MARKET_POLL_MS = 10_000;

export interface MarketView {
  symbol: string;
  price: number;
  prevPrice: number;
  mark: number;
  index: number;
  funding: number | null;
  countdownSec: number;
  change: number | null;
  high: number | null;
  low: number | null;
  volumeUsd: number;
  volumeBase: number | null;
  oi: number;
  spreadBps: number | null;
  regime: string;
  regimeSince: number;
  regimeTf: number;
  /** "24h" or the real span of the candle window ("15h") */
  winLabel: string;
  windowIs24h: boolean;
  /** REST payload (bridge ≥ 2.15) or null */
  rest: MarketInfoResponse | null;
  restMissing: boolean;
  dataAgeSec: number | null;
}

/**
 * One view of the market header data for a symbol: GET /api/market/{sym} (10 s) wins, the WS
 * snapshot and the 1m candles in memory fill the gaps (older bridges). A payload fetched for
 * the previous symbol is never shown for the new one.
 */
export function useMarketInfo(symbol: string): MarketView {
  const now = useNow();
  const nowSec = now / 1000;
  const price = useMarketStore((s) => s.prices[symbol] || 0);
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
    const derivedChange = change24h(winStats, price);
    const mark = rest?.mark_price || info?.mark_price || 0;
    const index = rest?.index_price || info?.index_price || 0;
    const funding = rest?.funding_rate ?? info?.funding_rate ?? null;
    const restAgeSec = ep.at > 0 ? Math.max(0, (now - ep.at) / 1000) : 0;
    const countdownSec = typeof rest?.funding_countdown_sec === "number"
      ? Math.max(0, rest.funding_countdown_sec - restAgeSec)
      : typeof info?.funding_countdown_sec === "number" && info.updated
        ? Math.max(0, info.funding_countdown_sec - (nowSec - info.updated))
        : fundingCountdownSec(now);
    const change = rest?.change_24h_pct ?? info?.change_24h_pct ?? derivedChange;
    const high = rest?.high_24h ?? info?.high_24h ?? (winStats.high !== null ? Math.max(winStats.high, price) : null);
    const lowRaw = rest?.low_24h ?? info?.low_24h ?? (winStats.low !== null ? Math.min(winStats.low, price || Infinity) : null);
    const low = lowRaw !== null && Number.isFinite(lowRaw) ? lowRaw : null;
    const bridgeChange = rest?.change_24h_pct ?? info?.change_24h_pct;
    const bridgeWindowMin = typeof rest?.window_min === "number" ? rest.window_min : null;
    const spanSec = typeof bridgeChange === "number" ? (bridgeWindowMin !== null ? bridgeWindowMin * 60 : 24 * 3600) : winStats.span_sec;
    const windowIs24h = spanSec >= 23.5 * 3600;
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
      volumeUsd: rest?.volume_24h_usd || info?.volume_24h || 0,
      volumeBase: typeof rest?.volume_24h_base === "number" ? rest.volume_24h_base : winStats.volume_base > 0 ? winStats.volume_base : null,
      oi: rest?.open_interest || info?.open_interest || 0,
      spreadBps: rest?.spread_bps ?? info?.spread_bps ?? orderbook?.spread_bps ?? null,
      regime: rest?.regime || wsRegime || riskRegime || "UNKNOWN",
      regimeSince: rest?.regime_since || info?.regime_since || riskSince || 0,
      regimeTf: rest?.regime_timeframe_min || info?.regime_timeframe_min || 15,
      winLabel: windowIs24h ? "24h" : spanLabel(spanSec),
      windowIs24h,
      rest,
      restMissing: ep.missing,
      dataAgeSec: typeof rest?.data_age_sec === "number" ? rest.data_age_sec : null,
    };
  }, [symbol, price, prevPrice, info, rest, ep.missing, ep.at, winStats, orderbook, wsRegime, riskRegime, riskSince, now, nowSec]);
}
