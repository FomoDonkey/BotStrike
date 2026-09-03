# ¿Cortos y más frecuencia? — medido, no opinado (2026-09-04)

**Pregunta de Edgar:** detectar bien los cambios de tendencia para poder operar cortos, y rebalancear
más veces al día si hace falta.

**Método:** el mismo panel, los mismos costes (8 bps/lado) y el mismo funding medido en Strike que
validaron la configuración actual. 14 mercados, 2016-09-02 → 2026-09-03, 3.654 días. 16
configuraciones, todas contadas como ensayos para el Sharpe deflactado.

Base actual: **Sharpe 1,92 · CAGR 11,2 % · maxDD 7,6 % · rotación 13,9×/año.**

## 0. El umbral de ruido (lo que casi nadie calcula)

Con 10 años de datos, el error estándar del Sharpe es **±0,53**. Para que dos configuraciones sean
distinguibles con un 95 % de confianza, la diferencia tiene que superar **1,48 de Sharpe**.

Esto reordena todo lo que viene: casi ninguna de las "mejoras" que salen abajo es real. Lo que sí es
real es lo que empeora mucho, y lo que mejora de forma **consistente en todos los escenarios**.

## 1. Reaccionar más rápido

| Configuración | Sharpe | CAGR | maxDD | rotación/año |
|---|---|---|---|---|
| Base (5,10,20,30,60,90) | 1,92 | 11,2 % | 7,6 % | 13,9 |
| Ventanas ×0,75 | 2,05 | 12,0 % | 7,7 % | 17,8 |
| Ventanas ×1,5 (más lento) | 1,87 | 10,9 % | **6,7 %** | 9,0 |
| Ventanas ×0,5 | 1,56 | 9,0 % | 10,5 % | 33,2 |
| Ventanas ×0,4 | 1,48 | 8,6 % | 10,5 % | 38,5 |
| Ventanas ×0,25 | **0,61** | 3,4 % | 16,2 % | 61,6 |

**Veredicto: acelerar destruye la estrategia.** El deterioro es monótono y a ×0,25 la caída de Sharpe
(1,31) roza el umbral de significancia con un patrón inequívoco. El ×0,75 es el mejor número de la
tabla pero está dentro del ruido: no hay evidencia para cambiar.

La razón es económica, no estadística: la rotación pasa de 13,9 a 61,6 vueltas al año. A 8 bps/lado
eso es ~1 punto de equity al año en comisiones que hay que ganar antes de empezar.

## 2. Rebalancear más veces

| Umbral de rebalanceo | Sharpe | CAGR | maxDD | rotación/año |
|---|---|---|---|---|
| 0,00 (rebalanceo total diario) | 1,97 | 11,4 % | 7,7 % | 15,4 |
| 0,05 | 1,98 | 11,5 % | 7,7 % | 15,1 |
| 0,10 | 1,97 | 11,5 % | 7,6 % | 14,9 |
| **0,20 (actual)** | 1,92 | 11,2 % | 7,6 % | 13,9 |
| 0,30 | 1,96 | 11,6 % | 7,9 % | 12,6 |

**Veredicto: da igual.** Todo el rango cae en 0,06 de Sharpe — ruido puro. El umbral actual no está
ni mejor ni peor que rebalancear cada día al objetivo exacto.

### Y vigilar el stop intradía es PEOR

| | Sharpe | CAGR | maxDD |
|---|---|---|---|
| Stop comprobado al cierre (actual) | 1,92 | 11,2 % | 7,6 % |
| Stop comprobado contra el mínimo del día | **1,82** | 9,5 % | 8,0 % |

Salir en cuanto el precio toca el nivel, en vez de esperar al cierre, **cuesta 1,7 puntos de CAGR**.
Las mechas intradía te sacan de tendencias que el día cierra intactas. Que el bot mire una vez al día
no es una limitación: es parte de por qué funciona.

## 3. El lado corto

| Diseño | Sharpe | CAGR | maxDD | rotación/año |
|---|---|---|---|---|
| Base long-only | 1,92 | 11,2 % | 7,6 % | 13,9 |
| **Cortos a media posición** | **1,92** | **11,5 %** | **5,6 %** | 18,3 |
| Cortos media + bajo la media de 200d | 1,86 | 11,4 % | 6,4 % | 16,2 |
| Cortos solo cripto+energía | 1,70 | 11,7 % | 5,9 % | 17,3 |
| Cortos simétricos (tamaño completo) | 1,57 | 11,8 % | 6,3 % | 24,4 |
| Cortos solo bajo la media de 200d | 1,56 | 11,7 % | 7,6 % | 19,3 |

El corto simétrico es el que ya se había medido (1,57) y por eso se descartó. **Sizing a la mitad
cambia el resultado**: mismo Sharpe, algo más de CAGR y la caída máxima baja de 7,6 % a 5,6 %.

### Estrés — la caída baja en los diez escenarios

| Escenario | Sharpe base | Sharpe cand. | maxDD base | maxDD cand. |
|---|---|---|---|---|
| Costes 8 bps | 1,92 | 1,92 | 7,6 % | **5,6 %** |
| Costes 15 bps | 1,75 | 1,70 | 8,1 % | **6,2 %** |
| Costes 25 bps | 1,50 | 1,38 | 10,0 % | **7,3 %** |
| Funding ×2 | 1,90 | 1,92 | 7,9 % | **5,5 %** |
| Funding ×3 | 1,87 | **1,93** | 8,1 % | **5,4 %** |
| Vol objetivo 0,10 | 1,93 | 1,93 | 3,9 % | **3,0 %** |
| Vol objetivo 0,30 | 1,92 | 1,91 | 11,2 % | **7,3 %** |
| N = 3 mercados | 1,82 | 1,76 | 8,4 % | **7,1 %** |
| N = 10 mercados | 1,94 | 1,95 | 7,2 % | **5,6 %** |
| Sin tope de correlación | 1,67 | 1,73 | 7,7 % | **5,6 %** |

Dos cosas que sí son señal y no ruido:

1. **La caída baja en los diez escenarios**, sin excepción. Una ventaja consistente en escenarios
   emparejados dice mucho más que una diferencia de Sharpe en un único número.
2. **Cuanto más caro el funding, mejor va el candidato.** A funding ×3 el candidato mejora (1,93) y
   la base empeora (1,87). Tiene explicación estructural: en Strike **el corto COBRA el funding**
   cuando la tasa es positiva, que es la mayoría del tiempo (mediana +8,1 %/año). Es la única
   cobertura natural que tiene el libro contra un encarecimiento del carry.

### Y lo que juega en contra

| Ventana | Sharpe base | Sharpe candidato |
|---|---|---|
| Primera mitad (2016-2021) | 2,04 | **2,24** |
| Segunda mitad (2021-2026) | 1,81 | **1,61** |
| 2022 en adelante | 1,94 | **1,73** |
| 2024 en adelante | 2,75 | **2,38** |

**El lado corto aportó en la primera mitad y ha restado en los últimos cuatro años.** No es una
fuente de rendimiento: es un seguro, y como todo seguro tiene una prima. Además la rotación sube de
13,9 a 18,3 vueltas al año, lo que lo hace más frágil a costes altos (a 25 bps pierde 0,12 de Sharpe
contra la base).

Auditoría de look-ahead — ambos degradan igual con un día extra de retraso, así que no hay artefacto:

| Desplazamiento | Base | Candidato |
|---|---|---|
| 1 (prohibido, usa la señal antes de tiempo) | 5,52 | 6,38 |
| 2 (especificación) | 1,95 | 1,92 |
| 3 (un día extra) | 1,74 | 1,64 |

## 4. Veredicto

1. **Rebalancear más veces al día: NO.** Medido, no cambia nada, y mirar el stop intradía cuesta 1,7
   puntos de CAGR. La cadencia diaria no es una carencia por corregir.
2. **Acelerar las señales: NO.** El deterioro es monótono y a ×0,25 el sistema se rompe.
3. **Cortos a media posición: implementar como OPCIÓN, apagada por defecto.** La reducción de caída
   es consistente y la cobertura contra el funding es estructural, pero ha restado rendimiento en el
   régimen reciente. La evidencia no justifica cambiar la configuración viva; sí justifica tenerlo
   disponible y medible.

**Lo que de verdad movería la aguja no está en esta tabla.** El limitante hoy no es el diseño: son
tres operaciones cerradas de historial real y una cuenta con 2,84 USDT. Un Sharpe de 1,92 en papel
con n=3 no es un resultado, es una promesa. La siguiente decisión con valor es el canario con dinero
real, no otro parámetro.
