import { motion } from "framer-motion";
import { GlassPanel } from "@/components/shared/GlassPanel";
import { AnimatedNumber } from "@/components/shared/AnimatedNumber";
import { CandlestickChart } from "@/components/charts/CandlestickChart";
import { useMarketStore } from "@/stores/marketStore";
import { useTradingStore } from "@/stores/tradingStore";
import { useMicroStore } from "@/stores/microStore";
import { formatUSD, formatPrice, formatBps, cn } from "@/lib/utils";
import { STRATEGY_COLORS, STRATEGY_LABELS } from "@/lib/constants";
import { ArrowUpRight, ArrowDownRight } from "lucide-react";

export function TradingPage() {
  const symbol = "BTCUSDT";
  const price = useMarketStore((s) => s.prices[symbol] || s.prices["BTC-USD"] || 0);
  const prevPrice = useMarketStore((s) => s.prevPrices[symbol] || s.prevPrices["BTC-USD"] || 0);
  const orderbook = useMarketStore((s) => s.orderbooks[symbol] || s.orderbooks["BTC-USD"]);
  const ticks = useMarketStore((s) => s.recentTicks[symbol] || s.recentTicks["BTC-USD"] || []);
  const positions = useTradingStore((s) => Object.values(s.positions).flat());
  const signals = useTradingStore((s) => s.recentSignals);
  const micro = useMicroStore((s) => s.snapshots[symbol] || s.snapshots["BTC-USD"]);

  const priceUp = price > prevPrice;

  return (
    <motion.div
      className="h-full flex flex-col gap-3"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.2 }}
    >
      {/* Top Row: Chart + Order Book */}
      <div className="flex-1 flex gap-3 min-h-0">
        {/* Main Chart */}
        <GlassPanel className="flex-1 flex flex-col overflow-hidden">
          <div className="flex items-center justify-between px-4 py-2 border-b border-white/5">
            <div className="flex items-center gap-3">
              <span className="font-mono font-bold text-sm text-text-primary">BTC/USD</span>
              <span className="text-xs text-text-muted">1m</span>
            </div>
            <div className="flex items-center gap-2">
              <span className={cn(
                "font-mono text-lg font-bold tabular-nums transition-colors duration-150",
                priceUp ? "text-profit" : "text-loss"
              )}>
                {price > 0 ? `$${formatPrice(price)}` : "---"}
              </span>
              {priceUp ? (
                <ArrowUpRight className="w-4 h-4 text-profit" />
              ) : (
                <ArrowDownRight className="w-4 h-4 text-loss" />
              )}
            </div>
          </div>
          <div className="flex-1 min-h-0">
            <CandlestickChart symbol={symbol} />
          </div>
        </GlassPanel>

        {/* Right Panel: Order Book + Recent Trades */}
        <div className="w-72 flex flex-col gap-3">
          {/* Order Book */}
          <GlassPanel className="flex-1 p-3 overflow-hidden">
            <h3 className="text-[10px] text-text-muted uppercase tracking-wider mb-2">Order Book</h3>
            {orderbook ? (
              <div className="space-y-0.5 text-xs font-mono">
                {/* Asks (reversed) */}
                {[...(orderbook.asks || [])].reverse().slice(0, 8).map((lvl, i) => (
                  <div key={`a${i}`} className="flex justify-between relative">
                    <div
                      className="absolute inset-y-0 right-0 bg-loss/5"
                      style={{ width: `${Math.min(lvl.quantity * 10, 100)}%` }}
                    />
                    <span className="text-loss relative z-10">{formatPrice(lvl.price)}</span>
                    <span className="text-text-muted relative z-10">{lvl.quantity.toFixed(4)}</span>
                  </div>
                ))}
                {/* Spread */}
                <div className="flex justify-center py-1 text-text-muted text-[10px]">
                  {orderbook.spread_bps ? `${formatBps(orderbook.spread_bps)} spread` : "---"}
                </div>
                {/* Bids */}
                {(orderbook.bids || []).slice(0, 8).map((lvl, i) => (
                  <div key={`b${i}`} className="flex justify-between relative">
                    <div
                      className="absolute inset-y-0 right-0 bg-profit/5"
                      style={{ width: `${Math.min(lvl.quantity * 10, 100)}%` }}
                    />
                    <span className="text-profit relative z-10">{formatPrice(lvl.price)}</span>
                    <span className="text-text-muted relative z-10">{lvl.quantity.toFixed(4)}</span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-text-muted text-xs">Waiting for data...</p>
            )}
          </GlassPanel>

          {/* Microstructure Mini */}
          <GlassPanel className="p-3">
            <h3 className="text-[10px] text-text-muted uppercase tracking-wider mb-2">Microstructure</h3>
            <div className="space-y-1.5 text-xs">
              <div className="flex justify-between">
                <span className="text-[#E84393]">VPIN</span>
                <span className="font-mono">
                  {micro?.vpin ? `${(micro.vpin.vpin * 100).toFixed(0)}%` : "---"}
                  {micro?.vpin?.is_toxic && <span className="text-loss ml-1">TOXIC</span>}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-[#FF7675]">Hawkes</span>
                <span className={cn("font-mono", micro?.hawkes?.is_spike ? "text-loss" : "text-text-secondary")}>
                  {micro?.hawkes ? `${micro.hawkes.multiplier.toFixed(1)}x` : "---"}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-text-secondary">Risk Score</span>
                <span className="font-mono">{micro?.risk_score?.toFixed(2) ?? "---"}</span>
              </div>
            </div>
          </GlassPanel>
        </div>
      </div>

      {/* Bottom Row: Positions + Signal Feed */}
      <div className="flex gap-3 h-44">
        {/* Positions */}
        <GlassPanel className="flex-1 p-3 overflow-auto">
          <h3 className="text-[10px] text-text-muted uppercase tracking-wider mb-2">Open Positions</h3>
          {positions.length === 0 ? (
            <p className="text-text-muted text-xs">No open positions</p>
          ) : (
            <table className="w-full text-xs">
              <thead>
                <tr className="text-text-muted border-b border-white/5">
                  <th className="text-left py-1">Symbol</th>
                  <th className="text-left">Side</th>
                  <th className="text-right">Entry</th>
                  <th className="text-right">Size</th>
                  <th className="text-right">uPnL</th>
                  <th className="text-left pl-2">Strategy</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((p, i) => (
                  <tr key={i} className="border-b border-white/[0.02]">
                    <td className="py-1 font-mono">{p.symbol}</td>
                    <td className={p.side === "BUY" ? "text-profit" : "text-loss"}>{p.side}</td>
                    <td className="text-right font-mono">{formatPrice(p.entry_price)}</td>
                    <td className="text-right font-mono">{p.size.toFixed(4)}</td>
                    <td className={cn("text-right font-mono", p.unrealized_pnl >= 0 ? "text-profit" : "text-loss")}>
                      {formatUSD(p.unrealized_pnl)}
                    </td>
                    <td className="pl-2">
                      <span
                        className="px-1.5 py-0.5 rounded text-[10px]"
                        style={{
                          backgroundColor: `${STRATEGY_COLORS[p.strategy || ""] || "#4A5568"}15`,
                          color: STRATEGY_COLORS[p.strategy || ""] || "#4A5568",
                        }}
                      >
                        {STRATEGY_LABELS[p.strategy || ""] || p.strategy || "---"}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </GlassPanel>

        {/* Signal Feed */}
        <GlassPanel className="w-80 p-3 overflow-auto">
          <h3 className="text-[10px] text-text-muted uppercase tracking-wider mb-2">Signal Feed</h3>
          {signals.length === 0 ? (
            <p className="text-text-muted text-xs">No signals yet</p>
          ) : (
            <div className="space-y-1.5">
              {[...signals].reverse().slice(0, 10).map((s, i) => (
                <motion.div
                  key={`${s.timestamp}-${i}`}
                  className="flex items-center justify-between text-xs"
                  initial={{ opacity: 0, x: 20 }}
                  animate={{ opacity: 1, x: 0 }}
                >
                  <div className="flex items-center gap-2">
                    <span
                      className="w-1.5 h-1.5 rounded-full"
                      style={{ backgroundColor: STRATEGY_COLORS[s.strategy] || "#4A5568" }}
                    />
                    <span className={s.side === "BUY" ? "text-profit" : "text-loss"}>
                      {s.side}
                    </span>
                    <span className="text-text-muted">{s.symbol}</span>
                  </div>
                  <span className="font-mono text-text-secondary">
                    {(s.strength * 100).toFixed(0)}%
                  </span>
                </motion.div>
              ))}
            </div>
          )}
        </GlassPanel>
      </div>
    </motion.div>
  );
}
