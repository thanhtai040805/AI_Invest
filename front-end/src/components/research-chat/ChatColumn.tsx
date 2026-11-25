"use client"

import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react"
import {
  AlertTriangle,
  AtSign,
  Briefcase,
  CalendarClock,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Eye,
  EyeOff,
  Layers3,
  Loader2,
  MessageSquare,
  Send,
  Sparkles,
  Square,
  X,
} from "lucide-react"
import { Button, Pill } from "@/components/ui"
import {
  normalizeDeclaredBlindspots,
  resolveActivityLogFromMetadata,
  type DiscrepancyAlert,
  type ResearchActivityItem,
  type ResearchChatMessage,
  type ResearchChatMode,
  type SseStageName,
} from "@/lib/api/researchChat"
import type { AuditBadgeToken } from "./markdownParser"
import { MarkdownCitationView } from "./MarkdownCitationView"
import { ResearchActivityTimeline } from "./ResearchActivityTimeline"
import {
  GENERAL_STARTER_PROMPTS,
  MOAT_QUICK_PROMPTS,
  PILOT_TICKERS,
  type StageProgressItem,
} from "./types"

interface ChatColumnProps {
  chatMode: ResearchChatMode
  onChatModeChange: (mode: ResearchChatMode) => void
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
  selectedAssistantMessageId: string | null
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
      className="mb-3 rounded-[8px] border border-loss/40 bg-loss/8 px-3 py-2.5 flex flex-wrap items-center justify-between gap-2 text-[12px]"
    >
      <div className="flex items-start gap-2 min-w-0 flex-1">
        <AlertTriangle className="h-4 w-4 text-loss shrink-0 mt-0.5" aria-hidden="true" />
        <div className="min-w-0">
          <div className="font-semibold text-loss">
            Cảnh báo Kiểm chứng Stage 7 ({critical.length} mục cần đối chiếu)
          </div>
          <div className="text-secondary text-[11.5px] leading-snug mt-0.5">
            {critical
              .slice(0, 2)
              .map((c) => `${c.code}: ${c.reason}`)
              .join(" · ")}
          </div>
        </div>
      </div>
      <button
        type="button"
        onClick={onInspect}
        className="shrink-0 rounded-[6px] border border-loss/35 bg-surface px-2.5 py-1 text-[11px] font-medium text-loss hover:bg-loss/10 transition-colors"
      >
        Mở Inspector
      </button>
    </div>
  )
}

function DiscrepancyAlertsBox({ alerts }: { alerts: DiscrepancyAlert[] }) {
  if (!alerts || alerts.length === 0) return null
  return (
    <div className="mt-3.5 rounded-[8px] border border-warning/35 bg-warning/8 p-3 space-y-2">
      <div className="flex items-center gap-1.5 text-[11.5px] font-semibold uppercase tracking-[0.06em] text-warning">
        <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        <span>Cảnh báo Mâu thuẫn PR vs Kiểm toán / Core DB ({alerts.length})</span>
      </div>
      <div className="space-y-2">
        {alerts.map((alert, idx) => {
          const sev = String(alert.severity ?? "MEDIUM").toUpperCase()
          const isHigh = sev === "HIGH"
          return (
            <div
              key={alert.alert_id ?? `disc-${idx}`}
              className="rounded-[6px] border border-line bg-surface px-3 py-2 text-[12.5px] space-y-1"
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
              {(alert.narrative_claim || alert.audited_value) && (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 pt-1 text-[12px]">
                  {alert.narrative_claim && (
                    <div className="rounded bg-paper px-2 py-1.5 border border-line">
                      <div className="text-[10px] font-semibold uppercase tracking-wider text-muted">
                        Tuyên bố BCTN / PR
                      </div>
                      <div className="text-ink mt-0.5">{alert.narrative_claim}</div>
                    </div>
                  )}
                  {alert.audited_value && (
                    <div className="rounded bg-soft/70 px-2 py-1.5 border border-line">
                      <div className="text-[10px] font-semibold uppercase tracking-wider text-muted">
                        Số liệu Kiểm toán / Core DB
                      </div>
                      <div className="text-ink font-mono mt-0.5">{alert.audited_value}</div>
                    </div>
                  )}
                </div>
              )}
              {Array.isArray(alert.citations) && alert.citations.length > 0 && (
                <div className="flex flex-wrap gap-1 pt-0.5">
                  {alert.citations.map((cit, cIdx) => (
                    <span
                      key={`${cit}-${cIdx}`}
                      className="rounded bg-soft px-1.5 py-0.5 font-mono text-[10.5px] text-secondary border border-line"
                    >
                      {cit}
                    </span>
                  ))}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

function DeclaredBlindspotsBox({
  blindspots,
}: {
  blindspots: ReturnType<typeof normalizeDeclaredBlindspots>
}) {
  if (!blindspots || blindspots.length === 0) return null
  return (
    <div className="mt-3 rounded-[8px] border border-mineral/30 bg-mineral/6 p-3 space-y-2">
      <div className="flex items-center gap-1.5 text-[11.5px] font-semibold uppercase tracking-[0.06em] text-mineral">
        <EyeOff className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        <span>Điểm mù Khai báo trung thực (Declared Blindspots · {blindspots.length})</span>
      </div>
      <ul className="space-y-1.5">
        {blindspots.map((b, idx) => (
          <li
            key={`${b.code}-${idx}`}
            className="flex flex-wrap items-start gap-2 rounded-[6px] border border-line bg-surface px-2.5 py-1.5 text-[12px]"
          >
            <span className="shrink-0 rounded bg-mineral/10 px-1.5 py-0.5 font-mono text-[10.5px] font-semibold text-mineral">
              {b.code}
            </span>
            <span className="text-secondary flex-1 min-w-[180px] leading-relaxed">{b.reason}</span>
            {b.scope && (
              <span className="font-mono text-[10.5px] text-muted shrink-0">[{b.scope}]</span>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}

function StagePillsBar({
  stages,
  streaming,
}: {
  stages: StageProgressItem[]
  streaming: boolean
}) {
  const hasProgress = streaming || stages.some((s) => s.status !== "idle")
  if (!hasProgress) return null

  return (
    <div
      aria-label="Tiến trình đường ống SAG v2"
      className="flex flex-wrap items-center gap-1.5 rounded-[8px] border border-line bg-surface/90 px-3 py-1.5"
    >
      {stages.map((item, idx) => {
        const isRunning = item.status === "running"
        const isDone = item.status === "done"
        const isError = item.status === "error"
        return (
          <div key={item.stage} className="flex items-center gap-1">
            {idx > 0 && <span className="text-muted text-[10px]">→</span>}
            <span
              title={item.detail ? JSON.stringify(item.detail) : item.stage}
              className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10.5px] font-medium transition-colors ${
                isRunning
                  ? "border-mineral/45 bg-mineral/12 text-mineral"
                  : isDone
                    ? "border-teal/35 bg-teal/10 text-teal"
                    : isError
                      ? "border-loss/40 bg-loss/10 text-loss"
                      : "border-line bg-paper text-muted"
              }`}
            >
              {isRunning ? (
                <Loader2 className="h-2.5 w-2.5 animate-spin shrink-0" aria-hidden="true" />
              ) : isDone ? (
                <CheckCircle2 className="h-2.5 w-2.5 shrink-0" aria-hidden="true" />
              ) : (
                <span className="h-1.5 w-1.5 rounded-full bg-current opacity-50" />
              )}
              <span>{item.label}</span>
            </span>
          </div>
        )
      })}
    </div>
  )
}

export function ChatColumn({
  chatMode,
  onChatModeChange,
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
  selectedAssistantMessageId,
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
  const [tickerPickerOpen, setTickerPickerOpen] = useState(false)
  const [customTickerInput, setCustomTickerInput] = useState("")
  const [moatPromptsOpen, setMoatPromptsOpen] = useState(false)
  const scrollEndRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    scrollEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" })
  }, [messages.length, streamingText, liveActivity.length])

  const taggedTickers = [
    ...(primaryTicker ? [primaryTicker] : []),
    ...compareTickers.filter((t) => t !== primaryTicker),
  ]

  const attachTicker = (rawSym: string) => {
    const sym = cleanTicker(rawSym)
    if (!sym || sym.length < 2 || sym.length > 8) return
    if (taggedTickers.includes(sym)) return
    if (taggedTickers.length >= 6) return
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
    if (nextPrimary !== primaryTicker) {
      onPrimaryTickerChange(nextPrimary)
    }
    if (nextCompare.length !== compareTickers.length) {
      onCompareTickersChange(nextCompare)
    }
    setCustomTickerInput("")
  }

  const submitDraft = () => {
    const clean = draft.trim()
    if (!clean || streaming) return
    setDraft("")
    setTickerPickerOpen(false)
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

  const activeStage: SseStageName | null =
    stages.find((s) => s.status === "running")?.stage ?? null

  return (
    <div className="flex-1 min-w-0 flex flex-col h-full bg-paper/45 overflow-hidden">
      {/* Compact Live Pipeline Strip (shown only while streaming or on active run) */}
      {(streaming || stages.some((s) => s.status !== "idle")) && (
        <div className="px-4 pt-2.5 shrink-0">
          <div className="max-w-4xl mx-auto">
            <StagePillsBar stages={stages} streaming={streaming} />
          </div>
        </div>
      )}

      {/* Scrollable Wide Central Chat Stream */}
      <div className="flex-1 overflow-y-auto px-4 py-5">
        <div className="max-w-4xl mx-auto space-y-4">
          {messages.length === 0 && !streaming && (
            <div className="py-6 sm:py-10 space-y-6">
              <div className="text-center space-y-2 max-w-2xl mx-auto">
                <div className="inline-flex items-center gap-1.5 rounded-full border border-line bg-surface px-3 py-1 text-[11.5px] font-medium text-secondary">
                  <Sparkles className="h-3.5 w-3.5 text-mineral" aria-hidden="true" />
                  <span>
                    {chatMode === "MOAT"
                      ? "MOAT Broker Mode · Kiểm định định lượng & Đối chất PR"
                      : "General Multi-Hop Chat · Toàn kho báo cáo tài chính HOSE"}
                  </span>
                </div>
                <h2 className="text-[20px] sm:text-[22px] font-semibold tracking-tight text-ink">
                  {chatMode === "MOAT"
                    ? `Kiểm định MOAT & Chất lượng lợi nhuận${primaryTicker ? ` · ${primaryTicker}` : ""}`
                    : "Hỏi bất cứ điều gì về doanh nghiệp HOSE"}
                </h2>
                <p className="text-[13px] text-secondary leading-relaxed">
                  {chatMode === "MOAT"
                    ? "Chế độ kiểm định chuyên sâu: đối chiếu tuyên bố BCTN với bảng kiểm toán VERIFIED, tính toán Python không nhẩm tay, và gắn trích dẫn quote_hash."
                    : "Trò chuyện tự do nhiều lượt không cần khóa mã cổ phiếu. Hệ thống tự nhận diện mã từ câu hỏi, gõ @FPT/@MWG để gắn nhanh, và tự động truy xuất đa bước."}
                </p>
              </div>

              {chatMode === "MOAT" ? (
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2.5">
                  {MOAT_QUICK_PROMPTS.map((item) => (
                    <button
                      key={item.id}
                      type="button"
                      disabled={streaming}
                      onClick={() => onSendPrompt(item.buildPrompt(primaryTicker))}
                      className="group text-left rounded-[10px] border border-line bg-surface px-3.5 py-3 transition-all hover:border-mineral/50 hover:shadow-xs disabled:opacity-50"
                    >
                      <div className="text-[12.5px] font-semibold text-ink group-hover:text-mineral leading-snug">
                        {item.title}
                      </div>
                      <div className="mt-1.5 font-mono text-[10.5px] text-muted">{item.tag}</div>
                    </button>
                  ))}
                </div>
              ) : (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                  {GENERAL_STARTER_PROMPTS.map((item) => (
                    <button
                      key={item.id}
                      type="button"
                      disabled={streaming}
                      onClick={() => onSendPrompt(item.prompt)}
                      className="group text-left rounded-[10px] border border-line bg-surface px-3.5 py-3 transition-all hover:border-mineral/50 hover:shadow-xs disabled:opacity-50"
                    >
                      <div className="text-[13px] font-semibold text-ink group-hover:text-mineral leading-snug">
                        {item.title}
                      </div>
                      <p className="mt-1 text-[12px] text-secondary line-clamp-2 leading-relaxed">
                        {item.prompt}
                      </p>
                      <div className="mt-1.5 font-mono text-[10px] text-muted">{item.tag}</div>
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}

          {messages.map((msg) => {
            if (msg.role === "user") {
              return (
                <div key={msg.id} className="flex justify-end">
                  <div className="max-w-[85%] rounded-[14px] rounded-tr-[4px] border border-line-strong bg-surface px-4 py-2.5 text-[13.5px] text-ink shadow-xs">
                    <div className="whitespace-pre-wrap leading-relaxed">{msg.content}</div>
                  </div>
                </div>
              )
            }

            const meta = msg.metadata ?? {}
            const isSelected = msg.id === selectedAssistantMessageId
            const alerts = Array.isArray(meta.discrepancy_alerts) ? meta.discrepancy_alerts : []
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
            const msgMode = meta.chat_mode === "MOAT" ? "MOAT" : "General"
            const isShortCircuit = meta.eval_eligible === false
            const activityItems = resolveActivityLogFromMetadata(meta)

            return (
              <div
                key={msg.id}
                className={`rounded-[12px] border bg-surface p-4 sm:p-5 transition-colors ${
                  isSelected
                    ? "border-mineral/55 shadow-[0_0_0_1px_rgba(70,103,121,0.12)]"
                    : "border-line"
                }`}
              >
                <div className="flex flex-wrap items-center justify-between gap-2 pb-2.5 mb-3 border-b border-line">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <Pill tone={msgMode === "MOAT" ? "mineral" : "teal"}>
                      {msgMode === "MOAT" ? "MOAT Broker" : "SAG v2"}
                    </Pill>
                    {isShortCircuit && meta.route_intent && (
                      <Pill tone="neutral">{meta.route_intent}</Pill>
                    )}
                    {Array.isArray(meta.expanded_tickers) && meta.expanded_tickers.length > 0 ? (
                      meta.expanded_tickers.map((tk) => (
                        <span
                          key={tk}
                          className="rounded bg-soft px-1.5 py-0.5 font-mono text-[10.5px] font-semibold text-ink border border-line"
                        >
                          {tk}
                        </span>
                      ))
                    ) : meta.primary_ticker ? (
                      <span className="rounded bg-soft px-1.5 py-0.5 font-mono text-[10.5px] font-semibold text-ink border border-line">
                        {meta.primary_ticker}
                      </span>
                    ) : null}
                    {meta.as_of_date && (
                      <span className="font-mono text-[11px] text-muted">
                        PIT {meta.as_of_date}
                      </span>
                    )}
                  </div>
                  <button
                    type="button"
                    onClick={() => onSelectAssistantMessage(msg.id)}
                    className={`inline-flex items-center gap-1.5 rounded-[6px] px-2.5 py-1 text-[11.5px] font-medium border transition-colors ${
                      isSelected
                        ? "border-mineral/45 bg-mineral/10 text-mineral"
                        : "border-line bg-paper text-secondary hover:text-ink hover:bg-soft"
                    }`}
                  >
                    <Eye className="h-3.5 w-3.5" aria-hidden="true" />
                    <span>
                      Bằng chứng{msgEvidenceCount > 0 ? ` (${msgEvidenceCount})` : ""}
                    </span>
                  </button>
                </div>

                <VerificationWarningBanner
                  blindspots={blindspots}
                  onInspect={() => onSelectAssistantMessage(msg.id)}
                />

                {activityItems.length > 0 && (
                  <ResearchActivityTimeline
                    items={activityItems}
                    isStreaming={false}
                    latencyMs={meta.telemetry?.latency_ms ?? null}
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
                          label: `[TBL]`,
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
                <DeclaredBlindspotsBox blindspots={blindspots} />
              </div>
            )
          })}

          {streaming && (
            <div className="rounded-[12px] border border-mineral/45 bg-surface p-4 sm:p-5 space-y-2.5">
              <div className="flex items-center justify-between gap-2 pb-2 border-b border-line">
                <div className="flex items-center gap-2 text-[12px] font-medium text-mineral">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                  <span>
                    {activeStage
                      ? `Đang chạy pha: ${activeStage}…`
                      : "Đang suy luận & truy xuất dữ liệu kiểm chứng…"}
                  </span>
                </div>
                <Pill tone="mineral">SSE Live</Pill>
              </div>
              <ResearchActivityTimeline
                items={liveActivity}
                isStreaming={true}
                startedAtMs={streamStartedAt}
                defaultExpanded={true}
              />
              {streamingText ? (
                <MarkdownCitationView content={streamingText} onBadgeClick={(b) => onBadgeClick(b)} />
              ) : liveActivity.length === 0 ? (
                <div className="space-y-2 py-1">
                  <div className="h-3 w-3/4 rounded bg-soft animate-pulse" />
                  <div className="h-3 w-1/2 rounded bg-soft animate-pulse" />
                </div>
              ) : null}
            </div>
          )}

          {streamError && (
            <div
              role="alert"
              className="rounded-[8px] border border-loss/35 bg-loss/8 px-3.5 py-2.5 text-[12.5px] text-loss"
            >
              {streamError}
            </div>
          )}

          <div ref={scrollEndRef} />
        </div>
      </div>

      {/* Compact ChatGPT-Style Composer Dock */}
      <div className="border-t border-line bg-surface px-4 py-3 shrink-0">
        <div className="max-w-4xl mx-auto space-y-2">
          {/* Optional MOAT quick-prompt drawer when messages already exist */}
          {chatMode === "MOAT" && messages.length > 0 && moatPromptsOpen && (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-1.5 pb-1">
              {MOAT_QUICK_PROMPTS.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  disabled={streaming}
                  onClick={() => {
                    setMoatPromptsOpen(false)
                    onSendPrompt(item.buildPrompt(primaryTicker))
                  }}
                  className="text-left rounded-[7px] border border-line bg-paper px-2.5 py-1.5 hover:border-mineral/45 hover:bg-soft/75 transition-colors disabled:opacity-50"
                >
                  <div className="text-[11.5px] font-medium text-ink line-clamp-1">{item.title}</div>
                  <div className="font-mono text-[9.5px] text-muted">{item.tag}</div>
                </button>
              ))}
            </div>
          )}

          {/* Optional Quick @Ticker Picker Drawer */}
          {tickerPickerOpen && (
            <div className="rounded-[8px] border border-line bg-paper p-2.5 space-y-2">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-[11px] font-semibold uppercase tracking-[0.06em] text-muted">
                  Gắn nhanh @mã cổ phiếu (tùy chọn · tối đa 6 mã)
                </span>
                <button
                  type="button"
                  onClick={() => setTickerPickerOpen(false)}
                  aria-label="Đóng chọn mã"
                  className="text-muted hover:text-ink"
                >
                  <X className="h-3.5 w-3.5" aria-hidden="true" />
                </button>
              </div>
              <div className="flex flex-wrap items-center gap-1.5">
                {(availableTickers && availableTickers.length > 0 ? availableTickers : PILOT_TICKERS).map((sym) => {
                  const isAttached = taggedTickers.includes(sym)
                  return (
                    <button
                      key={sym}
                      type="button"
                      disabled={streaming || (!isAttached && taggedTickers.length >= 6)}
                      onClick={() => (isAttached ? removeTicker(sym) : insertAtTag(sym))}
                      className={`h-6.5 px-2 rounded-[5px] font-mono text-[11px] font-semibold border transition-colors ${
                        isAttached
                          ? "bg-ink text-paper border-ink"
                          : "bg-surface text-secondary border-line hover:border-mineral hover:text-ink"
                      } disabled:opacity-45`}
                    >
                      @{sym}
                    </button>
                  )
                })}
                <form onSubmit={handleAddCustomTicker} className="flex items-center gap-1 ml-1">
                  <input
                    type="text"
                    value={customTickerInput}
                    onChange={(e) => setCustomTickerInput(e.target.value.toUpperCase())}
                    placeholder="Mã khác…"
                    maxLength={24}
                    disabled={streaming || taggedTickers.length >= 6}
                    aria-label="Nhập mã cổ phiếu tùy chọn"
                    className="h-6.5 w-24 rounded-[5px] border border-line bg-surface px-2 font-mono text-[11px] text-ink placeholder:text-muted focus:border-mineral focus:outline-none"
                  />
                  <Button
                    type="submit"
                    size="sm"
                    variant="secondary"
                    disabled={streaming || taggedTickers.length >= 6 || !customTickerInput.trim()}
                    className="!h-6.5 !px-2 text-[10.5px]"
                  >
                    Gắn
                  </Button>
                </form>
              </div>
            </div>
          )}

          {/* Active optional tagged tickers strip */}
          {taggedTickers.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-[11px] text-muted">Phạm vi gắn thẻ:</span>
              {taggedTickers.map((sym) => (
                <span
                  key={sym}
                  className="inline-flex items-center gap-1 h-[22px] pl-2 pr-1 rounded-full border border-mineral/35 bg-mineral/10 font-mono text-[11px] font-semibold text-mineral"
                >
                  @{sym}
                  <button
                    type="button"
                    disabled={streaming}
                    onClick={() => removeTicker(sym)}
                    aria-label={`Bỏ gắn mã ${sym}`}
                    className="rounded-full p-0.5 hover:bg-loss/15 hover:text-loss"
                  >
                    <X className="h-3 w-3" aria-hidden="true" />
                  </button>
                </span>
              ))}
              <button
                type="button"
                disabled={streaming}
                onClick={() => {
                  onPrimaryTickerChange("")
                  onCompareTickersChange([])
                }}
                className="text-[11px] text-muted hover:text-ink underline underline-offset-2 ml-1"
              >
                Bỏ lọc (tự động)
              </button>
            </div>
          )}

          {/* Input Box + Send/Stop */}
          <form onSubmit={handleSubmit} className="rounded-[12px] border border-line-strong bg-paper p-2 focus-within:border-mineral transition-colors">
            <div className="flex items-end gap-2">
              <textarea
                ref={textareaRef}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={handleKeyDown}
                rows={2}
                disabled={streaming}
                placeholder={
                  chatMode === "MOAT"
                    ? `Đặt câu hỏi kiểm định MOAT${primaryTicker ? ` cho ${primaryTicker}` : " (gõ @FPT, @PVT hoặc hỏi tự do)"}…`
                    : "Hỏi bất cứ điều gì về báo cáo doanh nghiệp HOSE (gõ @FPT, @MWG để gắn mã, Enter để gửi)…"
                }
                aria-label="Nhập câu hỏi phân tích doanh nghiệp"
                className="flex-1 resize-none bg-transparent px-2 py-1 text-[13.5px] text-ink placeholder:text-muted focus:outline-none disabled:opacity-60"
              />
              {streaming ? (
                <Button
                  type="button"
                  variant="secondary"
                  onClick={onStopStream}
                  className="!h-9 !px-3 !border-loss/40 !text-loss hover:!bg-loss/10 shrink-0"
                >
                  <Square className="h-3.5 w-3.5 fill-current" aria-hidden="true" />
                  <span>Dừng</span>
                </Button>
              ) : (
                <Button
                  type="submit"
                  variant="primary"
                  disabled={!draft.trim()}
                  className="!h-9 !px-3.5 shrink-0"
                >
                  <Send className="h-3.5 w-3.5" aria-hidden="true" />
                  <span>Gửi</span>
                </Button>
              )}
            </div>

            {/* Compact Composer Controls Row: General/MOAT + @Ticker + KG + PIT as_of_date */}
            <div className="flex flex-wrap items-center justify-between gap-2 pt-2 mt-1.5 border-t border-line/70">
              <div className="flex flex-wrap items-center gap-1.5">
                {/* Mode Toggle: General vs MOAT Broker */}
                <div
                  role="group"
                  aria-label="Chế độ phân tích"
                  className="inline-flex items-center rounded-[7px] border border-line bg-surface p-0.5"
                >
                  <button
                    type="button"
                    disabled={streaming}
                    onClick={() => onChatModeChange("GENERAL")}
                    className={`inline-flex items-center gap-1 rounded-[5px] px-2 h-6 text-[11px] font-medium transition-colors ${
                      chatMode === "GENERAL"
                        ? "bg-ink text-paper"
                        : "text-secondary hover:text-ink"
                    }`}
                  >
                    <MessageSquare className="h-3 w-3" aria-hidden="true" />
                    <span>General</span>
                  </button>
                  <button
                    type="button"
                    disabled={streaming}
                    onClick={() => onChatModeChange("MOAT")}
                    className={`inline-flex items-center gap-1 rounded-[5px] px-2 h-6 text-[11px] font-medium transition-colors ${
                      chatMode === "MOAT"
                        ? "bg-mineral text-paper"
                        : "text-secondary hover:text-ink"
                    }`}
                  >
                    <Briefcase className="h-3 w-3" aria-hidden="true" />
                    <span>MOAT Broker</span>
                  </button>
                </div>

                {/* Quick @Ticker Button */}
                <button
                  type="button"
                  disabled={streaming}
                  onClick={() => setTickerPickerOpen((v) => !v)}
                  className={`inline-flex items-center gap-1 rounded-[7px] border px-2 h-7 text-[11px] font-medium transition-colors ${
                    tickerPickerOpen || taggedTickers.length > 0
                      ? "border-mineral/45 bg-mineral/10 text-mineral"
                      : "border-line bg-surface text-secondary hover:text-ink"
                  }`}
                >
                  <AtSign className="h-3 w-3" aria-hidden="true" />
                  <span>Gắn mã{taggedTickers.length > 0 ? ` (${taggedTickers.length}/6)` : ""}</span>
                </button>

                {/* 3-Layer KG Toggle Chip */}
                <button
                  type="button"
                  disabled={streaming}
                  onClick={() => onEnableKgChange(!enableKg)}
                  aria-pressed={enableKg}
                  className={`inline-flex items-center gap-1 rounded-[7px] border px-2 h-7 text-[11px] font-medium transition-colors ${
                    enableKg
                      ? "border-teal/40 bg-teal/10 text-teal"
                      : "border-line bg-surface text-muted hover:text-ink"
                  }`}
                >
                  <Layers3 className="h-3 w-3" aria-hidden="true" />
                  <span>KG {enableKg ? "Bật" : "Tắt"}</span>
                </button>

                {/* MOAT quick prompts drawer toggle when in MOAT mode with active messages */}
                {chatMode === "MOAT" && messages.length > 0 && (
                  <button
                    type="button"
                    disabled={streaming}
                    onClick={() => setMoatPromptsOpen((v) => !v)}
                    className="inline-flex items-center gap-1 rounded-[7px] border border-line bg-surface px-2 h-7 text-[11px] font-medium text-secondary hover:text-ink transition-colors"
                  >
                    <span>6 câu MOAT</span>
                    {moatPromptsOpen ? (
                      <ChevronUp className="h-3 w-3" aria-hidden="true" />
                    ) : (
                      <ChevronDown className="h-3 w-3" aria-hidden="true" />
                    )}
                  </button>
                )}
              </div>

              {/* Compact PIT as_of_date Picker */}
              <div className="flex items-center gap-1.5">
                <CalendarClock className="h-3.5 w-3.5 text-muted shrink-0" aria-hidden="true" />
                <input
                  type="date"
                  value={asOfDate}
                  max={todayDate}
                  disabled={streaming}
                  onChange={(e) => onAsOfDateChange(e.target.value)}
                  aria-label="Ngày Point-in-Time as_of_date"
                  className="h-7 rounded-[6px] border border-line bg-surface px-2 font-mono text-[11px] text-ink focus:border-mineral focus:outline-none"
                />
                <button
                  type="button"
                  disabled={streaming}
                  onClick={() => onAsOfDateChange(asOfDate === "2025-06-30" ? todayDate : "2025-06-30")}
                  title="Chuyển nhanh mốc tua ngược PIT 2025-06-30 để kiểm thử G4"
                  className={`h-7 px-2 rounded-[6px] border font-mono text-[10.5px] transition-colors ${
                    asOfDate === "2025-06-30"
                      ? "border-warning/40 bg-warning/12 text-warning font-semibold"
                      : "border-line bg-surface text-secondary hover:text-ink"
                  }`}
                >
                  {asOfDate === "2025-06-30" ? "Hôm nay" : "PIT 2025"}
                </button>
              </div>
            </div>
          </form>
        </div>
      </div>
    </div>
  )
}
