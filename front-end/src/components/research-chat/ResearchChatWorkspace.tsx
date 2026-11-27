"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Loader2, PanelLeftClose, SquarePen, Trash2 } from "lucide-react"
import {
  deduplicateActivityLog,
  deleteResearchSession,
  researchChatApi,
  streamResearchChat,
  type EvalSummaryResponse,
  type MemoryMode,
  type ResearchActivityItem,
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
  const [chatMode, setChatMode] = useState<ResearchChatMode>("GENERAL")
  const [memoryMode, setMemoryMode] = useState<MemoryMode>("auto")
  const [primaryTicker, setPrimaryTicker] = useState("")
  const [compareTickers, setCompareTickers] = useState<string[]>([])
  const [enableKg, setEnableKg] = useState(true)
  const [asOfDate, setAsOfDate] = useState(todayDate)

  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [drawerOpen, setDrawerOpen] = useState(false)

  const [sessions, setSessions] = useState<ResearchSessionSummary[]>([])
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null)
  const [creatingSession, setCreatingSession] = useState(false)
  const [confirmDeleteSessionId, setConfirmDeleteSessionId] = useState<string | null>(null)
  const [deletingSessionId, setDeletingSessionId] = useState<string | null>(null)
  const [messages, setMessages] = useState<ResearchChatMessage[]>([])
  const [selectedAssistantMessageId, setSelectedAssistantMessageId] = useState<string | null>(null)

  const [streaming, setStreaming] = useState(false)
  const [streamingText, setStreamingText] = useState("")
  const [liveActivity, setLiveActivity] = useState<ResearchActivityItem[]>([])
  const [streamStartedAt, setStreamStartedAt] = useState<number | null>(null)
  const [stages, setStages] = useState<StageProgressItem[]>(createInitialStages)
  const [streamError, setStreamError] = useState<string | null>(null)

  const [activeTab, setActiveTab] = useState<InspectorTabId>("quotes")
  const [inspectorFocus, setInspectorFocus] = useState<InspectorFocus | null>(null)
  const [memoryRefreshNonce, setMemoryRefreshNonce] = useState(0)
  const [evalSummary, setEvalSummary] = useState<EvalSummaryResponse | null>(null)
  const [availableTickers, setAvailableTickers] = useState<string[]>([...PILOT_TICKERS])

  const abortRef = useRef<AbortController | null>(null)

  const refreshIngestedTickers = useCallback(async () => {
    try {
      const res = await researchChatApi.listIngestedTickers()
      const live =
        Array.isArray(res?.tickers) && res.tickers.length > 0
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
      setStages(createInitialStages())
      setInspectorFocus(null)
      setConfirmDeleteSessionId(null)
      setStreamError(null)
    } catch (err) {
      const apiErr = err as { code?: unknown; status?: unknown; message?: unknown } | null
      const isNotFound = apiErr?.status === 404 || apiErr?.code === "SESSION_NOT_FOUND"
      const msg = isNotFound
        ? "Phiên nghiên cứu này không còn tồn tại hoặc đã bị xóa."
        : apiErr && typeof apiErr.message === "string" && apiErr.message.trim()
          ? apiErr.message
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
      setLiveActivity([])
      setStreamStartedAt(Date.now())
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
      let accumulatedActivity: ResearchActivityItem[] = []
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
            memoryMode,
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
            onThought: (evt) => {
              setLiveActivity((prev) => {
                let next: ResearchActivityItem[]
                if (evt.append && evt.id) {
                  const idx = prev.findIndex((item) => item.id === evt.id)
                  if (idx !== -1) {
                    next = prev.map((item, i) =>
                      i === idx
                        ? {
                            ...item,
                            text: `${item.text ?? ""}${evt.text ?? ""}`,
                            timestamp_ms: evt.timestamp_ms ?? item.timestamp_ms,
                          }
                        : item,
                    )
                  } else {
                    next = [...prev, { ...evt, append: false }]
                  }
                } else {
                  const idx = prev.findIndex((item) => item.id === evt.id)
                  if (idx !== -1) {
                    next = prev.map((item, i) => (i === idx ? { ...item, ...evt } : item))
                  } else {
                    next = [...prev, evt]
                  }
                }
                accumulatedActivity = next
                return next
              })
            },
            onToolCall: (evt) => {
              setLiveActivity((prev) => {
                const matchKey = evt.call_id || evt.id
                const idx = prev.findIndex((item) => (item.call_id || item.id) === matchKey)
                const next =
                  idx !== -1
                    ? prev.map((item, i) => (i === idx ? { ...item, ...evt } : item))
                    : [...prev, evt]
                accumulatedActivity = next
                return next
              })
            },
            onToolResult: (evt) => {
              setLiveActivity((prev) => {
                const matchKey = evt.call_id || evt.id
                const idx = prev.findIndex((item) => (item.call_id || item.id) === matchKey)
                const next =
                  idx !== -1
                    ? prev.map((item, i) => (i === idx ? { ...item, ...evt } : item))
                    : [...prev, evt]
                accumulatedActivity = next
                return next
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
              const doneMeta: ResearchChatMetadata = {
                ...(done.metadata ?? {}),
                activity_log: deduplicateActivityLog(
                  Array.isArray(done.metadata?.activity_log) && done.metadata.activity_log.length > 0
                    ? done.metadata.activity_log
                    : accumulatedActivity,
                ),
              }
              const assistantMsg: ResearchChatMessage = {
                id: assistantId,
                sessionId: sessionId ?? undefined,
                role: "assistant",
                content: finalContent,
                metadata: doneMeta,
                createdAt: new Date().toISOString(),
              }
              setMessages((prev) => [...prev, assistantMsg])
              setSelectedAssistantMessageId(assistantId)
              setStages(createInitialStages())
              void refreshSessions()
              void refreshEvalSummary()
              setMemoryRefreshNonce((n) => n + 1)
            },
            onError: (err) => {
              setStreamError(err.code ? `[${err.code}] ${err.message}` : err.message)
              setStages((prev) =>
                prev.map((item) => (item.status === "running" ? { ...item, status: "error" } : item)),
              )
            },
          },
          controller.signal,
        )

        if (
          !finished &&
          (accumulatedText.trim() || accumulatedActivity.length > 0) &&
          !controller.signal.aborted
        ) {
          const fallbackId = `local-assistant-${Date.now()}`
          const partialBody = accumulatedText.trim()
            ? `\n\n${accumulatedText}`
            : "\n\n*(Luồng bị ngắt trong khi đang suy luận / gọi công cụ trước khi xuất câu trả lời.)*"
          setMessages((prev) => [
            ...prev,
            {
              id: fallbackId,
              sessionId: sessionId ?? undefined,
              role: "assistant",
              content: `> **[Chưa hoàn tất kiểm chứng]** Kết nối SSE bị ngắt trước Pha 7 (quote_hash & numeric audit). Nội dung bên dưới chưa qua xác thực và chưa được lưu trữ.${partialBody}`,
              metadata: {
                activity_log: deduplicateActivityLog(accumulatedActivity),
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
          const msg = err instanceof Error ? err.message : "Kết nối SSE bị gián đoạn."
          setStreamError((prev) => prev || msg)
        }
      } finally {
        abortRef.current = null
        setStreaming(false)
        setStreamingText("")
        setLiveActivity([])
        setStreamStartedAt(null)
      }
    },
    [
      activeSessionId,
      asOfDate,
      chatMode,
      compareTickers,
      enableKg,
      memoryMode,
      primaryTicker,
      refreshEvalSummary,
      refreshSessions,
      streaming,
    ],
  )

  const handleDeleteSession = useCallback(
    async (sessionId: string) => {
      if (streaming || deletingSessionId) return
      setDeletingSessionId(sessionId)
      setStreamError(null)
      try {
        await deleteResearchSession(sessionId)
        setSessions((prev) => prev.filter((s) => s.id !== sessionId))
        setConfirmDeleteSessionId(null)
        if (activeSessionId === sessionId) {
          setActiveSessionId(null)
          setMessages([])
          setSelectedAssistantMessageId(null)
          setPrimaryTicker("")
          setCompareTickers([])
          setStages(createInitialStages())
          setInspectorFocus(null)
        }
        void refreshEvalSummary()
        setMemoryRefreshNonce((n) => n + 1)
      } catch (err) {
        const msg =
          err && typeof err === "object" && "message" in err
            ? String((err as { message: unknown }).message)
            : "Không thể xóa phiên nghiên cứu."
        setStreamError(msg)
      } finally {
        setDeletingSessionId(null)
      }
    },
    [activeSessionId, deletingSessionId, refreshEvalSummary, streaming],
  )

  const handleSelectLinkedSession = useCallback(
    (sessionId: string) => {
      if (streaming || !sessionId) return
      if (typeof window !== "undefined" && window.innerWidth < 1280) {
        setDrawerOpen(false)
      }
      void loadSessionDetail(sessionId)
    },
    [loadSessionDetail, streaming],
  )

  const handleStopStream = useCallback(() => {
    abortRef.current?.abort()
    setStreaming(false)
  }, [])

  const handleBadgeClick = useCallback((badge: AuditBadgeToken, messageId?: string) => {
    if (messageId) setSelectedAssistantMessageId(messageId)
    const nextFocus = badgeToInspectorFocus(badge, Date.now())
    if (nextFocus) {
      setActiveTab(nextFocus.tab)
      setInspectorFocus(nextFocus)
    }
    setDrawerOpen(true)
  }, [])

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

  const activeMetadata: ResearchChatMetadata | null = selectedAssistantMessage?.metadata ?? null

  const evidenceCount = useMemo(() => {
    if (!activeMetadata) return 0
    return (
      (Array.isArray(activeMetadata.citations) ? activeMetadata.citations.length : 0) +
      (Array.isArray(activeMetadata.hydrated_tables) ? activeMetadata.hydrated_tables.length : 0) +
      (Array.isArray(activeMetadata.screening_results) ? activeMetadata.screening_results.length : 0) +
      (Array.isArray(activeMetadata.calculator_traces) ? activeMetadata.calculator_traces.length : 0) +
      (Array.isArray(activeMetadata.graph_hops) ? activeMetadata.graph_hops.length : 0) +
      (Array.isArray(activeMetadata.recalled_memories) ? activeMetadata.recalled_memories.length : 0) +
      (Array.isArray(activeMetadata.linked_sessions) ? activeMetadata.linked_sessions.length : 0) +
      (activeMetadata.portfolio_snapshot_used ? 1 : 0)
    )
  }, [activeMetadata])

  return (
    <div className="relative flex h-[calc(100dvh-48px)] w-full bg-surface overflow-hidden">
      {/* Left Minimalist ChatGPT-Style Conversation Sidebar */}
      {sidebarOpen && (
        <aside
          aria-label="Lịch sử hội thoại nghiên cứu"
          className="w-[252px] shrink-0 border-r border-line/60 bg-[#f9faf9] flex flex-col overflow-hidden"
        >
          <div className="h-13 px-2.5 flex items-center justify-between gap-1 shrink-0">
            <button
              type="button"
              onClick={handleStartNewConversation}
              disabled={streaming || creatingSession}
              className="flex-1 inline-flex items-center gap-2.5 rounded-[9px] px-2.5 h-9 text-[13.5px] font-normal text-ink hover:bg-soft/85 transition-colors disabled:opacity-45 cursor-pointer text-left"
            >
              <SquarePen className="h-[16px] w-[16px] text-secondary shrink-0" aria-hidden="true" />
              <span className="truncate">Trò chuyện mới</span>
            </button>

            <button
              type="button"
              onClick={() => setSidebarOpen(false)}
              aria-label="Thu gọn danh sách hội thoại"
              title="Thu gọn sidebar"
              className="inline-flex h-8 w-8 items-center justify-center rounded-[8px] text-secondary hover:bg-soft/85 hover:text-ink transition-colors shrink-0 cursor-pointer"
            >
              <PanelLeftClose className="h-[17px] w-[17px]" aria-hidden="true" />
            </button>
          </div>

          <div className="flex-1 overflow-y-auto px-2 pb-4">
            {sessions.length > 0 && (
              <div className="px-2.5 pt-3 pb-1.5 text-[11.5px] font-medium text-muted select-none">
                Gần đây
              </div>
            )}

            <div className="space-y-0.5">
              {sessions.map((s) => {
                const isActive = s.id === activeSessionId
                const isConfirmingDelete = confirmDeleteSessionId === s.id
                const isDeleting = deletingSessionId === s.id
                const sessionTitle = s.title || `Phiên ${s.id.slice(0, 8)}`
                return (
                  <div
                    key={s.id}
                    className={`group rounded-[9px] transition-colors ${
                      isActive ? "bg-[#e9ecea] text-ink" : "text-ink/85 hover:bg-soft/80"
                    }`}
                  >
                    <div className="flex items-center gap-1 px-2.5 h-9">
                      <button
                        type="button"
                        disabled={streaming || isDeleting}
                        onClick={() => {
                          setConfirmDeleteSessionId(null)
                          void loadSessionDetail(s.id)
                        }}
                        className="flex-1 min-w-0 text-left text-[13.5px] truncate disabled:opacity-50 cursor-pointer"
                      >
                        {sessionTitle}
                      </button>

                      <button
                        type="button"
                        disabled={streaming || Boolean(deletingSessionId)}
                        onClick={(e) => {
                          e.stopPropagation()
                          setConfirmDeleteSessionId(isConfirmingDelete ? null : s.id)
                        }}
                        title="Xóa cuộc trò chuyện"
                        aria-label={`Xóa phiên ${sessionTitle}`}
                        className={`shrink-0 inline-flex h-6 w-6 items-center justify-center rounded-[6px] text-muted hover:bg-loss/12 hover:text-loss transition-opacity cursor-pointer disabled:opacity-40 ${
                          isConfirmingDelete
                            ? "opacity-100 text-loss bg-loss/10"
                            : "opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 focus-visible:opacity-100"
                        }`}
                      >
                        <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                      </button>
                    </div>

                    {isConfirmingDelete && (
                      <div className="mx-2 mb-2 rounded-[7px] border border-loss/30 bg-surface px-2.5 py-1.5 flex items-center justify-between gap-1.5 text-[11.5px]">
                        <span className="text-loss font-medium truncate">Xóa?</span>
                        <div className="flex items-center gap-1 shrink-0">
                          <button
                            type="button"
                            disabled={isDeleting}
                            onClick={() => setConfirmDeleteSessionId(null)}
                            className="rounded px-1.5 py-0.5 text-secondary hover:text-ink cursor-pointer"
                          >
                            Hủy
                          </button>
                          <button
                            type="button"
                            disabled={isDeleting}
                            onClick={() => void handleDeleteSession(s.id)}
                            className="inline-flex items-center gap-1 rounded bg-loss text-paper px-2 py-0.5 font-medium hover:opacity-90 disabled:opacity-50 cursor-pointer"
                          >
                            {isDeleting && (
                              <Loader2 className="h-2.5 w-2.5 animate-spin" aria-hidden="true" />
                            )}
                            <span>Xóa</span>
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        </aside>
      )}

      {/* Main Pure-White Chat Canvas */}
      <div className="flex-1 min-w-0 flex flex-col h-full bg-surface overflow-hidden">
        <TopControlBar
          chatMode={chatMode}
          onChatModeChange={setChatMode}
          modeDisabled={streaming}
          memoryMode={memoryMode}
          sidebarOpen={sidebarOpen}
          onToggleSidebar={() => setSidebarOpen((v) => !v)}
          onStartNewConversation={handleStartNewConversation}
          newConversationDisabled={streaming || creatingSession}
          drawerOpen={drawerOpen}
          onToggleDrawer={() => setDrawerOpen((v) => !v)}
          evidenceCount={evidenceCount}
        />

        <ChatColumn
          chatMode={chatMode}
          memoryMode={memoryMode}
          onMemoryModeChange={setMemoryMode}
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
          onSelectAssistantMessage={handleSelectAssistantMessage}
          streaming={streaming}
          streamingText={streamingText}
          liveActivity={liveActivity}
          streamStartedAt={streamStartedAt}
          stages={stages}
          streamError={streamError}
          onSendPrompt={(q) => void handleSendPrompt(q)}
          onStopStream={handleStopStream}
          onBadgeClick={handleBadgeClick}
          onSelectLinkedSession={handleSelectLinkedSession}
        />
      </div>

      {/* Collapsible Right Slide-Over Evidence Drawer */}
      {drawerOpen && (
        <>
          <div
            onClick={() => setDrawerOpen(false)}
            aria-hidden="true"
            className="fixed inset-0 z-30 bg-ink/15 backdrop-blur-[1px] xl:hidden"
          />
          <aside
            aria-label="Ngăn bằng chứng và kiểm toán"
            className="fixed right-0 top-0 bottom-0 z-40 w-[min(92vw,480px)] shadow-xl xl:static xl:z-auto xl:w-[440px] 2xl:w-[480px] xl:shadow-none shrink-0 border-l border-line/75 bg-surface h-full overflow-hidden"
          >
            <EvidenceInspector
              activeTab={activeTab}
              onTabChange={setActiveTab}
              metadata={activeMetadata}
              messageId={selectedAssistantMessage?.id ?? null}
              activeSessionId={activeSessionId}
              memoryRefreshNonce={memoryRefreshNonce}
              onSelectLinkedSession={handleSelectLinkedSession}
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
  )
}
