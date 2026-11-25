"use client"

import { useEffect, useMemo, useState } from "react"
import {
  AlertCircle,
  Brain,
  Calculator,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Compass,
  FileSearch,
  FileText,
  Filter,
  History,
  Loader2,
  Network,
  ShieldCheck,
  Sparkles,
  Table2,
} from "lucide-react"
import type {
  ResearchActivityItem,
  SelectedDocumentPreview,
} from "@/lib/api/researchChat"

interface ResearchActivityTimelineProps {
  items: ResearchActivityItem[]
  isStreaming?: boolean
  startedAtMs?: number | null
  latencyMs?: number | null
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
  return `${(ms / 1000).toFixed(1).replace(".", ",")}s`
}

function formatPct(val?: number | null): string {
  if (val == null || !Number.isFinite(val)) return "—"
  return `${(val * 100).toFixed(1)}%`
}

function toolIconFor(tool?: string) {
  switch (tool) {
    case "search_corporate_filings":
      return FileSearch
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

export function ResearchActivityTimeline({
  items,
  isStreaming = false,
  startedAtMs = null,
  latencyMs = null,
  agentTurns = null,
  defaultExpanded = false,
  onInspectCitation,
  onInspectTable,
  onInspectCalc,
  onInspectGraph,
}: ResearchActivityTimelineProps) {
  const [userExpanded, setUserExpanded] = useState<boolean | null>(null)
  const [liveElapsedMs, setLiveElapsedMs] = useState(0)

  const expanded = userExpanded ?? (defaultExpanded || isStreaming)

  useEffect(() => {
    if (!isStreaming || !startedAtMs) {
      return
    }
    const timer = window.setInterval(() => {
      setLiveElapsedMs(Math.max(0, Date.now() - startedAtMs))
    }, 250)
    return () => window.clearInterval(timer)
  }, [isStreaming, startedAtMs])

  const stats = useMemo(() => {
    const toolItems = items.filter((i) => i.type === "tool_call")
    const runningTool = [...toolItems].reverse().find((i) => i.status === "running")
    const docKeys = new Set<string>()
    let tablesCount = 0
    let calcsCount = 0
    let hopsCount = 0
    let maxTurn = agentTurns ?? 0

    for (const item of items) {
      if (typeof item.turn === "number" && item.turn > maxTurn) {
        maxTurn = item.turn
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
      toolsTotal: toolItems.length,
      runningTool,
      docsSelected: docKeys.size,
      tablesCount,
      calcsCount,
      hopsCount,
      turnsCount: maxTurn,
    }
  }, [items, agentTurns])

  if (items.length === 0 && !isStreaming) return null

  const displayTime = isStreaming
    ? formatDuration(startedAtMs ? liveElapsedMs : 0)
    : formatDuration(
        latencyMs ??
          items.reduce((acc, it) => Math.max(acc, it.completed_at_ms ?? it.timestamp_ms ?? 0), 0),
      )

  const headerTitle = isStreaming
    ? stats.runningTool
      ? `Đang chạy: ${stats.runningTool.label || stats.runningTool.tool}…`
      : "Đang suy luận & chọn lọc tài liệu PIT…"
    : "Quá trình suy luận, gọi công cụ & chọn lọc tài liệu"

  const summaryBits: string[] = []
  if (stats.turnsCount > 0) summaryBits.push(`${stats.turnsCount} lượt`)
  if (stats.toolsTotal > 0) summaryBits.push(`${stats.toolsTotal} công cụ`)
  if (stats.docsSelected > 0) summaryBits.push(`${stats.docsSelected} tài liệu`)
  if (stats.tablesCount > 0) summaryBits.push(`${stats.tablesCount} bảng 2D`)
  if (stats.calcsCount > 0) summaryBits.push(`${stats.calcsCount} phép tính`)
  if (stats.hopsCount > 0) summaryBits.push(`${stats.hopsCount} liên kết KG`)

  return (
    <div
      className={`mb-3.5 rounded-[10px] border transition-colors ${
        isStreaming
          ? "border-mineral/45 bg-paper/90 shadow-xs"
          : "border-line bg-paper/65"
      }`}
    >
      {/* Collapsible Accordion Trigger */}
      <button
        type="button"
        onClick={() => setUserExpanded((prev) => !(prev ?? (defaultExpanded || isStreaming)))}
        aria-expanded={expanded}
        className="w-full flex items-center justify-between gap-2 px-3 py-2 text-left hover:bg-soft/55 rounded-[10px] transition-colors cursor-pointer"
      >
        <div className="flex items-center gap-2 min-w-0 flex-1">
          {isStreaming ? (
            <Loader2 className="h-3.5 w-3.5 text-mineral animate-spin shrink-0" aria-hidden="true" />
          ) : (
            <Brain className="h-3.5 w-3.5 text-mineral shrink-0" aria-hidden="true" />
          )}
          <span className="text-[12px] font-semibold text-ink truncate">
            {headerTitle}
          </span>
          {summaryBits.length > 0 && (
            <span className="hidden sm:inline-flex items-center text-[11px] font-mono text-secondary truncate">
              · {summaryBits.join(" · ")}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {displayTime && (
            <span className="rounded-full border border-line bg-surface px-2 py-[1px] font-mono text-[10.5px] text-secondary tnum">
              {displayTime}
            </span>
          )}
          {expanded ? (
            <ChevronDown className="h-3.5 w-3.5 text-muted" aria-hidden="true" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5 text-muted" aria-hidden="true" />
          )}
        </div>
      </button>

      {/* Live / Persisted Step-by-Step Timeline */}
      {expanded && (
        <div className="px-3 pb-3 pt-1 border-t border-line/70 space-y-2.5">
          {items.length === 0 && isStreaming && (
            <div className="flex items-center gap-2 py-1.5 text-[12px] text-secondary">
              <Sparkles className="h-3.5 w-3.5 text-mineral animate-pulse shrink-0" aria-hidden="true" />
              <span>Đang khởi tạo bộ định tuyến Tier-0 và kiểm tra phạm vi Point-in-Time…</span>
            </div>
          )}

          <ol className="relative space-y-2.5 pl-4 before:content-[''] before:absolute before:left-[6px] before:top-2 before:bottom-2 before:w-[1.5px] before:bg-line">
            {items.map((item, idx) => {
              const key = item.id || item.call_id || `act-${idx}`

              if (item.type === "routing" || item.type === "verification") {
                const isVer = item.type === "verification"
                const Icon = isVer ? ShieldCheck : Compass
                return (
                  <li key={key} className="relative">
                    <span
                      className={`absolute -left-4 top-0.5 flex h-3.5 w-3.5 items-center justify-center rounded-full border bg-surface ${
                        isVer ? "border-teal/50 text-teal" : "border-mineral/50 text-mineral"
                      }`}
                    >
                      <Icon className="h-2.5 w-2.5" aria-hidden="true" />
                    </span>
                    <div className="rounded-[7px] border border-line/80 bg-surface px-2.5 py-1.5 text-[11.5px]">
                      <div className="flex flex-wrap items-center justify-between gap-1.5">
                        <span className="font-semibold text-ink">
                          {item.title || (isVer ? "Kiểm chứng Stage 7" : "Định tuyến")}
                        </span>
                        {item.timestamp_ms != null && (
                          <span className="font-mono text-[10px] text-muted tnum">
                            +{formatDuration(item.timestamp_ms)}
                          </span>
                        )}
                      </div>
                      {item.text && (
                        <p className="mt-0.5 text-[11.5px] text-secondary leading-snug">
                          {item.text}
                        </p>
                      )}
                    </div>
                  </li>
                )
              }

              if (item.type === "thought") {
                const isReasoning = item.kind === "reasoning"
                return (
                  <li key={key} className="relative">
                    <span className="absolute -left-4 top-0.5 flex h-3.5 w-3.5 items-center justify-center rounded-full border border-mineral/45 bg-surface text-mineral">
                      {isReasoning ? (
                        <Brain className="h-2.5 w-2.5" aria-hidden="true" />
                      ) : (
                        <Sparkles className="h-2.5 w-2.5" aria-hidden="true" />
                      )}
                    </span>
                    <div
                      className={`rounded-[7px] border px-2.5 py-1.5 text-[11.5px] ${
                        isReasoning
                          ? "border-mineral/30 bg-mineral/5"
                          : "border-line/70 bg-surface/85"
                      }`}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-medium text-ink">
                          {item.title || "Suy luận"}
                        </span>
                        {item.timestamp_ms != null && (
                          <span className="font-mono text-[10px] text-muted tnum">
                            +{formatDuration(item.timestamp_ms)}
                          </span>
                        )}
                      </div>
                      {item.text && (
                        <p
                          className={`mt-1 leading-relaxed whitespace-pre-wrap max-h-36 overflow-y-auto ${
                            isReasoning ? "italic text-secondary" : "text-secondary"
                          }`}
                        >
                          {item.text}
                        </p>
                      )}
                    </div>
                  </li>
                )
              }

              // Tool Call + Document Selection Card
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
                    className={`absolute -left-4 top-0.5 flex h-3.5 w-3.5 items-center justify-center rounded-full border bg-surface ${
                      isRunning
                        ? "border-mineral text-mineral"
                        : isError
                          ? "border-loss text-loss"
                          : "border-teal text-teal"
                    }`}
                  >
                    {isRunning ? (
                      <Loader2 className="h-2.5 w-2.5 animate-spin" aria-hidden="true" />
                    ) : isError ? (
                      <AlertCircle className="h-2.5 w-2.5" aria-hidden="true" />
                    ) : (
                      <CheckCircle2 className="h-2.5 w-2.5" aria-hidden="true" />
                    )}
                  </span>

                  <div
                    className={`rounded-[8px] border bg-surface px-2.5 py-2 space-y-1.5 text-[11.5px] ${
                      isRunning
                        ? "border-mineral/45"
                        : isError
                          ? "border-loss/40 bg-loss/5"
                          : "border-line"
                    }`}
                  >
                    {/* Tool Header Row */}
                    <div className="flex flex-wrap items-center justify-between gap-1.5">
                      <div className="flex items-center gap-1.5 min-w-0">
                        <ToolIcon className="h-3.5 w-3.5 text-mineral shrink-0" aria-hidden="true" />
                        <span className="font-semibold text-ink truncate">
                          {item.label || item.tool}
                        </span>
                        {Array.isArray(item.tickers) && item.tickers.length > 0 && (
                          <span className="rounded bg-soft px-1.5 py-[1px] font-mono text-[10px] font-semibold text-ink border border-line">
                            {item.tickers.join(", ")}
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
                          <span className="text-mineral font-medium">Đang truy xuất…</span>
                        ) : (
                          item.duration_ms != null && <span>{formatDuration(item.duration_ms)}</span>
                        )}
                      </div>
                    </div>

                    {/* Args Preview / Queries */}
                    {item.args_preview && (
                      <div className="text-[11px] text-secondary">{item.args_preview}</div>
                    )}
                    {Array.isArray(item.queries) && item.queries.length > 0 && (
                      <div className="flex flex-wrap gap-1">
                        {item.queries.map((q, qIdx) => (
                          <span
                            key={`${key}-q-${qIdx}`}
                            className="inline-flex items-center rounded bg-paper border border-line px-1.5 py-0.5 font-mono text-[10px] text-secondary max-w-full truncate"
                            title={q}
                          >
                            “{q.length > 56 ? `${q.slice(0, 56)}…` : q}”
                          </span>
                        ))}
                      </div>
                    )}

                    {/* Outcome Summary */}
                    {item.summary && (
                      <div
                        className={`text-[11px] font-medium ${
                          isError ? "text-loss" : "text-teal"
                        }`}
                      >
                        {item.summary}
                      </div>
                    )}

                    {/* Selected Documents / Filing Filter Feed */}
                    {selectedDocs.length > 0 && (
                      <div className="pt-1 space-y-1">
                        <div className="text-[10px] font-semibold uppercase tracking-[0.05em] text-muted">
                          Tài liệu đã chọn lọc ({selectedDocs.length})
                        </div>
                        <div className="flex flex-wrap gap-1">
                          {selectedDocs.map((doc, dIdx) => (
                            <button
                              key={`${key}-doc-${doc.citation_id}-${dIdx}`}
                              type="button"
                              disabled={!onInspectCitation}
                              onClick={() => onInspectCitation?.(doc)}
                              title={doc.heading || `${doc.ticker} ${doc.sub_doc_slot} trang ${doc.page}`}
                              className={`inline-flex items-center gap-1 rounded-[5px] border border-line bg-paper px-1.5 py-0.5 text-[10.5px] text-ink transition-colors ${
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
                      </div>
                    )}

                    {/* Hydrated 2D Tables */}
                    {tables.length > 0 && (
                      <div className="pt-1 flex flex-wrap gap-1">
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
                              <span className="truncate max-w-[200px]">{t.title}</span>
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

                    {/* Python AST Calculator Result */}
                    {calc && (
                      <button
                        type="button"
                        disabled={!onInspectCalc}
                        onClick={() => onInspectCalc?.(calc.calc_id)}
                        className={`mt-1 w-full flex flex-wrap items-center justify-between gap-2 rounded-[5px] border border-teal/30 bg-teal/6 px-2 py-1 text-left ${
                          onInspectCalc ? "hover:border-teal cursor-pointer" : "cursor-default"
                        }`}
                      >
                        <span className="font-mono text-[10.5px] text-secondary truncate">
                          <strong className="text-teal">[{calc.calc_id}]</strong> {calc.formula}
                        </span>
                        <span className="font-mono text-[11px] font-semibold text-ink tnum">
                          = {calc.formatted}
                        </span>
                      </button>
                    )}

                    {/* Knowledge Graph Hops */}
                    {hops.length > 0 && (
                      <div className="pt-1 flex flex-wrap gap-1">
                        {hops.slice(0, 5).map((h, hIdx) => (
                          <button
                            key={`${key}-hop-${hIdx}`}
                            type="button"
                            disabled={!onInspectGraph}
                            onClick={() => onInspectGraph?.()}
                            className={`inline-flex items-center gap-1 rounded-[5px] border border-line bg-paper px-1.5 py-0.5 font-mono text-[10px] text-secondary ${
                              onInspectGraph ? "hover:border-mineral hover:text-ink cursor-pointer" : "cursor-default"
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

                    {/* Quantitative Screening Preview */}
                    {screenRows.length > 0 && (
                      <div className="pt-1 flex flex-wrap gap-1">
                        {screenRows.slice(0, 5).map((r, rIdx) => (
                          <span
                            key={`${key}-scr-${r.ticker}-${rIdx}`}
                            className="inline-flex items-center gap-1 rounded-[5px] border border-line bg-paper px-1.5 py-0.5 font-mono text-[10px] text-secondary"
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
