import { create } from "zustand";

export type AlertLevel = "info" | "warning" | "critical";

export interface Alert {
  id: string;
  level: AlertLevel;
  title: string;
  message: string;
  timestamp: number;
  dismissed: boolean;
  sound?: "trade" | "profit" | "loss" | "alert";
}

export interface AlertRule {
  id: string;
  enabled: boolean;
  name: string;
  type: "price_above" | "price_below" | "vpin_above" | "drawdown_above" | "hawkes_spike";
  threshold: number;
  symbol?: string;
  level: AlertLevel;
  cooldownSec: number;
  lastTriggered: number;
}

const DEFAULT_RULES: AlertRule[] = [
  { id: "dd_warn", enabled: true, name: "Drawdown Warning", type: "drawdown_above", threshold: 0.05, level: "warning", cooldownSec: 300, lastTriggered: 0 },
  { id: "dd_crit", enabled: true, name: "Drawdown Critical", type: "drawdown_above", threshold: 0.08, level: "critical", cooldownSec: 60, lastTriggered: 0 },
  { id: "vpin_toxic", enabled: true, name: "VPIN Toxic", type: "vpin_above", threshold: 0.8, level: "warning", cooldownSec: 120, lastTriggered: 0 },
  { id: "hawkes", enabled: true, name: "Hawkes Spike", type: "hawkes_spike", threshold: 4.0, level: "info", cooldownSec: 60, lastTriggered: 0 },
];

interface AlertState {
  alerts: Alert[];
  rules: AlertRule[];
  soundEnabled: boolean;

  addAlert: (alert: Omit<Alert, "id" | "timestamp" | "dismissed">) => void;
  dismissAlert: (id: string) => void;
  clearAll: () => void;
  toggleSound: () => void;
  updateRule: (id: string, updates: Partial<AlertRule>) => void;
  checkAndTrigger: (data: { drawdown_pct?: number; vpin?: number; hawkes_mult?: number; price?: number; symbol?: string }) => void;
}

let _alertCounter = 0;

export const useAlertStore = create<AlertState>((set, get) => ({
  alerts: [],
  rules: DEFAULT_RULES,
  soundEnabled: true,

  addAlert: (alert) => {
    const id = `alert_${++_alertCounter}`;
    const newAlert: Alert = { ...alert, id, timestamp: Date.now() / 1000, dismissed: false };
    set((s) => ({ alerts: [...s.alerts.slice(-99), newAlert] }));

    // Auto-dismiss after 10s for info, 30s for warning
    if (alert.level !== "critical") {
      setTimeout(() => get().dismissAlert(id), alert.level === "info" ? 10000 : 30000);
    }
  },

  dismissAlert: (id) =>
    set((s) => ({ alerts: s.alerts.map((a) => a.id === id ? { ...a, dismissed: true } : a) })),

  clearAll: () => set({ alerts: [] }),

  toggleSound: () => set((s) => ({ soundEnabled: !s.soundEnabled })),

  updateRule: (id, updates) =>
    set((s) => ({
      rules: s.rules.map((r) => r.id === id ? { ...r, ...updates } : r),
    })),

  checkAndTrigger: (data) => {
    const { rules, addAlert } = get();
    const now = Date.now() / 1000;

    for (const rule of rules) {
      if (!rule.enabled) continue;
      if (now - rule.lastTriggered < rule.cooldownSec) continue;

      let triggered = false;
      let message = "";

      switch (rule.type) {
        case "drawdown_above":
          if (data.drawdown_pct !== undefined && data.drawdown_pct >= rule.threshold) {
            triggered = true;
            message = `Drawdown at ${(data.drawdown_pct * 100).toFixed(1)}% (threshold: ${(rule.threshold * 100).toFixed(0)}%)`;
          }
          break;
        case "vpin_above":
          if (data.vpin !== undefined && data.vpin >= rule.threshold) {
            triggered = true;
            message = `VPIN at ${(data.vpin * 100).toFixed(0)}% — toxic flow detected`;
          }
          break;
        case "hawkes_spike":
          if (data.hawkes_mult !== undefined && data.hawkes_mult >= rule.threshold) {
            triggered = true;
            message = `Hawkes intensity spike: ${data.hawkes_mult.toFixed(1)}x baseline`;
          }
          break;
        case "price_above":
          if (data.price !== undefined && data.price >= rule.threshold) {
            triggered = true;
            message = `${data.symbol || "BTC"} price above $${rule.threshold.toLocaleString()}`;
          }
          break;
        case "price_below":
          if (data.price !== undefined && data.price <= rule.threshold) {
            triggered = true;
            message = `${data.symbol || "BTC"} price below $${rule.threshold.toLocaleString()}`;
          }
          break;
      }

      if (triggered) {
        rule.lastTriggered = now;
        addAlert({
          level: rule.level,
          title: rule.name,
          message,
          sound: rule.level === "critical" ? "alert" : undefined,
        });
      }
    }
  },
}));
