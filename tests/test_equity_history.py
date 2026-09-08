"""Mark-to-market equity history (analytics/equity_history.py): sampling, persistence, daily closes,
peak-to-trough drawdown, the backfill from fills × daily closes, and the portfolio surfaces that
read it (2026-09-08)."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from analytics import equity_history as eh
from analytics.portfolio import compute_portfolio

T0 = datetime(2026, 9, 2, 0, 0, tzinfo=timezone.utc).timestamp()      # 2026-09-02 00:00Z
DAY = 86400.0


def _rec(**kw):
    base = dict(trade_type="ENTRY", symbol="BTC-USD", side="BUY", quantity=0.0, price=0.0, entry_price=0.0,
                exit_price=0.0, fee=0.0, pnl=0.0, entry_fee_charged=0.0, timestamp=T0, strategy="TREND_DAILY",
                duration_sec=0.0, order_id="")
    base.update(kw)
    return SimpleNamespace(**base)


def test_sample_throttles_to_a_minute_and_persists(tmp_path):
    h = eh.EquityHistory(str(tmp_path / "eq.json"))
    assert h.sample(T0, 1000.0, 0.0, 0.0)
    assert not h.sample(T0 + 30, 1001.0)                       # younger than SAMPLE_SEC
    assert h.sample(T0 + 60, 1002.0, 1.0, 1.0)
    assert h.sample(T0 + 61, 1003.0, force=True)
    again = eh.EquityHistory(str(tmp_path / "eq.json"))
    assert [p[1] for p in again.series()] == [1000.0, 1002.0, 1003.0]
    assert again.first_real_ts() == T0 and again.count() == 3


def test_daily_closes_and_peak_to_trough_drawdown(tmp_path):
    h = eh.EquityHistory(str(tmp_path / "eq.json"))
    for k, eq in enumerate((1000.0, 1020.0, 1038.0, 1010.0, 1005.0, 1012.0)):
        h.sample(T0 + k * 3600 * 6, eq)                        # every 6 h → two days
    d = h.daily()
    assert d["2026-09-02"] == {"close": 1010.0, "high": 1038.0, "low": 1000.0, "first": 1000.0, "est": False, "samples": 4}
    assert d["2026-09-03"]["close"] == 1012.0 and d["2026-09-03"]["low"] == 1005.0
    assert h.max_drawdown() == pytest.approx((1038.0 - 1005.0) / 1038.0)
    assert h.max_drawdown(live_equity=990.0) == pytest.approx((1038.0 - 990.0) / 1038.0)
    assert h.max_drawdown(since_ts=T0 + DAY + 1) == pytest.approx((1012.0 - 1005.0) / 1012.0) or \
        h.max_drawdown(since_ts=T0 + DAY + 1) == 0.0
    assert h.peak() == 1038.0


def test_estimated_points_only_fill_days_before_the_first_real_sample(tmp_path):
    h = eh.EquityHistory(str(tmp_path / "eq.json"))
    h.sample(T0 + 3 * DAY + 100, 1010.0)                       # real samples start on 09-05
    n = h.add_estimated([
        {"ts": T0 + DAY - 1, "equity": 1005.0},                # 09-02 end
        {"ts": T0 + 2 * DAY - 1, "equity": 1030.0},            # 09-03 end
        {"ts": T0 + 3 * DAY - 1, "equity": 1020.0},            # 09-04 end
        {"ts": T0 + 4 * DAY - 1, "equity": 999.0},             # 09-05: already covered by a real sample → skipped
    ])
    assert n == 3 and h.add_estimated([{"ts": T0 + DAY - 1, "equity": 1.0}]) == 0
    assert [p[1] for p in h.series()] == [1005.0, 1030.0, 1020.0, 1010.0]
    assert h.last_estimated_ts() == T0 + 3 * DAY - 1 and h.first_real_ts() == T0 + 3 * DAY + 100
    assert h.daily()["2026-09-03"]["est"] is True and h.daily()["2026-09-05"]["est"] is False
    assert h.max_drawdown() == pytest.approx((1030.0 - 1010.0) / 1030.0)


def test_reconstruct_daily_mtm_prices_open_positions_at_the_day_close():
    # 09-02: buy 0.01 BTC at 100,000 (fee 0.5 debited) · 09-03: close 0.004 at 105,000 (round-trip fee 0.8,
    # entry share 0.2 already paid) · 09-04: nothing. Closes: 09-02 102,000 · 09-03 104,000 · 09-04 101,000.
    trades = [
        _rec(trade_type="ENTRY", quantity=0.01, price=100_000.0, entry_price=99_990.0, fee=0.5, timestamp=T0 + 3600),
        _rec(trade_type="EXIT", side="SELL", quantity=0.004, price=105_000.0, entry_price=100_000.0,
             exit_price=105_000.0, fee=0.8, pnl=0.004 * 5_000.0 - 0.8, entry_fee_charged=0.2, timestamp=T0 + DAY + 3600),
        _rec(trade_type="FUNDING", quantity=0.0, price=0.0, pnl=-0.05, timestamp=T0 + DAY + 7200),
    ]
    closes = {"2026-09-02": 102_000.0, "2026-09-03": 104_000.0, "2026-09-04": 101_000.0}
    pts = eh.reconstruct_daily_mtm(trades, 1000.0, lambda sym, day: closes.get(day), "2026-09-02", "2026-09-04")
    assert [p["est"] for p in pts] == [True, True, True]
    # day 1: cash −0.5, 0.01 BTC at avg 100,000 marked 102,000 → +20
    assert pts[0]["equity"] == pytest.approx(1000.0 - 0.5 + 20.0)
    # day 2: cash −0.5 + (19.2 + 0.2) − 0.05 = 18.85 · 0.006 BTC left marked 104,000 → +24
    assert pts[1]["equity"] == pytest.approx(1000.0 + 18.85 + 24.0)
    # day 3: same cash, 0.006 BTC at 101,000 → +6
    assert pts[2]["equity"] == pytest.approx(1000.0 + 18.85 + 6.0)
    assert pts[2]["ts"] == pytest.approx(T0 + 3 * DAY - 1.0)
    # a day whose close is unknown for an open position is skipped, not priced at zero
    pts2 = eh.reconstruct_daily_mtm(trades, 1000.0, lambda sym, day: closes.get(day) if day != "2026-09-03" else None,
                                    "2026-09-02", "2026-09-04")
    assert [p["ts"] for p in pts2] == [pts[0]["ts"], pts[2]["ts"]]


def test_portfolio_surfaces_read_the_mark_to_market_history(tmp_path):
    h = eh.EquityHistory(str(tmp_path / "eq.json"))
    # 09-02 close 1005 (est) · 09-03 close 1030 (est) · 09-04: real samples 1020 → 1038 → 1010
    h.add_estimated([{"ts": T0 + DAY - 1, "equity": 1005.0}, {"ts": T0 + 2 * DAY - 1, "equity": 1030.0}])
    for k, eq in enumerate((1020.0, 1038.0, 1010.0)):
        h.sample(T0 + 2 * DAY + 3600 * (k + 1), eq)
    trades = [_rec(trade_type="ENTRY", quantity=0.01, price=100_000.0, fee=0.5, timestamp=T0 + 3600)]
    now = T0 + 2 * DAY + 4 * 3600
    p = compute_portfolio(trades, 1000.0, [{"symbol": "BTC-USD", "side": "BUY", "notional": 1000.0, "unrealized_pnl": 12.5,
                                            "strategy": "TREND_DAILY"}],
                          now, equity=1012.0, margin_used=333.0, unrealized_pnl=12.5, equity_history=h)
    days = {d["date"]: d for d in p["daily"]}
    assert days["2026-09-02"]["equity"] == 1005.0 and days["2026-09-02"]["equity_est"] is True
    assert days["2026-09-03"]["equity"] == 1030.0 and days["2026-09-03"]["pnl_mtm"] == pytest.approx(25.0)
    assert days["2026-09-04"]["equity"] == 1012.0 and days["2026-09-04"]["equity_est"] is False      # today = live
    assert days["2026-09-04"]["pnl_mtm"] == pytest.approx(1012.0 - 1030.0)
    assert days["2026-09-02"]["equity_realised"] == pytest.approx(1000.0 - 0.5)
    wd = {w["date"]: w for w in p["win_days"]}
    assert wd["2026-09-03"]["result"] == "win" and wd["2026-09-03"]["pnl"] == pytest.approx(25.0)
    assert wd["2026-09-04"]["result"] == "loss" and wd["2026-09-02"]["result"] == "win"
    assert p["perf_30d"]["drawdown"] == pytest.approx((1038.0 - 1010.0) / 1038.0, abs=1e-6)
    assert p["perf_30d"]["drawdown_mtm"] is True
    assert p["perf_30d"]["sharpe_reason"].startswith("needs")
    assert p["analysis"]["longest_win_streak_days"] == 2
    assert p["equity_history"]["real_since"] == pytest.approx(T0 + 2 * DAY + 3600)
    assert p["equity_history"]["estimated_until"] == pytest.approx(T0 + 2 * DAY - 1)
    by = {s["strategy"]: s for s in p["by_strategy"]}
    assert by["TREND_DAILY"]["max_drawdown_mtm"] is True
    assert by["TREND_DAILY"]["max_drawdown"] == pytest.approx((1038.0 - 1010.0) / 1038.0, abs=1e-6)


def test_portfolio_without_history_keeps_the_cash_chain():
    trades = [_rec(trade_type="ENTRY", quantity=0.01, price=100_000.0, fee=0.5, timestamp=T0 + 3600)]
    p = compute_portfolio(trades, 1000.0, [], T0 + DAY, equity=999.5, margin_used=0.0, unrealized_pnl=0.0)
    assert p["equity_history"] == {"real_since": None, "estimated_until": None, "samples": 0}
    assert p["daily"][0]["equity"] == pytest.approx(999.5) and p["daily"][0]["equity_est"] is False
