import { useState } from "react";
import { AlertTriangle, Check } from "lucide-react";
import { api, ApiError, type RiskProfileInfo, type RiskProfilesResponse } from "@/lib/api";
import { useEndpoint } from "@/hooks/useEndpoint";
import { useBridgeConfig } from "@/lib/config";
import { useAlertStore } from "@/stores/alertStore";
import { refreshRiskIntoStore } from "@/hooks/useVisibilityRefresh";
import { Panel } from "@/components/ui/Panel";
import { Button } from "@/components/ui/Button";
import { Chip } from "@/components/ui/Chip";
import { ConfirmDialog } from "@/components/ui/Modal";
import { Hint } from "@/components/shared/Hint";
import { TOKEN_GATED_REASON } from "@/lib/constants";
import { cn, capitalize, formatMoney, formatPct } from "@/lib/utils";

const PROFILES_POLL_MS = 30_000;

/** The one sentence that answers "should I use more leverage?" (operator contract §4). */
export const RISK_LEVEL_COPY =
  "Same strategy, same edge. More risk does not mean better, it means bigger in both directions.";

const PROFILE_BLURB: Record<string, string> = {
  conservative: "The smallest position sizes. Slow, shallow drawdowns.",
  balanced: "The sizing the paper book runs today.",
  aggressive: "Deliberately past the research range. Shown with its own warning instead of this line.",
};

/**
 * Risk page header (§4): three cards priced for the CURRENT equity — expected per year, expected
 * worst drawdown and the loss limits that move with the level. Never called leverage: the lever is
 * the target volatility, and the Sharpe is flat across all three.
 */
export function RiskProfileCards() {
  const ep = useEndpoint(() => api.riskProfiles(), PROFILES_POLL_MS);
  const { isLocal, token } = useBridgeConfig();
  const addAlert = useAlertStore((s) => s.addAlert);
  const [pending, setPending] = useState<RiskProfileInfo | null>(null);
  const [busy, setBusy] = useState(false);
  const canApply = isLocal || token.length > 0;
  const disabledReason = canApply ? undefined : TOKEN_GATED_REASON;

  const d: RiskProfilesResponse | null = ep.data;
  const profiles = d?.profiles ?? [];
  const current = d?.current ?? "";
  // Size on the same basis the engine sizes on (equity including open positions), not on the risk
  // manager's realised equity: the header said "current equity $1,009.64" beside a card reading
  // $1,016.07 (audit 2026-09-03).
  const equity = d?.equity_basis ?? d?.equity ?? 0;
  const range = d?.validated_target_vol_range ?? [0.1, 0.3];
  const targetVol = d?.current_values?.trend_target_vol;
  const outsideRange =
    current === "custom" && typeof targetVol === "number" && (targetVol < (range[0] ?? 0.1) || targetVol > (range[1] ?? 0.3));
  const sharpe = profiles.length ? profiles.reduce((a, p) => a + (p.sharpe ?? 0), 0) / profiles.length : null;

  const apply = async (name: string) => {
    setBusy(true);
    try {
      const r = await api.riskProfileApply(name);
      addAlert({
        level: "info",
        title: `Risk level: ${capitalize(name)}`,
        message: `${r.status}${r.restart_required ? " · restart required" : " · live at the next daily run (00:05 UTC)"}`,
      });
      await refreshRiskIntoStore();
    } catch (e) {
      addAlert({ level: "critical", title: "Could not apply the risk level", message: e instanceof ApiError ? e.message : String(e), sound: "alert" });
    } finally {
      setBusy(false);
      setPending(null);
    }
  };

  if (!ep.loaded && !d) {
    return (
      <Panel className="px-4 py-3">
        <p className="text-[12.5px] font-medium text-text">Loading risk levels…</p>
      </Panel>
    );
  }
  if (!d) {
    return (
      <Panel className="px-4 py-3">
        <p className="text-[12.5px] font-medium text-text">
          {ep.missing ? "Risk levels need GET /api/risk/profiles (bridge ≥ 2.16)." : ep.error ?? "No risk levels reported."}
        </p>
      </Panel>
    );
  }

  return (
    <div className="flex flex-col gap-2 min-w-0">
      <div className="flex items-baseline gap-2 flex-wrap">
        <h2 className="text-[14px] font-semibold text-text">Risk level</h2>
        <span className="text-[12.5px] font-medium text-text-2">
          priced for the equity the bot sizes on <span className="num text-text font-semibold">{formatMoney(equity)}</span>
          {sharpe !== null && (
            <>
              {" · Sharpe "}
              <span className="num text-text font-semibold">{sharpe.toFixed(2)}</span>
              {" on all three"}
            </>
          )}
        </span>
      </div>
      <p className="text-[12.5px] font-semibold text-text leading-snug">{RISK_LEVEL_COPY}</p>

      {outsideRange && (
        <div className="flex items-start gap-2 rounded-lg border border-amber/60 bg-amber-soft px-3 py-2">
          <AlertTriangle className="w-4 h-4 text-amber shrink-0 mt-0.5" />
          <p className="text-[12.5px] font-medium text-text leading-snug">
            Target volatility {formatPct(targetVol ?? 0, 0)} is outside the validated range (
            {formatPct(range[0] ?? 0.1, 0)}–{formatPct(range[1] ?? 0.3, 0)}). Nothing above it was measured — the expected
            numbers below do not apply.
          </p>
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
        {profiles.map((p) => (
          <ProfileCard
            key={p.profile}
            p={p}
            selected={p.profile === current}
            canApply={canApply}
            disabledReason={disabledReason}
            busy={busy}
            onApply={() => setPending(p)}
          />
        ))}
      </div>

      {current === "custom" && (
        <Panel className="px-4 py-3 flex flex-col gap-1.5">
          <div className="flex items-center gap-2">
            <span className="text-[13px] font-semibold text-text">Custom</span>
            <Chip tone={outsideRange ? "amber" : "neutral"} size="xs">{outsideRange ? "OUTSIDE THE VALIDATED RANGE" : "READ ONLY"}</Chip>
          </div>
          <p className="text-[12.5px] font-medium text-text-2 leading-snug">
            The current settings do not match any validated level. Target volatility{" "}
            <span className="num text-text font-semibold">{formatPct(targetVol ?? 0, 0)}</span>, max drawdown{" "}
            <span className="num text-text font-semibold">{formatPct(d.current_values.max_drawdown_pct, 0)}</span>, daily{" "}
            <span className="num text-text font-semibold">{formatPct(d.current_values.max_daily_loss_pct, 0)}</span>, weekly{" "}
            <span className="num text-text font-semibold">{formatPct(d.current_values.max_weekly_loss_pct, 0)}</span>. Pick a
            card above to go back to a measured configuration.
          </p>
        </Panel>
      )}

      <ConfirmDialog
        open={pending !== null}
        onClose={() => setPending(null)}
        onConfirm={() => pending && void apply(pending.profile)}
        title={pending ? `Switch to ${capitalize(pending.profile)}?` : ""}
        confirmLabel="Apply risk level"
        busy={busy}
        body={
          pending && (
            <div className="flex flex-col gap-3">
              <dl className="kv">
                <dt>Target volatility</dt>
                <dd>{formatPct(pending.target_vol, 0)}</dd>
                <dt>Expected per year</dt>
                <dd className="text-mint">{formatMoney(pending.expected_year_usd)}</dd>
                <dt>Expected worst drawdown</dt>
                <dd className="text-rose">−{formatMoney(pending.expected_worst_drawdown_usd)}</dd>
                <dt>Max drawdown limit</dt>
                <dd>{formatPct(pending.limits.max_drawdown_pct, 0)}</dd>
                <dt>Daily / weekly loss limit</dt>
                <dd>{formatPct(pending.limits.max_daily_loss_pct, 0)} / {formatPct(pending.limits.max_weekly_loss_pct, 0)}</dd>
              </dl>
              <p className="text-[13px] font-medium text-text leading-relaxed">
                The new target volatility takes effect at the next daily run (00:05 UTC); the loss limits apply
                immediately. {RISK_LEVEL_COPY}
              </p>
            </div>
          )
        }
      />
    </div>
  );
}

function ProfileCard({ p, selected, canApply, disabledReason, busy, onApply }: {
  p: RiskProfileInfo;
  selected: boolean;
  canApply: boolean;
  disabledReason?: string;
  busy: boolean;
  onApply: () => void;
}) {
  return (
    <Panel accent={selected} className="px-4 py-3 flex flex-col gap-2 min-w-0">
      <div className="flex items-center gap-2 min-w-0">
        <span className="text-[13px] font-semibold text-text truncate">{capitalize(p.profile)}</span>
        {selected && (
          <Chip tone="mint" size="xs">
            <Check className="w-3 h-3" /> CURRENT
          </Chip>
        )}
        {/* A named profile can be measured and still sit outside the range the research covers.
            Stamping VALIDATED on it while the note underneath says the opposite is worse than
            either message alone (2026-09-04). */}
        <Chip tone={p.beyond_validated_range ? "amber" : p.validated ? "neutral" : "amber"} size="xs" className="ml-auto">
          {p.beyond_validated_range ? "BEYOND THE RESEARCH" : p.validated ? "VALIDATED" : "NOT VALIDATED"}
        </Chip>
      </div>

      <div className="flex items-end gap-4">
        <div className="min-w-0">
          <p className="text-[12px] font-medium text-text-2 truncate">
            <Hint title="Expected return over a year at today's equity, from the validated backtest of this target volatility.">Expected / year</Hint>
          </p>
          <p className="num text-[22px] leading-none font-bold text-mint">+{formatMoney(p.expected_year_usd)}</p>
        </div>
        <div className="min-w-0">
          <p className="text-[12px] font-medium text-text-2 truncate">
            <Hint title="Expected worst peak-to-trough loss at today's equity — what a bad stretch costs, in money.">Worst drawdown</Hint>
          </p>
          <p className="num text-[22px] leading-none font-bold text-rose">−{formatMoney(p.expected_worst_drawdown_usd)}</p>
        </div>
      </div>

      <dl className="kv">
        <dt><Hint title="How much the position sizer aims to make the book move per year. This is the lever — not leverage.">Target volatility</Hint></dt>
        <dd>{formatPct(p.target_vol, 0)}</dd>
        {typeof p.leverage_cap === "number" && (
          <>
            <dt><Hint title={p.leverage_note ?? "Ceiling on the position scalar, not a fixed multiplier."}>Leverage ceiling</Hint></dt>
            <dd>{p.leverage_cap}x max</dd>
          </>
        )}
        <dt>Expected return</dt>
        <dd>{formatPct(p.expected_cagr, 1)}</dd>
        {typeof p.worst_day === "number" && (
          <>
            <dt><Hint title="The worst single day this level produced over ten years of the validated backtest. The daily loss limit is set above it on purpose, so an ordinary bad day does not halt the bot.">Worst day seen</Hint></dt>
            <dd className="text-rose">−{formatPct(p.worst_day, 1)}</dd>
          </>
        )}
        {typeof p.longest_underwater_days === "number" && (
          <>
            <dt><Hint title="The longest the book went without making a new high. This is the cost that is easiest to overlook: more return and more drawdown also mean longer stretches of watching the account sit below its peak.">Longest below peak</Hint></dt>
            <dd>{p.longest_underwater_days} days</dd>
          </>
        )}
        <dt>Max drawdown limit</dt>
        <dd>{formatPct(p.limits.max_drawdown_pct, 0)}</dd>
        <dt>Daily loss limit</dt>
        <dd>{formatPct(p.limits.max_daily_loss_pct, 0)}</dd>
        <dt>Weekly loss limit</dt>
        <dd>{formatPct(p.limits.max_weekly_loss_pct, 0)}</dd>
      </dl>

      {p.beyond_validated_range && (
        <div className="flex items-start gap-2 rounded-[6px] border border-amber/40 bg-amber/10 px-2 py-1.5">
          <AlertTriangle className="w-3.5 h-3.5 text-amber shrink-0 mt-0.5" />
          <p className="text-[12px] font-medium text-text leading-snug">
            Outside the validated range. The research covers 10–30 % target volatility; this level was
            chosen deliberately. Same strategy and nearly the same Sharpe — the extra return is a
            bigger position, and the drawdown and the time spent under water grow with it.
          </p>
        </div>
      )}
      {!p.beyond_validated_range && (
        <p className="text-[12px] font-medium text-text-2 leading-snug">{PROFILE_BLURB[p.profile] ?? p.note ?? ""}</p>
      )}

      <Button
        variant={selected ? "secondary" : "primary"}
        size="sm"
        className={cn("w-full mt-auto")}
        disabled={selected || !canApply || busy}
        title={selected ? "This is the level the bot is running" : disabledReason}
        onClick={onApply}
      >
        {selected ? "In use" : `Use ${capitalize(p.profile)}`}
      </Button>
    </Panel>
  );
}
