import { io, type Socket } from "socket.io-client"
import { tokenStore } from "./api/client"
let socket: Socket | null = null
const stockRefs = new Map<string, number>()
const ohlcRefs = new Map<string, number>()
let marketRefs = 0

export function getSocket() {
  if (!socket) {
    socket = io(process.env.NEXT_PUBLIC_SOCKET_URL || process.env.NEXT_PUBLIC_WS_URL || "http://localhost:3001", {
      autoConnect: false,
      transports: ["websocket", "polling"],
      auth: callback => callback({ token: tokenStore.get() }),
    })
    socket.on("connect", () => {
      for (const symbol of stockRefs.keys()) socket?.emit("subscribe:symbol", symbol)
      if (marketRefs > 0) socket?.emit("subscribe:market")
      for (const key of ohlcRefs.keys()) {
        const [resolution, symbol] = key.split(":")
        socket?.emit("subscribe:ohlc", { symbol, resolution })
      }
    })
  }
  return socket
}

export function retainMarket(): () => void {
  const current = marketRefs
  marketRefs++
  const connection = getSocket()
  if (current === 0 && connection.connected) connection.emit("subscribe:market")
  if (!connection.connected) connection.connect()
  return () => {
    marketRefs = Math.max(0, marketRefs - 1)
    if (marketRefs === 0 && connection.connected) connection.emit("unsubscribe:market")
  }
}

export function retainStock(symbol: string): () => void {
  const sym = symbol.toUpperCase()
  const current = stockRefs.get(sym) ?? 0
  stockRefs.set(sym, current + 1)
  const connection = getSocket()
  if (current === 0 && connection.connected) connection.emit("subscribe:symbol", sym)
  if (!connection.connected) connection.connect()
  return () => {
    const remaining = (stockRefs.get(sym) ?? 1) - 1
    if (remaining <= 0) {
      stockRefs.delete(sym)
      if (connection.connected) connection.emit("unsubscribe:symbol", sym)
    } else stockRefs.set(sym, remaining)
  }
}

export function retainOhlc(symbol: string, resolution: string): () => void {
  const sym = symbol.toUpperCase()
  const key = `${resolution}:${sym}`
  const current = ohlcRefs.get(key) ?? 0
  ohlcRefs.set(key, current + 1)
  const connection = getSocket()
  if (current === 0 && connection.connected) connection.emit("subscribe:ohlc", { symbol: sym, resolution })
  if (!connection.connected) connection.connect()
  return () => {
    const remaining = (ohlcRefs.get(key) ?? 1) - 1
    if (remaining <= 0) {
      ohlcRefs.delete(key)
      if (connection.connected) connection.emit("unsubscribe:ohlc", { symbol: sym, resolution })
    } else ohlcRefs.set(key, remaining)
  }
}
