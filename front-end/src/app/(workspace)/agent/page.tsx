"use client"

import { useState } from "react"
import { FinancialCalendar } from "@/components/FinancialCalendar"
import { workspaceApi } from "@/lib/api"
import { useResource } from "@/lib/api/use-resource"
import { displayDate, vietnamDate } from "@/lib/financial-date"
import { Link } from "@/lib/router"
import {
  allocationDecision,
  cleanText,
  conditionLabels,
  decision,
  logOutput,
  logSummary,
  logSymbol,
  newest,
  numeric,
  object,
  related,
  timestamp,
  type RecordData,
} from "@/lib/agent-report"

interface AgentResponse {
  dates?: string[]
  theses?: RecordData[]
  counterTheses?: RecordData[]
  resolutions?: RecordData[]
  decisions?: RecordData[]
  positionHealth?: RecordData[]
  logs?: { agent: string; entries?: RecordData[] }[]
  account?: RecordData
  mode?: string
}

const agents: Record<string, string> = {
  market_surveillance: "Agent 01 · Giám sát thị trường",
  universe_discovery: "Agent 02 · Lọc cổ phiếu Lớp 0",
  equity_research: "Agent 03 · Phân tích đa nhân tố",
  investment_thesis: "Agent 04 · Luận điểm đầu tư",
  counter_thesis: "Agent 05 · Phản biện (Devil's Advocate)",
  portfolio_allocation: "Agent 06 · Phân bổ vốn Kelly",
  portfolio_risk: "Agent 07 · Kiểm soát rủi ro 5 lớp",
  trade_execution: "Agent 08 · Thực thi lệnh",
  position_monitoring: "Agent 09 · Canh gác vị thế",
  reinforcement_learning: "Agent 10 · Tự học & Hiệu chuẩn",
  system_governance: "Agent 11 · Kiểm toán hệ thống",
  strategy_cio: "Agent 12 · Trọng tài Tối cao CIO",
}

const fieldNames: Record<string, string> = {
  inputs: "Dữ liệu đầu vào", outputs: "Kết quả đầu ra", computation_trace: "Các bước tính toán",
  factor_raw_metrics: "Kết quả phân tích nhân tố", business_quality_evidence: "Bằng chứng chất lượng doanh nghiệp",
  thesis_text: "Luận điểm", pre_mortem_scenarios: "Kịch bản rủi ro", thesis_statement: "Luận điểm đầu tư",
  debate_challenge_text: "Lý do phản biện", llm_prompt_response: "Chi tiết phản biện",
  kelly_math_steps: "Tính toán phân bổ vốn (Kelly)", allocated_weight_pct: "Tỷ trọng phân bổ (%)",
  rationale: "Lý do phân bổ", es_97_5_inputs: "Đầu vào kiểm soát rủi ro", covariance_matrix: "Tính toán rủi ro",
  garch_cash_trace: "Kết quả kiểm soát rủi ro", slicing_schedule: "Các bước thực thi",
  orderbook_depth_snapshot: "Kết quả thực thi", filtered_counts: "Kết quả lọc",
  beneish_trace: "Chi tiết bộ lọc Beneish", exclusion_log: "Danh sách cổ phiếu loại bỏ",
  resolution_payload: "Hồ sơ quyết định CIO", debate_synthesis: "Tổng hợp phán quyết",
  audit_trail_verification: "Kết quả kiểm toán", ic_rolling_scores: "Điểm hiệu chuẩn",
  reward_signals: "Tín hiệu học", policy_weight_updates: "Cập nhật trọng số",
  pnl_pct: "Lãi/lỗ ghi nhận (%)", stop_loss_triggered: "Kích hoạt cắt lỗ",
  thesis_invalidated: "Luận điểm mất hiệu lực", final_resolution: "Phán quyết",
  verdict: "Kết luận", conditions: "Điều kiện thực thi", penalty_factor: "Hệ số phạt Kelly (λ)",
  weight_cap: "Trần tỷ trọng (NAV)", action_type: "Hành động", status: "Trạng thái",
  thesis_id: "Mã luận điểm", order_id: "Mã lệnh", decision_hash: "Mã kiểm toán",
  confirming_signals: "Tín hiệu xác nhận", invalidation_conditions: "Điều kiện vô hiệu",
  debate_summary: "Tổng hợp quyết định", executive_rationale: "Lý do điều hành",
  severity_tier: "Tầng rủi ro",
}

const control = "rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink focus-visible:outline-2 focus-visible:outline-mineral"
const numberFormat = new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 2 })

function number(value: unknown, suffix = "") {
  const n = numeric(value)
  return n === null ? "Chưa có dữ liệu" : `${numberFormat.format(n)}${suffix}`
}

function time(value: unknown) {
  if (!value || !Number.isFinite(Date.parse(String(value)))) return "Chưa có thời gian"
  return new Date(String(value)).toLocaleString("vi-VN", { timeZone: "Asia/Ho_Chi_Minh" })
}

function recordDate(row: RecordData) {
  return vietnamDate(row.analysis_date || row.date || timestamp(row))
}

function Value({ value }: { value: unknown }) {
  const [expanded, setExpanded] = useState(false)
  if (typeof value === "string" && /^[\[{]/.test(value.trim())) {
    try { value = JSON.parse(value) } catch { /* Legacy free text */ }
  }
  if (value === null || value === undefined || value === "") return <span className="text-muted">Chưa có dữ liệu</span>
  if (Array.isArray(value)) {
    if (!value.length) return <span className="text-muted">Không có mục nào</span>
    const visible = expanded ? value : value.slice(0, 5)
    return (
      <div className="min-w-0">
        <ul className="space-y-3">
          {visible.map((item, i) => (
            <li key={i} className="min-w-0 border-l-2 border-line/60 pl-3 text-sm leading-relaxed">
              <Value value={item} />
            </li>
          ))}
        </ul>
        {value.length > 5 && <button type="button" className="mt-2 text-xs font-medium text-mineral hover:underline" onClick={() => setExpanded(open => !open)}>
          {expanded ? "Thu gọn" : `Xem thêm ${value.length - 5} mục`}
        </button>}
      </div>
    )
  }
  if (typeof value === "object") {
    return (
      <dl className="grid min-w-0 grid-cols-1 gap-x-5 gap-y-2 text-sm sm:grid-cols-2">
        {Object.entries(value).map(([key, item]) => (
          <div key={key} className={`min-w-0 border-b border-line/40 pb-1.5 ${(item !== null && typeof item === "object") || (typeof item === "string" && item.length > 100) ? "sm:col-span-2" : ""}`}>
            <dt className="text-xs font-medium text-secondary">{fieldNames[key] || key.replaceAll("_", " ")}</dt>
            <dd className="mt-0.5 min-w-0 break-words font-medium text-ink [overflow-wrap:anywhere]"><Value value={item} /></dd>
          </div>
        ))}
      </dl>
    )
  }
  if (typeof value === "string" && value.length > 100) return <p className="max-w-[110ch] whitespace-pre-wrap">{cleanText(value)}</p>
  return <span>{typeof value === "boolean" ? (value ? "Đạt" : "Không") : cleanText(String(value))}</span>
}

function Metric({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-sm text-secondary">{label}</dt>
      <dd className="mt-2 text-xl font-semibold tabular-nums tracking-tight text-ink">{value}</dd>
      {note && <p className="mt-1 text-xs leading-relaxed text-secondary">{note}</p>}
    </div>
  )
}

function InvestmentThesisSection({ thesis }: { thesis?: RecordData }) {
  const [showPreMortem, setShowPreMortem] = useState(false)
  const signals = object(thesis?.confirming_signals)
  const invalidations = Array.isArray(thesis?.invalidation_conditions) ? thesis.invalidation_conditions : []
  const preMortems = Array.isArray(thesis?.pre_mortem_scenarios) ? thesis.pre_mortem_scenarios : []
  const range = Array.isArray(thesis?.target_price_range) ? (thesis.target_price_range as number[]) : []

  return (
    <article className="rounded-xl border border-line bg-surface p-5 space-y-4">
      {/* Header with Pipeline Step Badge */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line pb-3">
        <div className="flex items-center gap-2">
          <span className="text-[11px] font-bold uppercase tracking-wider px-2 py-0.5 rounded bg-teal/10 text-teal border border-teal/20">
            AGENT 04 · INVESTMENT THESIS
          </span>
          <span className="text-xs text-muted font-medium">Luận điểm đầu tư</span>
        </div>
        <div className="flex flex-wrap items-center gap-1.5 text-xs">
          {thesis?.catalyst_type && (
            <span className="px-2 py-0.5 rounded bg-soft text-ink font-semibold border border-line">
              Ngòi nổ: {String(thesis.catalyst_type)}
            </span>
          )}
          {thesis?.timeline_months != null && (
            <span className="px-2 py-0.5 rounded bg-soft text-secondary border border-line">
              Thời hạn: {String(thesis.timeline_months)} tháng
            </span>
          )}
        </div>
      </div>

      {/* Core Narrative Statement */}
      <div>
        <h4 className="text-xs font-semibold uppercase tracking-wider text-muted mb-1.5">Luận điểm cốt lõi (Core Investment Thesis):</h4>
        <p className="text-sm leading-relaxed text-ink whitespace-pre-wrap font-normal">
          {cleanText(String(thesis?.catalyst_description || thesis?.thesis_statement || thesis?.thesis || "Chưa có luận điểm được ghi nhận cho mã này."))}
        </p>
      </div>

      {/* Price Range & Target */}
      {range.length >= 2 && (
        <div className="rounded-lg bg-soft/40 border border-line p-2.5 flex items-center justify-between text-xs">
          <span className="text-muted">Khoảng giá mục tiêu dự kiến:</span>
          <span className="font-mono font-semibold text-ink">
            {number(range[0], " ₫")} – {number(range[1], " ₫")}
          </span>
        </div>
      )}

      {/* Independent Confirming Signals */}
      {Object.keys(signals).length > 0 && (
        <div className="space-y-1.5">
          <span className="text-xs font-semibold uppercase tracking-wider text-muted block">Bằng chứng xác nhận độc lập (3 Signals):</span>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
            {Object.entries(signals).map(([sigKey, sigVal]) => {
              const label = sigKey.includes("factor") ? "Tín hiệu nhân tố (F4/CSS)" : sigKey.includes("macro") ? "Tín hiệu vĩ mô (HMM)" : sigKey.includes("sector") ? "Tín hiệu ngành" : sigKey
              return (
                <div key={sigKey} className="rounded-lg bg-soft/50 border border-line p-2 text-xs">
                  <span className="text-[10px] text-muted block">{label}</span>
                  <span className="font-mono font-medium text-ink break-words">{cleanText(String(sigVal))}</span>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Invalidation Conditions (Trực tiếp thuộc Agent 3) */}
      {invalidations.length > 0 && (
        <div className="rounded-lg bg-warning/5 border border-warning/20 p-3 text-xs space-y-1.5">
          <span className="font-semibold uppercase tracking-wider text-warning block">
            Điều kiện tự vô hiệu hóa luận điểm (Invalidation Conditions):
          </span>
            <p className="text-secondary text-[11px]">Dùng các điều kiện dưới đây để xem xét lại luận điểm khi có dữ liệu mới:</p>
          <ul className="space-y-1 list-disc list-inside text-ink">
            {invalidations.map((item, i) => (
              <li key={i} className="leading-relaxed">{cleanText(String(item))}</li>
            ))}
          </ul>
        </div>
      )}

      {/* Pre-Mortem Scenarios (Kịch bản rủi ro dự phòng) */}
      {preMortems.length > 0 && (
        <details className="text-xs border-t border-line/60 pt-2" open={showPreMortem} onToggle={e => setShowPreMortem((e.target as HTMLDetailsElement).open)}>
          <summary className="cursor-pointer font-medium text-mineral hover:underline py-1">
            {showPreMortem ? "Thu gọn kịch bản rủi ro (Pre-mortem) ↑" : `Kịch bản rủi ro dự phòng (${preMortems.length} kịch bản) ↓`}
          </summary>
          <div className="mt-2 space-y-2">
            {preMortems.map((scene, i) => (
              <div key={i} className="rounded-lg border border-line bg-soft/40 p-2.5 text-xs text-secondary leading-relaxed">
                <span className="font-semibold text-ink">Kịch bản #{i + 1}: </span>
                <span>{cleanText(String(scene))}</span>
              </div>
            ))}
          </div>
        </details>
      )}
    </article>
  )
}

function CounterThesisSection({ counter }: { counter?: RecordData }) {
  const holes = Array.isArray(counter?.holes) ? counter.holes : []
  const blockReasons = Array.isArray(counter?.block_reasons) ? counter.block_reasons : []
  const constraints = object(counter?.execution_constraints)
  const [showFull, setShowFull] = useState(false)
  const cts = numeric(counter?.cts_score)

  return (
    <article className="rounded-xl border border-line bg-surface p-5 space-y-4">
      {/* Header with Pipeline Step Badge */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line pb-3">
        <div className="flex items-center gap-2">
          <span className="text-[11px] font-bold uppercase tracking-wider px-2 py-0.5 rounded bg-warning/10 text-warning border border-warning/20">
            AGENT 05 · COUNTER-THESIS (DEVIL&apos;S ADVOCATE)
          </span>
          <span className="text-xs text-muted font-medium">Phản biện độc lập & Thẩm định rủi ro</span>
        </div>
        {cts !== null && (
          <span className={`font-mono text-xs font-semibold px-2 py-0.5 rounded border ${cts >= 80 ? "bg-loss/10 text-loss border-loss/20" : cts >= 50 ? "bg-warning/10 text-warning border-warning/20" : "bg-teal/10 text-teal border-teal/20"}`}>
            CTS: {cts.toFixed(1)}/100{counter?.verdict ? ` · ${String(counter.verdict)}` : ""}
          </span>
        )}
      </div>

      {/* Red Team Overall Rationale (Đánh giá tổng quan) */}
      {counter?.rationale ? (
        <div>
          <h4 className="text-xs font-semibold uppercase tracking-wider text-muted mb-1.5">Đánh giá phản biện tổng quan (Red Team Critique):</h4>
          <p className="text-sm leading-relaxed text-secondary bg-soft/40 p-3 rounded-lg border border-line/60">
            {cleanText(String(counter.rationale))}
          </p>
        </div>
      ) : (
        <p className="text-sm text-secondary">Chưa có phản biện liên kết với luận điểm này.</p>
      )}

      {/* Main challenges/holes list */}
      {holes.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs font-semibold uppercase tracking-wider text-muted">Lỗ hổng & Thách thức phản biện ({holes.length}):</p>
          <ul className="space-y-2">
            {holes.map((hole, i) => (
              <li key={i} className="flex items-start gap-2 rounded-lg bg-soft/50 p-2.5 text-xs leading-relaxed border border-line/40">
                <span className="mt-0.5 text-warning font-bold shrink-0">⚠</span>
                <span className="text-ink">{cleanText(String(hole))}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Recommended Execution Constraints from Red Team */}
      {Object.keys(constraints).length > 0 && (
        <div className="rounded-lg bg-soft/60 border border-line p-3 text-xs space-y-2">
          <span className="font-semibold uppercase text-secondary block text-[11px]">Ràng buộc khuyến nghị từ Red Team:</span>
          {constraints.reason && <p className="text-ink font-medium leading-relaxed">{cleanText(String(constraints.reason))}</p>}
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 pt-1 border-t border-line/60">
            {constraints.entry_ceiling_price != null && (
              <div><span className="text-muted block text-[10px]">Trần giá mua an toàn</span><span className="font-mono font-semibold">{number(constraints.entry_ceiling_price, " ₫")}</span></div>
            )}
            {constraints.stop_loss_pct_override != null && (
              <div><span className="text-muted block text-[10px]">Mức Stop-loss siết chặt</span><span className="font-mono font-semibold text-loss">{(Number(constraints.stop_loss_pct_override) * 100).toFixed(1)}%</span></div>
            )}
            {constraints.max_position_size_multiplier != null && (
              <div><span className="text-muted block text-[10px]">Hạ quy mô vị thế</span><span className="font-mono font-semibold text-warning">×{String(constraints.max_position_size_multiplier)}</span></div>
            )}
          </div>
        </div>
      )}

      {/* Block reasons if any */}
      {blockReasons.length > 0 && (
        <div className="rounded-lg bg-loss/10 border border-loss/20 p-3 text-loss text-xs space-y-1">
          <span className="font-semibold uppercase block">Lý do chặn (Block Causes):</span>
          <ul className="list-disc list-inside">
            {blockReasons.map((reason, i) => <li key={i}>{cleanText(String(reason))}</li>)}
          </ul>
        </div>
      )}

      {/* Compact stats breakdown */}
      {counter && (
        <details className="mt-3 border-t border-line/60 pt-2 text-xs" open={showFull} onToggle={e => setShowFull((e.target as HTMLDetailsElement).open)}>
          <summary className="cursor-pointer font-medium text-mineral hover:underline py-1">
            {showFull ? "Thu gọn thông số phản biện ↑" : "Chi tiết thông số & quy tắc phản biện ↓"}
          </summary>
          <div className="mt-3 grid grid-cols-2 sm:grid-cols-3 gap-2 bg-soft/40 p-3 rounded-lg border border-line">
            <div><span className="text-muted block text-[11px]">Điểm CTS cơ sở</span><span className="font-mono font-semibold">{String(counter.base_cts ?? "—")}</span></div>
            <div><span className="text-muted block text-[11px]">Hệ số tương tác</span><span className="font-mono font-semibold">{counter.interaction_multiplier == null ? "—" : `×${String(counter.interaction_multiplier)}`}</span></div>
            <div><span className="text-muted block text-[11px]">Hệ số chế độ TT</span><span className="font-mono font-semibold">{counter.regime_multiplier == null ? "—" : `×${String(counter.regime_multiplier)}`}</span></div>
            <div><span className="text-muted block text-[11px]">Quy tắc 3 tín hiệu</span><span className="font-medium">{typeof counter.rule_of_three_passed === "boolean" ? counter.rule_of_three_passed ? "Đạt (Rule of 3)" : "Không đạt" : "—"}</span></div>
            <div><span className="text-muted block text-[11px]">Bán tháo kỹ thuật</span><span className="font-medium">{typeof counter.is_capitulation_rebound === "boolean" ? counter.is_capitulation_rebound ? "Có" : "Không" : "—"}</span></div>
            <div><span className="text-muted block text-[11px]">Thời điểm đánh giá</span><span className="font-mono">{time(counter.evaluated_at)}</span></div>
          </div>
        </details>
      )}
    </article>
  )
}

function CioResolutionDossier({ resolution }: { resolution: RecordData }) {
  const payload = object(resolution.verdict_payload)
  const hasConditions = Array.isArray(payload.conditions)
  const conditions = hasConditions ? (payload.conditions as string[]) : []
  const weightCap = numeric(payload.weight_cap ?? payload.allocated_weight_cap)
  const penalty = numeric(payload.penalty_factor)
  const tier = String(payload.severity_tier || resolution.decision_type || "")
  const hash = String(resolution.decision_hash || payload.decision_hash || "")
  const [open, setOpen] = useState(true)

  return (
    <section className="rounded-xl border border-line bg-surface p-5 space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line pb-3">
        <div className="flex items-center gap-2">
          <span className="text-[11px] font-bold uppercase tracking-wider px-2 py-0.5 rounded bg-rose-500/10 text-rose-600 border border-rose-500/20">
            AGENT 12 · STRATEGY CIO
          </span>
          <span className="text-xs text-muted font-medium">Trọng tài Tối cao & Quyết định giải ngân</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs px-2.5 py-0.5 rounded bg-teal/10 text-teal font-medium border border-teal/20">
            {hasConditions ? `${conditions.length} điều kiện ràng buộc` : "Chưa có danh sách điều kiện"}
          </span>
          <button onClick={() => setOpen(!open)} className="text-xs text-mineral font-medium hover:underline">
            {open ? "Thu gọn ↑" : "Mở rộng ↓"}
          </button>
        </div>
      </div>

      <p className="text-xs text-secondary leading-relaxed">
        Hội đồng Trọng tài Tối cao CIO (Agent 12) phân xử mâu thuẫn đối nghịch giữa Luận điểm đầu tư (Agent 04) và Phản biện độc lập (Agent 05) theo 3 Tầng rủi ro Hiến pháp để phê duyệt hạn ngạch hoặc áp đặt điều kiện giải ngân cho Agent 06.
      </p>

      {open && (
        <div className="space-y-4 pt-1 text-sm">
          {/* Conditions pills */}
          <div>
            <h4 className="text-xs font-semibold uppercase tracking-wider text-muted mb-2">Điều kiện giải ngân ràng buộc (Execution Conditions):</h4>
            {conditions.length > 0 ? (
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2.5">
                {conditions.map((code) => {
                  const item = conditionLabels[code] || { label: code.replaceAll("_", " "), desc: "Quy tắc kiểm soát rủi ro bắt buộc", tone: "border-line bg-soft text-ink" }
                  return (
                    <div key={code} className={`rounded-lg border p-3 ${item.tone}`}>
                      <div className="font-semibold text-xs leading-snug">{item.label}</div>
                      <div className="text-[11px] text-secondary mt-1 leading-relaxed">{item.desc}</div>
                    </div>
                  )
                })}
              </div>
            ) : (
              <p className="text-xs text-secondary">{hasConditions ? "Bản ghi xác nhận không có điều kiện bổ sung." : "Bản ghi không cung cấp danh sách điều kiện."}</p>
            )}
          </div>

          {/* Parameters Grid */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 rounded-lg bg-soft/50 border border-line p-3">
            <div>
              <span className="block text-[11px] text-muted">Tầng rủi ro (Severity Tier)</span>
              <span className="font-semibold text-xs text-ink">{!tier ? "—" : tier.includes("TIER_3") ? "Tầng 3 (Rủi ro thường)" : tier.includes("TIER_2") ? "Tầng 2 (Nguy cơ cận biên)" : tier.includes("TIER_1") ? "Tầng 1 (Vi phạm Hard Law)" : tier}</span>
            </div>
            <div>
              <span className="block text-[11px] text-muted">Trần tỷ trọng an toàn</span>
              <span className="font-mono font-semibold text-xs text-ink">{weightCap !== null ? `${(weightCap * 100).toFixed(1)}% NAV` : "—"}</span>
            </div>
            <div>
              <span className="block text-[11px] text-muted">Hệ số phạt Kelly (λ)</span>
              <span className="font-mono font-semibold text-xs text-ink">{penalty !== null ? penalty.toFixed(2) : "—"}</span>
            </div>
            <div>
              <span className="block text-[11px] text-muted">Thẩm quyền phán quyết</span>
              <span className="font-semibold text-xs text-teal">{String(resolution.decision_type || "—")}</span>
            </div>
          </div>

          {/* Cryptographic audit signature */}
          <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] text-secondary border-t border-line/60 pt-3 font-mono">
            <div className="flex items-center gap-2">
              <span className="text-muted">Mã phán quyết:</span>
              <span>{String(resolution.resolution_id || "—")}</span>
            </div>
            {hash && (
              <div className="flex items-center gap-1.5" title={hash}>
                <span className="text-muted">Mã băm kiểm toán:</span>
                <span className="bg-soft px-1.5 py-0.5 rounded border border-line">{hash.slice(0, 16)}…</span>
              </div>
            )}
          </div>
        </div>
      )}
    </section>
  )
}

function KellyMathTrace({ trace, weight }: { trace: RecordData; weight?: unknown }) {
  const p = numeric(trace.p_calibrated)
  const b = numeric(trace.payoff_ratio)
  const quarterKelly = numeric(trace.quarter_kelly_raw)
  const regimeScaler = numeric(trace.market_regime_scaler)
  const portTarget = numeric(trace.portfolio_target)
  const incWeight = numeric(trace.incremental_weight)
  const minCash = numeric(trace.min_cash_target)

  return (
    <div className="rounded-lg border border-line bg-soft/40 p-3 text-xs">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5">
        <div><span className="text-muted block text-[11px]">Xác suất thắng ($P$)</span><span className="font-mono font-semibold">{p !== null ? `${(p * 100).toFixed(1)}%` : "—"}</span></div>
        <div><span className="text-muted block text-[11px]">Tỷ lệ lãi/lỗ ($b$)</span><span className="font-mono font-semibold">{b !== null ? b.toFixed(2) : "—"}</span></div>
        <div><span className="text-muted block text-[11px]">Quarter Kelly gốc</span><span className="font-mono font-semibold">{quarterKelly !== null ? `${(quarterKelly * 100).toFixed(1)}%` : "—"}</span></div>
        <div><span className="text-muted block text-[11px]">Hệ số thị trường</span><span className="font-mono font-semibold">{regimeScaler !== null ? `×${regimeScaler.toFixed(2)}` : "—"}</span></div>
        <div><span className="text-muted block text-[11px]">Mục tiêu danh mục</span><span className="font-mono font-semibold text-teal">{portTarget !== null ? `${(portTarget * 100).toFixed(2)}% NAV` : "—"}</span></div>
        <div><span className="text-muted block text-[11px]">Đề xuất tăng tỷ trọng</span><span className="font-mono font-semibold text-gain">{incWeight !== null ? `+${(incWeight * 100).toFixed(2)}% NAV` : weight != null ? `${weight}% NAV` : "—"}</span></div>
        <div><span className="text-muted block text-[11px]">Ngưỡng Deadband</span><span className="font-medium">{typeof trace.deadband_passed === "boolean" ? trace.deadband_passed ? "Đạt điều kiện" : "Bỏ qua" : "—"}</span></div>
        <div><span className="text-muted block text-[11px]">Dự trữ tiền mặt tối thiểu</span><span className="font-mono font-semibold">{minCash !== null ? `${(minCash * 100).toFixed(0)}% NAV` : "—"}</span></div>
      </div>
    </div>
  )
}

function MacroUniverseSection({ msEntry, udEntry }: { msEntry?: RecordData; udEntry?: RecordData }) {
  const msOutputs = object(msEntry?.outputs)
  const regime = String(msOutputs.current_regime || "")
  const alertLevel = String(msOutputs.alert_level || "")
  const vix = numeric(msOutputs.vix_vn_analog)
  const csad = numeric(msOutputs.csad_score)
  const advDecl = numeric(msOutputs.adv_decl_ratio)
  const cashTarget = numeric(msOutputs.garch_cash_target_pct)
  const breadthMa20 = numeric(msOutputs.breadth_above_ma20_pct)
  const hmmProbs = object(msOutputs.hmm_probabilities)

  const udCounts = object(udEntry?.filtered_counts)
  const exclusions = Array.isArray(udCounts.exclusion_log) ? (udCounts.exclusion_log as RecordData[]) : (Array.isArray(udEntry?.exclusion_log) ? (udEntry.exclusion_log as RecordData[]) : [])
  const [showExclusions, setShowExclusions] = useState(false)

  const regimeInfo: Record<string, { label: string; tone: string; desc: string }> = {
    RANGE_BOUND: { label: "Biên hẹp (Range-Bound)", tone: "bg-warning/10 text-warning border-warning/20", desc: "Dao động tích lũy, ưu tiên phòng thủ & giải ngân thận trọng." },
    BULL_MARKET: { label: "Tăng trưởng (Bull Market)", tone: "bg-gain/10 text-gain border-gain/20", desc: "Xu hướng tăng giá chủ đạo, mở rộng tỷ trọng giải ngân." },
    BEAR_MARKET: { label: "Suy thoái (Bear Market)", tone: "bg-loss/10 text-loss border-loss/20", desc: "Thị trường con gấu rủi ro cao, ưu tiên nắm giữ tiền mặt." },
  }
  const currentRegime = regimeInfo[regime] || { label: regime || "Chưa có dữ liệu", tone: "bg-soft text-secondary border-line", desc: regime ? "Chưa có mô tả cho chế độ này." : "Chưa có bản ghi giám sát thị trường trong phạm vi đã chọn." }

  return (
    <div className="grid gap-5 lg:grid-cols-2 items-stretch">
      {/* AGENT 01: Market Surveillance */}
      <article className="rounded-xl border border-line bg-surface p-5 space-y-4 flex flex-col justify-between">
        <div>
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line pb-3">
            <div className="flex items-center gap-2">
              <span className="text-[11px] font-bold uppercase tracking-wider px-2 py-0.5 rounded bg-sky-500/10 text-sky-600 border border-sky-500/20">
                AGENT 01 · MARKET SURVEILLANCE
              </span>
              <span className="text-xs text-muted font-medium">Giám sát vĩ mô & Chế độ HMM</span>
            </div>
            <div className="flex items-center gap-1.5">
              <span className={`text-xs px-2 py-0.5 rounded font-semibold border ${currentRegime.tone}`}>
                {currentRegime.label}
              </span>
              {alertLevel && (
                <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${alertLevel === "RED" ? "bg-loss/20 text-loss" : alertLevel === "WARNING" ? "bg-warning/20 text-warning" : "bg-gain/20 text-gain"}`}>
                  CẢNH BÁO: {alertLevel}
                </span>
              )}
            </div>
          </div>

          <p className="mt-3 text-xs leading-relaxed text-secondary">{currentRegime.desc}</p>

          <div className="mt-3 grid grid-cols-2 sm:grid-cols-3 gap-2.5">
            <div className="rounded-lg bg-soft/50 border border-line p-2.5">
              <span className="text-[10px] text-muted block">VIX-VN Analog</span>
              <span className="font-mono text-sm font-bold text-ink">{vix !== null ? vix.toFixed(2) : "—"}</span>
              <span className="text-[10px] text-secondary block mt-0.5">{vix === null ? "Chưa có quan sát" : vix > 20 ? "Trên ngưỡng 20" : "Dưới ngưỡng 20"}</span>
            </div>
            <div className="rounded-lg bg-soft/50 border border-line p-2.5">
              <span className="text-[10px] text-muted block">Chỉ số bầy đàn CSAD</span>
              <span className="font-mono text-sm font-bold text-ink">{csad !== null ? csad.toFixed(4) : "—"}</span>
              <span className="text-[10px] text-secondary block mt-0.5">{msOutputs.herding_status ? String(msOutputs.herding_status) : "Chưa có kết luận"}</span>
            </div>
            <div className="rounded-lg bg-soft/50 border border-line p-2.5">
              <span className="text-[10px] text-muted block">Tỷ lệ Tăng/Giảm (A/D)</span>
              <span className="font-mono text-sm font-bold text-ink">{advDecl !== null ? advDecl.toFixed(2) : "—"}</span>
              <span className="text-[10px] text-secondary block mt-0.5">{advDecl === null ? "Chưa có quan sát" : advDecl < 1 ? "Số mã giảm nhiều hơn tăng" : advDecl > 1 ? "Số mã tăng nhiều hơn giảm" : "Cân bằng"}</span>
            </div>
            <div className="rounded-lg bg-soft/50 border border-line p-2.5">
              <span className="text-[10px] text-muted block">Dự trữ TM GARCH</span>
              <span className="font-mono text-sm font-bold text-teal">{cashTarget !== null ? `${cashTarget.toFixed(1)}% NAV` : "—"}</span>
              <span className="text-[10px] text-secondary block mt-0.5">Mục tiêu phòng thủ</span>
            </div>
            <div className="rounded-lg bg-soft/50 border border-line p-2.5 sm:col-span-2">
              <span className="text-[10px] text-muted block">Độ rộng thị trường &gt; MA20</span>
              <div className="flex items-center gap-2 mt-1">
                {breadthMa20 !== null ? <>
                  <div className="flex-1 bg-line/60 rounded-full h-2 overflow-hidden"><div className="bg-sky-500 h-2 rounded-full" style={{ width: `${Math.min(100, Math.max(0, breadthMa20))}%` }} /></div>
                  <span className="font-mono text-xs font-semibold text-ink">{breadthMa20.toFixed(1)}%</span>
                </> : <span className="text-xs text-muted">Chưa có dữ liệu độ rộng</span>}
              </div>
            </div>
          </div>

          {Object.keys(hmmProbs).length > 0 && (
            <div className="mt-3 rounded-lg border border-line bg-soft/30 p-2.5 text-xs">
              <span className="text-[10px] font-semibold text-muted block mb-1.5 uppercase tracking-wider">Xác suất phân bổ chế độ thị trường HMM:</span>
              <div className="grid grid-cols-3 gap-2 text-center">
                {Object.entries(hmmProbs).map(([k, prob]) => {
                  const val = numeric(prob)
                  const pct = val !== null ? (val * 100).toFixed(1) : "—"
                  return (
                    <div key={k} className="p-1 rounded bg-surface border border-line/50">
                      <span className="block text-[10px] text-secondary truncate">{k.replace("_MARKET", "")}</span>
                      <span className="font-mono font-bold text-ink text-xs">{pct}%</span>
                    </div>
                  )
                })}
              </div>
            </div>
          )}
        </div>
        <div className="text-[10px] text-muted border-t border-line/60 pt-2 flex justify-between">
          <span>Động cơ: RegimeEngineV2 · GARCH Cash Optimizer</span>
          <span>{msEntry ? time(timestamp(msEntry)) : "Chưa có log trong phạm vi này"}</span>
        </div>
      </article>

      {/* AGENT 02: Universe Discovery */}
      <article className="rounded-xl border border-line bg-surface p-5 space-y-4 flex flex-col justify-between">
        <div>
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line pb-3">
            <div className="flex items-center gap-2">
              <span className="text-[11px] font-bold uppercase tracking-wider px-2 py-0.5 rounded bg-indigo-500/10 text-indigo-600 border border-indigo-500/20">
                AGENT 02 · UNIVERSE DISCOVERY
              </span>
              <span className="text-xs text-muted font-medium">Khám phá vũ trụ & Lọc cứng Lớp 0</span>
            </div>
            <span className="text-xs px-2 py-0.5 rounded bg-soft text-secondary font-medium border border-line">
              Điều kiện lọc · không phải kết quả lượt chạy
            </span>
          </div>

          <p className="mt-3 text-xs leading-relaxed text-secondary">
            Cổng sàng lọc khắt khe loại bỏ tận gốc rủi ro gian lận kế toán và thanh khoản ảo trước khi cho phép cổ phiếu bước vào phễu phân tích đa nhân tố.
          </p>

          <div className="mt-3 space-y-2">
            <div className="flex items-center justify-between rounded-lg bg-soft/50 border border-line p-2.5 text-xs">
              <div className="flex items-center gap-2">
                <span className="text-muted font-bold">•</span>
                <div>
                  <span className="font-semibold text-ink block">Lọc cứng Beneish M-Score (M ≤ -1.78)</span>
                  <span className="text-[10px] text-secondary">Loại trừ rủi ro thao túng báo cáo tài chính doanh nghiệp</span>
                </div>
              </div>
              <span className="font-mono font-semibold text-secondary text-xs">M ≤ -1,78</span>
            </div>

            <div className="flex items-center justify-between rounded-lg bg-soft/50 border border-line p-2.5 text-xs">
              <div className="flex items-center gap-2">
                <span className="text-muted font-bold">•</span>
                <div>
                  <span className="font-semibold text-ink block">Ngưỡng thanh khoản tối thiểu (ADTV20 ≥ 15 tỷ VND)</span>
                  <span className="text-[10px] text-secondary">Đảm bảo dòng tiền ra vào khả thi không gãy giá</span>
                </div>
              </div>
              <span className="font-mono font-semibold text-secondary text-xs">ADTV20 ≥ 15 tỷ</span>
            </div>

            <div className="flex items-center justify-between rounded-lg bg-soft/50 border border-line p-2.5 text-xs">
              <div className="flex items-center gap-2">
                <span className="text-muted font-bold">•</span>
                <div>
                  <span className="font-semibold text-ink block">Danh sách loại trừ rủi ro</span>
                  <span className="text-[10px] text-secondary">
                    {!udEntry ? "Chưa có log lượt quét trong phạm vi này" : exclusions.length > 0 ? `Lượt chạy ghi nhận ${exclusions.length} mã bị loại` : "Lượt chạy không ghi nhận mã bị loại"}
                  </span>
                </div>
              </div>
              {exclusions.length > 0 && (
                <button
                  onClick={() => setShowExclusions(!showExclusions)}
                  className="text-xs text-mineral font-semibold hover:underline"
                >
                  {showExclusions ? "Thu gọn ↑" : `Xem ${exclusions.length} mã loại trừ ↓`}
                </button>
              )}
            </div>
          </div>

          {showExclusions && exclusions.length > 0 && (
            <div className="mt-3 max-h-48 overflow-y-auto space-y-1.5 border border-line rounded-lg p-2 bg-soft/40">
              {exclusions.slice(0, 10).map((ex, i) => (
                <div key={i} className="flex items-center justify-between text-[11px] p-1.5 rounded bg-surface border border-line/40">
                  <span className="font-mono font-bold text-ink">{String(ex.ticker || "N/A")}</span>
                  <span className="text-warning font-medium">{String(ex.reason || "EXCLUDED")}</span>
                  <span className="text-secondary truncate max-w-[200px] text-[10px]">{String(ex.detail || "")}</span>
                </div>
              ))}
              {exclusions.length > 10 && (
                <p className="text-[10px] text-center text-muted">Và {exclusions.length - 10} mã khác được ghi nhận trong nhật ký...</p>
              )}
            </div>
          )}
        </div>
        <div className="text-[10px] text-muted border-t border-line/60 pt-2 flex justify-between">
          <span>Động cơ: BeneishMScoreEngine · LiquidityFilter</span>
          <span>{udEntry ? time(timestamp(udEntry)) : "Chưa có log trong phạm vi này"}</span>
        </div>
      </article>
    </div>
  )
}

function EquityResearchSection({ entry, ticker }: { entry?: RecordData; ticker: string }) {
  const metrics = object(entry?.factor_raw_metrics)
  const css = numeric(metrics.css)
  const conviction = String(metrics.conviction || "")
  const percentile = numeric(metrics.percentile)
  const pe = numeric(metrics.pe)
  const pb = numeric(metrics.pb)
  const roe = numeric(metrics.roe)
  const de = numeric(metrics.de)

  const factors = [
    { key: "f1_value", label: "F1 · Giá trị (Value)", score: numeric(metrics.f1_value), note: "P/E, P/B, định giá chiết khấu" },
    { key: "f2_quality", label: "F2 · Chất lượng (Quality)", score: numeric(metrics.f2_quality), note: "Biên lợi nhuận, ROE, đòn bẩy" },
    { key: "f3_momentum", label: "F3 · Động lượng (Momentum)", score: numeric(metrics.f3_momentum), note: "Sức mạnh giá RS, so sánh VN-Index" },
    { key: "f4_earnings", label: "F4 · Lợi nhuận (Earnings)", score: numeric(metrics.f4_earnings), note: "Tăng trưởng EPS, chất lượng dồn tích" },
    { key: "f5_flow", label: "F5 · Dòng tiền (Flow)", score: numeric(metrics.f5_flow), note: "Khối ngoại, tự doanh & tích lũy tổ chức" },
    { key: "f6_technical", label: "F6 · Kỹ thuật (Technical)", score: numeric(metrics.f6_technical), note: "Vị thế MA20/MA50, khối lượng đột biến" },
  ]

  const researchGate = object(metrics.research_gate)
  const eligible = typeof researchGate.eligible_for_thesis === "boolean" ? researchGate.eligible_for_thesis : null

  return (
    <article className="rounded-xl border border-line bg-surface p-5 space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line pb-3">
        <div className="flex items-center gap-2">
          <span className="text-[11px] font-bold uppercase tracking-wider px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-600 border border-emerald-500/20">
            AGENT 03 · EQUITY RESEARCH
          </span>
          <span className="text-xs text-muted font-medium">Chấm điểm đa nhân tố F1–F6 & Điểm tổng hợp CSS · {ticker}</span>
        </div>
        <div className="flex items-center gap-2">
          <span className={`text-xs px-2 py-0.5 rounded font-semibold border ${conviction ? conviction === "A" ? "bg-gain/10 text-gain border-gain/20" : "bg-warning/10 text-warning border-warning/20" : "bg-soft text-secondary border-line"}`}>
            {conviction ? `Hạng ${conviction}` : "Chưa xếp hạng"}
          </span>
          {percentile !== null && (
            <span className="text-xs font-mono px-2 py-0.5 rounded bg-soft text-ink border border-line">
              Top {(100 - percentile).toFixed(1)}%
            </span>
          )}
        </div>
      </div>

      {css !== null ? (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 rounded-lg bg-soft/50 border border-line p-3">
            <div>
              <span className="block text-[11px] text-muted">Điểm tổng hợp CSS</span>
              <span className="text-xl font-bold font-mono text-gain">{css.toFixed(1)} <span className="text-xs text-secondary font-normal">/ 100</span></span>
            </div>
            <div>
              <span className="block text-[11px] text-muted">Hạng khuyến nghị</span>
              <span className="text-xl font-bold text-ink">{conviction}</span>
            </div>
            <div>
              <span className="block text-[11px] text-muted">Vị thế toàn thị trường</span>
              <span className="text-xl font-bold font-mono text-teal">{percentile !== null ? `${percentile.toFixed(1)}%` : "—"}</span>
            </div>
            <div>
              <span className="block text-[11px] text-muted">Điều kiện lập luận điểm</span>
              <span className={`text-xs font-semibold px-1.5 py-0.5 rounded inline-block mt-1 ${eligible === null ? "bg-soft text-secondary" : eligible ? "bg-gain/10 text-gain" : "bg-loss/10 text-loss"}`}>
                {eligible === null ? "Chưa có dữ liệu" : eligible ? "Đạt chuẩn đầu tư" : "Chưa đạt chuẩn"}
              </span>
            </div>
          </div>

          <div className="space-y-2.5">
            <h4 className="text-xs font-semibold uppercase tracking-wider text-muted">Hồ sơ 6 nhân tố lượng hóa (F1–F6 Multi-Factor Profile):</h4>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
              {factors.map(f => {
                const s = f.score
                const pct = s !== null ? Math.min(100, Math.max(0, s)) : null
                return (
                  <div key={f.key} className="rounded-lg bg-soft/40 border border-line p-2.5">
                    <div className="flex items-center justify-between text-xs mb-1">
                      <span className="font-semibold text-ink">{f.label}</span>
                      <span className="font-mono font-bold text-ink">{s !== null ? s.toFixed(1) : "—"}</span>
                    </div>
                    {pct !== null && <div className="bg-line/60 rounded-full h-1.5 overflow-hidden mb-1"><div className={`h-1.5 rounded-full ${pct >= 70 ? "bg-gain" : pct >= 50 ? "bg-teal" : pct >= 30 ? "bg-warning" : "bg-loss"}`} style={{ width: `${pct}%` }} /></div>}
                    <span className="text-[10px] text-secondary block">{f.note}</span>
                  </div>
                )
              })}
            </div>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 pt-2 border-t border-line/60 text-xs">
            <div><span className="text-muted block text-[10px]">Chỉ số P/E</span><span className="font-mono font-semibold">{pe !== null ? `${pe.toFixed(2)}x` : "—"}</span></div>
            <div><span className="text-muted block text-[10px]">Chỉ số P/B</span><span className="font-mono font-semibold">{pb !== null ? `${pb.toFixed(2)}x` : "—"}</span></div>
            <div><span className="text-muted block text-[10px]">Lợi nhuận/Vốn (ROE)</span><span className="font-mono font-semibold text-gain">{roe !== null ? `${(roe * 100).toFixed(1)}%` : "—"}</span></div>
            <div><span className="text-muted block text-[10px]">Đòn bẩy Nợ/Vốn (D/E)</span><span className="font-mono font-semibold">{de !== null ? de.toFixed(2) : "—"}</span></div>
          </div>
        </>
      ) : (
        <p className="text-sm text-secondary">Chưa ghi nhận bản ghi phân tích nhân tố riêng cho {ticker} trong phiên này.</p>
      )}
    </article>
  )
}

function PortfolioRiskSection({ entry, ticker }: { entry?: RecordData; ticker: string }) {
  const garch = object(entry?.garch_cash_trace)
  const dec = object(garch.decision)
  const action = String(dec.action || garch.risk_status || "")
  const origShares = numeric(dec.original_shares)
  const approvedShares = numeric(dec.approved_shares)
  const reduction = numeric(dec.exposure_reduction_pct)
  const approvedWeight = numeric(dec.approved_weight_pct)
  const minCash = numeric(dec.min_cash_target_pct)
  const rationale = cleanText(String(dec.rationale || ""))
  const hardLaws = object(garch.hard_laws)
  const cdc = object(garch.cdc)
  const drawdown = object(garch.drawdown)
  const tailRisk = object(garch.tail_risk)
  const tape = object(garch.tape_anomaly)
  const breadth = object(garch.market_breadth)
  const proposedCheck = object(garch.proposed_order_check)

  const actionConfig: Record<string, { label: string; tone: string }> = {
    PASS: { label: "THÔNG QUA (PASS)", tone: "bg-gain/10 text-gain border-gain/20" },
    APPROVE: { label: "PHÊ DUYỆT (APPROVE)", tone: "bg-gain/10 text-gain border-gain/20" },
    REDUCE: { label: "CẮT GIẢM QUY MÔ (REDUCE)", tone: "bg-warning/10 text-warning border-warning/20" },
    BLOCK: { label: "VETO / CHẶN LỆNH (BLOCK)", tone: "bg-loss/10 text-loss border-loss/20" },
  }
  const currentAction = actionConfig[action] || { label: "CHƯA CÓ KẾT QUẢ", tone: "bg-soft text-secondary border-line" }
  const checks = [
    ...Object.entries(hardLaws).filter(([key]) => key !== "all_passed").map(([key, value]) => [key.replaceAll("_", " "), value] as const),
    ...(tape.severity != null ? [["Dị thường giá/khối lượng", tape.severity] as const] : []),
    ...(tailRisk.tail_risk_verdict != null ? [["Tail risk", tailRisk.tail_risk_verdict] as const] : []),
    ...(drawdown.tier != null ? [["Drawdown", drawdown.tier] as const] : []),
    ...(breadth.health_tier != null ? [["Độ rộng thị trường", breadth.health_tier] as const] : []),
    ...(proposedCheck.passed != null ? [["Kiểm tra lệnh đề xuất", proposedCheck.passed] as const] : []),
  ]

  return (
    <article className="rounded-xl border border-line bg-surface p-5 space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line pb-3">
        <div className="flex items-center gap-2">
          <span className="text-[11px] font-bold uppercase tracking-wider px-2 py-0.5 rounded bg-purple-500/10 text-purple-600 border border-purple-500/20">
            AGENT 07 · PORTFOLIO RISK
          </span>
          <span className="text-xs text-muted font-medium">Cổng kiểm soát rủi ro 5 lớp & Quyền Veto tối cao · {ticker}</span>
        </div>
        <span className={`text-xs px-2.5 py-1 rounded font-bold border ${currentAction.tone}`}>
          {currentAction.label}
        </span>
      </div>

      <p className="text-xs text-secondary leading-relaxed">
        Chỉ các trạng thái có trong bản ghi Agent 07 mới được hiển thị. Thiếu trường kiểm soát không được xem là đã thông qua.
      </p>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 rounded-lg bg-soft/50 border border-line p-3">
        <div>
          <span className="block text-[11px] text-muted">Khối lượng đề xuất gốc</span>
          <span className="font-mono font-semibold text-xs text-ink">{origShares !== null ? `${number(origShares)} CP` : "—"}</span>
        </div>
        <div>
          <span className="block text-[11px] text-muted">Khối lượng phê duyệt an toàn</span>
          <span className="font-mono font-bold text-xs text-ink">{approvedShares !== null ? `${number(approvedShares)} CP` : "—"}</span>
          {reduction !== null && reduction > 0 && (
            <span className="text-[10px] text-loss font-semibold ml-1">(-{reduction.toFixed(1)}%)</span>
          )}
        </div>
        <div>
          <span className="block text-[11px] text-muted">Tỷ trọng phê duyệt</span>
          <span className="font-mono font-bold text-xs text-teal">{approvedWeight !== null ? `${approvedWeight.toFixed(2)}% NAV` : "—"}</span>
        </div>
        <div>
          <span className="block text-[11px] text-muted">Dự trữ tiền mặt tối thiểu</span>
          <span className="font-mono font-semibold text-xs text-ink">{minCash !== null ? `${minCash.toFixed(0)}% NAV` : "—"}</span>
        </div>
      </div>

      {rationale && (
        <div className="rounded-lg bg-warning/5 border border-warning/20 p-3 text-xs">
          <span className="font-semibold text-warning block mb-1">Căn cứ can thiệp kiểm soát rủi ro:</span>
          <p className="text-ink leading-relaxed">{rationale}</p>
        </div>
      )}

      {checks.length > 0 ? <div className="space-y-2">
        <h4 className="text-xs font-semibold uppercase tracking-wider text-muted">Tín hiệu kiểm soát được ghi nhận</h4>
        <dl className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {checks.map(([label, value]) => <div key={label} className="rounded-lg border border-line bg-soft/40 p-2.5 text-xs">
            <dt className="font-semibold capitalize text-secondary">{label}</dt>
            <dd className="mt-1 break-words font-mono text-ink">{typeof value === "boolean" ? (value ? "Đạt" : "Không đạt") : cleanText(String(value))}</dd>
          </div>)}
        </dl>
      </div> : <p className="rounded-lg border border-dashed border-line p-3 text-xs text-secondary">Bản ghi này không có chi tiết từng cổng kiểm soát.</p>}
      {entry && <p className="text-[11px] text-muted">Bản ghi Agent 07 · {time(timestamp(entry))}</p>}
      {!entry && <p className="text-[11px] text-muted">Chưa có bản ghi Agent 07 cho mã và ngày đang chọn.</p>}
    </article>
  )
}

function TradeExecutionSection({ activePlan, riskEntry, executionEntry, ticker }: { activePlan?: RecordData; riskEntry?: RecordData; executionEntry?: RecordData; ticker: string }) {
  const risk = logOutput("portfolio_risk", riskEntry || {})
  const decisionData = object(risk.decision)
  const approvedShares = numeric(decisionData.approved_shares)
  const planShares = numeric(activePlan?.target_shares)
  const execution = logOutput("trade_execution", executionEntry || {})
  const executionMetrics = object(execution.execution_metrics)
  const executionPlan = object(execution.execution_plan)
  const rawStatus = String(execution.status || execution.execution_decision || "")
  const statusLabels: Record<string, string> = { SKIPPED_ZERO_SHARES: "Bỏ qua · số lượng được duyệt bằng 0", EXECUTE: "Đã tạo kết quả thực thi", EXECUTED: "Đã thực thi", FILLED: "Đã khớp", PARTIALLY_FILLED: "Khớp một phần", REJECTED: "Bị từ chối" }
  const executionState = approvedShares === 0 ? "Bị chặn · Agent 07 duyệt 0 cổ phiếu" : approvedShares === null ? "Chưa có kết quả duyệt từ Agent 07" : executionEntry ? (statusLabels[rawStatus] || rawStatus || "Có log thực thi, thiếu trạng thái") : `Đã duyệt ${number(approvedShares)} cổ phiếu · chưa có log xác nhận thực thi`
  const executionTone = approvedShares === 0 || ["REJECTED", "SKIPPED_ZERO_SHARES"].includes(rawStatus) ? "bg-loss/10 text-loss border-loss/20" : approvedShares === null || !executionEntry ? "bg-soft text-secondary border-line" : "bg-gain/10 text-gain border-gain/20"

  return (
    <article className="rounded-xl border border-line bg-surface p-5 space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line pb-3">
        <div className="flex items-center gap-2">
          <span className="text-[11px] font-bold uppercase tracking-wider px-2 py-0.5 rounded bg-emerald-600/10 text-emerald-600 border border-emerald-600/20">
            AGENT 08 · TRADE EXECUTION
          </span>
        <span className="text-xs text-muted font-medium">Kết quả thực thi trong nhật ký Agent 08 · {ticker}</span>
        </div>
        <span className={`text-xs px-2.5 py-1 rounded font-semibold border ${executionTone}`}>{executionState}</span>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 rounded-lg bg-soft/50 border border-line p-3 text-xs">
        <div>
          <span className="block text-[10px] text-muted">Khối lượng kế hoạch Agent 06</span>
          <span className="font-semibold text-ink">{planShares === null ? "—" : `${number(planShares)} CP`}</span>
        </div>
        <div>
          <span className="block text-[10px] text-muted">Khối lượng duyệt Agent 07</span>
          <span className="font-mono font-semibold text-ink">{approvedShares === null ? "—" : `${number(approvedShares)} CP`}</span>
        </div>
        <div>
          <span className="block text-[10px] text-muted">Phương thức ghi trong log</span>
          <span className="font-semibold text-ink">{String(execution.execution_mode ?? executionPlan.strategy ?? "—")}</span>
        </div>
        <div>
          <span className="block text-[10px] text-muted">Khối lượng đã khớp</span>
          <span className="font-mono font-semibold text-ink">{numeric(executionMetrics.executed_quantity ?? execution.shares) === null ? "—" : `${number(executionMetrics.executed_quantity ?? execution.shares)} CP`}</span>
        </div>
      </div>
      <p className="text-xs text-secondary leading-relaxed">{executionMetrics.slippage != null || execution.slippage_bps != null ? `Trượt giá ghi nhận: ${execution.slippage_bps != null ? `${number(execution.slippage_bps)} bps` : `${number(Number(executionMetrics.slippage) * 10000)} bps`}.` : "Không có số liệu trượt giá trong log."} {executionEntry ? `Log Agent 08 · ${time(timestamp(executionEntry))}.` : "Chưa có bản ghi Agent 08 phù hợp."}</p>
      {(riskEntry || executionEntry) && <p className="text-[11px] text-muted">Ghép log theo mã và ngày. Hiện chưa có mã lượt chạy chung để xác nhận tuyệt đối chúng thuộc cùng một pipeline.</p>}
      {!activePlan && <p className="text-[11px] text-muted">Chưa có quyết định phân bổ Agent 06 cho mã này trong phạm vi đang chọn.</p>}
    </article>
  )
}

function PositionMonitoringSection({ position, entry, ticker }: { position?: RecordData; entry?: RecordData; ticker: string }) {
  const pnl = numeric(position?.current_pnl_pct ?? entry?.pnl_pct)
  const stopLossDistance = numeric(position?.distance_to_stop_loss_pct)
  const healthStatus = String(position?.thesis_health_status || (entry?.thesis_invalidated === true ? "INVALIDATED" : entry?.stop_loss_triggered === true ? "TRIGGERED" : ""))
  const stopLossTriggered = typeof entry?.stop_loss_triggered === "boolean" ? entry.stop_loss_triggered : null
  const thesisInvalidated = typeof entry?.thesis_invalidated === "boolean" ? entry.thesis_invalidated : null
  const healthTone = !healthStatus ? "bg-soft text-secondary border-line" : healthStatus === "HEALTHY" ? "bg-gain/10 text-gain border-gain/20" : healthStatus === "WARNING" ? "bg-warning/10 text-warning border-warning/20" : "bg-loss/10 text-loss border-loss/20"

  return (
    <article className="rounded-xl border border-line bg-surface p-5 space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line pb-3">
        <div className="flex items-center gap-2">
          <span className="text-[11px] font-bold uppercase tracking-wider px-2 py-0.5 rounded bg-amber-500/10 text-amber-600 border border-amber-500/20">
            AGENT 09 · POSITION MONITORING
          </span>
          <span className="text-xs text-muted font-medium">Trạng thái trong snapshot và log Agent 09 · {ticker}</span>
        </div>
        <div className="flex items-center gap-2">
          <span className={`text-xs px-2 py-0.5 rounded font-semibold border ${healthTone}`}>
            {!healthStatus ? "Chưa có trạng thái" : healthStatus === "HEALTHY" ? "Vị thế khỏe mạnh" : healthStatus === "WARNING" ? "Cảnh báo vi phạm" : healthStatus === "INVALIDATED" || healthStatus === "TRIGGERED" ? "Luận điểm mất hiệu lực" : healthStatus}
          </span>
        </div>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 rounded-lg bg-soft/50 border border-line p-3">
        <div>
          <span className="block text-[11px] text-muted">Lãi/Lỗ hiện tại (PnL)</span>
          <span className={`text-lg font-bold font-mono ${pnl === null ? "text-secondary" : pnl < 0 ? "text-loss" : "text-gain"}`}>
            {number(pnl, "%")}
          </span>
        </div>
        <div>
          <span className="block text-[11px] text-muted">Khoảng cách đến Stop-Loss</span>
          <span className="text-lg font-bold font-mono text-ink">
            {stopLossDistance !== null ? `${stopLossDistance.toFixed(2)}%` : "—"}
          </span>
        </div>
        <div>
          <span className="block text-[11px] text-muted">Kích hoạt cắt lỗ cứng</span>
          <span className={`text-xs font-semibold px-2 py-0.5 rounded inline-block mt-1 ${stopLossTriggered === true ? "bg-loss/20 text-loss font-bold" : stopLossTriggered === false ? "bg-soft text-secondary" : "bg-soft text-secondary"}`}>
            {stopLossTriggered === true ? "ĐÃ KÍCH HOẠT" : stopLossTriggered === false ? "Chưa kích hoạt" : "Chưa có dữ liệu"}
          </span>
        </div>
        <div>
          <span className="block text-[11px] text-muted">Trạng thái luận điểm</span>
          <span className={`text-xs font-semibold px-2 py-0.5 rounded inline-block mt-1 ${thesisInvalidated === true ? "bg-loss/20 text-loss font-bold" : "bg-soft text-secondary"}`}>
            {thesisInvalidated === true ? "VÔ HIỆU HÓA" : thesisInvalidated === false ? "Chưa bị vô hiệu" : "Chưa có dữ liệu"}
          </span>
        </div>
      </div>

      {!position && !entry && <p className="rounded-lg border border-dashed border-line p-3 text-xs text-secondary">Chưa có snapshot hoặc log giám sát cho mã này.</p>}

      <div className="text-[10px] text-muted border-t border-line/60 pt-2 flex justify-between">
        <span>{position ? "Snapshot gần nhất · không theo bộ lọc ngày" : entry ? "Log Agent 09 · theo ngày đã chọn" : "Chưa có dữ liệu giám sát"}</span>
        <span>{position ? time(position.last_updated) : entry ? time(timestamp(entry)) : ""}</span>
      </div>
    </article>
  )
}

function OfflineGovernanceSection({ rlEntry, govEntry }: { rlEntry?: RecordData; govEntry?: RecordData }) {
  const icScores = object(rlEntry?.ic_rolling_scores)
  const rlSignals = object(rlEntry?.reward_signals)
  const govAudit = object(govEntry?.audit_trail_verification)
  const systemStatus = String(govAudit.system_status || "")
  const latency = numeric(govAudit.broker_latency_ms)
  const recordsVerified = numeric(govAudit.chain_records_verified)
  const chainIntegrity = typeof govAudit.chain_integrity_valid === "boolean" ? govAudit.chain_integrity_valid : null
  const failsafeTriggered = typeof govAudit.failsafe_triggered === "boolean" ? govAudit.failsafe_triggered : govAudit.failsafe_status === "ACTIVE" ? true : govAudit.failsafe_status === "INACTIVE" ? false : null
  const enforcedLaws = Array.isArray(govAudit.hard_laws_enforced) ? govAudit.hard_laws_enforced : []

  return (
    <div className="grid gap-5 lg:grid-cols-2 items-stretch">
      {/* AGENT 10: Reinforcement Learning & Bayes Shrinkage */}
      <article className="rounded-xl border border-line bg-surface p-5 space-y-4 flex flex-col justify-between">
        <div>
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line pb-3">
            <div className="flex items-center gap-2">
              <span className="text-[11px] font-bold uppercase tracking-wider px-2 py-0.5 rounded bg-violet-500/10 text-violet-600 border border-violet-500/20">
                AGENT 10 · REINFORCEMENT LEARNING
              </span>
              <span className="text-xs text-muted font-medium">Tự học Bayes & Hiệu chuẩn trọng số IC</span>
            </div>
            <span className={`text-xs px-2 py-0.5 rounded font-medium border ${rlEntry ? "bg-soft text-secondary border-line" : "bg-soft text-muted border-line"}`}>
              {rlEntry ? "Có bản ghi Agent 10" : "Chưa có log trong phạm vi này"}
            </span>
          </div>

          <p className="mt-3 text-xs leading-relaxed text-secondary">
            Cơ chế học tăng cường ngoại tuyến (Offline RL) sử dụng co ngót Bayes thực nghiệm (Empirical Bayes Shrinkage) để tinh chỉnh trọng số các nhân tố F1–F6 dựa trên hệ số Information Coefficient (IC) thực tế.
          </p>

          <div className="mt-3 space-y-2">
              <span className="text-[11px] font-semibold text-muted block uppercase tracking-wider">Điểm IC ghi trong log Agent 10:</span>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 text-xs">
              {Object.keys(icScores).length > 0 ? (
                Object.entries(icScores).map(([factor, score]) => {
                  const s = numeric(score)
                  return (
                    <div key={factor} className="rounded-lg bg-soft/50 border border-line p-2">
                      <span className="text-[10px] text-muted block">{factor.replace("_", " ")}</span>
                      <span className="font-mono font-bold text-ink">{s !== null ? (s > 0 ? `+${s.toFixed(3)}` : s.toFixed(3)) : "—"}</span>
                    </div>
                  )
                })
              ) : (
                <p className="col-span-full rounded-lg border border-dashed border-line p-3 text-xs text-secondary">{rlEntry ? "Bản ghi Agent 10 không có điểm IC." : "Không có log Agent 10 trong phạm vi này."}</p>
              )}
            </div>
          </div>

          <div className="mt-3 rounded-lg bg-soft/30 border border-line p-2.5 text-xs space-y-1">
            <span className="text-[10px] text-muted block">Giao thức học:</span>
            <span className="font-medium text-ink block">{rlSignals.learning_protocol ? cleanText(String(rlSignals.learning_protocol)) : "Chưa có thông tin giao thức trong log."}</span>
          </div>
        </div>

        <div className="text-[10px] text-muted border-t border-line/60 pt-2 flex justify-between">
          <span>Động cơ: MRALEngine · Bayes Weight Calibration</span>
          <span>{rlEntry ? time(timestamp(rlEntry)) : "Chưa có log trong phạm vi này"}</span>
        </div>
      </article>

      {/* AGENT 11: System Governance */}
      <article className="rounded-xl border border-line bg-surface p-5 space-y-4 flex flex-col justify-between">
        <div>
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line pb-3">
            <div className="flex items-center gap-2">
              <span className="text-[11px] font-bold uppercase tracking-wider px-2 py-0.5 rounded bg-rose-500/10 text-rose-600 border border-rose-500/20">
                AGENT 11 · SYSTEM GOVERNANCE
              </span>
              <span className="text-xs text-muted font-medium">Kết quả kiểm toán trong log Agent 11</span>
            </div>
            <span className={`text-xs px-2 py-0.5 rounded font-semibold border ${!systemStatus ? "bg-soft text-secondary border-line" : systemStatus === "COMPLIANT" ? "bg-gain/10 text-gain border-gain/20" : "bg-warning/10 text-warning border-warning/20"}`}>
              {systemStatus || "Chưa có trạng thái"}
            </span>
          </div>

          <p className="mt-3 text-xs leading-relaxed text-secondary">
            Agent 11 ghi nhận tính toàn vẹn chuỗi kiểm toán, độ trễ broker, trạng thái failsafe và các quy tắc có trong báo cáo.
          </p>

          <div className="mt-3 grid grid-cols-2 sm:grid-cols-3 gap-2.5 text-xs">
            <div className="rounded-lg bg-soft/50 border border-line p-2.5">
              <span className="text-[10px] text-muted block">Toàn vẹn Merkle Chain</span>
              <span className={`font-mono text-sm font-bold ${chainIntegrity === null ? "text-secondary" : chainIntegrity ? "text-gain" : "text-loss"}`}>{chainIntegrity === null ? "Chưa có dữ liệu" : chainIntegrity ? "Hợp lệ" : "Có lỗi"}</span>
              <span className="text-[10px] text-secondary block mt-0.5">{recordsVerified !== null ? `${recordsVerified.toLocaleString("vi-VN")} bản ghi` : "Chưa có số lượng"}</span>
            </div>
            <div className="rounded-lg bg-soft/50 border border-line p-2.5">
              <span className="text-[10px] text-muted block">Độ trễ Broker API</span>
              <span className="font-mono text-sm font-bold text-ink">{latency !== null ? `${latency.toLocaleString("vi-VN")} ms` : "—"}</span>
              <span className="text-[10px] text-secondary block mt-0.5">{latency !== null ? "Độ trễ ghi nhận trong báo cáo" : "Chưa có độ trễ trong báo cáo"}</span>
            </div>
            <div className="rounded-lg bg-soft/50 border border-line p-2.5">
              <span className="text-[10px] text-muted block">Cơ chế Failsafe</span>
              <span className={`font-mono text-sm font-bold ${failsafeTriggered === null ? "text-secondary" : failsafeTriggered ? "text-loss" : "text-gain"}`}>{failsafeTriggered === null ? "Chưa có dữ liệu" : failsafeTriggered ? "Đã kích hoạt" : "Chưa kích hoạt"}</span>
              <span className="text-[10px] text-secondary block mt-0.5">{govAudit.failsafe_reason ? cleanText(String(govAudit.failsafe_reason)) : "Trạng thái theo log Agent 11"}</span>
            </div>
          </div>

          <div className="mt-3 rounded-lg border border-line bg-soft/30 p-2.5 text-xs space-y-1.5">
            <span className="text-[10px] font-semibold text-muted block uppercase tracking-wider">Quy tắc được liệt kê trong báo cáo:</span>
            {enforcedLaws.length ? <ul className="grid grid-cols-1 sm:grid-cols-2 gap-1 text-[11px] text-secondary">{enforcedLaws.map((law, index) => <li key={`${String(law)}-${index}`}>· {cleanText(String(law)).replaceAll("_", " ")}</li>)}</ul> : <p className="text-[11px] text-secondary">Bản ghi không liệt kê quy tắc.</p>}
          </div>
        </div>

        <div className="text-[10px] text-muted border-t border-line/60 pt-2 flex justify-between">
          <span>Sổ cái: SHA-256 Merkle Chain Verifier</span>
          <span>{govEntry ? time(timestamp(govEntry)) : "Chưa có log trong phạm vi này"}</span>
        </div>
      </article>
    </div>
  )
}

function LogDetailViewer({ agent, entry }: { agent: string; entry: RecordData }) {
  const [showAllExclusions, setShowAllExclusions] = useState(false)
  const isCio = agent === "strategy_cio"
  const isAlloc = agent === "portfolio_allocation"
  const isUniverse = agent === "universe_discovery"

  // Filter out internal and redundant fields
  const ignoredKeys = new Set(["id", "created_at", "generated_at", "date", "analysis_date", "ticker", "is_replay"])

  // Filter entries to remove duplications
  const filtered = Object.entries(entry).filter(([key, val]) => {
    if (ignoredKeys.has(key)) return false
    // In universe_discovery, if filtered_counts contains exclusion_log, don't repeat top-level exclusion_log
    if (isUniverse && key === "exclusion_log" && object(entry.filtered_counts).exclusion_log) return false
    // In strategy_cio, debate_synthesis already has the rationale; don't repeat executive_rationale
    if (isCio && key === "executive_rationale") return false
    return val !== null && val !== undefined
  })

  return (
    <div className="mt-4 space-y-4 border-t border-line pt-4 text-sm leading-relaxed">
      {/* Specific rich renderer for Kelly allocation steps */}
      {isAlloc && entry.kelly_math_steps && typeof entry.kelly_math_steps === "object" && (
        <section className="min-w-0">
          <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-teal">Các bước tính toán phân bổ vốn (Kelly Formula):</h4>
          <KellyMathTrace trace={object(entry.kelly_math_steps)} weight={entry.allocated_weight_pct} />
        </section>
      )}

      <div className="grid min-w-0 gap-5 md:grid-cols-2">
        {filtered.map(([key, value]) => {
          // If this is kelly_math_steps and already rendered custom above, skip
          if (isAlloc && key === "kelly_math_steps") return null

          // In universe discovery, limit huge exclusion lists to prevent 2000px stretch
          if (key === "exclusion_log" && Array.isArray(value) && value.length > 5) {
            const list = value as RecordData[]
            const visible = showAllExclusions ? list : list.slice(0, 5)
            return (
              <section key={key} className="md:col-span-2 min-w-0">
                <div className="flex items-center justify-between mb-2">
                  <h4 className="text-xs font-semibold uppercase tracking-wider text-teal">
                    {fieldNames[key] || key.replaceAll("_", " ")} ({list.length} mã)
                  </h4>
                  <button
                    onClick={() => setShowAllExclusions(!showAllExclusions)}
                    className="text-xs text-mineral font-medium hover:underline"
                  >
                    {showAllExclusions ? "Thu gọn 5 mã ↑" : `Xem toàn bộ ${list.length} mã ↓`}
                  </button>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
                  {visible.map((item, idx) => (
                    <div key={idx} className="p-2.5 rounded border border-line bg-soft/40 text-xs">
                      <span className="font-mono font-bold text-ink">{String(item.ticker || "N/A")}</span>
                      <span className="ml-2 text-warning font-medium">{String(item.reason || "")}</span>
                      <p className="mt-1 text-secondary text-[11px] line-clamp-2">{String(item.detail || "")}</p>
                    </div>
                  ))}
                </div>
              </section>
            )
          }

          const fullWidth = (value !== null && typeof value === "object") || (typeof value === "string" && value.length > 100)
          return (
            <section key={key} className={`min-w-0 ${fullWidth ? "md:col-span-2" : ""}`}>
              <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-teal">{fieldNames[key] || key.replaceAll("_", " ")}</h4>
              <Value value={value} />
            </section>
          )
        })}
      </div>
    </div>
  )
}

function AgentSkeleton() {
  return (
    <div role="status" aria-label="Đang tải quyết định và nhật ký" className="mx-auto max-w-[1600px] space-y-6 p-4 lg:p-6">
      <span className="sr-only">Đang tải quyết định và nhật ký…</span>
      <div className="animate-pulse space-y-6" aria-hidden="true">
        <div className="space-y-4 border-b border-line pb-6">
          <div className="h-4 w-44 rounded bg-soft" />
          <div className="grid grid-cols-2 gap-5 lg:grid-cols-4">
            {[1, 2, 3, 4].map(i => <div key={i} className="space-y-3"><div className="h-3 w-24 rounded bg-soft" /><div className="h-7 w-3/4 rounded bg-soft" /></div>)}
          </div>
        </div>
        <div className="grid gap-5 lg:grid-cols-2">
          {[1, 2].map(i => <div key={i} className="space-y-4 rounded-xl border border-line bg-surface p-5"><div className="h-5 w-1/2 rounded bg-soft" /><div className="h-4 w-3/4 rounded bg-soft" /><div className="h-24 rounded bg-soft" /></div>)}
        </div>
        <div className="grid gap-5 md:grid-cols-[220px_minmax(0,1fr)]">
          <div className="h-64 rounded-xl border border-line bg-surface p-4"><div className="h-5 w-28 rounded bg-soft" /></div>
          <div className="space-y-4 rounded-xl border border-line bg-surface p-5"><div className="h-5 w-1/3 rounded bg-soft" /><div className="h-4 w-2/3 rounded bg-soft" /><div className="h-32 rounded bg-soft" /></div>
        </div>
        <div className="space-y-4 rounded-xl border border-line bg-surface p-5"><div className="h-6 w-40 rounded bg-soft" />{[1, 2, 3].map(i => <div key={i} className="h-16 rounded bg-soft" />)}</div>
      </div>
    </div>
  )
}

function AgentLogRow({ agent, entry, index }: { agent: string; entry: RecordData; index: number }) {
  const [opened, setOpened] = useState(false)
  const out = logOutput(agent, entry)
  const status = out.status || out.alert_level || out.verdict || out.final_resolution
  const ticker = logSymbol(agent, entry)
  return (
    <details onToggle={event => setOpened(event.currentTarget.open)} className="group px-5 py-4 open:bg-paper/50">
      <summary className="cursor-pointer list-none rounded focus-visible:outline-2 focus-visible:outline-mineral">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className="text-sm font-semibold">{agents[agent] || agent}</span>
          <span className="rounded bg-soft px-2 py-0.5 font-mono text-xs">{ticker || "Hệ thống / không gắn mã"}</span>
          <time className="text-xs tabular-nums text-secondary font-mono">{time(timestamp(entry))}</time>
          {status != null && <span className="break-all text-xs font-semibold px-2 py-0.5 rounded bg-soft text-ink">{String(status)}</span>}
          <span className="ml-auto text-xs font-medium text-teal group-open:hidden">Mở log ↓</span>
          <span className="ml-auto hidden text-xs font-medium text-teal group-open:inline">Thu gọn ↑</span>
        </div>
        <p className="mt-2 max-w-[110ch] text-sm leading-relaxed text-secondary">{logSummary(agent, entry)}</p>
        {entry.analysis_date != null && <p className="mt-1 text-xs text-muted">Ngày phân tích: {displayDate(vietnamDate(entry.analysis_date))}{entry.is_replay ? " · Chạy lại dữ liệu lịch sử" : ""}</p>}
      </summary>
      {opened && <>
        <LogDetailViewer agent={agent} entry={entry} />
        <details className="mt-3">
          <summary className="cursor-pointer text-xs font-medium text-muted hover:underline">Dữ liệu gốc JSON · #{String(entry.id ?? index)}</summary>
          <pre className="mt-2 max-h-80 overflow-auto rounded-lg bg-soft p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap break-all">{JSON.stringify(entry, null, 2)}</pre>
        </details>
      </>}
    </details>
  )
}

export default function WarRoom() {
  const [date, setDate] = useState(() => vietnamDate())
  const [symbol, setSymbol] = useState("")
  const [search, setSearch] = useState("")
  const [agent, setAgent] = useState("all")
  const [scope, setScope] = useState("all")
  const [logLimit, setLogLimit] = useState(30)
  const resource = useResource(() => workspaceApi.agent(date || undefined), [date])
  const data = resource.data as AgentResponse | null
  const ready = !resource.loading && !resource.error
  const theses = ready ? newest(data?.theses || []).filter(row => !date || recordDate(row) === date) : []
  const resolutions = ready ? data?.resolutions || [] : []
  const plans = ready ? newest(data?.decisions || []) : []
  const logs = ready ? (data?.logs || []).flatMap(group => (group.entries || []).map(entry => ({ agent: group.agent, entry })))
    .sort((a, b) => (Date.parse(timestamp(b.entry)) || 0) - (Date.parse(timestamp(a.entry)) || 0)) : []
  const symbols = [...new Set([...theses, ...plans, ...(ready ? data?.positionHealth || [] : [])].map(row => String(row.ticker || "")).filter(Boolean))]
  const visibleSymbols = symbols.filter(item => item.toLowerCase().includes(search.toLowerCase()))
  const active = visibleSymbols.includes(symbol) ? symbol : visibleSymbols[0] || ""
  const thesis = theses.find(row => row.ticker === active)
  const resolution = thesis ? related(resolutions, thesis) : undefined
  const counter = thesis ? related(data?.counterTheses || [], thesis) : undefined
  const verdict = decision(resolution)
  const selectedPlans = newest(plans.filter(row => row.ticker === active && (!date || recordDate(row) === date)))
  const planForReview = selectedPlans[0]
  const position = ready ? data?.positionHealth?.find(row => row.ticker === active) : undefined
  const pnl = numeric(position?.current_pnl_pct)
  const account = ready ? data?.account : undefined
  const scopedLogs = logs.filter(log => scope === "all" || (scope === "symbol" ? !!active && logSymbol(log.agent, log.entry) === active : !logSymbol(log.agent, log.entry)))
  const shownLogs = scopedLogs.filter(log => agent === "all" || log.agent === agent)
  const target = numeric(thesis?.target_price)
  const entry = numeric(thesis?.entry_price_estimated)
  const upside = target !== null && entry !== null && entry > 0 ? (target / entry - 1) * 100 : null

  // Keep related panels on the selected analysis day; never silently substitute a different day's log.
  const entriesFor = (agentCode: string) => logs.filter(log => log.agent === agentCode && (!date || recordDate(log.entry) === date))
  const latestEntry = (agentCode: string) => entriesFor(agentCode)[0]?.entry
  const matchingEntries = (agentCode: string) => entriesFor(agentCode).filter(log => active && (logSymbol(log.agent, log.entry) === active || log.entry.ticker === active || object(log.entry.garch_cash_trace).ticker === active))
  const relatedDay = date || (planForReview ? recordDate(planForReview) : "")
  const relatedEntries = (agentCode: string) => {
    const candidates = matchingEntries(agentCode)
    const sameDay = relatedDay ? candidates.filter(log => recordDate(log.entry) === relatedDay) : candidates
    if (relatedDay && !sameDay.length) return []
    const referenceTime = planForReview ? Date.parse(timestamp(planForReview)) : Number.NaN
    return [...sameDay].sort((a, b) => {
      const aTime = Date.parse(timestamp(a.entry)) || 0
      const bTime = Date.parse(timestamp(b.entry)) || 0
      return Number.isFinite(referenceTime) ? Math.abs(aTime - referenceTime) - Math.abs(bTime - referenceTime) : bTime - aTime
    })
  }
  const msEntry = latestEntry("market_surveillance")
  const udEntry = latestEntry("universe_discovery")
  const eqEntry = matchingEntries("equity_research")[0]?.entry
  const riskEntry = relatedEntries("portfolio_risk")[0]?.entry
  const executionEntry = relatedEntries("trade_execution")[0]?.entry
  const posMonEntry = matchingEntries("position_monitoring")[0]?.entry
  const rlEntry = latestEntry("reinforcement_learning")
  const govEntry = latestEntry("system_governance")
  // Clean formatted debate summary
  const summaryText = cleanText(resolution?.debate_summary || "")
  const tierMatch = summaryText.match(/\[(TẦNG\s*\d+[^\]]*)\]/i)
  const tierTag = tierMatch ? tierMatch[1] : null
  const cleanSummaryBody = tierMatch ? summaryText.replace(tierMatch[0], "").trim() : summaryText

  return (
    <div className="min-h-full bg-paper text-ink">
      <header className="flex flex-wrap items-center justify-between gap-4 border-b border-line bg-surface px-5 py-5 lg:px-8">
        <div>
          <p className="mb-1 text-xs font-medium tracking-widest text-secondary">AI WAR ROOM {data?.mode ? ` / ${data.mode}` : ""}</p>
          <h1 className="text-2xl font-semibold tracking-tight">Quyết định & nhật ký đầu tư</h1>
          <p className="mt-1 text-sm text-secondary">Kết luận, bằng chứng và kết quả trong cùng một góc nhìn.</p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <FinancialCalendar label="Ngày phân tích" allowAll={false} value={date} dates={data?.dates || []} onChange={value => { setDate(value); setLogLimit(30) }} />
          <a href="#agent-logs" className={`${control} font-medium`}>Xem nhật ký ↓</a>
          <button className={control} onClick={() => void resource.reload()} disabled={resource.loading}>Làm mới</button>
        </div>
      </header>

      {resource.loading ? <AgentSkeleton /> : resource.error ? (
        <div className="mx-auto max-w-[1600px] p-4 lg:p-6"><div role="alert" className="rounded-lg border border-loss/30 bg-loss/5 p-4 text-sm text-loss">Không tải được báo cáo. <button className="underline" onClick={() => void resource.reload()}>Thử lại</button></div></div>
      ) : <div className="mx-auto max-w-[1600px] space-y-6 p-4 lg:p-6">
        <section aria-labelledby="performance-title" className="border-b border-line pb-5">
          <div className="mb-4 flex flex-wrap items-baseline justify-between gap-2">
            <h2 id="performance-title" className="text-base font-semibold">Kết quả tài khoản</h2>
            <p className="text-xs text-secondary">Ảnh chụp gần nhất · {time(account?.updated_at)} · không theo bộ lọc ngày</p>
          </div>
          <dl className="grid grid-cols-2 gap-5 lg:grid-cols-4">
            <Metric label="Tổng tài sản (NAV)" value={number(account?.total_nav, " ₫")} />
            <Metric label="Tiền mặt" value={number(account?.cash_balance, " ₫")} />
            <Metric label="Lãi/lỗ đã chốt" value="Chưa có dữ liệu" note="Chưa có báo cáo tổng lãi/lỗ của các lệnh đã đóng." />
            <Metric label="Lãi/lỗ vị thế" value="Xem theo mã" note="Theo trạng thái giám sát gần nhất; không cộng các tỷ lệ thành lãi/lỗ toàn quỹ." />
          </dl>
        </section>

        {/* Layer 1: Macro Surveillance & Universe Discovery (Agent 01 & Agent 02) */}
        <MacroUniverseSection msEntry={msEntry} udEntry={udEntry} />

        <div className="grid items-start gap-5 md:grid-cols-[220px_minmax(0,1fr)]">
          <aside className="min-w-0 rounded-xl border border-line bg-surface p-3">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="font-semibold">Cổ phiếu</h2>
              <span className="text-sm tabular-nums text-secondary">{symbols.length}</span>
            </div>
            <input aria-label="Tìm mã cổ phiếu" placeholder="Tìm mã cổ phiếu…" value={search} onChange={e => setSearch(e.target.value)} className={`${control} mb-3 w-full`} />
            <nav aria-label="Chọn mã phân tích" className="flex max-h-[480px] gap-2 overflow-auto md:block md:space-y-1">
              {visibleSymbols.map(item => {
                const t = theses.find(row => row.ticker === item)
                const plan = plans.find(row => row.ticker === item)
                const d = plan ? allocationDecision(plan.action) : decision(t ? related(resolutions, t) : undefined)
                return (
                  <button
                    key={item}
                    onClick={() => setSymbol(item)}
                    aria-pressed={active === item}
                    className={`w-full shrink-0 rounded-lg border-l-2 px-3 py-3 text-left transition-colors focus-visible:outline-2 focus-visible:outline-mineral max-md:w-44 ${active === item ? "border-teal bg-soft" : "border-transparent hover:bg-paper"}`}
                  >
                    <span className="block font-mono text-base font-semibold">{item}</span>
                    <span className={`mt-1 inline-block rounded px-1.5 py-0.5 text-xs font-medium ${d.tone}`}>{d.label}</span>
                    <span className="mt-1 block text-[11px] text-secondary">{plan ? "Quyết định phân bổ" : "Phán quyết CIO"}</span>
                  </button>
                )
              })}
            </nav>
            {ready && !visibleSymbols.length && <p className="py-4 text-sm text-secondary">{search ? "Không tìm thấy mã phù hợp." : "Chưa có luận điểm hoặc quyết định trong ngày này."}</p>}
            <p className="mt-3 border-t border-line pt-3 text-xs leading-relaxed text-secondary">{date ? displayDate(date) : "Tất cả ngày · luận điểm mới nhất của mỗi mã"}</p>
          </aside>

          <section className="min-w-0 space-y-5" aria-label="Chi tiết quyết định">
            {active ? <>
              {/* Executive CIO Banner & Core Target Metrics (Agent 12) */}
              <article className="overflow-hidden rounded-xl border border-line bg-surface">
                <div className="border-b border-line p-5 lg:p-6 bg-gradient-to-r from-surface via-soft/30 to-surface">
                  <div className="flex flex-wrap items-baseline justify-between gap-3">
                    <Link to={`/stock/${active}`} className="font-mono text-xl font-bold text-ink underline-offset-4 hover:underline">
                      {active} ↗
                    </Link>
                    <span className="text-xs text-secondary font-mono">Luận điểm · {time(thesis && timestamp(thesis))}</span>
                  </div>

                  <div className="mt-4 flex flex-wrap items-center gap-2.5">
                    <span className="text-[11px] font-bold uppercase tracking-wider px-2 py-0.5 rounded bg-rose-500/10 text-rose-600 border border-rose-500/20">
                      AGENT 12 · STRATEGY CIO
                    </span>
                    <span className={`px-2.5 py-1 rounded-md text-sm font-semibold border ${verdict.tone}`}>
                      {verdict.label}
                    </span>
                    {tierTag && (
                      <span className="px-2 py-0.5 rounded text-xs font-medium bg-mineral/10 text-mineral border border-mineral/20">
                        {tierTag}
                      </span>
                    )}
                    {verdict.code && (
                      <span className="ml-auto font-mono text-[11px] text-secondary bg-soft px-2 py-0.5 rounded border border-line">
                        {verdict.code}
                      </span>
                    )}
                  </div>

                  <p className="mt-3 max-w-[95ch] text-sm leading-relaxed text-ink font-normal">
                    {cleanSummaryBody || "Chưa có phán quyết CIO liên kết với luận điểm này. Trạng thái luận điểm không xác nhận một lệnh mua/bán."}
                  </p>

                  <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-secondary border-t border-line/60 pt-2.5">
                    <span>Thẩm quyền: Hội đồng Trọng tài Tối cao Agent 12 (Strategy CIO) & Trọng tài Hiến pháp độc lập</span>
                    <span className="ml-auto text-muted">Trạng thái giải ngân thực thi phụ thuộc vào hạn ngạch kiểm toán</span>
                  </div>
                </div>

                <div className="p-5 lg:p-6">
                  <dl className="grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
                    <Metric label="Giá vào dự kiến" value={number(entry, " ₫")} />
                    <Metric label="Giá mục tiêu" value={number(target, " ₫")} />
                    <Metric label="Điều kiện vô hiệu" value={Array.isArray(thesis?.invalidation_conditions) ? thesis.invalidation_conditions.map(item => cleanText(String(item))).filter(Boolean).join("; ") || "Chưa có dữ liệu" : "Chưa có dữ liệu"} />
                    <Metric label="Kỳ vọng đến mục tiêu" value={number(upside, "%")} note="So với giá vào dự kiến, chưa trừ phí; không phải lãi đã đạt." />
                  </dl>
                  <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-line pt-4">
                    <div>
                      <h3 className="text-sm font-medium">Lãi/lỗ vị thế {active} · giám sát gần nhất</h3>
                      <p className="mt-1 text-xs text-secondary">{position ? `${time(position.last_updated)} · không theo bộ lọc ngày` : "Chưa có trạng thái giám sát cho mã này."}</p>
                    </div>
                    <strong className={`text-2xl tabular-nums ${pnl === null ? "text-secondary" : pnl < 0 ? "text-loss" : "text-gain"}`}>
                      {number(pnl, "%")}
                    </strong>
                  </div>
                </div>
              </article>

              {/* Layer 2: Multi-Factor Equity Research (Agent 03) */}
              <EquityResearchSection entry={eqEntry} ticker={active} />

              {/* Research & Counter-Thesis Grid (Agent 04 & Agent 05) */}
              <div className="grid gap-5 lg:grid-cols-2 items-start">
                <InvestmentThesisSection thesis={thesis} />
                <CounterThesisSection counter={counter} />
              </div>

              {/* CIO Resolution Dossier & Execution Conditions (Agent 12) */}
              {resolution && <CioResolutionDossier resolution={resolution} />}

              {/* Layer 3: Portfolio Allocation (Agent 06) */}
              <section className="rounded-xl border border-line bg-surface p-5 space-y-4">
                <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line pb-3">
                  <div className="flex items-center gap-2">
                    <span className="text-[11px] font-bold uppercase tracking-wider px-2 py-0.5 rounded bg-gain/10 text-gain border border-gain/20">
                      AGENT 06 · PORTFOLIO ALLOCATION
                    </span>
                    <span className="text-xs text-muted font-medium">Định cỡ Quarter-Kelly & Kế hoạch giải ngân · {active}</span>
                  </div>
                  <span className="text-xs text-secondary">Lịch sử quyết định trong phạm vi ngày đã chọn</span>
                </div>
                {selectedPlans.length ? (
                  <div className="divide-y divide-line">
                    {selectedPlans.map((plan, i) => {
                      const dec = allocationDecision(plan.action)
                      return (
                        <div key={String(plan.decision_id || i)} className="py-3.5 first:pt-0">
                          <div className="flex flex-wrap items-center gap-3">
                            <span className={`px-2.5 py-0.5 rounded text-sm font-bold border ${dec.tone}`}>
                              {dec.label}
                            </span>
                            <span className="text-xs text-secondary font-mono">{time(timestamp(plan))}</span>
                            <span className="ml-auto text-sm tabular-nums font-semibold">
                              {number(plan.target_shares)} cổ phiếu · {number(plan.allocated_weight_pct, "% NAV")}
                            </span>
                          </div>
                          <p className="mt-2 text-sm leading-relaxed text-secondary">{cleanText(String(plan.rationale || "Chưa ghi nhận lý do."))}</p>
                        </div>
                      )
                    })}
                  </div>
                ) : (
                  <p className="mt-2 text-sm text-secondary">Chưa có quyết định phân bổ cho mã này.</p>
                )}
              </section>

              {/* Layer 3: Portfolio Risk Gatekeeper & Supreme Veto (Agent 07) */}
              <PortfolioRiskSection entry={riskEntry} ticker={active} />

              {/* Layer 4: Trade Execution & Adaptive Slicing (Agent 08) */}
              <TradeExecutionSection activePlan={planForReview} riskEntry={riskEntry} executionEntry={executionEntry} ticker={active} />

              {/* Layer 4: Position Monitoring & T0-T5 Defense Ladder (Agent 09) */}
              <PositionMonitoringSection position={position} entry={posMonEntry} ticker={active} />
            </> : (
              <div className="rounded-xl border border-dashed border-line p-8 text-sm text-secondary">
                Chọn một mã để xem quyết định. Nhật ký hệ thống vẫn có thể được xem bên dưới.
              </div>
            )}
          </section>
        </div>

        {/* Layer 5: Offline Governance & Continuous Learning (Agent 10 & Agent 11) */}
        <OfflineGovernanceSection rlEntry={rlEntry} govEntry={govEntry} />

        {/* Agent Logs section */}
        <section id="agent-logs" aria-labelledby="logs-title" className="rounded-xl border border-line bg-surface">
          <div className="space-y-4 border-b border-line p-5">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h2 id="logs-title" className="text-xl font-semibold tracking-tight">Nhật ký agent</h2>
              <span className="text-sm text-secondary">{shownLogs.length} bản ghi khớp bộ lọc · đang hiển thị {Math.min(logLimit, shownLogs.length)} · {displayDate(date)}</span>
            </div>
            <p className="text-sm text-secondary">Đọc kết quả, lý do và dữ liệu gốc theo thời gian. Nội dung được tối ưu hóa để loại bỏ trường lặp.</p>
            <div className="flex flex-wrap gap-3">
              <label className="text-sm text-secondary">Agent
                <select value={agent} onChange={e => { setAgent(e.target.value); setLogLimit(30) }} className={`${control} ml-2`}>
                  <option value="all">Tất cả agent</option>
                  {Object.entries(agents).map(([key, label]) => (
                    <option key={key} value={key}>{label} ({scopedLogs.filter(log => log.agent === key).length})</option>
                  ))}
                </select>
              </label>
              <label className="text-sm text-secondary">Phạm vi
                <select value={scope} onChange={e => { setScope(e.target.value); setLogLimit(30) }} className={`${control} ml-2`}>
                  <option value="all">Tất cả mã & hệ thống</option>
                  <option value="symbol" disabled={!active}>Chỉ mã {active}</option>
                  <option value="system">Không gắn mã / toàn hệ thống</option>
                </select>
              </label>
            </div>
          </div>

          {!shownLogs.length && (
            <p className="p-8 text-sm text-secondary">
              {resource.loading ? "Đang tải nhật ký…" : resource.error ? "Chưa tải được nhật ký." : "Không có bản ghi phù hợp với bộ lọc này. Không có log không đồng nghĩa agent đã hoàn tất."}
            </p>
          )}

          <div className="divide-y divide-line">
            {shownLogs.slice(0, logLimit).map((log, index) => <AgentLogRow key={`${log.agent}-${log.entry.id ?? index}`} agent={log.agent} entry={log.entry} index={index} />)}
          </div>

          {shownLogs.length > logLimit && (
            <div className="border-t border-line p-4 text-center">
              <button className={control} onClick={() => setLogLimit(limit => limit + 30)}>
                Xem thêm 30 bản ghi ({shownLogs.length - logLimit} còn lại)
              </button>
            </div>
          )}
          <p className="border-t border-line px-5 py-3 text-xs text-secondary">
            Đã tải tối đa 200 bản ghi gần nhất mỗi agent. Chọn một ngày để thu hẹp lịch sử.
          </p>
        </section>
      </div>}
    </div>
  )
}
