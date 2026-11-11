/* ============================================================================
 * AIInvest TradingView-Grade Charting Library — Type Definitions
 * Reconstructed from TradingView Charting Engine (Free + Pro + Premium + Ultimate) via REA MCP
 * ============================================================================ */

export type TvTimeframe =
  // Seconds (TradingView Premium / Expert / Ultimate)
  | "1s"
  | "5s"
  | "15s"
  | "30s"
  // Minutes
  | "1m"
  | "3m"
  | "5m"
  | "15m"
  | "30m"
  | "45m"
  // Hours
  | "1H"
  | "2H"
  | "3H"
  | "4H"
  // Days, Weeks, Months
  | "1D"
  | "1W"
  | "1M"
  | "3M"
  | "6M"
  | "12M"

export type TvDateRange = "1D" | "5D" | "1M" | "3M" | "6M" | "YTD" | "1Y" | "5Y" | "ALL"

export type TvChartStyle =
  | "candles"
  | "hollow_candles"
  | "volume_candles"
  | "heikin_ashi"
  | "bars"
  | "line"
  | "area"
  | "hlc_area"
  | "baseline"
  | "columns"
  | "high_low"
  // TradingView Pro / Premium / Ultimate Non-Time-Based Chart Types
  | "renko"
  | "line_break"
  | "kagi"
  | "point_and_figure"
  | "range"

export type TvScaleMode = "normal" | "logarithmic" | "percentage"

export type TvMagnetMode = "off" | "weak" | "strong"

export type TvCursorMode = "cross" | "dot" | "arrow" | "eraser"

export interface TvCandle {
  /** Unix timestamp in milliseconds */
  timestamp: number
  /** Original date/datetime string */
  date: string
  /** Formatted label for X-axis */
  label: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  /** Optional Point & Figure or Kagi metadata */
  pfKind?: "X" | "O"
}

export type TvIndicatorCategory =
  | "trend"
  | "oscillator"
  | "volatility"
  | "volume"
  | "strategy"
  | "profile"
  | "pattern"
  | "candlestick"
  | "options"
  | "fundamental"
  | "community"
  | "custom"

export type TvDialogSidebarSection =
  | "my_scripts"
  | "purchased"
  | "technicals"
  | "fundamentals"
  | "editors_picks"
  | "top"
  | "trending"
  | "store"

export type TvTechnicalSubTab =
  | "indicators"
  | "strategies"
  | "profiles"
  | "patterns"
  | "options"

export type TvFundamentalSubTab =
  | "all"
  | "valuation"
  | "income"
  | "balance"
  | "cashflow"

export type TvTierBadge = "FREE" | "BETA" | "PRO" | "PREMIUM" | "ULTIMATE"

/**
 * Supports all 270+ built-in TradingView studies, strategies, profiles, patterns,
 * fundamentals, community scripts, store packages, and custom user scripts (`CUSTOM_*`).
 */
export type TvIndicatorId = string

export interface TvIndicatorParamDef {
  key: string
  label: string
  defaultValue: number
  min: number
  max: number
  step?: number
  options?: { value: number; label: string }[]
}

export interface TvIndicatorMetadata {
  id: TvIndicatorId
  /** Official TradingView English name (as shown in TradingView Indicators dialog) */
  name: string
  shortName: string
  /** Clear Vietnamese name & explanation */
  viName: string
  description: string
  category: TvIndicatorCategory
  /** Sidebar destination in TradingView's Indicators, Metrics & Strategies modal */
  section?: TvDialogSidebarSection
  /** Horizontal sub-tab inside Technicals */
  subTab?: TvTechnicalSubTab
  /** Horizontal sub-tab inside Fundamentals */
  fundamentalGroup?: TvFundamentalSubTab
  /** Original TradingView pine-facade / @tv-* script identifier extracted via REA */
  tvScriptId?: string
  /** Tier or BETA badge (100% unlocked in AIInvest) */
  badge?: TvTierBadge
  /** Author username & boost likes for Community / Store scripts */
  author?: string
  likes?: number
  /** Allow adding multiple simultaneous instances with different periods */
  allowMultiple?: boolean
  /** Custom user formula expression (for Tập lệnh của tôi) */
  formula?: string
  overlay: boolean
  defaultColor: string
  secondaryColor?: string
  tertiaryColor?: string
  params: TvIndicatorParamDef[]
  referenceLevels?: { value: number; color: string; label?: string; dashed?: boolean }[]
  fixedRange?: [number, number]
}

export interface TvCustomScriptDef {
  id: string
  name: string
  shortName: string
  description: string
  formula: string
  overlay: boolean
  color: string
  createdAt: number
}

export interface TvIndicatorInstance {
  instanceId: string
  id: TvIndicatorId
  visible: boolean
  collapsed?: boolean
  maximized?: boolean
  paneHeight?: number
  color: string
  secondaryColor?: string
  tertiaryColor?: string
  lineWidth: number
  params: Record<string, number>
  customFormula?: string
  customName?: string
  customOverlay?: boolean
}

export interface TvIndicatorPlotSeries {
  key: string
  label: string
  color: string
  type: "line" | "histogram" | "dots" | "cloud" | "step" | "area" | "columns" | "circles_line"
  lineWidth?: number
  dashed?: boolean
  values: (number | null)[]
  /** Optional per-bar color override (e.g. MACD histogram, Volume, Supertrend, HuD DE|TE) */
  pointColors?: (string | null)[]
}

export interface TvSubPaneCandle {
  open: number | null
  high: number | null
  low: number | null
  close: number | null
  color: string
  borderColor?: string
  wickColor?: string
}

export interface TvSubPaneBgHighlight {
  barIndex: number
  color: string
}

export interface TvIndicatorFillBand {
  upperKey: string
  lowerKey: string
  fillColor: string
  dynamicBullBear?: boolean
  bullColor?: string
  bearColor?: string
}

export interface TvBarMarker {
  barIndex: number
  position: "aboveBar" | "belowBar"
  shape: "arrowUp" | "arrowDown" | "badge" | "diamond" | "circle"
  color: string
  text: string
  subtext?: string
}

export interface TvVolumeProfileBin {
  priceLow: number
  priceHigh: number
  priceMid: number
  upVol: number
  downVol: number
  totalVol: number
  tpoLetters?: string
}

export interface TvVolumeProfileBlock {
  startBar: number
  endBar: number
  anchorSide: "right" | "left"
  widthRatio: number
  mode: "vp" | "tpo" | "footprint"
  pocPrice: number
  vahPrice: number
  valPrice: number
  maxBinVol: number
  bins: TvVolumeProfileBin[]
  label?: string
}

export interface TvOverlayZone {
  startBar: number
  endBar: number
  topPrice: number
  bottomPrice: number
  fillColor: string
  borderColor?: string
  dashed?: boolean
  label?: string
  labelColor?: string
  extendRight?: boolean
}

export interface TvOverlayPolyline {
  points: { barIndex: number; price: number; label?: string }[]
  color: string
  lineWidth?: number
  dashed?: boolean
  fillPolygonColor?: string
  extendRight?: boolean
  rightLabel?: string
}

export interface TvStrategyTradeRecord {
  tradeNo: number
  side: "LONG" | "SHORT"
  entryBar: number
  exitBar: number
  entryDate: string
  exitDate: string
  entryPrice: number
  exitPrice: number
  pnlPct: number
  cumPnlPct: number
  signalName: string
}

export interface TvStrategyBacktestReport {
  strategyName: string
  netProfitPct: number
  totalTrades: number
  winningTrades: number
  winRatePct: number
  profitFactor: number
  maxDrawdownPct: number
  avgTradePct: number
  sharpeRatio: number
  equityCurve: (number | null)[]
  trades: TvStrategyTradeRecord[]
}

export interface TvIndicatorComputed {
  instance: TvIndicatorInstance
  meta: TvIndicatorMetadata
  title: string
  plots: TvIndicatorPlotSeries[]
  subCandles?: TvSubPaneCandle[]
  subBgHighlights?: TvSubPaneBgHighlight[]
  subPaneBadge?: { text: string; color: string }
  bands?: TvIndicatorFillBand[]
  markers?: TvBarMarker[]
  volumeProfiles?: TvVolumeProfileBlock[]
  zones?: TvOverlayZone[]
  polylines?: TvOverlayPolyline[]
  strategyReport?: TvStrategyBacktestReport
}

export type TvDrawingGroupId =
  | "cursors"
  | "trend_lines"
  | "gann_fibonacci"
  | "patterns"
  | "prediction_measurement"
  | "geometric_shapes"
  | "annotation"

export type TvDrawingToolId =
  // Cursors
  | "cursor_cross"
  | "cursor_dot"
  | "cursor_arrow"
  | "cursor_eraser"
  // Group 1: Trend Lines & Channels (data-name="linetool-group-trend-line")
  | "LineToolTrendLine"
  | "LineToolRay"
  | "LineToolInfoLine"
  | "LineToolExtended"
  | "LineToolTrendAngle"
  | "LineToolHorzLine"
  | "LineToolHorzRay"
  | "LineToolVertLine"
  | "LineToolCrossLine"
  | "LineToolParallelChannel"
  | "LineToolRegressionTrend"
  | "LineToolFlatBottom"
  | "LineToolDisjointAngle"
  // Group 2: Gann & Fibonacci & Pitchforks (data-name="linetool-group-gann-and-fibonacci")
  | "LineToolPitchfork"
  | "LineToolSchiffPitchfork2"
  | "LineToolInsidePitchfork"
  | "LineToolPitchfan"
  | "LineToolFibRetracement"
  | "LineToolTrendBasedFibExtension"
  | "LineToolFibChannel"
  | "LineToolFibTimeZone"
  | "LineToolFibSpeedResistanceFan"
  | "LineToolTrendBasedFibTime"
  | "LineToolFibCircles"
  | "LineToolFibSpiral"
  | "LineToolFibSpeedResistanceArcs"
  | "LineToolFibWedge"
  | "LineToolGannSquare"
  | "LineToolGannFixed"
  | "LineToolGannComplex"
  | "LineToolGannFan"
  // Group 3: Patterns (data-name="linetool-group-patterns")
  | "LineTool5PointsPattern"
  | "LineToolCypherPattern"
  | "LineToolThreeDrivers"
  | "LineToolHeadAndShoulders"
  | "LineToolABCD"
  | "LineToolTrianglePattern"
  | "LineToolElliottImpulse"
  | "LineToolElliottCorrection"
  | "LineToolElliottTriangle"
  | "LineToolElliottDoubleCombo"
  | "LineToolElliottTripleCombo"
  | "LineToolCircleLines"
  | "LineToolTimeCycles"
  | "LineToolSineLine"
  // Group 4: Prediction & Measurement (data-name="linetool-group-prediction-and-measurement")
  | "LineToolRiskRewardLong"
  | "LineToolRiskRewardShort"
  | "LineToolPrediction"
  | "LineToolBarsPattern"
  | "LineToolGhostFeed"
  | "LineToolProjection"
  | "LineToolAnchoredVWAP"
  | "LineToolFixedRangeVolumeProfile"
  | "LineToolAnchoredVolumeProfile"
  | "LineToolPriceRange"
  | "LineToolDateRange"
  | "LineToolDateAndPriceRange"
  // Group 5: Geometric Shapes (data-name="linetool-group-geometric-shapes")
  | "LineToolBrush"
  | "LineToolHighlighter"
  | "LineToolArrowMarker"
  | "LineToolArrow"
  | "LineToolArrowMarkUp"
  | "LineToolArrowMarkDown"
  | "LineToolRectangle"
  | "LineToolRotatedRectangle"
  | "LineToolPath"
  | "LineToolCircle"
  | "LineToolEllipse"
  | "LineToolPolyline"
  | "LineToolTriangle"
  | "LineToolArc"
  | "LineToolBezierQuadro"
  | "LineToolBezierCubic"
  // Group 6: Annotation Tools (data-name="linetool-group-annotation")
  | "LineToolText"
  | "LineToolTextAbsolute"
  | "LineToolNote"
  | "LineToolTextNote"
  | "LineToolCallout"
  | "LineToolComment"
  | "LineToolPriceLabel"
  | "LineToolPriceNote"
  | "LineToolSignpost"
  | "LineToolFlagMark"

export interface TvDrawingToolDefinition {
  id: TvDrawingToolId
  group: TvDrawingGroupId
  name: string
  viName: string
  iconName: string
  /** Number of clicks required to complete tool (0 for cursor, -1 for freehand/multi-click) */
  requiredPoints: number
  shortcut?: string
  defaultColor?: string
  defaultText?: string
}

export interface TvDrawingPoint {
  /** Bar index in current series (can be fractional or beyond last bar) */
  barIndex: number
  /** Unix timestamp ms of the bar (or extrapolated) */
  timestamp: number
  /** Price in VND */
  price: number
}

export interface TvDrawingObject {
  id: string
  tool: TvDrawingToolId
  group: TvDrawingGroupId
  points: TvDrawingPoint[]
  color: string
  fillColor: string
  lineWidth: number
  lineStyle: "solid" | "dashed" | "dotted"
  extendLeft?: boolean
  extendRight?: boolean
  text?: string
  fontSize?: number
  locked: boolean
  visible: boolean
  createdAt: number
}

export interface TvOpenOrderOverlay {
  id: string
  side: "BUY" | "SELL"
  orderType: string
  price: number
  quantity: number
  status: string
}

export interface TvTradingOverlay {
  symbol: string
  stockName?: string
  exchange?: string
  currentPrice: number
  refPrice: number
  ceilingPrice: number
  floorPrice: number
  isLive: boolean
  orderSide: "BUY" | "SELL"
  orderType?: "LO" | "MP" | "ATO" | "ATC"
  orderPrice: number
  /** True only when user explicitly entered or picked a custom limit order price */
  hasDraftOrder?: boolean
  orderQuantity?: number
  bestBidPrice?: number
  bestAskPrice?: number
  positionAvgPrice?: number | null
  positionQuantity?: number | null
  openOrders?: TvOpenOrderOverlay[]
  onPickPriceFromChart: (price: number) => void
  onClearOrderPrice?: () => void
  onChangeOrderSide?: (side: "BUY" | "SELL") => void
  onQuickOrderSubmit?: (side: "BUY" | "SELL", price: number) => void
  onSymbolChange?: (newSymbol: string) => void
}

export interface TvComparisonSeries {
  symbol: string
  name: string
  color: string
  candles: TvCandle[]
  visible: boolean
}

export interface TvReplayState {
  active: boolean
  selectingStartBar: boolean
  playing: boolean
  cutoffIndex: number
  speedMs: number
}

export interface TvChartSettings {
  theme: "light" | "dark"
  showGrid: boolean
  showWatermark: boolean
  showHoseBands: boolean
  showOrderLine: boolean
  showPositionLine: boolean
  showOpenOrders: boolean
  showCrosshairLabels: boolean
  showSessionBreaks: boolean
  showBarChangeValues: boolean
  showSeriesOHLC: boolean
  showIndicatorValues: boolean
  upColor: string
  downColor: string
  borderUpColor: string
  borderDownColor: string
  wickUpColor: string
  wickDownColor: string
  rightOffsetBars: number
}

export interface TvViewportState {
  /** Index of the leftmost visible bar (can be float for smooth panning) */
  startIndex: number
  /** Index of the rightmost visible bar (can be > candles.length - 1 for right offset) */
  endIndex: number
  /** Auto-fit Y price scale to visible bars */
  autoScale: boolean
  /** Manual price scale multiplier when user drags right Y-axis (< 1 zooms in, > 1 zooms out) */
  manualPriceScaleFactor: number
  /** Manual vertical price pan offset in VND when user pans vertically in manual scale mode */
  manualPricePanDelta: number
  /** Scale mode: normal, logarithmic, percentage */
  scaleMode: TvScaleMode
}
