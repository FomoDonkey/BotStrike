import { useEffect, useState } from "react";
import { api, type SymbolConfig } from "@/lib/api";
import { useMarketStore } from "@/stores/marketStore";
import { useMicroStore } from "@/stores/microStore";
import { useExchangeStore } from "@/stores/exchangeStore";
import { useNow } from "@/hooks/useNow";
import { Hint } from "@/components/shared/Hint";
import { HINTS } from "@/lib/hints";
import { EXCHANGE_LABELS, SYMBOL_LABELS } from "@/lib/constants";
import { cn, formatCompact, formatCompactUSD, formatPrice, formatSignedPct, formatUSD } from "@/lib/utils";
import { formatCountdown, fundingCountdownSec } from "@/lib/market";

/** "Details" tab of the chart area: contract facts, venue stats, microstructure and symbol config. */
export function MarketDetails({ symbol }: { symbol: string }) {
  const now = useNow();
  const info = useMarketStore((s) => s.marketInfo[symbol]);
  const ob = useMarketStore((s) => s.orderbooks[symbol]);
  const price = useMarketStore((s) => s.prices[symbol] || 0);
  const micro = useMicroStore((s) => s.snapshots[symbol]);
  const exchange = useExchangeStore((s) => s.exchange);
  const [cfg, setCfg] = useState<SymbolConfig | null>(null);
  const [cfgErr, setCfgErr] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api.config()
      .then((c) => { if (!cancelled) setCfg(c.symbols.find((s) => s.symbol === symbol) ?? null); })
      .catch(() => { if (!cancelled) setCfgErr(true); });
    return () => { cancelled = true; };
  }, [symbol]);

  const countdown = typeof info?.funding_countdown_sec === "number" && info.updated
    ? Math.max(0, info.funding_countdown_sec - (now / 1000 - info.updated))
    : fundingCountdownSec(now);

  const sections: { title: string; rows: { k: string; v: React.ReactNode; hint?: string }[] }[] = [
    {
      title: "Contract",
      rows: [
        { k: "Symbol", v: symbol },
        { k: "Base / quote", v: `${SYMBOL_LABELS[symbol] ?? symbol.split("-")[0]} / USD` },
        { k: "Venue", v: EXCHANGE_LABELS[exchange] ?? exchange },
        { k: "Type", v: "Perpetual (paper)" },
        { k: "Last price", v: price > 0 ? formatPrice(price) : "---" },
        { k: "Mark price", v: info?.mark_price ? formatPrice(info.mark_price) : "---", hint: HINTS.mark },
        { k: "Index price", v: info?.index_price ? formatPrice(info.index_price) : "---", hint: HINTS.index },
        { k: "Mark − index", v: info?.mark_price && info?.index_price ? formatSignedPct((info.mark_price - info.index_price) / info.index_price, 3) : "---", hint: "Premium of mark over index — the basis that funding corrects" },
      ],
    },
    {
      title: "Venue stats",
      rows: [
        { k: "Funding rate", v: typeof info?.funding_rate === "number" ? <span className={info.funding_rate > 0 ? "text-profit" : info.funding_rate < 0 ? "text-loss" : ""}>{formatSignedPct(info.funding_rate, 4)}</span> : "---", hint: HINTS.funding },
        { k: "Next funding", v: formatCountdown(countdown), hint: "Countdown to the next 8 h UTC funding mark" },
        { k: "24h volume", v: formatCompactUSD(info?.volume_24h ?? 0), hint: HINTS.vol24 },
        { k: "Open interest", v: info?.open_interest ? `${formatCompact(info.open_interest)} ${SYMBOL_LABELS[symbol] ?? ""}` : "---", hint: HINTS.oi },
        { k: "Best bid / ask", v: ob?.best_bid && ob?.best_ask ? `${formatPrice(ob.best_bid)} / ${formatPrice(ob.best_ask)}` : "---" },
        { k: "Spread", v: ob ? `${ob.spread.toFixed(2)} (${ob.spread_bps.toFixed(3)} bps)` : "---", hint: HINTS.spread },
        { k: "Microprice", v: ob?.microprice ? formatPrice(ob.microprice) : "---", hint: "Size-weighted mid: (bid × askSize + ask × bidSize) / (bidSize + askSize)" },
      ],
    },
    {
      title: "Microstructure",
      rows: [
        { k: "VPIN", v: micro?.vpin ? <span className={cn(micro.vpin.is_toxic && "text-loss")}>{(micro.vpin.vpin * 100).toFixed(0)}%{micro.vpin.is_toxic ? " · toxic" : ""}</span> : "---", hint: "Volume-synchronised probability of informed trading — order-flow toxicity" },
        { k: "Hawkes", v: micro?.hawkes ? <span className={cn(micro.hawkes.is_spike && "text-loss")}>{micro.hawkes.multiplier.toFixed(1)}x{micro.hawkes.is_spike ? " · spike" : ""}</span> : "---", hint: "Self-exciting intensity of trade arrivals vs baseline" },
        { k: "Kyle λ", v: micro?.kyle_lambda ? `${micro.kyle_lambda.lambda_bps.toFixed(2)} bps` : "---", hint: "Price impact per unit of signed volume" },
        { k: "Adverse selection", v: micro?.kyle_lambda ? `${micro.kyle_lambda.adverse_selection_bps.toFixed(2)} bps` : "---" },
        { k: "Risk score", v: typeof micro?.risk_score === "number" ? <span className={cn(micro.risk_score > 0.6 && "text-warning")}>{micro.risk_score.toFixed(2)}</span> : "---", hint: "Composite 0–1 microstructure risk used to scale position sizing" },
      ],
    },
    {
      title: "Symbol config",
      rows: cfg ? [
        { k: "Leverage", v: `${cfg.leverage}x` },
        { k: "Max position", v: formatUSD(cfg.max_position_usd) },
        { k: "VPIN bucket", v: String(cfg.vpin_bucket_size) },
        { k: "VPIN toxic ≥", v: String(cfg.vpin_toxic_threshold) },
        { k: "Hawkes spike ×", v: String(cfg.hawkes_spike_mult) },
        { k: "OBI levels", v: String(cfg.obi_levels) },
      ] : [{ k: cfgErr ? "Config unavailable" : "Loading…", v: "" }],
    },
  ];

  return (
    <div className="flex-1 min-h-0 overflow-auto">
      <div className="grid grid-cols-1 md:grid-cols-2 2xl:grid-cols-4 gap-x-8 px-3 py-2">
        {sections.map((sec) => (
          <div key={sec.title} className="min-w-0">
            <p className="text-[10.5px] uppercase tracking-[0.06em] text-text-muted h-7 flex items-center border-b border-hairline-soft">{sec.title}</p>
            <dl className="kv">
              {sec.rows.map((r) => (
                <div key={r.k} className="contents">
                  <dt>{r.hint ? <Hint title={r.hint}>{r.k}</Hint> : r.k}</dt>
                  <dd>{r.v}</dd>
                </div>
              ))}
            </dl>
          </div>
        ))}
      </div>
    </div>
  );
}
