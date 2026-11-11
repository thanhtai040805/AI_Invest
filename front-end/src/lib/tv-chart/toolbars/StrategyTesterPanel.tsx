"use client"

import React, { useMemo, useState } from "react"
import type { TvIndicatorComputed } from "../types"

export interface StrategyTesterPanelProps {
  strategyIndicators: TvIndicatorComputed[]
  isDark?: boolean
  isOpen: boolean
  onToggleOpen: () => void
  onOpenSettings?: (instanceId: string) => void
  onRemoveStrategy?: (instanceId: string) => void
}

export const StrategyTesterPanel: React.FC<StrategyTesterPanelProps> = ({
  strategyIndicators,
  isDark = false,
  isOpen,
  onToggleOpen,
  onOpenSettings,
  onRemoveStrategy,
}) => {
  const [selectedIdx, setSelectedIdx] = useState(0)
  const [activeTab, setActiveTab] = useState<"overview" | "trades">("overview")

  const currentStrategy =
    strategyIndicators[Math.min(selectedIdx, Math.max(0, strategyIndicators.length - 1))]
  const report = currentStrategy?.strategyReport

  const svgPath = useMemo(() => {
    if (!report || !report.equityCurve.length) return ""
    const pts = report.equityCurve
      .map((v, idx) => (v != null ? { idx, v } : null))
      .filter((x): x is { idx: number; v: number } => x !== null)
    if (pts.length < 2) return ""
    const minV = Math.min(0, ...pts.map((p) => p.v))
    const maxV = Math.max(1, ...pts.map((p) => p.v))
    const span = Math.max(1e-3, maxV - minV)
    const w = 320
    const h = 54
    return pts
      .map((p, k) => {
        const x = (k / (pts.length - 1)) * w
        const y = h - ((p.v - minV) / span) * (h - 8) - 4
        return `${k === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`
      })
      .join(" ")
  }, [report])

  if (strategyIndicators.length === 0 || !report) return null

  return (
    <div
      data-name="strategy-tester-panel"
      className={`border-t select-none shrink-0 transition-all ${
        isDark
          ? "bg-[#131722] border-[#2A2E39] text-[#D1D4DC]"
          : "bg-white border-[#E0E3EB] text-[#131722]"
      }`}
    >
      {/* Slim Header Bar */}
      <div className="h-8 px-3 flex items-center justify-between gap-2 border-b border-current/10">
        <div className="flex items-center gap-2 min-w-0">
          <button
            type="button"
            onClick={onToggleOpen}
            className="flex items-center gap-1.5 text-[11.5px] font-bold text-[#2962FF] hover:opacity-85"
          >
            <span>{isOpen ? "▾" : "▸"}</span>
            <span>Trình kiểm tra Chiến lược (Strategy Tester)</span>
          </button>

          {strategyIndicators.length > 1 ? (
            <select
              value={Math.min(selectedIdx, strategyIndicators.length - 1)}
              onChange={(e) => setSelectedIdx(Number(e.target.value))}
              className={`h-6 px-2 rounded text-[11px] font-semibold border outline-none ${
                isDark
                  ? "bg-[#1E222D] border-[#2A2E39] text-[#D1D4DC]"
                  : "bg-[#F8F9FD] border-[#E0E3EB] text-[#131722]"
              }`}
            >
              {strategyIndicators.map((st, idx) => (
                <option key={st.instance.instanceId} value={idx}>
                  {st.meta.name}
                </option>
              ))}
            </select>
          ) : (
            <span className="text-[11px] font-semibold truncate opacity-85">
              · {currentStrategy.meta.name}
            </span>
          )}

          <span
            className={`px-1.5 py-0.5 rounded text-[10px] font-mono font-bold ${
              report.netProfitPct >= 0
                ? "bg-[#089981]/15 text-[#089981]"
                : "bg-[#F23645]/15 text-[#F23645]"
            }`}
          >
            {report.netProfitPct >= 0 ? "+" : ""}
            {report.netProfitPct.toFixed(2)}%
          </span>
        </div>

        <div className="flex items-center gap-1.5 shrink-0">
          {isOpen && (
            <div className="flex items-center gap-1 mr-1">
              <button
                type="button"
                onClick={() => setActiveTab("overview")}
                className={`h-6 px-2.5 rounded-full text-[10.5px] font-semibold transition-colors ${
                  activeTab === "overview"
                    ? isDark
                      ? "bg-[#D1D4DC] text-[#131722]"
                      : "bg-[#131722] text-white"
                    : "hover:bg-current/10 opacity-75"
                }`}
              >
                Tổng quan hiệu suất
              </button>
              <button
                type="button"
                onClick={() => setActiveTab("trades")}
                className={`h-6 px-2.5 rounded-full text-[10.5px] font-semibold transition-colors ${
                  activeTab === "trades"
                    ? isDark
                      ? "bg-[#D1D4DC] text-[#131722]"
                      : "bg-[#131722] text-white"
                    : "hover:bg-current/10 opacity-75"
                }`}
              >
                Danh sách giao dịch ({report.totalTrades})
              </button>
            </div>
          )}

          {onOpenSettings && currentStrategy && (
            <button
              type="button"
              onClick={() => onOpenSettings(currentStrategy.instance.instanceId)}
              title="Tham số chiến lược"
              className="h-6 px-2 rounded text-[10.5px] font-medium hover:bg-current/10"
            >
              ⚙ Tham số
            </button>
          )}
          {onRemoveStrategy && currentStrategy && (
            <button
              type="button"
              onClick={() => onRemoveStrategy(currentStrategy.instance.instanceId)}
              title="Gỡ chiến lược khỏi biểu đồ"
              className="h-6 px-2 rounded text-[10.5px] font-semibold text-[#F23645] hover:bg-[#F23645]/10"
            >
              ✕ Gỡ
            </button>
          )}
          <button
            type="button"
            onClick={onToggleOpen}
            className="h-6 px-1.5 rounded text-[10.5px] opacity-70 hover:opacity-100"
          >
            {isOpen ? "Thu gọn" : "Mở rộng"}
          </button>
        </div>
      </div>

      {/* Collapsible Body */}
      {isOpen && (
        <div className="px-3.5 py-2.5">
          {activeTab === "overview" ? (
            <div className="flex flex-wrap lg:flex-nowrap items-center justify-between gap-4">
              <div className="grid grid-cols-3 sm:grid-cols-6 gap-3 flex-1 min-w-0">
                <div>
                  <div className="text-[10px] uppercase tracking-wider opacity-60">Lợi nhuận ròng</div>
                  <div
                    className={`text-[13.5px] font-mono font-bold mt-0.5 ${
                      report.netProfitPct >= 0 ? "text-[#089981]" : "text-[#F23645]"
                    }`}
                  >
                    {report.netProfitPct >= 0 ? "+" : ""}
                    {report.netProfitPct.toFixed(2)}%
                  </div>
                </div>
                <div>
                  <div className="text-[10px] uppercase tracking-wider opacity-60">Tổng số lệnh</div>
                  <div className="text-[13.5px] font-mono font-bold mt-0.5">
                    {report.totalTrades}
                  </div>
                </div>
                <div>
                  <div className="text-[10px] uppercase tracking-wider opacity-60">Tỷ lệ thắng</div>
                  <div className="text-[13.5px] font-mono font-bold mt-0.5 text-[#2962FF]">
                    {report.winRatePct.toFixed(1)}%
                  </div>
                </div>
                <div>
                  <div className="text-[10px] uppercase tracking-wider opacity-60">Profit Factor</div>
                  <div className="text-[13.5px] font-mono font-bold mt-0.5">
                    {report.profitFactor.toFixed(2)}
                  </div>
                </div>
                <div>
                  <div className="text-[10px] uppercase tracking-wider opacity-60">Sụt giảm tối đa</div>
                  <div className="text-[13.5px] font-mono font-bold mt-0.5 text-[#F23645]">
                    -{Math.abs(report.maxDrawdownPct).toFixed(2)}%
                  </div>
                </div>
                <div>
                  <div className="text-[10px] uppercase tracking-wider opacity-60">Sharpe Ratio</div>
                  <div className="text-[13.5px] font-mono font-bold mt-0.5">
                    {report.sharpeRatio.toFixed(2)}
                  </div>
                </div>
              </div>

              {/* Mini Equity Curve SVG */}
              <div
                className={`w-full lg:w-[330px] h-[58px] rounded-lg border px-2 py-1 shrink-0 flex items-center justify-center ${
                  isDark ? "bg-[#1E222D] border-[#2A2E39]" : "bg-[#F8F9FD] border-[#E0E3EB]"
                }`}
              >
                {svgPath ? (
                  <svg viewBox="0 0 320 54" className="w-full h-full overflow-visible">
                    <path
                      d={svgPath}
                      fill="none"
                      stroke={report.netProfitPct >= 0 ? "#089981" : "#F23645"}
                      strokeWidth="2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                ) : (
                  <span className="text-[10.5px] opacity-50">Chưa đủ giao dịch vẽ đường vốn</span>
                )}
              </div>
            </div>
          ) : (
            <div className="max-h-[132px] overflow-y-auto">
              <table className="w-full text-left text-[11px] font-mono">
                <thead className="sticky top-0 bg-inherit border-b border-current/10 text-[10px] uppercase opacity-65">
                  <tr>
                    <th className="py-1 pr-2">#</th>
                    <th className="py-1 px-2">Loại tín hiệu</th>
                    <th className="py-1 px-2">Ngày vào</th>
                    <th className="py-1 px-2 text-right">Giá vào</th>
                    <th className="py-1 px-2">Ngày đóng</th>
                    <th className="py-1 px-2 text-right">Giá đóng</th>
                    <th className="py-1 px-2 text-right">Lãi/Lỗ</th>
                    <th className="py-1 pl-2 text-right">Lũy kế</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-current/5">
                  {report.trades
                    .slice()
                    .reverse()
                    .map((tr) => (
                      <tr key={tr.tradeNo} className="hover:bg-current/[0.03]">
                        <td className="py-1 pr-2 opacity-70">#{tr.tradeNo}</td>
                        <td className="py-1 px-2 font-semibold text-[#089981]">{tr.signalName}</td>
                        <td className="py-1 px-2 opacity-80">{tr.entryDate.slice(0, 10)}</td>
                        <td className="py-1 px-2 text-right">
                          {Math.round(tr.entryPrice).toLocaleString("vi-VN")}
                        </td>
                        <td className="py-1 px-2 opacity-80">{tr.exitDate.slice(0, 10)}</td>
                        <td className="py-1 px-2 text-right">
                          {Math.round(tr.exitPrice).toLocaleString("vi-VN")}
                        </td>
                        <td
                          className={`py-1 px-2 text-right font-bold ${
                            tr.pnlPct >= 0 ? "text-[#089981]" : "text-[#F23645]"
                          }`}
                        >
                          {tr.pnlPct >= 0 ? "+" : ""}
                          {tr.pnlPct.toFixed(2)}%
                        </td>
                        <td
                          className={`py-1 pl-2 text-right font-semibold ${
                            tr.cumPnlPct >= 0 ? "text-[#089981]" : "text-[#F23645]"
                          }`}
                        >
                          {tr.cumPnlPct >= 0 ? "+" : ""}
                          {tr.cumPnlPct.toFixed(2)}%
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
