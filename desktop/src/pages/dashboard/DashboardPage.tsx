import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useShallow } from "zustand/shallow";
import { motion } from "framer-motion";
import { api, type EdgeResponse, type PerformanceResponse, type StrategyInfo, type TrendResponse } from "@/lib/api";
import { GlassPanel } from "@/components/shared/GlassPanel";
import { MetricCard } from "@/components/shared/MetricCard";
import { AnimatedNumber } from "@/components/shared/AnimatedNumber";
import { VerdictChip } from "@/components/shared/VerdictChip";
import { HoldTime, PnlCell, SideChip, StrategyTag } from "@/components/shared/TradeChips";
import { positionHoldSec, positionRoe } from "@/lib/market";
import { useMarketStore } from "@/stores/marketStore";
import { useTradingStore } from "@/stores/tradingStore";
import { useRiskStore } from "@/stores/riskStore";
import { useMicroStore } from "@/stores/microStore";
import { usePolling } from "@/hooks/usePolling";
import { useNow } from "@/hooks/useNow";
import { formatUSD, formatPct, formatPrice, formatLocalDateTime, formatRelative, cn } from "@/lib/utils";
import { STRATEGY_COLORS, STRATEGY_LABELS, SYMBOLS, SYMBOL_LABELS, SYMBOL_COLORS } from "@/lib/constants";
import { PieChart, Pie, Cell, ResponsiveContainer } from "recharts";
import {
  DollarSign, Target, BarChart3, ShieldAlert, Activity,
  Zap, CircleDot, ArrowUpRight, ArrowDownRight, TrendingUp, Gauge,
} from "lucide-react";

const stagger = {
  hidden: {},
  show: { transition: { staggerChildren: 0.03 } },
};

const fadeUp = {
  hidden: { opacity: 0, y: 8 },
  show: { opacity: 1, y: 0, transition: { duration: 0.2, ease: "easeOut" as const } },
};

const PERF_POLL_MS = 10_000;
const SLOW_POLL_MS = 30_000;

export function DashboardPage() {
  const [perf, setPerf] = useState<PerformanceResponse | null>(null);
  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [trend, setTrend] = useState<TrendResponse | null>(null);
  const [trendError, setTrendError] = useState(false);
  const [edge, setEdge] = useState<EdgeResponse | null>(null);
  const [edgeError, setEdgeError] = useState(false);
  const now = useNow();

  usePolling(() => api.performance().then(setPerf).catch(() => {}), PERF_POLL_MS);
  usePolling(() => api.strategies().then((r) => setStrategies(r.strategies ?? [])).catch(() => {}), SLOW_POLL_MS);
  usePolling(() => api.trend().then((t) => { setTrend(t); setTrendError(false); }).catch(() => setTrendError(true)), SLOW_POLL_MS);
  usePolling(() => api.edge().then((e) => { setEdge(e); setEdgeError(false); }).catch(() => setEdgeError(true)), SLOW_POLL_MS);

  const prices = useMarketStore(useShallow((s) => s.prices));
  const prevPrices = useMarketStore(useShallow((s) => s.prevPrices));
  const metrics = useTradingStore(useShallow((s) => s.metrics));
  const sessionDrawdown = useRiskStore((s) => s.drawdown_pct);
  const max_drawdown_pct = useRiskStore((s) => s.max_drawdown_pct);
  const circuit_breaker_active = useRiskStore((s) => s.circuit_breaker_active);
  const positions = useTradingStore(useShallow((s) => s.positions));
  const signals = useTradingStore(useShallow((s) => s.recentSignals));
  const micro = useMicroStore(useShallow((s) => s.snapshots));

  const allPositions = useMemo(() => Object.values(positions).flat(), [positions]);
  const recentSignals = useMemo(() => [...signals].reverse().slice(0, 8), [signals]);

  // Allocation donut from the real strategy list — enabled with allocation > 0, normalised.
  // No fake 50/50 when nothing is enabled: that is exactly what the empty state is for.
  const allocation = useMemo(() => {
    const on = strategies.filter((s) => (s.enabled ?? s.allocation > 0) && s.allocation > 0);
    const total = on.reduce((a, s) => a + s.allocation, 0);
    if (total <= 0) return [];
    return on.map((s) => ({
      type: s.type,
      name: STRATEGY_LABELS[s.type] ?? s.name ?? s.type,
      value: Math.round((s.allocation / total) * 1000) / 10,
      color: STRATEGY_COLORS[s.type] ?? "#6B7280",
    }));
  }, [strategies]);

  // All-time drawdown from /api/performance; the WS risk_update value is the SESSION drawdown
  // (resets on every engine restart) and is only a fallback for an older bridge.
  const currentDrawdown = perf?.current_drawdown ?? sessionDrawdown;
  const maxDrawdown = perf?.max_drawdown ?? metrics.max_drawdown;
  const sharpeValid = perf ? perf.sharpe_valid !== false : true;
  const sharpe = perf?.sharpe_ratio ?? metrics.sharpe_ratio;
  const edgeRows = useMemo(() => Object.entries(edge?.strategies ?? {}), [edge]);
  const nextRunMs = trend?.next_run_utc ? Date.parse(trend.next_run_utc) : Number.NaN;

  return (
    <motion.div
      className="space-y-4"
      variants={stagger}
      initial="hidden"
      animate="show"
    >
      {/* Hero: Portfolio Value + Tickers */}
      <motion.div variants={fadeUp}>
        <GlassPanel glow className="p-4 sm:p-6">
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
            <div className="min-w-0">
              <p className="text-xs text-text-secondary uppercase tracking-wider mb-1">Portfolio Value</p>
              <AnimatedNumber
                value={metrics.equity}
                format={formatUSD}
                className="text-3xl sm:text-4xl font-bold font-mono text-text-primary"
              />
              <div className="flex items-center gap-4 mt-2 flex-wrap">
                <AnimatedNumber
                  value={metrics.pnl}
                  format={(v) => `${v >= 0 ? "+" : ""}${formatUSD(v)}`}
                  colorize
                  className="text-sm font-mono font-medium"
                />
                <AnimatedNumber
                  value={metrics.equity > 0 ? metrics.pnl / Math.max(metrics.equity - metrics.pnl, 1) : 0}
                  format={(v) => `${v >= 0 ? "+" : ""}${formatPct(v)}`}
                  colorize
                  className="text-sm font-mono"
                />
                <span className="text-xs text-text-muted">{metrics.total_trades} trades</span>
              </div>
            </div>
            {/* Mini Tickers — all symbols */}
            <div className="grid grid-cols-2 sm:flex gap-3 sm:gap-4">
              {SYMBOLS.map((sym) => {
                const p = prices[sym] || 0;
                const prev = prevPrices[sym] || 0;
                const up = p > prev;
                return (
                  <div key={sym} className="sm:text-right">
                    <p className="text-[10px] uppercase tracking-wider" style={{ color: SYMBOL_COLORS[sym] || "#888" }}>
                      {SYMBOL_LABELS[sym]}
                    </p>
                    <div className="flex items-center gap-1 sm:justify-end">
                      <p className={cn("font-mono text-base font-semibold", p > 0 && (up ? "text-profit" : "text-loss"), p === 0 && "text-text-muted")}>
                        {p > 0 ? `$${formatPrice(p)}` : "---"}
                      </p>
                      {p > 0 && (up ?
                        <ArrowUpRight className="w-3 h-3 text-profit" /> :
                        <ArrowDownRight className="w-3 h-3 text-loss" />
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </GlassPanel>
      </motion.div>

      {/* Key Metrics Grid */}
      <motion.div variants={fadeUp} className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
        <MetricCard
          label="Sharpe Ratio"
          value={sharpe}
          format={(v) => v.toFixed(2)}
          display={sharpeValid ? undefined : "n/a"}
          subtext={sharpeValid
            ? (perf?.sample_days ? `${perf.sample_days} days · ${perf.total_trades} trades` : undefined)
            : "needs 30 days · 30 trades"}
          icon={<BarChart3 className="w-3 h-3" />}
        />
        <MetricCard
          label="Win Rate"
          value={perf?.win_rate ?? metrics.win_rate}
          format={formatPct}
          icon={<Target className="w-3 h-3" />}
        />
        <MetricCard
          label="Drawdown"
          value={currentDrawdown}
          format={formatPct}
          icon={<ShieldAlert className="w-3 h-3" />}
          glow={circuit_breaker_active}
          subtext={circuit_breaker_active
            ? "CIRCUIT BREAKER ACTIVE"
            : `Limit ${formatPct(max_drawdown_pct)} · Max ${formatPct(maxDrawdown)}`}
        />
        <MetricCard
          label="Total Fees"
          value={perf?.total_fees ?? metrics.total_fees}
          format={formatUSD}
          icon={<DollarSign className="w-3 h-3" />}
        />
      </motion.div>

      {/* Trend daily + Edge monitor */}
      <motion.div variants={fadeUp} className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <GlassPanel className="p-4 min-w-0">
          <h3 className="text-xs text-text-secondary uppercase tracking-wider mb-3 flex items-center gap-2">
            <TrendingUp className="w-3 h-3" style={{ color: STRATEGY_COLORS.TREND_DAILY }} /> Trend daily
            <Link to="/strategies" className="ml-auto text-[10px] normal-case tracking-normal text-accent hover:underline">Details →</Link>
          </h3>
          {trend ? (
            <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
              <span className="text-text-muted">Status</span>
              <span className={cn("font-mono text-right", trend.enabled ? "text-profit" : "text-text-muted")}>
                {trend.enabled ? "ENABLED" : "DISABLED"} · {formatPct(trend.allocation, 0)}
              </span>
              <span className="text-text-muted">Positions</span>
              <span className="font-mono text-right text-text-primary">{trend.positions?.length ?? 0}</span>
              <span className="text-text-muted">Exposure</span>
              <span className="font-mono text-right text-text-primary">{formatPct(trend.exposure ?? 0, 0)}</span>
              <span className="text-text-muted">Next run</span>
              <span className="font-mono text-right text-text-primary">
                {formatLocalDateTime(trend.next_run_utc)}
                {Number.isFinite(nextRunMs) && <span className="text-text-muted"> · {formatRelative(nextRunMs, now)}</span>}
              </span>
              <span className="text-text-muted">Last run</span>
              <span className={cn("font-mono text-right", /error|fail/i.test(trend.last_run_status) ? "text-loss" : "text-text-primary")}>
                {trend.last_run_status || "never"} · {formatLocalDateTime(trend.last_run_utc)}
              </span>
              {trend.last_error && <p className="col-span-2 text-[11px] font-mono text-loss break-words">{trend.last_error}</p>}
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center h-24 text-text-muted">
              <TrendingUp className="w-6 h-6 opacity-20 mb-2" />
              <p className="text-xs">{trendError ? "Trend daily not available on this bridge" : "Waiting for data..."}</p>
            </div>
          )}
        </GlassPanel>

        <GlassPanel className="p-4 min-w-0">
          <h3 className="text-xs text-text-secondary uppercase tracking-wider mb-3 flex items-center gap-2">
            <Gauge className="w-3 h-3" /> Edge monitor
            {edge && <span className="ml-auto text-[10px] normal-case tracking-normal text-text-muted font-mono">window {edge.window} · min {edge.min_trades} · kill t≤{edge.t_stat_kill}</span>}
          </h3>
          {edgeRows.length > 0 ? (
            <div className="space-y-1.5">
              {edgeRows.map(([type, e]) => (
                <div key={type} className="flex items-center gap-2 text-xs">
                  <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ backgroundColor: STRATEGY_COLORS[type] || "#6B7280" }} />
                  <span className="text-text-secondary truncate flex-1 min-w-0">{STRATEGY_LABELS[type] || type}</span>
                  <span className="font-mono text-text-muted hidden sm:inline">n {e.n} · t {typeof e.t_stat === "number" ? e.t_stat.toFixed(2) : "---"} · PF {typeof e.profit_factor === "number" ? e.profit_factor.toFixed(2) : "---"}</span>
                  <VerdictChip verdict={e.verdict} title={e.reason} />
                </div>
              ))}
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center h-24 text-text-muted">
              <Gauge className="w-6 h-6 opacity-20 mb-2" />
              <p className="text-xs">{edge ? "No strategy has traded yet" : edgeError ? "Edge monitor not available on this bridge" : "Waiting for data..."}</p>
            </div>
          )}
        </GlassPanel>
      </motion.div>

      {/* Bottom: Allocation Donut + Positions + Signals + Micro */}
      <motion.div variants={fadeUp} className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
        {/* Strategy Allocation Donut */}
        <GlassPanel className="p-4 flex flex-col items-center min-w-0">
          <h3 className="text-xs text-text-secondary uppercase tracking-wider mb-2 self-start">Allocation</h3>
          {allocation.length === 0 ? (
            <div className="flex flex-col items-center justify-center flex-1 min-h-32 text-text-muted text-center">
              <CircleDot className="w-6 h-6 opacity-20 mb-2" />
              <p className="text-xs">No allocation — enable a strategy</p>
              <Link to="/strategies" className="text-[11px] text-accent hover:underline mt-1">Open Strategies →</Link>
            </div>
          ) : (
            <>
              <ResponsiveContainer width={120} height={120}>
                <PieChart>
                  <Pie
                    data={allocation}
                    cx="50%"
                    cy="50%"
                    innerRadius={36}
                    outerRadius={52}
                    paddingAngle={4}
                    dataKey="value"
                    strokeWidth={0}
                    isAnimationActive={false}
                  >
                    {allocation.map((entry) => (
                      <Cell key={entry.type} fill={entry.color} />
                    ))}
                  </Pie>
                </PieChart>
              </ResponsiveContainer>
              <div className="space-y-1 mt-1 w-full">
                {allocation.map((s) => (
                  <div key={s.type} className="flex items-center justify-between text-[10px]">
                    <div className="flex items-center gap-1.5 min-w-0">
                      <span className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: s.color }} />
                      <span className="text-text-secondary truncate">{s.name}</span>
                    </div>
                    <span className="font-mono" style={{ color: s.color }}>{s.value}%</span>
                  </div>
                ))}
              </div>
            </>
          )}
        </GlassPanel>

        {/* Open Positions */}
        <GlassPanel className="p-4 min-w-0">
          <h3 className="text-xs text-text-secondary uppercase tracking-wider mb-3 flex items-center gap-2">
            <CircleDot className="w-3 h-3" /> Positions
          </h3>
          {allPositions.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-32 text-text-muted">
              <CircleDot className="w-6 h-6 opacity-20 mb-2" />
              <p className="text-xs">No open positions</p>
            </div>
          ) : (
            <div className="-mx-1">
              {allPositions.map((p, i) => (
                <Link
                  to="/trading"
                  key={`${p.symbol}-${p.strategy ?? ""}-${i}`}
                  className="flex items-center gap-2 h-8 px-1 border-b border-hairline-soft last:border-b-0 hover:bg-white/[0.03] text-xs"
                  title={`${p.symbol} · ${STRATEGY_LABELS[p.strategy ?? ""] ?? p.strategy ?? ""} — open Live Trading`}
                >
                  <SideChip side={p.side} compact />
                  <span className="font-medium text-text-primary truncate">{p.symbol}</span>
                  <StrategyTag strategy={p.strategy} dotOnly />
                  <HoldTime seconds={positionHoldSec(p, now)} className="text-[10.5px] ml-auto hidden sm:inline" />
                  <PnlCell pnl={p.unrealized_pnl ?? 0} roe={positionRoe(p)} inline className="text-xs shrink-0" />
                </Link>
              ))}
            </div>
          )}
        </GlassPanel>

        {/* Recent Signals */}
        <GlassPanel className="p-4 min-w-0">
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
              {recentSignals.map((s, i) => (
                <div
                  key={`${s.timestamp}-${i}`}
                  className="flex items-center justify-between text-xs p-1.5 rounded-lg hover:bg-white/[0.02]"
                >
                  <div className="flex items-center gap-2 min-w-0">
                    <span
                      className="w-1.5 h-1.5 rounded-full shrink-0"
                      style={{ backgroundColor: STRATEGY_COLORS[s.strategy] || "#6B7280" }}
                    />
                    <span className="text-text-secondary truncate">
                      {STRATEGY_LABELS[s.strategy] || s.strategy}
                    </span>
                  </div>
                  <span className={cn("font-mono", s.side === "BUY" ? "text-profit" : "text-loss")}>
                    {s.side}
                  </span>
                </div>
              ))}
            </div>
          )}
        </GlassPanel>

        {/* Microstructure Quick View */}
        <GlassPanel className="p-4 min-w-0">
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
                          style={{ backgroundColor: data.vpin.is_toxic ? "#F43F5E" : "#E84393" }}
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
                            ? "linear-gradient(90deg, #FFA502, #F43F5E)"
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
