import { useExchangeStore, type ExchangeId } from "@/stores/exchangeStore";
import { cn } from "@/lib/utils";
import { Chip } from "@/components/ui/Chip";

// Strike first: it is the venue the bot runs on (settings.exchange_venue) and the one the engine
// reports on /api/health. It was missing from this list altogether (2026-09-08). The fee figures
// are the venues' published tier-0 schedules, taker per side.
const EXCHANGES: { id: ExchangeId; name: string; fees: string; desc: string }[] = [
  { id: "strike", name: "Strike Finance", fees: "taker 0.05 % · maker −0.005 %", desc: "Perpetuals CLOB · the bot's venue · 31 markets incl. metals, energy, indices" },
  { id: "binance", name: "Binance", fees: "taker 0.04 %", desc: "Centralized · High liquidity · API keys" },
  { id: "hyperliquid", name: "Hyperliquid", fees: "taker 0.035 %", desc: "Decentralized · Wallet auth" },
];

export function ExchangeSelector() {
  const { exchange, setExchange } = useExchangeStore();
  return (
    <div className="flex flex-col sm:flex-row gap-3">
      {EXCHANGES.map((ex) => {
        const active = exchange === ex.id;
        return (
          <button
            key={ex.id}
            type="button"
            aria-pressed={active}
            onClick={() => setExchange(ex.id)}
            className={cn("flex-1 p-4 rounded-lg border text-left transition-colors", active ? "border-mint bg-mint-soft" : "border-hairline bg-panel-2 hover:bg-hover")}
          >
            <div className="flex items-center justify-between mb-1">
              <span className="text-[15px] font-semibold text-text">{ex.name}</span>
              <Chip tone={active ? "mint" : "neutral"} size="xs" uppercase={false}>{ex.fees}</Chip>
            </div>
            <p className="text-[12.5px] font-medium text-text-2">{ex.desc}</p>
          </button>
        );
      })}
    </div>
  );
}
