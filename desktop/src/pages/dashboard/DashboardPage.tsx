import { useMemo } from "react";
import { motion } from "framer-motion";
import { GlassPanel } from "@/components/shared/GlassPanel";
import { MetricCard } from "@/components/shared/MetricCard";
import { AnimatedNumber } from "@/components/shared/AnimatedNumber";
import { useMarketStore } from "@/stores/marketStore";
import { useTradingStore } from "@/stores/tradingStore";
import { useRiskStore } from "@/stores/riskStore";
import { useMicroStore } from "@/stores/microStore";
import { formatUSD, formatPct, formatPrice, cn } from "@/lib/utils";
import { STRATEGY_COLORS, STRATEGY_LABELS } from "@/lib/constants";
import { PieChart, Pie, Cell, ResponsiveContainer } from "recharts";
import {
  DollarSign, TrendingUp, Target, BarChart3, ShieldAlert, Activity,
  Zap, CircleDot, ArrowUpRight, ArrowDownRight,
} from "lucide-react";

const stagger = {
  hidden: {},
  show: { transition: { staggerChildren: 0.06 } },
};

const fadeUp = {
  hidden: { opacity: 0, y: 16 },
  show: { opacity: 1, y: 0, transition: { duration: 0.35, ease: "easeOut" as const } },
};

const ALLOCATION_DATA = [
  { name: "Mean Reversion", value: 40, color: "#6C5CE7" },
  { name: "Order Flow", value: 60, color: "#00CEC9" },
];

export function DashboardPage() {
  const btcPrice = useMarketStore((s) => s.prices["BTCUSDT"] || s.prices["BTC-USD"] || 0);
  const btcPrev = useMarketStore((s) => s.prevPrices["BTCUSDT"] || s.prevPrices["BTC-USD"] || 0);
  const ethPrice = useMarketStore((s) => s.prices["ETHUSDT"] || s.prices["ETH-USD"] || 0);
  const metrics = useTradingStore((s) => s.metrics);
  const risk = useRiskStore();
  const positions = useTradingStore((s) => s.positions);
  const signals = useTradingStore((s) => s.recentSignals);
  const micro = useMicroStore((s) => s.snapshots);

  const allPositions = useMemo(() => Object.values(positions).flat(), [positions]);
  const btcUp = btcPrice > btcPrev;

  return (
    <motion.div
      className="space-y-4"
      variants={stagger}
      initial="hidden"
      animate="show"
    >
      {/* Hero: Portfolio Value + Tickers */}
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
              <div className="flex items-center gap-4 mt-2">
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
                <span className="text-xs text-text-muted">{metrics.total_trades} trades</span>
              </div>
            </div>
            {/* Mini Tickers */}
            <div className="flex gap-5">
              <div className="text-right">
                <p className="text-[10px] text-text-muted uppercase tracking-wider">BTC</p>
                <div className="flex items-center gap-1 justify-end">
                  <p className={cn("font-mono text-lg font-semibold", btcUp ? "text-profit" : "text-loss")}>
                    {btcPrice > 0 ? `$${formatPrice(btcPrice)}` : "---"}
                  </p>
                  {btcPrice > 0 && (btcUp ?
                    <ArrowUpRight className="w-3.5 h-3.5 text-profit" /> :
                    <ArrowDownRight className="w-3.5 h-3.5 text-loss" />
                  )}
                </div>
              </div>
              <div className="text-right">
                <p className="text-[10px] text-text-muted uppercase tracking-wider">ETH</p>
                <p className="font-mono text-lg font-semibold text-text-primary">
                  {ethPrice > 0 ? `$${formatPrice(ethPrice)}` : "---"}
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
          label="Drawdown"
          value={risk.drawdown_pct}
          format={formatPct}
          icon={<ShieldAlert className="w-3 h-3" />}
          glow={risk.circuit_breaker_active}
          subtext={risk.circuit_breaker_active ? "CIRCUIT BREAKER ACTIVE" : `Max ${formatPct(risk.max_drawdown_pct)}`}
        />
        <MetricCard
          label="Total Fees"
          value={metrics.total_fees}
          format={formatUSD}
          icon={<DollarSign className="w-3 h-3" />}
        />
      </motion.div>

      {/* Bottom: Allocation Donut + Positions + Signals + Micro */}
      <motion.div variants={fadeUp} className="grid grid-cols-4 gap-3">
        {/* Strategy Allocation Donut */}
        <GlassPanel className="p-4 flex flex-col items-center">
          <h3 className="text-xs text-text-secondary uppercase tracking-wider mb-2 self-start">Allocation</h3>
          <ResponsiveContainer width={120} height={120}>
            <PieChart>
              <Pie
                data={ALLOCATION_DATA}
                cx="50%"
                cy="50%"
                innerRadius={36}
                outerRadius={52}
                paddingAngle={4}
                dataKey="value"
                strokeWidth={0}
              >
                {ALLOCATION_DATA.map((entry, i) => (
                  <Cell key={i} fill={entry.color} />
                ))}
              </Pie>
            </PieChart>
          </ResponsiveContainer>
          <div className="space-y-1 mt-1 w-full">
            {ALLOCATION_DATA.map((s) => (
              <div key={s.name} className="flex items-center justify-between text-[10px]">
                <div className="flex items-center gap-1.5">
                  <span className="w-2 h-2 rounded-full" style={{ backgroundColor: s.color }} />
                  <span className="text-text-secondary">{s.name}</span>
                </div>
                <span className="font-mono" style={{ color: s.color }}>{s.value}%</span>
              </div>
            ))}
          </div>
        </GlassPanel>

        {/* Open Positions */}
        <GlassPanel className="p-4">
          <h3 className="text-xs text-text-secondary uppercase tracking-wider mb-3 flex items-center gap-2">
            <CircleDot className="w-3 h-3" /> Positions
          </h3>
          {allPositions.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-32 text-text-muted">
              <CircleDot className="w-6 h-6 opacity-20 mb-2" />
              <p className="text-xs">No open positions</p>
            </div>
          ) : (
            <div className="space-y-2">
              {allPositions.map((p, i) => (
                <div key={i} className="flex items-center justify-between text-sm p-2 rounded-lg bg-white/[0.02]">
                  <div className="flex items-center gap-2">
                    <span className={cn(
                      "text-[10px] font-bold px-1.5 py-0.5 rounded",
                      p.side === "BUY" ? "bg-profit/10 text-profit" : "bg-loss/10 text-loss"
                    )}>
                      {p.side}
                    </span>
                    <span className="font-mono text-text-primary text-xs">{p.symbol}</span>
                  </div>
                  <AnimatedNumber
                    value={p.unrealized_pnl}
                    format={formatUSD}
                    colorize
                    className="font-mono text-xs"
                  />
                </div>
              ))}
            </div>
          )}
        </GlassPanel>

        {/* Recent Signals */}
        <GlassPanel className="p-4">
          <h3 className="text-xs text-text-secondary uppercase tracking-wider mb-3 flex items-center gap-2">
            <Zap className="w-3 h-3" /> Signals
          </h3>
          {signals.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-32 text-text-muted">
              <Zap className="w-6 h-6 opacity-20 mb-2" />
              <p className="text-xs">No signals yet</p>
            </div>
          ) : (
            <div className="space-y-1.5 max-h-48 overflow-y-auto">
              {[...signals].reverse().slice(0, 8).map((s, i) => (
                <motion.div
                  key={`${s.timestamp}-${i}`}
                  className="flex items-center justify-between text-xs p-1.5 rounded-lg hover:bg-white/[0.02]"
                  initial={{ opacity: 0, x: 12 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: i * 0.03 }}
                >
                  <div className="flex items-center gap-2">
                    <span
                      className="w-1.5 h-1.5 rounded-full"
                      style={{ backgroundColor: STRATEGY_COLORS[s.strategy] || "#4A5568" }}
                    />
                    <span className="text-text-secondary">
                      {STRATEGY_LABELS[s.strategy] || s.strategy}
                    </span>
                  </div>
                  <span className={cn("font-mono", s.side === "BUY" ? "text-profit" : "text-loss")}>
                    {s.side}
                  </span>
                </motion.div>
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
            <div className="flex flex-col items-center justify-center h-32 text-text-muted">
              <Activity className="w-6 h-6 opacity-20 mb-2" />
              <p className="text-xs">Waiting for data...</p>
            </div>
          ) : (
            <div className="space-y-3">
              {Object.entries(micro).map(([sym, data]) => (
                <div key={sym} className="space-y-2">
                  <span className="text-[10px] font-mono text-text-muted">{sym}</span>
                  {/* VPIN Bar */}
                  {data.vpin && (
                    <div>
                      <div className="flex items-center justify-between text-[10px] mb-1">
                        <span className="text-[#E84393]">VPIN</span>
                        <span className="font-mono">{(data.vpin.vpin * 100).toFixed(0)}%</span>
                      </div>
                      <div className="w-full h-1.5 rounded-full bg-white/5 overflow-hidden">
                        <motion.div
                          className="h-full rounded-full"
                          style={{ backgroundColor: data.vpin.is_toxic ? "#FF4757" : "#E84393" }}
                          initial={{ width: 0 }}
                          animate={{ width: `${data.vpin.vpin * 100}%` }}
                          transition={{ duration: 0.5, ease: "easeOut" }}
                        />
                      </div>
                    </div>
                  )}
                  {/* Hawkes */}
                  {data.hawkes && (
                    <div className="flex items-center justify-between text-[10px]">
                      <span className="text-[#FF7675]">Hawkes</span>
                      <span className={cn(
                        "font-mono px-1.5 py-0.5 rounded",
                        data.hawkes.is_spike ? "bg-loss/10 text-loss" : "text-text-secondary"
                      )}>
                        {data.hawkes.multiplier.toFixed(1)}x
                      </span>
                    </div>
                  )}
                  {/* Risk Score Bar */}
                  <div>
                    <div className="flex items-center justify-between text-[10px] mb-1">
                      <span className="text-text-muted">Risk</span>
                      <span className="font-mono">{data.risk_score?.toFixed(2) ?? "---"}</span>
                    </div>
                    <div className="w-full h-1 rounded-full bg-white/5 overflow-hidden">
                      <motion.div
                        className="h-full rounded-full"
                        style={{
                          background: (data.risk_score || 0) > 0.6
                            ? "linear-gradient(90deg, #FFA502, #FF4757)"
                            : "linear-gradient(90deg, #00D4AA, #00D4AAaa)",
                        }}
                        initial={{ width: 0 }}
                        animate={{ width: `${(data.risk_score || 0) * 100}%` }}
                        transition={{ duration: 0.5 }}
                      />
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </GlassPanel>
      </motion.div>
    </motion.div>
  );
}
