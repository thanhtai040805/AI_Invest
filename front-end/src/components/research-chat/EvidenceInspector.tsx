"use client"

import { Calculator, FileText, Network, X } from "lucide-react"
import type { ResearchChatMetadata } from "@/lib/api/researchChat"
import { InspectorGraphTab } from "./InspectorGraphTab"
import { InspectorQuotesTab } from "./InspectorQuotesTab"
import { InspectorTablesCalcTab } from "./InspectorTablesCalcTab"
import type { InspectorFocus, InspectorTabId } from "./types"

interface EvidenceInspectorProps {
  activeTab: InspectorTabId
  onTabChange: (tab: InspectorTabId) => void
  metadata: ResearchChatMetadata | null
  focus: InspectorFocus | null
  onClose?: () => void
  className?: string
}

export function EvidenceInspector({
  activeTab,
  onTabChange,
  metadata,
  focus,
  onClose,
  className,
}: EvidenceInspectorProps) {
  const citations = Array.isArray(metadata?.citations) ? metadata.citations : []
  const tables = Array.isArray(metadata?.hydrated_tables) ? metadata.hydrated_tables : []
  const screeningResults = Array.isArray(metadata?.screening_results)
    ? metadata.screening_results
    : []
  const calculators = Array.isArray(metadata?.calculator_traces) ? metadata.calculator_traces : []
  const graphHops = Array.isArray(metadata?.graph_hops) ? metadata.graph_hops : []
  const expandedTickers = Array.isArray(metadata?.expanded_tickers)
    ? metadata.expanded_tickers
    : []

  const tablesAndCalcsCount =
    tables.length + calculators.length + (screeningResults.length > 0 ? 1 : 0)

  const tabs: Array<{
    id: InspectorTabId
    label: string
    count: number
    icon: typeof FileText
  }> = [
    {
      id: "tables",
      label: "Bảng & Số liệu",
      count: tablesAndCalcsCount,
      icon: Calculator,
    },
    {
      id: "quotes",
      label: "Trích dẫn văn bản",
      count: citations.length,
      icon: FileText,
    },
    ...(graphHops.length > 0 || activeTab === "graph"
      ? [
          {
            id: "graph" as InspectorTabId,
            label: "Quan hệ ngành",
            count: graphHops.length,
            icon: Network,
          },
        ]
      : []),
  ]

  const visibleTabIds = new Set(tabs.map((t) => t.id))
  const effectiveTab: InspectorTabId = visibleTabIds.has(activeTab)
    ? activeTab
    : tablesAndCalcsCount > 0
      ? "tables"
      : "quotes"

  return (
    <div className={`flex flex-col h-full bg-surface overflow-hidden ${className ?? ""}`}>
      {/* Clean Vietnamese Broker Source Header */}
      <div className="border-b border-line/75 bg-surface px-4 pt-3.5 shrink-0">
        <div className="flex items-center justify-between gap-2 mb-2.5">
          <div className="flex items-center gap-2 min-w-0">
            <h2 className="text-[14px] font-semibold text-ink truncate">
              Nguồn & Số liệu đối chiếu
            </h2>
            {metadata?.primary_ticker && (
              <span className="rounded-full bg-soft px-2 py-0.5 font-mono text-[11px] font-medium text-secondary shrink-0">
                {metadata.primary_ticker}
              </span>
            )}
          </div>
          {onClose && (
            <button
              type="button"
              onClick={onClose}
              aria-label="Đóng nguồn đối chiếu"
              className="inline-flex h-7.5 w-7.5 items-center justify-center rounded-full text-secondary hover:text-ink hover:bg-soft transition-colors shrink-0 cursor-pointer"
            >
              <X className="h-4 w-4" aria-hidden="true" />
            </button>
          )}
        </div>

        <div className="flex items-center gap-1 -mb-px" role="tablist">
          {tabs.map((tab) => {
            const Icon = tab.icon
            const active = effectiveTab === tab.id
            return (
              <button
                key={tab.id}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => onTabChange(tab.id)}
                className={`inline-flex items-center gap-1.5 border-b-2 px-3 py-2 text-[12.5px] font-medium whitespace-nowrap transition-colors cursor-pointer ${
                  active
                    ? "border-ink text-ink"
                    : "border-transparent text-secondary hover:text-ink"
                }`}
              >
                <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                <span>{tab.label}</span>
                {tab.count > 0 && (
                  <span
                    className={`rounded-full px-1.5 py-[1px] font-mono text-[10.5px] tnum ${
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
      <div className="flex-1 overflow-y-auto p-4 bg-[#f9faf9]">
        {effectiveTab === "quotes" && (
          <InspectorQuotesTab citations={citations} focus={focus} />
        )}
        {effectiveTab === "tables" && (
          <InspectorTablesCalcTab
            tables={tables}
            calculators={calculators}
            screeningResults={screeningResults}
            focus={focus}
          />
        )}
        {effectiveTab === "graph" && (
          <InspectorGraphTab
            graphHops={graphHops}
            expandedTickers={expandedTickers}
            focus={focus}
          />
        )}
      </div>
    </div>
  )
}
