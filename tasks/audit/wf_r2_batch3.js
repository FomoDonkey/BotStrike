export const meta = {
  name: 'botstrike-audit-r2-batch3',
  description: 'Auditoria R2 tanda 3: senal, venue y calidad de tests (microstructure, hyperliquid, tests_quality)',
  phases: [
    { title: 'Find', detail: '3 finders sobre areas disjuntas, informe incremental en tasks/audit/r2/<area>.md' },
    { title: 'Verify', detail: '2 lentes adversariales sobre los P0/P1 mas severos (tope global para que la tanda quepa en una sesion)' },
    { title: 'Synthesize', detail: 'informe de la tanda en tasks/audit/r2_batch3_report.md' },
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
  { key: 'microstructure', title: 'Microestructura y datos (core/microstructure.py, core/microprice.py, core/market_data.py, exchange/binance_ws.py parseo de aggTrade/kline/markPrice)',
    focus: 'Verifica matematicamente VPIN (bucket por volumen USD, clasificacion bulk volume, ventana n_buckets, umbral toxic: con 50k USD por bucket en BTC cuantos segundos dura un bucket hoy? 01-F22), Hawkes (estimacion online de mu/alpha/beta: es estable? branching ratio alpha/beta<1?), Kyle lambda (regresion rolling: signo, unidades, R2, validez), microprice compuesto (01-F20 clamp), OBI (niveles, decay). Perf: mide con py -3.12 el coste de compute_all/on_trade/on_bar con datos reales (01-F18 dice 169 ms en el callback del WS) y busca bloqueos del event loop, listas sin limite (memoria a 24h: extrapola), locks. Datos: seed de klines (vela en formacion), alineacion de barras 1m al reloj, stale tick guard, markPrice/funding handler, timestamps ms vs s. PREGUNTA CENTRAL, respondela con numeros: la microestructura APORTA ALGO MEDIBLE o es coste computacional sin retorno? La tanda 1 demostro que Mean Reversion no tiene edge ni bruto; si los filtros de microestructura tampoco discriminan (mide la separacion entre trades ganadores y perdedores por VPIN/Hawkes/risk_score sobre los trades reales de data/trade_database.db y sobre klines reales), hay que decirlo y proponer archivar el modulo.' },
  { key: 'hyperliquid', title: 'Integracion Hyperliquid en profundidad (exchange/hyperliquid_client.py, exchange/hyperliquid_ws.py, uso desde main.py/bridge)',
    focus: 'Es el unico venue potencialmente ejecutable para el dueno (residente en Espana; Binance en modo solo-reducir desde jul-2026). YA EXISTE investigacion propia en tasks/research_r2_hyperliquid_execution.md (580 lineas): LEELA PRIMERO y usala como referencia, no la repitas. Verifica el CODIGO contra ella y contra la doc oficial: order types (limit tif Alo/Ioc/Gtc, trigger tp/sl isMarket, reduceOnly, grouping normalTpsl/positionTpsl), szDecimals y 5 cifras significativas (float_to_wire), minimo 10 USD, fees, funding HORARIO (no 8h), margen cross/isolated, rate limits por direccion, WS (userFills, orderUpdates, reconexion), testnet. CONFIRMA O REFUTA las dos trampas que documenta la investigacion: (a) DEFAULT_SLIPPAGE=0.05 en el SDK -> market_open sin slippage explicito envia IOC a +-5% del mid (hasta 500 bps sobre $1000); (b) market_close() prioriza account_address sobre wallet.address -> con agent wallet y sin account_address NO cierra nada, en silencio. Verifica si el codigo del repo cae en ellas. Verifica tambien 02-P1-13 (SL/TP crashean por float_to_wire(str); opera en mainnet aunque se pida testnet). Entrega la lista EXACTA y ordenada de cambios para que paper y live en Hyperliquid sean seguros, con estimacion de esfuerzo en horas.' },
  { key: 'tests_quality', title: 'Calidad de la suite de tests (tests/*.py) y huecos de cobertura en los caminos que pierden dinero',
    focus: 'Hoy son 138 tests. Lee cada uno y clasifica: que asertan de verdad, que mocks enmascaran comportamiento (p.ej. sesion aiohttp falsa que siempre devuelve FILLED; TestClient con engine mockeado), tests tautologicos, y tests que pasarian aunque el bug volviera (comprueba 3-5 revirtiendo el fix en una COPIA del repo en el scratchpad -- NUNCA modifiques el repo). Mide cobertura real con pytest-cov (instalalo en un venv temporal del scratchpad si no esta) y lista las funciones criticas sin cobertura: _flatten_all, close_all_positions live, _await_fill, _normalize_order_params, _retry_request recover, watchdog restart, paper SL/TP con high/low, risk validate_signal, allocation. CONTEXTO CRITICO: la ronda 2 ha demostrado DOS VECES que un fix se aplico a un solo lado del sistema y los tests no lo detectaron (exit_fibonacci arreglado solo en live; la posicion desnuda arreglada solo en el CLI mientras systemd corre el bridge). Diagnostica POR QUE la suite no detecto eso y propone la lista concreta de 15-25 tests que faltan (nombre, precondicion, asercion), priorizando los que habrian pillado esos dos casos.' },
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

const synthesis = await agent('Eres el mejor quant y arquitecto de sistemas de trading. Escribe el informe de la TANDA 3 (ultima) de la ronda 2 en ' + ROOT + '\\tasks\\audit\\r2_batch3_report.md (en espanol, Write completo). Areas cubiertas: microstructure, hyperliquid, tests_quality (senal, venue ejecutable y calidad de la red de seguridad). Contexto: el dueno tiene $1000, reside en Espana (Binance cerrado para el desde jul-2026 en modo solo-reducir), y HOY EL BOT NO OPERA A PROPOSITO: todas las estrategias estan congeladas tras la tanda 1, que midio edge bruto nulo en Mean Reversion. Por tanto la pregunta de esta tanda NO es "por que pierde dinero" sino: **cuando el proyecto tenga una estrategia con edge demostrado (trend diario spot, ver tasks/research_r2_trend_evidence.md), la MAQUINARIA de ejecucion y contabilidad sera digna de confianza para operarla?** Es decir: ejecutaria correctamente las ordenes, cerraria las posiciones cuando toca, y los numeros que reporta (PnL, equity, fees, sesiones) serian ciertos?\nESTRUCTURA: 1) Resumen ejecutivo en 5 puntos con numeros. 2) Tabla de hallazgos confirmados (id, severidad final, area, archivo:linea, titulo, fix en 1 linea) ordenada por severidad. 3) Un parrafo por area con lo esencial y su veredicto. 4) La pregunta de la confianza en la maquinaria respondida con un SI o un NO y su consecuencia practica. 5) Veredicto sobre la contabilidad: los numeros que ve el dueno en la UI y en Telegram son ciertos? donde exactamente mienten? 6) Plan de accion priorizado y secuenciado (P0 hoy / P1 semana / P2 mes) con esfuerzo estimado. 7) Anexo: refutados, P0/P1 sin verificar por tope de tanda, y P2/P3.\nDevuelve SOLO un resumen de 12 lineas.' + input, { label: 'synthesize', phase: 'Synthesize', effort: 'high' })

return {
  confirmed: confirmed.map(f => ({ id: f.id, sev: f.severity_final, area: f._area, title: f.title, file: f.file + ':' + f.line })),
  unverified: notVerified.map(f => ({ id: f.id, sev: f.severity, area: f._area, title: f.title })),
  refutedCount: refuted.length,
  minorCount: minors.length,
  areasDone: areas.map(a => a.area),
  synthesis,
}
