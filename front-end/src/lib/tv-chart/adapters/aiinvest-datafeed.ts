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
    return normalized
  } catch {
    const fallback = normalizeRawCandles(seedDailyCandles, timeframe, isIndex)
    if (isAggregatedMacro) {
      return aggregateCandles(fallback, timeframe)
    }
    return fallback
  }
}
