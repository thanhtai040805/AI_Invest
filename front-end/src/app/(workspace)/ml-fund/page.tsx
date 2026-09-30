"use client"

import { useState } from "react"
import { Page } from "@/components/Shell"
import { FinancialCalendar } from "@/components/FinancialCalendar"
import { Button, Panel, PanelHead, Pill } from "@/components/ui"
import { displayDate, vietnamDate } from "@/lib/financial-date"
import { workspaceApi } from "@/lib/api"
import { useResource } from "@/lib/api/use-resource"
import { Link } from "@/lib/router"

type Numeric = number | string | null
interface Prediction {
  id: number; ticker: string; predict_date: string; rank_pred: Numeric; pred_score_z: Numeric
  surv_prob: Numeric; mom_pred: Numeric; shares: Numeric; price: Numeric; target_weight_pct: Numeric
  execution_mode: string | null; realized_min_lock_ret: Numeric; realized_3d_ret: Numeric
  survival_outcome: boolean | null
  decision: string | null; decision_reason: string | null; order_status: string | null
  feature_date: string | null; model_version: string | null
}
interface Position {
  id: string; symbol: string; quantity: number; avg_price: Numeric; current_price: Numeric
  market_value: Numeric; price_date: string | null
}
interface Accuracy {
  total_evaluated: number; evaluated_today: number; realized_survival_rate: Numeric
  predicted_avg_survival_prob: Numeric; directional_hit_rate: Numeric
  avg_realized_3d_ret: Numeric; avg_predicted_3d_ret: Numeric
}
interface MLFundData {
  account: { account_id: string; cash_balance: Numeric; total_nav: Numeric; updated_at: string | null } | null
  accountId: string; positions: Position[]; predictions: Prediction[]; dates: string[]
  selectedDate: string | null; accuracy: Accuracy; history: Prediction[]; historyLimit: number; mode: string
  performance: { openingNav: Numeric; closingNav: Numeric; pnl: Numeric; returnPct: Numeric; fills: number; fees: Numeric; missing_receipts: number; startDate: string | null; endDate: string | null;
    sessions: { date: string; total_nav: Numeric; cash_balance: Numeric; dailyPnl: Numeric; cumulativePnl: Numeric }[] }
}

function numeric(value: Numeric | undefined): number | null {
  if (value == null || value === "") return null
  const n = Number(value)
  return Number.isFinite(n) ? n : null
}
function numberText(value: Numeric | undefined, decimals = 0) {
  const n = numeric(value)
  return n == null ? "—" : n.toLocaleString("vi-VN", { maximumFractionDigits: decimals })
}
function money(value: Numeric | undefined) {
  return numeric(value) == null ? "—" : `${numberText(value)} ₫`
}
function percent(value: Numeric | undefined, fraction = false, signed = false) {
  const n = numeric(value)
  if (n == null) return "—"
  const pct = fraction ? n * 100 : n
  return `${signed && pct > 0 ? "+" : ""}${pct.toLocaleString("vi-VN", { minimumFractionDigits: 1, maximumFractionDigits: 1 })}%`
}
function day(value: string | null) { return value ? displayDate(value.slice(0, 10)) : "—" }
function pnlTone(value: Numeric) { const n = numeric(value); return n == null || n === 0 ? "" : n > 0 ? "text-gain" : "text-loss" }

const decisionReasons: Record<string, string> = {
  SELECTED: "Được chọn", ALREADY_HELD: "Đã có vị thế", ORDER_ALREADY_PENDING: "Đã có lệnh chờ",
  INSUFFICIENT_BUDGET: "Không đủ ngân sách sau phí", OUTSIDE_TOP_K: "Ngoài nhóm ưu tiên",
  INVALID_PREDICTION: "Dữ liệu dự báo không hợp lệ", NO_HISTORICAL_PRICE: "Thiếu giá lịch sử",
  BEAR_DEFENSE: "Giữ tiền theo chế độ phòng vệ",
}
const orderStatuses: Record<string, string> = {
  PENDING_SHADOW: "Chờ khớp mô phỏng", PENDING_REPLAY: "Chờ khớp lịch sử", FILLED: "Đã khớp mô phỏng",
  FILLED_REPLAY: "Đã khớp lịch sử", EXPIRED: "Hết hiệu lực", EXPIRED_REPLAY: "Hết phiên lịch sử", CANCELLED: "Đã hủy",
}
function Predictions({ rows, date }: { rows: Prediction[]; date: string | null }) {
  return <Panel flush className="mb-4">
    <div className="px-5 pt-5"><PanelHead title="Bảng dự báo định lượng ML" sub={`Phiên ${day(date)} · ${rows.length} mã · Dự báo và đề xuất phân bổ, không phải vị thế đã khớp`} /></div>
    {rows.length === 0 ? <p className="px-5 pb-5 text-[13px] text-muted">Chưa có dự báo {date ? "trong ngày đã chọn" : "ML được lưu"}.</p> : <div className="overflow-x-auto"><table className="w-full text-[12px]">
      <thead><tr className="border-y border-line text-[10px] uppercase text-muted">{["Mã CP", "Điểm xếp hạng", "Điểm Z", "P(Sống sót)", "Kỳ vọng 3 phiên", "Khối lượng đề xuất", "Giá tham chiếu", "Tỷ trọng đề xuất", "Quyết định", "Trạng thái lệnh", "Chế độ"].map((title, i) => <th key={title} className={`px-3 py-2 font-medium ${i ? "text-right" : "text-left"}`}>{title}</th>)}</tr></thead>
      <tbody>{rows.map(p => <tr key={p.id} className="border-b border-line">
        <td className="px-3 py-3"><Link to={`/stock/${p.ticker}`} className="font-mono font-semibold hover:underline">{p.ticker}</Link></td>
        <td className="px-3 text-right font-mono">{numberText(p.rank_pred, 3)}</td><td className="px-3 text-right font-mono">{numberText(p.pred_score_z, 2)}</td>
        <td className="px-3 text-right font-mono">{percent(p.surv_prob, true)}</td><td className="px-3 text-right font-mono">{percent(p.mom_pred, true, true)}</td>
        <td className="px-3 text-right font-mono">{numberText(p.shares)}</td><td className="px-3 text-right font-mono">{money(p.price)}<div className="text-[10px] text-muted">{day(p.feature_date)}</div></td>
        <td className="px-3 text-right font-mono">{p.decision === "SKIP" ? "—" : percent(p.target_weight_pct, true)}</td>
        <td className="px-3 text-right">{p.decision === "BUY" ? "Đề xuất mua" : p.decision === "SKIP" ? "Bỏ qua" : "—"}<div className="text-[10px] text-muted">{p.decision_reason ? decisionReasons[p.decision_reason] || p.decision_reason : ""}</div></td>
        <td className="px-3 text-right">{p.order_status ? orderStatuses[p.order_status] || p.order_status : "—"}</td>
        <td className="px-3 text-right">{p.execution_mode === "REPLAY" ? "Replay" : p.execution_mode === "SHADOW_RUNNER" ? "Shadow" : p.execution_mode || "—"}</td>
      </tr>)}</tbody>
    </table></div>}
  </Panel>
}

function Portfolio({ data }: { data: MLFundData }) {
  const nav = numeric(data.account?.total_nav)
  const values = data.positions.map(p => numeric(p.market_value))
  const invested = values.some(v => v == null) ? null : values.reduce<number>((sum, v) => sum + (v ?? 0), 0)
  function weight(value: Numeric) { const n = numeric(value); return n != null && nav != null && nav > 0 ? percent(n / nav, true) : "—" }
  return <Panel>
    <PanelHead title="Danh mục ML" sub="Vị thế đang mở · Lãi/lỗ tạm tính theo giá đóng cửa gần nhất, chưa trừ phí bán" />
    {!data.account && <p className="mb-3 text-[12px] text-warning">Chưa có tài khoản ML trong dữ liệu.</p>}
    {data.positions.length === 0 ? <p className="py-8 text-center text-[13px] text-muted">Tài khoản ML chưa có vị thế mở.</p> : <div className="overflow-x-auto"><table className="w-full text-[12px]">
      <thead><tr className="border-b border-line text-[10px] uppercase text-muted">{["Mã CP", "Khối lượng", "Giá vốn/CP", "Thị giá/CP", "Tổng vốn", "Giá trị thị trường", "Lãi/lỗ", "Lãi/lỗ %", "Tỷ trọng"].map((title, i) => <th key={title} className={`py-2 pr-3 font-medium whitespace-nowrap ${i ? "text-right" : "text-left"}`}>{title}</th>)}</tr></thead>
      <tbody>{data.positions.map(p => {
        const cost = numeric(p.avg_price) == null ? null : Number(p.avg_price) * p.quantity
        const value = numeric(p.market_value)
        const pnl = cost == null || value == null ? null : value - cost
        return <tr key={p.id} className="border-b border-line">
        <td className="py-3 pr-3"><Link to={`/stock/${p.symbol}`} className="font-mono font-semibold hover:underline">{p.symbol}</Link></td>
        <td className="pr-3 text-right font-mono">{numberText(p.quantity)}</td><td className="pr-3 text-right font-mono">{money(p.avg_price)}</td>
        <td className="pr-3 text-right font-mono">{money(p.current_price)}<div className="text-[10px] text-muted">{day(p.price_date)}</div></td>
        <td className="pr-3 text-right font-mono whitespace-nowrap">{money(cost)}</td><td className="pr-3 text-right font-mono whitespace-nowrap">{money(p.market_value)}</td>
        <td className={`pr-3 text-right font-mono whitespace-nowrap ${pnlTone(pnl)}`}>{money(pnl)}</td><td className={`pr-3 text-right font-mono ${pnlTone(pnl)}`}>{percent(cost && pnl != null ? pnl / cost : null, true, true)}</td><td className="pr-3 text-right font-mono">{weight(p.market_value)}</td>
      </tr>})}<tr><td colSpan={5} className="pt-3 text-[11px] uppercase text-muted">Tổng giá trị vị thế</td><td className="pt-3 pr-3 text-right font-mono">{money(invested)}</td><td colSpan={2} className="pt-3 pr-3 text-right font-mono">{money(invested == null || data.positions.some(p => numeric(p.avg_price) == null) ? null : invested - data.positions.reduce((sum, p) => sum + Number(p.avg_price) * p.quantity, 0))}</td><td className="pt-3 pr-3 text-right font-mono">{weight(invested)}</td></tr></tbody>
    </table></div>}
    {values.some(v => v == null) && <p className="mt-3 text-[12px] text-warning">Thiếu giá thị trường cho một số vị thế; chưa tính được NAV và tỷ trọng đầy đủ.</p>}
  </Panel>
}

function AccuracyDashboard({ data }: { data: MLFundData }) {
  const [showHistory, setShowHistory] = useState(false)
  const a = data.accuracy
  return <Panel>
    <PanelHead title="Độ chính xác · Kỳ thống kê đã chọn" sub={`${a.total_evaluated} dự báo đã đối soát · ${a.evaluated_today} đối soát hôm nay`} />
    {a.total_evaluated === 0 ? <p className="py-8 text-center text-[13px] text-muted">Chưa có dự báo ML đã đối soát trong kỳ chọn. Độ chính xác sẽ hiển thị khi đủ 3 phiên sau ngày dữ liệu nền.</p> : <>
      <p className="mb-3 text-[11px] leading-relaxed text-secondary">“Tỷ lệ sống sót thực tế” so sánh các dự báo với kết quả của chiến lược giữ vị thế; “Kỳ vọng sống sót” là xác suất trung bình mô hình dự báo. “Đúng xu hướng” đo riêng việc dự báo tăng/giảm T+3. Các tỷ lệ này đo những mục tiêu khác nhau.</p>
      <div className="space-y-3">{[["Tỷ lệ sống sót thực tế", a.realized_survival_rate], ["Kỳ vọng sống sót mô hình", a.predicted_avg_survival_prob], ["Đúng xu hướng", a.directional_hit_rate]].map(([label, value]) => <div key={String(label)} className="flex justify-between gap-3 text-[12px]"><span className="text-secondary">{label}</span><span className="font-mono">{percent(value as Numeric)}</span></div>)}</div>
      <div className="my-4 grid grid-cols-2 gap-3">{[["Biến động thực tế 3 phiên", a.avg_realized_3d_ret], ["Kỳ vọng mô hình 3 phiên", a.avg_predicted_3d_ret]].map(([label, value]) => <div key={String(label)} className="rounded-lg border border-line bg-paper p-3"><div className="text-[10px] uppercase text-muted">{label}</div><div className="mt-1 font-mono text-[17px]">{percent(value as Numeric, false, true)}</div></div>)}</div>
      <button type="button" onClick={() => setShowHistory(v => !v)} className="text-[12px] text-mineral hover:underline">{showHistory ? "Ẩn lịch sử đối soát ↑" : "Lịch sử đối soát ↓"}</button>
      {showHistory && <div className="mt-3 overflow-x-auto"><table className="w-full text-[11px]">
        <thead><tr className="border-b border-line text-muted">{["Ngày dự báo", "Mã CP", "P(Sống sót)", "Sụt trong thời gian khóa", "Lợi nhuận T+3", "Sống sót"].map(title => <th key={title} className="py-2 pr-3 text-left font-medium">{title}</th>)}</tr></thead>
        <tbody>{data.history.map(p => <tr key={p.id} className="border-b border-line"><td className="py-2 pr-3 font-mono">{day(p.predict_date)}</td><td className="pr-3 font-mono">{p.ticker}</td><td className="pr-3 font-mono">{percent(p.surv_prob, true)}</td><td className="pr-3 font-mono">{percent(p.realized_min_lock_ret, true, true)}</td><td className="pr-3 font-mono">{percent(p.realized_3d_ret, true, true)}</td><td>{p.survival_outcome == null ? "—" : p.survival_outcome ? "Đạt" : "Không đạt"}</td></tr>)}</tbody>
      </table>{a.total_evaluated > data.historyLimit && <p className="mt-2 text-[11px] text-muted">Hiển thị {data.historyLimit} bản ghi gần nhất; thống kê tính trên toàn bộ kỳ đã chọn.</p>}</div>}
      <p className="mt-3 text-[11px] text-muted">Đối soát theo ngày dự báo và giá tham chiếu ngày dữ liệu nền, độc lập lãi/lỗ tài khoản. Chỉ số thiếu kết quả hiển thị “—”.</p>
    </>}
  </Panel>
}

export default function MLFund() {
  const today = vietnamDate()
  const [selectedDate, setSelectedDate] = useState(today)
  const [range, setRange] = useState("custom")
  const [from, setFrom] = useState(today)
  const [to, setTo] = useState(today)
  const [showPerformance, setShowPerformance] = useState(false)
  const periodStart = range === "custom" ? from : range === "all" ? "" : new Date(Date.parse(`${today}T00:00:00Z`) - (Number(range) - 1) * 86400000).toISOString().slice(0, 10)
  const periodEnd = range === "custom" ? to : range === "all" ? "" : today
  const invalidRange = !!periodStart && !!periodEnd && periodStart > periodEnd
  const resource = useResource<MLFundData>(() => invalidRange ? Promise.reject(new Error("Khoảng ngày không hợp lệ")) : workspaceApi.mlFund(selectedDate || undefined, { from: periodStart || undefined, to: periodEnd || undefined }), [selectedDate, periodStart, periodEnd, invalidRange])
  const data = resource.data
  return <Page title="ML Tự hành" sub="Mô hình định lượng độc lập · Tài khoản mô phỏng riêng" actions={<Button onClick={() => void resource.reload()} disabled={resource.loading}>Làm mới dữ liệu</Button>}>
    <div className="mb-4 flex flex-wrap items-center gap-3"><span className="text-[12px] font-medium">Ngày dự báo</span><FinancialCalendar label="Ngày dự báo ML" allowAll={false} value={selectedDate || data?.selectedDate || ""} dates={data?.dates} onChange={setSelectedDate}/><span className="text-[11px] text-muted">Lọc bảng dự báo · Danh mục là vị thế hiện tại</span></div>
    <div className="mb-4 flex flex-wrap items-center gap-3"><label htmlFor="ml-stat-period" className="text-[12px] font-medium">Kỳ thống kê</label><select id="ml-stat-period" value={range} onChange={e => { const next = e.target.value; setRange(next); if (next === "custom") { setFrom(today); setTo(today) } }} className="rounded-lg border border-line bg-white px-3 py-2 text-[12px]">{[["all", "Toàn bộ lịch sử"], ["15", "15 ngày gần nhất"], ["30", "30 ngày gần nhất"], ["60", "60 ngày gần nhất"], ["custom", "Khoảng ngày cụ thể"]].map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>{range === "custom" && <><span className="text-[12px]">Từ</span><FinancialCalendar label="Từ ngày thống kê" allowAll={false} value={from} dates={data?.dates} onChange={setFrom}/><span className="text-[12px]">Đến</span><FinancialCalendar label="Đến ngày thống kê" allowAll={false} value={to} dates={data?.dates} onChange={setTo}/></>}<span className="text-[11px] text-muted">Áp dụng cho lãi/lỗ và độ chính xác · Ngày lịch</span>{invalidRange && <span className="text-[12px] text-loss">Ngày bắt đầu phải trước ngày kết thúc.</span>}</div>
    {resource.error ? <Panel className="text-[13px] text-loss">Không tải được dữ liệu ML. <button type="button" onClick={() => void resource.reload()} className="underline">Thử lại</button></Panel> : resource.loading || !data ? <Panel className="text-[13px] text-muted">Đang tải dữ liệu ML...</Panel> : <>
      <Panel className="mb-4"><PanelHead title="Quỹ ML Tự hành" sub={data.accountId} action={<Pill tone="teal">Mô phỏng</Pill>}/><div className="grid grid-cols-3 gap-4">{[["NAV theo giá đóng cửa", money(data.account?.total_nav)], ["Tiền mặt", money(data.account?.cash_balance)], ["Vị thế đang mở", numberText(data.positions.length)]].map(([label, value]) => <div key={label}><div className="text-[10px] uppercase text-muted">{label}</div><div className="mt-1 font-mono text-[16px]">{value}</div></div>)}</div></Panel>
      <div className="mb-4 grid grid-cols-1 gap-4 lg:grid-cols-2"><Panel><PanelHead title="Lãi/lỗ lịch sử tài khoản" sub={`${day(data.performance.startDate)} → ${day(data.performance.endDate)} · ${data.performance.sessions.length} phiên có NAV cuối ngày`}/><div className="grid grid-cols-2 gap-4">{[["NAV đầu kỳ", money(data.performance.openingNav)], ["NAV cuối kỳ", money(data.performance.closingNav)], ["Lãi/lỗ sau phí", money(data.performance.pnl)], ["Tỷ suất kỳ", percent(data.performance.returnPct, false, true)], ["Lượt khớp lệnh", numberText(data.performance.fills)], ["Phí và thuế", data.performance.missing_receipts ? "Chưa đủ hóa đơn" : money(data.performance.fees ?? 0)]].map(([label, value]) => <div key={label}><div className="text-[10px] uppercase text-muted">{label}</div><div className={`mt-1 font-mono text-[16px] ${label === "Lãi/lỗ sau phí" || label === "Tỷ suất kỳ" ? pnlTone(data.performance.pnl) : ""}`}>{value}</div></div>)}</div><p className="mt-3 text-[11px] text-muted">Chênh lệch NAV cuối kỳ so với NAV đóng cửa trước kỳ, gồm vị thế chưa đóng và phí thực tế. Không phải lợi nhuận riêng của các dự báo.</p><button type="button" onClick={() => setShowPerformance(v => !v)} className="mt-3 text-[12px] text-mineral hover:underline">{showPerformance ? "Ẩn lịch sử NAV ↑" : "Xem lịch sử NAV và lãi/lỗ từng phiên ↓"}</button>{showPerformance && <div className="mt-3 overflow-x-auto"><table className="w-full text-[11px]"><thead><tr className="border-b border-line text-muted">{["Phiên", "NAV", "Tiền mặt", "Lãi/lỗ phiên", "Lũy kế trong kỳ"].map(t => <th key={t} className="py-2 pr-3 text-right">{t}</th>)}</tr></thead><tbody>{data.performance.sessions.map(s => <tr key={s.date} className="border-b border-line"><td className="py-2 pr-3 whitespace-nowrap">{day(s.date)}</td><td className="pr-3 text-right font-mono whitespace-nowrap">{money(s.total_nav)}</td><td className="pr-3 text-right font-mono whitespace-nowrap">{money(s.cash_balance)}</td><td className={`pr-3 text-right font-mono whitespace-nowrap ${pnlTone(s.dailyPnl)}`}>{money(s.dailyPnl)}</td><td className={`pr-3 text-right font-mono whitespace-nowrap ${pnlTone(s.cumulativePnl)}`}>{money(s.cumulativePnl)}</td></tr>)}</tbody></table></div>}</Panel><AccuracyDashboard data={data}/></div>
      <div className="mb-4"><Portfolio data={data}/></div>
      <Predictions rows={data.predictions} date={data.selectedDate}/>
    </>}
  </Page>
}
