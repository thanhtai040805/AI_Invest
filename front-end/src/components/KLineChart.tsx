import { useEffect, useRef } from "react"
import { KLineChartPro, type Datafeed, type DatafeedSubscribeCallback, type Period, type SymbolInfo } from "@klinecharts/pro"
import type { KLineData } from "klinecharts"
import "@klinecharts/pro/dist/klinecharts-pro.css"

import { stockApi } from "@/lib/api"
import { getSocket } from "@/lib/socket"

interface ApiCandle { date: string; open?: number; high?: number; low?: number; close: number; volume?: number }
interface PriceTick { price?: number; close?: number; volume?: number }

// ── Real Vietnamese market data feed connected to PostgreSQL & WebSocket ──
class RealMarketDatafeed implements Datafeed {
  private socketUnsub?: () => void
  private lastCandle?: KLineData

  async searchSymbols(): Promise<SymbolInfo[]> {
    return []
  }

  async getHistoryKLineData(symbol: SymbolInfo, period: Period, _from: number, _to: number): Promise<KLineData[]> {
    void period
    void _from
    void _to
    const sym = symbol.ticker.toUpperCase()
    try {
      const candles = await stockApi.ohlcv(sym) as ApiCandle[]
      if (Array.isArray(candles) && candles.length > 0) {
        const sorted = [...candles].sort((a, b) => new Date(a.date).getTime() - new Date(b.date).getTime())
        const formatted: KLineData[] = sorted.map((c) => {
          const timestamp = new Date(c.date).getTime()
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
            turnover: volume * close,
          }
        })
        this.lastCandle = formatted[formatted.length - 1]
        return formatted
      }
    } catch (err) {
      console.warn(`[KLineChart] Failed to fetch real OHLCV for ${sym}:`, err)
    }

    // Fallback if no history exists for ticker
    const now = Date.now()
    const basePrice = symbol.pricePrecision ? 25000 : 25000
    const fallback: KLineData = {
      timestamp: now,
      open: basePrice,
      high: basePrice,
      low: basePrice,
      close: basePrice,
      volume: 1000,
      turnover: basePrice * 1000,
    }
    this.lastCandle = fallback
    return [fallback]
  }

  subscribe(symbol: SymbolInfo, _period: Period, callback: DatafeedSubscribeCallback): void {
    const sym = symbol.ticker.toUpperCase()
    const socket = getSocket()

    const onPrice = (data: PriceTick) => {
      if (!data) return
      const price = Number(data.price ?? data.close)
      if (!price) return

      const vol = Number(data.volume ?? 0)
      if (this.lastCandle) {
        const updated: KLineData = {
          ...this.lastCandle,
          close: price,
          high: Math.max(this.lastCandle.high, price),
          low: Math.min(this.lastCandle.low, price),
          volume: vol || this.lastCandle.volume,
          turnover: (vol || Number(this.lastCandle.volume)) * price,
        }
        this.lastCandle = updated
        callback(updated)
      }
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

export function KLineChart({
  ticker, name, basePrice, precision = 2, height = 360, subIndicators = ["VOL"], drawingBar = false,
}: {
  ticker: string; name: string; basePrice: number; precision?: number; height?: number
  subIndicators?: string[]; drawingBar?: boolean
}) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!ref.current) return
    const container = ref.current
    const feed = new RealMarketDatafeed()
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
      // pro exposes no dispose(); stop the live feed and tear down its DOM
      feed.unsubscribe()
      container.innerHTML = ""
    }
  }, [ticker, name, basePrice, precision, drawingBar, subIndicators])

  return <div ref={ref} className="klc-pro w-full overflow-hidden rounded-[8px] border border-line" style={{ height }} />
}
