"""Funding accrual: settlement boundaries, direction, multi-period catch-up, persistence, sanity caps."""
import json

import pytest

from analytics.funding import (
    FundingAccrual, annualized_pct, settlement_boundaries,
)

H = 3600.0
DAY0 = 1_788_400_000 - (1_788_400_000 % 86400)      # a UTC midnight


def _pos(symbol="BTC-USD", side="BUY", notional=1000.0, strategy="TREND_DAILY", mark=70000.0):
    return {"symbol": symbol, "side": side, "notional": notional, "strategy": strategy, "mark_price": mark,
            "size": (notional / mark) if mark else 0.0}


def test_settlement_boundaries_are_utc_multiples_of_the_interval():
    b = settlement_boundaries(DAY0 + 1, DAY0 + 9 * H)
    assert b == [DAY0 + 8 * H]
    b = settlement_boundaries(DAY0 - 1, DAY0 + 17 * H)
    assert b == [DAY0, DAY0 + 8 * H, DAY0 + 16 * H]
    assert settlement_boundaries(DAY0 + 1, DAY0 + 2) == []        # nothing crossed
    assert settlement_boundaries(DAY0 + 9 * H, DAY0 + 3 * H) == []  # clock going backwards


def test_longs_pay_when_rate_is_positive_and_receive_when_negative(tmp_path):
    f = FundingAccrual(path=str(tmp_path / "f.json"))
    f.start(DAY0 + 1)
    assert f.compute([_pos()], {"BTC-USD": 0.0001}, DAY0 + 2) == []      # no boundary crossed yet
    pays = f.compute([_pos()], {"BTC-USD": 0.0001}, DAY0 + 8 * H + 5)
    assert len(pays) == 1 and pays[0].amount == pytest.approx(-0.10)     # 1000 * 0.0001, long pays
    short = f.compute([_pos(side="SELL")], {"BTC-USD": 0.0001}, DAY0 + 8 * H + 5)
    assert short[0].amount == pytest.approx(+0.10)                       # short receives
    neg = f.compute([_pos()], {"BTC-USD": -0.0002}, DAY0 + 8 * H + 5)
    assert neg[0].amount == pytest.approx(+0.20)                         # long receives when rate < 0


def test_downtime_charges_every_missed_settlement_once(tmp_path):
    f = FundingAccrual(path=str(tmp_path / "f.json"))
    f.start(DAY0 + 1)
    now = DAY0 + 25 * H                                                  # 3 boundaries crossed (8, 16, 24)
    pays = f.compute([_pos()], {"BTC-USD": 0.0001}, now)
    assert pays[0].periods == 3 and pays[0].amount == pytest.approx(-0.30)
    total = f.mark_settled(pays, now)
    assert total == pytest.approx(-0.30) and f.total_paid == pytest.approx(-0.30)
    # already settled → nothing pending until the next boundary
    assert f.compute([_pos()], {"BTC-USD": 0.0001}, now + 60) == []
    assert f.compute([_pos()], {"BTC-USD": 0.0001}, DAY0 + 33 * H) != []


def test_zero_missing_and_absurd_rates_are_skipped(tmp_path):
    f = FundingAccrual(path=str(tmp_path / "f.json"))
    f.start(DAY0 + 1)
    now = DAY0 + 8 * H + 1
    assert f.compute([_pos()], {"BTC-USD": 0.0}, now) == []
    assert f.compute([_pos()], {}, now) == []                            # symbol without a rate
    assert f.compute([_pos()], {"BTC-USD": 0.5}, now) == []               # 50 % per 8 h = bad tick
    assert f.compute([_pos()], {"BTC-USD": float("nan")}, now) == []
    assert f.compute([_pos(notional=0.0, mark=0.0)], {"BTC-USD": 0.0001}, now) == []


def test_state_persists_and_never_double_charges_across_restart(tmp_path):
    p = str(tmp_path / "f.json")
    f = FundingAccrual(path=p)
    f.start(DAY0 + 1)
    now = DAY0 + 8 * H + 5
    f.mark_settled(f.compute([_pos()], {"BTC-USD": 0.0001}, now), now)
    again = FundingAccrual.load(p)
    assert again.last_settled_ts == now and again.total_paid == pytest.approx(-0.10)
    assert again.by_symbol["BTC-USD"] == pytest.approx(-0.10)
    assert again.compute([_pos()], {"BTC-USD": 0.0001}, now + 10) == []   # same period not charged twice
    saved = json.load(open(p, encoding="utf-8"))
    assert saved["last_settled_ts"] == now and len(saved["history"]) == 1


def test_first_run_arms_the_clock_without_charging(tmp_path):
    f = FundingAccrual(path=str(tmp_path / "f.json"))
    assert f.due(DAY0 + 40 * H) == []                                    # no state → never a retroactive charge
    f.start(DAY0)
    assert f.due(DAY0 + 9 * H) == [DAY0 + 8 * H]
    f.start(DAY0 + 100)                                                  # start is idempotent
    assert f.last_settled_ts == DAY0


def test_status_and_annualization(tmp_path):
    f = FundingAccrual(path=str(tmp_path / "f.json"))
    f.start(DAY0)
    now = DAY0 + 8 * H + 1
    f.mark_settled(f.compute([_pos(), _pos(symbol="ETH-USD", notional=500.0, mark=2000.0)],
                             {"BTC-USD": 0.0001, "ETH-USD": 0.0002}, now), now)
    st = f.status()
    assert st["total_paid"] == pytest.approx(-0.20) and st["by_symbol"]["ETH-USD"] == pytest.approx(-0.10)
    assert st["last_settled_utc"].endswith("Z") and len(st["recent"]) == 2
    assert annualized_pct(0.0001) == pytest.approx(0.1095)               # 0.01 %/8 h ≈ 10.95 %/yr
    assert annualized_pct(0.0000168) == pytest.approx(0.0183, abs=1e-4)  # measured ADA rate ≈ 1.8 %/yr


def test_record_rates_appends_a_csv_row_per_market(tmp_path):
    from analytics.funding import record_rates
    p = str(tmp_path / "rates.csv")
    assert record_rates({"BTC-USD": 0.0001, "ETH-USD": -0.00002, "BAD": float("nan")}, DAY0, path=p) == 2
    assert record_rates({"BTC-USD": 0.00015}, DAY0 + 8 * H, path=p) == 1
    lines = open(p, encoding="utf-8").read().strip().splitlines()
    assert lines[0] == "ts,utc,symbol,rate,annualized_pct" and len(lines) == 4
    assert lines[1].split(",")[2] == "BTC-USD" and lines[1].endswith("0.109500")   # 0.01 %/8 h ≈ 10.95 %/yr
    assert record_rates({}, DAY0, path=p) == 0


def test_funding_is_attributed_to_the_position_not_the_market_lifetime():
    """A symbol closed and reopened must not inherit the previous position's carry."""
    acc = FundingAccrual(path=None)
    acc.history = [
        {"symbol": "BTC-USD", "amount": -1.0, "ts": 1000.0},   # first position
        {"symbol": "BTC-USD", "amount": -2.0, "ts": 2000.0},   # first position
        {"symbol": "BTC-USD", "amount": -0.5, "ts": 5000.0},   # second position, opened at 4000
        {"symbol": "ETH-USD", "amount": -9.0, "ts": 5000.0},   # another market entirely
    ]
    acc.by_symbol = {"BTC-USD": -3.5, "ETH-USD": -9.0}
    assert acc.since("BTC-USD", 4000.0) == -0.5          # only the open position's window
    assert acc.since("BTC-USD", 900.0) == -3.5           # the whole lifetime when asked for it
    assert acc.since("BTC-USD", 1500.0, 2500.0) == -2.0  # a closed position's window
    assert acc.since("BTC-USD", 0.0) == 0.0              # unknown open time: caller falls back
    assert acc.since("SOL-USD", 900.0) == 0.0
