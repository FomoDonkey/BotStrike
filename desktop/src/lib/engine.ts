import { invoke } from "@tauri-apps/api/core";
import { getBridgePort, isLocalBridge } from "./config";

let inFlight: Promise<void> | null = null;

/**
 * Ask the Rust side to start the bundled engine if nothing listens on the local port.
 * No-op when the configured bridge is remote, or when not running inside Tauri (plain `vite`).
 */
export function ensureLocalEngine(): Promise<void> {
  if (!isLocalBridge()) return Promise.resolve();
  if (inFlight) return inFlight;
  let p: Promise<string>;
  try {
    p = invoke<string>("ensure_local_engine", { port: getBridgePort() });
  } catch (e) {
    // window.__TAURI_INTERNALS__ missing (browser dev) → nothing to do
    console.info("[engine] not running inside Tauri:", e);
    return Promise.resolve();
  }
  inFlight = p
    .then((msg) => {
      console.info("[engine]", msg);
    })
    .catch((e: unknown) => {
      console.warn("[engine] ensure_local_engine:", e);
    })
    .finally(() => {
      inFlight = null;
    });
  return inFlight;
}
