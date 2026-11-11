"use client"

import React, { useEffect, useState } from "react"
import { createPortal } from "react-dom"
import { TvIcon } from "../icons/tv-icons"
import { TV_DRAWING_TOOL_MAP } from "../drawings/tool-groups"
import { fmtCompactAxisValue, fmtPriceVnd } from "../engine/canvas-renderer"
import type {
  TvCandle,
  TvDrawingObject,
  TvIndicatorComputed,
  TvTradingOverlay,
} from "../types"

export interface ObjectTreeModalProps {
  isOpen: boolean
  isDark?: boolean
  symbol: string
  activeCandle: TvCandle | null
  hoveredBarIndex: number
  trading?: TvTradingOverlay
  indicators: TvIndicatorComputed[]
  drawings: TvDrawingObject[]
  selectedDrawingId: string | null
  onClose: () => void
  onSelectDrawing: (id: string) => void
  onUpdateDrawing: (id: string, patch: Partial<TvDrawingObject>) => void
  onDeleteDrawing: (id: string) => void
  onToggleIndicatorVisible: (instanceId: string) => void
  onRemoveIndicator: (instanceId: string) => void
  onClearAllDrawings: () => void
}

export const ObjectTreeModal: React.FC<ObjectTreeModalProps> = ({
  isOpen,
  isDark = false,
  symbol,
  activeCandle,
  hoveredBarIndex,
  trading,
  indicators,
  drawings,
  selectedDrawingId,
  onClose,
  onSelectDrawing,
  onUpdateDrawing,
  onDeleteDrawing,
  onToggleIndicatorVisible,
  onRemoveIndicator,
  onClearAllDrawings,
}) => {
  const [mounted, setMounted] = useState(false)
  const [tab, setTab] = useState<"tree" | "data">("tree")

  useEffect(() => {
    setMounted(true)
  }, [])

  if (!isOpen || !mounted) return null

  return createPortal(
    <div
      className="fixed inset-0 z-[9999] flex items-center justify-end bg-black/40 backdrop-blur-xs"
      onClick={onClose}
    >
      <div
        data-name="object-tree-dialog"
        onClick={(e) => e.stopPropagation()}
        className={`w-full max-w-md h-full border-l shadow-2xl flex flex-col ${
          isDark
            ? "bg-[#1E222D] border-[#2A2E39] text-[#D1D4DC]"
            : "bg-white border-[#E0E3EB] text-[#131722]"
        }`}
      >
        {/* Header with Tabs */}
        <div className="px-4 py-3 border-b border-current/10 flex items-center justify-between">
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              onClick={() => setTab("tree")}
              className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-colors ${
                tab === "tree"
                  ? "bg-[#2962FF] text-white"
                  : "hover:bg-black/5 dark:hover:bg-white/5 opacity-75"
              }`}
            >
              Cây đối tượng ({indicators.length + drawings.length})
            </button>
            <button
              type="button"
              onClick={() => setTab("data")}
              className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-colors ${
                tab === "data"
                  ? "bg-[#2962FF] text-white"
                  : "hover:bg-black/5 dark:hover:bg-white/5 opacity-75"
              }`}
            >
              Cửa sổ dữ liệu
            </button>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="w-7 h-7 rounded-lg flex items-center justify-center hover:bg-black/5 dark:hover:bg-white/10"
          >
            ✕
          </button>
        </div>

        {/* Tab 1: Object Tree */}
        {tab === "tree" && (
          <div className="flex-1 overflow-y-auto p-4 space-y-4">
            {/* Group 1: Drawings */}
            <div>
              <div className="flex items-center justify-between mb-2">
                <span className="text-[11px] font-bold uppercase tracking-wider opacity-60">
                  Công cụ vẽ trên biểu đồ ({drawings.length})
                </span>
                {drawings.length > 0 && (
                  <button
                    type="button"
                    onClick={onClearAllDrawings}
                    className="text-[11px] text-[#F23645] hover:underline font-medium"
                  >
                    Xóa tất cả nét vẽ
                  </button>
                )}
              </div>

              {drawings.length === 0 ? (
                <div className="p-3 rounded-lg border border-dashed border-current/15 text-xs opacity-60 text-center">
                  Chưa có đối tượng vẽ nào trên mã {symbol}. Chọn công cụ ở thanh bên trái để vẽ.
                </div>
              ) : (
                <div className="space-y-1.5">
                  {drawings.map((d) => {
                    const toolDef = TV_DRAWING_TOOL_MAP[d.tool]
                    const isSelected = selectedDrawingId === d.id
                    return (
                      <div
                        key={d.id}
                        onClick={() => onSelectDrawing(d.id)}
                        className={`p-2 rounded-lg border flex items-center justify-between gap-2 cursor-pointer transition-colors ${
                          isSelected
                            ? "border-[#2962FF] bg-[#2962FF]/10"
                            : "border-current/10 hover:border-current/25"
                        }`}
                      >
                        <div className="flex items-center gap-2 min-w-0">
                          <span
                            className="w-3 h-3 rounded-sm shrink-0"
                            style={{ backgroundColor: d.color }}
                          />
                          {toolDef && <TvIcon name={toolDef.iconName} size={16} />}
                          <div className="min-w-0">
                            <div className="text-xs font-semibold truncate">
                              {toolDef?.viName || d.tool}
                            </div>
                            <div className="text-[10px] font-mono opacity-60">
                              {d.points.length} điểm neo ·{" "}
                              {d.points[0] ? `${fmtPriceVnd(d.points[0].price)} đ` : ""}
                            </div>
                          </div>
                        </div>

                        <div
                          className="flex items-center gap-1 shrink-0"
                          onClick={(e) => e.stopPropagation()}
                        >
                          <button
                            type="button"
                            onClick={() => onUpdateDrawing(d.id, { visible: !d.visible })}
                            className={`p-1 rounded ${
                              d.visible ? "text-[#089981]" : "opacity-40"
                            }`}
                            title={d.visible ? "Đang hiển thị" : "Đang ẩn"}
                          >
                            <TvIcon name="legend-show-hide-action" size={15} />
                          </button>
                          <button
                            type="button"
                            onClick={() => onUpdateDrawing(d.id, { locked: !d.locked })}
                            className={`p-1 rounded ${
                              d.locked ? "text-amber-500" : "opacity-50"
                            }`}
                            title={d.locked ? "Đang khóa" : "Chưa khóa"}
                          >
                            <TvIcon name="lock-all" size={15} />
                          </button>
                          <button
                            type="button"
                            onClick={() => onDeleteDrawing(d.id)}
                            className="p-1 rounded text-[#F23645] hover:bg-[#F23645]/10"
                            title="Xóa nét vẽ"
                          >
                            <TvIcon name="legend-delete-action" size={15} />
                          </button>
                        </div>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>

            {/* Group 2: Active Indicators */}
            <div>
              <div className="text-[11px] font-bold uppercase tracking-wider opacity-60 mb-2">
                Chỉ báo kỹ thuật ({indicators.length})
              </div>
              <div className="space-y-1.5">
                {indicators.map((comp) => (
                  <div
                    key={comp.instance.instanceId}
                    className="p-2 rounded-lg border border-current/10 flex items-center justify-between gap-2"
                  >
                    <div className="flex items-center gap-2 min-w-0">
                      <span
                        className="w-2.5 h-2.5 rounded-full shrink-0"
                        style={{ backgroundColor: comp.instance.color }}
                      />
                      <span className="text-xs font-semibold truncate">{comp.title}</span>
                      <span className="text-[10px] px-1.5 py-0.2 rounded bg-current/10 font-mono">
                        {comp.meta.overlay ? "Chính" : "Ngăn phụ"}
                      </span>
                    </div>
                    <div className="flex items-center gap-1 shrink-0">
                      <button
                        type="button"
                        onClick={() => onToggleIndicatorVisible(comp.instance.instanceId)}
                        className={`p-1 rounded ${
                          comp.instance.visible ? "text-[#089981]" : "opacity-40"
                        }`}
                        title={comp.instance.visible ? "Đang hiển thị" : "Đang ẩn"}
                      >
                        <TvIcon name="legend-show-hide-action" size={15} />
                      </button>
                      <button
                        type="button"
                        onClick={() => onRemoveIndicator(comp.instance.instanceId)}
                        className="p-1 rounded text-[#F23645] hover:bg-[#F23645]/10"
                        title="Gỡ chỉ báo"
                      >
                        <TvIcon name="legend-delete-action" size={15} />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* Tab 2: Data Window */}
        {tab === "data" && (
          <div className="flex-1 overflow-y-auto p-4 space-y-4 font-mono text-xs">
            {/* OHLCV Section */}
            <div className="p-3 rounded-lg border border-current/10 space-y-1.5">
              <div className="font-sans font-bold text-xs text-[#2962FF] mb-2">
                {symbol} — Phiên {activeCandle?.label ?? "—"}
              </div>
              <div className="flex justify-between">
                <span className="opacity-65">Giá Mở cửa (Open)</span>
                <span className="font-semibold">
                  {activeCandle ? fmtPriceVnd(activeCandle.open) : "—"}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="opacity-65">Giá Cao nhất (High)</span>
                <span className="font-semibold text-[#089981]">
                  {activeCandle ? fmtPriceVnd(activeCandle.high) : "—"}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="opacity-65">Giá Thấp nhất (Low)</span>
                <span className="font-semibold text-[#F23645]">
                  {activeCandle ? fmtPriceVnd(activeCandle.low) : "—"}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="opacity-65">Giá Đóng cửa (Close)</span>
                <span className="font-bold">
                  {activeCandle ? fmtPriceVnd(activeCandle.close) : "—"}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="opacity-65">Khối lượng (Volume)</span>
                <span className="font-semibold">
                  {activeCandle ? activeCandle.volume.toLocaleString("vi-VN") : "—"}
                </span>
              </div>
            </div>

            {/* HOSE Reference Levels */}
            {trading && (
              <div className="p-3 rounded-lg border border-current/10 space-y-1.5">
                <div className="font-sans font-bold text-xs opacity-75 mb-1">
                  Biên độ giao dịch HOSE
                </div>
                <div className="flex justify-between text-[#9C27B0]">
                  <span>Giá Trần (+7%)</span>
                  <span className="font-bold">{fmtPriceVnd(trading.ceilingPrice)}</span>
                </div>
                <div className="flex justify-between text-[#CA8A04]">
                  <span>Giá Tham chiếu (TC)</span>
                  <span className="font-bold">{fmtPriceVnd(trading.refPrice)}</span>
                </div>
                <div className="flex justify-between text-[#00ACC1]">
                  <span>Giá Sàn (-7%)</span>
                  <span className="font-bold">{fmtPriceVnd(trading.floorPrice)}</span>
                </div>
                <div className="flex justify-between pt-1 border-t border-current/10">
                  <span>Giá đặt lệnh ({trading.orderSide})</span>
                  <span className="font-bold">{fmtPriceVnd(trading.orderPrice)}</span>
                </div>
              </div>
            )}

            {/* Indicator Values at Cursor */}
            <div className="space-y-2">
              {indicators.map((comp) => (
                <div
                  key={comp.instance.instanceId}
                  className="p-3 rounded-lg border border-current/10 space-y-1"
                >
                  <div className="font-sans font-bold text-xs">{comp.title}</div>
                  {comp.plots.map((p) => {
                    const v = p.values[hoveredBarIndex]
                    return (
                      <div key={p.key} className="flex justify-between">
                        <span className="opacity-65">{p.label}</span>
                        <span style={{ color: p.color }} className="font-semibold">
                          {v !== null && v !== undefined ? fmtCompactAxisValue(v) : "—"}
                        </span>
                      </div>
                    )
                  })}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>,
    document.body
  )
}
