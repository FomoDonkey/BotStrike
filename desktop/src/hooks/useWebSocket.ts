import { useEffect, useRef } from "react";
import { connectAll, disconnectAll, getChannel } from "@/lib/ws";
import { useMarketStore } from "@/stores/marketStore";
import { useTradingStore } from "@/stores/tradingStore";
import { useMicroStore } from "@/stores/microStore";
import { useRiskStore } from "@/stores/riskStore";
import { useSystemStore } from "@/stores/systemStore";
import { useAlertStore } from "@/stores/alertStore";

export function useWebSocketBridge() {
  const initialized = useRef(false);

  useEffect(() => {
    if (initialized.current) return;
    initialized.current = true;

    connectAll();

    // Market channel
    const unsubMarket = getChannel("market").subscribe((msg) => {
      if (msg.type === "tick") {
        useMarketStore.getState().onTick(msg);
      } else if (msg.type === "candles") {
        useMarketStore.getState().onCandles(msg.symbol, msg.data);
      } else if (msg.type === "snapshot") {
        useMarketStore.getState().onSnapshot(msg.data);
      }
    });

    // Trading channel
    const unsubTrading = getChannel("trading").subscribe((msg) => {
      if (msg.type === "positions") {
        useTradingStore.getState().onPositions(msg.symbol, msg.data ?? []);
      } else if (msg.type === "trade") {
        useTradingStore.getState().onTrade(msg.data);
        // Alert on trade fill
        const t = msg.data;
        if (t) {
          useAlertStore.getState().addAlert({
            level: (t.pnl ?? 0) >= 0 ? "info" : "warning",
            title: "Trade Executed",
            message: `${t.side} ${t.symbol} @ $${t.price?.toFixed(2)} — PnL: $${(t.pnl ?? 0).toFixed(2)}`,
            sound: (t.pnl ?? 0) >= 0 ? "profit" : "loss",
          });
        }
      } else if (msg.type === "signal") {
        useTradingStore.getState().onSignal(msg.data);
      } else if (msg.type === "metrics") {
        const { type: _, timestamp: __, ...metrics } = msg;
        useTradingStore.getState().onMetrics(metrics);
      }
    });

    // Micro channel — trigger alerts
    const unsubMicro = getChannel("micro").subscribe((msg) => {
      if (msg.type === "micro_update") {
        useMicroStore.getState().onUpdate(msg.data);
        // Check alert rules against microstructure data
        const d = msg.data;
        if (d) {
          useAlertStore.getState().checkAndTrigger({
            vpin: d.vpin?.vpin,
            hawkes_mult: d.hawkes?.multiplier,
          });
        }
      }
    });

    // Risk channel — trigger alerts
    const unsubRisk = getChannel("risk").subscribe((msg) => {
      if (msg.type === "risk_update") {
        useRiskStore.getState().onUpdate(msg);
        // Check drawdown alerts
        useAlertStore.getState().checkAndTrigger({
          drawdown_pct: msg.drawdown_pct,
        });
      }
    });

    // System channel
    const unsubSystem = getChannel("system").subscribe((msg) => {
      if (msg.type === "health") {
        useSystemStore.getState().onHealth(msg);
        useSystemStore.getState().setBridgeConnected(true);
      }
    });

    return () => {
      unsubMarket();
      unsubTrading();
      unsubMicro();
      unsubRisk();
      unsubSystem();
      disconnectAll();
    };
  }, []);
}
