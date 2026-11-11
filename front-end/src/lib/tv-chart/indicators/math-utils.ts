/* ============================================================================
 * TradingView Pine-Equivalent Vector Math Primitives
 * Pure, deterministic O(N) time-series functions for 270+ Studies & Strategies
 * ============================================================================ */

import type { TvCandle } from "../types"

export function calcSMA(values: (number | null)[], length: number): (number | null)[] {
  const n = Math.max(1, Math.round(length))
  const out: (number | null)[] = new Array(values.length).fill(null)
  let sum = 0
  let count = 0
  const windowVals: number[] = []
  for (let i = 0; i < values.length; i++) {
    const v = values[i]
    if (v == null || !Number.isFinite(v)) {
      windowVals.length = 0
      sum = 0
      count = 0
      continue
    }
    windowVals.push(v)
    sum += v
    count++
    if (count > n) {
      sum -= windowVals.shift()!
      count--
    }
    if (count === n) {
      out[i] = sum / n
    }
  }
  return out
}

export function calcEMA(values: (number | null)[], length: number): (number | null)[] {
  const n = Math.max(1, Math.round(length))
  const out: (number | null)[] = new Array(values.length).fill(null)
  const k = 2 / (n + 1)
  let prevEma: number | null = null
  let validCount = 0
  let initSum = 0

  for (let i = 0; i < values.length; i++) {
    const v = values[i]
    if (v == null || !Number.isFinite(v)) continue
    validCount++
    if (prevEma === null) {
      initSum += v
      if (validCount === n) {
        prevEma = initSum / n
        out[i] = prevEma
      }
    } else {
      prevEma = v * k + prevEma * (1 - k)
      out[i] = prevEma
    }
  }
  return out
}

export function calcRMA(values: (number | null)[], length: number): (number | null)[] {
  const n = Math.max(1, Math.round(length))
  const out: (number | null)[] = new Array(values.length).fill(null)
  const alpha = 1 / n
  let prev: number | null = null
  let validCount = 0
  let initSum = 0

  for (let i = 0; i < values.length; i++) {
    const v = values[i]
    if (v == null || !Number.isFinite(v)) continue
    validCount++
    if (prev === null) {
      initSum += v
      if (validCount === n) {
        prev = initSum / n
        out[i] = prev
      }
    } else {
      prev = alpha * v + (1 - alpha) * prev
      out[i] = prev
    }
  }
  return out
}

export function calcWMA(values: (number | null)[], length: number): (number | null)[] {
  const n = Math.max(1, Math.round(length))
  const out: (number | null)[] = new Array(values.length).fill(null)
  const denom = (n * (n + 1)) / 2
  for (let i = n - 1; i < values.length; i++) {
    let weightedSum = 0
    let ok = true
    for (let j = 0; j < n; j++) {
      const val = values[i - n + 1 + j]
      if (val == null || !Number.isFinite(val)) {
        ok = false
        break
      }
      weightedSum += val * (j + 1)
    }
    if (ok) out[i] = weightedSum / denom
  }
  return out
}

export function calcDEMA(values: (number | null)[], length: number): (number | null)[] {
  const e1 = calcEMA(values, length)
  const e2 = calcEMA(e1, length)
  return values.map((_, i) => (e1[i] != null && e2[i] != null ? 2 * e1[i]! - e2[i]! : null))
}

export function calcTEMA(values: (number | null)[], length: number): (number | null)[] {
  const e1 = calcEMA(values, length)
  const e2 = calcEMA(e1, length)
  const e3 = calcEMA(e2, length)
  return values.map((_, i) =>
    e1[i] != null && e2[i] != null && e3[i] != null ? 3 * (e1[i]! - e2[i]!) + e3[i]! : null
  )
}

export function calcHMA(values: (number | null)[], length: number): (number | null)[] {
  const n = Math.max(2, Math.round(length))
  const half = calcWMA(values, Math.max(1, Math.floor(n / 2)))
  const full = calcWMA(values, n)
  const diff = values.map((_, i) => (half[i] != null && full[i] != null ? 2 * half[i]! - full[i]! : null))
  return calcWMA(diff, Math.max(1, Math.round(Math.sqrt(n))))
}

export function calcALMA(
  values: (number | null)[],
  length = 9,
  offset = 0.85,
  sigma = 6
): (number | null)[] {
  const n = Math.max(2, Math.round(length))
  const m = offset * (n - 1)
  const s = n / Math.max(0.5, sigma)
  const weights: number[] = []
  let wSum = 0
  for (let j = 0; j < n; j++) {
    const w = Math.exp(-((j - m) * (j - m)) / (2 * s * s))
    weights.push(w)
    wSum += w
  }
  const out: (number | null)[] = new Array(values.length).fill(null)
  for (let i = n - 1; i < values.length; i++) {
    let acc = 0
    let ok = true
    for (let j = 0; j < n; j++) {
      const v = values[i - n + 1 + j]
      if (v == null || !Number.isFinite(v)) {
        ok = false
        break
      }
      acc += v * weights[j]
    }
    if (ok && wSum > 0) out[i] = acc / wSum
  }
  return out
}

export function calcKAMA(values: (number | null)[], length = 10, fast = 2, slow = 30): (number | null)[] {
  const n = Math.max(2, Math.round(length))
  const fastSc = 2 / (fast + 1)
  const slowSc = 2 / (slow + 1)
  const out: (number | null)[] = new Array(values.length).fill(null)
  let kama: number | null = null

  for (let i = n; i < values.length; i++) {
    const cur = values[i]
    const past = values[i - n]
    if (cur == null || past == null) continue
    let vol = 0
    let ok = true
    for (let j = i - n + 1; j <= i; j++) {
      if (values[j] == null || values[j - 1] == null) {
        ok = false
        break
      }
      vol += Math.abs(values[j]! - values[j - 1]!)
    }
    if (!ok) continue
    const er = vol > 0 ? Math.abs(cur - past) / vol : 0
    const sc = Math.pow(er * (fastSc - slowSc) + slowSc, 2)
    kama = kama === null ? cur : kama + sc * (cur - kama)
    out[i] = kama
  }
  return out
}

export function calcVWMA(candles: TvCandle[], length = 20): (number | null)[] {
  const n = Math.max(1, Math.round(length))
  const out: (number | null)[] = new Array(candles.length).fill(null)
  for (let i = n - 1; i < candles.length; i++) {
    let pv = 0
    let vSum = 0
    for (let j = i - n + 1; j <= i; j++) {
      const vol = Math.max(1, candles[j].volume)
      pv += candles[j].close * vol
      vSum += vol
    }
    out[i] = vSum > 0 ? pv / vSum : candles[i].close
  }
  return out
}

export function calcLSMA(values: (number | null)[], length = 25): (number | null)[] {
  const n = Math.max(2, Math.round(length))
  const out: (number | null)[] = new Array(values.length).fill(null)
  const sumX = ((n - 1) * n) / 2
  const sumX2 = ((n - 1) * n * (2 * n - 1)) / 6
  const denom = n * sumX2 - sumX * sumX

  for (let i = n - 1; i < values.length; i++) {
    let sumY = 0
    let sumXY = 0
    let ok = true
    for (let j = 0; j < n; j++) {
      const y = values[i - n + 1 + j]
      if (y == null || !Number.isFinite(y)) {
        ok = false
        break
      }
      sumY += y
      sumXY += j * y
    }
    if (!ok || denom === 0) continue
    const slope = (n * sumXY - sumX * sumY) / denom
    const intercept = (sumY - slope * sumX) / n
    out[i] = intercept + slope * (n - 1)
  }
  return out
}

export function calcMcGinley(values: (number | null)[], length = 14): (number | null)[] {
  const n = Math.max(2, Math.round(length))
  const out: (number | null)[] = new Array(values.length).fill(null)
  let mg: number | null = null
  for (let i = 0; i < values.length; i++) {
    const v = values[i]
    if (v == null || v <= 0) continue
    if (mg === null) {
      mg = v
    } else {
      const ratio = Math.max(0.25, Math.min(4, v / mg))
      mg = mg + (v - mg) / (n * Math.pow(ratio, 4))
    }
    out[i] = mg
  }
  return out
}

export function calcStdDev(values: (number | null)[], length: number): (number | null)[] {
  const n = Math.max(2, Math.round(length))
  const basis = calcSMA(values, n)
  const out: (number | null)[] = new Array(values.length).fill(null)
  for (let i = n - 1; i < values.length; i++) {
    const mean = basis[i]
    if (mean == null) continue
    let sq = 0
    let ok = true
    for (let j = i - n + 1; j <= i; j++) {
      const v = values[j]
      if (v == null) {
        ok = false
        break
      }
      const d = v - mean
      sq += d * d
    }
    if (ok) out[i] = Math.sqrt(sq / n)
  }
  return out
}

export function calcTrueRange(candles: TvCandle[]): (number | null)[] {
  return candles.map((c, i) => {
    if (i === 0) return c.high - c.low
    const prevClose = candles[i - 1].close
    return Math.max(c.high - c.low, Math.abs(c.high - prevClose), Math.abs(c.low - prevClose))
  })
}

export function calcATR(candles: TvCandle[], length = 14): (number | null)[] {
  return calcRMA(calcTrueRange(candles), length)
}

export function calcHighest(values: (number | null)[], length: number): (number | null)[] {
  const n = Math.max(1, Math.round(length))
  const out: (number | null)[] = new Array(values.length).fill(null)
  for (let i = n - 1; i < values.length; i++) {
    let hh = -Infinity
    for (let j = i - n + 1; j <= i; j++) {
      if (values[j] != null && values[j]! > hh) hh = values[j]!
    }
    if (Number.isFinite(hh)) out[i] = hh
  }
  return out
}

export function calcLowest(values: (number | null)[], length: number): (number | null)[] {
  const n = Math.max(1, Math.round(length))
  const out: (number | null)[] = new Array(values.length).fill(null)
  for (let i = n - 1; i < values.length; i++) {
    let ll = Infinity
    for (let j = i - n + 1; j <= i; j++) {
      if (values[j] != null && values[j]! < ll) ll = values[j]!
    }
    if (Number.isFinite(ll)) out[i] = ll
  }
  return out
}

export function calcRSI(values: (number | null)[], length = 14): (number | null)[] {
  const len = values.length
  const gains: (number | null)[] = new Array(len).fill(null)
  const losses: (number | null)[] = new Array(len).fill(null)
  for (let i = 1; i < len; i++) {
    if (values[i] == null || values[i - 1] == null) continue
    const diff = values[i]! - values[i - 1]!
    gains[i] = diff > 0 ? diff : 0
    losses[i] = diff < 0 ? -diff : 0
  }
  const avgG = calcRMA(gains, length)
  const avgL = calcRMA(losses, length)
  return values.map((_, i) => {
    const g = avgG[i]
    const l = avgL[i]
    if (g == null || l == null) return null
    if (l === 0) return 100
    return 100 - 100 / (1 + g / l)
  })
}

export function calcStochastic(
  candles: TvCandle[],
  kPeriod = 14,
  smoothK = 3,
  dPeriod = 3
): { k: (number | null)[]; d: (number | null)[] } {
  const len = candles.length
  const rawK: (number | null)[] = new Array(len).fill(null)
  const n = Math.max(2, Math.round(kPeriod))
  for (let i = n - 1; i < len; i++) {
    let hh = -Infinity
    let ll = Infinity
    for (let j = i - n + 1; j <= i; j++) {
      if (candles[j].high > hh) hh = candles[j].high
      if (candles[j].low < ll) ll = candles[j].low
    }
    rawK[i] = hh > ll ? ((candles[i].close - ll) / (hh - ll)) * 100 : 50
  }
  const k = smoothK > 1 ? calcSMA(rawK, smoothK) : rawK
  const d = calcSMA(k, dPeriod)
  return { k, d }
}

export function calcCCI(candles: TvCandle[], length = 20): (number | null)[] {
  const n = Math.max(2, Math.round(length))
  const tp = candles.map((c) => (c.high + c.low + c.close) / 3)
  const smaTp = calcSMA(tp, n)
  const out: (number | null)[] = new Array(candles.length).fill(null)
  for (let i = n - 1; i < candles.length; i++) {
    const mean = smaTp[i]
    if (mean == null) continue
    let devSum = 0
    for (let j = i - n + 1; j <= i; j++) devSum += Math.abs(tp[j] - mean)
    const md = devSum / n
    out[i] = md > 0 ? (tp[i] - mean) / (0.015 * md) : 0
  }
  return out
}

export function calcMFI(candles: TvCandle[], length = 14): (number | null)[] {
  const n = Math.max(2, Math.round(length))
  const tp = candles.map((c) => (c.high + c.low + c.close) / 3)
  const out: (number | null)[] = new Array(candles.length).fill(null)
  for (let i = n; i < candles.length; i++) {
    let pos = 0
    let neg = 0
    for (let j = i - n + 1; j <= i; j++) {
      const mf = tp[j] * candles[j].volume
      if (tp[j] > tp[j - 1]) pos += mf
      else if (tp[j] < tp[j - 1]) neg += mf
    }
    out[i] = neg === 0 ? 100 : 100 - 100 / (1 + pos / neg)
  }
  return out
}

export function calcADXSeries(
  candles: TvCandle[],
  length = 14
): { adx: (number | null)[]; plusDi: (number | null)[]; minusDi: (number | null)[] } {
  const len = candles.length
  const tr = calcTrueRange(candles)
  const plusDm: (number | null)[] = new Array(len).fill(null)
  const minusDm: (number | null)[] = new Array(len).fill(null)
  for (let i = 1; i < len; i++) {
    const up = candles[i].high - candles[i - 1].high
    const dn = candles[i - 1].low - candles[i].low
    plusDm[i] = up > dn && up > 0 ? up : 0
    minusDm[i] = dn > up && dn > 0 ? dn : 0
  }
  const atr = calcRMA(tr, length)
  const smP = calcRMA(plusDm, length)
  const smM = calcRMA(minusDm, length)
  const plusDi: (number | null)[] = new Array(len).fill(null)
  const minusDi: (number | null)[] = new Array(len).fill(null)
  const dx: (number | null)[] = new Array(len).fill(null)
  for (let i = 0; i < len; i++) {
    if (atr[i] && atr[i]! > 0 && smP[i] != null && smM[i] != null) {
      const pdi = (100 * smP[i]!) / atr[i]!
      const mdi = (100 * smM[i]!) / atr[i]!
      plusDi[i] = pdi
      minusDi[i] = mdi
      dx[i] = pdi + mdi > 0 ? (100 * Math.abs(pdi - mdi)) / (pdi + mdi) : 0
    }
  }
  return { adx: calcRMA(dx, length), plusDi, minusDi }
}

export function calcSuperTrendSeries(
  candles: TvCandle[],
  atrLength = 10,
  factor = 3
): { line: (number | null)[]; dir: number[]; colors: (string | null)[] } {
  const len = candles.length
  const atr = calcATR(candles, atrLength)
  const line: (number | null)[] = new Array(len).fill(null)
  const dir: number[] = new Array(len).fill(1)
  const colors: (string | null)[] = new Array(len).fill(null)
  let prevUpper = 0
  let prevLower = 0
  let curDir = 1
  for (let i = 0; i < len; i++) {
    const a = atr[i]
    if (a == null) continue
    const hl2 = (candles[i].high + candles[i].low) / 2
    let bUp = hl2 + factor * a
    let bDn = hl2 - factor * a
    if (i > 0 && prevUpper > 0) {
      bUp = bUp < prevUpper || candles[i - 1].close > prevUpper ? bUp : prevUpper
      bDn = bDn > prevLower || candles[i - 1].close < prevLower ? bDn : prevLower
    }
    if (candles[i].close > bUp) curDir = 1
    else if (candles[i].close < bDn) curDir = -1
    prevUpper = bUp
    prevLower = bDn
    dir[i] = curDir
    line[i] = curDir === 1 ? bDn : bUp
    colors[i] = curDir === 1 ? "#089981" : "#F23645"
  }
  return { line, dir, colors }
}

export interface SwingPivotPoint {
  barIndex: number
  price: number
  type: "H" | "L"
}

export function findSwingPivots(candles: TvCandle[], leftRight = 5): SwingPivotPoint[] {
  const pivots: SwingPivotPoint[] = []
  const lr = Math.max(2, Math.round(leftRight))
  for (let i = lr; i < candles.length - lr; i++) {
    let isHigh = true
    let isLow = true
    for (let j = i - lr; j <= i + lr; j++) {
      if (j === i) continue
      if (candles[j].high >= candles[i].high) isHigh = false
      if (candles[j].low <= candles[i].low) isLow = false
    }
    if (isHigh) {
      if (pivots.length && pivots[pivots.length - 1].type === "H") {
        if (candles[i].high > pivots[pivots.length - 1].price) {
          pivots[pivots.length - 1] = { barIndex: i, price: candles[i].high, type: "H" }
        }
      } else {
        pivots.push({ barIndex: i, price: candles[i].high, type: "H" })
      }
    } else if (isLow) {
      if (pivots.length && pivots[pivots.length - 1].type === "L") {
        if (candles[i].low < pivots[pivots.length - 1].price) {
          pivots[pivots.length - 1] = { barIndex: i, price: candles[i].low, type: "L" }
        }
      } else {
        pivots.push({ barIndex: i, price: candles[i].low, type: "L" })
      }
    }
  }
  return pivots
}

/**
 * Safe Mini-Pine / Formula Evaluator for "Tập lệnh của tôi" (My Scripts).
 * Supports: close, open, high, low, volume, hl2, hlc3, ohlc4,
 * sma(src, len), ema(src, len), wma(src, len), rma(src, len), hma(src, len),
 * rsi(src, len), atr(len), stdev(src, len), highest(src, len), lowest(src, len)
 * and arithmetic +, -, *, / with parentheses — zero eval() / Function() security risk!
 */
export function evaluateCustomFormula(candles: TvCandle[], rawFormula: string): (number | null)[] {
  const len = candles.length
  if (!len) return []
  const expr = (rawFormula || "ema(close, 13) - ema(close, 34)").trim().toLowerCase()

  const seriesMap: Record<string, (number | null)[]> = {
    close: candles.map((c) => c.close),
    open: candles.map((c) => c.open),
    high: candles.map((c) => c.high),
    low: candles.map((c) => c.low),
    volume: candles.map((c) => c.volume),
    hl2: candles.map((c) => (c.high + c.low) / 2),
    hlc3: candles.map((c) => (c.high + c.low + c.close) / 3),
    ohlc4: candles.map((c) => (c.open + c.high + c.low + c.close) / 4),
  }

  let tokenCounter = 0
  let working = expr

  // Iteratively resolve function calls from innermost to outermost
  const fnRegex = /\b(sma|ema|wma|rma|hma|rsi|stdev|highest|lowest|atr)\s*\(([^()]*)\)/
  let guard = 0
  while (fnRegex.test(working) && guard++ < 25) {
    working = working.replace(fnRegex, (_, fnName: string, argsStr: string) => {
      const parts = argsStr.split(",").map((s) => s.trim())
      const key = `__s${tokenCounter++}__`
      if (fnName === "atr") {
        const p = Math.max(1, Number(parts[0]) || 14)
        seriesMap[key] = calcATR(candles, p)
      } else {
        const src = seriesMap[parts[0]] || seriesMap.close
        const p = Math.max(1, Number(parts[1]) || 14)
        if (fnName === "sma") seriesMap[key] = calcSMA(src, p)
        else if (fnName === "ema") seriesMap[key] = calcEMA(src, p)
        else if (fnName === "wma") seriesMap[key] = calcWMA(src, p)
        else if (fnName === "rma") seriesMap[key] = calcRMA(src, p)
        else if (fnName === "hma") seriesMap[key] = calcHMA(src, p)
        else if (fnName === "rsi") seriesMap[key] = calcRSI(src, p)
        else if (fnName === "stdev") seriesMap[key] = calcStdDev(src, p)
        else if (fnName === "highest") seriesMap[key] = calcHighest(src, p)
        else if (fnName === "lowest") seriesMap[key] = calcLowest(src, p)
        else seriesMap[key] = src
      }
      return key
    })
  }

  // Safe shunting-yard RPN arithmetic parser per bar
  const tokens =
    working.match(/__s\d+__|close|open|high|low|volume|hl2|hlc3|ohlc4|\d+(?:\.\d+)?|[+\-*/()]/g) || [
      "close",
    ]
  const prec: Record<string, number> = { "+": 1, "-": 1, "*": 2, "/": 2 }
  const outputQueue: string[] = []
  const opStack: string[] = []

  for (let tIdx = 0; tIdx < tokens.length; tIdx++) {
    const tok = tokens[tIdx]
    if (tok in seriesMap || /^\d/.test(tok)) {
      outputQueue.push(tok)
    } else if (tok in prec) {
      // Handle unary minus cleanly
      const prevTok = tIdx > 0 ? tokens[tIdx - 1] : "("
      if (tok === "-" && (prevTok === "(" || prevTok in prec)) {
        outputQueue.push("0")
      }
      while (
        opStack.length > 0 &&
        opStack[opStack.length - 1] in prec &&
        prec[opStack[opStack.length - 1]] >= prec[tok]
      ) {
        outputQueue.push(opStack.pop()!)
      }
      opStack.push(tok)
    } else if (tok === "(") {
      opStack.push(tok)
    } else if (tok === ")") {
      while (opStack.length > 0 && opStack[opStack.length - 1] !== "(") {
        outputQueue.push(opStack.pop()!)
      }
      opStack.pop()
    }
  }
  while (opStack.length > 0) {
    const op = opStack.pop()!
    if (op !== "(" && op !== ")") outputQueue.push(op)
  }

  const out: (number | null)[] = new Array(len).fill(null)
  for (let i = 0; i < len; i++) {
    const st: number[] = []
    let valid = true
    for (const rpnTok of outputQueue) {
      if (rpnTok in seriesMap) {
        const v = seriesMap[rpnTok][i]
        if (v == null || !Number.isFinite(v)) {
          valid = false
          break
        }
        st.push(v)
      } else if (/^\d/.test(rpnTok)) {
        st.push(Number(rpnTok))
      } else {
        const b = st.pop()
        const a = st.pop()
        if (a === undefined || b === undefined) {
          valid = false
          break
        }
        if (rpnTok === "+") st.push(a + b)
        else if (rpnTok === "-") st.push(a - b)
        else if (rpnTok === "*") st.push(a * b)
        else if (rpnTok === "/") st.push(Math.abs(b) > 1e-9 ? a / b : 0)
      }
    }
    if (valid && st.length > 0 && Number.isFinite(st[st.length - 1])) {
      out[i] = st[st.length - 1]
    }
  }
  return out
}
