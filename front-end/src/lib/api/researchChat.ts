import { API_BASE, apiClient, tokenStore } from "./client"
import {
  createSseParser,
  type ParsedSseFrame,
  type ResearchChatMetadata,
  type ResearchChatMode,
  type SseStageName,
} from "./researchChatHelpers"

export * from "./researchChatHelpers"

export interface ResearchSessionSummary {
  id: string
  title: string | null
  ticker?: string | null
  mode?: ResearchChatMode
  asOfDate?: string | null
  enableKg?: boolean | null
  createdAt: string
  updatedAt?: string
  lastMessageAt?: string | null
  lastMessagePreview?: string | null
  messageCount?: number
}

export interface ResearchChatMessage {
  id: string
  sessionId?: string
  role: "user" | "assistant" | "system" | string
  content: string
  type?: string
  metadata?: ResearchChatMetadata | null
  createdAt: string
}

export interface ResearchSessionDetailResponse {
  session: ResearchSessionSummary
  messages: ResearchChatMessage[]
}

export type EvalVerdict = "PASS" | "NEEDS_FIX" | "FAIL"

export interface ResearchFeedbackPayload {
  messageId: string
  verdict: EvalVerdict
  citationOk?: boolean
  tableCalcOk?: boolean
  graphContextOk?: boolean
  hallucinationFlag?: boolean
  notes?: string
}

export interface EvalSummaryResponse {
  totalSessions: number
  totalAssistantMessages: number
  feedbackCount: number
  autoHallucinationCount?: number
  gates: {
    g1CitationVerifiedRate: number | null
    g2TableCalcVerifiedRate: number | null
    g3AbstentionComplianceRate: number | null
    g4PitZeroLeakCount: number
    g5HumanPassRate: number | null
  }
}

export interface IngestedTickersResponse {
  organization_id: string
  tickers: string[]
  pilot_fallback: string[]
}

export interface StreamResearchChatPayload {
  sessionId: string
  query: string
  ticker?: string
  compareTickers?: string[]
  mode?: ResearchChatMode
  asOfDate?: string
  enableKg?: boolean
}

export interface SseStageEvent {
  stage: SseStageName
  status: "running" | "done" | "error"
  detail?: Record<string, unknown>
}

export interface SseDoneEvent {
  messageId?: string
  answer: string
  metadata: ResearchChatMetadata
}

export interface SseErrorEvent {
  code?: string
  message: string
}

async function refreshAccessToken(): Promise<string | null> {
  try {
    const res = await fetch(`${API_BASE}/auth/refresh`, {
      method: "POST",
      credentials: "include",
    })
    if (!res.ok) {
      tokenStore.clear()
      return null
    }
    const data = (await res.json()) as { accessToken?: string }
    if (data?.accessToken) {
      tokenStore.set(data.accessToken)
      return data.accessToken
    }
    return null
  } catch {
    tokenStore.clear()
    return null
  }
}

export interface StreamResearchChatCallbacks {
  onStage?: (stage: SseStageEvent) => void
  onDelta?: (text: string) => void
  onDone?: (done: SseDoneEvent) => void
  onError?: (err: SseErrorEvent) => void
}

export async function streamResearchChat(
  payload: StreamResearchChatPayload,
  callbacks: StreamResearchChatCallbacks,
  signal?: AbortSignal,
): Promise<void> {
  const sendRequest = async (token: string | null) =>
    fetch(`${API_BASE}/ai/research-chat/stream`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      credentials: "include",
      body: JSON.stringify(payload),
      signal,
    })

  let response = await sendRequest(tokenStore.get())
  if (response.status === 401) {
    const refreshed = await refreshAccessToken()
    if (refreshed) {
      response = await sendRequest(refreshed)
    }
  }

  if (!response.ok) {
    let message = `Yêu cầu thất bại (HTTP ${response.status}).`
    let code = `HTTP_${response.status}`
    try {
      const body = (await response.json()) as { code?: string; message?: string; error?: string }
      if (body?.message || body?.error) message = String(body.message || body.error)
      if (body?.code) code = String(body.code)
    } catch {
      // ignore non-json error response
    }
    callbacks.onError?.({ code, message })
    throw new Error(message)
  }

  if (!response.body) {
    const err = { code: "NO_STREAM_BODY", message: "Trình duyệt không nhận được luồng SSE từ máy chủ." }
    callbacks.onError?.(err)
    throw new Error(err.message)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder("utf-8")
  const parser = createSseParser()
  let receivedDone = false

  const dispatchFrame = (frame: ParsedSseFrame) => {
    let parsed: Record<string, unknown> | null = null
    try {
      parsed = JSON.parse(frame.data) as Record<string, unknown>
    } catch {
      parsed = null
    }

    if (frame.event === "stage" && parsed) {
      callbacks.onStage?.({
        stage: String(parsed.stage ?? "routing") as SseStageName,
        status: parsed.status === "done" || parsed.status === "error" ? parsed.status : "running",
        detail:
          parsed.detail && typeof parsed.detail === "object"
            ? (parsed.detail as Record<string, unknown>)
            : undefined,
      })
      return
    }

    if (frame.event === "delta") {
      const text = parsed ? String(parsed.text ?? "") : frame.data
      if (text) callbacks.onDelta?.(text)
      return
    }

    if (frame.event === "done" && parsed) {
      receivedDone = true
      callbacks.onDone?.({
        messageId: parsed.messageId ? String(parsed.messageId) : undefined,
        answer: String(parsed.answer ?? ""),
        metadata:
          parsed.metadata && typeof parsed.metadata === "object"
            ? (parsed.metadata as ResearchChatMetadata)
            : {},
      })
      return
    }

    if (frame.event === "error") {
      const err: SseErrorEvent = {
        code: parsed?.code ? String(parsed.code) : "STREAM_ERROR",
        message: parsed?.message ? String(parsed.message) : frame.data || "Lỗi xử lý luồng nghiên cứu.",
      }
      callbacks.onError?.(err)
    }
  }

  try {
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      const chunk = decoder.decode(value, { stream: true })
      for (const frame of parser.push(chunk)) {
        dispatchFrame(frame)
      }
    }
    const tail = decoder.decode()
    if (tail) {
      for (const frame of parser.push(tail)) {
        dispatchFrame(frame)
      }
    }
    for (const frame of parser.flush()) {
      dispatchFrame(frame)
    }
  } finally {
    reader.releaseLock()
  }

  if (!receivedDone && !signal?.aborted) {
    // Allow callers to handle incomplete stream gracefully if server closed early without done event
  }
}

export const researchChatApi = {
  createSession: (body: {
    ticker?: string
    title?: string
    mode?: ResearchChatMode
    asOfDate?: string
    enableKg?: boolean
  }): Promise<ResearchSessionSummary> =>
    apiClient.post("/ai/research-chat/sessions", body).then((r) => r.data),

  listSessions: (): Promise<{ sessions: ResearchSessionSummary[] }> =>
    apiClient.get("/ai/research-chat/sessions").then((r) => {
      const raw = r.data
      if (Array.isArray(raw)) return { sessions: raw }
      return { sessions: Array.isArray(raw?.sessions) ? raw.sessions : [] }
    }),

  getSession: (id: string): Promise<ResearchSessionDetailResponse> =>
    apiClient.get(`/ai/research-chat/sessions/${encodeURIComponent(id)}`).then((r) => r.data),

  submitFeedback: (body: ResearchFeedbackPayload): Promise<{ feedback: unknown }> =>
    apiClient.post("/ai/research-chat/feedback", body).then((r) => r.data),

  getEvalSummary: (): Promise<EvalSummaryResponse> =>
    apiClient.get("/ai/research-chat/eval-summary").then((r) => r.data),

  listIngestedTickers: (): Promise<IngestedTickersResponse> =>
    apiClient.get("/ai/research-chat/tickers").then((r) => r.data),
}
