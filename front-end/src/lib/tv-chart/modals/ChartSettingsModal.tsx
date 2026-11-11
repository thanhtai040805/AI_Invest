"use client"

import React, { useEffect, useState } from "react"
import { createPortal } from "react-dom"
import { TvIcon } from "../icons/tv-icons"
import type { TvChartSettings } from "../types"

export interface ChartSettingsModalProps {
  isOpen: boolean
  settings: TvChartSettings
  onClose: () => void
  onUpdateSettings: (patch: Partial<TvChartSettings>) => void
  onResetSettings: () => void
}

export const DEFAULT_TV_CHART_SETTINGS: TvChartSettings = {
  theme: "light",
  showGrid: true,
  showWatermark: true,
  showHoseBands: false,
  showOrderLine: true,
  showPositionLine: true,
  showOpenOrders: true,
  showCrosshairLabels: true,
  showSessionBreaks: false,
  showBarChangeValues: true,
  showSeriesOHLC: true,
  showIndicatorValues: true,
  upColor: "#089981",
  downColor: "#F23645",
  borderUpColor: "#089981",
  borderDownColor: "#F23645",
  wickUpColor: "#089981",
  wickDownColor: "#F23645",
  rightOffsetBars: 6,
}

export const ChartSettingsModal: React.FC<ChartSettingsModalProps> = ({
  isOpen,
  settings,
  onClose,
  onUpdateSettings,
  onResetSettings,
}) => {
  const [mounted, setMounted] = useState(false)
  const [tab, setTab] = useState<"symbol" | "trading" | "appearance" | "status">("symbol")
  const isDark = settings.theme === "dark"

  useEffect(() => {
    setMounted(true)
  }, [])

  if (!isOpen || !mounted) return null

  const applyColorPreset = (up: string, down: string) => {
    onUpdateSettings({
      upColor: up,
      downColor: down,
      borderUpColor: up,
      borderDownColor: down,
      wickUpColor: up,
      wickDownColor: down,
    })
  }

  return createPortal(
    <div
      className="fixed inset-0 z-[9999] flex items-center justify-center bg-black/50 backdrop-blur-xs p-4"
      onClick={onClose}
    >
      <div
        data-name="chart-settings-dialog"
        onClick={(e) => e.stopPropagation()}
        className={`w-full max-w-2xl rounded-xl border shadow-2xl overflow-hidden flex flex-col max-h-[86vh] ${
          isDark
            ? "bg-[#1E222D] border-[#2A2E39] text-[#D1D4DC]"
            : "bg-white border-[#E0E3EB] text-[#131722]"
        }`}
      >
        {/* Header */}
        <div className="px-5 py-3.5 border-b border-current/10 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <TvIcon name="header-toolbar-properties" size={20} className="text-[#2962FF]" />
            <h3 className="font-bold text-sm">Cài đặt biểu đồ (TradingView Properties)</h3>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="w-7 h-7 rounded-lg flex items-center justify-center hover:bg-black/5 dark:hover:bg-white/10"
          >
            ✕
          </button>
        </div>

        {/* Body */}
        <div className="grid grid-cols-1 sm:grid-cols-12 min-h-[340px]">
          {/* Left Tabs */}
          <div className="sm:col-span-4 border-r border-current/10 p-2.5 flex flex-col gap-1">
            {(
              [
                { id: "symbol", label: "Mã & Thân nến" },
                { id: "trading", label: "Giao dịch & Biên độ HOSE" },
                { id: "appearance", label: "Diện mạo & Lưới" },
                { id: "status", label: "Dòng trạng thái (Legend)" },
              ] as const
            ).map((t) => (
              <button
                key={t.id}
                type="button"
                onClick={() => setTab(t.id)}
                className={`px-3 py-2 rounded-lg text-xs font-medium text-left transition-colors ${
                  tab === t.id
                    ? "bg-[#2962FF]/15 text-[#2962FF] font-bold"
                    : "hover:bg-black/5 dark:hover:bg-white/5"
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>

          {/* Right Content */}
          <div className="sm:col-span-8 p-4 space-y-4 text-xs">
            {tab === "symbol" && (
              <>
                <div>
                  <div className="font-bold mb-2">Bộ màu nến tiêu chuẩn</div>
                  <div className="grid grid-cols-3 gap-2">
                    <button
                      type="button"
                      onClick={() => applyColorPreset("#089981", "#F23645")}
                      className="p-2 rounded-lg border border-current/15 hover:border-[#2962FF] flex flex-col items-center gap-1"
                    >
                      <div className="flex gap-1">
                        <span className="w-4 h-4 rounded-xs bg-[#089981]" />
                        <span className="w-4 h-4 rounded-xs bg-[#F23645]" />
                      </div>
                      <span className="text-[11px] font-medium">TradingView Chuẩn</span>
                    </button>

                    <button
                      type="button"
                      onClick={() => applyColorPreset("#2E7D32", "#C62828")}
                      className="p-2 rounded-lg border border-current/15 hover:border-[#2962FF] flex flex-col items-center gap-1"
                    >
                      <div className="flex gap-1">
                        <span className="w-4 h-4 rounded-xs bg-[#2E7D32]" />
                        <span className="w-4 h-4 rounded-xs bg-[#C62828]" />
                      </div>
                      <span className="text-[11px] font-medium">AIInvest Editorial</span>
                    </button>

                    <button
                      type="button"
                      onClick={() => applyColorPreset("#26A69A", "#EF5350")}
                      className="p-2 rounded-lg border border-current/15 hover:border-[#2962FF] flex flex-col items-center gap-1"
                    >
                      <div className="flex gap-1">
                        <span className="w-4 h-4 rounded-xs bg-[#26A69A]" />
                        <span className="w-4 h-4 rounded-xs bg-[#EF5350]" />
                      </div>
                      <span className="text-[11px] font-medium">HOSE Classic</span>
                    </button>
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-3 pt-2 border-t border-current/10">
                  <label className="flex items-center justify-between p-2 rounded border border-current/10">
                    <span>Thân nến Tăng</span>
                    <input
                      type="color"
                      value={settings.upColor}
                      onChange={(e) =>
                        onUpdateSettings({
                          upColor: e.target.value,
                          borderUpColor: e.target.value,
                          wickUpColor: e.target.value,
                        })
                      }
                      className="w-7 h-5 cursor-pointer bg-transparent border-0"
                    />
                  </label>
                  <label className="flex items-center justify-between p-2 rounded border border-current/10">
                    <span>Thân nến Giảm</span>
                    <input
                      type="color"
                      value={settings.downColor}
                      onChange={(e) =>
                        onUpdateSettings({
                          downColor: e.target.value,
                          borderDownColor: e.target.value,
                          wickDownColor: e.target.value,
                        })
                      }
                      className="w-7 h-5 cursor-pointer bg-transparent border-0"
                    />
                  </label>
                </div>
              </>
            )}

            {tab === "trading" && (
              <div className="space-y-3">
                <label className="flex items-center justify-between cursor-pointer">
                  <div>
                    <div className="font-semibold">
                      Đường biên độ HOSE (Giá Trần / Tham chiếu / Giá Sàn)
                    </div>
                    <div className="text-[11px] opacity-65">
                      Hiển thị đường nét đứt Tím (Trần), Vàng (TC), Xanh lơ (Sàn)
                    </div>
                  </div>
                  <input
                    type="checkbox"
                    checked={settings.showHoseBands}
                    onChange={(e) => onUpdateSettings({ showHoseBands: e.target.checked })}
                    className="w-4 h-4 accent-[#2962FF]"
                  />
                </label>

                <label className="flex items-center justify-between cursor-pointer pt-2 border-t border-current/10">
                  <div>
                    <div className="font-semibold">
                      Đường Giá đặt lệnh trực quan (Kéo thả trên biểu đồ)
                    </div>
                    <div className="text-[11px] opacity-65">
                      Cho phép kéo đường Lệnh Mua/Bán lên xuống để đổi giá đặt lệnh
                    </div>
                  </div>
                  <input
                    type="checkbox"
                    checked={settings.showOrderLine}
                    onChange={(e) => onUpdateSettings({ showOrderLine: e.target.checked })}
                    className="w-4 h-4 accent-[#2962FF]"
                  />
                </label>

                <label className="flex items-center justify-between cursor-pointer pt-2 border-t border-current/10">
                  <div>
                    <div className="font-semibold">Đường Giá vốn vị thế đang nắm giữ</div>
                    <div className="text-[11px] opacity-65">
                      Hiển thị mức giá bình quân danh mục và khối lượng cổ phiếu
                    </div>
                  </div>
                  <input
                    type="checkbox"
                    checked={settings.showPositionLine}
                    onChange={(e) => onUpdateSettings({ showPositionLine: e.target.checked })}
                    className="w-4 h-4 accent-[#2962FF]"
                  />
                </label>

                <label className="flex items-center justify-between cursor-pointer pt-2 border-t border-current/10">
                  <div>
                    <div className="font-semibold">Hiển thị các lệnh đang chờ khớp trên biểu đồ</div>
                    <div className="text-[11px] opacity-65">
                      Vẽ đường ngang cho các lệnh LO đang chờ khớp của mã này
                    </div>
                  </div>
                  <input
                    type="checkbox"
                    checked={settings.showOpenOrders}
                    onChange={(e) => onUpdateSettings({ showOpenOrders: e.target.checked })}
                    className="w-4 h-4 accent-[#2962FF]"
                  />
                </label>
              </div>
            )}

            {tab === "appearance" && (
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <span className="font-semibold">Giao diện nền (Theme)</span>
                  <div className="flex items-center gap-1.5">
                    <button
                      type="button"
                      onClick={() => onUpdateSettings({ theme: "light" })}
                      className={`px-3 py-1 rounded border text-xs font-semibold ${
                        settings.theme === "light"
                          ? "border-[#2962FF] bg-[#2962FF]/15 text-[#2962FF]"
                          : "border-current/15"
                      }`}
                    >
                      ☀ Sáng (Light)
                    </button>
                    <button
                      type="button"
                      onClick={() => onUpdateSettings({ theme: "dark" })}
                      className={`px-3 py-1 rounded border text-xs font-semibold ${
                        settings.theme === "dark"
                          ? "border-[#2962FF] bg-[#2962FF]/15 text-[#2962FF]"
                          : "border-current/15"
                      }`}
                    >
                      ☾ Tối (Dark)
                    </button>
                  </div>
                </div>

                <label className="flex items-center justify-between cursor-pointer pt-2 border-t border-current/10">
                  <span className="font-semibold">Hiển thị đường lưới dọc & ngang (Gridlines)</span>
                  <input
                    type="checkbox"
                    checked={settings.showGrid}
                    onChange={(e) => onUpdateSettings({ showGrid: e.target.checked })}
                    className="w-4 h-4 accent-[#2962FF]"
                  />
                </label>

                <label className="flex items-center justify-between cursor-pointer pt-2 border-t border-current/10">
                  <span className="font-semibold">Hình mờ mã cổ phiếu & khung thời gian (Watermark)</span>
                  <input
                    type="checkbox"
                    checked={settings.showWatermark}
                    onChange={(e) => onUpdateSettings({ showWatermark: e.target.checked })}
                    className="w-4 h-4 accent-[#2962FF]"
                  />
                </label>

                <label className="flex items-center justify-between pt-2 border-t border-current/10">
                  <span className="font-semibold">Khoảng trống bên phải (Số thanh nến)</span>
                  <input
                    type="number"
                    min={0}
                    max={30}
                    value={settings.rightOffsetBars}
                    onChange={(e) =>
                      onUpdateSettings({
                        rightOffsetBars: Math.max(0, Math.min(30, Number(e.target.value) || 0)),
                      })
                    }
                    className="w-16 px-2 py-1 rounded border border-current/20 bg-transparent font-mono text-center"
                  />
                </label>
              </div>
            )}

            {tab === "status" && (
              <div className="space-y-3">
                <label className="flex items-center justify-between cursor-pointer">
                  <span className="font-semibold">Hiển thị giá Mở / Cao / Thấp / Đóng (OHLC)</span>
                  <input
                    type="checkbox"
                    checked={settings.showSeriesOHLC}
                    onChange={(e) => onUpdateSettings({ showSeriesOHLC: e.target.checked })}
                    className="w-4 h-4 accent-[#2962FF]"
                  />
                </label>

                <label className="flex items-center justify-between cursor-pointer pt-2 border-t border-current/10">
                  <span className="font-semibold">Hiển thị mức thay đổi giá và % phiên</span>
                  <input
                    type="checkbox"
                    checked={settings.showBarChangeValues}
                    onChange={(e) => onUpdateSettings({ showBarChangeValues: e.target.checked })}
                    className="w-4 h-4 accent-[#2962FF]"
                  />
                </label>

                <label className="flex items-center justify-between cursor-pointer pt-2 border-t border-current/10">
                  <span className="font-semibold">Hiển thị giá trị số của các chỉ báo kỹ thuật</span>
                  <input
                    type="checkbox"
                    checked={settings.showIndicatorValues}
                    onChange={(e) => onUpdateSettings({ showIndicatorValues: e.target.checked })}
                    className="w-4 h-4 accent-[#2962FF]"
                  />
                </label>
              </div>
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="px-5 py-3 border-t border-current/10 flex items-center justify-between text-xs">
          <button
            type="button"
            onClick={onResetSettings}
            className="px-3 py-1.5 rounded-lg border border-current/15 hover:bg-black/5 dark:hover:bg-white/5 font-medium"
          >
            Khôi phục mặc định
          </button>
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-1.5 rounded-lg bg-[#2962FF] text-white font-semibold hover:bg-[#1E53E5]"
          >
            Đóng
          </button>
        </div>
      </div>
    </div>,
    document.body
  )
}
