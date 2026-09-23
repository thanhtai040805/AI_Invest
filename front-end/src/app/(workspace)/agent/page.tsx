"use client"

import { useMemo, useState } from "react"
import { agentOutput, pipeline, defaultRuns, defaultStockCases, type CaseItem, type StockCaseDetail } from "@/lib/agent-system"
import { Button, Conviction, metricTone, Pill, ReasoningBlock, SectionEyebrow, Tabs, PercentChange, fmt } from "@/components/ui"
import { Link } from "@/lib/router"
import { workspaceApi, marketApi } from "@/lib/api"
import { useResource } from "@/lib/api/use-resource"
import type { Stock } from "@/types"

interface AgentThesis {
  ticker?: string
  status?: string
  thesis_statement?: string
  thesis?: string
  catalyst_description?: string
  target_price?: number
  entry_price_estimated?: number
  invalidation_threshold?: number
  thesis_id?: string
}

interface AgentCounterThesis { ticker?: string; thesis_id?: string; argument_points?: string[] }
interface AgentLog { agent?: string; entries?: unknown[] }
interface AgentResponse { theses?: AgentThesis[]; counterTheses?: AgentCounterThesis[]; logs?: AgentLog[] }

const statusTone: Record<string, "gain" | "mineral" | "teal" | "warning" | "neutral"> = {
  New: "mineral", Updated: "teal", Confirmed: "gain", Watching: "neutral", Flagged: "warning",
}
const dotClass: Record<string, string> = {
  New: "bg-mineral", Updated: "bg-teal", Confirmed: "bg-gain", Watching: "bg-neutral", Flagged: "bg-warning",
}

function agentStatus(order: number, runIndex: number) {
  // later runs have advanced further through the 12-agent relay
  const reached = 12 - runIndex * 2.5
  if (order < reached - 1) return "Completed"
  if (order < reached) return "Working"
  return "Waiting"
}
const stateDot: Record<string, string> = {
  Completed: "bg-teal", Working: "bg-mineral animate-pulse", Waiting: "bg-line-strong",
}
const stateText: Record<string, string> = {
  Completed: "text-teal", Working: "text-mineral", Waiting: "text-muted",
}
// A signature-output card for one agent, contextual to the active run + case.
function AgentCard({ id, runId, symbol, badge }: { id: string; runId: string; symbol: string; badge: string }) {
  const [open, setOpen] = useState(false)
  const out = agentOutput(id, { runId, symbol })
  const actionable = id === "07" || id === "08"
  return (
    <div className="rounded-[10px] border border-line bg-paper p-3.5">
      <div className="flex items-center gap-2 mb-2.5">
        <span className="font-mono text-[10px] text-muted">{id}</span>
        <span className="text-[12px] font-medium text-ink">{badge}</span>
      </div>
      {actionable && <div className="mb-2 text-[9.5px] font-medium uppercase tracking-wide text-warning">Đề xuất hệ thống — không phải khuyến nghị</div>}
      <div className="flex flex-wrap gap-1.5 mb-1">
        {out.headline.map((m) => (
          <span key={m.label} className="inline-flex items-baseline gap-1.5 rounded-[6px] border border-line bg-surface px-2 py-1 text-[11px]">
            <span className="text-muted">{m.label}</span>
            <span className={`tnum font-mono font-medium ${metricTone[m.tone ?? "neutral"]}`}>{m.value}</span>
          </span>
        ))}
      </div>
      <button onClick={() => setOpen((v) => !v)} className="mt-1.5 text-[10.5px] font-medium text-mineral hover:underline">{open ? "Ẩn chi tiết ↑" : "Chi tiết ↓"}</button>
      {open && <ul className="mt-2 space-y-1 border-t border-line pt-2 text-[11px] leading-snug text-secondary">{out.detail.map((d) => <li key={d} className="flex gap-1.5"><span className="text-muted">·</span>{d}</li>)}</ul>}
    </div>
  )
}

const signalCards = [
  { id: "01", badge: "Chế độ thị trường" },
  { id: "05", badge: "Luận điểm phản biện" },
  { id: "06", badge: "Cổng kiểm soát rủi ro" },
  { id: "12", badge: "Chỉ thị CIO" },
  { id: "11", badge: "Kiểm toán quản trị" },
]

export default function WarRoom() {
  const resource = useResource(() => workspaceApi.agent().catch(() => null), [])
  const snapshotRes = useResource(() => marketApi.snapshot().catch(() => ({ items: [] })), [])

  const liveRuns = useMemo(() => {
    const raw = resource.data as AgentResponse | null
    if (!raw || !Array.isArray(raw.theses) || raw.theses.length === 0) {
      return defaultRuns
    }
    const liveCases: CaseItem[] = raw.theses.map((t) => ({
      symbol: String(t.ticker),
      status: t.status === "APPROVED" ? "Confirmed" : t.status === "WATCH" ? "Watching" : "New",
      note: String(t.thesis_statement || t.catalyst_description || "Luận điểm đầu tư tự hành"),
    }))
    const firstRun = {
      id: "run-live",
      label: `Run ${new Date().toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" })}`,
      time: "Hôm nay",
      triggered: "Autonomous Pipeline",
      cases: liveCases,
      agentsRun: raw.logs?.length || 12,
    }
    return [firstRun, ...defaultRuns.slice(1)]
  }, [resource.data])

  const liveStockCases = useMemo(() => {
    const raw = resource.data as AgentResponse | null
    if (!raw || !Array.isArray(raw.theses) || raw.theses.length === 0) {
      return defaultStockCases
    }
    const mapped: Record<string, StockCaseDetail> = { ...defaultStockCases }
    raw.theses.forEach((t) => {
      const sym = String(t.ticker)
      const base = defaultStockCases[sym] ?? defaultStockCases.HPG
      const counterMatch = raw.counterTheses?.find((ct) => ct.ticker === sym || ct.thesis_id === t.thesis_id)
      mapped[sym] = {
        ...base,
        symbol: sym,
        thesis: t.thesis_statement || t.thesis || base.thesis,
        catalysts: t.catalyst_description ? [t.catalyst_description] : base.catalysts,
        targetHigh: Number(t.target_price) || base.targetHigh,
        targetLow: Number(t.entry_price_estimated) || base.targetLow,
        invalidation: Number(t.invalidation_threshold) || base.invalidation,
        counter: counterMatch?.argument_points || base.counter,
        bias: t.status === "APPROVED" ? "Tích lũy · Vùng mua" : base.bias,
      }
    })
    return mapped
  }, [resource.data])

  const [runId, setRunId] = useState(defaultRuns[0].id)
  const run = liveRuns.find((r) => r.id === runId) ?? liveRuns[0]
  const runIndex = liveRuns.findIndex((r) => r.id === run.id)
  const [symbol, setSymbol] = useState(run.cases[0]?.symbol ?? "HPG")
  const [tab, setTab] = useState("Sources")
  const [leftView, setLeftView] = useState<"runs" | "pipeline">("runs")
  const [expanded, setExpanded] = useState<number | null>(null)
  const [showDetail, setShowDetail] = useState(false)

  // keep selected symbol valid for the run
  const activeSymbol = run.cases.some((c) => c.symbol === symbol) ? symbol : (run.cases[0]?.symbol ?? "HPG")
  const c = liveStockCases[activeSymbol] ?? defaultStockCases[activeSymbol] ?? defaultStockCases.HPG

  const stockList = (snapshotRes.data as { items?: Stock[] })?.items || []
  const stock = stockList.find((s) => s.symbol === activeSymbol) || {
    symbol: activeSymbol,
    name: activeSymbol === "HPG" ? "Hòa Phát Group" : activeSymbol === "MBB" ? "MBBank" : activeSymbol === "FPT" ? "FPT Corporation" : activeSymbol,
    price: c.targetLow || 28000,
    changePct: 0.8,
  }

  const runCase = run.cases.find((x) => x.symbol === activeSymbol) ?? run.cases[0]

  const totalCases = useMemo(() => new Set(liveRuns.flatMap((r) => r.cases.map((x) => x.symbol))).size, [liveRuns])

  return (
    <div className="min-h-full bg-paper">
      {/* Header */}
      <div className="min-h-[52px] border-b border-line bg-surface flex flex-wrap items-center gap-x-3 gap-y-1 px-6 py-2.5 shrink-0">
        <h1 className="text-[16px] font-semibold tracking-tight text-ink">Phòng chỉ huy AI</h1>
        <span className="text-[12px] text-muted hidden md:inline">Môi trường điều hành & phân tích định lượng</span>
        <div className="ml-auto flex items-center gap-2 text-[12px]">
          <span className="flex items-center gap-1.5 text-muted"><span className="w-1.5 h-1.5 rounded-full bg-gain animate-pulse" />{liveRuns.length} lượt chạy hôm nay · {totalCases} mã</span>
          <Pill tone="teal"><span className="w-1.5 h-1.5 rounded-full bg-teal animate-pulse mr-1" />Hệ thống tự hành</Pill>
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-[288px_minmax(0,1fr)_340px] items-stretch">
        {/* ── Left column ── */}
        <aside className="border-b xl:border-b-0 xl:border-r border-line bg-surface self-stretch">
          <div className="flex border-b border-line shrink-0">
            {(["runs", "pipeline"] as const).map((v) => (
              <button
                key={v}
                onClick={() => setLeftView(v)}
                className={`flex-1 h-10 text-[12.5px] font-medium capitalize transition-colors ${leftView === v ? "text-ink" : "text-muted hover:text-secondary"} relative`}
              >
                {v === "runs" ? "Lượt chạy hôm nay" : "Quy trình hoạt động"}
                {leftView === v && <span className="absolute left-4 right-4 -bottom-px h-[2px] bg-ink rounded-full" />}
              </button>
            ))}
          </div>

          {leftView === "runs" ? (
            <div className="p-3">
              <p className="text-[11.5px] text-muted leading-snug px-1 mb-3">
                Quy trình đa tác tử hoạt động tự động theo lịch và kích hoạt trực tiếp. Mỗi lượt phân tích nhiều mã cổ phiếu.
              </p>
              <div className="space-y-1.5">
                {liveRuns.map((r) => {
                  const active = r.id === run.id
                  return (
                    <button
                      key={r.id}
                      onClick={() => { setRunId(r.id); setSymbol(r.cases[0].symbol) }}
                      className={`w-full text-left rounded-[8px] p-3 border transition-colors ${active ? "border-line-strong bg-paper" : "border-transparent hover:bg-soft/60"}`}
                    >
                      <div className="flex items-center gap-2">
                        <span className={`w-1 h-8 rounded-full shrink-0 ${active ? "bg-mineral" : "bg-line-strong"}`} />
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2">
                            <span className="text-[13px] font-medium text-ink">{r.label}</span>
                            <span className="ml-auto tnum font-mono text-[11px] text-muted">{r.time}</span>
                          </div>
                          <div className="text-[11px] text-muted mt-0.5">{r.triggered} · {r.cases.length} mã · {r.agentsRun} tác tử</div>
                        </div>
                      </div>
                    </button>
                  )
                })}
              </div>
            </div>
          ) : (
            <div className="p-4">
              <p className="text-[11.5px] text-muted leading-snug mb-4">
                12 tác tử vận hành theo cơ chế tiếp sức. Mỗi tác tử chuyển giao kết quả cho tác tử kế tiếp — không tác tử nào tự ý đưa ra khuyến nghị từ dữ liệu thô.
              </p>
              <div className="relative">
                <div className="absolute left-[11px] top-1 bottom-1 w-px bg-line" />
                <div className="space-y-3.5">
                  {pipeline.map((p) => {
                    const st = agentStatus(p.order, runIndex)
                    const open = expanded === p.order
                    const actionable = p.id === "07" || p.id === "08"
                    const out = agentOutput(p.id, { runId: run.id, symbol: activeSymbol })
                    return (
                      <div key={p.order} className="relative pl-8">
                        <span className={`absolute left-[6px] top-1 w-[11px] h-[11px] rounded-full border-2 border-surface ${stateDot[st]}`} />
                        <button onClick={() => { setExpanded(open ? null : p.order); setShowDetail(false) }} className="w-full text-left">
                          <div className="flex items-center gap-2">
                            <span className="font-mono text-[10px] text-muted">{p.id}</span>
                            <span className="text-[12.5px] font-medium text-ink">{p.name}</span>
                            <span className={`ml-auto text-[10px] ${stateText[st]}`}>{st}</span>
                          </div>
                          <p className="text-[11.5px] text-secondary leading-snug mt-0.5">{p.does}</p>
                          <p className="text-[10.5px] text-muted italic mt-0.5">→ {p.handoff}</p>
                        </button>
                        {open && (
                          <div className="mt-2 rounded-[8px] border border-line bg-paper p-3">
                            {actionable && <div className="mb-2 text-[9.5px] font-medium uppercase tracking-wide text-warning">Đề xuất hệ thống — không phải khuyến nghị</div>}
                            <div className="space-y-1.5">{out.headline.map((m) => <div key={m.label} className="flex items-baseline justify-between gap-3 text-[11.5px]"><span className="text-secondary">{m.label}</span><span className={`tnum font-mono font-medium ${metricTone[m.tone ?? "neutral"]}`}>{m.value}</span></div>)}</div>
                            <button onClick={() => setShowDetail((v) => !v)} className="mt-2.5 text-[10.5px] font-medium text-mineral hover:underline">{showDetail ? "Ẩn chi tiết ↑" : "Chi tiết ↓"}</button>
                            {showDetail && <ul className="mt-2 space-y-1 border-t border-line pt-2 text-[11px] leading-snug text-secondary">{out.detail.map((d) => <li key={d} className="flex gap-1.5"><span className="text-muted">·</span>{d}</li>)}</ul>}
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              </div>
            </div>
          )}
        </aside>

        {/* ── Center column ── */}
        <section className="min-w-0 bg-paper">
          {/* Run context + case selector */}
          <div className="border-b border-line px-6 py-3 shrink-0 bg-surface/60">
            <div className="flex items-center gap-2 mb-2.5">
              <span className="text-[11px] font-semibold tracking-[0.12em] uppercase text-muted">{run.label}</span>
              <span className="text-[11px] text-muted">· {run.time} · kích hoạt bởi {run.triggered}</span>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {run.cases.map((rc) => {
                const active = rc.symbol === activeSymbol
                return (
                  <button
                    key={rc.symbol}
                    onClick={() => setSymbol(rc.symbol)}
                    className={`flex items-center gap-2 h-8 pl-2.5 pr-2 rounded-[7px] border text-[12.5px] transition-colors ${active ? "border-ink bg-ink text-paper" : "border-line bg-surface text-secondary hover:border-ink/30"}`}
                  >
                    <span className="font-mono font-medium">{rc.symbol}</span>
                    <span className={`text-[10px] px-1.5 h-[18px] inline-flex items-center gap-1 rounded-full ${active ? "bg-paper/20 text-paper" : "bg-soft text-muted"}`}>
                      {!active && <span className={`w-1 h-1 rounded-full ${dotClass[rc.status]}`} />}
                      {rc.status}
                    </span>
                  </button>
                )
              })}
            </div>
          </div>

          {/* Investment case */}
          <div className="max-w-2xl w-full mx-auto px-8 py-7">
            <div className="flex items-center gap-3 mb-1">
              <Link to={`/stock/${c.symbol}`} className="font-mono text-[18px] font-semibold text-ink hover:underline">{c.symbol}</Link>
              <span className="text-[14px] text-secondary">{stock.name}</span>
              <span className="tnum font-mono text-[14px] text-ink ml-auto">{fmt(stock.price)}</span>
              <PercentChange value={stock.changePct} className="text-[13px]" />
            </div>
            <div className="flex items-center gap-2 mt-1">
              <Pill tone={statusTone[runCase.status]}>{runCase.status === "Confirmed" ? "Đã duyệt" : runCase.status === "Watching" ? "Quan sát" : "Mới phát hiện"} trong phiên này</Pill>
              <span className="text-[12px] text-muted">{runCase.note}</span>
            </div>

            <div className="mt-6">
              <SectionEyebrow>Luận điểm đầu tư</SectionEyebrow>
              <p className="font-serif text-[16px] leading-relaxed text-ink">{c.thesis}</p>
              <div className="mt-4 grid grid-cols-2 gap-4 text-[13px]">
                <div>
                  <div className="text-[11px] uppercase tracking-wide text-muted mb-1.5">Chất xúc tác</div>
                  <ul className="space-y-1 text-secondary">{c.catalysts.map((x) => <li key={x}>· {x}</li>)}</ul>
                </div>
                <div>
                  <div className="text-[11px] uppercase tracking-wide text-muted mb-1">Vùng giá mục tiêu</div>
                  <div className="tnum font-mono text-ink text-[15px]">{fmt(c.targetLow)} – {fmt(c.targetHigh)}</div>
                  <div className="text-[11px] uppercase tracking-wide text-muted mt-3 mb-1">Điều kiện vi phạm</div>
                  <div className="tnum font-mono text-loss text-[14px]">Đóng nến &lt; {fmt(c.invalidation)}</div>
                </div>
              </div>
            </div>

            <div className="mt-7 pt-6 border-t border-line">
              <div className="flex items-center gap-2 mb-3">
                <SectionEyebrow>Luận điểm phản biện</SectionEyebrow>
                <Pill tone="warning">Tác tử phản biện độc lập</Pill>
              </div>
              <ul className="space-y-2 text-[13px] text-secondary">{c.counter.map((x) => <li key={x}>· {x}</li>)}</ul>
            </div>

            <div className="mt-7 pt-6 border-t border-line">
              <div className="flex items-center gap-2 mb-3">
                <SectionEyebrow>Tín hiệu tác tử</SectionEyebrow>
                <span className="text-[11px] text-muted">· {run.label} · {activeSymbol}</span>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {signalCards.map((sc) => (
                  <AgentCard key={sc.id} id={sc.id} runId={run.id} symbol={activeSymbol} badge={sc.badge} />
                ))}
              </div>
            </div>

            <div className="mt-7 pt-6 border-t border-line">
              <SectionEyebrow>Đánh giá hiện tại</SectionEyebrow>
              <div className="flex items-center gap-3 mb-4">
                <span className="text-[20px] font-semibold text-ink">{c.bias}</span>
                <Conviction level={c.conviction} />
              </div>
              <div className="border border-line rounded-[10px] p-4 bg-paper">
                <ReasoningBlock data={c.reasoning} />
              </div>
              <div className="flex flex-wrap gap-2 mt-4">
                <Button variant="primary">Lưu vào sổ ghi chú</Button>
                <Link to="/trade"><Button variant="secondary">Kế hoạch giải ngân</Button></Link>
                <Button variant="ghost">Cài đặt cảnh báo</Button>
              </div>
            </div>
          </div>
        </section>

        {/* ── Right column: evidence inspector ── */}
        <aside className="border-t xl:border-t-0 xl:border-l border-line bg-surface self-stretch">
          <div className="px-4 pt-4">
            <SectionEyebrow>Kiểm định bằng chứng · {c.symbol}</SectionEyebrow>
            <Tabs tabs={["Tín hiệu", "Phán quyết phản biện", "Nghị quyết CIO", "Nhật ký tác tử"]} active={tab === "Sources" || tab === "Signals" ? "Tín hiệu" : tab === "Counter-Verdicts" ? "Phán quyết phản biện" : tab === "CIO Directives" ? "Nghị quyết CIO" : tab === "Agent Logs" ? "Nhật ký tác tử" : tab} onChange={setTab} />
          </div>
          <div className="p-4">
            {(tab === "Tín hiệu" || tab === "Signals" || tab === "Sources") && (
              <div className="space-y-3">
                <div className="text-[12px] text-muted mb-2 font-medium">Bằng chứng định lượng từ cơ sở dữ liệu:</div>
                <div className="border border-line rounded-[8px] p-3 bg-paper">
                  <div className="flex items-center gap-2 mb-1.5">
                    <Pill tone="gain">Hệ số xác thực</Pill>
                    <span className="ml-auto text-[11px] text-muted font-mono">Thời gian thực</span>
                  </div>
                  <div className="text-[12.5px] font-medium text-ink">Luận điểm: {c.thesis}</div>
                  <div className="mt-2 text-[12px] text-secondary space-y-1">
                    {c.catalysts.map((cat: string, i: number) => (
                      <div key={i} className="flex gap-1.5"><span className="text-gain">✓</span>{cat}</div>
                    ))}
                  </div>
                </div>
                <div className="border border-line rounded-[8px] p-3 bg-paper">
                  <div className="text-[11px] uppercase tracking-wide text-muted font-semibold mb-1">Mục tiêu & Cắt lỗ</div>
                  <div className="text-[12.5px] font-mono">
                    <div>Mục tiêu: <span className="text-gain font-semibold">{fmt(c.targetLow)} – {fmt(c.targetHigh)}</span></div>
                    <div>Vi phạm: <span className="text-loss font-semibold">Đóng nến &lt; {fmt(c.invalidation)}</span></div>
                  </div>
                </div>
              </div>
            )}
            {(tab === "Phán quyết phản biện" || tab === "Counter-Verdicts") && (
              <div className="space-y-3">
                <div className="text-[12px] text-muted mb-2 font-medium">Phán quyết từ Devil&apos;s Advocate:</div>
                {c.counter.map((cnt: string, i: number) => (
                  <div key={i} className="border border-line rounded-[8px] p-3 bg-paper">
                    <div className="flex items-center gap-2 mb-1">
                      <Pill tone="warning">Phản biện #{i + 1}</Pill>
                    </div>
                    <p className="text-[12.5px] text-secondary leading-snug">{cnt}</p>
                  </div>
                ))}
              </div>
            )}
            {(tab === "Nghị quyết CIO" || tab === "CIO Directives") && (
              <div className="space-y-3 text-[12.5px]">
                <div className="text-[12px] text-muted mb-2 font-medium">Nghị quyết điều hành danh mục:</div>
                <div className="border border-line rounded-[8px] p-3 bg-paper space-y-2">
                  <div className="flex justify-between border-b border-line pb-1.5">
                    <span className="text-muted">Chế độ thị trường</span>
                    <span className="font-semibold text-ink">BULL_TRENDING</span>
                  </div>
                  <div className="flex justify-between border-b border-line pb-1.5">
                    <span className="text-muted">Chỉ thị giải ngân</span>
                    <span className="font-semibold text-gain">{c.bias}</span>
                  </div>
                  <div className="flex justify-between border-b border-line pb-1.5">
                    <span className="text-muted">Phương pháp sizing</span>
                    <span className="font-mono text-ink">Quarter-Kelly (0.25)</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted">Trần tỷ trọng mã</span>
                    <span className="font-mono text-teal">15% NAV</span>
                  </div>
                </div>
              </div>
            )}
            {(tab === "Nhật ký tác tử" || tab === "Agent Logs") && (
              <div className="space-y-2 text-[12px] font-mono">
                <div className="text-[11px] text-muted mb-2 font-sans">Nhật ký 12 Agent từ database:</div>
                {(resource.data as AgentResponse | null)?.logs?.slice(0, 8).map((l, i) => (
                  <div key={i} className="border border-line rounded-[6px] p-2 bg-paper flex items-center justify-between">
                    <span className="text-ink capitalize">{l.agent.replace(/_/g, " ")}</span>
                    <span className="text-muted text-[11px]">
                      {l.entries?.length || 0} bản ghi
                    </span>
                  </div>
                )) || <div className="text-muted py-4 text-center">Đang tải nhật ký agent...</div>}
              </div>
            )}
          </div>
        </aside>
      </div>
    </div>
  )
}
