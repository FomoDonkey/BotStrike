import type { ExitLadder, PositionData } from "@/lib/api";
import type { TradeData } from "@/stores/tradingStore";
import { isLong } from "@/lib/market";

/**
 * A trade EPISODE is one round trip of a position: the entry fill(s), the trims and adds along the
 * way, and the exit that flattens it — or, while it is still open, the live position. The trade DB
 * stores fills; the journal reads episodes, because that is what a trader follows on a chart.
 */
export type FillKind = "entry" | "add" | "trim" | "exit";

export interface EpisodeFill {
  ts: number;
  price: number;
  qty: number;
  kind: FillKind;
  pnl: number;
  fee: number;
  side: string;
}

export interface Episode {
  id: string;
  symbol: string;
  strategy: string | null;
  long: boolean;
  openTs: number;
  closeTs: number | null;
  open: boolean;
  /** volume-weighted average entry */
  entryPrice: number;
  /** volume-weighted average exit (closed episodes) */
  exitPrice: number | null;
  /** largest size held */
  qty: number;
  /** realised PnL (Σ exit pnl); open episodes add `unrealized` on top for display */
  pnl: number;
  unrealized: number;
  fees: number;
  fills: EpisodeFill[];
  exitReason: string | null;
  /** the live position, for open episodes */
  position?: PositionData;
  ladder?: ExitLadder | null;
  /** true when the entry fills are older than the fills loaded (the episode is reconstructed from the position) */
  truncated?: boolean;
}

const EPS = 1e-9;

export function isTrim(t: TradeData): boolean {
  const reason = String(t.exit_reason ?? "").toUpperCase();
  return reason === "REBALANCE" || String(t.order_id ?? "").startsWith("trend_rebalance_");
}

function key(symbol: string, strategy: string | null | undefined): string {
  return `${symbol}|${strategy ?? ""}`;
}

/** Fold fills (oldest first) into episodes; attach the live positions to the open ones. */
export function buildEpisodes(fills: TradeData[], positions: PositionData[], nowSec: number): Episode[] {
  const rank = (f: TradeData) => (f.trade_type === "ENTRY" ? 0 : 1);
  const sorted = [...fills].filter((f) => f.timestamp > 0 && f.price > 0)
    .sort((a, b) => a.timestamp - b.timestamp || rank(a) - rank(b));
  const byKey = new Map<string, { size: number; ep: Episode | null; entryQty: number; entryCost: number; exitQty: number; exitCost: number }>();
  const out: Episode[] = [];
  for (const f of sorted) {
    const k = key(f.symbol, f.strategy);
    const st = byKey.get(k) ?? { size: 0, ep: null, entryQty: 0, entryCost: 0, exitQty: 0, exitCost: 0 };
    if (f.trade_type === "ENTRY") {
      if (!st.ep || st.size <= EPS) {
        st.ep = {
          id: `${k}|${f.timestamp}`, symbol: f.symbol, strategy: f.strategy, long: isLong(f.side),
          openTs: f.timestamp, closeTs: null, open: true, entryPrice: f.price, exitPrice: null, qty: 0,
          pnl: 0, unrealized: 0, fees: 0, fills: [], exitReason: null,
        };
        st.size = 0; st.entryQty = 0; st.entryCost = 0; st.exitQty = 0; st.exitCost = 0;
        out.push(st.ep);
      }
      st.ep.fills.push({ ts: f.timestamp, price: f.price, qty: f.quantity, kind: st.size <= EPS ? "entry" : "add", pnl: 0, fee: f.fee || 0, side: f.side });
      st.size += f.quantity;
      st.entryQty += f.quantity; st.entryCost += f.quantity * f.price;
      st.ep.entryPrice = st.entryQty > 0 ? st.entryCost / st.entryQty : f.price;
      st.ep.qty = Math.max(st.ep.qty, st.size);
      st.ep.fees += f.fee || 0;
    } else if (st.ep) {
      const after = st.size - f.quantity;
      const reason = String(f.exit_reason ?? "").toUpperCase();
      const flattens = after <= Math.max(EPS, st.size * 0.02);
      // a trim never closes the episode; a full exit closes it even when the sizes disagree by dust
      const kind: FillKind = isTrim(f) ? "trim" : (flattens || (reason !== "" && reason !== "REBALANCE")) ? "exit" : "trim";
      st.ep.fills.push({ ts: f.timestamp, price: f.price, qty: f.quantity, kind, pnl: f.pnl || 0, fee: f.fee || 0, side: f.side });
      st.size = Math.max(0, after);
      st.exitQty += f.quantity; st.exitCost += f.quantity * f.price;
      st.ep.exitPrice = st.exitQty > 0 ? st.exitCost / st.exitQty : f.price;
      st.ep.pnl += f.pnl || 0;
      st.ep.fees += f.fee || 0;
      if (kind === "exit") {
        st.ep.open = false;
        st.ep.closeTs = f.timestamp;
        st.ep.exitReason = f.exit_reason ?? null;
        st.size = 0;
      }
    }
    byKey.set(k, st);
  }
  // live positions: attach to their open episode, or reconstruct one when the fills are older than the window
  for (const p of positions) {
    if (!(p.entry_price > 0)) continue;
    const k = key(p.symbol, p.strategy);
    const st = byKey.get(k);
    const openEp = st?.ep && st.ep.open ? st.ep : null;
    if (openEp) {
      openEp.position = p;
      openEp.ladder = p.exit_ladder ?? null;
      openEp.unrealized = Number(p.unrealized_pnl ?? 0) || 0;
      openEp.entryPrice = p.entry_price;                       // the book's own average, fee-aware
      // the entry fee of what is still open is accrued on the position (fees_paid); the exits'
      // rows carry the round-trip fee of what already left
      openEp.fees = openEp.fills.filter((f) => f.kind === "trim" || f.kind === "exit").reduce((a, f) => a + f.fee, 0)
        + (Number(p.fees_paid ?? 0) || 0);
    } else {
      const openTs = Number(p.opened_ts ?? 0) || nowSec;
      out.push({
        id: `${k}|pos|${openTs}`, symbol: p.symbol, strategy: p.strategy, long: isLong(p.side),
        openTs, closeTs: null, open: true, entryPrice: p.entry_price, exitPrice: null, qty: Math.abs(Number(p.size) || 0),
        pnl: 0, unrealized: Number(p.unrealized_pnl ?? 0) || 0, fees: Number(p.fees_paid ?? 0) || 0, fills: [], exitReason: null,
        position: p, ladder: p.exit_ladder ?? null, truncated: true,
      });
    }
  }
  return out.sort((a, b) => (b.closeTs ?? nowSec + 1) - (a.closeTs ?? nowSec + 1) || b.openTs - a.openTs);
}

export interface EpisodeStats {
  closed: number;
  open: number;
  /** rebalance trims across every episode (realised money, not round trips) */
  trims: number;
  /** realised PnL of every exit and trim, closed or still open */
  realised: number;
  wins: number;
  winRate: number | null;
  net: number;
  grossWins: number;
  grossLosses: number;
  profitFactor: number | null;
  avgHoldSec: number | null;
  best: Episode | null;
  worst: Episode | null;
  fees: number;
  unrealized: number;
}

export function episodeStats(episodes: Episode[]): EpisodeStats {
  const closed = episodes.filter((e) => !e.open);
  const open = episodes.filter((e) => e.open);
  let wins = 0, net = 0, gw = 0, gl = 0, hold = 0, fees = 0;
  let best: Episode | null = null, worst: Episode | null = null;
  for (const e of closed) {
    net += e.pnl; fees += e.fees;
    if (e.pnl > 0) { wins += 1; gw += e.pnl; } else gl += -e.pnl;
    if (e.closeTs) hold += e.closeTs - e.openTs;
    if (!best || e.pnl > best.pnl) best = e;
    if (!worst || e.pnl < worst.pnl) worst = e;
  }
  for (const e of open) fees += e.fees;
  const trims = episodes.reduce((a, e) => a + e.fills.filter((f) => f.kind === "trim").length, 0);
  const realised = episodes.reduce((a, e) => a + e.pnl, 0);
  return {
    closed: closed.length, open: open.length, trims, realised, wins,
    winRate: closed.length ? wins / closed.length : null,
    net, grossWins: gw, grossLosses: gl,
    profitFactor: gl > 0 ? gw / gl : (gw > 0 ? null : null),
    avgHoldSec: closed.length ? hold / closed.length : null,
    best, worst, fees,
    unrealized: open.reduce((a, e) => a + e.unrealized, 0),
  };
}
