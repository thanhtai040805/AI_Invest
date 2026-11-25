export type SseStageName =
  | "routing"
  | "retrieval"
  | "graph_hop"
  | "table_hydration"
  | "calculator"
  | "synthesis"
  | "verification"

export type SseStageStatus = "idle" | "running" | "done" | "error"

export interface ResearchCitation {
  citation_id: string
  n?: number
  ticker?: string
  doc_id?: string
  document_version_id?: string
  sub_doc_slot: string
  page: number
  published_date?: string
  quote: string
  quote_hash?: string
  verified: boolean
  evidence_origin?: "PRIMARY" | "CROSS_REF"
  rejection_reason?: string | null
}

export interface HydratedTableRow {
  label?: string
  values?: Array<string | number | null>
  cells?: Array<string | number | null>
}

export type ResearchChatMode = "GENERAL" | "MOAT"
export type MemoryMode = "auto" | "read_only" | "incognito"

export interface RecalledMemoryItem {
  badge_id: string
  memory_id: string
  memory_type: string
  pit_sensitivity?: string
  content_text: string
  tickers?: string[]
  source_authority?: string
  source_as_of_date?: string | null
  staleness_status: "FRESH" | "STALE_NEW_FILING" | "STALE_RESTATED" | string
  staleness_reason?: string | null
  pinned_by_user?: boolean
  score?: number
  origin_session_id?: string | null
}

export interface LinkedSessionItem {
  badge_id: string
  episode_id: string
  session_id: string
  session_title?: string | null
  episode_summary: string
  tickers?: string[]
  macro_factors?: string[]
  sectors?: string[]
  relation_type?: string
  link_score?: number
  source_as_of_date?: string | null
  staleness_status: "FRESH" | "STALE_NEW_FILING" | "STALE_RESTATED" | string
  staleness_reason?: string | null
}

export interface UserMemoryItem {
  memory_id: string
  scope: string
  memory_type: string
  pit_sensitivity: string
  canonical_slot_key?: string | null
  content_text: string
  structured_payload?: Record<string, unknown>
  tickers: string[]
  bound_doc_version_ids?: string[]
  confidence: number
  source_authority: string
  origin_episode_id?: string | null
  origin_session_id?: string | null
  source_as_of_date: string
  is_backtest_sandbox: boolean
  valid_from: string
  valid_to: string
  tx_from: string
  tx_to: string
  supersedes_memory_id?: string | null
  staleness_status: "FRESH" | "STALE_NEW_FILING" | "STALE_RESTATED" | string
  staleness_reason?: string | null
  reinforcement_count: number
  pinned_by_user: boolean
  qdrant_sync_status?: string
  sync_version?: number
  created_at: string
  updated_at: string
}

export interface UserMemoryListResponse {
  total: number
  items: UserMemoryItem[]
}

export interface RelatedSessionsResponse {
  session_id: string
  episode_id?: string | null
  staleness_status: string
  staleness_reason?: string | null
  related_sessions: LinkedSessionItem[]
}

export interface UserMemoryClearResponse {
  retracted_memories: number
  deleted_episodes: number
  closed_edges: number
}

export interface DeleteResearchSessionResponse {
  deleted: boolean
  sessionId: string
  outboxId?: string
  cascadeStatus?: "SENT" | "PENDING" | string
}

export interface HydratedTable {
  table_id: string
  title: string
  sub_doc_slot?: string
  page_from?: number
  page_to?: number
  currency?: string
  unit_raw?: string
  unit?: string
  unit_multiplier?: number
  verification_status: "VERIFIED" | "PARTIAL" | "TABLE_UNVERIFIED" | string
  sanity_error_ratio?: number | null
  headers: string[][] | string[]
  rows: Array<HydratedTableRow | Array<string | number | null>>
  column_roles?: string[]
  amount_col_indices?: number[]
  footnotes_text?: string | null
}

export interface CalculatorTraceInputRecord {
  value: number | string
  table_id?: string
  row?: string
  source?: string
  name?: string
}

export interface CalculatorTrace {
  calc_id: string
  formula: string
  description?: string
  label?: string
  inputs: Record<string, CalculatorTraceInputRecord> | Array<CalculatorTraceInputRecord & { name: string }>
  result?: number | null
  formatted: string
}

export interface GraphHop {
  layer?: "MACRO" | "VALUE_CHAIN" | "TRUE_PEER" | string
  src_label?: string
  from?: string
  relation_type?: string
  relation?: string
  dst_label?: string
  to?: string
  beta?: number | null
  p_value?: number | null
  fdr_q_value?: number | null
  tercile_stable?: boolean | null
  similarity?: number | null
  verification_tier?: string
  tier?: string
  effective_from?: string
}

export interface DiscrepancyAlert {
  alert_id?: string
  type?: string
  severity?: "HIGH" | "MEDIUM" | "LOW" | string
  metric?: string
  narrative_claim?: string
  audited_value?: string
  delta_pct?: number | null
  summary?: string
  citations?: string[]
}

export interface DeclaredBlindspot {
  code: string
  reason: string
  scope?: string
}

export interface ScreeningResultRow {
  ticker: string
  company_name?: string | null
  sector?: string | null
  fiscal_year?: number | null
  pe?: number | null
  pb?: number | null
  roe?: number | null
  roa?: number | null
  gross_margin?: number | null
  net_margin?: number | null
  debt_to_equity?: number | null
  liabilities_to_equity?: number | null
  revenue_yoy?: number | null
  source_kind?: string | null
  valid_from?: string | null
  [key: string]: unknown
}

export interface ToolTraceItem {
  tool: string
  duration_ms?: number
  matched_count?: number
  returned_count?: number
  recall_count?: number
  crag_grade?: string | null
  tickers?: string[]
  queries?: string[]
  [key: string]: unknown
}

export interface SelectedDocumentPreview {
  citation_id: string
  ticker: string
  sub_doc_slot: string
  page: number
  fiscal_year?: number | null
  heading?: string
  score?: number
  evidence_origin?: "PRIMARY" | "CROSS_REF" | string
  table_id?: string | null
}

export interface HydratedTablePreview {
  table_id: string
  ticker?: string
  title: string
  sub_doc_slot?: string
  page?: number
  verification_status: string
  rows_count?: number
  unit_raw?: string
}

export interface KgHopPreview {
  layer: string
  src_label: string
  relation_type: string
  dst_label: string
  beta?: number | null
  verification_tier?: string
}

export interface CalcResultPreview {
  calc_id: string
  formula: string
  description?: string
  formatted: string
}

export interface ScreeningPreviewRow {
  ticker: string
  sector?: string
  fiscal_year?: number | null
  pe?: number | null
  pb?: number | null
  roe?: number | null
  gross_margin?: number | null
  net_margin?: number | null
}

export interface ResearchActivityItem {
  id: string
  call_id?: string
  type: "routing" | "thought" | "tool_call" | "verification" | string
  turn?: number
  kind?: "plan" | "reasoning" | "verification" | string
  title?: string
  text?: string
  append?: boolean
  tool?: string
  label?: string
  stage?: SseStageName | string
  status?: "running" | "done" | "error"
  route_intent?: string | null
  budget_tier?: string | null
  tickers?: string[]
  queries?: string[]
  sub_doc_slots?: string[]
  args_preview?: string
  duration_ms?: number
  summary?: string
  crag_grade?: string | null
  reranker_model?: string | null
  recall_count?: number | null
  returned_count?: number | null
  selected_documents?: SelectedDocumentPreview[]
  hydrated_tables?: HydratedTablePreview[]
  kg_hops?: KgHopPreview[]
  calc_result?: CalcResultPreview | null
  screening_preview?: ScreeningPreviewRow[]
  error?: string | null
  verified_citations?: number
  rejected_citations?: number
  unsupported_numeric_claims?: number
  timestamp_ms?: number
  completed_at_ms?: number
}

export interface ResearchTelemetry {
  routing_slot?: string
  route_intent?: string | null
  route_domain_class?: string | null
  route_budget_tier?: string | null
  route_confidence?: number | null
  eval_eligible?: boolean
  quarantined_future_turns?: number
  retrieval_mode?: string
  reranker_provider?: string | null
  reranker_model?: string | null
  crag_grade?: string | null
  agent_turns?: number
  tool_calls_count?: number
  primary_section_count?: number
  cross_ref_section_count?: number
  leiden_nodes_visited?: number
  hipporag_hops?: number
  rejected_citations_count?: number
  unsupported_numeric_claims_count?: number
  hallucinated_citation_ids_count?: number
  hallucinated_memory_ids_count?: number
  user_memory_mode?: string
  memory_recall_need?: string
  recalled_memory_count?: number
  llm_model?: string | null
  embedding_model?: string | null
  input_tokens?: number
  cached_tokens?: number
  output_tokens?: number
  cost_usd?: number
  latency_ms?: number
}

export interface ResearchChatMetadata {
  mode?: string
  chat_mode?: ResearchChatMode
  route_intent?: string | null
  eval_eligible?: boolean
  primary_ticker?: string
  expanded_tickers?: string[]
  resolved_query?: string
  sub_queries?: string[]
  as_of_date?: string
  enable_kg?: boolean
  citations?: ResearchCitation[]
  rejected_citations?: ResearchCitation[]
  hydrated_tables?: HydratedTable[]
  screening_results?: ScreeningResultRow[]
  tool_traces?: ToolTraceItem[]
  activity_log?: ResearchActivityItem[]
  calculator_traces?: CalculatorTrace[]
  graph_hops?: GraphHop[]
  discrepancy_alerts?: DiscrepancyAlert[]
  declared_blindspots?: Array<DeclaredBlindspot | string>
  recalled_memories?: RecalledMemoryItem[]
  linked_sessions?: LinkedSessionItem[]
  portfolio_snapshot_used?: boolean
  telemetry?: ResearchTelemetry
}

export interface ParsedSseFrame {
  event: string
  data: string
}

/**
 * Stateful SSE parser that handles chunked UTF-8 text split across network boundaries,
 * CRLF or LF line endings, and multi-line `data:` blocks.
 */
export function createSseParser() {
  let buffer = ""

  const parseBlock = (block: string): ParsedSseFrame | null => {
    const trimmed = block.trim()
    if (!trimmed) return null
    let eventName = "message"
    const dataLines: string[] = []
    for (const rawLine of block.split(/\r?\n/)) {
      if (!rawLine || rawLine.startsWith(":")) continue
      const colonIdx = rawLine.indexOf(":")
      const field = colonIdx === -1 ? rawLine : rawLine.slice(0, colonIdx)
      let value = colonIdx === -1 ? "" : rawLine.slice(colonIdx + 1)
      if (value.startsWith(" ")) value = value.slice(1)
      if (field === "event") {
        eventName = value.trim() || "message"
      } else if (field === "data") {
        dataLines.push(value)
      }
    }
    if (dataLines.length === 0) return null
    return { event: eventName, data: dataLines.join("\n") }
  }

  return {
    push(chunk: string): ParsedSseFrame[] {
      buffer += chunk.replace(/\r\n/g, "\n").replace(/\r/g, "\n")
      const frames: ParsedSseFrame[] = []
      let boundary = buffer.indexOf("\n\n")
      while (boundary !== -1) {
        const block = buffer.slice(0, boundary)
        buffer = buffer.slice(boundary + 2)
        const parsed = parseBlock(block)
        if (parsed) frames.push(parsed)
        boundary = buffer.indexOf("\n\n")
      }
      return frames
    },
    flush(): ParsedSseFrame[] {
      const remaining = buffer
      buffer = ""
      const parsed = parseBlock(remaining)
      return parsed ? [parsed] : []
    },
  }
}

export function normalizeDeclaredBlindspots(
  raw: ResearchChatMetadata["declared_blindspots"],
): DeclaredBlindspot[] {
  if (!Array.isArray(raw)) return []
  return raw
    .map((item): DeclaredBlindspot | null => {
      if (typeof item === "string") {
        const clean = item.trim()
        return clean ? { code: "INSUFFICIENT_EVIDENCE", reason: clean } : null
      }
      if (item && typeof item === "object") {
        const reason = String(item.reason ?? "").trim()
        if (!reason) return null
        return {
          code: String(item.code ?? "INSUFFICIENT_EVIDENCE"),
          reason,
          scope: item.scope ? String(item.scope) : undefined,
        }
      }
      return null
    })
    .filter((item): item is DeclaredBlindspot => item !== null)
}

export function normalizeCalculatorInputs(
  inputs: CalculatorTrace["inputs"],
): Array<CalculatorTraceInputRecord & { name: string }> {
  if (Array.isArray(inputs)) {
    return inputs.map((entry, idx) => ({
      name: entry.name || `input_${idx + 1}`,
      value: entry.value,
      table_id: entry.table_id,
      row: entry.row,
      source: entry.source,
    }))
  }
  if (inputs && typeof inputs === "object") {
    return Object.entries(inputs).map(([name, rec]) => ({
      name,
      value: rec?.value ?? "—",
      table_id: rec?.table_id,
      row: rec?.row,
      source: rec?.source,
    }))
  }
  return []
}

/**
 * Reconstruct a structured activity timeline from persisted metadata.
 * Prefers `metadata.activity_log` emitted by SAG v2; gracefully degrades to `tool_traces` + `citations`
 * for historical messages saved before live activity logging was added.
 */
export function resolveActivityLogFromMetadata(
  meta?: ResearchChatMetadata | null,
): ResearchActivityItem[] {
  if (!meta) return []
  if (Array.isArray(meta.activity_log) && meta.activity_log.length > 0) {
    return meta.activity_log
  }
  const items: ResearchActivityItem[] = []
  const tel = meta.telemetry ?? {}
  if (meta.route_intent || tel.route_intent) {
    items.push({
      id: "fallback-routing",
      type: "routing",
      turn: 0,
      title: "Định tuyến Tier-0",
      text: `Ý định: ${meta.route_intent || tel.route_intent} · Ngân sách: ${tel.route_budget_tier || "standard"} · PIT: ${meta.as_of_date || "—"}`,
      status: "done",
    })
  }
  const traces = Array.isArray(meta.tool_traces) ? meta.tool_traces : []
  traces.forEach((tr, idx) => {
    items.push({
      id: `fallback-tool-${idx}`,
      call_id: `fallback-tool-${idx}`,
      type: "tool_call",
      tool: tr.tool,
      label: tr.tool,
      status: "done",
      tickers: Array.isArray(tr.tickers) ? tr.tickers : [],
      queries: Array.isArray(tr.queries) ? tr.queries : [],
      crag_grade: tr.crag_grade ?? null,
      recall_count: tr.recall_count ?? null,
      returned_count: tr.returned_count ?? null,
      duration_ms: tr.duration_ms,
      summary:
        tr.tool === "search_corporate_filings"
          ? `Chọn lọc ${tr.returned_count ?? 0}/${tr.recall_count ?? tr.returned_count ?? 0} đoạn${tr.crag_grade ? ` · CRAG ${tr.crag_grade}` : ""}`
          : `${tr.returned_count ?? tr.matched_count ?? 0} kết quả`,
    })
  })
  const cits = Array.isArray(meta.citations) ? meta.citations : []
  if (cits.length > 0) {
    const verified = cits.filter((c) => c.verified).length
    items.push({
      id: "fallback-verification",
      type: "verification",
      title: "Kiểm chứng Quote Hash & Số học (Stage 7)",
      text: `${verified}/${cits.length} trích dẫn khớp gốc · ${tel.unsupported_numeric_claims_count ?? 0} số liệu chưa khớp`,
      verified_citations: verified,
      rejected_citations: Array.isArray(meta.rejected_citations) ? meta.rejected_citations.length : 0,
      unsupported_numeric_claims: tel.unsupported_numeric_claims_count ?? 0,
      status: "done",
    })
  }
  return items
}
