# Divergence: veredicto final tras ampliar a 30 mercados (2026-09-04)

El informe del 2 de septiembre dejó dos condiciones pre-registradas para reabrir el caso:

1. *"≥ 100 operaciones de 4h con tendencia. A 4 al año eso significa añadir símbolos, no esperar."*
2. *"Cualquier cosa por debajo de t = 2 después de eso sigue desactivada."*

Ambas se han ejecutado hoy. Universo: **30 mercados que nunca se usaron para diseñar la estrategia**
(los 6 originales —BTC, ETH, SOL, ADA, BNB, XRP— quedan fuera precisamente porque sí se usaron),
40.968 barras de 1h cada uno, 2022-01 → 2026-09.

## Resultados, misma configuración, sin tocar un solo parámetro

| Línea | n | PF | Bruto/op | Neto/op | t | Sharpe | maxDD |
|---|---|---|---|---|---|---|---|
| 1h regular | 1.136 | 0,91 | −1,2 bps | −17,2 bps | −0,08 | −0,41 | 59,6 % |
| 1h ocultas | 1.347 | 0,84 | −13,3 bps | −29,3 bps | −1,21 | −0,92 | 76,5 % |
| **4h regular (la esperanza)** | **1.479** | **1,01** | **+20,6 bps** | **+4,6 bps** | **+0,95** | −0,05 | 61,2 % |
| 4h con tendencia | 115 | 1,10 | +40,9 bps | +24,9 bps | +0,62 | −0,32 | 12,5 % |
| 4h pivot k=5 | 1.404 | 0,94 | −2,6 bps | −18,6 bps | −0,12 | −0,41 | 66,2 % |

## Lo que pasó con la línea prometedora

Sobre 6 mercados el 4h daba **PF 1,11, neto +34,4 bps, t +1,09** con 323 operaciones. Al ampliar a 30
mercados y 1.479 operaciones:

- El neto se desploma de **+34,4 bps a +4,6 bps**.
- El bruto cae de +50,4 a +20,6 bps.
- El t-stat se queda clavado en ~1 (de 1,09 a 0,95).

Esa es la firma exacta de un espejismo de muestra pequeña: **el tamaño del efecto se derrumba cuando
crece n mientras el t-stat no se mueve.** Si hubiera señal, al quintuplicar las operaciones el efecto
se mantendría y el t-stat subiría con √n. Aquí pasa lo contrario.

Y la variante con tendencia repitió el patrón que el propio informe original anticipó: de **PF 3,28
con 13 operaciones** a **PF 1,10 con 115**, t = 0,62.

## GO/NO-GO del 4h con 30 mercados: 2 de 7

| Puerta | Resultado |
|---|---|
| n ≥ 300 | ✅ 1.479 |
| Sin artefacto de look-ahead | ✅ |
| PF neto ≥ 1,2 | ❌ 1,01 |
| t ≥ 2 | ❌ 0,95 |
| Sharpe ≥ 0,8 | ❌ −0,05 |
| maxDD < 15 % | ❌ 61,2 % |
| PF > 1 a 15 bps/lado | ❌ 0,97 |

El detalle que lo cierra: el bruto sigue siendo positivo (+20,6 bps), pero **el margen es menor que los
costes que necesita para sobrevivir**. A 15 bps/lado pierde. Con los costes medidos en Strike (4,6-8,5
bps/lado) el neto es +4,6 bps, que con t = 0,95 es indistinguible de cero.

## Veredicto

**Retirada.** No por sus dos condiciones pre-registradas: se cumplieron las dos y las falló.

No es un problema de optimización. La familia entera se ha medido en cinco configuraciones con
muestra suficiente y ninguna produce una expectativa neta distinguible de cero. Cada ensayo adicional
sobre la misma familia además encarece el listón estadístico, y ninguno ha aportado nada.

El bot se queda con **una estrategia validada y ninguna pretendiente**. Eso es mejor sitio del que
estaba: antes tenía una tarjeta más sugiriendo que algún día habría algo ahí.
