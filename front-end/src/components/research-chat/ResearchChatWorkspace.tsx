"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Loader2, PanelLeftClose, SquarePen, Trash2 } from "lucide-react"
import {
  deduplicateActivityLog,
  deleteResearchSession,
  researchChatApi,
  streamResearchChat,
  type MemoryMode,
  type ResearchActivityItem,
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

function pickBestTabForMetadata(meta?: ResearchChatMetadata | null): InspectorTabId {
  if (!meta) return "tables"
  const citationsLen = Array.isArray(meta.citations) ? meta.citations.length : 0
  const tablesAndCalcs =
    (Array.isArray(meta.hydrated_tables) ? meta.hydrated_tables.length : 0) +
    (Array.isArray(meta.calculator_traces) ? meta.calculator_traces.length : 0) +
    (Array.isArray(meta.screening_results) ? meta.screening_results.length : 0)
  if (citationsLen > 0) return "quotes"
  if (tablesAndCalcs > 0) return "tables"
  if (Array.isArray(meta.graph_hops) && meta.graph_hops.length > 0) return "graph"
  return "tables"
}

export function ResearchChatWorkspace() {
  const todayDate = useMemo(() => vietnamDate() || "2026-11-20", [])
  const [memoryMode, setMemoryMode] = useState<MemoryMode>("auto")

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

  const [activeTab, setActiveTab] = useState<InspectorTabId>("tables")
  const [inspectorFocus, setInspectorFocus] = useState<InspectorFocus | null>(null)

  const abortRef = useRef<AbortController | null>(null)

  const loadSessionDetail = useCallback(async (sessionId: string) => {
    try {
      const detail = await researchChatApi.getSession(sessionId)
      const list = Array.isArray(detail.messages) ? detail.messages : []
      setActiveSessionId(detail.session?.id ?? sessionId)
      setMessages(list)
      const lastAssistant = [...list].reverse().find((m) => m.role === "assistant")
      setSelectedAssistantMessageId(lastAssistant?.id ?? null)
      setActiveTab(pickBestTabForMetadata(lastAssistant?.metadata))
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
      try {
        const res = await researchChatApi.listSessions()
        if (!cancelled) {
          setSessions(Array.isArray(res.sessions) ? res.sessions : [])
        }
      } catch {
        // Ignore transient initial load error
      }
    }
    void init()
    return () => {
      cancelled = true
      abortRef.current?.abort()
    }
  }, [])

  const handleStartNewConversation = useCallback(() => {
    if (streaming || creatingSession) return
    setActiveSessionId(null)
    setMessages([])
    setSelectedAssistantMessageId(null)
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

      let sessionId = activeSessionId
      if (!sessionId) {
        setCreatingSession(true)
        try {
          const created = await researchChatApi.createSession({
            title: clean.slice(0, 56),
            mode: "GENERAL",
            asOfDate: todayDate,
            enableKg: true,
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
            mode: "GENERAL",
            asOfDate: todayDate,
            enableKg: true,
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
              setActiveTab(pickBestTabForMetadata(doneMeta))
              setStages(createInitialStages())
              void refreshSessions()
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
            : "\n\n*(Luồng bị ngắt trong khi đang suy luận trước khi xuất câu trả lời.)*"
          setMessages((prev) => [
            ...prev,
            {
              id: fallbackId,
              sessionId: sessionId ?? undefined,
              role: "assistant",
              content: `> **[Chưa hoàn tất]** Kết nối bị ngắt trước khi hoàn tất kiểm chứng.${partialBody}`,
              metadata: {
                activity_log: deduplicateActivityLog(accumulatedActivity),
                declared_blindspots: [
                  {
                    code: "STREAM_INTERRUPTED_UNVERIFIED",
                    reason: "Luồng kết thúc sớm trước khi hoàn tất.",
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
          const msg = err instanceof Error ? err.message : "Kết nối bị gián đoạn."
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
    [activeSessionId, memoryMode, refreshSessions, streaming, todayDate],
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
          setStages(createInitialStages())
          setInspectorFocus(null)
        }
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
    [activeSessionId, deletingSessionId, streaming],
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
    if (nextFocus && (nextFocus.tab === "quotes" || nextFocus.tab === "tables" || nextFocus.tab === "graph")) {
      setActiveTab(nextFocus.tab)
      setInspectorFocus(nextFocus)
    } else {
      setActiveTab("tables")
      setInspectorFocus(null)
    }
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

  const handleSelectAssistantMessage = useCallback(
    (messageId: string) => {
      setSelectedAssistantMessageId(messageId)
      const target = messages.find((m) => m.id === messageId)
      setActiveTab(pickBestTabForMetadata(target?.metadata ?? activeMetadata))
      setDrawerOpen(true)
    },
    [activeMetadata, messages],
  )

  const handleToggleDrawer = useCallback(() => {
    setDrawerOpen((prev) => {
      const next = !prev
      if (next) {
        setActiveTab(pickBestTabForMetadata(activeMetadata))
      }
      return next
    })
  }, [activeMetadata])

  const evidenceCount = useMemo(() => {
    if (!activeMetadata) return 0
    return (
      (Array.isArray(activeMetadata.citations) ? activeMetadata.citations.length : 0) +
      (Array.isArray(activeMetadata.hydrated_tables) ? activeMetadata.hydrated_tables.length : 0) +
      (Array.isArray(activeMetadata.screening_results) ? activeMetadata.screening_results.length : 0) +
      (Array.isArray(activeMetadata.calculator_traces) ? activeMetadata.calculator_traces.length : 0) +
      (Array.isArray(activeMetadata.graph_hops) ? activeMetadata.graph_hops.length : 0)
    )
  }, [activeMetadata])

  return (
    <div className="relative flex h-dvh w-full bg-surface overflow-hidden">
      {/* Left Minimalist ChatGPT-Style Conversation Sidebar */}
      {sidebarOpen && (
        <aside
          aria-label="Lịch sử hội thoại nghiên cứu"
          className="w-[244px] shrink-0 border-r border-line/55 bg-[#f9faf9] flex flex-col overflow-hidden"
        >
          <div className="h-12 px-2.5 flex items-center justify-between gap-1 shrink-0">
            <button
              type="button"
              onClick={handleStartNewConversation}
              disabled={streaming || creatingSession}
              className="flex-1 inline-flex items-center gap-2.5 rounded-[9px] px-2.5 h-8.5 text-[13.5px] font-normal text-ink hover:bg-soft/85 transition-colors disabled:opacity-45 cursor-pointer text-left"
            >
              <SquarePen className="h-[15px] w-[15px] text-secondary shrink-0" aria-hidden="true" />
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
            <div className="px-2.5 pt-3 pb-1.5 text-[11.5px] font-medium text-muted select-none">
              Gần đây
            </div>

            {sessions.length === 0 ? (
              <div className="px-2.5 py-2 text-[12.5px] text-muted/80 select-none">
                Chưa có cuộc trò chuyện
              </div>
            ) : (
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
            )}
          </div>
        </aside>
      )}

      {/* Main Pure-White Chat Canvas */}
      <div className="flex-1 min-w-0 flex flex-col h-full bg-surface overflow-hidden">
        <TopControlBar
          memoryMode={memoryMode}
          onMemoryModeChange={setMemoryMode}
          memoryModeDisabled={streaming}
          sidebarOpen={sidebarOpen}
          onToggleSidebar={() => setSidebarOpen((v) => !v)}
          onStartNewConversation={handleStartNewConversation}
          newConversationDisabled={streaming || creatingSession}
          drawerOpen={drawerOpen}
          onToggleDrawer={handleToggleDrawer}
          evidenceCount={evidenceCount}
        />

        <ChatColumn
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
            aria-label="Nguồn và số liệu đối chiếu"
            className="fixed right-0 top-0 bottom-0 z-40 w-[min(92vw,460px)] shadow-xl xl:static xl:z-auto xl:w-[420px] 2xl:w-[460px] xl:shadow-none shrink-0 border-l border-line/70 bg-surface h-full overflow-hidden"
          >
            <EvidenceInspector
              activeTab={activeTab}
              onTabChange={setActiveTab}
              metadata={activeMetadata}
              focus={inspectorFocus}
              onClose={() => setDrawerOpen(false)}
              className="h-full"
            />
          </aside>
        </>
      )}
    </div>
  )
}
