import type { FundingResponse } from "@/lib/api";
import type { EndpointState } from "@/hooks/useEndpoint";
import { secondsToSettlement } from "@/hooks/useFunding";
import { useNow } from "@/hooks/useNow";
import { Panel, PanelHeader } from "./Panel";
import { ListRow, ListSection, Signed } from "./ListRow";
import { ProgressBar } from "./KpiCard";
import { marketLabel } from "@/lib/market";
import { HINTS } from "@/lib/hints";
import { cn, formatClock, formatMoney, formatPct, formatSignedMoney } from "@/lib/utils";

/** Why funding exists and what it has cost historically — operator contract §3. */
export const FUNDING_COPY =
  "Perpetuals charge funding on a fixed clock — hourly on Strike. A long position pays when the " +
  "rate is positive. Measured on " +
  "Strike over 90 days, longs paid a median 8.1 %/yr of notional: from XAG at +15.1 % down to WTI " +
  "at −15.7 %, where the longs are the ones being paid.";

/**
 * Trade → Account tab block (§3): cumulative funding, the countdown to the next settlement and
 * the live annualised rate per market — the number that says whether holding is expensive now.
 */
export function FundingBlock({ funding }: { funding: EndpointState<FundingResponse> }) {
  const now = useNow();
  const f = funding.data;
  const rates = Object.entries(f?.rates ?? {});
  const left = secondsToSettlement(f?.next_settlement_utc, now);
  const interval = f?.interval_hours ?? 8;

  return (
    <ListSection title="Funding" right={f?.enabled === false ? "off" : undefined}>
      <ListRow label="Funding paid" hint={HINTS.fundingTotal}>
        <Signed value={typeof f?.total_paid === "number" ? f.total_paid : undefined} format={(v) => formatSignedMoney(v, 4)} />
      </ListRow>
      <ListRow label="Next settlement" hint={`Funding settles every ${interval} h on the UTC clock. Strike settles hourly; Binance-style venues every 8 h.`}>
        {left === null ? "---" : formatClock(left)}
      </ListRow>
      <ListRow label="Interval">{`${interval} h`}</ListRow>
      {rates.length === 0 ? (
        <p className="text-[12.5px] font-medium text-text py-1">
          {funding.missing ? "Live rates need bridge ≥ 2.16" : funding.loaded ? "No live funding rate yet" : "Loading funding…"}
        </p>
      ) : (
        <div className="flex flex-col gap-1 pt-1">
          <p className="text-[12px] font-medium text-text-2">Annualised rate per market — what holding costs at today's rate.</p>
          <div className="flex flex-wrap gap-1.5">
            {rates.map(([sym, r]) => (
              <span key={sym} className="inline-flex items-baseline gap-1 rounded-[6px] bg-panel-2 px-2 py-1">
                <span className="text-[12px] font-semibold text-text">{marketLabel(sym)}</span>
                <span className={cn("num text-[12px] font-semibold", (r.annualized_pct ?? 0) > 0 ? "text-rose" : (r.annualized_pct ?? 0) < 0 ? "text-mint" : "text-text")}>
                  {formatPct(r.annualized_pct ?? 0, 1)}
                </span>
                <span className="text-[11.5px] font-medium text-text-2">/yr</span>
              </span>
            ))}
          </div>
        </div>
      )}
    </ListSection>
  );
}

/**
 * "Funding cost" card (§3): total since inception plus a bar per market, with the copy that
 * explains what funding is and what it measured historically.
 */
export function FundingCostCard({ funding, className }: { funding: EndpointState<FundingResponse>; className?: string }) {
  const now = useNow();
  const f = funding.data;
  const total = typeof f?.total_paid === "number" ? f.total_paid : null;
  const bySymbol = Object.entries(f?.by_symbol ?? {}).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
  let peak = 0;
  for (const [, v] of bySymbol) peak = Math.max(peak, Math.abs(v));
  const left = secondsToSettlement(f?.next_settlement_utc, now);
  const interval = f?.interval_hours ?? 1;

  return (
    <Panel className={cn("flex flex-col", className)}>
      <PanelHeader
        title="Funding cost"
        right={
          <span className="text-[12px] font-medium text-text-2">
            next in <span className="num text-text font-semibold">{left === null ? "---" : formatClock(left)}</span>
          </span>
        }
      />
      <div className="px-4 py-3 flex flex-col gap-3 min-w-0">
        <div>
          <p className="text-[12.5px] font-medium text-text-2">Total since inception</p>
          <p className="num text-[24px] font-bold leading-tight">
            <Signed value={total ?? undefined} format={(v) => formatSignedMoney(v, 4)} />
          </p>
        </div>
        {bySymbol.length === 0 ? (
          <p className="text-[12.5px] font-medium text-text">
            {funding.missing ? "GET /api/funding needs bridge ≥ 2.16" : `No funding settled yet — the first charge lands at the next ${interval} h mark.`}
          </p>
        ) : (
          <div className="flex flex-col gap-2">
            {bySymbol.map(([sym, v]) => (
              <div key={sym}>
                <ListRow label={marketLabel(sym)}>
                  <Signed value={v} format={(x) => formatSignedMoney(x, 4)} />
                </ListRow>
                <ProgressBar ratio={peak > 0 ? Math.abs(v) / peak : 0} tone={v < 0 ? "rose" : "mint"} />
              </div>
            ))}
          </div>
        )}
        <p className="text-[12px] font-medium text-text-2 leading-snug">{FUNDING_COPY}</p>
        {typeof f?.total_paid === "number" && f.total_paid !== 0 && (
          <p className="text-[12px] font-medium text-text-2 leading-snug">
            Paid so far: <span className="num text-text font-semibold">{formatMoney(Math.abs(f.total_paid), 4)}</span> — rose bars are markets the book paid, mint ones paid the book.
          </p>
        )}
      </div>
    </Panel>
  );
}
