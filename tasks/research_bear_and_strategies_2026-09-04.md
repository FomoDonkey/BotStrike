# Mercado bajista, y por qué no funcionan las otras estrategias (2026-09-04)

Dos preguntas de Edgar: (1) si entramos en un invierno cripto largo, ¿el bot se queda parado?, y
(2) ¿las otras estrategias fallan por estar mal optimizadas o porque no tienen ventaja?

## 1. El libro NO se queda parado en un bajista — pero los cortos aportan mucho ahí

Reloj de ciclo: día bajista = BTC más de 40 % por debajo de su máximo. Son **1.829 de los 3.654 días**
de la muestra, la mitad del histórico.

| Ventana | Días | Base long-only | Con cortos ½ | Tiempo en mercado | Días con algún corto |
|---|---|---|---|---|---|
| **Todos los días bajistas** | 1.829 | **+32,1 %** | **+53,8 %** | 99 % → 100 % | 85 % |
| Todos los días no bajistas | 1.825 | +120,0 % | +93,6 % | 78 % | 60 % |
| Invierno cripto 2018-19 | 730 | +17,3 % | +23,4 % | 99 % → 100 % | 85 % |
| Bajista 2022-23 | 730 | +5,1 % | +6,0 % | 99 % → 100 % | 88 % |
| Alcista 2020-21 | 731 | +35,7 % | +40,2 % | 98 % → 100 % | 68 % |
| 2024 en adelante | 977 | +59,3 % | +49,7 % | 100 % | 85 % |

Rachas más largas sin ninguna posición abierta, libro long-only:

| Días parado | Ventana |
|---|---|
| 394 | 2016-09 → 2017-10 (arranque de la muestra, sin historial para calcular señales) |
| 11 | marzo 2020 (el crash del covid) |
| 4 | junio-julio 2022 |

**La premisa era falsa: el bot no se queda sin operar.** En los días bajistas está en mercado el 99 %
del tiempo y gana +32,1 %. La razón es que no es un bot de cripto: cuando cripto cae, el oro, el S&P,
la plata y el petróleo tienen sus propias tendencias. Eso es exactamente lo que compra la
diversificación. Fuera del arranque de la muestra, la racha más larga sin posiciones son **11 días**.

**Pero la intuición sobre los cortos es correcta:** durante los días bajistas el lado corto añade
**21,7 puntos** (+53,8 % contra +32,1 %) y 6 puntos en el invierno 2018-19. Y cuesta ~10 puntos en el
alcista reciente. Es exactamente el perfil de un seguro: paga cuando duele.

## 2. Las otras estrategias: ninguna está mal optimizada, están muertas

La prueba que distingue "mal optimizada" de "sin ventaja" es el **retorno BRUTO antes de comisiones**.
Si el bruto es positivo y el neto negativo, es un problema de costes y se arregla. Si el bruto es
cero, no hay nada que optimizar.

### Mean Reversion — muerta, con dos controles que lo cierran

Sobre 149,7 días de klines reales y 2.284 operaciones con el código exacto de producción:

- Bruto por operación: **−0,90 / −0,63 / −2,05 / +0,45 bps** (ETH/SOL/ADA/BTC), errores estándar de
  1,2-2,6 bps. Es un cero estadístico.
- Neto: PF 0,40-0,60, t-stat −5 a −8,7. Pierde en 20 de 20 bloques de 30 días.
- **Control 1:** entradas aleatorias con la misma frecuencia rinden igual.
- **Control 2, el decisivo: invertir el lado de TODAS las señales no mejora el resultado.**

Ese último control es el que zanja la pregunta. Una estrategia mal diseñada pero con información
direccional mejora al invertirla. Esta no. No hay señal que optimizar.

### Fibonacci — misma familia de evidencia

t = −2,6, PSR(0) = 0,005, bootstrap del PnL enteramente negativo. Congelada por la misma razón.

### Divergence — replicada FUERA DE MUESTRA hoy

El informe original dejó una hipótesis pre-registrada: *"las divergencias ocultas son la única línea
de la familia con expectativa bruta no negativa; merece un ensayo más sobre una ventana fresca"*.
Hoy se ejecutó sobre **seis mercados que el estudio nunca vio** (LTC, DOGE, LINK, AVAX, DOT, ATOM,
40.967 barras de 1h cada uno, 2022-2026):

| Variante | n | PF | Bruto/op | Neto/op | t |
|---|---|---|---|---|---|
| 1h regular (la base) | 1.136 | 0,91 | **−1,2 bps** | −17,2 bps | −0,08 |
| **1h ocultas** (la hipótesis pre-registrada) | 1.347 | 0,84 | **−13,3 bps** | −29,3 bps | **−1,21** |
| 1h con tendencia (EMA200) | 64 | 1,17 | +44,3 bps | +28,3 bps | +0,80 |
| **4h base** | 323 | 1,11 | **+50,4 bps** | **+34,4 bps** | **+1,09** |

**La hipótesis pre-registrada muere fuera de muestra.** Las divergencias ocultas dan bruto −13,3 bps
y t −1,21 sobre 1.347 operaciones en mercados nuevos: no era una promesa, era ruido de la muestra
original. El 1h regular se replica igual de muerto (bruto −1,2 bps).

**La única con pulso es la de 4h**: bruto +50,4 bps y neto +34,4 bps sobre 323 operaciones en datos
que nunca vio. Pero t = 1,09 y el listón del proyecto es t ≥ 2. No está probada, y tampoco muerta.

## 3. Veredicto

1. **El bajista no deja al bot parado.** La diversificación multiactivo ya resuelve eso: 99 % del
   tiempo en mercado durante los 1.829 días bajistas.
2. **Los cortos sí son la palanca del bajista**, con +21,7 puntos en esas ventanas a cambio de ~10 en
   el alcista reciente. La decisión es de preferencia, no de evidencia: es un seguro con prima.
3. **MR y Fibonacci están muertas, no mal optimizadas.** El control de inversión de señal lo cierra.
4. **Divergence 4h es lo único con expectativa bruta positiva fuera de muestra.** Para pasar el listón
   necesita t ≥ 2, o sea ~1.000 operaciones, o sea más mercados: a 70 operaciones al año con 6
   símbolos, son 10 años de espera o 4x más símbolos.

Lo que NO cambia: con tres operaciones cerradas de historial real, el limitante sigue sin ser el
diseño.
