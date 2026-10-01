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
  cost_basis: Numeric; unrealized_pnl: Numeric
}
interface Accuracy {
  total_evaluated: number; evaluated_today: number; realized_survival_rate: Numeric
  predicted_avg_survival_prob: Numeric; directional_hit_rate: Numeric
  avg_realized_3d_ret: Numeric; avg_predicted_3d_ret: Numeric
}
interface PredictionDay { date: string; feature_date: string | null; total_predictions: number; evaluated: number }
interface NavSession { date: string; total_nav: Numeric; cash_balance: Numeric; dailyPnl: Numeric; cumulativePnl: Numeric; previousSessionDate?: string | null }
interface MLFundData {
  account: { account_id: string; cash_balance: Numeric; total_nav: Numeric; estimated_nav: Numeric; updated_at: string | null } | null
  accountId: string; positions: Position[]; predictions: Prediction[]; dates: string[]
  selectedDate: string | null; accuracy: Accuracy; history: Prediction[]; historyLimit: number; mode: string
  range: { from: string | null; to: string | null }
  navDates?: string[]; predictionSummary?: PredictionDay[]
  latestClose: { date: string; total_nav: Numeric; cash_balance: Numeric } | null
  trading: { ledgerComplete: boolean; realizedPnl: Numeric;
    sales: { id: string; date: string; symbol: string; shares: number; cost: Numeric; proceeds: Numeric; pnl: Numeric }[] | null }
  performance: { openingNav: Numeric; closingNav: Numeric; pnl: Numeric; returnPct: Numeric; fills: number; fees: Numeric; missing_receipts: number; startDate: string | null; endDate: string | null;
    baselineDate: string | null; closingDate: string | null;
    sessions: NavSession[]; missingDates?: string[]; equityCurve?: { date: string; value: number }[] }
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
      <thead><tr className="border-y border-line text-[10px] uppercase text-muted">{["Mã CP", "Điểm xếp hạng", "Điểm Z", "P(Sống sót)", "Kỳ vọng 3 phiên", "Khối lượng đề xuất", "Giá nền ML", "Tỷ trọng đề xuất", "Quyết định", "Trạng thái lệnh", "Chế độ"].map((title, i) => <th key={title} className={`px-3 py-2 font-medium ${i ? "text-right" : "text-left"}`}>{title}</th>)}</tr></thead>
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
  const nav = numeric(data.account?.estimated_nav)
  const values = data.positions.map(p => numeric(p.market_value))
  const invested = values.some(v => v == null) ? null : values.reduce<number>((sum, v) => sum + (v ?? 0), 0)
  function weight(value: Numeric) { const n = numeric(value); return n != null && nav != null && nav > 0 ? percent(n / nav, true) : "—" }
  return <Panel>
    <PanelHead title="Danh mục ML hiện tại" sub="Ước tính theo giá đóng cửa gần nhất của từng mã · Giá vốn gồm phí mua, chưa trừ phí bán" />
    <p className="mb-3 text-[11px] text-muted">Tỷ trọng tính theo NAV ước tính của vị thế và tiền mặt hiện tại: {money(data.account?.estimated_nav)}.</p>
    {!data.trading.ledgerComplete && <p className="mb-3 text-[12px] text-warning">Sổ khớp lệnh chưa đối soát được với vị thế hiện tại. Chưa xác định giá vốn và lãi/lỗ.</p>}
    {!data.account && <p className="mb-3 text-[12px] text-warning">Chưa có tài khoản ML trong dữ liệu.</p>}
    {data.positions.length === 0 ? <p className="py-8 text-center text-[13px] text-muted">Tài khoản ML chưa có vị thế mở.</p> : <div className="overflow-x-auto"><table className="w-full text-[12px]">
      <thead><tr className="border-b border-line text-[10px] uppercase text-muted">{["Mã CP", "Khối lượng", "Giá vốn/CP", "Thị giá/CP", "Tổng vốn", "Giá trị thị trường", "Lãi/lỗ", "Lãi/lỗ %", "Tỷ trọng"].map((title, i) => <th key={title} className={`py-2 pr-3 font-medium whitespace-nowrap ${i ? "text-right" : "text-left"}`}>{title}</th>)}</tr></thead>
      <tbody>{data.positions.map(p => {
        const cost = numeric(p.cost_basis)
        const pnl = numeric(p.unrealized_pnl)
        return <tr key={p.id} className="border-b border-line">
        <td className="py-3 pr-3"><Link to={`/stock/${p.symbol}`} className="font-mono font-semibold hover:underline">{p.symbol}</Link></td>
        <td className="pr-3 text-right font-mono">{numberText(p.quantity)}</td><td className="pr-3 text-right font-mono">{money(cost == null ? null : cost / p.quantity)}</td>
        <td className="pr-3 text-right font-mono">{money(p.current_price)}<div className="text-[10px] text-muted">{day(p.price_date)}</div></td>
        <td className="pr-3 text-right font-mono whitespace-nowrap">{money(cost)}</td><td className="pr-3 text-right font-mono whitespace-nowrap">{money(p.market_value)}</td>
        <td className={`pr-3 text-right font-mono whitespace-nowrap ${pnlTone(pnl)}`}>{money(pnl)}</td><td className={`pr-3 text-right font-mono ${pnlTone(pnl)}`}>{percent(cost && pnl != null ? pnl / cost : null, true, true)}</td><td className="pr-3 text-right font-mono">{weight(p.market_value)}</td>
      </tr>})}<tr><td colSpan={5} className="pt-3 text-[11px] uppercase text-muted">Tổng giá trị vị thế</td><td className="pt-3 pr-3 text-right font-mono">{money(invested)}</td><td colSpan={2} className="pt-3 pr-3 text-right font-mono">{money(data.positions.some(p => numeric(p.unrealized_pnl) == null) ? null : data.positions.reduce((sum, p) => sum + Number(p.unrealized_pnl), 0))}</td><td className="pt-3 pr-3 text-right font-mono">{weight(invested)}</td></tr></tbody>
    </table></div>}
    {values.some(v => v == null) && <p className="mt-3 text-[12px] text-warning">Thiếu giá thị trường cho một số vị thế; chưa tính được NAV và tỷ trọng đầy đủ.</p>}
  </Panel>
}

function AccuracyDashboard({ data }: { data: MLFundData }) {
  const a = data.accuracy
  const days = data.predictionSummary ?? []
  const pending = days.reduce((sum, d) => sum + d.total_predictions - d.evaluated, 0)
  const latest = days.find(d => d.evaluated > 0)?.date ?? data.history[0]?.predict_date
  return <Panel>
    <PanelHead title="Kết quả dự báo ML" sub={`${a.total_evaluated} dự báo đã đối soát${latest ? ` · đến ngày dự báo ${day(latest)}` : ""}`} />
    {a.total_evaluated === 0 && <p className="mb-4 text-[12px] text-muted">Chưa có kết quả đối soát trong kỳ này. Dự báo cần đủ giá của 3 phiên sau ngày dữ liệu nền để đánh giá.</p>}
    <div className="grid grid-cols-2 gap-4">{[
      ["Sống sót thực tế", percent(a.realized_survival_rate)],
      ["Sống sót mô hình dự báo", percent(a.predicted_avg_survival_prob)],
      ["Đúng xu hướng", percent(a.directional_hit_rate)],
      ["Biến động giá sau 3 phiên", percent(a.avg_realized_3d_ret, false, true)],
      ["Kỳ vọng 3 phiên", percent(a.avg_predicted_3d_ret, false, true)],
      ["Chưa đối soát", `${numberText(pending)} dự báo`],
    ].map(([label, value]) => <div key={label}><div className="text-[10px] uppercase text-muted">{label}</div><div className="mt-1 font-mono text-[18px]">{value}</div></div>)}</div>
    <p className="mt-4 text-[11px] text-muted">Dự báo chưa đối soát không tính vào các tỷ lệ. Trạng thái từng ngày hiển thị trong lịch sử đối soát bên dưới.</p>
    <details className="mt-2 text-[11px] text-muted"><summary className="cursor-pointer text-mineral">Mốc giá và ý nghĩa các chỉ số</summary><p className="mt-2 leading-relaxed">Đối soát dùng giá nền ML đã lưu và ba bản ghi giá sau ngày dữ liệu nền. “Sống sót thực tế” xét mức giảm trong hai phiên đầu; “Đúng xu hướng” so chiều biến động đến phiên thứ ba. Kết quả này đo dự báo bằng giá dữ liệu ML, chưa trừ phí giao dịch. Lãi/lỗ đã chốt lấy từ giá khớp mua/bán thực tế.</p></details>
  </Panel>
}

function TradingDashboard({ data }: { data: MLFundData }) {
  const period = data.range.from || data.range.to ? `${day(data.range.from)} → ${data.range.to ? day(data.range.to) : "hiện tại"}` : "Toàn bộ lịch sử khớp lệnh"
  return <Panel>
    <PanelHead title="Lãi/lỗ đã chốt" sub={`Theo ngày bán khớp · ${period}`} />
    {!data.trading.ledgerComplete && <p className="mb-4 text-[12px] text-warning">Chưa đủ chứng từ hoặc sổ khớp lệnh lệch với vị thế hiện tại. Chưa xác định lãi/lỗ đã chốt.</p>}
    <div className="grid grid-cols-2 gap-4">{[
      ["Lãi/lỗ sau phí và thuế", money(data.trading.realizedPnl)],
      ["Lượt bán đã khớp", data.trading.sales ? numberText(data.trading.sales.length) : "—"],
      ["Phí/thuế phát sinh trong kỳ", money(data.performance.fees)],
      ["Tổng lượt mua/bán khớp", numberText(data.performance.fills)],
    ].map(([label, value]) => <div key={label}><div className="text-[10px] uppercase text-muted">{label}</div><div className={`mt-1 font-mono text-[18px] ${label === "Lãi/lỗ sau phí và thuế" ? pnlTone(data.trading.realizedPnl) : ""}`}>{value}</div></div>)}</div>
    <p className="mt-4 text-[11px] text-muted">Tiền bán sau phí/thuế trừ giá vốn bình quân của lượng đã bán, gồm phí mua được phân bổ. Giá vốn giữ toàn bộ lịch sử mua, kể cả trước kỳ chọn.</p>
    <p className="mt-2 text-[11px] text-muted">ML xét bán theo điều kiện bảo vệ giá và mốc 5 ngày lịch. Ba phiên là kỳ đối soát dự báo; lãi/lỗ được ghi nhận khi bán khớp.</p>
  </Panel>
}

function SaleHistory({ data }: { data: MLFundData }) {
  const sales = data.trading.sales
  return <Panel className="mb-4">
    <PanelHead title="Các lần bán đã khớp trong kỳ" sub="Bao gồm lượng bán một phần · Lãi/lỗ cố định theo chứng từ khớp lệnh" />
    {!sales ? <p className="text-[13px] text-muted">Chưa đối soát được sổ khớp lệnh.</p> : !sales.length ? <p className="text-[13px] text-muted">Kỳ này chưa có lần bán khớp. Lãi/lỗ đã chốt là 0 đ; vị thế đang mở được trình bày riêng.</p> : <div className="max-h-80 overflow-auto"><table className="w-full min-w-[660px] text-[12px]">
      <thead className="sticky top-0 bg-surface"><tr className="border-b border-line text-muted">{["Ngày bán", "Mã CP", "Khối lượng", "Giá vốn gồm phí mua", "Tiền bán sau phí/thuế", "Lãi/lỗ đã chốt"].map(t => <th key={t} className="py-2 pr-3 text-right font-medium">{t}</th>)}</tr></thead>
      <tbody>{sales.map(s => <tr key={s.id} className="border-b border-line"><td className="py-3 pr-3 text-right font-mono">{day(s.date)}</td><td className="pr-3 text-right font-mono">{s.symbol}</td><td className="pr-3 text-right font-mono">{numberText(s.shares)}</td><td className="pr-3 text-right font-mono whitespace-nowrap">{money(s.cost)}</td><td className="pr-3 text-right font-mono whitespace-nowrap">{money(s.proceeds)}</td><td className={`pr-3 text-right font-mono whitespace-nowrap ${pnlTone(s.pnl)}`}>{money(s.pnl)}</td></tr>)}</tbody>
    </table></div>}
  </Panel>
}

function NavHistory({ data }: { data: MLFundData }) {
  const performance = data.performance
  const points = (performance.equityCurve ?? performance.sessions.map(s => ({ date: s.date, value: Number(s.total_nav) }))).filter(p => Number.isFinite(p.value) && p.value > 0)
  const missing = performance.missingDates ?? []
  const values = points.map(p => p.value).filter(Number.isFinite)
  const min = Math.min(...values), max = Math.max(...values), span = Math.max(max - min, 1)
  const start = points.length ? Date.parse(points[0].date) : 0
  const duration = points.length ? Math.max(Date.parse(points.at(-1)!.date) - start, 86400000) : 1
  const position = (p: { date: string; value: number }) => `${(Date.parse(p.date) - start) / duration * 100},${39 - (p.value - min) / span * 38}`
  const path = points.map((p, i) => {
    const hasGap = i > 0 && missing.some(date => date > points[i - 1].date && date < p.date)
    return `${i === 0 || hasGap ? "M" : "L"}${position(p)}`
  }).join(" ")
  const sessions = [
    ...performance.sessions.map(s => ({ date: s.date, snapshot: s })),
    ...missing.map(date => ({ date, snapshot: null })),
  ].sort((a, b) => b.date.localeCompare(a.date))
  return <Panel className="mb-4">
    <PanelHead title="NAV cuối phiên" sub={`${performance.sessions.length} phiên có NAV · Snapshot lịch sử giữ nguyên khi đổi bộ lọc`} />
    <div className="mb-4 grid grid-cols-2 gap-4 lg:grid-cols-4">{[
      [`NAV mốc đầu · ${day(performance.baselineDate)}`, money(performance.openingNav)],
      [`NAV mốc cuối · ${day(performance.closingDate)}`, money(performance.closingNav)],
      ["Biến động NAV trong kỳ", money(performance.pnl)],
      ["Biến động NAV (%)", percent(performance.returnPct, false, true)],
    ].map(([label, value]) => <div key={label}><div className="text-[10px] uppercase text-muted">{label}</div><div className="mt-1 font-mono text-[16px]">{value}</div></div>)}</div>
    <p className="mb-4 text-[11px] text-muted">NAV cuối phiên gồm tiền mặt và giá trị vị thế theo giá đóng cửa. Chênh lệch NAV gồm cả lãi/lỗ chưa bán, với giả định quỹ paper không nạp/rút vốn ngoài giao dịch. Thiếu mốc đầu hoặc cuối thì chưa tính biến động cả kỳ.</p>
    {missing.length > 0 && <p role="status" className="mb-4 text-[12px] text-warning">Chưa ghi nhận NAV cuối ngày: {missing.map(day).join(", ")}. Dự báo có dữ liệu không đồng nghĩa đã có NAV. Chưa tính lãi/lỗ một phiên khi thiếu NAV phiên trước.</p>}
    {values.length >= 2 && <div className="mb-5">
      <div className="mb-2 flex justify-between text-[11px] text-muted"><span>{money(min)}</span><span>{money(max)}</span></div>
      <svg role="img" aria-label="Biểu đồ NAV cuối ngày" viewBox="0 0 100 40" preserveAspectRatio="none" className="h-40 w-full"><path d={path} fill="none" stroke="var(--color-teal)" strokeWidth="0.7" />{points.map(p => <circle key={p.date} cx={(Date.parse(p.date) - start) / duration * 100} cy={39 - (p.value - min) / span * 38} r="0.6" fill="var(--color-teal)"><title>{day(p.date)}: {money(p.value)}</title></circle>)}</svg>
      <div className="mt-2 flex justify-between text-[11px] text-muted"><span>{day(points[0].date)}</span><span>{day(points.at(-1)!.date)}</span></div>
    </div>}
    {!sessions.length ? <p className="text-[13px] text-muted">Chưa có NAV cuối ngày trong kỳ này. Chọn “Toàn bộ lịch sử” để xem các phiên đã lưu.</p> : <div className="max-h-80 overflow-auto"><table className="w-full min-w-[620px] text-[12px]">
      <thead className="sticky top-0 bg-surface"><tr className="border-b border-line text-muted">{["Phiên", "NAV cuối ngày", "Tiền mặt", "Biến động NAV phiên", "Biến động từ mốc đầu"].map(t => <th key={t} className="py-2 pr-3 text-right font-medium">{t}</th>)}</tr></thead>
      <tbody>{sessions.map(({ date, snapshot: s }) => <tr key={date} className="border-b border-line"><td className="py-3 pr-3 text-right font-mono whitespace-nowrap">{day(date)}</td>{s ? <><td className="pr-3 text-right font-mono whitespace-nowrap">{money(s.total_nav)}</td><td className="pr-3 text-right font-mono whitespace-nowrap">{money(s.cash_balance)}</td><td className={`pr-3 text-right whitespace-nowrap ${pnlTone(s.dailyPnl)}`}><span className="font-mono">{money(s.dailyPnl)}</span>{s.dailyPnl == null && s.previousSessionDate && <div className="text-[10px] text-muted">Thiếu NAV {day(s.previousSessionDate)}</div>}</td><td className={`pr-3 text-right font-mono whitespace-nowrap ${pnlTone(s.cumulativePnl)}`}>{money(s.cumulativePnl)}</td></> : <td colSpan={4} className="pr-3 text-right text-warning">Chưa ghi nhận NAV cuối ngày</td>}</tr>)}</tbody>
    </table></div>}
  </Panel>
}

function AccuracyHistory({ data }: { data: MLFundData }) {
  const dates = [...new Set(data.history.map(p => p.predict_date.slice(0, 10)))].sort().reverse()
  const [selected, setSelected] = useState("")
  const [page, setPage] = useState(0)
  const active = dates.includes(selected) ? selected : dates[0]
  const rows = data.history.filter(p => p.predict_date.slice(0, 10) === active)
  const pages = Math.max(1, Math.ceil(rows.length / 20))
  const currentPage = Math.min(page, pages - 1)
  const pending = (data.predictionSummary ?? []).filter(d => d.evaluated < d.total_predictions)
  return <Panel className="mb-4">
    <PanelHead title="Lịch sử đối soát dự báo" sub="Giá nền đã lưu → ba phiên sau ngày dữ liệu nền · Kỳ lọc theo ngày dự báo" />
    {pending.length > 0 && <div className="mb-4 flex flex-wrap gap-2">{pending.map(d => <div key={d.date} className="rounded-lg border border-line px-3 py-2 text-[12px]"><div className="font-medium">{day(d.date)}</div><div className="text-muted">{d.total_predictions - d.evaluated}/{d.total_predictions} dự báo chưa đối soát</div>{d.feature_date && <div className="text-[10px] text-muted">Cần 3 phiên sau {day(d.feature_date)}</div>}</div>)}</div>}
    {!rows.length ? <p className="text-[13px] text-muted">Chưa có kết quả đối soát trong kỳ đã chọn.</p> : <>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3"><label className="flex items-center gap-2 text-[12px]">Ngày đã có kết quả<select aria-label="Ngày đối soát" value={active} onChange={e => { setSelected(e.target.value); setPage(0) }} className="rounded-lg border border-line bg-surface px-3 py-2">{dates.map(date => <option key={date} value={date}>{day(date)}</option>)}</select></label><span className="text-[11px] text-muted">{rows.length} bản ghi đã tải cho ngày này</span></div>
      <div className="overflow-x-auto"><table className="w-full min-w-[640px] text-[12px]">
        <thead><tr className="border-b border-line text-muted">{["Mã CP", "Ngày dữ liệu nền", "Giá nền ML", "P(Sống sót)", "Sụt trong hai phiên đầu", "Biến động giá 3 phiên", "Sống sót"].map(title => <th key={title} className="py-2 pr-3 text-left font-medium">{title}</th>)}</tr></thead>
        <tbody>{rows.slice(currentPage * 20, (currentPage + 1) * 20).map(p => <tr key={p.id} className="border-b border-line"><td className="py-3 pr-3 font-mono">{p.ticker}</td><td className="pr-3 font-mono whitespace-nowrap">{day(p.feature_date)}</td><td className="pr-3 font-mono whitespace-nowrap">{money(p.price)}</td><td className="pr-3 font-mono">{percent(p.surv_prob, true)}</td><td className="pr-3 font-mono">{percent(p.realized_min_lock_ret, true, true)}</td><td className="pr-3 font-mono">{percent(p.realized_3d_ret, true, true)}</td><td>{p.survival_outcome == null ? "—" : p.survival_outcome ? "Đạt" : "Không đạt"}</td></tr>)}</tbody>
      </table></div>
      {pages > 1 && <div className="mt-3 flex items-center justify-end gap-3"><Button disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>Trước</Button><span className="text-[12px]">{currentPage + 1}/{pages}</span><Button disabled={currentPage === pages - 1} onClick={() => setPage(currentPage + 1)}>Sau</Button></div>}
    </>}
    {data.accuracy.total_evaluated > data.historyLimit && <p className="mt-3 text-[11px] text-muted">Bảng chi tiết tải {data.historyLimit} kết quả gần nhất. Thu hẹp kỳ thống kê để xem ngày cũ hơn; các chỉ số vẫn tính toàn bộ kỳ.</p>}
  </Panel>
}

export default function MLFund() {
  const today = vietnamDate()
  const [selectedDate, setSelectedDate] = useState("")
  const [range, setRange] = useState("all")
  const [from, setFrom] = useState(today)
  const [to, setTo] = useState(today)
  const periodStart = range === "custom" ? from : range === "all" ? "" : new Date(Date.parse(`${today}T00:00:00Z`) - (Number(range) - 1) * 86400000).toISOString().slice(0, 10)
  const periodEnd = range === "custom" ? to : range === "all" ? "" : today
  const invalidRange = !!periodStart && !!periodEnd && periodStart > periodEnd
  const resource = useResource<MLFundData>(() => invalidRange ? Promise.reject(new Error("Khoảng ngày không hợp lệ")) : workspaceApi.mlFund(selectedDate || undefined, { from: periodStart || undefined, to: periodEnd || undefined }), [selectedDate, periodStart, periodEnd, invalidRange])
  const data = resource.data
  const calendarDates = [...new Set([...(data?.dates ?? []), ...(data?.navDates ?? [])])].sort().reverse()
  const priceDates = data?.positions.map(p => p.price_date?.slice(0, 10)).filter((date): date is string => !!date).sort() ?? []
  return <Page title="ML Tự hành" sub="Mô hình định lượng độc lập · Tài khoản mô phỏng riêng" actions={<Button onClick={() => void resource.reload()} disabled={resource.loading}>Làm mới dữ liệu</Button>}>
    <div className="mb-4 flex flex-wrap items-center gap-3"><label htmlFor="ml-stat-period" className="text-[12px] font-medium">Kỳ thống kê</label><select id="ml-stat-period" value={range} onChange={e => { const next = e.target.value; setRange(next); if (next === "custom") { setFrom(data?.performance.startDate ?? today); setTo(data?.performance.endDate ?? today) } }} className="rounded-lg border border-line bg-surface px-3 py-2 text-[12px]">{[["all", "Toàn bộ lịch sử"], ["15", "15 ngày gần nhất"], ["30", "30 ngày gần nhất"], ["60", "60 ngày gần nhất"], ["custom", "Khoảng ngày cụ thể"]].map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>{range === "custom" && <><span className="text-[12px]">Từ</span><FinancialCalendar label="Từ ngày thống kê" allowAll={false} value={from} dates={calendarDates} onChange={setFrom}/><span className="text-[12px]">Đến</span><FinancialCalendar label="Đến ngày thống kê" allowAll={false} value={to} dates={calendarDates} onChange={setTo}/></>}<span className="text-[11px] text-muted">Lãi/lỗ tài khoản và kết quả dự báo</span>{invalidRange && <span className="text-[12px] text-loss">Ngày bắt đầu phải trước ngày kết thúc.</span>}</div>
    {resource.error && <p role="status" className="mb-4 text-[13px] text-loss">{invalidRange ? "Khoảng ngày không hợp lệ." : "Không làm mới được dữ liệu ML."}{data ? " Đang giữ số liệu kỳ đã tải trước đó." : ""} <button type="button" onClick={() => void resource.reload()} className="underline">Thử lại</button></p>}
    {!data ? <Panel className="text-[13px] text-muted">{resource.loading ? "Đang tải dữ liệu ML..." : "Chưa tải được dữ liệu."}</Panel> : <>
      {resource.loading && <p role="status" className="mb-3 text-[12px] text-muted">Đang làm mới · Số liệu bên dưới thuộc lần tải trước.</p>}
      <Panel className="mb-4"><PanelHead title="Quỹ ML Tự hành" sub="Snapshot NAV mới nhất và tài khoản hiện tại · Không theo bộ lọc kỳ" action={<Pill tone="teal">Mô phỏng</Pill>}/><div className="grid grid-cols-2 gap-4 sm:grid-cols-3">{[[`NAV chốt · ${day(data.latestClose?.date ?? null)}`, money(data.latestClose?.total_nav)], ["Tiền mặt hiện tại", money(data.account?.cash_balance)], ["Vị thế đang mở", numberText(data.positions.length)]].map(([label, value]) => <div key={label}><div className="text-[10px] uppercase text-muted">{label}</div><div className="mt-1 font-mono text-[18px]">{value}</div></div>)}</div><p className="mt-3 text-[11px] text-muted">{data.accountId}{priceDates.length ? ` · Vị thế hiện tại dùng giá đóng cửa từ ${day(priceDates[0])}${priceDates.at(-1) !== priceDates[0] ? ` đến ${day(priceDates.at(-1)!)}` : ""}` : ""}</p></Panel>
      <div className="mb-4 grid grid-cols-1 items-start gap-4 lg:grid-cols-2"><TradingDashboard data={data}/><AccuracyDashboard data={data}/></div>
      <SaleHistory data={data}/>
      <NavHistory data={data}/>
      <div className="mb-4"><Portfolio data={data}/></div>
      <div className="mb-3 flex flex-wrap items-center gap-3"><span className="text-[12px] font-medium">Ngày dự báo</span><FinancialCalendar label="Ngày dự báo ML" allowAll={false} availableOnly value={selectedDate || data.selectedDate || ""} dates={data.dates} onChange={setSelectedDate}/><button type="button" onClick={() => setSelectedDate("")} className="text-[12px] text-mineral hover:underline">Dự báo mới nhất</button><span className="text-[11px] text-muted">Chỉ lọc bảng dự báo bên dưới</span></div>
      <Predictions rows={data.predictions} date={data.selectedDate}/>
      <AccuracyHistory data={data}/>
    </>}
  </Page>
}
