"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  researchChatApi,
  streamResearchChat,
  type EvalSummaryResponse,
  type ResearchChatMessage,
  type ResearchChatMetadata,
  type ResearchSessionSummary,
} from "@/lib/api/researchChat"
import { vietnamDate } from "@/lib/financial-date"
import { ChatColumn } from "./ChatColumn"
import { EvidenceInspector } from "./EvidenceInspector"
import type { AuditBadgeToken } from "./markdownParser"
import { TopControlBar } from "./TopControlBar"
import {
  badgeToInspectorFocus,
  SSE_STAGE_ORDER,
  type InspectorFocus,
  type InspectorTabId,
  type StageProgressItem,
} from "./types"

function createInitialStages(): StageProgressItem[] {
  return SSE_STAGE_ORDER.map((s) => ({
    stage: s.stage,
    label: s.label,
    status: "idle",
  }))
}

export function ResearchChatWorkspace() {
  const todayDate = useMemo(() => vietnamDate() || "2026-11-20", [])
  const [primaryTicker, setPrimaryTicker] = useState("VCB")
  const [compareTickers, setCompareTickers] = useState<string[]>([])
  const [enableKg, setEnableKg] = useState(true)
  const [asOfDate, setAsOfDate] = useState(todayDate)

  const [sessions, setSessions] = useState<ResearchSessionSummary[]>([])
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null)
  const [creatingSession, setCreatingSession] = useState(false)
  const [messages, setMessages] = useState<ResearchChatMessage[]>([])
  const [selectedAssistantMessageId, setSelectedAssistantMessageId] = useState<string | null>(null)

  const [streaming, setStreaming] = useState(false)
  const [streamingText, setStreamingText] = useState("")
  const [stages, setStages] = useState<StageProgressItem[]>(createInitialStages)
  const [streamError, setStreamError] = useState<string | null>(null)

  const [activeTab, setActiveTab] = useState<InspectorTabId>("quotes")
  const [inspectorFocus, setInspectorFocus] = useState<InspectorFocus | null>(null)
  const [evalSummary, setEvalSummary] = useState<EvalSummaryResponse | null>(null)

  const abortRef = useRef<AbortController | null>(null)

  const refreshEvalSummary = useCallback(async () => {
    try {
      const summary = await researchChatApi.getEvalSummary()
      setEvalSummary(summary)
    } catch {
      // Keep UI responsive if summary endpoint is temporarily unreachable
    }
  }, [])

  const loadSessionDetail = useCallback(async (sessionId: string) => {
    try {
      const detail = await researchChatApi.getSession(sessionId)
      const list = Array.isArray(detail.messages) ? detail.messages : []
      setActiveSessionId(detail.session?.id ?? sessionId)
      if (detail.session?.ticker) setPrimaryTicker(detail.session.ticker)
      if (detail.session?.asOfDate) setAsOfDate(detail.session.asOfDate)
      if (typeof detail.session?.enableKg === "boolean") setEnableKg(detail.session.enableKg)
      setMessages(list)
      const lastAssistant = [...list].reverse().find((m) => m.role === "assistant")
      setSelectedAssistantMessageId(lastAssistant?.id ?? null)
      setStreamError(null)
    } catch (err) {
      const msg =
        err && typeof err === "object" && "message" in err
          ? String((err as { message: unknown }).message)
          : "Không thể tải chi tiết phiên nghiên cứu."
      setStreamError(msg)
    }
  }, [])

  const refreshSessions = useCallback(async (): Promise<ResearchSessionSummary[]> => {
    try {
      const res = await researchChatApi.listSessions()
      const list = Array.isArray(res.sessions) ? res.sessions : []
      setSessions(list)
      return list
    } catch {
      return []
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    async function init() {
      const [sessionRes] = await Promise.all([refreshSessions(), refreshEvalSummary()])
      if (cancelled) return
      if (sessionRes.length > 0) {
        await loadSessionDetail(sessionRes[0].id)
      }
    }
    void init()
    return () => {
      cancelled = true
      abortRef.current?.abort()
    }
  }, [loadSessionDetail, refreshEvalSummary, refreshSessions])

  const handleCreateNewSession = useCallback(async () => {
    if (streaming || creatingSession) return
    setCreatingSession(true)
    setStreamError(null)
    try {
      const created = await researchChatApi.createSession({
        ticker: primaryTicker,
        title: `${primaryTicker} · ${asOfDate}`,
        asOfDate,
        enableKg,
      })
      setActiveSessionId(created.id)
      setMessages([])
      setSelectedAssistantMessageId(null)
      setStages(createInitialStages())
      await refreshSessions()
    } catch (err) {
      const msg =
        err && typeof err === "object" && "message" in err
          ? String((err as { message: unknown }).message)
          : "Không thể tạo phiên nghiên cứu mới."
      setStreamError(msg)
    } finally {
      setCreatingSession(false)
    }
  }, [asOfDate, creatingSession, enableKg, primaryTicker, refreshSessions, streaming])

  const handleSendPrompt = useCallback(
    async (query: string) => {
      const clean = query.trim()
      if (!clean || streaming) return

      setStreamError(null)
      setStreaming(true)
      setStreamingText("")
      setStages(createInitialStages())

      let sessionId = activeSessionId
      if (!sessionId) {
        try {
          const created = await researchChatApi.createSession({
            ticker: primaryTicker,
            title: `${primaryTicker}: ${clean.slice(0, 48)}`,
            asOfDate,
            enableKg,
          })
          sessionId = created.id
          setActiveSessionId(sessionId)
          void refreshSessions()
        } catch (err) {
          const msg =
            err && typeof err === "object" && "message" in err
              ? String((err as { message: unknown }).message)
              : "Không thể khởi tạo phiên nghiên cứu."
          setStreamError(msg)
          setStreaming(false)
          return
        }
      }

      const userMsg: ResearchChatMessage = {
        id: `local-user-${Date.now()}`,
        sessionId,
        role: "user",
        content: clean,
        createdAt: new Date().toISOString(),
      }
      setMessages((prev) => [...prev, userMsg])

      const controller = new AbortController()
      abortRef.current = controller
      let accumulatedText = ""
      let finished = false

      try {
        await streamResearchChat(
          {
            sessionId,
            query: clean,
            ticker: primaryTicker,
            compareTickers: compareTickers.length > 0 ? compareTickers : undefined,
            asOfDate,
            enableKg,
          },
          {
            onStage: (evt) => {
              setStages((prev) => {
                const targetIdx = prev.findIndex((s) => s.stage === evt.stage)
                return prev.map((item, idx) => {
                  if (item.stage === evt.stage) {
                    return { ...item, status: evt.status, detail: evt.detail }
                  }
                  if (targetIdx > idx && item.status === "running") {
                    return { ...item, status: "done" }
                  }
                  return item
                })
              })
            },
            onDelta: (text) => {
              accumulatedText += text
              setStreamingText(accumulatedText)
            },
            onDone: (done) => {
              finished = true
              const assistantId = done.messageId || `local-assistant-${Date.now()}`
              const finalContent = done.answer || accumulatedText
              const assistantMsg: ResearchChatMessage = {
                id: assistantId,
                sessionId: sessionId ?? undefined,
                role: "assistant",
                content: finalContent,
                metadata: done.metadata,
                createdAt: new Date().toISOString(),
              }
              setMessages((prev) => [...prev, assistantMsg])
              setSelectedAssistantMessageId(assistantId)
              setStages((prev) =>
                prev.map((item) =>
                  item.status === "running" || item.status === "idle"
                    ? { ...item, status: "done" }
                    : item,
                ),
              )
              void refreshSessions()
              void refreshEvalSummary()
            },
            onError: (err) => {
              setStreamError(
                err.code ? `[${err.code}] ${err.message}` : err.message,
              )
              setStages((prev) =>
                prev.map((item) =>
                  item.status === "running" ? { ...item, status: "error" } : item,
                ),
              )
            },
          },
          controller.signal,
        )

        if (!finished && accumulatedText.trim() && !controller.signal.aborted) {
          const fallbackId = `local-assistant-${Date.now()}`
          setMessages((prev) => [
            ...prev,
            {
              id: fallbackId,
              sessionId: sessionId ?? undefined,
              role: "assistant",
              content: accumulatedText,
              metadata: {},
              createdAt: new Date().toISOString(),
            },
          ])
          setSelectedAssistantMessageId(fallbackId)
        }
      } catch (err) {
        if ((err as { name?: string })?.name !== "AbortError") {
          const msg =
            err instanceof Error ? err.message : "Kết nối SSE bị gián đoạn."
          setStreamError((prev) => prev || msg)
        }
      } finally {
        abortRef.current = null
        setStreaming(false)
        setStreamingText("")
      }
    },
    [
      activeSessionId,
      asOfDate,
      compareTickers,
      enableKg,
      primaryTicker,
      refreshEvalSummary,
      refreshSessions,
      streaming,
    ],
  )

  const handleStopStream = useCallback(() => {
    abortRef.current?.abort()
    setStreaming(false)
  }, [])

  const handleBadgeClick = useCallback(
    (badge: AuditBadgeToken, messageId?: string) => {
      if (messageId) setSelectedAssistantMessageId(messageId)
      const nextFocus = badgeToInspectorFocus(badge, Date.now())
      if (nextFocus) {
        setActiveTab(nextFocus.tab)
        setInspectorFocus(nextFocus)
      }
    },
    [],
  )

  const selectedAssistantMessage = useMemo(() => {
    const assistants = messages.filter((m) => m.role === "assistant")
    if (assistants.length === 0) return null
    if (selectedAssistantMessageId) {
      const found = assistants.find((m) => m.id === selectedAssistantMessageId)
      if (found) return found
    }
    return assistants[assistants.length - 1]
  }, [messages, selectedAssistantMessageId])

  const activeMetadata: ResearchChatMetadata | null =
    selectedAssistantMessage?.metadata ?? null

  return (
    <div className="space-y-3.5">
      <TopControlBar
        primaryTicker={primaryTicker}
        onPrimaryTickerChange={setPrimaryTicker}
        compareTickers={compareTickers}
        onCompareTickersChange={setCompareTickers}
        enableKg={enableKg}
        onEnableKgChange={setEnableKg}
        asOfDate={asOfDate}
        todayDate={todayDate}
        onAsOfDateChange={setAsOfDate}
        sessions={sessions}
        activeSessionId={activeSessionId}
        onSelectSession={(id) => void loadSessionDetail(id)}
        onCreateNewSession={() => void handleCreateNewSession()}
        creatingSession={creatingSession}
        evalSummary={evalSummary}
        onOpenGateTab={() => setActiveTab("eval")}
        disabled={streaming}
      />

      <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)] gap-3.5 items-start">
        <ChatColumn
          primaryTicker={primaryTicker}
          messages={messages}
          selectedAssistantMessageId={selectedAssistantMessage?.id ?? null}
          onSelectAssistantMessage={(id) => setSelectedAssistantMessageId(id)}
          streaming={streaming}
          streamingText={streamingText}
          stages={stages}
          streamError={streamError}
          onSendPrompt={(q) => void handleSendPrompt(q)}
          onStopStream={handleStopStream}
          onBadgeClick={handleBadgeClick}
        />

        <EvidenceInspector
          activeTab={activeTab}
          onTabChange={setActiveTab}
          metadata={activeMetadata}
          messageId={selectedAssistantMessage?.id ?? null}
          focus={inspectorFocus}
          evalSummary={evalSummary}
          onRefreshSummary={refreshEvalSummary}
        />
      </div>
    </div>
  )
}
