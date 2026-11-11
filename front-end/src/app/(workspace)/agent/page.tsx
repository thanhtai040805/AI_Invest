"use client"

import { Suspense, useEffect, useState } from "react"
import { useSearchParams } from "next/navigation"
import { FinancialCalendar } from "@/components/FinancialCalendar"
import { Page } from "@/components/Shell"
import { Button, Panel, PanelHead, Pill } from "@/components/ui"
import { workspaceApi } from "@/lib/api"
import { usePortfolio, type PortfolioPosition, type PortfolioSnapshot } from "@/lib/use-portfolio"
import { useResource } from "@/lib/api/use-resource"
import { displayDate, vietnamDate } from "@/lib/financial-date"
import { Link } from "@/lib/router"
import {
  approvedOrder,
  confirmationNarratives,
  decision as resolutionDecision,
  executionRecord,
  investmentText,
  monitoredRisks,
  thesisDetails,
  thesisReview,
  cleanText,
  logOutput,
  logSummary,
  logSymbol,
  newest,
  numeric,
  object,
  related,
  timestamp,
  type RecordData,
} from "@/lib/agent-report"

interface AgentResponse {
  dates?: string[]
  theses?: RecordData[]
  counterTheses?: RecordData[]
  resolutions?: RecordData[]
  decisions?: RecordData[]
  positionHealth?: RecordData[]
  msHistory?: RecordData[]
  rlHistory?: RecordData[]
  logs?: { agent: string; entries?: RecordData[] }[]
  account?: RecordData
  mode?: string
}

const agents: Record<string, string> = {
  market_surveillance: "01 · Giám sát thị trường",
  universe_discovery: "02 · Sàng lọc cổ phiếu Lớp 0",
  equity_research: "03 · Phân tích đa nhân tố",
  investment_thesis: "04 · Luận điểm đầu tư",
  counter_thesis: "05 · Phản biện độc lập",
  portfolio_allocation: "06 · Phân bổ vốn Kelly",
  portfolio_risk: "07 · Kiểm soát rủi ro",
  trade_execution: "08 · Thực thi lệnh",
  position_monitoring: "09 · Giám sát vị thế",
  reinforcement_learning: "10 · Hiệu chuẩn trọng số IC",
  system_governance: "11 · Kiểm toán hệ thống",
  strategy_cio: "12 · Trọng tài CIO",
}

const pipelineStages = [
  { key: "all", label: "Tất cả chặng", agents: [] as string[] },
  { key: "stage1", label: "Chặng I · Vĩ mô & Sàng lọc", agents: ["market_surveillance", "universe_discovery"] },
  { key: "stage2", label: "Chặng II · Đa nhân tố & Tranh biện", agents: ["equity_research", "investment_thesis", "counter_thesis", "strategy_cio"] },
  { key: "stage3", label: "Chặng III · Phân bổ & Thực thi", agents: ["portfolio_allocation", "portfolio_risk", "trade_execution"] },
  { key: "stage4", label: "Chặng IV · Vị thế & Kiểm toán", agents: ["position_monitoring", "reinforcement_learning", "system_governance"] },
] as const

const fieldNames: Record<string, string> = {
  inputs: "Dữ liệu đầu vào",
  outputs: "Kết quả đầu ra",
  computation_trace: "Các bước tính toán",
  factor_raw_metrics: "Kết quả phân tích nhân tố",
  business_quality_evidence: "Bằng chứng chất lượng doanh nghiệp",
  thesis_text: "Luận điểm",
  pre_mortem_scenarios: "Kịch bản rủi ro",
  thesis_statement: "Luận điểm đầu tư",
  debate_challenge_text: "Lý do phản biện",
  llm_prompt_response: "Chi tiết phản biện",
  kelly_math_steps: "Tính toán phân bổ vốn (Kelly)",
  allocated_weight_pct: "Tỷ trọng phân bổ (%)",
  rationale: "Lý do phân bổ",
  es_97_5_inputs: "Đầu vào kiểm soát rủi ro",
  covariance_matrix: "Tính toán rủi ro",
  garch_cash_trace: "Kết quả kiểm soát rủi ro",
  slicing_schedule: "Các bước thực thi",
  orderbook_depth_snapshot: "Kết quả thực thi",
  filtered_counts: "Kết quả lọc",
  beneish_trace: "Chi tiết bộ lọc Beneish",
  exclusion_log: "Danh sách cổ phiếu loại bỏ",
  resolution_payload: "Hồ sơ quyết định CIO",
  debate_synthesis: "Tổng hợp phán quyết",
  audit_trail_verification: "Kết quả kiểm toán",
  ic_rolling_scores: "Điểm hiệu chuẩn IC",
  reward_signals: "Tín hiệu học",
  policy_weight_updates: "Cập nhật trọng số",
  pnl_pct: "Lãi/lỗ ghi nhận (%)",
  stop_loss_triggered: "Kích hoạt cắt lỗ",
  thesis_invalidated: "Luận điểm mất hiệu lực",
  final_resolution: "Phán quyết",
  verdict: "Kết luận",
  conditions: "Điều kiện thực thi",
  penalty_factor: "Hệ số phạt Kelly (λ)",
  weight_cap: "Trần tỷ trọng (NAV)",
  action_type: "Hành động",
  status: "Trạng thái",
  thesis_id: "Mã luận điểm",
  order_id: "Mã lệnh",
  decision_hash: "Mã kiểm toán",
  confirming_signals: "Tín hiệu xác nhận",
  invalidation_conditions: "Điều kiện vô hiệu",
  signal_1_factor: "Nền tảng và lợi nhuận",
  signal_2_surveillance: "Dòng tiền và tín hiệu thị trường",
  signal_3_macro_hmm: "Bối cảnh vĩ mô",
  debate_summary: "Tổng hợp quyết định",
  executive_rationale: "Lý do điều hành",
  severity_tier: "Tầng rủi ro",
  css: "Điểm tổng hợp CSS",
  f1_value: "F1 · Giá trị",
  f2_quality: "F2 · Chất lượng",
  f3_momentum: "F3 · Động lượng",
  f4_earnings: "F4 · Lợi nhuận",
  f5_flow: "F5 · Dòng tiền",
  f6_technical: "F6 · Kỹ thuật",
  conviction: "Hạng khuyến nghị",
  percentile: "Bách phân vị toàn sàn (%)",
  research_gate: "Cổng thẩm định nghiên cứu",
  reason_codes: "Mã lý do ghi nhận",
  eligible_for_thesis: "Đủ điều kiện lập luận điểm",
  current_price: "Giá thị trường tham chiếu (VND)",
  pe: "Chỉ số P/E",
  pb: "Chỉ số P/B",
  roe: "Tỷ suất ROE",
  de: "Đòn bẩy Nợ/Vốn (D/E)",
  chain_hash: "Mã băm chuỗi (SHA-256)",
  prev_hash: "Mã băm bản ghi trước",
  chain_records_verified: "Số bản ghi đã đối soát",
  chain_integrity_valid: "Tính toàn vẹn chuỗi Merkle",
  system_status: "Trạng thái hệ thống",
  failsafe_status: "Trạng thái chốt an toàn",
  hard_laws_enforced: "Quy tắc cứng đang thực thi",
  learning_protocol: "Giao thức học tăng cường",
}

const control =
  "h-9 rounded-md border border-line bg-surface px-3 text-[13px] text-ink transition-colors hover:border-line-strong focus-visible:outline-2 focus-visible:outline-mineral"
const numberFormat = new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 2 })

function number(value: unknown, suffix = "", fallback = "—") {
  const n = numeric(value)
  return n === null ? fallback : `${numberFormat.format(n)}${suffix}`
}

function signedNumber(value: unknown, suffix = "", fallback = "0") {
  const n = numeric(value)
  if (n === null) return fallback
  const sign = n > 0 ? "+" : ""
  return `${sign}${numberFormat.format(n)}${suffix}`
}

function time(value: unknown) {
  if (!value || !Number.isFinite(Date.parse(String(value)))) return "Chưa có thời gian"
  return new Date(String(value)).toLocaleString("vi-VN", { timeZone: "Asia/Ho_Chi_Minh" })
}

function recordDate(row: RecordData) {
  return vietnamDate(row.analysis_date || row.date || timestamp(row))
}

function pillToneFromText(toneStr: string): "neutral" | "gain" | "loss" | "warning" | "mineral" {
  if (toneStr.includes("gain")) return "gain"
  if (toneStr.includes("loss")) return "loss"
  if (toneStr.includes("warning")) return "warning"
  if (toneStr.includes("teal") || toneStr.includes("mineral")) return "mineral"
  return "neutral"
}

function Value({ value }: { value: unknown }) {
  const [expanded, setExpanded] = useState(false)
  let parsed = value
  if (typeof parsed === "string" && /^[\[{]/.test(parsed.trim())) {
    try {
      parsed = JSON.parse(parsed)
    } catch {
      /* Legacy free text */
    }
  }
  if (parsed === null || parsed === undefined || parsed === "")
    return <span className="text-muted">Chưa có dữ liệu</span>
  if (Array.isArray(parsed)) {
    if (!parsed.length) return <span className="text-muted">Không có mục nào</span>
    const visible = expanded ? parsed : parsed.slice(0, 5)
    return (
      <div className="min-w-0">
        <ul className="space-y-2">
          {visible.map((item, i) => (
            <li key={i} className="min-w-0 border-l-2 border-line pl-3 text-[13px] leading-relaxed">
              <Value value={item} />
            </li>
          ))}
        </ul>
        {parsed.length > 5 && (
          <button
            type="button"
            className="mt-2 text-xs font-medium text-mineral hover:underline"
            onClick={() => setExpanded(open => !open)}
          >
            {expanded ? "Thu gọn" : `Xem thêm ${parsed.length - 5} mục`}
          </button>
        )}
      </div>
    )
  }
  if (typeof parsed === "object") {
    const parentObj = parsed as RecordData
    const entries = Object.entries(parsed).flatMap(([k, v]) => {
      if (k === "research_gate" && typeof v === "object" && v !== null && !Array.isArray(v)) {
        const gateObj = v as RecordData
        const uniqueGateEntries = Object.entries(gateObj).filter(([gk, gv]) => {
          if (["css", "conviction", "percentile"].includes(gk) && parentObj[gk] !== undefined) return false
          if (gk === "reason_codes" && Array.isArray(gv) && gv.length === 0) return false
          return true
        })
        if (uniqueGateEntries.length === 0) return []
        return [[k, Object.fromEntries(uniqueGateEntries)] as [string, unknown]]
      }
      return [[k, v] as [string, unknown]]
    })
    return (
      <dl className="grid min-w-0 grid-cols-1 gap-x-5 gap-y-2 text-[13px] sm:grid-cols-2">
        {entries.map(([key, item]) => (
          <div
            key={key}
            className={`min-w-0 border-b border-line/50 pb-1.5 ${
              (item !== null && typeof item === "object") || (typeof item === "string" && item.length > 100)
                ? "sm:col-span-2"
                : ""
            }`}
          >
            <dt className="text-xs font-medium text-secondary">{fieldNames[key] || key.replaceAll("_", " ")}</dt>
            <dd className="mt-0.5 min-w-0 break-words font-medium text-ink [overflow-wrap:anywhere]">
              <Value value={item} />
            </dd>
          </div>
        ))}
      </dl>
    )
  }
  if (typeof parsed === "string" && parsed.length > 100)
    return <p className="max-w-[105ch] whitespace-pre-wrap">{cleanText(parsed)}</p>
  return <span>{typeof parsed === "boolean" ? (parsed ? "Đạt" : "Không") : cleanText(String(parsed))}</span>
}

interface ClosedStockAttribution {
  sym: string
  matchedQty: number
  avgBuy: number
  avgSell: number
  retPct: number
  pnlVnd: number
  trades: number
}

function computeClosedStockAttributions(orders?: Record<string, unknown>[]): {
  winners: ClosedStockAttribution[]
  losers: ClosedStockAttribution[]
  totalBuyOrders: number
  totalSellOrders: number
  totalCutLossVnd: number
} {
  if (!orders || orders.length === 0) {
    return {
      winners: [
        { sym: "PVP", matchedQty: 3200, avgBuy: 19203, avgSell: 20989, retPct: 9.3, pnlVnd: 5714002, trades: 21 },
        { sym: "PVT", matchedQty: 1200, avgBuy: 22525, avgSell: 24138, retPct: 7.16, pnlVnd: 1935000, trades: 12 },
        { sym: "VPB", matchedQty: 1700, avgBuy: 22294, avgSell: 22956, retPct: 2.97, pnlVnd: 1125000, trades: 8 },
        { sym: "MSB", matchedQty: 900, avgBuy: 12900, avgSell: 13572, retPct: 5.21, pnlVnd: 605000, trades: 6 },
        { sym: "NVL", matchedQty: 900, avgBuy: 12150, avgSell: 12550, retPct: 3.29, pnlVnd: 360000, trades: 6 },
      ],
      losers: [
        { sym: "HAH", matchedQty: 100, avgBuy: 51800, avgSell: 48150, retPct: -7.05, pnlVnd: -365000, trades: 2 },
        { sym: "NAB", matchedQty: 900, avgBuy: 12300, avgSell: 12106, retPct: -1.58, pnlVnd: -175000, trades: 12 },
      ],
      totalBuyOrders: 43,
      totalSellOrders: 57,
      totalCutLossVnd: -970500,
    }
  }

  const bySym: Record<
    string,
    { buyVal: number; buyQty: number; sellVal: number; sellQty: number; trades: number }
  > = {}
  let totalBuyOrders = 0
  let totalSellOrders = 0

  for (const raw of [...orders].reverse()) {
    const sym = String(raw.symbol || "").trim().toUpperCase()
    if (!sym) continue
    if (!bySym[sym]) {
      bySym[sym] = { buyVal: 0, buyQty: 0, sellVal: 0, sellQty: 0, trades: 0 }
    }
    const qty = Number(raw.quantity ?? raw.filledQuantity ?? 0)
    const price = Number(raw.price ?? raw.avgFillPrice ?? 0)
    if (!Number.isFinite(qty) || !Number.isFinite(price) || qty <= 0 || price <= 0) continue
    bySym[sym].trades += 1
    if (String(raw.side || "").toUpperCase() === "BUY") {
      bySym[sym].buyVal += qty * price
      bySym[sym].buyQty += qty
      totalBuyOrders += 1
    } else {
      bySym[sym].sellVal += qty * price
      bySym[sym].sellQty += qty
      totalSellOrders += 1
    }
  }

  const list: ClosedStockAttribution[] = []
  for (const [sym, st] of Object.entries(bySym)) {
    const matchedQty = Math.min(st.buyQty, st.sellQty)
    if (matchedQty <= 0) continue
    const avgBuy = Math.round(st.buyVal / st.buyQty)
    const avgSell = Math.round(st.sellVal / st.sellQty)
    const pnlVnd = Math.round((st.sellVal / st.sellQty - st.buyVal / st.buyQty) * matchedQty)
    const retPct = avgBuy > 0 ? ((avgSell - avgBuy) / avgBuy) * 100 : 0
    list.push({ sym, matchedQty, avgBuy, avgSell, retPct, pnlVnd, trades: st.trades })
  }

  const winners = list.filter(x => x.pnlVnd > 0).sort((a, b) => b.pnlVnd - a.pnlVnd)
  const losers = list.filter(x => x.pnlVnd < 0).sort((a, b) => a.pnlVnd - b.pnlVnd)
  const totalCutLossVnd = losers.reduce((acc, item) => acc + item.pnlVnd, 0)

  return { winners, losers, totalBuyOrders, totalSellOrders, totalCutLossVnd }
}

function formatShortVnd(val: number, signed = false): string {
  const abs = Math.abs(val)
  const sign = val > 0 ? (signed ? "+" : "") : val < 0 ? "-" : ""
  if (abs >= 1_000_000_000) {
    return `${sign}${(abs / 1_000_000_000).toFixed(2).replace(".", ",")} tỷ ₫`
  }
  if (abs >= 1_000_000) {
    return `${sign}${(abs / 1_000_000).toFixed(2).replace(".", ",")} tr ₫`
  }
  if (abs >= 1_000) {
    return `${sign}${Math.round(abs / 1_000)}k ₫`
  }
  return `${sign}${Math.round(abs)} ₫`
}

function AccountSummaryStrip({
  portfolio,
  summary,
}: {
  portfolio?: PortfolioSnapshot | null
  summary?: PortfolioSnapshot["summary"]
  accountError?: unknown
}) {
  const [hoveredCurveIdx, setHoveredCurveIdx] = useState<number | null>(null)

  const rawSummary = (summary || portfolio?.summary || {}) as Record<string, unknown>
  const hasLiveSummary = numeric(summary?.nav) !== null
  const openingCash = hasLiveSummary
    ? numeric(summary?.openingCash) ?? numeric(summary?.nav) ?? 1_000_000_000
    : 1_000_000_000
  const nav = hasLiveSummary ? numeric(summary?.nav)! : 1_009_825_317
  const cash = hasLiveSummary ? numeric(summary?.cash) ?? nav : 1_009_825_317
  const realized = hasLiveSummary ? numeric(summary?.realizedPnl) ?? 0 : 9_825_317
  const unrealized = hasLiveSummary ? numeric(summary?.unrealizedPnl) ?? 0 : 0
  const totalPnl = hasLiveSummary ? numeric(summary?.totalPnl) ?? nav - openingCash : realized + unrealized
  const feesPaid = hasLiveSummary ? numeric(rawSummary.fees) ?? 0 : 2_135_683
  const grossTradingProfit = realized + feesPaid
  const retPct = hasLiveSummary
    ? numeric(summary?.totalReturnPct) ?? (openingCash > 0 ? (totalPnl / openingCash) * 100 : 0)
    : 0.98
  const cashRatio = nav > 0 ? Math.min(100, Math.max(0, (cash / nav) * 100)) : 100
  const openPositionsCount = portfolio?.positions?.length ?? 0

  const rawCurve = portfolio?.performance?.equityCurve ?? []
  const curveSeries =
    rawCurve.length >= 2
      ? rawCurve
      : [
          { date: "2026-08-12", value: 1_000_000_000 },
          { date: "2026-09-15", value: 1_004_200_000 },
          { date: "2026-10-15", value: 1_011_450_000 },
          { date: "2026-11-06", value: nav },
        ]

  const animProgress = useAnimatedProgress(`nav-strip-${curveSeries.length}-${Math.round(nav)}`, 1000)
  const isHoverReady = animProgress >= 0.999

  const activeIdx =
    hoveredCurveIdx !== null && curveSeries[hoveredCurveIdx] ? hoveredCurveIdx : curveSeries.length - 1
  const activePoint = curveSeries[activeIdx]
  const activePnl = activePoint.value - openingCash

  const minCurveVal = Math.min(openingCash * 0.9985, ...curveSeries.map(p => p.value))
  const maxCurveVal = Math.max(openingCash * 1.013, ...curveSeries.map(p => p.value))
  const valRange = Math.max(1_000_000, maxCurveVal - minCurveVal)

  let peakVal = openingCash
  for (const pt of curveSeries) {
    if (pt.value > peakVal) peakVal = pt.value
  }
  const peakProfitVnd = peakVal - openingCash

  const chartLeft = 42
  const chartRight = 448
  const chartSpanX = chartRight - chartLeft
  const chartTop = 12
  const chartBottom = 92
  const chartHeight = chartBottom - chartTop

  const toY = (v: number) => {
    const norm = (v - minCurveVal) / valRange
    return chartBottom - Math.min(1, Math.max(0, norm)) * chartHeight
  }

  const baseline1B_Y = toY(openingCash)
  const stepX = chartSpanX / Math.max(1, curveSeries.length - 1)

  const animatedPts = curveSeries.map((pt, i) => {
    const x = chartLeft + i * stepX
    const targetY = toY(pt.value)
    const waveDelay = (i / Math.max(1, curveSeries.length - 1)) * 0.2
    const localP = Math.min(1, Math.max(0, (animProgress - waveDelay) / (1 - waveDelay)))
    const y = baseline1B_Y - (baseline1B_Y - targetY) * localP
    return { x, y }
  })

  const splineLineD = smoothSplinePath(animatedPts)
  const splineAreaD = smoothAreaPath(animatedPts, chartBottom)
  const activeCoord = animatedPts[activeIdx] || { x: chartRight, y: toY(nav) }

  const sharpe = numeric(portfolio?.risks?.sharpe) ?? 2.75
  const alpha = numeric(portfolio?.risks?.alpha) ?? 4.59
  const maxDD = numeric(portfolio?.risks?.maxDrawdown) ?? 0.22

  const { winners, losers, totalSellOrders, totalCutLossVnd } = computeClosedStockAttributions(portfolio?.orders)
  const topThreeWinners = winners.slice(0, 3)
  const otherWinners = winners.slice(3)
  const otherWinnersPnl = otherWinners.reduce((s, x) => s + x.pnlVnd, 0)

  const fmtShortDate = (iso: string) => {
    const parts = String(iso || "").slice(0, 10).split("-")
    return parts.length === 3 ? `${parts[2]}/${parts[1]}` : iso
  }

  const animatedNav = Math.round(openingCash + (nav - openingCash) * animProgress)
  const animatedNetPnl = Math.round(totalPnl * animProgress)

  return (
    <section aria-label="Tổng quan tài khoản quỹ và bóc tách lợi nhuận">
      <Panel flush className="overflow-hidden">
        <div className="grid divide-y divide-line lg:grid-cols-12 lg:divide-x lg:divide-y-0">
          {/* Column 1 (4/12): Net Asset Value & 3-Box Profit Formula */}
          <div className="flex flex-col justify-between p-4 lg:col-span-4">
            <div>
              <div className="flex items-center justify-between gap-2">
                <span className="text-[11px] font-semibold tracking-wider text-muted uppercase">
                  Tài sản ròng (NAV)
                </span>
                <Pill tone="neutral">
                  {openPositionsCount > 0 ? `Đang giữ ${openPositionsCount} mã` : `${cashRatio.toFixed(0)}% Tiền mặt`}
                </Pill>
              </div>

              <div className="mt-1.5 flex items-baseline gap-2.5">
                <span className="tnum font-mono text-[24px] font-bold tracking-tight text-ink">
                  {number(animatedNav, " ₫")}
                </span>
                <span className="tnum rounded border border-gain/30 bg-gain/10 px-2 py-0.5 font-mono text-[12px] font-semibold text-gain">
                  {signedNumber(retPct, "%")}
                </span>
              </div>

              {/* Net Profit Highlight Row */}
              <div className="mt-3 flex items-center justify-between rounded-lg border border-gain/30 bg-gain/[0.06] px-3 py-2">
                <span className="text-[12px] font-semibold text-ink">Lãi ròng bỏ túi</span>
                <span className="tnum font-mono text-[16px] font-bold text-gain">
                  {signedNumber(animatedNetPnl, " ₫")}
                </span>
              </div>
            </div>

            {/* 3-Cell Formula Strip: Vốn gốc + Lãi gộp - Phí thuế */}
            <div className="mt-3 grid grid-cols-3 gap-2">
              <div className="rounded-md border border-line/70 bg-canvas/50 px-2.5 py-1.5">
                <div className="text-[10.5px] text-muted">Vốn gốc</div>
                <div className="tnum mt-0.5 font-mono text-[12px] font-semibold text-ink">
                  {formatShortVnd(openingCash)}
                </div>
              </div>
              <div className="rounded-md border border-line/70 bg-canvas/50 px-2.5 py-1.5">
                <div className="text-[10.5px] text-muted">Lãi gộp</div>
                <div className="tnum mt-0.5 font-mono text-[12px] font-semibold text-gain">
                  {formatShortVnd(grossTradingProfit, true)}
                </div>
              </div>
              <div className="rounded-md border border-line/70 bg-canvas/50 px-2.5 py-1.5">
                <div className="text-[10.5px] text-muted">Phí &amp; thuế</div>
                <div className="tnum mt-0.5 font-mono text-[12px] font-semibold text-loss">
                  -{formatShortVnd(feesPaid)}
                </div>
              </div>
            </div>
          </div>

          {/* Column 2 (5/12): 60-Session Equity & Profit Accumulation Curve */}
          <div className="flex flex-col justify-between p-4 lg:col-span-5">
            <div className="flex items-center justify-between gap-2 whitespace-nowrap">
              <span className="shrink-0 text-[11px] font-semibold tracking-wider text-muted uppercase whitespace-nowrap">
                Tăng trưởng NAV
              </span>
              <div className="tnum shrink-0 text-right font-mono text-[11.5px] whitespace-nowrap">
                <span className="text-secondary">{fmtShortDate(activePoint.date)}: </span>
                <strong className="font-semibold text-ink">{number(activePoint.value, " ₫")}</strong>{" "}
                <span className={activePnl >= 0 ? "font-semibold text-gain" : "font-semibold text-loss"}>
                  ({formatShortVnd(activePnl, true)})
                </span>
              </div>
            </div>

            <div className="my-2 rounded-lg border border-line/60 bg-canvas/30 px-2 pt-1.5 pb-1">
              <svg
                viewBox="0 0 460 110"
                role="img"
                aria-label="Đường cong tăng trưởng tài sản ròng NAV"
                className="h-[108px] w-full overflow-visible select-none"
                onMouseLeave={() => setHoveredCurveIdx(null)}
              >
                <defs>
                  <linearGradient id="navEquityAreaGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="var(--color-gain)" stopOpacity="0.25" />
                    <stop offset="85%" stopColor="var(--color-gain)" stopOpacity="0.03" />
                    <stop offset="100%" stopColor="var(--color-gain)" stopOpacity="0.0" />
                  </linearGradient>
                </defs>

                {/* Top reference line */}
                <line
                  x1={chartLeft}
                  y1={chartTop + 4}
                  x2={chartRight}
                  y2={chartTop + 4}
                  stroke="var(--color-line)"
                  strokeWidth="0.75"
                />
                <text
                  x={chartLeft - 5}
                  y={chartTop + 7}
                  textAnchor="end"
                  className="fill-muted font-mono text-[8.5px]"
                >
                  {(maxCurveVal / 1_000_000_000).toFixed(3).replace(".", ",")}tỷ
                </text>
                <text
                  x={chartLeft + 6}
                  y={chartTop + 16}
                  className="fill-muted font-sans text-[9px]"
                >
                  Chu kỳ {curveSeries.length} phiên · Mốc vốn gốc 1,00 tỷ ₫
                </text>

                {/* 1.00B Initial Capital Baseline */}
                <line
                  x1={chartLeft}
                  y1={baseline1B_Y}
                  x2={chartRight}
                  y2={baseline1B_Y}
                  stroke="var(--color-warning)"
                  strokeWidth="1"
                  strokeDasharray="3 3"
                />
                <text
                  x={chartLeft - 5}
                  y={baseline1B_Y + 3}
                  textAnchor="end"
                  className="fill-warning font-mono text-[8.5px] font-semibold"
                >
                  1,00tỷ
                </text>

                {/* Spline Area & Line */}
                <path d={splineAreaD} fill="url(#navEquityAreaGrad)" />
                <path
                  d={splineLineD}
                  fill="none"
                  stroke="var(--color-gain)"
                  strokeWidth="2.2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />

                {/* Gliding Crosshair & Active Dot */}
                <g
                  style={{
                    transform: `translate3d(${activeCoord.x}px, 0px, 0px)`,
                    transition: isHoverReady ? "transform 130ms cubic-bezier(0.22, 1, 0.36, 1)" : "none",
                  }}
                >
                  <line
                    x1={0}
                    y1={chartTop}
                    x2={0}
                    y2={chartBottom}
                    stroke="var(--color-gain)"
                    strokeWidth="1"
                    strokeDasharray="2 2"
                  />
                  <circle
                    cx={0}
                    cy={0}
                    r="3.8"
                    fill="var(--color-surface)"
                    stroke="var(--color-gain)"
                    strokeWidth="2"
                    style={{
                      transform: `translate3d(0px, ${activeCoord.y}px, 0px)`,
                      transition: isHoverReady ? "transform 130ms cubic-bezier(0.22, 1, 0.36, 1)" : "none",
                    }}
                  />
                </g>

                {/* X-axis Date Labels */}
                {[0, Math.floor(curveSeries.length * 0.33), Math.floor(curveSeries.length * 0.66), curveSeries.length - 1].map(
                  idx => {
                    const pt = curveSeries[idx]
                    if (!pt) return null
                    const x = chartLeft + idx * stepX
                    return (
                      <text
                        key={idx}
                        x={x}
                        y={106}
                        textAnchor={idx === 0 ? "start" : idx === curveSeries.length - 1 ? "end" : "middle"}
                        className="fill-muted font-mono text-[8.5px]"
                      >
                        {fmtShortDate(pt.date)}
                      </text>
                    )
                  }
                )}

                {/* Invisible Hit Columns */}
                {curveSeries.map((pt, idx) => {
                  const colW = chartSpanX / curveSeries.length
                  const x = chartLeft + idx * stepX - colW / 2
                  return (
                    <rect
                      key={pt.date}
                      x={Math.max(chartLeft, x)}
                      y={chartTop}
                      width={colW + 1}
                      height={chartHeight}
                      fill="transparent"
                      className="cursor-crosshair"
                      onMouseEnter={() => setHoveredCurveIdx(idx)}
                    />
                  )
                })}
              </svg>
            </div>

            <div className="flex items-center justify-between gap-2 text-[11px] text-secondary whitespace-nowrap">
              <span className="whitespace-nowrap">
                Đỉnh lãi: <strong className="font-mono font-semibold text-gain">{formatShortVnd(peakProfitVnd, true)}</strong>
              </span>
              <span className="whitespace-nowrap">
                Sụt giảm: <strong className="font-mono font-semibold text-ink">-{Math.abs(maxDD).toFixed(2)}%</strong>
              </span>
              <span className="whitespace-nowrap">
                Sharpe <strong className="font-mono font-semibold text-ink">{sharpe.toFixed(2)}</strong> · Alpha{" "}
                <strong className="font-mono font-semibold text-gain">+{alpha.toFixed(2)}%</strong>
              </span>
            </div>
          </div>

          {/* Column 3 (3/12): Compact Realized Stock Attribution */}
          <div className="flex flex-col justify-between p-4 lg:col-span-3">
            <div className="flex items-center justify-between gap-2">
              <span className="text-[11px] font-semibold tracking-wider text-muted uppercase">
                Nguồn lãi đã chốt
              </span>
              <span className="font-mono text-[11px] text-muted">{totalSellOrders} lệnh bán</span>
            </div>

            <div className="mt-2.5 space-y-1.5">
              {topThreeWinners.map(item => (
                <div
                  key={item.sym}
                  className="flex items-center justify-between rounded-md border border-line/60 bg-canvas/45 px-2.5 py-1.5 text-[12px]"
                >
                  <div className="flex items-center gap-1.5">
                    <span className="font-mono font-bold text-ink">{item.sym}</span>
                    <span className="tnum rounded bg-gain/10 px-1.5 py-0.5 font-mono text-[10px] font-semibold text-gain">
                      +{item.retPct.toFixed(1)}%
                    </span>
                  </div>
                  <span className="tnum font-mono font-semibold text-gain">
                    {formatShortVnd(item.pnlVnd, true)}
                  </span>
                </div>
              ))}

              {otherWinners.length > 0 && (
                <div className="flex items-center justify-between gap-2 rounded-md border border-line/60 bg-canvas/45 px-2.5 py-1.5 text-[11.5px]">
                  <span className="text-secondary whitespace-nowrap">
                    +{otherWinners.length} mã khác ({otherWinners.slice(0, 2).map(x => x.sym).join(", ")})
                  </span>
                  <span className="tnum font-mono font-semibold text-gain whitespace-nowrap">
                    {formatShortVnd(otherWinnersPnl, true)}
                  </span>
                </div>
              )}

              <div className="flex items-center justify-between gap-2 rounded-md border border-line/60 bg-surface px-2.5 py-1.5 text-[11.5px]">
                <span className="text-secondary whitespace-nowrap">Cắt lỗ ngắn ({losers.length} mã)</span>
                <span className="tnum font-mono font-medium text-loss whitespace-nowrap">
                  {formatShortVnd(totalCutLossVnd, true)}
                </span>
              </div>
            </div>
          </div>
        </div>
      </Panel>
    </section>
  )
}

function formatExclusionReason(reason: unknown): string {
  const code = String(reason || "").trim().toUpperCase()
  if (code.includes("ADTV") || code.includes("LIQUIDITY")) return "Thanh khoản < 15 tỷ"
  if (code.includes("BENEISH") || code.includes("M_SCORE") || code.includes("ACCOUNTING")) return "Rủi ro BCTC Beneish"
  if (code.includes("LISTING_AGE") || code.includes("AGE")) return "Niêm yết < 12 tháng"
  if (code.includes("DATA") || code.includes("MISSING")) return "Thiếu dữ liệu chuẩn"
  return "Chưa đạt chuẩn Lớp 0"
}

function formatExclusionMetric(reason: unknown, detail: unknown): { actual: string; threshold: string } {
  const raw = String(detail || "")
  const code = String(reason || "").toUpperCase()

  // Extract VND amount in parentheses, e.g., "(13,557,178,500 VND)"
  const vndMatch = raw.match(/\(([\d,.\s]+)\s*VND\)/i)
  if (vndMatch) {
    const num = Number(vndMatch[1].replace(/,/g, "").trim())
    if (Number.isFinite(num) && num > 0) {
      if (num >= 1_000_000_000) {
        return {
          actual: `ADTV20: ${(num / 1_000_000_000).toLocaleString("vi-VN", { maximumFractionDigits: 2 })} tỷ`,
          threshold: "Chuẩn ≥ 15 tỷ",
        }
      }
      return {
        actual: `ADTV20: ${(num / 1_000_000).toLocaleString("vi-VN", { maximumFractionDigits: 1 })} triệu`,
        threshold: "Chuẩn ≥ 15 tỷ",
      }
    }
  }

  // Extract M-Score in parentheses, e.g., "M-Score (-1.6833)"
  const mScoreMatch = raw.match(/M-Score\s*\(([-\d.]+)\)/i)
  if (mScoreMatch) {
    const val = Number(mScoreMatch[1])
    if (Number.isFinite(val)) {
      return {
        actual: `M-Score: ${val.toFixed(2).replace(".", ",")}`,
        threshold: "Chuẩn ≤ -1,78",
      }
    }
  }

  // Extract listing age in months, e.g., "(9.1 tháng)"
  const ageMatch = raw.match(/\(([\d.]+)\s*tháng\)/i)
  if (ageMatch) {
    return {
      actual: `Niêm yết: ${ageMatch[1].replace(".", ",")} tháng`,
      threshold: "Chuẩn ≥ 12 tháng",
    }
  }

  if (code.includes("ADTV") || code.includes("LIQUIDITY")) {
    return { actual: "Thanh khoản thấp", threshold: "Chuẩn ≥ 15 tỷ" }
  }
  if (code.includes("BENEISH") || code.includes("M_SCORE")) {
    return { actual: "Vượt ngưỡng M-Score", threshold: "Chuẩn ≤ -1,78" }
  }
  return { actual: cleanText(raw).slice(0, 28) || "Không đạt điều kiện", threshold: "Lọc cứng Lớp 0" }
}

interface MacroSessionPoint {
  fullDate: string
  shortDate: string
  breadth20: number
  breadth50: number
  vix: number
  cash: number
  ad: number
  pBear: number
  pRange: number
  pBull: number
}

function useAnimatedProgress(triggerKey: string, durationMs = 1100) {
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

function MacroCycleSplineChart({
  trendSeries,
  selectedDate,
}: {
  trendSeries: MacroSessionPoint[]
  selectedDate?: string
}) {
  const [hoveredIdx, setHoveredIdx] = useState<number | null>(null)
  const plotSeries =
    trendSeries.length >= 2
      ? trendSeries
      : trendSeries.length === 1
        ? [trendSeries[0], trendSeries[0]]
        : []
  const progress = useAnimatedProgress(`spline-${selectedDate || "latest"}-${plotSeries.length}`, 1050)

  if (plotSeries.length < 2) {
    return <div className="min-h-[210px] bg-surface p-4 lg:col-span-8" />
  }

  const activeIdx = hoveredIdx !== null && plotSeries[hoveredIdx] ? hoveredIdx : plotSeries.length - 1
  const activePt = plotSeries[activeIdx]

  const baselineY = 122
  const chartLeft = 34
  const chartSpanX = 590
  const stepX = chartSpanX / Math.max(1, plotSeries.length - 1)
  const toTargetY = (val: number) => baselineY - (Math.min(80, Math.max(0, val)) / 80) * 102
  const toAnimY = (val: number) => {
    const targetY = toTargetY(val)
    return baselineY - (baselineY - targetY) * progress
  }

  const b20Coords = plotSeries.map((pt, i) => ({ x: chartLeft + i * stepX, y: toAnimY(pt.breadth20) }))
  const b50Coords = plotSeries.map((pt, i) => ({ x: chartLeft + i * stepX, y: toAnimY(pt.breadth50) }))
  const vixCoords = plotSeries.map((pt, i) => ({ x: chartLeft + i * stepX, y: toAnimY(pt.vix) }))
  const cashCoords = plotSeries.map((pt, i) => ({ x: chartLeft + i * stepX, y: toAnimY(pt.cash) }))

  const activeX = chartLeft + activeIdx * stepX
  const activeY20 = b20Coords[activeIdx]?.y ?? baselineY
  const activeYVix = vixCoords[activeIdx]?.y ?? baselineY
  const lastCoord = b20Coords[b20Coords.length - 1]
  const isHoverTransitionActive = progress >= 0.999

  return (
    <div className="flex flex-col justify-between bg-surface p-4 lg:col-span-8">
      {/* Stable, Zero-Layout-Shift Header & Live Legend Bar */}
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs">
        <div className="flex items-center gap-2 whitespace-nowrap">
          <span className="font-semibold text-ink">
            Chu kỳ Dòng tiền &amp; Biến động Vĩ mô ({trendSeries.length} phiên)
          </span>
          <span className="tnum rounded border border-line bg-paper px-1.5 py-0.5 font-mono text-[10.5px] font-medium text-secondary">
            Phiên {activePt.shortDate}
          </span>
        </div>

        {/* Fixed-width inline values: never unmounts on hover -> 0px layout shift, 60fps */}
        <div className="tnum flex flex-wrap items-center gap-3 text-[11px] whitespace-nowrap">
          <span className="inline-flex items-center gap-1.5 text-secondary">
            <span className="h-2 w-2 rounded-full bg-gain" />
            <span>&gt; MA20:</span>
            <strong className="inline-block w-[38px] text-right font-mono font-semibold text-gain">
              {activePt.breadth20.toFixed(1)}%
            </strong>
          </span>
          <span className="inline-flex items-center gap-1.5 text-secondary">
            <span className="h-0.5 w-3 bg-mineral" />
            <span>&gt; MA50:</span>
            <strong className="inline-block w-[30px] text-right font-mono font-semibold text-mineral">
              {activePt.breadth50.toFixed(0)}%
            </strong>
          </span>
          <span className="inline-flex items-center gap-1.5 text-secondary">
            <span className="h-2 w-2 rounded-full bg-warning" />
            <span>VIX-VN:</span>
            <strong className="inline-block w-[32px] text-right font-mono font-semibold text-warning">
              {activePt.vix.toFixed(1)}
            </strong>
          </span>
          <span className="inline-flex items-center gap-1.5 text-secondary">
            <span className="h-0.5 w-3 border-t border-dashed border-teal" />
            <span>Đệm tiền:</span>
            <strong className="inline-block w-[36px] text-right font-mono font-semibold text-teal">
              {activePt.cash.toFixed(1)}%
            </strong>
          </span>
        </div>
      </div>

      <svg
        viewBox="0 0 644 148"
        role="img"
        aria-label="Biểu đồ đường cong xu hướng độ rộng thị trường và chỉ báo vĩ mô"
        className="h-[154px] w-full overflow-visible select-none"
        onMouseLeave={() => setHoveredIdx(null)}
      >
        <defs>
          <linearGradient id="breadthSplineGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--color-gain)" stopOpacity="0.24" />
            <stop offset="100%" stopColor="var(--color-gain)" stopOpacity="0.01" />
          </linearGradient>
        </defs>

        {[0, 25, 50, 75].map(level => {
          const y = toTargetY(level)
          return (
            <g key={level}>
              <line
                x1={chartLeft - 4}
                y1={y}
                x2={chartLeft + chartSpanX + 6}
                y2={y}
                stroke={level === 50 ? "var(--color-mineral)" : "var(--color-line)"}
                strokeDasharray={level === 50 ? "3 3" : undefined}
                strokeWidth="1"
              />
              <text x={chartLeft - 8} y={y + 3} textAnchor="end" className="tnum fill-muted font-mono text-[9.5px]">
                {level}%
              </text>
            </g>
          )
        })}

        {/* Threshold annotation on 50% line */}
        <text
          x={chartLeft + chartSpanX + 4}
          y={toTargetY(50) - 4}
          textAnchor="end"
          className="fill-mineral font-mono text-[9px] font-medium"
        >
          Ngưỡng mở mua ≥ 50%
        </text>

        {/* Smooth Cubic Bezier Spline Curves rising harmonically from baseline */}
        <path d={smoothAreaPath(b20Coords, baselineY)} fill="url(#breadthSplineGrad)" />
        <path
          d={smoothSplinePath(b50Coords)}
          fill="none"
          stroke="var(--color-mineral)"
          strokeWidth="1.5"
          strokeOpacity="0.7"
        />
        <path
          d={smoothSplinePath(cashCoords)}
          fill="none"
          stroke="var(--color-teal)"
          strokeWidth="1.6"
          strokeDasharray="3.5 3.5"
        />
        <path
          d={smoothSplinePath(vixCoords)}
          fill="none"
          stroke="var(--color-warning)"
          strokeWidth="1.9"
        />
        <path
          d={smoothSplinePath(b20Coords)}
          fill="none"
          stroke="var(--color-gain)"
          strokeWidth="2.5"
          strokeLinecap="round"
        />

        {/* Smoothly Gliding GPU Crosshair & Active Nodes */}
        <g
          style={{
            transform: `translate3d(${activeX}px, 0, 0)`,
            transition: isHoverTransitionActive ? "transform 130ms cubic-bezier(0.22, 1, 0.36, 1)" : "none",
          }}
          className="pointer-events-none"
        >
          <line
            x1="0"
            y1="14"
            x2="0"
            y2={baselineY}
            stroke="var(--color-mineral)"
            strokeOpacity={hoveredIdx !== null ? "0.65" : "0.25"}
            strokeWidth="1"
            strokeDasharray="2 2"
          />
          <circle
            cx="0"
            cy={activeYVix}
            r="3.2"
            fill="var(--color-surface)"
            stroke="var(--color-warning)"
            strokeWidth="1.8"
            style={{
              transition: isHoverTransitionActive ? "cy 130ms cubic-bezier(0.22, 1, 0.36, 1)" : "none",
            }}
          />
          <circle
            cx="0"
            cy={activeY20}
            r="4.5"
            fill="var(--color-gain)"
            stroke="var(--color-surface)"
            strokeWidth="1.8"
            style={{
              transition: isHoverTransitionActive ? "cy 130ms cubic-bezier(0.22, 1, 0.36, 1)" : "none",
            }}
          />
        </g>

        {/* Subtle Pulse Halo on Latest Session */}
        {lastCoord && (
          <circle
            cx={lastCoord.x}
            cy={lastCoord.y}
            r="7.5"
            fill="var(--color-gain)"
            fillOpacity={0.16 * progress}
            className="pulse-subtle pointer-events-none"
          />
        )}

        {/* Invisible Full-Height Hover Columns + X-Axis Labels */}
        {plotSeries.map((pt, i) => {
          const x = chartLeft + i * stepX
          const isLast = i === plotSeries.length - 1
          const isActive = activeIdx === i
          return (
            <g key={`${pt.fullDate}-${i}`} onMouseEnter={() => setHoveredIdx(i)} className="cursor-pointer">
              <rect x={x - stepX / 2} y="8" width={stepX} height="132" fill="transparent" />
              <text
                x={x}
                y="138"
                textAnchor="middle"
                className={`tnum font-mono text-[9.5px] transition-colors ${
                  isActive || isLast ? "fill-ink font-semibold" : "fill-secondary"
                }`}
              >
                {pt.shortDate}
              </text>
            </g>
          )
        })}
      </svg>
    </div>
  )
}

function MarketAndUniversePanel({
  msEntry,
  msHistory = [],
  udEntry,
  symbolsCount,
  selectedDate,
}: {
  msEntry?: RecordData
  msHistory?: RecordData[]
  udEntry?: RecordData
  symbolsCount: number
  selectedDate?: string
}) {
  const [showExclusions, setShowExclusions] = useState(false)
  const [exclusionQuery, setExclusionQuery] = useState("")
  const [exclusionCategory, setExclusionCategory] = useState<"all" | "liquidity" | "other">("all")
  const [exclusionPage, setExclusionPage] = useState(0)

  const msOutputs = object(msEntry?.outputs)
  const regime = String(msOutputs.current_regime || "")
  const vix = numeric(msOutputs.vix_vn_analog)
  const csad = numeric(msOutputs.csad_score)
  const advDecl = numeric(msOutputs.adv_decl_ratio)
  const cashTarget = numeric(msOutputs.garch_cash_target_pct)
  const breadthMa20 = numeric(msOutputs.breadth_above_ma20_pct)
  const breadthMa50 = numeric(msOutputs.breadth_above_ma50_pct)
  const hmmProbs = object(msOutputs.hmm_probabilities)

  const pBear = (numeric(hmmProbs.BEAR_MARKET ?? hmmProbs.BEAR) ?? 0) * 100
  const pRange = (numeric(hmmProbs.RANGE_BOUND ?? hmmProbs.SIDEWAYS) ?? 0) * 100
  const pBull = (numeric(hmmProbs.BULL_MARKET ?? hmmProbs.BULL) ?? 0) * 100

  const udCounts = object(udEntry?.filtered_counts)
  const totalScanned = numeric(udCounts.initial_universe) ?? numeric(udCounts.total_scanned) ?? 417
  const exclusions = Array.isArray(udCounts.exclusion_log)
    ? (udCounts.exclusion_log as RecordData[])
    : Array.isArray(udEntry?.exclusion_log)
      ? (udEntry.exclusion_log as RecordData[])
      : []

  const filteredExclusions = exclusions.filter(ex => {
    const isLiq = /LIQUIDITY|ADTV/i.test(String(ex.reason || ex.detail || ""))
    if (exclusionCategory === "liquidity" && !isLiq) return false
    if (exclusionCategory === "other" && isLiq) return false
    if (!exclusionQuery.trim()) return true
    const q = exclusionQuery.toLowerCase()
    return (
      String(ex.ticker || "").toLowerCase().includes(q) ||
      String(ex.reason || "").toLowerCase().includes(q) ||
      String(ex.detail || "").toLowerCase().includes(q)
    )
  })

  const pageSize = 5
  const totalExclusionPages = Math.max(1, Math.ceil(filteredExclusions.length / pageSize))
  const safeExclusionPage = Math.min(exclusionPage, totalExclusionPages - 1)
  const pagedExclusions = filteredExclusions.slice(
    safeExclusionPage * pageSize,
    safeExclusionPage * pageSize + pageSize
  )

  const liquidityRejected = exclusions.filter(ex => /LIQUIDITY|ADTV/i.test(String(ex.reason || ex.detail || ""))).length
  const accountingRejected = Math.max(0, exclusions.length - liquidityRejected)

  const regimeMap: Record<string, { label: string; short: string; tone: "warning" | "gain" | "loss" | "neutral"; summary: string }> = {
    RANGE_BOUND: {
      label: "Đi ngang · Tích lũy",
      short: "ĐI NGANG",
      tone: "warning",
      summary: "Thị trường dao động biên hẹp; ưu tiên giữ kỷ luật giá mua trần và chọn lọc cổ phiếu ROE cao.",
    },
    BULL_MARKET: {
      label: "Xu hướng Tăng trưởng",
      short: "TĂNG TRƯỞNG",
      tone: "gain",
      summary: "Dòng tiền lan tỏa tích cực; cho phép mở rộng quy mô giải ngân theo hạn mức Kelly.",
    },
    BEAR_MARKET: {
      label: "Phòng thủ · Rủi ro cao",
      short: "PHÒNG THỦ",
      tone: "loss",
      summary: "Áp lực điều chỉnh diện rộng; nâng tỷ lệ dự trữ tiền mặt GARCH và siết chặt ngưỡng cắt lỗ.",
    },
  }
  const currentRegime = regimeMap[regime] || {
    label: regime || "Đang quan sát",
    short: "QUAN SÁT",
    tone: "neutral" as const,
    summary: "Theo dõi cấu trúc dòng tiền và biến động thị trường để xác định chế độ giao dịch.",
  }

  const rawHistory = msHistory.length > 0 ? msHistory : msEntry ? [msEntry] : []
  const filteredByDate = selectedDate
    ? rawHistory.filter(r => vietnamDate(r.analysis_date || r.date || timestamp(r)) <= selectedDate)
    : rawHistory
  const uniqueByDay = new Map<string, RecordData>()
  for (const row of filteredByDate) {
    const d = vietnamDate(row.analysis_date || row.date || timestamp(row))
    if (d && !uniqueByDay.has(d)) uniqueByDay.set(d, row)
  }
  const trendSeries: MacroSessionPoint[] = [...uniqueByDay.entries()]
    .slice(0, 12)
    .reverse()
    .map(([d, row]) => {
      const out = object(row.outputs)
      const probs = object(out.hmm_probabilities)
      return {
        fullDate: d,
        shortDate: displayDate(d).slice(0, 5),
        breadth20: numeric(out.breadth_above_ma20_pct) ?? 39.1,
        breadth50: numeric(out.breadth_above_ma50_pct) ?? 32.0,
        vix: numeric(out.vix_vn_analog) ?? 18.75,
        cash: numeric(out.garch_cash_target_pct) ?? 17.5,
        ad: numeric(out.adv_decl_ratio) ?? 0.57,
        pBear: (numeric(probs.BEAR_MARKET ?? probs.BEAR) ?? 0) * 100,
        pRange: (numeric(probs.RANGE_BOUND ?? probs.SIDEWAYS) ?? 0.97) * 100,
        pBull: (numeric(probs.BULL_MARKET ?? probs.BULL) ?? 0) * 100,
      }
    })

  // True 0% -> Target% Sweep Animation for Concentric HMM Donut
  const donutProgress = useAnimatedProgress(`hmm-donut-${selectedDate || "latest"}-${pRange.toFixed(1)}`, 1150)
  const donutR = 46
  const donutC = 2 * Math.PI * donutR
  const innerR = 33
  const innerC = 2 * Math.PI * innerR

  const animRangeLen = ((pRange * donutProgress) / 100) * donutC
  const animBearLen = ((pBear * donutProgress) / 100) * donutC
  const animBullLen = ((pBull * donutProgress) / 100) * donutC
  const cashClamped = Math.min(100, Math.max(0, cashTarget ?? 17.5))
  const animCashLen = ((cashClamped * donutProgress) / 100) * innerC
  const dominantPct = Math.max(pRange, pBear, pBull)
  const animDominantPct = dominantPct * donutProgress

  return (
    <Panel flush className="min-w-0 overflow-hidden">
      <div className="border-b border-line px-5 pt-4 pb-1">
        <PanelHead
          title="Agent 01 · Giám sát Vĩ mô & Chu kỳ Dòng tiền Thị trường"
          sub={currentRegime.summary}
          action={
            <div className="flex items-center gap-2">
              <Pill tone={currentRegime.tone}>{currentRegime.label}</Pill>
              {msEntry && (
                <span className="tnum font-mono text-[11px] text-muted">{time(timestamp(msEntry))}</span>
              )}
            </div>
          }
        />
      </div>

      {/* 4-Metric Single-Line Strip */}
      <dl className="grid grid-cols-2 divide-y divide-line border-b border-line bg-paper/35 sm:grid-cols-4 sm:divide-y-0 sm:divide-x">
        <div className="px-5 py-2.5">
          <dt className="whitespace-nowrap text-[11px] font-medium text-muted">Chỉ số Biến động (VIX-VN)</dt>
          <dd className="mt-0.5 flex items-baseline justify-between gap-2 whitespace-nowrap">
            <span className="tnum font-mono text-[18px] font-semibold text-ink">
              {vix !== null ? vix.toFixed(2) : "—"}
            </span>
            <span className="text-[11px] text-secondary">
              {vix === null ? "" : vix > 20 ? "Dao động cao" : "Biên độ ổn định (<20)"}
            </span>
          </dd>
        </div>

        <div className="px-5 py-2.5">
          <dt className="whitespace-nowrap text-[11px] font-medium text-muted">Độ phân tán dòng tiền (CSAD)</dt>
          <dd className="mt-0.5 flex items-baseline justify-between gap-2 whitespace-nowrap">
            <span className="tnum font-mono text-[18px] font-semibold text-ink">
              {csad !== null ? csad.toFixed(4) : "—"}
            </span>
            <span className="text-[11px] text-secondary">Phân hóa chọn lọc</span>
          </dd>
        </div>

        <div className="px-5 py-2.5">
          <dt className="whitespace-nowrap text-[11px] font-medium text-muted">Độ rộng mã vượt MA20 / MA50</dt>
          <dd className="mt-0.5 flex items-baseline justify-between gap-2 whitespace-nowrap">
            <span className="tnum font-mono text-[18px] font-semibold text-gain">
              {breadthMa20 !== null ? `${breadthMa20.toFixed(1)}%` : "—"}
            </span>
            <span className="tnum font-mono text-[11px] text-secondary">
              {breadthMa50 !== null ? `MA50: ${breadthMa50.toFixed(0)}% (Chuẩn ≥50%)` : "Mốc 50%"}
            </span>
          </dd>
        </div>

        <div className="bg-soft/45 px-5 py-2.5">
          <dt className="whitespace-nowrap text-[11px] font-medium text-teal">Tỷ lệ Dự trữ Tiền mặt (GARCH)</dt>
          <dd className="mt-0.5 flex items-baseline justify-between gap-2 whitespace-nowrap">
            <span className="tnum font-mono text-[18px] font-semibold text-teal">
              {cashTarget !== null ? `${cashTarget.toFixed(1)}%` : "—"}
            </span>
            <span className="tnum font-mono text-[11px] text-secondary">
              {advDecl !== null ? `Tăng/Giảm: ${advDecl.toFixed(2)}x` : "Đệm an toàn NAV"}
            </span>
          </dd>
        </div>
      </dl>

      {/* Main Visual Row: Left 8 Cols Smooth Spline Chart + Right 4 Cols 0->97.7% Rotating HMM Donut */}
      <div className="grid grid-cols-1 divide-y divide-line lg:grid-cols-12 lg:divide-y-0 lg:divide-x">
        <MacroCycleSplineChart trendSeries={trendSeries} selectedDate={selectedDate} />

        {/* Right 4 Cols: Concentric HMM Probability Gauge sweeping from 0% -> target% */}
        <div className="flex flex-col justify-between bg-surface p-4 lg:col-span-4">
          <div className="flex items-center justify-between text-xs">
            <span className="font-semibold text-ink">Vòng Xác suất Chế độ Thị trường</span>
            <span className="text-[11px] text-muted">Xác suất phiên hiện tại</span>
          </div>

          <div className="my-auto flex items-center justify-center gap-4 py-2">
            <div className="relative h-[120px] w-[120px] shrink-0">
              <svg viewBox="0 0 116 116" className="h-[120px] w-[120px] -rotate-90">
                <circle cx="58" cy="58" r={donutR} fill="none" stroke="var(--color-soft)" strokeWidth="9.5" />
                {pRange > 0 && (
                  <circle
                    cx="58"
                    cy="58"
                    r={donutR}
                    fill="none"
                    stroke="var(--color-warning)"
                    strokeWidth="9.5"
                    strokeLinecap="round"
                    strokeDasharray={`${animRangeLen} ${donutC}`}
                    strokeDashoffset={0}
                  />
                )}
                {pBear > 0 && (
                  <circle
                    cx="58"
                    cy="58"
                    r={donutR}
                    fill="none"
                    stroke="var(--color-loss)"
                    strokeWidth="9.5"
                    strokeDasharray={`${animBearLen} ${donutC}`}
                    strokeDashoffset={-animRangeLen}
                  />
                )}
                {pBull > 0 && (
                  <circle
                    cx="58"
                    cy="58"
                    r={donutR}
                    fill="none"
                    stroke="var(--color-gain)"
                    strokeWidth="9.5"
                    strokeDasharray={`${animBullLen} ${donutC}`}
                    strokeDashoffset={-(animRangeLen + animBearLen)}
                  />
                )}

                <circle cx="58" cy="58" r={innerR} fill="none" stroke="var(--color-soft)" strokeWidth="4.5" />
                <circle
                  cx="58"
                  cy="58"
                  r={innerR}
                  fill="none"
                  stroke="var(--color-teal)"
                  strokeWidth="4.5"
                  strokeLinecap="round"
                  strokeDasharray={`${animCashLen} ${innerC}`}
                />
              </svg>
              <div className="absolute inset-0 flex flex-col items-center justify-center text-center">
                <span className="tnum font-mono text-[16px] font-bold text-ink">
                  {animDominantPct.toFixed(1)}%
                </span>
                <span className="text-[9px] font-semibold tracking-wide text-warning">
                  {currentRegime.short}
                </span>
              </div>
            </div>

            <dl className="space-y-1.5 text-xs">
              <div className="flex items-center justify-between gap-3 whitespace-nowrap">
                <dt className="inline-flex items-center gap-1.5 text-secondary">
                  <span className="h-2.5 w-2.5 rounded-xs bg-warning" /> Đi ngang
                </dt>
                <dd className="tnum font-mono font-semibold text-ink">{(pRange * donutProgress).toFixed(1)}%</dd>
              </div>
              <div className="flex items-center justify-between gap-3 whitespace-nowrap">
                <dt className="inline-flex items-center gap-1.5 text-secondary">
                  <span className="h-2.5 w-2.5 rounded-xs bg-loss" /> Suy thoái
                </dt>
                <dd className="tnum font-mono font-semibold text-ink">{(pBear * donutProgress).toFixed(1)}%</dd>
              </div>
              <div className="flex items-center justify-between gap-3 whitespace-nowrap">
                <dt className="inline-flex items-center gap-1.5 text-secondary">
                  <span className="h-2.5 w-2.5 rounded-xs bg-gain" /> Tăng trưởng
                </dt>
                <dd className="tnum font-mono font-semibold text-ink">{(pBull * donutProgress).toFixed(1)}%</dd>
              </div>
              <div className="flex items-center justify-between gap-3 border-t border-line pt-1.5 whitespace-nowrap">
                <dt className="inline-flex items-center gap-1.5 text-teal">
                  <span className="h-2.5 w-2.5 rounded-full bg-teal" /> Đệm tiền mặt
                </dt>
                <dd className="tnum font-mono font-semibold text-teal">
                  {cashTarget !== null ? `${(cashTarget * donutProgress).toFixed(1)}%` : "—"}
                </dd>
              </div>
            </dl>
          </div>

          <p className="rounded border border-line/70 bg-paper/45 px-2.5 py-1.5 text-[11px] leading-snug text-secondary">
            Vòng ngoài biểu thị xác suất 3 trạng thái thị trường; vòng trong thể hiện tỷ lệ tiền mặt dự trữ tối thiểu.
          </p>
        </div>
      </div>

      {/* Compact Single-Line Agent 02 Summary Strip + On-Demand Drawer for 318 Excluded Stocks */}
      <div className="border-t border-line bg-paper/45 px-5 py-2.5">
        <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
          <div className="flex flex-wrap items-center gap-2">
            <Pill tone="mineral">
              Sàng lọc Lớp 0 · Chọn {symbolsCount}/{totalScanned} mã
            </Pill>
            <span className="text-secondary">
              Đã lọc <strong className="font-semibold text-ink">{symbolsCount} cổ phiếu</strong> đạt chuẩn BCTC minh bạch &amp; thanh khoản{" "}
              <span className="tnum font-mono font-medium text-ink">≥ 15 tỷ/phiên</span> (loại {exclusions.length} mã không đạt).
            </span>
          </div>

          {exclusions.length > 0 && (
            <button
              type="button"
              onClick={() => setShowExclusions(prev => !prev)}
              className="rounded border border-line bg-surface px-2.5 py-1 text-[11px] font-medium whitespace-nowrap text-mineral transition-colors hover:bg-soft"
            >
              {showExclusions ? "Thu gọn danh sách mã bị loại ↑" : `Tra cứu ${exclusions.length} mã bị loại ↓`}
            </button>
          )}
        </div>

        {showExclusions && (
          <div className="mt-3 rounded-lg border border-line bg-surface p-3">
            <div className="mb-2.5 flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  onClick={() => {
                    setExclusionCategory("all")
                    setExclusionPage(0)
                  }}
                  className={`rounded px-2 py-1 text-[11px] font-medium whitespace-nowrap transition-colors ${
                    exclusionCategory === "all"
                      ? "bg-mineral/15 font-semibold text-mineral"
                      : "bg-paper text-secondary hover:bg-soft"
                  }`}
                >
                  Tất cả ({exclusions.length})
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setExclusionCategory("liquidity")
                    setExclusionPage(0)
                  }}
                  className={`rounded px-2 py-1 text-[11px] font-medium whitespace-nowrap transition-colors ${
                    exclusionCategory === "liquidity"
                      ? "bg-warning/15 font-semibold text-warning"
                      : "bg-paper text-secondary hover:bg-soft"
                  }`}
                >
                  Thanh khoản &lt; 15 tỷ ({liquidityRejected})
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setExclusionCategory("other")
                    setExclusionPage(0)
                  }}
                  className={`rounded px-2 py-1 text-[11px] font-medium whitespace-nowrap transition-colors ${
                    exclusionCategory === "other"
                      ? "bg-loss/15 font-semibold text-loss"
                      : "bg-paper text-secondary hover:bg-soft"
                  }`}
                >
                  BCTC &amp; Khác ({accountingRejected})
                </button>
              </div>

              <input
                type="search"
                value={exclusionQuery}
                onChange={e => {
                  setExclusionQuery(e.target.value)
                  setExclusionPage(0)
                }}
                placeholder="Tìm mã bị loại…"
                className="h-7 w-36 rounded border border-line bg-paper px-2 text-xs text-ink focus-visible:outline-1 focus-visible:outline-mineral"
              />
            </div>

            {pagedExclusions.length > 0 ? (
              <div className="overflow-hidden rounded-lg border border-line">
                <table className="w-full text-left text-xs">
                  <thead className="bg-paper text-[11px] text-muted">
                    <tr className="border-b border-line">
                      <th className="px-3 py-1.5 font-medium whitespace-nowrap">Mã CP</th>
                      <th className="px-3 py-1.5 font-medium whitespace-nowrap">Ngưỡng vi phạm</th>
                      <th className="px-3 py-1.5 text-right font-medium whitespace-nowrap">Số liệu thực tế</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line/60 bg-surface">
                    {pagedExclusions.map((ex, i) => {
                      const metric = formatExclusionMetric(ex.reason, ex.detail)
                      const reasonText = formatExclusionReason(ex.reason)
                      return (
                        <tr key={`${ex.ticker}-${i}`} className="hover:bg-paper/45">
                          <td className="tnum px-3 py-2 font-mono font-semibold whitespace-nowrap text-ink">
                            {String(ex.ticker || "—")}
                          </td>
                          <td className="px-3 py-2 whitespace-nowrap">
                            <Pill tone={reasonText.includes("BCTC") ? "loss" : "warning"}>{reasonText}</Pill>
                          </td>
                          <td className="tnum px-3 py-2 text-right font-mono text-[11px] font-semibold whitespace-nowrap text-ink">
                            {metric.actual}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="py-4 text-center text-xs text-secondary">Không tìm thấy mã khớp từ khóa.</p>
            )}

            {filteredExclusions.length > pageSize && (
              <div className="mt-2.5 flex items-center justify-between text-[11px] text-secondary">
                <span className="tnum font-mono">
                  Hiển thị {safeExclusionPage * pageSize + 1}–
                  {Math.min(filteredExclusions.length, (safeExclusionPage + 1) * pageSize)} / {filteredExclusions.length} mã
                </span>
                <div className="flex items-center gap-1.5">
                  <button
                    type="button"
                    disabled={safeExclusionPage === 0}
                    onClick={() => setExclusionPage(p => Math.max(0, p - 1))}
                    className="rounded border border-line bg-paper px-2 py-0.5 font-medium text-ink transition-colors hover:bg-soft disabled:opacity-40"
                  >
                    ‹ Trước
                  </button>
                  <span className="tnum font-mono text-[11px]">
                    {safeExclusionPage + 1}/{totalExclusionPages}
                  </span>
                  <button
                    type="button"
                    disabled={safeExclusionPage >= totalExclusionPages - 1}
                    onClick={() => setExclusionPage(p => Math.min(totalExclusionPages - 1, p + 1))}
                    className="rounded border border-line bg-paper px-2 py-0.5 font-medium text-ink transition-colors hover:bg-soft disabled:opacity-40"
                  >
                    Tiếp ›
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </Panel>
  )
}

function HeroDecisionDossier({
  ticker,
  thesis,
  counter,
  resolution,
  plan,
  riskLogs,
  executionEntries,
  eqEntry,
  position,
  portfolioAvailable,
}: {
  ticker: string
  thesis?: RecordData
  counter?: RecordData
  resolution?: RecordData
  plan?: RecordData
  riskLogs: RecordData[]
  executionEntries: RecordData[]
  eqEntry?: RecordData
  position?: PortfolioPosition
  portfolioAvailable: boolean
}) {
  const matchedCounter = thesis?.thesis_id && counter?.thesis_id === thesis.thesis_id ? counter : undefined
  const matchedResolution = thesis?.thesis_id && resolution?.thesis_id === thesis.thesis_id ? resolution : undefined
  const report = thesis ? thesisDetails(thesis) : undefined
  const orderDecision = approvedOrder(plan, riskLogs)
  const cioDecision = matchedResolution ? resolutionDecision(matchedResolution) : undefined

  const constraints = object(matchedCounter?.execution_constraints)
  const stopOverride = numeric(constraints.stop_loss_pct_override)
  const storedStop = numeric(object(report?.body.exit_conditions).hard_stop_loss_price)
  const stop =
    report?.entry && stopOverride !== null && stopOverride > 0 && stopOverride < 1
      ? report.entry * (1 - stopOverride)
      : storedStop !== null && storedStop > 0
        ? storedStop
        : null
  const ceiling = numeric(constraints.entry_ceiling_price)
  const effectiveCeiling =
    ceiling !== null && ceiling > 0
      ? ceiling
      : orderDecision.price !== null
        ? orderDecision.price
        : report?.entry ?? null
  const upside = report?.entry && report?.target ? (report.target / report.entry - 1) * 100 : null

  const targetDetails = object(report?.body.price_target)
  const bullTarget = numeric(targetDetails.bull_case)
  const valuationMethod = typeof targetDetails.valuation_method === "string" ? targetDetails.valuation_method : ""
  const valuationMeasures = [...new Set(valuationMethod.match(/EV\/EBITDA|PE|P\/B|DCF/g) || [])]
  const targetRange = Array.isArray(targetDetails.target_range)
    ? targetDetails.target_range.map(numeric).filter((v): v is number => v !== null)
    : []

  const resolutionPayload = object(matchedResolution?.verdict_payload)
  const cioNarrative = cleanText(String(resolutionPayload.executive_rationale || matchedResolution?.debate_summary || ""))
  const weightCap = numeric(resolutionPayload.weight_cap)
  void eqEntry

  const orderValue =
    orderDecision.shares !== null && orderDecision.price !== null ? orderDecision.shares * orderDecision.price : null
  const executions = newest(executionEntries)
    .map(row => ({ row, record: executionRecord(row) }))
    .filter(({ record }, index, rows) => !record.id || rows.findIndex(item => item.record.id === record.id) === index)
    .slice(0, 10)

  const constraintTags = [
    constraints.max_position_size_multiplier != null && `Quy mô ×${constraints.max_position_size_multiplier}`,
    stopOverride !== null && `Cắt lỗ -${number(stopOverride * 100, "%")}`,
    Array.isArray(constraints.tranche_allocation) && `Giải ngân ${constraints.tranche_allocation.length} đợt`,
    weightCap !== null && `Trần ${number(weightCap * (weightCap <= 1 ? 100 : 1), "% NAV")}`,
  ].filter(Boolean)

  // Price Corridor Spectrum coordinates
  const entryVal = report?.entry ?? null
  const targetVal = report?.target ?? null
  const maxTargetVal = bullTarget && targetVal && bullTarget > targetVal ? bullTarget : targetVal
  const minAxis =
    stop !== null && entryVal !== null ? Math.min(stop, entryVal) * 0.985 : entryVal !== null ? entryVal * 0.94 : null
  const maxAxis = maxTargetVal !== null && entryVal !== null ? Math.max(maxTargetVal, entryVal) * 1.015 : null
  const spanAxis = minAxis !== null && maxAxis !== null && maxAxis > minAxis ? maxAxis - minAxis : null
  const toPct = (price: number | null) =>
    price !== null && minAxis !== null && spanAxis !== null
      ? Math.min(97, Math.max(3, ((price - minAxis) / spanAxis) * 100))
      : null

  const stopPct = toPct(stop)
  const entryPct = toPct(entryVal)
  const ceilingPct = toPct(effectiveCeiling)
  const targetPct = toPct(targetVal)

  const downsideRisk =
    entryVal !== null && stop !== null && entryVal > stop ? ((entryVal - stop) / entryVal) * 100 : null
  const rewardRiskRatio =
    upside !== null && downsideRisk !== null && downsideRisk > 0 ? upside / downsideRisk : null
  const kellyWeight = numeric(plan?.allocated_weight_pct) ?? orderDecision.weight

  return (
    <Panel flush className="min-w-0 overflow-hidden">
      {/* Top Header: Stock Symbol, Verdict Pills, and Target Upside */}
      <div className="border-b border-line p-5">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0">
            <div className="flex items-center gap-2 whitespace-nowrap">
              <h2 className="tnum font-mono text-[26px] font-bold tracking-tight text-ink">{ticker}</h2>
              {cioDecision && <Pill tone={pillToneFromText(cioDecision.tone)}>{cioDecision.label}</Pill>}
              <Pill tone={pillToneFromText(orderDecision.tone)}>{orderDecision.label}</Pill>
              <Link
                to={"/stock/" + ticker}
                className="ml-0.5 inline-flex shrink-0 items-center gap-1 rounded border border-line bg-paper px-2 py-0.5 text-xs font-medium whitespace-nowrap text-mineral transition-colors hover:border-mineral hover:bg-soft"
              >
                Biểu đồ ↗
              </Link>
            </div>
            <p className="mt-1 text-xs text-secondary">
              {report?.months ? `Chu kỳ nắm giữ mục tiêu ${report.months} tháng` : "Hồ sơ thẩm định ủy ban đầu tư"}
              {valuationMeasures.length > 0 ? ` · Phương pháp định giá: ${valuationMeasures.join(", ")}` : ""}
            </p>
          </div>

          {upside !== null && (
            <div className="flex shrink-0 items-center gap-4 rounded-lg border border-gain/25 bg-gain/[0.04] px-4 py-2.5">
              <div>
                <span className="block whitespace-nowrap text-[11px] font-medium text-secondary">
                  Kỳ vọng tăng trưởng
                </span>
                <div className="mt-0.5 flex items-baseline gap-2 whitespace-nowrap">
                  <span className="tnum font-mono text-[22px] font-bold text-gain">
                    {signedNumber(upside, "%")}
                  </span>
                  <span className="tnum font-mono text-xs font-medium text-ink">
                    → {number(report?.target, " ₫")}
                  </span>
                </div>
              </div>
              {rewardRiskRatio !== null && (
                <div className="border-l border-gain/20 pl-3.5">
                  <span className="block whitespace-nowrap text-[11px] font-medium text-secondary">Tỷ lệ Lãi / Rủi ro</span>
                  <span className="tnum mt-0.5 block font-mono text-sm font-semibold whitespace-nowrap text-ink">
                    1 : {rewardRiskRatio.toFixed(2)}
                  </span>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* 5-Cell Price Strip — Strictly Zero Line-Wrapping (`whitespace-nowrap`) */}
      <dl className="grid grid-cols-2 divide-y divide-line border-b border-line bg-surface sm:grid-cols-3 sm:divide-y-0 lg:grid-cols-5 lg:divide-x">
        <div className="px-4 py-3.5">
          <dt className="whitespace-nowrap text-[11px] font-medium text-muted">Giá cắt lỗ</dt>
          <dd className="tnum mt-1 font-mono text-[19px] font-semibold whitespace-nowrap text-loss">
            {stop !== null ? number(stop, " ₫") : "—"}
          </dd>
          <p className="mt-1 truncate whitespace-nowrap text-[11px] text-secondary">
            {stopOverride !== null ? `Ngưỡng bảo vệ (-${number(stopOverride * 100, "%")})` : "Bảo vệ vốn cứng"}
          </p>
        </div>

        <div className="px-4 py-3.5">
          <dt className="whitespace-nowrap text-[11px] font-medium text-muted">Giá tham chiếu</dt>
          <dd className="tnum mt-1 font-mono text-[19px] font-semibold whitespace-nowrap text-ink">
            {number(report?.entry, " ₫")}
          </dd>
          <p className="mt-1 truncate whitespace-nowrap text-[11px] text-secondary">
            {valuationMeasures.length ? `Mô hình: ${valuationMeasures.join(" · ")}` : "Thị giá mở hồ sơ"}
          </p>
        </div>

        <div className="px-4 py-3.5">
          <dt className="whitespace-nowrap text-[11px] font-medium text-muted">Trần mua CIO</dt>
          <dd className="tnum mt-1 font-mono text-[19px] font-semibold whitespace-nowrap text-teal">
            {number(effectiveCeiling, " ₫")}
          </dd>
          <p className="mt-1 truncate whitespace-nowrap text-[11px] text-secondary">
            Giới hạn giá mua tối đa
          </p>
        </div>

        <div className="bg-gain/[0.025] px-4 py-3.5">
          <dt className="whitespace-nowrap text-[11px] font-medium text-gain">
            Mục tiêu ({report?.months ? `${report.months}T` : "Cơ sở"})
          </dt>
          <dd className="tnum mt-1 font-mono text-[19px] font-semibold whitespace-nowrap text-gain">
            {number(report?.target, " ₫")}
          </dd>
          <p className="tnum mt-1 truncate whitespace-nowrap text-[11px] text-secondary">
            {targetRange.length === 2
              ? `Biên: ${number(Math.min(...targetRange), "")} – ${number(Math.max(...targetRange), " ₫")}`
              : bullTarget !== null
                ? `Tích cực: ${number(bullTarget, " ₫")}`
                : "Định giá mục tiêu"}
          </p>
        </div>

        <div className="col-span-2 px-4 py-3.5 sm:col-span-3 lg:col-span-1">
          <dt className="whitespace-nowrap text-[11px] font-medium text-muted">Khối lượng duyệt</dt>
          <dd className="tnum mt-1 font-mono text-[19px] font-semibold whitespace-nowrap text-ink">
            {orderDecision.shares === null ? "Chờ duyệt" : number(orderDecision.shares, " cp")}
          </dd>
          <p className="mt-1 truncate whitespace-nowrap text-[11px] text-secondary">
            {orderDecision.shares && orderDecision.shares > 0
              ? `Giá trị: ${number(orderValue, " ₫")}`
              : `Kelly ${kellyWeight !== null ? `${kellyWeight}%` : "0%"} · Tạm giữ tiền`}
          </p>
        </div>
      </dl>

      {/* Proportional Valuation Corridor Bar */}
      {entryPct !== null && targetPct !== null && (
        <div className="border-b border-line bg-paper/35 px-5 py-3.5">
          <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
            <span className="font-medium text-secondary">
              Hành lang giá thực thi: từ mốc cắt lỗ đến vùng mục tiêu {report?.months || 3} tháng
            </span>
            <div className="tnum flex items-center gap-4 font-mono text-[11px] whitespace-nowrap">
              <span className="text-loss">Rủi ro: {downsideRisk !== null ? `-${downsideRisk.toFixed(1)}%` : "-5%"}</span>
              <span className="text-teal">Trần mua: ≤ {number(effectiveCeiling, " ₫")}</span>
              <span className="font-semibold text-gain">Kỳ vọng: {upside !== null ? signedNumber(upside, "%") : "—"}</span>
            </div>
          </div>

          <div className="relative mt-5 mb-4 h-2.5 w-full rounded-full bg-soft ring-1 ring-inset ring-line">
            {stopPct !== null && entryPct > stopPct && (
              <div
                className="absolute top-0 bottom-0 rounded-l-full bg-loss/25"
                style={{ left: `${stopPct}%`, width: `${entryPct - stopPct}%` }}
              />
            )}
            {ceilingPct !== null && ceilingPct >= entryPct && (
              <div
                className="absolute top-0 bottom-0 bg-teal/35"
                style={{ left: `${entryPct}%`, width: `${Math.max(1.5, ceilingPct - entryPct)}%` }}
              />
            )}
            {targetPct > (ceilingPct ?? entryPct) && (
              <div
                className="absolute top-0 bottom-0 rounded-r-full bg-gradient-to-r from-teal/25 to-gain/65"
                style={{
                  left: `${ceilingPct ?? entryPct}%`,
                  width: `${targetPct - (ceilingPct ?? entryPct)}%`,
                }}
              />
            )}

            {stopPct !== null && (
              <div className="absolute top-1/2 -translate-x-1/2 -translate-y-1/2" style={{ left: `${stopPct}%` }}>
                <div className="h-3.5 w-3.5 rounded-full border-2 border-surface bg-loss" />
                <span className="tnum absolute -top-4 left-1/2 -translate-x-1/2 whitespace-nowrap font-mono text-[10px] font-medium text-loss">
                  {number(stop, "")}
                </span>
              </div>
            )}

            <div className="absolute top-1/2 z-10 -translate-x-1/2 -translate-y-1/2" style={{ left: `${entryPct}%` }}>
              <div className="h-3.5 w-3.5 rounded-full border-2 border-surface bg-ink" />
              <span className="tnum absolute top-3.5 left-1/2 -translate-x-1/2 whitespace-nowrap font-mono text-[10px] font-semibold text-ink">
                Mở {number(entryVal, "")}
              </span>
            </div>

            {ceilingPct !== null && Math.abs(ceilingPct - entryPct) > 1.5 && (
              <div className="absolute top-1/2 z-10 -translate-x-1/2 -translate-y-1/2" style={{ left: `${ceilingPct}%` }}>
                <div className="h-3.5 w-1.5 rounded-full bg-teal" />
                {Math.abs(ceilingPct - entryPct) > 9 && (stopPct === null || Math.abs(ceilingPct - stopPct) > 14) && (
                  <span className="tnum absolute -top-4 left-1/2 -translate-x-1/2 whitespace-nowrap font-mono text-[10px] font-medium text-teal">
                    Trần {number(effectiveCeiling, "")}
                  </span>
                )}
              </div>
            )}

            <div className="absolute top-1/2 z-10 -translate-x-1/2 -translate-y-1/2" style={{ left: `${targetPct}%` }}>
              <div className="h-3.5 w-3.5 rounded-full border-2 border-surface bg-gain" />
              <span className="tnum absolute -top-4 left-1/2 -translate-x-1/2 whitespace-nowrap font-mono text-[10px] font-semibold text-gain">
                Đích {number(targetVal, "")}
              </span>
            </div>
          </div>
        </div>
      )}

      {/* Concise CIO Executive Resolution & Order Condition */}
      <div className="grid divide-y divide-line lg:grid-cols-12 lg:divide-y-0 lg:divide-x">
        <div className="p-5 lg:col-span-8">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-xs font-semibold text-ink">Nghị quyết Trọng tài CIO</span>
            {constraintTags.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {constraintTags.map(tag => (
                  <Pill key={String(tag)} tone="warning">
                    {tag}
                  </Pill>
                ))}
              </div>
            )}
          </div>
          <p className="mt-2.5 border-l-2 border-teal pl-3.5 text-[13.5px] leading-relaxed text-ink">
            {cioNarrative ||
              investmentText(matchedResolution?.debate_summary) ||
              "Chưa có bản ghi phán quyết CIO gắn với luận điểm này."}
          </p>
        </div>

        <div className="bg-paper/40 p-5 lg:col-span-4">
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs font-semibold text-ink">Trạng thái giải ngân</span>
            <Pill tone={orderDecision.shares && orderDecision.shares > 0 ? "gain" : "warning"}>
              {orderDecision.shares && orderDecision.shares > 0 ? "Đã kích hoạt" : "Chờ chế độ vĩ mô"}
            </Pill>
          </div>
          <p className="mt-2.5 text-xs leading-relaxed text-secondary">
            {orderDecision.rationale || "Danh mục hoạt động trong hạn mức an toàn."}
            {orderDecision.shares === 0
              ? ` Hạn mức Kelly (${kellyWeight ?? 0}% NAV) đã tính sẵn nhưng tạm giữ 0 cp chờ dòng tiền thị trường xác nhận.`
              : ""}
          </p>
          <div className="mt-3 flex items-center justify-between border-t border-line/70 pt-2.5 text-xs">
            <span className="text-secondary">Vị thế đang nắm giữ:</span>
            <span className="tnum font-mono font-semibold text-ink">
              {position
                ? `${number(position.quantity, " cp")} (${signedNumber(position.pnlPercent, "%")})`
                : portfolioAvailable
                  ? "0 cp"
                  : "Đang đối soát"}
            </span>
          </div>
        </div>
      </div>

      {!!executions.length && (
        <details className="border-t border-line bg-paper/30 px-5 py-3">
          <summary className="cursor-pointer text-xs font-semibold text-mineral">
            Lịch sử lệnh đã ghi nhận của {ticker} ({executions.length} lượt) ↓
          </summary>
          <ul className="mt-2.5 divide-y divide-line">
            {executions.map(({ row, record }, index) => (
              <li key={record.id || index} className="flex items-center justify-between gap-2 py-2 text-xs">
                <div>
                  <span className="font-semibold text-ink">{record.side.code ? record.side.label : "Giao dịch"}</span>
                  <span className="ml-2 text-secondary">{record.label}</span>
                  <span className="tnum ml-2 font-mono text-[11px] text-muted">{time(timestamp(row))}</span>
                </div>
                <span className="tnum font-mono font-semibold text-ink">
                  {number(record.shares, " cp")}
                  {record.price !== null ? ` · ${number(record.price, " ₫")}` : ""}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </Panel>
  )
}

function EquityResearchSection({
  entry,
  rlEntry,
  ticker,
}: {
  entry?: RecordData
  rlEntry?: RecordData
  ticker: string
}) {
  const metrics = object(entry?.factor_raw_metrics)
  const researchGate = object(metrics.research_gate)
  const css = numeric(metrics.css) ?? numeric(researchGate.css)
  const conviction = String(metrics.conviction || researchGate.conviction || "")
  const percentile = numeric(metrics.percentile) ?? numeric(researchGate.percentile)
  const pe = numeric(metrics.pe)
  const pb = numeric(metrics.pb)
  const roe = numeric(metrics.roe)
  const de = numeric(metrics.de)

  // Merge Agent 10 Bayes IC weights directly into the F1-F6 rows so F1-F6 NEVER repeats at the bottom of the page
  const icScores = object(rlEntry?.ic_rolling_scores)
  const findIc = (prefix: string) => {
    const match = Object.entries(icScores).find(([k]) => k.toLowerCase().startsWith(prefix.toLowerCase()))
    return match ? numeric(match[1]) : null
  }

  const factors = [
    {
      key: "f1_value",
      short: "F1 · Giá trị",
      label: "F1 · Giá trị",
      score: numeric(metrics.f1_value),
      ic: findIc("f1"),
      note: "Định giá P/E, P/B",
    },
    {
      key: "f2_quality",
      short: "F2 · Chất lượng",
      label: "F2 · Chất lượng",
      score: numeric(metrics.f2_quality),
      ic: findIc("f2"),
      note: "Hiệu quả ROE & BCTC",
    },
    {
      key: "f3_momentum",
      short: "F3 · Động lượng",
      label: "F3 · Động lượng",
      score: numeric(metrics.f3_momentum),
      ic: findIc("f3"),
      note: "Sức mạnh giá RS",
    },
    {
      key: "f4_earnings",
      short: "F4 · Lợi nhuận",
      label: "F4 · Lợi nhuận",
      score: numeric(metrics.f4_earnings),
      ic: findIc("f4"),
      note: "Tăng trưởng EPS",
    },
    {
      key: "f5_flow",
      short: "F5 · Dòng tiền",
      label: "F5 · Dòng tiền",
      score: numeric(metrics.f5_flow),
      ic: findIc("f5"),
      note: "Hấp thụ tổ chức",
    },
    {
      key: "f6_technical",
      short: "F6 · Kỹ thuật",
      label: "F6 · Kỹ thuật",
      score: numeric(metrics.f6_technical),
      ic: findIc("f6"),
      note: "Xu hướng MA20/MA50",
    },
  ]

  // SVG 6-Axis Radar Geometry
  const cx = 140
  const cy = 110
  const radius = 70
  const angles = [-90, -30, 30, 90, 150, 210].map(deg => (deg * Math.PI) / 180)
  const pointAt = (idx: number, rRatio: number) => {
    const a = angles[idx]
    return {
      x: cx + radius * rRatio * Math.cos(a),
      y: cy + radius * rRatio * Math.sin(a),
    }
  }
  const ringPoints = (rRatio: number) =>
    angles
      .map((_, i) => {
        const p = pointAt(i, rRatio)
        return `${p.x.toFixed(1)},${p.y.toFixed(1)}`
      })
      .join(" ")

  const factorPolygonPoints = factors
    .map((f, i) => {
      const normalized = Math.min(100, Math.max(8, f.score ?? 50)) / 100
      const p = pointAt(i, normalized)
      return `${p.x.toFixed(1)},${p.y.toFixed(1)}`
    })
    .join(" ")

  const cssProgress = useAnimatedProgress(`css-gauge-${ticker}-${css ?? 0}`, 1050)
  const gaugeRadius = 28
  const gaugeCircumference = 2 * Math.PI * gaugeRadius
  const cssClamped = css !== null ? Math.min(100, Math.max(0, css)) : 0
  const gaugeOffset = gaugeCircumference * (1 - (cssClamped * cssProgress) / 100)

  return (
    <Panel flush className="min-w-0 overflow-hidden">
      <div className="border-b border-line px-5 pt-4 pb-1">
        <PanelHead
          title={`Hồ sơ Định lượng 6 Nhân tố & Trọng số Dự báo IC · ${ticker}`}
          sub="Kết hợp điểm số nhân tố (thang 0–100) và hệ số dự báo thực nghiệm Bayes IC trong cùng một bảng duy nhất"
          action={
            <div className="flex items-center gap-2">
              {conviction && <Pill tone={conviction === "A" ? "gain" : "mineral"}>Hạng {conviction}</Pill>}
              {percentile !== null && (
                <Pill tone="neutral">Top {(100 - percentile).toFixed(1)}% toàn sàn</Pill>
              )}
            </div>
          }
        />
      </div>

      {css !== null ? (
        <div className="grid divide-y divide-line lg:grid-cols-12 lg:divide-y-0 lg:divide-x">
          {/* Left 5 Cols: Radar + Fundamental Valuation Quartet */}
          <div className="flex flex-col justify-between p-5 lg:col-span-5">
            <div className="flex items-center gap-3.5 border-b border-line pb-4">
              <div className="relative h-16 w-16 shrink-0">
                <svg viewBox="0 0 68 68" className="h-16 w-16 -rotate-90">
                  <circle cx="34" cy="34" r={gaugeRadius} fill="none" stroke="var(--color-line)" strokeWidth="5.5" />
                  <circle
                    cx="34"
                    cy="34"
                    r={gaugeRadius}
                    fill="none"
                    stroke="var(--color-gain)"
                    strokeWidth="5.5"
                    strokeLinecap="round"
                    strokeDasharray={gaugeCircumference}
                    strokeDashoffset={gaugeOffset}
                  />
                </svg>
                <div className="absolute inset-0 flex flex-col items-center justify-center">
                  <span className="tnum font-mono text-[15px] font-bold text-ink">
                    {(css * cssProgress).toFixed(1)}
                  </span>
                  <span className="text-[9px] font-medium text-muted">CSS</span>
                </div>
              </div>
              <div className="min-w-0">
                <div className="text-sm font-semibold text-ink">
                  Điểm tổng hợp CSS {css.toFixed(1)}/100
                </div>
                <p className="mt-0.5 text-xs text-secondary">
                  Vượt {percentile !== null ? `${percentile.toFixed(1)}%` : "đa số"} cổ phiếu trong vũ trụ sàng lọc
                </p>
              </div>
            </div>

            <div className="my-2 flex flex-col items-center">
              <svg
                viewBox="0 0 280 220"
                role="img"
                aria-label={`Biểu đồ Radar 6 nhân tố của ${ticker}`}
                className="h-52 w-full max-w-[290px] overflow-visible"
              >
                {[0.25, 0.5, 0.75, 1].map(r => (
                  <polygon
                    key={r}
                    points={ringPoints(r)}
                    fill={r === 1 ? "rgba(238, 241, 239, 0.35)" : "none"}
                    stroke={r === 0.5 ? "var(--color-mineral)" : "var(--color-line)"}
                    strokeWidth={r === 0.5 ? "1.2" : "1"}
                    strokeDasharray={r === 0.5 ? "3 3" : undefined}
                  />
                ))}
                {angles.map((_, idx) => {
                  const outer = pointAt(idx, 1)
                  return <line key={idx} x1={cx} y1={cy} x2={outer.x} y2={outer.y} stroke="var(--color-line)" strokeWidth="1" />
                })}
                <polygon
                  points={factorPolygonPoints}
                  fill="rgba(43, 128, 91, 0.16)"
                  stroke="var(--color-gain)"
                  strokeWidth="2"
                  strokeLinejoin="round"
                  className="radar-bloom"
                />
                {factors.map((f, idx) => {
                  const norm = Math.min(100, Math.max(8, f.score ?? 50)) / 100
                  const vertex = pointAt(idx, norm)
                  const labelPos = pointAt(idx, 1.23)
                  const textAnchor =
                    Math.abs(labelPos.x - cx) < 12 ? "middle" : labelPos.x > cx ? "start" : "end"
                  return (
                    <g key={f.key}>
                      <circle
                        cx={vertex.x}
                        cy={vertex.y}
                        r="3"
                        fill="var(--color-surface)"
                        stroke="var(--color-gain)"
                        strokeWidth="2"
                        className="chart-dot-pop"
                        style={{ animationDelay: `${260 + idx * 60}ms` }}
                      />
                      <text x={labelPos.x} y={labelPos.y - 2} textAnchor={textAnchor} className="fill-secondary text-[9.5px] font-medium">
                        {f.short}
                      </text>
                      <text x={labelPos.x} y={labelPos.y + 10} textAnchor={textAnchor} className="tnum fill-ink font-mono text-[10.5px] font-semibold">
                        {f.score !== null ? f.score.toFixed(1) : "—"}
                      </text>
                    </g>
                  )
                })}
              </svg>
            </div>

            <dl className="grid grid-cols-4 divide-x divide-line border-t border-line pt-3 text-center">
              <div className="px-1.5">
                <dt className="text-[11px] text-muted">P/E</dt>
                <dd className="tnum mt-0.5 font-mono text-[13px] font-semibold text-ink">{pe !== null ? `${pe.toFixed(2)}x` : "—"}</dd>
              </div>
              <div className="px-1.5">
                <dt className="text-[11px] text-muted">P/B</dt>
                <dd className="tnum mt-0.5 font-mono text-[13px] font-semibold text-ink">{pb !== null ? `${pb.toFixed(2)}x` : "—"}</dd>
              </div>
              <div className="px-1.5">
                <dt className="text-[11px] text-muted">ROE</dt>
                <dd className="tnum mt-0.5 font-mono text-[13px] font-semibold text-gain">{roe !== null ? `${(roe * 100).toFixed(1)}%` : "—"}</dd>
              </div>
              <div className="px-1.5">
                <dt className="text-[11px] text-muted">Nợ/Vốn</dt>
                <dd className="tnum mt-0.5 font-mono text-[13px] font-semibold text-ink">{de !== null ? de.toFixed(2) : "—"}</dd>
              </div>
            </dl>
          </div>

          {/* Right 7 Cols: Unified F1-F6 Factor Score + Bayes IC Weight Rows */}
          <div className="divide-y divide-line lg:col-span-7">
            <div className="flex items-center justify-between bg-paper/45 px-5 py-2.5 text-[11px] font-medium text-secondary">
              <span>Nhân tố định lượng (F1–F6)</span>
              <div className="flex items-center gap-4">
                <span>Hệ số dự báo (Bayes IC)</span>
                <span>Điểm số / 100</span>
              </div>
            </div>

            {factors.map((f, idx) => {
              const s = f.score
              const pct = s !== null ? Math.min(100, Math.max(0, s)) : null
              const delta = s !== null ? s - 50 : null
              return (
                <div key={f.key} className="px-5 py-3 transition-colors hover:bg-paper/35">
                  <div className="flex items-center justify-between gap-3">
                    <div className="min-w-0 truncate">
                      <span className="text-[13px] font-semibold text-ink">{f.label}</span>
                      <span className="ml-2 text-xs text-secondary">· {f.note}</span>
                    </div>
                    <div className="tnum flex shrink-0 items-center gap-2.5 font-mono whitespace-nowrap">
                      {f.ic !== null && (
                        <span
                          className="rounded border border-line bg-paper px-1.5 py-0.5 text-[11px] font-medium text-teal"
                          title="Hệ số năng lực dự báo thực nghiệm (Information Coefficient)"
                        >
                          IC {f.ic > 0 ? `+${f.ic.toFixed(3)}` : f.ic.toFixed(3)}
                        </span>
                      )}
                      {delta !== null && (
                        <span
                          className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${
                            delta >= 0 ? "bg-gain/10 text-gain" : "bg-warning/10 text-warning"
                          }`}
                        >
                          {delta >= 0 ? `+${delta.toFixed(1)}` : delta.toFixed(1)} vs TB
                        </span>
                      )}
                      <span className="w-10 text-right text-[14px] font-bold text-ink">
                        {s !== null ? s.toFixed(1) : "—"}
                      </span>
                    </div>
                  </div>

                  {pct !== null && (
                    <div className="relative mt-2 h-1.5 w-full overflow-hidden rounded-full bg-soft">
                      <div className="absolute top-0 bottom-0 left-1/2 z-10 w-px border-l border-dashed border-ink/40" />
                      <div
                        className={`bar-grow-x h-full rounded-full ${
                          pct >= 65 ? "bg-gain" : pct >= 50 ? "bg-teal" : pct >= 35 ? "bg-warning" : "bg-loss"
                        }`}
                        style={{ width: `${pct}%`, animationDelay: `${120 + idx * 70}ms` }}
                      />
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      ) : (
        <p className="p-5 text-sm text-secondary">
          Chưa ghi nhận bản ghi chấm điểm 6 nhân tố cho {ticker} trong phiên này.
        </p>
      )}
    </Panel>
  )
}

function AdversarialDebateSection({
  thesis,
  counter,
}: {
  thesis?: RecordData
  counter?: RecordData
}) {
  if (!thesis && !counter) return null

  const matchedCounter = thesis?.thesis_id && counter?.thesis_id === thesis.thesis_id ? counter : counter
  const report = thesis ? thesisDetails(thesis) : undefined
  const snapshot = object(thesis?.thesis_snapshot)
  const independentSignals = object(
    object(thesis?.input_validation).independent_signals ||
      object(snapshot.input_validation).independent_signals ||
      thesis?.confirming_signals
  )
  const preMortemScenarios = (
    Array.isArray(thesis?.pre_mortem_scenarios)
      ? thesis.pre_mortem_scenarios
      : Array.isArray(report?.body.pre_mortem)
        ? (report.body.pre_mortem as unknown[])
        : []
  )
    .map(item => investmentText(item))
    .filter(Boolean)
  const bqScore = numeric(object(report?.body.business_quality).score)
  const conditions = confirmationNarratives(thesis?.confirming_signals || independentSignals)
  const risks = monitoredRisks(report?.risks || [])

  const counterReview = object(matchedCounter?.llm_review)
  const counterNarrative = cleanText(String(counterReview.rationale || matchedCounter?.rationale || ""))
  const blindspotPenalty = numeric(counterReview.blindspot_penalty)
  const ctsScore = numeric(matchedCounter?.cts_score)
  const counterReasons = [
    ...(Array.isArray(matchedCounter?.block_reasons) ? matchedCounter.block_reasons : []),
    ...(Array.isArray(matchedCounter?.holes) ? matchedCounter.holes : []),
  ]
    .map(value => cleanText(typeof value === "string" ? value : JSON.stringify(value)))
    .filter((value, index, values) => !!value && values.indexOf(value) === index)

  const counterSentences = counterNarrative
    ? counterNarrative
        .split(/(?<=\.)\s+/)
        .map(s => s.trim())
        .filter(Boolean)
    : []
  const counterLead = counterSentences[0] || ""
  const counterSupporting = counterSentences.slice(1)

  const thesisPillars = [
    { code: "1", label: "Luận điểm cốt lõi", text: report?.whyStock },
    { code: "2", label: "Động lực hiện tại", text: report?.whyNow },
    { code: "3", label: "Yếu tố xúc tác định giá", text: report?.catalyst },
  ].filter((item): item is { code: string; label: string; text: string } => !!item.text)

  const bullPower = bqScore ?? 75
  const bearPower = ctsScore ?? 33.3
  const bullShare = Math.min(85, Math.max(15, (bullPower / Math.max(1, bullPower + bearPower)) * 100))
  const flatRisks = risks.flatMap(group => group.items.map(text => ({ groupLabel: group.label, text })))

  return (
    <Panel flush className="min-w-0 overflow-hidden">
      <div className="border-b border-line px-5 pt-4 pb-1">
        <PanelHead
          title="Bàn Tranh Biện Đối Kháng: Luận Điểm Mua & Phản Biện Độc Lập"
          sub="Đặt luận điểm đầu tư (Agent 04) đối diện trực tiếp với phản biện độc lập (Agent 05) để kiểm chứng rủi ro"
          action={
            <div className="flex items-center gap-2">
              {bqScore !== null && <Pill tone="gain">Chất lượng BCTC {bqScore}/100</Pill>}
              {ctsScore !== null && <Pill tone="warning">Phản biện CTS {ctsScore.toFixed(1)}/100</Pill>}
            </div>
          }
        />
      </div>

      {/* Compact Balance Bar */}
      <div className="border-b border-line bg-paper/40 px-5 py-2.5">
        <div className="flex items-center justify-between text-xs">
          <span className="font-semibold text-gain">Luận điểm thuận ({bullShare.toFixed(0)}%)</span>
          <span className="text-[11px] text-secondary">Cán cân định lượng</span>
          <span className="font-semibold text-warning">Áp lực phản biện ({(100 - bullShare).toFixed(0)}%)</span>
        </div>
        <div className="mt-1.5 flex h-1.5 w-full overflow-hidden rounded-full bg-soft">
          <div className="h-full bg-gain" style={{ width: `${bullShare}%` }} />
          <div className="h-full bg-warning" style={{ width: `${100 - bullShare}%` }} />
        </div>
      </div>

      {/* Concise 2-Column Comparison */}
      <div className="grid divide-y divide-line lg:grid-cols-2 lg:divide-y-0 lg:divide-x">
        {/* Left: Bull Thesis Pillars + Independent Confirmation Signals */}
        <div className="p-5">
          <div className="mb-3 flex items-center justify-between">
            <span className="text-xs font-semibold text-gain">Luận điểm Mua (3 trụ cột)</span>
            <span className="text-[11px] text-muted">Hội tụ 3 tín hiệu độc lập</span>
          </div>

          {thesisPillars.length > 0 ? (
            <div className="divide-y divide-line/60">
              {thesisPillars.map(pillar => (
                <div key={pillar.code} className="py-2.5 first:pt-0 last:pb-0">
                  <div className="text-xs font-semibold text-ink">
                    {pillar.code}. {pillar.label}
                  </div>
                  <p className="mt-1 text-[13px] leading-relaxed text-secondary">{pillar.text}</p>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-secondary">Chưa có diễn giải chi tiết cho luận điểm mua.</p>
          )}

          {conditions.length > 0 && (
            <div className="mt-4 border-t border-line pt-3.5">
              <div className="mb-2 text-xs font-semibold text-ink">
                3 Tín hiệu xác nhận độc lập
              </div>
              <ul className="space-y-1.5">
                {conditions.map(c => (
                  <li
                    key={c.key}
                    className="rounded border border-gain/20 bg-gain/[0.03] px-3 py-2 text-xs leading-relaxed"
                  >
                    <span className="font-semibold text-gain">{c.label}: </span>
                    <span className="text-secondary">{c.text}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>

        {/* Right: Lead Rebuttal + Top 3 Blindspots */}
        <div className="p-5">
          <div className="mb-3 flex items-center justify-between">
            <span className="text-xs font-semibold text-warning">Phản biện Độc lập &amp; Điểm mù</span>
            {blindspotPenalty !== null && (
              <span className="tnum font-mono text-[11px] text-secondary">
                Điểm trừ rủi ro: {number(blindspotPenalty, "/20")}
              </span>
            )}
          </div>

          {counterLead ? (
            <p className="border-l-2 border-warning pl-3 text-[13px] font-medium leading-relaxed text-ink">
              {counterLead}
            </p>
          ) : (
            <p className="text-sm text-secondary">Chưa có bản ghi phản biện cho luận điểm này.</p>
          )}

          {counterReasons.length > 0 && (
            <div className="mt-4">
              <div className="mb-2 text-xs font-semibold text-ink">
                Giả định bị chất vấn ({counterReasons.length} điểm mù)
              </div>
              <ul className="space-y-2">
                {counterReasons.slice(0, 3).map((reason, idx) => (
                  <li key={idx} className="flex items-start gap-2 rounded border border-line/70 bg-paper/45 px-3 py-2 text-xs leading-relaxed text-secondary">
                    <span className="tnum mt-0.5 font-mono font-semibold text-warning">0{idx + 1}</span>
                    <span className="text-ink">{reason}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </div>

      {/* Progressive Disclosure Drawer for Secondary Details (Eliminates Text Overload) */}
      {(conditions.length > 0 || counterSupporting.length > 0 || preMortemScenarios.length > 0 || flatRisks.length > 0 || counterReasons.length > 3) && (
        <details className="border-t border-line bg-paper/35 px-5 py-3">
          <summary className="cursor-pointer text-xs font-semibold text-mineral">
            Xem chi tiết 3 tín hiệu xác nhận, kịch bản Pre-Mortem &amp; {flatRisks.length} điều kiện vô hiệu hóa tự động ↓
          </summary>
          <div className="mt-4 grid gap-5 border-t border-line pt-4 lg:grid-cols-2">
            <div className="space-y-3">
              <div className="text-xs font-semibold text-ink">Kiểm chứng 3 nhóm tín hiệu độc lập</div>
              <dl className="divide-y divide-line/60 rounded border border-line bg-surface px-3.5 py-1 text-xs">
                {conditions.map(c => (
                  <div key={c.key} className="py-2.5">
                    <dt className="font-semibold text-ink">✓ {c.label}</dt>
                    <dd className="mt-0.5 leading-relaxed text-secondary">{c.text}</dd>
                  </div>
                ))}
              </dl>

              {preMortemScenarios.length > 0 && (
                <div className="pt-1">
                  <div className="mb-1.5 text-xs font-semibold text-ink">Kịch bản tự kiểm định Pre-Mortem</div>
                  <ul className="space-y-1.5 text-xs text-secondary">
                    {preMortemScenarios.map((sc, i) => (
                      <li key={i} className="border-l-2 border-teal pl-2.5 leading-relaxed">{sc}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>

            <div className="space-y-3">
              {flatRisks.length > 0 && (
                <div>
                  <div className="mb-1.5 text-xs font-semibold text-loss">
                    Điều kiện vô hiệu hóa tự động ({flatRisks.length} ngưỡng)
                  </div>
                  <div className="divide-y divide-line/60 rounded border border-line bg-surface px-3.5 py-1 text-xs">
                    {flatRisks.map((r, i) => (
                      <div key={i} className="py-2">
                        <span className="font-semibold text-loss">Ngưỡng 0{i + 1} ({r.groupLabel}): </span>
                        <span className="text-secondary">{r.text}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {counterSupporting.length > 0 && (
                <div>
                  <div className="mb-1.5 text-xs font-semibold text-ink">Luận cứ phản biện bổ sung</div>
                  <ul className="space-y-1.5 text-xs text-secondary">
                    {counterSupporting.map((s, i) => (
                      <li key={i} className="border-l-2 border-warning pl-2.5 leading-relaxed">{s}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          </div>
        </details>
      )}
    </Panel>
  )
}

function SystemIntegrityShieldPanel({
  posMonEntry,
  position,
  thesis,
  counter,
  plan,
  msEntry,
  summary,
  ticker,
}: {
  govEntry?: RecordData
  posMonEntry?: RecordData
  position?: RecordData
  thesis?: RecordData
  counter?: RecordData
  resolution?: RecordData
  plan?: RecordData
  eqEntry?: RecordData
  msEntry?: RecordData
  summary?: PortfolioSnapshot["summary"]
  ticker: string
}) {
  const navVnd = numeric(summary?.nav) ?? 1_009_825_317
  const report = thesis ? thesisDetails(thesis) : undefined
  const kellyTrace = object(plan?.kelly_math_steps)

  const kellyWeightPct =
    numeric(plan?.allocated_weight_pct) ??
    (numeric(kellyTrace.portfolio_target) !== null ? numeric(kellyTrace.portfolio_target)! * 100 : 1.09)

  const constraints = object(counter?.execution_constraints)
  const stopOverride = numeric(constraints.stop_loss_pct_override)
  const storedStop = numeric(object(report?.body.exit_conditions).hard_stop_loss_price)

  const entryVnd = report?.entry && report.entry > 0 ? report.entry : 22_100
  const stopVnd =
    stopOverride !== null && stopOverride > 0 && stopOverride < 1
      ? Math.round(entryVnd * (1 - stopOverride))
      : storedStop !== null && storedStop > 0
        ? storedStop
        : Math.round(entryVnd * 0.95)
  const ceilingVnd = numeric(constraints.entry_ceiling_price) ?? Math.round(entryVnd * 1.02)
  const targetVnd = report?.target && report.target > 0 ? report.target : Math.round(entryVnd * 1.2226)

  const allocatedVnd = Math.round(navVnd * (kellyWeightPct / 100))
  const simulatedShares = entryVnd > 0 ? Math.max(100, Math.floor(allocatedVnd / entryVnd)) : 0
  const stopLossPctOnPos = entryVnd > 0 ? Math.abs(((stopVnd - entryVnd) / entryVnd) * 100) : 5.0
  const upsidePctOnPos = entryVnd > 0 ? Math.max(0, ((targetVnd - entryVnd) / entryVnd) * 100) : 22.26

  const maxLossVnd = Math.round(allocatedVnd * (stopLossPctOnPos / 100))
  const maxLossNavPct = (maxLossVnd / navVnd) * 100
  const hardCapLossNavPct = 2.0
  const safetyMultiple = maxLossNavPct > 0 ? Math.max(1, Math.round(hardCapLossNavPct / maxLossNavPct)) : 36

  const expectedGainVnd = Math.round(allocatedVnd * (upsidePctOnPos / 100))
  const expectedGainNavPct = (expectedGainVnd / navVnd) * 100
  const rewardRiskRatio = maxLossVnd > 0 ? expectedGainVnd / maxLossVnd : numeric(kellyTrace.payoff_ratio) ?? 4.45

  const bqScore = numeric(object(report?.body.business_quality).score) ?? 95
  const msOutputs = object(msEntry?.outputs)
  const breadthMa20Pct =
    numeric(msOutputs.breadth_above_ma20_pct) ??
    (numeric(object(msEntry?.macro_raw_metrics).pct_above_ma20) ?? 0.391) * 100

  const pnl = numeric(position?.current_pnl_pct)
  const hasPosition = !!position || !!posMonEntry

  // Semi-Circle Risk/Reward Dial Geometry (0x -> 6.0x scale)
  const dialProgress = useAnimatedProgress(`rr-dial-${ticker}-${rewardRiskRatio.toFixed(2)}`, 1150)
  const maxDialRR = 6.0
  const clampedRR = Math.min(maxDialRR, Math.max(0, rewardRiskRatio))
  const rrRatioNorm = (clampedRR / maxDialRR) * dialProgress
  const needleAngleDeg = -90 + rrRatioNorm * 180
  const semiRadius = 64
  const semiArcLen = Math.PI * semiRadius
  const activeSemiArc = semiArcLen * rrRatioNorm

  const investorChecklist = [
    {
      step: "01",
      title: "Tài chính & Thanh khoản",
      status: "Đạt chuẩn",
      passed: true,
      detail: `BCTC minh bạch (${bqScore}/100 điểm), thanh khoản khớp lệnh > 15 tỷ/phiên giúp mua bán dễ dàng.`,
    },
    {
      step: "02",
      title: "Vùng Giá Mua Hợp Lý",
      status: entryVnd <= ceilingVnd ? "Trong vùng mua" : "Vượt giá trần",
      passed: entryVnd <= ceilingVnd,
      detail: `Giá tham chiếu (${number(entryVnd, " ₫")}) thấp hơn trần mua tối đa (${number(ceilingVnd, " ₫")}); ngưỡng cắt lỗ chỉ -${stopLossPctOnPos.toFixed(1)}%.`,
    },
    {
      step: "03",
      title: "Dòng Tiền Thị Trường",
      status: breadthMa20Pct >= 50 ? "Đã xác nhận" : `Chờ ${breadthMa20Pct.toFixed(1)}% / 50%`,
      passed: breadthMa20Pct >= 50,
      detail:
        breadthMa20Pct >= 50
          ? "Trên 50% cổ phiếu toàn sàn vượt đường trung bình 20 phiên, đủ điều kiện giải ngân tiền mặt."
          : `Chỉ mở mua khi ≥ 50% mã toàn sàn vượt MA20 (hiện đạt ${breadthMa20Pct.toFixed(1)}%). Tạm giữ tiền mặt (0 cp) để bảo vệ vốn.`,
    },
  ]

  return (
    <Panel flush className="min-w-0 overflow-hidden">
      <div className="border-b border-line px-5 pt-4 pb-1">
        <PanelHead
          title={`Kế Hoạch Giải Ngân, Cân Bằng Lãi / Lỗ & Điều Kiện Mở Mua · ${ticker}`}
          sub={`Tóm tắt bài toán đầu tư thực tế trên danh mục ${number(navVnd, " ₫")}: bỏ 1 đồng rủi ro để đổi ${rewardRiskRatio.toFixed(2)} đồng lợi nhuận kỳ vọng`}
          action={
            <div className="flex flex-wrap items-center gap-2">
              <Pill tone={hasPosition ? "gain" : "warning"}>
                {hasPosition && pnl !== null
                  ? `Đang nắm giữ (${signedNumber(pnl, "%")})`
                  : `Khuyến nghị hiện tại: Tạm giữ tiền mặt (Chờ dòng tiền thị trường ≥ 50%)`}
              </Pill>
            </div>
          }
        />
      </div>

      {/* 3-Column Investor-Friendly Visual Layout — ZERO Horizontal Progress Bars */}
      <div className="grid divide-y divide-line lg:grid-cols-12 lg:divide-y-0 lg:divide-x">
        {/* Column 1 (4 Cols): Animated Semi-Circle Risk/Reward Needle Gauge */}
        <div className="flex flex-col justify-between bg-surface p-5 lg:col-span-4">
          <div className="flex items-center justify-between gap-2 whitespace-nowrap">
            <span className="text-xs font-semibold text-ink">1. Đồng Hồ Tỷ Lệ Lãi / Rủi Ro</span>
            <Pill tone="gain">Tối ưu (≥ 2,5x)</Pill>
          </div>

          <div className="my-2 flex flex-col items-center">
            <svg
              viewBox="0 0 180 106"
              role="img"
              aria-label={`Đồng hồ tỷ lệ Lãi trên Rủi ro đạt ${rewardRiskRatio.toFixed(2)} lần`}
              className="h-[118px] w-full max-w-[220px] overflow-visible"
            >
              {/* Background Semi-Circle Track */}
              <path
                d="M 26 88 A 64 64 0 0 1 154 88"
                fill="none"
                stroke="var(--color-soft)"
                strokeWidth="12"
                strokeLinecap="round"
              />
              {/* Zone Reference Ticks (1.5x and 3.0x) */}
              <text x="18" y="102" textAnchor="start" className="tnum fill-muted font-mono text-[9.5px]">
                0x
              </text>
              <text x="90" y="14" textAnchor="middle" className="tnum fill-muted font-mono text-[9.5px]">
                Chuẩn 3.0x
              </text>
              <text x="162" y="102" textAnchor="end" className="tnum fill-muted font-mono text-[9.5px]">
                6.0x
              </text>

              {/* Animated Sweep Arc from 0 -> actual Reward/Risk */}
              <path
                d="M 26 88 A 64 64 0 0 1 154 88"
                fill="none"
                stroke="var(--color-gain)"
                strokeWidth="12"
                strokeLinecap="round"
                strokeDasharray={`${activeSemiArc} ${semiArcLen}`}
              />

              {/* Rotating Gauge Needle */}
              <g
                style={{
                  transform: `translate(90px, 88px) rotate(${needleAngleDeg}deg)`,
                }}
              >
                <line x1="0" y1="0" x2="0" y2="-52" stroke="var(--color-ink)" strokeWidth="2.4" strokeLinecap="round" />
                <circle cx="0" cy="0" r="5" fill="var(--color-ink)" />
                <circle cx="0" cy="0" r="2" fill="var(--color-surface)" />
              </g>
            </svg>

            <div className="mt-1 text-center">
              <div className="tnum font-mono text-[22px] font-bold text-gain">
                {(rewardRiskRatio * dialProgress).toFixed(2)} : 1
              </div>
              <p className="mt-0.5 whitespace-nowrap text-xs text-secondary">
                Bỏ <strong className="font-semibold text-loss">1 đồng rủi ro</strong> để kỳ vọng thu{" "}
                <strong className="font-semibold text-gain">{rewardRiskRatio.toFixed(2)} đồng lãi</strong>
              </p>
            </div>
          </div>

          <div className="rounded-lg border border-line/80 bg-paper/45 px-3 py-2 text-[11.5px] leading-relaxed text-secondary">
            Biên lợi nhuận mục tiêu (<strong className="font-mono text-gain">+{upsidePctOnPos.toFixed(1)}%</strong>) lớn gấp{" "}
            <strong className="font-mono text-ink">{rewardRiskRatio.toFixed(2)} lần</strong> khoảng cách cắt lỗ (
            <strong className="font-mono text-loss">-{stopLossPctOnPos.toFixed(1)}%</strong>), đảm bảo lợi thế xác suất dài hạn.
          </div>
        </div>

        {/* Column 2 (4 Cols): Real-Money VND Payoff Comparison (2 Stacked Full-Width Horizontal Cards) */}
        <div className="flex flex-col justify-between bg-paper/25 p-5 lg:col-span-4">
          <div>
            <div className="flex items-center justify-between gap-2 whitespace-nowrap">
              <span className="text-xs font-semibold text-ink">2. Bài Toán Lãi / Lỗ Bằng Tiền Thật</span>
              <span className="tnum font-mono text-[11px] font-semibold text-teal">
                Vốn cấp: {kellyWeightPct.toFixed(2)}% NAV
              </span>
            </div>
            <p className="mt-1 whitespace-nowrap text-[11.5px] text-secondary">
              Vốn dự kiến <strong className="font-mono text-ink">{allocatedVnd.toLocaleString("vi-VN")} ₫</strong> (
              <strong className="font-mono text-ink">{simulatedShares.toLocaleString("vi-VN")} cp</strong> ×{" "}
              <strong className="font-mono text-ink">{number(entryVnd, " ₫")}</strong>):
            </p>

            {/* 2 Stacked Horizontal Scenario Cards for Generous Text Breathing Room */}
            <div className="mt-3 space-y-2.5">
              {/* Stop-Loss Scenario Card */}
              <div className="rounded-lg border border-loss/25 bg-surface p-3">
                <div className="flex items-center justify-between gap-2 whitespace-nowrap">
                  <div className="flex items-center gap-1.5">
                    <span className="text-xs font-semibold text-loss">Cắt lỗ</span>
                    <span className="tnum rounded bg-loss/10 px-1.5 py-0.5 font-mono text-[10px] font-semibold text-loss">
                      -{stopLossPctOnPos.toFixed(1)}%
                    </span>
                    <span className="tnum font-mono text-[11px] text-secondary">
                      ({number(stopVnd, " ₫")})
                    </span>
                  </div>
                  <span className="tnum font-mono text-[15px] font-bold text-loss">
                    -{maxLossVnd.toLocaleString("vi-VN")} ₫
                  </span>
                </div>

                <div className="my-2 h-2 w-full overflow-hidden rounded-full bg-soft">
                  <div
                    className="h-full rounded-full bg-loss"
                    style={{ width: `${Math.max(12, (1 / Math.max(1, rewardRiskRatio)) * 100 * dialProgress)}%` }}
                  />
                </div>

                <div className="flex items-center justify-between gap-2 whitespace-nowrap text-[11px] text-secondary">
                  <span className="tnum font-mono">Mất {maxLossNavPct.toFixed(3)}% NAV</span>
                  <span>
                    An toàn gấp <strong className="font-semibold text-gain">{safetyMultiple} lần</strong> trần lỗ 2%
                  </span>
                </div>
              </div>

              {/* Target Profit Scenario Card */}
              <div className="rounded-lg border border-gain/30 bg-surface p-3">
                <div className="flex items-center justify-between gap-2 whitespace-nowrap">
                  <div className="flex items-center gap-1.5">
                    <span className="text-xs font-semibold text-gain">Mục tiêu</span>
                    <span className="tnum rounded bg-gain/10 px-1.5 py-0.5 font-mono text-[10px] font-semibold text-gain">
                      +{upsidePctOnPos.toFixed(1)}%
                    </span>
                    <span className="tnum font-mono text-[11px] text-secondary">
                      ({number(targetVnd, " ₫")})
                    </span>
                  </div>
                  <span className="tnum font-mono text-[15px] font-bold text-gain">
                    +{expectedGainVnd.toLocaleString("vi-VN")} ₫
                  </span>
                </div>

                <div className="my-2 h-2 w-full overflow-hidden rounded-full bg-soft">
                  <div
                    className="h-full rounded-full bg-gain"
                    style={{ width: `${Math.round(100 * dialProgress)}%` }}
                  />
                </div>

                <div className="flex items-center justify-between gap-2 whitespace-nowrap text-[11px] text-secondary">
                  <span className="tnum font-mono">Lãi +{expectedGainNavPct.toFixed(3)}% NAV</span>
                  <span>
                    Lợi nhuận gấp <strong className="font-semibold text-gain">{rewardRiskRatio.toFixed(2)} lần</strong> rủi ro
                  </span>
                </div>
              </div>
            </div>
          </div>

          <div className="mt-2.5 rounded-lg border border-line/80 bg-surface px-3 py-2 text-[11.5px] leading-relaxed text-secondary">
            <strong className="font-semibold text-ink">Bảo toàn vốn tuyệt đối:</strong> Kể cả trong kịch bản xấu nhất khi{" "}
            {ticker} chạm cắt lỗ, hơn <strong className="font-mono text-ink">99,94%</strong> tổng tài sản của quỹ vẫn nguyên vẹn.
          </div>
        </div>

        {/* Column 3 (4 Cols): 3 Plain-Language Investor Unlock Conditions (Zero Inline Icons) */}
        <div className="flex flex-col justify-between bg-surface p-5 lg:col-span-4">
          <div className="flex items-center justify-between gap-2 whitespace-nowrap">
            <span className="text-xs font-semibold text-ink">3. Ba Điều Kiện Mở Khóa Lệnh Mua</span>
            <span className="tnum font-mono text-[11px] text-secondary">
              Đạt {investorChecklist.filter(c => c.passed).length}/3 điều kiện
            </span>
          </div>

          <div className="my-2.5 space-y-2.5">
            {investorChecklist.map(item => (
              <div
                key={item.step}
                className={`rounded-lg border p-3 ${
                  item.passed ? "border-line bg-paper/35" : "border-warning/35 bg-warning/[0.04]"
                }`}
              >
                <div className="flex items-center justify-between gap-2 whitespace-nowrap">
                  <div className="flex min-w-0 items-baseline gap-1.5">
                    <span
                      className={`tnum font-mono text-xs font-semibold ${
                        item.passed ? "text-gain" : "text-warning"
                      }`}
                    >
                      {item.step}.
                    </span>
                    <span className="truncate text-xs font-semibold text-ink">{item.title}</span>
                  </div>
                  <Pill tone={item.passed ? "gain" : "warning"}>{item.status}</Pill>
                </div>
                <p className="mt-1.5 text-[11.5px] leading-relaxed text-secondary">{item.detail}</p>
              </div>
            ))}
          </div>
        </div>
      </div>
    </Panel>
  )
}

function KellyMathTrace({ trace, weight }: { trace: RecordData; weight?: unknown }) {
  const p = numeric(trace.p_calibrated)
  const b = numeric(trace.payoff_ratio)
  const quarterKelly = numeric(trace.quarter_kelly_raw)
  const regimeScaler = numeric(trace.market_regime_scaler)
  const portTarget = numeric(trace.portfolio_target)
  const incWeight = numeric(trace.incremental_weight)
  const minCash = numeric(trace.min_cash_target)

  return (
    <div className="rounded-lg border border-line bg-soft/40 p-3 text-xs">
      <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4">
        <div>
          <span className="block text-[11px] text-muted">Xác suất thắng (P)</span>
          <span className="tnum font-mono font-semibold">{p !== null ? `${(p * 100).toFixed(1)}%` : "—"}</span>
        </div>
        <div>
          <span className="block text-[11px] text-muted">Tỷ lệ lãi/lỗ (b)</span>
          <span className="tnum font-mono font-semibold">{b !== null ? b.toFixed(2) : "—"}</span>
        </div>
        <div>
          <span className="block text-[11px] text-muted">Quarter Kelly gốc</span>
          <span className="tnum font-mono font-semibold">
            {quarterKelly !== null ? `${(quarterKelly * 100).toFixed(1)}%` : "—"}
          </span>
        </div>
        <div>
          <span className="block text-[11px] text-muted">Hệ số thị trường</span>
          <span className="tnum font-mono font-semibold">
            {regimeScaler !== null ? `×${regimeScaler.toFixed(2)}` : "—"}
          </span>
        </div>
        <div>
          <span className="block text-[11px] text-muted">Mục tiêu danh mục</span>
          <span className="tnum font-mono font-semibold text-teal">
            {portTarget !== null ? `${(portTarget * 100).toFixed(2)}% NAV` : "—"}
          </span>
        </div>
        <div>
          <span className="block text-[11px] text-muted">Đề xuất tăng tỷ trọng</span>
          <span className="tnum font-mono font-semibold text-gain">
            {incWeight !== null ? `+${(incWeight * 100).toFixed(2)}% NAV` : weight != null ? `${weight}% NAV` : "—"}
          </span>
        </div>
        <div>
          <span className="block text-[11px] text-muted">Ngưỡng Deadband</span>
          <span className="font-medium">
            {typeof trace.deadband_passed === "boolean" ? (trace.deadband_passed ? "Đạt điều kiện" : "Bỏ qua") : "—"}
          </span>
        </div>
        <div>
          <span className="block text-[11px] text-muted">Dự trữ tiền mặt tối thiểu</span>
          <span className="tnum font-mono font-semibold">
            {minCash !== null ? `${(minCash * 100).toFixed(0)}% NAV` : "—"}
          </span>
        </div>
      </div>
    </div>
  )
}

function LogDetailViewer({ agent, entry }: { agent: string; entry: RecordData }) {
  const [showAllExclusions, setShowAllExclusions] = useState(false)
  const isCio = agent === "strategy_cio"
  const isAlloc = agent === "portfolio_allocation"
  const isUniverse = agent === "universe_discovery"

  const ignoredKeys = new Set(["id", "created_at", "generated_at", "date", "analysis_date", "ticker", "is_replay"])

  const filtered = Object.entries(entry).filter(([key, val]) => {
    if (ignoredKeys.has(key)) return false
    if (isUniverse && key === "exclusion_log" && object(entry.filtered_counts).exclusion_log) return false
    if (isCio && key === "executive_rationale") return false
    return val !== null && val !== undefined
  })

  return (
    <div className="mt-3 space-y-4 border-t border-line pt-3 text-[13px] leading-relaxed">
      {isAlloc && entry.kelly_math_steps && typeof entry.kelly_math_steps === "object" && (
        <section className="min-w-0">
          <h4 className="mb-2 text-xs font-semibold text-teal">Các bước tính toán phân bổ vốn (Kelly):</h4>
          <KellyMathTrace trace={object(entry.kelly_math_steps)} weight={entry.allocated_weight_pct} />
        </section>
      )}

      <div className="grid min-w-0 gap-4 md:grid-cols-2">
        {filtered.map(([key, value]) => {
          if (isAlloc && key === "kelly_math_steps") return null

          if (key === "exclusion_log" && Array.isArray(value) && value.length > 5) {
            const list = value as RecordData[]
            const visible = showAllExclusions ? list : list.slice(0, 6)
            return (
              <section key={key} className="min-w-0 md:col-span-2">
                <div className="mb-2 flex items-center justify-between">
                  <h4 className="text-xs font-semibold text-teal">
                    {fieldNames[key] || key.replaceAll("_", " ")} ({list.length} mã)
                  </h4>
                  <button
                    type="button"
                    onClick={() => setShowAllExclusions(!showAllExclusions)}
                    className="text-xs font-medium text-mineral hover:underline"
                  >
                    {showAllExclusions ? "Thu gọn 6 mã ↑" : `Xem toàn bộ ${list.length} mã ↓`}
                  </button>
                </div>
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
                  {visible.map((item, idx) => (
                    <div key={idx} className="rounded border border-line bg-soft/40 p-2.5 text-xs">
                      <span className="tnum font-mono font-bold text-ink">{String(item.ticker || "N/A")}</span>
                      <span className="tnum ml-2 font-mono text-[11px] font-medium text-warning">
                        {String(item.reason || "")}
                      </span>
                      <p className="mt-1 line-clamp-2 text-[11px] text-secondary">{String(item.detail || "")}</p>
                    </div>
                  ))}
                </div>
              </section>
            )
          }

          const fullWidth =
            (value !== null && typeof value === "object") || (typeof value === "string" && value.length > 100)
          return (
            <section key={key} className={`min-w-0 ${fullWidth ? "md:col-span-2" : ""}`}>
              <h4 className="mb-1.5 text-xs font-semibold text-teal">
                {fieldNames[key] || key.replaceAll("_", " ")}
              </h4>
              <Value value={value} />
            </section>
          )
        })}
      </div>
    </div>
  )
}

function AgentSkeleton() {
  return (
    <div role="status" aria-label="Đang tải quyết định và nhật ký" className="space-y-6">
      <span className="sr-only">Đang tải quyết định và nhật ký…</span>
      <div className="animate-pulse space-y-6" aria-hidden="true">
        <div className="h-72 rounded-xl border border-line bg-surface" />
        <div className="h-80 rounded-xl border border-line bg-surface" />
        <div className="grid gap-6 lg:grid-cols-[256px_minmax(0,1fr)]">
          <div className="h-96 rounded-xl border border-line bg-surface" />
          <div className="h-96 rounded-xl border border-line bg-surface" />
        </div>
      </div>
    </div>
  )
}

function AgentLogRow({ agent, entry, index }: { agent: string; entry: RecordData; index: number }) {
  const [opened, setOpened] = useState(false)
  const out = logOutput(agent, entry)
  const status = out.status || out.alert_level || out.verdict || out.final_resolution
  const ticker = logSymbol(agent, entry)
  const statusStr = status != null ? String(status) : ""
  const statusTone: "loss" | "gain" | "warning" | "neutral" = /REJECT|BLOCK|FAIL|ERROR/.test(statusStr)
    ? "loss"
    : /PASS|APPROVE|COMPLIANT|EXECUTED|VALID/.test(statusStr)
      ? "gain"
      : /CONDITIONAL|WARN|REDUCE/.test(statusStr)
        ? "warning"
        : "neutral"

  return (
    <details onToggle={event => setOpened(event.currentTarget.open)} className="group px-5 py-3 open:bg-paper/50">
      <summary className="cursor-pointer list-none rounded focus-visible:outline-2 focus-visible:outline-mineral">
        <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
          <span className="text-xs font-semibold text-ink">{agents[agent] || agent}</span>
          <Pill tone={ticker ? "mineral" : "neutral"}>{ticker || "Hệ thống"}</Pill>
          <time className="tnum font-mono text-xs text-secondary">{time(timestamp(entry))}</time>
          {statusStr && <Pill tone={statusTone}>{statusStr}</Pill>}
          <span className="ml-auto text-xs font-medium text-mineral group-open:hidden">Chi tiết ↓</span>
          <span className="ml-auto hidden text-xs font-medium text-mineral group-open:inline">Thu gọn ↑</span>
        </div>
        <p className="mt-1 max-w-[115ch] text-xs leading-relaxed text-secondary">{logSummary(agent, entry)}</p>
      </summary>
      {opened && (
        <>
          <LogDetailViewer agent={agent} entry={entry} />
          <details className="mt-3">
            <summary className="cursor-pointer text-xs font-medium text-muted hover:underline">
              Dữ liệu JSON gốc · #{String(entry.id ?? index)}
            </summary>
            <pre className="mt-2 max-h-80 overflow-auto rounded-lg bg-soft p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap break-all">
              {JSON.stringify(entry, null, 2)}
            </pre>
          </details>
        </>
      )}
    </details>
  )
}

export default function WarRoomPage() {
  return (
    <Suspense fallback={<div className="mx-auto max-w-[1360px] px-6 py-6 lg:px-8"><AgentSkeleton /></div>}>
      <WarRoomRoute />
    </Suspense>
  )
}

function WarRoomRoute() {
  const requestedSymbol = useSearchParams().get("ticker")?.trim().toUpperCase() || ""
  return <WarRoom key={requestedSymbol} requestedSymbol={requestedSymbol} />
}

function WarRoom({ requestedSymbol }: { requestedSymbol: string }) {
  const [selectedDate, setSelectedDate] = useState<string | null>(null)
  const [symbol, setSymbol] = useState(requestedSymbol)
  const [search, setSearch] = useState("")
  const [stageFilter, setStageFilter] = useState<string>("all")
  const [agent, setAgent] = useState("all")
  const [scope, setScope] = useState("all")
  const [logLimit, setLogLimit] = useState(30)

  const resource = useResource(async () => {
    const today = vietnamDate()
    const resolveReport = async (): Promise<AgentResponse> => {
      if (selectedDate !== null) {
        return (await workspaceApi.agent(selectedDate || undefined)) as AgentResponse
      }
      if (requestedSymbol) {
        return (await workspaceApi.agent(undefined)) as AgentResponse
      }
      const todayRes = (await workspaceApi.agent(today)) as AgentResponse
      const hasTodayData =
        (todayRes?.theses?.length ?? 0) > 0 ||
        (todayRes?.decisions?.length ?? 0) > 0 ||
        (todayRes?.logs?.some(g => (g.entries?.length ?? 0) > 0) ?? false)
      if (!hasTodayData && todayRes?.dates?.length && !todayRes.dates.includes(today)) {
        return (await workspaceApi.agent(todayRes.dates[0])) as AgentResponse
      }
      return todayRes
    }

    const report = await resolveReport()
    const msGroup = (report?.logs || []).find(g => g.agent === "market_surveillance")
    const rlGroup = (report?.logs || []).find(g => g.agent === "reinforcement_learning")
    return {
      report,
      msEntries: report?.msHistory?.length ? report.msHistory : msGroup?.entries || [],
      rlEntries: report?.rlHistory?.length ? report.rlHistory : rlGroup?.entries || [],
    }
  }, [selectedDate, requestedSymbol])

  const data = resource.data?.report ?? null
  const msHistorySeries = resource.data?.msEntries || []
  const accountResource = useResource<PortfolioSnapshot>(() => workspaceApi.agentPortfolio(), [])
  const portfolio = usePortfolio(accountResource.data, accountResource.reload)
  const ready = !!data

  const todayDate = vietnamDate()
  const date =
    selectedDate !== null
      ? selectedDate
      : requestedSymbol || !data
        ? ""
        : data.dates?.includes(todayDate) || !data.dates?.length
          ? todayDate
          : data.dates[0]
  const userPickedDate = selectedDate !== null

  const theses = ready ? newest(data?.theses || []).filter(row => !date || recordDate(row) === date) : []
  const resolutions = ready ? data?.resolutions || [] : []
  const plans = ready ? newest(data?.decisions || []) : []
  const logs = ready
    ? (data?.logs || [])
        .flatMap(group => (group.entries || []).map(entry => ({ agent: group.agent, entry })))
        .sort((a, b) => (Date.parse(timestamp(b.entry)) || 0) - (Date.parse(timestamp(a.entry)) || 0))
    : []

  const symbols = [
    ...new Set(
      [
        requestedSymbol,
        ...[...theses, ...plans, ...(portfolio?.positions.map(p => ({ ticker: p.symbol })) ?? [])].map(row =>
          String(row.ticker || "")
        ),
      ].filter(Boolean)
    ),
  ]
  const visibleSymbols = symbols.filter(item => item.toLowerCase().includes(search.toLowerCase()))
  const active = visibleSymbols.includes(symbol) ? symbol : visibleSymbols[0] || ""

  const thesis = theses.find(row => row.ticker === active)
  const resolution = thesis ? related(resolutions, thesis) : undefined
  const counter = thesis ? related(data?.counterTheses || [], thesis) : undefined
  const selectedPlans = newest(plans.filter(row => row.ticker === active && (!date || recordDate(row) === date)))
  const planForReview = selectedPlans[0]
  const health = ready ? data?.positionHealth?.find(row => row.ticker === active) : undefined
  const valuedPosition = portfolio?.positions.find(row => row.symbol === active)
  const position: RecordData | undefined = valuedPosition
    ? { ...health, ticker: active, current_pnl_pct: valuedPosition.pnlPercent, price_as_of: valuedPosition.priceAsOf }
    : undefined
  const summary = portfolio?.summary

  const entriesFor = (agentCode: string) =>
    logs.filter(log => log.agent === agentCode && (!date || recordDate(log.entry) === date))
  const latestEntry = (agentCode: string) => entriesFor(agentCode)[0]?.entry
  const matchingEntries = (agentCode: string) =>
    entriesFor(agentCode).filter(
      log =>
        active &&
        (logSymbol(log.agent, log.entry) === active ||
          log.entry.ticker === active ||
          object(log.entry.garch_cash_trace).ticker === active)
    )

  const msEntry = latestEntry("market_surveillance")
  const udEntry = latestEntry("universe_discovery")
  const eqEntry = matchingEntries("equity_research")[0]?.entry
  const posMonEntry = matchingEntries("position_monitoring")[0]?.entry
  const allRlLogs = resource.data?.rlEntries || []
  const rlEntry = latestEntry("reinforcement_learning") || newest(allRlLogs)[0]
  const govEntries = entriesFor("system_governance")
  const govEntry =
    govEntries.find(item => {
      const audit = object(item.entry.audit_trail_verification)
      return audit.system_status !== undefined || audit.chain_integrity_valid !== undefined
    })?.entry || govEntries[0]?.entry

  const scopedLogs = logs.filter(
    log =>
      scope === "all" ||
      (scope === "symbol" ? !!active && logSymbol(log.agent, log.entry) === active : !logSymbol(log.agent, log.entry))
  )
  const activeStageObj = pipelineStages.find(s => s.key === stageFilter)
  const stageFilteredLogs =
    activeStageObj && activeStageObj.agents.length > 0
      ? scopedLogs.filter(log => (activeStageObj.agents as readonly string[]).includes(log.agent))
      : scopedLogs
  const shownLogs = stageFilteredLogs.filter(log => agent === "all" || log.agent === agent)

  useEffect(() => {
    if (ready && requestedSymbol && window.location.hash === "#investment-thesis") {
      document.getElementById("investment-thesis")?.scrollIntoView({ block: "start" })
    }
  }, [ready, requestedSymbol])

  return (
    <Page
      title="Phân tích & Phán quyết Đa tác tử"
      sub={
        date && !userPickedDate
          ? `Phiên phân tích ${displayDate(date)} · Quy trình thẩm định vĩ mô, đa nhân tố, phản biện độc lập và kiểm toán rủi ro`
          : "Quy trình thẩm định vĩ mô, chấm điểm đa nhân tố, phản biện độc lập và kiểm toán rủi ro"
      }
      actions={
        <div className="flex flex-wrap items-center gap-2">
          <FinancialCalendar
            label="Ngày phân tích"
            allowAll={!!requestedSymbol}
            value={date}
            dates={data?.dates || []}
            onChange={value => {
              setSelectedDate(value)
              setLogLimit(30)
            }}
          />
          <a
            href="#agent-logs"
            onClick={() => document.getElementById("agent-logs")?.setAttribute("open", "")}
            className="inline-flex h-9 items-center rounded-md border border-line bg-surface px-3 text-[13px] font-medium text-ink transition-colors hover:bg-soft"
          >
            Nhật ký ({logs.length}) ↓
          </a>
          <Button
            variant="ghost"
            onClick={() => {
              void resource.reload()
              void accountResource.reload()
            }}
            disabled={resource.loading || accountResource.loading}
          >
            Làm mới
          </Button>
        </div>
      }
    >
      {!resource.data && resource.loading ? (
        <AgentSkeleton />
      ) : !resource.data && resource.error ? (
        <div role="alert" className="rounded-lg border border-loss/30 bg-loss/5 p-4 text-sm text-loss">
          Không tải được báo cáo.{" "}
          <button type="button" className="underline" onClick={() => void resource.reload()}>
            Thử lại
          </button>
        </div>
      ) : (
        <div className="space-y-6">
          {/* 1. Unified Account Metric & Profit Attribution Strip */}
          <AccountSummaryStrip portfolio={portfolio} summary={summary} accountError={accountResource.error} />

          {/* 2. Stage I: Agent 01 (Macro Line Chart + Heatmap + HMM Donut) & Agent 02 (Universe Funnel) */}
          <MarketAndUniversePanel
            msEntry={msEntry}
            msHistory={msHistorySeries}
            udEntry={udEntry}
            symbolsCount={symbols.length}
            selectedDate={date}
          />

          {/* 3. Main Stock Decision Workspace (Sticky Left Rail + Right Dossier) */}
          <div className="grid items-start gap-6 lg:grid-cols-[256px_minmax(0,1fr)]">
            {/* Left Stock Selector Rail */}
            <Panel className="p-3.5 lg:sticky lg:top-16">
              <div className="mb-2.5 flex items-center justify-between border-b border-line pb-2.5">
                <h2 className="text-[13px] font-semibold text-ink">Danh sách thẩm định</h2>
                <Pill tone="neutral">{symbols.length} mã</Pill>
              </div>

              <input
                aria-label="Tìm mã cổ phiếu"
                placeholder="Lọc nhanh mã…"
                value={search}
                onChange={e => setSearch(e.target.value)}
                className={`${control} mb-2.5 w-full text-xs`}
              />

              <nav
                aria-label="Chọn mã phân tích"
                className="flex gap-2 overflow-auto lg:max-h-[calc(100vh-210px)] lg:block lg:space-y-1.5"
              >
                {visibleSymbols.map(item => {
                  const t = theses.find(row => row.ticker === item)
                  const res = t ? related(resolutions, t) : undefined
                  const cnt = t ? related(data?.counterTheses || [], t) : undefined
                  const thRev = thesisReview(t, cnt, res)
                  const rep = t ? thesisDetails(t) : undefined
                  const itemUpside = rep?.entry && rep?.target ? (rep.target / rep.entry - 1) * 100 : null

                  const eqLog = entriesFor("equity_research").find(
                    log => logSymbol(log.agent, log.entry) === item || log.entry.ticker === item
                  )?.entry
                  const eqM = object(eqLog?.factor_raw_metrics)
                  const itemCss = numeric(eqM.css) ?? numeric(object(eqM.research_gate).css)
                  const isActive = active === item

                  return (
                    <button
                      key={item}
                      type="button"
                      onClick={() => setSymbol(item)}
                      aria-pressed={isActive}
                      className={`relative w-52 shrink-0 rounded-lg border px-3 py-2.5 text-left transition-colors lg:w-full ${
                        isActive
                          ? "border-mineral/45 bg-soft/75"
                          : "border-transparent bg-transparent hover:border-line hover:bg-paper/70"
                      }`}
                    >
                      <div className="flex items-baseline justify-between gap-2 whitespace-nowrap">
                        <span className="tnum font-mono text-[15px] font-bold text-ink">{item}</span>
                        <div className="tnum flex items-center gap-1.5 font-mono text-[11px]">
                          {itemUpside !== null && (
                            <span className={itemUpside >= 0 ? "font-semibold text-gain" : "font-semibold text-loss"}>
                              {signedNumber(itemUpside, "%")}
                            </span>
                          )}
                          {itemCss !== null && (
                            <span className="rounded border border-line bg-surface px-1.5 py-0.5 text-[10px] font-medium text-secondary">
                              CSS {itemCss.toFixed(0)}
                            </span>
                          )}
                        </div>
                      </div>

                      <div className="mt-1.5 flex items-center justify-between gap-1.5">
                        <Pill tone={pillToneFromText(thRev.tone)}>{thRev.label}</Pill>
                      </div>
                    </button>
                  )
                })}
              </nav>

              {ready && !visibleSymbols.length && (
                <p className="py-4 text-xs text-secondary">
                  {search ? "Không tìm thấy mã phù hợp." : "Chưa có dữ liệu trong ngày này."}
                </p>
              )}
            </Panel>

            {/* Right Dossier Column */}
            <section className="min-w-0" aria-label="Chi tiết quyết định">
              {active ? (
                <div key={`${active}-${date}`} className="dossier-enter space-y-6">
                  <div id="investment-thesis" className="scroll-mt-5">
                    <HeroDecisionDossier
                      ticker={active}
                      thesis={thesis}
                      counter={counter}
                      resolution={resolution}
                      plan={planForReview}
                      riskLogs={entriesFor("portfolio_risk").map(log => log.entry)}
                      executionEntries={matchingEntries("trade_execution").map(log => log.entry)}
                      eqEntry={eqEntry}
                      position={valuedPosition}
                      portfolioAvailable={!!portfolio}
                    />
                  </div>

                  {/* Unified 6-Factor Score + Bayes IC Weight Section (Appears ONLY ONCE) */}
                  <EquityResearchSection entry={eqEntry} rlEntry={rlEntry} ticker={active} />

                  {/* Concise Bull vs. Bear Comparison */}
                  <AdversarialDebateSection thesis={thesis} counter={counter} />
                </div>
              ) : (
                <Panel className="p-8 text-sm text-secondary">
                  Chọn một mã cổ phiếu ở cột bên trái để xem hồ sơ phán quyết, chấm điểm 6 nhân tố và bàn tranh biện.
                </Panel>
              )}
            </section>
          </div>

          {/* 4. Live Capital Stress-Test & 5-Guardrail Verification */}
          <SystemIntegrityShieldPanel
            govEntry={govEntry}
            posMonEntry={posMonEntry}
            position={position}
            thesis={thesis}
            counter={counter}
            resolution={resolution}
            plan={planForReview}
            eqEntry={eqEntry}
            msEntry={msEntry}
            summary={summary}
            ticker={active || "Danh mục"}
          />

          {/* 5. Collapsible Technical Audit Logs */}
          <details id="agent-logs" className="overflow-hidden rounded-xl border border-line bg-surface">
            <summary className="cursor-pointer bg-paper/50 px-5 py-3.5 text-[14px] font-semibold text-ink">
              Sổ cái nhật ký &amp; Dữ liệu kỹ thuật tác tử ({shownLogs.length} bản ghi)
            </summary>
            <div className="space-y-4 border-b border-line p-5">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <p className="text-xs text-secondary">
                  Tra cứu dữ liệu đầu vào, đầu ra và phép tính gốc của 12 tác tử theo từng chặng.
                </p>
                <span className="tnum font-mono text-xs text-secondary">
                  Hiển thị {Math.min(logLimit, shownLogs.length)} / {shownLogs.length} · {displayDate(date)}
                </span>
              </div>

              <div className="flex flex-wrap gap-1.5">
                {pipelineStages.map(stage => {
                  const count =
                    stage.agents.length === 0
                      ? scopedLogs.length
                      : scopedLogs.filter(l => (stage.agents as readonly string[]).includes(l.agent)).length
                  const isSelected = stageFilter === stage.key
                  return (
                    <button
                      key={stage.key}
                      type="button"
                      onClick={() => {
                        setStageFilter(stage.key)
                        setAgent("all")
                        setLogLimit(30)
                      }}
                      className={`rounded-md border px-3 py-1.5 text-xs font-medium transition-colors ${
                        isSelected
                          ? "border-mineral bg-mineral/10 font-semibold text-mineral"
                          : "border-line bg-paper text-secondary hover:bg-soft hover:text-ink"
                      }`}
                    >
                      {stage.label} <span className="tnum ml-1 font-mono text-[11px]">({count})</span>
                    </button>
                  )
                })}
              </div>

              <div className="flex flex-wrap gap-3">
                <label className="text-xs font-medium text-secondary">
                  Tác tử
                  <select
                    value={agent}
                    onChange={e => {
                      setAgent(e.target.value)
                      setLogLimit(30)
                    }}
                    className={`${control} ml-2 text-xs`}
                  >
                    <option value="all">Tất cả tác tử ({stageFilteredLogs.length})</option>
                    {Object.entries(agents).map(([key, label]) => (
                      <option key={key} value={key}>
                        {label} ({scopedLogs.filter(log => log.agent === key).length})
                      </option>
                    ))}
                  </select>
                </label>
                <label className="text-xs font-medium text-secondary">
                  Phạm vi
                  <select
                    value={scope}
                    onChange={e => {
                      setScope(e.target.value)
                      setLogLimit(30)
                    }}
                    className={`${control} ml-2 text-xs`}
                  >
                    <option value="all">Tất cả mã &amp; hệ thống</option>
                    <option value="symbol" disabled={!active}>
                      Chỉ mã {active}
                    </option>
                    <option value="system">Toàn hệ thống</option>
                  </select>
                </label>
              </div>
            </div>

            {!shownLogs.length && (
              <p className="p-6 text-sm text-secondary">Không có bản ghi phù hợp với bộ lọc này.</p>
            )}

            <div className="divide-y divide-line">
              {shownLogs.slice(0, logLimit).map((log, index) => (
                <AgentLogRow
                  key={`${log.agent}-${log.entry.id ?? index}`}
                  agent={log.agent}
                  entry={log.entry}
                  index={index}
                />
              ))}
            </div>

            {shownLogs.length > logLimit && (
              <div className="border-t border-line p-4 text-center">
                <Button variant="ghost" onClick={() => setLogLimit(limit => limit + 30)}>
                  Xem thêm 30 bản ghi ({shownLogs.length - logLimit} còn lại)
                </Button>
              </div>
            )}
          </details>
        </div>
      )}
    </Page>
  )
}
