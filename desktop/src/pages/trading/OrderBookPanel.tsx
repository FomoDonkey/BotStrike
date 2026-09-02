import { useEffect, useMemo, useRef, useState } from "react";
import { useMarketStore, type OrderBookLevel } from "@/stores/marketStore";
import { useFlashOnChange } from "@/hooks/useFlash";
import { SYMBOL_LABELS } from "@/lib/constants";
import { cn, formatPrice, formatSize } from "@/lib/utils";
import { Hint } from "@/components/shared/Hint";
import { HINTS } from "@/lib/hints";

interface Row {
  price: number;
  size: number;
  total: number;
}

function cumulative(levels: OrderBookLevel[]): Row[] {
  let total = 0;
  const out: Row[] = [];
  for (const l of levels) {
    const size = l.quantity || 0;
    total += size;
    out.push({ price: l.price, size, total });
  }
  return out;
}

const MAX_LEVELS = 10;   // the bridge broadcasts 10 per side
const ROW_PX = 22;
const MID_PX = 32;

/**
 * Levels per side that fit the list box, so asks · mid · bids are ALWAYS on screen (a fixed
 * 10 + 1 + 10 used to scroll the mid row and every bid out of a 900 px viewport).
 */
function useLevelsThatFit(ref: React.RefObject<HTMLDivElement | null>): number {
  const [levels, setLevels] = useState(MAX_LEVELS);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const obs = new ResizeObserver((entries) => {
      const h = entries[0].contentRect.height;
      if (h <= 0) return;
      const n = Math.floor((h - MID_PX) / 2 / ROW_PX);
      setLevels(Math.max(3, Math.min(MAX_LEVELS, n)));
    });
    obs.observe(el);
    return () => obs.disconnect();
  }, [ref]);
  return levels;
}

/** Price · Size · Total with cumulative depth bars, mid + spread, and the bid/ask ratio bar. */
export function OrderBookPanel({ symbol }: { symbol: string }) {
  const ob = useMarketStore((s) => s.orderbooks[symbol]);
  const price = useMarketStore((s) => s.prices[symbol] || 0);
  const prev = useMarketStore((s) => s.prevPrices[symbol] || 0);
  const base = SYMBOL_LABELS[symbol] ?? "";
  const listRef = useRef<HTMLDivElement>(null);
  const LEVELS = useLevelsThatFit(listRef);

  const { asks, bids, maxTotal, bidQty, askQty } = useMemo(() => {
    const asksRaw = (ob?.asks ?? []).slice(0, LEVELS);
    const bidsRaw = (ob?.bids ?? []).slice(0, LEVELS);
    const a = cumulative(asksRaw);
    const b = cumulative(bidsRaw);
    const maxT = Math.max(a[a.length - 1]?.total ?? 0, b[b.length - 1]?.total ?? 0, 1e-9);
    return {
      asks: [...a].reverse(), // best ask nearest the mid row
      bids: b,
      maxTotal: maxT,
      bidQty: b[b.length - 1]?.total ?? 0,
      askQty: a[a.length - 1]?.total ?? 0,
    };
  }, [ob, LEVELS]);

  const mid = ob?.mid_price ?? (price || null);
  const midRef = useRef<HTMLSpanElement>(null);
  useFlashOnChange(midRef, mid ?? null, "text-profit", "text-loss", 300);
  const up = price >= prev;
  const spreadAbs = ob?.spread ?? null;
  const spreadBps = ob?.spread_bps ?? null;
  const total = bidQty + askQty;
  const bidPct = total > 0 ? (bidQty / total) * 100 : 50;

  const empty = !ob || (asks.length === 0 && bids.length === 0);

  return (
    <div className="flex flex-col flex-1 min-h-0 text-[12px]">
      <div className="grid grid-cols-3 px-2 h-7 items-center text-[10.5px] uppercase tracking-[0.06em] text-text-muted border-b border-hairline-soft shrink-0">
        <span>Price (USD)</span>
        <span className="text-right">Size ({base})</span>
        <span className="text-right">Total ({base})</span>
      </div>
      {empty ? (
        <div ref={listRef} className="flex-1 flex items-center justify-center text-text-faint text-xs">Waiting for order book…</div>
      ) : (
      <div ref={listRef} className="flex-1 min-h-0 overflow-hidden">
        {asks.map((r) => <Level key={`a${r.price}`} row={r} side="ask" maxTotal={maxTotal} />)}
        <div className="flex items-center justify-between px-2 h-8 border-y border-hairline-soft bg-white/[0.02]">
          <span ref={midRef} className={cn("num text-[15px] font-semibold transition-colors", up ? "text-profit" : "text-loss")}>
            {mid ? formatPrice(mid) : "---"} <span className="text-[11px]">{up ? "↑" : "↓"}</span>
          </span>
          <span className="text-[11px] text-text-muted num">
            <Hint title={HINTS.spread}>Spread</Hint>: {spreadAbs !== null ? spreadAbs.toFixed(spreadAbs >= 1 ? 1 : 2) : "---"}
            {spreadBps !== null && ` / ${spreadBps < 1 ? spreadBps.toFixed(3) : spreadBps.toFixed(2)} bps`}
          </span>
        </div>
        {bids.map((r) => <Level key={`b${r.price}`} row={r} side="bid" maxTotal={maxTotal} />)}
      </div>
      )}
      <div className="px-2 py-1.5 border-t border-hairline-soft shrink-0" title={`Bid depth ${formatSize(bidQty)} ${base} vs ask depth ${formatSize(askQty)} ${base} over the top ${LEVELS} levels`}>
        <div className="flex items-center gap-2 text-[10px] font-bold leading-4 num">
          <span className="text-profit whitespace-nowrap">B {bidPct.toFixed(2)}%</span>
          <div className="flex flex-1 h-1.5 rounded-sm overflow-hidden bg-loss/30">
            <div className="bg-profit/70 h-full" style={{ width: `${bidPct}%` }} />
          </div>
          <span className="text-loss whitespace-nowrap">{(100 - bidPct).toFixed(2)}% S</span>
        </div>
      </div>
    </div>
  );
}

function Level({ row, side, maxTotal }: { row: Row; side: "ask" | "bid"; maxTotal: number }) {
  const w = Math.min(100, (row.total / maxTotal) * 100);
  return (
    <div className="relative grid grid-cols-3 px-2 h-[22px] items-center num hover:bg-white/[0.03]">
      <div
        className={cn("absolute inset-y-[2px] right-0 pointer-events-none", side === "ask" ? "bg-loss/15" : "bg-profit/15")}
        style={{ width: `${w}%` }}
      />
      <span className={cn("relative", side === "ask" ? "text-loss" : "text-profit")}>{formatPrice(row.price)}</span>
      <span className="relative text-right text-text-primary">{formatSize(row.size)}</span>
      <span className="relative text-right text-text-secondary">{formatSize(row.total)}</span>
    </div>
  );
}
