"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  AlertTriangle,
  ArrowUpRight,
  Brain,
  Briefcase,
  Check,
  Edit3,
  History,
  Loader2,
  Pin,
  PinOff,
  RefreshCw,
  Trash2,
  X,
} from "lucide-react"
import { Button, Pill } from "@/components/ui"
import {
  clearAllUserMemories,
  deleteUserMemory,
  getRelatedSessions,
  listUserMemories,
  patchUserMemory,
  type LinkedSessionItem,
  type RecalledMemoryItem,
  type RelatedSessionsResponse,
  type ResearchChatMetadata,
  type UserMemoryItem,
} from "@/lib/api/researchChat"
import type { InspectorFocus } from "./types"

type MemoryStatusFilter = "active" | "stale" | "superseded"

const MEMORY_TYPE_LABELS: Record<string, { label: string; tone: string }> = {
  STYLE_PREF_SAFE: { label: "Định dạng", tone: "teal" },
  USER_INVESTING_CRITERIA: { label: "Tiêu chí", tone: "mineral" },
  USER_EXTERNAL_HOLDING: { label: "Danh mục ngoài", tone: "gold" },
  INVESTMENT_THESIS: { label: "Luận điểm", tone: "mineral" },
  SESSION_EPISODE_DIGEST: { label: "Tóm tắt phiên", tone: "neutral" },
}

const RELATION_TYPE_LABELS: Record<string, string> = {
  SESSION_CONTINUED_FROM: "Tiếp nối phiên",
  SESSION_THESIS_EVOLUTION: "Tiến hóa luận điểm",
  SESSION_RELATED_THEME: "Cùng chủ đề / Vĩ mô",
}

function getErrorMessage(err: unknown, fallback: string): string {
  if (err && typeof err === "object") {
    const maybeResp = (err as { response?: { data?: { error?: unknown; message?: unknown; detail?: unknown } } }).response
    const serverMsg = maybeResp?.data?.error ?? maybeResp?.data?.message ?? maybeResp?.data?.detail
    if (typeof serverMsg === "string" && serverMsg.trim()) return serverMsg
    if ("message" in err && typeof (err as { message: unknown }).message === "string") {
      return (err as { message: string }).message
    }
  }
  return fallback
}

function formatStalenessLabel(status?: string | null): string {
  if (status === "STALE_RESTATED") return "[CẦN ĐỐI CHIẾU LẠI · BCTC Đính chính]"
  if (status === "STALE_NEW_FILING") return "[CẦN ĐỐI CHIẾU LẠI · Có BCTC mới]"
  return "[CẦN ĐỐI CHIẾU LẠI]"
}

function normalizeMemoryBadgeId(raw?: string | null): string {
  return String(raw ?? "")
    .trim()
    .replace(/^MEM\s*:\s*/i, "")
    .toUpperCase()
}

function normalizeSessionBadgeId(raw?: string | null): string {
  return String(raw ?? "")
    .trim()
    .replace(/^SESS\s*:\s*/i, "")
    .toUpperCase()
}

interface InspectorMemoryTabProps {
  metadata: ResearchChatMetadata | null
  activeSessionId: string | null
  messageId?: string | null
  refreshNonce?: number
  focus: InspectorFocus | null
  onSelectLinkedSession: (sessionId: string) => void
}

export function InspectorMemoryTab({
  metadata,
  activeSessionId,
  messageId,
  refreshNonce = 0,
  focus,
  onSelectLinkedSession,
}: InspectorMemoryTabProps) {
  const recalledMemories: RecalledMemoryItem[] = useMemo(
    () => (Array.isArray(metadata?.recalled_memories) ? metadata.recalled_memories : []),
    [metadata],
  )
  const linkedSessions: LinkedSessionItem[] = useMemo(
    () => (Array.isArray(metadata?.linked_sessions) ? metadata.linked_sessions : []),
    [metadata],
  )
  const portfolioSnapshotUsed = Boolean(metadata?.portfolio_snapshot_used)

  const memItemRefs = useRef<Record<string, HTMLDivElement | null>>({})
  const sessItemRefs = useRef<Record<string, HTMLDivElement | null>>({})
  const portfolioRef = useRef<HTMLDivElement | null>(null)
  const turnSectionRef = useRef<HTMLElement | null>(null)
  const relatedSectionRef = useRef<HTMLElement | null>(null)

  const hasExactSessionMatch = useMemo(() => {
    if (!focus || focus.tab !== "memory" || !focus.sessionBadgeId) return false
    const want = normalizeSessionBadgeId(focus.sessionBadgeId)
    return linkedSessions.some((s) => normalizeSessionBadgeId(s.badge_id) === want)
  }, [focus, linkedSessions])

  const highlightRelatedSectionFallback = Boolean(
    focus?.tab === "memory" && focus.sessionBadgeId && !hasExactSessionMatch,
  )

  useEffect(() => {
    if (!focus || focus.tab !== "memory") return
    if (focus.memoryBadgeId) {
      const want = normalizeMemoryBadgeId(focus.memoryBadgeId)
      const target = recalledMemories.find((m) => normalizeMemoryBadgeId(m.badge_id) === want)
      if (target) {
        memItemRefs.current[want]?.scrollIntoView({
          behavior: "smooth",
          block: "nearest",
        })
      } else {
        turnSectionRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" })
      }
      return
    }
    if (focus.sessionBadgeId) {
      const want = normalizeSessionBadgeId(focus.sessionBadgeId)
      const target = linkedSessions.find((s) => normalizeSessionBadgeId(s.badge_id) === want)
      if (target) {
        sessItemRefs.current[want]?.scrollIntoView({
          behavior: "smooth",
          block: "nearest",
        })
      } else {
        relatedSectionRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" })
      }
      return
    }
    if (focus.portfolioSymbol && portfolioRef.current) {
      portfolioRef.current.scrollIntoView({ behavior: "smooth", block: "nearest" })
    }
  }, [focus, recalledMemories, linkedSessions])

  // ── Section 2: Related Sessions (Cross-Session Graph) ──
  const [relatedData, setRelatedData] = useState<RelatedSessionsResponse | null>(null)
  const [relatedLoading, setRelatedLoading] = useState(false)
  const [relatedError, setRelatedError] = useState<string | null>(null)
  const relatedReqSeqRef = useRef(0)

  const fetchRelated = useCallback(async (sessionId: string | null) => {
    const reqId = ++relatedReqSeqRef.current
    if (!sessionId) {
      setRelatedData(null)
      setRelatedError(null)
      setRelatedLoading(false)
      return
    }
    setRelatedLoading(true)
    setRelatedError(null)
    try {
      const data = await getRelatedSessions(sessionId, 6)
      if (reqId !== relatedReqSeqRef.current) return
      setRelatedData(data)
    } catch (err) {
      if (reqId !== relatedReqSeqRef.current) return
      setRelatedError(getErrorMessage(err, "Không thể tải danh sách phiên liên quan."))
    } finally {
      if (reqId === relatedReqSeqRef.current) {
        setRelatedLoading(false)
      }
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    async function load() {
      await Promise.resolve()
      if (cancelled) return
      await fetchRelated(activeSessionId)
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [activeSessionId, messageId, refreshNonce, fetchRelated])

  // ── Section 3: Long-Term User Memory Management ──
  const [statusFilter, setStatusFilter] = useState<MemoryStatusFilter>("active")
  const statusFilterRef = useRef<MemoryStatusFilter>("active")
  useEffect(() => {
    statusFilterRef.current = statusFilter
  }, [statusFilter])
  const [memories, setMemories] = useState<UserMemoryItem[]>([])
  const [memoriesTotal, setMemoriesTotal] = useState(0)
  const [memoriesLoading, setMemoriesLoading] = useState(false)
  const [memoriesError, setMemoriesError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const memoriesReqSeqRef = useRef(0)

  const [editingId, setEditingId] = useState<string | null>(null)
  const [editDraft, setEditDraft] = useState("")
  const [busyMemoryId, setBusyMemoryId] = useState<string | null>(null)
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null)
  const [confirmClearAll, setConfirmClearAll] = useState(false)
  const [clearingAll, setClearingAll] = useState(false)

  const fetchMemories = useCallback(async (status: MemoryStatusFilter) => {
    const reqId = ++memoriesReqSeqRef.current
    setMemoriesLoading(true)
    setMemoriesError(null)
    try {
      const res = await listUserMemories({ status, limit: 50 })
      if (reqId !== memoriesReqSeqRef.current) return
      setMemories(Array.isArray(res?.items) ? res.items : [])
      setMemoriesTotal(typeof res?.total === "number" ? res.total : 0)
    } catch (err) {
      if (reqId !== memoriesReqSeqRef.current) return
      setMemoriesError(getErrorMessage(err, "Không thể tải danh sách ký ức dài hạn."))
    } finally {
      if (reqId === memoriesReqSeqRef.current) {
        setMemoriesLoading(false)
      }
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    async function load() {
      await Promise.resolve()
      if (cancelled) return
      await fetchMemories(statusFilter)
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [statusFilter, refreshNonce, fetchMemories])

  const handleTogglePin = async (item: UserMemoryItem) => {
    setBusyMemoryId(item.memory_id)
    setActionError(null)
    try {
      const updated = await patchUserMemory(item.memory_id, {
        pinned_by_user: !item.pinned_by_user,
        reason: item.pinned_by_user ? "USER_UNPIN_UI" : "USER_PIN_UI",
      })
      setMemories((prev) =>
        prev
          .map((m) => (m.memory_id === updated.memory_id ? updated : m))
          .sort((a, b) => Number(b.pinned_by_user) - Number(a.pinned_by_user)),
      )
    } catch (err) {
      setActionError(getErrorMessage(err, "Không thể cập nhật trạng thái ghim ký ức."))
    } finally {
      setBusyMemoryId(null)
    }
  }

  const handleStartEdit = (item: UserMemoryItem) => {
    setEditingId(item.memory_id)
    setEditDraft(item.content_text)
    setConfirmDeleteId(null)
    setActionError(null)
  }

  const handleSaveEdit = async (memoryId: string) => {
    const clean = editDraft.trim()
    if (!clean) {
      setActionError("Nội dung ký ức không được để trống.")
      return
    }
    setBusyMemoryId(memoryId)
    setActionError(null)
    try {
      const updated = await patchUserMemory(memoryId, {
        content_text: clean,
        reason: "USER_INLINE_EDIT_UI",
      })
      setMemories((prev) => prev.map((m) => (m.memory_id === updated.memory_id ? updated : m)))
      setEditingId(null)
      setEditDraft("")
    } catch (err) {
      setActionError(getErrorMessage(err, "Không thể lưu nội dung ký ức đã chỉnh sửa."))
    } finally {
      setBusyMemoryId(null)
    }
  }

  const handleDeleteSingle = async (memoryId: string) => {
    const filterAtStart = statusFilterRef.current
    setBusyMemoryId(memoryId)
    setActionError(null)
    try {
      await deleteUserMemory(memoryId)
      if (statusFilterRef.current === filterAtStart) {
        setMemories((prev) => prev.filter((m) => m.memory_id !== memoryId))
        setMemoriesTotal((prev) => Math.max(0, prev - 1))
      }
      setConfirmDeleteId(null)
    } catch (err) {
      setActionError(getErrorMessage(err, "Không thể xóa ký ức này."))
    } finally {
      setBusyMemoryId(null)
    }
  }

  const handleClearAll = async () => {
    setClearingAll(true)
    setActionError(null)
    try {
      await clearAllUserMemories()
      setConfirmClearAll(false)
      setEditingId(null)
      setConfirmDeleteId(null)
      await Promise.all([fetchMemories(statusFilter), fetchRelated(activeSessionId)])
    } catch (err) {
      setActionError(getErrorMessage(err, "Không thể xóa toàn bộ ký ức."))
    } finally {
      setClearingAll(false)
    }
  }

  const filterTabs: Array<{ id: MemoryStatusFilter; label: string }> = [
    { id: "active", label: "Đang dùng" },
    { id: "stale", label: "Cần đối chiếu" },
    { id: "superseded", label: "Đã thay thế" },
  ]

  return (
    <div className="space-y-4">
      {/* Section 1: Ký ức & Phiên liên kết của lượt này */}
      <section
        ref={turnSectionRef}
        className="rounded-[9px] border border-line bg-surface p-3.5 space-y-3"
      >
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-1.5 text-[12px] font-semibold text-ink">
            <Brain className="h-3.5 w-3.5 text-ceiling" aria-hidden="true" />
            <span>Ký ức & Phiên liên kết của lượt này</span>
          </div>
          <span className="font-mono text-[10.5px] text-muted tnum">
            {recalledMemories.length} MEM · {linkedSessions.length} SESS
          </span>
        </div>

        {portfolioSnapshotUsed && (
          <div
            ref={portfolioRef}
            className={`rounded-[7px] border px-2.5 py-2 flex items-center justify-between gap-2 text-[11.5px] transition-all ${
              focus?.tab === "memory" && focus.portfolioSymbol
                ? "border-gain bg-gain/12 ring-2 ring-gain/20"
                : "border-gain/30 bg-gain/8"
            }`}
          >
            <div className="flex items-center gap-1.5 min-w-0 text-gain font-medium">
              <Briefcase className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              <span className="truncate">
                Đã gắn Danh mục trực tiếp từ AIInvest Core DB ([PORT:*])
              </span>
            </div>
            <Pill tone="gain">Live Only</Pill>
          </div>
        )}

        {recalledMemories.length === 0 && linkedSessions.length === 0 && !portfolioSnapshotUsed ? (
          <p className="text-[12px] text-muted py-2">
            Lượt trả lời này không triệu hồi ký ức dài hạn hoặc phiên liên kết nào.
          </p>
        ) : (
          <div className="space-y-2.5">
            {recalledMemories.map((mem) => {
              const badgeKey = normalizeMemoryBadgeId(mem.badge_id)
              const highlighted =
                focus?.tab === "memory" &&
                normalizeMemoryBadgeId(focus.memoryBadgeId) === badgeKey
              const typeMeta = MEMORY_TYPE_LABELS[mem.memory_type] ?? {
                label: mem.memory_type,
                tone: "neutral",
              }
              const isStale = Boolean(mem.staleness_status && mem.staleness_status !== "FRESH")

              return (
                <div
                  key={`${mem.badge_id}-${mem.memory_id}`}
                  ref={(el) => {
                    memItemRefs.current[badgeKey] = el
                  }}
                  className={`rounded-[8px] border p-2.5 space-y-1.5 transition-all ${
                    highlighted
                      ? "border-mineral bg-mineral/6 ring-2 ring-mineral/20"
                      : "border-line bg-paper/75"
                  }`}
                >
                  <div className="flex flex-wrap items-center justify-between gap-1.5">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="inline-flex items-center gap-1 rounded bg-ceiling/10 border border-ceiling/25 px-1.5 py-0.5 font-mono text-[10.5px] font-semibold text-ceiling">
                        [MEM:{badgeKey}]
                      </span>
                      <Pill tone={typeMeta.tone}>{typeMeta.label}</Pill>
                      {mem.pinned_by_user && (
                        <span
                          title="Ký ức đã được bạn ghim"
                          className="inline-flex items-center gap-0.5 rounded bg-gold/15 border border-gold/30 px-1.5 py-0.5 text-[10px] font-medium text-warning"
                        >
                          <Pin className="h-2.5 w-2.5" aria-hidden="true" />
                          <span>Ghim</span>
                        </span>
                      )}
                      {isStale && (
                        <Pill tone="warning">
                          <AlertTriangle className="h-2.5 w-2.5" aria-hidden="true" />
                          <span>{formatStalenessLabel(mem.staleness_status)}</span>
                        </Pill>
                      )}
                    </div>
                    {mem.source_as_of_date && (
                      <span className="font-mono text-[10px] text-muted">
                        PIT {mem.source_as_of_date}
                      </span>
                    )}
                  </div>

                  <p className="text-[12.5px] leading-relaxed text-ink whitespace-pre-wrap">
                    {mem.content_text}
                  </p>

                  {isStale && mem.staleness_reason && (
                    <p className="text-[11px] text-warning leading-snug">
                      Lý do cần đối chiếu: {mem.staleness_reason}
                    </p>
                  )}

                  {Array.isArray(mem.tickers) && mem.tickers.length > 0 && (
                    <div className="flex flex-wrap items-center gap-1 pt-0.5">
                      {mem.tickers.map((tk) => (
                        <span
                          key={tk}
                          className="rounded bg-surface border border-line px-1.5 py-[1px] font-mono text-[10px] font-semibold text-secondary"
                        >
                          {tk}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              )
            })}

            {linkedSessions.map((ls) => {
              const badgeKey = normalizeSessionBadgeId(ls.badge_id)
              const highlighted =
                focus?.tab === "memory" &&
                normalizeSessionBadgeId(focus.sessionBadgeId) === badgeKey
              const isStale = Boolean(ls.staleness_status && ls.staleness_status !== "FRESH")

              return (
                <div
                  key={`${ls.badge_id}-${ls.episode_id}`}
                  ref={(el) => {
                    sessItemRefs.current[badgeKey] = el
                  }}
                  className={`rounded-[8px] border p-2.5 space-y-1.5 transition-all ${
                    highlighted
                      ? "border-floor bg-floor/8 ring-2 ring-floor/20"
                      : "border-line bg-paper/75"
                  }`}
                >
                  <div className="flex flex-wrap items-center justify-between gap-1.5">
                    <div className="flex flex-wrap items-center gap-1.5 min-w-0">
                      <span className="inline-flex items-center gap-1 rounded bg-floor/10 border border-floor/25 px-1.5 py-0.5 font-mono text-[10.5px] font-semibold text-floor">
                        [SESS:{badgeKey}]
                      </span>
                      <span className="text-[12px] font-semibold text-ink truncate">
                        {ls.session_title || `Phiên ${ls.session_id.slice(0, 8)}`}
                      </span>
                      {isStale && (
                        <Pill tone="warning">
                          <AlertTriangle className="h-2.5 w-2.5" aria-hidden="true" />
                          <span>[CẦN ĐỐI CHIẾU LẠI]</span>
                        </Pill>
                      )}
                    </div>
                    <button
                      type="button"
                      onClick={() => onSelectLinkedSession(ls.session_id)}
                      className="inline-flex items-center gap-1 rounded-[6px] border border-floor/35 bg-surface px-2 py-0.5 text-[11px] font-medium text-floor hover:bg-floor/10 transition-colors shrink-0 cursor-pointer"
                    >
                      <span>Mở phiên</span>
                      <ArrowUpRight className="h-3 w-3" aria-hidden="true" />
                    </button>
                  </div>

                  <p className="text-[12px] text-secondary leading-relaxed line-clamp-3">
                    {ls.episode_summary}
                  </p>

                  <div className="flex flex-wrap items-center gap-1 pt-0.5">
                    {(ls.tickers ?? []).map((tk) => (
                      <span
                        key={`t-${tk}`}
                        className="rounded bg-surface border border-line px-1.5 py-[1px] font-mono text-[10px] font-semibold text-ink"
                      >
                        {tk}
                      </span>
                    ))}
                    {(ls.macro_factors ?? []).map((mf) => (
                      <span
                        key={`m-${mf}`}
                        className="rounded bg-mineral/10 border border-mineral/20 px-1.5 py-[1px] font-mono text-[10px] text-mineral"
                      >
                        {mf}
                      </span>
                    ))}
                    {(ls.sectors ?? []).map((sec) => (
                      <span
                        key={`s-${sec}`}
                        className="rounded bg-soft px-1.5 py-[1px] text-[10px] text-secondary"
                      >
                        {sec}
                      </span>
                    ))}
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </section>

      {/* Section 2: Phiên liên quan (Cross-Session Graph) */}
      <section
        ref={relatedSectionRef}
        className={`rounded-[9px] border bg-surface p-3.5 space-y-2.5 transition-all ${
          highlightRelatedSectionFallback
            ? "border-floor ring-2 ring-floor/20"
            : "border-line"
        }`}
      >
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-1.5 text-[12px] font-semibold text-ink">
            <History className="h-3.5 w-3.5 text-floor" aria-hidden="true" />
            <span>Phiên liên quan (Cross-Session Graph)</span>
          </div>
          {activeSessionId && (
            <button
              type="button"
              disabled={relatedLoading}
              onClick={() => void fetchRelated(activeSessionId)}
              aria-label="Làm mới phiên liên quan"
              className="inline-flex h-6 w-6 items-center justify-center rounded border border-line bg-paper text-secondary hover:text-ink hover:bg-soft disabled:opacity-50"
            >
              <RefreshCw className={`h-3 w-3 ${relatedLoading ? "animate-spin" : ""}`} aria-hidden="true" />
            </button>
          )}
        </div>

        {!activeSessionId ? (
          <p className="text-[12px] text-muted">
            Chọn hoặc bắt đầu một cuộc trò chuyện để xem các phiên nghiên cứu liên quan.
          </p>
        ) : relatedLoading ? (
          <div className="flex items-center gap-2 py-2 text-[12px] text-muted">
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
            <span>Đang tải đồ thị liên kết phiên…</span>
          </div>
        ) : relatedError ? (
          <div className="rounded-[7px] border border-loss/35 bg-loss/8 px-2.5 py-2 text-[12px] text-loss flex items-center justify-between gap-2">
            <span>{relatedError}</span>
            <button
              type="button"
              onClick={() => void fetchRelated(activeSessionId)}
              className="underline font-medium shrink-0"
            >
              Thử lại
            </button>
          </div>
        ) : (
          <>
            {relatedData?.staleness_status && relatedData.staleness_status !== "FRESH" && (
              <div className="rounded-[7px] border border-warning/35 bg-warning/10 px-2.5 py-1.5 text-[11.5px] text-warning flex items-start gap-1.5">
                <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" aria-hidden="true" />
                <span>
                  Phiên hiện tại: <strong>{formatStalenessLabel(relatedData.staleness_status)}</strong>
                  {relatedData.staleness_reason ? ` — ${relatedData.staleness_reason}` : ""}
                </span>
              </div>
            )}

            {!relatedData || relatedData.related_sessions.length === 0 ? (
              <p className="text-[12px] text-muted">
                Chưa có phiên liên quan nào được nối qua mã cổ phiếu, ngành hoặc nhân tố vĩ mô.
              </p>
            ) : (
              <div className="space-y-2">
                {relatedData.related_sessions.map((rs) => {
                  const isStale = Boolean(rs.staleness_status && rs.staleness_status !== "FRESH")
                  const relLabel = rs.relation_type
                    ? RELATION_TYPE_LABELS[rs.relation_type] ?? rs.relation_type
                    : "Liên kết"
                  return (
                    <button
                      key={rs.episode_id}
                      type="button"
                      onClick={() => onSelectLinkedSession(rs.session_id)}
                      className="w-full text-left rounded-[8px] border border-line bg-paper/75 hover:border-floor/45 hover:bg-soft/60 p-2.5 space-y-1.5 transition-colors cursor-pointer"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-[12px] font-semibold text-ink truncate">
                          {rs.session_title || `Phiên ${rs.session_id.slice(0, 8)}`}
                        </span>
                        <ArrowUpRight className="h-3.5 w-3.5 text-floor shrink-0" aria-hidden="true" />
                      </div>
                      <div className="flex flex-wrap items-center gap-1.5">
                        <Pill tone="neutral">{relLabel}</Pill>
                        {typeof rs.link_score === "number" && rs.link_score > 0 && (
                          <span className="font-mono text-[10px] text-muted tnum">
                            score {rs.link_score.toFixed(2)}
                          </span>
                        )}
                        {rs.source_as_of_date && (
                          <span className="font-mono text-[10px] text-muted">
                            · {rs.source_as_of_date}
                          </span>
                        )}
                        {isStale && (
                          <Pill tone="warning">
                            <AlertTriangle className="h-2.5 w-2.5" aria-hidden="true" />
                            <span>[CẦN ĐỐI CHIẾU LẠI]</span>
                          </Pill>
                        )}
                      </div>
                      <p className="text-[11.5px] text-secondary line-clamp-2 leading-relaxed">
                        {rs.episode_summary}
                      </p>
                      {((rs.tickers && rs.tickers.length > 0) ||
                        (rs.macro_factors && rs.macro_factors.length > 0)) && (
                        <div className="flex flex-wrap gap-1">
                          {(rs.tickers ?? []).map((tk) => (
                            <span
                              key={tk}
                              className="rounded bg-surface border border-line px-1.5 py-[1px] font-mono text-[10px] font-semibold text-ink"
                            >
                              {tk}
                            </span>
                          ))}
                          {(rs.macro_factors ?? []).map((mf) => (
                            <span
                              key={mf}
                              className="rounded bg-mineral/10 px-1.5 py-[1px] font-mono text-[10px] text-mineral"
                            >
                              {mf}
                            </span>
                          ))}
                        </div>
                      )}
                    </button>
                  )
                })}
              </div>
            )}
          </>
        )}
      </section>

      {/* Section 3: Quản lý ký ức dài hạn của bạn */}
      <section className="rounded-[9px] border border-line bg-surface p-3.5 space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-1.5 text-[12px] font-semibold text-ink">
            <Brain className="h-3.5 w-3.5 text-mineral" aria-hidden="true" />
            <span>Quản lý ký ức dài hạn ({memoriesTotal})</span>
          </div>
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              disabled={memoriesLoading}
              onClick={() => void fetchMemories(statusFilter)}
              aria-label="Làm mới danh sách ký ức"
              className="inline-flex h-7 w-7 items-center justify-center rounded-[6px] border border-line bg-paper text-secondary hover:text-ink hover:bg-soft disabled:opacity-50"
            >
              <RefreshCw className={`h-3 w-3 ${memoriesLoading ? "animate-spin" : ""}`} aria-hidden="true" />
            </button>
            <button
              type="button"
              disabled={clearingAll}
              onClick={() => setConfirmClearAll(true)}
              className="inline-flex items-center gap-1 rounded-[6px] border border-loss/35 bg-loss/8 px-2 h-7 text-[11px] font-medium text-loss hover:bg-loss/14 transition-colors disabled:opacity-50 cursor-pointer"
            >
              <Trash2 className="h-3 w-3" aria-hidden="true" />
              <span>Xóa toàn bộ ký ức</span>
            </button>
          </div>
        </div>

        {/* Status Filter Pills */}
        <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Lọc trạng thái ký ức">
          {filterTabs.map((tab) => {
            const active = statusFilter === tab.id
            return (
              <button
                key={tab.id}
                type="button"
                onClick={() => {
                  setStatusFilter(tab.id)
                  setEditingId(null)
                  setConfirmDeleteId(null)
                }}
                className={`rounded-full border px-2.5 py-0.5 text-[11px] font-medium transition-colors cursor-pointer ${
                  active
                    ? "border-ink bg-ink text-paper"
                    : "border-line bg-paper text-secondary hover:text-ink hover:bg-soft"
                }`}
              >
                {tab.label}
              </button>
            )
          })}
        </div>

        {/* Inline Confirm Clear All */}
        {confirmClearAll && (
          <div
            role="alertdialog"
            aria-label="Xác nhận xóa toàn bộ ký ức"
            className="rounded-[8px] border border-loss/40 bg-loss/8 p-2.5 space-y-2 text-[12px]"
          >
            <p className="text-loss font-medium leading-snug">
              Xác nhận xóa toàn bộ ký ức dài hạn, tóm tắt phiên và đồ thị cá nhân? Hành động này không thể hoàn tác.
            </p>
            <div className="flex items-center justify-end gap-1.5">
              <Button
                size="sm"
                variant="secondary"
                disabled={clearingAll}
                onClick={() => setConfirmClearAll(false)}
                className="!h-7 !px-2.5 text-[11.5px]"
              >
                Hủy
              </Button>
              <button
                type="button"
                disabled={clearingAll}
                onClick={() => void handleClearAll()}
                className="inline-flex items-center gap-1 rounded-[6px] bg-loss text-paper px-2.5 h-7 text-[11.5px] font-medium hover:opacity-90 disabled:opacity-50 cursor-pointer"
              >
                {clearingAll && <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />}
                <span>Xác nhận xóa tất cả</span>
              </button>
            </div>
          </div>
        )}

        {actionError && (
          <div
            role="alert"
            className="rounded-[7px] border border-loss/35 bg-loss/8 px-2.5 py-2 text-[11.5px] text-loss flex items-center justify-between gap-2"
          >
            <span>{actionError}</span>
            <button
              type="button"
              onClick={() => setActionError(null)}
              aria-label="Đóng thông báo lỗi"
              className="text-loss hover:opacity-75"
            >
              <X className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
          </div>
        )}

        {memoriesLoading ? (
          <div className="flex items-center gap-2 py-4 justify-center text-[12px] text-muted">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            <span>Đang tải ký ức của bạn…</span>
          </div>
        ) : memoriesError ? (
          <div className="rounded-[7px] border border-loss/35 bg-loss/8 px-2.5 py-2 text-[12px] text-loss flex items-center justify-between gap-2">
            <span>{memoriesError}</span>
            <button
              type="button"
              onClick={() => void fetchMemories(statusFilter)}
              className="underline font-medium shrink-0"
            >
              Thử lại
            </button>
          </div>
        ) : memories.length === 0 ? (
          <div className="py-5 text-center text-[12px] text-muted">
            {statusFilter === "active"
              ? "Chưa có ký ức dài hạn nào đang hoạt động. Ký ức sẽ tự động học ở chế độ Tự động."
              : statusFilter === "stale"
                ? "Không có ký ức nào cần đối chiếu lại theo BCTC mới."
                : "Chưa có ký ức nào đã thay thế hoặc bị thu hồi."}
          </div>
        ) : (
          <div className="space-y-2">
            {memories.map((item) => {
              const typeMeta = MEMORY_TYPE_LABELS[item.memory_type] ?? {
                label: item.memory_type,
                tone: "neutral",
              }
              const isStale = Boolean(item.staleness_status && item.staleness_status !== "FRESH")
              const isEditing = editingId === item.memory_id
              const isConfirmingDelete = confirmDeleteId === item.memory_id
              const isBusy = busyMemoryId === item.memory_id
              const canMutate = statusFilter !== "superseded"

              return (
                <div
                  key={item.memory_id}
                  className="rounded-[8px] border border-line bg-paper/75 p-2.5 space-y-2"
                >
                  <div className="flex flex-wrap items-center justify-between gap-1.5">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <Pill tone={typeMeta.tone}>{typeMeta.label}</Pill>
                      {item.pinned_by_user && (
                        <Pill tone="gold">
                          <Pin className="h-2.5 w-2.5" aria-hidden="true" />
                          <span>Đã ghim</span>
                        </Pill>
                      )}
                      {isStale && (
                        <Pill tone="warning">
                          <AlertTriangle className="h-2.5 w-2.5" aria-hidden="true" />
                          <span>[CẦN ĐỐI CHIẾU LẠI]</span>
                        </Pill>
                      )}
                    </div>

                    {canMutate && !isEditing && (
                      <div className="flex items-center gap-1">
                        <button
                          type="button"
                          disabled={isBusy}
                          onClick={() => void handleTogglePin(item)}
                          title={item.pinned_by_user ? "Bỏ ghim ký ức" : "Ghim ký ức (bảo vệ khỏi ghi đè tự động)"}
                          aria-label={item.pinned_by_user ? "Bỏ ghim ký ức" : "Ghim ký ức"}
                          className={`inline-flex h-6 w-6 items-center justify-center rounded border transition-colors cursor-pointer disabled:opacity-50 ${
                            item.pinned_by_user
                              ? "border-gold/40 bg-gold/15 text-warning"
                              : "border-line bg-surface text-secondary hover:text-ink"
                          }`}
                        >
                          {item.pinned_by_user ? (
                            <PinOff className="h-3 w-3" aria-hidden="true" />
                          ) : (
                            <Pin className="h-3 w-3" aria-hidden="true" />
                          )}
                        </button>
                        <button
                          type="button"
                          disabled={isBusy}
                          onClick={() => handleStartEdit(item)}
                          title="Chỉnh sửa nội dung ký ức"
                          aria-label="Chỉnh sửa nội dung ký ức"
                          className="inline-flex h-6 w-6 items-center justify-center rounded border border-line bg-surface text-secondary hover:text-ink cursor-pointer disabled:opacity-50"
                        >
                          <Edit3 className="h-3 w-3" aria-hidden="true" />
                        </button>
                        <button
                          type="button"
                          disabled={isBusy}
                          onClick={() => setConfirmDeleteId(isConfirmingDelete ? null : item.memory_id)}
                          title="Xóa ký ức này"
                          aria-label="Xóa ký ức này"
                          className="inline-flex h-6 w-6 items-center justify-center rounded border border-line bg-surface text-secondary hover:border-loss/40 hover:text-loss cursor-pointer disabled:opacity-50"
                        >
                          <Trash2 className="h-3 w-3" aria-hidden="true" />
                        </button>
                      </div>
                    )}
                  </div>

                  {isEditing ? (
                    <div className="space-y-2">
                      <textarea
                        value={editDraft}
                        onChange={(e) => setEditDraft(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Escape") {
                            setEditingId(null)
                            setEditDraft("")
                          }
                        }}
                        rows={3}
                        maxLength={2000}
                        disabled={isBusy}
                        aria-label="Chỉnh sửa nội dung ký ức"
                        className="w-full rounded-[6px] border border-mineral bg-surface px-2.5 py-1.5 text-[12px] text-ink focus:outline-none"
                      />
                      <div className="flex items-center justify-end gap-1.5">
                        <Button
                          size="sm"
                          variant="secondary"
                          disabled={isBusy}
                          onClick={() => {
                            setEditingId(null)
                            setEditDraft("")
                          }}
                          className="!h-7 !px-2 text-[11px]"
                        >
                          Hủy
                        </Button>
                        <Button
                          size="sm"
                          variant="primary"
                          disabled={isBusy || !editDraft.trim()}
                          onClick={() => void handleSaveEdit(item.memory_id)}
                          className="!h-7 !px-2.5 text-[11px]"
                        >
                          {isBusy ? (
                            <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
                          ) : (
                            <Check className="h-3 w-3" aria-hidden="true" />
                          )}
                          <span>Lưu</span>
                        </Button>
                      </div>
                    </div>
                  ) : (
                    <p className="text-[12.5px] leading-relaxed text-ink whitespace-pre-wrap">
                      {item.content_text}
                    </p>
                  )}

                  {isConfirmingDelete && (
                    <div className="flex items-center justify-between gap-2 rounded-[6px] border border-loss/35 bg-loss/8 px-2 py-1.5 text-[11.5px]">
                      <span className="text-loss font-medium">Xóa ký ức này?</span>
                      <div className="flex items-center gap-1">
                        <button
                          type="button"
                          disabled={isBusy}
                          onClick={() => setConfirmDeleteId(null)}
                          className="rounded px-2 py-0.5 text-secondary hover:text-ink"
                        >
                          Hủy
                        </button>
                        <button
                          type="button"
                          disabled={isBusy}
                          onClick={() => void handleDeleteSingle(item.memory_id)}
                          className="rounded bg-loss text-paper px-2 py-0.5 font-medium hover:opacity-90 disabled:opacity-50"
                        >
                          {isBusy ? "Đang xóa…" : "Xóa"}
                        </button>
                      </div>
                    </div>
                  )}

                  <div className="flex flex-wrap items-center justify-between gap-1.5 text-[10.5px] font-mono text-muted">
                    <div className="flex flex-wrap items-center gap-1">
                      {(item.tickers ?? []).map((tk) => (
                        <span
                          key={tk}
                          className="rounded bg-surface border border-line px-1 py-[1px] text-[10px] font-semibold text-secondary"
                        >
                          {tk}
                        </span>
                      ))}
                      <span>PIT {item.source_as_of_date}</span>
                    </div>
                    <span>×{item.reinforcement_count} lượt</span>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </section>
    </div>
  )
}
