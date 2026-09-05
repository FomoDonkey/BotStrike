"""The trend book reports how far Strike's mark sits from the reference close it signals on.

Signals come from Yahoo / Binance daily bars (the only history long and clean enough to validate
on); fills and valuation come from Strike's mark. The basis between them is logged on every run,
refreshed hourly, warned above BASIS_WARN and shown in /api/trend. It never gates a trade.
"""
import pandas as pd

from strategies.trend_daily import BASIS_WARN
from test_trend_daily import TODAY, _engine, _frame


def test_basis_is_mark_over_last_settled_close(tmp_path):
    frames = {"BTC-USD": _frame("up"), "XAU-USD": _frame("flat")}
    eng, _, _ = _engine(tmp_path, frames)
    settled_btc = float(frames["BTC-USD"].loc[:TODAY - pd.Timedelta(days=1), "close"].iloc[-1])
    settled_xau = float(frames["XAU-USD"].loc[:TODAY - pd.Timedelta(days=1), "close"].iloc[-1])
    eng.set_venue_mark("BTC-USD", settled_btc * 1.01)
    eng.set_venue_mark("XAU-USD", settled_xau * 1.04)
    out = eng._basis_snapshot(eng.store.load(["BTC-USD", "XAU-USD"], TODAY))
    assert out["BTC-USD"] == 0.01
    assert out["XAU-USD"] == 0.04 and abs(out["XAU-USD"]) > BASIS_WARN
    status = eng.status()
    assert status["basis"] == {"BTC-USD": 0.01, "XAU-USD": 0.04}
    assert status["basis_warn"] == BASIS_WARN and status["basis_ts"] > 0


def test_markets_without_a_venue_mark_are_skipped_not_guessed(tmp_path):
    eng, _, _ = _engine(tmp_path, {"BTC-USD": _frame("up")})
    assert eng._basis_snapshot(eng.store.load(["BTC-USD"], TODAY)) == {}
    assert eng.status()["basis"] == {}
