import { useEffect, useRef, useState } from "react";
import { useShallow } from "zustand/shallow";
import { Monitor, Play, Square, RefreshCw, Trash2 } from "lucide-react";
import { Panel, PanelHeader, EmptyState } from "@/components/ui/Panel";
import { ListRow, ListSection } from "@/components/ui/ListRow";
import { Chip, StatusChip } from "@/components/ui/Chip";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/Modal";
import { PulsingDot } from "@/components/shared/PulsingDot";
import { useSystemStore } from "@/stores/systemStore";
import { useMarketStore } from "@/stores/marketStore";
import { useExchangeStore } from "@/stores/exchangeStore";
import { useBridgeConfig } from "@/lib/config";
import { useEndpoint } from "@/hooks/useEndpoint";
import { useNow } from "@/hooks/useNow";
import { useBotControl, type BotAction } from "@/components/layout/useBotControl";
import { api } from "@/lib/api";
import { EXCHANGE_LABELS } from "@/lib/constants";
import { cn, formatAge, formatDateTime, formatDuration, formatLocalDateTime, formatMoney, formatSignedMoney } from "@/lib/utils";

/** System (spec §3.8): health, ops monitor, feed status, version, uptime, Telegram, recent logs. */
/** Never print "[object Object]": the monitor can add nested facts at any time (2026-09-03). */
function factValue(v: unknown): string {
  if (v === null || v === undefined || v === "") return "---";
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (typeof v === "string" && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(v)) {
    const d = new Date(v.endsWith("Z") || /[+-]\d{2}:\d{2}$/.test(v) ? v : `${v}Z`);
    if (!Number.isNaN(d.getTime())) return formatDateTime(d.getTime());   // one date format on the page
  }
  if (typeof v === "object") {
    const entries = Object.entries(v as Record<string, unknown>);
    return entries.length ? entries.map(([k, x]) => `${k.replace(/_/g, " ")} ${String(x)}`).join(" · ") : "none";
  }
  return String(v);
}

export function SystemPage() {
  const now = useNow();
  const system = useSystemStore(useShallow((s) => ({ engineRunning: s.engineRunning, mode: s.mode, uptimeSec: s.uptimeSec, wsConnected: s.wsConnected, clientsConnected: s.clientsConnected, bridgeConnected: s.bridgeConnected, openChannels: s.openChannels })));
  const logs = useSystemStore((s) => s.logs);
  const lastTickAt = useMarketStore((s) => s.lastTickAt);
  const { url: bridgeUrl, mode: bridgeMode } = useBridgeConfig();
  const exchange = useExchangeStore((s) => s.exchange);
  const health = useEndpoint(() => api.health(), 5_000, bridgeUrl);
  const status = useEndpoint(() => api.botStatus(), 5_000, bridgeUrl);
  const ops = useEndpoint(() => api.ops(), 30_000, bridgeUrl);
  const { canControl, disabledReason, busy, run } = useBotControl();
  const [confirm, setConfirm] = useState<BotAction | null>(null);
  const logBoxRef = useRef<HTMLDivElement>(null);

  // Scroll the log box itself — scrollIntoView also scrolled the page's <main> on every load.
  useEffect(() => {
    const timer = setTimeout(() => {
      const el = logBoxRef.current;
      if (el) el.scrollTop = el.scrollHeight;
    }, 100);
    return () => clearTimeout(timer);
  }, [logs.length]);

  const h = health.data;
  const feedAge = lastTickAt > 0 ? (now - lastTickAt) / 1000 : null;
  const opsData = ops.data && ops.data.available ? ops.data : null;
  const facts = opsData?.facts ?? {};
  const journal = opsData?.journal_15 ?? {};

  return (
    <div className="flex flex-col gap-3 p-3 sm:p-4 min-w-0 lg:h-full">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <h1 className="text-[18px] font-semibold text-text flex items-center gap-2"><Monitor className="w-5 h-5 text-mint" /> System</h1>
        <div className="flex items-center gap-2">
          <Button variant="primary" size="sm" icon={<Play className="w-3.5 h-3.5" />} disabled={!canControl || system.engineRunning} title={disabledReason} loading={busy === "start"} onClick={() => void run("start")}>Start · paper</Button>
          <Button variant="secondary" size="sm" icon={<Square className="w-3.5 h-3.5 text-rose" />} disabled={!canControl || !system.engineRunning} title={disabledReason} loading={busy === "stop"} onClick={() => setConfirm("stop")}>Stop</Button>
          <Button variant="secondary" size="sm" icon={<RefreshCw className="w-3.5 h-3.5" />} disabled={!canControl} title={disabledReason} loading={busy === "restart"} onClick={() => setConfirm("restart")}>Restart</Button>
        </div>
      </div>
      {!canControl && <p className="text-[12.5px] font-medium text-amber -mt-1">{disabledReason}</p>}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
        <Panel accent={system.engineRunning} danger={!system.bridgeConnected}>
          <PanelHeader title="Health" right={<StatusChip status={!system.bridgeConnected ? "offline" : system.engineRunning ? "running" : "stopped"} size="xs" />} />
          <ListSection first>
            <ListRow label="Engine"><span className={cn("inline-flex items-center gap-2", system.engineRunning ? "text-mint" : "text-rose")}><PulsingDot active={system.engineRunning} />{system.engineRunning ? "Running" : "Stopped"}</span></ListRow>
            <ListRow label="Bridge status">{h ? <span className={cn(h.status === "ok" ? "text-mint" : "text-amber")}>{h.status}</span> : health.error ? <span className="text-rose">unreachable</span> : "---"}</ListRow>
            <ListRow label="Mode"><StatusChip status={system.mode} size="xs" /></ListRow>
            <ListRow label="Exchange">{EXCHANGE_LABELS[status.data?.exchange ?? exchange] ?? exchange}</ListRow>
            <ListRow label="Uptime">{formatDuration(system.uptimeSec)}</ListRow>
            <ListRow label="Version">{h?.version ? `v${h.version}` : "---"} <span className="text-text-2 font-medium">· UI {__APP_VERSION__}</span></ListRow>
            <ListRow label="WS clients">{system.clientsConnected}</ListRow>
            {status.data && <ListRow label="Equity">{formatMoney(status.data.equity ?? 0)} <span className={cn("ml-1", (status.data.pnl ?? 0) > 0 ? "text-mint" : (status.data.pnl ?? 0) < 0 ? "text-rose" : "")}>{formatSignedMoney(status.data.pnl ?? 0)}</span></ListRow>}
            <ListRow label="Telegram">{h ? (typeof h.telegram_failures === "number" ? <span className={cn(h.telegram_failures > 0 && "text-amber")}>{h.telegram_failures === 0 ? "ok · 0 failures" : `${h.telegram_failures} failures`}</span> : "not reported") : "---"}</ListRow>
            <ListRow label="Trend daily">{h ? (h.trend_daily_enabled ? "enabled" : "disabled") : "---"}</ListRow>
            <ListRow label="Microstructure">{h ? (h.microstructure_enabled ? "enabled" : "disabled") : "---"}</ListRow>
          </ListSection>
        </Panel>

        <Panel>
          <PanelHeader title="Ops monitor" right={opsData ? <Chip tone={(opsData.alerts?.length ?? 0) > 0 ? "amber" : "mint"} size="xs">{(opsData.alerts?.length ?? 0) > 0 ? `${opsData.alerts?.length} alert${opsData.alerts?.length === 1 ? "" : "s"}` : "all clear"}</Chip> : undefined} />
          {ops.missing ? (
            <EmptyState sub="GET /api/ops needs bridge ≥ 2.16">Ops monitor not available on this bridge</EmptyState>
          ) : !ops.loaded ? (
            <EmptyState>Loading ops state…</EmptyState>
          ) : !opsData ? (
            <EmptyState sub="The monitor timer runs on the server every 15 min (desktop / local bridges have none)">Ops monitor has not run yet</EmptyState>
          ) : (
            <>
              <ListSection first>
                <ListRow label="Last check">{formatLocalDateTime(opsData.last_check ?? null)}</ListRow>
                {opsData.next_timer && <ListRow label="Next timer">{formatLocalDateTime(opsData.next_timer)}</ListRow>}
                <ListRow label="Daily summary">{opsData.summary_sent ? "sent" : "pending"}{opsData.state?.last_summary_date ? <span className="text-text-2 font-medium"> · {opsData.state.last_summary_date}</span> : null}</ListRow>
                {Object.entries(facts).map(([k, v]) => <ListRow key={k} label={k.replace(/_/g, " ")}>{factValue(v)}</ListRow>)}
              </ListSection>
              {Object.keys(journal).length > 0 && (
                <ListSection title="Journal · last 15 min">
                  {Object.entries(journal).filter(([k, v]) => k !== "available" && !(k === "first_error" && !v)).map(([k, v]) => <ListRow key={k} label={k.replace(/_/g, " ")}><span className={cn(k === "errors" && Number(v) > 0 && "text-rose")}>{typeof v === "boolean" ? (v ? "yes" : "no") : String(v)}</span></ListRow>)}
                </ListSection>
              )}
              {(opsData.alerts?.length ?? 0) > 0 && (
                <ListSection title="Alerts">
                  {opsData.alerts!.map((a) => <p key={a.key} className="text-[12.5px] font-medium text-amber leading-snug py-1 break-words">{a.text}</p>)}
                </ListSection>
              )}
            </>
          )}
        </Panel>

        <Panel>
          <PanelHeader title="Connections" />
          <ListSection first>
            <ListRow label={`Bridge (${bridgeMode})`}><StatusChip status={system.bridgeConnected ? "online" : "offline"} size="xs" /></ListRow>
            <ListRow label="URL"><span className="break-all whitespace-normal text-right">{bridgeUrl.replace(/^https?:\/\//, "")}</span></ListRow>
            <ListRow label="WS channels">{system.openChannels.length}/5 <span className="text-text-2 font-medium">· {system.openChannels.join(", ") || "none"}</span></ListRow>
            <ListRow label={`${EXCHANGE_LABELS[exchange] ?? exchange} feed`}><StatusChip status={system.wsConnected || (feedAge !== null && feedAge < 30) ? "online" : "offline"} size="xs" /></ListRow>
            <ListRow label="Last tick">{formatAge(feedAge)}{feedAge !== null ? " ago" : ""}</ListRow>
            <ListRow label="Endpoint">{exchange === "hyperliquid" ? "api.hyperliquid.xyz" : "fstream.binance.com"}</ListRow>
            <ListRow label="Framework">Tauri v2 · React 19</ListRow>
          </ListSection>
        </Panel>
      </div>

      <Panel className="flex flex-col flex-1 min-h-[280px] lg:min-h-0 overflow-hidden">
        <PanelHeader title="Recent log lines" right={<Button variant="ghost" size="xs" icon={<Trash2 className="w-3.5 h-3.5" />} onClick={() => useSystemStore.setState({ logs: [] })}>Clear</Button>} />
        <div ref={logBoxRef} className="flex-1 overflow-auto p-3 font-mono text-[12px] leading-5">
          {logs.length === 0 ? (
            <p className="text-[12.5px] font-medium text-text font-sans">Waiting for log output…</p>
          ) : (
            logs.map((log, i) => (
              <div key={i} className="flex gap-2">
                <span className="text-text-2 shrink-0">{new Date(log.timestamp * 1000).toLocaleTimeString("en-US", { hour12: false })}</span>
                <span className={cn("shrink-0 w-14 font-semibold", log.level === "error" ? "text-rose" : log.level === "warn" || log.level === "warning" ? "text-amber" : "text-text-2")}>[{log.level}]</span>
                <span className="text-text break-all">{log.message}</span>
              </div>
            ))
          )}
        </div>
      </Panel>

      <ConfirmDialog
        open={confirm !== null}
        onClose={() => setConfirm(null)}
        onConfirm={() => { const a = confirm; setConfirm(null); if (a) void run(a); }}
        title={confirm === "stop" ? "Stop the bot?" : "Restart the engine?"}
        body={confirm === "stop" ? "The engine stops trading. Open paper positions stay in the book until the engine runs again." : "The engine stops and starts again with the same mode and exchange. Positions are kept."}
        confirmLabel={confirm === "stop" ? "Stop bot" : "Restart"}
        danger={confirm === "stop"}
        busy={busy !== null}
      />
    </div>
  );
}
