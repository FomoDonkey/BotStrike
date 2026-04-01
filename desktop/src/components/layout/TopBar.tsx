import { useMarketStore } from "@/stores/marketStore";
import { useTradingStore } from "@/stores/tradingStore";
import { useSystemStore } from "@/stores/systemStore";
import { useRiskStore } from "@/stores/riskStore";
import { PulsingDot } from "@/components/shared/PulsingDot";
import { AnimatedNumber } from "@/components/shared/AnimatedNumber";
import { formatUSD, formatPct, formatDuration } from "@/lib/utils";
import { cn } from "@/lib/utils";
import { Wifi, WifiOff, Clock, TrendingUp, TrendingDown, Minus } from "lucide-react";
import { useEffect, useState, useRef } from "react";

function PriceFlash({ price, prev }: { price: number; prev: number }) {
  const [flash, setFlash] = useState<"up" | "down" | null>(null);
  const lastPrice = useRef(prev);

  useEffect(() => {
    if (price === 0 || price === lastPrice.current) return;
    setFlash(price > lastPrice.current ? "up" : "down");
    lastPrice.current = price;
    const t = setTimeout(() => setFlash(null), 400);
    return () => clearTimeout(t);
  }, [price]);

  return (
    <span className={cn(
      "font-mono font-semibold text-sm tabular-nums transition-all duration-200 px-1.5 py-0.5 rounded",
      flash === "up" && "text-profit bg-profit/10",
      flash === "down" && "text-loss bg-loss/10",
      !flash && "text-text-primary",
    )}>
      {price > 0 ? `$${price.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "---"}
    </span>
  );
}

export function TopBar() {
  const btcPrice = useMarketStore((s) => s.prices["BTCUSDT"] || s.prices["BTC-USD"] || 0);
  const btcPrev = useMarketStore((s) => s.prevPrices["BTCUSDT"] || s.prevPrices["BTC-USD"] || 0);
  const ethPrice = useMarketStore((s) => s.prices["ETHUSDT"] || s.prices["ETH-USD"] || 0);
  const ethPrev = useMarketStore((s) => s.prevPrices["ETHUSDT"] || s.prevPrices["ETH-USD"] || 0);
  const metrics = useTradingStore((s) => s.metrics);
  const system = useSystemStore();
  const regime = useRiskStore((s) => s.regime);
  const [time, setTime] = useState(new Date());

  useEffect(() => {
    const t = setInterval(() => setTime(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  return (
    <header className="flex items-center justify-between h-11 px-4 bg-bg-surface/30 backdrop-blur-xl border-b border-white/5 text-xs select-none">
      {/* Left: Prices + Regime */}
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-1.5">
          <span className="text-text-muted font-medium">BTC</span>
          <PriceFlash price={btcPrice} prev={btcPrev} />
        </div>
        <div className="flex items-center gap-1.5">
          <span className="text-text-muted font-medium">ETH</span>
          <PriceFlash price={ethPrice} prev={ethPrev} />
        </div>

        <div className="w-px h-4 bg-white/5" />

        <div className="flex items-center gap-1.5">
          <span className="text-text-muted">Regime</span>
          <span className={cn(
            "px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wider",
            regime === "RANGING" && "bg-[#74B9FF]/10 text-[#74B9FF]",
            regime === "TRENDING_UP" && "bg-profit/10 text-profit",
            regime === "TRENDING_DOWN" && "bg-loss/10 text-loss",
            regime === "BREAKOUT" && "bg-[#E84393]/10 text-[#E84393]",
            regime === "UNKNOWN" && "bg-white/5 text-text-muted",
          )}>
            {regime}
          </span>
        </div>
      </div>

      {/* Center: Equity + PnL */}
      <div className="flex items-center gap-5">
        <div className="flex items-center gap-1.5">
          <span className="text-text-muted">Equity</span>
          <AnimatedNumber
            value={metrics.equity}
            format={formatUSD}
            className="font-mono font-semibold text-text-primary"
          />
        </div>
        <div className="flex items-center gap-1.5">
          <span className="text-text-muted">PnL</span>
          <AnimatedNumber
            value={metrics.pnl}
            format={(v) => `${v >= 0 ? "+" : ""}${formatUSD(v)}`}
            colorize
            className="font-mono font-semibold"
          />
        </div>
        <div className="flex items-center gap-1.5">
          <span className="text-text-muted">WR</span>
          <span className="font-mono font-semibold text-text-primary">
            {formatPct(metrics.win_rate)}
          </span>
        </div>
      </div>

      {/* Right: Mode + Connection + Clock */}
      <div className="flex items-center gap-3">
        <span className={cn(
          "px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider",
          system.mode === "live" && "bg-loss/10 text-loss shadow-[0_0_8px_rgba(255,71,87,0.15)]",
          system.mode === "paper" && "bg-warning/10 text-warning",
          system.mode === "dry_run" && "bg-info/10 text-info",
        )}>
          {system.mode}
        </span>

        <div className="flex items-center gap-1.5">
          <PulsingDot active={system.wsConnected} />
          {system.wsConnected ? (
            <Wifi className="w-3 h-3 text-accent" />
          ) : (
            <WifiOff className="w-3 h-3 text-loss" />
          )}
        </div>

        <div className="flex items-center gap-1 text-text-muted">
          <Clock className="w-3 h-3" />
          <span className="font-mono">{formatDuration(system.uptimeSec)}</span>
        </div>

        <span className="font-mono text-text-muted">
          {time.toLocaleTimeString("en-US", { hour12: false })}
        </span>
      </div>
    </header>
  );
}
