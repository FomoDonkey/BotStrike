import { BRIDGE_WS_URL } from "./constants";

type MessageHandler = (data: any) => void;

class WebSocketClient {
  private ws: WebSocket | null = null;
  private url: string;
  private channel: string;
  private handlers: Set<MessageHandler> = new Set();
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private pingTimer: ReturnType<typeof setInterval> | null = null;
  private _connected = false;
  private _reconnectAttempts = 0;

  constructor(channel: string) {
    this.channel = channel;
    this.url = `${BRIDGE_WS_URL}/ws/${channel}`;
  }

  get connected() {
    return this._connected;
  }

  connect() {
    if (this.ws?.readyState === WebSocket.OPEN || this.ws?.readyState === WebSocket.CONNECTING) return;

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
        this.ws = null;
        this.scheduleReconnect();
      };

      this.ws.onerror = () => {
        // onerror is always followed by onclose, so reconnect happens there
        this._connected = false;
      };
    } catch {
      this.ws = null;
      this.scheduleReconnect();
    }
  }

  disconnect() {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.stopPing();
    if (this.ws) {
      this.ws.onclose = null;
      this.ws.onerror = null;
      this.ws.close();
      this.ws = null;
    }
    this._connected = false;
  }

  subscribe(handler: MessageHandler) {
    this.handlers.add(handler);
    return () => this.handlers.delete(handler);
  }

  private startPing() {
    this.stopPing();
    this.pingTimer = setInterval(() => {
      if (this.ws?.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify({ type: "ping" }));
      }
    }, 30000);
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
    // Start at 3s, max 30s — much slower than before to avoid flooding
    const delay = Math.min(3000 * Math.pow(2, Math.min(this._reconnectAttempts - 1, 4)), 30000);
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
  // Stagger connections to avoid simultaneous flood
  const channelNames = ["market", "trading", "micro", "risk", "system"];
  channelNames.forEach((ch, i) => {
    setTimeout(() => getChannel(ch).connect(), i * 500);
  });
}

export function disconnectAll() {
  Object.values(channels).forEach((ch) => ch.disconnect());
}
