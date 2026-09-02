import { memo, useEffect, useRef, useState } from "react";
import { useShallow } from "zustand/shallow";
import { useMarketStore } from "@/stores/marketStore";
import { useTradingStore } from "@/stores/tradingStore";
import { useSystemStore } from "@/stores/systemStore";
import { useRiskStore } from "@/stores/riskStore";
import { PulsingDot } from "@/components/shared/PulsingDot";
import { AnimatedNumber } from "@/components/shared/AnimatedNumber";
import { useFlashOnChange } from "@/hooks/useFlash";
import { formatUSD, formatPct, formatDuration } from "@/lib/utils";
import { cn } from "@/lib/utils";
import { Wifi, WifiOff, Clock, Menu } from "lucide-react";
import { SYMBOLS, SYMBOL_LABELS } from "@/lib/constants";
import { useExchangeStore } from "@/stores/exchangeStore";
import { useBridgeConfig } from "@/lib/config";

// Isolated clock — only this re-renders every second
const ClockDisplay = memo(function ClockDisplay() {
  const [time, setTime] = useState(() => new Date());
  useEffect(() => {
    const t = setInterval(() => setTime(new Date()), 1000);
    return () => clearInterval(t);
  }, []);
  return (
    <span className="font-mono text-text-muted">
      {time.toLocaleTimeString("en-US", { hour12: false })}
    </span>
  );
});

const TICK_UP = "text-profit bg-profit/10";
const TICK_DOWN = "text-loss bg-loss/10";

/**
 * Price with a green/red flash on change. The flash is a DOM class toggled from an effect
 * (useFlashOnChange) — no state is written during render, so a burst of ticks can never
 * turn into an update loop.
 */
const PriceTicker = memo(function PriceTicker({ symbol, label }: { symbol: string; label: string }) {
  const price = useMarketStore((s) => s.prices[symbol] || 0);
  const ref = useRef<HTMLSpanElement>(null);
  useFlashOnChange(ref, Number.isFinite(price) ? price : 0, TICK_UP, TICK_DOWN, 400);

  return (
    <div className="flex items-center gap-1.5 shrink-0">
      <span className="text-text-muted font-medium">{label}</span>
      <span
        ref={ref}
        className="font-mono font-semibold text-sm tabular-nums transition-colors duration-200 px-1.5 py-0.5 rounded text-text-primary"
      >
        {price > 0 ? `$${price.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "---"}
      </span>
    </div>
  );
});

interface TopBarProps {
  /** Opens the mobile navigation drawer (button only rendered < lg). */
  onMenu?: () => void;
}

export function TopBar({ onMenu }: TopBarProps) {
  const { equity, pnl, win_rate } = useTradingStore(useShallow((s) => ({
    equity: s.metrics.equity,
    pnl: s.metrics.pnl,
    win_rate: s.metrics.win_rate,
  })));
  const { mode, wsConnected, bridgeConnected, uptimeSec } = useSystemStore(useShallow((s) => ({
    mode: s.mode,
    wsConnected: s.wsConnected,
    bridgeConnected: s.bridgeConnected,
    uptimeSec: s.uptimeSec,
  })));
  const hasPrices = useMarketStore((s) => Object.keys(s.prices).length > 0);
  const hasFeed = bridgeConnected && (wsConnected || hasPrices);
  const regime = useRiskStore((s) => s.regime);
  const exchange = useExchangeStore((s) => s.exchange);
  const { url: bridgeUrl, mode: bridgeMode } = useBridgeConfig();

  const connTitle = !bridgeConnected
    ? `Bridge unreachable: ${bridgeUrl}`
    : hasFeed
      ? `Bridge online (${bridgeUrl}) · market feed live`
      : `Bridge online (${bridgeUrl}) · engine stopped / no market feed`;

  return (
    <header className="flex items-center gap-3 h-11 px-3 sm:px-4 bg-bg-surface/30 backdrop-blur-xl border-b border-white/5 text-xs select-none">
      {/* Hamburger (< lg) */}
      <button
        type="button"
        onClick={onMenu}
        className="lg:hidden shrink-0 -ml-1 p-1.5 rounded-lg text-text-secondary hover:text-text-primary hover:bg-white/5"
        aria-label="Open navigation"
      >
        <Menu className="w-4 h-4" />
      </button>

      {/* Left: Prices + Regime — scrolls horizontally instead of clipping */}
      <div className="flex items-center gap-3 md:gap-4 min-w-0 flex-1 overflow-x-auto scrollbar-none whitespace-nowrap">
        {/* Regime first: it is state, never hidden by the scrolling ticker tail (at 1440 px the
            four tickers alone fill the left group and the chip used to be clipped to one letter). */}
        <div className="flex items-center gap-1.5 shrink-0">
          <span className="text-text-muted hidden md:inline">Regime</span>
          <span className={cn(
            "px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wider",
            regime === "RANGING" && "bg-[#74B9FF]/10 text-[#74B9FF]",
            regime === "TRENDING_UP" && "bg-profit/10 text-profit",
            regime === "TRENDING_DOWN" && "bg-loss/10 text-loss",
            regime === "BREAKOUT" && "bg-[#E84393]/10 text-[#E84393]",
            (regime === "UNKNOWN" || !regime) && "bg-white/5 text-text-muted",
          )}>
            {regime || "UNKNOWN"}
          </span>
        </div>
        <div className="w-px h-4 bg-white/5 shrink-0" />
        {SYMBOLS.map((sym) => (
          <PriceTicker key={sym} symbol={sym} label={SYMBOL_LABELS[sym] || sym} />
        ))}
      </div>

      {/* Center: Equity + PnL (+ WR ≥ lg) */}
      <div className="flex items-center gap-3 lg:gap-5 shrink-0">
        <div className="hidden md:flex items-center gap-1.5">
          <span className="text-text-muted">Equity</span>
          <AnimatedNumber value={equity} format={formatUSD} className="font-mono font-semibold text-text-primary" />
        </div>
        <div className="flex items-center gap-1.5">
          <span className="text-text-muted">PnL</span>
          <AnimatedNumber value={pnl} format={(v) => `${v >= 0 ? "+" : ""}${formatUSD(v)}`} colorize className="font-mono font-semibold" />
        </div>
        <div className="hidden lg:flex items-center gap-1.5">
          <span className="text-text-muted">WR</span>
          <span className="font-mono font-semibold text-text-primary">{formatPct(win_rate)}</span>
        </div>
      </div>

      {/* Right: Exchange + Bridge mode + Mode + Connection + Clock */}
      <div className="flex items-center gap-2 lg:gap-3 shrink-0">
        <span className="px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider bg-white/5 text-text-muted">
          {exchange === "hyperliquid" ? "HL" : "BIN"}
        </span>
        <span
          title={bridgeUrl}
          className={cn(
            "hidden md:inline px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider",
            bridgeMode === "remote" ? "bg-info/10 text-info" : "bg-white/5 text-text-muted",
          )}
        >
          {bridgeMode}
        </span>
        <span className={cn(
          "px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider",
          mode === "live" && "bg-loss/10 text-loss",
          mode === "paper" && "bg-warning/10 text-warning",
          mode === "dry_run" && "bg-info/10 text-info",
        )}>
          {mode}
        </span>
        {/* dot = bridge reachable; icon colour = market feed (green) / bridge only (amber) / offline (red) */}
        <div className="flex items-center gap-1.5" title={connTitle}>
          <PulsingDot active={bridgeConnected} />
          {!bridgeConnected
            ? <WifiOff className="w-3 h-3 text-loss" />
            : <Wifi className={cn("w-3 h-3", hasFeed ? "text-accent" : "text-warning")} />}
        </div>
        <div className="hidden md:flex items-center gap-1 text-text-muted">
          <Clock className="w-3 h-3" />
          <span className="font-mono">{formatDuration(uptimeSec)}</span>
        </div>
        <span className="hidden md:inline">
          <ClockDisplay />
        </span>
      </div>
    </header>
  );
}
