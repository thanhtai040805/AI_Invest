"use client"

import { useEffect, useMemo, useState } from "react"
import {
  AlertCircle,
  BarChart3,
  Brain,
  Calculator,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  ClipboardCheck,
  Compass,
  FileSearch,
  FileSpreadsheet,
  FileText,
  Filter,
  GitCompareArrows,
  History,
  Loader2,
  Network,
  SearchCheck,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Table2,
  Wrench,
} from "lucide-react"
import type {
  ResearchActivityItem,
  SelectedDocumentPreview,
} from "@/lib/api/researchChat"

interface ResearchActivityTimelineProps {
  items: ResearchActivityItem[]
  isStreaming?: boolean
  hasAnswerStarted?: boolean
  activeStageLabel?: string | null
  startedAtMs?: number | null
  thinkingDurationMs?: number | null
  latencyMs?: number | null
  reasoningTokens?: number | null
  agentTurns?: number | null
  defaultExpanded?: boolean
  onInspectCitation?: (doc: SelectedDocumentPreview) => void
  onInspectTable?: (tableId: string) => void
  onInspectCalc?: (calcId: string) => void
  onInspectGraph?: () => void
}

function formatDuration(ms?: number | null): string {
  if (ms == null || !Number.isFinite(ms) || ms < 0) return ""
  if (ms < 1000) return `${Math.round(ms)}ms`
  return `${(ms / 1000).toFixed(1).replace(".", ",")} giây`
}

function formatShortDuration(ms?: number | null): string {
  if (ms == null || !Number.isFinite(ms) || ms < 0) return ""
  if (ms < 1000) return `${Math.round(ms)}ms`
  return `${(ms / 1000).toFixed(1).replace(".", ",")}s`
}

function formatPct(val?: number | null): string {
  if (val == null || !Number.isFinite(val)) return "—"
  return `${(val * 100).toFixed(1)}%`
}

function toolIconFor(tool?: string) {
  switch (tool) {
    case "search_disclosures":
    case "search_corporate_filings":
      return FileSearch
    case "get_financial_statements":
      return FileSpreadsheet
    case "get_sector_kpis":
      return BarChart3
    case "scan_financial_risks":
      return ShieldAlert
    case "compare_peers":
      return GitCompareArrows
    case "check_plan_vs_actual":
      return ClipboardCheck
    case "hydrate_financial_tables":
      return Table2
    case "query_knowledge_graph":
      return Network
    case "run_financial_calculator":
      return Calculator
    case "screen_financial_universe":
      return Filter
    case "recall_session_memory":
      return History
    default:
      return Sparkles
  }
}

function cragBadgeClass(grade?: string | null): string {
  const g = String(grade ?? "").toUpperCase()
  if (g === "CORRECT") return "border-teal/35 bg-teal/10 text-teal"
  if (g === "AMBIGUOUS") return "border-warning/40 bg-warning/12 text-warning"
  if (g === "INCORRECT") return "border-loss/40 bg-loss/10 text-loss"
  return "border-line bg-soft text-secondary"
}

function verdictStatusBadge(status?: string | null): { label: string; cls: string } {
  const s = String(status ?? "").toUpperCase()
  if (s === "CONFIRMED") {
    return { label: "ĐÃ XÁC MINH", cls: "border-teal/35 bg-teal/10 text-teal" }
  }
  if (s === "REFUTED") {
    return { label: "BÁC BỎ TIỀN ĐỀ", cls: "border-loss/40 bg-loss/10 text-loss" }
  }
  if (s === "PARTIAL") {
    return { label: "MỘT PHẦN", cls: "border-warning/40 bg-warning/12 text-warning" }
  }
  if (s === "NO_DATA") {
    return { label: "THIẾU DỮ LIỆU PIT", cls: "border-line bg-soft text-secondary" }
  }
  return { label: s || "KẾT LUẬN", cls: "border-line bg-soft text-secondary" }
}

export function ResearchActivityTimeline({
  items,
  isStreaming = false,
  hasAnswerStarted = false,
  activeStageLabel = null,
  startedAtMs = null,
  thinkingDurationMs = null,
  latencyMs = null,
  reasoningTokens = null,
  agentTurns = null,
  defaultExpanded = false,
  onInspectCitation,
  onInspectTable,
  onInspectCalc,
  onInspectGraph,
}: ResearchActivityTimelineProps) {
  const [userExpanded, setUserExpanded] = useState<boolean | null>(null)
  const [liveElapsedMs, setLiveElapsedMs] = useState(0)

  const isActivelyThinking = isStreaming && !hasAnswerStarted

  useEffect(() => {
    if (!isActivelyThinking || !startedAtMs) {
      return
    }
    const tick = () => setLiveElapsedMs(Math.max(0, Date.now() - startedAtMs))
    tick()
    const timer = window.setInterval(tick, 200)
    return () => window.clearInterval(timer)
  }, [isActivelyThinking, startedAtMs])

  // ChatGPT behavior: auto-open while actively thinking, auto-collapse once answer text starts streaming
  // (unless the user explicitly clicked to toggle it open/closed).
  const autoExpanded = isActivelyThinking ? true : defaultExpanded
  const expanded = userExpanded ?? autoExpanded

  const stats = useMemo(() => {
    const toolItems = items.filter((i) => i.type === "tool_call")
    const runningTool = [...toolItems].reverse().find((i) => i.status === "running")
    const latestItem = items.length > 0 ? items[items.length - 1] : null
    const docKeys = new Set<string>()
    let tablesCount = 0
    let calcsCount = 0
    let hopsCount = 0
    let verdictCount = 0
    let hasReasoningText = false
    let maxStepMs = 0
    let lastReasoningId: string | null = null

    for (const item of items) {
      const stepTime = item.completed_at_ms ?? item.timestamp_ms ?? 0
      if (stepTime > maxStepMs) maxStepMs = stepTime
      if (item.kind === "reasoning") {
        lastReasoningId = item.id
        if (item.text?.trim()) hasReasoningText = true
      }
      if (item.kind === "verdict_card") {
        verdictCount += 1
      }
      if (Array.isArray(item.selected_documents)) {
        for (const d of item.selected_documents) {
          docKeys.add(`${d.citation_id}-${d.ticker}-${d.sub_doc_slot}-${d.page}`)
        }
      }
      if (Array.isArray(item.hydrated_tables)) {
        tablesCount += item.hydrated_tables.length
      }
      if (item.calc_result?.calc_id) {
        calcsCount += 1
      }
      if (Array.isArray(item.kg_hops)) {
        hopsCount += item.kg_hops.length
      }
    }

    return {
      stepsTotal: items.length,
      toolsTotal: toolItems.length,
      runningTool,
      latestItem,
      lastReasoningId,
      docsSelected: docKeys.size,
      tablesCount,
      calcsCount,
      hopsCount,
      verdictCount,
      hasReasoningText,
      maxStepMs,
      turnsCount: agentTurns ?? 0,
    }
  }, [items, agentTurns])

  if (items.length === 0 && !isStreaming) return null

  // `liveElapsedMs` freezes automatically as soon as `hasAnswerStarted` flips `isActivelyThinking` false
  const frozenStreamThinkingMs = isStreaming && liveElapsedMs > 0 ? liveElapsedMs : null
  const resolvedThinkingMs = isActivelyThinking
    ? startedAtMs
      ? liveElapsedMs
      : 0
    : thinkingDurationMs ??
      frozenStreamThinkingMs ??
      (stats.maxStepMs > 0 ? stats.maxStepMs : latencyMs)

  const liveSubtitle = stats.runningTool
    ? stats.runningTool.label || stats.runningTool.tool || ""
    : stats.latestItem?.title || activeStageLabel || "Khởi tạo đề bài & kiểm tra Point-in-Time"

  const summaryBits: string[] = []
  if (stats.stepsTotal > 0) summaryBits.push(`${stats.stepsTotal} bước`)
  if (stats.docsSelected > 0) summaryBits.push(`${stats.docsSelected} tài liệu`)
  if (stats.tablesCount > 0) summaryBits.push(`${stats.tablesCount} bảng 2D`)
  if (stats.calcsCount > 0) summaryBits.push(`${stats.calcsCount} phép tính`)
  if (stats.hopsCount > 0) summaryBits.push(`${stats.hopsCount} KG`)
  if (reasoningTokens != null && reasoningTokens > 0) {
    summaryBits.push(`${reasoningTokens.toLocaleString("vi-VN")} tok suy luận`)
  }

  const completedLabel = resolvedThinkingMs
    ? `Đã suy luận trong ${formatDuration(resolvedThinkingMs)}`
    : "Quá trình suy luận & kiểm chứng"

  return (
    <div className="mb-3.5">
      {/* ChatGPT-Style Minimalist Reasoning Toggle */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <button
          type="button"
          onClick={() => setUserExpanded((prev) => !(prev ?? autoExpanded))}
          aria-expanded={expanded}
          className="group inline-flex items-center gap-1.5 rounded-[8px] px-2 py-1 -ml-2 text-left transition-colors hover:bg-soft/80 cursor-pointer max-w-full"
        >
          {isActivelyThinking ? (
            <Loader2 className="h-3.5 w-3.5 text-mineral animate-spin shrink-0" aria-hidden="true" />
          ) : (
            <Brain
              className="h-3.5 w-3.5 text-muted group-hover:text-mineral transition-colors shrink-0"
              aria-hidden="true"
            />
          )}

          {isActivelyThinking ? (
            <span className="inline-flex items-center gap-1.5 min-w-0 text-[13px]">
              <span className="font-semibold text-ink animate-pulse shrink-0">Đang suy luận…</span>
              {liveSubtitle && (
                <span className="text-[12px] text-secondary truncate">· {liveSubtitle}</span>
              )}
              {startedAtMs ? (
                <span className="font-mono text-[11.5px] text-muted tnum shrink-0">
                  ({formatShortDuration(liveElapsedMs)})
                </span>
              ) : null}
            </span>
          ) : (
            <span className="inline-flex items-center gap-1.5 min-w-0">
              <span className="text-[13px] font-medium text-secondary group-hover:text-ink transition-colors">
                {completedLabel}
              </span>
              {summaryBits.length > 0 && (
                <span className="hidden sm:inline text-[11.5px] text-muted truncate">
                  · {summaryBits.join(" · ")}
                </span>
              )}
            </span>
          )}

          {expanded ? (
            <ChevronDown className="h-3.5 w-3.5 text-muted group-hover:text-ink shrink-0" aria-hidden="true" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5 text-muted group-hover:text-ink shrink-0" aria-hidden="true" />
          )}
        </button>

        {/* Post-answer Stage 7 audit micro-indicator while finalizing */}
        {isStreaming && hasAnswerStarted && (
          <span className="inline-flex items-center gap-1.5 rounded-full border border-mineral/30 bg-mineral/8 px-2.5 py-0.5 text-[11px] font-medium text-mineral">
            <Loader2 className="h-3 w-3 animate-spin shrink-0" aria-hidden="true" />
            <span>{activeStageLabel || "Đang viết & kiểm chứng Stage 7…"}</span>
          </span>
        )}
      </div>

      {/* ChatGPT-Style Clean Left-Hairline Step & Thinking Stream */}
      {expanded && (
        <div className="mt-1.5 ml-1.5 border-l-2 border-line/85 pl-4 py-1 space-y-3">
          {items.length === 0 && isStreaming && (
            <div className="flex items-center gap-2 py-1 text-[12.5px] text-secondary">
              <Sparkles className="h-3.5 w-3.5 text-mineral animate-pulse shrink-0" aria-hidden="true" />
              <span>Đang phân tích câu hỏi và khóa sổ dữ liệu Point-in-Time…</span>
            </div>
          )}

          <ol className="space-y-3">
            {items.map((item, idx) => {
              const key = item.call_id || item.id || `act-${idx}`

              // 1) Model Native Reasoning Stream (`kind === "reasoning"`) — clean ChatGPT prose style
              if (item.type === "thought" && item.kind === "reasoning") {
                const showLiveCursor = isActivelyThinking && item.id === stats.lastReasoningId
                return (
                  <li key={key} className="relative">
                    <span className="absolute -left-[21px] top-1 h-2 w-2 rounded-full bg-mineral ring-3 ring-surface" />
                    <div className="space-y-1">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-[11.5px] font-semibold text-ink/85">
                          {item.title || "Suy luận phân tích"}
                        </span>
                        {item.timestamp_ms != null && (
                          <span className="font-mono text-[10.5px] text-muted tnum">
                            +{formatShortDuration(item.timestamp_ms)}
                          </span>
                        )}
                      </div>
                      {item.text && (
                        <div className="text-[12.5px] leading-[1.68] text-secondary whitespace-pre-wrap">
                          {item.text}
                          {showLiveCursor && (
                            <span
                              aria-hidden="true"
                              className="inline-block ml-0.5 h-3.5 w-1.5 translate-y-0.5 bg-mineral/75 animate-pulse"
                            />
                          )}
                        </div>
                      )}
                    </div>
                  </li>
                )
              }

              // 2) Claim / Premise Verdict Card (`kind === "verdict_card"`)
              if (item.type === "thought" && item.kind === "verdict_card") {
                const vBadge = verdictStatusBadge(item.verdict_status)
                const itemBadges = Array.isArray(item.badges) ? item.badges : []
                return (
                  <li key={key} className="relative">
                    <span className="absolute -left-[21px] top-1.5 h-2 w-2 rounded-full bg-teal ring-3 ring-surface" />
                    <div className="rounded-[8px] border border-line/80 bg-paper/60 px-3 py-2 space-y-1.5 text-[12px]">
                      <div className="flex flex-wrap items-center justify-between gap-1.5">
                        <div className="flex flex-wrap items-center gap-1.5 min-w-0">
                          <SearchCheck className="h-3.5 w-3.5 text-teal shrink-0" aria-hidden="true" />
                          <span className="font-semibold text-ink">
                            {item.title || "Thẻ kết luận kiểm chứng"}
                          </span>
                          <span
                            className={`rounded-full border px-1.5 py-[1px] font-mono text-[9.5px] font-semibold ${vBadge.cls}`}
                          >
                            {vBadge.label}
                          </span>
                          {item.period && (
                            <span className="rounded bg-surface px-1.5 py-[1px] font-mono text-[10px] text-muted border border-line">
                              {item.period}
                            </span>
                          )}
                          {item.escalated && (
                            <span className="rounded bg-mineral/10 px-1.5 py-[1px] font-mono text-[9.5px] font-semibold text-mineral border border-mineral/30">
                              1-Hop Escalated
                            </span>
                          )}
                        </div>
                        {item.timestamp_ms != null && (
                          <span className="font-mono text-[10px] text-muted tnum">
                            +{formatShortDuration(item.timestamp_ms)}
                          </span>
                        )}
                      </div>
                      {item.claim_statement && (
                        <div className="text-[11.5px] font-medium text-ink/85">
                          {item.claim_statement}
                        </div>
                      )}
                      {item.text && (
                        <div className="text-[11.5px] text-secondary leading-relaxed whitespace-pre-wrap">
                          {item.text}
                        </div>
                      )}
                      {itemBadges.length > 0 && (
                        <div className="flex flex-wrap gap-1 pt-0.5">
                          {itemBadges.map((rawBadge, bIdx) => {
                            const cleanRef = rawBadge.replace(/^\[|\]$/g, "")
                            const isCalc = cleanRef.startsWith("CALC-")
                            const isTbl = cleanRef.startsWith("TBL:")
                            return (
                              <button
                                key={`${key}-vb-${bIdx}`}
                                type="button"
                                onClick={() => {
                                  if (isCalc && onInspectCalc) onInspectCalc(cleanRef)
                                  else if (isTbl && onInspectTable) onInspectTable(cleanRef.slice(4))
                                }}
                                className={`rounded border border-line bg-surface px-1.5 py-[1px] font-mono text-[10px] text-secondary ${
                                  (isCalc && onInspectCalc) || (isTbl && onInspectTable)
                                    ? "hover:border-mineral hover:text-mineral cursor-pointer"
                                    : "cursor-default"
                                }`}
                              >
                                {rawBadge}
                              </button>
                            )
                          })}
                        </div>
                      )}
                    </div>
                  </li>
                )
              }

              // 3) Routing, Plan, Escalation, Stage 7 Verification & Surgical Repair
              if (
                item.type === "routing" ||
                item.type === "verification" ||
                item.type === "thought"
              ) {
                const isVer = item.type === "verification"
                const isRepair = item.kind === "verification_repair"
                const isRouting = item.type === "routing"
                const Icon = isVer
                  ? ShieldCheck
                  : isRepair
                    ? Wrench
                    : isRouting
                      ? Compass
                      : Sparkles
                const dotColor = isVer
                  ? "bg-teal"
                  : isRepair
                    ? "bg-warning"
                    : "bg-mineral"
                const screenRows = Array.isArray(item.screening_preview)
                  ? item.screening_preview
                  : []
                return (
                  <li key={key} className="relative">
                    <span
                      className={`absolute -left-[21px] top-1.5 h-2 w-2 rounded-full ${dotColor} ring-3 ring-surface`}
                    />
                    <div className="space-y-1 text-[12px]">
                      <div className="flex flex-wrap items-center justify-between gap-1.5">
                        <div className="inline-flex items-center gap-1.5 font-semibold text-ink">
                          <Icon
                            className={`h-3.5 w-3.5 shrink-0 ${
                              isVer ? "text-teal" : isRepair ? "text-warning" : "text-mineral"
                            }`}
                            aria-hidden="true"
                          />
                          <span>
                            {item.title ||
                              (isVer ? "Kiểm chứng Stage 7" : isRouting ? "Định tuyến" : "Bước lập kế hoạch")}
                          </span>
                        </div>
                        {item.timestamp_ms != null && (
                          <span className="font-mono text-[10px] text-muted tnum">
                            +{formatShortDuration(item.timestamp_ms)}
                          </span>
                        )}
                      </div>
                      {item.text && (
                        <div className="text-[12px] text-secondary leading-relaxed whitespace-pre-wrap">
                          {item.text}
                        </div>
                      )}
                      {screenRows.length > 0 && (
                        <div className="pt-0.5 flex flex-wrap gap-1">
                          {screenRows.map((r, rIdx) => (
                            <span
                              key={`${key}-loc-scr-${r.ticker}-${rIdx}`}
                              className="inline-flex items-center gap-1 rounded-[5px] border border-line bg-paper px-1.5 py-0.5 font-mono text-[10px] text-secondary"
                            >
                              <strong className="text-ink">{r.ticker}</strong>
                              {r.pe != null && <span>P/E {r.pe.toFixed(1)}x</span>}
                              {r.pb != null && <span>P/B {r.pb.toFixed(2)}x</span>}
                              {r.roe != null && <span>ROE {formatPct(r.roe)}</span>}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  </li>
                )
              }

              // 4) Tool Call / Quant Engine / Disclosure Search Step
              const ToolIcon = toolIconFor(item.tool)
              const isRunning = item.status === "running"
              const isError = item.status === "error"
              const selectedDocs = Array.isArray(item.selected_documents)
                ? item.selected_documents
                : []
              const tables = Array.isArray(item.hydrated_tables) ? item.hydrated_tables : []
              const hops = Array.isArray(item.kg_hops) ? item.kg_hops : []
              const screenRows = Array.isArray(item.screening_preview)
                ? item.screening_preview
                : []
              const calc = item.calc_result

              return (
                <li key={key} className="relative">
                  <span
                    className={`absolute -left-[22px] top-1 flex h-3 w-3 items-center justify-center rounded-full bg-surface ${
                      isRunning ? "text-mineral" : isError ? "text-loss" : "text-teal"
                    }`}
                  >
                    {isRunning ? (
                      <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
                    ) : isError ? (
                      <AlertCircle className="h-3 w-3" aria-hidden="true" />
                    ) : (
                      <CheckCircle2 className="h-3 w-3" aria-hidden="true" />
                    )}
                  </span>

                  <div
                    className={`rounded-[8px] border px-3 py-2 space-y-1.5 text-[11.5px] ${
                      isRunning
                        ? "border-mineral/40 bg-paper/90"
                        : isError
                          ? "border-loss/35 bg-loss/5"
                          : "border-line/80 bg-paper/55"
                    }`}
                  >
                    <div className="flex flex-wrap items-center justify-between gap-1.5">
                      <div className="flex flex-wrap items-center gap-1.5 min-w-0">
                        <ToolIcon className="h-3.5 w-3.5 text-mineral shrink-0" aria-hidden="true" />
                        <span className="font-semibold text-ink">
                          {item.label || item.tool}
                        </span>
                        {Array.isArray(item.tickers) && item.tickers.length > 0 && (
                          <span className="rounded bg-surface px-1.5 py-[1px] font-mono text-[10px] font-semibold text-ink border border-line">
                            {item.tickers.slice(0, 6).join(", ")}
                          </span>
                        )}
                        {item.crag_grade && (
                          <span
                            className={`rounded-full border px-1.5 py-[1px] font-mono text-[9.5px] font-semibold ${cragBadgeClass(
                              item.crag_grade,
                            )}`}
                          >
                            CRAG {item.crag_grade}
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-1.5 font-mono text-[10px] text-muted tnum shrink-0">
                        {isRunning ? (
                          <span className="text-mineral font-medium">Đang thực thi…</span>
                        ) : (
                          item.duration_ms != null && <span>{formatShortDuration(item.duration_ms)}</span>
                        )}
                      </div>
                    </div>

                    {item.args_preview && (
                      <div className="text-[11px] text-secondary">{item.args_preview}</div>
                    )}
                    {Array.isArray(item.queries) && item.queries.length > 0 && (
                      <div className="flex flex-wrap gap-1">
                        {item.queries.map((q, qIdx) => (
                          <span
                            key={`${key}-q-${qIdx}`}
                            className="inline-flex items-center rounded bg-surface border border-line px-1.5 py-0.5 font-mono text-[10px] text-secondary max-w-full truncate"
                            title={q}
                          >
                            “{q.length > 64 ? `${q.slice(0, 64)}…` : q}”
                          </span>
                        ))}
                      </div>
                    )}

                    {item.summary && (
                      <div className={`text-[11px] font-medium ${isError ? "text-loss" : "text-teal"}`}>
                        {item.summary}
                      </div>
                    )}

                    {selectedDocs.length > 0 && (
                      <div className="pt-0.5 flex flex-wrap gap-1">
                        {selectedDocs.map((doc, dIdx) => (
                          <button
                            key={`${key}-doc-${doc.citation_id}-${dIdx}`}
                            type="button"
                            disabled={!onInspectCitation}
                            onClick={() => onInspectCitation?.(doc)}
                            title={doc.heading || `${doc.ticker} ${doc.sub_doc_slot} trang ${doc.page}`}
                            className={`inline-flex items-center gap-1 rounded-[5px] border border-line bg-surface px-1.5 py-0.5 text-[10.5px] text-ink transition-colors ${
                              onInspectCitation
                                ? "hover:border-mineral/55 hover:bg-mineral/8 cursor-pointer"
                                : "cursor-default"
                            }`}
                          >
                            <FileText className="h-2.5 w-2.5 text-mineral shrink-0" aria-hidden="true" />
                            <span className="font-mono font-semibold text-mineral">
                              [{doc.citation_id}]
                            </span>
                            <span className="font-mono font-medium">{doc.ticker}</span>
                            <span className="text-secondary">{doc.sub_doc_slot}</span>
                            <span className="font-mono text-muted">tr.{doc.page}</span>
                            {doc.evidence_origin === "CROSS_REF" && (
                              <span className="rounded bg-warning/12 px-1 text-[9px] font-mono text-warning">
                                XREF
                              </span>
                            )}
                          </button>
                        ))}
                      </div>
                    )}

                    {tables.length > 0 && (
                      <div className="pt-0.5 flex flex-wrap gap-1">
                        {tables.map((t, tIdx) => {
                          const isVerified = t.verification_status === "VERIFIED"
                          return (
                            <button
                              key={`${key}-tbl-${t.table_id}-${tIdx}`}
                              type="button"
                              disabled={!onInspectTable}
                              onClick={() => onInspectTable?.(t.table_id)}
                              className={`inline-flex items-center gap-1 rounded-[5px] border px-1.5 py-0.5 text-[10.5px] transition-colors ${
                                isVerified
                                  ? "border-teal/35 bg-teal/6 text-ink"
                                  : "border-warning/35 bg-warning/6 text-ink"
                              } ${onInspectTable ? "hover:border-mineral cursor-pointer" : "cursor-default"}`}
                            >
                              <Table2 className="h-2.5 w-2.5 text-teal shrink-0" aria-hidden="true" />
                              <span className="truncate max-w-[220px]">{t.title}</span>
                              {t.rows_count != null && (
                                <span className="font-mono text-[9.5px] text-muted">
                                  ({t.rows_count} dòng)
                                </span>
                              )}
                              <span
                                className={`font-mono text-[9px] font-semibold ${
                                  isVerified ? "text-teal" : "text-warning"
                                }`}
                              >
                                {isVerified ? "VERIFIED" : "UNVERIFIED"}
                              </span>
                            </button>
                          )
                        })}
                      </div>
                    )}

                    {calc && (
                      <button
                        type="button"
                        disabled={!onInspectCalc}
                        onClick={() => onInspectCalc?.(calc.calc_id)}
                        className={`mt-0.5 w-full flex flex-wrap items-center justify-between gap-2 rounded-[5px] border border-teal/30 bg-teal/6 px-2 py-1 text-left ${
                          onInspectCalc ? "hover:border-teal cursor-pointer" : "cursor-default"
                        }`}
                      >
                        <span className="font-mono text-[10.5px] text-secondary truncate">
                          <strong className="text-teal">[{calc.calc_id}]</strong>{" "}
                          {calc.description || calc.formula}
                        </span>
                        <span className="font-mono text-[11px] font-semibold text-ink tnum">
                          = {calc.formatted}
                        </span>
                      </button>
                    )}

                    {hops.length > 0 && (
                      <div className="pt-0.5 flex flex-wrap gap-1">
                        {hops.slice(0, 5).map((h, hIdx) => (
                          <button
                            key={`${key}-hop-${hIdx}`}
                            type="button"
                            disabled={!onInspectGraph}
                            onClick={() => onInspectGraph?.()}
                            className={`inline-flex items-center gap-1 rounded-[5px] border border-line bg-surface px-1.5 py-0.5 font-mono text-[10px] text-secondary ${
                              onInspectGraph
                                ? "hover:border-mineral hover:text-ink cursor-pointer"
                                : "cursor-default"
                            }`}
                          >
                            <span className="font-semibold text-mineral">{h.layer}</span>
                            <span>
                              {h.src_label} → {h.dst_label}
                            </span>
                            {h.beta != null && Number.isFinite(h.beta) && (
                              <span className="text-ink">β={h.beta.toFixed(2)}</span>
                            )}
                          </button>
                        ))}
                      </div>
                    )}

                    {screenRows.length > 0 && (
                      <div className="pt-0.5 flex flex-wrap gap-1">
                        {screenRows.slice(0, 5).map((r, rIdx) => (
                          <span
                            key={`${key}-scr-${r.ticker}-${rIdx}`}
                            className="inline-flex items-center gap-1 rounded-[5px] border border-line bg-surface px-1.5 py-0.5 font-mono text-[10px] text-secondary"
                          >
                            <strong className="text-ink">{r.ticker}</strong>
                            {r.pe != null && <span>P/E {r.pe.toFixed(1)}x</span>}
                            {r.roe != null && <span>ROE {formatPct(r.roe)}</span>}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                </li>
              )
            })}
          </ol>
        </div>
      )}
    </div>
  )
}
