import { motion } from "framer-motion";
import { GlassPanel } from "@/components/shared/GlassPanel";
import { MetricCard } from "@/components/shared/MetricCard";
import { useTradingStore } from "@/stores/tradingStore";
import { formatUSD, formatPct } from "@/lib/utils";
import { TrendingUp, Target, BarChart3, DollarSign, Timer, Percent } from "lucide-react";

export function PerformancePage() {
  const metrics = useTradingStore((s) => s.metrics);
  const trades = useTradingStore((s) => s.recentTrades);

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
        <MetricCard label="Total PnL" value={metrics.pnl} format={formatUSD} colorize icon={<DollarSign className="w-3 h-3" />} />
        <MetricCard label="Win Rate" value={metrics.win_rate} format={formatPct} icon={<Target className="w-3 h-3" />} />
        <MetricCard label="Sharpe" value={metrics.sharpe_ratio} format={(v) => v.toFixed(2)} icon={<BarChart3 className="w-3 h-3" />} />
        <MetricCard label="Max DD" value={metrics.max_drawdown} format={formatPct} icon={<TrendingUp className="w-3 h-3" />} />
        <MetricCard label="Trades" value={metrics.total_trades} format={(v) => v.toFixed(0)} icon={<Timer className="w-3 h-3" />} />
        <MetricCard label="Fees" value={metrics.total_fees} format={formatUSD} icon={<Percent className="w-3 h-3" />} />
      </div>

      {/* Equity Curve placeholder */}
      <GlassPanel className="p-4 h-64">
        <h3 className="text-xs text-text-secondary uppercase tracking-wider mb-3">Equity Curve</h3>
        <div className="flex items-center justify-center h-full text-text-muted text-sm">
          Equity chart will render with live data from bridge
        </div>
      </GlassPanel>

      {/* Trade History */}
      <GlassPanel className="p-4">
        <h3 className="text-xs text-text-secondary uppercase tracking-wider mb-3">Recent Trades</h3>
        {trades.length === 0 ? (
          <p className="text-text-muted text-sm">No trades yet</p>
        ) : (
          <table className="w-full text-xs">
            <thead>
              <tr className="text-text-muted border-b border-white/5">
                <th className="text-left py-1">Symbol</th>
                <th className="text-left">Side</th>
                <th className="text-right">Price</th>
                <th className="text-right">Qty</th>
                <th className="text-right">PnL</th>
                <th className="text-left pl-2">Strategy</th>
              </tr>
            </thead>
            <tbody>
              {[...trades].reverse().slice(0, 20).map((t, i) => (
                <tr key={i} className="border-b border-white/[0.02]">
                  <td className="py-1 font-mono">{t.symbol}</td>
                  <td className={t.side === "BUY" ? "text-profit" : "text-loss"}>{t.side}</td>
                  <td className="text-right font-mono">${t.price.toFixed(2)}</td>
                  <td className="text-right font-mono">{t.quantity.toFixed(4)}</td>
                  <td className={`text-right font-mono ${t.pnl >= 0 ? "text-profit" : "text-loss"}`}>
                    {formatUSD(t.pnl)}
                  </td>
                  <td className="pl-2 text-text-muted">{t.strategy || "---"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </GlassPanel>
    </motion.div>
  );
}
