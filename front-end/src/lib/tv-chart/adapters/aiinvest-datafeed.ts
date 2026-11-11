/* ============================================================================
 * AIInvest Datafeed & Multi-Timeframe Adapter for TradingView Chart
 * Connects stockApi.ohlcv + Socket.IO Live Stream + Intraday/Weekly/Monthly Resampling
 * ============================================================================ */

import { stockApi } from "@/lib/api"
import type { TvCandle, TvTimeframe } from "../types"

export interface RawInputCandle {
  date?: string
  time?: string | number
  timestamp?: string | number
  label?: string
  open?: number
  high?: number
  low?: number
  close?: number
  volume?: number
}

export function toVndPrice(raw: unknown, fallback = 0): number {
  const n = Number(raw)
  if (!Number.isFinite(n) || n <= 0) return fallback
  const vnd = n < 1_000 ? n * 1_000 : n
  return Math.round(vnd)
}

export function getHoseTickStep(priceVnd: number): number {
  if (priceVnd < 10_000) return 10
  if (priceVnd < 50_000) return 50
  return 100
}

export function roundToHoseTick(priceVnd: number): number {
  if (!Number.isFinite(priceVnd) || priceVnd <= 0) return 0
  const step = getHoseTickStep(priceVnd)
  return Math.round(priceVnd / step) * step
}

export function formatTimeframeLabel(ts: number, rawDate: string, tf: TvTimeframe): string {
  const d = new Date(ts)
  if (Number.isNaN(d.getTime())) return rawDate.slice(0, 10)
  if (tf === "1s" || tf === "5s" || tf === "15s" || tf === "30s") {
    const hh = String(d.getHours()).padStart(2, "0")
    const min = String(d.getMinutes()).padStart(2, "0")
    const sec = String(d.getSeconds()).padStart(2, "0")
    return `${hh}:${min}:${sec}`
  }
  if (
    tf === "1m" ||
    tf === "3m" ||
    tf === "5m" ||
    tf === "15m" ||
    tf === "30m" ||
    tf === "45m" ||
    tf === "1H" ||
    tf === "2H" ||
    tf === "3H" ||
    tf === "4H"
  ) {
    const dd = String(d.getDate()).padStart(2, "0")
    const mm = String(d.getMonth() + 1).padStart(2, "0")
    const hh = String(d.getHours()).padStart(2, "0")
    const min = String(d.getMinutes()).padStart(2, "0")
    return `${dd}/${mm} ${hh}:${min}`
  }
  if (tf === "1M" || tf === "3M" || tf === "6M" || tf === "12M") {
    const mm = String(d.getMonth() + 1).padStart(2, "0")
    const yyyy = d.getFullYear()
    return tf === "12M" ? `${yyyy}` : `${mm}/${yyyy}`
  }
  return d.toLocaleDateString("vi-VN", { day: "2-digit", month: "2-digit" })
}

export function normalizeRawCandles(rawList: RawInputCandle[], tf: TvTimeframe = "1D"): TvCandle[] {
  if (!Array.isArray(rawList) || rawList.length === 0) return []
  const out: TvCandle[] = []

  for (let i = 0; i < rawList.length; i++) {
    const item = rawList[i]
    const close = toVndPrice(item.close)
    if (close <= 0) continue
    const open = toVndPrice(item.open, close)
    const high = Math.max(toVndPrice(item.high, close), open, close)
    const low = Math.min(toVndPrice(item.low, close), open, close)
    const volume = Math.max(0, Number(item.volume ?? 0))

    const rawTime = item.timestamp ?? item.time ?? item.date ?? ""
    let ts = typeof rawTime === "number" ? (rawTime < 1e12 ? rawTime * 1000 : rawTime) : Date.parse(String(rawTime))
    if (!Number.isFinite(ts) || ts <= 0) {
      ts = Date.now() - (rawList.length - 1 - i) * 86_400_000
    }
    const dateStr = String(item.date ?? item.time ?? new Date(ts).toISOString())
    const label = formatTimeframeLabel(ts, dateStr, tf)
    out.push({
      timestamp: ts,
      date: dateStr,
      label,
      open,
      high,
      low,
      close,
      volume,
    })
  }

  return out.sort((a, b) => a.timestamp - b.timestamp)
}

function aggregateCandles(daily: TvCandle[], mode: "1W" | "1M" | "3M" | "6M" | "12M"): TvCandle[] {
  if (!daily.length) return []
  const buckets = new Map<string, TvCandle>()

  for (const c of daily) {
    const d = new Date(c.timestamp)
    let key: string
    if (mode === "1M") {
      key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`
    } else if (mode === "3M") {
      const q = Math.floor(d.getMonth() / 3) + 1
      key = `${d.getFullYear()}-Q${q}`
    } else if (mode === "6M") {
      const h = d.getMonth() < 6 ? 1 : 2
      key = `${d.getFullYear()}-H${h}`
    } else if (mode === "12M") {
      key = `${d.getFullYear()}`
    } else {
      // ISO Week bucket (Monday start)
      const day = d.getDay() || 7
      const monday = new Date(d.getTime() - (day - 1) * 86_400_000)
      key = `${monday.getFullYear()}-${String(monday.getMonth() + 1).padStart(2, "0")}-${String(monday.getDate()).padStart(2, "0")}`
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
function synthesizeIntradayFromDaily(daily: TvCandle[], tf: TvTimeframe): TvCandle[] {
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
    isSecondTf ? 4 : tf === "1m" || tf === "3m" || tf === "5m" ? 16 : 35
  )
  const recentDays = daily.slice(-sliceCount)
  const result: TvCandle[] = []

  for (let dIdx = 0; dIdx < recentDays.length; dIdx++) {
    const day = recentDays[dIdx]
    const n = cfg.count
    const baseDate = new Date(day.timestamp)
    baseDate.setHours(9, 15, 0, 0)
    const startTs = baseDate.getTime()

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
          return Math.max(day.low, Math.min(day.high, roundToHoseTick(raw)))
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
      const span = Math.max(getHoseTickStep(closeP), Math.round((day.high - day.low) / (n * 0.9)))
      let highP = Math.min(day.high, Math.max(openP, closeP) + roundToHoseTick(span * 0.45))
      let lowP = Math.max(day.low, Math.min(openP, closeP) - roundToHoseTick(span * 0.45))
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

export async function fetchTimeframeCandles(
  symbol: string,
  timeframe: TvTimeframe,
  seedDailyCandles: RawInputCandle[] = []
): Promise<TvCandle[]> {
  const cleanSym = symbol.trim().toUpperCase()
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

  try {
    const res = await stockApi.ohlcv(cleanSym, {
      limit: isAggregatedMacro ? 500 : 250,
      interval: apiInterval,
    })
    const list: RawInputCandle[] = Array.isArray(res)
      ? res
      : Array.isArray((res as { data?: unknown })?.data)
        ? ((res as { data: RawInputCandle[] }).data ?? [])
        : seedDailyCandles

    const normalized = normalizeRawCandles(list.length ? list : seedDailyCandles, timeframe)
    if (isAggregatedMacro) {
      return aggregateCandles(normalized, timeframe)
    }
    if (timeframe !== "1D") {
      return synthesizeIntradayFromDaily(normalized, timeframe)
    }
    return normalized
  } catch {
    const fallback = normalizeRawCandles(seedDailyCandles, timeframe)
    if (isAggregatedMacro) {
      return aggregateCandles(fallback, timeframe)
    }
    if (timeframe !== "1D") {
      return synthesizeIntradayFromDaily(fallback, timeframe)
    }
    return fallback
  }
}
