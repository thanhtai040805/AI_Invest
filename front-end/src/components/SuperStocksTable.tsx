"use client"

import { useEffect, useMemo, useState } from "react"
import { Button, CustomSelect, Panel, Pill, Tabs, type SelectOption } from "@/components/ui"
import { Link } from "@/lib/router"
import { formatSectorName } from "@/lib/sector-weights"
import { useRealtimeMarket } from "@/lib/use-realtime"
import type { ApiMarketStock } from "@/types"

export interface SuperStockItem {
  code: string
  symbol: string
  name: string
  sector: string
  close: number
  price: number
  changePct: number
  avg_buyforce: number
  ru_nen: boolean
  ru_nen_meta?: string | null
  test_cung: boolean
  test_cung_meta?: string | null
  dong_tien: boolean
  dong_tien_meta?: string | null
  tien_ap_dao: number
  max_buy: number
  max_buy_excl?: number
  mo_mua: boolean
  predict_vol: number
  vol_ma10?: number
  vol_ma10_excl?: number
  vol_ma20?: number
  trend_up: boolean
  average: number
  surge_pct_excl?: number
  tao_lap_surge?: boolean
  freq?: string
  avg_profit?: number
  rsi?: number
  mfi?: number
  vsa_score?: number
  bottom_score?: number
}

export interface SuperStocksPayload {
  items?: SuperStockItem[]
  total?: number
  asOf?: string | null
  quoteSource?: string
  liveQuoteCount?: number
}

export function formatCompactVol(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "—"
  if (n >= 1_000_000) {
    return `${Number((n / 1_000_000).toFixed(2))}M`
  }
  if (n >= 1_000) {
    return `${Number((n / 1_000).toFixed(1))}k`
  }
  return n.toLocaleString("vi-VN")
}

/**
 * Tooltip giải thích tiêu đề cột định lượng VSA
 */
function VsaHeaderTip({
  label,
  title,
  formula,
  detail,
}: {
  label: string
  title: string
  formula: string
  detail: string
}) {
  return (
    <div className="group/htip relative inline-flex items-center justify-center gap-1 cursor-help">
      <span>{label}</span>
      <span
        aria-hidden
        className="inline-flex h-3.5 w-3.5 items-center justify-center rounded-full border border-line-strong font-mono text-[9px] font-semibold text-muted transition-colors group-hover/htip:border-ink group-hover/htip:bg-ink group-hover/htip:text-paper"
      >
        ?
      </span>
      <div
        role="tooltip"
        className="pointer-events-none invisible opacity-0 group-hover/htip:visible group-hover/htip:opacity-100 transition-opacity duration-150 absolute top-full left-1/2 -translate-x-1/2 mt-1.5 z-30 w-[260px] rounded-[8px] border border-line-strong bg-surface p-3 text-left normal-case tracking-normal shadow-[0_10px_28px_rgba(24,32,29,0.14)]"
      >
        <div className="text-[11.5px] font-semibold text-ink">{title}</div>
        <div className="mt-1 inline-block rounded-[4px] bg-soft px-1.5 py-0.5 font-mono text-[10px] font-medium text-mineral">
          {formula}
        </div>
        <p className="mt-1.5 text-[11px] leading-relaxed text-secondary font-normal">
          {detail}
        </p>
      </div>
    </div>
  )
}

/**
 * Ô hiển thị tín hiệu VSA theo chuẩn Koyfin / TradingView Quant:
 * Thay vì lặp chữ "Đạt", hiển thị thẳng thông số vi cấu trúc của nến kích hoạt
 * (VD: "Rút 62% · T-1", "48% MA · T-0", "1.8x · MFI 68") kèm tooltip giải thích trực quan
 */
function VsaMetricCell({
  ok,
  meta,
  fallbackLabel,
  tone = "teal",
  kind,
  placeBelow = false,
}: {
  ok: boolean
  meta?: string | null
  fallbackLabel: string
  tone?: "teal" | "mineral" | "gain"
  kind?: "ru_nen" | "test_cung" | "dong_tien"
  placeBelow?: boolean
}) {
  if (!ok) {
    return (
      <span className="font-mono text-[11px] text-line-strong select-none" aria-label="Chưa kích hoạt">
        ·
      </span>
    )
  }

  const text = meta || fallbackLabel
  const parts = text.split(" · ")
  const primary = parts[0]
  const secondary = parts[1]
  const wickVal = primary.replace("Rút ", "")
  const maVal = primary.replace(" MA", "")
  const lagText =
    secondary === "T-0"
      ? "phiên hôm nay (T-0)"
      : secondary === "T-1"
        ? "1 phiên trước (T-1)"
        : secondary === "T-2"
          ? "2 phiên trước (T-2)"
          : secondary ? `${secondary.replace("T-", "")} phiên trước (${secondary})` : "10 phiên gần nhất"

  return (
    <div className="group/cell relative inline-flex items-center justify-center">
      <div
        className="inline-flex items-center gap-1.5 rounded-[5px] border px-2 py-0.5 font-mono text-[11px] whitespace-nowrap tnum cursor-help transition-colors"
        style={{
          borderColor:
            tone === "gain"
              ? "rgba(27, 122, 88, 0.24)"
              : tone === "mineral"
                ? "rgba(31, 69, 61, 0.22)"
                : "rgba(31, 107, 99, 0.24)",
          backgroundColor:
            tone === "gain"
              ? "rgba(27, 122, 88, 0.07)"
              : tone === "mineral"
                ? "rgba(31, 69, 61, 0.06)"
                : "rgba(31, 107, 99, 0.07)",
        }}
      >
        <span
          className={`font-semibold ${
            tone === "gain" ? "text-gain" : tone === "mineral" ? "text-mineral" : "text-teal"
          }`}
        >
          {primary}
        </span>
        {secondary && (
          <>
            <span className="text-line-strong">·</span>
            <span className="text-[10px] text-secondary">{secondary}</span>
          </>
        )}
      </div>

      {kind && (
        <div
          role="tooltip"
          className={`pointer-events-none invisible opacity-0 group-hover/cell:visible group-hover/cell:opacity-100 transition-opacity duration-150 absolute left-1/2 -translate-x-1/2 z-30 w-[245px] rounded-[8px] border border-line-strong bg-surface p-2.5 text-left normal-case tracking-normal shadow-[0_10px_28px_rgba(24,32,29,0.14)] ${
            placeBelow ? "top-full mt-1.5" : "bottom-full mb-1.5"
          }`}
        >
          {kind === "ru_nen" && (
            <>
              <div className="text-[11px] font-semibold text-teal">
                Rũ nến (Shakeout · {lagText})
              </div>
              <p className="mt-1 text-[10.5px] leading-relaxed text-secondary">
                Bóng nến dưới rút chân dài bằng <strong className="font-mono text-ink">{wickVal}</strong> tổng biên độ cây nến <span className="font-mono text-[10px]">(min(O,C)-L)/(H-L)</span>, quét thủng đáy hỗ trợ 5–10 phiên rồi kéo đóng cửa lên ≥60% biên độ.
              </p>
            </>
          )}
          {kind === "test_cung" && (
            <>
              <div className="text-[11px] font-semibold text-mineral">
                Test cung (No Supply · {lagText})
              </div>
              <p className="mt-1 text-[10.5px] leading-relaxed text-secondary">
                Khối lượng khớp phiên test chỉ bằng <strong className="font-mono text-ink">{maVal}</strong> trung bình 10 phiên (ngưỡng cạn cung ≤68% MA10) với biên độ hẹp ≤1.85% và rút chân đỡ giá ≥40%. % càng thấp càng cạn cung.
              </p>
            </>
          )}
          {kind === "dong_tien" && (
            <>
              <div className="text-[11px] font-semibold text-gain">
                Dòng tiền lớn (Active Flow)
              </div>
              <p className="mt-1 text-[10.5px] leading-relaxed text-secondary">
                Khối lượng phiên Pocket Pivot (trong nền 10 phiên) gấp <strong className="font-mono text-ink">{primary}</strong> lần MA10 (ngưỡng ≥1.45x MA10), đi kèm <strong className="font-mono text-ink">{secondary}</strong> xác nhận dòng tiền tổ chức.
              </p>
            </>
          )}
        </div>
      )}
    </div>
  )
}

/**
 * 3-Segment Micro-Matrix hiển thị ngay lập tức cổ phiếu đạt mấy trụ cột VSA (3/3, 2/3, 1/3)
 */
function VsaConfluenceBar({
  ruNen,
  testCung,
  dongTien,
}: {
  ruNen: boolean
  testCung: boolean
  dongTien: boolean
}) {
  const count = (ruNen ? 1 : 0) + (testCung ? 1 : 0) + (dongTien ? 1 : 0)
  return (
    <span
      title={`Hội tụ ${count}/3 tín hiệu VSA (Rũ nến · Test cung · Dòng tiền)`}
      className="inline-flex items-center gap-0.5 ml-1 align-middle"
    >
      <i
        className={`h-2 w-1.5 rounded-[1.5px] ${
          ruNen ? "bg-teal" : "bg-line"
        }`}
      />
      <i
        className={`h-2 w-1.5 rounded-[1.5px] ${
          testCung ? "bg-mineral" : "bg-line"
        }`}
      />
      <i
        className={`h-2 w-1.5 rounded-[1.5px] ${
          dongTien ? "bg-gain" : "bg-line"
        }`}
      />
    </span>
  )
}

function BuyforceCell({ value }: { value: number }) {
  const clamped = Math.min(100, Math.max(0, value))
  const barColor =
    clamped >= 60 ? "bg-teal" : clamped >= 48 ? "bg-mineral" : "bg-muted/60"

  return (
    <div className="flex items-center gap-2.5 min-w-[120px]">
      <span className="w-9 text-right font-mono text-[12px] font-medium text-ink tnum">
        {value}%
      </span>
      <div className="flex-1 h-1.5 rounded-full bg-soft overflow-hidden">
        <div
          className={`h-full rounded-full ${barColor}`}
          style={{ width: `${clamped}%` }}
        />
      </div>
    </div>
  )
}

function ExpectedBuyBadge({ moMua, close }: { moMua: boolean; close: number }) {
  const formatted = close.toLocaleString("vi-VN", {
    minimumFractionDigits: 1,
    maximumFractionDigits: 2,
  })
  return moMua ? (
    <span className="inline-flex items-center gap-1.5 rounded-[5px] border border-mineral/30 bg-teal/10 px-2.5 py-0.5 font-mono text-[11.5px] font-semibold text-mineral whitespace-nowrap tnum">
      <span className="h-1.5 w-1.5 rounded-full bg-teal" aria-hidden />
      Mở mua · {formatted}
    </span>
  ) : (
    <span className="inline-block rounded-[5px] border border-line bg-soft/70 px-2.5 py-0.5 font-mono text-[11.5px] text-secondary whitespace-nowrap tnum">
      Theo dõi · {formatted}
    </span>
  )
}

function ExpectedVolCell({
  vol,
  trendUp,
  average,
}: {
  vol: number
  trendUp: boolean
  average: number
}) {
  return (
    <div className="inline-flex items-center justify-end gap-2 font-mono text-[12px] whitespace-nowrap tnum">
      <span title={vol.toLocaleString("vi-VN")} className="text-ink">
        {formatCompactVol(vol)}
      </span>
      <span
        className={`w-12 text-right text-[11px] font-medium ${
          average >= 0 && trendUp
            ? "text-gain"
            : average < 0
              ? "text-loss"
              : "text-secondary"
        }`}
      >
        {average >= 0 ? "+" : ""}
        {average}%
      </span>
    </div>
  )
}

function Intraday15mCountdown() {
  const [now, setNow] = useState<Date | null>(null)
  useEffect(() => {
    setNow(new Date())
    const id = window.setInterval(() => setNow(new Date()), 1000)
    return () => window.clearInterval(id)
  }, [])
  if (!now) return null
  const minutes = now.getHours() * 60 + now.getMinutes()
  const seconds = minutes * 60 + now.getSeconds()
  const startSec = 9 * 3600
  const endSec = (14 * 60 + 45) * 60
  if (seconds < startSec || seconds >= endSec) return null
  const elapsed = seconds - startSec
  const rem = 900 - (elapsed % 900)
  const mm = Math.floor(rem / 60)
  const ss = rem % 60
  return (
    <Pill tone="gold">
      Chu kỳ 15p · {String(mm).padStart(2, "0")}:{String(ss).padStart(2, "0")}
    </Pill>
  )
}

export function SuperStocksSpotlight({
  data,
  loading,
}: {
  data?: SuperStocksPayload | null
  loading?: boolean
}) {
  return <SuperStocksTable data={data} loading={loading} showVolumeSurge />
}

const STRATEGY_TABS = [
  "Danh mục Siêu phẩm (Tích lũy & Dòng tiền)",
  "Danh mục Trading (Bắt đáy & Đảo chiều)",
]

const SIGNAL_FILTER_OPTIONS: SelectOption[] = [
  { value: "all", label: "Tất cả tín hiệu VSA" },
  { value: "mo_mua", label: "Đang có điểm Mở mua" },
  { value: "tao_lap", label: "Tạo lập đẩy giá (Đột biến KL dự kiến)" },
  { value: "full_combo", label: "Hội tụ đủ 3 tín hiệu (Rũ nến · Test cung · Dòng tiền)" },
  { value: "ru_nen", label: "Có Rũ nến (Wyckoff Spring 10P)" },
  { value: "test_cung", label: "Có Test cung (No Supply ≤68% MA10)" },
  { value: "dong_tien", label: "Có Dòng tiền (Pocket Pivot ≥1.45x MA10)" },
]

/**
 * Bảng định lượng VSA & Dòng tiền theo chuẩn Editorial Quant của AIInvest.
 * Tự động trộn dữ liệu khớp lệnh thời gian thực từ WebSocket (`market:snapshot`) khi chạy trên PROD.
 */
export function SuperStocksTable({
  data,
  loading,
  initialSector = "",
  showVolumeSurge = false,
  liveStocks,
}: {
  data?: SuperStocksPayload | null
  loading?: boolean
  initialSector?: string
  showVolumeSurge?: boolean
  liveStocks?: ApiMarketStock[]
}) {
  const { snapshot: wsSnapshot, isLive: wsLive } = useRealtimeMarket()
  const [strategyTab, setStrategyTab] = useState<string>(STRATEGY_TABS[0])
  const strategy = strategyTab === STRATEGY_TABS[1] ? "trading" : "sieu_pham"
  const [quickFilter, setQuickFilter] = useState<string>("all")
  const [sectorFilter, setSectorFilter] = useState<string>(initialSector)
  const [searchQuery, setSearchQuery] = useState<string>("")
  const [page, setPage] = useState<number>(1)
  const [expandSurges, setExpandSurges] = useState<boolean>(false)
  const pageSize = 15

  useEffect(() => {
    if (initialSector) setSectorFilter(initialSector)
  }, [initialSector])

  // Merge live intraday ticks from WebSocket snapshot if available in PROD
  const allItems = useMemo(() => {
    const base = Array.isArray(data?.items) ? data!.items! : []
    const streamList = liveStocks ?? wsSnapshot?.stocks
    if (!streamList || !streamList.length) return base

    const bySymbol = new Map<string, ApiMarketStock>()
    for (const st of streamList) {
      const sym = String(st.symbol ?? "").toUpperCase()
      if (sym) bySymbol.set(sym, st)
    }

    const now = new Date()
    const vnMin = now.getHours() * 60 + now.getMinutes()
    const isTrading = vnMin >= 9 * 60 && vnMin <= 14 * 60 + 45
    const elapsedMin = !isTrading
      ? 255
      : vnMin <= 11 * 60 + 30
        ? Math.max(15, vnMin - 9 * 60)
        : vnMin < 13 * 60
          ? 150
          : Math.min(255, 150 + (vnMin - 13 * 60))
    const projFactor = isTrading ? 255 / Math.max(15, elapsedMin) : 1

    return base.map((item) => {
      const live = bySymbol.get(item.code.toUpperCase())
      if (!live) return item
      const rawPrice = Number(live.price ?? 0)
      const closeK =
        rawPrice > 500
          ? Number((rawPrice / 1000).toFixed(2))
          : rawPrice > 0
            ? Number(rawPrice.toFixed(2))
            : item.close
      const rawChg = live.changePercent ?? live.change_pct
      const changePct =
        rawChg != null && Number.isFinite(Number(rawChg))
          ? Number(Number(rawChg).toFixed(2))
          : item.changePct
      const rawVol = Number(live.volume ?? 0)
      const predictVol =
        rawVol > 0 ? Math.round(rawVol * projFactor) : item.predict_vol
      const volMa10 = Math.max(1, item.vol_ma10 ?? item.vol_ma20 ?? 1)
      const volMa10Excl = Math.max(1, item.vol_ma10_excl ?? volMa10)
      const predRounded = predictVol >= 1_000_000 ? Math.round(predictVol / 1000) * 1000 : predictVol
      const average = Math.round(((predRounded - volMa10) / volMa10) * 100)
      const surgePctExcl = Math.round(((predRounded - volMa10Excl) / volMa10Excl) * 100)
      return {
        ...item,
        close: closeK,
        changePct,
        predict_vol: predictVol,
        average,
        surge_pct_excl: surgePctExcl,
        tao_lap_surge: changePct > 0 && volMa10Excl >= 500_000 && surgePctExcl >= 160,
        trend_up: predRounded >= volMa10,
      }
    })
  }, [data, liveStocks, wsSnapshot?.stocks])

  const allVolumeSurges = useMemo(
    () =>
      [...allItems]
        .filter((it) => {
          const surge = it.surge_pct_excl ?? it.average
          if (it.tao_lap_surge) return true
          return it.trend_up && surge >= 25 && it.tien_ap_dao >= 50 && it.changePct > 0
        })
        .sort((a, b) => {
          if (Boolean(b.tao_lap_surge) !== Boolean(a.tao_lap_surge)) {
            return b.tao_lap_surge ? 1 : -1
          }
          return (b.surge_pct_excl ?? b.average) - (a.surge_pct_excl ?? a.average)
        }),
    [allItems],
  )

  const displayedSurges = useMemo(
    () => (expandSurges ? allVolumeSurges : allVolumeSurges.slice(0, 12)),
    [allVolumeSurges, expandSurges],
  )

  const sectorOptions = useMemo<SelectOption[]>(() => {
    const counts = new Map<string, number>()
    allItems.forEach((it) => {
      if (it.sector) counts.set(it.sector, (counts.get(it.sector) ?? 0) + 1)
    })
    const sorted = Array.from(counts.entries()).sort((a, b) =>
      formatSectorName(a[0]).localeCompare(formatSectorName(b[0]), "vi"),
    )
    return [
      { value: "", label: `Tất cả ngành (${sorted.length})`, meta: `${allItems.length}` },
      ...sorted.map(([sec, count]) => ({
        value: sec,
        label: formatSectorName(sec),
        meta: `${count}`,
      })),
    ]
  }, [allItems])

  const stats = useMemo(() => {
    const moMuaCount = allItems.filter((i) => i.mo_mua).length
    const comboCount = allItems.filter((i) => i.ru_nen && i.test_cung && i.dong_tien).length
    const ruNenCount = allItems.filter((i) => i.ru_nen).length
    const testCungCount = allItems.filter((i) => i.test_cung).length
    const dongTienCount = allItems.filter((i) => i.dong_tien).length
    const taoLapCount = allVolumeSurges.length
    return { moMuaCount, comboCount, ruNenCount, testCungCount, dongTienCount, taoLapCount }
  }, [allItems, allVolumeSurges.length])

  const signalOptionsWithCounts = useMemo<SelectOption[]>(() => {
    const countMap: Record<string, number> = {
      all: allItems.length,
      mo_mua: stats.moMuaCount,
      tao_lap: stats.taoLapCount,
      full_combo: stats.comboCount,
      ru_nen: stats.ruNenCount,
      test_cung: stats.testCungCount,
      dong_tien: stats.dongTienCount,
    }
    return SIGNAL_FILTER_OPTIONS.map((opt) => ({
      ...opt,
      meta: `${countMap[opt.value] ?? 0} mã`,
    }))
  }, [allItems.length, stats])

  const filteredItems = useMemo(() => {
    const q = searchQuery.trim().toUpperCase()
    const list = allItems.filter((item) => {
      if (sectorFilter && item.sector !== sectorFilter) return false
      if (q && !item.code.toUpperCase().includes(q) && !item.name.toUpperCase().includes(q)) return false
      if (quickFilter === "mo_mua" && !item.mo_mua) return false
      if (quickFilter === "tao_lap" && !(item.tao_lap_surge || (item.trend_up && (item.surge_pct_excl ?? item.average) >= 25 && item.tien_ap_dao >= 50 && item.changePct > 0))) return false
      if (quickFilter === "ru_nen" && !item.ru_nen) return false
      if (quickFilter === "test_cung" && !item.test_cung) return false
      if (quickFilter === "dong_tien" && !item.dong_tien) return false
      if (quickFilter === "full_combo" && !(item.ru_nen && item.test_cung && item.dong_tien)) return false
      return true
    })

    if (quickFilter === "tao_lap") {
      return [...list].sort((a, b) => (b.surge_pct_excl ?? b.average) - (a.surge_pct_excl ?? a.average))
    }
    if (strategy === "trading") {
      return [...list].sort((a, b) => (b.bottom_score ?? 0) - (a.bottom_score ?? 0))
    }
    return [...list].sort((a, b) => (b.vsa_score ?? 0) - (a.vsa_score ?? 0))
  }, [allItems, sectorFilter, searchQuery, quickFilter, strategy])

  const totalPages = Math.max(1, Math.ceil(filteredItems.length / pageSize))
  const currentPage = Math.min(page, totalPages)
  const pagedItems = filteredItems.slice((currentPage - 1) * pageSize, currentPage * pageSize)

  const isRealtimeActive = wsLive || data?.quoteSource === "redis-dnse-live"

  return (
    <div className="space-y-4">
      {showVolumeSurge && displayedSurges.length > 0 && (
        <Panel flush>
          <div className="px-5 pt-3.5 pb-3 border-b border-line flex flex-wrap items-center justify-between gap-2">
            <div className="flex flex-wrap items-center gap-2.5">
              <h3 className="text-[14px] font-semibold text-ink tracking-tight">
                Tạo lập đẩy giá · Đột biến khối lượng khớp dự kiến
              </h3>
              <span className="rounded-[4px] bg-soft px-2 py-0.5 font-mono text-[11px] font-medium text-secondary tnum">
                Hiển thị {displayedSurges.length} / {allVolumeSurges.length} mã đạt chuẩn
              </span>
            </div>
            <div className="flex flex-wrap items-center gap-2.5">
              <Intraday15mCountdown />
              <button
                type="button"
                onClick={() => {
                  setQuickFilter(quickFilter === "tao_lap" ? "all" : "tao_lap")
                  setPage(1)
                }}
                className={`h-6 px-2 rounded-[4px] border font-mono text-[11px] font-medium transition-colors ${
                  quickFilter === "tao_lap"
                    ? "border-ink bg-ink text-paper"
                    : "border-line bg-surface text-secondary hover:border-line-strong hover:text-ink"
                }`}
              >
                {quickFilter === "tao_lap"
                  ? "Đang lọc 54 mã dưới bảng ✓"
                  : `Đưa cả ${allVolumeSurges.length} mã xuống bảng chi tiết ↓`}
              </button>
              {allVolumeSurges.length > 12 && (
                <button
                  type="button"
                  onClick={() => setExpandSurges((v) => !v)}
                  className="font-mono text-[11px] font-medium text-mineral hover:underline"
                >
                  {expandSurges ? "Thu gọn (12 mã)" : `Mở toàn bộ (${allVolumeSurges.length} mã)`}
                </button>
              )}
            </div>
          </div>

          {/* Khóa cứng chiều cao tối đa + grid cố định 6 cột để dù 100 mã cũng không bao giờ biến dạng layout */}
          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-6 divide-x divide-y divide-line max-h-[264px] overflow-y-auto">
            {displayedSurges.map((item) => (
              <Link
                key={item.code}
                to={`/stock/${encodeURIComponent(item.code)}`}
                className="px-3.5 py-2.5 hover:bg-soft/60 transition-colors flex items-center justify-between gap-2 min-w-0"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    <span className="font-mono text-[13px] font-semibold text-ink">
                      {item.code}
                    </span>
                    <span className="font-mono text-[10.5px] text-gain tnum">
                      +{item.changePct.toFixed(1)}%
                    </span>
                  </div>
                  <div className="text-[10.5px] text-muted truncate mt-0.5">
                    {formatSectorName(item.sector)}
                  </div>
                </div>
                <div className="text-right shrink-0">
                  <div className="font-mono text-[12px] font-semibold text-gain tnum whitespace-nowrap">
                    +{(item.surge_pct_excl ?? item.average).toFixed(0)}% KL
                  </div>
                  <div className="font-mono text-[10.5px] text-secondary tnum mt-0.5 whitespace-nowrap">
                    {formatCompactVol(item.predict_vol)}
                  </div>
                </div>
              </Link>
            ))}
          </div>
        </Panel>
      )}

      <Panel flush>
        {/* Header: Editorial Tabs + Inline Signal Summary + Custom Dropdown Filters */}
        <div className="px-5 pt-4 pb-3 border-b border-line space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <Tabs
              tabs={STRATEGY_TABS}
              active={strategyTab}
              onChange={(t) => {
                setStrategyTab(t)
                setPage(1)
              }}
            />

            {/* Inline Editorial Signal Counters */}
            <div className="flex flex-wrap items-center gap-1.5 text-[11.5px]">
              {(
                [
                  { key: "all", label: "Tổng mẫu", count: allItems.length },
                  { key: "mo_mua", label: "Mở mua", count: stats.moMuaCount },
                  { key: "tao_lap", label: "Đột biến KL", count: stats.taoLapCount },
                  { key: "ru_nen", label: "Rũ nến (10P)", count: stats.ruNenCount },
                  { key: "test_cung", label: "Test cung (10P)", count: stats.testCungCount },
                  { key: "dong_tien", label: "Dòng tiền", count: stats.dongTienCount },
                ] as const
              ).map((chip) => {
                const isActive = quickFilter === chip.key
                return (
                  <button
                    key={chip.key}
                    type="button"
                    onClick={() => {
                      setQuickFilter(isActive && chip.key !== "all" ? "all" : chip.key)
                      setPage(1)
                    }}
                    className={`inline-flex items-center gap-1.5 h-7 px-2.5 rounded-[5px] border transition-colors ${
                      isActive
                        ? "border-ink bg-ink text-paper font-medium"
                        : "border-line bg-surface text-secondary hover:border-line-strong hover:text-ink"
                    }`}
                  >
                    <span>{chip.label}</span>
                    <span
                      className={`font-mono text-[11px] tnum ${
                        isActive ? "text-paper/85" : "text-ink font-semibold"
                      }`}
                    >
                      {chip.count}
                    </span>
                  </button>
                )
              })}
            </div>
          </div>

          {/* Search & Custom Dropdowns Row */}
          <div className="flex flex-wrap items-center justify-between gap-2.5 pt-1">
            <div className="flex flex-wrap items-center gap-2">
              <input
                type="search"
                value={searchQuery}
                onChange={(e) => {
                  setSearchQuery(e.target.value)
                  setPage(1)
                }}
                placeholder="Tìm mã hoặc tên doanh nghiệp…"
                className="h-8 w-56 rounded-[6px] border border-line bg-surface px-2.5 text-[12px] text-ink outline-none focus:border-mineral"
              />
              <CustomSelect
                value={sectorFilter}
                onChange={(v) => {
                  setSectorFilter(v)
                  setPage(1)
                }}
                options={sectorOptions}
                ariaLabel="Lọc theo ngành"
                className="w-52"
              />
              <CustomSelect
                value={quickFilter}
                onChange={(v) => {
                  setQuickFilter(v)
                  setPage(1)
                }}
                options={signalOptionsWithCounts}
                ariaLabel="Lọc theo tín hiệu VSA"
                className="w-60"
              />
              {(sectorFilter || quickFilter !== "all" || searchQuery) && (
                <button
                  type="button"
                  onClick={() => {
                    setSectorFilter("")
                    setQuickFilter("all")
                    setSearchQuery("")
                    setPage(1)
                  }}
                  className="h-8 px-2.5 text-[11.5px] text-muted hover:text-ink transition-colors"
                >
                  Đặt lại bộ lọc
                </button>
              )}
            </div>

            <div className="flex items-center gap-2 font-mono text-[11.5px] text-muted tnum">
              <span
                className={`h-1.5 w-1.5 rounded-full ${
                  isRealtimeActive ? "bg-gain animate-pulse" : "bg-muted"
                }`}
              />
              <span>
                {isRealtimeActive ? "Trực tiếp DNSE" : `EOD ${data?.asOf ? String(data.asOf).slice(0, 10) : ""}`}
                {" · "}Hiển thị {pagedItems.length}/{filteredItems.length} mã
              </span>
            </div>
          </div>
        </div>

        {/* Table Body */}
        {loading && allItems.length === 0 ? (
          <div className="py-16 text-center text-[13px] text-muted">
            Đang tải dữ liệu định lượng VSA & Dòng tiền…
          </div>
        ) : filteredItems.length === 0 ? (
          <div className="py-16 text-center text-[13px] text-muted">
            Không có cổ phiếu nào khớp với tiêu chí lọc hiện tại.
          </div>
        ) : strategy === "sieu_pham" ? (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[940px] text-[12px]">
              <thead>
                <tr className="border-b border-line bg-soft/40 text-[10px] uppercase tracking-wider text-muted">
                  <th className="px-5 py-2.5 text-left font-medium">Mã / Ngành</th>
                  <th className="px-3 py-2.5 text-left font-medium w-[17%]">Mua chủ động (BVC)</th>
                  <th className="px-3 py-2.5 text-center font-medium">
                    <VsaHeaderTip
                      label="Rũ nến (% Rút)"
                      title="Rũ nến · Wyckoff Spring / Shakeout (10 phiên)"
                      formula="% Rút = (min(O, C) - L) / (H - L) ≥ 50%"
                      detail="Đo tỷ lệ bóng nến dưới so với toàn bộ biên độ cây nến. Đạt chuẩn khi giá quét thủng đáy hỗ trợ 5–10 phiên (biên độ ≥ 2.0%) nhưng rút chân ≥ 50% và đóng cửa ở phần trên (≥ 60%)."
                    />
                  </th>
                  <th className="px-3 py-2.5 text-center font-medium">
                    <VsaHeaderTip
                      label="Test cung (% MA10)"
                      title="Test cung · No Supply Bar (10 phiên)"
                      formula="% MA = Volume phiên test / MA10 ≤ 68%"
                      detail="Đo mức độ cạn kiệt nguồn cung trong nền 10 phiên. Đạt chuẩn khi biên độ nến hẹp (≤ 1.85%), rút chân đỡ giá ≥ 40%, đóng cửa ≥ 50% và Volume cạn kiệt ≤ 68% MA10. % càng thấp càng cạn cung."
                    />
                  </th>
                  <th className="px-3 py-2.5 text-center font-medium">
                    <VsaHeaderTip
                      label="Dòng tiền (Vol · MFI)"
                      title="Dòng tiền lớn · Pocket Pivot (10 phiên)"
                      formula="Vol / MA10 ≥ 1.45x & Close ≥ 60%"
                      detail="Xác nhận phiên Pocket Pivot trong nền 10 phiên: nến tăng (≥ 0.8%, đóng cửa ≥ 60% biên độ) với khối lượng bùng nổ ≥ 1.45 lần MA10."
                    />
                  </th>
                  <th className="px-3 py-2.5 text-right font-medium">Tiền áp đảo (20P)</th>
                  <th className="px-3 py-2.5 text-right font-medium">KL mua tối đa</th>
                  <th className="px-3 py-2.5 text-center font-medium">Trạng thái / Giá</th>
                  <th className="px-5 py-2.5 text-right font-medium">KL khớp dự kiến</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {pagedItems.map((row, rowIdx) => (
                  <tr key={row.code} className="hover:bg-soft/45 transition-colors">
                    <td className="px-5 py-2.5">
                      <Link
                        to={`/stock/${encodeURIComponent(row.code)}`}
                        className="group inline-flex flex-col"
                      >
                        <div className="flex items-center gap-1.5">
                          <span className="font-mono text-[13px] font-semibold text-ink group-hover:underline">
                            {row.code}
                          </span>
                          <span
                            className={`font-mono text-[11px] tnum ${
                              row.changePct > 0
                                ? "text-gain"
                                : row.changePct < 0
                                  ? "text-loss"
                                  : "text-muted"
                            }`}
                          >
                            {row.changePct > 0 ? "+" : ""}
                            {row.changePct.toFixed(2)}%
                          </span>
                          <VsaConfluenceBar
                            ruNen={row.ru_nen}
                            testCung={row.test_cung}
                            dongTien={row.dong_tien}
                          />
                        </div>
                        <span className="text-[11px] text-muted truncate max-w-[165px]">
                          {formatSectorName(row.sector)}
                        </span>
                      </Link>
                    </td>
                    <td className="px-3 py-2.5">
                      <BuyforceCell value={row.avg_buyforce} />
                    </td>
                    <td className="px-3 py-2.5 text-center">
                      <VsaMetricCell
                        ok={row.ru_nen}
                        meta={row.ru_nen_meta}
                        fallbackLabel="Shakeout"
                        tone="teal"
                        kind="ru_nen"
                        placeBelow={rowIdx < 2}
                      />
                    </td>
                    <td className="px-3 py-2.5 text-center">
                      <VsaMetricCell
                        ok={row.test_cung}
                        meta={row.test_cung_meta}
                        fallbackLabel="No Supply"
                        tone="mineral"
                        kind="test_cung"
                        placeBelow={rowIdx < 2}
                      />
                    </td>
                    <td className="px-3 py-2.5 text-center">
                      <VsaMetricCell
                        ok={row.dong_tien}
                        meta={row.dong_tien_meta}
                        fallbackLabel="Active Flow"
                        tone="gain"
                        kind="dong_tien"
                        placeBelow={rowIdx < 2}
                      />
                    </td>
                    <td className="px-3 py-2.5 text-right font-mono font-medium text-ink tnum">
                      <span className={row.tien_ap_dao >= 55 ? "text-gain font-semibold" : "text-secondary"}>
                        {row.tien_ap_dao}%
                      </span>
                    </td>
                    <td className="px-3 py-2.5 text-right font-mono text-secondary tnum">
                      {row.max_buy.toLocaleString("vi-VN")}
                    </td>
                    <td className="px-3 py-2.5 text-center">
                      <ExpectedBuyBadge moMua={row.mo_mua} close={row.close} />
                    </td>
                    <td className="px-5 py-2.5 text-right">
                      <ExpectedVolCell
                        vol={row.predict_vol}
                        trendUp={row.trend_up}
                        average={row.average}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[880px] text-[12px]">
              <thead>
                <tr className="border-b border-line bg-soft/40 text-[10px] uppercase tracking-wider text-muted">
                  <th className="px-5 py-2.5 text-left font-medium">Mã / Ngành</th>
                  <th className="px-3 py-2.5 text-left font-medium w-[20%]">Mua chủ động (BVC)</th>
                  <th className="px-3 py-2.5 text-center font-medium">RSI (14) / MFI (14)</th>
                  <th className="px-3 py-2.5 text-center font-medium">Tần suất nổ Vol</th>
                  <th className="px-3 py-2.5 text-right font-medium">Biên độ 20P</th>
                  <th className="px-3 py-2.5 text-right font-medium">KL mua tối đa</th>
                  <th className="px-3 py-2.5 text-center font-medium">Trạng thái / Giá</th>
                  <th className="px-5 py-2.5 text-right font-medium">KL khớp dự kiến</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {pagedItems.map((row, rowIdx) => (
                  <tr key={row.code} className="hover:bg-soft/45 transition-colors">
                    <td className="px-5 py-2.5">
                      <Link
                        to={`/stock/${encodeURIComponent(row.code)}`}
                        className="group inline-flex flex-col"
                      >
                        <div className="flex items-center gap-2">
                          <span className="font-mono text-[13px] font-semibold text-ink group-hover:underline">
                            {row.code}
                          </span>
                          <span
                            className={`font-mono text-[11px] tnum ${
                              row.changePct > 0
                                ? "text-gain"
                                : row.changePct < 0
                                  ? "text-loss"
                                  : "text-muted"
                            }`}
                          >
                            {row.changePct > 0 ? "+" : ""}
                            {row.changePct.toFixed(2)}%
                          </span>
                        </div>
                        <span className="text-[11px] text-muted truncate max-w-[165px]">
                          {formatSectorName(row.sector)}
                        </span>
                      </Link>
                    </td>
                    <td className="px-3 py-2.5">
                      <BuyforceCell value={row.avg_buyforce} />
                    </td>
                    <td className="px-3 py-2.5 text-center font-mono text-[11.5px] text-secondary tnum">
                      RSI {row.rsi != null ? row.rsi.toFixed(1) : "—"} · MFI{" "}
                      {row.mfi != null ? row.mfi.toFixed(0) : "—"}
                    </td>
                    <td className="px-3 py-2.5 text-center font-mono text-[11.5px] text-secondary">
                      {row.freq || "—"}
                    </td>
                    <td className="px-3 py-2.5 text-right font-mono font-medium text-gain tnum">
                      ±{row.avg_profit ?? 0}%
                    </td>
                    <td className="px-3 py-2.5 text-right font-mono text-secondary tnum">
                      {row.max_buy.toLocaleString("vi-VN")}
                    </td>
                    <td className="px-3 py-2.5 text-center">
                      <ExpectedBuyBadge moMua={row.mo_mua} close={row.close} />
                    </td>
                    <td className="px-5 py-2.5 text-right">
                      <ExpectedVolCell
                        vol={row.predict_vol}
                        trendUp={row.trend_up}
                        average={row.average}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Pagination Footer */}
        {totalPages > 1 && (
          <div className="px-5 py-3 border-t border-line flex items-center justify-between text-[12px]">
            <span className="font-mono text-muted tnum">
              Trang {currentPage} / {totalPages} ({filteredItems.length} mã)
            </span>
            <div className="flex items-center gap-1.5">
              <Button
                variant="secondary"
                disabled={currentPage <= 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
              >
                ← Trước
              </Button>
              <Button
                variant="secondary"
                disabled={currentPage >= totalPages}
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              >
                Tiếp →
              </Button>
            </div>
          </div>
        )}
      </Panel>
    </div>
  )
}

/**
 * Dải chẩn đoán VSA & Dòng tiền trên trang chi tiết cổ phiếu /stock/[symbol]
 */
export function StockVsaStrip({ item }: { item?: SuperStockItem | null }) {
  if (!item) return null
  return (
    <Panel flush className="mb-4">
      <div className="px-5 py-3.5 border-b border-line flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="text-[13.5px] font-semibold text-ink">
            Chẩn đoán cấu trúc VSA & Dòng tiền ({item.code})
          </h3>
          <p className="text-[11.5px] text-muted">
            Phân tích vi cấu trúc VSA 10–20 phiên: Mua chủ động BVC, Rũ nến (Spring), Test cung (≤68% MA10) và hạn mức thanh khoản chuẩn (3.0% MA10)
          </p>
        </div>
        <ExpectedBuyBadge moMua={item.mo_mua} close={item.close} />
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 divide-x divide-y lg:divide-y-0 divide-line text-[12px]">
        <div className="px-4 py-3">
          <div className="text-[10.5px] uppercase tracking-wide text-muted">Mua chủ động</div>
          <div className="mt-1.5">
            <BuyforceCell value={item.avg_buyforce} />
          </div>
        </div>

        <div className="px-4 py-3">
          <div className="text-[10.5px] uppercase tracking-wide text-muted">Rũ nến (10P)</div>
          <div className="mt-1.5">
            {item.ru_nen ? (
              <VsaMetricCell ok={item.ru_nen} meta={item.ru_nen_meta} fallbackLabel="Đã rũ chân" tone="teal" kind="ru_nen" />
            ) : (
              <span className="font-mono text-[11.5px] text-muted">Chưa kích hoạt</span>
            )}
          </div>
        </div>

        <div className="px-4 py-3">
          <div className="text-[10.5px] uppercase tracking-wide text-muted">Test cung (10P)</div>
          <div className="mt-1.5">
            {item.test_cung ? (
              <VsaMetricCell ok={item.test_cung} meta={item.test_cung_meta} fallbackLabel="Cạn cung" tone="mineral" kind="test_cung" />
            ) : (
              <span className="font-mono text-[11.5px] text-muted">Chưa kích hoạt</span>
            )}
          </div>
        </div>

        <div className="px-4 py-3">
          <div className="text-[10.5px] uppercase tracking-wide text-muted">Dòng tiền</div>
          <div className="mt-1.5">
            {item.dong_tien ? (
              <VsaMetricCell ok={item.dong_tien} meta={item.dong_tien_meta} fallbackLabel="Tiền vào" tone="gain" kind="dong_tien" />
            ) : (
              <span className="font-mono text-[11.5px] text-muted">Trung tính</span>
            )}
          </div>
        </div>

        <div className="px-4 py-3">
          <div className="text-[10.5px] uppercase tracking-wide text-muted">Tiền áp đảo (20P)</div>
          <div className="mt-1 font-mono text-[15px] font-semibold text-ink tnum">
            {item.tien_ap_dao}%
          </div>
        </div>

        <div className="px-4 py-3">
          <div className="text-[10.5px] uppercase tracking-wide text-muted">KL mua tối đa</div>
          <div className="mt-1 font-mono text-[14px] font-medium text-ink tnum">
            {item.max_buy.toLocaleString("vi-VN")} CP
          </div>
        </div>

        <div className="px-4 py-3">
          <div className="text-[10.5px] uppercase tracking-wide text-muted">KL khớp dự kiến</div>
          <div className="mt-1">
            <ExpectedVolCell vol={item.predict_vol} trendUp={item.trend_up} average={item.average} />
          </div>
        </div>
      </div>
    </Panel>
  )
}
