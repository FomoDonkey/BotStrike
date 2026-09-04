// Indicator maths for the chart, written to the ENGINE's definitions (core/indicators.py) so the
// value on screen is the value the bot computes. No React, no store access.
//
// pandas conventions reproduced on purpose:
//  · `ewm(span, adjust=False)` is seeded with the first observation, α = 2/(span+1); Wilder's
//    smoothing is span = 2n − 1 (α = 1/n). A NaN observation keeps the previous average and lets
//    its weight decay, exactly as pandas does with ignore_na=False.
//  · `rolling(n, min_periods).std()` is the SAMPLE standard deviation (ddof = 1), and a window is
//    computed directly rather than from running sums — on a flat stretch of an 80,000 $ price a
//    running sum of squares rounds a zero variance into a small positive one.
// Every function returns an array aligned to its input, NaN where there is no value yet.
import type { Candle } from "@/stores/marketStore";

const NAN = Number.NaN;

/** pandas `Series.ewm(span=span, adjust=False).mean()` (ignore_na=False). */
export function ewm(values: number[], span: number): number[] {
  const n = values.length;
  const out = new Array<number>(n).fill(NAN);
  const alpha = 2 / (span + 1);
  const decay = 1 - alpha;
  let avg = NAN;
  let oldWt = 1;
  for (let i = 0; i < n; i++) {
    const cur = values[i];
    const obs = Number.isFinite(cur);
    if (Number.isFinite(avg)) {
      oldWt *= decay;
      if (obs) {
        if (avg !== cur) avg = (oldWt * avg + alpha * cur) / (oldWt + alpha);
        oldWt = 1;
      }
    } else if (obs) {
      avg = cur;
    }
    out[i] = avg;
  }
  return out;
}

/** pandas `rolling(n, min_periods).mean()`; NaN inside the window is skipped, as pandas does. */
export function rollingMean(values: number[], n: number, minPeriods = n): number[] {
  const out = new Array<number>(values.length).fill(NAN);
  for (let i = 0; i < values.length; i++) {
    let sum = 0;
    let count = 0;
    for (let j = Math.max(0, i - n + 1); j <= i; j++) {
      const v = values[j];
      if (Number.isFinite(v)) { sum += v; count++; }
    }
    if (count >= minPeriods && count > 0) out[i] = sum / count;
  }
  return out;
}

/** pandas `rolling(n, min_periods).std()` — sample standard deviation (ddof = 1). */
export function rollingStd(values: number[], n: number, minPeriods = n): number[] {
  const out = new Array<number>(values.length).fill(NAN);
  for (let i = 0; i < values.length; i++) {
    let sum = 0;
    let count = 0;
    const from = Math.max(0, i - n + 1);
    for (let j = from; j <= i; j++) {
      const v = values[j];
      if (Number.isFinite(v)) { sum += v; count++; }
    }
    if (count < Math.max(minPeriods, 2)) continue;
    const mean = sum / count;
    let ss = 0;
    for (let j = from; j <= i; j++) {
      const v = values[j];
      if (Number.isFinite(v)) ss += (v - mean) * (v - mean);
    }
    out[i] = Math.sqrt(ss / (count - 1));
  }
  return out;
}

function rollingExtreme(values: number[], n: number, pick: (a: number, b: number) => number): number[] {
  const out = new Array<number>(values.length).fill(NAN);
  for (let i = n - 1; i < values.length; i++) {
    let acc = NAN;
    let count = 0;
    for (let j = i - n + 1; j <= i; j++) {
      const v = values[j];
      if (!Number.isFinite(v)) continue;
      acc = count ? pick(acc, v) : v;
      count++;
    }
    if (count === n) out[i] = acc;
  }
  return out;
}

export const rollingMax = (values: number[], n: number) => rollingExtreme(values, n, Math.max);
export const rollingMin = (values: number[], n: number) => rollingExtreme(values, n, Math.min);

/** `Indicators.sma` — rolling mean with min_periods = n. */
export const sma = (values: number[], n: number) => rollingMean(values, n, n);

/** `Indicators.ema` — `ewm(span=n, adjust=False)`, defined from the first bar. */
export const ema = (values: number[], n: number) => ewm(values, n);

/** `Indicators.bollinger_bands` — SMA n ± k · sample σ, NaN through the warm-up. */
export function bollinger(closes: number[], n = 20, k = 2): { upper: number[]; mid: number[]; lower: number[] } {
  const mid = rollingMean(closes, n, n);
  const sd = rollingStd(closes, n, n);
  const upper = mid.map((m, i) => m + k * sd[i]);
  const lower = mid.map((m, i) => m - k * sd[i]);
  return { upper, mid, lower };
}

/** `high_20` / `low_20` — the Donchian channel the engine computes on this frame. */
export function donchian(candles: Candle[], n = 20): { upper: number[]; lower: number[]; mid: number[] } {
  const upper = rollingMax(candles.map((c) => c.high), n);
  const lower = rollingMin(candles.map((c) => c.low), n);
  const mid = upper.map((u, i) => (u + lower[i]) / 2);
  return { upper, lower, mid };
}

/** `Indicators.rsi` — Wilder's smoothing via ewm(span = 2n − 1); a pure run of gains reads 100,
 *  a bar without a defined ratio reads 50, exactly as the engine fills it. */
export function rsi(closes: number[], n = 14): number[] {
  const gain = new Array<number>(closes.length).fill(0);
  const loss = new Array<number>(closes.length).fill(0);
  for (let i = 1; i < closes.length; i++) {
    const d = closes[i] - closes[i - 1];
    if (d > 0) gain[i] = d;
    else if (d < 0) loss[i] = -d;
  }
  const ag = ewm(gain, 2 * n - 1);
  const al = ewm(loss, 2 * n - 1);
  return ag.map((g, i) => {
    const l = al[i];
    if (!Number.isFinite(g) || !Number.isFinite(l)) return 50;
    if (l === 0) return g > 0 ? 100 : 50;
    return 100 - 100 / (1 + g / l);
  });
}

/** `Indicators.atr` — true range with Wilder's smoothing (ewm span = 2n − 1). */
export function atr(candles: Candle[], n = 14): number[] {
  const tr = candles.map((c, i) => {
    if (i === 0) return c.high - c.low;
    const pc = candles[i - 1].close;
    return Math.max(c.high - c.low, Math.abs(c.high - pc), Math.abs(c.low - pc));
  });
  return ewm(tr, 2 * n - 1);
}

/** `Indicators.adx` + `directional_indicators` — the engine's `_di_pair`, ADX with pandas' NaN
 *  handling on the DX line (a bar where +DI + −DI is zero has no DX and does not move the ADX). */
export function adx(candles: Candle[], n = 14): { adx: number[]; plusDi: number[]; minusDi: number[] } {
  const len = candles.length;
  const plusDm = new Array<number>(len).fill(0);
  const minusDm = new Array<number>(len).fill(0);
  for (let i = 1; i < len; i++) {
    const up = candles[i].high - candles[i - 1].high;
    const down = candles[i - 1].low - candles[i].low;
    if (up > down && up > 0) plusDm[i] = up;
    if (down > up && down > 0) minusDm[i] = down;
  }
  const atrv = atr(candles, n);
  const sp = ewm(plusDm, 2 * n - 1);
  const sm = ewm(minusDm, 2 * n - 1);
  const plusRaw = sp.map((v, i) => (atrv[i] > 0 ? (100 * v) / atrv[i] : NAN));
  const minusRaw = sm.map((v, i) => (atrv[i] > 0 ? (100 * v) / atrv[i] : NAN));
  const dx = plusRaw.map((p, i) => {
    const m = minusRaw[i];
    if (!Number.isFinite(p) || !Number.isFinite(m) || p + m === 0) return NAN;
    return (100 * Math.abs(p - m)) / (p + m);
  });
  return {
    adx: ewm(dx, 2 * n - 1),
    plusDi: plusRaw.map((v) => (Number.isFinite(v) ? v : 0)),
    minusDi: minusRaw.map((v) => (Number.isFinite(v) ? v : 0)),
  };
}

/** MACD on the engine's `ema_12` − `ema_26`, signal = ewm(span 9) of that line. */
export function macd(closes: number[], fast = 12, slow = 26, signalPeriod = 9): { macd: number[]; signal: number[]; hist: number[] } {
  const ef = ewm(closes, fast);
  const es = ewm(closes, slow);
  const line = ef.map((f, i) => f - es[i]);
  const signal = ewm(line, signalPeriod);
  const hist = line.map((v, i) => v - signal[i]);
  return { macd: line, signal, hist };
}

/** `Indicators.zscore` — (close − mean) / sample σ over n bars (min_periods 2); a flat window is 0. */
export function zscore(closes: number[], n = 100): number[] {
  const mean = rollingMean(closes, n, 2);
  const sd = rollingStd(closes, n, 2);
  return closes.map((c, i) => {
    const s = sd[i];
    if (!Number.isFinite(s) || s <= 1e-12 || !Number.isFinite(mean[i])) return 0;
    return (c - mean[i]) / s;
  });
}

/** `Indicators.momentum` — `pct_change(n)`, as a fraction. */
export function momentum(closes: number[], n: number): number[] {
  return closes.map((c, i) => (i >= n && closes[i - n] !== 0 ? c / closes[i - n] - 1 : NAN));
}

/** `Indicators.volume_ratio` — volume over its n-bar mean (min_periods n/2). */
export function volumeRatio(volumes: number[], n = 20): number[] {
  const avg = rollingMean(volumes, n, Math.max(Math.floor(n / 2), 2));
  return volumes.map((v, i) => (avg[i] > 0 ? v / avg[i] : NAN));
}

/** `Indicators.volatility_percentile` — where today's |return| mean sits in its last `lookback`
 *  values (0–1), including the engine's rule that fewer than ten valid values is no answer. */
export function volatilityPercentile(closes: number[], atrPeriod = 14, lookback = 100): number[] {
  const returns = closes.map((c, i) => (i > 0 && closes[i - 1] !== 0 ? Math.abs(c / closes[i - 1] - 1) : NAN));
  const vol = rollingMean(returns, atrPeriod, 2);
  const out = new Array<number>(closes.length).fill(NAN);
  for (let i = 0; i < closes.length; i++) {
    const start = Math.max(0, i - lookback + 1);
    let valid = 0;
    for (let j = start; j <= i; j++) if (Number.isFinite(vol[j])) valid++;
    if (valid < 10) continue;
    const size = i - start + 1;
    const denom = size - 1;
    if (denom < 1) { out[i] = 0.5; continue; }
    const last = vol[i];
    if (!Number.isFinite(last)) { out[i] = 0; continue; }
    let below = 0;
    for (let j = start; j < i; j++) if (vol[j] < last) below++;
    out[i] = below / denom;
  }
  return out;
}

/** Resample 1m candles to a higher timeframe client-side (shared by chart + indicator panes). */
export function resampleCandles(candles: Candle[], tfSeconds: number): Candle[] {
  if (tfSeconds <= 60 || !candles.length) return candles;
  const buckets = new Map<number, Candle>();
  for (const c of candles) {
    const key = Math.floor(c.time / tfSeconds) * tfSeconds;
    const existing = buckets.get(key);
    if (!existing) {
      buckets.set(key, { time: key, open: c.open, high: c.high, low: c.low, close: c.close, volume: c.volume });
    } else {
      existing.high = Math.max(existing.high, c.high);
      existing.low = Math.min(existing.low, c.low);
      existing.close = c.close;
      existing.volume += c.volume;
    }
  }
  return Array.from(buckets.values()).sort((a, b) => a.time - b.time);
}
