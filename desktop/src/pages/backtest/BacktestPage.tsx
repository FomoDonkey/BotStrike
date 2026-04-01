import { useState } from "react";
import { motion } from "framer-motion";
import { GlassPanel } from "@/components/shared/GlassPanel";
import { formatUSD, formatPct, cn } from "@/lib/utils";
import { api } from "@/lib/api";
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from "recharts";
import { FlaskConical, Play, Loader2 } from "lucide-react";

interface BacktestResult {
  equity_curve: number[];
  total_trades: number;
  win_rate: number;
  pnl: number;
  sharpe_ratio: number;
  max_drawdown: number;
}

export function BacktestPage() {
  const [symbol, setSymbol] = useState("BTC-USD");
  const [strategy, setStrategy] = useState("MEAN_REVERSION");
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const runBacktest = async () => {
    setRunning(true);
    setError(null);
    setResult(null);
    try {
      const res = await fetch("http://127.0.0.1:9420/api/backtest/run", {
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
      setError("Bridge server not running. Start it with: python -m server.bridge");
    }
    setRunning(false);
  };

  const curveData = result?.equity_curve?.map((v, i) => ({ idx: i, equity: v })) || [];

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
                <option value="MEAN_REVERSION">Mean Reversion</option>
                <option value="ORDER_FLOW_MOMENTUM">Order Flow Momentum</option>
                <option value="TREND_FOLLOWING">Trend Following</option>
                <option value="MARKET_MAKING">Market Making</option>
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
                <><Loader2 className="w-4 h-4 animate-spin" /> Running...</>
              ) : (
                <><Play className="w-4 h-4" /> Run Backtest</>
              )}
            </button>
          </div>

          {error && (
            <div className="mt-4 p-3 rounded-lg bg-loss/10 text-loss text-xs">
              {error}
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
                <p className="text-text-secondary text-sm">Running backtest...</p>
              </div>
            </div>
          )}

          {result && (
            <div className="space-y-4">
              {/* Metrics Row */}
              <div className="grid grid-cols-5 gap-3">
                {[
                  { label: "PnL", value: formatUSD(result.pnl), color: result.pnl >= 0 ? "text-profit" : "text-loss" },
                  { label: "Trades", value: result.total_trades.toString(), color: "text-text-primary" },
                  { label: "Win Rate", value: formatPct(result.win_rate), color: "text-text-primary" },
                  { label: "Sharpe", value: result.sharpe_ratio.toFixed(2), color: "text-text-primary" },
                  { label: "Max DD", value: formatPct(result.max_drawdown), color: "text-loss" },
                ].map((m) => (
                  <div key={m.label} className="text-center p-2 rounded-lg bg-white/[0.02]">
                    <p className="text-[10px] text-text-muted uppercase">{m.label}</p>
                    <p className={cn("font-mono font-semibold", m.color)}>{m.value}</p>
                  </div>
                ))}
              </div>

              {/* Equity Curve */}
              {curveData.length > 0 && (
                <ResponsiveContainer width="100%" height={200}>
                  <AreaChart data={curveData}>
                    <defs>
                      <linearGradient id="btGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#00D4AA" stopOpacity={0.3} />
                        <stop offset="95%" stopColor="#00D4AA" stopOpacity={0} />
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
                    />
                    <Tooltip
                      contentStyle={{ background: "#0B1120", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 8, fontSize: 12 }}
                      formatter={(v: any) => [`$${Number(v).toFixed(2)}`, "Equity"]}
                    />
                    <Area type="monotone" dataKey="equity" stroke="#00D4AA" fill="url(#btGrad)" strokeWidth={2} dot={false} />
                  </AreaChart>
                </ResponsiveContainer>
              )}
            </div>
          )}
        </GlassPanel>
      </div>
    </motion.div>
  );
}
