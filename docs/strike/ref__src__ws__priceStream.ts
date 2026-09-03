/**
 * Price WebSocket stream (public, no auth).
 * Connects to wss://v2.strikefinance.org/ws/stream
 *
 * Channels: markPrice, depth, trade, kline_<interval>, !miniTicker@arr
 */

import { PRICE_WS_URL } from "../api/config";
import type {
  MarkPriceUpdate,
  DepthUpdate,
  TradeEvent,
  MiniTickerUpdate,
} from "../types/ws";

export type PriceStreamCallback = (event: any) => void;

export class PriceStream {
  private ws: WebSocket | null = null;
  private subscriptions = new Set<string>();
  private listeners = new Map<string, Set<PriceStreamCallback>>();
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 10;

  connect() {
    if (this.ws?.readyState === WebSocket.OPEN) return;

    this.ws = new WebSocket(PRICE_WS_URL);

    this.ws.onopen = () => {
      console.log("[PriceStream] Connected");
      this.reconnectAttempts = 0;
      // Re-subscribe on reconnect
      for (const sub of this.subscriptions) {
        const [channel, symbol] = sub.split(":");
        this._sendSubscribe(channel, symbol);
      }
    };

    this.ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);

        // Can be array (miniTicker) or single object
        const messages = Array.isArray(data) ? data : [data];

        for (const msg of messages) {
          const eventType = msg.e;
          if (!eventType) continue;

          // Notify listeners by event type
          const typeListeners = this.listeners.get(eventType);
          if (typeListeners) {
            for (const cb of typeListeners) cb(msg);
          }

          // Notify listeners by symbol
          // Kline events put symbol in msg.k.s, all others use msg.s
          const symbol = msg.s || msg.k?.s;
          if (symbol) {
            const symbolKey = `${eventType}:${symbol}`;
            const symbolListeners = this.listeners.get(symbolKey);
            if (symbolListeners) {
              for (const cb of symbolListeners) cb(msg);
            }
          }
        }
      } catch {
        // ignore parse errors
      }
    };

    this.ws.onclose = () => {
      console.log("[PriceStream] Disconnected");
      this._scheduleReconnect();
    };

    this.ws.onerror = () => {
      this.ws?.close();
    };
  }

  disconnect() {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.ws?.close();
    this.ws = null;
  }

  /**
   * Subscribe to a channel for a symbol.
   * @example
   *   priceStream.subscribe("markPrice", "BTC-USD");
   *   priceStream.subscribe("depth", "BTC-USD");
   *   priceStream.subscribe("trade", "BTC-USD");
   */
  subscribe(channel: string, symbol?: string) {
    const key = symbol ? `${channel}:${symbol}` : channel;
    if (this.subscriptions.has(key)) return;
    this.subscriptions.add(key);
    this._sendSubscribe(channel, symbol);
  }

  unsubscribe(channel: string, symbol?: string) {
    const key = symbol ? `${channel}:${symbol}` : channel;
    this.subscriptions.delete(key);

    if (this.ws?.readyState === WebSocket.OPEN) {
      const msg: any = {
        id: Date.now(),
        method: "unsubscribe",
        channel,
      };
      if (symbol) msg.symbol = symbol;
      this.ws.send(JSON.stringify(msg));
    }
  }

  /**
   * Listen for events.
   * @param key - Event type (e.g. "markPriceUpdate") or "eventType:SYMBOL" (e.g. "depthUpdate:BTC-USD")
   * @returns Unsubscribe function
   */
  on(key: string, callback: PriceStreamCallback): () => void {
    if (!this.listeners.has(key)) {
      this.listeners.set(key, new Set());
    }
    this.listeners.get(key)!.add(callback);
    return () => this.listeners.get(key)?.delete(callback);
  }

  private _sendSubscribe(channel: string, symbol?: string) {
    if (this.ws?.readyState !== WebSocket.OPEN) return;
    const msg: any = {
      id: Date.now(),
      method: "subscribe",
      channel,
    };
    if (symbol) msg.symbol = symbol;
    this.ws.send(JSON.stringify(msg));
  }

  private _scheduleReconnect() {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) return;
    this.reconnectAttempts++;
    const delay = Math.min(5000 * this.reconnectAttempts, 30000);
    this.reconnectTimer = setTimeout(() => this.connect(), delay);
  }
}

// Singleton instance
export const priceStream = new PriceStream();
