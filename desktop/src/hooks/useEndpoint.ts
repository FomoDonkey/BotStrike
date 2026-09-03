import { useState } from "react";
import { ApiError } from "@/lib/api";
import { usePolling } from "./usePolling";

export interface EndpointState<T> {
  data: T | null;
  /** true after the first response (success or failure) */
  loaded: boolean;
  /** 404 — the bridge does not implement this endpoint yet */
  missing: boolean;
  error: string | null;
  /** Date.now() of the last successful response (0 = none yet) */
  at: number;
}

const EMPTY: EndpointState<never> = { data: null, loaded: false, missing: false, error: null, at: 0 };

/**
 * Poll a GET endpoint that may not exist on the connected bridge (v2.16 endpoints on a 2.15 CT).
 * A 404 becomes `missing: true` with no error toast; every other failure lands in `error`.
 * `key` resets the state (e.g. the symbol) so a stale payload is never shown for a new key.
 */
export function useEndpoint<T>(fetcher: () => Promise<T>, intervalMs: number, key = "", enabled = true): EndpointState<T> {
  const [state, setState] = useState<EndpointState<T> & { key: string }>({ ...EMPTY, key });

  usePolling(async () => {
    try {
      const data = await fetcher();
      setState({ data, loaded: true, missing: false, error: null, at: Date.now(), key });
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        setState({ data: null, loaded: true, missing: true, error: null, at: 0, key });
      } else {
        setState((s) => ({
          data: s.key === key ? s.data : null,
          loaded: true,
          missing: false,
          error: e instanceof ApiError ? e.message : String(e),
          at: s.key === key ? s.at : 0,
          key,
        }));
      }
    }
  }, intervalMs, enabled);

  if (state.key !== key) return EMPTY;
  return state;
}
