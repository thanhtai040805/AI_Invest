"use client"

import { Brain, ClipboardList, Cpu, Gauge, GitCommitHorizontal, Layers, Wrench } from "lucide-react"
import { Pill } from "@/components/ui"
import type { ResearchChatMetadata } from "@/lib/api/researchChat"

export function InspectorTraceTab({ metadata }: { metadata: ResearchChatMetadata }) {
  const tel = metadata.telemetry ?? {}
  const expanded = Array.isArray(metadata.expanded_tickers) ? metadata.expanded_tickers : []
  const toolTraces = Array.isArray(metadata.tool_traces) ? metadata.tool_traces : []
  const brief = metadata.research_brief ?? null
  const verdictCards = Array.isArray(metadata.verdict_cards) ? metadata.verdict_cards : []
  const thinkingMs = tel.thinking_duration_ms
  const reasoningTokens = tel.reasoning_tokens ?? 0
  const verifiedCitations = Array.isArray(metadata.citations)
    ? metadata.citations.filter((c) => c.verified).length
    : 0
  const rejectedCount =
    tel.rejected_citations_count ??
    (Array.isArray(metadata.rejected_citations) ? metadata.rejected_citations.length : 0)
  const unsupportedNumeric = tel.unsupported_numeric_claims_count ?? 0
  const hallucinatedMemoryIds = tel.hallucinated_memory_ids_count ?? 0
  const cachedTokens = tel.cached_tokens ?? 0
  const userMemoryMode = tel.user_memory_mode || "off"
  const memoryRecallNeed = tel.memory_recall_need || "none"
  const recalledMemoryCount =
    tel.recalled_memory_count ??
    (Array.isArray(metadata.recalled_memories) ? metadata.recalled_memories.length : 0)
  const primarySections = tel.primary_section_count
  const crossRefSections = tel.cross_ref_section_count
  const llmModelLabel = tel.llm_model || "Configured LLM"
  const embeddingModelLabel = tel.embedding_model || "Configured Embedding"
  const routeIntent = metadata.route_intent || tel.route_intent || "fact_lookup"
  const cragGrade = tel.crag_grade || "—"
  const quarantinedTurns = tel.quarantined_future_turns ?? 0

  const metrics: Array<{ label: string; value: string; sub?: string; alert?: boolean }> = [
    {
      label: "Tier-0 Decision Gate",
      value: routeIntent,
      sub: `${tel.route_budget_tier || "standard"} · ${metadata.eval_eligible === false ? "Short-Circuit" : "Research"}`,
    },
    {
      label: "Agent & Tool Calls",
      value: `${tel.agent_turns ?? 0} lượt · ${tel.tool_calls_count ?? toolTraces.length} tool`,
      sub: tel.retrieval_mode || "SHARED_CHAMBER_HYBRID",
    },
    {
      label: "CRAG & Reranker",
      value: cragGrade,
      sub: tel.reranker_model ? `${tel.reranker_provider || "rerank"}: ${tel.reranker_model}` : "Hybrid RRF + Grader",
    },
    {
      label: "Kênh Bằng chứng",
      value:
        primarySections != null || crossRefSections != null
          ? `${primarySections ?? 0} Pri · ${crossRefSections ?? 0} XRef`
          : "—",
      sub: quarantinedTurns > 0 ? `Cách ly PIT: ${quarantinedTurns} lượt tương lai` : "Primary vs Cross-Ref",
    },
    {
      label: "Kiểm chứng Citation & Số",
      value: `${verifiedCitations} khớp · ${rejectedCount} loại`,
      sub:
        unsupportedNumeric > 0 || hallucinatedMemoryIds > 0
          ? `${unsupportedNumeric} số chưa khớp · ${hallucinatedMemoryIds} MEM ảo`
          : "Quote Hash + Clause Numeric Audit",
      alert: unsupportedNumeric > 0 || hallucinatedMemoryIds > 0,
    },
    {
      label: "Suy luận & Độ trễ tổng",
      value:
        thinkingMs != null
          ? `${(thinkingMs / 1000).toFixed(1)}s suy luận`
          : tel.latency_ms != null
            ? `${tel.latency_ms.toLocaleString("vi-VN")} ms`
            : "—",
      sub:
        tel.latency_ms != null
          ? `Tổng E2E: ${(tel.latency_ms / 1000).toFixed(1)}s (${tel.latency_ms.toLocaleString("vi-VN")} ms)`
          : "End-to-End Pipeline",
    },
    {
      label: "Tokens (In / Cache / Out)",
      value:
        tel.input_tokens != null || tel.output_tokens != null
          ? `${(tel.input_tokens ?? 0).toLocaleString("vi-VN")} / ${cachedTokens.toLocaleString("vi-VN")} / ${(tel.output_tokens ?? 0).toLocaleString("vi-VN")}`
          : "—",
      sub:
        reasoningTokens > 0
          ? `${reasoningTokens.toLocaleString("vi-VN")} tok suy luận · ${llmModelLabel}`
          : llmModelLabel,
    },
    {
      label: "Chi phí & PIT",
      value: tel.cost_usd != null ? `$${tel.cost_usd.toFixed(5)}` : "—",
      sub: `${metadata.as_of_date || "—"} · ${metadata.chat_mode || "GENERAL"}`,
    },
  ]

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2.5">
        {metrics.map((m) => (
          <div
            key={m.label}
            className={`rounded-[8px] border bg-surface px-3 py-2.5 ${
              m.alert ? "border-loss/40 bg-loss/5" : "border-line"
            }`}
          >
            <div className="text-[10.5px] font-semibold uppercase tracking-[0.06em] text-muted">
              {m.label}
            </div>
            <div
              className={`mt-1 font-mono text-[14px] font-semibold tnum break-words ${
                m.alert ? "text-loss" : "text-ink"
              }`}
            >
              {m.value}
            </div>
            {m.sub && <div className="mt-0.5 text-[11px] text-secondary truncate">{m.sub}</div>}
          </div>
        ))}
      </div>

      {/* User Memory & Prompt Cache Telemetry */}
      <div className="rounded-[9px] border border-line bg-surface p-3.5 space-y-2.5">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-1.5 text-[12px] font-semibold text-ink">
            <Brain className="h-3.5 w-3.5 text-ceiling" aria-hidden="true" />
            <span>Ký ức Người dùng & Prompt Cache (Mem0-g)</span>
          </div>
          <Pill
            tone={
              userMemoryMode === "on"
                ? "teal"
                : userMemoryMode === "shadow"
                  ? "gold"
                  : userMemoryMode === "incognito"
                    ? "warning"
                    : "neutral"
            }
          >
            <span className="font-mono">{userMemoryMode}</span>
          </Pill>
        </div>
        <div className="grid grid-cols-2 gap-2 text-[12px]">
          <div className="rounded bg-paper px-2.5 py-2 border border-line">
            <div className="text-[10.5px] text-muted">Trục 8 · Recall Need</div>
            <div className="font-mono font-semibold text-ink truncate mt-0.5">{memoryRecallNeed}</div>
          </div>
          <div className="rounded bg-paper px-2.5 py-2 border border-line">
            <div className="text-[10.5px] text-muted">Ký ức triệu hồi</div>
            <div className="font-mono font-semibold text-ink tnum mt-0.5">
              {recalledMemoryCount} mục
            </div>
          </div>
          <div className="rounded bg-paper px-2.5 py-2 border border-line">
            <div className="text-[10.5px] text-muted">Cached Prompt Tokens</div>
            <div className="font-mono font-semibold text-teal tnum mt-0.5">
              {cachedTokens.toLocaleString("vi-VN")} tok
            </div>
          </div>
          <div className="rounded bg-paper px-2.5 py-2 border border-line">
            <div className="text-[10.5px] text-muted">Hallucinated Memory IDs</div>
            <div
              className={`font-mono font-semibold tnum mt-0.5 ${
                hallucinatedMemoryIds > 0 ? "text-loss" : "text-gain"
              }`}
            >
              {hallucinatedMemoryIds}
            </div>
          </div>
        </div>
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

      {(brief?.user_concern || verdictCards.length > 0) && (
        <div className="rounded-[9px] border border-line bg-surface p-3.5 space-y-2.5">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-1.5 text-[12px] font-semibold text-ink">
              <ClipboardList className="h-3.5 w-3.5 text-mineral" aria-hidden="true" />
              <span>Đề bài Trung tâm & Thẻ Kết luận ({verdictCards.length})</span>
            </div>
            {brief?.answer_shape && (
              <Pill tone="mineral">
                <span className="font-mono">{brief.answer_shape}</span>
              </Pill>
            )}
          </div>
          {brief?.user_concern && (
            <div className="rounded bg-paper px-2.5 py-2 border border-line text-[12px] text-ink">
              <span className="text-muted font-medium">Trọng tâm: </span>
              <span>{brief.user_concern}</span>
            </div>
          )}
          {verdictCards.length > 0 && (
            <div className="space-y-1.5">
              {verdictCards.map((vc) => {
                const st = String(vc.status || "").toUpperCase()
                const tone =
                  st === "CONFIRMED"
                    ? "teal"
                    : st === "REFUTED"
                      ? "loss"
                      : st === "PARTIAL"
                        ? "warning"
                        : "neutral"
                return (
                  <div
                    key={vc.claim_id}
                    className="rounded-[6px] border border-line bg-paper px-2.5 py-2 space-y-1 text-[11.5px]"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-1.5">
                      <span className="font-mono font-semibold text-ink">
                        [{vc.claim_id}] {vc.is_premise ? "Tiền đề" : "Luận điểm"}
                        {vc.period ? ` · ${vc.period}` : ""}
                      </span>
                      <div className="flex items-center gap-1">
                        {vc.escalated && <Pill tone="mineral">1-Hop</Pill>}
                        <Pill tone={tone}>{st}</Pill>
                      </div>
                    </div>
                    <div className="text-secondary leading-snug">{vc.statement}</div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}

      {toolTraces.length > 0 && (
        <div className="rounded-[9px] border border-line bg-surface p-3.5 space-y-2">
          <div className="flex items-center gap-1.5 text-[12px] font-semibold text-ink">
            <Wrench className="h-3.5 w-3.5 text-mineral" aria-hidden="true" />
            <span>Nhật ký Gọi Công cụ Bên ngoài ({toolTraces.length})</span>
          </div>
          <div className="space-y-1.5">
            {toolTraces.map((tr, idx) => (
              <div
                key={`${tr.tool}-${idx}`}
                className="flex flex-wrap items-center justify-between gap-2 rounded-[6px] border border-line bg-paper px-2.5 py-1.5 text-[11.5px]"
              >
                <div className="flex items-center gap-1.5 min-w-0">
                  <span className="font-mono font-semibold text-ink">{tr.tool}</span>
                  {tr.crag_grade && <Pill tone="neutral">{tr.crag_grade}</Pill>}
                  {Array.isArray(tr.tickers) && tr.tickers.length > 0 && (
                    <span className="font-mono text-[10.5px] text-secondary truncate">
                      [{tr.tickers.join(", ")}]
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-2 font-mono text-[10.5px] text-muted shrink-0">
                  {tr.returned_count != null && <span>{tr.returned_count} kết quả</span>}
                  {tr.duration_ms != null && <span>{tr.duration_ms} ms</span>}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

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
