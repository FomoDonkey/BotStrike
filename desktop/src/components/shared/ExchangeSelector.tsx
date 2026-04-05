import { useExchangeStore, type ExchangeId } from "@/stores/exchangeStore";
import { cn } from "@/lib/utils";

const EXCHANGES: { id: ExchangeId; name: string; fees: string; color: string; desc: string }[] = [
  {
    id: "binance",
    name: "Binance",
    fees: "8 bps RT",
    color: "#F0B90B",
    desc: "Centralized · High liquidity · API keys",
  },
  {
    id: "hyperliquid",
    name: "Hyperliquid",
    fees: "3-5 bps RT",
    color: "#4AE3B5",
    desc: "Decentralized · Lower fees · Wallet auth",
  },
];

export function ExchangeSelector() {
  const { exchange, setExchange } = useExchangeStore();

  return (
    <div className="flex gap-4">
      {EXCHANGES.map((ex) => {
        const active = exchange === ex.id;
        return (
          <button
            key={ex.id}
            onClick={() => setExchange(ex.id)}
            className={cn(
              "flex-1 p-5 rounded-2xl border-2 transition-all duration-200 text-left",
              active
                ? "border-opacity-100 bg-opacity-10 scale-[1.02]"
                : "border-white/5 bg-white/[0.02] hover:bg-white/[0.04] hover:border-white/10",
            )}
            style={active ? {
              borderColor: ex.color,
              backgroundColor: `${ex.color}10`,
            } : undefined}
          >
            <div className="flex items-center justify-between mb-2">
              <span
                className="text-lg font-bold"
                style={{ color: active ? ex.color : undefined }}
              >
                {ex.name}
              </span>
              <span className={cn(
                "text-xs font-mono px-2 py-0.5 rounded-full",
                active ? "bg-profit/10 text-profit" : "bg-white/5 text-text-muted",
              )}>
                {ex.fees}
              </span>
            </div>
            <p className="text-xs text-text-muted">{ex.desc}</p>
          </button>
        );
      })}
    </div>
  );
}
