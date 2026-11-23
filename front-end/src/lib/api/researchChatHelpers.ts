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
}

export interface HydratedTableRow {
  label?: string
  values?: Array<string | number | null>
  cells?: Array<string | number | null>
}

export type ResearchChatMode = "GENERAL" | "MOAT"

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

export interface ResearchTelemetry {
  routing_slot?: string
  leiden_nodes_visited?: number
  hipporag_hops?: number
  input_tokens?: number
  output_tokens?: number
  cost_usd?: number
  latency_ms?: number
}

export interface ResearchChatMetadata {
  mode?: string
  chat_mode?: ResearchChatMode
  primary_ticker?: string
  expanded_tickers?: string[]
  resolved_query?: string
  sub_queries?: string[]
  as_of_date?: string
  enable_kg?: boolean
  citations?: ResearchCitation[]
  hydrated_tables?: HydratedTable[]
  calculator_traces?: CalculatorTrace[]
  graph_hops?: GraphHop[]
  discrepancy_alerts?: DiscrepancyAlert[]
  declared_blindspots?: Array<DeclaredBlindspot | string>
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
