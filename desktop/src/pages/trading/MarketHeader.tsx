import { useEffect, useMemo, useRef, useState } from "react";
import { useShallow } from "zustand/shallow";
import { ChevronDown } from "lucide-react";
import { api, ApiError, type MarketInfoResponse } from "@/lib/api";
import { useMarketStore } from "@/stores/marketStore";
import { useRiskStore } from "@/stores/riskStore";
import { usePolling } from "@/hooks/usePolling";
import { useNow } from "@/hooks/useNow";
import { useFlashOnChange } from "@/hooks/useFlash";
import { Hint } from "@/components/shared/Hint";
import { HINTS } from "@/lib/hints";
import { SignedPct } from "@/components/shared/TradeChips";
import { REGIME_COLORS, SYMBOLS, SYMBOL_COLORS, SYMBOL_LABELS } from "@/lib/constants";
import { cn, formatCompact, formatCompactUSD, formatPrice, formatRelative, formatSignedPct } from "@/lib/utils";
import { change24h, formatCountdown, fundingCountdownSec, spanLabel, stats24h } from "@/lib/market";

interface MarketHeaderProps {
  symbol: string;
  onSymbolChange: (s: string) => void;
}

const MARKET_POLL_MS = 10_000;

function Stat({ label, hint, children, className }: { label: string; hint?: string; children: React.ReactNode; className?: string }) {
  return (
    <div className={cn("flex flex-col justify-center gap-0.5 shrink-0 min-w-0", className)}>
      <span className="text-[10.5px] leading-none text-text-muted whitespace-nowrap">
        {hint ? <Hint title={hint}>{label}</Hint> : label}
      </span>
      <span className="num text-[12.5px] leading-none text-text-primary whitespace-nowrap">{children}</span>
    </div>
  );
}

/** Per-symbol 24h % for the dropdown (bridge value when sent, else derived from the candles). */
function useSymbolChanges(nowSec: number) {
  const candles = useMarketStore(useShallow((s) => s.candles));
  const prices = useMarketStore(useShallow((s) => s.prices));
  const info = useMarketStore(useShallow((s) => s.marketInfo));
  return useMemo(() => {
    const out: Record<string, number | null> = {};
    for (const sym of SYMBOLS) {
      const bridge = info[sym]?.change_24h_pct;
      out[sym] = typeof bridge === "number" ? bridge : change24h(stats24h(candles[sym], nowSec), prices[sym] || 0);
    }
    return out;
    // nowSec on purpose only every minute — stats24h is cheap but not free at 1 Hz × 4 symbols
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candles, prices, info, Math.floor(nowSec / 60)]);
}

export function MarketHeader({ symbol, onSymbolChange }: MarketHeaderProps) {
  const now = useNow();
  const nowSec = now / 1000;
  const price = useMarketStore((s) => s.prices[symbol] || 0);
  const prevPrice = useMarketStore((s) => s.prevPrices[symbol] || 0);
  const info = useMarketStore((s) => s.marketInfo[symbol]);
  const candles = useMarketStore((s) => s.candles[symbol]);
  const orderbook = useMarketStore((s) => s.orderbooks[symbol]);
  const wsRegime = useMarketStore((s) => s.regime[symbol]);
  const riskRegime = useRiskStore((s) => s.regimes[symbol]);
  const riskSince = useRiskStore((s) => s.regimeSince[symbol]);
  const changes = useSymbolChanges(nowSec);

  // REST market header (bridge ≥ 2.15). A 404 means an older bridge → derive everything below.
  const [restState, setRestState] = useState<{ symbol: string; data: MarketInfoResponse | null; at: number }>({ symbol: "", data: null, at: 0 });
  const [restMissing, setRestMissing] = useState(false);
  usePolling(async () => {
    try {
      const r = await api.market(symbol);
      setRestState({ symbol, data: r.engine === false ? null : r, at: Date.now() });
      setRestMissing(false);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) setRestMissing(true);
    }
  }, MARKET_POLL_MS);
  // a payload fetched for the previous symbol is never shown for the new one
  const rest = restState.symbol === symbol ? restState.data : null;
  const restAt = restState.symbol === symbol ? restState.at : 0;

  // Client-side window stats (candles only) + live price folded in
  const winStats = useMemo(() => stats24h(candles, nowSec), [candles, nowSec]);
  const derivedChange = change24h(winStats, price);

  const mark = rest?.mark_price || info?.mark_price || 0;
  const index = rest?.index_price || info?.index_price || 0;
  const funding = rest?.funding_rate ?? info?.funding_rate ?? null;
  const restAge = restAt ? (now - restAt) / 1000 : 0;
  const countdown = typeof rest?.funding_countdown_sec === "number"
    ? Math.max(0, rest.funding_countdown_sec - restAge)
    : typeof info?.funding_countdown_sec === "number" && info.updated
      ? Math.max(0, info.funding_countdown_sec - (nowSec - info.updated))
      : fundingCountdownSec(now);
  const change = rest?.change_24h_pct ?? info?.change_24h_pct ?? derivedChange;
  const high = rest?.high_24h ?? info?.high_24h ?? (winStats.high !== null ? Math.max(winStats.high, price) : null);
  const low = rest?.low_24h ?? info?.low_24h ?? (winStats.low !== null ? Math.min(winStats.low, price || Infinity) : null);
  const volume = rest?.volume_24h_usd || info?.volume_24h || 0;
  const oi = rest?.open_interest || info?.open_interest || 0;
  const spreadBps = rest?.spread_bps ?? info?.spread_bps ?? orderbook?.spread_bps ?? null;
  // Window label: the bridge reports how many 1m bars back its 24h block (window_min); the
  // client-side fallback is labelled by the candle span it actually covers.
  const bridgeChange = rest?.change_24h_pct ?? info?.change_24h_pct;
  const bridgeWindowMin = typeof rest?.window_min === "number" ? rest.window_min : null;
  const spanSec = typeof bridgeChange === "number"
    ? (bridgeWindowMin !== null ? bridgeWindowMin * 60 : 24 * 3600)
    : winStats.span_sec;
  const windowIs24h = spanSec >= 23.5 * 3600;
  const winLabel = windowIs24h ? "24h" : spanLabel(spanSec);
  const winHint = windowIs24h
    ? HINTS.change24
    : typeof bridgeChange === "number"
      ? `The bridge has ${winLabel} of 1m bars since it started — the window grows to a full 24 h live.`
      : `Derived client-side from the ${winLabel} of 1m candles the bridge keeps in memory — a true 24 h window needs bridge ≥ 2.15 (/api/market).`;

  const regime = rest?.regime || wsRegime || riskRegime || "UNKNOWN";
  const since = rest?.regime_since || info?.regime_since || riskSince || 0;
  const regimeTf = rest?.regime_timeframe_min || info?.regime_timeframe_min || 15;
  const regimeColor = REGIME_COLORS[regime] ?? REGIME_COLORS.UNKNOWN;

  const priceRef = useRef<HTMLSpanElement>(null);
  useFlashOnChange(priceRef, price || null, "text-profit", "text-loss", 400);
  const up = price >= prevPrice;

  return (
    <div className="relative z-20 rounded-lg border border-hairline bg-bg-surface flex items-stretch min-w-0">
      <SymbolPicker symbol={symbol} onChange={onSymbolChange} changes={changes} />
      <div className="flex items-center gap-2 px-3 border-r border-hairline shrink-0">
        <span ref={priceRef} className={cn("num text-[18px] font-semibold leading-none transition-colors", price > 0 ? (up ? "text-profit" : "text-loss") : "text-text-faint")}>
          {price > 0 ? formatPrice(price) : "---"}
        </span>
        <SignedPct value={change} className="text-[11.5px]" />
      </div>
      <div className="flex items-center gap-5 px-3 overflow-x-auto scrollbar-none min-w-0 flex-1 py-1.5 rounded-r-lg">
        <Stat label="Mark Price" hint={HINTS.mark}>{mark > 0 ? formatPrice(mark) : "---"}</Stat>
        <Stat label="Index Price" hint={HINTS.index}>{index > 0 ? formatPrice(index) : "---"}</Stat>
        <Stat label="Funding / Countdown" hint={HINTS.funding}>
          {funding === null ? <span className="text-text-faint">---</span> : (
            <span className={funding > 0 ? "text-profit" : funding < 0 ? "text-loss" : ""}>{formatSignedPct(funding, 4)}</span>
          )}
          <span className="text-text-muted"> / {formatCountdown(countdown)}</span>
        </Stat>
        <Stat label={`${winLabel} Change`} hint={winHint}><SignedPct value={change} /></Stat>
        <Stat label={`${winLabel} High`} hint={winHint}>{high ? formatPrice(high) : "---"}</Stat>
        <Stat label={`${winLabel} Low`} hint={winHint}>{low && Number.isFinite(low) ? formatPrice(low) : "---"}</Stat>
        <Stat label="24h Volume" hint={HINTS.vol24}>{formatCompactUSD(volume)}</Stat>
        {oi > 0 && <Stat label="Open Interest" hint={HINTS.oi}>{formatCompact(oi)} {SYMBOL_LABELS[symbol] ?? ""}</Stat>}
        <Stat label="Spread" hint={HINTS.spread}>{spreadBps === null ? "---" : `${spreadBps.toFixed(2)} bps`}</Stat>
        <Stat label="Regime" hint={HINTS.regime}>
          <span className="inline-flex items-center gap-1.5">
            <span className="px-1.5 py-[2px] rounded text-[10px] font-bold uppercase tracking-wider leading-none" style={{ color: regimeColor, backgroundColor: `${regimeColor}1A` }}>
              {regime}
            </span>
            <span className="text-text-muted text-[11px]">
              {regimeTf}m{since > 0 ? ` · ${formatRelative(since * 1000, now).replace(" ago", "")}` : ""}
            </span>
          </span>
        </Stat>
        {restMissing && (
          <span className="text-[10px] text-text-faint whitespace-nowrap" title="GET /api/market/{symbol} returned 404 — values above come from the WS snapshot and the 1m candles.">
            bridge 2.14 · derived
          </span>
        )}
      </div>
    </div>
  );
}

function SymbolPicker({ symbol, onChange, changes }: { symbol: string; onChange: (s: string) => void; changes: Record<string, number | null> }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const prices = useMarketStore(useShallow((s) => s.prices));

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDoc); document.removeEventListener("keydown", onKey); };
  }, [open]);

  return (
    <div ref={ref} className="relative shrink-0 border-r border-hairline">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className="h-full flex items-center gap-2 px-3 rounded-l-lg hover:bg-white/[0.03] transition-colors"
      >
        <span className="w-2 h-2 rounded-full" style={{ backgroundColor: SYMBOL_COLORS[symbol] ?? "#888" }} />
        <span className="text-[15px] font-semibold text-text-primary tracking-tight">{symbol}</span>
        <ChevronDown className={cn("w-3.5 h-3.5 text-text-muted transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <div role="listbox" className="absolute left-0 top-full mt-1 z-30 w-72 rounded-lg border border-hairline bg-bg-elevated py-1 shadow-none">
          {SYMBOLS.map((sym) => {
            const active = sym === symbol;
            const p = prices[sym] || 0;
            return (
              <button
                key={sym}
                type="button"
                role="option"
                aria-selected={active}
                onClick={() => { onChange(sym); setOpen(false); }}
                className={cn("w-full flex items-center gap-2 px-3 h-8 text-[12.5px] hover:bg-white/[0.04]", active && "bg-white/[0.04]")}
              >
                <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: SYMBOL_COLORS[sym] ?? "#888" }} />
                <span className={cn("font-medium whitespace-nowrap", active ? "text-text-primary" : "text-text-secondary")}>{sym}</span>
                <span className="ml-auto num text-text-primary">{p > 0 ? formatPrice(p) : "---"}</span>
                <SignedPct value={changes[sym]} className="w-16 text-right text-[11px]" />
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
