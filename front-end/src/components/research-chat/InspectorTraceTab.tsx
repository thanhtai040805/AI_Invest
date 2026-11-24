"use client"

import { Cpu, Gauge, GitCommitHorizontal, Layers } from "lucide-react"
import { Pill } from "@/components/ui"
import type { ResearchChatMetadata } from "@/lib/api/researchChat"

export function InspectorTraceTab({ metadata }: { metadata: ResearchChatMetadata }) {
  const tel = metadata.telemetry ?? {}
  const expanded = Array.isArray(metadata.expanded_tickers) ? metadata.expanded_tickers : []
  const verifiedCitations = Array.isArray(metadata.citations)
    ? metadata.citations.filter((c) => c.verified).length
    : 0
  const rejectedCount =
    tel.rejected_citations_count ??
    (Array.isArray(metadata.rejected_citations) ? metadata.rejected_citations.length : 0)
  const unsupportedNumeric = tel.unsupported_numeric_claims_count ?? 0
  const primarySections = tel.primary_section_count
  const crossRefSections = tel.cross_ref_section_count
  const llmModelLabel = tel.llm_model || "Configured LLM"
  const embeddingModelLabel = tel.embedding_model || "Configured Embedding"

  const metrics: Array<{ label: string; value: string; sub?: string }> = [
    {
      label: "Chế độ Truy xuất",
      value: tel.retrieval_mode || "SHARED_CHAMBER_HYBRID",
      sub: tel.routing_slot ? `Slot chính: ${tel.routing_slot}` : "Qdrant Shared Chamber · PIT",
    },
    {
      label: "Kênh Bằng chứng",
      value:
        primarySections != null || crossRefSections != null
          ? `${primarySections ?? 0} Pri · ${crossRefSections ?? 0} XRef`
          : "—",
      sub: "Primary vs Cross-Ref",
    },
    {
      label: "Kiểm chứng Citation & Số",
      value: `${verifiedCitations} khớp · ${rejectedCount} loại`,
      sub:
        unsupportedNumeric > 0
          ? `${unsupportedNumeric} số liệu chưa khớp`
          : "Quote Hash + Numeric Audit",
    },
    {
      label: "Độ trễ tổng (Latency)",
      value: tel.latency_ms != null ? `${tel.latency_ms.toLocaleString("vi-VN")} ms` : "—",
      sub: "End-to-End Pipeline",
    },
    {
      label: "Input Tokens",
      value: tel.input_tokens != null ? tel.input_tokens.toLocaleString("vi-VN") : "—",
      sub: llmModelLabel,
    },
    {
      label: "Output Tokens",
      value: tel.output_tokens != null ? tel.output_tokens.toLocaleString("vi-VN") : "—",
      sub: "Grounded Synthesis",
    },
    {
      label: "Chi phí ước tính",
      value: tel.cost_usd != null ? `$${tel.cost_usd.toFixed(5)}` : "—",
      sub: llmModelLabel,
    },
    {
      label: "Chế độ & PIT",
      value: metadata.as_of_date || "—",
      sub: `${metadata.chat_mode || "GENERAL"} · ${metadata.mode || "RESEARCH_CHAT_V2"}`,
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
            <div className="mt-1 font-mono text-[14px] font-semibold text-ink tnum break-words">
              {m.value}
            </div>
            {m.sub && <div className="mt-0.5 text-[11px] text-secondary truncate">{m.sub}</div>}
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
              {metadata.primary_ticker || "(Tự động)"}
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
              Mã trong phạm vi truy xuất:
            </span>
            {expanded.map((sym) => (
              <Pill key={sym} tone="neutral">
                <span className="font-mono">{sym}</span>
              </Pill>
            ))}
          </div>
        )}
      </div>

      <div className="rounded-[8px] border border-line bg-paper px-3 py-2.5 flex items-center justify-between gap-2 text-[11.5px] text-secondary">
        <span className="inline-flex items-center gap-1.5 min-w-0">
          <Cpu className="h-3.5 w-3.5 text-muted shrink-0" aria-hidden="true" />
          <span className="truncate">Embedding: {embeddingModelLabel} + VN-Dot BM25</span>
        </span>
        <span className="inline-flex items-center gap-1 font-mono text-teal shrink-0">
          <Gauge className="h-3.5 w-3.5" aria-hidden="true" />
          PIT Locked
        </span>
      </div>
    </div>
  )
}
