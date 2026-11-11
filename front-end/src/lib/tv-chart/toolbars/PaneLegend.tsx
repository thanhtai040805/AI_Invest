"use client"

import React, { useState } from "react"
import { TvIcon } from "../icons/tv-icons"
import { fmtCompactAxisValue, fmtPriceVnd, type PaneLayoutBox } from "../engine/canvas-renderer"
import type {
  TvCandle,
  TvChartSettings,
  TvComparisonSeries,
  TvIndicatorComputed,
  TvTimeframe,
  TvTradingOverlay,
} from "../types"

export interface PaneLegendProps {
  symbol: string
  stockName?: string
  exchange?: string
  timeframe: TvTimeframe
  activeCandle: TvCandle | null
  prevCandle: TvCandle | null
  settings: TvChartSettings
  trading?: TvTradingOverlay
  overlayIndicators: TvIndicatorComputed[]
  subPanes: PaneLayoutBox[]
  comparisons: TvComparisonSeries[]
  hoveredBarIndex: number
  onToggleIndicatorVisible: (instanceId: string) => void
  onRemoveIndicator: (instanceId: string) => void
  onToggleIndicatorCollapsed: (instanceId: string) => void
  onToggleIndicatorMaximized: (instanceId: string) => void
  onOpenIndicatorSettings: (instanceId: string) => void
  onOpenIndicatorsModal: () => void
  onRemoveComparison: (symbol: string) => void
  onZoomIn: () => void
  onZoomOut: () => void
  onPanLeft: () => void
  onPanRight: () => void
  onResetView: () => void
}

export const PaneLegend: React.FC<PaneLegendProps> = ({
  symbol,
  stockName,
  exchange = "HOSE",
  timeframe,
  activeCandle,
  prevCandle,
  settings,
  trading,
  overlayIndicators,
  subPanes,
  comparisons,
  hoveredBarIndex,
  onToggleIndicatorVisible,
  onRemoveIndicator,
  onToggleIndicatorCollapsed,
  onToggleIndicatorMaximized,
  onOpenIndicatorSettings,
  onOpenIndicatorsModal,
  onRemoveComparison,
  onZoomIn,
  onZoomOut,
  onPanLeft,
  onPanRight,
  onResetView,
}) => {
  const [legendCollapsed, setLegendCollapsed] = useState(false)
  const [studiesCollapsed, setStudiesCollapsed] = useState(false)
  const isDark = settings.theme === "dark"

  const refBase = prevCandle?.close ?? trading?.refPrice ?? activeCandle?.open ?? 0
  const closeVal = activeCandle?.close ?? trading?.currentPrice ?? 0
  const changeAbs = refBase > 0 ? closeVal - refBase : 0
  const changePct = refBase > 0 ? (changeAbs / refBase) * 100 : 0

  const getPriceColorClass = (price: number) => {
    if (trading?.ceilingPrice && Math.abs(price - trading.ceilingPrice) < 1) {
      return "text-[#9C27B0]"
    }
    if (trading?.floorPrice && Math.abs(price - trading.floorPrice) < 1) {
      return "text-[#00ACC1]"
    }
    if (changeAbs > 0) return "text-[#089981]"
    if (changeAbs < 0) return "text-[#F23645]"
    return "text-[#CA8A04]"
  }

  const priceColorClass = getPriceColorClass(closeVal)

  const formatVolume = (vol: number) => {
    if (!Number.isFinite(vol)) return "—"
    if (vol >= 1_000_000) return `${(vol / 1_000_000).toFixed(2)}M`
    if (vol >= 1_000) return `${(vol / 1_000).toFixed(1)}K`
    return `${Math.round(vol)}`
  }

  const bidPrice = trading?.bestBidPrice || (closeVal > 0 ? closeVal - 50 : 0)
  const askPrice = trading?.bestAskPrice || (closeVal > 0 ? closeVal + 50 : 0)
  const spread = askPrice > 0 && bidPrice > 0 ? Math.max(0, askPrice - bidPrice) : 0

  return (
    <>
      {/* Main Pane Legend Overlay (Top-Left, TradingView Native Compact Stack) */}
      <div
        id="legend"
        data-name="legend"
        className="absolute top-2 left-2.5 z-10 flex flex-col gap-1 max-w-[calc(100%-92px)] pointer-events-none select-none"
      >
        {/* Row 1: Symbol + Timeframe + Exchange + OHLCV */}
        <div
          data-name="legend-source-item"
          className={`inline-flex flex-wrap items-center gap-x-2 gap-y-0.5 px-2 py-0.5 rounded-md pointer-events-auto w-fit transition-colors ${
            isDark
              ? "bg-[#131722]/80 text-[#D1D4DC] hover:bg-[#1E222D]/95"
              : "bg-white/85 text-[#131722] hover:bg-white/95"
          } backdrop-blur-[2px] border border-transparent hover:border-current/10`}
        >
          <button
            type="button"
            onClick={() => setLegendCollapsed((v) => !v)}
            title={legendCollapsed ? "Mở rộng chú giải" : "Thu gọn chú giải"}
            className="w-4 h-4 flex items-center justify-center rounded hover:bg-black/5 dark:hover:bg-white/10 opacity-70 hover:opacity-100"
          >
            <TvIcon
              name="legend-sources-wrapper"
              size={13}
              className={`transition-transform ${legendCollapsed ? "-rotate-90" : ""}`}
            />
          </button>

          <div data-name="legend-series-item" className="flex items-center gap-1.5">
            <span className="font-bold text-[12.5px] tracking-tight">{symbol}</span>
            {stockName && (
              <span className="hidden xl:inline text-[11px] opacity-65 max-w-[150px] truncate">
                {stockName}
              </span>
            )}
            <span className="text-[11px] opacity-40">·</span>
            <span className="text-[11px] font-semibold opacity-80">{timeframe}</span>
            <span className="text-[11px] opacity-40">·</span>
            <span className="text-[10px] font-semibold px-1.5 py-0.2 rounded bg-[#2962FF]/10 text-[#2962FF]">
              {exchange}
            </span>
            <span
              className={`w-1.5 h-1.5 rounded-full ${
                trading?.isLive ? "bg-[#089981] animate-pulse" : "bg-amber-500"
              }`}
              title={trading?.isLive ? "Dữ liệu trực tuyến (Realtime)" : "Dữ liệu lịch sử / kết phiên"}
            />
          </div>

          {activeCandle && settings.showSeriesOHLC && (
            <div className="flex flex-wrap items-center gap-x-2 text-[11px] font-mono">
              <span>
                <span className="opacity-55 mr-0.5">O</span>
                <span className={priceColorClass}>{fmtPriceVnd(activeCandle.open)}</span>
              </span>
              <span>
                <span className="opacity-55 mr-0.5">H</span>
                <span className={priceColorClass}>{fmtPriceVnd(activeCandle.high)}</span>
              </span>
              <span>
                <span className="opacity-55 mr-0.5">L</span>
                <span className={priceColorClass}>{fmtPriceVnd(activeCandle.low)}</span>
              </span>
              <span>
                <span className="opacity-55 mr-0.5">C</span>
                <span className={`font-semibold ${priceColorClass}`}>
                  {fmtPriceVnd(activeCandle.close)}
                </span>
              </span>
              {settings.showBarChangeValues && (
                <span className={`font-semibold ${priceColorClass}`}>
                  {changeAbs >= 0 ? "+" : ""}
                  {fmtPriceVnd(changeAbs)} ({changePct >= 0 ? "+" : ""}
                  {changePct.toFixed(2)}%)
                </span>
              )}
              <span>
                <span className="opacity-55 mr-0.5">KL</span>
                <span className="font-semibold opacity-85">{formatVolume(activeCandle.volume)}</span>
              </span>
            </div>
          )}
        </div>

        {/* Row 2: Authentic TradingView Quick Sell / Spread / Buy Pill (Clean, zero HOSE clutter) */}
        {!legendCollapsed && trading && (
          <div className="inline-flex items-center gap-1 pointer-events-auto w-fit">
            <button
              type="button"
              onClick={() => {
                trading.onChangeOrderSide?.("SELL")
                trading.onPickPriceFromChart(bidPrice)
              }}
              title="Đặt lệnh BÁN tại giá chào mua tốt nhất"
              className="flex items-center gap-1.5 px-2 py-0.5 rounded border border-[#F23645]/45 bg-white/90 dark:bg-[#131722]/90 hover:bg-[#F23645] text-[#F23645] hover:text-white transition-colors text-[11px] font-mono shadow-2xs"
            >
              <span className="font-bold">{fmtPriceVnd(bidPrice)}</span>
              <span className="font-sans font-bold text-[9.5px] uppercase tracking-wider opacity-85">
                BÁN
              </span>
            </button>

            <span className="text-[10px] font-mono opacity-55 px-0.5">
              {spread > 0 ? fmtPriceVnd(spread) : "0"}
            </span>

            <button
              type="button"
              onClick={() => {
                trading.onChangeOrderSide?.("BUY")
                trading.onPickPriceFromChart(askPrice)
              }}
              title="Đặt lệnh MUA tại giá chào bán tốt nhất"
              className="flex items-center gap-1.5 px-2 py-0.5 rounded border border-[#2962FF]/45 bg-white/90 dark:bg-[#131722]/90 hover:bg-[#2962FF] text-[#2962FF] hover:text-white transition-colors text-[11px] font-mono shadow-2xs"
            >
              <span className="font-bold">{fmtPriceVnd(askPrice)}</span>
              <span className="font-sans font-bold text-[9.5px] uppercase tracking-wider opacity-85">
                MUA
              </span>
            </button>
          </div>
        )}

        {/* Row 3: Comparison Symbols */}
        {!legendCollapsed &&
          comparisons.map((comp) => {
            const lastComp = comp.candles[comp.candles.length - 1]
            const firstComp = comp.candles[0]
            const pct =
              firstComp && lastComp && firstComp.close > 0
                ? ((lastComp.close - firstComp.close) / firstComp.close) * 100
                : 0
            return (
              <div
                key={comp.symbol}
                className={`inline-flex items-center gap-2 px-2 py-0.5 rounded text-[11px] pointer-events-auto w-fit ${
                  isDark ? "bg-[#131722]/75 text-[#D1D4DC]" : "bg-white/85 text-[#131722]"
                }`}
              >
                <span
                  className="w-2 h-2 rounded-full"
                  style={{ backgroundColor: comp.color }}
                />
                <span className="font-semibold">{comp.symbol}</span>
                <span className="font-mono opacity-80">
                  {lastComp ? fmtPriceVnd(lastComp.close) : "—"} ({pct >= 0 ? "+" : ""}
                  {pct.toFixed(2)}%)
                </span>
                <button
                  type="button"
                  onClick={() => onRemoveComparison(comp.symbol)}
                  className="opacity-60 hover:opacity-100 text-[#F23645]"
                  title="Gỡ mã so sánh"
                >
                  ✕
                </button>
              </div>
            )
          })}

        {/* Row 4+: Active Main-Pane Overlay Indicators (with Collapsible Group Header when >= 2) */}
        {!legendCollapsed && overlayIndicators.length > 0 && (
          <>
            {!studiesCollapsed &&
              overlayIndicators.map((comp) => (
                <div
                  key={comp.instance.instanceId}
                  data-name="legend-source-item"
                  className={`group inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[11px] leading-tight pointer-events-auto w-fit max-w-full transition-colors ${
                    isDark
                      ? "bg-[#131722]/75 text-[#D1D4DC] hover:bg-[#1E222D]"
                      : "bg-white/85 text-[#131722] hover:bg-white"
                  } ${!comp.instance.visible ? "opacity-50 line-through" : ""}`}
                >
                  <span
                    className="w-2 h-2 rounded-full shrink-0"
                    style={{ backgroundColor: comp.instance.color }}
                  />
                  <span className="font-semibold whitespace-nowrap">{comp.title}</span>

                  {settings.showIndicatorValues && comp.instance.visible && (
                    <div className="hidden sm:flex items-center gap-1.5 font-mono text-[10.5px] truncate">
                      {comp.plots.slice(0, 3).map((p) => {
                        const v = p.values[hoveredBarIndex]
                        if (v === null || v === undefined || !Number.isFinite(v)) return null
                        return (
                          <span key={p.key} style={{ color: p.color }} className="whitespace-nowrap">
                            {comp.plots.length > 1 ? `${p.label}: ` : ""}
                            {fmtCompactAxisValue(v)}
                          </span>
                        )
                      })}
                    </div>
                  )}

                  {/* Action Icons: Hide/Show, Settings, Remove */}
                  <div className="flex items-center gap-0.5 opacity-70 group-hover:opacity-100 transition-opacity ml-0.5 shrink-0">
                    <button
                      type="button"
                      onClick={() => onToggleIndicatorVisible(comp.instance.instanceId)}
                      title={comp.instance.visible ? "Ẩn chỉ báo" : "Hiện chỉ báo"}
                      className="p-0.5 rounded hover:bg-black/5 dark:hover:bg-white/10"
                    >
                      <TvIcon name="legend-show-hide-action" size={14} />
                    </button>
                    <button
                      type="button"
                      onClick={() => onOpenIndicatorSettings(comp.instance.instanceId)}
                      title="Cài đặt thông số chỉ báo"
                      className="p-0.5 rounded hover:bg-black/5 dark:hover:bg-white/10"
                    >
                      <TvIcon name="legend-settings-action" size={14} />
                    </button>
                    <button
                      type="button"
                      onClick={() => onRemoveIndicator(comp.instance.instanceId)}
                      title="Gỡ chỉ báo khỏi biểu đồ"
                      className="p-0.5 rounded hover:bg-[#F23645]/15 text-[#F23645]"
                    >
                      <TvIcon name="legend-delete-action" size={14} />
                    </button>
                  </div>
                </div>
              ))}

            {/* TradingView Collapsible "N chỉ báo" Toggle Pill */}
            <div className="inline-flex items-center gap-1 pointer-events-auto w-fit">
              {overlayIndicators.length >= 2 && (
                <button
                  type="button"
                  onClick={() => setStudiesCollapsed((v) => !v)}
                  title={studiesCollapsed ? "Hiện danh sách chỉ báo trên biểu đồ" : "Thu gọn danh sách chỉ báo"}
                  className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded border text-[10.5px] font-medium transition-colors ${
                    isDark
                      ? "bg-[#1E222D]/85 border-[#2A2E39] text-[#9DB2BD] hover:text-white"
                      : "bg-white/90 border-[#E0E3EB] text-[#4A505E] hover:text-[#131722]"
                  }`}
                >
                  <TvIcon
                    name="legend-sources-wrapper"
                    size={12}
                    className={`transition-transform ${studiesCollapsed ? "-rotate-90" : "rotate-180"}`}
                  />
                  <span>{overlayIndicators.length} chỉ báo</span>
                </button>
              )}

              <button
                type="button"
                onClick={onOpenIndicatorsModal}
                className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded border text-[10.5px] font-medium transition-colors ${
                  isDark
                    ? "bg-[#1E222D]/85 border-[#2A2E39] text-[#9DB2BD] hover:text-white"
                    : "bg-white/90 border-[#E0E3EB] text-[#4A505E] hover:text-[#131722]"
                }`}
              >
                <span>+ Chỉ báo</span>
              </button>
            </div>
          </>
        )}

        {!legendCollapsed && overlayIndicators.length === 0 && (
          <button
            type="button"
            onClick={onOpenIndicatorsModal}
            className={`inline-flex items-center gap-1 px-2 py-0.5 rounded border text-[10.5px] font-medium pointer-events-auto w-fit transition-colors ${
              isDark
                ? "bg-[#1E222D]/85 border-[#2A2E39] text-[#9DB2BD] hover:text-white"
                : "bg-white/90 border-[#E0E3EB] text-[#4A505E] hover:text-[#131722]"
            }`}
          >
            <span>+ Thêm chỉ báo</span>
          </button>
        )}
      </div>

      {/* Sub-Pane Headers (Positioned cleanly at the top-left of each sub-pane) */}
      {subPanes.map((pane) => {
        const comp = pane.computed
        if (!comp) return null
        return (
          <div
            key={pane.id}
            style={{ top: `${pane.top + 2}px` }}
            className={`absolute left-2.5 z-10 inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[11px] leading-tight pointer-events-auto select-none group transition-colors max-w-[calc(100%-92px)] ${
              isDark ? "bg-[#131722]/85 text-[#D1D4DC]" : "bg-white/85 text-[#131722]"
            } backdrop-blur-[2px]`}
          >
            <span
              className="w-2 h-2 rounded-full shrink-0"
              style={{ backgroundColor: comp.instance.color }}
            />
            <span className="font-semibold text-[11px] whitespace-nowrap">{comp.title}</span>

            {settings.showIndicatorValues && comp.instance.visible && !comp.instance.collapsed && (
              <div className="hidden sm:flex items-center gap-2 font-mono text-[10.5px] truncate">
                {comp.subCandles?.[hoveredBarIndex]?.close != null && (
                  <span
                    style={{ color: comp.subCandles[hoveredBarIndex].color }}
                    className="font-semibold whitespace-nowrap"
                  >
                    {fmtCompactAxisValue(comp.subCandles[hoveredBarIndex].close!)}
                  </span>
                )}
                {comp.plots.map((p) => {
                  const v = p.values[hoveredBarIndex]
                  if (v === null || v === undefined || !Number.isFinite(v)) return null
                  return (
                    <span
                      key={p.key}
                      style={{ color: p.pointColors?.[hoveredBarIndex] || p.color }}
                      className="whitespace-nowrap"
                    >
                      {p.label}: {fmtCompactAxisValue(v)}
                    </span>
                  )
                })}
              </div>
            )}

            {/* Sub-pane controls: Visibility, Settings, Collapse, Maximize, Remove */}
            <div className="flex items-center gap-0.5 opacity-70 group-hover:opacity-100 ml-0.5 shrink-0">
              <button
                type="button"
                onClick={() => onToggleIndicatorVisible(comp.instance.instanceId)}
                title={comp.instance.visible ? "Ẩn chỉ báo" : "Hiện chỉ báo"}
                className="p-0.5 rounded hover:bg-black/5 dark:hover:bg-white/10"
              >
                <TvIcon name="legend-show-hide-action" size={14} />
              </button>
              <button
                type="button"
                onClick={() => onOpenIndicatorSettings(comp.instance.instanceId)}
                title="Cài đặt thông số"
                className="p-0.5 rounded hover:bg-black/5 dark:hover:bg-white/10"
              >
                <TvIcon name="legend-settings-action" size={14} />
              </button>
              <button
                type="button"
                onClick={() => onToggleIndicatorCollapsed(comp.instance.instanceId)}
                title={comp.instance.collapsed ? "Mở rộng ngăn chỉ báo" : "Thu gọn ngăn chỉ báo"}
                className="p-0.5 rounded hover:bg-black/5 dark:hover:bg-white/10"
              >
                <TvIcon name="pane-button-collapse" size={13} />
              </button>
              <button
                type="button"
                onClick={() => onToggleIndicatorMaximized(comp.instance.instanceId)}
                title={comp.instance.maximized ? "Khôi phục kích thước" : "Phóng to ngăn chỉ báo"}
                className="p-0.5 rounded hover:bg-black/5 dark:hover:bg-white/10"
              >
                <TvIcon name="pane-button-maximize" size={13} />
              </button>
              <button
                type="button"
                onClick={() => onRemoveIndicator(comp.instance.instanceId)}
                title="Đóng ngăn chỉ báo"
                className="p-0.5 rounded hover:bg-[#F23645]/15 text-[#F23645]"
              >
                <TvIcon name="pane-button-close" size={13} />
              </button>
            </div>
          </div>
        )
      })}

      {/* Floating Bottom-Center TradingView Control Bar (Zoom Out / Zoom In / Scroll / Reset) */}
      <div
        data-name="control-bar"
        className={`absolute bottom-8 left-1/2 -translate-x-1/2 z-10 flex items-center gap-0.5 px-1.5 py-0.5 rounded-lg border shadow-md opacity-70 hover:opacity-100 transition-opacity select-none ${
          isDark
            ? "bg-[#1E222D]/95 border-[#2A2E39] text-[#D1D4DC]"
            : "bg-white/95 border-[#E0E3EB] text-[#131722]"
        }`}
      >
        <button
          type="button"
          onClick={onZoomOut}
          title="Thu nhỏ biểu đồ (Zoom Out)"
          className="w-6 h-6 rounded flex items-center justify-center hover:bg-black/5 dark:hover:bg-white/10 transition-colors"
        >
          <TvIcon name="control-bar" size={16} />
        </button>
        <button
          type="button"
          onClick={onZoomIn}
          title="Phóng to biểu đồ (Zoom In)"
          className="w-6 h-6 rounded flex items-center justify-center hover:bg-black/5 dark:hover:bg-white/10 transition-colors"
        >
          <TvIcon name="control-bar-zoom-in-button" size={16} />
        </button>
        <div className="w-px h-3.5 bg-current/15 mx-0.5" />
        <button
          type="button"
          onClick={onPanLeft}
          title="Cuộn sang trái (Lịch sử)"
          className="w-6 h-6 rounded flex items-center justify-center hover:bg-black/5 dark:hover:bg-white/10 transition-colors"
        >
          <TvIcon name="control-bar-scroll-left-button" size={15} className="-scale-x-100" />
        </button>
        <button
          type="button"
          onClick={onPanRight}
          title="Cuộn sang phải (Hiện tại)"
          className="w-6 h-6 rounded flex items-center justify-center hover:bg-black/5 dark:hover:bg-white/10 transition-colors"
        >
          <TvIcon name="control-bar-scroll-right-button" size={15} />
        </button>
        <div className="w-px h-3.5 bg-current/15 mx-0.5" />
        <button
          type="button"
          onClick={onResetView}
          title="Đặt lại khung nhìn chuẩn (Reset Chart View)"
          className="px-1.5 h-6 rounded flex items-center gap-1 hover:bg-black/5 dark:hover:bg-white/10 text-[10.5px] font-medium transition-colors"
        >
          <TvIcon name="control-bar-reset-scale-button" size={15} />
          <span className="hidden sm:inline">Mặc định</span>
        </button>
      </div>
    </>
  )
}
