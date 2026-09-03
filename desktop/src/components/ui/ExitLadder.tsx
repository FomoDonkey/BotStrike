import { useState, type MouseEvent as ReactMouseEvent, type FocusEvent as ReactFocusEvent } from "react";
import type { ExitLadder, ExitLadderLevel } from "@/lib/api";
import { ladderLevelLabel } from "@/lib/market";
import { cn, formatPrice, formatSignedPct } from "@/lib/utils";

/**
 * Why a trend position has no stop-loss and no take-profit — the copy the operator contract (§1)
 * requires wherever the ladder is shown.
 */
export const EXIT_LADDER_COPY =
  "This position exits in steps. Each Donchian lookback has its own trailing stop that never falls; " +
  "when price closes below one, that share leaves. There is no take profit: trend returns come from " +
  "letting winners run.";

const CARD_W = 330;
const CARD_H = 268;

/**
 * What the position returns AGAINST ITS ENTRY if every remaining leg trails out at today's stops.
 *
 * The ladder's own `worst_case_pct` is measured from the CURRENT price, which answers "how far can
 * it still fall" but not the question an operator actually asks: "if this trails out from here, do
 * I keep a profit?" A position can show +6.5 % unrealised and still have every stop below its entry
 * (BTC on 2026-09-03: entry 76,571.65, full exit 69,437.15).
 */
export function ladderOutcomeVsEntry(ladder: ExitLadder, entry: number | null | undefined): number | null {
  const levels = ladder.levels ?? [];
  if (!entry || entry <= 0 || levels.length === 0) return null;
  let weight = 0;
  let acc = 0;
  for (const lv of levels) {
    const share = lv.share_exiting ?? 0;
    if (!(share > 0) || !(lv.stop > 0)) continue;
    weight += share;
    acc += share * (lv.stop / entry - 1);
  }
  return weight > 0 ? acc / weight : null;
}

/**
 * Segmented bar: one segment per ladder level, sized by the share that leaves there and darkening
 * with distance. Purely decorative — every number is in the cell and the hover card.
 */
export function ExitWeightBar({ ladder, className, width = "w-[72px]" }: { ladder: ExitLadder; className?: string; width?: string }) {
  const levels = ladder.levels ?? [];
  let total = 0;
  for (const lv of levels) total += lv.share_exiting ?? 0;
  if (levels.length === 0 || total <= 0) return null;
  return (
    <span aria-hidden className={cn("inline-flex items-center gap-px h-[5px] rounded-full overflow-hidden bg-white/12", width, className)}>
      {levels.map((lv, i) => (
        <span
          key={`${lv.lookback}-${i}`}
          className="h-full bg-rose"
          style={{ width: `${((lv.share_exiting ?? 0) / total) * 100}%`, opacity: 1 - i * 0.17 }}
        />
      ))}
    </span>
  );
}

/** Full ladder: one row per level with price, distance, share exiting and weight left after it. */
export function ExitLadderDetail({ ladder, entry, className }: { ladder: ExitLadder; entry?: number | null; className?: string }) {
  const levels = ladder.levels ?? [];
  const outcome = ladderOutcomeVsEntry(ladder, entry);
  return (
    <div className={cn("min-w-0", className)}>
      <div className="grid grid-cols-[auto_1fr_auto_auto_auto_auto] gap-x-2.5 gap-y-1 text-[12px]">
        <span className="font-semibold uppercase tracking-[0.04em] text-text-2">Leg</span>
        <span className="font-semibold uppercase tracking-[0.04em] text-text-2 text-right">Stop</span>
        <span className="font-semibold uppercase tracking-[0.04em] text-text-2 text-right">Dist.</span>
        <span className="font-semibold uppercase tracking-[0.04em] text-text-2 text-right" title="Result against this position's entry price if that leg trails out">vs entry</span>
        <span className="font-semibold uppercase tracking-[0.04em] text-text-2 text-right">Leaves</span>
        <span className="font-semibold uppercase tracking-[0.04em] text-text-2 text-right">Left</span>
        {levels.map((lv, i) => (
          <LevelRow key={`${lv.lookback}-${i}`} lv={lv} entry={entry} />
        ))}
      </div>
      {outcome !== null && (
        <p className="mt-2 text-[12px] font-semibold leading-snug">
          <span className="text-text-2 font-medium">If it trails out from here: </span>
          <span className={cn("num", outcome > 0 ? "text-mint" : outcome < 0 ? "text-rose" : "text-text")}>
            {formatSignedPct(outcome, 1)} vs entry
          </span>
          <span className="text-text-2 font-medium">{outcome > 0 ? " — the profit is locked in" : " — a profit is NOT locked in yet"}</span>
        </p>
      )}
      <p className="mt-2 text-[12px] font-medium text-text-2 leading-snug">{EXIT_LADDER_COPY}</p>
    </div>
  );
}

function LevelRow({ lv, entry }: { lv: ExitLadderLevel; entry?: number | null }) {
  const vsEntry = entry && entry > 0 && lv.stop > 0 ? lv.stop / entry - 1 : null;
  return (
    <>
      <span className="num font-semibold text-text" title={`Donchian lookback ${lv.lookback} days`}>D{lv.lookback}</span>
      <span className="num font-semibold text-text text-right">{formatPrice(lv.stop)}</span>
      <span className="num font-semibold text-rose text-right">{formatSignedPct(lv.distance_pct ?? 0, 1)}</span>
      <span className={cn("num font-semibold text-right", vsEntry === null ? "text-text-3" : vsEntry > 0 ? "text-mint" : "text-rose")}>
        {vsEntry === null ? "---" : formatSignedPct(vsEntry, 1)}
      </span>
      <span className="font-semibold text-text-2 text-right whitespace-nowrap">{ladderLevelLabel(lv)}</span>
      <span className="num font-semibold text-text text-right">{Math.round((lv.weight_after ?? 0) * 100)}%</span>
    </>
  );
}

/**
 * Table cell for a trend position: `Exit 71,575 → 69,437` + the weight bar, with a hover/click
 * card listing every level. The card is `position: fixed` so the table's own scroll container
 * never clips it.
 */
export function ExitLadderCell({ ladder, entry, className }: { ladder: ExitLadder; entry?: number | null; className?: string }) {
  const [card, setCard] = useState<{ left: number; top: number; bottom: number } | null>(null);
  const [pinned, setPinned] = useState(false);

  const openAt = (e: ReactMouseEvent<HTMLButtonElement> | ReactFocusEvent<HTMLButtonElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    setCard({ left: r.left, top: r.top, bottom: r.bottom });
  };
  const leave = () => {
    if (!pinned) setCard(null);
  };
  const toggle = (e: ReactMouseEvent<HTMLButtonElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    setCard({ left: r.left, top: r.top, bottom: r.bottom });
    setPinned((p) => !p);
  };

  const open = card !== null;
  // One leg left (or every leg on the same price): "Exit 63.3810 → 63.3810" reads like a bug.
  const single = (ladder.levels?.length ?? 0) <= 1 || Math.abs(ladder.first_exit - ladder.full_exit) < 1e-9;
  let style: { left: number; top: number } | null = null;
  if (card) {
    const vw = typeof window === "undefined" ? 1440 : window.innerWidth;
    const vh = typeof window === "undefined" ? 900 : window.innerHeight;
    const left = Math.max(8, Math.min(card.left, vw - CARD_W - 8));
    const top = card.bottom + CARD_H + 8 > vh ? Math.max(8, card.top - CARD_H - 6) : card.bottom + 6;
    style = { left, top };
  }

  return (
    <div className={cn("relative inline-flex flex-col items-end gap-1", className)}>
      <button
        type="button"
        onMouseEnter={openAt}
        onMouseLeave={leave}
        onFocus={openAt}
        onBlur={leave}
        onClick={toggle}
        aria-expanded={open}
        title={EXIT_LADDER_COPY}
        className="inline-flex flex-col items-end gap-1 rounded-[4px] px-0.5 -mx-0.5 hover:bg-hover"
      >
        <span className="num whitespace-nowrap">
          <span className="text-text-2 font-medium">Exit </span>
          {single ? (
            <>
              <span className="text-rose font-semibold">{formatPrice(ladder.full_exit)}</span>
              <span className="text-text-2 font-medium"> · full exit</span>
            </>
          ) : (
            <>
              <span className="text-text font-semibold">{formatPrice(ladder.first_exit)}</span>
              <span className="text-text-2 font-medium"> → </span>
              <span className="text-rose font-semibold">{formatPrice(ladder.full_exit)}</span>
            </>
          )}
        </span>
        <ExitWeightBar ladder={ladder} />
      </button>
      {open && style && (
        <div
          role="tooltip"
          className="fixed z-[90] rounded-[10px] border border-hairline-strong bg-panel-2 px-3 py-2.5 text-left whitespace-normal"
          style={{ left: style.left, top: style.top, width: CARD_W }}
        >
          <div className="flex items-baseline gap-2 mb-1.5">
            <span className="text-[12.5px] font-semibold text-text">Exit ladder</span>
            <span className="text-[12px] font-medium text-text-2">
              {ladder.active}/{ladder.total} legs · worst {formatSignedPct(ladder.worst_case_pct ?? 0, 1)} from here
            </span>
          </div>
          <ExitLadderDetail ladder={ladder} entry={entry} />
        </div>
      )}
    </div>
  );
}
