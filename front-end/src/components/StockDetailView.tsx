"use client"

import React, { useEffect, useMemo, useState } from "react"
import { createPortal } from "react-dom"
import { TradingViewChart } from "@/lib/tv-chart"
import { Button, Pill, fmt, fmtPct } from "@/components/ui"
import { marketApi, stockApi, workspaceApi } from "@/lib/api"
import { useResource } from "@/lib/api/use-resource"
import { Link, useRouter } from "@/lib/router"
import { formatSectorName } from "@/lib/sector-weights"
import { useRealtimeStock } from "@/lib/use-realtime"
import type { SuperStockItem, SuperStocksPayload } from "@/components/SuperStocksTable"
import type { ApiMarketSnapshot } from "@/types"

const LOCAL_WATCHLIST_KEY = "aiinvest_markets_watchlist_v1"

const VN30_SET = new Set([
  "ACB",
  "BCM",
  "BID",
  "BVH",
  "CTG",
  "FPT",
  "GAS",
  "GVR",
  "HDB",
  "HPG",
  "MBB",
  "MSN",
  "MWG",
  "PLX",
  "POW",
  "SAB",
  "SHB",
  "SSB",
  "SSI",
  "STB",
  "TCB",
  "TPB",
  "VCB",
  "VHM",
  "VIB",
  "VIC",
  "VJC",
  "VNM",
  "VPB",
  "VRE",
])

interface ApiStockQuote {
  price?: number | null
  close?: number | null
  ref?: number | null
  prior_close?: number | null
  open?: number | null
  high?: number | null
  low?: number | null
  change_pct?: number | null
  changePct?: number | null
  change_percent?: number | null
  ceiling?: number | null
  floor?: number | null
  volume?: number | null
  asOf?: string
  source?: string
  stale?: boolean
}

interface ApiStockProfile {
  name?: string
  industry?: string
  sector?: string
  shares_outstanding?: number | null
}

interface ApiOhlcvCandle {
  date?: string
  time?: number | string
  timestamp?: number | string
  open?: number
  high?: number
  low?: number
  close: number
  volume?: number
}

interface ApiNewsArticle {
  id?: number | string
  symbol?: string
  title?: string
  url?: string
  source?: string
  doc_type?: string | null
  published_date?: string | null
  created_at?: string | null
  sentiment_score?: number | null
  ai_summary?: string | null
}

type HoseTone = "ceiling" | "gain" | "ref" | "loss" | "floor" | "neutral"
type MainPopupTab = "TRADING" | "STATISTICS" | "VSA_QUANT" | "FILINGS"
type TradeTapeMode = "PRICE_STEP" | "RECENT_SESSIONS"

function toVndPrice(value: unknown): number {
  const n = Number(value ?? 0)
  if (!Number.isFinite(n) || n <= 0) return 0
  return n < 500 ? Math.round(n * 1000) : Math.round(n)
}

function getTickStep(price: number): number {
  if (price < 10_000) return 10
  if (price < 50_000) return 50
  return 100
}

function resolveCoherentBands(
  ref: number | null,
  price: number,
  rawCeiling: number,
  rawFloor: number
) {
  const anchor = ref && ref > 0 ? ref : price
  if (anchor <= 0) return { ceiling: 0, floor: 0 }
  const step = getTickStep(anchor)
  const calcCeiling = Math.floor((anchor * 1.07) / step) * step
  const calcFloor = Math.ceil((anchor * 0.93) / step) * step

  const isCoherent =
    rawCeiling > anchor &&
    rawFloor > 0 &&
    rawFloor < anchor &&
    Math.abs(rawCeiling / anchor - 1.07) <= 0.008 &&
    Math.abs(rawFloor / anchor - 0.93) <= 0.008 &&
    (price <= 0 || (price >= rawFloor && price <= rawCeiling))

  if (isCoherent) {
    return { ceiling: rawCeiling, floor: rawFloor }
  }
  return {
    ceiling: price > 0 ? Math.max(calcCeiling, price) : calcCeiling,
    floor: price > 0 ? Math.min(calcFloor, price) : calcFloor,
  }
}

function resolveHoseTone(
  price: number,
  ref: number | null,
  ceiling: number,
  floor: number
): HoseTone {
  if (!Number.isFinite(price) || price <= 0) return "neutral"
  if (ceiling > 0 && price >= ceiling) return "ceiling"
  if (floor > 0 && price <= floor) return "floor"
  if (ref != null && ref > 0) {
    if (price === ref) return "ref"
    return price > ref ? "gain" : "loss"
  }
  return "neutral"
}

const TONE_TEXT_CLASS: Record<HoseTone, string> = {
  ceiling: "text-ceiling",
  gain: "text-gain",
  ref: "text-ref",
  loss: "text-loss",
  floor: "text-floor",
  neutral: "text-ink",
}

function formatShareVol(vol: number | null | undefined): string {
  if (vol == null || !Number.isFinite(vol) || vol <= 0) return "—"
  if (vol >= 1_000_000) return `${(vol / 1_000_000).toFixed(2).replace(".", ",")} tr`
  if (vol >= 1_000) return `${(vol / 1_000).toFixed(1).replace(".", ",")} K`
  return fmt(Math.round(vol))
}

function formatDateShortVi(iso?: string | number | null): string {
  if (!iso) return "—"
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return String(iso).slice(0, 10)
  return d.toLocaleDateString("vi-VN", {
    timeZone: "Asia/Ho_Chi_Minh",
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  })
}

export interface StockDetailSeed {
  symbol: string
  name?: string
  sectorLabel?: string
  price?: number
  ref?: number | null
  ceiling?: number
  floor?: number
  changePct?: number | null
  rawVolume?: number
  valueBillion?: number
  foreign?: number
  foreignKnown?: boolean
}

export interface StockDetailContentProps {
  symbol: string
  seed?: StockDetailSeed
  starred?: boolean
  onToggleStar?: (symbol: string) => void
  onClose?: () => void
  onSelectPrev?: () => void
  onSelectNext?: () => void
  prevSymbol?: string | null
  nextSymbol?: string | null
  isModal?: boolean
}

export function StockDetailContent({
  symbol: rawSymbol,
  seed,
  starred: externalStarred,
  onToggleStar: externalOnToggleStar,
  onClose,
  onSelectPrev,
  onSelectNext,
  prevSymbol,
  nextSymbol,
  isModal = false,
}: StockDetailContentProps) {
  const symbol = rawSymbol.trim().toUpperCase()
  const [mainTab, setMainTab] = useState<MainPopupTab>("TRADING")
  const [tapeMode, setTapeMode] = useState<TradeTapeMode>("PRICE_STEP")

  // Local fallback watchlist state if not controlled by parent
  const [localStars, setLocalStars] = useState<string[]>(() => {
    if (typeof window === "undefined") return []
    try {
      const saved = window.localStorage.getItem(LOCAL_WATCHLIST_KEY)
      const parsed = saved ? (JSON.parse(saved) as unknown) : []
      return Array.isArray(parsed) ? parsed.map((s) => String(s).toUpperCase()) : []
    } catch {
      return []
    }
  })

  const { navigate } = useRouter()
  const isStarred =
    externalStarred !== undefined ? externalStarred : localStars.includes(symbol)

  const handleToggleWatchlist = async () => {
    if (externalOnToggleStar) {
      externalOnToggleStar(symbol)
      return
    }
    const exists = localStars.includes(symbol)
    const next = exists ? localStars.filter((s) => s !== symbol) : [symbol, ...localStars]
    setLocalStars(next)
    try {
      window.localStorage.setItem(LOCAL_WATCHLIST_KEY, JSON.stringify(next))
    } catch {
      // ignore
    }
    try {
      const lists = (await workspaceApi.watchlists()) as Array<{
        id: string
        name: string
        symbols: string[]
      }>
      const first = Array.isArray(lists) ? lists[0] : undefined
      if (first) {
        await workspaceApi.updateWatchlist(first.id, { symbols: next })
      } else {
        await workspaceApi.createWatchlist({ name: "Theo dõi chính", symbols: next })
      }
    } catch {
      // ignore offline
    }
  }

  const resource = useResource(
    () =>
      Promise.all([
        stockApi.quote(symbol).catch(() => null),
        stockApi.profile(symbol).catch(() => null),
        marketApi.superStocks(symbol).catch(() => null),
        stockApi.ohlcv(symbol, { limit: 65, interval: "1D" }).catch(() => null),
        stockApi.news(symbol).catch(() => null),
        marketApi.snapshot().catch(() => null),
      ]),
    [symbol]
  )

  const [quoteRes, profileRes, vsaRes, ohlcvRes, newsRes, snapshotRes] = (resource.data || []) as [
    ApiStockQuote | null,
    ApiStockProfile | null,
    SuperStocksPayload | null,
    ApiOhlcvCandle[] | { data?: ApiOhlcvCandle[] } | null,
    ApiNewsArticle[] | { news?: ApiNewsArticle[] } | null,
    ApiMarketSnapshot | null,
  ]

  const vsaItem: SuperStockItem | null =
    Array.isArray(vsaRes?.items) && vsaRes.items.length > 0 ? vsaRes.items[0] : null

  const snapshotStock = useMemo(() => {
    const list = Array.isArray(snapshotRes?.stocks) ? snapshotRes.stocks : []
    return list.find((st) => String(st.symbol ?? "").toUpperCase() === symbol)
  }, [snapshotRes, symbol])

  const candles = useMemo(() => {
    const rawList = Array.isArray(ohlcvRes)
      ? ohlcvRes
      : Array.isArray((ohlcvRes as { data?: ApiOhlcvCandle[] })?.data)
        ? ((ohlcvRes as { data?: ApiOhlcvCandle[] }).data ?? [])
        : []

    return rawList
      .map((c) => {
        const close = toVndPrice(c.close)
        const open = toVndPrice(c.open ?? c.close)
        const high = toVndPrice(c.high ?? Math.max(open, close))
        const low = toVndPrice(c.low ?? Math.min(open, close))
        const volume = Number(c.volume ?? 0)
        const rawTime = c.date ?? c.timestamp ?? c.time ?? ""
        const ts =
          typeof rawTime === "number"
            ? rawTime > 1e12
              ? rawTime
              : rawTime * 1000
            : Date.parse(String(rawTime))
        return {
          ts: Number.isFinite(ts) ? ts : 0,
          dateStr: formatDateShortVi(rawTime),
          open,
          high,
          low,
          close,
          volume: Number.isFinite(volume) && volume > 0 ? volume : 0,
        }
      })
      .filter((c) => c.close > 0)
      .sort((a, b) => a.ts - b.ts)
  }, [ohlcvRes])

  const newsList = useMemo(() => {
    const rawList = Array.isArray(newsRes)
      ? newsRes
      : Array.isArray((newsRes as { news?: ApiNewsArticle[] })?.news)
        ? ((newsRes as { news?: ApiNewsArticle[] }).news ?? [])
        : []
    return rawList.filter((item) => item && item.title).slice(0, 12)
  }, [newsRes])

  const initialStock = useMemo(() => {
    const latestCandle = candles[candles.length - 1]
    const prevCandle = candles.length >= 2 ? candles[candles.length - 2] : undefined

    const price =
      toVndPrice(quoteRes?.price ?? quoteRes?.close) ||
      toVndPrice(seed?.price) ||
      toVndPrice(snapshotStock?.price) ||
      latestCandle?.close ||
      0

    const rawRef =
      quoteRes?.ref ??
      quoteRes?.prior_close ??
      seed?.ref ??
      snapshotStock?.ref ??
      prevCandle?.close
    const ref = rawRef != null && Number(rawRef) > 0 ? toVndPrice(rawRef) : null

    const { ceiling, floor } = resolveCoherentBands(
      ref,
      price,
      toVndPrice(quoteRes?.ceiling ?? seed?.ceiling ?? snapshotStock?.ceiling),
      toVndPrice(quoteRes?.floor ?? seed?.floor ?? snapshotStock?.floor)
    )

    const rawChange =
      quoteRes?.change_pct ??
      quoteRes?.changePct ??
      quoteRes?.change_percent ??
      seed?.changePct ??
      snapshotStock?.changePercent ??
      snapshotStock?.change_pct
    const parsedChange = rawChange == null ? NaN : Number(rawChange)
    const changePct = Number.isFinite(parsedChange)
      ? Number(parsedChange.toFixed(2))
      : ref && ref > 0 && price > 0
        ? Number((((price - ref) / ref) * 100).toFixed(2))
        : 0

    const rawVol = Number(
      quoteRes?.volume ?? seed?.rawVolume ?? snapshotStock?.volume ?? latestCandle?.volume ?? 0
    )

    const rawName = String(
      profileRes?.name || seed?.name || snapshotStock?.name || symbol
    ).trim()

    const rawSector = String(
      vsaItem?.sector ||
        (profileRes?.industry && profileRes.industry !== "HOSE" ? profileRes.industry : "") ||
        snapshotStock?.industry ||
        seed?.sectorLabel ||
        "OTHER"
    )
    const sectorLabel =
      rawSector === "OTHER" || rawSector === "HOSE"
        ? seed?.sectorLabel || "Cổ phiếu HOSE"
        : formatSectorName(rawSector)

    return {
      symbol,
      name: rawName,
      sector: sectorLabel,
      price,
      changePct,
      ref,
      ceiling,
      floor,
      rawVolume: Number.isFinite(rawVol) && rawVol > 0 ? rawVol : 0,
      volume: formatShareVol(rawVol),
      risk: "Unknown" as const,
      rsi: vsaItem?.rsi ?? 0,
      momentum: 0,
      beneish: "UNKNOWN" as const,
      flow: 0,
      rs: 0,
      factor: "",
      weight: 0,
      pe: 0,
      foreign: Number(
        seed?.foreign ?? snapshotStock?.foreign_flow ?? snapshotStock?.foreignFlow ?? 0
      ),
      spark: [],
    }
  }, [quoteRes, profileRes, vsaItem, snapshotStock, seed, candles, symbol])

  const { stock: realtimeStock, orderbook, isLive } = useRealtimeStock(symbol, initialStock)
  const s = realtimeStock || initialStock

  const price = toVndPrice(s.price) || initialStock.price
  const ref = s.ref != null && s.ref > 0 ? toVndPrice(s.ref) : initialStock.ref
  const { ceiling, floor } = resolveCoherentBands(ref, price, s.ceiling, s.floor)
  const priceDiff = ref != null && ref > 0 && price > 0 ? price - ref : null
  const changePct =
    s.changePct != null && Number.isFinite(s.changePct)
      ? s.changePct
      : priceDiff != null && ref && ref > 0
        ? Number(((priceDiff / ref) * 100).toFixed(2))
        : 0
  const tone = resolveHoseTone(price, ref, ceiling, floor)

  // Compute rich technical & session metrics from real OHLCV + VSA + Snapshot
  const analytics = useMemo(() => {
    const latest = candles[candles.length - 1]
    const openPrice = toVndPrice(quoteRes?.open) || latest?.open || ref || price
    const highPrice = Math.max(
      price,
      toVndPrice(quoteRes?.high) || latest?.high || price
    )
    const lowPrice =
      price > 0
        ? Math.min(price, toVndPrice(quoteRes?.low) || latest?.low || price)
        : 0
    const vwapEst =
      highPrice > 0 && lowPrice > 0 && price > 0
        ? Math.round((highPrice + lowPrice + price) / 3)
        : price

    const closes = candles.map((c) => c.close)
    const calcMa = (n: number) => {
      if (closes.length < n) return null
      const slice = closes.slice(-n)
      return Math.round(slice.reduce((acc, v) => acc + v, 0) / slice.length)
    }
    const ma10 = calcMa(10)
    const ma20 = calcMa(20)
    const ma50 = calcMa(50)

    const high60 = candles.length > 0 ? Math.max(...candles.map((c) => c.high)) : highPrice
    const low60 = candles.length > 0 ? Math.min(...candles.map((c) => c.low)) : lowPrice
    const pos60Pct =
      high60 > low60 && price > 0
        ? Math.min(100, Math.max(0, Math.round(((price - low60) / (high60 - low60)) * 100)))
        : 50

    const vols10 = candles.slice(-10).map((c) => c.volume).filter((v) => v > 0)
    const volMa10 =
      vsaItem?.vol_ma10 ??
      (vols10.length > 0
        ? Math.round(vols10.reduce((acc, v) => acc + v, 0) / vols10.length)
        : 0)

    const rawVol = initialStock.rawVolume || latest?.volume || 0
    const volVsMa10Pct =
      volMa10 > 0 && rawVol > 0 ? Math.round(((rawVol - volMa10) / volMa10) * 100) : null

    const explicitVal = Number(seed?.valueBillion ?? snapshotStock?.tradingValue ?? 0)
    const valueBillion =
      explicitVal > 0
        ? explicitVal > 1_000_000
          ? explicitVal / 1_000_000_000
          : explicitVal
        : price > 0 && rawVol > 0
          ? (price * rawVol) / 1_000_000_000
          : 0

    const foreignBillion = Number(
      seed?.foreign ?? snapshotStock?.foreign_flow ?? snapshotStock?.foreignFlow ?? 0
    )
    const foreignSharesEst =
      price > 0 && Math.abs(foreignBillion) > 0.005
        ? Math.round((foreignBillion * 1_000_000_000) / price)
        : 0

    const bandPosPct =
      ceiling > floor && price > 0
        ? Math.min(100, Math.max(0, Math.round(((price - floor) / (ceiling - floor)) * 100)))
        : 50

    // Recent 25 sessions in reverse chronological order with full OHLC, VWAP & liquidity
    const historyRows = candles
      .map((c, idx) => {
        const prev = idx > 0 ? candles[idx - 1].close : c.open
        const diff = prev > 0 ? c.close - prev : 0
        const pct = prev > 0 ? (diff / prev) * 100 : 0
        const cTone = resolveHoseTone(c.close, prev, 0, 0)
        const vsMa10 = volMa10 > 0 ? Math.round((c.volume / volMa10) * 100) : null
        const sessionVwap =
          c.high > 0 && c.low > 0 ? Math.round((c.high + c.low + c.close) / 3) : c.close
        const sessionValBillion =
          sessionVwap > 0 && c.volume > 0 ? (sessionVwap * c.volume) / 1_000_000_000 : 0
        return {
          ...c,
          prev,
          diff,
          pct,
          cTone,
          vsMa10,
          sessionVwap,
          sessionValBillion,
        }
      })
      .reverse()
      .slice(0, 25)

    // Build Price-Step Volume Profile (Phân bổ khớp lệnh theo bước giá - chuẩn DNSE / FireAnt)
    const tickStep = getTickStep(price || 20_000)
    const stepLow = lowPrice > 0 ? lowPrice : Math.max(tickStep, price - 3 * tickStep)
    const stepHigh = highPrice >= stepLow ? highPrice : price + 3 * tickStep
    const rawSpanSteps = Math.max(1, Math.round((stepHigh - stepLow) / tickStep))
    const stride = rawSpanSteps > 8 ? Math.ceil(rawSpanSteps / 7) * tickStep : tickStep
    const stepPrices: number[] = []
    for (let p = stepHigh; p >= stepLow && stepPrices.length < 8; p -= stride) {
      stepPrices.push(p)
    }
    if (price > 0 && !stepPrices.includes(price)) {
      stepPrices.push(price)
      stepPrices.sort((a, b) => b - a)
    }

    const buyRatioPct = Math.min(88, Math.max(12, Math.round(vsaItem?.avg_buyforce ?? 50)))
    const activeBuyShares = Math.round(rawVol * (buyRatioPct / 100))
    const activeSellShares = Math.max(0, Math.round(rawVol) - activeBuyShares)

    // Weight distribution peaked around VWAP & Closing price
    const rawWeights = stepPrices.map((p) => {
      const distVwap = Math.abs(p - vwapEst) / Math.max(tickStep, stepHigh - stepLow)
      const distClose = Math.abs(p - price) / Math.max(tickStep, stepHigh - stepLow)
      return Math.max(0.14, Math.exp(-2.6 * distVwap) + 0.65 * Math.exp(-3.2 * distClose))
    })
    const weightSum = rawWeights.reduce((acc, w) => acc + w, 0) || 1
    const priceStepRows = stepPrices.map((p, idx) => {
      const shareRatio = rawWeights[idx] / weightSum
      const vol = Math.round((rawVol * shareRatio) / 100) * 100
      const pctOfTotal = Number((shareRatio * 100).toFixed(1))
      const levelBias = p >= vwapEst ? 6 : -6
      const levelBuyPct = Math.min(90, Math.max(10, buyRatioPct + levelBias))
      const buyVol = Math.round((vol * (levelBuyPct / 100)) / 100) * 100
      const sellVol = Math.max(0, vol - buyVol)
      const pTone = resolveHoseTone(p, ref, ceiling, floor)
      return {
        price: p,
        vol,
        buyVol,
        sellVol,
        buyPct: levelBuyPct,
        pctOfTotal,
        pTone,
        isCurrentPrice: p === price,
      }
    })
    const maxStepVol = Math.max(100, ...priceStepRows.map((r) => r.vol))

    return {
      openPrice,
      highPrice,
      lowPrice,
      vwapEst,
      ma10,
      ma20,
      ma50,
      high60,
      low60,
      pos60Pct,
      volMa10,
      rawVol,
      volVsMa10Pct,
      valueBillion,
      foreignBillion,
      foreignSharesEst,
      bandPosPct,
      historyRows,
      buyRatioPct,
      activeBuyShares,
      activeSellShares,
      priceStepRows,
      maxStepVol,
    }
  }, [
    candles,
    quoteRes,
    ref,
    price,
    ceiling,
    floor,
    vsaItem,
    initialStock.rawVolume,
    seed,
    snapshotStock,
  ])

  // Build 3-level Order Book + Cumulative Depth for Market Depth Chart (chuẩn SSI iBoard & DNSE)
  const bookLadder = useMemo(() => {
    const rawBids = (orderbook?.bids ?? [])
      .map((b) => ({ price: toVndPrice(b.price), volume: Math.round(Number(b.volume ?? 0)) }))
      .filter((b) => b.price > 0 && b.volume > 0)
      .slice(0, 3)

    const rawAsks = (orderbook?.asks ?? [])
      .map((a) => ({ price: toVndPrice(a.price), volume: Math.round(Number(a.volume ?? 0)) }))
      .filter((a) => a.price > 0 && a.volume > 0)
      .slice(0, 3)

    const hasLiveBook = rawBids.length > 0 || rawAsks.length > 0
    let bids = rawBids
    let asks = rawAsks
    let isLiveBook = !orderbook?.stale
    let isSynthetic = false

    if (!hasLiveBook) {
      isLiveBook = false
      isSynthetic = true
      const step = getTickStep(price || 20_000)
      const baseVol = Math.max(
        10_000,
        Math.round(((analytics.rawVol || 500_000) * 0.012) / 100) * 100
      )
      const buyForce = vsaItem?.avg_buyforce ?? 50
      bids = [0, 1, 2].map((i) => ({
        price: Math.max(floor || step, price - i * step),
        volume: Math.round((baseVol * (1 + i * 0.22) * (buyForce / 50)) / 100) * 100,
      }))
      asks = [0, 1, 2].map((i) => ({
        price: Math.min(ceiling || price + 3 * step, price + (i + 1) * step),
        volume: Math.round((baseVol * (0.95 + i * 0.18) * ((100 - buyForce) / 50)) / 100) * 100,
      }))
    }

    const totalBid = bids.reduce((acc, b) => acc + b.volume, 0)
    const totalAsk = asks.reduce((acc, a) => acc + a.volume, 0)
    const maxVol = Math.max(1000, ...bids.map((b) => b.volume), ...asks.map((a) => a.volume))
    const buyRatio =
      totalBid + totalAsk > 0 ? Math.round((totalBid / (totalBid + totalAsk)) * 100) : 50

    const b1 = bids[0]?.volume ?? 0
    const b2 = b1 + (bids[1]?.volume ?? 0)
    const b3 = b2 + (bids[2]?.volume ?? 0)
    const a1 = asks[0]?.volume ?? 0
    const a2 = a1 + (asks[1]?.volume ?? 0)
    const a3 = a2 + (asks[2]?.volume ?? 0)
    const maxCumDepth = Math.max(1000, b3, a3)

    return {
      isLiveBook,
      isSynthetic,
      bids,
      asks,
      totalBid,
      totalAsk,
      maxVol,
      buyRatio,
      cumBids: [
        { price: bids[2]?.price ?? 0, cumVol: b3, rawVol: bids[2]?.volume ?? 0 },
        { price: bids[1]?.price ?? 0, cumVol: b2, rawVol: bids[1]?.volume ?? 0 },
        { price: bids[0]?.price ?? 0, cumVol: b1, rawVol: bids[0]?.volume ?? 0 },
      ],
      cumAsks: [
        { price: asks[0]?.price ?? 0, cumVol: a1, rawVol: asks[0]?.volume ?? 0 },
        { price: asks[1]?.price ?? 0, cumVol: a2, rawVol: asks[1]?.volume ?? 0 },
        { price: asks[2]?.price ?? 0, cumVol: a3, rawVol: asks[2]?.volume ?? 0 },
      ],
      maxCumDepth,
    }
  }, [orderbook, price, floor, ceiling, analytics.rawVol, vsaItem?.avg_buyforce])

  const isVn30 = VN30_SET.has(symbol)
  const asOfLabel = quoteRes?.asOf ? formatDateShortVi(quoteRes.asOf) : null
  const openTone = resolveHoseTone(analytics.openPrice, ref, ceiling, floor)
  const vwapTone = resolveHoseTone(analytics.vwapEst, ref, ceiling, floor)
  const lowTone = resolveHoseTone(analytics.lowPrice, ref, ceiling, floor)
  const highTone = resolveHoseTone(analytics.highPrice, ref, ceiling, floor)

  return (
    <div className="bg-surface text-ink rounded-[12px] border border-line overflow-hidden shadow-[0_24px_70px_rgba(18,26,23,0.22)]">
      {/* 1. HEADER CHUẨN SSI iBOARD & DNSE: TẦNG ĐỊNH DANH + TẦNG THÔNG SỐ PHIÊN + THANH TAB CHÍNH */}
      <div className="bg-surface border-b border-line">
        {/* Tầng 1A: Mã CK · Sàn · Tên Công ty · Ngành (Trái) & Điều hướng Mã / Theo dõi / Đặt lệnh / Đóng (Phải) */}
        <div className="px-5 pt-3 pb-2 flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-2 min-w-0">
            <span
              className={`font-mono text-[22px] font-bold tracking-tight leading-none ${TONE_TEXT_CLASS[tone]}`}
            >
              {symbol}
            </span>
            <span className="font-mono text-[11.5px] font-semibold text-secondary">
              (HOSE)
            </span>
            <span className="text-[13.5px] font-semibold text-ink truncate max-w-[340px]">
              {s.name && s.name.toUpperCase() !== symbol ? s.name : `Cổ phiếu ${symbol}`}
            </span>
            {isVn30 && (
              <span className="rounded bg-ink text-surface px-1.5 py-0.5 font-mono text-[10px] font-semibold">
                VN30
              </span>
            )}
            <span className="rounded bg-soft px-2 py-0.5 text-[11px] font-medium text-secondary border border-line/70">
              {s.sector}
            </span>
            <Pill tone={isLive ? "gain" : "neutral"}>
              <span
                className={`h-1.5 w-1.5 rounded-full ${
                  isLive ? "bg-gain animate-pulse" : "bg-muted"
                }`}
              />
              {isLive ? "Trực tiếp DNSE" : asOfLabel ? `Chốt ${asOfLabel}` : "Snapshot"}
            </Pill>
          </div>

          <div className="flex items-center gap-2 shrink-0">
            {(onSelectPrev || onSelectNext) && (
              <div className="inline-flex items-center rounded-[6px] border border-line bg-soft/60 p-0.5">
                <button
                  type="button"
                  disabled={!prevSymbol}
                  onClick={onSelectPrev}
                  title={prevSymbol ? `Mã trước: ${prevSymbol} (Phím ←)` : "Đầu danh sách"}
                  className="h-7 px-2 rounded-[4px] font-mono text-[11.5px] text-secondary hover:bg-surface hover:text-ink disabled:opacity-35 transition-colors"
                >
                  ‹ {prevSymbol || "Trước"}
                </button>
                <button
                  type="button"
                  disabled={!nextSymbol}
                  onClick={onSelectNext}
                  title={nextSymbol ? `Mã tiếp: ${nextSymbol} (Phím →)` : "Cuối danh sách"}
                  className="h-7 px-2 rounded-[4px] font-mono text-[11.5px] text-secondary hover:bg-surface hover:text-ink disabled:opacity-35 transition-colors"
                >
                  {nextSymbol || "Tiếp"} ›
                </button>
              </div>
            )}

            <Button
              variant="secondary"
              className="!h-8 !px-3 !text-[12px]"
              onClick={() => void handleToggleWatchlist()}
            >
              <span className={isStarred ? "text-gold mr-1" : "text-muted mr-1"}>
                {isStarred ? "★" : "☆"}
              </span>
              {isStarred ? "Đang theo dõi" : "Theo dõi"}
            </Button>

            <Link to={`/trade?symbol=${symbol}`} onClick={onClose}>
              <Button variant="primary" className="!h-8 !px-3.5 !text-[12px]">
                Đặt lệnh {symbol} →
              </Button>
            </Link>

            {isModal && onClose && (
              <button
                type="button"
                onClick={onClose}
                aria-label="Đóng cửa sổ chi tiết cổ phiếu (Esc)"
                title="Đóng (Esc)"
                className="inline-flex h-8 w-8 items-center justify-center rounded-[6px] border border-line bg-soft/60 text-[14px] text-secondary hover:bg-ink hover:text-surface transition-colors"
              >
                ✕
              </button>
            )}
          </div>
        </div>

        {/* Tầng 1B: Dải Thị giá & Thông số Phiên ngang chuẩn SSI iBoard */}
        <div className="px-5 py-2.5 bg-soft/35 border-y border-line/80 flex flex-wrap items-center justify-between gap-x-6 gap-y-2">
          {/* Cụm giá lớn + chênh lệch */}
          <div className="flex items-center gap-3">
            <span
              className={`font-mono text-[28px] font-bold tracking-tight leading-none tnum ${TONE_TEXT_CLASS[tone]}`}
            >
              {price > 0 ? fmt(price) : "—"}
            </span>
            <div
              className={`font-mono text-[11.5px] font-semibold leading-tight tnum ${TONE_TEXT_CLASS[tone]}`}
            >
              <div>
                {priceDiff != null && priceDiff !== 0
                  ? `${priceDiff > 0 ? "+" : ""}${fmt(priceDiff)}`
                  : "0"}
              </div>
              <div>{changePct === 0 ? "0,00%" : fmtPct(changePct)}</div>
            </div>
          </div>

          {/* Dải chỉ số phiên ngang chuẩn SSI iBoard */}
          <div className="flex flex-wrap items-center gap-x-6 gap-y-1.5 font-mono text-[12px] tnum">
            <div>
              <div className="font-sans text-[10px] font-semibold uppercase tracking-wider text-muted">
                Mở cửa / Trung bình
              </div>
              <div className="mt-0.5 font-semibold">
                <span className={TONE_TEXT_CLASS[openTone]}>
                  {analytics.openPrice > 0 ? fmt(analytics.openPrice) : "—"}
                </span>
                <span className="text-muted mx-1">/</span>
                <span className={TONE_TEXT_CLASS[vwapTone]}>
                  {analytics.vwapEst > 0 ? fmt(analytics.vwapEst) : "—"}
                </span>
              </div>
            </div>

            <div>
              <div className="font-sans text-[10px] font-semibold uppercase tracking-wider text-muted">
                Thấp / Cao phiên
              </div>
              <div className="mt-0.5 font-semibold">
                <span className={TONE_TEXT_CLASS[lowTone]}>
                  {analytics.lowPrice > 0 ? fmt(analytics.lowPrice) : "—"}
                </span>
                <span className="text-muted mx-1">/</span>
                <span className={TONE_TEXT_CLASS[highTone]}>
                  {analytics.highPrice > 0 ? fmt(analytics.highPrice) : "—"}
                </span>
              </div>
            </div>

            <div>
              <div className="font-sans text-[10px] font-semibold uppercase tracking-wider text-muted">
                Trần
              </div>
              <div className="mt-0.5 font-semibold text-ceiling">
                {ceiling > 0 ? fmt(ceiling) : "—"}
              </div>
            </div>

            <div>
              <div className="font-sans text-[10px] font-semibold uppercase tracking-wider text-muted">
                Sàn
              </div>
              <div className="mt-0.5 font-semibold text-floor">
                {floor > 0 ? fmt(floor) : "—"}
              </div>
            </div>

            <div>
              <div className="font-sans text-[10px] font-semibold uppercase tracking-wider text-muted">
                Tham chiếu
              </div>
              <div className="mt-0.5 font-semibold text-ref">
                {ref ? fmt(ref) : "—"}
              </div>
            </div>

            <div>
              <div className="font-sans text-[10px] font-semibold uppercase tracking-wider text-muted">
                Tổng KL khớp
              </div>
              <div className="mt-0.5 font-semibold text-ink">
                {analytics.rawVol > 0 ? fmt(Math.round(analytics.rawVol)) : "—"}
              </div>
            </div>

            <div>
              <div className="font-sans text-[10px] font-semibold uppercase tracking-wider text-muted">
                GTGD
              </div>
              <div className="mt-0.5 font-semibold text-ink">
                {analytics.valueBillion > 0
                  ? `${analytics.valueBillion.toFixed(1).replace(".", ",")} tỷ`
                  : "—"}
              </div>
            </div>

            <div>
              <div className="font-sans text-[10px] font-semibold uppercase tracking-wider text-muted">
                ĐTNN Ròng
              </div>
              <div
                className={`mt-0.5 font-semibold ${
                  analytics.foreignBillion > 0
                    ? "text-gain"
                    : analytics.foreignBillion < 0
                      ? "text-loss"
                      : "text-ink"
                }`}
              >
                {analytics.foreignBillion > 0 ? "+" : ""}
                {analytics.foreignBillion.toFixed(2).replace(".", ",")} tỷ
              </div>
            </div>
          </div>
        </div>

        {/* Tầng 1C: Thanh Tab Ngang Toàn Popup chuẩn SSI iBoard */}
        <div className="px-5 flex items-center justify-between gap-4 bg-surface" role="tablist">
          <div className="flex items-center gap-6 overflow-x-auto">
            {(
              [
                ["TRADING", "Giao dịch"],
                ["STATISTICS", "Thống kê lịch sử (25P)"],
                ["VSA_QUANT", "Phân tích VSA & Kỹ thuật"],
                ["FILINGS", `BCTC & Tin tức (${newsList.length})`],
              ] as [MainPopupTab, string][]
            ).map(([key, label]) => {
              const active = mainTab === key
              return (
                <button
                  key={key}
                  type="button"
                  role="tab"
                  aria-selected={active}
                  onClick={() => setMainTab(key)}
                  className={`relative py-2.5 text-[13px] font-semibold whitespace-nowrap transition-colors ${
                    active ? "text-ink" : "text-muted hover:text-ink"
                  }`}
                >
                  {label}
                  {active && (
                    <span className="absolute inset-x-0 bottom-0 h-[2.5px] rounded-full bg-ink" />
                  )}
                </button>
              )
            })}
          </div>

          <div className="hidden md:flex items-center gap-4 font-mono text-[11px] text-secondary tnum shrink-0">
            <span>
              MA20:{" "}
              <strong
                className={
                  analytics.ma20 && price >= analytics.ma20 ? "text-gain" : "text-loss"
                }
              >
                {analytics.ma20 ? fmt(analytics.ma20) : "—"}
              </strong>
            </span>
            <span>·</span>
            <span>
              RSI(14):{" "}
              <strong className="text-ink">
                {vsaItem?.rsi != null ? vsaItem.rsi.toFixed(1).replace(".", ",") : "—"}
              </strong>
            </span>
            <span>·</span>
            <span>
              Điểm VSA:{" "}
              <strong className={(vsaItem?.vsa_score ?? 0) >= 60 ? "text-gain" : "text-ink"}>
                {vsaItem?.vsa_score != null ? vsaItem.vsa_score.toFixed(1).replace(".", ",") : "—"}
              </strong>
              /100
            </span>
          </div>
        </div>
      </div>

      {/* 2. NỘI DUNG TAB 1: "GIAO DỊCH" — BỐ CỤC 3 CỘT CHUẨN SSI iBOARD & DNSE */}
      {mainTab === "TRADING" && (
        <div className="grid grid-cols-1 lg:grid-cols-12 divide-y lg:divide-y-0 lg:divide-x divide-line bg-surface">
          {/* CỘT 1 (7/12): BIỂU ĐỒ KỸ THUẬT TRADINGVIEW PRO CHIẾM TRỌN CHIỀU CAO */}
          <div className="lg:col-span-7 p-2 bg-surface flex flex-col justify-between">
            <TradingViewChart
              symbol={symbol}
              seedCandles={candles.map((c) => ({
                timestamp: c.ts,
                open: c.open,
                high: c.high,
                low: c.low,
                close: c.close,
                volume: c.volume,
              }))}
              precision={0}
              height={isModal ? 505 : 545}
              trading={{
                symbol,
                stockName: s.name,
                exchange: "HOSE",
                currentPrice: price,
                refPrice: ref ?? price,
                ceilingPrice: ceiling,
                floorPrice: floor,
                isLive,
                orderSide: "BUY",
                orderType: "LO",
                orderPrice: price,
                orderQuantity: 100,
                hasDraftOrder: false,
                bestBidPrice: bookLadder.bids[0]?.price ?? 0,
                bestAskPrice: bookLadder.asks[0]?.price ?? 0,
                onPickPriceFromChart: (picked) => {
                  onClose?.()
                  navigate(`/trade?symbol=${symbol}&price=${Math.round(picked)}`)
                },
              }}
            />
          </div>

          {/* CỘT 2 + 3 (5/12 CHIA ĐÔI): ĐỘ SÂU THỊ TRƯỜNG (TRÁI) & KHỚP LỆNH / BƯỚC GIÁ (PHẢI) */}
          <div className="lg:col-span-5 grid grid-cols-1 sm:grid-cols-2 divide-y sm:divide-y-0 sm:divide-x divide-line bg-surface">
            {/* CỘT 2A: ĐỘ SÂU THỊ TRƯỜNG */}
            <div className="flex flex-col justify-between">
              <div>
                <div className="px-3 py-2 border-b border-line bg-soft/40 flex items-center justify-between">
                  <span className="text-[12px] font-semibold text-ink">
                    Độ sâu thị trường
                  </span>
                  <span className="font-mono text-[10px] text-muted">
                    {bookLadder.isLiveBook ? "Trực tiếp" : "Bước giá chuẩn"}
                  </span>
                </div>

                {/* Bảng 3 Bước giá Đặt Mua / Đặt Bán chuẩn SSI iBoard & DNSE */}
                <div>
                  <div className="grid grid-cols-4 px-2.5 py-1.5 border-b border-line bg-soft/20 text-[10.5px] font-medium text-muted">
                    <span className="text-left">KL</span>
                    <span className="text-right pr-1.5">Giá mua</span>
                    <span className="text-left pl-1.5">Giá bán</span>
                    <span className="text-right">KL</span>
                  </div>

                  <div className="divide-y divide-line/60">
                    {[0, 1, 2].map((lvl) => {
                      const bid = bookLadder.bids[lvl]
                      const ask = bookLadder.asks[lvl]
                      const bidTone = bid
                        ? resolveHoseTone(bid.price, ref, ceiling, floor)
                        : "neutral"
                      const askTone = ask
                        ? resolveHoseTone(ask.price, ref, ceiling, floor)
                        : "neutral"
                      const bidW = bid
                        ? Math.min(100, Math.round((bid.volume / bookLadder.maxVol) * 100))
                        : 0
                      const askW = ask
                        ? Math.min(100, Math.round((ask.volume / bookLadder.maxVol) * 100))
                        : 0

                      return (
                        <div
                          key={lvl}
                          className="grid grid-cols-2 divide-x divide-line/70 h-[29px] font-mono text-[11.5px] tnum"
                        >
                          <div className="relative px-2.5 flex items-center justify-between overflow-hidden">
                            {bidW > 0 && (
                              <div
                                className="absolute inset-y-0.5 right-0 bg-gain/15 transition-all"
                                style={{ width: `${bidW}%` }}
                              />
                            )}
                            <span className="relative z-10 text-ink">
                              {bid ? fmt(bid.volume) : "—"}
                            </span>
                            <span
                              className={`relative z-10 font-semibold ${TONE_TEXT_CLASS[bidTone]}`}
                            >
                              {bid ? fmt(bid.price) : "—"}
                            </span>
                          </div>

                          <div className="relative px-2.5 flex items-center justify-between overflow-hidden">
                            {askW > 0 && (
                              <div
                                className="absolute inset-y-0.5 left-0 bg-loss/15 transition-all"
                                style={{ width: `${askW}%` }}
                              />
                            )}
                            <span
                              className={`relative z-10 font-semibold ${TONE_TEXT_CLASS[askTone]}`}
                            >
                              {ask ? fmt(ask.price) : "—"}
                            </span>
                            <span className="relative z-10 text-ink">
                              {ask ? fmt(ask.volume) : "—"}
                            </span>
                          </div>
                        </div>
                      )
                    })}
                  </div>

                  {/* Thanh tổng Dư mua / Dư bán ngay dưới 3 bước giá */}
                  <div className="px-2.5 py-2 border-t border-line bg-soft/15">
                    <div className="h-1.5 w-full rounded-full bg-soft overflow-hidden flex">
                      <div
                        className="h-full bg-gain transition-all"
                        style={{ width: `${bookLadder.buyRatio}%` }}
                      />
                      <div className="h-full bg-loss flex-1" />
                    </div>
                    <div className="mt-1.5 flex items-center justify-between font-mono text-[11px] tnum">
                      <span className="text-secondary">
                        Dư mua:{" "}
                        <strong className="text-ink font-semibold">
                          {formatShareVol(bookLadder.totalBid)}
                        </strong>
                      </span>
                      <span className="text-secondary">
                        Dư bán:{" "}
                        <strong className="text-ink font-semibold">
                          {formatShareVol(bookLadder.totalAsk)}
                        </strong>
                      </span>
                    </div>
                  </div>
                </div>

                {/* Biểu đồ Độ sâu thị trường (Staircase Step-Chart chuẩn SSI iBoard) */}
                <div className="px-2.5 py-2.5 border-t border-line">
                  <div className="text-[11px] font-semibold text-secondary text-center mb-2">
                    Biểu đồ độ sâu thị trường
                  </div>

                  <div className="relative h-[108px] pt-3 pb-1 px-1 rounded-[6px] bg-soft/30 border border-line/60 flex flex-col justify-end">
                    <span className="absolute top-1 left-1.5 font-mono text-[9.5px] text-muted tnum">
                      {formatShareVol(bookLadder.maxCumDepth)}
                    </span>
                    <span className="absolute top-1 right-1.5 font-mono text-[9.5px] text-muted tnum">
                      Cầu {bookLadder.buyRatio}% · Cung {100 - bookLadder.buyRatio}%
                    </span>

                    <div className="grid grid-cols-6 items-end h-[68px] gap-0.5 px-1">
                      {bookLadder.cumBids.map((b, i) => {
                        const hPct = Math.max(
                          14,
                          Math.round((b.cumVol / bookLadder.maxCumDepth) * 100)
                        )
                        return (
                          <div
                            key={`cb-${i}`}
                            className="h-full flex flex-col justify-end"
                            title={`Giá đặt mua ${fmt(b.price)}: Lũy kế ${fmt(b.cumVol)} CP`}
                          >
                            <div
                              className="w-full bg-gain/75 border-t-2 border-gain rounded-t-[2px] transition-all"
                              style={{ height: `${hPct}%` }}
                            />
                          </div>
                        )
                      })}
                      {bookLadder.cumAsks.map((a, i) => {
                        const hPct = Math.max(
                          14,
                          Math.round((a.cumVol / bookLadder.maxCumDepth) * 100)
                        )
                        return (
                          <div
                            key={`ca-${i}`}
                            className="h-full flex flex-col justify-end"
                            title={`Giá đặt bán ${fmt(a.price)}: Lũy kế ${fmt(a.cumVol)} CP`}
                          >
                            <div
                              className="w-full bg-loss/75 border-t-2 border-loss rounded-t-[2px] transition-all"
                              style={{ height: `${hPct}%` }}
                            />
                          </div>
                        )
                      })}
                    </div>

                    <div className="mt-1 grid grid-cols-6 gap-0.5 text-center font-mono text-[9px] text-secondary tnum">
                      {bookLadder.cumBids.map((b, i) => (
                        <span key={`cbl-${i}`} className="truncate">
                          {b.price > 0 ? (b.price / 1000).toFixed(2) : "—"}
                        </span>
                      ))}
                      {bookLadder.cumAsks.map((a, i) => (
                        <span key={`cal-${i}`} className="truncate">
                          {a.price > 0 ? (a.price / 1000).toFixed(2) : "—"}
                        </span>
                      ))}
                    </div>
                  </div>
                </div>
              </div>

              {/* Lưới Thống kê Phiên & Khối ngoại Đa cột Gọn Chuẩn DNSE */}
              <div className="px-2.5 py-2.5 border-t border-line bg-soft/20 font-mono text-[11px] tnum space-y-2">
                <div className="grid grid-cols-3 gap-1.5 pb-2 border-b border-line/70">
                  <div>
                    <span className="font-sans text-[10.5px] text-secondary">Sàn: </span>
                    <strong className="text-floor font-semibold">
                      {floor > 0 ? fmt(floor) : "—"}
                    </strong>
                  </div>
                  <div className="text-center">
                    <span className="font-sans text-[10.5px] text-secondary">TC: </span>
                    <strong className="text-ref font-semibold">{ref ? fmt(ref) : "—"}</strong>
                  </div>
                  <div className="text-right">
                    <span className="font-sans text-[10.5px] text-secondary">Trần: </span>
                    <strong className="text-ceiling font-semibold">
                      {ceiling > 0 ? fmt(ceiling) : "—"}
                    </strong>
                  </div>

                  <div>
                    <span className="font-sans text-[10.5px] text-secondary">Thấp: </span>
                    <strong className={`font-semibold ${TONE_TEXT_CLASS[lowTone]}`}>
                      {analytics.lowPrice > 0 ? fmt(analytics.lowPrice) : "—"}
                    </strong>
                  </div>
                  <div className="text-center">
                    <span className="font-sans text-[10.5px] text-secondary">TB: </span>
                    <strong className={`font-semibold ${TONE_TEXT_CLASS[vwapTone]}`}>
                      {analytics.vwapEst > 0 ? fmt(analytics.vwapEst) : "—"}
                    </strong>
                  </div>
                  <div className="text-right">
                    <span className="font-sans text-[10.5px] text-secondary">Cao: </span>
                    <strong className={`font-semibold ${TONE_TEXT_CLASS[highTone]}`}>
                      {analytics.highPrice > 0 ? fmt(analytics.highPrice) : "—"}
                    </strong>
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-x-2.5 gap-y-1.5 text-[11px] whitespace-nowrap">
                  <div className="flex items-center justify-between gap-1">
                    <span className="font-sans text-secondary">KL khớp</span>
                    <strong className="text-ink font-semibold">
                      {formatShareVol(analytics.rawVol)}
                    </strong>
                  </div>
                  <div className="flex items-center justify-between gap-1">
                    <span className="font-sans text-secondary">GT khớp</span>
                    <strong className="text-ink font-semibold">
                      {analytics.valueBillion > 0
                        ? `${analytics.valueBillion.toFixed(1).replace(".", ",")} tỷ`
                        : "—"}
                    </strong>
                  </div>

                  <div className="flex items-center justify-between gap-1">
                    <span className="font-sans text-secondary">KL TB 10P</span>
                    <span className="text-ink font-medium">
                      {formatShareVol(analytics.volMa10)}
                    </span>
                  </div>
                  <div className="flex items-center justify-between gap-1">
                    <span className="font-sans text-secondary">NN ròng</span>
                    <strong
                      className={`font-semibold ${
                        analytics.foreignBillion > 0
                          ? "text-gain"
                          : analytics.foreignBillion < 0
                            ? "text-loss"
                            : "text-ink"
                      }`}
                    >
                      {analytics.foreignBillion > 0 ? "+" : ""}
                      {analytics.foreignBillion.toFixed(2).replace(".", ",")} tỷ
                    </strong>
                  </div>

                  <div className="flex items-center justify-between gap-1">
                    <span className="font-sans text-secondary">KL NN</span>
                    <span className="text-ink">
                      {analytics.foreignSharesEst !== 0
                        ? `${analytics.foreignSharesEst > 0 ? "+" : ""}${formatShareVol(
                            Math.abs(analytics.foreignSharesEst)
                          )}`
                        : "0"}
                    </span>
                  </div>
                  <div className="flex items-center justify-between gap-1">
                    <span className="font-sans text-secondary">Tỷ trọng NN</span>
                    <span className="text-ink">
                      {analytics.valueBillion > 0
                        ? `${Math.min(
                            100,
                            Math.round(
                              (Math.abs(analytics.foreignBillion) / analytics.valueBillion) * 100
                            )
                          )}% GT`
                        : "—"}
                    </span>
                  </div>
                </div>
              </div>
            </div>

            {/* CỘT 2B: KHỚP LỆNH & PHÂN BỔ BƯỚC GIÁ */}
            <div className="flex flex-col justify-between">
              <div>
                <div className="px-3 py-1.5 border-b border-line bg-soft/40 flex items-center justify-between">
                  <span className="text-[12px] font-semibold text-ink">Khớp lệnh</span>
                  <div className="inline-flex items-center rounded-[5px] bg-soft p-0.5 border border-line">
                    <button
                      type="button"
                      onClick={() => setTapeMode("PRICE_STEP")}
                      className={`h-5 px-2 rounded-[3px] text-[10.5px] font-medium transition-colors ${
                        tapeMode === "PRICE_STEP"
                          ? "bg-ink text-surface"
                          : "text-secondary hover:text-ink"
                      }`}
                    >
                      Bước giá
                    </button>
                    <button
                      type="button"
                      onClick={() => setTapeMode("RECENT_SESSIONS")}
                      className={`h-5 px-2 rounded-[3px] text-[10.5px] font-medium transition-colors ${
                        tapeMode === "RECENT_SESSIONS"
                          ? "bg-ink text-surface"
                          : "text-secondary hover:text-ink"
                      }`}
                    >
                      Theo phiên
                    </button>
                  </div>
                </div>

                {/* Dải Mua chủ động (M) | Bán chủ động (B) chuẩn SSI iBoard */}
                <div className="px-2.5 py-2 border-b border-line bg-soft/15">
                  <div className="flex items-center justify-between font-mono text-[11px] tnum whitespace-nowrap">
                    <span className="text-gain font-semibold">
                      M: {formatShareVol(analytics.activeBuyShares)} ({analytics.buyRatioPct}%)
                    </span>
                    <span className="text-loss font-semibold">
                      B: {formatShareVol(analytics.activeSellShares)} (
                      {100 - analytics.buyRatioPct}%)
                    </span>
                  </div>
                  <div className="mt-1 h-1.5 w-full rounded-full bg-soft overflow-hidden flex">
                    <div
                      className="h-full bg-gain transition-all"
                      style={{ width: `${analytics.buyRatioPct}%` }}
                    />
                    <div className="h-full bg-loss flex-1" />
                  </div>
                </div>

                {/* Chế độ 1: Phân bổ Khớp lệnh theo Bước giá */}
                {tapeMode === "PRICE_STEP" ? (
                  <div>
                    <div className="grid grid-cols-12 px-2.5 py-1.5 border-b border-line bg-soft/20 text-[10px] font-medium text-muted">
                      <span className="col-span-3 text-left">Mức giá</span>
                      <span className="col-span-4 text-right pr-2">KL khớp</span>
                      <span className="col-span-5 text-right">Tỷ trọng Mua/Bán</span>
                    </div>

                    <div className="divide-y divide-line/50">
                      {analytics.priceStepRows.map((row) => {
                        const widthPct = Math.max(
                          8,
                          Math.round((row.vol / analytics.maxStepVol) * 100)
                        )
                        return (
                          <div
                            key={row.price}
                            className={`grid grid-cols-12 items-center px-2.5 h-[33px] font-mono text-[11px] tnum ${
                              row.isCurrentPrice ? "bg-soft/55" : ""
                            }`}
                          >
                            <div className="col-span-3 flex items-center gap-1">
                              <span
                                className={`font-semibold ${TONE_TEXT_CLASS[row.pTone]}`}
                              >
                                {fmt(row.price)}
                              </span>
                              {row.isCurrentPrice && (
                                <span
                                  className="h-1.5 w-1.5 rounded-full bg-ink shrink-0"
                                  title="Giá khớp hiện tại"
                                />
                              )}
                            </div>

                            <div className="col-span-4 text-right pr-2 text-ink font-medium">
                              {formatShareVol(row.vol)}
                            </div>

                            <div className="col-span-5 flex items-center gap-1.5">
                              <div className="flex-1 h-2.5 rounded-[2px] bg-soft overflow-hidden flex justify-end">
                                <div
                                  className="h-full flex overflow-hidden rounded-[2px]"
                                  style={{ width: `${widthPct}%` }}
                                >
                                  <div
                                    className="h-full bg-gain/80"
                                    style={{ width: `${row.buyPct}%` }}
                                  />
                                  <div className="h-full bg-loss/80 flex-1" />
                                </div>
                              </div>
                              <span className="w-9 text-right text-[10px] text-secondary">
                                {row.pctOfTotal.toFixed(0)}%
                              </span>
                            </div>
                          </div>
                        )
                      })}
                    </div>
                  </div>
                ) : (
                  /* Chế độ 2: Bảng Khớp lệnh 12 Phiên gần nhất */
                  <div className="max-h-[310px] overflow-y-auto">
                    <table className="w-full border-separate border-spacing-0 font-mono text-[11px] tnum">
                      <thead>
                        <tr className="bg-soft/40 font-sans text-[10px] font-medium text-muted">
                          <th className="sticky top-0 bg-soft py-1.5 pl-2.5 pr-1 text-left border-b border-line">
                            Ngày
                          </th>
                          <th className="sticky top-0 bg-soft py-1.5 px-1 text-right border-b border-line">
                            Giá
                          </th>
                          <th className="sticky top-0 bg-soft py-1.5 px-1 text-right border-b border-line">
                            +/- (%)
                          </th>
                          <th className="sticky top-0 bg-soft py-1.5 pr-2.5 pl-1 text-right border-b border-line">
                            KL
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {analytics.historyRows.slice(0, 12).map((r) => (
                          <tr key={r.dateStr} className="hover:bg-soft/40">
                            <td className="py-1.5 pl-2.5 pr-1 border-b border-line/60 text-secondary">
                              {r.dateStr.slice(0, 5)}
                            </td>
                            <td
                              className={`py-1.5 px-1 border-b border-line/60 text-right font-semibold ${TONE_TEXT_CLASS[r.cTone]}`}
                            >
                              {fmt(r.close)}
                            </td>
                            <td
                              className={`py-1.5 px-1 border-b border-line/60 text-right ${TONE_TEXT_CLASS[r.cTone]}`}
                            >
                              {r.diff === 0 ? "0,0%" : fmtPct(r.pct)}
                            </td>
                            <td className="py-1.5 pr-2.5 pl-1 border-b border-line/60 text-right text-ink">
                              {formatShareVol(r.volume)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>

              {/* Chân Cột 2B: Lưới Tóm tắt Định lượng VSA & Hạn mức Đi vốn An toàn */}
              <div className="px-2.5 py-2.5 border-t border-line bg-soft/20 font-mono text-[11px] tnum space-y-1.5">
                <div className="flex items-center justify-between">
                  <span className="font-sans text-[10.5px] font-semibold uppercase tracking-wider text-muted">
                    Tín hiệu VSA & Đi vốn
                  </span>
                  <button
                    type="button"
                    onClick={() => setMainTab("VSA_QUANT")}
                    className="font-sans text-[10.5px] font-medium text-mineral hover:underline"
                  >
                    Chi tiết →
                  </button>
                </div>

                <div className="grid grid-cols-2 gap-x-2 gap-y-1 text-[11px]">
                  <div className="flex items-center justify-between">
                    <span className="font-sans text-secondary">Điểm VSA</span>
                    <strong
                      className={
                        (vsaItem?.vsa_score ?? 0) >= 60 ? "text-gain" : "text-ink"
                      }
                    >
                      {vsaItem?.vsa_score != null
                        ? `${vsaItem.vsa_score.toFixed(1).replace(".", ",")}/100`
                        : "—"}
                    </strong>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="font-sans text-secondary">Áp đảo 20P</span>
                    <strong className="text-ink">{vsaItem?.tien_ap_dao ?? 0}%</strong>
                  </div>

                  <div className="flex items-center justify-between">
                    <span className="font-sans text-secondary">Kênh 60P</span>
                    <span className="text-ink">{analytics.pos60Pct}% kênh</span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="font-sans text-secondary">KL mua tối đa</span>
                    <strong className="text-teal">
                      {vsaItem?.max_buy ? formatShareVol(vsaItem.max_buy) : "—"}
                    </strong>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 3. NỘI DUNG TAB 2: "THỐNG KÊ LỊCH SỬ (25 PHIÊN)" */}
      {mainTab === "STATISTICS" && (
        <div className="bg-surface">
          <div className="px-5 py-3 border-b border-line bg-soft/25 flex flex-wrap items-center justify-between gap-2 text-[12px]">
            <span className="font-medium text-secondary">
              Thống kê lịch sử khớp lệnh, biên độ Mở / Cao / Thấp / Trung bình và đột biến thanh khoản 25 phiên gần nhất của{" "}
              <strong className="text-ink">{symbol}</strong>
            </span>
            <span className="font-mono text-[11.5px] text-secondary tnum">
              Đỉnh 60P: <strong className="text-gain">{fmt(analytics.high60)}</strong> · Đáy 60P:{" "}
              <strong className="text-loss">{fmt(analytics.low60)}</strong> · KL TB 10P:{" "}
              <strong className="text-ink">{formatShareVol(analytics.volMa10)} CP</strong>
            </span>
          </div>

          <div className="max-h-[465px] overflow-y-auto">
            <table className="w-full border-separate border-spacing-0 text-[12px]">
              <thead>
                <tr className="bg-soft text-[10.5px] font-semibold uppercase tracking-wider text-muted">
                  <th className="sticky top-0 bg-soft border-b border-line py-2.5 pl-5 pr-3 text-left">
                    Ngày GD
                  </th>
                  <th className="sticky top-0 bg-soft border-b border-line py-2.5 px-3 text-right">
                    Đóng cửa
                  </th>
                  <th className="sticky top-0 bg-soft border-b border-line py-2.5 px-3 text-right">
                    Thay đổi (+/- & %)
                  </th>
                  <th className="sticky top-0 bg-soft border-b border-line py-2.5 px-3 text-right">
                    KL khớp lệnh (CP)
                  </th>
                  <th className="sticky top-0 bg-soft border-b border-line py-2.5 px-3 text-right">
                    GTGD ước tính (Tỷ ₫)
                  </th>
                  <th className="sticky top-0 bg-soft border-b border-line py-2.5 px-3 text-right">
                    So với MA10
                  </th>
                  <th className="sticky top-0 bg-soft border-b border-line py-2.5 px-3 text-right">
                    Mở cửa
                  </th>
                  <th className="sticky top-0 bg-soft border-b border-line py-2.5 px-3 text-right">
                    Cao nhất
                  </th>
                  <th className="sticky top-0 bg-soft border-b border-line py-2.5 px-3 text-right">
                    Thấp nhất
                  </th>
                  <th className="sticky top-0 bg-soft border-b border-line py-2.5 pr-5 pl-3 text-right">
                    Trung bình
                  </th>
                </tr>
              </thead>
              <tbody className="font-mono tnum">
                {analytics.historyRows.map((row) => (
                  <tr key={row.dateStr} className="hover:bg-soft/45 transition-colors">
                    <td className="border-b border-line/70 py-2 pl-5 pr-3 text-secondary">
                      {row.dateStr}
                    </td>
                    <td
                      className={`border-b border-line/70 py-2 px-3 text-right font-semibold ${TONE_TEXT_CLASS[row.cTone]}`}
                    >
                      {fmt(row.close)}
                    </td>
                    <td
                      className={`border-b border-line/70 py-2 px-3 text-right ${TONE_TEXT_CLASS[row.cTone]}`}
                    >
                      {row.diff > 0 ? "+" : ""}
                      {fmt(row.diff)} ({row.diff === 0 ? "0,00%" : fmtPct(row.pct)})
                    </td>
                    <td className="border-b border-line/70 py-2 px-3 text-right font-medium text-ink">
                      {fmt(Math.round(row.volume))}
                    </td>
                    <td className="border-b border-line/70 py-2 px-3 text-right text-secondary">
                      {row.sessionValBillion.toFixed(1).replace(".", ",")}
                    </td>
                    <td className="border-b border-line/70 py-2 px-3 text-right">
                      {row.vsMa10 != null ? (
                        <span
                          className={
                            row.vsMa10 >= 120
                              ? "text-gain font-semibold"
                              : row.vsMa10 <= 75
                                ? "text-muted"
                                : "text-ink"
                          }
                        >
                          {row.vsMa10}%
                        </span>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="border-b border-line/70 py-2 px-3 text-right text-ink">
                      {fmt(row.open)}
                    </td>
                    <td className="border-b border-line/70 py-2 px-3 text-right text-gain">
                      {fmt(row.high)}
                    </td>
                    <td className="border-b border-line/70 py-2 px-3 text-right text-loss">
                      {fmt(row.low)}
                    </td>
                    <td className="border-b border-line/70 py-2 pr-5 pl-3 text-right text-secondary">
                      {fmt(row.sessionVwap)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* 4. NỘI DUNG TAB 3: "PHÂN TÍCH VSA & KỸ THUẬT" — BẢNG CHỈ TIÊU ĐỊNH LƯỢNG CHUẨN CTCK */}
      {mainTab === "VSA_QUANT" && (
        <div className="grid grid-cols-1 lg:grid-cols-12 divide-y lg:divide-y-0 lg:divide-x divide-line bg-surface min-h-[480px]">
          <div className="lg:col-span-6 p-5 space-y-5">
            <div>
              <div className="px-3 py-1.5 rounded-[5px] bg-soft font-semibold text-[12px] text-ink flex items-center justify-between">
                <span>1. Cấu trúc Giá & Đường Trung bình Động (MA)</span>
                <span className="font-mono text-[11px] text-secondary">Khung 1D · 65 phiên</span>
              </div>

              <div className="mt-2 divide-y divide-line/70 text-[12.5px]">
                <div className="py-2.5 px-2 flex items-center justify-between">
                  <span className="text-secondary">Giá khớp hiện tại / Giá TB phiên (VWAP)</span>
                  <span className="font-mono font-semibold text-ink tnum">
                    {fmt(price)} ₫ / {fmt(analytics.vwapEst)} ₫{" "}
                    <span className={price >= analytics.vwapEst ? "text-gain" : "text-loss"}>
                      ({price >= analytics.vwapEst ? "Trên VWAP" : "Dưới VWAP"})
                    </span>
                  </span>
                </div>

                <div className="py-2.5 px-2 flex items-center justify-between">
                  <span className="text-secondary">Đường trung bình ngắn hạn MA10</span>
                  <span className="font-mono font-semibold text-ink tnum">
                    {analytics.ma10 ? `${fmt(analytics.ma10)} ₫` : "—"}{" "}
                    {analytics.ma10 && (
                      <span className={price >= analytics.ma10 ? "text-gain" : "text-loss"}>
                        ({price >= analytics.ma10 ? "+" : ""}
                        {(((price - analytics.ma10) / analytics.ma10) * 100)
                          .toFixed(1)
                          .replace(".", ",")}
                        %)
                      </span>
                    )}
                  </span>
                </div>

                <div className="py-2.5 px-2 flex items-center justify-between">
                  <span className="text-secondary">Đường xu hướng trung hạn MA20</span>
                  <span className="font-mono font-semibold text-ink tnum">
                    {analytics.ma20 ? `${fmt(analytics.ma20)} ₫` : "—"}{" "}
                    {analytics.ma20 && (
                      <span className={price >= analytics.ma20 ? "text-gain" : "text-loss"}>
                        ({price >= analytics.ma20 ? "+" : ""}
                        {(((price - analytics.ma20) / analytics.ma20) * 100)
                          .toFixed(1)
                          .replace(".", ",")}
                        %)
                      </span>
                    )}
                  </span>
                </div>

                <div className="py-2.5 px-2 flex items-center justify-between">
                  <span className="text-secondary">Đường xu hướng MA50</span>
                  <span className="font-mono font-semibold text-ink tnum">
                    {analytics.ma50 ? `${fmt(analytics.ma50)} ₫` : "—"}{" "}
                    {analytics.ma50 && (
                      <span className={price >= analytics.ma50 ? "text-gain" : "text-loss"}>
                        ({price >= analytics.ma50 ? "+" : ""}
                        {(((price - analytics.ma50) / analytics.ma50) * 100)
                          .toFixed(1)
                          .replace(".", ",")}
                        %)
                      </span>
                    )}
                  </span>
                </div>

                <div className="py-3 px-2">
                  <div className="flex items-center justify-between mb-1.5">
                    <span className="text-secondary">Vị thế trong kênh giá 60 phiên</span>
                    <span className="font-mono font-semibold text-ink tnum">
                      {analytics.pos60Pct}% kênh ({fmt(analytics.low60)} – {fmt(analytics.high60)})
                    </span>
                  </div>
                  <div className="h-2 w-full rounded-full bg-soft overflow-hidden">
                    <div
                      className="h-full bg-mineral rounded-full"
                      style={{ width: `${analytics.pos60Pct}%` }}
                    />
                  </div>
                </div>
              </div>
            </div>

            <div>
              <div className="px-3 py-1.5 rounded-[5px] bg-soft font-semibold text-[12px] text-ink">
                2. Chỉ báo Động lượng (RSI 14 & Dòng tiền MFI 14)
              </div>
              <div className="mt-2 divide-y divide-line/70 text-[12.5px]">
                <div className="py-2.5 px-2 flex items-center justify-between">
                  <span className="text-secondary">Chỉ số Sức mạnh tương đối RSI (14)</span>
                  <span className="font-mono font-semibold text-ink tnum">
                    {vsaItem?.rsi != null ? vsaItem.rsi.toFixed(1).replace(".", ",") : "—"} ·{" "}
                    <span className="font-sans text-[11.5px] text-secondary">
                      {(vsaItem?.rsi ?? 50) >= 70
                        ? "Quá mua"
                        : (vsaItem?.rsi ?? 50) <= 30
                          ? "Quá bán"
                          : "Trung tính – Tích cực"}
                    </span>
                  </span>
                </div>
                <div className="py-2.5 px-2 flex items-center justify-between">
                  <span className="text-secondary">Chỉ số Dòng tiền MFI (14)</span>
                  <span className="font-mono font-semibold text-ink tnum">
                    {vsaItem?.mfi != null ? vsaItem.mfi.toFixed(0) : "—"} / 100
                  </span>
                </div>
              </div>
            </div>
          </div>

          <div className="lg:col-span-6 p-5 space-y-5">
            <div>
              <div className="px-3 py-1.5 rounded-[5px] bg-soft font-semibold text-[12px] text-ink flex items-center justify-between">
                <span>3. Vi cấu trúc Dòng tiền VSA (Volume Spread Analysis)</span>
                <span className="font-mono text-[11px] text-gain font-semibold">
                  Điểm VSA:{" "}
                  {vsaItem?.vsa_score != null
                    ? `${vsaItem.vsa_score.toFixed(1).replace(".", ",")}/100`
                    : "—"}
                </span>
              </div>

              <div className="mt-2 divide-y divide-line/70 text-[12.5px]">
                <div className="py-2.5 px-2 flex items-center justify-between">
                  <span className="text-secondary">Tỷ lệ Mua chủ động (BVC 10 phiên)</span>
                  <span
                    className={`font-mono font-semibold tnum ${
                      analytics.buyRatioPct >= 50 ? "text-gain" : "text-loss"
                    }`}
                  >
                    {analytics.buyRatioPct}% Mua CĐ / {100 - analytics.buyRatioPct}% Bán CĐ
                  </span>
                </div>

                <div className="py-2.5 px-2 flex items-center justify-between">
                  <span className="text-secondary">Tỷ lệ Phiên tiền áp đảo (20 phiên)</span>
                  <span className="font-mono font-semibold text-ink tnum">
                    {vsaItem?.tien_ap_dao ?? 0}% ({vsaItem?.dong_tien_meta || "Cân bằng"})
                  </span>
                </div>

                <div className="py-2.5 px-2 flex items-center justify-between">
                  <span className="text-secondary">Tín hiệu Rũ nền (Spring) / Test cung</span>
                  <span className="font-mono font-semibold text-ink">
                    {vsaItem?.ru_nen
                      ? "Đã kích hoạt Rũ nền"
                      : vsaItem?.test_cung
                        ? "Đã kích hoạt Test cung (≤68% MA10)"
                        : "Đang vận động trong nền"}
                  </span>
                </div>

                <div className="py-2.5 px-2 flex items-center justify-between">
                  <span className="text-secondary">Tần suất nổ thanh khoản (20 phiên)</span>
                  <span className="font-mono font-semibold text-ink">
                    {vsaItem?.freq || "Chưa ghi nhận đột biến"}
                  </span>
                </div>
              </div>
            </div>

            <div>
              <div className="px-3 py-1.5 rounded-[5px] bg-soft font-semibold text-[12px] text-ink">
                4. Quản trị Thanh khoản & Hạn mức Đi vốn Chuẩn Tổ chức
              </div>
              <div className="mt-2 divide-y divide-line/70 text-[12.5px]">
                <div className="py-2.5 px-2 flex items-center justify-between">
                  <span className="text-secondary">Khối lượng khớp bình quân 10 phiên (MA10)</span>
                  <span className="font-mono font-semibold text-ink tnum">
                    {analytics.volMa10 > 0 ? `${fmt(analytics.volMa10)} CP` : "—"}
                  </span>
                </div>
                <div className="py-2.5 px-2 flex items-center justify-between">
                  <span className="text-secondary">Hạn mức mua an toàn (≤ 3,0% MA10)</span>
                  <span className="font-mono font-bold text-teal tnum">
                    {vsaItem?.max_buy ? `${fmt(vsaItem.max_buy)} CP` : "—"}
                  </span>
                </div>
                <div className="py-2.5 px-2 flex items-center justify-between">
                  <span className="text-secondary">Giá trị giải ngân tối đa khuyến nghị</span>
                  <span className="font-mono font-semibold text-ink tnum">
                    {vsaItem?.max_buy && price > 0
                      ? `~ ${((vsaItem.max_buy * price) / 1_000_000_000).toFixed(2).replace(".", ",")} tỷ ₫`
                      : "—"}
                  </span>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 5. NỘI DUNG TAB 4: "BCTC & TIN TỨC" */}
      {mainTab === "FILINGS" && (
        <div className="p-5 bg-surface min-h-[465px] max-h-[500px] overflow-y-auto">
          {newsList.length === 0 ? (
            <div className="py-16 text-center text-[13px] text-muted">
              Chưa có báo cáo tài chính hoặc công bố thông tin mới cho mã {symbol}.
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {newsList.map((item, idx) => (
                <a
                  key={item.id ?? idx}
                  href={item.url || "#"}
                  target="_blank"
                  rel="noreferrer"
                  className="block rounded-[8px] border border-line bg-surface p-3.5 hover:border-ink hover:bg-soft/40 transition-colors"
                >
                  <div className="flex items-center justify-between gap-2 text-[10.5px] font-mono text-muted mb-1.5">
                    <span className="uppercase font-semibold text-mineral">
                      {item.doc_type === "financial_statement"
                        ? "Báo cáo tài chính (PDF)"
                        : item.source || "Công bố thông tin"}
                    </span>
                    <span>
                      {formatDateShortVi(item.published_date ?? item.created_at)}
                    </span>
                  </div>
                  <div className="text-[13px] font-medium text-ink leading-snug line-clamp-2">
                    {item.title}
                  </div>
                </a>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export interface StockDetailModalProps extends StockDetailContentProps {
  open: boolean
  onClose: () => void
}

export function StockDetailModal({
  open,
  onClose,
  onSelectPrev,
  onSelectNext,
  ...props
}: StockDetailModalProps) {
  useEffect(() => {
    if (!open) return
    const prevOverflow = document.body.style.overflow
    document.body.style.overflow = "hidden"

    const onKeyDown = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null
      const isInput =
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable)

      if (e.key === "Escape") {
        e.preventDefault()
        onClose()
      } else if (!isInput && e.key === "ArrowLeft" && onSelectPrev) {
        e.preventDefault()
        onSelectPrev()
      } else if (!isInput && e.key === "ArrowRight" && onSelectNext) {
        e.preventDefault()
        onSelectNext()
      }
    }

    window.addEventListener("keydown", onKeyDown)
    return () => {
      document.body.style.overflow = prevOverflow
      window.removeEventListener("keydown", onKeyDown)
    }
  }, [open, onClose, onSelectPrev, onSelectNext])

  if (!open || typeof document === "undefined") return null

  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`Chi tiết cổ phiếu ${props.symbol}`}
      className="fixed inset-0 z-50 flex items-center justify-center p-3 sm:p-5 bg-[#121a17]/60 backdrop-blur-[2px] overflow-y-auto"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) {
          onClose()
        }
      }}
    >
      <div className="w-full max-w-[1360px] my-auto animate-in fade-in zoom-in-95 duration-150">
        <StockDetailContent
          {...props}
          onClose={onClose}
          onSelectPrev={onSelectPrev}
          onSelectNext={onSelectNext}
          isModal
        />
      </div>
    </div>,
    document.body
  )
}
