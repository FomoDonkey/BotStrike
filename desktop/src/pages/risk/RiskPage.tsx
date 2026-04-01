import { motion } from "framer-motion";
import { GlassPanel } from "@/components/shared/GlassPanel";
import { MetricCard } from "@/components/shared/MetricCard";
import { useRiskStore } from "@/stores/riskStore";
import { useTradingStore } from "@/stores/tradingStore";
import { formatUSD, formatPct, cn } from "@/lib/utils";
import { Shield, AlertTriangle, CircleOff, Gauge, Zap, TrendingDown } from "lucide-react";

export function RiskPage() {
  const risk = useRiskStore();
  const metrics = useTradingStore((s) => s.metrics);

  const ddPct = risk.drawdown_pct * 100;
  const maxDdPct = risk.max_drawdown_pct * 100;
  const ddRatio = maxDdPct > 0 ? Math.min(ddPct / maxDdPct, 1) : 0;

  return (
    <motion.div
      className="space-y-4"
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
    >
      <h1 className="text-lg font-semibold text-text-primary flex items-center gap-2">
        <Shield className="w-5 h-5 text-accent" /> Risk Monitor
      </h1>

      {/* Circuit Breaker */}
      <GlassPanel
        className="p-6"
        glow={risk.circuit_breaker_active}
      >
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <div className={cn(
              "w-16 h-16 rounded-2xl flex items-center justify-center",
              risk.circuit_breaker_active ? "bg-loss/10" : "bg-profit/10"
            )}>
              {risk.circuit_breaker_active ? (
                <CircleOff className="w-8 h-8 text-loss" />
              ) : (
                <Shield className="w-8 h-8 text-profit" />
              )}
            </div>
            <div>
              <p className="text-sm text-text-secondary">Circuit Breaker</p>
              <p className={cn(
                "text-2xl font-bold",
                risk.circuit_breaker_active ? "text-loss" : "text-profit"
              )}>
                {risk.circuit_breaker_active ? "TRIPPED" : "NORMAL"}
              </p>
            </div>
          </div>
          <div className="text-right">
            <p className="text-xs text-text-muted">Max Drawdown Allowed</p>
            <p className="text-lg font-mono font-semibold text-text-primary">{maxDdPct.toFixed(1)}%</p>
          </div>
        </div>
      </GlassPanel>

      {/* Drawdown Gauge */}
      <GlassPanel className="p-5">
        <div className="flex items-center justify-between mb-3">
          <span className="text-xs text-text-secondary uppercase tracking-wider flex items-center gap-2">
            <TrendingDown className="w-3 h-3" /> Current Drawdown
          </span>
          <span className={cn(
            "text-xl font-mono font-bold",
            ddPct > 7 ? "text-loss" : ddPct > 3 ? "text-warning" : "text-profit"
          )}>
            {ddPct.toFixed(2)}%
          </span>
        </div>
        <div className="w-full h-4 rounded-full bg-white/5 overflow-hidden">
          <div
            className="h-full rounded-full transition-all duration-700"
            style={{
              width: `${ddRatio * 100}%`,
              background: ddPct > 7
                ? "linear-gradient(90deg, #FFA502, #FF4757)"
                : ddPct > 3
                  ? "linear-gradient(90deg, #00D4AA, #FFA502)"
                  : "linear-gradient(90deg, #00D4AA, #00D4AAaa)",
            }}
          />
        </div>
        <div className="flex justify-between mt-1 text-[10px] text-text-muted font-mono">
          <span>0%</span>
          <span>{maxDdPct}% (circuit breaker)</span>
        </div>
      </GlassPanel>

      {/* Risk Metrics Grid */}
      <div className="grid grid-cols-4 gap-3">
        <MetricCard label="Equity" value={risk.equity} format={formatUSD} icon={<Gauge className="w-3 h-3" />} />
        <MetricCard label="Max DD (Hist)" value={metrics.max_drawdown} format={formatPct} icon={<TrendingDown className="w-3 h-3" />} />
        <MetricCard label="Regime" value={0} format={() => risk.regime} icon={<Zap className="w-3 h-3" />} />
        <MetricCard label="Total Trades" value={metrics.total_trades} format={(v) => v.toFixed(0)} icon={<AlertTriangle className="w-3 h-3" />} />
      </div>
    </motion.div>
  );
}
