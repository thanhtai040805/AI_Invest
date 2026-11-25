"use client"

import { Activity, Brain, Calculator, FileText, Network, ShieldCheck, X } from "lucide-react"
import { Panel } from "@/components/ui"
import type { EvalSummaryResponse, ResearchChatMetadata } from "@/lib/api/researchChat"
import { InspectorEvalTab } from "./InspectorEvalTab"
import { InspectorGraphTab } from "./InspectorGraphTab"
import { InspectorMemoryTab } from "./InspectorMemoryTab"
import { InspectorQuotesTab } from "./InspectorQuotesTab"
import { InspectorTablesCalcTab } from "./InspectorTablesCalcTab"
import { InspectorTraceTab } from "./InspectorTraceTab"
import type { InspectorFocus, InspectorTabId } from "./types"

interface EvidenceInspectorProps {
  activeTab: InspectorTabId
  onTabChange: (tab: InspectorTabId) => void
  metadata: ResearchChatMetadata | null
  messageId: string | null
  activeSessionId: string | null
  memoryRefreshNonce?: number
  onSelectLinkedSession: (sessionId: string) => void
  focus: InspectorFocus | null
  evalSummary: EvalSummaryResponse | null
  onRefreshSummary: () => Promise<void> | void
  onClose?: () => void
  className?: string
}

export function EvidenceInspector({
  activeTab,
  onTabChange,
  metadata,
  messageId,
  activeSessionId,
  memoryRefreshNonce = 0,
  onSelectLinkedSession,
  focus,
  evalSummary,
  onRefreshSummary,
  onClose,
  className,
}: EvidenceInspectorProps) {
  const citations = Array.isArray(metadata?.citations) ? metadata.citations : []
  const tables = Array.isArray(metadata?.hydrated_tables) ? metadata.hydrated_tables : []
  const screeningResults = Array.isArray(metadata?.screening_results) ? metadata.screening_results : []
  const calculators = Array.isArray(metadata?.calculator_traces) ? metadata.calculator_traces : []
  const graphHops = Array.isArray(metadata?.graph_hops) ? metadata.graph_hops : []
  const expandedTickers = Array.isArray(metadata?.expanded_tickers) ? metadata.expanded_tickers : []
  const recalledMemories = Array.isArray(metadata?.recalled_memories) ? metadata.recalled_memories : []
  const linkedSessions = Array.isArray(metadata?.linked_sessions) ? metadata.linked_sessions : []
  const activeMemoryCount =
    recalledMemories.length + linkedSessions.length + (metadata?.portfolio_snapshot_used ? 1 : 0)

  const tabs: Array<{
    id: InspectorTabId
    label: string
    count?: number
    icon: typeof FileText
  }> = [
    { id: "quotes", label: "Trích dẫn gốc", count: citations.length, icon: FileText },
    {
      id: "tables",
      label: "Bảng & Calc",
      count: tables.length + calculators.length + (screeningResults.length > 0 ? 1 : 0),
      icon: Calculator,
    },
    { id: "graph", label: "Đồ thị 3 Tầng", count: graphHops.length, icon: Network },
    {
      id: "memory",
      label: "Ký ức",
      count: activeMemoryCount > 0 ? activeMemoryCount : undefined,
      icon: Brain,
    },
    { id: "trace", label: "Trace & Token", icon: Activity },
    { id: "eval", label: "Đánh giá G1–G5", icon: ShieldCheck },
  ]

  return (
    <Panel
      flush
      className={`flex flex-col h-full overflow-hidden ${className ?? "min-h-[680px] xl:h-[calc(100dvh-160px)]"}`}
    >
      {/* Header + 6 Tabs */}
      <div className="border-b border-line bg-surface px-4 pt-3 shrink-0">
        <div className="flex items-center justify-between gap-2 mb-2.5">
          <div className="flex items-center gap-2 min-w-0">
            <h2 className="text-[14px] font-semibold text-ink truncate">
              Evidence & Audit Inspector
            </h2>
            {metadata?.primary_ticker && (
              <span className="font-mono text-[11px] text-muted shrink-0">
                {metadata.primary_ticker} · {metadata.as_of_date || "PIT"}
              </span>
            )}
          </div>
          {onClose && (
            <button
              type="button"
              onClick={onClose}
              aria-label="Đóng ngăn bằng chứng"
              className="inline-flex h-7 w-7 items-center justify-center rounded-[6px] border border-line bg-paper text-secondary hover:text-ink hover:bg-soft transition-colors shrink-0"
            >
              <X className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
          )}
        </div>

        <div className="flex items-center gap-1 overflow-x-auto no-scrollbar -mb-px" role="tablist">
          {tabs.map((tab) => {
            const Icon = tab.icon
            const active = activeTab === tab.id
            return (
              <button
                key={tab.id}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => onTabChange(tab.id)}
                className={`inline-flex items-center gap-1.5 border-b-2 px-2.5 py-2 text-[12px] font-medium whitespace-nowrap transition-colors ${
                  active
                    ? "border-ink text-ink"
                    : "border-transparent text-secondary hover:text-ink"
                }`}
              >
                <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                <span>{tab.label}</span>
                {tab.count !== undefined && (
                  <span
                    className={`rounded-full px-1.5 py-[1px] font-mono text-[10px] tnum ${
                      active ? "bg-ink text-paper" : "bg-soft text-secondary"
                    }`}
                  >
                    {tab.count}
                  </span>
                )}
              </button>
            )
          })}
        </div>
      </div>

      {/* Tab Body */}
      <div className="flex-1 overflow-y-auto p-4 bg-paper/45">
        {activeTab === "quotes" && (
          <InspectorQuotesTab citations={citations} focus={focus} />
        )}
        {activeTab === "tables" && (
          <InspectorTablesCalcTab
            tables={tables}
            calculators={calculators}
            screeningResults={screeningResults}
            focus={focus}
          />
        )}
        {activeTab === "graph" && (
          <InspectorGraphTab
            graphHops={graphHops}
            expandedTickers={expandedTickers}
            focus={focus}
          />
        )}
        {activeTab === "memory" && (
          <InspectorMemoryTab
            metadata={metadata}
            activeSessionId={activeSessionId}
            messageId={messageId}
            refreshNonce={memoryRefreshNonce}
            focus={focus}
            onSelectLinkedSession={onSelectLinkedSession}
          />
        )}
        {activeTab === "trace" && (
          <InspectorTraceTab metadata={metadata ?? {}} />
        )}
        {activeTab === "eval" && (
          <InspectorEvalTab
            messageId={messageId}
            evalSummary={evalSummary}
            onRefreshSummary={onRefreshSummary}
          />
        )}
      </div>
    </Panel>
  )
}
