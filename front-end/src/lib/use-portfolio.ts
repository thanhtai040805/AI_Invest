"use client"

import { useEffect, useMemo, useState } from "react"
import { getSocket, retainStock } from "./socket"

export interface PortfolioPosition {
  id: string; symbol: string; quantity: number; avgPrice: number;
  currentPrice: number | null; marketValue: number | null; costBasis: number | null;
  pnl: number | null; pnlPercent: number | null; weight: number | null;
  priceAsOf: string | null; priceSource: string; stale: boolean;
}
export interface PortfolioSnapshot {
  summary: {
    accountId: string; nav: number | null; cash: number; totalCost: number | null;
    openingCash: number | null; realizedPnl: number | null; unrealizedPnl: number | null;
    totalPnl: number | null; totalReturnPct: number | null; ledgerComplete: boolean;
    dailyBaseline: number | null; dailyPnL: number | null; dailyPnLPercent: number | null;
    valuedAt: string; stalePrices: string[];
  }
  positions: PortfolioPosition[];
  performance: { equityCurve: { date: string; value: number }[]; asOf: string | null };
  risks: { sharpe: number | null; alpha: number | null; beta: number | null; maxDrawdown: number | null; message: string };
  orders: Record<string, unknown>[];
}
type PriceTick = { price: number; receivedAt: number }

/** Reuse symbol subscriptions; batch prices once/second, reload account state once/minute. */
export function usePortfolio(base: PortfolioSnapshot | null | undefined, reload: () => Promise<void>) {
  const [live, setLive] = useState<{ accountId: string; ticks: Record<string, PriceTick> } | null>(null)
  const [now, setNow] = useState(() => Date.now())
  const accountId = base?.summary.accountId
  const symbols = base?.positions.map(p => p.symbol).sort().join(",") ?? ""
  useEffect(() => {
    if (!accountId) return
    const socket = getSocket()
    const pending: Record<string, PriceTick> = {}
    const releases = symbols.split(",").filter(Boolean).map(symbol => {
      const event = `stock:price:${symbol}`
      const listener = (raw: { price?: number; receivedAt?: number; source?: string; stale?: boolean }) => {
        const price = Number(raw.price)
        let receivedAt = Number(raw.receivedAt)
        if (receivedAt > 0 && receivedAt < 1e12) receivedAt *= 1000
        const age = Date.now() - receivedAt
        if (raw.source !== "dnse-ws" || raw.stale || !Number.isFinite(price) || price <= 0 ||
          !Number.isFinite(receivedAt) || age < -1000 || age > 30_000) return
        if (!pending[symbol] || receivedAt >= pending[symbol].receivedAt) pending[symbol] = { price, receivedAt }
      }
      socket.on(event, listener)
      const release = retainStock(symbol)
      return () => { socket.off(event, listener); release() }
    })
    let elapsed = 0
    const timer = setInterval(() => {
      setNow(Date.now())
      if (Object.keys(pending).length) {
        const batch = { ...pending }
        for (const symbol of Object.keys(pending)) delete pending[symbol]
        setLive(previous => {
          const ticks = previous?.accountId === accountId ? { ...previous.ticks } : {}
          for (const [symbol, tick] of Object.entries(batch)) {
            if (!ticks[symbol] || tick.receivedAt >= ticks[symbol].receivedAt) ticks[symbol] = tick
          }
          return { accountId, ticks }
        })
      }
      if (++elapsed % 60 === 0 && document.visibilityState === "visible") void reload()
    }, 1000)
    const reconnect = () => { void reload() }
    socket.on("connect", reconnect)
    return () => { clearInterval(timer); socket.off("connect", reconnect); releases.forEach(release => release()) }
  }, [accountId, symbols, reload])

  return useMemo(() => {
    if (!base) return null
    const positions = base.positions.map(position => {
      const tick = live?.accountId === accountId ? live.ticks[position.symbol] : undefined
      const newer = tick && tick.receivedAt > Date.parse(position.priceAsOf ?? "")
      const useTick = tick && (newer || !position.priceAsOf)
      const receivedAt = useTick ? tick.receivedAt : Date.parse(position.priceAsOf ?? "")
      const currentPrice = useTick ? tick.price : position.currentPrice
      const marketValue = currentPrice === null ? null : currentPrice * position.quantity
      const pnl = marketValue === null || position.costBasis === null ? null : marketValue - position.costBasis
      return { ...position, currentPrice, marketValue, pnl,
        pnlPercent: pnl !== null && position.costBasis ? pnl / position.costBasis * 100 : null,
        priceAsOf: useTick ? new Date(tick.receivedAt).toISOString() : position.priceAsOf,
        priceSource: useTick ? "dnse-ws" : position.priceSource,
        stale: useTick ? now - receivedAt > 30_000 : position.stale || now - receivedAt > 30_000 }
    })
    const marketValue = positions.some(p => p.marketValue === null) ? null : positions.reduce((sum, p) => sum + p.marketValue!, 0)
    const nav = marketValue === null ? null : base.summary.cash + marketValue
    const unrealizedPnl = marketValue === null || base.summary.totalCost === null ? null : marketValue - base.summary.totalCost
    const totalPnl = unrealizedPnl === null || base.summary.realizedPnl === null ? null : unrealizedPnl + base.summary.realizedPnl
    const dailyPnL = nav === null || base.summary.dailyBaseline === null ? null : nav - base.summary.dailyBaseline
    const summary = { ...base.summary, nav, unrealizedPnl, totalPnl, dailyPnL,
      dailyPnLPercent: dailyPnL !== null && base.summary.dailyBaseline ? dailyPnL / base.summary.dailyBaseline * 100 : null,
      totalReturnPct: totalPnl !== null && base.summary.openingCash ? totalPnl / base.summary.openingCash * 100 : null,
      stalePrices: positions.filter(p => p.stale).map(p => p.symbol) }
    return { ...base, summary, positions: positions.map(p => ({ ...p, weight: nav && p.marketValue !== null ? p.marketValue / nav * 100 : null })) }
  }, [base, live, now, accountId])
}
