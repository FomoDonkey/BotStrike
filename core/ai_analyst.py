"""
AI Daily Analyst — Análisis estratégico diario con Claude.

NO opera en tiempo real. Se ejecuta 1 vez al día (o bajo demanda) para:
  1. Analizar trades del día, PnL, win rate
  2. Evaluar condiciones de mercado (régimen, volatilidad, tendencia)
  3. Sugerir ajustes de parámetros (SL/TP, RSI thresholds, ADX filter)
  4. Detectar patrones que las reglas fijas no capturan
  5. Generar reporte para Telegram

Uso:
    python -m core.ai_analyst              # análisis manual
    python -m core.ai_analyst --apply      # aplicar sugerencias automáticamente

Requiere: ANTHROPIC_API_KEY en .env
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

import structlog

logger = structlog.get_logger(__name__)


class AIAnalyst:
    """Analista IA que revisa performance y sugiere ajustes."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY", "")
        self._client = None
        self.last_analysis: Optional[Dict] = None
        self.analysis_history: List[Dict] = []

    @property
    def is_available(self) -> bool:
        return bool(self.api_key)

    def _get_client(self):
        if self._client is None:
            if not self.api_key:
                raise ValueError("GEMINI_API_KEY not configured")
            from google import genai
            self._client = genai.Client(api_key=self.api_key)
        return self._client

    def analyze(
        self,
        trades: List[Dict],
        equity: float,
        initial_capital: float,
        market_state: Dict,
        current_config: Dict,
    ) -> Dict:
        """Ejecuta análisis completo y retorna recomendaciones.

        Args:
            trades: Lista de trades recientes [{pnl, side, strategy, trigger, rsi, adx, ...}]
            equity: Capital actual
            initial_capital: Capital inicial
            market_state: {regime, adx, momentum, vol_pct, price, rsi}
            current_config: {leverage, sl_mult, tp_mult, rsi_oversold, rsi_overbought, adx_max}

        Returns:
            Dict con {summary, recommendations, parameter_changes, risk_assessment, confidence}
        """
        # Análisis quant profesional — no necesita LLM externo
        return self._offline_analysis(trades, equity, initial_capital, market_state, current_config)

    def _build_prompt(self, trades, equity, initial_capital, market_state, current_config) -> str:
        pnl = equity - initial_capital
        pnl_pct = pnl / initial_capital * 100
        wins = sum(1 for t in trades if t.get("pnl", 0) > 0)
        losses = len(trades) - wins
        wr = wins / len(trades) * 100 if trades else 0

        avg_win = 0
        avg_loss = 0
        if wins > 0:
            avg_win = sum(t["pnl"] for t in trades if t["pnl"] > 0) / wins
        if losses > 0:
            avg_loss = sum(t["pnl"] for t in trades if t["pnl"] <= 0) / losses

        trades_detail = ""
        for t in trades[-20:]:
            trades_detail += (
                f"  {t.get('side','?'):5s} PnL=${t.get('pnl',0):+.2f} "
                f"trigger={t.get('trigger','?')} RSI={t.get('rsi','?')} ADX={t.get('adx','?')}\n"
            )

        return f"""Analyze this crypto trading bot's performance and provide specific recommendations.

## ACCOUNT STATUS
- Capital: ${initial_capital:.0f} → ${equity:.2f} ({pnl_pct:+.1f}%)
- Total PnL: ${pnl:+.2f}
- Trades: {len(trades)} (Wins: {wins}, Losses: {losses}, WR: {wr:.1f}%)
- Avg Win: ${avg_win:+.2f}, Avg Loss: ${avg_loss:+.2f}
- R:R Ratio: {abs(avg_win/avg_loss) if avg_loss != 0 else 0:.2f}

## CURRENT MARKET STATE
- BTC Price: ${market_state.get('price', 0):,.2f}
- Regime: {market_state.get('regime', 'UNKNOWN')}
- ADX: {market_state.get('adx', 0):.1f}
- RSI (15m): {market_state.get('rsi', 50):.1f}
- Momentum: {market_state.get('momentum', 0):.5f}
- Volatility percentile: {market_state.get('vol_pct', 0.5):.2f}

## CURRENT CONFIGURATION
- Strategy: Divergence RSI+OBV on 15m bars + Order Flow Momentum
- Leverage: {current_config.get('leverage', 2)}x
- SL: {current_config.get('sl_mult', 2.0)}x ATR, TP: {current_config.get('tp_mult', 3.0)}x ATR
- RSI oversold: {current_config.get('rsi_oversold', 40)}, overbought: {current_config.get('rsi_overbought', 60)}
- ADX max: {current_config.get('adx_max', 30)}
- Risk per trade: {current_config.get('risk_pct', 1.0)}%

## RECENT TRADES
{trades_detail if trades_detail else "No trades yet."}

Respond in this exact JSON format:
{{
    "summary": "1-2 sentence assessment of current performance",
    "market_outlook": "bullish/bearish/neutral with reasoning",
    "recommendations": [
        "specific actionable recommendation 1",
        "specific actionable recommendation 2"
    ],
    "parameter_changes": {{
        "sl_mult": null or new_value,
        "tp_mult": null or new_value,
        "rsi_oversold": null or new_value,
        "rsi_overbought": null or new_value,
        "adx_max": null or new_value,
        "leverage": null or new_value,
        "risk_pct": null or new_value
    }},
    "risk_assessment": "low/medium/high with explanation",
    "confidence": 0.0 to 1.0
}}

Only suggest parameter changes when you have strong evidence. Use null for parameters that should stay unchanged. Be conservative — this is a small $300 account."""

    def _offline_analysis(self, trades, equity, initial_capital, market_state, current_config) -> Dict:
        """Análisis avanzado sin LLM — lógica de quant profesional."""
        import numpy as np

        pnl = equity - initial_capital
        pnl_pct = pnl / initial_capital * 100 if initial_capital > 0 else 0
        n_trades = len(trades)
        wins = [t for t in trades if t.get("pnl", 0) > 0]
        losses = [t for t in trades if t.get("pnl", 0) <= 0]
        wr = len(wins) / n_trades * 100 if n_trades > 0 else 0
        avg_win = np.mean([t["pnl"] for t in wins]) if wins else 0
        avg_loss = np.mean([abs(t["pnl"]) for t in losses]) if losses else 0
        profit_factor = sum(t["pnl"] for t in wins) / sum(abs(t["pnl"]) for t in losses) if losses else 0

        adx = market_state.get("adx", 0)
        rsi = market_state.get("rsi", 50)
        momentum = market_state.get("momentum", 0)
        vol_pct = market_state.get("vol_pct", 0.5)
        regime = market_state.get("regime", "UNKNOWN")
        price = market_state.get("price", 0)

        sl_mult = current_config.get("sl_mult", 2.0)
        tp_mult = current_config.get("tp_mult", 3.0)
        risk_pct = current_config.get("risk_pct", 1.0)
        leverage = current_config.get("leverage", 2)

        recommendations = []
        param_changes = {}

        # ── 1. Performance Analysis ───────────────────────────────
        if n_trades == 0:
            if adx > 30:
                recommendations.append(
                    f"No trades yet. ADX={adx:.0f} (trending) — divergences require ADX<30. "
                    "Waiting for consolidation is correct behavior."
                )
            else:
                recommendations.append(
                    f"No trades yet. ADX={adx:.0f} (ranging) — conditions are favorable. "
                    "Divergences are infrequent signals (~1 every 2 days). Patience required."
                )
        elif n_trades < 5:
            recommendations.append(
                f"Only {n_trades} trades — insufficient for statistical conclusions. "
                f"Need 20+ trades before adjusting parameters."
            )
        else:
            # Sufficient trades for analysis
            if wr >= 55:
                recommendations.append(
                    f"WR={wr:.0f}% is strong. Strategy is performing well. "
                    f"Consider increasing risk_pct to {min(risk_pct + 0.5, 2.0):.1f}% to compound gains."
                )
                if risk_pct < 1.5:
                    param_changes["risk_pct"] = min(risk_pct + 0.5, 2.0)
            elif wr >= 45:
                recommendations.append(
                    f"WR={wr:.0f}% is acceptable with R:R={tp_mult/sl_mult:.1f}:1. "
                    "Maintain current parameters."
                )
            elif wr >= 35:
                recommendations.append(
                    f"WR={wr:.0f}% is marginal. Review losing trades — are they mostly SL hits? "
                    "Consider widening SL to reduce stop-outs from noise."
                )
                param_changes["sl_mult"] = min(sl_mult + 0.5, 4.0)
            else:
                recommendations.append(
                    f"WR={wr:.0f}% is below breakeven ({100*sl_mult/(sl_mult+tp_mult):.0f}% needed). "
                    f"Reduce risk to {max(risk_pct * 0.5, 0.5):.1f}% until WR improves."
                )
                param_changes["risk_pct"] = max(risk_pct * 0.5, 0.5)

        # ── 2. R:R Analysis ───────────────────────────────────────
        if avg_win > 0 and avg_loss > 0:
            actual_rr = avg_win / avg_loss
            expected_rr = tp_mult / sl_mult
            if actual_rr < expected_rr * 0.7:
                recommendations.append(
                    f"Actual R:R ({actual_rr:.2f}) is 30%+ below expected ({expected_rr:.2f}). "
                    "Trades may be hitting SL before TP. Consider tightening TP or widening SL."
                )
            elif actual_rr > expected_rr * 1.3:
                recommendations.append(
                    f"Actual R:R ({actual_rr:.2f}) exceeds expected ({expected_rr:.2f}). "
                    "Strategy is capturing more than target — positive sign."
                )

        # ── 3. Drawdown Risk ─────────────────────────────────────
        if pnl_pct < -15:
            recommendations.append(
                f"CRITICAL: Account down {pnl_pct:.1f}%. Reduce leverage to 1x and risk to 0.5% "
                "until equity recovers above -10% drawdown."
            )
            param_changes["leverage"] = 1
            param_changes["risk_pct"] = 0.5
        elif pnl_pct < -10:
            recommendations.append(
                f"WARNING: Account down {pnl_pct:.1f}%. Consider halving position size."
            )
            param_changes["risk_pct"] = max(risk_pct * 0.5, 0.5)
        elif pnl_pct < -5:
            recommendations.append(
                f"Account down {pnl_pct:.1f}%. Normal variance for this strategy. "
                "Monitor but don't change parameters."
            )

        # ── 4. Market Regime Assessment ──────────────────────────
        if regime == "TRENDING_UP":
            if momentum > 0.01:
                outlook = "bullish"
                recommendations.append(
                    f"Market trending up (ADX={adx:.0f}, Mom={momentum:.4f}). "
                    "Bull divergences more likely to succeed. Bear divs risky."
                )
            else:
                outlook = "neutral"
        elif regime == "TRENDING_DOWN":
            outlook = "bearish"
            recommendations.append(
                f"Market trending down (ADX={adx:.0f}, Mom={momentum:.4f}). "
                "Bear divergences more likely to succeed. Bull divs risky."
            )
        elif regime == "RANGING":
            outlook = "neutral"
            recommendations.append(
                f"Market ranging (ADX={adx:.0f}). Ideal conditions for divergence strategy."
            )
        elif regime == "BREAKOUT":
            outlook = "volatile"
            recommendations.append(
                f"Market in breakout mode (ADX={adx:.0f}). "
                "Divergences disabled (correct). Wait for consolidation."
            )
        else:
            outlook = "uncertain"

        # ── 5. Volatility Assessment ─────────────────────────────
        if vol_pct > 0.8:
            recommendations.append(
                f"Volatility at {vol_pct:.0%} percentile (high). "
                "SL/TP will be wider in absolute terms. Position sizing auto-adjusts."
            )
        elif vol_pct < 0.2:
            recommendations.append(
                f"Volatility at {vol_pct:.0%} percentile (low). "
                "Tighter price ranges mean smaller profits per trade. "
                "Consider reducing TP to capture more frequent smaller moves."
            )
            param_changes["tp_mult"] = max(tp_mult - 0.5, 2.0)

        # ── 6. Consecutive Loss Detection ────────────────────────
        if n_trades >= 3:
            last_3 = [t.get("pnl", 0) for t in trades[-3:]]
            if all(p <= 0 for p in last_3):
                recommendations.append(
                    "3 consecutive losses detected. This can be normal variance. "
                    "Bot auto-reduces size after 4 losses. No manual intervention needed."
                )

        # ── 7. Trade Frequency Assessment ────────────────────────
        if n_trades > 0:
            hours_running = market_state.get("runtime_hours", 24)
            trades_per_day = n_trades / max(hours_running / 24, 0.1)
            if trades_per_day < 0.3:
                recommendations.append(
                    f"Trade frequency: {trades_per_day:.1f}/day (low). Divergences are rare by design. "
                    "Consider relaxing RSI thresholds (40→45 oversold, 60→55 overbought) "
                    "for more signals at cost of lower quality."
                )
            elif trades_per_day > 3:
                recommendations.append(
                    f"Trade frequency: {trades_per_day:.1f}/day (high for divergence). "
                    "May be detecting false divergences. Consider tightening ADX filter."
                )

        # ── 8. Profit Factor Assessment ──────────────────────────
        if n_trades >= 10 and profit_factor > 0:
            if profit_factor >= 2.0:
                recommendations.append(
                    f"Profit Factor: {profit_factor:.2f} (excellent). Strategy has strong edge."
                )
            elif profit_factor >= 1.2:
                recommendations.append(
                    f"Profit Factor: {profit_factor:.2f} (good). Positive expectancy confirmed."
                )
            elif profit_factor >= 1.0:
                recommendations.append(
                    f"Profit Factor: {profit_factor:.2f} (breakeven). Edge is marginal. "
                    "Fees may erode profits."
                )
            else:
                recommendations.append(
                    f"Profit Factor: {profit_factor:.2f} (negative edge). "
                    "Strategy is losing money after costs."
                )

        # ── Risk Assessment ──────────────────────────────────────
        if pnl_pct < -10:
            risk = "critical"
        elif pnl_pct < -5 or (n_trades >= 10 and wr < 35):
            risk = "high"
        elif pnl_pct < 0 or (n_trades >= 5 and wr < 45):
            risk = "medium"
        else:
            risk = "low"

        # Confidence based on sample size
        if n_trades >= 30:
            confidence = 0.8
        elif n_trades >= 10:
            confidence = 0.6
        elif n_trades >= 5:
            confidence = 0.4
        else:
            confidence = 0.2

        # Summary
        if n_trades == 0:
            summary = f"No trades yet. Account at ${equity:.2f}. Waiting for divergence signals."
        else:
            summary = (
                f"Account: ${equity:.2f} ({pnl_pct:+.1f}%). "
                f"{n_trades} trades, {wr:.0f}% WR, PF={profit_factor:.2f}. "
                f"Market: {regime} (ADX={adx:.0f})."
            )

        result = {
            "summary": summary,
            "market_outlook": outlook if 'outlook' in dir() else "neutral",
            "recommendations": recommendations,
            "parameter_changes": {k: v for k, v in param_changes.items()},
            "risk_assessment": risk,
            "confidence": confidence,
            "stats": {
                "trades": n_trades,
                "win_rate": round(wr, 1),
                "profit_factor": round(profit_factor, 2),
                "avg_win": round(avg_win, 2),
                "avg_loss": round(avg_loss, 2),
                "pnl_pct": round(pnl_pct, 2),
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": "quant_analysis",
        }
        self.last_analysis = result
        self.analysis_history.append(result)
        return result

    def format_telegram(self, analysis: Dict) -> str:
        """Formatea análisis para notificación de Telegram."""
        source = analysis.get("source", "?")
        summary = analysis.get("summary", "No analysis")
        outlook = analysis.get("market_outlook", "?")
        risk = analysis.get("risk_assessment", "?")
        confidence = analysis.get("confidence", 0)
        recs = analysis.get("recommendations", [])
        changes = analysis.get("parameter_changes", {})

        text = f"🤖 <b>AI Daily Analysis</b> ({source})\n\n"
        text += f"📊 {summary}\n"
        text += f"🔮 Outlook: <b>{outlook}</b>\n"
        text += f"⚠️ Risk: <b>{risk}</b> (confidence: {confidence:.0%})\n\n"

        if recs:
            text += "📋 <b>Recommendations:</b>\n"
            for r in recs:
                text += f"  • {r}\n"

        active_changes = {k: v for k, v in changes.items() if v is not None}
        if active_changes:
            text += "\n🔧 <b>Suggested changes:</b>\n"
            for k, v in active_changes.items():
                text += f"  • {k}: → {v}\n"

        return text


# CLI entry point
if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    from dotenv import load_dotenv
    load_dotenv()

    from config.settings import Settings
    settings = Settings()

    analyst = AIAnalyst()
    print(f"AI Analyst: {'Claude API' if analyst.is_available else 'Offline (heuristic)'}")

    # Gather data
    trades = []
    try:
        from trade_database.repository import TradeRepository
        repo = TradeRepository("data/trade_database.db")
        sessions = repo.get_sessions(limit=1)
        if sessions:
            trades = [t.__dict__ for t in repo.get_trades(sessions[0].session_id)]
    except Exception:
        pass

    sym = settings.symbols[0]
    market_state = {"regime": "UNKNOWN", "adx": 0, "momentum": 0, "vol_pct": 0.5, "price": 0, "rsi": 50}
    current_config = {
        "leverage": sym.leverage,
        "sl_mult": sym.mr_atr_mult_sl,
        "tp_mult": sym.mr_atr_mult_tp,
        "rsi_oversold": 40,
        "rsi_overbought": 60,
        "adx_max": 30,
        "risk_pct": settings.trading.risk_per_trade_pct * 100,
    }

    result = analyst.analyze(
        trades=trades,
        equity=settings.trading.initial_capital,
        initial_capital=settings.trading.initial_capital,
        market_state=market_state,
        current_config=current_config,
    )

    print(f"\n{json.dumps(result, indent=2)}")
    print(f"\nTelegram format:\n{analyst.format_telegram(result)}")

    if "--apply" in sys.argv and result.get("parameter_changes"):
        print("\n⚠️  Auto-apply not implemented yet. Review changes manually.")
