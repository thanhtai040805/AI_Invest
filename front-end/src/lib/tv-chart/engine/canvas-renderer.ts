/* ============================================================================
 * TradingView Multi-Pane HTML5 Canvas 2D Rendering Engine
 * High-DPI (devicePixelRatio) Renderer for Main Price Pane, Overlay Studies,
 * HOSE Visual Trading Lines, 68 Drawing Tools, Sub-Indicator Panes & Axes
 * ============================================================================ */

import { renderSingleDrawing, type DrawingCoordConverter } from "../drawings/drawing-renderer"
import type {
  TvCandle,
  TvChartSettings,
  TvChartStyle,
  TvComparisonSeries,
  TvDrawingObject,
  TvIndicatorComputed,
  TvReplayState,
  TvTradingOverlay,
  TvViewportState,
} from "../types"

export const PRICE_AXIS_WIDTH = 78
export const TIME_AXIS_HEIGHT = 26
export const PANE_SPLITTER_HEIGHT = 5

export interface PaneLayoutBox {
  id: string // "main" or indicator instanceId
  title: string
  top: number
  bottom: number
  height: number
  isMain: boolean
  computed?: TvIndicatorComputed
}

export interface RenderFrameParams {
  ctx: CanvasRenderingContext2D
  width: number
  height: number
  candles: TvCandle[]
  displayCandles: TvCandle[] // e.g. Heikin-Ashi when chartStyle === "heikin_ashi"
  chartStyle: TvChartStyle
  viewport: TvViewportState
  settings: TvChartSettings
  overlayIndicators: TvIndicatorComputed[]
  subPaneIndicators: TvIndicatorComputed[]
  comparisons: TvComparisonSeries[]
  drawings: TvDrawingObject[]
  draftDrawing: TvDrawingObject | null
  selectedDrawingId: string | null
  hoveredDrawingId: string | null
  trading?: TvTradingOverlay
  replay: TvReplayState
  mousePos: { x: number; y: number; inside: boolean }
  isDraggingOrderLine: boolean
  zoomBox?: { x1: number; y1: number; x2: number; y2: number } | null
}

export interface RenderFrameMetrics {
  plotLeft: number
  plotRight: number
  plotWidth: number
  plotTop: number
  plotBottom: number
  mainPane: PaneLayoutBox
  subPanes: PaneLayoutBox[]
  minPrice: number
  maxPrice: number
  basePrice: number
  barStep: number
  barWidth: number
  coordConverter: DrawingCoordConverter
}

export function fmtPriceVnd(price: number): string {
  if (!Number.isFinite(price)) return "—"
  return Math.round(price).toLocaleString("vi-VN")
}

export function fmtCompactAxisValue(val: number): string {
  if (!Number.isFinite(val)) return "—"
  const abs = Math.abs(val)
  if (abs >= 1_000_000_000) return `${(val / 1_000_000_000).toFixed(1)}B`
  if (abs >= 1_000_000) return `${(val / 1_000_000).toFixed(1)}M`
  if (abs >= 10_000) return Math.round(val).toLocaleString("vi-VN")
  if (abs >= 100) return val.toFixed(1)
  return val.toFixed(2)
}

function drawAxisPill(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  width: number,
  text: string,
  bgColor: string,
  textColor = "#FFFFFF"
) {
  const h = 19
  const ry = Math.round(y - h / 2)
  ctx.save()
  ctx.fillStyle = bgColor
  ctx.beginPath()
  ctx.roundRect(x + 2, ry, width - 4, h, 3.5)
  ctx.fill()

  ctx.fillStyle = textColor
  ctx.font = "600 10.5px 'JetBrains Mono', monospace"
  ctx.textAlign = "left"
  ctx.textBaseline = "middle"
  ctx.fillText(text, x + 7, y + 0.5)
  ctx.restore()
}

function computeNiceTicks(minVal: number, maxVal: number, targetCount = 6): number[] {
  const span = maxVal - minVal
  if (!Number.isFinite(span) || span <= 0) return []
  const rawStep = span / Math.max(2, targetCount)
  const mag = Math.pow(10, Math.floor(Math.log10(rawStep)))
  const norm = rawStep / mag
  let niceMult = 1
  if (norm >= 7.5) niceMult = 10
  else if (norm >= 3.5) niceMult = 5
  else if (norm >= 2.25) niceMult = 2.5
  else if (norm >= 1.5) niceMult = 2
  const step = Math.max(10, niceMult * mag)
  const first = Math.ceil(minVal / step) * step
  const ticks: number[] = []
  for (let v = first; v <= maxVal + step * 0.001 && ticks.length < 20; v += step) {
    ticks.push(Math.round(v))
  }
  return ticks
}

export function computeChartMetrics(
  width: number,
  height: number,
  candles: TvCandle[],
  displayCandles: TvCandle[],
  viewport: TvViewportState,
  settings: TvChartSettings,
  overlayIndicators: TvIndicatorComputed[],
  subPaneIndicators: TvIndicatorComputed[],
  trading?: TvTradingOverlay
): RenderFrameMetrics {
  const plotLeft = 0
  const plotRight = Math.max(120, width - PRICE_AXIS_WIDTH)
  const plotWidth = plotRight - plotLeft
  const plotTop = 0
  const plotBottom = Math.max(120, height - TIME_AXIS_HEIGHT)
  const totalPanesHeight = plotBottom - plotTop

  // Allocate heights between main pane and visible sub-panes safely (never overflowing plotBottom)
  const visibleSubs = subPaneIndicators.filter((ind) => ind.instance.visible)
  const maximizedSub = visibleSubs.find((ind) => ind.instance.maximized)

  const subPanes: PaneLayoutBox[] = []
  let mainPaneHeight = totalPanesHeight

  if (maximizedSub) {
    mainPaneHeight = 0
    subPanes.push({
      id: maximizedSub.instance.instanceId,
      title: maximizedSub.title,
      top: plotTop,
      bottom: plotBottom,
      height: totalPanesHeight,
      isMain: false,
      computed: maximizedSub,
    })
  } else if (visibleSubs.length > 0) {
    const minMainHeight = Math.max(150, Math.floor(totalPanesHeight * 0.44))
    const maxTotalSubHeight = Math.max(60, totalPanesHeight - minMainHeight)
    const rawHeights = visibleSubs.map((s) =>
      s.instance.collapsed ? 26 : Math.max(48, s.instance.paneHeight ?? 92)
    )
    const rawTotal = rawHeights.reduce((a, b) => a + b, 0)
    const scale = rawTotal > maxTotalSubHeight ? maxTotalSubHeight / rawTotal : 1
    const actualSubHeights = rawHeights.map((h, i) =>
      visibleSubs[i].instance.collapsed ? Math.min(26, Math.floor(h * scale)) : Math.max(36, Math.floor(h * scale))
    )
    // Re-normalize if minimum floor pushed sum slightly above maxTotalSubHeight
    const sumActual = actualSubHeights.reduce((a, b) => a + b, 0)
    const finalSubHeights =
      sumActual > maxTotalSubHeight
        ? actualSubHeights.map((h) => Math.max(24, Math.floor((h * maxTotalSubHeight) / sumActual)))
        : actualSubHeights

    const totalSubUsed = finalSubHeights.reduce((a, b) => a + b, 0)
    mainPaneHeight = Math.max(120, totalPanesHeight - totalSubUsed)

    let cursorY = plotTop + mainPaneHeight
    visibleSubs.forEach((s, idx) => {
      const h =
        idx === visibleSubs.length - 1
          ? Math.max(24, plotBottom - cursorY)
          : Math.min(finalSubHeights[idx], Math.max(24, plotBottom - cursorY))
      subPanes.push({
        id: s.instance.instanceId,
        title: s.title,
        top: cursorY,
        bottom: cursorY + h,
        height: h,
        isMain: false,
        computed: s,
      })
      cursorY += h
    })
  }

  const mainPane: PaneLayoutBox = {
    id: "main",
    title: "Price",
    top: plotTop,
    bottom: plotTop + mainPaneHeight,
    height: mainPaneHeight,
    isMain: true,
  }

  // Visible bar slice
  const visibleCount = Math.max(5, viewport.endIndex - viewport.startIndex + 1)
  const barStep = plotWidth / visibleCount
  const barWidth = Math.max(1.5, Math.min(22, barStep * 0.72))

  const iStart = Math.max(0, Math.floor(viewport.startIndex))
  const iEnd = Math.min(displayCandles.length - 1, Math.ceil(viewport.endIndex))

  let rawMin = Infinity
  let rawMax = -Infinity
  for (let i = iStart; i <= iEnd; i++) {
    const c = displayCandles[i]
    if (!c) continue
    if (c.low < rawMin) rawMin = c.low
    if (c.high > rawMax) rawMax = c.high
  }

  const candleMin = rawMin
  const candleMax = rawMax

  // Include visible overlay indicator bounds (clamped reasonably around candle price range)
  for (const ov of overlayIndicators) {
    if (!ov.instance.visible) continue
    for (const plot of ov.plots) {
      for (let i = iStart; i <= iEnd; i++) {
        const v = plot.values[i]
        if (v != null && Number.isFinite(v) && v > 0) {
          if (v < rawMin) rawMin = v
          if (v > rawMax) rawMax = v
        }
      }
    }
    if (Number.isFinite(candleMin) && Number.isFinite(candleMax)) {
      const floorClamp = candleMin * 0.72
      const ceilClamp = candleMax * 1.38
      if (ov.polylines) {
        for (const pl of ov.polylines) {
          for (const pt of pl.points) {
            if (pt.barIndex >= iStart - 15 && pt.barIndex <= iEnd + 25 && pt.price >= floorClamp && pt.price <= ceilClamp) {
              if (pt.price < rawMin) rawMin = pt.price
              if (pt.price > rawMax) rawMax = pt.price
            }
          }
        }
      }
      if (ov.zones) {
        for (const zn of ov.zones) {
          if (zn.endBar >= iStart && zn.startBar <= iEnd) {
            if (zn.bottomPrice >= floorClamp && zn.bottomPrice < rawMin) rawMin = zn.bottomPrice
            if (zn.topPrice <= ceilClamp && zn.topPrice > rawMax) rawMax = zn.topPrice
          }
        }
      }
    }
  }

  // Only include draft orderPrice in auto-scale when user explicitly has a draft limit order
  if (
    trading &&
    settings.showOrderLine &&
    trading.hasDraftOrder &&
    trading.orderPrice > 0 &&
    Number.isFinite(rawMin)
  ) {
    const mid = (rawMin + rawMax) / 2
    if (Math.abs(trading.orderPrice - mid) / Math.max(1, mid) < 0.14) {
      rawMin = Math.min(rawMin, trading.orderPrice)
      rawMax = Math.max(rawMax, trading.orderPrice)
    }
  }

  if (!Number.isFinite(rawMin) || !Number.isFinite(rawMax) || rawMax <= rawMin) {
    const fallback = trading?.currentPrice || 20_000
    rawMin = fallback * 0.95
    rawMax = fallback * 1.05
  }

  const span = Math.max(100, rawMax - rawMin)
  const pad = span * 0.09
  let minPrice = rawMin - pad
  let maxPrice = rawMax + pad

  if (!viewport.autoScale) {
    const mid = (minPrice + maxPrice) / 2 + viewport.manualPricePanDelta
    const half = ((maxPrice - minPrice) / 2) * Math.max(0.15, Math.min(6, viewport.manualPriceScaleFactor))
    minPrice = Math.max(10, mid - half)
    maxPrice = Math.max(minPrice + 50, mid + half)
  }

  const baseCandle = displayCandles[iStart] ?? displayCandles[0]
  const basePrice = baseCandle?.close > 0 ? baseCandle.close : (minPrice + maxPrice) / 2

  const mTop = mainPane.top + 10
  const mBot = Math.max(mTop + 20, mainPane.bottom - 10)
  const mH = mBot - mTop

  const priceToY = (price: number): number => {
    if (viewport.scaleMode === "logarithmic") {
      const logMin = Math.log10(Math.max(1, minPrice))
      const logMax = Math.log10(Math.max(10, maxPrice))
      const logVal = Math.log10(Math.max(1, price))
      const ratio = (logVal - logMin) / Math.max(1e-6, logMax - logMin)
      return mBot - ratio * mH
    }
    const ratio = (price - minPrice) / Math.max(1, maxPrice - minPrice)
    return mBot - ratio * mH
  }

  const yToPrice = (y: number): number => {
    const ratio = (mBot - y) / Math.max(1, mH)
    if (viewport.scaleMode === "logarithmic") {
      const logMin = Math.log10(Math.max(1, minPrice))
      const logMax = Math.log10(Math.max(10, maxPrice))
      return Math.pow(10, logMin + ratio * (logMax - logMin))
    }
    return minPrice + ratio * (maxPrice - minPrice)
  }

  const barIndexToX = (barIndex: number): number => {
    return plotLeft + (barIndex - viewport.startIndex + 0.5) * barStep
  }

  const xToBarIndex = (x: number): number => {
    return viewport.startIndex + (x - plotLeft) / barStep - 0.5
  }

  return {
    plotLeft,
    plotRight,
    plotWidth,
    plotTop,
    plotBottom,
    mainPane,
    subPanes,
    minPrice,
    maxPrice,
    basePrice,
    barStep,
    barWidth,
    coordConverter: {
      barIndexToX,
      xToBarIndex,
      priceToY,
      yToPrice,
      plotLeft,
      plotRight,
      plotTop: mainPane.top,
      plotBottom: mainPane.bottom,
      candles,
    },
  }
}

export function renderChartFrame(params: RenderFrameParams): RenderFrameMetrics {
  const {
    ctx,
    width,
    height,
    candles,
    displayCandles,
    chartStyle,
    viewport,
    settings,
    overlayIndicators,
    subPaneIndicators,
    comparisons,
    drawings,
    draftDrawing,
    selectedDrawingId,
    hoveredDrawingId,
    trading,
    replay,
    mousePos,
    isDraggingOrderLine,
    zoomBox,
  } = params

  const metrics = computeChartMetrics(
    width,
    height,
    candles,
    displayCandles,
    viewport,
    settings,
    overlayIndicators,
    subPaneIndicators,
    trading
  )

  const {
    plotLeft,
    plotRight,
    plotWidth,
    plotTop,
    plotBottom,
    mainPane,
    subPanes,
    minPrice,
    maxPrice,
    basePrice,
    barStep,
    barWidth,
    coordConverter,
  } = metrics

  const isDark = settings.theme === "dark"
  const bgColor = isDark ? "#131722" : "#FFFFFF"
  const axisBgColor = isDark ? "#131722" : "#FAFBF9"
  const gridColor = isDark ? "rgba(42, 46, 57, 0.75)" : "rgba(215, 221, 217, 0.58)"
  const borderColor = isDark ? "#2A2E39" : "#E0E3EB"
  const textMuted = isDark ? "#787B86" : "#787B86"
  const textInk = isDark ? "#D1D4DC" : "#131722"

  // Clear entire canvas
  ctx.fillStyle = bgColor
  ctx.fillRect(0, 0, width, height)

  // Axis backgrounds
  ctx.fillStyle = axisBgColor
  ctx.fillRect(plotRight, 0, PRICE_AXIS_WIDTH, height)
  ctx.fillRect(0, plotBottom, width, TIME_AXIS_HEIGHT)

  const iStart = Math.max(0, Math.floor(viewport.startIndex))
  const iEnd = Math.min(displayCandles.length - 1, Math.ceil(viewport.endIndex))

  /* ==========================================================================
   * 1. VERTICAL TIME GRIDLINES & BOTTOM TIME AXIS LABELS
   * ========================================================================== */
  const targetTickSpacingPx = 110
  const stepBars = Math.max(1, Math.round(targetTickSpacingPx / Math.max(1, barStep)))

  ctx.save()
  ctx.font = "500 10.5px 'JetBrains Mono', monospace"
  ctx.textAlign = "center"
  ctx.textBaseline = "middle"

  let lastDrawnLabelX = -999
  for (let i = iStart; i <= iEnd; i++) {
    if (i % stepBars !== 0 && i !== displayCandles.length - 1) continue
    const x = Math.round(coordConverter.barIndexToX(i)) + 0.5
    if (x < plotLeft + 26 || x > plotRight - 26) continue
    if (x - lastDrawnLabelX < 74) continue
    lastDrawnLabelX = x

    if (settings.showGrid) {
      ctx.strokeStyle = gridColor
      ctx.lineWidth = 1
      ctx.beginPath()
      ctx.moveTo(x, plotTop)
      ctx.lineTo(x, plotBottom)
      ctx.stroke()
    }

    ctx.fillStyle = textMuted
    ctx.fillText(displayCandles[i].label, x, plotBottom + TIME_AXIS_HEIGHT / 2)
  }
  ctx.restore()

  const nicePriceTicks = computeNiceTicks(
    minPrice,
    maxPrice,
    Math.max(4, Math.floor(mainPane.height / 54))
  )

  /* ==========================================================================
   * 2. MAIN PRICE PANE RENDERING
   * ========================================================================== */
  if (mainPane.height > 0) {
    ctx.save()
    ctx.beginPath()
    ctx.rect(plotLeft, mainPane.top, plotWidth, mainPane.height)
    ctx.clip()

    // Watermark (Symbol + Stock Name)
    if (settings.showWatermark && trading?.symbol) {
      ctx.save()
      ctx.fillStyle = isDark ? "rgba(255, 255, 255, 0.035)" : "rgba(19, 23, 34, 0.035)"
      ctx.font = "800 52px 'Be Vietnam Pro', sans-serif"
      ctx.textAlign = "center"
      ctx.textBaseline = "middle"
      ctx.fillText(trading.symbol, plotWidth / 2, mainPane.top + mainPane.height * 0.46)
      if (trading.stockName) {
        ctx.font = "600 15px 'Be Vietnam Pro', sans-serif"
        ctx.fillText(trading.stockName, plotWidth / 2, mainPane.top + mainPane.height * 0.46 + 36)
      }
      ctx.restore()
    }

    // Horizontal Price Gridlines (Nice Rounded Ticks)
    for (const pVal of nicePriceTicks) {
      const y = Math.round(coordConverter.priceToY(pVal)) + 0.5
      if (y < mainPane.top + 6 || y > mainPane.bottom - 6) continue

      if (settings.showGrid) {
        ctx.strokeStyle = gridColor
        ctx.lineWidth = 1
        ctx.beginPath()
        ctx.moveTo(plotLeft, y)
        ctx.lineTo(plotRight, y)
        ctx.stroke()
      }
    }

    // 2.1 Overlay Indicator Shaded Bands (Bollinger, Ichimoku Kumo, Donchian, Keltner)
    for (const ov of overlayIndicators) {
      if (!ov.instance.visible || !ov.bands) continue
      for (const band of ov.bands) {
        const upPlot = ov.plots.find((p) => p.key === band.upperKey)
        const lowPlot = ov.plots.find((p) => p.key === band.lowerKey)
        if (!upPlot || !lowPlot) continue

        for (let i = Math.max(1, iStart); i <= iEnd; i++) {
          const u0 = upPlot.values[i - 1]
          const l0 = lowPlot.values[i - 1]
          const u1 = upPlot.values[i]
          const l1 = lowPlot.values[i]
          if (u0 == null || l0 == null || u1 == null || l1 == null) continue

          const x0 = coordConverter.barIndexToX(i - 1)
          const x1 = coordConverter.barIndexToX(i)
          ctx.fillStyle = band.dynamicBullBear
            ? u1 >= l1
              ? band.bullColor || band.fillColor
              : band.bearColor || band.fillColor
            : band.fillColor

          ctx.beginPath()
          ctx.moveTo(x0, coordConverter.priceToY(u0))
          ctx.lineTo(x1, coordConverter.priceToY(u1))
          ctx.lineTo(x1, coordConverter.priceToY(l1))
          ctx.lineTo(x0, coordConverter.priceToY(l0))
          ctx.closePath()
          ctx.fill()
        }
      }
    }

    // 2.2 Main Series (All 16 TradingView Standard + Pro + Ultimate Chart Styles)
    const upColor = settings.upColor || "#2B805B"
    const downColor = settings.downColor || "#B55250"

    if (chartStyle === "hlc_area") {
      ctx.save()
      // Shaded High–Low channel
      for (let i = Math.max(1, iStart); i <= iEnd; i++) {
        const c0 = displayCandles[i - 1]
        const c1 = displayCandles[i]
        if (!c0 || !c1) continue
        const x0 = coordConverter.barIndexToX(i - 1)
        const x1 = coordConverter.barIndexToX(i)
        ctx.fillStyle = "rgba(41, 98, 255, 0.14)"
        ctx.beginPath()
        ctx.moveTo(x0, coordConverter.priceToY(c0.high))
        ctx.lineTo(x1, coordConverter.priceToY(c1.high))
        ctx.lineTo(x1, coordConverter.priceToY(c1.low))
        ctx.lineTo(x0, coordConverter.priceToY(c0.low))
        ctx.closePath()
        ctx.fill()
      }
      // High, Low & Close lines
      const drawSeriesLine = (getter: (c: TvCandle) => number, col: string, lw: number) => {
        ctx.strokeStyle = col
        ctx.lineWidth = lw
        ctx.beginPath()
        let started = false
        for (let i = iStart; i <= iEnd; i++) {
          const c = displayCandles[i]
          if (!c) continue
          const x = coordConverter.barIndexToX(i)
          const y = coordConverter.priceToY(getter(c))
          if (!started) { ctx.moveTo(x, y); started = true } else ctx.lineTo(x, y)
        }
        ctx.stroke()
      }
      drawSeriesLine((c) => c.high, "rgba(8, 153, 129, 0.75)", 1.1)
      drawSeriesLine((c) => c.low, "rgba(242, 54, 69, 0.75)", 1.1)
      drawSeriesLine((c) => c.close, "#2962FF", 2)
      ctx.restore()
    } else if (chartStyle === "high_low") {
      for (let i = iStart; i <= iEnd; i++) {
        const c = displayCandles[i]
        if (!c) continue
        const x = coordConverter.barIndexToX(i) - barWidth / 2
        const yHigh = coordConverter.priceToY(c.high)
        const yLow = coordConverter.priceToY(c.low)
        ctx.fillStyle = "rgba(41, 98, 255, 0.52)"
        ctx.fillRect(x, yHigh, barWidth, Math.max(2, yLow - yHigh))
      }
    } else if (chartStyle === "kagi") {
      ctx.save()
      for (let i = iStart; i <= iEnd; i++) {
        const c = displayCandles[i]
        if (!c) continue
        const isYang = c.pfKind ? c.pfKind === "X" : c.close >= c.open
        const col = isYang ? upColor : downColor
        const lw = isYang ? 2.6 : 1.3
        const cx = coordConverter.barIndexToX(i)
        const yOpen = coordConverter.priceToY(c.open)
        const yClose = coordConverter.priceToY(c.close)
        ctx.strokeStyle = col
        ctx.lineWidth = lw
        ctx.beginPath()
        if (i > iStart && displayCandles[i - 1]) {
          const prevX = coordConverter.barIndexToX(i - 1)
          ctx.moveTo(prevX, yOpen)
          ctx.lineTo(cx, yOpen)
        } else {
          ctx.moveTo(cx, yOpen)
        }
        ctx.lineTo(cx, yClose)
        ctx.stroke()
      }
      ctx.restore()
    } else if (chartStyle === "point_and_figure") {
      ctx.save()
      const fontSize = Math.max(8, Math.min(13, Math.floor(barWidth * 0.95)))
      ctx.font = `700 ${fontSize}px 'JetBrains Mono', monospace`
      ctx.textAlign = "center"
      ctx.textBaseline = "middle"
      for (let i = iStart; i <= iEnd; i++) {
        const c = displayCandles[i]
        if (!c) continue
        const isX = c.pfKind ? c.pfKind === "X" : c.close >= c.open
        const cx = coordConverter.barIndexToX(i)
        const yTop = coordConverter.priceToY(c.high)
        const yBot = coordConverter.priceToY(c.low)
        const spanPx = Math.max(fontSize, Math.abs(yBot - yTop))
        const count = Math.max(1, Math.min(20, Math.round(spanPx / (fontSize + 1))))
        ctx.fillStyle = isX ? upColor : downColor
        for (let k = 0; k < count; k++) {
          const py = count === 1 ? (yTop + yBot) / 2 : yTop + (k / (count - 1)) * (yBot - yTop)
          ctx.fillText(isX ? "×" : "○", cx, py)
        }
      }
      ctx.restore()
    } else if (chartStyle === "line" || chartStyle === "area" || chartStyle === "baseline") {
      const baselinePrice = (minPrice + maxPrice) / 2
      const baselineY = coordConverter.priceToY(baselinePrice)

      if (chartStyle === "area") {
        ctx.save()
        ctx.beginPath()
        let firstX = 0
        let lastX = 0
        let started = false
        for (let i = iStart; i <= iEnd; i++) {
          const c = displayCandles[i]
          if (!c) continue
          const x = coordConverter.barIndexToX(i)
          const y = coordConverter.priceToY(c.close)
          if (!started) {
            firstX = x
            ctx.moveTo(x, y)
            started = true
          } else {
            ctx.lineTo(x, y)
          }
          lastX = x
        }
        if (started) {
          ctx.lineTo(lastX, mainPane.bottom)
          ctx.lineTo(firstX, mainPane.bottom)
          ctx.closePath()
          const grad = ctx.createLinearGradient(0, mainPane.top, 0, mainPane.bottom)
          grad.addColorStop(0, "rgba(41, 98, 255, 0.28)")
          grad.addColorStop(1, "rgba(41, 98, 255, 0.01)")
          ctx.fillStyle = grad
          ctx.fill()
        }
        ctx.restore()
      }

      if (chartStyle === "baseline") {
        ctx.save()
        ctx.strokeStyle = textMuted
        ctx.setLineDash([4, 4])
        ctx.beginPath()
        ctx.moveTo(plotLeft, baselineY)
        ctx.lineTo(plotRight, baselineY)
        ctx.stroke()
        ctx.restore()
      }

      ctx.save()
      ctx.lineWidth = 2
      for (let i = Math.max(1, iStart); i <= iEnd; i++) {
        const c0 = displayCandles[i - 1]
        const c1 = displayCandles[i]
        if (!c0 || !c1) continue
        const x0 = coordConverter.barIndexToX(i - 1)
        const y0 = coordConverter.priceToY(c0.close)
        const x1 = coordConverter.barIndexToX(i)
        const y1 = coordConverter.priceToY(c1.close)

        ctx.strokeStyle =
          chartStyle === "baseline"
            ? c1.close >= baselinePrice
              ? upColor
              : downColor
            : chartStyle === "area"
              ? "#2962FF"
              : upColor
        ctx.beginPath()
        ctx.moveTo(x0, y0)
        ctx.lineTo(x1, y1)
        ctx.stroke()
      }
      ctx.restore()
    } else if (chartStyle === "columns") {
      for (let i = iStart; i <= iEnd; i++) {
        const c = displayCandles[i]
        if (!c) continue
        const isUp = c.close >= c.open
        const x = coordConverter.barIndexToX(i) - barWidth / 2
        const y = coordConverter.priceToY(c.close)
        ctx.fillStyle = isUp ? "rgba(43, 128, 91, 0.65)" : "rgba(181, 82, 80, 0.65)"
        ctx.fillRect(x, y, barWidth, Math.max(1, mainPane.bottom - y))
      }
    } else if (chartStyle === "bars") {
      for (let i = iStart; i <= iEnd; i++) {
        const c = displayCandles[i]
        if (!c) continue
        const isUp = c.close >= c.open
        const color = isUp ? upColor : downColor
        const cx = Math.round(coordConverter.barIndexToX(i)) + 0.5
        const yHigh = coordConverter.priceToY(c.high)
        const yLow = coordConverter.priceToY(c.low)
        const yOpen = coordConverter.priceToY(c.open)
        const yClose = coordConverter.priceToY(c.close)
        const tickW = Math.max(2, barWidth / 2)

        ctx.strokeStyle = color
        ctx.lineWidth = 1.4
        ctx.beginPath()
        ctx.moveTo(cx, yHigh)
        ctx.lineTo(cx, yLow)
        ctx.moveTo(cx - tickW, yOpen)
        ctx.lineTo(cx, yOpen)
        ctx.moveTo(cx, yClose)
        ctx.lineTo(cx + tickW, yClose)
        ctx.stroke()
      }
    } else {
      // candles, hollow_candles, volume_candles, heikin_ashi, renko, line_break, range
      let avgVisVol = 1
      if (chartStyle === "volume_candles") {
        let vSum = 0, vCnt = 0
        for (let i = iStart; i <= iEnd; i++) {
          if (displayCandles[i]) { vSum += displayCandles[i].volume; vCnt++ }
        }
        avgVisVol = Math.max(1, vSum / Math.max(1, vCnt))
      }

      for (let i = iStart; i <= iEnd; i++) {
        const c = displayCandles[i]
        if (!c) continue
        const isUp = c.close >= c.open
        const color = isUp ? upColor : downColor
        const cx = Math.round(coordConverter.barIndexToX(i)) + 0.5
        const yHigh = coordConverter.priceToY(c.high)
        const yLow = coordConverter.priceToY(c.low)
        const yOpen = coordConverter.priceToY(c.open)
        const yClose = coordConverter.priceToY(c.close)
        const bodyTop = Math.min(yOpen, yClose)
        const bodyH = Math.max(1.5, Math.abs(yClose - yOpen))
        const effWidth =
          chartStyle === "volume_candles"
            ? Math.max(2, Math.min(barStep * 0.94, barWidth * Math.max(0.45, Math.min(1.65, c.volume / avgVisVol))))
            : barWidth
        const bx = cx - effWidth / 2

        // Wick (skip for pure Renko / Line Break bricks where high/low equal open/close)
        if (chartStyle !== "renko" && chartStyle !== "line_break") {
          ctx.strokeStyle = color
          ctx.lineWidth = 1.2
          ctx.beginPath()
          ctx.moveTo(cx, yHigh)
          ctx.lineTo(cx, yLow)
          ctx.stroke()
        }

        // Body
        if (chartStyle === "hollow_candles" && isUp) {
          ctx.fillStyle = bgColor
          ctx.fillRect(bx, bodyTop, effWidth, bodyH)
          ctx.strokeStyle = color
          ctx.lineWidth = 1.3
          ctx.strokeRect(bx, bodyTop, effWidth, bodyH)
        } else {
          ctx.fillStyle = color
          ctx.fillRect(bx, bodyTop, effWidth, bodyH)
        }
      }
    }

    // 2.3 Comparison Series Overlays
    for (const comp of comparisons) {
      if (!comp.visible || comp.candles.length === 0) continue
      const compStartIdx = Math.max(0, Math.min(comp.candles.length - 1, iStart))
      const compBase = comp.candles[compStartIdx]?.close || comp.candles[0].close
      if (compBase <= 0) continue

      ctx.save()
      ctx.strokeStyle = comp.color
      ctx.lineWidth = 1.8
      ctx.beginPath()
      let started = false
      for (let i = iStart; i <= iEnd; i++) {
        const compIdx = Math.min(comp.candles.length - 1, i)
        const cc = comp.candles[compIdx]
        if (!cc) continue
        const normPrice = basePrice * (cc.close / compBase)
        const x = coordConverter.barIndexToX(i)
        const y = coordConverter.priceToY(normPrice)
        if (!started) {
          ctx.moveTo(x, y)
          started = true
        } else {
          ctx.lineTo(x, y)
        }
      }
      ctx.stroke()
      ctx.restore()
    }

    // 2.4 Overlay Indicator Plots (SMA, EMA, WMA, BB, Ichimoku, VWAP, PSAR, Supertrend, Pivots)
    for (const ov of overlayIndicators) {
      if (!ov.instance.visible) continue
      for (const plot of ov.plots) {
        ctx.save()
        ctx.lineWidth = plot.lineWidth ?? ov.instance.lineWidth ?? 1.5
        if (plot.dashed) ctx.setLineDash([4, 4])

        if (plot.type === "dots") {
          for (let i = iStart; i <= iEnd; i++) {
            const v = plot.values[i]
            if (v == null) continue
            const x = coordConverter.barIndexToX(i)
            const y = coordConverter.priceToY(v)
            ctx.fillStyle = plot.pointColors?.[i] || plot.color
            ctx.beginPath()
            ctx.arc(x, y, 2.6, 0, Math.PI * 2)
            ctx.fill()
          }
        } else {
          for (let i = Math.max(1, iStart); i <= iEnd; i++) {
            const v0 = plot.values[i - 1]
            const v1 = plot.values[i]
            if (v0 == null || v1 == null) continue
            const x0 = coordConverter.barIndexToX(i - 1)
            const y0 = coordConverter.priceToY(v0)
            const x1 = coordConverter.barIndexToX(i)
            const y1 = coordConverter.priceToY(v1)
            ctx.strokeStyle = plot.pointColors?.[i] || plot.color
            ctx.beginPath()
            ctx.moveTo(x0, y0)
            if (plot.type === "step") {
              ctx.lineTo(x1, y0)
              ctx.lineTo(x1, y1)
            } else {
              ctx.lineTo(x1, y1)
            }
            ctx.stroke()
          }
        }
        ctx.restore()
      }
    }

    // 2.4B Pro / Premium / Ultimate Overlay Primitives: Zones, Volume Profiles (VRVP/TPO/Footprint), Polylines & Markers
    const aboveStack = new Map<number, number>()
    const belowStack = new Map<number, number>()

    for (const ov of overlayIndicators) {
      if (!ov.instance.visible) continue

      // (a) Overlay Zones (Order Blocks, FVG, Gaps, Sessions, MTF Boxes, Darvas, Price Target)
      if (ov.zones && ov.zones.length > 0) {
        for (const zn of ov.zones) {
          if (zn.endBar < iStart - 5 && !zn.extendRight) continue
          if (zn.startBar > iEnd + 5) continue
          const x1 = Math.max(plotLeft, coordConverter.barIndexToX(zn.startBar) - barWidth / 2)
          const x2 = zn.extendRight
            ? plotRight
            : Math.min(plotRight, coordConverter.barIndexToX(zn.endBar) + barWidth / 2)
          if (x2 <= x1) continue
          const yTop = coordConverter.priceToY(zn.topPrice)
          const yBot = coordConverter.priceToY(zn.bottomPrice)
          const rectTop = Math.min(yTop, yBot)
          const rectH = Math.max(2, Math.abs(yBot - yTop))

          ctx.save()
          ctx.fillStyle = zn.fillColor
          ctx.fillRect(x1, rectTop, x2 - x1, rectH)
          if (zn.borderColor) {
            ctx.strokeStyle = zn.borderColor
            ctx.lineWidth = 1.1
            if (zn.dashed) ctx.setLineDash([4, 3])
            ctx.strokeRect(x1 + 0.5, rectTop + 0.5, Math.max(1, x2 - x1 - 1), Math.max(1, rectH - 1))
          }
          if (zn.label && x2 - x1 > 28) {
            ctx.setLineDash([])
            ctx.font = "600 9.5px 'JetBrains Mono', monospace"
            ctx.fillStyle = zn.labelColor || zn.borderColor || textInk
            ctx.textAlign = zn.extendRight ? "right" : "left"
            ctx.textBaseline = "top"
            ctx.fillText(zn.label, zn.extendRight ? x2 - 6 : x1 + 5, rectTop + 3)
          }
          ctx.restore()
        }
      }

      // (b) Volume Profile / Market Profile TPO / Volume Footprint Blocks
      if (ov.volumeProfiles && ov.volumeProfiles.length > 0) {
        for (const vp of ov.volumeProfiles) {
          if (vp.endBar < iStart - 10 || vp.startBar > iEnd + 10) continue
          const spanPx =
            vp.anchorSide === "right"
              ? plotWidth * Math.max(0.12, Math.min(0.42, vp.widthRatio))
              : Math.max(28, (vp.endBar - vp.startBar + 1) * barStep * Math.max(0.25, Math.min(0.92, vp.widthRatio)))
          const anchorX =
            vp.anchorSide === "right"
              ? plotRight
              : Math.max(plotLeft, coordConverter.barIndexToX(vp.startBar) - barWidth / 2)

          // Value Area subtle shaded backdrop
          const yVah = coordConverter.priceToY(vp.vahPrice)
          const yVal = coordConverter.priceToY(vp.valPrice)
          ctx.save()
          ctx.fillStyle = isDark ? "rgba(41, 98, 255, 0.08)" : "rgba(41, 98, 255, 0.055)"
          const vaLeft = vp.anchorSide === "right" ? anchorX - spanPx : anchorX
          ctx.fillRect(vaLeft, Math.min(yVah, yVal), spanPx, Math.abs(yVal - yVah))

          // Horizontal Histogram Bins (Up Vol + Down Vol) or TPO Letters / Footprint Delta
          for (const bin of vp.bins) {
            if (bin.totalVol <= 0) continue
            const yHi = coordConverter.priceToY(bin.priceHigh)
            const yLo = coordConverter.priceToY(bin.priceLow)
            const bh = Math.max(1.5, Math.abs(yLo - yHi) - 1)
            const by = Math.min(yHi, yLo) + 0.5
            const inVA = bin.priceMid >= vp.valPrice && bin.priceMid <= vp.vahPrice
            const alpha = inVA ? 0.68 : 0.34
            const totalW = (bin.totalVol / Math.max(1e-6, vp.maxBinVol)) * spanPx
            const upW = totalW * (bin.upVol / Math.max(1e-6, bin.totalVol))
            const dnW = Math.max(0, totalW - upW)

            if (vp.mode === "tpo" && bin.tpoLetters) {
              ctx.fillStyle = inVA ? "rgba(41, 98, 255, 0.22)" : "rgba(120, 123, 134, 0.14)"
              const bx = vp.anchorSide === "right" ? anchorX - totalW : anchorX
              ctx.fillRect(bx, by, totalW, bh)
              if (bh >= 7) {
                ctx.font = "600 8.5px 'JetBrains Mono', monospace"
                ctx.fillStyle = inVA ? "#2962FF" : textMuted
                ctx.textAlign = vp.anchorSide === "right" ? "right" : "left"
                ctx.textBaseline = "middle"
                ctx.fillText(
                  bin.tpoLetters.slice(0, 14),
                  vp.anchorSide === "right" ? anchorX - 3 : anchorX + 3,
                  by + bh / 2
                )
              }
            } else {
              if (vp.anchorSide === "right") {
                ctx.fillStyle = `rgba(38, 166, 154, ${alpha})`
                ctx.fillRect(anchorX - upW, by, upW, bh)
                ctx.fillStyle = `rgba(239, 83, 80, ${alpha})`
                ctx.fillRect(anchorX - upW - dnW, by, dnW, bh)
              } else {
                ctx.fillStyle = `rgba(38, 166, 154, ${alpha})`
                ctx.fillRect(anchorX, by, upW, bh)
                ctx.fillStyle = `rgba(239, 83, 80, ${alpha})`
                ctx.fillRect(anchorX + upW, by, dnW, bh)
              }
              if (vp.mode === "footprint" && bh >= 8 && spanPx >= 36) {
                const netDelta = bin.upVol - bin.downVol
                ctx.font = "600 8px 'JetBrains Mono', monospace"
                ctx.fillStyle = netDelta >= 0 ? "#089981" : "#F23645"
                ctx.textAlign = "left"
                ctx.textBaseline = "middle"
                ctx.fillText(
                  `${netDelta >= 0 ? "+" : ""}${fmtCompactAxisValue(netDelta)}`,
                  anchorX + 2,
                  by + bh / 2
                )
              }
            }
          }

          // POC (Point of Control) Amber Line + VAH / VAL Dashed Lines
          const xLineStart = Math.max(plotLeft, coordConverter.barIndexToX(vp.startBar))
          const xLineEnd = vp.anchorSide === "right" ? plotRight : Math.min(plotRight, coordConverter.barIndexToX(vp.endBar) + barWidth / 2)
          const yPoc = Math.round(coordConverter.priceToY(vp.pocPrice)) + 0.5

          ctx.strokeStyle = "#F59E0B"
          ctx.lineWidth = 1.8
          ctx.beginPath()
          ctx.moveTo(xLineStart, yPoc)
          ctx.lineTo(xLineEnd, yPoc)
          ctx.stroke()

          ctx.strokeStyle = "rgba(41, 98, 255, 0.65)"
          ctx.lineWidth = 1
          ctx.setLineDash([3, 3])
          ctx.beginPath()
          ctx.moveTo(xLineStart, yVah)
          ctx.lineTo(xLineEnd, yVah)
          ctx.moveTo(xLineStart, yVal)
          ctx.lineTo(xLineEnd, yVal)
          ctx.stroke()
          ctx.setLineDash([])

          if (vp.label && vp.mode !== "footprint") {
            ctx.font = "700 9.5px 'JetBrains Mono', monospace"
            ctx.fillStyle = "#F59E0B"
            ctx.textAlign = vp.anchorSide === "right" ? "right" : "left"
            ctx.textBaseline = "bottom"
            ctx.fillText(
              `${vp.label} POC ${fmtPriceVnd(vp.pocPrice)}`,
              vp.anchorSide === "right" ? plotRight - 6 : xLineStart + 4,
              yPoc - 2
            )
          }
          ctx.restore()
        }
      }

      // (c) Overlay Polylines & Polygons (Auto Fib, Auto Pitchfork, Trendlines, Chart Patterns, ZigZag)
      if (ov.polylines && ov.polylines.length > 0) {
        for (const pl of ov.polylines) {
          if (pl.points.length < 2) continue
          ctx.save()
          const pts = pl.points.map((pt) => ({
            x: coordConverter.barIndexToX(pt.barIndex),
            y: coordConverter.priceToY(pt.price),
            barIndex: pt.barIndex,
            price: pt.price,
            label: pt.label,
          }))

          if (pl.fillPolygonColor && pts.length >= 3) {
            ctx.fillStyle = pl.fillPolygonColor
            ctx.beginPath()
            ctx.moveTo(pts[0].x, pts[0].y)
            for (let k = 1; k < pts.length; k++) ctx.lineTo(pts[k].x, pts[k].y)
            ctx.closePath()
            ctx.fill()
          }

          ctx.strokeStyle = pl.color
          ctx.lineWidth = pl.lineWidth ?? 1.5
          if (pl.dashed) ctx.setLineDash([5, 4])
          ctx.beginPath()
          ctx.moveTo(pts[0].x, pts[0].y)
          for (let k = 1; k < pts.length; k++) ctx.lineTo(pts[k].x, pts[k].y)

          let endLabelX = pts[pts.length - 1].x
          let endLabelY = pts[pts.length - 1].y
          if (pl.extendRight && pts.length >= 2) {
            const pA = pts[pts.length - 2]
            const pB = pts[pts.length - 1]
            if (pB.x < plotRight && Math.abs(pB.x - pA.x) > 0.5) {
              const slope = (pB.y - pA.y) / (pB.x - pA.x)
              endLabelX = plotRight - 4
              endLabelY = pB.y + slope * (plotRight - pB.x)
              ctx.lineTo(plotRight, endLabelY)
            }
          }
          ctx.stroke()
          ctx.setLineDash([])

          // Vertex labels (X, A, B, C, D, Wave (1)..(5))
          for (const pt of pts) {
            if (!pt.label || pt.x < plotLeft || pt.x > plotRight) continue
            ctx.font = "700 9.5px 'JetBrains Mono', monospace"
            const tw = ctx.measureText(pt.label).width + 8
            ctx.fillStyle = isDark ? "rgba(19, 23, 34, 0.88)" : "rgba(255, 255, 255, 0.9)"
            ctx.beginPath()
            ctx.roundRect(pt.x - tw / 2, pt.y - 17, tw, 14, 3)
            ctx.fill()
            ctx.strokeStyle = pl.color
            ctx.lineWidth = 1
            ctx.stroke()
            ctx.fillStyle = pl.color
            ctx.textAlign = "center"
            ctx.textBaseline = "middle"
            ctx.fillText(pt.label, pt.x, pt.y - 10)
          }

          // Right-side level label (e.g. Fib 0.618 (21.450))
          if (pl.rightLabel && endLabelY >= mainPane.top + 8 && endLabelY <= mainPane.bottom - 8) {
            ctx.font = "600 9.5px 'JetBrains Mono', monospace"
            ctx.fillStyle = pl.color
            ctx.textAlign = "right"
            ctx.textBaseline = "bottom"
            ctx.fillText(pl.rightLabel, Math.min(plotRight - 6, endLabelX), endLabelY - 2)
          }
          ctx.restore()
        }
      }

      // (d) Bar Markers & Signal Badges (Strategies BUY/SELL, 45 Candlestick Patterns, BOS/CHoCH, Pivots HL)
      if (ov.markers && ov.markers.length > 0) {
        for (const mk of ov.markers) {
          if (mk.barIndex < iStart || mk.barIndex > iEnd) continue
          const c = displayCandles[mk.barIndex]
          if (!c) continue
          const cx = coordConverter.barIndexToX(mk.barIndex)
          const isAbove = mk.position === "aboveBar"
          const stackCount = (isAbove ? aboveStack.get(mk.barIndex) : belowStack.get(mk.barIndex)) ?? 0
          if (isAbove) aboveStack.set(mk.barIndex, stackCount + 1)
          else belowStack.set(mk.barIndex, stackCount + 1)

          const baseY = isAbove
            ? coordConverter.priceToY(c.high) - 10 - stackCount * 20
            : coordConverter.priceToY(c.low) + 10 + stackCount * 20

          ctx.save()
          if (mk.shape === "arrowUp") {
            ctx.fillStyle = mk.color
            ctx.beginPath()
            ctx.moveTo(cx, baseY - 5)
            ctx.lineTo(cx - 5, baseY + 3)
            ctx.lineTo(cx + 5, baseY + 3)
            ctx.closePath()
            ctx.fill()
            if (mk.text) {
              ctx.font = "700 9px 'JetBrains Mono', monospace"
              ctx.textAlign = "center"
              ctx.textBaseline = "top"
              ctx.fillText(mk.text, cx, baseY + 5)
            }
          } else if (mk.shape === "arrowDown") {
            ctx.fillStyle = mk.color
            ctx.beginPath()
            ctx.moveTo(cx, baseY + 5)
            ctx.lineTo(cx - 5, baseY - 3)
            ctx.lineTo(cx + 5, baseY - 3)
            ctx.closePath()
            ctx.fill()
            if (mk.text) {
              ctx.font = "700 9px 'JetBrains Mono', monospace"
              ctx.textAlign = "center"
              ctx.textBaseline = "bottom"
              ctx.fillText(mk.text, cx, baseY - 5)
            }
          } else if (mk.shape === "diamond" || mk.shape === "circle") {
            ctx.fillStyle = mk.color
            ctx.beginPath()
            if (mk.shape === "circle") {
              ctx.arc(cx, baseY, 4, 0, Math.PI * 2)
            } else {
              ctx.moveTo(cx, baseY - 4.5)
              ctx.lineTo(cx + 4.5, baseY)
              ctx.lineTo(cx, baseY + 4.5)
              ctx.lineTo(cx - 4.5, baseY)
              ctx.closePath()
            }
            ctx.fill()
            if (mk.text) {
              ctx.font = "600 8.5px 'JetBrains Mono', monospace"
              ctx.textAlign = "center"
              ctx.textBaseline = isAbove ? "bottom" : "top"
              ctx.fillText(mk.text, cx, isAbove ? baseY - 6 : baseY + 6)
            }
          } else {
            // Pill badge
            ctx.font = "700 9px 'JetBrains Mono', monospace"
            const w = Math.max(20, ctx.measureText(mk.text).width + 9)
            const h = 15
            const by = isAbove ? baseY - h : baseY
            ctx.fillStyle = mk.color
            ctx.beginPath()
            ctx.roundRect(cx - w / 2, by, w, h, 3.5)
            ctx.fill()
            ctx.fillStyle = "#FFFFFF"
            ctx.textAlign = "center"
            ctx.textBaseline = "middle"
            ctx.fillText(mk.text, cx, by + h / 2 + 0.5)
          }
          ctx.restore()
        }
      }
    }

    // 2.5 Clean TradingView Price Lines (Current Market Price, Active Position, Explicit Draft Order)
    if (trading) {
      // Authentic TradingView slim outlined tag (18px height, crisp 1px border, never bulky solid banner)
      const drawTvOrderTag = (
        price: number,
        color: string,
        dash: number[],
        leftText: string,
        rightText: string,
        lineWidth = 1,
        showCloseX = false
      ) => {
        if (price <= 0) return
        const y = Math.round(coordConverter.priceToY(price)) + 0.5
        if (y < mainPane.top + 10 || y > mainPane.bottom - 10) return

        ctx.save()
        ctx.strokeStyle = color
        ctx.lineWidth = lineWidth
        ctx.setLineDash(dash)
        ctx.beginPath()
        ctx.moveTo(plotLeft, y)
        ctx.lineTo(plotRight, y)
        ctx.stroke()
        ctx.setLineDash([])

        ctx.font = "600 10px 'JetBrains Mono', monospace"
        const wLeft = ctx.measureText(leftText).width + 12
        const wRight = rightText ? ctx.measureText(rightText).width + 10 : 0
        const wClose = showCloseX ? 18 : 0
        const totalW = wLeft + wRight + wClose
        const bx = Math.max(12, plotRight - totalW - 88)
        const by = Math.round(y - 9)
        const bh = 18

        // Crisp background + colored border (TradingView native order line style)
        ctx.fillStyle = bgColor
        ctx.beginPath()
        ctx.roundRect(bx, by, totalW, bh, 3)
        ctx.fill()

        // Colored left segment fill
        ctx.fillStyle = color
        ctx.beginPath()
        ctx.roundRect(bx, by, wLeft, bh, [3, 0, 0, 3])
        ctx.fill()

        ctx.strokeStyle = color
        ctx.lineWidth = 1
        ctx.beginPath()
        ctx.roundRect(bx + 0.5, by + 0.5, totalW - 1, bh - 1, 3)
        ctx.stroke()

        // Left text (white on colored badge)
        ctx.fillStyle = "#FFFFFF"
        ctx.textAlign = "left"
        ctx.textBaseline = "middle"
        ctx.fillText(leftText, bx + 6, y + 0.5)

        // Right text (colored on clean background)
        if (rightText) {
          ctx.fillStyle = color
          ctx.fillText(rightText, bx + wLeft + 5, y + 0.5)
        }

        // Optional close button divider + X
        if (showCloseX) {
          const cx = bx + wLeft + wRight
          ctx.strokeStyle = color
          ctx.beginPath()
          ctx.moveTo(cx + 0.5, by)
          ctx.lineTo(cx + 0.5, by + bh)
          ctx.stroke()
          ctx.fillStyle = textMuted
          ctx.textAlign = "center"
          ctx.fillText("✕", cx + wClose / 2, y + 0.5)
        }

        ctx.restore()
      }

      // Optional HOSE Reference/Ceiling/Floor lines (OFF by default like TradingView; ultra-subtle if enabled)
      if (settings.showHoseBands) {
        const drawSubtleRefLine = (price: number, color: string, tag: string) => {
          if (price <= 0) return
          const y = Math.round(coordConverter.priceToY(price)) + 0.5
          if (y < mainPane.top + 8 || y > mainPane.bottom - 8) return
          ctx.save()
          ctx.globalAlpha = 0.5
          ctx.strokeStyle = color
          ctx.lineWidth = 1
          ctx.setLineDash([4, 4])
          ctx.beginPath()
          ctx.moveTo(plotLeft, y)
          ctx.lineTo(plotRight, y)
          ctx.stroke()
          ctx.globalAlpha = 0.85
          ctx.font = "500 9.5px 'JetBrains Mono', monospace"
          ctx.fillStyle = color
          ctx.textAlign = "right"
          ctx.textBaseline = "bottom"
          ctx.fillText(tag, plotRight - 6, y - 2)
          ctx.restore()
        }
        drawSubtleRefLine(trading.ceilingPrice, "#9C27B0", `Trần ${fmtPriceVnd(trading.ceilingPrice)}`)
        drawSubtleRefLine(trading.refPrice, "#CA8A04", `TC ${fmtPriceVnd(trading.refPrice)}`)
        drawSubtleRefLine(trading.floorPrice, "#00ACC1", `Sàn ${fmtPriceVnd(trading.floorPrice)}`)
      }

      // Portfolio Position Avg Cost Line (Only if user actually holds shares of this symbol)
      if (
        settings.showPositionLine &&
        trading.positionAvgPrice &&
        trading.positionAvgPrice > 0 &&
        trading.positionQuantity &&
        trading.positionQuantity > 0
      ) {
        const pnlPct = (
          ((trading.currentPrice - trading.positionAvgPrice) / trading.positionAvgPrice) *
          100
        ).toFixed(1)
        const sign = Number(pnlPct) >= 0 ? "+" : ""
        drawTvOrderTag(
          trading.positionAvgPrice,
          "#2962FF",
          [3, 3],
          `Vị thế ${trading.positionQuantity.toLocaleString("vi-VN")}`,
          `${sign}${pnlPct}%`,
          1
        )
      }

      // Open Pending Orders on Chart
      if (settings.showOpenOrders && trading.openOrders && trading.openOrders.length > 0) {
        for (const ord of trading.openOrders) {
          const c = ord.side === "BUY" ? upColor : downColor
          drawTvOrderTag(
            ord.price,
            c,
            [4, 3],
            `${ord.side === "BUY" ? "Chờ Mua" : "Chờ Bán"} ${ord.orderType}`,
            `${ord.quantity.toLocaleString("vi-VN")}`,
            1
          )
        }
      }

      // Current Market Price Dotted Line (Standard TradingView Last Price Line)
      if (trading.currentPrice > 0) {
        const lastBar = displayCandles[displayCandles.length - 1]
        const curColor = lastBar
          ? lastBar.close >= lastBar.open
            ? upColor
            : downColor
          : trading.currentPrice >= trading.refPrice
            ? upColor
            : downColor
        const yCur = Math.round(coordConverter.priceToY(trading.currentPrice)) + 0.5
        if (yCur >= mainPane.top && yCur <= mainPane.bottom) {
          ctx.save()
          ctx.strokeStyle = curColor
          ctx.lineWidth = 1
          ctx.setLineDash([2, 2])
          ctx.beginPath()
          ctx.moveTo(plotLeft, yCur)
          ctx.lineTo(plotRight, yCur)
          ctx.stroke()
          ctx.restore()
        }
      }

      // Interactive Draggable Draft Order Line — ONLY shown when user explicitly picked/entered a Limit price
      if (settings.showOrderLine && trading.hasDraftOrder && trading.orderPrice > 0) {
        const ordColor = trading.orderSide === "BUY" ? upColor : downColor
        const sideLabel = trading.orderSide === "BUY" ? "MUA LO" : "BÁN LO"
        const qtyStr = trading.orderQuantity
          ? `${trading.orderQuantity.toLocaleString("vi-VN")} CP`
          : ""
        drawTvOrderTag(
          trading.orderPrice,
          ordColor,
          isDraggingOrderLine ? [] : [5, 3],
          `⇅ ${sideLabel}`,
          qtyStr,
          isDraggingOrderLine ? 1.6 : 1.1,
          Boolean(trading.onClearOrderPrice)
        )
      }
    }

    // 2.6 Saved & Active Draft Drawings
    for (const d of drawings) {
      renderSingleDrawing(
        ctx,
        d,
        coordConverter,
        d.id === selectedDrawingId,
        d.id === hoveredDrawingId
      )
    }
    if (draftDrawing) {
      renderSingleDrawing(ctx, draftDrawing, coordConverter, true, false)
    }

    ctx.restore()

    /* ----------------------------------------------------------------------
     * MAIN PANE RIGHT Y-AXIS LABELS & ANTI-COLLISION LIVE PILLS
     * ---------------------------------------------------------------------- */
    ctx.save()
    const occupiedYs: number[] = []
    let yOrdPill: number | null = null
    let yCurPill: number | null = null

    if (trading) {
      if (settings.showOrderLine && trading.hasDraftOrder && trading.orderPrice > 0) {
        const yO = coordConverter.priceToY(trading.orderPrice)
        if (yO >= mainPane.top + 9 && yO <= mainPane.bottom - 9) {
          yOrdPill = yO
          occupiedYs.push(yO)
        }
      }
      if (trading.currentPrice > 0) {
        const yC = coordConverter.priceToY(trading.currentPrice)
        if (yC >= mainPane.top + 9 && yC <= mainPane.bottom - 9) {
          yCurPill = yC
          occupiedYs.push(yC)
        }
      }
    }
    if (
      mousePos.inside &&
      mousePos.x >= plotLeft &&
      mousePos.x <= plotRight &&
      mousePos.y >= mainPane.top &&
      mousePos.y <= mainPane.bottom &&
      settings.showCrosshairLabels
    ) {
      occupiedYs.push(mousePos.y)
    }

    ctx.font = "500 10.5px 'JetBrains Mono', monospace"
    ctx.fillStyle = textMuted
    ctx.textAlign = "left"
    ctx.textBaseline = "middle"

    for (const pVal of nicePriceTicks) {
      const y = Math.round(coordConverter.priceToY(pVal))
      if (y < mainPane.top + 11 || y > mainPane.bottom - 11) continue
      // Skip tick label if it would collide with an active price pill
      if (occupiedYs.some((oy) => Math.abs(oy - y) < 18)) continue

      const labelText =
        viewport.scaleMode === "percentage"
          ? `${(((pVal - basePrice) / Math.max(1, basePrice)) * 100).toFixed(2)}%`
          : fmtPriceVnd(pVal)
      ctx.fillText(labelText, plotRight + 7, y)
    }

    if (trading) {
      if (yCurPill !== null) {
        const lastBar = displayCandles[displayCandles.length - 1]
        const curColor = lastBar
          ? lastBar.close >= lastBar.open
            ? upColor
            : downColor
          : trading.currentPrice >= trading.refPrice
            ? upColor
            : downColor
        drawAxisPill(
          ctx,
          plotRight,
          yCurPill,
          PRICE_AXIS_WIDTH,
          fmtPriceVnd(trading.currentPrice),
          curColor
        )
      }
      if (yOrdPill !== null) {
        drawAxisPill(
          ctx,
          plotRight,
          yOrdPill,
          PRICE_AXIS_WIDTH,
          fmtPriceVnd(trading.orderPrice),
          trading.orderSide === "BUY" ? upColor : downColor
        )
      }
    }
    ctx.restore()
  }

  /* ==========================================================================
   * 3. SUB-INDICATOR PANES RENDERING (VOL, RSI, MACD, STOCH, ADX, ATR, CCI...)
   * ========================================================================== */
  for (const pane of subPanes) {
    const comp = pane.computed
    if (!comp) continue

    // Top border separator line
    ctx.strokeStyle = borderColor
    ctx.lineWidth = 1
    ctx.beginPath()
    ctx.moveTo(0, pane.top + 0.5)
    ctx.lineTo(width, pane.top + 0.5)
    ctx.stroke()

    if (comp.instance.collapsed || pane.height <= 30) continue

    const sTop = pane.top + 22
    const sBot = pane.bottom - 6
    const sH = Math.max(16, sBot - sTop)

    // Determine Y bounds for sub-pane
    let subMin = comp.meta.fixedRange ? comp.meta.fixedRange[0] : Infinity
    let subMax = comp.meta.fixedRange ? comp.meta.fixedRange[1] : -Infinity

    if (!comp.meta.fixedRange) {
      if (comp.meta.id === "VOL") subMin = 0
      if (comp.subCandles) {
        for (let i = iStart; i <= iEnd; i++) {
          const sc = comp.subCandles[i]
          if (!sc) continue
          if (sc.low != null && Number.isFinite(sc.low) && sc.low < subMin) subMin = sc.low
          if (sc.high != null && Number.isFinite(sc.high) && sc.high > subMax) subMax = sc.high
        }
        if (comp.meta.referenceLevels) {
          for (const ref of comp.meta.referenceLevels) {
            if (Number.isFinite(ref.value)) {
              if (ref.value < subMin) subMin = ref.value
              if (ref.value > subMax) subMax = ref.value
            }
          }
        }
      }
      for (const plot of comp.plots) {
        for (let i = iStart; i <= iEnd; i++) {
          const v = plot.values[i]
          if (v != null && Number.isFinite(v)) {
            if (v < subMin) subMin = v
            if (v > subMax) subMax = v
          }
        }
      }
      if (!Number.isFinite(subMin) || !Number.isFinite(subMax) || subMax <= subMin) {
        subMin = 0
        subMax = 100
      } else if (comp.meta.id !== "VOL") {
        const pad = (subMax - subMin) * (comp.subCandles ? 0.16 : 0.1)
        subMin -= pad
        subMax += pad
      } else {
        subMax *= 1.12
      }
    }

    const valToSubY = (v: number): number => {
      const ratio = (v - subMin) / Math.max(1e-6, subMax - subMin)
      return sBot - ratio * sH
    }

    ctx.save()
    ctx.beginPath()
    ctx.rect(plotLeft, pane.top, plotWidth, pane.height)
    ctx.clip()

    // Sub-pane vertical background regime highlights (e.g. HuD DE | TE Golden/Death Cross)
    if (comp.subBgHighlights && comp.subBgHighlights.length > 0) {
      const colStep = Math.max(
        3,
        Math.abs(coordConverter.barIndexToX(1) - coordConverter.barIndexToX(0))
      )
      for (const bg of comp.subBgHighlights) {
        if (bg.barIndex < iStart || bg.barIndex > iEnd) continue
        const cx = coordConverter.barIndexToX(bg.barIndex)
        ctx.fillStyle = bg.color
        ctx.fillRect(cx - colStep / 2, pane.top + 1, colStep, pane.height - 2)
      }
    }

    // Reference bands (e.g. RSI 30..70 purple fill, Stoch 20..80 fill)
    if (comp.meta.referenceLevels && comp.meta.referenceLevels.length >= 2) {
      const topLv = comp.meta.referenceLevels[0].value
      const botLv = comp.meta.referenceLevels[comp.meta.referenceLevels.length - 1].value
      const yTopLv = valToSubY(topLv)
      const yBotLv = valToSubY(botLv)
      ctx.fillStyle = "rgba(126, 87, 194, 0.07)"
      ctx.fillRect(plotLeft, Math.min(yTopLv, yBotLv), plotWidth, Math.abs(yBotLv - yTopLv))
    }

    // Reference horizontal lines
    if (comp.meta.referenceLevels) {
      for (const ref of comp.meta.referenceLevels) {
        const ry = Math.round(valToSubY(ref.value)) + 0.5
        ctx.save()
        ctx.strokeStyle = ref.color
        ctx.lineWidth = 1
        if (ref.dashed) ctx.setLineDash([4, 4])
        ctx.beginPath()
        ctx.moveTo(plotLeft, ry)
        ctx.lineTo(plotRight, ry)
        ctx.stroke()
        ctx.restore()
      }
    }

    // Sub-pane plotcandle OHLC rendering (e.g. HuD DE | TE [Hud_Hud])
    if (comp.subCandles && comp.subCandles.length > 0) {
      const subCandleW = Math.max(2, Math.min(18, barWidth * 0.82))
      for (let i = iStart; i <= iEnd; i++) {
        const sc = comp.subCandles[i]
        if (!sc || sc.open == null || sc.close == null || sc.high == null || sc.low == null) {
          continue
        }
        const cx = Math.round(coordConverter.barIndexToX(i)) + 0.5
        const yHigh = valToSubY(sc.high)
        const yLow = valToSubY(sc.low)
        const yOpen = valToSubY(sc.open)
        const yClose = valToSubY(sc.close)
        const topBody = Math.min(yOpen, yClose)
        const bodyH = Math.max(1.5, Math.abs(yClose - yOpen))

        ctx.strokeStyle = sc.wickColor || sc.color
        ctx.lineWidth = 1
        ctx.beginPath()
        ctx.moveTo(cx, yHigh)
        ctx.lineTo(cx, yLow)
        ctx.stroke()

        const bx = Math.round(cx - subCandleW / 2)
        ctx.fillStyle = sc.color
        ctx.fillRect(bx, topBody, subCandleW, bodyH)
        if (sc.borderColor && subCandleW >= 3.5) {
          ctx.strokeStyle = sc.borderColor
          ctx.lineWidth = 0.8
          ctx.strokeRect(bx + 0.5, topBody + 0.5, Math.max(1, subCandleW - 1), Math.max(1, bodyH - 1))
        }
      }
    }

    // Plot series (histograms, columns, areas, dots, circles_line, steps & lines)
    const zeroY = Math.max(sTop, Math.min(sBot, valToSubY(0)))
    for (const plot of comp.plots) {
      if (plot.type === "histogram" || plot.type === "columns") {
        const useBottomBase =
          comp.meta.id === "VOL" ||
          comp.meta.id === "VOL_24H" ||
          comp.meta.id === "RVOL_TIME" ||
          subMin > 0
        for (let i = iStart; i <= iEnd; i++) {
          const v = plot.values[i]
          if (v == null) continue
          const colW = plot.type === "columns" ? Math.max(2, barWidth * 0.85) : barWidth
          const x = coordConverter.barIndexToX(i) - colW / 2
          const y = valToSubY(v)
          const baseY = useBottomBase ? sBot : zeroY
          ctx.fillStyle = plot.pointColors?.[i] || plot.color
          ctx.fillRect(x, Math.min(y, baseY), colW, Math.max(1, Math.abs(baseY - y)))
        }
      } else if (plot.type === "dots") {
        for (let i = iStart; i <= iEnd; i++) {
          const v = plot.values[i]
          if (v == null) continue
          const x = coordConverter.barIndexToX(i)
          const y = valToSubY(v)
          ctx.fillStyle = plot.pointColors?.[i] || plot.color
          ctx.beginPath()
          ctx.arc(x, y, 2.5, 0, Math.PI * 2)
          ctx.fill()
        }
      } else if (plot.type === "circles_line" || (plot.type === "line" && plot.pointColors)) {
        ctx.save()
        ctx.lineWidth = plot.lineWidth ?? 1.3
        if (plot.dashed) ctx.setLineDash([4, 4])
        const dotR = Math.min(2.1, Math.max(1.2, barWidth * 0.22))
        for (let i = iStart; i <= iEnd; i++) {
          const v = plot.values[i]
          if (v == null) continue
          const x = coordConverter.barIndexToX(i)
          const y = valToSubY(v)
          const segCol = plot.pointColors?.[i] || plot.color
          if (i > iStart && plot.values[i - 1] != null) {
            const px = coordConverter.barIndexToX(i - 1)
            const py = valToSubY(plot.values[i - 1]!)
            ctx.strokeStyle = segCol
            ctx.beginPath()
            ctx.moveTo(px, py)
            ctx.lineTo(x, y)
            ctx.stroke()
          }
          if (plot.type === "circles_line") {
            ctx.fillStyle = segCol
            ctx.beginPath()
            ctx.arc(x, y, dotR, 0, Math.PI * 2)
            ctx.fill()
          }
        }
        ctx.restore()
      } else {
        if (plot.type === "area") {
          ctx.save()
          ctx.beginPath()
          let startedArea = false
          let firstX = 0
          let lastX = 0
          for (let i = iStart; i <= iEnd; i++) {
            const v = plot.values[i]
            if (v == null) continue
            const x = coordConverter.barIndexToX(i)
            const y = valToSubY(v)
            if (!startedArea) {
              firstX = x
              ctx.moveTo(x, y)
              startedArea = true
            } else {
              ctx.lineTo(x, y)
            }
            lastX = x
          }
          if (startedArea) {
            ctx.lineTo(lastX, sBot)
            ctx.lineTo(firstX, sBot)
            ctx.closePath()
            const grad = ctx.createLinearGradient(0, sTop, 0, sBot)
            grad.addColorStop(0, "rgba(41, 98, 255, 0.24)")
            grad.addColorStop(1, "rgba(41, 98, 255, 0.02)")
            ctx.fillStyle = grad
            ctx.fill()
          }
          ctx.restore()
        }

        ctx.save()
        ctx.strokeStyle = plot.color
        ctx.lineWidth = plot.lineWidth ?? 1.5
        if (plot.dashed) ctx.setLineDash([4, 4])
        ctx.beginPath()
        let started = false
        for (let i = iStart; i <= iEnd; i++) {
          const v = plot.values[i]
          if (v == null) {
            started = false
            continue
          }
          const x = coordConverter.barIndexToX(i)
          const y = valToSubY(v)
          if (!started) {
            ctx.moveTo(x, y)
            started = true
          } else if (plot.type === "step" && i > iStart && plot.values[i - 1] != null) {
            const prevY = valToSubY(plot.values[i - 1]!)
            ctx.lineTo(x, prevY)
            ctx.lineTo(x, y)
          } else {
            ctx.lineTo(x, y)
          }
        }
        ctx.stroke()
        ctx.restore()
      }
    }

    // Sub-pane signal markers (▲/▼ triangles, ◆ diamonds, and Bull/Bear Div badges)
    if (comp.markers && comp.markers.length > 0 && (comp.subCandles || comp.plots[0])) {
      const stackAbove = new Map<number, number>()
      const stackBelow = new Map<number, number>()
      for (const mk of comp.markers) {
        if (mk.barIndex < iStart || mk.barIndex > iEnd) continue
        const sc = comp.subCandles?.[mk.barIndex]
        const anchorVal =
          sc && sc.high != null && sc.low != null
            ? mk.position === "aboveBar"
              ? sc.high
              : sc.low
            : comp.plots[0]?.values[mk.barIndex] ?? null
        if (anchorVal == null) continue

        const cx = coordConverter.barIndexToX(mk.barIndex)
        const py = valToSubY(anchorVal)
        const slot =
          mk.position === "aboveBar"
            ? stackAbove.get(mk.barIndex) ?? 0
            : stackBelow.get(mk.barIndex) ?? 0
        if (mk.position === "aboveBar") stackAbove.set(mk.barIndex, slot + 1)
        else stackBelow.set(mk.barIndex, slot + 1)
        const slotOffset = slot * 11

        ctx.save()
        if (mk.shape === "arrowUp") {
          const tipY = Math.min(sBot - 8, py + 4 + slotOffset)
          ctx.fillStyle = mk.color
          ctx.beginPath()
          ctx.moveTo(cx, tipY)
          ctx.lineTo(cx - 4.5, tipY + 7)
          ctx.lineTo(cx + 4.5, tipY + 7)
          ctx.closePath()
          ctx.fill()
        } else if (mk.shape === "arrowDown") {
          const tipY = Math.max(sTop + 8, py - 4 - slotOffset)
          ctx.fillStyle = mk.color
          ctx.beginPath()
          ctx.moveTo(cx, tipY)
          ctx.lineTo(cx - 4.5, tipY - 7)
          ctx.lineTo(cx + 4.5, tipY - 7)
          ctx.closePath()
          ctx.fill()
        } else if (mk.shape === "diamond") {
          const dy =
            mk.position === "aboveBar"
              ? Math.max(sTop + 6, py - 7 - slotOffset)
              : Math.min(sBot - 6, py + 7 + slotOffset)
          ctx.fillStyle = mk.color
          ctx.beginPath()
          ctx.moveTo(cx, dy - 4.2)
          ctx.lineTo(cx + 4.2, dy)
          ctx.lineTo(cx, dy + 4.2)
          ctx.lineTo(cx - 4.2, dy)
          ctx.closePath()
          ctx.fill()
        } else {
          ctx.font = "700 8.5px 'JetBrains Mono', monospace"
          const w = Math.max(22, ctx.measureText(mk.text).width + 8)
          const by =
            mk.position === "aboveBar"
              ? Math.max(sTop, py - 17 - slotOffset)
              : Math.min(sBot - 13, py + 4 + slotOffset)
          ctx.fillStyle = mk.color
          ctx.beginPath()
          ctx.roundRect(cx - w / 2, by, w, 13, 3)
          ctx.fill()
          ctx.fillStyle = "#FFFFFF"
          ctx.textAlign = "center"
          ctx.textBaseline = "middle"
          ctx.fillText(mk.text, cx, by + 6.5)
        }
        ctx.restore()
      }
    }

    // Sub-pane right-side expert callout badge (e.g. [ DEVIATION XPERT ] / [ TREND XPERT ])
    if (comp.subPaneBadge) {
      const lastBarIdx = Math.min(iEnd, displayCandles.length - 1)
      const lastSc = comp.subCandles?.[lastBarIdx]
      const anchorVal =
        lastSc?.close ?? comp.plots[0]?.values[lastBarIdx] ?? (subMin + subMax) / 2
      const rawY = Number.isFinite(anchorVal) ? valToSubY(anchorVal) : (sTop + sBot) / 2
      const badgeH = 17
      const badgeY = Math.max(sTop + 2, Math.min(sBot - badgeH - 2, rawY - badgeH / 2))

      ctx.save()
      ctx.font = "700 9.5px 'JetBrains Mono', monospace"
      const badgeW = Math.ceil(ctx.measureText(comp.subPaneBadge.text).width) + 14
      const lastBarX = coordConverter.barIndexToX(lastBarIdx)
      const badgeX = Math.max(
        plotLeft + 8,
        Math.min(plotRight - badgeW - 6, lastBarX + Math.max(10, barWidth))
      )

      ctx.fillStyle = comp.subPaneBadge.color
      ctx.beginPath()
      ctx.roundRect(badgeX, badgeY, badgeW, badgeH, 3)
      ctx.fill()

      // Left pointer triangle toward latest candle
      ctx.beginPath()
      ctx.moveTo(badgeX - 4, badgeY + badgeH / 2)
      ctx.lineTo(badgeX + 1, badgeY + 4)
      ctx.lineTo(badgeX + 1, badgeY + badgeH - 4)
      ctx.closePath()
      ctx.fill()

      ctx.fillStyle = "#FFFFFF"
      ctx.textAlign = "center"
      ctx.textBaseline = "middle"
      ctx.fillText(comp.subPaneBadge.text, badgeX + badgeW / 2, badgeY + badgeH / 2 + 0.5)
      ctx.restore()
    }

    ctx.restore()

    // Sub-pane Right Axis Labels & Latest Value Pill (with 18px anti-collision)
    ctx.save()
    ctx.font = "500 10px 'JetBrains Mono', monospace"
    ctx.fillStyle = textMuted
    ctx.textAlign = "left"
    ctx.textBaseline = "middle"

    const lastBarIdxForAxis = Math.min(iEnd, displayCandles.length - 1)
    const lastSubCandle = comp.subCandles?.[lastBarIdxForAxis]
    const primaryPlot = comp.plots[comp.plots.length > 1 && comp.meta.id === "MACD" ? 1 : 0]
    const lastVal =
      lastSubCandle?.close != null ? lastSubCandle.close : primaryPlot?.values[lastBarIdxForAxis]
    const pillColor =
      lastSubCandle?.close != null
        ? lastSubCandle.color
        : primaryPlot?.pointColors?.[lastBarIdxForAxis] || primaryPlot?.color || "#2962FF"
    const subPillY =
      lastVal != null && Number.isFinite(lastVal)
        ? (() => {
            const ly = valToSubY(lastVal)
            return ly >= pane.top + 8 && ly <= pane.bottom - 8 ? ly : null
          })()
        : null

    if (comp.meta.referenceLevels && comp.meta.referenceLevels.length > 0) {
      for (const ref of comp.meta.referenceLevels) {
        const ry = valToSubY(ref.value)
        if (ry >= pane.top + 8 && ry <= pane.bottom - 8) {
          if (subPillY !== null && Math.abs(ry - subPillY) < 18) continue
          ctx.fillText(ref.label || String(ref.value), plotRight + 7, ry)
        }
      }
    } else {
      const topTickY = sTop + 6
      const midTickY = (sTop + sBot) / 2
      if (subPillY === null || Math.abs(topTickY - subPillY) >= 18) {
        ctx.fillText(fmtCompactAxisValue(subMax), plotRight + 7, topTickY)
      }
      if (subPillY === null || Math.abs(midTickY - subPillY) >= 18) {
        ctx.fillText(fmtCompactAxisValue((subMin + subMax) / 2), plotRight + 7, midTickY)
      }
    }

    if (subPillY !== null && lastVal != null) {
      drawAxisPill(ctx, plotRight, subPillY, PRICE_AXIS_WIDTH, fmtCompactAxisValue(lastVal), pillColor)
    }
    ctx.restore()
  }

  /* ==========================================================================
   * 4. AXES BORDERS & BAR REPLAY CUTOFF OVERLAY
   * ========================================================================== */
  ctx.strokeStyle = borderColor
  ctx.lineWidth = 1
  ctx.beginPath()
  ctx.moveTo(plotRight + 0.5, 0)
  ctx.lineTo(plotRight + 0.5, height)
  ctx.moveTo(0, plotBottom + 0.5)
  ctx.lineTo(width, plotBottom + 0.5)
  ctx.stroke()

  if (replay.selectingStartBar && mousePos.inside && mousePos.x < plotRight) {
    ctx.save()
    ctx.fillStyle = "rgba(41, 98, 255, 0.12)"
    ctx.fillRect(mousePos.x, plotTop, plotRight - mousePos.x, plotBottom - plotTop)
    ctx.strokeStyle = "#2962FF"
    ctx.lineWidth = 2
    ctx.beginPath()
    ctx.moveTo(mousePos.x, plotTop)
    ctx.lineTo(mousePos.x, plotBottom)
    ctx.stroke()
    drawAxisPill(ctx, Math.max(10, mousePos.x - 68), plotTop + 22, 136, "✂ Cắt Replay tại đây", "#2962FF")
    ctx.restore()
  }

  /* ==========================================================================
   * 5. INTERACTIVE CROSSHAIR, AXIS CALLOUT BADGES & QUICK ORDER (+) BUTTON
   * ========================================================================== */
  if (mousePos.inside && mousePos.x >= plotLeft && mousePos.x <= plotRight && mousePos.y >= plotTop && mousePos.y <= plotBottom) {
    const hoveredIdx = Math.max(0, Math.min(displayCandles.length - 1, Math.round(coordConverter.xToBarIndex(mousePos.x))))
    const snappedX = Math.round(coordConverter.barIndexToX(hoveredIdx)) + 0.5
    const cy = Math.round(mousePos.y) + 0.5

    ctx.save()
    ctx.strokeStyle = isDark ? "rgba(209, 212, 220, 0.45)" : "rgba(24, 32, 29, 0.42)"
    ctx.lineWidth = 1
    ctx.setLineDash([4, 4])
    ctx.beginPath()
    ctx.moveTo(snappedX, plotTop)
    ctx.lineTo(snappedX, plotBottom)
    ctx.moveTo(plotLeft, cy)
    ctx.lineTo(plotRight, cy)
    ctx.stroke()
    ctx.restore()

    // Bottom X-axis Date Callout Badge
    const hoveredCandle = displayCandles[hoveredIdx]
    if (hoveredCandle && settings.showCrosshairLabels) {
      const dateStr = hoveredCandle.date.slice(0, 16).replace("T", " ")
      ctx.save()
      ctx.font = "600 10.5px 'JetBrains Mono', monospace"
      const w = Math.max(66, ctx.measureText(dateStr).width + 16)
      const bx = Math.max(4, Math.min(plotRight - w - 4, snappedX - w / 2))
      ctx.fillStyle = textInk
      ctx.beginPath()
      ctx.roundRect(bx, plotBottom + 3, w, TIME_AXIS_HEIGHT - 6, 3.5)
      ctx.fill()
      ctx.fillStyle = bgColor
      ctx.textAlign = "center"
      ctx.textBaseline = "middle"
      ctx.fillText(dateStr, bx + w / 2, plotBottom + TIME_AXIS_HEIGHT / 2)
      ctx.restore()
    }

    // Right Y-axis Price Callout Badge + Quick (+) Button when inside Main Pane
    if (mousePos.y >= mainPane.top && mousePos.y <= mainPane.bottom && settings.showCrosshairLabels) {
      const hoverPrice = coordConverter.yToPrice(mousePos.y)
      const label =
        viewport.scaleMode === "percentage"
          ? `${(((hoverPrice - basePrice) / Math.max(1, basePrice)) * 100).toFixed(2)}%`
          : fmtPriceVnd(hoverPrice)
      drawAxisPill(ctx, plotRight, cy, PRICE_AXIS_WIDTH, label, textInk, bgColor)

      // TradingView "+" button on right border to quick-pick price
      ctx.save()
      ctx.fillStyle = textInk
      ctx.beginPath()
      ctx.roundRect(plotRight - 20, cy - 9, 18, 18, 3.5)
      ctx.fill()
      ctx.fillStyle = bgColor
      ctx.font = "700 13px 'JetBrains Mono', monospace"
      ctx.textAlign = "center"
      ctx.textBaseline = "middle"
      ctx.fillText("+", plotRight - 11, cy + 0.5)
      ctx.restore()
    }
  }

  // 6. Box-Zoom Selection Overlay
  if (zoomBox) {
    const zx = Math.min(zoomBox.x1, zoomBox.x2)
    const zy = Math.min(zoomBox.y1, zoomBox.y2)
    const zw = Math.abs(zoomBox.x2 - zoomBox.x1)
    const zh = Math.abs(zoomBox.y2 - zoomBox.y1)
    if (zw > 4) {
      ctx.save()
      ctx.fillStyle = "rgba(41, 98, 255, 0.14)"
      ctx.fillRect(zx, plotTop, zw, plotBottom - plotTop)
      ctx.strokeStyle = "#2962FF"
      ctx.lineWidth = 1.2
      ctx.setLineDash([4, 3])
      ctx.strokeRect(zx + 0.5, zy + 0.5, zw, Math.max(20, zh))
      ctx.restore()
    }
  }

  return metrics
}
