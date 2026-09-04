import { memo } from "react";
import { useMarketStore } from "@/stores/marketStore";
import { useNow } from "@/hooks/useNow";
import { useSymbolChanges } from "@/hooks/useSymbolChanges";
import { useVenueMarkets } from "@/hooks/useVenueMarkets";
import { FAVORITE_SYMBOLS, SYMBOL_COLORS, SYMBOL_LABELS } from "@/lib/constants";
import { cn, formatPrice } from "@/lib/utils";
import { SignedPct } from "@/components/shared/TradeChips";

const Item = memo(function Item({ symbol, active, change, venuePrice, onSelect }: { symbol: string; active: boolean; change: number | null; venuePrice: number | null; onSelect: (s: string) => void }) {
  // The venue's mark, like everywhere else on these screens; the stream is the fallback so the strip
  // still shows something if the bridge is unreachable (2026-09-04).
  const streamed = useMarketStore((s) => s.prices[symbol] || 0);
  const price = venuePrice || streamed;
  return (
    <button
      type="button"
      onClick={() => onSelect(symbol)}
      aria-pressed={active}
      className={cn("inline-flex items-center gap-2 h-8 px-3 rounded-[6px] whitespace-nowrap transition-colors", active ? "bg-panel-2 ring-1 ring-hairline-strong" : "hover:bg-hover")}
    >
      <span className="w-2 h-2 rounded-full" style={{ backgroundColor: SYMBOL_COLORS[symbol] ?? "#FFFFFF" }} />
      <span className="text-[13px] font-semibold text-text">{SYMBOL_LABELS[symbol] ?? symbol}</span>
      <span className="num text-[13px] font-medium text-text">{price > 0 ? formatPrice(price) : "---"}</span>
      <SignedPct value={change} className="text-[12px]" />
    </button>
  );
});

/** Favorites strip under the nav (Trade page only): icon · price · 24h %. Scrolls when narrow. */
export function FavoritesStrip({ symbol, onSelect }: { symbol: string; onSelect: (s: string) => void }) {
  const now = useNow();
  const changes = useSymbolChanges(now / 1000);
  const { byMarket } = useVenueMarkets();
  return (
    <div className="flex items-center gap-1 h-10 px-1 overflow-x-auto scrollbar-none shrink-0 min-w-0">
      {FAVORITE_SYMBOLS.map((s) => <Item key={s} symbol={s} active={s === symbol} change={changes[s]} venuePrice={byMarket.get(s)?.price ?? null} onSelect={onSelect} />)}
    </div>
  );
}
