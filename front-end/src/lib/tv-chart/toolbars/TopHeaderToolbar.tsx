/* ============================================================================
 * TradingView Top Header Toolbar Component (Strictly Single-Row Layout)
 * Full Free + Pro + Premium + Ultimate Timeframes (20), Chart Styles (16) & Favorites
 * ============================================================================ */
"use client"

import React, { useEffect, useRef, useState } from "react"
import { TvIcon } from "../icons/tv-icons"
import { getIndicatorMetadata, TV_INDICATOR_LIST, TV_INDICATOR_REGISTRY } from "../indicators/registry"
import type {
  TvChartStyle,
  TvComparisonSeries,
  TvIndicatorId,
  TvIndicatorInstance,
  TvReplayState,
  TvTimeframe,
} from "../types"

const PINNED_TIMEFRAMES: TvTimeframe[] = ["1m", "5m", "15m", "1H", "4H", "1D", "1W", "1M"]

const TIMEFRAME_GROUPS: {
  groupLabel: string
  badge?: string
  items: { id: TvTimeframe; label: string; title: string }[]
}[] = [
  {
    groupLabel: "GIÂY",
    badge: "ULTIMATE",
    items: [
      { id: "1s", label: "1s", title: "1 Giây" },
      { id: "5s", label: "5s", title: "5 Giây" },
      { id: "15s", label: "15s", title: "15 Giây" },
      { id: "30s", label: "30s", title: "30 Giây" },
    ],
  },
  {
    groupLabel: "PHÚT",
    items: [
      { id: "1m", label: "1m", title: "1 Phút" },
      { id: "3m", label: "3m", title: "3 Phút" },
      { id: "5m", label: "5m", title: "5 Phút" },
      { id: "15m", label: "15m", title: "15 Phút" },
      { id: "30m", label: "30m", title: "30 Phút" },
      { id: "45m", label: "45m", title: "45 Phút" },
    ],
  },
  {
    groupLabel: "GIỜ",
    items: [
      { id: "1H", label: "1H", title: "1 Giờ" },
      { id: "2H", label: "2H", title: "2 Giờ" },
      { id: "3H", label: "3H", title: "3 Giờ" },
      { id: "4H", label: "4H", title: "4 Giờ" },
    ],
  },
  {
    groupLabel: "NGÀY · TUẦN · THÁNG",
    items: [
      { id: "1D", label: "1D", title: "1 Ngày" },
      { id: "1W", label: "1W", title: "1 Tuần" },
      { id: "1M", label: "1M", title: "1 Tháng" },
      { id: "3M", label: "3M", title: "3 Tháng (Quý)" },
      { id: "6M", label: "6M", title: "6 Tháng" },
      { id: "12M", label: "12M", title: "12 Tháng (Năm)" },
    ],
  },
]

const CHART_STYLES: {
  id: TvChartStyle
  label: string
  viLabel: string
  icon: string
  badge?: string
  nonTimeBased?: boolean
}[] = [
  { id: "candles", label: "Candles", viLabel: "Biểu đồ Nến (Candles)", icon: "style_candles" },
  {
    id: "hollow_candles",
    label: "Hollow Candles",
    viLabel: "Nến rỗng (Hollow Candles)",
    icon: "style_hollow_candles",
  },
  {
    id: "volume_candles",
    label: "Volume Candles",
    viLabel: "Nến tỷ trọng Khối lượng (Volume Candles)",
    icon: "style_candles",
    badge: "PRO",
  },
  { id: "heikin_ashi", label: "Heikin Ashi", viLabel: "Nến Heikin Ashi", icon: "style_heikin_ashi" },
  { id: "bars", label: "Bars (OHLC)", viLabel: "Thanh giá OHLC (Bars)", icon: "style_bars" },
  { id: "line", label: "Line", viLabel: "Đường giá (Line)", icon: "style_line" },
  { id: "area", label: "Area", viLabel: "Biểu đồ miền (Area)", icon: "style_area" },
  {
    id: "hlc_area",
    label: "HLC Area",
    viLabel: " Vùng HLC (High-Low-Close Area)",
    icon: "style_area",
    badge: "PRO",
  },
  { id: "baseline", label: "Baseline", viLabel: "Đường cơ sở (Baseline)", icon: "style_baseline" },
  { id: "columns", label: "Columns", viLabel: "Biểu đồ cột (Columns)", icon: "style_columns" },
  {
    id: "high_low",
    label: "High-Low",
    viLabel: "Thanh Đỉnh–Đáy (High-Low)",
    icon: "style_columns",
  },
  // Non-time-based Pro / Premium / Ultimate Chart Styles
  {
    id: "renko",
    label: "Renko",
    viLabel: "Gạch biến động Renko",
    icon: "style_candles",
    badge: "PRO",
    nonTimeBased: true,
  },
  {
    id: "line_break",
    label: "Line Break",
    viLabel: "Đường phá vỡ 3 phiên (Line Break)",
    icon: "style_hollow_candles",
    badge: "PRO",
    nonTimeBased: true,
  },
  {
    id: "kagi",
    label: "Kagi",
    viLabel: "Đường đảo chiều Kagi Nhật Bản",
    icon: "style_line",
    badge: "PREMIUM",
    nonTimeBased: true,
  },
  {
    id: "point_and_figure",
    label: "Point & Figure",
    viLabel: "Điểm & Hình X/O (Point & Figure)",
    icon: "style_bars",
    badge: "PREMIUM",
    nonTimeBased: true,
  },
  {
    id: "range",
    label: "Range",
    viLabel: "Thanh biên độ cố định (Range Bars)",
    icon: "style_candles",
    badge: "ULTIMATE",
    nonTimeBased: true,
  },
]

const DEFAULT_FAVORITE_IDS: TvIndicatorId[] = [
  "VP_VISIBLE",
  "AUTO_FIB_RET",
  "PAT_ALL_CHART",
  "STRAT_SUPERTREND",
  "COM_SMC_LUXALGO",
  "SMA",
  "EMA",
  "BB",
  "ICHIMOKU",
  "RSI",
  "MACD",
]

const COMPARE_PRESETS = [
  { symbol: "FPT", name: "FPT Corp", color: "#FF6D00" },
  { symbol: "SSI", name: "Chứng khoán SSI", color: "#9C27B0" },
  { symbol: "VCB", name: "Vietcombank", color: "#00897B" },
  { symbol: "MBB", name: "MBBank", color: "#E53935" },
  { symbol: "TCB", name: "Techcombank", color: "#00ACC1" },
  { symbol: "HPG", name: "Hòa Phát", color: "#2962FF" },
]

const POPULAR_VN_SYMBOLS = [
  { symbol: "HPG", name: "Tập đoàn Hòa Phát" },
  { symbol: "FPT", name: "CTCP FPT" },
  { symbol: "SSI", name: "Chứng khoán SSI" },
  { symbol: "VCB", name: "Ngân hàng Vietcombank" },
  { symbol: "MBB", name: "Ngân hàng Quân Đội" },
  { symbol: "TCB", name: "Ngân hàng Techcombank" },
  { symbol: "MWG", name: "Thế Giới Di Động" },
  { symbol: "VHM", name: "Vinhomes" },
  { symbol: "VIC", name: "Tập đoàn Vingroup" },
  { symbol: "STB", name: "Ngân hàng Sacombank" },
  { symbol: "ACB", name: "Ngân hàng Á Châu" },
  { symbol: "DGC", name: "Hóa chất Đức Giang" },
  { symbol: "GAS", name: "PV GAS" },
  { symbol: "VNM", name: "Vinamilk" },
  { symbol: "MSN", name: "Tập đoàn Masan" },
  { symbol: "GMD", name: "Gemadept" },
]

export interface TopHeaderToolbarProps {
  symbol: string
  timeframe: TvTimeframe
  chartStyle: TvChartStyle
  indicators: TvIndicatorInstance[]
  favoriteIds?: string[]
  comparisons: TvComparisonSeries[]
  replay: TvReplayState
  canUndo: boolean
  canRedo: boolean
  isFullscreen: boolean
  isDark: boolean
  onSymbolChange?: (sym: string) => void
  onTimeframeChange: (tf: TvTimeframe) => void
  onChartStyleChange: (style: TvChartStyle) => void
  onOpenIndicatorsModal: () => void
  onQuickToggleIndicator: (id: TvIndicatorId) => void
  onToggleComparison: (symbol: string, name: string, color: string) => void
  onToggleReplay: () => void
  onReplaySelectCut: () => void
  onReplayPlayPause: () => void
  onReplayStep: () => void
  onReplaySpeedChange: (ms: number) => void
  onUndo: () => void
  onRedo: () => void
  onOpenObjectTree: () => void
  onOpenSettings: () => void
  onToggleFullscreen: () => void
  onTakeSnapshot: () => void
}

export function TopHeaderToolbar({
  symbol,
  timeframe,
  chartStyle,
  indicators,
  favoriteIds,
  comparisons,
  replay,
  canUndo,
  canRedo,
  isFullscreen,
  isDark,
  onSymbolChange,
  onTimeframeChange,
  onChartStyleChange,
  onOpenIndicatorsModal,
  onQuickToggleIndicator,
  onToggleComparison,
  onToggleReplay,
  onUndo,
  onRedo,
  onOpenObjectTree,
  onOpenSettings,
  onToggleFullscreen,
  onTakeSnapshot,
}: TopHeaderToolbarProps) {
  const [styleMenuOpen, setStyleMenuOpen] = useState(false)
  const [compareMenuOpen, setCompareMenuOpen] = useState(false)
  const [symMenuOpen, setSymMenuOpen] = useState(false)
  const [tfMenuOpen, setTfMenuOpen] = useState(false)
  const [favMenuOpen, setFavMenuOpen] = useState(false)
  const [symFilter, setSymFilter] = useState("")

  const styleRef = useRef<HTMLDivElement>(null)
  const compareRef = useRef<HTMLDivElement>(null)
  const symRef = useRef<HTMLDivElement>(null)
  const tfRef = useRef<HTMLDivElement>(null)
  const favRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    function handleOutside(e: MouseEvent) {
      const target = e.target as Node
      if (styleRef.current && !styleRef.current.contains(target)) setStyleMenuOpen(false)
      if (compareRef.current && !compareRef.current.contains(target)) setCompareMenuOpen(false)
      if (symRef.current && !symRef.current.contains(target)) setSymMenuOpen(false)
      if (tfRef.current && !tfRef.current.contains(target)) setTfMenuOpen(false)
      if (favRef.current && !favRef.current.contains(target)) setFavMenuOpen(false)
    }
    document.addEventListener("mousedown", handleOutside)
    return () => document.removeEventListener("mousedown", handleOutside)
  }, [])

  const currentStyleMeta = CHART_STYLES.find((s) => s.id === chartStyle) || CHART_STYLES[0]
  const visibleIndicatorCount = indicators.filter((i) => i.visible).length
  const filteredSymbols = POPULAR_VN_SYMBOLS.filter(
    (s) =>
      s.symbol.includes(symFilter.trim().toUpperCase()) ||
      s.name.toLowerCase().includes(symFilter.trim().toLowerCase())
  )

  const validFavProp = (favoriteIds ?? []).filter((id) => Boolean(TV_INDICATOR_REGISTRY[id]))
  const quickFavIds = validFavProp.length > 0 ? validFavProp : DEFAULT_FAVORITE_IDS
  const isCustomTfActive = !PINNED_TIMEFRAMES.includes(timeframe)

  const popoverSurface = isDark
    ? "border-[#2A2E39] bg-[#1E222D] text-[#D1D4DC]"
    : "border-[#E0E3EB] bg-white text-[#131722]"

  return (
    <div
      className={`flex h-[38px] shrink-0 flex-nowrap items-center justify-between gap-1 border-b px-2 select-none text-[12px] z-30 ${
        isDark
          ? "border-[#2A2E39] bg-[#131722] text-[#D1D4DC]"
          : "border-[#E0E3EB] bg-white text-[#131722]"
      }`}
    >
      {/* LEFT SECTION: Strictly Single-Row (No flex-wrap) */}
      <div className="flex min-w-0 flex-nowrap items-center gap-0.5">
        {/* 1. Symbol Search */}
        <div ref={symRef} className="relative shrink-0">
          <button
            type="button"
            id="header-toolbar-symbol-search"
            onClick={() => setSymMenuOpen((v) => !v)}
            title="Tìm kiếm hoặc đổi mã cổ phiếu"
            className="inline-flex h-7 items-center gap-1.5 rounded px-2 font-mono text-[12.5px] font-bold hover:bg-black/5 dark:hover:bg-white/5 transition-colors whitespace-nowrap"
          >
            <TvIcon name="header-toolbar-quick-search" size={17} />
            <span>{symbol}</span>
          </button>

          {symMenuOpen && (
            <div className={`absolute left-0 top-full z-50 mt-1 w-[260px] rounded-lg border p-2 shadow-xl ${popoverSurface}`}>
              <input
                type="text"
                value={symFilter}
                onChange={(e) => setSymFilter(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && symFilter.trim()) {
                    onSymbolChange?.(symFilter.trim().toUpperCase())
                    setSymMenuOpen(false)
                    setSymFilter("")
                  }
                }}
                placeholder="Nhập mã CP (VD: HPG, FPT, VCB)..."
                className="mb-1.5 h-7 w-full rounded border border-current/15 bg-transparent px-2 font-mono text-[11.5px] focus:border-[#2962FF] focus:outline-none"
                autoFocus
              />
              <div className="max-h-[220px] overflow-y-auto divide-y divide-current/5">
                {filteredSymbols.map((item) => (
                  <button
                    key={item.symbol}
                    type="button"
                    onClick={() => {
                      onSymbolChange?.(item.symbol)
                      setSymMenuOpen(false)
                      setSymFilter("")
                    }}
                    className="flex w-full items-center justify-between rounded px-2 py-1.5 text-left hover:bg-[#2962FF]/10 transition-colors"
                  >
                    <span className="font-mono text-[12px] font-bold">{item.symbol}</span>
                    <span className="truncate text-[11px] opacity-70">{item.name}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* 2. Compare Symbol */}
        <div ref={compareRef} className="relative shrink-0">
          <button
            type="button"
            id="header-toolbar-compare"
            onClick={() => setCompareMenuOpen((v) => !v)}
            title="So sánh tương quan mã cổ phiếu (Compare)"
            className={`inline-flex h-7 items-center gap-1 rounded px-1.5 text-[11.5px] font-medium transition-colors whitespace-nowrap ${
              comparisons.length > 0
                ? "bg-[#2962FF]/12 text-[#2962FF]"
                : "hover:bg-black/5 dark:hover:bg-white/5"
            }`}
          >
            <TvIcon name="header-toolbar-compare" size={19} />
            {comparisons.length > 0 && (
              <span className="font-mono text-[10.5px] font-bold">{comparisons.length}</span>
            )}
          </button>

          {compareMenuOpen && (
            <div className={`absolute left-0 top-full z-50 mt-1 w-[235px] rounded-lg border p-2 shadow-xl ${popoverSurface}`}>
              <div className="mb-1.5 px-1 text-[10.5px] font-semibold uppercase tracking-wider opacity-60">
                So sánh tương quan giá
              </div>
              <div className="space-y-1">
                {COMPARE_PRESETS.filter((p) => p.symbol !== symbol).map((preset) => {
                  const active = comparisons.some((c) => c.symbol === preset.symbol && c.visible)
                  return (
                    <button
                      key={preset.symbol}
                      type="button"
                      onClick={() => onToggleComparison(preset.symbol, preset.name, preset.color)}
                      className={`flex w-full items-center justify-between rounded px-2 py-1.5 text-left text-[11.5px] transition-colors ${
                        active ? "bg-[#2962FF]/15 font-semibold" : "hover:bg-black/5 dark:hover:bg-white/5"
                      }`}
                    >
                      <span className="inline-flex items-center gap-2">
                        <span
                          className="h-2.5 w-2.5 rounded-full"
                          style={{ backgroundColor: preset.color }}
                        />
                        <span className="font-mono font-bold">{preset.symbol}</span>
                        <span className="text-[11px] opacity-75">{preset.name}</span>
                      </span>
                      <span className="font-mono text-[11px]">{active ? "✓" : "+"}</span>
                    </button>
                  )
                })}
              </div>
            </div>
          )}
        </div>

        <span className="mx-1 h-4 w-px shrink-0 bg-current/12" />

        {/* 3. Timeframes: Pinned Inline + Full 20-Interval Ultimate Dropdown (1s..12M) */}
        <div ref={tfRef} id="header-toolbar-intervals" className="relative flex shrink-0 items-center gap-0.5">
          <div className="hidden md:flex items-center gap-0.5">
            {PINNED_TIMEFRAMES.map((tfId) => {
              const active = timeframe === tfId
              return (
                <button
                  key={tfId}
                  type="button"
                  onClick={() => onTimeframeChange(tfId)}
                  className={`h-7 rounded px-1.5 font-mono text-[11.5px] font-semibold transition-colors whitespace-nowrap ${
                    active
                      ? "bg-[#2962FF]/15 text-[#2962FF]"
                      : "opacity-80 hover:bg-black/5 dark:hover:bg-white/5 hover:opacity-100"
                  }`}
                >
                  {tfId}
                </button>
              )
            })}
            {isCustomTfActive && (
              <span className="h-7 inline-flex items-center rounded px-1.5 font-mono text-[11.5px] font-bold bg-[#2962FF]/15 text-[#2962FF]">
                {timeframe}
              </span>
            )}
          </div>

          <button
            type="button"
            onClick={() => setTfMenuOpen((v) => !v)}
            aria-label="Mở toàn bộ 20 khung thời gian"
            title="Mở toàn bộ 20 khung thời gian (1s Giây Ultimate → 12M Năm)"
            className="inline-flex h-7 items-center gap-1 rounded px-1.5 font-mono text-[11px] hover:bg-black/5 dark:hover:bg-white/5 transition-colors"
          >
            <span className="md:hidden font-bold text-[#2962FF]">{timeframe}</span>
            <span className="opacity-70">▾</span>
          </button>

          {tfMenuOpen && (
            <div className={`absolute left-0 top-full z-50 mt-1 w-[240px] max-h-[420px] overflow-y-auto rounded-lg border p-2 shadow-xl ${popoverSurface}`}>
              {TIMEFRAME_GROUPS.map((grp, gIdx) => (
                <div key={grp.groupLabel} className={gIdx > 0 ? "mt-2 pt-2 border-t border-current/10" : ""}>
                  <div className="px-2 pb-1 flex items-center justify-between text-[10px] font-bold uppercase tracking-wider opacity-60">
                    <span>{grp.groupLabel}</span>
                    {grp.badge && (
                      <span className="px-1 py-0.2 rounded bg-amber-500/15 text-amber-500 text-[9px]">
                        {grp.badge}
                      </span>
                    )}
                  </div>
                  <div className="grid grid-cols-2 gap-0.5">
                    {grp.items.map((tf) => (
                      <button
                        key={tf.id}
                        type="button"
                        onClick={() => {
                          onTimeframeChange(tf.id)
                          setTfMenuOpen(false)
                        }}
                        className={`flex items-center justify-between rounded px-2 py-1 text-[11.5px] transition-colors ${
                          timeframe === tf.id
                            ? "bg-[#2962FF]/15 text-[#2962FF] font-bold"
                            : "hover:bg-black/5 dark:hover:bg-white/5"
                        }`}
                      >
                        <span className="font-mono">{tf.label}</span>
                        <span className="text-[10.5px] opacity-65 truncate ml-1">{tf.title}</span>
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <span className="mx-1 h-4 w-px shrink-0 bg-current/12" />

        {/* 4. Chart Styles Dropdown (All 16 Standard + Pro + Ultimate Styles) */}
        <div ref={styleRef} className="relative shrink-0">
          <button
            type="button"
            id="header-toolbar-chart-styles"
            onClick={() => setStyleMenuOpen((v) => !v)}
            title={`Kiểu biểu đồ: ${currentStyleMeta.viLabel}`}
            className="inline-flex h-7 items-center gap-1 rounded px-1.5 hover:bg-black/5 dark:hover:bg-white/5 transition-colors"
          >
            <TvIcon name={currentStyleMeta.icon} size={20} />
            {currentStyleMeta.nonTimeBased && (
              <span className="hidden lg:inline font-mono text-[10.5px] font-bold text-[#2962FF]">
                {currentStyleMeta.label}
              </span>
            )}
          </button>

          {styleMenuOpen && (
            <div className={`absolute left-0 top-full z-50 mt-1 w-[275px] max-h-[430px] overflow-y-auto rounded-lg border p-1.5 shadow-xl ${popoverSurface}`}>
              <div className="px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider opacity-55">
                Kiểu biểu đồ tiêu chuẩn & Pro
              </div>
              {CHART_STYLES.filter((s) => !s.nonTimeBased).map((st) => {
                const active = st.id === chartStyle
                return (
                  <button
                    key={st.id}
                    type="button"
                    onClick={() => {
                      onChartStyleChange(st.id)
                      setStyleMenuOpen(false)
                    }}
                    className={`flex w-full items-center justify-between gap-2 rounded px-2.5 py-1.5 text-left text-[12px] transition-colors ${
                      active
                        ? "bg-[#2962FF]/15 text-[#2962FF] font-semibold"
                        : "hover:bg-black/5 dark:hover:bg-white/5"
                    }`}
                  >
                    <span className="flex items-center gap-2 min-w-0">
                      <TvIcon name={st.icon} size={18} />
                      <span className="truncate">{st.viLabel}</span>
                    </span>
                    {st.badge && (
                      <span className="px-1.5 py-0.2 rounded text-[9px] font-bold bg-[#2962FF]/12 text-[#2962FF] shrink-0">
                        {st.badge}
                      </span>
                    )}
                  </button>
                )
              })}

              <div className="mt-1.5 pt-1.5 border-t border-current/10 px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider opacity-55">
                Phi thời gian (Pro / Ultimate)
              </div>
              {CHART_STYLES.filter((s) => s.nonTimeBased).map((st) => {
                const active = st.id === chartStyle
                return (
                  <button
                    key={st.id}
                    type="button"
                    onClick={() => {
                      onChartStyleChange(st.id)
                      setStyleMenuOpen(false)
                    }}
                    className={`flex w-full items-center justify-between gap-2 rounded px-2.5 py-1.5 text-left text-[12px] transition-colors ${
                      active
                        ? "bg-[#2962FF]/15 text-[#2962FF] font-semibold"
                        : "hover:bg-black/5 dark:hover:bg-white/5"
                    }`}
                  >
                    <span className="flex items-center gap-2 min-w-0">
                      <TvIcon name={st.icon} size={18} />
                      <span className="truncate">{st.viLabel}</span>
                    </span>
                    {st.badge && (
                      <span className="px-1.5 py-0.2 rounded text-[9px] font-bold bg-amber-500/15 text-amber-500 shrink-0">
                        {st.badge}
                      </span>
                    )}
                  </button>
                )
              })}
            </div>
          )}
        </div>

        <span className="mx-1 h-4 w-px shrink-0 bg-current/12" />

        {/* 5. Indicators Button + Compact Split Arrow for ★ Favorites */}
        <div ref={favRef} id="header-toolbar-indicators" className="relative flex shrink-0 items-center">
          <button
            type="button"
            data-name="open-indicators-dialog"
            onClick={onOpenIndicatorsModal}
            className="inline-flex h-7 items-center gap-1.5 rounded-l px-2 font-medium hover:bg-black/5 dark:hover:bg-white/5 transition-colors whitespace-nowrap"
            title={`Mở Chỉ báo, chỉ số và chiến lược (${TV_INDICATOR_LIST.length} Tập lệnh Free + Pro + Ultimate)`}
          >
            <TvIcon name="header-toolbar-indicators" size={19} />
            <span className="text-[12px]">Chỉ báo</span>
            {visibleIndicatorCount > 0 && (
              <span className="rounded-full bg-[#2962FF]/15 px-1.5 py-0.2 font-mono text-[10.5px] font-bold text-[#2962FF]">
                {visibleIndicatorCount}
              </span>
            )}
          </button>

          <button
            type="button"
            onClick={() => setFavMenuOpen((v) => !v)}
            aria-label="Bật/tắt nhanh các chỉ báo Yêu thích"
            title="Bật/tắt nhanh các chỉ báo Yêu thích (★)"
            className="inline-flex h-7 w-5 items-center justify-center rounded-r hover:bg-black/5 dark:hover:bg-white/5 text-[10px] opacity-70 hover:opacity-100 transition-colors"
          >
            ▾
          </button>

          {favMenuOpen && (
            <div className={`absolute left-0 top-full z-50 mt-1 w-[295px] rounded-lg border p-1.5 shadow-xl ${popoverSurface}`}>
              <div className="px-2 py-1 text-[10px] font-bold uppercase tracking-wider opacity-55">
                ★ Chỉ báo & Chiến lược Yêu thích
              </div>
              <div className="max-h-[280px] overflow-y-auto space-y-0.5">
                {quickFavIds.map((favId) => {
                  const meta = getIndicatorMetadata(favId)
                  const isActive = indicators.some((ind) => ind.id === favId)
                  return (
                    <button
                      key={favId}
                      type="button"
                      onClick={() => onQuickToggleIndicator(favId)}
                      className={`flex w-full items-center justify-between rounded px-2.5 py-1.5 text-left text-[12px] transition-colors ${
                        isActive
                          ? "bg-[#2962FF]/12 text-[#2962FF] font-semibold"
                          : "hover:bg-black/5 dark:hover:bg-white/5"
                      }`}
                    >
                      <div className="min-w-0 pr-2 flex items-center gap-1.5">
                        <span className="text-amber-400 text-[11px]">★</span>
                        <span className="truncate font-medium">{meta.name}</span>
                      </div>
                      <span className="font-mono text-[11px] shrink-0">{isActive ? "✓ Bật" : "+"}</span>
                    </button>
                  )
                })}
              </div>
              <div className="mt-1 pt-1 border-t border-current/10">
                <button
                  type="button"
                  onClick={() => {
                    setFavMenuOpen(false)
                    onOpenIndicatorsModal()
                  }}
                  className="w-full rounded px-2.5 py-1.5 text-center text-[11.5px] font-semibold text-[#2962FF] hover:bg-[#2962FF]/10"
                >
                  Mở toàn bộ {TV_INDICATOR_LIST.length} Chỉ báo, Hồ sơ & Chiến lược →
                </button>
              </div>
            </div>
          )}
        </div>

        <span className="mx-1 h-4 w-px shrink-0 bg-current/12" />

        {/* 6. Bar Replay Toggle */}
        <button
          type="button"
          id="header-toolbar-replay"
          onClick={onToggleReplay}
          title="Chế độ tua lại lịch sử nến (Bar Replay)"
          className={`inline-flex h-7 shrink-0 items-center gap-1 rounded px-2 text-[12px] font-medium transition-colors whitespace-nowrap ${
            replay.active
              ? "bg-[#2962FF] text-white"
              : "hover:bg-black/5 dark:hover:bg-white/5"
          }`}
        >
          <TvIcon name="header-toolbar-replay" size={19} />
          <span className="hidden lg:inline">Replay</span>
        </button>
      </div>

      {/* RIGHT SECTION: Undo/Redo + Object Tree + Settings + Fullscreen + Snapshot */}
      <div className="flex shrink-0 items-center gap-0.5">
        <button
          type="button"
          onClick={onUndo}
          disabled={!canUndo}
          title="Hoàn tác nét vẽ (Ctrl+Z)"
          className="inline-flex h-7 w-7 items-center justify-center rounded hover:bg-black/5 dark:hover:bg-white/5 disabled:opacity-30 transition-colors"
        >
          <TvIcon name="header-toolbar-undo-redo" size={18} />
        </button>
        <button
          type="button"
          onClick={onRedo}
          disabled={!canRedo}
          title="Làm lại nét vẽ (Ctrl+Y)"
          className="inline-flex h-7 w-7 items-center justify-center rounded hover:bg-black/5 dark:hover:bg-white/5 disabled:opacity-30 transition-colors"
        >
          <TvIcon name="redo" size={18} />
        </button>

        <span className="mx-0.5 h-4 w-px bg-current/12" />

        <button
          type="button"
          data-name="object_tree"
          onClick={onOpenObjectTree}
          title="Cây đối tượng & Cửa sổ dữ liệu (Object Tree)"
          className="inline-flex h-7 w-7 items-center justify-center rounded hover:bg-black/5 dark:hover:bg-white/5 transition-colors"
        >
          <TvIcon name="object_tree" size={20} />
        </button>

        <button
          type="button"
          id="header-toolbar-properties"
          onClick={onOpenSettings}
          title="Cài đặt biểu đồ (Chart Properties)"
          className="inline-flex h-7 w-7 items-center justify-center rounded hover:bg-black/5 dark:hover:bg-white/5 transition-colors"
        >
          <TvIcon name="header-toolbar-properties" size={19} />
        </button>

        <button
          type="button"
          id="header-toolbar-fullscreen"
          onClick={onToggleFullscreen}
          title={isFullscreen ? "Thoát toàn màn hình (Esc)" : "Toàn màn hình (Fullscreen)"}
          className="inline-flex h-7 w-7 items-center justify-center rounded hover:bg-black/5 dark:hover:bg-white/5 transition-colors"
        >
          <TvIcon name="header-toolbar-fullscreen" size={19} />
        </button>

        <button
          type="button"
          id="header-toolbar-screenshot"
          onClick={onTakeSnapshot}
          title="Chụp và tải ảnh biểu đồ PNG (Snapshot)"
          className="inline-flex h-7 w-7 items-center justify-center rounded hover:bg-black/5 dark:hover:bg-white/5 transition-colors"
        >
          <TvIcon name="header-toolbar-screenshot" size={19} />
        </button>
      </div>
    </div>
  )
}
