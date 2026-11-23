"use client"

import {
  PanelLeft,
  PanelLeftClose,
  PanelRight,
  PanelRightClose,
  ShieldCheck,
  Sparkles,
} from "lucide-react"
import { Pill } from "@/components/ui"
import type { EvalSummaryResponse, ResearchChatMode } from "@/lib/api/researchChat"

interface TopControlBarProps {
  chatMode: ResearchChatMode
  primaryTicker: string
  compareTickers: string[]
  asOfDate: string
  sidebarOpen: boolean
  onToggleSidebar: () => void
  drawerOpen: boolean
  onToggleDrawer: () => void
  evidenceCount: number
  evalSummary: EvalSummaryResponse | null
  onOpenGateTab: () => void
}

export function TopControlBar({
  chatMode,
  primaryTicker,
  compareTickers,
  asOfDate,
  sidebarOpen,
  onToggleSidebar,
  drawerOpen,
  onToggleDrawer,
  evidenceCount,
  evalSummary,
  onOpenGateTab,
}: TopControlBarProps) {
  const g5 = evalSummary?.gates?.g5HumanPassRate
  const g1 = evalSummary?.gates?.g1CitationVerifiedRate
  const gatePassCount = evalSummary
    ? [
        (g1 ?? 0) >= 0.98,
        (evalSummary.gates.g2TableCalcVerifiedRate ?? 0) >= 0.98,
        (evalSummary.gates.g3AbstentionComplianceRate ?? 0) >= 1,
        evalSummary.gates.g4PitZeroLeakCount === 0 && evalSummary.totalAssistantMessages > 0,
        (g5 ?? 0) >= 0.9,
      ].filter(Boolean).length
    : 0

  const taggedTickers = [
    ...(primaryTicker ? [primaryTicker] : []),
    ...compareTickers.filter((t) => t !== primaryTicker),
  ]

  return (
    <header className="flex flex-wrap items-center justify-between gap-2 border-b border-line bg-surface px-3.5 py-2.5 shrink-0">
      <div className="flex items-center gap-2 min-w-0">
        <button
          type="button"
          onClick={onToggleSidebar}
          aria-label={sidebarOpen ? "Thu gọn danh sách hội thoại" : "Mở danh sách hội thoại"}
          title={sidebarOpen ? "Thu gọn sidebar" : "Mở lịch sử trò chuyện"}
          className="inline-flex h-8 w-8 items-center justify-center rounded-[7px] border border-line bg-paper text-secondary hover:text-ink hover:bg-soft transition-colors shrink-0"
        >
          {sidebarOpen ? (
            <PanelLeftClose className="h-4 w-4" aria-hidden="true" />
          ) : (
            <PanelLeft className="h-4 w-4" aria-hidden="true" />
          )}
        </button>

        <div className="flex flex-wrap items-center gap-1.5 min-w-0">
          <Sparkles className="h-4 w-4 text-mineral shrink-0" aria-hidden="true" />
          <span className="text-[13.5px] font-semibold text-ink truncate">
            {chatMode === "MOAT" ? "MOAT Broker Audit" : "Research Chat"}
          </span>
          <Pill tone={chatMode === "MOAT" ? "mineral" : "teal"}>
            {chatMode === "MOAT" ? "MOAT" : "General"}
          </Pill>
          {taggedTickers.length > 0 ? (
            <span className="font-mono text-[11px] text-secondary truncate">
              {taggedTickers.map((t) => `@${t}`).join(" · ")}
            </span>
          ) : (
            <span className="text-[11.5px] text-muted hidden sm:inline">
              Toàn kho báo cáo · Tự động nhận diện mã
            </span>
          )}
          <span className="font-mono text-[11px] text-muted hidden md:inline">
            · PIT {asOfDate}
          </span>
        </div>
      </div>

      <div className="flex items-center gap-2 shrink-0">
        <button
          type="button"
          onClick={onOpenGateTab}
          title="Mở bảng chi tiết Exit Gate G1–G5 & chấm điểm kiểm nghiệm"
          className="inline-flex items-center gap-1.5 rounded-full border border-line-strong bg-soft/80 px-2.5 h-8 text-[11.5px] font-medium text-ink hover:bg-raised transition-colors"
        >
          <ShieldCheck className="h-3.5 w-3.5 text-teal shrink-0" aria-hidden="true" />
          <span className="font-mono tnum">
            Gate: {evalSummary ? `${gatePassCount}/5` : "—"}
          </span>
          {g5 != null && (
            <span className="text-muted font-mono tnum hidden sm:inline">
              ({Math.round(g5 * 100)}%)
            </span>
          )}
        </button>

        <button
          type="button"
          onClick={onToggleDrawer}
          aria-expanded={drawerOpen}
          aria-label={drawerOpen ? "Đóng ngăn bằng chứng" : "Mở ngăn bằng chứng"}
          className={`inline-flex items-center gap-1.5 rounded-[7px] border px-2.5 h-8 text-[12px] font-medium transition-colors ${
            drawerOpen
              ? "border-mineral/50 bg-mineral/12 text-mineral"
              : "border-line bg-paper text-secondary hover:text-ink hover:bg-soft"
          }`}
        >
          {drawerOpen ? (
            <PanelRightClose className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          ) : (
            <PanelRight className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          )}
          <span>Bằng chứng</span>
          {evidenceCount > 0 && (
            <span className="rounded-full bg-ink text-paper px-1.5 py-0.2 font-mono text-[10px] tnum">
              {evidenceCount}
            </span>
          )}
        </button>
      </div>
    </header>
  )
}
