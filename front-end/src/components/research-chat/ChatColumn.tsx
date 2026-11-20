"use client"

import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react"
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Eye,
  EyeOff,
  Loader2,
  Send,
  Sparkles,
  Square,
} from "lucide-react"
import { Button, Panel, Pill } from "@/components/ui"
import {
  normalizeDeclaredBlindspots,
  type DiscrepancyAlert,
  type ResearchChatMessage,
  type SseStageName,
} from "@/lib/api/researchChat"
import type { AuditBadgeToken } from "./markdownParser"
import { MarkdownCitationView } from "./MarkdownCitationView"
import { MOAT_QUICK_PROMPTS, type StageProgressItem } from "./types"

interface ChatColumnProps {
  primaryTicker: string
  messages: ResearchChatMessage[]
  selectedAssistantMessageId: string | null
  onSelectAssistantMessage: (messageId: string) => void
  streaming: boolean
  streamingText: string
  stages: StageProgressItem[]
  streamError: string | null
  onSendPrompt: (query: string) => void
  onStopStream: () => void
  onBadgeClick: (badge: AuditBadgeToken, messageId?: string) => void
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
      className="flex flex-wrap items-center gap-1.5 rounded-[8px] border border-line bg-paper px-3 py-2"
    >
      {stages.map((item, idx) => {
        const isRunning = item.status === "running"
        const isDone = item.status === "done"
        const isError = item.status === "error"
        return (
          <div key={item.stage} className="flex items-center gap-1.5">
            {idx > 0 && <span className="text-muted text-[11px]">→</span>}
            <span
              title={item.detail ? JSON.stringify(item.detail) : item.stage}
              className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium transition-colors ${
                isRunning
                  ? "border-mineral/45 bg-mineral/12 text-mineral"
                  : isDone
                    ? "border-teal/35 bg-teal/10 text-teal"
                    : isError
                      ? "border-loss/40 bg-loss/10 text-loss"
                      : "border-line bg-surface text-muted"
              }`}
            >
              {isRunning ? (
                <Loader2 className="h-3 w-3 animate-spin shrink-0" aria-hidden="true" />
              ) : isDone ? (
                <CheckCircle2 className="h-3 w-3 shrink-0" aria-hidden="true" />
              ) : (
                <span className="h-1.5 w-1.5 rounded-full bg-current opacity-60" />
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
  primaryTicker,
  messages,
  selectedAssistantMessageId,
  onSelectAssistantMessage,
  streaming,
  streamingText,
  stages,
  streamError,
  onSendPrompt,
  onStopStream,
  onBadgeClick,
}: ChatColumnProps) {
  const [draft, setDraft] = useState("")
  const [promptsExpanded, setPromptsExpanded] = useState(true)
  const scrollEndRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    scrollEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" })
  }, [messages.length, streamingText])

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

  const activeStage: SseStageName | null =
    stages.find((s) => s.status === "running")?.stage ?? null

  return (
    <Panel flush className="flex flex-col min-h-[680px] xl:h-[calc(100dvh-215px)] overflow-hidden">
      {/* Header + Quick-Prompts Toggle */}
      <div className="border-b border-line px-4 py-3 bg-surface shrink-0 space-y-2.5">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <Sparkles className="h-4 w-4 text-mineral shrink-0" aria-hidden="true" />
            <h2 className="text-[14px] font-semibold text-ink truncate">
              Tổng hợp Phân tích Doanh nghiệp · {primaryTicker}
            </h2>
          </div>
          <button
            type="button"
            onClick={() => setPromptsExpanded((v) => !v)}
            className="inline-flex items-center gap-1 text-[11.5px] font-medium text-secondary hover:text-ink"
          >
            <span>6 Câu hỏi kiểm định MOAT</span>
            {promptsExpanded ? (
              <ChevronUp className="h-3.5 w-3.5" aria-hidden="true" />
            ) : (
              <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />
            )}
          </button>
        </div>

        {promptsExpanded && (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-1.5">
            {MOAT_QUICK_PROMPTS.map((item) => (
              <button
                key={item.id}
                type="button"
                disabled={streaming}
                onClick={() => onSendPrompt(item.buildPrompt(primaryTicker))}
                className="group text-left rounded-[7px] border border-line bg-paper/70 px-2.5 py-2 transition-colors hover:border-mineral/45 hover:bg-soft/75 disabled:opacity-50"
              >
                <div className="text-[12px] font-medium text-ink group-hover:text-mineral leading-snug line-clamp-2">
                  {item.title}
                </div>
                <div className="mt-1 font-mono text-[10px] text-muted">{item.tag}</div>
              </button>
            ))}
          </div>
        )}

        <StagePillsBar stages={stages} streaming={streaming} />
      </div>

      {/* Scrollable Chat Stream */}
      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4 bg-paper/45">
        {messages.length === 0 && !streaming && (
          <div className="rounded-[10px] border border-dashed border-line-strong bg-surface/80 p-6 text-center space-y-2 my-6">
            <div className="text-[14px] font-semibold text-ink">
              Bắt đầu phiên kiểm nghiệm MOAT cho {primaryTicker}
            </div>
            <p className="text-[12.5px] text-secondary max-w-lg mx-auto leading-relaxed">
              Chọn 1 trong 6 câu hỏi kiểm định MOAT phía trên hoặc nhập câu hỏi tùy ý. Mọi con số tài chính sẽ chạy qua Python Calculator và gắn thẻ trích dẫn kiểm chứng <code className="font-mono text-[11px] bg-soft px-1 rounded">quote_hash</code>.
            </p>
          </div>
        )}

        {messages.map((msg) => {
          if (msg.role === "user") {
            return (
              <div key={msg.id} className="flex justify-end">
                <div className="max-w-[88%] rounded-[10px] border border-line-strong bg-surface px-3.5 py-2.5 text-[13.5px] text-ink shadow-xs">
                  <div className="whitespace-pre-wrap leading-relaxed">{msg.content}</div>
                </div>
              </div>
            )
          }

          const meta = msg.metadata ?? {}
          const isSelected = msg.id === selectedAssistantMessageId
          const alerts = Array.isArray(meta.discrepancy_alerts) ? meta.discrepancy_alerts : []
          const blindspots = normalizeDeclaredBlindspots(meta.declared_blindspots)

          return (
            <div
              key={msg.id}
              className={`rounded-[10px] border bg-surface p-4 transition-colors ${
                isSelected ? "border-mineral/60 shadow-[0_0_0_1px_rgba(70,103,121,0.14)]" : "border-line"
              }`}
            >
              <div className="flex flex-wrap items-center justify-between gap-2 pb-2.5 mb-3 border-b border-line">
                <div className="flex flex-wrap items-center gap-1.5">
                  <Pill tone="teal">
                    {meta.primary_ticker || primaryTicker} · SAG v2
                  </Pill>
                  {meta.as_of_date && (
                    <span className="font-mono text-[11px] text-muted">
                      PIT {meta.as_of_date}
                    </span>
                  )}
                  {Array.isArray(meta.expanded_tickers) && meta.expanded_tickers.length > 1 && (
                    <span className="font-mono text-[11px] text-secondary">
                      ({meta.expanded_tickers.join(", ")})
                    </span>
                  )}
                </div>
                <button
                  type="button"
                  onClick={() => onSelectAssistantMessage(msg.id)}
                  className={`inline-flex items-center gap-1 rounded-[6px] px-2 py-1 text-[11px] font-medium border transition-colors ${
                    isSelected
                      ? "border-mineral/40 bg-mineral/10 text-mineral"
                      : "border-line bg-paper text-secondary hover:text-ink"
                  }`}
                >
                  <Eye className="h-3 w-3" aria-hidden="true" />
                  <span>{isSelected ? "Đang soi Inspector" : "Soi bằng chứng"}</span>
                </button>
              </div>

              <MarkdownCitationView
                content={msg.content}
                onBadgeClick={(badge) => onBadgeClick(badge, msg.id)}
              />

              <DiscrepancyAlertsBox alerts={alerts} />
              <DeclaredBlindspotsBox blindspots={blindspots} />
            </div>
          )
        })}

        {streaming && (
          <div className="rounded-[10px] border border-mineral/45 bg-surface p-4 space-y-2.5">
            <div className="flex items-center justify-between gap-2 pb-2 border-b border-line">
              <div className="flex items-center gap-2 text-[12px] font-medium text-mineral">
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                <span>
                  {activeStage
                    ? `Đang chạy pha: ${activeStage}…`
                    : "Đang nhận dữ liệu tổng hợp có kiểm chứng…"}
                </span>
              </div>
              <Pill tone="mineral">SSE Live</Pill>
            </div>
            {streamingText ? (
              <MarkdownCitationView content={streamingText} onBadgeClick={(b) => onBadgeClick(b)} />
            ) : (
              <div className="space-y-2 py-1">
                <div className="h-3 w-3/4 rounded bg-soft animate-pulse" />
                <div className="h-3 w-1/2 rounded bg-soft animate-pulse" />
              </div>
            )}
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

      {/* Composer */}
      <form onSubmit={handleSubmit} className="border-t border-line bg-surface p-3 shrink-0">
        <div className="flex items-end gap-2">
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={handleKeyDown}
            rows={2}
            disabled={streaming}
            placeholder={`Đặt câu hỏi phân tích ${primaryTicker} (Enter để gửi, Shift+Enter xuống dòng)…`}
            aria-label="Nhập câu hỏi phân tích doanh nghiệp"
            className="flex-1 resize-none rounded-[8px] border border-line bg-paper px-3 py-2 text-[13px] text-ink placeholder:text-muted focus:border-mineral focus:outline-none disabled:opacity-60"
          />
          {streaming ? (
            <Button
              type="button"
              variant="secondary"
              onClick={onStopStream}
              className="!h-10 !border-loss/40 !text-loss hover:!bg-loss/10"
            >
              <Square className="h-3.5 w-3.5 fill-current" aria-hidden="true" />
              <span>Dừng</span>
            </Button>
          ) : (
            <Button
              type="submit"
              variant="primary"
              disabled={!draft.trim()}
              className="!h-10 px-4"
            >
              <Send className="h-3.5 w-3.5" aria-hidden="true" />
              <span>Phân tích</span>
            </Button>
          )}
        </div>
      </form>
    </Panel>
  )
}
