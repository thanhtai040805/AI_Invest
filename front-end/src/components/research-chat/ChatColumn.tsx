"use client"

import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react"
import {
  AlertTriangle,
  ArrowUp,
  CalendarClock,
  EyeOff,
  FileStack,
  Layers3,
  Plus,
  Square,
  X,
} from "lucide-react"
import { Pill } from "@/components/ui"
import {
  normalizeDeclaredBlindspots,
  resolveActivityLogFromMetadata,
  type DiscrepancyAlert,
  type MemoryMode,
  type ResearchActivityItem,
  type ResearchChatMessage,
  type ResearchChatMode,
} from "@/lib/api/researchChat"
import type { AuditBadgeToken } from "./markdownParser"
import { MarkdownCitationView } from "./MarkdownCitationView"
import { ResearchActivityTimeline } from "./ResearchActivityTimeline"
import { PILOT_TICKERS, type StageProgressItem } from "./types"

interface ChatColumnProps {
  chatMode: ResearchChatMode
  memoryMode: MemoryMode
  onMemoryModeChange: (mode: MemoryMode) => void
  primaryTicker: string
  onPrimaryTickerChange: (ticker: string) => void
  compareTickers: string[]
  onCompareTickersChange: (tickers: string[]) => void
  availableTickers?: readonly string[]
  enableKg: boolean
  onEnableKgChange: (enabled: boolean) => void
  asOfDate: string
  todayDate: string
  onAsOfDateChange: (date: string) => void
  messages: ResearchChatMessage[]
  onSelectAssistantMessage: (messageId: string) => void
  streaming: boolean
  streamingText: string
  liveActivity?: ResearchActivityItem[]
  streamStartedAt?: number | null
  stages: StageProgressItem[]
  streamError: string | null
  onSendPrompt: (query: string) => void
  onStopStream: () => void
  onBadgeClick: (badge: AuditBadgeToken, messageId?: string) => void
  onSelectLinkedSession?: (sessionId: string) => void
}

function cleanTicker(raw: string): string {
  return raw.trim().toUpperCase().replace(/[^A-Z0-9]/g, "")
}

function VerificationWarningBanner({
  blindspots,
  onInspect,
}: {
  blindspots: ReturnType<typeof normalizeDeclaredBlindspots>
  onInspect: () => void
}) {
  const critical = blindspots.filter(
    (b) =>
      b.code === "UNSUPPORTED_NUMERIC_CLAIM" ||
      b.code === "CITATION_CONTENT_MISMATCH" ||
      b.code === "HALLUCINATED_CITATION_ID" ||
      b.code === "HALLUCINATED_MEMORY_ID",
  )
  if (critical.length === 0) return null

  return (
    <div
      role="alert"
      className="mb-3 rounded-[10px] border border-loss/35 bg-loss/6 px-3.5 py-2.5 flex flex-wrap items-center justify-between gap-2 text-[12.5px]"
    >
      <div className="flex items-start gap-2 min-w-0 flex-1">
        <AlertTriangle className="h-4 w-4 text-loss shrink-0 mt-0.5" aria-hidden="true" />
        <div className="min-w-0">
          <div className="font-medium text-loss">
            Lưu ý kiểm chứng số liệu ({critical.length} mục)
          </div>
          <div className="text-secondary text-[11.5px] leading-snug mt-0.5">
            {critical
              .slice(0, 2)
              .map((c) => c.reason)
              .join(" · ")}
          </div>
        </div>
      </div>
      <button
        type="button"
        onClick={onInspect}
        className="shrink-0 rounded-[7px] border border-loss/30 bg-surface px-2.5 py-1 text-[11.5px] font-medium text-loss hover:bg-loss/10 transition-colors cursor-pointer"
      >
        Đối chiếu
      </button>
    </div>
  )
}

function DiscrepancyAlertsBox({ alerts }: { alerts: DiscrepancyAlert[] }) {
  if (!alerts || alerts.length === 0) return null
  return (
    <div className="mt-4 rounded-[10px] border border-warning/30 bg-warning/6 p-3.5 space-y-2">
      <div className="flex items-center gap-1.5 text-[11.5px] font-semibold uppercase tracking-[0.05em] text-warning">
        <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        <span>Chênh lệch BCTN vs Số kiểm toán ({alerts.length})</span>
      </div>
      <div className="space-y-2">
        {alerts.map((alert, idx) => {
          const sev = String(alert.severity ?? "MEDIUM").toUpperCase()
          const isHigh = sev === "HIGH"
          return (
            <div
              key={alert.alert_id ?? `disc-${idx}`}
              className="rounded-[8px] border border-line bg-surface px-3 py-2 text-[12.5px] space-y-1"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <Pill tone={isHigh ? "loss" : "warning"}>{sev}</Pill>
                  <span className="font-semibold text-ink">
                    {alert.metric || alert.type || "Chỉ tiêu đối chất"}
                  </span>
                </div>
                {alert.delta_pct != null && Number.isFinite(alert.delta_pct) && (
                  <span className="font-mono text-[11.5px] font-semibold text-loss tnum">
                    Δ {alert.delta_pct > 0 ? "+" : ""}
                    {alert.delta_pct.toFixed(2)}%
                  </span>
                )}
              </div>
              {alert.summary && <p className="text-secondary leading-relaxed">{alert.summary}</p>}
            </div>
          )
        })}
      </div>
    </div>
  )
}

export function ChatColumn({
  chatMode,
  memoryMode,
  onMemoryModeChange,
  primaryTicker,
  onPrimaryTickerChange,
  compareTickers,
  onCompareTickersChange,
  availableTickers,
  enableKg,
  onEnableKgChange,
  asOfDate,
  todayDate,
  onAsOfDateChange,
  messages,
  onSelectAssistantMessage,
  streaming,
  streamingText,
  liveActivity = [],
  streamStartedAt = null,
  stages,
  streamError,
  onSendPrompt,
  onStopStream,
  onBadgeClick,
  onSelectLinkedSession,
}: ChatColumnProps) {
  const [draft, setDraft] = useState("")
  const [plusMenuOpen, setPlusMenuOpen] = useState(false)
  const [customTickerInput, setCustomTickerInput] = useState("")
  const scrollEndRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const plusMenuRef = useRef<HTMLDivElement>(null)

  const isEmptyState = messages.length === 0 && !streaming

  useEffect(() => {
    if (!isEmptyState) {
      scrollEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" })
    }
  }, [isEmptyState, messages.length, streamingText, liveActivity.length])

  useEffect(() => {
    if (!plusMenuOpen) return
    const handleOutside = (e: MouseEvent) => {
      if (plusMenuRef.current && !plusMenuRef.current.contains(e.target as Node)) {
        setPlusMenuOpen(false)
      }
    }
    document.addEventListener("mousedown", handleOutside)
    return () => document.removeEventListener("mousedown", handleOutside)
  }, [plusMenuOpen])

  useEffect(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = "0px"
    el.style.height = `${Math.min(Math.max(el.scrollHeight, 36), 180)}px`
  }, [draft])

  const taggedTickers = [
    ...(primaryTicker ? [primaryTicker] : []),
    ...compareTickers.filter((t) => t !== primaryTicker),
  ]

  const attachTicker = (rawSym: string) => {
    const sym = cleanTicker(rawSym)
    if (!sym || sym.length < 2 || sym.length > 8) return
    if (taggedTickers.includes(sym) || taggedTickers.length >= 6) return
    if (!primaryTicker) {
      onPrimaryTickerChange(sym)
    } else {
      onCompareTickersChange([...compareTickers, sym])
    }
  }

  const removeTicker = (sym: string) => {
    if (sym === primaryTicker) {
      if (compareTickers.length > 0) {
        onPrimaryTickerChange(compareTickers[0])
        onCompareTickersChange(compareTickers.slice(1))
      } else {
        onPrimaryTickerChange("")
      }
      return
    }
    onCompareTickersChange(compareTickers.filter((t) => t !== sym))
  }

  const insertAtTag = (sym: string) => {
    const clean = cleanTicker(sym)
    if (!clean) return
    attachTicker(clean)
    setDraft((prev) => {
      const token = `@${clean}`
      if (prev.toUpperCase().includes(token)) return prev
      const sep = prev && !prev.endsWith(" ") ? " " : ""
      return `${prev}${sep}${token} `
    })
    textareaRef.current?.focus()
  }

  const handleAddCustomTicker = (e: FormEvent) => {
    e.preventDefault()
    const parts = customTickerInput
      .split(/[\s,;]+/)
      .map(cleanTicker)
      .filter((s) => s.length >= 2 && s.length <= 8)
    let nextPrimary = primaryTicker
    const nextCompare = [...compareTickers]
    const seen = new Set(taggedTickers)
    for (const sym of parts) {
      if (seen.has(sym) || seen.size >= 6) continue
      seen.add(sym)
      if (!nextPrimary) {
        nextPrimary = sym
      } else {
        nextCompare.push(sym)
      }
    }
    if (nextPrimary !== primaryTicker) onPrimaryTickerChange(nextPrimary)
    if (nextCompare.length !== compareTickers.length) onCompareTickersChange(nextCompare)
    setCustomTickerInput("")
  }

  const submitDraft = () => {
    const clean = draft.trim()
    if (!clean || streaming) return
    setDraft("")
    setPlusMenuOpen(false)
    onSendPrompt(clean)
  }

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault()
    submitDraft()
  }

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      submitDraft()
    }
  }

  const runningStageItem = stages.find((s) => s.status === "running") ?? null
  const activeStageMessage =
    runningStageItem && typeof runningStageItem.detail?.message === "string"
      ? runningStageItem.detail.message
      : runningStageItem?.label ?? null

  const hasCustomDate = Boolean(asOfDate && asOfDate !== todayDate)
  const hasActiveChips =
    taggedTickers.length > 0 || hasCustomDate || memoryMode === "incognito" || !enableKg

  const quickTickers = (
    availableTickers && availableTickers.length > 0 ? availableTickers : PILOT_TICKERS
  ).slice(0, 12)

  const renderComposer = () => (
    <form
      onSubmit={handleSubmit}
      className="relative rounded-[28px] border border-line/90 bg-surface shadow-[0_3px_20px_rgba(24,32,29,0.06)] px-3 py-2 focus-within:border-line-strong focus-within:shadow-[0_6px_28px_rgba(24,32,29,0.09)] transition-all"
    >
      {/* Subtle context chips only when user opted into an override */}
      {hasActiveChips && (
        <div className="flex flex-wrap items-center gap-1.5 px-2 pt-1 pb-1.5">
          {taggedTickers.map((sym) => (
            <span
              key={sym}
              className="inline-flex items-center gap-1 h-6 pl-2.5 pr-1.5 rounded-full bg-soft font-mono text-[11.5px] font-medium text-ink"
            >
              @{sym}
              <button
                type="button"
                disabled={streaming}
                onClick={() => removeTicker(sym)}
                aria-label={`Bỏ gắn mã ${sym}`}
                className="rounded-full p-0.5 text-muted hover:text-ink cursor-pointer"
              >
                <X className="h-3 w-3" aria-hidden="true" />
              </button>
            </span>
          ))}
          {hasCustomDate && (
            <span className="inline-flex items-center gap-1 h-6 pl-2.5 pr-1.5 rounded-full bg-soft font-mono text-[11.5px] text-secondary">
              PIT {asOfDate}
              <button
                type="button"
                disabled={streaming}
                onClick={() => onAsOfDateChange(todayDate)}
                aria-label="Đặt lại ngày hôm nay"
                className="rounded-full p-0.5 text-muted hover:text-ink cursor-pointer"
              >
                <X className="h-3 w-3" aria-hidden="true" />
              </button>
            </span>
          )}
          {memoryMode === "incognito" && (
            <span className="inline-flex items-center gap-1 h-6 pl-2.5 pr-1.5 rounded-full bg-soft text-[11.5px] text-secondary">
              Ẩn danh
              <button
                type="button"
                disabled={streaming}
                onClick={() => onMemoryModeChange("auto")}
                aria-label="Tắt ẩn danh"
                className="rounded-full p-0.5 text-muted hover:text-ink cursor-pointer"
              >
                <X className="h-3 w-3" aria-hidden="true" />
              </button>
            </span>
          )}
          {!enableKg && (
            <span className="inline-flex items-center gap-1 h-6 pl-2.5 pr-1.5 rounded-full bg-soft text-[11.5px] text-secondary">
              KG tắt
              <button
                type="button"
                disabled={streaming}
                onClick={() => onEnableKgChange(true)}
                aria-label="Bật lại đồ thị tri thức"
                className="rounded-full p-0.5 text-muted hover:text-ink cursor-pointer"
              >
                <X className="h-3 w-3" aria-hidden="true" />
              </button>
            </span>
          )}
        </div>
      )}

      <div className="flex items-end gap-2">
        {/* Single clean '+' button tucking away optional broker settings */}
        <div ref={plusMenuRef} className="relative shrink-0">
          <button
            type="button"
            disabled={streaming}
            onClick={() => setPlusMenuOpen((v) => !v)}
            aria-expanded={plusMenuOpen}
            aria-label="Tùy chọn nâng cao"
            title="Gắn mã cổ phiếu, khóa ngày dữ liệu PIT, hoặc ẩn danh"
            className={`h-9 w-9 rounded-full flex items-center justify-center transition-colors cursor-pointer disabled:opacity-40 ${
              plusMenuOpen
                ? "bg-soft text-ink"
                : "text-secondary hover:bg-soft hover:text-ink"
            }`}
          >
            <Plus className="h-5 w-5 stroke-[1.8]" aria-hidden="true" />
          </button>

          {plusMenuOpen && (
            <div className="popover-enter absolute bottom-full left-0 mb-2.5 w-[296px] rounded-[18px] border border-line bg-surface p-3.5 shadow-xl shadow-ink/10 z-30 space-y-3.5 text-[12.5px]">
              {/* 1. Quick @Ticker attach */}
              <div className="space-y-1.5">
                <div className="text-[11.5px] font-medium text-muted">Gắn mã cổ phiếu (tùy chọn)</div>
                <div className="flex items-center gap-1.5">
                  <input
                    type="text"
                    value={customTickerInput}
                    onChange={(e) => setCustomTickerInput(e.target.value.toUpperCase())}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault()
                        handleAddCustomTicker(e)
                      }
                    }}
                    placeholder="VD: FPT, ACB, VHM…"
                    maxLength={24}
                    aria-label="Nhập mã cổ phiếu"
                    className="h-8 flex-1 rounded-[9px] border border-line bg-paper px-2.5 font-mono text-[12px] text-ink placeholder:text-muted focus:border-line-strong focus:outline-none"
                  />
                  <button
                    type="button"
                    disabled={!customTickerInput.trim()}
                    onClick={(e) => handleAddCustomTicker(e)}
                    className="h-8 px-2.5 rounded-[9px] bg-ink text-paper text-[12px] font-medium disabled:opacity-40 cursor-pointer"
                  >
                    Thêm
                  </button>
                </div>
                <div className="flex flex-wrap gap-1 pt-0.5">
                  {quickTickers.map((sym) => {
                    const active = taggedTickers.includes(sym)
                    return (
                      <button
                        key={sym}
                        type="button"
                        onClick={() => (active ? removeTicker(sym) : insertAtTag(sym))}
                        className={`h-6 px-2 rounded-full font-mono text-[11px] transition-colors cursor-pointer ${
                          active
                            ? "bg-ink text-paper font-medium"
                            : "bg-soft text-secondary hover:text-ink"
                        }`}
                      >
                        {sym}
                      </button>
                    )
                  })}
                </div>
              </div>

              {/* 2. Point-in-Time Date */}
              <div className="flex items-center justify-between gap-2 pt-2.5 border-t border-line/70">
                <span className="inline-flex items-center gap-1.5 text-secondary">
                  <CalendarClock className="h-3.5 w-3.5 text-muted" aria-hidden="true" />
                  <span>Ngày dữ liệu (PIT)</span>
                </span>
                <input
                  type="date"
                  value={asOfDate}
                  max={todayDate}
                  onChange={(e) => onAsOfDateChange(e.target.value || todayDate)}
                  aria-label="Ngày Point-in-Time as_of_date"
                  className="h-7.5 rounded-[8px] border border-line bg-paper px-2 font-mono text-[11.5px] text-ink focus:outline-none"
                />
              </div>

              {/* 3. KG + Incognito toggles */}
              <div className="space-y-2 pt-2.5 border-t border-line/70">
                <button
                  type="button"
                  onClick={() => onEnableKgChange(!enableKg)}
                  className="w-full flex items-center justify-between rounded-[8px] py-1 text-left text-secondary hover:text-ink cursor-pointer"
                >
                  <span className="inline-flex items-center gap-1.5">
                    <Layers3 className="h-3.5 w-3.5 text-muted" aria-hidden="true" />
                    <span>Đồ thị vĩ mô & ngành</span>
                  </span>
                  <span className="font-mono text-[11.5px] text-ink">
                    {enableKg ? "Bật" : "Tắt"}
                  </span>
                </button>

                <button
                  type="button"
                  onClick={() =>
                    onMemoryModeChange(memoryMode === "incognito" ? "auto" : "incognito")
                  }
                  className="w-full flex items-center justify-between rounded-[8px] py-1 text-left text-secondary hover:text-ink cursor-pointer"
                >
                  <span className="inline-flex items-center gap-1.5">
                    <EyeOff className="h-3.5 w-3.5 text-muted" aria-hidden="true" />
                    <span>Trò chuyện ẩn danh</span>
                  </span>
                  <span className="font-mono text-[11.5px] text-ink">
                    {memoryMode === "incognito" ? "Bật" : "Tắt"}
                  </span>
                </button>
              </div>
            </div>
          )}
        </div>

        {/* Clean Single-Line Auto-Expanding Input */}
        <textarea
          ref={textareaRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={handleKeyDown}
          rows={1}
          disabled={streaming}
          placeholder={
            chatMode === "MOAT"
              ? "Nhập mã hoặc câu hỏi kiểm định chuyên sâu…"
              : "Hỏi bất kỳ cổ phiếu, BCTC hay ngành nào…"
          }
          aria-label="Nhập câu hỏi phân tích doanh nghiệp"
          className="flex-1 min-h-[36px] max-h-[180px] resize-none bg-transparent py-1.5 px-1 text-[15px] leading-[1.5] text-ink placeholder:text-muted focus:outline-none disabled:opacity-60"
        />

        {/* Circular Send / Stop Button */}
        {streaming ? (
          <button
            type="button"
            onClick={onStopStream}
            aria-label="Dừng"
            title="Dừng suy luận"
            className="h-9 w-9 rounded-full bg-ink text-paper flex items-center justify-center hover:opacity-90 transition-opacity shrink-0 cursor-pointer"
          >
            <Square className="h-3.5 w-3.5 fill-current" aria-hidden="true" />
          </button>
        ) : (
          <button
            type="submit"
            disabled={!draft.trim()}
            aria-label="Gửi"
            title="Gửi câu hỏi"
            className="h-9 w-9 rounded-full bg-ink text-paper flex items-center justify-center hover:opacity-90 disabled:bg-[#ecefee] disabled:text-disabled transition-all shrink-0 cursor-pointer disabled:cursor-default"
          >
            <ArrowUp className="h-[18px] w-[18px] stroke-[2.25]" aria-hidden="true" />
          </button>
        )}
      </div>
    </form>
  )

  return (
    <div className="flex-1 min-w-0 flex flex-col h-full bg-surface overflow-hidden">
      {isEmptyState ? (
        /* Pure ChatGPT-Style Centered Empty State */
        <div className="flex-1 flex flex-col items-center justify-center px-4 sm:px-6 pb-16">
          <h1 className="text-[24px] sm:text-[28px] font-normal tracking-[-0.015em] text-ink text-center mb-7 select-none">
            {chatMode === "MOAT"
              ? "Hôm nay bạn muốn kiểm định sâu doanh nghiệp nào?"
              : "Hôm nay bạn dự định phân tích gì?"}
          </h1>

          <div className="w-full max-w-[700px]">
            {renderComposer()}
            {streamError && (
              <div
                role="alert"
                className="mt-3 rounded-[10px] border border-loss/30 bg-loss/6 px-3.5 py-2.5 text-[12.5px] text-loss"
              >
                {streamError}
              </div>
            )}
          </div>
        </div>
      ) : (
        /* Clean Borderless Conversation Stream + Bottom Pill Composer */
        <>
          <div className="flex-1 overflow-y-auto px-4 sm:px-6">
            <div className="max-w-[740px] mx-auto py-6 space-y-8">
              {messages.map((msg) => {
                if (msg.role === "user") {
                  return (
                    <div key={msg.id} className="flex justify-end">
                      <div className="max-w-[82%] rounded-[22px] bg-[#f3f4f3] px-4 py-2.5 text-[14.5px] text-ink">
                        <div className="whitespace-pre-wrap leading-relaxed">{msg.content}</div>
                      </div>
                    </div>
                  )
                }

                const meta = msg.metadata ?? {}
                const alerts = Array.isArray(meta.discrepancy_alerts)
                  ? meta.discrepancy_alerts
                  : []
                const blindspots = normalizeDeclaredBlindspots(meta.declared_blindspots)
                const msgEvidenceCount =
                  (Array.isArray(meta.citations) ? meta.citations.length : 0) +
                  (Array.isArray(meta.hydrated_tables) ? meta.hydrated_tables.length : 0) +
                  (Array.isArray(meta.screening_results) ? meta.screening_results.length : 0) +
                  (Array.isArray(meta.calculator_traces) ? meta.calculator_traces.length : 0) +
                  (Array.isArray(meta.graph_hops) ? meta.graph_hops.length : 0) +
                  (Array.isArray(meta.recalled_memories) ? meta.recalled_memories.length : 0) +
                  (Array.isArray(meta.linked_sessions) ? meta.linked_sessions.length : 0) +
                  (meta.portfolio_snapshot_used ? 1 : 0)
                const activityItems = resolveActivityLogFromMetadata(meta)

                return (
                  <div key={msg.id} className="space-y-2">
                    <VerificationWarningBanner
                      blindspots={blindspots}
                      onInspect={() => onSelectAssistantMessage(msg.id)}
                    />

                    {activityItems.length > 0 && (
                      <ResearchActivityTimeline
                        items={activityItems}
                        isStreaming={false}
                        hasAnswerStarted={true}
                        thinkingDurationMs={meta.telemetry?.thinking_duration_ms ?? null}
                        latencyMs={meta.telemetry?.latency_ms ?? null}
                        reasoningTokens={meta.telemetry?.reasoning_tokens ?? null}
                        agentTurns={meta.telemetry?.agent_turns ?? null}
                        defaultExpanded={false}
                        onInspectCitation={(doc) => {
                          const numMatch = /^C(\d+)$/i.exec(doc.citation_id)
                          onBadgeClick(
                            {
                              type: "badge",
                              kind: "citation",
                              raw: `[${doc.citation_id}]`,
                              label: `[${doc.citation_id}]`,
                              refId: doc.citation_id,
                              index: numMatch ? Number(numMatch[1]) : undefined,
                              slot: doc.sub_doc_slot,
                              page: doc.page,
                            },
                            msg.id,
                          )
                        }}
                        onInspectTable={(tableId) => {
                          const tblIdx = Array.isArray(meta.hydrated_tables)
                            ? meta.hydrated_tables.findIndex((t) => t.table_id === tableId)
                            : -1
                          onBadgeClick(
                            {
                              type: "badge",
                              kind: "table",
                              raw: `[TBL:${tableId}]`,
                              label: "[TBL]",
                              refId: tableId,
                              index: tblIdx >= 0 ? tblIdx + 1 : 1,
                            },
                            msg.id,
                          )
                        }}
                        onInspectCalc={(calcId) => {
                          onBadgeClick(
                            {
                              type: "badge",
                              kind: "calculator",
                              raw: `[${calcId}]`,
                              label: `[${calcId}]`,
                              refId: calcId,
                            },
                            msg.id,
                          )
                        }}
                        onInspectGraph={() => {
                          onBadgeClick(
                            {
                              type: "badge",
                              kind: "kg",
                              raw: "[KG]",
                              label: "[KG]",
                              index: 1,
                            },
                            msg.id,
                          )
                        }}
                      />
                    )}

                    <MarkdownCitationView
                      content={msg.content}
                      linkedSessions={meta.linked_sessions}
                      onBadgeClick={(badge) => onBadgeClick(badge, msg.id)}
                      onSelectLinkedSession={onSelectLinkedSession}
                    />

                    <DiscrepancyAlertsBox alerts={alerts} />

                    {msgEvidenceCount > 0 && (
                      <div className="pt-1">
                        <button
                          type="button"
                          onClick={() => onSelectAssistantMessage(msg.id)}
                          className="inline-flex items-center gap-1.5 rounded-full px-2.5 h-7 -ml-2.5 text-[12px] text-muted hover:bg-soft hover:text-ink transition-colors cursor-pointer"
                        >
                          <FileStack className="h-3.5 w-3.5" aria-hidden="true" />
                          <span>Nguồn & số liệu ({msgEvidenceCount})</span>
                        </button>
                      </div>
                    )}
                  </div>
                )
              })}

              {streaming && (
                <div key="live-streaming-turn" className="space-y-2">
                  <ResearchActivityTimeline
                    items={liveActivity}
                    isStreaming={true}
                    hasAnswerStarted={Boolean(streamingText.trim())}
                    activeStageLabel={activeStageMessage}
                    startedAtMs={streamStartedAt}
                    defaultExpanded={false}
                  />

                  {streamingText ? (
                    <MarkdownCitationView
                      content={streamingText}
                      onBadgeClick={(b) => onBadgeClick(b)}
                    />
                  ) : null}
                </div>
              )}

              {streamError && (
                <div
                  role="alert"
                  className="rounded-[10px] border border-loss/30 bg-loss/6 px-3.5 py-2.5 text-[12.5px] text-loss"
                >
                  {streamError}
                </div>
              )}

              <div ref={scrollEndRef} />
            </div>
          </div>

          <div className="px-4 sm:px-6 pb-5 pt-2 bg-surface shrink-0">
            <div className="max-w-[740px] mx-auto">{renderComposer()}</div>
          </div>
        </>
      )}
    </div>
  )
}
