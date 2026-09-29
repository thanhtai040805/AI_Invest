import { useEffect, useRef } from "react"
import { KLineChartPro, type Datafeed, type DatafeedSubscribeCallback, type Period, type SymbolInfo } from "@klinecharts/pro"
import type { KLineData } from "klinecharts"
import "@klinecharts/pro/dist/klinecharts-pro.css"

import { stockApi } from "@/lib/api"
import { getSocket } from "@/lib/socket"

interface ApiCandle { date?: string; time?: number | string; timestamp?: number | string; open?: number; high?: number; low?: number; close: number; volume?: number }
interface PriceTick { price?: number; close?: number; open?: number; high?: number; low?: number; volume?: number; timestamp?: number | string }

function toChartPrice(value: number): number {
  return value > 0 && value < 500 ? value * 1000 : value
}

function candleTimestamp(candle: ApiCandle): number {
  return parseTimestamp(candle.timestamp ?? candle.time ?? candle.date)
}

function parseTimestamp(raw?: number | string): number {
  if (typeof raw === "string" && !/^\d+$/.test(raw)) return new Date(raw).getTime()
  const numeric = Number(raw ?? 0)
  return numeric > 1e12 ? numeric : numeric * 1000
}

const vietnamDateFormatter = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Ho_Chi_Minh",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
})

function vietnamDateKey(timestamp: number): string {
  const parts = vietnamDateFormatter.formatToParts(timestamp)
  const part = (type: string) => parts.find((item) => item.type === type)?.value ?? ""
  return `${part("year")}-${part("month")}-${part("day")}`
}

// ── Real Vietnamese market data feed connected to PostgreSQL & WebSocket ──
class RealMarketDatafeed implements Datafeed {
  private socketUnsub?: () => void
  private lastCandle?: KLineData
  private historyCache: KLineData[] | null = null
  private hasMoreHistory = true
  private isFetching = false
  private currentTicker = ""
  private fallbackPrice = 25000

  constructor(fallbackPrice = 25000) {
    this.fallbackPrice = fallbackPrice > 0 ? fallbackPrice : 25000
  }

  setFallbackPrice(p: number) {
    if (p > 0) this.fallbackPrice = p
  }

  async searchSymbols(): Promise<SymbolInfo[]> {
    return []
  }

  async getHistoryKLineData(symbol: SymbolInfo, period: Period, from: number, to: number): Promise<KLineData[]> {
    void period
    const sym = symbol.ticker.toUpperCase()

    // Reset cache if ticker changed
    if (this.currentTicker !== sym) {
      this.currentTicker = sym
      this.historyCache = null
      this.hasMoreHistory = true
    }

    // If history is already loaded and klinecharts is requesting older data (loadMore)
    if (this.historyCache !== null) {
      const oldestLoaded = this.historyCache[0]?.timestamp ?? 0
      if (to <= oldestLoaded) {
        // No older candles available in this window. Return empty array to signal hasMore = false
        this.hasMoreHistory = false
        return []
      }
      const inRange = this.historyCache.filter((c) => c.timestamp >= from && c.timestamp <= to)
      return inRange.length > 0 ? inRange : []
    }

    // Prevent concurrent duplicate initial fetches
    if (this.isFetching) {
      return []
    }

    this.isFetching = true
    try {
      const response = await stockApi.ohlcv(sym, { limit: 300 })
      const candles = (Array.isArray(response) ? response : (response as { data?: ApiCandle[] })?.data) as ApiCandle[]
      if (Array.isArray(candles) && candles.length > 0) {
        const sorted = [...candles].sort((a, b) => candleTimestamp(a) - candleTimestamp(b))
        const formatted: KLineData[] = sorted.map((c) => {
          const timestamp = candleTimestamp(c)
          const close = toChartPrice(Number(c.close))
          const open = toChartPrice(Number(c.open ?? c.close))
          const high = toChartPrice(Number(c.high ?? Math.max(open, close)))
          const low = toChartPrice(Number(c.low ?? Math.min(open, close)))
          const volume = Number(c.volume ?? 0)
          return {
            timestamp,
            open,
            high,
            low,
            close,
            volume,
            turnover: volume * close,
          }
        })
        this.historyCache = formatted
        this.lastCandle = formatted[formatted.length - 1]
        if (formatted.length < 300) {
          this.hasMoreHistory = false
        }
        return formatted
      }
    } catch (err) {
      console.warn(`[KLineChart] Failed to fetch real OHLCV for ${sym}:`, err)
    } finally {
      this.isFetching = false
    }

    // Fallback if no history exists for ticker
    this.hasMoreHistory = false
    const now = Date.now()
    const basePrice = this.fallbackPrice
    const fallback: KLineData = {
      timestamp: now,
      open: basePrice,
      high: basePrice,
      low: basePrice,
      close: basePrice,
      volume: 1000,
      turnover: basePrice * 1000,
    }
    this.historyCache = [fallback]
    this.lastCandle = fallback
    return [fallback]
  }

  subscribe(symbol: SymbolInfo, _period: Period, callback: DatafeedSubscribeCallback): void {
    const sym = symbol.ticker.toUpperCase()
    const socket = getSocket()

    const onPrice = (data: PriceTick) => {
      if (!data) return
      const price = toChartPrice(Number(data.price ?? data.close))
      if (!price) return

      const vol = Number(data.volume ?? 0)
      const eventTime = parseTimestamp(data.timestamp) || Date.now()
      if (this.lastCandle && eventTime < this.lastCandle.timestamp) return
      const currentDate = vietnamDateKey(eventTime)
      const lastDate = this.lastCandle ? vietnamDateKey(this.lastCandle.timestamp) : ""
      const sameSession = !!this.lastCandle && currentDate === lastDate
      const open = toChartPrice(Number(data.open ?? (sameSession ? this.lastCandle?.open : price)))
      const high = toChartPrice(Number(data.high ?? price))
      const low = toChartPrice(Number(data.low ?? price))
      const updated: KLineData = {
        timestamp: sameSession ? this.lastCandle!.timestamp : Date.parse(`${currentDate}T00:00:00Z`),
        open,
        high: Math.max(high, price, sameSession ? this.lastCandle!.high : high),
        low: Math.min(low, price, sameSession ? this.lastCandle!.low : low),
        close: price,
        volume: vol || (sameSession ? this.lastCandle!.volume : 0),
        turnover: (vol || (sameSession ? Number(this.lastCandle!.volume) : 0)) * price,
      }
      this.lastCandle = updated
      if (this.historyCache) {
        if (sameSession) this.historyCache[this.historyCache.length - 1] = updated
        else this.historyCache.push(updated)
      }
      callback(updated)
    }

    if (!socket.connected) socket.connect()
    socket.emit("subscribe:symbol", sym)
    socket.on(`stock:price:${sym}`, onPrice)

    this.socketUnsub = () => {
      socket.emit("unsubscribe:symbol", sym)
      socket.off(`stock:price:${sym}`, onPrice)
    }
  }

  unsubscribe(): void {
    if (this.socketUnsub) {
      this.socketUnsub()
      this.socketUnsub = undefined
    }
  }
}

// ── Project-palette theming for the pro chart canvas ──────────────
const gain = "#2b805b"
const loss = "#b55250"
const line = "#d7ddd9"
const muted = "#7a8580"
const ink = "#18201d"
const mineral = "#466779"

const chartStyles = {
  grid: {
    horizontal: { color: line, style: "dashed", dashedValue: [2, 3] },
    vertical: { color: line, style: "dashed", dashedValue: [2, 3] },
  },
  candle: {
    bar: { upColor: gain, downColor: loss, noChangeColor: muted, upBorderColor: gain, downBorderColor: loss, upWickColor: gain, downWickColor: loss },
    priceMark: {
      high: { color: muted },
      low: { color: muted },
      last: { upColor: gain, downColor: loss, noChangeColor: muted, text: { borderColor: ink, backgroundColor: ink } },
    },
    tooltip: { text: { color: ink }, rect: { color: "#ffffff", borderColor: line } },
  },
  indicator: {
    lines: [{ color: mineral }, { color: "#b08a43" }, { color: "#3f6259" }, { color: muted }],
    bars: [{ upColor: "color-mix(in srgb, #2b805b 55%, transparent)", downColor: "color-mix(in srgb, #b55250 55%, transparent)", noChangeColor: muted }],
  },
  xAxis: { axisLine: { color: line }, tickLine: { color: line }, tickText: { color: muted } },
  yAxis: { axisLine: { color: line }, tickLine: { color: line }, tickText: { color: muted } },
  crosshair: {
    horizontal: { line: { color: mineral }, text: { backgroundColor: mineral, borderColor: mineral } },
    vertical: { line: { color: mineral }, text: { backgroundColor: mineral, borderColor: mineral } },
  },
} as const

const defaultPeriods: Period[] = [
  { multiplier: 15, timespan: "minute", text: "15m" },
  { multiplier: 60, timespan: "minute", text: "1H" },
  { multiplier: 1, timespan: "day", text: "1D" },
  { multiplier: 1, timespan: "week", text: "1W" },
  { multiplier: 1, timespan: "month", text: "1M" },
]

const DEFAULT_SUB_INDICATORS = ["VOL"]

export function KLineChart({
  ticker,
  name,
  basePrice = 25000,
  precision = 2,
  height = 360,
  subIndicators = DEFAULT_SUB_INDICATORS,
  drawingBar = false,
}: {
  ticker: string
  name: string
  basePrice?: number
  precision?: number
  height?: number
  subIndicators?: string[]
  drawingBar?: boolean
}) {
  const ref = useRef<HTMLDivElement>(null)
  const feedRef = useRef<RealMarketDatafeed | null>(null)
  const subIndicatorsKey = subIndicators.join(",")

  // Update fallback price without tearing down the chart instance
  useEffect(() => {
    if (feedRef.current && basePrice > 0) {
      feedRef.current.setFallbackPrice(basePrice)
    }
  }, [basePrice])

  useEffect(() => {
    if (!ref.current) return
    const container = ref.current
    const feed = new RealMarketDatafeed(basePrice)
    feedRef.current = feed

    try {
      new KLineChartPro({
        container,
        theme: "light",
        locale: "en-US",
        drawingBarVisible: drawingBar,
        symbol: { ticker, name, shortName: ticker, exchange: "HOSE", market: "stocks", pricePrecision: precision, volumePrecision: 0, priceCurrency: "vnd" },
        period: { multiplier: 1, timespan: "day", text: "1D" },
        periods: defaultPeriods,
        subIndicators,
        mainIndicators: ["MA"],
        datafeed: feed,
        styles: chartStyles as unknown as ConstructorParameters<typeof KLineChartPro>[0]["styles"],
      })
    } catch (e) {
      console.error("KLineChartPro init failed", e)
    }

    return () => {
      feed.unsubscribe()
      feedRef.current = null
      container.innerHTML = ""
    }
  }, [ticker, name, precision, drawingBar, subIndicatorsKey])

  return <div ref={ref} className="klc-pro w-full overflow-hidden rounded-[8px] border border-line" style={{ height }} />
}
