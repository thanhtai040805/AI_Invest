"use client"

import React, { memo, useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Page } from "@/components/Shell"
import { StockDetailModal } from "@/components/StockDetailView"
import {
  Button,
  CustomSelect,
  Panel,
  Pill,
  fmt,
  fmtPct,
  type SelectOption,
} from "@/components/ui"
import { marketApi, workspaceApi } from "@/lib/api"
import { useResource } from "@/lib/api/use-resource"
import { getSocket } from "@/lib/socket"
import { formatSectorName } from "@/lib/sector-weights"
import {
  useRealtimeMarket,
  useRealtimeMarketOrderBooks,
  type RealtimeMarketOrderBook,
} from "@/lib/use-realtime"
import type { ApiMarketIndex, ApiMarketSnapshot } from "@/types"

interface ApiIndicesResponse {
  indices?: ApiMarketIndex[]
  history?: Record<string, number[]>
  asOf?: string | null
  source?: string
  stale?: boolean
}

interface ApiSnapshotResponse extends ApiMarketSnapshot {
  source?: string
  quoteSource?: string
  redisQuoteCount?: number
  stale?: boolean
  asOf?: string | null
  foreignFlowAsOf?: string | null
}

interface ApiOrderBookLevel {
  price: number
  volume: number
}

interface ApiOrderBook {
  symbol?: string
  bids: ApiOrderBookLevel[]
  asks: ApiOrderBookLevel[]
  receivedAt?: number
  stale?: boolean
  lastUpdate?: string
  source?: string
}

interface ApiOrderBooksResponse {
  orderbooks?: Record<string, ApiOrderBook>
}

interface ApiLiquidityResponse {
  totalValueBillion?: number | null
  stockCount?: number
  approximate?: boolean
  stale?: boolean
  source?: string
  lastUpdate?: string | null
  asOf?: string | null
}

interface SuperStockItem {
  symbol?: string
  sector?: string
}

interface ApiSuperStocksResponse {
  items?: SuperStockItem[]
}

interface WatchlistRecord {
  id: string
  name: string
  symbols: string[]
}

type HoseTone = "ceiling" | "gain" | "ref" | "loss" | "floor" | "neutral"

interface MarketBoardItem {
  symbol: string
  name: string
  searchText: string
  sectorLabel: string
  price: number
  ref: number | null
  ceiling: number
  floor: number
  priceDiff: number | null
  changePct: number | null
  tone: HoseTone
  rawVolume: number
  valueBillion: number
  foreign: number
  foreignKnown: boolean
  isLiveTick: boolean
  priceBandAsOf?: string | null
  isVn30: boolean
}

type PresetTab = "VN30" | "ALL" | "WATCHLIST" | "CASHFLOW" | "FOREIGN" | "BREAKOUT"
type ForeignSide = "BUY" | "SELL"
type BreakoutSide = "ALL" | "UP" | "DOWN"
type ReturnBucketKey =
  | "FLOOR"
  | "DEEP_LOSS"
  | "MILD_LOSS"
  | "REF"
  | "MILD_GAIN"
  | "STRONG_GAIN"
  | "CEILING"

type SortField =
  | "symbol"
  | "price"
  | "priceDiff"
  | "changePct"
  | "bidPrice"
  | "askPrice"
  | "volume"
  | "valueBillion"
  | "foreign"
type SortDir = "asc" | "desc"

const LOCAL_WATCHLIST_KEY = "aiinvest_markets_watchlist_v1"
const PAGE_SIZE_OPTIONS = [30, 50, 100] as const

const VN30_SYMBOLS = new Set([
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

function stripViAccents(input: string): string {
  return input
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/đ/g, "d")
    .replace(/Đ/g, "D")
    .toLowerCase()
}

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

function classifyReturnBucket(item: MarketBoardItem): ReturnBucketKey {
  if (item.tone === "floor") return "FLOOR"
  if (item.tone === "ceiling") return "CEILING"
  const pct = item.changePct ?? 0
  if (pct <= -3) return "DEEP_LOSS"
  if (pct < 0) return "MILD_LOSS"
  if (pct === 0 || item.tone === "ref") return "REF"
  if (pct < 3) return "MILD_GAIN"
  return "STRONG_GAIN"
}

const TONE_TEXT_CLASS: Record<HoseTone, string> = {
  ceiling: "text-ceiling",
  gain: "text-gain",
  ref: "text-ref",
  loss: "text-loss",
  floor: "text-floor",
  neutral: "text-muted",
}

function formatPriceCell(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n) || n <= 0) return "—"
  return fmt(Math.round(n))
}

function formatShareVolume(vol: number): string {
  if (!Number.isFinite(vol) || vol <= 0) return "—"
  if (vol >= 1_000_000) return `${(vol / 1_000_000).toFixed(2).replace(".", ",")} tr`
  if (vol >= 1_000) return `${(vol / 1_000).toFixed(1).replace(".", ",")} K`
  return fmt(Math.round(vol))
}

function formatOrderLotVolume(vol: number | undefined): string {
  if (vol == null || !Number.isFinite(vol) || vol <= 0) return "—"
  if (vol >= 1_000_000) return `${(vol / 1_000_000).toFixed(2).replace(".", ",")} tr`
  return fmt(Math.round(vol))
}

function formatBillionVnd(val: number): string {
  if (!Number.isFinite(val) || val <= 0) return "—"
  if (val >= 100) return fmt(Math.round(val))
  if (val >= 10) return val.toFixed(1).replace(".", ",")
  return val.toFixed(2).replace(".", ",")
}

function formatForeignBillion(val: number): string {
  if (Math.abs(val) < 0.005) return "0,00"
  const sign = val > 0 ? "+" : "-"
  const abs = Math.abs(val)
  const formatted =
    abs >= 100
      ? fmt(Math.round(abs))
      : abs >= 10
        ? abs.toFixed(1).replace(".", ",")
        : abs.toFixed(2).replace(".", ",")
  return `${sign}${formatted}`
}

function formatVnDate(iso?: string | null): string | null {
  if (!iso) return null
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return null
  return d.toLocaleDateString("vi-VN", {
    timeZone: "Asia/Ho_Chi_Minh",
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  })
}

interface VnIndexTrendChartProps {
  history: number[]
  isGain: boolean
}

const VnIndexTrendChart = memo(function VnIndexTrendChart({
  history,
  isGain,
}: VnIndexTrendChartProps) {
  const [hoverIdx, setHoverIdx] = useState<number | null>(null)

  const series = useMemo(() => {
    const clean = history.filter((n) => Number.isFinite(n) && n > 0)
    return clean.slice(-30)
  }, [history])

  const stats = useMemo(() => {
    if (series.length < 2) return null
    const min = Math.min(...series)
    const max = Math.max(...series)
    const span = Math.max(max - min, 1)
    const last20 = series.slice(-20)
    const ma20 = last20.reduce((acc, v) => acc + v, 0) / last20.length
    const startVal = series[0]
    const endVal = series[series.length - 1]
    const periodDiffPct = startVal > 0 ? ((endVal - startVal) / startVal) * 100 : 0

    const W = 320
    const H = 86
    const padX = 4
    const padY = 10
    const plotW = W - padX * 2
    const plotH = H - padY * 2

    const points = series.map((val, idx) => {
      const x = padX + (idx / (series.length - 1)) * plotW
      const y = padY + (1 - (val - min) / span) * plotH
      return { x, y, val, idx }
    })

    const linePath = points
      .map((p, idx) => `${idx === 0 ? "M" : "L"}${p.x.toFixed(1)},${p.y.toFixed(1)}`)
      .join(" ")
    const areaPath = `${linePath} L${points[points.length - 1].x.toFixed(1)},${H} L${points[0].x.toFixed(1)},${H} Z`
    const ma20Y = padY + (1 - Math.min(Math.max((ma20 - min) / span, 0), 1)) * plotH

    return {
      W,
      H,
      min,
      max,
      ma20,
      ma20Y,
      periodDiffPct,
      points,
      linePath,
      areaPath,
    }
  }, [series])

  if (!stats) {
    return (
      <div className="h-[86px] flex items-center justify-center text-[11.5px] text-muted font-mono">
        Đang tải chuỗi 30 phiên VN-Index…
      </div>
    )
  }

  const activePoint =
    hoverIdx != null && stats.points[hoverIdx]
      ? stats.points[hoverIdx]
      : stats.points[stats.points.length - 1]
  const strokeColor = isGain ? "var(--color-gain)" : "var(--color-loss)"
  const gradId = isGain ? "vniAreaGain" : "vniAreaLoss"

  return (
    <div className="mt-2">
      <div className="flex items-center justify-between text-[11px] font-mono tnum text-muted mb-1">
        <span>
          {hoverIdx != null
            ? `Phiên T-${stats.points.length - 1 - hoverIdx}: `
            : `MA20 (${stats.ma20.toLocaleString("vi-VN", { maximumFractionDigits: 1 })}): `}
          <strong className="text-ink font-semibold">
            {hoverIdx != null
              ? activePoint.val.toLocaleString("vi-VN", {
                  minimumFractionDigits: 2,
                  maximumFractionDigits: 2,
                })
              : `${activePoint.val >= stats.ma20 ? "Trên" : "Dưới"} ${(
                  ((activePoint.val - stats.ma20) / stats.ma20) *
                  100
                )
                  .toFixed(2)
                  .replace(".", ",")}%`}
          </strong>
        </span>
        <span>
          Biên 30P:{" "}
          <span className="text-secondary">
            {stats.min.toLocaleString("vi-VN", { maximumFractionDigits: 0 })}–
            {stats.max.toLocaleString("vi-VN", { maximumFractionDigits: 0 })}
          </span>
        </span>
      </div>

      <div
        className="relative h-[86px] w-full select-none"
        onMouseLeave={() => setHoverIdx(null)}
      >
        <svg
          viewBox={`0 0 ${stats.W} ${stats.H}`}
          className="h-full w-full overflow-visible"
          preserveAspectRatio="none"
        >
          <defs>
            <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={strokeColor} stopOpacity="0.22" />
              <stop offset="100%" stopColor={strokeColor} stopOpacity="0.01" />
            </linearGradient>
          </defs>

          {/* Đường lưới ngang Đỉnh / Đáy 30 phiên */}
          <line
            x1="0"
            y1="10"
            x2={stats.W}
            y2="10"
            stroke="var(--color-line)"
            strokeWidth="1"
          />
          <line
            x1="0"
            y1={stats.H - 10}
            x2={stats.W}
            y2={stats.H - 10}
            stroke="var(--color-line)"
            strokeWidth="1"
          />

          {/* Đường trung bình động MA20 nét đứt */}
          <line
            x1="0"
            y1={stats.ma20Y}
            x2={stats.W}
            y2={stats.ma20Y}
            stroke="var(--color-ref)"
            strokeWidth="1"
            strokeDasharray="3 3"
            opacity="0.75"
          />

          {/* Vùng màu & Đường giá VN-Index */}
          <path d={stats.areaPath} fill={`url(#${gradId})`} />
          <path
            d={stats.linePath}
            fill="none"
            stroke={strokeColor}
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
          />

          {/* Crosshair khi di chuột */}
          {hoverIdx != null && (
            <line
              x1={activePoint.x}
              y1="4"
              x2={activePoint.x}
              y2={stats.H - 4}
              stroke="var(--color-ink)"
              strokeWidth="1"
              strokeDasharray="2 2"
              opacity="0.45"
            />
          )}

          {/* Điểm chốt hiện tại hoặc đang hover */}
          <circle
            cx={activePoint.x}
            cy={activePoint.y}
            r="3.2"
            fill={strokeColor}
            stroke="var(--color-surface)"
            strokeWidth="1.5"
          />

          {/* Vùng bắt sự kiện hover mượt theo từng phiên */}
          {stats.points.map((pt, idx) => {
            const colW = stats.W / stats.points.length
            return (
              <rect
                key={pt.idx}
                x={idx * colW}
                y="0"
                width={colW}
                height={stats.H}
                fill="transparent"
                className="cursor-crosshair"
                onMouseEnter={() => setHoverIdx(idx)}
              />
            )
          })}
        </svg>

        {/* Nhãn chú thích trực tiếp trên đồ thị */}
        <span className="pointer-events-none absolute right-1 top-0.5 rounded bg-surface/85 px-1 font-mono text-[9.5px] text-muted tnum">
          Đỉnh {stats.max.toLocaleString("vi-VN", { maximumFractionDigits: 1 })}
        </span>
        <span className="pointer-events-none absolute right-1 bottom-0.5 rounded bg-surface/85 px-1 font-mono text-[9.5px] text-muted tnum">
          Đáy {stats.min.toLocaleString("vi-VN", { maximumFractionDigits: 1 })}
        </span>
      </div>
      <div className="mt-1 flex items-center justify-between font-mono text-[9.5px] text-muted tnum">
        <span>T-30 phiên</span>
        <span className="text-ref">-- Đường TB động MA20 ({stats.ma20.toLocaleString("vi-VN", { maximumFractionDigits: 1 })})</span>
        <span>Hiện tại</span>
      </div>
    </div>
  )
})

interface MarketBoardRowProps {
  item: MarketBoardItem
  book?: RealtimeMarketOrderBook
  starred: boolean
  flash?: "up" | "down"
  onToggleStar: (symbol: string) => void
  onOpenDetail: (symbol: string) => void
}

const MarketBoardRow = memo(function MarketBoardRow({
  item,
  book,
  starred,
  flash,
  onToggleStar,
  onOpenDetail,
}: MarketBoardRowProps) {
  const bid = book?.bids?.[0]
  const ask = book?.asks?.[0]
  const bidPrice = bid ? toVndPrice(bid.price) : 0
  const askPrice = ask ? toVndPrice(ask.price) : 0
  const bidTone =
    bidPrice > 0 ? resolveHoseTone(bidPrice, item.ref, item.ceiling, item.floor) : "neutral"
  const askTone =
    askPrice > 0 ? resolveHoseTone(askPrice, item.ref, item.ceiling, item.floor) : "neutral"
  const priceToneClass = TONE_TEXT_CLASS[item.tone]
  const symbolToneClass = item.price > 0 ? TONE_TEXT_CLASS[item.tone] : "text-ink"

  const staleBookTitle =
    book?.stale && book.receivedAt > 0
      ? `Snapshot sổ lệnh gần nhất lúc ${new Date(book.receivedAt).toLocaleString("vi-VN", {
          timeZone: "Asia/Ho_Chi_Minh",
        })}`
      : undefined

  const bandTitle = item.priceBandAsOf
    ? `Biên độ Trần/Sàn cập nhật ${formatVnDate(item.priceBandAsOf) ?? ""}`
    : `${item.name} · ${item.sectorLabel}`

  const flashClass = flash === "up" ? "flash-up" : flash === "down" ? "flash-down" : ""

  return (
    <tr
      onClick={() => onOpenDetail(item.symbol)}
      className="group h-[38px] cursor-pointer hover:bg-soft/55 transition-colors"
    >
      {/* Mã CK (ghim trái khi màn hình nhỏ) */}
      <td className="sticky left-0 z-10 bg-surface group-hover:bg-[#f2f4f2] transition-colors border-b border-r border-line pl-4 pr-3 py-2 whitespace-nowrap">
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation()
              onToggleStar(item.symbol)
            }}
            aria-label={starred ? `Bỏ theo dõi ${item.symbol}` : `Theo dõi ${item.symbol}`}
            title={starred ? "Bỏ khỏi danh mục theo dõi" : "Thêm vào danh mục theo dõi"}
            className={`text-[13px] leading-none transition-colors ${
              starred ? "text-gold" : "text-line-strong hover:text-secondary"
            }`}
          >
            {starred ? "★" : "☆"}
          </button>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation()
              onOpenDetail(item.symbol)
            }}
            title={`Xem chi tiết ${item.symbol} · ${item.name} (${item.sectorLabel})`}
            className={`font-mono text-[13px] font-semibold tracking-tight hover:underline ${symbolToneClass}`}
          >
            {item.symbol}
          </button>
          {item.isVn30 && (
            <span
              className="rounded bg-soft px-1 py-0.5 font-mono text-[9px] font-medium text-muted leading-none"
              title="Thuộc rổ chỉ số VN30"
            >
              30
            </span>
          )}
          {item.isLiveTick && (
            <span
              className="h-1.5 w-1.5 rounded-full bg-gain shrink-0"
              title="Đang nhận tick trực tiếp DNSE"
            />
          )}
        </div>
      </td>

      {/* Giá chuẩn: TC · Trần · Sàn */}
      <td
        className="border-b border-line px-2.5 py-2 text-right font-mono text-[12.5px] tnum text-ref whitespace-nowrap"
        title={bandTitle}
      >
        {formatPriceCell(item.ref)}
      </td>
      <td
        className="border-b border-line px-2.5 py-2 text-right font-mono text-[12.5px] tnum text-ceiling whitespace-nowrap"
        title={bandTitle}
      >
        {formatPriceCell(item.ceiling)}
      </td>
      <td
        className="border-b border-r border-line px-2.5 py-2 text-right font-mono text-[12.5px] tnum text-floor whitespace-nowrap"
        title={bandTitle}
      >
        {formatPriceCell(item.floor)}
      </td>

      {/* Dư mua: Giá 1 · KL 1 */}
      <td
        className={`border-b border-line px-2.5 py-2 text-right font-mono text-[12.5px] tnum whitespace-nowrap ${
          bidPrice > 0
            ? book?.stale
              ? `${TONE_TEXT_CLASS[bidTone]} opacity-70`
              : `${TONE_TEXT_CLASS[bidTone]} font-medium`
            : "text-disabled"
        }`}
        title={staleBookTitle}
      >
        {bidPrice > 0 ? formatPriceCell(bidPrice) : "—"}
      </td>
      <td
        className={`border-b border-r border-line px-2.5 py-2 text-right font-mono text-[12px] tnum whitespace-nowrap ${
          bid ? (book?.stale ? "text-muted/75" : "text-secondary") : "text-disabled"
        }`}
        title={staleBookTitle}
      >
        {bid ? formatOrderLotVolume(bid.volume) : "—"}
      </td>

      {/* Khớp lệnh: Giá · +/- · % */}
      <td
        className={`border-b border-line bg-soft/35 group-hover:bg-soft/75 px-2.5 py-2 text-right font-mono text-[12.5px] font-semibold tnum whitespace-nowrap ${priceToneClass} ${flashClass}`}
      >
        {formatPriceCell(item.price)}
      </td>
      <td
        className={`border-b border-line bg-soft/35 group-hover:bg-soft/75 px-2.5 py-2 text-right font-mono text-[12px] tnum whitespace-nowrap ${priceToneClass} ${flashClass}`}
      >
        {item.priceDiff == null
          ? "—"
          : item.priceDiff === 0
            ? "0"
            : `${item.priceDiff > 0 ? "+" : ""}${fmt(Math.round(item.priceDiff))}`}
      </td>
      <td
        className={`border-b border-r border-line bg-soft/35 group-hover:bg-soft/75 px-2.5 py-2 text-right font-mono text-[12px] font-medium tnum whitespace-nowrap ${priceToneClass} ${flashClass}`}
      >
        {item.changePct != null && Number.isFinite(item.changePct) ? (
          <span
            title={
              Math.abs(item.changePct) >= 20
                ? "Biến động vượt biên độ thường — đối chiếu ngày chốt quyền / tham chiếu"
                : undefined
            }
          >
            {item.changePct === 0 ? "0,00%" : fmtPct(item.changePct)}
            {Math.abs(item.changePct) >= 20 ? " *" : ""}
          </span>
        ) : (
          "—"
        )}
      </td>

      {/* Dư bán: Giá 1 · KL 1 */}
      <td
        className={`border-b border-line px-2.5 py-2 text-right font-mono text-[12.5px] tnum whitespace-nowrap ${
          askPrice > 0
            ? book?.stale
              ? `${TONE_TEXT_CLASS[askTone]} opacity-70`
              : `${TONE_TEXT_CLASS[askTone]} font-medium`
            : "text-disabled"
        }`}
        title={staleBookTitle}
      >
        {askPrice > 0 ? formatPriceCell(askPrice) : "—"}
      </td>
      <td
        className={`border-b border-r border-line px-2.5 py-2 text-right font-mono text-[12px] tnum whitespace-nowrap ${
          ask ? (book?.stale ? "text-muted/75" : "text-secondary") : "text-disabled"
        }`}
        title={staleBookTitle}
      >
        {ask ? formatOrderLotVolume(ask.volume) : "—"}
      </td>

      {/* Thanh khoản & ĐTNN: Tổng KL · GTGD (tỷ) · ĐTNN (tỷ) */}
      <td
        className="border-b border-line px-3 py-2 text-right font-mono text-[12.5px] text-ink tnum whitespace-nowrap"
        title={item.rawVolume > 0 ? `${fmt(Math.round(item.rawVolume))} cổ phiếu` : undefined}
      >
        {formatShareVolume(item.rawVolume)}
      </td>
      <td className="border-b border-line px-3 py-2 text-right font-mono text-[12.5px] text-secondary tnum whitespace-nowrap">
        {formatBillionVnd(item.valueBillion)}
      </td>
      <td
        className={`border-b border-line pr-4 pl-3 py-2 text-right font-mono text-[12.5px] font-medium tnum whitespace-nowrap ${
          !item.foreignKnown
            ? "text-disabled"
            : item.foreign > 0
              ? "text-gain"
              : item.foreign < 0
                ? "text-loss"
                : "text-muted"
        }`}
      >
        {item.foreignKnown ? formatForeignBillion(item.foreign) : "—"}
      </td>
    </tr>
  )
})

const RETURN_BUCKETS: Array<{
  key: ReturnBucketKey
  label: string
  shortDesc: string
  barClass: string
  textClass: string
}> = [
  {
    key: "FLOOR",
    label: "Sàn",
    shortDesc: "Giảm kịch sàn (-7%)",
    barClass: "bg-floor",
    textClass: "text-floor",
  },
  {
    key: "DEEP_LOSS",
    label: "<-3%",
    shortDesc: "Giảm sâu trên 3%",
    barClass: "bg-loss",
    textClass: "text-loss",
  },
  {
    key: "MILD_LOSS",
    label: "-3..0%",
    shortDesc: "Giảm nhẹ dưới 3%",
    barClass: "bg-loss/60",
    textClass: "text-loss",
  },
  {
    key: "REF",
    label: "TC",
    shortDesc: "Đứng giá tham chiếu (0%)",
    barClass: "bg-ref",
    textClass: "text-ref",
  },
  {
    key: "MILD_GAIN",
    label: "0..+3%",
    shortDesc: "Tăng nhẹ dưới 3%",
    barClass: "bg-gain/60",
    textClass: "text-gain",
  },
  {
    key: "STRONG_GAIN",
    label: ">+3%",
    shortDesc: "Tăng mạnh trên 3%",
    barClass: "bg-gain",
    textClass: "text-gain",
  },
  {
    key: "CEILING",
    label: "Trần",
    shortDesc: "Tăng kịch trần (+7%)",
    barClass: "bg-ceiling",
    textClass: "text-ceiling",
  },
]

export default function Markets() {
  const resource = useResource(
    () =>
      Promise.all([
        marketApi.indices().catch(() => null),
        marketApi.snapshot().catch(() => null),
        marketApi.liquidity().catch(() => null),
        marketApi.orderbooks().catch(() => null),
        marketApi.superStocks().catch(() => null),
        workspaceApi.watchlists().catch(() => []),
      ]),
    []
  )

  const { snapshot: liveSnapshot, indices: liveIndices, isLive, liquidity: liveLiquidity } =
    useRealtimeMarket()

  const [socketConnected, setSocketConnected] = useState<boolean>(() =>
    typeof window !== "undefined" ? getSocket().connected : false
  )
  const [nowMs, setNowMs] = useState<number>(0)

  useEffect(() => {
    const socket = getSocket()
    const sync = () => setSocketConnected(socket.connected)
    sync()
    socket.on("connect", sync)
    socket.on("disconnect", sync)
    const initTimer = window.setTimeout(() => setNowMs(Date.now()), 0)
    const tickTimer = window.setInterval(() => setNowMs(Date.now()), 5_000)
    return () => {
      socket.off("connect", sync)
      socket.off("disconnect", sync)
      window.clearTimeout(initTimer)
      window.clearInterval(tickTimer)
    }
  }, [])

  // Filter, Market Basket, Search, Sort & Pagination states
  const [preset, setPreset] = useState<PresetTab>("VN30")
  const [foreignSide, setForeignSide] = useState<ForeignSide>("BUY")
  const [breakoutSide, setBreakoutSide] = useState<BreakoutSide>("ALL")
  const [bucketFilter, setBucketFilter] = useState<ReturnBucketKey | null>(null)
  const [sectorFilter, setSectorFilter] = useState<string>("ALL")
  const [search, setSearch] = useState<string>("")
  const [quickAddSymbol, setQuickAddSymbol] = useState<string>("")
  const [sortField, setSortField] = useState<SortField>("valueBillion")
  const [sortDir, setSortDir] = useState<SortDir>("desc")
  const [page, setPage] = useState<number>(1)
  const [pageSize, setPageSize] = useState<number>(30)
  const [activeSymbol, setActiveSymbol] = useState<string | null>(null)

  // Watchlist state (derived from workspaceApi + localStorage fallback)
  const [localFallbackStars] = useState<string[]>(() => {
    if (typeof window === "undefined") return []
    try {
      const saved = window.localStorage.getItem(LOCAL_WATCHLIST_KEY)
      const parsed = saved ? (JSON.parse(saved) as unknown) : []
      return Array.isArray(parsed) ? parsed.map((s) => String(s).toUpperCase()) : []
    } catch {
      return []
    }
  })
  const [manualStarred, setManualStarred] = useState<string[] | null>(null)
  const createdWatchlistIdRef = useRef<string | null>(null)

  const [indicesRes, snapshotRes, liquidityRes, orderbooksRes, superStocksRes, watchlistsRes] =
    (resource.data || []) as [
      ApiIndicesResponse | null,
      ApiSnapshotResponse | null,
      ApiLiquidityResponse | null,
      ApiOrderBooksResponse | null,
      ApiSuperStocksResponse | null,
      WatchlistRecord[] | null,
    ]

  const primaryWatchlist = Array.isArray(watchlistsRes) ? watchlistsRes[0] : undefined
  const serverWatchlistId = primaryWatchlist?.id ?? null

  const starredSymbols = useMemo(() => {
    if (manualStarred !== null) return manualStarred
    if (
      primaryWatchlist &&
      Array.isArray(primaryWatchlist.symbols) &&
      primaryWatchlist.symbols.length > 0
    ) {
      return Array.from(new Set(primaryWatchlist.symbols.map((s) => String(s).toUpperCase())))
    }
    return localFallbackStars
  }, [manualStarred, primaryWatchlist, localFallbackStars])

  const starredSet = useMemo(() => new Set(starredSymbols), [starredSymbols])

  const persistWatchlist = useCallback(
    (next: string[]) => {
      setManualStarred(next)
      try {
        window.localStorage.setItem(LOCAL_WATCHLIST_KEY, JSON.stringify(next))
      } catch {
        // ignore storage quota errors
      }
      const wlId = createdWatchlistIdRef.current ?? serverWatchlistId
      if (wlId) {
        void workspaceApi.updateWatchlist(wlId, { symbols: next }).catch(() => undefined)
      } else {
        void workspaceApi
          .createWatchlist({ name: "Theo dõi chính", symbols: next })
          .then((created: { id?: string } | null) => {
            if (created?.id) createdWatchlistIdRef.current = created.id
          })
          .catch(() => undefined)
      }
    },
    [serverWatchlistId]
  )

  const handleToggleStar = useCallback(
    (symbol: string) => {
      const sym = symbol.toUpperCase()
      const exists = starredSymbols.includes(sym)
      const next = exists
        ? starredSymbols.filter((item) => item !== sym)
        : [sym, ...starredSymbols]
      persistWatchlist(next)
    },
    [starredSymbols, persistWatchlist]
  )

  const initialOrderbooks = useMemo(() => orderbooksRes?.orderbooks ?? {}, [orderbooksRes])
  const orderbooks = useRealtimeMarketOrderBooks(initialOrderbooks)

  const sectorMap = useMemo(() => {
    const map = new Map<string, string>()
    for (const item of superStocksRes?.items ?? []) {
      const sym = String(item.symbol ?? "").trim().toUpperCase()
      if (sym && item.sector) {
        map.set(sym, item.sector)
      }
    }
    return map
  }, [superStocksRes])

  const { initialIndices, vnIndexPointDiff, vnIndexHistory, boardItems } = useMemo(() => {
    const indicesList = Array.isArray(indicesRes?.indices) ? indicesRes.indices : []
    const normalizeIndexSym = (item: ApiMarketIndex) =>
      String(item.symbol ?? item.name ?? "")
        .toUpperCase()
        .replaceAll("-", "")

    const vnIndexItem = indicesList.find((x) => normalizeIndexSym(x) === "VNINDEX")

    const fmtIndexVal = (item?: ApiMarketIndex) => {
      const v = Number(item?.value ?? NaN)
      return Number.isFinite(v) && v > 0
        ? v.toLocaleString("vi-VN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
        : "—"
    }
    const fmtIndexPct = (item?: ApiMarketIndex) => {
      const raw = item?.changePercent ?? item?.change_pct
      const v = raw == null ? NaN : Number(raw)
      return Number.isFinite(v) ? v : null
    }

    const spark = Array.isArray(indicesRes?.history?.VNINDEX) ? indicesRes.history.VNINDEX : []
    const latestVni = Number(vnIndexItem?.value ?? spark[spark.length - 1] ?? NaN)
    const prevVni = spark.length >= 2 ? Number(spark[spark.length - 2]) : NaN
    const pointDiff =
      vnIndexItem?.change != null && Number.isFinite(Number(vnIndexItem.change))
        ? Number(vnIndexItem.change)
        : Number.isFinite(latestVni) && Number.isFinite(prevVni) && prevVni > 0
          ? latestVni - prevVni
          : null

    const parsedIndices = {
      vnIndexVal: fmtIndexVal(vnIndexItem),
      vnIndexPct: fmtIndexPct(vnIndexItem),
    }

    const isSocketSnapshot =
      Array.isArray(liveSnapshot?.stocks) && liveSnapshot.stocks.length > 0
    const rawStocks = isSocketSnapshot
      ? (liveSnapshot?.stocks ?? [])
      : Array.isArray(snapshotRes?.stocks)
        ? snapshotRes.stocks
        : []

    const dbBySymbol = new Map(
      (snapshotRes?.stocks ?? []).map((st) => [String(st.symbol ?? "").toUpperCase(), st])
    )

    const items: MarketBoardItem[] = rawStocks.map((r) => {
      const sym = String(r.symbol ?? "").trim().toUpperCase()
      const dbStock = dbBySymbol.get(sym)
      const name = String(r.name || dbStock?.name || sym).trim()
      const rawSector = String(r.industry || dbStock?.industry || sectorMap.get(sym) || "OTHER")
      const sectorLabel = rawSector === "OTHER" ? "Nhóm khác" : formatSectorName(rawSector)

      const price = toVndPrice(r.price)
      const rawRefVal = r.ref ?? dbStock?.ref
      const ref = rawRefVal != null && Number(rawRefVal) > 0 ? toVndPrice(rawRefVal) : null
      const { ceiling, floor } = resolveCoherentBands(
        ref,
        price,
        toVndPrice(r.ceiling ?? dbStock?.ceiling),
        toVndPrice(r.floor ?? dbStock?.floor)
      )

      const priceDiff = ref != null && ref > 0 && price > 0 ? price - ref : null
      const rawChange = r.changePercent ?? r.change_pct
      const changePct =
        rawChange != null && Number.isFinite(Number(rawChange))
          ? Number(Number(rawChange).toFixed(2))
          : priceDiff != null && ref && ref > 0
            ? Number(((priceDiff / ref) * 100).toFixed(2))
            : null

      const tone = resolveHoseTone(price, ref, ceiling, floor)
      const rawVol = Number(r.volume ?? dbStock?.volume ?? 0)
      const rawVolume = Number.isFinite(rawVol) && rawVol > 0 ? rawVol : 0

      const explicitTradingVal = Number(r.tradingValue ?? 0)
      const valueBillion =
        Number.isFinite(explicitTradingVal) && explicitTradingVal > 0
          ? explicitTradingVal > 1_000_000
            ? explicitTradingVal / 1_000_000_000
            : explicitTradingVal
          : price > 0 && rawVolume > 0
            ? (price * rawVolume) / 1_000_000_000
            : 0

      const rawForeign =
        r.foreign_flow ?? r.foreignFlow ?? dbStock?.foreign_flow ?? dbStock?.foreignFlow
      const foreignVal = rawForeign == null ? NaN : Number(rawForeign)
      const foreignKnown = Number.isFinite(foreignVal)

      const source = r.source ?? (isSocketSnapshot ? undefined : snapshotRes?.source)
      const isLiveTick = source === "dnse-ws"

      return {
        symbol: sym,
        name,
        searchText: `${sym.toLowerCase()} ${stripViAccents(name)} ${stripViAccents(sectorLabel)}`,
        sectorLabel,
        price,
        ref,
        ceiling,
        floor,
        priceDiff,
        changePct,
        tone,
        rawVolume,
        valueBillion,
        foreign: foreignKnown ? foreignVal : 0,
        foreignKnown,
        isLiveTick,
        priceBandAsOf: r.priceBandAsOf ?? dbStock?.priceBandAsOf,
        isVn30: VN30_SYMBOLS.has(sym),
      }
    })

    return {
      initialIndices: parsedIndices,
      vnIndexPointDiff: pointDiff,
      vnIndexHistory: spark,
      boardItems: items,
    }
  }, [indicesRes, snapshotRes, liveSnapshot, sectorMap])

  // Track realtime price flashes from WebSocket stream
  const prevPriceMapRef = useRef<Map<string, number>>(new Map())
  const [flashMap, setFlashMap] = useState<Record<string, "up" | "down">>({})

  useEffect(() => {
    for (const st of snapshotRes?.stocks ?? []) {
      const sym = String(st.symbol ?? "").trim().toUpperCase()
      const p = toVndPrice(st.price)
      if (sym && p > 0 && !prevPriceMapRef.current.has(sym)) {
        prevPriceMapRef.current.set(sym, p)
      }
    }
  }, [snapshotRes])

  useEffect(() => {
    const socket = getSocket()
    const clearTimers = new Set<number>()

    function onMarketSnapshot(payload: { stocks?: Array<{ symbol?: string; price?: unknown }> }) {
      const list = Array.isArray(payload?.stocks) ? payload.stocks : []
      if (!list.length) return
      const prev = prevPriceMapRef.current
      const changed: Record<string, "up" | "down"> = {}
      let hasChanges = false

      for (const raw of list) {
        const sym = String(raw.symbol ?? "").trim().toUpperCase()
        const nextPrice = toVndPrice(raw.price)
        if (!sym || nextPrice <= 0) continue
        const oldPrice = prev.get(sym)
        if (oldPrice && oldPrice > 0 && nextPrice !== oldPrice) {
          changed[sym] = nextPrice > oldPrice ? "up" : "down"
          hasChanges = true
        }
        prev.set(sym, nextPrice)
      }

      if (!hasChanges) return
      setFlashMap((curr) => ({ ...curr, ...changed }))
      const timerId = window.setTimeout(() => {
        clearTimers.delete(timerId)
        setFlashMap((curr) => {
          const next = { ...curr }
          for (const sym of Object.keys(changed)) {
            delete next[sym]
          }
          return next
        })
      }, 950)
      clearTimers.add(timerId)
    }

    socket.on("market:snapshot", onMarketSnapshot)
    return () => {
      socket.off("market:snapshot", onMarketSnapshot)
      for (const id of clearTimers) window.clearTimeout(id)
      clearTimers.clear()
    }
  }, [])

  // Comprehensive Market Structure Analytics (explains Breadth, Sector Flow, Foreign Flow & Basket Counts)
  const marketStats = useMemo(() => {
    let ceilingCount = 0
    let gainCount = 0
    let refCount = 0
    let lossCount = 0
    let floorCount = 0
    let foreignNet = 0
    let foreignBuyTotal = 0
    let foreignSellTotal = 0
    let foreignKnownCount = 0
    let foreignBuyCount = 0
    let foreignSellCount = 0
    let totalMatchedShares = 0
    let computedValueBillion = 0
    let liveTickCount = 0
    let vn30Count = 0
    let vn30GainCount = 0
    let vn30LossCount = 0
    let vn30ValueBillion = 0
    let cashflowFocusCount = 0
    let breakoutCount = 0
    let breakoutUpCount = 0
    let breakoutDownCount = 0

    const bucketCounts: Record<ReturnBucketKey, number> = {
      FLOOR: 0,
      DEEP_LOSS: 0,
      MILD_LOSS: 0,
      REF: 0,
      MILD_GAIN: 0,
      STRONG_GAIN: 0,
      CEILING: 0,
    }

    const sectorAgg = new Map<
      string,
      { label: string; valueBillion: number; pctSum: number; count: number }
    >()

    for (const item of boardItems) {
      const bucket = classifyReturnBucket(item)
      bucketCounts[bucket] += 1

      if (item.tone === "ceiling") {
        ceilingCount += 1
        gainCount += 1
      } else if (item.tone === "gain") {
        gainCount += 1
      } else if (item.tone === "ref") {
        refCount += 1
      } else if (item.tone === "floor") {
        floorCount += 1
        lossCount += 1
      } else if (item.tone === "loss") {
        lossCount += 1
      }

      if (item.isVn30) {
        vn30Count += 1
        vn30ValueBillion += item.valueBillion
        if (item.tone === "gain" || item.tone === "ceiling") vn30GainCount += 1
        else if (item.tone === "loss" || item.tone === "floor") vn30LossCount += 1
      }

      if (item.valueBillion >= 30) {
        cashflowFocusCount += 1
      }

      const absPct = Math.abs(item.changePct ?? 0)
      if (absPct >= 3 || item.tone === "ceiling" || item.tone === "floor") {
        breakoutCount += 1
        if ((item.changePct ?? 0) > 0 || item.tone === "ceiling") breakoutUpCount += 1
        else if ((item.changePct ?? 0) < 0 || item.tone === "floor") breakoutDownCount += 1
      }

      if (item.foreignKnown) {
        foreignKnownCount += 1
        foreignNet += item.foreign
        if (item.foreign > 0.05) {
          foreignBuyCount += 1
          foreignBuyTotal += item.foreign
        } else if (item.foreign < -0.05) {
          foreignSellCount += 1
          foreignSellTotal += Math.abs(item.foreign)
        }
      }

      if (item.sectorLabel !== "Nhóm khác") {
        const prev = sectorAgg.get(item.sectorLabel) ?? {
          label: item.sectorLabel,
          valueBillion: 0,
          pctSum: 0,
          count: 0,
        }
        prev.valueBillion += item.valueBillion
        if (item.changePct != null && Number.isFinite(item.changePct)) {
          prev.pctSum += item.changePct
          prev.count += 1
        }
        sectorAgg.set(item.sectorLabel, prev)
      }

      totalMatchedShares += item.rawVolume
      computedValueBillion += item.valueBillion
      if (item.isLiveTick) liveTickCount += 1
    }

    const topSectors = Array.from(sectorAgg.values())
      .sort((a, b) => b.valueBillion - a.valueBillion)
      .slice(0, 3)
      .map((s) => ({
        label: s.label,
        valueBillion: s.valueBillion,
        avgPct: s.count > 0 ? s.pctSum / s.count : 0,
      }))

    const topForeignBuys = boardItems
      .filter((i) => i.foreignKnown && i.foreign > 0.05)
      .sort((a, b) => b.foreign - a.foreign)
      .slice(0, 4)

    const topForeignSells = boardItems
      .filter((i) => i.foreignKnown && i.foreign < -0.05)
      .sort((a, b) => a.foreign - b.foreign)
      .slice(0, 4)

    const maxForeignAbs = Math.max(
      1,
      ...topForeignBuys.map((x) => Math.abs(x.foreign)),
      ...topForeignSells.map((x) => Math.abs(x.foreign))
    )

    const maxBucketCount = Math.max(1, ...Object.values(bucketCounts))

    return {
      ceilingCount,
      gainCount,
      refCount,
      lossCount,
      floorCount,
      foreignNet,
      foreignBuyTotal,
      foreignSellTotal,
      foreignKnownCount,
      foreignBuyCount,
      foreignSellCount,
      totalMatchedShares,
      computedValueBillion,
      liveTickCount,
      vn30Count,
      vn30GainCount,
      vn30LossCount,
      vn30ValueBillion,
      cashflowFocusCount,
      breakoutCount,
      breakoutUpCount,
      breakoutDownCount,
      bucketCounts,
      maxBucketCount,
      topSectors,
      topForeignBuys,
      topForeignSells,
      maxForeignAbs,
    }
  }, [boardItems])

  const sectorOptions = useMemo<SelectOption[]>(() => {
    const counts = new Map<string, number>()
    for (const item of boardItems) {
      counts.set(item.sectorLabel, (counts.get(item.sectorLabel) ?? 0) + 1)
    }
    const sortedSectors = Array.from(counts.entries()).sort((a, b) => {
      if (a[0] === "Nhóm khác") return 1
      if (b[0] === "Nhóm khác") return -1
      return b[1] - a[1]
    })
    return [
      { value: "ALL", label: "Tất cả nhóm ngành", meta: `${boardItems.length}` },
      ...sortedSectors.map(([label, count]) => ({
        value: label,
        label,
        meta: `${count}`,
      })),
    ]
  }, [boardItems])

  const handleSelectPreset = (nextPreset: PresetTab) => {
    setPreset(nextPreset)
    setBucketFilter(null)
    setPage(1)
    if (nextPreset === "VN30" || nextPreset === "ALL" || nextPreset === "CASHFLOW") {
      setSortField("valueBillion")
      setSortDir("desc")
    } else if (nextPreset === "FOREIGN") {
      setSortField("foreign")
      setSortDir(foreignSide === "BUY" ? "desc" : "asc")
    } else if (nextPreset === "BREAKOUT") {
      setSortField("changePct")
      setSortDir(breakoutSide === "DOWN" ? "asc" : "desc")
    }
  }

  const handleQuickAddWatchlist = (e: React.FormEvent) => {
    e.preventDefault()
    const raw = quickAddSymbol.trim().toUpperCase()
    if (!raw) return
    const tokens = raw
      .split(/[\s,;]+/)
      .map((s) => s.trim().toUpperCase())
      .filter((s) => s.length >= 3)
    if (!tokens.length) return
    const validSymbols = new Set(boardItems.map((b) => b.symbol))
    const matched = tokens.filter((sym) => validSymbols.has(sym))
    const toAdd = matched.length > 0 ? matched : tokens
    const next = Array.from(new Set([...toAdd, ...starredSymbols]))
    persistWatchlist(next)
    setQuickAddSymbol("")
  }

  const handleSort = (field: SortField) => {
    setPage(1)
    if (sortField === field) {
      setSortDir((d) => (d === "desc" ? "asc" : "desc"))
    } else {
      setSortField(field)
      setSortDir(field === "symbol" ? "asc" : "desc")
    }
  }

  const filteredItems = useMemo(() => {
    const q = stripViAccents(search.trim())

    const filtered = boardItems.filter((item) => {
      if (bucketFilter && classifyReturnBucket(item) !== bucketFilter) return false

      if (!bucketFilter) {
        if (preset === "VN30" && !item.isVn30) return false
        if (preset === "WATCHLIST" && !starredSet.has(item.symbol)) return false
        if (preset === "CASHFLOW" && item.valueBillion < 30) return false
        if (preset === "FOREIGN") {
          if (!item.foreignKnown) return false
          if (foreignSide === "BUY" && item.foreign <= 0.05) return false
          if (foreignSide === "SELL" && item.foreign >= -0.05) return false
        }
        if (preset === "BREAKOUT") {
          const pct = item.changePct ?? 0
          const isStrongUp = pct >= 3 || item.tone === "ceiling"
          const isStrongDown = pct <= -3 || item.tone === "floor"
          if (breakoutSide === "UP" && !isStrongUp) return false
          if (breakoutSide === "DOWN" && !isStrongDown) return false
          if (breakoutSide === "ALL" && !isStrongUp && !isStrongDown) return false
        }
      }

      if (sectorFilter !== "ALL" && item.sectorLabel !== sectorFilter) return false
      if (q && !item.searchText.includes(q)) return false
      return true
    })

    const mult = sortDir === "asc" ? 1 : -1
    filtered.sort((a, b) => {
      if (sortField === "symbol") {
        return a.symbol.localeCompare(b.symbol) * mult
      }
      const getVal = (it: MarketBoardItem): number => {
        switch (sortField) {
          case "price":
            return it.price
          case "priceDiff":
            return it.priceDiff ?? -Infinity
          case "changePct":
            return it.changePct ?? -Infinity
          case "bidPrice":
            return toVndPrice(orderbooks[it.symbol]?.bids?.[0]?.price)
          case "askPrice":
            return toVndPrice(orderbooks[it.symbol]?.asks?.[0]?.price)
          case "volume":
            return it.rawVolume
          case "valueBillion":
            return it.valueBillion
          case "foreign":
            return it.foreignKnown ? it.foreign : -Infinity
          default:
            return 0
        }
      }
      const va = getVal(a)
      const vb = getVal(b)
      if (va === vb) return a.symbol.localeCompare(b.symbol)
      if (va === -Infinity) return 1
      if (vb === -Infinity) return -1
      return (va - vb) * mult
    })

    return filtered
  }, [
    boardItems,
    bucketFilter,
    preset,
    starredSet,
    foreignSide,
    breakoutSide,
    sectorFilter,
    search,
    sortField,
    sortDir,
    orderbooks,
  ])

  // Paginated slice: keeps DOM ultra-light (default 30 rows) while searching/sorting all 367 symbols
  const totalPages = Math.max(1, Math.ceil(filteredItems.length / pageSize))
  const safePage = Math.min(page, totalPages)
  const startIdx = (safePage - 1) * pageSize
  const pagedItems = useMemo(
    () => filteredItems.slice(startIdx, startIdx + pageSize),
    [filteredItems, startIdx, pageSize]
  )

  const currentVniVal = liveIndices?.vnIndexVal || initialIndices.vnIndexVal
  const currentVniPct =
    liveIndices?.vnIndexPct !== undefined ? liveIndices.vnIndexPct : initialIndices.vnIndexPct

  const liveLiquidityAt = Date.parse(String(liveLiquidity?.lastUpdate ?? ""))
  const activeLiquidity = (
    liveLiquidityAt > 0 && nowMs > 0 && nowMs - liveLiquidityAt < 15_000
      ? liveLiquidity
      : liquidityRes
  ) as ApiLiquidityResponse | null
  const liquidityBillion =
    Number(activeLiquidity?.totalValueBillion ?? 0) > 0
      ? Number(activeLiquidity?.totalValueBillion)
      : marketStats.computedValueBillion

  const asOfDate =
    formatVnDate(snapshotRes?.asOf) ??
    formatVnDate(activeLiquidity?.asOf) ??
    formatVnDate(indicesRes?.asOf)
  const hasActiveRealtime = isLive || marketStats.liveTickCount > 0

  const sortIcon = (field: SortField) => {
    if (sortField !== field) return null
    return <span className="ml-1 text-ink">{sortDir === "desc" ? "↓" : "↑"}</span>
  }

  const activeBucketMeta = bucketFilter
    ? RETURN_BUCKETS.find((b) => b.key === bucketFilter)
    : undefined

  // Resolve active stock seed + prev/next ticker navigation inside the popup
  const activeStockNav = useMemo(() => {
    if (!activeSymbol) {
      return { item: null, prevSymbol: null as string | null, nextSymbol: null as string | null }
    }
    const list = filteredItems.some((it) => it.symbol === activeSymbol)
      ? filteredItems
      : boardItems
    const idx = list.findIndex((it) => it.symbol === activeSymbol)
    const item = idx >= 0 ? list[idx] : boardItems.find((it) => it.symbol === activeSymbol) ?? null
    const prevSymbol = idx > 0 ? list[idx - 1].symbol : list.length > 1 ? list[list.length - 1].symbol : null
    const nextSymbol =
      idx >= 0 && idx < list.length - 1 ? list[idx + 1].symbol : list.length > 1 ? list[0].symbol : null
    return { item, prevSymbol, nextSymbol }
  }, [activeSymbol, filteredItems, boardItems])

  return (
    <Page
      title="Bảng giá trực tuyến"
      sub="Sàn HOSE · Bức tranh cấu trúc dòng tiền, rổ chỉ số VN30, sổ lệnh trực tiếp và giao dịch khối ngoại."
      actions={
        <Pill tone={hasActiveRealtime ? "gain" : socketConnected ? "teal" : "neutral"}>
          <span
            className={`h-1.5 w-1.5 rounded-full ${
              hasActiveRealtime
                ? "bg-gain animate-pulse"
                : socketConnected
                  ? "bg-mineral animate-pulse"
                  : "bg-muted"
            }`}
          />
          {hasActiveRealtime
            ? `Trực tiếp DNSE · ${
                liveSnapshot?.liveSymbols ?? marketStats.liveTickCount
              }/${boardItems.length} mã`
            : asOfDate
              ? `Chốt phiên ${asOfDate}`
              : "Đang đồng bộ"}
        </Pill>
      }
    >
      {/* 1. BỨC TRANH TOÀN CẢNH SÀN HOSE — 3 ĐỒ THỊ TRỰC QUAN GIẢI THÍCH XU HƯỚNG, ĐỘ RỘNG & DÒNG TIỀN */}
      <Panel flush className="overflow-hidden">
        <div className="grid grid-cols-1 lg:grid-cols-12 divide-y lg:divide-y-0 lg:divide-x divide-line">
          {/* KHỐI 1: ĐỒ THỊ VÙNG VN-INDEX 30 PHIÊN & TƯƠNG QUAN MA20 */}
          <div className="lg:col-span-4 p-4 flex flex-col justify-between bg-surface">
            <div>
              <div className="flex items-start justify-between gap-2">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="text-[11px] font-semibold uppercase tracking-wider text-muted">
                      VN-Index · 30 Phiên
                    </span>
                    <span className="rounded bg-soft px-1.5 py-0.5 font-mono text-[10px] text-secondary tnum">
                      HOSE
                    </span>
                  </div>
                  <div className="mt-1 flex items-baseline gap-2.5">
                    <span className="font-mono text-[24px] font-semibold tracking-tight text-ink tnum leading-none">
                      {currentVniVal}
                    </span>
                    {currentVniVal !== "—" && currentVniPct != null && (
                      <span
                        className={`font-mono text-[12.5px] font-semibold tnum ${
                          currentVniPct > 0
                            ? "text-gain"
                            : currentVniPct < 0
                              ? "text-loss"
                              : "text-ref"
                        }`}
                      >
                        {currentVniPct > 0 ? "▲ " : currentVniPct < 0 ? "▼ " : ""}
                        {vnIndexPointDiff != null
                          ? `${Math.abs(vnIndexPointDiff).toLocaleString("vi-VN", {
                              minimumFractionDigits: 2,
                              maximumFractionDigits: 2,
                            })} (`
                          : ""}
                        {currentVniPct === 0 ? "0,00%" : fmtPct(currentVniPct)}
                        {vnIndexPointDiff != null ? ")" : ""}
                      </span>
                    )}
                  </div>
                </div>

                <div className="text-right">
                  <div className="text-[10.5px] uppercase tracking-wider text-muted">
                    Thanh khoản sàn
                  </div>
                  <div className="font-mono text-[14px] font-semibold text-ink tnum mt-0.5">
                    {liquidityBillion > 0 ? `${fmt(Math.round(liquidityBillion))} tỷ ₫` : "—"}
                  </div>
                  <div className="font-mono text-[10.5px] text-muted tnum">
                    KL: {formatShareVolume(marketStats.totalMatchedShares)} CP
                  </div>
                </div>
              </div>

              <VnIndexTrendChart
                history={vnIndexHistory}
                isGain={(currentVniPct ?? 0) >= 0}
              />
            </div>

            <div className="mt-2.5 pt-2 border-t border-line flex items-center justify-between text-[11px] text-secondary">
              <span>
                Rổ trụ <strong className="text-ink font-medium">VN30</strong>:{" "}
                <span className="font-mono text-gain tnum">{marketStats.vn30GainCount} tăng</span> ·{" "}
                <span className="font-mono text-loss tnum">{marketStats.vn30LossCount} giảm</span>
              </span>
              <span className="font-mono text-[11px] text-muted tnum">
                GTGD VN30: {fmt(Math.round(marketStats.vn30ValueBillion))} tỷ (
                {liquidityBillion > 0
                  ? `${Math.round((marketStats.vn30ValueBillion / liquidityBillion) * 100)}%`
                  : "—"}
                )
              </span>
            </div>
          </div>

          {/* KHỐI 2: ĐỒ THỊ PHỔ PHÂN BỔ BIÊN ĐỘ 7 MỨC & TOP NGÀNH HÚT TIỀN */}
          <div className="lg:col-span-4 p-4 flex flex-col justify-between bg-surface">
            <div>
              <div className="flex items-center justify-between gap-2">
                <div>
                  <span className="text-[11px] font-semibold uppercase tracking-wider text-muted">
                    Phổ phân bổ biên độ ({boardItems.length} mã)
                  </span>
                  <div className="mt-0.5 flex items-center gap-2 font-mono text-[12px] tnum">
                    <span className="text-gain font-semibold">
                      ▲ {marketStats.gainCount} tăng{" "}
                      <span className="text-ceiling font-normal">({marketStats.ceilingCount} trần)</span>
                    </span>
                    <span className="text-line-strong">·</span>
                    <span className="text-ref font-medium">■ {marketStats.refCount} TC</span>
                    <span className="text-line-strong">·</span>
                    <span className="text-loss font-semibold">
                      ▼ {marketStats.lossCount} giảm{" "}
                      <span className="text-floor font-normal">({marketStats.floorCount} sàn)</span>
                    </span>
                  </div>
                </div>
                {bucketFilter && (
                  <button
                    type="button"
                    onClick={() => setBucketFilter(null)}
                    className="rounded bg-soft px-2 py-0.5 text-[10.5px] font-medium text-ink hover:bg-raised transition-colors"
                  >
                    Bỏ lọc ✕
                  </button>
                )}
              </div>

              {/* Histogram 7 cột biên độ tương tác */}
              <div className="mt-3 grid grid-cols-7 gap-1.5 items-end h-[74px] pt-4 px-1 rounded-[6px] bg-soft/45 border border-line/70">
                {RETURN_BUCKETS.map((b) => {
                  const count = marketStats.bucketCounts[b.key]
                  const heightPct =
                    count > 0
                      ? Math.max(12, Math.round((count / marketStats.maxBucketCount) * 100))
                      : 4
                  const isSelected = bucketFilter === b.key
                  return (
                    <button
                      key={b.key}
                      type="button"
                      onClick={() => {
                        setBucketFilter((prev) => (prev === b.key ? null : b.key))
                        setPage(1)
                      }}
                      title={`${b.shortDesc}: ${count} mã (Bấm để lọc trên bảng giá)`}
                      className={`group flex flex-col items-center justify-end h-full rounded px-0.5 pb-1 transition-all ${
                        isSelected
                          ? "bg-surface ring-1 ring-ink shadow-2xs"
                          : "hover:bg-surface/70"
                      }`}
                    >
                      <span
                        className={`font-mono text-[10.5px] font-semibold tnum leading-none mb-1 ${b.textClass}`}
                      >
                        {count}
                      </span>
                      <div className="w-full px-1 flex items-end h-[34px]">
                        <div
                          className={`w-full rounded-t-[3px] transition-all ${b.barClass} ${
                            isSelected ? "opacity-100" : "opacity-85 group-hover:opacity-100"
                          }`}
                          style={{ height: `${heightPct}%` }}
                        />
                      </div>
                      <span className="mt-1 font-mono text-[9.5px] text-secondary tnum leading-none whitespace-nowrap">
                        {b.label}
                      </span>
                    </button>
                  )
                })}
              </div>
            </div>

            {/* Top 3 nhóm ngành hút dòng tiền lớn nhất phiên */}
            <div className="mt-2.5 pt-2 border-t border-line">
              <div className="flex items-center justify-between text-[10.5px] text-muted mb-1">
                <span className="uppercase tracking-wider font-semibold">
                  Top ngành dẫn dắt thanh khoản
                </span>
                <span>Bấm để lọc ngành</span>
              </div>
              <div className="grid grid-cols-3 gap-1.5">
                {marketStats.topSectors.map((sec) => {
                  const activeSec = sectorFilter === sec.label
                  return (
                    <button
                      key={sec.label}
                      type="button"
                      onClick={() => {
                        setSectorFilter((curr) => (curr === sec.label ? "ALL" : sec.label))
                        setPage(1)
                      }}
                      className={`text-left rounded-[5px] border px-2 py-1 transition-colors ${
                        activeSec
                          ? "border-ink bg-soft"
                          : "border-line bg-surface hover:bg-soft/60"
                      }`}
                    >
                      <div className="truncate text-[11px] font-medium text-ink">{sec.label}</div>
                      <div className="flex items-baseline justify-between gap-1 font-mono text-[10.5px] tnum mt-0.5">
                        <span className="text-secondary">
                          {fmt(Math.round(sec.valueBillion))} tỷ
                        </span>
                        <span
                          className={
                            sec.avgPct > 0
                              ? "text-gain font-medium"
                              : sec.avgPct < 0
                                ? "text-loss font-medium"
                                : "text-ref"
                          }
                        >
                          {sec.avgPct > 0 ? "+" : ""}
                          {sec.avgPct.toFixed(1).replace(".", ",")}%
                        </span>
                      </div>
                    </button>
                  )
                })}
              </div>
            </div>
          </div>

          {/* KHỐI 3: ĐỒ THỊ ĐỐI XỨNG DÒNG VỐN KHỐI NGOẠI (TOP MUA RÒNG VS TOP BÁN RÒNG) */}
          <div className="lg:col-span-4 p-4 flex flex-col justify-between bg-surface">
            <div>
              <div className="flex items-start justify-between gap-2">
                <div>
                  <span className="text-[11px] font-semibold uppercase tracking-wider text-muted">
                    Tâm điểm Khối ngoại (ĐTNN)
                  </span>
                  <div className="mt-0.5 flex items-baseline gap-2">
                    <span
                      className={`font-mono text-[20px] font-semibold tnum leading-none ${
                        marketStats.foreignNet >= 0 ? "text-gain" : "text-loss"
                      }`}
                    >
                      {marketStats.foreignNet >= 0 ? "+" : ""}
                      {fmt(Math.round(marketStats.foreignNet))} tỷ ₫
                    </span>
                    <span className="font-mono text-[11px] text-muted tnum">
                      ({marketStats.foreignBuyCount} mua · {marketStats.foreignSellCount} bán)
                    </span>
                  </div>
                </div>

                {/* Tỷ trọng Tổng Mua vs Tổng Bán */}
                <div className="text-right font-mono text-[10.5px] tnum">
                  <div className="text-gain">
                    Mua: +{fmt(Math.round(marketStats.foreignBuyTotal))} tỷ
                  </div>
                  <div className="text-loss">
                    Bán: -{fmt(Math.round(marketStats.foreignSellTotal))} tỷ
                  </div>
                </div>
              </div>

              {/* Đồ thị Butterfly / Diverging Bars: Top 4 Mua ròng vs Top 4 Bán ròng */}
              <div className="mt-2.5 grid grid-cols-2 gap-3 pt-1">
                {/* Cột Mua ròng mạnh nhất */}
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between text-[10px] font-semibold uppercase tracking-wider text-gain">
                    <span>Top Mua ròng</span>
                    <span>Giá trị (tỷ)</span>
                  </div>
                  {marketStats.topForeignBuys.map((st) => {
                    const widthPct = Math.max(
                      14,
                      Math.round((Math.abs(st.foreign) / marketStats.maxForeignAbs) * 100)
                    )
                    return (
                      <div key={st.symbol} className="relative h-[22px] rounded bg-soft/60 overflow-hidden">
                        <div
                          className="absolute inset-y-0 left-0 bg-gain/18 border-r border-gain/40"
                          style={{ width: `${widthPct}%` }}
                        />
                        <div className="relative z-10 h-full px-2 flex items-center justify-between font-mono text-[11.5px] tnum">
                          <button
                            type="button"
                            onClick={() => setActiveSymbol(st.symbol)}
                            className="font-semibold text-ink hover:underline"
                            title={`Xem chi tiết ${st.symbol} · ${st.name} (Giá: ${formatPriceCell(st.price)})`}
                          >
                            {st.symbol}
                          </button>
                          <span className="text-gain font-medium">
                            {formatForeignBillion(st.foreign)}
                          </span>
                        </div>
                      </div>
                    )
                  })}
                </div>

                {/* Cột Bán ròng mạnh nhất */}
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between text-[10px] font-semibold uppercase tracking-wider text-loss">
                    <span>Top Bán ròng</span>
                    <span>Giá trị (tỷ)</span>
                  </div>
                  {marketStats.topForeignSells.map((st) => {
                    const widthPct = Math.max(
                      14,
                      Math.round((Math.abs(st.foreign) / marketStats.maxForeignAbs) * 100)
                    )
                    return (
                      <div key={st.symbol} className="relative h-[22px] rounded bg-soft/60 overflow-hidden">
                        <div
                          className="absolute inset-y-0 right-0 bg-loss/18 border-l border-loss/40"
                          style={{ width: `${widthPct}%` }}
                        />
                        <div className="relative z-10 h-full px-2 flex items-center justify-between font-mono text-[11.5px] tnum">
                          <button
                            type="button"
                            onClick={() => setActiveSymbol(st.symbol)}
                            className="font-semibold text-ink hover:underline"
                            title={`Xem chi tiết ${st.symbol} · ${st.name} (Giá: ${formatPriceCell(st.price)})`}
                          >
                            {st.symbol}
                          </button>
                          <span className="text-loss font-medium">
                            {formatForeignBillion(st.foreign)}
                          </span>
                        </div>
                      </div>
                    )
                  })}
                </div>
              </div>
            </div>

            {/* Thanh cân bằng lực Mua / Bán khối ngoại */}
            <div className="mt-2.5 pt-2 border-t border-line flex items-center gap-2">
              <span className="font-mono text-[10.5px] text-gain font-medium tnum shrink-0">
                Mua{" "}
                {marketStats.foreignBuyTotal + marketStats.foreignSellTotal > 0
                  ? Math.round(
                      (marketStats.foreignBuyTotal /
                        (marketStats.foreignBuyTotal + marketStats.foreignSellTotal)) *
                        100
                    )
                  : 50}
                %
              </span>
              <div className="h-1.5 flex-1 rounded-full bg-soft overflow-hidden flex">
                <div
                  className="bg-gain h-full"
                  style={{
                    width: `${
                      marketStats.foreignBuyTotal + marketStats.foreignSellTotal > 0
                        ? (marketStats.foreignBuyTotal /
                            (marketStats.foreignBuyTotal + marketStats.foreignSellTotal)) *
                          100
                        : 50
                    }%`,
                  }}
                />
                <div className="bg-loss h-full flex-1" />
              </div>
              <span className="font-mono text-[10.5px] text-loss font-medium tnum shrink-0">
                Bán{" "}
                {marketStats.foreignBuyTotal + marketStats.foreignSellTotal > 0
                  ? Math.round(
                      (marketStats.foreignSellTotal /
                        (marketStats.foreignBuyTotal + marketStats.foreignSellTotal)) *
                        100
                    )
                  : 50}
                %
              </span>
            </div>
          </div>
        </div>
      </Panel>

      {/* 2. BẢNG ĐIỆN TRỰC TUYẾN HOSE & HỆ THỐNG RỔ CỔ PHIẾU CHUYÊN NGHIỆP */}
      <Panel flush className="mt-4 overflow-hidden">
        {/* Thanh Rổ cổ phiếu thiết kế chuẩn nghiệp vụ (VN30, Toàn sàn, Danh mục theo dõi, Tâm điểm dòng tiền, Khối ngoại, Biên độ mạnh) */}
        <div className="px-4 py-2.5 border-b border-line bg-surface">
          <div className="flex flex-col xl:flex-row xl:items-center xl:justify-between gap-2">
            {/* Segmented Market Baskets — đồng bộ chiều cao h-8 (32px), căn giữa tuyệt đối trên 1 hàng */}
            <div
              className="inline-flex flex-wrap xl:flex-nowrap items-center min-h-8 p-[3px] gap-0.5 rounded-[7px] bg-soft border border-line"
              role="tablist"
              aria-label="Rổ cổ phiếu và bộ lọc chuyên sâu"
            >
              {(
                [
                  {
                    key: "VN30" as PresetTab,
                    label: "Rổ VN30",
                    badge: `${marketStats.vn30Count}`,
                    desc: "30 cổ phiếu vốn hóa & thanh khoản dẫn dắt sàn HOSE",
                  },
                  {
                    key: "ALL" as PresetTab,
                    label: "Toàn sàn HOSE",
                    badge: `${boardItems.length}`,
                    desc: "Toàn bộ cổ phiếu niêm yết trên sàn HOSE",
                  },
                  {
                    key: "WATCHLIST" as PresetTab,
                    label: "Theo dõi",
                    badge: `${starredSymbols.length}`,
                    desc: "Các mã cổ phiếu bạn đã ghim theo dõi cá nhân",
                  },
                  {
                    key: "CASHFLOW" as PresetTab,
                    label: "Tâm điểm dòng tiền",
                    badge: `${marketStats.cashflowFocusCount}`,
                    desc: "Cổ phiếu hút dòng tiền lớn (GTGD >= 30 tỷ ₫)",
                  },
                  {
                    key: "FOREIGN" as PresetTab,
                    label: "Khối ngoại",
                    badge: `${marketStats.foreignBuyCount + marketStats.foreignSellCount}`,
                    desc: "Cổ phiếu có giao dịch mua/bán ròng đáng chú ý của ĐTNN",
                  },
                  {
                    key: "BREAKOUT" as PresetTab,
                    label: "Biên độ ±3%",
                    badge: `${marketStats.breakoutCount}`,
                    desc: "Cổ phiếu biến động mạnh >= ±3% hoặc chạm Trần/Sàn",
                  },
                ] as const
              ).map((tab) => {
                const active = !bucketFilter && preset === tab.key
                return (
                  <button
                    key={tab.key}
                    type="button"
                    role="tab"
                    aria-selected={active}
                    title={tab.desc}
                    onClick={() => handleSelectPreset(tab.key)}
                    className={`h-[26px] px-2.5 rounded-[5px] text-[12px] font-medium inline-flex items-center justify-center gap-1.5 transition-all whitespace-nowrap shrink-0 ${
                      active
                        ? "bg-ink text-surface shadow-2xs"
                        : "text-secondary hover:text-ink hover:bg-surface/70"
                    }`}
                  >
                    <span>{tab.label}</span>
                    <span
                      className={`font-mono text-[11px] font-normal tnum ${
                        active ? "text-surface/75" : "text-muted"
                      }`}
                    >
                      {tab.badge}
                    </span>
                  </button>
                )
              })}
            </div>

            {/* Bộ công cụ Lọc ngành & Tìm kiếm nhanh (đồng bộ chiều cao h-8 = 32px) */}
            <div className="flex flex-wrap items-center gap-2 shrink-0">
              {/* Khi đang lọc theo cột Histogram biên độ */}
              {activeBucketMeta && (
                <div className="inline-flex items-center gap-1.5 h-8 px-2.5 rounded-[6px] bg-soft border border-ink/30 text-[12px]">
                  <span className="text-secondary">Phổ giá:</span>
                  <strong className={`font-mono ${activeBucketMeta.textClass}`}>
                    {activeBucketMeta.label} ({activeBucketMeta.shortDesc})
                  </strong>
                  <button
                    type="button"
                    onClick={() => setBucketFilter(null)}
                    className="ml-1 text-muted hover:text-ink"
                    aria-label="Xóa lọc biên độ"
                  >
                    ✕
                  </button>
                </div>
              )}

              {/* Bộ chuyển nhanh Mua ròng / Bán ròng khi ở tab Khối ngoại */}
              {!bucketFilter && preset === "FOREIGN" && (
                <div className="inline-flex items-center h-8 rounded-[6px] border border-line bg-soft/60 p-[2px] text-[11.5px]">
                  <button
                    type="button"
                    onClick={() => {
                      setForeignSide("BUY")
                      setSortField("foreign")
                      setSortDir("desc")
                      setPage(1)
                    }}
                    className={`h-[26px] px-2.5 rounded-[4px] inline-flex items-center justify-center font-medium transition-colors ${
                      foreignSide === "BUY"
                        ? "bg-surface text-gain shadow-2xs font-semibold"
                        : "text-secondary hover:text-ink"
                    }`}
                  >
                    Mua ròng ({marketStats.foreignBuyCount})
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setForeignSide("SELL")
                      setSortField("foreign")
                      setSortDir("asc")
                      setPage(1)
                    }}
                    className={`h-[26px] px-2.5 rounded-[4px] inline-flex items-center justify-center font-medium transition-colors ${
                      foreignSide === "SELL"
                        ? "bg-surface text-loss shadow-2xs font-semibold"
                        : "text-secondary hover:text-ink"
                    }`}
                  >
                    Bán ròng ({marketStats.foreignSellCount})
                  </button>
                </div>
              )}

              {/* Bộ chuyển nhanh Bứt phá tăng / Giảm sâu khi ở tab Biên độ mạnh */}
              {!bucketFilter && preset === "BREAKOUT" && (
                <div className="inline-flex items-center h-8 rounded-[6px] border border-line bg-soft/60 p-[2px] text-[11.5px]">
                  <button
                    type="button"
                    onClick={() => {
                      setBreakoutSide("ALL")
                      setSortField("changePct")
                      setSortDir("desc")
                      setPage(1)
                    }}
                    className={`h-[26px] px-2 rounded-[4px] inline-flex items-center justify-center font-medium transition-colors ${
                      breakoutSide === "ALL"
                        ? "bg-surface text-ink shadow-2xs font-semibold"
                        : "text-secondary hover:text-ink"
                    }`}
                  >
                    Tất cả ({marketStats.breakoutCount})
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setBreakoutSide("UP")
                      setSortField("changePct")
                      setSortDir("desc")
                      setPage(1)
                    }}
                    className={`h-[26px] px-2 rounded-[4px] inline-flex items-center justify-center font-medium transition-colors ${
                      breakoutSide === "UP"
                        ? "bg-surface text-gain shadow-2xs font-semibold"
                        : "text-secondary hover:text-ink"
                    }`}
                  >
                    Tăng &ge;3% ({marketStats.breakoutUpCount})
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setBreakoutSide("DOWN")
                      setSortField("changePct")
                      setSortDir("asc")
                      setPage(1)
                    }}
                    className={`h-[26px] px-2 rounded-[4px] inline-flex items-center justify-center font-medium transition-colors ${
                      breakoutSide === "DOWN"
                        ? "bg-surface text-loss shadow-2xs font-semibold"
                        : "text-secondary hover:text-ink"
                    }`}
                  >
                    Giảm &le;-3% ({marketStats.breakoutDownCount})
                  </button>
                </div>
              )}

              {/* Ô thêm nhanh mã vào Danh mục theo dõi khi đang mở tab WATCHLIST */}
              {!bucketFilter && preset === "WATCHLIST" && (
                <form onSubmit={handleQuickAddWatchlist} className="inline-flex items-center gap-1">
                  <input
                    type="text"
                    value={quickAddSymbol}
                    onChange={(e) => setQuickAddSymbol(e.target.value)}
                    placeholder="+ Thêm mã (VD: HPG, VCB)…"
                    aria-label="Thêm nhanh mã cổ phiếu vào danh mục theo dõi"
                    className="h-8 w-40 rounded-[6px] border border-line bg-soft/50 px-2.5 font-mono text-[11.5px] uppercase text-ink placeholder:normal-case placeholder:font-sans placeholder:text-muted outline-none focus:border-ink focus:bg-surface"
                  />
                  {quickAddSymbol.trim() && (
                    <Button type="submit" variant="primary" className="!h-8 !px-2.5 !text-[11.5px]">
                      Thêm
                    </Button>
                  )}
                </form>
              )}

              <CustomSelect
                value={sectorFilter}
                onChange={(val) => {
                  setSectorFilter(val)
                  setPage(1)
                }}
                options={sectorOptions}
                ariaLabel="Lọc theo nhóm ngành"
                className="w-40"
                align="right"
              />

              <div className="relative flex-1 sm:flex-initial sm:w-48">
                <input
                  type="text"
                  value={search}
                  onChange={(e) => {
                    setSearch(e.target.value)
                    setPage(1)
                  }}
                  placeholder="Tìm mã CP, tên công ty…"
                  aria-label="Tìm mã cổ phiếu hoặc tên doanh nghiệp"
                  className="h-8 w-full rounded-[6px] border border-line bg-surface pl-3 pr-7 text-[12px] text-ink placeholder:text-muted outline-none focus:border-ink transition-colors"
                />
                {search && (
                  <button
                    type="button"
                    onClick={() => {
                      setSearch("")
                      setPage(1)
                    }}
                    aria-label="Xóa từ khóa tìm kiếm"
                    className="absolute right-2 top-1/2 -translate-y-1/2 text-[11px] text-muted hover:text-ink"
                  >
                    ✕
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>

        {/* Bảng điện 2 tầng tiêu đề — không cuộn lồng, chỉ render trang hiện tại (30 mã mặc định) */}
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1020px] border-separate border-spacing-0 text-[12.5px]">
            <thead>
              <tr className="text-[10.5px] font-semibold uppercase tracking-wider text-muted">
                <th
                  rowSpan={2}
                  onClick={() => handleSort("symbol")}
                  className="sticky left-0 z-10 h-[56px] w-[132px] cursor-pointer select-none border-b border-r border-line bg-soft/65 pl-4 pr-3 text-left align-middle hover:text-ink transition-colors whitespace-nowrap"
                >
                  Mã CK{sortIcon("symbol")}
                </th>
                <th
                  colSpan={3}
                  className="h-[28px] border-b border-r border-line bg-soft/65 px-2 text-center align-middle whitespace-nowrap"
                >
                  Giá chuẩn
                </th>
                <th
                  colSpan={2}
                  className="h-[28px] border-b border-r border-line bg-soft/65 px-2 text-center align-middle whitespace-nowrap"
                >
                  Dư mua
                </th>
                <th
                  colSpan={3}
                  className="h-[28px] border-b border-r border-line bg-raised/75 px-2 text-center text-ink align-middle whitespace-nowrap"
                >
                  Khớp lệnh
                </th>
                <th
                  colSpan={2}
                  className="h-[28px] border-b border-r border-line bg-soft/65 px-2 text-center align-middle whitespace-nowrap"
                >
                  Dư bán
                </th>
                <th
                  colSpan={3}
                  className="h-[28px] border-b border-line bg-soft/65 px-2 text-center align-middle whitespace-nowrap"
                >
                  Thanh khoản & ĐTNN
                </th>
              </tr>

              <tr className="text-[10.5px] font-medium uppercase tracking-wide text-secondary">
                <th className="h-[28px] border-b border-line bg-soft/65 px-2.5 text-right text-ref whitespace-nowrap">
                  TC
                </th>
                <th className="h-[28px] border-b border-line bg-soft/65 px-2.5 text-right text-ceiling whitespace-nowrap">
                  Trần
                </th>
                <th className="h-[28px] border-b border-r border-line bg-soft/65 px-2.5 text-right text-floor whitespace-nowrap">
                  Sàn
                </th>

                <th
                  onClick={() => handleSort("bidPrice")}
                  className="h-[28px] cursor-pointer select-none border-b border-line bg-soft/65 px-2.5 text-right hover:text-ink transition-colors whitespace-nowrap"
                >
                  Giá 1{sortIcon("bidPrice")}
                </th>
                <th className="h-[28px] border-b border-r border-line bg-soft/65 px-2.5 text-right whitespace-nowrap">
                  KL 1
                </th>

                <th
                  onClick={() => handleSort("price")}
                  className="h-[28px] cursor-pointer select-none border-b border-line bg-raised/75 px-2.5 text-right font-semibold text-ink hover:text-mineral transition-colors whitespace-nowrap"
                >
                  Giá{sortIcon("price")}
                </th>
                <th
                  onClick={() => handleSort("priceDiff")}
                  className="h-[28px] cursor-pointer select-none border-b border-line bg-raised/75 px-2.5 text-right font-semibold text-ink hover:text-mineral transition-colors whitespace-nowrap"
                >
                  +/-{sortIcon("priceDiff")}
                </th>
                <th
                  onClick={() => handleSort("changePct")}
                  className="h-[28px] cursor-pointer select-none border-b border-r border-line bg-raised/75 px-2.5 text-right font-semibold text-ink hover:text-mineral transition-colors whitespace-nowrap"
                >
                  %{sortIcon("changePct")}
                </th>

                <th
                  onClick={() => handleSort("askPrice")}
                  className="h-[28px] cursor-pointer select-none border-b border-line bg-soft/65 px-2.5 text-right hover:text-ink transition-colors whitespace-nowrap"
                >
                  Giá 1{sortIcon("askPrice")}
                </th>
                <th className="h-[28px] border-b border-r border-line bg-soft/65 px-2.5 text-right whitespace-nowrap">
                  KL 1
                </th>

                <th
                  onClick={() => handleSort("volume")}
                  className="h-[28px] cursor-pointer select-none border-b border-line bg-soft/65 px-3 text-right hover:text-ink transition-colors whitespace-nowrap"
                >
                  Tổng KL{sortIcon("volume")}
                </th>
                <th
                  onClick={() => handleSort("valueBillion")}
                  className="h-[28px] cursor-pointer select-none border-b border-line bg-soft/65 px-3 text-right hover:text-ink transition-colors whitespace-nowrap"
                  title="Giá trị giao dịch khớp lệnh (tỷ VNĐ)"
                >
                  GTGD (tỷ){sortIcon("valueBillion")}
                </th>
                <th
                  onClick={() => handleSort("foreign")}
                  className="h-[28px] cursor-pointer select-none border-b border-line bg-soft/65 pr-4 pl-3 text-right hover:text-ink transition-colors whitespace-nowrap"
                  title="Giá trị mua/bán ròng của Nhà đầu tư nước ngoài (tỷ VNĐ)"
                >
                  ĐTNN (tỷ){sortIcon("foreign")}
                </th>
              </tr>
            </thead>

            <tbody>
              {pagedItems.length === 0 ? (
                <tr>
                  <td colSpan={14} className="px-6 py-14 text-center text-[13px] text-secondary">
                    {resource.loading ? (
                      "Đang tải dữ liệu bảng giá trực tuyến sàn HOSE…"
                    ) : preset === "WATCHLIST" && starredSymbols.length === 0 ? (
                      <div className="space-y-3 max-w-md mx-auto">
                        <p className="font-medium text-ink">Danh mục theo dõi đang trống</p>
                        <p className="text-muted text-[12px]">
                          Bấm vào ngôi sao (☆) cạnh mã cổ phiếu bất kỳ hoặc chọn nhanh rổ mẫu dưới đây để bắt đầu theo dõi:
                        </p>
                        <div className="flex flex-wrap items-center justify-center gap-2 pt-1">
                          <Button
                            variant="primary"
                            onClick={() =>
                              persistWatchlist(["FPT", "VCB", "HPG", "SSI", "TCB", "MBB", "MWG"])
                            }
                          >
                            + Thêm nhanh 7 mã đầu ngành (FPT, VCB, HPG, SSI…)
                          </Button>
                          <Button variant="secondary" onClick={() => handleSelectPreset("VN30")}>
                            Xem rổ VN30
                          </Button>
                        </div>
                      </div>
                    ) : (
                      <div className="space-y-2">
                        <p>Không có mã cổ phiếu phù hợp với điều kiện lọc hiện tại.</p>
                        <Button
                          variant="secondary"
                          onClick={() => {
                            setPreset("VN30")
                            setBucketFilter(null)
                            setSectorFilter("ALL")
                            setSearch("")
                            setPage(1)
                          }}
                        >
                          Đặt lại bộ lọc
                        </Button>
                      </div>
                    )}
                  </td>
                </tr>
              ) : (
                pagedItems.map((item) => (
                  <MarketBoardRow
                    key={item.symbol}
                    item={item}
                    book={orderbooks[item.symbol]}
                    starred={starredSet.has(item.symbol)}
                    flash={flashMap[item.symbol]}
                    onToggleStar={handleToggleStar}
                    onOpenDetail={setActiveSymbol}
                  />
                ))
              )}
            </tbody>
          </table>
        </div>

        {/* Chân trang & Điều khiển số dòng hiển thị tối ưu hiệu năng */}
        <div className="px-4 py-3 border-t border-line bg-surface flex flex-wrap items-center justify-between gap-3 text-[12px]">
          <div className="flex flex-wrap items-center gap-3 text-muted">
            <span className="font-mono tnum">
              Hiển thị{" "}
              <strong className="text-ink font-semibold">
                {filteredItems.length === 0 ? 0 : startIdx + 1}–
                {Math.min(startIdx + pageSize, filteredItems.length)}
              </strong>{" "}
              / {filteredItems.length} mã
            </span>

            <span className="text-line-strong">·</span>

            <div className="inline-flex items-center gap-1">
              <span>Mỗi trang:</span>
              {PAGE_SIZE_OPTIONS.map((sz) => (
                <button
                  key={sz}
                  type="button"
                  onClick={() => {
                    setPageSize(sz)
                    setPage(1)
                  }}
                  className={`h-6 px-2 rounded-[5px] font-mono text-[11.5px] tnum transition-colors ${
                    pageSize === sz
                      ? "bg-soft text-ink font-semibold border border-line-strong"
                      : "text-muted hover:text-ink"
                  }`}
                >
                  {sz}
                </button>
              ))}
            </div>
          </div>

          {totalPages > 1 && (
            <div className="flex items-center gap-2">
              <span className="font-mono text-[11.5px] text-muted tnum mr-1">
                Trang {safePage} / {totalPages}
              </span>
              <Button
                variant="secondary"
                className="!h-7 !px-2.5 !text-[12px]"
                disabled={safePage <= 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
              >
                ← Trước
              </Button>
              <Button
                variant="secondary"
                className="!h-7 !px-2.5 !text-[12px]"
                disabled={safePage >= totalPages}
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              >
                Tiếp →
              </Button>
            </div>
          )}
        </div>
      </Panel>

      {/* POPUP CHI TIẾT CỔ PHIẾU CHUẨN CTCK (Bấm vào bất kỳ mã nào trên bảng giá / khối ngoại) */}
      <StockDetailModal
        open={Boolean(activeSymbol)}
        symbol={activeSymbol || "SSI"}
        seed={activeStockNav.item ?? undefined}
        starred={activeSymbol ? starredSet.has(activeSymbol) : false}
        onToggleStar={handleToggleStar}
        onClose={() => setActiveSymbol(null)}
        prevSymbol={activeStockNav.prevSymbol}
        nextSymbol={activeStockNav.nextSymbol}
        onSelectPrev={
          activeStockNav.prevSymbol
            ? () => setActiveSymbol(activeStockNav.prevSymbol)
            : undefined
        }
        onSelectNext={
          activeStockNav.nextSymbol
            ? () => setActiveSymbol(activeStockNav.nextSymbol)
            : undefined
        }
      />
    </Page>
  )
}
