"use client"

import React, { useEffect, useMemo, useRef, useState } from "react"
import { createPortal } from "react-dom"
import { getIndicatorMetadata, registerCustomScripts, TV_INDICATOR_LIST } from "../indicators/registry"
import type {
  TvCustomScriptDef,
  TvDialogSidebarSection,
  TvFundamentalSubTab,
  TvIndicatorId,
  TvIndicatorInstance,
  TvIndicatorMetadata,
  TvTechnicalSubTab,
  TvTierBadge,
} from "../types"

export interface IndicatorsModalProps {
  isOpen: boolean
  isDark?: boolean
  activeIndicators: TvIndicatorInstance[]
  initialEditingInstanceId?: string | null
  favoriteIds?: string[]
  customScripts?: TvCustomScriptDef[]
  onClose: () => void
  onAddIndicator: (id: TvIndicatorId, customDef?: TvCustomScriptDef) => void
  onUpdateIndicator: (instanceId: string, patch: Partial<TvIndicatorInstance>) => void
  onRemoveIndicator: (instanceId: string) => void
  onResetDefaults: () => void
  onToggleFavorite?: (id: TvIndicatorId) => void
  onCreateCustomScript?: (script: TvCustomScriptDef) => void
  onDeleteCustomScript?: (scriptId: string) => void
}

/* ============================================================================
 * EXACT TRADINGVIEW DIALOG ICONS EXTRACTED VIA REA MCP (viewBox="0 0 28 28")
 * ============================================================================ */
const SidebarIcon: React.FC<{ section: TvDialogSidebarSection }> = ({ section }) => {
  switch (section) {
    case "my_scripts":
      return (
        <svg width={22} height={22} viewBox="0 0 28 28" className="shrink-0">
          <path
            fill="currentColor"
            d="M11 10.5c0-1.02.27-1.89.8-2.5.54-.6 1.39-1 2.7-1 1.31 0 2.16.4 2.7 1 .53.61.8 1.48.8 2.5s-.27 1.89-.8 2.5c-.54.6-1.39 1-2.7 1-1.31 0-2.16-.4-2.7-1a3.75 3.75 0 0 1-.8-2.5zM14.5 6c-1.53 0-2.68.49-3.44 1.34A4.67 4.67 0 0 0 10 10.5c0 1.19.31 2.32 1.06 3.16.76.85 1.91 1.34 3.44 1.34s2.68-.49 3.44-1.34A4.67 4.67 0 0 0 19 10.5c0-1.19-.31-2.32-1.06-3.16C17.18 6.49 16.03 6 14.5 6zM7 23c0-2.4 1.83-5 5-5h5c3.17 0 5 2.6 5 5h1c0-2.85-2.17-6-6-6h-5c-3.83 0-6 3.15-6 6h1z"
          />
        </svg>
      )
    case "purchased":
      return (
        <svg width={22} height={22} viewBox="0 0 28 28" className="shrink-0">
          <path
            fill="currentColor"
            d="M18 9h4.999v14.002H4V9.009L18 4zM5 22.002h16.999v-2h-2.998a4.001 4.001 0 0 1 0-8.002h2.998v-2H5zM19.001 13a3.002 3.002 0 0 0 0 6.003h2.998V13zM19 15a1 1 0 1 1 0 2 1 1 0 0 1 0-2M6.993 9H17V5.42z"
          />
        </svg>
      )
    case "technicals":
      return (
        <svg width={22} height={22} viewBox="0 0 28 28" className="shrink-0">
          <path
            fill="currentColor"
            d="m23.53 7.53-5.09 5.09a2.75 2.75 0 0 1-3.88 0l-3.18-3.18a1.25 1.25 0 0 0-1.76 0l-5.09 5.09-1.06-1.06 5.09-5.09a2.75 2.75 0 0 1 3.88 0l3.18 3.18c.48.48 1.28.48 1.76 0l5.09-5.09 1.06 1.06ZM11.25 21.99V22h-1.5V14h1.5V22Zm12 0V22h-1.5V14h1.5V22Zm-15 0V22h-1.5V16h1.5V22Zm6 0V22h-1.5V16h1.5V22Zm6 0V22h-1.5V16h1.5V22Zm-3 0V22h-1.5V18h1.5V22Zm-12 0V22h-1.5V19h1.5V22Z"
          />
        </svg>
      )
    case "fundamentals":
      return (
        <svg width={22} height={22} viewBox="0 0 28 28" className="shrink-0">
          <path
            fill="currentColor"
            fillRule="evenodd"
            d="M17.5 7H17v6h-3v-3H9v6H5v6h17V7h-4.5Zm.5 14h3V8h-3v13Zm-1 0v-7h-3v7h3Zm-4-7.5V21h-3V11h3v2.5ZM9 21v-4H6v4h3Z"
          />
        </svg>
      )
    case "editors_picks":
      return (
        <svg width={22} height={22} viewBox="0 0 28 28" className="shrink-0">
          <path fill="currentColor" d="m21 23-7-5.37L7 23V6h14zM8 20.984l6-4.615 6 4.615V7H8z" />
        </svg>
      )
    case "top":
      return (
        <svg width={22} height={22} viewBox="0 0 28 28" className="shrink-0">
          <path
            fill="currentColor"
            d="M22.002 23H5v-4h4v-3h4v-4h4V8h5.002zM6 22h3v-2H6zm4 0h3v-5h-3zm4 0h3v-9h-3zm4 0h3.002V9H18zM15 9h-1V6.665l-8.342 7.339-.66-.75L13.244 6H11V5h4z"
          />
        </svg>
      )
    case "trending":
      return (
        <svg width={22} height={22} viewBox="0 0 28 28" className="shrink-0">
          <path
            fill="currentColor"
            fillRule="evenodd"
            d="M12.184 2.112a.5.5 0 0 1 .535-.061c1.825.89 3.823 2.451 5.024 4.597 1.112 1.984 1.528 4.446.527 7.265a2.47 2.47 0 0 0 1.614-1.196c.446-.781.545-1.878.143-3.055a.5.5 0 0 1 .822-.52l.088.086c.85.828 1.836 1.79 2.497 3.08.695 1.357 1.016 3.044.556 5.292-.705 3.44-3.17 6.396-6.342 7.378a.5.5 0 0 1-.372-.03c-1.03-.516-1.867-1.085-2.436-1.955-.433-.662-.686-1.461-.788-2.47-.752.68-1.234 1.299-1.54 1.87-.394.736-.512 1.422-.512 2.107a.5.5 0 0 1-.623.485c-3.382-.86-6.083-3.428-7.053-6.987-.693-2.547-.107-5.096 1.087-7.21 1.192-2.11 3.013-3.832 4.87-4.737a.5.5 0 0 1 .664.677c-.631 1.232-.563 1.822-.473 2.057l.01.026c.888-.905 1.43-1.649 1.695-2.48.294-.925.268-2.032-.162-3.707a.5.5 0 0 1 .169-.512m-1.84 7.863h-.002l-.005-.002-.01-.004a1 1 0 0 1-.105-.045 1.3 1.3 0 0 1-.215-.137 1.5 1.5 0 0 1-.468-.643c-.145-.375-.177-.855-.029-1.472a11.3 11.3 0 0 0-3.228 3.608c-1.099 1.945-1.601 4.222-.993 6.455.817 2.998 2.988 5.204 5.745 6.11a5.2 5.2 0 0 1 .596-1.924c.483-.902 1.288-1.828 2.563-2.816a.5.5 0 0 1 .807.39c.011 1.41.253 2.302.677 2.95.399.61.994 1.062 1.865 1.515 2.692-.919 4.841-3.503 5.468-6.56.418-2.04.119-3.495-.466-4.637-.341-.665-.783-1.235-1.266-1.764.02.808-.158 1.57-.526 2.214C20.13 14.305 18.99 15 17.5 15a.5.5 0 0 1-.458-.701c1.253-2.853.895-5.26-.171-7.163-.88-1.57-2.253-2.815-3.647-3.668.22 1.234.2 2.242-.094 3.166-.374 1.176-1.17 2.14-2.281 3.224a.5.5 0 0 1-.503.118z"
          />
        </svg>
      )
    case "store":
      return (
        <svg width={22} height={22} viewBox="0 0 28 28" className="shrink-0">
          <path
            fill="currentColor"
            d="M14 2a4 4 0 0 1 4 4v1h4l1 17H5L6 7h4V6a4 4 0 0 1 4-4M6.06 23h15.88l-.882-15H18v3h-1V8h-6v3h-1V8H6.942zM14 3a3 3 0 0 0-3 3v1h6V6a3 3 0 0 0-3-3"
          />
        </svg>
      )
  }
}

const SIDEBAR_GROUPS: {
  title: string
  items: { id: TvDialogSidebarSection; label: string }[]
}[] = [
  {
    title: "CÁ NHÂN",
    items: [
      { id: "my_scripts", label: "Tập lệnh của tôi" },
      { id: "purchased", label: "Đã mua" },
    ],
  },
  {
    title: "TÍCH HỢP SẴN",
    items: [
      { id: "technicals", label: "Chỉ số Kỹ thuật" },
      { id: "fundamentals", label: "Cơ bản" },
    ],
  },
  {
    title: "CỘNG ĐỒNG",
    items: [
      { id: "editors_picks", label: "Biên tập viên chọn" },
      { id: "top", label: "Hàng đầu" },
      { id: "trending", label: "Xu hướng" },
      { id: "store", label: "Chợ" },
    ],
  },
]

const TECHNICAL_SUBTABS: { id: TvTechnicalSubTab; label: string }[] = [
  { id: "indicators", label: "Các chỉ báo" },
  { id: "strategies", label: "Chiến lược" },
  { id: "profiles", label: "Hồ sơ" },
  { id: "patterns", label: "Mô hình" },
  { id: "options", label: "Options" },
]

const FUNDAMENTAL_SUBTABS: { id: TvFundamentalSubTab; label: string }[] = [
  { id: "all", label: "Tất cả" },
  { id: "valuation", label: "Định giá" },
  { id: "income", label: "Kết quả kinh doanh" },
  { id: "balance", label: "Bảng cân đối" },
  { id: "cashflow", label: "Dòng tiền & Cổ tức" },
]

const PRESET_PINE_FORMULAS: { name: string; formula: string; overlay: boolean; color: string }[] = [
  {
    name: "EMA Ribbon Spread (13 - 34)",
    formula: "ema(close, 13) - ema(close, 34)",
    overlay: false,
    color: "#2962FF",
  },
  {
    name: "Dynamic Keltner Upper (HMA20 + 2*ATR)",
    formula: "hma(close, 20) + 2 * atr(14)",
    overlay: true,
    color: "#089981",
  },
  {
    name: "Normalized RSI Momentum",
    formula: "(rsi(close, 14) - 50) * 2",
    overlay: false,
    color: "#7E57C2",
  },
]

function renderTierBadge(badge?: TvTierBadge, isDark = false) {
  if (!badge || badge === "FREE") return null
  if (badge === "BETA") {
    return (
      <span
        className={`px-1.5 py-0.5 rounded text-[9.5px] font-bold tracking-wider uppercase shrink-0 ${
          isDark ? "bg-[#2A2E39] text-[#B2B5BE]" : "bg-[#E9ECF2] text-[#4A505E]"
        }`}
      >
        BETA
      </span>
    )
  }
  const styles: Record<string, string> = {
    PRO: "bg-[#2962FF]/12 text-[#2962FF]",
    PREMIUM: "bg-[#7E57C2]/14 text-[#7E57C2]",
    ULTIMATE: "bg-amber-500/15 text-amber-600 dark:text-amber-400",
  }
  return (
    <span
      title={`Tính năng ${badge} đã được mở khóa 100%`}
      className={`px-1.5 py-0.5 rounded text-[9.5px] font-bold tracking-wider uppercase shrink-0 ${
        styles[badge] || styles.PRO
      }`}
    >
      {badge}
    </span>
  )
}

function fmtBoostLikes(likes?: number): string {
  if (!likes) return ""
  if (likes >= 1000) return `${(likes / 1000).toFixed(1)}K`
  return String(likes)
}

export const IndicatorsModal: React.FC<IndicatorsModalProps> = ({
  isOpen,
  isDark = false,
  activeIndicators,
  initialEditingInstanceId = null,
  favoriteIds,
  customScripts,
  onClose,
  onAddIndicator,
  onUpdateIndicator,
  onRemoveIndicator,
  onResetDefaults,
  onToggleFavorite,
  onCreateCustomScript,
  onDeleteCustomScript,
}) => {
  const [mounted, setMounted] = useState(false)
  const [search, setSearch] = useState("")
  const [sidebarSection, setSidebarSection] = useState<TvDialogSidebarSection>("technicals")
  const [techSubTab, setTechSubTab] = useState<TvTechnicalSubTab>("indicators")
  const [patternFilter, setPatternFilter] = useState<"all" | "chart" | "candlestick">("all")
  const [fundSubTab, setFundSubTab] = useState<TvFundamentalSubTab>("all")
  const [myScriptsTab, setMyScriptsTab] = useState<"custom" | "favorites" | "active">("custom")
  const [expandedIds, setExpandedIds] = useState<Record<string, boolean>>({})

  // Fallback internal state if parent omits favorites / customScripts
  const [localFavs, setLocalFavs] = useState<string[]>([
    "SMA",
    "EMA",
    "BB",
    "RSI",
    "MACD",
    "VP_VISIBLE",
    "STRAT_SUPERTREND",
  ])
  const [localCustom, setLocalCustom] = useState<TvCustomScriptDef[]>([])

  // Custom Pine Formula Builder state
  const [newScriptName, setNewScriptName] = useState("Custom Trend Spread")
  const [newScriptFormula, setNewScriptFormula] = useState("ema(close, 13) - ema(close, 34)")
  const [newScriptOverlay, setNewScriptOverlay] = useState(false)
  const [newScriptColor, setNewScriptColor] = useState("#2962FF")

  const searchInputRef = useRef<HTMLInputElement | null>(null)

  const effFavs = favoriteIds ?? localFavs
  const effCustom = customScripts ?? localCustom

  useEffect(() => {
    setMounted(true)
  }, [])

  useEffect(() => {
    if (effCustom.length > 0) {
      registerCustomScripts(effCustom)
    }
  }, [effCustom])

  useEffect(() => {
    if (isOpen) {
      window.setTimeout(() => searchInputRef.current?.focus(), 40)
    }
  }, [isOpen])

  // Jump directly to parameter editor when user clicks ⚙ Settings on a PaneLegend item
  useEffect(() => {
    if (!isOpen || !initialEditingInstanceId) return
    const target = activeIndicators.find((a) => a.instanceId === initialEditingInstanceId)
    if (!target) return
    const meta = getIndicatorMetadata(target.id, target)
    setExpandedIds((prev) => ({ ...prev, [target.id]: true }))
    if (meta.section) {
      setSidebarSection(meta.section)
      if (meta.section === "technicals" && meta.subTab) setTechSubTab(meta.subTab)
      if (meta.section === "fundamentals") setFundSubTab(meta.fundamentalGroup || "all")
      if (meta.section === "my_scripts") setMyScriptsTab("active")
    }
  }, [isOpen, initialEditingInstanceId, activeIndicators])

  const handleToggleFav = (id: string, e: React.MouseEvent) => {
    e.stopPropagation()
    if (onToggleFavorite) {
      onToggleFavorite(id)
    } else {
      setLocalFavs((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]))
    }
  }

  const handleCreateCustom = (e: React.FormEvent) => {
    e.preventDefault()
    if (!newScriptName.trim() || !newScriptFormula.trim()) return
    const created: TvCustomScriptDef = {
      id: `CUSTOM_${Date.now()}`,
      name: newScriptName.trim(),
      shortName: newScriptName.trim().slice(0, 14),
      description: `Công thức Pine: ${newScriptFormula.trim()}`,
      formula: newScriptFormula.trim(),
      overlay: newScriptOverlay,
      color: newScriptColor,
      createdAt: Date.now(),
    }
    registerCustomScripts([created])
    if (onCreateCustomScript) {
      onCreateCustomScript(created)
    } else {
      setLocalCustom((prev) => [created, ...prev])
    }
    onAddIndicator(created.id, created)
  }

  const displayedCatalog = useMemo(() => {
    const q = search.trim().toLowerCase()
    const baseList = TV_INDICATOR_LIST

    // Global instant search across all 280+ items when search query is non-empty
    if (q.length > 0) {
      return baseList
        .filter(
          (m) =>
            m.name.toLowerCase().includes(q) ||
            m.viName.toLowerCase().includes(q) ||
            m.shortName.toLowerCase().includes(q) ||
            m.id.toLowerCase().includes(q) ||
            m.description.toLowerCase().includes(q) ||
            (m.author && m.author.toLowerCase().includes(q)) ||
            (m.tvScriptId && m.tvScriptId.toLowerCase().includes(q))
        )
        .sort((a, b) => {
          const aStarts = a.name.toLowerCase().startsWith(q) ? 0 : 1
          const bStarts = b.name.toLowerCase().startsWith(q) ? 0 : 1
          if (aStarts !== bStarts) return aStarts - bStarts
          return a.name.localeCompare(b.name)
        })
    }

    if (sidebarSection === "my_scripts") {
      if (myScriptsTab === "favorites") {
        return baseList.filter((m) => effFavs.includes(m.id))
      }
      if (myScriptsTab === "active") {
        return baseList.filter((m) => activeIndicators.some((a) => a.id === m.id))
      }
      return baseList.filter((m) => m.category === "custom")
    }

    if (sidebarSection === "technicals") {
      const list = baseList.filter(
        (m) => (m.section || "technicals") === "technicals" && (m.subTab || "indicators") === techSubTab
      )
      if (techSubTab === "patterns") {
        if (patternFilter === "chart") return list.filter((m) => m.category === "pattern")
        if (patternFilter === "candlestick") return list.filter((m) => m.category === "candlestick")
        return list
      }
      // Sort A-Z strictly like TradingView screenshot (24-hour volume -> Accumulation/distribution -> ...)
      return [...list].sort((a, b) => a.name.localeCompare(b.name, "en", { sensitivity: "base" }))
    }

    if (sidebarSection === "fundamentals") {
      const list = baseList.filter((m) => m.section === "fundamentals")
      if (fundSubTab === "all") return list
      return list.filter((m) => m.fundamentalGroup === fundSubTab)
    }

    return baseList.filter((m) => m.section === sidebarSection)
  }, [
    search,
    sidebarSection,
    techSubTab,
    patternFilter,
    fundSubTab,
    myScriptsTab,
    effFavs,
    activeIndicators,
    effCustom.length,
  ])

  if (!isOpen || !mounted) return null

  const activePillClass = isDark
    ? "bg-[#F0F3FA] text-[#131722] font-semibold"
    : "bg-[#131722] text-white font-semibold"
  const inactivePillClass = isDark
    ? "bg-[#2A2E39] text-[#D1D4DC] hover:bg-[#363A45] font-medium"
    : "bg-[#F0F3FA] text-[#131722] hover:bg-[#E0E3EB] font-medium"

  const modalContent = (
    <div
      className="fixed inset-0 z-[9999] flex items-center justify-center bg-black/45 backdrop-blur-[2px] p-3 sm:p-5"
      onClick={onClose}
    >
      <div
        data-name="indicators-dialog"
        role="dialog"
        aria-label="Chỉ báo, chỉ số và chiến lược"
        onClick={(e) => e.stopPropagation()}
        className={`w-full max-w-[920px] h-[82vh] max-h-[740px] rounded-xl border shadow-2xl overflow-hidden flex flex-col ${
          isDark
            ? "bg-[#1E222D] border-[#2A2E39] text-[#D1D4DC]"
            : "bg-white border-[#E0E3EB] text-[#131722]"
        }`}
      >
        {/* 1. TradingView Exact Header */}
        <div className="px-6 pt-5 pb-3 flex items-center justify-between gap-3 shrink-0">
          <div className="flex items-center gap-3 min-w-0">
            <h2 className="text-[20px] font-bold tracking-tight truncate">
              Chỉ báo, chỉ số và chiến lược
            </h2>
            <span
              className={`hidden sm:inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10.5px] font-semibold ${
                isDark ? "bg-[#2962FF]/20 text-[#5B8CFF]" : "bg-[#2962FF]/10 text-[#2962FF]"
              }`}
            >
              <span>⚡ {TV_INDICATOR_LIST.length} Tập lệnh</span>
              <span>· Full Pro/Ultra</span>
            </span>
          </div>

          <div className="flex items-center gap-2 shrink-0">
            {activeIndicators.length > 0 && (
              <button
                type="button"
                onClick={() => {
                  setSearch("")
                  setSidebarSection("my_scripts")
                  setMyScriptsTab("active")
                }}
                className={`h-7 px-2.5 rounded-full text-[11.5px] font-semibold transition-colors ${
                  isDark
                    ? "bg-[#2A2E39] hover:bg-[#363A45] text-[#D1D4DC]"
                    : "bg-[#F0F3FA] hover:bg-[#E0E3EB] text-[#131722]"
                }`}
              >
                Đang bật ({activeIndicators.length})
              </button>
            )}
            <button
              type="button"
              onClick={onResetDefaults}
              title="Khôi phục bộ chỉ báo mặc định (SMA 20, EMA 50, Volume)"
              className="h-7 px-2.5 rounded-full text-[11.5px] font-medium opacity-70 hover:opacity-100 hover:bg-current/5 transition-opacity"
            >
              Mặc định
            </button>
            <button
              type="button"
              onClick={onClose}
              aria-label="Đóng"
              className="w-8 h-8 rounded-lg flex items-center justify-center opacity-70 hover:opacity-100 hover:bg-current/10 transition-colors"
            >
              <svg width={14} height={14} viewBox="0 0 14 14">
                <path
                  stroke="currentColor"
                  strokeWidth="1.3"
                  d="m1.5 1.5 11 11m0-11-11 11"
                  vectorEffect="non-scaling-stroke"
                />
              </svg>
            </button>
          </div>
        </div>

        {/* 2. Full-Width Search Input (Exact REA Search SVG) */}
        <div className="px-6 pb-3.5 border-b border-current/10 shrink-0">
          <div
            className={`h-10 px-3 rounded-lg border flex items-center gap-2.5 transition-colors ${
              isDark
                ? "bg-[#131722] border-[#2A2E39] focus-within:border-[#2962FF]"
                : "bg-white border-[#E0E3EB] focus-within:border-[#2962FF]"
            }`}
          >
            <svg width={20} height={20} viewBox="0 0 28 28" className="opacity-55 shrink-0">
              <path
                fill="currentColor"
                d="M12.182 4a8.18 8.18 0 0 1 6.29 13.412l5.526 5.525-1.06 1.06-5.527-5.525A8.182 8.182 0 1 1 12.181 4m0 1.5a6.681 6.681 0 1 0 0 13.363 6.681 6.681 0 0 0 0-13.363"
              />
            </svg>
            <input
              ref={searchInputRef}
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Tìm kiếm"
              className="flex-1 bg-transparent text-[14px] outline-none placeholder:opacity-50"
            />
            {search && (
              <button
                type="button"
                onClick={() => setSearch("")}
                className="px-1.5 py-0.5 rounded text-[11px] opacity-60 hover:opacity-100"
              >
                ✕
              </button>
            )}
          </div>
        </div>

        {/* 3. Split Body: Left Sidebar + Right Catalog */}
        <div className="flex-1 flex min-h-0">
          {/* Left Navigation Sidebar */}
          <div className="w-[215px] sm:w-[232px] border-r border-current/10 py-3 px-2.5 overflow-y-auto shrink-0 select-none">
            {SIDEBAR_GROUPS.map((grp, gIdx) => (
              <div key={grp.title} className={gIdx > 0 ? "mt-4" : ""}>
                <div className="px-3 pb-1.5 text-[11px] font-semibold uppercase tracking-wider text-[#787B86]">
                  {grp.title}
                </div>
                <div className="space-y-0.5">
                  {grp.items.map((item) => {
                    const isSelected = !search.trim() && sidebarSection === item.id
                    return (
                      <button
                        key={item.id}
                        type="button"
                        onClick={() => {
                          setSearch("")
                          setSidebarSection(item.id)
                        }}
                        className={`w-full h-9 px-3 rounded-lg flex items-center gap-2.5 text-[13.5px] transition-colors ${
                          isSelected
                            ? isDark
                              ? "bg-[#2A2E39] text-white font-semibold"
                              : "bg-[#F0F3FA] text-[#131722] font-semibold"
                            : "hover:bg-current/[0.05] font-normal opacity-90"
                        }`}
                      >
                        <SidebarIcon section={item.id} />
                        <span className="truncate">{item.label}</span>
                      </button>
                    )
                  })}
                </div>
              </div>
            ))}
          </div>

          {/* Right Script List & Sub-Tabs */}
          <div className="flex-1 flex flex-col min-w-0">
            {/* Sub-Tabs Bar */}
            {!search.trim() && sidebarSection === "technicals" && (
              <div className="px-6 pt-3.5 pb-2 shrink-0 space-y-2">
                <div className="flex items-center gap-2 flex-wrap">
                  {TECHNICAL_SUBTABS.map((tab) => (
                    <button
                      key={tab.id}
                      type="button"
                      onClick={() => setTechSubTab(tab.id)}
                      className={`h-8 px-3.5 rounded-full text-[13px] transition-colors ${
                        techSubTab === tab.id ? activePillClass : inactivePillClass
                      }`}
                    >
                      {tab.label}
                    </button>
                  ))}
                </div>

                {techSubTab === "patterns" && (
                  <div className="flex items-center gap-1.5 pt-0.5 text-[11.5px]">
                    {[
                      { id: "all" as const, label: "Tất cả (64)" },
                      { id: "chart" as const, label: "Mô hình Biểu đồ Auto (19)" },
                      { id: "candlestick" as const, label: "Mẫu nến Nhật Bản (45)" },
                    ].map((pf) => (
                      <button
                        key={pf.id}
                        type="button"
                        onClick={() => setPatternFilter(pf.id)}
                        className={`h-6 px-2.5 rounded-md font-medium transition-colors ${
                          patternFilter === pf.id
                            ? "bg-[#2962FF]/15 text-[#2962FF] font-semibold"
                            : "opacity-70 hover:opacity-100 hover:bg-current/5"
                        }`}
                      >
                        {pf.label}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}

            {!search.trim() && sidebarSection === "fundamentals" && (
              <div className="px-6 pt-3.5 pb-2 flex items-center gap-2 flex-wrap shrink-0">
                {FUNDAMENTAL_SUBTABS.map((tab) => (
                  <button
                    key={tab.id}
                    type="button"
                    onClick={() => setFundSubTab(tab.id)}
                    className={`h-8 px-3.5 rounded-full text-[13px] transition-colors ${
                      fundSubTab === tab.id ? activePillClass : inactivePillClass
                    }`}
                  >
                    {tab.label}
                  </button>
                ))}
              </div>
            )}

            {!search.trim() && sidebarSection === "my_scripts" && (
              <div className="px-6 pt-3.5 pb-2 flex items-center gap-2 flex-wrap shrink-0">
                {[
                  { id: "custom" as const, label: `Công thức tự tạo (${effCustom.length})` },
                  { id: "favorites" as const, label: `Yêu thích ★ (${effFavs.length})` },
                  { id: "active" as const, label: `Đang bật (${activeIndicators.length})` },
                ].map((tab) => (
                  <button
                    key={tab.id}
                    type="button"
                    onClick={() => setMyScriptsTab(tab.id)}
                    className={`h-8 px-3.5 rounded-full text-[13px] transition-colors ${
                      myScriptsTab === tab.id ? activePillClass : inactivePillClass
                    }`}
                  >
                    {tab.label}
                  </button>
                ))}
              </div>
            )}

            {/* Mini-Pine Custom Formula Studio inside Tập lệnh của tôi -> Công thức tự tạo */}
            {!search.trim() && sidebarSection === "my_scripts" && myScriptsTab === "custom" && (
              <form
                onSubmit={handleCreateCustom}
                className={`mx-6 mt-2 mb-2 p-3.5 rounded-xl border space-y-2.5 shrink-0 ${
                  isDark ? "bg-[#131722] border-[#2A2E39]" : "bg-[#F8F9FD] border-[#E0E3EB]"
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="text-[12px] font-bold">
                    ⚡ Mini-Pine Formula Studio (Tạo chỉ báo riêng trực tiếp)
                  </div>
                  <div className="flex items-center gap-1 flex-wrap">
                    {PRESET_PINE_FORMULAS.map((pr) => (
                      <button
                        key={pr.name}
                        type="button"
                        onClick={() => {
                          setNewScriptName(pr.name)
                          setNewScriptFormula(pr.formula)
                          setNewScriptOverlay(pr.overlay)
                          setNewScriptColor(pr.color)
                        }}
                        className="px-2 py-0.5 rounded text-[10px] font-medium bg-[#2962FF]/10 text-[#2962FF] hover:bg-[#2962FF]/20"
                      >
                        Mẫu: {pr.name.split("(")[0].trim()}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-12 gap-2">
                  <input
                    type="text"
                    value={newScriptName}
                    onChange={(e) => setNewScriptName(e.target.value)}
                    placeholder="Tên chỉ báo..."
                    className={`sm:col-span-4 h-8 px-2.5 rounded-lg border text-[12px] outline-none ${
                      isDark ? "bg-[#1E222D] border-[#2A2E39]" : "bg-white border-[#E0E3EB]"
                    }`}
                  />
                  <input
                    type="text"
                    value={newScriptFormula}
                    onChange={(e) => setNewScriptFormula(e.target.value)}
                    placeholder="VD: ema(close, 13) - ema(close, 34)"
                    className={`sm:col-span-8 h-8 px-2.5 rounded-lg border font-mono text-[12px] outline-none ${
                      isDark ? "bg-[#1E222D] border-[#2A2E39]" : "bg-white border-[#E0E3EB]"
                    }`}
                  />
                </div>

                <div className="flex items-center justify-between gap-2 text-[11.5px]">
                  <div className="flex items-center gap-3">
                    <label className="flex items-center gap-1.5 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={newScriptOverlay}
                        onChange={(e) => setNewScriptOverlay(e.target.checked)}
                      />
                      <span>Vẽ đè trên nến (Overlay)</span>
                    </label>
                    <label className="flex items-center gap-1.5 cursor-pointer">
                      <span>Màu:</span>
                      <input
                        type="color"
                        value={newScriptColor}
                        onChange={(e) => setNewScriptColor(e.target.value)}
                        className="w-5 h-5 rounded cursor-pointer border-0 bg-transparent"
                      />
                    </label>
                  </div>
                  <button
                    type="submit"
                    className="h-7 px-3 rounded-lg bg-[#2962FF] hover:bg-[#1E53E5] text-white text-[11.5px] font-semibold"
                  >
                    + Lưu & Vẽ lên biểu đồ
                  </button>
                </div>
              </form>
            )}

            {/* Column Header Row (Exact TÊN TẬP LỆNH) */}
            <div className="px-6 pt-2 pb-1.5 flex items-center justify-between text-[11px] font-semibold uppercase tracking-wider text-[#787B86] shrink-0">
              <span>
                TÊN TẬP LỆNH{" "}
                <span className="font-mono opacity-75">({displayedCatalog.length})</span>
              </span>
              <span>
                {sidebarSection === "editors_picks" ||
                sidebarSection === "top" ||
                sidebarSection === "trending" ||
                sidebarSection === "store" ||
                sidebarSection === "purchased"
                  ? "TÁC GIẢ · LƯỢT BOOST"
                  : "THAO TÁC"}
              </span>
            </div>

            {/* Scrollable Script List */}
            <div className="flex-1 overflow-y-auto px-3 pb-4">
              {displayedCatalog.length === 0 ? (
                <div className="py-14 text-center text-[13px] opacity-60">
                  Không tìm thấy tập lệnh phù hợp. Hãy thử từ khóa khác hoặc tạo mới trong Tập lệnh của tôi.
                </div>
              ) : (
                <div className="space-y-0.5">
                  {displayedCatalog.map((meta: TvIndicatorMetadata) => {
                    const activeMatches = activeIndicators.filter((a) => a.id === meta.id)
                    const isActive = activeMatches.length > 0
                    const isFav = effFavs.includes(meta.id)
                    const isExpanded = Boolean(expandedIds[meta.id])
                    const canAddMore = Boolean(meta.allowMultiple) || !isActive

                    const handleRowPrimaryClick = () => {
                      if (canAddMore) {
                        onAddIndicator(meta.id)
                      } else if (activeMatches[0]) {
                        onRemoveIndicator(activeMatches[0].instanceId)
                      }
                    }

                    return (
                      <div
                        key={meta.id}
                        className={`rounded-lg transition-colors ${
                          isExpanded
                            ? isDark
                              ? "bg-[#262B38]"
                              : "bg-[#F5F7FC]"
                            : ""
                        }`}
                      >
                        <div
                          onClick={handleRowPrimaryClick}
                          title={`${meta.viName} — ${meta.description}`}
                          className={`min-h-[37px] px-3 py-1.5 rounded-lg flex items-center justify-between gap-3 cursor-pointer group transition-colors ${
                            isDark ? "hover:bg-[#2A2E39]/80" : "hover:bg-[#F0F3FA]/85"
                          }`}
                        >
                          {/* Left: Favorite Star + Official Script Name + Tier Badge + Subtle Vietnamese Hint */}
                          <div className="flex items-center gap-2 min-w-0 flex-1">
                            <button
                              type="button"
                              onClick={(e) => handleToggleFav(meta.id, e)}
                              aria-label={`${isFav ? "Bỏ yêu thích" : "Yêu thích"} ${meta.name}`}
                              title={isFav ? "Bỏ khỏi Yêu thích" : "Thêm vào Yêu thích"}
                              className={`w-5 h-5 flex items-center justify-center rounded text-[13px] shrink-0 transition-opacity ${
                                isFav
                                  ? "text-amber-400 opacity-100"
                                  : "opacity-0 group-hover:opacity-55 hover:!opacity-100"
                              }`}
                            >
                              {isFav ? "★" : "☆"}
                            </button>

                            <div className="min-w-0 flex items-center gap-2 overflow-hidden">
                              <span
                                className={`text-[13.5px] leading-snug truncate shrink-0 max-w-[340px] ${
                                  isActive ? "font-semibold text-[#2962FF]" : "font-normal"
                                }`}
                              >
                                {meta.name}
                              </span>
                              <span className="shrink-0">{renderTierBadge(meta.badge, isDark)}</span>
                              {meta.viName && meta.viName !== meta.name && (
                                <span className="hidden md:inline min-w-0 text-[11.5px] text-[#787B86] truncate opacity-0 group-hover:opacity-90 transition-opacity">
                                  · {meta.viName}
                                </span>
                              )}
                            </div>
                          </div>

                          {/* Right: Author/Boosts + Active State + Explicit Add / Settings / Remove */}
                          <div
                            className="flex items-center gap-1.5 shrink-0"
                            onClick={(e) => e.stopPropagation()}
                          >
                            {meta.author && (
                              <span className="hidden sm:inline-flex items-center gap-1.5 text-[11.5px] text-[#787B86] mr-1">
                                <span className="truncate max-w-[115px]">bởi {meta.author}</span>
                                {meta.likes && (
                                  <span className="font-mono text-[10.5px] px-1.5 py-0.5 rounded bg-current/[0.06]">
                                    🚀 {fmtBoostLikes(meta.likes)}
                                  </span>
                                )}
                              </span>
                            )}

                            {isActive && (
                              <>
                                <span className="px-1.5 py-0.5 rounded text-[10.5px] font-semibold bg-[#089981]/14 text-[#089981]">
                                  Đang bật{activeMatches.length > 1 ? ` (${activeMatches.length})` : ""}
                                </span>
                                <button
                                  type="button"
                                  aria-label={`Tham số ${meta.name}`}
                                  onClick={() =>
                                    setExpandedIds((prev) => ({
                                      ...prev,
                                      [meta.id]: !prev[meta.id],
                                    }))
                                  }
                                  className={`h-6 px-2 rounded text-[11px] font-medium transition-colors ${
                                    isExpanded
                                      ? "bg-[#2962FF] text-white"
                                      : "bg-current/[0.07] hover:bg-current/[0.14]"
                                  }`}
                                >
                                  ⚙ Tham số
                                </button>
                                <button
                                  type="button"
                                  aria-label={`Bỏ ${meta.name}`}
                                  onClick={() =>
                                    onRemoveIndicator(
                                      activeMatches[activeMatches.length - 1].instanceId
                                    )
                                  }
                                  title="Gỡ chỉ báo khỏi biểu đồ"
                                  className="h-6 px-2 rounded text-[11px] font-semibold text-[#F23645] bg-[#F23645]/10 hover:bg-[#F23645]/20"
                                >
                                  ✕ Bỏ
                                </button>
                              </>
                            )}

                            {canAddMore && (
                              <button
                                type="button"
                                aria-label={`Thêm ${meta.name}`}
                                onClick={() => onAddIndicator(meta.id)}
                                className={`h-6 px-2.5 rounded text-[11px] font-semibold transition-opacity ${
                                  isActive
                                    ? "bg-[#2962FF]/12 text-[#2962FF] hover:bg-[#2962FF]/20"
                                    : "opacity-0 group-hover:opacity-100 bg-[#2962FF] text-white hover:bg-[#1E53E5]"
                                }`}
                              >
                                + Thêm{isActive ? " đường" : ""}
                              </button>
                            )}

                            {meta.category === "custom" && onDeleteCustomScript && (
                              <button
                                type="button"
                                onClick={() => onDeleteCustomScript(meta.id)}
                                title="Xóa vĩnh viễn công thức này"
                                className="h-6 px-1.5 rounded text-[11px] text-[#F23645] opacity-70 hover:opacity-100"
                              >
                                🗑
                              </button>
                            )}
                          </div>
                        </div>

                        {/* Inline Parameter & Style Editor for Active Instances */}
                        {isExpanded && activeMatches.length > 0 && (
                          <div className="px-4 pb-3 pt-1.5 border-t border-current/10 space-y-2">
                            <p className="text-[11.5px] text-[#787B86]">{meta.description}</p>
                            {activeMatches.map((inst, idx) => (
                              <div
                                key={inst.instanceId}
                                className={`p-2.5 rounded-lg border flex flex-wrap items-center justify-between gap-2.5 text-[11.5px] ${
                                  isDark
                                    ? "bg-[#131722] border-[#2A2E39]"
                                    : "bg-white border-[#E0E3EB]"
                                }`}
                              >
                                <div className="flex flex-wrap items-center gap-3">
                                  <span className="font-bold text-[#2962FF]">
                                    #{idx + 1} {meta.shortName}
                                  </span>

                                  <label className="flex items-center gap-1.5">
                                    <span className="opacity-70">Màu:</span>
                                    <input
                                      type="color"
                                      value={inst.color}
                                      onChange={(e) =>
                                        onUpdateIndicator(inst.instanceId, {
                                          color: e.target.value,
                                        })
                                      }
                                      className="w-5 h-5 rounded cursor-pointer border-0 bg-transparent"
                                    />
                                  </label>

                                  {meta.params.map((p) => (
                                    <label key={p.key} className="flex items-center gap-1.5">
                                      <span className="opacity-75">{p.label}:</span>
                                      {p.options && p.options.length > 0 ? (
                                        <select
                                          value={inst.params[p.key] ?? p.defaultValue}
                                          onChange={(e) => {
                                            const raw = Number(e.target.value)
                                            if (!Number.isFinite(raw)) return
                                            onUpdateIndicator(inst.instanceId, {
                                              params: { ...inst.params, [p.key]: raw },
                                            })
                                          }}
                                          className={`h-6 px-2 rounded border font-medium text-[11.5px] outline-none ${
                                            isDark
                                              ? "bg-[#1E222D] border-[#2A2E39] text-[#D1D4DC]"
                                              : "bg-[#F8F9FD] border-[#E0E3EB] text-[#131722]"
                                          }`}
                                        >
                                          {p.options.map((opt) => (
                                            <option key={opt.value} value={opt.value}>
                                              {opt.label}
                                            </option>
                                          ))}
                                        </select>
                                      ) : (
                                        <input
                                          type="number"
                                          min={p.min}
                                          max={p.max}
                                          step={p.step ?? 1}
                                          value={inst.params[p.key] ?? p.defaultValue}
                                          onChange={(e) => {
                                            const raw = Number(e.target.value)
                                            if (!Number.isFinite(raw)) return
                                            const clamped = Math.max(p.min, Math.min(p.max, raw))
                                            onUpdateIndicator(inst.instanceId, {
                                              params: { ...inst.params, [p.key]: clamped },
                                            })
                                          }}
                                          className={`w-16 h-6 px-1.5 rounded border font-mono text-[11.5px] outline-none ${
                                            isDark
                                              ? "bg-[#1E222D] border-[#2A2E39]"
                                              : "bg-[#F8F9FD] border-[#E0E3EB]"
                                          }`}
                                        />
                                      )}
                                    </label>
                                  ))}
                                </div>

                                <div className="flex items-center gap-1.5">
                                  <button
                                    type="button"
                                    onClick={() =>
                                      onUpdateIndicator(inst.instanceId, { visible: !inst.visible })
                                    }
                                    className="h-6 px-2 rounded bg-current/[0.07] hover:bg-current/[0.14] font-medium"
                                  >
                                    {inst.visible ? "👁 Hiện" : "🙈 Ẩn"}
                                  </button>
                                  <button
                                    type="button"
                                    onClick={() => onRemoveIndicator(inst.instanceId)}
                                    className="h-6 px-2 rounded bg-[#F23645]/12 text-[#F23645] hover:bg-[#F23645]/20 font-semibold"
                                  >
                                    ✕ Xóa
                                  </button>
                                </div>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )

  return createPortal(modalContent, document.body)
}
