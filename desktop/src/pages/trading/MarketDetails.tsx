import { useMemo } from "react";
import { api, type PositionData } from "@/lib/api";
import type { MarketView } from "@/hooks/useMarketInfo";
import { useEndpoint } from "@/hooks/useEndpoint";
import { useMicroStore } from "@/stores/microStore";
import { useExchangeStore } from "@/stores/exchangeStore";
import { useMarketStore } from "@/stores/marketStore";
import { ListRow, ListSection } from "@/components/ui/ListRow";
import { StrategyTag } from "@/components/ui/Chip";
import { HINTS } from "@/lib/hints";
import { EXCHANGE_LABELS, STRATEGY_DESCRIPTIONS, SYMBOL_LABELS } from "@/lib/constants";
import { cn, formatCompact, formatCompactUSD, formatPct, formatPrice, formatSignedPct, formatUSD } from "@/lib/utils";
import { formatCountdown, PAPER_MAINTENANCE_MARGIN, positionNotional } from "@/lib/market";

const CONFIG_POLL_MS = 60_000;

const ABOUT: Record<string, string> = {
  "BTC-USD": "Bitcoin perpetual. The largest and most liquid crypto market; the bot's regime reference symbol. Prices from Binance Futures, execution on Strike.",
  "ETH-USD": "Ether perpetual. Second by liquidity; trades in the same trend and mean-reversion books as BTC. Prices from Binance Futures, execution on Strike.",
  "SOL-USD": "Solana perpetual. Higher beta than BTC/ETH — wider ATR stops and smaller sizes. Prices from Binance Futures, execution on Strike.",
  "ADA-USD": "Cardano perpetual. Lower price, larger contract sizes; same risk rules as the other symbols. Prices from Binance Futures, execution on Strike.",
};

/** Details tab (spec §3.1): About · Order size rules · Funding & fees · Price protection · Regime parameters. */
export function MarketDetails({ market: m, positions }: { market: MarketView; positions: PositionData[] }) {
  const symbol = m.symbol;
  const ob = useMarketStore((s) => s.orderbooks[symbol]);
  const micro = useMicroStore((s) => s.snapshots[symbol]);
  const exchange = useExchangeStore((s) => s.exchange);
  const cfg = useEndpoint(() => api.config(), CONFIG_POLL_MS);
  const symCfg = useMemo(() => cfg.data?.symbols.find((s) => s.symbol === symbol) ?? null, [cfg.data, symbol]);
  const trading = cfg.data?.trading ?? null;
  const sc = m.rest?.symbol_config ?? null;
  const leverage = sc?.leverage ?? symCfg?.leverage ?? null;
  const maxPos = sc?.max_position_usd ?? symCfg?.max_position_usd ?? null;
  const minNotional = sc?.min_notional_usd ?? null;
  const taker = sc?.taker_fee ?? trading?.taker_fee ?? null;
  const maker = sc?.maker_fee ?? trading?.maker_fee ?? null;
  const mm = sc?.maintenance_margin ?? PAPER_MAINTENANCE_MARGIN;
  const strategies = sc?.strategies ?? (symCfg && Array.isArray(symCfg.strategies) ? (symCfg.strategies as string[]) : null);
  const openNotional = positions.filter((p) => p.symbol === symbol).reduce((a, p) => a + positionNotional(p), 0);
  const base = SYMBOL_LABELS[symbol] ?? symbol.split("-")[0];

  return (
    <div className="flex-1 min-h-0 overflow-auto">
      <div className="grid grid-cols-1 md:grid-cols-2 2xl:grid-cols-3 gap-x-6">
        <div className="min-w-0 md:col-span-2 2xl:col-span-3">
          <ListSection title={`About ${symbol}`} first>
            <p className="text-[13px] font-medium text-text leading-relaxed">{ABOUT[symbol] ?? `${base} perpetual on ${EXCHANGE_LABELS[exchange] ?? exchange}.`}</p>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 mt-2 text-[12.5px]">
              {/* This is where the PRICES come from. Execution is Strike; calling the feed "Venue"
                  on a page about a market the bot trades elsewhere reads as the wrong claim. */}
              <span className="font-medium text-text-2">Price feed <span className="text-text font-semibold">{EXCHANGE_LABELS[exchange] ?? exchange}</span></span>
              <span className="font-medium text-text-2">Execution <span className="text-text font-semibold">Strike · paper</span></span>
              <span className="font-medium text-text-2">Type <span className="text-text font-semibold">Perpetual · paper</span></span>
              <span className="font-medium text-text-2">Base / quote <span className="text-text font-semibold">{base} / USD</span></span>
              {strategies && strategies.length > 0 && (
                <span className="inline-flex items-center gap-2 font-medium text-text-2">Strategies {strategies.map((s) => <StrategyTag key={s} strategy={s} />)}</span>
              )}
            </div>
          </ListSection>
        </div>

        <div className="min-w-0">
          <ListSection title="Order size rules">
            <ListRow label="Leverage" hint="Leverage applied to this symbol's positions (SymbolConfig.leverage). Trend daily positions are always 1x.">{leverage !== null ? `${leverage}x` : "---"}</ListRow>
            <ListRow label="Max position" hint="Largest notional the risk manager allows on this symbol">{maxPos !== null ? formatUSD(maxPos) : "---"}</ListRow>
            <ListRow label="Min notional" hint="Smallest order the paper book accepts">{minNotional !== null ? formatUSD(minNotional) : <span title="symbol_config.min_notional_usd needs bridge ≥ 2.16">---</span>}</ListRow>
            <ListRow label="Risk per trade" hint="Fraction of equity risked between entry and stop on each signal">{trading ? formatPct(trading.risk_per_trade_pct, 2) : "---"}</ListRow>
            <ListRow label="Max total exposure" hint="Sum of open notionals / equity allowed">{trading ? formatPct(trading.max_total_exposure_pct, 0) : "---"}</ListRow>
            <ListRow label="Open on this symbol" hint={HINTS.notional}>{formatUSD(openNotional)}</ListRow>
          </ListSection>
        </div>

        <div className="min-w-0">
          <ListSection title="Funding & fees">
            {/* Positive funding is what a LONG pays: rose, like every other cost on these screens. */}
            <ListRow label="Current funding" hint={HINTS.funding}>
              {m.funding === null || m.funding === undefined
                ? <span className="text-text-3">---</span>
                : <span className={cn("num", m.funding > 0 ? "text-rose" : m.funding < 0 ? "text-mint" : "text-text")}>{formatSignedPct(m.funding, 4)}</span>}
            </ListRow>
            <ListRow label="Next payment" hint="Countdown to the venue's next funding settlement">{formatCountdown(m.countdownSec)}</ListRow>
            <ListRow label="Maintenance margin" hint="Margin fraction at which the paper liquidation estimate triggers">{formatPct(mm, 1)}</ListRow>
            <ListRow label="Taker fee (paper)">{taker !== null ? formatPct(taker, 2) : "---"}</ListRow>
            <ListRow label="Maker fee (paper)">{maker !== null ? formatPct(maker, 2) : "---"}</ListRow>
            <ListRow label="Open interest" hint={HINTS.oi}>{m.oi > 0 ? `${formatCompact(m.oi)} ${base}` : "---"}</ListRow>
            <ListRow label={`${m.winLabel} volume`} hint={HINTS.vol24}>{formatCompactUSD(m.volumeUsd)}</ListRow>
          </ListSection>
        </div>

        <div className="min-w-0">
          <ListSection title="Price protection">
            <ListRow label="Slippage model" hint="Paper fills are moved against you by this many basis points of the mark price">{trading ? `${trading.slippage_bps} bps` : "---"}</ListRow>
            <ListRow label="Mark price" hint={HINTS.mark}>{m.mark > 0 ? formatPrice(m.mark) : "---"}</ListRow>
            <ListRow label="Index price" hint={HINTS.index}>{m.index > 0 ? formatPrice(m.index) : "---"}</ListRow>
            <ListRow label="Mark − index" hint="Premium of mark over index — the basis funding corrects">{m.mark > 0 && m.index > 0 ? formatSignedPct((m.mark - m.index) / m.index, 3) : "---"}</ListRow>
            <ListRow label="Best bid / ask">{ob?.best_bid && ob?.best_ask ? `${formatPrice(ob.best_bid)} / ${formatPrice(ob.best_ask)}` : "---"}</ListRow>
            <ListRow label="Spread" hint={HINTS.spread}>{ob ? `${ob.spread.toFixed(2)} (${ob.spread_bps.toFixed(3)} bps)` : "---"}</ListRow>
            <ListRow label="Data age" hint="Seconds since the last tick the engine received for this symbol">{m.dataAgeSec !== null ? `${m.dataAgeSec.toFixed(1)} s` : "---"}</ListRow>
          </ListSection>
        </div>

        <div className="min-w-0">
          <ListSection title="Regime parameters">
            <ListRow label="Detection frame" hint={HINTS.regime}>{m.regimeTf} min bars</ListRow>
            <ListRow label="Min dwell" hint="A new regime must hold this long before it is confirmed">30 min</ListRow>
            <ListRow label="Current regime">{m.regime.replace(/_/g, " ")}</ListRow>
            <ListRow label="Candidate" hint="Regime the detector is leaning to, not yet confirmed">{m.rest?.regime_candidate || "---"}</ListRow>
            <ListRow label="Since">{m.regimeSince > 0 ? new Date(m.regimeSince * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "---"}</ListRow>
          </ListSection>
        </div>

        <div className="min-w-0">
          <ListSection title="Microstructure">
            <ListRow label="VPIN" hint="Volume-synchronised probability of informed trading — order-flow toxicity">
              {micro?.vpin ? <span className={cn(micro.vpin.is_toxic && "text-rose")}>{(micro.vpin.vpin * 100).toFixed(0)}%{micro.vpin.is_toxic ? " · toxic" : ""}</span> : "---"}
            </ListRow>
            <ListRow label="Hawkes" hint="Self-exciting intensity of trade arrivals vs baseline">
              {micro?.hawkes ? <span className={cn(micro.hawkes.is_spike && "text-rose")}>{micro.hawkes.multiplier.toFixed(1)}x{micro.hawkes.is_spike ? " · spike" : ""}</span> : "---"}
            </ListRow>
            <ListRow label="Kyle λ" hint="Price impact per unit of signed volume">{micro?.kyle_lambda ? `${micro.kyle_lambda.lambda_bps.toFixed(2)} bps` : "---"}</ListRow>
            <ListRow label="Adverse selection">{micro?.kyle_lambda ? `${micro.kyle_lambda.adverse_selection_bps.toFixed(2)} bps` : "---"}</ListRow>
            <ListRow label="Risk score" hint="Composite 0–1 microstructure risk used to scale position sizing">
              {typeof micro?.risk_score === "number" ? <span className={cn(micro.risk_score > 0.6 && "text-amber")}>{micro.risk_score.toFixed(2)}</span> : <span title="Microstructure is disabled on this bridge (trading.microstructure_enabled)">off</span>}
            </ListRow>
            {strategies && (
              <ListRow label="Strategy notes">
                <span className="text-[12px] font-medium text-text-2 whitespace-normal text-right">{strategies.map((s) => STRATEGY_DESCRIPTIONS[s]?.split(":")[0] ?? s).join(" · ")}</span>
              </ListRow>
            )}
          </ListSection>
        </div>
      </div>
    </div>
  );
}
