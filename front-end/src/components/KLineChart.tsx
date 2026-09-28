import { useEffect, useRef } from "react"
import { KLineChartPro, type Datafeed, type DatafeedSubscribeCallback, type Period, type SymbolInfo } from "@klinecharts/pro"
import type { KLineData } from "klinecharts"
import "@klinecharts/pro/dist/klinecharts-pro.css"

import { stockApi } from "@/lib/api"
import { getSocket, retainOhlc, retainStock } from "@/lib/socket"

interface ApiCandle { date?: string; time?: number | string; timestamp?: number | string; open?: number; high?: number; low?: number; close: number; volume?: number }
interface OhlcTick { symbol?: string; resolution?: string; timestamp?: number; open?: number; high?: number; low?: number; close?: number; volume?: number }

function dnseResolution(period: Period): string | null {
  if (period.timespan === "minute") return period.multiplier === 60 ? "1H" : String(period.multiplier)
  if (period.timespan === "day") return "1D"
  if (period.timespan === "week") return "1W"
  return null
}

// ── Real Vietnamese market data feed connected to PostgreSQL & WebSocket ──
class RealMarketDatafeed implements Datafeed {
  private socketUnsub?: () => void
  private lastCandle?: KLineData
  private historyCache: KLineData[] | null = null
  private hasMoreHistory = true
  private isFetching = false
  private currentTicker = ""
  private currentResolution = ""

  async searchSymbols(): Promise<SymbolInfo[]> {
    return []
  }

  async getHistoryKLineData(symbol: SymbolInfo, period: Period, from: number, to: number): Promise<KLineData[]> {
    const sym = symbol.ticker.toUpperCase()
    const resolution = dnseResolution(period) ?? "1D"

    // Reset cache if ticker changed
    if (this.currentTicker !== sym || this.currentResolution !== resolution) {
      this.currentTicker = sym
      this.currentResolution = resolution
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
      const candles = (await stockApi.ohlcv(sym, { limit: 300, interval: resolution })) as ApiCandle[]
      if (Array.isArray(candles) && candles.length > 0) {
        const candleTime = (c: ApiCandle) => {
          const raw = c.timestamp ?? c.time ?? c.date
          if (typeof raw === "string" && !/^\d+$/.test(raw)) return new Date(raw).getTime()
          const numeric = Number(raw ?? 0)
          return numeric > 1e12 ? numeric : numeric * 1000
        }
        const sorted = [...candles].sort((a, b) => candleTime(a) - candleTime(b))
        const formatted: KLineData[] = sorted.map((c) => {
          const timestamp = candleTime(c)
          const open = Number(c.open ?? c.close)
          const high = Number(c.high ?? Math.max(open, Number(c.close)))
          const low = Number(c.low ?? Math.min(open, Number(c.close)))
          const close = Number(c.close)
          const volume = Number(c.volume ?? 0)
          return {
            timestamp,
            open,
            high,
            low,
            close,
            volume,
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

    // An empty chart is preferable to a fabricated market candle.
    this.hasMoreHistory = false
    this.historyCache = []
    return []
  }

  subscribe(symbol: SymbolInfo, period: Period, callback: DatafeedSubscribeCallback): void {
    this.unsubscribe()
    const sym = symbol.ticker.toUpperCase()
    const resolution = dnseResolution(period)
    const socket = getSocket()
    if (!resolution) return

    const onCandle = (data: OhlcTick) => {
      if (data?.symbol !== sym || data.resolution !== resolution) return
      const rawTime = Number(data.timestamp ?? 0)
      const timestamp = rawTime > 1e12 ? rawTime : rawTime * 1000
      if (!timestamp || (this.lastCandle && timestamp < this.lastCandle.timestamp)) return
      const candle: KLineData = {
        timestamp,
        open: Number(data.open ?? 0),
        high: Number(data.high ?? 0),
        low: Number(data.low ?? 0),
        close: Number(data.close ?? 0),
        volume: Number(data.volume ?? 0),
      }
      if (!candle.open || !candle.close) return
      this.lastCandle = candle
      callback(candle)
    }

    const releaseStock = ["VNINDEX", "VN30", "VN100"].includes(sym) ? () => {} : retainStock(sym)
    const releaseOhlc = retainOhlc(sym, resolution)
    socket.on(`stock:ohlc:${sym}`, onCandle)
    socket.on(`stock:ohlcClosed:${sym}`, onCandle)

    this.socketUnsub = () => {
      releaseOhlc()
      releaseStock()
      socket.off(`stock:ohlc:${sym}`, onCandle)
      socket.off(`stock:ohlcClosed:${sym}`, onCandle)
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
]

const DEFAULT_SUB_INDICATORS = ["VOL"]

export function KLineChart({
  ticker,
  name,
  precision = 2,
  height = 360,
  subIndicators = DEFAULT_SUB_INDICATORS,
  drawingBar = false,
}: {
  ticker: string
  name: string
  precision?: number
  height?: number
  subIndicators?: string[]
  drawingBar?: boolean
}) {
  const ref = useRef<HTMLDivElement>(null)
  const feedRef = useRef<RealMarketDatafeed | null>(null)
  const subIndicatorsKey = subIndicators.join(",")

  useEffect(() => {
    if (!ref.current) return
    const container = ref.current
    const feed = new RealMarketDatafeed()
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
