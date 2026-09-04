import { useEffect, useMemo, useRef, useState } from "react";
import { useShallow } from "zustand/shallow";
import { Search, Star } from "lucide-react";
import { useMarketStore } from "@/stores/marketStore";
import { useVenueMarkets } from "@/hooks/useVenueMarkets";
import { fundingDirection, fundingMeaning } from "@/lib/market";
import { useNow } from "@/hooks/useNow";
import { useSymbolChanges } from "@/hooks/useSymbolChanges";
import { FAVORITE_SYMBOLS, SYMBOLS, SYMBOL_COLORS, SYMBOL_LABELS } from "@/lib/constants";
import { cn, formatCompact, formatCompactUSD, formatPrice, formatSignedPct } from "@/lib/utils";
import { Modal } from "@/components/ui/Modal";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { SignedPct } from "@/components/shared/TradeChips";

interface MarketPickerProps {
  open: boolean;
  onClose: () => void;
  symbol: string;
  onSelect: (symbol: string) => void;
}

type Tab = "favorites" | "live" | "all";
// "All" is every market the venue lists — the picker used to stop at four crypto while the trend book
// held gold, silver, the S&P and oil (2026-09-04).
const TABS = [{ id: "favorites" as const, label: "Favorites" },
              { id: "live" as const, label: "Live feed" },
              { id: "all" as const, label: "All markets" }];

/** Strike's market picker (Ctrl+K): search, Favorites / All, dense columns, ↑↓ Enter Esc. */
export function MarketPicker({ open, onClose, symbol, onSelect }: MarketPickerProps) {
  const now = useNow();
  const [query, setQuery] = useState("");
  const [tab, setTab] = useState<Tab>("favorites");
  const [cursor, setCursor] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const prices = useMarketStore(useShallow((s) => s.prices));
  const info = useMarketStore(useShallow((s) => s.marketInfo));
  const changes = useSymbolChanges(now / 1000);

  const venue = useVenueMarkets(open);
  const byMarket = venue.byMarket;

  const rows = useMemo(() => {
    const all: string[] = venue.list.length ? venue.list.map((v) => v.symbol) : [...SYMBOLS];
    const q = query.trim().toLowerCase();
    // A SEARCH SEARCHES EVERYTHING. Filtering within the active tab meant typing "NVDA" on the
    // Favorites tab answered "No market matches NVDA" about a market the venue lists and the bot
    // supports — you had to know to switch tabs first (Edgar, 2026-09-04). The tabs are for browsing;
    // a query is for finding.
    const base = q ? all
      : tab === "favorites" ? all.filter((s) => FAVORITE_SYMBOLS.includes(s))
      : tab === "live" ? all.filter((s) => byMarket.get(s)?.feed ?? SYMBOLS.includes(s as (typeof SYMBOLS)[number]))
      : all;
    const filtered = base.filter((s) => !q || s.toLowerCase().includes(q) || (SYMBOL_LABELS[s] ?? "").toLowerCase().includes(q));
    // held first, then the ones the daily run may buy, then the rest — alphabetical inside each group
    const rank = (s: string) => {
      const v = byMarket.get(s);
      return v?.held ? 0 : v?.feed ? 1 : v?.pool ? 2 : 3;
    };
    return filtered.sort((x, y) => rank(x) - rank(y) || x.localeCompare(y));
  }, [tab, query, venue.list, byMarket]);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  const select = (s: string) => {
    onSelect(s);
    onClose();
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setCursor((c) => Math.min(rows.length - 1, c + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setCursor((c) => Math.max(0, c - 1)); }
    else if (e.key === "Enter") { e.preventDefault(); const r = rows[Math.min(cursor, rows.length - 1)]; if (r) select(r); }
  };

  const active = Math.min(cursor, Math.max(0, rows.length - 1));

  return (
    // Wider than it was: the list now carries every venue market, and long names plus their tags
    // pushed the funding column off the right edge (2026-09-04).
    <Modal open={open} onClose={onClose} bare width="max-w-5xl">
      <div className="flex items-center gap-2 h-12 px-3 border-b border-hairline shrink-0">
        <Search className="w-4 h-4 text-text shrink-0" />
        <input
          ref={inputRef}
          value={query}
          onChange={(e) => { setQuery(e.target.value); setCursor(0); }}
          onKeyDown={onKey}
          placeholder="Search markets"
          aria-label="Search markets"
          className="flex-1 min-w-0 h-8 bg-transparent text-[14px] font-medium text-text placeholder:text-text-3 focus:outline-none"
        />
        <SegmentedControl options={TABS} value={tab} onChange={(t) => { setTab(t); setCursor(0); }} size="sm" />
      </div>
      <div className="overflow-auto min-h-0">
        <table className="term-table" style={{ minWidth: 640 }}>
          <thead>
            <tr>
              <th className="l">Symbol</th>
              <th>Last Price</th>
              <th>24h Change</th>
              <th>24h Volume</th>
              <th>Open Interest</th>
              {/* Not "8H": Strike settles hourly, and the interval is a venue fact. */}
              <th>Funding</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr><td colSpan={6} className="c text-text">No market on the venue matches “{query}”</td></tr>
            )}
            {rows.map((s, i) => {
              // Every figure in this row is the VENUE's. Reading price/change/volume/OI off the
              // socket printed Binance's numbers for the four streamed markets and nothing for the
              // other 27 — one row, two venues (audit 2026-09-04). The stream is the last resort.
              const v = byMarket.get(s);
              const p = v?.price || prices[s] || 0;
              const f = v?.funding_rate;
              return (
                <tr
                  key={s}
                  className={cn("cursor-pointer", i === active && "is-open")}
                  onMouseEnter={() => setCursor(i)}
                  onClick={() => select(s)}
                  aria-selected={s === symbol}
                >
                  <td className="l">
                    <span className="inline-flex items-center gap-2">
                      <Star className={cn("w-3.5 h-3.5", FAVORITE_SYMBOLS.includes(s) ? "text-amber fill-amber" : "text-text-3")} />
                      <span className="w-2 h-2 rounded-full" style={{ backgroundColor: SYMBOL_COLORS[s] ?? "#FFFFFF" }} />
                      <span className="font-semibold text-text">{s}</span>
                      {s === symbol && <span className="text-[11px] font-medium text-mint">current</span>}
                      {byMarket.get(s)?.held && <span className="text-[11px] font-medium text-mint">open</span>}
                      {byMarket.get(s) && !byMarket.get(s)!.feed && (
                        <span className="text-[11px] font-medium text-text-2" title="No intraday stream: this market trades in the daily trend book, priced from daily bars">daily only</span>
                      )}
                    </span>
                  </td>
                  <td className="num">{p > 0 ? formatPrice(p) : "---"}</td>
                  <td><SignedPct value={typeof v?.change_24h_pct === "number" ? v.change_24h_pct : changes[s]} /></td>
                  <td className="num">{typeof v?.volume_24h_usd === "number" ? formatCompactUSD(v.volume_24h_usd) : "---"}</td>
                  <td className="num">{typeof v?.open_interest === "number" ? `${formatCompact(v.open_interest)} ${SYMBOL_LABELS[s] ?? s.split("-")[0]}` : "---"}</td>
                  {(() => {
                    const rate = f;
                    return (
                      <td className={cn("num", typeof rate === "number" ? (rate > 0 ? "text-mint" : rate < 0 ? "text-rose" : "text-text") : "text-text-3")}
                          title={typeof rate === "number" ? `${fundingDirection(rate)} — ${fundingMeaning(rate)}` : undefined}>
                        {typeof rate === "number" ? formatSignedPct(rate, 4) : "---"}
                      </td>
                    );
                  })()}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="flex items-center gap-4 h-9 px-3 border-t border-hairline shrink-0 text-[12px] font-medium text-text-2 overflow-x-auto scrollbar-none whitespace-nowrap">
        <Key k="Ctrl K" v="Open" /><Key k="↑↓" v="Navigate" /><Key k="Enter" v="Select" /><Key k="Esc" v="Close" />
      </div>
    </Modal>
  );
}

function Key({ k, v }: { k: string; v: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <kbd className="inline-flex items-center h-5 px-1.5 rounded-[4px] bg-panel-2 border border-hairline text-[11px] font-semibold text-text">{k}</kbd>
      {v}
    </span>
  );
}
