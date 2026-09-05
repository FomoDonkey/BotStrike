"""The daily / weekly mark-to-market PnL is measured from the period start, not from the last restart.

Seen 2026-09-05 on the CT: daily +$0.29 before a redeploy, −$0.003 right after it, with the book
unchanged — the open-PnL baseline had been re-seeded at the restart.
"""
from datetime import datetime, timezone

from config.settings import Settings
from risk.risk_manager import RiskManager


def _rm(initial=1000.0):
    s = Settings()
    s.trading.initial_capital = initial
    return RiskManager(s)


def _keys():
    now = datetime.now(timezone.utc)
    wk = now.isocalendar()[:2]
    return now.strftime("%Y-%m-%d"), f"{wk[0]}-W{wk[1]:02d}"


def test_baselines_saved_by_the_same_period_survive_a_restart():
    day, week = _keys()
    before = _rm()
    before.update_unrealized(-1.0)            # the day opened with the book $1 under water
    before.update_unrealized(+0.3)            # ... and it recovered: today reads +1.30
    assert before.daily_pnl_mtm == 1.3
    saved = before.period_baselines()
    assert saved["day"] == day and saved["week"] == week and saved["day_start_unrealized"] == -1.0

    after = _rm()                              # the restart
    after.seed_period_baselines(saved)
    after.update_unrealized(+0.3)             # first mark of the new process: the same book
    assert after.daily_pnl_mtm == 1.3         # not 0.0
    assert after.weekly_pnl_mtm == 1.3


def test_baselines_from_another_period_are_ignored():
    stale = {"day": "2000-01-01", "day_start_unrealized": -50.0, "week": "2000-W01", "week_start_unrealized": -50.0}
    rm = _rm()
    rm.seed_period_baselines(stale)
    rm.update_unrealized(+0.3)
    assert rm.daily_pnl_mtm == 0.0            # seeded by the first mark, as before
    assert rm.weekly_pnl_mtm == 0.0


def test_only_the_week_can_carry_over_when_the_day_changed():
    _, week = _keys()
    rm = _rm()
    rm.seed_period_baselines({"day": "2000-01-01", "day_start_unrealized": -9.0, "week": week, "week_start_unrealized": -2.0})
    rm.update_unrealized(+1.0)
    assert rm.daily_pnl_mtm == 0.0
    assert rm.weekly_pnl_mtm == 3.0


def test_the_persisted_state_round_trips_through_main(tmp_path, monkeypatch):
    import json
    import main as engine_main
    rm = _rm()
    rm.update_unrealized(-1.0)
    fake = type("E", (), {})()
    fake.paper = True
    fake.risk_manager = rm
    fake._risk_peak_path = lambda: str(tmp_path / "risk_peak.json")
    fake._load_risk_state = lambda: engine_main.BotStrike._load_risk_state(fake)
    engine_main.BotStrike._save_risk_peak(fake, 1011.09)
    d = json.loads((tmp_path / "risk_peak.json").read_text())
    assert d["peak"] == 1011.09 and d["day_start_unrealized"] == -1.0 and d["source"] == "paper"
    assert engine_main.BotStrike._load_risk_peak(fake) == 1011.09
    assert engine_main.BotStrike._load_risk_state(fake)["week_start_unrealized"] == -1.0
