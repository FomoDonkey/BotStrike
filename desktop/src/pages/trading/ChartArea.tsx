import { useMemo, useState } from "react";
import type { IChartApi } from "lightweight-charts";
import type { PositionData } from "@/lib/api";
import type { SignalData, TradeData } from "@/stores/tradingStore";
import { ErrorBoundary } from "@/components/shared/ErrorBoundary";
import { TabBar } from "@/components/shared/TabBar";
import { CandlestickChart } from "@/components/charts/CandlestickChart";
import { IndicatorPane, type IndicatorKind } from "@/components/charts/IndicatorPane";
import { TIMEFRAMES, type Timeframe } from "@/components/charts/chartConfig";
import { divergenceOverlays, positionPriceLines } from "@/components/charts/chartOverlays";
import { SignalsFeed } from "./SignalsFeed";
import { DepthChart } from "./DepthChart";
import { MarketDetails } from "./MarketDetails";
import { cn } from "@/lib/utils";

type ChartTab = "chart" | "signals" | "depth" | "details";
type Indicator = IndicatorKind | "none";

const TABS = [
  { id: "chart", label: "Chart" },
  { id: "signals", label: "Signals" },
  { id: "depth", label: "Depth" },
  { id: "details", label: "Details" },
] as const satisfies readonly { id: ChartTab; label: string }[];

/** Contract §4: paper takes no manual orders — the engine decides. */
export const MANUAL_ORDER_TOOLTIP = "Las órdenes las decide el motor; activa una estrategia en Strategies";

interface ChartAreaProps {
  symbol: string;
  timeframe: Timeframe;
  onTimeframe: (tf: Timeframe) => void;
  markers: TradeData[];
  positions: PositionData[];
  signals: SignalData[];
}

export function ChartArea({ symbol, timeframe, onTimeframe, markers, positions, signals }: ChartAreaProps) {
  const [tab, setTab] = useState<ChartTab>("chart");
  const [indicator, setIndicator] = useState<Indicator>("macd");
  const [mainChart, setMainChart] = useState<IChartApi | null>(null);

  const priceLines = useMemo(() => positionPriceLines(positions, symbol), [positions, symbol]);
  const overlays = useMemo(() => divergenceOverlays(signals, symbol), [signals, symbol]);
  const symbolSignals = useMemo(() => signals.filter((s) => s.symbol === symbol).length, [signals, symbol]);

  const toolbar = (
    <div className="flex items-center gap-2">
      <div className="flex items-center gap-px">
        {TIMEFRAMES.map((tf) => (
          <button
            key={tf}
            type="button"
            onClick={() => onTimeframe(tf)}
            className={cn("px-2 h-6 rounded text-[11px] font-mono transition-colors", timeframe === tf ? "bg-white/10 text-text-primary" : "text-text-muted hover:text-text-secondary")}
          >
            {tf}
          </button>
        ))}
      </div>
      <span className="w-px h-4 bg-hairline" />
      <select
        value={indicator}
        onChange={(e) => setIndicator(e.target.value as Indicator)}
        aria-label="Indicator pane"
        className="h-6 bg-transparent text-[11px] text-text-secondary border border-hairline rounded px-1 focus:outline-none focus:border-white/30"
      >
        <option value="none">No indicator</option>
        <option value="rsi">RSI 14</option>
        <option value="macd">MACD 12/26/9</option>
      </select>
      <span className="w-px h-4 bg-hairline" />
      <ManualOrderButtons />
    </div>
  );

  return (
    <div className="flex flex-col flex-1 min-h-0 min-w-0">
      <TabBar
        tabs={TABS.map((t) => (t.id === "signals" && symbolSignals ? { ...t, badge: symbolSignals } : t))}
        value={tab}
        onChange={setTab}
        right={tab === "chart" ? <div className="hidden md:block">{toolbar}</div> : undefined}
      />
      {/* < md: the toolbar gets its own row so the four tabs never scroll out of sight */}
      {tab === "chart" && (
        <div className="md:hidden flex items-center h-8 px-2 border-b border-hairline-soft overflow-x-auto scrollbar-none shrink-0">{toolbar}</div>
      )}

      {tab === "chart" && (
        <div className="flex flex-col flex-1 min-h-0">
          <ErrorBoundary fallback={<div className="flex flex-1 items-center justify-center text-text-muted text-sm">Chart unavailable</div>}>
            <div className="relative flex-1 min-h-0">
              <div className="absolute inset-0">
                <CandlestickChart
                  className="w-full h-full"
                  symbol={symbol}
                  trades={markers}
                  timeframe={timeframe}
                  priceLines={priceLines}
                  overlays={overlays}
                  onChart={setMainChart}
                />
              </div>
              {overlays.length > 0 && (
                <div className="absolute left-2 top-1 z-[2] text-[10.5px] font-mono text-[#F472B6] pointer-events-none select-none">
                  {overlays.map((o) => o.label).join(" · ")} divergence
                </div>
              )}
            </div>
            {indicator !== "none" && (
              <div className="relative h-[26%] min-h-[96px] max-h-[180px] border-t border-hairline-soft shrink-0">
                <IndicatorPane symbol={symbol} timeframe={timeframe} kind={indicator} mainChart={mainChart} />
              </div>
            )}
          </ErrorBoundary>
        </div>
      )}
      {tab === "signals" && <SignalsFeed signals={signals} symbol={symbol} />}
      {tab === "depth" && <DepthChart symbol={symbol} />}
      {tab === "details" && <MarketDetails symbol={symbol} />}
    </div>
  );
}

/** Long / Short present but disabled — a wrapper carries the tooltip since disabled buttons swallow hover. */
function ManualOrderButtons() {
  return (
    <span className="inline-flex items-center gap-1" title={MANUAL_ORDER_TOOLTIP}>
      <button type="button" disabled aria-disabled className="h-6 px-2.5 rounded text-[11px] font-semibold bg-profit/15 text-profit opacity-50 cursor-not-allowed">Long</button>
      <button type="button" disabled aria-disabled className="h-6 px-2.5 rounded text-[11px] font-semibold bg-loss/15 text-loss opacity-50 cursor-not-allowed">Short</button>
    </span>
  );
}
