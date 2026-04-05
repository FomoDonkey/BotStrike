import { useEffect, useState, useMemo } from "react";
import { motion } from "framer-motion";
import { GlassPanel } from "@/components/shared/GlassPanel";
import { MetricCard } from "@/components/shared/MetricCard";
import { useTradingStore } from "@/stores/tradingStore";
import { formatUSD, formatPct, cn } from "@/lib/utils";
import { STRATEGY_COLORS, STRATEGY_LABELS } from "@/lib/constants";
import { api } from "@/lib/api";
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  BarChart, Bar, Cell,
} from "recharts";
import { TrendingUp, Target, BarChart3, DollarSign, Timer, Percent } from "lucide-react";

interface TradeRecord {
  id: number;
  symbol: string;
  side: string;
  strategy: string;
  entry_price: number;
  exit_price: number;
  quantity: number;
  pnl: number;
  fee: number;
  duration_sec: number;
  entry_time: string;
  exit_time: string;
  regime: string;
}

interface PerfData {
  equity: number;
  pnl: number;
  total_trades: number;
  win_rate: number;
  sharpe_ratio: number;
  max_drawdown: number;
  total_fees: number;
  avg_win: number;
  avg_loss: number;
  profit_factor: number;
  equity_curve: number[];
}

export function PerformancePage() {
  const metrics = useTradingStore((s) => s.metrics);
  const [trades, setTrades] = useState<TradeRecord[]>([]);
  const [perfData, setPerfData] = useState<PerfData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const [perf, tradeRes] = await Promise.all([
          api.performance().catch(() => null),
          api.trades(200).catch(() => ({ trades: [] })),
        ]);
        if (cancelled) return;
        if (perf && !perf.error) setPerfData(perf);
        setTrades(tradeRes.trades || []);
      } catch {
        // bridge not running
      }
      setLoading(false);
    }
    load();
    const interval = setInterval(load, 30000); // 30s — less aggressive than 10s
    return () => { cancelled = true; clearInterval(interval); };
  }, []);

  const equityCurveData = useMemo(() => {
    if (!perfData?.equity_curve?.length) return [];
    return perfData.equity_curve.map((v, i) => ({ idx: i, equity: typeof v === "number" ? v : 1000 }));
  }, [perfData?.equity_curve]);

  const p = perfData || metrics;

  // Strategy breakdown
  const strategyBreakdown = useMemo(() => {
    const map: Record<string, { pnl: number; trades: number; wins: number }> = {};
    for (const t of trades) {
      const key = t.strategy || "UNKNOWN";
      if (!map[key]) map[key] = { pnl: 0, trades: 0, wins: 0 };
      map[key].pnl += t.pnl || 0;
      map[key].trades++;
      if ((t.pnl || 0) > 0) map[key].wins++;
    }
    return Object.entries(map).map(([name, d]) => ({
      name: STRATEGY_LABELS[name] || name,
      color: STRATEGY_COLORS[name] || "#4A5568",
      ...d,
      wr: d.trades > 0 ? d.wins / d.trades : 0,
    }));
  }, [trades]);

  return (
    <motion.div
      className="space-y-4"
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25 }}
    >
      <h1 className="text-lg font-semibold text-text-primary">Performance Analytics</h1>

      {/* Metrics */}
      <div className="grid grid-cols-6 gap-3">
        <MetricCard label="Total PnL" value={p.pnl} format={formatUSD} colorize icon={<DollarSign className="w-3 h-3" />} />
        <MetricCard label="Win Rate" value={p.win_rate} format={formatPct} icon={<Target className="w-3 h-3" />} />
        <MetricCard label="Sharpe" value={p.sharpe_ratio} format={(v) => v.toFixed(2)} icon={<BarChart3 className="w-3 h-3" />} />
        <MetricCard label="Max DD" value={p.max_drawdown} format={formatPct} icon={<TrendingUp className="w-3 h-3" />} />
        <MetricCard label="Trades" value={p.total_trades} format={(v) => v.toFixed(0)} icon={<Timer className="w-3 h-3" />} />
        <MetricCard label="Fees" value={p.total_fees} format={formatUSD} icon={<Percent className="w-3 h-3" />} />
      </div>

      {/* Equity Curve */}
      <GlassPanel className="p-4">
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
              <XAxis dataKey="idx" hide />
              <YAxis
                domain={["dataMin - 5", "dataMax + 5"]}
                tick={{ fill: "#8898AA", fontSize: 10, fontFamily: "JetBrains Mono" }}
                axisLine={false}
                tickLine={false}
                width={60}
                tickFormatter={(v) => `$${v}`}
              />
              <Tooltip
                contentStyle={{ background: "#0B1120", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 8, fontSize: 12, fontFamily: "JetBrains Mono" }}
                labelStyle={{ color: "#8898AA" }}
                formatter={(v: any) => [`$${Number(v).toFixed(2)}`, "Equity"]}
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
      <div className="grid grid-cols-3 gap-3">
        {/* Strategy Breakdown */}
        <GlassPanel className="p-4">
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

        {/* Trade History */}
        <GlassPanel className="col-span-2 p-4 max-h-80 overflow-auto">
          <h3 className="text-xs text-text-secondary uppercase tracking-wider mb-3">Trade History</h3>
          {trades.length === 0 ? (
            <p className="text-text-muted text-sm">{loading ? "Loading..." : "No trades recorded"}</p>
          ) : (
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-bg-surface">
                <tr className="text-text-muted border-b border-white/5">
                  <th className="text-left py-1">Open</th>
                  <th className="text-left">Close</th>
                  <th className="text-left">Symbol</th>
                  <th className="text-left">Side</th>
                  <th className="text-right">Entry</th>
                  <th className="text-right">Exit</th>
                  <th className="text-right">PnL</th>
                  <th className="text-left pl-2">Strategy</th>
                  <th className="text-left">Regime</th>
                </tr>
              </thead>
              <tbody>
                {trades.slice(0, 50).map((t) => (
                  <tr key={t.id} className="border-b border-white/[0.02] hover:bg-white/[0.02]">
                    <td className="py-1 text-text-muted">{t.entry_time ? new Date(t.entry_time).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "---"}</td>
                    <td className="py-1 text-text-muted">{t.exit_time ? new Date(t.exit_time).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "---"}</td>
                    <td className="font-mono">{t.symbol}</td>
                    <td className={t.side === "BUY" ? "text-profit" : "text-loss"}>
                      {t.side}{t.pnl !== 0 ? " (close)" : ""}
                    </td>
                    <td className="text-right font-mono">${(t.entry_price || 0).toFixed(2)}</td>
                    <td className="text-right font-mono">${(t.exit_price || 0).toFixed(2)}</td>
                    <td className={cn("text-right font-mono", (t.pnl || 0) >= 0 ? "text-profit" : "text-loss")}>
                      {formatUSD(t.pnl || 0)}
                    </td>
                    <td className="pl-2">
                      <span className="text-[10px]" style={{ color: STRATEGY_COLORS[t.strategy] || "#4A5568" }}>
                        {STRATEGY_LABELS[t.strategy] || t.strategy || "---"}
                      </span>
                    </td>
                    <td className="text-text-muted text-[10px]">{t.regime || "---"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </GlassPanel>
      </div>
    </motion.div>
  );
}
