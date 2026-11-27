"use client"

import { EyeOff, FileStack, PanelLeft, PanelRightClose, SquarePen } from "lucide-react"
import type { MemoryMode, ResearchChatMode } from "@/lib/api/researchChat"

interface TopControlBarProps {
  chatMode: ResearchChatMode
  onChatModeChange: (mode: ResearchChatMode) => void
  modeDisabled?: boolean
  memoryMode: MemoryMode
  sidebarOpen: boolean
  onToggleSidebar: () => void
  onStartNewConversation: () => void
  newConversationDisabled?: boolean
  drawerOpen: boolean
  onToggleDrawer: () => void
  evidenceCount: number
}

export function TopControlBar({
  chatMode,
  onChatModeChange,
  modeDisabled,
  memoryMode,
  sidebarOpen,
  onToggleSidebar,
  onStartNewConversation,
  newConversationDisabled,
  drawerOpen,
  onToggleDrawer,
  evidenceCount,
}: TopControlBarProps) {
  return (
    <header className="h-13 px-3.5 sm:px-5 flex items-center justify-between gap-3 bg-surface shrink-0">
      {/* Left: Only visible when conversation sidebar is collapsed */}
      <div className="flex items-center gap-1 min-w-[84px]">
        {!sidebarOpen && (
          <>
            <button
              type="button"
              onClick={onToggleSidebar}
              aria-label="Mở lịch sử trò chuyện"
              title="Mở lịch sử trò chuyện"
              className="inline-flex h-9 w-9 items-center justify-center rounded-[9px] text-secondary hover:bg-soft hover:text-ink transition-colors cursor-pointer"
            >
              <PanelLeft className="h-[18px] w-[18px]" aria-hidden="true" />
            </button>
            <button
              type="button"
              disabled={newConversationDisabled}
              onClick={onStartNewConversation}
              aria-label="Trò chuyện mới"
              title="Trò chuyện mới"
              className="inline-flex h-9 w-9 items-center justify-center rounded-[9px] text-secondary hover:bg-soft hover:text-ink transition-colors disabled:opacity-40 cursor-pointer"
            >
              <SquarePen className="h-[17px] w-[17px]" aria-hidden="true" />
            </button>
          </>
        )}
      </div>

      {/* Center: Clean ChatGPT-style 2-tab pill ("Trò chuyện" | "Chuyên sâu") */}
      <div
        role="tablist"
        aria-label="Chế độ phân tích"
        className="inline-flex items-center rounded-full bg-[#f1f3f1] p-1"
      >
        <button
          type="button"
          role="tab"
          aria-selected={chatMode === "GENERAL"}
          disabled={modeDisabled}
          onClick={() => onChatModeChange("GENERAL")}
          className={`rounded-full px-4 h-7.5 text-[13px] transition-all cursor-pointer disabled:opacity-50 ${
            chatMode === "GENERAL"
              ? "bg-surface text-ink font-medium shadow-[0_1px_3px_rgba(24,32,29,0.08)]"
              : "text-secondary hover:text-ink"
          }`}
        >
          Trò chuyện
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={chatMode === "MOAT"}
          disabled={modeDisabled}
          onClick={() => onChatModeChange("MOAT")}
          className={`rounded-full px-4 h-7.5 text-[13px] transition-all cursor-pointer disabled:opacity-50 ${
            chatMode === "MOAT"
              ? "bg-surface text-ink font-medium shadow-[0_1px_3px_rgba(24,32,29,0.08)]"
              : "text-secondary hover:text-ink"
          }`}
        >
          Chuyên sâu
        </button>
      </div>

      {/* Right: Quiet evidence drawer toggle only when active session has sources (or incognito badge) */}
      <div className="flex items-center justify-end gap-2 min-w-[84px]">
        {memoryMode === "incognito" && (
          <span
            title="Phiên ẩn danh đang bật"
            className="inline-flex items-center gap-1 rounded-full bg-soft px-2.5 h-7 text-[11.5px] text-secondary"
          >
            <EyeOff className="h-3.5 w-3.5" aria-hidden="true" />
            <span className="hidden sm:inline">Ẩn danh</span>
          </span>
        )}

        {(evidenceCount > 0 || drawerOpen) && (
          <button
            type="button"
            onClick={onToggleDrawer}
            aria-expanded={drawerOpen}
            aria-label={drawerOpen ? "Đóng ngăn bằng chứng" : "Mở ngăn bằng chứng"}
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
            <span className="hidden sm:inline">Nguồn</span>
            {evidenceCount > 0 && (
              <span className="font-mono text-[11.5px] text-muted tnum">{evidenceCount}</span>
            )}
          </button>
        )}
      </div>
    </header>
  )
}
