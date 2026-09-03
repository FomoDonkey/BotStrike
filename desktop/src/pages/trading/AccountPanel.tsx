import { useState } from "react";
import { useShallow } from "zustand/shallow";
import { RefreshCw } from "lucide-react";
import type { PositionData } from "@/lib/api";
import { useAccount } from "@/hooks/useAccount";
import { useRiskStore } from "@/stores/riskStore";
import { ListRow, ListSection, Signed } from "@/components/ui/ListRow";
import { Button } from "@/components/ui/Button";
import { StatusChip } from "@/components/ui/Chip";
import { ConfirmDialog } from "@/components/ui/Modal";
import { FundingBlock } from "@/components/ui/Funding";
import { useFunding } from "@/hooks/useFunding";
import { useBotControl } from "@/components/layout/useBotControl";
import { HINTS } from "@/lib/hints";
import { cn, formatMoney, formatPct, formatSignedMoney } from "@/lib/utils";
import { PAPER_MAINTENANCE_MARGIN } from "@/lib/market";

interface AccountPanelProps {
  positions: PositionData[];
  className?: string;
  /** Show the Restart engine button row */
  actions?: boolean;
}

/** Strike's Account Overview list + Daily / Weekly limits, peak, drawdown; Restart engine in place of Deposit / Withdraw. */
export function AccountPanel({ positions, className, actions = true }: AccountPanelProps) {
  const { acct, derived, missing } = useAccount(positions);
  const risk = useRiskStore(useShallow((s) => ({ dailyLimit: s.daily_limit, weeklyLimit: s.weekly_limit })));
  const { canControl, disabledReason, busy, run } = useBotControl();
  const funding = useFunding();
  const [confirm, setConfirm] = useState(false);
  const mr = acct.margin_ratio;
  const mrTone = mr >= 0.8 ? "text-rose" : mr >= 0.5 ? "text-amber" : "text-text";

  return (
    <div className={cn("flex flex-col min-h-0 overflow-y-auto", className)}>
      <ListSection title="Account overview" first right={<StatusChip status={acct.mode || "paper"} size="xs" />}>
        <ListRow label="Account Value" hint="Equity = initial capital + realised PnL + unrealised PnL" size="md">{formatMoney(acct.equity)}</ListRow>
        <ListRow label="Available Balance" hint={HINTS.available}>{formatMoney(acct.available)}</ListRow>
        <ListRow label="Withdrawable Balance" hint="Paper: equal to the available balance">{formatMoney(acct.available)}</ListRow>
        <ListRow label="Position Value" hint={HINTS.notional}>{formatMoney(acct.position_value)}</ListRow>
        <ListRow label="Unrealized PNL" hint={HINTS.pnl}><Signed value={acct.unrealized_pnl} format={formatSignedMoney} /></ListRow>
        <ListRow label="Margin Ratio" hint={HINTS.marginRatio}><span className={mrTone}>{formatPct(acct.margin_ratio, 1)}</span></ListRow>
        <ListRow label="Maintenance Margin" hint="Margin needed to keep the open positions (paper: 0.5 % of position value)">{formatMoney(acct.position_value * PAPER_MAINTENANCE_MARGIN)}</ListRow>
        <ListRow label="Margin Used" hint={HINTS.margin}>{formatMoney(acct.margin_used)}</ListRow>
        <ListRow label="Effective Leverage" hint={HINTS.levEff}>{acct.leverage_effective.toFixed(2)}x</ListRow>
      </ListSection>
      <ListSection title="Period">
        <ListRow label="Daily PnL" hint={HINTS.dailyPnl}>
          <Signed value={acct.daily_pnl} format={formatSignedMoney} />
          {risk.dailyLimit > 0 && <span className="text-text-2 font-medium"> / -{formatMoney(risk.dailyLimit, 0)}</span>}
        </ListRow>
        <ListRow label="Weekly PnL" hint={HINTS.weeklyPnl}>
          <Signed value={acct.weekly_pnl} format={formatSignedMoney} />
          {risk.weeklyLimit > 0 && <span className="text-text-2 font-medium"> / -{formatMoney(risk.weeklyLimit, 0)}</span>}
        </ListRow>
        <ListRow label="Realized PnL" hint="Closed-trade PnL net of fees (all-time)"><Signed value={acct.realized_pnl} format={formatSignedMoney} /></ListRow>
        <ListRow label="Fees today" hint={HINTS.feesToday}>{Number.isFinite(acct.fees_today) ? formatMoney(acct.fees_today) : "---"}</ListRow>
        <ListRow label="Peak equity">{acct.peak_equity > 0 ? formatMoney(acct.peak_equity) : "---"}</ListRow>
        <ListRow label="Drawdown" hint={HINTS.drawdown}><span className={acct.drawdown_pct > 0 ? "text-rose" : ""}>{formatPct(acct.drawdown_pct)}</span></ListRow>
        <ListRow label="Open positions">{acct.open_positions}</ListRow>
      </ListSection>
      <FundingBlock funding={funding} />
      {actions && (
        <div className="px-3 py-3 border-t border-hairline flex items-center gap-2">
          <Button variant="secondary" className="flex-1" icon={<RefreshCw className="w-3.5 h-3.5" />} disabled={!canControl} title={disabledReason} loading={busy === "restart"} onClick={() => setConfirm(true)}>
            Restart engine
          </Button>
        </div>
      )}
      {derived && (
        <p className="px-3 py-2 text-[12px] font-medium text-text-2 border-t border-hairline shrink-0" title={missing ? "GET /api/account returned 404 — figures are derived from the broadcasts and the open positions." : "No account overview from the bridge yet (engine stopped?) — figures are derived from the broadcasts and the open positions."}>
          Derived · {missing ? "GET /api/account needs bridge ≥ 2.15" : "waiting for the bridge account overview"}
        </p>
      )}
      <ConfirmDialog
        open={confirm}
        onClose={() => setConfirm(false)}
        onConfirm={() => { setConfirm(false); void run("restart"); }}
        title="Restart the engine?"
        body="The engine stops and starts again with the same mode and exchange. Open paper positions are kept."
        confirmLabel="Restart"
        busy={busy !== null}
      />
    </div>
  );
}
