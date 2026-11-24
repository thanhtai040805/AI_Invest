"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { MessageSquarePlus, MessagesSquare } from "lucide-react"
import { Button, Panel } from "@/components/ui"
import {
  researchChatApi,
  streamResearchChat,
  type EvalSummaryResponse,
  type ResearchChatMessage,
  type ResearchChatMetadata,
  type ResearchChatMode,
  type ResearchSessionSummary,
} from "@/lib/api/researchChat"
import { vietnamDate } from "@/lib/financial-date"
import { ChatColumn } from "./ChatColumn"
import { EvidenceInspector } from "./EvidenceInspector"
import type { AuditBadgeToken } from "./markdownParser"
import { TopControlBar } from "./TopControlBar"
import {
  badgeToInspectorFocus,
  PILOT_TICKERS,
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
  // Free-form ChatGPT-style defaults: no mandatory primary ticker lock, GENERAL mode default
  const [chatMode, setChatMode] = useState<ResearchChatMode>("GENERAL")
  const [primaryTicker, setPrimaryTicker] = useState("")
  const [compareTickers, setCompareTickers] = useState<string[]>([])
  const [enableKg, setEnableKg] = useState(true)
  const [asOfDate, setAsOfDate] = useState(todayDate)

  // ChatGPT-style layout drawers
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [drawerOpen, setDrawerOpen] = useState(false)

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
  const [availableTickers, setAvailableTickers] = useState<string[]>([...PILOT_TICKERS])

  const abortRef = useRef<AbortController | null>(null)

  const refreshIngestedTickers = useCallback(async () => {
    try {
      const res = await researchChatApi.listIngestedTickers()
      const live = Array.isArray(res?.tickers) && res.tickers.length > 0
        ? res.tickers
        : Array.isArray(res?.pilot_fallback) && res.pilot_fallback.length > 0
          ? res.pilot_fallback
          : [...PILOT_TICKERS]
      setAvailableTickers(live)
    } catch {
      // Keep PILOT_TICKERS fallback if ticker endpoint is temporarily unreachable
    }
  }, [])

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
      const resolvedMode: ResearchChatMode =
        detail.session?.mode === "MOAT"
          ? "MOAT"
          : [...list].reverse().find((m) => m.role === "assistant")?.metadata?.chat_mode === "MOAT"
            ? "MOAT"
            : "GENERAL"
      setChatMode(resolvedMode)
      // Keep GENERAL sessions unlocked (no stale single-ticker lock when pivoting companies);
      // history carry-over in backend handles follow-ups automatically.
      setPrimaryTicker(
        resolvedMode === "MOAT" && detail.session?.ticker
          ? detail.session.ticker.toUpperCase()
          : "",
      )
      setCompareTickers([])
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
      await Promise.all([refreshSessions(), refreshEvalSummary(), refreshIngestedTickers()])
      if (cancelled) return
    }
    void init()
    return () => {
      cancelled = true
      abortRef.current?.abort()
    }
  }, [refreshEvalSummary, refreshIngestedTickers, refreshSessions])

  const handleStartNewConversation = useCallback(() => {
    if (streaming || creatingSession) return
    setActiveSessionId(null)
    setMessages([])
    setSelectedAssistantMessageId(null)
    setPrimaryTicker("")
    setCompareTickers([])
    setStages(createInitialStages())
    setStreamError(null)
    setDrawerOpen(false)
  }, [creatingSession, streaming])

  const handleSendPrompt = useCallback(
    async (query: string) => {
      const clean = query.trim()
      if (!clean || streaming) return

      setStreamError(null)
      setStreaming(true)
      setStreamingText("")
      setStages(createInitialStages())

      const effectiveTicker = primaryTicker.trim().toUpperCase() || undefined
      let sessionId = activeSessionId
      if (!sessionId) {
        setCreatingSession(true)
        try {
          const titlePrefix = effectiveTicker ? `${effectiveTicker}: ` : ""
          const created = await researchChatApi.createSession({
            ticker: effectiveTicker,
            title: `${titlePrefix}${clean.slice(0, 56)}`,
            mode: chatMode,
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
          setCreatingSession(false)
          return
        } finally {
          setCreatingSession(false)
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
            ticker: effectiveTicker,
            compareTickers: compareTickers.length > 0 ? compareTickers : undefined,
            mode: chatMode,
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
              content:
                `> **[Chưa hoàn tất kiểm chứng]** Kết nối SSE bị ngắt trước Pha 7 (quote_hash & numeric audit). Nội dung bên dưới chưa qua xác thực và chưa được lưu trữ.\n\n${accumulatedText}`,
              metadata: {
                declared_blindspots: [
                  {
                    code: "STREAM_INTERRUPTED_UNVERIFIED",
                    reason:
                      "Luồng SSE kết thúc trước khi nhận sự kiện hoàn tất (done) từ Pha 7; các trích dẫn và số liệu chưa được kiểm chứng.",
                    scope: "stream",
                  },
                ],
              },
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
      chatMode,
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
      setDrawerOpen(true)
    },
    [],
  )

  const handleSelectAssistantMessage = useCallback((messageId: string) => {
    setSelectedAssistantMessageId(messageId)
    setDrawerOpen(true)
  }, [])

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

  const evidenceCount = useMemo(() => {
    if (!activeMetadata) return 0
    return (
      (Array.isArray(activeMetadata.citations) ? activeMetadata.citations.length : 0) +
      (Array.isArray(activeMetadata.hydrated_tables) ? activeMetadata.hydrated_tables.length : 0) +
      (Array.isArray(activeMetadata.calculator_traces) ? activeMetadata.calculator_traces.length : 0) +
      (Array.isArray(activeMetadata.graph_hops) ? activeMetadata.graph_hops.length : 0)
    )
  }, [activeMetadata])

  return (
    <Panel
      flush
      className="relative flex flex-col h-[calc(100dvh-132px)] min-h-[640px] overflow-hidden"
    >
      <TopControlBar
        chatMode={chatMode}
        primaryTicker={primaryTicker}
        compareTickers={compareTickers}
        asOfDate={asOfDate}
        sidebarOpen={sidebarOpen}
        onToggleSidebar={() => setSidebarOpen((v) => !v)}
        drawerOpen={drawerOpen}
        onToggleDrawer={() => setDrawerOpen((v) => !v)}
        evidenceCount={evidenceCount}
        evalSummary={evalSummary}
        onOpenGateTab={() => {
          setActiveTab("eval")
          setDrawerOpen(true)
        }}
      />

      <div className="relative flex flex-1 min-h-0 overflow-hidden">
        {/* Left Collapsible Conversation Sidebar */}
        {sidebarOpen && (
          <aside
            aria-label="Lịch sử hội thoại nghiên cứu"
            className="w-[248px] shrink-0 border-r border-line bg-surface flex flex-col overflow-hidden"
          >
            <div className="p-2.5 border-b border-line shrink-0">
              <Button
                variant="primary"
                size="sm"
                onClick={handleStartNewConversation}
                disabled={streaming || creatingSession}
                className="w-full justify-center !h-9 text-[12.5px]"
              >
                <MessageSquarePlus className="h-4 w-4 shrink-0" aria-hidden="true" />
                <span>Cuộc trò chuyện mới</span>
              </Button>
            </div>

            <div className="flex-1 overflow-y-auto p-2 space-y-1">
              {sessions.length === 0 ? (
                <div className="px-3 py-6 text-center text-[12px] text-muted space-y-1.5">
                  <MessagesSquare className="h-5 w-5 mx-auto opacity-50" aria-hidden="true" />
                  <p>Chưa có cuộc trò chuyện lưu trữ.</p>
                </div>
              ) : (
                sessions.map((s) => {
                  const isActive = s.id === activeSessionId
                  const dateLabel = s.lastMessageAt || s.updatedAt || s.createdAt
                  return (
                    <button
                      key={s.id}
                      type="button"
                      disabled={streaming}
                      onClick={() => void loadSessionDetail(s.id)}
                      className={`w-full text-left rounded-[8px] px-2.5 py-2 transition-colors border ${
                        isActive
                          ? "border-mineral/40 bg-mineral/10 text-ink"
                          : "border-transparent hover:bg-soft/75 text-secondary hover:text-ink"
                      } disabled:opacity-50`}
                    >
                      <div className="flex items-center gap-1.5 min-w-0">
                        {s.ticker && (
                          <span className="shrink-0 rounded bg-paper border border-line px-1 py-0.2 font-mono text-[9.5px] font-semibold text-ink">
                            {s.ticker}
                          </span>
                        )}
                        <span className="text-[12.5px] font-medium truncate">
                          {s.title || `Phiên ${s.id.slice(0, 8)}`}
                        </span>
                      </div>
                      <div className="mt-1 flex items-center justify-between text-[10.5px] text-muted font-mono">
                        <span>{dateLabel ? dateLabel.slice(0, 10) : ""}</span>
                        {typeof s.messageCount === "number" && (
                          <span>{s.messageCount} tin</span>
                        )}
                      </div>
                    </button>
                  )
                })
              )}
            </div>
          </aside>
        )}

        {/* Wide Central Chat Stream */}
        <ChatColumn
          chatMode={chatMode}
          onChatModeChange={setChatMode}
          primaryTicker={primaryTicker}
          onPrimaryTickerChange={setPrimaryTicker}
          compareTickers={compareTickers}
          onCompareTickersChange={setCompareTickers}
          availableTickers={availableTickers}
          enableKg={enableKg}
          onEnableKgChange={setEnableKg}
          asOfDate={asOfDate}
          todayDate={todayDate}
          onAsOfDateChange={setAsOfDate}
          messages={messages}
          selectedAssistantMessageId={selectedAssistantMessage?.id ?? null}
          onSelectAssistantMessage={handleSelectAssistantMessage}
          streaming={streaming}
          streamingText={streamingText}
          stages={stages}
          streamError={streamError}
          onSendPrompt={(q) => void handleSendPrompt(q)}
          onStopStream={handleStopStream}
          onBadgeClick={handleBadgeClick}
        />

        {/* Collapsible Right Slide-Over Evidence Drawer */}
        {drawerOpen && (
          <>
            <div
              onClick={() => setDrawerOpen(false)}
              aria-hidden="true"
              className="fixed inset-0 z-30 bg-ink/20 backdrop-blur-[1px] xl:hidden"
            />
            <aside
              aria-label="Ngăn bằng chứng và kiểm toán"
              className="fixed right-0 top-0 bottom-0 z-40 w-[min(92vw,480px)] shadow-xl xl:static xl:z-auto xl:w-[440px] 2xl:w-[500px] xl:shadow-none shrink-0 border-l border-line bg-surface h-full overflow-hidden"
            >
              <EvidenceInspector
                activeTab={activeTab}
                onTabChange={setActiveTab}
                metadata={activeMetadata}
                messageId={selectedAssistantMessage?.id ?? null}
                focus={inspectorFocus}
                evalSummary={evalSummary}
                onRefreshSummary={refreshEvalSummary}
                onClose={() => setDrawerOpen(false)}
                className="h-full border-0 rounded-none"
              />
            </aside>
          </>
        )}
      </div>
    </Panel>
  )
}
