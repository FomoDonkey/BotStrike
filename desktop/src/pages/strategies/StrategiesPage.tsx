import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { GlassPanel } from "@/components/shared/GlassPanel";
import { STRATEGY_COLORS, STRATEGY_LABELS } from "@/lib/constants";
import { Brain, ToggleLeft, ToggleRight } from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";

interface StrategyInfo {
  type: string;
  active: boolean;
  allocation: number;
  name: string;
  killed?: boolean;
  kill_reason?: string;
}

const FALLBACK_STRATEGIES: StrategyInfo[] = [
  { type: "MEAN_REVERSION", active: true, allocation: 0.50, name: "MeanReversionStrategy" },
  { type: "FIBONACCI_RETRACEMENT", active: true, allocation: 0.50, name: "FibonacciRetracementStrategy" },
  { type: "ORDER_FLOW_MOMENTUM", active: false, allocation: 0, name: "OrderFlowMomentumStrategy" },
  { type: "TREND_FOLLOWING", active: false, allocation: 0, name: "TrendFollowingStrategy" },
  { type: "MARKET_MAKING", active: false, allocation: 0, name: "MarketMakingStrategy" },
];

const STRATEGY_DESCS: Record<string, string> = {
  MEAN_REVERSION: "5m pullback in 1H trend direction — RSI + BB + trailing stop (ETH/SOL/ADA)",
  FIBONACCI_RETRACEMENT: "15m impulse-retracement at 50-61.8% Fib zone — R:R 3.6:1 (BTC)",
  ORDER_FLOW_MOMENTUM: "OBI + Hawkes + Microprice scalping (archived)",
  TREND_FOLLOWING: "EMA crossover + ADX confirmation (archived)",
  MARKET_MAKING: "Avellaneda-Stoikov dynamic spreads (archived)",
};

export function StrategiesPage() {
  const [strategies, setStrategies] = useState<StrategyInfo[]>(FALLBACK_STRATEGIES);

  useEffect(() => {
    api.strategies()
      .then((data) => {
        if (data?.strategies?.length > 0) {
          setStrategies(data.strategies);
        }
      })
      .catch(() => {});
  }, []);

  return (
    <motion.div
      className="space-y-4"
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
    >
      <h1 className="text-lg font-semibold text-text-primary flex items-center gap-2">
        <Brain className="w-5 h-5 text-accent" /> Strategy Manager
      </h1>

      <div className="grid grid-cols-2 gap-4">
        {strategies.map((s) => (
          <GlassPanel
            key={s.type}
            className="p-5"
            glow={s.active}
          >
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-3">
                <div
                  className="w-3 h-3 rounded-full"
                  style={{ backgroundColor: STRATEGY_COLORS[s.type] }}
                />
                <span className="font-semibold text-text-primary">
                  {STRATEGY_LABELS[s.type] || s.type}
                </span>
              </div>
              {s.active ? (
                <ToggleRight className="w-6 h-6 text-accent" />
              ) : (
                <ToggleLeft className="w-6 h-6 text-text-muted" />
              )}
            </div>
            <p className="text-xs text-text-secondary mb-4">
              {STRATEGY_DESCS[s.type] || s.name}
            </p>
            <div className="flex items-center justify-between text-xs">
              <span className="text-text-muted">Allocation</span>
              <div className="flex items-center gap-2">
                <div className="w-24 h-1.5 rounded-full bg-white/5 overflow-hidden">
                  <div
                    className="h-full rounded-full"
                    style={{
                      width: `${s.allocation * 100}%`,
                      backgroundColor: STRATEGY_COLORS[s.type],
                    }}
                  />
                </div>
                <span className="font-mono" style={{ color: STRATEGY_COLORS[s.type] }}>
                  {(s.allocation * 100).toFixed(0)}%
                </span>
              </div>
            </div>
            <div className="flex items-center justify-between text-xs mt-2">
              <span className="text-text-muted">Status</span>
              <span className={cn(
                "px-2 py-0.5 rounded text-[10px] font-semibold uppercase",
                s.killed ? "bg-loss/10 text-loss" :
                s.active ? "bg-profit/10 text-profit" : "bg-white/5 text-text-muted"
              )}>
                {s.killed ? "KILLED" : s.active ? "ACTIVE" : "DISABLED"}
              </span>
            </div>
            {s.killed && s.kill_reason && (
              <div className="text-[10px] text-loss/80 mt-1 font-mono truncate">
                {s.kill_reason}
              </div>
            )}
          </GlassPanel>
        ))}
      </div>
    </motion.div>
  );
}
