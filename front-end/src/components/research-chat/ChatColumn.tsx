"use client"

import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react"
import { AlertTriangle, ArrowUp, Square } from "lucide-react"
import { Pill } from "@/components/ui"
import {
  normalizeDeclaredBlindspots,
  resolveActivityLogFromMetadata,
  type DiscrepancyAlert,
  type ResearchActivityItem,
  type ResearchChatMessage,
} from "@/lib/api/researchChat"
import type { AuditBadgeToken } from "./markdownParser"
import { MarkdownCitationView } from "./MarkdownCitationView"
import { ResearchActivityTimeline } from "./ResearchActivityTimeline"
import type { StageProgressItem } from "./types"

interface ChatColumnProps {
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

// Strip backend-prepended raw Stage-7 markdown banner so UI renders at most ONE clean human note
const LEADING_STAGE7_BANNER_RE = /^>\s*⚠️\s*\*\*Cảnh báo kiểm chứng tự động[^\n]*\n\s*/i
const RAW_RISK_ENUM_RE =
  /\b(?:LOW|MEDIUM|HIGH)\s+(?:VENDOR_DATA_INCONSISTENCY|QUARTERLY_CF_UNRELIABLE|FY_QSUM_MISMATCH|AGGRESSIVE_LOAN_GROWTH|WEAK_CASH_CONVERSION|HIGH_RECEIVABLES_BLOAT|HIGH_INVENTORY_BLOAT|SHORT_TERM_LIQUIDITY_SQUEEZE)\b\s*[:–-]?\s*/g

function cleanDisplayContent(raw: string): string {
  if (!raw) return ""
  return raw.replace(LEADING_STAGE7_BANNER_RE, "").replace(RAW_RISK_ENUM_RE, "")
}

function VerificationNotice({
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
      role="status"
      className="mb-2 inline-flex flex-wrap items-center gap-2 rounded-[10px] bg-warning/8 border border-warning/25 px-3 py-1.5 text-[12px] text-secondary"
    >
      <AlertTriangle className="h-3.5 w-3.5 text-warning shrink-0" aria-hidden="true" />
      <span>
        Có {critical.length} chỉ số tóm tắt cần đối chiếu thêm với bảng gốc.
      </span>
      <button
        type="button"
        onClick={onInspect}
        className="font-medium text-ink underline underline-offset-2 hover:text-mineral cursor-pointer"
      >
        Xem số liệu gốc
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
  const scrollEndRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const isEmptyState = messages.length === 0 && !streaming

  useEffect(() => {
    if (!isEmptyState) {
      scrollEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" })
    }
  }, [isEmptyState, messages.length, streamingText, liveActivity.length])

  useEffect(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = "0px"
    el.style.height = `${Math.min(Math.max(el.scrollHeight, 28), 180)}px`
  }, [draft])

  const submitDraft = () => {
    const clean = draft.trim()
    if (!clean || streaming) return
    setDraft("")
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

  const renderComposer = () => (
    <form
      onSubmit={handleSubmit}
      className="flex items-end gap-3 min-h-[56px] rounded-[28px] border border-line/85 bg-[#f4f5f4]/80 px-5 py-2.5 shadow-[0_2px_16px_rgba(24,32,29,0.05)] focus-within:bg-surface focus-within:border-line-strong focus-within:shadow-[0_6px_26px_rgba(24,32,29,0.09)] transition-all"
    >
      <textarea
        ref={textareaRef}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={handleKeyDown}
        rows={1}
        disabled={streaming}
        placeholder="Hỏi bất kỳ cổ phiếu, báo cáo tài chính hay ngành nào…"
        aria-label="Nhập câu hỏi phân tích doanh nghiệp"
        className="flex-1 min-h-[28px] max-h-[180px] resize-none bg-transparent py-1 text-[15px] leading-[1.5] text-ink placeholder:text-muted focus:outline-none disabled:opacity-60"
      />

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
          className="h-9 w-9 rounded-full bg-ink text-paper flex items-center justify-center hover:opacity-90 disabled:bg-[#e4e7e5] disabled:text-disabled transition-all shrink-0 cursor-pointer disabled:cursor-default"
        >
          <ArrowUp className="h-[18px] w-[18px] stroke-[2.25]" aria-hidden="true" />
        </button>
      )}
    </form>
  )

  return (
    <div className="relative flex-1 min-w-0 flex flex-col h-full bg-surface overflow-hidden">
      {isEmptyState ? (
        /* Pure ChatGPT-Style Centered Empty State */
        <div className="flex-1 flex flex-col items-center justify-center px-4 sm:px-6 pb-14">
          <h1 className="text-[25px] sm:text-[28px] font-normal tracking-[-0.015em] text-ink text-center mb-7 select-none">
            Hôm nay bạn dự định phân tích gì?
          </h1>

          <div className="w-full max-w-[680px]">
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
        /* Clean Borderless Conversation Stream + Soft Top Scroll Mask + Bottom Pill Composer */
        <>
          <div
            aria-hidden="true"
            className="pointer-events-none absolute top-0 inset-x-0 h-5 bg-gradient-to-b from-surface via-surface/85 to-transparent z-10"
          />

          <div className="flex-1 overflow-y-auto px-4 sm:px-6">
            <div className="max-w-[740px] mx-auto pt-4 pb-6 space-y-8">
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
                const activityItems = resolveActivityLogFromMetadata(meta)

                return (
                  <div key={msg.id} className="space-y-2">
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

                    <VerificationNotice
                      blindspots={blindspots}
                      onInspect={() => onSelectAssistantMessage(msg.id)}
                    />

                    <MarkdownCitationView
                      content={cleanDisplayContent(msg.content)}
                      linkedSessions={meta.linked_sessions}
                      onBadgeClick={(badge) => onBadgeClick(badge, msg.id)}
                      onSelectLinkedSession={onSelectLinkedSession}
                    />

                    <DiscrepancyAlertsBox alerts={alerts} />
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
                      content={cleanDisplayContent(streamingText)}
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
