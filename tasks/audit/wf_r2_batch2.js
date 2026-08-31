export const meta = {
  name: 'botstrike-audit-r2-batch2',
  description: 'Auditoria R2 tanda 2: ejecucion y contabilidad (fix_core, fix_exchange, persistence) con verificacion adversarial acotada',
  phases: [
    { title: 'Find', detail: '3 finders sobre areas disjuntas, informe incremental en tasks/audit/r2/<area>.md' },
    { title: 'Verify', detail: '2 lentes adversariales sobre los P0/P1 mas severos (tope global para que la tanda quepa en una sesion)' },
    { title: 'Synthesize', detail: 'informe de la tanda en tasks/audit/r2_batch2_report.md' },
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

const PREAMBLE = `Eres el mejor auditor del mundo de sistemas de trading algoritmico (quant + ingeniero senior). Repositorio: ${ROOT} (Python 3.12: usa "py -3.12"; tests: "py -3.12 -m pytest tests/ -q -p no:cacheprovider", hoy 127/127). NO SUPONGAS NADA: lee el codigo real (Read/Grep), ejecuta snippets con py -3.12 cuando una afirmacion numerica o de comportamiento se pueda comprobar, y contrasta con documentacion oficial (WebFetch) cuando afirmes que un endpoint/campo/formula es incorrecto.
CONTEXTO: existe una auditoria previa (2026-08-29) en tasks/audit/01..05_*.md (119 hallazgos) y un consolidado tasks/audit_2026-08-29.md; los P0 se corrigieron en b3dbf75 (core/exchange/bridge) y ffacf4a (desktop). NO repitas hallazgos ya listados ahi salvo que sigan abiertos (referencia su id, ej. "01-F07 sigue abierto") o que el fix sea incorrecto/incompleto.
CAMBIOS MUY RECIENTES QUE YA ESTAN APLICADOS (no los reportes como hallazgos nuevos, pero SI puedes criticarlos si estan mal):
 - fb073a1 + 1309927: TODAS las estrategias estan CONGELADAS (allocation 0.00, REGIME_WEIGHTS 0.00 en todos los regimenes, SYMBOL_STRATEGY_MAP con set() vacio para los 4 simbolos). El bot NO abre posiciones a proposito. Fibonacci por falta de evidencia; Mean Reversion porque la tanda 1 midio edge BRUTO nulo (2.284 trades, 150 dias reales, -0.90/-0.63/-2.05/+0.45 bps con SE 1.2-2.6; invertir las senales no mejora el resultado). Ver tasks/audit/r2_batch1_report.md.
 - 1309927 (tanda 1, P0 corregidos): risk/risk_manager.py Risk of Ruin pasa a ser POR ESTRATEGIA con probation de 6 h (ROR_PROBATION_SEC) y log de cambio de estado (antes era global, silencioso y permanente); backtesting/backtester.py usa OrderExecutionEngine.is_exit_signal en sus 3 sitios (antes listas hardcodeadas que ignoraban exit_fibonacci) y la ventana pasa de 501 barras a MAX_BARS importado de core.market_data; core/quant_models.py RiskOfRuin.reset().
 - fb073a1: risk/risk_manager.py _adjust_position_size, el guard entry~=stop pasa de ABSOLUTO (0.001 unidades de precio) a RELATIVO (1e-5 del entry). Antes bloqueaba el 100% de senales de ADA (0 trades de ADA en la DB).
 - 6d528d9 "seguridad R2": server/bridge.py _EXPOSE_TOKEN derivado de BOTSTRIKE_HOST a nivel de modulo, filtro de redaccion de token en logs, BOTSTRIKE_ALLOW_LIVE (live devuelve 403 sin el), token por cabecera X-BotStrike-Token; deploy/update.sh corre la suite y aborta el restart si falla.
 - v2.13.1: server/bridge.py _merged_performance() = rendimiento realizado desde la trade DB + unrealized vivo; analytics/performance.py ANNUALIZATION_FACTOR 252->365 y parametro use_equity_after.
INVESTIGACION DISPONIBLE (leela si te ayuda a juzgar): tasks/research_r2_trend_evidence.md (replica propia: Sharpe 1.14 neto en BTC; con $1000 lo viable son 3 activos spot; MR intradia y Fibonacci sin edge tras costes), tasks/research_r2_venues_es_2026.md (Binance cerrado para residentes ES desde 1-jul-2026; perps = CFD con tope 2:1 por ESMA), tasks/research_sota_2026.md.
SALIDA: (1) escribe tu informe en ${ROOT}\\tasks\\audit\\r2\\<AREA>.md DE FORMA INCREMENTAL (crea el archivo al empezar con Write; anade cada hallazgo con Edit en cuanto lo confirmes; nunca lo dejes para el final; si el archivo ya existe con contenido de un intento anterior, SOBREESCRIBELO); (2) devuelve los hallazgos estructurados (id "<AREA>-NN", severidad P0=pierde dinero/rompe produccion, P1=bug real impacto medio, P2=mejora importante, P3=cosmetico; file relativo al repo; line entero; evidence con codigo real). NO modifiques ningun archivo del proyecto salvo tu informe. Se brutalmente honesto; si algo esta bien, dilo y no inventes hallazgos.`

const AREAS = [
  { key: 'fix_core', title: 'Revision adversarial de los fixes ronda 1 en core (main.py, execution/order_engine.py, execution/paper_simulator.py, portfolio/portfolio_manager.py, config/settings.py)',
    focus: 'Haz "git show b3dbf75 -- main.py execution/ portfolio/ config/" y lee el codigo resultante. Verifica UNO POR UNO: _flatten_all() (orden cerrar->cancelar, paper y live, flag _dd_flattened y reset, que pasa si close_all falla parcialmente, doble llamada shutdown+halt), is_exit_signal() (todas las acciones reales que emiten las estrategias: grep action en strategies/; falsos positivos/negativos; OJO: hoy tambien lo usa backtesting/backtester.py, asi que un fallo aqui rompe los dos lados), performance factor (formula R-multiples, ventana 50, min 20, clamp, probation 3600s: puede quedarse en bucle?), entries_allowed en _process_symbol (con posicion abierta y gate cerrado: se ejecutan exits? se pueden abrir entradas por otra via?). Busca regresiones: excepciones nuevas no capturadas, awaits faltantes, atributos que no existen en paper_sim/execution_engine segun el modo, tipos (Side/Signal). Ejecuta tests/test_p0_round2.py y lee que asertan de verdad (mocks que enmascaran). ATENCION: con TODAS las estrategias congeladas hoy (allocation 0.00, SYMBOL_STRATEGY_MAP vacio), verifica que el pipeline sigue siendo correcto y que el bot NO abre posiciones por ninguna via alternativa; y que si manana se descongela una estrategia, el camino entries->exits sigue integro.' },
  { key: 'fix_exchange', title: 'Revision adversarial de los fixes ronda 1 en exchange (exchange/binance_client.py, exchange/binance_ws.py) contra la doc oficial de Binance USDT-M Futures',
    focus: 'Haz "git show b3dbf75 -- exchange/" y lee el codigo resultante. Verifica contra https://developers.binance.com/docs/derivatives/usds-margined-futures : exchangeInfo (filtros LOT_SIZE/MARKET_LOT_SIZE/PRICE_FILTER/MIN_NOTIONAL: redondeo floor de qty, tick para price y stopPrice, BUY ceil vs SELL floor en triggers), fallback DEFAULT_SYMBOL_FILTERS (valores reales hoy via GET /fapi/v1/exchangeInfo con WebFetch o curl para BTCUSDT/ETHUSDT/SOLUSDT/ADAUSDT), _retry_request idempotent/recover_fn (GET /fapi/v1/order?origClientOrderId: codigos -2013 vs -2011; timeout en el propio GET; doble envio en batchOrders), newClientOrderId (longitud max 36 y regex oficial), newOrderRespType=RESULT y _await_fill (MARKET puede devolver EXPIRED/PARTIALLY_FILLED? que pasa si executedQty < origQty), reintento -2022 ReduceOnly, depth parser b/a (secuencia pu/u: hay snapshot+update o solo partial depth?), close_all_positions (positionRisk: hedge mode positionSide, qty negativa para short, precision). Ejecuta snippets con py -3.12 importando exchange.binance_client con una sesion falsa para probar _normalize_order_params con los filtros reales. CONTEXTO IMPORTANTE: Binance esta CERRADO para el dueno (residente ES) desde el 1-jul-2026 en modo solo-reducir; el cliente se usa hoy solo para DATOS publicos en paper. Prioriza por tanto: (a) que la ruta de datos publicos sea correcta y robusta, (b) que la ruta de ORDENES no pueda dispararse por accidente, (c) el resto como deuda documentada.' },
  { key: 'persistence', title: 'Persistencia, contabilidad y notificaciones (trade_database/, analytics/, data_lifecycle/, logging_metrics/, notifications/telegram.py, server/serializers.py)',
    focus: 'Comprueba que el PnL sea CONSISTENTE entre paper_simulator, TradeDB, metrics.jsonl, /api/performance y Telegram: fees ambos lados, funding, posiciones abiertas al cerrar sesion (end_session), sesiones huerfanas tras crash/os._exit (verifica que queda en la DB tras un kill -9 y que reporta el siguiente arranque). OJO: /api/performance cambio el 2026-08-31 (server/bridge.py _merged_performance): el realizado sale de la trade DB encadenando pnl (use_equity_after=False) y se le suma el unrealized vivo; VERIFICA esa fusion con datos reales de data/trade_database.db (hay sesiones con final_equity=0 y equity_after que reinicia en initial_capital cada sesion: se contabiliza bien?, hay doble conteo entre realizado y no realizado?). SQLite: WAL, timeouts, escritura desde hilos (backtest en to_thread + engine asyncio sobre el mismo archivo), integridad, crecimiento, indices. logging_metrics: metrics.jsonl ~50 MB/dia con logrotate copytruncate (es seguro con el modo de apertura del archivo?), secretos en logs. Telegram: reintentos, rate limit, bloqueo del loop si falla la red. Analytics: formulas de Sharpe/Sortino/Calmar y unidades (ANNUALIZATION_FACTOR paso a 365 el 2026-08-31: comprueba que es coherente en TODOS los modulos). data_lifecycle: rutas Windows hardcoded, tareas que no existen en Linux.' },
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

const synthesis = await agent('Eres el mejor quant y arquitecto de sistemas de trading. Escribe el informe de la TANDA 2 de la ronda 2 en ' + ROOT + '\\tasks\\audit\\r2_batch2_report.md (en espanol, Write completo). Areas cubiertas: fix_core, fix_exchange, persistence (ejecucion y contabilidad). Contexto: el dueno tiene $1000, reside en Espana (Binance cerrado para el desde jul-2026 en modo solo-reducir), y HOY EL BOT NO OPERA A PROPOSITO: todas las estrategias estan congeladas tras la tanda 1, que midio edge bruto nulo en Mean Reversion. Por tanto la pregunta de esta tanda NO es "por que pierde dinero" sino: **cuando el proyecto tenga una estrategia con edge demostrado (trend diario spot, ver tasks/research_r2_trend_evidence.md), la MAQUINARIA de ejecucion y contabilidad sera digna de confianza para operarla?** Es decir: ejecutaria correctamente las ordenes, cerraria las posiciones cuando toca, y los numeros que reporta (PnL, equity, fees, sesiones) serian ciertos?\nESTRUCTURA: 1) Resumen ejecutivo en 5 puntos con numeros. 2) Tabla de hallazgos confirmados (id, severidad final, area, archivo:linea, titulo, fix en 1 linea) ordenada por severidad. 3) Un parrafo por area con lo esencial y su veredicto. 4) La pregunta de la confianza en la maquinaria respondida con un SI o un NO y su consecuencia practica. 5) Veredicto sobre la contabilidad: los numeros que ve el dueno en la UI y en Telegram son ciertos? donde exactamente mienten? 6) Plan de accion priorizado y secuenciado (P0 hoy / P1 semana / P2 mes) con esfuerzo estimado. 7) Anexo: refutados, P0/P1 sin verificar por tope de tanda, y P2/P3.\nDevuelve SOLO un resumen de 12 lineas.' + input, { label: 'synthesize', phase: 'Synthesize', effort: 'high' })

return {
  confirmed: confirmed.map(f => ({ id: f.id, sev: f.severity_final, area: f._area, title: f.title, file: f.file + ':' + f.line })),
  unverified: notVerified.map(f => ({ id: f.id, sev: f.severity, area: f._area, title: f.title })),
  refutedCount: refuted.length,
  minorCount: minors.length,
  areasDone: areas.map(a => a.area),
  synthesis,
}
