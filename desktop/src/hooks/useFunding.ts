import { api, type FundingResponse } from "@/lib/api";
import { useEndpoint, type EndpointState } from "./useEndpoint";

const FUNDING_POLL_MS = 30_000;

/** GET /api/funding (operator contract §3): cumulative cost, per market, and the live rates. */
export function useFunding(): EndpointState<FundingResponse> {
  return useEndpoint(() => api.funding(), FUNDING_POLL_MS);
}

/** Seconds until the next settlement, or null when the bridge did not report one. */
export function secondsToSettlement(iso: string | null | undefined, nowMs: number): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  return Math.max(0, (t - nowMs) / 1000);
}
