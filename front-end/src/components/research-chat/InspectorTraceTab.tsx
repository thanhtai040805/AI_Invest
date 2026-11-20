"use client"

import { Cpu, Gauge, GitCommitHorizontal, Layers } from "lucide-react"
import { Pill } from "@/components/ui"
import type { ResearchChatMetadata } from "@/lib/api/researchChat"

export function InspectorTraceTab({ metadata }: { metadata: ResearchChatMetadata }) {
  const tel = metadata.telemetry ?? {}
  const expanded = Array.isArray(metadata.expanded_tickers) ? metadata.expanded_tickers : []

  const metrics: Array<{ label: string; value: string; sub?: string }> = [
    {
      label: "Routing Slot",
      value: tel.routing_slot || "—",
      sub: "Blue-Green Leiden Slot",
    },
    {
      label: "Leiden Nodes Visited",
      value: tel.leiden_nodes_visited != null ? String(tel.leiden_nodes_visited) : "—",
      sub: "Tree Beam Routing",
    },
    {
      label: "HippoRAG 2 Hops",
      value: tel.hipporag_hops != null ? String(tel.hipporag_hops) : "—",
      sub: "Cross-Slot Multi-Hop",
    },
    {
      label: "Độ trễ tổng (Latency)",
      value: tel.latency_ms != null ? `${tel.latency_ms.toLocaleString("vi-VN")} ms` : "—",
      sub: "End-to-End Pipeline",
    },
    {
      label: "Input Tokens",
      value: tel.input_tokens != null ? tel.input_tokens.toLocaleString("vi-VN") : "—",
      sub: "DeepSeek-V4-Flash",
    },
    {
      label: "Output Tokens",
      value: tel.output_tokens != null ? tel.output_tokens.toLocaleString("vi-VN") : "—",
      sub: "Grounded Synthesis",
    },
    {
      label: "Chi phí ước tính",
      value: tel.cost_usd != null ? `$${tel.cost_usd.toFixed(5)}` : "—",
      sub: "DeepInfra API",
    },
    {
      label: "Chế độ & PIT",
      value: metadata.as_of_date || "—",
      sub: metadata.mode || "RESEARCH_CHAT_V2",
    },
  ]

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2.5">
        {metrics.map((m) => (
          <div
            key={m.label}
            className="rounded-[8px] border border-line bg-surface px-3 py-2.5"
          >
            <div className="text-[10.5px] font-semibold uppercase tracking-[0.06em] text-muted">
              {m.label}
            </div>
            <div className="mt-1 font-mono text-[16px] font-semibold text-ink tnum">
              {m.value}
            </div>
            {m.sub && <div className="mt-0.5 text-[11px] text-secondary">{m.sub}</div>}
          </div>
        ))}
      </div>

      <div className="rounded-[9px] border border-line bg-surface p-3.5 space-y-2.5">
        <div className="flex items-center gap-1.5 text-[12px] font-semibold text-ink">
          <Layers className="h-3.5 w-3.5 text-mineral" aria-hidden="true" />
          <span>Ngữ cảnh Truy xuất Point-in-Time</span>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-[12px]">
          <div className="rounded bg-paper px-2.5 py-2 border border-line">
            <span className="text-muted">Mã chính: </span>
            <span className="font-mono font-semibold text-ink">
              {metadata.primary_ticker || "—"}
            </span>
          </div>
          <div className="rounded bg-paper px-2.5 py-2 border border-line">
            <span className="text-muted">3-Layer KG: </span>
            <span className="font-mono font-semibold text-ink">
              {metadata.enable_kg === false ? "Tắt (Single-Ticker)" : "Đang bật"}
            </span>
          </div>
        </div>
        {expanded.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5 pt-1">
            <span className="inline-flex items-center gap-1 text-[11.5px] text-secondary mr-1">
              <GitCommitHorizontal className="h-3.5 w-3.5 text-muted" aria-hidden="true" />
              Allowed Project IDs:
            </span>
            {expanded.map((sym) => (
              <Pill key={sym} tone="neutral">
                <span className="font-mono">{sym}</span>
              </Pill>
            ))}
          </div>
        )}
      </div>

      <div className="rounded-[8px] border border-line bg-paper px-3 py-2.5 flex items-center justify-between text-[11.5px] text-secondary">
        <span className="inline-flex items-center gap-1.5">
          <Cpu className="h-3.5 w-3.5 text-muted" aria-hidden="true" />
          <span>Embedding: Qwen/Qwen3-Embedding-0.6B (1024d) + VN-Dot BM25</span>
        </span>
        <span className="inline-flex items-center gap-1 font-mono text-teal">
          <Gauge className="h-3.5 w-3.5" aria-hidden="true" />
          PIT Locked
        </span>
      </div>
    </div>
  )
}
