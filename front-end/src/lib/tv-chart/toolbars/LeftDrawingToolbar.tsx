/* ============================================================================
 * TradingView Left Drawing Toolbar Component
 * Reconstructed from TradingView DOM (id="drawing-toolbar") via REA MCP
 * ============================================================================ */
"use client"

import React, { useEffect, useRef, useState } from "react"
import { TV_DRAWING_GROUPS, TV_DRAWING_TOOL_MAP } from "../drawings/tool-groups"
import { TvIcon } from "../icons/tv-icons"
import type { TvDrawingGroupId, TvDrawingToolId, TvMagnetMode } from "../types"

export interface LeftDrawingToolbarProps {
  activeTool: TvDrawingToolId
  magnetMode: TvMagnetMode
  keepDrawing: boolean
  lockAllDrawings: boolean
  hideAllDrawings: boolean
  drawingsCount: number
  isZoomMode?: boolean
  isDark: boolean
  onSelectTool: (tool: TvDrawingToolId) => void
  onCycleMagnetMode: () => void
  onToggleKeepDrawing: () => void
  onToggleLockAll: () => void
  onToggleHideAll: () => void
  onClearDrawings: () => void
  onClearIndicators: () => void
  onZoomIn: () => void
  onZoomOut?: () => void
  onToggleZoomMode?: () => void
}

export function LeftDrawingToolbar({
  activeTool,
  magnetMode,
  keepDrawing,
  lockAllDrawings,
  hideAllDrawings,
  drawingsCount,
  isZoomMode = false,
  isDark,
  onSelectTool,
  onCycleMagnetMode,
  onToggleKeepDrawing,
  onToggleLockAll,
  onToggleHideAll,
  onClearDrawings,
  onClearIndicators,
  onZoomIn,
  onZoomOut,
  onToggleZoomMode,
}: LeftDrawingToolbarProps) {
  const [openGroupId, setOpenGroupId] = useState<TvDrawingGroupId | null>(null)
  const [removeMenuOpen, setRemoveMenuOpen] = useState(false)
  const [lastSelectedPerGroup, setLastSelectedPerGroup] = useState<Record<TvDrawingGroupId, TvDrawingToolId>>({
    cursors: "cursor_cross",
    trend_lines: "LineToolTrendLine",
    gann_fibonacci: "LineToolFibRetracement",
    patterns: "LineTool5PointsPattern",
    prediction_measurement: "LineToolRiskRewardLong",
    geometric_shapes: "LineToolRectangle",
    annotation: "LineToolText",
  })

  const toolbarRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    function handleOutside(e: MouseEvent) {
      if (toolbarRef.current && !toolbarRef.current.contains(e.target as Node)) {
        setOpenGroupId(null)
        setRemoveMenuOpen(false)
      }
    }
    document.addEventListener("mousedown", handleOutside)
    return () => document.removeEventListener("mousedown", handleOutside)
  }, [])

  const handleChooseTool = (group: TvDrawingGroupId, toolId: TvDrawingToolId) => {
    setLastSelectedPerGroup((prev) => ({ ...prev, [group]: toolId }))
    onSelectTool(toolId)
    setOpenGroupId(null)
  }

  return (
    <div
      ref={toolbarRef}
      id="drawing-toolbar"
      className={`relative flex w-[42px] shrink-0 flex-col items-center gap-0.5 border-r py-1.5 select-none ${
        isDark
          ? "border-[#2A2E39] bg-[#131722] text-[#D1D4DC]"
          : "border-[var(--color-line)] bg-[var(--color-surface)] text-[var(--color-ink)]"
      }`}
    >
      {/* 7 TradingView Tool Groups */}
      {TV_DRAWING_GROUPS.map((group) => {
        const currentGroupToolId = lastSelectedPerGroup[group.id] || group.tools[0].id
        const currentToolDef = TV_DRAWING_TOOL_MAP[currentGroupToolId] || group.tools[0]
        const isGroupActive = group.tools.some((t) => t.id === activeTool)
        const isFlyoutOpen = openGroupId === group.id

        return (
          <div key={group.id} className="relative flex w-full justify-center">
            <div className="group relative flex items-center">
              <button
                type="button"
                data-name={group.dataName}
                title={`${currentToolDef.viName} (Nhấp đúp hoặc bấm mũi tên để mở nhóm ${group.viLabel})`}
                onClick={() => {
                  if (activeTool === currentToolDef.id) {
                    setOpenGroupId((prev) => (prev === group.id ? null : group.id))
                  } else {
                    handleChooseTool(group.id, currentToolDef.id)
                  }
                }}
                className={`flex h-[32px] w-[32px] items-center justify-center rounded transition-colors ${
                  isGroupActive
                    ? "bg-[#2962FF] text-white"
                    : "hover:bg-[var(--color-surface-2)]"
                }`}
              >
                <TvIcon name={currentToolDef.iconName} size={22} />
              </button>

              {/* Corner Flyout Trigger */}
              <button
                type="button"
                title={`Mở danh sách: ${group.viLabel} (${group.tools.length} công cụ)`}
                onClick={(e) => {
                  e.stopPropagation()
                  setOpenGroupId((prev) => (prev === group.id ? null : group.id))
                  setRemoveMenuOpen(false)
                }}
                className={`absolute -right-1 bottom-0.5 flex h-3.5 w-3.5 items-center justify-center rounded-full text-[8px] transition-opacity ${
                  isFlyoutOpen
                    ? "bg-[#2962FF] text-white opacity-100"
                    : "bg-[var(--color-surface-2)] opacity-70 hover:opacity-100"
                }`}
              >
                ▸
              </button>
            </div>

            {/* Flyout Menu for Group */}
            {isFlyoutOpen && (
              <div
                className={`absolute left-[44px] top-0 z-50 w-[265px] max-h-[380px] overflow-y-auto rounded-[6px] border p-1.5 shadow-2xl ${
                  isDark
                    ? "border-[#2A2E39] bg-[#1E222D] text-[#D1D4DC]"
                    : "border-[var(--color-line-strong)] bg-[var(--color-surface)] text-[var(--color-ink)]"
                }`}
              >
                <div className="mb-1 border-b border-[var(--color-line)]/60 px-2 pb-1 text-[10.5px] font-semibold uppercase tracking-wider opacity-65">
                  {group.viLabel} ({group.tools.length})
                </div>
                <div className="space-y-0.5">
                  {group.tools.map((tool) => {
                    const isToolSelected = activeTool === tool.id
                    return (
                      <button
                        key={tool.id}
                        type="button"
                        onClick={() => handleChooseTool(group.id, tool.id)}
                        className={`flex w-full items-center justify-between gap-2 rounded px-2 py-1.5 text-left text-[12px] transition-colors ${
                          isToolSelected
                            ? "bg-[#2962FF]/15 text-[#2962FF] font-semibold"
                            : "hover:bg-[var(--color-surface-2)]"
                        }`}
                      >
                        <span className="flex items-center gap-2.5 min-w-0">
                          <TvIcon name={tool.iconName} size={21} />
                          <span className="truncate">{tool.viName}</span>
                        </span>
                        {tool.shortcut && (
                          <span className="shrink-0 rounded bg-[var(--color-surface-2)] px-1.5 py-0.5 font-mono text-[10px] opacity-70">
                            {tool.shortcut}
                          </span>
                        )}
                      </button>
                    )
                  })}
                </div>
              </div>
            )}
          </div>
        )
      })}

      <span className="my-1 h-[1px] w-6 bg-[var(--color-line)]" />

      {/* Quick Measure Tool (data-name="measure") */}
      <button
        type="button"
        data-name="measure"
        title="Thước đo nhanh Giá & Thời gian (Measure)"
        onClick={() =>
          onSelectTool(
            activeTool === "LineToolDateAndPriceRange" ? "cursor_cross" : "LineToolDateAndPriceRange"
          )
        }
        className={`flex h-[31px] w-[31px] items-center justify-center rounded transition-colors ${
          activeTool === "LineToolDateAndPriceRange"
            ? "bg-[#2962FF] text-white"
            : "hover:bg-[var(--color-surface-2)]"
        }`}
      >
        <TvIcon name="measure" size={21} />
      </button>

      {/* Zoom In Tool (Box-Zoom Drag Mode or Step Zoom In, data-name="zoom") */}
      <button
        type="button"
        data-name="zoom"
        title="Phóng to vùng chọn (Bấm rồi kéo khung chữ nhật trên biểu đồ để phóng to, hoặc nhấn Esc để thoát)"
        onClick={() => {
          if (onToggleZoomMode) {
            onToggleZoomMode()
          } else {
            onZoomIn()
          }
        }}
        className={`flex h-[31px] w-[31px] items-center justify-center rounded transition-colors ${
          isZoomMode
            ? "bg-[#2962FF] text-white"
            : "hover:bg-[var(--color-surface-2)]"
        }`}
      >
        <TvIcon name="zoom" size={21} />
      </button>

      {/* Zoom Out Tool (Step Zoom Out) */}
      {onZoomOut && (
        <button
          type="button"
          data-name="zoom-out"
          title="Thu nhỏ biểu đồ (Zoom Out)"
          onClick={onZoomOut}
          className="flex h-[31px] w-[31px] items-center justify-center rounded hover:bg-[var(--color-surface-2)] transition-colors"
        >
          <TvIcon name="zoom-out" size={21} />
        </button>
      )}

      <span className="my-1 h-[1px] w-6 bg-[var(--color-line)]" />

      {/* Magnet Mode (data-name="magnet-button") */}
      <button
        type="button"
        data-name="magnet-button"
        title={`Chế độ Nam châm bắt dính OHLC: ${
          magnetMode === "off" ? "Đang tắt" : magnetMode === "weak" ? "Nam châm nhẹ" : "Nam châm mạnh"
        }`}
        onClick={onCycleMagnetMode}
        className={`relative flex h-[31px] w-[31px] items-center justify-center rounded transition-colors ${
          magnetMode !== "off"
            ? "bg-[#2962FF]/18 text-[#2962FF]"
            : "hover:bg-[var(--color-surface-2)]"
        }`}
      >
        <TvIcon name="magnet-button" size={21} />
        {magnetMode === "strong" && (
          <span className="absolute right-0.5 top-0.5 h-1.5 w-1.5 rounded-full bg-[#2962FF]" />
        )}
      </button>

      {/* Stay in Drawing Mode (data-name="drawginmode") */}
      <button
        type="button"
        data-name="drawginmode"
        title={`Giữ chế độ vẽ liên tục: ${keepDrawing ? "Đang bật" : "Đang tắt"}`}
        onClick={onToggleKeepDrawing}
        className={`flex h-[31px] w-[31px] items-center justify-center rounded transition-colors ${
          keepDrawing
            ? "bg-[#2962FF]/18 text-[#2962FF]"
            : "hover:bg-[var(--color-surface-2)]"
        }`}
      >
        <TvIcon name="drawginmode" size={21} />
      </button>

      {/* Lock All Drawings (data-name="lock-all") */}
      <button
        type="button"
        data-name="lock-all"
        title={lockAllDrawings ? "Mở khóa tất cả công cụ vẽ" : "Khóa tất cả công cụ vẽ"}
        onClick={onToggleLockAll}
        className={`flex h-[31px] w-[31px] items-center justify-center rounded transition-colors ${
          lockAllDrawings
            ? "bg-[#F57C00]/20 text-[#F57C00]"
            : "hover:bg-[var(--color-surface-2)]"
        }`}
      >
        <TvIcon name="lock-all" size={21} />
      </button>

      {/* Hide All Drawings (data-name="hide-all") */}
      <button
        type="button"
        data-name="hide-all"
        title={hideAllDrawings ? "Hiện lại các nét vẽ" : "Ẩn tất cả nét vẽ trên biểu đồ"}
        onClick={onToggleHideAll}
        className={`flex h-[31px] w-[31px] items-center justify-center rounded transition-colors ${
          hideAllDrawings
            ? "bg-[#2962FF]/18 text-[#2962FF]"
            : "hover:bg-[var(--color-surface-2)]"
        }`}
      >
        <TvIcon name="legend-show-hide-action" size={18} />
      </button>

      {/* Remove Objects Menu (data-name="removeAllDrawingTools") */}
      <div className="relative flex w-full justify-center">
        <button
          type="button"
          data-name="removeAllDrawingTools"
          title={`Xóa đối tượng (${drawingsCount} nét vẽ)`}
          onClick={() => {
            setRemoveMenuOpen((v) => !v)
            setOpenGroupId(null)
          }}
          className="flex h-[31px] w-[31px] items-center justify-center rounded hover:bg-[var(--color-loss-soft)] hover:text-[var(--color-loss)] transition-colors"
        >
          <TvIcon name="removeAllDrawingTools" size={21} />
        </button>

        {removeMenuOpen && (
          <div
            className={`absolute bottom-0 left-[44px] z-50 w-[220px] rounded-[6px] border p-1.5 shadow-xl text-[12px] ${
              isDark
                ? "border-[#2A2E39] bg-[#1E222D] text-[#D1D4DC]"
                : "border-[var(--color-line-strong)] bg-[var(--color-surface)] text-[var(--color-ink)]"
            }`}
          >
            <button
              type="button"
              onClick={() => {
                onClearDrawings()
                setRemoveMenuOpen(false)
              }}
              className="flex w-full items-center justify-between rounded px-2.5 py-1.5 text-left hover:bg-[var(--color-surface-2)]"
            >
              <span>Xóa tất cả công cụ vẽ</span>
              <span className="font-mono text-[11px] opacity-65">{drawingsCount}</span>
            </button>
            <button
              type="button"
              onClick={() => {
                onClearIndicators()
                setRemoveMenuOpen(false)
              }}
              className="flex w-full items-center justify-between rounded px-2.5 py-1.5 text-left hover:bg-[var(--color-surface-2)]"
            >
              <span>Xóa tất cả chỉ báo</span>
            </button>
            <button
              type="button"
              onClick={() => {
                onClearDrawings()
                onClearIndicators()
                setRemoveMenuOpen(false)
              }}
              className="flex w-full items-center justify-between rounded px-2.5 py-1.5 text-left font-semibold text-[var(--color-loss)] hover:bg-[var(--color-loss-soft)]"
            >
              <span>Xóa cả nét vẽ & chỉ báo</span>
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
