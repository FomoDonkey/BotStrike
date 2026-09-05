# Auditoría de optimalidad — ¿está el bot diseñado de la forma óptima? (2026-09-05)

Pregunta de Edgar: "¿es lo óptimo tal y como está, en todo lo que hace y cómo lo hace? Ejemplo: que el
universo se recalcule una vez al mes, ¿es lo mejor pensado, o revisarlo según cómo esté el mercado?"

Método: para cada decisión de diseño, qué hace hoy, qué evidencia MEDIDA la sostiene (panel de 14
mercados, 3.654 días, mismo código que ejecuta el bot) y el veredicto. Donde no había medida, se ha
medido hoy (`scripts/universe_repick_study.py`). Recordatorio estadístico: con 10 años de datos el
error estándar del Sharpe es ±0,53; dos configuraciones solo son distinguibles si difieren en > 1,5.
Casi nada de lo que sigue se distingue de la base; eso ES el resultado: el sistema está en una meseta.

## 1. El universo — tu ejemplo, medido hoy

| Re-selección | Sharpe | CAGR | maxDD | rotación/año | cambios de universo/año |
|---|---|---|---|---|---|
| diaria | 1,88 | 12,4 % | 8,2 % | 16,5 | 2,3 |
| semanal | 1,87 | 12,3 % | 8,2 % | 16,4 | 1,6 |
| **mensual (el bot)** | **1,92** | **12,5 %** | **8,2 %** | **15,9** | **1,1** |
| trimestral | 1,89 | 12,3 % | 8,2 % | 14,7 | 0,9 |
| anual | 1,62 | 10,4 % | 8,0 % | 14,9 | 0,3 |
| fija (una vez, nunca más) | 1,15 | 11,2 % | 14,5 % | 23,1 | 0,1 |
| mensual + evento de correlación ("según el mercado") | 1,91 | 12,5 % | 8,2 % | 16,1 | 1,9 |

Lectura: de diaria a trimestral todo cae en 0,05 de Sharpe (ruido); revisar más a menudo solo añade
rotación. Revisar menos (anual) pierde 0,3 porque los mercados nuevos tardan un año en entrar y las
correlaciones cambian; no revisar nunca es claramente peor (maxDD 14,5 %). **Mensual está en la meseta
con el menor número de cambios.** "Adaptarlo al mercado" ya ocurre, pero en el sitio correcto: el
peso de cada mercado va a 0 cuando no hay tendencia (Donchian), el tamaño baja cuando sube la
volatilidad (vol targeting) y las correlaciones se re-evalúan en cada selección. La pertenencia al
universo es elegibilidad (liquidez, diversificación, historia), no timing; hacerla depender del
mercado convierte la selección en otra apuesta más, y la variante de evento lo confirma: re-seleccionar además cada vez que una pareja tenida cruza el tope de correlación da exactamente lo mismo (1,91 vs 1,92) con casi el doble de cambios de universo (1,9/año vs 1,1).

Tope de correlación con la regla del motor (mensual):

| Tope | Sharpe | CAGR | maxDD | miembros medios | cambios/año |
|---|---|---|---|---|---|
| 0,60 | 2,05 | 13,9 % | 9,4 % | 4,5 | 3,4 |
| 0,70 | 1,95 | 12,7 % | 7,9 % | 4,8 | 3,1 |
| **0,85 (el bot)** | **1,92** | **12,5 %** | **8,2 %** | **5,3** | **1,1** |
| 0,95 | 1,86 | 12,4 % | 9,6 % | 5,3 | 0,4 |
| sin tope | 1,65 | 11,3 % | 9,0 % | 5,4 | 0,1 |

Lectura: la dirección es consistente con el primer estudio (más estricto → algo mejor Sharpe; sin tope → claramente peor), pero 0,6 lo consigue con 4,5 mercados en vez de 5,3 (menos diversificación, pesos 1/N más grandes) y una caída máxima mayor (9,4 %); 0,7 mejora ambas cosas en 0,03 de Sharpe y 0,3 puntos de DD, que es ruido. Nada supera el umbral de distinguibilidad (1,5). Veredicto: 0,85 se queda; 0,70 es el candidato a revisar cuando haya más historia, no ahora.

Lo demás del universo: pool de 12 de 31 (acciones sueltas fuera por sesgo de supervivencia: Strike
lista a los ganadores de hoy — medido 1,76 con ellas vs 1,81 sin), N = 6 (N = 3 / 8 / 10 → 1,44 /
1,81 / 1,83: más no mejora), una por clase y luego por historia, nunca por rentabilidad pasada, y el
suelo de liquidez del venue, que hoy se ha descubierto que NO se estaba aplicando (b7af4ba: salida
diaria, fail-closed). Veredicto: **correcto; no tocar**. Decisión pendiente de Edgar: el pool.

## 2. La señal

Donchian ensemble 5/10/20/30/60/90, solo largos, trailing stop que nunca baja, comprobado al cierre.
- Acelerar (lookbacks ×0,5) → 1,89; ×0,25 rompe el sistema. Ralentizar (×1,5) → 1,70. Medido.
- Stop intradía (mirar el mínimo del día) → cuesta 1,7 puntos de CAGR. Mirar una vez al día es parte
  de por qué funciona, no una carencia.
- Cortos: simétricos 1,57 (peor); **a media posición 1,92 con maxDD 5,6 % vs 7,6 %**, pero restan en
  los últimos cuatro años (1,61 vs 1,81 en la segunda mitad). Está implementado como opción, apagado.
  Es un seguro con prima, no una fuente de rendimiento. Decisión de Edgar.
Veredicto: **óptimo dentro de lo distinguible**.

## 3. El tamaño

Vol targeting por activo (90 días, anualización por activo: 365 cripto / 252 TradFi), 1/N,
techo 3×, objetivo 0,80 (agresivo, elegido por Edgar con los tres perfiles validados 11/11 y las
colas medidas: peor día −9,1 %, 721 días bajo pico). Ventana de vol 45/135 → 1,86/1,78 (ruido).
Interés compuesto activado (el tamaño sigue al equity, que es lo que se validó).
Veredicto: **correcto**. El nivel de riesgo es el dial del usuario, no una decisión de diseño.

## 4. La ejecución

- Una vez al día a las 04:05 UTC, al mark del venue ± media horquilla MEDIDA por mercado (0,1 bps en
  BTC, 4 en oro) + taker 5 bps; banda muerta del 20 % (umbral medido: da igual en todo el rango);
  reglas del venue (step, tick, mínimo 10 $). Correcto.
- Las patas cripto ejecutan 4 h después de su cierre diario (00:00 UTC) porque los futuros/índices
  no están asentados en Yahoo hasta las 04:00 UTC. Un día entero de retraso cuesta 0,21 de Sharpe
  (shift 2 → 3); 4 h son ~0,03 en la mitad cripto del libro: ruido. Dos ejecuciones al día no
  compensan la complejidad.
- **La palanca real está en vivo, no en papel**: órdenes limit post-only (maker −0,5 bps de rebate)
  en vez de market (taker 5 bps) para rebalanceos que no tienen prisa. Con 15,9 vueltas/año a 1×
  son ~0,9 %/año de equity; con el libro agresivo (~3× de exposición) ~2,3 %/año. En papel NO se
  simula (sería suponer ejecuciones que no se pueden demostrar); en vivo, limit con timeout y
  fallback a market.

## 5. El riesgo

Límites diario/semanal/drawdown mark-to-market, recortados de la cola medida de cada perfil; circuit
breaker; puerta de riesgo que bloquea añadir; baselines persistentes. Monitor de edge: mata una
estrategia con t-stat ≤ −2 sobre 100–200 salidas reales — para el libro trend (~90 salidas/año) eso
es un juicio a 1–2 años, por diseño: un año plano NO lo mata (t ≈ 0). Hoy se ha corregido que los
recortes de rebalanceo contaban como operaciones (ac986b5).
Veredicto: **correcto**.

## 6. Los datos

Señal en Yahoo (TradFi) / Binance (cripto) con velas asentadas, cura de la caché, monitor de base
frente al mark de Strike; dinero (marks, fills, funding, reglas, fees) en Strike. Las velas de Strike
no sirven para la señal (medido: 20–135 días de historia, un tercio de días sin trade en oro).
Veredicto: **correcto**.

## 7. Lo que de verdad movería la aguja

No es un parámetro. Es, por orden:
1. **El canario con dinero real.** Cuatro días de tracking en papel y tres salidas reales no son un
   historial; un Sharpe de 1,92 en papel es una promesa. Bloqueado por decisión regulatoria de Edgar.
2. **Ejecución maker en vivo** (§4): 1–2 %/año medibles, sin tocar la estrategia.
3. **Las dos decisiones pendientes**: pool (acciones sueltas) y cortos a media posición.

## Veredicto global

El diseño está en una meseta medida: ninguna variante probada (frecuencia de universo, N, tope de
correlación, lookbacks, ventana de vol, umbral de rebalanceo, cadencia intradía) se distingue de la
base más allá del ruido, y las que se distinguen son peores. Lo que sí estaba mal era de ejecución
(el suelo de liquidez sin aplicar, el régimen tras reinicio, los baselines) y se ha corregido hoy.
