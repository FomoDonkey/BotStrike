import { useState } from "react";
import { FlaskConical, Play } from "lucide-react";
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts";
import { Panel, PanelHeader, EmptyState } from "@/components/ui/Panel";
import { Button } from "@/components/ui/Button";
import { KpiCard } from "@/components/ui/KpiCard";
import { ListRow, ListSection, Signed } from "@/components/ui/ListRow";
import { cn, formatMoney, formatPct, formatSignedMoney } from "@/lib/utils";
import { CHART_GRID, CHART_TEXT, CHART_TOOLTIP_ITEM, CHART_TOOLTIP_LABEL, CHART_TOOLTIP_STYLE, COLOR_DOWN, COLOR_UP, STRATEGY_LABELS, SYMBOLS } from "@/lib/constants";
import { api, ApiError, type BacktestResult } from "@/lib/api";
import { getBridgeUrl, useBridgeConfig } from "@/lib/config";
import { useExchangeStore } from "@/stores/exchangeStore";
import { INPUT_CLS } from "@/components/settings/FieldInput";

// These engines are RETIRED: they have no gross edge and the bot can no longer allocate capital to
// them (core.types.RETIRED_STRATEGIES refuses it). They stay runnable HERE on purpose — being able to
// reproduce the evidence beats being asked to trust a document (Edgar, 2026-09-04).
const AVAILABLE_STRATEGIES = [
  { value: "MEAN_REVERSION", label: "Mean Reversion — retired, verify only", active: true },
  { value: "FIBONACCI_RETRACEMENT", label: "Fibonacci Retracement — retired, verify only", active: true },
  { value: "ORDER_FLOW_MOMENTUM", label: "Order Flow Momentum (archived)", active: false },
  { value: "TREND_FOLLOWING", label: "Trend Following (archived)", active: false },
  { value: "MARKET_MAKING", label: "Market Making (archived)", active: false },
];

/** Backtest (spec §3.5): restyle only — no logic changes. */
export function BacktestPage() {
  const [symbol, setSymbol] = useState("BTC-USD");
  const [strategy, setStrategy] = useState("MEAN_REVERSION");   // the only 1m engine the backtester runs
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const { isLocal, token } = useBridgeConfig();
  const canRun = isLocal || token.length > 0;

  const runBacktest = async () => {
    setRunning(true);
    setError(null);
    setResult(null);
    setElapsed(0);
    const t0 = Date.now();
    const timer = setInterval(() => setElapsed(Math.floor((Date.now() - t0) / 1000)), 1000);
    try {
      const data = await api.backtestRun({ symbol, strategy, exchange: useExchangeStore.getState().exchange });
      setResult(data);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : `Bridge unreachable at ${getBridgeUrl()}`);
    }
    clearInterval(timer);
    setElapsed(Math.floor((Date.now() - t0) / 1000));
    setRunning(false);
  };

  const curveData = result?.equity_curve?.map((v, i) => ({ idx: i, equity: v })) || [];
  const isProfitable = (result?.pnl ?? 0) >= 0;
  const stroke = isProfitable ? COLOR_UP : COLOR_DOWN;

  return (
    <div className="flex flex-col gap-3 p-3 sm:p-4 min-w-0">
      <h1 className="text-[18px] font-semibold text-text flex items-center gap-2"><FlaskConical className="w-5 h-5 text-mint" /> Backtest</h1>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
        <Panel>
          <PanelHeader title="Configuration" />
          <div className="px-4 py-3 space-y-3">
            <label className="block">
              <span className="text-[12.5px] font-medium text-text-2 block mb-1">Symbol</span>
              <select value={symbol} onChange={(e) => setSymbol(e.target.value)} className={cn(INPUT_CLS, "bs-select")}>
                {SYMBOLS.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            </label>
            <label className="block">
              <span className="text-[12.5px] font-medium text-text-2 block mb-1">Strategy</span>
              <select value={strategy} onChange={(e) => setStrategy(e.target.value)} className={cn(INPUT_CLS, "bs-select")}>
                {AVAILABLE_STRATEGIES.map((s) => <option key={s.value} value={s.value} disabled={!s.active}>{s.label}</option>)}
              </select>
            </label>
            <Button variant="primary" className="w-full h-9" icon={<Play className="w-4 h-4" />} onClick={runBacktest} loading={running} disabled={!canRun} title={canRun ? undefined : "Remote bridge — set the auth token in Settings → Connection to run backtests"}>
              {running ? `Running… ${elapsed}s` : "Run backtest"}
            </Button>
            {!canRun && <p className="text-[12px] font-medium text-amber">Remote bridge without a token — backtests are disabled here.</p>}
            {error && <div className="p-3 rounded-[6px] bg-rose-soft text-rose text-[12.5px] font-medium break-words">{error}</div>}
            {result && (
              <ListSection first className="px-0">
                <ListRow label="Bars tested">{result.bars_tested?.toLocaleString() ?? "?"}</ListRow>
                <ListRow label="Elapsed">{elapsed}s</ListRow>
                <ListRow label="Strategy">{STRATEGY_LABELS[strategy] ?? strategy}</ListRow>
              </ListSection>
            )}
          </div>
        </Panel>

        <Panel className="lg:col-span-2 flex flex-col">
          <PanelHeader title="Results" />
          {!result && !running && <EmptyState sub="Reproduces a RETIRED engine over the local 1m history: it verifies the verdict, it does not enable anything. The daily trend book is validated in the research, and Divergence with scripts/divergence_research.py.">Configure and run a backtest to see results</EmptyState>}
          {running && <EmptyState sub={`${elapsed}s`}>Running backtest…</EmptyState>}
          {result && (
            <div className="p-3 space-y-3">
              <div className="grid grid-cols-2 sm:grid-cols-3 xl:grid-cols-6 gap-2">
                <KpiCard label="Net PnL" value={<Signed value={result.pnl} format={formatSignedMoney} />} />
                <KpiCard label="Return" value={<Signed value={(result.return_pct ?? 0) / 100} format={(v) => `${v > 0 ? "+" : ""}${(v * 100).toFixed(2)}%`} />} />
                <KpiCard label="Trades" value={result.total_trades} />
                <KpiCard label="Win rate" value={formatPct(result.win_rate)} tone={result.win_rate >= 0.5 ? "mint" : "rose"} />
                <KpiCard label="Sharpe" value={result.sharpe_ratio?.toFixed(2) ?? "0"} tone={result.sharpe_ratio > 0 ? "mint" : "rose"} />
                <KpiCard label="Max DD" value={formatPct(result.max_drawdown)} tone="rose" />
              </div>
              <div className="grid grid-cols-2 xl:grid-cols-4 gap-x-6 px-1">
                <ListRow label="Profit factor">{result.profit_factor?.toFixed(2) ?? "0"}</ListRow>
                <ListRow label="Avg trade">{formatMoney(result.avg_trade_pnl ?? 0)}</ListRow>
                <ListRow label="Total fees">{formatMoney(result.total_fees ?? 0)}</ListRow>
                <ListRow label="Expectancy">{result.total_trades > 0 ? formatMoney(result.pnl / result.total_trades) : "$0.00"}</ListRow>
              </div>
              {curveData.length > 0 && (
                <ResponsiveContainer width="100%" height={240}>
                  <AreaChart data={curveData}>
                    <defs>
                      <linearGradient id="btGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor={stroke} stopOpacity={0.25} />
                        <stop offset="95%" stopColor={stroke} stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid stroke={CHART_GRID} vertical={false} />
                    <XAxis dataKey="idx" hide />
                    <YAxis tick={{ fill: CHART_TEXT, fontSize: 11 }} axisLine={false} tickLine={false} width={64} tickFormatter={(v) => formatMoney(Number(v), 0)} domain={["dataMin - 5", "dataMax + 5"]} />
                    <Tooltip contentStyle={CHART_TOOLTIP_STYLE} labelStyle={CHART_TOOLTIP_LABEL} itemStyle={CHART_TOOLTIP_ITEM} formatter={(v: unknown) => [formatMoney(Number(v)), "Equity"]} />
                    <Area type="monotone" dataKey="equity" stroke={stroke} fill="url(#btGrad)" strokeWidth={2} dot={false} isAnimationActive={false} />
                  </AreaChart>
                </ResponsiveContainer>
              )}
              {result.total_trades === 0 && (
                <div className="p-3 rounded-[6px] bg-amber-soft text-amber text-[12.5px] font-medium">
                  No trades generated. The strategy needs specific market conditions to trigger — try more data, another symbol or another strategy.
                </div>
              )}
            </div>
          )}
        </Panel>
      </div>
    </div>
  );
}
