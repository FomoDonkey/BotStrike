import { useEffect, useRef } from "react";
import { connectAll, disconnectAll, getChannel } from "@/lib/ws";
import { useMarketStore } from "@/stores/marketStore";
import { useTradingStore } from "@/stores/tradingStore";
import { useMicroStore } from "@/stores/microStore";
import { useRiskStore } from "@/stores/riskStore";
import { useSystemStore } from "@/stores/systemStore";

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
      } else if (msg.type === "signal") {
        useTradingStore.getState().onSignal(msg.data);
      } else if (msg.type === "metrics") {
        // Extract metrics fields from the message envelope
        const { type: _, timestamp: __, ...metrics } = msg;
        useTradingStore.getState().onMetrics(metrics);
      }
    });

    // Micro channel
    const unsubMicro = getChannel("micro").subscribe((msg) => {
      if (msg.type === "micro_update") {
        useMicroStore.getState().onUpdate(msg.data);
      }
    });

    // Risk channel
    const unsubRisk = getChannel("risk").subscribe((msg) => {
      if (msg.type === "risk_update") {
        useRiskStore.getState().onUpdate(msg);
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
