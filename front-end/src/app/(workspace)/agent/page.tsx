"use client"

import { FinancialCalendar } from "@/components/FinancialCalendar"
import { vietnamDate } from "@/lib/financial-date"
import { useMemo, useState } from "react"
import { agentOutput, pipeline, defaultRuns, defaultStockCases, type CaseItem, type StockCaseDetail } from "@/lib/agent-system"
import { Button, Conviction, metricTone, Pill, ReasoningBlock, SectionEyebrow, Tabs, PercentChange, fmt } from "@/components/ui"
import { Link } from "@/lib/router"
import { workspaceApi, marketApi } from "@/lib/api"
import { useResource } from "@/lib/api/use-resource"
import type { Stock } from "@/types"

interface AgentThesis {
  ticker?: string
  status?: string
  thesis_statement?: string
  thesis?: string
  catalyst_type?: string
  catalyst_description?: string
  target_price?: number | string
  entry_price_estimated?: number | string
  invalidation_threshold?: number | string
  timeline_months?: number
  thesis_id?: string
  analysis_date?: string
  generated_at?: string
  created_at?: string
  confirming_signals?: {
    signal_1_factor?: string
    signal_2_surveillance?: string
    signal_3_macro_hmm?: string
    [key: string]: unknown
  }
  invalidation_conditions?: string[]
  pre_mortem_scenarios?: string[]
}

interface AgentCounterThesis {
  ticker?: string
  thesis_id?: string
  cts_score?: string | number
  verdict?: string
  holes?: string[]
  rationale?: string
  evaluated_at?: string
  execution_constraints?: {
    reason?: string
    tranche_allocation?: number[]
    entry_ceiling_price?: number
    stop_loss_pct_override?: number
    max_position_size_multiplier?: number
  }
}

interface AgentCioResolution {
  resolution_id?: string
  thesis_id?: string
  ticker?: string
  final_resolution?: string
  debate_summary?: string
  analysis_date?: string
  generated_at?: string
  created_at?: string
  decision_type?: string
  decision_hash?: string
  verdict_payload?: {
    weight_cap?: number
    penalty_factor?: number
    conditions?: string[]
    severity_tier?: string
    current_regime?: string
    [key: string]: unknown
  }
}

interface AgentLogEntry {
  id?: number | string
  date?: string
  inputs?: Record<string, unknown>
  outputs?: Record<string, unknown>
  computation_trace?: Record<string, unknown>
  analysis_date?: string
  generated_at?: string
  created_at?: string
}

interface AgentLog {
  agent: string
  entries?: AgentLogEntry[]
}

interface AgentResponse {
  dates?: string[]
  theses?: AgentThesis[]
  counterTheses?: AgentCounterThesis[]
  resolutions?: AgentCioResolution[]
  logs?: AgentLog[]
}

interface ExtendedStockCase extends StockCaseDetail {
  thesisId?: string
  counterDetail?: AgentCounterThesis
  resolutionDetail?: AgentCioResolution
  invalidationConditions?: string[]
  preMortemScenarios?: string[]
  confirmingSignals?: {
    signal_1_factor?: string
    signal_2_surveillance?: string
    signal_3_macro_hmm?: string
  }
}

const statusTone: Record<string, "gain" | "mineral" | "teal" | "warning" | "neutral"> = {
  New: "mineral",
  Updated: "teal",
  Confirmed: "gain",
  Watching: "neutral",
  Flagged: "warning",
}

const dotClass: Record<string, string> = {
  New: "bg-mineral",
  Updated: "bg-teal",
  Confirmed: "bg-gain",
  Watching: "bg-neutral",
  Flagged: "bg-warning",
}

const AGENT_CATALOG: { id: string; key: string; name: string; vn: string; phase: string }[] = [
  { id: "01", key: "market_surveillance", name: "Market Surveillance", vn: "Giám sát thị trường", phase: "Detection" },
  { id: "10", key: "reinforcement_learning", name: "Reinforcement Learning", vn: "Tự học & hiệu chuẩn", phase: "Detection" },
  { id: "02", key: "universe_discovery", name: "Universe Discovery", vn: "Lọc Universe", phase: "Detection" },
  { id: "03", key: "equity_research", name: "Equity Research", vn: "Chấm điểm cổ phiếu", phase: "Analysis" },
  { id: "04", key: "investment_thesis", name: "Investment Thesis", vn: "Luận điểm mua", phase: "Analysis" },
  { id: "05", key: "counter_thesis", name: "Devil's Advocate", vn: "Phản biện luận điểm", phase: "Analysis" },
  { id: "12", key: "strategy_cio", name: "Strategy CIO", vn: "Trọng tài tối cao", phase: "Decision" },
  { id: "07", key: "portfolio_allocation", name: "Portfolio Allocation", vn: "Chia tiền Quarter-Kelly", phase: "Decision" },
  { id: "06", key: "portfolio_risk", name: "Portfolio Risk", vn: "Cổng rủi ro Sovereign", phase: "Decision" },
  { id: "08", key: "trade_execution", name: "Trade Execution", vn: "Thực thi lệnh", phase: "Execution" },
  { id: "09", key: "position_monitoring", name: "Position Monitoring", vn: "Giám sát vị thế", phase: "Execution" },
  { id: "11", key: "system_governance", name: "System Governance", vn: "Kiểm toán & quản trị", phase: "Governance" },
]

function agentStatus(order: number, runIndex: number) {
  const reached = 12 - runIndex * 2.5
  if (order < reached - 1) return "Completed"
  if (order < reached) return "Working"
  return "Waiting"
}

const stateDot: Record<string, string> = {
  Completed: "bg-teal",
  Working: "bg-mineral animate-pulse",
  Waiting: "bg-line-strong",
}

const stateText: Record<string, string> = {
  Completed: "text-teal",
  Working: "text-mineral",
  Waiting: "text-muted",
}

function extractDateStr(value: unknown): string {
  return value ? vietnamDate(value) : ""
}

function formatDisplayDate(dateStr: string, todayStr: string): string {
  if (!dateStr || dateStr === "all") return "Tất cả các ngày"
  const parts = dateStr.split("-")
  if (parts.length !== 3) return dateStr
  const [y, m, d] = parts
  const formatted = `${d}/${m}/${y}`
  if (dateStr === todayStr) return `${formatted} (Hôm nay)`
  const today = new Date(todayStr)
  const target = new Date(dateStr)
  const diffTime = today.getTime() - target.getTime()
  const diffDays = Math.round(diffTime / (1000 * 3600 * 24))
  if (diffDays === 1) return `${formatted} (Hôm qua)`
  return formatted
}

function AgentCard({ id, runId, symbol, badge }: { id: string; runId: string; symbol: string; badge: string }) {
  const [open, setOpen] = useState(false)
  const out = agentOutput(id, { runId, symbol })
  const actionable = id === "07" || id === "08"
  return (
    <div className="rounded-[10px] border border-line bg-paper p-3.5">
      <div className="flex items-center gap-2 mb-2.5">
        <span className="font-mono text-[10px] text-muted">{id}</span>
        <span className="text-[12px] font-medium text-ink">{badge}</span>
      </div>
      {actionable && <div className="mb-2 text-[9.5px] font-medium uppercase tracking-wide text-warning">Đề xuất hệ thống — không phải khuyến nghị</div>}
      <div className="flex flex-wrap gap-1.5 mb-1">
        {out.headline.map((m) => (
          <span key={m.label} className="inline-flex items-baseline gap-1.5 rounded-[6px] border border-line bg-surface px-2 py-1 text-[11px]">
            <span className="text-muted">{m.label}</span>
            <span className={`tnum font-mono font-medium ${metricTone[m.tone ?? "neutral"]}`}>{m.value}</span>
          </span>
        ))}
      </div>
      <button onClick={() => setOpen((v) => !v)} className="mt-1.5 text-[10.5px] font-medium text-mineral hover:underline">{open ? "Ẩn chi tiết ↑" : "Chi tiết ↓"}</button>
      {open && <ul className="mt-2 space-y-1 border-t border-line pt-2 text-[11px] leading-snug text-secondary">{out.detail.map((d) => <li key={d} className="flex gap-1.5"><span className="text-muted">·</span>{d}</li>)}</ul>}
    </div>
  )
}

const signalCards = [
  { id: "01", badge: "Chế độ thị trường" },
  { id: "05", badge: "Luận điểm phản biện" },
  { id: "06", badge: "Cổng kiểm soát rủi ro" },
  { id: "12", badge: "Chỉ thị CIO" },
  { id: "11", badge: "Kiểm toán quản trị" },
]

export default function WarRoom() {
  const [selectedDate, setSelectedDate] = useState<string>("all")
  const resource = useResource(() => workspaceApi.agent(selectedDate === "all" ? undefined : selectedDate), [selectedDate])
  const snapshotRes = useResource(() => marketApi.snapshot().catch(() => ({ items: [] })), [])

  const rawData = resource.data as AgentResponse | null
  const todayStr = useMemo(() => vietnamDate(), [])

  // Danh sách các ngày có dữ liệu từ Database
  const availableDates = useMemo(() => {
    const dates = new Set<string>(rawData?.dates ?? [])
    rawData?.theses?.forEach((t) => {
      const d = extractDateStr(t.analysis_date || t.generated_at || t.created_at)
      if (d) dates.add(d)
    })
    rawData?.resolutions?.forEach((r) => {
      const d = extractDateStr(r.created_at)
      if (d) dates.add(d)
    })
    rawData?.logs?.forEach((l) => {
      l.entries?.forEach((e) => {
        const d = extractDateStr(e.analysis_date || e.created_at || e.date)
        if (d) dates.add(d)
      })
    })
    return Array.from(dates).sort().reverse()
  }, [rawData])



  // Lọc theses theo ngày
  const filteredTheses = useMemo(() => {
    if (!rawData?.theses || rawData.theses.length === 0) return []
    if (selectedDate === "all") return rawData.theses
    const filtered = rawData.theses.filter((t) => extractDateStr(t.analysis_date || t.generated_at || t.created_at) === selectedDate)
    return filtered
  }, [rawData, selectedDate])

  // 1. Chỉ 1 phiên duy nhất 09:45 theo đúng quy trình Tự hành (Autonomous Pipeline)
  const liveRuns = useMemo(() => {
    if (!filteredTheses || filteredTheses.length === 0) {
      return [{ ...defaultRuns[0], label: "Chưa có phiên phân tích", cases: [], agentsRun: 0, time: "—", triggered: "" }]
    }
    const liveCases: CaseItem[] = filteredTheses.map((t) => ({
      symbol: String(t.ticker),
      status: t.status === "APPROVED" ? "Confirmed" : t.status === "WATCH" ? "Watching" : "Updated",
      note: String(t.thesis_statement || t.catalyst_description || "Luận điểm đầu tư tự hành"),
    }))
    const firstRun = {
      id: "run-0945",
      label: `Phiên 09:45 (${formatDisplayDate(selectedDate, todayStr)})`,
      time: "09:45",
      triggered: "Autonomous Pipeline · Khớp lệnh liên tục HOSE",
      cases: liveCases,
      agentsRun: rawData?.logs?.filter(log => (log.entries?.length ?? 0) > 0).length ?? 0,
    }
    return [firstRun]
  }, [filteredTheses, selectedDate, todayStr, rawData?.logs])

  // 2. Mapping data động từ Database (confirming_signals, pre_mortem, invalidation, holes, rationale)
  const liveStockCases = useMemo(() => {
    if (!rawData || !Array.isArray(rawData.theses) || rawData.theses.length === 0) {
      return defaultStockCases as Record<string, ExtendedStockCase>
    }
    const mapped: Record<string, ExtendedStockCase> = { ...(defaultStockCases as Record<string, ExtendedStockCase>) }
    rawData.theses.forEach((t) => {
      const sym = String(t.ticker)
      const base = defaultStockCases[sym] ?? defaultStockCases.HPG
      const counterMatch = rawData.counterTheses?.find((ct) => ct.ticker === sym || ct.thesis_id === t.thesis_id)
      const resolutionMatch = rawData.resolutions?.find((r) => r.ticker === sym || r.thesis_id === t.thesis_id)

      const sig = (typeof t.confirming_signals === "object" && t.confirming_signals !== null) ? t.confirming_signals : {}
      const preMortem = Array.isArray(t.pre_mortem_scenarios) ? t.pre_mortem_scenarios : []
      const invalidationConds = Array.isArray(t.invalidation_conditions) ? t.invalidation_conditions : []
      const holes = Array.isArray(counterMatch?.holes) && counterMatch.holes.length > 0 ? counterMatch.holes : []

      const targetLow = Number(t.entry_price_estimated) || (Number(t.target_price) ? Math.round(Number(t.target_price) * 0.85) : base.targetLow)
      const targetHigh = Number(t.target_price) || base.targetHigh
      const invalidation = Number(t.invalidation_threshold) || Math.round(targetLow * 0.95)

      mapped[sym] = {
        ...base,
        symbol: sym,
        thesisId: t.thesis_id,
        bias: t.status === "APPROVED"
          ? "Tích lũy · Vùng mua"
          : t.status === "CONDITIONAL_APPROVED"
          ? "Giải ngân thận trọng (Kèm điều kiện)"
          : "Theo dõi chặt chẽ",
        conviction: t.status === "APPROVED" ? "Strong" : t.status === "CONDITIONAL_APPROVED" ? "Moderate" : "Weak",
        thesis: t.thesis_statement || t.thesis || (t.catalyst_description ? `Luận điểm dựa trên catalyst: ${t.catalyst_description}` : base.thesis),
        catalysts: t.catalyst_description ? [t.catalyst_description] : (base.catalysts || []),
        targetHigh,
        targetLow,
        invalidation,
        counter: holes.length > 0 ? holes : (counterMatch?.rationale ? [counterMatch.rationale] : base.counter),
        reasoning: {
          observation: sig.signal_2_surveillance
            ? `Giám sát thị trường: ${sig.signal_2_surveillance}. Lực cầu chủ động duy trì tại vùng hỗ trợ định lượng.`
            : `Dòng tiền khớp lệnh tự động ghi nhận tín hiệu hấp thụ cung tại vùng tích lũy.`,
          change: sig.signal_3_macro_hmm
            ? `Trạng thái vĩ mô HMM: ${sig.signal_3_macro_hmm}. Phân bổ rủi ro hệ thống ở trạng thái ổn định.`
            : `Xu hướng dòng tiền tổ chức và khối ngoại chuyển biến tích cực trong các phiên gần nhất.`,
          evidence: sig.signal_1_factor
            ? `Mô hình Alpha đa nhân tố: ${sig.signal_1_factor}. Điểm định giá và chất lượng tài sản vượt ngưỡng kiểm định.`
            : (t.catalyst_description || `Biên lợi nhuận gộp và các chỉ số tài chính quý gần nhất ghi nhận tăng trưởng vượt trung bình ngành.`),
          interpretation: t.catalyst_description
            ? t.catalyst_description
            : (t.thesis_statement || `Tổ chức đang gia tăng tỷ trọng khi triển vọng tăng trưởng chu kỳ được xác lập.`),
          risk: preMortem[0]
            ? preMortem[0]
            : `Rủi ro biến động giá nguyên vật liệu đầu vào và tiến độ thực thi dự án.`,
          invalidation: invalidationConds[0]
            ? invalidationConds[0]
            : `Đóng cửa thủng ngưỡng hỗ trợ kỹ thuật hoặc xuất hiện đột biến phân phối của dòng tiền lớn.`,
        },
        counterDetail: counterMatch,
        resolutionDetail: resolutionMatch,
        invalidationConditions: invalidationConds,
        preMortemScenarios: preMortem,
        confirmingSignals: sig,
      }
    })
    return mapped
  }, [rawData])

  const [runId, setRunId] = useState(defaultRuns[0].id)
  const run = liveRuns.find((r) => r.id === runId) ?? liveRuns[0]
  const runIndex = liveRuns.findIndex((r) => r.id === run.id)
  const [symbol, setSymbol] = useState(run.cases[0]?.symbol ?? "HPG")
  const [tab, setTab] = useState("Tín hiệu")
  const [leftView, setLeftView] = useState<"runs" | "pipeline">("runs")
  const [expanded, setExpanded] = useState<number | null>(null)
  const [showDetail, setShowDetail] = useState(false)
  const [selectedAgentKey, setSelectedAgentKey] = useState<string>("strategy_cio")
  const [expandedLogId, setExpandedLogId] = useState<string | number | null>(null)

  // keep selected symbol valid for the run
  const activeSymbol = run.cases.some((c) => c.symbol === symbol) ? symbol : (run.cases[0]?.symbol ?? "HPG")
  const c: ExtendedStockCase = (liveStockCases[activeSymbol] ?? defaultStockCases[activeSymbol] ?? defaultStockCases.HPG) as ExtendedStockCase

  const stockList = (snapshotRes.data as { items?: Stock[] })?.items || []
  const stock = stockList.find((s) => s.symbol === activeSymbol) || {
    symbol: activeSymbol,
    name: activeSymbol === "HPG" ? "Hòa Phát Group" : activeSymbol === "MBB" ? "MBBank" : activeSymbol === "FPT" ? "FPT Corporation" : activeSymbol,
    price: c.targetLow || 28000,
    changePct: 0.8,
  }

  const runCase = run.cases.find((x) => x.symbol === activeSymbol) ?? run.cases[0]
  const hasCases = !resource.loading && !resource.error && filteredTheses.length > 0
  const totalCases = useMemo(() => new Set(liveRuns.flatMap((r) => r.cases.map((x) => x.symbol))).size, [liveRuns])

  // Lấy số bản ghi cho agent theo ngày đã chọn
  const getAgentLogCount = (agentKey: string) => {
    if (!rawData?.logs) return 0
    const found = rawData.logs.find((l) => l.agent === agentKey)
    if (!found?.entries) return 0
    if (selectedDate === "all") return found.entries.length
    return found.entries.filter((e) => extractDateStr(e.analysis_date || e.created_at || e.date) === selectedDate).length
  }

  // Danh sách log của agent đang chọn lọc theo ngày
  const selectedAgentLogs = useMemo(() => {
    if (!rawData?.logs) return []
    const found = rawData.logs.find((l) => l.agent === selectedAgentKey)
    const entries = found?.entries || []
    if (selectedDate === "all") return entries
    return entries.filter((e) => extractDateStr(e.analysis_date || e.created_at || e.date) === selectedDate)
  }, [rawData, selectedAgentKey, selectedDate])

  return (
    <div className="min-h-full bg-paper">
      {/* Header */}
      <div className="min-h-[52px] border-b border-line bg-surface flex flex-wrap items-center gap-x-3 gap-y-1 px-6 py-2.5 shrink-0">
        <h1 className="text-[16px] font-semibold tracking-tight text-ink">Phòng chỉ huy AI</h1>
        <span className="text-[12px] text-muted hidden md:inline">Môi trường điều hành & phân tích định lượng</span>
        <div className="ml-auto flex items-center gap-2 text-[12px]">
          <span className="flex items-center gap-1.5 text-muted"><span className="w-1.5 h-1.5 rounded-full bg-gain animate-pulse" />{hasCases ? liveRuns.length : 0} phiên tự hành · {totalCases} mã được thẩm định</span>
          <Pill tone="teal"><span className="w-1.5 h-1.5 rounded-full bg-teal animate-pulse mr-1" />Hệ thống tự hành 09:45</Pill>
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-[288px_minmax(0,1fr)_360px] items-stretch">
        {/* ── Left column ── */}
        <aside className="border-b xl:border-b-0 xl:border-r border-line bg-surface self-stretch">
          {/* Date Selector Header */}
          <div className="p-3 pb-2 border-b border-line bg-soft/30">
            <div className="flex items-center justify-between gap-2 p-2 rounded-[7px] border border-line bg-paper text-[12px]">
              <span className="text-muted text-[11px] font-semibold shrink-0">Ngày chạy:</span>
              <FinancialCalendar label="Ngày chạy Agent" value={selectedDate === "all" ? "" : selectedDate} dates={availableDates} onChange={date => setSelectedDate(date || "all")} />
            </div>
          </div>

          <div className="flex border-b border-line shrink-0">
            {(["runs", "pipeline"] as const).map((v) => (
              <button
                key={v}
                onClick={() => setLeftView(v)}
                className={`flex-1 h-10 text-[12.5px] font-medium capitalize transition-colors ${leftView === v ? "text-ink" : "text-muted hover:text-secondary"} relative`}
              >
                {v === "runs" ? "Phiên chạy tự hành" : "Quy trình hoạt động"}
                {leftView === v && <span className="absolute left-4 right-4 -bottom-px h-[2px] bg-ink rounded-full" />}
              </button>
            ))}
          </div>

          {leftView === "runs" ? (
            <div className="p-3">
              <p className="text-[11.5px] text-muted leading-snug px-1 mb-3">
                Phiên tự hành khởi động cố định lúc 09:45 sáng (khớp lệnh liên tục HOSE), quét toàn bộ universe và kích hoạt 12 tác tử.
              </p>
              <div className="space-y-1.5">
                {liveRuns.map((r) => {
                  const active = r.id === run.id
                  return (
                    <button
                      key={r.id}
                      onClick={() => { setRunId(r.id); setSymbol(r.cases[0]?.symbol ?? "HPG") }}
                      className={`w-full text-left rounded-[8px] p-3 border transition-colors ${active ? "border-line-strong bg-paper shadow-xs" : "border-transparent hover:bg-soft/60"}`}
                    >
                      <div className="flex items-center gap-2">
                        <span className={`w-1 h-9 rounded-full shrink-0 ${active ? "bg-mineral" : "bg-line-strong"}`} />
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2">
                            <span className="text-[13px] font-semibold text-ink">{r.label}</span>
                            <span className="ml-auto tnum font-mono text-[11px] text-teal font-medium">{r.time}</span>
                          </div>
                          <div className="text-[11px] text-muted mt-0.5">{r.triggered} · {r.cases.length} mã · {r.agentsRun} tác tử</div>
                        </div>
                      </div>
                    </button>
                  )
                })}
              </div>
            </div>
          ) : (
            <div className="p-4">
              <p className="text-[11.5px] text-muted leading-snug mb-4">
                12 tác tử vận hành theo cơ chế tiếp sức. Mỗi tác tử chuyển giao kết quả cho tác tử kế tiếp — không tác tử nào tự ý đưa ra khuyến nghị từ dữ liệu thô.
              </p>
              <div className="relative">
                <div className="absolute left-[11px] top-1 bottom-1 w-px bg-line" />
                <div className="space-y-3.5">
                  {pipeline.map((p) => {
                    const st = agentStatus(p.order, runIndex)
                    const open = expanded === p.order
                    const actionable = p.id === "07" || p.id === "08"
                    const out = agentOutput(p.id, { runId: run.id, symbol: activeSymbol })
                    return (
                      <div key={p.order} className="relative pl-8">
                        <span className={`absolute left-[6px] top-1 w-[11px] h-[11px] rounded-full border-2 border-surface ${stateDot[st]}`} />
                        <button onClick={() => { setExpanded(open ? null : p.order); setShowDetail(false) }} className="w-full text-left">
                          <div className="flex items-center gap-2">
                            <span className="font-mono text-[10px] text-muted">{p.id}</span>
                            <span className="text-[12.5px] font-medium text-ink">{p.name}</span>
                            <span className={`ml-auto text-[10px] ${stateText[st]}`}>{st}</span>
                          </div>
                          <p className="text-[11.5px] text-secondary leading-snug mt-0.5">{p.does}</p>
                          <p className="text-[10.5px] text-muted italic mt-0.5">→ {p.handoff}</p>
                        </button>
                        {open && (
                          <div className="mt-2 rounded-[8px] border border-line bg-paper p-3">
                            {actionable && <div className="mb-2 text-[9.5px] font-medium uppercase tracking-wide text-warning">Đề xuất hệ thống — không phải khuyến nghị</div>}
                            <div className="space-y-1.5">{out.headline.map((m) => <div key={m.label} className="flex items-baseline justify-between gap-3 text-[11.5px]"><span className="text-secondary">{m.label}</span><span className={`tnum font-mono font-medium ${metricTone[m.tone ?? "neutral"]}`}>{m.value}</span></div>)}</div>
                            <button onClick={() => setShowDetail((v) => !v)} className="mt-2.5 text-[10.5px] font-medium text-mineral hover:underline">{showDetail ? "Ẩn chi tiết ↑" : "Chi tiết ↓"}</button>
                            {showDetail && <ul className="mt-2 space-y-1 border-t border-line pt-2 text-[11px] leading-snug text-secondary">{out.detail.map((d) => <li key={d} className="flex gap-1.5"><span className="text-muted">·</span>{d}</li>)}</ul>}
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              </div>
            </div>
          )}
        </aside>

        {/* ── Center column ── */}
        <section className="min-w-0 bg-paper">
          {!hasCases ? <div className="p-6 text-[13px] text-muted">
            {resource.loading ? "Đang tải phiên phân tích..." : resource.error ? <>Không tải được dữ liệu Agent. <button className="underline" onClick={() => void resource.reload()}>Thử lại</button></> : "Không có luận điểm trong ngày đã chọn. Chọn ngày có dữ liệu hoặc xem tất cả ngày. Nhật ký tác tử vẫn có thể có bản ghi riêng."}
          </div> : <>
          {/* Run context + case selector */}
          <div className="border-b border-line px-6 py-3 shrink-0 bg-surface/60">
            <div className="flex items-center gap-2 mb-2.5">
              <span className="text-[11px] font-semibold tracking-[0.12em] uppercase text-muted">{run.label}</span>
              <span className="text-[11px] text-muted">· {run.time} · {run.triggered}</span>
            </div>
            <div className="flex flex-wrap gap-1.5 max-h-28 overflow-y-auto pr-1">
              {run.cases.map((rc) => {
                const active = rc.symbol === activeSymbol
                return (
                  <button
                    key={rc.symbol}
                    onClick={() => setSymbol(rc.symbol)}
                    className={`flex items-center gap-2 h-8 pl-2.5 pr-2 rounded-[7px] border text-[12.5px] transition-colors ${active ? "border-ink bg-ink text-paper" : "border-line bg-surface text-secondary hover:border-ink/30"}`}
                  >
                    <span className="font-mono font-medium">{rc.symbol}</span>
                    <span className={`text-[10px] px-1.5 h-[18px] inline-flex items-center gap-1 rounded-full ${active ? "bg-paper/20 text-paper" : "bg-soft text-muted"}`}>
                      {!active && <span className={`w-1 h-1 rounded-full ${dotClass[rc.status]}`} />}
                      {rc.status}
                    </span>
                  </button>
                )
              })}
            </div>
          </div>

          {/* Investment case */}
          <div className="max-w-2xl w-full mx-auto px-8 py-7">
            <div className="flex items-center gap-3 mb-1">
              <Link to={`/stock/${c.symbol}`} className="font-mono text-[18px] font-semibold text-ink hover:underline">{c.symbol}</Link>
              <span className="text-[14px] text-secondary">{stock.name}</span>
              <span className="tnum font-mono text-[14px] text-ink ml-auto">{fmt(stock.price)}</span>
              <PercentChange value={stock.changePct} className="text-[13px]" />
            </div>
            <div className="flex items-center gap-2 mt-1">
              <Pill tone={statusTone[runCase.status]}>{runCase.status === "Confirmed" ? "Đã duyệt" : runCase.status === "Watching" ? "Quan sát" : "Cập nhật"} trong phiên này</Pill>
              <span className="text-[12px] text-muted truncate max-w-md">{runCase.note}</span>
            </div>

            <div className="mt-6">
              <SectionEyebrow>Luận điểm đầu tư</SectionEyebrow>
              <p className="font-serif text-[16px] leading-relaxed text-ink">{c.thesis}</p>
              <div className="mt-4 grid grid-cols-2 gap-4 text-[13px]">
                <div>
                  <div className="text-[11px] uppercase tracking-wide text-muted mb-1.5 font-semibold">Chất xúc tác (Catalysts)</div>
                  <ul className="space-y-1 text-secondary">{c.catalysts.map((x) => <li key={x}>· {x}</li>)}</ul>
                </div>
                <div>
                  <div className="text-[11px] uppercase tracking-wide text-muted mb-1 font-semibold">Vùng giá mục tiêu</div>
                  <div className="tnum font-mono text-ink text-[15px]">{fmt(c.targetLow)} – {fmt(c.targetHigh)}</div>
                  <div className="text-[11px] uppercase tracking-wide text-muted mt-3 mb-1 font-semibold">Điều kiện vi phạm</div>
                  <div className="tnum font-mono text-loss text-[14px]">Đóng nến &lt; {fmt(c.invalidation)}</div>
                </div>
              </div>
            </div>

            <div className="mt-7 pt-6 border-t border-line">
              <div className="flex items-center gap-2 mb-3">
                <SectionEyebrow>Luận điểm phản biện</SectionEyebrow>
                <Pill tone="warning">Tác tử phản biện độc lập</Pill>
              </div>
              <ul className="space-y-2 text-[13px] text-secondary">{c.counter.map((x) => <li key={x}>· {x}</li>)}</ul>
            </div>

            <div className="mt-7 pt-6 border-t border-line">
              <div className="flex items-center gap-2 mb-3">
                <SectionEyebrow>Tín hiệu tác tử</SectionEyebrow>
                <span className="text-[11px] text-muted">· {run.label} · {activeSymbol}</span>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {signalCards.map((sc) => (
                  <AgentCard key={sc.id} id={sc.id} runId={run.id} symbol={activeSymbol} badge={sc.badge} />
                ))}
              </div>
            </div>

            <div className="mt-7 pt-6 border-t border-line">
              <SectionEyebrow>Đánh giá hiện tại</SectionEyebrow>
              <div className="flex items-center gap-3 mb-4">
                <span className="text-[20px] font-semibold text-ink">{c.bias}</span>
                <Conviction level={c.conviction} />
              </div>
              <div className="border border-line rounded-[10px] p-4 bg-paper shadow-2xs">
                <ReasoningBlock data={c.reasoning} />
              </div>
              <div className="flex flex-wrap gap-2 mt-4">
                <Button variant="primary">Lưu vào sổ ghi chú</Button>
                <Link to="/trade"><Button variant="secondary">Kế hoạch giải ngân</Button></Link>
                <Button variant="ghost">Cài đặt cảnh báo</Button>
              </div>
            </div>
          </div>
          </>}
        </section>

        {/* ── Right column: evidence inspector & logs ── */}
        <aside className="border-t xl:border-t-0 xl:border-l border-line bg-surface self-stretch">
          <div className="px-4 pt-4">
            <SectionEyebrow>Kiểm định bằng chứng · {c.symbol}</SectionEyebrow>
            {!hasCases && tab !== "Nhật ký tác tử" && <p className="mb-3 text-[12px] text-muted">Chưa có dữ liệu luận điểm cho ngày này.</p>}
            <Tabs
              tabs={["Tín hiệu", "Phán quyết phản biện", "Nghị quyết CIO", "Nhật ký tác tử"]}
              active={tab}
              onChange={setTab}
            />
          </div>
          <div className="p-4">
            {hasCases && tab === "Tín hiệu" && (
              <div className="space-y-3">
                <div className="text-[12px] text-muted mb-2 font-medium">Bằng chứng định lượng từ cơ sở dữ liệu:</div>
                <div className="border border-line rounded-[8px] p-3 bg-paper">
                  <div className="flex items-center gap-2 mb-1.5">
                    <Pill tone="gain">Hệ số xác thực</Pill>
                    <span className="ml-auto text-[11px] text-muted font-mono">Dữ liệu thực</span>
                  </div>
                  <div className="text-[12.5px] font-medium text-ink">Luận điểm: {c.thesis}</div>
                  <div className="mt-2 text-[12px] text-secondary space-y-1">
                    {c.catalysts.map((cat: string, i: number) => (
                      <div key={i} className="flex gap-1.5"><span className="text-gain">✓</span>{cat}</div>
                    ))}
                  </div>
                </div>

                {c.confirmingSignals && (
                  <div className="border border-line rounded-[8px] p-3 bg-paper space-y-2">
                    <div className="text-[11px] uppercase tracking-wide text-muted font-semibold">Tín hiệu xác nhận (Rule of Three)</div>
                    <div className="space-y-1.5 text-[12px]">
                      {c.confirmingSignals.signal_1_factor && (
                        <div className="p-2 rounded bg-soft/60">
                          <span className="font-medium text-ink">Mô hình Alpha: </span>
                          <span className="text-secondary">{c.confirmingSignals.signal_1_factor}</span>
                        </div>
                      )}
                      {c.confirmingSignals.signal_2_surveillance && (
                        <div className="p-2 rounded bg-soft/60">
                          <span className="font-medium text-ink">Dòng tiền & Tape: </span>
                          <span className="text-secondary">{c.confirmingSignals.signal_2_surveillance}</span>
                        </div>
                      )}
                      {c.confirmingSignals.signal_3_macro_hmm && (
                        <div className="p-2 rounded bg-soft/60">
                          <span className="font-medium text-ink">Chế độ vĩ mô: </span>
                          <span className="text-secondary">{c.confirmingSignals.signal_3_macro_hmm}</span>
                        </div>
                      )}
                    </div>
                  </div>
                )}

                <div className="border border-line rounded-[8px] p-3 bg-paper">
                  <div className="text-[11px] uppercase tracking-wide text-muted font-semibold mb-1">Mục tiêu & Cắt lỗ</div>
                  <div className="text-[12.5px] font-mono space-y-0.5">
                    <div>Vùng gom: <span className="text-ink font-semibold">{fmt(c.targetLow)}</span></div>
                    <div>Mục tiêu: <span className="text-gain font-semibold">{fmt(c.targetHigh)}</span></div>
                    <div>Vi phạm: <span className="text-loss font-semibold">Đóng nến &lt; {fmt(c.invalidation)}</span></div>
                  </div>
                </div>

                {c.invalidationConditions && c.invalidationConditions.length > 0 && (
                  <div className="border border-loss/20 bg-loss/5 rounded-[8px] p-3">
                    <div className="text-[11px] uppercase tracking-wide text-loss font-semibold mb-1.5">Điều kiện vi phạm (Invalidation)</div>
                    <ul className="text-[12px] text-secondary space-y-1">
                      {c.invalidationConditions.map((cond, i) => (
                        <li key={i} className="flex gap-1.5"><span className="text-loss">✕</span>{cond}</li>
                      ))}
                    </ul>
                  </div>
                )}

                {c.preMortemScenarios && c.preMortemScenarios.length > 0 && (
                  <div className="border border-warning/20 bg-warning/5 rounded-[8px] p-3">
                    <div className="text-[11px] uppercase tracking-wide text-warning font-semibold mb-1.5">Kịch bản rủi ro xấu (Pre-Mortem)</div>
                    <ul className="text-[12px] text-secondary space-y-1">
                      {c.preMortemScenarios.map((pm, i) => (
                        <li key={i} className="flex gap-1.5"><span className="text-warning">!</span>{pm}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            )}

            {hasCases && tab === "Phán quyết phản biện" && (
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <div className="text-[12px] text-muted font-medium">Phán quyết từ Devil&apos;s Advocate:</div>
                  {c.counterDetail?.verdict && (
                    <Pill tone={c.counterDetail.verdict === "APPROVED" ? "gain" : c.counterDetail.verdict === "CONDITIONAL" ? "warning" : "neutral"}>
                      {c.counterDetail.verdict} · CTS {c.counterDetail.cts_score || "31.3"}/100
                    </Pill>
                  )}
                </div>

                {c.counterDetail?.rationale && (
                  <div className="border border-line rounded-[8px] p-3 bg-paper">
                    <div className="text-[11px] uppercase tracking-wide text-muted font-semibold mb-1">Nhận định Devil&apos;s Advocate</div>
                    <p className="text-[12.5px] text-secondary leading-relaxed">{c.counterDetail.rationale}</p>
                  </div>
                )}

                <div className="space-y-2">
                  <div className="text-[11px] uppercase tracking-wide text-muted font-semibold">Các lỗ hổng phát hiện ({c.counter.length})</div>
                  {c.counter.map((cnt: string, i: number) => (
                    <div key={i} className="border border-line rounded-[8px] p-3 bg-paper">
                      <div className="flex items-center gap-2 mb-1">
                        <Pill tone="warning">Lỗ hổng #{i + 1}</Pill>
                      </div>
                      <p className="text-[12.5px] text-secondary leading-snug">{cnt}</p>
                    </div>
                  ))}
                </div>

                {c.counterDetail?.execution_constraints && (
                  <div className="border border-warning/30 bg-warning/5 rounded-[8px] p-3 text-[12px]">
                    <div className="font-semibold text-warning mb-1">Ràng buộc thực thi vị thế:</div>
                    <div className="text-secondary leading-snug">{c.counterDetail.execution_constraints.reason}</div>
                    {c.counterDetail.execution_constraints.entry_ceiling_price && (
                      <div className="mt-2 pt-2 border-t border-warning/20 grid grid-cols-2 gap-2 font-mono text-[11px] text-ink">
                        <div>Trần giá vào: <span className="font-semibold">{fmt(c.counterDetail.execution_constraints.entry_ceiling_price)}</span></div>
                        <div>Siết cắt lỗ: <span className="font-semibold text-loss">{(c.counterDetail.execution_constraints.stop_loss_pct_override || 0.05) * 100}%</span></div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}

            {hasCases && tab === "Nghị quyết CIO" && (
              <div className="space-y-3 text-[12.5px]">
                <div className="flex items-center justify-between mb-1">
                  <div className="text-[12px] text-muted font-medium">Nghị quyết điều hành danh mục:</div>
                  <Pill tone={c.resolutionDetail?.final_resolution?.includes("PROCEED") ? "teal" : "gain"}>
                    {c.resolutionDetail?.final_resolution || "PROCEED_WITH_PENALTY"}
                  </Pill>
                </div>

                {c.resolutionDetail?.debate_summary && (
                  <div className="border border-line rounded-[8px] p-3 bg-paper">
                    <div className="text-[11px] uppercase tracking-wide text-muted font-semibold mb-1">Phán quyết phân xử CIO</div>
                    <p className="text-[12.5px] text-ink leading-relaxed">{c.resolutionDetail.debate_summary}</p>
                  </div>
                )}

                <div className="border border-line rounded-[8px] p-3 bg-paper space-y-2">
                  <div className="flex justify-between border-b border-line pb-1.5">
                    <span className="text-muted">Cấp độ rủi ro (Tier)</span>
                    <span className="font-semibold text-ink">{c.resolutionDetail?.verdict_payload?.severity_tier || "TIER_3_NORMAL_BUSINESS_RISK"}</span>
                  </div>
                  <div className="flex justify-between border-b border-line pb-1.5">
                    <span className="text-muted">Chỉ thị giải ngân</span>
                    <span className="font-semibold text-gain">{c.bias}</span>
                  </div>
                  <div className="flex justify-between border-b border-line pb-1.5">
                    <span className="text-muted">Hệ số phạt Kelly (Lambda)</span>
                    <span className="font-mono text-ink">{c.resolutionDetail?.verdict_payload?.penalty_factor ?? 0.5}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted">Trần tỷ trọng mã</span>
                    <span className="font-mono text-teal">
                      {c.resolutionDetail?.verdict_payload?.weight_cap ? `${(c.resolutionDetail.verdict_payload.weight_cap * 100).toFixed(1)}% NAV` : "8.0% NAV"}
                    </span>
                  </div>
                </div>

                {c.resolutionDetail?.verdict_payload?.conditions && c.resolutionDetail.verdict_payload.conditions.length > 0 && (
                  <div className="border border-line rounded-[8px] p-3 bg-paper">
                    <div className="text-[11px] uppercase tracking-wide text-muted font-semibold mb-1.5">Điều kiện thực thi</div>
                    <div className="flex flex-wrap gap-1">
                      {c.resolutionDetail.verdict_payload.conditions.map((cond, i) => (
                        <span key={i} className="text-[11px] font-mono px-2 py-0.5 rounded bg-soft text-secondary border border-line">
                          {cond}
                        </span>
                      ))}
                    </div>
                  </div>
                )}

                {c.resolutionDetail?.decision_hash && (
                  <div className="p-2.5 rounded-[6px] bg-soft/50 border border-line text-[10.5px]">
                    <div className="text-muted mb-0.5">Khóa băm kiểm toán mật mã (Audit Hash):</div>
                    <div className="font-mono text-muted break-all select-all">{c.resolutionDetail.decision_hash}</div>
                  </div>
                )}
              </div>
            )}

            {tab === "Nhật ký tác tử" && (
              <div className="space-y-3">
                {/* Header with Date Filter */}
                <div className="flex items-center justify-between pb-2 border-b border-line">
                  <div>
                    <div className="text-[12px] font-semibold text-ink">Nhật ký 12 tác tử</div>
                    <div className="text-[10.5px] text-muted">{formatDisplayDate(selectedDate, todayStr)}</div>
                  </div>
                  <FinancialCalendar label="Ngày chạy Agent" value={selectedDate === "all" ? "" : selectedDate} dates={availableDates} onChange={date => setSelectedDate(date || "all")} />
                </div>

                {/* Agent Grid Selector */}
                <div className="grid grid-cols-2 gap-1.5 max-h-48 overflow-y-auto pr-1">
                  {AGENT_CATALOG.map((ag) => {
                    const active = ag.key === selectedAgentKey
                    const count = getAgentLogCount(ag.key)
                    return (
                      <button
                        key={ag.key}
                        onClick={() => setSelectedAgentKey(ag.key)}
                        className={`text-left p-2 rounded-[6px] border text-[11px] transition-colors ${active ? "border-ink bg-ink text-paper" : "border-line bg-paper text-secondary hover:border-ink/30"}`}
                      >
                        <div className="flex items-center gap-1.5">
                          <span className="font-mono text-[9.5px] opacity-70">{ag.id}</span>
                          <span className="font-medium truncate">{ag.vn}</span>
                        </div>
                        <div className="text-[9.5px] opacity-70 mt-0.5 font-mono">{count} bản ghi</div>
                      </button>
                    )
                  })}
                </div>

                {/* Selected Agent Log Detail */}
                <div className="mt-3 pt-3 border-t border-line space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-[12px] font-semibold text-ink">
                      {AGENT_CATALOG.find((a) => a.key === selectedAgentKey)?.vn || selectedAgentKey}
                    </span>
                    <span className="text-[10.5px] text-muted font-mono">{selectedAgentLogs.length} bản ghi</span>
                  </div>

                  {selectedAgentLogs.length === 0 ? (
                    <div className="text-muted text-[12px] py-4 text-center border border-dashed border-line rounded-[6px]">
                      Không có bản ghi nhật ký nào trong DB vào ngày {formatDisplayDate(selectedDate, todayStr)}.
                    </div>
                  ) : (
                    <div className="space-y-2 max-h-[360px] overflow-y-auto pr-1">
                      {selectedAgentLogs.map((entry, idx) => {
                        const isExpanded = expandedLogId === (entry.id ?? idx)
                        const outputs = entry.outputs || {}
                        return (
                          <div key={idx} className="border border-line rounded-[8px] p-2.5 bg-paper text-[11.5px] space-y-1.5">
                            <div className="flex items-center justify-between">
                              <span className="font-mono text-muted text-[10.5px]">
                                {entry.created_at ? new Date(entry.created_at).toLocaleString("vi-VN", { timeZone: "Asia/Ho_Chi_Minh" }) : `Log #${entry.id ?? idx}`}
                              </span>
                              {outputs.alert_level ? (
                                <Pill tone={outputs.alert_level === "WARNING" ? "warning" : outputs.alert_level === "CRITICAL" ? "loss" : "teal"}>
                                  {String(outputs.alert_level)}
                                </Pill>
                              ) : (
                                <Pill tone="teal">COMPLETED</Pill>
                              )}
                            </div>

                            {/* Key output highlights */}
                            <div className="text-[11px] text-secondary space-y-1">
                              {outputs.current_regime && (
                                <div className="flex justify-between">
                                  <span className="text-muted">Chế độ thị trường:</span>
                                  <span className="font-semibold text-ink">{String(outputs.current_regime)}</span>
                                </div>
                              )}
                              {outputs.vix_vn_analog !== undefined && (
                                <div className="flex justify-between">
                                  <span className="text-muted">VIX-VN Analog:</span>
                                  <span className="font-mono text-ink">{Number(outputs.vix_vn_analog).toFixed(2)}</span>
                                </div>
                              )}
                              {outputs.csad_score !== undefined && (
                                <div className="flex justify-between">
                                  <span className="text-muted">Điểm CSAD:</span>
                                  <span className="font-mono text-ink">{Number(outputs.csad_score).toFixed(4)}</span>
                                </div>
                              )}
                              {outputs.session_context && (
                                <div className="flex justify-between">
                                  <span className="text-muted">Bối cảnh phiên:</span>
                                  <span className="font-medium text-ink">{String(outputs.session_context)}</span>
                                </div>
                              )}
                              {outputs.atc_anomalies_count !== undefined && (
                                <div className="flex justify-between">
                                  <span className="text-muted">Dị thường ATC:</span>
                                  <span className="font-mono text-warning">{String(outputs.atc_anomalies_count)}</span>
                                </div>
                              )}
                            </div>

                            <button
                              onClick={() => setExpandedLogId(isExpanded ? null : (entry.id ?? idx))}
                              className="text-[10.5px] font-medium text-mineral hover:underline mt-1 inline-block"
                            >
                              {isExpanded ? "Ẩn chi tiết tính toán ↑" : "Xem chi tiết Inputs / Outputs / Trace ↓"}
                            </button>

                            {isExpanded && (
                              <div className="mt-2 pt-2 border-t border-line text-[10px] font-mono bg-soft/60 p-2 rounded max-h-48 overflow-auto space-y-2">
                                <div>
                                  <div className="text-muted font-semibold mb-0.5">INPUTS:</div>
                                  <pre className="whitespace-pre-wrap text-secondary">{JSON.stringify(entry.inputs, null, 2)}</pre>
                                </div>
                                <div>
                                  <div className="text-muted font-semibold mb-0.5">OUTPUTS:</div>
                                  <pre className="whitespace-pre-wrap text-secondary">{JSON.stringify(entry.outputs, null, 2)}</pre>
                                </div>
                                {entry.computation_trace && (
                                  <div>
                                    <div className="text-muted font-semibold mb-0.5">COMPUTATION TRACE:</div>
                                    <pre className="whitespace-pre-wrap text-secondary">{JSON.stringify(entry.computation_trace, null, 2)}</pre>
                                  </div>
                                )}
                              </div>
                            )}
                          </div>
                        )
                      })}
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        </aside>
      </div>
    </div>
  )
}
