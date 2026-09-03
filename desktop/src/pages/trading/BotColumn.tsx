import { useMemo, useState } from "react";
import { api, type PositionData, type StrategyInfo } from "@/lib/api";
import type { MarketView } from "@/hooks/useMarketInfo";
import { useEndpoint } from "@/hooks/useEndpoint";
import { useNow } from "@/hooks/useNow";
import { useSystemStore } from "@/stores/systemStore";
import { useTradingStore } from "@/stores/tradingStore";
import { useBridgeConfig } from "@/lib/config";
import { TabBar } from "@/components/ui/TabBar";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { ListRow, ListSection, Signed } from "@/components/ui/ListRow";
import { SideChip, StatusChip, StrategyTag } from "@/components/ui/Chip";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/Panel";
import { HoldTime } from "@/components/shared/TradeChips";
import { HINTS } from "@/lib/hints";
import { STRATEGY_LABELS } from "@/lib/constants";
import { cn, capitalize, formatDurationShort, formatMoney, formatPct, formatPrice, formatSignedMoney, formatSignedPct, formatSize } from "@/lib/utils";
import { isLong, positionHoldSec, positionLiquidation, positionMargin, positionNotional, positionRoe, PAPER_MAINTENANCE_MARGIN } from "@/lib/market";
import { useAccount } from "@/hooks/useAccount";
import { AccountPanel } from "./AccountPanel";

type BotTab = "bot" | "account";
const STRAT_POLL_MS = 30_000;
const CONFIG_POLL_MS = 60_000;

interface BotColumnProps {
  market: MarketView;
  positions: PositionData[];
  className?: string;
  /** Force a tab (mobile tabs) */
  tab?: BotTab;
  showAccount?: boolean;
}

/** "BTC-USD" · "BTCUSDT" · "BTC/USDT" → "BTC" so spot-style trend symbols match the perp symbol. */
function baseAsset(sym: string): string {
  return sym.toUpperCase().replace(/[-_/]/g, "").replace(/USDT?$/, "");
}

function strategyStatus(s: StrategyInfo): string {
  if (s.killed) return "killed";
  if (s.active) return "active";
  if (s.enabled ?? s.allocation > 0) return "enabled";
  return "disabled";
}

/** Bot column (spec §3.1): segmented header `Paper · 1x · Long-only`, tabs Bot · Account. */
export function BotColumn({ market, positions, className, tab: forcedTab, showAccount = true }: BotColumnProps) {
  const [tab, setTab] = useState<BotTab>("bot");
  const mode = useSystemStore((s) => s.mode);
  const active = forcedTab ?? (showAccount ? tab : "bot");
  const lev = market.rest?.symbol_config?.leverage;
  const header = [
    { id: "mode", label: capitalize(mode.replace("_", " ")), title: "Execution mode" },
    { id: "lev", label: `${typeof lev === "number" ? lev : "—"}x`, title: "Leverage configured for this symbol" },
    { id: "dir", label: "Long-only", title: "Trend daily is long-only; MR / divergence may short when enabled" },
  ];

  return (
    <div className={cn("flex flex-col min-h-0 min-w-0 bg-panel", className)}>
      {forcedTab === undefined && (
        <div className="flex items-center h-10 px-2 border-b border-hairline shrink-0">
          <SegmentedControl options={header} value="mode" onChange={() => undefined} size="sm" static className="w-full [&>button]:flex-1" />
        </div>
      )}
      {forcedTab === undefined && showAccount && (
        <TabBar size="sm" tabs={[{ id: "bot", label: "Bot" }, { id: "account", label: "Account" }]} value={tab} onChange={setTab} />
      )}
      {active === "bot" ? <BotTab market={market} positions={positions} /> : <AccountPanel positions={positions} />}
    </div>
  );
}

function BotTab({ market, positions }: { market: MarketView; positions: PositionData[] }) {
  const symbol = market.symbol;
  const now = useNow();
  const strategies = useEndpoint(() => api.strategies(), STRAT_POLL_MS);
  const trend = useEndpoint(() => api.trend(), STRAT_POLL_MS);
  const cfg = useEndpoint(() => api.config(), CONFIG_POLL_MS);
  const { acct } = useAccount(positions);
  const equity = useTradingStore((s) => s.metrics.equity);
  const { isLocal, token } = useBridgeConfig();
  const canControl = isLocal || token.length > 0;

  const symPositions = useMemo(() => positions.filter((p) => p.symbol === symbol), [positions, symbol]);
  const onSymbol = useMemo(() => {
    const list = strategies.data?.strategies ?? [];
    const base = baseAsset(symbol);
    const holding = new Set(symPositions.map((p) => p.strategy ?? ""));
    return list.filter((s) => holding.has(s.type) || !s.symbols || s.symbols.length === 0 || s.symbols.some((x) => baseAsset(x) === base));
  }, [strategies.data, symbol, symPositions]);
  const pos = symPositions[0] ?? null;
  const trading = cfg.data?.trading ?? null;
  const sc = market.rest?.symbol_config ?? null;
  const symCfg = cfg.data?.symbols.find((s) => s.symbol === symbol) ?? null;
  const leverage = sc?.leverage ?? symCfg?.leverage ?? 1;
  const taker = sc?.taker_fee ?? trading?.taker_fee ?? null;
  const eq = acct.equity > 0 ? acct.equity : equity;
  const riskUsd = trading ? trading.risk_per_trade_pct * eq : null;
  const nextRunMs = trend.data?.next_run_utc ? Date.parse(trend.data.next_run_utc) : Number.NaN;
  // targets are keyed by the Binance symbol (BTCUSDT); the UI symbol is BTC-USD (positions carry ui_symbol)
  const targets = (trend.data?.targets ?? {}) as Record<string, number>;
  const trendPositions = (trend.data?.positions ?? []) as Array<{ symbol?: string; ui_symbol?: string }>;
  const binanceSymbol = trendPositions.find((p) => p.ui_symbol === symbol)?.symbol ?? symbol.replace("-", "").replace(/USD$/, "USDT");
  const target = targets[symbol] ?? targets[binanceSymbol];
  const estEntry = market.mark > 0 ? market.mark : market.price;
  const slip = trading ? trading.slippage_bps : null;
  const estLiqLong = estEntry > 0 && leverage > 1 ? estEntry * (1 - 1 / leverage + PAPER_MAINTENANCE_MARGIN) : null;
  const marginNext = riskUsd !== null && trading ? Math.min(riskUsd * 10, trading.max_total_exposure_pct * eq) / leverage : null;

  return (
    <div className="flex flex-col min-h-0 overflow-y-auto">
      <ListSection title="Strategies on this symbol" first right={<span className="text-[12px] font-medium text-text-2">{onSymbol.length}</span>}>
        {!strategies.loaded ? (
          <p className="text-[12.5px] font-medium text-text py-1">Loading strategies…</p>
        ) : onSymbol.length === 0 ? (
          <p className="text-[12.5px] font-medium text-text py-1">No strategy is assigned to {symbol}</p>
        ) : (
          <div className="flex flex-col gap-1.5 py-1">
            {onSymbol.map((s) => {
              const isTrend = s.type === "TREND_DAILY";
              const next = isTrend && Number.isFinite(nextRunMs) ? formatDurationShort((nextRunMs - now) / 1000) : null;
              return (
                <div key={s.type} className="rounded-[6px] bg-panel-2 px-2.5 py-2">
                  <div className="flex items-center gap-2">
                    <StrategyTag strategy={s.type} />
                    <StatusChip status={strategyStatus(s)} size="xs" className="ml-auto" />
                    <span className="num text-[12px] font-semibold text-text">{formatPct(s.allocation, 0)}</span>
                  </div>
                  <p className="text-[12px] font-medium text-text-2 mt-1 leading-snug">
                    {isTrend
                      ? `Next run ${next ? `in ${next}` : "---"}${typeof target === "number" ? ` · target ${formatPct(target, 1)}` : " · no target"}${trend.data?.last_run_status ? ` · last ${trend.data.last_run_status}` : ""}`
                      : s.killed
                        ? `Killed: ${s.kill_reason ?? "edge monitor"}`
                        : s.active
                          ? "Watching 1m bars for a signal"
                          : "Disabled — no new entries"}
                  </p>
                </div>
              );
            })}
          </div>
        )}
      </ListSection>

      <ListSection title="Open position" right={symPositions.length > 1 ? <span className="text-[12px] font-medium text-text-2">{symPositions.length} on {symbol}</span> : undefined}>
        {pos ? <PositionCard p={pos} now={now} /> : <EmptyState className="py-4">No open position on {symbol}</EmptyState>}
        <Button
          variant="secondary"
          className="w-full mt-2"
          disabled
          title={canControl ? "This bridge exposes no manual close endpoint — exits are decided by the engine (SL / TP / signal / rebalance)" : "Remote bridge without a token — and this bridge exposes no manual close endpoint"}
        >
          Close position (paper)
        </Button>
      </ListSection>

      <ListSection title="Next order (estimate)">
        <ListRow label="Est. entry" hint={HINTS.mark}>{estEntry > 0 ? formatPrice(estEntry) : "---"}</ListRow>
        <ListRow label="Slippage model" hint="Paper fills are moved against you by this many bps of the mark">{slip !== null ? `${slip} bps` : "---"}</ListRow>
        <ListRow label="Est. liquidation" hint={HINTS.liq}>{estLiqLong !== null ? formatPrice(estLiqLong) : <span title="Leverage 1 — spot-like, no liquidation">none (1x)</span>}</ListRow>
        <ListRow label="Margin" hint="Capital reserved for the next position: notional / leverage">{marginNext !== null ? formatMoney(marginNext) : "---"}</ListRow>
        <ListRow label="Order size" hint="Risk manager sizing: risk_per_trade_pct × equity is the loss at the stop; the notional depends on the stop distance">{riskUsd !== null ? `${formatMoney(riskUsd)} at risk` : "---"}</ListRow>
        <ListRow label="Fees" hint="Taker fee on the entry (paper)">{taker !== null && estEntry > 0 && marginNext !== null ? `${formatPct(taker, 2)} · ${formatMoney(marginNext * leverage * taker)}` : taker !== null ? formatPct(taker, 2) : "---"}</ListRow>
      </ListSection>
    </div>
  );
}

function PositionCard({ p, now }: { p: PositionData; now: number }) {
  const roe = positionRoe(p);
  const liq = positionLiquidation(p);
  const mark = p.mark_price > 0 ? p.mark_price : p.entry_price;
  const long = isLong(p.side);
  return (
    <div className="rounded-[6px] bg-panel-2 px-2.5 py-2">
      <div className="flex items-center gap-2">
        <SideChip side={p.side} size="xs" />
        <span className="text-[13px] font-semibold text-text">{p.symbol}</span>
        <StrategyTag strategy={p.strategy} className="ml-auto text-[12px]" />
      </div>
      <div className="mt-1.5 grid grid-cols-2 gap-x-3">
        <ListRow label="Size">{formatSize(p.size)}</ListRow>
        <ListRow label="Notional" hint={HINTS.notional}>{formatMoney(positionNotional(p))}</ListRow>
        <ListRow label="Entry">{formatPrice(p.entry_price)}</ListRow>
        <ListRow label="Mark" hint={HINTS.mark}>{mark > 0 ? formatPrice(mark) : "---"}</ListRow>
        <ListRow label="PnL" hint={HINTS.pnl}><Signed value={p.unrealized_pnl ?? 0} format={formatSignedMoney} /></ListRow>
        <ListRow label="ROE" hint={HINTS.roe}><Signed value={roe} format={(v) => formatSignedPct(v)} /></ListRow>
        <ListRow label="SL" hint={HINTS.sl}>{p.stop_loss && p.stop_loss > 0 ? formatPrice(p.stop_loss) : "---"}</ListRow>
        <ListRow label="TP" hint={HINTS.tp}>{p.take_profit && p.take_profit > 0 ? formatPrice(p.take_profit) : "---"}</ListRow>
        <ListRow label="Liq." hint={HINTS.liq}>{liq.price ? formatPrice(liq.price) : "---"}</ListRow>
        <ListRow label="Margin" hint={HINTS.margin}>{formatMoney(positionMargin(p))}</ListRow>
        <ListRow label="Hold" hint={HINTS.hold}><HoldTime seconds={positionHoldSec(p, now)} /></ListRow>
        <ListRow label="Side">{long ? "Long" : "Short"} · {STRATEGY_LABELS[p.strategy ?? ""] ? "" : ""}{p.leverage ?? 1}x</ListRow>
      </div>
    </div>
  );
}
