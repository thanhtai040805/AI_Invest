"use client"

import {
  Brain,
  EyeOff,
  PanelLeft,
  PanelLeftClose,
  PanelRight,
  PanelRightClose,
  ShieldCheck,
  Sparkles,
} from "lucide-react"
import { Pill } from "@/components/ui"
import type { EvalSummaryResponse, MemoryMode, ResearchChatMode } from "@/lib/api/researchChat"

interface TopControlBarProps {
  chatMode: ResearchChatMode
  memoryMode: MemoryMode
  onMemoryModeChange: (mode: MemoryMode) => void
  memoryModeDisabled?: boolean
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

const MEMORY_MODE_OPTIONS: ReadonlyArray<{
  value: MemoryMode
  label: string
  title: string
}> = [
  {
    value: "auto",
    label: "Tự động",
    title: "Đọc ký ức dài hạn + tự động ghi nhớ sở thích & luận điểm sau lượt trả lời",
  },
  {
    value: "read_only",
    label: "Chỉ đọc",
    title: "Đọc ký ức dài hạn & danh mục nhưng không ghi ký ức mới từ phiên này",
  },
  {
    value: "incognito",
    label: "Ẩn danh",
    title: "Phiên ẩn danh: không đọc ký ức, không gửi danh mục Core DB, không lưu ký ức",
  },
]

export function TopControlBar({
  chatMode,
  memoryMode,
  onMemoryModeChange,
  memoryModeDisabled,
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
          {memoryMode === "incognito" && (
            <Pill tone="warning">
              <EyeOff className="h-3 w-3 shrink-0" aria-hidden="true" />
              <span>Ẩn danh</span>
            </Pill>
          )}
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

      <div className="flex flex-wrap items-center gap-2 shrink-0">
        {/* Compact Memory Mode Selector */}
        <div
          role="group"
          aria-label="Chế độ ký ức dài hạn"
          className="inline-flex items-center rounded-[7px] border border-line bg-paper p-0.5"
        >
          <span
            className="hidden sm:inline-flex items-center pl-1.5 pr-1 text-muted"
            title="Chế độ ký ức"
          >
            {memoryMode === "incognito" ? (
              <EyeOff className="h-3.5 w-3.5 text-warning" aria-hidden="true" />
            ) : (
              <Brain className="h-3.5 w-3.5 text-ceiling" aria-hidden="true" />
            )}
          </span>
          {MEMORY_MODE_OPTIONS.map((opt) => {
            const active = memoryMode === opt.value
            return (
              <button
                key={opt.value}
                type="button"
                disabled={memoryModeDisabled}
                title={opt.title}
                aria-pressed={active}
                onClick={() => onMemoryModeChange(opt.value)}
                className={`rounded-[5px] px-2 h-6.5 text-[11px] font-medium transition-colors cursor-pointer disabled:opacity-50 ${
                  active
                    ? opt.value === "incognito"
                      ? "bg-warning text-paper"
                      : "bg-ink text-paper"
                    : "text-secondary hover:text-ink"
                }`}
              >
                {opt.label}
              </button>
            )
          })}
        </div>

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
            <span className="rounded-full bg-ink text-paper px-1.5 py-[1px] font-mono text-[10px] tnum">
              {evidenceCount}
            </span>
          )}
        </button>
      </div>
    </header>
  )
}
