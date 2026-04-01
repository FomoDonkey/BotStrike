import { create } from "zustand";

interface SystemState {
  engineRunning: boolean;
  mode: string;
  uptimeSec: number;
  wsConnected: boolean;
  clientsConnected: number;
  bridgeConnected: boolean;

  onHealth: (data: any) => void;
  setBridgeConnected: (v: boolean) => void;
}

export const useSystemStore = create<SystemState>((set) => ({
  engineRunning: false,
  mode: "paper",
  uptimeSec: 0,
  wsConnected: false,
  clientsConnected: 0,
  bridgeConnected: false,

  onHealth: (data) =>
    set({
      engineRunning: data.engine_running ?? false,
      mode: data.mode ?? "paper",
      uptimeSec: data.uptime_sec ?? 0,
      wsConnected: data.ws_connected ?? false,
      clientsConnected: data.clients_connected ?? 0,
    }),

  setBridgeConnected: (v) => set({ bridgeConnected: v }),
}));
