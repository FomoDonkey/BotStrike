import { motion } from "framer-motion";
import { GlassPanel } from "@/components/shared/GlassPanel";
import { STRATEGY_COLORS, STRATEGY_LABELS } from "@/lib/constants";
import { Brain, ToggleLeft, ToggleRight } from "lucide-react";
import { cn } from "@/lib/utils";

const STRATEGIES = [
  { type: "MEAN_REVERSION", active: true, allocation: 0.40, desc: "RSI + Bollinger + OBI convergence on 15m bars" },
  { type: "ORDER_FLOW_MOMENTUM", active: true, allocation: 0.60, desc: "OBI + Hawkes + Microprice scalping (30-180s hold)" },
  { type: "TREND_FOLLOWING", active: false, allocation: 0, desc: "Disabled — breakout generates 0% win rate" },
  { type: "MARKET_MAKING", active: false, allocation: 0, desc: "Disabled — not profitable with $300 capital" },
];

export function StrategiesPage() {
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
        {STRATEGIES.map((s) => (
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
                  {STRATEGY_LABELS[s.type]}
                </span>
              </div>
              {s.active ? (
                <ToggleRight className="w-6 h-6 text-accent" />
              ) : (
                <ToggleLeft className="w-6 h-6 text-text-muted" />
              )}
            </div>
            <p className="text-xs text-text-secondary mb-4">{s.desc}</p>
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
                s.active ? "bg-profit/10 text-profit" : "bg-white/5 text-text-muted"
              )}>
                {s.active ? "ACTIVE" : "DISABLED"}
              </span>
            </div>
          </GlassPanel>
        ))}
      </div>
    </motion.div>
  );
}
