import { useMemo } from "react";
import { FAVORITE_SYMBOLS } from "@/lib/constants";
import { useVenueMarkets } from "./useVenueMarkets";

/**
 * The markets pinned on the favorites strip, the footer ticker and the picker's Favorites tab.
 *
 * The book's OPEN positions first, in the venue's order, then the streamed symbols. Until
 * 2026-09-08 the pinned list was the four intraday crypto while the book held BTC, SOL, WTI, XAU,
 * ADA and ZEC — the strip and the ticker named ETH (not held) and none of gold, oil or ZEC. What
 * the bot holds is what the operator wants at a glance; the constant only bridges the first
 * seconds before the venue list is in.
 */
export function useFavoriteSymbols(): readonly string[] {
  const { list } = useVenueMarkets();
  return useMemo(() => {
    const held = list.filter((v) => v.held).map((v) => v.symbol);
    const out = [...held];
    for (const s of FAVORITE_SYMBOLS) if (!out.includes(s)) out.push(s);
    return out;
  }, [list]);
}
