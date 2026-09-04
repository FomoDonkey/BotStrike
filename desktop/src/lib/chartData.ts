// What the chart draws for (symbol, timeframe): REST history at the bar size asked for, with the
// socket's live one-minute bars laid over its right edge.
//
// Until 2026-09-04 the chart drew ONLY the socket snapshot — the last 500 one-minute bars — and
// resampled them client-side, so a 1 h chart of BTC held eight candles and a 4 h chart held two.
// Strike's own terminal shows months at any resolution. Now the bars come from
// `/api/market/{sym}/klines` (the engine's 90-day frame for a streamed market, the venue's klines
// for the rest) and the socket only carries the live edge: every bucket from the last REST bar on
// is rebuilt from the one-minute stream, so the forming candle still moves tick by tick.
import type { Candle, KlineWindow } from "@/stores/marketStore";
import { resampleCandles } from "@/lib/indicators";
import { INTERVAL_SECONDS, TF_SECONDS, type Timeframe } from "@/components/charts/chartConfig";

export interface ChartSeries {
  candles: Candle[];
  /** seconds per bar actually drawn */
  barSeconds: number;
  /** the interval drawn when it is coarser than the one asked for (thin venue market), else null */
  served: string | null;
}

export const klineKey = (symbol: string, timeframe: string) => `${symbol}:${timeframe}`;

const EMPTY: ChartSeries = { candles: [], barSeconds: 60, served: null };

interface Inputs {
  candles: Record<string, Candle[]>;
  klines: Record<string, KlineWindow>;
}

/** The two store slices a chart for (symbol, timeframe) depends on — subscribers compare these by identity. */
export function chartInputs(state: Inputs, symbol: string, timeframe: Timeframe): [Candle[] | undefined, KlineWindow | undefined] {
  return [state.candles[symbol], state.klines[klineKey(symbol, timeframe)]];
}

export function readChartCandles(state: Inputs, symbol: string, timeframe: Timeframe): ChartSeries {
  const tfSeconds = TF_SECONDS[timeframe];
  const hist = state.klines[klineKey(symbol, timeframe)];
  const live = state.candles[symbol];

  if (!hist?.candles.length) {
    // history not in yet: the socket window alone, as before
    if (!live?.length) return EMPTY;
    return { candles: resampleCandles(live, tfSeconds), barSeconds: tfSeconds, served: null };
  }
  const histSeconds = INTERVAL_SECONDS[hist.interval] ?? tfSeconds;
  const served = histSeconds !== tfSeconds ? hist.interval : null;
  // A coarser bar than asked for (thin market) does not share a grid with the 1 m stream, and a
  // market the engine does not stream has no live bars at all: the history is the chart.
  if (served || !live?.length) return { candles: hist.candles, barSeconds: histSeconds, served };

  const tail = resampleCandles(live, tfSeconds);
  const lastHist = hist.candles[hist.candles.length - 1].time;
  // The live edge owns every bucket from the last REST bar on — that bar has moved since it was
  // fetched. A bucket that starts before the socket window is only partly covered, so it is not
  // trusted; the next refresh of the history closes that gap.
  const edge = tail.filter((c) => c.time >= lastHist && c.time >= live[0].time);
  if (!edge.length) return { candles: hist.candles, barSeconds: tfSeconds, served: null };
  const first = edge[0].time;
  const base = hist.candles.filter((c) => c.time < first);
  return { candles: base.concat(edge), barSeconds: tfSeconds, served: null };
}
