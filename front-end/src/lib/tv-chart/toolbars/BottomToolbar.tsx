"use client"

import React, { useEffect, useState } from "react"
import { TvIcon } from "../icons/tv-icons"
import type { TvDateRange, TvScaleMode, TvTradingOverlay } from "../types"

export interface BottomToolbarProps {
  activeRange: TvDateRange | null
  scaleMode: TvScaleMode
  autoScale: boolean
  isDark?: boolean
  trading?: TvTradingOverlay
  onSelectDateRange: (range: TvDateRange) => void
  onToggleScaleMode: (mode: TvScaleMode) => void
  onToggleAutoScale: () => void
  onGoToLatest: () => void
}

const DATE_RANGES: { id: TvDateRange; label: string; title: string }[] = [
  { id: "1D", label: "1D", title: "1 Ngày giao dịch" },
  { id: "5D", label: "5D", title: "5 Ngày giao dịch" },
  { id: "1M", label: "1M", title: "1 Tháng" },
  { id: "3M", label: "3M", title: "3 Tháng" },
  { id: "6M", label: "6M", title: "6 Tháng" },
  { id: "YTD", label: "YTD", title: "Từ đầu năm đến nay (Year to Date)" },
  { id: "1Y", label: "1Y", title: "1 Năm" },
  { id: "5Y", label: "5Y", title: "5 Năm" },
  { id: "ALL", label: "Tất cả", title: "Toàn bộ lịch sử dữ liệu" },
]

export const BottomToolbar: React.FC<BottomToolbarProps> = ({
  activeRange,
  scaleMode,
  autoScale,
  isDark = false,
  onSelectDateRange,
  onToggleScaleMode,
  onToggleAutoScale,
  onGoToLatest,
}) => {
  const [vnClock, setVnClock] = useState<string>("--:--:-- (UTC+7)")

  useEffect(() => {
    const updateClock = () => {
      try {
        const now = new Date()
        const timeStr = new Intl.DateTimeFormat("vi-VN", {
          timeZone: "Asia/Ho_Chi_Minh",
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
          hour12: false,
        }).format(now)
        setVnClock(`${timeStr} (UTC+7)`)
      } catch {
        setVnClock("UTC+7")
      }
    }
    updateClock()
    const timer = window.setInterval(updateClock, 1000)
    return () => window.clearInterval(timer)
  }, [])

  return (
    <div
      data-name="date-ranges-tabs"
      className={`h-8 px-2.5 border-t flex flex-nowrap items-center justify-between gap-2 text-[11px] select-none shrink-0 ${
        isDark
          ? "bg-[#131722] border-[#2A2E39] text-[#9DB2BD]"
          : "bg-white border-[#E0E3EB] text-[#4A505E]"
      }`}
    >
      {/* Left: Quick Date Ranges + Go to Latest */}
      <div className="flex flex-nowrap items-center gap-0.5 overflow-x-auto no-scrollbar min-w-0">
        {DATE_RANGES.map((r) => {
          const isActive = activeRange === r.id
          return (
            <button
              key={r.id}
              type="button"
              onClick={() => onSelectDateRange(r.id)}
              title={r.title}
              className={`h-6 px-2 rounded font-semibold transition-colors whitespace-nowrap shrink-0 ${
                isActive
                  ? "bg-[#2962FF]/15 text-[#2962FF]"
                  : "hover:bg-black/5 dark:hover:bg-white/5 hover:text-current"
              }`}
            >
              {r.label}
            </button>
          )
        })}

        <div className="w-px h-4 bg-current/15 mx-1 shrink-0" />

        <button
          type="button"
          onClick={onGoToLatest}
          title="Di chuyển tới thanh nến mới nhất"
          className="h-6 px-1.5 rounded flex items-center gap-1 hover:bg-black/5 dark:hover:bg-white/5 whitespace-nowrap shrink-0"
        >
          <TvIcon name="control-bar-back-to-realtime" size={14} />
          <span className="hidden md:inline text-[10.5px]">Hiện tại</span>
        </button>
      </div>

      {/* Right: Timezone Clock + Scale Mode (% / log / auto) */}
      <div className="flex flex-nowrap items-center gap-1 shrink-0">
        <span className="hidden sm:inline font-mono text-[10.5px] opacity-75 px-1.5 whitespace-nowrap">
          {vnClock}
        </span>

        <div className="w-px h-4 bg-current/15 mx-0.5" />

        <button
          type="button"
          onClick={() =>
            onToggleScaleMode(scaleMode === "percentage" ? "normal" : "percentage")
          }
          title="Thang giá phần trăm (% thay đổi so với đầu khung nhìn)"
          className={`h-6 px-1.5 rounded font-mono text-[11px] font-semibold transition-colors ${
            scaleMode === "percentage"
              ? "bg-[#2962FF] text-white"
              : "hover:bg-black/5 dark:hover:bg-white/5"
          }`}
        >
          %
        </button>

        <button
          type="button"
          onClick={() =>
            onToggleScaleMode(scaleMode === "logarithmic" ? "normal" : "logarithmic")
          }
          title="Thang giá Logarit (Log Scale)"
          className={`h-6 px-1.5 rounded font-mono text-[11px] font-semibold transition-colors ${
            scaleMode === "logarithmic"
              ? "bg-[#2962FF] text-white"
              : "hover:bg-black/5 dark:hover:bg-white/5"
          }`}
        >
          log
        </button>

        <button
          type="button"
          onClick={onToggleAutoScale}
          title="Tự động co giãn trục giá khớp với dữ liệu trên màn hình (Auto Scale)"
          className={`h-6 px-2 rounded text-[11px] font-semibold transition-colors whitespace-nowrap ${
            autoScale
              ? "bg-[#2962FF]/15 text-[#2962FF]"
              : "hover:bg-black/5 dark:hover:bg-white/5"
          }`}
        >
          tự động
        </button>
      </div>
    </div>
  )
}
