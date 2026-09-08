"""Independent reconciliation of BotStrike accounting from the raw trade rows (round 17, 2026-09-08).

Run against the CT: `py -3.12 scripts/reconcile_accounting.py` (BOTSTRIKE_BRIDGE overrides the URL).
Every line must read OK; a MISMATCH names the surface that disagrees with the ledger.

Pulls every endpoint at (almost) the same instant and re-derives, from /api/trades only:
  equity, realised, fees, funding, per-position average entry / size / unrealised, round trips,
  win rate, profit factor, per-day PnL — then prints every identity with its delta.
"""
import json, sys, io, urllib.request, datetime as dt
from collections import defaultdict

import os
B = os.getenv("BOTSTRIKE_BRIDGE", "http://192.168.1.204:9420")   # python scripts/reconcile_accounting.py
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

def get(path):
    with urllib.request.urlopen(B + path, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))

t0 = dt.datetime.now(dt.timezone.utc)
trades = get("/api/trades?limit=5000")["trades"]
positions = get("/api/positions")["positions"]
account = get("/api/account")
perf = get("/api/performance")
pf = get("/api/portfolio")
risk = get("/api/risk")
trend = get("/api/trend")
edge = get("/api/edge")
funding_api = get("/api/funding")
t1 = dt.datetime.now(dt.timezone.utc)
print(f"snapshot {t0:%H:%M:%S}–{t1:%H:%M:%S}Z · rows {len(trades)} · positions {len(positions)}")

def utc(ts): return dt.datetime.fromtimestamp(ts, dt.timezone.utc)
def d(a, b): return f"Δ {a - b:+.4f}"
def ok(name, a, b, tol=0.02):
    flag = "OK " if abs(a - b) <= tol else "MISMATCH"
    print(f"  [{flag}] {name}: {a:.4f} vs {b:.4f}  {d(a, b)}")
    return abs(a - b) <= tol

fills = [t for t in trades if t.get("trade_type") in ("ENTRY", "EXIT")]
fund = [t for t in trades if t.get("trade_type") == "FUNDING"]
exits = [t for t in fills if t["trade_type"] == "EXIT"]
entries = [t for t in fills if t["trade_type"] == "ENTRY"]
def is_trim(t): return str(t.get("exit_reason") or "").lower() == "rebalance" or str(t.get("order_id") or "").startswith("trend_rebalance_")
trims = [t for t in exits if is_trim(t)]
rts = [t for t in exits if not is_trim(t)]

print("\n== 1. CASH CHAIN (from rows) ==")
cash_fills = sum(float(t.get("cash_effect") or 0) for t in fills)
cash_fund = sum(float(t.get("pnl") or 0) for t in fund)
cash_fund_ce = sum(float(t.get("cash_effect") or 0) for t in fund)
print(f"  Σ cash_effect fills = {cash_fills:+.4f} · Σ funding pnl = {cash_fund:+.4f} (cash_effect {cash_fund_ce:+.4f}) · rows funding {len(fund)}")
realised_rows = cash_fills + cash_fund
init = float(account["initial_capital"])
ok("account.realized_pnl = Σ cash_effect(all rows)", account["realized_pnl"], realised_rows)
ok("portfolio.realized_pnl = Σ cash_effect", pf["realized_pnl"], realised_rows)
ok("performance.realized_pnl = Σ cash_effect", perf["realized_pnl"], realised_rows)
ok("account.funding_paid = Σ funding rows", account.get("funding_paid", 0), cash_fund)
ok("funding.total_paid = Σ funding rows", funding_api.get("total_paid", 0), cash_fund)
by_sym_f = defaultdict(float)
for t in fund: by_sym_f[t["symbol"]] += float(t.get("pnl") or 0)
for s, v in sorted(by_sym_f.items()):
    ok(f"funding.by_symbol[{s}]", funding_api["by_symbol"].get(s, 0), v, 0.0005)

print("\n== 2. POSITIONS (rebuilt from fills, FIFO-average) ==")
book = {}   # symbol -> dict(qty, cost, fee_accrued, entry_fee_debited)
for t in sorted(fills, key=lambda x: (x.get("entry_ts") if x["trade_type"] == "ENTRY" else x.get("exit_ts")) or 0):
    s = t["symbol"]; b = book.setdefault(s, {"qty": 0.0, "cost": 0.0, "fee_deb": 0.0, "n_entries": 0})
    q = float(t["quantity"])
    if t["trade_type"] == "ENTRY":
        b["qty"] += q; b["cost"] += q * float(t["entry_price"]); b["fee_deb"] += float(t.get("fee") or 0); b["n_entries"] += 1
    else:
        if b["qty"] > 1e-12:
            avg = b["cost"] / b["qty"]
            b["cost"] -= q * avg
            b["fee_deb"] -= float(t.get("entry_fee_charged") or 0)
        b["qty"] -= q
        if b["qty"] <= 1e-9: b["qty"] = 0.0; b["cost"] = 0.0; b["fee_deb"] = 0.0
unreal_rows = 0.0
for p in positions:
    s = p["symbol"]; b = book.get(s, {"qty": 0, "cost": 0, "fee_deb": 0})
    avg = b["cost"] / b["qty"] if b["qty"] > 0 else float("nan")
    mark = float(p["mark_price"]); size = float(p["size"]); ep = float(p["entry_price"])
    u = size * (mark - ep) if p["side"] in ("BUY", "LONG") else size * (ep - mark)
    unreal_rows += u
    print(f"  {s:9s} size api {size:.6f} rows {b['qty']:.6f} {d(size, b['qty'])} | avg entry api {ep:.4f} rows {avg:.4f} {d(ep, avg)} | "
          f"upnl api {float(p['unrealized_pnl']):+.4f} recomputed {u:+.4f} {d(float(p['unrealized_pnl']), u)} | "
          f"entry_fee_debited api {float(p.get('entry_fee_debited') or 0):.4f} rows {b['fee_deb']:.4f} | fees_paid(accrued) {float(p.get('fees_paid') or 0):.4f} | funding api {float(p.get('funding_paid') or 0):+.4f}")
unreal_api = sum(float(p["unrealized_pnl"]) for p in positions)
ok("Σ positions.unrealized (api) = Σ recomputed size×(mark−entry)", unreal_api, unreal_rows, 0.01)
ok("account.unrealized_pnl = Σ positions.unrealized", account["unrealized_pnl"], unreal_api, 0.05)

print("\n== 3. EQUITY ==")
eq_rows = init + realised_rows + unreal_api
ok("account.equity = initial + Σ cash + Σ unrealised", account["equity"], eq_rows, 0.05)
ok("portfolio.equity = account.equity", pf["equity"], account["equity"], 0.1)
ok("performance.equity = account.equity", perf["equity"], account["equity"], 0.1)
ok("risk.equity = account.equity", risk["equity"], account["equity"], 0.1)
ok("portfolio.alltime_pnl = equity − initial", pf["alltime_pnl"], account["equity"] - init, 0.05)
ok("performance.pnl = equity − initial", perf["pnl"], account["equity"] - init, 0.1)
ok("account.available = equity − margin_used", account["available"], account["equity"] - account["margin_used"], 0.01)
ok("portfolio.cash = account.available", pf["cash"], account["available"], 0.1)
margin_rows = sum(float(p["notional"]) / float(p.get("leverage") or 1) for p in positions)
ok("account.margin_used = Σ notional/leverage", account["margin_used"], margin_rows, 0.05)
posval = sum(float(p["notional"]) for p in positions)
ok("account.position_value = Σ notional", account["position_value"], posval, 0.1)
ok("trend.positions notional Σ = Σ notional", sum(float(p["notional"]) for p in trend["positions"]), posval, 0.5)
ok("portfolio.trend_book_notional = Σ notional", pf["trend_book_notional"], posval, 0.5)

print("\n== 4. EXIT ROWS: pnl = qty×(exit−entry) − round-trip fee ==")
for t in exits:
    q = float(t["quantity"]); ep = float(t["entry_price"]); xp = float(t["exit_price"]); fee = float(t.get("fee") or 0)
    gross = q * (xp - ep) if t["side"] in ("SELL",) else q * (ep - xp)   # SELL closes a long
    net = gross - fee
    rt_fee = (q * ep + q * xp) * 0.0005
    tag = "trim" if is_trim(t) else "RT  "
    print(f"  {utc(t['exit_ts']):%m-%d %H:%M} {t['symbol']:9s} {tag} qty {q:.6f} entry {ep:.4f} exit {xp:.4f} gross {gross:+.4f} fee {fee:.4f} (RT@0.05%: {rt_fee:.4f}) net {net:+.4f} row.pnl {float(t['pnl']):+.4f} {d(float(t['pnl']), net)} cash {float(t.get('cash_effect') or 0):+.4f} efc {float(t.get('entry_fee_charged') or 0):.4f}")

print("\n== 5. STATISTICS (round trips only) ==")
rt_pnl = [float(t["pnl"]) for t in rts]
wins = [x for x in rt_pnl if x > 0]; losses = [x for x in rt_pnl if x <= 0]
pf_rt = (sum(wins) / -sum(losses)) if losses and sum(losses) < 0 else float("inf")
print(f"  round trips {len(rts)} · trims {len(trims)} · wins {len(wins)} · Σ RT pnl {sum(rt_pnl):+.4f} · Σ trim pnl {sum(float(t['pnl']) for t in trims):+.4f} · PF {pf_rt:.3f} · win rate {len(wins)/max(1,len(rts)):.4f}")
ok("performance.total_trades = round trips", perf["total_trades"], len(rts), 0)
ok("performance.rebalance_trims = trims", perf.get("rebalance_trims", -1), len(trims), 0)
ok("performance.win_rate", perf["win_rate"], len(wins) / max(1, len(rts)), 0.001)
ok("performance.profit_factor", perf["profit_factor"] or 0, pf_rt, 0.01)
ok("performance.trade_pnl = Σ RT pnl", perf.get("trade_pnl", 0), sum(rt_pnl), 0.001)
ok("performance.avg_win", perf["avg_win"], sum(wins) / max(1, len(wins)), 0.001)
ok("performance.avg_loss", perf["avg_loss"], sum(losses) / max(1, len(losses)), 0.001)
e = edge["strategies"].get("TREND_DAILY", {})
ok("edge.n = round trips", e.get("n", -1), len(rts), 0)
ok("edge.net_pnl = Σ RT pnl", e.get("net_pnl", 0), sum(rt_pnl), 0.001)
bs = [r for r in pf["by_strategy"] if r["strategy"] == "TREND_DAILY"][0]
ok("by_strategy.trades = round trips", bs["trades"], len(rts), 0)
ok("by_strategy.trims", bs.get("trims", -1), len(trims), 0)
ok("by_strategy.win_rate", bs["win_rate"], len(wins) / max(1, len(rts)), 0.001)
ok("by_strategy.profit_factor", bs["profit_factor"], pf_rt, 0.01)
ok("by_strategy.realized = Σ cash", bs["realized"], realised_rows, 0.01)
ok("by_strategy.pnl = realized + unrealized", bs["pnl"], bs["realized"] + bs["unrealized"], 0.001)
ok("by_strategy.funding = Σ funding", bs.get("funding", 0), cash_fund, 0.001)
ok("perf_30d.win_rate", pf["perf_30d"]["win_rate"], len(wins) / max(1, len(rts)), 0.001)
ok("perf_30d.trades = round trips", pf["perf_30d"]["trades"], len(rts), 0)

print("\n== 6. FEES ==")
fee_rows = sum(float(t.get("fee") or 0) for t in fills)
fee_paid_rows = sum((float(t.get("fee") or 0) if t["trade_type"] == "ENTRY" else max(0.0, float(t.get("fee") or 0) - float(t.get("entry_fee_charged") or 0))) for t in fills)
print(f"  Σ row.fee = {fee_rows:.4f} · Σ fee actually charged (models.fee_paid) = {fee_paid_rows:.4f} · Σ positions.fees_paid (accrued on open) = {sum(float(p.get('fees_paid') or 0) for p in positions):.4f}")
ok("performance.total_fees = Σ fee charged", perf["total_fees"], fee_paid_rows, 0.001)
ok("portfolio.fees_paid = Σ fee charged", pf["fees_paid"], fee_paid_rows, 0.001)
ok("by_strategy.fees = Σ fee charged", bs["fees"], fee_paid_rows, 0.001)
ok("edge.fees = Σ RT fees", e.get("fees", 0), sum(float(t.get("fee") or 0) for t in rts), 0.001)

print("\n== 7. DAILY TABLE / WIN DAYS (UTC day of the cash effect) ==")
day_cash = defaultdict(float); day_trades = defaultdict(int); day_vol = defaultdict(float); day_fees = defaultdict(float)
for t in fills:
    ts = t["entry_ts"] if t["trade_type"] == "ENTRY" else t["exit_ts"]
    day = utc(ts).strftime("%Y-%m-%d")
    day_cash[day] += float(t.get("cash_effect") or 0)
    px = float(t["entry_price"] if t["trade_type"] == "ENTRY" else t["exit_price"])
    day_vol[day] += float(t["quantity"]) * px
    if t["trade_type"] == "EXIT" and not is_trim(t): day_trades[day] += 1
    day_fees[day] += (float(t.get("fee") or 0) if t["trade_type"] == "ENTRY" else max(0.0, float(t.get("fee") or 0) - float(t.get("entry_fee_charged") or 0)))
for t in fund:
    day_cash[utc(t["entry_ts"]).strftime("%Y-%m-%d")] += float(t.get("pnl") or 0)
cum = 0.0
for row in pf["daily"]:
    day = row["date"]; cum += day_cash.get(day, 0.0)
    exp_eq = init + cum
    print(f"  {day}: api pnl {row['pnl']:+.4f} rows {day_cash.get(day,0):+.4f} {d(row['pnl'], day_cash.get(day,0))} | trades api {row['trades']} rows {day_trades.get(day,0)} | vol api {row['volume']:.2f} rows {day_vol.get(day,0):.2f} | fees api {row['fees']:.4f} rows {day_fees.get(day,0):.4f} | equity api {row['equity']:.4f} cum-cash {exp_eq:.4f}")
wd = {w["date"]: w for w in pf["win_days"]}
for day in sorted(day_cash):
    w = wd.get(day)
    if w: print(f"  win_day {day}: api {w['pnl']:+.4f} {w['result']} trades {w['trades']} | rows {day_cash[day]:+.4f}")
last = pf["daily"][-1]
ok("daily[-1].equity = account.equity (today includes unrealised)", last["equity"], account["equity"], 0.1)
ok("Σ daily.pnl = Σ cash (realised)", sum(r["pnl"] for r in pf["daily"]), realised_rows, 0.01)
ok("alltime_volume = Σ fills notional", pf["alltime_volume"], sum(day_vol.values()), 0.5)

print("\n== 8. PERIOD MTM (account) ==")
print(f"  daily_pnl {account['daily_pnl']:+.4f} (realised part {account.get('daily_pnl_realised', 0):+.4f}) · weekly_pnl {account['weekly_pnl']:+.4f} (realised {account.get('weekly_pnl_realised', 0):+.4f})")
today = t0.strftime("%Y-%m-%d")
ok("daily_pnl_realised = today's cash rows", account.get("daily_pnl_realised", 0), day_cash.get(today, 0.0), 0.01)
print(f"  peak {account['peak_equity']:.4f} · drawdown {account['drawdown_pct']*100:.3f} % · recomputed {(1 - account['equity']/account['peak_equity'])*100:.3f} %")
ok("drawdown_pct = 1 − equity/peak", account["drawdown_pct"], 1 - account["equity"] / account["peak_equity"], 0.0005)
ok("performance.max_drawdown ≥ current", perf["max_drawdown"], max(perf["max_drawdown"], account["drawdown_pct"]), 0.0005)
