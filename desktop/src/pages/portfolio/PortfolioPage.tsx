import { useMemo, useState } from "react";
import { useShallow } from "zustand/shallow";
import { ChevronDown } from "lucide-react";
import { api } from "@/lib/api";
import { usePortfolio } from "@/hooks/usePortfolio";
import { useEndpoint } from "@/hooks/useEndpoint";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { useNow } from "@/hooks/useNow";
import { useAccount } from "@/hooks/useAccount";
import { useTradingStore } from "@/stores/tradingStore";
import { useSystemStore } from "@/stores/systemStore";
import { useExchangeStore } from "@/stores/exchangeStore";
import { Panel, PanelHeader, EmptyState } from "@/components/ui/Panel";
import { ListRow, ListSection, Signed } from "@/components/ui/ListRow";
import { KpiCard, ProgressBar } from "@/components/ui/KpiCard";
import { WinDayDots } from "@/components/ui/WinDayDots";
import { BiasBar } from "@/components/ui/BiasBar";
import { TabBar } from "@/components/ui/TabBar";
import { StatusChip } from "@/components/ui/Chip";
import { ActivityFeed } from "@/components/ui/ActivityFeed";
import { FundingCostCard } from "@/components/ui/Funding";
import { useFunding } from "@/hooks/useFunding";
import { DataTable, type Column } from "@/components/ui/DataTable";
import { EXCHANGE_LABELS } from "@/lib/constants";
import { HINTS } from "@/lib/hints";
import { cn, formatDurationShort, formatMoney, formatPct, formatPrice, formatSignedMoney, formatSize } from "@/lib/utils";
import { PositionsTable } from "@/pages/trading/PositionsTable";
import { OrdersTable } from "@/pages/trading/OrdersTable";
import { OrderHistoryTable } from "@/pages/trading/OrderHistoryTable";
import { TradeHistoryTable } from "@/pages/trading/TradeHistoryTable";
import { useTradeHistory } from "@/pages/trading/useTradeHistory";
import { PortfolioChart } from "./PortfolioChart";
import type { TrendPosition } from "@/lib/api";

type TableTab = "positions" | "trend" | "orders" | "order_history" | "history";
const TABLE_TABS = [
  { id: "positions" as const, label: "Positions" },
  { id: "trend" as const, label: "Trend book" },
  { id: "orders" as const, label: "Open Orders" },
  { id: "order_history" as const, label: "Order History" },
  { id: "history" as const, label: "Trade History" },
];

function todayIsoUtc(nowMs: number): string {
  return new Date(nowMs).toISOString().slice(0, 10);
}

/** Portfolio page (spec §3.2): left account list · KPI cards + chart + tables · recent activity. */
export function PortfolioPage() {
  const pf = usePortfolio();
  const p = pf.data && pf.data.engine !== false ? pf.data : null;
  const perf = useEndpoint(() => api.performance(), 30_000);
  const trend = useEndpoint(() => api.trend(), 30_000);
  const positionsMap = useTradingStore(useShallow((s) => s.positions));
  const positions = useMemo(() => Object.values(positionsMap).flat(), [positionsMap]);
  const metrics = useTradingStore(useShallow((s) => s.metrics));
  const mode = useSystemStore((s) => s.mode);
  const exchange = useExchangeStore((s) => s.exchange);
  const { acct } = useAccount(positions);
  const history = useTradeHistory();
  const funding = useFunding();
  const [tab, setTab] = useState<TableTab>("positions");
  const [accountOpen, setAccountOpen] = useState(false);
  const isXl = useMediaQuery("(min-width: 1280px)");
  const isLg = useMediaQuery("(min-width: 1024px)");
  const now = useNow();

  // Real numbers only: /api/portfolio when it exists, otherwise the pieces the 2.15 endpoints carry.
  const equity = p?.equity ?? acct.equity;
  const cash = p?.cash ?? acct.available;
  const unreal = p?.unrealized_pnl ?? acct.unrealized_pnl;
  const alltimePnl = p?.alltime_pnl ?? perf.data?.pnl ?? metrics.pnl;
  const fees = p?.fees_paid ?? perf.data?.total_fees ?? metrics.total_fees;
  const leverage = p?.leverage ?? acct.leverage_effective;
  const marginUsage = p?.margin_usage ?? acct.margin_ratio;
  const initial = p?.initial_capital ?? (acct.initial_capital > 0 ? acct.initial_capital : perf.data?.initial_capital ?? null);
  const since = p?.since_ts ?? perf.data?.first_trade_ts ?? null;
  const trendBook = p?.trend_book_notional ?? (trend.data?.positions?.reduce((a, t) => a + (t.notional || 0), 0) ?? null);
  const winDays = p?.win_days ?? null;
  const wins = winDays?.filter((d) => d.result === "win").length ?? 0;
  const tradedDays = winDays?.filter((d) => d.trades > 0).length ?? 0;
  const longN = p?.bias.long_notional ?? positions.filter((x) => x.side === "BUY" || x.side === "LONG").reduce((a, x) => a + Math.abs((x.notional ?? x.size * x.mark_price) || 0), 0);
  const shortN = p?.bias.short_notional ?? positions.filter((x) => x.side === "SELL" || x.side === "SHORT").reduce((a, x) => a + Math.abs((x.notional ?? x.size * x.mark_price) || 0), 0);
  const sharpe30 = p?.perf_30d;
  const todayIso = todayIsoUtc(now);
  const fundingTotal = typeof funding.data?.total_paid === "number"
    ? funding.data.total_paid
    : typeof acct.funding_paid === "number" ? acct.funding_paid : null;

  const left = (
    <Panel className="flex flex-col min-h-0">
      <ListSection first>
        <ListRow label="Mode"><StatusChip status={p?.mode ?? mode} size="xs" /></ListRow>
        <ListRow label="Feed">{EXCHANGE_LABELS[exchange] ?? exchange}</ListRow>
        <ListRow label="Initial capital">{initial !== null ? formatMoney(initial) : "---"}</ListRow>
        <ListRow label="Since" hint="First run of the paper book (UTC)">{since ? new Date(since * 1000).toISOString().slice(0, 10) : "---"}</ListRow>
      </ListSection>
      <ListSection>
        <p className="text-[12.5px] font-medium text-text-2">Account value</p>
        <p className="num text-[28px] font-bold text-text leading-tight">{formatMoney(equity)}</p>
        <p className="text-[12.5px] font-medium">
          <Signed value={alltimePnl} format={formatSignedMoney} /> <span className="text-text-2">all time</span>
          {initial && initial > 0 && <span className="text-text-2"> · <Signed value={alltimePnl / initial} format={(v) => `${v > 0 ? "+" : ""}${(v * 100).toFixed(2)}%`} /></span>}
        </p>
      </ListSection>
      <ListSection title="Account equity">
        <ListRow label="Paper balance" hint="Cash = equity − margin used by open positions">{formatMoney(cash)}</ListRow>
        <ListRow label="Trend book" hint="Notional of the trend daily positions">{trendBook !== null ? formatMoney(trendBook) : "---"}</ListRow>
        <ListRow label="Unrealized PNL" hint={HINTS.pnl}><Signed value={unreal} format={formatSignedMoney} /></ListRow>
      </ListSection>
      <ListSection title="Overview">
        <ListRow label="Unrealized PNL"><Signed value={unreal} format={formatSignedMoney} /></ListRow>
        <ListRow label="Account leverage" hint={HINTS.levEff}>{leverage.toFixed(2)}x</ListRow>
        <ListRow label="Margin usage" hint={HINTS.marginRatio}>{formatPct(marginUsage, 1)}</ListRow>
        <ListRow label="All Time PNL"><Signed value={alltimePnl} format={formatSignedMoney} /></ListRow>
        <ListRow label="All Time Volume" hint="Sum of entry and exit notionals">{p ? formatMoney(p.alltime_volume) : "---"}</ListRow>
        <ListRow label="Fees paid">{formatMoney(fees)}</ListRow>
        <ListRow label="Funding paid" hint={HINTS.fundingPaid}>
          <Signed value={fundingTotal ?? undefined} format={(v) => formatSignedMoney(v, 4)} />
        </ListRow>
      </ListSection>
      <ListSection title="30 Day Volume" right={p ? formatMoney(p.volume_30d) : "---"}>
        <button type="button" onClick={() => setTab("history")} className="text-[12.5px] font-semibold text-mint hover:underline">See trade history →</button>
      </ListSection>
      <ListSection title="Fees">
        <ListRow label="Taker (paper)">{p ? formatPct(p.fees_taker, 2) : "---"}</ListRow>
        <ListRow label="Maker (paper)">{p ? formatPct(p.fees_maker, 2) : "---"}</ListRow>
      </ListSection>
      <ListSection title="Analysis">
        <ListRow label="Longest win streak" hint="Consecutive UTC days with positive realised PnL">{p ? `${p.analysis.longest_win_streak_days} days` : "---"}</ListRow>
        <ListRow label="Trading style">{p ? p.analysis.trading_style : "---"}</ListRow>
        <ListRow label="Avg trade duration">{p ? (p.analysis.avg_hold_sec > 0 ? formatDurationShort(p.analysis.avg_hold_sec) : "---") : "---"}</ListRow>
        <ListRow label="Median trade duration">{p ? (p.analysis.median_hold_sec > 0 ? formatDurationShort(p.analysis.median_hold_sec) : "---") : "---"}</ListRow>
      </ListSection>
      <ListSection title="Performance 30D">
        <ListRow label="Drawdown" hint={HINTS.drawdown}><span className={cn(sharpe30 && sharpe30.drawdown > 0 && "text-rose")}>{sharpe30 ? formatPct(sharpe30.drawdown) : "---"}</span></ListRow>
        <ListRow label="Win rate">{sharpe30 ? formatPct(sharpe30.win_rate, 1) : "---"}</ListRow>
        <ListRow label="Sharpe" hint={sharpe30 && !sharpe30.sharpe_valid ? sharpe30.sharpe_reason ?? "needs 30 trades and 30 days" : "Annualised Sharpe of the daily returns"}>
          {sharpe30 ? (sharpe30.sharpe_valid && typeof sharpe30.sharpe === "number" ? sharpe30.sharpe.toFixed(2) : <span title={sharpe30.sharpe_reason}>n/a · {sharpe30.sharpe_reason ?? "needs 30 trades and 30 days"}</span>) : "---"}
        </ListRow>
        <ListRow label="Trades">{sharpe30 ? sharpe30.trades : "---"}</ListRow>
      </ListSection>
      {pf.missing && <p className="px-3 py-2 text-[12px] font-medium text-text-2 border-t border-hairline">Rows marked --- need GET /api/portfolio (bridge ≥ 2.16).</p>}
    </Panel>
  );

  const kpis = (
    <div className="grid grid-cols-2 xl:grid-cols-4 gap-2">
      <KpiCard label="Performance" hint="All-time PnL and the last 18 UTC days (mint = win day, rose = loss day)" value={<Signed value={alltimePnl} format={formatSignedMoney} />} unit="PNL" sub={winDays ? `${wins} win day${wins === 1 ? "" : "s"} · ${tradedDays} day${tradedDays === 1 ? "" : "s"} traded` : "Win days need bridge ≥ 2.16"}>
        <WinDayDots days={winDays} />
      </KpiCard>
      <KpiCard label="Leverage" hint={HINTS.levEff} value={`${leverage.toFixed(2)}x`} sub={`Position value ${formatMoney(p ? p.equity * p.leverage : acct.position_value)}`}>
        <ProgressBar ratio={leverage / 5} tone={leverage >= 3 ? "rose" : leverage >= 1.5 ? "amber" : "mint"} />
      </KpiCard>
      <KpiCard label="Margin usage" hint={HINTS.marginRatio} value={formatPct(marginUsage, 1)} sub={`${formatMoney(cash, 0)} free`}>
        <ProgressBar ratio={marginUsage} tone={marginUsage >= 0.8 ? "rose" : marginUsage >= 0.5 ? "amber" : "mint"} />
      </KpiCard>
      <KpiCard label="Direction bias" hint="Long vs short notional of the open positions" value={`${((p?.bias.long_pct ?? (longN + shortN > 0 ? longN / (longN + shortN) : 0)) * 100).toFixed(0)}%`} unit="long">
        <BiasBar longNotional={longN} shortNotional={shortN} />
      </KpiCard>
    </div>
  );

  const chart = (
    <Panel className="flex flex-col min-h-[320px] overflow-hidden">
      <PortfolioChart days={p?.daily ?? []} missing={pf.missing} todayIso={todayIso} />
    </Panel>
  );

  const tables = (
    <Panel className="flex flex-col overflow-hidden min-h-[280px] max-h-[520px]">
      <TabBar size="sm" tabs={TABLE_TABS.map((t) => ({ ...t, count: t.id === "positions" ? positions.length : t.id === "trend" ? trend.data?.positions?.length : t.id === "history" ? history.closed.length : undefined }))} value={tab} onChange={setTab} />
      {tab === "positions" && <PositionsTable positions={positions} />}
      {tab === "trend" && <TrendBookTable rows={trend.data?.positions ?? []} loaded={trend.loaded} />}
      {tab === "orders" && <OrdersTable positions={positions} />}
      {tab === "order_history" && <OrderHistoryTable trades={history.trades} loading={history.loading} />}
      {tab === "history" && <TradeHistoryTable trades={history.closed} loading={history.loading} error={history.error} />}
    </Panel>
  );

  const fundingCard = <FundingCostCard funding={funding} />;

  const activity = (
    <Panel className="flex flex-col overflow-hidden min-h-[320px] lg:min-h-0 lg:flex-1">
      <PanelHeader title="Recent activity" dense />
      <ActivityFeed limit={100} />
    </Panel>
  );

  if (isXl) {
    return (
      <div className="grid grid-cols-[290px_minmax(0,1fr)_330px] gap-2 p-2 h-full min-h-0">
        <div className="min-h-0 overflow-y-auto">{left}</div>
        <div className="flex flex-col gap-2 min-h-0 overflow-y-auto">
          {kpis}
          {chart}
          {tables}
          {fundingCard}
        </div>
        <div className="min-h-0 flex flex-col">{activity}</div>
      </div>
    );
  }
  if (isLg) {
    return (
      <div className="grid grid-cols-[290px_minmax(0,1fr)] gap-2 p-2 h-full min-h-0">
        <div className="min-h-0 overflow-y-auto">{left}</div>
        <div className="flex flex-col gap-2 min-h-0 overflow-y-auto">
          {kpis}
          {chart}
          {tables}
          {fundingCard}
          <div className="min-h-[360px] flex flex-col">{activity}</div>
        </div>
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-2 p-2 min-w-0">
      {kpis}
      <Panel>
        <button type="button" onClick={() => setAccountOpen((o) => !o)} className="w-full flex items-center h-10 px-3 text-[13px] font-semibold text-text" aria-expanded={accountOpen}>
          Account <span className="ml-2 num font-semibold">{formatMoney(equity)}</span>
          <ChevronDown className={cn("w-4 h-4 ml-auto transition-transform", accountOpen && "rotate-180")} />
        </button>
        {accountOpen && <div className="border-t border-hairline">{left}</div>}
      </Panel>
      {chart}
      {tables}
      {fundingCard}
      {activity}
    </div>
  );
}

function TrendBookTable({ rows, loaded }: { rows: TrendPosition[]; loaded: boolean }) {
  const columns: Column<TrendPosition>[] = [
    { id: "symbol", label: "Symbol", align: "l", sortValue: (r) => r.symbol, render: (r) => <span className="font-semibold">{r.symbol}</span> },
    { id: "size", label: "Size", sortValue: (r) => r.size, render: (r) => <span className="num">{formatSize(r.size)}</span> },
    { id: "entry", label: "Entry", sortValue: (r) => r.entry_price, render: (r) => <span className="num">{formatPrice(r.entry_price)}</span> },
    { id: "mark", label: "Mark", sortValue: (r) => r.mark_price, render: (r) => <span className="num">{formatPrice(r.mark_price)}</span> },
    { id: "notional", label: "Notional", sortValue: (r) => r.notional, render: (r) => <span className="num">{formatMoney(r.notional)}</span> },
    { id: "pnl", label: "Unrealized PNL", sortValue: (r) => r.unrealized_pnl, render: (r) => <Signed value={r.unrealized_pnl} format={formatSignedMoney} /> },
    { id: "weight", label: "Weight", sortValue: (r) => r.weight, render: (r) => <span className="num">{formatPct(r.weight, 1)}</span> },
    { id: "opened", label: "Opened", align: "l", render: (r) => r.opened },
  ];
  if (!loaded) return <EmptyState>Loading trend book…</EmptyState>;
  return <DataTable columns={columns} rows={rows} rowKey={(r) => r.symbol} minWidth="720px" emptyText="No trend positions" emptySub="The daily run opens positions at 00:05 UTC when the Donchian ensemble has targets" />;
}
