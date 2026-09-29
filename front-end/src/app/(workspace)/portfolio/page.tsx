"use client"

import { Page } from "@/components/Shell"
import { Link } from "@/lib/router"
import { portfolioApi } from "@/lib/api"
import { useResource } from "@/lib/api/use-resource"
import { DataState } from "@/components/data-state"
import { Button, MetricStrip, Panel, PanelHead, PercentChange, RiskLabel, fmt } from "@/components/ui"

function EquityCurve({ points }: { points?: { date: string; value: number }[] }) {
  const curvePoints = Array.isArray(points) ? points.map(p => p.value).filter((value): value is number => typeof value === "number" && Number.isFinite(value)) : []
  if (curvePoints.length < 2) return <p className="grid h-52 place-items-center text-center text-[13px] text-muted">Chưa đủ lịch sử NAV để vẽ đường tăng trưởng.</p>
  const min = Math.min(...curvePoints), max = Math.max(...curvePoints), range = Math.max(max - min, 1)
  const path = (d: number[]) => d.map((v, i) => `${(i / Math.max(d.length - 1, 1)) * 100},${40 - ((v - min) / range) * 38 - 1}`).join(" ")
  return (
    <svg viewBox="0 0 100 40" preserveAspectRatio="none" className="w-full h-52">
      <polyline points={path(curvePoints)} fill="none" stroke="var(--color-teal)" strokeWidth="0.7" />
    </svg>
  )
}

type PositionRow = { symbol: string; entry: number | null; current: number | null; weight: number | null; pnl: number | null; recWeight: number | null; kelly: number | null; risk: string | null; stop: number | null }
const n = (value: unknown): number | null => value === null || value === undefined || value === "" || !Number.isFinite(Number(value)) ? null : Number(value)
const displayNumber = (value: number | null) => value === null ? "—" : fmt(value)
const displayPercent = (value: number | null) => value === null ? "—" : `${value.toLocaleString("en-US", { maximumFractionDigits: 2 })}%`
const list = (value: unknown): Record<string, unknown>[] => Array.isArray(value) ? value : Array.isArray((value as { data?: unknown[] })?.data) ? (value as { data: Record<string, unknown>[] }).data : []

export default function Portfolio() {
  const resource = useResource(() => Promise.all([portfolioApi.summary(), portfolioApi.positions(), portfolioApi.performance(), portfolioApi.risks(), portfolioApi.orders()]), [])
  const [summaryRaw, positionsRaw, perfRaw, risksRaw, ordersRaw] = resource.data ?? []
  const summary = (summaryRaw?.data ?? summaryRaw ?? {}) as Record<string, unknown>
  const positions: PositionRow[] = list(positionsRaw).map(row => ({ symbol: String(row.symbol ?? row.ticker ?? "—"), entry: n(row.entry ?? row.avgPrice), current: n(row.current ?? row.currentPrice), weight: n(row.weight), pnl: n(row.pnlPercent ?? row.pnl), recWeight: n(row.recommendedWeight), kelly: n(row.kelly), risk: typeof row.risk === "string" ? row.risk : null, stop: n(row.stop ?? row.stopPrice) }))
  const summaryRisk = (risksRaw?.data ?? risksRaw ?? {}) as Record<string, unknown>
  const riskMetrics = [
    ["Hệ số Sharpe", n(summaryRisk.sharpe)],
    ["Alpha", n(summaryRisk.alpha)],
    ["Beta", n(summaryRisk.beta)],
    ["Sụt giảm tối đa", n(summaryRisk.maxDrawdown)],
  ] as const
  const todayReturn = n(summary.todayReturn ?? summary.dayChangePct)
  const totalReturn = n(summary.totalReturnPct ?? summary.returnPct)
  const liveOrders = list(ordersRaw)
  if (resource.loading || resource.error || !resource.data) return <Page title="Danh mục"><DataState loading={resource.loading} error={resource.error} empty={!resource.loading && !resource.data} retry={() => void resource.reload()}><></></DataState></Page>
  return (
    <Page
      title="Danh mục đầu tư"
      sub="Độ chịu tải rủi ro · Phân bổ tài sản · Quản trị sụt giảm vốn"
      actions={<><Button variant="secondary">Xuất danh mục</Button><Link to="/trade"><Button variant="primary">Tái cân bằng</Button></Link></>}
    >
      <MetricStrip items={[
        { label: "NAV", value: displayNumber(n(summary.nav ?? summary.totalValue)), sub: <span className="text-muted">Giá trị tài sản ròng</span> },
        { label: "Hôm nay", value: todayReturn === null ? "—" : <PercentChange value={todayReturn} arrow={false} /> },
        { label: "Lợi nhuận", value: totalReturn === null ? "—" : <PercentChange value={totalReturn} arrow={false} /> },
        { label: "Tiền mặt", value: displayNumber(n(summary.cash ?? summary.cashBalance)) },
      ]} />

      <div className="grid grid-cols-1 lg:grid-cols-[1.5fr_1fr] gap-4 mt-4">
        <Panel>
          <PanelHead title="Lịch sử NAV danh mục" />
          <EquityCurve points={(perfRaw as { equityCurve?: { date: string; value: number }[] } | null)?.equityCurve} />
        </Panel>
        <Panel>
          <PanelHead title="Chỉ số rủi ro" sub="Tính từ lịch sử NAV khi có đủ dữ liệu" />
          <div className="grid grid-cols-2 gap-4">
            {riskMetrics.map(([label, value]) => <div key={label}><div className="text-[11px] text-muted">{label}</div><div className="mt-1 font-mono text-[15px] text-ink">{value === null ? "—" : label === "Sụt giảm tối đa" || label === "Alpha" ? `${value}%` : value.toFixed(2)}</div></div>)}
          </div>
          {riskMetrics.every(([, value]) => value === null) && <p className="mt-4 text-[12px] text-muted">Chưa đủ lịch sử NAV ngày để tính các chỉ số rủi ro.</p>}
        </Panel>
      </div>

      <Panel className="mt-4" flush>
        <div className="p-5 pb-3"><PanelHead title="Vị thế nắm giữ" sub={`${positions.length} mã đang nắm giữ · Chỉ số phân bổ thiếu dữ liệu hiển thị —`} /></div>
        <div className="overflow-x-auto">
          <table className="w-full text-[13px] min-w-[900px]">
            <thead><tr className="text-[11px] uppercase tracking-wide text-muted border-y border-line">
              {["Mã CP", "Giá vốn", "Thị giá", "Tỷ trọng", "Lãi/Lỗ", "Tỷ trọng chuẩn", "¼ Kelly", "Rủi ro", "Chặn lỗ"].map((h, i) => (
                <th key={h} className={`font-medium py-2.5 ${i === 0 ? "text-left pl-5" : i >= 1 && i <= 6 ? "text-right px-3" : "text-left px-3"} ${i === 8 ? "pr-5" : ""}`}>{h}</th>
              ))}
            </tr></thead>
            <tbody className="divide-y divide-line">
              {positions.map((p) => (
                <tr key={p.symbol} className="hover:bg-soft/50 transition-colors">
                  <td className="py-3 pl-5"><Link to={`/stock/${p.symbol}`} className="font-mono font-medium text-ink hover:underline">{p.symbol}</Link></td>
                  <td className="text-right px-3 tnum font-mono text-secondary">{displayNumber(p.entry)}</td>
                  <td className="text-right px-3 tnum font-mono text-ink">{displayNumber(p.current)}</td>
                  <td className="text-right px-3 tnum font-mono text-ink">{displayPercent(p.weight)}</td>
                  <td className="text-right px-3">{p.pnl === null ? "—" : <PercentChange value={p.pnl} arrow={false} />}</td>
                  <td className="text-right px-3 tnum font-mono text-secondary">{displayPercent(p.recWeight)}</td>
                  <td className="text-right px-3 tnum font-mono text-mineral">{displayPercent(p.kelly)}</td>
                  <td className="px-3">{p.risk ? <RiskLabel risk={p.risk} /> : "—"}</td>
                  <td className="pr-5 tnum font-mono text-loss">{displayNumber(p.stop)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      <Panel className="mt-4">
        <PanelHead title="Lịch sử lệnh" sub="Các lệnh giao dịch của tài khoản" />
        <div className="overflow-x-auto">
          <table className="w-full min-w-[700px] text-[13px]">
            <thead><tr className="border-b border-line text-left text-[10px] font-medium uppercase tracking-wide text-muted">{["Mã", "Chiều", "Khối lượng", "Giá", "Trạng thái", "Thời gian"].map(h => <th className="pb-2.5" key={h}>{h}</th>)}</tr></thead>
            <tbody className="divide-y divide-line">{liveOrders.map(order => <tr key={String(order.id)}><td className="py-3 font-mono font-semibold text-ink">{String(order.symbol ?? order.ticker)}</td><td>{String(order.side ?? "—")}</td><td className="font-mono">{String(order.quantity ?? "—")}</td><td className="font-mono">{String(order.limitPrice ?? order.price ?? "—")}</td><td>{String(order.status ?? "—")}</td><td className="font-mono text-muted">{String(order.createdAt ?? order.date ?? "—")}</td></tr>)}</tbody>
          </table>
          {!liveOrders.length && <p className="py-6 text-center text-[13px] text-muted">Chưa có lệnh giao dịch.</p>}
        </div>
      </Panel>
    </Page>
  )
}
