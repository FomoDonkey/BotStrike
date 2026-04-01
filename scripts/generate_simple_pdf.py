"""
Generador de documentacion PDF simplificada para BotStrike.
Produce un documento accesible para cualquier persona, sin jerga tecnica ni formulas.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fpdf import FPDF

# -- Colores ---------------------------------------------------------------
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

    # -- Header / Footer ----------------------------------------------------

    def header(self):
        if self.page_no() <= 1:
            return
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(*C_TEXT_LIGHT)
        self.cell(0, 8, "Guia Simplificada | BotStrike v1.0", align="L")
        self.cell(0, 8, f"Pagina {self.page_no()}", align="R", new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(*C_BORDER)
        self.line(10, 14, 200, 14)
        self.ln(4)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(*C_TEXT_LIGHT)
        self.cell(0, 10, "Guia Simplificada | BotStrike v1.0", align="C")

    # -- Utilidades ---------------------------------------------------------

    def cover_page(self):
        self.add_page()
        self.ln(50)
        self.set_font("Helvetica", "B", 36)
        self.set_text_color(*C_PRIMARY)
        self.cell(0, 15, "BotStrike", align="C", new_x="LMARGIN", new_y="NEXT")
        self.ln(3)
        self.set_font("Helvetica", "", 16)
        self.set_text_color(*C_TEXT)
        self.cell(0, 10, "Guia Simplificada", align="C", new_x="LMARGIN", new_y="NEXT")
        self.cell(0, 8, "Como funciona el sistema, explicado de forma sencilla", align="C", new_x="LMARGIN", new_y="NEXT")
        self.ln(15)
        self.set_draw_color(*C_PRIMARY)
        self.set_line_width(0.8)
        self.line(60, self.get_y(), 150, self.get_y())
        self.ln(15)
        self.set_font("Helvetica", "", 11)
        self.set_text_color(*C_TEXT_LIGHT)
        info_lines = [
            "Un bot que opera criptomonedas automaticamente",
            "3 estrategias diferentes trabajando en equipo",
            "Proteccion de capital como prioridad numero uno",
            "",
            "Activos: Bitcoin (BTC) | Ethereum (ETH) | Cardano (ADA)",
            "Exchange: Strike Finance (derivados/futuros)",
            "",
            "Esta guia explica todo sin tecnicismos,",
            "para que cualquier persona pueda entenderlo.",
        ]
        for line in info_lines:
            self.cell(0, 7, line, align="C", new_x="LMARGIN", new_y="NEXT")
        self.ln(25)
        self.set_font("Helvetica", "I", 9)
        self.cell(0, 6, "Guia No Tecnica - Marzo 2026", align="C", new_x="LMARGIN", new_y="NEXT")

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
            title_w = self.get_string_width(title)
            self.cell(title_w + 2, 7, title)
            dots_x = 10 + indent + title_w + 2
            page_str = str(page)
            page_w = self.get_string_width(page_str)
            end_x = 200 - page_w - 2
            self.set_font("Helvetica", "", 8)
            self.set_text_color(*C_BORDER)
            dot_count = max(1, int((end_x - dots_x) / 1.5))
            self.cell(end_x - dots_x, 7, " " + "." * dot_count + " ")
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

    def table(self, headers, rows, col_widths=None):
        if col_widths is None:
            n = len(headers)
            col_widths = [190 / n] * n

        self.set_font("Helvetica", "B", 9)
        self.set_fill_color(*C_TABLE_HEADER)
        self.set_text_color(*C_WHITE)
        for i, h in enumerate(headers):
            self.cell(col_widths[i], 7, f" {h}", border=0, fill=True)
        self.ln()

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


# =========================================================================
# CONTENIDO DE LA GUIA SIMPLIFICADA
# =========================================================================

def build_pdf():
    """Genera PDF en 2 pasadas: primera captura paginas del TOC, segunda las usa."""
    pdf = _build_content(with_toc=False)
    toc_data = pdf._toc_entries[:]

    final = _build_content(with_toc=True, toc_entries=toc_data)

    out_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "BotStrike_Guia_Simple.pdf",
    )
    final.output(out_path)
    return out_path


def _build_content(with_toc=False, toc_entries=None):
    pdf = BotStrikePDF()
    pdf.set_title("BotStrike - Guia Simplificada")
    pdf.set_author("BotStrike Trading System")

    if with_toc and toc_entries:
        adjusted = [{"level": e["level"], "title": e["title"], "page": e["page"] + 1} for e in toc_entries]
        pdf._toc_entries = adjusted

    pdf.cover_page()

    if with_toc:
        pdf.add_toc_page()

    # =================================================================
    # 1. QUE ES BOTSTRIKE?
    # =================================================================
    pdf.add_page()
    pdf.chapter_title("Que es BotStrike?")

    pdf.body_text(
        "Imagina que contratas a 3 traders expertos que trabajan las 24 horas del dia, "
        "los 7 dias de la semana, sin descanso, sin emociones, y sin cometer errores humanos. "
        "Eso es BotStrike: un programa de computadora que opera criptomonedas automaticamente "
        "en un exchange llamado Strike Finance."
    )

    pdf.section_title("Tres Expertos en Uno")
    pdf.body_text(
        "BotStrike no es un solo bot, sino tres estrategias diferentes trabajando en equipo. "
        "Cada una tiene su propia 'personalidad' y funciona mejor en distintas condiciones del mercado. "
        "Cuando una no encuentra oportunidades, otra si. Se complementan."
    )
    pdf.bullet_list([
        "El Cazador de Rebotes (Mean Reversion): busca precios que se alejaron demasiado de lo normal",
        "El Surfista de Tendencias (Trend Following): detecta cuando el mercado tiene una direccion clara y la sigue",
        "El Intermediario (Market Making): compra y vende al mismo tiempo para ganar la diferencia de precios",
    ])

    pdf.section_title("El Gerente de Riesgos")
    pdf.body_text(
        "Ademas de los 3 traders, hay un 'gerente de riesgos' que los supervisa constantemente. "
        "Su trabajo es asegurarse de que ninguno pierda demasiado dinero. Si las cosas se ponen "
        "peligrosas, el gerente puede reducir posiciones o incluso detener todo el sistema. "
        "La proteccion del capital siempre es mas importante que ganar dinero."
    )

    pdf.section_title("Donde Opera")
    pdf.body_text(
        "BotStrike opera en Strike Finance, un exchange descentralizado de derivados (futuros). "
        "Los futuros permiten apostar tanto a que el precio suba como a que baje, y usar "
        "apalancamiento (operar con mas dinero del que realmente tienes depositado). "
        "El sistema opera tres criptomonedas:"
    )
    pdf.table(
        ["Criptomoneda", "Simbolo", "Caracteristica"],
        [
            ["Bitcoin", "BTC-USD", "La mas estable y liquida de las tres"],
            ["Ethereum", "ETH-USD", "Volatilidad media, buen balance"],
            ["Cardano", "ADA-USD", "La mas volatil, movimientos mas grandes"],
        ],
        [50, 40, 100],
    )

    pdf.info_box(
        "Idea Clave",
        "BotStrike es como un equipo de 3 traders con un gerente de riesgos, "
        "trabajando sin parar en el mercado de criptomonedas. Cada trader tiene "
        "una especialidad diferente y entre todos cubren cualquier tipo de mercado."
    )

    # =================================================================
    # 2. LAS 3 ESTRATEGIAS
    # =================================================================
    pdf.add_page()
    pdf.chapter_title("Las 3 Estrategias")

    pdf.body_text(
        "Cada estrategia es como un jugador en un equipo deportivo: tiene su posicion, "
        "su estilo de juego y sus momentos para brillar. Veamos cada una en detalle."
    )

    pdf.section_title("El Cazador de Rebotes (Mean Reversion)")
    pdf.body_text(
        "Piensa en una liga elastica (o goma). Si la estiras mucho hacia un lado, tarde o temprano "
        "vuelve a su posicion original. Los precios de las criptomonedas hacen algo parecido: "
        "cuando se alejan demasiado de su promedio historico, tienden a regresar."
    )
    pdf.body_text(
        "Esta estrategia detecta cuando el precio se ha 'estirado' mucho por encima o por debajo "
        "de lo normal, y apuesta a que va a regresar. Si el precio esta muy bajo comparado con su "
        "promedio, compra. Si esta muy alto, vende."
    )
    pdf.bullet_list([
        "Funciona mejor cuando el mercado esta tranquilo y se mueve de lado",
        "Pierde cuando hay una tendencia fuerte (el precio sigue subiendo o bajando sin parar)",
        "Tiene proteccion: si el precio no regresa, se corta la perdida automaticamente",
    ])

    pdf.section_title("El Surfista de Tendencias (Trend Following)")
    pdf.body_text(
        "Imagina a un surfista en el mar. No intenta ir contra la ola; espera a que venga una buena, "
        "se sube y la cabalga hasta que pierde fuerza. Esta estrategia hace lo mismo: cuando el "
        "mercado tiene una direccion clara (sube con fuerza o baja con fuerza), se sube a la ola."
    )
    pdf.body_text(
        "El surfista no intenta adivinar cuando cambia la direccion. Simplemente detecta que ya "
        "hay una ola en marcha y la aprovecha. Cuando la ola se acaba, se baja."
    )
    pdf.bullet_list([
        "Funciona mejor cuando el mercado tiene una direccion clara",
        "Pierde en mercados sin tendencia (muchas olas falsas, entra y sale sin ganar)",
        "Usa un 'stop loss movil': la proteccion se mueve a favor, asegurando ganancias parciales",
    ])

    pdf.section_title("El Intermediario (Market Making)")
    pdf.body_text(
        "Piensa en una casa de cambio en un aeropuerto. Compra dolares a un precio y los vende "
        "a otro precio un poco mas alto. La diferencia entre el precio de compra y el de venta "
        "es su ganancia. No le importa si el dolar sube o baja; gana con la diferencia."
    )
    pdf.body_text(
        "Esta estrategia hace lo mismo: pone una orden de compra a un precio y una de venta a "
        "un precio ligeramente mayor. Cuando ambas se ejecutan, gana la diferencia (el 'spread'). "
        "El truco esta en ajustar los precios constantemente segun el riesgo."
    )
    pdf.bullet_list([
        "Funciona mejor cuando el mercado esta tranquilo y no hay grandes movimientos",
        "Pierde cuando el mercado se mueve bruscamente en una direccion (queda atrapado de un lado)",
        "Si acumula mucho inventario de un lado, ajusta precios para equilibrarse",
    ])

    pdf.check_page_space(50)
    pdf.section_title("Cuando Brilla Cada Una")
    pdf.table(
        ["Estrategia", "Mercado Ideal", "Mercado Dificil", "Analogia"],
        [
            ["Cazador de Rebotes", "Tranquilo, lateral", "Tendencia fuerte", "Liga elastica"],
            ["Surfista de Tendencias", "Direccion clara", "Lateral, sin ola", "Surfista"],
            ["Intermediario", "Muy tranquilo", "Movimiento brusco", "Casa de cambio"],
        ],
        [40, 42, 42, 66],
    )

    pdf.info_box(
        "Idea Clave",
        "Las 3 estrategias se complementan: cuando el mercado esta tranquilo, "
        "el Cazador y el Intermediario trabajan. Cuando hay tendencia, el Surfista "
        "toma el mando. Siempre hay alguien preparado para cada situacion."
    )

    # =================================================================
    # 3. COMO DETECTA EL ESTADO DEL MERCADO
    # =================================================================
    pdf.add_page()
    pdf.chapter_title("Como Detecta el Estado del Mercado")

    pdf.body_text(
        "Asi como un piloto de avion revisa el clima antes de volar, BotStrike revisa el "
        "'clima' del mercado antes de operar. El mercado puede estar en diferentes estados, "
        "y cada estado favorece a distintas estrategias."
    )

    pdf.section_title("Los 3 Climas del Mercado")
    pdf.body_text(
        "BotStrike clasifica el mercado en tres grandes categorias, como si fueran estados del clima:"
    )

    pdf.table(
        ["Clima del Mercado", "Nombre Tecnico", "Que pasa", "Quien trabaja"],
        [
            ["Calmado", "RANGING", "Precio sube y baja sin direccion clara", "Cazador + Intermediario"],
            ["Tormenta con direccion", "TRENDING", "Precio sube o baja con fuerza", "Surfista"],
            ["Huracan", "BREAKOUT", "Movimiento explosivo y repentino", "Surfista (con cautela)"],
        ],
        [38, 35, 62, 55],
    )

    pdf.section_title("Revision Constante")
    pdf.body_text(
        "El sistema revisa el estado del mercado cada 5 segundos. No espera a que pase algo "
        "malo para reaccionar; esta constantemente midiendo la temperatura del mercado. "
        "Si el clima cambia de 'calmado' a 'tormenta', automaticamente activa al Surfista "
        "y reduce la actividad del Cazador y el Intermediario."
    )

    pdf.section_title("Senales que Observa")
    pdf.body_text(
        "Para determinar el clima, BotStrike mira varias cosas (sin entrar en detalles tecnicos):"
    )
    pdf.bullet_list([
        "Cuanto se esta moviendo el precio (volatilidad): si se mueve mucho, hay tormenta",
        "Si hay una direccion clara o el precio va y viene (tendencia vs lateral)",
        "La velocidad del movimiento: movimientos rapidos indican huracan",
        "El volumen de operaciones: mucha actividad suele acompanar los cambios fuertes",
    ])

    pdf.section_title("Proteccion Contra Falsas Alarmas")
    pdf.body_text(
        "Para evitar reaccionar ante cambios falsos (como cuando el cielo se nubla pero no llueve), "
        "el sistema requiere que el nuevo clima se confirme 2 veces seguidas antes de cambiar. "
        "Esto evita que los traders se activen y desactiven cada pocos segundos sin razon."
    )

    pdf.info_box(
        "Idea Clave",
        "BotStrike revisa el 'clima' del mercado cada 5 segundos y ajusta automaticamente "
        "que estrategias estan activas. No es un sistema rigido: se adapta a las condiciones "
        "cambiantes como un buen piloto ajusta el vuelo segun el clima."
    )

    # =================================================================
    # 4. COMO PROTEGE EL DINERO
    # =================================================================
    pdf.add_page()
    pdf.chapter_title("Como Protege el Dinero")

    pdf.body_text(
        "La parte mas importante de BotStrike no es cuanto gana, sino como protege el capital. "
        "El sistema tiene multiples capas de proteccion, como un banco tiene guardias, "
        "camaras, boveda, alarmas y seguros. Si una capa falla, la siguiente actua."
    )

    pdf.section_title("Capa 1: Nunca Apostar Todo")
    pdf.body_text(
        "En cada operacion, BotStrike solo arriesga el 2% del capital total. Esto significa que "
        "incluso si una operacion sale completamente mal, solo se pierde una parte pequena. "
        "Necesitarias 50 perdidas seguidas para perder todo, algo estadisticamente casi imposible."
    )

    pdf.section_title("Capa 2: Boton de Panico (Max Drawdown)")
    pdf.body_text(
        "Si las perdidas acumuladas llegan al 15% del capital, el sistema se detiene completamente. "
        "Como un boton de emergencia en una fabrica: todo para. Esto garantiza que siempre queda "
        "al menos el 85% del capital para poder recuperarse otro dia."
    )

    pdf.section_title("Capa 3: Pausa Automatica (Circuit Breaker)")
    pdf.body_text(
        "Si las perdidas ocurren muy rapido (no el 15% total, sino perdidas fuertes en poco tiempo), "
        "el sistema se pausa temporalmente. Como cuando la luz se corta en tu casa si hay un "
        "cortocircuito: se apaga para proteger la instalacion."
    )

    pdf.section_title("Capa 4: Control de Diversificacion")
    pdf.body_text(
        "Normalmente, Bitcoin, Ethereum y Cardano se mueven de forma algo independiente. Pero a veces, "
        "en momentos de panico, todas caen al mismo tiempo. BotStrike detecta cuando las 3 "
        "criptomonedas se mueven juntas (correlacion alta) y reduce automaticamente la exposicion. "
        "Si todo cae junto, tener posiciones en las 3 no te protege: es como tener 3 paraguas iguales."
    )

    pdf.section_title("Capa 5: Detector de Fragilidad del Mercado")
    pdf.body_text(
        "A veces el mercado es tan 'delgado' (poca liquidez) que nuestras propias ordenes podrian "
        "mover el precio. Imagina un lago pequeno: si tiras una piedra grande, se nota mucho. "
        "BotStrike mide constantemente que tanto impacto tendrian sus ordenes en el mercado. "
        "Si el impacto seria demasiado grande, reduce el tamano de las ordenes o deja de operar."
    )

    pdf.check_page_space(60)
    pdf.section_title("Resumen de Protecciones")
    pdf.table(
        ["Proteccion", "Analogia", "Que Hace"],
        [
            ["Riesgo por operacion (2%)", "No poner todos los huevos en una canasta", "Limita perdida por trade"],
            ["Max Drawdown (15%)", "Boton de emergencia", "Detiene todo si hay 15% de perdida"],
            ["Circuit Breaker", "Fusible electrico", "Pausa si las perdidas son muy rapidas"],
            ["Control de correlacion", "No llevar 3 paraguas iguales", "Reduce si todo se mueve junto"],
            ["Detector de fragilidad", "No tirar piedras grandes en lago pequeno", "Reduce si el mercado es fragil"],
        ],
        [48, 62, 80],
    )

    pdf.info_box(
        "Idea Clave",
        "BotStrike tiene 5+ capas de proteccion. La filosofia es clara: es mejor dejar de ganar "
        "una oportunidad que perder dinero. El capital se protege primero, las ganancias vienen despues."
    )

    # =================================================================
    # 5. EL CEREBRO DEL MARKET MAKING
    # =================================================================
    pdf.add_page()
    pdf.chapter_title("El Cerebro del Market Making")

    pdf.body_text(
        "El Intermediario (Market Making) es la estrategia mas sofisticada de BotStrike. "
        "Necesita un 'cerebro' especial para decidir a que precios comprar y vender. "
        "Veamos como funciona con analogias simples."
    )

    pdf.section_title("Precios Dinamicos: Como un Comerciante Inteligente")
    pdf.body_text(
        "Imagina que tienes una tienda de frutas. Si te llegan muchos clientes comprando manzanas "
        "y tu almacen se vacia, subirias el precio para no quedarte sin stock. Si por el contrario "
        "tienes el almacen lleno y nadie compra, bajarias el precio para atraer compradores."
    )
    pdf.body_text(
        "El Market Making hace exactamente esto: ajusta continuamente sus precios de compra y "
        "venta dependiendo de cuanto inventario tiene. Si ha comprado mucho (inventario alto), "
        "baja el precio de venta para atraer vendedores y equilibrarse. Si ha vendido mucho, "
        "sube el precio de compra."
    )

    pdf.section_title("Detector de Traders Informados (VPIN)")
    pdf.body_text(
        "Imagina que eres un vendedor de autos usados. Normalmente, tus clientes son personas normales. "
        "Pero de repente notas que llegan muchos mecanicos expertos queriendo comprar un modelo "
        "especifico. Eso es sospechoso: probablemente saben algo que tu no (tal vez ese modelo "
        "va a subir de valor)."
    )
    pdf.body_text(
        "VPIN detecta exactamente eso en el mercado: cuando hay un flujo inusual de operaciones "
        "que sugiere que alguien tiene informacion privilegiada. Cuando esto pasa, el sistema "
        "ensancha sus precios (mas diferencia entre compra y venta) o deja de operar, "
        "porque operar contra alguien que sabe mas que tu es un mal negocio."
    )

    pdf.section_title("Detector de Fragilidad (Kyle Lambda)")
    pdf.body_text(
        "Kyle Lambda mide que tan 'fragil' esta el mercado, es decir, cuanto moveria el precio "
        "una sola orden grande. Si el mercado es profundo (muchos compradores y vendedores), "
        "una orden no lo mueve mucho. Si es delgado, una orden puede causar un movimiento grande."
    )
    pdf.body_text(
        "Es como la diferencia entre tirar una piedra en el oceano (nadie lo nota) y tirar "
        "la misma piedra en un charco (salpica todo). El sistema mide esto constantemente "
        "y cuando el mercado esta fragil, reduce sus ordenes o aumenta los margenes de seguridad."
    )

    pdf.info_box(
        "Idea Clave",
        "El Market Making ajusta precios como un comerciante inteligente: segun su inventario, "
        "segun cuanto 'saben' los demas traders, y segun que tan fragil esta el mercado. "
        "Si las condiciones son peligrosas, se vuelve muy conservador o se detiene."
    )

    # =================================================================
    # 6. COMO EJECUTA LAS ORDENES
    # =================================================================
    pdf.add_page()
    pdf.chapter_title("Como Ejecuta las Ordenes")

    pdf.body_text(
        "Una vez que una estrategia decide comprar o vender, todavia queda una decision "
        "importante: como ejecutar esa orden de la forma mas barata y eficiente posible."
    )

    pdf.section_title("Dos Formas de Comprar")
    pdf.body_text(
        "Hay dos formas basicas de ejecutar una orden, como dos formas de comprar en un mercado:"
    )
    pdf.bullet_list([
        "Orden de mercado (Market Order): como llegar a una tienda y decir 'lo quiero ya, al "
        "precio que sea'. Es inmediata pero puedes pagar un poco mas caro.",
        "Orden limite (Limit Order): como poner un anuncio diciendo 'compro a este precio'. "
        "Es mas barata pero no esta garantizado que alguien acepte tu oferta.",
    ])

    pdf.section_title("El Enrutador Inteligente")
    pdf.body_text(
        "BotStrike tiene un 'enrutador inteligente' que decide automaticamente cual de las dos "
        "opciones usar. Analiza los costos de cada opcion y elige la mas barata. Considera cosas como:"
    )
    pdf.bullet_list([
        "Que tan urgente es la orden (si el precio se mueve rapido, mejor ir al mercado)",
        "Que probabilidad hay de que la orden limite se ejecute (si es baja, no vale la pena esperar)",
        "Cuanto costaria cada opcion en comisiones y deslizamiento de precio",
        "Que tanto impacto tendria nuestra orden en el mercado (ordenes grandes mueven el precio)",
    ])

    pdf.section_title("Division de Ordenes Grandes")
    pdf.body_text(
        "Si la orden es grande, el sistema la divide en pedazos mas pequenos y los ejecuta "
        "a lo largo del tiempo. Es como el dicho: 'Como te comes un elefante? Un bocado a la vez'. "
        "Si compramos todo de golpe, el propio acto de comprar subiria el precio y pagariamos mas caro."
    )

    pdf.section_title("Medicion de Costos Reales")
    pdf.body_text(
        "Despues de cada operacion, el sistema compara el precio que esperaba obtener con el "
        "precio que realmente obtuvo. Esta diferencia (llamada 'deslizamiento') se registra "
        "y se usa para mejorar las decisiones futuras. Es un sistema que aprende de su propia experiencia."
    )

    pdf.info_box(
        "Idea Clave",
        "BotStrike no solo decide QUE comprar o vender, sino COMO hacerlo de la forma mas "
        "economica. Elige entre compra inmediata o con paciencia, divide ordenes grandes, "
        "y mide constantemente sus costos reales para mejorar."
    )

    # =================================================================
    # 7. MODOS DE USO
    # =================================================================
    pdf.add_page()
    pdf.chapter_title("Modos de Uso")

    pdf.body_text(
        "BotStrike no es solo un programa que opera en vivo. Tiene varios modos de uso, "
        "cada uno disenado para una etapa diferente del proceso."
    )

    pdf.section_title("Paper Trading: Modo Practica")
    pdf.body_text(
        "Como un simulador de vuelo para pilotos. Usa datos reales del mercado, pero el dinero "
        "es ficticio. Permite probar estrategias sin arriesgar nada. Todo funciona exactamente "
        "igual que en modo real: mismas decisiones, mismas protecciones, mismo registro de "
        "operaciones. La unica diferencia es que no se envian ordenes reales al exchange."
    )

    pdf.section_title("Live Trading: Modo Real")
    pdf.body_text(
        "El modo real. Dinero real, ordenes reales, ganancias y perdidas reales. Solo se debe "
        "activar despues de haber probado extensivamente en paper trading y backtesting."
    )

    pdf.section_title("Backtesting: Prueba con Datos Historicos")
    pdf.body_text(
        "Como ver una pelicula que ya paso. Toma datos historicos del mercado y simula que "
        "habria pasado si BotStrike hubiera estado operando en ese periodo. Permite probar "
        "ideas y estrategias antes de arriesgar dinero real. El sistema tiene varios niveles "
        "de realismo: desde simulaciones rapidas hasta reproducciones tick-a-tick muy precisas."
    )

    pdf.section_title("Dashboard: El Panel de Control")
    pdf.body_text(
        "Una interfaz visual donde puedes ver todo lo que hace el bot en tiempo real. "
        "Como el tablero de un auto: velocidad, combustible, temperatura. Veremos mas "
        "detalles en el siguiente capitulo."
    )

    pdf.section_title("Recoleccion de Datos")
    pdf.body_text(
        "Un modo que simplemente graba datos del mercado continuamente, sin operar. "
        "Estos datos se usan despues para backtesting y analisis. Como una camara de "
        "seguridad que graba todo para poder revisarlo despues."
    )

    pdf.check_page_space(50)
    pdf.section_title("Resumen de Modos")
    pdf.table(
        ["Modo", "Dinero Real?", "Ordenes Reales?", "Para Que Sirve"],
        [
            ["Paper Trading", "No", "No", "Probar sin riesgo con datos reales"],
            ["Live Trading", "Si", "Si", "Operar de verdad"],
            ["Backtesting", "No", "No", "Probar con datos del pasado"],
            ["Dashboard", "N/A", "N/A", "Monitorear y analizar visualmente"],
            ["Recoleccion", "No", "No", "Grabar datos para uso futuro"],
        ],
        [35, 30, 35, 90],
    )

    pdf.info_box(
        "Idea Clave",
        "Nunca se opera con dinero real sin antes haber probado extensivamente en paper "
        "trading y backtesting. BotStrike permite un camino gradual: recolectar datos, "
        "probar en el pasado, practicar en vivo sin riesgo, y finalmente operar de verdad."
    )

    # =================================================================
    # 8. EL PANEL DE CONTROL (DASHBOARD)
    # =================================================================
    pdf.add_page()
    pdf.chapter_title("El Panel de Control (Dashboard)")

    pdf.body_text(
        "El dashboard es como el tablero de instrumentos de un auto o la cabina de un avion. "
        "Muestra toda la informacion importante de un vistazo, organizada en 4 secciones principales."
    )

    pdf.section_title("Operaciones en Vivo")
    pdf.body_text(
        "Muestra en tiempo real que esta haciendo el bot: que posiciones tiene abiertas, "
        "cuantas ganancias o perdidas acumula, que estrategia genero cada operacion, "
        "y el estado actual de cada criptomoneda. Es como mirar a tus traders trabajar "
        "a traves de una ventana."
    )
    pdf.bullet_list([
        "Posiciones abiertas con ganancias/perdidas en tiempo real",
        "Historial de operaciones recientes",
        "Estado del mercado (clima) para cada criptomoneda",
        "Indicadores de riesgo actuales",
    ])

    pdf.section_title("Backtesting Visual")
    pdf.body_text(
        "Permite ejecutar pruebas con datos historicos y ver los resultados graficamente. "
        "Puedes ver curvas de ganancias, comparar estrategias, y entender en que periodos "
        "el sistema gano o perdio."
    )

    pdf.section_title("Analisis de Riesgos")
    pdf.body_text(
        "Una seccion dedicada a mostrar que tan seguro esta el sistema. Muestra las capas "
        "de proteccion, cuanto margen queda antes de que se active alguna, y el historico "
        "de perdidas maximas. Es como el panel de salud de un paciente en un hospital."
    )

    pdf.section_title("Panel de Administracion")
    pdf.body_text(
        "Para configurar el sistema, ejecutar diagnosticos, y hacer pruebas rapidas. "
        "Desde aqui se puede cambiar parametros, activar o desactivar estrategias, "
        "y verificar que todo funciona correctamente."
    )

    pdf.info_box(
        "Idea Clave",
        "El dashboard te da visibilidad total sobre lo que hace BotStrike. No es una "
        "caja negra: puedes ver cada decision, cada operacion y cada metrica de riesgo "
        "en tiempo real, todo desde una interfaz visual accesible."
    )

    # =================================================================
    # 9. NUMEROS Y RESULTADOS
    # =================================================================
    pdf.add_page()
    pdf.chapter_title("Numeros y Resultados")

    pdf.body_text(
        "BotStrike no es solo un sistema que 'funciona'. Ha sido probado, auditado y "
        "verificado exhaustivamente para asegurar que cada parte hace lo que debe hacer."
    )

    pdf.section_title("Pruebas Automatizadas")
    pdf.body_text(
        "El sistema tiene mas de 153 pruebas automatizadas que se ejecutan para verificar "
        "que todo funciona correctamente. Es como una lista de chequeo de un avion antes de "
        "despegar: cada componente se verifica individualmente y en conjunto."
    )
    pdf.bullet_list([
        "Cada estrategia se prueba por separado y en conjunto",
        "Las protecciones de riesgo se verifican con escenarios extremos",
        "La ejecucion de ordenes se prueba con datos reales",
        "Las bases de datos se verifican para asegurar que no pierden informacion",
    ])

    pdf.section_title("Auditorias de Codigo")
    pdf.body_text(
        "Se han realizado 11 sesiones de auditoria profunda del codigo, como inspecciones "
        "de calidad en una fabrica. Estas auditorias encontraron y corrigieron mas de 80 "
        "errores, desde problemas criticos hasta mejoras menores."
    )
    pdf.bullet_list([
        "Errores criticos: problemas que podrian haber causado perdidas reales (todos corregidos)",
        "Errores importantes: comportamientos incorrectos que afectaban rendimiento (todos corregidos)",
        "Mejoras menores: optimizaciones y limpieza de codigo (todas aplicadas)",
    ])

    pdf.section_title("Monitoreo Continuo")
    pdf.body_text(
        "El sistema se vigila a si mismo constantemente. Si algo falla (una conexion se cae, "
        "un calculo da un resultado raro, una tarea se detiene), el sistema lo detecta, "
        "lo reporta, e intenta recuperarse automaticamente. Si el problema es grave, "
        "detiene las operaciones para proteger el capital."
    )

    pdf.section_title("Registro de Operaciones")
    pdf.body_text(
        "Cada operacion que hace BotStrike queda registrada en una base de datos con todos "
        "sus detalles: precio de entrada, precio de salida, ganancia o perdida, que estrategia "
        "la genero, en que clima de mercado estaba, cuanto costo ejecutarla, y mas. "
        "Esto permite analizar el rendimiento y mejorar continuamente."
    )

    pdf.info_box(
        "Idea Clave",
        "BotStrike ha pasado por 153+ pruebas automaticas y 11 auditorias profundas. "
        "Cada error encontrado fue corregido y cada correccion tiene su propia prueba "
        "para verificar que no vuelva a ocurrir. Es un sistema probado a fondo."
    )

    # =================================================================
    # 10. GLOSARIO DE TERMINOS
    # =================================================================
    pdf.add_page()
    pdf.chapter_title("Glosario de Terminos")

    pdf.body_text(
        "A continuacion, un glosario con los terminos mas comunes que puedes encontrar "
        "al hablar sobre BotStrike o trading en general, explicados de forma sencilla."
    )

    glossary = [
        ["Spread", "La diferencia entre el precio mas alto al que alguien quiere comprar y el "
         "precio mas bajo al que alguien quiere vender. Es el 'costo' de operar."],
        ["Bid / Ask", "Bid es el precio al que alguien quiere comprar. Ask es el precio al "
         "que alguien quiere vender. El ask siempre es un poco mas alto que el bid."],
        ["Leverage (Apalancamiento)", "Operar con mas dinero del que tienes. Si usas 10x, "
         "con $1,000 controlas $10,000. Multiplica ganancias Y perdidas."],
        ["Drawdown", "La caida desde el punto mas alto de ganancias hasta el punto mas bajo. "
         "Si ganaste $10,000 y luego bajaste a $8,500, tu drawdown es 15%."],
        ["Slippage (Deslizamiento)", "La diferencia entre el precio que esperabas y el precio "
         "que realmente obtuviste. Ocurre porque el mercado se mueve mientras tu orden se ejecuta."],
        ["PnL", "Profit and Loss (Ganancia y Perdida). El resultado neto de tus operaciones."],
        ["Basis Points (bps)", "Una forma de medir porcentajes muy pequenos. 1 bps = 0.01%. "
         "100 bps = 1%. Se usa porque los spreads suelen ser fracciones pequenas."],
        ["Stop Loss", "Una orden automatica que cierra tu posicion si el precio llega a un nivel "
         "de perdida predeterminado. Como un freno de emergencia."],
        ["Take Profit", "Una orden automatica que cierra tu posicion cuando llegas a un nivel "
         "de ganancia objetivo. Asegura tus ganancias."],
        ["Funding Rate", "Un pago periodico entre compradores y vendedores en futuros perpetuos. "
         "Mantiene el precio del futuro cerca del precio real del activo."],
        ["Orderbook", "La lista de todas las ordenes de compra y venta pendientes en un exchange. "
         "Muestra cuanta demanda y oferta hay a cada nivel de precio."],
        ["VPIN", "Un indicador que detecta cuando traders con informacion privilegiada estan "
         "operando. Valores altos significan peligro."],
        ["Hawkes Process", "Un modelo que detecta rachas de actividad inusual en el mercado. "
         "Cuando hay muchas operaciones en poco tiempo, se 'enciende' la alerta."],
        ["Kyle Lambda", "Una medida de que tanto nuestras ordenes moverian el precio del mercado. "
         "Si es alto, el mercado es fragil y debemos operar con cuidado."],
    ]

    for term_def in glossary:
        term = term_def[0]
        definition = term_def[1]
        pdf.check_page_space(20)
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(*C_PRIMARY)
        pdf.cell(0, 6, term, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*C_TEXT)
        pdf.multi_cell(0, 5, f"  {definition}")
        pdf.ln(2)

    pdf.info_box(
        "Nota Final",
        "Esta guia es un resumen simplificado. Para detalles tecnicos completos, "
        "consulta el documento 'BotStrike - Documentacion Tecnica' que incluye "
        "formulas, arquitectura detallada y parametros de configuracion."
    )

    return pdf


if __name__ == "__main__":
    path = build_pdf()
    print(f"\nPDF generado exitosamente: {path}")
    print(f"Tamano: {os.path.getsize(path) / 1024:.1f} KB")
