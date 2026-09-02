import { useMarketStore } from "@/stores/marketStore";
import { SYMBOL_LABELS } from "@/lib/constants";
import { cn, formatPrice, formatSize, formatTimeShort } from "@/lib/utils";

/** Live tape: last 60 market trades of the symbol (time · price · size · side), newest first. */
export function TradesTape({ symbol }: { symbol: string }) {
  const tape = useMarketStore((s) => s.tape[symbol]);
  const base = SYMBOL_LABELS[symbol] ?? "";

  if (!tape?.length) {
    return <div className="flex-1 flex items-center justify-center text-text-faint text-xs">Waiting for trades…</div>;
  }

  return (
    <div className="flex flex-col flex-1 min-h-0 text-[12px]">
      <div className="grid grid-cols-[1fr_1fr_1fr_auto] gap-2 px-2 h-7 items-center text-[10.5px] uppercase tracking-[0.06em] text-text-muted border-b border-hairline-soft shrink-0">
        <span>Time</span>
        <span className="text-right">Price (USD)</span>
        <span className="text-right">Size ({base})</span>
        <span className="w-4 text-right">Side</span>
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto scrollbar-none">
        {tape.map((t, i) => {
          const buy = t.side === "BUY";
          return (
            <div
              key={`${t.timestamp}-${i}`}
              className={cn("grid grid-cols-[1fr_1fr_1fr_auto] gap-2 px-2 h-[22px] items-center num hover:bg-white/[0.03]", i === 0 && "bg-white/[0.02]")}
              title={`${buy ? "Taker buy" : "Taker sell"} · ${formatSize(t.quantity)} ${base} @ ${formatPrice(t.price)} · $${t.notional.toFixed(2)}`}
            >
              <span className="text-text-muted">{formatTimeShort(t.timestamp)}</span>
              <span className={cn("text-right", buy ? "text-profit" : "text-loss")}>{formatPrice(t.price)}</span>
              <span className="text-right text-text-primary">{formatSize(t.quantity)}</span>
              <span className={cn("w-4 text-right text-[10px] font-bold", buy ? "text-profit" : "text-loss")}>{buy ? "B" : "S"}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
