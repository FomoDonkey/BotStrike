import { BRIDGE_WS_URL } from "./constants";

type MessageHandler = (data: any) => void;

class WebSocketClient {
  private ws: WebSocket | null = null;
  private url: string;
  private handlers: Set<MessageHandler> = new Set();
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private pingTimer: ReturnType<typeof setInterval> | null = null;
  private _connected = false;
  private _reconnectAttempts = 0;
  private _maxReconnectDelay = 10000;

  constructor(channel: string) {
    this.url = `${BRIDGE_WS_URL}/ws/${channel}`;
  }

  get connected() {
    return this._connected;
  }

  connect() {
    if (this.ws?.readyState === WebSocket.OPEN) return;

    try {
      this.ws = new WebSocket(this.url);

      this.ws.onopen = () => {
        this._connected = true;
        this._reconnectAttempts = 0;
        this.startPing();
      };

      this.ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          this.handlers.forEach((h) => h(data));
        } catch {
          // ignore parse errors
        }
      };

      this.ws.onclose = () => {
        this._connected = false;
        this.stopPing();
        this.scheduleReconnect();
      };

      this.ws.onerror = () => {
        this._connected = false;
      };
    } catch {
      this.scheduleReconnect();
    }
  }

  disconnect() {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.stopPing();
    this.ws?.close();
    this.ws = null;
    this._connected = false;
  }

  subscribe(handler: MessageHandler) {
    this.handlers.add(handler);
    return () => this.handlers.delete(handler);
  }

  private startPing() {
    this.pingTimer = setInterval(() => {
      if (this.ws?.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify({ type: "ping" }));
      }
    }, 15000);
  }

  private stopPing() {
    if (this.pingTimer) {
      clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
  }

  private scheduleReconnect() {
    if (this.reconnectTimer) return;
    this._reconnectAttempts++;
    const delay = Math.min(1000 * Math.pow(1.5, this._reconnectAttempts), this._maxReconnectDelay);
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delay);
  }
}

// Singleton connections per channel
const channels: Record<string, WebSocketClient> = {};

export function getChannel(channel: string): WebSocketClient {
  if (!channels[channel]) {
    channels[channel] = new WebSocketClient(channel);
  }
  return channels[channel];
}

export function connectAll() {
  ["market", "trading", "micro", "risk", "system"].forEach((ch) => {
    getChannel(ch).connect();
  });
}

export function disconnectAll() {
  Object.values(channels).forEach((ch) => ch.disconnect());
}
