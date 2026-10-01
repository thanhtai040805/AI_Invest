"use client"

import { Page } from "@/components/Shell"
import { Link } from "@/lib/router"
import { portfolioApi } from "@/lib/api"
import { usePortfolio, type PortfolioSnapshot } from "@/lib/use-portfolio"
import { useResource } from "@/lib/api/use-resource"
import { DataState } from "@/components/data-state"
import { Button, MetricStrip, Panel, PanelHead, PercentChange, fmt } from "@/components/ui"
import { NavLineChart } from "@/components/NavLineChart"

type PositionRow = { symbol: string; quantity: number | null; entry: number | null; current: number | null; marketValue: number | null; weight: number | null; pnl: number | null; pnlPercent: number | null; priceAsOf: string | null; stale: boolean }
const n = (value: unknown): number | null => value === null || value === undefined || value === "" || !Number.isFinite(Number(value)) ? null : Number(value)
const displayNumber = (value: number | null) => value === null ? "—" : fmt(value)
const displayPercent = (value: number | null) => value === null ? "—" : `${value.toLocaleString("vi-VN", { maximumFractionDigits: 2 })}%`

export default function Portfolio() {
  const resource = useResource<PortfolioSnapshot>(() => portfolioApi.snapshot(), [])
  const snapshot = usePortfolio(resource.data, resource.reload)
  const summary = snapshot?.summary
  const positions: PositionRow[] = (snapshot?.positions ?? []).map(row => ({ symbol: row.symbol, quantity: row.quantity, entry: row.costBasis !== null && row.quantity > 0 ? row.costBasis / row.quantity : null, current: row.currentPrice, marketValue: row.marketValue, weight: row.weight, pnl: row.pnl, pnlPercent: row.pnlPercent, priceAsOf: row.priceAsOf, stale: row.stale }))
  const summaryRisk = snapshot?.risks
  const riskMetrics = [
    ["Hệ số Sharpe", n(summaryRisk?.sharpe)],
    ["Alpha", n(summaryRisk?.alpha)],
    ["Beta", n(summaryRisk?.beta)],
    ["Sụt giảm tối đa", n(summaryRisk?.maxDrawdown)],
  ] as const
  const todayReturn = n(summary?.dailyPnLPercent)
  const totalReturn = n(summary?.totalReturnPct)
  const liveOrders = snapshot?.orders ?? []
  if ((!resource.data && resource.loading) || (!resource.data && resource.error) || !snapshot || !summary) return <Page title="Danh mục"><DataState loading={resource.loading} error={resource.error} empty={!resource.loading && !resource.data} retry={() => void resource.reload()}><></></DataState></Page>
  return (
    <Page
      title="Danh mục đầu tư"
      sub={`Tài khoản ${summary.accountId} · Giá cập nhật theo thị trường`}
      actions={<div className="flex gap-2"><Button onClick={() => void resource.reload()}>Làm mới</Button><Link to="/trade"><Button variant="primary">Đặt lệnh</Button></Link></div>}
    >
      <MetricStrip items={[
        { label: "NAV", value: displayNumber(n(summary.nav)), sub: <span className="text-muted">Giá trị tài sản ròng</span> },
        { label: "Hôm nay", value: todayReturn === null ? "—" : <PercentChange value={todayReturn} arrow={false} /> },
        { label: "Lợi nhuận tài khoản", value: totalReturn === null ? "—" : <PercentChange value={totalReturn} arrow={false} /> },
        { label: "Tiền mặt", value: displayNumber(n(summary.cash)) },
      ]} />

      <MetricStrip items={[
        { label: "Lãi/lỗ đã chốt", value: displayNumber(summary.realizedPnl), sub: "Sau phí và thuế" },
        { label: "Lãi/lỗ chưa chốt", value: displayNumber(summary.unrealizedPnl), sub: "Giá vốn gồm phí mua" },
        { label: "Tổng lãi/lỗ", value: displayNumber(summary.totalPnl), sub: "Đã chốt + chưa chốt" },
      ]} />
      {!summary.ledgerComplete && <p role="status" className="mt-3 text-sm text-warning">Sổ khớp lệnh chưa đối soát đủ với vị thế. Lãi/lỗ sau phí chưa xác định.</p>}
      {!!summary.stalePrices.length && <p role="status" className="mt-3 text-sm text-muted">Giá gần nhất hoặc giá đóng cửa: {summary.stalePrices.join(", ")}. Xem thời điểm giá từng mã.</p>}
      {resource.error && <p role="status" className="mt-3 text-sm text-warning">Chưa làm mới được trạng thái tài khoản. Đang hiển thị dữ liệu lần tải gần nhất.</p>}
      <div className="grid grid-cols-1 lg:grid-cols-[1.5fr_1fr] gap-4 mt-4">
        <Panel>
          <PanelHead title="Lịch sử NAV danh mục" sub={`NAV cuối ngày · ${snapshot.performance.equityCurve.length} điểm · cập nhật đến ${snapshot.performance.asOf ?? "chưa có dữ liệu"}`} />
          <NavLineChart points={snapshot.performance.equityCurve} />
        </Panel>
        <Panel>
          <PanelHead title="Chỉ số rủi ro" sub="Tính từ lịch sử NAV khi có đủ dữ liệu" />
          <div className="grid grid-cols-2 gap-4">
            {riskMetrics.map(([label, value]) => <div key={label}><div className="text-[11px] text-muted">{label}</div><div className="mt-1 font-mono text-[15px] text-ink">{value === null ? "—" : label === "Sụt giảm tối đa" || label === "Alpha" ? `${value}%` : value.toFixed(2)}</div></div>)}
          </div>
          <p className="mt-4 text-[12px] text-muted">{summaryRisk?.message}</p>
        </Panel>
      </div>

      <Panel className="mt-4" flush>
        <div className="p-5 pb-3"><PanelHead title="Vị thế nắm giữ" sub={`${positions.length} mã · Lãi/lỗ sau phí mua; chưa trừ phí bán dự kiến`} /></div>
        <div className="overflow-x-auto">
          <table className="w-full text-[13px] min-w-[900px]">
            <thead><tr className="text-[11px] uppercase tracking-wide text-muted border-y border-line">
              {["Mã CP", "Số lượng", "Giá vốn gồm phí", "Thị giá", "Giá trị thị trường", "Tỷ trọng NAV", "Lãi/Lỗ (₫)", "Lãi/Lỗ (%)"].map((h, i) => (
                <th key={h} className={`font-medium py-2.5 ${i === 0 ? "text-left pl-5" : "text-right px-3"} ${i === 7 ? "pr-5" : ""}`}>{h}</th>
              ))}
            </tr></thead>
            <tbody className="divide-y divide-line">
              {positions.map((p) => (
                <tr key={p.symbol} className="hover:bg-soft/50 transition-colors">
                  <td className="py-3 pl-5"><Link to={`/stock/${p.symbol}`} className="font-mono font-medium text-ink hover:underline">{p.symbol}</Link></td>
                  <td className="text-right px-3 tnum font-mono text-secondary">{p.quantity === null ? "—" : p.quantity.toLocaleString("vi-VN")}</td>
                  <td className="text-right px-3 tnum font-mono text-secondary">{displayNumber(p.entry)}</td>
                  <td className="text-right px-3 tnum font-mono text-ink">{displayNumber(p.current)}<div className="mt-1 text-[10px] text-muted">{p.priceAsOf ? new Date(p.priceAsOf).toLocaleString("vi-VN", { timeZone: "Asia/Ho_Chi_Minh" }) : "Thiếu giá"}{p.stale ? " · Gần nhất" : ""}</div></td>
                  <td className="text-right px-3 tnum font-mono text-ink">{displayNumber(p.marketValue)}</td>
                  <td className="text-right px-3 tnum font-mono text-ink">{displayPercent(p.weight)}</td>
                  <td className={`text-right px-3 tnum font-mono ${p.pnl === null ? "text-muted" : p.pnl >= 0 ? "text-gain" : "text-loss"}`}>{p.pnl === null ? "—" : `${p.pnl >= 0 ? "+" : ""}${fmt(p.pnl)} ₫`}</td>
                  <td className="pr-5 text-right">{p.pnlPercent === null ? "—" : <PercentChange value={p.pnlPercent} arrow={false} />}</td>
                </tr>
              ))}
              {!positions.length && <tr><td colSpan={8} className="py-8 text-center text-[13px] text-muted">Chưa có vị thế trong tài khoản này.</td></tr>}
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
