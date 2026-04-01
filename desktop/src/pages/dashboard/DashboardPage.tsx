import { motion } from "framer-motion";
import { GlassPanel } from "@/components/shared/GlassPanel";
import { MetricCard } from "@/components/shared/MetricCard";
import { AnimatedNumber } from "@/components/shared/AnimatedNumber";
import { useMarketStore } from "@/stores/marketStore";
import { useTradingStore } from "@/stores/tradingStore";
import { useRiskStore } from "@/stores/riskStore";
import { useMicroStore } from "@/stores/microStore";
import { useSystemStore } from "@/stores/systemStore";
import { formatUSD, formatPct, formatPrice, cn } from "@/lib/utils";
import { STRATEGY_COLORS, STRATEGY_LABELS, REGIME_COLORS } from "@/lib/constants";
import {
  DollarSign, TrendingUp, Target, BarChart3, ShieldAlert, Activity,
  Zap, CircleDot,
} from "lucide-react";

const stagger = {
  hidden: {},
  show: { transition: { staggerChildren: 0.05 } },
};

const fadeUp = {
  hidden: { opacity: 0, y: 12 },
  show: { opacity: 1, y: 0, transition: { duration: 0.3 } },
};

export function DashboardPage() {
  const btcPrice = useMarketStore((s) => s.prices["BTCUSDT"] || s.prices["BTC-USD"] || 0);
  const ethPrice = useMarketStore((s) => s.prices["ETHUSDT"] || s.prices["ETH-USD"] || 0);
  const metrics = useTradingStore((s) => s.metrics);
  const risk = useRiskStore();
  const system = useSystemStore();
  const positions = useTradingStore((s) => s.positions);
  const signals = useTradingStore((s) => s.recentSignals);
  const micro = useMicroStore((s) => s.snapshots);

  const allPositions = Object.values(positions).flat();
  const floatingPnl = allPositions.reduce((sum, p) => sum + p.unrealized_pnl, 0);

  return (
    <motion.div
      className="space-y-4"
      variants={stagger}
      initial="hidden"
      animate="show"
    >
      {/* Hero: Portfolio Value */}
      <motion.div variants={fadeUp}>
        <GlassPanel glow className="p-6">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-xs text-text-secondary uppercase tracking-wider mb-1">Portfolio Value</p>
              <AnimatedNumber
                value={metrics.equity}
                format={formatUSD}
                className="text-4xl font-bold font-mono text-text-primary"
              />
              <div className="flex items-center gap-3 mt-2">
                <AnimatedNumber
                  value={metrics.pnl}
                  format={(v) => `${v >= 0 ? "+" : ""}${formatUSD(v)}`}
                  colorize
                  className="text-sm font-mono font-medium"
                />
                <AnimatedNumber
                  value={metrics.equity > 0 ? metrics.pnl / (metrics.equity - metrics.pnl || 300) : 0}
                  format={(v) => `${v >= 0 ? "+" : ""}${formatPct(v)}`}
                  colorize
                  className="text-sm font-mono"
                />
              </div>
            </div>
            <div className="flex gap-6">
              {/* BTC mini ticker */}
              <div className="text-right">
                <p className="text-[10px] text-text-muted uppercase">BTC</p>
                <p className="font-mono text-lg font-semibold text-text-primary">
                  {btcPrice > 0 ? formatPrice(btcPrice) : "---"}
                </p>
              </div>
              {/* ETH mini ticker */}
              <div className="text-right">
                <p className="text-[10px] text-text-muted uppercase">ETH</p>
                <p className="font-mono text-lg font-semibold text-text-primary">
                  {ethPrice > 0 ? formatPrice(ethPrice) : "---"}
                </p>
              </div>
            </div>
          </div>
        </GlassPanel>
      </motion.div>

      {/* Key Metrics Grid */}
      <motion.div variants={fadeUp} className="grid grid-cols-4 gap-3">
        <MetricCard
          label="Sharpe Ratio"
          value={metrics.sharpe_ratio}
          format={(v) => v.toFixed(2)}
          icon={<BarChart3 className="w-3 h-3" />}
        />
        <MetricCard
          label="Win Rate"
          value={metrics.win_rate}
          format={formatPct}
          icon={<Target className="w-3 h-3" />}
        />
        <MetricCard
          label="Max Drawdown"
          value={risk.drawdown_pct}
          format={formatPct}
          colorize
          icon={<ShieldAlert className="w-3 h-3" />}
        />
        <MetricCard
          label="Total Fees"
          value={metrics.total_fees}
          format={formatUSD}
          icon={<DollarSign className="w-3 h-3" />}
        />
      </motion.div>

      {/* Bottom Grid: Positions, Signals, Microstructure */}
      <motion.div variants={fadeUp} className="grid grid-cols-3 gap-3">
        {/* Open Positions */}
        <GlassPanel className="p-4">
          <h3 className="text-xs text-text-secondary uppercase tracking-wider mb-3 flex items-center gap-2">
            <CircleDot className="w-3 h-3" /> Open Positions
          </h3>
          {allPositions.length === 0 ? (
            <p className="text-text-muted text-sm">No open positions</p>
          ) : (
            <div className="space-y-2">
              {allPositions.map((p, i) => (
                <div key={i} className="flex items-center justify-between text-sm">
                  <div className="flex items-center gap-2">
                    <span className={cn(
                      "text-[10px] font-bold px-1.5 py-0.5 rounded",
                      p.side === "BUY" ? "bg-profit/10 text-profit" : "bg-loss/10 text-loss"
                    )}>
                      {p.side}
                    </span>
                    <span className="font-mono text-text-primary">{p.symbol}</span>
                  </div>
                  <AnimatedNumber
                    value={p.unrealized_pnl}
                    format={formatUSD}
                    colorize
                    className="font-mono text-sm"
                  />
                </div>
              ))}
            </div>
          )}
        </GlassPanel>

        {/* Recent Signals */}
        <GlassPanel className="p-4">
          <h3 className="text-xs text-text-secondary uppercase tracking-wider mb-3 flex items-center gap-2">
            <Zap className="w-3 h-3" /> Recent Signals
          </h3>
          {signals.length === 0 ? (
            <p className="text-text-muted text-sm">No signals yet</p>
          ) : (
            <div className="space-y-2 max-h-48 overflow-y-auto">
              {[...signals].reverse().slice(0, 8).map((s, i) => (
                <div key={i} className="flex items-center justify-between text-xs">
                  <div className="flex items-center gap-2">
                    <span
                      className="w-1.5 h-1.5 rounded-full"
                      style={{ backgroundColor: STRATEGY_COLORS[s.strategy] || "#4A5568" }}
                    />
                    <span className="text-text-secondary">
                      {STRATEGY_LABELS[s.strategy] || s.strategy}
                    </span>
                  </div>
                  <span className={cn(
                    "font-mono",
                    s.side === "BUY" ? "text-profit" : "text-loss"
                  )}>
                    {s.side} {s.symbol}
                  </span>
                </div>
              ))}
            </div>
          )}
        </GlassPanel>

        {/* Microstructure Quick View */}
        <GlassPanel className="p-4">
          <h3 className="text-xs text-text-secondary uppercase tracking-wider mb-3 flex items-center gap-2">
            <Activity className="w-3 h-3" /> Microstructure
          </h3>
          {Object.keys(micro).length === 0 ? (
            <p className="text-text-muted text-sm">Waiting for data...</p>
          ) : (
            <div className="space-y-3">
              {Object.entries(micro).map(([sym, data]) => (
                <div key={sym} className="space-y-1.5">
                  <span className="text-xs font-mono text-text-muted">{sym}</span>
                  {data.vpin && (
                    <div className="flex items-center justify-between text-xs">
                      <span className="text-[#E84393]">VPIN</span>
                      <div className="flex items-center gap-2">
                        <div className="w-20 h-1.5 rounded-full bg-white/5 overflow-hidden">
                          <div
                            className="h-full rounded-full transition-all duration-500"
                            style={{
                              width: `${(data.vpin.vpin * 100)}%`,
                              backgroundColor: data.vpin.is_toxic ? "#FF4757" : "#E84393",
                            }}
                          />
                        </div>
                        <span className="font-mono w-10 text-right">{(data.vpin.vpin * 100).toFixed(0)}%</span>
                      </div>
                    </div>
                  )}
                  {data.hawkes && (
                    <div className="flex items-center justify-between text-xs">
                      <span className="text-[#FF7675]">Hawkes</span>
                      <span className={cn(
                        "font-mono",
                        data.hawkes.is_spike ? "text-loss" : "text-text-secondary"
                      )}>
                        {data.hawkes.multiplier.toFixed(1)}x
                      </span>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </GlassPanel>
      </motion.div>
    </motion.div>
  );
}
