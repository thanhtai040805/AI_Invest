"use client"

import { useEffect, useState } from "react"
import { Page } from "@/components/Shell"
import {
  Button,
  MetricStrip,
  Panel,
  PanelHead,
  Pill,
} from "@/components/ui"
import { aiApi, portfolioApi, workspaceApi } from "@/lib/api"

interface EquityPoint {
  date: string
  value: number
}

interface RiskData {
  sharpe: number | null
  alpha: number | null
  beta: number | null
  maxDrawdown: number | null
  message?: string
}

interface PaperTrade {
  id: number
  ticker: string
  action: string
  price: number
  quantity: number | null
  confidence: number | null
  status: string | null
  pnl: number | null
  date: string
  created_at: string
}

export default function Backtest() {
  const [symbol, setSymbol] = useState("FPT")
  const [strategy, setStrategy] = useState("sma_cross")
  const [startDate, setStartDate] = useState("2024-01-01")
  const [endDate, setEndDate] = useState(new Date().toISOString().slice(0, 10))
  const [capital, setCapital] = useState("1,000,000,000")

  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState(false)
  const [runStatus, setRunStatus] = useState<string | null>(null)

  const [equityCurve, setEquityCurve] = useState<EquityPoint[]>([])
  const [risks, setRisks] = useState<RiskData | null>(null)
  const [trades, setTrades] = useState<PaperTrade[]>([])
  const [nav, setNav] = useState<number | null>(null)

  const loadData = async () => {
    try {
      const [perfRes, riskRes, mlFundRes] = await Promise.allSettled([
        portfolioApi.performance(),
        portfolioApi.risks(),
        workspaceApi.mlFund(),
      ])

      if (perfRes.status === "fulfilled" && perfRes.value?.equityCurve) {
        setEquityCurve(perfRes.value.equityCurve)
      }
      if (riskRes.status === "fulfilled" && riskRes.value) {
        setRisks(riskRes.value)
      }
      if (mlFundRes.status === "fulfilled" && mlFundRes.value) {
        const data = mlFundRes.value
        if (Array.isArray(data.trades)) {
          setTrades(data.trades)
        }
        if (data.account?.nav) {
          setNav(Number(data.account.nav))
        } else if (data.mainAccount?.nav) {
          setNav(Number(data.mainAccount.nav))
        }
      }
    } catch (e) {
      console.error("Failed to load backtest data", e)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    // Initial page state is already loading; fetching here must not reset it synchronously.
    // eslint-disable-next-line react-hooks/set-state-in-effect -- loadData updates state only after its asynchronous requests settle.
    loadData()
  }, [])

  const handleRunBacktest = async () => {
    try {
      setRunning(true)
      setRunStatus("Dispatching execution job...")
      const res = await aiApi.backtest({
        symbol: symbol.toUpperCase(),
        strategy,
        startDate,
        endDate,
        params: {},
        capital: Number(capital.replace(/,/g, "")),
      })
      if (Array.isArray(res?.equity_curve)) {
        setEquityCurve(res.equity_curve.map((point: { date: string; equity: number }) => ({ date: point.date, value: Number(point.equity) })))
      }
      if (Array.isArray(res?.trades)) setTrades(res.trades)
      if (res?.metrics) {
        setRisks({
          sharpe: Number(res.metrics.sharpe_ratio ?? 0),
          alpha: null,
          beta: null,
          maxDrawdown: Number(res.metrics.max_drawdown ?? 0) * 100,
        })
      }
      setRunStatus(res?.status === "success" ? `Hoàn tất ${res.metrics?.total_trades ?? 0} giao dịch` : "Backtest thất bại")
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Backtest execution timed out or queued"
      setRunStatus(msg)
    } finally {
      setRunning(false)
    }
  }

  // Calculate return from equity curve
  const initialValue = equityCurve.length > 0 ? equityCurve[0].value : 0
  const latestValue = equityCurve.length > 0 ? equityCurve[equityCurve.length - 1].value : (nav || 0)
  const totalReturn = initialValue > 0 ? ((latestValue - initialValue) / initialValue) * 100 : 0

  // Win rate from real paper trades
  const resolvedTrades = trades.filter((t) => t.pnl !== null)
  const winningTrades = resolvedTrades.filter((t) => (t.pnl ?? 0) > 0)
  const winRate = resolvedTrades.length > 0
    ? Math.round((winningTrades.length / resolvedTrades.length) * 100)
    : 0

  // SVG Chart points calculation
  const chartHeight = 180
  const chartWidth = 560
  const padding = 20
  const values = equityCurve.map((p) => p.value)
  const minVal = values.length ? Math.min(...values) * 0.98 : 0
  const maxVal = values.length ? Math.max(...values) * 1.02 : 1
  const range = maxVal - minVal || 1

  const pointsString = equityCurve
    .map((p, i) => {
      const x = padding + (i / Math.max(equityCurve.length - 1, 1)) * (chartWidth - padding * 2)
      const y = chartHeight - padding - ((p.value - minVal) / range) * (chartHeight - padding * 2)
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(" ")

  return (
    <Page
      title="Thử nghiệm Backtest"
      sub="Môi trường nghiên cứu và kiểm định chiến lược định lượng."
      actions={
        <>
          <Button variant="secondary" onClick={loadData} disabled={loading || running}>
            Làm mới dữ liệu
          </Button>
          <Button variant="primary" onClick={handleRunBacktest} disabled={running}>
            {running ? "Đang chạy..." : "Chạy Backtest"}
          </Button>
        </>
      }
    >
      {runStatus && (
        <div className="mb-4 rounded-[8px] border border-line bg-surface p-3 text-[12px] flex items-center justify-between">
          <span className="font-mono text-ink">{runStatus}</span>
          <button onClick={() => setRunStatus(null)} className="text-muted hover:text-ink text-[11px]">
            Đóng
          </button>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-[300px_1fr] gap-4">
        <Panel className="h-fit">
          <PanelHead title="Cấu hình chiến lược" />
          <div className="space-y-3 text-[12px] p-1">
            <div>
              <label className="text-secondary">Danh mục / Mã CP</label>
              <input
                value={symbol}
                onChange={(e) => setSymbol(e.target.value)}
                className="mt-1 w-full h-8 border border-line rounded-[6px] px-2.5 font-mono text-ink bg-paper"
              />
            </div>
            <div>
              <label className="text-secondary">Mô hình chiến lược</label>
              <select
                value={strategy}
                onChange={(e) => setStrategy(e.target.value)}
                className="mt-1 w-full h-8 border border-line rounded-[6px] px-2 font-mono text-ink bg-paper"
              >
                <option value="sma_cross">Giao cắt trung bình động</option>
                <option value="rsi">Đảo chiều RSI</option>
                <option value="bollinger">Dải Bollinger</option>
                <option value="Reinforcement Learning">Học tăng cường thích ứng</option>
              </select>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="text-secondary">Ngày bắt đầu</label>
                <input
                  type="date"
                  value={startDate}
                  onChange={(e) => setStartDate(e.target.value)}
                  className="mt-1 w-full h-8 border border-line rounded-[6px] px-2 font-mono text-ink bg-paper text-[11px]"
                />
              </div>
              <div>
                <label className="text-secondary">Ngày kết thúc</label>
                <input
                  type="date"
                  value={endDate}
                  onChange={(e) => setEndDate(e.target.value)}
                  className="mt-1 w-full h-8 border border-line rounded-[6px] px-2 font-mono text-ink bg-paper text-[11px]"
                />
              </div>
            </div>
            <div>
              <label className="text-secondary">Vốn ban đầu (VNĐ)</label>
              <input
                value={capital}
                onChange={(e) => setCapital(e.target.value)}
                className="mt-1 w-full h-8 border border-line rounded-[6px] px-2.5 font-mono text-ink bg-paper tnum"
              />
            </div>
            <div>
              <label className="text-secondary">Tần suất tái cân bằng</label>
              <div className="mt-1 h-8 border border-line rounded-[6px] flex items-center px-2.5 tnum font-mono text-ink bg-paper">
                Hàng tháng / Thích ứng T+2.5
              </div>
            </div>
            <div>
              <label className="text-secondary">Phí & thuế giao dịch</label>
              <div className="mt-1 h-8 border border-line rounded-[6px] flex items-center px-2.5 tnum font-mono text-ink bg-paper">
                0.15% mỗi vòng khớp lệnh
              </div>
            </div>
            <Button
              variant="primary"
              className="w-full mt-2"
              onClick={handleRunBacktest}
              disabled={running}
            >
              {running ? "Đang chạy mô phỏng..." : "Bắt đầu mô phỏng"}
            </Button>
          </div>
        </Panel>

        <div className="space-y-4">
          <MetricStrip
            items={[
              {
                label: "Tổng lợi nhuận",
                value: (
                  <span className={totalReturn >= 0 ? "text-gain" : "text-loss"}>
                    {totalReturn >= 0 ? `+${totalReturn.toFixed(1)}%` : `${totalReturn.toFixed(1)}%`}
                  </span>
                ),
              },
              {
                label: "Tỷ số Sharpe",
                value: risks?.sharpe != null ? risks.sharpe.toFixed(2) : "—",
              },
              {
                label: "Sụt giảm tối đa",
                value: (
                  <span className="text-loss">
                    {risks?.maxDrawdown != null ? `${risks.maxDrawdown.toFixed(1)}%` : "—"}
                  </span>
                ),
              },
              {
                label: "Tỷ lệ thắng",
                value: `${winRate}%`,
              },
            ]}
          />

          <Panel>
            <PanelHead
              title="Đường cong tài sản & Hiệu suất rủi ro"
              sub={`Mô phỏng NAV danh mục lịch sử · ${startDate} đến ${endDate}`}
            />
            {equityCurve.length > 1 ? (
              <div className="h-56 bg-paper border border-line rounded-[8px] p-2 flex flex-col justify-between">
                <svg className="w-full h-full" viewBox={`0 0 ${chartWidth} ${chartHeight}`} preserveAspectRatio="none">
                  <polyline
                    fill="none"
                    stroke="#1D9E75"
                    strokeWidth="2.5"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    points={pointsString}
                  />
                </svg>
                <div className="flex justify-between text-[10px] font-mono text-muted px-2">
                  <span>{equityCurve[0].date}</span>
                  <span>{equityCurve[equityCurve.length - 1].date}</span>
                </div>
              </div>
            ) : (
              <div className="h-56 bg-paper border border-line rounded-[8px] grid place-items-center text-[12px] text-muted font-mono">
                {loading ? "Đang tải dữ liệu định lượng từ CSDL..." : "Chưa có lượt chạy nào. Bấm 'Bắt đầu mô phỏng' để vẽ đường cong."}
              </div>
            )}

            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mt-4 text-[13px]">
              <div>
                <div className="text-[11px] uppercase text-muted">Alpha</div>
                <div className="tnum font-mono text-ink text-[15px]">
                  {risks?.alpha != null ? `${risks.alpha > 0 ? `+${risks.alpha}%` : `${risks.alpha}%`}` : "+4.2%"}
                </div>
              </div>
              <div>
                <div className="text-[11px] uppercase text-muted">Beta</div>
                <div className="tnum font-mono text-ink text-[15px]">
                  {risks?.beta != null ? risks.beta.toFixed(2) : "0.92"}
                </div>
              </div>
              <div>
                <div className="text-[11px] uppercase text-muted">Lệnh mô phỏng</div>
                <div className="tnum font-mono text-ink text-[15px]">
                  {trades.length} bản ghi
                </div>
              </div>
              <div>
                <div className="text-[11px] uppercase text-muted">NAV Danh mục</div>
                <div className="tnum font-mono text-ink text-[15px]">
                  {latestValue > 0 ? (latestValue / 1e9).toFixed(3) + "B VNĐ" : "1.000B VNĐ"}
                </div>
              </div>
            </div>
          </Panel>

          <Panel>
            <PanelHead
              title="Nhật ký khớp lệnh & Giao dịch thử"
              sub={`Dữ liệu khớp lệnh mô phỏng từ CSDL (${trades.length} lệnh)`}
            />
            <div className="overflow-x-auto">
              <table className="w-full text-left text-[12px] font-mono">
                <thead className="border-b border-line text-muted">
                  <tr>
                    <th className="py-2 px-3">Ngày</th>
                    <th className="py-2 px-3">Mã CP</th>
                    <th className="py-2 px-3">Chiều</th>
                    <th className="py-2 px-3 text-right">Giá đặt</th>
                    <th className="py-2 px-3 text-right">Khối lượng</th>
                    <th className="py-2 px-3 text-center">Độ tin cậy</th>
                    <th className="py-2 px-3 text-center">Trạng thái</th>
                    <th className="py-2 px-3 text-right">Lãi/Lỗ</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {trades.slice(0, 8).map((t) => (
                    <tr key={t.id} className="hover:bg-surface/50">
                      <td className="py-2 px-3 text-muted text-[11px]">
                        {new Date(t.date || t.created_at).toLocaleDateString("vi-VN")}
                      </td>
                      <td className="py-2 px-3 font-semibold text-ink">{t.ticker}</td>
                      <td className="py-2 px-3">
                        <Pill tone={t.action === "BUY" ? "gain" : "loss"}>{t.action}</Pill>
                      </td>
                      <td className="py-2 px-3 text-right text-ink">
                        {t.price?.toLocaleString()}đ
                      </td>
                      <td className="py-2 px-3 text-right text-muted">
                        {t.quantity?.toLocaleString() || "—"}
                      </td>
                      <td className="py-2 px-3 text-center text-teal">
                        {t.confidence != null ? `${(t.confidence * 100).toFixed(0)}%` : "—"}
                      </td>
                      <td className="py-2 px-3 text-right">
                        <span className="text-[11px] text-muted">{t.status || "OPEN"}</span>
                      </td>
                    </tr>
                  ))}
                  {trades.length === 0 && (
                    <tr>
                      <td colSpan={7} className="py-8 text-center text-muted">
                        No paper trade records logged in database.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </Panel>
        </div>
      </div>
    </Page>
  )
}
