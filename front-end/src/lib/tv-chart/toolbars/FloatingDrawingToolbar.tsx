"use client"

import React, { useState } from "react"
import { TvIcon } from "../icons/tv-icons"
import { TV_DRAWING_TOOL_MAP } from "../drawings/tool-groups"
import type { TvDrawingObject } from "../types"

export interface FloatingDrawingToolbarProps {
  drawing: TvDrawingObject
  isDark?: boolean
  onUpdate: (patch: Partial<TvDrawingObject>) => void
  onClone: () => void
  onDelete: () => void
  onClose: () => void
}

const TV_PALETTE = [
  "#2962FF", // TV Blue
  "#089981", // TV Bull Teal
  "#F23645", // TV Bear Coral
  "#FF9800", // Orange
  "#AB47BC", // Purple (HOSE Ceiling)
  "#00BCD4", // Cyan (HOSE Floor)
  "#EAB308", // Gold (HOSE Ref)
  "#EC407A", // Pink
  "#26A69A", // Emerald
  "#5C6BC0", // Indigo
  "#787B86", // Slate
  "#131722", // Charcoal
]

export const FloatingDrawingToolbar: React.FC<FloatingDrawingToolbarProps> = ({
  drawing,
  isDark = false,
  onUpdate,
  onClone,
  onDelete,
  onClose,
}) => {
  const [showColorMenu, setShowColorMenu] = useState(false)
  const [showTextEditor, setShowTextEditor] = useState(false)

  const toolDef = TV_DRAWING_TOOL_MAP[drawing.tool]

  const hexToRgba = (hex: string, alpha: number) => {
    const clean = hex.replace("#", "")
    if (clean.length !== 6) return `rgba(41, 98, 255, ${alpha})`
    const r = parseInt(clean.slice(0, 2), 16)
    const g = parseInt(clean.slice(2, 4), 16)
    const b = parseInt(clean.slice(4, 6), 16)
    return `rgba(${r}, ${g}, ${b}, ${alpha})`
  }

  return (
    <div
      data-name="floating-drawing-toolbar"
      className={`absolute top-3 left-1/2 -translate-x-1/2 z-30 flex items-center gap-1 px-2 py-1 rounded-lg border shadow-lg select-none transition-all ${
        isDark
          ? "bg-[#1E222D]/95 border-[#2A2E39] text-[#D1D4DC]"
          : "bg-white/95 border-[#E0E3EB] text-[#131722]"
      } backdrop-blur-md`}
      onMouseDown={(e) => e.stopPropagation()}
      onClick={(e) => e.stopPropagation()}
    >
      {/* Tool badge */}
      <div className="flex items-center gap-1.5 pr-2 border-r border-current/10 text-[11px] font-semibold">
        {toolDef && <TvIcon name={toolDef.iconName} size={16} className="text-[#2962FF]" />}
        <span className="max-w-[130px] truncate">{toolDef?.viName || drawing.tool}</span>
      </div>

      {/* Color Picker */}
      <div className="relative">
        <button
          type="button"
          onClick={() => setShowColorMenu((v) => !v)}
          title="Màu nét & nền"
          className="h-7 px-2 rounded flex items-center gap-1.5 hover:bg-black/5 dark:hover:bg-white/5"
        >
          <span
            className="w-4 h-4 rounded-sm border border-black/20 shadow-inner"
            style={{ backgroundColor: drawing.color }}
          />
          <span className="text-[10px] opacity-70">▼</span>
        </button>

        {showColorMenu && (
          <div
            className={`absolute left-0 top-full mt-1.5 p-2.5 rounded-lg border shadow-xl z-50 w-48 ${
              isDark
                ? "bg-[#1E222D] border-[#2A2E39] text-[#D1D4DC]"
                : "bg-white border-[#E0E3EB] text-[#131722]"
            }`}
          >
            <div className="text-[10px] font-semibold uppercase tracking-wider opacity-60 mb-1.5">
              Màu đường vẽ
            </div>
            <div className="grid grid-cols-6 gap-1.5 mb-2.5">
              {TV_PALETTE.map((c) => (
                <button
                  key={c}
                  type="button"
                  onClick={() => {
                    onUpdate({
                      color: c,
                      fillColor: hexToRgba(c, 0.14),
                    })
                    setShowColorMenu(false)
                  }}
                  className={`w-6 h-6 rounded border transition-transform hover:scale-110 ${
                    drawing.color.toUpperCase() === c.toUpperCase()
                      ? "ring-2 ring-[#2962FF] border-white"
                      : "border-black/15"
                  }`}
                  style={{ backgroundColor: c }}
                />
              ))}
            </div>
            <div className="flex items-center justify-between pt-1.5 border-t border-current/10 text-[11px]">
              <span>Tùy chỉnh:</span>
              <input
                type="color"
                value={drawing.color.startsWith("#") ? drawing.color : "#2962FF"}
                onChange={(e) =>
                  onUpdate({
                    color: e.target.value,
                    fillColor: hexToRgba(e.target.value, 0.14),
                  })
                }
                className="w-7 h-5 rounded cursor-pointer border-0 bg-transparent"
              />
            </div>
          </div>
        )}
      </div>

      {/* Line Width (1px - 4px) */}
      <div className="flex items-center gap-0.5 px-1 border-l border-current/10">
        {[1, 2, 3, 4].map((w) => (
          <button
            key={w}
            type="button"
            onClick={() => onUpdate({ lineWidth: w })}
            title={`Độ dày ${w}px`}
            className={`h-7 px-1.5 rounded text-[11px] font-mono transition-colors ${
              drawing.lineWidth === w
                ? "bg-[#2962FF]/15 text-[#2962FF] font-bold"
                : "hover:bg-black/5 dark:hover:bg-white/5 opacity-75"
            }`}
          >
            {w}px
          </button>
        ))}
      </div>

      {/* Line Style (Solid / Dashed / Dotted) */}
      <div className="flex items-center gap-0.5 px-1 border-l border-current/10">
        {(
          [
            { id: "solid", label: "━━", title: "Nét liền" },
            { id: "dashed", label: "╌╌", title: "Nét đứt" },
            { id: "dotted", label: "┈┈", title: "Nét chấm" },
          ] as const
        ).map((st) => (
          <button
            key={st.id}
            type="button"
            onClick={() => onUpdate({ lineStyle: st.id })}
            title={st.title}
            className={`h-7 px-1.5 rounded text-[11px] font-mono transition-colors ${
              drawing.lineStyle === st.id
                ? "bg-[#2962FF]/15 text-[#2962FF] font-bold"
                : "hover:bg-black/5 dark:hover:bg-white/5 opacity-75"
            }`}
          >
            {st.label}
          </button>
        ))}
      </div>

      {/* Extend Right Toggle */}
      <button
        type="button"
        onClick={() => onUpdate({ extendRight: !drawing.extendRight })}
        title="Kéo dài sang phải (Extend Right)"
        className={`h-7 px-2 rounded text-[11px] font-medium border-l border-current/10 transition-colors ${
          drawing.extendRight
            ? "bg-[#2962FF]/15 text-[#2962FF] font-semibold"
            : "hover:bg-black/5 dark:hover:bg-white/5 opacity-75"
        }`}
      >
        ⇥ Kéo dài
      </button>

      {/* Text Label Editor */}
      <div className="relative border-l border-current/10 pl-1">
        <button
          type="button"
          onClick={() => setShowTextEditor((v) => !v)}
          title="Chỉnh sửa văn bản / ghi chú"
          className={`h-7 px-2 rounded text-[11px] flex items-center gap-1 transition-colors ${
            drawing.text
              ? "bg-[#2962FF]/15 text-[#2962FF] font-semibold"
              : "hover:bg-black/5 dark:hover:bg-white/5 opacity-80"
          }`}
        >
          <span>T</span>
          <span className="hidden sm:inline">Nhãn</span>
        </button>

        {showTextEditor && (
          <div
            className={`absolute left-0 top-full mt-1.5 p-2 rounded-lg border shadow-xl z-50 w-56 flex items-center gap-1.5 ${
              isDark
                ? "bg-[#1E222D] border-[#2A2E39] text-[#D1D4DC]"
                : "bg-white border-[#E0E3EB] text-[#131722]"
            }`}
          >
            <input
              type="text"
              value={drawing.text ?? ""}
              onChange={(e) => onUpdate({ text: e.target.value })}
              placeholder="Nhập nhãn hiển thị..."
              className="flex-1 text-xs px-2 py-1 rounded border border-current/20 bg-transparent focus:outline-none focus:border-[#2962FF]"
              autoFocus
              onKeyDown={(e) => {
                if (e.key === "Enter") setShowTextEditor(false)
              }}
            />
            <button
              type="button"
              onClick={() => setShowTextEditor(false)}
              className="px-2 py-1 rounded text-[11px] bg-[#2962FF] text-white font-semibold"
            >
              OK
            </button>
          </div>
        )}
      </div>

      {/* Lock / Unlock */}
      <button
        type="button"
        onClick={() => onUpdate({ locked: !drawing.locked })}
        title={drawing.locked ? "Mở khóa đối tượng" : "Khóa vị trí đối tượng"}
        className={`h-7 w-7 rounded flex items-center justify-center border-l border-current/10 transition-colors ${
          drawing.locked
            ? "bg-amber-500/15 text-amber-600"
            : "hover:bg-black/5 dark:hover:bg-white/5 opacity-80"
        }`}
      >
        <TvIcon name="lock-all" size={16} />
      </button>

      {/* Clone */}
      <button
        type="button"
        onClick={onClone}
        title="Nhân bản đối tượng (Clone)"
        className="h-7 px-2 rounded text-[11px] hover:bg-black/5 dark:hover:bg-white/5 opacity-80"
      >
        ⧉
      </button>

      {/* Delete */}
      <button
        type="button"
        onClick={onDelete}
        title="Xóa đối tượng (Delete)"
        className="h-7 w-7 rounded flex items-center justify-center text-[#F23645] hover:bg-[#F23645]/10 transition-colors"
      >
        <TvIcon name="removeAllDrawingTools" size={16} />
      </button>

      {/* Deselect */}
      <button
        type="button"
        onClick={onClose}
        title="Đóng thanh công cụ (Esc)"
        className="h-7 w-6 rounded flex items-center justify-center text-xs opacity-60 hover:opacity-100"
      >
        ✕
      </button>
    </div>
  )
}
