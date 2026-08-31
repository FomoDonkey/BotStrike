#!/usr/bin/env python
"""Trend diario (Donchian ensemble) — investigacion y validacion GO/NO-GO.

Implementa la especificacion EXACTA de tasks/research_r2_trend_evidence.md §11.2 y
ejecuta la checklist de aceptacion §11.3. Es deliberadamente INDEPENDIENTE de
backtesting/backtester.py: la auditoria R2 (backtest_parity-01/02) demostro que aquel
motor no tiene paridad con el live (42,9% de solapamiento de senales), asi que usarlo
para decidir si una estrategia merece capital seria repetir el error que congelo a
Mean Reversion. Este script es pequeno y auditable a proposito.

Datos: klines DIARIOS de Binance SPOT (api.binance.com, sin API key), cacheados en
data/binance_daily/<SYMBOL>.parquet. Spot porque la estrategia es long-only y el
research (§10.2) recomienda ejecutar en spot: funding = 0 y sin riesgo de liquidacion.

Uso:
    py -3.12 scripts/trend_daily_research.py                 # descarga (cache) + validacion
    py -3.12 scripts/trend_daily_research.py --no-download   # solo cache local
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Especificacion §11.2 (NO optimizar en la primera pasada) ──────────────────
LOOKBACKS = [5, 10, 20, 30, 60, 90]
TARGET_VOL = 0.20          # anual
VOL_WINDOW = 90            # dias
ANNUALIZATION = 365        # cripto opera 365 dias
LEVERAGE_CAP = 2.0
N_ASSETS = 3
REBALANCE_THRESHOLD = 0.20  # solo sobre cambios inducidos por volatilidad
COMMISSION_BPS = 5.0        # por lado, taker
SLIPPAGE_BPS = 3.0          # majors
MIN_LISTING_DAYS = 365
LIQ_ENTER_USD = 2_000_000   # volumen mediano 30d para entrar
LIQ_EXIT_USD = 1_000_000    # ... y para salir

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "binance_daily")

# Pool de candidatos: pares USDT con historia larga en Binance. Incluye deliberadamente
# "majors caidos" (EOS, IOTA, NEO, ZEC, DASH, ETC, XLM) para reducir —no eliminar— el
# sesgo de supervivencia: si solo se incluyen los que hoy estan arriba, el backtest
# hereda una decision tomada con informacion del futuro.
POOL = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT", "LTCUSDT",
    "TRXUSDT", "ETCUSDT", "EOSUSDT", "XLMUSDT", "NEOUSDT", "IOTAUSDT", "ZECUSDT",
    "DASHUSDT", "SOLUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT", "ATOMUSDT",
]
START_MS = 1_502_928_000_000  # 2017-08-17


# ── Datos ─────────────────────────────────────────────────────────────────────
def _fetch_daily(symbol: str) -> Optional[pd.DataFrame]:
    rows: List[list] = []
    start = START_MS
    while True:
        url = (f"https://api.binance.com/api/v3/klines?symbol={symbol}"
               f"&interval=1d&limit=1000&startTime={start}")
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                chunk = json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 400:
                return None  # par inexistente / delisted
            raise
        if not chunk:
            break
        rows.extend(chunk)
        if len(chunk) < 1000:
            break
        start = chunk[-1][0] + 86_400_000
        time.sleep(0.15)
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=[
        "open_time", "open", "high", "low", "close", "volume", "close_time",
        "quote_volume", "trades", "tb_base", "tb_quote", "ignore"])
    df = df[["open_time", "open", "high", "low", "close", "volume", "quote_volume"]]
    for c in ("open", "high", "low", "close", "volume", "quote_volume"):
        df[c] = df[c].astype(float)
    df["date"] = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.tz_localize(None)
    return df.drop(columns=["open_time"]).drop_duplicates("date").set_index("date").sort_index()


def load_universe(download: bool = True) -> Dict[str, pd.DataFrame]:
    os.makedirs(DATA_DIR, exist_ok=True)
    out: Dict[str, pd.DataFrame] = {}
    for sym in POOL:
        path = os.path.join(DATA_DIR, f"{sym}.parquet")
        if os.path.exists(path):
            out[sym] = pd.read_parquet(path)
            continue
        if not download:
            continue
        df = _fetch_daily(sym)
        if df is None or len(df) < MIN_LISTING_DAYS:
            print(f"  {sym}: sin datos suficientes, descartado")
            continue
        df.to_parquet(path)
        out[sym] = df
        print(f"  {sym}: {len(df)} dias  {df.index[0].date()} -> {df.index[-1].date()}")
    return out


# ── Estrategia §11.2 ──────────────────────────────────────────────────────────
def sub_strategy_positions(close: pd.Series, n: int) -> Tuple[pd.Series, pd.Series]:
    """Posicion (0/1) y trailing stop de UN lookback. Solo usa datos <= t."""
    roll_max = close.rolling(n).max()
    roll_min = close.rolling(n).min()
    mid = 0.5 * (roll_max + roll_min)

    pos = np.zeros(len(close))
    stop = np.full(len(close), np.nan)
    in_pos = False
    cur_stop = np.nan
    c = close.to_numpy()
    m = mid.to_numpy()
    rmax = roll_max.to_numpy()

    for i in range(len(c)):
        if np.isnan(m[i]):
            continue
        if not in_pos:
            # Entrada: el cierre es el maximo de los ultimos n cierres
            if c[i] >= rmax[i]:
                in_pos = True
                cur_stop = m[i]          # stop inicial = DonchianMid en la entrada
        else:
            cur_stop = max(cur_stop, m[i])   # trailing: nunca baja
            if c[i] <= cur_stop:
                in_pos = False
                cur_stop = np.nan
        pos[i] = 1.0 if in_pos else 0.0
        stop[i] = cur_stop
    return pd.Series(pos, index=close.index), pd.Series(stop, index=close.index)


def asset_weight(df: pd.DataFrame) -> pd.Series:
    """Peso objetivo del activo: media sobre lookbacks de (escalar de vol) x posicion."""
    close = df["close"]
    ret = close.pct_change(fill_method=None)
    sigma = ret.rolling(VOL_WINDOW).std() * np.sqrt(ANNUALIZATION)
    vol_scalar = (TARGET_VOL / sigma).clip(upper=LEVERAGE_CAP)
    vol_scalar = vol_scalar.replace([np.inf, -np.inf], np.nan)

    weights = []
    for n in LOOKBACKS:
        pos, _ = sub_strategy_positions(close, n)
        weights.append(vol_scalar * pos)
    w = pd.concat(weights, axis=1).mean(axis=1)
    return w.fillna(0.0)


def monthly_universe(data: Dict[str, pd.DataFrame], dates: pd.DatetimeIndex) -> pd.DataFrame:
    """Universo point-in-time: top-N por volumen mediano 30d, recalculado a fin de mes.

    Usa SOLO datos <= la fecha de decision. Filtros de liquidez y antiguedad minima.
    """
    dollar_vol = pd.DataFrame({s: d["quote_volume"] for s, d in data.items()}).reindex(dates)
    med30 = dollar_vol.rolling(30).median()
    first_seen = {s: d.index[0] for s, d in data.items()}

    selected = pd.DataFrame(0.0, index=dates, columns=list(data.keys()))
    current: List[str] = []
    month = None
    for dt in dates:
        if month != (dt.year, dt.month):
            month = (dt.year, dt.month)
            row = med30.loc[dt].dropna()
            eligible = [s for s in row.index
                        if row[s] >= LIQ_ENTER_USD
                        and (dt - first_seen[s]).days >= MIN_LISTING_DAYS]
            ranked = sorted(eligible, key=lambda s: row[s], reverse=True)
            keep = [s for s in current
                    if s in row.index and row[s] >= LIQ_EXIT_USD and s in eligible]
            for s in ranked:
                if len(keep) >= N_ASSETS:
                    break
                if s not in keep:
                    keep.append(s)
            current = keep[:N_ASSETS]
        for s in current:
            selected.loc[dt, s] = 1.0 / max(len(current), 1)
    return selected


def backtest(data: Dict[str, pd.DataFrame], cost_bps: float = COMMISSION_BPS + SLIPPAGE_BPS,
             lookbacks: Optional[List[int]] = None, target_vol: float = TARGET_VOL,
             vol_window: int = VOL_WINDOW) -> Dict:
    """Ejecuta la estrategia. Senal en el cierre de t -> ejecucion en la APERTURA de t+1.

    Los retornos son apertura-a-apertura: el peso decidido en el cierre de t esta en
    vigor desde la apertura de t+1 hasta la de t+2, de modo que pnl[t+2] = w[t]*r_oo[t+2]
    (shift de 2). Por construccion no puede haber look-ahead.
    """
    global LOOKBACKS, TARGET_VOL, VOL_WINDOW
    saved = (LOOKBACKS, TARGET_VOL, VOL_WINDOW)
    if lookbacks is not None:
        LOOKBACKS = lookbacks
    TARGET_VOL, VOL_WINDOW = target_vol, vol_window
    try:
        dates = pd.DatetimeIndex(sorted(set().union(*[d.index for d in data.values()])))
        alloc = monthly_universe(data, dates)

        w_assets, r_oo = {}, {}
        for s, d in data.items():
            d = d.reindex(dates)
            w_assets[s] = asset_weight(d).reindex(dates).fillna(0.0)
            r_oo[s] = (d["open"] / d["open"].shift(1) - 1.0).reindex(dates).fillna(0.0)
        W = pd.DataFrame(w_assets) * alloc          # peso final por activo
        R = pd.DataFrame(r_oo)

        # Umbral de rebalanceo: solo sobre cambios de tamano (no sobre entradas/salidas)
        W_exec = W.copy()
        prev = pd.Series(0.0, index=W.columns)
        for dt in W.index:
            tgt = W.loc[dt]
            newp = prev.copy()
            for s in W.columns:
                if (tgt[s] == 0) != (prev[s] == 0):
                    newp[s] = tgt[s]                # senal: ejecutar siempre
                elif prev[s] > 0 and abs(tgt[s] - prev[s]) / prev[s] > REBALANCE_THRESHOLD:
                    newp[s] = tgt[s]                # cambio por volatilidad: con umbral
            W_exec.loc[dt] = newp
            prev = newp

        def _pnl(shift: int) -> pd.Series:
            g = (W_exec.shift(shift) * R).sum(axis=1)
            t = W_exec.diff().abs().sum(axis=1).shift(shift).fillna(0.0)
            return (g - t * (cost_bps / 10_000.0)).fillna(0.0)

        gross = (W_exec.shift(2) * R).sum(axis=1)
        turnover = W_exec.diff().abs().sum(axis=1).shift(2).fillna(0.0)
        costs = turnover * (cost_bps / 10_000.0)
        net = (gross - costs).fillna(0.0)

        holding = (W_exec > 0).astype(int)
        closed_trades = int((holding.diff() == -1).sum().sum())
        # Retornos de comprar-y-aguantar sobre el MISMO periodo y los mismos precios
        bh_major = R[[s for s in ("BTCUSDT", "ETHUSDT", "BNBUSDT") if s in R.columns]].mean(axis=1)
        return {"net": net, "gross": gross, "costs": costs, "weights": W_exec,
                "closed_trades": closed_trades, "turnover": turnover,
                "pnl_at_shift": _pnl, "buy_hold": bh_major,
                "buy_hold_btc": R["BTCUSDT"] if "BTCUSDT" in R.columns else None}
    finally:
        LOOKBACKS, TARGET_VOL, VOL_WINDOW = saved


# ── Metricas ──────────────────────────────────────────────────────────────────
def metrics(net: pd.Series) -> Dict[str, float]:
    r = net.dropna()
    r = r[r.index >= r.index[0]]
    if len(r) < 30 or r.std() == 0:
        return {"sharpe": 0.0, "cagr": 0.0, "vol": 0.0, "max_dd": 0.0, "skew": 0.0, "days": len(r)}
    sharpe = float(r.mean() / r.std() * np.sqrt(ANNUALIZATION))
    eq = (1 + r).cumprod()
    years = len(r) / ANNUALIZATION
    cagr = float(eq.iloc[-1] ** (1 / years) - 1) if years > 0 else 0.0
    dd = float((1 - eq / eq.cummax()).max())
    return {"sharpe": sharpe, "cagr": cagr, "vol": float(r.std() * np.sqrt(ANNUALIZATION)),
            "max_dd": dd, "skew": float(r.skew()), "days": len(r)}


def deflated_sharpe(sharpe: float, n_obs: int, n_trials: int, skew: float, kurt: float = 3.0) -> float:
    """DSR de Bailey & Lopez de Prado. n_trials = configuraciones REALMENTE probadas."""
    if n_obs < 30 or n_trials < 1:
        return 0.0
    from math import log, sqrt, erf
    emc = 0.5772156649
    e_max = (1 - emc) * _norm_ppf(1 - 1.0 / n_trials) + emc * _norm_ppf(1 - 1.0 / (n_trials * np.e))
    sr_star = e_max / np.sqrt(n_obs)  # umbral en unidades de Sharpe por observacion
    sr = sharpe / np.sqrt(ANNUALIZATION)
    denom = np.sqrt(1 - skew * sr + (kurt - 1) / 4 * sr ** 2)
    if denom <= 0:
        return 0.0
    z = (sr - sr_star) * np.sqrt(n_obs - 1) / denom
    return float(0.5 * (1 + erf(z / sqrt(2))))


def _norm_ppf(p: float) -> float:
    """Inversa de la normal estandar (Acklam), sin scipy."""
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    pl, ph = 0.02425, 1 - 0.02425
    if p < pl:
        q = np.sqrt(-2 * np.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > ph:
        q = np.sqrt(-2 * np.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-download", action="store_true")
    args = ap.parse_args()

    print("== Datos ==")
    data = load_universe(download=not args.no_download)
    if len(data) < N_ASSETS:
        print(f"!! solo {len(data)} simbolos disponibles")
        return 1
    print(f"  universo candidato: {len(data)} simbolos")

    n_trials = 0
    print("\n== Backtest (configuracion de la especificacion, sin optimizar) ==")
    base = backtest(data)
    n_trials += 1
    m = metrics(base["net"])
    print(f"  Sharpe neto {m['sharpe']:.2f} | CAGR {m['cagr']*100:.1f}% | vol {m['vol']*100:.1f}% "
          f"| maxDD {m['max_dd']*100:.1f}% | skew {m['skew']:.2f} | {m['days']} dias "
          f"| {base['closed_trades']} trades cerrados")

    net = base["net"]
    recent = net[net.index >= "2022-01-01"]
    m_recent = metrics(recent)
    print(f"  submuestra 2022+: Sharpe {m_recent['sharpe']:.2f} ({m_recent['days']} dias)")

    print("\n== Robustez a costes ==")
    cost_res = {}
    for c in (COMMISSION_BPS + SLIPPAGE_BPS, 15.0, 25.0, 50.0):
        r = backtest(data, cost_bps=c)
        n_trials += 1
        cost_res[c] = metrics(r["net"])["sharpe"]
        print(f"  {c:>4.0f} bps/lado -> Sharpe {cost_res[c]:.2f}")

    print("\n== Sensibilidad de parametros (+-50%) ==")
    sens = {}
    for label, kwargs in [
        ("target_vol 0.10", {"target_vol": 0.10}),
        ("target_vol 0.30", {"target_vol": 0.30}),
        ("vol_window 45", {"vol_window": 45}),
        ("vol_window 135", {"vol_window": 135}),
        ("lookbacks x0.5", {"lookbacks": [3, 5, 10, 15, 30, 45]}),
        ("lookbacks x1.5", {"lookbacks": [8, 15, 30, 45, 90, 135]}),
    ]:
        r = backtest(data, **kwargs)
        n_trials += 1
        sens[label] = metrics(r["net"])["sharpe"]
        print(f"  {label:<18} -> Sharpe {sens[label]:.2f}")

    print("\n== Criterio 12: auditoria de LOOK-AHEAD (mismo motor, solo cambia el desfase) ==")
    la = {sh: metrics(base["pnl_at_shift"](sh)) for sh in (0, 1, 2, 3, 4)}
    notes = {0: "TRAMPA: usa el futuro", 1: "ejecucion al cierre de t (prohibida)",
             2: "LA ESPECIFICACION", 3: "un dia mas tarde", 4: "dos dias mas tarde"}
    for sh, mm in la.items():
        print(f"  shift={sh}  Sharpe {mm['sharpe']:5.2f}  CAGR {mm['cagr']*100:5.1f}%  "
              f"maxDD {mm['max_dd']*100:4.1f}%   {notes[sh]}")
    # Un edge REAL apenas se degrada al retrasar la ejecucion un dia mas; un artefacto
    # de timing se desploma. Esta es la prueba, no la ausencia de bugs evidentes.
    decay = (la[2]["sharpe"] - la[3]["sharpe"]) / abs(la[2]["sharpe"]) if la[2]["sharpe"] else 1.0
    no_lookahead = decay < 0.25
    print(f"  degradacion al retrasar 1 dia mas: {decay*100:.1f}% "
          f"({'estable -> edge real' if no_lookahead else 'se desploma -> artefacto de timing'})")

    print("\n== Contexto honesto: comprar y aguantar, mismo periodo y mismos precios ==")
    m_bh = metrics(base["buy_hold"])
    m_btc = metrics(base["buy_hold_btc"]) if base["buy_hold_btc"] is not None else None
    print(f"  B&H BTC/ETH/BNB   Sharpe {m_bh['sharpe']:.2f}  CAGR {m_bh['cagr']*100:.1f}%  "
          f"maxDD {m_bh['max_dd']*100:.1f}%")
    if m_btc:
        print(f"  B&H solo BTC      Sharpe {m_btc['sharpe']:.2f}  CAGR {m_btc['cagr']*100:.1f}%  "
              f"maxDD {m_btc['max_dd']*100:.1f}%")
    print(f"  ESTRATEGIA        Sharpe {m['sharpe']:.2f}  CAGR {m['cagr']*100:.1f}%  "
          f"maxDD {m['max_dd']*100:.1f}%")
    print("  Lectura: el trend NO gana en retorno absoluto; gana en Sharpe y sobre todo en")
    print("  drawdown. Su ventaja es la FORMA de la distribucion, no el retorno (research §3.1).")

    print("\n== Cobertura de regimenes (anos naturales) ==")
    yearly = net.groupby(net.index.year).apply(lambda s: float((1 + s).prod() - 1))
    for y, v in yearly.items():
        print(f"  {y}: {v*100:+.1f}%")

    dsr = deflated_sharpe(m["sharpe"], m["days"], n_trials, m["skew"])
    worst_sens = max(abs(s - m["sharpe"]) / abs(m["sharpe"]) for s in sens.values()) if m["sharpe"] else 1.0

    print("\n" + "=" * 72)
    print("CHECKLIST GO / NO-GO (research §11.3)")
    print("=" * 72)
    checks = [
        ("1  Sharpe neto muestra completa >= 0.80", m["sharpe"], m["sharpe"] >= 0.80),
        ("2  Sharpe 2022+ >= 0.50", m_recent["sharpe"], m_recent["sharpe"] >= 0.50),
        ("3  DSR >= 0.95", dsr, dsr >= 0.95),
        ("5  Configuraciones probadas <= 300", n_trials, n_trials <= 300),
        ("6  Trades cerrados >= 150", base["closed_trades"], base["closed_trades"] >= 150),
        ("8  Sharpe > 0.5 con 50 bps", cost_res[50.0], cost_res[50.0] > 0.5),
        ("9  Sensibilidad: variacion < 25%", worst_sens, worst_sens < 0.25),
        ("10 Max drawdown <= 25%", m["max_dd"], m["max_dd"] <= 0.25),
        ("11 Asimetria > 0", m["skew"], m["skew"] > 0),
        ("12 Sin look-ahead (estable al retrasar 1 dia)", decay, no_lookahead),
        ("descarte  Sharpe < 2.0 (si no: sospecha de bug)", m["sharpe"], m["sharpe"] < 2.0),
    ]
    passed = 0
    for name, value, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:<48} = {value:.2f}")
        passed += int(ok)
    print(f"\n  {passed}/{len(checks)} criterios computables superados")
    print("  NO computados aqui: 4 (PBO/CSCV) y 7 (regimenes formales).")
    print("\n  LIMITACIONES QUE NO HAY QUE OLVIDAR AL LEER ESTE RESULTADO:")
    print("   - Sesgo de supervivencia: el pool son 20 pares que EXISTEN HOY en Binance.")
    print("     Los que murieron no estan, y eso infla el resultado. Reducido incluyendo")
    print("     majors caidos (EOS/IOTA/NEO/ZEC/DASH), no eliminado.")
    print("   - Precios de Binance SPOT, pero Binance esta CERRADO para el dueno (MiCA):")
    print("     el venue real tendra otros precios y otros costes.")
    print("   - La sensibilidad a target_vol es trivial por construccion (el vol targeting")
    print("     escala retorno y volatilidad a la vez): ese criterio informa poco.")
    print(f"\n  VEREDICTO: {'GO a paper trading' if passed == len(checks) else 'NO-GO'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
