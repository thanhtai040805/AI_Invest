"use client"

import { Activity, Calculator, FileText, Network, ShieldCheck } from "lucide-react"
import { Panel } from "@/components/ui"
import type { EvalSummaryResponse, ResearchChatMetadata } from "@/lib/api/researchChat"
import { InspectorEvalTab } from "./InspectorEvalTab"
import { InspectorGraphTab } from "./InspectorGraphTab"
import { InspectorQuotesTab } from "./InspectorQuotesTab"
import { InspectorTablesCalcTab } from "./InspectorTablesCalcTab"
import { InspectorTraceTab } from "./InspectorTraceTab"
import type { InspectorFocus, InspectorTabId } from "./types"

interface EvidenceInspectorProps {
  activeTab: InspectorTabId
  onTabChange: (tab: InspectorTabId) => void
  metadata: ResearchChatMetadata | null
  messageId: string | null
  focus: InspectorFocus | null
  evalSummary: EvalSummaryResponse | null
  onRefreshSummary: () => Promise<void> | void
}

export function EvidenceInspector({
  activeTab,
  onTabChange,
  metadata,
  messageId,
  focus,
  evalSummary,
  onRefreshSummary,
}: EvidenceInspectorProps) {
  const citations = Array.isArray(metadata?.citations) ? metadata.citations : []
  const tables = Array.isArray(metadata?.hydrated_tables) ? metadata.hydrated_tables : []
  const calculators = Array.isArray(metadata?.calculator_traces) ? metadata.calculator_traces : []
  const graphHops = Array.isArray(metadata?.graph_hops) ? metadata.graph_hops : []
  const expandedTickers = Array.isArray(metadata?.expanded_tickers) ? metadata.expanded_tickers : []

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
      count: tables.length + calculators.length,
      icon: Calculator,
    },
    { id: "graph", label: "Đồ thị 3 Tầng", count: graphHops.length, icon: Network },
    { id: "trace", label: "Trace & Token", icon: Activity },
    { id: "eval", label: "Đánh giá G1–G5", icon: ShieldCheck },
  ]

  return (
    <Panel flush className="flex flex-col min-h-[680px] xl:h-[calc(100dvh-215px)] overflow-hidden">
      {/* Header + 5 Tabs */}
      <div className="border-b border-line bg-surface px-4 pt-3 shrink-0">
        <div className="flex items-center justify-between gap-2 mb-2.5">
          <h2 className="text-[14px] font-semibold text-ink">
            Evidence & Audit Inspector
          </h2>
          {metadata?.primary_ticker && (
            <span className="font-mono text-[11px] text-muted">
              {metadata.primary_ticker} · {metadata.as_of_date || "PIT"}
            </span>
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
                    className={`rounded-full px-1.5 py-0.2 font-mono text-[10px] tnum ${
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
