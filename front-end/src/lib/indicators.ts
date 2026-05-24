export function calcMA(data: number[], period: number): (number | null)[] {
  const result: (number | null)[] = [];
  for (let i = 0; i < data.length; i++) {
    if (i < period - 1) { result.push(null); continue; }
    let sum = 0;
    for (let j = i - period + 1; j <= i; j++) sum += data[j];
    result.push(sum / period);
  }
  return result;
}

export function calcEMA(data: number[], period: number): (number | null)[] {
  const k = 2 / (period + 1);
  const out: (number | null)[] = [];
  let ema: number | null = null;
  for (let i = 0; i < data.length; i++) {
    if (i < period - 1) {
      out.push(null);
    } else if (ema === null) {
      let s = 0;
      for (let j = 0; j < period; j++) s += data[j];
      ema = s / period;
      out.push(ema);
    } else {
      ema = data[i] * k + ema * (1 - k);
      out.push(ema);
    }
  }
  return out;
}

export function calcBOLL(data: number[], period: number, multiplier: number) {
  const ma = calcMA(data, period);
  const upper: (number | null)[] = [];
  const mid: (number | null)[] = [];
  const lower: (number | null)[] = [];
  for (let i = 0; i < data.length; i++) {
    if (ma[i] === null) { upper.push(null); mid.push(null); lower.push(null); continue; }
    const m = ma[i]!;
    let sumSq = 0;
    let count = 0;
    for (let j = Math.max(0, i - period + 1); j <= i; j++) { sumSq += (data[j] - m) ** 2; count++; }
    const std = Math.sqrt(sumSq / count);
    upper.push(m + multiplier * std);
    mid.push(m);
    lower.push(m - multiplier * std);
  }
  return { upper, mid, lower };
}

export function calcMACD(data: number[], fast = 12, slow = 26, signal = 9) {
  const emaFast = calcEMA(data, fast);
  const emaSlow = calcEMA(data, slow);
  const dif: (number | null)[] = [];
  for (let i = 0; i < data.length; i++) {
    if (emaFast[i] === null || emaSlow[i] === null) { dif.push(null); continue; }
    dif.push(emaFast[i]! - emaSlow[i]!);
  }
  const signalLine = calcEMA(dif.filter((v): v is number => v !== null), signal);
  const dea: (number | null)[] = [];
  let sigIdx = 0;
  for (let i = 0; i < data.length; i++) {
    if (dif[i] === null) { dea.push(null); continue; }
    dea.push(signalLine[sigIdx] ?? null);
    sigIdx++;
  }
  const histogram: (number | null)[] = [];
  for (let i = 0; i < data.length; i++) {
    if (dif[i] === null || dea[i] === null) { histogram.push(null); continue; }
    histogram.push(dif[i]! - dea[i]!);
  }
  return { dif, dea, signal: dea, histogram };
}

export function calcRSI(data: number[], period = 14): (number | null)[] {
  const result: (number | null)[] = [];
  let gain = 0, loss = 0;
  for (let i = 0; i < data.length; i++) {
    if (i === 0) { result.push(null); continue; }
    const diff = data[i] - data[i - 1];
    if (i <= period) {
      gain += Math.max(diff, 0);
      loss += Math.max(-diff, 0);
      if (i === period) {
        const avgGain = gain / period;
        const avgLoss = loss / period;
        result.push(avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss));
      } else {
        result.push(null);
      }
    } else {
      const prevGain = gain;
      const prevLoss = loss;
      gain = (prevGain * (period - 1) + Math.max(diff, 0)) / period;
      loss = (prevLoss * (period - 1) + Math.max(-diff, 0)) / period;
      result.push(loss === 0 ? 100 : 100 - 100 / (1 + gain / loss));
    }
  }
  return result;
}

export function calcKDJ(highs: number[], lows: number[], closes: number[], period = 9) {
  const k: (number | null)[] = [];
  const d: (number | null)[] = [];
  const j: (number | null)[] = [];
  let prevK = 50, prevD = 50;
  for (let i = 0; i < closes.length; i++) {
    if (i < period - 1) { k.push(null); d.push(null); j.push(null); continue; }
    let hh = -Infinity, ll = Infinity;
    for (let p = i - period + 1; p <= i; p++) { hh = Math.max(hh, highs[p]); ll = Math.min(ll, lows[p]); }
    const rsv = hh === ll ? 50 : ((closes[i] - ll) / (hh - ll)) * 100;
    const curK = (2 / 3) * prevK + (1 / 3) * rsv;
    const curD = (2 / 3) * prevD + (1 / 3) * curK;
    k.push(curK);
    d.push(curD);
    j.push(3 * curK - 2 * curD);
    prevK = curK;
    prevD = curD;
  }
  return { k, d, j };
}
