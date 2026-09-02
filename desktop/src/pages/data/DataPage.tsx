import { useEffect, useState } from "react";
import { useShallow } from "zustand/shallow";
import { motion } from "framer-motion";
import { GlassPanel } from "@/components/shared/GlassPanel";
import { useSystemStore } from "@/stores/systemStore";
import { useMarketStore } from "@/stores/marketStore";
import { PulsingDot } from "@/components/shared/PulsingDot";
import { api, type DatasetInfo } from "@/lib/api";
import { WS_CHANNEL_LIST } from "@/lib/constants";
import { Database, Wifi, HardDrive, Calendar, BarChart3 } from "lucide-react";
import { cn, formatInt } from "@/lib/utils";

export function DataPage() {
  const system = useSystemStore(useShallow((s) => ({
    wsConnected: s.wsConnected,
    bridgeConnected: s.bridgeConnected,
    openChannels: s.openChannels,
  })));
  const prices = useMarketStore(useShallow((s) => s.prices));
  const [catalog, setCatalog] = useState<DatasetInfo[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    api.dataCatalog().then((data) => {
      if (cancelled) return;
      if (data?.datasets) setCatalog(data.datasets);
      setLoading(false);
    }).catch(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  const tickCounts = Object.entries(prices).map(([sym, price]) => ({
    symbol: sym,
    lastPrice: price,
    status: price > 0 ? "streaming" : "waiting",
  }));

  return (
    <motion.div
      className="space-y-4"
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
    >
      <h1 className="text-lg font-semibold text-text-primary flex items-center gap-2">
        <Database className="w-5 h-5 text-accent" /> Market Data
      </h1>

      {/* Live Feeds */}
      <GlassPanel className="p-4 sm:p-5">
        <h3 className="text-xs text-text-secondary uppercase tracking-wider mb-4 flex items-center gap-2">
          <Wifi className="w-3 h-3" /> Live Data Feeds
        </h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
          {tickCounts.length > 0 ? tickCounts.map((t) => (
            <div key={t.symbol} className="p-3 rounded-lg bg-white/[0.02] flex items-center justify-between gap-2 min-w-0">
              <div className="flex items-center gap-2 min-w-0">
                <PulsingDot active={t.status === "streaming"} />
                <div className="min-w-0">
                  <p className="font-mono text-sm font-semibold text-text-primary">{t.symbol}</p>
                  <p className="text-[10px] text-text-muted">{t.status === "streaming" ? "Live" : "Connecting..."}</p>
                </div>
              </div>
              <p className="font-mono text-sm text-accent whitespace-nowrap">${t.lastPrice.toLocaleString("en-US", { minimumFractionDigits: 2 })}</p>
            </div>
          )) : (
            <div className="col-span-full text-center py-4 text-text-muted text-sm">
              <PulsingDot active={system.wsConnected} className="mx-auto mb-2" />
              {system.wsConnected ? "Receiving data..." : "No data feeds — bridge not connected"}
            </div>
          )}
        </div>
      </GlassPanel>

      {/* Connection Status */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <GlassPanel className="p-4 sm:p-5">
          <h3 className="text-xs text-text-secondary uppercase tracking-wider mb-4 flex items-center gap-2">
            <BarChart3 className="w-3 h-3" /> Stream Status
          </h3>
          <div className="space-y-2">
            {WS_CHANNEL_LIST.map((ch) => {
              const open = system.openChannels.includes(ch);
              return (
                <div key={ch} className="flex items-center justify-between text-xs">
                  <span className="text-text-secondary font-mono">ws/{ch}</span>
                  <span className={cn(
                    "px-2 py-0.5 rounded text-[10px] font-mono",
                    open ? "bg-profit/10 text-profit" : system.bridgeConnected ? "bg-warning/10 text-warning" : "bg-white/5 text-text-muted"
                  )}>
                    {open ? "SUBSCRIBED" : system.bridgeConnected ? "RECONNECTING" : "PENDING"}
                  </span>
                </div>
              );
            })}
          </div>
        </GlassPanel>

        {/* Data Catalog */}
        <GlassPanel className="p-4 sm:p-5 min-w-0">
          <h3 className="text-xs text-text-secondary uppercase tracking-wider mb-4 flex items-center gap-2">
            <HardDrive className="w-3 h-3" /> Local Data Catalog
          </h3>
          {loading ? (
            <p className="text-text-muted text-xs">Loading catalog...</p>
          ) : catalog.length > 0 ? (
            <div className="overflow-x-auto -mx-1 px-1">
              <table className="w-full min-w-[420px] text-xs">
                <thead>
                  <tr className="text-text-muted border-b border-white/5">
                    <th className="text-left py-1 font-normal">Dataset</th>
                    <th className="text-right font-normal">Rows</th>
                    <th className="text-right font-normal">Size</th>
                    <th className="text-right font-normal">Range</th>
                  </tr>
                </thead>
                <tbody>
                  {catalog.slice(0, 20).map((d, i) => (
                    <tr key={`${d.symbol}-${d.type}-${i}`} className="border-b border-white/[0.02]">
                      <td className="py-1.5">
                        <span className="font-mono text-text-primary">{d.symbol}</span>
                        <span className="text-text-muted ml-2">{d.type}</span>
                      </td>
                      <td className="text-right font-mono text-text-secondary">{formatInt(d.records ?? 0)}</td>
                      <td className="text-right font-mono text-text-muted">{typeof d.size_mb === "number" ? `${d.size_mb.toFixed(1)} MB` : "---"}</td>
                      <td className="text-right font-mono text-text-muted whitespace-nowrap">{d.date_range || "---"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="text-center py-4">
              <Calendar className="w-8 h-8 text-text-muted/30 mx-auto mb-2" />
              <p className="text-text-muted text-xs">
                No catalog data. Run the data collector to build local datasets.
              </p>
            </div>
          )}
        </GlassPanel>
      </div>
    </motion.div>
  );
}
