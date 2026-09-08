import { memo } from "react";
import { useMarketStore } from "@/stores/marketStore";
import { useNow } from "@/hooks/useNow";
import { useSymbolChanges } from "@/hooks/useSymbolChanges";
import { useVenueMarkets } from "@/hooks/useVenueMarkets";
import { useFavoriteSymbols } from "@/hooks/useFavoriteSymbols";
import { SYMBOL_COLORS } from "@/lib/constants";
import { marketLabel } from "@/lib/market";
import { cn, formatPrice } from "@/lib/utils";
import { SignedPct } from "@/components/shared/TradeChips";

const Item = memo(function Item({ symbol, active, held, change, venuePrice, onSelect }: { symbol: string; active: boolean; held: boolean; change: number | null; venuePrice: number | null; onSelect: (s: string) => void }) {
  // The venue's mark, like everywhere else on these screens; the stream is the fallback so the strip
  // still shows something if the bridge is unreachable (2026-09-04).
  const streamed = useMarketStore((s) => s.prices[symbol] || 0);
  const price = venuePrice || streamed;
  return (
    <button
      type="button"
      onClick={() => onSelect(symbol)}
      aria-pressed={active}
      title={held ? `${symbol} · open position in the trend book` : symbol}
      className={cn("inline-flex items-center gap-2 h-8 px-3 rounded-[6px] whitespace-nowrap transition-colors", active ? "bg-panel-2 ring-1 ring-hairline-strong" : "hover:bg-hover")}
    >
      <span className={cn("w-2 h-2 rounded-full", held && "ring-2 ring-mint/50")} style={{ backgroundColor: SYMBOL_COLORS[symbol] ?? "#FFFFFF" }} />
      <span className="text-[13px] font-semibold text-text">{marketLabel(symbol)}</span>
      <span className="num text-[13px] font-medium text-text">{price > 0 ? formatPrice(price) : "---"}</span>
      <SignedPct value={change} className="text-[12px]" />
    </button>
  );
});

/** Favorites strip under the nav (Trade page only): the held markets first, then the streamed ones — icon · price · 24h %. */
export function FavoritesStrip({ symbol, onSelect }: { symbol: string; onSelect: (s: string) => void }) {
  const now = useNow();
  const favorites = useFavoriteSymbols();
  const changes = useSymbolChanges(now / 1000, favorites);
  const { byMarket } = useVenueMarkets();
  return (
    <div className="flex items-center gap-1 h-10 px-1 overflow-x-auto scrollbar-none shrink-0 min-w-0">
      {favorites.map((s) => <Item key={s} symbol={s} active={s === symbol} held={byMarket.get(s)?.held ?? false} change={changes[s] ?? null} venuePrice={byMarket.get(s)?.price ?? null} onSelect={onSelect} />)}
    </div>
  );
}
