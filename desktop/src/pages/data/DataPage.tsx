import { useMemo } from "react";
import { useShallow } from "zustand/shallow";
import { Database } from "lucide-react";
import { useSystemStore } from "@/stores/systemStore";
import { useMarketStore } from "@/stores/marketStore";
import { useNow } from "@/hooks/useNow";
import { useEndpoint } from "@/hooks/useEndpoint";
import { api, type DatasetInfo } from "@/lib/api";
import { SYMBOLS, WS_CHANNEL_LIST } from "@/lib/constants";
import { formatAge, formatInt, formatPrice } from "@/lib/utils";
import { Panel, PanelHeader, EmptyState } from "@/components/ui/Panel";
import { Chip, StatusChip } from "@/components/ui/Chip";
import { ListRow } from "@/components/ui/ListRow";
import { DataTable, type Column } from "@/components/ui/DataTable";
import { PulsingDot } from "@/components/shared/PulsingDot";

/** Data (spec §3.6): live feeds, stream status, local catalog — restyle only. */
export function DataPage() {
  const now = useNow();
  const system = useSystemStore(useShallow((s) => ({ wsConnected: s.wsConnected, bridgeConnected: s.bridgeConnected, openChannels: s.openChannels })));
  const prices = useMarketStore(useShallow((s) => s.prices));
  const candles = useMarketStore(useShallow((s) => s.candles));
  const lastTickAt = useMarketStore((s) => s.lastTickAt);
  const catalog = useEndpoint(() => api.dataCatalog(), 60_000);
  const rows = useMemo<DatasetInfo[]>(() => {
    const d = catalog.data?.datasets;
    if (!d) return [];
    return Array.isArray(d) ? d : Object.values(d);
  }, [catalog.data]);
  const age = lastTickAt > 0 ? (now - lastTickAt) / 1000 : null;

  const columns: Column<DatasetInfo>[] = [
    { id: "dataset", label: "Dataset", align: "l", sortValue: (d) => d.symbol, render: (d) => <span className="inline-flex items-center gap-2"><span className="font-semibold">{d.symbol}</span><Chip tone="neutral" size="xs" uppercase={false}>{d.type ?? d.data_type ?? "---"}{d.timeframe ? ` ${d.timeframe}` : ""}</Chip></span> },
    { id: "rows", label: "Rows", sortValue: (d) => d.records ?? d.total_rows ?? 0, render: (d) => <span className="num">{formatInt(d.records ?? d.total_rows ?? 0)}</span> },
    { id: "size", label: "Size", sortValue: (d) => d.size_mb ?? 0, render: (d) => <span className="num">{typeof d.size_mb === "number" ? `${d.size_mb.toFixed(2)} MB` : "---"}</span> },
    { id: "range", label: "Range", align: "l", render: (d) => d.date_range || (d.date_start ? `${d.date_start} → ${d.date_end ?? "…"}` : "---") },
  ];

  return (
    <div className="flex flex-col gap-3 p-3 sm:p-4 min-w-0">
      <h1 className="text-[18px] font-semibold text-text flex items-center gap-2"><Database className="w-5 h-5 text-mint" /> Data</h1>

      <Panel>
        <PanelHeader title="Live feeds" right={<span className="text-[12px] font-medium text-text-2">last tick <span className="num text-text font-semibold">{formatAge(age)}</span> ago</span>} />
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-2 p-3">
          {SYMBOLS.map((sym) => {
            const price = prices[sym] || 0;
            const n = candles[sym]?.length ?? 0;
            return (
              <div key={sym} className="rounded-[6px] bg-panel-2 px-3 py-2 flex items-center justify-between gap-2 min-w-0">
                <div className="flex items-center gap-2 min-w-0">
                  <PulsingDot active={price > 0} inactive="amber" />
                  <div className="min-w-0">
                    <p className="text-[13px] font-semibold text-text">{sym}</p>
                    <p className="text-[12px] font-medium text-text-2">{price > 0 ? `Live · ${n} 1m bars` : system.bridgeConnected ? "Waiting for ticks" : "Bridge offline"}</p>
                  </div>
                </div>
                <p className="num text-[13px] font-semibold text-text whitespace-nowrap">{price > 0 ? formatPrice(price) : "---"}</p>
              </div>
            );
          })}
        </div>
      </Panel>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <Panel>
          <PanelHeader title="Stream status" />
          <div className="px-4 py-2">
            {WS_CHANNEL_LIST.map((ch) => {
              const open = system.openChannels.includes(ch);
              return (
                <ListRow key={ch} label={`ws/${ch}`}>
                  <StatusChip status={open ? "online" : system.bridgeConnected ? "warning" : "disabled"} label={open ? "subscribed" : system.bridgeConnected ? "reconnecting" : "pending"} size="xs" />
                </ListRow>
              );
            })}
            <ListRow label="Market feed"><StatusChip status={system.wsConnected ? "online" : "offline"} label={system.wsConnected ? "connected" : "disconnected"} size="xs" /></ListRow>
          </div>
        </Panel>

        <Panel className="flex flex-col overflow-hidden">
          <PanelHeader title="Local data catalog" right={<span className="text-[12px] font-medium text-text-2">{rows.length} dataset{rows.length === 1 ? "" : "s"}</span>} />
          {!catalog.loaded ? (
            <EmptyState>Loading catalog…</EmptyState>
          ) : (
            <DataTable columns={columns} rows={rows} rowKey={(d, i) => `${d.symbol}-${d.type ?? d.data_type}-${i}`} minWidth="480px" emptyText="No catalog data" emptySub="Run the data collector to build local datasets" limit={40} />
          )}
        </Panel>
      </div>
    </div>
  );
}
