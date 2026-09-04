import { useEffect, useMemo, useRef, useState } from "react";
import { AlignJustify, AlignVerticalJustifyEnd, AlignVerticalJustifyStart } from "lucide-react";
import { useMarketStore, type OrderBookLevel } from "@/stores/marketStore";
import { useUnstreamed } from "@/hooks/useVenueMarkets";
import { useFlashOnChange } from "@/hooks/useFlash";
import { SYMBOL_LABELS } from "@/lib/constants";
import { cn, formatPrice, formatSize } from "@/lib/utils";
import { Hint } from "@/components/shared/Hint";
import { HINTS } from "@/lib/hints";
import { TabBar } from "@/components/ui/TabBar";
import { IconButton } from "@/components/ui/Button";
import { Popover, MenuItem, DropdownTrigger } from "@/components/ui/Popover";
import { EmptyState } from "@/components/ui/Panel";
import { TradesTape } from "./TradesTape";

type BookTab = "book" | "trades";
type BookLayout = "both" | "bids" | "asks";

interface Row {
  price: number;
  size: number;
  total: number;
}

const MAX_LEVELS = 10; // the bridge broadcasts 10 per side
const ROW_PX = 22;
const MID_PX = 40;

/** Group raw levels by a price step (precision selector), then accumulate totals. */
function group(levels: OrderBookLevel[], step: number, side: "ask" | "bid"): Row[] {
  const buckets = new Map<number, number>();
  for (const l of levels) {
    const p = step > 0 ? (side === "ask" ? Math.ceil(l.price / step) * step : Math.floor(l.price / step) * step) : l.price;
    buckets.set(p, (buckets.get(p) ?? 0) + (l.quantity || 0));
  }
  const sorted = [...buckets.entries()].sort((a, b) => (side === "ask" ? a[0] - b[0] : b[0] - a[0]));
  let total = 0;
  const out: Row[] = [];
  for (const [price, size] of sorted) {
    total += size;
    out.push({ price, size, total });
  }
  return out;
}

/** Precision steps relative to the price magnitude (0 = raw ticks). */
function stepsFor(price: number): number[] {
  if (price >= 10000) return [0, 1, 10, 50];
  if (price >= 1000) return [0, 0.1, 1, 5];
  if (price >= 10) return [0, 0.01, 0.1, 0.5];
  return [0, 0.0001, 0.001, 0.01];
}

function stepLabel(step: number): string {
  return step === 0 ? "Raw" : step >= 1 ? String(step) : step.toString();
}

/** Levels per side that fit the list box, so asks · mid · bids are always on screen. */
function useLevelsThatFit(ref: React.RefObject<HTMLDivElement | null>, layout: BookLayout): number {
  const [levels, setLevels] = useState(MAX_LEVELS);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const obs = new ResizeObserver((entries) => {
      const h = entries[0].contentRect.height;
      if (h <= 0) return;
      const n = layout === "both" ? Math.floor((h - MID_PX) / 2 / ROW_PX) : Math.floor((h - MID_PX) / ROW_PX);
      setLevels(Math.max(3, Math.min(MAX_LEVELS, n)));
    });
    obs.observe(el);
    return () => obs.disconnect();
  }, [ref, layout]);
  return levels;
}

/** Order Book · Trades column (spec §3.1): layout toggles, precision, depth bars, mid + spread, ratio bar. */
export function OrderBookColumn({ symbol, className }: { symbol: string; className?: string }) {
  const [tab, setTab] = useState<BookTab>("book");
  return (
    <div className={cn("flex flex-col min-h-0 min-w-0 bg-panel", className)}>
      <TabBar size="sm" tabs={[{ id: "book", label: "Order Book" }, { id: "trades", label: "Trades" }]} value={tab} onChange={setTab} />
      {tab === "book" ? <OrderBook symbol={symbol} /> : <TradesTape symbol={symbol} />}
    </div>
  );
}

function OrderBook({ symbol }: { symbol: string }) {
  const unstreamed = useUnstreamed(symbol);
  const ob = useMarketStore((s) => s.orderbooks[symbol]);
  const price = useMarketStore((s) => s.prices[symbol] || 0);
  const prev = useMarketStore((s) => s.prevPrices[symbol] || 0);
  const base = SYMBOL_LABELS[symbol] ?? "";
  const [layout, setLayout] = useState<BookLayout>("both");
  const [step, setStep] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);
  const LEVELS = useLevelsThatFit(listRef, layout);
  const steps = stepsFor(ob?.mid_price ?? price);
  const effStep = steps.includes(step) ? step : 0;

  const { asks, bids, maxTotal, bidQty, askQty } = useMemo(() => {
    const a = group(ob?.asks ?? [], effStep, "ask").slice(0, LEVELS);
    const b = group(ob?.bids ?? [], effStep, "bid").slice(0, LEVELS);
    const maxT = Math.max(a[a.length - 1]?.total ?? 0, b[b.length - 1]?.total ?? 0, 1e-9);
    return { asks: [...a].reverse(), bids: b, maxTotal: maxT, bidQty: b[b.length - 1]?.total ?? 0, askQty: a[a.length - 1]?.total ?? 0 };
  }, [ob, LEVELS, effStep]);

  const mid = ob?.mid_price ?? (price || null);
  const midRef = useRef<HTMLSpanElement>(null);
  useFlashOnChange(midRef, mid ?? null, "text-mint", "text-rose", 300);
  const up = price >= prev;
  const spreadAbs = ob?.spread ?? null;
  const spreadBps = ob?.spread_bps ?? null;
  const total = bidQty + askQty;
  const bidPct = total > 0 ? (bidQty / total) * 100 : 50;
  const empty = !ob || (asks.length === 0 && bids.length === 0);

  return (
    <div className="flex flex-col flex-1 min-h-0 text-[12.5px]">
      <div className="flex items-center gap-1 px-2 h-9 border-b border-hairline-soft shrink-0">
        <IconButton active={layout === "both"} onClick={() => setLayout("both")} title="Bids and asks" aria-label="Bids and asks"><AlignJustify className="w-3.5 h-3.5" /></IconButton>
        <IconButton active={layout === "bids"} onClick={() => setLayout("bids")} title="Bids only" aria-label="Bids only"><AlignVerticalJustifyEnd className="w-3.5 h-3.5 text-mint" /></IconButton>
        <IconButton active={layout === "asks"} onClick={() => setLayout("asks")} title="Asks only" aria-label="Asks only"><AlignVerticalJustifyStart className="w-3.5 h-3.5 text-rose" /></IconButton>
        <Popover align="right" width="w-28" className="ml-auto" trigger={(open) => <DropdownTrigger size="xs" open={open} label={stepLabel(effStep)} />}>
          {(close) => steps.map((s) => <MenuItem key={s} active={s === effStep} onClick={() => { setStep(s); close(); }}>{stepLabel(s)}</MenuItem>)}
        </Popover>
      </div>
      <div className="grid grid-cols-3 px-2 h-7 items-center text-[11px] font-medium uppercase tracking-[0.04em] text-text-2 border-b border-hairline-soft shrink-0">
        <span>Price (USD)</span>
        <span className="text-right">Size ({base})</span>
        <span className="text-right">Total ({base})</span>
      </div>
      {empty ? (
        <div ref={listRef} className="flex-1 flex"><EmptyState>{unstreamed ? "Not streamed for this market" : "Waiting for order book…"}</EmptyState></div>
      ) : (
        <div ref={listRef} className="flex-1 min-h-0 overflow-hidden flex flex-col">
          {layout !== "bids" && <div className={cn(layout === "asks" && "flex-1 flex flex-col justify-end")}>{asks.map((r) => <Level key={`a${r.price}`} row={r} side="ask" maxTotal={maxTotal} />)}</div>}
          <div className="flex items-center justify-between px-2 h-10 border-y border-hairline-soft bg-panel-2 shrink-0">
            <span ref={midRef} className={cn("num text-[20px] font-semibold transition-colors leading-none", up ? "text-mint" : "text-rose")}>
              {mid ? formatPrice(mid) : "---"} <span className="text-[13px]">{up ? "↑" : "↓"}</span>
            </span>
            <span className="text-[11.5px] font-medium text-text-2 num text-right leading-tight">
              <Hint title={"The ladder and this spread are the PRICE FEED's book (Binance), which is what the strategies read — not the venue's. What an order on Strike actually pays is the Spread in the market header and the Details tab."}>Spread (feed)</Hint>: <span className="text-text">{spreadAbs !== null ? spreadAbs.toFixed(spreadAbs >= 1 ? 2 : 3) : "---"}</span>
              {spreadBps !== null && <span> / <span className="text-text">{spreadBps < 1 ? spreadBps.toFixed(3) : spreadBps.toFixed(2)} bps</span></span>}
            </span>
          </div>
          {layout !== "asks" && <div>{bids.map((r) => <Level key={`b${r.price}`} row={r} side="bid" maxTotal={maxTotal} />)}</div>}
        </div>
      )}
      <div className="px-2 py-2 border-t border-hairline-soft shrink-0" title={`Bid depth ${formatSize(bidQty)} ${base} vs ask depth ${formatSize(askQty)} ${base} over the top ${LEVELS} levels`}>
        <div className="flex items-center gap-2 text-[11.5px] font-semibold leading-4 num">
          <span className="text-mint whitespace-nowrap">B {bidPct.toFixed(2)}%</span>
          <div className="flex flex-1 h-1.5 rounded-sm overflow-hidden bg-rose/40">
            <div className="bg-mint h-full" style={{ width: `${bidPct}%` }} />
          </div>
          <span className="text-rose whitespace-nowrap">{(100 - bidPct).toFixed(2)}% S</span>
        </div>
      </div>
    </div>
  );
}

function Level({ row, side, maxTotal }: { row: Row; side: "ask" | "bid"; maxTotal: number }) {
  const w = Math.min(100, (row.total / maxTotal) * 100);
  return (
    <div className="relative grid grid-cols-3 px-2 h-[22px] items-center num hover:bg-hover">
      <div className={cn("absolute inset-y-[2px] right-0 pointer-events-none", side === "ask" ? "bg-rose/20" : "bg-mint/20")} style={{ width: `${w}%` }} />
      <span className={cn("relative font-medium", side === "ask" ? "text-rose" : "text-mint")}>{formatPrice(row.price)}</span>
      <span className="relative text-right font-medium text-text">{formatSize(row.size)}</span>
      <span className="relative text-right font-medium text-text">{formatSize(row.total)}</span>
    </div>
  );
}
