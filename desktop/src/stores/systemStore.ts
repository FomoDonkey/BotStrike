import { create } from "zustand";

interface SystemState {
  engineRunning: boolean;
  mode: string;
  uptimeSec: number;
  wsConnected: boolean;
  clientsConnected: number;
  bridgeConnected: boolean;
  _lastHealthAt: number;

  onHealth: (data: any) => void;
  setBridgeConnected: (v: boolean) => void;
}

// Detect bridge disconnection: if no health message for 10s, mark as disconnected
let _healthWatchdog: ReturnType<typeof setInterval> | null = null;

function startHealthWatchdog() {
  if (_healthWatchdog) return;
  _healthWatchdog = setInterval(() => {
    const state = useSystemStore.getState();
    if (state.bridgeConnected && Date.now() - state._lastHealthAt > 10000) {
      useSystemStore.setState({ bridgeConnected: false, engineRunning: false });
    }
  }, 5000);
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
    _lastHealthAt: 0,

    onHealth: (data) =>
      set({
        engineRunning: data.engine_running ?? false,
        mode: data.mode ?? "paper",
        uptimeSec: data.uptime_sec ?? 0,
        wsConnected: data.ws_connected ?? false,
        clientsConnected: data.clients_connected ?? 0,
        _lastHealthAt: Date.now(),
      }),

    setBridgeConnected: (v) => set({ bridgeConnected: v }),
  };
});
