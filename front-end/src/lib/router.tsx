"use client"

import NextLink from "next/link"
import { usePathname, useRouter as useNextRouter } from "next/navigation"
import type { MouseEvent, ReactNode } from "react"

export const STOCK_MODAL_EVENT = "aiinvest:open-stock-modal"

export interface StockModalEventDetail {
  symbol: string
  symbols?: string[]
}

export function extractStockModalSymbol(to: string): string | null {
  const match = /^\/stock\/([^/?#]+)(?:[?#].*)?$/i.exec(to.trim())
  if (!match?.[1]) return null
  try {
    const clean = decodeURIComponent(match[1]).trim().toUpperCase()
    return clean || null
  } catch {
    return match[1].trim().toUpperCase() || null
  }
}

export function openStockModal(symbol: string, symbols?: string[]) {
  const clean = symbol.trim().toUpperCase()
  if (!clean || typeof window === "undefined") return
  window.dispatchEvent(
    new CustomEvent<StockModalEventDetail>(STOCK_MODAL_EVENT, {
      detail: { symbol: clean, symbols },
    })
  )
}

export function RouterProvider({ children }: { children: ReactNode }) {
  return children
}

export function useRouter() {
  const router = useNextRouter()
  const path = usePathname()
  return {
    path,
    navigate: (to: string) => {
      const stockSym = extractStockModalSymbol(to)
      if (stockSym) {
        openStockModal(stockSym)
        return
      }
      router.push(to)
    },
  }
}

export function Link({
  to,
  className,
  children,
  onClick,
  title,
}: {
  to: string
  className?: string
  children: ReactNode
  onClick?: () => void
  title?: string
}) {
  const stockSym = extractStockModalSymbol(to)
  if (stockSym) {
    const fallbackHref = `/markets?symbol=${encodeURIComponent(stockSym)}`
    const handleStockClick = (e: MouseEvent<HTMLAnchorElement>) => {
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) {
        onClick?.()
        return
      }
      e.preventDefault()
      e.stopPropagation()
      onClick?.()
      openStockModal(stockSym)
    }
    return (
      <a href={fallbackHref} className={className} title={title} onClick={handleStockClick}>
        {children}
      </a>
    )
  }

  return (
    <NextLink href={to} className={className} title={title} onClick={onClick}>
      {children}
    </NextLink>
  )
}
