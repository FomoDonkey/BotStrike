import { useState } from "react";
import { motion } from "framer-motion";
import { GlassPanel } from "@/components/shared/GlassPanel";
import { formatUSD, formatPct, cn } from "@/lib/utils";
import { BRIDGE_URL, STRATEGY_LABELS } from "@/lib/constants";
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from "recharts";
import { FlaskConical, Play, Loader2, TrendingUp, TrendingDown, BarChart3, Clock } from "lucide-react";

interface BacktestResult {
  equity_curve: number[];
  total_trades: number;
  win_rate: number;
  pnl: number;
  sharpe_ratio: number;
  max_drawdown: number;
  profit_factor: number;
  avg_trade_pnl: number;
  total_fees: number;
  return_pct: number;
  by_strategy: Record<string, { trades: number; pnl: number; win_rate: number }>;
  bars_tested: number;
}

// Only show strategies that are actually active/available
const AVAILABLE_STRATEGIES = [
  { value: "MEAN_REVERSION", label: "Mean Reversion", active: true },
  { value: "ORDER_FLOW_MOMENTUM", label: "Order Flow Momentum", active: false },
  { value: "TREND_FOLLOWING", label: "Trend Following (archived)", active: false },
  { value: "MARKET_MAKING", label: "Market Making (archived)", active: false },
];

export function BacktestPage() {
  const [symbol, setSymbol] = useState("BTC-USD");
  const [strategy, setStrategy] = useState("MEAN_REVERSION");
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);

  const runBacktest = async () => {
    setRunning(true);
    setError(null);
    setResult(null);
    setElapsed(0);
    const t0 = Date.now();
    const timer = setInterval(() => setElapsed(Math.floor((Date.now() - t0) / 1000)), 1000);
    try {
      const res = await fetch(`${BRIDGE_URL}/api/backtest/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbol, strategy }),
      });
      const data = await res.json();
      if (data.error) {
        setError(data.error);
      } else {
        setResult(data);
      }
    } catch {
      setError("Bridge server not running. Start with: python -m server.bridge");
    }
    clearInterval(timer);
    setElapsed(Math.floor((Date.now() - t0) / 1000));
    setRunning(false);
  };

  const curveData = result?.equity_curve?.map((v, i) => ({ idx: i, equity: v })) || [];
  const isProfitable = (result?.pnl ?? 0) >= 0;

  return (
    <motion.div
      className="space-y-4"
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
    >
      <h1 className="text-lg font-semibold text-text-primary flex items-center gap-2">
        <FlaskConical className="w-5 h-5 text-accent" /> Backtesting Lab
      </h1>

      <div className="grid grid-cols-3 gap-4">
        {/* Config Form */}
        <GlassPanel className="p-5">
          <h3 className="text-xs text-text-secondary uppercase tracking-wider mb-4">Configuration</h3>
          <div className="space-y-4">
            <div>
              <label className="text-xs text-text-muted block mb-1">Symbol</label>
              <select
                value={symbol}
                onChange={(e) => setSymbol(e.target.value)}
                className="w-full bg-bg-base border border-white/10 rounded-lg px-3 py-2 text-sm text-text-primary font-mono focus:outline-none focus:border-accent/50"
              >
                <option value="BTC-USD">BTC-USD</option>
                <option value="ETH-USD">ETH-USD</option>
                <option value="SOL-USD">SOL-USD</option>
                <option value="ADA-USD">ADA-USD</option>
              </select>
            </div>
            <div>
              <label className="text-xs text-text-muted block mb-1">Strategy</label>
              <select
                value={strategy}
                onChange={(e) => setStrategy(e.target.value)}
                className="w-full bg-bg-base border border-white/10 rounded-lg px-3 py-2 text-sm text-text-primary font-mono focus:outline-none focus:border-accent/50"
              >
                {AVAILABLE_STRATEGIES.map((s) => (
                  <option key={s.value} value={s.value} disabled={!s.active && s.value !== "MEAN_REVERSION"}>
                    {s.label}
                  </option>
                ))}
              </select>
            </div>
            <button
              onClick={runBacktest}
              disabled={running}
              className={cn(
                "w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg text-sm font-semibold transition-all",
                running
                  ? "bg-accent/20 text-accent/50 cursor-wait"
                  : "bg-accent text-bg-base hover:bg-accent/90"
              )}
            >
              {running ? (
                <><Loader2 className="w-4 h-4 animate-spin" /> Running... {elapsed}s</>
              ) : (
                <><Play className="w-4 h-4" /> Run Backtest</>
              )}
            </button>
          </div>

          {error && (
            <div className="mt-4 p-3 rounded-lg bg-loss/10 text-loss text-xs font-mono">
              {error}
            </div>
          )}

          {/* Summary info after run */}
          {result && (
            <div className="mt-4 pt-4 border-t border-white/5 space-y-2">
              <div className="flex items-center gap-1.5 text-xs text-text-muted">
                <Clock className="w-3 h-3" />
                <span>{result.bars_tested?.toLocaleString() ?? "?"} bars tested in {elapsed}s</span>
              </div>
              <div className="flex items-center gap-1.5 text-xs text-text-muted">
                <BarChart3 className="w-3 h-3" />
                <span>Strategy: {STRATEGY_LABELS[strategy] ?? strategy}</span>
              </div>
            </div>
          )}
        </GlassPanel>

        {/* Results */}
        <GlassPanel className="col-span-2 p-5">
          <h3 className="text-xs text-text-secondary uppercase tracking-wider mb-4">Results</h3>

          {!result && !running && (
            <div className="flex items-center justify-center h-64 text-text-muted text-sm">
              Configure and run a backtest to see results
            </div>
          )}

          {running && (
            <div className="flex items-center justify-center h-64">
              <div className="text-center">
                <Loader2 className="w-8 h-8 text-accent animate-spin mx-auto mb-3" />
                <p className="text-text-secondary text-sm">Running backtest... {elapsed}s</p>
              </div>
            </div>
          )}

          {result && (
            <div className="space-y-4">
              {/* Primary Metrics Row */}
              <div className="grid grid-cols-6 gap-3">
                {[
                  {
                    label: "Net PnL",
                    value: formatUSD(result.pnl),
                    color: isProfitable ? "text-profit" : "text-loss",
                    icon: isProfitable ? TrendingUp : TrendingDown,
                  },
                  { label: "Return", value: `${result.return_pct?.toFixed(2) ?? "0"}%`, color: isProfitable ? "text-profit" : "text-loss" },
                  { label: "Trades", value: result.total_trades.toString(), color: "text-text-primary" },
                  { label: "Win Rate", value: formatPct(result.win_rate), color: result.win_rate >= 0.5 ? "text-profit" : "text-loss" },
                  { label: "Sharpe", value: result.sharpe_ratio?.toFixed(2) ?? "0", color: result.sharpe_ratio > 0 ? "text-accent" : "text-loss" },
                  { label: "Max DD", value: formatPct(result.max_drawdown), color: "text-loss" },
                ].map((m) => (
                  <div key={m.label} className="text-center p-2 rounded-lg bg-white/[0.02] border border-white/5">
                    <p className="text-[10px] text-text-muted uppercase">{m.label}</p>
                    <p className={cn("font-mono font-semibold text-sm", m.color)}>{m.value}</p>
                  </div>
                ))}
              </div>

              {/* Secondary Metrics Row */}
              <div className="grid grid-cols-4 gap-3">
                {[
                  { label: "Profit Factor", value: result.profit_factor?.toFixed(2) ?? "0" },
                  { label: "Avg Trade", value: formatUSD(result.avg_trade_pnl ?? 0) },
                  { label: "Total Fees", value: formatUSD(result.total_fees ?? 0) },
                  { label: "Expectancy", value: result.total_trades > 0 ? formatUSD(result.pnl / result.total_trades) : "$0.00" },
                ].map((m) => (
                  <div key={m.label} className="text-center p-1.5 rounded bg-white/[0.01]">
                    <p className="text-[9px] text-text-muted uppercase">{m.label}</p>
                    <p className="font-mono text-xs text-text-secondary">{m.value}</p>
                  </div>
                ))}
              </div>

              {/* Equity Curve */}
              {curveData.length > 0 && (
                <ResponsiveContainer width="100%" height={220}>
                  <AreaChart data={curveData}>
                    <defs>
                      <linearGradient id="btGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor={isProfitable ? "#00D4AA" : "#FF4757"} stopOpacity={0.3} />
                        <stop offset="95%" stopColor={isProfitable ? "#00D4AA" : "#FF4757"} stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.03)" />
                    <XAxis dataKey="idx" hide />
                    <YAxis
                      tick={{ fill: "#8898AA", fontSize: 10, fontFamily: "JetBrains Mono" }}
                      axisLine={false}
                      tickLine={false}
                      width={55}
                      tickFormatter={(v) => `$${v}`}
                      domain={["dataMin - 5", "dataMax + 5"]}
                    />
                    <Tooltip
                      contentStyle={{ background: "#0B1120", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 8, fontSize: 12 }}
                      formatter={(v: any) => [`$${Number(v).toFixed(2)}`, "Equity"]}
                    />
                    <Area
                      type="monotone"
                      dataKey="equity"
                      stroke={isProfitable ? "#00D4AA" : "#FF4757"}
                      fill="url(#btGrad)"
                      strokeWidth={2}
                      dot={false}
                    />
                  </AreaChart>
                </ResponsiveContainer>
              )}

              {/* No trades warning */}
              {result.total_trades === 0 && (
                <div className="p-3 rounded-lg bg-yellow-500/10 text-yellow-400 text-xs">
                  No trades generated. The MR strategy requires specific conditions
                  (1H trend + 5m RSI pullback). Try with more data or a different symbol.
                </div>
              )}
            </div>
          )}
        </GlassPanel>
      </div>
    </motion.div>
  );
}
