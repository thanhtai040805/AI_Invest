"use client"

import { useState, useEffect, useRef } from "react"
import { getSocket } from "./socket"
import type { Stock } from "@/types"

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

  useEffect(() => {
    if (initialStock) {
      setStock((prev) => ({
        ...initialStock,
        price: prev?.price ?? initialStock.price,
        changePct: prev?.changePct ?? initialStock.changePct,
        volume: prev?.volume ?? initialStock.volume,
        ref: prev?.ref ?? initialStock.ref,
        ceiling: prev?.ceiling ?? initialStock.ceiling,
        floor: prev?.floor ?? initialStock.floor,
      }))
    }
  }, [initialStock])

  useEffect(() => {
    if (!symbol) return

    const sym = symbol.toUpperCase()
    const socket = getSocket()

    function onConnect() {
      setIsLive(true)
      socket.emit("subscribe:symbol", sym)
    }

    function onDisconnect() {
      setIsLive(false)
    }

    function onPrice(data: any) {
      if (!data) return
      setIsLive(true)
      setLastTickAt(new Date())

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
          ref: Number(data.ref ?? prev.ref),
          ceiling: Number(data.ceiling ?? prev.ceiling),
          floor: Number(data.floor ?? prev.floor),
          changePct: data.change_pct !== undefined ? Number(Number(data.change_pct).toFixed(2)) : prev.changePct,
          volume: data.volume ? String(data.volume) : prev.volume,
        }
      })
    }

    function onOrderBook(data: any) {
      if (!data) return
      setIsLive(true)
      if (data.bids || data.asks) {
        setOrderbook({
          bids: Array.isArray(data.bids) ? data.bids : [],
          asks: Array.isArray(data.asks) ? data.asks : [],
        })
      }
    }

    function onTrade(data: any) {
      if (!data) return
      setIsLive(true)
      setTrades((prev) => [
        {
          time: String(data.time || new Date().toLocaleTimeString("vi-VN")),
          price: Number(data.price ?? 0),
          volume: Number(data.volume ?? 0),
          side: data.side,
        },
        ...prev.slice(0, 19),
      ])
    }

    if (socket.connected) {
      onConnect()
    } else {
      socket.connect()
    }

    socket.on("connect", onConnect)
    socket.on("disconnect", onDisconnect)
    socket.on(`stock:price:${sym}`, onPrice)
    socket.on(`stock:orderbook:${sym}`, onOrderBook)
    socket.on(`stock:trades:${sym}`, onTrade)

    return () => {
      socket.emit("unsubscribe:symbol", sym)
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
}) {
  const [indices, setIndices] = useState(initialIndices)
  const [isLive, setIsLive] = useState(false)

  useEffect(() => {
    if (initialIndices) setIndices(initialIndices)
  }, [initialIndices])

  useEffect(() => {
    const socket = getSocket()

    function onConnect() {
      setIsLive(true)
      socket.emit("subscribe:market")
    }

    function onDisconnect() {
      setIsLive(false)
    }

    function onIndices(data: any) {
      setIsLive(true)
      const list = Array.isArray(data?.indices) ? data.indices : []
      if (!list.length) return

      const vnIndexItem = list.find((x: any) => String(x.symbol).includes("VNINDEX") || String(x.symbol).includes("VN-INDEX"))
      const vn30Item = list.find((x: any) => String(x.symbol).includes("VN30"))

      setIndices((prev) => ({
        vnIndexVal: vnIndexItem
          ? Number(vnIndexItem.value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
          : (prev?.vnIndexVal ?? "1,284.32"),
        vnIndexPct: vnIndexItem ? Number(Number(vnIndexItem.change_pct).toFixed(2)) : (prev?.vnIndexPct ?? 0.72),
        vn30Val: vn30Item
          ? Number(vn30Item.value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
          : (prev?.vn30Val ?? "1,351.27"),
        vn30Pct: vn30Item ? Number(Number(vn30Item.change_pct).toFixed(2)) : (prev?.vn30Pct ?? 0.48),
      }))
    }

    if (socket.connected) {
      onConnect()
    } else {
      socket.connect()
    }

    socket.on("connect", onConnect)
    socket.on("disconnect", onDisconnect)
    socket.on("market:indices", onIndices)

    return () => {
      socket.emit("unsubscribe:market")
      socket.off("connect", onConnect)
      socket.off("disconnect", onDisconnect)
      socket.off("market:indices", onIndices)
    }
  }, [])

  return { indices, isLive }
}
