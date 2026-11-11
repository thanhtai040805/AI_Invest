"use client"

import { useEffect } from "react"
import { useParams, useRouter } from "next/navigation"

export default function StockDetailRedirectPage() {
  const { symbol } = useParams<{ symbol: string }>()
  const router = useRouter()
  const cleanSymbol = (symbol || "HPG").trim().toUpperCase()

  useEffect(() => {
    router.replace(`/markets?symbol=${encodeURIComponent(cleanSymbol)}`)
  }, [cleanSymbol, router])

  return (
    <div className="flex min-h-[40vh] items-center justify-center text-[13px] text-muted font-mono">
      Đang mở bảng phân tích {cleanSymbol}…
    </div>
  )
}
