"use client"

import { useState, useEffect, useRef } from "react"
import { getSocket, retainMarket, retainMarketOrderbooks, retainStock } from "./socket"
import type { Stock } from "@/types"
import type { ApiMarketIndex, ApiMarketStock } from "@/types"

interface PriceTick { price?: number; close?: number; ref?: number | null; prevClose?: number | null; ceiling?: number; floor?: number; change_pct?: number | null; changePercent?: number | null; volume?: number; matchVolume?: number; isSnapshot?: boolean }
interface OrderBookTick { bids?: RealtimeOrderBookLevel[]; asks?: RealtimeOrderBookLevel[]; receivedAt?: number; source?: string; stale?: boolean; isSnapshot?: boolean }
interface TradeTick { time?: string; price?: number; volume?: number; matchVolume?: number; side?: "BUY" | "SELL"; isSnapshot?: boolean }
interface MarketIndicesTick { indices?: ApiMarketIndex[]; isSnapshot?: boolean }
interface MarketSnapshotTick { stocks?: ApiMarketStock[]; total?: number; liveSymbols?: number; isSnapshot?: boolean }
interface MarketHeatmapTick { sectors?: Array<Record<string, unknown>>; isSnapshot?: boolean }
interface MarketBreadthTick { advancers?: number; decliners?: number; unchanged?: number; available?: number; unknown?: number; total?: number; coverage?: number; isSnapshot?: boolean }
interface MarketLiquidityTick { totalValueBillion?: number | null; lastUpdate?: string; source?: string; approximate?: boolean; stale?: boolean; isSnapshot?: boolean }
interface MarketOrderBookTick extends RealtimeOrderBook {
  symbol?: string
  receivedAt?: number
  lastUpdate?: string
  source?: string
  stale?: boolean
}

export interface RealtimeOrderBookLevel {
  price: number
  volume: number
}

export interface RealtimeOrderBook {
  bids: RealtimeOrderBookLevel[]
  asks: RealtimeOrderBookLevel[]
}

interface RealtimeStockOrderBook extends RealtimeOrderBook {
  symbol: string
  receivedAt: number
}

export interface RealtimeMarketOrderBook extends RealtimeOrderBook {
  receivedAt: number
  lastUpdate?: string
}

export interface RealtimeTrade {
  time: string
  price: number
  volume: number
  side?: "BUY" | "SELL"
}

export function useRealtimeStock(symbol: string, initialStock?: Stock) {
  const [stock, setStock] = useState<Stock | undefined>(initialStock)
  const [orderbook, setOrderbook] = useState<RealtimeStockOrderBook | null>(null)
  const [trades, setTrades] = useState<RealtimeTrade[]>([])
  const [isLive, setIsLive] = useState(false)
  const [lastTickAt, setLastTickAt] = useState<Date | null>(null)
  const [flash, setFlash] = useState<"gain" | "loss" | null>(null)
  const flashTimeoutRef = useRef<NodeJS.Timeout | null>(null)
  const hasRealtimePriceRef = useRef(false)
  const lastRealtimeMessageAt = useRef(0)

  useEffect(() => {
    hasRealtimePriceRef.current = false
  }, [symbol])

  useEffect(() => {
    if (initialStock) {
      setStock((prev) => {
        if (!prev) return initialStock
        const price = hasRealtimePriceRef.current ? (prev.price ?? initialStock.price) : initialStock.price
        const changePct = hasRealtimePriceRef.current ? prev.changePct : initialStock.changePct
        const volume = hasRealtimePriceRef.current ? (prev.volume ?? initialStock.volume) : initialStock.volume

        if (
          prev.symbol === initialStock.symbol &&
          prev.price === price &&
          prev.changePct === changePct &&
          prev.ref === initialStock.ref &&
          prev.ceiling === initialStock.ceiling &&
          prev.floor === initialStock.floor &&
          prev.volume === volume &&
          prev.name === initialStock.name
        ) {
          return prev
        }
        return {
          ...initialStock,
          price,
          changePct,
          volume,
          ref: hasRealtimePriceRef.current ? prev.ref : initialStock.ref,
          ceiling: prev.ceiling ?? initialStock.ceiling,
          floor: prev.floor ?? initialStock.floor,
        }
      })
    }
  }, [
    initialStock?.symbol,
    initialStock?.price,
    initialStock?.changePct,
    initialStock?.ref,
    initialStock?.ceiling,
    initialStock?.floor,
    initialStock?.volume,
    initialStock?.name,
  ])

  useEffect(() => {
    if (!symbol) return

    const sym = symbol.toUpperCase()
    const socket = getSocket()

    function onConnect() {
      setIsLive(false)
      lastRealtimeMessageAt.current = 0
    }

    function onDisconnect() {
      setIsLive(false)
    }

    function onPrice(data: PriceTick) {
      if (!data) return
      if (!data.isSnapshot) {
        hasRealtimePriceRef.current = true
        setIsLive(true)
        lastRealtimeMessageAt.current = Date.now()
        setLastTickAt(new Date())
      }

      setStock((prev) => {
        if (!prev) return prev
        const newPrice = Number(data.price ?? data.close ?? prev.price)
        const oldPrice = Number(prev.price)

        if (oldPrice && newPrice !== oldPrice) {
          if (flashTimeoutRef.current) clearTimeout(flashTimeoutRef.current)
          setFlash(newPrice > oldPrice ? "gain" : "loss")
          flashTimeoutRef.current = setTimeout(() => setFlash(null), 1000)
        }

        const rawRef = data.ref !== undefined ? data.ref : data.prevClose
        const ref = rawRef === undefined ? prev.ref : rawRef === null ? null : Number(rawRef)
        const hasChange = data.change_pct !== undefined || data.changePercent !== undefined
        const rawChange = data.change_pct !== undefined ? data.change_pct : data.changePercent
        const changePct = !hasChange
          ? prev.changePct
          : rawChange == null
            ? null
            : Number.isFinite(Number(rawChange))
              ? Number(Number(rawChange).toFixed(2))
              : null

        return {
          ...prev,
          price: newPrice || prev.price,
          ref,
          ceiling: Number(data.ceiling || prev.ceiling),
          floor: Number(data.floor || prev.floor),
          changePct,
          volume: data.volume ? String(data.volume) : prev.volume,
        }
      })
    }

    function onOrderBook(data: OrderBookTick) {
      if (!data) return
      let receivedAt = Number(data.receivedAt ?? 0)
      if (receivedAt > 0 && receivedAt < 1_000_000_000_000) receivedAt *= 1000
      const age = Date.now() - receivedAt
      if (data.source !== "dnse-ws" || data.stale || !Number.isFinite(receivedAt) || !receivedAt || age < -5_000 || age >= 15_000) {
        setOrderbook(null)
        return
      }
      if (!data.isSnapshot) {
        setIsLive(true)
        lastRealtimeMessageAt.current = Date.now()
      }
      if (data.bids || data.asks) {
        setOrderbook({
          symbol: sym,
          bids: Array.isArray(data.bids) ? data.bids : [],
          asks: Array.isArray(data.asks) ? data.asks : [],
          receivedAt,
        })
      }
    }

    function onTrade(data: TradeTick) {
      if (!data) return
      if (!data.isSnapshot) {
        setIsLive(true)
        lastRealtimeMessageAt.current = Date.now()
      }
      setTrades((prev) => [
        {
          time: String(data.time || new Date().toLocaleTimeString("vi-VN")),
          price: Number(data.price ?? 0),
          volume: Number(data.matchVolume ?? data.volume ?? 0),
          side: data.side,
        },
        ...prev.slice(0, 19),
      ])
    }

    const releaseStock = retainStock(sym)
    if (socket.connected) {
      onConnect()
    }

    socket.on("connect", onConnect)
    socket.on("disconnect", onDisconnect)
    socket.on(`stock:price:${sym}`, onPrice)
    socket.on(`stock:orderbook:${sym}`, onOrderBook)
    socket.on(`stock:trades:${sym}`, onTrade)
    const freshnessTimer = window.setInterval(() => {
      if (lastRealtimeMessageAt.current && Date.now() - lastRealtimeMessageAt.current > 30_000) setIsLive(false)
      setOrderbook((current) => current && Date.now() - current.receivedAt >= 15_000 ? null : current)
    }, 1_000)

    return () => {
      window.clearInterval(freshnessTimer)
      releaseStock()
      socket.off("connect", onConnect)
      socket.off("disconnect", onDisconnect)
      socket.off(`stock:price:${sym}`, onPrice)
      socket.off(`stock:orderbook:${sym}`, onOrderBook)
      socket.off(`stock:trades:${sym}`, onTrade)
      if (flashTimeoutRef.current) clearTimeout(flashTimeoutRef.current)
    }
  }, [symbol])

  return { stock, orderbook: orderbook?.symbol === symbol.trim().toUpperCase() ? orderbook : null, trades, isLive, lastTickAt, flash }
}

export function useRealtimeMarket(initialIndices?: {
  vnIndexVal: string
  vnIndexPct: number | null
  vn100Val?: string
  vn100Pct?: number | null
}) {
  const [indices, setIndices] = useState(initialIndices)
  const [isLive, setIsLive] = useState(false)
  const [heatmapLive, setHeatmapLive] = useState(false)
  const [snapshot, setSnapshot] = useState<MarketSnapshotTick | null>(null)
  const [heatmap, setHeatmap] = useState<MarketHeatmapTick | null>(null)
  const [liquidity, setLiquidity] = useState<MarketLiquidityTick | null>(null)
  const lastIndicesAt = useRef(0)
  const lastHeatmapAt = useRef(0)
  const [breadth, setBreadth] = useState<MarketBreadthTick | null>(null)

  useEffect(() => {
    if (initialIndices) {
      setIndices((prev) => {
        if (lastIndicesAt.current > 0) return prev
        if (
          prev?.vnIndexVal === initialIndices.vnIndexVal &&
          prev?.vnIndexPct === initialIndices.vnIndexPct &&
          prev?.vn100Val === initialIndices.vn100Val &&
          prev?.vn100Pct === initialIndices.vn100Pct
        ) {
          return prev
        }
        return initialIndices
      })
    }
  }, [
    initialIndices?.vnIndexVal,
    initialIndices?.vnIndexPct,
    initialIndices?.vn100Val,
    initialIndices?.vn100Pct,
  ])

  useEffect(() => {
    const socket = getSocket()

    function onConnect() {
      setIsLive(false)
      setHeatmapLive(false)
      lastIndicesAt.current = 0
      lastHeatmapAt.current = 0
    }

    function onDisconnect() {
      setIsLive(false)
      setHeatmapLive(false)
    }

    function onIndices(data: MarketIndicesTick) {
      const list = Array.isArray(data?.indices) ? data.indices : []
      if (!list.length) return

      const indices = list as ApiMarketIndex[]
      const getName = (item: ApiMarketIndex) => String(item.symbol ?? item.name ?? "").toUpperCase().replaceAll("-", "")
      const indexChange = (item: ApiMarketIndex) => {
        const raw = item.changePercent ?? item.change_pct
        const value = raw == null ? NaN : Number(raw)
        return Number.isFinite(value) ? value : null
      }
      const vnIndexItem = indices.find((x) => getName(x) === "VNINDEX")
      const vn100Item = indices.find((x) => getName(x) === "VN100")
      if (!vnIndexItem && !vn100Item) return
      if (!data.isSnapshot && vnIndexItem) {
        const receivedAt = Number(vnIndexItem.receivedAt ?? 0) * 1000
        const fresh = receivedAt > 0 && Date.now() - receivedAt < 15_000
        setIsLive(fresh)
        if (fresh) lastIndicesAt.current = receivedAt
      }

      setIndices((prev) => ({
        vnIndexVal: vnIndexItem && vnIndexItem.value != null && Number.isFinite(Number(vnIndexItem.value)) && Number(vnIndexItem.value) > 0
          ? Number(vnIndexItem.value).toLocaleString("vi-VN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
          : prev?.vnIndexVal ?? "—",
        vnIndexPct: vnIndexItem ? indexChange(vnIndexItem) : prev?.vnIndexPct ?? null,
        vn100Val: vn100Item && vn100Item.value != null && Number.isFinite(Number(vn100Item.value)) && Number(vn100Item.value) > 0
          ? Number(vn100Item.value).toLocaleString("vi-VN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
          : prev?.vn100Val ?? "—",
        vn100Pct: vn100Item ? indexChange(vn100Item) : prev?.vn100Pct ?? null,
      }))
    }

    function onSnapshot(data: MarketSnapshotTick) {
      if (!Array.isArray(data?.stocks)) return
      setSnapshot(data)
    }

    function onHeatmap(data: MarketHeatmapTick) {
      if (!Array.isArray(data?.sectors)) return
      setHeatmap(data)
      if (!data.isSnapshot) setHeatmapLive(true)
      if (!data.isSnapshot) lastHeatmapAt.current = Date.now()
    }

    function onBreadth(data: MarketBreadthTick) {
      if (typeof data?.advancers !== "number" || typeof data?.decliners !== "number") return
      setBreadth(data)
    }

    function onLiquidity(data: MarketLiquidityTick) {
      if (!data) {
        setLiquidity(null)
        return
      }
      const value = data.totalValueBillion
      setLiquidity(value == null || Number.isFinite(Number(value)) ? data : { ...data, totalValueBillion: null })
    }

    const freshnessTimer = window.setInterval(() => {
      if (lastIndicesAt.current && Date.now() - lastIndicesAt.current > 15_000) setIsLive(false)
      if (lastHeatmapAt.current && Date.now() - lastHeatmapAt.current > 15_000) setHeatmapLive(false)
    }, 1_000)

    const releaseMarket = retainMarket()
    socket.on("connect", onConnect)
    socket.on("disconnect", onDisconnect)
    socket.on("market:indices", onIndices)
    socket.on("market:snapshot", onSnapshot)
    socket.on("market:heatmap", onHeatmap)
    socket.on("market:breadth", onBreadth)
    socket.on("market:liquidity", onLiquidity)
    if (socket.connected) {
      onConnect()
    } else {
      socket.connect()
    }

    return () => {
      releaseMarket()
      socket.off("connect", onConnect)
      socket.off("disconnect", onDisconnect)
      socket.off("market:indices", onIndices)
      socket.off("market:snapshot", onSnapshot)
      socket.off("market:heatmap", onHeatmap)
      socket.off("market:breadth", onBreadth)
      socket.off("market:liquidity", onLiquidity)
      window.clearInterval(freshnessTimer)
    }
  }, [])

  return { indices, isLive, snapshot, heatmap, heatmapLive, breadth, liquidity }
}

export function useRealtimeMarketOrderBooks(
  initial: Record<string, MarketOrderBookTick> = {},
): Record<string, RealtimeMarketOrderBook> {
  const [orderbooks, setOrderbooks] = useState<Record<string, RealtimeMarketOrderBook>>({})
  const pendingOrderBooks = useRef(new Map<string, RealtimeMarketOrderBook>())
  const updateFrame = useRef<number | null>(null)

  useEffect(() => {
    setOrderbooks((current) => {
      const next: Record<string, RealtimeMarketOrderBook> = {}
      for (const [symbol, book] of Object.entries(initial)) {
        const receivedAt = Number(book.receivedAt ?? 0)
        const age = Date.now() - receivedAt
        if (book.source === "dnse-ws" && !book.stale && Number.isFinite(receivedAt) && receivedAt > 0 && age >= -5_000 && age < 15_000) {
          next[symbol] = { ...book, receivedAt }
        }
      }
      for (const [symbol, book] of Object.entries(current)) {
        if (book.receivedAt >= (next[symbol]?.receivedAt ?? 0)) next[symbol] = book
      }
      return next
    })
  }, [initial])

  useEffect(() => {
    const socket = getSocket()
    function onOrderBook(data: MarketOrderBookTick) {
      const symbol = String(data?.symbol ?? "").trim().toUpperCase()
      if (!symbol || data.source !== "dnse-ws" || !Array.isArray(data.bids) || !Array.isArray(data.asks)) return
      let receivedAt = Number(data.receivedAt ?? 0)
      if (receivedAt > 0 && receivedAt < 1_000_000_000_000) receivedAt *= 1000
      if (!receivedAt || !Number.isFinite(receivedAt)) receivedAt = Date.now()
      const age = Date.now() - receivedAt
      if (data.stale || age < -5_000 || age >= 15_000) return
      pendingOrderBooks.current.set(symbol, { bids: data.bids, asks: data.asks, receivedAt, lastUpdate: data.lastUpdate })
      if (updateFrame.current !== null) return
      updateFrame.current = window.requestAnimationFrame(() => {
        updateFrame.current = null
        const updates: Record<string, RealtimeMarketOrderBook> = {}
        for (const [updatedSymbol, book] of pendingOrderBooks.current) updates[updatedSymbol] = book
        pendingOrderBooks.current.clear()
        setOrderbooks((current) => ({ ...current, ...updates }))
      })
    }
    socket.on("market:orderbook", onOrderBook)
    const release = retainMarketOrderbooks()
    const pruneTimer = window.setInterval(() => {
      const now = Date.now()
      setOrderbooks((current) => {
        const fresh = Object.fromEntries(Object.entries(current).filter(([, book]) => {
          const age = now - book.receivedAt
          return age >= -5_000 && age < 15_000
        }))
        return Object.keys(fresh).length === Object.keys(current).length ? current : fresh
      })
    }, 1_000)
    return () => {
      release()
      socket.off("market:orderbook", onOrderBook)
      window.clearInterval(pruneTimer)
      if (updateFrame.current !== null) window.cancelAnimationFrame(updateFrame.current)
      updateFrame.current = null
      pendingOrderBooks.current.clear()
    }
  }, [])

  return orderbooks
}
