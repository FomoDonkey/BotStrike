"""
Generador de documentación PDF para BotStrike.
Produce un documento profesional con índice, arquitectura, estrategias y detalles técnicos.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fpdf import FPDF

# ── Colores ──────────────────────────────────────────────────────
C_BG_DARK = (14, 17, 23)
C_PRIMARY = (108, 92, 231)      # morado
C_ACCENT = (0, 206, 209)        # cyan
C_TEXT = (50, 50, 55)
C_TEXT_LIGHT = (100, 100, 110)
C_WHITE = (255, 255, 255)
C_LIGHT_BG = (245, 245, 250)
C_TABLE_HEADER = (108, 92, 231)
C_TABLE_ROW_ALT = (248, 247, 252)
C_CODE_BG = (40, 44, 52)
C_BORDER = (220, 220, 230)


class BotStrikePDF(FPDF):
    """PDF personalizado con header/footer y utilidades de formato."""

    def __init__(self):
        super().__init__("P", "mm", "A4")
        self.set_auto_page_break(auto=True, margin=25)
        self._toc_entries = []
        self._current_chapter = 0

    # ── Header / Footer ──────────────────────────────────────────

    def header(self):
        if self.page_no() <= 1:
            return
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(*C_TEXT_LIGHT)
        self.cell(0, 8, "BotStrike - Sistema de Trading Algoritmico", align="L")
        self.cell(0, 8, f"Pagina {self.page_no()}", align="R", new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(*C_BORDER)
        self.line(10, 14, 200, 14)
        self.ln(4)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(*C_TEXT_LIGHT)
        self.cell(0, 10, "Documento generado automaticamente | BotStrike v1.0 | Strike Finance Perpetuals", align="C")

    # ── Utilidades ───────────────────────────────────────────────

    def cover_page(self):
        self.add_page()
        self.ln(50)
        self.set_font("Helvetica", "B", 36)
        self.set_text_color(*C_PRIMARY)
        self.cell(0, 15, "BotStrike", align="C", new_x="LMARGIN", new_y="NEXT")
        self.ln(3)
        self.set_font("Helvetica", "", 16)
        self.set_text_color(*C_TEXT)
        self.cell(0, 10, "Sistema de Trading Algoritmico", align="C", new_x="LMARGIN", new_y="NEXT")
        self.cell(0, 8, "para Strike Finance Perpetuals", align="C", new_x="LMARGIN", new_y="NEXT")
        self.ln(15)
        self.set_draw_color(*C_PRIMARY)
        self.set_line_width(0.8)
        self.line(60, self.get_y(), 150, self.get_y())
        self.ln(15)
        self.set_font("Helvetica", "", 11)
        self.set_text_color(*C_TEXT_LIGHT)
        info_lines = [
            "Estrategias: Mean Reversion | Trend Following | Market Making",
            "Microestructura: VPIN | Hawkes Process | Avellaneda-Stoikov",
            "Kyle Lambda: Market Impact Estimation + Adverse Selection",
            "Activos: BTC-USD | ETH-USD | ADA-USD",
            "",
            "Arquitectura modular async/await con 12+ modulos independientes",
            "Task Supervisor con auto-restart de tasks no-criticos",
            "Backtesting realista tick-by-tick con datos reales de Strike",
            "Paper trading con pipeline identico al live",
            "Dashboard Streamlit en tiempo real",
        ]
        for line in info_lines:
            self.cell(0, 7, line, align="C", new_x="LMARGIN", new_y="NEXT")
        self.ln(25)
        self.set_font("Helvetica", "I", 9)
        self.cell(0, 6, "Documentacion Tecnica Completa", align="C", new_x="LMARGIN", new_y="NEXT")
        self.cell(0, 6, "Marzo 2026", align="C", new_x="LMARGIN", new_y="NEXT")

    def add_toc_page(self):
        self.add_page()
        self.set_font("Helvetica", "B", 22)
        self.set_text_color(*C_PRIMARY)
        self.cell(0, 12, "Indice", new_x="LMARGIN", new_y="NEXT")
        self.ln(5)
        self.set_draw_color(*C_PRIMARY)
        self.line(10, self.get_y(), 80, self.get_y())
        self.ln(8)

        for entry in self._toc_entries:
            level = entry["level"]
            title = entry["title"]
            page = entry["page"]

            if level == 1:
                self.set_font("Helvetica", "B", 11)
                self.set_text_color(*C_TEXT)
                indent = 0
            else:
                self.set_font("Helvetica", "", 10)
                self.set_text_color(*C_TEXT_LIGHT)
                indent = 8

            self.set_x(10 + indent)
            # Title
            title_w = self.get_string_width(title)
            self.cell(title_w + 2, 7, title)
            # Dots
            dots_x = 10 + indent + title_w + 2
            page_str = str(page)
            page_w = self.get_string_width(page_str)
            end_x = 200 - page_w - 2
            self.set_font("Helvetica", "", 8)
            self.set_text_color(*C_BORDER)
            dot_count = max(1, int((end_x - dots_x) / 1.5))
            self.cell(end_x - dots_x, 7, " " + "." * dot_count + " ")
            # Page number
            if level == 1:
                self.set_font("Helvetica", "B", 11)
            else:
                self.set_font("Helvetica", "", 10)
            self.set_text_color(*C_TEXT)
            self.cell(page_w + 2, 7, page_str, new_x="LMARGIN", new_y="NEXT")

    def chapter_title(self, title):
        self._current_chapter += 1
        num = self._current_chapter
        self._toc_entries.append({"level": 1, "title": f"{num}. {title}", "page": self.page_no()})
        self.set_font("Helvetica", "B", 18)
        self.set_text_color(*C_PRIMARY)
        self.cell(0, 12, f"{num}. {title}", new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(*C_PRIMARY)
        self.set_line_width(0.5)
        self.line(10, self.get_y() + 1, 200, self.get_y() + 1)
        self.ln(6)

    def section_title(self, title):
        num = self._current_chapter
        sub = sum(1 for e in self._toc_entries if e["level"] == 2 and e["title"].startswith(f"{num}.")) + 1
        full = f"{num}.{sub} {title}"
        self._toc_entries.append({"level": 2, "title": full, "page": self.page_no()})
        self.ln(3)
        self.set_font("Helvetica", "B", 13)
        self.set_text_color(80, 70, 160)
        self.cell(0, 9, full, new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def body_text(self, text):
        self.set_font("Helvetica", "", 10)
        self.set_text_color(*C_TEXT)
        self.multi_cell(0, 5.5, text)
        self.ln(2)

    def bullet_list(self, items):
        self.set_font("Helvetica", "", 10)
        self.set_text_color(*C_TEXT)
        for item in items:
            x = self.get_x()
            self.set_x(x + 5)
            self.set_font("Helvetica", "B", 10)
            self.set_text_color(*C_PRIMARY)
            self.cell(5, 5.5, "-")
            self.set_font("Helvetica", "", 10)
            self.set_text_color(*C_TEXT)
            self.multi_cell(0, 5.5, f" {item}")
            self.ln(1)
        self.ln(2)

    def info_box(self, title, text):
        self.set_fill_color(*C_LIGHT_BG)
        self.set_draw_color(*C_PRIMARY)
        y_start = self.get_y()
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(*C_PRIMARY)
        self.cell(0, 7, f"  {title}", fill=True, new_x="LMARGIN", new_y="NEXT")
        self.set_font("Helvetica", "", 9)
        self.set_text_color(*C_TEXT)
        self.multi_cell(0, 5, f"  {text}", fill=True)
        y_end = self.get_y()
        self.set_line_width(0.4)
        self.line(10, y_start, 10, y_end)
        self.ln(4)

    def code_block(self, text):
        self.set_fill_color(240, 240, 245)
        self.set_font("Courier", "", 8)
        self.set_text_color(60, 60, 70)
        lines = text.strip().split("\n")
        for line in lines:
            self.cell(0, 4.5, f"  {line}", fill=True, new_x="LMARGIN", new_y="NEXT")
        self.ln(3)

    def table(self, headers, rows, col_widths=None):
        if col_widths is None:
            n = len(headers)
            col_widths = [190 / n] * n

        # Header
        self.set_font("Helvetica", "B", 9)
        self.set_fill_color(*C_TABLE_HEADER)
        self.set_text_color(*C_WHITE)
        for i, h in enumerate(headers):
            self.cell(col_widths[i], 7, f" {h}", border=0, fill=True)
        self.ln()

        # Rows
        self.set_font("Helvetica", "", 9)
        for r_idx, row in enumerate(rows):
            if r_idx % 2 == 1:
                self.set_fill_color(*C_TABLE_ROW_ALT)
            else:
                self.set_fill_color(*C_WHITE)
            self.set_text_color(*C_TEXT)
            for i, cell in enumerate(row):
                self.cell(col_widths[i], 6, f" {cell}", border=0, fill=True)
            self.ln()
        self.ln(4)

    def check_page_space(self, needed_mm=40):
        if self.get_y() > 297 - 25 - needed_mm:
            self.add_page()


def build_pdf():
    """Genera PDF en 2 pasadas: primera captura paginas del TOC, segunda las usa."""
    # PRIMERA PASADA: generar contenido sin TOC para capturar page numbers
    pdf = _build_content(with_toc=False)
    toc_data = pdf._toc_entries[:]

    # SEGUNDA PASADA: generar con TOC (paginas ajustadas +1)
    final = _build_content(with_toc=True, toc_entries=toc_data)

    out_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "BotStrike_Documentacion.pdf")
    final.output(out_path)
    return out_path


def _build_content(with_toc=False, toc_entries=None):
    pdf = BotStrikePDF()
    pdf.set_title("BotStrike - Documentacion Tecnica")
    pdf.set_author("BotStrike Trading System")

    if with_toc and toc_entries:
        adjusted = [{"level": e["level"], "title": e["title"], "page": e["page"] + 1} for e in toc_entries]
        pdf._toc_entries = adjusted

    pdf.cover_page()

    if with_toc:
        pdf.add_toc_page()

    # Placeholder TOC (se rellenará al final)
    toc_page_num = 2

    # ═══════════════════════════════════════════════════════════════
    # 1. VISION GENERAL
    # ═══════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.chapter_title("Vision General del Sistema")

    pdf.body_text(
        "BotStrike es un sistema de trading algoritmico de grado profesional disenado "
        "para operar perpetual futures en Strike Finance, un exchange descentralizado de derivados. "
        "El sistema implementa tres estrategias complementarias que operan simultaneamente, "
        "adaptandose dinamicamente a las condiciones del mercado mediante deteccion automatica de regimen."
    )

    pdf.section_title("Objetivo")
    pdf.body_text(
        "Generar retornos consistentes en mercados de criptomonedas mediante la combinacion de "
        "estrategias no correlacionadas (mean reversion, trend following, market making) que se "
        "complementan en distintos regimenes de mercado. El sistema prioriza la gestion de riesgo "
        "y la proteccion de capital sobre la maximizacion de retornos."
    )

    pdf.section_title("Caracteristicas Principales")
    pdf.bullet_list([
        "3 estrategias simultaneas con asignacion dinamica de capital por regimen",
        "Microestructura avanzada: VPIN, Hawkes Process, Avellaneda-Stoikov mejorado",
        "Microprice (Stoikov 2018): fair value superior al mid-price en todas las decisiones",
        "Order Book Imbalance: alpha multi-nivel con exponential decay y delta tracking",
        "Execution Intelligence: Smart Router, Fill Probability, Queue Model, TWAP, Spread Predictor",
        "Modelos cuantitativos: Volatility Targeting, Kelly Criterion, Risk of Ruin, Monte Carlo",
        "Risk Parity: covarianza rolling + inverse-vol weighting para allocation optima",
        "Correlation Regime: detecta stress (corr>0.85) y reduce exposicion automaticamente",
        "Gestion de riesgo multicapa: drawdown, exposure, circuit breaker, funding rate",
        "Paper trading con pipeline identico al live (sin divergencia de comportamiento)",
        "Backtesting realista tick-by-tick con datos reales de Strike Finance",
        "Dashboard Streamlit en tiempo real con 4 paginas de monitoreo",
        "Recoleccion continua de datos de mercado (WebSocket + REST) a Parquet",
        "Base de datos SQLite de trades con analytics multi-dimensional",
        "Walk-forward optimization y stress testing",
        "Slippage real measurement: tracking de expected vs fill price en cada trade",
        "Kyle Lambda: estimacion de impacto permanente de mercado (Cov(dP,Q)/Var(Q))",
        "Adverse Selection Measurement: mark-to-market de fills tras T+5min",
        "Impact Stress: bloqueo/reduccion automatica cuando lambda es extremo",
        "Task Supervisor: auto-restart de tasks no-criticos, shutdown tras 3 crashes criticos",
        "Slippage avanzado de 8 componentes (incluye permanent impact via Kyle Lambda)",
    ])

    pdf.section_title("Activos Soportados")
    pdf.table(
        ["Simbolo", "Leverage", "Posicion Max", "VPIN Bucket", "Estrategia Primaria"],
        [
            ["BTC-USD", "10x", "$20,000", "$50,000", "Todas"],
            ["ETH-USD", "15x", "$15,000", "$10,000", "Todas"],
            ["ADA-USD", "20x", "$5,000", "$500", "Todas"],
        ],
        [30, 25, 35, 35, 65],
    )

    # ═══════════════════════════════════════════════════════════════
    # 2. ARQUITECTURA
    # ═══════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.chapter_title("Arquitectura del Sistema")

    pdf.body_text(
        "BotStrike sigue una arquitectura modular basada en eventos con 12+ modulos independientes. "
        "Cada modulo tiene una responsabilidad unica y se comunica con los demas a traves de interfaces "
        "bien definidas. El sistema es completamente asincrono (asyncio) para manejar multiples "
        "fuentes de datos y simbolos concurrentemente. "
        "Un Task Supervisor monitorea todos los loops criticos: si un task no-critico (metrics, data_refresh) "
        "crashea, se reinicia automaticamente. Si un task critico falla 3 veces, el sistema hace shutdown seguro."
    )

    pdf.section_title("Modulos del Sistema")
    pdf.table(
        ["Modulo", "Directorio", "Responsabilidad"],
        [
            ["Configuracion", "config/", "Parametros globales, por simbolo y por estrategia"],
            ["Core Types", "core/types.py", "Enums, dataclasses compartidas (Signal, Order, Position)"],
            ["Market Data", "core/market_data.py", "Recoleccion OHLCV, indicadores tecnicos, deteccion stale"],
            ["Indicadores", "core/indicators.py", "ATR, EMA, RSI, ADX, Z-score, Bollinger, momentum"],
            ["Regimen", "core/regime_detector.py", "Clasificacion: RANGING, TRENDING, BREAKOUT"],
            ["Microestructura", "core/microstructure.py", "VPIN, Hawkes, A-S engine, Kyle Lambda"],
            ["Microprice", "core/microprice.py", "Fair value L1, Multi-Level, Adjusted (intensity+OBI)"],
            ["OBI Alpha", "core/orderbook_alpha.py", "Order Book Imbalance multi-nivel con delta"],
            ["Quant Models", "core/quant_models.py", "VolTarget, Kelly, RoR, MonteCarlo, CorrRegime"],
            ["Estrategias", "strategies/", "Mean Reversion, Trend Following, Market Making"],
            ["Riesgo", "risk/risk_manager.py", "Drawdown, exposure, RoR, VolTarget, Kelly, CorrStress"],
            ["Portfolio", "portfolio/portfolio_manager.py", "Regime allocation + Risk Parity blend"],
            ["Smart Router", "execution/smart_router.py", "Fill prob, queue, TWAP, spread pred, analytics"],
            ["Ejecucion", "execution/", "Order engine, paper simulator, slippage model"],
            ["Exchange", "exchange/", "REST client Ed25519, WebSocket market+user"],
            ["Backtesting", "backtesting/", "Backtester bar-by-bar y tick-by-tick realista"],
            ["Trade DB", "trade_database/", "SQLite persistence, analytics adapter"],
            ["Dashboard", "dashboard/", "Streamlit multi-page (live, backtest, risk, admin)"],
        ],
        [30, 42, 118],
    )

    pdf.section_title("Flujo de Datos en Tiempo Real")
    pdf.body_text(
        "El flujo principal del sistema en modo live/paper sigue este ciclo cada 5 segundos "
        "(MR/TF) o cada 500ms (MM):"
    )
    pdf.bullet_list([
        "1. WebSocket recibe trades y orderbook tick-by-tick de Strike Finance",
        "2. MarketDataCollector agrega ticks en barras OHLCV de 1 minuto con indicadores",
        "3. MicrostructureEngine actualiza VPIN y Hawkes por cada trade",
        "3b. KyleLambdaEstimator computa impacto permanente via Cov(dP,Q)/Var(Q) rolling",
        "4. TradeIntensityModel clasifica trades como buy/sell y calcula intensidad bidireccional",
        "5. RegimeDetector clasifica el mercado (RANGING / TRENDING / BREAKOUT)",
        "6. MicropriceCalculator computa fair value ajustado (superior al mid_price)",
        "7. OrderBookImbalance calcula presion de compra/venta multi-nivel",
        "8. PortfolioManager calcula allocation con Risk Parity blend + regime weights",
        "9. Cada estrategia genera senales con OBI confirmation + Kelly sizing",
        "10. RiskManager valida: RoR, VolTarget, CorrStress, impact stress, drawdown, micro, funding",
        "11. SmartOrderRouter decide limit vs market por modelo de costos",
        "12. OrderExecutionEngine (o PaperSimulator) ejecuta con slippage tracking",
        "13. ExecutionAnalytics mide implementation shortfall y calibra modelos",
    ])

    pdf.section_title("Modos de Operacion")
    pdf.table(
        ["Modo", "Comando", "Descripcion"],
        [
            ["Live Trading", "python main.py", "Ordenes reales al exchange Strike Finance"],
            ["Paper Trading", "python main.py --paper", "Datos reales de mainnet, fills simulados, PnL completo"],
            ["Dry Run", "python main.py --dry-run", "Monitoreo live sin ordenes ni simulacion"],
            ["Backtest Basico", "python main.py --backtest", "Bar-by-bar con datos sinteticos o CSV"],
            ["Backtest Realista", "python main.py --backtest-realistic", "Tick-by-tick con microestructura completa"],
            ["Backtest Real", "python main.py --backtest-real", "Con datos recolectados de Strike"],
            ["Stress Test", "python main.py --backtest-stress", "Inyeccion de eventos extremos"],
            ["Walk-Forward", "python main.py --walk-forward", "Validacion out-of-sample por folds"],
            ["Optimizer", "python main.py --optimize", "Grid search de parametros"],
            ["Data Collector", "python main.py --collect-data", "Recoleccion continua a Parquet"],
            ["Dashboard", "python main.py --dashboard", "Streamlit en localhost:8501"],
        ],
        [35, 62, 93],
    )

    # ═══════════════════════════════════════════════════════════════
    # 3. DETECCION DE REGIMEN
    # ═══════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.chapter_title("Deteccion de Regimen de Mercado")

    pdf.body_text(
        "El RegimeDetector clasifica continuamente el estado del mercado para cada simbolo. "
        "Esta clasificacion determina que estrategias se activan y cuanto capital se les asigna. "
        "El detector usa multiples senales con thresholds adaptativos por activo y un mecanismo "
        "de suavizado para evitar whipsaws (cambios bruscos)."
    )

    pdf.section_title("Regimenes Definidos")
    pdf.table(
        ["Regimen", "Condicion", "Estrategia Primaria", "Peso MM"],
        [
            ["RANGING", "Baja volatilidad, ADX bajo", "Mean Reversion (45%)", "45%"],
            ["TRENDING_UP", "ADX alto, momentum positivo, EMA cross up", "Trend Following (70%)", "20%"],
            ["TRENDING_DOWN", "ADX alto, momentum negativo, EMA cross down", "Trend Following (70%)", "20%"],
            ["BREAKOUT", "Alta volatilidad + momentum fuerte", "Trend Following (85%)", "10%"],
            ["UNKNOWN", "Datos insuficientes", "Balanceado (30/40/30)", "30%"],
        ],
        [35, 55, 55, 45],
    )

    pdf.section_title("Senales de Clasificacion")
    pdf.bullet_list([
        "Volatility Percentile (vol_pct): percentil de volatilidad en ventana de 100 barras",
        "ADX (Average Directional Index): fuerza de tendencia de 0 a 100",
        "Momentum 20 periodos: retorno porcentual en las ultimas 20 barras",
        "EMA Crossover: cruce de EMA rapida (12) sobre EMA lenta (26)",
    ])

    pdf.section_title("Thresholds Adaptativos")
    pdf.body_text(
        "Los thresholds no son fijos - se recalculan continuamente basandose en los ultimos 500 "
        "datos de cada activo. Esto permite que BTC (menos volatil relativamente) y ADA (muy volatil) "
        "tengan umbrales apropiados a su naturaleza:"
    )
    pdf.bullet_list([
        "vol_low: percentil 30 de la distribucion historica de vol_pct (min 0.2)",
        "vol_high: percentil 75 de la distribucion historica de vol_pct (max 0.9)",
        "adx_trend: percentil 60 de ADX historico (min 20.0)",
        "mom_threshold: percentil 65 de |momentum| historico (min 0.005)",
    ])

    pdf.section_title("Suavizado Anti-Whipsaw")
    pdf.body_text(
        "Para evitar cambios de regimen erraticos que causarian entrada/salida constante de estrategias, "
        "el detector requiere 2 detecciones consecutivas del mismo regimen para confirmar un cambio. "
        "Si la deteccion actual no coincide con la anterior, mantiene el ultimo regimen estable."
    )

    # ═══════════════════════════════════════════════════════════════
    # 4. ESTRATEGIA: MEAN REVERSION
    # ═══════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.chapter_title("Estrategia: Mean Reversion")

    pdf.body_text(
        "La estrategia Mean Reversion opera bajo la premisa de que los precios tienden a regresar "
        "a su media estadistica. Cuando el precio se aleja significativamente (medido por Z-score), "
        "la estrategia abre una posicion esperando la reversion."
    )

    pdf.info_box("Regimen Optimo", "RANGING - Mercados laterales donde el precio oscila alrededor de una media estable.")

    pdf.section_title("Logica de Entrada")
    pdf.body_text("Se generan senales de entrada cuando se cumplen TODAS las condiciones:")

    pdf.check_page_space(50)
    pdf.table(
        ["Direccion", "Z-score", "RSI", "Datos Minimos"],
        [
            ["LONG (compra)", "< -threshold (debajo de media)", "< 35 (sobreventa)", "lookback barras (100)"],
            ["SHORT (venta)", "> +threshold (encima de media)", "> 65 (sobrecompra)", "lookback barras (100)"],
        ],
        [35, 55, 45, 55],
    )

    pdf.section_title("Threshold Dinamico")
    pdf.body_text(
        "El threshold de Z-score no es fijo. Se ajusta segun la volatilidad relativa actual (vol_pct):"
    )
    pdf.code_block(
        "entry_threshold = mr_zscore_entry * (0.8 + 0.4 * vol_pct)\n"
        "\n"
        "Ejemplo con mr_zscore_entry = 2.0:\n"
        "  Baja volatilidad (vol_pct=0.1):  threshold = 2.0 * 0.84 = 1.68  (mas senales)\n"
        "  Media (vol_pct=0.5):              threshold = 2.0 * 1.00 = 2.00  (normal)\n"
        "  Alta volatilidad (vol_pct=0.9):   threshold = 2.0 * 1.16 = 2.32  (filtra ruido)"
    )

    pdf.section_title("Stop Loss y Take Profit")
    pdf.body_text("Ambos son dinamicos basados en ATR (Average True Range):")
    pdf.bullet_list([
        "Stop Loss = precio +/- ATR x 1.5 (mr_atr_mult_sl)",
        "Take Profit = precio +/- ATR x 2.5 (mr_atr_mult_tp)",
        "Ratio riesgo/recompensa: 1:1.67",
    ])

    pdf.section_title("Signal Strength")
    pdf.body_text(
        "La fuerza de la senal (0 a 1) es proporcional a cuanto se alejo el precio de la media. "
        "Esto permite que el sistema de ejecucion ajuste el tipo de orden:"
    )
    pdf.code_block("strength = min(|zscore| / (entry_threshold * 2), 1.0)")

    pdf.section_title("Senal de Salida")
    pdf.body_text(
        "Cuando el Z-score regresa hacia la media (|zscore| < mr_zscore_exit = 0.5), se genera "
        "una senal de cierre. Esto captura la reversion sin esperar a que SL/TP se activen."
    )

    pdf.section_title("Order Book Imbalance (Confirmacion)")
    pdf.body_text(
        "MR usa el OBI como filtro de confirmacion. Si la imbalance del orderbook favorece la direccion "
        "del trade (buy pressure para longs, sell pressure para shorts), el strength de la senal "
        "recibe un bonus de hasta +15%. Esto mejora el win rate 3-7% sin reducir el numero de trades."
    )

    pdf.section_title("Kelly Criterion (Sizing Optimo)")
    pdf.body_text(
        "Si hay 50+ trades de historial, el sizing usa Half-Kelly capped (0.5% a 3%) en vez del "
        "2% fijo. Kelly se calcula por estrategia separadamente, adaptandose al edge real de MR."
    )

    pdf.section_title("Proteccion de Microestructura")
    pdf.body_text(
        "El RiskManager bloquea entradas de MR cuando VPIN es toxico (>= 0.6) o Hawkes spike ratio "
        ">= 3.0. El razonamiento: flujo informado (VPIN alto) puede romper la reversion, causando "
        "perdidas cuando el precio no revierte."
    )

    # ═══════════════════════════════════════════════════════════════
    # 5. ESTRATEGIA: TREND FOLLOWING
    # ═══════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.chapter_title("Estrategia: Trend Following")

    pdf.body_text(
        "Trend Following captura movimientos direccionales del mercado entrando en rompimientos "
        "y cruces de medias moviles. Usa trailing stops para dejar correr las ganancias y "
        "cortar perdidas rapido."
    )

    pdf.info_box("Regimen Optimo", "TRENDING_UP, TRENDING_DOWN, BREAKOUT - Mercados con direccion clara y momentum fuerte.")

    pdf.section_title("Senales de Entrada")
    pdf.body_text("Se requiere al menos UNA de estas condiciones de entrada, mas filtros:")

    pdf.bullet_list([
        "Cruce de EMA: EMA rapida (12) cruza sobre EMA lenta (26) - senal principal",
        "Breakout: precio rompe Bollinger Band superior (long) o inferior (short)",
        "Filtro de regimen: debe coincidir con TRENDING_UP/DOWN o BREAKOUT",
        "Filtro de volumen: vol_ratio > 1.2 (volumen por encima de su media de 20 periodos)",
        "Filtro de momentum: momentum > 0 para long, < 0 para short (confirmacion direccional)",
    ])

    pdf.section_title("Trailing Stop")
    pdf.body_text(
        "En vez de stop loss fijo, TF usa un trailing stop que se mueve a favor de la posicion:"
    )
    pdf.code_block(
        "trail_distance = ATR * tf_atr_mult_trail  (default: ATR * 2.0)\n"
        "\n"
        "LONG:  new_stop = max(current_stop, price - trail_distance)\n"
        "SHORT: new_stop = min(current_stop, price + trail_distance)\n"
        "\n"
        "Si price <= new_stop (long) o price >= new_stop (short) -> cierre"
    )

    pdf.section_title("Take Profit")
    pdf.body_text("Ratio 3:1 respecto al riesgo del stop loss:")
    pdf.code_block(
        "LONG:  take_profit = price + 3.0 * (price - stop_loss)\n"
        "SHORT: take_profit = price - 3.0 * (stop_loss - price)"
    )

    pdf.section_title("Ajuste por Fuerza de Tendencia")
    pdf.body_text(
        "El capital asignado se reduce proporcionalmente al ADX (fuerza de tendencia). "
        "Con ADX bajo, la posicion es mas pequena; con ADX alto, es mas agresiva:"
    )
    pdf.code_block(
        "strength = min(ADX / 50, 1.0)\n"
        "adjusted_capital = allocated_capital * strength"
    )

    # ═══════════════════════════════════════════════════════════════
    # 6. ESTRATEGIA: MARKET MAKING
    # ═══════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.chapter_title("Estrategia: Market Making")

    pdf.body_text(
        "Market Making captura el spread bid-ask colocando ordenes limite a ambos lados del precio. "
        "Usa el modelo Avellaneda-Stoikov mejorado para calcular spreads optimos que balancean "
        "rentabilidad contra riesgo de inventario y adverse selection."
    )

    pdf.info_box("Regimen Optimo", "RANGING, UNKNOWN - Mercados tranquilos donde el spread se captura sin riesgo direccional excesivo.")

    pdf.section_title("Motor Avellaneda-Stoikov Mejorado")
    pdf.body_text("El motor calcula precios optimos de bid/ask con estas formulas:")

    pdf.code_block(
        "# Precio de reserva (ajustado por inventario)\n"
        "reservation_price = mid_price - inventory * ATR * gamma * T\n"
        "\n"
        "# Spread optimo\n"
        "ATR_bps = (ATR / mid_price) * 10,000\n"
        "kappa_factor = 1 / (0.5 + effective_kappa * 0.333)\n"
        "optimal_spread = ATR_bps * effective_gamma * kappa_factor * (0.5 + T)\n"
        "\n"
        "# Inventory skew (suave, no lineal)\n"
        "skew = tanh(inventory_ratio * 2) * ATR * 0.5\n"
        "\n"
        "# Precios finales\n"
        "bid = reservation_price - spread/2 - skew\n"
        "ask = reservation_price + spread/2 - skew"
    )

    pdf.section_title("Ajuste Dinamico de Gamma")
    pdf.body_text(
        "Gamma (aversion al riesgo) se incrementa automaticamente con VPIN y Hawkes, "
        "lo que ensancha el spread como proteccion:"
    )
    pdf.bullet_list([
        "VPIN multiplier: 1.0x (VPIN=0) hasta 3.0x (VPIN>=0.8)",
        "Hawkes multiplier: 1.0x (normal) hasta 2.5x (spike ratio 7x)",
        "Gamma combinado: puede llegar a ~7.5x el base, produciendo spreads de hasta 100 bps",
    ])

    pdf.section_title("Filtros de Seguridad")
    pdf.bullet_list([
        "PAUSA COMPLETA: Si VPIN >= 0.8 Y Hawkes spike simultaneo, no se generan quotes",
        "Reduccion de tamano: risk_score > 0.3 reduce sizing hasta 60%",
        "Spread minimo: siempre >= 2x fee medio (7 bps) para cubrir costos",
        "Spread maximo: 100 bps (defensivo, no captura spread pero protege capital)",
        "Inventory limits: no genera bids si inventory >= max, no genera asks si inventory <= -max",
    ])

    pdf.section_title("Unwind de Inventario")
    pdf.body_text(
        "Cuando el regimen cambia y MM se desactiva (ej: RANGING -> TRENDING), el inventario "
        "existente se cierra con una market order. Sin esto, la posicion de MM quedaria expuesta "
        "a riesgo direccional sin limites."
    )

    # ═══════════════════════════════════════════════════════════════
    # 7. MICROESTRUCTURA
    # ═══════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.chapter_title("Indicadores de Microestructura")

    pdf.body_text(
        "BotStrike incorpora tres indicadores avanzados de microestructura de mercado que operan "
        "a nivel de tick individual (no solo por barra). Estos indicadores alimentan las decisiones "
        "de todas las estrategias y del risk manager."
    )

    pdf.section_title("VPIN (Volume-Synchronized Probability of Informed Trading)")
    pdf.body_text(
        "VPIN mide la probabilidad de que traders informados esten operando. Un VPIN alto indica "
        "desequilibrio en el flujo de ordenes, lo que sugiere que el precio puede moverse bruscamente."
    )
    pdf.bullet_list([
        "Algoritmo: Bulk Volume Classification clasifica cada trade como compra/venta",
        "Agrupacion en buckets de volumen fijo (BTC: $50k, ETH: $10k, ADA: $500)",
        "VPIN = media de |buy_vol - sell_vol| / total_vol sobre N buckets",
        "Threshold toxico: 0.6 (flujo informado detectado)",
        "Impacto: MR bloqueado, MM ensancha spread o pausa, TF puede confirmar momentum",
    ])

    pdf.section_title("Hawkes Process (Deteccion de Picos de Actividad)")
    pdf.body_text(
        "El proceso de Hawkes modela la auto-excitacion en eventos de trading. "
        "Cuando un cluster de trades ocurre, la intensidad sube temporalmente, "
        "detectando picos anomalos de actividad."
    )
    pdf.code_block(
        "lambda(t) = mu + SUM[ alpha * exp(-beta * (t - t_i)) ]\n"
        "\n"
        "mu    = 1.0   (intensidad base)\n"
        "alpha = 0.5   (factor de excitacion)\n"
        "beta  = 2.0   (tasa de decaimiento)\n"
        "spike = intensity > mu_original * 2.5"
    )
    pdf.body_text(
        "Nota: el threshold de spike usa mu ORIGINAL (fijo), no el adaptativo. "
        "Esto evita que el mu adaptativo absorba los spikes, haciendolos indetectables."
    )

    pdf.section_title("Kyle Lambda (Market Impact Estimation)")
    pdf.body_text(
        "Kyle Lambda estima cuanto mueve $1 de volumen signed el precio permanentemente. "
        "Es el eslabon entre deteccion de toxicidad (VPIN/Hawkes) y optimizacion de ejecucion. "
        "Lambda alto = mercado iliquido o dominado por informed traders."
    )
    pdf.code_block(
        "# Estimacion rolling incremental\n"
        "lambda = Cov(delta_P, Q) / Var(Q)\n"
        "\n"
        "delta_P = cambio de precio en bps\n"
        "Q = signed volume en USD (buy=+, sell=-)\n"
        "\n"
        "# Smoothing\n"
        "lambda_ema = EMA(lambda, span=100)\n"
        "\n"
        "# Impact estimation\n"
        "permanent_impact = lambda_ema * sqrt(size/depth)"
    )
    pdf.body_text("Integracion en el sistema:")
    pdf.bullet_list([
        "A-S Engine: gamma escala 1.0x a 1.5x con impact_stress (lambda alto = spreads mas anchos)",
        "Smart Router: penaliza market orders sumando permanent_impact al costo estimado",
        "Risk Manager: impact_stress >= 1.5 bloquea trades, > 0.5 reduce sizing",
        "Slippage Model: componente 8 -- permanent impact = lambda * sqrt(size/depth)",
        "Paper Simulator: aplica permanent impact en fills simulados",
        "Adverse Selection: registra fills y mide mark-to-market tras 5 minutos",
    ])
    pdf.info_box("Impact Stress Score",
        "0.0 = normal, 0.5 = moderado (reduce sizing), 1.5+ = extremo (bloquea trades). "
        "Se calcula como lambda_ema / 2.0, capped a 2.0.")

    pdf.section_title("Risk Score Combinado")
    pdf.body_text("Los indicadores se combinan en un risk_score unico (0 a 1):")
    pdf.code_block(
        "vpin_score  = min(vpin / 0.8, 1.0)\n"
        "hawkes_score = min((spike_ratio - 1) / 3, 1.0)\n"
        "risk_score  = max(vpin_score, hawkes_score)"
    )

    # ═══════════════════════════════════════════════════════════════
    # 8. GESTION DE RIESGO
    # ═══════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.chapter_title("Gestion de Riesgo")

    pdf.body_text(
        "El RiskManager es la ultima linea de defensa antes de la ejecucion. Toda senal generada "
        "por las estrategias DEBE pasar por validacion de riesgo (excepto unwinds de emergencia)."
    )

    pdf.section_title("Capas de Proteccion")
    pdf.table(
        ["Capa", "Parametro", "Accion"],
        [
            ["Microestructura", "VPIN toxico / Hawkes spike", "Bloquea MR, reduce sizing 50%"],
            ["Funding Rate", ">= 5 bps/8h contra posicion", "Bloquea entrada"],
            ["Funding Rate", ">= 1 bps/8h contra posicion", "Reduce sizing 30%"],
            ["Circuit Breaker", "Drawdown > 80% del max (12%)", "Pausa 5 minutos"],
            ["Max Drawdown", ">= 15% del equity peak", "Bloquea todas las senales"],
            ["Exposicion Total", "> 80% del equity", "Bloquea senales nuevas"],
            ["Exposicion por Activo", "> max_position_usd", "Reduce tamano"],
            ["Leverage", "Margen > 50% equity", "Reduce tamano"],
            ["Perdidas Consecutivas", ">= 3 consecutivas", "Reduce sizing exponencial"],
            ["Stop Loss Dinamico", "Drawdown > 50% max", "Aprieta SL hasta 30%"],
            ["Risk of Ruin", "RoR > 3%", "Reduce sizing 50%. RoR > 10%: pausa total"],
            ["Volatility Targeting", "Vol realizada vs 15% target", "Escala posiciones (0.5x a 2.0x)"],
            ["Correlation Stress", "Avg corr > 0.85", "Reduce exposicion hasta 60%"],
            ["Impact Stress (Kyle Lambda)", "lambda_ema > threshold", "Stress>=1.5: bloquea. >0.5: reduce sizing"],
            ["Kelly Criterion", "50+ trades historial", "Half-Kelly capped (0.5% a 3%)"],
        ],
        [35, 55, 100],
    )

    pdf.section_title("Position Sizing")
    pdf.body_text("El tamano de posicion se calcula por riesgo, no por capital disponible:")
    pdf.code_block(
        "risk_amount = capital * risk_per_trade_pct  (2%)\n"
        "risk_per_unit = |price - stop_loss|\n"
        "size_units = risk_amount / risk_per_unit\n"
        "max_units = (capital * leverage) / price\n"
        "final_size = min(size_units, max_units)"
    )

    pdf.section_title("Reduccion por Perdidas Consecutivas")
    pdf.body_text("Tras 3+ perdidas consecutivas, el tamano se reduce exponencialmente:")
    pdf.code_block(
        "reduction = 0.5 ^ (consecutive_losses - 2)\n"
        "\n"
        "3 perdidas: 50%  del tamano normal\n"
        "4 perdidas: 25%  del tamano normal\n"
        "5 perdidas: 12.5% del tamano normal"
    )

    # ═══════════════════════════════════════════════════════════════
    # 9. PORTFOLIO MANAGER
    # ═══════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.chapter_title("Gestion de Portfolio")

    pdf.body_text(
        "El PortfolioManager distribuye el capital entre estrategias y simbolos de forma dinamica, "
        "considerando el regimen actual, el performance historico de cada estrategia, y el nivel de drawdown."
    )

    pdf.section_title("Asignacion por Regimen")
    pdf.table(
        ["Regimen", "Mean Reversion", "Trend Following", "Market Making"],
        [
            ["RANGING", "45%", "10%", "45%"],
            ["TRENDING_UP", "10%", "70%", "20%"],
            ["TRENDING_DOWN", "10%", "70%", "20%"],
            ["BREAKOUT", "5%", "85%", "10%"],
            ["UNKNOWN", "30%", "40%", "30%"],
        ],
        [45, 45, 50, 50],
    )

    pdf.section_title("Factores de Ajuste")
    pdf.bullet_list([
        "Performance factor (0.5 a 1.5): sigmoid basado en PnL promedio por trade de la estrategia",
        "Drawdown factor (0.3 a 1.0): reduce asignacion general conforme drawdown aumenta",
        "Risk Parity blend (30%): inverse-vol weighting ajusta pesos por volatilidad realizada",
        "Symbol share: distribucion equitativa entre simbolos (1/N)",
    ])
    pdf.code_block(
        "# Base allocation\n"
        "allocation = equity * regime_weight * perf_factor * dd_factor * symbol_share\n"
        "\n"
        "# Risk Parity adjustment (70% regime + 30% inverse-vol)\n"
        "rp_ratio = risk_parity_weight / neutral_weight\n"
        "base_weight = 0.7 * regime_weight + 0.3 * regime_weight * min(rp_ratio, 2.0)"
    )

    pdf.section_title("Desactivacion de Estrategias")
    pdf.body_text(
        "Una estrategia se desactiva automaticamente si: su peso por regimen es < 8% (no vale la pena "
        "operar con tan poco capital), o su performance factor < 0.6 (esta perdiendo consistentemente)."
    )

    # ═══════════════════════════════════════════════════════════════
    # 10. EJECUCION Y PAPER TRADING
    # ═══════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.chapter_title("Ejecucion de Ordenes y Paper Trading")

    pdf.section_title("Smart Order Router")
    pdf.body_text(
        "La decision limit vs market NO es hardcodeada - usa un modelo de costos que compara"
        "el costo esperado de cada alternativa y elige la mas barata:"
    )
    pdf.code_block(
        "# Costo de MARKET order\n"
        "market_cost = half_spread + size_impact + taker_fee + lambda_impact\n"
        "\n"
        "# Costo de LIMIT order\n"
        "limit_cost = (1-P(fill)) * opportunity_cost + P(fill) * (maker_fee - price_improvement)\n"
        "\n"
        "# Decision\n"
        "decision = MARKET if market_cost <= limit_cost else LIMIT"
    )
    pdf.body_text("Casos especiales que se resuelven sin modelo:")
    pdf.table(
        ["Contexto", "Decision", "Razon"],
        [
            ["Market Making", "LIMIT + post_only", "Capturar maker rebate siempre"],
            ["Salida urgente (SL/TP/exit)", "MARKET", "Prioridad = velocidad"],
            ["Spread < 3 bps", "MARKET", "Spread tan tight que limit no mejora"],
            ["Orden > $10k", "TWAP split", "Reduce market impact (sqrt model)"],
        ],
        [50, 40, 100],
    )

    pdf.section_title("Fill Probability Model")
    pdf.body_text(
        "Estima P(fill) para ordenes limit usando 5 factores combinados con logistic function:"
    )
    pdf.bullet_list([
        "Distancia al mid (normalizada por spread): mas lejos = menor prob",
        "Volatilidad (ATR): mayor vol = mas prob de que precio toque nuestro nivel",
        "Queue depth: mas capital delante = menor prob de fill",
        "Trade intensity: mas trades/sec = mas prob de que nos toque",
        "Horizonte temporal: mas tiempo = mas prob (sqrt scaling, Brownian motion)",
    ])

    pdf.section_title("Modelo de Slippage Avanzado (8 Componentes)")
    pdf.body_text("El slippage usa un modelo de 8 factores calibrables:")
    pdf.code_block(
        "1. Spread component: half_spread (market) o 0 (limit)\n"
        "2. Size impact: sqrt(size/depth) * 3 bps  (Almgren-Chriss concavo)\n"
        "3. Volatility scaling: 0.5 + ATR_bps/20 * 0.5\n"
        "4. Hawkes impact: (ratio-1) * 0.8 bps si spike\n"
        "5. Regime multiplier: RANGING=0.8x a BREAKOUT=2.0x\n"
        "6. OBI adverse selection: imbalance_contra * 2 bps\n"
        "7. VPIN toxicity premium: (vpin-0.4) * 3 bps si toxico\n"
        "8. Permanent impact: lambda * sqrt(size/depth) (Kyle Lambda)"
    )

    pdf.section_title("Paper Trading Simulator")
    pdf.body_text(
        "El paper simulator reemplaza al exchange en modo --paper. La clave de diseno es que los "
        "fills simulados fluyen por el MISMO pipeline que los fills reales:"
    )
    pdf.bullet_list([
        "Aplica slippage dinamico (no fijo) basado en regimen y tamano",
        "Verifica SL/TP en CADA tick de precio via on_price_update()",
        "Produce objetos Trade identicos a los del exchange real",
        "Entry: fee=0, pnl=0. Exit: fee=ambos lados, pnl=gross-fees (identico al exchange)",
        "Tracked via trade_db con source='paper' para analytics separados",
    ])

    # ═══════════════════════════════════════════════════════════════
    # 11. BACKTESTING
    # ═══════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.chapter_title("Sistema de Backtesting")

    pdf.section_title("Backtester Basico (Bar-by-Bar)")
    pdf.body_text(
        "Itera sobre barras OHLCV, aplica indicadores, detecta regimen, genera senales, "
        "y simula fills con slippage y fees. Incluye verificacion de SL/TP intra-bar "
        "y liquidaciones por leverage."
    )

    pdf.section_title("Backtester Realista (Tick-by-Tick)")
    pdf.body_text(
        "Replica EXACTAMENTE el loop de live trading. Diferencias clave vs el basico:"
    )
    pdf.bullet_list([
        "Alimenta microestructura tick-by-tick ANTES de procesar cada barra",
        "Usa PortfolioManager real con asignacion dinamica por regimen",
        "Usa RiskManager completo con TODOS los filtros (drawdown, exposure, micro, funding)",
        "Distingue fees de maker (MM) vs taker (MR/TF)",
        "Produce JSONL identico al de produccion (signal, trade, micro, regime, portfolio)",
        "Soporta orderbook real del collector (no solo simulado con ATR)",
    ])

    pdf.section_title("Metricas de Resultado")
    pdf.table(
        ["Metrica", "Formula", "Uso"],
        [
            ["Net PnL", "Suma de PnL de todos los trades", "Rentabilidad absoluta"],
            ["Win Rate", "Trades ganadores / Total trades", "Consistencia"],
            ["Profit Factor", "Gross profit / |Gross loss|", "Eficiencia (>1 = rentable)"],
            ["Sharpe Ratio", "mean(daily_ret) / std(daily_ret) * sqrt(252)", "Riesgo-ajustado"],
            ["Calmar Ratio", "Return / Max Drawdown", "Retorno vs peor caida"],
            ["Max Drawdown", "Maxima caida desde peak de equity", "Peor escenario historico"],
        ],
        [35, 75, 80],
    )

    # ═══════════════════════════════════════════════════════════════
    # 12. EXCHANGE INTEGRATION
    # ═══════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.chapter_title("Integracion con Strike Finance")

    pdf.section_title("Autenticacion Ed25519")
    pdf.body_text(
        "Strike Finance usa autenticacion criptografica Ed25519 (no HMAC-SHA como Binance). "
        "Cada request autenticado requiere:"
    )
    pdf.code_block(
        "message = f'{METHOD}:{PATH}:{TIMESTAMP}:{NONCE}:{SHA256(BODY)}'\n"
        "signature = Ed25519_sign(message, private_key)\n"
        "\n"
        "Headers:\n"
        "  X-API-Wallet-Public-Key: <public_key_hex>\n"
        "  X-API-Wallet-Signature: <signature_hex>\n"
        "  X-API-Wallet-Timestamp: <unix_seconds>\n"
        "  X-API-Wallet-Nonce: <uuid4>"
    )

    pdf.section_title("WebSocket Dual")
    pdf.bullet_list([
        "Market WS (wss://api.strikefinance.org/ws/price): trades, depth, markprice publicos",
        "User WS (wss://api.strikefinance.org/ws/user-api): fills, posiciones, balance (autenticado)",
        "Reconexion automatica con exponential backoff (1s a 60s)",
        "Re-suscripcion de canales tras cada reconexion",
        "Manejo de frames con multiples JSON separados por newline",
    ])

    pdf.section_title("Rate Limiting")
    pdf.body_text(
        "Token bucket rate limiter: 50 requests por 10 segundos. Si se alcanza el limite, "
        "el request espera automaticamente hasta que haya capacidad."
    )

    # ═══════════════════════════════════════════════════════════════
    # 13. DATA PIPELINE
    # ═══════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.chapter_title("Pipeline de Datos")

    pdf.section_title("Recoleccion Continua")
    pdf.body_text(
        "El data collector (python main.py --collect-data) recolecta datos continuamente de Strike "
        "Finance MAINNET, independientemente de si el bot opera en testnet:"
    )
    pdf.table(
        ["Tipo de Dato", "Fuente Primaria", "Backup", "Almacenamiento", "Frecuencia Flush"],
        [
            ["Trades", "WebSocket", "REST /v2/trades cada 15s", "Parquet diario por simbolo", "30s"],
            ["Klines 1m", "WebSocket", "REST /v2/klines cada 60s", "Parquet incremental", "60s"],
            ["Orderbook", "WebSocket depth", "REST /v2/depth cada 10s", "Parquet diario por simbolo", "30s"],
            ["Stats", "REST periodico", "-", "JSON metadata", "300s"],
        ],
        [28, 35, 42, 48, 37],
    )

    pdf.section_title("Formato de Almacenamiento")
    pdf.bullet_list([
        "Parquet con compresion zstd (40-60% menos que snappy)",
        "Organizacion: data/{tipo}/{simbolo}/{fecha}.parquet",
        "Klines: data/klines/{simbolo}/1m.parquet (incremental)",
        "Deduplicacion por trade_id o timestamp+price+qty",
        "Catalogo JSON con metadata de cada dataset (filas, fechas, tamano)",
    ])

    # ═══════════════════════════════════════════════════════════════
    # 14. CONFIGURACION
    # ═══════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.chapter_title("Configuracion y Parametros")

    pdf.section_title("Parametros Globales (TradingConfig)")
    pdf.table(
        ["Parametro", "Valor", "Descripcion"],
        [
            ["initial_capital", "$100,000", "Capital inicial para trading/backtest"],
            ["max_drawdown_pct", "15%", "Drawdown maximo permitido (bloquea trading)"],
            ["max_leverage", "20x", "Leverage maximo global"],
            ["max_total_exposure_pct", "80%", "Exposicion maxima vs equity"],
            ["risk_per_trade_pct", "2%", "Riesgo por trade (position sizing)"],
            ["maker_fee", "0.02%", "Fee para ordenes maker (MM)"],
            ["taker_fee", "0.05%", "Fee para ordenes taker (MR/TF)"],
            ["slippage_bps", "2 bps", "Slippage base estimado"],
            ["funding_rate_warn", "1 bps/8h", "Reduce sizing 30% contra funding"],
            ["funding_rate_block", "5 bps/8h", "Bloquea entradas contra funding"],
            ["data_stale_warn_sec", "30s", "Warning si datos > 30s sin actualizar"],
            ["data_stale_block_sec", "120s", "No opera si datos > 2min sin actualizar"],
            ["strategy_interval_sec", "5s", "Frecuencia del loop MR/TF"],
            ["mm_interval_sec", "0.5s", "Frecuencia del loop Market Making"],
        ],
        [45, 30, 115],
    )

    pdf.check_page_space(70)
    pdf.section_title("Parametros por Simbolo (SymbolConfig)")
    pdf.table(
        ["Parametro", "BTC-USD", "ETH-USD", "ADA-USD"],
        [
            ["leverage", "10x", "15x", "20x"],
            ["max_position_usd", "$20,000", "$15,000", "$5,000"],
            ["mr_zscore_entry", "2.0", "2.0", "2.0"],
            ["mr_lookback", "100", "100", "100"],
            ["mr_atr_mult_sl", "1.5", "1.5", "1.5"],
            ["mr_atr_mult_tp", "2.5", "2.5", "2.5"],
            ["tf_ema_fast / slow", "12 / 26", "12 / 26", "12 / 26"],
            ["tf_atr_mult_trail", "2.0", "2.0", "2.0"],
            ["mm_base_spread_bps", "10", "10", "10"],
            ["mm_order_levels", "3", "3", "3"],
            ["mm_order_size_usd", "$500", "$500", "$500"],
            ["vpin_bucket_size", "$50,000", "$10,000", "$500"],
            ["hawkes_mu / alpha / beta", "1.0 / 0.5 / 2.0", "1.0 / 0.5 / 2.0", "1.0 / 0.5 / 2.0"],
        ],
        [45, 50, 50, 45],
    )

    # ═══════════════════════════════════════════════════════════════
    # 15. TICK QUALITY GUARDS
    # ═══════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.chapter_title("Tick Quality Guards (WebSocket)")

    pdf.body_text(
        "El sistema incorpora guards de calidad de ticks inspirados en mejores practicas de "
        "trading de alta frecuencia. Estos filtros protegen contra datos corruptos o stale que "
        "podrian causar fills incorrectos, hedges erroneos o entradas en precios falsos."
    )

    pdf.section_title("Guards Implementados")
    pdf.table(
        ["Guard", "Parametro", "Comportamiento"],
        [
            ["Warmup Period", "5 segundos", "Descarta TODOS los ticks en los primeros 5s post-conexion WS"],
            ["First Tick Skip", "1 tick/simbolo", "Primer tick por simbolo post-conexion es snapshot cacheado, se descarta"],
            ["Stale Tick Guard", "5% max delta", "Rechaza ticks con delta > 5% vs ultimo precio aceptado"],
            ["Jitter EMA", "alpha=0.1", "Monitorea intervalo promedio entre ticks (diagnostico, no filtro)"],
        ],
        [35, 35, 120],
    )

    pdf.section_title("Diseno")
    pdf.bullet_list([
        "Guards solo activos DESPUES de on_ws_connected() (cuando WS real se conecta)",
        "Backtesting, tests y REST init NO pasan por guards (ws_connect_time=0)",
        "Metricas de calidad disponibles via get_tick_quality_stats()",
        "Logging periodico en _metrics_loop cada 60 segundos",
        "on_ws_connected() se dispara automaticamente en cada reconexion del WS",
    ])

    pdf.info_box("Limitaciones de Datos",
        "Cuando el collector se detiene (PC apagado, crash), los trades y orderbook de ese "
        "periodo se pierden. Solo las klines (velas 1m) se pueden recuperar via REST con "
        "startTime/endTime. El collector NO hace backfill automatico al arrancar.")

    # ═══════════════════════════════════════════════════════════════
    # 16. MICROPRICE Y ORDER BOOK ALPHA
    # ═══════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.chapter_title("Microprice y Order Book Alpha")

    pdf.section_title("Microprice (Stoikov 2018)")
    pdf.body_text(
        "El mid-price clasico (best_bid + best_ask) / 2 ignora la informacion contenida en las "
        "cantidades de cada lado del book. El microprice corrige esto ponderando por cantidades inversas:"
    )
    pdf.code_block(
        "microprice = ask * (bid_qty / (bid_qty + ask_qty))\n"
        "          + bid * (ask_qty / (bid_qty + ask_qty))\n"
        "\n"
        "Intuicion:\n"
        "  bid_qty >> ask_qty -> mucho soporte -> precio justo sube hacia ask\n"
        "  ask_qty >> bid_qty -> presion venta -> precio justo baja hacia bid\n"
        "  bid_qty == ask_qty -> microprice == mid_price (caso clasico)"
    )
    pdf.body_text("BotStrike implementa 3 niveles de sofisticacion:")
    pdf.bullet_list([
        "Level-1: Solo top-of-book (mas rapido, baseline institucional)",
        "Multi-Level: Pondera N niveles con decay exponencial (0.6^nivel)",
        "Adjusted: Incorpora trade intensity buy/sell y OBI momentum delta",
    ])
    pdf.info_box("Impacto", "Microprice reemplaza mid_price como fair value en: reservation price A-S, "
        "referencia de slippage, metadata de senales para smart routing.")

    pdf.section_title("Order Book Imbalance (OBI)")
    pdf.body_text(
        "OBI mide la presion relativa de compra vs venta en los niveles del orderbook. "
        "Es un predictor estadisticamente significativo de movimiento a corto plazo en crypto."
    )
    pdf.code_block(
        "# Simple imbalance\n"
        "OBI = (bid_depth - ask_depth) / (bid_depth + ask_depth)\n"
        "\n"
        "# Weighted (niveles cercanos pesan mas, decay=0.5)\n"
        "weighted_OBI = sum(bid_usd * w) - sum(ask_usd * w) / total_weighted\n"
        "\n"
        "# Delta (mas predictivo que nivel absoluto)\n"
        "delta = current_OBI - previous_OBI"
    )
    pdf.body_text("Integracion en estrategias:")
    pdf.table(
        ["Estrategia", "Uso del OBI", "Efecto"],
        [
            ["Mean Reversion", "Confirmacion de reversal", "Strength bonus hasta +15% si OBI favorable"],
            ["Trend Following", "Boost de confianza", "Strength bonus hasta +15% si OBI en direccion"],
            ["Market Making", "Spread skew", "Desplaza fair value segun presion del book"],
        ],
        [40, 55, 95],
    )

    # ═══════════════════════════════════════════════════════════════
    # 17. MODELOS CUANTITATIVOS AVANZADOS
    # ═══════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.chapter_title("Modelos Cuantitativos Avanzados")

    pdf.section_title("Volatility Targeting")
    pdf.body_text(
        "Escala la exposicion total del portfolio para mantener una volatilidad anualizada constante. "
        "Cuando la vol realizada sube, reduce posiciones. Cuando baja, aumenta. "
        "Usado por CTAs profesionales para estabilizar el Sharpe ratio."
    )
    pdf.code_block(
        "scalar = target_vol / realized_vol(20d)\n"
        "scalar = clamp(scalar, 0.5, 2.0)\n"
        "\n"
        "target_vol = 15% anualizado (configurable)\n"
        "Se aplica a TODAS las allocations antes del risk manager"
    )

    pdf.section_title("Kelly Criterion (Half-Kelly Capped)")
    pdf.body_text(
        "Sizing optimo de posiciones basado en el edge real del sistema. "
        "Se calcula por estrategia con ventana rolling de 200 trades."
    )
    pdf.code_block(
        "f* = (p * b - q) / b\n"
        "  p = win_rate, q = 1-p, b = avg_win/avg_loss\n"
        "\n"
        "risk_pct = clamp(f*/2, floor=0.5%, ceiling=3%)\n"
        "Activacion: 50+ trades de historial. Default: 2% si insuficiente."
    )

    pdf.section_title("Risk of Ruin")
    pdf.body_text(
        "Calcula la probabilidad de alcanzar el max drawdown. "
        "Se recalcula tras cada trade y auto-throttlea el sistema."
    )
    pdf.code_block(
        "# Formula analitica\n"
        "edge = win_rate * payoff_ratio - (1 - win_rate)\n"
        "RoR = ((1-edge)/(1+edge))^capital_units\n"
        "\n"
        "# Auto-control\n"
        "RoR > 3%  -> reduce sizing 50%\n"
        "RoR > 10% -> pausa trading completamente"
    )

    pdf.section_title("Monte Carlo Bootstrap")
    pdf.body_text(
        "Simulacion de equity curves por resampleo de trades historicos. "
        "NO usa GBM - preserva la estructura de dependencia real."
    )
    pdf.bullet_list([
        "10,000 simulaciones por default (configurable)",
        "Output: percentiles de equity final (p5/median/p95)",
        "Max drawdown distribution (median y p95)",
        "Probabilidad de ser rentable y probabilidad de ruin",
    ])

    pdf.section_title("Correlation Regime")
    pdf.body_text(
        "Detecta cuando la correlacion entre activos sube por encima de 0.85 (stress mode). "
        "En crypto, las correlaciones saltan a ~1.0 durante crashes, eliminando la diversificacion."
    )
    pdf.code_block(
        "avg_corr = mean(pairwise_correlations(BTC, ETH, ADA))\n"
        "if avg_corr > 0.85:\n"
        "    stress_factor = max(0.4, 1 - (corr-0.85)/(1-0.85) * 0.6)\n"
        "    # Reduce exposicion total automaticamente"
    )

    pdf.section_title("Risk Parity (Covarianza)")
    pdf.body_text(
        "Inverse-volatility weighting que asigna mas capital a los buckets menos volatiles. "
        "Se combina 70/30 con los pesos de regimen para no perder la logica existente."
    )
    pdf.code_block(
        "weight_i = (1/vol_i) / sum(1/vol_j)\n"
        "final = 0.7 * regime_weight + 0.3 * regime_weight * rp_ratio"
    )

    # ═══════════════════════════════════════════════════════════════
    # 18. EXECUTION INTELLIGENCE
    # ═══════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.chapter_title("Execution Intelligence")

    pdf.body_text(
        "Capa de ejecucion nivel institucional que reemplaza la logica hardcodeada de order type "
        "con modelos de costos, probabilidad de fill, y algoritmos de ejecucion."
    )

    pdf.section_title("Queue Position Model")
    pdf.body_text(
        "Estima la posicion en la cola para ordenes limit. En un CLOB, las ordenes se ejecutan "
        "en orden price-time priority. Si hay $50k delante, necesitas que se ejecuten antes."
    )
    pdf.code_block(
        "queue_ahead = price_level_depth_usd\n"
        "consume_rate = trade_rate * avg_trade_size * 0.5  # Solo tu lado\n"
        "time_to_front = queue_ahead / consume_rate"
    )

    pdf.section_title("Spread Predictor")
    pdf.body_text("Predice el spread futuro usando features del mercado:")
    pdf.bullet_list([
        "EMA del spread historico (baseline con mean reversion)",
        "Volatilidad (ATR alto -> spread ancho): factor 0.7x a 1.5x",
        "VPIN (flujo toxico -> spread sube): factor 1.0x a 1.4x",
        "Hawkes (actividad anomala -> spread cambia): factor hasta 1.3x",
        "OBI (imbalance alto -> spread se ensancha): factor hasta 1.3x",
    ])

    pdf.section_title("Trade Intensity Model (Bidireccional)")
    pdf.body_text(
        "A diferencia del Hawkes unidireccional (que suma todos los trades), este modelo "
        "separa buy vs sell trades para entender la presion direccional del flujo. "
        "Alimenta el microprice ajustado y el fill probability model."
    )

    pdf.section_title("TWAP Engine")
    pdf.body_text(
        "Para ordenes grandes (>$10k), divide la ejecucion en N slices temporales "
        "para minimizar market impact. Impact es concavo (sqrt), asi que 4 ordenes de "
        "$2.5k tienen menos impacto total que 1 de $10k."
    )

    pdf.section_title("Execution Analytics")
    pdf.body_text("Post-trade analysis que mide calidad de ejecucion:")
    pdf.bullet_list([
        "Implementation shortfall: diferencia decision_price vs fill_price",
        "Fill rate: % de limit orders que se ejecutaron",
        "Slippage real vs modelado: calibra el slippage model empiricamente",
        "Latencia: tiempo entre envio de orden y fill",
        "Breakdown por estrategia y tipo de orden",
    ])

    pdf.section_title("Slippage Real Measurement")
    pdf.body_text(
        "Cada Trade registra expected_price (de la senal), fill_price (real), y actual_slippage_bps. "
        "El SlippageTracker agrega estos datos y produce estadisticas por simbolo y regimen. "
        "Esto permite recalibrar el modelo de slippage del backtester con datos reales, "
        "previniendo que los backtests sean sistematicamente optimistas."
    )

    # ═══════════════════════════════════════════════════════════════
    # 19. TESTS Y CALIDAD
    # ═══════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.chapter_title("Testing y Calidad de Codigo")

    pdf.section_title("Suites de Tests")
    pdf.table(
        ["Suite", "Tests", "Cobertura"],
        [
            ["test_bug_fixes.py", "52", "Verificacion de cada bug fix aplicado (55+ fixes)"],
            ["test_execution_intelligence.py", "34", "Microprice, FillProb, SmartRouter, TWAP, Analytics"],
            ["test_core_functional.py", "21", "Indicadores, regimen, microestructura, types"],
            ["test_strategies_functional.py", "15", "MR, TF, MM: senales, filtros, edge cases"],
            ["test_self_audit.py", "31", "Integracion end-to-end, paper sim, backtester"],
            ["test_functional.py", "36", "Pipeline completo, analytics, stress, walk-forward"],
        ],
        [55, 20, 115],
    )
    pdf.body_text("Total: 153+ tests automatizados. 122 core pasando al 100%.")

    pdf.section_title("Auditorias de Codigo Realizadas")
    pdf.body_text(
        "Se han realizado 11 sesiones de auditoria profunda del codigo, incluyendo una revision "
        "cuantitativa completa, un audit E2E, y un deep audit post-quant-upgrade que encontro y "
        "corrigio 10 bugs adicionales (2 critical, 2 high, 6 medium). Total: 80+ bugs corregidos. "
        "Cada fix tiene su test de verificacion correspondiente."
    )
    pdf.bullet_list([
        "CRITICAL: micro=None crash, testnet siempre True, equity double-count",
        "HIGH: MR doubling, Hawkes self-defeating, ADX bearish bias, paper PnL=0",
        "HIGH: Paper slippage sin regimen, MM sin risk checks, RSI NaN edge case",
        "MEDIUM: WS reconnect race, bar boundary tick leakage, ISO week year",
        "MEDIUM: Hawkes sin validacion estabilidad, maintenance margin 0.5% a 2%",
        "LOW: Imports no usados, Euler hardcodeado, profit_factor inf vs 9999.99",
    ])

    pdf.section_title("Revision Cuantitativa")
    pdf.body_text(
        "Se verifico numericamente cada formula del sistema contra implementaciones de referencia:"
    )
    pdf.bullet_list([
        "Indicadores (ATR, RSI, Z-score, ADX, Bollinger): 9/9 checks OK",
        "VPIN discriminacion: balanced=0.19 vs directional=1.0 (separacion correcta)",
        "Hawkes burst detection: 4.3x spike ratio detectado, decay verificado",
        "A-S spreads: monotonicamente crecientes calm(10) < vpin(28) < crisis(50) bps",
        "A-S inventory skew: verificado numericamente (long=quotes bajan, short=suben)",
        "Position sizing, drawdown, liquidation: formulas verificadas con calculos manuales",
        "PnL arithmetic: sum(trades) = reported net_pnl en backtests de 3000 barras",
        "Trade DB round-trip: datos preservados exactamente (0 perdida de precision)",
        "JSON serialization: todos los outputs del sistema son serializables",
    ])

    out_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "BotStrike_Documentacion.pdf")
    pdf.output(out_path)
    return pdf


if __name__ == "__main__":
    path = build_pdf()
    print(f"\nPDF generado exitosamente: {path}")
    print(f"Tamano: {os.path.getsize(path) / 1024:.1f} KB")
