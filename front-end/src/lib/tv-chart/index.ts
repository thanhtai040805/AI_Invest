/* ============================================================================
 * @/lib/tv-chart — AIInvest Standalone TradingView-Grade Charting Library
 * Reconstructed via REA MCP (Free + Essential + Plus + Pro + Premium + Ultimate):
 * - 287+ Unlocked Technical Studies, Backtest Strategies, Volume/TPO Profiles,
 *   Auto Chart & Candlestick Patterns, Options Analytics, Fundamentals,
 *   Community / Store / VIP Vault Scripts & Custom Formula Studio
 * - 16 Chart Styles (incl. Renko, Line Break, Kagi, Point & Figure, Range)
 * - 20 Timeframes (1s .. 12M), 68 Drawing Tools, Multi-Pane HTML5 Canvas 2D Engine
 * ============================================================================ */

// Main Component
export { TradingViewChart, type TradingViewChartProps } from "./components/TradingViewChart"

// Sub-Toolbars, Docked Panels & Legend
export { TopHeaderToolbar, type TopHeaderToolbarProps } from "./toolbars/TopHeaderToolbar"
export { LeftDrawingToolbar, type LeftDrawingToolbarProps } from "./toolbars/LeftDrawingToolbar"
export {
  FloatingDrawingToolbar,
  type FloatingDrawingToolbarProps,
} from "./toolbars/FloatingDrawingToolbar"
export { PaneLegend, type PaneLegendProps } from "./toolbars/PaneLegend"
export {
  StrategyTesterPanel,
  type StrategyTesterPanelProps,
} from "./toolbars/StrategyTesterPanel"
export { BottomToolbar, type BottomToolbarProps } from "./toolbars/BottomToolbar"

// Modals & Dialogs
export { IndicatorsModal, type IndicatorsModalProps } from "./modals/IndicatorsModal"
export { ObjectTreeModal, type ObjectTreeModalProps } from "./modals/ObjectTreeModal"
export {
  ChartSettingsModal,
  DEFAULT_TV_CHART_SETTINGS,
  type ChartSettingsModalProps,
} from "./modals/ChartSettingsModal"

// Rendering Engine & Drawing Engine
export {
  computeChartMetrics,
  renderChartFrame,
  fmtPriceVnd,
  fmtCompactAxisValue,
  PRICE_AXIS_WIDTH,
  TIME_AXIS_HEIGHT,
  PANE_SPLITTER_HEIGHT,
  type PaneLayoutBox,
  type RenderFrameMetrics,
  type RenderFrameParams,
} from "./engine/canvas-renderer"

export {
  renderSingleDrawing,
  hitTestDrawing,
  type DrawingCoordConverter,
} from "./drawings/drawing-renderer"

export {
  TV_DRAWING_GROUPS,
  TV_DRAWING_TOOL_MAP,
  type TvDrawingGroupMeta,
} from "./drawings/tool-groups"

// Indicators Catalog, Registry & Calculator
export {
  ALL_CATALOG_INDICATORS,
  BUILTIN_TECHNICAL_STUDIES,
  BUILTIN_STRATEGIES,
  PROFILE_STUDIES,
  AUTO_CHART_PATTERNS,
  CANDLESTICK_PATTERNS,
  OPTIONS_STUDIES,
  FUNDAMENTAL_STUDIES,
  COMMUNITY_AND_VIP_STUDIES,
} from "./indicators/catalog-data"

export {
  TV_INDICATOR_REGISTRY,
  TV_INDICATOR_LIST,
  DEFAULT_ACTIVE_INDICATORS,
  createIndicatorInstance,
  getIndicatorMetadata,
  registerCustomScripts,
} from "./indicators/registry"

export {
  computeIndicator,
  computeHeikinAshi,
  computeRenko,
  computeLineBreak,
  computeKagi,
  computePointAndFigure,
  computeRangeBars,
  transformSeriesForChartStyle,
  evaluateCustomFormula,
  calcSMA,
  calcEMA,
  calcRMA,
  calcWMA,
  calcTrueRange,
  type IndicatorComputeContext,
} from "./indicators/calculator"

// Datafeed & HOSE Tick Helpers
export {
  fetchTimeframeCandles,
  normalizeRawCandles,
  getHoseTickStep,
  roundToHoseTick,
  toVndPrice,
  type RawInputCandle,
} from "./adapters/aiinvest-datafeed"

// Icons
export { TvIcon, TV_RAW_SVGS, type TvIconProps } from "./icons/tv-icons"

// Types
export type {
  TvTimeframe,
  TvDateRange,
  TvChartStyle,
  TvScaleMode,
  TvMagnetMode,
  TvCursorMode,
  TvCandle,
  TvIndicatorCategory,
  TvDialogSidebarSection,
  TvTechnicalSubTab,
  TvFundamentalSubTab,
  TvTierBadge,
  TvIndicatorId,
  TvIndicatorParamDef,
  TvIndicatorMetadata,
  TvCustomScriptDef,
  TvIndicatorInstance,
  TvIndicatorPlotSeries,
  TvSubPaneCandle,
  TvSubPaneBgHighlight,
  TvIndicatorFillBand,
  TvBarMarker,
  TvVolumeProfileBin,
  TvVolumeProfileBlock,
  TvOverlayZone,
  TvOverlayPolyline,
  TvStrategyTradeRecord,
  TvStrategyBacktestReport,
  TvIndicatorComputed,
  TvDrawingGroupId,
  TvDrawingToolId,
  TvDrawingToolDefinition,
  TvDrawingPoint,
  TvDrawingObject,
  TvOpenOrderOverlay,
  TvTradingOverlay,
  TvComparisonSeries,
  TvReplayState,
  TvChartSettings,
  TvViewportState,
} from "./types"
