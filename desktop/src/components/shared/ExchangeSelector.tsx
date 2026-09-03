import { useExchangeStore, type ExchangeId } from "@/stores/exchangeStore";
import { cn } from "@/lib/utils";
import { Chip } from "@/components/ui/Chip";

const EXCHANGES: { id: ExchangeId; name: string; fees: string; desc: string }[] = [
  { id: "binance", name: "Binance", fees: "8 bps RT", desc: "Centralized · High liquidity · API keys" },
  { id: "hyperliquid", name: "Hyperliquid", fees: "3-5 bps RT", desc: "Decentralized · Lower fees · Wallet auth" },
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
