"use client"

import { EyeOff, FileStack, PanelLeft, PanelRightClose, SquarePen } from "lucide-react"
import type { MemoryMode } from "@/lib/api/researchChat"

interface TopControlBarProps {
  memoryMode: MemoryMode
  onMemoryModeChange: (mode: MemoryMode) => void
  memoryModeDisabled?: boolean
  sidebarOpen: boolean
  onToggleSidebar: () => void
  onStartNewConversation: () => void
  newConversationDisabled?: boolean
  drawerOpen: boolean
  onToggleDrawer: () => void
  evidenceCount: number
}

export function TopControlBar({
  memoryMode,
  onMemoryModeChange,
  memoryModeDisabled,
  sidebarOpen,
  onToggleSidebar,
  onStartNewConversation,
  newConversationDisabled,
  drawerOpen,
  onToggleDrawer,
  evidenceCount,
}: TopControlBarProps) {
  const isIncognito = memoryMode === "incognito"

  return (
    <header className="h-12 px-3.5 sm:px-5 flex items-center justify-between gap-3 bg-surface shrink-0">
      {/* Left: Only visible when conversation sidebar is collapsed */}
      <div className="flex items-center gap-1">
        {!sidebarOpen && (
          <>
            <button
              type="button"
              onClick={onToggleSidebar}
              aria-label="Mở lịch sử trò chuyện"
              title="Mở lịch sử trò chuyện"
              className="inline-flex h-8.5 w-8.5 items-center justify-center rounded-[9px] text-secondary hover:bg-soft hover:text-ink transition-colors cursor-pointer"
            >
              <PanelLeft className="h-[18px] w-[18px]" aria-hidden="true" />
            </button>
            <button
              type="button"
              disabled={newConversationDisabled}
              onClick={onStartNewConversation}
              aria-label="Trò chuyện mới"
              title="Trò chuyện mới"
              className="inline-flex h-8.5 w-8.5 items-center justify-center rounded-[9px] text-secondary hover:bg-soft hover:text-ink transition-colors disabled:opacity-40 cursor-pointer"
            >
              <SquarePen className="h-[17px] w-[17px]" aria-hidden="true" />
            </button>
          </>
        )}
      </div>

      {/* Right: Quiet Temporary-Chat toggle (like ChatGPT top-right) + Nguồn drawer toggle */}
      <div className="flex items-center justify-end gap-1.5">
        <button
          type="button"
          disabled={memoryModeDisabled}
          onClick={() => onMemoryModeChange(isIncognito ? "auto" : "incognito")}
          aria-pressed={isIncognito}
          aria-label={isIncognito ? "Tắt trò chuyện tạm thời" : "Bật trò chuyện tạm thời"}
          title={
            isIncognito
              ? "Đang bật trò chuyện tạm thời (không lưu ký ức)"
              : "Trò chuyện tạm thời (ẩn danh)"
          }
          className={`inline-flex items-center gap-1.5 rounded-full px-2.5 h-8 text-[12px] transition-colors cursor-pointer disabled:opacity-40 ${
            isIncognito
              ? "bg-ink text-paper font-medium"
              : "text-muted hover:bg-soft hover:text-ink"
          }`}
        >
          <EyeOff className="h-4 w-4 shrink-0" aria-hidden="true" />
          {isIncognito && <span>Tạm thời</span>}
        </button>

        {(evidenceCount > 0 || drawerOpen) && (
          <button
            type="button"
            onClick={onToggleDrawer}
            aria-expanded={drawerOpen}
            aria-label={drawerOpen ? "Đóng nguồn đối chiếu" : "Mở nguồn đối chiếu"}
            className={`inline-flex items-center gap-1.5 rounded-full px-3 h-8 text-[12.5px] font-medium transition-colors cursor-pointer ${
              drawerOpen
                ? "bg-soft text-ink"
                : "text-secondary hover:bg-soft hover:text-ink"
            }`}
          >
            {drawerOpen ? (
              <PanelRightClose className="h-4 w-4 shrink-0" aria-hidden="true" />
            ) : (
              <FileStack className="h-4 w-4 shrink-0" aria-hidden="true" />
            )}
            <span>Nguồn</span>
            {evidenceCount > 0 && (
              <span className="font-mono text-[11.5px] text-muted tnum">{evidenceCount}</span>
            )}
          </button>
        )}
      </div>
    </header>
  )
}
