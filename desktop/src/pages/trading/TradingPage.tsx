import { useMemo } from "react";
import { useShallow } from "zustand/shallow";
import { ErrorBoundary } from "@/components/shared/ErrorBoundary";
import { CandlestickChart } from "@/components/charts/CandlestickChart";
import { AnimatedNumber } from "@/components/shared/AnimatedNumber";
import { useMarketStore } from "@/stores/marketStore";
import { useTradingStore } from "@/stores/tradingStore";
import { useMicroStore } from "@/stores/microStore";
import { formatUSD, formatPrice, formatBps, cn } from "@/lib/utils";
import { STRATEGY_COLORS, STRATEGY_LABELS } from "@/lib/constants";
import { ArrowUpRight, ArrowDownRight } from "lucide-react";

// Static glass panel — no framer-motion to avoid animation issues
function Panel({ className, children }: { className?: string; children: React.ReactNode }) {
  return (
    <div className={cn(
      "rounded-2xl bg-bg-surface/70 backdrop-blur-xl border border-white/5 shadow-[0_4px_24px_rgba(0,0,0,0.4)]",
      className
    )}>
      {children}
    </div>
  );
}

export function TradingPage() {
  const symbol = "BTC-USD";
  const price = useMarketStore((s) => s.prices[symbol] || 0);
  const prevPrice = useMarketStore((s) => s.prevPrices[symbol] || 0);
  const orderbook = useMarketStore((s) => s.orderbooks[symbol]);
  const positionsMap = useTradingStore(useShallow((s) => s.positions));
  const positions = useMemo(() => Object.values(positionsMap).flat(), [positionsMap]);
  const signals = useTradingStore(useShallow((s) => s.recentSignals));
  const recentSignals = useMemo(() => [...signals].reverse().slice(0, 10), [signals]);
  const micro = useMicroStore((s) => s.snapshots[symbol]);

  const priceUp = price > prevPrice;

  return (
    <div className="h-full flex flex-col gap-3">
      {/* Top Row: Chart + Order Book */}
      <div className="flex-1 flex gap-3 min-h-0">
        {/* Main Chart */}
        <Panel className="flex-1 flex flex-col overflow-hidden">
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
            <ErrorBoundary
              fallback={
                <div className="flex items-center justify-center h-full text-text-muted text-sm">
                  Chart unavailable
                </div>
              }
            >
              <CandlestickChart symbol={symbol} />
            </ErrorBoundary>
          </div>
        </Panel>

        {/* Right Panel */}
        <div className="w-72 flex flex-col gap-3">
          {/* Order Book */}
          <Panel className="flex-1 p-3 overflow-hidden">
            <h3 className="text-[10px] text-text-muted uppercase tracking-wider mb-2">Order Book</h3>
            {orderbook && (orderbook.bids?.length > 0 || orderbook.asks?.length > 0) ? (
              (() => {
                const asks = [...(orderbook.asks || [])].reverse().slice(0, 8);
                const bids = (orderbook.bids || []).slice(0, 8);
                const allQty = [...asks, ...bids].map(l => l.quantity || 0);
                const maxQty = Math.max(...allQty, 0.001); // normalize bars by max quantity
                return (
                  <div className="space-y-0.5 text-xs font-mono">
                    {asks.map((lvl, i) => (
                      <div key={`a${i}`} className="flex justify-between relative">
                        <div
                          className="absolute inset-y-0 right-0 bg-loss/10"
                          style={{ width: `${Math.min((lvl.quantity || 0) / maxQty * 100, 100)}%` }}
                        />
                        <span className="text-loss relative z-10">{formatPrice(lvl.price)}</span>
                        <span className="text-text-muted relative z-10">{(lvl.quantity || 0).toFixed(4)}</span>
                      </div>
                    ))}
                    <div className="flex justify-center py-1 text-text-muted text-[10px]">
                      {orderbook.spread_bps ? `${formatBps(orderbook.spread_bps)} spread` : "---"}
                    </div>
                    {bids.map((lvl, i) => (
                      <div key={`b${i}`} className="flex justify-between relative">
                        <div
                          className="absolute inset-y-0 right-0 bg-profit/10"
                          style={{ width: `${Math.min((lvl.quantity || 0) / maxQty * 100, 100)}%` }}
                        />
                        <span className="text-profit relative z-10">{formatPrice(lvl.price)}</span>
                        <span className="text-text-muted relative z-10">{(lvl.quantity || 0).toFixed(4)}</span>
                      </div>
                    ))}
                  </div>
                );
              })()
            ) : (
              <p className="text-text-muted text-xs">Waiting for data...</p>
            )}
          </Panel>

          {/* Microstructure */}
          <Panel className="p-3">
            <h3 className="text-[10px] text-text-muted uppercase tracking-wider mb-2">Microstructure</h3>
            <div className="space-y-1.5 text-xs">
              <div className="flex justify-between">
                <span className="text-[#E84393]">VPIN</span>
                <span className="font-mono">
                  {micro?.vpin ? `${(micro.vpin.vpin * 100).toFixed(0)}%` : "---"}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-[#FF7675]">Hawkes</span>
                <span className="font-mono text-text-secondary">
                  {micro?.hawkes ? `${micro.hawkes.multiplier.toFixed(1)}x` : "---"}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-text-secondary">Risk Score</span>
                <span className="font-mono">{micro?.risk_score?.toFixed(2) ?? "---"}</span>
              </div>
            </div>
          </Panel>
        </div>
      </div>

      {/* Bottom Row */}
      <div className="flex gap-3 h-44">
        <Panel className="flex-1 p-3 overflow-auto">
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
                    <td className={cn("text-right font-mono", (p.unrealized_pnl ?? 0) >= 0 ? "text-profit" : "text-loss")}>
                      {formatUSD(p.unrealized_pnl ?? 0)}
                    </td>
                    <td className="pl-2 text-text-muted text-[10px]">
                      {STRATEGY_LABELS[p.strategy || ""] || "---"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Panel>

        <Panel className="w-80 p-3 overflow-auto">
          <h3 className="text-[10px] text-text-muted uppercase tracking-wider mb-2">Signal Feed</h3>
          {signals.length === 0 ? (
            <p className="text-text-muted text-xs">No signals yet</p>
          ) : (
            <div className="space-y-1.5">
              {recentSignals.map((s, i) => (
                <div key={`${s.timestamp}-${i}`} className="flex items-center justify-between text-xs">
                  <div className="flex items-center gap-2">
                    <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: STRATEGY_COLORS[s.strategy] || "#4A5568" }} />
                    <span className={s.side === "BUY" ? "text-profit" : "text-loss"}>{s.side}</span>
                    <span className="text-text-muted">{s.symbol}</span>
                  </div>
                  <span className="font-mono text-text-secondary">{(s.strength * 100).toFixed(0)}%</span>
                </div>
              ))}
            </div>
          )}
        </Panel>
      </div>
    </div>
  );
}
