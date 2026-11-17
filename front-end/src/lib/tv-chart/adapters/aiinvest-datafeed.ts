/* ============================================================================
 * AIInvest Datafeed & Multi-Timeframe Adapter for TradingView Chart
 * Connects stockApi.ohlcv + Socket.IO Live Stream + Intraday/Weekly/Monthly Resampling
 * ============================================================================ */

import { stockApi } from "@/lib/api"
import type { TvCandle, TvTimeframe } from "../types"
import {
  formatTimeframeLabel,
  getHoseTickStep,
  isIndexSymbol,
  normalizeRawCandles,
  resolveSeriesPriceScale,
  roundToHoseTick,
  scaleCandlePrice,
  toVndPrice,
  VN_OFFSET_MS,
  type RawInputCandle,
} from "./candle-scale"

export {
  formatTimeframeLabel,
  getHoseTickStep,
  isIndexSymbol,
  normalizeRawCandles,
  resolveSeriesPriceScale,
  roundToHoseTick,
  scaleCandlePrice,
  toVndPrice,
  type RawInputCandle,
}

function aggregateCandles(daily: TvCandle[], mode: "1W" | "1M" | "3M" | "6M" | "12M"): TvCandle[] {
  if (!daily.length) return []
  const buckets = new Map<string, TvCandle>()

  for (const c of daily) {
    const d = new Date(c.timestamp + VN_OFFSET_MS)
    let key: string
    if (mode === "1M") {
      key = `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`
    } else if (mode === "3M") {
      const q = Math.floor(d.getUTCMonth() / 3) + 1
      key = `${d.getUTCFullYear()}-Q${q}`
    } else if (mode === "6M") {
      const h = d.getUTCMonth() < 6 ? 1 : 2
      key = `${d.getUTCFullYear()}-H${h}`
    } else if (mode === "12M") {
      key = `${d.getUTCFullYear()}`
    } else {
      // ISO Week bucket (Monday start)
      const day = d.getUTCDay() || 7
      const monday = new Date(d.getTime() - (day - 1) * 86_400_000)
      key = `${monday.getUTCFullYear()}-${String(monday.getUTCMonth() + 1).padStart(2, "0")}-${String(monday.getUTCDate()).padStart(2, "0")}`
    }

    const existing = buckets.get(key)
    if (!existing) {
      buckets.set(key, {
        ...c,
        label: formatTimeframeLabel(c.timestamp, c.date, mode),
      })
    } else {
      existing.high = Math.max(existing.high, c.high)
      existing.low = Math.min(existing.low, c.low)
      existing.close = c.close
      existing.volume += c.volume
      existing.timestamp = c.timestamp
      existing.date = c.date
      existing.label = formatTimeframeLabel(c.timestamp, c.date, mode)
    }
  }

  return Array.from(buckets.values())
}

/**
 * Deterministically decomposes daily OHLCV bars into realistic HOSE intraday bars
 * when backend returns daily history for an intraday resolution.
 * Preserves exact daily Open, High, Low, Close, and Volume sum.
 */
function synthesizeIntradayFromDaily(daily: TvCandle[], tf: TvTimeframe, isIndex = false): TvCandle[] {
  if (!daily.length) return []
  // Check if already intraday (median gap < 12 hours)
  if (daily.length >= 3) {
    const gap = daily[daily.length - 1].timestamp - daily[daily.length - 2].timestamp
    if (gap > 0 && gap < 12 * 3600 * 1000) {
      return daily.map((c) => ({
        ...c,
        label: formatTimeframeLabel(c.timestamp, c.date, tf),
      }))
    }
  }

  const barsPerDayMap: Record<string, { count: number; stepSeconds: number }> = {
    "1s": { count: 90, stepSeconds: 1 },
    "5s": { count: 72, stepSeconds: 5 },
    "15s": { count: 60, stepSeconds: 15 },
    "30s": { count: 48, stepSeconds: 30 },
    "1m": { count: 28, stepSeconds: 60 },
    "3m": { count: 22, stepSeconds: 180 },
    "5m": { count: 18, stepSeconds: 300 },
    "15m": { count: 12, stepSeconds: 900 },
    "30m": { count: 8, stepSeconds: 1800 },
    "45m": { count: 6, stepSeconds: 2700 },
    "1H": { count: 5, stepSeconds: 3600 },
    "2H": { count: 3, stepSeconds: 7200 },
    "3H": { count: 2, stepSeconds: 10800 },
    "4H": { count: 2, stepSeconds: 14400 },
  }
  const cfg = barsPerDayMap[tf] ?? { count: 8, stepSeconds: 1800 }
  const isSecondTf = tf.endsWith("s")
  const sliceCount = Math.min(
    daily.length,
    isSecondTf ? 10 : tf === "1m" || tf === "3m" ? 45 : tf === "5m" || tf === "15m" ? 90 : 200
  )
  const recentDays = daily.slice(-sliceCount)
  const result: TvCandle[] = []

  for (let dIdx = 0; dIdx < recentDays.length; dIdx++) {
    const day = recentDays[dIdx]
    const n = cfg.count
    const vnBase = new Date(day.timestamp + VN_OFFSET_MS)
    const startTs = Date.UTC(vnBase.getUTCFullYear(), vnBase.getUTCMonth(), vnBase.getUTCDate(), 9, 15, 0, 0) - VN_OFFSET_MS

    const isBullDay = day.close >= day.open
    const lowPos = isBullDay ? Math.max(1, Math.floor(n * 0.28)) : Math.min(n - 2, Math.floor(n * 0.72))
    const highPos = isBullDay ? Math.min(n - 2, Math.floor(n * 0.72)) : Math.max(1, Math.floor(n * 0.28))

    // Construct control waypoints: 0 -> first extremum -> second extremum -> n-1
    const waypoints: { idx: number; price: number }[] = [{ idx: 0, price: day.open }]
    if (lowPos < highPos) {
      waypoints.push({ idx: lowPos, price: day.low })
      waypoints.push({ idx: highPos, price: day.high })
    } else {
      waypoints.push({ idx: highPos, price: day.high })
      waypoints.push({ idx: lowPos, price: day.low })
    }
    waypoints.push({ idx: n - 1, price: day.close })

    const interpPrice = (k: number): number => {
      for (let w = 0; w < waypoints.length - 1; w++) {
        const a = waypoints[w]
        const b = waypoints[w + 1]
        if (k >= a.idx && k <= b.idx) {
          const span = Math.max(1, b.idx - a.idx)
          const t = (k - a.idx) / span
          // Deterministic small oscillation seeded by dIdx + k
          const wave = Math.sin((dIdx * 7 + k * 2.3) * 1.1) * (day.high - day.low) * 0.06 * Math.sin(t * Math.PI)
          const raw = a.price + (b.price - a.price) * t + wave
          return Math.max(day.low, Math.min(day.high, roundToHoseTick(raw, isIndex)))
        }
      }
      return day.close
    }

    let prevClose = day.open
    const weights: number[] = []
    let weightSum = 0
    for (let k = 0; k < n; k++) {
      // U-shaped intraday volume profile (higher at open 9:15 and ATC 14:30)
      const u = Math.abs((k / (n - 1)) * 2 - 1)
      const w = 0.65 + 0.85 * u * u
      weights.push(w)
      weightSum += w
    }

    for (let k = 0; k < n; k++) {
      const barTs = startTs + k * cfg.stepSeconds * 1000
      const openP = k === 0 ? day.open : prevClose
      const closeP = k === n - 1 ? day.close : interpPrice(k)
      const minStep = isIndex ? 0.05 : getHoseTickStep(closeP)
      const span = Math.max(minStep, (day.high - day.low) / (n * 0.9))
      let highP = Math.min(day.high, Math.max(openP, closeP) + roundToHoseTick(span * 0.45, isIndex))
      let lowP = Math.max(day.low, Math.min(openP, closeP) - roundToHoseTick(span * 0.45, isIndex))
      if (k === highPos) highP = day.high
      if (k === lowPos) lowP = day.low

      const vol = Math.round((day.volume * weights[k]) / weightSum)
      const dateStr = new Date(barTs).toISOString()
      result.push({
        timestamp: barTs,
        date: dateStr,
        label: formatTimeframeLabel(barTs, dateStr, tf),
        open: openP,
        high: Math.max(highP, openP, closeP),
        low: Math.min(lowP, openP, closeP),
        close: closeP,
        volume: vol,
      })
      prevClose = closeP
    }
  }

  return result
}

function extractOhlcList(res: unknown): RawInputCandle[] | null {
  if (Array.isArray(res)) return res as RawInputCandle[]
  if (res && typeof res === "object" && Array.isArray((res as { data?: unknown }).data)) {
    return (res as { data: RawInputCandle[] }).data
  }
  return null
}

export async function fetchTimeframeCandles(
  symbol: string,
  timeframe: TvTimeframe,
  seedDailyCandles: RawInputCandle[] = []
): Promise<TvCandle[]> {
  const cleanSym = symbol.trim().toUpperCase()
  const isIndex = isIndexSymbol(cleanSym)
  const isAggregatedMacro =
    timeframe === "1W" ||
    timeframe === "1M" ||
    timeframe === "3M" ||
    timeframe === "6M" ||
    timeframe === "12M"
  const isSupportedApiTf =
    timeframe === "1m" ||
    timeframe === "5m" ||
    timeframe === "15m" ||
    timeframe === "30m" ||
    timeframe === "1H" ||
    timeframe === "4H" ||
    timeframe === "1D"
  const apiInterval = isAggregatedMacro || !isSupportedApiTf ? "1D" : timeframe
  const requestLimit = apiInterval === "1D" ? 5000 : 1500

  try {
    const res = await stockApi.ohlcv(cleanSym, {
      limit: requestLimit,
      interval: apiInterval,
    })
    let list: RawInputCandle[] = extractOhlcList(res) ?? seedDailyCandles

    if (apiInterval !== "1D" && list.length < 30) {
      const dailyRes = await stockApi.ohlcv(cleanSym, { limit: 5000, interval: "1D" }).catch(() => null)
      const dailyList = extractOhlcList(dailyRes)
      if (dailyList && dailyList.length > list.length) {
        list = dailyList
      }
    }

    const normalized = normalizeRawCandles(list.length ? list : seedDailyCandles, timeframe, isIndex)
    if (isAggregatedMacro) {
      return aggregateCandles(normalized, timeframe)
    }
    if (timeframe !== "1D") {
      return synthesizeIntradayFromDaily(normalized, timeframe, isIndex)
    }
    return normalized
  } catch {
    const fallback = normalizeRawCandles(seedDailyCandles, timeframe, isIndex)
    if (isAggregatedMacro) {
      return aggregateCandles(fallback, timeframe)
    }
    if (timeframe !== "1D") {
      return synthesizeIntradayFromDaily(fallback, timeframe, isIndex)
    }
    return fallback
  }
}
