"""Full pipeline integration test for paper trading."""
import sys, os, logging
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
logging.disable(logging.CRITICAL)
import structlog
structlog.configure(wrapper_class=structlog.BoundLogger, logger_factory=structlog.ReturnLoggerFactory())

from config.settings import Settings
from core.types import Signal, Side, StrategyType, MarketRegime
from execution.paper_simulator import PaperTradingSimulator
from risk.risk_manager import RiskManager
from portfolio.portfolio_manager import PortfolioManager
from logging_metrics.logger import MetricsCollector
from trade_database.repository import TradeRepository
from trade_database.adapter import TradeDBAdapter
from core.microstructure import MicrostructureEngine

print("=== Full Paper Trading Pipeline Test ===")

settings = Settings()
rm = RiskManager(settings)
pm = PortfolioManager(settings, rm)
mc = MetricsCollector()
micro = MicrostructureEngine(symbols=settings.symbol_names, config=settings.get_microstructure_config())
repo = TradeRepository("data/trade_database.db")
tdb = TradeDBAdapter(repo, source="paper")
sim = PaperTradingSimulator(settings)

tdb.start_session(initial_equity=100000, symbol="MULTI", notes="paper test")

def process_fill(trade):
    mc.add_trade(trade)
    if trade.strategy:
        pm.update_strategy_pnl(trade.strategy, trade.pnl)
    eq = rm.current_equity + trade.pnl
    rm.update_equity(eq)
    mc.update_equity(eq)
    if trade.pnl != 0:
        rm.record_trade_result(trade.pnl)
    ms = micro.get_snapshot(trade.symbol)
    tdb.on_trade(trade, regime=MarketRegime.RANGING,
                 equity_before=rm.current_equity - trade.pnl,
                 equity_after=rm.current_equity,
                 micro_vpin=ms.vpin.vpin, micro_risk_score=ms.risk_score)

# Entry signals
sym_btc = settings.get_symbol_config("BTC-USD")
sym_eth = settings.get_symbol_config("ETH-USD")

sig1 = Signal(strategy=StrategyType.MEAN_REVERSION, symbol="BTC-USD", side=Side.BUY,
              strength=0.7, entry_price=50000, stop_loss=49500, take_profit=51000, size_usd=5000)
sig2 = Signal(strategy=StrategyType.TREND_FOLLOWING, symbol="ETH-USD", side=Side.BUY,
              strength=0.6, entry_price=3000, stop_loss=2950, take_profit=3100, size_usd=3000)

for sig, sc in [(sig1, sym_btc), (sig2, sym_eth)]:
    for t in sim.execute_signals([sig], [], sc):
        process_fill(t)

print(f"Entries: positions={sim.position_count}, equity=${rm.current_equity:,.2f}")

# SL on BTC
for t in sim.on_price_update("BTC-USD", 49400, high=49500, low=49300):
    process_fill(t)
print(f"SL BTC: positions={sim.position_count}, equity=${rm.current_equity:,.2f}, DD={rm.current_drawdown_pct:.4%}")

# TP on ETH
for t in sim.on_price_update("ETH-USD", 3110, high=3110, low=3090):
    process_fill(t)
print(f"TP ETH: positions={sim.position_count}, equity=${rm.current_equity:,.2f}")

# End session
tdb.end_session(final_equity=rm.current_equity, max_drawdown=rm.current_drawdown_pct)

# Verify DB
trades = repo.get_trades(session_id=tdb.session_id)
print(f"\nTrade DB: {len(trades)} trades")
for t in trades:
    print(f"  {t.symbol:8s} {t.side:10s} {t.strategy:18s} PnL=${t.pnl:>8.2f}")

# Verify metrics
m = mc.get_metrics()
print(f"\nMetrics: trades={m['total_trades']}, PnL=${m['total_pnl']:,.2f}, WR={m['win_rate']:.0%}")

# Verify portfolio
s = pm.get_portfolio_summary()
print(f"Portfolio: equity=${s['equity']:,.2f}, pnl={s['strategy_pnl']}")

# Verify session
sessions = repo.get_sessions(source="paper")
assert len(sessions) >= 1, "No paper session found"
ps = sessions[0]
assert ps.source == "paper"
print(f"\nSession: source={ps.source}, trades={ps.total_trades}, pnl=${ps.total_pnl:,.2f}")

# Analytics on paper trades
from analytics.performance import PerformanceAnalyzer
analyzer = PerformanceAnalyzer()
report = analyzer.analyze(trades, initial_equity=100000)
print(f"Analytics: {report.summary_str()}")

# Cleanup
repo.delete_session(tdb.session_id)
os.remove("data/trade_database.db")

print("\n=== ALL PAPER TRADING TESTS PASSED ===")
