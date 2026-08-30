import { getBridgeWsUrl } from "./config";
import {
  WS_CHANNEL_LIST,
  WS_PING_MS,
  WS_STALE_MS,
  WS_RECONNECT_BASE_MS,
  WS_RECONNECT_MAX_MS,
  WS_STAGGER_MS,
} from "./constants";

export interface BridgeMessage {
  type?: string;
  timestamp?: number;
  symbol?: string;
  data?: unknown;
  error?: string;
  [key: string]: unknown;
}

type MessageHandler = (data: BridgeMessage) => void;
type StatusListener = (channel: string, open: boolean) => void;

const statusListeners = new Set<StatusListener>();

/** Subscribe to per-channel open/close transitions (used by systemStore). */
export function onChannelStatus(l: StatusListener): () => void {
  statusListeners.add(l);
  return () => {
    statusListeners.delete(l);
  };
}

class WebSocketClient {
  private ws: WebSocket | null = null;
  private readonly channel: string;
  private handlers = new Set<MessageHandler>();
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private pingTimer: ReturnType<typeof setInterval> | null = null;
  private lastMessageAt = 0;
  private _connected = false;
  private _reconnectAttempts = 0;
  private wantOpen = false; // user intent — a close after disconnect() must not reconnect

  constructor(channel: string) {
    this.channel = channel;
  }

  get connected() {
    return this._connected;
  }

  connect() {
    this.wantOpen = true;
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) return;

    // Resolved on every connect → a new URL saved in Settings takes effect on the next (re)connect.
    const url = `${getBridgeWsUrl()}/ws/${this.channel}`;
    let ws: WebSocket;
    try {
      ws = new WebSocket(url);
    } catch {
      this.setClosed();
      this.scheduleReconnect();
      return;
    }
    this.ws = ws;

    ws.onopen = () => {
      if (this.ws !== ws) return;
      this._connected = true;
      this._reconnectAttempts = 0;
      this.lastMessageAt = Date.now();
      this.startPing();
      this.emit(true);
    };

    ws.onmessage = (event) => {
      this.lastMessageAt = Date.now();
      let data: unknown;
      try {
        data = JSON.parse(event.data);
      } catch {
        return; // ignore non-JSON frames
      }
      if (!data || typeof data !== "object") return;
      const msg = data as BridgeMessage;
      if (msg.type === "pong") return;
      this.handlers.forEach((h) => h(msg));
    };

    ws.onclose = () => {
      if (this.ws !== ws) return;
      this.ws = null;
      this.setClosed();
      this.scheduleReconnect();
    };

    ws.onerror = () => {
      /* always followed by onclose → reconnect is scheduled there */
    };
  }

  disconnect() {
    this.wantOpen = false;
    this._reconnectAttempts = 0;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.dropSocket();
    this.setClosed();
  }

  subscribe(handler: MessageHandler) {
    this.handlers.add(handler);
    return () => {
      this.handlers.delete(handler);
    };
  }

  /** Detach handlers + close without waiting for the close handshake (may take a long time on a dead peer). */
  private dropSocket() {
    const ws = this.ws;
    this.ws = null;
    if (!ws) return;
    ws.onopen = null;
    ws.onmessage = null;
    ws.onclose = null;
    ws.onerror = null;
    try {
      ws.close();
    } catch {
      /* already closed */
    }
  }

  private setClosed() {
    this.stopPing();
    if (this._connected) {
      this._connected = false;
      this.emit(false);
    }
  }

  private emit(open: boolean) {
    statusListeners.forEach((l) => l(this.channel, open));
  }

  private startPing() {
    this.stopPing();
    this.pingTimer = setInterval(() => {
      if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
      if (Date.now() - this.lastMessageAt > WS_STALE_MS) {
        // Half-open TCP (LAN / Tailscale / sleeping laptop): no pong and no broadcast →
        // the OS would take minutes to notice. Treat as dead and reconnect now.
        this.dropSocket();
        this.setClosed();
        this.scheduleReconnect();
        return;
      }
      try {
        this.ws.send(JSON.stringify({ type: "ping" }));
      } catch {
        /* send on a closing socket — onclose will follow */
      }
    }, WS_PING_MS);
  }

  private stopPing() {
    if (this.pingTimer) {
      clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
  }

  private scheduleReconnect() {
    if (this.reconnectTimer || !this.wantOpen) return;
    this._reconnectAttempts++;
    // 3 s → 48 s capped at 30 s, with 50-150 % jitter to avoid a thundering herd of 5 channels
    const base = Math.min(WS_RECONNECT_BASE_MS * 2 ** Math.min(this._reconnectAttempts - 1, 4), WS_RECONNECT_MAX_MS);
    const delay = base * (0.5 + Math.random());
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delay);
  }
}

// Singleton connections per channel
const channels = new Map<string, WebSocketClient>();
let staggerTimers: ReturnType<typeof setTimeout>[] = [];
let started = false;

export function getChannel(channel: string): WebSocketClient {
  let c = channels.get(channel);
  if (!c) {
    c = new WebSocketClient(channel);
    channels.set(channel, c);
  }
  return c;
}

export function connectAll() {
  clearStagger();
  started = true;
  WS_CHANNEL_LIST.forEach((ch, i) => {
    staggerTimers.push(setTimeout(() => getChannel(ch).connect(), i * WS_STAGGER_MS));
  });
}

export function disconnectAll() {
  clearStagger();
  started = false;
  channels.forEach((c) => c.disconnect());
}

export function isStarted() {
  return started;
}

function clearStagger() {
  staggerTimers.forEach(clearTimeout);
  staggerTimers = [];
}
