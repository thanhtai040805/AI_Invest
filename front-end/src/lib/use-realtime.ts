"use client"

import { useState, useEffect, useRef } from "react"
import { getSocket, retainMarket, retainStock } from "./socket"
import type { Stock } from "@/types"
import type { ApiMarketIndex, ApiMarketStock } from "@/types"

interface PriceTick { price?: number; close?: number; ref?: number; prevClose?: number; ceiling?: number; floor?: number; change_pct?: number; changePercent?: number; volume?: number; matchVolume?: number; isSnapshot?: boolean }
interface OrderBookTick { bids?: RealtimeOrderBookLevel[]; asks?: RealtimeOrderBookLevel[]; isSnapshot?: boolean }
interface TradeTick { time?: string; price?: number; volume?: number; matchVolume?: number; side?: "BUY" | "SELL"; isSnapshot?: boolean }
interface MarketIndicesTick { indices?: ApiMarketIndex[]; isSnapshot?: boolean }
interface MarketSnapshotTick { stocks?: ApiMarketStock[]; total?: number; liveSymbols?: number; isSnapshot?: boolean }
interface MarketHeatmapTick { sectors?: Array<Record<string, unknown>>; isSnapshot?: boolean }
interface MarketBreadthTick { advancers?: number; decliners?: number; unchanged?: number; isSnapshot?: boolean }
interface MarketLiquidityTick { totalValueBillion?: number | null; isSnapshot?: boolean }

export interface RealtimeOrderBookLevel {
  price: number
  volume: number
}

export interface RealtimeOrderBook {
  bids: RealtimeOrderBookLevel[]
  asks: RealtimeOrderBookLevel[]
}

export interface RealtimeTrade {
  time: string
  price: number
  volume: number
  side?: "BUY" | "SELL"
}

export function useRealtimeStock(symbol: string, initialStock?: Stock) {
  const [stock, setStock] = useState<Stock | undefined>(initialStock)
  const [orderbook, setOrderbook] = useState<RealtimeOrderBook | null>(null)
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
        const changePct = hasRealtimePriceRef.current ? (prev.changePct ?? initialStock.changePct) : initialStock.changePct
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
          ref: prev.ref ?? initialStock.ref,
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

        return {
          ...prev,
          price: newPrice || prev.price,
          ref: Number(data.ref ?? data.prevClose ?? prev.ref),
          ceiling: Number(data.ceiling || prev.ceiling),
          floor: Number(data.floor || prev.floor),
          changePct: data.change_pct !== undefined || data.changePercent !== undefined
            ? Number(Number(data.change_pct ?? data.changePercent).toFixed(2))
            : prev.changePct,
          volume: data.volume ? String(data.volume) : prev.volume,
        }
      })
    }

    function onOrderBook(data: OrderBookTick) {
      if (!data) return
      if (!data.isSnapshot) {
        setIsLive(true)
        lastRealtimeMessageAt.current = Date.now()
      }
      if (data.bids || data.asks) {
        setOrderbook({
          bids: Array.isArray(data.bids) ? data.bids : [],
          asks: Array.isArray(data.asks) ? data.asks : [],
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

  return { stock, orderbook, trades, isLive, lastTickAt, flash }
}

export function useRealtimeMarket(initialIndices?: {
  vnIndexVal: string
  vnIndexPct: number
  vn30Val: string
  vn30Pct: number
  vn100Val?: string
  vn100Pct?: number
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
          prev?.vn30Val === initialIndices.vn30Val &&
          prev?.vn30Pct === initialIndices.vn30Pct &&
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
    initialIndices?.vn30Val,
    initialIndices?.vn30Pct,
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
      const getName = (item: ApiMarketIndex) => String(item.name ?? item.symbol ?? "").toUpperCase().replaceAll("-", "")
      const vnIndexItem = indices.find((x) => getName(x) === "VNINDEX")
      const vn30Item = indices.find((x) => getName(x) === "VN30")
      const vn100Item = indices.find((x) => getName(x) === "VN100")
      if (!vnIndexItem && !vn30Item && !vn100Item) return
      if (!data.isSnapshot && vnIndexItem) {
        const receivedAt = Number(vnIndexItem.receivedAt ?? 0) * 1000
        const fresh = receivedAt > 0 && Date.now() - receivedAt < 15_000
        setIsLive(fresh)
        if (fresh) lastIndicesAt.current = receivedAt
      }

      setIndices((prev) => ({
        vnIndexVal: vnIndexItem && Number.isFinite(Number(vnIndexItem.value))
          ? Number(vnIndexItem.value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
          : prev?.vnIndexVal ?? "—",
        vnIndexPct: vnIndexItem ? Number(vnIndexItem.changePercent ?? vnIndexItem.change_pct ?? 0) : prev?.vnIndexPct ?? 0,
        vn30Val: vn30Item && Number.isFinite(Number(vn30Item.value))
          ? Number(vn30Item.value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
          : prev?.vn30Val ?? "—",
        vn30Pct: vn30Item ? Number(vn30Item.changePercent ?? vn30Item.change_pct ?? 0) : prev?.vn30Pct ?? 0,
        vn100Val: vn100Item && Number.isFinite(Number(vn100Item.value))
          ? Number(vn100Item.value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
          : prev?.vn100Val ?? "—",
        vn100Pct: vn100Item ? Number(vn100Item.changePercent ?? vn100Item.change_pct ?? 0) : prev?.vn100Pct ?? 0,
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
      if (data?.totalValueBillion != null && Number.isFinite(Number(data.totalValueBillion))) setLiquidity(data)
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
