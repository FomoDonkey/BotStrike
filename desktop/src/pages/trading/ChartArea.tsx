import { useMemo, useRef, useState } from "react";
import type { IChartApi } from "lightweight-charts";
import { Camera, Maximize2, RotateCcw } from "lucide-react";
import type { PositionData } from "@/lib/api";
import { useMarketStore } from "@/stores/marketStore";
import type { SignalData, TradeData } from "@/stores/tradingStore";
import { useUiStore } from "@/stores/uiStore";
import { useUnstreamed } from "@/hooks/useVenueMarkets";
import type { MarketView } from "@/hooks/useMarketInfo";
import { ErrorBoundary } from "@/components/shared/ErrorBoundary";
import { TabBar } from "@/components/ui/TabBar";
import { IconButton } from "@/components/ui/Button";
import { Popover, MenuItem, DropdownTrigger, MenuLabel } from "@/components/ui/Popover";
import { FundingChart } from "@/components/ui/FundingChart";
import { CandlestickChart } from "@/components/charts/CandlestickChart";
import { IndicatorPane, type IndicatorKind } from "@/components/charts/IndicatorPane";
import { MORE_TIMEFRAMES, TF_SECONDS, TIMEFRAMES, type Timeframe } from "@/components/charts/chartConfig";
import { divergenceOverlays, positionPriceLines, type PriceLineSpec } from "@/components/charts/chartOverlays";
import { exitLadderOf } from "@/lib/market";
import { formatPrice, formatSignedPct } from "@/lib/utils";
import { COLOR_BLUE, COLOR_DOWN, COLOR_DOWN_CB, COLOR_UP, COLOR_UP_CB, SYMBOLS} from "@/lib/constants";
import { cn } from "@/lib/utils";
import { SignalsFeed } from "./SignalsFeed";
import { DepthChart } from "./DepthChart";
import { MarketDetails } from "./MarketDetails";

type ChartTab = "chart" | "funding" | "depth" | "signals" | "details";
type Indicator = IndicatorKind | "none";
type PriceMode = "last" | "mark";

const TABS = [
  { id: "chart", label: "Chart" },
  { id: "funding", label: "Funding" },
  { id: "depth", label: "Depth" },
  { id: "signals", label: "Signals" },
  { id: "details", label: "Details" },
] as const satisfies readonly { id: ChartTab; label: string }[];

interface ChartAreaProps {
  market: MarketView;
  timeframe: Timeframe;
  onTimeframe: (tf: Timeframe) => void;
  markers: TradeData[];
  positions: PositionData[];
  signals: SignalData[];
  /** the interval the venue could actually fill, when it is coarser than the one asked for */
  servedInterval?: string | null;
}

interface Elements {
  trades: boolean;
  positions: boolean;
  divergence: boolean;
}

/** Chart · Funding · Depth · Signals · Details with Strike's toolbar (spec §3.1). */
export function ChartArea({ market, timeframe, onTimeframe, markers, positions, signals, servedInterval }: ChartAreaProps) {
  const symbol = market.symbol;
  // NOT the hard-coded four: whether a market is streamed is something the bridge reports, and
  // every market has a chart now — the difference is only how it arrives (2026-09-04).
  const hasFeed = !useUnstreamed(symbol);
  // What resolution the store actually holds for this symbol. The engine streams 1 m bars; a market
  // it does not stream is fetched from the venue at the timeframe asked for, and a thin one comes
  // back coarser. The chart needs this or it buckets already-bucketed bars (2026-09-04).
  const sourceSeconds = hasFeed ? 60 : TF_SECONDS[(servedInterval as Timeframe) ?? timeframe] ?? TF_SECONDS[timeframe];
  const [tab, setTab] = useState<ChartTab>("chart");
  const [indicator, setIndicator] = useState<Indicator>("macd");
  const [priceMode, setPriceMode] = useState<PriceMode>("last");
  const [elements, setElements] = useState<Elements>({ trades: true, positions: true, divergence: true });
  const [mainChart, setMainChart] = useState<IChartApi | null>(null);
  const legendRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const colorBlind = useUiStore((s) => s.display.colorBlind);
  const upColor = colorBlind ? COLOR_UP_CB : COLOR_UP;
  const downColor = colorBlind ? COLOR_DOWN_CB : COLOR_DOWN;

  const priceLines = useMemo<PriceLineSpec[]>(() => {
    const lines = elements.positions ? positionPriceLines(positions, symbol) : [];
    if (priceMode === "mark" && market.mark > 0) {
      lines.push({ id: "mark", price: market.mark, color: COLOR_BLUE, title: "Mark", style: "dotted" });
    }
    return lines;
  }, [positions, symbol, elements.positions, priceMode, market.mark]);
  const overlays = useMemo(() => (elements.divergence ? divergenceOverlays(signals, symbol) : []), [signals, symbol, elements.divergence]);
  // The ladder legend: on a 5m chart the stops sit ~8 % below the visible candles, so the dashed
  // lines exist but are outside the auto-scaled price range — the operator still needs the numbers.
  const ladder = useMemo(() => {
    if (!elements.positions) return null;
    for (const p of positions) {
      if (p.symbol !== symbol) continue;
      const l = exitLadderOf(p);
      if (l) return l;
    }
    return null;
  }, [positions, symbol, elements.positions]);
  // How many of the bars on screen had NO trade. Strike writes a bar for every period from the
  // mark whether or not anything changed hands, so on a thin market most "candles" are a flat dash
  // — real data, but it reads as a broken chart unless the chart says what it is (Edgar, 2026-09-04).
  const flatShare = useMemo(() => {
    const c = useMarketStore.getState().candles[symbol] ?? [];
    if (c.length < 20) return 0;
    const tail = c.slice(-200);
    const flat = tail.filter((k) => k.high === k.low).length;
    return flat / tail.length;
  }, [symbol, market.price]);
  const tradeMarkers = elements.trades ? markers : [];
  const symbolSignals = useMemo(() => signals.filter((s) => s.symbol === symbol).length, [signals, symbol]);

  const screenshot = () => {
    if (!mainChart) return;
    try {
      const canvas = mainChart.takeScreenshot();
      const url = canvas.toDataURL("image/png");
      const a = document.createElement("a");
      a.href = url;
      a.download = `${symbol}-${timeframe}.png`;
      a.click();
    } catch (e) {
      console.warn("[chart] screenshot failed", e);
    }
  };
  const reset = () => {
    try {
      mainChart?.timeScale().resetTimeScale();
      mainChart?.timeScale().scrollToRealTime();
    } catch { /* disposed */ }
  };
  const fullscreen = () => {
    const el = panelRef.current;
    if (!el) return;
    if (document.fullscreenElement) void document.exitFullscreen();
    else void el.requestFullscreen?.();
  };

  const toolbar = (
    <div className="flex items-center gap-1.5 whitespace-nowrap">
      <div className="flex items-center gap-px">
        {TIMEFRAMES.map((tf) => (
          <button
            key={tf}
            type="button"
            aria-pressed={timeframe === tf}
            onClick={() => onTimeframe(tf)}
            className={cn("h-6 px-2 rounded-[6px] text-[12px] font-medium transition-colors", timeframe === tf ? "bg-active text-text" : "text-text-2 hover:text-text hover:bg-hover")}
          >
            {tf}
          </button>
        ))}
        <Popover width="w-28" trigger={(open) => <DropdownTrigger size="xs" open={open} label={MORE_TIMEFRAMES.includes(timeframe) ? timeframe : "More"} />}>
          {(close) => MORE_TIMEFRAMES.map((tf) => <MenuItem key={tf} active={timeframe === tf} onClick={() => { onTimeframe(tf); close(); }}>{tf}</MenuItem>)}
        </Popover>
      </div>
      <span className="w-px h-4 bg-hairline-strong" />
      <Popover width="w-40" trigger={(open) => <DropdownTrigger size="xs" open={open} label={priceMode === "mark" ? "Mark Price" : "Last Price"} />}>
        {(close) => (
          <>
            <MenuItem active={priceMode === "last"} onClick={() => { setPriceMode("last"); close(); }}>Last Price</MenuItem>
            <MenuItem active={priceMode === "mark"} onClick={() => { setPriceMode("mark"); close(); }} title="Adds the mark price line to the chart">Mark Price</MenuItem>
          </>
        )}
      </Popover>
      <Popover width="w-44" trigger={(open) => <DropdownTrigger size="xs" open={open} label={indicator === "none" ? "Indicators" : indicator === "rsi" ? "RSI 14" : "MACD"} />}>
        {(close) => (
          <>
            <MenuItem active={indicator === "none"} onClick={() => { setIndicator("none"); close(); }}>None</MenuItem>
            <MenuItem active={indicator === "rsi"} onClick={() => { setIndicator("rsi"); close(); }}>RSI 14</MenuItem>
            <MenuItem active={indicator === "macd"} onClick={() => { setIndicator("macd"); close(); }}>MACD 12 / 26 / 9</MenuItem>
          </>
        )}
      </Popover>
      <Popover width="w-52" trigger={(open) => <DropdownTrigger size="xs" open={open} label="Chart Elements" />}>
        <MenuLabel>Show</MenuLabel>
        <MenuItem onClick={() => setElements((e) => ({ ...e, trades: !e.trades }))}><Check on={elements.trades} /> Trade markers</MenuItem>
        <MenuItem onClick={() => setElements((e) => ({ ...e, positions: !e.positions }))}><Check on={elements.positions} /> Position lines (entry · exit ladder · SL · TP · liq)</MenuItem>
        <MenuItem onClick={() => setElements((e) => ({ ...e, divergence: !e.divergence }))}><Check on={elements.divergence} /> Divergence overlays</MenuItem>
      </Popover>
      <span className="w-px h-4 bg-hairline-strong" />
      <IconButton onClick={screenshot} title="Screenshot" aria-label="Screenshot" disabled={!mainChart}><Camera className="w-3.5 h-3.5" /></IconButton>
      <IconButton onClick={reset} title="Reset view" aria-label="Reset view" disabled={!mainChart}><RotateCcw className="w-3.5 h-3.5" /></IconButton>
      <IconButton onClick={fullscreen} title="Fullscreen" aria-label="Fullscreen"><Maximize2 className="w-3.5 h-3.5" /></IconButton>
    </div>
  );

  return (
    <div ref={panelRef} className="flex flex-col flex-1 min-h-0 min-w-0 bg-panel">
      <TabBar
        size="sm"
        tabs={TABS.map((t) => (t.id === "signals" && symbolSignals ? { ...t, count: symbolSignals } : t))}
        value={tab}
        onChange={setTab}
        right={tab === "chart" ? <div className="hidden md:block">{toolbar}</div> : undefined}
      />
      {tab === "chart" && (
        <div className="md:hidden flex items-center h-9 px-2 border-b border-hairline-soft overflow-x-auto scrollbar-none shrink-0">{toolbar}</div>
      )}

      {/* The engine streams four symbols over the socket; the other 27 get their candles, book and
          prints from the venue over REST instead, which refreshes every few seconds rather than
          tick by tick. Everything is here — say where it comes from rather than claim it is live
          tick data (2026-09-04). */}
      {tab === "chart" && (!hasFeed || flatShare > 0.5) && (
        <div className="px-3 py-1 border-b border-hairline-soft text-[12px] font-medium text-text-2 shrink-0 truncate"
             title={[
               !hasFeed ? `${symbol} is polled from the venue rather than streamed: the chart, book and tape refresh every few seconds instead of tick by tick. The numbers are Strike's own.` : "",
               servedInterval ? `This market does not trade often enough to fill a ${timeframe} bar, so the chart is drawn at ${servedInterval}.` : "",
               flatShare > 0.5 ? `${Math.round(flatShare * 100)} % of the bars on screen had no trade at all. Strike writes a bar for every period from the mark regardless, so those draw as a flat dash — that is the venue's data, not a rendering fault.` : "",
             ].filter(Boolean).join(" ")}>
          {!hasFeed && <>Polled from the venue{servedInterval ? <> · drawn at <span className="text-text font-semibold">{servedInterval}</span></> : null}</>}
          {!hasFeed && flatShare > 0.5 && " · "}
          {flatShare > 0.5 && <><span className="text-text font-semibold">{Math.round(flatShare * 100)} %</span> of these bars had no trade — flat by nature, not by fault</>}
        </div>
      )}
      {tab === "chart" && (
        <div className="flex flex-col flex-1 min-h-0">
          <ErrorBoundary fallback={<div className="flex flex-1 items-center justify-center text-text text-[13px] font-medium">Chart unavailable</div>}>
            <div className="relative flex-1 min-h-0">
              <div className="absolute inset-0">
                <CandlestickChart
                  className="w-full h-full"
                  symbol={symbol}
                  trades={tradeMarkers}
                  timeframe={timeframe}
                  sourceSeconds={sourceSeconds}
                  priceLines={priceLines}
                  overlays={overlays}
                  onChart={setMainChart}
                  upColor={upColor}
                  downColor={downColor}
                  legendRef={legendRef}
                />
              </div>
              {/* OHLC legend line — "BTC-USD · 5m  O H L C Δ" (updated from the crosshair, no state) */}
              <div className="absolute left-2 top-1.5 z-[2] flex items-center gap-2 max-w-[calc(100%-92px)] overflow-hidden text-[11.5px] font-medium num text-text pointer-events-none select-none whitespace-pre">
                <span className="font-semibold">{symbol} · {timeframe}</span>
                <span ref={legendRef} className="truncate" />
              </div>
              {(ladder || overlays.length > 0) && (
                <div className="absolute left-2 top-[26px] z-[2] w-fit max-w-[calc(100%-92px)] flex flex-col gap-0.5 rounded-[4px] bg-panel px-1 py-px pointer-events-none select-none">
                  {ladder && (
                    <span className="text-[11px] font-medium text-rose truncate">
                      exit ladder {formatPrice(ladder.first_exit)} → {formatPrice(ladder.full_exit)} · full exit {formatSignedPct(ladder.worst_case_pct ?? 0, 1)}
                    </span>
                  )}
                  {overlays.length > 0 && (
                    <span className="text-[11px] font-medium text-[#F472B6] truncate">
                      {overlays.map((o) => o.label).join(" · ")} divergence
                    </span>
                  )}
                </div>
              )}
            </div>
            {indicator !== "none" && (
              <div className="relative h-[26%] min-h-[96px] max-h-[180px] border-t border-hairline shrink-0">
                <IndicatorPane symbol={symbol} timeframe={timeframe} sourceSeconds={sourceSeconds} kind={indicator} mainChart={mainChart} />
              </div>
            )}
          </ErrorBoundary>
        </div>
      )}
      {tab === "funding" && <FundingChart symbol={symbol} />}
      {tab === "depth" && <DepthChart symbol={symbol} />}
      {tab === "signals" && <SignalsFeed signals={signals} symbol={symbol} />}
      {tab === "details" && <MarketDetails market={market} positions={positions} />}
    </div>
  );
}

function Check({ on }: { on: boolean }) {
  return <span className={cn("inline-flex items-center justify-center w-3.5 h-3.5 rounded-[3px] border", on ? "bg-mint border-mint text-bg" : "border-hairline-strong")}>{on && <span className="text-[10px] font-bold leading-none">✓</span>}</span>;
}
