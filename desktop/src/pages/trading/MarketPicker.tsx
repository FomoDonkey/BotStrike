import { useEffect, useMemo, useRef, useState } from "react";
import { useShallow } from "zustand/shallow";
import { Search, Star } from "lucide-react";
import { useMarketStore } from "@/stores/marketStore";
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

type Tab = "favorites" | "all";
const TABS = [{ id: "favorites" as const, label: "Favorites" }, { id: "all" as const, label: "All" }];

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

  const rows = useMemo(() => {
    const base = tab === "favorites" ? SYMBOLS.filter((s) => FAVORITE_SYMBOLS.includes(s)) : [...SYMBOLS];
    const q = query.trim().toLowerCase();
    return base.filter((s) => !q || s.toLowerCase().includes(q) || (SYMBOL_LABELS[s] ?? "").toLowerCase().includes(q));
  }, [tab, query]);

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
    <Modal open={open} onClose={onClose} bare width="max-w-3xl">
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
              <th>8H Funding</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr><td colSpan={6} className="c text-text">No market matches “{query}”</td></tr>
            )}
            {rows.map((s, i) => {
              const p = prices[s] || 0;
              const mi = info[s];
              const f = mi?.funding_rate;
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
                    </span>
                  </td>
                  <td className="num">{p > 0 ? formatPrice(p) : "---"}</td>
                  <td><SignedPct value={changes[s]} /></td>
                  <td className="num">{formatCompactUSD(mi?.volume_24h ?? 0)}</td>
                  <td className="num">{mi?.open_interest ? `${formatCompact(mi.open_interest)} ${SYMBOL_LABELS[s] ?? ""}` : "---"}</td>
                  <td className={cn("num", typeof f === "number" ? (f > 0 ? "text-mint" : f < 0 ? "text-rose" : "text-text") : "text-text-3")}>
                    {typeof f === "number" ? formatSignedPct(f, 4) : "---"}
                  </td>
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
