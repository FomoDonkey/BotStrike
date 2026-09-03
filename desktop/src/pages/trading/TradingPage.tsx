import { useMemo, useState } from "react";
import { useShallow } from "zustand/shallow";
import { useTradingStore, type TradeData } from "@/stores/tradingStore";
import { useUiStore } from "@/stores/uiStore";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { useMarketInfo } from "@/hooks/useMarketInfo";
import { TabBar } from "@/components/ui/TabBar";
import { Panel } from "@/components/ui/Panel";
import type { Timeframe } from "@/components/charts/chartConfig";
import { cn } from "@/lib/utils";
import { FavoritesStrip } from "./FavoritesStrip";
import { MarketHeader } from "./MarketHeader";
import { ChartArea } from "./ChartArea";
import { OrderBookColumn } from "./OrderBookColumn";
import { TradesTape } from "./TradesTape";
import { BotColumn } from "./BotColumn";
import { AccountPanel } from "./AccountPanel";
import { BottomPanel } from "./BottomPanel";
import { useTradeHistory } from "./useTradeHistory";

type MobileTab = "book" | "trades" | "bot" | "account";

const SYMBOL_KEY = "botstrike.trading.symbol";
const TF_KEY = "botstrike.trading.timeframe";

function loadSymbol(): string {
  try { return localStorage.getItem(SYMBOL_KEY) || "BTC-USD"; } catch { return "BTC-USD"; }
}
function loadTimeframe(): Timeframe {
  try { return (localStorage.getItem(TF_KEY) as Timeframe) || "5m"; } catch { return "5m"; }
}

/**
 * Trade page (spec §3.1): favorites strip · market header · [chart 1fr | order book 290 | bot 300]
 * · bottom panel. Two columns between lg and xl, everything stacks below lg (spec §6).
 */
export function TradingPage() {
  const [symbol, setSymbolState] = useState(loadSymbol);
  const setSymbol = (s: string) => { setSymbolState(s); try { localStorage.setItem(SYMBOL_KEY, s); } catch { /* ignore */ } };
  const [timeframe, setTimeframeState] = useState<Timeframe>(loadTimeframe);
  const setTimeframe = (tf: Timeframe) => { setTimeframeState(tf); try { localStorage.setItem(TF_KEY, tf); } catch { /* ignore */ } };
  const [mobileTab, setMobileTab] = useState<MobileTab>("book");
  const isLg = useMediaQuery("(min-width: 1024px)");
  const isXl = useMediaQuery("(min-width: 1280px)");
  const layout = useUiStore(useShallow((s) => s.layout));

  const market = useMarketInfo(symbol);
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

  const showBook = layout.orderBook;
  const chart = layout.chart && (
    <Panel className="flex flex-col min-h-[440px] lg:min-h-0 overflow-hidden">
      <ChartArea market={market} timeframe={timeframe} onTimeframe={setTimeframe} markers={markers} positions={positions} signals={signals} />
    </Panel>
  );
  const bottom = layout.tables && (
    <Panel className="flex flex-col overflow-hidden min-h-[280px] max-h-[70vh] lg:max-h-none lg:h-[300px] lg:shrink-0">
      <BottomPanel symbol={symbol} positions={positions} trades={history.trades} closed={history.closed} signals={signals} loading={history.loading} error={history.error} activityEnabled={layout.activityFeed} />
    </Panel>
  );

  return (
    <div className="flex flex-col gap-2 p-2 lg:h-full min-w-0">
      {layout.favorites && <FavoritesStrip symbol={symbol} onSelect={setSymbol} />}
      <MarketHeader market={market} onSymbolChange={setSymbol} />

      {isXl ? (
        <div className={cn("grid gap-2 flex-1 min-h-0 min-w-0", showBook ? "grid-cols-[minmax(0,1fr)_290px_300px]" : "grid-cols-[minmax(0,1fr)_300px]")}>
          {chart || <Panel className="flex items-center justify-center text-[13px] font-medium text-text">Chart hidden (Settings → Layout)</Panel>}
          {showBook && <Panel className="overflow-hidden flex flex-col min-h-0"><OrderBookColumn symbol={symbol} className="flex-1" /></Panel>}
          <Panel className="overflow-hidden flex flex-col min-h-0"><BotColumn market={market} positions={positions} showAccount={layout.accountOverview} className="flex-1" /></Panel>
        </div>
      ) : isLg ? (
        <div className="grid grid-cols-[minmax(0,1fr)_300px] gap-2 flex-1 min-h-0 min-w-0">
          {chart || <Panel className="flex items-center justify-center text-[13px] font-medium text-text">Chart hidden (Settings → Layout)</Panel>}
          <div className="flex flex-col gap-2 min-h-0">
            {showBook && <Panel className="overflow-hidden flex flex-col flex-1 min-h-0"><OrderBookColumn symbol={symbol} className="flex-1" /></Panel>}
            <Panel className="overflow-hidden flex flex-col flex-1 min-h-0"><BotColumn market={market} positions={positions} showAccount={layout.accountOverview} className="flex-1" /></Panel>
          </div>
        </div>
      ) : (
        <>
          {chart}
          <Panel className="flex flex-col overflow-hidden min-h-[440px]">
            <TabBar
              size="sm"
              tabs={[
                ...(showBook ? [{ id: "book" as const, label: "Book" }, { id: "trades" as const, label: "Trades" }] : []),
                { id: "bot" as const, label: "Bot" },
                ...(layout.accountOverview ? [{ id: "account" as const, label: "Account" }] : []),
              ]}
              value={!showBook && (mobileTab === "book" || mobileTab === "trades") ? "bot" : mobileTab}
              onChange={setMobileTab}
            />
            {mobileTab === "book" && showBook && <OrderBookColumn symbol={symbol} className="flex-1" />}
            {mobileTab === "trades" && showBook && <TradesTape symbol={symbol} />}
            {(mobileTab === "bot" || (!showBook && (mobileTab === "book" || mobileTab === "trades"))) && <BotColumn market={market} positions={positions} tab="bot" className="flex-1" />}
            {mobileTab === "account" && <AccountPanel positions={positions} className="flex-1" />}
          </Panel>
        </>
      )}

      {bottom}
    </div>
  );
}
