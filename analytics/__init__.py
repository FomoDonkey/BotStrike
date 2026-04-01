"""
Analytics — Capa de análisis de rendimiento de estrategias.

Proporciona:
  - PerformanceAnalyzer: métricas por estrategia, símbolo, régimen, periodo
  - PerformanceReport: resultado estructurado de análisis
  - Funciones de riesgo: Sharpe, Sortino, Calmar, max drawdown, etc.
"""
from analytics.performance import PerformanceAnalyzer, PerformanceReport

__all__ = ["PerformanceAnalyzer", "PerformanceReport"]
