import { api, type ActivityEvent } from "@/lib/api";
import { useEndpoint } from "./useEndpoint";

const POLL_MS = 10_000;

/** GET /api/activity (spec §5.2). `missing` on a 2.15 bridge. */
export function useActivity(limit = 100, enabled = true) {
  const st = useEndpoint(() => api.activity(limit), POLL_MS, String(limit), enabled);
  const events: ActivityEvent[] = st.data?.events ?? [];
  return { events, loaded: st.loaded, missing: st.missing, error: st.error };
}
