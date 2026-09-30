"use client"

import { useEffect, useState } from "react"
import { Page } from "@/components/Shell"
import { Button, MetricStrip, Panel, PanelHead, Pill, fmt } from "@/components/ui"
import { aiApi } from "@/lib/api"

const todayLocal = () => {
  const date = new Date()
  date.setMinutes(date.getMinutes() - date.getTimezoneOffset())
  return date.toISOString().slice(0, 10)
}

interface EquityPoint { date: string; equity: number }
interface BacktestTrade { date?: string; side?: string; price?: number; quantity?: number }
interface BacktestResult {
  run_id?: string
  status?: string
  metrics?: Record<string, number | null | undefined>
  equity_curve?: EquityPoint[]
  trades?: BacktestTrade[]
}

const numberFrom = (value: string) => Number(value.replace(/[,.\s]/g, ""))
const money = (value: unknown) => Number.isFinite(Number(value)) ? `${fmt(Number(value))} ₫` : "—"

export default function Backtest() {
  const [symbol, setSymbol] = useState("FPT")
  const [strategy, setStrategy] = useState("sma_cross")
  const [startDate, setStartDate] = useState("2024-01-01")
  const [endDate, setEndDate] = useState(todayLocal)
  const [capital, setCapital] = useState("1000000000")
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState(false)
  const [historyRuns, setHistoryRuns] = useState<string[]>([])
  const [selectedRun, setSelectedRun] = useState("")
  const [result, setResult] = useState<BacktestResult | null>(null)
  const [message, setMessage] = useState("")

  const loadHistory = async () => {
    setLoading(true)
    try {
      const response = await aiApi.backtestHistory()
      const runs = Array.isArray(response?.runs) ? response.runs.map(String) : []
      setHistoryRuns(runs)
      if (selectedRun && !runs.includes(selectedRun)) {
        setSelectedRun("")
        setResult(null)
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Không tải được lịch sử backtest.")
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    // Load only the backtest run index; portfolio and ML Fund data are separate products.
    // eslint-disable-next-line react-hooks/set-state-in-effect -- loadHistory updates state after its asynchronous request settles.
    void loadHistory()
  }, [])

  const loadRun = async (runId: string) => {
    setSelectedRun(runId)
    setMessage("")
    if (!runId) {
      setResult(null)
      return
    }
    setResult(null)
    setLoading(true)
    try {
      const response = await aiApi.backtestResults(runId)
      if (response?.status !== "completed") {
        setResult(null)
        setMessage(response?.status === "not_found" ? "Không tìm thấy kết quả của lượt chạy này." : `Lượt chạy hiện có trạng thái: ${response?.status ?? "không xác định"}.`)
        return
      }
      setResult(response)
    } catch (error) {
      setResult(null)
      setMessage(error instanceof Error ? error.message : "Không tải được kết quả backtest.")
    } finally {
      setLoading(false)
    }
  }

  const runBacktest = async () => {
    const cleanSymbol = symbol.trim().toUpperCase()
    const initialCapital = numberFrom(capital)
    if (!/^[A-Z0-9.-]{1,16}$/.test(cleanSymbol)) {
      setMessage("Nhập mã cổ phiếu gồm 1–16 ký tự chữ, số, dấu chấm hoặc gạch ngang.")
      return
    }
    if (startDate >= endDate) {
      setMessage("Ngày bắt đầu phải trước ngày kết thúc.")
      return
    }
    if (!Number.isFinite(initialCapital) || initialCapital <= 0) {
      setMessage("Vốn ban đầu phải lớn hơn 0.")
      return
    }

    setRunning(true)
    setResult(null)
    setSelectedRun("")
    setMessage("Đang chạy backtest…")
    try {
      const response = await aiApi.backtest({
        symbol: cleanSymbol,
        strategy,
        startDate,
        endDate,
        params: {},
        capital: initialCapital,
      })
      if (response?.status !== "success") {
        setMessage(response?.logs || "Backtest không hoàn tất thành công.")
        return
      }
      setResult(response)
      setSelectedRun(String(response.run_id ?? ""))
      setMessage(`Hoàn tất · ${response.metrics?.total_trades ?? response.trades?.length ?? 0} giao dịch`)
      const historyResponse = await aiApi.backtestHistory().catch(() => null)
      if (Array.isArray(historyResponse?.runs)) setHistoryRuns(historyResponse.runs.map(String))
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Không thể chạy backtest.")
    } finally {
      setRunning(false)
    }
  }

  const curve = (result?.equity_curve ?? []).filter(point => Number.isFinite(Number(point.equity)))
  const trades = result?.trades ?? []
  const metrics = result?.metrics ?? {}
  const chartWidth = 560, chartHeight = 180, padding = 20
  const values = curve.map(point => Number(point.equity))
  const minVal = values.length ? Math.min(...values) * 0.98 : 0
  const maxVal = values.length ? Math.max(...values) * 1.02 : 1
  const range = maxVal - minVal || 1
  const points = curve.map((point, index) => {
    const x = padding + index / Math.max(curve.length - 1, 1) * (chartWidth - padding * 2)
    const y = chartHeight - padding - ((Number(point.equity) - minVal) / range) * (chartHeight - padding * 2)
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(" ")

  return (
    <Page
      title="Thử nghiệm Backtest"
      sub="Kết quả chỉ hiển thị từ các lượt chạy của bộ máy backtest."
      actions={<>
        <Button variant="secondary" onClick={() => void loadHistory()} disabled={loading || running}>Làm mới</Button>
        <Button variant="primary" onClick={() => void runBacktest()} disabled={running || loading}>{running ? "Đang chạy…" : "Chạy backtest"}</Button>
      </>}
    >
      {message && <div role="status" className="mb-4 rounded-lg border border-line bg-surface px-4 py-3 text-sm text-secondary">{message}</div>}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[300px_minmax(0,1fr)]">
        <Panel className="h-fit">
          <PanelHead title="Cấu hình chiến lược" />
          <div className="space-y-3 p-1 text-[12px]">
            <label className="block text-secondary">Mã cổ phiếu
              <input value={symbol} onChange={event => setSymbol(event.target.value)} maxLength={16} className="mt-1 h-9 w-full rounded-md border border-line bg-paper px-2.5 font-mono text-ink" />
            </label>
            <label className="block text-secondary">Chiến lược
              <select value={strategy} onChange={event => setStrategy(event.target.value)} className="mt-1 h-9 w-full rounded-md border border-line bg-paper px-2 font-mono text-ink">
                <option value="sma_cross">Giao cắt trung bình động</option>
                <option value="rsi">Đảo chiều RSI</option>
                <option value="bollinger">Dải Bollinger</option>
              </select>
            </label>
            <div className="grid grid-cols-2 gap-2">
              <label className="text-secondary">Ngày bắt đầu<input type="date" value={startDate} onChange={event => setStartDate(event.target.value)} className="mt-1 h-9 w-full rounded-md border border-line bg-paper px-2 font-mono text-[11px] text-ink" /></label>
              <label className="text-secondary">Ngày kết thúc<input type="date" value={endDate} onChange={event => setEndDate(event.target.value)} className="mt-1 h-9 w-full rounded-md border border-line bg-paper px-2 font-mono text-[11px] text-ink" /></label>
            </div>
            <label className="block text-secondary">Vốn ban đầu (VNĐ)
              <input inputMode="numeric" value={capital} onChange={event => setCapital(event.target.value)} className="mt-1 h-9 w-full rounded-md border border-line bg-paper px-2.5 font-mono text-ink" />
            </label>
            <p className="rounded-md bg-soft px-3 py-2 text-[11px] leading-relaxed text-secondary">Phí, thuế và cách khớp lệnh do bộ máy backtest trả về; màn hình không giả định các thông số chưa có trong kết quả.</p>
            <Button variant="primary" className="w-full" onClick={() => void runBacktest()} disabled={running || loading}>{running ? "Đang mô phỏng…" : "Bắt đầu mô phỏng"}</Button>
          </div>
        </Panel>

        <div className="min-w-0 space-y-4">
          <Panel>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <PanelHead title="Kết quả backtest" sub={result?.run_id ? `Mã lượt chạy: ${result.run_id}` : "Chưa chọn hoặc chạy backtest."} />
              <label className="text-xs text-secondary">Lịch sử
                <select value={selectedRun} onChange={event => void loadRun(event.target.value)} disabled={loading || running} className="ml-2 max-w-[280px] rounded-md border border-line bg-paper px-2 py-1.5 font-mono text-[11px] text-ink">
                  <option value="">Lượt hiện tại</option>
                  {historyRuns.map(run => <option key={run} value={run}>{run}</option>)}
                </select>
              </label>
            </div>
            <MetricStrip items={[
              { label: "CAGR", value: metrics.cagr == null ? "—" : <span className={Number(metrics.cagr) >= 0 ? "text-gain" : "text-loss"}>{(Number(metrics.cagr) * 100).toLocaleString("vi-VN", { maximumFractionDigits: 2 })}%</span> },
              { label: "Sharpe", value: metrics.sharpe_ratio == null ? "—" : Number(metrics.sharpe_ratio).toFixed(2) },
              { label: "Sụt giảm tối đa", value: metrics.max_drawdown == null ? "—" : `${(Number(metrics.max_drawdown) * 100).toLocaleString("vi-VN", { maximumFractionDigits: 2 })}%` },
              { label: "Giao dịch", value: metrics.total_trades ?? trades.length },
            ]} />
            {curve.length > 1 ? <div className="mt-4 h-56 rounded-lg border border-line bg-paper p-2">
              <svg className="h-[calc(100%-24px)] w-full" viewBox={`0 0 ${chartWidth} ${chartHeight}`} preserveAspectRatio="none"><polyline fill="none" stroke="var(--color-gain)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" points={points} /></svg>
              <div className="flex justify-between px-2 text-[10px] font-mono text-muted"><span>{curve[0].date}</span><span>{curve[curve.length - 1].date}</span></div>
            </div> : <div className="mt-4 grid h-48 place-items-center rounded-lg border border-dashed border-line bg-paper px-6 text-center text-sm text-muted">{loading ? "Đang tải kết quả…" : "Chạy backtest hoặc chọn một lượt đã lưu để xem đường tài sản."}</div>}
            <dl className="mt-4 grid grid-cols-2 gap-4 text-sm md:grid-cols-3">
              <div><dt className="text-xs text-muted">NAV cuối kỳ</dt><dd className="mt-1 font-mono text-ink">{metrics.ending_equity == null ? "—" : money(metrics.ending_equity)}</dd></div>
              <div><dt className="text-xs text-muted">Chi phí giao dịch</dt><dd className="mt-1 font-mono text-ink">{metrics.total_costs == null ? "—" : money(metrics.total_costs)}</dd></div>
              <div><dt className="text-xs text-muted">Lượt chạy</dt><dd className="mt-1 font-mono text-ink">{result?.status ?? "—"}</dd></div>
            </dl>
          </Panel>

          <Panel>
            <PanelHead title="Giao dịch mô phỏng" sub={`${trades.length} giao dịch trong lượt chạy đang chọn`} />
            <div className="overflow-x-auto">
              <table className="w-full min-w-[560px] text-left text-[12px] font-mono">
                <thead className="border-b border-line text-muted"><tr><th className="px-3 py-2">Ngày</th><th className="px-3 py-2">Chiều</th><th className="px-3 py-2 text-right">Giá</th><th className="px-3 py-2 text-right">Khối lượng</th></tr></thead>
                <tbody className="divide-y divide-line">{trades.slice(0, 200).map((trade, index) => <tr key={`${trade.date}-${index}`}>
                  <td className="px-3 py-2 text-secondary">{trade.date ? new Date(trade.date).toLocaleDateString("vi-VN") : "—"}</td>
                  <td className="px-3 py-2"><Pill tone={String(trade.side).toLowerCase() === "buy" ? "gain" : "loss"}>{trade.side ?? "—"}</Pill></td>
                  <td className="px-3 py-2 text-right">{trade.price == null ? "—" : money(trade.price)}</td>
                  <td className="px-3 py-2 text-right">{trade.quantity == null ? "—" : Number(trade.quantity).toLocaleString("vi-VN")}</td>
                </tr>)}
                {!trades.length && <tr><td colSpan={4} className="py-8 text-center text-sm text-muted">Chưa có giao dịch trong kết quả này.</td></tr>}</tbody>
              </table>
              {trades.length > 200 && <p className="pt-3 text-center text-xs text-muted">Đang hiển thị 200 trên {trades.length} giao dịch.</p>}
            </div>
          </Panel>
        </div>
      </div>
    </Page>
  )
}
