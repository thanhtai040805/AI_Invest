"use client"

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { marketApi, portfolioApi, stockApi } from "@/lib/api"
import { getSocket, retainOhlc } from "@/lib/socket"
import { useRealtimeMarket, useRealtimeStock } from "@/lib/use-realtime"
import { usePortfolio, type PortfolioPosition, type PortfolioSnapshot } from "@/lib/use-portfolio"
import { fmt, fmtPct, Panel, PanelHead, Pill } from "@/components/ui"
import { TradingViewChart } from "@/lib/tv-chart"
import type { ApiMarketStock, Stock } from "@/types"

const DEFAULT_SYMBOLS = ["HPG", "FPT", "SSI", "MBB", "TCB", "VCB"]

interface RawCandle {
  date?: string
  time?: string | number
  timestamp?: string | number
  open?: number
  high?: number
  low?: number
  close?: number
  volume?: number
}

interface LiveOhlcTick {
  symbol?: string
  resolution?: string
  timestamp?: number | string
  open?: number
  high?: number
  low?: number
  close?: number
  volume?: number
}

interface CandlePoint {
  date: string
  label: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  sma20: number | null
}

interface BookLevel {
  price: number
  volume: number
}

interface OrderbookState {
  symbol: string
  bids: BookLevel[]
  asks: BookLevel[]
  receivedAt: number | null
  lastUpdate: string | null
  stale: boolean
}

interface TradeOrderRecord {
  id: string
  symbol: string
  stockName?: string
  sector?: string | null
  exchange?: string | null
  side: "BUY" | "SELL"
  orderType: string
  price: number
  limitPrice?: number
  avgFillPrice?: number | null
  quantity: number
  filledQuantity?: number
  remainingQuantity?: number
  lockedCash?: number
  lockedShares?: number
  status: string
  createdAt?: string
}

const vnDateFormatter = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Ho_Chi_Minh",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
})

function toVnDateKey(raw?: string | number | Date): string {
  if (raw == null || raw === "") return ""
  if (typeof raw === "string" && /^\d{4}-\d{2}-\d{2}/.test(raw)) return raw.slice(0, 10)
  const d =
    raw instanceof Date
      ? raw
      : typeof raw === "number"
        ? new Date(raw > 1e12 ? raw : raw * 1000)
        : /^\d+$/.test(raw)
          ? new Date(Number(raw) > 1e12 ? Number(raw) : Number(raw) * 1000)
          : new Date(raw)
  if (Number.isNaN(d.getTime())) return ""
  const parts = vnDateFormatter.formatToParts(d)
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? ""
  return `${get("year")}-${get("month")}-${get("day")}`
}

const fmtPrice = (n: number | null | undefined) =>
  n != null && Number.isFinite(Number(n)) && Number(n) > 0 ? fmt(Math.round(Number(n))) : "—"

const fmtVND = (n: number | null | undefined) =>
  n != null && Number.isFinite(Number(n)) ? fmt(Math.round(Number(n))) : "0"

const fmtNumber = (n: number | null | undefined) =>
  n != null && Number.isFinite(Number(n)) ? fmt(Math.round(Number(n))) : "0"

const fmtPercent = (n: number | null | undefined) =>
  n != null && Number.isFinite(Number(n)) ? fmtPct(Number(n)) : "0,00%"

const toneText = (val: number | null | undefined) => {
  const v = Number(val ?? 0)
  if (v > 0) return "text-[var(--color-gain)]"
  if (v < 0) return "text-[var(--color-loss)]"
  return "text-[var(--color-ref)]"
}

/**
 * Chuẩn 5 màu bảng điện chứng khoán Việt Nam (HOSE):
 * Tím (Trần) · Xanh lá (> TC) · Vàng (= TC) · Đỏ (< TC) · Xanh sàn (Sàn)
 */
function hosePriceTone(
  price: number | null | undefined,
  ref: number | null | undefined,
  ceiling?: number | null,
  floor?: number | null
): string {
  const p = Number(price ?? 0)
  if (!Number.isFinite(p) || p <= 0) return "text-[var(--color-muted)]"
  const c = Number(ceiling ?? 0)
  const f = Number(floor ?? 0)
  const r = Number(ref ?? 0)
  if (c > 0 && p >= c) return "text-[var(--color-ceiling)]"
  if (f > 0 && p <= f) return "text-[var(--color-floor)]"
  if (r > 0) {
    if (p === r) return "text-[var(--color-ref)]"
    return p > r ? "text-[var(--color-gain)]" : "text-[var(--color-loss)]"
  }
  return "text-[var(--color-ink)]"
}

function toVndPrice(value: unknown, fallback = 0): number {
  const n = Number(value ?? fallback)
  if (!Number.isFinite(n) || n <= 0) return fallback
  return n < 500 ? Math.round(n * 1000) : Math.round(n)
}

function getTickStep(price: number): number {
  if (price < 10_000) return 10
  if (price < 50_000) return 50
  return 100
}

function roundToTick(price: number): number {
  const step = getTickStep(price)
  return Math.max(step, Math.round(price / step) * step)
}

function resolveCoherentBands(ref: number, price: number, rawCeiling: number, rawFloor: number) {
  const anchor = ref > 0 ? ref : price
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

function formatCompactVol(vol: number): string {
  if (!Number.isFinite(vol) || vol <= 0) return "0"
  if (vol >= 1_000_000) return `${(vol / 1_000_000).toFixed(2).replace(".", ",")} tr`
  if (vol >= 1_000) return `${(vol / 1_000).toFixed(1).replace(".", ",")} K`
  return fmtNumber(vol)
}

/**
 * Hiển thị khối lượng chuẩn bảng giá HOSE:
 * Giữ nguyên đơn vị cổ phiếu (VD: 125.400) để trader đọc lô chính xác,
 * chỉ rút gọn triệu khi >= 10.000.000 CP để không tràn cột.
 */
function formatBookVolume(vol: number): string {
  if (!Number.isFinite(vol) || vol <= 0) return "—"
  if (vol >= 10_000_000) return `${(vol / 1_000_000).toFixed(2).replace(".", ",")} tr`
  return fmtNumber(vol)
}

function normalizeBookLevels(raw: unknown, side: "BID" | "ASK"): BookLevel[] {
  if (!Array.isArray(raw)) return []
  const byPrice = new Map<number, number>()
  for (const item of raw) {
    let rawPrice: unknown
    let rawVol: unknown
    if (Array.isArray(item)) {
      rawPrice = item[0]
      rawVol = item[1]
    } else if (item && typeof item === "object") {
      const rec = item as Record<string, unknown>
      rawPrice = rec.price ?? rec.p
      rawVol = rec.volume ?? rec.q ?? rec.quantity
    }
    const price = toVndPrice(rawPrice, 0)
    const volume = Math.round(Number(rawVol ?? 0))
    if (price > 0 && Number.isFinite(volume) && volume > 0) {
      byPrice.set(price, (byPrice.get(price) ?? 0) + volume)
    }
  }
  const levels = Array.from(byPrice.entries()).map(([price, volume]) => ({ price, volume }))
  levels.sort((a, b) => (side === "BID" ? b.price - a.price : a.price - b.price))
  return levels.slice(0, 3)
}

function formatTimeVi(msOrDate?: number | Date | null): string {
  if (!msOrDate) return ""
  const d = msOrDate instanceof Date ? msOrDate : new Date(msOrDate)
  if (Number.isNaN(d.getTime())) return ""
  return d.toLocaleTimeString("vi-VN", {
    timeZone: "Asia/Ho_Chi_Minh",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  })
}

function formatDateTimeVi(iso?: string): { date: string; time: string } {
  if (!iso) return { date: "—", time: "" }
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return { date: iso.slice(0, 10), time: "" }
  return {
    date: d.toLocaleDateString("vi-VN", { day: "2-digit", month: "2-digit", year: "numeric" }),
    time: d.toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" }),
  }
}

function computeOrderFinancials(side: "BUY" | "SELL", price: number, quantity: number) {
  const gross = Math.max(0, Math.round(price * quantity))
  const fee = gross > 0 ? Math.max(Math.round(gross * 0.001), 10_000) : 0
  const tax = side === "SELL" && gross > 0 ? Math.round(gross * 0.001) : 0
  const netCash = side === "BUY" ? -(gross + fee) : gross - fee - tax
  return { gross, fee, tax, netCash, totalCostOrProceeds: Math.abs(netCash) }
}

function formatFeedStampVi(ms?: number | null, iso?: string | null): string {
  const d = ms && ms > 0 ? new Date(ms) : iso ? new Date(iso) : null
  if (!d || Number.isNaN(d.getTime())) return ""
  const timeStr = d.toLocaleTimeString("vi-VN", {
    timeZone: "Asia/Ho_Chi_Minh",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  })
  const dateStr = d.toLocaleDateString("vi-VN", {
    timeZone: "Asia/Ho_Chi_Minh",
    day: "2-digit",
    month: "2-digit",
  })
  return `${timeStr} ${dateStr}`
}

function mapStockQuote(
  symbol: string,
  quote: Record<string, unknown> | null,
  profile: Record<string, unknown> | null
): Stock {
  const price = toVndPrice(quote?.price ?? quote?.close)
  const rawRefCandidate = quote?.ref ?? quote?.prevClose ?? quote?.prior_close
  const ref = rawRefCandidate == null ? price : toVndPrice(rawRefCandidate, price)
  const rawChange =
    quote?.change_pct ?? quote?.changePercent ?? quote?.changePct ?? quote?.change_percent
  const changePct =
    rawChange != null && Number.isFinite(Number(rawChange))
      ? Number(Number(rawChange).toFixed(2))
      : ref > 0 && price > 0
        ? Number((((price - ref) / ref) * 100).toFixed(2))
        : 0
  const rawCeiling = toVndPrice(quote?.ceiling, 0)
  const rawFloor = toVndPrice(quote?.floor, 0)
  const { ceiling, floor } = resolveCoherentBands(ref, price, rawCeiling, rawFloor)
  const volNum = Number(quote?.volume ?? quote?.volume_total ?? 0)
  const rawName = String(profile?.name ?? quote?.name ?? symbol).trim()

  return {
    symbol,
    name: rawName && rawName.toUpperCase() !== symbol.toUpperCase() ? rawName : `Cổ phiếu ${symbol}`,
    sector: String(profile?.industry ?? profile?.sector ?? "HOSE"),
    price,
    changePct,
    ref,
    ceiling,
    floor,
    volume: volNum > 0 ? formatCompactVol(volNum) : "—",
    foreign: Number(quote?.foreign_flow ?? quote?.foreign_net_vol ?? 0),
    weight: 0,
    momentum: 0,
    rs: 0,
    flow: 0,
    factor: "Neutral",
    risk: "Unknown",
    beneish: "UNKNOWN",
    spark: [],
  }
}

/* ============================================================================
 * COMPONENT 2: BẢNG CHÀO MUA – BÁN CHUẨN HOSE (T-BOARD 3 BƯỚC GIÁ LIVE)
 * - Đối xứng chuẩn bảng điện chứng khoán Việt Nam:
 *   Trái (Dư mua · Bid): Bậc | KL Mua | Giá Mua  <-->  Giá Bán | KL Bán | Bậc :Phải (Ask · Dư bán)
 * - Giá Mua 1 (cao nhất) và Giá Bán 1 (thấp nhất) đối diện nhau qua trục Spread trung tâm
 * - Áp dụng chuẩn 5 màu giá HOSE (Trần tím · Tăng xanh · TC vàng · Giảm đỏ · Sàn xanh lơ)
 * - Tự động cập nhật 100% qua WebSocket DNSE (không có nút Làm mới thủ công, không bịa số liệu)
 * ============================================================================ */
function BidAskOrderbook({
  bids,
  asks,
  refPrice,
  ceilingPrice,
  floorPrice,
  selectedOrderPrice,
  isLiveStream,
  orderbookStale,
  receivedAt,
  lastUpdate,
  socketConnected,
  onSelectPrice,
}: {
  bids: BookLevel[]
  asks: BookLevel[]
  refPrice: number
  ceilingPrice: number
  floorPrice: number
  selectedOrderPrice: number
  isLiveStream: boolean
  orderbookStale: boolean
  receivedAt: number | null
  lastUpdate: string | null
  socketConnected: boolean
  onSelectPrice: (price: number) => void
}) {
  const hasRealDepth = bids.length > 0 || asks.length > 0
  const bestBid = bids[0]?.price ?? 0
  const bestAsk = asks[0]?.price ?? 0
  const spread = bestBid > 0 && bestAsk > 0 && bestAsk >= bestBid ? bestAsk - bestBid : null
  const spreadPct =
    spread !== null && bestBid > 0 ? ((spread / bestBid) * 100).toFixed(2).replace(".", ",") : null

  const totalBidVol = useMemo(() => bids.reduce((sum, b) => sum + b.volume, 0), [bids])
  const totalAskVol = useMemo(() => asks.reduce((sum, a) => sum + a.volume, 0), [asks])
  const totalBookVol = totalBidVol + totalAskVol
  const bidPct = totalBookVol > 0 ? Math.round((totalBidVol / totalBookVol) * 100) : 50
  const askPct = totalBookVol > 0 ? 100 - bidPct : 50

  const maxLevelVol = useMemo(
    () => Math.max(...bids.map((b) => b.volume), ...asks.map((a) => a.volume), 1),
    [bids, asks]
  )

  const liveTime = formatTimeVi(receivedAt)
  const snapshotStamp = formatFeedStampVi(receivedAt, lastUpdate)
  const isRealtimeBook = hasRealDepth && !orderbookStale

  return (
    <div className="mt-3 pt-3 border-t border-[var(--color-line)]">
      {/* Header Row: Title + Live Stream Telemetry (Zero manual refresh button) */}
      <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
        <div className="flex items-center gap-2 min-w-0">
          <h3 className="text-[13.5px] font-semibold text-[var(--color-ink)] whitespace-nowrap">
            Bảng chào Mua / Bán (Bid · Ask)
          </h3>
          <span className="hidden sm:inline text-[11.5px] text-[var(--color-muted)] whitespace-nowrap">
            · 3 bước giá chuẩn HOSE
          </span>
        </div>

        <div className="flex items-center gap-3 font-mono text-[11px] tabular-nums whitespace-nowrap">
          {spread !== null && (
            <span className="rounded bg-[var(--color-surface-2)] px-2 py-0.5 text-[var(--color-ink-2)] border border-[var(--color-line)]">
              Chênh lệch: <strong>{fmtPrice(spread)} ₫</strong>
              {spreadPct ? ` (${spreadPct}%)` : ""}
            </span>
          )}

          <span className="inline-flex items-center gap-1.5 text-[var(--color-muted)]">
            <span
              className={`h-1.5 w-1.5 rounded-full ${
                isRealtimeBook || isLiveStream
                  ? "bg-[var(--color-gain)] animate-pulse"
                  : socketConnected
                    ? "bg-[var(--color-mineral)] animate-pulse"
                    : "bg-[var(--color-ref)]"
              }`}
            />
            {isRealtimeBook
              ? `Trực tiếp${liveTime ? ` · ${liveTime}` : ""}`
              : hasRealDepth
                ? `Feed ${snapshotStamp || "phiên gần nhất"}`
                : socketConnected
                  ? "Tự động nhận trực tiếp"
                  : "Đang kết nối luồng giá…"}
          </span>
        </div>
      </div>

      {/* Unified T-Board Container */}
      <div className="overflow-hidden rounded-[var(--radius-md)] border border-[var(--color-line)] bg-[var(--color-surface)]">
        <div className="grid grid-cols-1 md:grid-cols-2 md:divide-x md:divide-[var(--color-line-strong)]">
          {/* TRÁI: DƯ MUA (BID) — Giá Mua sát trục giữa */}
          <div>
            <div className="grid grid-cols-[58px_1fr_88px] items-center border-b border-[var(--color-line)] bg-[var(--color-surface-2)]/85 px-3 py-1.5 font-mono text-[10.5px] uppercase tracking-[0.06em] text-[var(--color-muted)] whitespace-nowrap">
              <span>Dư mua</span>
              <span className="text-right">KL mua</span>
              <span className="text-right font-semibold text-[var(--color-gain)]">Giá mua</span>
            </div>

            <div className="divide-y divide-[var(--color-line)]/60">
              {[0, 1, 2].map((idx) => {
                const level = bids[idx]
                const rank = idx + 1
                if (!level) {
                  return (
                    <div
                      key={`bid-empty-${rank}`}
                      className="grid h-[33px] grid-cols-[58px_1fr_88px] items-center px-3 font-mono text-[12px] tabular-nums text-[var(--color-muted)] whitespace-nowrap"
                    >
                      <span className="text-[11px]">Mua {rank}</span>
                      <span className="text-right">—</span>
                      <span className="text-right">—</span>
                    </div>
                  )
                }

                const depthPct = Math.max(6, Math.round((level.volume / maxLevelVol) * 100))
                const isSelected = selectedOrderPrice === level.price
                const tone = hosePriceTone(level.price, refPrice, ceilingPrice, floorPrice)

                return (
                  <button
                    key={`bid-${rank}-${level.price}`}
                    type="button"
                    onClick={() => onSelectPrice(level.price)}
                    title={`Chọn giá Mua ${rank}: ${fmtPrice(level.price)} ₫`}
                    className={`group relative grid h-[33px] w-full grid-cols-[58px_1fr_88px] items-center overflow-hidden px-3 text-left font-mono text-[12.5px] tabular-nums transition-colors whitespace-nowrap ${
                      isSelected
                        ? "bg-[var(--color-gain-soft)]/50 ring-1 ring-inset ring-[var(--color-gain)]/50"
                        : "hover:bg-[var(--color-surface-2)]/75"
                    }`}
                  >
                    {/* Thanh độ sâu thanh khoản neo từ trục Spread trung tâm tỏa sang trái */}
                    <span
                      className="pointer-events-none absolute inset-y-0.5 right-0 bg-[var(--color-gain)]/14 transition-[width] duration-300"
                      style={{ width: `${depthPct}%` }}
                    />
                    <span
                      className={`relative text-[11px] ${
                        rank === 1
                          ? "font-semibold text-[var(--color-ink)]"
                          : "text-[var(--color-muted)] group-hover:text-[var(--color-ink-2)]"
                      }`}
                    >
                      Mua {rank}
                    </span>
                    <span
                      className={`relative text-right ${
                        rank === 1 ? "font-medium text-[var(--color-ink)]" : "text-[var(--color-ink-2)]"
                      }`}
                    >
                      {formatBookVolume(level.volume)}
                    </span>
                    <span
                      className={`relative text-right ${rank === 1 ? "font-bold" : "font-semibold"} ${tone}`}
                    >
                      {fmtPrice(level.price)}
                    </span>
                  </button>
                )
              })}
            </div>
          </div>

          {/* PHẢI: DƯ BÁN (ASK) — Giá Bán sát trục giữa */}
          <div className="border-t border-[var(--color-line)] md:border-t-0">
            <div className="grid grid-cols-[88px_1fr_58px] items-center border-b border-[var(--color-line)] bg-[var(--color-surface-2)]/85 px-3 py-1.5 font-mono text-[10.5px] uppercase tracking-[0.06em] text-[var(--color-muted)] whitespace-nowrap">
              <span className="font-semibold text-[var(--color-loss)]">Giá bán</span>
              <span className="text-right">KL bán</span>
              <span className="text-right">Dư bán</span>
            </div>

            <div className="divide-y divide-[var(--color-line)]/60">
              {[0, 1, 2].map((idx) => {
                const level = asks[idx]
                const rank = idx + 1
                if (!level) {
                  return (
                    <div
                      key={`ask-empty-${rank}`}
                      className="grid h-[33px] grid-cols-[88px_1fr_58px] items-center px-3 font-mono text-[12px] tabular-nums text-[var(--color-muted)] whitespace-nowrap"
                    >
                      <span>—</span>
                      <span className="text-right">—</span>
                      <span className="text-right text-[11px]">Bán {rank}</span>
                    </div>
                  )
                }

                const depthPct = Math.max(6, Math.round((level.volume / maxLevelVol) * 100))
                const isSelected = selectedOrderPrice === level.price
                const tone = hosePriceTone(level.price, refPrice, ceilingPrice, floorPrice)

                return (
                  <button
                    key={`ask-${rank}-${level.price}`}
                    type="button"
                    onClick={() => onSelectPrice(level.price)}
                    title={`Chọn giá Bán ${rank}: ${fmtPrice(level.price)} ₫`}
                    className={`group relative grid h-[33px] w-full grid-cols-[88px_1fr_58px] items-center overflow-hidden px-3 text-left font-mono text-[12.5px] tabular-nums transition-colors whitespace-nowrap ${
                      isSelected
                        ? "bg-[var(--color-loss-soft)]/50 ring-1 ring-inset ring-[var(--color-loss)]/50"
                        : "hover:bg-[var(--color-surface-2)]/75"
                    }`}
                  >
                    {/* Thanh độ sâu thanh khoản neo từ trục Spread trung tâm tỏa sang phải */}
                    <span
                      className="pointer-events-none absolute inset-y-0.5 left-0 bg-[var(--color-loss)]/14 transition-[width] duration-300"
                      style={{ width: `${depthPct}%` }}
                    />
                    <span className={`relative ${rank === 1 ? "font-bold" : "font-semibold"} ${tone}`}>
                      {fmtPrice(level.price)}
                    </span>
                    <span
                      className={`relative text-right ${
                        rank === 1 ? "font-medium text-[var(--color-ink)]" : "text-[var(--color-ink-2)]"
                      }`}
                    >
                      {formatBookVolume(level.volume)}
                    </span>
                    <span
                      className={`relative text-right text-[11px] ${
                        rank === 1
                          ? "font-semibold text-[var(--color-ink)]"
                          : "text-[var(--color-muted)] group-hover:text-[var(--color-ink-2)]"
                      }`}
                    >
                      Bán {rank}
                    </span>
                  </button>
                )
              })}
            </div>
          </div>
        </div>

        {/* Thanh tổng kết tỷ trọng Dư Mua / Dư Bán */}
        <div className="flex items-center justify-between gap-3 border-t border-[var(--color-line)] bg-[var(--color-surface-2)]/55 px-3 py-1.5 font-mono text-[11px] tabular-nums whitespace-nowrap">
          {hasRealDepth ? (
            <>
              <span className="font-medium text-[var(--color-gain)]">
                Tổng mua: <strong>{formatBookVolume(totalBidVol)}</strong> ({bidPct}%)
              </span>
              <div className="hidden sm:flex h-1.5 flex-1 max-w-[200px] overflow-hidden rounded-full bg-[var(--color-line)]">
                <div
                  className="bg-[var(--color-gain)] transition-[width] duration-300"
                  style={{ width: `${bidPct}%` }}
                />
                <div
                  className="bg-[var(--color-loss)] transition-[width] duration-300"
                  style={{ width: `${askPct}%` }}
                />
              </div>
              <span className="font-medium text-[var(--color-loss)]">
                ({askPct}%) <strong>{formatBookVolume(totalAskVol)}</strong> :Tổng bán
              </span>
            </>
          ) : (
            <>
              <span className="text-[var(--color-muted)]">Tổng mua: —</span>
              <span className="truncate font-sans text-[11px] text-[var(--color-muted)]">
                Chờ dữ liệu chào Mua / Bán từ phiên giao dịch HOSE (09:00–15:00)
              </span>
              <span className="text-[var(--color-muted)]">Tổng bán: —</span>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

/* ============================================================================
 * MAIN PAGE: GIAO DỊCH CỔ PHIẾU (/trade) — 100% LIVE STREAMING & CHUẨN HOSE
 * ============================================================================ */
export default function TradePage() {
  const [baseSnapshot, setBaseSnapshot] = useState<PortfolioSnapshot | null>(null)
  const reloadPortfolio = useCallback(async () => {
    try {
      const snap = (await portfolioApi.snapshot()) as PortfolioSnapshot
      setBaseSnapshot(snap)
    } catch {
      // keep previous snapshot
    }
  }, [])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void reloadPortfolio()
    }, 0)
    return () => window.clearTimeout(timer)
  }, [reloadPortfolio])

  const portfolioData = usePortfolio(baseSnapshot, reloadPortfolio)

  const [symbol, setSymbol] = useState(() => {
    if (typeof window !== "undefined") {
      const p = new URLSearchParams(window.location.search).get("symbol")?.trim().toUpperCase()
      if (p && /^[A-Z0-9]{1,10}$/.test(p)) return p
    }
    return "HPG"
  })
  const [searchQuery, setSearchQuery] = useState("")
  const [searchOpen, setSearchOpen] = useState(false)
  const [seedUniverse, setSeedUniverse] = useState<ApiMarketStock[]>([])
  const searchBoxRef = useRef<HTMLDivElement | null>(null)

  const [initialStock, setInitialStock] = useState<Stock | undefined>(undefined)
  const [rawCandles, setRawCandles] = useState<RawCandle[]>([])
  const [apiOrderbook, setApiOrderbook] = useState<OrderbookState>({
    symbol: "HPG",
    bids: [],
    asks: [],
    receivedAt: null,
    lastUpdate: null,
    stale: true,
  })
  const [liveForeignBillion, setLiveForeignBillion] = useState<number | null>(null)
  const [socketConnected, setSocketConnected] = useState<boolean>(() =>
    typeof window !== "undefined" ? getSocket().connected : false
  )

  // Order Ticket state
  const [side, setSide] = useState<"BUY" | "SELL">(() => {
    if (typeof window !== "undefined") {
      const s = new URLSearchParams(window.location.search).get("side")?.trim().toUpperCase()
      if (s === "BUY" || s === "SELL") return s
    }
    return "BUY"
  })
  const [orderType, setOrderType] = useState<"LO" | "MP" | "ATO" | "ATC">("LO")
  const [qtyInput, setQtyInput] = useState("100")
  const [priceInput, setPriceInput] = useState(() => {
    if (typeof window !== "undefined") {
      const p = Number(new URLSearchParams(window.location.search).get("price") ?? 0)
      if (Number.isFinite(p) && p > 0) return String(Math.round(p))
    }
    return ""
  })
  const [priceInputFlash, setPriceInputFlash] = useState(false)
  const [showDraftOrderOnChart, setShowDraftOrderOnChart] = useState(() => {
    if (typeof window !== "undefined") {
      const p = Number(new URLSearchParams(window.location.search).get("price") ?? 0)
      return Number.isFinite(p) && p > 0
    }
    return false
  })
  const [submitting, setSubmitting] = useState(false)
  const [actionOrderId, setActionOrderId] = useState<string | null>(null)
  const [amendingOrder, setAmendingOrder] = useState<TradeOrderRecord | null>(null)
  const [amendPriceInput, setAmendPriceInput] = useState("")
  const [amendQtyInput, setAmendQtyInput] = useState("")
  const [orderNotice, setOrderNotice] = useState<{
    tone: "gain" | "loss"
    text: string
    orderId?: string
  } | null>(null)

  // Order History state
  const [ledgerFilter, setLedgerFilter] = useState<"ALL" | "OPEN" | "SYMBOL" | "BUY" | "SELL">("ALL")
  const [ledgerSearch, setLedgerSearch] = useState("")
  const [selectedOrderReceipt, setSelectedOrderReceipt] = useState<TradeOrderRecord | null>(null)

  // Live market-wide snapshot via WebSocket
  const { snapshot: liveMarketSnapshot } = useRealtimeMarket()

  useEffect(() => {
    let active = true
    marketApi
      .snapshot()
      .then((snap) => {
        if (!active) return
        const list = Array.isArray((snap as { stocks?: unknown })?.stocks)
          ? ((snap as { stocks: ApiMarketStock[] }).stocks ?? [])
          : []
        setSeedUniverse(list)
      })
      .catch(() => {})
    return () => {
      active = false
    }
  }, [])

  const marketUniverse = useMemo(() => {
    if (Array.isArray(liveMarketSnapshot?.stocks) && liveMarketSnapshot.stocks.length > 0) {
      return liveMarketSnapshot.stocks
    }
    return seedUniverse
  }, [liveMarketSnapshot?.stocks, seedUniverse])

  useEffect(() => {
    function handleOutside(e: MouseEvent) {
      if (searchBoxRef.current && !searchBoxRef.current.contains(e.target as Node)) {
        setSearchOpen(false)
      }
    }
    document.addEventListener("mousedown", handleOutside)
    return () => document.removeEventListener("mousedown", handleOutside)
  }, [])

  const selectSymbol = (nextSym: string, nextSide?: "BUY" | "SELL", prefillQty?: number) => {
    const clean = nextSym.trim().toUpperCase()
    if (!clean) return
    setSymbol(clean)
    setPriceInput("")
    setShowDraftOrderOnChart(false)
    setOrderNotice(null)
    setLiveForeignBillion(null)
    setSearchOpen(false)
    setSearchQuery("")
    if (nextSide) setSide(nextSide)
    if (prefillQty && prefillQty >= 100) {
      setQtyInput(String(Math.floor(prefillQty / 100) * 100))
    }
    if (typeof window !== "undefined") {
      const url = new URL(window.location.href)
      url.searchParams.set("symbol", clean)
      window.history.replaceState({}, "", url.toString())
    }
  }

  const syncOrderbookSnapshot = useCallback((sym: string) => {
    const targetSym = sym.trim().toUpperCase()
    stockApi
      .orderbook(targetSym)
      .then((ob) => {
        const rec = (ob ?? {}) as Record<string, unknown>
        const lastUpdate = rec.lastUpdate ? String(rec.lastUpdate) : null
        let receivedAt = Number(rec.receivedAt ?? 0)
        if (receivedAt > 0 && receivedAt < 1_000_000_000_000) receivedAt *= 1000
        if (!receivedAt && lastUpdate) {
          receivedAt = Date.parse(lastUpdate) || 0
        }
        const stale = rec.stale === true || !receivedAt || Date.now() - receivedAt >= 15_000
        setApiOrderbook({
          symbol: targetSym,
          bids: normalizeBookLevels(rec.bids, "BID"),
          asks: normalizeBookLevels(rec.asks, "ASK"),
          receivedAt: receivedAt > 0 ? receivedAt : null,
          lastUpdate,
          stale,
        })
      })
      .catch(() =>
        setApiOrderbook({
          symbol: targetSym,
          bids: [],
          asks: [],
          receivedAt: null,
          lastUpdate: null,
          stale: true,
        })
      )
  }, [])

  // Initial seed per symbol (REST seed, then WebSocket takes over 100% live)
  useEffect(() => {
    let active = true
    const targetSym = symbol.trim().toUpperCase()
    Promise.all([
      stockApi.quote(targetSym).catch(() => null),
      stockApi.profile(targetSym).catch(() => null),
      stockApi.ohlcv(targetSym, { limit: 130, interval: "1D" }).catch(() => []),
      stockApi.orderbook(targetSym).catch(() => null),
    ]).then(([quote, profile, ohlcv, ob]) => {
      if (!active) return
      const mapped = mapStockQuote(
        targetSym,
        quote as Record<string, unknown> | null,
        profile as Record<string, unknown> | null
      )
      setInitialStock(mapped)
      setPriceInput((prev) => (prev ? prev : mapped.price > 0 ? String(mapped.price) : ""))
      const candleList = Array.isArray(ohlcv)
        ? ohlcv
        : Array.isArray((ohlcv as { data?: unknown })?.data)
          ? ((ohlcv as { data: RawCandle[] }).data ?? [])
          : []
      setRawCandles(candleList as RawCandle[])

      const rec = (ob ?? {}) as Record<string, unknown>
      const lastUpdate = rec.lastUpdate ? String(rec.lastUpdate) : null
      let receivedAt = Number(rec.receivedAt ?? 0)
      if (receivedAt > 0 && receivedAt < 1_000_000_000_000) receivedAt *= 1000
      if (!receivedAt && lastUpdate) {
        receivedAt = Date.parse(lastUpdate) || 0
      }
      const stale = rec.stale === true || !receivedAt || Date.now() - receivedAt >= 15_000
      setApiOrderbook({
        symbol: targetSym,
        bids: normalizeBookLevels(rec.bids, "BID"),
        asks: normalizeBookLevels(rec.asks, "ASK"),
        receivedAt: receivedAt > 0 ? receivedAt : null,
        lastUpdate,
        stale,
      })
    })
    return () => {
      active = false
    }
  }, [symbol])

  // Subscribe to live 1D OHLC candles, foreign flow & automatic reconnect sync
  useEffect(() => {
    const sym = symbol.trim().toUpperCase()
    if (!sym) return
    const socket = getSocket()
    const releaseOhlc = retainOhlc(sym, "1D")

    const onConnect = () => {
      setSocketConnected(true)
      syncOrderbookSnapshot(sym)
      void reloadPortfolio()
    }

    const onDisconnect = () => {
      setSocketConnected(false)
    }

    const onOhlcTick = (data: LiveOhlcTick) => {
      if (!data || (data.symbol && data.symbol.toUpperCase() !== sym)) return
      if (data.resolution && data.resolution.toUpperCase() !== "1D") return
      const close = toVndPrice(data.close)
      if (close <= 0) return
      const tickDateKey = toVnDateKey(data.timestamp) || toVnDateKey(new Date())
      setRawCandles((prev) => {
        if (!prev.length) {
          return [
            {
              date: tickDateKey,
              open: toVndPrice(data.open, close),
              high: Math.max(toVndPrice(data.high, close), close),
              low: Math.min(toVndPrice(data.low, close), close),
              close,
              volume: Number(data.volume ?? 0),
            },
          ]
        }
        const next = [...prev]
        const last = next[next.length - 1]
        const lastKey = toVnDateKey(last.date ?? last.time ?? last.timestamp)
        if (lastKey && lastKey === tickDateKey) {
          const prevOpen = toVndPrice(last.open, close)
          const prevHigh = toVndPrice(last.high, close)
          const prevLow = toVndPrice(last.low, close)
          next[next.length - 1] = {
            ...last,
            open: toVndPrice(data.open, prevOpen),
            high: Math.max(toVndPrice(data.high, prevHigh), close, prevHigh),
            low: Math.min(toVndPrice(data.low, prevLow), close, prevLow),
            close,
            volume: Math.max(Number(data.volume ?? 0), Number(last.volume ?? 0)),
          }
        } else if (!lastKey || tickDateKey > lastKey) {
          next.push({
            date: tickDateKey,
            open: toVndPrice(data.open, close),
            high: Math.max(toVndPrice(data.high, close), close),
            low: Math.min(toVndPrice(data.low, close), close),
            close,
            volume: Number(data.volume ?? 0),
          })
        }
        return next
      })
    }

    const onForeignTick = (data: Record<string, unknown>) => {
      if (!data) return
      const rawFlow = data.foreign_flow ?? data.netValueBillion
      if (rawFlow != null && Number.isFinite(Number(rawFlow))) {
        setLiveForeignBillion(Number(rawFlow))
        return
      }
      const netVnd =
        data.netValue != null
          ? Number(data.netValue)
          : data.buyValue != null || data.sellValue != null
            ? Number(data.buyValue ?? 0) - Number(data.sellValue ?? 0)
            : NaN
      if (Number.isFinite(netVnd)) {
        setLiveForeignBillion(netVnd / 1_000_000_000)
      }
    }

    const onOrderUpdated = () => {
      syncOrderbookSnapshot(sym)
      void reloadPortfolio()
    }

    socket.on("connect", onConnect)
    socket.on("disconnect", onDisconnect)
    socket.on(`stock:ohlc:${sym}`, onOhlcTick)
    socket.on(`stock:ohlcClosed:${sym}`, onOhlcTick)
    socket.on(`stock:foreign:${sym}`, onForeignTick)
    socket.on("portfolio:order_updated", onOrderUpdated)

    return () => {
      releaseOhlc()
      socket.off("connect", onConnect)
      socket.off("disconnect", onDisconnect)
      socket.off(`stock:ohlc:${sym}`, onOhlcTick)
      socket.off(`stock:ohlcClosed:${sym}`, onOhlcTick)
      socket.off(`stock:foreign:${sym}`, onForeignTick)
      socket.off("portfolio:order_updated", onOrderUpdated)
    }
  }, [symbol, syncOrderbookSnapshot, reloadPortfolio])

  const {
    stock: liveStock,
    orderbook: wsOrderbook,
    isLive,
    lastTickAt,
    flash,
  } = useRealtimeStock(symbol, initialStock)

  const snapshotMatch = useMemo(
    () => marketUniverse.find((item) => String(item.symbol ?? "").toUpperCase() === symbol),
    [marketUniverse, symbol]
  )

  const stock = useMemo(() => {
    const base = liveStock ?? initialStock ?? mapStockQuote(symbol, null, null)
    const price = toVndPrice(base.price)
    const rawRef = base.ref != null && base.ref > 0 ? toVndPrice(base.ref, price) : price
    const { ceiling, floor } = resolveCoherentBands(
      rawRef,
      price,
      toVndPrice(base.ceiling, 0),
      toVndPrice(base.floor, 0)
    )
    const snapName = String(snapshotMatch?.name ?? "").trim()
    const cleanName =
      base.name && !base.name.startsWith("Cổ phiếu ") && base.name.toUpperCase() !== symbol
        ? base.name
        : snapName && snapName.toUpperCase() !== symbol
          ? snapName
          : `Cổ phiếu ${symbol}`
    const foreignFlow =
      liveForeignBillion != null
        ? liveForeignBillion
        : snapshotMatch?.foreign_flow != null && Number.isFinite(Number(snapshotMatch.foreign_flow))
          ? Number(snapshotMatch.foreign_flow)
          : null
    const rawVolStr = String(base.volume ?? "").trim()
    const parsedVolNum = /^\d+(\.\d+)?$/.test(rawVolStr) ? Number(rawVolStr) : NaN
    const snapVolNum = Number(snapshotMatch?.volume ?? 0)
    const displayVolume = Number.isFinite(parsedVolNum) && parsedVolNum > 0
      ? formatCompactVol(parsedVolNum)
      : rawVolStr && rawVolStr !== "—" && rawVolStr !== "0"
        ? rawVolStr
        : snapVolNum > 0
          ? formatCompactVol(snapVolNum)
          : "—"

    const priceDiff = rawRef > 0 && price > 0 ? price - rawRef : 0
    const changePct =
      base.changePct != null && Number.isFinite(Number(base.changePct))
        ? Number(Number(base.changePct).toFixed(2))
        : rawRef > 0 && price > 0
          ? Number((((price - rawRef) / rawRef) * 100).toFixed(2))
          : 0

    return {
      ...base,
      price,
      ref: rawRef,
      ceiling,
      floor,
      changePct,
      volume: displayVolume,
      name: cleanName,
      foreignFlow,
      priceDiff,
    }
  }, [liveStock, initialStock, snapshotMatch, liveForeignBillion, symbol])

  const candles: CandlePoint[] = useMemo(() => {
    const valid = rawCandles
      .map((item) => {
        const close = toVndPrice(item.close)
        const open = toVndPrice(item.open, close)
        const high = Math.max(toVndPrice(item.high, close), open, close)
        const low = Math.min(toVndPrice(item.low, close), open, close)
        const volume = Number(item.volume ?? 0)
        const rawDate = String(item.date ?? item.time ?? item.timestamp ?? "")
        const d = new Date(rawDate)
        const label = Number.isNaN(d.getTime())
          ? rawDate.slice(0, 10)
          : d.toLocaleDateString("vi-VN", { day: "2-digit", month: "2-digit" })
        return { date: rawDate, label, open, high, low, close, volume }
      })
      .filter((c) => c.close > 0)

    // Merge live matched price into the latest session candle so chart updates tick-by-tick
    if (isLive && stock.price > 0 && valid.length > 0) {
      const todayKey = toVnDateKey(new Date())
      const lastIdx = valid.length - 1
      const last = valid[lastIdx]
      const lastKey = toVnDateKey(last.date)
      if (!lastKey || lastKey === todayKey) {
        valid[lastIdx] = {
          ...last,
          close: stock.price,
          high: Math.max(last.high, stock.price),
          low: Math.min(last.low, stock.price),
        }
      }
    }

    return valid.map((c, idx) => {
      if (idx < 19) return { ...c, sma20: null }
      const windowSlice = valid.slice(idx - 19, idx + 1)
      const avg = windowSlice.reduce((s, item) => s + item.close, 0) / 20
      return { ...c, sma20: avg }
    })
  }, [rawCandles, isLive, stock.price])

  const latestCandle = candles.length > 0 ? candles[candles.length - 1] : null

  // Real-time Orderbook state: strictly exchange-sourced (wsOrderbook + REST seed scoped per symbol)
  const resolvedOrderbook = useMemo<OrderbookState>(() => {
    const cleanSym = symbol.trim().toUpperCase()
    const matchedApi =
      apiOrderbook.symbol === cleanSym
        ? apiOrderbook
        : {
            symbol: cleanSym,
            bids: [],
            asks: [],
            receivedAt: null,
            lastUpdate: null,
            stale: true,
          }

    if (wsOrderbook && wsOrderbook.symbol === cleanSym) {
      const wsBids = normalizeBookLevels(wsOrderbook.bids, "BID")
      const wsAsks = normalizeBookLevels(wsOrderbook.asks, "ASK")
      const wsTs = wsOrderbook.receivedAt || 0
      const apiTs = matchedApi.receivedAt || 0
      if (wsBids.length > 0 || wsAsks.length > 0 || wsTs >= apiTs) {
        return {
          symbol: cleanSym,
          bids: wsBids,
          asks: wsAsks,
          receivedAt: wsOrderbook.receivedAt || matchedApi.receivedAt,
          lastUpdate: matchedApi.lastUpdate,
          stale: wsOrderbook.stale,
        }
      }
    }
    return matchedApi
  }, [wsOrderbook, apiOrderbook, symbol])

  const activeBids = resolvedOrderbook.bids
  const activeAsks = resolvedOrderbook.asks
  const bestBidPrice = activeBids[0]?.price ?? 0
  const bestAskPrice = activeAsks[0]?.price ?? 0

  const fillPriceInput = (newPrice: number) => {
    if (!Number.isFinite(newPrice) || newPrice <= 0) return
    setOrderType("LO")
    setPriceInput(String(Math.round(newPrice)))
    setShowDraftOrderOnChart(true)
    setPriceInputFlash(true)
    window.setTimeout(() => setPriceInputFlash(false), 360)
  }

  const positions: PortfolioPosition[] = useMemo(
    () => portfolioData?.positions ?? [],
    [portfolioData?.positions]
  )

  const orders: TradeOrderRecord[] = useMemo(() => {
    const raw = portfolioData?.orders ?? []
    return raw.map((o) => {
      const qty = Number(o.quantity ?? 0)
      const status = String(o.status ?? "FILLED").toUpperCase()
      const filledQty =
        o.filledQuantity != null
          ? Number(o.filledQuantity)
          : status === "FILLED"
            ? qty
            : 0
      const remQty =
        o.remainingQuantity != null
          ? Number(o.remainingQuantity)
          : status === "PENDING" || status === "PARTIALLY_FILLED"
            ? Math.max(0, qty - filledQty)
            : 0
      const resolvedPrice = toVndPrice(o.price)
      const limitPrice = o.limitPrice != null ? toVndPrice(o.limitPrice, resolvedPrice) : resolvedPrice
      const avgFillPrice =
        o.avgFillPrice != null && Number(o.avgFillPrice) > 0
          ? toVndPrice(o.avgFillPrice)
          : filledQty > 0
            ? resolvedPrice
            : null
      return {
        id: String(o.id ?? ""),
        symbol: String(o.symbol ?? "").toUpperCase(),
        stockName: o.stockName ? String(o.stockName) : undefined,
        sector: o.sector ? String(o.sector) : null,
        exchange: o.exchange ? String(o.exchange) : "HOSE",
        side: String(o.side ?? "BUY").toUpperCase() === "SELL" ? "SELL" : "BUY",
        orderType: String(o.orderType ?? "LO").toUpperCase(),
        price: resolvedPrice,
        limitPrice,
        avgFillPrice,
        quantity: qty,
        filledQuantity: filledQty,
        remainingQuantity: remQty,
        lockedCash: Number(o.lockedCash ?? 0),
        lockedShares: Number(o.lockedShares ?? 0),
        status,
        createdAt: o.createdAt ? String(o.createdAt) : undefined,
      }
    })
  }, [portfolioData?.orders])

  const buyingPower = Number(
    portfolioData?.summary?.buyingPower ?? portfolioData?.summary?.cash ?? 0
  )
  const lockedCashTotal = Number(portfolioData?.summary?.lockedCash ?? 0)

  const currentPosition = useMemo(
    () => positions.find((p) => p.symbol.toUpperCase() === symbol.toUpperCase()) ?? null,
    [positions, symbol]
  )

  const quickSymbols = useMemo(() => {
    const held = positions.map((p) => p.symbol.toUpperCase())
    const merged = Array.from(new Set([symbol, ...held, ...DEFAULT_SYMBOLS]))
    return merged.slice(0, 6)
  }, [positions, symbol])

  const searchResults = useMemo(() => {
    const q = searchQuery.trim().toUpperCase()
    if (!q) return marketUniverse.slice(0, 8)
    return marketUniverse
      .filter(
        (item) =>
          String(item.symbol ?? "").toUpperCase().includes(q) ||
          String(item.name ?? "").toUpperCase().includes(q)
      )
      .slice(0, 8)
  }, [marketUniverse, searchQuery])

  const quantity = Number(qtyInput || 0)
  const validLotQty = Number.isInteger(quantity) && quantity >= 100 && quantity % 100 === 0
  const parsedLimitPrice = Number(priceInput || 0)
  const mpCounterPrice =
    side === "BUY"
      ? bestAskPrice > 0
        ? bestAskPrice
        : stock.price
      : bestBidPrice > 0
        ? bestBidPrice
        : stock.price
  const auctionReferencePrice =
    side === "BUY"
      ? stock.ceiling > 0
        ? stock.ceiling
        : stock.price
      : stock.floor > 0
        ? stock.floor
        : stock.price
  const effectivePrice =
    orderType === "MP"
      ? mpCounterPrice
      : orderType === "ATO" || orderType === "ATC"
        ? auctionReferencePrice
        : parsedLimitPrice > 0
          ? parsedLimitPrice
          : stock.price
  const priceStep = getTickStep(effectivePrice || stock.price || 20_000)

  const financials = useMemo(
    () => computeOrderFinancials(side, effectivePrice, validLotQty ? quantity : 0),
    [side, effectivePrice, validLotQty, quantity]
  )

  const maxBuyableShares = useMemo(() => {
    const unitPrice = effectivePrice || stock.price
    if (unitPrice <= 0 || buyingPower <= 10_000) return 0
    const rawShares = Math.floor((buyingPower - 10_000) / (unitPrice * 1.001))
    return Math.max(0, Math.floor(rawShares / 100) * 100)
  }, [effectivePrice, stock.price, buyingPower])

  const heldShares = currentPosition?.quantity ?? 0
  const sellableShares = currentPosition?.sellableQuantity ?? heldShares
  const lockedSellShares = currentPosition?.lockedQuantity ?? 0
  const pendingT2Shares = currentPosition?.pendingT2Quantity ?? 0

  const priceBandWarning = useMemo(() => {
    if (orderType !== "LO" || effectivePrice <= 0) return null
    if (stock.ceiling > 0 && effectivePrice > stock.ceiling) {
      return `Giá đặt vượt trần phiên (${fmtPrice(stock.ceiling)} ₫)`
    }
    if (stock.floor > 0 && effectivePrice < stock.floor) {
      return `Giá đặt thấp hơn sàn phiên (${fmtPrice(stock.floor)} ₫)`
    }
    const reqStep = getTickStep(effectivePrice)
    if (effectivePrice % reqStep !== 0) {
      return `Giá đặt phải tuân theo bước giá HOSE (${fmtPrice(reqStep)} ₫)`
    }
    return null
  }, [orderType, effectivePrice, stock.ceiling, stock.floor])

  const validationError = useMemo(() => {
    if (priceBandWarning) return priceBandWarning
    if (!validLotQty) return "Khối lượng đặt phải là bội số lô 100 cổ phiếu"
    if (side === "BUY" && financials.totalCostOrProceeds > buyingPower) {
      return "Vượt quá sức mua khả dụng (đã trừ tiền phong tỏa chờ khớp)"
    }
    if (side === "SELL" && quantity > sellableShares) {
      return heldShares === 0
        ? `Tài khoản hiện không nắm giữ ${symbol} để bán`
        : lockedSellShares > 0
          ? `Vượt số dư khả dụng (${fmtNumber(sellableShares)} CP khả dụng · ${fmtNumber(lockedSellShares)} CP đang chờ bán)`
          : `Số lượng bán vượt số dư khả dụng (${fmtNumber(sellableShares)} CP)`
    }
    return null
  }, [
    priceBandWarning,
    validLotQty,
    side,
    financials.totalCostOrProceeds,
    buyingPower,
    quantity,
    sellableShares,
    heldShares,
    lockedSellShares,
    symbol,
  ])

  const canSubmit = !submitting && !validationError && effectivePrice > 0

  async function handleSubmitOrder() {
    if (!canSubmit) return
    setSubmitting(true)
    setOrderNotice(null)
    try {
      const created = (await portfolioApi.order({
        symbol,
        side,
        orderType,
        quantity,
        price: orderType === "LO" ? effectivePrice : undefined,
      })) as Record<string, unknown>
      syncOrderbookSnapshot(symbol)
      await reloadPortfolio()
      setShowDraftOrderOnChart(false)

      const createdStatus = String(created?.status ?? "FILLED").toUpperCase()
      const filledQty = Number(created?.filledQuantity ?? (createdStatus === "FILLED" ? quantity : 0))
      const remQty = Number(created?.remainingQuantity ?? Math.max(0, quantity - filledQty))
      const avgPx = toVndPrice(created?.avgFillPrice ?? created?.price, effectivePrice)
      const limPx = toVndPrice(created?.limitPrice ?? created?.price, effectivePrice)
      const sideLabel = side === "BUY" ? "MUA" : "BÁN"

      const noticeText =
        createdStatus === "FILLED"
          ? `Đã khớp 100% lệnh ${orderType} ${sideLabel} ${fmtNumber(quantity)} ${symbol} tại giá bình quân ${fmtPrice(avgPx)} ₫.`
          : createdStatus === "PARTIALLY_FILLED"
            ? `Khớp 1 phần (${fmtNumber(filledQty)}/${fmtNumber(quantity)} CP) tại ${fmtPrice(avgPx)} ₫ · Còn ${fmtNumber(remQty)} CP đang chờ khớp tại ${fmtPrice(limPx)} ₫.`
            : `Lệnh ${orderType} ${sideLabel} ${fmtNumber(quantity)} ${symbol} (${orderType === "ATO" || orderType === "ATC" ? orderType : `${fmtPrice(limPx)} ₫`}) đã vào Sổ lệnh LIVE chờ đối ứng.`

      setOrderNotice({
        tone: "gain",
        text: noticeText,
        orderId: created?.id ? String(created.id) : undefined,
      })
    } catch (e: unknown) {
      const rawMsg =
        (e as { response?: { data?: { error?: string } } })?.response?.data?.error ||
        (e instanceof Error ? e.message : "Không thể đặt lệnh.")
      const viMsg =
        rawMsg.includes("continuous trading session") || rawMsg.includes("Circuit breaker")
          ? "Ngoài giờ khớp lệnh liên tục HOSE (09:15–11:30 & 13:00–14:30)."
          : rawMsg.includes("stale") || rawMsg.includes("unavailable")
            ? "Sổ lệnh trực tiếp đang tạm nghỉ hoặc chưa có dữ liệu mới."
            : rawMsg
      setOrderNotice({
        tone: "loss",
        text: viMsg,
      })
    } finally {
      setSubmitting(false)
    }
  }

  async function handleCancelOrder(ord: TradeOrderRecord) {
    if (!ord.id || actionOrderId) return
    setActionOrderId(ord.id)
    try {
      await portfolioApi.cancelOrder(ord.id)
      syncOrderbookSnapshot(ord.symbol)
      await reloadPortfolio()
      if (selectedOrderReceipt?.id === ord.id) {
        setSelectedOrderReceipt(null)
      }
      setOrderNotice({
        tone: "gain",
        text: `Đã hủy phần chờ khớp (${fmtNumber(ord.remainingQuantity ?? ord.quantity)} CP) của lệnh ${ord.side === "BUY" ? "MUA" : "BÁN"} ${ord.symbol} — giải phóng ký quỹ tức thì.`,
      })
    } catch (e: unknown) {
      const rawMsg =
        (e as { response?: { data?: { error?: string } } })?.response?.data?.error ||
        (e instanceof Error ? e.message : "Không thể hủy lệnh.")
      setOrderNotice({ tone: "loss", text: rawMsg })
    } finally {
      setActionOrderId(null)
    }
  }

  function openAmendDialog(ord: TradeOrderRecord) {
    setAmendingOrder(ord)
    setAmendPriceInput(String(Math.round(ord.limitPrice || ord.price || stock.price)))
    setAmendQtyInput(String(ord.quantity))
  }

  async function handleConfirmAmend() {
    if (!amendingOrder || actionOrderId) return
    const nextPrice = Number(amendPriceInput || 0)
    const nextQty = Number(amendQtyInput || 0)
    if (!Number.isInteger(nextQty) || nextQty < 100 || nextQty % 100 !== 0) {
      setOrderNotice({ tone: "loss", text: "Khối lượng sửa lệnh phải là bội số lô 100 CP." })
      return
    }
    setActionOrderId(amendingOrder.id)
    try {
      await portfolioApi.amendOrder(amendingOrder.id, {
        price: amendingOrder.orderType === "LO" && nextPrice > 0 ? nextPrice : undefined,
        quantity: nextQty,
      })
      syncOrderbookSnapshot(amendingOrder.symbol)
      await reloadPortfolio()
      setOrderNotice({
        tone: "gain",
        text: `Đã cập nhật lệnh ${amendingOrder.symbol}: ${fmtNumber(nextQty)} CP${amendingOrder.orderType === "LO" ? ` · giá ${fmtPrice(nextPrice)} ₫` : ""}.`,
      })
      setAmendingOrder(null)
    } catch (e: unknown) {
      const rawMsg =
        (e as { response?: { data?: { error?: string } } })?.response?.data?.error ||
        (e instanceof Error ? e.message : "Không thể sửa lệnh.")
      setOrderNotice({ tone: "loss", text: rawMsg })
    } finally {
      setActionOrderId(null)
    }
  }

  const openOrdersTotalCount = useMemo(
    () => orders.filter((o) => o.status === "PENDING" || o.status === "PARTIALLY_FILLED").length,
    [orders]
  )

  const filteredOrders = useMemo(() => {
    return orders.filter((ord) => {
      if (
        ledgerFilter === "OPEN" &&
        ord.status !== "PENDING" &&
        ord.status !== "PARTIALLY_FILLED"
      ) {
        return false
      }
      if (ledgerFilter === "SYMBOL" && ord.symbol.toUpperCase() !== symbol.toUpperCase()) return false
      if (ledgerFilter === "BUY" && ord.side !== "BUY") return false
      if (ledgerFilter === "SELL" && ord.side !== "SELL") return false
      if (ledgerSearch.trim()) {
        const q = ledgerSearch.trim().toUpperCase()
        const matchSym = ord.symbol.toUpperCase().includes(q)
        const matchSector = String(ord.sector ?? "").toUpperCase().includes(q)
        if (!matchSym && !matchSector) return false
      }
      return true
    })
  }, [orders, ledgerFilter, symbol, ledgerSearch])

  const lastTickTime = formatTimeVi(lastTickAt)
  const mainPriceTone = hosePriceTone(stock.price, stock.ref, stock.ceiling, stock.floor)

  return (
    <div className="max-w-[1360px] mx-auto px-6 lg:px-8 py-6 space-y-4">
      {/* ====================================================================
       * 1. THANH TIÊU ĐỀ, TRẠNG THÁI LIVE STREAM & CHỌN NHANH MÃ CỔ PHIẾU
       * ==================================================================== */}
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="font-display text-[26px] font-semibold tracking-tight text-[var(--color-ink)] leading-none">
            Giao dịch & Khớp lệnh
          </h1>
          <Pill tone={isLive ? "gain" : socketConnected ? "teal" : "neutral"}>
            <span
              className={`h-1.5 w-1.5 rounded-full ${
                isLive
                  ? "bg-[var(--color-gain)] animate-pulse"
                  : socketConnected
                    ? "bg-[var(--color-mineral)] animate-pulse"
                    : "bg-[var(--color-muted)]"
              }`}
            />
            {isLive
              ? `Trực tiếp HOSE${lastTickTime ? ` · ${lastTickTime}` : ""}`
              : socketConnected
                ? "Tự động cập nhật · Ngoài phiên khớp lệnh"
                : "Đang kết nối luồng trực tiếp…"}
          </Pill>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {/* Quick Symbol Chips */}
          <div className="flex items-center gap-1.5">
            {quickSymbols.map((sym) => {
              const isCurrent = sym === symbol
              const isHeld = positions.some((p) => p.symbol.toUpperCase() === sym)
              return (
                <button
                  key={sym}
                  type="button"
                  onClick={() => selectSymbol(sym)}
                  className={`inline-flex h-8 items-center gap-1.5 rounded-[var(--radius-sm)] border px-2.5 font-mono text-[12px] font-medium transition-colors whitespace-nowrap ${
                    isCurrent
                      ? "border-[var(--color-ink)] bg-[var(--color-ink)] text-[var(--color-paper)]"
                      : "border-[var(--color-line)] bg-[var(--color-surface)] text-[var(--color-ink-2)] hover:border-[var(--color-ink)]"
                  }`}
                >
                  {isHeld && (
                    <span
                      className={`h-1.5 w-1.5 rounded-full ${
                        isCurrent ? "bg-[var(--color-paper)]" : "bg-[var(--color-gain)]"
                      }`}
                    />
                  )}
                  {sym}
                </button>
              )
            })}
          </div>

          {/* Search Input (Live Market Universe) */}
          <div ref={searchBoxRef} className="relative">
            <div className="flex h-8 items-center rounded-[var(--radius-sm)] border border-[var(--color-line-strong)] bg-[var(--color-surface)] px-2.5 focus-within:border-[var(--color-ink)]">
              <input
                type="text"
                value={searchQuery}
                onFocus={() => setSearchOpen(true)}
                onChange={(e) => {
                  setSearchQuery(e.target.value)
                  setSearchOpen(true)
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && searchResults.length > 0) {
                    selectSymbol(String(searchResults[0].symbol ?? "HPG"))
                  }
                }}
                placeholder="Tìm mã hoặc tên công ty..."
                className="w-[180px] bg-transparent text-[12px] text-[var(--color-ink)] placeholder:text-[var(--color-muted)] focus:outline-none"
              />
            </div>

            {searchOpen && (
              <div className="absolute right-0 z-40 mt-1.5 w-[310px] rounded-[var(--radius-md)] border border-[var(--color-line-strong)] bg-[var(--color-surface)] p-1.5 shadow-xl">
                <div className="max-h-[250px] overflow-y-auto divide-y divide-[var(--color-line)]/50">
                  {searchResults.map((item) => {
                    const sym = String(item.symbol ?? "").toUpperCase()
                    const pr = toVndPrice(item.price)
                    const chg = Number(item.change_pct ?? 0)
                    return (
                      <button
                        key={sym}
                        type="button"
                        onClick={() => selectSymbol(sym)}
                        className="flex w-full items-center justify-between gap-2 rounded-[var(--radius-sm)] px-2.5 py-2 text-left hover:bg-[var(--color-surface-2)] transition-colors"
                      >
                        <div className="min-w-0">
                          <div className="flex items-center gap-1.5">
                            <span className="font-mono text-[12.5px] font-bold text-[var(--color-ink)]">
                              {sym}
                            </span>
                            <span className="truncate text-[11.5px] text-[var(--color-muted)]">
                              {String(item.name ?? sym)}
                            </span>
                          </div>
                        </div>
                        <div className="shrink-0 text-right font-mono text-[11.5px] tabular-nums">
                          <div className="font-semibold text-[var(--color-ink)]">{fmtPrice(pr)}</div>
                          <div className={`text-[10.5px] ${toneText(chg)}`}>{fmtPercent(chg)}</div>
                        </div>
                      </button>
                    )
                  })}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ====================================================================
       * 2. THANH THÔNG TIN MÃ CỔ PHIẾU TRỰC TIẾP (CÂN ĐỐI TOÀN CHIỀU NGANG)
       * ==================================================================== */}
      <Panel className="!px-5 !py-3.5">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between lg:divide-x lg:divide-[var(--color-line)]">
          {/* Cột 1: Định danh & Giá khớp trực tiếp (có hiệu ứng nháy tick realtime) */}
          <div className="flex items-center justify-between sm:justify-start gap-6 lg:pr-6 shrink-0">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className="font-display text-[24px] font-bold tracking-tight text-[var(--color-ink)] leading-none">
                  {stock.symbol}
                </span>
                <span className="rounded bg-[var(--color-surface-2)] px-1.5 py-0.5 font-mono text-[10px] font-semibold text-[var(--color-muted)]">
                  HOSE
                </span>
                {currentPosition && (
                  <span className="rounded bg-[var(--color-gain-soft)] px-2 py-0.5 font-mono text-[10px] font-semibold text-[var(--color-gain)] whitespace-nowrap">
                    Đang giữ {fmtNumber(currentPosition.quantity)} CP
                  </span>
                )}
              </div>
              <p className="mt-1 max-w-[220px] truncate text-[12px] text-[var(--color-muted)]">
                {stock.name}
              </p>
            </div>

            <div
              className={`rounded-[var(--radius-sm)] px-2.5 py-1 font-mono tabular-nums text-right sm:text-left whitespace-nowrap transition-colors duration-300 ${
                flash === "gain"
                  ? "bg-[var(--color-gain-soft)]"
                  : flash === "loss"
                    ? "bg-[var(--color-loss-soft)]"
                    : ""
              }`}
            >
              <div className={`text-[23px] font-bold leading-none ${mainPriceTone}`}>
                {fmtPrice(stock.price)} <span className="text-[13px] font-normal">₫</span>
              </div>
              <div className={`mt-1 text-[12px] font-medium ${mainPriceTone}`}>
                {stock.priceDiff > 0 ? "▲ " : stock.priceDiff < 0 ? "▼ " : ""}
                {fmtVND(Math.abs(stock.priceDiff))} ({fmtPercent(stock.changePct)})
              </div>
            </div>
          </div>

          {/* Cột 2: Mốc Giá Trần / Tham Chiếu / Sàn chuẩn HOSE (Bấm để điền giá) */}
          <div className="grid grid-cols-3 gap-2 lg:px-6 shrink-0">
            <button
              type="button"
              onClick={() => fillPriceInput(stock.ceiling)}
              title="Chạm để điền giá Trần"
              className="rounded-[var(--radius-sm)] border border-[var(--color-line)] bg-[var(--color-surface-2)]/60 px-3 py-1.5 text-center hover:border-[var(--color-ceiling)] transition-colors whitespace-nowrap"
            >
              <span className="block text-[10px] uppercase tracking-wider text-[var(--color-muted)]">
                Trần
              </span>
              <span className="mt-0.5 block font-mono text-[13px] font-semibold text-[var(--color-ceiling)] tabular-nums">
                {fmtPrice(stock.ceiling)}
              </span>
            </button>
            <button
              type="button"
              onClick={() => fillPriceInput(stock.ref ?? stock.price)}
              title="Chạm để điền giá Tham chiếu"
              className="rounded-[var(--radius-sm)] border border-[var(--color-line)] bg-[var(--color-surface-2)]/60 px-3 py-1.5 text-center hover:border-[var(--color-ref)] transition-colors whitespace-nowrap"
            >
              <span className="block text-[10px] uppercase tracking-wider text-[var(--color-muted)]">
                Tham chiếu
              </span>
              <span className="mt-0.5 block font-mono text-[13px] font-semibold text-[var(--color-ref)] tabular-nums">
                {fmtPrice(stock.ref ?? stock.price)}
              </span>
            </button>
            <button
              type="button"
              onClick={() => fillPriceInput(stock.floor)}
              title="Chạm để điền giá Sàn"
              className="rounded-[var(--radius-sm)] border border-[var(--color-line)] bg-[var(--color-surface-2)]/60 px-3 py-1.5 text-center hover:border-[var(--color-floor)] transition-colors whitespace-nowrap"
            >
              <span className="block text-[10px] uppercase tracking-wider text-[var(--color-muted)]">
                Sàn
              </span>
              <span className="mt-0.5 block font-mono text-[13px] font-semibold text-[var(--color-floor)] tabular-nums">
                {fmtPrice(stock.floor)}
              </span>
            </button>
          </div>

          {/* Cột 3: Thống kê phiên trực tiếp */}
          <div className="flex flex-wrap items-center justify-between gap-6 lg:pl-6 flex-1">
            <div className="whitespace-nowrap">
              <div className="text-[11px] text-[var(--color-muted)]">KL khớp phiên</div>
              <div className="mt-0.5 font-mono text-[13.5px] font-semibold text-[var(--color-ink)] tabular-nums">
                {stock.volume}
              </div>
            </div>
            <div className="whitespace-nowrap">
              <div className="text-[11px] text-[var(--color-muted)]">Thấp – Cao phiên</div>
              <div className="mt-0.5 font-mono text-[13.5px] font-semibold text-[var(--color-ink)] tabular-nums">
                {latestCandle ? `${fmtPrice(latestCandle.low)} – ${fmtPrice(latestCandle.high)}` : "—"}
              </div>
            </div>
            <div className="whitespace-nowrap">
              <div className="text-[11px] text-[var(--color-muted)]">Khối ngoại</div>
              <div
                className={`mt-0.5 font-mono text-[13.5px] font-semibold tabular-nums ${
                  stock.foreignFlow != null ? toneText(stock.foreignFlow) : "text-[var(--color-ink)]"
                }`}
              >
                {stock.foreignFlow != null
                  ? `${stock.foreignFlow >= 0 ? "+" : ""}${stock.foreignFlow.toFixed(1).replace(".", ",")} tỷ`
                  : "—"}
              </div>
            </div>
          </div>
        </div>
      </Panel>

      {/* ====================================================================
       * 3. KHU VỰC GIAO DỊCH CHÍNH 2 CỘT RỘNG RÃI (8 : 4)
       * ==================================================================== */}
      <div className="grid gap-4 xl:grid-cols-12 items-stretch">
        {/* CỘT TRÁI (8/12): BIỂU ĐỒ TRADINGVIEW PRO + BẢNG CHÀO MUA/BÁN CHUẨN HOSE */}
        <Panel className="xl:col-span-8 !p-3 flex flex-col justify-between gap-3">
          <TradingViewChart
            symbol={symbol}
            seedCandles={candles}
            height={580}
            trading={{
              symbol,
              stockName: stock.name,
              exchange: "HOSE",
              currentPrice: stock.price,
              refPrice: stock.ref ?? stock.price,
              ceilingPrice: stock.ceiling,
              floorPrice: stock.floor,
              isLive,
              orderSide: side,
              orderType,
              orderPrice: effectivePrice,
              orderQuantity: validLotQty ? quantity : 100,
              hasDraftOrder: showDraftOrderOnChart && orderType === "LO" && parsedLimitPrice > 0,
              bestBidPrice,
              bestAskPrice,
              positionAvgPrice: currentPosition ? toVndPrice(currentPosition.avgPrice) : null,
              positionQuantity: currentPosition?.quantity ?? null,
              openOrders: orders
                .filter(
                  (o) =>
                    o.symbol.toUpperCase() === symbol.toUpperCase() &&
                    (o.status === "PENDING" || o.status === "PARTIALLY_FILLED" || o.status === "OPEN")
                )
                .map((o) => ({
                  id: o.id,
                  side: o.side,
                  orderType: o.orderType,
                  price: o.limitPrice && o.limitPrice > 0 ? o.limitPrice : o.price,
                  quantity: o.remainingQuantity && o.remainingQuantity > 0 ? o.remainingQuantity : o.quantity,
                  status: o.status,
                })),
              onPickPriceFromChart: (picked) => fillPriceInput(picked),
              onClearOrderPrice: () => setShowDraftOrderOnChart(false),
              onChangeOrderSide: (newSide) => setSide(newSide),
              onSymbolChange: (newSym) => selectSymbol(newSym),
            }}
          />

          <BidAskOrderbook
            bids={activeBids}
            asks={activeAsks}
            refPrice={stock.ref ?? stock.price}
            ceilingPrice={stock.ceiling}
            floorPrice={stock.floor}
            selectedOrderPrice={effectivePrice}
            isLiveStream={isLive}
            orderbookStale={resolvedOrderbook.stale}
            receivedAt={resolvedOrderbook.receivedAt}
            lastUpdate={resolvedOrderbook.lastUpdate}
            socketConnected={socketConnected}
            onSelectPrice={(p) => fillPriceInput(p)}
          />
        </Panel>

        {/* CỘT PHẢI (4/12): PHIẾU ĐẶT LỆNH */}
        <Panel className="xl:col-span-4 !p-4 flex flex-col justify-between">
          <div className="space-y-3">
            {/* Header Phiếu lệnh */}
            <div className="flex items-center justify-between pb-2.5 border-b border-[var(--color-line)]">
              <h2 className="text-[15px] font-semibold text-[var(--color-ink)]">Phiếu đặt lệnh</h2>
              <span className="font-mono text-[12.5px] font-semibold text-[var(--color-muted)]">
                Mã: <strong className="text-[var(--color-ink)]">{symbol}</strong>
              </span>
            </div>

            {/* 1. Chọn Mua / Bán */}
            <div className="grid grid-cols-2 gap-1 rounded-[var(--radius-sm)] border border-[var(--color-line)] bg-[var(--color-surface-2)] p-1">
              <button
                type="button"
                onClick={() => {
                  setSide("BUY")
                  setOrderNotice(null)
                }}
                className={`rounded-[5px] py-1.5 text-[13px] font-semibold transition-colors whitespace-nowrap ${
                  side === "BUY"
                    ? "bg-[var(--color-gain)] text-[var(--color-paper)]"
                    : "text-[var(--color-muted)] hover:text-[var(--color-ink)]"
                }`}
              >
                Mua cổ phiếu
              </button>
              <button
                type="button"
                onClick={() => {
                  setSide("SELL")
                  setOrderNotice(null)
                }}
                className={`rounded-[5px] py-1.5 text-[13px] font-semibold transition-colors whitespace-nowrap ${
                  side === "SELL"
                    ? "bg-[var(--color-loss)] text-[var(--color-paper)]"
                    : "text-[var(--color-muted)] hover:text-[var(--color-ink)]"
                }`}
              >
                Bán cổ phiếu
              </button>
            </div>

            {/* 2. Loại lệnh chuẩn HOSE: LO / MP / ATO / ATC */}
            <div className="grid grid-cols-4 gap-1.5">
              {(
                [
                  { id: "LO", sub: "Giới hạn" },
                  { id: "MP", sub: "Thị trường" },
                  { id: "ATO", sub: "Mở cửa" },
                  { id: "ATC", sub: "Đóng cửa" },
                ] as const
              ).map((item) => (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => setOrderType(item.id)}
                  className={`rounded-[var(--radius-sm)] border py-1.5 px-1 text-center transition-colors whitespace-nowrap ${
                    orderType === item.id
                      ? "border-[var(--color-ink)] bg-[var(--color-ink)] text-[var(--color-paper)]"
                      : "border-[var(--color-line)] bg-[var(--color-surface-2)] text-[var(--color-muted)] hover:text-[var(--color-ink)]"
                  }`}
                >
                  <span className="block font-mono text-[12px] font-bold leading-tight">{item.id}</span>
                  <span className="block text-[9.5px] opacity-80 leading-tight">{item.sub}</span>
                </button>
              ))}
            </div>

            {/* 3. Thông tin Sức mua / Cổ phiếu đang giữ & Ký quỹ */}
            <div className="rounded-[var(--radius-sm)] border border-[var(--color-line)] bg-[var(--color-surface-2)]/65 px-3 py-2 text-[12px] space-y-1">
              {side === "BUY" ? (
                <>
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-[var(--color-muted)]">Sức mua khả dụng</span>
                    <strong className="font-mono text-[var(--color-ink)] tabular-nums whitespace-nowrap">
                      {fmtVND(buyingPower)} ₫
                    </strong>
                  </div>
                  {lockedCashTotal > 0 && (
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-[var(--color-muted)]">Tiền phong tỏa chờ khớp</span>
                      <span className="font-mono text-[11.5px] text-[var(--color-ref)] tabular-nums whitespace-nowrap">
                        {fmtVND(lockedCashTotal)} ₫
                      </span>
                    </div>
                  )}
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-[var(--color-muted)]">KL mua tối đa</span>
                    <strong className="font-mono text-[var(--color-gain)] tabular-nums whitespace-nowrap">
                      {fmtNumber(maxBuyableShares)} CP
                    </strong>
                  </div>
                </>
              ) : (
                <>
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-[var(--color-muted)]">Số dư khả dụng ({symbol})</span>
                    <strong className="font-mono text-[var(--color-ink)] tabular-nums whitespace-nowrap">
                      {fmtNumber(sellableShares)} CP
                    </strong>
                  </div>
                  {lockedSellShares > 0 && (
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-[var(--color-muted)]">KL phong tỏa chờ bán</span>
                      <span className="font-mono text-[11.5px] text-[var(--color-ref)] tabular-nums whitespace-nowrap">
                        {fmtNumber(lockedSellShares)} CP
                      </span>
                    </div>
                  )}
                  {pendingT2Shares > 0 && (
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-[var(--color-muted)]">Chờ về VSDC (T+2.5)</span>
                      <span className="font-mono text-[11.5px] text-[var(--color-muted)] tabular-nums whitespace-nowrap">
                        {fmtNumber(pendingT2Shares)} CP
                      </span>
                    </div>
                  )}
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-[var(--color-muted)]">Giá vốn trung bình</span>
                    <strong className="font-mono text-[var(--color-ink-2)] tabular-nums whitespace-nowrap">
                      {currentPosition ? `${fmtPrice(currentPosition.avgPrice)} ₫` : "—"}
                    </strong>
                  </div>
                </>
              )}
            </div>

            {/* 4. Khối lượng đặt (Lô 100 CP) */}
            <div>
              <div className="flex items-center justify-between text-[12px] mb-1">
                <span className="text-[var(--color-muted)]">Khối lượng (Lô 100 CP)</span>
                <span className="font-mono text-[11.5px] text-[var(--color-ink-2)] tabular-nums">
                  {Math.floor(quantity / 100)} lô
                </span>
              </div>
              <div className="flex items-center gap-1.5">
                <button
                  type="button"
                  onClick={() => setQtyInput(String(Math.max(100, (Math.floor(quantity / 100) - 1) * 100)))}
                  className="h-9 w-9 shrink-0 rounded-[var(--radius-sm)] border border-[var(--color-line-strong)] bg-[var(--color-surface-2)] font-mono text-[15px] font-bold text-[var(--color-ink)] hover:border-[var(--color-ink)] transition-colors"
                >
                  −
                </button>
                <input
                  type="number"
                  step={100}
                  min={100}
                  value={qtyInput}
                  onChange={(e) => setQtyInput(e.target.value)}
                  className="h-9 w-full rounded-[var(--radius-sm)] border border-[var(--color-line-strong)] bg-[var(--color-surface)] px-3 text-center font-mono text-[14px] font-semibold text-[var(--color-ink)] tabular-nums focus:border-[var(--color-ink)] focus:outline-none"
                />
                <button
                  type="button"
                  onClick={() => setQtyInput(String(Math.max(100, (Math.floor(quantity / 100) + 1) * 100)))}
                  className="h-9 w-9 shrink-0 rounded-[var(--radius-sm)] border border-[var(--color-line-strong)] bg-[var(--color-surface-2)] font-mono text-[15px] font-bold text-[var(--color-ink)] hover:border-[var(--color-ink)] transition-colors"
                >
                  +
                </button>
              </div>

              <div className="mt-1.5 grid grid-cols-4 gap-1.5">
                {([0.25, 0.5, 0.75, 1] as const).map((ratio) => {
                  const maxBase = side === "BUY" ? maxBuyableShares : sellableShares
                  const targetQty =
                    maxBase >= 100 ? Math.max(100, Math.floor((maxBase * ratio) / 100) * 100) : 100
                  return (
                    <button
                      key={ratio}
                      type="button"
                      onClick={() => setQtyInput(String(targetQty))}
                      className="rounded-[var(--radius-sm)] border border-[var(--color-line)] bg-[var(--color-surface-2)] py-1 font-mono text-[11.5px] font-medium text-[var(--color-ink-2)] hover:border-[var(--color-ink)] transition-colors whitespace-nowrap"
                    >
                      {ratio === 1 ? "Tối đa" : `${ratio * 100}%`}
                    </button>
                  )
                })}
              </div>
            </div>

            {/* 5. Mức giá đặt (VND) */}
            {orderType === "LO" ? (
              <div>
                <div className="flex items-center justify-between text-[12px] mb-1">
                  <span className="text-[var(--color-muted)]">Giá đặt (VND)</span>
                  <span className="font-mono text-[11.5px] text-[var(--color-muted)]">
                    Bước giá: {fmtPrice(priceStep)} ₫
                  </span>
                </div>
                <div className="flex items-center gap-1.5">
                  <button
                    type="button"
                    onClick={() =>
                      fillPriceInput(Math.max(stock.floor || priceStep, effectivePrice - priceStep))
                    }
                    className="h-9 w-9 shrink-0 rounded-[var(--radius-sm)] border border-[var(--color-line-strong)] bg-[var(--color-surface-2)] font-mono text-[15px] font-bold text-[var(--color-ink)] hover:border-[var(--color-ink)] transition-colors"
                  >
                    −
                  </button>
                  <input
                    type="number"
                    step={priceStep}
                    value={priceInput}
                    onChange={(e) => {
                      setPriceInput(e.target.value)
                      setShowDraftOrderOnChart(e.target.value.trim() !== "")
                    }}
                    className={`h-9 w-full rounded-[var(--radius-sm)] border bg-[var(--color-surface)] px-3 text-center font-mono text-[14px] font-semibold text-[var(--color-ink)] tabular-nums focus:outline-none transition-shadow ${
                      priceInputFlash
                        ? "border-[var(--color-gain)] shadow-[0_0_0_3px_rgba(27,107,74,0.18)]"
                        : "border-[var(--color-line-strong)] focus:border-[var(--color-ink)]"
                    }`}
                  />
                  <button
                    type="button"
                    onClick={() =>
                      fillPriceInput(
                        stock.ceiling > 0
                          ? Math.min(stock.ceiling, effectivePrice + priceStep)
                          : effectivePrice + priceStep
                      )
                    }
                    className="h-9 w-9 shrink-0 rounded-[var(--radius-sm)] border border-[var(--color-line-strong)] bg-[var(--color-surface-2)] font-mono text-[15px] font-bold text-[var(--color-ink)] hover:border-[var(--color-ink)] transition-colors"
                  >
                    +
                  </button>
                </div>

                {/* Nút chọn nhanh giá: Ưu tiên Mua 1 / TC / Bán 1 khi có sổ lệnh thật, hoặc Sàn / Thị giá / Trần ngoài giờ */}
                {bestBidPrice > 0 || bestAskPrice > 0 ? (
                  <div className="mt-1.5 grid grid-cols-3 gap-1.5">
                    <button
                      type="button"
                      disabled={bestBidPrice <= 0}
                      onClick={() => fillPriceInput(bestBidPrice)}
                      className="rounded-[var(--radius-sm)] border border-[var(--color-line)] bg-[var(--color-surface-2)] px-2 py-1 text-center font-mono tabular-nums hover:border-[var(--color-gain)] disabled:opacity-45 transition-colors whitespace-nowrap"
                    >
                      <span className="block text-[10px] font-sans text-[var(--color-muted)]">Mua 1</span>
                      <span
                        className={`block text-[12px] font-semibold ${hosePriceTone(
                          bestBidPrice,
                          stock.ref,
                          stock.ceiling,
                          stock.floor
                        )}`}
                      >
                        {fmtPrice(bestBidPrice)}
                      </span>
                    </button>
                    <button
                      type="button"
                      onClick={() => fillPriceInput(stock.ref ?? stock.price)}
                      className="rounded-[var(--radius-sm)] border border-[var(--color-line)] bg-[var(--color-surface-2)] px-2 py-1 text-center font-mono tabular-nums hover:border-[var(--color-ref)] transition-colors whitespace-nowrap"
                    >
                      <span className="block text-[10px] font-sans text-[var(--color-muted)]">Tham chiếu</span>
                      <span className="block text-[12px] font-semibold text-[var(--color-ref)]">
                        {fmtPrice(stock.ref ?? stock.price)}
                      </span>
                    </button>
                    <button
                      type="button"
                      disabled={bestAskPrice <= 0}
                      onClick={() => fillPriceInput(bestAskPrice)}
                      className="rounded-[var(--radius-sm)] border border-[var(--color-line)] bg-[var(--color-surface-2)] px-2 py-1 text-center font-mono tabular-nums hover:border-[var(--color-loss)] disabled:opacity-45 transition-colors whitespace-nowrap"
                    >
                      <span className="block text-[10px] font-sans text-[var(--color-muted)]">Bán 1</span>
                      <span
                        className={`block text-[12px] font-semibold ${hosePriceTone(
                          bestAskPrice,
                          stock.ref,
                          stock.ceiling,
                          stock.floor
                        )}`}
                      >
                        {fmtPrice(bestAskPrice)}
                      </span>
                    </button>
                  </div>
                ) : (
                  <div className="mt-1.5 grid grid-cols-3 gap-1.5">
                    <button
                      type="button"
                      onClick={() => fillPriceInput(stock.floor)}
                      className="rounded-[var(--radius-sm)] border border-[var(--color-line)] bg-[var(--color-surface-2)] px-2 py-1 text-center font-mono tabular-nums hover:border-[var(--color-floor)] transition-colors whitespace-nowrap"
                    >
                      <span className="block text-[10px] font-sans text-[var(--color-muted)]">Giá sàn</span>
                      <span className="block text-[12px] font-semibold text-[var(--color-floor)]">
                        {fmtPrice(stock.floor)}
                      </span>
                    </button>
                    <button
                      type="button"
                      onClick={() => fillPriceInput(stock.price)}
                      className="rounded-[var(--radius-sm)] border border-[var(--color-line)] bg-[var(--color-surface-2)] px-2 py-1 text-center font-mono tabular-nums hover:border-[var(--color-ink)] transition-colors whitespace-nowrap"
                    >
                      <span className="block text-[10px] font-sans text-[var(--color-muted)]">Thị giá</span>
                      <span className={`block text-[12px] font-semibold ${mainPriceTone}`}>
                        {fmtPrice(stock.price)}
                      </span>
                    </button>
                    <button
                      type="button"
                      onClick={() => fillPriceInput(stock.ceiling)}
                      className="rounded-[var(--radius-sm)] border border-[var(--color-line)] bg-[var(--color-surface-2)] px-2 py-1 text-center font-mono tabular-nums hover:border-[var(--color-ceiling)] transition-colors whitespace-nowrap"
                    >
                      <span className="block text-[10px] font-sans text-[var(--color-muted)]">Giá trần</span>
                      <span className="block text-[12px] font-semibold text-[var(--color-ceiling)]">
                        {fmtPrice(stock.ceiling)}
                      </span>
                    </button>
                  </div>
                )}
              </div>
            ) : orderType === "MP" ? (
              <div className="rounded-[var(--radius-sm)] border border-[var(--color-line)] bg-[var(--color-surface-2)] px-3.5 py-2.5 text-[12px] text-[var(--color-ink-2)] leading-relaxed">
                Lệnh <strong>MP</strong> quét khớp ngay qua các bước giá {side === "BUY" ? "Bán" : "Mua"} tốt nhất trên Sổ lệnh LIVE ({side === "BUY" ? "Bán 1" : "Mua 1"} tạm tính:{" "}
                <strong className="font-mono">{fmtPrice(mpCounterPrice)} ₫</strong>); phần dư tự chuyển thành <strong>LO</strong> theo quy tắc MTL HOSE.
              </div>
            ) : (
              <div className="rounded-[var(--radius-sm)] border border-[var(--color-line)] bg-[var(--color-surface-2)] px-3.5 py-2.5 text-[12px] text-[var(--color-ink-2)] leading-relaxed">
                Lệnh <strong>{orderType}</strong> ưu tiên khớp tại phiên định kỳ{" "}
                {orderType === "ATO" ? "mở cửa (09:00–09:15)" : "đóng cửa (14:30–14:45)"} HOSE · Ký quỹ tạm tính theo giá{" "}
                {side === "BUY" ? "Trần" : "Sàn"} (<strong className="font-mono">{fmtPrice(auctionReferencePrice)} ₫</strong>).
              </div>
            )}
          </div>

          {/* 6. Tóm tắt giá trị lệnh & Nút xác nhận */}
          <div className="mt-3 pt-3 border-t border-[var(--color-line)] space-y-2.5">
            <div className="space-y-1 text-[12px]">
              <div className="flex items-center justify-between gap-2">
                <span className="text-[var(--color-muted)]">Giá trị lệnh</span>
                <span className="font-mono text-[var(--color-ink-2)] tabular-nums whitespace-nowrap">
                  {fmtVND(financials.gross)} ₫
                </span>
              </div>
              <div className="flex items-center justify-between gap-2">
                <span className="text-[var(--color-muted)]">
                  Phí & thuế tạm tính ({side === "SELL" ? "0,20%" : "0,10%"})
                </span>
                <span className="font-mono text-[var(--color-ink-2)] tabular-nums whitespace-nowrap">
                  {fmtVND(financials.fee + financials.tax)} ₫
                </span>
              </div>
              <div className="flex items-center justify-between gap-2 pt-1.5 border-t border-[var(--color-line)]/70">
                <span className="font-medium text-[var(--color-ink)]">
                  {side === "BUY" ? "Tổng thanh toán" : "Tiền ròng nhận về"}
                </span>
                <span
                  className={`font-mono text-[14.5px] font-bold tabular-nums whitespace-nowrap ${
                    side === "BUY" ? "text-[var(--color-ink)]" : "text-[var(--color-gain)]"
                  }`}
                >
                  {fmtVND(financials.totalCostOrProceeds)} ₫
                </span>
              </div>
            </div>

            {validationError && (
              <div className="rounded-[var(--radius-sm)] border border-[var(--color-loss)]/40 bg-[var(--color-loss-soft)] px-3 py-1.5 text-[11.5px] font-medium text-[var(--color-loss)]">
                {validationError}
              </div>
            )}

            {orderNotice && (
              <div
                className={`rounded-[var(--radius-sm)] border px-3 py-1.5 text-[11.5px] ${
                  orderNotice.tone === "gain"
                    ? "border-[var(--color-gain)]/40 bg-[var(--color-gain-soft)] text-[var(--color-gain)]"
                    : "border-[var(--color-loss)]/40 bg-[var(--color-loss-soft)] text-[var(--color-loss)]"
                }`}
              >
                <div>{orderNotice.text}</div>
                {orderNotice.orderId && (
                  <button
                    type="button"
                    onClick={() => {
                      const found = orders.find((o) => o.id === orderNotice.orderId)
                      if (found) setSelectedOrderReceipt(found)
                    }}
                    className="mt-1 font-mono text-[11px] underline"
                  >
                    Xem chi tiết lệnh →
                  </button>
                )}
              </div>
            )}

            <button
              type="button"
              onClick={handleSubmitOrder}
              disabled={!canSubmit}
              className={`h-10 w-full rounded-[var(--radius-sm)] text-[13px] font-semibold text-[var(--color-paper)] transition-opacity whitespace-nowrap ${
                side === "BUY" ? "bg-[var(--color-gain)]" : "bg-[var(--color-loss)]"
              } disabled:opacity-40 disabled:cursor-not-allowed`}
            >
              {submitting
                ? "Đang gửi lệnh..."
                : `Đặt lệnh ${orderType} ${side === "BUY" ? "Mua" : "Bán"} ${fmtNumber(validLotQty ? quantity : 0)} ${symbol}`}
            </button>
          </div>
        </Panel>
      </div>

      {/* ====================================================================
       * 4. SỔ LỆNH LIVE & LỊCH SỬ GIAO DỊCH
       * ==================================================================== */}
      <Panel>
        <PanelHead
          title="Sổ lệnh LIVE & Lịch sử giao dịch"
          sub={`Tổng ${orders.length} lệnh (${openOrdersTotalCount} lệnh đang chờ khớp trên sổ) · Hỗ trợ Sửa / Hủy O(1) theo thời gian thực.`}
          action={
            <div className="flex flex-wrap items-center gap-2">
              <div className="inline-flex rounded-[var(--radius-sm)] border border-[var(--color-line)] bg-[var(--color-surface-2)] p-0.5">
                {(
                  [
                    { id: "ALL", label: `Tất cả (${orders.length})` },
                    { id: "OPEN", label: `Chờ khớp (${openOrdersTotalCount})` },
                    {
                      id: "SYMBOL",
                      label: `${symbol} (${orders.filter((o) => o.symbol.toUpperCase() === symbol).length})`,
                    },
                    { id: "BUY", label: "Mua" },
                    { id: "SELL", label: "Bán" },
                  ] as const
                ).map((tab) => (
                  <button
                    key={tab.id}
                    type="button"
                    onClick={() => setLedgerFilter(tab.id)}
                    className={`px-2.5 py-1 text-[11.5px] font-medium rounded-[4px] transition-colors whitespace-nowrap ${
                      ledgerFilter === tab.id
                        ? "bg-[var(--color-ink)] text-[var(--color-paper)]"
                        : "text-[var(--color-muted)] hover:text-[var(--color-ink)]"
                    }`}
                  >
                    {tab.label}
                  </button>
                ))}
              </div>

              <input
                type="text"
                value={ledgerSearch}
                onChange={(e) => setLedgerSearch(e.target.value)}
                placeholder="Lọc mã CP..."
                className="h-8 w-[150px] rounded-[var(--radius-sm)] border border-[var(--color-line)] bg-[var(--color-surface)] px-2.5 text-[12px] text-[var(--color-ink)] placeholder:text-[var(--color-muted)] focus:border-[var(--color-ink)] focus:outline-none"
              />
            </div>
          }
        />

        <div className="overflow-x-auto border-t border-[var(--color-line)] pt-1">
          <table className="w-full text-left text-[12.5px]">
            <thead>
              <tr className="border-b border-[var(--color-line)] text-[10.5px] font-mono uppercase tracking-[0.08em] text-[var(--color-muted)] whitespace-nowrap">
                <th className="py-2.5 pr-3">Thời gian</th>
                <th className="py-2.5 px-3">Mã CP</th>
                <th className="py-2.5 px-3">Loại lệnh</th>
                <th className="py-2.5 px-3 text-right">KL khớp / Đặt</th>
                <th className="py-2.5 px-3 text-right">Giá đặt / Khớp</th>
                <th className="py-2.5 px-3 text-right">Phí & Thuế</th>
                <th className="py-2.5 px-3 text-right">Giá trị / Phong tỏa</th>
                <th className="py-2.5 pl-3 text-right">Trạng thái & Thao tác</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--color-line)]/70 font-mono tabular-nums">
              {filteredOrders.length === 0 ? (
                <tr>
                  <td colSpan={8} className="py-8 text-center font-sans text-[13px] text-[var(--color-muted)]">
                    Chưa có lệnh giao dịch nào khớp với bộ lọc.
                  </td>
                </tr>
              ) : (
                filteredOrders.slice(0, 25).map((ord) => {
                  const dt = formatDateTimeVi(ord.createdAt)
                  const isOpen = ord.status === "PENDING" || ord.status === "PARTIALLY_FILLED"
                  const isCancelled = ord.status === "CANCELLED"
                  const filledQty = ord.filledQuantity ?? (ord.status === "FILLED" ? ord.quantity : 0)
                  const basisQty = filledQty > 0 ? filledQty : ord.quantity
                  const basisPrice =
                    filledQty > 0 && ord.avgFillPrice
                      ? ord.avgFillPrice
                      : ord.limitPrice || ord.price
                  const fin = computeOrderFinancials(ord.side, Number(basisPrice), Number(basisQty))
                  const isBuy = ord.side === "BUY"
                  const busy = actionOrderId === ord.id

                  const statusMeta =
                    ord.status === "FILLED"
                      ? {
                          label: "Đã khớp",
                          cls: "bg-[var(--color-gain-soft)] text-[var(--color-gain)]",
                        }
                      : ord.status === "PARTIALLY_FILLED"
                        ? {
                            label: "Khớp 1 phần",
                            cls: "bg-amber-500/15 text-[var(--color-ref)]",
                          }
                        : ord.status === "PENDING"
                          ? {
                              label: "Chờ khớp",
                              cls: "bg-[var(--color-surface-2)] text-[var(--color-ink)] border border-[var(--color-line-strong)]",
                            }
                          : {
                              label: isCancelled && filledQty > 0 ? "Hủy phần dư" : "Đã hủy",
                              cls: "bg-[var(--color-surface-2)] text-[var(--color-muted)]",
                            }

                  return (
                    <tr
                      key={ord.id}
                      onClick={() => setSelectedOrderReceipt(ord)}
                      className="cursor-pointer hover:bg-[var(--color-surface-2)]/65 transition-colors"
                    >
                      <td className="py-2.5 pr-3 whitespace-nowrap">
                        <span className="text-[12px] text-[var(--color-ink)]">{dt.date}</span>
                        {dt.time && (
                          <span className="ml-1.5 text-[11px] text-[var(--color-muted)]">{dt.time}</span>
                        )}
                      </td>
                      <td className="py-2.5 px-3 whitespace-nowrap">
                        <span className="font-bold text-[var(--color-ink)]">{ord.symbol}</span>
                        {ord.sector && (
                          <span className="ml-2 font-sans text-[11.5px] text-[var(--color-muted)]">
                            {ord.sector}
                          </span>
                        )}
                      </td>
                      <td className="py-2.5 px-3 whitespace-nowrap">
                        <span
                          className={`inline-flex items-center rounded px-2 py-0.5 text-[10.5px] font-semibold ${
                            isBuy
                              ? "bg-[var(--color-gain-soft)] text-[var(--color-gain)]"
                              : "bg-[var(--color-loss-soft)] text-[var(--color-loss)]"
                          }`}
                        >
                          {isBuy ? "Mua" : "Bán"} · {ord.orderType || "LO"}
                        </span>
                      </td>
                      <td className="py-2.5 px-3 text-right font-semibold text-[var(--color-ink)] whitespace-nowrap">
                        {fmtNumber(filledQty)}/{fmtNumber(ord.quantity)} CP
                      </td>
                      <td className="py-2.5 px-3 text-right text-[var(--color-ink)] whitespace-nowrap">
                        {ord.avgFillPrice && filledQty > 0 ? (
                          <div>
                            <div className="font-semibold">{fmtPrice(ord.avgFillPrice)} ₫</div>
                            {isOpen && ord.limitPrice ? (
                              <div className="text-[10.5px] text-[var(--color-muted)]">
                                Đặt: {fmtPrice(ord.limitPrice)} ₫
                              </div>
                            ) : null}
                          </div>
                        ) : ord.orderType === "ATO" || ord.orderType === "ATC" ? (
                          <span>{ord.orderType}</span>
                        ) : (
                          <span>{fmtPrice(ord.limitPrice || ord.price)} ₫</span>
                        )}
                      </td>
                      <td className="py-2.5 px-3 text-right text-[var(--color-muted)] whitespace-nowrap">
                        {filledQty > 0 ? `${fmtVND(fin.fee + fin.tax)} ₫` : "—"}
                      </td>
                      <td
                        className={`py-2.5 px-3 text-right font-semibold whitespace-nowrap ${
                          isCancelled && filledQty === 0
                            ? "text-[var(--color-muted)]"
                            : isBuy
                              ? "text-[var(--color-ink)]"
                              : "text-[var(--color-gain)]"
                        }`}
                      >
                        {isCancelled && filledQty === 0 ? (
                          "—"
                        ) : isOpen && filledQty === 0 && isBuy && (ord.lockedCash ?? 0) > 0 ? (
                          <span className="text-[var(--color-ref)]">
                            🔒 {fmtVND(ord.lockedCash ?? 0)} ₫
                          </span>
                        ) : (
                          <>
                            {isBuy ? "−" : "+"}
                            {fmtVND(fin.totalCostOrProceeds)} ₫
                          </>
                        )}
                      </td>
                      <td
                        className="py-2.5 pl-3 text-right whitespace-nowrap"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <div className="inline-flex items-center gap-1.5">
                          <span
                            className={`rounded-full px-2 py-0.5 font-sans text-[11px] font-medium ${statusMeta.cls}`}
                          >
                            {statusMeta.label}
                          </span>
                          {isOpen ? (
                            <>
                              {ord.orderType === "LO" && (
                                <button
                                  type="button"
                                  disabled={busy}
                                  onClick={() => openAmendDialog(ord)}
                                  className="rounded border border-[var(--color-line-strong)] bg-[var(--color-surface)] px-2 py-0.5 font-sans text-[11px] font-medium text-[var(--color-ink)] hover:border-[var(--color-ink)] disabled:opacity-45 transition-colors"
                                >
                                  Sửa
                                </button>
                              )}
                              <button
                                type="button"
                                disabled={busy}
                                onClick={() => handleCancelOrder(ord)}
                                className="rounded border border-[var(--color-loss)]/45 bg-[var(--color-loss-soft)] px-2 py-0.5 font-sans text-[11px] font-semibold text-[var(--color-loss)] hover:border-[var(--color-loss)] disabled:opacity-45 transition-colors"
                              >
                                {busy ? "…" : "Hủy"}
                              </button>
                            </>
                          ) : (
                            <button
                              type="button"
                              onClick={() => {
                                selectSymbol(ord.symbol, ord.side, ord.quantity)
                                fillPriceInput(Number(ord.limitPrice || ord.price))
                                window.scrollTo({ top: 0, behavior: "smooth" })
                              }}
                              className="rounded border border-[var(--color-line)] bg-[var(--color-surface)] px-2 py-0.5 font-sans text-[11px] font-medium text-[var(--color-ink-2)] hover:border-[var(--color-ink)] transition-colors"
                            >
                              Đặt lại
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  )
                })
              )}
            </tbody>
          </table>
        </div>
      </Panel>

      {/* ====================================================================
       * MODAL: SỬA LỆNH LIVE (AMEND ORDER O(1))
       * ==================================================================== */}
      {amendingOrder && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
          onClick={() => setAmendingOrder(null)}
        >
          <div
            className="w-full max-w-[400px] rounded-[var(--radius-md)] border border-[var(--color-line-strong)] bg-[var(--color-paper)] p-5 shadow-2xl space-y-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-start justify-between border-b border-[var(--color-line)] pb-3">
              <div>
                <span className="font-mono text-[10.5px] uppercase tracking-[0.12em] text-[var(--color-muted)]">
                  Sửa lệnh trên Sổ lệnh LIVE
                </span>
                <h3 className="mt-0.5 font-display text-[19px] font-semibold text-[var(--color-ink)]">
                  {amendingOrder.side === "BUY" ? "Mua" : "Bán"} {amendingOrder.symbol} ·{" "}
                  {amendingOrder.orderType}
                </h3>
              </div>
              <button
                type="button"
                onClick={() => setAmendingOrder(null)}
                className="rounded border border-[var(--color-line)] px-2 py-0.5 text-[12px] text-[var(--color-muted)] hover:text-[var(--color-ink)]"
              >
                ✕
              </button>
            </div>

            <div className="space-y-3 text-[12.5px]">
              <div className="rounded-[var(--radius-sm)] border border-[var(--color-line)] bg-[var(--color-surface-2)] px-3 py-2 font-mono text-[11.5px] flex items-center justify-between">
                <span className="font-sans text-[var(--color-muted)]">Đã khớp / Tổng đặt</span>
                <strong className="text-[var(--color-ink)]">
                  {fmtNumber(amendingOrder.filledQuantity ?? 0)} / {fmtNumber(amendingOrder.quantity)} CP
                </strong>
              </div>

              {amendingOrder.orderType === "LO" && (
                <div>
                  <label className="block text-[12px] text-[var(--color-muted)] mb-1">
                    Giá giới hạn mới (VND)
                  </label>
                  <input
                    type="number"
                    step={getTickStep(Number(amendPriceInput) || amendingOrder.price || 20_000)}
                    value={amendPriceInput}
                    onChange={(e) => setAmendPriceInput(e.target.value)}
                    className="h-9 w-full rounded-[var(--radius-sm)] border border-[var(--color-line-strong)] bg-[var(--color-surface)] px-3 font-mono text-[14px] font-semibold text-[var(--color-ink)] focus:border-[var(--color-ink)] focus:outline-none"
                  />
                </div>
              )}

              <div>
                <label className="block text-[12px] text-[var(--color-muted)] mb-1">
                  Tổng khối lượng lệnh mới (Lô 100 CP)
                </label>
                <input
                  type="number"
                  step={100}
                  min={Math.max(100, Math.ceil(((amendingOrder.filledQuantity ?? 0) + 100) / 100) * 100)}
                  value={amendQtyInput}
                  onChange={(e) => setAmendQtyInput(e.target.value)}
                  className="h-9 w-full rounded-[var(--radius-sm)] border border-[var(--color-line-strong)] bg-[var(--color-surface)] px-3 font-mono text-[14px] font-semibold text-[var(--color-ink)] focus:border-[var(--color-ink)] focus:outline-none"
                />
                <p className="mt-1 text-[11px] text-[var(--color-muted)]">
                  Giảm khối lượng khi giữ nguyên giá sẽ bảo toàn thứ tự ưu tiên thời gian (FIFO HOSE).
                </p>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-2 pt-2 border-t border-[var(--color-line)]">
              <button
                type="button"
                onClick={() => setAmendingOrder(null)}
                className="h-9 rounded-[var(--radius-sm)] border border-[var(--color-line-strong)] bg-[var(--color-surface)] text-[12.5px] font-medium text-[var(--color-ink)]"
              >
                Đóng
              </button>
              <button
                type="button"
                disabled={actionOrderId === amendingOrder.id}
                onClick={handleConfirmAmend}
                className="h-9 rounded-[var(--radius-sm)] bg-[var(--color-ink)] text-[12.5px] font-semibold text-[var(--color-paper)] disabled:opacity-45"
              >
                {actionOrderId === amendingOrder.id ? "Đang lưu..." : "Xác nhận sửa lệnh"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ====================================================================
       * DRAWER: CHI TIẾT LỆNH GIAO DỊCH
       * ==================================================================== */}
      {selectedOrderReceipt && (
        <div
          className="fixed inset-0 z-50 flex justify-end bg-black/35 backdrop-blur-[2px]"
          onClick={() => setSelectedOrderReceipt(null)}
        >
          <div
            className="flex h-full w-full max-w-[420px] flex-col justify-between border-l border-[var(--color-line-strong)] bg-[var(--color-paper)] p-6 shadow-2xl overflow-y-auto"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="space-y-5">
              <div className="flex items-start justify-between gap-3 border-b border-[var(--color-line)] pb-4">
                <div>
                  <span className="font-mono text-[10.5px] uppercase tracking-[0.12em] text-[var(--color-muted)]">
                    Chi tiết lệnh giao dịch
                  </span>
                  <h3 className="mt-1 font-display text-[22px] font-semibold text-[var(--color-ink)]">
                    {selectedOrderReceipt.side === "BUY" ? "Mua" : "Bán"} {selectedOrderReceipt.symbol}
                  </h3>
                  <p className="text-[12px] text-[var(--color-muted)]">
                    {selectedOrderReceipt.sector || "HOSE"}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => setSelectedOrderReceipt(null)}
                  className="rounded-[var(--radius-sm)] border border-[var(--color-line)] px-2.5 py-1 text-[12px] text-[var(--color-muted)] hover:border-[var(--color-ink)] hover:text-[var(--color-ink)]"
                >
                  Đóng ✕
                </button>
              </div>

              {(() => {
                const filledQty =
                  selectedOrderReceipt.filledQuantity ??
                  (selectedOrderReceipt.status === "FILLED" ? selectedOrderReceipt.quantity : 0)
                const remQty =
                  selectedOrderReceipt.remainingQuantity ??
                  Math.max(0, selectedOrderReceipt.quantity - filledQty)
                const basisQty = filledQty > 0 ? filledQty : selectedOrderReceipt.quantity
                const basisPrice =
                  filledQty > 0 && selectedOrderReceipt.avgFillPrice
                    ? selectedOrderReceipt.avgFillPrice
                    : selectedOrderReceipt.limitPrice || selectedOrderReceipt.price
                const fin = computeOrderFinancials(
                  selectedOrderReceipt.side,
                  Number(basisPrice),
                  Number(basisQty)
                )
                const dt = formatDateTimeVi(selectedOrderReceipt.createdAt)
                const isBuy = selectedOrderReceipt.side === "BUY"
                const statusLabel =
                  selectedOrderReceipt.status === "FILLED"
                    ? "Đã khớp"
                    : selectedOrderReceipt.status === "PARTIALLY_FILLED"
                      ? "Khớp 1 phần"
                      : selectedOrderReceipt.status === "PENDING"
                        ? "Đang chờ khớp"
                        : "Đã hủy"

                return (
                  <div className="space-y-4 font-mono text-[12px] tabular-nums">
                    <div className="rounded-[var(--radius-md)] border border-[var(--color-line)] bg-[var(--color-surface)] p-4 space-y-2.5">
                      <div className="flex items-center justify-between">
                        <span className="font-sans text-[var(--color-muted)]">Mã lệnh</span>
                        <span className="text-[var(--color-ink)] font-semibold">
                          #{String(selectedOrderReceipt.id).slice(0, 12)}
                        </span>
                      </div>
                      <div className="flex items-center justify-between">
                        <span className="font-sans text-[var(--color-muted)]">Thời gian đặt</span>
                        <span className="text-[var(--color-ink)]">
                          {dt.date} {dt.time}
                        </span>
                      </div>
                      <div className="flex items-center justify-between">
                        <span className="font-sans text-[var(--color-muted)]">Trạng thái</span>
                        <span className="rounded bg-[var(--color-surface-2)] px-2 py-0.5 text-[10.5px] font-semibold text-[var(--color-ink)]">
                          {statusLabel} · {selectedOrderReceipt.orderType || "LO"}
                        </span>
                      </div>
                    </div>

                    <div className="rounded-[var(--radius-md)] border border-[var(--color-line)] bg-[var(--color-surface)] p-4 space-y-2.5">
                      <div className="flex items-center justify-between">
                        <span className="font-sans text-[var(--color-muted)]">KL đã khớp / Tổng đặt</span>
                        <strong className="text-[var(--color-ink)]">
                          {fmtNumber(filledQty)} / {fmtNumber(selectedOrderReceipt.quantity)} CP
                        </strong>
                      </div>
                      {remQty > 0 && (
                        <div className="flex items-center justify-between">
                          <span className="font-sans text-[var(--color-muted)]">KL đang chờ khớp</span>
                          <span className="text-[var(--color-ref)] font-semibold">
                            {fmtNumber(remQty)} CP
                          </span>
                        </div>
                      )}
                      <div className="flex items-center justify-between">
                        <span className="font-sans text-[var(--color-muted)]">
                          {filledQty > 0 ? "Giá khớp bình quân" : "Giá đặt"}
                        </span>
                        <strong className="text-[var(--color-ink)]">
                          {fmtPrice(basisPrice)} ₫
                        </strong>
                      </div>
                      {(selectedOrderReceipt.lockedCash ?? 0) > 0 && (
                        <div className="flex items-center justify-between">
                          <span className="font-sans text-[var(--color-muted)]">Tiền đang phong tỏa</span>
                          <span className="text-[var(--color-ref)]">
                            {fmtVND(selectedOrderReceipt.lockedCash ?? 0)} ₫
                          </span>
                        </div>
                      )}
                      <div className="flex items-center justify-between">
                        <span className="font-sans text-[var(--color-muted)]">
                          Giá trị {filledQty > 0 ? "đã khớp" : "dự kiến"}
                        </span>
                        <span className="text-[var(--color-ink-2)]">{fmtVND(fin.gross)} ₫</span>
                      </div>
                      <div className="flex items-center justify-between">
                        <span className="font-sans text-[var(--color-muted)]">Phí giao dịch (0,10%)</span>
                        <span className="text-[var(--color-loss)]">−{fmtVND(fin.fee)} ₫</span>
                      </div>
                      {!isBuy && (
                        <div className="flex items-center justify-between">
                          <span className="font-sans text-[var(--color-muted)]">Thuế bán (0,10%)</span>
                          <span className="text-[var(--color-loss)]">−{fmtVND(fin.tax)} ₫</span>
                        </div>
                      )}
                      <div className="flex items-center justify-between border-t border-[var(--color-line)] pt-2.5 text-[13.5px]">
                        <span className="font-sans font-semibold text-[var(--color-ink)]">
                          {isBuy ? "Tổng thanh toán" : "Tiền ròng nhận về"}
                        </span>
                        <strong className={isBuy ? "text-[var(--color-ink)]" : "text-[var(--color-gain)]"}>
                          {isBuy ? "−" : "+"}
                          {fmtVND(fin.totalCostOrProceeds)} ₫
                        </strong>
                      </div>
                    </div>
                  </div>
                )
              })()}
            </div>

            {selectedOrderReceipt.status === "PENDING" ||
            selectedOrderReceipt.status === "PARTIALLY_FILLED" ? (
              <div className="mt-6 grid grid-cols-2 gap-2.5 pt-4 border-t border-[var(--color-line)]">
                {selectedOrderReceipt.orderType === "LO" ? (
                  <button
                    type="button"
                    onClick={() => {
                      const target = selectedOrderReceipt
                      setSelectedOrderReceipt(null)
                      openAmendDialog(target)
                    }}
                    className="rounded-[var(--radius-sm)] border border-[var(--color-line-strong)] bg-[var(--color-surface)] py-2.5 text-[12.5px] font-semibold text-[var(--color-ink)] hover:border-[var(--color-ink)] transition-colors"
                  >
                    Sửa lệnh này
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={() => {
                      selectSymbol(selectedOrderReceipt.symbol)
                      setSelectedOrderReceipt(null)
                    }}
                    className="rounded-[var(--radius-sm)] border border-[var(--color-line-strong)] bg-[var(--color-surface)] py-2.5 text-[12.5px] font-medium text-[var(--color-ink)] hover:border-[var(--color-ink)] transition-colors"
                  >
                    Xem biểu đồ {selectedOrderReceipt.symbol}
                  </button>
                )}
                <button
                  type="button"
                  disabled={actionOrderId === selectedOrderReceipt.id}
                  onClick={() => handleCancelOrder(selectedOrderReceipt)}
                  className="rounded-[var(--radius-sm)] bg-[var(--color-loss)] py-2.5 text-[12.5px] font-semibold text-[var(--color-paper)] hover:opacity-90 disabled:opacity-45 transition-opacity"
                >
                  Hủy phần chờ khớp
                </button>
              </div>
            ) : (
              <div className="mt-6 grid grid-cols-2 gap-2.5 pt-4 border-t border-[var(--color-line)]">
                <button
                  type="button"
                  onClick={() => {
                    selectSymbol(selectedOrderReceipt.symbol)
                    setSelectedOrderReceipt(null)
                  }}
                  className="rounded-[var(--radius-sm)] border border-[var(--color-line-strong)] bg-[var(--color-surface)] py-2.5 text-[12.5px] font-medium text-[var(--color-ink)] hover:border-[var(--color-ink)] transition-colors"
                >
                  Xem biểu đồ {selectedOrderReceipt.symbol}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    selectSymbol(
                      selectedOrderReceipt.symbol,
                      selectedOrderReceipt.side,
                      selectedOrderReceipt.quantity
                    )
                    fillPriceInput(Number(selectedOrderReceipt.limitPrice || selectedOrderReceipt.price))
                    setSelectedOrderReceipt(null)
                    window.scrollTo({ top: 0, behavior: "smooth" })
                  }}
                  className="rounded-[var(--radius-sm)] bg-[var(--color-ink)] py-2.5 text-[12.5px] font-semibold text-[var(--color-paper)] hover:opacity-90 transition-opacity"
                >
                  Đặt lại lệnh này
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
