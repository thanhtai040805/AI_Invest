"use client"

import { useState, useEffect } from "react"
import { FinancialCalendar } from "@/components/FinancialCalendar"
import { displayDate, vietnamDate } from "@/lib/financial-date"
import { Page } from "@/components/Shell"
import { Button, Panel, PanelHead, Pill } from "@/components/ui"
import { Link } from "@/lib/router"
import { workspaceApi } from "@/lib/api"
import { useResource } from "@/lib/api/use-resource"
import type { ApiProblem } from "@/lib/api/client"

interface InvestmentThesis {
  analysis_date?: string | null
  is_replay?: boolean | null
  history_id?: string
  thesis_id: string
  ticker: string
  catalyst_type: string
  target_price_range: [number, number] | null
  confirming_signals: Record<string, string> | null
  invalidation_conditions: string[] | null
  status: string
  created_at: string
  generated_at?: string
  catalyst_description?: string
  why_now?: string
  why_this_stock?: string
  counter_verdict?: {
    cts_score: number | string
    base_cts: number | string
    interaction_multiplier: number | string
    regime_multiplier: number | string
    rationale?: string
    holes?: string[]
    execution_constraints?: { reason?: string }
  } | null
}

const EMPTY_THESES: InvestmentThesis[] = []
const thesisStatus: Record<string, { label: string; tone: string }> = {
  APPROVED_ACTIVE: { label: "Được duyệt", tone: "gain" },
  CONDITIONAL_APPROVED: { label: "Duyệt có điều kiện", tone: "warning" },
  REJECTED: { label: "Bị từ chối", tone: "loss" },
  PENDING_COUNTER_ANALYSIS: { label: "Chờ phản biện", tone: "neutral" },
  ACTIVE: { label: "Đang hoạt động", tone: "teal" },
}

function thesisDay(value: string) {
  return new Date(value).toLocaleDateString("vi-VN", { timeZone: "Asia/Ho_Chi_Minh" })
}

export default function ResearchPage() {
  const resource = useResource(() => workspaceApi.research(), [])
  const [thesisView, setThesisView] = useState<"latest" | "history">("latest")
  const [historyTicker, setHistoryTicker] = useState("")
  const [historyDate, setHistoryDate] = useState(() => vietnamDate())
  const [historyPage, setHistoryPage] = useState(1)
  const [historyRetry, setHistoryRetry] = useState(0)
  const [history, setHistory] = useState<{
    data: { theses: InvestmentThesis[]; total: number; page: number; pageSize: number; dates: string[] } | null
    loading: boolean
    error: ApiProblem | null
  }>({ data: null, loading: false, error: null })

  useEffect(() => {
    if (thesisView !== "history") return
    let cancelled = false
    // Start a new request; cleanup prevents an older filter response replacing it.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setHistory(previous => ({ data: previous.data, loading: true, error: null }))
    workspaceApi.thesisHistory({ page: historyPage, ticker: historyTicker || undefined, date: historyDate || undefined })
      .then(data => { if (!cancelled) setHistory({ data, loading: false, error: null }) })
      .catch((error: ApiProblem) => { if (!cancelled) setHistory({ data: null, loading: false, error }) })
    return () => { cancelled = true }
  }, [thesisView, historyPage, historyTicker, historyDate, historyRetry])

  const raw = resource.data as { theses?: InvestmentThesis[] } | null
  const theses = raw?.theses ?? EMPTY_THESES
  const visibleTheses = thesisView === "history" ? history.data?.theses ?? EMPTY_THESES : theses
  const thesisLoading = thesisView === "history" ? history.loading : resource.loading
  const thesisError = thesisView === "history" ? history.error : resource.error
  function clearHistoryFilters() {
    setHistoryTicker("")
    setHistoryDate(vietnamDate())
    setHistoryPage(1)
  }
  function showAllHistory() {
    setHistoryTicker("")
    setHistoryDate("")
    setHistoryPage(1)
  }

  return (
    <Page
      title="Luận điểm đầu tư"
      sub="Theo dõi luận điểm đầu tư mới nhất và lịch sử phân tích từ AI Engine."
    >
      {resource.error && (
        <Panel className="mb-4 text-loss text-[13px]">
          Không tải được dữ liệu nghiên cứu. <button className="underline" onClick={() => void resource.reload()}>Thử lại</button>
        </Panel>
      )}
        <div className="space-y-4">
          <PanelHead
            title={thesisView === "history" ? "Lịch sử luận điểm đầu tư" : "Luận điểm mới nhất theo mã cổ phiếu"}
            sub={thesisView === "history"
              ? "Lọc theo ngày phân tích của replay; thời điểm ghi bản lưu được hiển thị riêng. Bản lưu cũ thiếu ngày phân tích được nhóm theo ngày ghi log. Trạng thái là trạng thái lúc lập. Ngày giờ theo Việt Nam."
              : "Agent 04 lập luận điểm; Agent 05 phản biện và cập nhật trạng thái. Duyệt có điều kiện cần tuân thủ giới hạn thực thi."}
          />
          <div className="flex flex-wrap items-center gap-2">
            <Button variant={thesisView === "latest" ? "primary" : "secondary"} onClick={() => setThesisView("latest")}>Mới nhất ({theses.length})</Button>
            <Button variant={thesisView === "history" ? "primary" : "secondary"} onClick={() => {
              clearHistoryFilters()
              setHistory(previous => ({ data: previous.data, loading: true, error: null }))
              setHistoryRetry(n => n + 1)
              setThesisView("history")
            }}>Lịch sử</Button>
            {thesisView === "history" && <>
              <input aria-label="Lọc lịch sử theo mã cổ phiếu" placeholder="Mã CP, ví dụ HPG" value={historyTicker}
                maxLength={16} onChange={e => { setHistoryTicker(e.target.value.trim().toUpperCase()); setHistoryPage(1) }}
                className="h-8 px-3 rounded-[6px] border border-line bg-surface text-[12px] w-44" />
              <FinancialCalendar label="Ngày phân tích luận điểm" allowAll availableOnly value={historyDate} dates={history.data?.dates} onChange={date => { setHistoryDate(date); setHistoryPage(1) }} />
              <Button onClick={clearHistoryFilters}>Hôm nay</Button>
              <span className="text-[12px] text-muted">{history.loading ? "Đang tải lịch sử..." : `${history.data?.total ?? 0} bản lưu`}</span>
            </>}
          </div>
          {thesisView === "history" && !history.loading && history.data && history.data.dates.length > 0 && <div className="flex flex-wrap items-center gap-2 text-[12px] text-muted">
            <span>Ngày phân tích có bản lưu gần nhất{historyTicker ? ` · ${historyTicker}` : ""}:</span>
            {history.data.dates.slice(0, 5).map(date => <button key={date} type="button" onClick={() => { setHistoryDate(date); setHistoryPage(1) }} aria-pressed={historyDate === date}
              className={`rounded border px-2 py-1 font-mono ${historyDate === date ? "border-ink bg-ink text-paper" : "border-line bg-surface text-ink hover:border-ink/40"}`}>{displayDate(date)}</button>)}
          </div>}
          {thesisError && thesisView === "history" && <Panel className="text-loss text-[13px]">
            Không tải được lịch sử. <button className="underline" onClick={() => setHistoryRetry(n => n + 1)}>Thử lại</button>
          </Panel>}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {thesisLoading ? (
              <Panel className="col-span-full text-center py-8 text-muted text-[13px]">Đang tải luận điểm đầu tư...</Panel>
            ) : visibleTheses.length === 0 ? (
              <Panel className="col-span-full text-center py-8 text-muted text-[13px]">
                {thesisError ? "Dữ liệu chưa tải được." : thesisView === "history" ? historyDate ? "Không có bản lưu trong ngày đã chọn. Chọn ngày có dữ liệu gần nhất bên trên." : "Chưa có lịch sử phù hợp với bộ lọc." : "Chưa có luận điểm đầu tư nào được ghi nhận."}
                {thesisView === "history" && !thesisError && (historyDate || historyTicker) && <div className="mt-3"><Button onClick={showAllHistory}>Xem toàn bộ lịch sử</Button></div>}
              </Panel>
            ) : (
              visibleTheses.map((t) => {
                const signals = t.confirming_signals ? Object.values(t.confirming_signals) : []
                const invalidations = t.invalidation_conditions || []
                const generatedAt = t.generated_at || t.created_at
                const status = thesisStatus[t.status] ?? { label: t.status || "Không rõ", tone: "neutral" }
                const verdict = t.counter_verdict
                const expectedCTS = verdict ? Math.min(100, Math.round(Number(verdict.base_cts) * Number(verdict.interaction_multiplier) * Number(verdict.regime_multiplier) * 10) / 10) : null
                const inconsistentCTS = verdict && expectedCTS !== null && Math.abs(expectedCTS - Number(verdict.cts_score)) > 0.1
                return (
                  <Panel key={t.history_id || t.thesis_id}>
                    <div className="flex items-center justify-between mb-2">
                      <div className="flex items-center gap-2">
                        <Link to={`/stock/${t.ticker}`}>
                          <span className="font-mono text-[16px] font-bold text-ink hover:underline cursor-pointer">
                            {t.ticker}
                          </span>
                        </Link>
                        <Pill tone="gold">{t.catalyst_type || "Cơ bản"}</Pill>
                      </div>
                      <Pill tone={status.tone}>
                        {status.label}
                      </Pill>
                    </div>

                    {t.catalyst_description && <p className="text-[13px] leading-relaxed text-ink mt-3">{t.catalyst_description}</p>}
                    {t.why_now && <p className="text-[12px] leading-relaxed text-secondary mt-2"><strong>Tại sao lúc này: </strong>{t.why_now}</p>}
                    {t.why_this_stock && <p className="text-[12px] leading-relaxed text-secondary mt-2"><strong>Tại sao mã này: </strong>{t.why_this_stock}</p>}
                    {verdict && <div className="mt-3 pt-3 border-t border-line text-[12px] space-y-2">
                      <div className="font-mono">CTS: {Number(verdict.cts_score).toFixed(1)}/100</div>
                      {inconsistentCTS && <p className="text-warning">Các thành phần CTS đã lưu không khớp điểm cuối. Cần đối soát lần chạy gốc.</p>}
                      {verdict.execution_constraints?.reason && <p className="text-warning">{verdict.execution_constraints.reason}</p>}
                      <details>
                        <summary className="cursor-pointer text-secondary">Lý do phản biện & rủi ro</summary>
                        <p className="mt-2 leading-relaxed text-secondary">{verdict.rationale || "Chưa có lý do phản biện."}</p>
                        {verdict.holes?.map((hole, i) => <p key={i} className="mt-2 text-secondary">• {hole}</p>)}
                      </details>
                    </div>}

                    {t.target_price_range && (
                      <div className="text-[12.5px] font-mono text-ink mt-2 mb-3">
                        Mục tiêu: <span className="text-gain font-semibold">{t.target_price_range[0]?.toLocaleString("vi-VN")}</span> – <span className="text-gain font-semibold">{t.target_price_range[1]?.toLocaleString("vi-VN")}</span> đ
                      </div>
                    )}

                    {signals.length > 0 && (
                      <div className="mt-3 pt-3 border-t border-line text-[12px]">
                        <div className="text-[10.5px] uppercase tracking-wide text-muted mb-1 font-semibold">Tín hiệu xác nhận</div>
                        <ul className="space-y-1 text-secondary">
                          {signals.map((sig, i) => (
                            <li key={i} className="flex gap-1.5"><span className="text-gain">✓</span>{sig}</li>
                          ))}
                        </ul>
                      </div>
                    )}

                    {invalidations.length > 0 && (
                      <div className="mt-3 pt-3 border-t border-line text-[12px]">
                        <div className="text-[10.5px] uppercase tracking-wide text-muted mb-1 font-semibold">Điều kiện vi phạm hủy bỏ</div>
                        <ul className="space-y-1 text-secondary">
                          {invalidations.map((inv, i) => (
                            <li key={i} className="flex gap-1.5"><span className="text-loss">✗</span>{inv}</li>
                          ))}
                        </ul>
                      </div>
                    )}

                    <div className="flex flex-wrap items-center justify-between gap-2 mt-4 pt-3 border-t border-line text-[11px] text-muted font-mono">
                      <span className="break-all">ID: {t.thesis_id}{t.history_id ? ` · Bản lưu #${t.history_id}` : ""}</span>
                      <div className="text-right space-y-1">
                        {t.history_id && <div className="text-secondary">{t.is_replay === true ? "Replay" : t.is_replay === false ? "Phân tích" : "Bản lưu cũ"} · {t.analysis_date ? `Ngày phân tích ${displayDate(t.analysis_date)}` : "Chưa lưu ngày phân tích"}</div>}
                        <div>{t.history_id ? "Ghi bản lưu: " : "Lập luận điểm: "}{thesisDay(generatedAt)} · {new Date(generatedAt).toLocaleTimeString("vi-VN", { timeZone: "Asia/Ho_Chi_Minh" })}</div>
                      </div>
                    </div>
                  </Panel>
                )
              })
            )}
          </div>
          {thesisView === "history" && history.data && <div className="flex items-center justify-between gap-3">
            <Button disabled={history.loading || historyPage <= 1} onClick={() => setHistoryPage(p => p - 1)}>Trang trước</Button>
            <span className="text-[12px] text-muted">Trang {historyPage} / {Math.max(1, Math.ceil(history.data.total / history.data.pageSize))}</span>
            <Button disabled={history.loading || historyPage * history.data.pageSize >= history.data.total} onClick={() => setHistoryPage(p => p + 1)}>Trang sau</Button>
          </div>}
        </div>
    </Page>
  )
}
