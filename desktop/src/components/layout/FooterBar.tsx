import { memo } from "react";
import { Link } from "react-router-dom";
import { useShallow } from "zustand/shallow";
import { Activity, BookOpen, Monitor } from "lucide-react";
import { useSystemStore } from "@/stores/systemStore";
import { useMarketStore } from "@/stores/marketStore";
import { useExchangeStore } from "@/stores/exchangeStore";
import { useUiStore } from "@/stores/uiStore";
import { useNow } from "@/hooks/useNow";
import { EXCHANGE_LABELS, SYMBOLS, SYMBOL_LABELS, DOCS_URL } from "@/lib/constants";
import { cn, capitalize, formatAge, formatCompactUSD, formatPrice } from "@/lib/utils";
import { useSymbolChanges } from "@/hooks/useSymbolChanges";
import { useVenueMarkets } from "@/hooks/useVenueMarkets";
import { SignedPct } from "@/components/shared/TradeChips";

const TickerItem = memo(function TickerItem({ symbol, change }: { symbol: string; change: number | null }) {
  const streamed = useMarketStore((s) => s.prices[symbol] || 0);
  const { byMarket } = useVenueMarkets();
  const price = byMarket.get(symbol)?.price || streamed;   // the venue's mark, as everywhere else
  return (
    <Link to="/trading" className="inline-flex items-center gap-1.5 px-3 whitespace-nowrap text-[12.5px] hover:bg-hover h-8">
      <span className="font-semibold text-text">{SYMBOL_LABELS[symbol] ?? symbol}</span>
      <span className="num font-medium text-text">{price > 0 ? formatPrice(price) : "---"}</span>
      <SignedPct value={change} className="text-[12px]" />
    </Link>
  );
});

/** 32 px footer status bar (spec §2): mode · feed age · ticker marquee · 24H vol · Activity / Docs / System. */
export function FooterBar({ className }: { className?: string }) {
  const now = useNow();
  const { mode, bridgeConnected, wsConnected } = useSystemStore(useShallow((s) => ({ mode: s.mode, bridgeConnected: s.bridgeConnected, wsConnected: s.wsConnected })));
  const lastTickAt = useMarketStore((s) => s.lastTickAt);
  // Was BTC's 24 h volume off the Binance stream: $16 B on a venue whose entire book turned over
  // $5.7 M that day. Summing the venue's own quote volume is both true and the figure the venue
  // states in this exact spot on its own screen (audit 2026-09-04).
  const venueMarkets = useVenueMarkets();
  const venueVol = venueMarkets.list.reduce((a, v) => a + (v.volume_24h_usd || 0), 0);
  const exchange = useExchangeStore((s) => s.exchange);
  const setActivityOpen = useUiStore((s) => s.setActivityOpen);
  const activityOpen = useUiStore((s) => s.activityOpen);
  const changes = useSymbolChanges(now / 1000);
  const age = lastTickAt > 0 ? (now - lastTickAt) / 1000 : null;
  const live = bridgeConnected && (wsConnected || (age !== null && age < 30));

  const items = SYMBOLS.map((s) => <TickerItem key={s} symbol={s} change={changes[s]} />);

  return (
    <footer className={cn("items-center h-8 bg-bg border-t border-hairline shrink-0 text-[12.5px] select-none min-w-0", className)}>
      <span className="inline-flex items-center gap-1.5 px-3 h-full border-r border-hairline font-medium text-text whitespace-nowrap shrink-0" title={bridgeConnected ? "Bridge online" : "Bridge unreachable"}>
        <span className={cn("w-2 h-2 rounded-full", !bridgeConnected ? "bg-rose" : live ? "bg-mint" : "bg-amber")} />
        {capitalize(mode.replace("_", " "))}
      </span>
      <span className="hidden xl:inline-flex items-center gap-1.5 px-3 h-full border-r border-hairline font-medium text-text whitespace-nowrap shrink-0" title="Age of the last market tick">
        {EXCHANGE_LABELS[exchange] ?? exchange} feed <span className="num text-text-2">{formatAge(age)}</span>
      </span>
      <div className="marquee flex-1 min-w-0 overflow-hidden h-full">
        <div className="marquee-track h-full items-center">
          {items}
          {items.map((it, i) => <span key={`dup-${i}`} aria-hidden>{it}</span>)}
        </div>
      </div>
      <span className="hidden 2xl:inline-flex items-center gap-1.5 px-3 h-full border-l border-hairline font-medium text-text whitespace-nowrap shrink-0" title="Quote volume across every market the venue lists, last 24 h">
        24H Vol <span className="num">{venueVol > 0 ? formatCompactUSD(venueVol) : "---"}</span>
      </span>
      <nav className="flex items-center h-full border-l border-hairline shrink-0">
        <button type="button" onClick={() => setActivityOpen(!activityOpen)} className={cn("inline-flex items-center gap-1.5 px-3 h-full font-medium text-text hover:bg-hover", activityOpen && "bg-active")}>
          <Activity className="w-3.5 h-3.5" /> Activity
        </button>
        <a href={DOCS_URL} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 px-3 h-full font-medium text-text hover:bg-hover border-l border-hairline">
          <BookOpen className="w-3.5 h-3.5" /> Docs
        </a>
        <Link to="/system" className="inline-flex items-center gap-1.5 px-3 h-full font-medium text-text hover:bg-hover border-l border-hairline">
          <Monitor className="w-3.5 h-3.5" /> System
        </Link>
      </nav>
    </footer>
  );
}
