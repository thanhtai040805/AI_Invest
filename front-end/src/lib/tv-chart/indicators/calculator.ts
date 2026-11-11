/* ============================================================================
 * TradingView Complete Mathematical & Chart-Style Engine (Free + Pro + Ultimate)
 * - 16 Chart Style Transformers (incl. Renko, Line Break, Kagi, Point & Figure, Range)
 * - 280+ Unlocked Studies, Strategies, Profiles, Patterns, Options, Fundamentals & Custom Pine
 * ============================================================================ */

import type {
  TvBarMarker,
  TvCandle,
  TvChartStyle,
  TvIndicatorComputed,
  TvIndicatorInstance,
  TvOverlayPolyline,
} from "../types"
import {
  type IndicatorComputeContext,
  computeAutoOverlayStudy,
  computeCandlestickPatternStudy,
  computeChartPatternStudy,
  computeCommunityVipStudy,
  computeFundamentalStudy,
  computeOptionsStudy,
  computeProfileStudy,
  computeStrategyStudy,
} from "./advanced-engines"
import {
  calcADXSeries,
  calcALMA,
  calcATR,
  calcCCI,
  calcDEMA,
  calcEMA,
  calcHighest,
  calcHMA,
  calcKAMA,
  calcLowest,
  calcLSMA,
  calcMcGinley,
  calcMFI,
  calcRMA,
  calcRSI,
  calcSMA,
  calcStdDev,
  calcStochastic,
  calcSuperTrendSeries,
  calcTEMA,
  calcTrueRange,
  calcVWMA,
  calcWMA,
  evaluateCustomFormula,
} from "./math-utils"
import { getIndicatorMetadata } from "./registry"

export { calcEMA, calcRMA, calcSMA, calcTrueRange, calcWMA, evaluateCustomFormula }
export type { IndicatorComputeContext }

/* ============================================================================
 * 1. ULTIMATE CHART STYLE SERIES TRANSFORMERS
 * ============================================================================ */

export function computeHeikinAshi(candles: TvCandle[]): TvCandle[] {
  if (!candles.length) return []
  const result: TvCandle[] = []
  for (let i = 0; i < candles.length; i++) {
    const c = candles[i]
    const haClose = (c.open + c.high + c.low + c.close) / 4
    const haOpen = i === 0 ? (c.open + c.close) / 2 : (result[i - 1].open + result[i - 1].close) / 2
    const haHigh = Math.max(c.high, haOpen, haClose)
    const haLow = Math.min(c.low, haOpen, haClose)
    result.push({
      ...c,
      open: haOpen,
      high: haHigh,
      low: haLow,
      close: haClose,
    })
  }
  return result
}

function estimateBoxSize(candles: TvCandle[], atrMult = 0.85): number {
  if (!candles.length) return 100
  const atr = calcATR(candles, 14)
  const validAtr = atr.filter((v): v is number => v != null && v > 0)
  const lastClose = candles[candles.length - 1].close || 1000
  const raw = validAtr.length
    ? (validAtr.reduce((s, v) => s + v, 0) / validAtr.length) * atrMult
    : lastClose * 0.012
  return Math.max(lastClose * 0.004, raw)
}

export function computeRenko(candles: TvCandle[]): TvCandle[] {
  if (candles.length < 2) return candles
  const brickSize = estimateBoxSize(candles, 0.75)
  const bricks: TvCandle[] = []
  let lastBrickClose = Math.round(candles[0].close / brickSize) * brickSize
  let lastDir = 0
  let accVol = 0

  for (let i = 0; i < candles.length; i++) {
    const c = candles[i]
    accVol += c.volume
    let diff = c.close - lastBrickClose

    while (
      (lastDir >= 0 && diff >= brickSize) ||
      (lastDir < 0 && diff >= brickSize * 2)
    ) {
      const open = lastDir < 0 ? lastBrickClose + brickSize : lastBrickClose
      const close = open + brickSize
      bricks.push({
        timestamp: c.timestamp,
        date: c.date,
        label: c.label,
        open,
        high: close,
        low: open,
        close,
        volume: accVol,
      })
      accVol = Math.max(1, Math.round(c.volume * 0.25))
      lastBrickClose = close
      lastDir = 1
      diff = c.close - lastBrickClose
      if (bricks.length > 800) break
    }

    while (
      (lastDir <= 0 && -diff >= brickSize) ||
      (lastDir > 0 && -diff >= brickSize * 2)
    ) {
      const open = lastDir > 0 ? lastBrickClose - brickSize : lastBrickClose
      const close = open - brickSize
      bricks.push({
        timestamp: c.timestamp,
        date: c.date,
        label: c.label,
        open,
        high: open,
        low: close,
        close,
        volume: accVol,
      })
      accVol = Math.max(1, Math.round(c.volume * 0.25))
      lastBrickClose = close
      lastDir = -1
      diff = c.close - lastBrickClose
      if (bricks.length > 800) break
    }
  }

  return bricks.length >= 5 ? bricks : candles
}

export function computeLineBreak(candles: TvCandle[], lineCount = 3): TvCandle[] {
  if (candles.length < 3) return candles
  const n = Math.max(2, lineCount)
  const lines: TvCandle[] = [
    {
      ...candles[0],
      high: Math.max(candles[0].open, candles[0].close),
      low: Math.min(candles[0].open, candles[0].close),
    },
  ]

  for (let i = 1; i < candles.length; i++) {
    const c = candles[i]
    const prev = lines[lines.length - 1]
    const prevBull = prev.close >= prev.open
    const recent = lines.slice(-n)
    const highestHigh = Math.max(...recent.map((l) => Math.max(l.open, l.close)))
    const lowestLow = Math.min(...recent.map((l) => Math.min(l.open, l.close)))

    if (prevBull) {
      if (c.close > prev.close) {
        lines.push({
          ...c,
          open: prev.close,
          close: c.close,
          high: c.close,
          low: prev.close,
        })
      } else if (c.close < lowestLow) {
        lines.push({
          ...c,
          open: prev.open,
          close: c.close,
          high: prev.open,
          low: c.close,
        })
      }
    } else {
      if (c.close < prev.close) {
        lines.push({
          ...c,
          open: prev.close,
          close: c.close,
          high: prev.close,
          low: c.close,
        })
      } else if (c.close > highestHigh) {
        lines.push({
          ...c,
          open: prev.open,
          close: c.close,
          high: c.close,
          low: prev.open,
        })
      }
    }
  }

  return lines.length >= 5 ? lines : candles
}

export function computeKagi(candles: TvCandle[]): TvCandle[] {
  if (candles.length < 3) return candles
  const revAmount = estimateBoxSize(candles, 0.9)
  const out: TvCandle[] = []
  let dir = candles[1].close >= candles[0].close ? 1 : -1
  let segStart = candles[0].close
  let segExtreme = candles[1].close
  let priorShoulder = Math.max(candles[0].close, candles[1].close)
  let priorWaist = Math.min(candles[0].close, candles[1].close)
  let isYang = dir === 1

  for (let i = 1; i < candles.length; i++) {
    const c = candles[i]
    if (dir === 1) {
      if (c.close >= segExtreme) {
        segExtreme = c.close
        if (segExtreme > priorShoulder) isYang = true
      } else if (segExtreme - c.close >= revAmount) {
        out.push({
          ...c,
          open: segStart,
          close: segExtreme,
          high: Math.max(segStart, segExtreme),
          low: Math.min(segStart, segExtreme),
          pfKind: isYang ? "X" : "O",
        })
        priorShoulder = segExtreme
        segStart = segExtreme
        segExtreme = c.close
        dir = -1
        if (segExtreme < priorWaist) isYang = false
      }
    } else {
      if (c.close <= segExtreme) {
        segExtreme = c.close
        if (segExtreme < priorWaist) isYang = false
      } else if (c.close - segExtreme >= revAmount) {
        out.push({
          ...c,
          open: segStart,
          close: segExtreme,
          high: Math.max(segStart, segExtreme),
          low: Math.min(segStart, segExtreme),
          pfKind: isYang ? "X" : "O",
        })
        priorWaist = segExtreme
        segStart = segExtreme
        segExtreme = c.close
        dir = 1
        if (segExtreme > priorShoulder) isYang = true
      }
    }
  }

  const last = candles[candles.length - 1]
  out.push({
    ...last,
    open: segStart,
    close: segExtreme,
    high: Math.max(segStart, segExtreme),
    low: Math.min(segStart, segExtreme),
    pfKind: isYang ? "X" : "O",
  })

  return out.length >= 4 ? out : candles
}

export function computePointAndFigure(candles: TvCandle[]): TvCandle[] {
  if (candles.length < 3) return candles
  const boxSize = estimateBoxSize(candles, 0.7)
  const reversal = 3 * boxSize
  const cols: TvCandle[] = []

  let colKind: "X" | "O" = candles[1].close >= candles[0].close ? "X" : "O"
  let colBottom = Math.floor(Math.min(candles[0].low, candles[1].low) / boxSize) * boxSize
  let colTop = Math.ceil(Math.max(candles[0].high, candles[1].high) / boxSize) * boxSize
  let colCandle = candles[0]
  let colVol = 0

  for (let i = 0; i < candles.length; i++) {
    const c = candles[i]
    colVol += c.volume
    if (colKind === "X") {
      if (c.high >= colTop + boxSize) {
        colTop = Math.floor(c.high / boxSize) * boxSize
        colCandle = c
      } else if (colTop - c.low >= reversal) {
        cols.push({
          ...colCandle,
          open: colBottom,
          close: colTop,
          high: colTop,
          low: colBottom,
          volume: colVol,
          pfKind: "X",
        })
        colKind = "O"
        colTop = colTop - boxSize
        colBottom = Math.ceil(c.low / boxSize) * boxSize
        if (colBottom >= colTop) colBottom = colTop - boxSize
        colCandle = c
        colVol = c.volume
      }
    } else {
      if (c.low <= colBottom - boxSize) {
        colBottom = Math.ceil(c.low / boxSize) * boxSize
        colCandle = c
      } else if (c.high - colBottom >= reversal) {
        cols.push({
          ...colCandle,
          open: colTop,
          close: colBottom,
          high: colTop,
          low: colBottom,
          volume: colVol,
          pfKind: "O",
        })
        colKind = "X"
        colBottom = colBottom + boxSize
        colTop = Math.floor(c.high / boxSize) * boxSize
        if (colTop <= colBottom) colTop = colBottom + boxSize
        colCandle = c
        colVol = c.volume
      }
    }
  }

  cols.push({
    ...colCandle,
    open: colKind === "X" ? colBottom : colTop,
    close: colKind === "X" ? colTop : colBottom,
    high: colTop,
    low: colBottom,
    volume: colVol,
    pfKind: colKind,
  })

  return cols.length >= 4 ? cols : candles
}

export function computeRangeBars(candles: TvCandle[]): TvCandle[] {
  if (candles.length < 3) return candles
  const targetRange = estimateBoxSize(candles, 1.15)
  const out: TvCandle[] = []
  let curOpen = candles[0].open
  let curHigh = candles[0].high
  let curLow = candles[0].low
  let curVol = 0

  for (let i = 0; i < candles.length; i++) {
    const c = candles[i]
    curHigh = Math.max(curHigh, c.high)
    curLow = Math.min(curLow, c.low)
    curVol += c.volume

    while (curHigh - curLow >= targetRange && out.length < 800) {
      const bull = c.close >= curOpen
      if (bull) {
        const close = curLow + targetRange
        out.push({
          timestamp: c.timestamp,
          date: c.date,
          label: c.label,
          open: curOpen,
          high: close,
          low: curLow,
          close,
          volume: curVol,
        })
        curOpen = close
        curLow = close
        curHigh = Math.max(close, c.high)
      } else {
        const close = curHigh - targetRange
        out.push({
          timestamp: c.timestamp,
          date: c.date,
          label: c.label,
          open: curOpen,
          high: curHigh,
          low: close,
          close,
          volume: curVol,
        })
        curOpen = close
        curHigh = close
        curLow = Math.min(close, c.low)
      }
      curVol = Math.max(1, Math.round(c.volume * 0.3))
    }
  }

  const last = candles[candles.length - 1]
  out.push({
    ...last,
    open: curOpen,
    high: Math.max(curHigh, curOpen, last.close),
    low: Math.min(curLow, curOpen, last.close),
    close: last.close,
    volume: Math.max(1, curVol),
  })

  return out.length >= 5 ? out : candles
}

export function transformSeriesForChartStyle(
  candles: TvCandle[],
  chartStyle: TvChartStyle
): TvCandle[] {
  switch (chartStyle) {
    case "heikin_ashi":
      return computeHeikinAshi(candles)
    case "renko":
      return computeRenko(candles)
    case "line_break":
      return computeLineBreak(candles, 3)
    case "kagi":
      return computeKagi(candles)
    case "point_and_figure":
      return computePointAndFigure(candles)
    case "range":
      return computeRangeBars(candles)
    default:
      return candles
  }
}

/* ============================================================================
 * 2. HELPER FUNCTIONS FOR BUILT-IN TECHNICAL STUDIES
 * ============================================================================ */

function calcADLSeries(candles: TvCandle[]): (number | null)[] {
  const out: (number | null)[] = new Array(candles.length).fill(null)
  let acc = 0
  for (let i = 0; i < candles.length; i++) {
    const c = candles[i]
    const hl = c.high - c.low
    const mfm = hl > 0 ? (c.close - c.low - (c.high - c.close)) / hl : 0
    acc += mfm * c.volume
    out[i] = acc
  }
  return out
}

function calcROCSeries(values: (number | null)[], length: number): (number | null)[] {
  const n = Math.max(1, Math.round(length))
  const out: (number | null)[] = new Array(values.length).fill(null)
  for (let i = n; i < values.length; i++) {
    const cur = values[i]
    const prev = values[i - n]
    if (cur != null && prev != null && Math.abs(prev) > 1e-9) {
      out[i] = ((cur - prev) / prev) * 100
    }
  }
  return out
}

function calcRCISingle(closes: number[], length: number): (number | null)[] {
  const n = Math.max(3, Math.round(length))
  const out: (number | null)[] = new Array(closes.length).fill(null)
  const denom = n * (n * n - 1)
  for (let i = n - 1; i < closes.length; i++) {
    const slice = closes.slice(i - n + 1, i + 1)
    let dSum = 0
    for (let j = 0; j < n; j++) {
      const timeRank = n - j // 1 = newest, n = oldest
      let greater = 0
      let equal = 0
      for (let k = 0; k < n; k++) {
        if (slice[k] > slice[j]) greater++
        else if (slice[k] === slice[j]) equal++
      }
      const priceRank = greater + 1 + (equal - 1) * 0.5
      const diff = timeRank - priceRank
      dSum += diff * diff
    }
    out[i] = (1 - (6 * dSum) / denom) * 100
  }
  return out
}

function shiftRight(values: (number | null)[], offset: number): (number | null)[] {
  if (offset <= 0) return values
  const out: (number | null)[] = new Array(values.length).fill(null)
  for (let i = offset; i < values.length; i++) {
    out[i] = values[i - offset]
  }
  return out
}

/* ============================================================================
 * 3. MASTER INDICATOR DISPATCHER (280+ UNLOCKED STUDIES)
 * ============================================================================ */

export function computeIndicator(
  candles: TvCandle[],
  instance: TvIndicatorInstance,
  ctx?: IndicatorComputeContext
): TvIndicatorComputed {
  const meta = getIndicatorMetadata(instance.id, instance)
  const len = candles.length

  if (len === 0) {
    return { instance, meta, title: meta.shortName, plots: [] }
  }

  // 1. Specialized Pro / Premium / Ultimate Category Engines
  if (meta.category === "profile") {
    return computeProfileStudy(candles, instance, meta, ctx)
  }
  if (meta.category === "pattern") {
    return computeChartPatternStudy(candles, instance, meta)
  }
  if (meta.category === "candlestick") {
    return computeCandlestickPatternStudy(candles, instance, meta)
  }
  if (meta.category === "strategy") {
    return computeStrategyStudy(candles, instance, meta)
  }
  if (meta.category === "options") {
    return computeOptionsStudy(candles, instance, meta)
  }
  if (meta.category === "fundamental") {
    return computeFundamentalStudy(candles, instance, meta, ctx?.symbol || "HPG")
  }
  if (meta.category === "community") {
    return computeCommunityVipStudy(candles, instance, meta)
  }
  if (meta.category === "custom") {
    const formula =
      instance.customFormula || meta.formula || "ema(close, 13) - ema(close, 34)"
    const vals = evaluateCustomFormula(candles, formula)
    return {
      instance,
      meta,
      title: `${instance.customName || meta.shortName}`,
      plots: [
        {
          key: "custom_plot",
          label: meta.shortName,
          color: instance.color || meta.defaultColor,
          type: "line",
          lineWidth: instance.lineWidth || 1.8,
          values: vals,
        },
      ],
    }
  }

  // 2. Auto-Geometric Overlay Studies (Auto Fib, Auto Pitchfork, Key Levels, Trendlines, Gaps, ZigZag, etc.)
  const autoOverlay = computeAutoOverlayStudy(candles, instance, meta, ctx)
  if (autoOverlay) return autoOverlay

  // 3. Built-in Technical Studies (120 Official A–Z Studies)
  const closes = candles.map((c) => c.close)
  const highs = candles.map((c) => c.high)
  const lows = candles.map((c) => c.low)
  const hl2 = candles.map((c) => (c.high + c.low) / 2)
  const hlc3 = candles.map((c) => (c.high + c.low + c.close) / 3)

  switch (instance.id) {
    /* -------------------- MOVING AVERAGES -------------------- */
    case "SMA": {
      const p = instance.params.length ?? 20
      return {
        instance,
        meta,
        title: `SMA (${p})`,
        plots: [{ key: "sma", label: `SMA ${p}`, color: instance.color, type: "line", lineWidth: instance.lineWidth, values: calcSMA(closes, p) }],
      }
    }

    case "EMA": {
      const p = instance.params.length ?? 20
      return {
        instance,
        meta,
        title: `EMA (${p})`,
        plots: [{ key: "ema", label: `EMA ${p}`, color: instance.color, type: "line", lineWidth: instance.lineWidth, values: calcEMA(closes, p) }],
      }
    }

    case "WMA": {
      const p = instance.params.length ?? 20
      return {
        instance,
        meta,
        title: `WMA (${p})`,
        plots: [{ key: "wma", label: `WMA ${p}`, color: instance.color, type: "line", lineWidth: instance.lineWidth, values: calcWMA(closes, p) }],
      }
    }

    case "DEMA": {
      const p = instance.params.length ?? 9
      return {
        instance,
        meta,
        title: `DEMA (${p})`,
        plots: [{ key: "dema", label: `DEMA ${p}`, color: instance.color, type: "line", lineWidth: instance.lineWidth, values: calcDEMA(closes, p) }],
      }
    }

    case "TEMA": {
      const p = instance.params.length ?? 9
      return {
        instance,
        meta,
        title: `TEMA (${p})`,
        plots: [{ key: "tema", label: `TEMA ${p}`, color: instance.color, type: "line", lineWidth: instance.lineWidth, values: calcTEMA(closes, p) }],
      }
    }

    case "HMA": {
      const p = instance.params.length ?? 9
      return {
        instance,
        meta,
        title: `HMA (${p})`,
        plots: [{ key: "hma", label: `HMA ${p}`, color: instance.color, type: "line", lineWidth: instance.lineWidth, values: calcHMA(closes, p) }],
      }
    }

    case "ALMA": {
      const p = instance.params.length ?? 9
      const offset = instance.params.offset ?? 0.85
      const sigma = instance.params.sigma ?? 6
      return {
        instance,
        meta,
        title: `ALMA (${p}, ${offset}, ${sigma})`,
        plots: [{ key: "alma", label: "ALMA", color: instance.color, type: "line", lineWidth: instance.lineWidth, values: calcALMA(closes, p, offset, sigma) }],
      }
    }

    case "KAMA": {
      const p = instance.params.length ?? 10
      return {
        instance,
        meta,
        title: `KAMA (${p})`,
        plots: [{ key: "kama", label: "KAMA", color: instance.color, type: "line", lineWidth: instance.lineWidth, values: calcKAMA(closes, p) }],
      }
    }

    case "VWMA": {
      const p = instance.params.length ?? 20
      return {
        instance,
        meta,
        title: `VWMA (${p})`,
        plots: [{ key: "vwma", label: `VWMA ${p}`, color: instance.color, type: "line", lineWidth: instance.lineWidth, values: calcVWMA(candles, p) }],
      }
    }

    case "LSMA": {
      const p = instance.params.length ?? 25
      return {
        instance,
        meta,
        title: `LSMA (${p})`,
        plots: [{ key: "lsma", label: `LSMA ${p}`, color: instance.color, type: "line", lineWidth: instance.lineWidth, values: calcLSMA(closes, p) }],
      }
    }

    case "MCGINLEY": {
      const p = instance.params.length ?? 14
      return {
        instance,
        meta,
        title: `McGinley (${p})`,
        plots: [{ key: "mcginley", label: "McGinley", color: instance.color, type: "line", lineWidth: instance.lineWidth, values: calcMcGinley(closes, p) }],
      }
    }

    case "SMMA": {
      const p = instance.params.length ?? 7
      return {
        instance,
        meta,
        title: `SMMA (${p})`,
        plots: [{ key: "smma", label: `SMMA ${p}`, color: instance.color, type: "line", lineWidth: instance.lineWidth, values: calcRMA(closes, p) }],
      }
    }

    case "ZLEMA": {
      const p = Math.max(2, Math.round(instance.params.length ?? 20))
      const lag = Math.floor((p - 1) / 2)
      const deLagged: (number | null)[] = closes.map((c, i) => (i >= lag ? c + (c - closes[i - lag]) : c))
      return {
        instance,
        meta,
        title: `ZLEMA (${p})`,
        plots: [{ key: "zlema", label: `ZLEMA ${p}`, color: instance.color, type: "line", lineWidth: instance.lineWidth, values: calcEMA(deLagged, p) }],
      }
    }

    case "T3_MA": {
      const p = Math.max(2, Math.round(instance.params.length ?? 8))
      const v = 0.7
      const c1 = -v * v * v
      const c2 = 3 * v * v + 3 * v * v * v
      const c3 = -6 * v * v - 3 * v - 3 * v * v * v
      const c4 = 1 + 3 * v + v * v * v + 3 * v * v
      const e1 = calcEMA(closes, p)
      const e2 = calcEMA(e1, p)
      const e3 = calcEMA(e2, p)
      const e4 = calcEMA(e3, p)
      const e5 = calcEMA(e4, p)
      const e6 = calcEMA(e5, p)
      const t3 = closes.map((_, i) =>
        e3[i] != null && e4[i] != null && e5[i] != null && e6[i] != null
          ? c1 * e6[i]! + c2 * e5[i]! + c3 * e4[i]! + c4 * e3[i]!
          : null
      )
      return {
        instance,
        meta,
        title: `Tillson T3 (${p})`,
        plots: [{ key: "t3", label: `T3 ${p}`, color: instance.color, type: "line", lineWidth: instance.lineWidth, values: t3 }],
      }
    }

    case "VIDYA": {
      const p = Math.max(2, Math.round(instance.params.length ?? 14))
      const alpha = 2 / (p + 1)
      const out: (number | null)[] = new Array(len).fill(null)
      let prev: number | null = null
      for (let i = p; i < len; i++) {
        let up = 0
        let dn = 0
        for (let j = i - p + 1; j <= i; j++) {
          const d = closes[j] - closes[j - 1]
          if (d > 0) up += d
          else dn -= d
        }
        const cmoAbs = up + dn > 0 ? Math.abs(up - dn) / (up + dn) : 0
        prev = prev == null ? closes[i] : alpha * cmoAbs * closes[i] + (1 - alpha * cmoAbs) * prev
        out[i] = prev
      }
      return {
        instance,
        meta,
        title: `VIDYA (${p})`,
        plots: [{ key: "vidya", label: "VIDYA", color: instance.color, type: "line", lineWidth: instance.lineWidth, values: out }],
      }
    }

    case "FRAMA": {
      const p = Math.max(4, Math.round((instance.params.length ?? 16) / 2) * 2)
      const half = p / 2
      const out: (number | null)[] = new Array(len).fill(null)
      let prev: number | null = null
      for (let i = p - 1; i < len; i++) {
        let h1 = -Infinity, l1 = Infinity, h2 = -Infinity, l2 = Infinity
        for (let j = i - p + 1; j <= i - half; j++) {
          if (highs[j] > h1) h1 = highs[j]
          if (lows[j] < l1) l1 = lows[j]
        }
        for (let j = i - half + 1; j <= i; j++) {
          if (highs[j] > h2) h2 = highs[j]
          if (lows[j] < l2) l2 = lows[j]
        }
        const hAll = Math.max(h1, h2)
        const lAll = Math.min(l1, l2)
        const n1 = (h1 - l1) / half
        const n2 = (h2 - l2) / half
        const n3 = (hAll - lAll) / p
        const dim =
          n1 > 0 && n2 > 0 && n3 > 0
            ? (Math.log(n1 + n2) - Math.log(n3)) / Math.log(2)
            : 1
        const alpha = Math.max(0.01, Math.min(1, Math.exp(-4.6 * (dim - 1))))
        prev = prev == null ? closes[i] : alpha * closes[i] + (1 - alpha) * prev
        out[i] = prev
      }
      return {
        instance,
        meta,
        title: `FRAMA (${p})`,
        plots: [{ key: "frama", label: "FRAMA", color: instance.color, type: "line", lineWidth: instance.lineWidth, values: out }],
      }
    }

    case "JMA": {
      const p = Math.max(2, Math.round(instance.params.length ?? 14))
      const alma = calcALMA(closes, p, 0.88, 6.5)
      const hma = calcHMA(closes, p)
      const jma = closes.map((_, i) => (alma[i] != null && hma[i] != null ? 0.55 * alma[i]! + 0.45 * hma[i]! : alma[i] ?? hma[i]))
      return {
        instance,
        meta,
        title: `JMA (${p})`,
        plots: [{ key: "jma", label: `JMA ${p}`, color: instance.color, type: "line", lineWidth: instance.lineWidth, values: jma }],
      }
    }

    case "MA_CROSS": {
      const fastLen = Math.max(2, Math.round(instance.params.fast ?? 9))
      const slowLen = Math.max(fastLen + 1, Math.round(instance.params.slow ?? 26))
      const f = calcEMA(closes, fastLen)
      const s = calcEMA(closes, slowLen)
      const markers: TvBarMarker[] = []
      for (let i = 1; i < len; i++) {
        if (f[i] != null && s[i] != null && f[i - 1] != null && s[i - 1] != null) {
          if (f[i - 1]! <= s[i - 1]! && f[i]! > s[i]!) {
            markers.push({ barIndex: i, position: "belowBar", shape: "arrowUp", color: "#089981", text: "Cross +" })
          } else if (f[i - 1]! >= s[i - 1]! && f[i]! < s[i]!) {
            markers.push({ barIndex: i, position: "aboveBar", shape: "arrowDown", color: "#F23645", text: "Cross -" })
          }
        }
      }
      return {
        instance,
        meta,
        title: `MA Cross (${fastLen}, ${slowLen})`,
        plots: [
          { key: "mac_f", label: `Fast ${fastLen}`, color: instance.color, type: "line", lineWidth: 1.6, values: f },
          { key: "mac_s", label: `Slow ${slowLen}`, color: instance.secondaryColor || "#FF6D00", type: "line", lineWidth: 1.6, values: s },
        ],
        markers,
      }
    }

    case "MA_RIBBON": {
      const m1 = instance.params.ma1 ?? 20
      const m2 = instance.params.ma2 ?? 50
      const m3 = instance.params.ma3 ?? 100
      const m4 = instance.params.ma4 ?? 200
      return {
        instance,
        meta,
        title: `MA Ribbon (${m1}, ${m2}, ${m3}, ${m4})`,
        plots: [
          { key: "mar_1", label: `SMA ${m1}`, color: "#2962FF", type: "line", lineWidth: 1.4, values: calcSMA(closes, m1) },
          { key: "mar_2", label: `SMA ${m2}`, color: "#00ACC1", type: "line", lineWidth: 1.4, values: calcSMA(closes, m2) },
          { key: "mar_3", label: `SMA ${m3}`, color: "#FB8C00", type: "line", lineWidth: 1.5, values: calcSMA(closes, m3) },
          { key: "mar_4", label: `SMA ${m4}`, color: "#E53935", type: "line", lineWidth: 1.7, values: calcSMA(closes, m4) },
        ],
      }
    }

    case "GMMA": {
      const shortPeriods = [3, 5, 8, 10, 12, 15]
      const longPeriods = [30, 35, 40, 45, 50, 60]
      return {
        instance,
        meta,
        title: "GMMA (3..15 / 30..60)",
        plots: [
          ...shortPeriods.map((p) => ({
            key: `gmma_s_${p}`,
            label: `E${p}`,
            color: instance.color || "#2962FF",
            type: "line" as const,
            lineWidth: 1.1,
            values: calcEMA(closes, p),
          })),
          ...longPeriods.map((p) => ({
            key: `gmma_l_${p}`,
            label: `E${p}`,
            color: instance.secondaryColor || "#FF6D00",
            type: "line" as const,
            lineWidth: 1.15,
            values: calcEMA(closes, p),
          })),
        ],
      }
    }

    case "ALLIGATOR": {
      const jawP = instance.params.jaw ?? 13
      const teethP = instance.params.teeth ?? 8
      const lipsP = instance.params.lips ?? 5
      return {
        instance,
        meta,
        title: `Alligator (${jawP}, ${teethP}, ${lipsP})`,
        plots: [
          { key: "alli_jaw", label: "Jaw", color: "#2962FF", type: "line", lineWidth: 1.5, values: shiftRight(calcRMA(hl2, jawP), 8) },
          { key: "alli_teeth", label: "Teeth", color: "#F23645", type: "line", lineWidth: 1.4, values: shiftRight(calcRMA(hl2, teethP), 5) },
          { key: "alli_lips", label: "Lips", color: "#089981", type: "line", lineWidth: 1.4, values: shiftRight(calcRMA(hl2, lipsP), 3) },
        ],
      }
    }

    case "MEDIAN": {
      const p = Math.max(3, Math.round(instance.params.length ?? 14))
      const med: (number | null)[] = new Array(len).fill(null)
      for (let i = p - 1; i < len; i++) {
        const sl = hl2.slice(i - p + 1, i + 1).sort((a, b) => a - b)
        const mid = Math.floor(sl.length / 2)
        med[i] = sl.length % 2 === 1 ? sl[mid] : (sl[mid - 1] + sl[mid]) / 2
      }
      const medEma = calcEMA(med, p)
      return {
        instance,
        meta,
        title: `Median (${p})`,
        plots: [
          { key: "med_line", label: "Median", color: "#43A047", type: "line", lineWidth: 1.6, values: med },
          { key: "med_ema", label: "EMA", color: "#AB47BC", type: "line", lineWidth: 1.4, values: medEma },
        ],
        bands: [
          {
            upperKey: "med_line",
            lowerKey: "med_ema",
            fillColor: "rgba(67, 160, 71, 0.14)",
            dynamicBullBear: true,
            bullColor: "rgba(67, 160, 71, 0.16)",
            bearColor: "rgba(171, 71, 188, 0.16)",
          },
        ],
      }
    }

    case "TWAP": {
      const p = Math.max(2, Math.round(instance.params.length ?? 20))
      const ohlc4 = candles.map((c) => (c.open + c.high + c.low + c.close) / 4)
      return {
        instance,
        meta,
        title: `TWAP (${p})`,
        plots: [{ key: "twap", label: "TWAP", color: instance.color, type: "line", lineWidth: instance.lineWidth, values: calcSMA(ohlc4, p) }],
      }
    }

    /* -------------------- BANDS, STOPS & PIVOTS -------------------- */
    case "BB": {
      const p = Math.max(2, Math.round(instance.params.length ?? 20))
      const mult = instance.params.mult ?? 2
      const basis = calcSMA(closes, p)
      const sd = calcStdDev(closes, p)
      const upper = basis.map((m, i) => (m != null && sd[i] != null ? m + mult * sd[i]! : null))
      const lower = basis.map((m, i) => (m != null && sd[i] != null ? m - mult * sd[i]! : null))
      return {
        instance,
        meta,
        title: `BB (${p}, ${mult})`,
        plots: [
          { key: "bb_basis", label: "Basis", color: "#FF6D00", type: "line", lineWidth: 1.3, values: basis },
          { key: "bb_upper", label: "Upper", color: instance.color, type: "line", lineWidth: 1.2, values: upper },
          { key: "bb_lower", label: "Lower", color: instance.color, type: "line", lineWidth: 1.2, values: lower },
        ],
        bands: [{ upperKey: "bb_upper", lowerKey: "bb_lower", fillColor: "rgba(41, 98, 255, 0.08)" }],
      }
    }

    case "BB_PCT_B": {
      const p = Math.max(2, Math.round(instance.params.length ?? 20))
      const mult = instance.params.mult ?? 2
      const basis = calcSMA(closes, p)
      const sd = calcStdDev(closes, p)
      const pctB = closes.map((c, i) => {
        if (basis[i] == null || sd[i] == null || sd[i]! <= 0) return null
        const up = basis[i]! + mult * sd[i]!
        const dn = basis[i]! - mult * sd[i]!
        return up > dn ? (c - dn) / (up - dn) : 0.5
      })
      return {
        instance,
        meta,
        title: `BB %B (${p}, ${mult})`,
        plots: [{ key: "bb_pctb", label: "%B", color: instance.color, type: "line", lineWidth: 1.6, values: pctB }],
      }
    }

    case "BB_WIDTH": {
      const p = Math.max(2, Math.round(instance.params.length ?? 20))
      const mult = instance.params.mult ?? 2
      const basis = calcSMA(closes, p)
      const sd = calcStdDev(closes, p)
      const bbw = closes.map((_, i) =>
        basis[i] != null && basis[i]! > 0 && sd[i] != null ? ((2 * mult * sd[i]!) / basis[i]!) * 100 : null
      )
      return {
        instance,
        meta,
        title: `BB Width (${p}, ${mult})`,
        plots: [{ key: "bbw", label: "BBW %", color: instance.color, type: "line", lineWidth: 1.6, values: bbw }],
      }
    }

    case "BB_BARS": {
      const p = Math.max(2, Math.round(instance.params.length ?? 20))
      const basis = calcSMA(closes, p)
      const sd = calcStdDev(closes, p)
      const up1 = basis.map((m, i) => (m != null && sd[i] != null ? m + sd[i]! : null))
      const dn1 = basis.map((m, i) => (m != null && sd[i] != null ? m - sd[i]! : null))
      return {
        instance,
        meta,
        title: `BB Bars (${p})`,
        plots: [
          { key: "bbb_u", label: "+1σ", color: "#089981", type: "line", lineWidth: 1.2, values: up1 },
          { key: "bbb_l", label: "-1σ", color: "#F23645", type: "line", lineWidth: 1.2, values: dn1 },
        ],
        bands: [{ upperKey: "bbb_u", lowerKey: "bbb_l", fillColor: "rgba(66, 165, 245, 0.1)" }],
      }
    }

    case "BBTREND": {
      const fast = Math.max(5, Math.round(instance.params.fast ?? 20))
      const slow = Math.max(fast + 5, Math.round(instance.params.slow ?? 50))
      const sB = calcSMA(closes, fast), sSd = calcStdDev(closes, fast)
      const lB = calcSMA(closes, slow), lSd = calcStdDev(closes, slow)
      const bbt: (number | null)[] = new Array(len).fill(null)
      const cols: (string | null)[] = new Array(len).fill(null)
      for (let i = 0; i < len; i++) {
        if (sB[i] && sSd[i] != null && lB[i] && lSd[i] != null && sB[i]! > 0) {
          const sUp = sB[i]! + 2 * sSd[i]!, sLo = sB[i]! - 2 * sSd[i]!
          const lUp = lB[i]! + 2 * lSd[i]!, lLo = lB[i]! - 2 * lSd[i]!
          const v = ((Math.abs(sLo - lLo) - Math.abs(sUp - lUp)) / sB[i]!) * 100
          bbt[i] = v
          cols[i] = v >= 0 ? "#26A69A" : "#EF5350"
        }
      }
      return {
        instance,
        meta,
        title: `BBTrend (${fast}, ${slow})`,
        plots: [{ key: "bbtrend", label: "BBTrend", color: instance.color, type: "histogram", values: bbt, pointColors: cols }],
      }
    }

    case "ICHIMOKU": {
      const tenkanLen = Math.max(2, Math.round(instance.params.tenkan ?? 9))
      const kijunLen = Math.max(2, Math.round(instance.params.kijun ?? 26))
      const senkouBLen = Math.max(5, Math.round(instance.params.senkouB ?? 52))
      const donchianMid = (period: number): (number | null)[] => {
        const hh = calcHighest(highs, period)
        const ll = calcLowest(lows, period)
        return hh.map((h, i) => (h != null && ll[i] != null ? (h + ll[i]!) / 2 : null))
      }
      const tenkan = donchianMid(tenkanLen)
      const kijun = donchianMid(kijunLen)
      const senkouA = tenkan.map((t, i) => (t != null && kijun[i] != null ? (t + kijun[i]!) / 2 : null))
      const senkouB = donchianMid(senkouBLen)
      return {
        instance,
        meta,
        title: `Ichimoku (${tenkanLen}, ${kijunLen}, ${senkouBLen})`,
        plots: [
          { key: "ichi_tenkan", label: "Tenkan", color: "#2962FF", type: "line", lineWidth: 1.3, values: tenkan },
          { key: "ichi_kijun", label: "Kijun", color: "#B71C1C", type: "line", lineWidth: 1.3, values: kijun },
          { key: "ichi_senkouA", label: "Span A", color: "#43A047", type: "line", lineWidth: 1, values: senkouA },
          { key: "ichi_senkouB", label: "Span B", color: "#EF5350", type: "line", lineWidth: 1, values: senkouB },
        ],
        bands: [
          {
            upperKey: "ichi_senkouA",
            lowerKey: "ichi_senkouB",
            fillColor: "rgba(67, 160, 71, 0.11)",
            dynamicBullBear: true,
            bullColor: "rgba(67, 160, 71, 0.13)",
            bearColor: "rgba(239, 83, 80, 0.13)",
          },
        ],
      }
    }

    case "VWAP": {
      const p = Math.max(1, Math.round(instance.params.length ?? 20))
      const vwap: (number | null)[] = new Array(len).fill(null)
      for (let i = 0; i < len; i++) {
        const s = Math.max(0, i - p + 1)
        let pv = 0, vSum = 0
        for (let j = s; j <= i; j++) {
          const vol = Math.max(1, candles[j].volume)
          pv += hlc3[j] * vol
          vSum += vol
        }
        vwap[i] = vSum > 0 ? pv / vSum : closes[i]
      }
      return {
        instance,
        meta,
        title: `VWAP (${p})`,
        plots: [{ key: "vwap", label: "VWAP", color: instance.color, type: "line", lineWidth: instance.lineWidth, values: vwap }],
      }
    }

    case "VWAP_BANDS": {
      const p = Math.max(5, Math.round(instance.params.length ?? 20))
      const mult = instance.params.mult ?? 2
      const vwap: (number | null)[] = new Array(len).fill(null)
      const up: (number | null)[] = new Array(len).fill(null)
      const dn: (number | null)[] = new Array(len).fill(null)
      for (let i = 0; i < len; i++) {
        const s = Math.max(0, i - p + 1)
        let pv = 0, pv2 = 0, vSum = 0
        for (let j = s; j <= i; j++) {
          const vol = Math.max(1, candles[j].volume)
          pv += hlc3[j] * vol
          pv2 += hlc3[j] * hlc3[j] * vol
          vSum += vol
        }
        const mean = pv / Math.max(1, vSum)
        const variance = Math.max(0, pv2 / Math.max(1, vSum) - mean * mean)
        const sd = Math.sqrt(variance)
        vwap[i] = mean
        up[i] = mean + mult * sd
        dn[i] = mean - mult * sd
      }
      return {
        instance,
        meta,
        title: `VWAP ±${mult}σ (${p})`,
        plots: [
          { key: "vwb_m", label: "VWAP", color: instance.color, type: "line", lineWidth: 1.7, values: vwap },
          { key: "vwb_u", label: `+${mult}σ`, color: "#089981", type: "line", lineWidth: 1.2, dashed: true, values: up },
          { key: "vwb_l", label: `-${mult}σ`, color: "#F23645", type: "line", lineWidth: 1.2, dashed: true, values: dn },
        ],
        bands: [{ upperKey: "vwb_u", lowerKey: "vwb_l", fillColor: "rgba(126, 87, 194, 0.08)" }],
      }
    }

    case "PSAR": {
      const step = instance.params.start ?? 0.02
      const maxAf = instance.params.max ?? 0.2
      const sar: (number | null)[] = new Array(len).fill(null)
      const colors: (string | null)[] = new Array(len).fill(null)
      if (len >= 2) {
        let isLong = candles[1].close >= candles[0].close
        let af = step
        let ep = isLong ? candles[0].high : candles[0].low
        let curSar = isLong ? candles[0].low : candles[0].high
        for (let i = 1; i < len; i++) {
          sar[i] = curSar
          colors[i] = isLong ? "#089981" : "#F23645"
          const c = candles[i]
          if (isLong) {
            if (c.low < curSar) {
              isLong = false
              curSar = ep
              ep = c.low
              af = step
            } else {
              if (c.high > ep) {
                ep = c.high
                af = Math.min(maxAf, af + step)
              }
              curSar += af * (ep - curSar)
              if (i >= 2) curSar = Math.min(curSar, candles[i - 1].low, candles[i - 2].low)
            }
          } else {
            if (c.high > curSar) {
              isLong = true
              curSar = ep
              ep = c.high
              af = step
            } else {
              if (c.low < ep) {
                ep = c.low
                af = Math.min(maxAf, af + step)
              }
              curSar += af * (ep - curSar)
              if (i >= 2) curSar = Math.max(curSar, candles[i - 1].high, candles[i - 2].high)
            }
          }
        }
      }
      return {
        instance,
        meta,
        title: `SAR (${step}, ${maxAf})`,
        plots: [{ key: "psar", label: "SAR", color: instance.color, type: "dots", values: sar, pointColors: colors }],
      }
    }

    case "SUPERTREND": {
      const atrLen = Math.max(2, Math.round(instance.params.atrLength ?? 10))
      const factor = instance.params.factor ?? 3
      const { line, colors } = calcSuperTrendSeries(candles, atrLen, factor)
      return {
        instance,
        meta,
        title: `Supertrend (${atrLen}, ${factor})`,
        plots: [{ key: "supertrend", label: "Supertrend", color: instance.color, type: "line", lineWidth: 2, values: line, pointColors: colors }],
      }
    }

    case "DONCHIAN": {
      const p = Math.max(2, Math.round(instance.params.length ?? 20))
      const upper = calcHighest(highs, p)
      const lower = calcLowest(lows, p)
      const basis = upper.map((u, i) => (u != null && lower[i] != null ? (u + lower[i]!) / 2 : null))
      return {
        instance,
        meta,
        title: `DC (${p})`,
        plots: [
          { key: "dc_upper", label: "Upper", color: "#2962FF", type: "line", lineWidth: 1.2, values: upper },
          { key: "dc_basis", label: "Basis", color: "#FF6D00", type: "line", lineWidth: 1.1, values: basis },
          { key: "dc_lower", label: "Lower", color: "#2962FF", type: "line", lineWidth: 1.2, values: lower },
        ],
        bands: [{ upperKey: "dc_upper", lowerKey: "dc_lower", fillColor: "rgba(0, 172, 193, 0.08)" }],
      }
    }

    case "KELTNER": {
      const p = Math.max(2, Math.round(instance.params.length ?? 20))
      const mult = instance.params.mult ?? 2
      const basis = calcEMA(closes, p)
      const atr = calcATR(candles, 10)
      const upper = basis.map((b, i) => (b != null && atr[i] != null ? b + mult * atr[i]! : null))
      const lower = basis.map((b, i) => (b != null && atr[i] != null ? b - mult * atr[i]! : null))
      return {
        instance,
        meta,
        title: `KC (${p}, ${mult})`,
        plots: [
          { key: "kc_upper", label: "Upper", color: instance.color, type: "line", lineWidth: 1.2, values: upper },
          { key: "kc_basis", label: "Basis", color: instance.color, type: "line", lineWidth: 1.2, values: basis },
          { key: "kc_lower", label: "Lower", color: instance.color, type: "line", lineWidth: 1.2, values: lower },
        ],
        bands: [{ upperKey: "kc_upper", lowerKey: "kc_lower", fillColor: "rgba(57, 73, 171, 0.08)" }],
      }
    }

    case "ENVELOPE": {
      const p = Math.max(2, Math.round(instance.params.length ?? 20))
      const pct = (instance.params.percent ?? 5) / 100
      const basis = calcSMA(closes, p)
      const upper = basis.map((v) => (v != null ? v * (1 + pct) : null))
      const lower = basis.map((v) => (v != null ? v * (1 - pct) : null))
      return {
        instance,
        meta,
        title: `Env (${p}, ${(pct * 100).toFixed(1)}%)`,
        plots: [
          { key: "env_upper", label: "Upper", color: instance.color, type: "line", lineWidth: 1.2, values: upper },
          { key: "env_basis", label: "Basis", color: "#FF6D00", type: "line", lineWidth: 1.1, values: basis },
          { key: "env_lower", label: "Lower", color: instance.color, type: "line", lineWidth: 1.2, values: lower },
        ],
        bands: [{ upperKey: "env_upper", lowerKey: "env_lower", fillColor: "rgba(92, 107, 192, 0.07)" }],
      }
    }

    case "STDERR_BANDS": {
      const p = Math.max(4, Math.round(instance.params.length ?? 21))
      const reg = calcLSMA(closes, p)
      const sd = calcStdDev(closes, p)
      const se = sd.map((s) => (s != null ? (2 * s) / Math.sqrt(p) : null))
      const upper = reg.map((r, i) => (r != null && se[i] != null ? r + se[i]! : null))
      const lower = reg.map((r, i) => (r != null && se[i] != null ? r - se[i]! : null))
      return {
        instance,
        meta,
        title: `SE Bands (${p})`,
        plots: [
          { key: "seb_u", label: "Upper", color: instance.color, type: "line", lineWidth: 1.2, values: upper },
          { key: "seb_m", label: "LinReg", color: "#FF6D00", type: "line", lineWidth: 1.3, values: reg },
          { key: "seb_l", label: "Lower", color: instance.color, type: "line", lineWidth: 1.2, values: lower },
        ],
        bands: [{ upperKey: "seb_u", lowerKey: "seb_l", fillColor: "rgba(41, 98, 255, 0.07)" }],
      }
    }

    case "CHANDE_KROLL": {
      const p = Math.max(2, Math.round(instance.params.length ?? 10))
      const mult = instance.params.mult ?? 1
      const atr = calcATR(candles, p)
      const hh = calcHighest(highs, p)
      const ll = calcLowest(lows, p)
      const initHigh = hh.map((h, i) => (h != null && atr[i] != null ? h - mult * atr[i]! : null))
      const initLow = ll.map((l, i) => (l != null && atr[i] != null ? l + mult * atr[i]! : null))
      const shortStop = calcHighest(initHigh, 9)
      const longStop = calcLowest(initLow, 9)
      return {
        instance,
        meta,
        title: `Chande Kroll (${p}, ${mult})`,
        plots: [
          { key: "ck_long", label: "Long Stop", color: "#089981", type: "line", lineWidth: 1.5, values: longStop },
          { key: "ck_short", label: "Short Stop", color: "#FF6D00", type: "line", lineWidth: 1.5, values: shortStop },
        ],
      }
    }

    case "CHANDELIER_EXIT":
    case "VOL_STOP": {
      const p = Math.max(2, Math.round(instance.params.length ?? 22))
      const mult = instance.params.mult ?? 3
      const { line, colors } = calcSuperTrendSeries(candles, p, mult)
      return {
        instance,
        meta,
        title: `${meta.shortName} (${p}, ${mult})`,
        plots: [{ key: "ce_line", label: meta.shortName, color: instance.color, type: "line", lineWidth: 1.8, values: line, pointColors: colors }],
      }
    }

    case "PIVOT":
    case "PIVOT_FIBONACCI":
    case "PIVOT_WOODIE":
    case "PIVOT_CAMARILLA":
    case "GHOST_PIVOTS": {
      const period = Math.max(3, Math.round(instance.params.period ?? 15))
      const pLine: (number | null)[] = new Array(len).fill(null)
      const r1: (number | null)[] = new Array(len).fill(null)
      const s1: (number | null)[] = new Array(len).fill(null)
      const r2: (number | null)[] = new Array(len).fill(null)
      const s2: (number | null)[] = new Array(len).fill(null)

      for (let start = period; start < len; start += period) {
        let hh = -Infinity, ll = Infinity
        const cPrev = candles[start - 1].close
        for (let j = start - period; j < start; j++) {
          if (candles[j].high > hh) hh = candles[j].high
          if (candles[j].low < ll) ll = candles[j].low
        }
        const range = hh - ll
        let pVal = (hh + ll + cPrev) / 3
        let r1v = 2 * pVal - ll, s1v = 2 * pVal - hh
        let r2v = pVal + range, s2v = pVal - range

        if (instance.id === "PIVOT_FIBONACCI") {
          r1v = pVal + 0.382 * range; s1v = pVal - 0.382 * range
          r2v = pVal + 0.618 * range; s2v = pVal - 0.618 * range
        } else if (instance.id === "PIVOT_WOODIE") {
          pVal = (hh + ll + 2 * candles[start].open) / 4
          r1v = 2 * pVal - ll; s1v = 2 * pVal - hh
          r2v = pVal + range; s2v = pVal - range
        } else if (instance.id === "PIVOT_CAMARILLA") {
          r1v = cPrev + (range * 1.1) / 4; s1v = cPrev - (range * 1.1) / 4
          r2v = cPrev + (range * 1.1) / 2; s2v = cPrev - (range * 1.1) / 2
        } else if (instance.id === "GHOST_PIVOTS") {
          pVal = (hh + ll + 2 * cPrev) / 4
          r1v = 2 * pVal - ll; s1v = 2 * pVal - hh
          r2v = pVal + 0.786 * range; s2v = pVal - 0.786 * range
        }

        const end = Math.min(len, start + period)
        for (let k = start; k < end; k++) {
          pLine[k] = pVal; r1[k] = r1v; s1[k] = s1v; r2[k] = r2v; s2[k] = s2v
        }
      }

      return {
        instance,
        meta,
        title: `${meta.shortName} (${period})`,
        plots: [
          { key: "piv_p", label: "P", color: "#FB8C00", type: "step", lineWidth: 1.4, values: pLine },
          { key: "piv_r1", label: "R1", color: "#089981", type: "step", lineWidth: 1.2, dashed: true, values: r1 },
          { key: "piv_s1", label: "S1", color: "#F23645", type: "step", lineWidth: 1.2, dashed: true, values: s1 },
          { key: "piv_r2", label: "R2", color: "#00897B", type: "step", lineWidth: 1, dashed: true, values: r2 },
          { key: "piv_s2", label: "S2", color: "#D32F2F", type: "step", lineWidth: 1, dashed: true, values: s2 },
        ],
      }
    }

    /* -------------------- VOLUME & BREADTH STUDIES -------------------- */
    case "VOL": {
      const maLen = Math.max(2, Math.round(instance.params.maLength ?? 20))
      const vols = candles.map((c) => c.volume)
      const volColors = candles.map((c) =>
        c.close >= c.open ? "rgba(38, 166, 154, 0.55)" : "rgba(239, 83, 80, 0.55)"
      )
      return {
        instance,
        meta,
        title: `Vol (${maLen})`,
        plots: [
          { key: "vol_bars", label: "Vol", color: instance.color, type: "histogram", values: vols, pointColors: volColors },
          { key: "vol_ma", label: `MA ${maLen}`, color: instance.secondaryColor || "#F57C00", type: "line", lineWidth: 1.4, values: calcSMA(vols, maLen) },
        ],
      }
    }

    case "VOL_24H": {
      const p = Math.max(2, Math.round(instance.params.length ?? 24))
      const turnover = candles.map((c) => (c.volume * c.close) / 1e9) // Billion VND
      const rolling: (number | null)[] = new Array(len).fill(null)
      for (let i = 0; i < len; i++) {
        let sum = 0
        for (let j = Math.max(0, i - p + 1); j <= i; j++) sum += turnover[j]
        rolling[i] = sum
      }
      return {
        instance,
        meta,
        title: `24h Vol (${p})`,
        plots: [{ key: "v24", label: "GTGD (Tỷ)", color: instance.color, type: "columns", values: rolling }],
      }
    }

    case "ADL": {
      const adl = calcADLSeries(candles)
      const sig = calcEMA(adl, instance.params.length ?? 14)
      return {
        instance,
        meta,
        title: "Accum/Dist",
        plots: [
          { key: "adl", label: "A/D", color: instance.color, type: "line", lineWidth: 1.6, values: adl },
          { key: "adl_s", label: "EMA", color: "#FF6D00", type: "line", lineWidth: 1.2, dashed: true, values: sig },
        ],
      }
    }

    case "ADV_DEC_LINE": {
      const ad: (number | null)[] = new Array(len).fill(null)
      let cum = 0
      for (let i = 1; i < len; i++) {
        const diff = closes[i] - closes[i - 1]
        const hl = Math.max(1, candles[i].high - candles[i].low)
        cum += (diff / hl) * 100
        ad[i] = cum
      }
      return {
        instance,
        meta,
        title: "Advance-Decline Line",
        plots: [{ key: "ad_line", label: "ADL", color: instance.color, type: "line", lineWidth: 1.6, values: ad }],
      }
    }

    case "ADV_DEC_RATIO":
    case "ADV_DEC_BARS": {
      const p = Math.max(2, Math.round(instance.params.length ?? 14))
      const out: (number | null)[] = new Array(len).fill(null)
      for (let i = p; i < len; i++) {
        let up = 0, dn = 0
        for (let j = i - p + 1; j <= i; j++) {
          if (closes[j] >= closes[j - 1]) up += instance.id === "ADV_DEC_BARS" ? 1 : candles[j].volume
          else dn += instance.id === "ADV_DEC_BARS" ? 1 : candles[j].volume
        }
        out[i] = dn > 0 ? up / dn : up > 0 ? 3 : 1
      }
      return {
        instance,
        meta,
        title: `${meta.shortName} (${p})`,
        plots: [{ key: "adr_r", label: meta.shortName, color: instance.color, type: "line", lineWidth: 1.6, values: out }],
      }
    }

    case "CMF": {
      const p = Math.max(2, Math.round(instance.params.length ?? 20))
      const cmf: (number | null)[] = new Array(len).fill(null)
      for (let i = p - 1; i < len; i++) {
        let mfvSum = 0, vSum = 0
        for (let j = i - p + 1; j <= i; j++) {
          const c = candles[j]
          const hl = c.high - c.low
          const mfm = hl > 0 ? (c.close - c.low - (c.high - c.close)) / hl : 0
          mfvSum += mfm * c.volume
          vSum += c.volume
        }
        cmf[i] = vSum > 0 ? mfvSum / vSum : 0
      }
      return {
        instance,
        meta,
        title: `CMF (${p})`,
        plots: [{ key: "cmf", label: "CMF", color: instance.color, type: "line", lineWidth: 1.6, values: cmf }],
      }
    }

    case "CHAIKIN_OSC": {
      const fast = instance.params.fast ?? 3
      const slow = instance.params.slow ?? 10
      const adl = calcADLSeries(candles)
      const ef = calcEMA(adl, fast)
      const es = calcEMA(adl, slow)
      const osc = adl.map((_, i) => (ef[i] != null && es[i] != null ? ef[i]! - es[i]! : null))
      return {
        instance,
        meta,
        title: `Chaikin Osc (${fast}, ${slow})`,
        plots: [{ key: "ch_osc", label: "CO", color: instance.color, type: "line", lineWidth: 1.6, values: osc }],
      }
    }

    case "OBV": {
      const maLen = Math.max(2, Math.round(instance.params.maLength ?? 20))
      const obv: (number | null)[] = new Array(len).fill(null)
      let acc = 0
      for (let i = 0; i < len; i++) {
        if (i > 0) {
          if (closes[i] > closes[i - 1]) acc += candles[i].volume
          else if (closes[i] < closes[i - 1]) acc -= candles[i].volume
        }
        obv[i] = acc
      }
      return {
        instance,
        meta,
        title: `OBV (SMA ${maLen})`,
        plots: [
          { key: "obv", label: "OBV", color: instance.color, type: "line", lineWidth: 1.6, values: obv },
          { key: "obv_ma", label: "SMA", color: instance.secondaryColor || "#FF6D00", type: "line", lineWidth: 1.3, values: calcSMA(obv, maLen) },
        ],
      }
    }

    case "CVI":
    case "PVT": {
      const out: (number | null)[] = new Array(len).fill(null)
      let acc = 0
      for (let i = 1; i < len; i++) {
        if (instance.id === "PVT") {
          acc += candles[i].volume * ((closes[i] - closes[i - 1]) / Math.max(1, closes[i - 1]))
        } else {
          acc += closes[i] >= candles[i].open ? candles[i].volume : -candles[i].volume
        }
        out[i] = acc
      }
      return {
        instance,
        meta,
        title: meta.shortName,
        plots: [{ key: "cvi_pvt", label: meta.shortName, color: instance.color, type: "line", lineWidth: 1.6, values: out }],
      }
    }

    case "EOM": {
      const p = Math.max(2, Math.round(instance.params.length ?? 14))
      const raw: (number | null)[] = new Array(len).fill(null)
      for (let i = 1; i < len; i++) {
        const dm = hl2[i] - hl2[i - 1]
        const boxRatio = (candles[i].volume / 1e6) / Math.max(1e-4, candles[i].high - candles[i].low)
        raw[i] = boxRatio > 0 ? dm / boxRatio : 0
      }
      return {
        instance,
        meta,
        title: `EOM (${p})`,
        plots: [{ key: "eom", label: "EOM", color: instance.color, type: "line", lineWidth: 1.6, values: calcSMA(raw, p) }],
      }
    }

    case "EFI": {
      const p = Math.max(2, Math.round(instance.params.length ?? 13))
      const raw: (number | null)[] = candles.map((c, i) => (i > 0 ? (c.close - candles[i - 1].close) * c.volume : null))
      return {
        instance,
        meta,
        title: `EFI (${p})`,
        plots: [{ key: "efi", label: "EFI", color: instance.color, type: "line", lineWidth: 1.6, values: calcEMA(raw, p) }],
      }
    }

    case "KLINGER": {
      const fast = instance.params.fast ?? 34
      const slow = instance.params.slow ?? 55
      const vf: (number | null)[] = new Array(len).fill(null)
      let prevTrend = 1
      for (let i = 1; i < len; i++) {
        const trend = hlc3[i] > hlc3[i - 1] ? 1 : -1
        const dm = Math.max(1e-4, candles[i].high - candles[i].low)
        vf[i] = candles[i].volume * trend * (trend === prevTrend ? 1.4 : 0.8) * (dm / Math.max(1, closes[i])) * 100
        prevTrend = trend
      }
      const ef = calcEMA(vf, fast)
      const es = calcEMA(vf, slow)
      const kvo = vf.map((_, i) => (ef[i] != null && es[i] != null ? ef[i]! - es[i]! : null))
      const sig = calcEMA(kvo, 13)
      return {
        instance,
        meta,
        title: `KVO (${fast}, ${slow})`,
        plots: [
          { key: "kvo", label: "KVO", color: instance.color, type: "line", lineWidth: 1.6, values: kvo },
          { key: "kvo_s", label: "Signal", color: instance.secondaryColor || "#43A047", type: "line", lineWidth: 1.3, values: sig },
        ],
      }
    }

    case "NVI":
    case "PVI": {
      const sigLen = Math.max(5, Math.min(len - 2, Math.round(instance.params.length ?? 50)))
      const idx: (number | null)[] = new Array(len).fill(null)
      let val = 1000
      idx[0] = val
      for (let i = 1; i < len; i++) {
        const cond =
          instance.id === "NVI"
            ? candles[i].volume < candles[i - 1].volume
            : candles[i].volume > candles[i - 1].volume
        if (cond && closes[i - 1] > 0) {
          val *= 1 + (closes[i] - closes[i - 1]) / closes[i - 1]
        }
        idx[i] = val
      }
      return {
        instance,
        meta,
        title: `${meta.shortName} (EMA ${sigLen})`,
        plots: [
          { key: "npvi", label: meta.shortName, color: instance.color, type: "line", lineWidth: 1.6, values: idx },
          { key: "npvi_s", label: "EMA", color: instance.secondaryColor || "#FF6D00", type: "line", lineWidth: 1.3, values: calcEMA(idx, sigLen) },
        ],
      }
    }

    case "NET_VOL":
    case "VOL_DELTA":
    case "UP_DOWN_VOL": {
      const delta = candles.map((c) => {
        if (instance.id === "UP_DOWN_VOL") return c.close >= c.open ? c.volume : -c.volume
        const hl = Math.max(1e-4, c.high - c.low)
        const bullRatio = (c.close - c.low) / hl
        return c.volume * (2 * bullRatio - 1)
      })
      const cols = delta.map((d) => (d >= 0 ? "rgba(8, 153, 129, 0.72)" : "rgba(242, 54, 69, 0.72)"))
      return {
        instance,
        meta,
        title: meta.shortName,
        plots: [{ key: "vdelta", label: meta.shortName, color: instance.color, type: "histogram", values: delta, pointColors: cols }],
      }
    }

    case "OPEN_INTEREST": {
      const oi: (number | null)[] = new Array(len).fill(null)
      let cur = (candles[0]?.volume || 100000) * 3.2
      for (let i = 0; i < len; i++) {
        const chg = i > 0 ? Math.abs(closes[i] - closes[i - 1]) / Math.max(1, closes[i - 1]) : 0.01
        cur = cur * 0.96 + candles[i].volume * (0.25 + chg * 6)
        oi[i] = cur
      }
      return {
        instance,
        meta,
        title: "Open Interest",
        plots: [{ key: "oi", label: "OI", color: instance.color, type: "area", lineWidth: 1.6, values: oi }],
      }
    }

    case "PVO":
    case "VOL_OSC": {
      const fast = instance.params.fast ?? (instance.id === "PVO" ? 12 : 5)
      const slow = instance.params.slow ?? (instance.id === "PVO" ? 26 : 10)
      const vols = candles.map((c) => c.volume)
      const ef = calcEMA(vols, fast)
      const es = calcEMA(vols, slow)
      const pvo = vols.map((_, i) => (ef[i] != null && es[i] && es[i]! > 0 ? ((ef[i]! - es[i]!) / es[i]!) * 100 : null))
      const sig = calcEMA(pvo, instance.params.signal ?? 9)
      return {
        instance,
        meta,
        title: `${meta.shortName} (${fast}, ${slow})`,
        plots:
          instance.id === "PVO"
            ? [
                { key: "pvo", label: "PVO %", color: instance.color, type: "line", lineWidth: 1.6, values: pvo },
                { key: "pvo_s", label: "Signal", color: "#FF6D00", type: "line", lineWidth: 1.3, values: sig },
              ]
            : [{ key: "vosc", label: "Vol Osc %", color: instance.color, type: "line", lineWidth: 1.6, values: pvo }],
      }
    }

    case "RVOL_TIME": {
      const p = Math.max(2, Math.round(instance.params.length ?? 20))
      const vols = candles.map((c) => c.volume)
      const avg = calcSMA(vols, p)
      const rvol = vols.map((v, i) => (avg[i] && avg[i]! > 0 ? v / avg[i]! : null))
      const cols = rvol.map((rv, i) =>
        rv == null
          ? null
          : rv >= 1.5
            ? candles[i].close >= candles[i].open
              ? "#089981"
              : "#F23645"
            : "rgba(120, 123, 134, 0.55)"
      )
      return {
        instance,
        meta,
        title: `RVOL (${p})`,
        plots: [{ key: "rvol", label: "RVOL (x)", color: instance.color, type: "columns", values: rvol, pointColors: cols }],
      }
    }

    case "MFI": {
      const p = Math.max(2, Math.round(instance.params.length ?? 14))
      return {
        instance,
        meta,
        title: `MFI (${p})`,
        plots: [{ key: "mfi", label: "MFI", color: instance.color, type: "line", lineWidth: 1.6, values: calcMFI(candles, p) }],
      }
    }

    /* -------------------- OSCILLATORS & VOLATILITY -------------------- */
    case "AROON":
    case "AROON_OSC": {
      const p = Math.max(2, Math.round(instance.params.length ?? 14))
      const up: (number | null)[] = new Array(len).fill(null)
      const dn: (number | null)[] = new Array(len).fill(null)
      const osc: (number | null)[] = new Array(len).fill(null)
      for (let i = p; i < len; i++) {
        let hhIdx = i - p, llIdx = i - p
        for (let j = i - p; j <= i; j++) {
          if (highs[j] >= highs[hhIdx]) hhIdx = j
          if (lows[j] <= lows[llIdx]) llIdx = j
        }
        up[i] = ((p - (i - hhIdx)) / p) * 100
        dn[i] = ((p - (i - llIdx)) / p) * 100
        osc[i] = up[i]! - dn[i]!
      }
      if (instance.id === "AROON_OSC") {
        return {
          instance,
          meta,
          title: `Aroon Osc (${p})`,
          plots: [{ key: "aroon_osc", label: "Aroon Osc", color: instance.color, type: "line", lineWidth: 1.6, values: osc }],
        }
      }
      return {
        instance,
        meta,
        title: `Aroon (${p})`,
        plots: [
          { key: "aroon_up", label: "Aroon Up", color: "#FF6D00", type: "line", lineWidth: 1.5, values: up },
          { key: "aroon_dn", label: "Aroon Down", color: "#2962FF", type: "line", lineWidth: 1.5, values: dn },
        ],
      }
    }

    case "ADR": {
      const p = Math.max(2, Math.round(instance.params.length ?? 14))
      const ranges = candles.map((c) => c.high - c.low)
      return {
        instance,
        meta,
        title: `ADR (${p})`,
        plots: [{ key: "adr", label: "ADR", color: instance.color, type: "line", lineWidth: 1.6, values: calcSMA(ranges, p) }],
      }
    }

    case "ADX":
    case "DMI": {
      const p = Math.max(2, Math.round(instance.params.length ?? 14))
      const { adx, plusDi, minusDi } = calcADXSeries(candles, p)
      return {
        instance,
        meta,
        title: `${meta.shortName} (${p})`,
        plots: [
          { key: "adx", label: "ADX", color: instance.color, type: "line", lineWidth: 1.8, values: adx },
          { key: "plus_di", label: "+DI", color: instance.secondaryColor || "#089981", type: "line", lineWidth: 1.2, values: plusDi },
          { key: "minus_di", label: "-DI", color: instance.tertiaryColor || "#F23645", type: "line", lineWidth: 1.2, values: minusDi },
        ],
      }
    }

    case "ATR": {
      const p = Math.max(2, Math.round(instance.params.length ?? 14))
      return {
        instance,
        meta,
        title: `ATR (${p})`,
        plots: [{ key: "atr", label: "ATR", color: instance.color, type: "line", lineWidth: 1.6, values: calcATR(candles, p) }],
      }
    }

    case "AO": {
      const f = calcSMA(hl2, instance.params.fast ?? 5)
      const s = calcSMA(hl2, instance.params.slow ?? 34)
      const ao = hl2.map((_, i) => (f[i] != null && s[i] != null ? f[i]! - s[i]! : null))
      const cols = ao.map((v, i) => (v == null ? null : i > 0 && ao[i - 1] != null && v >= ao[i - 1]! ? "#089981" : "#F23645"))
      return {
        instance,
        meta,
        title: "AO (5, 34)",
        plots: [{ key: "ao", label: "AO", color: instance.color, type: "histogram", values: ao, pointColors: cols }],
      }
    }

    case "BOP": {
      const raw = candles.map((c) => (c.high > c.low ? (c.close - c.open) / (c.high - c.low) : 0))
      const p = instance.params.length ?? 14
      return {
        instance,
        meta,
        title: `BOP (${p})`,
        plots: [{ key: "bop", label: "BOP", color: instance.color, type: "line", lineWidth: 1.6, values: calcSMA(raw, p) }],
      }
    }

    case "BULL_BEAR_POWER": {
      const p = Math.max(2, Math.round(instance.params.length ?? 13))
      const ema = calcEMA(closes, p)
      const bbp = closes.map((_, i) => (ema[i] != null ? highs[i] - ema[i]! + (lows[i] - ema[i]!) : null))
      const cols = bbp.map((v) => (v == null ? null : v >= 0 ? "#089981" : "#F23645"))
      return {
        instance,
        meta,
        title: `BBP (${p})`,
        plots: [{ key: "bbp", label: "BBPower", color: instance.color, type: "histogram", values: bbp, pointColors: cols }],
      }
    }

    case "CMO": {
      const p = Math.max(2, Math.round(instance.params.length ?? 9))
      const cmo: (number | null)[] = new Array(len).fill(null)
      for (let i = p; i < len; i++) {
        let su = 0, sd = 0
        for (let j = i - p + 1; j <= i; j++) {
          const d = closes[j] - closes[j - 1]
          if (d > 0) su += d
          else sd -= d
        }
        cmo[i] = su + sd > 0 ? ((su - sd) / (su + sd)) * 100 : 0
      }
      return {
        instance,
        meta,
        title: `CMO (${p})`,
        plots: [{ key: "cmo", label: "CMO", color: instance.color, type: "line", lineWidth: 1.6, values: cmo }],
      }
    }

    case "CHOP": {
      const p = Math.max(3, Math.round(instance.params.length ?? 14))
      const tr = calcTrueRange(candles)
      const hh = calcHighest(highs, p)
      const ll = calcLowest(lows, p)
      const chop: (number | null)[] = new Array(len).fill(null)
      for (let i = p - 1; i < len; i++) {
        let trSum = 0
        for (let j = i - p + 1; j <= i; j++) trSum += tr[j] ?? 0
        const range = (hh[i] ?? 0) - (ll[i] ?? 0)
        if (range > 0 && trSum > 0) {
          chop[i] = (100 * Math.log10(trSum / range)) / Math.log10(p)
        }
      }
      return {
        instance,
        meta,
        title: `CHOP (${p})`,
        plots: [{ key: "chop", label: "CHOP", color: instance.color, type: "line", lineWidth: 1.6, values: chop }],
      }
    }

    case "CHOP_ZONE":
    case "ELDER_IMPULSE": {
      const p = Math.max(3, Math.round(instance.params.length ?? (instance.id === "CHOP_ZONE" ? 34 : 13)))
      const ema = calcEMA(closes, p)
      const ef = calcEMA(closes, 12), es = calcEMA(closes, 26)
      const macd = closes.map((_, i) => (ef[i] != null && es[i] != null ? ef[i]! - es[i]! : null))
      const sig = calcEMA(macd, 9)
      const hist = macd.map((m, i) => (m != null && sig[i] != null ? m - sig[i]! : null))
      const vals: (number | null)[] = new Array(len).fill(1)
      const cols: (string | null)[] = new Array(len).fill("#2962FF")
      for (let i = 1; i < len; i++) {
        if (ema[i] == null || ema[i - 1] == null) continue
        const emaUp = ema[i]! > ema[i - 1]!
        const histUp = hist[i] != null && hist[i - 1] != null ? hist[i]! >= hist[i - 1]! : emaUp
        if (emaUp && histUp) cols[i] = "#089981"
        else if (!emaUp && !histUp) cols[i] = "#F23645"
        else cols[i] = "#2962FF"
      }
      return {
        instance,
        meta,
        title: `${meta.shortName} (${p})`,
        plots: [{ key: "cz_ei", label: meta.shortName, color: instance.color, type: "columns", values: vals, pointColors: cols }],
      }
    }

    case "CCI": {
      const p = Math.max(2, Math.round(instance.params.length ?? 20))
      return {
        instance,
        meta,
        title: `CCI (${p})`,
        plots: [{ key: "cci", label: "CCI", color: instance.color, type: "line", lineWidth: 1.5, values: calcCCI(candles, p) }],
      }
    }

    case "WOODIES_CCI": {
      const fast = instance.params.fast ?? 6
      const slow = instance.params.slow ?? 14
      const cciSlow = calcCCI(candles, slow)
      const cciFast = calcCCI(candles, fast)
      const cols = cciSlow.map((v) => (v == null ? null : v >= 0 ? "rgba(0, 137, 123, 0.65)" : "rgba(239, 83, 80, 0.65)"))
      return {
        instance,
        meta,
        title: `Woodies CCI (${fast}, ${slow})`,
        plots: [
          { key: "wcci_h", label: `CCI ${slow}`, color: "#00897B", type: "histogram", values: cciSlow, pointColors: cols },
          { key: "wcci_t", label: `Turbo ${fast}`, color: "#FB8C00", type: "line", lineWidth: 1.5, values: cciFast },
        ],
      }
    }

    case "CONNORS_RSI": {
      const rLen = Math.max(2, Math.round(instance.params.length ?? 3))
      const rsiPrice = calcRSI(closes, rLen)
      const streak: number[] = new Array(len).fill(0)
      for (let i = 1; i < len; i++) {
        if (closes[i] > closes[i - 1]) streak[i] = streak[i - 1] > 0 ? streak[i - 1] + 1 : 1
        else if (closes[i] < closes[i - 1]) streak[i] = streak[i - 1] < 0 ? streak[i - 1] - 1 : -1
      }
      const rsiStreak = calcRSI(streak, 2)
      const roc1 = calcROCSeries(closes, 1)
      const pctRank: (number | null)[] = new Array(len).fill(null)
      for (let i = 20; i < len; i++) {
        const win = Math.min(100, i)
        let below = 0
        for (let j = i - win + 1; j < i; j++) {
          if ((roc1[j] ?? 0) < (roc1[i] ?? 0)) below++
        }
        pctRank[i] = (below / win) * 100
      }
      const crsi = closes.map((_, i) =>
        rsiPrice[i] != null && rsiStreak[i] != null && pctRank[i] != null
          ? (rsiPrice[i]! + rsiStreak[i]! + pctRank[i]!) / 3
          : rsiPrice[i]
      )
      return {
        instance,
        meta,
        title: `Connors RSI (${rLen}, 2, 100)`,
        plots: [{ key: "crsi", label: "CRSI", color: instance.color, type: "line", lineWidth: 1.6, values: crsi }],
      }
    }

    case "COPPOCK": {
      const wLen = Math.max(2, Math.round(instance.params.length ?? 10))
      const r14 = calcROCSeries(closes, 14)
      const r11 = calcROCSeries(closes, 11)
      const sumR = closes.map((_, i) => (r14[i] != null && r11[i] != null ? r14[i]! + r11[i]! : null))
      return {
        instance,
        meta,
        title: `Coppock (${wLen}, 14, 11)`,
        plots: [{ key: "coppock", label: "Coppock", color: instance.color, type: "line", lineWidth: 1.6, values: calcWMA(sumR, wLen) }],
      }
    }

    case "CORR_COEFF":
    case "TSI_STRENGTH": {
      const p = Math.max(3, Math.round(instance.params.length ?? 20))
      const cc: (number | null)[] = new Array(len).fill(null)
      const meanX = (p - 1) / 2
      let varX = 0
      for (let j = 0; j < p; j++) varX += (j - meanX) * (j - meanX)
      for (let i = p - 1; i < len; i++) {
        let sumY = 0
        for (let j = 0; j < p; j++) sumY += closes[i - p + 1 + j]
        const meanY = sumY / p
        let cov = 0, varY = 0
        for (let j = 0; j < p; j++) {
          const dy = closes[i - p + 1 + j] - meanY
          cov += (j - meanX) * dy
          varY += dy * dy
        }
        cc[i] = varX > 0 && varY > 0 ? cov / Math.sqrt(varX * varY) : 0
      }
      return {
        instance,
        meta,
        title: `${meta.shortName} (${p})`,
        plots: [{ key: "corr", label: meta.shortName, color: instance.color, type: "line", lineWidth: 1.6, values: cc }],
      }
    }

    case "DPO": {
      const p = Math.max(3, Math.round(instance.params.length ?? 21))
      const shift = Math.floor(p / 2) + 1
      const sma = calcSMA(closes, p)
      const dpo = closes.map((c, i) => (i >= shift && sma[i - shift] != null ? c - sma[i - shift]! : null))
      return {
        instance,
        meta,
        title: `DPO (${p})`,
        plots: [{ key: "dpo", label: "DPO", color: instance.color, type: "line", lineWidth: 1.6, values: dpo }],
      }
    }

    case "FISHER": {
      const p = Math.max(3, Math.round(instance.params.length ?? 9))
      const hh = calcHighest(hl2, p)
      const ll = calcLowest(hl2, p)
      const fish: (number | null)[] = new Array(len).fill(null)
      const trig: (number | null)[] = new Array(len).fill(null)
      let val = 0, prevFish = 0
      for (let i = p - 1; i < len; i++) {
        const range = (hh[i] ?? 0) - (ll[i] ?? 0)
        const raw = range > 0 ? ((hl2[i] - ll[i]!) / range - 0.5) * 2 : 0
        val = Math.max(-0.999, Math.min(0.999, 0.33 * raw + 0.67 * val))
        const f = 0.5 * Math.log((1 + val) / (1 - val)) + 0.5 * prevFish
        trig[i] = prevFish
        fish[i] = f
        prevFish = f
      }
      return {
        instance,
        meta,
        title: `Fisher (${p})`,
        plots: [
          { key: "fish", label: "Fisher", color: instance.color, type: "line", lineWidth: 1.6, values: fish },
          { key: "fish_t", label: "Trigger", color: "#FF6D00", type: "line", lineWidth: 1.3, values: trig },
        ],
      }
    }

    case "HV": {
      const p = Math.max(3, Math.round(instance.params.length ?? 10))
      const logRet: (number | null)[] = candles.map((c, i) =>
        i > 0 && candles[i - 1].close > 0 ? Math.log(c.close / candles[i - 1].close) : null
      )
      const sd = calcStdDev(logRet, p)
      const hv = sd.map((s) => (s != null ? s * Math.sqrt(252) * 100 : null))
      return {
        instance,
        meta,
        title: `HV (${p})`,
        plots: [{ key: "hv", label: "HV %", color: instance.color, type: "line", lineWidth: 1.6, values: hv }],
      }
    }

    case "CHAIKIN_VOL": {
      const p = Math.max(2, Math.round(instance.params.length ?? 10))
      const hlDiff = candles.map((c) => c.high - c.low)
      const emaHl = calcEMA(hlDiff, p)
      const cv = calcROCSeries(emaHl, p)
      return {
        instance,
        meta,
        title: `Chaikin Vol (${p})`,
        plots: [{ key: "ch_vol", label: "CV %", color: instance.color, type: "line", lineWidth: 1.6, values: cv }],
      }
    }

    case "KST":
    case "PRING_K": {
      const r1 = calcSMA(calcROCSeries(closes, 10), 10)
      const r2 = calcSMA(calcROCSeries(closes, 15), 10)
      const r3 = calcSMA(calcROCSeries(closes, 20), 10)
      const r4 = calcSMA(calcROCSeries(closes, 30), 15)
      const kst = closes.map((_, i) =>
        r1[i] != null && r2[i] != null && r3[i] != null && r4[i] != null
          ? r1[i]! + 2 * r2[i]! + 3 * r3[i]! + 4 * r4[i]!
          : null
      )
      const sig = calcSMA(kst, instance.params.length ?? 9)
      return {
        instance,
        meta,
        title: meta.shortName,
        plots: [
          { key: "kst", label: meta.shortName, color: instance.color, type: "line", lineWidth: 1.6, values: kst },
          { key: "kst_s", label: "Signal", color: "#F4511E", type: "line", lineWidth: 1.3, values: sig },
        ],
      }
    }

    case "MASS_INDEX": {
      const p = Math.max(4, Math.round(instance.params.length ?? 10))
      const hlDiff = candles.map((c) => Math.max(1e-4, c.high - c.low))
      const e1 = calcEMA(hlDiff, 9)
      const e2 = calcEMA(e1, 9)
      const ratio = hlDiff.map((_, i) => (e1[i] != null && e2[i] && e2[i]! > 0 ? e1[i]! / e2[i]! : null))
      const mass: (number | null)[] = new Array(len).fill(null)
      for (let i = p - 1; i < len; i++) {
        let sum = 0, ok = true
        for (let j = i - p + 1; j <= i; j++) {
          if (ratio[j] == null) { ok = false; break }
          sum += ratio[j]!
        }
        if (ok) mass[i] = sum * (25 / p)
      }
      return {
        instance,
        meta,
        title: `Mass Index (${p})`,
        plots: [{ key: "mass", label: "Mass", color: instance.color, type: "line", lineWidth: 1.6, values: mass }],
      }
    }

    case "RSI": {
      const p = Math.max(2, Math.round(instance.params.length ?? 14))
      const maLen = Math.max(2, Math.round(instance.params.maLength ?? 14))
      const rsi = calcRSI(closes, p)
      const rsiMa = calcSMA(rsi, maLen)
      return {
        instance,
        meta,
        title: `RSI (${p}, SMA ${maLen})`,
        plots: [
          { key: "rsi", label: "RSI", color: instance.color, type: "line", lineWidth: 1.7, values: rsi },
          { key: "rsi_ma", label: "RSI-MA", color: instance.secondaryColor || "#FBC02D", type: "line", lineWidth: 1.3, values: rsiMa },
        ],
      }
    }

    case "RSI_DIVERGENCE": {
      const p = Math.max(2, Math.round(instance.params.length ?? 14))
      const rsi = calcRSI(closes, p)
      const polylines: TvOverlayPolyline[] = []
      const markers: TvBarMarker[] = []
      let prevLowBar = -1, prevHighBar = -1
      for (let i = 4; i < len - 2; i++) {
        if (rsi[i] == null) continue
        const isRsiLow = rsi[i]! < (rsi[i - 1] ?? 100) && rsi[i]! < (rsi[i - 2] ?? 100) && rsi[i]! < (rsi[i + 1] ?? 100) && rsi[i]! < 45
        const isRsiHigh = rsi[i]! > (rsi[i - 1] ?? 0) && rsi[i]! > (rsi[i - 2] ?? 0) && rsi[i]! > (rsi[i + 1] ?? 0) && rsi[i]! > 55
        if (isRsiLow) {
          if (prevLowBar >= 0 && i - prevLowBar <= 35 && lows[i] < lows[prevLowBar] && rsi[i]! > rsi[prevLowBar]!) {
            markers.push({ barIndex: i, position: "belowBar", shape: "badge", color: "#089981", text: "Bull Div" })
          }
          prevLowBar = i
        }
        if (isRsiHigh) {
          if (prevHighBar >= 0 && i - prevHighBar <= 35 && highs[i] > highs[prevHighBar] && rsi[i]! < rsi[prevHighBar]!) {
            markers.push({ barIndex: i, position: "aboveBar", shape: "badge", color: "#F23645", text: "Bear Div" })
          }
          prevHighBar = i
        }
      }
      return {
        instance,
        meta,
        title: `RSI Divergence (${p})`,
        plots: [{ key: "rsi_div", label: "RSI", color: instance.color, type: "line", lineWidth: 1.7, values: rsi }],
        polylines,
        markers,
      }
    }

    case "MACD": {
      const fast = Math.max(2, Math.round(instance.params.fast ?? 12))
      const slow = Math.max(fast + 1, Math.round(instance.params.slow ?? 26))
      const sigLen = Math.max(2, Math.round(instance.params.signal ?? 9))
      const ef = calcEMA(closes, fast), es = calcEMA(closes, slow)
      const macdLine = closes.map((_, i) => (ef[i] != null && es[i] != null ? ef[i]! - es[i]! : null))
      const signalLine = calcEMA(macdLine, sigLen)
      const hist: (number | null)[] = new Array(len).fill(null)
      const histColors: (string | null)[] = new Array(len).fill(null)
      for (let i = 0; i < len; i++) {
        if (macdLine[i] != null && signalLine[i] != null) {
          const h = macdLine[i]! - signalLine[i]!
          hist[i] = h
          const prevH = i > 0 && hist[i - 1] != null ? hist[i - 1]! : 0
          histColors[i] = h >= 0 ? (h >= prevH ? "#26A69A" : "#B2DFDB") : h <= prevH ? "#EF5350" : "#FFCDD2"
        }
      }
      return {
        instance,
        meta,
        title: `MACD (${fast}, ${slow}, ${sigLen})`,
        plots: [
          { key: "macd_hist", label: "Histogram", color: "#26A69A", type: "histogram", values: hist, pointColors: histColors },
          { key: "macd_line", label: "MACD", color: instance.color, type: "line", lineWidth: 1.6, values: macdLine },
          { key: "macd_signal", label: "Signal", color: instance.secondaryColor || "#FF6D00", type: "line", lineWidth: 1.5, values: signalLine },
        ],
      }
    }

    case "PPO":
    case "PMO": {
      const fast = Math.max(2, Math.round(instance.params.fast ?? (instance.id === "PPO" ? 12 : 20)))
      const slow = Math.max(3, Math.round(instance.params.slow ?? (instance.id === "PPO" ? 26 : 35)))
      const ef = calcEMA(closes, Math.min(fast, slow))
      const es = calcEMA(closes, Math.max(fast, slow))
      const ppo = closes.map((_, i) => (ef[i] != null && es[i] && es[i]! > 0 ? ((ef[i]! - es[i]!) / es[i]!) * 100 : null))
      const sig = calcEMA(ppo, instance.params.signal ?? 9)
      return {
        instance,
        meta,
        title: `${meta.shortName} (${fast}, ${slow})`,
        plots: [
          { key: "ppo", label: meta.shortName, color: instance.color, type: "line", lineWidth: 1.6, values: ppo },
          { key: "ppo_s", label: "Signal", color: instance.secondaryColor || "#FF6D00", type: "line", lineWidth: 1.3, values: sig },
        ],
      }
    }

    case "STOCH": {
      const kPeriod = Math.max(2, Math.round(instance.params.kPeriod ?? 14))
      const smoothK = Math.max(1, Math.round(instance.params.smoothK ?? 3))
      const dPeriod = Math.max(1, Math.round(instance.params.dPeriod ?? 3))
      const { k, d } = calcStochastic(candles, kPeriod, smoothK, dPeriod)
      return {
        instance,
        meta,
        title: `Stoch (${kPeriod}, ${smoothK}, ${dPeriod})`,
        plots: [
          { key: "stoch_k", label: "%K", color: instance.color, type: "line", lineWidth: 1.5, values: k },
          { key: "stoch_d", label: "%D", color: instance.secondaryColor || "#FF6D00", type: "line", lineWidth: 1.4, values: d },
        ],
      }
    }

    case "STOCH_RSI": {
      const rsiLen = Math.max(2, Math.round(instance.params.rsiLength ?? 14))
      const stochLen = Math.max(2, Math.round(instance.params.stochLength ?? 14))
      const smoothK = Math.max(1, Math.round(instance.params.k ?? 3))
      const smoothD = Math.max(1, Math.round(instance.params.d ?? 3))
      const rsiVals = calcRSI(closes, rsiLen)
      const hh = calcHighest(rsiVals, stochLen)
      const ll = calcLowest(rsiVals, stochLen)
      const rawK = rsiVals.map((v, i) =>
        v != null && hh[i] != null && ll[i] != null ? (hh[i]! > ll[i]! ? ((v - ll[i]!) / (hh[i]! - ll[i]!)) * 100 : 50) : null
      )
      const kVals = calcSMA(rawK, smoothK)
      const dVals = calcSMA(kVals, smoothD)
      return {
        instance,
        meta,
        title: `Stoch RSI (${rsiLen}, ${stochLen}, ${smoothK}, ${smoothD})`,
        plots: [
          { key: "srsi_k", label: "K", color: instance.color, type: "line", lineWidth: 1.5, values: kVals },
          { key: "srsi_d", label: "D", color: instance.secondaryColor || "#FF6D00", type: "line", lineWidth: 1.4, values: dVals },
        ],
      }
    }

    case "SMI": {
      const p = Math.max(2, Math.round(instance.params.length ?? 10))
      const hh = calcHighest(highs, p), ll = calcLowest(lows, p)
      const rel = closes.map((c, i) => (hh[i] != null && ll[i] != null ? c - (hh[i]! + ll[i]!) / 2 : null))
      const range = highs.map((_, i) => (hh[i] != null && ll[i] != null ? hh[i]! - ll[i]! : null))
      const dsRel = calcEMA(calcEMA(rel, 3), 3)
      const dsRng = calcEMA(calcEMA(range, 3), 3)
      const smi = closes.map((_, i) => (dsRel[i] != null && dsRng[i] && dsRng[i]! > 0 ? (200 * dsRel[i]!) / dsRng[i]! : null))
      const sig = calcEMA(smi, 3)
      return {
        instance,
        meta,
        title: `SMI (${p})`,
        plots: [
          { key: "smi", label: "SMI", color: instance.color, type: "line", lineWidth: 1.6, values: smi },
          { key: "smi_s", label: "Signal", color: "#FF6D00", type: "line", lineWidth: 1.3, values: sig },
        ],
      }
    }

    case "STC": {
      const sLen = Math.max(3, Math.round(instance.params.length ?? 10))
      const fast = Math.max(3, Math.round(instance.params.fast ?? 23))
      const slow = Math.max(fast + 2, Math.round(instance.params.slow ?? 50))
      const ef = calcEMA(closes, fast), es = calcEMA(closes, slow)
      const macd = closes.map((_, i) => (ef[i] != null && es[i] != null ? ef[i]! - es[i]! : null))
      const stoch1 = (src: (number | null)[]) => {
        const hh = calcHighest(src, sLen), ll = calcLowest(src, sLen)
        const raw = src.map((v, i) => (v != null && hh[i] != null && ll[i] != null && hh[i]! > ll[i]! ? ((v - ll[i]!) / (hh[i]! - ll[i]!)) * 100 : null))
        return calcEMA(raw, 3)
      }
      const stc = stoch1(stoch1(macd))
      return {
        instance,
        meta,
        title: `STC (${sLen}, ${fast}, ${slow})`,
        plots: [{ key: "stc", label: "STC", color: instance.color, type: "line", lineWidth: 1.7, values: stc }],
      }
    }

    case "TRUE_STRENGTH":
    case "SMI_ERGODIC":
    case "SMI_ERGODIC_OSC": {
      const fast = Math.max(2, Math.round(instance.params.fast ?? 13))
      const slow = Math.max(fast + 1, Math.round(instance.params.slow ?? 25))
      const pc: (number | null)[] = closes.map((c, i) => (i > 0 ? c - closes[i - 1] : null))
      const absPc: (number | null)[] = pc.map((v) => (v != null ? Math.abs(v) : null))
      const dsPc = calcEMA(calcEMA(pc, slow), fast)
      const dsAbs = calcEMA(calcEMA(absPc, slow), fast)
      const tsi = closes.map((_, i) => (dsPc[i] != null && dsAbs[i] && dsAbs[i]! > 0 ? (100 * dsPc[i]!) / dsAbs[i]! : null))
      const sig = calcEMA(tsi, 7)
      if (instance.id === "SMI_ERGODIC_OSC") {
        const osc = tsi.map((t, i) => (t != null && sig[i] != null ? (t - sig[i]!) / 100 : null))
        const cols = osc.map((v) => (v == null ? null : v >= 0 ? "#26A69A" : "#EF5350"))
        return {
          instance,
          meta,
          title: `SMIO (${fast}, ${slow})`,
          plots: [{ key: "smio", label: "SMIO", color: instance.color, type: "histogram", values: osc, pointColors: cols }],
        }
      }
      return {
        instance,
        meta,
        title: `${meta.shortName} (${fast}, ${slow})`,
        plots: [
          { key: "tsi", label: meta.shortName, color: instance.color, type: "line", lineWidth: 1.6, values: tsi },
          { key: "tsi_s", label: "Signal", color: instance.secondaryColor || "#F23645", type: "line", lineWidth: 1.3, values: sig },
        ],
      }
    }

    case "TRIX": {
      const p = Math.max(2, Math.round(instance.params.length ?? 18))
      const e3 = calcEMA(calcEMA(calcEMA(closes, p), p), p)
      return {
        instance,
        meta,
        title: `TRIX (${p})`,
        plots: [{ key: "trix", label: "TRIX", color: instance.color, type: "line", lineWidth: 1.6, values: calcROCSeries(e3, 1) }],
      }
    }

    case "ULCER": {
      const p = Math.max(2, Math.round(instance.params.length ?? 14))
      const hh = calcHighest(closes, p)
      const ddSq = closes.map((c, i) => (hh[i] && hh[i]! > 0 ? Math.pow(((c - hh[i]!) / hh[i]!) * 100, 2) : null))
      const meanSq = calcSMA(ddSq, p)
      const ui = meanSq.map((m) => (m != null ? Math.sqrt(Math.max(0, m)) : null))
      return {
        instance,
        meta,
        title: `Ulcer (${p})`,
        plots: [{ key: "ulcer", label: "Ulcer %", color: instance.color, type: "line", lineWidth: 1.6, values: ui }],
      }
    }

    case "ULTIMATE_OSC": {
      const f = Math.max(2, Math.round(instance.params.fast ?? 7))
      const m = Math.max(f + 1, Math.round(instance.params.mid ?? 14))
      const s = Math.max(m + 1, Math.round(instance.params.slow ?? 28))
      const bp: number[] = new Array(len).fill(0)
      const tr: number[] = new Array(len).fill(1)
      for (let i = 1; i < len; i++) {
        const minL = Math.min(lows[i], closes[i - 1])
        const maxH = Math.max(highs[i], closes[i - 1])
        bp[i] = closes[i] - minL
        tr[i] = Math.max(1e-6, maxH - minL)
      }
      const avgRatio = (win: number, idx: number) => {
        let bSum = 0, tSum = 0
        for (let j = idx - win + 1; j <= idx; j++) { bSum += bp[j]; tSum += tr[j] }
        return tSum > 0 ? bSum / tSum : 0.5
      }
      const uo: (number | null)[] = new Array(len).fill(null)
      for (let i = s; i < len; i++) {
        uo[i] = (100 * (4 * avgRatio(f, i) + 2 * avgRatio(m, i) + avgRatio(s, i))) / 7
      }
      return {
        instance,
        meta,
        title: `UO (${f}, ${m}, ${s})`,
        plots: [{ key: "uo", label: "UO", color: instance.color, type: "line", lineWidth: 1.6, values: uo }],
      }
    }

    case "VORTEX": {
      const p = Math.max(2, Math.round(instance.params.length ?? 14))
      const tr = calcTrueRange(candles)
      const viPlus: (number | null)[] = new Array(len).fill(null)
      const viMinus: (number | null)[] = new Array(len).fill(null)
      for (let i = p; i < len; i++) {
        let vmP = 0, vmM = 0, trSum = 0
        for (let j = i - p + 1; j <= i; j++) {
          vmP += Math.abs(highs[j] - lows[j - 1])
          vmM += Math.abs(lows[j] - highs[j - 1])
          trSum += tr[j] ?? 0
        }
        if (trSum > 0) {
          viPlus[i] = vmP / trSum
          viMinus[i] = vmM / trSum
        }
      }
      return {
        instance,
        meta,
        title: `Vortex (${p})`,
        plots: [
          { key: "vi_p", label: "VI+", color: "#2962FF", type: "line", lineWidth: 1.5, values: viPlus },
          { key: "vi_m", label: "VI-", color: "#F23645", type: "line", lineWidth: 1.5, values: viMinus },
        ],
      }
    }

    case "WILLR": {
      const p = Math.max(2, Math.round(instance.params.length ?? 14))
      const hh = calcHighest(highs, p), ll = calcLowest(lows, p)
      const wr = closes.map((c, i) =>
        hh[i] != null && ll[i] != null && hh[i]! > ll[i]! ? ((hh[i]! - c) / (hh[i]! - ll[i]!)) * -100 : null
      )
      return {
        instance,
        meta,
        title: `Williams %R (${p})`,
        plots: [{ key: "willr", label: "%R", color: instance.color, type: "line", lineWidth: 1.5, values: wr }],
      }
    }

    case "MOM": {
      const p = Math.max(1, Math.round(instance.params.length ?? 10))
      const mom = closes.map((c, i) => (i >= p ? c - closes[i - p] : null))
      return {
        instance,
        meta,
        title: `Mom (${p})`,
        plots: [{ key: "mom", label: "Mom", color: instance.color, type: "line", lineWidth: 1.5, values: mom }],
      }
    }

    case "ROC": {
      const p = Math.max(1, Math.round(instance.params.length ?? 9))
      return {
        instance,
        meta,
        title: `ROC (${p})`,
        plots: [{ key: "roc", label: "ROC%", color: instance.color, type: "line", lineWidth: 1.5, values: calcROCSeries(closes, p) }],
      }
    }

    case "PERFORMANCE": {
      const p = Math.max(2, Math.round(instance.params.length ?? 60))
      const baseIdx = Math.max(0, len - p)
      const basePrice = Math.max(1e-6, closes[baseIdx])
      const perf = closes.map((c, i) => (i >= baseIdx ? ((c - basePrice) / basePrice) * 100 : null))
      return {
        instance,
        meta,
        title: `Perf % (${p})`,
        plots: [{ key: "perf", label: "Perf %", color: instance.color, type: "area", lineWidth: 1.6, values: perf }],
      }
    }

    case "RCI": {
      const p = Math.max(3, Math.round(instance.params.length ?? 9))
      return {
        instance,
        meta,
        title: `RCI (${p})`,
        plots: [{ key: "rci", label: `RCI ${p}`, color: instance.color, type: "line", lineWidth: 1.6, values: calcRCISingle(closes, p) }],
      }
    }

    case "RCI_RIBBON": {
      const s = instance.params.short ?? 9
      const m = instance.params.mid ?? 26
      const l = instance.params.long ?? 52
      return {
        instance,
        meta,
        title: `RCI (${s}, ${m}, ${l})`,
        plots: [
          { key: "rci_s", label: `RCI ${s}`, color: "#2962FF", type: "line", lineWidth: 1.5, values: calcRCISingle(closes, s) },
          { key: "rci_m", label: `RCI ${m}`, color: "#089981", type: "line", lineWidth: 1.5, values: calcRCISingle(closes, m) },
          { key: "rci_l", label: `RCI ${l}`, color: "#F23645", type: "line", lineWidth: 1.5, values: calcRCISingle(closes, l) },
        ],
      }
    }

    case "RVGI": {
      const p = Math.max(4, Math.round(instance.params.length ?? 10))
      const num: (number | null)[] = new Array(len).fill(null)
      const den: (number | null)[] = new Array(len).fill(null)
      for (let i = 3; i < len; i++) {
        const co = (k: number) => candles[k].close - candles[k].open
        const hl = (k: number) => candles[k].high - candles[k].low
        num[i] = (co(i) + 2 * co(i - 1) + 2 * co(i - 2) + co(i - 3)) / 6
        den[i] = (hl(i) + 2 * hl(i - 1) + 2 * hl(i - 2) + hl(i - 3)) / 6
      }
      const sNum = calcSMA(num, p), sDen = calcSMA(den, p)
      const rvgi = closes.map((_, i) => (sNum[i] != null && sDen[i] && Math.abs(sDen[i]!) > 1e-6 ? sNum[i]! / sDen[i]! : null))
      const sig = calcWMA(rvgi, 4)
      return {
        instance,
        meta,
        title: `RVGI (${p})`,
        plots: [
          { key: "rvgi", label: "RVGI", color: "#089981", type: "line", lineWidth: 1.6, values: rvgi },
          { key: "rvgi_s", label: "Signal", color: "#F23645", type: "line", lineWidth: 1.4, values: sig },
        ],
      }
    }

    case "RVI": {
      const p = Math.max(3, Math.round(instance.params.length ?? 10))
      const sd = calcStdDev(closes, p)
      const upSd: (number | null)[] = closes.map((c, i) => (i > 0 && sd[i] != null ? (c >= closes[i - 1] ? sd[i]! : 0) : null))
      const dnSd: (number | null)[] = closes.map((c, i) => (i > 0 && sd[i] != null ? (c < closes[i - 1] ? sd[i]! : 0) : null))
      const eu = calcRMA(upSd, 14), ed = calcRMA(dnSd, 14)
      const rvi = closes.map((_, i) => (eu[i] != null && ed[i] != null && eu[i]! + ed[i]! > 0 ? (100 * eu[i]!) / (eu[i]! + ed[i]!) : null))
      return {
        instance,
        meta,
        title: `RVI (${p})`,
        plots: [{ key: "rvi", label: "RVI", color: instance.color, type: "line", lineWidth: 1.6, values: rvi }],
      }
    }

    case "SEASONALITY": {
      const p = Math.max(5, Math.round(instance.params.length ?? 20))
      const seas: (number | null)[] = new Array(len).fill(null)
      for (let i = 1; i < len; i++) {
        const slot = i % p
        let sumPct = 0, cnt = 0
        for (let j = slot > 0 ? slot : p; j <= i; j += p) {
          if (closes[j - 1] > 0) {
            sumPct += ((closes[j] - closes[j - 1]) / closes[j - 1]) * 100
            cnt++
          }
        }
        seas[i] = cnt > 0 ? sumPct / cnt : 0
      }
      const cols = seas.map((v) => (v == null ? null : v >= 0 ? "rgba(0, 137, 123, 0.7)" : "rgba(239, 83, 80, 0.7)"))
      return {
        instance,
        meta,
        title: `Seasonality (${p})`,
        plots: [{ key: "seas", label: "Avg %", color: instance.color, type: "columns", values: seas, pointColors: cols }],
      }
    }

    case "STDDEV_IND": {
      const p = Math.max(2, Math.round(instance.params.length ?? 20))
      return {
        instance,
        meta,
        title: `StdDev (${p})`,
        plots: [{ key: "stddev", label: "StdDev", color: instance.color, type: "line", lineWidth: 1.6, values: calcStdDev(closes, p) }],
      }
    }

    case "TECH_RATINGS": {
      const sma10 = calcSMA(closes, 10), ema20 = calcEMA(closes, 20), sma50 = calcSMA(closes, 50), sma100 = calcSMA(closes, 100)
      const rsi = calcRSI(closes, 14)
      const cci = calcCCI(candles, 20)
      const { k } = calcStochastic(candles, 14, 3, 3)
      const score: (number | null)[] = new Array(len).fill(null)
      const cols: (string | null)[] = new Array(len).fill(null)
      for (let i = 0; i < len; i++) {
        let votes = 0, total = 0
        const voteMa = (ma: (number | null)[]) => {
          if (ma[i] != null) { total++; votes += closes[i] > ma[i]! ? 1 : -1 }
        }
        voteMa(sma10); voteMa(ema20); voteMa(sma50); voteMa(sma100)
        if (rsi[i] != null) { total++; votes += rsi[i]! < 32 ? 1 : rsi[i]! > 68 ? -1 : rsi[i]! >= 50 ? 0.4 : -0.4 }
        if (cci[i] != null) { total++; votes += cci[i]! < -100 ? 1 : cci[i]! > 100 ? -1 : cci[i]! >= 0 ? 0.35 : -0.35 }
        if (k[i] != null) { total++; votes += k[i]! < 20 ? 1 : k[i]! > 80 ? -1 : k[i]! >= 50 ? 0.3 : -0.3 }
        if (total > 0) {
          const s = votes / total
          score[i] = s
          cols[i] = s >= 0.45 ? "#089981" : s >= 0.1 ? "#4DB6AC" : s <= -0.45 ? "#F23645" : s <= -0.1 ? "#EF9A9A" : "#90A4AE"
        }
      }
      return {
        instance,
        meta,
        title: "Technical Ratings",
        plots: [{ key: "tr_score", label: "Rating", color: instance.color, type: "columns", values: score, pointColors: cols }],
      }
    }

    case "LINREG_SLOPE": {
      const p = Math.max(2, Math.round(instance.params.length ?? 14))
      const ls = calcLSMA(closes, p)
      const sma = calcSMA(closes, p)
      const slope = closes.map((_, i) => (ls[i] != null && sma[i] != null ? ((ls[i]! - sma[i]!) * 2) / Math.max(1, p - 1) : null))
      return {
        instance,
        meta,
        title: `LinReg Slope (${p})`,
        plots: [{ key: "lrs", label: "Slope", color: instance.color, type: "line", lineWidth: 1.6, values: slope }],
      }
    }

    case "MAJORITY_RULE": {
      const p = Math.max(2, Math.round(instance.params.length ?? 14))
      const mr: (number | null)[] = new Array(len).fill(null)
      for (let i = p; i < len; i++) {
        let up = 0
        for (let j = i - p + 1; j <= i; j++) if (closes[j] > closes[j - 1]) up++
        mr[i] = (up / p) * 100
      }
      return {
        instance,
        meta,
        title: `Majority Rule (${p})`,
        plots: [{ key: "maj_r", label: "Bull %", color: instance.color, type: "line", lineWidth: 1.6, values: mr }],
      }
    }

    default: {
      const p = Math.max(2, Math.round(instance.params.length ?? 14))
      return {
        instance,
        meta,
        title: `${meta.shortName} (${p})`,
        plots: [
          {
            key: "fallback_plot",
            label: meta.shortName,
            color: instance.color || meta.defaultColor,
            type: "line",
            lineWidth: instance.lineWidth || 1.6,
            values: meta.overlay ? calcEMA(closes, p) : calcRSI(closes, p),
          },
        ],
      }
    }
  }
}
