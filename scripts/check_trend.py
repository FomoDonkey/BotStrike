"""Quick BTC trend check across timeframes."""
import warnings; warnings.filterwarnings('ignore')
import json, asyncio

async def analyze():
    import aiohttp
    async with aiohttp.ClientSession() as session:
        urls = {
            '15m': 'https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=15m&limit=100',
            '4h': 'https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=4h&limit=50',
            '1d': 'https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=30',
        }
        data = {}
        for tf, url in urls.items():
            async with session.get(url) as resp:
                raw = await resp.json()
            data[tf] = [float(c[4]) for c in raw]

    def ema(arr, span):
        result = [arr[0]]
        alpha = 2 / (span + 1)
        for i in range(1, len(arr)):
            result.append(alpha * arr[i] + (1 - alpha) * result[-1])
        return result

    closes_15m = data['15m']
    closes_4h = data['4h']
    closes_1d = data['1d']

    # Bot's trend: EMA20 vs EMA50 on 15m bars
    ema20_15m = ema(closes_15m, 20)[-1]
    ema50_15m = ema(closes_15m, 50)[-1]
    bot_trend = 'BULLISH' if ema20_15m > ema50_15m else 'BEARISH'

    print("=== ANALISIS DE TENDENCIA BTC ===")
    print()
    print(f"15m (lo que el bot usa para decidir):")
    print(f"  EMA20 = ${ema20_15m:,.0f}")
    print(f"  EMA50 = ${ema50_15m:,.0f}")
    print(f"  Diferencia: ${ema20_15m - ema50_15m:+,.0f}")
    print(f"  --> Bot dice: {bot_trend}")
    print()

    # 4h
    ema20_4h = ema(closes_4h, 20)[-1]
    ema50_4h = ema(closes_4h, 50)[-1]
    trend_4h = 'BULLISH' if ema20_4h > ema50_4h else 'BEARISH'
    print(f"4H (tendencia intermedia):")
    print(f"  EMA20 = ${ema20_4h:,.0f}")
    print(f"  EMA50 = ${ema50_4h:,.0f}")
    print(f"  --> Tendencia 4H: {trend_4h}")
    print()

    # 1D
    print(f"DIARIO (tendencia macro):")
    print(f"  Ahora:       ${closes_1d[-1]:,.0f}")
    print(f"  Hace 7 dias: ${closes_1d[-7]:,.0f}  ({(closes_1d[-1]/closes_1d[-7]-1)*100:+.1f}%)")
    print(f"  Hace 14 dias: ${closes_1d[-14]:,.0f}  ({(closes_1d[-1]/closes_1d[-14]-1)*100:+.1f}%)")
    print(f"  Hace 30 dias: ${closes_1d[0]:,.0f}  ({(closes_1d[-1]/closes_1d[0]-1)*100:+.1f}%)")
    ema7_1d = ema(closes_1d, 7)[-1]
    ema21_1d = ema(closes_1d, 21)[-1]
    trend_1d = 'BULLISH' if ema7_1d > ema21_1d else 'BEARISH'
    print(f"  EMA7 = ${ema7_1d:,.0f}, EMA21 = ${ema21_1d:,.0f}")
    print(f"  --> Tendencia diaria: {trend_1d}")
    print()

    # Price action last 25h (100 bars of 15m)
    print(f"Accion de precio 25h (15m bars):")
    print(f"  Low:    ${min(closes_15m):,.0f}")
    print(f"  High:   ${max(closes_15m):,.0f}")
    print(f"  Inicio: ${closes_15m[0]:,.0f}")
    print(f"  Ahora:  ${closes_15m[-1]:,.0f}")
    print(f"  Cambio: {(closes_15m[-1]/closes_15m[0]-1)*100:+.2f}%")
    print()

    # Verdict
    print("=== VEREDICTO ===")
    all_bull = bot_trend == 'BULLISH' and trend_4h == 'BULLISH' and trend_1d == 'BULLISH'
    all_bear = bot_trend == 'BEARISH' and trend_4h == 'BEARISH' and trend_1d == 'BEARISH'

    if all_bull:
        print("TODOS los timeframes BULLISH --> Solo BUY es CORRECTO")
    elif all_bear:
        print("TODOS los timeframes BEARISH --> Solo SELL seria correcto")
    else:
        print(f"MIXTO: 15m={bot_trend}, 4h={trend_4h}, 1d={trend_1d}")
        if bot_trend == 'BULLISH' and trend_1d == 'BEARISH':
            print("PROBLEMA: Bot ve bullish en 15m pero diario es bearish!")
            print("Solo comprar en tendencia bajista diaria es RIESGOSO")
        elif bot_trend == 'BEARISH' and trend_1d == 'BULLISH':
            print("Bot es conservador, no opera contra tendencia diaria")

asyncio.run(analyze())
