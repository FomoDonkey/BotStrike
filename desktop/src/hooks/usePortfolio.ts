import { api, type PortfolioResponse } from "@/lib/api";
import { useEndpoint, type EndpointState } from "./useEndpoint";

const POLL_MS = 10_000;

/** GET /api/portfolio (spec §5.1). `missing` on a 2.15 bridge; `data.engine === false` while stopped. */
export function usePortfolio(intervalMs = POLL_MS): EndpointState<PortfolioResponse> {
  return useEndpoint(() => api.portfolio(), intervalMs);
}
