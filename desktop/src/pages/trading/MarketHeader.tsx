import { useEffect, useRef, useState, type ReactNode } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { MarketView } from "@/hooks/useMarketInfo";
import { useNow } from "@/hooks/useNow";
import { useFlashOnChange } from "@/hooks/useFlash";
import { Hint } from "@/components/shared/Hint";
import { HINTS } from "@/lib/hints";
import { SignedPct } from "@/components/shared/TradeChips";
import { RegimeChip } from "@/components/ui/Chip";
import { SYMBOL_COLORS, SYMBOL_LABELS } from "@/lib/constants";
import { cn, formatCompact, formatCompactUSD, formatPrice, formatRelative, formatSignedPct } from "@/lib/utils";
import { formatCountdown, fundingDirection, fundingMeaning, fundingTone} from "@/lib/market";
import { MarketPicker } from "./MarketPicker";

interface MarketHeaderProps {
  market: MarketView;
  onSymbolChange: (s: string) => void;
}

function Stat({ label, hint, sub, children, className }: { label: string; hint?: string; sub?: string; children: ReactNode; className?: string }) {
  return (
    <div className={cn("flex flex-col justify-center gap-1 shrink-0 min-w-0", className)}>
      <span className="text-[12px] leading-none font-medium text-text-2 whitespace-nowrap">{hint ? <Hint title={hint}>{label}</Hint> : label}</span>
      <span className="num text-[13px] leading-none font-semibold text-text whitespace-nowrap">{children}</span>
      {/* Who pays whom, in words: the colour convention differs between venues, the wording cannot. */}
      {sub && <span className="text-[11px] leading-none font-medium text-text-2 whitespace-nowrap">{sub}</span>}
    </div>
  );
}

/** Strike's market header: symbol picker · price · mark / index / funding / 24h block / OI / spread / regime. */
export function MarketHeader({ market: m, onSymbolChange }: MarketHeaderProps) {
  const now = useNow();
  const [pickerOpen, setPickerOpen] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [overflow, setOverflow] = useState(false);

  // Ctrl+K opens the market picker (spec §3.1)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPickerOpen((o) => !o);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // chevron only when the stats row actually overflows
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const check = () => setOverflow(el.scrollWidth > el.clientWidth + 4 && el.scrollLeft + el.clientWidth < el.scrollWidth - 4);
    const obs = new ResizeObserver(check);
    obs.observe(el);
    el.addEventListener("scroll", check);
    check();
    return () => { obs.disconnect(); el.removeEventListener("scroll", check); };
  }, []);

  const priceRef = useRef<HTMLSpanElement>(null);
  useFlashOnChange(priceRef, m.price || null, "text-mint", "text-rose", 400);
  const up = m.price >= m.prevPrice;
  const winHint = m.statsMissing
    ? "The venue publishes no 24 h statistics for this market. Nothing is filled in from anywhere else — its price, funding, book and open interest above are still the venue's own."
    : m.windowIs24h
      ? HINTS.change24
      : `The bridge has ${m.winLabel} of 1m bars since it started — the window grows to a full 24 h live.`;

  return (
    <div className="relative z-20 rounded-lg border border-hairline bg-panel flex items-stretch min-w-0 h-14">
      <button
        type="button"
        onClick={() => setPickerOpen(true)}
        aria-haspopup="dialog"
        aria-expanded={pickerOpen}
        title="Change market (Ctrl K)"
        className="flex items-center gap-2 px-3 border-r border-hairline shrink-0 rounded-l-lg hover:bg-hover transition-colors"
      >
        <span className="inline-flex items-center justify-center w-6 h-6 rounded-full text-[11px] font-bold text-bg" style={{ backgroundColor: SYMBOL_COLORS[m.symbol] ?? "#FFFFFF" }}>
          {(SYMBOL_LABELS[m.symbol] ?? m.symbol).slice(0, 1)}
        </span>
        <span className="text-[16px] font-semibold text-text tracking-tight whitespace-nowrap">{m.symbol}</span>
        <ChevronDown className={cn("w-4 h-4 text-text transition-transform", pickerOpen && "rotate-180")} />
      </button>
      <div className="flex items-center gap-2 px-3 border-r border-hairline shrink-0">
        <span ref={priceRef} className={cn("num text-[20px] font-semibold leading-none transition-colors", m.price > 0 ? (up ? "text-mint" : "text-rose") : "text-text")}>
          {m.price > 0 ? formatPrice(m.price) : "---"}
        </span>
        <SignedPct value={m.change} className="text-[12.5px]" />
      </div>
      <div ref={scrollRef} className="flex items-center gap-6 px-4 overflow-x-auto scrollbar-none min-w-0 flex-1 rounded-r-lg">
        <Stat label="Mark Price" hint={HINTS.mark}>{m.mark > 0 ? formatPrice(m.mark) : "---"}</Stat>
        <Stat label="Index Price" hint={HINTS.index}>{m.index > 0 ? formatPrice(m.index) : "---"}</Stat>
        <Stat label="Funding / Countdown" hint={HINTS.funding} sub={fundingDirection(m.funding)}>
          {m.funding === null ? <span className="text-text-3">---</span> : <span title={`${fundingDirection(m.funding)} — ${fundingMeaning(m.funding)}`}
                        className={fundingTone(m.funding) === "mint" ? "text-mint" : fundingTone(m.funding) === "rose" ? "text-rose" : ""}>{formatSignedPct(m.funding, 4)}</span>}
          <span className="text-text-2 font-medium"> / {formatCountdown(m.countdownSec)}</span>
        </Stat>
        <Stat label={`${m.winLabel} Change`} hint={winHint}><SignedPct value={m.change} /></Stat>
        <Stat label={`${m.winLabel} High`} hint={winHint}>{m.high ? formatPrice(m.high) : "---"}</Stat>
        <Stat label={`${m.winLabel} Low`} hint={winHint}>{m.low ? formatPrice(m.low) : "---"}</Stat>
        <Stat label={`${m.winLabel} Vol`} hint={m.statsMissing ? winHint : HINTS.vol24}>
          {/* "$0" when the venue reports no trades, "---" only when it reports nothing at all. */}
          {m.statsMissing ? "---" : <>
            {m.volumeBase !== null && m.volumeBase > 0 && <span>{formatCompact(m.volumeBase)} {SYMBOL_LABELS[m.symbol] ?? m.symbol.split("-")[0]} <span className="text-text-2 font-medium">·</span> </span>}
            {formatCompactUSD(m.volumeUsd)}
          </>}
        </Stat>
        {/* Zero is a fact on four of the venue's markets, not a value we failed to fetch. */}
        <Stat label="Open Interest" hint={HINTS.oi}>
          {m.rest && typeof m.rest.open_interest === "number"
            ? `${formatCompact(m.oi)} ${SYMBOL_LABELS[m.symbol] ?? m.symbol.split("-")[0]}` : "---"}
        </Stat>
        <Stat label="Spread" hint={HINTS.spread}>{m.spreadBps === null ? "---" : `${m.spreadBps.toFixed(2)} bps`}</Stat>
        <Stat label="Regime" hint={HINTS.regime}>
          <span className="inline-flex items-center gap-1.5">
            <RegimeChip regime={m.regime} size="xs" />
            <span className="text-text-2 font-medium text-[12px]">{m.regimeTf}m{m.regimeSince > 0 ? ` · ${formatRelative(m.regimeSince * 1000, now).replace(" ago", "")}` : ""}</span>
          </span>
        </Stat>
        {m.restMissing && (
          <span className="text-[12px] font-medium text-text-2 whitespace-nowrap" title="GET /api/market/{symbol} returned 404 — values come from the WS snapshot and the 1m candles.">
            bridge 2.14 · derived
          </span>
        )}
      </div>
      {overflow && (
        <button
          type="button"
          aria-label="Scroll stats"
          onClick={() => scrollRef.current?.scrollBy({ left: 240, behavior: "smooth" })}
          className="absolute right-0 top-0 bottom-0 w-8 flex items-center justify-center rounded-r-lg bg-panel border-l border-hairline text-text hover:bg-hover"
        >
          <ChevronRight className="w-4 h-4" />
        </button>
      )}
      <MarketPicker open={pickerOpen} onClose={() => setPickerOpen(false)} symbol={m.symbol} onSelect={onSymbolChange} />
    </div>
  );
}
