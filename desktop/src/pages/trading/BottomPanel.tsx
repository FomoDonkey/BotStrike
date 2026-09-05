import { useMemo, useState } from "react";
import { Download } from "lucide-react";
import type { PositionData, ProtectiveOrder, TradeRecord } from "@/lib/api";
import { tradesExportUrl } from "@/lib/api";
import type { SignalData } from "@/stores/tradingStore";
import { useActivity } from "@/hooks/useActivity";
import { TabBar } from "@/components/ui/TabBar";
import { Popover, MenuItem, DropdownTrigger } from "@/components/ui/Popover";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { ActivityFeed } from "@/components/ui/ActivityFeed";
import { STRATEGY_LABELS, SYMBOLS } from "@/lib/constants";
import { isLong, tradePositionSide } from "@/lib/market";
import { PositionsTable } from "./PositionsTable";
import { useOrders } from "@/hooks/useOrders";
import { OrdersTable } from "./OrdersTable";
import { OrderHistoryTable, type OrderRow } from "./OrderHistoryTable";
import { TradeHistoryTable } from "./TradeHistoryTable";
import { SignalsFeed } from "./SignalsFeed";

type BottomTab = "positions" | "orders" | "order_history" | "history" | "signals" | "activity";
type Side = "all" | "long" | "short";
type Scope = "market" | "all";

interface BottomPanelProps {
  symbol: string;
  positions: PositionData[];
  trades: TradeRecord[];
  closed: TradeRecord[];
  signals: SignalData[];
  loading: boolean;
  error: string | null;
  activityEnabled: boolean;
}

/** Positions · Orders · Order History · Trade History · Signals · Activity with counts, filters and CSV export. */
export function BottomPanel({ symbol, positions, trades, closed, signals, loading, error, activityEnabled }: BottomPanelProps) {
  const [tab, setTab] = useState<BottomTab>("positions");
  const [scope, setScope] = useState<Scope>("all");
  const [market, setMarket] = useState<string>("all");
  const [strategy, setStrategy] = useState<string>("all");
  const [side, setSide] = useState<Side>("all");
  const { rows: orders } = useOrders(positions);
  const activity = useActivity(100, activityEnabled);

  const marketFilter = scope === "market" ? symbol : market;
  // every market with a position or a trade, not the four intraday symbols: the book held six
  // markets and the menu could name only four of them (2026-09-05)
  const markets = useMemo(() => {
    const set = new Set<string>(SYMBOLS);
    for (const p of positions) if (p.symbol) set.add(p.symbol);
    for (const t of trades) if (t.symbol) set.add(t.symbol);
    for (const t of closed) if (t.symbol) set.add(t.symbol);
    return [...set].sort();
  }, [positions, trades, closed]);
  const strategies = useMemo(() => {
    const set = new Set<string>();
    for (const p of positions) if (p.strategy) set.add(p.strategy);
    for (const t of trades) if (t.strategy) set.add(t.strategy);
    for (const s of signals) set.add(s.strategy);
    return [...set].sort();
  }, [positions, trades, signals]);

  const fPositions = useMemo(() => positions.filter((p) =>
    (marketFilter === "all" || p.symbol === marketFilter) && (strategy === "all" || p.strategy === strategy) && (side === "all" || (side === "long") === isLong(p.side))),
  [positions, marketFilter, strategy, side]);
  const fClosed = useMemo(() => closed.filter((t) =>
    (marketFilter === "all" || t.symbol === marketFilter) && (strategy === "all" || t.strategy === strategy) && (side === "all" || (side === "long") === (tradePositionSide(t) === "LONG"))),
  [closed, marketFilter, strategy, side]);
  const fSignals = useMemo(() => signals.filter((s) =>
    (marketFilter === "all" || s.symbol === marketFilter) && (strategy === "all" || s.strategy === strategy) && (side === "all" || (side === "long") === isLong(s.side))),
  [signals, marketFilter, strategy, side]);
  const orderFilter = (o: ProtectiveOrder) => (marketFilter === "all" || o.symbol === marketFilter) && (strategy === "all" || o.strategy === strategy);
  const fOrders = orders.filter(orderFilter).length;
  const orderRowFilter = (r: OrderRow) => (marketFilter === "all" || r.symbol === marketFilter) && (strategy === "all" || r.strategy === strategy);
  const fTrades = useMemo(() => trades.filter((t) => marketFilter === "all" || t.symbol === marketFilter), [trades, marketFilter]);

  const tabs = [
    { id: "positions" as const, label: "Positions", count: fPositions.length },
    { id: "orders" as const, label: "Orders", count: fOrders },
    { id: "order_history" as const, label: "Order History" },
    { id: "history" as const, label: "Trade History", count: fClosed.length },
    { id: "signals" as const, label: "Signals", count: fSignals.length },
    ...(activityEnabled ? [{ id: "activity" as const, label: "Activity", count: activity.events.length || undefined }] : []),
  ];

  const right = (
    <div className="flex items-center gap-1.5 whitespace-nowrap">
      <SegmentedControl size="sm" value={scope} onChange={setScope} options={[{ id: "market", label: symbol.split("-")[0] }, { id: "all", label: "All" }]} />
      <Popover align="right" width="w-36" trigger={(open) => <DropdownTrigger size="xs" open={open} label={market === "all" ? "Markets" : market} />}>
        {(close) => (
          <>
            <MenuItem active={market === "all"} onClick={() => { setMarket("all"); setScope("all"); close(); }}>All markets</MenuItem>
            {markets.map((s) => <MenuItem key={s} active={market === s} onClick={() => { setMarket(s); setScope("all"); close(); }}>{s}</MenuItem>)}
          </>
        )}
      </Popover>
      <Popover align="right" width="w-44" trigger={(open) => <DropdownTrigger size="xs" open={open} label={strategy === "all" ? "Strategy" : (STRATEGY_LABELS[strategy] ?? strategy)} />}>
        {(close) => (
          <>
            <MenuItem active={strategy === "all"} onClick={() => { setStrategy("all"); close(); }}>All strategies</MenuItem>
            {strategies.map((s) => <MenuItem key={s} active={strategy === s} onClick={() => { setStrategy(s); close(); }}>{STRATEGY_LABELS[s] ?? s}</MenuItem>)}
            {strategies.length === 0 && <MenuItem disabled>No strategy has traded yet</MenuItem>}
          </>
        )}
      </Popover>
      <Popover align="right" width="w-32" trigger={(open) => <DropdownTrigger size="xs" open={open} label={side === "all" ? "Type" : side === "long" ? "Long" : "Short"} />}>
        {(close) => (
          <>
            <MenuItem active={side === "all"} onClick={() => { setSide("all"); close(); }}>All</MenuItem>
            <MenuItem active={side === "long"} onClick={() => { setSide("long"); close(); }}>Long</MenuItem>
            <MenuItem active={side === "short"} onClick={() => { setSide("short"); close(); }}>Short</MenuItem>
          </>
        )}
      </Popover>
      <a
        href={tradesExportUrl()}
        download="botstrike-trades.csv"
        target="_blank"
        rel="noreferrer"
        title="Download every trade as CSV (GET /api/trades/export.csv)"
        className="inline-flex items-center gap-1 h-6 px-2 rounded-[6px] text-[12px] font-medium text-text hover:bg-hover"
      >
        <Download className="w-3.5 h-3.5" /> Export
      </a>
    </div>
  );

  return (
    <div className="flex flex-col flex-1 min-h-0 min-w-0">
      <TabBar size="sm" tabs={tabs} value={tab} onChange={setTab} right={<div className="hidden md:block">{right}</div>} />
      <div className="md:hidden flex items-center h-9 px-2 border-b border-hairline-soft overflow-x-auto scrollbar-none shrink-0">{right}</div>
      {tab === "positions" && <PositionsTable positions={fPositions} symbol={symbol} />}
      {tab === "orders" && <OrdersTable positions={positions} symbol={symbol} filter={orderFilter} />}
      {tab === "order_history" && <OrderHistoryTable trades={fTrades} symbol={symbol} loading={loading} filter={orderRowFilter} />}
      {tab === "history" && <TradeHistoryTable trades={fClosed} loading={loading} error={error} symbol={symbol} />}
      {tab === "signals" && <SignalsFeed signals={fSignals} />}
      {tab === "activity" && <ActivityFeed limit={100} symbol={marketFilter === "all" ? undefined : marketFilter} compact />}
    </div>
  );
}
