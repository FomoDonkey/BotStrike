import { useMemo, useState } from "react";
import { useShallow } from "zustand/shallow";
import { useTradingStore, type TradeData } from "@/stores/tradingStore";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { TabBar } from "@/components/shared/TabBar";
import type { Timeframe } from "@/components/charts/chartConfig";
import { cn } from "@/lib/utils";
import { MarketHeader } from "./MarketHeader";
import { ChartArea } from "./ChartArea";
import { OrderBookPanel } from "./OrderBookPanel";
import { TradesTape } from "./TradesTape";
import { PositionsTable } from "./PositionsTable";
import { OrdersTable } from "./OrdersTable";
import { TradeHistoryTable } from "./TradeHistoryTable";
import { SignalsFeed } from "./SignalsFeed";
import { AccountPanel } from "./AccountPanel";
import { useTradeHistory } from "./useTradeHistory";

type RightTab = "book" | "tape" | "account";
type BottomTab = "positions" | "orders" | "history" | "signals" | "account";

const SYMBOL_KEY = "botstrike.trading.symbol";

function loadSymbol(): string {
  try { return localStorage.getItem(SYMBOL_KEY) || "BTC-USD"; } catch { return "BTC-USD"; }
}

function Panel({ className, children }: { className?: string; children: React.ReactNode }) {
  return <div className={cn("rounded-lg border border-hairline bg-bg-surface flex flex-col min-w-0 overflow-hidden", className)}>{children}</div>;
}

/**
 * Live Trading terminal (contract §1): market header → chart + order book / tape → positions,
 * orders, history, signals, account. Stacks below lg; nothing ever scrolls the body sideways.
 */
export function TradingPage() {
  const [symbol, setSymbolState] = useState(loadSymbol);
  const setSymbol = (s: string) => { setSymbolState(s); try { localStorage.setItem(SYMBOL_KEY, s); } catch { /* ignore */ } };
  const [timeframe, setTimeframe] = useState<Timeframe>("5m");
  const [rightTab, setRightTab] = useState<RightTab>("book");
  const [bottomTab, setBottomTab] = useState<BottomTab>("positions");
  const isDesktop = useMediaQuery("(min-width: 1024px)");

  const positionsMap = useTradingStore(useShallow((s) => s.positions));
  const positions = useMemo(() => Object.values(positionsMap).flat(), [positionsMap]);
  const signals = useTradingStore(useShallow((s) => s.recentSignals));
  const liveTrades = useTradingStore(useShallow((s) => s.recentTrades));
  const history = useTradeHistory();

  // DB history + live WS fills, deduped (a live fill also lands in the DB within seconds)
  const markers = useMemo(() => {
    const seen = new Set<string>();
    const merged: TradeData[] = [];
    for (const t of [...history.markers, ...liveTrades]) {
      if (t.symbol !== symbol || !t.timestamp || !t.price) continue;
      const key = `${t.trade_type}_${Math.round(t.timestamp)}_${t.price.toFixed(4)}`;
      if (seen.has(key)) continue;
      seen.add(key);
      merged.push(t);
    }
    return merged;
  }, [history.markers, liveTrades, symbol]);

  const rightTabs = useMemo(() => {
    const base = [{ id: "book" as const, label: "Order Book" }, { id: "tape" as const, label: "Trades" }];
    return isDesktop ? base : [...base, { id: "account" as const, label: "Account" }];
  }, [isDesktop]);
  const effectiveRight: RightTab = isDesktop && rightTab === "account" ? "book" : rightTab;

  const bottomTabs = [
    { id: "positions" as const, label: "Positions", badge: positions.length || undefined },
    { id: "orders" as const, label: "Orders" },
    { id: "history" as const, label: "Trade History", badge: history.closed.length || undefined },
    { id: "signals" as const, label: "Signals", badge: signals.length || undefined },
    { id: "account" as const, label: "Account" },
  ];

  return (
    <div className="flex flex-col gap-2 lg:h-full min-w-0">
      <MarketHeader symbol={symbol} onSymbolChange={setSymbol} />

      {/* Middle: chart + right rail */}
      <div className="flex flex-col lg:flex-row gap-2 lg:flex-1 lg:min-h-0 min-w-0">
        <Panel className="flex-1 min-h-[480px] lg:min-h-0">
          <ChartArea
            symbol={symbol}
            timeframe={timeframe}
            onTimeframe={setTimeframe}
            markers={markers}
            positions={positions}
            signals={signals}
          />
        </Panel>

        <div className="w-full lg:w-[292px] xl:w-[316px] shrink-0 flex flex-col gap-2 lg:min-h-0">
          <Panel className="flex-1 min-h-[380px] lg:min-h-0">
            <TabBar tabs={rightTabs} value={effectiveRight} onChange={setRightTab} size="sm" />
            {effectiveRight === "book" && <OrderBookPanel symbol={symbol} />}
            {effectiveRight === "tape" && <TradesTape symbol={symbol} />}
            {effectiveRight === "account" && <AccountPanel positions={positions} variant="compact" />}
          </Panel>
          {isDesktop && (
            <Panel className="shrink-0">
              <div className="h-8 px-3 flex items-center text-[11.5px] font-medium text-text-primary border-b border-hairline">Account Overview</div>
              <AccountPanel positions={positions} variant="compact" />
            </Panel>
          )}
        </div>
      </div>

      {/* Bottom: positions / orders / history / signals / account */}
      <Panel className="lg:h-[min(300px,36vh)] lg:shrink-0 min-h-[260px] max-h-[70vh] lg:max-h-none">
        <TabBar tabs={bottomTabs} value={bottomTab} onChange={setBottomTab} size="sm" />
        {bottomTab === "positions" && <PositionsTable positions={positions} symbol={symbol} />}
        {bottomTab === "orders" && <OrdersTable positions={positions} symbol={symbol} />}
        {bottomTab === "history" && <TradeHistoryTable trades={history.closed} loading={history.loading} error={history.error} symbol={symbol} />}
        {bottomTab === "signals" && <SignalsFeed signals={signals} />}
        {bottomTab === "account" && <AccountPanel positions={positions} variant="full" />}
      </Panel>
    </div>
  );
}
