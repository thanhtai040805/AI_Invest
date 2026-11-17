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

const INDEX_SYMBOLS = new Set([
  "VNINDEX",
  "VN-INDEX",
  "^VNINDEX",
  "VN30",
  "VN100",
  "HNX",
  "HNXINDEX",
  "HNX30",
  "UPCOM",
  "UPCOMINDEX",
])

export function isIndexSymbol(symbol?: string | null): boolean {
  if (!symbol) return false
  return INDEX_SYMBOLS.has(symbol.trim().toUpperCase())
}

export function toVndPrice(raw: unknown, fallback = 0, isIndex = false): number {
  const n = Number(raw)
  if (!Number.isFinite(n) || n <= 0) return fallback
  if (isIndex) {
    // Auto-heal any legacy 1000x scaled index points (e.g. 1,746,900 -> 1746.90)
    const idxVal = n > 100_000 ? n / 1_000 : n
    return Number(idxVal.toFixed(2))
  }
  const vnd = n < 1_000 ? n * 1_000 : n
  return Math.round(vnd)
}

export function resolveSeriesPriceScale(rawList: RawInputCandle[], isIndex = false): number {
  if (isIndex || !Array.isArray(rawList) || rawList.length === 0) return 1
  const maxClose = rawList.reduce((m, item) => {
    const c = Number(item?.close)
    return Number.isFinite(c) && c > m ? c : m
  }, 0)
  return maxClose > 0 && maxClose < 1_000 ? 1_000 : 1
}

export function scaleCandlePrice(
  raw: unknown,
  fallback: number,
  scale: number,
  isIndex = false
): number {
  const n = Number(raw)
  if (!Number.isFinite(n) || n <= 0) return fallback
  if (isIndex) {
    const idxVal = n > 100_000 ? n / 1_000 : n
    return Number(idxVal.toFixed(2))
  }
  return Math.round(n * scale)
}

export function getHoseTickStep(priceVnd: number): number {
  if (priceVnd < 10_000) return 10
  if (priceVnd < 50_000) return 50
  return 100
}

export function roundToHoseTick(priceVnd: number, isIndex = false): number {
  if (!Number.isFinite(priceVnd) || priceVnd <= 0) return 0
  if (isIndex) return Number(priceVnd.toFixed(2))
  const step = getHoseTickStep(priceVnd)
  return Math.round(priceVnd / step) * step
}

export const VN_OFFSET_MS = 7 * 3_600_000

export function formatTimeframeLabel(ts: number, rawDate: string, tf: TvTimeframe): string {
  const raw = new Date(ts)
  if (Number.isNaN(raw.getTime())) return rawDate.slice(0, 10)
  const d = new Date(ts + VN_OFFSET_MS)
  if (tf === "1s" || tf === "5s" || tf === "15s" || tf === "30s") {
    const hh = String(d.getUTCHours()).padStart(2, "0")
    const min = String(d.getUTCMinutes()).padStart(2, "0")
    const sec = String(d.getUTCSeconds()).padStart(2, "0")
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
    const dd = String(d.getUTCDate()).padStart(2, "0")
    const mm = String(d.getUTCMonth() + 1).padStart(2, "0")
    const hh = String(d.getUTCHours()).padStart(2, "0")
    const min = String(d.getUTCMinutes()).padStart(2, "0")
    return `${dd}/${mm} ${hh}:${min}`
  }
  if (tf === "1M" || tf === "3M" || tf === "6M" || tf === "12M") {
    const mm = String(d.getUTCMonth() + 1).padStart(2, "0")
    const yyyy = d.getUTCFullYear()
    return tf === "12M" ? `${yyyy}` : `${mm}/${yyyy}`
  }
  return raw.toLocaleDateString("vi-VN", {
    timeZone: "Asia/Ho_Chi_Minh",
    day: "2-digit",
    month: "2-digit",
  })
}

export function normalizeRawCandles(
  rawList: RawInputCandle[],
  tf: TvTimeframe = "1D",
  isIndex = false
): TvCandle[] {
  if (!Array.isArray(rawList) || rawList.length === 0) return []
  const scale = resolveSeriesPriceScale(rawList, isIndex)
  const out: TvCandle[] = []

  for (let i = 0; i < rawList.length; i++) {
    const item = rawList[i]
    const close = scaleCandlePrice(item.close, 0, scale, isIndex)
    if (close <= 0) continue
    const open = scaleCandlePrice(item.open, close, scale, isIndex)
    const high = Math.max(scaleCandlePrice(item.high, close, scale, isIndex), open, close)
    const low = Math.min(scaleCandlePrice(item.low, close, scale, isIndex), open, close)
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
