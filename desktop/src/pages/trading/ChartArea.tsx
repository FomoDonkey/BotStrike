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
import { Popover, MenuItem, DropdownTrigger, MenuLabel, MenuDivider } from "@/components/ui/Popover";
import { FundingChart } from "@/components/ui/FundingChart";
import { CandlestickChart } from "@/components/charts/CandlestickChart";
import { IndicatorPane } from "@/components/charts/IndicatorPane";
import { INDICATOR_BY_ID, MAX_PANES, OVERLAY_DEFS, PANE_DEFS, type IndicatorDef } from "@/components/charts/chartIndicators";
import { MORE_TIMEFRAMES, TIMEFRAMES, type Timeframe } from "@/components/charts/chartConfig";
import { divergenceOverlays, positionPriceLines, type PriceLineSpec } from "@/components/charts/chartOverlays";
import { readChartCandles } from "@/lib/chartData";
import { exitLadderOf } from "@/lib/market";
import { formatPrice, formatSignedPct } from "@/lib/utils";
import { COLOR_BLUE, COLOR_DOWN, COLOR_DOWN_CB, COLOR_UP, COLOR_UP_CB } from "@/lib/constants";
import { cn } from "@/lib/utils";
import { SignalsFeed } from "./SignalsFeed";
import { DepthChart } from "./DepthChart";
import { MarketDetails } from "./MarketDetails";

type ChartTab = "chart" | "funding" | "depth" | "signals" | "details";
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

/** Which indicators are on, remembered per browser like the symbol and the timeframe. */
interface IndicatorPrefs {
  overlays: string[];
  panes: string[];
}

const IND_KEY = "botstrike.chart.indicators";
// The book's live strategy is a Donchian channel, so the channel is the one line that belongs on
// the price by default; MACD keeps the pane Strike opens with.
const DEFAULT_INDICATORS: IndicatorPrefs = { overlays: ["dc20"], panes: ["macd"] };

function loadIndicators(): IndicatorPrefs {
  try {
    const raw = localStorage.getItem(IND_KEY);
    if (raw) {
      const p = JSON.parse(raw) as Partial<IndicatorPrefs>;
      const known = (ids: unknown, place: IndicatorDef["place"]) =>
        Array.isArray(ids) ? ids.filter((id): id is string => typeof id === "string" && INDICATOR_BY_ID[id]?.place === place) : [];
      return { overlays: known(p.overlays, "overlay"), panes: known(p.panes, "pane").slice(0, MAX_PANES) };
    }
  } catch { /* corrupt / unavailable storage */ }
  return { overlays: [...DEFAULT_INDICATORS.overlays], panes: [...DEFAULT_INDICATORS.panes] };
}

function saveIndicators(p: IndicatorPrefs) {
  try { localStorage.setItem(IND_KEY, JSON.stringify(p)); } catch { /* ignore */ }
}

function groupBy(defs: IndicatorDef[]): [string, IndicatorDef[]][] {
  const out = new Map<string, IndicatorDef[]>();
  for (const d of defs) out.set(d.group, [...(out.get(d.group) ?? []), d]);
  return [...out.entries()];
}

/** Chart · Funding · Depth · Signals · Details with Strike's toolbar (spec §3.1). */
export function ChartArea({ market, timeframe, onTimeframe, markers, positions, signals, servedInterval }: ChartAreaProps) {
  const symbol = market.symbol;
  // NOT the hard-coded four: whether a market is streamed is something the bridge reports, and
  // every market has a chart now — the difference is only how it arrives (2026-09-04).
  const hasFeed = !useUnstreamed(symbol);
  const [tab, setTab] = useState<ChartTab>("chart");
  const [indicators, setIndicators] = useState<IndicatorPrefs>(loadIndicators);
  const [priceMode, setPriceMode] = useState<PriceMode>("last");
  const [elements, setElements] = useState<Elements>({ trades: true, positions: true, divergence: true });
  const [mainChart, setMainChart] = useState<IChartApi | null>(null);
  const legendRef = useRef<HTMLDivElement>(null);
  const overlayLegendRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const colorBlind = useUiStore((s) => s.display.colorBlind);
  const upColor = colorBlind ? COLOR_UP_CB : COLOR_UP;
  const downColor = colorBlind ? COLOR_DOWN_CB : COLOR_DOWN;

  const overlayDefs = useMemo(() => indicators.overlays.map((id) => INDICATOR_BY_ID[id]).filter(Boolean), [indicators.overlays]);
  const paneDefs = useMemo(() => indicators.panes.map((id) => INDICATOR_BY_ID[id]).filter(Boolean), [indicators.panes]);
  const indicatorCount = overlayDefs.length + paneDefs.length;

  const toggleIndicator = (def: IndicatorDef) => {
    setIndicators((p) => {
      const key = def.place === "overlay" ? "overlays" : "panes";
      const on = p[key].includes(def.id);
      let list = on ? p[key].filter((id) => id !== def.id) : [...p[key], def.id];
      if (key === "panes") list = list.slice(-MAX_PANES);
      const next = { ...p, [key]: list };
      saveIndicators(next);
      return next;
    });
  };
  const clearIndicators = () => {
    const next = { overlays: [], panes: [] };
    saveIndicators(next);
    setIndicators(next);
  };

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
    const { candles } = readChartCandles(useMarketStore.getState(), symbol, timeframe);
    // re-read on every price move: the bars on screen change with it
    if (candles.length < 20 || !Number.isFinite(market.price)) return 0;
    const tail = candles.slice(-200);
    const flat = tail.filter((k) => k.high === k.low).length;
    return flat / tail.length;
  }, [symbol, timeframe, market.price]);
  const tradeMarkers = elements.trades ? markers : [];
  const symbolSignals = useMemo(() => signals.filter((s) => s.symbol === symbol).length, [signals, symbol]);

  // Notes about the bars themselves, in the legend rather than on a row of their own: the row cost
  // the chart 26 px on every thin market, and the chart is the thing being starved of height.
  const notes: { text: string; title: string }[] = [];
  if (!hasFeed) {
    notes.push({
      text: servedInterval ? `polled from the venue · drawn at ${servedInterval}` : "polled from the venue",
      title: `${symbol} is polled from the venue rather than streamed: the chart, book and tape refresh every few seconds instead of tick by tick. The numbers are Strike's own.${servedInterval ? ` This market does not trade often enough to fill a ${timeframe} bar, so the chart is drawn at ${servedInterval}.` : ""}`,
    });
  }
  if (flatShare > 0.5) {
    notes.push({
      text: `${Math.round(flatShare * 100)} % of these bars had no trade — flat by nature, not by fault`,
      title: `${Math.round(flatShare * 100)} % of the bars on screen had no trade at all. Strike writes a bar for every period from the mark regardless, so those draw as a flat dash — that is the venue's data, not a rendering fault.`,
    });
  }
  const legendRows = 1 + (overlayDefs.length ? 1 : 0) + (ladder || overlays.length ? 1 : 0) + (notes.length ? 1 : 0);
  // Pane heights: one pane takes a quarter of the panel, three take half between them, and the
  // price keeps the rest. Strike's default is 219 / 110 / 109 px at this window height.
  const paneHeight = paneDefs.length <= 1 ? 25 : paneDefs.length === 2 ? 21 : 17;

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

  const indicatorItem = (def: IndicatorDef) => {
    const on = def.place === "overlay" ? indicators.overlays.includes(def.id) : indicators.panes.includes(def.id);
    const full = def.place === "pane" && !on && indicators.panes.length >= MAX_PANES;
    return (
      <MenuItem
        key={def.id}
        onClick={() => toggleIndicator(def)}
        disabled={full}
        title={full ? `Up to ${MAX_PANES} panes at a time — remove one first` : `engine column: ${def.engine}`}
      >
        <Check on={on} />
        <span className="flex-1 truncate">{def.label}</span>
        <span className="num text-[10.5px] text-text-2 truncate max-w-[40%]">{def.engine}</span>
      </MenuItem>
    );
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
      <Popover width="w-80" trigger={(open) => <DropdownTrigger size="xs" open={open} label={indicatorCount ? `Indicators · ${indicatorCount}` : "Indicators"} />}>
        <MenuLabel>On the price</MenuLabel>
        {groupBy(OVERLAY_DEFS).map(([group, defs]) => (
          <div key={group}>
            <div className="px-3 pt-1 pb-0.5 text-[10.5px] font-medium text-text-2">{group}</div>
            {defs.map(indicatorItem)}
          </div>
        ))}
        <MenuDivider />
        <MenuLabel>Panes below · up to {MAX_PANES}</MenuLabel>
        {groupBy(PANE_DEFS).map(([group, defs]) => (
          <div key={group}>
            <div className="px-3 pt-1 pb-0.5 text-[10.5px] font-medium text-text-2">{group}</div>
            {defs.map(indicatorItem)}
          </div>
        ))}
        <MenuDivider />
        <MenuItem onClick={clearIndicators} disabled={!indicatorCount} tone="rose">Clear all</MenuItem>
      </Popover>
      <Popover width="w-64" trigger={(open) => <DropdownTrigger size="xs" open={open} label="Chart Elements" />}>
        <MenuLabel>Show</MenuLabel>
        <MenuItem onClick={() => setElements((e) => ({ ...e, trades: !e.trades }))}><Check on={elements.trades} /> Trade markers</MenuItem>
        <MenuItem onClick={() => setElements((e) => ({ ...e, positions: !e.positions }))} className="h-auto py-1.5">
          <Check on={elements.positions} />
          <span className="flex flex-col leading-tight">
            <span>Position lines</span>
            <span className="text-[11px] text-text-2">entry · exit ladder · SL · TP · liq</span>
          </span>
        </MenuItem>
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
                  priceLines={priceLines}
                  overlays={overlays}
                  overlayDefs={overlayDefs}
                  onChart={setMainChart}
                  upColor={upColor}
                  downColor={downColor}
                  legendRef={legendRef}
                  overlayLegendRef={overlayLegendRef}
                  legendRows={legendRows}
                />
              </div>
              {/* Legend block, Strike-style: "BTC-USD · 5m  O H L C Δ" on the first row, the
                  overlays' values on the second, the position ladder / divergence on the third
                  and any note about the bars on the last — all written from the crosshair, no state.
                  The chart keeps its candles below these rows (legendRows → scale margin). */}
              <div className="absolute left-2 top-1.5 z-[2] flex flex-col gap-0.5 max-w-[calc(100%-92px)] pointer-events-none select-none">
                <div className="flex items-center gap-2 overflow-hidden text-[11.5px] font-medium num text-text whitespace-pre">
                  <span className="font-semibold">{symbol} · {timeframe}</span>
                  <span ref={legendRef} className="truncate" />
                </div>
                <div ref={overlayLegendRef} className={cn("text-[11px] font-medium num text-text whitespace-pre truncate", !overlayDefs.length && "hidden")} />
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
                {notes.length > 0 && (
                  <span className="text-[11px] font-medium text-text-2 truncate pointer-events-auto" title={notes.map((n) => n.title).join(" ")}>
                    {notes.map((n) => n.text).join(" · ")}
                  </span>
                )}
              </div>
            </div>
            {paneDefs.map((def) => (
              <div key={def.id} className="relative border-t border-hairline shrink-0" style={{ height: `${paneHeight}%`, minHeight: 76, maxHeight: 150 }}>
                <IndicatorPane symbol={symbol} timeframe={timeframe} def={def} mainChart={mainChart} onRemove={() => toggleIndicator(def)} />
              </div>
            ))}
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
  return <span className={cn("inline-flex items-center justify-center w-3.5 h-3.5 shrink-0 rounded-[3px] border", on ? "bg-mint border-mint text-bg" : "border-hairline-strong")}>{on && <span className="text-[10px] font-bold leading-none">✓</span>}</span>;
}
