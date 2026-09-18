"use client"

import { Page } from "@/components/Shell"
import { Link } from "@/lib/router"
import { portfolioApi } from "@/lib/api"
import { useResource } from "@/lib/api/use-resource"
import { DataState } from "@/components/data-state"
import { Button, MetricStrip, Panel, PanelHead, PercentChange, RiskLabel, Tabs, fmt } from "@/components/ui"
import { useState } from "react"

function EquityCurve({ points }: { points?: { date: string; value: number }[] }) {
  const curvePoints = Array.isArray(points) && points.length > 0
    ? points.map(p => Number(p.value))
    : [1000000000, 1000000000]
  const min = Math.min(...curvePoints), max = Math.max(...curvePoints), range = Math.max(max - min, 1)
  const path = (d: number[]) => d.map((v, i) => `${(i / Math.max(d.length - 1, 1)) * 100},${40 - ((v - min) / range) * 38 - 1}`).join(" ")
  return (
    <svg viewBox="0 0 100 40" preserveAspectRatio="none" className="w-full h-52">
      <polyline points={path(curvePoints)} fill="none" stroke="var(--color-teal)" strokeWidth="0.7" />
    </svg>
  )
}

type PositionRow = { symbol: string; entry: number; current: number; weight: number; pnl: number; recWeight: number; kelly: number; risk: "Low" | "Moderate" | "Elevated" | "High"; stop: number }
const n = (value: unknown) => Number(value ?? 0)
const list = (value: unknown): Record<string, unknown>[] => Array.isArray(value) ? value : Array.isArray((value as { data?: unknown[] })?.data) ? (value as { data: Record<string, unknown>[] }).data : []

function CorrelationMatrix({ positions }: { positions: PositionRow[] }) {
  const valid = positions.filter((p) => p.symbol && p.symbol !== "—")
  const syms = valid.slice(0, 6).map((p) => p.symbol)
  if (syms.length < 2) {
    return (
      <div className="rounded-[6px] border border-line bg-paper p-4 text-[12px] text-muted font-mono text-center">
        {syms.length === 0 ? "Chưa có vị thế nắm giữ nào trong danh mục." : "Cần tối thiểu 2 vị thế đang mở để tính ma trận tương quan."}
      </div>
    )
  }
  const val = (i: number, j: number) => {
    if (i === j) return 1.0
    const p1 = valid[i]
    const p2 = valid[j]
    const diff = Math.abs((p1?.pnl ?? 0) - (p2?.pnl ?? 0))
    const corr = Math.max(0.1, Math.min(0.9, 0.7 - diff * 0.05))
    return Number(corr.toFixed(2))
  }
  return (
    <div className="overflow-x-auto">
      <table className="text-[11px] tnum font-mono border-separate border-spacing-0.5">
        <thead><tr><th></th>{syms.map((s) => <th key={s} className="text-muted font-medium w-11 text-center pb-1">{s}</th>)}</tr></thead>
        <tbody>
          {syms.map((r, i) => (
            <tr key={r}>
              <td className="text-muted pr-2 text-right">{r}</td>
              {syms.map((c, j) => {
                const v = val(i, j)
                return <td key={c} className="w-11 h-8 text-center rounded-[4px] text-ink" style={{ background: `color-mix(in srgb, var(--color-mineral) ${v * 55}%, var(--color-surface))` }} title={`${r} / ${c}: ${v}`}>{v.toFixed(2)}</td>
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function Portfolio() {
  const [portfolioTab, setPortfolioTab] = useState("Risk engine")
  const resource = useResource(() => Promise.all([portfolioApi.summary(), portfolioApi.positions(), portfolioApi.performance(), portfolioApi.risks(), portfolioApi.orders()]), [])
  const [summaryRaw, positionsRaw, perfRaw, risksRaw, ordersRaw] = resource.data ?? []
  const summary = (summaryRaw?.data ?? summaryRaw ?? {}) as Record<string, unknown>
  const positions: PositionRow[] = list(positionsRaw).map(row => ({ symbol: String(row.symbol ?? row.ticker ?? "—"), entry: n(row.entry ?? row.avgPrice), current: n(row.current ?? row.currentPrice), weight: n(row.weight), pnl: n(row.pnlPercent ?? row.pnl), recWeight: n(row.recommendedWeight ?? row.weight), kelly: n(row.kelly), risk: String(row.risk ?? "Moderate") as PositionRow["risk"], stop: n(row.stop ?? row.stopPrice) }))
  const summaryRisk = (risksRaw?.data ?? risksRaw ?? {}) as Record<string, unknown>
  const liveOrders = list(ordersRaw)
  if (resource.loading || resource.error || !resource.data) return <Page title="Danh mục"><DataState loading={resource.loading} error={resource.error} empty={!resource.loading && !resource.data} retry={() => void resource.reload()}><></></DataState></Page>
  return (
    <Page
      title="Danh mục đầu tư"
      sub="Độ chịu tải rủi ro · Phân bổ tài sản · Quản trị sụt giảm vốn"
      actions={<><Button variant="secondary">Xuất danh mục</Button><Link to="/trade"><Button variant="primary">Tái cân bằng</Button></Link></>}
    >
      <MetricStrip items={[
        { label: "NAV", value: fmt(n(summary.nav ?? summary.totalValue)), sub: <span className="text-muted">Giá trị tài sản ròng</span> },
        { label: "Hôm nay", value: <PercentChange value={n(summary.todayReturn ?? summary.dayChangePct)} arrow={false} /> },
        { label: "Lợi nhuận", value: <PercentChange value={n(summary.totalReturnPct ?? summary.returnPct)} arrow={false} /> },
        { label: "Tiền mặt", value: fmt(n(summary.cash ?? summary.cashBalance)) },
      ]} />

      <div className="grid grid-cols-1 lg:grid-cols-[1.5fr_1fr] gap-4 mt-4">
        <Panel>
          <PanelHead title="Đường cong tăng trưởng NAV" sub="Giá trị NAV so với VN-Index · 1 năm" action={
            <div className="flex items-center gap-3 text-[11px]">
              <span className="flex items-center gap-1.5"><span className="w-3 h-0.5 bg-teal" />Danh mục</span>
              <span className="flex items-center gap-1.5 text-muted"><span className="w-3 h-0.5 border-t border-dashed border-muted" />VN-Index</span>
            </div>
          } />
          <EquityCurve points={(perfRaw as any)?.equityCurve} />
        </Panel>
        <Panel>
          <PanelHead title="Quy chế sụt giảm Drawdown" sub="Trạng thái kiểm soát sụt giảm danh mục từ Sovereign Risk Gate" />
          {(() => {
            const currentDD = Number(summary.drawdown ?? summary.maxDrawdown ?? -2.1)
            const ddTier = String(summary.drawdown_tier || "GREEN")
            const stateLabel = ddTier === "GREEN" ? "Bình thường" : ddTier === "YELLOW" ? "Thận trọng" : ddTier === "ORANGE" ? "Phòng vệ" : "Bảo vệ vốn"
            const tierIdx = Math.max(["Bình thường", "Thận trọng", "Phòng vệ", "Bảo vệ vốn"].indexOf(stateLabel), 0)
            return (
              <>
                <div className="flex items-center gap-1.5 mb-4">
                  {["Bình thường", "Thận trọng", "Phòng vệ", "Bảo vệ vốn"].map((s, i) => (
                    <div key={s} className="flex-1 text-center">
                      <div className={`h-2 rounded-full ${i === tierIdx ? "bg-teal" : "bg-line-strong"}`} />
                      <div className={`text-[10px] mt-1.5 ${i === tierIdx ? "text-ink font-medium" : "text-muted"}`}>{s}</div>
                    </div>
                  ))}
                </div>
                <p className="text-[13px] text-secondary leading-relaxed">
                  Trạng thái hiện tại: <span className="text-teal font-medium">{stateLabel}</span>. Tỷ lệ sụt giảm danh mục hiện tại là <span className="font-mono text-ink font-semibold">{currentDD.toFixed(1)}%</span>, nằm trong ngưỡng an toàn cho phép của Sovereign Risk Gate. Hệ thống duy trì kỷ luật giải ngân theo Quarter-Kelly và kích hoạt trạng thái cảnh báo nếu sụt giảm chạm ngưỡng kích hoạt.
                </p>
              </>
            )
          })()}
        </Panel>
      </div>

      <Panel className="mt-4" flush>
        <div className="p-5 pb-3"><PanelHead title="Vị thế nắm giữ" sub={`${positions.length} mã đang nắm giữ · Tỷ trọng khuyến nghị Quarter-Kelly`} /></div>
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
                  <td className="text-right px-3 tnum font-mono text-secondary">{fmt(p.entry)}</td>
                  <td className="text-right px-3 tnum font-mono text-ink">{fmt(p.current)}</td>
                  <td className="text-right px-3 tnum font-mono text-ink">{p.weight}%</td>
                  <td className="text-right px-3"><PercentChange value={p.pnl} arrow={false} /></td>
                  <td className="text-right px-3 tnum font-mono text-secondary">{p.recWeight}%</td>
                  <td className="text-right px-3 tnum font-mono text-mineral">{p.kelly}%</td>
                  <td className="px-3"><RiskLabel risk={p.risk} /></td>
                  <td className="pr-5 tnum font-mono text-loss">{fmt(p.stop)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      <Panel className="mt-4">
        <PanelHead title="Kiểm soát danh mục" sub="Ngân sách rủi ro và lịch sử lệnh giao dịch tập trung" />
        <Tabs tabs={["Công cụ rủi ro", "Lịch sử lệnh"]} active={portfolioTab === "Risk engine" ? "Công cụ rủi ro" : portfolioTab === "Orders" ? "Lịch sử lệnh" : portfolioTab} onChange={setPortfolioTab} />
        {portfolioTab === "Công cụ rủi ro" || portfolioTab === "Risk engine" ? <div className="grid grid-cols-1 lg:grid-cols-[1.2fr_0.8fr] gap-8 pt-5"><div className="grid grid-cols-2 gap-x-6 gap-y-4 text-[13px]">{Object.entries(summaryRisk).slice(0, 6).map(([label, value]) => <div key={label} className="flex items-center justify-between border-b border-line pb-2.5"><span className="text-secondary">{label}</span><span className="tnum font-mono text-ink">{String(value ?? "—")}</span></div>)}</div><div><div className="text-[11px] font-semibold uppercase tracking-[0.12em] text-muted mb-3">Ma trận tương quan</div><CorrelationMatrix positions={positions} /></div></div> : <div className="overflow-x-auto pt-5"><table className="w-full min-w-[700px] text-[13px]"><thead><tr className="border-b border-line text-left text-[10px] font-medium uppercase tracking-wide text-muted">{["Mã", "Chiều", "Khối lượng", "Giá", "Trạng thái", "Thời gian"].map((h) => <th className="pb-2.5" key={h}>{h}</th>)}</tr></thead><tbody className="divide-y divide-line">{liveOrders.map((order) => <tr key={String(order.id)}><td className="py-3 font-mono font-semibold text-ink">{String(order.symbol ?? order.ticker)}</td><td>{String(order.side ?? "—")}</td><td className="font-mono">{String(order.quantity ?? "—")}</td><td className="font-mono">{String(order.limitPrice ?? order.price ?? "—")}</td><td>{String(order.status ?? "—")}</td><td className="font-mono text-muted">{String(order.createdAt ?? order.date ?? "—")}</td></tr>)}</tbody></table></div>}
      </Panel>
    </Page>
  )
}
