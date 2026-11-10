"use client"

import { useEffect, useMemo, useState } from "react"
import { Page } from "@/components/Shell"
import { Link } from "@/lib/router"
import { portfolioApi, stockApi } from "@/lib/api"
import { formatSectorName } from "@/lib/sector-weights"
import { usePortfolio, type PortfolioPosition, type PortfolioSnapshot } from "@/lib/use-portfolio"
import { useResource } from "@/lib/api/use-resource"
import { DataState } from "@/components/data-state"
import { Button, Panel, Pill, fmt } from "@/components/ui"
import type { ApiProblem } from "@/lib/api/client"

interface ClosedStockTrade {
  sym: string
  sectorCode: string
  sectorLabel: string
  exchange: string
  buyQty: number
  sellQty: number
  matchedQty: number
  avgBuy: number
  avgSell: number
  investedVnd: number
  retPct: number
  pnlVnd: number
  tradesCount: number
  holdingDays: number
  firstDate: string
  lastDate: string
  orders: NormalizedOrder[]
}

interface NormalizedOrder {
  id: string
  symbol: string
  sectorCode: string
  sectorLabel: string
  exchange: string
  side: "BUY" | "SELL"
  orderType: string
  quantity: number
  price: number
  totalValue: number
  status: string
  createdAt: string
}

interface SectorAttributionItem {
  sectorCode: string
  sectorLabel: string
  color: string
  symbols: string[]
  tradesCount: number
  winCount: number
  totalCount: number
  capitalVnd: number
  weightPct: number
  pnlVnd: number
  retPct: number
}

interface PersonalCharter {
  styleTarget: "T_PLUS" | "SWING" | "POSITION"
  favoriteSectors: string[]
  maxPosWeightPct: number
  maxStopLossPct: number
}

const DEFAULT_CHARTER: PersonalCharter = {
  styleTarget: "SWING",
  favoriteSectors: ["TRANSPORTATION", "BANKS", "OIL_GAS"],
  maxPosWeightPct: 15,
  maxStopLossPct: 5,
}

const KNOWN_SYMBOL_SECTOR_FALLBACK: Record<string, string> = {
  PVP: "TRANSPORTATION",
  PVT: "TRANSPORTATION",
  HAH: "TRANSPORTATION",
  GMD: "TRANSPORTATION",
  VSC: "TRANSPORTATION",
  VPB: "BANKS",
  MSB: "BANKS",
  NAB: "BANKS",
  CTG: "BANKS",
  VCB: "BANKS",
  BID: "BANKS",
  TCB: "BANKS",
  MBB: "BANKS",
  ACB: "BANKS",
  VIB: "BANKS",
  OCB: "BANKS",
  STB: "BANKS",
  HDB: "BANKS",
  NVL: "REAL_ESTATE",
  VHM: "REAL_ESTATE",
  VIC: "REAL_ESTATE",
  KDH: "REAL_ESTATE",
  NLG: "REAL_ESTATE",
  DIG: "REAL_ESTATE",
  PDR: "REAL_ESTATE",
  AAA: "CHEMICALS",
  DCM: "CHEMICALS",
  DPM: "CHEMICALS",
  DGC: "CHEMICALS",
  CSV: "CHEMICALS",
  PHR: "CHEMICALS",
  DHC: "BASIC_RESOURCES",
  HPG: "BASIC_RESOURCES",
  HSG: "BASIC_RESOURCES",
  NKG: "BASIC_RESOURCES",
  POW: "UTILITIES",
  NT2: "UTILITIES",
  REE: "UTILITIES",
  PC1: "UTILITIES",
  GAS: "OIL_GAS",
  PVD: "OIL_GAS",
  PVS: "OIL_GAS",
  BSR: "OIL_GAS",
  PLX: "OIL_GAS",
  FPT: "TECHNOLOGY",
  CMG: "TECHNOLOGY",
  MWG: "RETAIL_TRADE",
  FRT: "RETAIL_TRADE",
  PNJ: "RETAIL_TRADE",
  DGW: "RETAIL_TRADE",
  SSI: "FINANCIAL_SERVICES",
  VND: "FINANCIAL_SERVICES",
  VCI: "FINANCIAL_SERVICES",
  HCM: "FINANCIAL_SERVICES",
  VIX: "FINANCIAL_SERVICES",
  PAN: "AGRICULTURE",
  DBC: "AGRICULTURE",
  HAG: "AGRICULTURE",
  VNM: "FOOD_BEVERAGE",
  MSN: "FOOD_BEVERAGE",
  SAB: "FOOD_BEVERAGE",
}

const EDITORIAL_SECTOR_COLORS = [
  "#1f6a4a",
  "#0f766e",
  "#2563eb",
  "#d97706",
  "#7c3aed",
  "#64748b",
]

const CHARTER_SECTOR_CHOICES = [
  "BANKS",
  "TRANSPORTATION",
  "FINANCIAL_SERVICES",
  "REAL_ESTATE",
  "OIL_GAS",
  "CHEMICALS",
  "BASIC_RESOURCES",
  "TECHNOLOGY",
  "RETAIL_TRADE",
  "UTILITIES",
]

const n = (value: unknown): number | null =>
  value === null || value === undefined || value === "" || !Number.isFinite(Number(value))
    ? null
    : Number(value)

const fmtVnd = (value: number | null | undefined, suffix = " ₫"): string => {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—"
  return `${Math.round(value).toLocaleString("vi-VN")}${suffix}`
}

const signedVnd = (value: number | null | undefined, suffix = " ₫"): string => {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—"
  const rounded = Math.round(value)
  const sign = rounded > 0 ? "+" : ""
  return `${sign}${rounded.toLocaleString("vi-VN")}${suffix}`
}

const signedPct = (value: number | null | undefined, digits = 2): string => {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—"
  const sign = value > 0 ? "+" : ""
  return `${sign}${value.toFixed(digits).replace(".", ",")}%`
}

const shortVnd = (val: number | null | undefined, signed = false): string => {
  if (val === null || val === undefined || !Number.isFinite(val)) return "—"
  const abs = Math.abs(val)
  const sign = val > 0 ? (signed ? "+" : "") : val < 0 ? "-" : ""
  if (abs >= 1_000_000_000) {
    return `${sign}${(abs / 1_000_000_000).toFixed(2).replace(".", ",")} tỷ ₫`
  }
  if (abs >= 1_000_000) {
    return `${sign}${(abs / 1_000_000_000 >= 0.1 ? abs / 1_000_000 : abs / 1_000_000).toFixed(2).replace(".", ",")} tr ₫`
  }
  if (abs >= 1_000) {
    return `${sign}${Math.round(abs / 1_000)}k ₫`
  }
  return `${sign}${Math.round(abs)} ₫`
}

const fmtShortDate = (iso: string): string => {
  const parts = String(iso || "").slice(0, 10).split("-")
  return parts.length === 3 ? `${parts[2]}/${parts[1]}` : String(iso || "—")
}

const fmtFullDate = (iso: string): string => {
  const parts = String(iso || "").slice(0, 10).split("-")
  return parts.length === 3 ? `${parts[2]}/${parts[1]}/${parts[0]}` : String(iso || "—")
}

const fmtVietnamDateTime = (iso: string): string => {
  if (!iso) return "—"
  const d = new Date(iso)
  if (!Number.isFinite(d.getTime())) return String(iso)
  const datePart = d.toLocaleDateString("vi-VN", {
    timeZone: "Asia/Ho_Chi_Minh",
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  })
  const timePart = d.toLocaleTimeString("vi-VN", {
    timeZone: "Asia/Ho_Chi_Minh",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  })
  return `${datePart} · ${timePart}`
}

function resolveSectorCode(symbol: string, rawSector?: unknown): string {
  const cleaned = String(rawSector ?? "").trim().toUpperCase()
  if (cleaned && cleaned !== "HOSE" && cleaned !== "HNX" && cleaned !== "UPCOM") {
    return cleaned
  }
  return KNOWN_SYMBOL_SECTOR_FALLBACK[symbol.toUpperCase()] || "OTHER_INDUSTRIALS"
}

function polarToCartesian(cx: number, cy: number, r: number, angleDeg: number) {
  const rad = ((angleDeg - 90) * Math.PI) / 180.0
  return {
    x: cx + r * Math.cos(rad),
    y: cy + r * Math.sin(rad),
  }
}

function describeDonutArc(cx: number, cy: number, r: number, startAngle: number, endAngle: number): string {
  const clampedEnd = Math.min(endAngle, startAngle + 359.5)
  if (clampedEnd <= startAngle + 0.5) return ""
  const start = polarToCartesian(cx, cy, r, clampedEnd)
  const end = polarToCartesian(cx, cy, r, startAngle)
  const largeArcFlag = clampedEnd - startAngle <= 180 ? "0" : "1"
  return `M ${start.x.toFixed(2)} ${start.y.toFixed(2)} A ${r} ${r} 0 ${largeArcFlag} 0 ${end.x.toFixed(2)} ${end.y.toFixed(2)}`
}

function useAnimatedProgress(triggerKey: string, durationMs = 900) {
  const [state, setState] = useState<{ key: string; val: number }>({ key: triggerKey, val: 0 })
  useEffect(() => {
    let rafId = 0
    const start = performance.now()
    const tick = (now: number) => {
      const elapsed = Math.max(0, now - start)
      const t = Math.min(1, elapsed / durationMs)
      const eased = 1 - Math.pow(1 - t, 4)
      setState({ key: triggerKey, val: eased })
      if (t < 1) {
        rafId = requestAnimationFrame(tick)
      }
    }
    rafId = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(rafId)
  }, [triggerKey, durationMs])
  return state.key === triggerKey ? state.val : 0
}

function smoothSplinePath(pts: { x: number; y: number }[]): string {
  if (pts.length === 0) return ""
  if (pts.length === 1) return `M ${pts[0].x.toFixed(1)} ${pts[0].y.toFixed(1)}`
  let d = `M ${pts[0].x.toFixed(1)} ${pts[0].y.toFixed(1)}`
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i]
    const p1 = pts[i + 1]
    const midX = (p0.x + p1.x) / 2
    d += ` C ${midX.toFixed(1)} ${p0.y.toFixed(1)}, ${midX.toFixed(1)} ${p1.y.toFixed(1)}, ${p1.x.toFixed(1)} ${p1.y.toFixed(1)}`
  }
  return d
}

function smoothAreaPath(pts: { x: number; y: number }[], baselineY: number): string {
  if (pts.length < 2) return ""
  const lineD = smoothSplinePath(pts)
  const firstX = pts[0].x.toFixed(1)
  const lastX = pts[pts.length - 1].x.toFixed(1)
  const base = baselineY.toFixed(1)
  return `${lineD} L ${lastX} ${base} L ${firstX} ${base} Z`
}

function normalizeOrdersAndRoundTrips(rawOrders: Record<string, unknown>[]): {
  normalizedOrders: NormalizedOrder[]
  closedTrades: ClosedStockTrade[]
  buyCount: number
  sellCount: number
  totalGrossWinVnd: number
  totalCutLossVnd: number
} {
  const normalizedOrders: NormalizedOrder[] = []
  const bySym: Record<
    string,
    {
      sectorCode: string
      exchange: string
      buyVal: number
      buyQty: number
      sellVal: number
      sellQty: number
      tradesCount: number
      firstDate: string
      lastDate: string
      orders: NormalizedOrder[]
    }
  > = {}

  let buyCount = 0
  let sellCount = 0

  const chronological = [...rawOrders].reverse()
  for (let i = 0; i < chronological.length; i++) {
    const raw = chronological[i]
    const symbol = String(raw.symbol ?? raw.ticker ?? "").trim().toUpperCase()
    if (!symbol) continue
    const side: "BUY" | "SELL" = String(raw.side || "").toUpperCase() === "BUY" ? "BUY" : "SELL"
    const orderType = String(raw.orderType || "LO").toUpperCase()
    const quantity = Number(raw.quantity ?? raw.filledQuantity ?? 0)
    const rawPrice = Number(raw.price ?? raw.limitPrice ?? raw.avgFillPrice ?? 0)
    const price = rawPrice > 0 && rawPrice < 500 ? Math.round(rawPrice * 1000) : Math.round(rawPrice)
    if (!Number.isFinite(quantity) || quantity <= 0 || !Number.isFinite(price) || price <= 0) continue

    const sectorCode = resolveSectorCode(symbol, raw.sector)
    const sectorLabel = formatSectorName(sectorCode)
    const exchange = String(raw.exchange || "HOSE").toUpperCase()
    const createdAt = String(raw.createdAt ?? raw.date ?? "")
    const norm: NormalizedOrder = {
      id: String(raw.id ?? `${symbol}-${i}`),
      symbol,
      sectorCode,
      sectorLabel,
      exchange,
      side,
      orderType,
      quantity,
      price,
      totalValue: quantity * price,
      status: String(raw.status ?? "FILLED"),
      createdAt,
    }
    normalizedOrders.push(norm)

    if (!bySym[symbol]) {
      bySym[symbol] = {
        sectorCode,
        exchange,
        buyVal: 0,
        buyQty: 0,
        sellVal: 0,
        sellQty: 0,
        tradesCount: 0,
        firstDate: createdAt,
        lastDate: createdAt,
        orders: [],
      }
    }
    const bucket = bySym[symbol]
    bucket.tradesCount += 1
    if (!bucket.firstDate || (createdAt && createdAt < bucket.firstDate)) bucket.firstDate = createdAt
    if (!bucket.lastDate || (createdAt && createdAt > bucket.lastDate)) bucket.lastDate = createdAt
    bucket.orders.push(norm)

    if (side === "BUY") {
      bucket.buyVal += quantity * price
      bucket.buyQty += quantity
      buyCount += 1
    } else {
      bucket.sellVal += quantity * price
      bucket.sellQty += quantity
      sellCount += 1
    }
  }

  const closedTrades: ClosedStockTrade[] = []
  for (const [sym, b] of Object.entries(bySym)) {
    const matchedQty = Math.min(b.buyQty, b.sellQty)
    if (matchedQty <= 0) continue
    const avgBuy = Math.round(b.buyVal / b.buyQty)
    const avgSell = Math.round(b.sellVal / b.sellQty)
    const investedVnd = Math.round(avgBuy * matchedQty)
    const pnlVnd = Math.round((b.sellVal / b.sellQty - b.buyVal / b.buyQty) * matchedQty)
    const retPct = avgBuy > 0 ? ((avgSell - avgBuy) / avgBuy) * 100 : 0
    const t0 = Date.parse(b.firstDate)
    const t1 = Date.parse(b.lastDate)
    const holdingDays =
      Number.isFinite(t0) && Number.isFinite(t1) && t1 >= t0
        ? Math.max(1, Math.round((t1 - t0) / 86_400_000))
        : 5

    closedTrades.push({
      sym,
      sectorCode: b.sectorCode,
      sectorLabel: formatSectorName(b.sectorCode),
      exchange: b.exchange,
      buyQty: b.buyQty,
      sellQty: b.sellQty,
      matchedQty,
      avgBuy,
      avgSell,
      investedVnd,
      retPct,
      pnlVnd,
      tradesCount: b.tradesCount,
      holdingDays,
      firstDate: b.firstDate,
      lastDate: b.lastDate,
      orders: [...b.orders].reverse(),
    })
  }

  closedTrades.sort((a, b) => b.pnlVnd - a.pnlVnd)
  const totalGrossWinVnd = closedTrades.filter(t => t.pnlVnd > 0).reduce((s, t) => s + t.pnlVnd, 0)
  const totalCutLossVnd = closedTrades.filter(t => t.pnlVnd < 0).reduce((s, t) => s + t.pnlVnd, 0)

  return {
    normalizedOrders: normalizedOrders.reverse(),
    closedTrades,
    buyCount,
    sellCount,
    totalGrossWinVnd,
    totalCutLossVnd,
  }
}

function PersonalWealthStage({
  curvePoints,
  benchmarkPoints,
  alphaPct,
  openingCash,
  currentNav,
  cashVnd,
  marketValueVnd,
  positionsCount,
  totalPnl,
  totalReturnPct,
  dailyPnL,
  dailyPnLPercent,
}: {
  curvePoints: { date: string; value: number }[]
  benchmarkPoints?: { date: string; value: number }[]
  alphaPct: number | null
  openingCash: number | null
  currentNav: number | null
  cashVnd: number
  marketValueVnd: number | null
  positionsCount: number
  totalPnl: number | null
  totalReturnPct: number | null
  dailyPnL: number | null
  dailyPnLPercent: number | null
}) {
  const [chartMode, setChartMode] = useState<"nav" | "vni">("nav")
  const [windowSize, setWindowSize] = useState<15 | 30 | 60>(60)
  const [hoveredIdx, setHoveredIdx] = useState<number | null>(null)

  const slicedCurve = useMemo(() => curvePoints.slice(-windowSize), [curvePoints, windowSize])
  const baseCapital = openingCash ?? (slicedCurve.length > 0 ? slicedCurve[0].value : currentNav ?? cashVnd)

  const enrichedSeries = useMemo(() => {
    let runningPeak = baseCapital > 0 ? baseCapital : 0
    const benchList = benchmarkPoints ?? []
    const windowStartNav =
      slicedCurve.length > 0 && slicedCurve[0].value > 0 ? slicedCurve[0].value : baseCapital

    const findVniOnOrBefore = (dateStr: string): number | null => {
      if (benchList.length === 0) return null
      let found: number | null = null
      for (let i = 0; i < benchList.length; i++) {
        if (benchList[i].date <= dateStr && benchList[i].value > 0) {
          found = benchList[i].value
        }
      }
      return found ?? (benchList[0]?.value > 0 ? benchList[0].value : null)
    }

    const vniStart =
      slicedCurve.length > 0 ? findVniOnOrBefore(slicedCurve[0].date) : null

    const series: {
      date: string
      nav: number
      pnlVnd: number
      portRetPct: number
      windowPortRetPct: number
      vniPoints: number | null
      vniRetPct: number
      vniSimulatedNav: number
      alphaSpreadPct: number
      ddPct: number
    }[] = []

    for (let idx = 0; idx < slicedCurve.length; idx++) {
      const pt = slicedCurve[idx]
      if (pt.value > runningPeak) runningPeak = pt.value
      const ddPct = runningPeak > 0 ? ((pt.value - runningPeak) / runningPeak) * 100 : 0
      const portRetPct = baseCapital > 0 ? ((pt.value - baseCapital) / baseCapital) * 100 : 0
      const windowPortRetPct =
        windowStartNav > 0 ? ((pt.value - windowStartNav) / windowStartNav) * 100 : portRetPct
      const vniVal = findVniOnOrBefore(pt.date)
      const vniRetPct =
        vniStart && vniStart > 0 && vniVal !== null
          ? ((vniVal - vniStart) / vniStart) * 100
          : 0
      const vniSimulatedNav = windowStartNav * (1 + vniRetPct / 100)
      const alphaSpreadPct = windowPortRetPct - vniRetPct

      series.push({
        date: pt.date,
        nav: pt.value,
        pnlVnd: pt.value - baseCapital,
        portRetPct,
        windowPortRetPct,
        vniPoints: vniVal,
        vniRetPct,
        vniSimulatedNav,
        alphaSpreadPct,
        ddPct,
      })
    }
    return series
  }, [slicedCurve, baseCapital, benchmarkPoints])

  const progress = useAnimatedProgress(
    `wealth-stage-${chartMode}-${windowSize}-${enrichedSeries.length}`,
    850,
  )

  const W = 740
  const H = 225
  const padL = 62
  const padR = 16
  const padT = 22
  const mainH = 118
  const gapH = 22
  const ddTop = padT + mainH + gapH
  const ddH = 38
  const plotW = W - padL - padR

  // Value range for "nav" mode: strictly calibrated to Personal Portfolio NAV so the curve is never squashed
  const navValues = enrichedSeries.map(p => p.nav)
  const rawMin = navValues.length > 0 ? Math.min(baseCapital, ...navValues) : baseCapital
  const rawMax = navValues.length > 0 ? Math.max(baseCapital, ...navValues) : baseCapital
  const span = Math.max(rawMax - rawMin, Math.max(baseCapital * 0.005, 1_000_000))
  const minVal = rawMin - span * 0.12
  const maxVal = rawMax + span * 0.16
  const valRange = Math.max(maxVal - minVal, 1)

  // Signed power scale for "vni" comparison mode so a low-volatility portfolio curve (+1%) isn't squashed flat by VNI swings (-4%)
  const scalePct = (pct: number) => Math.sign(pct) * Math.pow(Math.abs(pct), 0.62)
  const rawPctValues = enrichedSeries.flatMap(p => [p.windowPortRetPct, p.vniRetPct, 0])
  const rawMinPct = rawPctValues.length > 0 ? Math.min(...rawPctValues) : -2
  const rawMaxPct = rawPctValues.length > 0 ? Math.max(...rawPctValues) : 2
  const scaledMinPct = scalePct(rawMinPct) - 0.35
  const scaledMaxPct = scalePct(rawMaxPct) + 0.42
  const scaledPctRange = Math.max(scaledMaxPct - scaledMinPct, 0.5)

  const baselineY =
    chartMode === "nav"
      ? padT + mainH - ((baseCapital - minVal) / valRange) * mainH
      : padT + mainH - ((0 - scaledMinPct) / scaledPctRange) * mainH

  const navCoords = enrichedSeries.map((pt, idx) => {
    const x = padL + (idx / Math.max(enrichedSeries.length - 1, 1)) * plotW
    const targetY =
      chartMode === "nav"
        ? padT + mainH - ((pt.nav - minVal) / valRange) * mainH
        : padT + mainH - ((scalePct(pt.windowPortRetPct) - scaledMinPct) / scaledPctRange) * mainH
    const y = baselineY + (targetY - baselineY) * progress
    return { x, y }
  })

  const vniCoords = enrichedSeries.map((pt, idx) => {
    const x = padL + (idx / Math.max(enrichedSeries.length - 1, 1)) * plotW
    const targetY =
      padT + mainH - ((scalePct(pt.vniRetPct) - scaledMinPct) / scaledPctRange) * mainH
    const y = baselineY + (targetY - baselineY) * progress
    return { x, y }
  })

  // Find peak NAV point in current window for annotation in "nav" mode
  let peakIdx = 0
  for (let i = 1; i < enrichedSeries.length; i++) {
    if (enrichedSeries[i].nav > enrichedSeries[peakIdx].nav) peakIdx = i
  }
  const peakPt = enrichedSeries[peakIdx]
  const peakCoord = navCoords[peakIdx]

  const ddCoords = enrichedSeries.map((pt, idx) => {
    const x = padL + (idx / Math.max(enrichedSeries.length - 1, 1)) * plotW
    const clampedDd = Math.max(-15, Math.min(0, pt.ddPct))
    const maxDdScale = Math.min(-0.5, ...enrichedSeries.map(s => s.ddPct))
    const normalized = maxDdScale < 0 ? Math.min(1, Math.abs(clampedDd) / Math.abs(maxDdScale)) : 0
    const y = ddTop + normalized * (ddH - 6) * progress
    return { x, y }
  })

  const navLinePath = smoothSplinePath(navCoords)
  const navAreaPath = smoothAreaPath(navCoords, padT + mainH)
  const vniLinePath = smoothSplinePath(vniCoords)
  const ddLinePath = smoothSplinePath(ddCoords)
  const ddAreaPath = smoothAreaPath(ddCoords, ddTop)

  const activeIdx =
    hoveredIdx !== null && hoveredIdx < enrichedSeries.length
      ? hoveredIdx
      : Math.max(0, enrichedSeries.length - 1)
  const activePt = enrichedSeries[activeIdx]
  const activeCoord = navCoords[activeIdx]
  const activeVniCoord = vniCoords[activeIdx]
  const lastCoord = navCoords[navCoords.length - 1]
  const lastPt = enrichedSeries[enrichedSeries.length - 1]

  const isTotalPositive = (totalPnl ?? 0) >= 0
  const isDailyPositive = (dailyPnL ?? 0) >= 0
  const cashWeightPct =
    currentNav && currentNav > 0 ? Math.min(100, Math.max(0, (cashVnd / currentNav) * 100)) : 100
  const stockWeightPct = Math.max(0, 100 - cashWeightPct)

  return (
    <div className="flex flex-col justify-between p-5 lg:col-span-8">
      <div>
        <div className="flex items-center justify-between gap-2 border-b border-line pb-4 whitespace-nowrap">
          <div>
            <div className="text-[10.5px] font-semibold tracking-wider text-muted uppercase">
              Tài sản ròng (NAV)
            </div>
            <div className="tnum mt-0.5 font-mono text-[21px] font-bold tracking-tight text-ink">
              {fmtVnd(currentNav)}
            </div>
          </div>

          <div className="border-l border-line pl-3">
            <div className="text-[10.5px] font-semibold tracking-wider text-muted uppercase">
              Tổng Lãi / Lỗ Ròng
            </div>
            <div className="mt-1 flex items-baseline gap-1.5">
              <span
                className={`tnum font-mono text-[15px] font-bold ${
                  isTotalPositive ? "text-gain" : "text-loss"
                }`}
              >
                {signedVnd(totalPnl)}
              </span>
              <span
                className={`tnum rounded px-1.5 py-0.5 font-mono text-[10.5px] font-semibold ${
                  isTotalPositive ? "bg-gain/12 text-gain" : "bg-loss/12 text-loss"
                }`}
              >
                {signedPct(totalReturnPct)}
              </span>
            </div>
          </div>

          <div className="border-l border-line pl-3">
            <div className="text-[10.5px] font-semibold tracking-wider text-muted uppercase">
              Vượt VN-Index (Alpha)
            </div>
            <div className="mt-1 flex items-baseline gap-1.5">
              <span
                className={`tnum font-mono text-[15px] font-bold ${
                  (alphaPct ?? lastPt?.alphaSpreadPct ?? 0) >= 0 ? "text-gain" : "text-loss"
                }`}
              >
                {signedPct(alphaPct ?? lastPt?.alphaSpreadPct ?? 0)}
              </span>
              {lastPt && (
                <span className="tnum font-mono text-[10.5px] text-secondary">
                  (VNI {signedPct(lastPt.vniRetPct)})
                </span>
              )}
            </div>
          </div>

          <div className="border-l border-line pl-3">
            <div className="text-[10.5px] font-semibold tracking-wider text-muted uppercase">
              Biến động hôm nay
            </div>
            <div className="mt-1 flex items-baseline gap-1">
              <span
                className={`tnum font-mono text-[15px] font-semibold ${
                  dailyPnL === null
                    ? "text-secondary"
                    : isDailyPositive
                      ? "text-gain"
                      : "text-loss"
                }`}
              >
                {dailyPnL !== null ? signedVnd(dailyPnL) : "0 ₫"}
              </span>
              {dailyPnLPercent !== null && (
                <span className="tnum font-mono text-[10.5px] text-secondary">
                  ({signedPct(dailyPnLPercent)})
                </span>
              )}
            </div>
          </div>
        </div>

        {/* Fixed 1-line chart toolbar & readout: never wraps or overlaps on hover */}
        <div className="mt-3 flex h-7 items-center justify-between gap-2 overflow-hidden whitespace-nowrap">
          {activePt ? (
            <div className="tnum flex min-w-0 items-center gap-1.5 overflow-hidden font-mono text-[10.5px] whitespace-nowrap">
              <span className="shrink-0 rounded bg-canvas px-1.5 py-0.5 text-[10px] font-semibold text-secondary">
                Phiên {fmtShortDate(activePt.date)}
              </span>
              {chartMode === "nav" ? (
                <>
                  <strong className="text-ink">{fmtVnd(activePt.nav)}</strong>
                  <span className={activePt.pnlVnd >= 0 ? "font-semibold text-gain" : "font-semibold text-loss"}>
                    ({signedPct(activePt.windowPortRetPct)})
                  </span>
                  <span className="text-muted">·</span>
                  <span className="text-secondary">
                    VNI{" "}
                    <strong className={activePt.vniRetPct >= 0 ? "text-gain" : "text-loss"}>
                      {signedPct(activePt.vniRetPct)}
                    </strong>
                  </span>
                  <span className="text-muted">·</span>
                  <span className="text-secondary">
                    DD <strong className="text-ink">{activePt.ddPct.toFixed(2)}%</strong>
                  </span>
                </>
              ) : (
                <>
                  <span className="text-secondary">
                    Danh mục{" "}
                    <strong className={activePt.windowPortRetPct >= 0 ? "text-gain" : "text-loss"}>
                      {signedPct(activePt.windowPortRetPct)}
                    </strong>
                  </span>
                  <span className="text-muted">·</span>
                  <span className="text-secondary">
                    VN-Index{" "}
                    <strong className={activePt.vniRetPct >= 0 ? "text-gain" : "text-loss"}>
                      {signedPct(activePt.vniRetPct)}
                    </strong>
                  </span>
                  <span className="text-muted">·</span>
                  <span className="text-secondary">
                    Alpha{" "}
                    <strong className={activePt.alphaSpreadPct >= 0 ? "text-gain" : "text-loss"}>
                      {signedPct(activePt.alphaSpreadPct)}
                    </strong>
                  </span>
                </>
              )}
            </div>
          ) : (
            <span className="font-mono text-[11px] text-muted">
              Tài khoản mới — lịch sử NAV sẽ tự động cập nhật sau mỗi phiên
            </span>
          )}

          {curvePoints.length > 1 && (
            <div className="flex shrink-0 items-center gap-1.5 whitespace-nowrap">
              {/* Mode Switcher: Tài sản (₫) vs So sánh VN-Index (%) */}
              <div className="inline-flex items-center rounded-md border border-line bg-canvas p-0.5 text-[10.5px]">
                <button
                  type="button"
                  onClick={() => setChartMode("nav")}
                  className={`rounded px-2 py-0.5 font-mono font-medium transition-colors ${
                    chartMode === "nav"
                      ? "bg-ink text-paper shadow-2xs"
                      : "text-secondary hover:text-ink"
                  }`}
                >
                  Tài sản (₫)
                </button>
                <button
                  type="button"
                  onClick={() => setChartMode("vni")}
                  className={`rounded px-2 py-0.5 font-mono font-medium transition-colors ${
                    chartMode === "vni"
                      ? "bg-ink text-paper shadow-2xs"
                      : "text-secondary hover:text-ink"
                  }`}
                >
                  So sánh VNI (%)
                </button>
              </div>

              {/* Window Switcher: 15P / 30P / 60P */}
              <div className="inline-flex items-center rounded-md border border-line bg-canvas p-0.5 text-[10.5px]">
                {(
                  [
                    { v: 15 as const, label: "15P" },
                    { v: 30 as const, label: "30P" },
                    { v: 60 as const, label: "60P" },
                  ] as const
                ).map(opt => (
                  <button
                    key={opt.v}
                    type="button"
                    onClick={() => setWindowSize(opt.v)}
                    className={`rounded px-1.5 py-0.5 font-mono font-medium transition-colors ${
                      windowSize === opt.v
                        ? "bg-ink text-paper shadow-2xs"
                        : "text-secondary hover:text-ink"
                    }`}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>

        {enrichedSeries.length >= 2 ? (
          <div className="mt-1.5 rounded-lg border border-line/80 bg-canvas/40 p-2">
            <svg
              viewBox={`0 0 ${W} ${H}`}
              className="h-[205px] w-full select-none overflow-visible"
              role="img"
              aria-label="Biểu đồ tăng trưởng tài sản ròng cá nhân so sánh với VN-Index và mức sụt giảm"
              onMouseLeave={() => setHoveredIdx(null)}
            >
              <defs>
                <linearGradient id="personalNavAreaGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="var(--color-gain)" stopOpacity="0.22" />
                  <stop offset="100%" stopColor="var(--color-gain)" stopOpacity="0.0" />
                </linearGradient>
                <linearGradient id="personalDdAreaGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="var(--color-loss)" stopOpacity="0.04" />
                  <stop offset="100%" stopColor="var(--color-loss)" stopOpacity="0.20" />
                </linearGradient>
              </defs>

              {/* In-chart Header Legend */}
              {lastPt && (
                <g>
                  <circle cx={padL + 6} cy={10} r="3.5" fill="var(--color-gain)" />
                  <text
                    x={padL + 14}
                    y={13}
                    className="fill-ink font-mono text-[9.5px] font-semibold"
                  >
                    Danh mục cá nhân ({signedPct(lastPt.windowPortRetPct)})
                  </text>

                  {chartMode === "vni" && (
                    <>
                      <line
                        x1={padL + 188}
                        y1={10}
                        x2={padL + 204}
                        y2={10}
                        stroke="var(--color-mineral)"
                        strokeWidth="1.8"
                        strokeDasharray="3 2"
                      />
                      <text
                        x={padL + 209}
                        y={13}
                        className="fill-secondary font-mono text-[9.5px] font-medium"
                      >
                        VN-Index ({signedPct(lastPt.vniRetPct)})
                      </text>
                    </>
                  )}

                  <text
                    x={W - padR}
                    y={13}
                    textAnchor="end"
                    className="fill-gain font-mono text-[9.5px] font-semibold"
                  >
                    Alpha vượt VN-Index: {signedPct(alphaPct ?? lastPt.alphaSpreadPct)} (VNI {signedPct(lastPt.vniRetPct)})
                  </text>
                </g>
              )}

              <line
                x1={padL}
                y1={padT}
                x2={W - padR}
                y2={padT}
                stroke="var(--color-line)"
                strokeWidth="0.75"
              />
              <line
                x1={padL}
                y1={baselineY}
                x2={W - padR}
                y2={baselineY}
                stroke="var(--color-muted)"
                strokeDasharray="3 3"
                strokeWidth="0.9"
              />
              <line
                x1={padL}
                y1={padT + mainH}
                x2={W - padR}
                y2={padT + mainH}
                stroke="var(--color-line)"
                strokeWidth="0.85"
              />

              <text
                x={padL - 6}
                y={padT + 4}
                textAnchor="end"
                className="fill-muted font-mono text-[9.5px]"
              >
                {chartMode === "nav" ? shortVnd(rawMax) : signedPct(rawMaxPct, 1)}
              </text>
              <text
                x={padL - 6}
                y={baselineY + 3}
                textAnchor="end"
                className="fill-muted font-mono text-[9.5px]"
              >
                {chartMode === "nav" ? shortVnd(baseCapital) : "0,0%"}
              </text>
              {chartMode === "vni" && (
                <text
                  x={padL - 6}
                  y={padT + mainH}
                  textAnchor="end"
                  className="fill-muted font-mono text-[9.5px]"
                >
                  {signedPct(rawMinPct, 1)}
                </text>
              )}

              {/* VN-Index Reference Curve (Only in "vni" comparison mode so "nav" mode keeps 100% pure NAV scaling) */}
              {chartMode === "vni" && vniLinePath && (
                <path
                  d={vniLinePath}
                  fill="none"
                  stroke="var(--color-mineral)"
                  strokeWidth="1.75"
                  strokeDasharray="4 3"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  opacity="0.88"
                />
              )}

              {/* Personal Portfolio NAV Curve (Solid Emerald Line + Gradient Area) */}
              {navAreaPath && <path d={navAreaPath} fill="url(#personalNavAreaGrad)" />}
              {navLinePath && (
                <path
                  d={navLinePath}
                  fill="none"
                  stroke="var(--color-gain)"
                  strokeWidth="2.2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              )}

              {/* Peak NAV Callout in "nav" mode */}
              {chartMode === "nav" && peakPt && peakCoord && peakPt.pnlVnd > 0 && (
                <g>
                  <circle
                    cx={peakCoord.x}
                    cy={peakCoord.y}
                    r="2.8"
                    fill="var(--color-gain)"
                  />
                  <text
                    x={peakCoord.x}
                    y={Math.max(padT + 10, peakCoord.y - 6)}
                    textAnchor="middle"
                    className="fill-gain font-mono text-[9px] font-semibold"
                  >
                    Đỉnh {fmtShortDate(peakPt.date)}: {shortVnd(peakPt.pnlVnd, true)}
                  </text>
                </g>
              )}

              <text
                x={padL}
                y={ddTop - 5}
                className="fill-muted font-mono text-[8.5px] uppercase"
              >
                Sụt giảm từ đỉnh (Drawdown %)
              </text>
              <line
                x1={padL}
                y1={ddTop}
                x2={W - padR}
                y2={ddTop}
                stroke="var(--color-line)"
                strokeWidth="0.75"
              />
              <line
                x1={padL}
                y1={ddTop + ddH}
                x2={W - padR}
                y2={ddTop + ddH}
                stroke="var(--color-line)"
                strokeWidth="0.75"
              />
              <text
                x={padL - 6}
                y={ddTop + 5}
                textAnchor="end"
                className="fill-muted font-mono text-[9px]"
              >
                0%
              </text>

              {ddAreaPath && <path d={ddAreaPath} fill="url(#personalDdAreaGrad)" />}
              {ddLinePath && (
                <path
                  d={ddLinePath}
                  fill="none"
                  stroke="var(--color-loss)"
                  strokeWidth="1.25"
                  strokeOpacity="0.75"
                />
              )}

              {activeCoord && (
                <g>
                  <line
                    x1={activeCoord.x}
                    y1={padT}
                    x2={activeCoord.x}
                    y2={ddTop + ddH}
                    stroke="var(--color-ink)"
                    strokeDasharray="2 2"
                    strokeWidth="0.9"
                    opacity="0.45"
                  />
                  {chartMode === "vni" && activeVniCoord && (
                    <circle
                      cx={activeVniCoord.x}
                      cy={activeVniCoord.y}
                      r="3.2"
                      fill="var(--color-paper)"
                      stroke="var(--color-mineral)"
                      strokeWidth="1.8"
                    />
                  )}
                  <circle
                    cx={activeCoord.x}
                    cy={activeCoord.y}
                    r="4"
                    fill="var(--color-paper)"
                    stroke="var(--color-gain)"
                    strokeWidth="2.2"
                  />
                </g>
              )}

              {lastCoord && hoveredIdx === null && (
                <circle
                  cx={lastCoord.x}
                  cy={lastCoord.y}
                  r="4"
                  fill="var(--color-gain)"
                  stroke="var(--color-paper)"
                  strokeWidth="1.5"
                />
              )}

              {enrichedSeries.length > 0 && (
                <>
                  <text
                    x={padL}
                    y={H - 4}
                    textAnchor="start"
                    className="fill-muted font-mono text-[9.5px]"
                  >
                    {fmtShortDate(enrichedSeries[0].date)}
                  </text>
                  <text
                    x={padL + plotW / 2}
                    y={H - 4}
                    textAnchor="middle"
                    className="fill-muted font-mono text-[9.5px]"
                  >
                    {fmtShortDate(enrichedSeries[Math.floor(enrichedSeries.length / 2)].date)}
                  </text>
                  <text
                    x={W - padR}
                    y={H - 4}
                    textAnchor="end"
                    className="fill-ink font-mono text-[9.5px] font-semibold"
                  >
                    {fmtShortDate(enrichedSeries[enrichedSeries.length - 1].date)}
                  </text>
                </>
              )}

              {navCoords.map((pt, idx) => {
                const colW = plotW / Math.max(navCoords.length, 1)
                return (
                  <rect
                    key={enrichedSeries[idx].date}
                    x={pt.x - colW / 2}
                    y={padT}
                    width={colW}
                    height={ddTop + ddH - padT}
                    fill="transparent"
                    className="cursor-crosshair"
                    onMouseEnter={() => setHoveredIdx(idx)}
                  />
                )
              })}
            </svg>
          </div>
        ) : (
          <div className="mt-2 flex h-[205px] flex-col items-center justify-center rounded-lg border border-dashed border-line bg-canvas/40 p-6 text-center">
            <div className="text-sm font-semibold text-ink">
              Tài khoản chưa có lịch sử biến động NAV nhiều phiên
            </div>
            <p className="mt-1 max-w-md text-xs text-secondary">
              Số dư tài sản ròng hiện tại của bạn là <strong className="font-mono text-ink">{fmtVnd(currentNav)}</strong>. Đường cong hiệu suất sẽ tự động được vẽ sau các phiên giao dịch tiếp theo.
            </p>
          </div>
        )}
      </div>

      {/* Bottom Bar: Personal Cash vs Equity Allocation */}
      <div className="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-line/80 bg-canvas/55 px-3.5 py-2 text-xs">
        <div className="flex items-center gap-3 whitespace-nowrap">
          <span className="font-semibold text-ink">Cơ cấu tài sản hiện tại:</span>
          <span className="tnum font-mono text-secondary">
            Tiền mặt <strong className="text-ink">{cashWeightPct.toFixed(1)}%</strong> ({fmtVnd(cashVnd)})
          </span>
          <span className="text-muted">·</span>
          <span className="tnum font-mono text-secondary">
            Cổ phiếu <strong className="text-ink">{stockWeightPct.toFixed(1)}%</strong> ({positionsCount} mã · {fmtVnd(marketValueVnd ?? 0)})
          </span>
        </div>

        <div className="flex items-center gap-2 whitespace-nowrap">
          <div className="h-2 w-28 overflow-hidden rounded-full bg-line/60">
            <div
              className="h-full rounded-full bg-gain transition-all duration-500"
              style={{ width: `${Math.max(4, stockWeightPct > 0 ? stockWeightPct : 100)}%` }}
            />
          </div>
          <span className="font-mono text-[11px] font-medium text-gain">
            {positionsCount > 0 ? `Đang giữ ${positionsCount} mã` : "Sẵn sàng giải ngân"}
          </span>
        </div>
      </div>
    </div>
  )
}

/**
 * NEW BESPOKE VISUALIZATION 1 (5/12): Interactive Radial Sector Ecosystem Ring
 */
function RadialSectorStudio({
  sectors,
  selectedSector,
  onSelectSector,
  favoriteSectors,
}: {
  sectors: SectorAttributionItem[]
  selectedSector: string | null
  onSelectSector: (code: string | null) => void
  favoriteSectors: string[]
}) {
  const [hoveredSector, setHoveredSector] = useState<string | null>(null)
  const progress = useAnimatedProgress(`radial-sector-${sectors.length}`, 850)

  const activeSector =
    sectors.find(s => s.sectorCode === (hoveredSector ?? selectedSector)) ?? sectors[0] ?? null

  const arcs = useMemo(() => {
    let cursor = 0
    const gapDeg = sectors.length > 1 ? 3.5 : 0
    const list: (SectorAttributionItem & { startAngle: number; endAngle: number })[] = []
    for (let i = 0; i < sectors.length; i++) {
      const s = sectors[i]
      const sweep = Math.max(8, (s.weightPct / 100) * (360 - sectors.length * gapDeg))
      const start = cursor
      const end = cursor + sweep * progress
      cursor += sweep + gapDeg
      list.push({
        ...s,
        startAngle: start,
        endAngle: end,
      })
    }
    return list
  }, [sectors, progress])

  return (
    <Panel className="flex flex-col justify-between p-5 lg:col-span-6">
      <div>
        <div className="flex items-start justify-between gap-2">
          <div>
            <h2 className="text-[15px] font-semibold text-ink">
              Vòng Quang Phổ Hệ Sinh Thái Ngành
            </h2>
            <p className="mt-0.5 text-xs text-secondary">
              Cấu trúc phân bổ vốn và hiệu quả sinh lời theo từng nhóm ngành của riêng bạn
            </p>
          </div>
          {selectedSector && (
            <button
              type="button"
              onClick={() => onSelectSector(null)}
              className="shrink-0 rounded border border-line bg-canvas px-2 py-0.5 text-[11px] font-medium text-ink hover:bg-soft whitespace-nowrap"
            >
              Bỏ lọc ×
            </button>
          )}
        </div>

        {sectors.length > 0 && activeSector ? (
          <div className="mt-4 flex flex-col items-center gap-4 sm:flex-row">
            {/* Radial SVG Donut Studio */}
            <div className="relative flex h-[182px] w-[182px] shrink-0 items-center justify-center">
              <svg
                viewBox="0 0 200 200"
                className="h-[182px] w-[182px] select-none overflow-visible"
                role="img"
                aria-label="Biểu đồ vòng cung tỷ trọng ngành cá nhân"
                onMouseLeave={() => setHoveredSector(null)}
              >
                <circle
                  cx="100"
                  cy="100"
                  r="78"
                  fill="none"
                  stroke="var(--color-line)"
                  strokeWidth="15"
                  strokeOpacity="0.4"
                />
                {arcs.map(arc => {
                  const isActive = activeSector.sectorCode === arc.sectorCode
                  const d = describeDonutArc(100, 100, 78, arc.startAngle, arc.endAngle)
                  if (!d) return null
                  return (
                    <path
                      key={arc.sectorCode}
                      d={d}
                      fill="none"
                      stroke={arc.color}
                      strokeWidth={isActive ? 20 : 15}
                      strokeLinecap="butt"
                      className="cursor-pointer transition-all duration-200"
                      opacity={
                        hoveredSector || selectedSector ? (isActive ? 1 : 0.45) : 0.92
                      }
                      onMouseEnter={() => setHoveredSector(arc.sectorCode)}
                      onClick={() =>
                        onSelectSector(
                          selectedSector === arc.sectorCode ? null : arc.sectorCode,
                        )
                      }
                    />
                  )
                })}
              </svg>

              {/* Center Interactive Readout */}
              <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center px-6 text-center">
                <span className="max-w-[122px] truncate text-[10.5px] font-semibold text-secondary">
                  {activeSector.sectorLabel}
                </span>
                <span className="tnum mt-0.5 font-mono text-[21px] font-bold tracking-tight text-ink">
                  {activeSector.weightPct.toFixed(1)}%
                </span>
                <span
                  className={`tnum font-mono text-[11px] font-semibold ${
                    activeSector.pnlVnd >= 0 ? "text-gain" : "text-loss"
                  }`}
                >
                  {shortVnd(activeSector.pnlVnd, true)}
                </span>
                <span className="mt-0.5 font-mono text-[9.5px] text-muted">
                  Thắng {activeSector.winCount}/{activeSector.totalCount} mã
                </span>
              </div>
            </div>

            {/* Interactive Sector Legend */}
            <div className="w-full min-w-0 flex-1 space-y-1.5">
              {sectors.slice(0, 5).map(sec => {
                const isSelected = selectedSector === sec.sectorCode
                const isActive = activeSector.sectorCode === sec.sectorCode
                const isFav = favoriteSectors.includes(sec.sectorCode)
                return (
                  <button
                    key={sec.sectorCode}
                    type="button"
                    onMouseEnter={() => setHoveredSector(sec.sectorCode)}
                    onMouseLeave={() => setHoveredSector(null)}
                    onClick={() =>
                      onSelectSector(
                        selectedSector === sec.sectorCode ? null : sec.sectorCode,
                      )
                    }
                    className={`block w-full rounded-lg border px-2.5 py-1.5 text-left transition-colors ${
                      isSelected
                        ? "border-ink bg-canvas shadow-2xs"
                        : isActive
                          ? "border-line bg-canvas/70"
                          : "border-transparent hover:border-line/70 hover:bg-canvas/40"
                    }`}
                  >
                    <div className="flex items-center justify-between gap-1.5 whitespace-nowrap">
                      <div className="flex min-w-0 items-center gap-1.5">
                        <span
                          className="h-2.5 w-2.5 shrink-0 rounded-full"
                          style={{ backgroundColor: sec.color }}
                        />
                        <span className="truncate text-xs font-semibold text-ink">
                          {sec.sectorLabel}
                        </span>
                        {isFav && (
                          <span className="shrink-0 rounded bg-gain/10 px-1 py-0.2 text-[9px] font-semibold text-gain">
                            Ưu tiên
                          </span>
                        )}
                      </div>
                      <span
                        className={`tnum shrink-0 font-mono text-xs font-bold ${
                          sec.pnlVnd >= 0 ? "text-gain" : "text-loss"
                        }`}
                      >
                        {signedVnd(sec.pnlVnd)}
                      </span>
                    </div>

                    <div className="mt-0.5 flex items-center justify-between gap-2 pl-4 font-mono text-[10px] whitespace-nowrap">
                      <span className="truncate text-muted">
                        {sec.weightPct.toFixed(1)}% vốn · {sec.symbols.slice(0, 3).join(", ")}
                      </span>
                      <span className="tnum shrink-0 text-secondary">
                        Thắng {sec.winCount}/{sec.totalCount} mã
                      </span>
                    </div>
                  </button>
                )
              })}
            </div>
          </div>
        ) : (
          <div className="mt-4 flex h-[192px] items-center justify-center rounded-lg border border-dashed border-line bg-canvas/40 p-4 text-center text-xs text-secondary">
            Chưa có dữ liệu phân bổ ngành. Hãy đặt lệnh giao dịch đầu tiên để kích hoạt Vòng Quang Phổ Ngành.
          </div>
        )}
      </div>

      <div className="mt-3 flex items-center justify-between gap-2 rounded-md border border-line/60 bg-canvas/55 px-3 py-2 text-[11px] text-secondary whitespace-nowrap">
        <span className="truncate">
          Bấm vào từng cung ngành để lọc trực tiếp bảng Sổ cái bên dưới
        </span>
        <Link to="/sectors" className="shrink-0 font-semibold text-ink hover:underline">
          Sóng ngành →
        </Link>
      </div>
    </Panel>
  )
}

/**
 * NEW BESPOKE VISUALIZATION 2 (6/12): Trade Horizon × Return % × Capital Bubble Scatter Matrix
 */
function TradingStyleBubbleMatrix({
  closedTrades,
  charter,
  onSelectTrade,
  onOpenCharter,
}: {
  closedTrades: ClosedStockTrade[]
  charter: PersonalCharter
  onSelectTrade: (trade: ClosedStockTrade) => void
  onOpenCharter: () => void
}) {
  const [hoveredSym, setHoveredSym] = useState<string | null>(null)
  const progress = useAnimatedProgress(`bubble-matrix-${closedTrades.length}`, 900)

  const W = 560
  const H = 198
  const padL = 44
  const padR = 18
  const padT = 22
  const padB = 24
  const plotW = W - padL - padR
  const plotH = H - padT - padB

  const maxDays = useMemo(() => {
    if (closedTrades.length === 0) return 30
    return Math.max(28, ...closedTrades.map(t => t.holdingDays))
  }, [closedTrades])

  const { minRet, maxRet } = useMemo(() => {
    if (closedTrades.length === 0) return { minRet: -8, maxRet: 12 }
    const rets = closedTrades.map(t => t.retPct)
    return {
      minRet: Math.min(-5, Math.floor(Math.min(...rets) - 2.5)),
      maxRet: Math.max(8, Math.ceil(Math.max(...rets) + 2.5)),
    }
  }, [closedTrades])

  const retSpan = Math.max(maxRet - minRet, 1)
  const zeroY = padT + plotH - ((0 - minRet) / retSpan) * plotH

  const maxCap = useMemo(() => {
    if (closedTrades.length === 0) return 1
    return Math.max(1, ...closedTrades.map(t => t.investedVnd))
  }, [closedTrades])

  // Square-root X-axis mapping so T+ (≤7d), Swing (8-21d), and Position (>21d) have balanced visual columns
  const dayToX = (days: number) => {
    const clamped = Math.max(1, Math.min(maxDays, days))
    const norm = Math.sqrt(clamped) / Math.sqrt(maxDays)
    return padL + norm * plotW
  }

  const tPlusX = dayToX(7)
  const swingX = dayToX(21)

  const nodes = useMemo(() => {
    const raw = closedTrades.map(t => {
      const clampedDays = Math.max(1, Math.min(maxDays, t.holdingDays))
      const normX = Math.sqrt(clampedDays) / Math.sqrt(maxDays)
      const x = padL + normX * plotW
      const targetY = padT + plotH - ((t.retPct - minRet) / retSpan) * plotH
      const r = 9.5 + Math.sqrt(t.investedVnd / maxCap) * 7.5
      return {
        trade: t,
        x,
        targetY,
        r,
      }
    })

    // Deterministic 8-pass pairwise collision relaxation so bubbles never overlap
    for (let iter = 0; iter < 8; iter++) {
      for (let i = 0; i < raw.length; i++) {
        for (let j = i + 1; j < raw.length; j++) {
          const a = raw[i]
          const b = raw[j]
          let dx = b.x - a.x
          let dy = b.targetY - a.targetY
          if (Math.abs(dx) < 0.01 && Math.abs(dy) < 0.01) {
            dx = (j - i) * 1.5
            dy = (i % 2 === 0 ? 1 : -1) * 1.2
          }
          const dist = Math.hypot(dx, dy)
          const minDist = a.r + b.r + 3.5
          if (dist < minDist && dist > 0) {
            const overlap = (minDist - dist) * 0.52
            const ux = dx / dist
            const uy = dy / dist
            a.x -= ux * overlap
            b.x += ux * overlap
            a.targetY -= uy * overlap * 0.45
            b.targetY += uy * overlap * 0.45
          }
        }
      }
      for (let i = 0; i < raw.length; i++) {
        const n = raw[i]
        n.x = Math.max(padL + n.r + 2, Math.min(W - padR - n.r - 2, n.x))
        n.targetY = Math.max(padT + n.r + 2, Math.min(padT + plotH - n.r - 2, n.targetY))
      }
    }

    return raw.map(n => ({
      trade: n.trade,
      x: n.x,
      y: zeroY + (n.targetY - zeroY) * progress,
      r: n.r * progress,
    }))
  }, [closedTrades, maxDays, minRet, retSpan, plotW, plotH, zeroY, maxCap, progress, W, padL, padR, padT])

  const activeTrade =
    closedTrades.find(t => t.sym === hoveredSym) ?? closedTrades[0] ?? null

  const avgDays =
    closedTrades.length > 0
      ? Math.round(closedTrades.reduce((s, t) => s + t.holdingDays, 0) / closedTrades.length)
      : null

  const styleArchetype =
    avgDays === null
      ? "Đang định hình"
      : avgDays <= 7
        ? "Phong cách: Lướt sóng T+"
        : avgDays <= 21
          ? "Phong cách: Swing Ngắn hạn"
          : "Phong cách: Theo Sóng ngành"

  const worstCutPct =
    closedTrades.length > 0 ? Math.min(0, ...closedTrades.map(t => t.retPct)) : 0
  const stopLossCompliant = Math.abs(worstCutPct) <= charter.maxStopLossPct

  return (
    <Panel className="flex flex-col justify-between p-5 lg:col-span-6">
      <div>
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-[15px] font-semibold text-ink">
            Bản Đồ Tọa Độ Phong Cách &amp; Chu Kỳ
          </h2>
          <div className="shrink-0">
            <Pill tone="mineral">{styleArchetype}</Pill>
          </div>
        </div>
        <p className="mt-0.5 text-xs text-secondary">
          Trục ngang: Số ngày nắm giữ · Trục dọc: Tỷ suất sinh lời (%) · Cỡ bong bóng: Vốn giải ngân
        </p>

        {/* Fixed 1-line interactive readout header (Never wraps on hover) */}
        <div className="mt-2.5 flex h-5 items-center justify-between gap-2 overflow-hidden text-[11px] whitespace-nowrap">
          <span className="shrink-0 font-semibold tracking-wider text-muted uppercase">
            Tọa độ mã CP ({avgDays !== null ? `TB ${avgDays} ngày` : "Mới"})
          </span>

          {activeTrade && (
            <div className="tnum flex shrink-0 items-center gap-1 font-mono">
              <strong className="text-ink">{activeTrade.sym}</strong>
              <span className="text-secondary">
                · {activeTrade.holdingDays} ngày · Vốn {shortVnd(activeTrade.investedVnd)}
              </span>
              <strong className={activeTrade.pnlVnd >= 0 ? "text-gain" : "text-loss"}>
                · {signedPct(activeTrade.retPct, 1)} ({shortVnd(activeTrade.pnlVnd, true)})
              </strong>
            </div>
          )}
        </div>

        {closedTrades.length > 0 ? (
          <div className="mt-1 rounded-lg border border-line/80 bg-canvas/35 p-2">
            <svg
              viewBox={`0 0 ${W} ${H}`}
              className="h-[184px] w-full select-none overflow-visible"
              role="img"
              aria-label="Biểu đồ phân tán chu kỳ nắm giữ và tỷ suất sinh lời từng mã cổ phiếu"
              onMouseLeave={() => setHoveredSym(null)}
            >
              {/* Winning Zone above 0% & Cut-loss Zone below 0% */}
              <rect
                x={padL}
                y={padT}
                width={plotW}
                height={Math.max(0, zeroY - padT)}
                fill="var(--color-gain)"
                fillOpacity="0.045"
              />
              <rect
                x={padL}
                y={zeroY}
                width={plotW}
                height={Math.max(0, padT + plotH - zeroY)}
                fill="var(--color-loss)"
                fillOpacity="0.045"
              />

              {/* Style Regime Vertical Dividers */}
              <line
                x1={tPlusX}
                y1={padT}
                x2={tPlusX}
                y2={padT + plotH}
                stroke="var(--color-line)"
                strokeDasharray="3 3"
                strokeWidth="1"
              />
              <line
                x1={swingX}
                y1={padT}
                x2={swingX}
                y2={padT + plotH}
                stroke="var(--color-line)"
                strokeDasharray="3 3"
                strokeWidth="1"
              />

              {/* Regime Labels placed in dedicated top header band above plot */}
              <text
                x={(padL + tPlusX) / 2}
                y={padT - 6}
                textAnchor="middle"
                className="fill-muted font-mono text-[8.5px] font-medium uppercase"
              >
                Lướt T+ (≤7 ngày)
              </text>
              <text
                x={(tPlusX + swingX) / 2}
                y={padT - 6}
                textAnchor="middle"
                className="fill-muted font-mono text-[8.5px] font-medium uppercase"
              >
                Swing (8–21 ngày)
              </text>
              <text
                x={(swingX + W - padR) / 2}
                y={padT - 6}
                textAnchor="middle"
                className="fill-muted font-mono text-[8.5px] font-medium uppercase"
              >
                Theo sóng ngành (&gt;21 ngày)
              </text>

              {/* Zero Return Baseline */}
              <line
                x1={padL}
                y1={zeroY}
                x2={W - padR}
                y2={zeroY}
                stroke="var(--color-muted)"
                strokeWidth="1"
              />

              {/* Y-Axis Labels */}
              <text
                x={padL - 6}
                y={padT + 8}
                textAnchor="end"
                className="fill-gain font-mono text-[9px] font-semibold"
              >
                +{maxRet}%
              </text>
              <text
                x={padL - 6}
                y={zeroY + 3}
                textAnchor="end"
                className="fill-ink font-mono text-[9px] font-semibold"
              >
                0%
              </text>
              <text
                x={padL - 6}
                y={padT + plotH}
                textAnchor="end"
                className="fill-loss font-mono text-[9px] font-semibold"
              >
                {minRet}%
              </text>

              {/* X-Axis Labels */}
              <text
                x={padL}
                y={H - 5}
                textAnchor="start"
                className="fill-muted font-mono text-[9px]"
              >
                1 ngày
              </text>
              <text
                x={tPlusX}
                y={H - 5}
                textAnchor="middle"
                className="fill-muted font-mono text-[9px]"
              >
                7 ngày
              </text>
              <text
                x={swingX}
                y={H - 5}
                textAnchor="middle"
                className="fill-muted font-mono text-[9px]"
              >
                21 ngày
              </text>
              <text
                x={W - padR}
                y={H - 5}
                textAnchor="end"
                className="fill-ink font-mono text-[9px] font-semibold"
              >
                {maxDays} ngày
              </text>

              {/* Trade Bubbles */}
              {nodes.map(({ trade, x, y, r }) => {
                const isWin = trade.pnlVnd >= 0
                const isActive = activeTrade?.sym === trade.sym
                return (
                  <g
                    key={trade.sym}
                    className="cursor-pointer"
                    onMouseEnter={() => setHoveredSym(trade.sym)}
                    onClick={() => onSelectTrade(trade)}
                  >
                    {isActive && (
                      <circle
                        cx={x}
                        cy={y}
                        r={r + 3.5}
                        fill="none"
                        stroke="var(--color-ink)"
                        strokeWidth="1.3"
                        opacity="0.65"
                      />
                    )}
                    <circle
                      cx={x}
                      cy={y}
                      r={r}
                      fill={isWin ? "var(--color-gain)" : "var(--color-loss)"}
                      fillOpacity={isActive ? 0.94 : 0.82}
                      stroke="var(--color-surface)"
                      strokeWidth="1.5"
                    />
                    <text
                      x={x}
                      y={y + 3}
                      textAnchor="middle"
                      className="pointer-events-none fill-paper font-mono text-[8.5px] font-bold"
                    >
                      {trade.sym}
                    </text>
                  </g>
                )
              })}
            </svg>
          </div>
        ) : (
          <div className="mt-2 flex h-[184px] items-center justify-center rounded-lg border border-dashed border-line bg-canvas/40 p-4 text-center text-xs text-secondary">
            Bản đồ tọa độ bong bóng sẽ tự động vẽ các vị thế của bạn ngay khi có giao dịch hoàn tất.
          </div>
        )}
      </div>

      {/* Bottom 1-line Charter Guardrail Check */}
      <div className="mt-3 flex items-center justify-between gap-2 rounded-md border border-line/60 bg-canvas/55 px-3 py-2 text-[11px] whitespace-nowrap">
        <span className="truncate text-secondary">
          Luật cá nhân:{" "}
          <strong className={stopLossCompliant ? "text-gain" : "text-ink"}>
            Cắt lỗ ≤ -{charter.maxStopLossPct}% · Tối đa {charter.maxPosWeightPct}% NAV/mã
          </strong>{" "}
          (Sâu nhất {worstCutPct.toFixed(1)}%)
        </span>
        <button
          type="button"
          onClick={onOpenCharter}
          className="shrink-0 font-semibold text-ink hover:underline"
        >
          Tùy chỉnh luật →
        </button>
      </div>
    </Panel>
  )
}

function StockTradeAuditDrawer({
  trade,
  onClose,
  onOpenOrderModal,
}: {
  trade: ClosedStockTrade | null
  onClose: () => void
  onOpenOrderModal: (sym: string, suggestedPrice: number) => void
}) {
  useEffect(() => {
    if (!trade) return
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose()
    }
    window.addEventListener("keydown", handleKey)
    return () => window.removeEventListener("keydown", handleKey)
  }, [trade, onClose])

  if (!trade) return null
  const isWin = trade.pnlVnd >= 0

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-ink/35 backdrop-blur-[1px] transition-opacity"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`Kiểm toán giao dịch cổ phiếu ${trade.sym}`}
        className="flex h-full w-full max-w-lg flex-col justify-between border-l border-line bg-surface p-6 shadow-2xl"
        onClick={e => e.stopPropagation()}
      >
        <div className="overflow-y-auto pr-1">
          <div className="flex items-start justify-between gap-3 border-b border-line pb-4">
            <div>
              <div className="flex items-center gap-2">
                <span className="font-mono text-2xl font-bold text-ink">{trade.sym}</span>
                <Pill tone={isWin ? "gain" : "loss"}>
                  {isWin ? "Thương vụ Chốt lời" : "Thương vụ Cắt lỗ"}
                </Pill>
                <span className="rounded border border-line bg-canvas px-2 py-0.5 text-[11px] font-medium text-secondary">
                  {trade.sectorLabel} · {trade.exchange}
                </span>
              </div>
              <p className="mt-1 text-xs text-secondary">
                Chu kỳ nắm giữ {trade.holdingDays} ngày ({fmtFullDate(trade.firstDate)} → {fmtFullDate(trade.lastDate)})
              </p>
            </div>
            <button
              type="button"
              onClick={onClose}
              className="rounded-md border border-line bg-canvas px-2.5 py-1 text-xs font-medium text-secondary hover:bg-soft hover:text-ink"
            >
              Đóng (Esc)
            </button>
          </div>

          <div className="mt-4 grid grid-cols-3 gap-2.5">
            <div className="rounded-lg border border-line bg-canvas/60 p-3">
              <div className="text-[10.5px] text-muted">Giá Mua TB → Bán TB</div>
              <div className="tnum mt-1 font-mono text-xs font-semibold text-ink whitespace-nowrap">
                {fmt(trade.avgBuy)} → {fmt(trade.avgSell)}
              </div>
              <div className="mt-0.5 font-mono text-[10px] text-secondary">
                Khối lượng: {fmt(trade.matchedQty)} cp
              </div>
            </div>

            <div className="rounded-lg border border-line bg-canvas/60 p-3">
              <div className="text-[10.5px] text-muted">Tỷ suất sinh lời</div>
              <div
                className={`tnum mt-1 font-mono text-base font-bold ${
                  isWin ? "text-gain" : "text-loss"
                }`}
              >
                {signedPct(trade.retPct)}
              </div>
              <div className="mt-0.5 font-mono text-[10px] text-secondary">
                {trade.tradesCount} lệnh khớp
              </div>
            </div>

            <div className="rounded-lg border border-line bg-canvas/60 p-3">
              <div className="text-[10.5px] text-muted">Lãi / Lỗ gộp (VND)</div>
              <div
                className={`tnum mt-1 font-mono text-base font-bold ${
                  isWin ? "text-gain" : "text-loss"
                }`}
              >
                {signedVnd(trade.pnlVnd)}
              </div>
              <div className="mt-0.5 font-mono text-[10px] text-secondary">
                Vốn: {shortVnd(trade.investedVnd)}
              </div>
            </div>
          </div>

          <div className="mt-5">
            <div className="mb-2 flex items-center justify-between">
              <h3 className="text-xs font-semibold tracking-wider text-muted uppercase">
                Nhật ký các lệnh khớp của mã {trade.sym}
              </h3>
              <span className="font-mono text-[11px] text-secondary">
                Tổng cộng {trade.orders.length} lệnh
              </span>
            </div>

            <div className="overflow-hidden rounded-lg border border-line">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-line bg-canvas/70 text-[10px] font-semibold tracking-wider text-muted uppercase">
                    <th className="px-3 py-2 text-left">Chiều</th>
                    <th className="px-3 py-2 text-right">Khối lượng</th>
                    <th className="px-3 py-2 text-right">Giá khớp</th>
                    <th className="px-3 py-2 text-right">Giá trị lệnh</th>
                    <th className="px-3 py-2 text-right">Thời gian</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line/60">
                  {trade.orders.map(ord => (
                    <tr key={ord.id} className="hover:bg-soft/40">
                      <td className="px-3 py-2">
                        <span
                          className={`inline-block rounded px-1.5 py-0.5 font-mono text-[10px] font-semibold ${
                            ord.side === "BUY"
                              ? "bg-mineral/12 text-mineral"
                              : "bg-gain/12 text-gain"
                          }`}
                        >
                          {ord.side === "BUY" ? "MUA" : "BÁN"}
                        </span>
                      </td>
                      <td className="tnum px-3 py-2 text-right font-mono text-ink">
                        {fmt(ord.quantity)} cp
                      </td>
                      <td className="tnum px-3 py-2 text-right font-mono text-ink">
                        {fmtVnd(ord.price)}
                      </td>
                      <td className="tnum px-3 py-2 text-right font-mono font-medium text-ink">
                        {fmtVnd(ord.totalValue)}
                      </td>
                      <td className="tnum px-3 py-2 text-right font-mono text-[11px] text-secondary">
                        {fmtVietnamDateTime(ord.createdAt)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>

        <div className="mt-4 flex items-center justify-between gap-2 border-t border-line pt-4">
          <Link
            to={`/trade?symbol=${encodeURIComponent(trade.sym)}`}
            className="inline-flex h-9 items-center rounded-md border border-line bg-canvas px-3.5 text-xs font-medium text-ink hover:bg-soft"
          >
            Mở Bảng giá {trade.sym} →
          </Link>
          <div className="flex items-center gap-2">
            <Button
              variant="primary"
              onClick={() => {
                onClose()
                onOpenOrderModal(trade.sym, trade.avgSell)
              }}
            >
              Đặt lệnh mới với {trade.sym}
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}

function PersonalCharterModal({
  isOpen,
  charter,
  onSave,
  onClose,
}: {
  isOpen: boolean
  charter: PersonalCharter
  onSave: (next: PersonalCharter) => void
  onClose: () => void
}) {
  const [draft, setDraft] = useState<PersonalCharter>(charter)

  useEffect(() => {
    if (!isOpen) return
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose()
    }
    window.addEventListener("keydown", handleKey)
    return () => window.removeEventListener("keydown", handleKey)
  }, [isOpen, onClose])

  if (!isOpen) return null

  const toggleSector = (code: string) => {
    setDraft(prev => {
      const exists = prev.favoriteSectors.includes(code)
      if (exists) {
        return {
          ...prev,
          favoriteSectors: prev.favoriteSectors.filter(s => s !== code),
        }
      }
      if (prev.favoriteSectors.length >= 3) {
        return {
          ...prev,
          favoriteSectors: [...prev.favoriteSectors.slice(1), code],
        }
      }
      return {
        ...prev,
        favoriteSectors: [...prev.favoriteSectors, code],
      }
    })
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-ink/40 p-4 backdrop-blur-[1px]"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Thiết lập Phong cách và Luật Giao dịch Cá nhân"
        className="w-full max-w-lg overflow-hidden rounded-xl border border-line bg-surface shadow-2xl"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-start justify-between border-b border-line bg-canvas/60 px-5 py-4">
          <div>
            <h2 className="text-base font-semibold text-ink">
              Kim Chỉ Nam &amp; Luật Giao Dịch Cá Nhân
            </h2>
            <p className="mt-0.5 text-xs text-secondary">
              Cấu hình khẩu vị ngành và ngưỡng quản trị rủi ro của riêng bạn để đối chiếu tự động trên danh mục
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-line bg-surface px-2.5 py-1 text-xs font-medium text-secondary hover:bg-soft hover:text-ink"
          >
            Đóng
          </button>
        </div>

        <div className="space-y-4 p-5 text-xs">
          <div>
            <label className="block font-semibold text-ink">
              1. Trường phái &amp; Nhịp độ giao dịch mục tiêu
            </label>
            <div className="mt-1.5 grid grid-cols-3 gap-2">
              {(
                [
                  { id: "T_PLUS" as const, title: "Lướt sóng T+", sub: "Chu kỳ 3–7 ngày" },
                  { id: "SWING" as const, title: "Swing ngắn hạn", sub: "Chu kỳ 1–4 tuần" },
                  { id: "POSITION" as const, title: "Nắm giữ vị thế", sub: "Chu kỳ > 1 tháng" },
                ] as const
              ).map(opt => (
                <button
                  key={opt.id}
                  type="button"
                  onClick={() => setDraft(prev => ({ ...prev, styleTarget: opt.id }))}
                  className={`rounded-lg border px-3 py-2 text-left transition-colors ${
                    draft.styleTarget === opt.id
                      ? "border-ink bg-ink text-paper"
                      : "border-line bg-canvas text-ink hover:bg-soft"
                  }`}
                >
                  <div className="font-semibold">{opt.title}</div>
                  <div
                    className={`mt-0.5 text-[10.5px] ${
                      draft.styleTarget === opt.id ? "text-paper/80" : "text-secondary"
                    }`}
                  >
                    {opt.sub}
                  </div>
                </button>
              ))}
            </div>
          </div>

          <div>
            <div className="flex items-center justify-between">
              <label className="font-semibold text-ink">
                2. Nhóm ngành sở trường ưu tiên (Chọn tối đa 3 ngành)
              </label>
              <span className="font-mono text-[11px] text-secondary">
                Đã chọn {draft.favoriteSectors.length}/3
              </span>
            </div>
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {CHARTER_SECTOR_CHOICES.map(code => {
                const active = draft.favoriteSectors.includes(code)
                return (
                  <button
                    key={code}
                    type="button"
                    onClick={() => toggleSector(code)}
                    className={`rounded-md border px-2.5 py-1 text-[11.5px] font-medium transition-colors ${
                      active
                        ? "border-gain bg-gain/12 font-semibold text-gain"
                        : "border-line bg-canvas text-secondary hover:text-ink"
                    }`}
                  >
                    {formatSectorName(code)}
                  </button>
                )
              })}
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block font-semibold text-ink">
                3. Trần tỷ trọng tối đa 1 mã (% NAV)
              </label>
              <div className="mt-1.5 grid grid-cols-4 gap-1.5 font-mono">
                {[10, 15, 20, 25].map(pct => (
                  <button
                    key={pct}
                    type="button"
                    onClick={() => setDraft(prev => ({ ...prev, maxPosWeightPct: pct }))}
                    className={`rounded border py-1.5 text-center font-semibold ${
                      draft.maxPosWeightPct === pct
                        ? "border-ink bg-ink text-paper"
                        : "border-line bg-canvas text-ink hover:bg-soft"
                    }`}
                  >
                    {pct}%
                  </button>
                ))}
              </div>
            </div>

            <div>
              <label className="block font-semibold text-ink">
                4. Ngưỡng cắt lỗ kỷ luật tối đa
              </label>
              <div className="mt-1.5 grid grid-cols-4 gap-1.5 font-mono">
                {[3, 5, 7, 10].map(pct => (
                  <button
                    key={pct}
                    type="button"
                    onClick={() => setDraft(prev => ({ ...prev, maxStopLossPct: pct }))}
                    className={`rounded border py-1.5 text-center font-semibold ${
                      draft.maxStopLossPct === pct
                        ? "border-loss bg-loss text-paper"
                        : "border-line bg-canvas text-ink hover:bg-soft"
                    }`}
                  >
                    -{pct}%
                  </button>
                ))}
              </div>
            </div>
          </div>
        </div>

        <div className="flex items-center justify-between border-t border-line bg-canvas/80 px-5 py-3">
          <button
            type="button"
            onClick={() => setDraft(DEFAULT_CHARTER)}
            className="text-xs font-medium text-secondary hover:text-ink hover:underline"
          >
            Khôi phục mặc định
          </button>
          <div className="flex items-center gap-2">
            <Button variant="ghost" onClick={onClose}>
              Hủy
            </Button>
            <Button
              variant="primary"
              onClick={() => {
                onSave(draft)
                onClose()
              }}
            >
              Lưu Luật Cá Nhân
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}

function PersonalOrderModal({
  isOpen,
  initialSymbol,
  initialSide = "BUY",
  initialPrice,
  personalSymbols,
  positions,
  cashVnd,
  onClose,
  onOrderSuccess,
}: {
  isOpen: boolean
  initialSymbol: string
  initialSide?: "BUY" | "SELL"
  initialPrice?: number
  personalSymbols: string[]
  positions: PortfolioPosition[]
  cashVnd: number
  onClose: () => void
  onOrderSuccess: () => Promise<void>
}) {
  const [symbol, setSymbol] = useState(initialSymbol || "FPT")
  const [side, setSide] = useState<"BUY" | "SELL">(initialSide)
  const [orderType, setOrderType] = useState<"LO" | "MP">("LO")
  const [quantity, setQuantity] = useState("100")
  const [priceStr, setPriceStr] = useState(initialPrice ? String(Math.round(initialPrice)) : "")
  const [submitting, setSubmitting] = useState(false)
  const [feedback, setFeedback] = useState<{ tone: "gain" | "loss"; text: string } | null>(null)

  useEffect(() => {
    if (!isOpen) return
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose()
    }
    window.addEventListener("keydown", handleKey)
    return () => window.removeEventListener("keydown", handleKey)
  }, [isOpen, onClose])

  useEffect(() => {
    const cleanSym = symbol.trim().toUpperCase()
    if (!isOpen || cleanSym.length < 3) return
    let cancelled = false
    stockApi
      .quote(cleanSym)
      .then((q: Record<string, unknown> | null) => {
        if (cancelled || !q) return
        const rawP = Number(q.price ?? q.close ?? q.matchPrice ?? q.refPrice ?? 0)
        const vndPrice = rawP > 0 && rawP < 500 ? Math.round(rawP * 1000) : Math.round(rawP)
        if (vndPrice > 0) {
          setPriceStr(prev => (prev === "" ? String(vndPrice) : prev))
        }
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [symbol, isOpen])

  if (!isOpen) return null

  const cleanSym = symbol.trim().toUpperCase()
  const heldPosition = positions.find(p => p.symbol.toUpperCase() === cleanSym)
  const heldQty = heldPosition?.quantity ?? 0

  const parsedQty = Math.max(0, Math.floor(Number(quantity) || 0))
  const parsedPrice = Math.max(0, Math.round(Number(priceStr) || 0))
  const orderValue = parsedQty * parsedPrice
  const estBrokerageFee = orderValue > 0 ? Math.max(10_000, Math.round(orderValue * 0.001)) : 0
  const estSellTax = side === "SELL" && orderValue > 0 ? Math.round(orderValue * 0.001) : 0
  const netCashImpact =
    side === "BUY" ? orderValue + estBrokerageFee : Math.max(0, orderValue - estBrokerageFee - estSellTax)

  const maxBuyLots =
    parsedPrice > 0 ? Math.floor(cashVnd / (parsedPrice * 1.001) / 100) * 100 : 0

  const applyBuyingPowerPct = (pct: number) => {
    if (side === "SELL") {
      const targetSell = Math.max(100, Math.floor(((heldQty * pct) / 100) / 100) * 100)
      setQuantity(String(Math.min(heldQty, targetSell)))
      return
    }
    if (parsedPrice <= 0) return
    const budget = (cashVnd * pct) / 100
    const lots = Math.max(100, Math.floor(budget / (parsedPrice * 1.001) / 100) * 100)
    setQuantity(String(lots))
  }

  const handleSubmit = async () => {
    setFeedback(null)
    if (!cleanSym) {
      setFeedback({ tone: "loss", text: "Vui lòng nhập mã cổ phiếu hợp lệ." })
      return
    }
    if (parsedQty <= 0 || parsedQty % 100 !== 0) {
      setFeedback({ tone: "loss", text: "Khối lượng giao dịch phải là bội số lô chẵn 100 cổ phiếu." })
      return
    }
    if (orderType === "LO" && parsedPrice <= 0) {
      setFeedback({ tone: "loss", text: "Vui lòng nhập mức giá đặt hợp lệ (VND/cp)." })
      return
    }
    setSubmitting(true)
    try {
      await portfolioApi.order({
        symbol: cleanSym,
        side,
        orderType,
        quantity: parsedQty,
        price: orderType === "LO" ? parsedPrice : undefined,
      })
      setFeedback({
        tone: "gain",
        text: `Đã gửi lệnh ${side === "BUY" ? "MUA" : "BÁN"} ${fmt(parsedQty)} cp ${cleanSym} thành công. Đang đồng bộ danh mục...`,
      })
      await onOrderSuccess()
    } catch (err) {
      const problem = err as ApiProblem
      setFeedback({
        tone: "loss",
        text: problem?.message || "Không thể khớp lệnh lúc này (Sổ lệnh trực tuyến ngoài giờ hoặc thiếu dữ liệu).",
      })
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-ink/40 p-4 backdrop-blur-[1px]"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Phiếu đặt lệnh giao dịch cá nhân"
        className="w-full max-w-lg overflow-hidden rounded-xl border border-line bg-surface shadow-2xl"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-start justify-between border-b border-line bg-canvas/60 px-5 py-4">
          <div>
            <h2 className="text-base font-semibold text-ink">
              Phiếu Đặt Lệnh Giao Dịch Cá Nhân
            </h2>
            <p className="mt-0.5 text-xs text-secondary">
              Sức mua tiền mặt của tài khoản:{" "}
              <strong className="font-mono text-ink">{fmtVnd(cashVnd)}</strong>
              {heldQty > 0 ? (
                <>
                  {" "}· Đang giữ <strong className="font-mono text-ink">{fmt(heldQty)} cp {cleanSym}</strong>
                </>
              ) : null}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-line bg-surface px-2.5 py-1 text-xs font-medium text-secondary hover:bg-soft hover:text-ink"
          >
            Đóng
          </button>
        </div>

        <div className="space-y-4 p-5">
          {personalSymbols.length > 0 && (
            <div>
              <div className="mb-1.5 text-[11px] font-semibold text-muted uppercase">
                Mã cổ phiếu trong danh mục &amp; lịch sử của bạn
              </div>
              <div className="flex flex-wrap gap-1.5">
                {personalSymbols.slice(0, 8).map(sym => (
                  <button
                    key={sym}
                    type="button"
                    onClick={() => {
                      setSymbol(sym)
                      setFeedback(null)
                      const pos = positions.find(p => p.symbol === sym)
                      if (pos?.currentPrice && pos.currentPrice > 0) {
                        setPriceStr(String(Math.round(pos.currentPrice)))
                      } else {
                        setPriceStr("")
                      }
                    }}
                    className={`rounded-md border px-2.5 py-1 font-mono text-xs font-semibold transition-colors ${
                      symbol.toUpperCase() === sym
                        ? "border-ink bg-ink text-paper"
                        : "border-line bg-canvas text-ink hover:bg-soft"
                    }`}
                  >
                    {sym}
                  </button>
                ))}
              </div>
            </div>
          )}

          <div className="grid grid-cols-3 gap-3">
            <div>
              <label className="block text-[11px] font-medium text-secondary">Mã cổ phiếu</label>
              <input
                value={symbol}
                placeholder="VD: FPT, VCB..."
                onChange={e => setSymbol(e.target.value.toUpperCase())}
                className="mt-1 h-9 w-full rounded-md border border-line bg-canvas px-3 font-mono text-sm font-bold text-ink uppercase focus:border-ink focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-[11px] font-medium text-secondary">Chiều giao dịch</label>
              <div className="mt-1 grid h-9 grid-cols-2 rounded-md border border-line bg-canvas p-0.5 text-xs font-semibold">
                <button
                  type="button"
                  onClick={() => setSide("BUY")}
                  className={`rounded ${side === "BUY" ? "bg-gain text-paper" : "text-secondary"}`}
                >
                  MUA
                </button>
                <button
                  type="button"
                  onClick={() => setSide("SELL")}
                  className={`rounded ${side === "SELL" ? "bg-loss text-paper" : "text-secondary"}`}
                >
                  BÁN
                </button>
              </div>
            </div>
            <div>
              <label className="block text-[11px] font-medium text-secondary">Loại lệnh</label>
              <div className="mt-1 grid h-9 grid-cols-2 rounded-md border border-line bg-canvas p-0.5 font-mono text-xs font-semibold">
                <button
                  type="button"
                  onClick={() => setOrderType("LO")}
                  className={`rounded ${orderType === "LO" ? "bg-ink text-paper" : "text-secondary"}`}
                >
                  LO
                </button>
                <button
                  type="button"
                  onClick={() => setOrderType("MP")}
                  className={`rounded ${orderType === "MP" ? "bg-ink text-paper" : "text-secondary"}`}
                >
                  MP
                </button>
              </div>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <div className="flex items-center justify-between text-[11px]">
                <label className="font-medium text-secondary">Giá đặt (VND/cp)</label>
                <span className="font-mono text-[10px] text-muted">
                  {orderType === "MP" ? "Khớp theo giá thị trường" : "Bước giá 10–100 ₫"}
                </span>
              </div>
              <input
                type="number"
                step={50}
                value={priceStr}
                disabled={orderType === "MP"}
                placeholder="Nhập giá..."
                onChange={e => setPriceStr(e.target.value)}
                className="tnum mt-1 h-9 w-full rounded-md border border-line bg-canvas px-3 font-mono text-sm font-semibold text-ink focus:border-ink focus:outline-none disabled:opacity-50"
              />
            </div>

            <div>
              <div className="flex items-center justify-between text-[11px]">
                <label className="font-medium text-secondary">Khối lượng (Lô 100 cp)</label>
                <span className="font-mono text-[10px] text-muted">
                  {side === "BUY" ? `Tối đa: ${fmt(maxBuyLots)} cp` : `Đang có: ${fmt(heldQty)} cp`}
                </span>
              </div>
              <input
                type="number"
                step={100}
                min={100}
                value={quantity}
                onChange={e => setQuantity(e.target.value)}
                className="tnum mt-1 h-9 w-full rounded-md border border-line bg-canvas px-3 font-mono text-sm font-semibold text-ink focus:border-ink focus:outline-none"
              />
            </div>
          </div>

          <div className="flex items-center justify-between gap-2 rounded-md border border-line/70 bg-canvas/50 px-3 py-2 text-[11px] whitespace-nowrap">
            <span className="shrink-0 text-secondary">
              {side === "BUY" ? "Phân bổ nhanh theo sức mua:" : "Phân bổ theo số lượng nắm giữ:"}
            </span>
            <div className="flex items-center gap-1.5 font-mono whitespace-nowrap">
              {[10, 25, 50, 100].map(pct => (
                <button
                  key={pct}
                  type="button"
                  onClick={() => applyBuyingPowerPct(pct)}
                  className="rounded border border-line bg-surface px-2 py-0.5 text-[10.5px] font-medium text-ink hover:bg-soft whitespace-nowrap"
                >
                  {pct}%
                </button>
              ))}
            </div>
          </div>

          <div className="rounded-lg border border-line bg-canvas/60 p-3.5 text-xs">
            <div className="flex items-center justify-between border-b border-line/70 pb-2">
              <span className="text-secondary">Giá trị khớp dự kiến (chưa phí):</span>
              <span className="tnum font-mono text-[14px] font-bold text-ink">{fmtVnd(orderValue)}</span>
            </div>
            <div className="mt-2 flex items-center justify-between text-[11.5px]">
              <span className="text-secondary">
                Phí môi giới (0,1% · tối thiểu 10.000 ₫){side === "SELL" ? " + Thuế bán (0,1%)" : ""}:
              </span>
              <span className="tnum font-mono text-secondary">
                {fmtVnd(estBrokerageFee + estSellTax)}
              </span>
            </div>
            <div className="mt-1.5 flex items-center justify-between text-[12px] font-semibold">
              <span className="text-ink">
                {side === "BUY" ? "Tổng tiền thanh toán từ số dư:" : "Tiền ròng thu về sau phí & thuế:"}
              </span>
              <span className={`tnum font-mono ${side === "BUY" ? "text-ink" : "text-gain"}`}>
                {fmtVnd(netCashImpact)}
              </span>
            </div>
          </div>

          {feedback && (
            <div
              role="status"
              className={`rounded-md border px-3 py-2 text-xs font-medium ${
                feedback.tone === "gain"
                  ? "border-gain/35 bg-gain/10 text-gain"
                  : "border-loss/35 bg-loss/10 text-loss"
              }`}
            >
              {feedback.text}
            </div>
          )}
        </div>

        <div className="flex items-center justify-between gap-3 border-t border-line bg-canvas/80 px-5 py-3 whitespace-nowrap">
          <Link to={`/trade?symbol=${encodeURIComponent(symbol)}`} className="text-[11.5px] font-medium text-secondary hover:text-ink hover:underline">
            Xem sổ lệnh thị trường của {symbol || "mã này"} →
          </Link>
          <div className="flex shrink-0 items-center gap-2 whitespace-nowrap">
            <Button variant="ghost" onClick={onClose}>
              Hủy
            </Button>
            <Button variant="primary" onClick={() => void handleSubmit()} disabled={submitting}>
              {submitting ? "Đang gửi lệnh…" : `Xác nhận ${side === "BUY" ? "MUA" : "BÁN"} ${symbol.toUpperCase()}`}
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}

export default function Portfolio() {
  const snapshotResource = useResource(() => portfolioApi.snapshot() as Promise<PortfolioSnapshot>, [])
  const snapshot = usePortfolio(snapshotResource.data, snapshotResource.reload)
  const summary = snapshot?.summary

  const [ledgerTab, setLedgerTab] = useState<"positions" | "roundtrips" | "orders">("positions")
  const [selectedSectorFilter, setSelectedSectorFilter] = useState<string | null>(null)
  const [orderSideFilter, setOrderSideFilter] = useState<"ALL" | "BUY" | "SELL">("ALL")
  const [orderSearch, setOrderSearch] = useState("")
  const [orderLimit, setOrderLimit] = useState(15)
  const [selectedTradeForAudit, setSelectedTradeForAudit] = useState<ClosedStockTrade | null>(null)
  const [charterModalOpen, setCharterModalOpen] = useState(false)
  const [charterOverride, setCharterOverride] = useState<PersonalCharter | null>(null)
  const [orderModalState, setOrderModalState] = useState<{
    open: boolean
    symbol: string
    side?: "BUY" | "SELL"
    price?: number
  }>({ open: false, symbol: "FPT" })

  const accountId = summary?.accountId || "default"
  const charter = useMemo<PersonalCharter>(() => {
    if (charterOverride) return charterOverride
    if (typeof window === "undefined") return DEFAULT_CHARTER
    try {
      const saved = window.localStorage.getItem(`aiinvest:charter:${accountId}`)
      if (saved) {
        const parsed = JSON.parse(saved) as Partial<PersonalCharter>
        return {
          styleTarget: parsed.styleTarget ?? DEFAULT_CHARTER.styleTarget,
          favoriteSectors: Array.isArray(parsed.favoriteSectors)
            ? parsed.favoriteSectors
            : DEFAULT_CHARTER.favoriteSectors,
          maxPosWeightPct: Number(parsed.maxPosWeightPct) || DEFAULT_CHARTER.maxPosWeightPct,
          maxStopLossPct: Number(parsed.maxStopLossPct) || DEFAULT_CHARTER.maxStopLossPct,
        }
      }
    } catch {
      // Ignore storage error
    }
    return DEFAULT_CHARTER
  }, [accountId, charterOverride])

  const saveCharter = (next: PersonalCharter) => {
    setCharterOverride(next)
    try {
      window.localStorage.setItem(`aiinvest:charter:${accountId}`, JSON.stringify(next))
    } catch {
      // Ignore storage error
    }
  }

  const rawOrders = useMemo(() => snapshot?.orders ?? [], [snapshot?.orders])
  const {
    normalizedOrders,
    closedTrades,
    buyCount,
    sellCount,
    totalGrossWinVnd,
    totalCutLossVnd,
  } = useMemo(() => normalizeOrdersAndRoundTrips(rawOrders), [rawOrders])

  // Personal symbols from user's own holdings + historical orders (zero AI War Room data)
  const personalSymbols = useMemo(() => {
    const set = new Set<string>()
    for (const p of snapshot?.positions ?? []) {
      if (p.symbol) set.add(p.symbol.toUpperCase())
    }
    for (const c of closedTrades) {
      if (c.sym) set.add(c.sym.toUpperCase())
    }
    return Array.from(set)
  }, [snapshot?.positions, closedTrades])

  // Sector Attribution (combines open positions + closed trades for radial ecosystem ring)
  const sectorEcosystem = useMemo<SectorAttributionItem[]>(() => {
    const map = new Map<
      string,
      {
        sectorCode: string
        sectorLabel: string
        symbols: Set<string>
        tradesCount: number
        winCount: number
        totalCount: number
        capitalVnd: number
        pnlVnd: number
      }
    >()

    let totalCapital = 0

    for (const p of snapshot?.positions ?? []) {
      const sectorCode = resolveSectorCode(p.symbol, p.sector)
      const cap = p.marketValue ?? p.costBasis ?? p.avgPrice * p.quantity
      totalCapital += cap
      const existing = map.get(sectorCode) ?? {
        sectorCode,
        sectorLabel: formatSectorName(sectorCode),
        symbols: new Set<string>(),
        tradesCount: 0,
        winCount: 0,
        totalCount: 0,
        capitalVnd: 0,
        pnlVnd: 0,
      }
      existing.symbols.add(p.symbol)
      existing.tradesCount += 1
      existing.totalCount += 1
      if ((p.pnl ?? 0) >= 0) existing.winCount += 1
      existing.capitalVnd += cap
      existing.pnlVnd += p.pnl ?? 0
      map.set(sectorCode, existing)
    }

    for (const t of closedTrades) {
      totalCapital += t.investedVnd
      const existing = map.get(t.sectorCode) ?? {
        sectorCode: t.sectorCode,
        sectorLabel: t.sectorLabel,
        symbols: new Set<string>(),
        tradesCount: 0,
        winCount: 0,
        totalCount: 0,
        capitalVnd: 0,
        pnlVnd: 0,
      }
      existing.symbols.add(t.sym)
      existing.tradesCount += t.tradesCount
      existing.totalCount += 1
      if (t.pnlVnd > 0) existing.winCount += 1
      existing.capitalVnd += t.investedVnd
      existing.pnlVnd += t.pnlVnd
      map.set(t.sectorCode, existing)
    }

    const sorted = Array.from(map.values()).sort((a, b) => b.pnlVnd - a.pnlVnd)
    return sorted.map((item, idx) => ({
      sectorCode: item.sectorCode,
      sectorLabel: item.sectorLabel,
      color: EDITORIAL_SECTOR_COLORS[idx % EDITORIAL_SECTOR_COLORS.length],
      symbols: Array.from(item.symbols),
      tradesCount: item.tradesCount,
      winCount: item.winCount,
      totalCount: item.totalCount,
      capitalVnd: item.capitalVnd,
      weightPct: totalCapital > 0 ? (item.capitalVnd / totalCapital) * 100 : 0,
      pnlVnd: item.pnlVnd,
      retPct: item.capitalVnd > 0 ? (item.pnlVnd / item.capitalVnd) * 100 : 0,
    }))
  }, [snapshot?.positions, closedTrades])

  // Monthly breakdown computed strictly from the user's equityCurve
  const openingCash = n(summary?.openingCash)
  const monthlyStats = useMemo(() => {
    const curve = snapshot?.performance?.equityCurve ?? []
    if (curve.length < 2) return []
    const monthsMap: Record<string, { first: number; last: number; count: number }> = {}
    for (const pt of curve) {
      const m = String(pt.date || "").slice(0, 7)
      if (!m || !Number.isFinite(pt.value)) continue
      if (!monthsMap[m]) {
        monthsMap[m] = { first: pt.value, last: pt.value, count: 0 }
      }
      monthsMap[m].last = pt.value
      monthsMap[m].count += 1
    }
    const entries = Object.entries(monthsMap).sort((a, b) => a[0].localeCompare(b[0]))
    let prevEnd = openingCash ?? entries[0]?.[1]?.first ?? 0
    const result: {
      label: string
      sessions: number
      endNav: number
      deltaVnd: number
      retPct: number
    }[] = []
    for (const [ym, item] of entries) {
      const deltaVnd = item.last - prevEnd
      const retPct = prevEnd > 0 ? (deltaVnd / prevEnd) * 100 : 0
      prevEnd = item.last
      const [yr, mo] = ym.split("-")
      result.push({
        label: `Tháng ${mo}/${yr?.slice(2)}`,
        sessions: item.count,
        endNav: item.last,
        deltaVnd,
        retPct,
      })
    }
    return result
  }, [snapshot?.performance?.equityCurve, openingCash])

  if (
    (!snapshotResource.data && snapshotResource.loading) ||
    (!snapshotResource.data && snapshotResource.error) ||
    !snapshot ||
    !summary
  ) {
    return (
      <Page title="Danh mục đầu tư">
        <DataState
          loading={snapshotResource.loading}
          error={snapshotResource.error}
          empty={!snapshotResource.loading && !snapshotResource.data}
          retry={() => void snapshotResource.reload()}
        >
          <></>
        </DataState>
      </Page>
    )
  }

  const rawSummaryObj = summary as unknown as Record<string, unknown>
  const navVnd = n(summary.nav)
  const cashVnd = n(summary.cash) ?? 0
  const marketValueVnd = n(rawSummaryObj.marketValue) ?? (navVnd !== null ? Math.max(0, navVnd - cashVnd) : null)
  const totalCostVnd = n(summary.totalCost)
  const realizedPnl = n(summary.realizedPnl)
  const unrealizedPnl = n(summary.unrealizedPnl)
  const totalPnl = n(summary.totalPnl)
  const totalReturnPct = n(summary.totalReturnPct)
  const dailyPnL = n(summary.dailyPnL)
  const dailyPnLPercent = n(summary.dailyPnLPercent)
  const feesPaid = n(rawSummaryObj.fees)

  const sharpe = n(snapshot.risks?.sharpe)
  const alpha = n(snapshot.risks?.alpha)
  const beta = n(snapshot.risks?.beta)
  const maxDrawdown = n(snapshot.risks?.maxDrawdown)

  const winningTrades = closedTrades.filter(t => t.pnlVnd > 0)
  const losingTrades = closedTrades.filter(t => t.pnlVnd < 0)
  const winRatePct = closedTrades.length > 0 ? (winningTrades.length / closedTrades.length) * 100 : null
  const avgWinVnd = winningTrades.length > 0 ? Math.round(totalGrossWinVnd / winningTrades.length) : null
  const avgLossVnd = losingTrades.length > 0 ? Math.round(Math.abs(totalCutLossVnd) / losingTrades.length) : null
  const payoffAsymmetry = avgWinVnd && avgLossVnd && avgLossVnd > 0 ? avgWinVnd / avgLossVnd : null
  const profitFactor =
    totalCutLossVnd < 0 ? totalGrossWinVnd / Math.abs(totalCutLossVnd) : null
  const worstCutLossVnd =
    losingTrades.length > 0 ? Math.min(...losingTrades.map(t => t.pnlVnd)) : null

  const filteredPositions = snapshot.positions.filter(pos => {
    if (!selectedSectorFilter) return true
    return resolveSectorCode(pos.symbol, pos.sector) === selectedSectorFilter
  })

  const filteredClosedTrades = closedTrades.filter(t => {
    if (!selectedSectorFilter) return true
    return t.sectorCode === selectedSectorFilter
  })

  const filteredOrders = normalizedOrders.filter(ord => {
    if (selectedSectorFilter && ord.sectorCode !== selectedSectorFilter) return false
    if (orderSideFilter !== "ALL" && ord.side !== orderSideFilter) return false
    if (orderSearch.trim() && !ord.symbol.toLowerCase().includes(orderSearch.trim().toLowerCase())) return false
    return true
  })

  const exportLedgerCsv = () => {
    const header = ["Mã CP", "Ngành", "Chiều", "Loại lệnh", "Khối lượng", "Giá khớp (VND)", "Tổng giá trị (VND)", "Trạng thái", "Thời gian"]
    const rows = normalizedOrders.map(o => [
      o.symbol,
      o.sectorLabel,
      o.side === "BUY" ? "MUA" : "BÁN",
      o.orderType,
      String(o.quantity),
      String(o.price),
      String(o.totalValue),
      o.status,
      fmtVietnamDateTime(o.createdAt),
    ])
    const csvContent =
      "\uFEFF" +
      [header, ...rows].map(r => r.map(cell => `"${String(cell).replace(/"/g, '""')}"`).join(",")).join("\n")
    const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" })
    const url = URL.createObjectURL(blob)
    const link = document.createElement("a")
    link.href = url
    link.download = `sao-ke-danh-muc-${snapshot.performance.asOf || "ca-nhan"}.csv`
    link.click()
    URL.revokeObjectURL(url)
  }

  return (
    <Page
      title="Danh mục Đầu tư Cá nhân"
      sub={`Tài sản ròng thời gian thực, cơ cấu nắm giữ (${snapshot.positions.length} vị thế mở), quang phổ ngành và sổ cái ${normalizedOrders.length} lệnh khớp`}
      actions={
        <div className="flex shrink-0 items-center gap-2 whitespace-nowrap">
          <Button variant="ghost" onClick={() => setCharterModalOpen(true)}>
            Luật Cá nhân
          </Button>
          {normalizedOrders.length > 0 && (
            <Button variant="ghost" onClick={exportLedgerCsv}>
              Xuất Sao kê (.CSV)
            </Button>
          )}
          <Button variant="ghost" onClick={() => void snapshotResource.reload()}>
            Làm mới
          </Button>
          <Button
            variant="primary"
            onClick={() =>
              setOrderModalState({
                open: true,
                symbol: personalSymbols[0] || "FPT",
                side: "BUY",
              })
            }
          >
            Đặt lệnh giao dịch
          </Button>
        </div>
      }
    >
      <div className="space-y-6">
        {/* MASTER BLOCK 1 (GIỮ NGUYÊN BẢN TỐT): Personal Wealth Stage (8/12) + Personal Accounting Receipt & Risk Metrics (4/12) */}
        <Panel flush className="overflow-hidden">
          <div className="grid divide-y divide-line lg:grid-cols-12 lg:divide-x lg:divide-y-0">
            <PersonalWealthStage
              curvePoints={snapshot.performance.equityCurve}
              benchmarkPoints={snapshot.performance.benchmarkCurve}
              alphaPct={alpha}
              openingCash={openingCash}
              currentNav={navVnd}
              cashVnd={cashVnd}
              marketValueVnd={marketValueVnd}
              positionsCount={snapshot.positions.length}
              totalPnl={totalPnl}
              totalReturnPct={totalReturnPct}
              dailyPnL={dailyPnL}
              dailyPnLPercent={dailyPnLPercent}
            />

            {/* Right 4/12: Personal Capital Accounting Receipt, Monthly Breakdown & Risk Metrics */}
            <div className="flex flex-col justify-between bg-canvas/25 p-5 lg:col-span-4">
              <div>
                <div className="flex items-center justify-between">
                  <span className="text-[11px] font-semibold tracking-wider text-muted uppercase">
                    Phiếu Đối Soát Tài Sản Cá Nhân
                  </span>
                  <span
                    className={`font-mono text-[10.5px] font-medium ${
                      summary.ledgerComplete ? "text-gain" : "text-warning"
                    }`}
                  >
                    {summary.ledgerComplete ? "Sổ cái khớp 100%" : "Đang đồng bộ chứng từ"}
                  </span>
                </div>

                <div className="mt-3 space-y-2 rounded-lg border border-line bg-surface p-3.5 text-[12px]">
                  <div className="flex items-baseline justify-between gap-2 whitespace-nowrap">
                    <span className="text-secondary">Vốn gốc ban đầu</span>
                    <span className="flex-1 border-b border-dotted border-line/80" />
                    <span className="tnum font-mono font-semibold text-ink">{fmtVnd(openingCash)}</span>
                  </div>

                  <div className="flex items-baseline justify-between gap-2 whitespace-nowrap">
                    <span className="text-secondary">Tiền mặt khả dụng</span>
                    <span className="flex-1 border-b border-dotted border-line/80" />
                    <span className="tnum font-mono font-semibold text-ink">{fmtVnd(cashVnd)}</span>
                  </div>

                  <div className="flex items-baseline justify-between gap-2 whitespace-nowrap">
                    <span className="text-secondary">
                      Giá trị cổ phiếu ({snapshot.positions.length} mã)
                    </span>
                    <span className="flex-1 border-b border-dotted border-line/80" />
                    <span className="tnum font-mono font-semibold text-ink">{fmtVnd(marketValueVnd)}</span>
                  </div>

                  <div className="flex items-baseline justify-between gap-2 whitespace-nowrap">
                    <span className="text-secondary">Lãi / lỗ vị thế đang mở</span>
                    <span className="flex-1 border-b border-dotted border-line/80" />
                    <span
                      className={`tnum font-mono font-medium ${
                        (unrealizedPnl ?? 0) > 0
                          ? "text-gain"
                          : (unrealizedPnl ?? 0) < 0
                            ? "text-loss"
                            : "text-ink"
                      }`}
                    >
                      {signedVnd(unrealizedPnl)}
                    </span>
                  </div>

                  <div className="flex items-baseline justify-between gap-2 whitespace-nowrap">
                    <span className="text-secondary">Lãi / lỗ đã chốt (sau phí &amp; thuế)</span>
                    <span className="flex-1 border-b border-dotted border-line/80" />
                    <span
                      className={`tnum font-mono font-semibold ${
                        (realizedPnl ?? 0) > 0
                          ? "text-gain"
                          : (realizedPnl ?? 0) < 0
                            ? "text-loss"
                            : "text-ink"
                      }`}
                    >
                      {signedVnd(realizedPnl)}
                    </span>
                  </div>

                  {feesPaid !== null && feesPaid > 0 && (
                    <div className="flex items-baseline justify-between gap-2 text-[11px] whitespace-nowrap">
                      <span className="text-muted">Trong đó tổng phí &amp; thuế đã nộp</span>
                      <span className="flex-1 border-b border-dotted border-line/60" />
                      <span className="tnum font-mono text-secondary">-{fmtVnd(feesPaid)}</span>
                    </div>
                  )}

                  <div className="flex items-baseline justify-between gap-2 border-t-2 border-double border-line pt-2.5 whitespace-nowrap">
                    <span className="font-semibold text-ink">Tổng Lãi / Lỗ ròng tài khoản</span>
                    <span
                      className={`tnum font-mono text-[15.5px] font-bold ${
                        (totalPnl ?? 0) > 0 ? "text-gain" : (totalPnl ?? 0) < 0 ? "text-loss" : "text-ink"
                      }`}
                    >
                      {signedVnd(totalPnl)}
                    </span>
                  </div>
                </div>

                {/* Monthly Performance Breakdown from User's Equity Curve */}
                <div className="mt-4">
                  <div className="mb-2 flex items-center justify-between">
                    <span className="text-[11px] font-semibold tracking-wider text-muted uppercase">
                      Hiệu suất theo tháng
                    </span>
                    <span className="font-mono text-[10px] text-muted">
                      {snapshot.performance.equityCurve.length} phiên
                    </span>
                  </div>
                  {monthlyStats.length > 0 ? (
                    <div className="grid grid-cols-2 gap-2">
                      {monthlyStats.slice(-4).map(m => (
                        <div
                          key={m.label}
                          className="flex items-center justify-between rounded-md border border-line/75 bg-surface px-2.5 py-1.5 text-[11.5px] whitespace-nowrap"
                        >
                          <div>
                            <div className="font-medium text-ink">{m.label}</div>
                            <div className="font-mono text-[9.5px] text-muted">{m.sessions} phiên</div>
                          </div>
                          <div className="text-right">
                            <div
                              className={`tnum font-mono font-semibold ${
                                m.retPct > 0 ? "text-gain" : m.retPct < 0 ? "text-loss" : "text-ink"
                              }`}
                            >
                              {signedPct(m.retPct)}
                            </div>
                            <div className="tnum font-mono text-[9.5px] text-secondary">
                              {shortVnd(m.deltaVnd, true)}
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="rounded-md border border-dashed border-line bg-surface px-3 py-2.5 text-xs text-secondary">
                      Chưa đủ dữ liệu lịch sử NAV theo tháng.
                    </div>
                  )}
                </div>
              </div>

              {/* 4 Quantitative Risk Metrics of the Personal Portfolio */}
              <div className="mt-4 border-t border-line pt-3.5">
                <div className="mb-2 flex items-center justify-between">
                  <span className="text-[11px] font-semibold tracking-wider text-muted uppercase">
                    Chỉ số rủi ro danh mục cá nhân
                  </span>
                  <span className="font-mono text-[10px] text-muted">
                    {snapshot.performance.asOf ? `Đến ${fmtShortDate(snapshot.performance.asOf)}` : "Hiện tại"}
                  </span>
                </div>
                <div className="grid grid-cols-4 gap-2 text-center">
                  <div className="rounded-md border border-line/75 bg-surface px-2 py-2">
                    <div className="text-[10px] text-muted">Sharpe</div>
                    <div className="tnum mt-0.5 font-mono text-[14px] font-bold text-ink">
                      {sharpe !== null ? sharpe.toFixed(2) : "—"}
                    </div>
                  </div>
                  <div className="rounded-md border border-line/75 bg-surface px-2 py-2">
                    <div className="text-[10px] text-muted">Alpha</div>
                    <div
                      className={`tnum mt-0.5 font-mono text-[14px] font-bold ${
                        (alpha ?? 0) > 0 ? "text-gain" : (alpha ?? 0) < 0 ? "text-loss" : "text-ink"
                      }`}
                    >
                      {alpha !== null ? `${alpha > 0 ? "+" : ""}${alpha.toFixed(2)}%` : "—"}
                    </div>
                  </div>
                  <div className="rounded-md border border-line/75 bg-surface px-2 py-2">
                    <div className="text-[10px] text-muted">Beta</div>
                    <div className="tnum mt-0.5 font-mono text-[14px] font-bold text-ink">
                      {beta !== null ? beta.toFixed(2) : "—"}
                    </div>
                  </div>
                  <div className="rounded-md border border-line/75 bg-surface px-2 py-2">
                    <div className="text-[10px] text-muted">Max DD</div>
                    <div className="tnum mt-0.5 font-mono text-[14px] font-bold text-ink">
                      {maxDrawdown !== null ? `-${Math.abs(maxDrawdown).toFixed(2)}%` : "—"}
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </Panel>

        {/* MASTER BLOCK 2 (GIỮ NGUYÊN BẢN TỐT): Cơ cấu Nắm giữ & Sức mua Tài khoản (6/12) + Thống kê Hiệu quả Giao dịch Đã chốt (6/12) */}
        <div className="grid gap-6 lg:grid-cols-12">
          {/* Left 6/12: Personal Holdings & Buying Power Allocation */}
          <Panel className="flex flex-col justify-between p-5 lg:col-span-6">
            <div>
              <div className="flex items-center justify-between gap-2">
                <div>
                  <h2 className="text-[15px] font-semibold text-ink">
                    Cơ cấu Nắm giữ &amp; Sức mua Tài khoản
                  </h2>
                  <p className="mt-0.5 text-xs text-secondary">
                    Phân bổ thực tế giữa tiền mặt khả dụng và các vị thế cổ phiếu đang nắm giữ của bạn
                  </p>
                </div>
                <Pill tone={snapshot.positions.length > 0 ? "mineral" : "neutral"}>
                  {snapshot.positions.length} mã đang giữ
                </Pill>
              </div>

              <div className="mt-3.5 grid grid-cols-3 gap-2.5">
                <div className="rounded-lg border border-line/80 bg-canvas/50 px-3 py-2">
                  <div className="text-[10.5px] text-muted">Tiền mặt khả dụng</div>
                  <div className="tnum mt-0.5 font-mono text-[14px] font-bold text-ink">
                    {shortVnd(cashVnd)}
                  </div>
                  <div className="text-[10px] text-secondary">
                    {navVnd && navVnd > 0 ? `${((cashVnd / navVnd) * 100).toFixed(1)}% NAV` : "Sẵn sàng đặt lệnh"}
                  </div>
                </div>

                <div className="rounded-lg border border-line/80 bg-canvas/50 px-3 py-2">
                  <div className="text-[10.5px] text-muted">Giá vốn đang giữ</div>
                  <div className="tnum mt-0.5 font-mono text-[14px] font-bold text-ink">
                    {totalCostVnd !== null ? shortVnd(totalCostVnd) : "0 ₫"}
                  </div>
                  <div className="text-[10px] text-secondary">
                    Thị giá: {marketValueVnd !== null ? shortVnd(marketValueVnd) : "0 ₫"}
                  </div>
                </div>

                <div className="rounded-lg border border-line/80 bg-canvas/50 px-3 py-2">
                  <div className="text-[10.5px] text-muted">Lãi / Lỗ tạm tính</div>
                  <div
                    className={`tnum mt-0.5 font-mono text-[14px] font-bold ${
                      (unrealizedPnl ?? 0) > 0
                        ? "text-gain"
                        : (unrealizedPnl ?? 0) < 0
                          ? "text-loss"
                          : "text-ink"
                    }`}
                  >
                    {unrealizedPnl !== null ? shortVnd(unrealizedPnl, true) : "0 ₫"}
                  </div>
                  <div className="text-[10px] text-secondary">
                    {snapshot.positions.length > 0 ? "Cập nhật theo giá khớp" : "Không có vị thế mở"}
                  </div>
                </div>
              </div>

              {snapshot.positions.length > 0 ? (
                <div className="mt-3.5 overflow-hidden rounded-lg border border-line">
                  <table className="w-full text-[12px]">
                    <thead>
                      <tr className="border-b border-line bg-canvas/70 text-[10px] font-semibold text-muted uppercase whitespace-nowrap">
                        <th className="px-3 py-2 text-left">Mã CP</th>
                        <th className="px-2 py-2 text-right">Khối lượng</th>
                        <th className="px-2 py-2 text-right">Giá vốn → Thị giá</th>
                        <th className="px-2 py-2 text-right">Tỷ trọng</th>
                        <th className="px-2 py-2 text-right">Lãi / Lỗ</th>
                        <th className="px-3 py-2 text-right">Lệnh</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-line/60">
                      {snapshot.positions.slice(0, 5).map(pos => {
                        const entry =
                          pos.costBasis !== null && pos.quantity > 0 ? pos.costBasis / pos.quantity : pos.avgPrice
                        const isPosWin = (pos.pnl ?? 0) >= 0
                        return (
                          <tr key={pos.symbol} className="hover:bg-soft/35 whitespace-nowrap">
                            <td className="px-3 py-2 font-mono font-bold text-ink">{pos.symbol}</td>
                            <td className="tnum px-2 py-2 text-right font-mono text-ink">{fmt(pos.quantity)} cp</td>
                            <td className="tnum px-2 py-2 text-right font-mono text-secondary">
                              {fmt(entry)} → <strong className="text-ink">{fmt(pos.currentPrice)}</strong>
                            </td>
                            <td className="tnum px-2 py-2 text-right font-mono text-ink">
                              {pos.weight !== null ? `${pos.weight.toFixed(1)}%` : "—"}
                            </td>
                            <td
                              className={`tnum px-2 py-2 text-right font-mono font-semibold ${
                                isPosWin ? "text-gain" : "text-loss"
                              }`}
                            >
                              {signedPct(pos.pnlPercent)}
                            </td>
                            <td className="px-3 py-2 text-right">
                              <button
                                type="button"
                                onClick={() =>
                                  setOrderModalState({
                                    open: true,
                                    symbol: pos.symbol,
                                    side: "SELL",
                                    price: pos.currentPrice ?? entry,
                                  })
                                }
                                className="rounded border border-line bg-canvas px-2 py-0.5 text-[11px] font-medium text-ink hover:bg-soft"
                              >
                                Giao dịch
                              </button>
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="mt-3.5 rounded-lg border border-dashed border-line bg-canvas/40 p-4">
                  <div className="text-xs font-semibold text-ink">
                    Tài khoản hiện không nắm giữ cổ phiếu nào (100% Tiền mặt: {fmtVnd(cashVnd)})
                  </div>
                  <p className="mt-1 text-xs text-secondary">
                    Toàn bộ số dư tiền mặt đang ở trạng thái sẵn sàng giải ngân. Bạn có thể đặt lệnh trực tiếp cho bất kỳ mã cổ phiếu nào trên sàn HOSE, HNX, UPCOM.
                  </p>
                  <div className="mt-3 flex items-center gap-2">
                    <Button
                      variant="primary"
                      onClick={() =>
                        setOrderModalState({
                          open: true,
                          symbol: personalSymbols[0] || "FPT",
                          side: "BUY",
                        })
                      }
                    >
                      Mở Phiếu Đặt Lệnh Cá Nhân
                    </Button>
                    <Link
                      to="/trade"
                      className="inline-flex h-8 items-center rounded-md border border-line bg-surface px-3 text-xs font-medium text-ink hover:bg-soft"
                    >
                      Sang Bảng giá Giao dịch →
                    </Link>
                  </div>
                </div>
              )}
            </div>

            <div className="mt-3 flex items-center justify-between gap-2 rounded-md border border-line/60 bg-canvas/55 px-3 py-2 text-[11px] text-secondary whitespace-nowrap">
              <span>
                Định giá theo thị giá khớp gần nhất ({summary.stalePrices.length === 0 ? "Realtime" : "Đóng cửa"})
              </span>
              <span className="shrink-0 font-mono font-semibold text-ink">
                Sức mua: {fmtVnd(cashVnd)}
              </span>
            </div>
          </Panel>

          {/* Right 6/12: Personal Realized Trade Statistics (Strictly from User's Own Orders) */}
          <Panel className="flex flex-col justify-between p-5 lg:col-span-6">
            <div>
              <div className="flex items-center justify-between gap-2">
                <div>
                  <h2 className="text-[15px] font-semibold text-ink">
                    Thống kê Hiệu quả Giao dịch Đã chốt
                  </h2>
                  <p className="mt-0.5 text-xs text-secondary">
                    Tổng hợp từ {normalizedOrders.length} lệnh khớp thực tế của tài khoản · Bấm vào mã CP để xem kiểm toán lệnh
                  </p>
                </div>
                {winRatePct !== null && <Pill tone="gain">Thắng {winRatePct.toFixed(1)}%</Pill>}
              </div>

              {closedTrades.length > 0 ? (
                <>
                  <div className="mt-3.5 grid grid-cols-3 gap-2.5">
                    <div className="rounded-lg border border-line/80 bg-canvas/50 px-3 py-2">
                      <div className="text-[10.5px] text-muted">Tỷ lệ mã có lãi</div>
                      <div className="tnum mt-0.5 font-mono text-[14px] font-bold text-ink">
                        {winningTrades.length} / {closedTrades.length} mã
                      </div>
                      <div className="text-[10px] font-medium text-gain">
                        Win Rate {winRatePct !== null ? `${winRatePct.toFixed(1)}%` : "—"}
                      </div>
                    </div>

                    <div className="rounded-lg border border-line/80 bg-canvas/50 px-3 py-2">
                      <div className="text-[10.5px] text-muted">Lãi gộp TB mỗi mã thắng</div>
                      <div className="tnum mt-0.5 font-mono text-[14px] font-bold text-gain">
                        {avgWinVnd !== null ? `+${shortVnd(avgWinVnd)}` : "—"}
                      </div>
                      <div className="text-[10px] text-secondary">
                        Tổng lãi gộp: +{shortVnd(totalGrossWinVnd)}
                      </div>
                    </div>

                    <div className="rounded-lg border border-line/80 bg-canvas/50 px-3 py-2">
                      <div className="text-[10.5px] text-muted">Cắt lỗ TB mỗi mã lỗ</div>
                      <div className="tnum mt-0.5 font-mono text-[14px] font-bold text-loss">
                        {avgLossVnd !== null ? `-${shortVnd(avgLossVnd)}` : "0 ₫"}
                      </div>
                      <div className="text-[10px] font-medium text-gain">
                        {payoffAsymmetry !== null
                          ? `Lãi TB gấp ${payoffAsymmetry.toFixed(1)}x lỗ TB`
                          : "Chưa có mã cắt lỗ"}
                      </div>
                    </div>
                  </div>

                  <div className="mt-3.5 grid grid-cols-2 gap-3">
                    <div className="rounded-lg border border-gain/25 bg-gain/[0.03] p-3">
                      <div className="mb-2 flex items-center justify-between text-[11px] font-semibold text-gain">
                        <span>Mã Chốt Lời ({winningTrades.length})</span>
                        <span className="font-mono">+{shortVnd(totalGrossWinVnd)}</span>
                      </div>
                      <div className="space-y-1.5">
                        {winningTrades.slice(0, 5).map(t => (
                          <button
                            key={t.sym}
                            type="button"
                            onClick={() => setSelectedTradeForAudit(t)}
                            className="flex w-full items-center justify-between rounded border border-line/70 bg-surface px-2.5 py-1.5 text-left text-[11.5px] transition-colors hover:border-gain hover:bg-soft/50 whitespace-nowrap"
                          >
                            <div className="flex items-center gap-1.5">
                              <span className="font-mono font-bold text-ink">{t.sym}</span>
                              <span className="rounded bg-gain/10 px-1 py-0.5 font-mono text-[9.5px] font-semibold text-gain">
                                +{t.retPct.toFixed(1)}%
                              </span>
                            </div>
                            <span className="tnum font-mono font-semibold text-gain">+{fmtVnd(t.pnlVnd)}</span>
                          </button>
                        ))}
                      </div>
                    </div>

                    <div className="rounded-lg border border-line bg-canvas/45 p-3">
                      <div className="mb-2 flex items-center justify-between text-[11px] font-semibold text-ink">
                        <span>Mã Cắt Lỗ ({losingTrades.length})</span>
                        <span className="font-mono text-loss">{signedVnd(totalCutLossVnd)}</span>
                      </div>
                      <div className="space-y-1.5">
                        {losingTrades.slice(0, 5).map(t => (
                          <button
                            key={t.sym}
                            type="button"
                            onClick={() => setSelectedTradeForAudit(t)}
                            className="flex w-full items-center justify-between rounded border border-line/70 bg-surface px-2.5 py-1.5 text-left text-[11.5px] transition-colors hover:border-loss/50 hover:bg-soft/50 whitespace-nowrap"
                          >
                            <div className="flex items-center gap-1.5">
                              <span className="font-mono font-bold text-ink">{t.sym}</span>
                              <span className="rounded bg-loss/10 px-1 py-0.5 font-mono text-[9.5px] font-semibold text-loss">
                                {t.retPct.toFixed(1)}%
                              </span>
                            </div>
                            <span className="tnum font-mono font-medium text-loss">{signedVnd(t.pnlVnd)}</span>
                          </button>
                        ))}
                      </div>
                    </div>
                  </div>
                </>
              ) : (
                <div className="mt-3.5 flex h-[210px] flex-col items-center justify-center rounded-lg border border-dashed border-line bg-canvas/40 p-6 text-center">
                  <div className="text-xs font-semibold text-ink">
                    Tài khoản chưa có thương vụ cổ phiếu nào đã tất toán
                  </div>
                  <p className="mt-1 max-w-sm text-xs text-secondary">
                    Khi bạn thực hiện mua và bán chốt lời/cắt lỗ cổ phiếu, hệ thống sẽ tự động thống kê tỷ lệ thắng và hiệu suất từng mã tại đây.
                  </p>
                </div>
              )}
            </div>

            <div className="mt-3 flex items-center justify-between gap-2 rounded-md border border-line/60 bg-canvas/55 px-3 py-2 text-[11px] text-secondary whitespace-nowrap">
              <span>
                {worstCutLossVnd !== null
                  ? `Mức cắt lỗ lớn nhất: ${signedVnd(worstCutLossVnd)}`
                  : "Chưa phát sinh giao dịch cắt lỗ"}
              </span>
              <span className="shrink-0 font-mono font-semibold text-ink">
                Profit Factor: {profitFactor !== null ? `${profitFactor.toFixed(1).replace(".", ",")}x` : "—"}
              </span>
            </div>
          </Panel>
        </div>

        {/* MASTER BLOCK 3 (THÊM MỚI ĐỘC BẢN): Radial Sector Ecosystem Ring (5/12) + Trade Horizon × Return Bubble Matrix (7/12) */}
        <div className="grid gap-6 lg:grid-cols-12">
          <RadialSectorStudio
            sectors={sectorEcosystem}
            selectedSector={selectedSectorFilter}
            onSelectSector={setSelectedSectorFilter}
            favoriteSectors={charter.favoriteSectors}
          />

          <TradingStyleBubbleMatrix
            closedTrades={closedTrades}
            charter={charter}
            onSelectTrade={setSelectedTradeForAudit}
            onOpenCharter={() => setCharterModalOpen(true)}
          />
        </div>

        {/* MASTER BLOCK 4: Personal Portfolio Ledger (3-Mode Interactive Table with Sector Cross-Filter) */}
        <Panel flush className="overflow-hidden">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-3.5">
            <div className="flex flex-wrap items-center gap-2">
              <div className="inline-flex rounded-lg border border-line bg-canvas p-1 text-xs">
                <button
                  type="button"
                  onClick={() => setLedgerTab("positions")}
                  className={`rounded-md px-3 py-1.5 font-medium transition-colors ${
                    ledgerTab === "positions"
                      ? "bg-ink text-paper shadow-2xs"
                      : "text-secondary hover:text-ink"
                  }`}
                >
                  1. Vị thế đang nắm giữ ({filteredPositions.length})
                </button>
                <button
                  type="button"
                  onClick={() => setLedgerTab("roundtrips")}
                  className={`rounded-md px-3 py-1.5 font-medium transition-colors ${
                    ledgerTab === "roundtrips"
                      ? "bg-ink text-paper shadow-2xs"
                      : "text-secondary hover:text-ink"
                  }`}
                >
                  2. Tổng kết theo Mã đã chốt ({filteredClosedTrades.length} mã)
                </button>
                <button
                  type="button"
                  onClick={() => setLedgerTab("orders")}
                  className={`rounded-md px-3 py-1.5 font-medium transition-colors ${
                    ledgerTab === "orders"
                      ? "bg-ink text-paper shadow-2xs"
                      : "text-secondary hover:text-ink"
                  }`}
                >
                  3. Sổ khớp lệnh chi tiết ({filteredOrders.length} lệnh)
                </button>
              </div>

              {selectedSectorFilter && (
                <button
                  type="button"
                  onClick={() => setSelectedSectorFilter(null)}
                  className="inline-flex items-center gap-1.5 rounded-md border border-gain/40 bg-gain/10 px-2.5 py-1 text-xs font-semibold text-gain hover:bg-gain/15"
                >
                  <span>Đang lọc ngành: {formatSectorName(selectedSectorFilter)}</span>
                  <span>×</span>
                </button>
              )}
            </div>

            {ledgerTab === "orders" && normalizedOrders.length > 0 && (
              <div className="flex flex-wrap items-center gap-2">
                <div className="inline-flex rounded-md border border-line bg-canvas p-0.5 text-[11px]">
                  <button
                    type="button"
                    onClick={() => setOrderSideFilter("ALL")}
                    className={`rounded px-2.5 py-1 font-medium ${
                      orderSideFilter === "ALL" ? "bg-surface font-semibold text-ink shadow-2xs" : "text-muted"
                    }`}
                  >
                    Tất cả ({normalizedOrders.length})
                  </button>
                  <button
                    type="button"
                    onClick={() => setOrderSideFilter("BUY")}
                    className={`rounded px-2.5 py-1 font-medium ${
                      orderSideFilter === "BUY" ? "bg-surface font-semibold text-mineral shadow-2xs" : "text-muted"
                    }`}
                  >
                    Mua ({buyCount})
                  </button>
                  <button
                    type="button"
                    onClick={() => setOrderSideFilter("SELL")}
                    className={`rounded px-2.5 py-1 font-medium ${
                      orderSideFilter === "SELL" ? "bg-surface font-semibold text-gain shadow-2xs" : "text-muted"
                    }`}
                  >
                    Bán ({sellCount})
                  </button>
                </div>

                <input
                  value={orderSearch}
                  onChange={e => setOrderSearch(e.target.value)}
                  placeholder="Tìm mã CP…"
                  aria-label="Lọc lịch sử lệnh theo mã cổ phiếu"
                  className="h-8 w-36 rounded-md border border-line bg-canvas px-2.5 text-xs text-ink focus:border-ink focus:outline-none"
                />
              </div>
            )}
          </div>

          {/* TAB 1: Open Positions */}
          {ledgerTab === "positions" && (
            <div className="overflow-x-auto">
              {filteredPositions.length > 0 ? (
                <table className="w-full min-w-[860px] text-[13px]">
                  <thead>
                    <tr className="border-b border-line bg-canvas/50 text-[10.5px] font-semibold tracking-wider text-muted uppercase">
                      <th className="py-2.5 pl-5 text-left">Mã CP &amp; Ngành</th>
                      <th className="px-3 py-2.5 text-right">Khối lượng</th>
                      <th className="px-3 py-2.5 text-right">Giá vốn TB</th>
                      <th className="px-3 py-2.5 text-right">Thị giá</th>
                      <th className="px-3 py-2.5 text-right">Giá trị thị trường</th>
                      <th className="px-3 py-2.5 text-right">Tỷ trọng NAV</th>
                      <th className="px-3 py-2.5 text-right">Lãi / Lỗ (₫)</th>
                      <th className="py-2.5 pr-5 text-right">Hành động</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line/70">
                    {filteredPositions.map(pos => {
                      const entryPrice =
                        pos.costBasis !== null && pos.quantity > 0 ? pos.costBasis / pos.quantity : pos.avgPrice
                      const isPosWin = (pos.pnl ?? 0) >= 0
                      const secLabel = formatSectorName(resolveSectorCode(pos.symbol, pos.sector))
                      return (
                        <tr key={pos.symbol} className="hover:bg-soft/45 whitespace-nowrap">
                          <td className="py-3 pl-5">
                            <div className="flex items-center gap-2">
                              <span className="font-mono font-bold text-ink">{pos.symbol}</span>
                              <span className="rounded border border-line bg-canvas px-1.5 py-0.5 text-[10.5px] text-secondary">
                                {secLabel}
                              </span>
                            </div>
                          </td>
                          <td className="tnum px-3 py-3 text-right font-mono text-ink">{fmt(pos.quantity)} cp</td>
                          <td className="tnum px-3 py-3 text-right font-mono text-secondary">{fmtVnd(entryPrice)}</td>
                          <td className="tnum px-3 py-3 text-right font-mono text-ink">{fmtVnd(pos.currentPrice)}</td>
                          <td className="tnum px-3 py-3 text-right font-mono text-ink">{fmtVnd(pos.marketValue)}</td>
                          <td className="tnum px-3 py-3 text-right font-mono text-ink">
                            {pos.weight !== null ? `${pos.weight.toFixed(2)}%` : "—"}
                          </td>
                          <td
                            className={`tnum px-3 py-3 text-right font-mono font-bold ${
                              isPosWin ? "text-gain" : "text-loss"
                            }`}
                          >
                            {signedVnd(pos.pnl)} ({signedPct(pos.pnlPercent)})
                          </td>
                          <td className="py-3 pr-5 text-right">
                            <div className="inline-flex items-center gap-1.5">
                              <button
                                type="button"
                                onClick={() =>
                                  setOrderModalState({
                                    open: true,
                                    symbol: pos.symbol,
                                    side: "BUY",
                                    price: pos.currentPrice ?? entryPrice,
                                  })
                                }
                                className="rounded border border-line bg-canvas px-2.5 py-1 text-xs font-medium text-ink hover:bg-soft"
                              >
                                Mua thêm
                              </button>
                              <button
                                type="button"
                                onClick={() =>
                                  setOrderModalState({
                                    open: true,
                                    symbol: pos.symbol,
                                    side: "SELL",
                                    price: pos.currentPrice ?? entryPrice,
                                  })
                                }
                                className="rounded border border-loss/35 bg-loss/10 px-2.5 py-1 text-xs font-medium text-loss hover:bg-loss/20"
                              >
                                Bán
                              </button>
                            </div>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              ) : (
                <div className="flex flex-col items-center justify-between gap-4 p-6 text-center sm:flex-row sm:text-left">
                  <div>
                    <div className="text-sm font-semibold text-ink">
                      Danh mục hiện không có vị thế cổ phiếu đang mở (Số dư tiền mặt: {fmtVnd(cashVnd)})
                    </div>
                    <p className="mt-0.5 text-xs text-secondary">
                      {closedTrades.length > 0 ? (
                        <>
                          Tài khoản của bạn đã tất toán toàn bộ {closedTrades.length} mã cổ phiếu trước đó với lãi ròng đã chốt{" "}
                          <strong className="font-mono text-gain">{signedVnd(realizedPnl)}</strong>. Bạn có thể xem lại bảng tổng kết các mã đã chốt hoặc đặt lệnh giao dịch mới.
                        </>
                      ) : (
                        "Bạn có thể bắt đầu xây dựng danh mục cá nhân bằng cách đặt lệnh mua cổ phiếu đầu tiên."
                      )}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    {closedTrades.length > 0 && (
                      <Button variant="ghost" onClick={() => setLedgerTab("roundtrips")}>
                        Xem {closedTrades.length} mã đã chốt
                      </Button>
                    )}
                    <Button
                      variant="primary"
                      onClick={() =>
                        setOrderModalState({
                          open: true,
                          symbol: personalSymbols[0] || "FPT",
                          side: "BUY",
                        })
                      }
                    >
                      Đặt lệnh mua cổ phiếu
                    </Button>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* TAB 2: Closed Stock Round-Trip Attribution Table */}
          {ledgerTab === "roundtrips" && (
            <div className="overflow-x-auto">
              {filteredClosedTrades.length > 0 ? (
                <table className="w-full min-w-[860px] text-[13px]">
                  <thead>
                    <tr className="border-b border-line bg-canvas/50 text-[10.5px] font-semibold tracking-wider text-muted uppercase">
                      <th className="py-2.5 pl-5 text-left">Mã CP &amp; Ngành</th>
                      <th className="px-3 py-2.5 text-right">Chu kỳ giữ</th>
                      <th className="px-3 py-2.5 text-right">Khối lượng đối ứng</th>
                      <th className="px-3 py-2.5 text-right">Giá mua TB</th>
                      <th className="px-3 py-2.5 text-right">Giá bán TB</th>
                      <th className="px-3 py-2.5 text-right">Tỷ suất (%)</th>
                      <th className="px-3 py-2.5 text-right">Lãi / Lỗ gộp (₫)</th>
                      <th className="py-2.5 pr-5 text-right">Chi tiết lệnh</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line/70">
                    {filteredClosedTrades.map(item => {
                      const isWin = item.pnlVnd >= 0
                      return (
                        <tr
                          key={item.sym}
                          onClick={() => setSelectedTradeForAudit(item)}
                          className="cursor-pointer transition-colors hover:bg-soft/45 whitespace-nowrap"
                        >
                          <td className="py-3 pl-5">
                            <div className="flex items-center gap-2">
                              <span className="font-mono font-bold text-ink">{item.sym}</span>
                              <span className="rounded border border-line bg-canvas px-1.5 py-0.5 text-[10.5px] text-secondary">
                                {item.sectorLabel}
                              </span>
                              <Pill tone={isWin ? "gain" : "loss"}>
                                {isWin ? "Chốt lời" : "Cắt lỗ"}
                              </Pill>
                            </div>
                          </td>
                          <td className="tnum px-3 py-3 text-right font-mono text-secondary">
                            {item.holdingDays} ngày ({item.tradesCount} lệnh)
                          </td>
                          <td className="tnum px-3 py-3 text-right font-mono text-ink">
                            {fmt(item.matchedQty)} cp
                          </td>
                          <td className="tnum px-3 py-3 text-right font-mono text-secondary">
                            {fmtVnd(item.avgBuy)}
                          </td>
                          <td className="tnum px-3 py-3 text-right font-mono font-medium text-ink">
                            {fmtVnd(item.avgSell)}
                          </td>
                          <td className="tnum px-3 py-3 text-right font-mono">
                            <span
                              className={`rounded px-2 py-0.5 text-xs font-semibold ${
                                isWin ? "bg-gain/10 text-gain" : "bg-loss/10 text-loss"
                              }`}
                            >
                              {signedPct(item.retPct)}
                            </span>
                          </td>
                          <td
                            className={`tnum px-3 py-3 text-right font-mono font-bold ${
                              isWin ? "text-gain" : "text-loss"
                            }`}
                          >
                            {signedVnd(item.pnlVnd)}
                          </td>
                          <td className="py-3 pr-5 text-right">
                            <button
                              type="button"
                              onClick={e => {
                                e.stopPropagation()
                                setSelectedTradeForAudit(item)
                              }}
                              className="rounded border border-line bg-canvas px-2.5 py-1 text-xs font-medium text-ink hover:bg-soft"
                            >
                              Xem {item.tradesCount} lệnh →
                            </button>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              ) : (
                <div className="p-8 text-center text-xs text-secondary">
                  Không có mã cổ phiếu đã chốt nào thuộc bộ lọc ngành hiện tại.
                </div>
              )}
            </div>
          )}

          {/* TAB 3: Formatted Order History */}
          {ledgerTab === "orders" && (
            <div>
              {filteredOrders.length > 0 ? (
                <>
                  <div className="overflow-x-auto">
                    <table className="w-full min-w-[820px] text-[13px]">
                      <thead>
                        <tr className="border-b border-line bg-canvas/50 text-[10.5px] font-semibold tracking-wider text-muted uppercase">
                          <th className="py-2.5 pl-5 text-left">Mã CP &amp; Ngành</th>
                          <th className="px-3 py-2.5 text-left">Chiều</th>
                          <th className="px-3 py-2.5 text-right">Khối lượng</th>
                          <th className="px-3 py-2.5 text-right">Giá khớp</th>
                          <th className="px-3 py-2.5 text-right">Tổng giá trị lệnh</th>
                          <th className="px-3 py-2.5 text-left">Trạng thái</th>
                          <th className="py-2.5 pr-5 text-right">Thời gian khớp (Giờ VN)</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-line/70">
                        {filteredOrders.slice(0, orderLimit).map(ord => {
                          const relatedRoundTrip = closedTrades.find(c => c.sym === ord.symbol)
                          return (
                            <tr
                              key={ord.id}
                              onClick={() => {
                                if (relatedRoundTrip) setSelectedTradeForAudit(relatedRoundTrip)
                              }}
                              className="cursor-pointer transition-colors hover:bg-soft/45 whitespace-nowrap"
                            >
                              <td className="py-2.5 pl-5">
                                <div className="flex items-center gap-2">
                                  <span className="font-mono font-bold text-ink">{ord.symbol}</span>
                                  <span className="rounded border border-line bg-canvas px-1.5 py-0.5 text-[10px] text-secondary">
                                    {ord.sectorLabel}
                                  </span>
                                </div>
                              </td>
                              <td className="px-3 py-2.5">
                                <span
                                  className={`inline-block rounded px-2 py-0.5 font-mono text-[10.5px] font-semibold ${
                                    ord.side === "BUY"
                                      ? "bg-mineral/12 text-mineral"
                                      : "bg-gain/12 text-gain"
                                  }`}
                                >
                                  {ord.side === "BUY" ? "MUA" : "BÁN"}
                                </span>
                              </td>
                              <td className="tnum px-3 py-2.5 text-right font-mono text-ink">
                                {fmt(ord.quantity)} cp
                              </td>
                              <td className="tnum px-3 py-2.5 text-right font-mono text-ink">
                                {fmtVnd(ord.price)}
                              </td>
                              <td className="tnum px-3 py-2.5 text-right font-mono font-semibold text-ink">
                                {fmtVnd(ord.totalValue)}
                              </td>
                              <td className="px-3 py-2.5">
                                <Pill tone="gain">Đã khớp</Pill>
                              </td>
                              <td className="tnum py-2.5 pr-5 text-right font-mono text-xs text-secondary">
                                {fmtVietnamDateTime(ord.createdAt)}
                              </td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                  </div>

                  {filteredOrders.length > orderLimit && (
                    <div className="flex items-center justify-between border-t border-line bg-canvas/40 px-5 py-3 text-xs text-secondary">
                      <span>
                        Đang hiển thị {orderLimit} / {filteredOrders.length} lệnh giao dịch
                      </span>
                      <div className="flex items-center gap-2">
                        <Button variant="ghost" onClick={() => setOrderLimit(prev => prev + 25)}>
                          Xem thêm 25 lệnh
                        </Button>
                        <Button variant="ghost" onClick={() => setOrderLimit(filteredOrders.length)}>
                          Hiển thị toàn bộ ({filteredOrders.length})
                        </Button>
                      </div>
                    </div>
                  )}
                </>
              ) : (
                <div className="p-8 text-center text-xs text-secondary">
                  Không có lệnh giao dịch nào khớp với bộ lọc hiện tại.
                </div>
              )}
            </div>
          )}
        </Panel>
      </div>

      {/* POPUP 1: Personal Stock Trade Audit Drawer */}
      <StockTradeAuditDrawer
        trade={selectedTradeForAudit}
        onClose={() => setSelectedTradeForAudit(null)}
        onOpenOrderModal={(sym, suggestedPrice) =>
          setOrderModalState({ open: true, symbol: sym, side: "BUY", price: suggestedPrice })
        }
      />

      {/* POPUP 2: Personal Investment Charter & Guardrails Modal */}
      {charterModalOpen && (
        <PersonalCharterModal
          isOpen={charterModalOpen}
          charter={charter}
          onSave={saveCharter}
          onClose={() => setCharterModalOpen(false)}
        />
      )}

      {/* POPUP 3: Personal Order Execution Modal */}
      {orderModalState.open && (
        <PersonalOrderModal
          key={`${orderModalState.symbol}-${orderModalState.side ?? "BUY"}-${orderModalState.price ?? "auto"}`}
          isOpen={orderModalState.open}
          initialSymbol={orderModalState.symbol}
          initialSide={orderModalState.side}
          initialPrice={orderModalState.price}
          personalSymbols={personalSymbols}
          positions={snapshot.positions}
          cashVnd={cashVnd}
          onClose={() => setOrderModalState(prev => ({ ...prev, open: false }))}
          onOrderSuccess={async () => {
            await snapshotResource.reload()
          }}
        />
      )}
    </Page>
  )
}
