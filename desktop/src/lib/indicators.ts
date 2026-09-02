// Pure indicator maths for the chart sub-pane (no React, no store access).
import type { Candle } from "@/stores/marketStore";

export interface IndicatorPoint {
  time: number;
  value: number;
}

export interface MacdPoint {
  time: number;
  macd: number;
  signal: number;
  hist: number;
}

/** Wilder RSI over closes; the first `period` bars produce no value. */
export function rsi(candles: Candle[], period = 14): IndicatorPoint[] {
  const out: IndicatorPoint[] = [];
  if (candles.length <= period) return out;
  let gain = 0;
  let loss = 0;
  for (let i = 1; i <= period; i++) {
    const d = candles[i].close - candles[i - 1].close;
    if (d >= 0) gain += d; else loss -= d;
  }
  let avgGain = gain / period;
  let avgLoss = loss / period;
  const push = (i: number) => {
    const rs = avgLoss === 0 ? Infinity : avgGain / avgLoss;
    const v = avgLoss === 0 ? 100 : 100 - 100 / (1 + rs);
    out.push({ time: candles[i].time, value: Math.max(0, Math.min(100, v)) });
  };
  push(period);
  for (let i = period + 1; i < candles.length; i++) {
    const d = candles[i].close - candles[i - 1].close;
    avgGain = (avgGain * (period - 1) + Math.max(d, 0)) / period;
    avgLoss = (avgLoss * (period - 1) + Math.max(-d, 0)) / period;
    push(i);
  }
  return out;
}

function ema(values: number[], period: number): number[] {
  const k = 2 / (period + 1);
  const out: number[] = new Array(values.length).fill(NaN);
  if (values.length < period) return out;
  let sum = 0;
  for (let i = 0; i < period; i++) sum += values[i];
  let prev = sum / period;
  out[period - 1] = prev;
  for (let i = period; i < values.length; i++) {
    prev = values[i] * k + prev * (1 - k);
    out[i] = prev;
  }
  return out;
}

/** MACD(fast, slow, signal) on closes; rows before the slow+signal warm-up are dropped. */
export function macd(candles: Candle[], fast = 12, slow = 26, signalPeriod = 9): MacdPoint[] {
  const closes = candles.map((c) => c.close);
  const ef = ema(closes, fast);
  const es = ema(closes, slow);
  const line: number[] = closes.map((_, i) => (Number.isFinite(ef[i]) && Number.isFinite(es[i]) ? ef[i] - es[i] : NaN));
  // signal EMA only over the defined part of the MACD line
  const firstIdx = line.findIndex((v) => Number.isFinite(v));
  if (firstIdx < 0) return [];
  const defined = line.slice(firstIdx);
  const sig = ema(defined, signalPeriod);
  const out: MacdPoint[] = [];
  for (let j = 0; j < defined.length; j++) {
    if (!Number.isFinite(sig[j])) continue;
    const i = firstIdx + j;
    out.push({ time: candles[i].time, macd: defined[j], signal: sig[j], hist: defined[j] - sig[j] });
  }
  return out;
}

/** Resample 1m candles to a higher timeframe client-side (shared by chart + indicator pane). */
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
