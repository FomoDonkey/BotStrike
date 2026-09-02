import { useEffect } from "react";
import { connectAll, disconnectAll, getChannel, onChannelStatus } from "@/lib/ws";
import { ensureLocalEngine } from "@/lib/engine";
import { useMarketStore, type Candle, type Tick, type SnapshotData } from "@/stores/marketStore";
import {
  useTradingStore,
  type PositionData,
  type TradeData,
  type SignalData,
  type MetricsData,
} from "@/stores/tradingStore";
import { useMicroStore, type MicroData } from "@/stores/microStore";
import { useRiskStore } from "@/stores/riskStore";
import {
  useSystemStore,
  type HealthMessage,
  type LogMessage,
  type EngineErrorMessage,
} from "@/stores/systemStore";
import { useAlertStore } from "@/stores/alertStore";
import { TRADE_ALERT_MAX_AGE_MS } from "@/lib/constants";
import { toMs } from "@/lib/utils";

/**
 * Start WebSocket connections to the bridge.
 * Called from ConnectionOverlay once the user clicks "Connect" / "Skip".
 * When the configured bridge is loopback, also asks Rust to spawn the bundled engine.
 */
export function startWebSockets() {
  void ensureLocalEngine(); // no-op unless the configured bridge is local
  connectAll();
}

/** Settings → "Save & reconnect": tear everything down and reconnect against the current URL. */
export function restartWebSockets() {
  disconnectAll();
  useSystemStore.getState().resetConnection();
  startWebSockets();
}

export function useWebSocketBridge() {
  useEffect(() => {
    // DON'T auto-connect here — ConnectionOverlay calls startWebSockets().
    // We only wire up the channel handlers.

    const unsubStatus = onChannelStatus((ch, open) => useSystemStore.getState().onChannelStatus(ch, open));

    // Market channel
    const unsubMarket = getChannel("market").subscribe((msg) => {
      try {
        if (msg.type === "tick") {
          useMarketStore.getState().onTick(msg as unknown as Tick);
        } else if (msg.type === "candles") {
          useMarketStore.getState().onCandles(msg.symbol ?? "", (msg.data as Candle[] | undefined) ?? []);
        } else if (msg.type === "snapshot") {
          useMarketStore.getState().onSnapshot((msg.data as SnapshotData | undefined) ?? {});
        }
      } catch (e) {
        console.error("[ws:market] handler error:", e);
      }
    });

    // Trading channel
    const unsubTrading = getChannel("trading").subscribe((msg) => {
      try {
        if (msg.type === "positions") {
          useTradingStore.getState().onPositions(msg.symbol ?? "", (msg.data as PositionData[] | undefined) ?? []);
        } else if (msg.type === "trade") {
          const t = msg.data as TradeData | undefined;
          if (t) {
            useTradingStore.getState().onTrade(t);
            // The bridge replays its recent fills on every (re)connect — a toast + sound for
            // each of them was the "20 notifications on page load" symptom. Only a fill that
            // happened in the last minute is news.
            const fillMs = toMs(t.timestamp) || toMs(msg.timestamp);
            const isLive = fillMs > 0 && Date.now() - fillMs <= TRADE_ALERT_MAX_AGE_MS;
            if (isLive) {
              const isExit = t.trade_type === "EXIT" || (t.pnl ?? 0) !== 0;
              const label = isExit ? `Close ${t.side}` : `Open ${t.side}`;
              const pnlStr = isExit ? ` -- PnL: $${(t.pnl ?? 0).toFixed(4)}` : "";
              useAlertStore.getState().addAlert({
                level: isExit ? ((t.pnl ?? 0) >= 0 ? "info" : "warning") : "info",
                title: isExit ? "Position Closed" : "Position Opened",
                message: `${label} ${t.symbol} @ $${(t.price ?? 0).toFixed(2)}${pnlStr}`,
                sound: isExit ? ((t.pnl ?? 0) >= 0 ? "profit" : "loss") : "trade",
              });
            }
          }
        } else if (msg.type === "signal") {
          if (msg.data) useTradingStore.getState().onSignal(msg.data as SignalData);
        } else if (msg.type === "metrics") {
          const { type: _type, timestamp: _ts, ...metrics } = msg;
          useTradingStore.getState().onMetrics(metrics as unknown as Partial<MetricsData>);
        }
      } catch (e) {
        console.error("[ws:trading] handler error:", e);
      }
    });

    // Micro channel
    const unsubMicro = getChannel("micro").subscribe((msg) => {
      try {
        if (msg.type === "micro_update") {
          const d = msg.data as MicroData | undefined;
          if (d) {
            useMicroStore.getState().onUpdate(d);
            useAlertStore.getState().checkAndTrigger({
              vpin: d.vpin?.vpin,
              hawkes_mult: d.hawkes?.multiplier,
            });
          }
        }
      } catch (e) {
        console.error("[ws:micro] handler error:", e);
      }
    });

    // Risk channel
    const unsubRisk = getChannel("risk").subscribe((msg) => {
      try {
        if (msg.type === "risk_update") {
          const { type: _type, timestamp: _ts, ...riskData } = msg;
          useRiskStore.getState().onUpdate(riskData);
          useAlertStore.getState().checkAndTrigger({
            drawdown_pct: typeof msg.drawdown_pct === "number" ? msg.drawdown_pct : undefined,
          });
        }
      } catch (e) {
        console.error("[ws:risk] handler error:", e);
      }
    });

    // System channel
    const unsubSystem = getChannel("system").subscribe((msg) => {
      try {
        if (msg.type === "health") {
          useSystemStore.getState().onHealth(msg as HealthMessage); // also sets bridgeConnected=true
        } else if (msg.type === "log") {
          useSystemStore.getState().onLog(msg as LogMessage);
        } else if (msg.type === "engine_error") {
          useSystemStore.getState().onEngineError(msg as EngineErrorMessage);
          useAlertStore.getState().addAlert({
            level: "critical",
            title: "Engine Error",
            message: msg.error ?? "Unknown engine error",
            sound: "circuitBreaker",
          });
        }
      } catch (e) {
        console.error("[ws:system] handler error:", e);
      }
    });

    return () => {
      unsubStatus();
      unsubMarket();
      unsubTrading();
      unsubMicro();
      unsubRisk();
      unsubSystem();
      disconnectAll();
    };
  }, []);
}
