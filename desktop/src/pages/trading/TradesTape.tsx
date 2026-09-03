import { useMarketStore } from "@/stores/marketStore";
import { SYMBOL_LABELS } from "@/lib/constants";
import { cn, formatPrice, formatSize, formatTimeShort } from "@/lib/utils";
import { EmptyState } from "@/components/ui/Panel";

/** Live tape: last 60 market trades (Price · Size · Time), coloured price, newest first. */
export function TradesTape({ symbol }: { symbol: string }) {
  const tape = useMarketStore((s) => s.tape[symbol]);
  const base = SYMBOL_LABELS[symbol] ?? "";

  if (!tape?.length) return <EmptyState>Waiting for trades…</EmptyState>;

  return (
    <div className="flex flex-col flex-1 min-h-0 text-[12.5px]">
      <div className="grid grid-cols-[1.2fr_1fr_0.9fr] gap-2 px-2 h-7 items-center text-[11px] font-medium uppercase tracking-[0.04em] text-text-2 border-b border-hairline-soft shrink-0">
        <span>Price (USD)</span>
        <span className="text-right">Size ({base})</span>
        <span className="text-right">Time</span>
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto scrollbar-none">
        {tape.map((t, i) => {
          const buy = t.side === "BUY";
          return (
            <div
              key={`${t.timestamp}-${i}`}
              className={cn("grid grid-cols-[1.2fr_1fr_0.9fr] gap-2 px-2 h-[22px] items-center num hover:bg-hover", i === 0 && "bg-panel-2")}
              title={`${buy ? "Taker buy" : "Taker sell"} · ${formatSize(t.quantity)} ${base} @ ${formatPrice(t.price)} · $${t.notional.toFixed(2)}`}
            >
              <span className={cn("font-medium", buy ? "text-mint" : "text-rose")}>{formatPrice(t.price)}</span>
              <span className="text-right font-medium text-text">{formatSize(t.quantity)}</span>
              <span className="text-right font-medium text-text-2">{formatTimeShort(t.timestamp)}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
