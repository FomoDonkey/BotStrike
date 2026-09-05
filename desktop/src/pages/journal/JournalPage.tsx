import { useEffect, useMemo, useRef, useState } from "react";
import { History, Crosshair } from "lucide-react";
import { CandlestickChart, type PathSpec } from "@/components/charts/CandlestickChart";
import { positionPriceLines } from "@/components/charts/chartOverlays";
import { TF_SECONDS, type Timeframe } from "@/components/charts/chartConfig";
import { useTradeHistory } from "@/pages/trading/useTradeHistory";
import { useTradingStore } from "@/stores/tradingStore";
import { useVenueFallback } from "@/hooks/useVenueFallback";
import { buildEpisodes, episodeStats, type Episode } from "@/lib/tradeEpisodes";
import { Panel, PanelHeader, EmptyState } from "@/components/ui/Panel";
import { KpiCard } from "@/components/ui/KpiCard";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { Popover, MenuItem, DropdownTrigger } from "@/components/ui/Popover";
import { Chip, SideChip, StrategyTag, ExitReasonChip } from "@/components/ui/Chip";
import { COLOR_DOWN, COLOR_UP } from "@/lib/constants";
import { cn, formatDuration, formatMoney, formatPrice, formatSignedMoney, formatSignedPct, formatPct } from "@/lib/utils";

type Range = "30d" | "90d" | "all";
const TFS: readonly Timeframe[] = ["1h", "4h", "1d"];
const AMBER = "#F5B942";

function fmtTs(sec: number): string {
  return new Date(sec * 1000).toLocaleString(undefined, { month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function episodeReturn(e: Episode): number | null {
  const notional = e.entryPrice * e.qty;
  if (!(notional > 0)) return null;
  return (e.pnl + (e.open ? e.unrealized : 0)) / notional;
}

/** The path of one trade on the price: entry → adds / trims → exit (or the live mark while open). */
function episodePath(e: Episode, nowSec: number, highlighted: boolean): PathSpec {
  const pts: { time: number; price: number }[] = [];
  if (e.fills.length) {
    for (const f of e.fills) pts.push({ time: f.ts, price: f.price });
  } else {
    pts.push({ time: e.openTs, price: e.entryPrice });
  }
  if (e.open) {
    const mark = Number(e.position?.mark_price ?? 0) || e.entryPrice;
    pts.push({ time: nowSec, price: mark });
  }
  const total = e.pnl + (e.open ? e.unrealized : 0);
  return {
    id: e.id,
    color: total >= 0 ? COLOR_UP : COLOR_DOWN,
    width: highlighted ? 3 : 2,
    style: e.open ? "dashed" : "solid",
    points: pts,
  };
}

function EpisodeCard({ e, active, nowSec, onClick }: { e: Episode; active: boolean; nowSec: number; onClick: () => void }) {
  const total = e.pnl + (e.open ? e.unrealized : 0);
  const ret = episodeReturn(e);
  const trims = e.fills.filter((f) => f.kind === "trim").length;
  const adds = e.fills.filter((f) => f.kind === "add").length;
  const hold = (e.closeTs ?? nowSec) - e.openTs;
  const tone = total > 0 ? "text-mint" : total < 0 ? "text-rose" : "text-text";
  return (
    <button type="button" onClick={onClick}
      className={cn("w-full text-left rounded-[8px] border px-3 py-2.5 transition-colors",
        active ? "border-mint/60 bg-mint-soft/40" : "border-hairline bg-panel-2 hover:border-hairline-strong",
        "border-l-[3px]", total > 0 ? "border-l-mint" : total < 0 ? "border-l-rose" : "border-l-hairline-strong")}>
      <div className="flex items-center gap-2 min-w-0">
        <span className="text-[13px] font-semibold text-text">{e.symbol}</span>
        <SideChip side={e.long ? "LONG" : "SHORT"} size="xs" />
        <StrategyTag strategy={e.strategy} />
        <span className="ml-auto flex items-center gap-1.5">
          {e.open ? <Chip tone="mint" size="xs" dot>open</Chip> : <ExitReasonChip reason={e.exitReason ?? "close"} />}
        </span>
      </div>
      <div className="mt-1.5 flex items-baseline justify-between gap-2">
        <span className={cn("num text-[16px] font-semibold", tone)}>{formatSignedMoney(total)}</span>
        <span className={cn("num text-[12px] font-medium", tone)}>{ret === null ? "" : formatSignedPct(ret, 2)}</span>
      </div>
      <div className="mt-1 grid grid-cols-2 gap-x-3 gap-y-0.5 text-[11.5px] text-text-2">
        <span>Entry <span className="num text-text">{formatPrice(e.entryPrice)}</span></span>
        <span>{e.open ? "Mark" : "Exit"} <span className="num text-text">{formatPrice(e.open ? Number(e.position?.mark_price ?? 0) || e.entryPrice : e.exitPrice ?? 0)}</span></span>
        <span>Size <span className="num text-text">{e.qty.toLocaleString(undefined, { maximumFractionDigits: 6 })}</span></span>
        <span>Hold <span className="num text-text">{formatDuration(hold)}</span></span>
        <span>Fees <span className="num text-text">{formatMoney(e.fees)}</span></span>
        <span>Fills <span className="num text-text">{e.fills.length}</span>{trims ? <span className="text-amber"> · {trims} trim{trims > 1 ? "s" : ""}</span> : null}{adds ? <span> · {adds} add{adds > 1 ? "s" : ""}</span> : null}</span>
        <span className="col-span-2 text-text-3">{fmtTs(e.openTs)} → {e.closeTs ? fmtTs(e.closeTs) : "open"}</span>
        {e.open && e.ladder ? (
          <span className="col-span-2 text-text-3">Exit ladder <span className="num text-text-2">{formatPrice(e.ladder.first_exit)} → {formatPrice(e.ladder.full_exit)}</span> · {e.ladder.active}/{e.ladder.total} legs · worst {formatSignedPct(e.ladder.worst_case_pct ?? 0, 1)}</span>
        ) : null}
        {e.truncated ? <span className="col-span-2 text-text-3">entry fills older than the loaded history</span> : null}
      </div>
    </button>
  );
}

/** Journal: one large chart per market with every entry, add, trim and exit, the path of each trade
 *  coloured by its result, the live exit ladder, and the trade list that zooms the chart. */
export function JournalPage() {
  const { markers: fills, trades: rawTrades, loading } = useTradeHistory();
  const positionsBySymbol = useTradingStore((s) => s.positions);
  const positions = useMemo(() => Object.values(positionsBySymbol).flat(), [positionsBySymbol]);
  const [tick, setTick] = useState(() => Math.floor(Date.now() / 1000));
  useEffect(() => {
    const iv = setInterval(() => setTick(Math.floor(Date.now() / 1000)), 30_000);
    return () => clearInterval(iv);
  }, []);
  const episodes = useMemo(() => buildEpisodes(fills, positions, tick), [fills, positions, tick]);
  const markets = useMemo(() => {
    const by = new Map<string, { symbol: string; n: number; open: number; last: number; pnl: number }>();
    for (const e of episodes) {
      const m = by.get(e.symbol) ?? { symbol: e.symbol, n: 0, open: 0, last: 0, pnl: 0 };
      m.n += 1; m.open += e.open ? 1 : 0; m.last = Math.max(m.last, e.closeTs ?? tick); m.pnl += e.pnl + (e.open ? e.unrealized : 0);
      by.set(e.symbol, m);
    }
    return [...by.values()].sort((a, b) => b.open - a.open || b.last - a.last || a.symbol.localeCompare(b.symbol));
  }, [episodes, tick]);

  const [chosen, setSymbol] = useState<string>("");
  const symbol = chosen || markets[0]?.symbol || "";
  const [timeframe, setTimeframe] = useState<Timeframe>("4h");
  const [range, setRange] = useState<Range>("30d");
  const [show, setShow] = useState({ fills: true, paths: true, ladder: true });
  // a click's zoom and the selected trade belong to the market + timeframe they were made on:
  // switching either falls back to the range window without an effect
  const viewKey = `${symbol}|${timeframe}`;
  const [pick, setPick] = useState<{ key: string; id: string | null; focus: { from: number; to: number } | null } | null>(null);
  const selected = pick && pick.key === viewKey ? pick.id : null;
  const rangeDays = range === "30d" ? 30 : range === "90d" ? 90 : 400;
  const rangeFocus = useMemo(() => ({ from: tick - rangeDays * 86400, to: tick + TF_SECONDS[timeframe] * 3 }), [tick, rangeDays, timeframe]);
  const focus = pick && pick.key === viewKey && pick.focus ? pick.focus : rangeFocus;
  const { servedInterval } = useVenueFallback(symbol, timeframe);
  const legendRef = useRef<HTMLDivElement>(null);

  const symEpisodes = useMemo(() => episodes.filter((e) => e.symbol === symbol), [episodes, symbol]);
  const stats = useMemo(() => episodeStats(symEpisodes), [symEpisodes]);
  const allStats = useMemo(() => episodeStats(episodes), [episodes]);
  // funding settles on the positions, not on a fill: it is part of what the market cost or paid
  const funding = useMemo(() => {
    let all = 0, sym = 0;
    for (const t of rawTrades) {
      if (t.trade_type !== "FUNDING") continue;
      const v = Number(t.pnl) || 0;
      all += v;
      if (t.symbol === symbol) sym += v;
    }
    return { all, sym };
  }, [rawTrades, symbol]);
  const allNet = allStats.realised + allStats.unrealized + funding.all;
  const symNet = stats.realised + stats.unrealized + funding.sym;
  const trades = useMemo(() => (show.fills ? fills.filter((f) => f.symbol === symbol) : []), [fills, symbol, show.fills]);
  const priceLines = useMemo(() => (show.ladder ? positionPriceLines(positions, symbol) : []), [positions, symbol, show.ladder]);
  const paths = useMemo<PathSpec[]>(() => (show.paths ? symEpisodes.map((e) => episodePath(e, tick, selected === e.id)) : []),
    [symEpisodes, tick, selected, show.paths]);

  const focusEpisode = (e: Episode) => {
    const end = e.closeTs ?? tick;
    const span = Math.max(end - e.openTs, TF_SECONDS[timeframe] * 6);
    const pad = Math.max(span * 0.35, TF_SECONDS[timeframe] * 12);
    setPick({ key: viewKey, id: e.id, focus: { from: e.openTs - pad, to: end + pad } });
  };
  const applyRange = (r: Range) => {
    setRange(r);
    setPick(null);
  };

  const pf = stats.profitFactor;
  const winRate = stats.winRate;

  return (
    <div className="flex flex-col gap-3 p-3 sm:p-4 min-w-0">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-[18px] font-semibold text-text flex items-center gap-2"><History className="w-5 h-5 text-mint" /> Journal</h1>
        <span className="text-[12px] text-text-2">every fill on the chart · {allStats.closed} round trip{allStats.closed === 1 ? "" : "s"} closed · {allStats.trims} trim{allStats.trims === 1 ? "" : "s"} · {allStats.open} open · net <span className={cn("num font-semibold", allNet >= 0 ? "text-mint" : "text-rose")} title="realised (exits and trims) + open PnL + funding: the same figure as the account">{formatSignedMoney(allNet)}</span> across {markets.length} market{markets.length === 1 ? "" : "s"}</span>
        <div className="ml-auto flex flex-wrap items-center gap-1.5">
          <Popover align="right" width="w-56" trigger={(open) => <DropdownTrigger size="xs" open={open} label={symbol || "Market"} />}>
            {(close) => (
              <>
                {markets.map((m) => (
                  <MenuItem key={m.symbol} active={m.symbol === symbol} onClick={() => { setSymbol(m.symbol); close(); }}>
                    <span className="flex items-center justify-between w-full gap-2">
                      <span>{m.symbol}{m.open ? <span className="ml-1.5 text-mint">●</span> : null}</span>
                      <span className={cn("num text-[11px]", m.pnl >= 0 ? "text-mint" : "text-rose")}>{formatSignedMoney(m.pnl)} · {m.n}</span>
                    </span>
                  </MenuItem>
                ))}
                {!markets.length && <MenuItem disabled>No trades yet</MenuItem>}
              </>
            )}
          </Popover>
          <SegmentedControl size="sm" value={timeframe} onChange={(v) => setTimeframe(v)} options={TFS.map((t) => ({ id: t, label: t }))} />
          <SegmentedControl size="sm" value={range} onChange={applyRange} options={[{ id: "30d", label: "30d" }, { id: "90d", label: "90d" }, { id: "all", label: "All" }]} />
          <Popover align="right" width="w-56" trigger={(open) => <DropdownTrigger size="xs" open={open} label="Layers" />}>
            {() => (
              <>
                <MenuItem onClick={() => setShow((s) => ({ ...s, fills: !s.fills }))}><span className={cn("mr-2", show.fills ? "text-mint" : "text-text-3")}>●</span> Fills (entry · add · trim · exit)</MenuItem>
                <MenuItem onClick={() => setShow((s) => ({ ...s, paths: !s.paths }))}><span className={cn("mr-2", show.paths ? "text-mint" : "text-text-3")}>●</span> Trade paths, coloured by result</MenuItem>
                <MenuItem onClick={() => setShow((s) => ({ ...s, ladder: !s.ladder }))}><span className={cn("mr-2", show.ladder ? "text-mint" : "text-text-3")}>●</span> Open position: entry + exit ladder</MenuItem>
              </>
            )}
          </Popover>
        </div>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 xl:grid-cols-8 gap-2">
        <KpiCard label="Net PnL" hint="Realised (every exit and trim) + open PnL + funding on this market: the account's own figure for it" value={<span className={symNet >= 0 ? "text-mint" : "text-rose"}>{formatSignedMoney(symNet)}</span>} sub={`realised ${formatSignedMoney(stats.realised)} · funding ${formatSignedMoney(funding.sym)}`} />
        <KpiCard label="Open PnL" hint="Mark-to-market of the open position(s) on this market" value={<span className={stats.unrealized >= 0 ? "text-mint" : "text-rose"}>{formatSignedMoney(stats.unrealized)}</span>} sub={`${stats.open} open`} />
        <KpiCard label="Round trips" hint="Positions opened and flattened. A rebalance trim realises money but is not a round trip" value={stats.closed} sub={`${stats.trims} trim${stats.trims === 1 ? "" : "s"} realised`} />
        <KpiCard label="Win rate" hint="Round trips with a positive net PnL" value={winRate === null ? "---" : formatPct(winRate, 0)} sub={winRate === null ? "no round trip closed yet" : `${stats.wins} of ${stats.closed}`} />
        <KpiCard label="Profit factor" hint="Gross wins / gross losses of the closed round trips" value={pf === null ? (stats.grossWins > 0 ? "∞" : "---") : pf.toFixed(2)} sub={`+${formatMoney(stats.grossWins)} / −${formatMoney(stats.grossLosses)}`} />
        <KpiCard label="Avg hold" hint="Average time from entry to the exit that flattened the position (round trips)" value={stats.avgHoldSec === null ? "---" : formatDuration(stats.avgHoldSec)} />
        <KpiCard label="Best / worst" hint="Best and worst closed round trips" value={stats.best ? <span className="text-mint">{formatSignedMoney(stats.best.pnl)}</span> : "---"} sub={stats.worst ? <span className={stats.worst.pnl < 0 ? "text-rose" : undefined}>worst {formatSignedMoney(stats.worst.pnl)}</span> : undefined} />
        <KpiCard label="Fees" hint="Venue fees on this market: the round-trip fee of every exit and trim, plus the entry fee accrued on what is still open" value={formatMoney(stats.fees)} />
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_380px] gap-3 min-w-0">
        <Panel className="relative min-w-0 overflow-hidden">
          <PanelHeader dense title={<span className="flex items-center gap-2">{symbol || "—"} <span className="text-text-2 font-medium">· {servedInterval ?? timeframe}{servedInterval ? <span className="text-amber"> (the venue served {servedInterval} bars)</span> : null}</span></span>}
            right={<span className="flex items-center gap-2 text-[11px] text-text-2">
              <span className="inline-flex items-center gap-1"><span className="text-mint">▲</span> long entry</span>
              <span className="inline-flex items-center gap-1"><span className="text-rose">▼</span> short entry</span>
              <span className="inline-flex items-center gap-1"><span className="text-mint">●</span> exit +</span>
              <span className="inline-flex items-center gap-1"><span className="text-rose">●</span> exit −</span>
              <span className="inline-flex items-center gap-1"><span style={{ color: AMBER }}>■</span> trim</span>
              <span className="inline-flex items-center gap-1 text-text-3">— path · - - open</span>
            </span>} />
          <div className="relative h-[58vh] min-h-[420px]">
            <div ref={legendRef} className="pointer-events-none absolute left-2 top-1.5 z-10 num text-[11px] font-medium text-text-2 whitespace-nowrap" />
            {symbol ? (
              <CandlestickChart symbol={symbol} timeframe={timeframe} trades={trades} paths={paths} focus={focus} priceLines={priceLines} legendRef={legendRef} legendRows={1} className="h-full" />
            ) : (
              <EmptyState sub="Fills appear here as soon as the book trades">{loading ? "Loading fills…" : "No trades yet"}</EmptyState>
            )}
          </div>
        </Panel>

        <Panel className="flex flex-col overflow-hidden xl:max-h-[calc(58vh+44px)]">
          <PanelHeader dense title="Trades" right={<span className="text-[11px] text-text-2 inline-flex items-center gap-1"><Crosshair className="w-3.5 h-3.5" /> click to zoom the chart</span>} />
          <div className="flex-1 overflow-y-auto p-2 flex flex-col gap-2">
            {symEpisodes.length ? symEpisodes.map((e) => (
              <EpisodeCard key={e.id} e={e} active={selected === e.id} nowSec={tick} onClick={() => focusEpisode(e)} />
            )) : <EmptyState sub="Every round trip on this market will be listed here">No trades on {symbol || "this market"}</EmptyState>}
          </div>
        </Panel>
      </div>
    </div>
  );
}
