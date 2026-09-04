import { useExchangeStore } from "./exchangeStore";
import { create } from "zustand";
import { HEALTH_STALE_MS, HEALTH_WATCHDOG_TICK_MS, STORE_FLUSH_MS } from "@/lib/constants";

export interface LogEntry {
  timestamp: number;
  level: string;
  message: string;
}

export interface HealthMessage {
  engine_running?: boolean;
  mode?: string;
  uptime_sec?: number;
  ws_connected?: boolean;
  clients_connected?: number;
}

export interface LogMessage {
  timestamp?: number;
  level?: string;
  message?: string;
}

export interface EngineErrorMessage {
  timestamp?: number;
  error?: string;
}

interface SystemState {
  engineRunning: boolean;
  mode: string;
  uptimeSec: number;
  wsConnected: boolean;
  clientsConnected: number;
  bridgeConnected: boolean;
  openChannels: string[]; // WS channels currently open to the bridge
  _lastHealthAt: number;
  logs: LogEntry[];

  onHealth: (data: HealthMessage) => void;
  onLog: (data: LogMessage) => void;
  onEngineError: (data: EngineErrorMessage) => void;
  setBridgeConnected: (v: boolean) => void;
  onChannelStatus: (channel: string, open: boolean) => void;
  resetConnection: () => void;
}

const MAX_LOGS = 200;

// Detect bridge disconnection: if no health message for HEALTH_STALE_MS, mark as disconnected
let _healthWatchdog: ReturnType<typeof setInterval> | null = null;

function startHealthWatchdog() {
  if (_healthWatchdog) return;
  _healthWatchdog = setInterval(() => {
    const state = useSystemStore.getState();
    if (state.bridgeConnected && Date.now() - state._lastHealthAt > HEALTH_STALE_MS) {
      useSystemStore.setState({ bridgeConnected: false, engineRunning: false });
    }
  }, HEALTH_WATCHDOG_TICK_MS);
}

// ── Log batching ─────────────────────────────────────────────────
// The bridge replays its recent log ring on connect (dozens of lines in one burst); each line
// used to be a synchronous set() → one React commit per line. Queue and flush every STORE_FLUSH_MS.

const pendingLogs: LogEntry[] = [];
let logFlushTimer: ReturnType<typeof setTimeout> | null = null;

function queueLog(entry: LogEntry) {
  pendingLogs.push(entry);
  if (pendingLogs.length > MAX_LOGS) pendingLogs.splice(0, pendingLogs.length - MAX_LOGS);
  if (logFlushTimer) return;
  logFlushTimer = setTimeout(flushLogs, STORE_FLUSH_MS);
}

function flushLogs() {
  logFlushTimer = null;
  if (pendingLogs.length === 0) return;
  const batch = pendingLogs.splice(0, pendingLogs.length);
  useSystemStore.setState((s) => ({ logs: [...s.logs, ...batch].slice(-MAX_LOGS) }));
}

export const useSystemStore = create<SystemState>((set) => {
  startHealthWatchdog();
  return {
    engineRunning: false,
    mode: "paper",
    uptimeSec: 0,
    wsConnected: false,
    clientsConnected: 0,
    bridgeConnected: false,
    openChannels: [],
    _lastHealthAt: 0,
    logs: [],

    onHealth: (data) => {
      // the venue is whatever the engine reports, never what this browser last remembered
      useExchangeStore.getState().syncFromEngine((data as { exchange?: string }).exchange);
      set({
        engineRunning: data.engine_running ?? false,
        mode: data.mode ?? "paper",
        uptimeSec: data.uptime_sec ?? 0,
        wsConnected: data.ws_connected ?? false,
        clientsConnected: data.clients_connected ?? 0,
        _lastHealthAt: Date.now(),
        bridgeConnected: true,
      });
    },

    onLog: (data) =>
      queueLog({
        timestamp: data.timestamp ?? Date.now() / 1000,
        level: data.level ?? "info",
        message: data.message ?? JSON.stringify(data),
      }),

    onEngineError: (data) =>
      queueLog({
        timestamp: data.timestamp ?? Date.now() / 1000,
        level: "error",
        message: data.error ?? "Unknown engine error",
      }),

    setBridgeConnected: (v) => set({ bridgeConnected: v }),

    onChannelStatus: (channel, open) =>
      set((s) => {
        const next = new Set(s.openChannels);
        if (open) next.add(channel);
        else next.delete(channel);
        const openChannels = [...next];
        // Lost the last socket → bridge unreachable right now; don't wait for the watchdog.
        return openChannels.length === 0 && s.bridgeConnected
          ? { openChannels, bridgeConnected: false, engineRunning: false }
          : { openChannels };
      }),

    resetConnection: () =>
      set({ bridgeConnected: false, engineRunning: false, openChannels: [], _lastHealthAt: 0, uptimeSec: 0 }),
  };
});
