import { useState, useMemo } from "react";
import { motion } from "framer-motion";
import { GlassPanel } from "@/components/shared/GlassPanel";
import { MetricCard } from "@/components/shared/MetricCard";
import { useTradingStore } from "@/stores/tradingStore";
import { usePolling } from "@/hooks/usePolling";
import { formatUSD, formatPct, cn } from "@/lib/utils";
import { STRATEGY_COLORS, STRATEGY_LABELS } from "@/lib/constants";
import { api, type PerformanceResponse, type TradeRecord } from "@/lib/api";
import { TradeHistoryTable } from "@/pages/trading/TradeHistoryTable";
import { isClosedTrade } from "@/pages/trading/useTradeHistory";
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from "recharts";
import { TrendingUp, Target, BarChart3, DollarSign, Timer, Percent } from "lucide-react";

const POLL_MS = 30_000;
const TRADE_FETCH_LIMIT = 400; // ENTRY + EXIT rows → ~200 closed trades
const HISTORY_ROWS = 100;

function Chip({ label, value, tone }: { label: string; value: string; tone?: "profit" | "loss" | "muted" }) {
  return (
    <span className="whitespace-nowrap">
      {label}{" "}
      <span className={cn(
        tone === "profit" && "text-profit",
        tone === "loss" && "text-loss",
        (!tone || tone === "muted") && "text-text-secondary",
      )}>
        {value}
      </span>
    </span>
  );
}

export function PerformancePage() {
  const metrics = useTradingStore((s) => s.metrics);
  const [trades, setTrades] = useState<TradeRecord[]>([]);
  const [perfData, setPerfData] = useState<PerformanceResponse | null>(null);
  const [loading, setLoading] = useState(true);

  usePolling(async () => {
    const [perf, tradeRes] = await Promise.all([
      api.performance().catch(() => null),
      api.trades(TRADE_FETCH_LIMIT).catch(() => ({ trades: [] })),
    ]);
    if (perf) setPerfData(perf);
    setTrades(tradeRes.trades || []);
    setLoading(false);
  }, POLL_MS);

  const equityCurve = perfData?.equity_curve;
  const equityCurveTs = perfData?.equity_curve_ts;
  const hasTimeAxis = !!equityCurveTs?.length;
  const equityCurveData = useMemo(() => {
    if (equityCurveTs?.length) {
      return equityCurveTs.map(([t, v]) => ({ idx: t * 1000, equity: v }));
    }
    if (!equityCurve?.length) return [];
    return equityCurve.map((v, i) => ({ idx: i, equity: typeof v === "number" ? v : 1000 }));
  }, [equityCurve, equityCurveTs]);

  // Evenly spaced time ticks — recharts derives ticks from data points, which
  // overlap when trades cluster in time.
  const xTicks = useMemo(() => {
    if (!hasTimeAxis || equityCurveData.length < 2) return undefined;
    const t0 = equityCurveData[0].idx;
    const t1 = equityCurveData[equityCurveData.length - 1].idx;
    const n = 5;
    return Array.from({ length: n + 1 }, (_, i) => t0 + ((t1 - t0) * i) / n);
  }, [hasTimeAxis, equityCurveData]);

  const p = perfData || metrics;
  const sharpeValid = perfData ? perfData.sharpe_valid !== false : true;

  const closedTrades = useMemo(() => trades.filter(isClosedTrade), [trades]);

  const strategyBreakdown = useMemo(() => {
    const map: Record<string, { pnl: number; trades: number; wins: number }> = {};
    for (const t of closedTrades) {
      const key = t.strategy || "UNKNOWN";
      if (!map[key]) map[key] = { pnl: 0, trades: 0, wins: 0 };
      map[key].pnl += t.pnl || 0;
      map[key].trades++;
      if ((t.pnl || 0) > 0) map[key].wins++;
    }
    return Object.entries(map).map(([name, d]) => ({
      name: STRATEGY_LABELS[name] || name,
      color: STRATEGY_COLORS[name] || "#6B7280",
      ...d,
      wr: d.trades > 0 ? d.wins / d.trades : 0,
    }));
  }, [closedTrades]);

  const totalTrades = perfData?.total_trades ?? metrics.total_trades;

  return (
    <motion.div
      className="space-y-4"
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25 }}
    >
      <div className="flex flex-col sm:flex-row sm:items-baseline sm:justify-between gap-2">
        <h1 className="text-lg font-semibold text-text-primary">Performance Analytics</h1>
        {perfData?.realized_pnl !== undefined && (
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] font-mono text-text-muted">
            <Chip label="Capital" value={formatUSD(perfData.initial_capital ?? 0)} />
            <Chip label="Realized" value={formatUSD(perfData.realized_pnl)} tone={perfData.realized_pnl >= 0 ? "profit" : "loss"} />
            <Chip label="Unrealized" value={formatUSD(perfData.unrealized_pnl ?? 0)} tone={(perfData.unrealized_pnl ?? 0) >= 0 ? "profit" : "loss"} />
            <Chip label="Session" value={formatUSD(perfData.session_pnl ?? 0)} tone={(perfData.session_pnl ?? 0) >= 0 ? "profit" : "loss"} />
            {perfData.peak_equity !== undefined && <Chip label="Peak" value={formatUSD(perfData.peak_equity)} />}
            {perfData.current_drawdown !== undefined && (
              <Chip label="Current DD" value={formatPct(perfData.current_drawdown)} tone={perfData.current_drawdown > 0 ? "loss" : "muted"} />
            )}
          </div>
        )}
      </div>

      {/* Metrics */}
      <div className="grid grid-cols-2 sm:grid-cols-3 xl:grid-cols-6 gap-3">
        <MetricCard label="Total PnL" value={p.pnl} format={formatUSD} colorize icon={<DollarSign className="w-3 h-3" />} />
        <MetricCard label="Win Rate" value={p.win_rate} format={formatPct} icon={<Target className="w-3 h-3" />} />
        <MetricCard
          label="Sharpe"
          value={p.sharpe_ratio}
          format={(v) => v.toFixed(2)}
          display={sharpeValid ? undefined : "n/a"}
          subtext={sharpeValid ? (perfData?.sample_days ? `${perfData.sample_days} days` : undefined) : "needs 30 days · 30 trades"}
          icon={<BarChart3 className="w-3 h-3" />}
        />
        <MetricCard label="Max DD" value={p.max_drawdown} format={formatPct} icon={<TrendingUp className="w-3 h-3" />} />
        <MetricCard label="Trades" value={p.total_trades} format={(v) => v.toFixed(0)} icon={<Timer className="w-3 h-3" />} />
        <MetricCard label="Fees" value={p.total_fees} format={formatUSD} icon={<Percent className="w-3 h-3" />} />
      </div>

      {/* Equity Curve */}
      <GlassPanel className="p-4 min-w-0">
        <h3 className="text-xs text-text-secondary uppercase tracking-wider mb-3">Equity Curve</h3>
        {equityCurveData.length > 0 ? (
          <ResponsiveContainer width="100%" height={240}>
            <AreaChart data={equityCurveData}>
              <defs>
                <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#00D4AA" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#00D4AA" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.03)" />
              <XAxis
                dataKey="idx"
                hide={!hasTimeAxis}
                type="number"
                domain={["dataMin", "dataMax"]}
                scale="time"
                ticks={xTicks}
                minTickGap={80}
                tick={{ fill: "rgba(255,255,255,0.6)", fontSize: 10, fontFamily: "JetBrains Mono" }}
                axisLine={false}
                tickLine={false}
                tickFormatter={(v) => new Date(v).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
              />
              <YAxis
                domain={["dataMin - 5", "dataMax + 5"]}
                tick={{ fill: "rgba(255,255,255,0.6)", fontSize: 10, fontFamily: "JetBrains Mono" }}
                axisLine={false}
                tickLine={false}
                width={60}
                tickFormatter={(v) => `$${Number(v).toFixed(2)}`}
              />
              <Tooltip
                contentStyle={{ background: "#171717", border: "1px solid rgba(255,255,255,0.12)", borderRadius: 6, fontSize: 12, fontFamily: "JetBrains Mono" }}
                labelStyle={{ color: "rgba(255,255,255,0.6)" }}
                labelFormatter={(v) => hasTimeAxis
                  ? new Date(Number(v)).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
                  : String(v)}
                formatter={(v: unknown) => [`$${Number(v).toFixed(2)}`, "Equity"]}
              />
              <Area type="monotone" dataKey="equity" stroke="#00D4AA" fill="url(#eqGrad)" strokeWidth={2} dot={false} />
            </AreaChart>
          </ResponsiveContainer>
        ) : (
          <div className="flex items-center justify-center h-60 text-text-muted text-sm">
            {loading ? "Loading equity data..." : "No equity data — start the bridge server"}
          </div>
        )}
      </GlassPanel>

      {/* Strategy Breakdown + Trade History */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
        {/* Strategy Breakdown */}
        <GlassPanel className="p-4 min-w-0">
          <h3 className="text-xs text-text-secondary uppercase tracking-wider mb-3">By Strategy</h3>
          {strategyBreakdown.length > 0 ? (
            <div className="space-y-3">
              {strategyBreakdown.map((s) => (
                <div key={s.name} className="space-y-1">
                  <div className="flex justify-between text-xs">
                    <span style={{ color: s.color }}>{s.name}</span>
                    <span className={cn("font-mono", s.pnl >= 0 ? "text-profit" : "text-loss")}>
                      {formatUSD(s.pnl)}
                    </span>
                  </div>
                  <div className="flex justify-between text-[10px] text-text-muted">
                    <span>{s.trades} trades</span>
                    <span>WR {formatPct(s.wr)}</span>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-text-muted text-xs">No trades yet</p>
          )}
        </GlassPanel>

        {/* Trade History — closed trades only, one row per round-trip (shared terminal rows) */}
        <GlassPanel className="lg:col-span-2 p-0 min-w-0 flex flex-col max-h-[480px]">
          <div className="flex items-baseline justify-between px-4 h-10 shrink-0 border-b border-hairline gap-2">
            <h3 className="text-xs text-text-secondary uppercase tracking-wider">Trade History</h3>
            <span className="text-[11px] font-mono text-text-muted">
              {totalTrades} closed{closedTrades.length > HISTORY_ROWS ? ` · showing ${HISTORY_ROWS}` : ""}
            </span>
          </div>
          <TradeHistoryTable trades={closedTrades} loading={loading} limit={HISTORY_ROWS} />
        </GlassPanel>
      </div>
    </motion.div>
  );
}
