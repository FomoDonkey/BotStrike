import { BRIDGE_URL } from "./constants";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 30_000); // 30s timeout
  try {
    const res = await fetch(`${BRIDGE_URL}${path}`, {
      ...options,
      signal: controller.signal,
      headers: { "Content-Type": "application/json", ...options?.headers },
    });
    if (!res.ok) throw new Error(`API error: ${res.status}`);
    return res.json();
  } finally {
    clearTimeout(timeout);
  }
}

export const api = {
  health: () => request<any>("/api/health"),
  config: () => request<any>("/api/config"),
  botStatus: () => request<any>("/api/bot/status"),
  botStart: (mode = "paper") => request<any>("/api/bot/start?mode=" + mode, { method: "POST" }),
  botStop: () => request<any>("/api/bot/stop", { method: "POST" }),
  performance: () => request<any>("/api/performance"),
  strategies: () => request<any>("/api/strategies"),
  trades: (limit = 100) => request<any>(`/api/trades?limit=${limit}`),
  dataCatalog: () => request<any>("/api/data/catalog"),
};
