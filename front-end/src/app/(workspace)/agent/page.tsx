"use client"

import { useState } from "react"
import { FinancialCalendar } from "@/components/FinancialCalendar"
import { workspaceApi } from "@/lib/api"
import { usePortfolio, type PortfolioPosition, type PortfolioSnapshot } from "@/lib/use-portfolio"
import { useResource } from "@/lib/api/use-resource"
import { displayDate, vietnamDate } from "@/lib/financial-date"
import { Link } from "@/lib/router"
import {
  approvedOrder,
  confirmationNarratives,
  executionRecord,
  horizonGroup,
  investmentHorizons,
  investmentText,
  monitoredRisks,
  thesisDetails,
  thesisReview,
  cleanText,
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
  portfolio_allocation: "Agent 06 · Phân bổ giao dịch",
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

function InvestmentThesisSection({ thesis, counter, resolution }: { thesis?: RecordData; counter?: RecordData; resolution?: RecordData }) {
  if (!thesis) return <article className="rounded-xl border border-line bg-surface p-5 lg:p-6"><h2 className="text-lg font-semibold">Luận điểm đầu tư</h2><p className="mt-3 text-sm leading-7 text-secondary">Chưa có luận điểm cho mã và ngày đang xem. Giá mua, mục tiêu và câu chuyện đầu tư sẽ hiển thị khi có bản phân tích được ghi nhận.</p></article>
  const report = thesisDetails(thesis)
  const review = thesisReview(thesis, counter, resolution)
  const blocked = /BLOCK|REJECT/i.test(review.code) || numeric(object(resolution?.verdict_payload).weight_cap) === 0
  const constraints = counter?.thesis_id === thesis?.thesis_id ? object(counter?.execution_constraints) : {}
  const stopOverride = numeric(constraints.stop_loss_pct_override)
  const storedStop = numeric(object(report.body.exit_conditions).hard_stop_loss_price)
  const stop = report.entry && stopOverride !== null && stopOverride > 0 && stopOverride < 1 ? report.entry * (1 - stopOverride) : storedStop !== null && storedStop > 0 ? storedStop : null
  const ceiling = numeric(constraints.entry_ceiling_price)
  const upside = report.entry && report.target ? (report.target / report.entry - 1) * 100 : null
  const category = horizonGroup(report.months)
  const conclusion = investmentText(resolution?.debate_summary)
  const conditions = confirmationNarratives(thesis?.confirming_signals)
  const risks = monitoredRisks(report.risks)

  return (
    <article className="rounded-xl border border-line bg-surface">
      <header className="border-b border-line p-5 lg:p-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-lg font-semibold tracking-tight">Luận điểm đầu tư sau thẩm định</h2>
          <span className={"rounded-md px-2.5 py-1 text-xs font-medium " + review.tone}>{review.label}</span>
        </div>
        <p className="mt-2 text-xs leading-relaxed text-secondary">Bản phân tích {time(thesis && timestamp(thesis))}. Kết luận thẩm định được đối chiếu theo đúng phiên bản luận điểm.</p>
        {conclusion && <p className="mt-4 max-w-[80ch] whitespace-pre-wrap text-sm leading-7 text-ink">{conclusion}</p>}
        {!review.complete && <p role="status" className="mt-3 text-sm text-warning">Luận điểm đang chờ kết luận đầy đủ; giá và mục tiêu dưới đây là dữ liệu phân tích đã ghi nhận.</p>}
        {blocked && <p role="status" className="mt-3 text-sm text-loss">Luận điểm này chưa được chấp thuận đầu tư. Giá và mục tiêu bên dưới là dữ liệu phân tích đã ghi nhận.</p>}
      </header>

      <div className="space-y-7 p-5 lg:p-6">
        <dl className="grid gap-x-6 gap-y-5 sm:grid-cols-2 lg:grid-cols-4">
          <Metric label="Giá mua tham chiếu" value={number(report.entry, " ₫")} note="Giá tại thời điểm lập luận điểm." />
          <Metric label="Mục tiêu đầu tư" value={number(report.target, " ₫")} />
          <Metric label="Tiềm năng đến mục tiêu" value={number(upside, "%")} note="Ước tính từ giá tham chiếu, chưa trừ phí." />
          <Metric label="Thời hạn đã phân tích" value={report.months === null ? "Chưa xác định" : number(report.months, " tháng")} />
        </dl>

        <section aria-label="Câu chuyện đầu tư" className="space-y-5 border-t border-line pt-6">
          {[
            ["Vì sao chọn doanh nghiệp này?", report.whyStock],
            ["Vì sao xem xét đầu tư lúc này?", report.whyNow],
            ["Điều gì có thể đưa giá đến mục tiêu?", report.catalyst],
          ].map(([label, text]) => (
            <div key={label}>
              <h3 className="mb-2 text-sm font-semibold text-ink">{label}</h3>
              <p className="max-w-[80ch] whitespace-pre-wrap text-sm leading-7 text-secondary">{text || "Bản báo cáo chưa cung cấp diễn giải cho nội dung này."}</p>
            </div>
          ))}
        </section>

        <section aria-labelledby="investment-conditions-title" className="border-t border-line pt-6">
          <h3 id="investment-conditions-title" className="text-base font-semibold">Các điều kiện đã đáp ứng</h3>
          <dl className="mt-4 space-y-4">
            {conditions.map(condition => (
              <div key={condition.key} className="grid gap-1.5 sm:grid-cols-[180px_minmax(0,1fr)] sm:gap-5">
                <dt className="text-sm font-medium text-ink">{condition.label}</dt>
                <dd className="max-w-[75ch] text-sm leading-7 text-secondary">{condition.text}</dd>
              </div>
            ))}
          </dl>
        </section>

        <section aria-labelledby="horizon-targets-title" className="border-t border-line pt-6">
          <h3 id="horizon-targets-title" className="text-base font-semibold">Mục tiêu theo thời hạn đầu tư</h3>
          <p className="mt-1.5 text-xs leading-relaxed text-secondary">Mỗi mục tiêu giữ nguyên thời hạn của báo cáo. Các nhóm chưa có phân tích riêng sẽ được ghi rõ.</p>
          <div className="mt-4 overflow-x-auto">
            <table className="w-full min-w-[480px] text-left text-sm">
              <thead className="border-b border-line text-xs text-secondary">
                <tr><th scope="col" className="pb-3 pr-4 font-medium">Mục tiêu</th><th scope="col" className="pb-3 pr-4 font-medium">Thời hạn ghi nhận</th><th scope="col" className="pb-3 pr-4 font-medium">Giá mục tiêu</th><th scope="col" className="pb-3 font-medium">Tiềm năng</th></tr>
              </thead>
              <tbody className="divide-y divide-line">
                {investmentHorizons.map(horizon => (
                  <tr key={horizon.key}>
                    <th scope="row" className="py-4 pr-4 font-medium">{horizon.label}</th>
                    {category === horizon.key ? <>
                      <td className="py-4 pr-4 text-secondary">{number(report.months, " tháng")}</td>
                      <td className="py-4 pr-4 font-semibold tabular-nums">{number(report.target, " ₫")}</td>
                      <td className="py-4 font-semibold tabular-nums">{number(upside, "%")}</td>
                    </> : <td colSpan={3} className="py-4 text-secondary">Chưa có mục tiêu riêng trong báo cáo này</td>}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section aria-labelledby="observed-risks-title" className="border-t border-line pt-6">
          <h3 id="observed-risks-title" className="text-base font-semibold">Rủi ro cần quan sát</h3>
          <p className="mt-1.5 text-xs leading-relaxed text-secondary">Theo dõi các diễn biến dưới đây để xem xét lại quyết định khi có thông tin mới.</p>
          <dl className="mt-4 space-y-4">
            {risks.map(group => (
              <div key={group.key} className="grid gap-2 sm:grid-cols-[180px_minmax(0,1fr)] sm:gap-5">
                <dt className="text-sm font-medium">{group.label}</dt>
                <dd className="max-w-[75ch] text-sm leading-7 text-secondary">
                  {group.items.length ? <ul className="space-y-2">{group.items.map(text => <li key={text}>{text}</li>)}</ul> : "Báo cáo chưa nêu điều kiện theo dõi cho nhóm này."}
                </dd>
              </div>
            ))}
          </dl>
          {(stop !== null || (ceiling !== null && ceiling > 0)) && (
            <dl className="mt-5 grid gap-4 rounded-lg bg-soft p-4 sm:grid-cols-2">
              {stop !== null && <Metric label="Mốc giá cần xem xét bảo vệ vốn" value={number(stop, " ₫")} note="Mốc trong luận điểm; đối chiếu với quyết định quản trị vị thế khi giá chạm ngưỡng." />}
              {ceiling !== null && ceiling > 0 && <Metric label="Giá mua tối đa theo điều kiện thẩm định" value={number(ceiling, " ₫")} />}
            </dl>
          )}
        </section>
      </div>
    </article>
  )
}

function FinalOrderSection({ ticker, plan, riskLogs, executionEntries, position, portfolioAvailable }: { ticker: string; plan?: RecordData; riskLogs: RecordData[]; executionEntries: RecordData[]; position?: PortfolioPosition; portfolioAvailable: boolean }) {
  const result = approvedOrder(plan, riskLogs)
  const value = result.shares !== null && result.price !== null ? result.shares * result.price : null
  const executions = newest(executionEntries).map(row => ({ row, record: executionRecord(row) }))
    .filter(({ record }, index, rows) => !record.id || rows.findIndex(item => item.record.id === record.id) === index).slice(0, 10)
  return (
    <article className="rounded-xl border border-line bg-surface p-5 lg:p-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Link to={"/stock/" + ticker} className="font-mono text-xl font-semibold underline-offset-4 hover:underline">{ticker} ↗</Link>
          <h2 className="mt-1 text-sm font-medium text-secondary">Quyết định giao dịch và phân bổ cuối cùng</h2>
        </div>
        <span className={"rounded-lg px-3 py-2 text-lg font-semibold " + result.tone}>{result.label}</span>
      </header>
      <dl className="mt-6 grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
        <Metric label="Số cổ phiếu giao dịch được duyệt" value={result.shares === null ? "Chờ duyệt" : number(result.shares, " cp")} />
        <Metric label="Giá đặt lệnh tham chiếu" value={number(result.price, " ₫")} />
        <Metric label="Giá trị lệnh được duyệt" value={number(value, " ₫")} />
        <Metric label="Phân bổ cho lệnh này" value={number(result.weight, "% tài sản")} note="Phần vốn cho lệnh, chưa phải tỷ trọng nắm giữ sau giao dịch." />
      </dl>
      {result.rationale && <p className="mt-4 max-w-[85ch] text-sm leading-7 text-secondary">{result.rationale}</p>}
      <p role="status" className="mt-4 text-xs leading-relaxed text-secondary">{!plan ? "Chưa có kế hoạch giao dịch cho mã và ngày đang xem." : result.approved ? result.shares === 0 ? "Quyết định này không tạo thêm giao dịch." : "Số lượng trên là hạn mức được duyệt. Kết quả khớp lệnh được ghi nhận riêng trong sổ tài khoản." : "Kế hoạch đang chờ kết quả kiểm soát rủi ro gắn với đúng quyết định; chưa có số lượng cuối cùng."}</p>
      <footer className="mt-5 flex flex-wrap justify-between gap-2 border-t border-line pt-4 text-xs text-secondary">
        <span>Quyết định {time(plan && timestamp(plan))}{result.riskEntry ? " · Duyệt " + time(timestamp(result.riskEntry)) : ""}</span>
        <span>Hiện đang nắm giữ: {position ? number(position.quantity, " cp") : portfolioAvailable ? "Không có vị thế" : "Chưa có dữ liệu vị thế"}{position ? " · Lãi/lỗ " + number(position.pnlPercent, "%") : ""}</span>
      </footer>
      {!!executions.length && <details className="mt-4 border-t border-line pt-4">
        <summary className="cursor-pointer text-sm font-medium focus-visible:outline-2 focus-visible:outline-mineral">Lượt thực thi đã ghi nhận của {ticker}</summary>
        <p className="mt-2 text-xs leading-relaxed text-secondary">Các lượt trong ngày đang xem có thời điểm và mã lệnh riêng. Số khớp bên dưới được đọc từ từng lượt thực thi.</p>
        <ul className="mt-3 divide-y divide-line">
          {executions.map(({ row, record }, index) => <li key={record.id || index} className="flex flex-wrap items-center justify-between gap-2 py-3 text-sm">
            <div><span className="font-medium">{record.side.code ? record.side.label : "Chưa có chiều giao dịch"}</span><span className="ml-2 text-secondary">{record.label}{record.simulated ? " · Mô phỏng" : record.replay ? " · Chạy lại lịch sử" : ""}</span><p className="mt-1 text-xs text-secondary">{time(timestamp(row))}{record.id ? " · Lệnh " + record.id : ""}</p></div>
            <span className="tabular-nums">{number(record.shares, " cp đã khớp")}{record.price !== null ? " · " + number(record.price, " ₫") : ""}</span>
          </li>)}
        </ul>
      </details>}
    </article>
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
  const researchGate = object(metrics.research_gate)
  const css = numeric(metrics.css) ?? numeric(researchGate.css)
  const conviction = String(metrics.conviction || researchGate.conviction || "")
  const percentile = numeric(metrics.percentile) ?? numeric(researchGate.percentile)
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
        <p className="text-sm text-secondary">{entry ? `Bản ghi Agent-03 của ${ticker} không có điểm CSS.` : `Chưa ghi nhận bản ghi phân tích nhân tố riêng cho ${ticker} trong phiên này.`}</p>
      )}
    </article>
  )
}

function PositionMonitoringSection({ position, entry, ticker }: { position?: RecordData; entry?: RecordData; ticker: string }) {
  const pnl = numeric(position?.current_pnl_pct)
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
          <span className="block text-[11px] text-muted">Lãi/lỗ theo giá gần nhất (sau phí mua)</span>
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
        <span>{position ? "Giá định giá gần nhất · không theo bộ lọc ngày" : entry ? "Log Agent 09 · theo ngày đã chọn" : "Chưa có dữ liệu giám sát"}</span>
        <span>{position ? time(position.price_as_of) : entry ? time(timestamp(entry)) : ""}</span>
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
  const accountResource = useResource<PortfolioSnapshot>(() => workspaceApi.agentPortfolio(), [])
  const portfolio = usePortfolio(accountResource.data, accountResource.reload)
  const ready = !!resource.data
  const theses = ready ? newest(data?.theses || []).filter(row => !date || recordDate(row) === date) : []
  const resolutions = ready ? data?.resolutions || [] : []
  const plans = ready ? newest(data?.decisions || []) : []
  const logs = ready ? (data?.logs || []).flatMap(group => (group.entries || []).map(entry => ({ agent: group.agent, entry })))
    .sort((a, b) => (Date.parse(timestamp(b.entry)) || 0) - (Date.parse(timestamp(a.entry)) || 0)) : []
  const symbols = [...new Set([...theses, ...plans, ...(portfolio?.positions.map(p => ({ ticker: p.symbol })) ?? [])].map(row => String(row.ticker || "")).filter(Boolean))]
  const visibleSymbols = symbols.filter(item => item.toLowerCase().includes(search.toLowerCase()))
  const active = visibleSymbols.includes(symbol) ? symbol : visibleSymbols[0] || ""
  const thesis = theses.find(row => row.ticker === active)
  const resolution = thesis ? related(resolutions, thesis) : undefined
  const counter = thesis ? related(data?.counterTheses || [], thesis) : undefined
  const selectedPlans = newest(plans.filter(row => row.ticker === active && (!date || recordDate(row) === date)))
  const planForReview = selectedPlans[0]
  const health = ready ? data?.positionHealth?.find(row => row.ticker === active) : undefined
  const valuedPosition = portfolio?.positions.find(row => row.symbol === active)
  const position: RecordData | undefined = valuedPosition ? { ...health, ticker: active, current_pnl_pct: valuedPosition.pnlPercent, price_as_of: valuedPosition.priceAsOf } : undefined
  const summary = portfolio?.summary
  const scopedLogs = logs.filter(log => scope === "all" || (scope === "symbol" ? !!active && logSymbol(log.agent, log.entry) === active : !logSymbol(log.agent, log.entry)))
  const shownLogs = scopedLogs.filter(log => agent === "all" || log.agent === agent)
  // Keep related panels on the selected analysis day; never silently substitute a different day's log.
  const entriesFor = (agentCode: string) => logs.filter(log => log.agent === agentCode && (!date || recordDate(log.entry) === date))
  const latestEntry = (agentCode: string) => entriesFor(agentCode)[0]?.entry
  const matchingEntries = (agentCode: string) => entriesFor(agentCode).filter(log => active && (logSymbol(log.agent, log.entry) === active || log.entry.ticker === active || object(log.entry.garch_cash_trace).ticker === active))
  const msEntry = latestEntry("market_surveillance")
  const udEntry = latestEntry("universe_discovery")
  const eqEntry = matchingEntries("equity_research")[0]?.entry
  const posMonEntry = matchingEntries("position_monitoring")[0]?.entry
  const rlEntry = latestEntry("reinforcement_learning")
  const govEntry = latestEntry("system_governance")

  return (
    <div className="min-h-full bg-paper text-ink">
      <header className="flex flex-wrap items-center justify-between gap-4 border-b border-line bg-surface px-5 py-5 lg:px-8">
        <div>
          <p className="mb-1 text-xs font-medium tracking-widest text-secondary">BÁO CÁO AGENTS {data?.mode ? ` / ${data.mode}` : ""}</p>
          <h1 className="text-2xl font-semibold tracking-tight">Quyết định đầu tư</h1>
          <p className="mt-1 text-sm text-secondary">Phân bổ cuối cùng, mục tiêu và câu chuyện đầu tư sau thẩm định.</p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <FinancialCalendar label="Ngày phân tích" allowAll={false} value={date} dates={data?.dates || []} onChange={value => { setDate(value); setLogLimit(30) }} />
          <a href="#agent-logs" onClick={() => document.getElementById("agent-logs")?.setAttribute("open", "")} className={`${control} font-medium`}>Xem nhật ký ↓</a>
          <button className={control} onClick={() => { void resource.reload(); void accountResource.reload() }} disabled={resource.loading || accountResource.loading}>Làm mới</button>
        </div>
      </header>

      {!resource.data && resource.loading ? <AgentSkeleton /> : !resource.data && resource.error ? (
        <div className="mx-auto max-w-[1600px] p-4 lg:p-6"><div role="alert" className="rounded-lg border border-loss/30 bg-loss/5 p-4 text-sm text-loss">Không tải được báo cáo. <button className="underline" onClick={() => void resource.reload()}>Thử lại</button></div></div>
      ) : <div className="mx-auto max-w-[1600px] space-y-6 p-4 lg:p-6">
        <details className="rounded-xl border border-line bg-surface p-5">
          <summary className="cursor-pointer text-sm font-medium focus-visible:outline-2 focus-visible:outline-mineral">Kết quả tài khoản hiện tại</summary>
          <p className="mb-4 mt-4 text-xs text-secondary">Quỹ Multi-Agent · {summary?.accountId} · Trạng thái hiện tại, không theo bộ lọc ngày</p>
          <dl className="grid grid-cols-2 gap-5 lg:grid-cols-3">
            <Metric label="Tổng tài sản (NAV)" value={number(summary?.nav, " ₫")} />
            <Metric label="Tiền mặt" value={number(summary?.cash, " ₫")} />
            <Metric label="Lãi/lỗ đã chốt" value={number(summary?.realizedPnl, " ₫")} note="Sau phí mua, phí bán và thuế." />
            <Metric label="Lãi/lỗ chưa chốt" value={number(summary?.unrealizedPnl, " ₫")} note="Giá vốn gồm phí mua; chưa trừ phí bán dự kiến." />
            <Metric label="Tổng lãi/lỗ" value={number(summary?.totalPnl, " ₫")} note="Đã chốt + chưa chốt." />
            <Metric label="Lợi nhuận tài khoản" value={number(summary?.totalReturnPct, "%")} note="Tính trên vốn ban đầu đã đối soát." />
          </dl>
          {summary && !summary.ledgerComplete && <p role="status" className="mt-3 text-xs text-warning">Sổ khớp lệnh chưa đối soát đủ với vị thế. Lãi/lỗ sau phí chưa xác định.</p>}
          {!!summary?.stalePrices.length && <p role="status" className="mt-3 text-xs text-secondary">Giá gần nhất hoặc đóng cửa: {summary.stalePrices.join(", ")}. Cập nhật tài khoản lúc {time(summary.valuedAt)}.</p>}
          {accountResource.error && <p role="status" className="mt-3 text-xs text-warning">Chưa làm mới được tài khoản; đang hiển thị dữ liệu lần tải gần nhất.</p>}
        </details>

        {/* Layer 1: Macro Surveillance & Universe Discovery (Agent 01 & Agent 02) */}
        <details className="rounded-xl border border-line bg-surface p-5">
          <summary className="cursor-pointer text-sm font-medium focus-visible:outline-2 focus-visible:outline-mineral">Dữ liệu thị trường và kết quả sàng lọc</summary>
          <div className="mt-5"><MacroUniverseSection msEntry={msEntry} udEntry={udEntry} /></div>
        </details>

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
                const d = plan ? approvedOrder(plan, entriesFor("portfolio_risk").map(log => log.entry)) : thesisReview(t, t ? related(data?.counterTheses || [], t) : undefined, t ? related(resolutions, t) : undefined)
                return (
                  <button
                    key={item}
                    onClick={() => setSymbol(item)}
                    aria-pressed={active === item}
                    className={`w-full shrink-0 rounded-lg border-l-2 px-3 py-3 text-left transition-colors focus-visible:outline-2 focus-visible:outline-mineral max-md:w-44 ${active === item ? "border-teal bg-soft" : "border-transparent hover:bg-paper"}`}
                  >
                    <span className="block font-mono text-base font-semibold">{item}</span>
                    <span className={`mt-1 inline-block rounded px-1.5 py-0.5 text-xs font-medium ${d.tone}`}>{d.label}</span>
                    <span className="mt-1 block text-[11px] text-secondary">{plan ? "Quyết định giao dịch" : "Kết luận thẩm định"}</span>
                  </button>
                )
              })}
            </nav>
            {ready && !visibleSymbols.length && <p className="py-4 text-sm text-secondary">{search ? "Không tìm thấy mã phù hợp." : "Chưa có luận điểm hoặc quyết định trong ngày này."}</p>}
            <p className="mt-3 border-t border-line pt-3 text-xs leading-relaxed text-secondary">{date ? displayDate(date) : "Tất cả ngày · luận điểm mới nhất của mỗi mã"}</p>
          </aside>

          <section className="min-w-0 space-y-5" aria-label="Chi tiết quyết định">
            {active ? <>
              <FinalOrderSection ticker={active} plan={planForReview} riskLogs={entriesFor("portfolio_risk").map(log => log.entry)} executionEntries={matchingEntries("trade_execution").map(log => log.entry)} position={valuedPosition} portfolioAvailable={!!portfolio} />
              <InvestmentThesisSection thesis={thesis} counter={counter} resolution={resolution} />
              <details className="rounded-xl border border-line bg-surface p-5">
                <summary className="cursor-pointer text-sm font-medium focus-visible:outline-2 focus-visible:outline-mineral">Dữ liệu phân tích và theo dõi vị thế</summary>
                <div className="mt-5 space-y-5">
                  <EquityResearchSection entry={eqEntry} ticker={active} />
                  <PositionMonitoringSection position={position} entry={posMonEntry} ticker={active} />
                </div>
              </details>
            </> : (
              <div className="rounded-xl border border-dashed border-line p-8 text-sm text-secondary">
                Chọn một mã để xem quyết định. Nhật ký hệ thống vẫn có thể được xem bên dưới.
              </div>
            )}
          </section>
        </div>

        {/* Layer 5: Offline Governance & Continuous Learning (Agent 10 & Agent 11) */}
        <details className="rounded-xl border border-line bg-surface p-5">
          <summary className="cursor-pointer text-sm font-medium focus-visible:outline-2 focus-visible:outline-mineral">Kết quả học và kiểm toán hệ thống</summary>
          <div className="mt-5"><OfflineGovernanceSection rlEntry={rlEntry} govEntry={govEntry} /></div>
        </details>

        {/* Agent Logs section */}
        <details id="agent-logs" className="rounded-xl border border-line bg-surface">
          <summary className="cursor-pointer px-5 py-5 text-base font-semibold focus-visible:outline-2 focus-visible:outline-mineral">Nhật ký và dữ liệu kỹ thuật</summary>
          <div className="space-y-4 border-b border-line p-5">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h2 className="text-base font-semibold">Tra cứu nhật ký</h2>
              <span className="text-sm text-secondary">{shownLogs.length} bản ghi khớp bộ lọc · đang hiển thị {Math.min(logLimit, shownLogs.length)} · {displayDate(date)}</span>
            </div>
            <p className="text-sm text-secondary">Đọc kết quả, lý do và dữ liệu gốc theo thời gian. Mở từng bản ghi khi cần xem dữ liệu và phép tính gốc.</p>
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
        </details>
      </div>}
    </div>
  )
}
