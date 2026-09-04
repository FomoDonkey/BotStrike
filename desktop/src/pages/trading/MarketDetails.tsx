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
import { formatCountdown, PAPER_MAINTENANCE_MARGIN, positionNotional, fundingDirection, fundingMeaning, fundingTone} from "@/lib/market";

const CONFIG_POLL_MS = 60_000;

const ABOUT: Record<string, string> = {
  "BTC-USD": "Bitcoin perpetual. The largest and most liquid crypto market; the bot's regime reference symbol. Priced and executed on Strike.",
  "ETH-USD": "Ether perpetual. Second by liquidity; trades in the same trend and mean-reversion books as BTC. Priced and executed on Strike.",
  "SOL-USD": "Solana perpetual. Higher beta than BTC/ETH — wider ATR stops and smaller sizes. Priced and executed on Strike.",
  "ADA-USD": "Cardano perpetual. Lower price, larger contract sizes; same risk rules as the other symbols. Priced and executed on Strike.",
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
  const vf = m.rest?.venue_filters ?? null;
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
              {/* One venue for both, since 2026-09-04. The daily bars the signal is computed from
                  still come from Binance and Yahoo, because Strike's history is 168 days deep. */}
              <span className="font-medium text-text-2">Live data <span className="text-text font-semibold">{EXCHANGE_LABELS[exchange] ?? exchange}</span></span>
              <span className="font-medium text-text-2">Execution <span className="text-text font-semibold">Strike · paper</span></span>
              <span className="font-medium text-text-2" title="Strike lists 168 days of daily bars for BTC and 19 for the S&P; the Donchian ensemble is fitted on ten years, so its daily history comes from Binance and Yahoo">Signal history <span className="text-text font-semibold">Binance · Yahoo</span></span>
              <span className="font-medium text-text-2">Type <span className="text-text font-semibold">Perpetual · paper</span></span>
              <span className="font-medium text-text-2">Base / quote <span className="text-text font-semibold">{base} / USD</span></span>
              {strategies && strategies.length > 0 ? (
                <span className="inline-flex items-center gap-2 font-medium text-text-2">Strategies {strategies.map((s) => <StrategyTag key={s} strategy={s} />)}</span>
              ) : (
                /* An empty list is a fact, not a gap: only the trend universe is traded, and the
                   panel used to advertise retired strategies instead of saying so (2026-09-04). */
                <span className="font-medium text-text-2" title="Only the daily trend book trades, and only over its own universe. This market can be watched here but nothing will open a position on it.">
                  Strategies <span className="text-text font-semibold">none — not in the trend universe</span>
                </span>
              )}
            </div>
          </ListSection>
        </div>

        <div className="min-w-0">
          <ListSection title="Order size rules">
            <ListRow label="Leverage" hint="Cap on this market's leverage. The daily trend run does not use a fixed figure: it sizes each market by its own volatility (target vol / realised vol) and clamps the result at this cap.">{leverage !== null ? `${leverage}x max` : "---"}</ListRow>
            <ListRow label="Max position" hint="Largest notional the risk manager allows on this symbol. A market with no per-symbol row has no fixed cap — the daily run sizes it by volatility and the account-wide exposure limit binds instead.">
              {maxPos !== null ? formatUSD(maxPos) : <span className="text-text-2">No per-market cap</span>}
            </ListRow>
            <ListRow label="Min notional" hint="Smallest order the paper book accepts">{minNotional !== null ? formatUSD(minNotional) : <span title="symbol_config.min_notional_usd needs bridge ≥ 2.16">---</span>}</ListRow>
            <ListRow label="Risk per trade" hint="Fraction of equity risked between entry and stop on each signal">{trading ? formatPct(trading.risk_per_trade_pct, 2) : "---"}</ListRow>
            <ListRow label="Max total exposure" hint="Sum of open notionals / equity allowed">{trading ? formatPct(trading.max_total_exposure_pct, 0) : "---"}</ListRow>
            <ListRow label="Open on this symbol" hint={HINTS.notional}>{formatUSD(openNotional)}</ListRow>
          </ListSection>

          {/* The venue's own rules, straight from its exchangeInfo. Nothing read these until
              2026-09-04: the panel showed a hard-coded $20 minimum where Strike asks $10, and the
              cap on a single market order was not on screen at all. */}
          <ListSection title="Venue order rules">
            <ListRow label="Min notional" hint="Smallest order the VENUE accepts">
              {vf?.min_notional ? formatUSD(vf.min_notional) : "---"}
            </ListRow>
            <ListRow label="Tick size" hint="Price increment the venue accepts">
              {vf?.tick_size ?? "---"}
            </ListRow>
            <ListRow label="Step size" hint="Quantity increment the venue accepts">
              {vf?.step_size ?? "---"}
            </ListRow>
            <ListRow label="Min / max size" hint="Quantity bounds on a limit order">
              {vf?.min_qty != null && vf?.max_qty != null ? `${vf.min_qty} / ${formatCompact(vf.max_qty)} ${base}` : "---"}
            </ListRow>
            <ListRow label="Max market order" hint="The largest quantity the venue will fill as a single market order. A position bigger than this has to be worked in pieces">
              {vf?.market_max_qty != null ? `${formatCompact(vf.market_max_qty)} ${base}` : "---"}
            </ListRow>
            <ListRow label="Liquidation fee" hint="Charged by the VENUE on a liquidation, as it publishes it">
              {vf?.liquidation_fee != null ? formatPct(vf.liquidation_fee, 2) : "---"}
            </ListRow>
            <ListRow label="Margin asset">{vf?.margin_asset ?? "---"}</ListRow>
            <ListRow label="Market status">{vf?.status ?? "---"}</ListRow>
          </ListSection>
        </div>

        <div className="min-w-0">
          <ListSection title="Funding & fees">
            {/* Positive funding is what a LONG pays: rose, like every other cost on these screens. */}
            <ListRow label="Current funding" hint={HINTS.funding}>
              {m.funding === null || m.funding === undefined
                ? <span className="text-text-3">---</span>
                : <span className={cn("num", fundingTone(m.funding) === "mint" ? "text-mint" : fundingTone(m.funding) === "rose" ? "text-rose" : "text-text")}
                        title={`${fundingDirection(m.funding)} — ${fundingMeaning(m.funding)}`}>{formatSignedPct(m.funding, 4)}</span>}
            </ListRow>
            <ListRow label="Direction" hint="Who pays whom at the next settlement, in the venue's own words">
              <span className="font-semibold">{fundingDirection(m.funding)}</span>
              <span className="text-text-2 font-medium"> · {fundingMeaning(m.funding)}</span>
            </ListRow>
            <ListRow label="Next payment" hint="Countdown to the venue's next funding settlement">{formatCountdown(m.countdownSec)}</ListRow>
            <ListRow label="Maintenance margin" hint="Margin fraction at which the paper liquidation estimate triggers">{formatPct(mm, 1)}</ListRow>
            {/* The VENUE's published schedule, not a guess: tier 0 of
                docs.strikefinance.org/perpetuals/trading-fees. A negative maker fee is a rebate. */}
            <ListRow label="Taker fee" hint="Strike's published taker fee for tier 0 ($0-$100K of 30-day volume), charged on fill notional. Tiers are recalculated daily at 00:05 UTC and fall to 0.028 % above $200M of volume">
              {taker !== null ? formatPct(taker, 3) : "---"}
            </ListRow>
            <ListRow label="Maker fee" hint="Strike tier 0 pays a REBATE to makers: a negative number here means the venue credits you on eligible maker fills, it is not a cost. The daily trend run crosses the spread, so in practice it pays the taker fee above">
              {maker !== null
                ? <span className={maker < 0 ? "text-mint" : undefined}>
                    {formatPct(maker, 3)}{maker < 0 ? " rebate" : ""}
                  </span>
                : "---"}
            </ListRow>
            <ListRow label="Open interest" hint={HINTS.oi}>{m.rest && typeof m.rest.open_interest === "number" ? `${formatCompact(m.oi)} ${base}` : "---"}</ListRow>
            <ListRow label={`${m.winLabel} volume`} hint={HINTS.vol24}>{m.statsMissing ? <span className="text-text-2">The venue publishes none</span> : formatCompactUSD(m.volumeUsd)}</ListRow>
          </ListSection>
        </div>

        <div className="min-w-0">
          <ListSection title="Price protection">
            {/* Per market, not one number for all of them: BTC's book is 0.23 bps wide and gold's
                is 8 (measured on the venue, 2026-09-04). */}
            <ListRow label="Slippage model" hint="Paper fills are moved against you by this many basis points: half this market's own measured spread on the venue, floored at the configured default">
              {typeof sc?.slippage_bps === "number" ? `${sc.slippage_bps} bps` : trading ? `${trading.slippage_bps} bps` : "---"}
            </ListRow>
            <ListRow label="Mark price" hint={HINTS.mark}>{m.mark > 0 ? formatPrice(m.mark) : "---"}</ListRow>
            <ListRow label="Index price" hint={HINTS.index}>{m.index > 0 ? formatPrice(m.index) : "---"}</ListRow>
            <ListRow label="Mark − index" hint="Premium of mark over index — the basis funding corrects">{m.mark > 0 && m.index > 0 ? formatSignedPct((m.mark - m.index) / m.index, 3) : "---"}</ListRow>
            {/* Strike's book, which is the one an order crosses — not the streamed reference feed's.
                Quoting Binance here said BTC cost 0.012 bps to cross where Strike's own screen says
                0.09, and ADA 4.5 where Strike is 6.3 (audit 2026-09-04). */}
            <ListRow label="Best bid / ask" hint="Top of the venue's own book">
              {m.rest?.best_bid && m.rest?.best_ask ? `${formatPrice(m.rest.best_bid)} / ${formatPrice(m.rest.best_ask)}` : "---"}
            </ListRow>
            <ListRow label="Spread" hint={HINTS.spread}>
              {m.rest?.best_bid && m.rest?.best_ask
                ? `${(m.rest.best_ask - m.rest.best_bid).toFixed(4)} (${(m.spreadBps ?? 0).toFixed(3)} bps)`
                : m.spreadBps !== null ? `${m.spreadBps.toFixed(3)} bps (measured median)` : "---"}
            </ListRow>
            <ListRow label="Live book spread" hint={`Top of the streamed book from ${EXCHANGE_LABELS[exchange] ?? exchange}, which is the same venue the order goes to. It moves faster than the measured median above; both describe the same book`}>
              {ob ? `${ob.spread_bps.toFixed(3)} bps` : m.feedSpreadBps !== null ? `${m.feedSpreadBps.toFixed(3)} bps` : "---"}
            </ListRow>
            <ListRow label="Venue quote age" hint="Seconds since the bridge last refreshed this market from the venue">{m.dataAgeSec !== null ? `${m.dataAgeSec.toFixed(1)} s` : "---"}</ListRow>
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
