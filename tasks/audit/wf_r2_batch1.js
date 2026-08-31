export const meta = {
  name: 'botstrike-audit-r2-batch1',
  description: 'Auditoria R2 tanda 1: las 3 areas criticas para el dinero (strategies, risk_sizing, backtest_parity) con verificacion adversarial acotada',
  phases: [
    { title: 'Find', detail: '3 finders sobre areas disjuntas, informe incremental en tasks/audit/r2/<area>.md' },
    { title: 'Verify', detail: '2 lentes adversariales sobre los P0/P1 mas severos (tope global para que la tanda quepa en una sesion)' },
    { title: 'Synthesize', detail: 'informe de la tanda en tasks/audit/r2_batch1_report.md' },
  ],
}

const ROOT = 'C:\\Users\\edgar\\Desktop\\proyectos\\BotStrike'

// Tope duro: el workflow de 12 areas murio 3 veces por limite. El coste real no son los finders,
// son las lentes (areas x hallazgos x lentes). Con 3 areas y este tope: 3 + 8 + 1 = 12 agentes.
const MAX_VERIFIED_FINDINGS = 4

const FINDINGS_SCHEMA = {
  type: 'object', required: ['area', 'summary', 'findings'],
  properties: {
    area: { type: 'string' },
    summary: { type: 'string', description: '5-10 lineas: veredicto del area' },
    findings: { type: 'array', items: {
      type: 'object', required: ['id', 'severity', 'title', 'file', 'line', 'evidence', 'why', 'fix', 'verified_how'],
      properties: {
        id: { type: 'string' }, severity: { type: 'string', enum: ['P0', 'P1', 'P2', 'P3'] },
        title: { type: 'string' }, file: { type: 'string' }, line: { type: 'integer' },
        evidence: { type: 'string', description: 'fragmento real de codigo, 3-10 lineas' },
        why: { type: 'string' }, fix: { type: 'string' }, verified_how: { type: 'string' },
      } } },
  },
}

const VERDICT_SCHEMA = {
  type: 'object', required: ['refuted', 'confidence', 'reason'],
  properties: {
    refuted: { type: 'boolean' }, confidence: { type: 'number' }, reason: { type: 'string' },
    severity_adjust: { type: 'string', enum: ['P0', 'P1', 'P2', 'P3', 'none'] },
  },
}

const PREAMBLE = `Eres el mejor auditor del mundo de sistemas de trading algoritmico (quant + ingeniero senior). Repositorio: ${ROOT} (Python 3.12: usa "py -3.12"; tests: "py -3.12 -m pytest tests/ -q -p no:cacheprovider", hoy 112/112). NO SUPONGAS NADA: lee el codigo real (Read/Grep), ejecuta snippets con py -3.12 cuando una afirmacion numerica o de comportamiento se pueda comprobar, y contrasta con documentacion oficial (WebFetch) cuando afirmes que un endpoint/campo/formula es incorrecto.
CONTEXTO: existe una auditoria previa (2026-08-29) en tasks/audit/01..05_*.md (119 hallazgos) y un consolidado tasks/audit_2026-08-29.md; los P0 se corrigieron en b3dbf75 (core/exchange/bridge) y ffacf4a (desktop). NO repitas hallazgos ya listados ahi salvo que sigan abiertos (referencia su id, ej. "01-F07 sigue abierto") o que el fix sea incorrecto/incompleto.
CAMBIOS MUY RECIENTES QUE YA ESTAN APLICADOS (no los reportes como hallazgos nuevos, pero SI puedes criticarlos si estan mal):
 - fb073a1 "Fase 0 quant": FIBONACCI_RETRACEMENT congelado en las 3 puertas (config/settings.py allocation 0.00, portfolio/portfolio_manager.py REGIME_WEIGHTS 0.00 en todos los regimenes, SYMBOL_STRATEGY_MAP BTC-USD = set() vacio). Mean Reversion sigue activa (ETH/SOL/ADA). Motivo: sin evidencia publicada (tasks/research_r2_trend_evidence.md seccion 9) y 20% WR en paper.
 - fb073a1: risk/risk_manager.py _adjust_position_size, el guard entry~=stop pasa de ABSOLUTO (0.001 unidades de precio) a RELATIVO (1e-5 del entry). Antes bloqueaba el 100% de senales de ADA (0 trades de ADA en la DB).
 - 6d528d9 "seguridad R2": server/bridge.py _EXPOSE_TOKEN derivado de BOTSTRIKE_HOST a nivel de modulo, filtro de redaccion de token en logs, BOTSTRIKE_ALLOW_LIVE (live devuelve 403 sin el), token por cabecera X-BotStrike-Token; deploy/update.sh corre la suite y aborta el restart si falla.
 - v2.13.1: server/bridge.py _merged_performance() = rendimiento realizado desde la trade DB + unrealized vivo; analytics/performance.py ANNUALIZATION_FACTOR 252->365 y parametro use_equity_after.
INVESTIGACION DISPONIBLE (leela si te ayuda a juzgar): tasks/research_r2_trend_evidence.md (replica propia: Sharpe 1.14 neto en BTC; con $1000 lo viable son 3 activos spot; MR intradia y Fibonacci sin edge tras costes), tasks/research_r2_venues_es_2026.md (Binance cerrado para residentes ES desde 1-jul-2026; perps = CFD con tope 2:1 por ESMA), tasks/research_sota_2026.md.
SALIDA: (1) escribe tu informe en ${ROOT}\\tasks\\audit\\r2\\<AREA>.md DE FORMA INCREMENTAL (crea el archivo al empezar con Write; anade cada hallazgo con Edit en cuanto lo confirmes; nunca lo dejes para el final; si el archivo ya existe con contenido de un intento anterior, SOBREESCRIBELO); (2) devuelve los hallazgos estructurados (id "<AREA>-NN", severidad P0=pierde dinero/rompe produccion, P1=bug real impacto medio, P2=mejora importante, P3=cosmetico; file relativo al repo; line entero; evidence con codigo real). NO modifiques ningun archivo del proyecto salvo tu informe. Se brutalmente honesto; si algo esta bien, dilo y no inventes hallazgos.`

const AREAS = [
  { key: 'strategies', title: 'Estrategias y senal (strategies/mean_reversion.py, strategies/fibonacci_retracement.py, strategies/base.py, core/indicators.py, core/regime_detector.py, core/market_data.py)',
    focus: 'Verifica en profundidad los P1 abiertos de la ronda 1 (01-F04 bars_held desde len(), F05 filtro 1H nulo, F06 sin puerta R:R neto, F10 impulsos no caducan, F11 volatility_percentile inexistente, F16 seed con vela en formacion, F17 resampleo posicional): confirma con snippets py -3.12 sobre datos reales de data/binance_futures/klines/<SYM>/1m.parquet (existen, 150 dias) el comportamiento exacto (p.ej. cuantas barras 5m/15m/1H ve la estrategia con buffer 2000 de 1m; distribucion de ADX; frecuencia de senales; R:R neto medio con ATR real). Busca lo que la ronda 1 no vio: look-ahead sutil (uso de close de la barra actual en resample incompleto), estado compartido entre simbolos, NaN handling, division por cero, senales duplicadas en el mismo bar, side/quantity inconsistentes, metadata que el order_engine necesita y falta (sl/tp/action), y coherencia entre lo que documenta el README y lo que corre. Cuantifica: con los datos reales, cuantas senales/dia genera cada estrategia y que fraccion sobrevive a un filtro ATR>=2x coste. IMPORTANTE: Mean Reversion es AHORA la unica estrategia con capital (Fibonacci congelado), asi que su analisis es el prioritario; di sin rodeos si MR tiene o no esperanza positiva tras costes con los datos reales, y si el codigo hace lo que dice su docstring.' },
  { key: 'risk_sizing', title: 'Riesgo y sizing numerico (risk/risk_manager.py, portfolio/portfolio_manager.py, core/quant_models.py, strategies/base.py sizing)',
    focus: 'Reproduce NUMERICAMENTE con py -3.12 (instanciando RiskManager/PortfolioManager con Settings reales y senales sinteticas) el tamano de posicion que saldria en live para BTC/ETH/SOL/ADA con equity 1000: riesgo real por trade, notional, leverage efectivo, exposicion total con 4 posiciones, y comprueba contra max_total_exposure_pct=0.6 (01-F14 dice que equivale a 300% del equity: verifica), Binance minNotional 100 USDT en BTC (se puede abrir siquiera?), Kelly (kelly_min_trades=100: alguna vez se activa? con que datos?), vol targeting (se aplica antes o despues del cap de leverage), risk of ruin, consecutive losses, funding filter (F15), circuit breaker (rearme, expiracion, persistencia tras reinicio: F07), drawdown calculado con equity que no incluye unrealized. Busca formulas incorrectas (anualizacion, sqrt(252) vs 365, ATR en % vs abs), unidades mezcladas (USD vs contratos), y estados mutables compartidos. ATENCION ESPECIAL: el guard entry~=stop se cambio hoy a relativo (1e-5) — verifica que el nuevo umbral es correcto para los 4 simbolos y que no abre la puerta a tamanos absurdos con stops minusculos. Entrega una tabla senal -> tamano verificada y la lista de parametros incoherentes con el capital de $1000.' },
  { key: 'backtest_parity', title: 'Paridad backtest <-> live (backtesting/backtester.py, backtesting/realistic.py o similar, main.py loop de estrategia, core/market_data.py buffers, scripts/download_futures_klines.py)',
    focus: 'La ronda 1 (04) encontro que el backtester alimenta 501 barras y live 2000, que exit_fibonacci no existe en backtest, y sizing distinto. Construye una TABLA DE PARIDAD exhaustiva: para cada paso del ciclo live (tick -> barra 1m -> buffer -> resample -> indicadores -> senal -> risk validate -> allocation -> sizing -> orden -> fill (precio, slippage, fee) -> SL/TP (precio de trigger, ejecucion intrabar high/low) -> funding -> exit signals -> registro), que hace live y que hace cada backtester, con archivo:linea de ambos lados. Ejecuta un experimento de paridad con py -3.12: mismos 3 dias de BTC 1m reales alimentados (a) al Backtester y (b) a MarketDataCollector+estrategia como en live (tick a tick o barra a barra), y compara la lista de senales; reporta diferencias con causa. Revisa scripts/download_futures_klines.py (correccion de timestamps, columnas, gaps, limites de API, formato esperado por HistoricalDataLoader) y lista que scripts/ estan rotos. ESTE ES EL P0 DECLARADO DEL PROYECTO: sin paridad, ningun backtest sirve para aprobar una estrategia a live. Se concluyente sobre si hoy existe paridad o no.' },
]

const LENSES = [
  { key: 'correctness', text: 'LENTE CORRECCION: lee el codigo real en file:line y alrededor (+-60 lineas) y decide si el hallazgo describe correctamente el comportamiento del codigo. Refuta si el codigo no hace lo que el hallazgo dice, si hay una guarda que lo impide, o si la linea/archivo no corresponden.' },
  { key: 'reproduce', text: 'LENTE REPRODUCCION: intenta reproducir el fallo ejecutando codigo con py -3.12 (importa el modulo, construye el escenario con mocks minimos, muestra la salida). Refuta si no consigues reproducirlo ni encontrar una traza de ejecucion plausible.' },
]

function finderPrompt(a) {
  return PREAMBLE + '\n\nAREA = ' + a.key + '\nTITULO: ' + a.title + '\nFOCO (haz TODO esto):\n' + a.focus + '\n\nAl terminar, tu informe ' + ROOT + '\\tasks\\audit\\r2\\' + a.key + '.md debe contener: hallazgos (formato ### [Px] id - titulo / Archivo / Evidencia / Por que / Fix / Verificado como), tabla resumen y "Veredicto" de 10 lineas. Devuelve los hallazgos estructurados (todos, P0..P3) y el resumen.'
}

function verifyPrompt(f, lens) {
  return 'Eres un verificador adversarial independiente. Repositorio: ' + ROOT + ' (py -3.12). Tu unico trabajo es intentar REFUTAR este hallazgo de auditoria. Si tienes dudas razonables o no puedes confirmarlo, refuted=true (por defecto). confidence en [0,1].\n\nHALLAZGO:\n' + JSON.stringify(f, null, 2) + '\n\n' + lens.text + '\n\nNo modifiques archivos del repositorio. Responde con refuted, confidence, reason (3-8 lineas con evidencia concreta: archivo:linea, salida de ejecucion) y severity_adjust.'
}

// ---------- Find (3 areas en paralelo) ----------
phase('Find')
const found = await parallel(AREAS.map(a => () =>
  agent(finderPrompt(a), { label: 'find:' + a.key, phase: 'Find', schema: FINDINGS_SCHEMA })
    .then(res => ({ area: a.key, res }))))

const areas = found.filter(x => x && x.res)
for (const x of areas) log('find:' + x.area + ': ' + (x.res.findings || []).length + ' hallazgos')

// ---------- Verify (tope global: solo los P0/P1 mas severos) ----------
phase('Verify')
const allFindings = areas.flatMap(x => (x.res.findings || []).map(f => Object.assign({}, f, { _area: x.area })))
const critical = allFindings
  .filter(f => f.severity === 'P0' || f.severity === 'P1')
  .sort((a, b) => (a.severity === 'P0' ? 0 : 1) - (b.severity === 'P0' ? 0 : 1))
const toVerify = critical.slice(0, MAX_VERIFIED_FINDINGS)
const notVerified = critical.slice(MAX_VERIFIED_FINDINGS)
log('P0/P1 totales: ' + critical.length + ' -> verificando los ' + toVerify.length + ' mas severos (' + notVerified.length + ' quedan sin verificar)')

const verified = await parallel(toVerify.map(f => () =>
  parallel(LENSES.map(l => () =>
    agent(verifyPrompt(f, l), { label: 'verify:' + f.id + ':' + l.key, phase: 'Verify', schema: VERDICT_SCHEMA, effort: 'high' })))
    .then(vs => {
      const votes = vs.filter(Boolean)
      const refutes = votes.filter(v => v.refuted).length
      const adjust = votes.map(v => v.severity_adjust).filter(s => s && s !== 'none')
      // 2 lentes: se refuta solo si AMBAS refutan (con una sola en contra queda "dudoso pero vivo")
      return Object.assign({}, f, { votes, refuted: votes.length === 2 && refutes === 2, severity_final: adjust.length ? adjust.sort()[0] : f.severity })
    })))

const ok = verified.filter(Boolean)
const confirmed = ok.filter(v => !v.refuted)
const refuted = ok.filter(v => v.refuted)
const minors = allFindings.filter(f => f.severity === 'P2' || f.severity === 'P3')
log('Confirmados: ' + confirmed.length + ' | Refutados: ' + refuted.length + ' | P2/P3: ' + minors.length)

// ---------- Synthesis ----------
phase('Synthesize')
const input = '\n\nCONFIRMADOS (verificados por 2 lentes):\n' + JSON.stringify(confirmed.map(f => ({ id: f.id, area: f._area, severity: f.severity_final, title: f.title, file: f.file, line: f.line, why: f.why, fix: f.fix, votes: f.votes.map(v => ({ refuted: v.refuted, confidence: v.confidence, reason: v.reason })) })), null, 1)
  + '\n\nREFUTADOS:\n' + JSON.stringify(refuted.map(f => ({ id: f.id, title: f.title, reasons: f.votes.map(v => v.reason) })), null, 1)
  + '\n\nP0/P1 SIN VERIFICAR (tope de la tanda):\n' + JSON.stringify(notVerified.map(f => ({ id: f.id, area: f._area, severity: f.severity, title: f.title, file: f.file, line: f.line, fix: f.fix })), null, 1)
  + '\n\nP2/P3:\n' + JSON.stringify(minors.map(f => ({ id: f.id, area: f._area, severity: f.severity, title: f.title, file: f.file, line: f.line })), null, 1)
  + '\n\nRESUMENES POR AREA:\n' + JSON.stringify(areas.map(a => ({ area: a.area, summary: a.res.summary })), null, 1)

const synthesis = await agent('Eres el mejor quant y arquitecto de sistemas de trading. Escribe el informe de la TANDA 1 de la ronda 2 en ' + ROOT + '\\tasks\\audit\\r2_batch1_report.md (en espanol, Write completo). Areas cubiertas: strategies, risk_sizing, backtest_parity (las 3 criticas para el dinero). Contexto: el dueno tiene $1000, reside en Espana (Binance cerrado para el desde jul-2026), y hoy solo Mean Reversion tiene capital asignado (Fibonacci congelado por falta de evidencia). La pregunta que el informe debe responder es: DESPUES de esta tanda, que impide que este bot gane dinero, y en que orden se arregla.\nESTRUCTURA: 1) Resumen ejecutivo en 5 puntos con numeros. 2) Tabla de hallazgos confirmados (id, severidad final, area, archivo:linea, titulo, fix en 1 linea) ordenada por severidad. 3) Un parrafo por area con lo esencial y su veredicto. 4) La pregunta de la paridad backtest-live respondida con un SI o un NO y su consecuencia practica. 5) Veredicto sobre Mean Reversion: se mantiene con capital o se congela como Fibonacci (razona con los numeros del area strategies y con tasks/research_r2_trend_evidence.md). 6) Plan de accion priorizado y secuenciado (P0 hoy / P1 semana / P2 mes) con esfuerzo estimado. 7) Anexo: refutados, P0/P1 sin verificar por tope de tanda, y P2/P3.\nDevuelve SOLO un resumen de 12 lineas.' + input, { label: 'synthesize', phase: 'Synthesize', effort: 'high' })

return {
  confirmed: confirmed.map(f => ({ id: f.id, sev: f.severity_final, area: f._area, title: f.title, file: f.file + ':' + f.line })),
  unverified: notVerified.map(f => ({ id: f.id, sev: f.severity, area: f._area, title: f.title })),
  refutedCount: refuted.length,
  minorCount: minors.length,
  areasDone: areas.map(a => a.area),
  synthesis,
}
